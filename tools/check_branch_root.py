#!/usr/bin/env python3
"""Fail-closed branch-root gate.

The protected ref is supplied explicitly or derived from origin/HEAD. The candidate revision is
supplied explicitly or defaults to HEAD. A merge-base exit status of 0 means rooted, 1 means
ORPHANED, and every other status is cannot-evaluate. A rooted candidate that restores, at a path it
changes from any merge base, the exact blob a first-parent commit of the protected line replaced
within the REVERT_WINDOW commits ending at that merge base is REVERTS, unless its image there equals
the protected tip's or a commit in base..head carries a "Reverts: <full sha>" trailer naming every
such commit. First-parent lag is always reported
and becomes a blocking STALE verdict only when --max-lag is configured.

  check_branch_root.py [--root DIR] [--protected REF] [--head REV] [--max-lag N]
  check_branch_root.py --self-test

Exit: 0 rooted (and fresh when configured); 1 ORPHANED, REVERTS or STALE; 2 usage/cannot-evaluate.
Stdlib only; offline against the local object database.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: check_branch_root.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import argparse
import contextlib
import io
import os
import shutil
import subprocess
import tempfile
from collections import namedtuple
from pathlib import Path

# Liftable and stdlib-only: the default root is resolved from the working directory via git
# rev-parse (_repo_root), with no dependency on any repository-local helper module.
TIMEOUT = 30
# The revert check looks back this many first-parent commits, the merge base included. It matches the
# --max-lag 200 the CI legs pin, so a branch the staleness layer admits has its whole lag in view.
REVERT_WINDOW = 200
Result = namedtuple(
    "Result",
    ("code", "verdict", "protected_ref", "protected_sha", "head_ref", "head_sha",
     "bases", "lag", "max_lag", "detail", "findings"),
    defaults=((),),
)
# The window walk. --diff-merges=first-parent is named even though a current git (2.53 checked) implies
# it from --first-parent, so a git that does not still diffs a merge on the protected line against its
# first parent instead of printing no raw diff for it.
_WINDOW_LOG_ARGS = ("log", "--first-parent", "--diff-merges=first-parent", "--raw", "--no-renames",
                    "--no-abbrev", "--no-color", "--no-show-signature", "--format=%H")


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


def _git(root, *args, input_text=None, binary=False):
    try:
        return subprocess.run(
            ["git", "-C", str(root), *args],
            input=input_text,
            capture_output=True,
            text=not binary,
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


def _object_id(token):
    """True for a full lowercase hex object id (SHA-1 or SHA-256), checked on the raw bytes so a
    non-ASCII byte is refused here rather than raising on decode."""
    return len(token) in (40, 64) and all(char in b"0123456789abcdef" for char in token)


def _image(mode, oid, command):
    """One side of a raw record: None where the path is absent, else the (mode, oid) pair. A mode that is
    not six octal digits or an oid that is not a full hex id is a malformed record (cannot-evaluate)."""
    if len(mode) != 6 or not all(char in b"01234567" for char in mode) or not _object_id(oid):
        raise CannotEvaluate("git {} printed a malformed raw record".format(command))
    return None if mode == b"000000" else (mode.decode("ascii"), oid.decode("ascii"))


def _blob(image):
    """The blob id an image holds, or None where the path is absent. A revert is judged on this alone,
    so a mode change cannot hide a restored pre-image."""
    return None if image is None else image[1]


def _raw_records(root, *args, commits=None):
    """Run a git raw-diff command with -z and return its (commit, path, pre, post) records. Paths stay
    bytes, so a space, newline or non-UTF-8 byte in a name is compared exactly. An image is a
    (mode, oid) pair, or None where the path is absent on that side. commit is the preceding %H line
    for log output and None for diff-tree. When commits is a list, every %H line is appended to it,
    including those of commits that changed nothing."""
    probe = _git(root, args[0], "-z", *args[1:], binary=True)
    if probe.returncode != 0:
        raise CannotEvaluate("git {} failed ({})".format(
            args[0], probe.stderr.decode("utf-8", "replace").strip() or "no diagnostic"))
    records = []
    commit = None
    tokens = iter(probe.stdout.split(b"\0"))
    for token in tokens:
        header = token.lstrip(b"\n")
        if not header:
            continue
        if header.startswith(b":"):
            fields = header[1:].split(b" ")
            path = next(tokens, None)
            if len(fields) != 5 or not path:
                raise CannotEvaluate("git {} printed a malformed raw record".format(args[0]))
            records.append((commit, path, _image(fields[0], fields[2], args[0]),
                            _image(fields[1], fields[3], args[0])))
        elif _object_id(header):
            commit = header.decode("ascii")
            if commits is not None:
                commits.append(commit)
        else:
            raise CannotEvaluate("git {} printed an unrecognized line".format(args[0]))
    return records


def _window_listed(commits, base):
    """A correct git lists the window newest first starting at base itself, so an empty listing, or one
    that does not start at base, cannot be judged."""
    if not commits or commits[0] != base:
        raise CannotEvaluate("git log listed {} for the revert window below {}, not that base first"
                             .format("no commit" if not commits else commits[0], base))


def _window_touches(root, base, window):
    """(commit, path, pre, post) for every path each of the window first-parent commits ending at base
    changed against its first parent, newest first. A shallow repository is judged only when the parent
    of the oldest window commit is present: a shallow boundary commit diffs as if it added every file.
    A listing without base as its first commit is cannot-evaluate."""
    if _is_shallow(root):
        commits = _git(root, "rev-list", "--count", "--first-parent", "-n", str(window + 1), base)
        if commits.returncode != 0 or not commits.stdout.strip().isdigit():
            raise CannotEvaluate("cannot count the revert window in a shallow repository")
        if int(commits.stdout.strip()) <= window:
            raise CannotEvaluate(
                "the repository is shallow and its history ends inside the {}-commit revert window "
                "below {}; run `git fetch --unshallow` and retry".format(window, base))
    commits = []
    records = _raw_records(root, *_WINDOW_LOG_ARGS, "-n", str(window), base, "--", commits=commits)
    _window_listed(commits, base)
    return records


def _reverts_named(root, base, head_sha):
    """The values of every Reverts trailer on the commits in base..head."""
    probe = _git(root, "log", "--no-show-signature", "--no-color",
                 "--format=%(trailers:key=Reverts,valueonly,unfold)", "-z",
                 "{}..{}".format(base, head_sha), "--")
    if probe.returncode != 0:
        raise CannotEvaluate("cannot read Reverts trailers in {}..{} ({})".format(
            base, head_sha, probe.stderr.strip() or "git log failed"))
    return {value.strip() for value in probe.stdout.replace("\0", "\n").splitlines() if value.strip()}


def _allowed(undone, named):
    """A deliberate restoration passes only when a trailer names each undone commit by its full id."""
    return all(commit in named for commit in undone)


def _revert_findings(root, base, head_sha, window):
    """(path, undone) for each path the head changes from base whose head blob equals the pre-image blob
    of a commit in the window (file modes are not compared); undone lists the window commits on that path
    from base back to that commit, newest first. A deletion matches an add (an absent pre-image). Paths
    whose undone commits all carry a Reverts trailer are dropped."""
    changed = {path: post for _c, path, _pre, post in _raw_records(
        root, "diff-tree", "-r", "--raw", "--no-renames", "--no-abbrev", base, head_sha, "--")}
    if not changed:
        return []
    touches = {}
    for commit, path, pre, _post in _window_touches(root, base, window):
        if path in changed:
            touches.setdefault(path, []).append((commit, pre))
    findings = []
    for path in sorted(touches):
        undone = []
        for commit, pre in touches[path]:
            undone.append(commit)
            if _blob(pre) == _blob(changed[path]):
                findings.append((path, tuple(undone)))
                break
    if findings:
        named = _reverts_named(root, base, head_sha)
        findings = [(path, undone) for path, undone in findings if not _allowed(undone, named)]
    return findings


def _union_findings(root, bases, _protected_sha, head_sha, window):
    """The revert findings judged from every merge base, deduplicated, and the bases that produced any.
    A revert can be visible from one base only, so no single base stands in for the others."""
    findings, anchors = [], []
    for base in bases:
        found = _revert_findings(root, base, head_sha, window)
        if found:
            anchors.append(base)
        findings.extend(finding for finding in found if finding not in findings)
    return sorted(findings), anchors


def _settled(root, protected_sha, head_sha, findings):
    """Drop the findings at paths where the head's image equals the protected tip's: merging the head
    leaves such a path exactly as the protected line has it, so it undoes nothing there (for example a
    stale branch that carries a revert the protected line already made)."""
    if not findings:
        return findings
    differs = {path for _c, path, _pre, _post in _raw_records(
        root, "diff-tree", "-r", "--raw", "--no-renames", "--no-abbrev", protected_sha, head_sha, "--")}
    return [(path, undone) for path, undone in findings if path in differs]


def evaluate(root, protected=None, head=None, max_lag=None, revert_window=None):
    # A malformed threshold is a cannot-evaluate (exit 2), never a silently-disabled or inverted guard
    # (guard-input-soundness / D11): a negative or non-integer --max-lag would make every lag "exceed" it.
    if max_lag is not None and (isinstance(max_lag, bool) or not isinstance(max_lag, int) or max_lag < 0):
        raise CannotEvaluate("--max-lag must be a non-negative integer, got {!r}".format(max_lag))
    if revert_window is None:
        revert_window = REVERT_WINDOW
    if isinstance(revert_window, bool) or not isinstance(revert_window, int) or revert_window < 1:
        raise CannotEvaluate("the revert window must be a positive integer, got {!r}".format(revert_window))
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

    # The reported lag is the least across bases; the revert check is judged from every base.
    lag = min(_count_lag(root, base, protected_sha) for base in bases)
    findings, anchors = _union_findings(root, bases, protected_sha, head_sha, revert_window)
    findings = _settled(root, protected_sha, head_sha, findings)
    if findings:
        return Result(
            1, "REVERTS", protected_ref, protected_sha, head_ref, head_sha,
            bases, lag, max_lag, "restores content the protected line replaced by merge base(s) {}".format(
                ", ".join(anchors)),
            tuple(findings),
        )
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
    if result.verdict == "REVERTS":
        lines = ["  {!r} undoes {}".format(path.decode("utf-8", "surrogateescape"), ", ".join(undone))
                 for path, undone in result.findings]
        print(
            "VIOLATION: branch-root: {} {}: at {} path(s) it holds the exact content that a first-parent "
            "commit of {} within the {} commits ending at that base replaced, so merging it reverts "
            "that change:\n{}\nRebuild the branch from its own changes on top of the merge base, or, when "
            "the restoration is deliberate, add a trailer `Reverts: <full sha>` naming each listed commit "
            "to a commit in <merge base>..<head>. This gate never rewrites a branch itself.".format(
                result.head_sha, result.detail, len(result.findings), result.protected_ref,
                REVERT_WINDOW, "\n".join(lines))
        )
        return
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


def run(root, protected=None, head=None, max_lag=None, revert_window=None):
    try:
        result = evaluate(root, protected=protected, head=head, max_lag=max_lag,
                          revert_window=revert_window)
    except CannotEvaluate as exc:
        print("ERROR: branch-root cannot evaluate: {}".format(exc), file=sys.stderr)
        return 2
    _print_result(result)
    return result.code


def _fixture_git(root, *args, identity=False, input_text=None, input_bytes=None):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _git_fixture_env import git_fixture_env
    env = git_fixture_env(GIT_NO_LAZY_FETCH="1", GIT_TERMINAL_PROMPT="0",
                          GIT_OPTIONAL_LOCKS="0", GIT_NO_REPLACE_OBJECTS="1")
    cmd = ["git", "-C", str(root)]
    if identity:
        cmd += [
            "-c", "user.name=Branch Root Self Test",
            "-c", "user.email=branch-root@example.invalid",
            "-c", "commit.gpgsign=false",
        ]
    cmd += list(args)
    if input_bytes is not None:
        return subprocess.run(
            cmd, input=input_bytes, capture_output=True, check=True, timeout=TIMEOUT, env=env,
        ).stdout.decode("ascii").strip()
    return subprocess.run(
        cmd,
        input=input_text,
        capture_output=True,
        text=True,
        check=True,
        timeout=TIMEOUT,
        env=env,
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


def _plumb_commit(root, files, parents, message, modes=None):
    """Write a flat tree of {path bytes: content bytes} with hash-object and mktree, then commit it, so
    a fixture path may carry a space, a newline or a non-UTF-8 byte whatever the filesystem allows.
    modes maps a path to a file mode other than 100644."""
    modes = modes or {}
    entries = b"".join(
        modes.get(path, b"100644") + b" blob "
        + _fixture_git(root, "hash-object", "-w", "--stdin", input_bytes=data).encode()
        + b"\t" + path + b"\0"
        for path, data in files.items())
    tree = _fixture_git(root, "mktree", "-z", input_bytes=entries)
    return _commit_tree(root, tree, parents, message)


def _revert_vectors(temp, failures, case):
    """R1-R19: the revert check. One repository holds every history; each case names its own refs."""
    def ref(name, sha):
        _fixture_git(repo, "update-ref", "refs/heads/" + name, sha)
        return "refs/heads/" + name

    def found(label, protected, head, want, revert_window=None):
        try:
            got = evaluate(repo, protected, head, revert_window=revert_window)
        except CannotEvaluate as exc:
            failures.append("{}: expected finding paths {!r}, got cannot-evaluate ({})".format(
                label, sorted(want), exc))
            return None
        paths = {path for path, _undone in got.findings}
        if paths != want:
            failures.append("{}: expected finding paths {!r}, got {!r} ({})".format(
                label, sorted(want), sorted(paths), got.verdict))
        return got

    repo = temp / "r-revert"
    repo.mkdir()
    _fixture_git(repo, "init", "-q", "-b", "protected")
    old = {b"seed.txt": b"seed\n", b"c.txt": b"c1\n"}
    base = _plumb_commit(repo, old, (), "seed")
    c1 = _plumb_commit(repo, {**old, b"b.txt": b"b\n"}, (base,), "main adds b")
    main_files = {**old, b"b.txt": b"b\n", b"c.txt": b"c2\n"}
    c2 = _plumb_commit(repo, main_files, (c1,), "main changes c")
    feature = {**old, b"f.txt": b"feature\n"}
    branch = _plumb_commit(repo, feature, (base,), "feature")
    main = ref("main-r", c2)

    # R1: the incident fold: the branch's tree committed on the newer protected tip. Merge base is the
    # tip, lag 0, so ancestry alone passes; the check names exactly the add and the change it reverts.
    fold = _plumb_commit(repo, feature, (c2,), "folded")
    case("R1 incident fold", 1, repo, main, ref("r1", fold))
    r1 = found("R1 incident fold", main, "refs/heads/r1", {b"b.txt", b"c.txt"})
    if r1 is not None and dict(r1.findings) != {b"b.txt": (c1,), b"c.txt": (c2,)}:
        failures.append("R1 incident fold: expected b.txt undoing {} and c.txt undoing {}, got {!r}"
                        .format(c1, c2, r1.findings))
    # R2: the same tree folded onto the pinned merge base.
    case("R2 pinned fold", 0, repo, main, ref("r2", _plumb_commit(repo, feature, (base,), "pinned")))
    # R3: a stale but correct branch that was never rebased.
    r3 = _plumb_commit(repo, {**feature, b"g.txt": b"g\n"}, (branch,), "feature two")
    case("R3 stale unrebased", 0, repo, main, ref("r3", r3))

    # R4: the same shape made through porcelain: check out the branch, mixed-reset onto the tip, add -A.
    _fixture_git(repo, "switch", "-q", "-c", "r4", branch)
    _fixture_git(repo, "reset", "-q", "--mixed", c2)
    _fixture_git(repo, "add", "-A")
    _fixture_git(repo, "commit", "-q", "-m", "reset fold", identity=True)
    if _fixture_git(repo, "rev-parse", "r4^{tree}") != _fixture_git(repo, "rev-parse", fold + "^{tree}"):
        failures.append("R4 reset fold: the porcelain fold did not reproduce the R1 tree")
    case("R4 reset fold", 1, repo, main, "refs/heads/r4")
    _fixture_git(repo, "switch", "-q", "--detach", base)

    # R5: the protected line advances again after the fold: it drops b.txt, changes c.txt once more and
    # adds n.txt. Anchored on the merge base c.txt is still found; b.txt is not, because the protected
    # tip dropped it too, so merging the fold leaves it as the tip has it. Anchored on the tip, n.txt
    # (which the fold never had) would be reported as well.
    drop = _plumb_commit(repo, {**old, b"c.txt": b"c2\n"}, (c2,), "main drops b")
    again = _plumb_commit(repo, {**old, b"c.txt": b"c3\n"}, (drop,), "main changes c again")
    later = _plumb_commit(repo, {**old, b"c.txt": b"c3\n", b"n.txt": b"n\n"}, (again,), "main adds n")
    advanced = ref("protected", later)
    case("R5 main advanced", 1, repo, advanced, "refs/heads/r1")
    found("R5 main advanced", advanced, "refs/heads/r1", {b"c.txt"})
    # R6: the branch rebased onto the tip.
    case("R6 rebased", 0, repo, main, ref("r6", _plumb_commit(
        repo, {**main_files, b"f.txt": b"feature\n"}, (c2,), "rebased")))

    # R7: a revert inside a file the branch also edits is not detected: the head's blob is new. This
    # pins the disclosed residual, so a change that starts catching it updates the residue too.
    shared = _plumb_commit(repo, {**old, b"s.txt": b"s1\nshared\n"}, (base,), "main adds s")
    shared2 = _plumb_commit(repo, {**old, b"s.txt": b"s2\nshared\n"}, (shared,), "main edits s")
    case("R7 shared-file residual", 0, repo, ref("r7-main", shared2), ref("r7", _plumb_commit(
        repo, {**old, b"s.txt": b"s1\nbranch\n"}, (shared2,), "fold over a shared edit")))

    # R8: a deliberate delete of a file the protected line added is a finding, and passes only with a
    # Reverts trailer naming the adding commit by its full id.
    dropped = {**old, b"c.txt": b"c2\n"}
    case("R8 deliberate delete", 1, repo, main, ref("r8", _plumb_commit(repo, dropped, (c2,), "drop b")))
    case("R8 deliberate delete with trailer", 0, repo, main, ref("r8t", _plumb_commit(
        repo, dropped, (c2,), "drop b\n\nReverts: {}".format(c1))))
    # R9: a trailer naming another commit, or the right commit abbreviated, allows nothing.
    case("R9 trailer names the wrong commit", 1, repo, main, ref("r9", _plumb_commit(
        repo, dropped, (c2,), "drop b\n\nReverts: {}".format(c2))))
    case("R9 abbreviated trailer", 1, repo, main, ref("r9a", _plumb_commit(
        repo, dropped, (c2,), "drop b\n\nReverts: {}".format(c1[:12]))))

    # R10: window boundary. w.txt changes, then three empty commits, so the change is the fourth commit
    # ending at the base: inside a window of 5, exactly at a window of 4, outside a window of 3.
    w0 = _plumb_commit(repo, {b"w.txt": b"w1\n"}, (), "w root")
    tip = _plumb_commit(repo, {b"w.txt": b"w2\n"}, (w0,), "w change")
    for i in range(3):
        tip = _plumb_commit(repo, {b"w.txt": b"w2\n"}, (tip,), "w filler {}".format(i))
    w_main = ref("r10-main", tip)
    w_head = ref("r10", _plumb_commit(repo, {b"w.txt": b"w1\n"}, (tip,), "w revert"))
    for label, window, want in (("inside", 5, {b"w.txt"}), ("at", 4, {b"w.txt"}), ("outside", 3, set())):
        found("R10 window {} K={}".format(label, window), w_main, w_head, want, window)
    if REVERT_WINDOW != 200:
        failures.append("R10 window: REVERT_WINDOW must stay 200 to match the pinned --max-lag 200")

    # R11: paths with a space, a newline and a non-UTF-8 byte are compared as exact bytes.
    odd = {b"with space.txt": b"1\n", b"new\nline.txt": b"2\n", b"bad-\xff-byte.txt": b"3\n"}
    q0 = _plumb_commit(repo, {b"q.txt": b"q\n"}, (), "q root")
    q1 = _plumb_commit(repo, {b"q.txt": b"q\n", **odd}, (q0,), "main adds odd names")
    q_head = ref("r11", _plumb_commit(repo, {b"q.txt": b"q\n"}, (q1,), "drops odd names"))
    found("R11 odd path bytes", ref("r11-main", q1), q_head, set(odd))

    # R13: a merge on the protected line's first-parent chain is diffed against its first parent.
    t0 = _plumb_commit(repo, {b"a.txt": b"a1\n", b"m.txt": b"m1\n"}, (), "t root")
    side = _plumb_commit(repo, {b"a.txt": b"a1\n", b"m.txt": b"m2\n"}, (t0,), "side changes m")
    t1 = _plumb_commit(repo, {b"a.txt": b"a2\n", b"m.txt": b"m1\n"}, (t0,), "main changes a")
    merged = _plumb_commit(repo, {b"a.txt": b"a2\n", b"m.txt": b"m2\n"}, (t1, side), "merge side")
    found("R13 first-parent merge", ref("r13-main", merged), ref("r13", _plumb_commit(
        repo, {b"a.txt": b"a2\n", b"m.txt": b"m1\n"}, (merged,), "reverts the merge's m")), {b"m.txt"})
    if "--diff-merges=first-parent" not in _WINDOW_LOG_ARGS:
        failures.append("R13 pin: the window walk must name --diff-merges=first-parent")

    # R14: the push self-check names the same commit twice: nothing changed, nothing to judge.
    case("R14 protected is head", 0, repo, main, main)

    # R15: a fold restores the exact pre-image blob of x.txt but as an executable file. The revert is
    # judged on the blob alone, so the mode change does not hide it.
    x0 = _plumb_commit(repo, {b"x.txt": b"x1\n"}, (), "x root")
    x1 = _plumb_commit(repo, {b"x.txt": b"x2\n"}, (x0,), "main changes x")
    x_head = ref("r15", _plumb_commit(repo, {b"x.txt": b"x1\n"}, (x1,), "restores x as executable",
                                      {b"x.txt": b"100755"}))
    case("R15 mode-flipped restore", 1, repo, ref("r15-main", x1), x_head)
    found("R15 mode-flipped restore", "refs/heads/r15-main", x_head, {b"x.txt"})

    # R16: two merge bases. The protected tip P merges side commit A (which changes p.txt) into S; the
    # head has parents A and S and puts p.txt back. From S (the least lag, 1) the head changes nothing
    # at p.txt; only from A (lag 2) is the revert visible. Every base is judged; the lag stays 1.
    p0 = _plumb_commit(repo, {b"p.txt": b"p1\n"}, (), "p root")
    p_side = _plumb_commit(repo, {b"p.txt": b"p2\n"}, (p0,), "side changes p")
    p_line = _plumb_commit(repo, {b"p.txt": b"p1\n", b"s.txt": b"s\n"}, (p0,), "main adds s")
    p_tip = _plumb_commit(repo, {b"p.txt": b"p2\n", b"s.txt": b"s\n"}, (p_line, p_side), "merge side")
    p_head = ref("r16", _plumb_commit(repo, {b"p.txt": b"p1\n", b"s.txt": b"s\n"}, (p_side, p_line),
                                      "merges both and puts p back"))
    p_main = ref("r16-main", p_tip)
    case("R16 two merge bases", 1, repo, p_main, p_head)
    r16 = found("R16 two merge bases", p_main, p_head, {b"p.txt"})
    if r16 is not None and (len(r16.bases) != 2 or r16.lag != 1):
        failures.append("R16 two merge bases: expected two bases and lag 1, got bases={} lag={}".format(
            len(r16.bases), r16.lag))

    # R17: a stale branch cut before the protected line reverted its own change to k.txt and its own
    # add of d.txt carries that revert. Merging it leaves both paths as the protected tip has them.
    k0 = _plumb_commit(repo, {b"k.txt": b"k1\n"}, (), "k root")
    k1 = _plumb_commit(repo, {b"k.txt": b"k2\n", b"d.txt": b"d\n"}, (k0,), "main changes k, adds d")
    k2 = _plumb_commit(repo, {b"k.txt": b"k1\n"}, (k1,), "main reverts k and d")
    k3 = _plumb_commit(repo, {b"k.txt": b"k1\n", b"o.txt": b"o\n"}, (k2,), "main adds o")
    k_head = ref("r17", _plumb_commit(repo, {b"k.txt": b"k1\n", b"g.txt": b"g\n"}, (k1,),
                                      "branch work carrying the revert of k and d"))
    k_main = ref("r17-main", k3)
    case("R17 carried protected revert", 0, repo, k_main, k_head)
    found("R17 carried protected revert", k_main, k_head, set())

    # R18 and R19 stand in for git with a wrapper around _git that rewrites one command's output.
    real_git = _git

    def faked(command, rewrite):
        def fake(root, *args, **kwargs):
            probe = real_git(root, *args, **kwargs)
            if args[0] == command and (command != "log" or "--first-parent" in args):
                return subprocess.CompletedProcess(probe.args, 0, rewrite(probe.stdout), b"")
            return probe
        return fake

    def judged(label, fake, want):
        globals()["_git"] = fake
        try:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                got = run(repo, main, "refs/heads/r1")
        except Exception as exc:
            got = "an uncaught {}".format(type(exc).__name__)
        finally:
            globals()["_git"] = real_git
        if got != want:
            failures.append("{}: expected exit {}, got {}".format(label, want, got))

    # R18: a git that exits 0 with an empty window log, or with one that does not list the base, leaves
    # nothing to judge: cannot-evaluate, never a pass.
    judged("R18 empty window log", faked("log", lambda _out: b""), 2)
    judged("R18 window log without the base",
           faked("log", lambda out: out.replace(c2.encode(), b"f" * len(c2))), 2)

    # R19: a raw record with a non-ASCII or otherwise malformed mode or object id is cannot-evaluate.
    def record(mode, oid):
        return b":" + mode + b" 100644 " + oid + b" " + b"a" * 40 + b" M\0b.txt\0"
    for label, mode, oid in (("non-ASCII oid", b"100644", b"\xff" * 40),
                             ("non-ASCII mode", b"10064\xff", b"a" * 40),
                             ("abbreviated oid", b"100644", b"a" * 12),
                             ("non-hex oid", b"100644", b"g" * 40)):
        judged("R19 " + label, faked("diff-tree", lambda _out, m=mode, o=oid: record(m, o)), 2)

    # R12: a shallow clone whose history ends inside the window is cannot-evaluate.
    shallow = temp / "r12-shallow"
    try:
        shallow_ready = _shallow_clone(temp, repo, shallow) is not None
    except (OSError, subprocess.SubprocessError, CannotEvaluate) as exc:
        shallow_ready = False
        _not_run(failures, "R12 shallow window", "a shallow file:// clone could not be built ({})".format(exc))
    else:
        if not shallow_ready:
            _not_run(failures, "R12 shallow window", "the shallow-clone builder returned no clone")
    if shallow_ready:
        shallow_head = _plumb_commit(shallow, {**old, b"c.txt": b"c2\n"}, ("HEAD",), "revert c in shallow")
        _fixture_git(shallow, "update-ref", "refs/heads/r12", shallow_head)
        case("R12 shallow window", 2, shallow, "refs/heads/protected", "refs/heads/r12")


def _mutant_vector(failures):
    """Each mutant of the revert check, applied by replacing module attributes, must turn the named
    R vector red in a nested self-test run."""
    real_findings, real_touches, real_records = _revert_findings, _window_touches, _raw_records

    def tip_anchored(root, _base, head_sha, window):
        return real_findings(root, _resolve_commit(root, "refs/heads/protected", "protected"),
                             head_sha, window)

    def post_images(root, base, window):
        return [(c, p, post, pre) for c, p, pre, post in real_touches(root, base, window)]

    def absence_ignored(root, base, window):
        return [record for record in real_touches(root, base, window) if record[2] is not None]

    def without_z(root, *args, commits=None):
        probe = _git(root, *args)
        records = []
        commit = None
        for line in probe.stdout.splitlines():
            if line.startswith(":") and "\t" in line:
                fields, path = line[1:].split("\t", 1)
                fields = fields.split(" ")
                records.append((commit, path.encode(),
                                None if fields[0] == "000000" else (fields[0], fields[2]),
                                None if fields[1] == "000000" else (fields[1], fields[3])))
            elif line:
                commit = line
                if commits is not None:
                    commits.append(commit)
        return records

    def least_lag_only(root, bases, protected_sha, head_sha, window):
        anchor = min(bases, key=lambda base: _count_lag(root, base, protected_sha))
        return real_findings(root, anchor, head_sha, window), [anchor]

    def decoded_unchecked(mode, oid, _command):
        return None if mode == b"000000" else (mode.decode("ascii"), oid.decode("ascii"))

    for label, injected, vector in (
            ("check removed", {"_revert_findings": lambda *_a: []}, "R1 "),
            ("anchored on the protected tip", {"_revert_findings": tip_anchored}, "R5 "),
            ("post-images compared", {"_window_touches": post_images}, "R1 "),
            ("absence ignored", {"_window_touches": absence_ignored}, "R1 "),
            ("any trailer accepted", {"_allowed": lambda _undone, named: bool(named)}, "R9 "),
            ("window one wider", {"_window_touches": lambda r, b, w: real_touches(r, b, w + 1)}, "R10 "),
            ("window one narrower", {"_window_touches": lambda r, b, w: real_touches(r, b, w - 1)}, "R10 "),
            ("parsed without -z", {"_raw_records": without_z}, "R11 "),
            ("--diff-merges=first-parent dropped", {"_WINDOW_LOG_ARGS": tuple(
                arg for arg in _WINDOW_LOG_ARGS if arg != "--diff-merges=first-parent")}, "R13 "),
            ("file mode compared with the blob", {"_blob": lambda image: image}, "R15 "),
            ("least-lag merge base only", {"_union_findings": least_lag_only}, "R16 "),
            ("protected tip image ignored", {"_settled": lambda _r, _p, _h, findings: findings}, "R17 "),
            ("window listing unchecked", {"_window_listed": lambda _commits, _base: None}, "R18 "),
            ("record bytes decoded unchecked", {"_image": decoded_unchecked}, "R19 ")):
        saved = {name: value for name, value in globals().items() if name in injected}
        missing = sorted(name for name in injected if name not in saved)
        if missing:
            raise KeyError(", ".join(missing))
        globals().update(injected)
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                rc = self_test(_vectors=False)
        finally:
            globals().update(saved)
        if rc == 0 or not _recorded(out.getvalue(), vector):
            failures.append("(mutant vector, {}) the mutant must turn {}red, got exit {}".format(
                label, vector, rc))


def _non_utf8_dir(parent):
    """The V10 fixture: make and return a directory under parent whose name carries a non-UTF-8 byte.
    Raises OSError or UnicodeError where the filesystem rejects such a name."""
    raw = os.fsencode(str(parent)) + b"/v10-non-utf8-\xff-repo"
    os.mkdir(raw)
    return Path(os.fsdecode(raw))


def _shallow_clone(parent, source, dest):
    """The V11 fixture: a depth-1 file:// clone of source at dest (a plain local-path clone ignores
    --depth). Raises OSError, SubprocessError or CannotEvaluate where a shallow clone cannot be built."""
    _fixture_git(parent, "clone", "--depth", "1", "-q", "file://{}".format(source), str(dest),
                 "-b", "protected")
    if not _is_shallow(dest):
        raise CannotEvaluate("the depth-1 file:// clone is not shallow")
    return dest


def _not_run(failures, case, cause):
    """Record a self-test case that could not run as a NAMED failure (a counted cannot-evaluate that
    fails the run), never a printed note beside a PASS: coverage that did not run is not coverage."""
    failures.append("NOT RUN: {}: {}; coverage that did not run FAILS the self-test, never a silent "
                    "PASS".format(case, cause))


_V10_CASE = "V10 non-UTF-8-git-output"
_V11_CASE = "V11 shallow-clone"


def _recorded(output, entry):
    """True when entry heads a bullet on the SELF-TEST FAIL list in output: a recorded failure prints as
    "  - <entry>" after that header, so a NOT RUN line that was only printed (never recorded) does not
    count."""
    _head, header, listed = output.partition("SELF-TEST FAIL:\n")
    return bool(header) and "\n  - " + entry in "\n" + listed


def _not_run_vector(failures):
    """Red-on-revert vector for the V10/V11 skips that printed SELF-TEST PASS (PARTIAL) and exited 0. Nested
    self-test runs replace the V10 builder alone, the V11 builder alone (the other fixture left real), and
    then both, with builders that raise; two more runs replace each builder alone with one that returns None
    instead of a fixture. Each run must exit nonzero AND carry every replaced case's NOT RUN bullet on its
    failure list. The single-case runs matter: in the combined run the sibling's recorded failure alone makes
    the exit nonzero, so a call site that only printed its NOT RUN line would pass there."""
    def _rejects_name(_parent):
        raise OSError("injected: a non-UTF-8 directory name is rejected")

    def _no_shallow(_parent, _source, _dest):
        raise CannotEvaluate("injected: a shallow file:// clone cannot be built")

    def _no_dir(_parent):
        return None

    def _no_clone(_parent, _source, _dest):
        return None

    for run_label, injected, labels in (
            ("V10 only unbuildable", {"_non_utf8_dir": _rejects_name}, (_V10_CASE,)),
            ("V11 only unbuildable", {"_shallow_clone": _no_shallow}, (_V11_CASE,)),
            ("V10 and V11 unbuildable",
             {"_non_utf8_dir": _rejects_name, "_shallow_clone": _no_shallow}, (_V10_CASE, _V11_CASE)),
            ("V10 builder returns None", {"_non_utf8_dir": _no_dir}, (_V10_CASE,)),
            ("V11 builder returns None", {"_shallow_clone": _no_clone}, (_V11_CASE,))):
        saved = {name: value for name, value in globals().items() if name in injected}
        missing = sorted(name for name in injected if name not in saved)
        if missing:
            raise KeyError(", ".join(missing))
        globals().update(injected)
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                rc = self_test(_vectors=False)
        finally:
            globals().update(saved)
        for label in labels:
            if rc == 0 or not _recorded(out.getvalue(), "NOT RUN: {}: ".format(label)):
                failures.append("(NOT RUN vector, {}) an unavailable {} fixture must exit nonzero AND record "
                                "a NOT RUN failure naming it on the failure list, never a PASS (PARTIAL) "
                                "exit 0, a silent skip or a bare printed line (got exit {})"
                                .format(run_label, label, rc))


def self_test(_vectors=True):
    try:
        temp = Path(tempfile.mkdtemp(prefix="aiqt-branchroot-selftest-"))
    except OSError as exc:
        print("SELF-TEST ERROR: cannot create fixture root: {}".format(exc), file=sys.stderr)
        return 2
    failures = []
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
        # (exit 2), never an uncaught decode crash to exit 1. A filesystem that rejects the name is a
        # NOT RUN failure (the fixture needs a byte-transparent filesystem such as the Linux CI runner).
        try:
            v10 = _non_utf8_dir(temp)
        except (OSError, UnicodeError) as exc:
            v10 = None
            _not_run(failures, _V10_CASE, "this filesystem rejects a non-UTF-8 directory name, so the "
                     "fixture cannot be built ({})".format(exc))
        else:
            if v10 is None:
                _not_run(failures, _V10_CASE, "the fixture builder returned no directory")
        if v10 is not None:
            _fixture_git(v10, "init", "-q", "-b", "protected")
            case("V10 non-utf8 git output is cannot-evaluate", 2, v10,
                 "refs/heads/protected", "HEAD")

        # V11: a shallow clone cannot tell a missing merge base from unfetched history, so a merge-base
        # exit 1 in a shallow repository is cannot-evaluate (exit 2), never a false ORPHANED deny. A
        # file:// clone with --depth is genuinely shallow (a plain local-path clone ignores --depth). Where
        # a shallow file:// clone cannot be built the case is a NOT RUN failure, never a skip beside a PASS.
        v11_src = _fixture_repo(temp, "v11-shallow-src")
        _fixture_commit(v11_src, "c2")  # depth >= 2 so a depth-1 clone actually truncates
        v11 = temp / "v11-shallow-clone"
        try:
            shallow_ready = _shallow_clone(temp, v11_src, v11) is not None
        except (OSError, subprocess.SubprocessError, CannotEvaluate) as exc:
            shallow_ready = False
            _not_run(failures, _V11_CASE, "a shallow file:// clone could not be built ({})".format(exc))
        else:
            if not shallow_ready:
                _not_run(failures, _V11_CASE, "the shallow-clone builder returned no clone")
        if shallow_ready:
            v11_tree = _fixture_git(v11, "rev-parse", "HEAD^{tree}")
            v11_orphan = _commit_tree(v11, v11_tree, (), "orphan in shallow")
            _fixture_git(v11, "update-ref", "refs/heads/orphan", v11_orphan)
            case("V11 shallow orphan is cannot-evaluate", 2, v11,
                 "refs/heads/protected", "refs/heads/orphan")

        _revert_vectors(temp, failures, case)
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

    if _vectors:
        _not_run_vector(failures)
        _mutant_vector(failures)

    if failures:
        print("SELF-TEST FAIL:")
        for failure in failures:
            print("  - " + failure)
        return 1
    covered = ("rooted, orphaned, configured/report-only stale, partial rewrite, underivable protected "
               "ref, missing head, malformed/negative threshold, two-base criss-cross, non-repository, and "
               "default-root/cwd-unavailable")
    print("SELF-TEST PASS: V1-V13 cover " + covered + ", non-UTF-8-git-output, and shallow-clone "
          "(both cannot-evaluate) outcomes; verdicts are asserted by exit code; an unbuildable V10 or V11 "
          "fixture is a NOT RUN failure (nonzero exit), never a PASS (PARTIAL); R1-R19 cover the revert "
          "check and each listed mutant of it turns its named R vector red")
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
