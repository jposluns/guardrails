#!/usr/bin/env python3
"""Read-only discovery of old operational source files for OPF adoption (spec 14, 14.2).

discover_sources(product_root) walks the product root and PROPOSES files that look like old
operational records: a hand-kept TODO list or backlog, a decisions log, release notes, an issue
export. Each proposal is (path, reason). A proposal is not a source: investigate(..., sources=...)
takes it only after the adopter confirms it (spec 14.2: import MUST NOT authorize an incidental
discovery), and the adopter may add files discovery did not propose.

The walk is bounded like the planner (its MAX_ENTRIES, MAX_DEPTH, MAX_PATH_BYTES, MAX_FILE_BYTES and
MAX_TOTAL_BYTES), never follows a symlink, lists each directory in sorted order and writes nothing. It
never enters:
- a `.git` or `.aiqt` component at any depth (STORE_ROOT_CONTROL_DIRS, compared case-insensitively:
  version control and the AIQT tree, the legacy import and adoption journals included);
- a path investigation already inventories: every DETECTION_ROOTS entry (`.working` among them, so the
  whole store tree, its machine store and its control area) and every deliverable destination (the
  product-root VERSION and each declared deliverable or view target outside `.working`);
- a path registered under [unmanaged] in the resolved store manifest (spec 14.2: ordinary tooling MUST
  NOT read it); discovery lists the parent directory, which names it, and makes no other access; and
- generated tool output (GENERATED_NAMES).
Each such entry, each symlink, special file, hard-linked file, file over the planner byte bound, and each
name outside the contained-path grammar is reported as ruled out, with its reason.

A store that does not resolve is accepted only where the planner accepts one: no pointer names another
root, and no `.working` subdirectory holds a candidate manifest (_opf_adopt_plan._require_no_store_manifest),
so no unmanaged registration can exist that discovery would miss. Any other unresolved store, any bound
reached, and any entry that changes during the walk is cannot-evaluate with no proposals, never a partial
list presented as complete.

Signals: the file name of a file with a NAME_SUFFIXES suffix (NAME_SIGNALS, on the lowercased name before
its first dot with everything but letters and digits removed, so todo.py is not proposed), else the first
ATX Markdown heading that matches HEADING_SIGNALS (normalized the same way) within the first
CONTENT_PREFIX_BYTES of a Markdown or plain-text file (CONTENT_SUFFIXES; an extensionless file is not
scanned, so a Makefile comment is not a heading). Signals are heuristics: a proposal is a question for the
adopter, never a classification, and a file whose name and headings match no signal is not proposed.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: _opf_adopt_discover.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import os
import stat
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _opf_adopt as schema  # noqa: E402
import _opf_adopt_plan as planning  # noqa: E402
import _opf_store as store  # noqa: E402

MAX_ENTRIES = planning.MAX_ENTRIES
MAX_DEPTH = planning.MAX_DEPTH
MAX_PATH_BYTES = planning.MAX_PATH_BYTES
MAX_FILE_BYTES = planning.MAX_FILE_BYTES
MAX_TOTAL_BYTES = planning.MAX_TOTAL_BYTES
CONTENT_PREFIX_BYTES = 16 * 1024
CONTENT_SUFFIXES = (".markdown", ".md", ".txt")
NAME_SUFFIXES = ("", ".adoc", ".csv", ".json", ".markdown", ".md", ".org", ".rst", ".toml", ".tsv", ".txt",
                 ".yaml", ".yml")
CONTROL_NAMES = tuple(name.casefold() for name in store.STORE_ROOT_CONTROL_DIRS)
GENERATED_NAMES = (".mypy_cache", ".pytest_cache", ".tox", ".venv", "__pycache__", "node_modules", "venv")
NAME_SIGNALS = {
    "backlog": "a backlog",
    "changelog": "release notes",
    "changes": "release notes",
    "decisionlog": "a decisions log",
    "decisions": "a decisions log",
    "decisionslog": "a decisions log",
    "issues": "an issue export",
    "releasenotes": "release notes",
    "todo": "a TODO list",
    "todos": "a TODO list",
}
HEADING_SIGNALS = {
    "backlog": "a backlog",
    "changelog": "release notes",
    "decisionlog": "a decisions log",
    "decisions": "a decisions log",
    "decisionslog": "a decisions log",
    "openissues": "an issue export",
    "releasenotes": "release notes",
    "todo": "a TODO list",
    "todolist": "a TODO list",
    "todos": "a TODO list",
}


class DiscoverResult:
    __slots__ = ("status", "findings", "proposals", "searched", "ruled_out")

    def __init__(self, status, findings=(), proposals=(), searched=(), ruled_out=()):
        self.status = status
        self.findings = tuple(findings)
        self.proposals = tuple(proposals)    # (path, reason): proposals for the adopter, never sources
        self.searched = tuple(searched)      # every directory enumerated, "." for the product root
        self.ruled_out = tuple(ruled_out)    # (path, reason): entries discovery did not enter or read


def _bound(what):
    raise planning.PlanError("discovery {} bound reached: cannot-evaluate, never a partial list".format(what))


def _normal(text):
    return "".join(ch for ch in text.lower() if ch.isalnum())


def _headings(data):
    """The ATX heading texts in `data`, in order; nothing for content holding a NUL byte."""
    if b"\0" in data:
        return
    for line in data.decode("utf-8", errors="replace").splitlines():
        indent = len(line) - len(line.lstrip(" "))
        body = line[indent:]
        level = len(body) - len(body.lstrip("#"))
        if indent > 3 or not 1 <= level <= 6 or body[level:level + 1] not in ("", " ", "\t"):
            continue
        yield body[level:].strip().rstrip("#").strip()


def _scope(root_fd, root):
    """(inventoried, unmanaged): the paths investigation inventories and the registered [unmanaged]
    paths, from the pointers and the store manifest the resolver reads through the held root."""
    for pointer in (store.POINTER_REL, store.LOCAL_POINTER_REL):
        target = store._read_pointer_target(root_fd, pointer)
        if target is not None and store._target_store_root(target, root) != root:
            raise planning.PlanError("companion/remote store requires separately scoped investigation")
    resolution, raw = store.resolve_store_fd(root_fd, root)
    unmanaged = []
    if resolution.status == store.RESOLVED:
        if len(raw) > MAX_FILE_BYTES:
            raise planning.PlanError("resolved manifest exceeds byte bound")
        manifest = tomllib.loads(raw.decode("utf-8"))
        checked = store.validate_manifest(manifest)
        if checked.status != store.VALID:
            raise planning.PlanError("resolved manifest is not valid: " + "; ".join(checked.findings))
        unmanaged = [planning._path(path) for path in manifest.get("unmanaged", {}).get("paths", [])]
    else:
        if resolution.status == store.CANNOT_EVALUATE:
            planning._require_no_store_manifest(root_fd, resolution)
        import _opf_init
        manifest = tomllib.loads(_opf_init.build_manifest())
    inventoried = set(planning.DETECTION_ROOTS) | set(planning._deliverable_destinations(manifest))
    return frozenset(inventoried), tuple(sorted(unmanaged))


def _ruled_out(path, name, inventoried, unmanaged):
    """Why discovery does not enter or read `path`, from its name alone, or None."""
    if name.casefold() in CONTROL_NAMES:
        return "version-control or AIQT control area, never entered"
    if path in inventoried:
        return "inventoried by investigate (a detection root or deliverable destination)"
    if any(planning._under(path, kept) for kept in unmanaged):
        return "registered [unmanaged]: ordinary tooling must not read it (spec 14.2)"
    if name in GENERATED_NAMES:
        return "generated tool output"
    try:
        name.encode("utf-8")
    except UnicodeError:
        return "name is not UTF-8, so investigate cannot take it"
    if not schema._is_contained_filepath(path):
        return "name outside the contained-path grammar, so investigate cannot take it"
    return None


def _read_prefix(fd, name, before, budget):
    """At most CONTENT_PREFIX_BYTES of a regular file, no-follow and nonblocking, tied to `before`."""
    want = min(before.st_size, CONTENT_PREFIX_BYTES)
    if budget[0] + want > MAX_TOTAL_BYTES:
        _bound("content byte")
    child = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    try:
        opened = os.fstat(child)
        if (not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1
                or planning._stamp(opened) != planning._stamp(before)):
            raise planning.PlanError("non-regular, linked, or changed file: {!r}".format(name))
        chunks = []
        size = 0
        while size < want:
            block = os.read(child, want - size)
            if not block:
                break
            size += len(block)
            chunks.append(block)
        budget[0] += size
        if planning._stamp(os.fstat(child)) != planning._stamp(opened):
            raise planning.PlanError("file changed during read: {!r}".format(name))
        return b"".join(chunks)
    finally:
        store._journal._close_fd_propagating(child)


def _signal(fd, name, before, budget):
    """(proposal reason, ruled-out reason) for a regular file; both None when it shows no signal."""
    if before.st_nlink != 1:
        return None, "hard-linked file, not read (investigate refuses a linked file)"
    if before.st_size > MAX_FILE_BYTES:
        return None, "over the investigation byte bound, not read (investigate refuses it)"
    suffix = os.path.splitext(name)[1].lower()
    kind = NAME_SIGNALS.get(_normal(name.lstrip(".").split(".", 1)[0]))
    if kind and suffix in NAME_SUFFIXES:
        return "name {!r} looks like {}".format(name, kind), None
    if suffix not in CONTENT_SUFFIXES:
        return None, None
    for heading in _headings(_read_prefix(fd, name, before, budget)):
        kind = HEADING_SIGNALS.get(_normal(heading))
        if kind:
            return "heading {!r} looks like {}".format(heading[:80], kind), None
    return None, None


def _discover(root):
    store._journal.require_containment()
    root_fd = store._open_dir_nofollow(root)
    try:
        root_stat = os.fstat(root_fd)
        inventoried, unmanaged = _scope(root_fd, root)
        proposals, searched, ruled_out = [], [], []
        counts = {"entries": 0, "path_bytes": 0}
        budget = [0]

        def visit(fd, path, depth):
            searched.append(path)
            names = []
            with os.scandir(fd) as listing:
                for entry in listing:
                    names.append(entry.name)
                    counts["entries"] += 1
                    if counts["entries"] > MAX_ENTRIES:
                        _bound("entry")
            for name in sorted(names):
                child_path = name if path == "." else path + "/" + name
                counts["path_bytes"] += len(child_path.encode("utf-8", "surrogateescape"))
                if counts["path_bytes"] > MAX_PATH_BYTES:
                    _bound("path byte")
                reason = _ruled_out(child_path, name, inventoried, unmanaged)
                if reason:
                    ruled_out.append((child_path, reason))
                    continue
                before = os.stat(name, dir_fd=fd, follow_symlinks=False)
                if stat.S_ISLNK(before.st_mode):
                    ruled_out.append((child_path, "symlink, not followed"))
                elif stat.S_ISDIR(before.st_mode):
                    if depth + 1 > MAX_DEPTH:
                        _bound("depth")
                    child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                    try:
                        if planning._stamp(os.fstat(child)) != planning._stamp(before):
                            raise planning.PlanError("directory changed before enumeration")
                        visit(child, child_path, depth + 1)
                        if planning._stamp(os.fstat(child)) != planning._stamp(before):
                            raise planning.PlanError("directory changed during enumeration")
                    finally:
                        store._journal._close_fd_propagating(child)
                elif stat.S_ISREG(before.st_mode):
                    proposal, refusal = _signal(fd, name, before, budget)
                    if proposal:
                        proposals.append((child_path, proposal))
                    elif refusal:
                        ruled_out.append((child_path, refusal))
                else:
                    ruled_out.append((child_path, "special file, not opened"))

        visit(root_fd, ".", 0)
        if planning._stamp(os.fstat(root_fd)) != planning._stamp(root_stat):
            raise planning.PlanError("product root changed during discovery")
        check_fd = store._open_dir_nofollow(root)
        try:
            if planning._stamp(os.fstat(check_fd)) != planning._stamp(root_stat):
                raise planning.PlanError("product root changed during discovery")
        finally:
            store._journal._close_fd_propagating(check_fd)
        return sorted(proposals), sorted(searched), sorted(ruled_out)
    finally:
        store._journal._close_fd_propagating(root_fd)


def discover_sources(product_root):
    """Propose old operational files for the adopter to confirm. Writes nothing; a proposal is never a
    source until the adopter confirms it and it is passed to investigate(..., sources=...)."""
    try:
        if not isinstance(product_root, (str, os.PathLike)):
            raise planning.PlanError("product_root must be an absolute path")
        root = Path(product_root)
        if not root.is_absolute() or ".." in root.parts:
            raise planning.PlanError("product_root must be absolute and contain no '..'")
        proposals, searched, ruled_out = _discover(root)
        return DiscoverResult(store.VALID, proposals=proposals, searched=searched, ruled_out=ruled_out)
    except (OSError, ValueError, UnicodeError, RecursionError,
            store.StoreError, store._journal.JournalError) as exc:
        return DiscoverResult(store.CANNOT_EVALUATE, [str(exc)])


def self_test():
    """Synthetic fixtures only. The caller/CI must provide writable temp space."""
    import contextlib
    import io
    import tempfile
    import unittest
    from unittest import mock

    from _opf_emit import emit_checked

    module = sys.modules[__name__]

    class DiscoveryTests(unittest.TestCase):
        def setUp(self):
            self.temp = tempfile.TemporaryDirectory(prefix="opf-adopt-discover-")
            self.addCleanup(self.temp.cleanup)
            self.root = Path(self.temp.name).resolve() / "product"
            self.root.mkdir()

        def write(self, rel, data=b"- item\n"):
            path = self.root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)

        def discover(self):
            result = discover_sources(self.root)
            self.assertEqual(result.status, store.VALID, result.findings)
            return result

        def paths(self, rows):
            return [path for path, _reason in rows]

        def test_discover_by_name(self):
            self.write("TODO.md")
            self.write("notes/release-notes.txt")
            self.write("notes/Decisions_Log.markdown")
            self.write("export/issues.json", b"[]\n")
            self.write("notes/readme.md", b"# Install\n")
            self.write("src/todo.py", b"x = 1\n")
            result = self.discover()
            self.assertEqual(self.paths(result.proposals), [
                "TODO.md", "export/issues.json", "notes/Decisions_Log.markdown",
                "notes/release-notes.txt"])
            self.assertTrue(all(reason.startswith("name ") for _path, reason in result.proposals))

        def test_discover_by_content_heading(self):
            self.write("notes/plan.md", b"intro\n\n## Decisions\n\n- use TOML\n")
            self.write("notes/install.md", b"# Install\n")
            self.write("notes/code.md", b"    # Decisions\n")
            self.write("notes/script.py", b"# Decisions\n")
            self.write("Makefile", b"# TODO\n")
            self.write("notes/late.md", b"x" * CONTENT_PREFIX_BYTES + b"\n# TODO\n")
            result = self.discover()
            self.assertEqual(result.proposals, (
                ("notes/plan.md", "heading 'Decisions' looks like a decisions log"),))

        def test_excludes_git(self):
            for rel in (".git/TODO.md", "vendor/lib/.git/TODO.md", ".aiqt/TODO.md", "sub/.GIT/TODO.md"):
                self.write(rel)
            result = self.discover()
            self.assertEqual(result.proposals, ())
            self.assertEqual(self.paths(result.ruled_out), [".aiqt", ".git", "sub/.GIT", "vendor/lib/.git"])
            self.assertFalse([p for p in result.searched if ".git" in p.casefold() or ".aiqt" in p])

        def test_excludes_unmanaged_content(self):
            import _opf_init
            manifest = tomllib.loads(_opf_init.build_manifest())
            manifest["unmanaged"]["paths"] = ["notes/private", "notes/kept-todo.md"]
            self.write(".working/toml/manifest.toml", emit_checked(manifest).encode("utf-8"))
            self.write("notes/private/TODO.md")
            self.write("notes/kept-todo.md")
            self.write("notes/TODO.md")
            # A FIFO under the unmanaged subtree would be listed if discovery entered it.
            os.mkfifo(self.root / "notes/private/pipe")
            result = self.discover()
            self.assertEqual(self.paths(result.proposals), ["notes/TODO.md"])
            unmanaged = [path for path, reason in result.ruled_out if "[unmanaged]" in reason]
            self.assertEqual(unmanaged, ["notes/kept-todo.md", "notes/private"])
            seen = self.paths(result.proposals) + self.paths(result.ruled_out) + list(result.searched)
            self.assertFalse([p for p in seen if p.startswith("notes/private/")])

        def test_excludes_detection_roots(self):
            for rel in (".working/TODO.md", ".working/archive/TODO.md", "CHANGELOG.md", "VERSION",
                        ".github/workflows/TODO.md", ".cursor/TODO.md", "docs/CHANGELOG.md"):
                self.write(rel)
            result = self.discover()
            self.assertEqual(self.paths(result.proposals), ["docs/CHANGELOG.md"])
            inventoried = [path for path, reason in result.ruled_out if "inventoried" in reason]
            self.assertEqual(inventoried, [".cursor", ".github/workflows", ".working", "CHANGELOG.md",
                                           "VERSION"])
            self.assertNotIn(".working", result.searched)

        def test_excludes_generated_output(self):
            self.write("node_modules/pkg/TODO.md")
            self.write("src/__pycache__/TODO.md")
            result = self.discover()
            self.assertEqual(result.proposals, ())
            self.assertEqual(self.paths(result.ruled_out), ["node_modules", "src/__pycache__"])

        def test_symlink_not_followed(self):
            outside = self.root.parent / "outside"
            outside.mkdir()
            (outside / "TODO.md").write_bytes(b"- elsewhere\n")
            (outside / "notes.md").write_bytes(b"# Backlog\n")
            os.symlink(outside, self.root / "linked")
            os.symlink(outside / "TODO.md", self.root / "TODO.md")
            os.symlink(outside / "notes.md", self.root / "notes.md")
            result = self.discover()
            self.assertEqual(result.proposals, ())
            self.assertEqual(result.ruled_out, tuple(
                (path, "symlink, not followed") for path in ("TODO.md", "linked", "notes.md")))
            self.assertEqual(result.searched, (".",))

        def test_writes_nothing(self):
            self.write("TODO.md")
            self.write("notes/plan.md", b"# Backlog\n")
            self.write("notes/other.txt", b"text\n")

            def snapshot():
                return {str(p.relative_to(self.root)): (p.is_dir(), p.read_bytes() if p.is_file() else None,
                                                        p.stat().st_mtime_ns)
                        for p in self.root.rglob("*")}

            before = snapshot()
            original_open = os.open
            original_io_open = io.open

            def ro_open(path, flags, *args, **kwargs):
                self.assertFalse(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT
                                          | os.O_TRUNC | os.O_APPEND))
                return original_open(path, flags, *args, **kwargs)

            def ro_io_open(path, mode="r", *args, **kwargs):
                self.assertFalse(any(c in mode for c in "wax+"))
                return original_io_open(path, mode, *args, **kwargs)

            with contextlib.ExitStack() as stack:
                stack.enter_context(mock.patch.object(os, "open", ro_open))
                stack.enter_context(mock.patch.object(
                    os, "supports_dir_fd", os.supports_dir_fd | {ro_open}))
                stack.enter_context(mock.patch.object(io, "open", ro_io_open))
                stack.enter_context(mock.patch("builtins.open", ro_io_open))
                stack.enter_context(mock.patch("subprocess.Popen",
                                               side_effect=AssertionError("process effect")))
                stack.enter_context(mock.patch.object(
                    store._journal, "run_transaction", side_effect=AssertionError("journal effect")))
                result = self.discover()
            self.assertEqual(self.paths(result.proposals), ["TODO.md", "notes/plan.md"])
            self.assertEqual(snapshot(), before)

        def test_deterministic_order(self):
            for rel in ("b/TODO.md", "a-b/backlog.md", "a/TODO.md", "TODO.md", "c.md"):
                self.write(rel)
            (self.root / "z-link").symlink_to("a")
            first = self.discover()
            real_scandir = os.scandir

            class Reversed:
                def __init__(self, fd):
                    with real_scandir(fd) as listing:
                        self.entries = list(listing)

                def __enter__(self):
                    return iter(reversed(self.entries))

                def __exit__(self, *exc):
                    return False

            with mock.patch.object(os, "scandir", Reversed):
                second = self.discover()
            expected = ["TODO.md", "a-b/backlog.md", "a/TODO.md", "b/TODO.md"]
            for result in (first, second):
                self.assertEqual(self.paths(result.proposals), expected)
                self.assertEqual(list(result.searched), [".", "a", "a-b", "b"])
            self.assertEqual((first.proposals, first.searched, first.ruled_out),
                             (second.proposals, second.searched, second.ruled_out))

        def test_bound_reached_is_cannot_evaluate(self):
            self.write("TODO.md")
            self.write("a/b/TODO.md")
            for i in range(4):
                self.write("f{}.md".format(i), b"text\n")
            self.assertEqual(self.paths(self.discover().proposals), ["TODO.md", "a/b/TODO.md"])
            for name, value in (("MAX_ENTRIES", 3), ("MAX_DEPTH", 1), ("MAX_PATH_BYTES", 16),
                                ("MAX_TOTAL_BYTES", 6)):
                with self.subTest(bound=name), mock.patch.object(module, name, value):
                    result = discover_sources(self.root)
                    self.assertEqual(result.status, store.CANNOT_EVALUATE)
                    self.assertEqual((result.proposals, result.searched, result.ruled_out), ((), (), ()))
                    self.assertTrue(any("bound reached" in f for f in result.findings), result.findings)

        def test_unresolvable_store_refuses(self):
            self.write("TODO.md")
            self.write(".working/notes.md")
            # Foreign .working content without a candidate manifest is admitted, as the planner admits it.
            self.assertEqual(self.paths(self.discover().proposals), ["TODO.md"])
            # A candidate manifest that does not resolve might register unmanaged paths: refuse.
            self.write(".working/old/manifest.toml", b"not toml\n")
            self.assertEqual(discover_sources(self.root).status, store.CANNOT_EVALUATE)
            (self.root / ".working/old/manifest.toml").unlink()
            self.write(".opf.toml", b"[store]\ntarget = \"dir:companion\"\n")
            result = discover_sources(self.root)
            self.assertEqual(result.status, store.CANNOT_EVALUATE)
            self.assertTrue(any("companion/remote store" in f for f in result.findings), result.findings)
            self.assertEqual(discover_sources("relative/root").status, store.CANNOT_EVALUATE)

        def test_proposals_feed_investigate_only_when_confirmed(self):
            self.write("TODO.md")
            self.write("docs/notes.md", b"# Decision log\n")
            result = self.discover()
            self.assertEqual(self.paths(result.proposals), ["TODO.md", "docs/notes.md"])
            # Discovery authorizes nothing: without confirmed sources, investigation has no candidate.
            bare = planning.investigate(self.root, sources=[])
            self.assertEqual(tomllib.loads(bare.observation.decode())["candidates"], [])
            # The adopter confirms one proposal; only that one becomes a source.
            confirmed = planning.investigate(self.root, sources=["TODO.md"])
            self.assertEqual(confirmed.status, store.VALID, confirmed.findings)
            self.assertEqual(tomllib.loads(confirmed.observation.decode())["candidates"], ["TODO.md"])

    suite = unittest.defaultTestLoader.loadTestsFromTestCase(DiscoveryTests)
    expected = suite.countTestCases()
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() and result.testsRun == expected else 1


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-test"]:
        sys.exit(self_test())
    print("usage: _opf_adopt_discover.py --self-test (call discover_sources from Python)", file=sys.stderr)
    sys.exit(2)
