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


def precheck_special_files(root):
    """Refuse a repository tree that holds a special file or a hostile symlink, before any tool reads
    from it (D-400-SPECIAL-FILE-PRECHECK). Walk root (os.walk with a raising onerror, never following a
    symlink for traversal, pruning every entry named .git), lstat every entry, and on the first FIFO,
    socket, block or character device print its path and exit 2 (the gates' fail-closed exit), as on an
    unlistable directory. A FIFO with no writer blocks any plain read of it forever, so one walk at the
    common entry point replaces routing every reader of the tree through the non-blocking reader one by
    one. Runs once per process per root (cached); returns root so a caller can wrap the expression that
    computes it.

    SYMLINK POLICY. Traversal never follows a symlink, but a plain reader downstream would, so every
    symlink the walk meets is judged by its target: os.stat (which follows the link but never opens, so
    it cannot block on a FIFO) classifies the target, and the link is refused by name when its target is
    a special file, is missing (dangling) or unresolvable (a loop), is a DIRECTORY (a symlinked directory
    is REFUSED rather than walked, the simpler of the two sound choices: this tree ships no directory
    symlinks, and refusal also closes the route to a target under the pruned .git), or resolves (via
    os.path.realpath) outside the repository root. A symlink to a regular file inside the root is
    accepted: its target is itself walked and checked from the root.

    This module is invoked directly (python3 -I -B tools/_gen_common.py --precheck) as the FIRST step of
    .github/workflows/quality.yml and the first line of tools/run_all_checks.sh, which stops on refusal
    (|| exit 2), so every CI gate and self-test after it runs on a tree this walk already checked.
    opf/tools/_containment.py carries a copy for the standalone OPF pack, which may not import this
    module (check_opf_standalone_closure), invoked the same way first in opf/tools/run_all_checks.sh. The
    two copies behave identically: check_manifest's self-test requires the two functions (docstrings
    aside) and their two module tables to stay identical, binds each name exactly once per module, and
    refuses a module-level attribute rebinding (an os.walk swap) in either copy. The per-tool calls
    (repo_root() on its .git-ancestor path, each gate's --root, the gates that find their root on their
    own, and the direct calls in .github/check_newtab_contract.py, tools/audit_reference.py and
    opf/tools/check_opf_init_contract.py) stay for local single-tool runs.

    Not covered, with reasons (residuals): a special file CREATED after this walk, and a symlink
    RETARGETED after this walk (a concurrent writer; one walk cannot close a race against a hostile
    co-writer of the tree); a vanished entry (skipped); everything under .git, which this walk prunes
    (git's own metadata is outside the tools' read set, but git itself reads it, so a FIFO planted at
    for example .git/config still hangs the gates that invoke git; removing such a plant needs the
    operator, not a gate); check_branch_root (it reads no working-tree file, only the object database
    through git); check_release_cut (its working-tree reads go through working_blob, a non-blocking
    no-follow open that refuses a non-regular file); the adopter tools whose --root is a product
    repository (doctor, migrate, pin) when run OUTSIDE the runner, on roots the runner never sees; the
    Path.cwd() fallback of repo_root() when no .git ancestor exists; and library modules with no entry
    point. The shared reader (read_source_bytes) stays on the corpus and manifest paths as defence in
    depth."""
    root = Path(root)
    key = os.path.abspath(root)
    if key in _PRECHECKED_ROOTS:
        return root

    def _raise(exc):
        raise exc

    def _refuse(path, why):
        print("error: {}: refused, {}; remove it; fail-closed".format(path, why), file=sys.stderr)
        raise SystemExit(2)
    real_root = os.path.realpath(root)
    try:
        for dirpath, dirnames, filenames in os.walk(root, onerror=_raise, followlinks=False):
            dirnames[:] = [d for d in dirnames if d != ".git"]
            for name in dirnames + filenames:
                if name == ".git":
                    continue
                path = os.path.join(dirpath, name)
                try:
                    mode = os.lstat(path).st_mode
                except FileNotFoundError:
                    continue
                for is_kind, kind in _SPECIAL_KINDS:
                    if is_kind(mode):
                        _refuse(path, "{} in the repository tree (a special file, not a regular "
                                      "file)".format(kind))
                if stat.S_ISLNK(mode):
                    try:
                        target_mode = os.stat(path).st_mode  # follows the link; stat never opens, so it
                    except FileNotFoundError:                # cannot block on a FIFO target
                        _refuse(path, "a dangling symlink (its target is missing)")
                    except OSError as exc:
                        _refuse(path, "a symlink whose target cannot be resolved ({})".format(exc))
                    for is_kind, kind in _SPECIAL_KINDS:
                        if is_kind(target_mode):
                            _refuse(path, "a symlink to {} (a special file, not a regular "
                                          "file)".format(kind))
                    if stat.S_ISDIR(target_mode):
                        _refuse(path, "a symlink to a directory (the walk never follows a symlink, so "
                                      "its contents would evade this check)")
                    real = os.path.realpath(path)
                    if real != real_root and not real.startswith(real_root + os.sep):
                        _refuse(path, "a symlink resolving outside the repository root (to "
                                      "{})".format(real))
    except OSError as exc:
        print("error: cannot walk the repository tree {} ({}); fail-closed".format(root, exc), file=sys.stderr)
        raise SystemExit(2)
    _PRECHECKED_ROOTS.add(key)
    return root


def repo_root(start=None):
    p = Path(start or __file__).resolve()
    for anc in [p, *p.parents]:
        if (anc / ".git").exists():
            return precheck_special_files(anc)
    return Path.cwd()


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
    non-regular entry, or any open/fstat/read error is SourceReadRefused; plain absence (ENOENT) stays a
    FileNotFoundError so callers that report a missing file keep doing so. `limit`, when given, reads at most
    that many bytes. On a regular file the result equals path.read_bytes() (or its first `limit` bytes)."""
    if not hasattr(os, "O_NOFOLLOW"):  # no O_NOFOLLOW (Windows): refuse a symlink by lstat before the open
        try:
            is_link = stat.S_ISLNK(os.lstat(path).st_mode)
        except FileNotFoundError:
            raise
        except OSError as exc:
            raise SourceReadRefused("{}: refused, cannot lstat ({})".format(path, exc)) from exc
        if is_link:
            raise SourceReadRefused("{}: refused, a symlink, not a regular file".format(path))
    flags = (os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
             | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_BINARY", 0))
    try:
        fd = os.open(path, flags)
    except FileNotFoundError:
        raise
    except OSError as exc:
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
    # Runner/CI entry (D-400-SPECIAL-FILE-PRECHECK): precheck the repository tree BEFORE any gate runs.
    # Usage: python3 -I -B tools/_gen_common.py --precheck
    # Exit 0 when the walk finds nothing to refuse; exit 2 (fail-closed) on a refusal, on an unknown
    # argument, or when no .git ancestor locates the repository root.
    if sys.argv[1:] != ["--precheck"]:
        print("usage: python3 -I -B tools/_gen_common.py --precheck", file=sys.stderr)
        raise SystemExit(2)
    _entry = Path(__file__).resolve()
    for _anc in _entry.parents:
        if (_anc / ".git").exists():
            precheck_special_files(_anc)
            print("PASS: special-file precheck found nothing to refuse in {}".format(_anc))
            raise SystemExit(0)
    print("error: no .git ancestor above {}; cannot locate the repository root; fail-closed".format(_entry),
          file=sys.stderr)
    raise SystemExit(2)
