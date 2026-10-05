"""Shared helpers for the single-source generators (roadmap, changelog). Stdlib only.

Requires Python 3.11+ for tomllib; CI pins 3.14. The hooks self-test (tools/selftest_aiqt_hooks.py)
needs 3.12+ for sys.monitoring. run_all_checks.sh runs these locally.
"""
import io
import os
import stat
import sys

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    sys.exit("error: the roadmap/changelog generators require Python 3.11+ (tomllib).")

from pathlib import Path
from urllib.parse import urlparse


def is_external_url(href):
    """True iff href is a link that leaves the aiqt.ai SITE, so it must carry a new-tab target/rel. A
    host equal to aiqt.ai or a subdomain of it is internal (same site); every other http(s) or
    protocol-relative destination is external. This is a SITE/host test, not an origin test: a different
    scheme or port on aiqt.ai (http://aiqt.ai, https://aiqt.ai:444) or a subdomain (https://sub.aiqt.ai)
    is deliberately internal, because a same-site link needs no new-tab safety.

    Classification follows how a browser parses an <a href>, so a naive-parser bypass cannot hide an
    off-site link: the HOSTNAME is parsed (aiqt.ai in a subdomain, userinfo, or path is not internal);
    backslashes fold to '/', ASCII tab/newline/CR are stripped, and any special-scheme slash count
    (http:/x, http:\\x, http:///x, http:x) normalizes to '//' before parsing, all per WHATWG; the scheme
    is matched case-insensitively; and it FAILS CLOSED (a malformed, hostless, or protocol-relative
    http(s) URL classifies EXTERNAL, never silently skipped). A relative path, fragment, mailto/tel, or
    other non-web href is not an off-site web link (False).

    Scope and residual: this classifies the project's own trusted, well-formed HTML to catch a forgotten
    target/rel on an off-site link; it is not a general validator of attacker-controlled hrefs. A host
    given as a raw IP or with a trailing dot is compared literally, and an internationalized host is not
    punycode-folded; each errs toward EXTERNAL, never toward a missed off-site link.

    The site host is read from AIQT_SITE_HOST at call time (default, and empty-value fallback, aiqt.ai;
    lowercased), so a non-aiqt.ai adopter can vendor the pack unpatched; behaviour is unchanged when it
    is unset or empty."""
    s = (href or "").strip().translate({9: None, 10: None, 13: None}).replace("\\", "/")
    low = s.lower()
    if low.startswith(("http:", "https:")):
        scheme, _, rest = s.partition(":")    # special scheme: any slash count (incl. 0) means authority follows (WHATWG)
        probe = scheme + "://" + rest.lstrip("/")
    elif low.startswith("//"):
        probe = "https:" + s                  # protocol-relative: give it a scheme so the authority parses
    else:
        return False                          # relative, fragment, mailto, tel, ...: not an off-site web link
    try:
        host = (urlparse(probe).hostname or "").lower()
    except ValueError:
        return True                           # a web URL we cannot parse: fail-closed to external
    if not host:
        return True                           # web URL with no resolvable host: fail-closed to external
    site = (os.environ.get("AIQT_SITE_HOST", "aiqt.ai") or "aiqt.ai").lower()  # empty/unset -> aiqt.ai
    return not (host == site or host.endswith("." + site))


_PRECHECKED_ROOTS = set()
_SPECIAL_KINDS = ((stat.S_ISFIFO, "a FIFO"), (stat.S_ISSOCK, "a socket"), (stat.S_ISBLK, "a block device"),
                  (stat.S_ISCHR, "a character device"))


def _git_lines(root, args):
    """One bounded, read-only git query under root, as raw bytes; None when git cannot answer (no git
    binary, not a repository, a timeout, any nonzero exit, or a root no process call accepts: an
    embedded NUL raises ValueError, not OSError, and is git-cannot-answer, after which the caller's
    own walk of that root fails closed by name). Callers treat None as git-absent and fall
    back on the fail-closed side: a fixed-location root, a walk with NO ignore filter (more refusals,
    never fewer). stdin is closed and the call is bounded, so this probe itself cannot be parked by a
    hostile tree; the ignore-control files and nested .git markers git WOULD read with a plain
    blocking open are screened by the caller before the one query that reads them (see
    precheck_special_files).

    CONFIGURATION. On a root the effective user owns, git runs with the caller's global and system
    configuration pinned away, so on that root the caller's core.fsmonitor does not run and the
    caller's global ignore file cannot hide a path from the walk. That holds ONLY on such a root. On
    a root another uid owns, or whose owner cannot be read, the caller's global and system
    configuration still apply, its core.fsmonitor and global ignore file included: a disclosed
    residual, which tools/selftest_git_fixture_env.py reports as config exposure for any self-test
    that reaches it. On an owned root, a refusal git reports as dubious ownership (its git dir
    belongs to another uid) is a named refusal, exit 2, never None, so the root cross-check is never
    silently skipped there."""
    import subprocess
    # QA round 7 (codex M4 = claude M2): every GIT_* variable is scrubbed from the child
    # environment, as check_portability and scrub_git_environment already do. An inherited
    # GIT_DIR / GIT_WORK_TREE / GIT_INDEX_FILE (or any other GIT_ control) can redirect
    # rev-parse, ls-files and the ignore list to a repository that is NOT the tree this walk
    # certifies (a nested or decoy repository above all), which would exempt a planted special
    # file from the walk or blind the tracked-content shadow test.
    env = dict((key, value) for key, value in os.environ.items() if not key.startswith("GIT_"))
    # D-400 fixture env: ONLY on a root the effective user owns are the caller's global and system
    # configuration pinned away (HOME and XDG_CONFIG_HOME at os.devnull, GIT_CONFIG_GLOBAL and
    # GIT_CONFIG_SYSTEM at os.devnull, GIT_CONFIG_NOSYSTEM=1; LC_ALL=C keeps git's refusal text
    # untranslated for the ownership test below). On such a root a caller's core.fsmonitor does not
    # run in this query and a caller's global ignore file cannot hide a path from the walk (fewer
    # ignored paths, so more refusals, never fewer). On a root another uid owns, or whose owner
    # cannot be read, the caller's configuration still applies, fsmonitor and global ignore file
    # included; the fixture-env observer reports that exposure. This stays a disclosed residual
    # rather than being narrowed: git refuses such a checkout as dubious ownership unless the
    # caller's own global or system configuration trusts it (the caller_env_without_git stance), and
    # trusting the root here instead (a command-scope safe.directory for this one root) would make
    # git honour the other owner's repository-local configuration (its core.fsmonitor run as this
    # user, its core.excludesFile hiding a path from the walk) for a caller who never trusted that
    # checkout, which is the one protection dubious ownership exists for, on the very root under
    # check; pinning without trust would instead make every such query git-absent while the gates'
    # own git reads, under the caller's configuration, still answer.
    try:
        owned = os.stat(root).st_uid == os.geteuid()
    except (OSError, ValueError, AttributeError):
        owned = False
    if owned:
        env.update(HOME=os.devnull, XDG_CONFIG_HOME=os.devnull, GIT_CONFIG_NOSYSTEM="1",
                   GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull, LC_ALL="C")
    try:
        proc = subprocess.run(["git", "-C", os.fspath(root), *args], stdin=subprocess.DEVNULL,
                              capture_output=True, timeout=60, env=env)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        # D-400 fixture env, QA round 2 (MINOR-1): on an owned root whose git dir another uid owns,
        # the pinned git refuses as dubious ownership (a caller's safe.directory is pinned away
        # here), while the gates' own git reads may still answer under the caller's configuration.
        # Reading that refusal as git-absent would silently skip the root cross-check, so it is
        # refused by name. Both message forms git has used are matched (2.35.2 and 2.36: "is owned
        # by someone else"; 2.37 on: "dubious ownership").
        if owned and (b"dubious ownership" in proc.stderr or b"is owned by someone else" in proc.stderr):
            print("error: git refuses {} as dubious ownership although this user owns it (its git "
                  "dir belongs to another user; a caller's safe.directory does not apply to this "
                  "query on an owned root); cannot confirm which tree to walk; fail-closed (give the "
                  "git dir to the owner of the root)".format(root), file=sys.stderr)
            raise SystemExit(2)
        return None
    return proc.stdout


def _git_toplevel(root):
    """os.path.realpath of `git rev-parse --show-toplevel` run AT root, or None when git cannot
    answer. This never searches upward for a .git marker itself: whether a .git entry is a real
    repository is git's judgement (git skips an invalid planted marker such as an empty tools/.git
    directory), and the answer is used only to CONFIRM a root already derived from a fixed location,
    never to choose one (D-400-SPECIAL-FILE-PRECHECK root derivation). An answer that is not one
    NUL-free absolute path (empty, several lines, an embedded NUL or other undecodable bytes, a
    relative path) is cannot-evaluate, exit 2, by name: never a raw ValueError out of the path call,
    and never read as git-absent, which would skip the confirmation. On a root the effective user
    owns, a refusal git reports as dubious ownership is exit 2 in _git_lines, never None. On a root
    another uid owns, git answers or refuses under the caller's own configuration (see _git_lines),
    and a refusal there is None: git-absent, as for every git read under that configuration."""
    out = _git_lines(root, ["rev-parse", "--show-toplevel"])
    if out is None:
        return None
    top = out[:-1] if out.endswith(b"\n") else out  # at most ONE trailing newline; a second is malformed
    try:
        if not top or b"\0" in top or b"\n" in top:
            raise ValueError("not exactly one NUL-free line")
        text = os.fsdecode(top)
        if not os.path.isabs(text):
            raise ValueError("not an absolute path")
        return os.path.realpath(text)
    except (OSError, ValueError) as exc:
        print("error: git rev-parse --show-toplevel at {} returned a malformed root {!r} ({}); cannot "
              "evaluate which tree to walk; fail-closed".format(root, out, exc), file=sys.stderr)
        raise SystemExit(2)


def _ignored_paths(root):
    """The root-relative paths git reports ignored under root (git ls-files --others --ignored
    --exclude-standard --directory, NUL-separated; a wholly ignored directory comes back as one
    entry), without trailing separators; None when git cannot answer, in which case the caller walks
    with NO ignore filter (strictly more refusals, never fewer)."""
    out = _git_lines(root, ["ls-files", "-z", "--others", "--ignored", "--exclude-standard",
                            "--directory"])
    if out is None:
        return None
    paths = set()
    for raw in out.split(b"\0"):
        if raw:
            paths.add(os.fsdecode(raw).rstrip("/").replace("/", os.sep))
    return frozenset(paths)


def _derive_precheck_root(entry):
    """The tree root for a --precheck run, from the entry module's own FIXED location, never from an
    upward search for a .git marker (a planted empty tools/.git or opf/tools/.git must not narrow the
    walk and certify the wrong tree). tools/_gen_common.py roots at the parent of its tools/
    directory. opf/tools/_containment.py roots two levels up (the authoring repository) when that tree
    is recognizably the authoring one (the opf/ directory name beside a tools/_gen_common.py sentinel;
    the probe stats and never opens, so a special file planted at the sentinel path only narrows the
    root to the opf/ tree, never blocks), else at the standalone opf/ tree itself. Whenever git can
    answer, the __main__ entries and repo_root() require _git_toplevel to CONFIRM the derivation; a
    disagreement is cannot-evaluate (exit 2), never a silently substituted root."""
    here = entry.parent
    if (entry.name == "_containment.py" and here.parent.name == "opf"
            and (here.parent.parent / "tools" / "_gen_common.py").is_file()):
        return here.parent.parent
    return here.parent


def precheck_special_files(root):
    """Refuse a tree that holds a BLOCKING-READ hazard, before any tool reads from it
    (D-400-SPECIAL-FILE-PRECHECK): a gate that opens a FIFO, socket or device, directly or through a
    symlink, waits forever (a FIFO with no writer above all). Plain errors (an unlistable directory,
    an unresolvable symlink) stay loud, exit 2. Runs once per process per root (cached); returns root
    so a caller can wrap the expression that computes it.

    REFUSED, by name, exit 2 (hazard-scoped, so a layout a developer legitimately has does not trip
    it): a FIFO, socket, block or character device; a symlink whose resolved target is one of those,
    EVEN when the link is git-ignored (a gate's read of a fixed path follows a link regardless of its
    ignore status; os.stat classifies the target without opening, so the classification itself cannot
    block); a non-ignored symlink that cannot be resolved for any reason OTHER than a missing target
    (a loop above all); and a non-ignored symlink to a directory OUTSIDE the root, whose contents this
    walk cannot certify. ACCEPTED (except on a TRACKED path while git can list the tracked paths,
    whose route is then held to the stricter tracked-path rule below: a tracked path through a link
    leaving the root is refused even when it dangles or reaches a regular file; without the list
    that stricter rule lapses, the residual named below): a regular file or directory; a DANGLING symlink (every open
    of it fails at once, so no read of it can block); a symlink to a REGULAR file, inside or outside
    the root (a plain read of a regular file does not block); a symlink to a directory INSIDE the root,
    whose contents are walked UNDER THE LINK'S OWN LOGICAL PATH with the link path's ignore
    classification (QA round 11 codex MAJOR: the ignore status and the tracked-shadow test follow
    the path AS GIT NAMES IT, never the target's spelling; substituting the target's path let a
    second link regain the ignored-only leniencies under that spelling, so a tracked path through
    two chained links reached an uncertified tree; the target's own subtree is independently
    walked under its own path and classification, which keeps QA round 9 claude MD1 and QA round
    10 claude MD2 covered in both directions), at most 64 link-walks per run (a directory-link
    cycle, a link to its own ancestor, or a heavier aliasing is refused by name, fail-closed,
    never walked forever or certified in part), so linked-in contents, which a gate can reach
    through the link's own certified path, are checked rather than trusted. QA round 12 (claude
    m3): an in-root directory link at a GIT-IGNORED logical path (the link ignored, or under an
    ignored directory) is NOT walked under that path and does not count toward the bound: every
    rule below that path is already the lenient ignored one, its target is walked under its own
    path and classification, and every tracked path through it is resolved by the tracked-path
    layer, so walking it again adds no refusal (a pnpm-style ignored node_modules with many
    in-root links passes). QA round 13 (claude MEDIUM 1): that skip applies ONLY when git can list
    the tracked paths; when it cannot (the index query ls-files fails, or HEAD's ls-tree fails on a
    HEAD not proven unborn, while the ignore query answers; QA round 14, below) the tracked-path
    layer does not run, so such a link is walked under its logical path and counted like any
    other, and a target the physical walk prunes (the repository's own git dir) is still
    examined. The bound still refuses more than 64 link-walks at non-ignored logical
    paths, a tracked workspace-link layout of that size included. And a GIT-IGNORED symlink
    to a directory outside the root THAT SHADOWS NO TRACKED CONTENT (git ls-files under the link's
    own path answers empty), or a git-ignored unresolvable symlink, both accepted un-walked (a
    developer's .venv link shadows nothing; an unresolvable link cannot block, every open of it
    fails at once); a git-ignored out-of-root directory link that DOES shadow tracked content is
    refused by name, because a gate's fixed-path read of that tracked content would follow it out
    of the certified tree. Independently of the walk, every TRACKED logical path (index or HEAD)
    is resolved COMPONENT BY COMPONENT, lstat and readlink only (nothing is opened, so the
    classification itself cannot block), and REFUSED BY THE LOGICAL PATH when any link step leaves
    the root, when resolution exceeds 40 link steps (a loop), or when the object it resolves to is
    a special file (QA round 11, codex MAJOR: the object a tracked path resolves to is always
    classified, however many directory links re-spell the route); a tracked path with a missing or
    dangling component stays accepted (every open of it fails at once) when no link step on its
    route has left the root. That tracked-path layer runs only when git can list the tracked
    paths. QA round 14 (codex MEDIUM 1 and 2): the list is unavailable when the index query
    (ls-files) fails, when HEAD's ls-tree fails and HEAD is not PROVEN unborn (a failed or timed-out
    query never stands for an unborn HEAD; only two queries that succeed prove it: symbolic-ref
    names one branch ref and for-each-ref on that name answers empty), or when either answer is
    not framed as git frames it (NUL-terminated records, no unterminated tail, each a decodable
    relative path with no empty, '.' or '..' component). Without the list the walk still refuses
    every special file a tracked path reaches (an ignored in-root directory link is then walked
    under its own path, QA round 13 above, and every symlink to a special file is refused, ignored
    or not), but the stricter route rule for tracked paths lapses (QA round 14, claude MINOR 1):
    a tracked path through a link leaving the root to a REGULAR file or a DANGLING target is then
    accepted, as for any other path. That residual cannot block (a regular file reads without
    blocking; every open of a dangling link fails at once), and it needs a failed ls-files, a
    failed ls-tree on a HEAD that names a commit, or a malformed answer from either.

    SCOPE: the whole tree under root, except the repository's OWN git dir (the one `git rev-parse
    --absolute-git-dir` names; git's own metadata is outside the tools' read set, but git itself
    reads it, so a FIFO planted at for example .git/config still hangs the gates that invoke git;
    removing such a plant needs the operator). Every OTHER entry named .git, at any depth, is
    DESCENDED like any directory (QA round 6, claude B1: a gate's tree scan reads into a nested
    tools/qa/.git like any other path, so a special file there is refused by name), and INCLUDING
    the contents of every git-ignored directory (QA round 5): the walk DESCENDS into ignored
    directories too (scandir and lstat only; no regular file is ever opened and no link is followed
    out of the root), so a special file is refused BY NAME wherever it sits. A socket or FIFO a
    developer keeps inside an ignored directory (a .venv, a build output) therefore now stops the
    checks, by name, which is the fail-closed direction; remove the special file to proceed. Git's
    ignore judgement governs only the SYMLINK leniencies (REFUSED/ACCEPTED above): an ignored
    unresolvable link is accepted, and an ignored out-of-root directory link is accepted un-walked
    only when it shadows NO TRACKED content (git ls-files under the link's own path answers empty; a
    developer's .venv link shadows nothing), because a gate's fixed-path read reaches uncertified
    content only through a link planted OVER a tracked path.
    DEFENCE IN DEPTH, PARTIAL IN THIS TREE (QA round 9, codex 2 = claude m1): a second layer of
    NON-BLOCKING, fstat-checked readers (_walk.read_text_nonblocking, read_source_bytes, or a
    gate's own O_NONBLOCK or stat-before-open reader) covers only the reads ALREADY CONVERTED to
    it, not every gate-reachable read: the raw-read lint in tools/check_release_cut.py --self-test
    still PINS unconverted pre-existing raw-read sites (_RAW_READ_PENDING, a pin that only
    shrinks; the follow-up change converts them), and an external-link COPY such as the
    standalone-closure copier (shutil.copytree in tools/check_opf_standalone_closure.py) reads
    through accepted links outside both this walk and those readers. Residual until that
    follow-up lands: the contents of an ACCEPTED (non-shadowing) ignored out-of-root directory
    link stay outside this walk, and a read of them through an unconverted raw-read site or an
    external-link copy can still block; only a read behind a converted reader refuses a
    non-regular file by name. Before git is asked for the
    ignore list, a first pass refuses any in-tree .gitignore that is, or resolves to, a special file,
    or is a symlink git cannot resolve other than a dangling one: git computes the ignore list by
    OPENING those files with a plain blocking read, so they are screened ahead of the one git query
    that reads them. The same pass screens every nested .git entry (QA round 11, claude m2): a
    .git that is, or resolves to, a special file, and a .git directory whose HEAD is, or resolves
    to, one, are refused by name BEFORE that query, because git's ignore probe opens a nested .git
    file and .git/HEAD with plain blocking reads while deciding whether the directory is a nested
    repository; without this screen the refusal still came, but only after the query's 60-second
    bound had expired. QA round 12 (codex MINOR = claude m2): git's probe also reads a nested
    .git directory's commondir, and FOLLOWS a nested gitfile ('gitdir: <path>') to read its
    target's HEAD and commondir (it reads nothing else there: no refs, no config, not the
    commondir's own target). So the same pass reads each nested gitfile itself (one bounded,
    O_NONBLOCK open, fstat-checked as a regular file, parsed as git parses it), REFUSES by name a
    gitfile whose target resolves outside both the tree and the repository's own git dir (this
    walk cannot certify a directory it does not cover), and screens commondir beside HEAD in every
    nested .git directory and every in-tree gitfile target. The ROOT .git entry is the
    repository's own marker and is not followed (a linked worktree's root gitfile names a git dir
    outside the tree by design; git has already read it to answer rev-parse, and its metadata is
    the operator-domain residual SCOPE names). When git cannot answer at all (an exported tree
    with no repository; any git failure), the walk runs with NO ignore filter: strictly more
    refusals, never fewer.

    ENTRY POINTS. This module is invoked directly (--precheck) as the FIRST post-checkout run step of
    every job in every CI workflow (tools/check_ci_parity.py holds every job to that order) and ahead
    of every gate in each runner, which stops on refusal (exit 2), so every gate and self-test after
    it acts on a checked tree. The root comes from _derive_precheck_root (a fixed location, never an
    upward .git search); the INVOKED location (os.path.abspath, no link resolution) and the RESOLVED
    module file must derive the same real root, so a tools/ or opf/tools/ directory replaced by a
    symlink cannot move the walk out of the invoked tree; and the root must be confirmed by
    _git_toplevel whenever git can answer. BOOTSTRAP: the
    runners and the CI steps first test, in shell ([ -f ] && [ ! -h ]), that the precheck module
    itself is a regular non-symlink file, because python3 would block LOADING a FIFO planted at the
    script path before any line of this code could run. Residuals that remain OUTSIDE the precheck:
    the interpreter binary and the runner script itself (both are read before the bootstrap line can
    run); everything under .git and the ignored paths, both as above; a special file CREATED, or a
    symlink RETARGETED, after this walk (one walk cannot close a race against a hostile co-writer of
    the tree); a vanished entry (skipped); and the per-tool residuals the tools' own docstrings
    carry. The shared reader (read_source_bytes) stays on the corpus and manifest paths as defence in
    depth.

    COPIES. tools/_gen_common.py and opf/tools/_containment.py each carry this section (the standalone
    OPF pack may not import tools/: check_opf_standalone_closure). tools/check_manifest.py's self-test
    compares the two copies' precheck sections (these helpers, the two module tables and the __main__
    block) as parsed trees, and holds each module to shape rules that keep the compared definitions
    the operative ones; its docstring names the mutation classes those rules do and do not catch."""
    root = Path(root)
    key = os.path.abspath(root)
    if key in _PRECHECKED_ROOTS:
        return root

    def _raise(exc):
        raise exc

    def _refuse(path, why):
        print("error: {}: refused, {}; remove it; fail-closed".format(path, why), file=sys.stderr)
        raise SystemExit(2)
    # QA round 6 (claude B1): only the repository's OWN git dir (the one `git rev-parse
    # --absolute-git-dir` names) is outside the walk; every OTHER entry named .git, at any depth,
    # is an ordinary directory a gate's tree scan can read into, so it is descended (lstat only)
    # and a special file inside it is refused by name. When git cannot answer, or answers
    # malformed, NOTHING named .git is skipped: strictly more refusals, never fewer.
    own_git_dir = None
    _dir_out = _git_lines(root, ["rev-parse", "--absolute-git-dir"])
    if _dir_out is not None:
        _dir_top = _dir_out[:-1] if _dir_out.endswith(b"\n") else _dir_out
        if _dir_top and b"\0" not in _dir_top and b"\n" not in _dir_top:
            try:
                _dir_text = os.fsdecode(_dir_top)
                if os.path.isabs(_dir_text):
                    own_git_dir = os.path.realpath(_dir_text)
            except (OSError, ValueError):
                own_git_dir = None

    def _own_git(path):
        # QA round 7 (claude m-b): the exemption is applied ONLY at the repository root (the
        # canonical <root>/.git location; both call sites also require that position). A root
        # .git FILE (a gitfile with gitdir: tools/qa/.git) must not exempt an IN-TREE nested
        # directory from the walk: that directory is descended with lstat like any other and a
        # special file inside it is refused by name (git's own reads of such a layout are
        # bounded by the _git_lines timeout).
        return own_git_dir is not None and os.path.realpath(path) == own_git_dir
    def _screen_pre_git(path, what):
        # lstat, then stat for a symlink: classification without ever opening, so this screen
        # itself cannot block. Refuses, by name, an entry that is (or resolves to) a special
        # file, and a non-dangling symlink that cannot be resolved; returns the resolved mode,
        # or None when the entry is missing or dangles (git's own open of it fails at once).
        try:
            checked = os.lstat(path).st_mode
        except FileNotFoundError:
            return None
        if stat.S_ISLNK(checked):
            try:
                checked = os.stat(path).st_mode
            except (FileNotFoundError, NotADirectoryError):
                return None
            except OSError as exc:
                _refuse(path, "{} that is a symlink git cannot resolve ({}); git opens it "
                              "with a plain blocking read".format(what, exc))
        for is_kind, kind in _SPECIAL_KINDS:
            if is_kind(checked):
                _refuse(path, "{} that is (or resolves to) {}; git opens it with a plain "
                              "blocking read".format(what, kind))
        return checked

    def _gitfile_target(path, dirpath):
        # QA round 12 (codex MINOR = claude m2): the real path of the git dir a nested gitfile
        # names, parsed as git's read_gitfile parses it ('gitdir: ' first, trailing CR and LF
        # dropped, the path ending at a NUL, a relative path joined to the gitfile's own
        # directory), or None when git would not follow it. One open, O_NONBLOCK and fstat-checked
        # as a regular file, so a raced swap to a FIFO cannot park it. A target outside both the
        # tree and the repository's own git dir is refused by name.
        flags = (os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0)
                 | getattr(os, "O_BINARY", 0))
        try:
            fd = os.open(path, flags)
        except FileNotFoundError:
            return None
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                _refuse(path, "a nested .git marker that stopped being a regular file while it "
                              "was screened")
            data = b""
            while len(data) <= 1 << 20:
                chunk = os.read(fd, 1 << 16)
                if not chunk:
                    break
                data += chunk
        finally:
            os.close(fd)
        if len(data) > 1 << 20:
            _refuse(path, "a nested gitfile larger than 1 MiB, which this screen does not parse")
        if not data.startswith(b"gitdir: "):
            return None
        body = data.rstrip(b"\r\n")
        if len(body) < 9:
            return None
        target = os.path.realpath(os.path.join(dirpath,
                                               os.fsdecode(body[8:].split(b"\0", 1)[0])))
        for allowed in (real_root, own_git_dir):
            if allowed is not None and (target == allowed
                                        or target.startswith(allowed + os.sep)):
                return target if os.path.isdir(target) else None
        _refuse(path, "a nested gitfile naming a git dir outside the repository tree (to {}); "
                      "git's ignore query reads that directory's HEAD and commondir with plain "
                      "blocking opens, and this walk cannot certify a tree it does not "
                      "cover".format(target))

    try:
        real_root = os.path.realpath(root)
        for dirpath, dirnames, filenames in os.walk(root, onerror=_raise, followlinks=False):
            dirnames[:] = [d for d in dirnames
                           if not (d == ".git" and os.path.realpath(dirpath) == real_root
                                   and _own_git(os.path.join(dirpath, d)))]
            for name in dirnames + filenames:
                if name == ".gitignore":
                    _screen_pre_git(os.path.join(dirpath, name),
                                    "an ignore-control file (git reads it to compute the "
                                    "ignore list)")
                elif name == ".git":
                    # QA round 11 (claude m2): git's ignore query (ls-files --others) probes
                    # every nested .git while deciding whether its directory is a nested
                    # repository: it OPENS a .git file (a gitfile) and a .git/HEAD with plain
                    # blocking reads, so both are screened BEFORE the one git query that reads
                    # them. Without this screen the walk below still refused such a plant, but
                    # only after that query's 60-second bound had expired. The repository's
                    # OWN root .git dir is already pruned above (git's reads of its own
                    # metadata are the operator-domain residual the SCOPE section names).
                    path = os.path.join(dirpath, name)
                    mode = _screen_pre_git(path, "a nested .git marker (git reads it while "
                                                 "probing for a nested repository)")
                    gitdir = None
                    if mode is not None and stat.S_ISDIR(mode):
                        gitdir = path
                    elif (mode is not None and stat.S_ISREG(mode)
                          and os.path.realpath(dirpath) != real_root):
                        # QA round 12 (codex MINOR = claude m2): git follows a nested gitfile
                        # to its target and reads that target's HEAD and commondir.
                        gitdir = _gitfile_target(path, dirpath)
                    if gitdir is not None:
                        for probe in ("HEAD", "commondir"):
                            _screen_pre_git(os.path.join(gitdir, probe),
                                            "a nested git dir's {} (git reads it while probing "
                                            "for a nested repository)".format(probe))
        ignored = _ignored_paths(root)
        tracked_state = []

        def _tracked():
            # QA round 6 (claude M3): a path shadows tracked content when it has entries in the
            # INDEX or in HEAD; asking the index alone lets a staged removal (git rm --cached)
            # hide a shadowing link, so both are unioned. An unborn HEAD (no commit yet)
            # contributes nothing; any other ls-tree failure is git-cannot-answer (None), on
            # which every caller fails closed. QA round 14 (codex MEDIUM 1): _git_lines answers
            # None for a timeout or any error as well as for a missing HEAD, so a failed query
            # never stands for an unborn HEAD. HEAD is unborn only when two queries that SUCCEED
            # prove it: symbolic-ref names exactly one branch ref (a detached HEAD always names a
            # commit), and for-each-ref on that name answers empty (a ref whose commit or tree
            # object is missing is still listed). QA round 14 (codex MEDIUM 2): each answer is a
            # list only when framed as git frames it (NUL-terminated records, no unterminated
            # tail, each a decodable relative path with no empty, '.' or '..' component);
            # anything else is git-cannot-answer too. One cached read-only git query set per walk.
            def _records(raw):
                if raw is None or (raw and not raw.endswith(b"\0")):
                    return None
                paths = []
                for record in (raw[:-1].split(b"\0") if raw else ()):
                    try:
                        text = os.fsdecode(record)
                    except ValueError:
                        return None
                    if (text.startswith("/")
                            or any(part in ("", ".", "..") for part in text.split("/"))):
                        return None
                    paths.append(text)
                return paths

            if not tracked_state:
                out = _records(_git_lines(root, ["ls-files", "-z"]))
                head = _git_lines(root, ["ls-tree", "-r", "-z", "--name-only", "HEAD"])
                if head is None:
                    ref = _git_lines(root, ["symbolic-ref", "-q", "HEAD"])
                    name = ref[:-1] if ref is not None and ref.endswith(b"\n") else b""
                    if (name.startswith(b"refs/heads/")
                            and not any(bad in name for bad in (b"\0", b"\n", b"*", b"?", b"[",
                                                                b"\\"))
                            and _git_lines(root, ["for-each-ref", "--format=%(refname)",
                                                  os.fsdecode(name)]) == b""):
                        head = b""
                head = _records(head)
                if out is None or head is None:
                    tracked_state.append(None)
                else:
                    tracked_state.append(sorted(set(out) | set(head)))
            return tracked_state[0]

        def _shadows_tracked(rel):
            # claude M2 (QA round 5): an ignored out-of-root directory link is allowed only when it
            # shadows NO tracked content; one planted over a tracked path redirects a gate's
            # fixed-path read of that content to a tree this walk cannot certify, so it is refused.
            # When git cannot answer (None), the caller refuses (fail-closed).
            tracked = _tracked()
            if tracked is None:
                return None
            spec = rel.replace(os.sep, "/")
            return any(entry == spec or entry.startswith(spec + "/") for entry in tracked)

        def _classify_tracked(spec):
            # QA round 11 (codex MAJOR): resolve root/<spec> COMPONENT BY COMPONENT with lstat
            # and readlink only (nothing is opened, so the classification itself cannot block).
            # Returns the mode of the object the tracked path resolves to, or None when a
            # component is missing or dangles (every open of that path fails at once). Refuses,
            # BY THE LOGICAL PATH, a link step that leaves the root (a '..' above it, or an
            # absolute target outside it, compared on the RAW target string: a lexically
            # normalized '..' can disagree with the kernel when it follows a link, so '..' is
            # only ever applied to the physical, already-resolved prefix), a resolution of more
            # than 40 link steps (a loop), and a component no lstat or readlink call can
            # classify.
            logical = os.path.join(os.fspath(root), spec.replace("/", os.sep))
            pending = list(reversed(spec.split("/")))
            cur = real_root
            hops = 0
            mode = None
            while pending:
                comp = pending.pop()
                if comp in ("", os.curdir):
                    continue
                if comp == os.pardir:
                    step = os.path.dirname(cur)
                    if step != real_root and not step.startswith(real_root + os.sep):
                        _refuse(logical, "a tracked path that resolves through a link leaving "
                                         "the repository root (a '..' step above {}); what a "
                                         "gate's read of it reaches cannot be certified from "
                                         "this root".format(cur))
                    cur, mode = step, None
                    continue
                candidate = os.path.join(cur, comp)
                try:
                    mode = os.lstat(candidate).st_mode
                except (FileNotFoundError, NotADirectoryError):
                    return None
                except OSError as exc:
                    _refuse(logical, "a tracked path with a component this walk cannot "
                                     "classify ({})".format(exc))
                if stat.S_ISLNK(mode):
                    hops += 1
                    if hops > 40:
                        _refuse(logical, "a tracked path whose resolution takes more than 40 "
                                         "link steps (a link loop); what it resolves to "
                                         "cannot be certified")
                    try:
                        target = os.readlink(candidate)
                    except OSError as exc:
                        _refuse(logical, "a tracked path through a symlink this walk cannot "
                                         "read ({})".format(exc))
                    if os.path.isabs(target):
                        if target != real_root and not target.startswith(real_root + os.sep):
                            _refuse(logical, "a tracked path that resolves through a link "
                                             "leaving the repository root (to {}); what a "
                                             "gate's read of it reaches cannot be certified "
                                             "from this root".format(target))
                        pending.extend(reversed(target[len(real_root):].split(os.sep)))
                        cur = real_root
                    else:
                        pending.extend(reversed(target.split(os.sep)))
                    mode = None
                    continue
                cur = candidate
            if mode is None:
                try:
                    mode = os.lstat(cur).st_mode
                except OSError as exc:
                    _refuse(logical, "a tracked path with a component this walk cannot "
                                     "classify ({})".format(exc))
            return mode

        link_walks = 0
        stack = [(os.fspath(root), "", False)]
        while stack:
            dirpath, relbase, under_ignored = stack.pop()
            try:
                with os.scandir(dirpath) as scan:
                    entries = sorted(scan, key=lambda item: item.name)
            except FileNotFoundError:
                continue
            for item in entries:
                name = item.name
                if name == ".git" and relbase == "" and _own_git(item.path):
                    continue
                rel = os.path.join(relbase, name) if relbase else name
                skipped = under_ignored or (ignored is not None and rel in ignored)
                path = item.path
                try:
                    mode = item.stat(follow_symlinks=False).st_mode
                except FileNotFoundError:
                    continue
                if not stat.S_ISLNK(mode):
                    # Ignored or not, a special file is refused and a directory is descended: the
                    # walk opens nothing, so descending an ignored directory costs lstat calls only,
                    # and a special file is a blocking-read hazard wherever a reader meets it.
                    for is_kind, kind in _SPECIAL_KINDS:
                        if is_kind(mode):
                            _refuse(path, "{} in the repository tree (a special file, not a regular "
                                          "file)".format(kind))
                    if stat.S_ISDIR(mode):
                        stack.append((path, rel, skipped))
                    continue
                # QA round 7 (claude B1): the INTERPRETER itself reads __pycache__/<module>.pyc
                # with a plain blocking open when a gate imports an in-tree module (-B stops only
                # the WRITES), and that read sits outside every gate reader, so a SYMLINK named
                # __pycache__ is refused by NAME, ignored or not, wherever it sits and whatever it
                # resolves to (a real __pycache__ directory stays walked like any directory, and a
                # special file inside it is already refused by name; the second, independent layer
                # is the runners' and workflows' -X pycache_prefix redirection).
                if name == "__pycache__":
                    _refuse(path, "a symlink named __pycache__ (ignored or not); the Python "
                                  "interpreter itself opens __pycache__/*.pyc with a plain "
                                  "blocking read when a gate imports a module, so its target "
                                  "can never be certified by this walk")
                # A symlink is classified by its TARGET even when the link itself is git-ignored: a
                # gate's read of a fixed path follows a link regardless of its ignore status.
                try:
                    target_mode = os.stat(path).st_mode  # follows the link; stat never opens, so
                except FileNotFoundError:                # it cannot block on a FIFO target
                    continue  # dangling: every open of it fails at once; no read of it can block
                except NotADirectoryError:
                    continue  # dangling through a non-directory component: open fails at once too
                except OSError as exc:
                    if skipped:
                        continue  # ignored and unresolvable: every open of it fails at once too
                    _refuse(path, "a symlink whose target cannot be resolved ({}); no reader of "
                                  "it can be classified".format(exc))
                for is_kind, kind in _SPECIAL_KINDS:
                    if is_kind(target_mode):
                        _refuse(path, "a symlink to {} (a special file, not a regular "
                                      "file)".format(kind))
                if stat.S_ISDIR(target_mode):
                    real = os.path.realpath(path)
                    if (skipped and (real == real_root or real.startswith(real_root + os.sep))
                            and _tracked() is not None):
                        # QA round 12 (claude m3): an in-root directory link at an IGNORED
                        # logical path is not walked under that path, and so not counted: below
                        # it every rule is already the lenient ignored one, its target is walked
                        # under its own path and classification, and every tracked path through
                        # it is resolved by the tracked-path layer after this walk. QA round 13
                        # (claude MEDIUM 1): that layer runs only when git can list the tracked
                        # paths, so the skip applies only then; without the list (ls-files
                        # fails, or ls-tree fails on a HEAD not proven unborn, or an answer is
                        # malformed, while the ignore query answers) the link is walked and
                        # counted below like any other, so a target the physical walk prunes
                        # (the repository's own git dir) is still examined.
                        continue
                    if real == real_root or real.startswith(real_root + os.sep):
                        # QA round 11 (codex MAJOR): descend through an in-root directory link
                        # by the link's OWN LOGICAL path and ignore classification, never the
                        # target's spelling: substituting the target's path (round 10) let a
                        # second link, ignored only under that spelling, regain the
                        # ignored-only leniencies while the shadow test saw only the
                        # target-relative rel, so a tracked path through two chained links
                        # reached an uncertified tree. The target's own subtree is still
                        # walked under its own path and classification by the physical
                        # descent, which keeps QA round 9 claude MD1 and QA round 10 claude
                        # MD2 covered in both directions. Link-walks are COUNTED, never
                        # deduplicated (a dedup keyed on the target identity is exactly what
                        # lost the logical spelling): a directory-link cycle (a link to its
                        # own ancestor above all) or a tree aliased through more than 64 links
                        # is refused by name, fail-closed.
                        link_walks += 1
                        if link_walks > 64:
                            _refuse(path, "a symlinked directory traversal past this walk's "
                                          "bound of 64 (a directory-link cycle, a link to its "
                                          "own ancestor, or a heavily aliased tree); the "
                                          "logical paths through it cannot all be certified")
                        stack.append((path, rel, skipped))
                    elif not skipped:
                        _refuse(path, "a symlink to a directory outside the repository root (to "
                                      "{}); its contents cannot be certified from this "
                                      "root".format(real))
                    elif _shadows_tracked(rel) is not False:
                        _refuse(path, "a git-ignored symlink to a directory outside the repository "
                                      "root (to {}) that shadows tracked content; a gate's read of "
                                      "that tracked path would follow it to a tree this walk "
                                      "cannot certify".format(real))
        # QA round 11 (codex MAJOR), the independent layer the walk's leniencies cannot blunt:
        # every TRACKED logical path is resolved component by component and the OBJECT it
        # resolves to is classified, however many directory links re-spell the route. When git
        # cannot list the tracked paths (ls-files fails, ls-tree fails on a HEAD not proven
        # unborn, or either answer is malformed; QA round 14) this layer does not run. The walk
        # above still refuses every special file a tracked path can reach: an ignored in-root
        # directory link is then walked under its own path (the round-12 skip needs the tracked
        # list; QA round 13, claude MEDIUM 1), every symlink to a special file is refused,
        # ignored or not, a non-ignored out-of-root directory link is refused outright, and an
        # ignored one is refused as a possible shadow of tracked content. What lapses is the
        # stricter route rule for tracked paths (QA round 14, claude MINOR 1): a tracked path
        # through a link leaving the root to a regular file or a dangling target is accepted,
        # the residual the docstring names (neither can block). The ignore filter is independent
        # of the tracked list: ls-files or ls-tree can fail while the ignore query answers.
        for spec in (_tracked() or ()):
            final_mode = _classify_tracked(spec)
            if final_mode is None:
                continue
            for is_kind, kind in _SPECIAL_KINDS:
                if is_kind(final_mode):
                    _refuse(os.path.join(os.fspath(root), spec.replace("/", os.sep)),
                            "a tracked path that resolves, through the links on its route, to "
                            "{} (a special file, not a regular file)".format(kind))
    except (OSError, ValueError) as exc:  # ValueError: a root path no path call accepts (an embedded NUL)
        print("error: cannot walk the repository tree {} ({}); fail-closed (an unreadable or "
              "unwalkable entry, even inside a git-ignored directory, stops the checks by design; "
              "make it readable or remove it)".format(root, exc), file=sys.stderr)
        raise SystemExit(2)
    _PRECHECKED_ROOTS.add(key)
    return root


def repo_root():
    """The repository root for the tools/ gates: the parent of this module's own tools/ directory
    (its FIXED location), confirmed by `git rev-parse --show-toplevel` whenever git can answer; a
    disagreement is cannot-evaluate, exit 2 (never a silently substituted root), and without git (an
    exported tree) the fixed location stands alone. Never an upward search for a .git marker: a
    planted empty tools/.git must not narrow the root (D-400-SPECIAL-FILE-PRECHECK). The returned
    root has passed precheck_special_files."""
    root = Path(__file__).resolve().parents[1]
    confirmed = _git_toplevel(root)
    if confirmed is not None and confirmed != os.path.realpath(root):
        print("error: the repository root derived from {} is {}, but git rev-parse --show-toplevel "
              "says {}; cannot evaluate which tree to walk; fail-closed".format(__file__, root,
                                                                                confirmed),
              file=sys.stderr)
        raise SystemExit(2)
    return precheck_special_files(root)


class SourceReadRefused(OSError):
    """A tracked source the shared reader refuses: a symlink, a non-regular entry (a FIFO, a device, a
    directory), or an open/fstat/read error other than plain absence. An OSError subclass, so every caller's
    existing fail-closed OSError arm (exit 2 for the gates) maps it with no new handler
    (check-fails-closed-on-unreadable)."""


def read_source_bytes(path, limit=None):
    """Read a tracked source file (a rule-corpus source, a manifest SOURCES member, a TOML record) as raw
    bytes through ONE descriptor: open O_RDONLY|O_NOFOLLOW|O_NONBLOCK, fstat the OPENED descriptor and
    require S_ISREG, then read. O_NONBLOCK makes the open of a FIFO with no writer (or a slow device) return
    at once, so the S_ISREG refusal is reached instead of the read blocking forever (F-CORPUS-FIFO-HANG); on a
    regular file it is a no-op. O_NOFOLLOW refuses a symlink final component (ELOOP). A symlink, a
    non-regular entry, any open/fstat/read error, or a path no path call accepts (an embedded NUL, which
    os.open rejects with ValueError, not OSError) is SourceReadRefused; plain absence (ENOENT) stays a
    FileNotFoundError so callers that report a missing file keep doing so. `limit`, when given, reads at most
    that many bytes. On a regular file the result equals path.read_bytes() (or its first `limit` bytes)."""
    if not hasattr(os, "O_NOFOLLOW"):  # no O_NOFOLLOW (Windows): refuse a symlink by lstat before the open
        try:
            is_link = stat.S_ISLNK(os.lstat(path).st_mode)
        except FileNotFoundError:
            raise
        except (OSError, ValueError) as exc:
            raise SourceReadRefused("{}: refused, cannot lstat ({})".format(path, exc)) from exc
        if is_link:
            raise SourceReadRefused("{}: refused, a symlink, not a regular file".format(path))
    flags = (os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
             | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_BINARY", 0))
    try:
        fd = os.open(path, flags)
    except FileNotFoundError:
        raise
    except (OSError, ValueError) as exc:
        raise SourceReadRefused("{}: refused, cannot open as a regular non-symlink file ({})".format(
            path, exc)) from exc
    try:
        try:
            mode = os.fstat(fd).st_mode
        except OSError as exc:
            raise SourceReadRefused("{}: refused, cannot fstat ({})".format(path, exc)) from exc
        if not stat.S_ISREG(mode):
            raise SourceReadRefused("{}: refused, not a regular file (a FIFO, device, or directory)".format(
                path))
        chunks, remaining = [], limit
        try:
            while remaining is None or remaining > 0:
                chunk = os.read(fd, 1 << 20 if remaining is None else min(remaining, 1 << 20))
                if not chunk:
                    break
                chunks.append(chunk)
                if remaining is not None:
                    remaining -= len(chunk)
        except OSError as exc:
            raise SourceReadRefused("{}: refused, read error ({})".format(path, exc)) from exc
        return b"".join(chunks)
    finally:
        os.close(fd)


def read_source_text(path, encoding="utf-8"):
    """read_source_bytes decoded exactly as path.read_text(encoding=...) decodes (strict errors, universal
    newlines), so a regular file reads identically; a non-UTF-8 source still raises UnicodeDecodeError."""
    return io.TextIOWrapper(io.BytesIO(read_source_bytes(path)), encoding=encoding).read()


def load_toml(path):
    # read_source_bytes, not open(): a FIFO at a TOML record must be refused, never block the gate.
    raw = read_source_bytes(path)
    with io.BytesIO(raw) as handle:
        try:
            return tomllib.load(handle)
        except RecursionError as exc:
            # Callers fail closed on the ValueError family by contract, but tomllib raises RecursionError (a
            # RuntimeError) on a deeply nested array or inline table; map it into that family here, at the
            # parse locus, so no caller's ValueError handler is bypassed (F-TOML-BARE-VALUEERROR-CLASS).
            raise ValueError("{}: TOML nesting is too deep to parse ({})".format(path, exc)) from exc


def _markers(name):
    return ("<!-- {}:BEGIN (generated) -->".format(name),
            "<!-- {}:END -->".format(name))


def replace_block(html_text, name, inner):
    """Replace the content between the named markers, keeping the markers."""
    begin, end = _markers(name)
    i = html_text.find(begin)
    j = html_text.find(end)
    if i == -1 or j == -1 or j < i:
        raise ValueError("markers for {} not found in the page".format(name))
    return html_text[:i] + begin + "\n" + inner + "\n      " + html_text[j:]


def reconcile(path, new_text, check):
    """Write new_text to path, or (check mode) return True if it would change. Fail-closed: an OSError
    reading the current file or writing the new one exits 2 with a message rather than a raw traceback,
    so a drift gate or a regeneration never dies unhandled on a read-only fs, a permission error, or a
    full disk. An invalid-UTF-8 (non-decodable) existing target is mapped to the same exit 2: read_text
    decodes as UTF-8, so a corrupt target raises UnicodeDecodeError, and that is fail-closed too rather
    than a raw traceback. The current target is read through read_source_text, so a target that is a
    symlink (even a dangling one) or not a regular file (a FIFO, a directory) is refused, exit 2, in both
    modes: check never compares through a link and write never writes through one."""
    path = Path(path)
    try:
        try:
            current = read_source_text(path)
        except FileNotFoundError:
            current = None
        if check:
            return current != new_text
        path.write_text(new_text, encoding="utf-8")
        return False
    except (OSError, UnicodeError) as exc:
        print("error: cannot read or write {} ({}); fail-closed".format(path, exc), file=sys.stderr)
        raise SystemExit(2)


if __name__ == "__main__":
    # Runner/CI entry (D-400-SPECIAL-FILE-PRECHECK): precheck the tree BEFORE any gate runs.
    # Usage: python3 -I -B <this module> --precheck
    # The root comes from this module's own FIXED location (_derive_precheck_root), never from an
    # upward .git search a planted marker could narrow. The INVOKED location (os.path.abspath, no
    # link resolution) and the RESOLVED module file must derive the same real root: a tools/ or
    # opf/tools/ directory replaced by a symlink would otherwise move the walk to the link target
    # and certify the wrong tree while the runner's gates read the invoked one. Whenever git can
    # answer, rev-parse must CONFIRM the derivation, and a disagreement is cannot-evaluate. Without
    # git (an exported tree) the fixed location stands alone and the walk runs with no ignore
    # filter. On a root this user owns, a dubious-ownership refusal is exit 2 (_git_lines), never
    # read as git-absent. Exit 0 when the walk finds nothing to refuse; exit 2 on a refusal, a root
    # disagreement, or an unknown argument.
    if sys.argv[1:] != ["--precheck"]:
        print("usage: python3 -I -B {} --precheck".format(sys.argv[0] or __file__), file=sys.stderr)
        raise SystemExit(2)
    _entry = Path(os.path.abspath(__file__))
    _root = _derive_precheck_root(_entry)
    _resolved_root = os.path.realpath(_derive_precheck_root(Path(os.path.realpath(__file__))))
    if os.path.realpath(_root) != _resolved_root:
        print("error: the tree root derived from the invoked path {} is {}, but the module file "
              "resolves into {}; a symlinked script directory cannot certify the invoked tree; "
              "fail-closed".format(_entry, _root, _resolved_root), file=sys.stderr)
        raise SystemExit(2)
    _confirmed = _git_toplevel(_root)
    if _confirmed is not None and _confirmed != os.path.realpath(_root):
        print("error: the tree root derived from {} is {}, but git rev-parse --show-toplevel says "
              "{}; cannot evaluate which tree to walk; fail-closed".format(_entry, _root, _confirmed),
              file=sys.stderr)
        raise SystemExit(2)
    precheck_special_files(_root)
    print("PASS: special-file precheck found nothing to refuse in {}".format(_root))
    raise SystemExit(0)
