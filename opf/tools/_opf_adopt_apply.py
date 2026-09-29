#!/usr/bin/env python3
"""OPF adoption apply engine, slice 1: the apply SHELL with ZERO executable operations (OPF-SPEC 1.3.0).

This slice (the clean-start adoption track's first apply unit) supplies the engine SKELETON only, shaped
by spec 1.3.0 sections 4.2, 5.7 and 14: run identity; the evidence-bundle, archive and Move homes, all
derived from the `_opf_store` homes constructors (never re-spelled here); the `opf.evidence.inventory/v1`
inventory DERIVED from the run's own transaction op list and published in that SAME transaction as the
retained bytes it lists (spec 4.2), a base `inventory.toml` then one `inventory-<phase>.toml` per later
phase, never rewritten; the homes-1 bundle verification the completion checks carry themselves while
C-EVIDENCE-ENUM is inactive (spec 14.1), which RE-READS the inventories and payload digests from disk;
the preserve-first composition of spec 14.2; live re-observation of every operand; one journaled
transaction per (run, phase), reconcile-first; and a dispatch table keyed by the closed twelve-op
ADOPT_OPS vocabulary in which EVERY op returns a refusing not-yet-executable verdict. No operation
executes: the file ops, init-store composition, trust verification, approval capture, hook activation,
rendering, receipt writing, the completion checks, retirement, and the CLI verb remain later slices.
Outside its own self-test fixtures this module is dead code until those slices land.

Preserve-first (spec 14.2), enforced over the composed op list BEFORE any transaction opens: a live file
is removed ONLY as the second half of a pair whose first half, earlier in the SAME transaction, creates
its byte-identical archive copy at `.working/archive/adoption/<run-id>/<source-path>`. The copy's create
op carries the plan digest as its content digest, which the journal verifies against the staged bytes and
fsyncs (file and parent) before the next op runs, and the removal carries the same digest and mode as a
`source-poststate` pin, which the journal verifies at preimage capture under its lock. So the copy is
verified and durably committed before the source is removed, never reversed and never split across
transactions, and a pre-commit abort (the journal's reverse-order rollback) restores the source from its
digest-checked preimage BEFORE it discards the copy. A source whose live bytes no longer match its plan
digest is drifted and is never archived or removed. A non-occupying source takes only its retirement
preimage at apply and stays frozen in place (its removal waits for the green completion check, a later
slice). The adoption archive and every evidence bundle are immutable: an op may only create beneath this
run's own archive, this run's own bundle, or the Move root, never write, remove, or rmdir anything under
`.working/archive/` or `.working/imported/`, and never create in another run's home.

The adoption journal root is `.aiqt/adopt/journal` at the PRODUCT root: the homes-1 legacy journal family
of `.aiqt/record/journal` and `.aiqt/import/journal`. It is anchored there, never under the store, because
recovery of an interrupted apply MUST resolve from the apply journal alone and MUST NOT require the live
tree to resolve as a store (spec 14.2): an apply that archived and removed an occupying manifest leaves no
store to resolve. reconcile() therefore never resolves the store. Homes 2 relocates transaction records
under `.working/journals/adoption/` and forbids OPF state under `.aiqt/` (spec 4.2, 14.2); homes 2 is not
activated in this build (SUPPORTED_HOMES = 1).

Journal discipline (reconcile-first, the `opf record` step-1 posture): every transaction inspects the
adoption journal FIRST and REFUSES on a held lock (never seized) or any open (INTENT-without-terminal)
transaction. Recovery is the EXPLICIT reconcile() entry: it rolls an open transaction forward when every
poststate already verifies, else back from its durable preimages (`_journal.recover`), under the journal's
own lock, breaking a confirmed-dead owner's lock only through `_journal.reconcile_and_claim_stale`; the
interrupted run stays refused. A transaction is named by its run (base) or run.phase, so one run has at
most one base transaction and one per phase; a second attempt refuses before it opens, and changing
approved work takes a fresh plan with its own run id (spec 14.1).

Single-writer lease (spec 5.7): this slice carries NO lease join, so a transaction REFUSES, before writing
anything, when the product root resolves a store (RESOLVED) or when a pointer names a store outside the
product root. A first adoption has no store and so no lease home; the pre-store single-writer control is
the coupled-init substrate's operation lock, which joins with the init-store slice. Disclosed residuals of
this slice, none of them a relaxation: bundle MEMBERSHIP (an off-inventory file inside a bundle) is not
reconciled here, only listed payloads; containment registration of the archive, Move and evidence homes on
homes 1 is part of the 1.3.0 activation, not this slice; investigation does not read this journal (the
planner's ancestry disclosure lists durable OPF history outside .working, and `.aiqt/adopt/journal` is one
more such home), so the stage driver's plan stage must refuse over a non-clean adoption journal through
journal_clean_or_refuse; the shipped `retire-file` vocabulary row is a single `remove`, while spec 1.3.0
preserves the retirement preimage at apply and removes only after the green check, a vocabulary split for
the op slices; interruption is exercised in-process through the journal's kill-point seam, and
subprocess kill-injection arrives with the first executable operations.

Outcome model: single-sourced from `_opf_store` exactly as the sibling `_opf_adopt` does; the inventory
grading is the doctor's own shared validator (`_opf_check._evidence_rows`), so a malformed inventory or a
path claimed twice is CANNOT-EVALUATE and the legacy ingest format is the named `legacy-ingest-inventory`
finding (spec 4.2). Filesystem entry points raise `AdoptApplyError` (a fail-closed refusal, exit 2).

Offline, stdlib only, fail-closed. It lives under `opf/tools/` and imports ONLY sibling `opf/tools/`
modules, so the standalone-closure property holds.

Exit convention (the repo's gates and the sibling OPF units): 0 clean, 1 a finding, 2 cannot-evaluate.
"""
import datetime
import hashlib
import os
import re
import stat
import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _journal              # noqa: E402
import _opf_adopt as schema  # noqa: E402
import _opf_store as store   # noqa: E402
from _opf_emit import EmitError, emit_checked  # noqa: E402

KIND = "adoption"
SESSION_ID = "opf-adopt"
OPERATION = "adopt-apply"
# The adoption journal root, at the PRODUCT root and outside `.working/` (see the module docstring).
JOURNAL_REL = ".aiqt/adopt/journal"
# The only plan format apply may take (spec 14.1); its schema lands in a later slice.
PLAN_V2_FORMAT = "opf.adoption.plan/v2"
DIR_MODE = 0o755
FILE_MODE = 0o644
_NONCE_RE = re.compile(r"^[0-9a-f]{16}\Z")
# The store-tree control homes this engine may create beneath and never rewrite (spec 4.2, 14.2).
_CONTROL_HOMES = (store.ARCHIVE_REL, store.IMPORTED_REL)
_MOVED_ROOT = store.ARCHIVE_REL + "/moved"


class AdoptApplyError(Exception):
    """A fail-closed refusal or cannot-evaluate carrying the operator-facing reason (mapped to exit 2)."""


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def _within(path, home):
    return path == home or path.startswith(home + "/")


# --- run identity (the homes grammar, _opf_store; `adopt-<YYYYMMDD>T<HHMMSS>Z-<hash16>`, spec 4.2) ----

def is_run_id(value):
    """True only for a string the homes constructors accept as an adoption run id (spec 4.2: lexical
    shape, not calendar validity). An import-family id, a traversing spelling, or any other malformed
    value is refused, never accepted."""
    try:
        store.evidence_run(KIND, value)
    except ValueError:
        return False
    return True


def mint_run_id(now, run_nonce):
    """Mint `adopt-<UTCSTAMP>Z-<hash16>` from an injected clock instant and nonce, then validate the
    result against the homes grammar so the mint can never drift from what the constructors accept.
    Identical inputs give a byte-identical id. Raises AdoptApplyError on a naive/non-UTC datetime or a
    nonce outside 16 lowercase hex characters (fail-closed, never a fabricated identity)."""
    if (type(now) is not datetime.datetime or type(now.tzinfo) is not datetime.timezone
            or now.utcoffset() != datetime.timedelta(0)):
        raise AdoptApplyError("now must be a clock-derived aware UTC datetime")
    if not isinstance(run_nonce, str) or not _NONCE_RE.match(run_nonce):
        raise AdoptApplyError("run_nonce must be 16 lowercase hex characters")
    run_id = "adopt-" + now.strftime("%Y%m%dT%H%M%SZ") + "-" + run_nonce
    if not is_run_id(run_id):
        raise AdoptApplyError("minted run id {!r} does not match the adoption run-id grammar "
                              "(fail-closed)".format(run_id))
    return run_id


# --- homes paths, derived from the _opf_store constructors (spec 4.2) ---------------------------------

def evidence_home_rel(run_id):
    """`.working/imported/adoption/<run-id>`, the adoption evidence bundle (spec 4.2, 14.2)."""
    try:
        return store.evidence_run(KIND, run_id)
    except ValueError as exc:
        raise AdoptApplyError("evidence home: {}".format(exc))


def inventory_rel(run_id, phase=None):
    """The bundle-root inventory: `inventory.toml`, or `inventory-<phase>.toml` for a later phase."""
    try:
        return store.evidence_inventory(KIND, run_id, phase)
    except ValueError as exc:
        raise AdoptApplyError("evidence inventory: {}".format(exc))


def archive_rel(run_id, source_path):
    """`.working/archive/adoption/<run-id>/<source-path>`: the retirement preimage of a non-occupying
    source, or the archived copy of an occupying one (spec 14.2)."""
    try:
        return store.retire_preimage(run_id, source_path)
    except ValueError as exc:
        raise AdoptApplyError("adoption archive: {}".format(exc))


def _txn_name(run_id, phase):
    inventory_rel(run_id, phase)   # validates both the run id and the phase spelling
    return run_id if phase is None else "{}.{}".format(run_id, phase)


def _plan_hex(plan_digest):
    if not schema._is_digest(plan_digest):
        raise AdoptApplyError("plan digest {!r} is not a sha256:<64 hex> digest".format(plan_digest))
    return plan_digest[len("sha256:"):]


# --- live re-observation (investigation is not a snapshot; "Re-observe at apply") ---------------------

def _read_live(root_fd, relpath):
    """(fstat, bytes) of one contained, regular, single-linked live file, or (None, None) when absent. A
    symlink, special file, multiply-linked file, or non-contained spelling refuses."""
    if not schema._is_contained_filepath(relpath):
        raise AdoptApplyError("operand {!r} is not a contained relative file path".format(relpath))
    try:
        st = _journal._lstat_contained(root_fd, relpath)
    except (_journal.JournalError, OSError) as exc:
        raise AdoptApplyError("cannot observe {!r} ({}); fail-closed".format(relpath, exc))
    if st is None:
        return None, None
    if not stat.S_ISREG(st.st_mode):
        raise AdoptApplyError("operand {!r} is not a regular file (a symlink or special entry is "
                              "refused)".format(relpath))
    try:
        data, fst = _journal._read_contained(root_fd, relpath, require_single_link=True)
    except (_journal.JournalError, OSError) as exc:
        raise AdoptApplyError("cannot read {!r} contained ({}); fail-closed".format(relpath, exc))
    return fst, data


def observe_live(root_fd, relpath):
    """Re-observe ONE operand's live state at apply time: absent, or a regular file's mode, size and
    bare-hex sha256. The op slices call this immediately before composing each op, so a preimage or
    absence precondition is checked against the LIVE tree, never the investigation inventory; drift
    grading is each op's own precondition, not this observer's."""
    fst, data = _read_live(root_fd, relpath)
    if fst is None:
        return dict(kind="absent")
    return dict(kind="file", mode=stat.S_IMODE(fst.st_mode), size=len(data), sha256=_sha256(data))


# --- the evidence inventory (spec 4.2): graded by the doctor's own shared validator -------------------

def validate_inventory(doc, run_id):
    """Grade ONE parsed inventory of the adoption bundle of `run_id` exactly as C-EVIDENCE-ENUM does,
    through `_opf_check._evidence_rows`: VALID; the legacy ingest format is the named
    `legacy-ingest-inventory` finding (INVALID); every malformed input, a path claimed twice included,
    is CANNOT-EVALUATE (spec 4.2). Both non-VALID verdicts are refusing."""
    import _opf_check
    if not is_run_id(run_id):
        return schema._cannot("inventory scope run id {!r} does not match the adoption "
                              "grammar".format(run_id))
    try:
        rows = _opf_check._evidence_rows(evidence_home_rel(run_id), KIND, run_id, doc)
        seen = set()
        for row in rows:
            if row["path"] in seen:
                raise ValueError("{!r} is claimed more than once".format(row["path"]))
            seen.add(row["path"])
    except _opf_check._LegacyIngestInventory as exc:
        return schema._invalid([str(exc)])
    except (TypeError, ValueError, KeyError) as exc:
        return schema._cannot("malformed inventory: {}".format(exc))
    return schema._ok()


def inventory_row(path, data):
    """One inventory row for retained bytes at a store-relative path (spec 4.2 row shape)."""
    return dict(path=path, size=len(data), sha256=_sha256(data))


def emit_inventory(run_id, rows):
    """Canonical inventory bytes for the adoption bundle of `run_id`: rows sorted by path, validated
    fail-closed FIRST so a malformed row can never reach bytes. Raises AdoptApplyError on any refusal."""
    if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
        raise AdoptApplyError("inventory rows must be a list of tables")
    doc = dict(format=store.EVIDENCE_INVENTORY_FORMAT,
               file=sorted((dict(r) for r in rows), key=lambda r: str(r.get("path"))))
    checked = validate_inventory(doc, run_id)
    if checked.status != store.VALID:
        raise AdoptApplyError("inventory refused: {}".format("; ".join(checked.findings)))
    try:
        return emit_checked(doc).encode("utf-8")
    except EmitError as exc:
        raise AdoptApplyError("inventory cannot be emitted canonically ({}); fail-closed".format(exc))


def _open_product_root(product_root):
    if not isinstance(product_root, (str, os.PathLike)):
        raise AdoptApplyError("product_root must be an absolute path")
    root = Path(product_root)
    if not root.is_absolute() or ".." in root.parts:
        raise AdoptApplyError("product_root must be absolute and contain no '..'")
    try:
        return store._open_dir_nofollow(root)
    except OSError as exc:
        raise AdoptApplyError("cannot open product root {!r} contained ({}); "
                              "fail-closed".format(str(root), exc))


def verify_bundle(product_root, run_id):
    """Homes-1 evidence verification carried by the completion checks (spec 14.1, 4.2): RE-READ every
    bundle-root inventory of this adoption run from disk, then every payload those inventories list,
    contained and single-linked. Read-only. A missing bundle, a bundle with no inventory, a missing listed
    file, a listed entry that is not a regular file, and a size or digest mismatch are findings (INVALID),
    as is the legacy ingest format; a phase inventory without inventory.toml, an unreadable or malformed
    inventory, a path claimed twice across the bundle's inventories, and an unreadable payload are
    CANNOT-EVALUATE. Bundle membership (an unlisted file) is not reconciled here."""
    if not is_run_id(run_id):
        return schema._cannot("bundle run id {!r} does not match the adoption grammar".format(run_id))
    root_fd = _open_product_root(product_root)
    try:
        return _verify_bundle_at(root_fd, run_id, evidence_home_rel(run_id))
    finally:
        os.close(root_fd)


def _verify_bundle_at(root_fd, run_id, bundle):
    try:
        st = _journal._lstat_contained(root_fd, bundle)
        if st is None:
            return schema._invalid(["evidence bundle {!r} is missing".format(bundle)])
        if not stat.S_ISDIR(st.st_mode):
            return schema._cannot("evidence bundle {!r} is not a directory".format(bundle))
        dfd = _journal._open_dir_contained(root_fd, bundle)
        try:
            names = sorted(os.listdir(dfd))
        finally:
            os.close(dfd)
    except (_journal.JournalError, OSError) as exc:
        return schema._cannot("cannot list evidence bundle {!r} ({})".format(bundle, exc))
    inventories = [name for name in names if store.is_evidence_inventory_name(name)]
    if not inventories:
        return schema._invalid(["evidence bundle {!r} has no inventory".format(bundle)])
    if "inventory.toml" not in inventories:
        return schema._cannot("evidence bundle {!r} has a phase inventory but no inventory.toml; a "
                              "phase inventory never stands in for it (spec 4.2)".format(bundle))
    expected = {}
    for name in inventories:
        rel = bundle + "/" + name
        try:
            raw, _fst = _journal._read_contained(root_fd, rel, require_single_link=True)
            doc = tomllib.loads(raw.decode("utf-8"))
        except (_journal.JournalError, OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
            return schema._cannot("inventory {!r} is unreadable or unparseable ({})".format(rel, exc))
        checked = validate_inventory(doc, run_id)
        if checked.status != store.VALID:
            return schema.AdoptValidation(checked.status, [
                "inventory {!r}: {}".format(rel, f) for f in checked.findings])
        for row in doc["file"]:
            if row["path"] in expected:
                return schema._cannot("{!r} is claimed by more than one inventory of bundle "
                                      "{!r}".format(row["path"], bundle))
            expected[row["path"]] = row
    findings = []
    for path in sorted(expected):
        row = expected[path]
        try:
            pst = _journal._lstat_contained(root_fd, path)
            if pst is None:
                findings.append("listed file {!r} is missing".format(path))
                continue
            if not stat.S_ISREG(pst.st_mode):
                findings.append("listed entry {!r} is not a regular file".format(path))
                continue
            data, _fst = _journal._read_contained(root_fd, path, require_single_link=True)
        except (_journal.JournalError, OSError) as exc:
            return schema._cannot("cannot read listed file {!r} ({})".format(path, exc))
        if len(data) != row["size"] or _sha256(data) != row["sha256"]:
            findings.append("listed file {!r} does not match its recorded size and sha256 (payload "
                            "drift)".format(path))
    return schema._ok() if not findings else schema._invalid(findings)


# --- composition: preserve-first, derived inventories, immutable homes (spec 4.2, 14.2) ---------------

def _archive_root(run_id):
    return "{}/{}".format(store.ARCHIVE_REL, store._home_run(KIND, run_id))


def _bundle_root_inventory(run_id, path):
    bundle = evidence_home_rel(run_id)
    member = path[len(bundle) + 1:] if path.startswith(bundle + "/") else None
    return member is not None and "/" not in member and store.is_evidence_inventory_name(member)


def _evidence_eligible(run_id, path):
    """Whether a created path is retained evidence an inventory row claims (spec 4.2): a member of this
    run's bundle other than a bundle-root inventory, this run's archive, or a default Move destination."""
    if _within(path, evidence_home_rel(run_id)):
        return not _bundle_root_inventory(run_id, path)
    return _within(path, _archive_root(run_id)) or _within(path, _MOVED_ROOT)


def derive_rows(run_id, ops, staged):
    """The inventory rows of one transaction, DERIVED from its own op list and staged bytes, never
    supplied by a caller: one row per retained-evidence create."""
    rows = []
    for op in ops:
        if op.get("op") == "create" and _evidence_eligible(run_id, op["path"]):
            data = staged.get(op["path"])
            if not isinstance(data, bytes):
                raise AdoptApplyError("no staged bytes for evidence {!r}".format(op["path"]))
            rows.append(inventory_row(op["path"], data))
    return rows


def _pinned_remove(rel, data, mode):
    """Remove a file only while it still holds the verified bytes and mode; the journal verifies the pin
    at capture, under its lock, before it takes the preimage (the _opf_ingest_apply idiom)."""
    return dict(op="remove", path=rel, poststate=dict(kind="absent"),
                **{"source-poststate": dict(kind="file", mode=mode, sha256=_sha256(data))})


class ApplyOps:
    """ONE adoption transaction's ordered op list and staged bytes, composed only through these methods
    against the live tree beneath `root_fd`: create-only publication, preserve-first archival, and a
    final inventory derived from the list itself. check_apply_ops re-proves every invariant over the
    finished list, so a hand-built list is held to the same rules."""

    def __init__(self, root_fd, run_id, phase=None):
        self.root_fd = root_fd
        self.run_id = run_id
        self.phase = phase
        self.inventory = inventory_rel(run_id, phase)
        self.ops = []
        self.staged = {}
        self._dirs = set()
        self.sealed = False

    def _mkdirs(self, rel):
        parts = rel.split("/")[:-1]
        for i in range(1, len(parts) + 1):
            d = "/".join(parts[:i])
            if d in self._dirs:
                continue
            try:
                st = _journal._lstat_contained(self.root_fd, d)
            except (_journal.JournalError, OSError) as exc:
                raise AdoptApplyError("cannot inspect {!r} ({}); fail-closed".format(d, exc))
            if st is None:
                self.ops.append(dict(op="mkdir", path=d, poststate=dict(kind="dir", mode=DIR_MODE)))
            elif not stat.S_ISDIR(st.st_mode):
                raise AdoptApplyError("path component {!r} exists and is not a directory".format(d))
            self._dirs.add(d)

    def create(self, rel, data, mode=FILE_MODE):
        """Exclusive create of planned bytes at an absent path; an occupied path refuses (a collision
        routes to a disposition, never an overwrite)."""
        if self.sealed:
            raise AdoptApplyError("the transaction is sealed by its inventory; nothing may follow it")
        if not schema._is_contained_filepath(rel) or not isinstance(data, bytes) or rel in self.staged:
            raise AdoptApplyError("create needs a fresh contained path and bytes, not {!r}".format(rel))
        fst, _data = _read_live(self.root_fd, rel)
        if fst is not None:
            raise AdoptApplyError("{!r} is occupied; a collision routes to a disposition, never an "
                                  "overwrite (an inventory is never rewritten)".format(rel))
        self._mkdirs(rel)
        self.ops.append(dict(op="create", path=rel,
                             poststate=dict(kind="file", mode=mode, **{"content-sha256": _sha256(data)})))
        self.staged[rel] = data

    def preserve(self, source_path, plan_digest):
        """Preserve one plan-enumerated source at apply: re-observe its live bytes, require them to equal
        the plan digest (a drifted source MUST NOT be archived, spec 14.2), and create the byte-identical
        copy at `.working/archive/adoption/<run-id>/<source-path>`. The source itself stays live and
        frozen: a non-occupying source's retirement preimage. Returns (bytes, mode)."""
        expected = _plan_hex(plan_digest)
        fst, data = _read_live(self.root_fd, source_path)
        if fst is None:
            raise AdoptApplyError("plan-enumerated source {!r} is absent at apply; a fresh plan with its "
                                  "own approval is the remedy (spec 14.1)".format(source_path))
        if _sha256(data) != expected:
            raise AdoptApplyError("source {!r} is drifted: its live bytes no longer match its plan digest, "
                                  "so it MUST NOT be archived, retired, moved or removed; a fresh plan "
                                  "with its own approval is the remedy (spec 14.2)".format(source_path))
        self.create(archive_rel(self.run_id, source_path), data)
        return data, stat.S_IMODE(fst.st_mode)

    def archive_occupying(self, source_path, plan_digest):
        """Resolve one occupied destination preserve-first (spec 14.2): the verified archive copy's create
        op, then the pinned removal of the live source, in THIS transaction and in that order."""
        data, mode = self.preserve(source_path, plan_digest)
        self.ops.append(_pinned_remove(source_path, data, mode))

    def seal(self):
        """Create this transaction's inventory, derived from its own op list, as its final op."""
        data = emit_inventory(self.run_id, derive_rows(self.run_id, self.ops, self.staged))
        self.create(self.inventory, data)
        self.sealed = True


def check_apply_ops(run_id, phase, ops, staged):
    """Re-prove the shell's invariants over ONE finished op list, pure and before any transaction
    opens; returns the findings (empty means admissible). Preserve-first: every removal carries a pinned
    digest and mode and follows, in this same list, the create of its archive copy with that same digest
    (spec 14.2). Immutable homes: under `.working/archive/` and `.working/imported/` only a create beneath
    this run's own archive, own bundle, or the Move root (and the mkdirs leading there) is allowed, never
    a write, remove, or rmdir, and never another run's home (spec 14.2, 4.2). One op per path. The final
    op, and the only bundle-root inventory, is this transaction's own inventory, byte-equal to the
    inventory derived from the list's retained bytes (spec 4.2)."""
    target = inventory_rel(run_id, phase)
    roots = (evidence_home_rel(run_id), _archive_root(run_id), _MOVED_ROOT)
    if not isinstance(ops, list) or not ops or not isinstance(staged, dict):
        return ["the transaction carries no op list"]
    findings = []
    seen = set()
    created = {}
    for i, op in enumerate(ops):
        where = "op[{}]".format(i)
        if (not isinstance(op, dict) or op.get("op") not in _journal.OP_KINDS
                or not schema._is_contained_filepath(op.get("path"))):
            findings.append("{} is not a contained journal op".format(where))
            continue
        kind, path = op["op"], op["path"]
        if path in seen:
            findings.append("{} touches {!r} a second time (one op per path)".format(where, path))
        seen.add(path)
        if any(_within(path, home) for home in _CONTROL_HOMES):
            if kind == "create" and not any(_within(path, r) and path != r for r in roots):
                findings.append("{} creates {!r} outside this run's own archive, own evidence bundle and "
                                "the Move root; another run's home is immutable".format(where, path))
            elif kind == "mkdir" and not any(_within(path, r) or _within(r, path) for r in roots):
                findings.append("{} creates directory {!r} outside this run's own homes".format(where, path))
            elif kind not in ("create", "mkdir"):
                findings.append("{} would {} {!r}: the adoption archive and evidence bundles are immutable "
                                "(spec 14.2, 4.2); only a create beneath this run's own homes is "
                                "allowed".format(where, kind, path))
        if kind == "create":
            digest = (op.get("poststate") or {}).get("content-sha256")
            data = staged.get(path)
            if not isinstance(data, bytes) or _sha256(data) != digest:
                findings.append("{} create {!r} has no staged bytes matching its content digest".format(
                    where, path))
            created[path] = digest
        elif kind == "remove":
            pin = op.get("source-poststate")
            if not (isinstance(pin, dict) and pin.get("kind") == "file" and isinstance(pin.get("sha256"), str)
                    and isinstance(pin.get("mode"), int)):
                findings.append("{} removes {!r} without a pinned file digest and mode "
                                "(source-poststate)".format(where, path))
            elif archive_rel(run_id, path) not in created:
                findings.append("{} removes {!r} before, or without, its archive copy earlier in this "
                                "transaction: preserve-first, verify then remove, never reversed or split "
                                "(spec 14.2)".format(where, path))
            elif created.get(archive_rel(run_id, path)) != pin["sha256"]:
                findings.append("{} removes {!r} whose pinned digest differs from its archive copy's "
                                "content digest".format(where, path))
    inventories = [i for i, op in enumerate(ops)
                   if isinstance(op, dict) and op.get("op") == "create" and isinstance(op.get("path"), str)
                   and _bundle_root_inventory(run_id, op["path"])]
    if [ops[i]["path"] for i in inventories] != [target]:
        findings.append("the transaction must publish exactly its own inventory {!r} and no other "
                        "bundle-root inventory".format(target))
    elif inventories[0] != len(ops) - 1:
        findings.append("the inventory {!r} must be the final op, after every retained byte it "
                        "lists".format(target))
    else:
        try:
            derived = emit_inventory(run_id, derive_rows(run_id, ops[:-1], staged))
        except (AdoptApplyError, KeyError, TypeError, AttributeError) as exc:
            findings.append("no inventory can be derived from this transaction ({})".format(exc))
        else:
            if staged.get(target) != derived:
                findings.append("inventory {!r} is not the one derived from this transaction's own "
                                "retained bytes (spec 4.2: derived from the run's transaction record and "
                                "published with the bytes it lists)".format(target))
    return findings


# --- the journaled transaction shell (reconcile-first; `.aiqt/adopt/journal`) --------------------------

def _journal_root(product_root):
    return Path(product_root) / JOURNAL_REL


def journal_clean_or_refuse(root_fd, journal_root):
    """The reconcile-first discipline (`opf record` step 1): inspect the adoption journal READ-ONLY and
    REFUSE on a held lock (a possibly-live owner is never seized) or any open transaction, directing the
    operator to the explicit reconcile() entry. An absent journal root is clean; an unreadable or corrupt
    journal refuses. Nothing is written here, recovery included."""
    try:
        st = _journal._lstat_contained(root_fd, JOURNAL_REL)
    except (_journal.JournalError, OSError) as exc:
        raise AdoptApplyError("cannot inspect the adoption journal {} ({}); "
                              "fail-closed".format(JOURNAL_REL, exc))
    if st is None:
        return
    if not stat.S_ISDIR(st.st_mode):
        raise AdoptApplyError("the adoption journal {} is not a directory; fail-closed".format(JOURNAL_REL))
    try:
        jr_fd = _journal.open_journal_root_fd(root_fd, JOURNAL_REL)
    except (_journal.JournalError, OSError) as exc:
        raise AdoptApplyError("cannot open the adoption journal {} ({}); "
                              "fail-closed".format(JOURNAL_REL, exc))
    try:
        try:
            owner = _journal.read_lock_owner(journal_root)
            txns = _journal._journal_txn_dirs(jr_fd, journal_root)
            opened = sorted(t.name for t in txns if _journal.classify_state(jr_fd, t) == "open")
        except (_journal.JournalError, OSError) as exc:
            raise AdoptApplyError("the adoption journal {} cannot be read ({}); "
                                  "fail-closed".format(JOURNAL_REL, exc))
    finally:
        _journal._close_fd_quietly(jr_fd)
    if owner is not None:
        raise AdoptApplyError("the adoption journal lock is held (pid {}); it is never seized. "
                              "Reconcile once no adoption run is live (fail-closed)".format(owner.get("pid")))
    if opened:
        raise AdoptApplyError("interrupted adoption transaction(s) {} must be reconciled before this run "
                              "(run reconcile()); nothing was written (fail-closed)".format(", ".join(opened)))


def reconcile(product_root):
    """The EXPLICIT recovery step, never an implicit side effect of a run: reconcile every adoption
    journal transaction to a terminal state from the journal ALONE (spec 14.2: the live tree is never
    required to resolve as a store, and this function never resolves it), rolling an open one FORWARD
    when every poststate already verifies, else BACK from its durable preimages, under the journal's own
    lock. A lock held by a possibly-live owner refuses (never seized); a confirmed-dead owner's lock is
    broken only through `_journal.reconcile_and_claim_stale`. Returns the (transaction, outcome) pairs.
    The interrupted run itself stays refused, so the operator inspects the reconciled tree first."""
    root_fd = _open_product_root(product_root)
    journal_root = _journal_root(product_root)
    outcomes = []
    try:
        try:
            _journal.require_containment()
            st = _journal._lstat_contained(root_fd, JOURNAL_REL)
            if st is None:
                return outcomes
            if not stat.S_ISDIR(st.st_mode):
                raise AdoptApplyError("the adoption journal {} is not a directory; "
                                      "fail-closed".format(JOURNAL_REL))
            jr_fd = _journal.open_journal_root_fd(root_fd, JOURNAL_REL)
            try:
                owner = _journal.read_lock_owner(journal_root)
                txns = _journal._journal_txn_dirs(jr_fd, journal_root)
                opened = sorted(t.name for t in txns if _journal.classify_state(jr_fd, t) == "open")
                if owner is None and not opened:
                    return outcomes
                if owner is not None and not _journal.owner_confirmed_dead(owner):
                    raise AdoptApplyError("the adoption journal lock is held by a possibly-live owner "
                                          "(pid {}); it is never seized (fail-closed)".format(owner.get("pid")))
                if owner is not None:
                    if _journal.reconcile_and_claim_stale(journal_root, jr_fd, root_fd,
                                                          SESSION_ID) != "acquired":
                        raise AdoptApplyError("the adoption journal lock became live during "
                                              "reconciliation; it is never seized (fail-closed)")
                else:
                    _journal.acquire_lock(journal_root, SESSION_ID)
                try:
                    for txn in txns:
                        outcomes.append((txn.name, _journal.recover(jr_fd, txn, root_fd)))
                finally:
                    _journal.release_lock(journal_root)
            finally:
                _journal._close_fd_quietly(jr_fd)
        except (_journal.JournalError, OSError) as exc:
            raise AdoptApplyError("the adoption journal {} cannot be reconciled ({}); "
                                  "fail-closed".format(JOURNAL_REL, exc))
    finally:
        os.close(root_fd)
    return outcomes


def _store_posture_or_refuse(product_root):
    """Spec 5.7: a run MUST hold the single-writer lease before mutating a store, and this slice has no
    lease join. A product root that resolves a store therefore refuses, as does a pointer naming a store
    outside the product root. NOT-ADOPTED and a foreign `.working/` without a manifest (the first-adoption
    states adoption exists for) have no store and so no lease home."""
    res = store.resolve_store(product_root)
    if res.status == store.RESOLVED:
        raise AdoptApplyError("the product root resolves a store ({}); this apply shell carries no "
                              "single-writer lease join yet (spec 5.7), so it refuses before writing "
                              "(fail-closed)".format(res.machine_rel))
    if res.pointer_source not in (None, "default") and (
            res.store_root is None or Path(os.path.abspath(res.store_root)) != Path(product_root)):
        raise AdoptApplyError("a pointer ({}) names a store outside the product root; adoption evidence "
                              "and archives belong at that store root, which this slice does not "
                              "support (fail-closed)".format(res.pointer_source))


def run_adopt_transaction(product_root, run_id, compose, phase=None):
    """ONE journaled adoption transaction, the run's base transaction or one later phase's, through the
    shared 9.3 engine. Refusals BEFORE anything is written, in order: containment, the store posture
    (no lease join, spec 5.7), a non-clean journal (reconcile-first), an existing transaction of this
    run and phase, and for a phase a missing committed inventory.toml (spec 4.2). Then, under the journal
    lock so observation and the journal's own capture are contiguous, compose(ops) fills a fresh ApplyOps
    against the live tree, the derived inventory seals it, and check_apply_ops re-proves every invariant;
    a refusal there releases the lock with nothing written beyond the journal directories. A failure that
    may have left the transaction open RETAINS the lock so every later run refuses into reconcile().
    Returns the transaction name."""
    txn = _txn_name(run_id, phase)
    if not callable(compose):
        raise AdoptApplyError("compose must be a callable that fills the transaction's ApplyOps")
    root_fd = _open_product_root(product_root)
    journal_root = _journal_root(product_root)
    jr_fd = None
    try:
        try:
            _journal.require_containment()
        except _journal.JournalError as exc:
            raise AdoptApplyError("{} (fail-closed)".format(exc))
        _store_posture_or_refuse(product_root)
        journal_clean_or_refuse(root_fd, journal_root)
        try:
            prior = _journal._lstat_contained(root_fd, JOURNAL_REL + "/" + txn)
        except (_journal.JournalError, OSError) as exc:
            raise AdoptApplyError("cannot inspect the adoption journal ({}); fail-closed".format(exc))
        if prior is not None:
            raise AdoptApplyError("run {} already has its transaction {!r}: one run takes one transaction "
                                  "per phase, and changing approved work takes a fresh plan with its own run "
                                  "id (spec 14.1); nothing written (fail-closed)".format(run_id, txn))
        if phase is not None and _read_live(root_fd, inventory_rel(run_id))[0] is None:
            raise AdoptApplyError("phase {!r} needs the run's committed inventory.toml; a phase inventory "
                                  "never stands in for it (spec 4.2)".format(phase))
        try:
            _journal.ensure_journal_dirs(root_fd, JOURNAL_REL)
            jr_fd = _journal.open_journal_root_fd(root_fd, JOURNAL_REL)
        except (_journal.JournalError, OSError) as exc:
            raise AdoptApplyError("cannot prepare the adoption journal {} ({}); nothing "
                                  "written (fail-closed)".format(JOURNAL_REL, exc))
        held = retain = False
        try:
            try:
                _journal.acquire_lock(journal_root, SESSION_ID)
            except _journal.JournalError as exc:
                raise AdoptApplyError("cannot take the adoption journal lock ({}); nothing "
                                      "written (fail-closed)".format(exc))
            held = True
            ops = ApplyOps(root_fd, run_id, phase)
            compose(ops)
            ops.seal()
            findings = check_apply_ops(run_id, phase, ops.ops, ops.staged)
            if findings:
                raise AdoptApplyError("the composed transaction is refused before it opens: {}".format(
                    "; ".join(findings)))
            staged = dict(ops.staged)

            def staged_reader(op):
                data = staged.get(op["path"])
                if not isinstance(data, bytes):
                    # raised INSIDE apply, so the engine rolls back rather than leaving an open transaction.
                    raise _journal.JournalError("no staged bytes for {!r} (fail-closed)".format(op["path"]))
                return data

            header = dict(kind=KIND, run_id=run_id, phase=phase or "base", operation=OPERATION)
            try:
                _journal.run_transaction(root_fd, jr_fd, journal_root, txn, header, ops.ops,
                                         staged_reader, SESSION_ID)
            except (_journal.JournalError, OSError) as exc:
                # An absent transaction directory reads as nothing-opened (read_frames), so a failure
                # before INTENT is told apart from one after it (the record-publication precedent).
                try:
                    state = _journal.classify_state(jr_fd, journal_root / txn)
                except _journal.JournalError:
                    state = None
                if state == "nothing-opened":
                    raise AdoptApplyError("the adoption transaction was refused before it opened ({}); "
                                          "nothing written (fail-closed)".format(exc))
                if state == "rolled-back":
                    raise AdoptApplyError("the adoption transaction was refused and rolled back to the "
                                          "prestate ({}); nothing written (fail-closed)".format(exc))
                retain = True
                raise AdoptApplyError("the adoption transaction {} FAILED and is {} ({}); the journal lock "
                                      "is retained so the next run refuses into reconcile() "
                                      "(fail-closed)".format(txn, state or "in an unreadable state", exc))
            return txn
        finally:
            if held and not retain:
                try:
                    _journal.release_lock(journal_root)
                except (_journal.JournalError, OSError):
                    pass   # a leftover lock refuses the next run into reconcile(), never a silent seize
    finally:
        if jr_fd is not None:
            _journal._close_fd_quietly(jr_fd)
        os.close(root_fd)


# --- the dispatch table: EVERY op refuses not-yet-executable in this slice -----------------------------

def _not_yet_executable(op_row, context=None):
    """The one slice-1 handler behind every dispatch entry: a refusing not-yet-executable verdict. Later
    slices replace individual OP_HANDLERS entries with real executors; the self-test pins every entry to
    THIS handler and every canonical row to a refusing status, so a silently-enabled op is a red."""
    name = op_row.get("op") if isinstance(op_row, dict) else None
    return schema.AdoptValidation(store.CANNOT_EVALUATE, [
        "op {!r} is not yet executable in this build; a later adoption slice lands it "
        "(fail-closed)".format(name)])


# Keyed by the closed ADOPT_OPS vocabulary; the self-test reconciles this table against
# ADOPT_OPS_BY_NAME in BOTH directions so it can neither drop nor invent an op.
OP_HANDLERS = {
    "install-pack": _not_yet_executable,
    "init-store": _not_yet_executable,
    "create-file": _not_yet_executable,
    "plant-governance": _not_yet_executable,
    "import-file": _not_yet_executable,
    "register-unmanaged": _not_yet_executable,
    "move-file": _not_yet_executable,
    "repoint-consumer": _not_yet_executable,
    "retire-file": _not_yet_executable,
    "enable-hook": _not_yet_executable,
    "render-views": _not_yet_executable,
    "record-adoption": _not_yet_executable,
}


def dispatch(op_row, context=None):
    """Validate, then dispatch ONE plan op row. A malformed row propagates the validator's refusing
    verdict; an op with no registered handler (dispatch-roster drift) is CANNOT-EVALUATE, never a skip.
    In this slice every handler refuses, so dispatch never has a side effect."""
    checked = schema.validate_op(op_row)
    if checked.status != store.VALID:
        return checked
    handler = OP_HANDLERS.get(op_row["op"])
    if handler is None:
        return schema.AdoptValidation(store.CANNOT_EVALUATE, [
            "op {!r} has no registered handler (dispatch roster drift; fail-closed)".format(op_row["op"])])
    return handler(op_row, context)


def apply_plan(plan_doc):
    """The slice-1 apply entry, pure and write-free. Spec 14.1 binds apply to an approved
    `opf.adoption.plan/v2` plan, so any other format, the shipped v1 schema included, is refused as
    apply input; v2 validation and the approval binding land in a later slice, so a v2-marked plan is
    refused too. VALID is unreachable in this build."""
    if not isinstance(plan_doc, dict):
        return schema._cannot("adoption plan is not a table")
    if plan_doc.get("format") != PLAN_V2_FORMAT:
        return schema._cannot("apply takes only an approved {} plan (spec 14.1); {!r} binds none of the "
                              "v2 roster, so it is never apply input (fail-closed)".format(
                                  PLAN_V2_FORMAT, plan_doc.get("format")))
    return schema._cannot("{} validation and the approval binding land in a later adoption slice; apply "
                          "refuses (fail-closed)".format(PLAN_V2_FORMAT))


# --- self-test -----------------------------------------------------------------------------------------

def self_test():
    """Fail-closed invariants over synthetic vectors and throwaway temporary fixtures, judged on returned
    statuses, byte comparisons and journal states; a refusal check also matches one reason keyword so the
    refusal is attributed to the rule under test. No git, no network, no subprocess; every write lands
    under its own TemporaryDirectory."""
    try:
        _journal.require_containment()
    except _journal.JournalError as exc:
        print("OPF-ADOPT-APPLY SELF-TEST: containment unavailable ({}); cannot evaluate".format(exc),
              file=sys.stderr)
        return 2
    try:
        return _self_test_checks()
    except Exception as exc:  # noqa: BLE001  final fail-closed backstop, never an uncaught exit-1 escape
        print("OPF-ADOPT-APPLY SELF-TEST: harness error ({!r}); failing closed to exit 2".format(exc),
              file=sys.stderr)
        return 2


def _self_test_checks():
    import tempfile
    from unittest import mock

    failures = []
    checked = [0]

    def check(name, cond):
        checked[0] += 1
        if not cond:
            failures.append(name)

    def refusal(fn, *args, **kwargs):
        """The refusal text when fn refuses with AdoptApplyError, else None."""
        return attempt(fn, *args, **kwargs)[1]

    def attempt(fn, *args, **kwargs):
        """(result, None) on success, (None, refusal text) on AdoptApplyError, so a refused step is a
        recorded check failure rather than an escape that would mask the checks after it."""
        try:
            return fn(*args, **kwargs), None
        except AdoptApplyError as exc:
            return None, str(exc)

    ZERO = "0" * 64
    now = datetime.datetime(2026, 9, 17, 12, 0, 0, tzinfo=datetime.timezone.utc)
    VALID, INVALID, CANNOT = store.VALID, store.INVALID, store.CANNOT_EVALUATE

    # 0: the dispatch roster reconciles against the closed vocabulary in BOTH directions, every entry is
    # pinned to the refusing handler, and every canonical row refuses. A silently-enabled op is a red; the
    # slice that legitimately lands an op updates these pins in the same change.
    check("handlers-cover-vocabulary", set(OP_HANDLERS) == set(schema.ADOPT_OPS_BY_NAME))
    check("handlers-all-refusing", all(h is _not_yet_executable for h in OP_HANDLERS.values()))
    for name in sorted(schema.ADOPT_OP_NAMES):
        res = dispatch(schema.canonical_op(name))
        check("op-{}-refuses-not-yet-executable".format(name),
              res.status == CANNOT and any("not yet executable" in f for f in res.findings))
    check("dispatch-out-of-vocab-cannot-eval", dispatch(dict(op="delete-everything")).status == CANNOT)
    check("dispatch-malformed-row-invalid", dispatch(dict(op="create-file", path="a/b")).status == INVALID)

    # 1: run identity. The homes grammar and the schema's shipped grammar agree on every vector; the mint
    # validates its own output; the import family and traversal spellings are refused.
    rid = mint_run_id(now, "0123456789abcdef")
    other_run = mint_run_id(now, "fedcba9876543210")
    check("mint-run-id-grammar", is_run_id(rid) and rid == "adopt-20260917T120000Z-0123456789abcdef")
    check("mint-run-id-deterministic", mint_run_id(now, "0123456789abcdef") == rid)
    check("mint-naive-now-refused", refusal(mint_run_id, now.replace(tzinfo=None), "0123456789abcdef"))
    check("mint-bad-nonce-refused", refusal(mint_run_id, now, "XYZ"))
    vectors = (rid, "imp-20260917T120000Z-0123456789abcdef", "adopt-20260917T120000Z-../escapes/xx",
               "adopt-20260917T120000Z-0123456789ABCDEF", "adopt-2026091T120000Z-0123456789abcdef", 7, None)
    check("run-id-store-and-schema-grammars-agree",
          all(is_run_id(v) == (isinstance(v, str) and bool(schema._RUN_ID_RE.match(v))) for v in vectors))
    check("run-id-import-family-rejected", not is_run_id("imp-20260917T120000Z-0123456789abcdef"))
    check("run-id-traversal-rejected", not is_run_id("adopt-20260917T120000Z-../escapes/xx"))

    # 2: every home comes from the _opf_store constructors, pinned to the spec 4.2 spellings.
    home = evidence_home_rel(rid)
    check("homes-evidence-from-store",
          home == store.evidence_run("adoption", rid) == ".working/imported/adoption/" + rid)
    check("homes-inventory-from-store",
          inventory_rel(rid) == store.evidence_inventory("adoption", rid) == home + "/inventory.toml"
          and inventory_rel(rid, "completion") == home + "/inventory-completion.toml")
    check("homes-archive-from-store",
          archive_rel(rid, "a/b.md") == store.retire_preimage(rid, "a/b.md")
          == ".working/archive/adoption/" + rid + "/a/b.md")
    check("homes-bad-phase-refused", refusal(inventory_rel, rid, "Bad"))
    check("homes-bad-run-refused", refusal(evidence_home_rel, "imp-20260917T120000Z-0123456789abcdef"))

    # 3: inventory grading is the doctor's (spec 4.2): emit -> reparse -> re-emit is a byte fixed point; a
    # path claimed twice and every malformed row are CANNOT-EVALUATE; the legacy format is the named finding.
    rows = [inventory_row(home + "/payload/a.md", b"alpha\n"),
            inventory_row(archive_rel(rid, "legacy/OLD.md"), b"old\n")]
    data = emit_inventory(rid, rows)
    doc = tomllib.loads(data.decode("utf-8"))
    check("inventory-roundtrip-valid", validate_inventory(doc, rid).status == VALID)
    check("inventory-reemit-fixed-point", emit_inventory(rid, doc["file"]) == data)

    def variant(**changes):
        d = dict(format=store.EVIDENCE_INVENTORY_FORMAT, file=[dict(r) for r in doc["file"]])
        d.update(changes)
        return d

    dup = variant()
    dup["file"].append(dict(dup["file"][0]))
    check("inventory-duplicate-claim-cannot-eval", validate_inventory(dup, rid).status == CANNOT)
    legacy = validate_inventory(dict(format="opf.ingest.evidence-inventory/v1", file=[]), rid)
    check("inventory-legacy-format-named-finding",
          legacy.status == INVALID and any("legacy-ingest-inventory" in f for f in legacy.findings))
    check("inventory-unknown-key-cannot-eval", validate_inventory(variant(extra=1), rid).status == CANNOT)
    check("inventory-not-a-table-cannot-eval", validate_inventory([], rid).status == CANNOT)
    for label, key, value in (("negative-size", "size", -1), ("bool-size", "size", True),
                              ("prefixed-sha", "sha256", "sha256:" + ZERO), ("uppercase-sha", "sha256", "A" * 64)):
        bad = variant()
        bad["file"][0][key] = value
        check("inventory-{}-cannot-eval".format(label), validate_inventory(bad, rid).status == CANNOT)
    for label, path in (("bundle-root-inventory", home + "/inventory.toml"),
                        ("bundle-root-phase-inventory", home + "/inventory-a.toml"),
                        ("outside-families", ".working/somewhere/else.md"),
                        ("other-run-archive", archive_rel(other_run, "x.md")),
                        ("traversal", ".working/../escape")):
        bad = variant()
        bad["file"][0]["path"] = path
        check("inventory-{}-path-cannot-eval".format(label), validate_inventory(bad, rid).status == CANNOT)
    for label, path in (("nested-inventory-member", home + "/payload/inventory.toml"),
                        ("moved-destination", store.moved_dest("legacy/OLD.md")),
                        ("same-run-archive", archive_rel(rid, "OLD.md"))):
        ok = variant()
        ok["file"][0]["path"] = path
        check("inventory-{}-path-valid".format(label), validate_inventory(ok, rid).status == VALID)
    check("emit-malformed-refused", refusal(emit_inventory, rid, [dict(path="docs/x.md", size=1, sha256=ZERO)]))

    # 4: the op-list invariants, over hand-built lists (check_apply_ops is pure).
    src, body = ".working/TODO.md", b"todo\n"
    copy = archive_rel(rid, src)

    def c(path, payload):
        return dict(op="create", path=path,
                    poststate=dict(kind="file", mode=FILE_MODE, **{"content-sha256": _sha256(payload)}))

    def sealed(ops, staged, phase=None):
        inv = emit_inventory(rid, derive_rows(rid, ops, staged))
        staged = dict(staged)
        staged[inventory_rel(rid, phase)] = inv
        return ops + [c(inventory_rel(rid, phase), inv)], staged

    def findings_of(ops, staged, phase=None):
        return check_apply_ops(rid, phase, ops, staged)

    good = sealed([c(copy, body), _pinned_remove(src, body, FILE_MODE)], {copy: body})
    check("compose-preserve-first-admitted", findings_of(*good) == [])
    check("compose-remove-before-copy-refused", any("preserve-first" in f for f in findings_of(
        *sealed([_pinned_remove(src, body, FILE_MODE), c(copy, body)], {copy: body}))))
    check("compose-remove-without-copy-refused", any("preserve-first" in f for f in findings_of(
        *sealed([_pinned_remove(src, body, FILE_MODE)], {}))))
    check("compose-unpinned-remove-refused", any("source-poststate" in f for f in findings_of(
        *sealed([c(copy, body), dict(op="remove", path=src, poststate=dict(kind="absent"))], {copy: body}))))
    check("compose-pin-copy-mismatch-refused", any("differs" in f for f in findings_of(
        *sealed([c(copy, b"other\n"), _pinned_remove(src, body, FILE_MODE)], {copy: b"other\n"}))))
    elsewhere = (("write-archive", dict(op="write", path=copy,
                                        poststate=dict(kind="file", **{"content-sha256": _sha256(body)})), {}),
                 ("remove-archive", _pinned_remove(copy, body, FILE_MODE), {}),
                 ("rmdir-evidence", dict(op="rmdir", path=home, poststate=dict(kind="absent")), {}),
                 ("create-other-run-archive", c(archive_rel(other_run, src), body),
                  {archive_rel(other_run, src): body}),
                 ("create-other-run-bundle", c(evidence_home_rel(other_run) + "/x.md", body),
                  {evidence_home_rel(other_run) + "/x.md": body}),
                 ("create-import-bundle", c(store.evidence_run("import", "imp-20260917T120000Z-0123456789abcdef")
                                            + "/x.md", body),
                  {store.evidence_run("import", "imp-20260917T120000Z-0123456789abcdef") + "/x.md": body}))
    for label, op, staged in elsewhere:
        check("compose-immutable-{}-refused".format(label),
              any("immutable" in f for f in findings_of(*sealed([op], staged))))
    hand = emit_inventory(rid, [inventory_row(copy, body), inventory_row(home + "/never-written.md", b"x")])
    check("compose-hand-inventory-refused", any("derived" in f for f in findings_of(
        [c(copy, body), _pinned_remove(src, body, FILE_MODE), c(inventory_rel(rid), hand)],
        {copy: body, inventory_rel(rid): hand})))
    check("compose-inventory-not-final-refused", any("final op" in f for f in findings_of(
        [good[0][-1]] + good[0][:-1], good[1])))
    check("compose-no-inventory-refused", any("exactly its own inventory" in f for f in findings_of(
        good[0][:-1], good[1])))
    check("compose-base-inventory-in-phase-refused", any("exactly its own inventory" in f for f in findings_of(
        good[0], good[1], "completion")))
    check("compose-path-twice-refused", any("second time" in f for f in findings_of(*sealed(
        [c(copy, body), _pinned_remove(src, body, FILE_MODE),
         dict(op="write", path=src, poststate=dict(kind="file", **{"content-sha256": _sha256(body)}))],
        {copy: body, src: body}))))

    # 5: the journaled shell over throwaway fixtures. An occupied view destination and an occupying
    # machine-store file (a foreign manifest-shaped file, so no store resolves) are archived preserve-first
    # and removed; a non-occupying source takes only its preimage and stays frozen in place.
    def fixture(temp):
        root = Path(temp).resolve()
        files = {".working/TODO.md": b"hand-kept todo\n",
                 ".working/toml/manifest.toml": b"# a foreign manifest-shaped file\n",
                 "legacy/RULES.md": b"old rules\n"}
        for rel, payload in files.items():
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_bytes(payload)
        return root, files

    def snapshot(root):
        return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*"))
                if p.is_file() and not p.is_symlink() and not str(p.relative_to(root)).startswith(".aiqt/")}

    def plan_digest(payload):
        return "sha256:" + _sha256(payload)

    def compose_full(files, drift=False):
        def compose(ops):
            todo = files[".working/TODO.md"] + (b"drift" if drift else b"")
            ops.archive_occupying(".working/TODO.md", plan_digest(todo))
            ops.archive_occupying(".working/toml/manifest.toml", plan_digest(files[".working/toml/manifest.toml"]))
            ops.preserve("legacy/RULES.md", plan_digest(files["legacy/RULES.md"]))
        return compose

    def lock_free(root):
        return _journal.read_lock_owner(_journal_root(root)) is None

    def txn_state(root, txn):
        jr_fd = _journal.open_journal_root_from_path(root, JOURNAL_REL)
        try:
            return _journal.classify_state(jr_fd, _journal_root(root) / txn)
        finally:
            _journal._close_fd_quietly(jr_fd)

    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        root, files = fixture(temp)
        check("fixture-store-not-resolved", store.resolve_store(root).status != store.RESOLVED)
        txn, _why = attempt(run_adopt_transaction, root, rid, compose_full(files))
        after = snapshot(root)
        check("apply-txn-named-by-run", txn == rid and txn_state(root, rid) == "complete" and lock_free(root))
        check("preserve-first-archive-copies-byte-identical",
              all(after.get(archive_rel(rid, p)) == files[p] for p in files))
        check("preserve-first-occupying-sources-removed",
              ".working/TODO.md" not in after and ".working/toml/manifest.toml" not in after)
        check("non-occupying-source-frozen-in-place", after.get("legacy/RULES.md") == files["legacy/RULES.md"])
        inv = tomllib.loads(after.get(inventory_rel(rid), b"file = []").decode("utf-8"))
        check("inventory-derived-from-transaction",
              sorted(r["path"] for r in inv["file"]) == sorted(archive_rel(rid, p) for p in files))
        check("apply-bundle-verifies", verify_bundle(root, rid).status == VALID)
        # THE single-byte payload flip (size preserved): the re-read verification goes red, then the
        # missing and non-regular listed-entry findings.
        archived = root / archive_rel(rid, ".working/TODO.md")
        archived.parent.mkdir(parents=True, exist_ok=True)
        archived.write_bytes(b"hand-kept todO\n")
        flipped = verify_bundle(root, rid)
        check("verify-single-byte-flip-invalid",
              flipped.status == INVALID and any("payload drift" in f for f in flipped.findings))
        archived.unlink()
        check("verify-missing-listed-invalid", verify_bundle(root, rid).status == INVALID)
        archived.symlink_to("elsewhere")
        typed = verify_bundle(root, rid)
        check("verify-non-regular-invalid",
              typed.status == INVALID and any("not a regular file" in f for f in typed.findings))
        archived.unlink()
        archived.write_bytes(files[".working/TODO.md"])
        check("verify-restored-valid", verify_bundle(root, rid).status == VALID)
        # one transaction per run and phase: a second base transaction refuses before it opens.
        before = snapshot(root)
        late = refusal(run_adopt_transaction, root, rid,
                       lambda ops: ops.create(evidence_home_rel(rid) + "/late.md", b"late\n"))
        check("second-base-txn-refused", late is not None and "fresh plan" in late)
        check("second-base-txn-writes-nothing", snapshot(root) == before and lock_free(root))
        # a later phase publishes its own inventory beside the committed base inventory.
        ptxn, _why = attempt(run_adopt_transaction, root, rid, lambda ops: ops.create(
            evidence_home_rel(rid) + "/completion/result.toml", b"green = true\n"), phase="completion")
        check("phase-txn-published",
              ptxn == rid + ".completion" and (root / inventory_rel(rid, "completion")).is_file()
              and verify_bundle(root, rid).status == VALID)
        base = root / inventory_rel(rid)
        saved = base.read_bytes() if base.is_file() else None
        if saved is not None:
            base.unlink()
        check("verify-phase-without-base-cannot-eval",
              saved is not None and verify_bundle(root, rid).status == CANNOT)
        if saved is not None:
            base.write_bytes(saved)

    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        root, files = fixture(temp)
        before = snapshot(root)
        orphan = refusal(run_adopt_transaction, root, rid, lambda ops: ops.create(
            evidence_home_rel(rid) + "/completion/result.toml", b"x\n"), phase="completion")
        check("phase-without-base-refused", orphan is not None and "never stands in" in orphan)
        drifted = refusal(run_adopt_transaction, root, rid, compose_full(files, drift=True))
        check("drifted-source-refused", drifted is not None and "drifted" in drifted)
        check("drifted-source-writes-nothing", snapshot(root) == before and lock_free(root)
              and not (root / JOURNAL_REL / rid).exists())

    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        import _opf_init
        root = Path(temp).resolve()
        (root / ".working/toml").mkdir(parents=True)
        (root / ".working/toml/manifest.toml").write_text(_opf_init.build_manifest(), encoding="utf-8")
        before = snapshot(root)
        leased = refusal(run_adopt_transaction, root, rid, lambda ops: None)
        check("resolved-store-refused-without-lease",
              store.resolve_store(root).status == store.RESOLVED and leased is not None and "lease" in leased)
        check("resolved-store-writes-nothing", snapshot(root) == before and not (root / ".aiqt").exists())

    # 6: the pre-commit abort. A failure at the final op (after both removals) rolls the one transaction
    # back in reverse order: each source is restored byte-identical before its archive copy is discarded.
    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        root, files = fixture(temp)
        before = snapshot(root)
        real_verify = _journal._verify_staged_digest

        def failing_inventory(op, payload):
            if op["path"] == inventory_rel(rid):
                raise _journal.JournalError("injected failure at the inventory publication")
            return real_verify(op, payload)

        with mock.patch.object(_journal, "_verify_staged_digest", failing_inventory):
            aborted = refusal(run_adopt_transaction, root, rid, compose_full(files))
        check("abort-rolls-back", aborted is not None and "rolled back" in aborted
              and txn_state(root, rid) == "rolled-back" and lock_free(root))
        check("abort-restores-prestate", snapshot(root) == before)

    # 7: an interrupted apply (INTENT without a terminal frame, through the journal's kill-point seam)
    # refuses every later run until the EXPLICIT reconcile, which works from the journal alone and never
    # resolves the store; it reverses fully mid-archive, and completes forward once every op landed.
    class _Interrupt(Exception):
        pass

    def interrupt_when(pred):
        def fake(name):
            if name.startswith("after-apply-") and pred():
                raise _Interrupt(name)
        return fake

    def reconcile_without_store(root):
        with mock.patch.object(store, "resolve_store", side_effect=AssertionError("reconcile resolved")):
            try:
                return reconcile(root)
            except (AssertionError, AdoptApplyError):
                return []

    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        root, files = fixture(temp)
        before = snapshot(root)
        manifest = ".working/toml/manifest.toml"
        mid = interrupt_when(lambda: (root / archive_rel(rid, manifest)).exists() and (root / manifest).exists())
        with mock.patch.object(_journal, "_kill_point", mid):
            try:
                run_adopt_transaction(root, rid, compose_full(files))
                interrupted = False
            except (_Interrupt, AdoptApplyError) as exc:
                interrupted = isinstance(exc, _Interrupt)
        check("interrupt-leaves-open-transaction", interrupted and txn_state(root, rid) == "open")
        partial = snapshot(root)
        blocked = refusal(run_adopt_transaction, root, other_run,
                          lambda ops: ops.create(evidence_home_rel(other_run) + "/x.md", b"x\n"))
        check("interrupted-journal-refuses-next-run", blocked is not None and "reconcile" in blocked)
        check("interrupted-journal-refusal-writes-nothing", snapshot(root) == partial)
        check("reconcile-back-from-journal-alone", (rid, "rolled-back") in reconcile_without_store(root))
        check("reconcile-back-restores-prestate", snapshot(root) == before and lock_free(root))
        check("reconciled-journal-admits-next-run",
              attempt(run_adopt_transaction, root, other_run, compose_full(files))[0] == other_run)

    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        root, files = fixture(temp)
        landed = interrupt_when(lambda: (root / inventory_rel(rid)).exists())
        with mock.patch.object(_journal, "_kill_point", landed):
            try:
                run_adopt_transaction(root, rid, compose_full(files))
            except (_Interrupt, AdoptApplyError):
                pass
        check("reconcile-forward-from-journal-alone", (rid, "rolled-forward") in reconcile_without_store(root))
        check("reconcile-forward-verified-poststate",
              txn_state(root, rid) == "complete" and verify_bundle(root, rid).status == VALID
              and not (root / ".working/TODO.md").exists())

    # 8: a held journal lock refuses a transaction and a reconcile (never seized).
    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        root, files = fixture(temp)
        journal_root = _journal_root(root)
        root_fd = store._open_dir_nofollow(root)
        try:
            _journal.ensure_journal_dirs(root_fd, JOURNAL_REL)
        finally:
            os.close(root_fd)
        _journal.acquire_lock(journal_root, "opf-adopt-selftest-peer")
        check("held-lock-refuses-transaction", refusal(run_adopt_transaction, root, rid, compose_full(files)))
        check("held-lock-refuses-reconcile", refusal(reconcile, root))
        _journal.release_lock(journal_root)

    # 9: live re-observation over a throwaway fixture.
    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        root = Path(temp).resolve()
        (root / "legacy.md").write_bytes(b"legacy\n")
        (root / "link.md").symlink_to("legacy.md")
        root_fd = store._open_dir_nofollow(root)
        try:
            check("observe-absent", observe_live(root_fd, "missing.md") == dict(kind="absent"))
            seen = observe_live(root_fd, "legacy.md")
            check("observe-file-digest", seen.get("size") == 7 and seen.get("sha256") == _sha256(b"legacy\n"))
            check("observe-symlink-refused", refusal(observe_live, root_fd, "link.md"))
            check("observe-traversal-refused", refusal(observe_live, root_fd, "../escape"))
        finally:
            os.close(root_fd)

    # 10: apply takes only a plan/v2 (spec 14.1): the shipped v1 schema is never apply input.
    v1 = apply_plan(schema.canonical_plan())
    check("apply-v1-plan-refused", v1.status == CANNOT and any("never apply input" in f for f in v1.findings))
    check("apply-v2-marked-plan-refused", apply_plan(dict(format=PLAN_V2_FORMAT)).status == CANNOT)
    check("apply-non-table-refused", apply_plan([]).status == CANNOT)

    if failures:
        print("OPF-ADOPT-APPLY SELF-TEST: FAIL ({} of {} checks failed)".format(len(failures), checked[0]))
        for f in failures:
            print("  FAILED: {}".format(f))
        return 1
    print("OPF-ADOPT-APPLY SELF-TEST: PASS ({} apply-shell checks; zero executable ops)".format(checked[0]))
    return 0


def main():
    args = sys.argv[1:]
    if "--self-test" in args or "--selftest" in args:
        return self_test()
    print("usage: _opf_adopt_apply.py --self-test (a library module; the adoption verb is a later "
          "slice)", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
