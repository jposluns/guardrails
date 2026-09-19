#!/usr/bin/env python3
"""OPF root-ingest DETECTION engine (OPF-MIGRATE MIG-PR1): read-only detection + disposition worksheet.

This is the FIRST slice of root-ingest (REQ-OPF-ROOT-INGEST, spec 14.2), the analogue of the import
layer's read-only `scan_import` leg (`_opf_import.scan_import`): it ENUMERATES the files a store does not
yet manage and emits a digest-stamped DISPOSITION WORKSHEET, read-only. It WIRES NO verb and STAGES NO run
(inert, like `--scan`). Named `_opf_ingest` (not `_opf_migrate`) so it does not collide with `opf migrate`,
which stays store RELOCATION (OQ-1 ruled 2026-09-17). The importer layer, plan composition, review /
acceptance, apply promotion, and the `opf adopt` verb wiring are LATER slices (MIG-PR2..PR6) and are NOT
added here; `opf adopt` continues to hit the fail-closed `KNOWN_VERBS` dispatch, pinned by the module
self-test's dispatch-deferral vector (consciously flipped at MIG-PR6).

Ratified design constraints applied (PD-OPF-MIGRATE-DESIGN OQ-2..7, ratified 2026-09-19):
  - OQ-2: the worksheet row shape is ACCEPTANCE-READY for the SHARED `opf.import.acceptance/v1` format that
    already exists (`_opf_import.ACCEPTANCE_FORMAT`); PR1 introduces NO sibling acceptance format and no
    acceptance artefact (review is MIG-PR4). Each row carries a `disposition`, an `origin` (reusing the
    import provenance vocabulary `_opf_import._ORIGIN_VALUES`), and a content `sha256`, so a later
    accept/reject decision can echo one row per decision, exactly the import acceptance echo model.
  - OQ-3: DECLARED-ONLY detection outside the store. The mandatory STORE scope (every non-OPF-managed file
    under `.working/`) is auto-detected; the product root OUTSIDE `.working/` is inferred NOTHING, and a
    product file becomes a `declared`-scope row ONLY when an operator `--include` pattern names it. Nothing
    outside `.working/` is ever inferred. Fail closed.

Verdict model (single-sourced from `_opf_import`, OQ-C: no local mirror of the 0/1/2 contract): a
`DetectResult` carries `CLEAN` (0) / `FINDING` (1) / `CANNOT_EVALUATE` (2), judged by the verdict value,
never by grepping output. Read-only: `detect` resolves and manifest-validates the store FIRST (the
init-first precondition, exactly as `scan_import`: an unresolved or non-VALID store is CANNOT-EVALUATE,
"run `opf init` first"), enumerates through a contained no-follow walk, writes NOTHING, allocates no run id
(SECI-preview-has-no-side-effects). The worksheet is `now`-free / nonce-free / absolute-root-free, so its
canonical-payload digest is byte-identical across runs (like the scan inventory), a stable binding anchor
that a later `--plan` recomputes and compares.

Disclosed coverage residuals (part of the engine, not a footnote):
  - detection reads a file only to digest it by path + size + sha256; it NEVER decodes content, so a
    non-UTF-8 file is a legitimate detected row, NOT a CANNOT-EVALUATE (this deliberately diverges from
    `scan_import`'s UTF-8-only residual: PR1 detects, it does not import).
  - a file whose bytes exceed the contained-reader ceiling (`_journal._MAX_PRODUCT_READ_BYTES`, 16 MiB) is
    refused fail-closed (CANNOT-EVALUATE naming the file), never silently skipped or accounted partial.
  - the no-follow walk fails CLOSED on ANY symlink, exotic entry (FIFO / device / socket), or unreadable
    entry in a traversed subtree: a refusing CANNOT-EVALUATE naming the entry, never a silent skip
    (check-fails-closed-on-unreadable). The opt-in `--include` product-root walk therefore refuses a
    product subtree that carries a symlink; this is disclosed and deliberate for the read-only preview.
  - `--include` patterns are matched with `fnmatch.fnmatchcase` over POSIX-relative paths, so `*` also
    matches `/`; the pattern semantics are disclosed rather than a full recursive-glob grammar.
  - semantic disposition correctness (which files SHOULD be kept, migrated, or moved) is a human decision,
    out of scope: detect defaults EVERY row to `unresolved` and takes no action on any file (spec 14.2).

Offline, stdlib only, fail-closed. It lives under `opf/tools/` and imports ONLY sibling `opf/tools/`
modules, so the standalone-closure property (OPF-SELF-CONTAIN) holds.

Exit convention (the sibling OPF units): 0 clean, 1 a finding, 2 cannot-evaluate.
"""
import fnmatch
import os
import posixpath
import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _journal        # noqa: E402  contained no-follow primitives (parent-open, contained read)
import _opf_store      # noqa: E402  U1: resolution, manifest, contained reads, containment helpers
import _opf_import     # noqa: E402  reuse the 0/1/2 verdicts, provenance vocab, digest/emit helpers (no fork)


# --- fixed formats, closed vocabularies, and the outcome model ---------------------------------------

# The 0/1/2 verdict contract, single-sourced from the import layer (OQ-C): one contract, no local mirror.
CLEAN = _opf_import.CLEAN                    # 0: a clean detection
FINDING = _opf_import.FINDING                # 1: a validation finding (a bad --include, a store-scope glob)
CANNOT_EVALUATE = _opf_import.CANNOT_EVALUATE  # 2: unresolved store / unreadable / exotic (fail-closed)

# The disposition worksheet format token and schema marker (canonical `_opf_emit` TOML, the store's native
# byte-canonical form, matching `inventory.toml`; JSON is reserved for the shared acceptance record only).
WORKSHEET_FORMAT = "opf-ingest-dispositions-v1"
SCHEMA = 1

# The closed disposition vocabulary (spec 14.2). Detect EMITS ONLY `unresolved` (fail closed: detection
# takes NO default action on any file); the other three are legal values an operator sets before `--plan`
# (MIG-PR3), so the validator accepts the full closed set while detect never produces them.
DISPOSITIONS = ("unresolved", "keep", "migrate", "move")
_DETECT_DISPOSITION = "unresolved"

# The closed scope vocabulary: `store` is the mandatory auto-detected scope under `.working/`; `declared`
# is an operator `--include` match in the product root.
SCOPES = ("store", "declared")

# Reuse the import provenance vocabulary (OQ-2: acceptance-ready, so a later decision echoes the origin the
# import acceptance already enforces). Detect emits only `baseline` (the deterministic classifier).
ORIGINS = _opf_import._ORIGIN_VALUES
_DETECT_ORIGIN = _opf_import._BASELINE_ORIGIN

# The closed per-row keyset and the digest grammar (reused from the import layer: `sha256:`+64 lowercase hex).
_ROW_KEYS = frozenset({"source_path", "scope", "disposition", "origin", "sha256", "size", "note"})
_WORKSHEET_KEYS = frozenset({"format", "schema", "row", "worksheet_digest"})
_DIGEST_RE = _opf_import._DIGEST_RE

# A bound on the opt-in `--include` product-root walk and the mandatory `.working/` walk: a runaway or a
# hostile deep tree is refused fail-closed rather than enumerated unboundedly (SECA resource-bounds).
_MAX_WALK_ENTRIES = 1 << 20


class DetectResult:
    """The inert result of a read-only DETECTION (spec 14.2). Judged by its verdict, never by grepping
    output. On a clean detection `worksheet` is the frozen, canonical worksheet payload (carrying its own
    `worksheet_digest`), `rows` the ordered disposition rows, and `worksheet_digest` the reproducibility
    anchor. Detect writes NOTHING and allocates no run id (SECI-preview-has-no-side-effects)."""
    __slots__ = ("verdict", "findings", "worksheet", "worksheet_digest", "rows")

    def __init__(self, verdict, findings=None, worksheet=None, worksheet_digest=None, rows=None):
        self.verdict = verdict                    # CLEAN / FINDING / CANNOT_EVALUATE
        self.findings = findings or []
        self.worksheet = worksheet or {}
        self.worksheet_digest = worksheet_digest
        self.rows = rows or []


class _DetectError(Exception):
    """An input detection cannot use, carrying the verdict so a finding (verdict 1: a bad --include, a
    store-scope glob) and an unreadable/exotic/unresolvable input (verdict 2) are distinguished at the raise
    site rather than collapsed. Callers convert it into a DetectResult (mirrors `_opf_import._StageError`)."""
    def __init__(self, verdict, message):
        super().__init__(message)
        self.verdict = verdict
        self.message = message


def _finding(msg):
    return _DetectError(FINDING, msg)


def _cannot(msg):
    return _DetectError(CANNOT_EVALUATE, msg)


# --- canonical worksheet emission --------------------------------------------------------------------

def _worksheet_bytes(payload):
    """Byte-canonical TOML bytes of the worksheet payload (U8 via the import layer's checked emitter). A
    value outside the constrained subset, or a failed round trip, is CANNOT-EVALUATE, fail-closed."""
    try:
        return _opf_import._emit_bytes(payload, "ingest worksheet")
    except _opf_import._StageError as exc:
        raise _cannot("worksheet is not byte-canonically emittable ({})".format(exc.message))


def _build_worksheet(rows):
    """Assemble the canonical worksheet payload from the ordered rows and stamp its content digest. The
    payload carries no timestamp, run id, nonce, or absolute root, so it is byte-identical across runs
    (like the scan inventory); the digest is `sha256:`+hex over its canonical serialization."""
    payload = {"format": WORKSHEET_FORMAT, "schema": SCHEMA, "row": rows}
    digest = "sha256:" + _opf_import._sha256_hex(_worksheet_bytes(payload))
    worksheet = dict(payload)
    worksheet["worksheet_digest"] = digest
    return worksheet, digest


def _row(source_path, scope, sha256, size):
    """One closed-keyset disposition row. Detect always emits `unresolved` / `baseline`; `note` is a
    present, string-typed, empty field (the closed keyset is fixed so the worksheet shape is stable). This is
    a pure row constructor: it does NOT re-decide whether `source_path` is a legal worksheet path. That is the
    SOLE job of `validate_worksheet`, which `detect` runs over the assembled worksheet BEFORE returning CLEAN
    (single authority: no parallel `_is_contained_relpath` proxy here to drift from the validator), so a
    source_path detection accepts but the validator rejects (a name whose second character is `:`, which the
    contained reader accepts but `_is_contained_relpath` reads as a drive path) surfaces as a LOCATED
    CANNOT-EVALUATE from that one check, never a CLEAN worksheet that then fails its own validator."""
    return {"source_path": source_path, "scope": scope, "disposition": _DETECT_DISPOSITION,
            "origin": _DETECT_ORIGIN, "sha256": sha256, "size": size, "note": ""}


# --- contained no-follow enumeration (mirrors _opf_check._list_contained / _opf_store._immediate_subdirs) -

def _walk_regular_files(dfd, prefix, prune, out, budget, pruned_dirs=None):
    """Recursively collect the regular-file paths beneath the OPEN directory fd `dfd`, no-follow. `prefix`
    is the POSIX relpath of `dfd` from the walk root ("" for the root). A subdirectory whose relpath is in
    `prune` is skipped entirely and NEVER ENTERED, which (because the walk reaches every directory at its own
    level) skips its whole subtree: the machine store subtree and the reserved `.working/imports/` tree, or
    `.working/` itself for the declared-scope product walk, PLUS every covered managed directory the caller
    adds (a declared-unmanaged directory, a view / deliverable target, or the store-root `.aiqt` / `.git`
    control trees). Pruning a covered directory BEFORE descent is load-bearing: an excluded subtree is
    neither listed nor read, so an unreadable, symlinked, or exotic entry inside a subtree we would exclude
    anyway cannot block detection with a spurious CANNOT-EVALUATE (spec 14.2 never reads / enters an
    unmanaged path). Fails CLOSED (a refusing CANNOT-EVALUATE
    naming the entry) on a symlink, an exotic entry (neither a directory nor a regular file), or an I/O
    error, so a swapped or exotic entry is never silently skipped (check-fails-closed-on-unreadable,
    SECI-symlink-resolution). Bounded by `budget` (a one-element mutable counter) so a runaway or hostile
    deep tree is refused rather than enumerated unboundedly (SECA resource-bounds). When `pruned_dirs` is
    a list, every pruned subdirectory's relpath is appended to it (the covered directories that ACTUALLY
    EXIST, encountered and skipped before descent), so a caller can tell an existing-but-excluded subtree
    from an absent one without entering it (F2: existence is separate from exclusion)."""
    try:
        entries = sorted(os.listdir(dfd))
    except OSError as exc:
        raise _cannot("cannot list {!r} no-follow ({})".format(prefix or ".", exc))
    for entry in entries:
        rel = entry if not prefix else prefix + "/" + entry
        budget[0] += 1
        if budget[0] > _MAX_WALK_ENTRIES:
            raise _cannot("detection set exceeds the {}-entry ceiling (fail-closed)".format(_MAX_WALK_ENTRIES))
        # A non-UTF-8 (surrogate-escaped) entry name cannot be represented in the byte-canonical worksheet
        # (a TOML string is UTF-8) and would raise uncaught when sorted or digested: refuse it fail-closed,
        # naming it, rather than crash or silently skip (check-fails-closed-on-unreadable).
        try:
            rel.encode("utf-8")
        except UnicodeEncodeError:
            raise _cannot("{!r} has a non-UTF-8 name that cannot be represented in the worksheet "
                          "(fail-closed, never followed or silently skipped)".format(rel))
        try:
            est = os.stat(entry, dir_fd=dfd, follow_symlinks=False)
        except OSError as exc:
            raise _cannot("cannot stat {!r} ({})".format(rel, exc))
        if stat.S_ISDIR(est.st_mode):
            if rel in prune:
                if pruned_dirs is not None:
                    pruned_dirs.append(rel)
                continue
            try:
                sub = os.open(entry, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=dfd)
            except OSError as exc:
                raise _cannot("cannot open directory {!r} no-follow ({})".format(rel, exc))
            try:
                _walk_regular_files(sub, rel, prune, out, budget, pruned_dirs)
            finally:
                os.close(sub)
        elif stat.S_ISREG(est.st_mode):
            out.append(rel)
        else:
            raise _cannot("{!r} is neither a directory nor a regular file (an exotic or symlinked entry; "
                          "fail-closed, never followed)".format(rel))
    return out


def _digest_of(root_fd, rel):
    """The observed content digest and byte length of a contained regular file, read no-follow through the
    shared contained reader. Detection never DECODES the bytes (a non-UTF-8 file is a normal row). A read
    error (an unreadable entry, a hardlinked/swapped victim, a file over the reader ceiling) is a refusing
    CANNOT-EVALUATE naming the file, never a silent skip."""
    try:
        raw, _fst = _journal._read_contained(root_fd, rel)
    except _journal.JournalError as exc:
        raise _cannot("cannot read {!r} ({}); fail-closed".format(rel, exc))
    return "sha256:" + _opf_import._sha256_hex(raw), len(raw)


# --- the OPF-managed set (OQ-B: derived from the resolved manifest authorities, never hard-coded) --------

# The store-root control / VCS directory names are the SINGLE store-topology authority
# `_opf_store.STORE_ROOT_CONTROL_DIRS`, the EXACT tuple `_opf_import._assemble_preview` drops at the store
# root (its `_ignore`, which drops these at the store root only, never a same-named dir nested deeper). Ingest
# holds NO parallel literal of its own and derives its store-root control exclusion from that one constant, so
# the two can never mirror-drift (the true single-authority premise shift: one source, no mirror). `.aiqt` is
# the control UMBRELLA: the apply-promotion ops + archive trees (`_opf_import.IMPORT_OPS_REL` /
# `IMPORT_JOURNAL_REL` / `IMPORT_ARCHIVE_REL`) and migrate's `.aiqt/migration/journal` ALL nest under it, so
# excluding the whole `.aiqt` SUBTREE covers every control / journal / archive tree BY CONSTRUCTION rather
# than by re-enumerating each; `.git` is the VCS dir. The self-test asserts SET EQUALITY between the exclusion
# ingest builds and that authority, and that those import-layer constants stay under `.aiqt`, so a reintroduced
# literal or an ops tree relocated out from under `.aiqt` is caught as drift.


def _canonical_managed(p):
    """Canonicalize a manifest-declared managed path to the byte-canonical POSIX-relative spelling the
    no-follow walk produces, so a non-canonical BUT VALID declaration (`./x`, `x//y`, `x/./y`) still
    matches the file it names and cannot evade the managed-exclusion set (the literal-string comparison
    would otherwise miss an aliased spelling). Called only on a path the manifest validator already
    accepted as contained (`_is_contained_relpath`), so `posixpath.normpath` cannot escape the root or
    yield an empty result."""
    return posixpath.normpath(p)


def _under_any(p, prefixes):
    """True when the canonical POSIX-relative path `p` EQUALS, or lies UNDER, any prefix in `prefixes`
    (subtree containment). This is the same grading `_opf_check.managed_file` applies via
    `_opf_check._under_any` against its `valid_unmanaged` authority, so the ingest exclusion covers a
    declared-unmanaged DIRECTORY exactly as the checker does: a file under a valid unmanaged subtree is
    managed, never enumerated as a stray and never read (spec 14.2). The trailing `/` keeps a name-prefix
    sibling (`report.md.bak` beside `report.md`) from being falsely treated as contained."""
    for pref in prefixes:
        if p == pref or p.startswith(pref + "/"):
            return True
    return False


def _store_root_control_prefixes(resolution):
    """The store-root control / VCS subtrees (`.aiqt`, `.git`) as PRODUCT-root-relative POSIX prefixes when
    the store falls UNDER the product root (`.aiqt` / `.git` inline, `ops/.aiqt` / `ops/.git` for a `dir:ops`
    relocation), else () (the store resolves OUTSIDE the product root, so the declared product-root walk
    never reaches it). Anchored at the RESOLVED store root EXACTLY as `_store_working_under_product` anchors
    `.working`, so a relocated store's control trees are excluded wherever they resolve and ONLY the
    store-root `.aiqt` / `.git` (never a same-named dir nested deeper, e.g. `.working/.aiqt`) is covered,
    mirroring `_opf_import._assemble_preview`'s store-root-only drop. `_managed_paths` returns these as its
    separate `control` set, joined to the covered set of the DECLARED (product-root) scope ONLY (never store
    scope, whose STORE-relative paths a `.working`-nested store's product-relative prefix could otherwise
    collide with; see `_managed_paths`), so the declared scope never reads an apply-promotion ops / journal /
    archive tree or the VCS dir (spec 14.2 never reads a managed path); because the `.aiqt` subtree is a
    prefix, every tree nesting under it is covered by construction (see `_opf_store.STORE_ROOT_CONTROL_DIRS`)."""
    try:
        rel = Path(os.path.abspath(resolution.store_root)).relative_to(
            Path(os.path.abspath(resolution.product_root)))
    except ValueError:
        return ()
    base = rel.as_posix()
    return tuple(posixpath.normpath(posixpath.join(base, d)) for d in _opf_store.STORE_ROOT_CONTROL_DIRS)


def _managed_paths(resolution, manifest_data):
    """The CLOSED "OPF-managed" set, derived FROM the resolved store authorities rather than a parallel
    hand-copied list (guard-input-soundness). Returns (prune_prefixes, covered, control):
      - prune_prefixes: store-relative directory subtrees never detected under `.working/`: the machine
        store subtree (`resolution.machine_rel`, which contains the manifest, the typed indexes, and the
        control ledgers) and the reserved `.working/imports/` run tree (`_opf_import.IMPORTS_REL`).
      - covered: managed paths matched by SUBTREE containment (`_under_any`), exactly as
        `_opf_check.managed_file` grades a file against its `valid_unmanaged` authority: a path that EQUALS
        a covered entry OR lies UNDER one is managed wherever it falls, neither enumerated as a stray nor
        read. The entries are the manifest's declared view / deliverable targets
        (`[views].*.target` / `[deliverables].*.target`), its already-declared `[unmanaged].paths` (each
        canonicalized by `_canonical_managed` so a non-canonical but valid spelling cannot evade exclusion),
        and the store pointer control files (`.opf.toml` / `.opf.local.toml`) at the PRODUCT root (OPF
        control files, never ingestible). Because membership is by containment, a declared-unmanaged
        DIRECTORY covers its whole subtree so tooling never reads an unmanaged path (spec 14.2), and a
        view / deliverable target is defended the same way should one ever be a directory (single-file
        today, but subtree-prefix membership is the correct rule, low-cost, and closes the class).
      - control: the store-root control / VCS subtrees (`.aiqt` / `.git`, product-relative via
        `_store_root_control_prefixes`), mirroring `_opf_import._assemble_preview`'s store-root drop set,
        derived from that shared authority rather than a parallel hand-list (guard-input-soundness).
        Returned SEPARATELY from `covered` and applied to the DECLARED (product-root) scope ONLY, never to
        store scope: store scope walks the store's own `.working/` and grades STORE-relative paths, while
        the control trees sit at the store ROOT (siblings of `.working/`, never under it), so excluding them
        there is unnecessary AND, for a store relocated UNDER the product's own `.working/` (`dir:.working/
        ops`), a product-relative prefix such as `.working/ops/.aiqt` would collide with a store-relative
        path and silently suppress a real store stray. Binding each exclusion to its correct resolved root
        keeps the store-relative and product-relative namespaces from cross-contaminating (F1)."""
    prune = {resolution.machine_rel, _opf_import.IMPORTS_REL}
    covered = set()
    for section in ("views", "deliverables"):
        tables = manifest_data.get(section)
        if isinstance(tables, dict):
            for tbl in tables.values():
                if isinstance(tbl, dict) and isinstance(tbl.get("target"), str):
                    covered.add(_canonical_managed(tbl["target"]))
    unmanaged = manifest_data.get("unmanaged")
    if isinstance(unmanaged, dict):
        paths = unmanaged.get("paths")
        if isinstance(paths, list):
            for p in paths:
                if isinstance(p, str):
                    covered.add(_canonical_managed(p))
    # The store pointer control files at the product root are OPF control files, managed wherever they fall
    # and never ingestible, so an `--include` naming one yields no declared row (C1).
    covered.add(_opf_store.POINTER_REL)
    covered.add(_opf_store.LOCAL_POINTER_REL)
    # The store-root control / VCS subtrees (`.aiqt` / `.git`), product-relative and anchored at the
    # RESOLVED store root, mirror `_opf_import._assemble_preview`'s store-root drop set so the declared
    # product-root scope never enumerates or reads an apply-promotion ops / journal / archive tree or the
    # VCS dir. The whole `.aiqt` subtree is a prefix, so every tree nesting under it (IMPORT_OPS_REL,
    # IMPORT_ARCHIVE_REL, migrate's `.aiqt/migration/journal`) is covered BY CONSTRUCTION, not by a parallel
    # hand-list that keeps missing an authority (the F-MAJOR premise shift; guard-input-soundness). They are
    # PRODUCT-relative, so they are returned SEPARATELY and joined ONLY to the DECLARED-scope covered set,
    # never store scope, so a `.working`-nested store's prefix cannot collide with a store-relative path (F1).
    control = set(_store_root_control_prefixes(resolution))
    return prune, covered, control


# --- detection ---------------------------------------------------------------------------------------

def _detect_store_scope(store_fd, prune, covered):
    """Enumerate every non-OPF-managed regular file under `.working/` (the mandatory store scope). Opens
    the `.working/` directory no-follow beneath the resolved store-root fd, walks it pruning the managed
    subtrees, and drops any file a `covered` entry manages by SUBTREE containment (`_under_any`): a path
    that equals a covered entry OR lies under a declared-unmanaged directory is skipped BEFORE it is read,
    so an unmanaged subtree's contents are never digested (spec 14.2). Each survivor is a `store`-scope row
    defaulting to `unresolved` / `baseline`."""
    try:
        working_fd = os.open(_opf_store.WORKING_DIRNAME, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                             dir_fd=store_fd)
    except FileNotFoundError:
        # A resolved VALID store carries a `.working/` tree; its absence now is a race, fail-closed.
        raise _cannot("{} vanished under the resolved store root (fail-closed)".format(
            _opf_store.WORKING_DIRNAME))
    except OSError as exc:
        raise _cannot("cannot open {} no-follow ({})".format(_opf_store.WORKING_DIRNAME, exc))
    try:
        files = []
        # Prune the machine / imports subtrees AND every covered managed subtree BEFORE descent, so a
        # declared-unmanaged directory under `.working/` is never entered or read (F1: traverse-before-
        # exclude). The result-stage `_under_any` below still drops a covered FILE (a directory prune skips
        # only directories); together an unmanaged path is neither enumerated nor read (spec 14.2).
        _walk_regular_files(working_fd, _opf_store.WORKING_DIRNAME, prune | covered, files, [0])
    finally:
        os.close(working_fd)
    rows = []
    for rel in files:
        if _under_any(rel, covered):
            continue
        digest, size = _digest_of(store_fd, rel)
        rows.append(_row(rel, "store", digest, size))
    return rows


def _store_working_under_product(resolution):
    """The resolved store's `.working` subtree as a PRODUCT-root-relative POSIX path when the store falls
    UNDER the product root (".working" for an inline/default store, "ops/.working" for a `dir:ops`
    relocation), else None (the store resolves OUTSIDE the product root, so the product-root declared walk
    never reaches it). Derived from the RESOLVED store location (`resolution.store_root` against
    `resolution.product_root`), the same authority `_detect_store_scope` walks, so a relocated store's own
    subtree is excluded from declared scope wherever it resolves, not only the literal product-root
    `.working`."""
    try:
        rel = Path(os.path.abspath(resolution.store_root)).relative_to(
            Path(os.path.abspath(resolution.product_root)))
    except ValueError:
        return None
    return posixpath.normpath(posixpath.join(rel.as_posix(), _opf_store.WORKING_DIRNAME))


def _detect_declared_scope(product_root, include, covered, store_working_rel):
    """Enumerate the product-root files an operator `--include` pattern names (the opt-in declared scope,
    OQ-3). Each pattern is validated FIRST (contained, root-relative, and never naming the auto-detected
    store scope), then the product root is walked no-follow ONCE (pruning the store `.working/` subtree,
    which is store scope), and each pattern is matched with `fnmatch.fnmatchcase`. `store_working_rel` is the
    resolved store's `.working` subtree as a product-relative POSIX path (".working" inline, "ops/.working"
    for a `dir:ops` relocation, or None when the store resolves outside the product root), derived from the
    RESOLVED store location so a relocated store's machine / imports / stray files are excluded from declared
    scope wherever they resolve and are never emitted as declared rows; the literal product-root `.working/`
    is always off-limits too (the pre-relocation contract). A pattern that matches no product file is a
    FINDING (declared-input must resolve, OQ-A, matching `scan_import`'s refusal of an absent declared
    source). Each matched file is a `declared`-scope row (union across patterns, digested once), EXCEPT a
    path a `covered` entry manages by SUBTREE containment (`_under_any`): a `[views]`/`[deliverables]`
    target, a declared `[unmanaged]` path (or a file UNDER a declared-unmanaged directory), or a store
    pointer control file, which commonly falls at the product root. It is excluded before it is read,
    exactly as the store scope excludes it, so an `--include` cannot pull an already-managed path (or a
    file inside an unmanaged subtree) into declared scope (OQ-3 contract; spec 14.2 never reads an
    unmanaged path)."""
    scope_prefixes = {_opf_store.WORKING_DIRNAME}
    if store_working_rel is not None:
        scope_prefixes.add(store_working_rel)
    for pat in include:
        if not (isinstance(pat, str) and _opf_store._is_contained_relpath(pat)):
            raise _finding("--include pattern {!r} is not a contained root-relative pattern (no absolute "
                           "path, no '..' escape)".format(pat))
        named = next((pref for pref in scope_prefixes if pat == pref or pat.startswith(pref + "/")), None)
        if named is not None:
            raise _finding("--include pattern {!r} names the {} store scope, which is detected "
                           "automatically; declared scope is the product root only".format(pat, named))
    fd = _opf_store._open_root_fd(Path(os.path.abspath(product_root)))
    try:
        files = []
        pruned_dirs = []
        # Prune the store `.working/` scope AND every covered managed subtree BEFORE descent (F1:
        # traverse-before-exclude), so a declared-unmanaged directory or the store-root `.aiqt` / `.git`
        # control trees are never entered or read even when an entry inside them is unreadable / exotic.
        # `pruned_dirs` collects every covered directory that ACTUALLY EXISTS (was encountered and skipped),
        # so the no-match check can tell an existing-but-excluded subtree from an absent one WITHOUT
        # entering it (F2: separate existence from exclusion).
        _walk_regular_files(fd, "", set(scope_prefixes) | covered, files, [0], pruned_dirs)
        # The covered directories that EXIST on the real filesystem (pruned before descent). A pattern that
        # points at, or under, one resolves to a present-but-excluded subtree; a covered path that does NOT
        # exist leaves no entry here, so a pattern under it stays an (unresolved) no-match finding.
        existing_covered = {d for d in pruned_dirs if d in covered}
        matched = set()
        for pat in include:
            hits = [f for f in files if fnmatch.fnmatchcase(f, pat)]
            # A pattern with no non-excluded hit is a no-match FINDING (a declared input must resolve),
            # SEPARATING existence from exclusion (F2): it stays a finding UNLESS it points at or under a
            # covered path that ACTUALLY EXISTS (`existing_covered`), which resolves to a present-but-excluded
            # subtree (never read), not an absent input. A covered path that does NOT exist is still a
            # no-match finding, so an `--include` naming an ABSENT declared-unmanaged path is caught even
            # though the path is covered (round-5 conflated covered with existing, letting an absent covered
            # path bypass the finding). A covered FILE that exists is walked into `files`, so a pattern
            # naming one already gets a hit and never reaches this branch.
            if not hits and not _under_any(pat, existing_covered):
                raise _finding("--include pattern {!r} matched no product-root file (a declared input must "
                               "resolve; fail-closed)".format(pat))
            matched.update(hits)
        rows = []
        for rel in sorted(matched):
            if _under_any(rel, covered):
                continue      # excluded wherever it falls (equal to, or under, a covered path), unread
            digest, size = _digest_of(fd, rel)
            rows.append(_row(rel, "declared", digest, size))
    finally:
        os.close(fd)
    return rows


def _detect_rows(product_root, resolution, include):
    """Assemble the ordered disposition rows: the mandatory store scope plus the opt-in declared scope. The
    rows are sorted by (source_path bytes, scope) so the worksheet digest is invariant to enumeration and
    `--include` order (determinism)."""
    store_fd = _opf_store._open_store_root_fd(resolution.store_root,
                                              resolution.pointer_source != "default")
    try:
        manifest_rel = "{}/{}".format(resolution.machine_rel, _opf_store.MANIFEST_NAME)
        try:
            manifest_data = _opf_store._read_toml_contained(store_fd, manifest_rel)
        except _opf_store.StoreError as exc:
            raise _cannot(str(exc))
        if not isinstance(manifest_data, dict):
            raise _cannot("store manifest {} vanished after validation (fail-closed)".format(manifest_rel))
        # The exclusion set is derived from THIS read, so re-validate it rather than trusting a bare dict
        # check: a racing rewrite to a parseable-but-non-VALID manifest between the init-first validation and
        # here must fail closed, never feed exclusions from an unvalidated re-read (guard-input-soundness).
        reval = _opf_store.validate_manifest(manifest_data)
        if reval.status != _opf_store.VALID:
            raise _cannot("store manifest {} is not VALID on re-read ({}: {}); fail-closed".format(
                manifest_rel, reval.status, "; ".join(reval.findings)))
        prune, covered, control = _managed_paths(resolution, manifest_data)
        rows = _detect_store_scope(store_fd, prune, covered)
    finally:
        os.close(store_fd)
    if include:
        # The store-root control / VCS prefixes are PRODUCT-relative, so they join the covered set for the
        # DECLARED product-root scope ONLY (never store scope; see `_managed_paths` for why).
        rows += _detect_declared_scope(product_root, include, covered | control,
                                       _store_working_under_product(resolution))
    rows.sort(key=lambda r: (r["source_path"].encode("utf-8"), r["scope"]))
    return rows


def detect(product_root, include=None):
    """Deterministically DETECT the non-OPF-managed files of a store into a digest-stamped disposition
    worksheet (spec 14.2), read-only. Resolves and manifest-validates the store FIRST (the init-first
    precondition: an unresolved or non-VALID store is CANNOT-EVALUATE, so detection cannot run against a
    location with no store), then enumerates the mandatory `.working/` store scope and the opt-in
    `--include` declared scope through a contained no-follow walk. Writes NOTHING and allocates no run id
    (SECI-preview-has-no-side-effects). `include` is None or a list of `fnmatch` pattern strings. Returns a
    DetectResult; fail-closed on anything unresolved, unreadable, or exotic."""
    try:
        _journal.require_containment()
    except _journal.JournalError as exc:
        return DetectResult(CANNOT_EVALUATE, ["{}; fail-closed".format(exc)])
    try:
        if not isinstance(product_root, (str, os.PathLike)):
            raise _cannot("product_root must be a path string or os.PathLike, got {}".format(
                type(product_root).__name__))
        if include is not None and not (isinstance(include, (list, tuple))
                                        and all(isinstance(p, str) for p in include)):
            raise _cannot("include must be None or a list of pattern strings")
        # Init-first precondition: resolve and validate the store before enumerating (exactly as
        # scan_import), so detection has the manifest that defines what is OPF-managed.
        try:
            resolution = _opf_store.resolve_store(product_root)
            if resolution.status != _opf_store.RESOLVED:
                raise _cannot("store did not resolve ({}: {}); run `opf init` first".format(
                    resolution.status, resolution.detail))
            mv = _opf_store.load_manifest(resolution)
        except ValueError as exc:
            raise _cannot("cannot parse store manifest for {!r} ({})".format(product_root, exc))
        if mv.status != _opf_store.VALID:
            raise _cannot("store manifest is not VALID ({}: {}); run `opf init` first".format(
                mv.status, "; ".join(mv.findings)))

        rows = _detect_rows(product_root, resolution, include)
        worksheet, digest = _build_worksheet(rows)
        # Validate the assembled worksheet against its own validator BEFORE returning CLEAN: `validate_worksheet`
        # is the SINGLE authority on what is a legal worksheet, so a source_path detection accepted (the
        # contained reader read it) but the validator rejects (a `:` in its second character, which
        # `_is_contained_relpath` reads as a drive path) is a LOCATED CANNOT-EVALUATE naming the offending
        # path, never a CLEAN worksheet that then fails its own validator (guard-input-soundness: detect never
        # emits a clean result its validator would reject).
        ws_findings = validate_worksheet(worksheet)
        if ws_findings:
            bad = ", ".join(sorted({r["source_path"] for r in rows
                                    if isinstance(r.get("source_path"), str)})) or "(none)"
            raise _cannot("the assembled worksheet fails its own validator (fail-closed, never a clean "
                          "detection whose worksheet is invalid); detected source_path(s): {}; findings: "
                          "{}".format(bad, "; ".join(ws_findings)))
        return DetectResult(CLEAN, worksheet=worksheet, worksheet_digest=digest, rows=rows)
    except _DetectError as exc:
        return DetectResult(exc.verdict, [exc.message])
    except _journal.JournalError as exc:
        return DetectResult(CANNOT_EVALUATE, ["{}; fail-closed".format(exc)])
    except OSError as exc:
        return DetectResult(CANNOT_EVALUATE, ["fail-closed on a filesystem read error: {}".format(exc)])
    except RecursionError as exc:
        return DetectResult(CANNOT_EVALUATE, ["fail-closed on excessive input nesting: {}".format(exc)])


# --- worksheet validation (consumed by the gate) -----------------------------------------------------

def _reader_reads(source_path):
    """True when the CONTAINED READER (`_journal._check_rel`, the predicate `_read_contained` applies via
    `_open_parent`) would accept `source_path`. The worksheet validator reuses the reader's OWN check so it
    can never accept a `source_path` (a control character, a backslash, a `.`/`..` segment) the reader then
    rejects at read time: a valid digest must not make an unreadable path ingestible (guard-input-soundness,
    validate against the real reader rather than a weaker parallel predicate)."""
    try:
        _journal._check_rel(source_path)
        return True
    except _journal.JournalError:
        return False


def validate_worksheet(worksheet):
    """Validate a disposition worksheet against its closed schema and vocabulary and recompute its content
    digest. Returns a list of finding strings (empty means valid). A structural or vocabulary violation is
    a finding; the recorded `worksheet_digest` must recompute over the row set (so a mutated payload, or a
    reordered row set, is caught). Fail-closed: an unparseable payload is findings, never a silent pass."""
    if not isinstance(worksheet, dict):
        return ["worksheet is not a table"]
    findings = []
    extra = set(worksheet) - _WORKSHEET_KEYS
    if extra:
        # A non-string top-level key is itself an unknown key: render every extra key through str() before
        # sorting / joining, so a `{1: ...}` key is a refusing finding rather than a TypeError from sorting
        # or joining a mixed-type set (F3; fail-closed, never crash).
        findings.append("worksheet carries unknown key(s): {}".format(
            ", ".join(sorted(str(k) for k in extra))))
    if worksheet.get("format") != WORKSHEET_FORMAT:
        findings.append("worksheet.format is not {!r}".format(WORKSHEET_FORMAT))
    sch = worksheet.get("schema")
    if not (type(sch) is int and sch == SCHEMA):    # type-strict: True (== 1) and 1.0 (== 1) are not the version
        findings.append("worksheet.schema is not {!r}".format(SCHEMA))
    rows = worksheet.get("row")
    if not isinstance(rows, list):
        findings.append("worksheet.row must be a list of disposition rows")
        return findings
    for i, r in enumerate(rows):
        where = "worksheet.row[{}]".format(i)
        if not isinstance(r, dict):
            findings.append("{}: a row must be a table".format(where))
            continue
        if set(r) != _ROW_KEYS:
            findings.append("{}: a row is a closed keyset {{source_path, scope, disposition, origin, "
                            "sha256, size, note}}".format(where))
            continue
        sp = r.get("source_path")
        if not (isinstance(sp, str) and _opf_store._is_contained_relpath(sp)):
            findings.append("{}: source_path must be a contained root-relative path".format(where))
        elif not _reader_reads(sp):
            findings.append("{}: source_path {!r} is not a path the contained reader accepts (a control "
                            "character or a `.`/`..` segment), so a valid digest cannot make it "
                            "ingestible".format(where, sp))
        if r.get("scope") not in SCOPES:
            findings.append("{}: scope {!r} is not one of {}".format(where, r.get("scope"), list(SCOPES)))
        if r.get("disposition") not in DISPOSITIONS:
            findings.append("{}: disposition {!r} is not one of {}".format(
                where, r.get("disposition"), list(DISPOSITIONS)))
        if r.get("origin") not in ORIGINS:
            findings.append("{}: origin {!r} is not one of {}".format(where, r.get("origin"), list(ORIGINS)))
        sha = r.get("sha256")
        if not (isinstance(sha, str) and _DIGEST_RE.match(sha)):
            findings.append("{}: sha256 must be `sha256:`+64 lowercase hex".format(where))
        size = r.get("size")
        if not (type(size) is int and size >= 0):
            findings.append("{}: size must be a non-negative integer".format(where))
        if not isinstance(r.get("note"), str):
            findings.append("{}: note must be a string".format(where))
    payload = {"format": worksheet.get("format"), "schema": worksheet.get("schema"), "row": rows}
    try:
        recomputed = "sha256:" + _opf_import._sha256_hex(_worksheet_bytes(payload))
    except _DetectError as exc:
        findings.append("worksheet payload is not canonically emittable ({})".format(exc.message))
        return findings
    if worksheet.get("worksheet_digest") != recomputed:
        findings.append("worksheet_digest {!r} does not recompute over the row set (got {!r})".format(
            worksheet.get("worksheet_digest"), recomputed))
    return findings


# --- self-test ---------------------------------------------------------------------------------------

def self_test():
    """Detection + worksheet invariants over synthetic stores, with a discriminating vector per guarantee
    (change-carries-check). Judged on returned verdict / finding values, never by grepping output. The
    tempdir is removed in a finally (test-hermeticity)."""
    import shutil
    import tempfile

    try:
        _journal.require_containment()
    except _journal.JournalError as exc:
        print("OPF-INGEST SELF-TEST ERROR: {}; fail-closed".format(exc), file=sys.stderr)
        return 2

    failures = []

    def check(label, cond):
        if not cond:
            failures.append(label)

    def manifest_text(extra_top=""):
        lines = [
            "[opf]", 'standard = "opf"',
            'spec_version = "{}"'.format(_opf_store.SUPPORTED_SPEC_VERSION),
            'layout = "inline"', 'posture = "required"', 'import_status = "none"',
            "", "[store]", 'sync_target = ""',
            "", "[modules]", "governance = true",
            "", "[types.backlog_item]", 'namespace = "BI"',
            "", "[vendors]", 'registered = []',
        ]
        if extra_top:
            lines += ["", extra_top]
        return "\n".join(lines) + "\n"

    base = Path(tempfile.mkdtemp(prefix="opf-ingest-selftest-")).resolve()
    counter = [0]

    def build_store(strays=None, product=None, manifest_extra="", machine="toml"):
        """A synthetic resolved store: root/.working/<machine>/{manifest,counters}.toml. `strays` maps a
        store-relative path (under .working/) to text; `product` maps a product-root-relative path
        (outside .working/) to text. Returns (root, machine_dir)."""
        counter[0] += 1
        root = base / "case-{:02d}".format(counter[0])
        machine_dir = root / ".working" / machine
        machine_dir.mkdir(parents=True)
        (machine_dir / "manifest.toml").write_text(manifest_text(manifest_extra), encoding="utf-8")
        (machine_dir / "counters.toml").write_text(
            "schema = 1\n\n[counters]\nBI = 0\nLF = 0\nWL = 0\n", encoding="utf-8")
        for rel, text in (strays or {}).items():
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")
        for rel, text in (product or {}).items():
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")
        return root, machine_dir

    def build_relocated(subdir="ops", strays=None, product=None, manifest_extra=""):
        """A synthetic RELOCATED store: a committed `.opf.toml` at the product root names `dir:<subdir>`, so
        the store resolves at `<root>/<subdir>/.working/toml` (store_root != product_root). `strays` are
        store-relative (under `<subdir>/.working/`); `product` are product-root-relative. Returns root."""
        counter[0] += 1
        root = base / "reloc-{:02d}".format(counter[0])
        machine_dir = root / subdir / ".working" / "toml"
        machine_dir.mkdir(parents=True)
        (machine_dir / "manifest.toml").write_text(manifest_text(manifest_extra), encoding="utf-8")
        (machine_dir / "counters.toml").write_text(
            "schema = 1\n\n[counters]\nBI = 0\nLF = 0\nWL = 0\n", encoding="utf-8")
        (root / _opf_store.POINTER_REL).write_text(
            '[store]\ntarget = "dir:{}"\n'.format(subdir), encoding="utf-8")
        for rel, text in (strays or {}).items():
            p = root / subdir / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")
        for rel, text in (product or {}).items():
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")
        return root

    def snapshot(root):
        """A byte snapshot of a store tree (relpath -> bytes), for the detect-writes-nothing invariant."""
        out = {}
        for dirpath, _dirs, names in os.walk(str(root)):
            for name in names:
                fp = Path(dirpath) / name
                out[str(fp.relative_to(root))] = fp.read_bytes()
        return out

    try:
        # 1. clean store: no strays under .working/ beyond the machine subdir -> CLEAN, zero rows.
        root, _m = build_store()
        clean = detect(root)
        check("clean-store-detects-nothing",
              clean.verdict == CLEAN and clean.rows == [] and clean.worksheet.get("worksheet_digest"))

        # 2. store-scope enumeration: each planted non-managed file under .working/ appears exactly once.
        root, _m = build_store(strays={".working/stray.md": "one", ".working/sub/deep.txt": "two"})
        r = detect(root)
        store_paths = [row["source_path"] for row in r.rows if row["scope"] == "store"]
        check("store-scope-enumerates-each-once",
              r.verdict == CLEAN and store_paths == [".working/stray.md", ".working/sub/deep.txt"]
              and len(store_paths) == len(set(store_paths)))
        check("store-scope-rows-default-unresolved-baseline",
              all(row["disposition"] == "unresolved" and row["origin"] == "baseline"
                  and row["scope"] == "store" for row in r.rows))

        # 3. per-row sha256 is well-formed and matches the file bytes.
        import hashlib as _hl
        want = "sha256:" + _hl.sha256(b"one").hexdigest()
        row0 = next(row for row in r.rows if row["source_path"] == ".working/stray.md")
        check("per-row-sha256-wellformed-and-correct",
              _DIGEST_RE.match(row0["sha256"]) and row0["sha256"] == want and row0["size"] == 3)

        # 4. managed-path exclusion: manifest, counters (machine subtree), an imports run file, and an
        #    already-declared [unmanaged] path do NOT appear as rows; over-detection is caught.
        root, _m = build_store(
            strays={".working/imports/imp-x/frames.log": "run", ".working/declared-note.md": "kept",
                    ".working/real-stray.md": "found"},
            manifest_extra='[unmanaged]\npaths = [".working/declared-note.md"]')
        r = detect(root)
        paths = {row["source_path"] for row in r.rows}
        check("managed-path-exclusion",
              r.verdict == CLEAN and ".working/real-stray.md" in paths
              and ".working/toml/manifest.toml" not in paths
              and ".working/toml/counters.toml" not in paths
              and ".working/imports/imp-x/frames.log" not in paths
              and ".working/declared-note.md" not in paths)

        # 5. determinism: reversed --include order (and a re-run) yields an EQUAL worksheet digest.
        root, _m = build_store(strays={".working/a.md": "aa", ".working/b.md": "bbb"},
                               product={"docs/x.md": "x", "docs/y.md": "yy"})
        d1 = detect(root, include=["docs/x.md", "docs/y.md"])
        d2 = detect(root, include=["docs/y.md", "docs/x.md"])
        check("detect-determinism-equal-digest",
              d1.verdict == CLEAN and d2.verdict == CLEAN
              and d1.worksheet_digest == d2.worksheet_digest)

        # 6. declared scope: an --include glob matches product files as `declared` rows; store scope is
        #    still auto-detected alongside.
        decl = detect(root, include=["docs/*.md"])
        declared = sorted(row["source_path"] for row in decl.rows if row["scope"] == "declared")
        check("declared-scope-glob-match",
              decl.verdict == CLEAN and declared == ["docs/x.md", "docs/y.md"]
              and any(row["scope"] == "store" for row in decl.rows))

        # 7. glob-no-match is a FINDING (declared-input must resolve, OQ-A).
        check("glob-no-match-is-finding",
              detect(root, include=["nope/*.md"]).verdict == FINDING)

        # 8. an --include that escapes the product root, or names the store scope, is a FINDING.
        check("include-escape-is-finding", detect(root, include=["../outside.md"]).verdict == FINDING)
        check("include-store-scope-is-finding",
              detect(root, include=[".working/a.md"]).verdict == FINDING)

        # 8a. declared-scope managed-exclusion (F1): an --include naming a MANAGED product-root path (a
        #     [views]/[deliverables] target, or a declared [unmanaged] path at the product root) yields NO
        #     declared row for it: it is excluded wherever it falls, exactly as the store scope excludes it.
        root, _m = build_store(
            product={"report.md": "R", "out.pdf": "D", "keep.md": "K", "loose.md": "L"},
            manifest_extra=('[views.main]\nkind = "composed"\nsources = ["docs"]\ntarget = "report.md"\n\n'
                            '[deliverables.d1]\nkind = "curated"\ntarget = "out.pdf"\n\n'
                            '[unmanaged]\npaths = ["keep.md"]'))
        decl = detect(root, include=["report.md", "out.pdf", "keep.md", "loose.md"])
        decl_paths = {row["source_path"] for row in decl.rows if row["scope"] == "declared"}
        check("declared-scope-managed-excluded",
              decl.verdict == CLEAN and "loose.md" in decl_paths
              and "report.md" not in decl_paths and "out.pdf" not in decl_paths
              and "keep.md" not in decl_paths)

        # 8b. non-canonical managed spelling (F2): a VALID but non-canonical [unmanaged].paths spelling
        #     (`./x`, `x//y`, `x/./y`) still excludes the store file it names; a literal-string comparison
        #     would let the aliased path evade exclusion and be detected as a stray.
        for _spell in ("./.working/kept.md", ".working//kept.md", ".working/./kept.md"):
            root, _m = build_store(strays={".working/kept.md": "k", ".working/real.md": "r"},
                                   manifest_extra='[unmanaged]\npaths = ["{}"]'.format(_spell))
            r = detect(root)
            r_paths = {row["source_path"] for row in r.rows}
            check("non-canonical-managed-excluded",
                  r.verdict == CLEAN and ".working/real.md" in r_paths
                  and ".working/kept.md" not in r_paths)

        # 8b-subtree. declared-unmanaged DIRECTORY covers its subtree (round-3 F1): an [unmanaged].paths
        #     entry that names a DIRECTORY covers its whole subtree by containment (matching
        #     _opf_check.managed_file / _under_any), so a file UNDER it is neither enumerated as a stray nor
        #     READ (its content is never digested; spec 14.2), in BOTH store scope (under `.working/`) and
        #     declared scope (a product-root --include hit). An exact-match exclusion would emit (and read)
        #     each child, so a child appearing as a row is the fail-without-the-fix discriminator.
        root, _m = build_store(
            strays={".working/legacy-dir/kept.md": "k", ".working/legacy-dir/sub/deep.md": "d",
                    ".working/real.md": "r"},
            product={"docs/legacy/old.md": "o", "docs/keep.md": "K"},
            manifest_extra='[unmanaged]\npaths = [".working/legacy-dir", "docs/legacy"]')
        r = detect(root, include=["docs/legacy/old.md", "docs/keep.md"])
        sub_paths = {row["source_path"] for row in r.rows}
        check("unmanaged-directory-covers-subtree",
              r.verdict == CLEAN and ".working/real.md" in sub_paths and "docs/keep.md" in sub_paths
              and ".working/legacy-dir/kept.md" not in sub_paths
              and ".working/legacy-dir/sub/deep.md" not in sub_paths
              and "docs/legacy/old.md" not in sub_paths)

        # 8b-existence (F2: separate EXISTENCE from exclusion). Round 5 conflated "covered" with "exists": an
        #     --include under a covered path bypassed the no-match FINDING regardless of whether the path
        #     existed. An --include naming an ABSENT declared-unmanaged path must STILL be a no-match FINDING
        #     (a declared input must resolve), while one under a PRESENT declared-unmanaged directory stays
        #     CLEAN (a present-but-excluded subtree, never read). Both cases share a covered [unmanaged] path;
        #     only filesystem existence distinguishes them, so this is the fail-without-the-fix discriminator.
        root, _m = build_store(product={"live.md": "L"},
                               manifest_extra='[unmanaged]\npaths = ["qa-absent-legacy"]')
        absent = detect(root, include=["qa-absent-legacy/*.md"])
        check("declared-absent-covered-include-is-finding", absent.verdict == FINDING and absent.rows == [])
        root, _m = build_store(product={"present-legacy/old.md": "o", "live.md": "L"},
                               manifest_extra='[unmanaged]\npaths = ["present-legacy"]')
        present = detect(root, include=["present-legacy/*.md"])
        present_decl = {row["source_path"] for row in present.rows if row["scope"] == "declared"}
        check("declared-present-covered-include-clean",
              present.verdict == CLEAN and "present-legacy/old.md" not in present_decl)

        # 8b-control (F-MAJOR premise shift). The store-root control / VCS subtrees (`.aiqt/import`,
        #     `.aiqt/import/journal`, `.aiqt/import-archive`, and `.git`) are OPF-managed / VCS content, NOT
        #     ingestible: a broad `--include=["*"]` product-root walk must neither emit them as declared rows
        #     NOR read them, exactly as `_opf_import._assemble_preview` drops `.git`/`.aiqt` at the store
        #     root. The exclusion is the whole `.aiqt` subtree + `.git`, so every apply / migration ops /
        #     journal / archive tree that nests under `.aiqt` is covered BY CONSTRUCTION. Without the
        #     store-root control exclusion these leak as declared rows and are content-read (the pre-fix
        #     defect); a live product file still detected alongside is the flip discriminator.
        root, _m = build_store(product={
            ".aiqt/import/journal/txn/frames.log": "j", ".aiqt/import/imp-x/transaction.toml": "t",
            ".aiqt/import-archive/imp-x/acceptance.json": "a", ".git/config": "g", "readme.md": "R"})
        r = detect(root, include=["*"])
        ctl_paths = {row["source_path"] for row in r.rows}
        check("store-root-control-trees-excluded",
              r.verdict == CLEAN and "readme.md" in ctl_paths
              and not any(p == d or p.startswith(d + "/")
                          for d in _opf_store.STORE_ROOT_CONTROL_DIRS for p in ctl_paths))
        # a RELOCATED store carries its control trees at `ops/.aiqt` / `ops/.git`; the exclusion is anchored
        # at the RESOLVED store root, so they are excluded wherever the store resolves (a product-relative-
        # `.aiqt`-only rule would leak `ops/.aiqt/...` as declared rows).
        reloc = build_relocated(product={
            "ops/.aiqt/import/journal/f.log": "j", "ops/.git/config": "g", "readme.md": "R"})
        rr = detect(reloc, include=["*"])
        rr_paths = {row["source_path"] for row in rr.rows if row["scope"] == "declared"}
        check("relocated-store-root-control-excluded",
              rr.verdict == CLEAN and "readme.md" in rr_paths
              and not any(".aiqt" in p or p.startswith("ops/.git") for p in rr_paths))
        # control-dirs-single-authority-set-equality (single-authority drift-catcher, G): ingest holds NO
        #     control-dir literal of its own; it derives the store-root control exclusion from the ONE
        #     authority `_opf_store.STORE_ROOT_CONTROL_DIRS`, the SAME tuple `_opf_import._assemble_preview`
        #     drops at the store root. The prefixes ingest ACTUALLY excludes for an inline store (product root
        #     == store root, so the prefixes are the bare dir names) must EQUAL that authority as a SET, so a
        #     reintroduced or divergent literal fails closed (a presence-only check would miss a superset drift).
        _inline_root, _im = build_store()
        _inline_res = _opf_store.resolve_store(_inline_root)
        check("control-dirs-single-authority-set-equality",
              set(_store_root_control_prefixes(_inline_res)) == set(_opf_store.STORE_ROOT_CONTROL_DIRS))
        # covered-derivation-matches-import-authority (drift-catcher): `.aiqt` is the control UMBRELLA every
        #     import-layer ops / journal / archive constant must nest under (and the imports run tree stays
        #     under `.working/`), so the `.aiqt` SUBTREE exclusion covers them by construction. If the import
        #     layer relocated an ops tree out from under `.aiqt`, the umbrella exclusion would silently stop
        #     covering it; this catches that drift at source. `.aiqt` is asserted to remain a member of the
        #     single authority, so dropping it from `STORE_ROOT_CONTROL_DIRS` also fails closed here.
        _umbrella = ".aiqt"
        check("covered-derivation-matches-import-authority",
              _umbrella in _opf_store.STORE_ROOT_CONTROL_DIRS
              and all(rel == _umbrella or rel.startswith(_umbrella + "/")
                      for rel in (_opf_import.IMPORT_OPS_REL, _opf_import.IMPORT_JOURNAL_REL,
                                  _opf_import.IMPORT_ARCHIVE_REL))
              and _opf_import.IMPORTS_REL.startswith(_opf_store.WORKING_DIRNAME + "/"))

        # 8b-prune (F1: traverse-before-exclude). A covered (excluded) directory is PRUNED before descent,
        #     never entered, so an unreadable / exotic entry inside a subtree we would exclude anyway cannot
        #     block detection with a spurious CANNOT-EVALUATE (spec 14.2 never enters an unmanaged subtree). A
        #     SYMLINK planted inside a declared-unmanaged directory makes the pre-fix walk (which entered the
        #     subtree, excluding only at the result stage) fail closed on the symlink; pruning before descent
        #     detects the live sibling CLEAN and never enters the excluded subtree. Symlink support is
        #     platform-gated exactly as the symlink-in-scope vector below.
        root, _m = build_store(strays={".working/real.md": "r"},
                               product={"legacy/keep.md": "k", "live.md": "L"},
                               manifest_extra='[unmanaged]\npaths = ["legacy"]')
        try:
            os.symlink(str(_m / "manifest.toml"), str(root / "legacy" / "link.md"))
            prune_sym_ok = True
        except OSError:
            prune_sym_ok = False   # platform without symlink support: skip this vector, not a failure
        if prune_sym_ok:
            pr = detect(root, include=["legacy/*", "live.md"])
            pr_paths = {row["source_path"] for row in pr.rows}
            check("excluded-subtree-pruned-before-descent",
                  pr.verdict == CLEAN and "live.md" in pr_paths
                  and not any(p.startswith("legacy/") for p in pr_paths))

        # 8c. RELOCATED-store declared-scope exclusion (round-2 F1): a `.opf.toml` -> `dir:ops` relocation
        #     puts the store at `ops/.working/`. The declared-scope exclusion is derived from the RESOLVED
        #     store location, so an --include naming the relocated store scope is a FINDING and the store's
        #     machine / imports / stray files never leak as declared rows. A product-relative-`.working`-only
        #     exclusion would emit `ops/.working/...` as declared rows.
        reloc = build_relocated(
            strays={".working/toml/extra.toml": "m", ".working/imports/imp-x/frames.log": "run",
                    ".working/stray.md": "s"},
            product={"readme.md": "R"})
        f1 = detect(reloc, include=["ops/.working/*"])
        check("relocated-store-scope-include-is-finding", f1.verdict == FINDING and f1.rows == [])
        # a legitimate product-root include on the SAME relocated store still emits its declared row, and a
        # broad `*` never pulls the pruned store subtree in.
        f1ok = detect(reloc, include=["readme.md", "*"])
        f1decl = sorted(row["source_path"] for row in f1ok.rows if row["scope"] == "declared")
        check("relocated-product-include-ok",
              f1ok.verdict == CLEAN and f1decl == ["readme.md"]
              and not any(".working" in p for p in f1decl))

        # 8c-nested (F1: bind each exclusion to its correct resolved root). A store relocated UNDER the
        #     product's OWN `.working/` (`dir:.working/ops`) resolves (store_root at `.working/ops`), so its
        #     PRODUCT-relative store-root control prefix is `.working/ops/.aiqt`. That prefix must NOT enter
        #     the STORE-scope covered set: store scope grades STORE-relative paths, and a store stray whose
        #     store-relative path is `.working/ops/.aiqt/foo.md` would then COLLIDE with the product-relative
        #     prefix and be silently suppressed. The fix returns the control prefixes SEPARATELY (declared
        #     scope only), so the colliding store stray is detected; a plain store stray alongside is the
        #     live-sibling flip. Without the fix the colliding stray is dropped (fail-without-the-fix).
        reloc = build_relocated(subdir=".working/ops",
                                strays={".working/ops/.aiqt/foo.md": "collide", ".working/plain.md": "p"})
        nested = detect(reloc)
        nested_store = {row["source_path"] for row in nested.rows if row["scope"] == "store"}
        check("nested-store-control-prefix-no-false-suppression",
              nested.verdict == CLEAN and ".working/plain.md" in nested_store
              and ".working/ops/.aiqt/foo.md" in nested_store)

        # 8d. store pointer control files excluded from declared scope (round-2 C1): `.opf.toml` /
        #     `.opf.local.toml` are OPF control files at the product root, managed wherever they fall; an
        #     --include naming one yields NO declared row (excluded exactly as a manifest-declared managed
        #     path). Without them in the managed `covered` set the walkable product-root pointer is emitted.
        reloc = build_relocated(product={"readme.md": "R"})
        (reloc / _opf_store.LOCAL_POINTER_REL).write_text('[store]\ntarget = "dir:ops"\n', encoding="utf-8")
        c1 = detect(reloc, include=[".opf.toml", ".opf.local.toml", "readme.md"])
        c1decl = {row["source_path"] for row in c1.rows if row["scope"] == "declared"}
        check("pointer-control-files-excluded",
              c1.verdict == CLEAN and c1decl == {"readme.md"}
              and ".opf.toml" not in c1decl and ".opf.local.toml" not in c1decl)

        # 9. init-first: an unresolved store (no manifest) is CANNOT-EVALUATE, "run `opf init` first".
        unadopted = base / "unadopted"
        unadopted.mkdir()
        (unadopted / "loose.md").write_text("x", encoding="utf-8")
        ce = detect(unadopted)
        check("init-first-cannot-evaluate",
              ce.verdict == CANNOT_EVALUATE and any("opf init" in f for f in ce.findings))

        # 10. a symlink in scope is refused fail-closed (never followed, never a silent skip).
        root, _m = build_store(strays={".working/real.md": "r"})
        try:
            os.symlink(str(_m / "manifest.toml"), str(root / ".working" / "link.md"))
            sym_ok = True
        except OSError:
            sym_ok = False   # platform without symlink support: skip this vector, not a failure
        if sym_ok:
            check("symlink-in-scope-fail-closed", detect(root).verdict == CANNOT_EVALUATE)

        # 10a. a non-UTF-8 (invalid-byte) filename in scope is refused fail-closed (F4): it cannot be
        #      represented in the byte-canonical worksheet, so detect returns CANNOT-EVALUATE naming it,
        #      never a clean row and never an uncaught UnicodeEncodeError.
        root, _m = build_store(strays={".working/ok.md": "o"})
        working_fd = os.open(str(root / ".working"), os.O_RDONLY | os.O_DIRECTORY)
        try:
            bad_fd = os.open(b"\xff.md", os.O_WRONLY | os.O_CREAT, 0o644, dir_fd=working_fd)
            os.write(bad_fd, b"x")
            os.close(bad_fd)
            nonutf8_ok = True
        except OSError:
            nonutf8_ok = False   # a filesystem that rejects the byte name: skip this vector, not a failure
        finally:
            os.close(working_fd)
        if nonutf8_ok:
            check("non-utf8-name-fail-closed", detect(root).verdict == CANNOT_EVALUATE)

        # 11. detect-writes-nothing: the store tree is byte-identical after detect (preview, no side effect).
        root, _m = build_store(strays={".working/s.md": "s"}, product={"docs/p.md": "p"})
        before = snapshot(root)
        detect(root, include=["docs/*.md"])
        check("detect-writes-nothing", snapshot(root) == before)

        # 12. worksheet validation: a clean worksheet validates; a vocab mutation and a digest mutation are
        #     each caught (the gate's schema/vocab and digest-recompute checks). Each vocab/keyset
        #     discriminator carries an HONESTLY recomputed digest over its own mutated payload (round-3 F2:
        #     isolate the target check), so the finding it raises is the vocab/keyset violation itself, not
        #     a masking digest-recompute mismatch; removing the vocab/keyset check would then turn the
        #     discriminator GREEN. The digest-drift discriminator alone keeps a deliberately wrong digest.
        root, _m = build_store(strays={".working/v.md": "vv"})
        good = detect(root).worksheet
        check("validate-clean-worksheet", validate_worksheet(good) == [])
        vocab_payload = {"format": good["format"], "schema": good["schema"],
                         "row": [dict(good["row"][0], disposition="bogus")]}
        vocab_honest = "sha256:" + _opf_import._sha256_hex(_worksheet_bytes(vocab_payload))
        check("validate-vocab-violation",
              validate_worksheet(dict(vocab_payload, worksheet_digest=vocab_honest)) != [])
        bad_digest = dict(good)
        bad_digest["worksheet_digest"] = "sha256:" + "0" * 64
        check("validate-digest-drift", validate_worksheet(bad_digest) != [])
        key_payload = {"format": good["format"], "schema": good["schema"],
                       "row": [dict(good["row"][0], surprise=1)]}
        key_honest = "sha256:" + _opf_import._sha256_hex(_worksheet_bytes(key_payload))
        check("validate-closed-keyset",
              validate_worksheet(dict(key_payload, worksheet_digest=key_honest)) != [])
        # 12a. schema type-strictness (F5): schema True (== 1) or 1.0 (== 1), each with an HONESTLY
        #      recomputed digest, is still rejected; a bare `!= SCHEMA` comparison would accept it because
        #      True == 1 == 1.0, so the digest recompute is the only thing that would catch a mutation and a
        #      matching-digest payload with a bool/float schema would otherwise validate.
        for _bad_schema in (True, 1.0):
            _payload = {"format": good["format"], "schema": _bad_schema, "row": good["row"]}
            _honest = "sha256:" + _opf_import._sha256_hex(_worksheet_bytes(_payload))
            _bad_sch = dict(_payload, worksheet_digest=_honest)
            check("validate-schema-type-strict", validate_worksheet(_bad_sch) != [])
        # 12a-reader (F2): validate_worksheet must reject a source_path the CONTAINED READER
        #      (`_journal._check_rel`, the predicate `_read_contained` applies) rejects, so a valid digest
        #      cannot make an unreadable path ingestible. A control character (TAB) slips past
        #      `_is_contained_relpath` but the reader rejects it; the payload carries an HONESTLY recomputed
        #      digest (the emitter escapes the control char, so it round-trips byte-canonically), isolating
        #      the reader check: dropping it makes `_is_contained_relpath` alone accept the path and the
        #      discriminator turns GREEN.
        ctl_payload = {"format": good["format"], "schema": good["schema"],
                       "row": [dict(good["row"][0], source_path=".working/x\ty.md")]}
        ctl_honest = "sha256:" + _opf_import._sha256_hex(_worksheet_bytes(ctl_payload))
        check("validate-rejects-reader-unreadable-source-path",
              validate_worksheet(dict(ctl_payload, worksheet_digest=ctl_honest)) != [])
        # 12a-reader-roundtrip (D): a source_path carrying a NUL or any control character is rejected even
        #      after a full SERIALIZE + RE-PARSE round trip, so a valid recomputed digest cannot LAUNDER a
        #      control-char path through TOML: the emitter escapes the char (it round-trips byte-canonically),
        #      the digest recomputes honestly, and validate_worksheet still rejects it through the SAME
        #      contained-reader check (`_journal._check_rel`) the reader applies. Each is isolated by the
        #      honest digest, so the finding is the reader-rejection, not a masking digest mismatch.
        import tomllib as _tomllib
        for _ctl in ("\x00", "\t", "\x01"):
            _rt_payload = {"format": good["format"], "schema": good["schema"],
                           "row": [dict(good["row"][0], source_path=".working/x{}y.md".format(_ctl))]}
            _rt_honest = "sha256:" + _opf_import._sha256_hex(_worksheet_bytes(_rt_payload))
            _rt = _tomllib.loads(_worksheet_bytes(dict(_rt_payload, worksheet_digest=_rt_honest)).decode("utf-8"))
            check("validate-rejects-control-char-after-roundtrip", validate_worksheet(_rt) != [])
        # 12a-key (F3): a NON-STRING top-level key is a refusing finding (rendered as an unknown key), never
        #      a TypeError from sorting / joining the unknown-key set; the pre-fix `", ".join(sorted(extra))`
        #      raises on a non-string key, so this vector CRASHED without the fix and is a clean finding with it.
        nonstr_key = dict(good)
        nonstr_key[1] = "x"
        check("validate-nonstring-key-is-finding", validate_worksheet(nonstr_key) != [])
        # 12a-key-mixed (E): a MIXED int/str extra-key set is handled without raising: an int key alongside a
        #      str key sorts through str() rather than a TypeError in `sorted()` over a mixed-type set, and the
        #      result is a refusing finding, per the validator's documented contract (fail-closed, never crash).
        mixed_key = dict(good)
        mixed_key[1] = "a"
        mixed_key["zzz-unknown"] = "b"
        check("validate-mixed-key-set-is-finding", validate_worksheet(mixed_key) != [])

        # 12b. detection-consistent-with-its-validator (round-2 F2): a product-root file whose name the
        #      worksheet validator would reject as non-contained (a `:` in the second character, read as a
        #      drive letter) is a LOCATED CANNOT-EVALUATE at detection, never a CLEAN worksheet that then
        #      FAILS validate_worksheet. A bare enumerate-then-emit returns CLEAN with an invalid row.
        root, _m = build_store(product={"a:b.md": "x"})
        f2 = detect(root, include=["*"])
        check("bad-name-located-refusal-not-clean",
              f2.verdict == CANNOT_EVALUATE and any("a:b.md" in msg for msg in f2.findings))
        # the invariant the guard secures: a CLEAN detection's worksheet ALWAYS validates.
        root, _m = build_store(strays={".working/ok.md": "o"}, product={"docs/y.md": "y"})
        cleaned = detect(root, include=["docs/*.md"])
        check("clean-worksheet-always-validates",
              cleaned.verdict == CLEAN and validate_worksheet(cleaned.worksheet) == [])

        # 13. dispatch-deferral: `opf adopt` is NOT wired; the fail-closed KNOWN_VERBS dispatch stands
        #     (consciously flipped at MIG-PR6, which wires the verb).
        import opf as _opf_cli
        check("dispatch-deferral-adopt-unwired",
              "adopt" not in _opf_cli.KNOWN_VERBS and "detect" not in _opf_cli.KNOWN_VERBS)
    except OSError as exc:
        print("OPF-INGEST SELF-TEST ERROR: harness error: {}".format(exc), file=sys.stderr)
        shutil.rmtree(str(base), ignore_errors=True)
        return 2
    finally:
        shutil.rmtree(str(base), ignore_errors=True)

    if failures:
        for f in failures:
            print("OPF-INGEST SELF-TEST FAIL: {}".format(f), file=sys.stderr)
        return 1
    print("OPF-INGEST SELF-TEST PASS: detect enumerates store scope exactly once, excludes the managed set "
          "by subtree containment (including a managed path an --include names, a non-canonical managed "
          "spelling, a declared-unmanaged DIRECTORY's whole subtree unread, the store pointer control "
          "files, and the store-root `.aiqt` / `.git` control / VCS trees derived from the import layer's "
          "own drop-set authority so every apply / migration ops / journal / archive tree is covered by "
          "construction, drift-checked against the import constants), PRUNES a covered subtree before "
          "descent so an unreadable / exotic entry inside an excluded tree never blocks detection, derives "
          "the declared-scope exclusion from the RESOLVED store location so a relocated (`dir:`) store's "
          "own subtree never leaks as declared and its product-relative store-root control prefix never "
          "suppresses a store stray (even a store nested under the product's own `.working/`), honours "
          "declared-scope --include (glob-no-match/escape/store-scope each a finding, an absent covered "
          "declared path still a no-match finding while a present covered one is clean), is deterministic "
          "+ writes nothing, fails closed on an unresolved "
          "store, a symlink, a non-UTF-8 name, and a non-contained detected path (so a CLEAN worksheet "
          "always validates), the worksheet validates and catches vocab/digest/keyset/schema-type "
          "mutations, a source_path the contained reader rejects, and a non-string top-level key, and "
          "`opf adopt` stays unwired.")
    return 0


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if args == ["--self-test"]:
        return self_test()
    if args:
        print("_opf_ingest: unexpected argument(s): {}".format(" ".join(args)), file=sys.stderr)
        return 2
    print("_opf_ingest: the root-ingest detection engine module; run with --self-test to exercise it "
          "(no verb is wired in this slice, MIG-PR1).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
