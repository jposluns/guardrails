#!/usr/bin/env python3
"""First-push helper: run the pre-push message/metadata check and the push in ONE process, bound to
ONE commit and ONE branch, so the check GATES exactly what the push publishes.

The failures this closes: a check run on its own line, with `git push` on the next line of the same
command or script, can pass control to the push while the check failed (a separate step is not
sequencing that preserves the gate's failure); and a check and a push that each resolve HEAD for
themselves can disagree when a commit, reset or checkout lands in between, publishing text that was
never scanned. Here the helper resolves the commit SHA and the branch name ONCE, hands both to the
check it launches itself (--head/--branch), and pushes that exact SHA by name only after it has
OBSERVED exit 0 from that check; a finding (exit 1) and a cannot-evaluate (exit 2 or anything else)
both refuse the push. The check binds its SCANNED SET to what the push would actually publish:
every commit reachable from that SHA and not reachable from any ref the target remote advertises at
check time (read with git ls-remote; local remote-tracking refs can be stale, and a path or URL
remote has none), so a commit below --base that the remote lacks is scanned too, and a set that
cannot be computed, or is empty, refuses. The check resolves --remote ONCE to a single literal
URL and accepts it only when every condition of a closed set holds, refusing otherwise with a
named reason: a printable --remote with no whitespace; for a configured remote exactly one push
URL, byte-identical to its one fetch URL (a pushurl, an added URL or a pushInsteadOf rewrite
refuses); a destination that is an absolute local path or a file://, https:// or ssh:// URL
(an ext:: or other helper transport, an scp-like name, a relative path or a bare name refuses)
and does not name a configured remote; no legacy remotes or branches file under the git path at
all; no url.<base>.insteadOf or pushInsteadOf value prefixing the raw configured or resolved
URL (an unrelated rewrite for another URL stays allowed); no core.sshCommand, ssh.variant or
remote uploadpack/receivepack key; and no GIT_SSH or GIT_SSH_COMMAND in the environment. It
reads the advertisement from that URL and reports it on a `push destination:` line; this helper
then pushes to exactly that literal URL, never to the remote's name, so a setting that rewrites
or routes the destination cannot point the advertisement read and the push at different
repositories. One residual no local check can see: a server that routes its upload-pack and
receive-pack services to different repositories behind one URL. The check also ignores
replace refs and a grafts file on every git read, so a replace ref cannot substitute a clean
message, parent list or changelog for the ones the push sends (the push transports the original
objects; a grafts file left in force would instead break the push itself on the missing objects,
so that pin is defence in depth). --base stays
as a cross-check of the caller's intent and must be a proper ancestor of that SHA; when the default
branch has advanced past the branch point, pass the merge-base (git merge-base <default-branch>
HEAD), not the advanced tip. The push passes --no-follow-tags, so a push.followTags configuration
cannot attach unscanned annotated tags to the gated push (pushing a tag remains outside this helper
either way), and --recurse-submodules=no, so a push.recurseSubmodules configuration cannot act on
the gated push (observed with this helper's SHA refspec, that configuration makes the push fail on
the submodule rather than publish its never scanned commits; the pin keeps the gated push to the
scanned superproject commits either way).

  push_branch.py --body-file <abs path> --base <40-hex sha> [--title <text>] [--remote <name>]
  push_branch.py --self-test

The check is tools/check_msg_leaks.py --pre-push (the script beside this one), over the repository
containing the current working directory: the drafted PR body file (composed with --title when one
is given), the message of every commit in the published set, every string each of those commits
adds to the parsed changelog.toml against its parent or parents (every intermediate state the push
publishes, not only the endpoints), and the branch name, against the hashed leak denylist, the
STRUCTURAL patterns and the narrow AI-attribution layer. Every input is explicit; there is no
ambient default for the body or the base, and the helper hands the check the same --remote it will
push to. On a pass the check prints the sha256 of the exact body bytes it scanned and the one
resolved destination URL; this helper reprints the digest and never rereads the body itself, so the
digest it prints can only describe bytes the check scanned, and the body later given to the PR can
be compared against them. It then runs `git push --no-follow-tags --recurse-submodules=no
<resolved-url> <sha>:refs/heads/<branch>` for the commit and branch it resolved before the check
and the destination URL the check read the advertisement from. A push that exits non-zero is reported and mapped to exit 2 (the
helper's cannot-evaluate verdict), not passed through as the push's own exit status. After a
successful push the branch's upstream is set in a separate step (a SHA-sourced push to a literal
URL records no remote-tracking ref itself, so where --remote was a configured name the helper
first records the pushed commit under the remote-tracking ref that remote's OWN fetch refspecs
map the branch to, the ref a fetch from it would update; when no refspec maps the branch, the
mapping is ambiguous, the mapped ref lies outside refs/remotes/, or the mapped ref is a symbolic
ref, nothing is recorded and the skip is reported plainly, because an invented ref name could
shadow, or through a pre-existing symbolic ref move, a ref a fetch would never touch); if the
upstream step fails, the completed, scanned push is still reported with exit 0 and the line says
so (a --remote given as a URL has no remote-tracking ref, so there the step always fails and the
line is expected).

FAIL-CLOSED: an unknown, repeated or valueless argument (--self-test mixed with anything else
included: it is honoured only as the sole argument), a working directory outside a git repository,
a HEAD that does not resolve to a commit, a detached HEAD, a branch name git itself rejects, any
non-zero exit from the check, a check that cannot be launched, and a passing check that does not
report the scanned-body digest or exactly one resolved push destination each refuse the push
(nothing is pushed), exit 2 (exit 1 when the check exits 1). The check's output is reprinted, then the refusal is printed. A check that crashes
with exit 1 is indistinguishable here from a finding and is labelled as one; either way nothing is
pushed.

HONEST SCOPE. This helper gates only pushes made THROUGH it: a plain `git push`, an IDE push, or
any push made without it bypasses it entirely (a later push is gated only when it is again made
through this helper), and the CI scan after the PR opens is the backstop there. Without --title the
eventual PR title is not scanned before the push; the CI pre-merge run is its first scan. A body
edited between this run and the PR creation is caught only by comparing the printed digest or by
the CI run on the edited event. The check's own residuals (paraphrased attribution, AI names
outside the shared identity set, obfuscated denylist terms, changelog text outside the parsed
strings, such as TOML comments) carry over.

The --self-test builds throwaway git repositories and a bare remote under a scrubbed fixture
environment (tools/_git_fixture_env.py) and asserts, for each finding channel (a leaky commit below
--base, a leaky intermediate changelog state, and a leaky commit hidden by a replace ref or a
grafts file included) and a broad set of fail-closed shapes (a remote whose push URL set splits
from its one fetch URL through a pushurl, an added URL or a pushInsteadOf rewrite, a remote name
with trailing whitespace, a legacy remotes or branches file present at all, an insteadOf rewrite
matching the configured URL, a destination that resolves to a configured remote's name or falls
outside the allowed URL shapes, a per-remote uploadpack or receivepack program, a core.sshCommand
or ssh.variant setting, and a GIT_SSH or GIT_SSH_COMMAND variable included), that the helper exits
non-zero AND the remote gained no ref, and that a clean branch pushes (to the resolved literal
URL, with controls for a byte-identical pushurl, a file:// destination, and a rewrite configured
for an unrelated URL), a push under push.followTags publishes no unscanned tag, and a push under
push.recurseSubmodules publishes no unscanned submodule commit; that the tracking ref after a push
through a configured name follows that remote's own fetch refspec mapping, is skipped (and said
so) when no refspec maps the branch, and never moves another ref through a pre-existing symbolic
tracking ref; it also drives the check directly for shapes
reachable only there (a subdirectory --repo, a --head bound to a commit other than the checkout, a
detached HEAD, a destination unusable as one URL), and drives a copy of this helper against stub
checks for the digest-missing and unexpected-exit refusals and for the race in which a commit lands
between the check and the push, where the remote must hold exactly the scanned SHA.
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
import re
import subprocess
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

HERE = Path(__file__).resolve()
CHECK = HERE.parent / "check_msg_leaks.py"
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")   # a canonical commit id: resolved once, scanned, then pushed
# The digest line the check prints for the exact body bytes it scanned; the helper reprints it and
# never rereads the body, so the printed digest cannot describe bytes the check did not scan.
_BODY_DIGEST_RE = re.compile(r"^body sha256: ([0-9a-f]{64})$", re.MULTILINE)
# The destination line the check prints on a pass: the one literal URL it resolved --remote to
# and read the advertisement from, accepted only from its closed set of URL shapes with every
# rewrite and service-routing setting it can see refused. The push below goes to exactly this
# URL, never to the remote's name. The check refuses a destination carrying a line break, so one
# line always holds the whole URL; requiring exactly one match keeps a second line from ever
# smuggling in another destination.
_DESTINATION_RE = re.compile(r"^push destination: (.+)$", re.MULTILINE)
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


def _head_commit(toplevel):
    """The commit HEAD names, resolved ONCE and returned as a canonical SHA; None on any failure.
    The check scans exactly this commit and the push publishes exactly this commit, so a commit or
    checkout between the check and the push cannot change what is published to unscanned text."""
    try:
        proc = subprocess.run(["git", "-C", toplevel, "rev-parse", "--verify", "HEAD^{commit}"],
                              capture_output=True, encoding="utf-8")
    except (OSError, ValueError):
        return None
    if proc.returncode != 0:
        return None
    sha = proc.stdout.strip()
    return sha if _SHA_RE.match(sha) else None


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


def _fetch_refspec_target(toplevel, remote, branch):
    """The remote-tracking ref the remote's OWN fetch refspecs map refs/heads/<branch> to, or
    (None, reason) when the mapping gives nothing to record. Only the configured mapping
    decides: a ref name invented here could shadow, or through a pre-existing symbolic ref
    move, a ref a fetch from this remote would never touch. A negative refspec and a refspec
    without a destination (it fetches into FETCH_HEAD only) map nothing; a glob refspec maps
    through its one '*' on each side; several refspecs naming different destinations are
    ambiguous and map nothing."""
    try:
        proc = subprocess.run(["git", "-C", toplevel, "config", "-z", "--get-all",
                               "remote." + remote + ".fetch"],
                              capture_output=True, encoding="utf-8")
    except (OSError, ValueError):
        return None, "the remote's fetch refspecs could not be read"
    if proc.returncode != 0:
        return None, "this remote has no fetch refspec"
    source = "refs/heads/" + branch
    records = proc.stdout.split("\0")
    if records and records[-1] == "":
        records.pop()
    targets = set()
    for refspec in records:
        spec = refspec[1:] if refspec.startswith("+") else refspec
        if spec.startswith("^") or ":" not in spec:
            continue
        src, _sep, dst = spec.partition(":")
        if src.count("*") == 1 and dst.count("*") == 1:
            prefix, _sep, suffix = src.partition("*")
            if (source.startswith(prefix) and source.endswith(suffix)
                    and len(source) >= len(prefix) + len(suffix)):
                targets.add(dst.replace(
                    "*", source[len(prefix):len(source) - len(suffix)], 1))
        elif "*" not in src and "*" not in dst and src == source:
            targets.add(dst)
    if not targets:
        return None, "no fetch refspec of this remote maps the pushed branch"
    if len(targets) > 1:
        return None, "the remote's fetch refspecs map the pushed branch ambiguously"
    return targets.pop(), None


def _record_tracking_ref(toplevel, remote, branch, head):
    """Record the pushed commit under the remote-tracking ref the remote's own fetch refspecs
    map the branch to (the ref a fetch from that remote would update), so the upstream step can
    bind to it. Returns (ref, None) when recorded, else (None, reason). A mapped ref outside
    refs/remotes/ is not recorded (it is not a remote-tracking ref, and a leading '-' could
    otherwise be read as an option), and a mapped ref that is a symbolic ref is skipped: writing
    through it would move whatever ref it points at, a local branch included, which a fetch
    never does (update-ref runs with --no-deref as a second barrier)."""
    target, reason = _fetch_refspec_target(toplevel, remote, branch)
    if target is None:
        return None, reason
    if not target.startswith("refs/remotes/"):
        return None, "the mapped tracking ref lies outside refs/remotes/"
    try:
        symref = subprocess.run(["git", "-C", toplevel, "symbolic-ref", "-q", target],
                                capture_output=True, encoding="utf-8")
    except (OSError, ValueError):
        return None, "the mapped tracking ref could not be inspected"
    if symref.returncode == 0:
        return None, "the mapped tracking ref is a symbolic ref"
    if symref.returncode != 1:
        return None, "the mapped tracking ref could not be inspected"
    try:
        updated = subprocess.run(["git", "-C", toplevel, "update-ref", "--no-deref",
                                  target, head], capture_output=True, encoding="utf-8")
    except (OSError, ValueError):
        return None, "the tracking ref could not be recorded"
    if updated.returncode != 0:
        return None, "the tracking ref could not be recorded"
    return target, None


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
    """The gated first push. The helper resolves the commit and the branch once, the check (run as
    a child of THIS process) scans exactly that commit and branch name, and the push publishes
    exactly that commit to exactly that branch, so a failed check cannot be sequenced around and a
    commit or checkout between the check and the push cannot change what is published."""
    opts, problem = parse_argv(argv)
    if problem is not None:
        _refuse(problem)
        return 2
    toplevel = _toplevel()
    if not toplevel:
        _refuse("the working directory is not inside a git repository")
        return 2
    head = _head_commit(toplevel)
    if not head:
        _refuse("cannot resolve HEAD to a commit")
        return 2
    branch = _current_branch(toplevel)
    if not branch:
        _refuse("cannot establish the current branch (detached HEAD)")
        return 2
    if not _valid_branch(toplevel, branch):
        _refuse("the current branch name is not a valid branch ref")
        return 2
    check_argv = [sys.executable, "-I", "-B", str(CHECK), "--pre-push", "--repo", toplevel,
                  "--body-file", opts["body-file"], "--base", opts["base"],
                  "--head", head, "--branch", branch, "--remote", opts["remote"]]
    if opts["title"] is not None:
        check_argv += ["--title", opts["title"]]
    try:
        check = subprocess.run(check_argv, capture_output=True, encoding="utf-8")
    except (OSError, ValueError) as exc:
        _refuse("could not launch the pre-push check ({})".format(type(exc).__name__))
        return 2
    sys.stdout.write(check.stdout)       # the check's output is reprinted; its exit is the verdict
    sys.stderr.write(check.stderr)
    if check.returncode != 0:
        _refuse("the pre-push check did not pass (exit {})".format(check.returncode))
        return 1 if check.returncode == 1 else 2
    digest = _BODY_DIGEST_RE.search(check.stdout)
    if digest is None:
        _refuse("the check passed but did not report the scanned-body digest")
        return 2
    destinations = _DESTINATION_RE.findall(check.stdout)
    if len(destinations) != 1:
        _refuse("the check passed but did not report exactly one resolved push destination")
        return 2
    destination = destinations[0]
    print("pre-push check passed; body sha256: {}".format(digest.group(1)))
    # --no-follow-tags: a push.followTags configuration would otherwise attach annotated tags,
    # whose messages this check never scanned, to the gated push. --recurse-submodules=no: a
    # push.recurseSubmodules configuration would otherwise act on the gated push (observed with
    # this helper's SHA refspec, it makes the push fail on the submodule rather than publish its
    # commits; the pin keeps the gated push to the scanned superproject commits either way). The
    # push goes to the literal URL the check resolved and read the advertisement from, never to
    # the remote's name, so what was scanned and what is written are one repository.
    pushed = subprocess.run(["git", "-C", toplevel, "push", "--no-follow-tags",
                             "--recurse-submodules=no", destination,
                             head + ":refs/heads/" + branch])
    if pushed.returncode != 0:
        print("push exited non-zero ({})".format(pushed.returncode))
        return 2
    if destination != opts["remote"]:
        # a push to a literal URL records no remote-tracking ref; where --remote was a configured
        # name (the only case in which the resolved destination differs from it), record the
        # pushed commit under the ref that remote's own fetch refspecs map the branch to (the ref
        # a fetch from it would update), so the upstream step below can bind to it; with no such
        # mapping nothing is recorded and the skip is reported, because an invented ref name
        # could shadow, or through a pre-existing symbolic ref move, an unrelated ref
        recorded, skip_reason = _record_tracking_ref(toplevel, opts["remote"], branch, head)
    else:
        recorded, skip_reason = None, ("--remote is itself the destination URL, which has no "
                                       "remote-tracking ref")
    if recorded is not None:
        print("tracking ref recorded at {}".format(recorded))
    else:
        print("no tracking ref recorded ({})".format(skip_reason))
    upstream = subprocess.run(["git", "-C", toplevel, "branch",
                               "--set-upstream-to=" + opts["remote"] + "/" + branch, branch],
                              capture_output=True, encoding="utf-8")
    if upstream.returncode != 0:
        print("push completed; upstream not set (no remote-tracking ref answers to that "
              "remote/branch name here)")
        return 0
    print("push completed; upstream set")
    return 0


def main(argv):
    if argv == ["--self-test"]:        # honoured only as the sole argument, never mixed into a real run
        return self_test()
    return run_push(argv)


def _expect(condition, message, failures):
    if not condition:
        failures.append(str(message))


def self_test():
    """Hermetic end-to-end vectors over throwaway repositories and a bare remote: each finding
    channel refuses the push and leaves the remote without the ref; each exercised fail-closed
    shape does the same with exit 2; a clean branch (an empty body included) pushes, prints the
    digest of the scanned body, and gets its upstream set."""
    import shutil
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

    def helper(args, cwd, script=None, **env_overrides):
        return subprocess.run([sys.executable, "-I", "-B", str(script or HERE), *args],
                              cwd=str(cwd), env=git_fixture_env(**env_overrides),
                              capture_output=True, encoding="utf-8")

    def check_direct(args, cwd):
        return subprocess.run([sys.executable, "-I", "-B", str(CHECK), *args], cwd=str(cwd),
                              env=git_fixture_env(), capture_output=True, encoding="utf-8")

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
            rc = fixture_git_rc(["rev-parse", "--verify", "--quiet", "refs/heads/" + name], origin)
            if rc not in (0, 1):                 # 1 is absence; anything else is an inspection error
                raise AssertionError("ref inspection failed for {} (exit {})".format(name, rc))
            return rc == 0

        # 1. clean branch, clean body: the push happens, the digest of the scanned bytes is printed
        #    (once by the check, once by this helper), and the upstream is set by the config step
        branch_with_commit("feat-clean")
        done = helper(["--body-file", str(clean_body), "--base", base], repo)
        _expect(done.returncode == 0, ("clean push", done.returncode, done.stdout, done.stderr), failures)
        _expect(ref_exists("feat-clean"), "clean push must create the remote ref", failures)
        digest = hashlib.sha256(clean_body.read_bytes()).hexdigest()
        _expect(done.stdout.count("body sha256: " + digest) == 2,
                "the digest must be printed by the check and reprinted by the helper", failures)
        up = fixture_git(["rev-parse", "--abbrev-ref", "feat-clean@{upstream}"], repo).strip()
        _expect(up == "origin/feat-clean", ("upstream", up), failures)

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
        # a changelog leak OUTSIDE the items array: a second release's title, scanned because every
        # parsed string is scanned
        branch_with_commit("feat-chlog-title", changelog='title = "t"\n[[release]]\nitems = '
                           '["base item"]\n[[release]]\ntitle = "notes drafted using ChatGPT"\n')
        finding_vectors.append(("feat-chlog-title", ["--body-file", str(clean_body), "--base", base]))
        # a misspelled items key: its string value is still scanned, never silently ignored
        branch_with_commit("feat-chlog-field", changelog='title = "t"\n[[release]]\nitems = '
                           '["base item"]\nitem = "mentions alpha bravo"\n')
        finding_vectors.append(("feat-chlog-field", ["--body-file", str(clean_body), "--base", base]))
        branch_with_commit("feat-msg", message="touch alpha bravo")
        finding_vectors.append(("feat-msg", ["--body-file", str(clean_body), "--base", base]))
        branch_with_commit("alpha-bravo-fix")
        finding_vectors.append(("alpha-bravo-fix", ["--body-file", str(clean_body), "--base", base]))
        # the title channel: a leaky --title refuses even with a clean body and branch
        branch_with_commit("feat-title")
        finding_vectors.append(("feat-title", ["--body-file", str(clean_body), "--base", base,
                                               "--title", "touch alpha bravo"]))
        for name, args in finding_vectors:
            fixture_git(["checkout", "-q", name], repo)
            done = helper(args, repo)
            _expect(done.returncode == 1, (name, done.returncode, done.stdout, done.stderr), failures)
            _expect("REFUSED" in done.stdout, (name, "refusal line missing"), failures)
            _expect("body sha256" not in done.stdout, (name, "digest printed on a refusal"), failures)
            _expect(not ref_exists(name), (name, "a finding must not push"), failures)
        # the control for the title vector: the same branch with a clean title pushes
        done = helper(["--body-file", str(clean_body), "--base", base, "--title", "a clean title"],
                      repo)
        _expect(done.returncode == 0, ("clean title", done.returncode, done.stdout, done.stderr), failures)
        _expect(ref_exists("feat-title"), "a clean titled push must create the remote ref", failures)

        # fail-closed: each refuses with exit 2 and the remote gains no ref
        branch_with_commit("feat-failclosed")
        link_body = tmp / "body-link.md"
        link_body.symlink_to(clean_body)
        binary_body = tmp / "body-binary.md"
        binary_body.write_bytes(b"\xff\xfe\x00")
        # body-rel.md EXISTS relative to the working directory, so the relative-body vector fails
        # only through the absolute-path guard, never through absence
        (repo / "body-rel.md").write_text("A normal body.\n", encoding="utf-8")
        for label, args in (
                ("relative body", ["--body-file", "body-rel.md", "--base", base]),
                ("missing body", ["--body-file", str(tmp / "absent.md"), "--base", base]),
                ("symlink body", ["--body-file", str(link_body), "--base", base]),
                ("non-UTF-8 body", ["--body-file", str(binary_body), "--base", base]),
                ("non-SHA base", ["--body-file", str(clean_body), "--base", "HEAD"]),
                ("zero-SHA base", ["--body-file", str(clean_body), "--base", "0" * 40]),
                ("nonexistent base", ["--body-file", str(clean_body), "--base", "b" * 40]),
                ("empty title", ["--body-file", str(clean_body), "--base", base, "--title", ""]),
                ("unknown flag", ["--body-file", str(clean_body), "--base", base, "--force", "x"]),
                ("repeated flag", ["--body-file", str(clean_body), "--body-file", str(clean_body),
                                   "--base", base]),
                ("valueless flag", ["--body-file", str(clean_body), "--base"]),
                ("mixed self-test flag", ["--self-test", "--base", base]),
                ("missing required", ["--base", base])):
            done = helper(args, repo)
            _expect(done.returncode == 2, (label, done.returncode, done.stdout, done.stderr), failures)
            _expect("REFUSED" in done.stdout, (label, "refusal line missing"), failures)
            _expect(not ref_exists("feat-failclosed"), (label, "a refusal must not push"), failures)
        # the push range must be exactly what the push adds: a base equal to the pushed commit (an
        # empty range: nothing would be scanned) and a base that is not an ancestor both refuse
        own_head = fixture_git(["rev-parse", "HEAD"], repo).strip()
        done = helper(["--body-file", str(clean_body), "--base", own_head], repo)
        _expect(done.returncode == 2, ("base equals HEAD", done.returncode, done.stdout, done.stderr),
                failures)
        _expect(not ref_exists("feat-failclosed"), "an empty range must not push", failures)
        unrelated = fixture_git(["rev-parse", "refs/heads/feat-clean"], repo).strip()
        done = helper(["--body-file", str(clean_body), "--base", unrelated], repo)
        _expect(done.returncode == 2, ("non-ancestor base", done.returncode, done.stdout, done.stderr),
                failures)
        _expect(not ref_exists("feat-failclosed"), "a non-ancestor base must not push", failures)
        branch_with_commit("feat-badtoml", changelog="not toml [")
        done = helper(["--body-file", str(clean_body), "--base", base], repo)
        _expect(done.returncode == 2, ("bad changelog", done.returncode, done.stderr), failures)
        _expect(not ref_exists("feat-badtoml"), "a malformed changelog must not push", failures)
        # a changelog deleted on the branch: present at base, absent at head, never read as clean
        fixture_git(["checkout", "-q", "main"], repo)
        fixture_git(["checkout", "-q", "-b", "feat-chlog-del"], repo)
        fixture_git(["rm", "-q", "changelog.toml"], repo)
        fixture_git(["commit", "-q", "-m", "a clean message"], repo)
        done = helper(["--body-file", str(clean_body), "--base", base], repo)
        _expect(done.returncode == 2, ("deleted changelog", done.returncode, done.stdout, done.stderr),
                failures)
        _expect(not ref_exists("feat-chlog-del"), "a deleted changelog must not push", failures)
        # the scanned set is bound to what the push PUBLISHES: a leaky commit BELOW --base that the
        # remote lacks is still scanned and refuses (a base..head scan pushed it unscanned); the
        # clean-tip-over-clean-history control is the feat-clean vector above
        fixture_git(["checkout", "-q", "main"], repo)
        fixture_git(["checkout", "-q", "-b", "feat-below"], repo)
        (repo / "work.txt").write_text("below one\n", encoding="utf-8")
        fixture_git(["add", "-A"], repo)
        fixture_git(["commit", "-q", "-m", "touch alpha bravo"], repo)
        below_leaky = fixture_git(["rev-parse", "HEAD"], repo).strip()
        (repo / "work.txt").write_text("below two\n", encoding="utf-8")
        fixture_git(["add", "-A"], repo)
        fixture_git(["commit", "-q", "-m", "a clean message"], repo)
        done = helper(["--body-file", str(clean_body), "--base", below_leaky], repo)
        _expect(done.returncode == 1,
                ("leaky commit below base", done.returncode, done.stdout, done.stderr), failures)
        _expect(not ref_exists("feat-below"), "a leaky commit below base must not push", failures)
        _expect(fixture_git_rc(["cat-file", "-e", below_leaky], origin) != 0,
                "the leaky commit below base must not reach the remote", failures)
        # an intermediate changelog state the push publishes is scanned: a leaky string added in
        # one commit and removed again before the tip refuses although the endpoint delta is empty
        branch_with_commit("feat-chlog-mid", changelog='title = "t"\n[[release]]\nitems = '
                           '["base item", "notes drafted using ChatGPT"]\n')
        (repo / "changelog.toml").write_text('title = "t"\n[[release]]\nitems = ["base item"]\n',
                                             encoding="utf-8")
        fixture_git(["add", "-A"], repo)
        fixture_git(["commit", "-q", "-m", "a clean message"], repo)
        done = helper(["--body-file", str(clean_body), "--base", base], repo)
        _expect(done.returncode == 1,
                ("intermediate changelog leak", done.returncode, done.stdout, done.stderr), failures)
        _expect(not ref_exists("feat-chlog-mid"), "an intermediate changelog leak must not push",
                failures)
        # a changelog deleted in an INTERMEDIATE commit fails closed even when the tip restores it
        fixture_git(["checkout", "-q", "main"], repo)
        fixture_git(["checkout", "-q", "-b", "feat-chlog-middel"], repo)
        fixture_git(["rm", "-q", "changelog.toml"], repo)
        fixture_git(["commit", "-q", "-m", "a clean message"], repo)
        (repo / "changelog.toml").write_text('title = "t"\n[[release]]\nitems = ["base item"]\n',
                                             encoding="utf-8")
        fixture_git(["add", "-A"], repo)
        fixture_git(["commit", "-q", "-m", "a clean message"], repo)
        done = helper(["--body-file", str(clean_body), "--base", base], repo)
        _expect(done.returncode == 2,
                ("intermediate deleted changelog", done.returncode, done.stdout, done.stderr),
                failures)
        _expect(not ref_exists("feat-chlog-middel"),
                "an intermediate changelog deletion must not push", failures)
        # a push whose every commit the remote already advertises publishes nothing new and fails
        # closed rather than scanning an empty set as clean (under a base..head rule this pushed:
        # the base was a proper ancestor and the local range was non-empty)
        old_clean_tip = fixture_git(["rev-parse", "refs/heads/feat-clean"], repo).strip()
        fixture_git(["checkout", "-q", "-b", "feat-dup", old_clean_tip], repo)
        done = helper(["--body-file", str(clean_body), "--base", base], repo)
        _expect(done.returncode == 2,
                ("already-published set", done.returncode, done.stdout, done.stderr), failures)
        _expect(not ref_exists("feat-dup"), "an empty published set must not push", failures)
        # push.followTags cannot attach an unscanned annotated tag: the helper pushes with
        # --no-follow-tags, so the branch lands and the tag does not
        branch_with_commit("feat-tags")
        fixture_git(["config", "push.followTags", "true"], repo)
        fixture_git(["tag", "-a", "v-tagged", "-m", "touch alpha bravo"], repo)
        done = helper(["--body-file", str(clean_body), "--base", base], repo)
        _expect(done.returncode == 0,
                ("followTags push", done.returncode, done.stdout, done.stderr), failures)
        _expect(ref_exists("feat-tags"), "the followTags push must still create the branch ref",
                failures)
        _expect(fixture_git_rc(["rev-parse", "--verify", "--quiet", "refs/tags/v-tagged"],
                               origin) != 0,
                "an unscanned annotated tag must not ride along on the gated push", failures)
        fixture_git(["config", "--unset", "push.followTags"], repo)
        fixture_git(["tag", "-d", "v-tagged"], repo)
        # the scanned set must describe the repository the push WRITES to: a remote whose push
        # URL set is not exactly its one fetch URL could have the fetch side already advertising
        # a leaky commit the push side lacks, excluding it from the scan while the push still
        # sends it, so any such split refuses (exit 2) before anything is read or pushed
        seeded = tmp / "seeded.git"
        pushside = tmp / "pushside.git"
        fixture_git(["init", "-q", "--bare", str(seeded)], tmp)
        fixture_git(["init", "-q", "--bare", str(pushside)], tmp)
        fixture_git(["checkout", "-q", "main"], repo)
        fixture_git(["checkout", "-q", "-b", "feat-pushurl"], repo)
        (repo / "work.txt").write_text("split one\n", encoding="utf-8")
        fixture_git(["add", "-A"], repo)
        fixture_git(["commit", "-q", "-m", "touch alpha bravo"], repo)
        split_leaky = fixture_git(["rev-parse", "HEAD"], repo).strip()
        (repo / "work.txt").write_text("split two\n", encoding="utf-8")
        fixture_git(["add", "-A"], repo)
        fixture_git(["commit", "-q", "-m", "a clean message"], repo)
        fixture_git(["remote", "add", "split", str(seeded)], repo)
        # the fetch side already advertises the leaky commit; the push side holds nothing
        fixture_git(["push", "-q", "split", split_leaky + ":refs/heads/seeded"], repo)
        fixture_git(["config", "remote.split.pushurl", str(pushside)], repo)
        done = helper(["--body-file", str(clean_body), "--base", base, "--remote", "split"], repo)
        _expect(done.returncode == 2,
                ("split push URL", done.returncode, done.stdout, done.stderr), failures)
        _expect("push URL" in done.stderr, "the split-URL refusal must name the rule", failures)
        _expect(fixture_git_rc(["cat-file", "-e", split_leaky], pushside) != 0,
                "a split push URL must not publish the unscanned commit", failures)
        # the control: a pushurl byte-identical to the one fetch URL is one repository, so the
        # same branch scans against what the push sends (the leaky commit is advertised there,
        # leaving the clean tip as the published set) and pushes
        fixture_git(["config", "remote.split.pushurl", str(seeded)], repo)
        done = helper(["--body-file", str(clean_body), "--base", base, "--remote", "split"], repo)
        _expect(done.returncode == 0,
                ("identical pushurl", done.returncode, done.stdout, done.stderr), failures)
        _expect(fixture_git_rc(["rev-parse", "--verify", "--quiet", "refs/heads/feat-pushurl"],
                               seeded) == 0,
                "a pushurl identical to the fetch URL must still push", failures)
        # the control's push left the branch tip advertised, under which every later vector on
        # this branch would refuse through the empty-published-set rule whatever the URL rules
        # did; deleting the pushed ref keeps the published set non-empty, so each vector below
        # passes only through its own refusal (asserted by message); with that refusal removed,
        # the message assertions are what fail (the helper pushes only to the one resolved URL,
        # so the second repository still gains nothing)
        fixture_git(["update-ref", "-d", "refs/heads/feat-pushurl"], seeded)
        # an added second URL: push sends to EVERY configured URL while the advertisement is
        # read from the first, so the same split refuses
        fixture_git(["config", "--unset", "remote.split.pushurl"], repo)
        fixture_git(["remote", "set-url", "--add", "split", str(pushside)], repo)
        done = helper(["--body-file", str(clean_body), "--base", base, "--remote", "split"], repo)
        _expect(done.returncode == 2,
                ("added second URL", done.returncode, done.stdout, done.stderr), failures)
        _expect("push URL" in done.stderr, "the added-URL refusal must name the rule", failures)
        _expect(fixture_git_rc(["cat-file", "-e", split_leaky], pushside) != 0,
                "an added second URL must not publish the unscanned commit", failures)
        fixture_git(["remote", "set-url", "--delete", "split", str(pushside)], repo)
        # a pushInsteadOf rewrite redirects only the push: on a configured remote the expanded
        # push URL differs from the fetch URL and refuses; on a --remote given as a URL there is
        # no configured push side to compare, and the rewrite applies to a literal push URL even
        # beside a byte-identical pushurl, so any configured rewrite at all refuses
        fixture_git(["config", "url." + str(pushside) + ".pushInsteadOf", str(seeded)], repo)
        done = helper(["--body-file", str(clean_body), "--base", base, "--remote", "split"], repo)
        _expect(done.returncode == 2,
                ("pushInsteadOf remote", done.returncode, done.stdout, done.stderr), failures)
        _expect("push URL" in done.stderr,
                "the configured-remote rewrite refusal must name the rule", failures)
        _expect(fixture_git_rc(["cat-file", "-e", split_leaky], pushside) != 0,
                "a configured-remote rewrite must not publish the unscanned commit", failures)
        done = helper(["--body-file", str(clean_body), "--base", base, "--remote", str(seeded)],
                      repo)
        _expect(done.returncode == 2,
                ("pushInsteadOf URL", done.returncode, done.stdout, done.stderr), failures)
        _expect("pushInsteadOf" in done.stderr,
                "the URL-remote rewrite refusal must name the rule", failures)
        _expect(fixture_git_rc(["cat-file", "-e", split_leaky], pushside) != 0,
                "a pushInsteadOf rewrite must not publish the unscanned commit", failures)
        fixture_git(["config", "--remove-section", "url." + str(pushside)], repo)
        # a remote defined only by a legacy .git/remotes file is not a configured remote, so its
        # name reaches the URL handling, where git would still read it as that legacy remote
        # (ls-remote from its first URL line, push to every URL line): the name refuses as a
        # destination before anything is read or pushed
        legacy_dir = repo / ".git" / "remotes"
        legacy_dir.mkdir(exist_ok=True)
        (legacy_dir / "legsplit").write_text(
            "URL: {}\nURL: {}\n".format(seeded, pushside), encoding="utf-8")
        done = helper(["--body-file", str(clean_body), "--base", base, "--remote", "legsplit"],
                      repo)
        _expect(done.returncode == 2,
                ("legacy remotes file", done.returncode, done.stdout, done.stderr), failures)
        _expect("legacy" in done.stderr, "the legacy-remote refusal must name the rule", failures)
        _expect(fixture_git_rc(["cat-file", "-e", split_leaky], pushside) != 0,
                "a legacy remotes file must not publish the unscanned commit", failures)
        (legacy_dir / "legsplit").unlink()
        # a legacy .git/branches file is the same one-file remote mechanism: refused alike
        branches_dir = repo / ".git" / "branches"
        branches_dir.mkdir(exist_ok=True)
        (branches_dir / "legbranch").write_text(str(seeded) + "\n", encoding="utf-8")
        done = helper(["--body-file", str(clean_body), "--base", base, "--remote", "legbranch"],
                      repo)
        _expect(done.returncode == 2,
                ("legacy branches file", done.returncode, done.stdout, done.stderr), failures)
        _expect("legacy" in done.stderr, "the branches-file refusal must name the rule", failures)
        (branches_dir / "legbranch").unlink()
        # a configured remote whose one URL is itself another configured remote's name: the
        # resolved destination would be read by git as that remote, not as a URL, so it refuses
        fixture_git(["remote", "add", "alias", "split"], repo)
        done = helper(["--body-file", str(clean_body), "--base", base, "--remote", "alias"], repo)
        _expect(done.returncode == 2,
                ("alias destination", done.returncode, done.stdout, done.stderr), failures)
        _expect("names a configured remote" in done.stderr,
                "the alias refusal must name the rule", failures)
        fixture_git(["remote", "remove", "alias"], repo)
        # a per-remote uploadpack or receivepack program routes one of the two services to
        # another repository entirely, so any such key, together with core.sshCommand and
        # ssh.variant, refuses before anything is read or pushed
        for label, key, value, fragment in (
                ("uploadpack key", "remote.split.uploadpack",
                 "git upload-pack '" + str(seeded) + "' #", "uploadpack or receivepack"),
                ("receivepack key", "remote.split.receivepack",
                 "git receive-pack '" + str(pushside) + "' #", "uploadpack or receivepack"),
                ("core.sshCommand key", "core.sshCommand", "ssh -F /nonexistent",
                 "core.sshCommand"),
                ("ssh.variant key", "ssh.variant", "simple", "ssh.variant")):
            fixture_git(["config", key, value], repo)
            done = helper(["--body-file", str(clean_body), "--base", base,
                           "--remote", "split"], repo)
            _expect(done.returncode == 2,
                    (label, done.returncode, done.stdout, done.stderr), failures)
            _expect(fragment in done.stderr,
                    (label, "the refusal must name the rule", done.stderr), failures)
            _expect(fixture_git_rc(["cat-file", "-e", split_leaky], pushside) != 0,
                    (label, "a service-routing key must not publish the unscanned commit"),
                    failures)
            _expect(fixture_git_rc(["rev-parse", "--verify", "--quiet",
                                    "refs/heads/feat-pushurl"], seeded) != 0,
                    (label, "a service-routing key must not push"), failures)
            fixture_git(["config", "--unset", key], repo)
        # GIT_SSH / GIT_SSH_COMMAND route the ssh transport the same way from the environment,
        # so either variable refuses; every other vector here runs with both unset (the control)
        for variable in ("GIT_SSH", "GIT_SSH_COMMAND"):
            done = helper(["--body-file", str(clean_body), "--base", base,
                           "--remote", "split"], repo, **{variable: "/bin/false"})
            _expect(done.returncode == 2,
                    (variable, done.returncode, done.stdout, done.stderr), failures)
            _expect("the {} environment variable is set".format(variable) in done.stderr,
                    (variable, "the refusal must name the variable", done.stderr), failures)
            _expect(fixture_git_rc(["rev-parse", "--verify", "--quiet",
                                    "refs/heads/feat-pushurl"], seeded) != 0,
                    (variable, "an ssh environment override must not push"), failures)
        # an insteadOf rewrite whose value prefixes the remote's raw configured URL has already
        # rewritten the resolution once, and one prefixing the resolved URL would rewrite the
        # ls-remote and push of the literal URL again (a chain), so each refuses; the push side
        # must gain nothing
        fixture_git(["config", "url." + str(pushside) + ".insteadOf", str(seeded)], repo)
        done = helper(["--body-file", str(clean_body), "--base", base, "--remote", "split"], repo)
        _expect(done.returncode == 2,
                ("insteadOf on configured remote", done.returncode, done.stdout, done.stderr),
                failures)
        _expect("url.<base>.insteadOf rewrite matches" in done.stderr,
                "the raw-URL rewrite refusal must name the rule", failures)
        _expect(fixture_git_rc(["cat-file", "-e", split_leaky], pushside) != 0,
                "a rewrite of the configured URL must not publish the unscanned commit", failures)
        done = helper(["--body-file", str(clean_body), "--base", base, "--remote", str(seeded)],
                      repo)
        _expect(done.returncode == 2,
                ("insteadOf on URL remote", done.returncode, done.stdout, done.stderr), failures)
        _expect("url.<base>.insteadOf rewrite matches" in done.stderr,
                "the resolved-URL rewrite refusal must name the rule", failures)
        _expect(fixture_git_rc(["cat-file", "-e", split_leaky], pushside) != 0,
                "a rewrite of the resolved URL must not publish the unscanned commit", failures)
        fixture_git(["config", "--remove-section", "url." + str(pushside)], repo)
        # the control for the rewrite rule: a rewrite configured for an UNRELATED URL prefix
        # matches neither the raw nor the resolved URL and must stay allowed, so the clean push
        # still lands (the pushed ref is deleted again to keep later published sets non-empty)
        fixture_git(["config", "url.https://unrelated.example/a.insteadOf",
                     "https://nowhere.example/a"], repo)
        fixture_git(["config", "url.https://unrelated.example/b.pushInsteadOf",
                     "https://nowhere.example/b"], repo)
        done = helper(["--body-file", str(clean_body), "--base", base, "--remote", "split"], repo)
        _expect(done.returncode == 0,
                ("unrelated rewrite control", done.returncode, done.stdout, done.stderr),
                failures)
        _expect(fixture_git_rc(["rev-parse", "--verify", "--quiet",
                                "refs/heads/feat-pushurl"], seeded) == 0,
                "an unrelated rewrite must not block the clean push", failures)
        fixture_git(["config", "--remove-section", "url.https://unrelated.example/a"], repo)
        fixture_git(["config", "--remove-section", "url.https://unrelated.example/b"], repo)
        fixture_git(["update-ref", "-d", "refs/heads/feat-pushurl"], seeded)
        # a destination outside the closed shape set (an ext:: or other helper transport, an
        # scp-like name, a relative path) can route the two services separately or resolve
        # against the working directory, so each refuses by shape, before any transport runs
        for bad_shape in ("ext::fake", "example.org:repo.git", "../seeded.git"):
            done = helper(["--body-file", str(clean_body), "--base", base,
                           "--remote", bad_shape], repo)
            _expect(done.returncode == 2,
                    ("destination shape", bad_shape, done.returncode, done.stdout, done.stderr),
                    failures)
            _expect("not an absolute local path" in done.stderr,
                    ("the shape refusal must name the rule", bad_shape), failures)
            _expect(fixture_git_rc(["rev-parse", "--verify", "--quiet",
                                    "refs/heads/feat-pushurl"], seeded) != 0,
                    (bad_shape, "a shape refusal must not push"), failures)
        # a remote name carrying whitespace refuses outright: git itself reads a legacy file
        # whose name keeps the trailing space, so a name-keyed probe that strips it would check
        # one path while git reads another
        legacy_dir = repo / ".git" / "remotes"
        legacy_dir.mkdir(exist_ok=True)
        (legacy_dir / "lg ").write_text(
            "URL: {}\nURL: {}\n".format(seeded, pushside), encoding="utf-8")
        done = helper(["--body-file", str(clean_body), "--base", base, "--remote", "lg "], repo)
        _expect(done.returncode == 2,
                ("trailing-space name", done.returncode, done.stdout, done.stderr), failures)
        _expect("whitespace" in done.stderr,
                "the whitespace-name refusal must name the rule", failures)
        _expect(fixture_git_rc(["cat-file", "-e", split_leaky], pushside) != 0,
                "a whitespace name must not publish the unscanned commit", failures)
        # while that file exists, even a clean configured remote refuses: a legacy remotes or
        # branches file present at all means a name-shaped destination somewhere could be read
        # as that remote, so presence alone fails closed, whatever the file is called
        done = helper(["--body-file", str(clean_body), "--base", base, "--remote", "split"], repo)
        _expect(done.returncode == 2,
                ("legacy file present", done.returncode, done.stdout, done.stderr), failures)
        _expect("legacy" in done.stderr,
                "the legacy-presence refusal must name the rule", failures)
        _expect(fixture_git_rc(["rev-parse", "--verify", "--quiet",
                                "refs/heads/feat-pushurl"], seeded) != 0,
                "a present legacy file must not push", failures)
        (legacy_dir / "lg ").unlink()
        # a --remote given directly as a URL still pushes cleanly; the clean run names the
        # resolved destination, and a URL records no remote-tracking ref, so the upstream
        # convenience step reports it was not set
        branch_with_commit("feat-urlremote")
        done = helper(["--body-file", str(clean_body), "--base", base, "--remote", str(origin)],
                      repo)
        _expect(done.returncode == 0,
                ("URL-remote clean push", done.returncode, done.stdout, done.stderr), failures)
        _expect(ref_exists("feat-urlremote"),
                "a URL-remote clean push must create the remote ref", failures)
        _expect("push destination: " + str(origin) in done.stdout,
                "the check must report the resolved destination it read", failures)
        _expect("no tracking ref recorded" in done.stdout,
                ("URL-remote tracking line", done.stdout), failures)
        _expect("upstream not set" in done.stdout,
                ("URL-remote upstream line", done.stdout), failures)
        # a file:// URL is inside the closed destination shape set: the control for that rule
        branch_with_commit("feat-fileurl")
        done = helper(["--body-file", str(clean_body), "--base", base,
                       "--remote", "file://" + str(origin)], repo)
        _expect(done.returncode == 0,
                ("file URL clean push", done.returncode, done.stdout, done.stderr), failures)
        _expect(ref_exists("feat-fileurl"),
                "a file:// clean push must create the remote ref", failures)
        # the tracking ref after a push through a configured name follows that remote's OWN
        # fetch refspec mapping, never an invented name: with a custom mapping the mapped ref is
        # written and the invented one is not
        fixture_git(["remote", "add", "mapped", str(origin)], repo)
        fixture_git(["config", "--replace-all", "remote.mapped.fetch",
                     "+refs/heads/*:refs/remotes/mapped/imported/*"], repo)
        branch_with_commit("feat-mapped")
        mapped_head = fixture_git(["rev-parse", "HEAD"], repo).strip()
        done = helper(["--body-file", str(clean_body), "--base", base, "--remote", "mapped"],
                      repo)
        _expect(done.returncode == 0,
                ("mapped push", done.returncode, done.stdout, done.stderr), failures)
        _expect("tracking ref recorded at refs/remotes/mapped/imported/feat-mapped"
                in done.stdout, ("mapped tracking line", done.stdout), failures)
        recorded_at = fixture_git(["rev-parse", "refs/remotes/mapped/imported/feat-mapped"],
                                  repo).strip()
        _expect(recorded_at == mapped_head, ("mapped tracking ref", recorded_at), failures)
        _expect(fixture_git_rc(["rev-parse", "--verify", "--quiet",
                                "refs/remotes/mapped/feat-mapped"], repo) != 0,
                "the helper must not invent a tracking ref outside the fetch mapping", failures)
        # with no refspec mapping the pushed branch, nothing is recorded and the skip is
        # reported plainly
        fixture_git(["config", "--replace-all", "remote.mapped.fetch",
                     "+refs/heads/main:refs/remotes/mapped/main"], repo)
        branch_with_commit("feat-unmapped")
        done = helper(["--body-file", str(clean_body), "--base", base, "--remote", "mapped"],
                      repo)
        _expect(done.returncode == 0,
                ("unmapped push", done.returncode, done.stdout, done.stderr), failures)
        _expect("no tracking ref recorded (no fetch refspec of this remote maps the pushed "
                "branch)" in done.stdout, ("unmapped tracking line", done.stdout), failures)
        _expect(fixture_git_rc(["rev-parse", "--verify", "--quiet",
                                "refs/remotes/mapped/feat-unmapped"], repo) != 0,
                "an unmapped branch must record no tracking ref", failures)
        fixture_git(["remote", "remove", "mapped"], repo)
        # a pre-existing SYMBOLIC tracking ref is never written through: writing it would move
        # the ref it points at (local main here), which a fetch never does; the skip is reported
        main_sha = fixture_git(["rev-parse", "refs/heads/main"], repo).strip()
        fixture_git(["symbolic-ref", "refs/remotes/origin/feat-symref", "refs/heads/main"],
                    repo)
        branch_with_commit("feat-symref")
        done = helper(["--body-file", str(clean_body), "--base", base], repo)
        _expect(done.returncode == 0,
                ("symref push", done.returncode, done.stdout, done.stderr), failures)
        _expect("no tracking ref recorded (the mapped tracking ref is a symbolic ref)"
                in done.stdout, ("symref tracking line", done.stdout), failures)
        _expect(fixture_git(["rev-parse", "refs/heads/main"], repo).strip() == main_sha,
                "a symbolic tracking ref must not move the local ref it points at", failures)
        fixture_git(["update-ref", "--no-deref", "-d", "refs/remotes/origin/feat-symref"],
                    repo)
        # a replace ref cannot substitute a clean message for the one the push sends: the push
        # transports the ORIGINAL object, so the scan reads the original graph and still refuses
        fixture_git(["checkout", "-q", "main"], repo)
        fixture_git(["checkout", "-q", "-b", "feat-replace"], repo)
        (repo / "work.txt").write_text("replace work\n", encoding="utf-8")
        fixture_git(["add", "-A"], repo)
        fixture_git(["commit", "-q", "-m", "touch alpha bravo"], repo)
        replaced = fixture_git(["rev-parse", "HEAD"], repo).strip()
        replaced_tree = fixture_git(["rev-parse", "HEAD^{tree}"], repo).strip()
        stand_in = fixture_git(["commit-tree", replaced_tree, "-p", base, "-m",
                                "a clean message"], repo).strip()
        fixture_git(["replace", replaced, stand_in], repo)
        done = helper(["--body-file", str(clean_body), "--base", base], repo)
        _expect(done.returncode == 1,
                ("replaced leaky commit", done.returncode, done.stdout, done.stderr), failures)
        _expect(not ref_exists("feat-replace"), "a replaced leaky commit must not push", failures)
        _expect(fixture_git_rc(["cat-file", "-e", replaced], origin) != 0,
                "the original of a replaced leaky commit must not reach the remote", failures)
        fixture_git(["replace", "-d", replaced], repo)
        # a grafts file cannot cut a leaky parent out of the walked ancestry: the scan reads the
        # true parents, so the grafted-out commit is still scanned and still refuses (defence in
        # depth: with the graft honoured, this push is rejected by the remote for missing objects
        # rather than transporting the hidden commit, so the exit-code and landed-ref assertions
        # are what discriminate here, not the object's absence from the remote)
        fixture_git(["checkout", "-q", "main"], repo)
        fixture_git(["checkout", "-q", "-b", "feat-graft"], repo)
        (repo / "work.txt").write_text("graft one\n", encoding="utf-8")
        fixture_git(["add", "-A"], repo)
        fixture_git(["commit", "-q", "-m", "touch alpha bravo"], repo)
        graft_leaky = fixture_git(["rev-parse", "HEAD"], repo).strip()
        (repo / "work.txt").write_text("graft two\n", encoding="utf-8")
        fixture_git(["add", "-A"], repo)
        fixture_git(["commit", "-q", "-m", "a clean message"], repo)
        graft_tip = fixture_git(["rev-parse", "HEAD"], repo).strip()
        (repo / ".git" / "info" / "grafts").write_text("{} {}\n".format(graft_tip, base),
                                                       encoding="utf-8")
        done = helper(["--body-file", str(clean_body), "--base", base], repo)
        _expect(done.returncode == 1,
                ("grafted-out leaky commit", done.returncode, done.stdout, done.stderr), failures)
        _expect(not ref_exists("feat-graft"), "a grafted-out leaky commit must not push", failures)
        _expect(fixture_git_rc(["cat-file", "-e", graft_leaky], origin) != 0,
                "a grafted-out leaky commit must not reach the remote", failures)
        (repo / ".git" / "info" / "grafts").unlink()
        # push.recurseSubmodules cannot act on the gated push: the push pins
        # --recurse-submodules=no, so the superproject branch lands while the submodule's
        # unpushed (never scanned) commit stays local (without the pin, this helper's SHA
        # refspec makes the configured push fail outright rather than publish the submodule
        # commit, so the exit-code and landed-branch assertions are what discriminate here)
        subremote = tmp / "subremote.git"
        fixture_git(["init", "-q", "-b", "main", "--bare", str(subremote)], tmp)
        subwork = tmp / "subwork"
        fixture_git(["init", "-q", "-b", "main", str(subwork)], tmp)
        fixture_git(["config", "user.name", "Fixture Maintainer"], subwork)
        fixture_git(["config", "user.email", "fixture@example.org"], subwork)
        (subwork / "sub.txt").write_text("sub\n", encoding="utf-8")
        fixture_git(["add", "-A"], subwork)
        fixture_git(["commit", "-q", "-m", "sub base"], subwork)
        fixture_git(["remote", "add", "origin", str(subremote)], subwork)
        fixture_git(["push", "-q", "origin", "main"], subwork)
        fixture_git(["checkout", "-q", "main"], repo)
        fixture_git(["checkout", "-q", "-b", "feat-submodule"], repo)
        fixture_git(["-c", "protocol.file.allow=always", "submodule", "add", "-q",
                     str(subremote), "sub"], repo)
        inner = repo / "sub"
        fixture_git(["config", "user.name", "Fixture Maintainer"], inner)
        fixture_git(["config", "user.email", "fixture@example.org"], inner)
        (inner / "sub.txt").write_text("sub two\n", encoding="utf-8")
        fixture_git(["add", "-A"], inner)
        fixture_git(["commit", "-q", "-m", "an unpushed submodule message"], inner)
        sub_unpushed = fixture_git(["rev-parse", "HEAD"], inner).strip()
        fixture_git(["add", "-A"], repo)
        fixture_git(["commit", "-q", "-m", "a clean message"], repo)
        fixture_git(["config", "push.recurseSubmodules", "on-demand"], repo)
        done = helper(["--body-file", str(clean_body), "--base", base], repo)
        _expect(done.returncode == 0,
                ("submodule push", done.returncode, done.stdout, done.stderr), failures)
        _expect(ref_exists("feat-submodule"),
                "the gated push must still land the superproject branch", failures)
        _expect(fixture_git_rc(["cat-file", "-e", sub_unpushed], subremote) != 0,
                "an unscanned submodule commit must not ride along on the gated push", failures)
        fixture_git(["config", "--unset", "push.recurseSubmodules"], repo)
        fixture_git(["checkout", "-q", "main"], repo)
        shutil.rmtree(repo / "sub", ignore_errors=True)   # keep later add -A fixtures clean
        # a destination whose advertisement cannot be read refuses at CHECK time (an absolute
        # path with no repository behind it: the shape is allowed, the read fails), so the
        # published set cannot be established and nothing is pushed; a bare name such as
        # "nosuch" refuses earlier, by shape
        branch_with_commit("feat-badremote")
        done = helper(["--body-file", str(clean_body), "--base", base,
                       "--remote", str(tmp / "nosuch.git")], repo)
        _expect(done.returncode == 2, ("bad remote", done.returncode, done.stdout, done.stderr), failures)
        _expect("ls-remote" in done.stderr, "an unreadable remote advertisement must be named",
                failures)
        _expect(not ref_exists("feat-badremote"), "an unreadable remote must not push", failures)
        # a push that fails AFTER a passing check (a non-fast-forward here: the remote branch was
        # pushed above and the local one is amended away from it) is reported and mapped to exit 2
        fixture_git(["checkout", "-q", "feat-clean"], repo)
        fixture_git(["commit", "-q", "--amend", "-m", "a clean message reworded"], repo)
        done = helper(["--body-file", str(clean_body), "--base", base], repo)
        _expect(done.returncode == 2,
                ("non-fast-forward push", done.returncode, done.stdout, done.stderr), failures)
        _expect("push exited non-zero" in done.stdout, "a failed push must be reported", failures)
        remote_clean = fixture_git(["rev-parse", "refs/heads/feat-clean"], origin).strip()
        _expect(remote_clean == old_clean_tip,
                "a failed push must leave the remote branch unchanged", failures)
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
        # a repository whose HEAD does not resolve to a commit (no commit yet) refuses before the check
        unborn = tmp / "unborn"
        fixture_git(["init", "-q", "-b", "main", str(unborn)], tmp)
        done = helper(["--body-file", str(clean_body), "--base", base], unborn)
        _expect(done.returncode == 2, ("unborn HEAD", done.returncode, done.stdout, done.stderr), failures)
        _expect("cannot resolve HEAD to a commit" in done.stdout,
                "the helper itself must refuse an unresolvable HEAD", failures)

        # a passing check that reports no digest, and a check with an unexpected exit status, must
        # both refuse: drive a COPY of this helper against stub checks (the copy resolves its check
        # beside itself, so the stub stands in without touching the real one)
        stub = tmp / "stub"
        stub.mkdir()
        (stub / "push_branch.py").write_bytes(HERE.read_bytes())
        (stub / "check_msg_leaks.py").write_text(
            'import sys\nprint("PASS (stub): no digest line")\nsys.exit(0)\n', encoding="utf-8")
        branch_with_commit("feat-stub")
        done = helper(["--body-file", str(clean_body), "--base", base], repo,
                      script=stub / "push_branch.py")
        _expect(done.returncode == 2, ("missing digest", done.returncode, done.stdout), failures)
        _expect("did not report the scanned-body digest" in done.stdout,
                "a digestless pass must name the refusal", failures)
        _expect(not ref_exists("feat-stub"), "a digestless pass must not push", failures)
        # a passing check that reports a digest but no resolved destination refuses too: the
        # helper must never fall back to pushing the remote's name
        (stub / "check_msg_leaks.py").write_text(
            'import sys\nprint("body sha256: " + "0" * 64)\nsys.exit(0)\n', encoding="utf-8")
        done = helper(["--body-file", str(clean_body), "--base", base], repo,
                      script=stub / "push_branch.py")
        _expect(done.returncode == 2,
                ("missing destination", done.returncode, done.stdout), failures)
        _expect("exactly one resolved push destination" in done.stdout,
                "a destinationless pass must name the refusal", failures)
        _expect(not ref_exists("feat-stub"), "a destinationless pass must not push", failures)
        # two destination lines are as unusable as none: exactly one is required (the real check
        # refuses a destination carrying a line break, so it can only ever print one)
        (stub / "check_msg_leaks.py").write_text(
            'import sys\nprint("body sha256: " + "0" * 64)\n'
            'print("push destination: /one")\nprint("push destination: /two")\nsys.exit(0)\n',
            encoding="utf-8")
        done = helper(["--body-file", str(clean_body), "--base", base], repo,
                      script=stub / "push_branch.py")
        _expect(done.returncode == 2,
                ("two destinations", done.returncode, done.stdout), failures)
        _expect("exactly one resolved push destination" in done.stdout,
                "a two-destination pass must name the refusal", failures)
        _expect(not ref_exists("feat-stub"), "a two-destination pass must not push", failures)
        (stub / "check_msg_leaks.py").write_text('import sys\nsys.exit(3)\n', encoding="utf-8")
        done = helper(["--body-file", str(clean_body), "--base", base], repo,
                      script=stub / "push_branch.py")
        _expect(done.returncode == 2, ("check exit 3", done.returncode, done.stdout), failures)
        _expect("(exit 3)" in done.stdout, "an unexpected check status must be named", failures)
        _expect(not ref_exists("feat-stub"), "an unexpected check status must not push", failures)

        # the push is bound to the commit the check scanned, THROUGH the helper: a wrapper check
        # runs the real check, then commits leaky text, so HEAD moves between the check and the
        # push; the remote must receive exactly the scanned SHA and never the racing commit (a
        # helper pushing HEAD, or dropping the --head binding, fails this vector)
        racing_check = (
            "import subprocess, sys\n"
            "from pathlib import Path\n"
            "if '--head' not in sys.argv or '--branch' not in sys.argv:\n"
            "    sys.exit(3)\n"   # the helper must hand the check the exact commit and branch
            "done = subprocess.run([sys.executable, '-I', '-B', " + repr(str(CHECK)) + "]\n"
            "                      + sys.argv[1:], capture_output=True, encoding='utf-8')\n"
            "sys.stdout.write(done.stdout)\n"
            "sys.stderr.write(done.stderr)\n"
            "if done.returncode == 0:\n"
            "    repo = sys.argv[sys.argv.index('--repo') + 1]\n"
            "    Path(repo, 'race.txt').write_text('raced\\n', encoding='utf-8')\n"
            "    subprocess.run(['git', '-C', repo, 'add', '-A'], capture_output=True)\n"
            "    subprocess.run(['git', '-C', repo, 'commit', '-q', '-m',\n"
            "                    'racing touch alpha bravo'], capture_output=True)\n"
            "sys.exit(done.returncode)\n")
        branch_with_commit("feat-race")
        scanned = fixture_git(["rev-parse", "HEAD"], repo).strip()
        (stub / "check_msg_leaks.py").write_text(racing_check, encoding="utf-8")
        done = helper(["--body-file", str(clean_body), "--base", base], repo,
                      script=stub / "push_branch.py")
        _expect(done.returncode == 0, ("race push", done.returncode, done.stdout, done.stderr),
                failures)
        moved = fixture_git(["rev-parse", "HEAD"], repo).strip()
        _expect(moved != scanned, "the racing wrapper must move HEAD before the push", failures)
        remote_tip = (fixture_git(["rev-parse", "refs/heads/feat-race"], origin).strip()
                      if ref_exists("feat-race") else None)
        _expect(remote_tip == scanned, ("race remote tip", remote_tip, scanned), failures)
        _expect(fixture_git_rc(["cat-file", "-e", moved], origin) != 0,
                "the racing commit must not reach the remote", failures)

        # shapes reachable only by driving the check directly
        # --repo must be the repository root: from a subdirectory the changelog would read as absent
        done = check_direct(["--pre-push", "--repo", str(repo / "tools"), "--body-file",
                             str(clean_body), "--base", base], repo)
        _expect(done.returncode == 2, ("subdir repo", done.returncode, done.stdout, done.stderr),
                failures)
        _expect("repository root" in done.stderr, "the subdir refusal must name the root rule", failures)
        # --head binds the scan to the exact commit the helper pushes, not to the checkout: with a
        # clean branch checked out, scanning the leaky commit's SHA still finds its leak
        leaky = fixture_git(["rev-parse", "refs/heads/feat-msg"], repo).strip()
        fixture_git(["checkout", "-q", "feat-clean"], repo)
        done = check_direct(["--pre-push", "--repo", str(repo), "--body-file", str(clean_body),
                             "--base", base, "--head", leaky, "--branch", "feat-msg"], repo)
        _expect(done.returncode == 1, ("bound head", done.returncode, done.stdout, done.stderr),
                failures)
        # a --head that names no commit in the repository fails closed
        done = check_direct(["--pre-push", "--repo", str(repo), "--body-file", str(clean_body),
                             "--base", base, "--head", "c" * 40, "--branch", "feat-msg"], repo)
        _expect(done.returncode == 2, ("unknown head", done.returncode, done.stderr), failures)
        # an empty --branch fails closed, named as such (the flag is accepted but its value is not)
        done = check_direct(["--pre-push", "--repo", str(repo), "--body-file", str(clean_body),
                             "--base", base, "--head", leaky, "--branch", ""], repo)
        _expect(done.returncode == 2, ("empty branch", done.returncode, done.stderr), failures)
        _expect("--branch" in done.stderr, "the empty-branch refusal must name the flag", failures)
        # a destination that begins with '-' can never be handed to ls-remote and push, or
        # printed, as one URL, and one carrying a line break falls to the whitespace-name rule:
        # each fails closed naming its rule
        for bad_destination, fragment in (("-seeded", "cannot be handed"),
                                          ("a\nb", "whitespace")):
            done = check_direct(["--pre-push", "--repo", str(repo), "--body-file",
                                 str(clean_body), "--base", base, "--head", leaky,
                                 "--branch", "feat-msg", "--remote", bad_destination], repo)
            _expect(done.returncode == 2,
                    ("unusable destination", bad_destination, done.returncode, done.stderr),
                    failures)
            _expect(fragment in done.stderr,
                    ("the unusable-destination refusal must name the rule", bad_destination),
                    failures)
        # without --branch the check resolves the current branch itself, so a detached HEAD fails closed
        fixture_git(["checkout", "-q", "--detach", "main"], repo)
        done = check_direct(["--pre-push", "--repo", str(repo), "--body-file", str(clean_body),
                             "--base", base], repo)
        _expect(done.returncode == 2, ("check detached", done.returncode, done.stderr), failures)
    if failures:
        print("SELF-TEST FAIL ({} failure(s)):".format(len(failures)))
        for failure in failures:
            print("  " + failure)
        return 1
    print("SELF-TEST PASS: a clean branch (an empty body or a clean title included) pushes to the "
          "one resolved destination URL the check reports, prints the scanned-body digest twice "
          "(check and helper), gets its upstream set (reported as not set for a --remote given as "
          "a URL), carries no unscanned tag along even under push.followTags and no unscanned "
          "submodule commit even under push.recurseSubmodules, and still pushes under a pushurl "
          "byte-identical to the one fetch URL, over a file:// destination, and beside a rewrite "
          "configured for an unrelated URL prefix; the tracking ref follows the remote's own "
          "fetch refspec mapping, is skipped with a plain report when no refspec maps the pushed "
          "branch or --remote is itself the URL, and never writes through a pre-existing "
          "symbolic tracking ref (local main stays put); a commit "
          "landing between the check and the push leaves exactly the scanned SHA on the remote, "
          "never the racing commit; every finding channel (an attribution trailer in the body, a "
          "denylist term in the body, a changelog string added in any published commit, an "
          "intermediate state or a leak outside the items array included, a commit message "
          "anywhere in the published set, a leaky commit below --base, behind a replace ref or "
          "behind a grafts file included, the branch name, a leaky --title) refuses with exit 1 "
          "and no remote ref; and every exercised fail-closed shape (a relative, missing, "
          "symlinked or non-UTF-8 body, a non-SHA, zero-SHA or nonexistent base, a base equal to "
          "the pushed commit or not its ancestor, an empty title, an unknown, repeated or "
          "valueless flag, --self-test mixed with other arguments, a malformed changelog, a "
          "changelog deleted at the tip or in an intermediate commit, a remote whose push URL set "
          "is not exactly its one fetch URL, a pushInsteadOf rewrite over a configured remote or "
          "beside a URL remote, an insteadOf rewrite matching the raw or resolved URL, a remote "
          "name carrying whitespace, a legacy remotes or branches file present at all, a "
          "destination resolving to a configured remote's name, a destination unusable as one "
          "URL or outside the allowed shapes (an ext:: transport, an scp-like name, a relative "
          "path), a per-remote uploadpack or receivepack program, a core.sshCommand or "
          "ssh.variant setting, a GIT_SSH or GIT_SSH_COMMAND variable, a remote whose advertised "
          "refs cannot be read, a push publishing nothing the "
          "remote lacks, a failed push, a detached HEAD, an unborn HEAD, a non-repository working "
          "directory, a subdirectory --repo, an unknown --head, a digestless check pass, a check "
          "pass without exactly one resolved destination, an unexpected check exit) refuses with "
          "exit 2 and no remote ref")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
