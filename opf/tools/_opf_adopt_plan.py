#!/usr/bin/env python3
"""Read-only OPF adoption investigation and planning (PR-B).

Only explicit source roots, .working, and DETECTION_ROOTS are inventoried.
.git is never entered. A resolved machine subtree and declared unmanaged paths
are exclusions, recorded in the inventory; foreign content inside those excluded
subtrees is NOT covered. Companion stores refuse in this slice. Detection is by
path only: a candidate is neither a parsed registration nor a working pipeline.
Ancestry marks re-adoption when a pointer, a resolved store manifest, or unresolved
content at an OPF-reserved .working name (ANCESTRY_RESERVED) is present, so store
debris never reads as a zero-seedable first adoption. first-adoption only means none
of these: git history is not read, and a manifestless store under any other .working
name reads as foreign content. Durable OPF history outside .working also reads as
first-adoption on its own, including: root import-promotion state (.aiqt/import,
.aiqt/import/journal, .aiqt/import-archive), the record journal (.aiqt/record/journal)
and the .gitignore opf-managed block.

VALID means an inert, digest-bound proposal, NEVER permission/readiness to apply.
No release is trusted, acceptance verified, hook activated, or transaction run.
The frozen plan is `opf.adoption.plan/v2` (spec 14.1). A migrate decision
resolves: the source is kept for post-adoption import, preserved under the
run's adoption preimage home, named in the import scope, and given no import
op; import references are refused as decision inputs. A candidate
at a planned managed destination (a declared view of the resolved or default
manifest, or a path under the planned machine store) is occupying (spec 14.2):
keep refuses, and any other disposition is preserved under the adoption archive.
Occupancy covers only those declared views and machine-store paths. A pre-existing
file at a deliverable destination outside .working (the product-root VERSION, each
declared deliverable such as CHANGELOG.md) is a candidate, so it takes an explicit
disposition before init-store, but it is not classified as occupying. The store
control area (.working/archive, imported, staging, journals and the imports tree)
is never adopter content in any homes generation (spec 14.2): it is inventoried,
never a candidate, and a decision naming it refuses.
A move decision without a destination plans the spec 14.2 default,
.working/archive/moved/<source-path>, from the source row's own path, never stripped;
its store base must be observed (an excluded or unobserved nearest ancestor is
cannot-evaluate) and an occupied default destination is a collision. Every move
destination and archive preservation copy is checked against the one
protected-destination predicate the apply shell shares (_opf_adopt.protected_destination).
DISCLOSED-STRICTER (disclose-guard-residuals): spec 14.2 requires only the default Move
to refuse a store resolved outside the product root; investigation refuses every plan,
and so every Move, default or explicit, whose committed or local pointer names a store
at another root, whether or not that store resolves.
Every planned creation (move and preservation destinations, init-store,
render-views and pack members, the receipt) needs observed absence unless an
occupying source is archived from it first; a replacement needs matching
observed bytes, and each keep's manifest registration is bound to the observed or
scaffolded manifest it rewrites. A store that does not resolve needs init-store,
and init-store refuses a store that already resolves; render-views is required
whenever views are declared and writes exactly the planned declared views.
The revision, release and anchor, prompt pack, enforcement contents and skip
policy are caller inputs, shape-checked and digest-bound, never verified here;
enforcement members are tied to the ops installing them, never to a real pack.
Inventory reads detect ordinary concurrent edits, not a coherent filesystem
snapshot or an adversarial writer restoring stat values. Re-observe at apply.
"""
import copy
import datetime
import hashlib
import os
import stat
import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _opf_adopt as schema  # noqa: E402
import _opf_store as store  # noqa: E402
from _opf_emit import EmitError, emit_checked  # noqa: E402

OBS_FORMAT = "opf.adoption.observation/v1"
MAX_ENTRIES = 10000
MAX_DEPTH = 32
MAX_PATH_BYTES = 1024 * 1024
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 64 * 1024 * 1024
MAX_ARTIFACT_BYTES = 16 * 1024 * 1024
DETECTION_ROOTS = (
    ".opf.toml", ".opf.local.toml", ".working",
    "AGENTS.md", "CLAUDE.md", "GEMINI.md", ".claude", ".cursor", ".gemini", ".codex",
    ".github/workflows", ".gitlab-ci.yml", "Jenkinsfile", ".circleci", "azure-pipelines.yml",
    "VERSION", "CHANGELOG.md", "release-notes.toml",
)
# OPF-reserved .working names, from the store module's own constants: the standard machine
# subdirectory and every reserved store control subdirectory. Content at any is ancestry.
ANCESTRY_RESERVED = tuple(sorted(
    store.WORKING_DIRNAME + "/" + name
    for name in (store.DEFAULT_MACHINE_SUBDIR,) + store.RESERVED_MACHINE_SUBDIRS))
RESIDUALS = (
    "Only the enumerated scope is covered; detection is by path, not semantics.",
    "Machine-store and unmanaged exclusions are not inspected for foreign files.",
    "No trust, acceptance, store-validity, registration, or behaviour verdict.",
    "Additional ops are proposals; cross-op preconditions and postimages are not proved.",
    "Resolver reads retain the resolver limits; inventory caps are not a process sandbox.",
    "No coherent snapshot; apply must recheck inventory, preimages and absences.",
    "No commit, merge, network, journal, import staging, rendering or hook effects.",
    "Ancestry reads pointers, the resolved manifest and reserved .working names; git history "
    "and a manifestless store under another .working name are not read as ancestry. "
    "Durable OPF history outside .working also reads as first-adoption on its own, including: "
    "root import-promotion state (.aiqt/import, .aiqt/import/journal, .aiqt/import-archive), "
    "the record journal (.aiqt/record/journal) and the .gitignore opf-managed block.",
)


class PlanError(ValueError):
    pass


class AdoptResult:
    __slots__ = ("status", "findings", "observation", "inventory", "plan", "unresolved")

    def __init__(self, status, findings=(), observation=None, inventory=None,
                 plan=None, unresolved=()):
        self.status = status
        self.findings = tuple(findings)
        self.observation = observation       # immutable canonical TOML bytes, or None
        self.inventory = inventory           # observation + attributed decisions, or None
        self.plan = plan                     # immutable canonical TOML bytes, or None
        self.unresolved = tuple(unresolved)  # source paths, never default dispositions


def _digest(data):
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _emit(document):
    data = emit_checked(document).encode("utf-8")
    if len(data) > MAX_ARTIFACT_BYTES:
        raise PlanError("canonical artefact exceeds byte bound")
    return data


def _seal(document, field):
    out = dict(document)
    out[field] = _digest(_emit(document))  # exclude ONLY its own digest field
    return _emit(out)


def _path(value):
    if not schema._is_contained_filepath(value):
        raise PlanError("invalid contained file/directory path: {!r}".format(value))
    value.encode("utf-8")  # surrogate filenames refuse rather than being normalized
    if ".git" in value.split("/"):
        raise PlanError(".git is outside adoption investigation")
    return value


def _under(path, parent):
    return path == parent or path.startswith(parent + "/")


def _stamp(st):
    return (st.st_dev, st.st_ino, st.st_mode, st.st_nlink, st.st_size,
            st.st_mtime_ns, st.st_ctime_ns)


def _read(fd, name, before, budget):
    """Bounded, no-follow, nonblocking regular-file read, tied to the opened inode."""
    child = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    try:
        opened = os.fstat(child)
        if (not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1
                or _stamp(opened) != _stamp(before)):
            raise PlanError("non-regular, linked, or changed file: {!r}".format(name))
        if opened.st_size > MAX_FILE_BYTES:
            raise PlanError("file exceeds byte bound: {!r}".format(name))
        chunks = []
        size = 0
        while True:
            block = os.read(child, min(65536, MAX_FILE_BYTES - size + 1))
            if not block:
                break
            size += len(block)
            budget[0] += len(block)
            if size > MAX_FILE_BYTES or budget[0] > MAX_TOTAL_BYTES:
                raise PlanError("inventory exceeds byte bounds")
            chunks.append(block)
        if _stamp(os.fstat(child)) != _stamp(opened):
            raise PlanError("file changed during read: {!r}".format(name))
        if _stamp(os.stat(name, dir_fd=fd, follow_symlinks=False)) != _stamp(opened):
            raise PlanError("file name changed during read: {!r}".format(name))
        return b"".join(chunks)
    finally:
        store._journal._close_fd_propagating(child)


def _roots(sources):
    if type(sources) not in (list, tuple):
        raise PlanError("sources must be an explicit list of contained paths")
    if len(sources) > MAX_ENTRIES:
        raise PlanError("too many source/target roots")
    paths = sorted(_path(p) for p in sources)
    if len(paths) > MAX_ENTRIES or len(paths) != len(set(paths)):
        raise PlanError("too many or duplicate source roots")
    for i, path in enumerate(paths):
        if any(_under(path, earlier) for earlier in paths[:i]):
            raise PlanError("overlapping source roots")
    return paths


def _inventory(root, sources, targets):
    store._journal.require_containment()
    root_fd = store._open_dir_nofollow(root)
    budget = [0]
    try:
        root_stat = os.fstat(root_fd)
        # Bind discovery to this product only. Inspect BOTH pointers, even when
        # the local override would hide a malformed committed pointer.
        traces = []
        for pointer in (store.POINTER_REL, store.LOCAL_POINTER_REL):
            target = store._read_pointer_target(root_fd, pointer)
            if target is not None and store._target_store_root(target, root) != root:
                raise PlanError("companion/remote store requires separately scoped investigation")
            if target is not None:
                traces.append(pointer)
        # Descriptor-bound resolution (round-5 defect 1): resolve through the SAME held root
        # descriptor every inventory read uses -- pointer reads through root_fd, discovery holding the
        # .working listing descriptor and reading each listed manifest beneath it -- never a path
        # re-resolution, so a root or .working swapped after the open above is never read. Discovery
        # returns the resolved manifest's exact bytes, so it is never re-opened by path below.
        resolution, manifest_raw = store.resolve_store_fd(root_fd, root)
        excluded = []
        if resolution.status == store.CANNOT_EVALUATE:
            # Prove that this is foreign content with NO candidate store manifest
            # before reading any of its files. Do not infer this from resolver prose.
            working = store._journal._lstat_at(root_fd, store.WORKING_DIRNAME)
            if working is None or not stat.S_ISDIR(working.st_mode):
                raise PlanError("store resolution cannot be evaluated: " + resolution.detail)
            wfd = os.open(store.WORKING_DIRNAME,
                          os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd)
            try:
                count = 0
                with os.scandir(wfd) as listing:
                    for entry in listing:
                        count += 1
                        if count > MAX_ENTRIES:
                            raise PlanError("store discovery exceeds entry bound")
                        st = os.stat(entry.name, dir_fd=wfd, follow_symlinks=False)
                        if stat.S_ISDIR(st.st_mode):
                            child = os.open(entry.name, os.O_RDONLY | os.O_DIRECTORY
                                            | os.O_NOFOLLOW, dir_fd=wfd)
                            try:
                                if store._journal._lstat_at(child, store.MANIFEST_NAME) is not None:
                                    raise PlanError("unresolved store manifest requires repair")
                            finally:
                                store._journal._close_fd_propagating(child)
            finally:
                store._journal._close_fd_propagating(wfd)
        if resolution.status == store.RESOLVED:
            manifest_path = resolution.machine_rel + "/" + store.MANIFEST_NAME
            # The exact bytes discovery already read through the .working listing descriptor,
            # brought under the planner's own byte bounds; a fresh by-path read here could read a
            # tree swapped in after the listing (round-5 defect 1).
            raw = manifest_raw
            if len(raw) > MAX_FILE_BYTES:
                raise PlanError("file exceeds byte bound: {!r}".format(manifest_path))
            budget[0] += len(raw)
            if budget[0] > MAX_TOTAL_BYTES:
                raise PlanError("inventory exceeds byte bounds")
            manifest = tomllib.loads(raw.decode("utf-8"))
            checked = store.validate_manifest(manifest)
            if checked.status != store.VALID:
                raise PlanError("resolved manifest is not valid: " + "; ".join(checked.findings))
            excluded.append({"path": resolution.machine_rel, "reason": "machine-store"})
            # A legacy store (homes 1) reserves only the imports tree and excludes nothing more; homes 2
            # also reserves every store control root and excludes it from investigation (spec 14.2).
            homes = store.homes_generation(manifest)
            control = list(store.store_control_roots(homes))
            for path in manifest.get("unmanaged", {}).get("paths", []):
                # Conservative collision check: do not let an exclusion conceal the
                # machine subtree, a store control root, or a declared view. Fine-grained store
                # membership remains the doctor's job, not an adoption-planner reimplementation.
                path = _path(path)
                reserved = [resolution.machine_rel] + control
                reserved += [v["target"] for v in manifest.get("views", {}).values()]
                if any(_under(path, p) or _under(p, path) for p in reserved):
                    raise PlanError("unmanaged exclusion overlaps a reserved store path")
                excluded.append({"path": path, "reason": "registered-unmanaged"})
            if homes >= 2:
                excluded.extend({"path": path, "reason": "store-control"} for path in control)
            manifest_digest = _digest(raw)
            traces.append(manifest_path)
            view_targets = sorted(v["target"] for v in manifest.get("views", {}).values())
            deliverables = _deliverable_destinations(manifest)
        else:
            homes = 1
            manifest = None
            manifest_path = ""
            manifest_digest = ""
            view_targets = None
            import _opf_init
            deliverables = _deliverable_destinations(tomllib.loads(_opf_init.build_manifest()))
        exclusions = sorted(excluded, key=lambda row: (row["path"], row["reason"]))
        for source in sources + targets:
            if any(_under(source, row["path"]) for row in exclusions):
                raise PlanError("source is in an excluded subtree: {!r}".format(source))

        entries = {}
        path_bytes = [0]

        def visit(parent, name, path, depth):
            if path in entries:
                return
            _path(path)
            path_bytes[0] += len(path.encode("utf-8"))
            if (len(entries) >= MAX_ENTRIES or path_bytes[0] > MAX_PATH_BYTES
                    or depth > MAX_DEPTH):
                raise PlanError("inventory entry/path/depth bound exceeded")
            try:
                before = os.stat(name, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                entries[path] = {"path": path, "kind": "absent"}
                return
            row = {"path": path, "kind": "excluded"}
            entries[path] = row
            if any(_under(path, item["path"]) for item in exclusions):
                return
            if stat.S_ISDIR(before.st_mode):
                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                dir_fd=parent)
                try:
                    if _stamp(os.fstat(child)) != _stamp(before):
                        raise PlanError("directory changed before enumeration")
                    row["kind"] = "directory"
                    names = []
                    with os.scandir(child) as listing:
                        for entry in listing:
                            names.append(entry.name)
                            if len(names) > MAX_ENTRIES:
                                raise PlanError("directory exceeds entry bound")
                    for child_name in sorted(names):
                        visit(child, child_name, path + "/" + child_name, depth + 1)
                    if _stamp(os.fstat(child)) != _stamp(before):
                        raise PlanError("directory changed during enumeration")
                    if _stamp(os.stat(name, dir_fd=parent, follow_symlinks=False)) != _stamp(before):
                        raise PlanError("directory name changed during enumeration")
                finally:
                    store._journal._close_fd_propagating(child)
            elif stat.S_ISREG(before.st_mode):
                # A malformed/ambiguous store must not be scanned as ordinary foreign
                # content: a manifest may contain unmanaged exclusions we cannot trust.
                if (resolution.status != store.RESOLVED
                        and path.startswith(".working/")
                        and path.count("/") == 2 and path.endswith("/" + store.MANIFEST_NAME)):
                    raise PlanError("unresolved store manifest requires repair before investigation")
                data = _read(parent, name, before, budget)
                row.update(kind="file", size=len(data), digest=_digest(data))
            else:
                raise PlanError("symlink or special entry refused: {!r}".format(path))

        requested = sorted(set(sources) | set(targets) | set(DETECTION_ROOTS) | set(deliverables))
        # Walk shortest roots first, avoiding a duplicate read when a source root
        # encloses one of the fixed detection paths.
        for path in sorted(requested, key=lambda p: (p.count("/"), p)):
            if any(_under(path, item["path"]) for item in exclusions):
                continue
            try:
                parent, name = store._journal._open_parent(root_fd, path)
            except FileNotFoundError:
                entries[path] = {"path": path, "kind": "absent"}
                continue
            try:
                visit(parent, name, path, 0)
            finally:
                store._journal._close_fd_propagating(parent)
        for path in sources:
            if entries.get(path, {}).get("kind") in (None, "absent", "excluded"):
                raise PlanError("declared source is unavailable: {!r}".format(path))
        if resolution.status != store.RESOLVED:
            # With no resolved store, content at a reserved name (for example counters and
            # records, or import runs, left without a manifest) is still prior ancestry.
            traces += [path for path in ANCESTRY_RESERVED
                       if entries.get(path, {}).get("kind") in ("file", "directory")]
        # Candidate roots are the explicit sources, .working and the deliverable destinations outside it
        # (spec 14: each pre-existing file there takes a disposition before init-store), outside
        # exclusions. The store control area is never adopter content, in any homes generation (spec
        # 14.2): its entries stay in the inventory with their digests, as OPF control area, and are
        # never candidates.
        candidate_roots = sources + [".working"] + deliverables
        candidates = [
            row["path"] for row in entries.values()
            if row["kind"] == "file"
            and any(_under(row["path"], prefix) for prefix in candidate_roots)
            and not schema._in_control_area(row["path"])
        ]
        # Empty directories are surfaced separately. They are not file operands.
        empty = [
            row["path"] for row in entries.values()
            if row["kind"] == "directory" and row["path"] != ".working"
            and any(_under(row["path"], prefix) for prefix in candidate_roots)
            and not schema._in_control_area(row["path"])
            and not any(p.startswith(row["path"] + "/") for p in entries)
        ]
        check_fd = store._open_dir_nofollow(root)
        try:
            if _stamp(os.fstat(check_fd)) != _stamp(root_stat):
                raise PlanError("product root changed during investigation")
        finally:
            store._journal._close_fd_propagating(check_fd)
        # The homes generation, the resolved store's declared view targets and its parsed manifest are
        # returned beside the observation, never inside it; all come from the same manifest read that
        # fixed the exclusions, whose digest the observation records.
        return {
            "format": OBS_FORMAT,
            "product_root": str(root),
            "scope": requested,
            "sources": sources,
            "targets": targets,
            "exclusions": exclusions,
            "entries": sorted(entries.values(), key=lambda row: row["path"]),
            "candidates": sorted(candidates),
            "empty_directories": sorted(empty),
            "resolution": {
                "status": resolution.status,
                "detail": resolution.detail,
                "pointer_source": resolution.pointer_source or "",
                "machine_rel": resolution.machine_rel or "",
                "manifest_path": manifest_path,
                "manifest_digest": manifest_digest,
            },
            "detections": [
                {"path": path, "kind": entries[path]["kind"],
                 "evidence": "filesystem-entry; candidate only"}
                for path in DETECTION_ROOTS if path in entries
            ],
            # Prior-OPF ancestry, from the same pointer and manifest reads that fixed the
            # exclusions plus the walked reserved names, so the seeded-versus-zero counters
            # choice is plan-visible. Debris is re-adoption, not a third value that a
            # consumer testing only for re-adoption could zero-seed.
            "ancestry": {
                "adoption": "re-adoption" if traces else "first-adoption",
                "traces": traces,
                "evidence": "live pointers, resolved manifest and reserved .working names; "
                            "git history and other .working names are not ancestry. "
                            "Durable OPF history outside .working also reads as first-adoption "
                            "on its own, including: root import-promotion state (.aiqt/import, "
                            ".aiqt/import/journal, .aiqt/import-archive), the record journal "
                            "(.aiqt/record/journal) and the .gitignore opf-managed block",
            },
            "coverage_residuals": list(RESIDUALS),
        }, homes, view_targets, manifest
    finally:
        store._journal._close_fd_propagating(root_fd)


def _deliverable_destinations(manifest):
    """The destinations outside .working whose pre-existing files take a disposition before init-store
    (spec 14, 14.2): the product-root VERSION, each declared deliverable target, and each declared view
    target outside .working, from the resolved manifest or else the default one init-store writes."""
    targets = {"VERSION"}
    targets.update(row["target"] for row in manifest.get("deliverables", {}).values())
    targets.update(row["target"] for row in manifest.get("views", {}).values()
                   if not _under(row["target"], store.WORKING_DIRNAME))
    return sorted(targets)


def investigate(product_root, *, sources, targets=()):
    """No output path: return inert canonical bytes. Required source absence refuses."""
    return _investigate(product_root, sources, targets)[0]


def _investigate(product_root, sources, targets):
    """investigate, plus the resolved store's homes generation (legacy 1 when nothing resolved), its
    declared view targets and its parsed manifest (both None when nothing resolved)."""
    try:
        if not isinstance(product_root, (str, os.PathLike)):
            raise PlanError("product_root must be an absolute path")
        root = Path(product_root)
        if not root.is_absolute() or ".." in root.parts:
            raise PlanError("product_root must be absolute and contain no '..'")
        doc, homes, views, manifest = _inventory(root, _roots(sources), _roots(targets))
        observed = AdoptResult(store.VALID, observation=_seal(doc, "observation_digest"))
        return observed, homes, views, manifest
    except (OSError, ValueError, UnicodeError, RecursionError, EmitError,
            store.StoreError, store._journal.JournalError) as exc:
        return AdoptResult(store.CANNOT_EVALUATE, [str(exc)]), 1, None, None


def _managed_destinations(doc, views):
    """The planned managed destinations a candidate can occupy (spec 14.2), as
    (machine_rel, view_targets). A resolved store contributes its machine store and
    its manifest's declared view targets; otherwise the plan targets the store
    init-store would create: the default machine subdirectory and the default
    manifest's views. The machine subtree is covered whole, as the unmanaged-overlap
    check treats it.

    The store control area is not a managed destination but OPF's own, in every
    homes generation (spec 14.2): investigation never surfaces it as a candidate,
    so no disposition, keep, registration or move selects it (see _decisions).

    DISCLOSED-RESIDUAL (disclose-guard-residuals): occupancy is limited to those
    declared views and machine-store paths. A pre-existing file at a deliverable
    destination outside .working (the product-root VERSION, each declared
    deliverable target such as CHANGELOG.md) is a candidate and so takes an
    explicit disposition before init-store, but it is not classified as
    occupying: a keep there registers [unmanaged] rather than refusing, and a
    move there is accepted once its absence is observed. Release notes are
    detected by path only. Classifying deliverables as managed destinations is
    left to the deliverable registry, not claimed by this planner."""
    if doc["resolution"]["status"] == store.RESOLVED:
        return doc["resolution"]["machine_rel"], frozenset(views)
    import _opf_init
    default = tomllib.loads(_opf_init.build_manifest())
    return (store.WORKING_DIRNAME + "/" + store.DEFAULT_MACHINE_SUBDIR,
            frozenset(v["target"] for v in default["views"].values()))


def _observed_kinds(observation):
    """The observation's entry kinds by path."""
    return {row["path"]: row["kind"] for row in observation["entries"]}


def _observed_absent(kinds, path):
    """Whether the observation proves `path` absent: its own entry is absent, or its
    nearest observed ancestor is absent or a directory the walk enumerated without
    meeting it. An excluded or unobserved ancestor proves nothing, and a file or
    directory at the path, or a file ancestor, is an occupied destination."""
    probe = path
    while True:
        kind = kinds.get(probe)
        if kind is not None:
            return kind == "absent" or (probe != path and kind == "directory")
        if "/" not in probe:
            return False
        probe = probe.rsplit("/", 1)[0]


def _default_move_destination(kinds, path):
    """The spec 14.2 default Move destination of a source, `.working/archive/moved/<source-path>`,
    from the row's own path with its substructure kept (store.moved_dest, never stripped). The planner
    freezes store_root ".", so the store-relative constructor is also the product-relative path. Its
    absence must be observed: an excluded or unobserved nearest ancestor (a homes-2 store excludes its
    control area from investigation) leaves the store base unobservable, which is cannot-evaluate
    naming the source, and an entry at the destination or a file ancestor is an occupied destination,
    a collision never an overwrite."""
    destination = store.moved_dest(path)
    probe = destination
    while probe not in kinds and "/" in probe:
        probe = probe.rsplit("/", 1)[0]
    if kinds.get(probe) in (None, "excluded"):
        raise PlanError("default move destination {!r} of {!r}: the store base cannot be observed, "
                        "so its absence is unproven (cannot-evaluate)".format(destination, path))
    if not _observed_absent(kinds, destination):
        raise PlanError("default move destination {!r} of {!r} is occupied: a collision, never an "
                        "overwrite (spec 14.2)".format(destination, path))
    return destination


def _decisions(observation, decisions, run_id, managed):
    """Per-candidate dispositions -> (disposition ops, plan-v2 source rows, unresolved).

    `managed` is (machine_rel, view_targets) from _managed_destinations. A candidate
    at a declared view or under the planned machine store occupies a managed
    destination (spec 14.2): keep refuses, and any other disposition is preserved
    under the adoption archive, a move included. A move destination and every
    preservation destination alike need observed absence (an occupied destination
    is a collision, never an overwrite). A move without a destination takes the spec
    14.2 default (_default_move_destination). A keep's register-unmanaged row is minted
    here without its manifest digests, which _bind_registrations adds once the
    program is ordered."""
    if type(decisions) is not list:
        raise PlanError("decisions must be a list")
    machine_rel, view_targets = managed
    files = {row["path"]: row for row in observation["entries"] if row["kind"] == "file"}
    kinds = _observed_kinds(observation)
    wanted = set(observation["candidates"])
    seen = set()
    ops = []
    sources = []
    unresolved = set(wanted)
    for row in sorted(decisions, key=lambda r: r.get("path", "") if type(r) is dict
                      and isinstance(r.get("path", ""), str) else ""):
        if type(row) is not dict:
            raise PlanError("decision must be a table")
        required = {"path", "disposition", "actor"}
        optional = {"destination"}
        if not required <= row.keys() or set(row) - required - optional:
            raise PlanError("malformed decision keys")
        path = _path(row["path"])
        # The store control area is OPF's, never adopter content, in every homes generation (spec 14.2):
        # investigation surfaces none of it, so no decision can select, keep, register, move, retire or
        # migrate it, a committed adoption-archive original included.
        if schema._in_control_area(path):
            raise PlanError("decision selects the reserved store control area: {!r} (spec 14.2)".format(
                path))
        if path not in wanted or path in seen or not schema._is_token(row["actor"]):
            raise PlanError("duplicate/out-of-scope decision or missing actor")
        seen.add(path)
        disposition = row["disposition"]
        digest = files[path]["digest"]
        # Occupancy (spec 14.2): a candidate at a declared view or under the planned machine store
        # occupies a managed destination. The store control area never reaches this point (refused
        # above), so the occupancy classification ranges over adopter content only; validate_plan
        # independently refuses a source, kept entry or write in the control area.
        occupying = path in view_targets or _under(path, machine_rel)
        source = {"path": path, "digest": digest, "disposition": disposition, "occupying": occupying}
        if disposition == "keep" and set(row) == required:
            if occupying:
                raise PlanError("keep names a managed destination: {!r}".format(path))
            ops.append({"op": "register-unmanaged", "entry": path,
                        "note": "proposed by " + row["actor"]})
        elif disposition == "retire" and set(row) == required:
            ops.append({"op": "retire-file", "path": path, "preimage_digest": digest})
            source["preservation"] = store.retire_preimage(run_id, path)
        elif disposition == "move":
            # The keyset check above admits exactly required, or required plus a destination.
            if "destination" not in row:
                destination = _default_move_destination(kinds, path)
            else:
                destination = _path(row["destination"])
                # Only observed absence is accepted: the destination's own absent entry, or an
                # absent or walked ancestor. Declare a destination outside the walked roots in
                # targets when investigating, then pass the same targets to plan.
                if not _observed_absent(kinds, destination):
                    raise PlanError("move destination has no observed absence")
            ops.append({"op": "move-file", "source": path,
                        "destination": destination, "source_digest": digest})
            source["preservation"] = store.retire_preimage(run_id, path) if occupying else destination
        elif disposition == "migrate" and set(row) == required:
            # Kept for post-adoption import (spec 14.2): no op and no import reference. Its exact bytes
            # are preserved at apply under the adoption archive, and the import scope names it.
            source["preservation"] = store.retire_preimage(run_id, path)
        else:
            raise PlanError("unknown disposition or incompatible decision fields")
        if "preservation" in source and not _observed_absent(kinds, source["preservation"]):
            raise PlanError("preservation destination has no observed absence: {!r}".format(
                source["preservation"]))
        sources.append(source)
        unresolved.remove(path)
    return ops, sorted(sources, key=lambda r: r["path"]), sorted(unresolved)


def plan(product_root, *, sources, expected_observation_digest, product, decisions,
         ops, now, run_nonce, bindings, targets=()):
    """Re-investigate, bind to the reviewed inventory, then purely freeze the proposal.

    ops is an ordered list of additional PR-A vocabulary rows. It has no authority:
    release and enforcement member digests, generated init and view postimages, hook
    diffs and receipt digests remain unverified inputs to later PRs, though every
    file they name is bound in the effects and checked against the observed tree. No
    generic op can substitute for the explicit per-candidate dispositions below.

    bindings carries exactly the caller-supplied plan-v2 inputs
    (schema.PLAN_BINDING_INPUTS). The store identity comes from the observation
    (the resolved or default machine store and its ancestry); the sources, effects,
    completion roster and import policy are derived here, never supplied.
    """
    observed, homes, views, manifest_model = _investigate(product_root, sources, targets)
    if observed.status != store.VALID:
        return observed
    try:
        doc = tomllib.loads(observed.observation.decode("utf-8"))
        if not schema._is_digest(expected_observation_digest):
            raise PlanError("expected_observation_digest is malformed")
        if doc["observation_digest"] != expected_observation_digest:
            raise PlanError("inventory changed; discuss a fresh observation")
        if (type(now) is not datetime.datetime or type(now.tzinfo) is not datetime.timezone
                or now.utcoffset() != datetime.timedelta(0)):
            raise PlanError("now must be a clock-derived aware UTC datetime")
        if not isinstance(run_nonce, str) or not re_full_nonce(run_nonce):
            raise PlanError("run_nonce must be 16 lowercase hex characters")
        run_id = "adopt-" + now.strftime("%Y%m%dT%H%M%SZ") + "-" + run_nonce
        if type(bindings) is not dict or set(bindings) != set(schema.PLAN_BINDING_INPUTS):
            raise PlanError("bindings must carry exactly the plan-v2 binding inputs")
        if type(ops) is not list:
            raise PlanError("ops must be an ordered list")
        for row in ops:
            checked = schema.validate_op(row, homes=homes)
            if checked.status != store.VALID:
                return AdoptResult(checked.status, checked.findings, observation=observed.observation)
            if row["op"] in ("register-unmanaged", "move-file", "retire-file"):
                raise PlanError("disposition ops must come from attributed decisions")
            for field, value in row.items():
                if schema._FIELD_KINDS.get(field) in ("filepath", "dirpath") and value != ".":
                    _path(value)
            for member in row.get("members", ()):
                _path(member["path"])
        managed = _managed_destinations(doc, views)
        disposition_ops, source_rows, unresolved = _decisions(doc, decisions, run_id, managed)
        unresolved += doc["empty_directories"]
        if unresolved:
            return AdoptResult(store.CANNOT_EVALUATE,
                               ["migration_incomplete: unresolved source dispositions"],
                               observation=observed.observation, unresolved=sorted(unresolved))
        # Attribution belongs in the frozen plan. PR-A permits note on KEEP only;
        # bind the other attributed decisions through a separate inventory envelope.
        # This envelope is the plan's inventory, containing observation + proposals;
        # its inner observation digest remains stable across discussion revisions.
        inventory = {
            "format": "opf.adoption.planning-inventory/v1",
            "observation": doc,
            "decisions": sorted(decisions, key=lambda row: row["path"]),
        }
        inventory_bytes = _seal(inventory, "inventory_digest")
        inventory_doc = tomllib.loads(inventory_bytes.decode("utf-8"))
        instant = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        ordered = _order_ops(disposition_ops, ops)
        _bind_registrations(ordered, doc, manifest_model)
        for row in disposition_ops:
            checked = schema.validate_op(row, homes=homes)
            if checked.status != store.VALID:
                return AdoptResult(checked.status, checked.findings, observation=observed.observation)
        # DISCLOSED-RESIDUAL (disclose-guard-residuals): re-adoption binds the adoption kind only. Spec
        # 8.2 requires re-adoption to seed both counter series from a pinned ancestral snapshot, but
        # this plan pins none: a resolved store's counters.toml lies in the excluded machine store, so
        # only the manifest digest is bound, and selecting and pinning the seeding snapshot is the later
        # seed step's (_opf_init_operation.read_ancestral_counter_seed), not this planner's.
        # The frozen identity's store_root is ".", the one root this planner investigates, so the
        # product-relative retire_preimage homes _decisions records equal the store-composed
        # preservation homes validate_plan requires (spec 14.2).
        identity = {"store_root": ".", "machine_rel": managed[0],
                    "adoption": doc["ancestry"]["adoption"]}
        effects = schema.derive_effects(ordered, source_rows, schema.store_manifest(identity), run_id)
        _check_planned_effects(doc, ordered, source_rows, effects, managed)
        proposal = {
            "format": schema.PLAN_FORMAT, "schema": schema.SCHEMA_VERSION,
            "product": product,
            "inventory_digest": inventory_doc["inventory_digest"],
            "run_id": run_id,
            "created_at": instant,
            "revision": bindings["revision"],
            "store": identity,
            "sources": source_rows,
            "effects": effects,
            "release": bindings["release"],
            "prompt_pack": bindings["prompt_pack"],
            "enforcement": bindings["enforcement"],
            "completion": schema.plan_completion(),
            "import_policy": schema.plan_import_policy(bindings["skip_policy"], source_rows),
            "ops": ordered,
        }
        frozen = _seal(proposal, "plan_digest")
        checked = schema.validate_plan(tomllib.loads(frozen.decode("utf-8")), homes=homes)
        if checked.status != store.VALID:
            return AdoptResult(checked.status, checked.findings, observation=observed.observation)
        return AdoptResult(store.VALID, observation=observed.observation,
                           inventory=inventory_bytes, plan=frozen)
    except (ValueError, UnicodeError, RecursionError, EmitError) as exc:
        return AdoptResult(store.CANNOT_EVALUATE, [str(exc)], observation=observed.observation)


def _check_planned_effects(observation, ops, sources, effects, managed):
    """Bind the program's effects to the observed tree and the planned store (spec
    14.1, 14.2). A store that does not resolve needs init-store, and init-store
    refuses a store that already resolves; render-views is required whenever the
    planned store declares a view and writes exactly the planned declared views;
    every creation needs observed absence unless an occupying source is archived and
    removed from that path first; and every replacement or repointing names an
    observed file with its old bytes, the manifest's registration chain excepted
    (_bind_registrations binds it).

    Why no record-adoption row is required: the receipt core binds this plan's own
    plan_digest and the approval recorded after the plan freezes, so no plan can
    carry that core's final digest; the spec 14 receipt obligation falls to apply and
    completion. A resolved store takes no init-store, and a store declaring no view
    has nothing to render."""
    resolved = observation["resolution"]["status"] == store.RESOLVED
    names = [row["op"] for row in ops]
    if not resolved and "init-store" not in names:
        raise PlanError("a store that does not resolve needs an init-store row (spec 14)")
    if managed[1] and "render-views" not in names:
        raise PlanError("the planned store declares views, so the plan needs a render-views row "
                        "(spec 14)")
    for row in ops:
        if row["op"] == "init-store" and resolved:
            raise PlanError("init-store names a store that already resolves")
        if row["op"] == "render-views":
            rendered = sorted(schema._compose(row["store_root"], m["path"]) for m in row["members"])
            if rendered != sorted(managed[1]):
                raise PlanError("render-views members are not exactly the planned declared views")
    kinds = _observed_kinds(observation)
    archived = set(row["path"] for row in sources if row["occupying"])
    for row in effects["creations"]:
        if row["path"] not in archived and not _observed_absent(kinds, row["path"]):
            raise PlanError("planned creation has no observed absence: {!r}".format(row["path"]))
    files = {row["path"]: row["digest"] for row in observation["entries"] if row["kind"] == "file"}
    manifest = schema.store_manifest({"store_root": ".", "machine_rel": managed[0]})
    for row in effects["replacements"] + effects["repointings"]:
        # The manifest's registration chain is bound by _bind_registrations, and validate_plan refuses
        # any other rewrite of the manifest.
        if row["path"] == manifest:
            continue
        if files.get(row["path"]) != row["old_digest"]:
            raise PlanError("planned replacement does not match its observed bytes: {!r}".format(
                row["path"]))


def _bind_registrations(ordered, observation, manifest_model):
    """Bind each keep's register-unmanaged row to the manifest write it performs
    (spec 14.1 exact effects, 14.2 Keep). In program order a row's old digest is the
    manifest the program leaves before it and its new digest is the emit_checked
    rendering of that manifest with the kept path appended to [unmanaged].paths,
    which must validate as a manifest. The chain starts at a resolved store's
    observed manifest, or at the manifest init-store scaffolds, which must then be
    the default manifest, the one whose views occupancy classified, since the planner
    holds no other bytes to extend. The postimage is this planner's rendering; apply
    must reproduce those bytes or refuse, never write others."""
    rows = [row for row in ordered if row["op"] == "register-unmanaged"]
    if not rows:
        return
    if observation["resolution"]["status"] == store.RESOLVED:
        model, digest = copy.deepcopy(manifest_model), observation["resolution"]["manifest_digest"]
    else:
        import _opf_init
        text = _opf_init.build_manifest()
        path = store.WORKING_DIRNAME + "/" + store.DEFAULT_MACHINE_SUBDIR + "/" + store.MANIFEST_NAME
        scaffolded = [member["digest"] for row in ordered if row["op"] == "init-store"
                      for member in row["members"]
                      if schema._compose(row["store_root"], member["path"]) == path]
        if scaffolded != [_digest(text.encode("utf-8"))]:
            raise PlanError("a keep registers into the manifest init-store scaffolds, so "
                            "init-store must scaffold the default manifest")
        model, digest = tomllib.loads(text), scaffolded[0]
    for row in rows:
        model.setdefault("unmanaged", {}).setdefault("paths", []).append(row["entry"])
        data = emit_checked(model).encode("utf-8")
        checked = store.validate_manifest(tomllib.loads(data.decode("utf-8")))
        if checked.status != store.VALID:
            raise PlanError("registering {!r} yields an invalid manifest: {}".format(
                row["entry"], "; ".join(checked.findings)))
        row["old_digest"], row["new_digest"] = digest, _digest(data)
        digest = row["new_digest"]


def _order_ops(dispositions, additional):
    """Move/retire first; create a store before unmanaged registration.

    This is ordering of proposals, not compilation of journal effects or proof
    that init/render/receipt preconditions can be satisfied by the current tree.
    """
    inits = [i for i, row in enumerate(additional) if row["op"] == "init-store"]
    if len(inits) > 1:
        raise PlanError("a proposal may initialize only one store")
    cut = inits[0] + 1 if inits else 0
    before = [r for r in dispositions if r["op"] in ("move-file", "retire-file")]
    after = [r for r in dispositions if r["op"] not in ("move-file", "retire-file")]
    return before + additional[:cut] + after + additional[cut:]


def re_full_nonce(value):
    return len(value) == 16 and all(c in "0123456789abcdef" for c in value)


def self_test():
    """Synthetic fixtures only. The caller/CI must provide writable temp space."""
    import contextlib
    import io
    import tempfile
    import unittest
    from unittest import mock

    class PlanningTests(unittest.TestCase):
        def setUp(self):
            self.temp = tempfile.TemporaryDirectory(prefix="opf-adopt-plan-")
            self.addCleanup(self.temp.cleanup)
            self.root = Path(self.temp.name).resolve()
            (self.root / "legacy.md").write_bytes(b"legacy\n")
            self.sources = ["legacy.md"]
            self.targets = ["archive/legacy.md"]
            self.now = datetime.datetime(2026, 1, 2, 3, 4, 5, tzinfo=datetime.timezone.utc)
            self.decision = {"path": "legacy.md", "disposition": "keep", "actor": "fixture"}
            self.bindings = schema.canonical_plan_bindings()
            self.with_init = True
            import _opf_init
            views = tomllib.loads(_opf_init.build_manifest())["views"].values()
            self.views = sorted(v["target"] for v in views)

        def all_targets(self):
            # The pre-commit hook lies outside every walked root, so its absence is a declared target.
            return sorted(set(self.targets) | {".opf/hooks/pre-commit"})

        def base_ops(self):
            # Scaffold the default store (unless one resolves), render its declared views and plant
            # every enforcement member.
            import _opf_init
            init = {"op": "init-store", "store_root": ".",
                    "members": [{"path": ".working/toml/manifest.toml",
                                 "digest": _digest(_opf_init.build_manifest().encode())}]}
            render = {"op": "render-views", "store_root": ".",
                      "members": [{"path": v, "digest": _digest(v.encode())} for v in self.views]}
            pack = schema.enforcement_install_op(self.bindings["enforcement"])
            return ([init] if self.with_init else []) + [render, pack]

        def observation(self):
            result = investigate(self.root, sources=self.sources, targets=self.all_targets())
            self.assertEqual(result.status, store.VALID, result.findings)
            return result

        def make_plan(self, observation=None, decisions=None, **changes):
            observation = observation or self.observation()
            args = dict(
                sources=self.sources, targets=self.all_targets(),
                expected_observation_digest=tomllib.loads(
                    observation.observation.decode())["observation_digest"],
                product="opf", decisions=[self.decision] if decisions is None else decisions,
                ops=self.base_ops(),
                now=self.now, run_nonce="0123456789abcdef", bindings=self.bindings,
            )
            args.update(changes)
            return plan(self.root, **args)

        def snapshot(self):
            return {str(p.relative_to(self.root)): p.read_bytes()
                    for p in self.root.rglob("*") if p.is_file()}

        def test_frozen_digests_and_no_effects(self):
            observation = self.observation()
            before = self.snapshot()
            # Deny Python file-write opens and process execution while the actual
            # engine runs. fd opens are checked too; preserve the capability probe's
            # membership for the delegating wrapper.
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
                result = self.make_plan(observation)
            self.assertEqual(result.status, store.VALID, result.findings)
            self.assertEqual(self.snapshot(), before)
            p = tomllib.loads(result.plan.decode())
            inv = tomllib.loads(result.inventory.decode())
            self.assertEqual(p["inventory_digest"], inv["inventory_digest"])
            self.assertEqual(p["ops"][0]["op"], "init-store")
            for raw, field in ((result.plan, "plan_digest"),
                               (result.inventory, "inventory_digest"),
                               (result.observation, "observation_digest")):
                doc = tomllib.loads(raw.decode())
                digest = doc.pop(field)
                self.assertEqual(digest, _digest(_emit(doc)))

        def test_deterministic_and_attributed_revision(self):
            one = self.make_plan()
            two = self.make_plan()
            self.assertEqual(one.status, store.VALID, one.findings)
            self.assertEqual((one.plan, one.inventory), (two.plan, two.inventory))
            changed = self.make_plan(decisions=[dict(self.decision, actor="another")])
            self.assertEqual(changed.status, store.VALID, changed.findings)
            self.assertEqual(one.observation, changed.observation)
            self.assertNotEqual(one.plan, changed.plan)

        def test_stale_inventory(self):
            observed = self.observation()
            (self.root / "legacy.md").write_bytes(b"changed\n")
            result = self.make_plan(observed)
            self.assertEqual(result.status, store.CANNOT_EVALUATE)
            self.assertIsNone(result.plan)

        def test_unresolved_refuses(self):
            before = self.snapshot()
            result = self.make_plan(decisions=[])
            self.assertEqual(result.status, store.CANNOT_EVALUATE)
            self.assertEqual(result.unresolved, ("legacy.md",))
            self.assertIsNone(result.plan)
            self.assertEqual(before, self.snapshot())
            self.assertFalse((self.root / ".working").exists())

        def test_migrate_resolves_keep_frozen(self):
            # A bare migrate decision resolves (spec 14.2): kept for post-adoption import,
            # preserved under the adoption preimage home, named in the import scope, and
            # given no import op.
            before = self.snapshot()
            result = self.make_plan(decisions=[dict(self.decision, disposition="migrate")])
            self.assertEqual(result.status, store.VALID, result.findings)
            self.assertEqual(result.unresolved, ())
            p = tomllib.loads(result.plan.decode())
            preserved = store.retire_preimage(p["run_id"], "legacy.md")
            digest = _digest(b"legacy\n")
            self.assertEqual(p["sources"], [{"path": "legacy.md", "digest": digest,
                                             "disposition": "migrate", "occupying": False,
                                             "preservation": preserved}])
            self.assertEqual(p["import_policy"]["scope"], ["legacy.md"])
            self.assertIn({"path": preserved, "digest": digest}, p["effects"]["creations"])
            self.assertEqual(p["effects"]["removals"], [{"path": "legacy.md", "digest": digest}])
            self.assertEqual([row["op"] for row in p["ops"]],
                             ["init-store", "render-views", "install-pack"])
            self.assertEqual(before, self.snapshot())
            self.assertFalse((self.root / ".working").exists())
            # Import references are no longer decision inputs: an old acceptance cannot ride the plan.
            referenced = dict(self.decision, disposition="migrate",
                              import_run_id="imp-20260101T000000Z-0123456789abcdef",
                              acceptance_digest="sha256:" + "0" * 64)
            result = self.make_plan(decisions=[referenced])
            self.assertEqual(result.status, store.CANNOT_EVALUATE)
            self.assertIsNone(result.plan)

        def test_plan_v2_bindings(self):
            result = self.make_plan(decisions=[dict(self.decision, disposition="retire")])
            self.assertEqual(result.status, store.VALID, result.findings)
            p = tomllib.loads(result.plan.decode())
            preserved = store.retire_preimage(p["run_id"], "legacy.md")
            digest = _digest(b"legacy\n")
            self.assertEqual(p["format"], "opf.adoption.plan/v2")
            self.assertEqual(p["revision"], self.bindings["revision"])
            # The store identity is derived: the default machine store and the investigated ancestry.
            self.assertEqual(p["store"], {"store_root": ".", "machine_rel": ".working/toml",
                                          "adoption": "first-adoption"})
            self.assertIn(p["store"]["adoption"], schema.ADOPTION_KINDS)
            self.assertEqual(p["sources"], [{"path": "legacy.md", "digest": digest,
                                             "disposition": "retire", "occupying": False,
                                             "preservation": preserved}])
            # The exact effects: the preservation copy, the scaffolded manifest and every enforcement
            # member, each tied to the op that writes it, beside the retire removal.
            pack = [dict(m) for row in self.bindings["enforcement"] for m in row["members"]]
            init, render, _ = self.base_ops()
            creations = sorted([{"path": preserved, "digest": digest}] + init["members"]
                               + render["members"] + pack, key=lambda r: sorted(r.items()))
            self.assertEqual(p["effects"], {"creations": creations,
                                            "replacements": [], "removals": [{"path": "legacy.md",
                                                                               "digest": digest}],
                                            "repointings": []})
            for key in ("release", "prompt_pack", "enforcement"):
                self.assertEqual(p[key], self.bindings[key])
            self.assertEqual(p["completion"], {"checks": list(schema.COMPLETION_CHECKS),
                                               "retirement": "green-checks-and-matching-bytes"})
            self.assertEqual(p["import_policy"], {"missingness": list(schema.MISSINGNESS_REASONS),
                                                  "unparsed": "retain-verbatim", "skip": "no-skip",
                                                  "scope": []})
            release = self.bindings["release"]
            for label, bindings in (
                    ("missing-input", {k: v for k, v in self.bindings.items() if k != "prompt_pack"}),
                    ("extra-input", dict(self.bindings, note="x")),
                    ("supplied-store", dict(self.bindings, store={"store_root": "."})),
                    ("revision-not-hex", dict(self.bindings, revision="HEAD")),
                    ("bad-skip-policy", dict(self.bindings, skip_policy="silent")),
                    ("anchor-disagrees", dict(self.bindings, release=dict(
                        release, anchor_sha256="sha256:" + "4" * 64))),
                    ("missing-platform",
                     dict(self.bindings, enforcement=self.bindings["enforcement"][:-1])),
                    ("bad-prompt-version", dict(self.bindings, prompt_pack=dict(
                        self.bindings["prompt_pack"], version="latest"))),
                    # every residual disclosure omitted, and one means' required disclosure omitted
                    ("residuals-none", dict(self.bindings, enforcement=[
                        dict(row, residuals=["none"]) for row in self.bindings["enforcement"]])),
                    ("pre-commit-residual-omitted", dict(self.bindings, enforcement=[
                        dict(row, residuals=["canonical-hand-edits", "same-user-tampering"])
                        if row["platform"] == "pre-commit" else row
                        for row in self.bindings["enforcement"]]))):
                with self.subTest(label):
                    result = self.make_plan(bindings=bindings)
                    self.assertNotEqual(result.status, store.VALID)
                    self.assertIsNone(result.plan)

        def test_occupied_destinations(self):
            # Foreign content at a planned managed destination (spec 14.2): a view the
            # default manifest declares, or a path under the machine store init-store would
            # create. Keep refuses there; any other disposition occupies and is preserved
            # under the adoption archive, a move included.
            (self.root / ".working/toml").mkdir(parents=True)
            (self.root / ".working/TODO.md").write_bytes(b"todo\n")
            (self.root / ".working/toml/counters.toml").write_bytes(b"[x]\n")
            (self.root / ".working/notes.md").write_bytes(b"notes\n")
            self.targets = ["archive/TODO.md"]
            observed = self.observation()

            def decide(path, disposition, **extra):
                return dict(path=path, disposition=disposition, actor="fixture", **extra)
            ordinary = [self.decision, decide(".working/notes.md", "keep")]
            for kept, other in ((".working/TODO.md", ".working/toml/counters.toml"),
                                (".working/toml/counters.toml", ".working/TODO.md")):
                with self.subTest(keep=kept):
                    result = self.make_plan(observed, decisions=ordinary + [decide(kept, "keep"),
                                                                           decide(other, "retire")])
                    self.assertEqual(result.status, store.CANNOT_EVALUATE)
                    self.assertIsNone(result.plan)
            result = self.make_plan(observed, decisions=ordinary + [
                decide(".working/TODO.md", "move", destination="archive/TODO.md"),
                decide(".working/toml/counters.toml", "migrate")])
            self.assertEqual(result.status, store.VALID, result.findings)
            p = tomllib.loads(result.plan.decode())
            self.assertEqual(p["store"]["adoption"], "re-adoption")
            rows = {row["path"]: row for row in p["sources"]}
            for path, disposition in ((".working/TODO.md", "move"),
                                      (".working/toml/counters.toml", "migrate")):
                row = rows[path]
                self.assertEqual((row["disposition"], row["occupying"], row["preservation"]),
                                 (disposition, True, store.retire_preimage(p["run_id"], path)))
            self.assertEqual((rows[".working/notes.md"]["occupying"], rows["legacy.md"]["occupying"]),
                             (False, False))
            self.assertNotIn("preservation", rows[".working/notes.md"])
            move = [row for row in p["ops"] if row["op"] == "move-file"]
            self.assertEqual([row["destination"] for row in move], ["archive/TODO.md"])
            self.assertIn({"path": store.retire_preimage(p["run_id"], ".working/TODO.md"),
                           "digest": _digest(b"todo\n")}, p["effects"]["creations"])

        def test_move_requires_observed_absence(self):
            decision = dict(self.decision, disposition="move", destination="archive/legacy.md")
            result = self.make_plan(decisions=[decision])
            self.assertEqual(result.status, store.VALID, result.findings)
            p = tomllib.loads(result.plan.decode())
            self.assertEqual(p["ops"][0]["op"], "move-file")
            (self.root / "archive").mkdir()
            (self.root / "archive/legacy.md").write_bytes(b"occupied")
            result = self.make_plan(decisions=[decision])
            self.assertEqual(result.status, store.CANNOT_EVALUATE)
            self.assertEqual((self.root / "archive/legacy.md").read_bytes(), b"occupied")
            # An explicit destination inside the store tree must lie beneath .working/archive/moved/.
            self.targets = [".working/elsewhere/legacy.md", ".working/archive/moved/legacy.md"]
            for destination, status in ((".working/elsewhere/legacy.md", store.INVALID),
                                        (".working/archive/moved/legacy.md", store.VALID)):
                with self.subTest(destination=destination):
                    result = self.make_plan(decisions=[dict(decision, destination=destination)])
                    self.assertEqual(result.status, status, result.findings)

        def test_default_move_destination(self):
            # A move without a destination plans the spec 14.2 default from the row's own path,
            # never stripped: legacy/OLD.md lands at .working/archive/moved/legacy/OLD.md, and a
            # store-tree source keeps its .working/ prefix beneath the Move root. A non-occupying
            # default Move is preserved at that destination; an occupying one is archived, and
            # still moves to its default destination.
            (self.root / "legacy").mkdir()
            (self.root / "legacy/OLD.md").write_bytes(b"old\n")
            (self.root / ".working").mkdir()
            (self.root / ".working/TODO.md").write_bytes(b"todo\n")
            self.sources = ["legacy", "legacy.md"]
            decisions = [self.decision, dict(path="legacy/OLD.md", disposition="move", actor="fixture"),
                         dict(path=".working/TODO.md", disposition="move", actor="fixture")]
            result = self.make_plan(decisions=decisions)
            self.assertEqual(result.status, store.VALID, result.findings)
            p = tomllib.loads(result.plan.decode())
            moves = dict((row["source"], row["destination"])
                         for row in p["ops"] if row["op"] == "move-file")
            self.assertEqual(moves, dict([
                ("legacy/OLD.md", ".working/archive/moved/legacy/OLD.md"),
                (".working/TODO.md", ".working/archive/moved/.working/TODO.md")]))
            rows = dict((row["path"], row) for row in p["sources"])
            old, todo = rows["legacy/OLD.md"], rows[".working/TODO.md"]
            self.assertEqual((old["occupying"], old["preservation"]),
                             (False, ".working/archive/moved/legacy/OLD.md"))
            self.assertEqual((todo["occupying"], todo["preservation"]),
                             (True, store.retire_preimage(p["run_id"], ".working/TODO.md")))
            self.assertIn(dict(path=".working/archive/moved/legacy/OLD.md", digest=_digest(b"old\n")),
                          p["effects"]["creations"])

        def test_default_move_collision(self):
            # An occupied default destination is a collision finding, never an overwrite (spec 14.2).
            occupied = store.moved_dest("legacy.md")
            (self.root / occupied).parent.mkdir(parents=True)
            (self.root / occupied).write_bytes(b"planted\n")
            result = self.make_plan(decisions=[dict(self.decision, disposition="move")])
            self.assertEqual(result.status, store.CANNOT_EVALUATE)
            self.assertIsNone(result.plan)
            self.assertTrue(any("collision" in f and occupied in f for f in result.findings),
                            result.findings)
            self.assertEqual((self.root / occupied).read_bytes(), b"planted\n")

        def test_default_move_store_base_unobservable(self):
            # A homes-2 store excludes its control area from investigation, so the default
            # destination's store base is excluded: its absence is unproven, which is
            # cannot-evaluate naming the source.
            import _opf_init
            manifest = tomllib.loads(_opf_init.build_manifest())
            manifest["opf"].update(homes=2, spec_version=store.HOMES2_SPEC_VERSION)
            (self.root / ".working/toml").mkdir(parents=True)
            (self.root / ".working/toml/manifest.toml").write_text(emit_checked(manifest),
                                                                   encoding="utf-8")
            (self.root / ".working/archive/moved").mkdir(parents=True)
            self.with_init = False
            with mock.patch.object(store, "SUPPORTED_HOMES", 2):
                doc = tomllib.loads(self.observation().observation.decode())
                result = self.make_plan(decisions=[dict(self.decision, disposition="move")])
            self.assertIn(dict(path=".working/archive", reason="store-control"), doc["exclusions"])
            self.assertEqual(result.status, store.CANNOT_EVALUATE)
            self.assertIsNone(result.plan)
            self.assertTrue(any("store base cannot be observed" in f and "'legacy.md'" in f
                                for f in result.findings), result.findings)

        def test_companion_store_refuses_moves(self):
            # Stricter than spec 14.2, which scopes the refusal to the default Move: a pointer
            # naming a store at another root refuses every plan, the default and an explicit Move
            # alike.
            import _opf_init
            (self.root / "companion/.working/toml").mkdir(parents=True)
            (self.root / "companion/.working/toml/manifest.toml").write_text(
                _opf_init.build_manifest(), encoding="utf-8")
            (self.root / ".opf.toml").write_text('[store]\ntarget = "dir:companion"\n',
                                                 encoding="utf-8")
            for decision in (dict(self.decision, disposition="move"),
                             dict(self.decision, disposition="move", destination="archive/legacy.md")):
                with self.subTest(destination=decision.get("destination")):
                    result = plan(self.root, sources=self.sources, targets=self.all_targets(),
                                  expected_observation_digest="sha256:" + "0" * 64, product="opf",
                                  decisions=[decision], ops=self.base_ops(), now=self.now,
                                  run_nonce="0123456789abcdef", bindings=self.bindings)
                    self.assertEqual(result.status, store.CANNOT_EVALUATE)
                    self.assertIsNone(result.plan)
                    self.assertTrue(any("companion/remote store" in f for f in result.findings),
                                    result.findings)

        def test_protected_move_destinations(self):
            # The planner refuses exactly the destinations apply refuses (the shared predicate
            # _opf_adopt.protected_destination): .git and .aiqt at any depth, the product-root
            # pointers and the store control area; an ordinary destination still plans.
            run_id = "adopt-20260102T030405Z-0123456789abcdef"
            protected = [".aiqt/x.md", "docs/.aiqt/x.md", ".opf.toml", ".opf.local.toml"]
            self.targets = protected + ["moved.md"]
            for destination in protected:
                with self.subTest(destination=destination):
                    result = self.make_plan(decisions=[dict(self.decision, disposition="move",
                                                            destination=destination)])
                    self.assertEqual(result.status, store.INVALID, result.findings)
                    self.assertIsNone(result.plan)
                    reason = schema.protected_destination(destination, run_id)
                    self.assertIn("plan move-file destination is protected: " + reason, result.findings)
            result = self.make_plan(decisions=[dict(self.decision, disposition="move",
                                                    destination="moved.md")])
            self.assertEqual(result.status, store.VALID, result.findings)
            # An archive preservation copy is a destination too: a retire of a source with an
            # .aiqt component refuses.
            (self.root / "docs/.aiqt").mkdir(parents=True)
            (self.root / "docs/.aiqt/notes.md").write_bytes(b"notes\n")
            self.sources = ["docs", "legacy.md"]
            retired = dict(path="docs/.aiqt/notes.md", disposition="retire", actor="fixture")
            result = self.make_plan(decisions=[self.decision, retired])
            self.assertEqual(result.status, store.INVALID, result.findings)
            self.assertTrue(any("'docs/.aiqt/notes.md' preservation is protected" in f
                                for f in result.findings), result.findings)

        def test_store_identity_and_generated_effects(self):
            # The init, render and receipt outputs are exact effects, each at the frozen store. Another
            # store and an outside receipt are observed absent, so the store binding refuses them. A
            # retire decision keeps the manifest registration chain out of these store-identity cases.
            self.decision = dict(self.decision, disposition="retire")
            self.targets = self.targets + ["other-product", "receipt.toml"]
            init, render, pack = self.base_ops()
            receipt = {"op": "record-adoption", "receipt_path": ".working/toml/adoption.toml",
                       "receipt_core_digest": _digest(b"r")}
            result = self.make_plan(ops=[init, render, pack, receipt])
            self.assertEqual(result.status, store.VALID, result.findings)
            created = tomllib.loads(result.plan.decode())["effects"]["creations"]
            for row in (init["members"] + render["members"]
                        + [{"path": ".working/toml/adoption.toml", "digest": _digest(b"r")}]):
                self.assertIn(row, created)
            other_machine = [{"path": ".working/data/manifest.toml", "digest": _digest(b"m")}]
            for label, ops, status in (
                    # an op targeting another store contradicts the frozen store identity
                    ("other-store", [dict(init, store_root="other-product"), render, pack],
                     store.INVALID),
                    ("receipt-outside-store",
                     [init, render, pack, dict(receipt, receipt_path="receipt.toml")], store.INVALID),
                    ("other-machine-store", [dict(init, members=other_machine), render, pack],
                     store.INVALID),
                    # render-views writes exactly the declared views, at the frozen store
                    ("other-render-store", [init, pack, dict(render, store_root="other-product")],
                     store.CANNOT_EVALUATE),
                    ("render-missing-view", [init, pack, dict(render, members=render["members"][1:])],
                     store.CANNOT_EVALUATE),
                    # the required program (spec 14): a store that does not resolve is scaffolded, and
                    # its declared views are rendered
                    ("no-init-store", [render, pack], store.CANNOT_EVALUATE),
                    ("no-render-views", [init, pack], store.CANNOT_EVALUATE),
                    # an enforcement member no op installs
                    ("enforcement-uninstalled", [init, render], store.INVALID)):
                with self.subTest(label):
                    result = self.make_plan(ops=ops)
                    self.assertEqual(result.status, status, result.findings)
                    self.assertIsNone(result.plan)

        def test_hook_replacement_binds_observed_bytes(self):
            # A deny hook merged into an existing registration is a replacement tied to its enforcement
            # member, and its old digest must be the observed bytes.
            (self.root / ".claude").mkdir()
            (self.root / ".claude/settings.json").write_bytes(b"{}\n")
            member = self.bindings["enforcement"][2]["members"][0]
            others = [row for row in self.bindings["enforcement"] if row["platform"] != "claude-code"]
            hook = {"op": "enable-hook", "registration_path": ".claude/settings.json",
                    "plugin_entry": "opf", "old_digest": _digest(b"{}\n"),
                    "new_digest": member["digest"]}
            ops = self.base_ops()[:2] + [schema.enforcement_install_op(others)]
            result = self.make_plan(ops=ops + [hook])
            self.assertEqual(result.status, store.VALID, result.findings)
            replacements = tomllib.loads(result.plan.decode())["effects"]["replacements"]
            self.assertEqual([row for row in replacements if row["path"] == ".claude/settings.json"], [
                {"path": ".claude/settings.json", "old_digest": hook["old_digest"],
                 "new_digest": member["digest"]}])
            for label, ops in (("stale-old-digest", ops + [dict(hook, old_digest=_digest(b"x"))]),
                               ("planted-over-existing", self.base_ops())):
                with self.subTest(label):
                    self.assertEqual(self.make_plan(ops=ops).status, store.CANNOT_EVALUATE)

        def test_effect_collisions(self):
            (self.root / "other.md").write_bytes(b"other\n")
            self.sources = ["legacy.md", "other.md"]
            self.targets = ["archive/x.md", ".working/archive/moved"]
            move = dict(self.decision, disposition="move", destination="archive/x.md")
            # two moves to one destination are a collision, never frozen VALID
            result = self.make_plan(decisions=[move, dict(move, path="other.md")])
            self.assertEqual(result.status, store.INVALID)
            self.assertIsNone(result.plan)
            # the Move archive root is a directory home, never a file target
            home = dict(move, destination=".working/archive/moved")
            result = self.make_plan(decisions=[home, dict(self.decision, path="other.md")])
            self.assertEqual(result.status, store.INVALID)
            # a caller creation on a preservation destination, or on a source left live, collides
            retire = dict(self.decision, disposition="retire")
            preserved = store.retire_preimage("adopt-20260102T030405Z-0123456789abcdef", "legacy.md")
            create = {"op": "create-file", "content_digest": _digest(b"c")}
            for path, status in ((preserved, store.INVALID), ("other.md", store.CANNOT_EVALUATE)):
                with self.subTest(create=path):
                    result = self.make_plan(decisions=[retire, dict(self.decision, path="other.md")],
                                            ops=self.base_ops() + [dict(create, path=path)])
                    self.assertEqual(result.status, status, result.findings)
                    self.assertIsNone(result.plan)

        def test_preservation_destination_occupied(self):
            # A preservation destination is checked for occupancy exactly as a move destination is:
            # bytes already at this run's adoption archive path refuse the plan, never an overwrite.
            preserved = store.retire_preimage("adopt-20260102T030405Z-0123456789abcdef", "legacy.md")
            (self.root / preserved).parent.mkdir(parents=True)
            (self.root / preserved).write_bytes(b"planted\n")
            # The planted file is control area, so it is no candidate and needs no decision of its own.
            result = self.make_plan(decisions=[dict(self.decision, disposition="retire")])
            self.assertEqual(result.status, store.CANNOT_EVALUATE)
            self.assertIsNone(result.plan)
            self.assertEqual((self.root / preserved).read_bytes(), b"planted\n")

        def test_live_source_rewrite_refused(self):
            # A source left live at apply stays byte-identical until its recorded retirement (spec 14.1,
            # 14.2), so no disposition lets the approved plan rewrite it: neither merging the deny hook
            # into a declared registration file nor repointing a declared consumer. Undeclared, the same
            # hook merge plans (test_hook_replacement_binds_observed_bytes).
            member = self.bindings["enforcement"][2]["members"][0]
            others = [row for row in self.bindings["enforcement"] if row["platform"] != "claude-code"]
            hook = {"op": "enable-hook", "registration_path": ".claude/settings.json",
                    "plugin_entry": "opf", "old_digest": _digest(b"{}\n"),
                    "new_digest": member["digest"]}
            repoint = {"op": "repoint-consumer", "path": "legacy.md",
                       "old_digest": _digest(b"legacy\n"), "new_digest": _digest(b"repointed\n")}
            init, render, pack = self.base_ops()
            for source, rewrite, ops in (
                    ("legacy.md", "legacy.md", [init, render, pack, repoint]),
                    (".claude", ".claude/settings.json",
                     [init, render, schema.enforcement_install_op(others), hook])):
                if source == ".claude":
                    (self.root / ".claude").mkdir()
                    (self.root / ".claude/settings.json").write_bytes(b"{}\n")
                self.sources = [source]
                self.targets = ["moved.md"]
                for disposition, extra in (("keep", {}), ("retire", {}), ("migrate", {}),
                                           ("move", {"destination": "moved.md"})):
                    with self.subTest(rewrite=rewrite, disposition=disposition):
                        decision = dict(path=rewrite, disposition=disposition, actor="fixture", **extra)
                        result = self.make_plan(decisions=[decision], ops=ops)
                        self.assertEqual(result.status, store.INVALID, result.findings)
                        self.assertIsNone(result.plan)

        def test_registration_rewrites_manifest(self):
            # Each keep's register-unmanaged row is the manifest replacement it performs (spec 14.1):
            # the chain starts at the default manifest init-store scaffolds, or at a resolved store's
            # observed manifest, and each link is the emit_checked manifest with one more [unmanaged]
            # path.
            import _opf_init
            (self.root / "other.md").write_bytes(b"other\n")
            self.sources = ["legacy.md", "other.md"]
            keeps = [self.decision, dict(self.decision, path="other.md")]
            text = _opf_init.build_manifest()
            model = tomllib.loads(text)
            digests = [_digest(text.encode())]
            for entry in ("legacy.md", "other.md"):
                model["unmanaged"]["paths"].append(entry)
                digests.append(_digest(emit_checked(model).encode()))
            chain = [("legacy.md", digests[0], digests[1]), ("other.md", digests[1], digests[2])]
            result = self.make_plan(decisions=keeps)
            self.assertEqual(result.status, store.VALID, result.findings)
            p = tomllib.loads(result.plan.decode())
            self.assertEqual([(row["entry"], row["old_digest"], row["new_digest"]) for row in p["ops"]
                              if row["op"] == "register-unmanaged"], chain)
            self.assertEqual(p["effects"]["replacements"], sorted(
                [{"path": ".working/toml/manifest.toml", "old_digest": old, "new_digest": new}
                 for _, old, new in chain], key=lambda r: sorted(r.items())))
            self.assertFalse(any(row["path"].startswith(".working/archive/")
                                 for row in p["effects"]["creations"]))
            # Scaffolded bytes other than the default manifest leave the chain underivable, so it
            # refuses.
            init, render, pack = self.base_ops()
            odd = dict(init, members=[{"path": ".working/toml/manifest.toml", "digest": _digest(b"m")}])
            self.assertEqual(self.make_plan(decisions=keeps, ops=[odd, render, pack]).status,
                             store.CANNOT_EVALUATE)
            # A store that already resolves: the chain starts at the observed manifest bytes.
            (self.root / ".working/toml").mkdir(parents=True)
            (self.root / ".working/toml/manifest.toml").write_text(text, encoding="utf-8")
            self.with_init = False
            result = self.make_plan(decisions=keeps)
            self.assertEqual(result.status, store.VALID, result.findings)
            p = tomllib.loads(result.plan.decode())
            self.assertEqual([(row["entry"], row["old_digest"], row["new_digest"]) for row in p["ops"]
                              if row["op"] == "register-unmanaged"], chain)
            # Apply archives the live manifest before its first rewrite (spec 14.2), so that copy of
            # the observed bytes is a bound creation (spec 14.1); a scaffolded manifest has none.
            preserved = store.retire_preimage(p["run_id"], ".working/toml/manifest.toml")
            self.assertIn({"path": preserved, "digest": digests[0]}, p["effects"]["creations"])

        def test_control_area_never_selected(self):
            # Spec 14.2 carries no homes qualifier: in a legacy (homes 1) layout the store control area,
            # a committed adoption-archive original included, is inventoried but never a candidate. A
            # decision naming it refuses rather than keeping, registering, moving or retiring it, and no
            # caller creation lands in it.
            archived = ".working/archive/adoption/adopt-20250101T000000Z-0123456789abcdef/old.md"
            staged = ".working/staging/import/imp-20250101T000000Z-0123456789abcdef/run.toml"
            for rel in (archived, staged):
                (self.root / rel).parent.mkdir(parents=True, exist_ok=True)
                (self.root / rel).write_bytes(b"control\n")
            self.targets = ["moved.md"]
            doc = tomllib.loads(self.observation().observation.decode())
            self.assertIn(archived, [row["path"] for row in doc["entries"]])
            self.assertEqual((doc["candidates"], doc["empty_directories"]), (["legacy.md"], []))
            self.assertEqual(self.make_plan().status, store.VALID)
            for disposition, extra in (("keep", {}), ("retire", {}), ("migrate", {}),
                                       ("move", {"destination": "moved.md"})):
                with self.subTest(disposition=disposition):
                    decision = dict(path=archived, disposition=disposition, actor="fixture", **extra)
                    result = self.make_plan(decisions=[self.decision, decision])
                    self.assertEqual(result.status, store.CANNOT_EVALUATE)
                    refusal = "decision selects the reserved store control area: {!r} (spec 14.2)"
                    self.assertEqual(result.findings, (refusal.format(archived),))
                    self.assertIsNone(result.plan)
            run = "adopt-20260102T030405Z-0123456789abcdef"
            run_home = store.retire_preimage(run, "x").rsplit("/", 1)[0]
            for path in (run_home + "/extra.md", ".working/archive/moved/planted.md", staged + ".new"):
                with self.subTest(create=path):
                    create = {"op": "create-file", "path": path, "content_digest": _digest(b"x")}
                    result = self.make_plan(ops=self.base_ops() + [create])
                    self.assertEqual(result.status, store.INVALID, result.findings)
                    self.assertIsNone(result.plan)

        def test_deliverables_need_disposition(self):
            # A pre-existing product-root VERSION or CHANGELOG.md sits at a deliverable destination
            # (spec 14), so it takes an explicit disposition before init-store: undecided, the plan is
            # incomplete.
            (self.root / "VERSION").write_bytes(b"1.0.0\n")
            (self.root / "CHANGELOG.md").write_bytes(b"# Changes\n")
            self.sources = []
            doc = tomllib.loads(self.observation().observation.decode())
            self.assertEqual(doc["candidates"], ["CHANGELOG.md", "VERSION"])
            result = self.make_plan(decisions=[])
            self.assertEqual(result.status, store.CANNOT_EVALUATE)
            self.assertEqual(result.unresolved, ("CHANGELOG.md", "VERSION"))
            self.assertIsNone(result.plan)
            retired = [dict(self.decision, path=path, disposition="retire")
                       for path in ("CHANGELOG.md", "VERSION")]
            result = self.make_plan(decisions=retired)
            self.assertEqual(result.status, store.VALID, result.findings)

        def test_foreign_working_preserves_resolver_status(self):
            (self.root / ".working").mkdir()
            (self.root / ".working/notes.md").write_bytes(b"notes")
            result = self.observation()
            doc = tomllib.loads(result.observation.decode())
            self.assertEqual(doc["resolution"]["status"], store.CANNOT_EVALUATE)
            self.assertIn(".working/notes.md", doc["candidates"])
            planned = self.make_plan(result)
            self.assertEqual(planned.unresolved, (".working/notes.md",))

        def test_platform_detection_roots(self):
            (self.root / "GEMINI.md").write_bytes(b"gemini\n")
            (self.root / ".gemini").mkdir()
            (self.root / ".gemini/settings.json").write_bytes(b"{}\n")
            (self.root / ".codex").mkdir()
            (self.root / ".codex/config.toml").write_bytes(b"")
            doc = tomllib.loads(self.observation().observation.decode())
            detected = {row["path"]: row["kind"] for row in doc["detections"]}
            self.assertEqual(detected.get(".gemini"), "directory")
            self.assertEqual(detected.get("GEMINI.md"), "file")
            self.assertEqual(detected.get(".codex"), "directory")
            self.assertIn(".gemini/settings.json", [row["path"] for row in doc["entries"]])
            # Detection by path only: a platform surface is not an adoption candidate.
            self.assertNotIn(".gemini/settings.json", doc["candidates"])
            # A pre-existing platform file is never overwritten: planting GEMINI.md over it collides,
            # and the same row bound to an absent path beneath the walked .gemini directory plans.
            self.assertEqual(self.make_plan().status, store.CANNOT_EVALUATE)
            gemini = [{"path": ".gemini/opf.md", "digest": _digest(b"g")}]
            self.bindings["enforcement"][4]["members"] = gemini
            self.assertEqual(self.make_plan().status, store.VALID)

        def test_ancestry_marks_re_adoption(self):
            def ancestry():
                return tomllib.loads(self.observation().observation.decode())["ancestry"]
            self.assertEqual((ancestry()["adoption"], ancestry()["traces"]), ("first-adoption", []))
            inventory = tomllib.loads(self.make_plan().inventory.decode())
            self.assertEqual(inventory["observation"]["ancestry"]["adoption"], "first-adoption")
            # A committed dir:. pointer beside foreign .working content (no manifest) is ancestry.
            (self.root / ".working").mkdir()
            (self.root / ".working/notes.md").write_bytes(b"notes")
            (self.root / ".opf.toml").write_text('[store]\ntarget = "dir:."\n', encoding="utf-8")
            self.assertEqual((ancestry()["adoption"], ancestry()["traces"]),
                             ("re-adoption", [".opf.toml"]))
            (self.root / ".opf.toml").unlink()
            import _opf_init
            (self.root / ".working/toml").mkdir()
            (self.root / ".working/toml/manifest.toml").write_text(
                _opf_init.build_manifest(), encoding="utf-8")
            self.assertEqual((ancestry()["adoption"], ancestry()["traces"]),
                             ("re-adoption", [".working/toml/manifest.toml"]))

        def test_ancestry_store_debris_is_re_adoption(self):
            import shutil

            def ancestry():
                return tomllib.loads(self.observation().observation.decode())["ancestry"]
            # Oracle derived from the store constants, not from this module's own set.
            reserved = [store.WORKING_DIRNAME + "/" + name for name in
                        (store.DEFAULT_MACHINE_SUBDIR,) + store.RESERVED_MACHINE_SUBDIRS]
            run = ".working/imports/imp-20260102T030405Z-0123456789abcdef"
            cases = [(".working/toml", [".working/toml/counters.toml"], [".working/toml/records"]),
                     (".working/imports", [run + "/run.toml"], [])]
            cases += [(name, [name + "/leftover.toml"], []) for name in reserved
                      if name not in (".working/toml", ".working/imports")]
            for name, files, dirs in cases:
                with self.subTest(reserved=name):
                    try:
                        for rel in dirs + [str(Path(rel).parent) for rel in files]:
                            (self.root / rel).mkdir(parents=True, exist_ok=True)
                        for rel in files:
                            (self.root / rel).write_bytes(b"[x]\n")
                        doc = tomllib.loads(self.observation().observation.decode())
                        self.assertEqual(doc["resolution"]["status"], store.CANNOT_EVALUATE)
                        self.assertEqual((doc["ancestry"]["adoption"], doc["ancestry"]["traces"]),
                                         ("re-adoption", [name]))
                    finally:
                        shutil.rmtree(self.root / ".working")
            # Reserved files, even empty ones, and empty reserved directories are ancestry.
            name = ".working/toml"
            for kind, content in (("file", b""), ("file", b"[x]\n"), ("directory", None)):
                with self.subTest(reserved=name, kind=kind, content=content):
                    try:
                        (self.root / ".working").mkdir()
                        if kind == "file":
                            (self.root / name).write_bytes(content)
                        else:
                            (self.root / name).mkdir()
                        doc = tomllib.loads(self.observation().observation.decode())
                        self.assertEqual((doc["ancestry"]["adoption"], doc["ancestry"]["traces"]),
                                         ("re-adoption", [name]))
                    finally:
                        shutil.rmtree(self.root / ".working")
            # Foreign .working content, even a nested reserved-looking name, stays first-adoption.
            (self.root / ".working/notes").mkdir(parents=True)
            (self.root / ".working/notes/toml").write_bytes(b"notes")
            self.assertEqual((ancestry()["adoption"], ancestry()["traces"]), ("first-adoption", []))
            # A pointer and debris are both traces, pointer first.
            (self.root / ".working/toml").mkdir()
            (self.root / ".working/toml/counters.toml").write_bytes(b"[x]\n")
            (self.root / ".opf.toml").write_text('[store]\ntarget = "dir:."\n', encoding="utf-8")
            self.assertEqual(ancestry()["traces"], [".opf.toml", ".working/toml"])

        def test_unreadable_declared_source(self):
            with mock.patch.object(sys.modules[__name__], "_read",
                                   side_effect=OSError("injected read failure")):
                result = investigate(self.root, sources=self.sources)
            self.assertEqual(result.status, store.CANNOT_EVALUATE)
            self.assertIsNone(result.observation)

        def test_missing_symlink_hardlink_special_and_bounds(self):
            result = investigate(self.root, sources=["missing"])
            self.assertEqual(result.status, store.CANNOT_EVALUATE)
            (self.root / "link").symlink_to("legacy.md")
            self.assertEqual(investigate(self.root, sources=["link"]).status,
                             store.CANNOT_EVALUATE)
            (self.root / "link").unlink()
            os.link(self.root / "legacy.md", self.root / "hard")
            self.assertEqual(investigate(self.root, sources=["hard"]).status,
                             store.CANNOT_EVALUATE)
            (self.root / "hard").unlink()
            os.mkfifo(self.root / "fifo")
            self.assertEqual(investigate(self.root, sources=["fifo"]).status,
                             store.CANNOT_EVALUATE)
            (self.root / "fifo").unlink()
            with mock.patch.object(sys.modules[__name__], "MAX_FILE_BYTES", 1):
                self.assertEqual(investigate(self.root, sources=self.sources).status,
                                 store.CANNOT_EVALUATE)
            with mock.patch.object(sys.modules[__name__], "MAX_ENTRIES", 1):
                self.assertEqual(investigate(self.root, sources=self.sources).status,
                                 store.CANNOT_EVALUATE)

        def test_bad_inputs_and_unknown_ops(self):
            for sources in ("legacy.md", ["../escape"], [".git/config"],
                            ["legacy.md", "legacy.md"], ["a", "a/b"]):
                with self.subTest(sources=sources):
                    self.assertEqual(investigate(self.root, sources=sources).status,
                                     store.CANNOT_EVALUATE)
            for changes in ({"product": "unknown"}, {"ops": [{"op": "shell"}]},
                            {"now": self.now.replace(tzinfo=None)},
                            {"run_nonce": "bad"}, {"ops": [{"op": "import-file"}]},
                            {"bindings": None}):
                with self.subTest(changes=changes):
                    result = self.make_plan(**changes)
                    self.assertNotEqual(result.status, store.VALID)
                    self.assertIsNone(result.plan)

        def test_resolved_store_exclusions(self):
            import _opf_init
            machine = self.root / ".working/toml"
            machine.mkdir(parents=True)
            manifest = tomllib.loads(_opf_init.build_manifest())
            manifest["unmanaged"]["paths"] = [".working/private"]
            # Drop one default view, so the planner must read the resolved manifest's own view targets.
            del manifest["views"]["BACKLOG.md"]
            self.views = sorted(v["target"] for v in manifest["views"].values())
            (machine / "manifest.toml").write_text(emit_checked(manifest), encoding="utf-8")
            private = self.root / ".working/private"
            private.mkdir()
            # A FIFO under an unmanaged subtree would refuse if the walker entered it.
            os.mkfifo(private / "unreadable")
            result = self.observation()
            doc = tomllib.loads(result.observation.decode())
            self.assertEqual(doc["resolution"]["status"], store.RESOLVED)
            self.assertIn({"path": ".working/private", "reason": "registered-unmanaged"},
                          doc["exclusions"])
            self.assertNotIn(".working/private/unreadable",
                             [row["path"] for row in doc["entries"]])
            self.assertEqual(investigate(self.root, sources=[".working/private"]).status,
                             store.CANNOT_EVALUATE)
            # init-store over a store that already resolves contradicts the frozen identity; the plan
            # binds the resolved machine store and its re-adoption ancestry.
            self.assertEqual(self.make_plan(result).status, store.CANNOT_EVALUATE)
            self.with_init = False
            planned = self.make_plan(result)
            self.assertEqual(planned.status, store.VALID, planned.findings)
            self.assertEqual(tomllib.loads(planned.plan.decode())["store"],
                             {"store_root": ".", "machine_rel": ".working/toml",
                              "adoption": "re-adoption"})
            # The resolved manifest's own declared view is a managed destination: keep refuses there and
            # a retire is occupying.
            (self.root / ".working/TODO.md").write_bytes(b"todo\n")
            view = dict(self.decision, path=".working/TODO.md")
            self.assertEqual(self.make_plan(decisions=[self.decision, view]).status,
                             store.CANNOT_EVALUATE)
            retired = self.make_plan(decisions=[self.decision, dict(view, disposition="retire")])
            self.assertEqual(retired.status, store.VALID, retired.findings)
            rows = tomllib.loads(retired.plan.decode())["sources"]
            self.assertEqual([row["occupying"] for row in rows], [True, False])
            # A path the default manifest declares but this store does not is ordinary content here.
            (self.root / ".working/BACKLOG.md").write_bytes(b"backlog\n")
            kept = self.make_plan(decisions=[self.decision, dict(view, disposition="retire"),
                                             dict(self.decision, path=".working/BACKLOG.md")])
            self.assertEqual(kept.status, store.VALID, kept.findings)
            rows = tomllib.loads(kept.plan.decode())["sources"]
            self.assertEqual([(row["path"], row["occupying"]) for row in rows],
                             [(".working/BACKLOG.md", False), (".working/TODO.md", True),
                              ("legacy.md", False)])

        def test_malformed_pointer_and_store_manifest(self):
            (self.root / ".opf.toml").write_bytes(b"not toml")
            self.assertEqual(investigate(self.root, sources=[]).status, store.CANNOT_EVALUATE)
            (self.root / ".opf.toml").unlink()
            machine = self.root / ".working/toml"
            machine.mkdir(parents=True)
            (machine / "manifest.toml").write_bytes(b"not toml")
            self.assertEqual(investigate(self.root, sources=[]).status, store.CANNOT_EVALUATE)

        def test_working_swap_after_listing_never_read(self):
            # Round-5 defect 1 (the rounds-2-4 invariant): every name discovery reads from the
            # .working listing must be read through the descriptor that produced the listing. Swap
            # .working for a replacement tree the instant the discovery listing returns: the
            # replacement's manifest must NEVER be read on any path (resolution or the planner's own
            # manifest use), and the investigation still refuses at the product-root re-check rather
            # than proceeding over the swap.
            import _opf_init
            machine = self.root / ".working/toml"
            machine.mkdir(parents=True)
            (machine / "manifest.toml").write_text(_opf_init.build_manifest(), encoding="utf-8")
            original_ino = (machine / "manifest.toml").stat().st_ino
            replacement = self.root / ".working-replacement"
            (replacement / "toml").mkdir(parents=True)
            (replacement / "toml/manifest.toml").write_text(_opf_init.build_manifest(),
                                                            encoding="utf-8")
            replacement_ino = (replacement / "toml/manifest.toml").stat().st_ino
            self.assertNotEqual(original_ino, replacement_ino)
            state = {"fired": False}
            real_listdir = os.listdir
            real_read = os.read
            read_inos = set()

            def swap_listdir(target="."):
                names = real_listdir(target)
                if isinstance(target, int) and not state["fired"] and sorted(names) == ["toml"]:
                    # The .working listing during store resolution: swap the whole tree NOW, after
                    # the listing returned but before any listed manifest is read.
                    os.rename(self.root / ".working", self.root / ".working-swapped-out")
                    os.rename(self.root / ".working-replacement", self.root / ".working")
                    state["fired"] = True
                return names

            def spy_read(fd, size):
                try:
                    read_inos.add(os.fstat(fd).st_ino)
                except OSError:
                    pass
                return real_read(fd, size)

            with mock.patch.object(os, "listdir", swap_listdir), \
                    mock.patch.object(os, "read", spy_read):
                result = investigate(self.root, sources=self.sources, targets=self.all_targets())
            self.assertTrue(state["fired"], "the .working swap injection did not fire")
            self.assertEqual(os.lstat(self.root / ".working/toml/manifest.toml").st_ino,
                             replacement_ino)   # the swap really landed at the pathname
            self.assertIn(original_ino, read_inos,
                          "the listed store's manifest was not read at all")
            self.assertNotIn(replacement_ino, read_inos,
                             "the swapped-in replacement manifest was read")
            # The honest outcome over a root mutated mid-investigation stays fail-closed.
            self.assertEqual(result.status, store.CANNOT_EVALUATE)
            self.assertTrue(any("product root changed" in f for f in result.findings),
                            result.findings)

    suite = unittest.defaultTestLoader.loadTestsFromTestCase(PlanningTests)
    expected = suite.countTestCases()
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() and result.testsRun == expected else 1


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-test"]:
        sys.exit(self_test())
    print("usage: _opf_adopt_plan.py --self-test (the verb is `opf adopt plan`)", file=sys.stderr)
    sys.exit(2)
