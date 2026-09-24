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
  - the no-follow walk fails CLOSED on a NON-covered symlink, exotic entry (FIFO / device / socket), or
    unreadable entry in a traversed subtree: a refusing CANNOT-EVALUATE naming the entry, never a silent skip
    (check-fails-closed-on-unreadable). A COVERED (prune-set) symlink or exotic entry is instead PRUNED
    unread, exactly as a covered directory is, honouring "tooling never reads an unmanaged path" (spec 14.2);
    only a NON-covered such entry fails closed. The opt-in `--include` product-root walk therefore refuses a
    product subtree that carries a NON-covered symlink; this is disclosed and deliberate for the read-only
    preview.
  - `--include` patterns are matched with `fnmatch.fnmatchcase` over POSIX-relative paths, so `*` also
    matches `/`; the pattern semantics are disclosed rather than a full recursive-glob grammar.
  - semantic disposition correctness (which files SHOULD be kept, migrated, or moved) is a human decision,
    out of scope: detect defaults EVERY row to `unresolved` and takes no action on any file (spec 14.2).
  - the machine-store interior (`.working/<machine>/`) and the reserved imports staging tree
    (`_opf_import.IMPORTS_REL`) are OPF's CONTROL AREA, policed by the steady-state checker's C-CONTAINMENT,
    NOT by this adoption-SOURCE stray detector: they hold the manifest, the typed indexes, the control
    ledgers, and the reserved import runs (never adoption source), so they are pruned from detection
    wholesale by construction. This is a ratified intentional prune (F10-2 / F10-3), disclosed here, not a
    coverage gap; the convergence gate (`check_opf_ingest.py`) asserts it rather than re-policing it.
  - RATIFIED DIVERGENCES (R12, disclosed; hardening deferred post-1.1.1): ingest and the checker may differ
    on two TRAVERSAL / ENTRY edges, by design: (a) when a DEEP file is declared `[unmanaged]` (e.g.
    `.working/legacy/keep.md`), the checker grades the intermediate directory `.working/legacy` as
    unregistered while ingest reports only stray FILES (no stray is laundered into CLEAN); (b) a COVERED
    exotic entry (a symlink / FIFO under an `[unmanaged]` cover) is pruned unread by ingest (CLEAN) but
    fail-closed CANNOT-EVALUATE'd by the checker. These are an inherent division of labor: ingest =
    adoption-SOURCE stray-file detection; the checker = full steady-state integrity incl. directory-grading
    + covered-exotic fail-close. The full traversal / entry-type unification is a POST-1.1.1 hardening TODO
    (see PD-MIG-PR1-R12).

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
import _opf_importers   # noqa: E402  MIG-PR3: the closed importer-kind vocabulary + run/validate importer
#                                    (acyclic: _opf_importers imports _journal/_opf_store/_opf_import/
#                                    _opf_schema/_opf_views only, never _opf_ingest)
import _opf_views      # noqa: E402  the SAME view name-resolution + spec-destination authority the checker
#                                    (`_opf_check._check_containment`) grades managed view targets against,
#                                    so ingest's covered set cannot disagree with C-CONTAINMENT (F-8.1)
import _opf_check      # noqa: E402  the steady-state store checker: its pure `classify_containment` is the
#                                    SINGLE authority ingest derives its [unmanaged] cover + store-scope view
#                                    targets from, so ingest and C-CONTAINMENT cannot diverge on the managed
#                                    SET (the collision-filtered valid_unmanaged + the view/deliverable
#                                    targets) by construction (F10-1). Acyclic: _opf_check's top-level imports
#                                    (_journal, _opf_emit, _opf_import, _opf_views, _opf_changelog, _opf_store,
#                                    _opf_schema, _opf_release) none import _opf_ingest; only check_opf_ingest
#                                    imports _opf_ingest, lazily inside its self-test.


# --- fixed formats, closed vocabularies, and the outcome model ---------------------------------------

# The 0/1/2 verdict contract, single-sourced from the import layer (OQ-C): one contract, no local mirror.
CLEAN = _opf_import.CLEAN                    # 0: a clean detection
FINDING = _opf_import.FINDING                # 1: a validation finding (a bad --include, a store-scope glob, a
#                                                 colliding [unmanaged] declaration that covers nothing, F10-1)
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

def _walk_regular_files(dfd, prefix, prune, out, budget, pruned_dirs=None, pruned_nondirs=None,
                        leaf_dests=None):
    """Recursively collect the regular-file paths beneath the OPEN directory fd `dfd`, no-follow. `prefix`
    is the POSIX relpath of `dfd` from the walk root ("" for the root). A subdirectory whose relpath is in
    `prune` is skipped entirely and NEVER ENTERED, which (because the walk reaches every directory at its own
    level) skips its whole subtree: the machine store subtree and the reserved `.working/imports/` tree, or
    `.working/` itself for the declared-scope product walk, PLUS every covered managed directory the caller
    adds (a declared-unmanaged directory, a view / deliverable target, or the store-root `.aiqt` / `.git`
    control trees). Pruning a covered directory BEFORE descent is load-bearing: an excluded subtree is
    neither listed nor read, so an unreadable, symlinked, or exotic entry inside a subtree we would exclude
    anyway cannot block detection with a spurious CANNOT-EVALUATE (spec 14.2 never reads / enters an
    unmanaged path). A pruned entry is skipped BEFORE it is refused as exotic, so a covered entry that is
    ITSELF a symlink or other exotic entry (a declared-unmanaged symlink) is pruned, not refused, exactly
    as a covered directory is (F2); the exotic refusal below stands for a NON-covered exotic entry. Fails
    CLOSED (a refusing CANNOT-EVALUATE
    naming the entry) on a NON-pruned symlink, exotic entry (neither a directory nor a regular file), or I/O
    error, so a swapped or exotic entry is never silently skipped (check-fails-closed-on-unreadable,
    SECI-symlink-resolution). Bounded by `budget` (a one-element mutable counter) so a runaway or hostile
    deep tree is refused rather than enumerated unboundedly (SECA resource-bounds). When `pruned_dirs` is
    a list, every pruned subdirectory's relpath is appended to it (the covered directories that ACTUALLY
    EXIST, encountered and skipped before descent), so a caller can tell an existing-but-excluded subtree
    from an absent one without entering it (F2: existence is separate from exclusion). When `pruned_nondirs`
    is a list, every pruned COVERED NON-directory entry (at least a symlink or other exotic entry) is
    likewise appended, so a caller sees a PRESENT covered symlink-to-dir the same way it sees a present
    covered directory: a pattern beneath it is then a CANNOT-EVALUATE, matching the real-directory case,
    rather than a false no-match FINDING across the no-read boundary (F-8.2, the R7-1 class through the
    symlink shape).

    `leaf_dests` is the set of EXACT-LEAF managed destinations (recognized view spec destinations and public
    deliverable / pointer targets), which the checker matches by EXACT-leaf equality (`_opf_check.managed_leaf`:
    `if p in view_targets`), NOT by subtree containment. An exact-leaf destination is therefore NOT in `prune`:
    a REGULAR FILE there is the managed output (dropped at the result stage by equality, as today), but a
    DIRECTORY at that path is a WRONG-TYPE anomaly (a regular-file output is expected) and fails CLOSED here as
    a refusing CANNOT-EVALUATE naming it, NEVER a silent subtree prune that hides its children. This AGREES
    with the checker, which reads the same directory destination as a non-regular-file cannot-evaluate rather
    than a covered subtree (R9-1). A symlink / exotic entry at an exact-leaf path is not pruned either, so it
    hits the fail-closed exotic refusal below like any other non-covered exotic entry."""
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
            if leaf_dests is not None and rel in leaf_dests:
                # A DIRECTORY at an EXACT-LEAF managed destination (a view spec destination or a public
                # deliverable / pointer target) is a WRONG-TYPE anomaly: a regular-file output is expected
                # there. Fail CLOSED naming it, never a silent subtree prune that would hide its children (a
                # covered exact-leaf entry does NOT cover a subtree, matching the checker's `managed_leaf`
                # exact-equality; R9-1). The checker reads the same destination as a non-regular-file
                # cannot-evaluate, so ingest and the checker AGREE on such a store.
                raise _cannot("{!r} is a directory at an OPF-managed view / deliverable destination, which "
                              "expects a regular-file output (fail-closed, wrong type; never a silent subtree "
                              "prune that hides its children)".format(rel))
            if rel in prune:
                if pruned_dirs is not None:
                    pruned_dirs.append(rel)
                continue
            try:
                sub = os.open(entry, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=dfd)
            except OSError as exc:
                raise _cannot("cannot open directory {!r} no-follow ({})".format(rel, exc))
            try:
                _walk_regular_files(sub, rel, prune, out, budget, pruned_dirs, pruned_nondirs, leaf_dests)
            finally:
                os.close(sub)
        elif stat.S_ISREG(est.st_mode):
            out.append(rel)
        elif rel in prune:
            # A COVERED (declared-unmanaged / pruned) entry that is ITSELF a symlink or other exotic entry
            # is PRUNED here, exactly as a covered DIRECTORY is pruned before descent above: never read and
            # never refused, honouring "tooling never reads an unmanaged path" (spec 14.2). Without this a
            # covered symlink reached the exotic refusal below and blocked the whole detect with a spurious
            # CANNOT-EVALUATE before it could be pruned (F2). It is recorded in `pruned_nondirs` (not
            # `pruned_dirs`, since it is not a subtree) so a PRESENT covered symlink-to-dir is visible to the
            # no-match derivation: a pattern beneath it is a CANNOT-EVALUATE, matching the present-covered-
            # directory case, not a false no-match FINDING across the no-read boundary (F-8.2). The refusal
            # below stays for a NON-covered exotic entry.
            if pruned_nondirs is not None:
                pruned_nondirs.append(rel)
            continue
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


# The generated PUBLIC deliverables live at the PRODUCT repository root in EVERY topology (spec 5.8;
# OPF-SPEC.md 408-417 / 867-868), so their manifest `target`s are genuinely PRODUCT-relative and are NOT
# re-anchored at the store root, unlike every other view/deliverable target and every `[unmanaged].path`,
# which are STORE-relative (graded relative to the store root; spec 14.2 / OPF-SPEC.md:16). This is the
# closed public set; a target whose value is one of these is bound to the product root.
_PUBLIC_TARGETS = frozenset({"VERSION", "CHANGELOG.md"})


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


def _store_base_under_product(resolution):
    """The store root's PRODUCT-root-relative POSIX base (IDENTITY '.' for an inline/default store whose
    store root IS the product root; 'ops' for a `dir:ops` relocation; '.working/ops' for a store nested under
    the product's own `.working/`), or None when the store resolves OUTSIDE the product root. This is the
    SINGLE authority for the re-anchoring base, shared by `_reanchor_declared`, `_declared_to_store_rel`, and
    (through the shared derivation builders below) the MIG-PR4b review gate, so plan-time derivation and the
    gate cannot drift on how the base is computed."""
    try:
        rel = Path(os.path.abspath(resolution.store_root)).relative_to(
            Path(os.path.abspath(resolution.product_root)))
    except ValueError:
        return None
    return rel.as_posix()


def _reanchor_rel(base, rel):
    """Re-anchor ONE store-relative POSIX path at `base` (the `_store_base_under_product` base; '.' for an
    inline store), returning the byte-canonical POSIX spelling the no-follow walk produces (via
    `posixpath.normpath`). The SINGLE re-anchor formula, so every store-relative entry is re-anchored
    identically wherever it is derived (guard-input-soundness: no parallel predicate that could drift)."""
    return posixpath.normpath(posixpath.join(base, rel))


def _store_rel_of(base, rel):
    """The inverse of `_reanchor_rel`: the STORE-relative spelling of a product-relative `rel` under `base`,
    or None when `rel` does not fall under `base`. Shared by `_declared_to_store_rel` (plan time) and the
    review gate's keep re-derivation so the inverse cannot drift from the forward re-anchor either."""
    b = posixpath.normpath(base)
    p = posixpath.normpath(rel)
    if b == ".":
        return p               # inline / default store: the store root IS the product root (identity)
    if p.startswith(b + "/"):
        return p[len(b) + 1:]
    return None


def _reanchor_declared(resolution, store_rel_paths):
    """Map STORE-relative POSIX paths to their PRODUCT-root-relative spelling for the DECLARED (product-root)
    scope when the store falls UNDER the product root (IDENTITY for an inline/default store whose store root
    IS the product root; `ops/x` for a `dir:ops` relocation; `.working/ops/x` for a store nested under the
    product's own `.working/`), else () (the store resolves OUTSIDE the product root, so the declared
    product-root walk never reaches store content and no store-relative entry can collide with a product
    path). This is the SINGLE re-anchoring path the declared scope uses, so EVERY store-relative managed
    entry -- the `.working/` view/deliverable targets, the `[unmanaged].paths`, and the store-root control /
    VCS dirs -- is bound to the resolved store root BY CONSTRUCTION and the store-relative / product-relative
    namespaces can never cross-contaminate (F1). Anchored EXACTLY as `_store_working_under_product` anchors
    `.working`; a returned path is the byte-canonical POSIX spelling the no-follow walk produces (via
    `posixpath.normpath`), so it compares against the walk's paths without an aliased-spelling miss."""
    base = _store_base_under_product(resolution)
    if base is None:
        return ()
    return tuple(_reanchor_rel(base, p) for p in store_rel_paths)


def _store_root_control_prefixes(resolution):
    """The store-root control / VCS subtrees (`.aiqt`, `.git`) as PRODUCT-root-relative POSIX prefixes when
    the store falls UNDER the product root (`.aiqt` / `.git` inline, `ops/.aiqt` / `ops/.git` for a `dir:ops`
    relocation), else () (the store resolves OUTSIDE the product root, so the declared product-root walk
    never reaches it). Re-anchored at the RESOLVED store root through the shared `_reanchor_declared` path
    (the SAME path `_managed_paths` re-anchors the manifest covered entries through), so ONLY the store-root
    `.aiqt` / `.git` (never a same-named dir nested deeper, e.g. `.working/.aiqt`) is covered, mirroring
    `_opf_import._assemble_preview`'s store-root-only drop. `_managed_paths` folds these into the DECLARED
    (product-root) scope ONLY (never store scope, whose STORE-relative paths a `.working`-nested store's
    product-relative prefix could otherwise collide with; see `_managed_paths`), so the declared scope never
    reads an apply-promotion ops / journal / archive tree or the VCS dir (spec 14.2 never reads a managed
    path); because the `.aiqt` subtree is a prefix, every tree nesting under it is covered by construction
    (see `_opf_store.STORE_ROOT_CONTROL_DIRS`). Derived from that ONE authority, so no parallel literal can
    drift (the self-test asserts set-equality for an inline store)."""
    return _reanchor_declared(resolution, _opf_store.STORE_ROOT_CONTROL_DIRS)


def _managed_paths(resolution, manifest_data):
    """The CLOSED "OPF-managed" set, derived FROM the resolved store authorities rather than a parallel
    hand-copied list (guard-input-soundness). Returns (prune_prefixes, store_leaf, store_subtree,
    declared_leaf, declared_subtree), splitting the managed set into the TWO MATCHING KINDS the checker uses
    (`_opf_check`), one pair per scope, so ingest mirrors the checker's MATCHING semantics and not only its
    derivation (R9-1). The adoption-content classification (the store-scope view targets and the
    collision-filtered [unmanaged] cover) is DERIVED from the checker's SINGLE pure authority
    `_opf_check.classify_containment`, so ingest and C-CONTAINMENT cannot diverge on the managed SET (the
    collision-filtered valid_unmanaged + the view/deliverable targets) by construction (F10-1): the same
    `_resolve_view` + `_spec_destination` view derivation and the same
    contained-only, collision-filtered, canonicalized `valid_unmanaged`.
      - prune_prefixes: store-relative directory subtrees never detected under `.working/`: the machine
        store subtree (`resolution.machine_rel`, which contains the manifest, the typed indexes, and the
        control ledgers) and the reserved `.working/imports/` run tree (`_opf_import.IMPORTS_REL`). This is
        OPF's control area, the checker's C-CONTAINMENT ground, not this adoption-source detector's, so it is
        pruned wholesale by design (F10-2 / F10-3, ratified).
      - store_leaf / declared_leaf: EXACT-LEAF managed destinations, matched by EXACT path EQUALITY, exactly
        as `_opf_check.managed_leaf` matches a VIEW target (`if p in view_targets`), NEVER by subtree
        containment. A REGULAR FILE at that exact path is the managed output (excluded from results, as an
        equality member); a DIRECTORY there is a WRONG-TYPE anomaly the walk fails closed on (a regular-file
        output is expected), NEVER a subtree prune that hides its children, agreeing with the checker (R9-1).
        The store-scope leaf entries are the checker's `classify_containment(...).view_targets` VERBATIM (the
        recognized store-scope view spec destinations, in the RAW store-relative spelling the checker uses;
        no `_canonical_managed` re-wrap, so they are byte-identical to the checker's set). The declared-scope
        leaf set RE-ANCHORS those store-scope destinations at the resolved store root AND folds in the
        genuinely product-root leaves UN-anchored: the store pointer control files (`.opf.toml` /
        `.opf.local.toml`, managed wherever they fall; C1) and the PUBLIC view / deliverable targets
        (`_PUBLIC_TARGETS`, product-root in every topology, spec 5.8). A deliverable target is a leaf ONLY
        when PUBLIC (deliverables have no name-resolution authority and the checker treats a store-scope
        deliverable target as a stray; F-8.1). The PUBLIC view destination (VERSION, product scope) is NOT
        part of the checker's store-scope `view_targets`, so it is still derived here from the same
        `_opf_views` authority (D3: product / deliverable handling unchanged).
      - store_subtree / declared_subtree: SUBTREE-covered entries, matched by SUBTREE containment
        (`_under_any`) exactly as `_opf_check.managed_file` grades a file against its `valid_unmanaged`
        authority: a path that EQUALS a covered entry OR lies UNDER one is managed, so a declared-unmanaged
        DIRECTORY covers its whole subtree and tooling never reads an unmanaged path (spec 14.2). The
        store-scope subtree entries are the checker's collision-filtered `valid_unmanaged` VERBATIM (the
        surviving [unmanaged].paths, canonicalized and contained-only, in the RAW store-relative spelling): a
        COLLIDING declaration (one that names or contains a managed store path) is ABSENT from that set and is
        surfaced as a located FINDING below, so it covers NOTHING and can no longer launder a stray beneath
        it into a false CLEAN (F10-1; the 5-round-recurring divergence, closed by construction). The
        declared-scope subtree set RE-ANCHORS those [unmanaged].paths AND the store-root `.aiqt` / `.git`
        control / VCS dirs through the SINGLE `_reanchor_declared` path, so the store-relative /
        product-relative namespace-collision class is closed BY CONSTRUCTION for any current or future
        store-relative entry (F1): on a relocated / pointer store (store root != product root) a
        store-relative `notes` becomes `ops/notes`, so it neither suppresses a real product stray at `notes/`
        nor leaves a store path `ops/notes/` emitted and READ (the round-7 MAJOR). The `.aiqt` subtree is a
        prefix, so every tree nesting under it (IMPORT_OPS_REL, IMPORT_ARCHIVE_REL, migrate's
        `.aiqt/migration/journal`) is covered BY CONSTRUCTION, not by a parallel hand-list that keeps missing
        an authority (guard-input-soundness). Only the DECLARED scope folds in the control dirs, so for a
        store nested under the product's own `.working/` (`dir:.working/ops`) a product-relative prefix such
        as `.working/ops/.aiqt` cannot collide with a store-relative store-scope path and silently suppress a
        real store stray (F1). For an inline / default store re-anchoring is IDENTITY, so inline behaviour is
        unchanged. The R7-1 three-valued no-match consults ONLY the SUBTREE set, since an exact-leaf entry is
        a single file matched in results, not an unread covered subtree."""
    prune = {resolution.machine_rel, _opf_import.IMPORTS_REL}
    # DERIVE the adoption-content managed classification from the checker's SINGLE pure authority rather than
    # re-deriving it here, so ingest and C-CONTAINMENT cannot diverge on the [unmanaged] cover or the view
    # targets (F10-1). The helper is pure (no I/O, no `rep`) and derives enabled_types / layout internally
    # from the manifest the caller already validated (D2).
    cls = _opf_check.classify_containment(manifest_data, resolution.machine_rel)
    # A malformed [unmanaged] entry is a located CANNOT-EVALUATE (a by-construction backstop: the manifest
    # validator `_opf_store._validate_unmanaged` normally rejects an escaping / non-string entry upstream, so
    # `detect` fails closed at init-first validation before reaching here; this mirrors the checker's cant).
    if cls.malformed:
        raise _cannot("a declared [unmanaged] path entry cannot be evaluated, mirroring _opf_check "
                      "C-CONTAINMENT (spec 14.2): {}".format("; ".join(cls.malformed)))
    # FINDING (F10-1 / D1): a COLLIDING [unmanaged] declaration (one that names or contains a managed store
    # path) COVERS NOTHING and is a located FINDING, exactly as the checker's C-CONTAINMENT rejects it. Ingest
    # must not fold a rejected declaration into the cover, where (pre-fix) it laundered a stray beneath it into
    # a false CLEAN; a colliding store flips CLEAN -> FINDING.
    if cls.colliding:
        raise _finding("a declared [unmanaged] path collides with a managed store path, so it covers "
                       "nothing and is a containment finding, mirroring _opf_check C-CONTAINMENT (spec "
                       "14.2): {}".format("; ".join(cls.colliding)))
    # STORE-scope view leaves and the surviving [unmanaged] subtree covers come STRAIGHT from the checker's
    # validated classification, in the RAW store-relative spelling the checker uses (no `_canonical_managed`
    # re-wrap on the view dests, so ingest's leaves are byte-identical to the checker's `view_targets`).
    store_leaf_rel = set(cls.view_targets)      # STORE-relative EXACT-LEAF view spec destinations (checker authority)
    store_subtree_rel = set(cls.valid_unmanaged)  # STORE-relative SUBTREE covers (collision-filtered [unmanaged].paths)
    product_leaf = set()       # genuinely PRODUCT-root EXACT-LEAF entries (public view/deliverable targets)
    # The PRODUCT-root public view destination (VERSION, product scope, spec 5.8) has no store-scope leaf and
    # is NOT part of the checker's store-scope view_targets, so ingest still derives it here from the same
    # `_opf_views` authority (D3: product / deliverable handling unchanged). A store-scope view destination is
    # already carried by cls.view_targets above, so this loop contributes ONLY the public product-root leaf,
    # keeping the exact original `elif dest in _PUBLIC_TARGETS` guard semantics.
    views = manifest_data.get("views")
    if isinstance(views, dict):
        for name in views:
            if not isinstance(name, str):
                continue
            try:
                _opf_views._resolve_view(name)   # a RECOGNIZED view name (pure name check, no I/O)
            except _opf_views.ViewsError:
                continue                          # C3: an unrecognized name marks NO managed target
            scope, dest = _opf_views._spec_destination(name)
            if scope == "store" and _opf_store._is_contained_relpath(dest):
                continue                          # store-scope leaf now derived from cls.view_targets (F10-1)
            if dest in _PUBLIC_TARGETS:           # the product-scope view destination (VERSION), spec 5.8
                product_leaf.add(dest)
    # DELIVERABLES have NO name-resolution authority (the manifest validator grades only their SHAPE, and the
    # checker's C-CONTAINMENT does NOT treat a store-scope deliverable target as managed), so ingest mirrors
    # the checker: ONLY a PUBLIC deliverable target (VERSION / CHANGELOG.md, product-root in every topology,
    # spec 5.8) is covered (as an EXACT LEAF); any OTHER (rebound / store-relative) deliverable target is
    # covered by NOTHING and stays a detectable stray, exactly as the checker grades it (F-8.1).
    deliverables = manifest_data.get("deliverables")
    if isinstance(deliverables, dict):
        for tbl in deliverables.values():
            if isinstance(tbl, dict) and isinstance(tbl.get("target"), str):
                t = _canonical_managed(tbl["target"])
                if t in _PUBLIC_TARGETS:
                    product_leaf.add(t)
    # STORE scope grades STORE-relative paths, so it uses the RAW store-relative sets. (A product-root public
    # target never falls under `.working/`, so folding it in would be a harmless no-op; it is left out to keep
    # store scope strictly the store-relative sets.)
    store_leaf = set(store_leaf_rel)
    store_subtree = set(store_subtree_rel)
    # DECLARED (product-root) scope. EXACT-LEAF: the genuinely product-root public targets and pointer control
    # files (UN-anchored) plus the store-scope view destinations re-anchored at the resolved store root.
    # SUBTREE: the `[unmanaged].paths` and the store-root control / VCS dirs, re-anchored through the ONE
    # `_reanchor_declared` path. On a store OUTSIDE the product root, `_reanchor_declared` yields () (the
    # declared walk never reaches store content), leaving only the un-anchored product-root leaves.
    declared_leaf = set(product_leaf)
    declared_leaf.add(_opf_store.POINTER_REL)
    declared_leaf.add(_opf_store.LOCAL_POINTER_REL)
    declared_leaf.update(_reanchor_declared(resolution, store_leaf_rel))
    declared_subtree = set(_reanchor_declared(
        resolution, store_subtree_rel | set(_opf_store.STORE_ROOT_CONTROL_DIRS)))
    return prune, store_leaf, store_subtree, declared_leaf, declared_subtree


# --- detection ---------------------------------------------------------------------------------------

def _detect_store_scope(store_fd, prune, leaf, subtree):
    """Enumerate every non-OPF-managed regular file under `.working/` (the mandatory store scope). Opens
    the `.working/` directory no-follow beneath the resolved store-root fd, walks it pruning the managed
    SUBTREE covers, and drops any file a covered entry manages, matched the SAME two ways the checker does:
    an EXACT-LEAF managed destination (`leaf`, a recognized view spec destination) is dropped by EXACT path
    equality (a regular-file output; a DIRECTORY there fails closed in the walk as a wrong-type anomaly, R9-1),
    while a `subtree` cover (a declared-unmanaged path) is dropped by SUBTREE containment (`_under_any`): a
    path that equals it OR lies under a declared-unmanaged directory is skipped BEFORE it is read, so an
    unmanaged subtree's contents are never digested (spec 14.2). Each survivor is a `store`-scope row
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
        # Prune the machine / imports subtrees AND every covered SUBTREE cover BEFORE descent, so a
        # declared-unmanaged directory under `.working/` is never entered or read (F1: traverse-before-
        # exclude). An EXACT-LEAF destination is NOT pruned (it does not cover a subtree): the walk drops its
        # regular-file output at the result stage by equality, and fails closed on a DIRECTORY there (R9-1).
        # The result-stage drop below still removes a covered FILE (a directory prune skips only directories);
        # together an unmanaged path is neither enumerated nor read (spec 14.2).
        _walk_regular_files(working_fd, _opf_store.WORKING_DIRNAME, prune | subtree, files, [0],
                            leaf_dests=leaf)
    finally:
        os.close(working_fd)
    rows = []
    for rel in files:
        if rel in leaf or _under_any(rel, subtree):
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


def _skip_char_class(pat, i):
    """Given `pat[i] == '['`, return (end_index, is_class): `end_index` is the index just past a completed
    `fnmatch` character class (the char after its closing `]`), mirroring `fnmatch`'s own class scan (a `]`
    immediately after `[` or `[!` is a literal member, not the close). An unterminated `[` is a LITERAL `[`,
    returned as (i + 1, False). Used by `_glob_prefix_possible` to step over a class as a single-char match."""
    n = len(pat)
    k = i + 1
    if k < n and pat[k] == "!":
        k += 1
    if k < n and pat[k] == "]":
        k += 1
    while k < n and pat[k] != "]":
        k += 1
    if k >= n:
        return i + 1, False   # unterminated '[' is a literal '['
    return k + 1, True        # index past the closing ']'


def _glob_prefix_possible(pat, prefix):
    """True when SOME string beginning with the literal `prefix` can match the `fnmatch.fnmatchcase` pattern
    `pat` (`*` matches any run INCLUDING `/`, `?` any single char, `[...]` one char), used to decide whether
    a glob's match set can descend into a covered subtree (prefix `<covered>/`). This is a SOUND
    OVER-APPROXIMATION toward a match (fail-closed toward CANNOT-EVALUATE, R7-1): a `[...]` class is treated
    as matching ANY single char, and once the whole prefix is consumed at ANY pattern position the remaining
    pattern is assumed satisfiable, so it NEVER reports False when the pattern could really match within the
    subtree (a spurious True only over-widens toward CANNOT-EVALUATE, never a false no-match). Each recursion
    step strictly advances the pattern or prefix index, so it terminates; the memo bounds the `*` fan-out."""
    n, m = len(pat), len(prefix)
    memo = {}

    def can(i, j):
        if j == m:
            return True             # whole prefix consumed; the remaining pattern matches some suffix
        if i == n:
            return False            # pattern exhausted with prefix left: no match has this prefix
        key = (i, j)
        if key in memo:
            return memo[key]
        ch = pat[i]
        if ch == "*":
            res = can(i + 1, j) or can(i, j + 1)   # match zero, or consume prefix[j] and stay on '*'
        elif ch == "?":
            res = can(i + 1, j + 1)                 # one char
        elif ch == "[":
            k, is_class = _skip_char_class(pat, i)
            res = can(k, j + 1) if is_class else (prefix[j] == "[" and can(i + 1, j + 1))
        else:
            res = ch == prefix[j] and can(i + 1, j + 1)
        memo[key] = res
        return res

    return can(0, 0)


def _pattern_intersects_covered(pat, existing_covered):
    """The PRESENT covered subtree an `--include` pattern with no read-space hit COULD still match within (so
    the match can be neither confirmed nor denied without reading an unmanaged path), or None when the
    pattern lies ENTIRELY within the read (non-excluded) space. This replaces the round-7 literal-ancestor
    proxy (`_under_any(pat, existing_covered)`), which unsoundly took a pattern's literal ANCESTOR prefix as
    proof the input EXISTS: ancestor existence never proves a descendant, and a wildcard can match a covered
    path its literal prefix does not name (R7-1). A covered subtree qualifies when the pattern names it or a
    path under it (`_under_any`), when it fnmatches the covered dir itself, or when the glob's match set can
    descend into it (`_glob_prefix_possible` against `<covered>/`), over-approximating toward a hit
    (fail-closed toward CANNOT-EVALUATE). Only a PRESENT covered dir (`existing_covered`) qualifies: an ABSENT
    covered path holds no unread content, so a pattern that can only resolve within it stays a genuine
    no-match FINDING (a declared input must resolve). Returns the first such covered dir (name-sorted) for a
    LOCATED message."""
    for c in sorted(existing_covered):
        if (_under_any(pat, (c,)) or fnmatch.fnmatchcase(c, pat)
                or _glob_prefix_possible(pat, c + "/")):
            return c
    return None


def include_scope_prefixes(store_working_rel):
    """The store-scope prefixes an `--include` pattern may never name: the literal product-root `.working`
    (the pre-relocation contract) plus the RESOLVED store's `.working` subtree when it falls under the
    product root (`store_working_rel`; None when it resolves outside). Shared by detection and the MIG-PR4b
    review gate's frozen-include check."""
    scope_prefixes = {_opf_store.WORKING_DIRNAME}
    if store_working_rel is not None:
        scope_prefixes.add(store_working_rel)
    return scope_prefixes


def validate_include_patterns(include, scope_prefixes):
    """Validate each `--include` pattern: a contained root-relative pattern (no absolute path, no `..`
    escape) that never names the auto-detected store scope (`scope_prefixes`). Raises a FINDING
    _DetectError on the first bad pattern. The SINGLE include-pattern authority, shared by
    `_detect_declared_scope` and the MIG-PR4b review gate over the frozen include set."""
    for pat in include:
        if not (isinstance(pat, str) and _opf_store._is_contained_relpath(pat)):
            raise _finding("--include pattern {!r} is not a contained root-relative pattern (no absolute "
                           "path, no '..' escape)".format(pat))
        named = next((pref for pref in scope_prefixes if pat == pref or pat.startswith(pref + "/")), None)
        if named is not None:
            raise _finding("--include pattern {!r} names the {} store scope, which is detected "
                           "automatically; declared scope is the product root only".format(pat, named))


def include_matches(pattern, path):
    """True when the declared-scope `--include` `pattern` names the product-relative `path` (case-sensitive
    `fnmatch`, the detection matcher). The SINGLE include matcher, shared by `_detect_declared_scope` and the
    MIG-PR4b review gate, which requires every frozen declared-scope row to fall inside the frozen include
    set under exactly this matcher."""
    return fnmatch.fnmatchcase(path, pattern)


def _detect_declared_scope(product_root, include, leaf, subtree, store_working_rel):
    """Enumerate the product-root files an operator `--include` pattern names (the opt-in declared scope,
    OQ-3). Each pattern is validated FIRST (contained, root-relative, and never naming the auto-detected
    store scope), then the product root is walked no-follow ONCE (pruning the store `.working/` subtree,
    which is store scope), and each pattern is matched with `fnmatch.fnmatchcase`. `store_working_rel` is the
    resolved store's `.working` subtree as a product-relative POSIX path (".working" inline, "ops/.working"
    for a `dir:ops` relocation, or None when the store resolves outside the product root), derived from the
    RESOLVED store location so a relocated store's machine / imports / stray files are excluded from declared
    scope wherever they resolve and are never emitted as declared rows; the literal product-root `.working/`
    is always off-limits too (the pre-relocation contract). A pattern with no read-space hit is THREE-VALUED
    (R7-1): a no-match FINDING (declared-input must resolve, OQ-A, matching `scan_import`'s refusal of an
    absent declared source) only when it lies ENTIRELY within the read space, but a CANNOT-EVALUATE when its
    match set could reach into a PRESENT covered SUBTREE detection never reads (neither match nor no-match is
    knowable across that no-read boundary; `_pattern_intersects_covered`). The covered set is matched the SAME
    two ways the checker does: an EXACT-LEAF managed destination (`leaf`, a public view / deliverable target
    or a store pointer control file) drops its regular-file output by EXACT path equality (a DIRECTORY there
    fails closed as a wrong-type anomaly, R9-1, never a subtree prune), while a `subtree` cover (a declared
    `[unmanaged]` path or a store-root control / VCS dir) is matched by SUBTREE containment (`_under_any`), so
    a file UNDER a declared-unmanaged directory is excluded. Each matched file is a `declared`-scope row
    (union across patterns, digested once) EXCEPT such a covered path, which is excluded before it is read,
    exactly as the store scope excludes it, so an `--include` cannot pull an already-managed path (or a file
    inside an unmanaged subtree) into declared scope (OQ-3 contract; spec 14.2 never reads an unmanaged path).
    Only the SUBTREE covers feed the R7-1 no-match derivation (an exact-leaf entry is a single file matched in
    results, not an unread covered subtree)."""
    scope_prefixes = include_scope_prefixes(store_working_rel)
    validate_include_patterns(include, scope_prefixes)
    fd = _opf_store._open_root_fd(Path(os.path.abspath(product_root)))
    try:
        files = []
        pruned_dirs = []
        pruned_nondirs = []
        # Prune the store `.working/` scope AND every covered SUBTREE cover BEFORE descent (F1:
        # traverse-before-exclude), so a declared-unmanaged directory or the store-root `.aiqt` / `.git`
        # control trees are never entered or read even when an entry inside them is unreadable / exotic. An
        # EXACT-LEAF destination is NOT pruned (it does not cover a subtree): the walk drops its regular-file
        # output at the result stage and fails closed on a DIRECTORY there (R9-1, wrong type).
        # `pruned_dirs` collects every covered directory that ACTUALLY EXISTS (was encountered and skipped),
        # and `pruned_nondirs` every covered NON-directory (a symlink-to-dir or other exotic entry) pruned in
        # its place, so the no-match check can tell an existing-but-excluded path (directory OR symlink) from
        # an absent one WITHOUT entering it (F2 / F-8.2: separate existence from exclusion, for both shapes).
        _walk_regular_files(fd, "", set(scope_prefixes) | subtree, files, [0], pruned_dirs, pruned_nondirs,
                            leaf_dests=leaf)
        # The covered SUBTREE dirs that EXIST on the real filesystem (pruned before descent), whether a real
        # directory or a covered symlink-to-dir / exotic entry standing in for one. Only SUBTREE covers feed
        # this present-set (an exact-leaf entry is a single file matched in results, not an unread subtree; it
        # is never pruned and cannot appear here). A pattern whose match set can descend into one resolves to a
        # PRESENT-but-excluded, unread location; a covered path that does NOT exist leaves no entry here, so a
        # pattern that can only resolve within it stays an (unresolved) no-match finding. A present covered
        # SYMLINK is included so a pattern beneath it is a CANNOT-EVALUATE, at parity with the real-directory
        # case, not a false no-match FINDING across the no-read boundary (F-8.2).
        existing_covered = {d for d in pruned_dirs if d in subtree} | {d for d in pruned_nondirs if d in subtree}
        matched = set()
        for pat in include:
            hits = [f for f in files if include_matches(pat, f)]
            if not hits:
                # THREE-VALUED no-match derivation (R7-1), never the round-7 literal-ancestor existence
                # proxy. A declared pattern with no read-space hit is:
                #  - CANNOT-EVALUATE (verdict 2) when its match set COULD reach into a PRESENT covered
                #    subtree whose contents detection never reads: the match can be neither confirmed nor
                #    denied across that no-read boundary, so detection invents neither existence nor absence
                #    (`_pattern_intersects_covered`, over-approximating toward this fail-closed verdict). An
                #    ancestor prefix that merely exists never proves a descendant, and a wildcard can reach a
                #    covered path its literal prefix does not name; both now resolve here.
                #  - a no-match FINDING (verdict 1) only when the pattern lies ENTIRELY within the read
                #    (non-excluded) space and matched nothing (a declared input must resolve, OQ-A). An
                #    ABSENT covered path holds no unread content, so a pattern that can only resolve within it
                #    is a genuine no-match, not a CANNOT-EVALUATE.
                # A covered FILE that exists is walked into `files`, so a pattern naming one already gets a
                # hit and never reaches this branch.
                covered_reach = _pattern_intersects_covered(pat, existing_covered)
                if covered_reach is not None:
                    raise _cannot("--include pattern {!r} could match within the covered, unread subtree "
                                  "{!r}, so detection can neither confirm nor deny the match without reading "
                                  "an unmanaged path (fail-closed)".format(pat, covered_reach))
                raise _finding("--include pattern {!r} matched no product-root file (a declared input must "
                               "resolve; fail-closed)".format(pat))
            matched.update(hits)
        rows = []
        for rel in sorted(matched):
            if rel in leaf or _under_any(rel, subtree):
                continue      # a managed output at an exact-leaf destination (equality), or a path equal to
                #               / under a SUBTREE cover, is excluded before it is read
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
        prune, store_leaf, store_subtree, declared_leaf, declared_subtree = _managed_paths(
            resolution, manifest_data)
        rows = _detect_store_scope(store_fd, prune, store_leaf, store_subtree)
    finally:
        os.close(store_fd)
    if include:
        # `declared_leaf` / `declared_subtree` are the managed set already re-anchored for the product-root
        # scope, split into the two matching kinds: EXACT-LEAF destinations (the store-scope view dests
        # re-anchored, plus the product-root pointer + public targets un-anchored) matched by equality, and
        # SUBTREE covers (the [unmanaged].paths and control / VCS dirs bound to the resolved store root)
        # matched by containment; see `_managed_paths`.
        rows += _detect_declared_scope(product_root, include, declared_leaf, declared_subtree,
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


# --- ingest-options companion schema (MIG-PR3 decision 1) ---------------------------------------------
# The EXPLICIT fail-closed companion to a triaged worksheet: it binds each config-bearing disposition row,
# by (scope, source_path), to its importer_kind (migrate) or dest_path (move). There is NO implicit
# importer probing and the worksheet `note` is NEVER a config channel (decision 1); a migrate/move row with
# no options binding is CANNOT_EVALUATE.
OPTIONS_FORMAT = "opf-ingest-options-v1"
_OPTIONS_KEYS = frozenset({"format", "schema", "option"})
_OPTION_ROW_KEYS = frozenset({"scope", "source_path", "importer_kind", "dest_path"})
# decision 3 archive convention (DIV-1 reconciliation 3b): a move row whose options omit dest_path defaults
# to `.archive/<source_path>` (substructure preserved, collision-free, provenance-keeping); an explicit
# dest_path overrides. Flip _MOVE_DEST_ARCHIVE_DEFAULT to False to require an explicit dest (ruling 3a).
ARCHIVE_DIRNAME = ".archive"
_MOVE_DEST_ARCHIVE_DEFAULT = True


def validate_options(options):
    """Validate an --ingest-options payload against its closed schema, returns a findings list (empty ==
    valid). Structural/vocabulary violations are findings; the SEMANTIC binding (every migrate/move row has
    exactly one option, importer_kind is a known kind, dest is in-bounds) is checked by the planner against
    the reconciled worksheet, not here (this validates SHAPE only). Fail-closed on an unparseable payload."""
    if not isinstance(options, dict):
        return ["ingest-options is not a table"]
    findings = []
    extra = set(options) - _OPTIONS_KEYS
    if extra:
        findings.append("ingest-options carries unknown key(s): {}".format(
            ", ".join(sorted(str(k) for k in extra))))
    if options.get("format") != OPTIONS_FORMAT:
        findings.append("ingest-options.format is not {!r}".format(OPTIONS_FORMAT))
    sch = options.get("schema")
    if not (type(sch) is int and sch == SCHEMA):
        findings.append("ingest-options.schema is not {!r}".format(SCHEMA))
    rows = options.get("option")
    if not isinstance(rows, list):
        findings.append("ingest-options.option must be a list of option rows")
        return findings
    for i, r in enumerate(rows):
        where = "ingest-options.option[{}]".format(i)
        if not isinstance(r, dict):
            findings.append("{}: an option row must be a table".format(where)); continue
        if not (set(r) <= _OPTION_ROW_KEYS and {"scope", "source_path"} <= set(r)):
            findings.append("{}: option row keys must be a subset of {{scope, source_path, importer_kind, "
                            "dest_path}} with scope+source_path required".format(where)); continue
        if r.get("scope") not in SCOPES:
            findings.append("{}: scope {!r} is not one of {}".format(where, r.get("scope"), list(SCOPES)))
        sp = r.get("source_path")
        if not (isinstance(sp, str) and _opf_store._is_contained_relpath(sp) and _reader_reads(sp)):
            findings.append("{}: source_path must be a contained root-relative path the reader accepts".format(where))
        if "importer_kind" in r and (r["importer_kind"] not in _opf_importers.IMPORTER_KINDS):
            findings.append("{}: importer_kind {!r} is not a known importer ({})".format(
                where, r.get("importer_kind"), list(_opf_importers.IMPORTER_KINDS)))
        if "dest_path" in r and not (isinstance(r["dest_path"], str)
                                     and _opf_store._is_contained_relpath(r["dest_path"])
                                     and _reader_reads(r["dest_path"])):
            findings.append("{}: dest_path must be a contained root-relative path".format(where))
    return findings


# --- shared disposition DERIVATION builders (single authority; MIG-PR4b RE-DERIVATION) -----------------
# Each pure builder below is the ONE place a deterministic frozen ingest artefact's shape AND formula is
# defined. It is called by BOTH plan_ingest (to PRODUCE the artefact) and the MIG-PR4b review gate
# (_verify_ingest_review_model, to RE-DERIVE it and require the frozen artefact to EQUAL the re-derivation).
# A new field or a changed formula therefore lands in exactly one place and the gate re-derives it
# automatically, so the producer and the checker cannot drift -- the parallel-predicate hazard the per-field
# gate suffered from (each finding added one more assertion the checker had to keep exhaustive). The only
# resolution input these need is the re-anchor `base` (`_store_base_under_product`); the gate has no live
# store at review time, so it recovers that base as a CONSISTENCY binding over every piece of frozen evidence
# that observes it (the crosswalk store rows and the declared keep actions; see the gate) rather than from
# `resolve_store`. The pure ADMISSIBILITY checks below (admit_*) are shared the same way.

def resolve_by_scope(base, scope, rel):
    """Resolve a SCOPE-relative worksheet source_path to the product-relative path staging reads: IDENTITY
    for a declared row, `_reanchor_rel(base, rel)` for a store row. Returns None for a store row whose store
    resolves OUTSIDE the product root (base is None); the caller decides whether that is CANNOT-EVALUATE
    (plan time) or a located FINDING (gate). The single scope-resolution authority."""
    if scope != "store":
        return rel
    if base is None:
        return None
    return _reanchor_rel(base, rel)


def keep_unmanaged(base, scope, sp):
    """The [unmanaged].paths spelling a keep action registers: a store-scope source_path VERBATIM (already
    store-relative), else the store-relative spelling of the declared product path (`_store_rel_of`; None when
    it does not fall under the resolved store root). The single keep-exemption spelling authority."""
    if scope == "store":
        return sp
    if base is None:
        # The store resolves OUTSIDE the product root, so a declared product path has no store-relative
        # spelling. Returned as None BEFORE _store_rel_of (which cannot normalize a None base), so the caller
        # refuses it located (plan time: CANNOT-EVALUATE; gate: a FINDING), never an uncaught TypeError.
        return None
    return _store_rel_of(base, sp)


def admit_keep_unmanaged(base, scope, sp):
    """The ADMISSIBLE [unmanaged].paths spelling of a keep row, or a raised CANNOT-EVALUATE _DetectError. A
    declared keep whose product path has no store-relative spelling (the store resolves outside the product
    root, or the path does not fall under the resolved store root) is refused, and the emitted spelling is
    round-tripped through the forward re-anchor (`_reanchor_rel`) so the exemption provably re-anchors back to
    the kept file; the spelling must be a legal contained [unmanaged].paths entry. Pure (no filesystem read):
    shared by `_keep_action` (plan time) and the MIG-PR4b review gate (single authority)."""
    unmanaged = keep_unmanaged(base, scope, sp)
    if scope != "store":
        if unmanaged is None or _reanchor_rel(base, unmanaged) != sp:
            raise _cannot("keep row {!r} (declared scope) does not fall under the resolved store root, "
                          "so no store-relative [unmanaged].paths entry can cover it; emitting the "
                          "product spelling would exempt a DIFFERENT file once re-anchored at the store "
                          "root (fail-closed, disclosed residual; retriage the row or relocate the file "
                          "under the store)".format(sp))
    if not _opf_store._is_contained_relpath(unmanaged):
        raise _cannot("keep source {!r} does not yield a legal [unmanaged].paths entry ({!r})".format(
            sp, unmanaged))
    return unmanaged


def admit_row_binding(r, opt):
    """The PURE per-row admissibility of a worksheet row against its --ingest-options binding `opt` (None
    when unbound), raising a _DetectError. A keep/unresolved row binds to NO option (FINDING); a migrate/move
    row requires a binding (absent: CANNOT-EVALUATE), carries an empty note (the note is never a config
    channel: CANNOT-EVALUATE), and binds exactly its disposition's fields (a forbidden field present, or a
    required field absent on a present binding: FINDING). The SINGLE authority, shared by plan_ingest and the
    MIG-PR4b review gate over the frozen worksheet + options (equality with a constructor does not prove
    admissibility)."""
    dispo = r["disposition"]; sp = r["source_path"]
    if dispo in ("keep", "unresolved") and opt is not None:
        # round-2 P2-6: a keep/unresolved row binds to NO options row (the ratified "keep/unresolved binds
        # to none"); a binding here is misdirected configuration, a FINDING exactly like a dangling binding.
        # (An unresolved row already halted at step 4; the branch keeps the vocabulary closed.)
        raise _finding("{} row {!r} carries an --ingest-options binding; a keep/unresolved row "
                       "binds to none (remove the option row or retriage)".format(dispo, sp))
    if dispo in ("migrate", "move"):
        if opt is None:
            raise _cannot("{} row {!r} has no --ingest-options binding (required for a "
                          "migrate/move row)".format(dispo, sp))
        if r["note"] != "":
            raise _cannot("{} row {!r} carries a non-empty note; configuration belongs in "
                          "--ingest-options, never the worksheet note (decision 1)".format(dispo, sp))
        # round-2 P2-6 / round-3 F1: REQUIRED + PERMITTED fields per disposition, validated BEFORE any field
        # is indexed, so an incompatible binding is a structured verdict (a FORBIDDEN field present is a
        # FINDING, and a REQUIRED field absent on a PRESENT binding is a FINDING too, an invalid options
        # document the operator must fix; a completely ABSENT binding, opt is None above, stays
        # CANNOT-EVALUATE, the missing-config class), never an uncaught KeyError and never a silently ignored
        # field.
        if dispo == "migrate":
            if "dest_path" in opt:
                raise _finding("migrate row {!r} binds a dest_path; a migrate row takes "
                               "importer_kind only (dest_path belongs to a move row)".format(sp))
            if "importer_kind" not in opt:
                raise _finding("migrate row {!r} binds no importer_kind (required for a migrate "
                               "row)".format(sp))
        else:
            if "importer_kind" in opt:
                raise _finding("move row {!r} binds an importer_kind; a move row takes "
                               "dest_path only (importer_kind belongs to a migrate "
                               "row)".format(sp))
            if not _MOVE_DEST_ARCHIVE_DEFAULT and "dest_path" not in opt:
                raise _finding("move row {!r} binds no dest_path and the archive default is "
                               "disabled (ruling 3a requires an explicit dest)".format(sp))


def admit_row_scope(scope, sp, base):
    """The PURE static SCOPE admissibility of one worksheet row's (scope, source_path), raising a FINDING
    _DetectError: exactly the scope boundaries detection enforces that are decidable from the frozen path
    and the re-anchor `base` alone (no live tree, no manifest). A STORE-scope row is store-relative and
    detection emits it only from the mandatory `.working/` subtree (the store walk root), never from the
    reserved imports tree it prunes. A DECLARED-scope row is product-relative and detection never emits one
    from the literal product-root `.working/`, from the RESOLVED store working subtree
    (`include_scope_prefixes`), from the store-root control / VCS dirs re-anchored at `base`
    (`_opf_store.STORE_ROOT_CONTROL_DIRS`), or at a store pointer control file. `base` is the store-under-
    product re-anchor base (None when the store resolves outside the product root, or when the review gate
    cannot observe it: only the base-free boundaries then apply). Shared by plan_ingest (defence in depth
    behind the fresh-detect reconcile) and the MIG-PR4b review gate over the frozen worksheet, so a row
    re-scoped across the store / product boundary is refused statically rather than re-derived faithfully.
    The manifest-dependent covers ([unmanaged].paths, view targets, the machine subtree) are NOT decidable
    here without the live store and remain the fresh-detect reconcile's (disclosed)."""
    working = _opf_store.WORKING_DIRNAME
    if scope == "store":
        if not sp.startswith(working + "/"):
            raise _finding("store-scope row {!r} does not lie in the mandatory store subtree {!r}/ (a store "
                           "row is store-relative and detected only under it)".format(sp, working))
        if _under_any(sp, (_opf_import.IMPORTS_REL,)):
            raise _finding("store-scope row {!r} lies in the reserved imports tree {!r}, which detection "
                           "prunes wholesale".format(sp, _opf_import.IMPORTS_REL))
    elif scope == "declared":
        store_working_rel = (None if base is None
                             else posixpath.normpath(posixpath.join(base, working)))
        excluded = set(include_scope_prefixes(store_working_rel))
        if base is not None:
            excluded.update(_reanchor_rel(base, d) for d in _opf_store.STORE_ROOT_CONTROL_DIRS)
        hit = next((pref for pref in sorted(excluded) if _under_any(sp, (pref,))), None)
        if hit is not None:
            raise _finding("declared-scope row {!r} lies in {!r}, a store subtree declared scope never "
                           "reads (it belongs to the store scope or the store control area)".format(sp, hit))
        if sp in (_opf_store.POINTER_REL, _opf_store.LOCAL_POINTER_REL):
            raise _finding("declared-scope row {!r} is a store pointer control file, which detection never "
                           "emits".format(sp))
    else:
        raise _finding("row {!r} carries an unknown scope {!r}".format(sp, scope))


def admit_move_dest(sp, dest, move_dests, sources):
    """The PURE move-SET admissibility of one move (source `sp`, authorized destination `dest`) against the
    destinations already admitted (`move_dests`: dest -> source, in worksheet order) and the COMPLETE
    resolved product-relative source set of the worksheet (`sources`: every row's scope-resolved path, the
    move's own included), raising a FINDING _DetectError, and recording `dest` on success. Refuses a
    duplicate destination, a move-to-self, a destination EQUAL to any source, a destination BENEATH a source
    (a source is a regular file, so it cannot be a directory), a destination that is an ANCESTOR of a source
    (the source's parent is a directory, so it cannot be the moved file), and two destinations in
    ancestor/descendant relation. Every source is a file the live tree holds at plan time, so the planner's
    live no-overwrite lstat refuses each of these too; deciding them here from the frozen set lets the
    MIG-PR4b review gate (no live tree) refuse them at parity. Shared by plan_ingest and the review gate.
    Destinations colliding with a product path OUTSIDE the worksheet are decidable only against the live
    tree (the planner's lstat; a disclosed review-time residual)."""
    if dest in move_dests:
        raise _finding("duplicate move destination {!r} ({} and {})".format(dest, move_dests[dest], sp))
    if dest == sp:
        raise _finding("move-to-self: {!r} destination equals its source".format(sp))
    # round-4 F2: the destination against the COMPLETE resolved source set (equality, and the `/`-guarded
    # component-wise ancestor/descendant relation in both directions, the nesting idiom below). Sorted so
    # the located finding is deterministic.
    for src in sorted(sources):
        if dest == src:
            raise _finding("move destination {!r} (for {}) equals the worksheet source {!r}; a planned "
                           "source is an existing file (no-overwrite)".format(dest, sp, src))
        if dest.startswith(src + "/"):
            raise _finding("move destination {!r} (for {}) lies beneath the worksheet source {!r}, which is "
                           "a regular file, not a directory".format(dest, sp, src))
        if src.startswith(dest + "/"):
            raise _finding("move destination {!r} (for {}) is an ancestor directory of the worksheet source "
                           "{!r}; it already exists as a directory (no-overwrite)".format(dest, sp, src))
    # round-3 F3: two planned destinations in ANCESTOR/DESCENDANT relation are impossible TOGETHER (the
    # shorter path must be a regular file for one move and a directory for the other), yet each passes the
    # per-dest boundary check alone because neither exists yet, and the exact-duplicate check above cannot
    # see the relation, so a nested pair staged an unappliable move set CLEAN. Both dests are canonical
    # (validate_options and the walk admit no `.`/`..`/empty segment), so the `/`-guarded prefix test is an
    # exact component-wise ancestor test and a name-prefix sibling (saved/ab beside saved/a) is not swept in
    # (the `_under_any` idiom).
    for prior_dest, prior_sp in move_dests.items():
        if dest.startswith(prior_dest + "/") or prior_dest.startswith(dest + "/"):
            raise _finding("move destinations {!r} (for {}) and {!r} (for {}) nest: one "
                           "needs the shorter path as a directory, the other as a regular "
                           "file, an impossible move set".format(prior_dest, prior_sp, dest, sp))
    move_dests[dest] = sp


def derive_crosswalk_row(r, resolved_sp):
    """One scoped-crosswalk row {scope, source_path, resolved_source_path, disposition} (the frozen review
    surface's disposition authority). `r` is a worksheet row; `resolved_sp` its scope-resolved product path."""
    return {"scope": r["scope"], "source_path": r["source_path"],
            "resolved_source_path": resolved_sp, "disposition": r["disposition"]}


def derive_move_dest(opt, sp):
    """The authorized move destination: the explicit options dest_path, else the archive default
    (`_archive_dest`) when the archive default is enabled. The single move-destination formula."""
    return (opt.get("dest_path") or _archive_dest(sp)) if _MOVE_DEST_ARCHIVE_DEFAULT else opt.get("dest_path")


def derive_keep_action(r, unmanaged):
    """One inert keep pending-action {kind=keep, scope, source_path, unmanaged_path, sha256, size}. PR-C must
    add `unmanaged_path` to [unmanaged].paths ATOMICALLY with the retention before the "checker never flags
    it" guarantee holds (R-2; see `_keep_action`). The frozen identity is the worksheet row's own sha256/size."""
    return {"kind": "keep", "scope": r["scope"], "source_path": r["source_path"],
            "unmanaged_path": unmanaged, "sha256": r["sha256"], "size": r["size"]}


def derive_move_action(r, dest):
    """One inert move pending-action {kind=move, scope, source_path, dest_path, sha256, size}. The frozen
    identity is the worksheet row's own sha256/size; `dest` is the authorized `derive_move_dest` value."""
    return {"kind": "move", "scope": r["scope"], "source_path": r["source_path"],
            "dest_path": dest, "sha256": r["sha256"], "size": r["size"]}


def derive_migrate_scaffold(r, resolved_sp, importer_kind):
    """The DETERMINISTIC part of a migrate review row {scope, source_path, resolved_source_path,
    importer_kind}; the importer-derived candidate_count / proposal_count the planner adds are
    importer-validated by the gate, never re-derived (the importer is never re-run). `importer_kind` is the
    OPTIONS binding's kind, so the gate binds the frozen migrate row's kind to the options doc, not itself."""
    return {"scope": r["scope"], "source_path": r["source_path"],
            "resolved_source_path": resolved_sp, "importer_kind": importer_kind}


def sort_candidate_rows(candidates, migrate_rows):
    """The producer's DETERMINISTIC candidates_draft.toml order (round-4 F6): grouped by migrate row in the
    order the planner records the migrate rows (worksheet order), then by draft_ref bytes within one importer
    output (draft_refs are unique per output, so the order is total). The single ordering authority, shared
    by plan_ingest (which stages the drafts in this order) and the MIG-PR4b review gate (which requires the
    frozen candidates_draft.toml to EQUAL it), so a reordered draft list is a FINDING rather than an unbound
    degree of freedom. Each candidate's source_path names a migrate row (the gate validates this first)."""
    rank = {}
    for i, m in enumerate(migrate_rows):
        rank.setdefault(m["source_path"], i)
    return sorted(candidates, key=lambda c: (rank[c["source_path"]], c["draft_ref"].encode("utf-8")))


# --- the disposition PLANNER (MIG-PR3): compose a triaged worksheet + options into a staged inert plan ---

def plan_ingest(product_root, worksheet, options, include=None, *, now, run_nonce):
    """Compose a triaged disposition WORKSHEET + its --ingest-options into a STAGED, INERT plan under
    `.working/imports/<run-id>/` via _opf_import.plan_import. NEVER manufactures acceptance.json; every
    ingest source stays unmapped/legacy_fragment; apply/promotion is refused (ingest-actions.toml, the
    refusal marker, staged as the FIRST ingest artefact and ahead of the review artefacts, so a partial
    staging failure can never leave a reviewable-but-unmarked run; round-2 P1-1). Each source is resolved
    BY SCOPE to the one product-relative path staging reads (a store-scope row under the RESOLVED store
    root, a declared row under the product root; round-2 P1-2), its bytes are BOUND to the reconciled
    worksheet digest immediately before staging AND re-verified from the staged run's own records after
    staging (round-2 P1-2/P1-4), and a move destination is refused inside the RESOLVED store working tree,
    not only the literal `.working/` (round-2 P1-3), and two planned move destinations in
    ancestor/descendant relation are refused as an impossible move set (round-3 F3). Verdicts: an
    invalid-but-well-formed options document, an incompatible per-disposition binding, and a present
    options binding missing a required field are FINDINGS (round-3 F1); an unreadable/unparseable input,
    a completely absent options binding, and a digest-binding failure are CANNOT-EVALUATE (round-2
    P2-5/P2-6, round-3 F1).

    Disclosed residuals (rounds 2-3): (R-2) a keep action's "steady-state checker never flags it" guarantee is
    CONDITIONAL: it holds only once PR-C applies the retention and the [unmanaged].paths registration
    ATOMICALLY, in one journaled transaction (see _keep_action); the plan itself changes nothing.
    (staging-root topology) a store-scope row of a store that resolves OUTSIDE the product root has no
    product-relative spelling for the product-root staging reader, so it is refused CANNOT-EVALUATE rather
    than planned. (keep-exemption topology) a DECLARED-scope keep whose product path does not fall under
    the RESOLVED store root has no contained store-relative [unmanaged].paths spelling, so it is refused
    CANNOT-EVALUATE rather than planned with an exemption that re-anchors to a different file (round-3
    F2; see _keep_action). (post-stage refusal) a digest-binding failure detected AFTER staging leaves
    the refused run dir behind; it is non-promotable (it carries the refusal marker as its first-staged
    artefact).
    (cooperating writers) an actor that hand-writes inventory/proposals/acceptance into a partial run dir
    is outside the cooperating-writer contract this layer (like the import journal) assumes.
    Fail-closed throughout. Returns a _opf_import.PlanResult so the CLI's _import_exit maps it uniformly."""
    try:
        _journal.require_containment()
    except _journal.JournalError as exc:
        return _opf_import.PlanResult(CANNOT_EVALUATE, ["{}; fail-closed".format(exc)])
    try:
        _opf_import._require_utc(now); _opf_import._require_nonce(run_nonce)

        # 1. WORKSHEET INTAKE: it must pass its own MIG-PR1 validator (single authority) verbatim.
        ws_findings = validate_worksheet(worksheet)
        if ws_findings:
            raise _cannot("worksheet fails validation: {}".format("; ".join(ws_findings)))

        # 2. OPTIONS INTAKE (shape). An invalid-but-WELL-FORMED options document (an unknown key, a bad
        #    format/schema marker, an unknown importer_kind, an escaping dest_path) is a FINDING (verdict
        #    1), the ratified mapping (round-2 P2-5); CANNOT-EVALUATE stays reserved for an input the
        #    planner cannot even read as a table (unreadable/unparseable, e.g. a non-table payload; a TOML
        #    parse error is already refused upstream at the CLI read).
        if not isinstance(options, dict):
            raise _cannot("ingest-options is not a table (unreadable/unparseable input; fail-closed)")
        opt_findings = validate_options(options)
        if opt_findings:
            raise _finding("ingest-options fails validation: {}".format("; ".join(opt_findings)))

        # 3. FRESH RECONCILE: re-detect with the SAME include set and require an EXACT-SET match against the
        #    worksheet on the immutable fields (source_path, scope, sha256, size). Any drift is fail-closed:
        #    a ghost/omitted/newly-detected path, or a mutated digest/size, means the worksheet no longer
        #    describes the tree (guard-input-soundness: the plan cannot rest on a stale triage).
        fresh = detect(product_root, include=include)
        if fresh.verdict != CLEAN:
            return _opf_import.PlanResult(fresh.verdict, fresh.findings)
        _reconcile_worksheet_against_detect(worksheet, fresh)   # raises _DetectError on any mismatch

        # 4. UNRESOLVED GATE: a fully-triaged worksheet has NO `unresolved` row (fail-closed choice from the
        #    ratified synthesis / Seed A). One unresolved row halts planning (FINDING).
        rows = worksheet["row"]
        unresolved = [r["source_path"] for r in rows if r["disposition"] == "unresolved"]
        if unresolved:
            raise _finding("worksheet has {} unresolved row(s); complete triage before --plan: {}".format(
                len(unresolved), ", ".join(sorted(unresolved))))

        # 5. OPTIONS BINDING: index options by (scope, source_path); each migrate/move row binds to exactly
        #    one option; a keep/unresolved row binds to none; a dangling option (no worksheet row) is a
        #    finding (collision surface). Also the note-is-not-config rule (DIV-2): a non-empty note on a
        #    migrate/move row is CANNOT_EVALUATE.
        opt_by_key, dispo_by_key = {}, {}
        for r in rows:
            dispo_by_key[(r["scope"], r["source_path"])] = r
        for o in options["option"]:
            key = (o["scope"], o["source_path"])
            if key in opt_by_key:
                raise _finding("duplicate ingest-options binding for {}".format(key))
            if key not in dispo_by_key:
                raise _finding("ingest-options binds {} which is not a worksheet row".format(key))
            opt_by_key[key] = o

        # 6. PER-DISPOSITION COMPOSITION over the RESOLVED source identities (round-2 P1-2). A worksheet
        #    source_path is SCOPE-relative: a store-scope row is STORE-relative (detect emits it via the
        #    store descriptor), a declared row PRODUCT-relative. Each row is resolved BY SCOPE to the one
        #    product-root-relative path the staging layer (plan_import, which reads under the product root)
        #    will read: identity for declared scope, `_reanchor_declared` for store scope (identity on an
        #    inline store, `ops/...` on a `dir:ops` relocation). A store that resolves OUTSIDE the product
        #    root has no product-relative spelling for its store-scope rows, so planning them through the
        #    product-root staging reader is refused CANNOT-EVALUATE (disclosed residual), never a read of a
        #    same-spelled impostor product path.
        try:
            resolution = _opf_store.resolve_store(product_root)
            if resolution.status != _opf_store.RESOLVED:
                raise _cannot("store did not resolve for planning ({}: {}); fail-closed".format(
                    resolution.status, resolution.detail))
        except ValueError as exc:
            raise _cannot("cannot parse store pointer/manifest for {!r} ({})".format(product_root, exc))
        # round-2 P1-3: the move boundary is graded against the RESOLVED store working subtree (the same
        # authority the declared-scope exclusion uses), not only the literal product-root `.working/`.
        store_working_rel = _store_working_under_product(resolution)
        # The re-anchor base captured ONCE (the single input the shared derivation builders need); the gate
        # recovers the same base as a consistency binding over the frozen crosswalk store rows and declared
        # keep actions.
        reanchor_base = _store_base_under_product(resolution)

        def _resolve_by_scope(scope, rel):
            resolved = resolve_by_scope(reanchor_base, scope, rel)
            if resolved is None:   # a store row whose store resolves OUTSIDE the product root
                raise _cannot("store-scope row {!r} belongs to a store that resolves OUTSIDE the product "
                              "root, which this build cannot stage through the product-root import reader "
                              "(fail-closed, disclosed residual)".format(rel))
            return resolved

        product_root_fd = _opf_store._open_root_fd(Path(os.path.abspath(product_root)))
        try:
            import_set, importer_proposals, candidates_draft, actions = [], [], [], []
            move_dests = {}
            # round-4 F2: the COMPLETE resolved product-relative source set, computed ONCE before the walk so
            # every move destination is admitted against every source (including a later row's), through the
            # SAME pure resolve_by_scope the review gate re-derives with. A store row whose store resolves
            # outside the product root (None) is refused CANNOT-EVALUATE in the walk below.
            all_sources = {p for p in (resolve_by_scope(reanchor_base, r["scope"], r["source_path"])
                                       for r in rows) if p is not None}
            expected = {}   # resolved product-relative path -> the reconciled (sha256, size) it must stage
            # MIG-PR4a review evidence: the scoped resolution crosswalk ((scope, source_path) -> resolved
            # product-relative source, per disposition) and each migrate row's importer selection + validated
            # result counts (a zero-candidate / zero-proposal migrate is preserved as an explicit row).
            crosswalk, migrate_records = [], []
            for r in rows:
                key = (r["scope"], r["source_path"]); sp = r["source_path"]; dispo = r["disposition"]
                opt = opt_by_key.get(key)
                # The per-disposition binding / note / field admissibility comes from the SHARED pure
                # admit_row_binding authority the MIG-PR4b review gate also applies to the frozen worksheet +
                # options (single authority; see admit_row_binding).
                admit_row_binding(r, opt)
                # Static scope admissibility (the SHARED admit_row_scope authority the review gate applies to
                # the frozen worksheet): defence in depth behind the reconcile above, which already requires
                # every row to be one detection emitted.
                admit_row_scope(r["scope"], sp, reanchor_base)
                resolved_sp = _resolve_by_scope(r["scope"], sp)
                if resolved_sp in expected:
                    raise _cannot("worksheet row {!r} and another row both resolve to product path {!r}; "
                                  "the staged run cannot bind one path to two identities "
                                  "(fail-closed)".format(sp, resolved_sp))
                # round-2 P1-2/P1-4: BIND the bytes staging will read to the reconciled worksheet identity.
                # Re-read the source at its RESOLVED path immediately before composition and require digest
                # and size to equal the reconciled row's; an impostor at a same-spelled product path, or a
                # write racing the reconcile-to-stage window, is CANNOT-EVALUATE, never a clean plan whose
                # action.sha256 diverges from the staged bytes. plan_import re-reads the file after this
                # check, so the staged run is re-verified AGAINST the worksheet once staging returns (the
                # post-stage binding at step 7), closing the residual window between this read and the
                # staging read.
                digest, size = _digest_of(product_root_fd, resolved_sp)
                if digest != r["sha256"] or size != r["size"]:
                    raise _cannot("source {!r} (resolved {!r}) does not match its reconciled worksheet "
                                  "identity (worksheet {} / {} bytes, observed {} / {} bytes); the tree "
                                  "changed between reconcile and staging, or the resolved path carries "
                                  "different bytes; fail-closed".format(
                                      sp, resolved_sp, r["sha256"], r["size"], digest, size))
                expected[resolved_sp] = (r["sha256"], r["size"])
                if dispo == "keep":
                    # decision 2: retain-in-place + emit an [unmanaged] exemption for an out-of-.working
                    # file. Mapping stays unmapped. R-2: the "checker never later flags it" guarantee is
                    # CONDITIONAL on PR-C applying retention + [unmanaged].paths registration ATOMICALLY
                    # (one journaled transaction; see _keep_action).
                    actions.append(_keep_action(product_root, resolution, r))
                elif dispo == "move":
                    dest = derive_move_dest(opt, sp)
                    # The PURE admissions first, in the review gate's order (boundary, then the move set:
                    # duplicate destination / move-to-self / a worksheet-source conflict / nesting, the SHARED
                    # admit_move_dest authority, recording dest on success), so a frozen-decidable refusal
                    # carries the SAME verdict here as at review; the live no-overwrite lstat follows.
                    admit_move_boundary(dest, store_working_rel)
                    admit_move_dest(sp, dest, move_dests, all_sources)
                    _check_move_boundary(product_root, dest, product_root_fd,
                                         store_working_rel)   # FINDING on any live collision
                    actions.append(derive_move_action(r, dest))
                elif dispo == "migrate":
                    src = _opf_import._read_sources(product_root_fd, [resolved_sp])[0]
                    if "sha256:" + src["sha256"] != r["sha256"]:
                        raise _cannot("migrate source {!r} (resolved {!r}) changed between its binding "
                                      "verification and the importer read; fail-closed".format(
                                          sp, resolved_sp))
                    ir = _opf_importers.run_importer(opt["importer_kind"], src)
                    verdict, findings = _opf_importers.validate_importer_output(ir, src)
                    if verdict != CLEAN:
                        return _opf_import.PlanResult(verdict, findings)
                    importer_proposals += ir.proposals
                    candidates_draft += [dict(c, source_path=sp, importer_kind=opt["importer_kind"])
                                         for c in ir.candidates]
                    # MIG-PR4a: record the importer selection + its VALIDATED result counts per migrate row,
                    # so a zero-candidate / zero-proposal migrate (a CLEAN importer run that rested nothing)
                    # is preserved as an explicit review-evidence row rather than leaving no trace.
                    migrate_records.append(dict(
                        derive_migrate_scaffold(r, resolved_sp, opt["importer_kind"]),
                        candidate_count=len(ir.candidates), proposal_count=len(ir.proposals)))
                # keep/migrate/move ALL stay in the import_set so the baseline quarantines them as
                # legacy_fragment (unmapped); nothing is dropped (decisions 2/3: mapping stays unmapped).
                # The import_set carries the RESOLVED product-relative path (the identity the staging
                # reader actually reads), so a relocated store's store-scope row stages the STORE bytes.
                crosswalk.append(derive_crosswalk_row(r, resolved_sp))
                import_set.append(resolved_sp)
        finally:
            os.close(product_root_fd)

        # MIG-PR4a: assemble the FROZEN review evidence for plan_import to stage LAST (after the marker and
        # every payload). The worksheet embeds the ORIGINAL triage (scope/path/origin/disposition/note plus
        # its recomputed worksheet_digest); the options doc is normalized to its closed shape (a move option's
        # ABSENT dest_path records the archive default vs an explicit dest); include is preserved as declared;
        # `expected` lets the bundle writer re-check the staged source identities before it finalizes.
        # round-4 F6: the drafts are staged in the SHARED normalized order the review gate re-derives.
        candidates_draft = sort_candidate_rows(candidates_draft, migrate_records)
        normalized_options = {
            "format": options.get("format"), "schema": options.get("schema"),
            "option": [{k: o[k] for k in _OPTION_ROW_KEYS if k in o} for o in options["option"]]}
        ingest_review_inputs = {
            "worksheet": worksheet, "options": normalized_options, "include": include,
            "crosswalk": crosswalk, "migrate": migrate_records, "expected": expected}

        # 7. STAGE via the op-layer plan_import: baseline (all unmapped -> legacy_fragment), importer
        #    proposals tagged importer_proposal, ingest-actions.toml (the non-promotable refusal marker,
        #    staged FIRST among the ingest artefacts and ahead of the review artefacts; P1-1),
        #    candidates_draft.toml, then the MIG-PR4a frozen review bundle (ingest-review.toml, staged LAST).
        result = _opf_import.plan_import(product_root, import_set, importer_proposals=importer_proposals,
                                         candidates_draft=candidates_draft, ingest_actions=actions,
                                         ingest_review_inputs=ingest_review_inputs,
                                         now=now, run_nonce=run_nonce)
        if result.verdict != CLEAN:
            return result
        # POST-STAGE BINDING (round-2 P1-4): the staged run's OWN records are re-read and required to equal
        # the reconciled worksheet identities: run.toml (the digests of the bytes stage_import actually read
        # and preserved) and inventory.toml (the review surface a later acceptance binds). A write racing
        # the window between the pre-stage verification above and plan_import's own reads therefore cannot
        # yield a CLEAN plan whose staged bytes diverge from action.sha256. On a mismatch the verdict is
        # CANNOT-EVALUATE; the refused staged run is left behind NON-PROMOTABLE (its first-staged ingest
        # artefact is the refusal marker, P1-1) and named in the message.
        _verify_staged_against_worksheet(resolution, result.run_rel, result.run_id, expected)
        return result
    except _DetectError as exc:
        return _opf_import.PlanResult(exc.verdict, [exc.message])
    except _opf_import._StageError as exc:
        return _opf_import.PlanResult(exc.verdict, [exc.message])
    except _journal.JournalError as exc:
        return _opf_import.PlanResult(CANNOT_EVALUATE, ["{}; fail-closed".format(exc)])
    except OSError as exc:
        return _opf_import.PlanResult(CANNOT_EVALUATE, ["fail-closed on a filesystem read error: {}".format(exc)])
    except RecursionError as exc:
        return _opf_import.PlanResult(CANNOT_EVALUATE, ["fail-closed on excessive input nesting: {}".format(exc)])


def _archive_dest(source_path):
    """decision 3: `.archive/<source_path>` - substructure preserved for provenance, collision-free, and a
    single dotdir under root OUTSIDE .working (root stays clean). posixpath.join keeps forward slashes."""
    return posixpath.join(ARCHIVE_DIRNAME, source_path)


def _reconcile_worksheet_against_detect(worksheet, fresh):
    """EXACT-SET reconciliation on the immutable fields. Membership by (scope, source_path); a ghost
    (worksheet-only), an omission (detect-only), or a sha256/size drift is fail-closed. A mismatch on a
    detect-derived field is CANNOT_EVALUATE (the tree moved under the triage); membership drift is a
    FINDING (the worksheet describes a different set)."""
    def keyset(rows):
        return {(r["scope"], r["source_path"]): (r["sha256"], r["size"]) for r in rows}
    w, f = keyset(worksheet["row"]), keyset(fresh.rows)
    only_w = sorted(set(w) - set(f)); only_f = sorted(set(f) - set(w))
    if only_w or only_f:
        raise _finding("worksheet/detect set mismatch: worksheet-only={}, detect-only={} (re-run detect "
                       "and re-triage)".format(only_w, only_f))
    drift = sorted(k for k in w if w[k] != f[k])
    if drift:
        raise _cannot("worksheet sha256/size drifted from a fresh detect for {} (a file changed between "
                      "detection and planning); fail-closed".format(drift))


def _verify_staged_against_worksheet(resolution, run_rel, run_id, expected):
    """POST-STAGE BINDING (round-2 P1-4): re-read the staged run's run.toml (the digests of the bytes
    stage_import actually read and content-addressed into `sources/`) and inventory.toml (the review
    surface) and require their source sets and digests to EQUAL the reconciled worksheet identities in
    `expected` (resolved product-relative path -> (sha256, size)). Any divergence means the tree moved
    between the planner's reconcile/verification reads and the staging layer's own reads, so the plan no
    longer describes the staged bytes: CANNOT-EVALUATE naming the run, which stays behind NON-PROMOTABLE
    (its first-staged ingest artefact is the refusal marker; P1-1). Fail-closed on anything unreadable or
    malformed, never a silent pass (check-fails-closed-on-unreadable)."""
    try:
        store_fd = _opf_store._open_store_root_fd(resolution.store_root,
                                                  resolution.pointer_source != "default")
    except OSError as exc:
        raise _cannot("cannot open store root {!r} to verify the staged run against the worksheet ({}); "
                      "fail-closed, the staged run {} is refused non-promotable".format(
                          resolution.store_root, exc, run_id))
    try:
        for name in ("run.toml", "inventory.toml"):
            rel = run_rel + "/" + name
            try:
                doc = _opf_store._read_toml_contained(store_fd, rel)
            except _opf_store.StoreError as exc:
                raise _cannot("cannot re-read staged {} for the worksheet binding ({}); fail-closed, the "
                              "staged run {} is refused non-promotable".format(rel, exc, run_id))
            rows = doc.get("source") if isinstance(doc, dict) else None
            if not isinstance(rows, list):
                raise _cannot("staged {} carries no source list; the worksheet binding cannot be verified "
                              "(fail-closed, the staged run {} is refused non-promotable)".format(rel, run_id))
            seen = {}
            for s in rows:
                if not (isinstance(s, dict) and isinstance(s.get("path"), str)
                        and isinstance(s.get("sha256"), str) and type(s.get("size")) is int):
                    raise _cannot("staged {} carries a malformed source row; the worksheet binding cannot "
                                  "be verified (fail-closed, the staged run {} is refused "
                                  "non-promotable)".format(rel, run_id))
                seen[s["path"]] = ("sha256:" + s["sha256"], s["size"])
            if len(seen) != len(rows):
                raise _cannot("staged {} carries a duplicate source path; the worksheet binding cannot be "
                              "verified (fail-closed, the staged run {} is refused non-promotable)".format(
                                  rel, run_id))
            if set(seen) != set(expected):
                raise _cannot("staged {} source set {} does not equal the reconciled worksheet set {}; the "
                              "tree changed under staging (fail-closed, the staged run {} is refused "
                              "non-promotable)".format(rel, sorted(seen), sorted(expected), run_id))
            drift = sorted(path for path in expected if seen[path] != expected[path])
            if drift:
                raise _cannot("staged {} digests drifted from the reconciled worksheet for {} (a file "
                              "changed between reconcile/verification and staging); fail-closed, the "
                              "staged run {} is refused non-promotable".format(rel, drift, run_id))
    finally:
        os.close(store_fd)


def _check_move_boundary(product_root, dest, product_root_fd, store_working_rel):
    """decision 3 move boundary: dest is a contained relpath beneath the product root but STRICTLY OUTSIDE
    the resolved .working tree, with a hard NO-OVERWRITE rule. An existing file OR a dangling symlink at
    dest is a collision (FINDING); a dest inside the store working tree, or move-to-self/dup-dest
    (caller-checked), is a FINDING. `store_working_rel` is the RESOLVED store's `.working` subtree as a
    product-relative path (`_store_working_under_product`: ".working" inline, "ops/.working" on a `dir:ops`
    relocation, None when the store resolves outside the product root), so a relocated store's working tree
    refuses a dest exactly as the inline literal does (round-2 P1-3); the literal product-root `.working/`
    stays refused too (the pre-relocation contract, and the inline case where both tests coincide). Uses
    the no-follow lstat so a symlink at dest is seen as a symlink, never followed."""
    admit_move_boundary(dest, store_working_rel)
    st = _journal._lstat_contained(product_root_fd, dest)
    if st is not None:
        raise _finding("move destination {!r} already exists (no-overwrite); a move-to-an-existing-file or "
                       "dangling symlink is a collision".format(dest))


def admit_move_boundary(dest, store_working_rel):
    """The PURE part of the decision-3 move boundary (no filesystem read): a destination inside the literal
    product-root `.working/`, or inside the RESOLVED store working tree `store_working_rel` (None when the
    store resolves outside the product root), is a FINDING _DetectError. Shared by `_check_move_boundary`
    (which adds the live no-overwrite lstat) and the MIG-PR4b review gate."""
    if dest.split("/", 1)[0] == _opf_store.WORKING_DIRNAME:
        raise _finding("move destination {!r} lies inside .working/ (the store tree); a move target must "
                       "be beneath the product root but OUTSIDE .working".format(dest))
    if store_working_rel is not None and (dest == store_working_rel
                                          or dest.startswith(store_working_rel + "/")):
        raise _finding("move destination {!r} lies inside the RESOLVED store working tree {!r}; a move "
                       "target must be beneath the product root but OUTSIDE the store".format(
                           dest, store_working_rel))


def _declared_to_store_rel(resolution, rel):
    """The STORE-relative spelling of a DECLARED (product-root-relative) path: the exact inverse of
    `_reanchor_declared`, so `_reanchor_declared(resolution, (result,)) == (rel,)` whenever a spelling
    exists (both sides are the byte-canonical POSIX spelling the walk produces, so the round trip is an
    equality, not a heuristic). Returns None when no contained store-relative spelling exists, because the
    store resolves OUTSIDE the product root or `rel` does not fall under the resolved store root: an
    `[unmanaged].paths` entry is a STORE-relative vocabulary (spec 14.2; see `_managed_paths`), so no entry
    can name such a path and the caller must refuse rather than emit a spelling that re-anchors to a
    different file."""
    base = _store_base_under_product(resolution)
    if base is None:
        return None
    return _store_rel_of(base, rel)


def _keep_action(product_root, resolution, r):
    """One inert keep pending-action (decision 2: retain-in-place + an [unmanaged] exemption). R-2
    DISCLOSURE (the atomicity condition, round-2 P2-7): `unmanaged_path` is a PENDING registration; the
    "steady-state checker never flags it" guarantee holds ONLY once PR-C applies the retention AND the
    [unmanaged].paths registration ATOMICALLY, in ONE journaled transaction covering both. Until that
    atomic apply, and under any non-atomic application (registration without retention, or retention
    without registration), the kept file remains a checker-detectable stray or the cover names a missing
    path, and the checker MAY flag either state; the plan itself changes nothing, so planning opens no such
    window. This is a disclosed residual of the plan slice, not a guarantee the plan can make.

    `unmanaged_path` is anchored PER SCOPE (round-3 F2). An [unmanaged].paths entry is STORE-relative
    (detection re-anchors it at the resolved store root for declared scope, `_reanchor_declared`), while a
    worksheet source_path is SCOPE-relative (a store row store-relative, a declared row product-relative).
    A store-scope keep therefore emits its source_path verbatim, and a declared-scope keep emits the
    STORE-relative spelling of its product path (`_declared_to_store_rel`; identity on an inline store), so
    the registered exemption re-anchors back to the file actually kept, never to a same-spelled store path
    (the pre-fix defect: a declared keep on a `dir:ops` store emitted the product spelling, which
    re-anchored to `ops/<path>`, exempting the WRONG file while the kept one stayed detected). A declared
    keep whose product path does not fall under the resolved store root has NO contained store-relative
    spelling, so it is refused CANNOT-EVALUATE (a disclosed residual, never a wrong-file exemption). The
    emitted spelling is round-tripped through `_reanchor_declared` before it is trusted, so the exemption
    is validated against the SAME authority detection will re-anchor it with (guard-input-soundness)."""
    # The [unmanaged].paths spelling comes from the SHARED admit_keep_unmanaged authority (store: verbatim;
    # declared: the store-relative spelling, refused when none re-anchors back to the kept file), the same
    # one the review gate re-derives with. `_reanchor_rel(base, x)` is exactly `_reanchor_declared`'s
    # per-entry formula, so the round trip is unchanged.
    unmanaged = admit_keep_unmanaged(_store_base_under_product(resolution), r["scope"], r["source_path"])
    # PR-C must add unmanaged_path to [unmanaged].paths ATOMICALLY with the retention (one journaled
    # transaction) BEFORE the "checker never flags it" guarantee holds (R-2; see the docstring).
    return derive_keep_action(r, unmanaged)


# --- self-test ---------------------------------------------------------------------------------------

def _self_test_planner(check, build_store, build_relocated, snapshot, symlink_supported,
                       *, gate=False):
    """MIG-PR3 representative vectors, shared by the gate and module registries.

    Literal expected origins and destinations are independent of production constants.
    This does not claim exhaustive command-grammar or filesystem coverage.
    """
    import datetime
    import shutil
    import subprocess
    import tomllib
    from contextlib import contextmanager

    now = datetime.datetime(2026, 9, 9, 12, tzinfo=datetime.timezone.utc)

    @contextmanager
    def fixture(files=None, relocated=False):
        root = None
        try:
            if relocated:
                root = build_relocated(product=files or {"legacy/a.md": "source\n"})
                machine = root / "ops/.working/toml"
            else:
                root, machine = build_store(product=files or {"legacy/a.md": "source\n"})
            yield root, machine
        finally:
            if root is not None:
                shutil.rmtree(root)

    def stamp(rows):
        payload = {"format": WORKSHEET_FORMAT, "schema": SCHEMA, "row": rows}
        return dict(payload, worksheet_digest="sha256:" +
                    _opf_import._sha256_hex(_worksheet_bytes(payload)))

    def triage(root, files, disposition):
        found = detect(root, include=[p for p in files if not p.startswith(".working/")] or None)
        check("fixture-detect", found.verdict == CLEAN and
              {r["source_path"] for r in found.rows} == set(files))
        return stamp([dict(r, disposition=disposition, note="") for r in found.rows])

    def options(ws, **fields):
        return {"format": "opf-ingest-options-v1", "schema": 1,
                "option": [dict(scope=r["scope"], source_path=r["source_path"], **fields)
                           for r in ws["row"]]}

    empty = {"format": "opf-ingest-options-v1", "schema": 1, "option": []}

    def plan(root, ws, opts, files):
        return plan_ingest(root, ws, opts,
                           include=[p for p in files if not p.startswith(".working/")] or None,
                           now=now, run_nonce="mig-pr3-test")

    def read(run, name):
        return tomllib.loads((run / name).read_text(encoding="utf-8"))

    def staged(root, machine, ws, opts, files):
        result = plan(root, ws, opts, files)
        check("clean", result.verdict == CLEAN and bool(result.run_id))
        if result.verdict != CLEAN or not result.run_id:
            return None
        return machine.parent / "imports" / result.run_id

    def unmapped(run, files):
        mappings = read(run, "mappings.toml")["mapping"]
        fragments = read(run, "fragments/legacy_fragment.index.toml")["record"]
        return (len(mappings) == len(files) == len(fragments)
                and {r["source_path"] for r in mappings} == set(files)
                and all(r["state"] == "unmapped" for r in mappings)
                and all(r["type"] == "legacy_fragment" for r in fragments)
                and {r["source_path"] for r in fragments} == set(files)
                and all("target" not in r for r in mappings))

    def schema_validator():
        row = {"scope": "declared", "source_path": "legacy/a.md",
               "importer_kind": "github-tasklist"}
        good = dict(empty, option=[row])
        check("accepts-well-formed", validate_options(good) == [])
        bad = [dict(good, unknown=True), dict(good, format="wrong"),
               dict(good, schema=2), dict(good, schema=True),
               dict(good, option=[dict(row, unknown=True)]),
               dict(good, option=[dict(row, importer_kind="unknown")]),
               dict(good, option=[dict(row, dest_path="../escape")])]
        for i, doc in enumerate(bad):
            check("invalid-options-{}".format(i), bool(validate_options(doc)))
            # round-2 P2-5: the PLANNER VERDICT for an invalid-but-well-formed options document is the
            # ratified FINDING (1), asserted on the verdict itself, never only the validator messages
            # (pre-fix the planner mapped every one of these to CANNOT-EVALUATE, so this check FAILS
            # without the fix).
            with fixture() as (root, _machine):
                files = ["legacy/a.md"]
                ws = triage(root, files, "migrate")
                check("invalid-options-verdict-{}".format(i),
                      plan(root, ws, doc, files).verdict == FINDING)
        # an UNREADABLE/UNPARSEABLE options input (not even a table) stays CANNOT-EVALUATE (2), the
        # reserved unparseable-class verdict; flattening the split would turn this check red.
        with fixture() as (root, _machine):
            files = ["legacy/a.md"]
            ws = triage(root, files, "migrate")
            check("nontable-options-cannot-evaluate",
                  plan(root, ws, ["not-a-table"], files).verdict == CANNOT_EVALUATE)
        for disposition in ("migrate", "move"):
            with fixture() as (root, _machine):
                files = ["legacy/a.md"]
                ws = triage(root, files, disposition)
                check("missing-binding-" + disposition,
                      plan(root, ws, empty, files).verdict == CANNOT_EVALUATE)

    def keep():
        # round-3 F2: a keep's exemption is judged by its COVERAGE, not its emitted string. Each round
        # trip registers the EMITTED unmanaged_path in the store manifest (exactly what PR-C will apply)
        # and RE-DETECTS: the kept file must no longer be detected, and no unrelated file may be
        # spuriously excluded. Pre-fix a declared keep on a relocated (`dir:ops`) store emitted the
        # PRODUCT spelling, which re-anchors at the store root to a DIFFERENT file, so every relocated
        # declared leg here FAILS without the fix.
        import shutil as _sh

        def register(machine, unmanaged):
            mp = machine / "manifest.toml"
            mp.write_text(mp.read_text(encoding="utf-8")
                          + '\n[unmanaged]\npaths = ["{}"]\n'.format(unmanaged), encoding="utf-8")

        def emitted(root, machine, files, mapped=None):
            ws = triage(root, files, "keep")
            before = snapshot(machine)
            run = staged(root, machine, ws, empty, files)
            if run is None:
                return None
            check("mapping-unmapped", unmapped(run, mapped or files))
            check("baseline-byte-unchanged", snapshot(machine) == before)
            return read(run, "ingest-actions.toml")["action"]

        # inline: identity anchoring (unchanged behaviour), asserted by string AND by coverage.
        with fixture({"legacy/a.md": "source\n", "other.md": "unrelated\n"}) as (root, machine):
            actions = emitted(root, machine, ["legacy/a.md"])
            if actions is not None:
                check("keep-action", len(actions) == 1 and actions[0]["kind"] == "keep"
                      and actions[0]["source_path"] == "legacy/a.md"
                      and actions[0].get("unmanaged_path") == "legacy/a.md")
                check("source-byte-unchanged", (root / "legacy/a.md").read_bytes() == b"source\n")
                register(machine, actions[0]["unmanaged_path"])
                after = detect(root, include=["legacy/a.md", "other.md"])
                check("keep-coverage-inline", after.verdict == CLEAN and
                      {r["source_path"] for r in after.rows} == {"other.md"})
        # relocated dir:ops, DECLARED keep UNDER the store root: the exemption is the STORE-relative
        # spelling ("legacy/a.md" for product "ops/legacy/a.md"), which re-anchors back to the kept
        # file, while the same-spelled PRODUCT-root file stays detected (no spurious exclusion).
        # Pre-fix the emission was "ops/legacy/a.md" (re-anchoring to ops/ops/legacy/a.md, covering
        # nothing), so both checks FAIL without the fix.
        root = build_relocated(product={"ops/legacy/a.md": "kept\n", "legacy/a.md": "unrelated\n"})
        try:
            machine = root / "ops/.working/toml"
            actions = emitted(root, machine, ["ops/legacy/a.md"])
            if actions is not None:
                check("keep-reloc-declared-store-rel",
                      len(actions) == 1 and actions[0].get("unmanaged_path") == "legacy/a.md")
                register(machine, actions[0]["unmanaged_path"])
                after = detect(root, include=["ops/legacy/a.md", "legacy/a.md"])
                check("keep-coverage-reloc-declared", after.verdict == CLEAN and
                      {r["source_path"] for r in after.rows} == {"legacy/a.md"})
        finally:
            _sh.rmtree(root)
        # relocated dir:ops, STORE-scope keep: a store row's source_path is already store-relative and
        # is emitted verbatim; registered, it covers the kept store file (regression guard).
        root = build_relocated(strays={".working/a.md": "kept\n"})
        try:
            machine = root / "ops/.working/toml"
            actions = emitted(root, machine, [".working/a.md"], mapped=["ops/.working/a.md"])
            if actions is not None:
                check("keep-reloc-store-verbatim",
                      len(actions) == 1 and actions[0].get("unmanaged_path") == ".working/a.md")
                register(machine, actions[0]["unmanaged_path"])
                after = detect(root)
                check("keep-coverage-reloc-store", after.verdict == CLEAN and after.rows == [])
        finally:
            _sh.rmtree(root)
        # relocated dir:ops, DECLARED keep OUTSIDE the store root (the round-3 F2 repro): no contained
        # store-relative spelling exists, so the plan is refused CANNOT-EVALUATE with nothing staged,
        # never a CLEAN plan whose exemption re-anchors to a different file. Pre-fix this was a CLEAN
        # plan emitting unmanaged_path="legacy/a.md" (which registration would re-anchor to the WRONG
        # file, ops/legacy/a.md), so this check FAILS without the fix.
        root = build_relocated(product={"legacy/a.md": "source\n"})
        try:
            machine = root / "ops/.working/toml"
            ws = triage(root, ["legacy/a.md"], "keep")
            result = plan(root, ws, empty, ["legacy/a.md"])
            check("keep-reloc-outside-store-refused", result.verdict == CANNOT_EVALUATE)
            check("keep-reloc-outside-store-nothing-staged",
                  not (machine.parent / "imports").exists())
        finally:
            _sh.rmtree(root)

    def migrate():
        for kind, body in (("github-tasklist", "- [ ] one\n- [x] two\n"),
                           ("keepachangelog", "## [1.0.0] - 2026-01-01\n### Added\n- One\n")):
            files = ["legacy/a.md"]
            with fixture({files[0]: body}) as (root, machine):
                ws = triage(root, files, "migrate")
                run = staged(root, machine, ws, options(ws, importer_kind=kind), files)
                if run is None:
                    continue
                proposals = read(run, "proposals.toml")["proposal"]
                drafts = read(run, "candidates_draft.toml")["candidate"]
                check("importer-proposal-" + kind, bool(proposals) and
                      all(p["origin"] == "importer_proposal" for p in proposals))
                check("nonempty-drafts-" + kind, bool(drafts) and
                      all(c["source_path"] == files[0] and c["importer_kind"] == kind
                          for c in drafts))
                check("mapping-unmapped-" + kind, unmapped(run, files))

    def archive():
        for explicit in (False, True):
            with fixture() as (root, machine):
                files = ["legacy/a.md"]
                ws = triage(root, files, "move")
                opts = options(ws, **({"dest_path": "saved/override.md"} if explicit else {}))
                check("dest-presence", ("dest_path" in opts["option"][0]) == explicit)
                run = staged(root, machine, ws, opts, files)
                if run is None:
                    continue
                actions = read(run, "ingest-actions.toml")["action"]
                expected = "saved/override.md" if explicit else ".archive/legacy/a.md"
                check("resolved-dest", len(actions) == 1 and actions[0]["kind"] == "move"
                      and actions[0]["source_path"] == files[0]
                      and actions[0]["dest_path"] == expected)
                check("move-inert", (root / files[0]).read_bytes() == b"source\n"
                      and not (root / expected).exists())

    def collisions():
        cases = ["dup-dest", "move-to-self", "dest-in-working", "existing-file"]
        if symlink_supported():
            cases.append("dangling-symlink")
        for case in cases:
            files = ["legacy/a.md", "legacy/b.md"] if case == "dup-dest" else ["legacy/a.md"]
            # Store-scope source avoids the unrelated declared-scope symlink walk refusal.
            if case == "dangling-symlink":
                files = [".working/legacy/a.md"]
            with fixture({p: "source\n" for p in files}) as (root, machine):
                ws = triage(root, files, "move")
                dest = {"move-to-self": files[0], "dest-in-working": ".working/new.md"}.get(
                    case, "occupied.md")
                if case == "existing-file":
                    (root / dest).write_text("existing\n", encoding="utf-8")
                elif case == "dangling-symlink":
                    os.symlink("absent-target", root / dest)
                check(case, plan(root, ws, options(ws, dest_path=dest), files).verdict == FINDING)
                check(case + "-nothing-staged", not (machine.parent / "imports").exists())
        # round-2 P1-3: a dest inside the RESOLVED store working tree of a RELOCATED (`dir:ops`) store is
        # a FINDING exactly like the inline literal `.working/` dest (pre-fix the literal-prefix test let
        # `ops/.working/new.md` through CLEAN, so this check FAILS without the fix).
        with fixture(relocated=True) as (root, machine):
            files = ["legacy/a.md"]
            ws = triage(root, files, "move")
            check("dest-in-resolved-working",
                  plan(root, ws, options(ws, dest_path="ops/.working/new.md"), files).verdict == FINDING)
            check("dest-in-resolved-working-nothing-staged", not (machine.parent / "imports").exists())
        # round-3 F3: two planned move destinations in ANCESTOR/DESCENDANT relation (saved/a +
        # saved/a/b, both absent, on the reported dir:ops store shape) are an IMPOSSIBLE move set: one
        # needs saved/a as a regular file, the other as a directory. Pre-fix only EXACT duplicates were
        # rejected, so the nested pair staged CLEAN; each nested leg here FAILS without the fix. A
        # non-conflicting pair (saved/a + saved/b) and a name-prefix sibling (saved/a + saved/ab) stay
        # CLEAN, pinning the predicate to path components, never a bare string prefix.
        import shutil as _sh

        def move_pair(dest_a, dest_b):
            root = build_relocated(strays={".working/a.md": "one\n", ".working/b.md": "two\n"})
            try:
                machine = root / "ops/.working/toml"
                files = [".working/a.md", ".working/b.md"]
                ws = triage(root, files, "move")
                opts = {"format": OPTIONS_FORMAT, "schema": SCHEMA, "option": [
                    {"scope": "store", "source_path": files[0], "dest_path": dest_a},
                    {"scope": "store", "source_path": files[1], "dest_path": dest_b}]}
                result = plan(root, ws, opts, files)
                return result.verdict, (machine.parent / "imports").exists()
            finally:
                _sh.rmtree(root)

        for label, pair, want in (("nested-dest", ("saved/a", "saved/a/b"), FINDING),
                                  ("nested-dest-reversed", ("saved/a/b", "saved/a"), FINDING),
                                  ("nested-dest-clean-pair", ("saved/a", "saved/b"), CLEAN),
                                  ("nested-dest-sibling-name", ("saved/a", "saved/ab"), CLEAN)):
            verdict, staged_run = move_pair(*pair)
            check(label, verdict == want and staged_run == (want == CLEAN))

        archive()  # clean companion: an always-FINDING guard must fail too

    def fail_closed():
        schema_validator()
        with fixture() as (root, _machine):
            files = ["legacy/a.md"]
            ws = triage(root, files, "move")
            ws = stamp([dict(r, note="dest_path=saved/a.md") for r in ws["row"]])
            check("nonempty-note", plan(root, ws, options(ws), files).verdict == CANNOT_EVALUATE)
        # Positive control prevents an unrelated CLI/flag failure from passing the malformed leg.
        for malformed in (False, True):
            with fixture() as (root, _machine):
                files = ["legacy/a.md"]
                ws = triage(root, files, "move")
                wp, op = root / "worksheet.toml", root / "options.toml"
                wp.write_bytes(_worksheet_bytes(ws))
                op.write_bytes(b"option = [\n" if malformed else _worksheet_bytes(options(ws)))
                proc = subprocess.run(
                    [sys.executable, "-I", "-B", str(Path(__file__).with_name("opf.py")),
                     "import", "--root", str(root), "--plan", "--dispositions", str(wp),
                     "--ingest-options", str(op), "--include", files[0]],
                    capture_output=True, check=False)
                check("cli-malformed" if malformed else "cli-valid",
                      proc.returncode == (2 if malformed else 0))

    def reconcile():
        for case, verdict in (("digest-drift", CANNOT_EVALUATE), ("omission", FINDING),
                              ("ghost", FINDING)):
            with fixture() as (root, machine):
                files = ["legacy/a.md"]
                ws = triage(root, files, "keep")
                if case == "digest-drift":
                    (root / files[0]).write_bytes(b"SOURCE\n")  # same size, changed digest
                elif case == "omission":
                    ws = stamp([])
                else:
                    ws = stamp(ws["row"] + [dict(ws["row"][0], source_path="legacy/ghost.md")])
                check(case + "-valid-worksheet", validate_worksheet(ws) == [])
                before = snapshot(root)
                check(case, plan(root, ws, empty, files).verdict == verdict)
                check(case + "-nothing-staged", snapshot(root) == before
                      and not (machine.parent / "imports").exists())

    def unresolved():
        with fixture({"legacy/a.md": "one", "legacy/b.md": "two"}) as (root, machine):
            files = ["legacy/a.md", "legacy/b.md"]
            ws = triage(root, files, "keep")
            ws = stamp([dict(r, disposition="unresolved" if i == 0 else "keep")
                        for i, r in enumerate(ws["row"])])
            before = snapshot(root)
            check("one-unresolved", plan(root, ws, empty, files).verdict == FINDING)
            check("nothing-staged", snapshot(root) == before
                  and not (machine.parent / "imports").exists())

    def disposition_bindings():
        # round-2 P2-6: REQUIRED + PERMITTED fields per disposition, judged on the PLANNER VERDICT: a
        # FORBIDDEN field present is a FINDING, a REQUIRED field absent on a present binding is a
        # FINDING (round-3 F1), and a keep row forbids any binding at all. Discriminators: pre-fix,
        # migrate with a bare binding CRASHED with an uncaught KeyError (fail-open), and each
        # incompatible combination returned a silent CLEAN, so every check here FAILS without the fix.
        cases = [
            ("migrate-bare-binding", "migrate", {"importer_kind": None}, FINDING),
            ("migrate-forbids-dest-path", "migrate",
             {"importer_kind": "github-tasklist", "dest_path": "saved/x.md"}, FINDING),
            ("move-forbids-importer-kind", "move",
             {"dest_path": "saved/x.md", "importer_kind": "github-tasklist"}, FINDING),
            ("keep-forbids-binding", "keep", {"importer_kind": None}, FINDING),
            ("keep-forbids-dest-path", "keep", {"dest_path": "saved/x.md"}, FINDING),
        ]
        for label, dispo, fields, want in cases:
            fields = {k: v for k, v in fields.items() if v is not None}
            with fixture({"legacy/a.md": "- [ ] one\n"}) as (root, machine):
                files = ["legacy/a.md"]
                ws = triage(root, files, dispo)
                result = plan(root, ws, options(ws, **fields), files)
                check(label, result.verdict == want)
                check(label + "-nothing-staged", not (machine.parent / "imports").exists())

    def relocated_store_scope():
        # round-2 P1-2: on a RELOCATED (`dir:ops`) store a STORE-scope row resolves under the RESOLVED
        # store root and the staged bytes are BOUND to the reconciled worksheet digest. Pre-fix the raw
        # store-relative source_path was read at the PRODUCT root: with a same-length impostor planted at
        # the literal product path the plan staged the IMPOSTOR bytes under a CLEAN verdict (action.sha256
        # = original, staged body = impostor), and with no impostor it failed on a phantom absent source;
        # every leg here FAILS without the fix.
        import hashlib
        import shutil as _sh
        body = b"- [ ] one\n"
        orig_hex = hashlib.sha256(body).hexdigest()
        imp_hex = hashlib.sha256(b"- [x] IMP\n").hexdigest()
        for dispo, fields, impostor in (("keep", None, True),
                                        ("migrate", {"importer_kind": "github-tasklist"}, False),
                                        ("move", {"dest_path": "saved/x.md"}, False)):
            root = build_relocated(strays={".working/a.md": body.decode("utf-8")})
            try:
                machine = root / "ops/.working/toml"
                if impostor:
                    ip = root / ".working" / "a.md"
                    ip.parent.mkdir(parents=True, exist_ok=True)
                    ip.write_bytes(b"- [x] IMP\n")   # same length, different bytes
                files = [".working/a.md"]
                ws = triage(root, files, dispo)
                opts = options(ws, **fields) if fields else empty
                run = staged(root, machine, ws, opts, files)
                if run is None:
                    continue
                run_doc = read(run, "run.toml")["source"]
                inv_doc = read(run, "inventory.toml")["source"]
                check("reloc-store-bytes-" + dispo,
                      [s["path"] for s in run_doc] == ["ops/.working/a.md"]
                      and all(s["sha256"] == orig_hex for s in run_doc)
                      and [s["path"] for s in inv_doc] == ["ops/.working/a.md"]
                      and all(s["sha256"] == orig_hex for s in inv_doc)
                      and (run / "sources" / orig_hex).read_bytes() == body)
                mappings = read(run, "mappings.toml")["mapping"]
                check("reloc-resolved-mapping-" + dispo,
                      set(m["source_path"] for m in mappings) == set(["ops/.working/a.md"]))
                if impostor:
                    check("reloc-impostor-never-staged-" + dispo,
                          not (run / "sources" / imp_hex).exists())
                # MIG-PR4b: exercise the gate's re-derivation on a RELOCATED STORE-SCOPE run, so the store-row
                # branch of the base recovery (base = "ops", not ".") and resolve_by_scope re-anchor
                # are covered (the declared-scope pr4b fixture only ever recovers base "."). A coherent
                # relocated store-scope run must PASS all six ingest checks: the frozen crosswalk / actions /
                # migrate scaffold re-derive-and-equal succeed under the recovered "ops" base.
                import check_opf_import as _chk_reloc
                _gres = _chk_reloc.check_staged_run(str(run))
                check("reloc-gate-store-scope-rederive-" + dispo,
                      all(_gres[cid][0] for cid in ("ingest-run-structure",) + _chk_reloc._INGEST_CHECK_IDS))
            finally:
                _sh.rmtree(root)

    def reconcile_stage_window():
        # round-2 P1-4, leg 1 (pre-stage verification): a same-length mutation BETWEEN the reconcile and
        # the composition read is CANNOT-EVALUATE with nothing staged. Pre-fix the plan returned CLEAN with
        # the mutation staged under the original action.sha256 (the existing reconcile test mutates BEFORE
        # planning and cannot see this window), so this check FAILS without the fix.
        with fixture({"legacy/a.md": "original body\n"}) as (root, machine):
            files = ["legacy/a.md"]
            ws = triage(root, files, "keep")
            real_reconcile = _reconcile_worksheet_against_detect

            def mutate_after_reconcile(worksheet, fresh):
                real_reconcile(worksheet, fresh)
                (root / files[0]).write_bytes(b"MUTATION body\n")

            globals()["_reconcile_worksheet_against_detect"] = mutate_after_reconcile
            try:
                result = plan(root, ws, empty, files)
            finally:
                globals()["_reconcile_worksheet_against_detect"] = real_reconcile
            check("window-prestage-cannot-evaluate", result.verdict == CANNOT_EVALUATE)
            check("window-prestage-nothing-staged", not (machine.parent / "imports").exists())
        # leg 2 (post-stage binding): a mutation AFTER every planner read but BEFORE the staging layer's
        # own reads (injected around plan_import itself) is caught by re-reading the STAGED run's records
        # against the worksheet: CANNOT-EVALUATE, and the refused leftover run is NON-PROMOTABLE because
        # it carries the refusal marker as its first-staged ingest artefact (P1-1). Pre-fix this leg was a
        # CLEAN plan whose staged digests diverged from action.sha256.
        with fixture({"legacy/a.md": "original body\n"}) as (root, machine):
            files = ["legacy/a.md"]
            ws = triage(root, files, "keep")
            real_plan_import = _opf_import.plan_import

            def mutate_then_plan(*args, **kwargs):
                (root / files[0]).write_bytes(b"MUTATION body\n")
                return real_plan_import(*args, **kwargs)

            _opf_import.plan_import = mutate_then_plan
            try:
                result = plan(root, ws, empty, files)
            finally:
                _opf_import.plan_import = real_plan_import
            check("window-poststage-cannot-evaluate", result.verdict == CANNOT_EVALUATE)
            imports = machine.parent / "imports"
            runs = sorted(imports.iterdir()) if imports.exists() else []
            check("window-poststage-run-refused",
                  len(runs) == 1 and (runs[0] / "ingest-actions.toml").exists()
                  and _opf_import.apply_import(root, runs[0].name, now=now).verdict == CANNOT_EVALUATE)
        # leg 3 (post-stage DUP-PATH guard): a staged run.toml whose [[source]] list REPEATS a path (a
        # DRIFTED sha256 first, the CORRECT row last) is refused CANNOT-EVALUATE by the count guard
        # (len(seen) != len(rows)) BEFORE the last-wins reduction can mask the drifted occurrence. This leg
        # FAILS without the `len(seen) != len(rows)` guard in _verify_staged_against_worksheet: with it
        # removed, last-wins == expected and the drift check passes, so the divergent staged bytes bind CLEAN.
        with fixture({"legacy/a.md": "source\n"}) as (root, machine):
            files = ["legacy/a.md"]
            ws = triage(root, files, "keep")
            run = staged(root, machine, ws, empty, files)
            if run is not None:
                run_rel = "{}/{}".format(_opf_import.IMPORTS_REL, run.name)
                resolution = _opf_store.resolve_store(root)
                run_src = read(run, "run.toml")["source"]
                expected = {s["path"]: ("sha256:" + s["sha256"], s["size"]) for s in run_src}
                rt = read(run, "run.toml")
                orig_row = rt["source"][0]
                drifted = dict(orig_row, sha256=("1" * 64 if orig_row["sha256"] != "1" * 64 else "0" * 64))
                rt["source"] = [drifted, dict(orig_row)]  # dup path: drifted first, correct (last-wins) last
                (run / "run.toml").write_bytes(_opf_import._emit_bytes(rt, "run.toml"))
                raised = False
                try:
                    _verify_staged_against_worksheet(resolution, run_rel, run.name, expected)
                except _DetectError as exc:
                    raised = (exc.verdict == CANNOT_EVALUATE and "duplicate source path" in exc.message
                              and "run.toml" in exc.message)
                check("window-poststage-dup-path-refused", raised)

    def partial_ingest_stage():
        # round-2 P1-1: the ingest identity/refusal is DURABLE before the run is reviewable or promotable.
        # Three sabotage points around the ingest-artefact write, each judged on returned verdicts:
        #  (a) after-first: failure AFTER the FIRST ingest op leaves marker-without-draft; apply refuses
        #      with the distinct ingest CANNOT-EVALUATE. Pre-fix the first op was candidates_draft and the
        #      review artefacts were already staged, so the leftover was an unmarked, reviewable,
        #      PROMOTABLE run (the reported bypass): this check FAILS without the fix.
        #  (b) draft-without-marker (synthesized by filtering the marker op): apply still refuses via the
        #      candidates_draft defence-in-depth recognition, and the run is NOT reviewable because the
        #      review artefacts are staged only AFTER the ingest artefacts.
        #  (c) before: failure BEFORE any ingest op leaves neither marker nor review artefacts, so review
        #      is CANNOT-EVALUATE and no acceptance (hence no promotion) can exist.
        import _journal as _jn
        for kind in ("after-first", "draft-without-marker", "before"):
            with fixture() as (root, machine):
                files = ["legacy/a.md"]
                ws = triage(root, files, "keep")
                real_apply = _jn.apply_ops

                def sabotaged(store_root_fd, ops, reader, kind=kind):
                    paths = [o.get("path", "") for o in ops]
                    if any(p.endswith("candidates_draft.toml") or p.endswith("ingest-actions.toml")
                           for p in paths):
                        if kind == "before":
                            raise _jn.JournalError("injected: before any ingest op")
                        if kind == "after-first":
                            subset = [o for o in ops
                                      if not o.get("path", "").endswith("candidates_draft.toml")]
                        else:
                            subset = [o for o in ops
                                      if not o.get("path", "").endswith("ingest-actions.toml")]
                        real_apply(store_root_fd, subset, reader)
                        raise _jn.JournalError("injected: partial ingest write")
                    return real_apply(store_root_fd, ops, reader)

                _jn.apply_ops = sabotaged
                try:
                    result = plan(root, ws, empty, files)
                finally:
                    _jn.apply_ops = real_apply
                check(kind + "-plan-cannot-evaluate", result.verdict == CANNOT_EVALUATE)
                imports = machine.parent / "imports"
                runs = sorted(imports.iterdir()) if imports.exists() else []
                if len(runs) != 1:
                    check(kind + "-one-leftover-run", False)
                    continue
                run = runs[0]
                marker = (run / "ingest-actions.toml").exists()
                draft = (run / "candidates_draft.toml").exists()
                reviewable = (run / "inventory.toml").exists() or (run / "proposals.toml").exists()
                rv = _opf_import.review_import(root, run.name, actor="qa", decisions=[], now=now)
                ap = _opf_import.apply_import(root, run.name, now=now)
                if kind == "after-first":
                    check(kind + "-marker-first", marker and not draft and not reviewable)
                    check(kind + "-apply-refused", ap.verdict == CANNOT_EVALUATE and ap.promoted is False
                          and any("root-ingest" in f for f in ap.findings))
                elif kind == "draft-without-marker":
                    check(kind + "-not-reviewable", draft and not marker and not reviewable
                          and rv.verdict == CANNOT_EVALUATE)
                    check(kind + "-apply-refused", ap.verdict == CANNOT_EVALUATE and ap.promoted is False
                          and any("root-ingest" in f for f in ap.findings))
                else:
                    check(kind + "-not-reviewable", not marker and not draft and not reviewable
                          and rv.verdict == CANNOT_EVALUATE)
                    check(kind + "-not-promoted", ap.promoted is False)

    def refuse_apply():
        with fixture() as (root, machine):
            files = ["legacy/a.md"]
            ws = triage(root, files, "keep")
            run = staged(root, machine, ws, empty, files)
            if run is None:
                return
            check("marker-present", read(run, "ingest-actions.toml")["action"] != [])
            check("no-acceptance-manufactured", not (run / "acceptance.json").exists())
            before = snapshot(root)
            result = _opf_import.apply_import(root, run.name, now=now)
            check("refused", result.verdict == CANNOT_EVALUATE and result.promoted is False)
            check("no-acceptance-after-apply", not (run / "acceptance.json").exists())
            check("no-journal", not (root / _opf_import.IMPORT_JOURNAL_REL).exists())
            check("apply-writes-nothing", snapshot(root) == before)

    def review_bundle():
        """OPF-MIGRATE MIG-PR4a: the FROZEN ingest review bundle is staged for every disposition class, binds
        the staged artefacts by digest, embeds the review-only inputs, preserves a zero-candidate/zero-proposal
        migrate, round-trips the bounded structural reader (absent -> None, malformed -> CANNOT-EVALUATE), a
        run is recognized as ingest by the bundle alone, review capture is refused (PR-4c), and a real staged
        migrate run now passes the staged-run gate (the repaired importer-proposal provenance gate). Each check
        FAILS without its production change (change-carries-check)."""
        import check_opf_import as _chk

        def open_fd(root):
            resolution = _opf_store.resolve_store(root)
            return _opf_store._open_store_root_fd(
                resolution.store_root, resolution.pointer_source != "default")

        def load_bundle(root, run):
            fd = open_fd(root)
            try:
                return _opf_import._load_staged_ingest_for_review(
                    fd, "{}/{}".format(_opf_import.IMPORTS_REL, run.name))
            finally:
                os.close(fd)

        def dig(run, name):
            return "sha256:" + _opf_import._sha256_hex((run / name).read_bytes())

        def rebind(run, b):
            """After a mutation, refresh EVERY digest the gate recomputes (report.toml's artefact digests,
            plan_digest and inventory_digest, and the bundle's six bindings), write the bundle `b`, and
            regenerate IMPORT-REPORT.md exactly as the planner would, so only the TARGET guard can fire."""
            rep = read(run, "report.toml")
            for a in rep["artifact"]:
                if (run / a["path"]).is_file():
                    a["sha256"] = _opf_import._sha256_hex((run / a["path"]).read_bytes())
            rep["plan_digest"] = dig(run, "plan.toml")
            inv = read(run, "inventory.toml")
            rep["inventory_digest"] = inv["inventory_digest"]
            (run / "report.toml").write_bytes(_opf_import._emit_bytes(rep, "report.toml"))
            b["binding"].update(
                plan_digest=rep["plan_digest"], inventory_digest=rep["inventory_digest"],
                ingest_actions_digest=dig(run, "ingest-actions.toml"),
                candidates_draft_digest=dig(run, "candidates_draft.toml"),
                proposals_digest=dig(run, "proposals.toml"),
                inventory_toml_digest=dig(run, "inventory.toml"))
            (run / _opf_import.INGEST_REVIEW_NAME).write_bytes(
                _opf_import._emit_bytes(b, _opf_import.INGEST_REVIEW_NAME))
            norm = [{"source_path": p["source_path"], "span": list(p["span"]),
                     "suggested_state": p["suggested_state"], "note": p.get("note", ""),
                     "_origin": p["origin"]} for p in read(run, "proposals.toml")["proposal"]]
            (run / "IMPORT-REPORT.md").write_bytes((
                _opf_import._render_report_md(inv["inventory_digest"], inv["fragment"], norm, run.name)
                + _opf_import._render_ingest_review_md(_opf_import._ingest_render_model(
                    run.name, b["crosswalk"], b["migrate"]))).encode("utf-8"))

        def restamp(b):
            """Recompute the embedded worksheet's own digest after a row edit (validate_worksheet stays green)."""
            payload = {k: v for k, v in b["worksheet"].items() if k != "worksheet_digest"}
            b["worksheet"]["worksheet_digest"] = "sha256:" + _opf_import._sha256_hex(_worksheet_bytes(payload))

        # (a) keep-only / move-only / migrate-only / zero-result-migrate round-trips
        scenarios = [("keep", {"legacy/a.md": "source\n"}, "keep", None),
                     ("move", {"legacy/a.md": "source\n"}, "move", None),
                     ("migrate", {"legacy/a.md": "- [ ] one\n- [x] two\n"}, "migrate", "github-tasklist"),
                     ("zero", {"legacy/a.md": "just prose\nno tasks here\n"}, "migrate", "github-tasklist")]
        for tag, files_map, dispo, kind in scenarios:
            files = list(files_map)
            with fixture(files_map) as (root, machine):
                ws = triage(root, files, dispo)
                opts = options(ws, importer_kind=kind) if dispo == "migrate" else (
                    options(ws) if dispo == "move" else empty)
                run = staged(root, machine, ws, opts, files)
                if run is None:
                    continue
                check("bundle-present-" + tag, (run / "ingest-review.toml").exists())
                bundle = load_bundle(root, run)
                check("bundle-loads-" + tag, bundle is not None
                      and bundle["format"] == _opf_import.INGEST_REVIEW_FORMAT
                      and bundle["run_id"] == run.name)
                if bundle is None:
                    continue
                check("bundle-worksheet-" + tag,
                      bundle["worksheet"]["worksheet_digest"] == ws["worksheet_digest"]
                      and len(bundle["worksheet"]["row"]) == len(files))
                check("bundle-include-" + tag,
                      bundle["include_declared"] is True and bundle["include"] == files)
                check("bundle-options-" + tag,
                      bundle["options"]["format"] == OPTIONS_FORMAT
                      and len(bundle["options"]["option"]) == len(opts["option"]))
                cw = {r["source_path"]: r for r in bundle["crosswalk"]}
                check("bundle-crosswalk-" + tag,
                      set(cw) == set(files)
                      and all(cw[f]["disposition"] == dispo for f in files)
                      and all(cw[f]["resolved_source_path"] == f for f in files))
                b = bundle["binding"]
                rep = read(run, "report.toml")
                check("bundle-binding-" + tag,
                      b["plan_digest"] == rep["plan_digest"]
                      and b["inventory_digest"] == rep["inventory_digest"]
                      and b["ingest_actions_digest"] == dig(run, "ingest-actions.toml")
                      and b["candidates_draft_digest"] == dig(run, "candidates_draft.toml")
                      and b["proposals_digest"] == dig(run, "proposals.toml")
                      and b["inventory_toml_digest"] == dig(run, "inventory.toml"))
                if dispo == "migrate":
                    mig = {m["source_path"]: m for m in bundle["migrate"]}
                    check("bundle-migrate-" + tag,
                          set(mig) == set(files) and all(mig[f]["importer_kind"] == kind for f in files))
                    # a real staged migrate run passes the repaired staged-run gate (item 7)
                    check("migrate-gate-proposals-clean-" + tag,
                          _chk.check_staged_run(str(run))["proposals-artifact"][0] is True)
                    if tag == "zero":
                        check("bundle-zero-result-preserved",
                              all((mig.get(f) or {}).get("candidate_count") == 0
                                  and (mig.get(f) or {}).get("proposal_count") == 0
                                  for f in files))
                else:
                    check("bundle-no-migrate-" + tag, bundle["migrate"] == [])
                rev = _opf_import.review_import(root, run.name, actor="tester", decisions=[], now=now)
                check("review-refused-" + tag, rev.verdict == CANNOT_EVALUATE)

        # (b) a MIXED keep+move+migrate run: crosswalk carries every row's disposition; migrate list carries
        # ONLY the migrate row (importer selection is per migrate row).
        mixed_map = {"legacy/keep.md": "keepme\n", "legacy/move.md": "moveme\n",
                     "legacy/mig.md": "- [ ] t\n"}
        by = {"legacy/keep.md": "keep", "legacy/move.md": "move", "legacy/mig.md": "migrate"}
        files = list(mixed_map)
        with fixture(mixed_map) as (root, machine):
            found = detect(root, include=files)
            ws = stamp([dict(r, disposition=by[r["source_path"]], note="") for r in found.rows])
            opt_rows = []
            for r in ws["row"]:
                if by[r["source_path"]] == "migrate":
                    opt_rows.append({"scope": r["scope"], "source_path": r["source_path"],
                                     "importer_kind": "github-tasklist"})
                elif by[r["source_path"]] == "move":
                    opt_rows.append({"scope": r["scope"], "source_path": r["source_path"]})
            opts = {"format": OPTIONS_FORMAT, "schema": SCHEMA, "option": opt_rows}
            run = staged(root, machine, ws, opts, files)
            if run is not None:
                bundle = load_bundle(root, run)
                if bundle is not None:
                    check("mixed-crosswalk",
                          {r["source_path"]: r["disposition"] for r in bundle["crosswalk"]} == by)
                    check("mixed-migrate-only-migrate-row",
                          [m["source_path"] for m in bundle["migrate"]] == ["legacy/mig.md"])
                    check("mixed-review-refused",
                          _opf_import.review_import(root, run.name, actor="t", decisions=[],
                                                    now=now).verdict == CANNOT_EVALUATE)

        # (c) the bounded structural reader: a genuinely ABSENT bundle -> None (a non-4a run is
        # distinguishable), a present-but-MALFORMED bundle -> CANNOT-EVALUATE (fail-closed); and the bundle
        # ALONE marks a run ingest (recognized at apply/review).
        with fixture({"legacy/a.md": "source\n"}) as (root, machine):
            ws = triage(root, ["legacy/a.md"], "keep")
            run = staged(root, machine, ws, empty, ["legacy/a.md"])
            if run is not None:
                run_rel = "{}/{}".format(_opf_import.IMPORTS_REL, run.name)
                (run / "ingest-review.toml").write_text('format = "wrong"\nschema = 1\n', encoding="utf-8")
                fd = open_fd(root)
                try:
                    raised = False
                    try:
                        _opf_import._load_staged_ingest_for_review(fd, run_rel)
                    except _opf_import._StageError:
                        raised = True
                    check("reader-malformed-refuses", raised)
                finally:
                    os.close(fd)
                (run / "ingest-review.toml").unlink()
                fd = open_fd(root)
                try:
                    check("reader-absent-none",
                          _opf_import._load_staged_ingest_for_review(fd, run_rel) is None)
                finally:
                    os.close(fd)
        with fixture({"legacy/a.md": "source\n"}) as (root, machine):
            bogus = "imp-20260909T120000Z-000000000000beef"
            rd = machine.parent / "imports" / bogus
            rd.mkdir(parents=True)
            (rd / _opf_import.INGEST_REVIEW_NAME).write_text("x = 1\n", encoding="utf-8")
            fd = open_fd(root)
            try:
                check("marker-recognizes-bundle-only",
                      _opf_import._ingest_run_marker(
                          fd, "{}/{}".format(_opf_import.IMPORTS_REL, bogus))
                      == _opf_import.INGEST_REVIEW_NAME)
            finally:
                os.close(fd)

        # (d) F2 reader ENTRY-shape: a structurally-valid bundle whose worksheet.row (resp. options.option) is
        # a list whose ENTRIES are NOT tables is CANNOT-EVALUATE. Pre-fix the reader asserted only that row /
        # option were lists, so a `[42]` entry passed the structural reader and a later per-row consumer would
        # crash on it; each leg FAILS without its `all(isinstance(...))` entry guard (change-carries-check).
        import copy
        with fixture({"legacy/a.md": "source\n"}) as (root, machine):
            ws = triage(root, ["legacy/a.md"], "keep")
            run = staged(root, machine, ws, empty, ["legacy/a.md"])
            if run is not None:
                run_rel = "{}/{}".format(_opf_import.IMPORTS_REL, run.name)
                good = load_bundle(root, run)
                for owner, field, name in (("worksheet", "row", "reader-row-entry-not-table"),
                                           ("options", "option", "reader-option-entry-not-table")):
                    bad = copy.deepcopy(good)
                    bad[owner][field] = [42]
                    (run / _opf_import.INGEST_REVIEW_NAME).write_bytes(
                        _opf_import._emit_bytes(bad, _opf_import.INGEST_REVIEW_NAME))
                    fd = open_fd(root)
                    try:
                        raised = False
                        try:
                            _opf_import._load_staged_ingest_for_review(fd, run_rel)
                        except _opf_import._StageError as exc:
                            raised = exc.verdict == CANNOT_EVALUATE
                        check(name, raised)
                    finally:
                        os.close(fd)

        # (e) F1 bundle-writer INVENTORY drift: the writer re-checks BOTH staged source records (run.toml AND
        # inventory.toml) against the frozen crosswalk BEFORE it finalizes, so a frozen bundle can never
        # describe a drifted snapshot. A staged inventory.toml whose source identity drifts from run.toml /
        # expected is refused CANNOT-EVALUATE naming inventory.toml. Pre-fix only run.toml was re-checked, so
        # an inventory.toml drift slipped through; this leg FAILS without the inventory.toml arm of the
        # step-3 loop (change-carries-check).
        with fixture({"legacy/a.md": "source\n"}) as (root, machine):
            ws = triage(root, ["legacy/a.md"], "keep")
            run = staged(root, machine, ws, empty, ["legacy/a.md"])
            if run is not None:
                run_rel = "{}/{}".format(_opf_import.IMPORTS_REL, run.name)
                bundle = load_bundle(root, run)
                run_src = read(run, "run.toml")["source"]
                expected = {s["path"]: ("sha256:" + s["sha256"], s["size"]) for s in run_src}
                review_inputs = {
                    "include": bundle["include"] if bundle["include_declared"] else None,
                    "worksheet": bundle["worksheet"], "options": bundle["options"],
                    "crosswalk": bundle["crosswalk"], "migrate": bundle["migrate"],
                    "expected": expected}
                inv = read(run, "inventory.toml")
                inv["source"][0]["sha256"] = ("1" * 64 if inv["source"][0]["sha256"] != "1" * 64
                                              else "0" * 64)
                (run / "inventory.toml").write_bytes(_opf_import._emit_bytes(inv, "inventory.toml"))
                raised = False
                try:
                    _opf_import._write_ingest_review_bundle(root, run_rel, run.name, review_inputs)
                except _opf_import._StageError as exc:
                    raised = exc.verdict == CANNOT_EVALUATE and "inventory.toml" in exc.message
                check("bundle-inventory-drift-refused", raised)

        # (f) cx4 DUPLICATE source path: a staged inventory.toml whose [[source]] list repeats a path (a
        # DRIFTED sha256 first, the CORRECT row last) is refused CANNOT-EVALUATE. Last-wins would reduce the
        # list to the correct final identity and MASK the drifted occurrence, so the writer rejects a source
        # list whose distinct-path count differs from its row count. This leg FAILS without the
        # `len(staged_ident) != len(srcs)` guard: with it removed, last-wins == expected and the drift check
        # passes, so the bundle would finalize over a drifted duplicate (F-MIG-PR4A-DUP-PATH-MASK).
        with fixture({"legacy/a.md": "source\n"}) as (root, machine):
            ws = triage(root, ["legacy/a.md"], "keep")
            run = staged(root, machine, ws, empty, ["legacy/a.md"])
            if run is not None:
                run_rel = "{}/{}".format(_opf_import.IMPORTS_REL, run.name)
                bundle = load_bundle(root, run)
                run_src = read(run, "run.toml")["source"]
                expected = {s["path"]: ("sha256:" + s["sha256"], s["size"]) for s in run_src}
                review_inputs = {
                    "include": bundle["include"] if bundle["include_declared"] else None,
                    "worksheet": bundle["worksheet"], "options": bundle["options"],
                    "crosswalk": bundle["crosswalk"], "migrate": bundle["migrate"],
                    "expected": expected}
                inv = read(run, "inventory.toml")
                orig_row = inv["source"][0]
                drifted = dict(orig_row, sha256=("1" * 64 if orig_row["sha256"] != "1" * 64 else "0" * 64))
                inv["source"] = [drifted, dict(orig_row)]  # dup path: drifted first, correct (last-wins) last
                (run / "inventory.toml").write_bytes(_opf_import._emit_bytes(inv, "inventory.toml"))
                raised = False
                try:
                    _opf_import._write_ingest_review_bundle(root, run_rel, run.name, review_inputs)
                except _opf_import._StageError as exc:
                    raised = (exc.verdict == CANNOT_EVALUATE and "duplicate source path" in exc.message
                              and "inventory.toml" in exc.message)
                check("bundle-inventory-dup-path-refused", raised)

        # (g) codex A SPLIT-READ (behavioural): inventory.toml's identities are parsed from the SAME bytes
        # bound in step 2, never a SECOND read in step 3, so no second inventory read exists to race the
        # binding read. This pins that property behaviourally: over a valid staged run, record every rel
        # _read_toml is asked for during _write_ingest_review_bundle and assert inventory.toml is NEVER among
        # them (only run.toml is re-read in step 3). This leg FAILS if step 3 is reverted to re-read
        # inventory.toml via _read_toml (the split-read the same-bytes fix closes).
        with fixture({"legacy/a.md": "source\n"}) as (root, machine):
            ws = triage(root, ["legacy/a.md"], "keep")
            run = staged(root, machine, ws, empty, ["legacy/a.md"])
            if run is not None:
                run_rel = "{}/{}".format(_opf_import.IMPORTS_REL, run.name)
                bundle = load_bundle(root, run)
                run_src = read(run, "run.toml")["source"]
                expected = {s["path"]: ("sha256:" + s["sha256"], s["size"]) for s in run_src}
                review_inputs = {
                    "include": bundle["include"] if bundle["include_declared"] else None,
                    "worksheet": bundle["worksheet"], "options": bundle["options"],
                    "crosswalk": bundle["crosswalk"], "migrate": bundle["migrate"],
                    "expected": expected}
                (run / _opf_import.INGEST_REVIEW_NAME).unlink()  # clean re-create for the instrumented write
                reads = []
                _real_read_toml = _opf_import._read_toml
                def _recording_read_toml(fd, rel, *a, **k):
                    reads.append(rel)
                    return _real_read_toml(fd, rel, *a, **k)
                _opf_import._read_toml = _recording_read_toml
                try:
                    _opf_import._write_ingest_review_bundle(root, run_rel, run.name, review_inputs)
                finally:
                    _opf_import._read_toml = _real_read_toml
                check("bundle-inventory-single-read",
                      (run_rel + "/inventory.toml") not in reads and (run_rel + "/run.toml") in reads)

        # (h0) F-MIG-PR4A-INT-VALUEERROR: a staged inventory.toml carrying an integer literal OVER CPython's
        # 4300-digit string-conversion ceiling makes the step-3 tomllib.loads raise a BARE ValueError (NOT a
        # TOMLDecodeError). The parse converts the whole ValueError family to CANNOT-EVALUATE, matching
        # _read_toml's own parse-locus handling, so the writer fails closed to verdict 2 rather than RAISING.
        # This leg FAILS (a bare ValueError escapes) if step 3 catches only (UnicodeDecodeError,
        # tomllib.TOMLDecodeError): TOMLDecodeError is a ValueError subclass but the over-long-integer error
        # is a bare ValueError, so the narrower tuple lets it through.
        with fixture({"legacy/a.md": "source\n"}) as (root, machine):
            ws = triage(root, ["legacy/a.md"], "keep")
            run = staged(root, machine, ws, empty, ["legacy/a.md"])
            if run is not None:
                run_rel = "{}/{}".format(_opf_import.IMPORTS_REL, run.name)
                bundle = load_bundle(root, run)
                run_src = read(run, "run.toml")["source"]
                expected = {s["path"]: ("sha256:" + s["sha256"], s["size"]) for s in run_src}
                review_inputs = {
                    "include": bundle["include"] if bundle["include_declared"] else None,
                    "worksheet": bundle["worksheet"], "options": bundle["options"],
                    "crosswalk": bundle["crosswalk"], "migrate": bundle["migrate"],
                    "expected": expected}
                # Append the over-long integer as RAW bytes: the canonical emitter would itself hit the
                # int->str ceiling, so it is written straight into the staged bytes step 2 binds and step 3
                # parses. tomllib then raises a bare ValueError on the 5000-digit literal (> the 4300 ceiling).
                raw = (run / "inventory.toml").read_bytes()
                (run / "inventory.toml").write_bytes(raw + b"\nover_long = " + b"9" * 5000 + b"\n")
                verdict = None
                raised_bare = False
                try:
                    _opf_import._write_ingest_review_bundle(root, run_rel, run.name, review_inputs)
                except _opf_import._StageError as exc:
                    verdict = exc.verdict
                except ValueError:
                    raised_bare = True  # the pre-fix regression: the bare ValueError escaped the writer
                check("bundle-inventory-hugeint-fail-closed", verdict == CANNOT_EVALUATE and not raised_bare)

        # (h0b) F-TOML-BARE-VALUEERROR-CLASS: the same step-3 parse must also convert the class's
        # RecursionError member (a 1200-deep nested array; a RuntimeError, not a ValueError) to
        # CANNOT-EVALUATE. The recursion limit is pinned to the CPython default 1000 (test-hermeticity).
        _h0b_prev_reclimit = sys.getrecursionlimit()
        sys.setrecursionlimit(1000)
        try:
            with fixture({"legacy/a.md": "source\n"}) as (root, machine):
                ws = triage(root, ["legacy/a.md"], "keep")
                run = staged(root, machine, ws, empty, ["legacy/a.md"])
                if run is not None:
                    run_rel = "{}/{}".format(_opf_import.IMPORTS_REL, run.name)
                    bundle = load_bundle(root, run)
                    run_src = read(run, "run.toml")["source"]
                    expected = {s["path"]: ("sha256:" + s["sha256"], s["size"]) for s in run_src}
                    review_inputs = {
                        "include": bundle["include"] if bundle["include_declared"] else None,
                        "worksheet": bundle["worksheet"], "options": bundle["options"],
                        "crosswalk": bundle["crosswalk"], "migrate": bundle["migrate"],
                        "expected": expected}
                    # Append the over-long integer as RAW bytes: the canonical emitter would itself hit the
                    # int->str ceiling, so it is written straight into the staged bytes step 2 binds and step 3
                    # parses. tomllib then raises a bare ValueError on the 5000-digit literal (> the 4300 ceiling).
                    raw = (run / "inventory.toml").read_bytes()
                    (run / "inventory.toml").write_bytes(raw + b"\ndeep = " + b"[" * 1200 + b"]" * 1200 + b"\n")
                    verdict = None
                    raised_bare = False
                    try:
                        _opf_import._write_ingest_review_bundle(root, run_rel, run.name, review_inputs)
                    except _opf_import._StageError as exc:
                        verdict = exc.verdict
                    except RecursionError:
                        raised_bare = True  # the RecursionError escaped the writer
                    check("bundle-inventory-deep-nesting-fail-closed", verdict == CANNOT_EVALUATE and not raised_bare)
        finally:
            sys.setrecursionlimit(_h0b_prev_reclimit)

        # (h) MIG-PR4b: the read-only SEMANTIC gate over the frozen bundle. Over a COHERENT mixed
        # keep+move+migrate ingest run all five ingest checks PASS; each new check then FINDINGs on its own
        # targeted mutation (change-carries-check). The bundle is NOT digest-bound by any artefact (acyclic
        # graph), so a structurally-valid bundle rewrite exercises the correspondence checks in isolation,
        # while a staged-report tamper exercises the byte-reproduction check. Every mutation is restored so the
        # cases are independent.
        NAME = _opf_import.INGEST_REVIEW_NAME
        _pr4b_ids = ("ingest-run-structure", "ingest-source-binding", "ingest-disposition-totality",
                     "ingest-draft-loss-binding", "ingest-report-reproducibility", "ingest-artefact-completeness")
        pr4b_map = {"legacy/keep.md": "keepme\n", "legacy/move.md": "moveme\n",
                    "legacy/mig.md": "- [ ] one\n- [x] two\n"}
        pr4b_by = {"legacy/keep.md": "keep", "legacy/move.md": "move", "legacy/mig.md": "migrate"}
        pr4b_files = list(pr4b_map)
        with fixture(pr4b_map) as (root, machine):
            found = detect(root, include=pr4b_files)
            ws = stamp([dict(r, disposition=pr4b_by[r["source_path"]], note="") for r in found.rows])
            opt_rows = []
            for r in ws["row"]:
                if pr4b_by[r["source_path"]] == "migrate":
                    opt_rows.append({"scope": r["scope"], "source_path": r["source_path"],
                                     "importer_kind": "github-tasklist"})
                elif pr4b_by[r["source_path"]] == "move":
                    opt_rows.append({"scope": r["scope"], "source_path": r["source_path"]})
            opts = {"format": OPTIONS_FORMAT, "schema": SCHEMA, "option": opt_rows}
            run = staged(root, machine, ws, opts, pr4b_files)
            if run is not None:
                rundir = str(run)
                pristine = load_bundle(root, run)
                orig_bundle = (run / NAME).read_bytes()

                def write_bundle(b):
                    (run / NAME).write_bytes(_opf_import._emit_bytes(b, NAME))

                def restore_bundle():
                    (run / NAME).write_bytes(orig_bundle)

                def flip(name, check_id, mutate):
                    b = copy.deepcopy(pristine)
                    mutate(b)
                    write_bundle(b)
                    try:
                        res = _chk.check_staged_run(rundir)
                        check(name, res[check_id][0] is False)
                    finally:
                        restore_bundle()

                # (h0) all five PASS on the coherent run.
                clean_res = _chk.check_staged_run(rundir)
                for cid in _pr4b_ids:
                    check("pr4b-clean-" + cid, clean_res[cid][0] is True)

                # (h1) ingest-source-binding: a stale-but-well-shaped binding.inventory_toml_digest, and a
                # crosswalk disposition drifted from the worksheet.
                def _bad_inv_digest(b):
                    cur = b["binding"]["inventory_toml_digest"]
                    b["binding"]["inventory_toml_digest"] = "sha256:" + ("0" * 64 if cur != "sha256:"
                                                                         + "0" * 64 else "1" * 64)
                flip("pr4b-disc-source-binding-digest", "ingest-source-binding", _bad_inv_digest)

                def _drift_crosswalk_disp(b):
                    row = b["crosswalk"][0]
                    row["disposition"] = "move" if row["disposition"] != "move" else "keep"
                flip("pr4b-disc-source-binding-crosswalk-disp", "ingest-source-binding", _drift_crosswalk_disp)

                # (h2) ingest-disposition-totality: drop the migrate correspondence, and a duplicate migrate row
                # (duplicate rejection before dictionary construction).
                flip("pr4b-disc-disposition-totality-drop-migrate", "ingest-disposition-totality",
                     lambda b: b.__setitem__("migrate", []))
                flip("pr4b-disc-disposition-totality-dup-migrate", "ingest-disposition-totality",
                     lambda b: b["migrate"].append(copy.deepcopy(b["migrate"][0])))

                # (h3) ingest-draft-loss-binding: a stale binding.candidates_draft_digest, and a frozen count
                # that no longer matches the staged evidence.
                def _bad_draft_digest(b):
                    cur = b["binding"]["candidates_draft_digest"]
                    b["binding"]["candidates_draft_digest"] = "sha256:" + ("0" * 64 if cur != "sha256:"
                                                                           + "0" * 64 else "1" * 64)
                flip("pr4b-disc-draft-loss-digest", "ingest-draft-loss-binding", _bad_draft_digest)
                flip("pr4b-disc-draft-loss-count", "ingest-draft-loss-binding",
                     lambda b: b["migrate"][0].__setitem__("candidate_count",
                                                           b["migrate"][0]["candidate_count"] + 1))

                # (h4) ingest-report-reproducibility: tamper the staged IMPORT-REPORT.md (bundle untouched).
                orig_report = (run / "IMPORT-REPORT.md").read_bytes()
                (run / "IMPORT-REPORT.md").write_bytes(orig_report + b"\n<!-- tampered -->\n")
                check("pr4b-disc-report-repro",
                      _chk.check_staged_run(rundir)["ingest-report-reproducibility"][0] is False)
                (run / "IMPORT-REPORT.md").write_bytes(orig_report)

                # (h5) ingest-run-structure: a present-but-malformed bundle, and an ingest-marked run whose
                # review bundle is absent (partial run), both a located FINDING at the gate (never a raise).
                (run / NAME).write_text('format = "wrong"\nschema = 1\n', encoding="utf-8")
                check("pr4b-disc-structure-malformed",
                      _chk.check_staged_run(rundir)["ingest-run-structure"][0] is False)
                restore_bundle()
                (run / NAME).unlink()
                check("pr4b-disc-structure-partial",
                      _chk.check_staged_run(rundir)["ingest-run-structure"][0] is False)
                restore_bundle()

                # (h6) MIG-PR4b round-2 semantic-correspondence discriminators. Each new gate guard PASSES on
                # the coherent run above and FINDINGs on its own targeted mutation, and flips (passes) if only
                # that guard is reverted. Bundle-only mutations use flip(); mutations that touch a STAGED
                # artefact refresh the matching binding digest so the digest arm stays green and only the
                # target correspondence guard fires.

                # FIX 1a: a stale-but-well-shaped binding.ingest_actions_digest -> ingest-source-binding.
                def _bad_actions_digest(b):
                    cur = b["binding"]["ingest_actions_digest"]
                    b["binding"]["ingest_actions_digest"] = "sha256:" + ("0" * 64 if cur != "sha256:"
                                                                         + "0" * 64 else "1" * 64)
                flip("bundle-action-digest-recompute", "ingest-source-binding", _bad_actions_digest)

                # FIX 2: a worksheet row whose frozen sha256 diverges from the staged source identity, kept
                # internally consistent (worksheet_digest recomputed) so validate_worksheet still passes and
                # only the FIX 2 identity guard fires.
                def _drift_ws_identity(b):
                    row = b["worksheet"]["row"][0]
                    cur = row["sha256"]
                    row["sha256"] = "sha256:" + ("0" * 64 if cur != "sha256:" + "0" * 64 else "1" * 64)
                    payload = {k: v for k, v in b["worksheet"].items() if k != "worksheet_digest"}
                    b["worksheet"]["worksheet_digest"] = "sha256:" + _opf_import._sha256_hex(
                        _worksheet_bytes(payload))
                flip("bundle-worksheet-identity-vs-source", "ingest-source-binding", _drift_ws_identity)

                # FIX 3a: drop the option for a move/migrate row so options no longer correspond to the
                # dispositions -> ingest-source-binding. (validate_options still passes: the remaining options
                # are well-shaped, so only the FIX 3a correspondence guard fires.)
                flip("bundle-options-vs-disposition", "ingest-source-binding",
                     lambda b: b["options"]["option"].pop())

                # FIX 3b: a migrate row whose importer_kind is not a registered importer -> ingest-source-binding.
                flip("bundle-importer-kind-registered", "ingest-source-binding",
                     lambda b: b["migrate"][0].__setitem__("importer_kind", "no-such-importer"))

                # FIX 1b: a keep worksheet row whose staged ingest-actions action is kind=move to an
                # unauthorized dest -> ingest-disposition-totality. The staged ingest-actions.toml is mutated
                # and binding.ingest_actions_digest refreshed so ingest-source-binding stays green and only the
                # action-kind guard fires.
                acts = read(run, "ingest-actions.toml")
                orig_acts = (run / "ingest-actions.toml").read_bytes()
                for a in acts["action"]:
                    if a["kind"] == "keep":
                        a["kind"] = "move"
                        a["dest_path"] = ".archive/unauthorized.md"
                        break
                (run / "ingest-actions.toml").write_bytes(
                    _opf_import._emit_bytes(acts, "ingest-actions.toml"))
                b = copy.deepcopy(pristine)
                b["binding"]["ingest_actions_digest"] = dig(run, "ingest-actions.toml")
                write_bundle(b)
                check("bundle-action-kind-vs-disposition",
                      _chk.check_staged_run(rundir)["ingest-disposition-totality"][0] is False)
                (run / "ingest-actions.toml").write_bytes(orig_acts)
                restore_bundle()

                # FIX 4a: a staged candidate whose record.status is not a legal status -> ingest-draft-loss-
                # binding (through the real record validator). binding.candidates_draft_digest refreshed so the
                # digest arm stays green and only the envelope-validity guard fires.
                cds = read(run, "candidates_draft.toml")
                orig_cds = (run / "candidates_draft.toml").read_bytes()
                cds["candidate"][0]["record"]["status"] = "not-a-status"
                (run / "candidates_draft.toml").write_bytes(
                    _opf_import._emit_bytes(cds, "candidates_draft.toml"))
                b = copy.deepcopy(pristine)
                b["binding"]["candidates_draft_digest"] = dig(run, "candidates_draft.toml")
                write_bundle(b)
                check("bundle-candidate-envelope-valid",
                      _chk.check_staged_run(rundir)["ingest-draft-loss-binding"][0] is False)
                (run / "candidates_draft.toml").write_bytes(orig_cds)
                restore_bundle()

                # FIX 4b: a candidate whose source_path names no migrate row (a VALID envelope under a ghost
                # source) -> ingest-draft-loss-binding (the envelope-validity guard passes, only the no-extra-
                # source guard fires). binding.candidates_draft_digest refreshed so the digest arm stays green.
                cds = read(run, "candidates_draft.toml")
                orig_cds = (run / "candidates_draft.toml").read_bytes()
                ghost = copy.deepcopy(cds["candidate"][0])
                ghost["source_path"] = "legacy/ghost.md"
                cds["candidate"].append(ghost)
                (run / "candidates_draft.toml").write_bytes(
                    _opf_import._emit_bytes(cds, "candidates_draft.toml"))
                b = copy.deepcopy(pristine)
                b["binding"]["candidates_draft_digest"] = dig(run, "candidates_draft.toml")
                write_bundle(b)
                check("bundle-candidate-no-extra-source",
                      _chk.check_staged_run(rundir)["ingest-draft-loss-binding"][0] is False)
                (run / "candidates_draft.toml").write_bytes(orig_cds)
                restore_bundle()

                # (h8) MIG-PR4b RE-DERIVATION discriminators: one per re-derived artefact / escape vector
                # (E1-E8), each PASSING on the coherent run above and FINDING on its single targeted mutation.
                # These reproduce the codex/gemini/claude round-1/2 escapes the per-vector gate passed; the
                # re-derive-and-equal mechanism now catches each. Bundle-only mutations use flip(); mutations
                # that touch a STAGED artefact refresh the matching binding digest so only the target
                # correspondence arm fires (change-carries-check).
                other_kind = sorted(k for k in _opf_importers.IMPORTER_KINDS
                                    if k != "github-tasklist")[0]

                # E1 (importer_kind bound across options / migrate scaffold / candidate stamp). (a) the frozen
                # migrate row's kind drifts from the OPTIONS kind; (b) the OPTIONS kind drifts from the migrate
                # row; both -> disposition-totality (scaffold re-derive binds migrate.importer_kind to options).
                # Each uses a REGISTERED kind so validate_options / the registry arm still pass and only the
                # scaffold equality fires.
                flip("pr4b-disc-e1-migrate-kind-vs-options", "ingest-disposition-totality",
                     lambda b: b["migrate"][0].__setitem__("importer_kind", other_kind))

                def _drift_option_kind(b):
                    for o in b["options"]["option"]:
                        if "importer_kind" in o:
                            o["importer_kind"] = other_kind
                            break
                flip("pr4b-disc-e1-options-kind-vs-migrate", "ingest-disposition-totality", _drift_option_kind)

                # E1/E8 (candidate importer_kind STAMP): the staged candidate's importer_kind stamp drifts from
                # its migrate row -> ingest-draft-loss-binding. Touches candidates_draft.toml; digest refreshed.
                cds = read(run, "candidates_draft.toml")
                orig_cds = (run / "candidates_draft.toml").read_bytes()
                cds["candidate"][0]["importer_kind"] = other_kind
                (run / "candidates_draft.toml").write_bytes(
                    _opf_import._emit_bytes(cds, "candidates_draft.toml"))
                b = copy.deepcopy(pristine)
                b["binding"]["candidates_draft_digest"] = dig(run, "candidates_draft.toml")
                write_bundle(b)
                check("pr4b-disc-e1-candidate-kind-stamp",
                      _chk.check_staged_run(rundir)["ingest-draft-loss-binding"][0] is False)
                (run / "candidates_draft.toml").write_bytes(orig_cds)
                restore_bundle()

                # E8 (candidate NO-SKIP, the real escape the prior gate missed): an EXTRA malformed candidate
                # (non-string source_path) is APPENDED beside the intact real candidates, so the per-migrate
                # COUNTS still match and the ghost-source set is unperturbed. The prior gate SILENTLY DROPPED it
                # (`isinstance(c.get("source_path"), str)`), so the run passed; the re-derivation gate iterates
                # EVERY candidate fail-closed and FINDINGs the malformed one -> draft-loss. binding digest
                # refreshed so only the candidate-iteration guard fires.
                cds = read(run, "candidates_draft.toml")
                orig_cds = (run / "candidates_draft.toml").read_bytes()
                _ghost = copy.deepcopy(cds["candidate"][0])
                _ghost["source_path"] = 123
                cds["candidate"].append(_ghost)
                (run / "candidates_draft.toml").write_bytes(
                    _opf_import._emit_bytes(cds, "candidates_draft.toml"))
                b = copy.deepcopy(pristine)
                b["binding"]["candidates_draft_digest"] = dig(run, "candidates_draft.toml")
                write_bundle(b)
                check("pr4b-disc-e8-candidate-noskip-malformed",
                      _chk.check_staged_run(rundir)["ingest-draft-loss-binding"][0] is False)
                (run / "candidates_draft.toml").write_bytes(orig_cds)
                restore_bundle()

                # E2 (candidate closed keyset): a candidate carrying an EXTRA key -> draft-loss FINDING.
                cds = read(run, "candidates_draft.toml")
                orig_cds = (run / "candidates_draft.toml").read_bytes()
                cds["candidate"][0]["rogue"] = "x"
                (run / "candidates_draft.toml").write_bytes(
                    _opf_import._emit_bytes(cds, "candidates_draft.toml"))
                b = copy.deepcopy(pristine)
                b["binding"]["candidates_draft_digest"] = dig(run, "candidates_draft.toml")
                write_bundle(b)
                check("pr4b-disc-e2-candidate-extra-key",
                      _chk.check_staged_run(rundir)["ingest-draft-loss-binding"][0] is False)
                (run / "candidates_draft.toml").write_bytes(orig_cds)
                restore_bundle()

                # E4 (inventory identities vs preserved bytes): the frozen inventory.toml source sha256 drifts
                # from the PRESERVED sources/<sha> bytes, kept self-consistent (its own inventory_digest and the
                # report + bundle bindings all refreshed) so ONLY the _build_inventory re-derive fires ->
                # ingest-source-binding.
                inv = read(run, "inventory.toml")
                orig_inv = (run / "inventory.toml").read_bytes()
                orig_report = (run / "report.toml").read_bytes()
                _old_sha = inv["source"][0]["sha256"]
                inv["source"][0]["sha256"] = ("1" if _old_sha[0] != "1" else "0") + _old_sha[1:]
                _inv_payload = {k: v for k, v in inv.items() if k != "inventory_digest"}
                _new_inv_digest = "sha256:" + _opf_import._sha256_hex(
                    _opf_import._emit_bytes(_inv_payload, "inventory"))
                inv["inventory_digest"] = _new_inv_digest
                (run / "inventory.toml").write_bytes(_opf_import._emit_bytes(inv, "inventory.toml"))
                rep = read(run, "report.toml")
                rep["inventory_digest"] = _new_inv_digest
                (run / "report.toml").write_bytes(_opf_import._emit_bytes(rep, "report.toml"))
                b = copy.deepcopy(pristine)
                b["binding"]["inventory_digest"] = _new_inv_digest
                b["binding"]["inventory_toml_digest"] = dig(run, "inventory.toml")
                write_bundle(b)
                check("pr4b-disc-e4-inventory-vs-preserved-bytes",
                      _chk.check_staged_run(rundir)["ingest-source-binding"][0] is False)
                (run / "inventory.toml").write_bytes(orig_inv)
                (run / "report.toml").write_bytes(orig_report)
                restore_bundle()

                # E5 (keep action RE-DERIVE): the staged keep action's unmanaged_path drifts from the shared
                # keep_unmanaged spelling -> disposition-totality. Touches ingest-actions.toml; digest refreshed.
                acts = read(run, "ingest-actions.toml")
                orig_acts = (run / "ingest-actions.toml").read_bytes()
                for a in acts["action"]:
                    if a["kind"] == "keep":
                        a["unmanaged_path"] = "legacy/forged-exemption.md"
                        break
                (run / "ingest-actions.toml").write_bytes(
                    _opf_import._emit_bytes(acts, "ingest-actions.toml"))
                b = copy.deepcopy(pristine)
                b["binding"]["ingest_actions_digest"] = dig(run, "ingest-actions.toml")
                write_bundle(b)
                check("pr4b-disc-e5-keep-unmanaged-path",
                      _chk.check_staged_run(rundir)["ingest-disposition-totality"][0] is False)
                (run / "ingest-actions.toml").write_bytes(orig_acts)
                restore_bundle()

                # E5 (move action RE-DERIVE incl identity): the staged move action's sha256 drifts from the
                # worksheet row identity -> disposition-totality. Touches ingest-actions.toml; digest refreshed.
                acts = read(run, "ingest-actions.toml")
                orig_acts = (run / "ingest-actions.toml").read_bytes()
                for a in acts["action"]:
                    if a["kind"] == "move":
                        _os = a["sha256"]
                        a["sha256"] = "sha256:" + ("0" * 64 if _os != "sha256:" + "0" * 64 else "1" * 64)
                        break
                (run / "ingest-actions.toml").write_bytes(
                    _opf_import._emit_bytes(acts, "ingest-actions.toml"))
                b = copy.deepcopy(pristine)
                b["binding"]["ingest_actions_digest"] = dig(run, "ingest-actions.toml")
                write_bundle(b)
                check("pr4b-disc-e5-move-action-identity",
                      _chk.check_staged_run(rundir)["ingest-disposition-totality"][0] is False)
                (run / "ingest-actions.toml").write_bytes(orig_acts)
                restore_bundle()

                # E6 (migrate resolved vs crosswalk base): the frozen migrate row's resolved_source_path drifts
                # from the base-consistent re-derivation -> disposition-totality (scaffold re-derive).
                flip("pr4b-disc-e6-migrate-resolved", "ingest-disposition-totality",
                     lambda b: b["migrate"][0].__setitem__("resolved_source_path", "legacy/forged-resolved.md"))

                # E7 (document envelopes): the staged ingest-actions.toml run_id no longer names this run dir ->
                # disposition-totality envelope FINDING. Touches ingest-actions.toml; digest refreshed.
                acts = read(run, "ingest-actions.toml")
                orig_acts = (run / "ingest-actions.toml").read_bytes()
                acts["run_id"] = "imp-20260101T000000Z-0000000000000000"
                (run / "ingest-actions.toml").write_bytes(
                    _opf_import._emit_bytes(acts, "ingest-actions.toml"))
                b = copy.deepcopy(pristine)
                b["binding"]["ingest_actions_digest"] = dig(run, "ingest-actions.toml")
                write_bundle(b)
                check("pr4b-disc-e7-actions-envelope-run-id",
                      _chk.check_staged_run(rundir)["ingest-disposition-totality"][0] is False)
                (run / "ingest-actions.toml").write_bytes(orig_acts)
                restore_bundle()

                # E7 (candidates_draft envelope): the staged candidates_draft.toml run_id drifts -> draft-loss
                # envelope FINDING. Touches candidates_draft.toml; digest refreshed.
                cds = read(run, "candidates_draft.toml")
                orig_cds = (run / "candidates_draft.toml").read_bytes()
                cds["run_id"] = "imp-20260101T000000Z-0000000000000000"
                (run / "candidates_draft.toml").write_bytes(
                    _opf_import._emit_bytes(cds, "candidates_draft.toml"))
                b = copy.deepcopy(pristine)
                b["binding"]["candidates_draft_digest"] = dig(run, "candidates_draft.toml")
                write_bundle(b)
                check("pr4b-disc-e7-candidates-envelope-run-id",
                      _chk.check_staged_run(rundir)["ingest-draft-loss-binding"][0] is False)
                (run / "candidates_draft.toml").write_bytes(orig_cds)
                restore_bundle()

                # E8 (duplicate crosswalk key): a duplicated crosswalk (scope, source_path) row rejected before
                # the equality reduction -> ingest-source-binding. Bundle-only.
                flip("pr4b-disc-e8-crosswalk-dup-key", "ingest-source-binding",
                     lambda b: b["crosswalk"].append(copy.deepcopy(b["crosswalk"][0])))

                # (h9) MIG-PR4b ROUND-2 discriminators (R1-R10, G1, C2). Each reproduces a confirmed escape the
                # round-2 gate PASSED: the mutation is applied, then EVERY digest the gate recomputes is refreshed
                # and IMPORT-REPORT.md regenerated exactly as the planner would (coherent()), so only the target
                # guard can fire. Where two guards overlap by design, the located detail is asserted so removing
                # ONLY the target guard flips the discriminator (change-carries-check).
                def snapshot_run():
                    return {p.relative_to(run).as_posix(): p.read_bytes()
                            for p in run.rglob("*") if p.is_file() and not p.is_symlink()}

                def restore_run(snap):
                    for p in sorted(run.rglob("*"), key=lambda q: len(q.parts), reverse=True):
                        rel = p.relative_to(run).as_posix()
                        if p.is_symlink() or p.is_file():
                            if rel not in snap:
                                p.unlink()
                        elif p.is_dir() and not any(k.startswith(rel + "/") for k in snap):
                            p.rmdir()
                    for rel, data in snap.items():
                        (run / rel).parent.mkdir(parents=True, exist_ok=True)
                        (run / rel).write_bytes(data)

                def coherent(name, check_id, edit, detail=None):
                    snap = snapshot_run()
                    try:
                        b = copy.deepcopy(pristine)
                        edit(b)
                        rebind(run, b)
                        res = _chk.check_staged_run(rundir)[check_id]
                        check(name, res[0] is False and (detail is None or detail in res[1]))
                    finally:
                        restore_run(snap)

                def put(rel, model):
                    (run / rel).write_bytes(_opf_import._emit_bytes(model, rel))

                def edit_doc(rel, fn):
                    doc = read(run, rel)
                    fn(doc)
                    put(rel, doc)

                def first(rows, **match):
                    return next(r for r in rows if all(r.get(k) == v for k, v in match.items()))

                # the harness itself is sound: a no-op coherent() rewrite, through the SAME rebind() path every
                # mutation uses (digest refresh + report regeneration), leaves EVERY gate check PASSING (else
                # each discriminator below would pass vacuously on a harness-induced failure). Round-5: the probe
                # previously wrote the bundle without rebind(), so it never exercised that path; its own
                # discriminator shows a report-corrupting rebind is caught by the same probe.
                def noop_probe(rebinder):
                    snap = snapshot_run()
                    try:
                        rebinder(run, copy.deepcopy(pristine))
                        res = _chk.check_staged_run(rundir)
                        return all(res[cid][0] for cid in _pr4b_ids) and all(ok for ok, _d in res.values())
                    finally:
                        restore_run(snap)

                def corrupt_rebind(run_, b):
                    rebind(run_, b)
                    with open(run_ / "IMPORT-REPORT.md", "ab") as fh:
                        fh.write(b"forged trailing line\n")

                check("pr4b-harness-noop-clean", noop_probe(rebind))
                check("pr4b-disc-harness-noop-detects-corrupt-rebind", not noop_probe(corrupt_rebind))

                # R1 (ARTEFACT COMPLETENESS): plan.toml replaced by a non-plan, a legacy-fragment body forged, an
                # unexpected staged file, a registered artefact missing, and candidate/counters.toml unparseable.
                def _r1_plan(b):
                    (run / "plan.toml").write_text('nonsense = "not a plan"\n', encoding="utf-8")
                coherent("pr4b-disc-r1-plan-rederive", "ingest-artefact-completeness", _r1_plan, "plan.toml")
                coherent("pr4b-disc-r1-fragment-body", "ingest-artefact-completeness",
                         lambda b: edit_doc("fragments/legacy_fragment.index.toml",
                                            lambda d: d["record"][0].__setitem__("body", "forged")),
                         "legacy_fragment index does not equal")
                coherent("pr4b-disc-r1-unexpected-file", "ingest-artefact-completeness",
                         lambda b: (run / "rogue.txt").write_text("x\n", encoding="utf-8"),
                         "outside the ingest artefact registry")
                coherent("pr4b-disc-r1-missing-artefact", "ingest-artefact-completeness",
                         lambda b: (run / "sources" / first(read(run, "run.toml")["source"],
                                                           path="legacy/keep.md")["sha256"]).unlink(),
                         "missing registered artefact")
                coherent("pr4b-disc-r1-counters-unparseable", "ingest-artefact-completeness",
                         lambda b: (run / "candidate/counters.toml").write_bytes(b"not valid toml [\n"),
                         "counters.toml")
                coherent("pr4b-disc-r1-counters-lf-high-water", "ingest-artefact-completeness",
                         lambda b: edit_doc("candidate/counters.toml",
                                            lambda d: d["counters"].__setitem__("LF", d["counters"]["LF"] + 1)),
                         "LF high-water")

                # R2 (ENVELOPES): run.toml with a bool schema and a foreign run_id, and mappings.toml with a bool
                # schema, are refused at the registry envelope BEFORE any field is consumed.
                def _r2_run(b):
                    edit_doc("run.toml", lambda d: d.update(schema=True, run_id="wrong"))
                coherent("pr4b-disc-r2-run-envelope", "ingest-artefact-completeness", _r2_run,
                         "run.toml envelope invalid")
                coherent("pr4b-disc-r2-mappings-envelope", "ingest-artefact-completeness",
                         lambda b: edit_doc("mappings.toml", lambda d: d.__setitem__("schema", False)),
                         "mappings.toml envelope invalid")

                # R3 (MULTIPLICITY): a duplicated run.toml source row with inventory.toml rebuilt to match.
                def _r3_dup(b):
                    rd = read(run, "run.toml")
                    rd["source"].append(dict(rd["source"][0]))
                    put("run.toml", rd)
                    put("inventory.toml", _opf_import._build_inventory(rd["source"])[0])
                coherent("pr4b-disc-r3-duplicate-run-source", "ingest-source-binding", _r3_dup,
                         "duplicate source path")

                # R4 (PROPOSALS): an importer_proposal on the KEEP source, and a model_proposal on a ghost path
                # with an out-of-range span (plan_ingest emits zero model-origin proposals).
                def _r4_keep(b):
                    edit_doc("proposals.toml", lambda d: d["proposal"].append(
                        {"origin": _opf_import._IMPORTER_PROPOSAL_ORIGIN, "source_path": "legacy/keep.md",
                         "span": [0, 1], "suggested_state": "mapped", "note": ""}))
                coherent("pr4b-disc-r4-importer-proposal-on-keep", "ingest-draft-loss-binding", _r4_keep,
                         "is not a migrate row's resolved source")

                def _r4_model(b):
                    edit_doc("proposals.toml", lambda d: d["proposal"].append(
                        {"origin": _opf_import._MODEL_PROPOSAL_ORIGIN, "source_path": "ghost/outside.md",
                         "span": [-10, 99999], "suggested_state": "mapped", "note": ""}))
                coherent("pr4b-disc-r4-model-proposal", "ingest-draft-loss-binding", _r4_model,
                         "importer proposals only")

                # R5 (CANDIDATE REFS): a duplicated candidate (same draft_ref, candidate_count raised to match),
                # and an empty draft_ref, both through the SHARED candidate_ref_findings.
                def _r5_dup(b):
                    edit_doc("candidates_draft.toml",
                             lambda d: d["candidate"].append(copy.deepcopy(d["candidate"][0])))
                    first(b["migrate"], source_path="legacy/mig.md")["candidate_count"] += 1
                coherent("pr4b-disc-r5-duplicate-draft-ref", "ingest-draft-loss-binding", _r5_dup,
                         "draft-ref invariants")
                coherent("pr4b-disc-r5-empty-draft-ref", "ingest-draft-loss-binding",
                         lambda b: edit_doc("candidates_draft.toml",
                                            lambda d: d["candidate"][0].__setitem__("draft_ref", "")),
                         "draft-ref invariants")

                # R6 (ADMISSIBILITY): a move to itself (option + action dest re-derived to match), an importer_kind
                # on a move option, and a non-empty worksheet note on a move row (worksheet digest refreshed).
                def _r6_self(b):
                    first(b["options"]["option"], source_path="legacy/move.md")["dest_path"] = "legacy/move.md"
                    edit_doc("ingest-actions.toml", lambda d: first(
                        d["action"], source_path="legacy/move.md").__setitem__("dest_path", "legacy/move.md"))
                coherent("pr4b-disc-r6-move-to-self", "ingest-disposition-totality", _r6_self, "not admissible")
                coherent("pr4b-disc-r6-move-importer-kind", "ingest-disposition-totality",
                         lambda b: first(b["options"]["option"], source_path="legacy/move.md").__setitem__(
                             "importer_kind", "github-tasklist"), "not admissible")

                def _r6_note(b):
                    first(b["worksheet"]["row"], source_path="legacy/move.md")["note"] = "configure me"
                    payload = {k: v for k, v in b["worksheet"].items() if k != "worksheet_digest"}
                    b["worksheet"]["worksheet_digest"] = "sha256:" + _opf_import._sha256_hex(
                        _worksheet_bytes(payload))
                coherent("pr4b-disc-r6-move-note", "ingest-disposition-totality", _r6_note, "not admissible")

                # R9 (TYPE STRICTNESS): a float action size equal-by-value to the worksheet's int size.
                coherent("pr4b-disc-r9-float-size", "ingest-disposition-totality",
                         lambda b: edit_doc("ingest-actions.toml", lambda d: first(
                             d["action"], kind="keep").__setitem__("size", float(first(d["action"], kind="keep")[
                                 "size"]))), "does not equal the action re-derived")

                # R10 (WHOLE ROW): an extra key on a crosswalk row, and on a migrate row.
                coherent("pr4b-disc-r10-crosswalk-extra-key", "ingest-source-binding",
                         lambda b: b["crosswalk"][0].__setitem__("rogue", "ignored"),
                         "frozen crosswalk does not equal")
                coherent("pr4b-disc-r10-migrate-extra-key", "ingest-disposition-totality",
                         lambda b: b["migrate"][0].__setitem__("rogue", "ignored"), "closed scaffold")

                # G1 (INCLUDE): a mutated read-space the declared rows fall outside of, and an undeclared include
                # that nonetheless carries patterns.
                coherent("pr4b-disc-g1-include-read-space", "ingest-source-binding",
                         lambda b: b.__setitem__("include", ["nothing/*"]), "outside the frozen include set")
                coherent("pr4b-disc-g1-include-declared-flag", "ingest-source-binding",
                         lambda b: b.__setitem__("include_declared", False), "include_declared is false")

                # C2 (duplicate-resolved guard, isolated): a STORE-scope worksheet row naming the same file as the
                # declared keep row (an inline store re-anchors it to the SAME product path), with its crosswalk
                # row appended as the builder derives it, so the crosswalk equality and the set equalities all
                # hold and ONLY the multiplicity guard over resolved_source_path fires.
                # A-M3 made this collision statically unreachable: a STORE row lies under `.working/` and a
                # declared row never does, so no two scope-admissible rows share a resolved path. The twin is now
                # refused FIRST by the shared scope admissibility (asserted), and the multiplicity guard stays as
                # defence in depth, isolated here by stubbing ONLY admit_row_scope on the module the gate
                # imports, so removing the multiplicity guard still flips this discriminator.
                def _c2(b):
                    keep_row = first(b["worksheet"]["row"], source_path="legacy/keep.md")
                    twin = dict(keep_row, scope="store")
                    b["worksheet"]["row"].append(twin)
                    restamp(b)
                    b["crosswalk"].append(derive_crosswalk_row(twin, "legacy/keep.md"))
                coherent("pr4b-disc-c2-twin-scope-refused", "ingest-source-binding", _c2,
                         "not scope-admissible")
                import _opf_ingest as _gate_ing
                _real_scope = _gate_ing.admit_row_scope
                _gate_ing.admit_row_scope = lambda *a, **k: None
                try:
                    coherent("pr4b-disc-c2-duplicate-resolved", "ingest-source-binding", _c2,
                             "duplicate resolved_source_path")
                finally:
                    _gate_ing.admit_row_scope = _real_scope

                # A-m4 (PROPOSALS DOCUMENT RE-DERIVE-AND-EQUAL): the staged importer proposals REVERSED (each
                # row still valid, confined, counted, and the report regenerated to match) no longer equal the
                # producer's sorted construction -> draft-loss. Guarded against a vacuous pass: the fixture's
                # migrate source must yield at least two proposals whose order a reversal changes.
                check("pr4b-am4-fixture-two-proposals", len(read(run, "proposals.toml")["proposal"]) >= 2)
                coherent("pr4b-disc-am4-proposals-order", "ingest-draft-loss-binding",
                         lambda b: edit_doc("proposals.toml", lambda d: d["proposal"].reverse()),
                         "proposals.toml does not equal the document the planner constructs")

                # ROUND-4 discriminators (each mutation applied, every digest refreshed and IMPORT-REPORT.md
                # regenerated by coherent(), so only the target guard can fire; each flips with its fix reverted).
                # F1 (ABSENT vs PRESENT-FALSY): a staged candidate whose record carries a falsy `id` (False, 0,
                # "", [], {}) or a falsy `updated_at` is refused by the shared _validate_candidate at the gate; the
                # pre-fix validator synthesized a placeholder over every falsy value and PASSED it.
                for _tag, _field, _val, _det in (("id-false", "id", False, "carries an `id`"),
                                                 ("id-zero", "id", 0, "carries an `id`"),
                                                 ("id-empty-str", "id", "", "carries an `id`"),
                                                 ("id-empty-list", "id", [], "carries an `id`"),
                                                 ("id-empty-table", "id", {}, "carries an `id`"),
                                                 ("updated-at-false", "updated_at", False, "updated_at"),
                                                 ("updated-at-empty-str", "updated_at", "", "updated_at")):
                    coherent("pr4b-disc-r4f1-candidate-" + _tag, "ingest-draft-loss-binding",
                             lambda b, _field=_field, _val=_val: edit_doc(
                                 "candidates_draft.toml",
                                 lambda d: d["candidate"][0]["record"].__setitem__(_field, _val)), _det)

                # F2 (MOVE DEST vs THE COMPLETE SOURCE SET): the move's option + action dest re-pointed at another
                # worksheet source, beneath it, and at its ancestor directory (plan_ingest refuses all three at
                # plan time via its live lstat; the pre-fix gate PASSED them, having no source-set admission).
                def _f2(dest):
                    def edit(b):
                        first(b["options"]["option"], source_path="legacy/move.md")["dest_path"] = dest
                        edit_doc("ingest-actions.toml", lambda d: first(
                            d["action"], source_path="legacy/move.md").__setitem__("dest_path", dest))
                    return edit
                coherent("pr4b-disc-r4f2-dest-equals-source", "ingest-disposition-totality",
                         _f2("legacy/keep.md"), "equals the worksheet source")
                coherent("pr4b-disc-r4f2-dest-beneath-source", "ingest-disposition-totality",
                         _f2("legacy/keep.md/child"), "lies beneath the worksheet source")
                coherent("pr4b-disc-r4f2-dest-ancestor-of-source", "ingest-disposition-totality",
                         _f2("legacy"), "is an ancestor directory of the worksheet source")

                # F5 (CLOSED DOCUMENT SHAPES): a field outside the producer's shape on candidates_draft.toml, on
                # the ingest-review.toml top level, and in its binding table (each pre-fix silently ignored).
                coherent("pr4b-disc-r4f5-candidates-draft-extra-field", "ingest-draft-loss-binding",
                         lambda b: edit_doc("candidates_draft.toml", lambda d: d.__setitem__("rogue", "x")),
                         "outside the producer's closed document shape")
                coherent("pr4b-disc-r4f5-bundle-extra-field", "ingest-run-structure",
                         lambda b: b.__setitem__("rogue", "x"), "outside the producer's closed shape")
                coherent("pr4b-disc-r4f5-bundle-binding-extra-field", "ingest-run-structure",
                         lambda b: b["binding"].__setitem__("rogue", "sha256:" + "0" * 64),
                         "binding carries field(s) outside")

                # F6 (CANDIDATE ORDER, within one importer output): the migrate source's two drafts reversed
                # (each still valid, counted, digest-bound) no longer equal the producer's sorted order.
                check("pr4b-r4f6-fixture-two-candidates",
                      len(read(run, "candidates_draft.toml")["candidate"]) >= 2)
                coherent("pr4b-disc-r4f6-candidates-order", "ingest-draft-loss-binding",
                         lambda b: edit_doc("candidates_draft.toml", lambda d: d["candidate"].reverse()),
                         "not in the order the planner stages them")

                # A-M1 (DETECTION THROUGH THE SUPPLIED DIR): a DETACHED copy (outside any conventional
                # `<store>/.working/imports/<run-id>` ancestry) of the coherent run is still classified an
                # ingest run from its OWN listing: the clean copy passes all six ingest checks, and with
                # candidates_draft.toml deleted and an unregistered file added it FINDINGs (the pre-fix gate
                # read the reconstructed conventional path's absence as "not an ingest run" and passed it).
                # Nested so the copy's own conventional ancestry (three levels up) holds NO `.working/imports/`
                # and cannot resolve back to the original run: a conventional-path classifier finds nothing.
                detached = root / "pr4b-detached" / "elsewhere" / "review" / run.name
                shutil.copytree(str(run), str(detached))
                try:
                    _det_clean = _chk.check_staged_run(str(detached))
                    (detached / "candidates_draft.toml").unlink()
                    (detached / "rogue.txt").write_text("x\n", encoding="utf-8")
                    _det_bad = _chk.check_staged_run(str(detached))
                    check("pr4b-disc-am1-detached-run",
                          all(_det_clean[cid][0] for cid in _pr4b_ids)
                          and _det_bad["ingest-run-structure"][1] != "not an ingest run"
                          and _det_bad["ingest-artefact-completeness"][0] is False)
                finally:
                    shutil.rmtree(str(root / "pr4b-detached"))

                # A-M2 (NON-BLOCKING, REGULAR-FILE-VALIDATED READS): a FIFO in place of candidates_draft.toml (an
                # ingest artefact) and of mappings.toml (a core artefact), each on its own detached copy, is a
                # located FINDING, never a read that blocks forever. The gate runs in a CHILD under a timeout
                # guard (the pre-fix gate hung on the writer-less FIFO open), so a regression fails this check
                # rather than hanging the suite.
                fifo_root = root / "pr4b-fifo"
                try:
                    fifo_runs = []
                    for victim in ("candidates_draft.toml", "mappings.toml"):
                        dest = fifo_root / victim.replace(".", "-") / run.name
                        shutil.copytree(str(run), str(dest))
                        (dest / victim).unlink()
                        os.mkfifo(str(dest / victim))
                        fifo_runs.append(str(dest))
                    probe = (
                        "import sys\nsys.path.insert(0, {tools!r})\nimport check_opf_import as c\n"
                        "a = c.check_staged_run({ingest!r})\nb = c.check_staged_run({core!r})\n"
                        "ok = (a['ingest-draft-loss-binding'][0] is False and 'not a regular file' in "
                        "a['ingest-draft-loss-binding'][1] and b['staged-run-structure'][0] is False and "
                        "set(b) == set(c.EXPECTED_CHECKS))\nsys.exit(0 if ok else 3)\n").format(
                            tools=str(Path(__file__).resolve().parent), ingest=fifo_runs[0], core=fifo_runs[1])
                    try:
                        _fifo = subprocess.run([sys.executable, "-I", "-B", "-c", probe], timeout=120,
                                               capture_output=True)
                        _fifo_ok = _fifo.returncode == 0
                    except subprocess.TimeoutExpired:
                        _fifo_ok = False
                    check("pr4b-disc-am2-fifo-nonblocking", _fifo_ok)
                finally:
                    shutil.rmtree(str(fifo_root), ignore_errors=True)

        # (h9b) ROUND-4 F6 (CANDIDATE ORDER ACROSS importer outputs): two migrate sources; the drafts staged
        # in the shared sort_candidate_rows order (migrate-row order, then draft_ref) pass, and the same drafts
        # regrouped with the SECOND migrate row's draft first (digests refreshed, report regenerated) are a
        # FINDING, so a sort keyed on draft_ref alone (ignoring the migrate-row grouping) flips this check.
        with fixture({"legacy/m1.md": "- [ ] one\n- [x] two\n", "legacy/m2.md": "- [ ] three\n"}) as (root, machine):
            ws = triage(root, ["legacy/m1.md", "legacy/m2.md"], "migrate")
            run = staged(root, machine, ws, options(ws, importer_kind="github-tasklist"),
                         ["legacy/m1.md", "legacy/m2.md"])
            if run is not None:
                import check_opf_import as _chk6
                _f6_clean = _chk6.check_staged_run(str(run))["ingest-draft-loss-binding"]
                d = read(run, "candidates_draft.toml")
                order = [(c["source_path"], c["draft_ref"]) for c in d["candidate"]]
                d["candidate"] = [c for c in d["candidate"] if c["source_path"] == "legacy/m2.md"] + [
                    c for c in d["candidate"] if c["source_path"] == "legacy/m1.md"]
                (run / "candidates_draft.toml").write_bytes(_opf_import._emit_bytes(d, "candidates_draft.toml"))
                rebind(run, load_bundle(root, run))
                _f6 = _chk6.check_staged_run(str(run))["ingest-draft-loss-binding"]
                check("pr4b-disc-r4f6-candidates-group-order",
                      _f6_clean[0] is True
                      and order == [("legacy/m1.md", "draft-0001"), ("legacy/m1.md", "draft-0002"),
                                    ("legacy/m2.md", "draft-0001")]
                      and _f6[0] is False and "not in the order the planner stages them" in _f6[1])

        # (h9c) ROUND-4 F2 PLANNER PARITY: plan_ingest refuses the three frozen-decidable source conflicts with the
        # SAME FINDING the review gate returns (the shared admit_move_dest, run BEFORE the live lstat), including
        # a destination beneath a source FILE (pre-fix a CANNOT-EVALUATE from the live walk's ENOTDIR).
        with fixture({"legacy/keep.md": "k\n", "legacy/move.md": "m\n"}) as (root, machine):
            found = detect(root, include=["legacy/keep.md", "legacy/move.md"])
            ws = stamp([dict(r, disposition="keep" if r["source_path"] == "legacy/keep.md" else "move",
                             note="") for r in found.rows])
            for _tag, _dest, _det in (("equals", "legacy/keep.md", "equals the worksheet source"),
                                      ("beneath", "legacy/keep.md/child", "lies beneath the worksheet source"),
                                      ("ancestor", "legacy", "is an ancestor directory of the worksheet source")):
                opts = {"format": OPTIONS_FORMAT, "schema": SCHEMA,
                        "option": [{"scope": "declared", "source_path": "legacy/move.md", "dest_path": _dest}]}
                pr = plan(root, ws, opts, ["legacy/keep.md", "legacy/move.md"])
                check("pr4b-disc-r4f2-planner-parity-" + _tag,
                      pr.verdict == FINDING and any(_det in f for f in pr.findings))

        # (h10) R7 (RE-ANCHOR BASE is a consistency binding over ALL frozen evidence, never "." by default): a
        # RELOCATED (`dir:ops`) store with a DECLARED keep under it and NO store-scope rows is a clean plan
        # (unmanaged_path = the store-relative "legacy/a.md"); the gate recovers base "ops" from the declared
        # keep action and PASSES all six ingest checks (the round-2 gate recovered "." and FALSE-FINDINGed it).
        # A second declared keep whose frozen unmanaged_path implies a DIFFERENT base (digests refreshed) is a
        # located contradiction, never a pass.
        root = build_relocated(product={"ops/legacy/a.md": "kept a\n", "ops/legacy/b.md": "kept b\n"})
        try:
            machine = root / "ops/.working/toml"
            r7_files = ["ops/legacy/a.md", "ops/legacy/b.md"]
            ws = triage(root, r7_files, "keep")
            run = staged(root, machine, ws, empty, r7_files)
            if run is not None:
                acts = read(run, "ingest-actions.toml")["action"]
                check("pr4b-r7-fixture-store-relative",
                      sorted(a["unmanaged_path"] for a in acts) == ["legacy/a.md", "legacy/b.md"])
                r7_res = _chk.check_staged_run(str(run))
                check("pr4b-disc-r7-relocated-declared-keep-passes",
                      all(r7_res[cid][0] for cid in _pr4b_ids))
                r7_acts = read(run, "ingest-actions.toml")
                first_act = [a for a in r7_acts["action"] if a["source_path"] == "ops/legacy/b.md"][0]
                first_act["unmanaged_path"] = "b.md"    # implies base "ops/legacy", contradicting "ops"
                (run / "ingest-actions.toml").write_bytes(
                    _opf_import._emit_bytes(r7_acts, "ingest-actions.toml"))
                r7_bundle = load_bundle(root, run)
                r7_bundle["binding"]["ingest_actions_digest"] = dig(run, "ingest-actions.toml")
                (run / NAME).write_bytes(_opf_import._emit_bytes(r7_bundle, NAME))
                r7_bad = _chk.check_staged_run(str(run))["ingest-disposition-totality"]
                check("pr4b-disc-r7-contradictory-base",
                      r7_bad[0] is False and "more than one re-anchor base" in r7_bad[1])
        finally:
            shutil.rmtree(root)

        # (h11) R8 (REGRESSION): a DECLARED keep on a store that resolves OUTSIDE the product root has no
        # store-relative [unmanaged].paths spelling; plan_ingest returns the located verdict 2, never raises
        # (the round-2 extraction passed a None base into _store_rel_of, a TypeError plan_ingest does not
        # catch).
        # The store is a SIBLING of the product root (`dir:../<name>`, resolved against the product root), so
        # both live under the harness tempdir and are removed here.
        root = build_relocated(subdir="../r8-external-store", product={"legacy/a.md": "a\n"})
        try:
            ws = triage(root, ["legacy/a.md"], "keep")
            try:
                r8 = plan(root, ws, empty, ["legacy/a.md"])
                r8_verdict, r8_raised = r8.verdict, False
            except Exception:      # noqa: BLE001 - the regression under test is exactly an escape
                r8_verdict, r8_raised = None, True
            check("pr4b-disc-r8-external-store-declared-keep",
                  not r8_raised and r8_verdict == CANNOT_EVALUATE)
        finally:
            shutil.rmtree(root)
            shutil.rmtree(str(root.parent / "r8-external-store"), ignore_errors=True)

        # (h12) A-M3 (STATIC SCOPE ADMISSIBILITY, both directions) on a RELOCATED `dir:ops` store. (a) A STORE
        # row `.working/a.md` re-scoped as a DECLARED row `ops/.working/a.md` (crosswalk, keep action,
        # include, and every digest re-derived to match, so each equality holds) lies in the recovered store
        # working subtree; (b) a DECLARED keep `ops/legacy/a.md` re-scoped as a STORE row `legacy/a.md` lies
        # outside the mandatory `.working/` subtree. Both pre-fix PASSED the gate; plan_ingest refuses the
        # SAME frozen inputs with a FINDING (asserted, so the gate's verdict is at parity with the producer).
        def _am3(name, files, product_key, mutate):
            root = build_relocated(**{product_key: files})
            try:
                machine = root / "ops/.working/toml"
                paths = list(files)
                ws = triage(root, paths, "keep")
                run = staged(root, machine, ws, empty, paths)
                if run is None:
                    return
                b = load_bundle(root, run)
                acts = read(run, "ingest-actions.toml")
                mutate(b, acts)
                restamp(b)
                (run / "ingest-actions.toml").write_bytes(_opf_import._emit_bytes(acts, "ingest-actions.toml"))
                rebind(run, b)
                res = _chk.check_staged_run(str(run))["ingest-source-binding"]
                replan = plan_ingest(root, b["worksheet"], b["options"],
                                     include=b["include"] if b["include_declared"] else None,
                                     now=now, run_nonce="mig-pr4b-am3")
                check(name, res[0] is False and "not scope-admissible" in res[1] and replan.verdict == FINDING)
            finally:
                shutil.rmtree(root)

        def _am3_store_to_declared(b, acts):
            row = b["worksheet"]["row"][0]
            row.update(scope="declared", source_path="ops/.working/a.md")
            b["crosswalk"] = [derive_crosswalk_row(row, "ops/.working/a.md")]
            b.update(include_declared=True, include=["ops/*"])
            acts["action"] = [derive_keep_action(row, ".working/a.md")]
        _am3("pr4b-disc-am3-store-row-as-declared", {".working/a.md": "stray\n"}, "strays",
             _am3_store_to_declared)

        def _am3_declared_to_store(b, acts):
            row = b["worksheet"]["row"][0]
            row.update(scope="store", source_path="legacy/a.md")
            b["crosswalk"] = [derive_crosswalk_row(row, "ops/legacy/a.md")]
            b.update(include_declared=False, include=[])
            acts["action"] = [derive_keep_action(row, "legacy/a.md")]
        _am3("pr4b-disc-am3-declared-row-as-store", {"ops/legacy/a.md": "kept\n"}, "product",
             _am3_declared_to_store)

        # (h13) B-m (STORE-SCOPE keep action RE-DERIVE): a store-scope keep's unmanaged_path is emitted VERBATIM
        # and is NOT base-recovery evidence, so a drifted spelling (ingest_actions_digest and every other
        # digest refreshed) reaches the action comparator itself: the located re-derivation diagnostic is
        # REQUIRED, so a comparator that ignored unmanaged_path would pass this mutation and flip the check
        # (the declared-keep E5 vector above is refused earlier, by base recovery, and cannot prove that).
        root, machine = build_store(strays={".working/legacy/skeep.md": "store keep\n"})
        try:
            ws = triage(root, [".working/legacy/skeep.md"], "keep")
            run = staged(root, machine, ws, empty, [".working/legacy/skeep.md"])
            if run is not None:
                _bm_clean = _chk.check_staged_run(str(run))
                acts = read(run, "ingest-actions.toml")
                acts["action"][0]["unmanaged_path"] = ".working/legacy/forged-exemption.md"
                (run / "ingest-actions.toml").write_bytes(_opf_import._emit_bytes(acts, "ingest-actions.toml"))
                rebind(run, load_bundle(root, run))
                _bm = _chk.check_staged_run(str(run))["ingest-disposition-totality"]
                check("pr4b-disc-bm-store-keep-unmanaged-path",
                      all(_bm_clean[cid][0] for cid in _pr4b_ids) and _bm[0] is False
                      and "does not equal the action re-derived" in _bm[1])
        finally:
            shutil.rmtree(root)

        # (h14) C-m1 (the report claims NO validation it did not observe): a plan whose ingest-review.toml
        # creation fails (injected at the bundle writer) leaves a staged IMPORT-REPORT.md behind; it must state
        # the neutral "semantic validation required" status, never "gate-validated" (the pre-fix render claimed
        # validation before any bundle existed or any gate ran).
        with fixture({"legacy/a.md": "source\n"}) as (root, machine):
            ws = triage(root, ["legacy/a.md"], "keep")
            _real_bundle_writer = _opf_import._write_ingest_review_bundle

            def _failing_bundle_writer(*_a, **_k):
                raise _opf_import._StageError(CANNOT_EVALUATE, "injected ingest-review.toml create failure")
            _opf_import._write_ingest_review_bundle = _failing_bundle_writer
            try:
                cm1 = plan(root, ws, empty, ["legacy/a.md"])
            finally:
                _opf_import._write_ingest_review_bundle = _real_bundle_writer
            left = sorted((machine.parent / "imports").iterdir()) if (machine.parent / "imports").is_dir() else []
            md = (left[0] / "IMPORT-REPORT.md").read_text(encoding="utf-8") if len(left) == 1 else ""
            check("pr4b-disc-cm1-report-claims-no-validation",
                  cm1.verdict == CANNOT_EVALUATE and len(left) == 1
                  and not (left[0] / _opf_import.INGEST_REVIEW_NAME).exists()
                  and "reviewability: staged; semantic validation required" in md
                  and "gate-validated" not in md)

        # (h15) C-m2 (decision-unit ids are KIND-PREFIXED and asserted UNIQUE): a migrate row `legacy/a.md` with
        # drafts beside a keep row for a file literally named `legacy/a.md#conversion` yields three DISTINCT ids
        # (pre-fix both the conversion unit and the second file's disposition unit displayed as
        # `declared:legacy/a.md#conversion`), and a duplicated frozen crosswalk row is refused CANNOT-EVALUATE
        # rather than rendered as two decisions sharing one id.
        _cm2_cw = [{"scope": "declared", "source_path": "legacy/a.md", "resolved_source_path": "legacy/a.md",
                    "disposition": "migrate"},
                   {"scope": "declared", "source_path": "legacy/a.md#conversion",
                    "resolved_source_path": "legacy/a.md#conversion", "disposition": "keep"}]
        _cm2_mig = [{"scope": "declared", "source_path": "legacy/a.md", "resolved_source_path": "legacy/a.md",
                     "importer_kind": "github-tasklist", "candidate_count": 2, "proposal_count": 2}]
        try:
            _cm2_ids = [u["unit_id"] for u in _opf_import._ingest_render_model(
                "imp-20260101T000000Z-0000000000000000", _cm2_cw, _cm2_mig)["decision_units"]]
        except _opf_import._StageError:
            _cm2_ids = None   # a colliding id formula is refused by the uniqueness assertion: this leg fails
        try:
            _opf_import._ingest_render_model("imp-20260101T000000Z-0000000000000000",
                                             _cm2_cw + [dict(_cm2_cw[1])], _cm2_mig)
            _cm2_dup_refused = False
        except _opf_import._StageError as exc:
            _cm2_dup_refused = exc.verdict == CANNOT_EVALUATE
        check("pr4b-disc-cm2-unit-ids-kind-prefixed-unique",
              _cm2_ids == ["conversion:declared:legacy/a.md", "disposition:declared:legacy/a.md",
                           "disposition:declared:legacy/a.md#conversion"] and _cm2_dup_refused)

        # (h7c) E3 (C1 render fix in the report BODY): _render_report_md escapes the UNTRUSTED source_path of a
        # fragment AND a proposal for the Markdown/terminal sink, so a crafted C1 byte cannot reach the
        # reviewer's terminal raw from the ordinary report body (not only the ingest section). Reverting the
        # source_path escape in _render_report_md passes the byte through raw (change-carries-check).
        _rr = _opf_import._render_report_md(
            "sha256:" + "0" * 64,
            [{"source_path": "frag\x9b.md", "span": [0, 1], "fragment_id": "frag-0000000000000000",
              "fragment_digest": "0" * 64}],
            [{"source_path": "prop\x9b.md", "span": [0, 1], "suggested_state": "unmapped", "note": "",
              "_origin": _opf_import._MODEL_PROPOSAL_ORIGIN}],
            "imp-20260101T000000Z-0000000000000000")
        check("pr4b-disc-e3-report-body-c1-escape",
              "frag\\x9b.md" in _rr and "prop\\x9b.md" in _rr and "\x9b" not in _rr)

        # (h7) FIX 5: _ingest_md_escape neutralizes the C1 control range (0x80-0x9f: CSI 0x9b, OSC 0x9d, etc.)
        # as well as C0 / DEL / backtick, so a crafted staged value cannot carry a C1 terminal-control byte
        # into the reviewer's terminal. Reverting the C1 arm passes the byte through raw (change-carries-check);
        # 0xa0 (just above C1) stays unescaped, and the C0 / backtick handling is unchanged.
        esc = _opf_import._ingest_md_escape
        check("bundle-c1-escape",
              esc("\x9b") == "\\x9b" and esc("\x80") == "\\x80" and esc("\x9f") == "\\x9f"
              and esc("\xa0") == "\xa0" and esc("`") == "\\u0060" and esc("\x1b") == "\\x1b")

    registry = (
        [("options-schema-validator", schema_validator), ("keep-unmanaged-exemption", keep),
         ("migrate-importer-proposal", migrate), ("move-collision-matrix", collisions),
         ("archive-prefill-default", archive), ("ingest-plan-refuses-apply", refuse_apply),
         ("disposition-binding-matrix", disposition_bindings),
         ("relocated-store-scope-bytes", relocated_store_scope),
         ("reconcile-stage-window", reconcile_stage_window),
         ("partial-ingest-stage-nonpromotable", partial_ingest_stage),
         ("ingest-review-bundle", review_bundle)]
        if gate else
        [("plan-ingest-keep-exemption", keep), ("plan-ingest-migrate-provenance", migrate),
         ("plan-ingest-move-archive-default", archive),
         ("plan-ingest-move-collision-matrix", collisions),
         ("plan-ingest-options-fail-closed", fail_closed),
         ("plan-ingest-reconcile-fail-closed", reconcile),
         ("plan-ingest-unresolved-halts", unresolved),
         ("plan-ingest-refuses-apply", refuse_apply),
         ("plan-ingest-disposition-binding-matrix", disposition_bindings),
         ("plan-ingest-relocated-store-scope", relocated_store_scope),
         ("plan-ingest-reconcile-stage-window", reconcile_stage_window),
         ("plan-ingest-partial-stage-nonpromotable", partial_ingest_stage),
         ("plan-ingest-review-bundle", review_bundle)])
    outer_check = check
    for label, test in registry:
        check = lambda suffix, cond, label=label: outer_check(label + "/" + suffix, cond)
        test()


def self_test():
    """Detection + worksheet invariants over synthetic stores, with a discriminating vector per guarantee
    (change-carries-check). Judged on returned verdict / finding values, never by grepping output. The
    tempdir is removed in a finally (test-hermeticity)."""
    import errno
    import shutil
    import tempfile

    try:
        _journal.require_containment()
    except _journal.JournalError as exc:
        print("OPF-INGEST SELF-TEST ERROR: {}; fail-closed".format(exc), file=sys.stderr)
        return 2

    failures = []
    checked = [0]

    def check(label, cond):
        checked[0] += 1
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

    def symlink_supported():
        """True when this platform + filesystem can create a symlink, PROBED ONCE with a throwaway dangling
        link under `base`. Only a GENUINE unsupported-platform signal returns False so a symlink vector is
        SKIPPED (not a failure): `errno.ENOSYS` (the platform-level "function not implemented"), or a
        `NotImplementedError` / `AttributeError` on `os.symlink` itself. Any OTHER OSError (EACCES, EPERM, a
        read-only filesystem, etc.) is a real setup condition, NOT "unsupported", so it PROPAGATES to the
        harness-error path (exit 2): a permission-denied environment can never MASQUERADE as unsupported and
        SILENTLY SKIP the symlink discriminators, letting them gain zero coverage (F9.2 / codex-8 #3:
        check-fails-closed / test-hermeticity)."""
        probe = base / ".symlink-probe"
        try:
            os.symlink("target", str(probe))
        except (NotImplementedError, AttributeError):
            return False   # os.symlink is genuinely unavailable / not implemented on this platform
        except OSError as exc:
            if exc.errno == errno.ENOSYS:
                return False   # the platform-level "function not implemented" signal
            raise              # EACCES / EPERM / read-only fs / etc. -> a real error, never a silent skip
        os.unlink(str(probe))
        return True

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

        # 8a. declared-scope managed-exclusion (F1) + the F-8.1 correction. The managed PRODUCT-ROOT paths an
        #     --include cannot pull in are a declared [unmanaged] path (`keep.md`) and a PUBLIC deliverable
        #     target (`CHANGELOG.md`, product-root in every topology). Per F-8.1, a ROGUE view target
        #     (`report.md`, an unrecognized view name marks nothing) and a NON-PUBLIC deliverable target
        #     (`out.pdf`) are NOT managed and so ARE detected (agreeing with the checker, which grades them as
        #     strays); reverting F-8.1 (covering the RAW target) would wrongly EXCLUDE `report.md` / `out.pdf`.
        root, _m = build_store(
            product={"report.md": "R", "out.pdf": "D", "keep.md": "K", "loose.md": "L", "CHANGELOG.md": "C"},
            manifest_extra=('[views.main]\nkind = "composed"\nsources = ["docs"]\ntarget = "report.md"\n\n'
                            '[deliverables.d1]\nkind = "curated"\ntarget = "out.pdf"\n\n'
                            '[deliverables."CHANGELOG.md"]\nkind = "curated"\ntarget = "CHANGELOG.md"\n\n'
                            '[unmanaged]\npaths = ["keep.md"]'))
        decl = detect(root, include=["report.md", "out.pdf", "keep.md", "loose.md", "CHANGELOG.md"])
        decl_paths = {row["source_path"] for row in decl.rows if row["scope"] == "declared"}
        check("declared-scope-managed-excluded",
              decl.verdict == CLEAN and "loose.md" in decl_paths
              and "keep.md" not in decl_paths and "CHANGELOG.md" not in decl_paths
              and "report.md" in decl_paths and "out.pdf" in decl_paths)

        # 8a1. F-8.1: the view / deliverable COVERED derivation MIRRORS the checker authority
        #      (`_opf_check._check_containment`, round-14 C3), gating on `_opf_views._resolve_view` and using
        #      the `_opf_views._spec_destination`, so ingest and the checker AGREE and a crafted / rebound
        #      manifest cannot launder a store path into "managed". All STORE-scope (under `.working/`).
        # (a) ROGUE-NAME LAUNDER DETECTED: an UNRECOGNIZED view name (`rogue`) with target `.working/rogue.md`
        #     marks NOTHING (`_resolve_view` raises), so a real `.working/rogue.md` is DETECTED as a stray,
        #     agreeing with C-CONTAINMENT. Reverting F-8.1 (covering the RAW target) OMITS `.working/rogue.md`.
        root, _m = build_store(
            strays={".working/rogue.md": "x", ".working/real.md": "r"},
            manifest_extra=('[views.rogue]\nkind = "composed"\nsources = ["backlog_item"]\n'
                            'target = ".working/rogue.md"'))
        r = detect(root)
        f81 = {row["source_path"] for row in r.rows}
        check("f81-rogue-view-name-launder-detected",
              r.verdict == CLEAN and ".working/rogue.md" in f81 and ".working/real.md" in f81)
        # (b) WELL-FORMED VIEW UNCHANGED: a RECOGNIZED store-scope view (`TODO.md`) whose target EQUALS its
        #     spec destination (`.working/TODO.md`) COVERS that destination, so a file there is NOT a stray
        #     while a real stray alongside still is (no-regression on the happy path).
        root, _m = build_store(
            strays={".working/TODO.md": "t", ".working/real.md": "r"},
            manifest_extra=('[views."TODO.md"]\nkind = "composed"\nsources = ["backlog_item"]\n'
                            'target = ".working/TODO.md"'))
        r = detect(root)
        f81 = {row["source_path"] for row in r.rows}
        check("f81-wellformed-view-target-covered",
              r.verdict == CLEAN and ".working/TODO.md" not in f81 and ".working/real.md" in f81)
        # (c) REBIND DETECTED: a RECOGNIZED view (`TODO.md`) REBOUND off its spec destination (target
        #     `.working/evil.md`) covers the SPEC destination `.working/TODO.md` (NOT a stray) while the
        #     rebound `.working/evil.md` becomes a DETECTABLE stray, exactly as the checker grades it.
        #     Reverting F-8.1 (covering the RAW target) INVERTS both: `.working/evil.md` omitted, `.working/
        #     TODO.md` emitted.
        root, _m = build_store(
            strays={".working/TODO.md": "t", ".working/evil.md": "e", ".working/real.md": "r"},
            manifest_extra=('[views."TODO.md"]\nkind = "composed"\nsources = ["backlog_item"]\n'
                            'target = ".working/evil.md"'))
        r = detect(root)
        f81 = {row["source_path"] for row in r.rows}
        check("f81-rebound-view-covers-spec-dest-and-rebind-is-stray",
              r.verdict == CLEAN and ".working/TODO.md" not in f81
              and ".working/evil.md" in f81 and ".working/real.md" in f81)
        # (d) NON-PUBLIC DELIVERABLE TARGET DETECTED: deliverables carry no name-resolution authority and the
        #     checker covers NO store-scope deliverable target, so a `.working/d.md` deliverable target is a
        #     DETECTABLE stray (a PUBLIC target stays product-relative and covered; see 8e-e). Reverting F-8.1
        #     (covering the raw deliverable target) OMITS `.working/d.md`.
        root, _m = build_store(
            strays={".working/d.md": "d", ".working/real.md": "r"},
            manifest_extra='[deliverables.d1]\nkind = "curated"\ntarget = ".working/d.md"')
        r = detect(root)
        f81 = {row["source_path"] for row in r.rows}
        check("f81-nonpublic-deliverable-target-detected",
              r.verdict == CLEAN and ".working/d.md" in f81 and ".working/real.md" in f81)

        # 8f. R9-1: ingest mirrors the checker's MATCHING semantics, not only its covered-set DERIVATION. An
        #     EXACT-LEAF managed destination (a recognized view spec destination; a public deliverable target)
        #     is matched by EXACT equality, NOT subtree containment (as `_opf_check.managed_leaf` matches a
        #     view target `if p in view_targets`): a REGULAR FILE there is the managed output (excluded), but a
        #     DIRECTORY there is a WRONG-TYPE anomaly -> CANNOT-EVALUATE naming it, never a silent subtree prune
        #     that hides its children. Pre-fix the covered entry pruned the whole subtree, so a DIRECTORY at
        #     `.working/TODO.md/` silently OMITTED `.working/TODO.md/inner.md` and returned CLEAN while the
        #     checker flagged it C-CONTAINMENT and cannot-evaluated the store (they DISAGREED); the fix restores
        #     agreement (both CANNOT-EVALUATE), cross-verified against `_opf_check.validate_store` at review.
        # (a) DIRECTORY at a store-scope VIEW spec destination -> CANNOT-EVALUATE naming it (the R9-1 store),
        #     with the hidden child never emitted as a row. Reverting the split re-prunes the subtree -> CLEAN
        #     omitting the child (the fail-without-the-fix defect).
        root, _m = build_store(
            strays={".working/TODO.md/inner.md": "hidden", ".working/real.md": "r"},
            manifest_extra=('[views."TODO.md"]\nkind = "composed"\nsources = ["backlog_item"]\n'
                            'target = ".working/TODO.md"'))
        r91 = detect(root)
        check("f91-dir-at-view-spec-dest-cannot-evaluate",
              r91.verdict == CANNOT_EVALUATE and any(".working/TODO.md" in f for f in r91.findings)
              and not any(row["source_path"] == ".working/TODO.md/inner.md" for row in r91.rows))
        # (b) a REGULAR FILE at the same view spec destination is the managed output: excluded by EQUALITY,
        #     with a real stray alongside still detected (the well-formed happy path, unchanged).
        root, _m = build_store(
            strays={".working/TODO.md": "t", ".working/real.md": "r"},
            manifest_extra=('[views."TODO.md"]\nkind = "composed"\nsources = ["backlog_item"]\n'
                            'target = ".working/TODO.md"'))
        rf = detect(root)
        rf_paths = {row["source_path"] for row in rf.rows}
        check("f91-regular-file-at-view-dest-excluded",
              rf.verdict == CLEAN and ".working/TODO.md" not in rf_paths and ".working/real.md" in rf_paths)
        # (c) a DIRECTORY at a PUBLIC deliverable target (product-root, exercised through declared scope) is
        #     likewise a WRONG-TYPE anomaly -> CANNOT-EVALUATE naming it, never a subtree prune. A regular file
        #     there stays excluded (test 8a); here `CHANGELOG.md` is a directory containing a hidden child.
        root, _m = build_store(
            product={"CHANGELOG.md/inner.md": "hidden", "readme.md": "R"},
            manifest_extra='[deliverables."CHANGELOG.md"]\nkind = "curated"\ntarget = "CHANGELOG.md"')
        rpub = detect(root, include=["*"])
        check("f91-dir-at-public-target-cannot-evaluate",
              rpub.verdict == CANNOT_EVALUATE and any("CHANGELOG.md" in f for f in rpub.findings)
              and not any(row["source_path"] == "CHANGELOG.md/inner.md" for row in rpub.rows))

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
        # The declared include is a BROAD glob with a real read-space hit (`docs/keep.md`), NOT a pattern
        # naming the covered `docs/legacy` subtree directly: a pattern that can only resolve within a PRESENT
        # covered subtree is now a CANNOT-EVALUATE (R7-1), so the declared-scope subtree exclusion is shown
        # here by the pruned `docs/legacy` producing no `docs/legacy/old.md` row while `docs/keep.md` is
        # detected (revert the directory prune -> `docs/legacy/old.md` is entered, matched and read).
        r = detect(root, include=["docs/*"])
        sub_paths = {row["source_path"] for row in r.rows}
        check("unmanaged-directory-covers-subtree",
              r.verdict == CLEAN and ".working/real.md" in sub_paths and "docs/keep.md" in sub_paths
              and ".working/legacy-dir/kept.md" not in sub_paths
              and ".working/legacy-dir/sub/deep.md" not in sub_paths
              and "docs/legacy/old.md" not in sub_paths)

        # 8b-existence + R7-1 THREE-VALUED no-match derivation. Round 5 separated EXISTENCE from exclusion;
        #     R7-1 corrects the residual UNSOUNDNESS of using a pattern's literal ANCESTOR prefix as proof
        #     the input EXISTS. A declared --include with no read-space hit is:
        #       - a no-match FINDING only if it lies ENTIRELY within the read (non-excluded) space, so an
        #         --include naming an ABSENT covered path (no unread content to hide a match) is a FINDING;
        #       - CANNOT-EVALUATE if it could match within a PRESENT covered subtree detection never reads
        #         (neither match nor no-match is knowable across the no-read boundary), whether the pattern
        #         is a literal descendant (ancestor existence never proves a descendant), a wildcard whose
        #         match set descends into the covered dir, or a wildcard the covered dir's literal prefix
        #         does not name. The round-7 proxy returned CLEAN for the literal cases and a FALSE no-match
        #         FINDING for the wildcard cases; both are now CANNOT-EVALUATE (fail-closed).
        # (a) ABSENT covered path -> a genuine no-match FINDING (only existence separates it from the cases
        #     below); reverting the derivation keeps this a FINDING, so it is the existence-separator anchor.
        root, _m = build_store(product={"live.md": "L"},
                               manifest_extra='[unmanaged]\npaths = ["qa-absent-legacy"]')
        absent = detect(root, include=["qa-absent-legacy/*.md"])
        check("declared-absent-covered-include-is-finding", absent.verdict == FINDING and absent.rows == [])
        # (b/c) PRESENT covered `legacy` with an UNREAD child `legacy/old.md`: a literal descendant (present
        #     or absent) and a wildcard that reaches into `legacy` are each CANNOT-EVALUATE naming `legacy`.
        root, _m = build_store(product={"live.md": "L", "legacy/old.md": "o"},
                               manifest_extra='[unmanaged]\npaths = ["legacy"]')
        for _pat in ("legacy/old.md", "legacy/ABSENT.md", "legacy/*.txt"):
            _r = detect(root, include=[_pat])   # literal descendant: reverting -> CLEAN (verdict 0)
            check("r71-covered-descendant-cannot-evaluate",
                  _r.verdict == CANNOT_EVALUATE and any("legacy" in f for f in _r.findings))
        for _pat in ("*/old.md", "l*/old.md"):
            _r = detect(root, include=[_pat])   # wildcard into covered: reverting -> false FINDING (verdict 1)
            check("r71-wildcard-reaches-covered-cannot-evaluate",
                  _r.verdict == CANNOT_EVALUATE and any("legacy" in f for f in _r.findings))
        # (d) covered-reaching include WITH a coexisting store stray: `*/old.md` reaches the PRESENT covered
        #     `legacy`, so the single atomic verdict is CANNOT-EVALUATE even though a real store stray
        #     (`.working/loose.md`) coexists; it never degrades to a no-match FINDING (verdict 1). The atomic
        #     single verdict correctly WITHHOLDS the whole worksheet (store stray included) as the fail-closed
        #     semantics require (codex-8 #2: this is NOT a "stray retention" test; the property is the
        #     CANNOT-EVALUATE verdict, not that the stray row survives). Reverting R7-1 makes it a false
        #     FINDING (verdict 1).
        root, _m = build_store(strays={".working/loose.md": "loose"}, product={"legacy/old.md": "o"},
                               manifest_extra='[unmanaged]\npaths = ["legacy"]')
        covered_reaching = detect(root, include=["*/old.md"])
        check("r71-covered-reaching-include-with-store-stray-cannot-evaluate",
              covered_reaching.verdict == CANNOT_EVALUATE)
        # (e) a purely READ-SPACE no-match (no covered subtree the pattern can reach) stays a FINDING, so the
        #     three-valued derivation never over-widens a genuine no-match into CANNOT-EVALUATE.
        root, _m = build_store(product={"live.md": "L"})
        check("r71-read-space-no-match-is-finding", detect(root, include=["nope/*.md"]).verdict == FINDING)

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

        # 8b-prune (F1: traverse-before-exclude). A covered (excluded) DIRECTORY is PRUNED before descent,
        #     never entered, so an unreadable / exotic entry inside a subtree we would exclude anyway cannot
        #     block detection with a spurious CANNOT-EVALUATE (spec 14.2 never enters an unmanaged subtree). A
        #     SYMLINK planted inside a declared-unmanaged directory makes the pre-fix walk (which entered the
        #     subtree, excluding only at the result stage) fail closed on the symlink; pruning before descent
        #     detects the live sibling CLEAN and never enters the excluded subtree. The declared include is
        #     `live.md` alone (a covered-subtree pattern like `legacy/*` is now a CANNOT-EVALUATE, R7-1, which
        #     would mask this DIRECTORY-prune vector); reverting the directory prune enters `legacy`, hits the
        #     symlink and fails closed. Symlink support is platform-gated via `symlink_supported()`, which
        #     probes once: a genuinely unsupported platform skips this vector, but on a supported platform a
        #     fixture-setup `os.symlink` error RAISES and fails the harness rather than silently skipping it.
        root, _m = build_store(strays={".working/real.md": "r"},
                               product={"legacy/keep.md": "k", "live.md": "L"},
                               manifest_extra='[unmanaged]\npaths = ["legacy"]')
        if symlink_supported():
            os.symlink(str(_m / "manifest.toml"), str(root / "legacy" / "link.md"))
            pr = detect(root, include=["live.md"])
            pr_paths = {row["source_path"] for row in pr.rows}
            check("excluded-subtree-pruned-before-descent",
                  pr.verdict == CLEAN and "live.md" in pr_paths
                  and not any(p.startswith("legacy/") for p in pr_paths))

        # 8b-covered-symlink-entry (F2). A covered entry that is ITSELF a symlink (a declared-unmanaged
        #     SYMLINK, not a directory containing one) reached the exotic-entry refusal before it could be
        #     pruned, blocking the whole detect with a spurious CANNOT-EVALUATE. Pruning a covered entry
        #     BEFORE the exotic classification prunes it (never read, never refused, spec 14.2), so a live
        #     sibling detects CLEAN. Reverting the pre-refusal prune makes the covered symlink fail closed
        #     (CANNOT-EVALUATE) again. A NON-covered exotic entry still fails closed (the 10./10a. vectors).
        #     A fixture-setup os.symlink error fails the harness (codex-8 #3), never a silent skip.
        root, _m = build_store(product={"live.md": "L"}, manifest_extra='[unmanaged]\npaths = ["legacy"]')
        if symlink_supported():
            os.symlink(str(_m / "manifest.toml"), str(root / "legacy"))   # `legacy` IS the symlink (covered)
            cs = detect(root, include=["live.md"])
            cs_paths = {row["source_path"] for row in cs.rows}
            check("covered-symlink-entry-pruned-not-refused",
                  cs.verdict == CLEAN and "live.md" in cs_paths and "legacy" not in cs_paths)

        # 8b-covered-symlink-beneath (F-8.2). A PRESENT covered entry that is a SYMLINK-TO-DIR is recorded as
        #     existing (via `pruned_nondirs`), so an --include BENEATH it is a CANNOT-EVALUATE at PARITY with
        #     the real-directory case (`_pattern_intersects_covered` sees the present covered path), never a
        #     false no-match FINDING across the no-read boundary (the R7-1 class through the symlink shape).
        #     Reverting F-8.2 (dropping the covered symlink from the present set) makes the symlink case a
        #     false FINDING while the real-dir control stays CANNOT-EVALUATE, breaking the parity assertion.
        root, _m = build_store(product={"legacy_real/old.md": "o", "live.md": "L"},
                               manifest_extra='[unmanaged]\npaths = ["legacy"]')
        if symlink_supported():
            os.symlink(str(root / "legacy_real"), str(root / "legacy"))   # `legacy` -> a DIR (covered)
            sb = detect(root, include=["legacy/old.md"])
            root2, _m2 = build_store(product={"legacy/old.md": "o", "live.md": "L"},
                                     manifest_extra='[unmanaged]\npaths = ["legacy"]')
            rb = detect(root2, include=["legacy/old.md"])   # real covered dir control
            check("covered-symlink-beneath-cannot-evaluate-parity",
                  sb.verdict == CANNOT_EVALUATE and any("legacy" in f for f in sb.findings)
                  and rb.verdict == CANNOT_EVALUATE and sb.verdict == rb.verdict)

        # 8g. F9.2: the symlink_supported() probe treats ONLY a genuine unsupported-platform signal
        #     (errno.ENOSYS, or a NotImplementedError / AttributeError on os.symlink) as "unsupported" (skip);
        #     any OTHER OSError (EACCES etc.) PROPAGATES to the harness-error path, so a permission-denied
        #     environment can never MASQUERADE as unsupported and SILENTLY SKIP the symlink assertions (rc 0
        #     PASS). Pre-fix the probe caught EVERY OSError and returned False, so an injected EACCES read as
        #     "unsupported"; with the fix it RAISES (the harness maps it to rc 2), while an injected ENOSYS
        #     still returns False (skip-and-stay-green, the disclosed intended path). os.symlink is restored in
        #     a finally (test-hermeticity). Judged on the probe's raise/return, not by grepping output.
        _real_symlink = os.symlink

        def _symlink_raising(err):
            def _stub(*_a, **_k):
                raise err
            return _stub
        try:
            os.symlink = _symlink_raising(PermissionError(errno.EACCES, "permission denied"))
            _eacces_propagates = False
            try:
                symlink_supported()
            except OSError as _exc:
                _eacces_propagates = _exc.errno == errno.EACCES
            os.symlink = _symlink_raising(OSError(errno.ENOSYS, "function not implemented"))
            _enosys_skips = symlink_supported() is False
        finally:
            os.symlink = _real_symlink
        check("f92-probe-eacces-propagates", _eacces_propagates and _enosys_skips)

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

        # 8e. RE-ANCHOR the store-relative managed set for DECLARED scope on a RELOCATED store (round-7
        #     MAJOR, F1). On a `dir:ops` store (store root != product root) a manifest STORE-relative entry
        #     is bound to the resolved STORE root for the product-root declared scope, so a store-relative
        #     `notes` becomes `ops/notes`. The surviving store-relative covered entries are the
        #     [unmanaged].paths (a view spec destination is always under `.working/`, already pruned as store
        #     scope; a deliverable target is not covered at all, F-8.1), so the two re-anchoring directions
        #     are exercised through [unmanaged]:
        #       - SUPPRESSION: the raw (un-anchored) entry would match the product-relative namespace and
        #         silently suppress a REAL product stray at `notes/`; re-anchored, the product stray is
        #         DETECTED. (fail-without-fix: the stray is missing from the rows.)
        #       - UNMANAGED-READ: the store-side path under `ops/notes/` must be EXCLUDED and never READ; the
        #         raw entry left it emitted + digested (spec 14.2 violation).
        #     For an inline store re-anchoring is IDENTITY, so the inline vectors above are unchanged. Case (c)
        #     then pins the corrected NON-PUBLIC-deliverable behaviour (not covered) under relocation.
        # (a) unmanaged SUPPRESSION: `[unmanaged] notes` (store-relative) must not suppress product `notes/`.
        reloc = build_relocated(manifest_extra='[unmanaged]\npaths = ["notes"]',
                                product={"notes/todo.md": "stray", "ops/notes/legacy.md": "storeside",
                                         "readme.md": "R"})
        s1 = detect(reloc, include=["notes/*", "readme.md"])
        s1decl = {row["source_path"] for row in s1.rows if row["scope"] == "declared"}
        check("reanchor-unmanaged-suppression",
              s1.verdict == CLEAN and "notes/todo.md" in s1decl)   # raw `notes` would suppress it
        # (b) unmanaged UNMANAGED-READ: the store-side `ops/notes/legacy.md` is excluded + never read. The
        #     pattern resolves entirely within the (re-anchored) covered `ops/notes`, so per R7-1 the sound
        #     verdict is CANNOT-EVALUATE; the KEY property is that `ops/notes/legacy.md` is never a row (the
        #     raw entry emitted + READ it). Reverting the re-anchor: `ops/notes/legacy.md` becomes a row.
        s2 = detect(reloc, include=["ops/notes/*", "readme.md"])
        check("reanchor-unmanaged-not-read",
              s2.verdict == CANNOT_EVALUATE
              and not any(row["source_path"] == "ops/notes/legacy.md" for row in s2.rows))
        # (c) NON-PUBLIC deliverable target NOT covered under relocation (F-8.1): a `[deliverables.d1]
        #     target="report.md"` covers NOTHING (deliverables carry no name-resolution authority, and the
        #     checker grades a store-scope deliverable target as a stray), so on a `dir:ops` store NEITHER the
        #     product-root `report.md` NOR the store-side `ops/report.md` is suppressed -- both are DETECTED,
        #     agreeing with the checker. Reverting F-8.1 (covering + re-anchoring the raw deliverable target)
        #     would EXCLUDE the store-side `ops/report.md` (the flip). The re-anchoring MECHANISM itself stays
        #     exercised by the [unmanaged] directions (a)/(b) above, whose entries are the surviving
        #     store-relative covered set.
        reloc = build_relocated(manifest_extra='[deliverables.d1]\nkind = "curated"\ntarget = "report.md"',
                                product={"report.md": "stray", "ops/report.md": "storeside", "readme.md": "R"})
        td = detect(reloc, include=["report.md", "ops/report.md", "readme.md"])
        tddecl = {row["source_path"] for row in td.rows if row["scope"] == "declared"}
        check("nonpublic-deliverable-target-not-covered-reloc",
              td.verdict == CLEAN and "report.md" in tddecl and "ops/report.md" in tddecl)
        # (e) PUBLIC-target correctness: a PUBLIC deliverable target (`CHANGELOG.md`, product-root in every
        #     topology, spec 5.8) is NOT re-anchored, so the real product-root `CHANGELOG.md` stays excluded
        #     on a relocated store. Re-anchoring it (the finding's simplification) would DETECT it as a stray.
        reloc = build_relocated(
            manifest_extra='[deliverables."CHANGELOG.md"]\nkind = "curated"\ntarget = "CHANGELOG.md"',
            product={"CHANGELOG.md": "cl", "readme.md": "R"})
        pt = detect(reloc, include=["CHANGELOG.md", "readme.md"])
        ptdecl = {row["source_path"] for row in pt.rows if row["scope"] == "declared"}
        check("public-target-not-reanchored", pt.verdict == CLEAN and ptdecl == {"readme.md"})

        # 9. init-first: an unresolved store (no manifest) is CANNOT-EVALUATE, "run `opf init` first".
        unadopted = base / "unadopted"
        unadopted.mkdir()
        (unadopted / "loose.md").write_text("x", encoding="utf-8")
        ce = detect(unadopted)
        check("init-first-cannot-evaluate",
              ce.verdict == CANNOT_EVALUATE and any("opf init" in f for f in ce.findings))

        # 10. a NON-covered symlink in scope is refused fail-closed (never followed, never a silent skip). A
        #     fixture-setup os.symlink error fails the harness (codex-8 #3), never a silent skip of the vector.
        root, _m = build_store(strays={".working/real.md": "r"})
        if symlink_supported():
            os.symlink(str(_m / "manifest.toml"), str(root / ".working" / "link.md"))
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

        _self_test_planner(check, build_store, build_relocated, snapshot, symlink_supported)

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
    print("OPF-INGEST SELF-TEST COUNT: {} checks".format(checked[0]))
    print("OPF-INGEST SELF-TEST PASS: detect enumerates store scope exactly once, excludes the managed set "
          "by subtree containment (including a managed path an --include names, a non-canonical managed "
          "spelling, a declared-unmanaged DIRECTORY's whole subtree unread, the store pointer control "
          "files, and the store-root `.aiqt` / `.git` control / VCS trees derived from the import layer's "
          "own drop-set authority so every apply / migration ops / journal / archive tree is covered by "
          "construction, drift-checked against the import constants), derives the VIEW / DELIVERABLE covered "
          "set by MIRRORING the checker authority (`_opf_views._resolve_view` + `_spec_destination`, so a "
          "rogue view NAME cannot launder a store path, a REBIND covers the spec destination not the raw "
          "target, and a non-public deliverable target is a detectable stray -- ingest and C-CONTAINMENT "
          "agree; F-8.1), PRUNES a covered DIRECTORY (and a covered entry that is ITSELF a symlink or exotic) "
          "before it is entered or refused so it never blocks detection, with a present covered symlink-to-"
          "dir recorded so an --include beneath it is a CANNOT-EVALUATE at parity with the real-dir case "
          "(F-8.2), MATCHES an EXACT-LEAF managed destination (a recognized view spec destination, a public "
          "deliverable target) by EQUALITY as the checker's managed_leaf does so a DIRECTORY there is a "
          "WRONG-TYPE CANNOT-EVALUATE naming it (ingest and the checker agree) rather than a silent subtree "
          "prune that hides its children, while a regular-file output there stays excluded (R9-1), "
          "RE-ANCHORS every store-relative managed entry (the recognized store-scope view spec "
          "destinations, the [unmanaged].paths, and the control / VCS dirs) at the RESOLVED store root for "
          "declared scope so a relocated (`dir:`) store neither suppresses a real product stray nor emits + "
          "reads a store path (identity for an inline store), while a PUBLIC deliverable target and the "
          "pointer files stay product-relative, honours declared-scope --include (escape / store-scope each a "
          "finding) with a "
          "THREE-VALUED no-match derivation (a read-space no-match and an ABSENT covered path are findings, "
          "while a pattern that could match within a PRESENT covered subtree detection never reads is "
          "CANNOT-EVALUATE, never a false CLEAN or a false no-match that withholds a real stray), is "
          "deterministic + writes nothing, fails closed on an unresolved "
          "store, a symlink, a non-UTF-8 name, and a non-contained detected path (so a CLEAN worksheet "
          "always validates), gates its own symlink probe so only a genuine unsupported-platform signal "
          "(ENOSYS) skips a symlink vector while an EACCES propagates rather than masquerading as a silent "
          "skip (F9.2), the worksheet validates and catches vocab/digest/keyset/schema-type "
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
