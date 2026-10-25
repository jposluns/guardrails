"""Shared helpers for the single-source generators (roadmap, changelog). Stdlib only, plus _standards.py beside
it for the generated-file directory probe (target_dir_present).

Requires Python 3.14 or newer, the floor in .aiqt/core/python-floor.toml, which CI pins.
run_all_checks.sh runs these locally.
"""
import os
import sys

try:
    import tomllib
except ModuleNotFoundError:
    import os
    if tuple(sys.version_info[:2]) < (3, 14):
        # Reached only through an importer that carries no floor guard yet (every guarded
        # entrypoint refuses an older Python that can start it first; one that cannot start
        # it fails with Python's own error before reaching here): the version is the
        # problem, so say so. The message is a literal, as the fallback grammar in
        # tools/check_python_floor.py requires (it admits no call), so it names no version.
        try:
            sys.stderr.write(
                "error: the roadmap/changelog generators require Python 3.14 or newer, and this "
                "interpreter is older. Nothing was run (cannot evaluate).\n")
            sys.stderr.flush()
        except BaseException:
            pass
    else:  # every Python 3.14 ships tomllib, so this installation is incomplete
        try:
            sys.stderr.write(
                "error: the roadmap/changelog generators cannot import tomllib, part of the Python "
                "standard library; this installation is incomplete. Nothing was run (cannot evaluate).\n")
            sys.stderr.flush()
        except BaseException:
            pass
    os._exit(2)

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


def repo_root(start=None):
    p = Path(start or __file__).resolve()
    for anc in [p, *p.parents]:
        if (anc / ".git").exists():
            return anc
    return Path.cwd()


def load_toml(path):
    with open(path, "rb") as handle:
        try:
            return tomllib.load(handle)
        except (ValueError, RecursionError, MemoryError) as exc:
            # Callers fail closed on the ValueError family by contract, so a ValueError (a TOMLDecodeError or
            # an over-long integer literal) passes to them unchanged. tomllib raises RecursionError (a
            # RuntimeError) on a deeply nested array or inline table and, from CPython 3.14.8, on a key of more
            # than 1000 dotted parts, and MemoryError on a many-part key under a memory limit; map each into
            # that family here, at the parse locus, so no caller's ValueError handler is bypassed
            # (F-TOML-BARE-VALUEERROR-CLASS). A MemoryError is named as the resource failure it is, never
            # as nesting.
            if isinstance(exc, ValueError):
                raise
            if isinstance(exc, MemoryError):
                raise ValueError("{}: out of memory parsing TOML ({})".format(path, exc)) from exc
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


def target_dir_present(path, root=None):
    """dir_chain_or_absent (tools/_standards.py) for the directory holding the generated file at path, judged
    at every component under root (by default repo_root()), before the first read, write or delete of that
    file: a component that is there but cannot be read as a directory (a regular file, or a symbolic link
    that is dangling, whose target is not a directory or resolves outside the repository), one that cannot be
    examined, or a path not under root exits 2 with a message, so nothing is read through it, written to it
    or reported clean from it. A symbolic link to a directory inside root is followed. Returns True when the
    directory is there and False when it is absent (a write creates it). A component retargeted between this
    probe and the access (by another writer at the same moment) is not detected."""
    # Imported here, not at module level: a caller that never reaches a generated file needs only this
    # module, and _standards.py sits beside it in tools/.
    from _standards import dir_chain_or_absent
    parent = Path(path).parent
    try:
        present, problem = dir_chain_or_absent(parent, Path(repo_root() if root is None else root))
    except OSError as exc:
        present, problem = False, "cannot be examined ({})".format(exc)
    if problem is not None:
        print("error: {} {} (cannot evaluate); fail-closed".format(parent, problem), file=sys.stderr)
        raise SystemExit(2)
    return present


def reconcile(path, new_text, check, root=None, make_parents=False):
    """Write new_text to path, or (check mode) return True if it would change. Fail-closed: an OSError
    reading the current file or writing the new one exits 2 with a message rather than a raw traceback,
    so a drift gate or a regeneration never dies unhandled on a read-only fs, a permission error, or a
    full disk. An invalid-UTF-8 (non-decodable) existing target is mapped to the same exit 2: read_text
    decodes as UTF-8, so a corrupt target raises UnicodeDecodeError, and that is fail-closed too rather
    than a raw traceback. Before the first read, the directory holding path is judged under root by
    target_dir_present (exit 2 on one that is there but cannot be read as a directory inside the
    repository); a caller that renders a tree other than repo_root() passes that tree as root. With
    make_parents, a write first creates the absent directories above path, only after that probe."""
    path = Path(path)
    target_dir_present(path, root)
    try:
        current = path.read_text(encoding="utf-8") if path.exists() else None
        if check:
            return current != new_text
        if make_parents:
            path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(new_text, encoding="utf-8")
        return False
    except (OSError, UnicodeError) as exc:
        print("error: cannot read or write {} ({}); fail-closed".format(path, exc), file=sys.stderr)
        raise SystemExit(2)


_PAST_NS = 10 ** 9  # a fixed modification time a write always moves


def linked_dir_failures(tmp, label, build, run, link_rel, target_rel, drift=None, unread=None):
    """Self-test vectors shared by the generators that write target_rel through reconcile (and
    target_dir_present). For each of two trees, build(base) makes one that run(base, False) generates; then the
    directory link_rel above target_rel is moved, replaced by a symbolic link to it, the target edited by
    drift(path) when given (for a generator that writes only on a difference), and its modification time set
    to a fixed past value. Moved outside the tree, run(base, True) and run(base, False) must each exit 2
    (returned, or raised as SystemExit) with the target untouched there, never read through, reported drifted
    or clean, or written out of the repository; moved elsewhere inside the tree, run(base, False) must exit 0
    having rewritten the target there and run(base, True) then exit 0. unread(path), when given, rewrites the
    target moved outside so that reading it fails on its own (a page without its markers, say), and each
    outside run must then print the link refusal (LINK_OUTSIDE_ROOT), so a generator that reads the target
    before the probe fails with its own error instead and goes red. Returns one failure line per case that
    does not hold, so a revert of the probe goes red in the caller's self-test."""
    import io
    import shutil
    from contextlib import redirect_stderr, redirect_stdout
    from _standards import LINK_OUTSIDE_ROOT

    def quiet(base, check):
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(out):
            try:
                code = run(base, check)
            except SystemExit as exc:
                code = exc.code
        return code, out.getvalue()

    failures = []
    for where in ("outside", "in-root"):
        base = Path(tmp) / "{}-linked-{}".format(label, where)
        base.mkdir(parents=True)
        build(base)
        first = quiet(base, False)[0]
        if first != 0:
            failures.append("{}: the tree for the {} {} link did not generate (exit {})".format(
                label, where, link_rel, first))
            continue
        link = base / link_rel
        held = Path(tmp) / "{}-held-{}".format(label, where) if where == "outside" else base / "shared" / "held"
        held.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(link), str(held))
        link.symlink_to(held if where == "outside" else os.path.relpath(held, link.parent))
        target = held / Path(target_rel).relative_to(link_rel)
        if drift is not None:
            drift(target)
        if unread is not None and where == "outside":
            unread(target)
        os.utime(target, ns=(_PAST_NS, _PAST_NS))
        if where == "outside":
            runs = (quiet(base, True), quiet(base, False))
            got, want = tuple(code for code, _out in runs), (2, 2)
            moved = os.stat(target).st_mtime_ns != _PAST_NS
            if got != want or moved:
                failures.append("{}: {} symlinked outside the root expected --check and write exit {} with {} "
                                "not written there, got exit {}, written {}".format(label, link_rel, want,
                                                                                     target_rel, got, moved))
            elif unread is not None and not all(LINK_OUTSIDE_ROOT in out for _code, out in runs):
                failures.append("{}: {} symlinked outside the root with {} there unreadable expected --check and "
                                "write to print the link refusal, so the target is never read through it, got "
                                "{!r}".format(label, link_rel, target_rel, [out for _code, out in runs]))
        else:
            got, want = (quiet(base, False)[0], quiet(base, True)[0]), (0, 0)
            moved = os.stat(target).st_mtime_ns != _PAST_NS
            if got != want or not moved:
                failures.append("{}: {} symlinked inside the root expected write and --check exit {} with {} "
                                "rewritten there, got exit {}, written {}".format(label, link_rel, want,
                                                                                   target_rel, got, moved))
    return failures
