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
    present, string-typed, empty field (the closed keyset is fixed so the worksheet shape is stable)."""
    return {"source_path": source_path, "scope": scope, "disposition": _DETECT_DISPOSITION,
            "origin": _DETECT_ORIGIN, "sha256": sha256, "size": size, "note": ""}


# --- contained no-follow enumeration (mirrors _opf_check._list_contained / _opf_store._immediate_subdirs) -

def _walk_regular_files(dfd, prefix, prune, out, budget):
    """Recursively collect the regular-file paths beneath the OPEN directory fd `dfd`, no-follow. `prefix`
    is the POSIX relpath of `dfd` from the walk root ("" for the root). A subdirectory whose relpath is in
    `prune` is skipped entirely (the machine store subtree and the reserved `.working/imports/` tree, or
    `.working/` itself for the declared-scope product walk). Fails CLOSED (a refusing CANNOT-EVALUATE
    naming the entry) on a symlink, an exotic entry (neither a directory nor a regular file), or an I/O
    error, so a swapped or exotic entry is never silently skipped (check-fails-closed-on-unreadable,
    SECI-symlink-resolution). Bounded by `budget` (a one-element mutable counter) so a runaway or hostile
    deep tree is refused rather than enumerated unboundedly (SECA resource-bounds)."""
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
                continue
            try:
                sub = os.open(entry, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=dfd)
            except OSError as exc:
                raise _cannot("cannot open directory {!r} no-follow ({})".format(rel, exc))
            try:
                _walk_regular_files(sub, rel, prune, out, budget)
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

def _canonical_managed(p):
    """Canonicalize a manifest-declared managed path to the byte-canonical POSIX-relative spelling the
    no-follow walk produces, so a non-canonical BUT VALID declaration (`./x`, `x//y`, `x/./y`) still
    matches the file it names and cannot evade the managed-exclusion set (the literal-string comparison
    would otherwise miss an aliased spelling). Called only on a path the manifest validator already
    accepted as contained (`_is_contained_relpath`), so `posixpath.normpath` cannot escape the root or
    yield an empty result."""
    return posixpath.normpath(p)


def _managed_paths(resolution, manifest_data):
    """The CLOSED "OPF-managed" set, derived FROM the resolved store authorities rather than a parallel
    hand-copied list (guard-input-soundness). Returns (prune_prefixes, exact):
      - prune_prefixes: store-relative directory subtrees never detected under `.working/`: the machine
        store subtree (`resolution.machine_rel`, which contains the manifest, the typed indexes, and the
        control ledgers) and the reserved `.working/imports/` run tree (`_opf_import.IMPORTS_REL`).
      - exact: store-relative individual paths that are managed wherever they fall: the manifest's declared
        view targets and deliverable targets (`[views].*.target` / `[deliverables].*.target`) and its
        already-declared `[unmanaged].paths`. An already-declared unmanaged path is NOT re-detected. Each is
        canonicalized (`_canonical_managed`) so a non-canonical but valid spelling cannot evade exclusion."""
    prune = {resolution.machine_rel, _opf_import.IMPORTS_REL}
    exact = set()
    for section in ("views", "deliverables"):
        tables = manifest_data.get(section)
        if isinstance(tables, dict):
            for tbl in tables.values():
                if isinstance(tbl, dict) and isinstance(tbl.get("target"), str):
                    exact.add(_canonical_managed(tbl["target"]))
    unmanaged = manifest_data.get("unmanaged")
    if isinstance(unmanaged, dict):
        paths = unmanaged.get("paths")
        if isinstance(paths, list):
            for p in paths:
                if isinstance(p, str):
                    exact.add(_canonical_managed(p))
    return prune, exact


# --- detection ---------------------------------------------------------------------------------------

def _detect_store_scope(store_fd, prune, exact):
    """Enumerate every non-OPF-managed regular file under `.working/` (the mandatory store scope). Opens
    the `.working/` directory no-follow beneath the resolved store-root fd, walks it pruning the managed
    subtrees, and drops any file whose store-relative path is an already-declared managed exact path. Each
    survivor is a `store`-scope row defaulting to `unresolved` / `baseline`."""
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
        _walk_regular_files(working_fd, _opf_store.WORKING_DIRNAME, prune, files, [0])
    finally:
        os.close(working_fd)
    rows = []
    for rel in files:
        if rel in exact:
            continue
        digest, size = _digest_of(store_fd, rel)
        rows.append(_row(rel, "store", digest, size))
    return rows


def _detect_declared_scope(product_root, include, exact):
    """Enumerate the product-root files an operator `--include` pattern names (the opt-in declared scope,
    OQ-3). Each pattern is validated FIRST (contained, root-relative, and never naming the auto-detected
    `.working/` store scope), then the product root is walked no-follow ONCE (pruning `.working/`, which is
    store scope), and each pattern is matched with `fnmatch.fnmatchcase`. A pattern that matches no product
    file is a FINDING (declared-input must resolve, OQ-A, matching `scan_import`'s refusal of an absent
    declared source). Each matched file is a `declared`-scope row (union across patterns, digested once),
    EXCEPT a managed path in `exact` (a `[views]`/`[deliverables]` target or a declared `[unmanaged]` path,
    which commonly falls at the product root): it is excluded wherever it falls, exactly as the store scope
    excludes it, so an `--include` cannot pull an already-managed path into declared scope (OQ-3 contract)."""
    for pat in include:
        if not (isinstance(pat, str) and _opf_store._is_contained_relpath(pat)):
            raise _finding("--include pattern {!r} is not a contained root-relative pattern (no absolute "
                           "path, no '..' escape)".format(pat))
        if pat.split("/", 1)[0] == _opf_store.WORKING_DIRNAME:
            raise _finding("--include pattern {!r} names the {} store scope, which is detected "
                           "automatically; declared scope is the product root only".format(
                               pat, _opf_store.WORKING_DIRNAME))
    fd = _opf_store._open_root_fd(Path(os.path.abspath(product_root)))
    try:
        files = []
        _walk_regular_files(fd, "", {_opf_store.WORKING_DIRNAME}, files, [0])
        matched = set()
        for pat in include:
            hits = [f for f in files if fnmatch.fnmatchcase(f, pat)]
            if not hits:
                raise _finding("--include pattern {!r} matched no product-root file (a declared input must "
                               "resolve; fail-closed)".format(pat))
            matched.update(hits)
        rows = []
        for rel in sorted(matched):
            if rel in exact:
                continue      # a managed path is excluded wherever it falls, even when --include names it
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
        prune, exact = _managed_paths(resolution, manifest_data)
        rows = _detect_store_scope(store_fd, prune, exact)
    finally:
        os.close(store_fd)
    if include:
        rows += _detect_declared_scope(product_root, include, exact)
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
        findings.append("worksheet carries unknown key(s): {}".format(", ".join(sorted(extra))))
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
        #     each caught (the gate's schema/vocab and digest-recompute checks).
        root, _m = build_store(strays={".working/v.md": "vv"})
        good = detect(root).worksheet
        check("validate-clean-worksheet", validate_worksheet(good) == [])
        bad_vocab = {"format": good["format"], "schema": good["schema"],
                     "row": [dict(good["row"][0], disposition="bogus")],
                     "worksheet_digest": good["worksheet_digest"]}
        check("validate-vocab-violation", validate_worksheet(bad_vocab) != [])
        bad_digest = dict(good)
        bad_digest["worksheet_digest"] = "sha256:" + "0" * 64
        check("validate-digest-drift", validate_worksheet(bad_digest) != [])
        bad_key = {"format": good["format"], "schema": good["schema"],
                   "row": [dict(good["row"][0], surprise=1)],
                   "worksheet_digest": good["worksheet_digest"]}
        check("validate-closed-keyset", validate_worksheet(bad_key) != [])
        # 12a. schema type-strictness (F5): schema True (== 1) or 1.0 (== 1), each with an HONESTLY
        #      recomputed digest, is still rejected; a bare `!= SCHEMA` comparison would accept it because
        #      True == 1 == 1.0, so the digest recompute is the only thing that would catch a mutation and a
        #      matching-digest payload with a bool/float schema would otherwise validate.
        for _bad_schema in (True, 1.0):
            _payload = {"format": good["format"], "schema": _bad_schema, "row": good["row"]}
            _honest = "sha256:" + _opf_import._sha256_hex(_worksheet_bytes(_payload))
            _bad_sch = dict(_payload, worksheet_digest=_honest)
            check("validate-schema-type-strict", validate_worksheet(_bad_sch) != [])

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
          "(including a managed path an --include names and a non-canonical managed spelling), honours "
          "declared-scope --include (glob-no-match/escape/store-scope each a finding), is deterministic + "
          "writes nothing, fails closed on an unresolved store, a symlink, and a non-UTF-8 name, the "
          "worksheet validates and catches vocab/digest/keyset/schema-type mutations, and `opf adopt` stays "
          "unwired.")
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
