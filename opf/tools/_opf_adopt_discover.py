#!/usr/bin/env python3
"""Read-only discovery of old operational source files for OPF adoption (spec 14, 14.2).

discover_sources(product_root) walks the product root and PROPOSES files that look like old
operational records: a hand-kept TODO list or backlog, a decisions log, release notes, an issue
export. Each proposal is (path, reason). A proposal is not a source. investigate(..., sources=...)
takes whatever list its caller passes: that the adopter confirms each proposal first, and may add
files discovery did not propose, is a rule of the adopt prompt (adopt/SKILL.md section 3), not a
mechanism here, and no code records or checks a confirmation. The prompt applies to proposals the
rule spec 14.2 sets for post-adoption import (no incidental discovery is authorized). report(result)
renders a result as printable-ASCII lines, so a file name can neither start a line nor fail to encode.

Store resolution runs first and makes exactly the reads investigation makes to resolve the store
(store.resolve_store_fd; spec 4.2, 4.3, 4.5): the local pointer file at the product root, and the
committed one only when no local one exists (spec 4.3: the local override wins wholesale); a listing of
`.working` with a no-follow lstat of each entry; and, in each immediate real subdirectory of `.working`,
control-area names included (spec 4.2 requires every one), a no-follow probe of `manifest.toml`, read
when it is a regular singly-linked file within the resolver read cap (store.MAX_STORE_READ_BYTES). It
reads nothing deeper in the store tree. The scope check before it reads each pointer file once. A store
the resolver cannot evaluate adds the planner existence proof (_opf_adopt_plan._require_no_store_manifest):
the same listing and probes, with no read. Before the resolver runs, discovery makes that listing and
those probes itself (_store_inputs) and charges resolution to its own budgets: STORE_LISTINGS listings of
`.working` (its own, the resolver and the proof) to the entry and path-byte budgets, and each present
pointer file twice and each present manifest once, at most the resolver cap each, to the content-byte
budget (an upper bound: with a local pointer present the committed one is read once, not twice). It
refuses, before the resolver runs, a `.working` holding a `.git` or `.aiqt` subdirectory (compared
caseless), whose manifest probe would enter it. After resolution it refuses an [unmanaged] entry the
planner refuses, through the same check (_opf_adopt_plan._resolved_unmanaged: not a contained path, or
overlapping a reserved store path), and, as spec 14.2 requires, one that equals or contains a present
candidate manifest; it also refuses one that equals a pointer file, which the planner admits (stricter,
disclosed).

Bound on resolution work: the content bytes charged for resolution are the read allowance of the scope
check and the resolver (store.resolve_store_fd `allowance`). Every pointer and manifest read draws on it
and stops at the smaller of the resolver cap and what is left, never reading a byte past it, and a file
that does not fit is cannot-evaluate. So, whatever a concurrent writer does, resolution reads at most the
bytes discovery charged, each pointer or manifest at most store.MAX_STORE_READ_BYTES, and every byte
discovery reads from the product tree, re-reads included, is within MAX_TOTAL_BYTES. The listings are not
metered: a writer that adds entries to `.working` after the listing discovery makes, and before the
resolver or the planner existence proof lists it, can make them list and probe entries not charged to the
entry and path-byte budgets. Such an entry can be a `.git` or `.aiqt` subdirectory, which they then open
to probe its `manifest.toml` (the resolver reads one present there, drawing on the allowance), so store
resolution can enter one despite the refusal above. The recheck below sees such an addition only through
the `.working` stamp, subject to its limits.

The walk is bounded like the planner (its MAX_ENTRIES, MAX_DEPTH, MAX_PATH_BYTES, MAX_FILE_BYTES and
MAX_TOTAL_BYTES), never follows a symlink, lists each directory in sorted order and writes nothing. It
never enters:
- a `.git` or `.aiqt` component at any depth (STORE_ROOT_CONTROL_DIRS: version control and the AIQT
  tree, the legacy import and adoption journals included);
- a path investigation already inventories: every DETECTION_ROOTS entry (`.working` among them, so the
  whole store tree, its machine store and its control area) and every deliverable destination (the
  product-root VERSION and each declared deliverable or view target outside `.working`);
- a path registered under [unmanaged] in the resolved store manifest (spec 14.2: ordinary tooling MUST
  NOT read it); the walk lists the parent directory, which names it, and makes no other access. Store
  resolution, which runs before the registrations are known, is the one exception: as investigation
  does, it probes `manifest.toml` in every immediate real subdirectory of `.working`, a registered one
  included, and reads one present there before the spec 14.2 refusal above rejects it; and
- generated tool output (GENERATED_NAMES).
`.git`, `.aiqt`, `.working` and the [unmanaged] paths compare by Unicode canonical caseless matching
(_fold), so a case or normalization variant that a case-insensitive filesystem would alias to them is
ruled out too; on a case-sensitive filesystem that over-excludes, the safe direction. A variant of an
[unmanaged] path is ruled out with its own reason, not as registered: investigation compares byte-exact
and would take it as a source. Folding follows Unicode full case folding, not a filesystem fold table:
a pair of names a filesystem equates that Unicode folding keeps apart is not caught. The other
inventoried paths compare byte-exact, as the planner compares them: on a case-insensitive filesystem a
variant such as `changelog.md` names the inventoried `CHANGELOG.md` and can be read and proposed, a
duplicate of what investigation reads, not a read the spec forbids. Each excluded entry, each symlink,
special file, hard-linked file, file over the planner byte bound, and each name outside the
contained-path grammar is reported as ruled out, with its reason.

A store that does not resolve is accepted only where the planner accepts one: no pointer names another
root, and no `.working` subdirectory holds a candidate manifest (_opf_adopt_plan._require_no_store_manifest),
so no unmanaged registration can exist that discovery would miss. Any other unresolved store and any
bound reached is cannot-evaluate with no proposals, never a partial list presented as complete.

Concurrent change: discovery compares samples; it does not take a snapshot. After the walk, discovery
reopens the product root by path, no-follow, and compares the stamp (device, inode, mode, link count, size,
mtime and ctime) of every directory it listed, every entry it examined by lstat (each proposed file
included) and every store-resolution input with the stamp it recorded first; and it reads again the prefix
of each file proposed for its heading, charged to the content-byte budget, and compares its SHA-256 with
that of the bytes the walk read. A difference, a vanished or new input, or a root path that no longer
names the walked directory is cannot-evaluate with no proposals. Each comparison holds only when it is
made: a change made after a byte or an entry is last observed can escape. That includes any change after
the recheck, a change to an entry after the recheck has compared it, and a write that leaves every stamp
field equal made to a heading prefix during the recheck read itself, after the bytes are returned and
before that read and the recheck finish: the recheck hashes the bytes the read returned, and the closing
fstat of the read sees the same stamp (test_change_after_final_observation_escapes pins that this reads
VALID). So VALID does not establish one consistent tree state: neither the tree as the adopter later
finds it nor the tree at any one instant of discovery. A second limit: outside those heading prefixes the
recheck compares metadata, not contents, so a change there that leaves every stamp field equal is missed
even when it is made before the recheck. Such changes include a write through a shared writable mapping
whose page is already dirty (it takes no write fault, so mtime and ctime need not move), a same-size edit
within the filesystem timestamp granularity, a writer able to set the system clock, and a stale
attribute cache on a network filesystem; that list is not exhaustive. What a change that escapes can do:
in a directory, add or remove an entry unseen, so the list can miss a file or name one that is gone; in a
file proposed for its heading, leave the proposal resting on a heading the file no longer holds; in a
file read but not proposed, add a heading unseen, so a proposal can be missing, never wrongly present; in
a file proposed by name or not read, nothing, as no result rests on its contents; in a store-resolution
input, leave the scope as the resolver read it. Files read but not proposed are not read again: that
would double the content-byte charge of every scanned file, and a missed change there can only withhold
a proposal. An entry ruled out by name is never examined, so its contents are not compared; a rename into
or out of it changes the stamp of the directory that holds it.

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

import hashlib
import os
import stat
import tomllib
import unicodedata
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
# `.working` is listed by discovery itself, by the resolver and by the planner existence proof.
STORE_LISTINGS = 3
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


def _fold(text):
    """The Unicode canonical caseless form of `text` (Unicode section 3.13: NFD of the full case folding
    of NFD), so a case or normalization variant of a name compares equal to it."""
    return unicodedata.normalize("NFD", unicodedata.normalize("NFD", text).casefold())


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


def _store_inputs(root_fd, counts, budget):
    """Make the listing and probes store resolution makes, before it runs; charge them to the discovery
    budgets as the module docstring states; refuse a `.working` holding a `.git` or `.aiqt` subdirectory;
    and return (inputs, charge): the identity of every resolution input as (pointers, working, subdirs),
    that is (name, lstat or None) for each pointer file, the stat of `.working` (None when absent), and
    (name, stat, lstat of its manifest.toml or None) for each immediate real subdirectory of `.working`;
    and the content bytes charged for resolution, which _scope passes the resolver as its read allowance."""
    pointers = []
    charge = 0
    for pointer in (store.LOCAL_POINTER_REL, store.POINTER_REL):
        found = store._journal._lstat_at(root_fd, pointer)
        pointers.append((pointer, found))
        if found is not None and stat.S_ISREG(found.st_mode):
            charge += 2 * min(found.st_size, store.MAX_STORE_READ_BYTES)
    working = store._journal._lstat_at(root_fd, store.WORKING_DIRNAME)
    subdirs = []
    if working is not None and stat.S_ISDIR(working.st_mode):
        wfd = os.open(store.WORKING_DIRNAME, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd)
        try:
            working = os.fstat(wfd)
            names = []
            with os.scandir(wfd) as listing:
                for entry in listing:
                    names.append(entry.name)
                    counts["entries"] += STORE_LISTINGS
                    if counts["entries"] > MAX_ENTRIES:
                        _bound("entry")
            for name in sorted(names):
                path = store.WORKING_DIRNAME + "/" + name
                counts["path_bytes"] += STORE_LISTINGS * len(path.encode("utf-8", "surrogateescape"))
                if counts["path_bytes"] > MAX_PATH_BYTES:
                    _bound("path byte")
                if not stat.S_ISDIR(os.stat(name, dir_fd=wfd, follow_symlinks=False).st_mode):
                    continue  # the resolver probes a real directory only, never a symlink or a file
                if _fold(name) in CONTROL_NAMES:
                    raise planning.PlanError(
                        "store resolution would probe {!r}: discovery never enters a .git or .aiqt "
                        "component".format(path + "/" + store.MANIFEST_NAME))
                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=wfd)
                try:
                    manifest = store._journal._lstat_at(child, store.MANIFEST_NAME)
                    subdirs.append((name, os.fstat(child), manifest))
                finally:
                    store._journal._close_fd_propagating(child)
                if manifest is not None and stat.S_ISREG(manifest.st_mode):
                    charge += min(manifest.st_size, store.MAX_STORE_READ_BYTES)
        finally:
            store._journal._close_fd_propagating(wfd)
    if budget[0] + charge > MAX_TOTAL_BYTES:
        _bound("content byte")
    budget[0] += charge
    return (tuple(pointers), working, tuple(subdirs)), charge


def _same(now, before, what, path):
    """Refuse unless `now` and `before` are both None or carry the same stamp (_opf_adopt_plan._stamp)."""
    if (now is None) != (before is None) or (
            now is not None and planning._stamp(now) != planning._stamp(before)):
        raise planning.PlanError("{} changed during discovery: {!r}".format(what, path))


def _recheck_store(root_fd, inputs):
    """Compare every store-resolution input with the identity _store_inputs recorded: each pointer file,
    `.working`, each probed subdirectory and its manifest.toml. A subdirectory added to or removed from
    `.working` changes the `.working` stamp."""
    pointers, working, subdirs = inputs
    what = "store resolution input"
    for pointer, before in pointers:
        _same(store._journal._lstat_at(root_fd, pointer), before, what, pointer)
    if working is None or not stat.S_ISDIR(working.st_mode):
        _same(store._journal._lstat_at(root_fd, store.WORKING_DIRNAME), working, what, store.WORKING_DIRNAME)
        return
    wfd = os.open(store.WORKING_DIRNAME, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd)
    try:
        _same(os.fstat(wfd), working, what, store.WORKING_DIRNAME)
        for name, before, manifest in subdirs:
            path = store.WORKING_DIRNAME + "/" + name
            child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=wfd)
            try:
                _same(os.fstat(child), before, what, path)
                _same(store._journal._lstat_at(child, store.MANIFEST_NAME), manifest, what,
                      path + "/" + store.MANIFEST_NAME)
            finally:
                store._journal._close_fd_propagating(child)
    finally:
        store._journal._close_fd_propagating(wfd)


def _recheck_walk(fd, path, examined, digests, budget):
    """Compare each entry the walk examined in the directory held at `fd` (`path`) with the stamp the
    walk recorded, reopening each listed directory no-follow and descending into it; and read again the
    prefix of each file proposed for its heading, charged to the content-byte budget, refusing unless it
    hashes to the digest the walk recorded (`digests`: path -> SHA-256 of the prefix the walk read)."""
    for name, before in examined.get(path, ()):
        child_path = name if path == "." else path + "/" + name
        if not stat.S_ISDIR(before.st_mode):
            _same(store._journal._lstat_at(fd, name), before, "entry", child_path)
            if child_path in digests and hashlib.sha256(
                    _read_prefix(fd, name, before, budget)).digest() != digests[child_path]:
                raise planning.PlanError(
                    "proposed file contents changed during discovery: {!r}".format(child_path))
            continue
        child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
        try:
            _same(os.fstat(child), before, "entry", child_path)
            _recheck_walk(child, child_path, examined, digests, budget)
        finally:
            store._journal._close_fd_propagating(child)


def _scope(root_fd, root, inputs, charge):
    """(inventoried, unmanaged): the paths investigation inventories, and each registered [unmanaged]
    path paired with its folded form (_fold), from the pointers and the store manifest the resolver reads
    through the held root. `inputs` is what _store_inputs recorded, for the spec 14.2 candidate-manifest
    refusal; `charge` is what it charged for resolution, the read allowance every pointer and manifest
    read here and in the resolver draws on (store._read_store_bytes_contained), so those reads stop at
    the charge and at the resolver cap per file, a store grown since _store_inputs included."""
    allowance = [charge]
    for pointer in (store.POINTER_REL, store.LOCAL_POINTER_REL):
        target = store._read_pointer_target(root_fd, pointer, allowance)
        if target is not None and store._target_store_root(target, root) != root:
            raise planning.PlanError("companion/remote store requires separately scoped investigation")
    resolution, raw = store.resolve_store_fd(root_fd, root, allowance=allowance)
    unmanaged = []
    if resolution.status == store.RESOLVED:
        if len(raw) > MAX_FILE_BYTES:
            raise planning.PlanError("resolved manifest exceeds byte bound")
        try:
            manifest = tomllib.loads(raw.decode("utf-8"))
        except (ValueError, RecursionError, MemoryError) as exc:
            # The store resolver has already parsed these very bytes (discover_machine_store_fd, whose
            # _parse_store_toml maps ValueError, RecursionError and MemoryError to StoreError), so only
            # a transient failure reaches this re-parse: a RecursionError at this deeper stack, or a
            # MemoryError under pressure (F-TOML-BARE-VALUEERROR-CLASS). Each maps to the discovery
            # PlanError, cannot-evaluate.
            raise planning.PlanError("resolved manifest does not parse: {}".format(exc)) from exc
        checked = store.validate_manifest(
            manifest, manifest_rel=resolution.machine_rel + "/" + store.MANIFEST_NAME)
        if checked.status != store.VALID:
            raise planning.PlanError("resolved manifest is not valid: " + "; ".join(checked.findings))
        unmanaged = planning._resolved_unmanaged(manifest, resolution.machine_rel)
        pointers, _working, subdirs = inputs
        reads = [pointer for pointer, found in pointers if found is not None]
        reads += [store.WORKING_DIRNAME + "/" + name + "/" + store.MANIFEST_NAME
                  for name, _found, manifest_stat in subdirs if manifest_stat is not None]
        for kept in unmanaged:
            hit = [path for path in reads if planning._under(_fold(path), _fold(kept))]
            if hit:
                raise planning.PlanError(
                    "unmanaged exclusion {!r} equals or contains store-resolution input {!r} (spec 14.2: "
                    "a contradictory input, cannot-evaluate)".format(kept, hit[0]))
    else:
        if resolution.status == store.CANNOT_EVALUATE:
            planning._require_no_store_manifest(root_fd, resolution)
        import _opf_init
        try:
            manifest = tomllib.loads(_opf_init.build_manifest())
        except (ValueError, RecursionError, MemoryError) as exc:
            # the same classes out of the generated default manifest: cannot-evaluate, never a pass
            raise planning.PlanError("generated store manifest does not parse: {}".format(exc)) from exc
    inventoried = set(planning.DETECTION_ROOTS) | set(planning._deliverable_destinations(manifest))
    return frozenset(inventoried), tuple(sorted((path, _fold(path)) for path in unmanaged))


def _ruled_out(path, name, inventoried, unmanaged):
    """Why discovery does not enter or read `path`, from its name alone, or None. `unmanaged` holds
    (path, folded path) for each [unmanaged] path."""
    if _fold(name) in CONTROL_NAMES:
        return "version-control or AIQT control area, never entered"
    if path in inventoried:
        return "inventoried by investigate (a detection root or deliverable destination)"
    folded = _fold(path)
    if folded == _fold(store.WORKING_DIRNAME):
        return ("a case or normalization variant of .working, never entered (a case-insensitive filesystem "
                "aliases it to the store tree)")
    if any(planning._under(path, kept) for kept, _folded in unmanaged):
        return "registered [unmanaged]: ordinary tooling must not read it (spec 14.2)"
    if any(planning._under(folded, kept) for _kept, kept in unmanaged):
        return ("a case or normalization variant of an [unmanaged] path, never entered (a case-insensitive "
                "filesystem may alias it to the registered path; investigation compares byte-exact)")
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
    """(proposal reason, ruled-out reason, content) for a regular file; the reasons both None when it
    shows no signal, and content the prefix read when the proposal rests on it (a heading), else None."""
    if before.st_nlink != 1:
        return None, "hard-linked file, not read (investigate refuses a linked file)", None
    if before.st_size > MAX_FILE_BYTES:
        return None, "over the investigation byte bound, not read (investigate refuses it)", None
    suffix = os.path.splitext(name)[1].lower()
    kind = NAME_SIGNALS.get(_normal(name.lstrip(".").split(".", 1)[0]))
    if kind and suffix in NAME_SUFFIXES:
        return "name {!r} looks like {}".format(name, kind), None, None
    if suffix not in CONTENT_SUFFIXES:
        return None, None, None
    data = _read_prefix(fd, name, before, budget)
    for heading in _headings(data):
        kind = HEADING_SIGNALS.get(_normal(heading))
        if kind:
            return "heading {!r} looks like {}".format(heading[:80], kind), None, data
    return None, None, None


def _discover(root):
    store._journal.require_containment()
    root_fd = store._open_dir_nofollow(root)
    try:
        root_stat = os.fstat(root_fd)
        # Budgets start before store resolution, which _store_inputs charges to them.
        counts = {"entries": 0, "path_bytes": 0}
        budget = [0]
        inputs, charge = _store_inputs(root_fd, counts, budget)
        inventoried, unmanaged = _scope(root_fd, root, inputs, charge)
        proposals, searched, ruled_out = [], [], []
        examined = {}  # directory path -> [(name, lstat)] for every entry the walk examined there
        digests = {}  # path -> SHA-256 of the prefix read, for each file proposed for its heading

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
                examined.setdefault(path, []).append((name, before))
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
                    proposal, refusal, content = _signal(fd, name, before, budget)
                    if proposal:
                        proposals.append((child_path, proposal))
                        if content is not None:
                            digests[child_path] = hashlib.sha256(content).digest()
                    elif refusal:
                        ruled_out.append((child_path, refusal))
                else:
                    ruled_out.append((child_path, "special file, not opened"))

        visit(root_fd, ".", 0)
        if planning._stamp(os.fstat(root_fd)) != planning._stamp(root_stat):
            raise planning.PlanError("product root changed during discovery")
        # Recheck through the root path, reopened no-follow, so a swapped root or ancestor is seen too.
        check_fd = store._open_dir_nofollow(root)
        try:
            if planning._stamp(os.fstat(check_fd)) != planning._stamp(root_stat):
                raise planning.PlanError("product root path no longer names the walked directory")
            _recheck_walk(check_fd, ".", examined, digests, budget)
            _recheck_store(check_fd, inputs)
        finally:
            store._journal._close_fd_propagating(check_fd)
        return sorted(proposals), sorted(searched), sorted(ruled_out)
    finally:
        store._journal._close_fd_propagating(root_fd)


def discover_sources(product_root):
    """Propose old operational files for the adopter to confirm. Writes nothing. A proposal becomes a
    source only when a caller passes it to investigate(..., sources=...); the adopt prompt requires the
    adopter to confirm it first, and nothing here records or checks that confirmation."""
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


def _escape(text):
    """`text` as printable ASCII: a backslash doubled and every other character outside space to tilde
    written as its Python escape, so a file name can neither start a report line nor fail to encode."""
    return "".join(ch if " " <= ch <= "~" and ch != "\\" else "\\\\" if ch == "\\" else ascii(ch)[1:-1]
                   for ch in text)


def report(result):
    """A DiscoverResult as printable-ASCII text, one item per line: the status, then each finding,
    proposal, ruled-out entry and searched directory, every path and reason escaped (_escape)."""
    lines = [_escape(result.status)]
    lines += ["finding: " + _escape(text) for text in result.findings]
    lines += ["propose " + _escape(path) + ": " + _escape(why) for path, why in result.proposals]
    lines += ["ruled out " + _escape(path) + ": " + _escape(why) for path, why in result.ruled_out]
    lines += ["searched " + _escape(path) for path in result.searched]
    return "\n".join(lines)


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

        def age(self):
            """Set every fixture timestamp to one old instant, so a later change moves an mtime even
            within the filesystem timestamp granularity and the change vectors cannot pass by timing."""
            for top, dirs, files in os.walk(self.root):
                for name in dirs + files:
                    os.utime(os.path.join(top, name), (10 ** 9, 10 ** 9), follow_symlinks=False)
            os.utime(self.root, (10 ** 9, 10 ** 9))

        def reset(self, name):
            self.root = Path(self.temp.name).resolve() / name
            self.root.mkdir(parents=True)

        def store_manifest(self, unmanaged=()):
            import _opf_init
            manifest = tomllib.loads(_opf_init.build_manifest())
            manifest["unmanaged"]["paths"] = list(unmanaged)
            self.write(".working/toml/manifest.toml", emit_checked(manifest).encode("utf-8"))

        def refused(self, text):
            result = discover_sources(self.root)
            self.assertEqual(result.status, store.CANNOT_EVALUATE, result.findings)
            self.assertEqual((result.proposals, result.searched, result.ruled_out), ((), (), ()))
            self.assertTrue(any(text in finding for finding in result.findings), result.findings)

        def resolver_reads(self):
            seen = []
            real = store._read_store_bytes_contained

            def spy(fd, rel, *metered):
                seen.append(rel)
                return real(fd, rel, *metered)
            return seen, mock.patch.object(store, "_read_store_bytes_contained", spy)

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

        def test_proposals_feed_investigate_only_when_passed(self):
            self.write("TODO.md")
            self.write("docs/notes.md", b"# Decision log\n")
            result = self.discover()
            self.assertEqual(self.paths(result.proposals), ["TODO.md", "docs/notes.md"])
            # Discovery authorizes nothing: with no sources passed, investigation has no candidate.
            bare = planning.investigate(self.root, sources=[])
            self.assertEqual(tomllib.loads(bare.observation.decode())["candidates"], [])
            # Only the proposal the caller passes becomes a source. The prompt has the adopter confirm it
            # first; nothing in code checks that confirmation.
            confirmed = planning.investigate(self.root, sources=["TODO.md"])
            self.assertEqual(confirmed.status, store.VALID, confirmed.findings)
            self.assertEqual(tomllib.loads(confirmed.observation.decode())["candidates"], ["TODO.md"])

        def test_linked_oversized_special_and_bad_names_ruled_out(self):
            self.write("TODO.md")
            os.link(self.root / "TODO.md", self.root / "backlog.md")
            self.write("big/todo.txt", b"x" * 64)
            os.mkfifo(self.root / "todo.fifo")
            self.write("TODO\n.md")
            os.close(os.open(os.fsencode(str(self.root)) + b"/todo\xe9.md", os.O_CREAT | os.O_WRONLY, 0o644))
            with mock.patch.object(module, "MAX_FILE_BYTES", 32):
                result = self.discover()
            self.assertEqual(result.proposals, ())
            linked = "hard-linked file, not read (investigate refuses a linked file)"
            self.assertEqual(result.ruled_out, (
                ("TODO\n.md", "name outside the contained-path grammar, so investigate cannot take it"),
                ("TODO.md", linked),
                ("backlog.md", linked),
                ("big/todo.txt", "over the investigation byte bound, not read (investigate refuses it)"),
                ("todo.fifo", "special file, not opened"),
                ("todo\udce9.md", "name is not UTF-8, so investigate cannot take it"),
            ))
            self.assertEqual(result.searched, (".", "big"))

        def test_case_and_normalization_variants_ruled_out(self):
            self.store_manifest(["notes/private", "notes/caf\u00e9"])
            for rel in (".WORKING/TODO.md", ".Git/TODO.md", "Notes/Private/TODO.md", "notes/private/TODO.md",
                        "notes/cafe\u0301/TODO.md", "notes/TODO.md", "changelog.md"):
                self.write(rel)
            result = self.discover()
            # A case variant of another inventoried path compares byte-exact, as the planner compares it.
            self.assertEqual(self.paths(result.proposals), ["changelog.md", "notes/TODO.md"])
            unmanaged = "registered [unmanaged]: ordinary tooling must not read it (spec 14.2)"
            # A variant is ruled out under its own reason, never reported as the registered path.
            variant = ("a case or normalization variant of an [unmanaged] path, never entered (a "
                       "case-insensitive filesystem may alias it to the registered path; investigation "
                       "compares byte-exact)")
            self.assertEqual(dict(result.ruled_out), {
                ".Git": "version-control or AIQT control area, never entered",
                ".WORKING": "a case or normalization variant of .working, never entered (a case-insensitive "
                            "filesystem aliases it to the store tree)",
                ".working": "inventoried by investigate (a detection root or deliverable destination)",
                "Notes/Private": variant,
                "notes/cafe\u0301": variant,
                "notes/private": unmanaged,
            })
            self.assertEqual(result.searched, (".", "Notes", "notes"))

        def test_store_resolution_reads_and_git_refusal(self):
            self.store_manifest()
            for rel in (".working/archive/old.md", ".working/journals/x", ".working/notes.md"):
                self.write(rel)
            os.symlink(self.root / ".working/archive", self.root / ".working/linked")
            seen, spy = self.resolver_reads()
            with spy:
                result = self.discover()
            # Exactly the stated reads: the scope check reads both pointer files, the resolver reads the
            # local one and, with no local one present, the committed one, and manifest.toml in each immediate
            # real subdirectory of .working, control-area names included.
            manifests = ["archive/manifest.toml", "journals/manifest.toml", "toml/manifest.toml"]
            self.assertEqual(sorted(seen), sorted([".opf.toml", ".opf.local.toml"] * 2 + manifests))
            self.assertEqual(result.searched, (".",))
            # A local override present: the resolver never reads the committed pointer (spec 4.3).
            self.write(".opf.local.toml", b"[store]\ntarget = \"dir:.\"\n")
            seen, spy = self.resolver_reads()
            with spy:
                self.discover()
            self.assertEqual(sorted(seen), sorted([".opf.toml"] + [".opf.local.toml"] * 2 + manifests))
            (self.root / ".opf.local.toml").unlink()
            # A .git file (a submodule gitfile) is never probed into, so it is admitted.
            self.write(".working/.git", b"gitdir: elsewhere\n")
            self.assertEqual(self.discover().status, store.VALID)
            for name in (".Git", ".aiqt"):
                with self.subTest(name=name):
                    (self.root / ".working" / name).mkdir()
                    seen, spy = self.resolver_reads()
                    with spy:
                        self.refused("discovery never enters a .git or .aiqt component")
                    self.assertEqual(seen, [])
                    (self.root / ".working" / name).rmdir()

        def measured(self):
            """One VALID discovery run measured by watching os.scandir and os.read, never from STORE_LISTINGS
            or the charging code: (listings of .working, bytes read from store-resolution inputs, all bytes
            read)."""
            working = (self.root / ".working").stat()
            candidates = [self.root / ".opf.toml", self.root / ".opf.local.toml"]
            candidates += list((self.root / ".working").glob("*/manifest.toml"))
            inputs = {(path.lstat().st_dev, path.lstat().st_ino) for path in candidates if path.exists()}
            real_scandir, real_read = os.scandir, os.read
            listings, input_bytes, all_bytes = [0], [0], [0]

            def scandir(target):
                if isinstance(target, int):
                    st = os.fstat(target)
                    listings[0] += (st.st_dev, st.st_ino) == (working.st_dev, working.st_ino)
                return real_scandir(target)

            def read(fd, size):
                block = real_read(fd, size)
                st = os.fstat(fd)
                if (st.st_dev, st.st_ino) in inputs:
                    input_bytes[0] += len(block)
                all_bytes[0] += len(block)
                return block
            with mock.patch.object(os, "scandir", scandir), mock.patch.object(os, "read", read):
                self.discover()
            return listings[0], input_bytes[0], all_bytes[0]

        def test_resolution_charged_to_budgets(self):
            def resolved():
                self.store_manifest()
                self.write(".opf.toml", b"[store]\ntarget = \"dir:.\"\n")

            def unresolved():
                (self.root / ".working").mkdir()

            # A store the resolver resolves is listed by discovery and the resolver; one it cannot evaluate is
            # listed a third time by the planner existence proof. The charge per .working entry is the most
            # listings any outcome makes, counted here, and each fixture also reads a heading (the walk read
            # and the recheck read), so a store charge that is not carried into the walk budget is seen.
            fixtures = {}
            for label, build in (("resolved", resolved), ("unresolved", unresolved)):
                self.reset(label)
                build()
                (self.root / ".working/a").mkdir()
                (self.root / ".working/b").mkdir()
                self.write("TODO.md")
                self.write("notes.md", b"# TODO\n")
                fixtures[label] = (self.root, self.measured())
            self.assertEqual(sorted(found[0] for _root, found in fixtures.values()), [2, 3])
            most = max(found[0] for _root, found in fixtures.values())
            for label, (root, (_listings, input_bytes, all_bytes)) in fixtures.items():
                self.root = root
                self.assertEqual(all_bytes - input_bytes, 2 * len(b"# TODO\n"))
                working = os.listdir(root / ".working")
                top = os.listdir(root)
                charge = {"MAX_ENTRIES": most * len(working),
                          "MAX_PATH_BYTES": most * sum(len(".working/" + name) for name in working),
                          "MAX_TOTAL_BYTES": input_bytes}
                walk = {"MAX_ENTRIES": len(top), "MAX_PATH_BYTES": sum(len(name) for name in top),
                        "MAX_TOTAL_BYTES": all_bytes - input_bytes}
                for name in ("MAX_ENTRIES", "MAX_PATH_BYTES", "MAX_TOTAL_BYTES"):
                    with self.subTest(store=label, bound=name):
                        with mock.patch.object(module, name, charge[name] + walk[name]):
                            self.assertEqual(self.paths(self.discover().proposals), ["TODO.md", "notes.md"])
                        with mock.patch.object(module, name, charge[name] + walk[name] - 1):
                            self.refused("bound reached")
                        # A bound the resolution charge alone exceeds trips before the resolver reads
                        # anything.
                        seen, spy = self.resolver_reads()
                        with spy, mock.patch.object(module, name, charge[name] - 1):
                            self.refused("bound reached")
                        self.assertEqual(seen, [])

        def grown_read(self, rel, text, nth=1):
            """Discover while `rel` grows past the resolver cap just before its `nth` open, after the lstat
            that charged it; require a refusal naming `text`; return the bytes os.read took from the file
            from that open on."""
            path = self.root / rel
            before = path.stat()
            real_open, real_read = os.open, os.read
            opens, taken = [], [0]

            def grow_then_open(target, *args, **kwargs):
                if target == path.name:
                    opens.append(target)
                    if len(opens) == nth:
                        with open(path, "ab") as handle:
                            handle.write(b"#" + b"x" * (store.MAX_STORE_READ_BYTES + 64))
                return real_open(target, *args, **kwargs)

            def counted_read(fd, size):
                block = real_read(fd, size)
                st = os.fstat(fd)
                if len(opens) >= nth and (st.st_dev, st.st_ino) == (before.st_dev, before.st_ino):
                    taken[0] += len(block)
                return block
            with mock.patch.object(os, "open", grow_then_open), mock.patch.object(
                    os, "supports_dir_fd", os.supports_dir_fd | {grow_then_open}), mock.patch.object(
                    os, "read", counted_read):
                self.refused(text)
            self.assertGreaterEqual(len(opens), nth)
            return taken[0]

        def test_resolution_reads_stop_at_charge_and_cap(self):
            pointer = b"[store]\ntarget = \"dir:.\"\n"
            unresolved = "unresolved store manifest requires repair"
            self.store_manifest()
            manifest = (self.root / ".working/toml/manifest.toml").stat().st_size
            size = len(pointer)
            # Each grown store input is read no further than the charge left when its read starts. The
            # scope check reads .opf.toml, then .opf.local.toml; the resolver reads .opf.local.toml, or
            # .opf.toml when none is present, then the manifests. Each present pointer is charged twice,
            # each manifest once.
            for label, rel, nth, local, left, text in (
                    ("manifest", ".working/toml/manifest.toml", 1, False, manifest, unresolved),
                    ("pointer-scope", ".opf.toml", 1, False, 2 * size + manifest, "exceeds the"),
                    ("pointer-resolver", ".opf.toml", 2, False, size + manifest, unresolved),
                    ("local-resolver", ".opf.local.toml", 2, True, 2 * size + manifest, unresolved)):
                with self.subTest(grown=label):
                    self.reset(label)
                    self.store_manifest()
                    self.write(".opf.toml", pointer)
                    if local:
                        self.write(".opf.local.toml", pointer)
                    self.assertLessEqual(self.grown_read(rel, text, nth), left)
            # With more charge left than the resolver cap, the read stops at the cap.
            self.reset("cap")
            self.store_manifest()
            self.write(".working/zz/manifest.toml", b"[x]\n#" + b"y" * manifest + b"\n")
            cap = manifest + 16
            with mock.patch.object(store, "MAX_STORE_READ_BYTES", cap):
                self.discover()
                self.assertLessEqual(self.grown_read(".working/toml/manifest.toml", unresolved), cap)

        def test_same_stamp_change_to_heading_proposal(self):
            import mmap
            changed = "proposed file contents changed during discovery: " + repr("notes.md")
            self.write("notes.md", b"# TODO\n")
            real_signal, real_read = _signal, os.read
            walked = []

            def after_walk(fd, name, before, budget):
                found = real_signal(fd, name, before, budget)
                walked.append(name)
                return found

            def altered(fd, size):
                block = real_read(fd, size)
                return b"# NOPE" + block[6:] if walked and block.startswith(b"# TODO") else block
            # The recheck reads the prefix of a file proposed for its heading again: bytes that differ from
            # those the walk read refuse, every stamp field equal.
            with mock.patch.object(module, "_signal", after_walk), mock.patch.object(os, "read", altered):
                self.refused(changed)
            # A write through a shared writable mapping whose page is already dirty takes no write fault, so
            # mtime and ctime need not move; it refuses whichever recheck sees it.
            self.reset("mapped")
            self.write("notes.md", b"# TODO\n")
            equal = []
            with open(self.root / "notes.md", "r+b") as handle, mmap.mmap(handle.fileno(), 0) as view:
                view[0:1] = b"#"

                def mapped(fd, name, before, budget):
                    found = real_signal(fd, name, before, budget)
                    view[2:6] = b"NOPE"
                    equal.append(planning._stamp(os.stat(self.root / "notes.md")) == planning._stamp(before))
                    return found
                with mock.patch.object(module, "_signal", mapped):
                    result = discover_sources(self.root)
            self.assertEqual((result.status, result.proposals), (store.CANNOT_EVALUATE, ()))
            if equal == [True]:
                self.assertEqual(result.findings, (changed,))

        def test_change_after_final_observation_escapes(self):
            import mmap
            stale = (("notes.md", "heading 'TODO' looks like a TODO list"),)
            real_read_prefix, real_read = _read_prefix, os.read
            # Discovery compares samples, not a snapshot (module docstring). A write through a shared writable
            # mapping whose page is already dirty, made to a heading prefix during the recheck read after its
            # bytes are returned, or after that read, escapes: the run reads VALID with the stale proposal. A
            # change that turns this red detects more than the docstring and the gate residue state, so
            # restate them with it.
            for label in ("during-recheck-read", "after-recheck-read"):
                with self.subTest(change=label):
                    self.reset(label)
                    self.write("notes.md", b"# TODO\n")
                    target = self.root / "notes.md"
                    inode = target.stat().st_ino
                    reads, equal = [], []
                    with open(target, "r+b") as handle, mmap.mmap(handle.fileno(), 0) as view:
                        view[0:1] = b"#"

                        def write():
                            before = planning._stamp(os.stat(target))
                            view[2:6] = b"NOPE"
                            equal.append(planning._stamp(os.stat(target)) == before)

                        def read(fd, size):
                            block = real_read(fd, size)
                            if os.fstat(fd).st_ino == inode:
                                reads.append(len(block))
                                if label == "during-recheck-read" and len(reads) == 2:
                                    write()
                            return block

                        def read_prefix(fd, name, before, budget):
                            data = real_read_prefix(fd, name, before, budget)
                            if label == "after-recheck-read" and len(reads) == 2 and not equal:
                                write()
                            return data
                        with mock.patch.object(os, "read", read), mock.patch.object(
                                module, "_read_prefix", read_prefix):
                            result = discover_sources(self.root)
                    if label == "during-recheck-read" and equal == [False]:
                        # A filesystem where the mapping write moves a stamp refuses at the closing fstat.
                        self.assertEqual((result.status, result.proposals, result.findings), (
                            store.CANNOT_EVALUATE, (), ("file changed during read: " + repr("notes.md"),)))
                    else:
                        self.assertEqual((result.status, result.proposals), (store.VALID, stale),
                                         result.findings)
                    # Not vacuous: the walk read and the recheck read, each the whole file, and the one write.
                    self.assertEqual((reads, len(equal)), ([len(b"# TODO\n")] * 2, 1))
                    self.assertEqual(target.read_bytes(), b"# NOPE\n")

        def test_unmanaged_refusals(self):
            self.write("TODO.md")
            self.write(".working/old/manifest.toml", b"[x]\n")
            self.write(".opf.toml", b"[store]\ntarget = \"dir:.\"\n")
            for unmanaged, text in (
                    ([".working/toml"], "overlaps a reserved store path"),
                    ([".working/imports/x"], "overlaps a reserved store path"),
                    (["vendor/.git"], ".git is outside adoption investigation"),
                    (["a/../b"], "invalid contained file/directory path"),
                    ([".working/old"], "store-resolution input " + repr(".working/old/manifest.toml")),
                    ([".working/OLD/manifest.toml"],
                     "store-resolution input " + repr(".working/old/manifest.toml")),
                    ([".opf.toml"], "store-resolution input " + repr(".opf.toml"))):
                with self.subTest(unmanaged=unmanaged):
                    self.store_manifest(unmanaged)
                    self.refused(text)
            # The planner refuses the reserved overlap through the same check.
            self.store_manifest([".working/toml"])
            observed = planning.investigate(self.root, sources=[])
            self.assertEqual(observed.findings, ("unmanaged exclusion overlaps a reserved store path",))
            # An [unmanaged] entry under .working that holds no candidate manifest is admitted.
            self.store_manifest([".working/notes"])
            self.write(".working/notes/a.md")
            self.assertEqual(self.paths(self.discover().proposals), ["TODO.md"])

        def test_older_manifest_refusal_names_path(self):
            # The below-supported spec_version refusal read through source discovery names the
            # resolved manifest's own path (validate_manifest's manifest_rel); falling back to
            # the unnamed "the store manifest" wording is red.
            import _opf_init
            self.write("TODO.md")
            old_manifest = _opf_init.build_manifest().replace(
                'spec_version = "' + store.SPEC_VERSION + '"', 'spec_version = "1.1.0"')
            self.assertIn('spec_version = "1.1.0"', old_manifest)
            self.write(".working/toml/manifest.toml", old_manifest.encode("utf-8"))
            self.refused("'.working/toml/manifest.toml' declares [opf].spec_version '1.1.0'")

        def test_resolved_manifest_and_root_refusals(self):
            self.write("TODO.md")
            self.write(".working/toml/manifest.toml", b"[opf]\nstandard = \"opf\"\n")
            self.refused("resolved manifest is not valid")
            self.store_manifest()
            with mock.patch.object(module, "MAX_FILE_BYTES", 100):
                self.refused("resolved manifest exceeds byte bound")
            for bad in (5, None, "relative/root", str(self.root) + "/../product"):
                with self.subTest(root=bad):
                    self.assertEqual(discover_sources(bad).status, store.CANNOT_EVALUATE)

        def test_scope_parse_failure_is_cannot_evaluate(self):
            import types
            # tomllib raises a bare ValueError, RecursionError and MemoryError
            # (F-TOML-BARE-VALUEERROR-CLASS); injected at this module binding, only the two _scope
            # parses see them. Each class on each branch is cannot-evaluate under the site text, with
            # no proposals (refused checks the empty tuple): a scope that failed open would instead
            # propose notes/private/TODO.md, registered [unmanaged] on the resolved branch.
            for branch, text in (("resolved", "resolved manifest does not parse: injected"),
                                 ("generated", "generated store manifest does not parse: injected")):
                for kind in (ValueError, RecursionError, MemoryError):
                    with self.subTest(branch=branch, kind=kind.__name__):
                        self.reset(branch + "-" + kind.__name__)
                        if branch == "resolved":
                            self.store_manifest(["notes/private"])
                        self.write("notes/private/TODO.md", b"- secret\n")
                        self.write("notes/TODO.md")
                        parses = []

                        def boom(data, kind=kind):
                            parses.append(data)
                            raise kind("injected {} at the _scope parse".format(kind.__name__))
                        with mock.patch.object(module, "tomllib", types.SimpleNamespace(loads=boom)):
                            self.refused(text)
                        self.assertEqual(len(parses), 1)

        def test_change_during_discovery_is_cannot_evaluate(self):
            real_signal, real_ruled_out, real_open, real_read = _signal, _ruled_out, os.open, os.read

            def tree():
                self.write("TODO.md")
                self.write("plain.md", b"text\n")
                self.write("a/keep.md", b"text\n")
                self.write("b/TODO.md")

            def append(rel):
                def effect():
                    with open(self.root / rel, "ab") as handle:
                        handle.write(b"- more\n")
                return effect

            def create(rel):
                return lambda: self.write(rel)

            def move():
                os.rename(self.root / "b/TODO.md", self.root / "a/TODO.md")

            def after_signal(target, effect):
                def hooked(fd, name, before, budget):
                    found = real_signal(fd, name, before, budget)
                    if name == target:
                        effect()
                    return found
                return mock.patch.object(module, "_signal", hooked)

            def before_ruling(target, effect):
                def hooked(path, *args):
                    if path == target:
                        effect()
                    return real_ruled_out(path, *args)
                return mock.patch.object(module, "_ruled_out", hooked)

            @contextlib.contextmanager
            def os_hook(attribute, matches, effect):
                fired = []
                real = real_open if attribute == "open" else real_read

                def hooked(first, *args, **kwargs):
                    if not fired and matches(first):
                        fired.append(True)
                        effect()
                    return real(first, *args, **kwargs)
                with mock.patch.object(os, attribute, hooked), mock.patch.object(
                        os, "supports_dir_fd", os.supports_dir_fd | {hooked}):
                    yield

            def reading(rel):
                return lambda fd: os.fstat(fd).st_ino == (self.root / rel).stat().st_ino

            changed = "entry changed during discovery: "
            cases = (
                ("proposed-edit", after_signal("TODO.md", append("TODO.md")), changed + repr("TODO.md")),
                ("unproposed-edit", after_signal("plain.md", append("plain.md")), changed + repr("plain.md")),
                ("moved", before_ruling("b", move), changed + repr("a")),
                ("dir-before", os_hook("open", lambda path: path == "a", create("a/new.md")),
                 "directory changed before enumeration"),
                ("dir-during", after_signal("keep.md", create("a/new.md")),
                 "directory changed during enumeration"),
                ("root", after_signal("plain.md", create("new.md")), "product root changed during discovery"),
                ("file-open", os_hook("open", lambda path: path == "plain.md", append("plain.md")),
                 "non-regular, linked, or changed file"),
                ("file-read", os_hook("read", reading("plain.md"), append("plain.md")),
                 "file changed during read"),
            )
            for label, hook, text in cases:
                with self.subTest(change=label):
                    self.reset(label)
                    tree()
                    self.age()
                    with hook:
                        self.refused(text)
            store_changed = "store resolution input changed during discovery: "
            for label, effect, path in (
                    ("manifest-edit", append(".working/toml/manifest.toml"), ".working/toml/manifest.toml"),
                    ("machine-dir", create(".working/toml/new.md"), ".working/toml"),
                    ("working-dir", create(".working/new/x.md"), ".working"),
                    ("pointer-edit", append(".opf.toml"), ".opf.toml")):
                with self.subTest(change=label):
                    self.reset(label)
                    tree()
                    self.store_manifest()
                    self.write(".opf.toml", b"[store]\ntarget = \"dir:.\"\n")
                    self.age()
                    with after_signal("plain.md", effect):
                        self.refused(store_changed + repr(path))
            with self.subTest(change="root-path"):
                outer = Path(self.temp.name).resolve() / "outer"
                self.root = outer / "product"
                tree()

                def swap():
                    os.rename(outer, outer.parent / "outer-moved")
                    (outer / "product").mkdir(parents=True)
                with after_signal("plain.md", swap):
                    result = discover_sources(self.root)
                self.assertEqual(result.status, store.CANNOT_EVALUATE)
                self.assertEqual(result.findings, ("product root path no longer names the walked directory",))

        def test_report_escapes_names(self):
            self.write("TODO.md")
            self.write("TODO\npropose forged.md: looks like a TODO list.md")
            os.close(os.open(os.fsencode(str(self.root)) + b"/todo\xe9.md", os.O_CREAT | os.O_WRONLY, 0o644))
            self.write("docs/notes.md", b"# TODO\x1b\n")
            result = self.discover()
            text = report(result)
            text.encode("ascii")
            lines = text.split("\n")
            self.assertEqual(len(lines),
                             1 + len(result.proposals) + len(result.ruled_out) + len(result.searched))
            self.assertEqual(lines[0], "VALID")
            self.assertEqual([line for line in lines if line.startswith("propose ")], [
                "propose TODO.md: name " + repr("TODO.md") + " looks like a TODO list",
                "propose docs/notes.md: heading " + repr("TODO").rstrip(chr(39)) + "\\\\x1b" + chr(39)
                + " looks like a TODO list"])
            self.assertIn("ruled out TODO\\npropose forged.md: looks like a TODO list.md: name outside the "
                          "contained-path grammar, so investigate cannot take it", lines)
            self.assertIn("ruled out todo\\udce9.md: name is not UTF-8, so investigate cannot take it", lines)
            self.assertEqual(report(DiscoverResult(store.CANNOT_EVALUATE, ["a\nb\\c"])),
                             "CANNOT-EVALUATE\nfinding: a\\nb\\\\c")

    suite = unittest.defaultTestLoader.loadTestsFromTestCase(DiscoveryTests)
    expected = suite.countTestCases()
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() and result.testsRun == expected else 1


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-test"]:
        sys.exit(self_test())
    print("usage: _opf_adopt_discover.py --self-test (call discover_sources and report from Python)",
          file=sys.stderr)
    sys.exit(2)
