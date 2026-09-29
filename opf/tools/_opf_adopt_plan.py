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
run's adoption preimage home, named in the import scope, and given no
import-file row; import references are refused as decision inputs. A candidate
at a planned managed destination (a declared view of the resolved or default
manifest, or a path under the planned machine store) is occupying (spec 14.2):
keep refuses, and any other disposition is preserved under the adoption archive.
The revision, release and anchor, prompt pack, enforcement contents and skip
policy are caller inputs, shape-checked and digest-bound, never verified here.
Inventory reads detect ordinary concurrent edits, not a coherent filesystem
snapshot or an adversarial writer restoring stat values. Re-observe at apply.
"""
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
        os.close(child)


def _read_rel(root_fd, path, budget):
    parent, name = store._journal._open_parent(root_fd, path)
    try:
        before = os.stat(name, dir_fd=parent, follow_symlinks=False)
        return _read(parent, name, before, budget)
    finally:
        os.close(parent)


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
        resolution = store.resolve_store(root)
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
                                os.close(child)
            finally:
                os.close(wfd)
        if resolution.status == store.RESOLVED:
            manifest_path = resolution.machine_rel + "/" + store.MANIFEST_NAME
            raw = _read_rel(root_fd, manifest_path, budget)
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
        else:
            homes = 1
            manifest_path = ""
            manifest_digest = ""
            view_targets = None
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
                    os.close(child)
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

        requested = sorted(set(sources) | set(targets) | set(DETECTION_ROOTS))
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
                os.close(parent)
        for path in sources:
            if entries.get(path, {}).get("kind") in (None, "absent", "excluded"):
                raise PlanError("declared source is unavailable: {!r}".format(path))
        if resolution.status != store.RESOLVED:
            # With no resolved store, content at a reserved name (for example counters and
            # records, or import runs, left without a manifest) is still prior ancestry.
            traces += [path for path in ANCESTRY_RESERVED
                       if entries.get(path, {}).get("kind") in ("file", "directory")]
        # Candidate roots are the explicit sources plus .working outside exclusions.
        candidate_roots = sources + [".working"]
        candidates = [
            row["path"] for row in entries.values()
            if row["kind"] == "file"
            and any(_under(row["path"], prefix) for prefix in candidate_roots)
        ]
        # Empty directories are surfaced separately. They are not file operands.
        empty = [
            row["path"] for row in entries.values()
            if row["kind"] == "directory" and row["path"] != ".working"
            and any(_under(row["path"], prefix) for prefix in candidate_roots)
            and not any(p.startswith(row["path"] + "/") for p in entries)
        ]
        check_fd = store._open_dir_nofollow(root)
        try:
            if _stamp(os.fstat(check_fd)) != _stamp(root_stat):
                raise PlanError("product root changed during investigation")
        finally:
            os.close(check_fd)
        # The homes generation and the resolved store's declared view targets are returned beside the
        # observation, never inside it, so the observation bytes are unchanged; both come from the same
        # manifest read that fixed the exclusions.
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
        }, homes, view_targets
    finally:
        os.close(root_fd)


def investigate(product_root, *, sources, targets=()):
    """No output path: return inert canonical bytes. Required source absence refuses."""
    return _investigate(product_root, sources, targets)[0]


def _investigate(product_root, sources, targets):
    """investigate, plus the resolved store's homes generation (legacy 1 when nothing resolved) and its
    declared view targets (None when nothing resolved)."""
    try:
        if not isinstance(product_root, (str, os.PathLike)):
            raise PlanError("product_root must be an absolute path")
        root = Path(product_root)
        if not root.is_absolute() or ".." in root.parts:
            raise PlanError("product_root must be absolute and contain no '..'")
        doc, homes, views = _inventory(root, _roots(sources), _roots(targets))
        return AdoptResult(store.VALID, observation=_seal(doc, "observation_digest")), homes, views
    except (OSError, ValueError, UnicodeError, RecursionError, EmitError,
            store.StoreError, store._journal.JournalError) as exc:
        return AdoptResult(store.CANNOT_EVALUATE, [str(exc)]), 1, None


def _managed_destinations(doc, views):
    """The planned managed destinations a candidate can occupy (spec 14.2), as
    (machine_rel, view_targets). A resolved store contributes its machine store and
    its manifest's declared view targets; otherwise the plan targets the store
    init-store would create: the default machine subdirectory and the default
    manifest's views. The machine subtree is covered whole, as the unmanaged-overlap
    check treats it."""
    if doc["resolution"]["status"] == store.RESOLVED:
        return doc["resolution"]["machine_rel"], frozenset(views)
    import _opf_init
    default = tomllib.loads(_opf_init.build_manifest())
    return (store.WORKING_DIRNAME + "/" + store.DEFAULT_MACHINE_SUBDIR,
            frozenset(v["target"] for v in default["views"].values()))


def _decisions(observation, decisions, run_id, managed):
    """Per-candidate dispositions -> (disposition ops, plan-v2 source rows, unresolved).

    `managed` is (machine_rel, view_targets) from _managed_destinations. A candidate
    at a declared view or under the planned machine store occupies a managed
    destination (spec 14.2): keep refuses, and any other disposition is preserved
    under the adoption archive, a move included."""
    if type(decisions) is not list:
        raise PlanError("decisions must be a list")
    machine_rel, view_targets = managed
    files = {row["path"]: row for row in observation["entries"] if row["kind"] == "file"}
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
        if path not in wanted or path in seen or not schema._is_token(row["actor"]):
            raise PlanError("duplicate/out-of-scope decision or missing actor")
        seen.add(path)
        disposition = row["disposition"]
        digest = files[path]["digest"]
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
        elif disposition == "move" and set(row) == required | {"destination"}:
            destination = _path(row["destination"])
            target = next((r for r in observation["entries"] if r["path"] == destination), None)
            # Only directly observed absence is accepted; declare a move destination
            # in targets when investigating, then pass the same targets to plan.
            if target is None or target["kind"] != "absent":
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
        sources.append(source)
        unresolved.remove(path)
    return ops, sorted(sources, key=lambda r: r["path"]), sorted(unresolved)


def plan(product_root, *, sources, expected_observation_digest, product, decisions,
         ops, now, run_nonce, bindings, targets=()):
    """Re-investigate, bind to the reviewed inventory, then purely freeze the proposal.

    ops is an ordered list of additional PR-A vocabulary rows. It has no authority:
    release member digests, generated postimages, hook diffs and receipts remain
    unverified inputs to later PRs. No generic op can substitute for the explicit
    per-candidate dispositions below.

    bindings carries exactly the caller-supplied plan-v2 inputs
    (schema.PLAN_BINDING_INPUTS). The store identity comes from the observation
    (the resolved or default machine store and its ancestry); the sources, effects,
    completion roster and import policy are derived here, never supplied.
    """
    observed, homes, views = _investigate(product_root, sources, targets)
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
            if row["op"] in ("register-unmanaged", "move-file", "retire-file", "import-file"):
                raise PlanError("disposition ops must come from attributed decisions")
            for field, value in row.items():
                if schema._FIELD_KINDS.get(field) in ("filepath", "dirpath") and value != ".":
                    _path(value)
            if row["op"] == "install-pack":
                for member in row["members"]:
                    _path(member["path"])
        managed = _managed_destinations(doc, views)
        disposition_ops, source_rows, unresolved = _decisions(doc, decisions, run_id, managed)
        for row in disposition_ops:
            checked = schema.validate_op(row, homes=homes)
            if checked.status != store.VALID:
                return AdoptResult(checked.status, checked.findings, observation=observed.observation)
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
        proposal = {
            "format": schema.PLAN_FORMAT, "schema": schema.SCHEMA_VERSION,
            "product": product,
            "inventory_digest": inventory_doc["inventory_digest"],
            "run_id": run_id,
            "created_at": instant,
            "revision": bindings["revision"],
            "store": {"store_root": ".", "machine_rel": managed[0],
                      "adoption": doc["ancestry"]["adoption"]},
            "sources": source_rows,
            "effects": schema.derive_effects(ordered, source_rows),
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

        def observation(self):
            result = investigate(self.root, sources=self.sources, targets=self.targets)
            self.assertEqual(result.status, store.VALID, result.findings)
            return result

        def make_plan(self, observation=None, decisions=None, **changes):
            observation = observation or self.observation()
            args = dict(
                sources=self.sources, targets=self.targets,
                expected_observation_digest=tomllib.loads(
                    observation.observation.decode())["observation_digest"],
                product="opf", decisions=[self.decision] if decisions is None else decisions,
                ops=[{"op": "init-store", "store_root": "."}],
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
            # given no import-file row.
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
            self.assertEqual(p["effects"]["creations"], [{"path": preserved, "digest": digest}])
            self.assertEqual(p["effects"]["removals"], [{"path": "legacy.md", "digest": digest}])
            self.assertEqual([row["op"] for row in p["ops"]], ["init-store"])
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
            self.assertEqual(p["effects"], {"creations": [{"path": preserved, "digest": digest}],
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
                        self.bindings["prompt_pack"], version="latest")))):
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
            # The plan binds the resolved machine store and its re-adoption ancestry.
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

    suite = unittest.defaultTestLoader.loadTestsFromTestCase(PlanningTests)
    expected = suite.countTestCases()
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() and result.testsRun == expected else 1
