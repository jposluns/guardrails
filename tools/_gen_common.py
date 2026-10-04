"""Shared helpers for the single-source generators (roadmap, changelog). Stdlib only.

Requires Python 3.11+ for tomllib; CI pins 3.14. run_all_checks.sh runs these locally.
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
    hostile tree; the ignore-control files git WOULD read with a plain blocking open are screened by
    the caller before the one query that reads them (see precheck_special_files)."""
    import subprocess
    try:
        proc = subprocess.run(["git", "-C", os.fspath(root), *args], stdin=subprocess.DEVNULL,
                              capture_output=True, timeout=60)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
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
    and never read as git-absent, which would skip the confirmation."""
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
    walk cannot certify. ACCEPTED: a regular file or directory; a DANGLING symlink (every open of it
    fails at once, so no read of it can block); a symlink to a REGULAR file, inside or outside the
    root (a plain read of a regular file does not block); a symlink to a directory INSIDE the root,
    whose resolved target subtree is walked ONCE even when that target is git-ignored (a visited set
    keyed by device and inode bounds link cycles), so linked-in contents, which a gate can reach
    through the link's own certified path, are checked rather than trusted; and a GIT-IGNORED symlink
    to a directory outside the root THAT SHADOWS NO TRACKED CONTENT (git ls-files under the link's
    own path answers empty), or a git-ignored unresolvable symlink, both accepted un-walked (a
    developer's .venv link shadows nothing; an unresolvable link cannot block, every open of it
    fails at once); a git-ignored out-of-root directory link that DOES shadow tracked content is
    refused by name, because a gate's fixed-path read of that tracked content would follow it out
    of the certified tree.

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
    DEFENCE IN DEPTH: this walk is one of two independent layers; every gate-reachable read of
    repository or configuration-declared content also goes through a NON-BLOCKING, fstat-checked
    reader (_walk.read_text_nonblocking, read_source_bytes, or that gate's own O_NONBLOCK or
    stat-before-open reader), which refuses a non-regular file by name instead of blocking on it,
    and the raw-read lint in tools/check_release_cut.py --self-test holds the gate entry modules
    and their in-tree imports to those readers, so neither layer alone carries the guarantee.
    Residual: the contents of an ACCEPTED (non-shadowing) ignored out-of-root directory link stay
    outside this walk, covered by the reader layer alone. Before git is asked for the
    ignore list, a first pass refuses any in-tree .gitignore that is, or resolves to, a special file,
    or is a symlink git cannot resolve other than a dangling one: git computes the ignore list by
    OPENING those files with a plain blocking read, so they are screened ahead of the one git query
    that reads them. When git cannot answer at all (an exported tree with no repository; any git
    failure), the walk runs with NO ignore filter: strictly more refusals, never fewer.

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
        return own_git_dir is not None and os.path.realpath(path) == own_git_dir
    try:
        real_root = os.path.realpath(root)
        for dirpath, dirnames, filenames in os.walk(root, onerror=_raise, followlinks=False):
            dirnames[:] = [d for d in dirnames
                           if not (d == ".git" and _own_git(os.path.join(dirpath, d)))]
            for name in dirnames + filenames:
                if name != ".gitignore":
                    continue
                path = os.path.join(dirpath, name)
                try:
                    checked = os.lstat(path).st_mode
                except FileNotFoundError:
                    continue
                if stat.S_ISLNK(checked):
                    try:
                        checked = os.stat(path).st_mode
                    except (FileNotFoundError, NotADirectoryError):
                        continue
                    except OSError as exc:
                        _refuse(path, "an ignore-control file that is a symlink git cannot resolve "
                                      "({}); git opens it to compute the ignore list".format(exc))
                for is_kind, kind in _SPECIAL_KINDS:
                    if is_kind(checked):
                        _refuse(path, "an ignore-control file that is (or resolves to) {}; git opens "
                                      "it with a plain blocking read to compute the ignore "
                                      "list".format(kind))
        ignored = _ignored_paths(root)
        tracked_state = []

        def _shadows_tracked(rel):
            # claude M2 (QA round 5): an ignored out-of-root directory link is allowed only when it
            # shadows NO tracked content; one planted over a tracked path redirects a gate's
            # fixed-path read of that content to a tree this walk cannot certify, so it is refused.
            # ONE cached read-only git query; when git cannot answer, the caller refuses (fail-closed).
            if not tracked_state:
                # QA round 6 (claude M3): a path shadows tracked content when it has entries in the
                # INDEX or in HEAD; asking the index alone lets a staged removal (git rm --cached)
                # hide a shadowing link, so both are unioned. An unborn HEAD (no commit yet)
                # contributes nothing; any other ls-tree failure is git-cannot-answer (refuse).
                out = _git_lines(root, ["ls-files", "-z"])
                head = _git_lines(root, ["ls-tree", "-r", "-z", "--name-only", "HEAD"])
                if head is None and _git_lines(root, ["rev-parse", "--verify", "--quiet",
                                                      "HEAD"]) is None:
                    head = b""
                if out is None or head is None:
                    tracked_state.append(None)
                else:
                    tracked_state.append(sorted(
                        {os.fsdecode(raw) for raw in out.split(b"\0") + head.split(b"\0") if raw}))
            tracked = tracked_state[0]
            if tracked is None:
                return None
            spec = rel.replace(os.sep, "/")
            return any(entry == spec or entry.startswith(spec + "/") for entry in tracked)

        visited = set()
        stack = [(os.fspath(root), "", False)]
        while stack:
            dirpath, relbase, under_ignored = stack.pop()
            try:
                dir_stat = os.stat(dirpath)
            except FileNotFoundError:
                continue
            dir_key = (dir_stat.st_dev, dir_stat.st_ino)
            if dir_key in visited:
                continue
            visited.add(dir_key)
            try:
                with os.scandir(dirpath) as scan:
                    entries = sorted(scan, key=lambda item: item.name)
            except FileNotFoundError:
                continue
            for item in entries:
                name = item.name
                if name == ".git" and _own_git(item.path):
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
                    if real == real_root or real.startswith(real_root + os.sep):
                        link_rel = os.path.relpath(real, real_root)
                        if link_rel == ".":
                            link_rel = ""
                        stack.append((real, link_rel, skipped))  # walked even when the TARGET
                        #            is ignored: a gate can reach it through the link's own path
                    elif not skipped:
                        _refuse(path, "a symlink to a directory outside the repository root (to "
                                      "{}); its contents cannot be certified from this "
                                      "root".format(real))
                    elif _shadows_tracked(rel) is not False:
                        _refuse(path, "a git-ignored symlink to a directory outside the repository "
                                      "root (to {}) that shadows tracked content; a gate's read of "
                                      "that tracked path would follow it to a tree this walk "
                                      "cannot certify".format(real))
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
    # filter. Exit 0 when the walk finds nothing to refuse; exit 2 on a refusal, a root
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
