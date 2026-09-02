#!/usr/bin/env python3
"""Fail-closed branch-root gate.

The protected ref is supplied explicitly or derived from origin/HEAD. The candidate revision is
supplied explicitly or defaults to HEAD. A merge-base exit status of 0 means rooted, 1 means
ORPHANED, and every other status is cannot-evaluate. First-parent lag is always reported and becomes
a blocking STALE verdict only when --max-lag is configured.

  check_branch_root.py [--root DIR] [--protected REF] [--head REV] [--max-lag N]
  check_branch_root.py --self-test

Exit: 0 rooted (and fresh when configured); 1 ORPHANED or STALE; 2 usage/cannot-evaluate.
Stdlib only; offline against the local object database.
"""
import argparse
import contextlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
from collections import namedtuple
from pathlib import Path

# Liftable and stdlib-only: the default root is resolved from the working directory via git
# rev-parse (_repo_root), with no dependency on any repository-local helper module.
TIMEOUT = 30
Result = namedtuple(
    "Result",
    ("code", "verdict", "protected_ref", "protected_sha", "head_ref", "head_sha",
     "bases", "lag", "max_lag", "detail"),
)


class CannotEvaluate(RuntimeError):
    """A required repository input or git result could not answer the question."""


def _git_env():
    """Remove ambient git redirection/config channels and reassert an offline, non-interactive posture;
    also neutralize replace refs so a grafted parent cannot mask an orphan as rooted through merge-base."""
    env = dict(os.environ)
    for key in [key for key in env if key.startswith("GIT_")]:
        env.pop(key, None)
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_NO_LAZY_FETCH"] = "1"
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_OPTIONAL_LOCKS"] = "0"
    # Replace refs (git replace --graft, refs/replace/*) are honoured by merge-base by default, so a
    # grafted parent could make an orphaned branch appear rooted. Disable them for every gate probe. The
    # on-disk .git/info/grafts residual is out of scope (an accidental-case guardrail, disclosed in the
    # gate residue): full adversarial-grade parent-rewrite resistance is not this gate's remit.
    env["GIT_NO_REPLACE_OBJECTS"] = "1"
    return env


def _git(root, *args, input_text=None):
    try:
        return subprocess.run(
            ["git", "-C", str(root), *args],
            input=input_text,
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
            env=_git_env(),
        )
    except (OSError, subprocess.SubprocessError, UnicodeDecodeError) as exc:
        # text=True decodes stdout/stderr as strict UTF-8; a non-UTF-8 git output (for example a
        # repository path or ref carrying non-UTF-8 bytes) raises UnicodeDecodeError. Route it to a
        # cannot-evaluate (exit 2), never let it escape as an uncaught decode crash to exit 1.
        raise CannotEvaluate("git invocation failed: {}".format(exc))


def _repo_root(requested):
    probe = _git(requested, "rev-parse", "--show-toplevel")
    if probe.returncode != 0:
        raise CannotEvaluate(
            "{} is not a readable git repository ({})".format(
                requested, probe.stderr.strip() or "git rev-parse failed"
            )
        )
    lines = probe.stdout.splitlines()
    if len(lines) != 1 or not lines[0]:
        raise CannotEvaluate("git rev-parse returned no unique repository root")
    try:
        resolved = Path(lines[0]).resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise CannotEvaluate("repository root cannot be resolved: {}".format(exc))
    if not resolved.is_dir():
        raise CannotEvaluate("repository root is not a directory: {}".format(resolved))
    return resolved


def _resolve_commit(root, revision, kind):
    probe = _git(
        root, "rev-parse", "--verify", "--quiet", "--end-of-options",
        "{}^{{commit}}".format(revision),
    )
    if probe.returncode != 0:
        if kind == "protected":
            raise CannotEvaluate(
                "protected ref {!r} cannot be resolved; derive it explicitly with --protected or repair "
                "origin/HEAD with: git remote set-head origin --auto".format(revision)
            )
        raise CannotEvaluate("candidate head {!r} cannot be resolved to a commit".format(revision))
    lines = [line for line in probe.stdout.splitlines() if line]
    if len(lines) != 1:
        raise CannotEvaluate("{} revision {!r} did not resolve to one commit".format(kind, revision))
    return lines[0]


def _count_lag(root, base, protected_sha):
    probe = _git(
        root, "rev-list", "--count", "--first-parent",
        "{}..{}".format(base, protected_sha),
    )
    if probe.returncode != 0:
        raise CannotEvaluate(
            "cannot measure first-parent lag from {} to {} ({})".format(
                base, protected_sha, probe.stderr.strip() or "git rev-list failed"
            )
        )
    value = probe.stdout.strip()
    if not value.isdigit():
        raise CannotEvaluate("git rev-list returned a malformed lag count {!r}".format(value))
    return int(value)


def _is_shallow(root):
    """True when the repository is a shallow clone, False when it is complete. A shallow clone truncates
    history, so a missing merge base there is unfetched-history, not an orphan; the caller routes exit 1 in
    a shallow repo to cannot-evaluate rather than ORPHANED. Fail-closed: a failed or unrecognized probe is
    itself a cannot-evaluate, never a silent 'not shallow'."""
    probe = _git(root, "rev-parse", "--is-shallow-repository")
    if probe.returncode != 0:
        raise CannotEvaluate(
            "cannot determine whether the repository is shallow ({})".format(
                probe.stderr.strip() or "git rev-parse --is-shallow-repository failed"
            )
        )
    value = probe.stdout.strip()
    if value not in ("true", "false"):
        raise CannotEvaluate(
            "git rev-parse --is-shallow-repository returned an unexpected value {!r}".format(value)
        )
    return value == "true"


def evaluate(root, protected=None, head=None, max_lag=None):
    # A malformed threshold is a cannot-evaluate (exit 2), never a silently-disabled or inverted guard
    # (guard-input-soundness / D11): a negative or non-integer --max-lag would make every lag "exceed" it.
    if max_lag is not None and (isinstance(max_lag, bool) or not isinstance(max_lag, int) or max_lag < 0):
        raise CannotEvaluate("--max-lag must be a non-negative integer, got {!r}".format(max_lag))
    root = _repo_root(root)
    protected_ref = protected if protected is not None else "origin/HEAD"
    head_ref = head if head is not None else "HEAD"
    protected_sha = _resolve_commit(root, protected_ref, "protected")
    head_sha = _resolve_commit(root, head_ref, "head")

    merge = _git(root, "merge-base", "--all", protected_sha, head_sha)
    if merge.returncode == 1:
        # A shallow clone truncates history, so a missing merge base can mean the shared root was simply
        # not fetched rather than that the branch is orphaned. In a shallow repo, exit 1 is cannot-evaluate
        # (exit 2), never a false ORPHANED deny; the CI gate checks out at fetch-depth:0 (full history).
        if _is_shallow(root):
            raise CannotEvaluate(
                "git merge-base found no common ancestor, but the repository is shallow, so an orphaned "
                "start cannot be distinguished from unfetched history; run `git fetch --unshallow` (the CI "
                "branch-root gate runs on a fetch-depth:0 checkout) and retry"
            )
        return Result(
            1, "ORPHANED", protected_ref, protected_sha, head_ref, head_sha,
            (), None, max_lag, "git merge-base reported no common ancestor",
        )
    if merge.returncode != 0:
        raise CannotEvaluate(
            "git merge-base returned unexpected status {} ({})".format(
                merge.returncode, merge.stderr.strip() or "no diagnostic"
            )
        )
    bases = tuple(line for line in merge.stdout.splitlines() if line)
    if not bases:
        raise CannotEvaluate("git merge-base exited 0 but returned no merge base")

    lag = min(_count_lag(root, base, protected_sha) for base in bases)
    if max_lag is not None and lag > max_lag:
        return Result(
            1, "STALE", protected_ref, protected_sha, head_ref, head_sha,
            bases, lag, max_lag, "first-parent lag exceeds the configured threshold",
        )
    return Result(
        0, "PASS", protected_ref, protected_sha, head_ref, head_sha,
        bases, lag, max_lag, "ancestry resolves to the protected line",
    )


def _print_result(result):
    if result.verdict == "ORPHANED":
        print(
            "VIOLATION: branch-root: no merge base between {} and {}: this branch is rooted on a\n"
            "retired history (a rewrite or re-initialization removed its root from the live line). "
            "Re-home before any\ndispatch or merge:\n"
            "  git fetch origin\n"
            "  git rebase --onto {} <old-base> <your-branch>   (replay unique commits), or recut:\n"
            "  git checkout -B <your-branch> {}\n"
            "This gate detects and blocks; it never re-homes a branch itself. Re-homing is a "
            "state-changing action\nreserved to the operator.".format(
                result.head_sha,
                result.protected_ref,
                result.protected_ref,
                result.protected_ref,
            )
        )
        return
    bases = ", ".join(result.bases)
    threshold = (
        "report-only (no threshold configured)"
        if result.max_lag is None
        else "configured maximum {}".format(result.max_lag)
    )
    if result.verdict == "STALE":
        print(
            "VIOLATION: branch-root: {} is rooted at merge base(s) {}, but its nearest "
            "first-parent lag is {} commit(s), over {}. Re-home it onto {} before dispatch or merge; "
            "this gate never performs that state-changing action.".format(
                result.head_sha, bases, result.lag, result.max_lag, result.protected_ref
            )
        )
        return
    print(
        "PASS: branch-root: {} resolves to {} via merge base(s) {}; first-parent lag {} ({})".format(
            result.head_sha, result.protected_ref, bases, result.lag, threshold
        )
    )


def run(root, protected=None, head=None, max_lag=None):
    try:
        result = evaluate(root, protected=protected, head=head, max_lag=max_lag)
    except CannotEvaluate as exc:
        print("ERROR: branch-root cannot evaluate: {}".format(exc), file=sys.stderr)
        return 2
    _print_result(result)
    return result.code


def _fixture_git(root, *args, identity=False, input_text=None):
    cmd = ["git", "-C", str(root)]
    if identity:
        cmd += [
            "-c", "user.name=Branch Root Self Test",
            "-c", "user.email=branch-root@example.invalid",
            "-c", "commit.gpgsign=false",
        ]
    cmd += list(args)
    return subprocess.run(
        cmd,
        input=input_text,
        capture_output=True,
        text=True,
        check=True,
        timeout=TIMEOUT,
        env=_git_env(),
    ).stdout.strip()


def _fixture_repo(parent, name):
    root = parent / name
    root.mkdir()
    _fixture_git(root, "init", "-q", "-b", "protected")
    (root / "seed.txt").write_text("seed\n", encoding="utf-8")
    _fixture_git(root, "add", "seed.txt")
    _fixture_git(root, "commit", "-q", "-m", "seed", identity=True)
    return root


def _fixture_commit(root, name):
    path = root / "{}.txt".format(name)
    path.write_text(name + "\n", encoding="utf-8")
    _fixture_git(root, "add", path.name)
    _fixture_git(root, "commit", "-q", "-m", name, identity=True)
    return _fixture_git(root, "rev-parse", "HEAD")


def _commit_tree(root, tree, parents, message):
    args = ["commit-tree", tree]
    for parent in parents:
        args += ["-p", parent]
    return _fixture_git(root, *args, identity=True, input_text=message + "\n")


def self_test():
    try:
        temp = Path(tempfile.mkdtemp(prefix="aiqt-branchroot-selftest-"))
    except OSError as exc:
        print("SELF-TEST ERROR: cannot create fixture root: {}".format(exc), file=sys.stderr)
        return 2
    failures = []
    skipped = []
    old_home = os.environ.get("HOME")
    old_xdg = os.environ.get("XDG_CONFIG_HOME")
    fixture_home = temp / "home"
    fixture_home.mkdir()
    os.environ["HOME"] = str(fixture_home)
    # Isolate the XDG git-config channel too (a user XDG_CONFIG_HOME could carry core.hooksPath or a
    # clean/process filter that perturbs fixture commits), so the self-test is hermetic, not only HOME-clean.
    os.environ["XDG_CONFIG_HOME"] = str(fixture_home / "xdg")

    def case(label, expected, root, protected=None, head=None, max_lag=None):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            got = run(root, protected=protected, head=head, max_lag=max_lag)
        if got != expected:
            failures.append("{}: expected exit {}, got {}".format(label, expected, got))

    try:
        # V1: branch cut at protected tip, then one feature commit. Base is the tip and lag is zero.
        v1 = _fixture_repo(temp, "v1-rooted")
        tip = _fixture_git(v1, "rev-parse", "HEAD")
        _fixture_git(v1, "branch", "feature", tip)
        _fixture_git(v1, "switch", "-q", "feature")
        _fixture_commit(v1, "feature-one")
        case("V1 rooted-tip", 0, v1, "refs/heads/protected", "refs/heads/feature")
        v1_result = evaluate(v1, "refs/heads/protected", "refs/heads/feature")
        if v1_result.lag != 0 or len(v1_result.bases) != 1:
            failures.append("V1: expected one base and lag 0")

        # V2: a commit with no parent is an unrelated retired root.
        v2 = _fixture_repo(temp, "v2-orphan")
        tree = _fixture_git(v2, "rev-parse", "HEAD^{tree}")
        orphan = _commit_tree(v2, tree, (), "unrelated root")
        _fixture_git(v2, "update-ref", "refs/heads/orphan", orphan)
        case("V2 orphaned", 1, v2, "refs/heads/protected", "refs/heads/orphan")

        # V3: a surviving fork point whose first-parent lag exceeds N.
        v3 = _fixture_repo(temp, "v3-stale")
        fork = _fixture_git(v3, "rev-parse", "HEAD")
        _fixture_git(v3, "branch", "feature", fork)
        for i in range(4):
            _fixture_commit(v3, "protected-{}".format(i))
        case("V3 stale configured", 1, v3, "refs/heads/protected", "refs/heads/feature", 2)
        case("V3 report-only", 0, v3, "refs/heads/protected", "refs/heads/feature")

        # V4: the old fork was rewritten away but an ancient shared root survives.
        v4 = _fixture_repo(temp, "v4-partial-rewrite")
        _fixture_git(v4, "switch", "-q", "-c", "old-line")
        _fixture_commit(v4, "old-fork")
        _fixture_git(v4, "switch", "-q", "-c", "feature")
        _fixture_commit(v4, "feature")
        _fixture_git(v4, "switch", "-q", "protected")
        for i in range(4):
            _fixture_commit(v4, "rewritten-{}".format(i))
        case("V4 hard invariant", 0, v4, "refs/heads/protected", "refs/heads/feature")
        case("V4 corridor", 1, v4, "refs/heads/protected", "refs/heads/feature", 1)

        # V5: default derivation cannot find origin/HEAD.
        v5 = _fixture_repo(temp, "v5-no-origin-head")
        case("V5 protected underivable", 2, v5)

        # V6: the candidate revision does not exist.
        case("V6 missing head", 2, v1, "refs/heads/protected", "refs/heads/no-such-head")

        # V7: malformed and negative controls are usage/cannot-evaluate, never disabled thresholds.
        case("V7 negative threshold", 2, v1, "refs/heads/protected", "refs/heads/feature", -1)
        # The non-integer spelling is exercised through main(), where control parsing owns the verdict.
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            got = main([
                "--root", str(v1), "--protected", "refs/heads/protected",
                "--head", "refs/heads/feature", "--max-lag", "not-an-integer",
            ])
        if got != 2:
            failures.append("V7 malformed threshold: expected exit 2, got {}".format(got))

        # V8: two reciprocal merge commits have two best common ancestors.
        v8 = _fixture_repo(temp, "v8-criss-cross")
        root_sha = _fixture_git(v8, "rev-parse", "HEAD")
        tree = _fixture_git(v8, "rev-parse", "HEAD^{tree}")
        a1 = _commit_tree(v8, tree, (root_sha,), "a1")
        b1 = _commit_tree(v8, tree, (root_sha,), "b1")
        m1 = _commit_tree(v8, tree, (a1, b1), "merge one")
        m2 = _commit_tree(v8, tree, (b1, a1), "merge two")
        _fixture_git(v8, "update-ref", "refs/heads/protected", m1)
        _fixture_git(v8, "update-ref", "refs/heads/feature", m2)
        case("V8 criss-cross", 0, v8, "refs/heads/protected", "refs/heads/feature")
        v8_result = evaluate(v8, "refs/heads/protected", "refs/heads/feature")
        if len(v8_result.bases) != 2 or v8_result.lag != 1:
            failures.append(
                "V8: expected two bases and nearest first-parent lag 1, got bases={} lag={}".format(
                    len(v8_result.bases), v8_result.lag
                )
            )

        # V9: a directory with no repository is cannot-evaluate.
        v9 = temp / "v9-not-a-repo"
        v9.mkdir()
        case("V9 non-repository", 2, v9, "refs/heads/protected", "HEAD")

        # V10: non-UTF-8 git output (a repository whose path carries a non-UTF-8 byte, so
        # `git rev-parse --show-toplevel` emits bytes strict UTF-8 cannot decode) is a cannot-evaluate
        # (exit 2), never an uncaught decode crash to exit 1.
        try:
            v10_bytes = os.fsencode(str(temp)) + b"/v10-non-utf8-\xff-repo"
            os.mkdir(v10_bytes)
        except (OSError, UnicodeError):
            v10_bytes = None
            skipped.append(
                "V10 non-UTF-8-git-output (this filesystem rejects a non-UTF-8 directory name, so the "
                "fixture cannot be built here; it runs on a byte-transparent filesystem such as the "
                "Linux CI runner)"
            )
        if v10_bytes is not None:
            v10 = Path(os.fsdecode(v10_bytes))
            _fixture_git(v10, "init", "-q", "-b", "protected")
            case("V10 non-utf8 git output is cannot-evaluate", 2, v10,
                 "refs/heads/protected", "HEAD")

        # V11: a shallow clone cannot tell a missing merge base from unfetched history, so a merge-base
        # exit 1 in a shallow repository is cannot-evaluate (exit 2), never a false ORPHANED deny. A
        # file:// clone with --depth is genuinely shallow (a plain local-path clone ignores --depth). The
        # case is skipped with a printed note (never a false pass) where a shallow file:// clone cannot be
        # built here; it runs on the CI runner.
        v11_src = _fixture_repo(temp, "v11-shallow-src")
        _fixture_commit(v11_src, "c2")  # depth >= 2 so a depth-1 clone actually truncates
        v11 = temp / "v11-shallow-clone"
        try:
            _fixture_git(temp, "clone", "--depth", "1", "-q",
                         "file://{}".format(v11_src), str(v11), "-b", "protected")
            shallow_ready = _is_shallow(v11)
        except (OSError, subprocess.SubprocessError, CannotEvaluate):
            shallow_ready = False
        if not shallow_ready:
            skipped.append(
                "V11 shallow-clone (a shallow file:// clone could not be built here; the shallow "
                "branch-root path runs where file:// clones are constructible, such as the CI runner)"
            )
        else:
            v11_tree = _fixture_git(v11, "rev-parse", "HEAD^{tree}")
            v11_orphan = _commit_tree(v11, v11_tree, (), "orphan in shallow")
            _fixture_git(v11, "update-ref", "refs/heads/orphan", v11_orphan)
            case("V11 shallow orphan is cannot-evaluate", 2, v11,
                 "refs/heads/protected", "refs/heads/orphan")
    except (OSError, subprocess.SubprocessError, CannotEvaluate) as exc:
        print("SELF-TEST ERROR: fixture setup/evaluation failed: {}".format(exc), file=sys.stderr)
        return 2
    finally:
        if old_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = old_home
        if old_xdg is None:
            os.environ.pop("XDG_CONFIG_HOME", None)
        else:
            os.environ["XDG_CONFIG_HOME"] = old_xdg
        shutil.rmtree(temp, ignore_errors=True)

    # V12: a default-root acquisition failure (a deleted or unsearchable working directory) is a
    # cannot-evaluate (exit 2), never an uncaught exception that would exit 1 (reserved for ORPHANED/STALE).
    original_cwd = Path.cwd
    try:
        Path.cwd = staticmethod(
            lambda: (_ for _ in ()).throw(FileNotFoundError("working directory removed")))
        with contextlib.redirect_stderr(io.StringIO()):
            got = main(["--max-lag", "200"])
        if got != 2:
            failures.append("V12 unavailable cwd: expected exit 2, got {}".format(got))
    finally:
        Path.cwd = original_cwd

    # V13: the --max-lag CLI type rejects separators/whitespace/signs that int(value, 10) would accept.
    import argparse as _ap
    for _bad in ("1_000", " 5 ", "-5", "+5", "", "5.0"):
        try:
            _max_lag(_bad)
            failures.append("V13 malformed --max-lag {!r}: accepted, expected reject".format(_bad))
        except _ap.ArgumentTypeError:
            pass
    for _good, _want in (("0", 0), ("200", 200)):
        try:
            if _max_lag(_good) != _want:
                failures.append("V13 --max-lag {!r}: wrong parse".format(_good))
        except _ap.ArgumentTypeError:
            failures.append("V13 --max-lag {!r}: rejected a valid value".format(_good))

    if failures:
        print("SELF-TEST FAIL:")
        for failure in failures:
            print("  - " + failure)
        return 1
    covered = ("rooted, orphaned, configured/report-only stale, partial rewrite, underivable protected "
               "ref, missing head, malformed/negative threshold, two-base criss-cross, non-repository, and "
               "default-root/cwd-unavailable")
    if skipped:
        # A declared case whose fixture could not be built in THIS environment is disclosed explicitly and
        # is NOT counted as covered, so the pass can never falsely claim coverage it did not exercise.
        print("SELF-TEST PASS (PARTIAL): verified " + covered + "; verdicts are asserted by exit code. "
              "NOT VERIFIED HERE (declared cases whose fixtures could not be built in this environment): "
              + "; ".join(skipped))
    else:
        print("SELF-TEST PASS: V1-V13 cover " + covered + ", non-UTF-8-git-output, and shallow-clone "
              "(both cannot-evaluate) outcomes; verdicts are asserted by exit code")
    return 0


def _max_lag(value):
    # str.isdigit() accepts ONLY a pure run of digits: it rejects a sign, surrounding whitespace, and the
    # underscore or other digit separators that int(value, 10) would silently accept, so a malformed
    # threshold is a usage error rather than a mis-parsed value. isascii() additionally rejects non-ASCII
    # digit characters (for example superscripts or other-script digits) that isdigit() would allow.
    if not (isinstance(value, str) and value.isascii() and value.isdigit()):
        raise argparse.ArgumentTypeError("must be a non-negative integer")
    return int(value, 10)


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="check_branch_root.py",
        description="Verify that a candidate revision remains rooted on the derived protected line.",
    )
    parser.add_argument("--root", type=Path)
    parser.add_argument("--protected")
    parser.add_argument("--head")
    parser.add_argument("--max-lag", type=_max_lag)
    parser.add_argument("--self-test", action="store_true")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code)
    if args.self_test:
        if any(value is not None for value in (args.root, args.protected, args.head, args.max_lag)):
            parser.print_usage(sys.stderr)
            return 2
        return self_test()
    if args.root is not None:
        root = args.root
    else:
        try:
            root = str(Path.cwd())
        except OSError as exc:
            # A deleted or unsearchable working directory is a cannot-evaluate (exit 2), never an uncaught
            # exception to exit 1 (which the contract reserves for ORPHANED/STALE).
            print("branch-root: the working directory is unavailable ({}); cannot evaluate".format(exc),
                  file=sys.stderr)
            return 2
    return run(root, protected=args.protected, head=args.head, max_lag=args.max_lag)


if __name__ == "__main__":
    sys.exit(main())
