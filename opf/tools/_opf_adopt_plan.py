#!/usr/bin/env python3
"""Read-only OPF adoption investigation and planning (PR-B).

Only explicit source roots, .working, and DETECTION_ROOTS are inventoried.
.git is never entered. A resolved machine subtree and declared unmanaged paths
are exclusions, recorded in the inventory; foreign content inside those excluded
subtrees is NOT covered. Companion stores refuse in this slice. Detection is by
path only: a candidate is neither a parsed registration nor a working pipeline.

VALID means an inert, digest-bound proposal, NEVER permission/readiness to apply.
No release is trusted, acceptance verified, hook activated, or transaction run.
Migration without an existing run/acceptance reference remains unresolved.
Even supplied import references need PR-D's staged-run and acceptance checks.
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
    "AGENTS.md", "CLAUDE.md", ".claude", ".cursor", ".github/workflows",
    ".gitlab-ci.yml", "Jenkinsfile", ".circleci", "azure-pipelines.yml",
    "VERSION", "CHANGELOG.md", "release-notes.toml",
)
RESIDUALS = (
    "Only the enumerated scope is covered; detection is by path, not semantics.",
    "Machine-store and unmanaged exclusions are not inspected for foreign files.",
    "No trust, acceptance, store-validity, registration, or behaviour verdict.",
    "Additional ops are proposals; cross-op preconditions and postimages are not proved.",
    "Resolver reads retain the resolver limits; inventory caps are not a process sandbox.",
    "No coherent snapshot; apply must recheck inventory, preimages and absences.",
    "No commit, merge, network, journal, import staging, rendering or hook effects.",
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
        for pointer in (store.POINTER_REL, store.LOCAL_POINTER_REL):
            target = store._read_pointer_target(root_fd, pointer)
            if target is not None and store._target_store_root(target, root) != root:
                raise PlanError("companion/remote store requires separately scoped investigation")
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
        else:
            manifest_path = ""
            manifest_digest = ""
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
            "coverage_residuals": list(RESIDUALS),
        }
    finally:
        os.close(root_fd)


def investigate(product_root, *, sources, targets=()):
    """No output path: return inert canonical bytes. Required source absence refuses."""
    try:
        if not isinstance(product_root, (str, os.PathLike)):
            raise PlanError("product_root must be an absolute path")
        root = Path(product_root)
        if not root.is_absolute() or ".." in root.parts:
            raise PlanError("product_root must be absolute and contain no '..'")
        doc = _inventory(root, _roots(sources), _roots(targets))
        return AdoptResult(store.VALID, observation=_seal(doc, "observation_digest"))
    except (OSError, ValueError, UnicodeError, RecursionError, EmitError,
            store.StoreError, store._journal.JournalError) as exc:
        return AdoptResult(store.CANNOT_EVALUATE, [str(exc)])


def _decisions(observation, decisions):
    if type(decisions) is not list:
        raise PlanError("decisions must be a list")
    files = {row["path"]: row for row in observation["entries"] if row["kind"] == "file"}
    wanted = set(observation["candidates"])
    seen = set()
    ops = []
    unresolved = set(wanted)
    for row in sorted(decisions, key=lambda r: r.get("path", "") if type(r) is dict
                      and isinstance(r.get("path", ""), str) else ""):
        if type(row) is not dict:
            raise PlanError("decision must be a table")
        required = {"path", "disposition", "actor"}
        optional = {"destination", "import_run_id", "acceptance_digest"}
        if not required <= row.keys() or set(row) - required - optional:
            raise PlanError("malformed decision keys")
        path = _path(row["path"])
        if path not in wanted or path in seen or not schema._is_token(row["actor"]):
            raise PlanError("duplicate/out-of-scope decision or missing actor")
        seen.add(path)
        disposition = row["disposition"]
        digest = files[path]["digest"]
        if disposition == "keep" and set(row) == required:
            ops.append({"op": "register-unmanaged", "entry": path,
                        "note": "proposed by " + row["actor"]})
        elif disposition == "retire" and set(row) == required:
            ops.append({"op": "retire-file", "path": path, "preimage_digest": digest})
        elif disposition == "move" and set(row) == required | {"destination"}:
            destination = _path(row["destination"])
            target = next((r for r in observation["entries"] if r["path"] == destination), None)
            # Only directly observed absence is accepted; declare a move destination
            # in targets when investigating, then pass the same targets to plan.
            if target is None or target["kind"] != "absent":
                raise PlanError("move destination has no observed absence")
            ops.append({"op": "move-file", "source": path,
                        "destination": destination, "source_digest": digest})
        elif disposition == "migrate":
            if set(row) == required:
                continue  # no fabricated run id or blanket fragment acceptance
            if set(row) != required | {"import_run_id", "acceptance_digest"}:
                raise PlanError("migration needs both import reference fields")
            ops.append({"op": "import-file", "import_run_id": row["import_run_id"],
                        "acceptance_digest": row["acceptance_digest"]})
        else:
            raise PlanError("unknown disposition or incompatible decision fields")
        unresolved.remove(path)
    return ops, sorted(unresolved)


def plan(product_root, *, sources, expected_observation_digest, product, decisions,
         ops, now, run_nonce, targets=()):
    """Re-investigate, bind to the reviewed inventory, then purely freeze the proposal.

    ops is an ordered list of additional PR-A vocabulary rows. It has no authority:
    release member digests, generated postimages, hook diffs, receipts and import
    acceptance references remain unverified inputs to later PRs. No generic op
    can substitute for the explicit per-candidate dispositions below.
    """
    observed = investigate(product_root, sources=sources, targets=targets)
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
        if type(ops) is not list:
            raise PlanError("ops must be an ordered list")
        for row in ops:
            checked = schema.validate_op(row)
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
        disposition_ops, unresolved = _decisions(doc, decisions)
        for row in disposition_ops:
            checked = schema.validate_op(row)
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
        proposal = {
            "format": schema.PLAN_FORMAT, "schema": schema.SCHEMA_VERSION,
            "product": product,
            "inventory_digest": inventory_doc["inventory_digest"],
            "run_id": "adopt-" + now.strftime("%Y%m%dT%H%M%SZ") + "-" + run_nonce,
            "created_at": instant,
            "ops": _order_ops(disposition_ops, ops),
        }
        frozen = _seal(proposal, "plan_digest")
        checked = schema.validate_plan(tomllib.loads(frozen.decode("utf-8")))
        if checked.status != store.VALID:
            return AdoptResult(checked.status, checked.findings, observation=observed.observation)
        return AdoptResult(store.VALID, observation=observed.observation,
                           inventory=inventory_bytes, plan=frozen)
    except (ValueError, UnicodeError, RecursionError, EmitError) as exc:
        return AdoptResult(store.CANNOT_EVALUATE, [str(exc)], observation=observed.observation)


def _order_ops(dispositions, additional):
    """Move/retire first; create a store before unmanaged registration/import.

    This is ordering of proposals, not compilation of journal effects or proof
    that init/render/receipt preconditions can be satisfied by the current tree.
    """
    inits = [i for i, row in enumerate(additional) if row["op"] == "init-store"]
    if len(inits) > 1:
        raise PlanError("a proposal may initialize only one store")
    cut = inits[0] + 1 if inits else 0
    before = [r for r in dispositions if r["op"] in ("move-file", "retire-file")]
    after = [r for r in dispositions if r["op"] not in ("move-file", "retire-file")]
    unique = []
    imports = {}
    for row in after:
        if row["op"] == "import-file":
            key = row["import_run_id"]
            if key in imports:
                if imports[key] != row:
                    raise PlanError("conflicting acceptance references for an import run")
                continue
            imports[key] = row
        unique.append(row)
    return before + additional[:cut] + unique + additional[cut:]


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
                now=self.now, run_nonce="0123456789abcdef",
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

        def test_unresolved_and_migrate_without_staging(self):
            before = self.snapshot()
            for decisions in ([], [dict(self.decision, disposition="migrate")]):
                with self.subTest(decisions=decisions):
                    result = self.make_plan(decisions=decisions)
                    self.assertEqual(result.status, store.CANNOT_EVALUATE)
                    self.assertEqual(result.unresolved, ("legacy.md",))
                    self.assertIsNone(result.plan)
            self.assertEqual(before, self.snapshot())
            self.assertFalse((self.root / ".working").exists())

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

        def test_foreign_working_preserves_resolver_status(self):
            (self.root / ".working").mkdir()
            (self.root / ".working/notes.md").write_bytes(b"notes")
            result = self.observation()
            doc = tomllib.loads(result.observation.decode())
            self.assertEqual(doc["resolution"]["status"], store.CANNOT_EVALUATE)
            self.assertIn(".working/notes.md", doc["candidates"])
            planned = self.make_plan(result)
            self.assertEqual(planned.unresolved, (".working/notes.md",))

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
                            {"run_nonce": "bad"}, {"ops": [{"op": "import-file"}]}):
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
