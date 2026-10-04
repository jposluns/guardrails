#!/usr/bin/env python3
"""OPF adoption apply engine, slices 1-2: the apply SHELL and its three file ops (OPF-SPEC 1.3.0).

Slice 1 (the clean-start adoption track's first apply unit) supplies the engine SKELETON, shaped by spec
1.3.0 sections 4.2, 5.7 and 14: run identity; the evidence-bundle, archive and Move homes, all
derived from the `_opf_store` homes constructors (never re-spelled here); the `opf.evidence.inventory/v1`
inventory DERIVED from the run's own transaction op list and published in that SAME transaction as the
retained bytes it lists (spec 4.2), a base `inventory.toml` then one `inventory-<phase>.toml` per later
phase, never rewritten; the homes-1 bundle verification the completion checks carry themselves while
C-EVIDENCE-ENUM is inactive (spec 14.1), which RE-READS the inventories and payload digests from disk;
the preserve-first composition of spec 14.2; live re-observation of every operand; one journaled
transaction per (run, phase), reconcile-first; and a dispatch table keyed by the closed eleven-op
ADOPT_OPS vocabulary. Slice 2 makes the three pure file ops executable (EXECUTABLE_OPS: create-file,
move-file, retire-file), staged as spec 14.1 and 14.2 order them: the run's base transaction is the APPLY
stage, which creates a planned file, takes a non-occupying retire source's retirement preimage while the
source stays frozen in place, and archives an occupying retire or move source preserve-first; the run's
RETIREMENT_PHASE transaction, which the stage driver runs only after the green completion check, is the
RETIREMENT stage, which removes a frozen retire source once the retirement preimage the committed base
published re-verifies, relocates a frozen non-occupying move source (live bytes re-verified against the
plan digest; the base publishes nothing for it), and relocates an occupying move source from the archive
copy the committed base published, re-verified. Every preserved copy and relocated file keeps its
source's mode bits exactly. Every other op still returns a refusing not-yet-executable
verdict: init-store composition, trust verification, approval capture, hook activation, rendering,
receipt writing, the completion checks, the stage driver itself, and the MUTATING CLI subcommands
(approve, apply, complete, reconcile) remain later slices; the read-only `opf adopt` subcommands plan and
status shipped with K9a. Live outside the self-test fixtures today: `opf adopt status` opens and lists the
evidence home in opf.py through the _journal containment primitives, then grades each listed bundle
through this module's _verify_bundle_at (beneath the HELD home descriptor it is passed) and the journal
through journal_state, with _open_product_root anchoring both reads to one product-root descriptor;
every mutating entry -- the transaction shell, reconcile(), the dispatch table and compose_rows -- stays
reachable only from the self-test until those slices land.

Preserve-first (spec 14.2), enforced over the composed op list BEFORE any transaction opens: a live file
is removed, OR OVERWRITTEN BY A `write` (which destroys the live bytes exactly as a removal does), ONLY
as the second half of a pair whose first half, earlier in the SAME transaction, creates its byte-identical
archive copy at `.working/archive/adoption/<run-id>/<source-path>`. The copy's create op carries the plan
digest as its content digest, which the journal verifies against the staged bytes, RE-READS from the
written destination and digest-verifies again (a spec 14.2 verification checkpoint), and fsyncs (file and
parent) before the next op runs, and the removal or write carries the same digest and mode as a
`source-poststate` pin, which the journal verifies at preimage capture under its lock. So the copy is
verified on disk and durably committed before the source is destroyed, never reversed and never split
across transactions, and a pre-commit abort (the journal's reverse-order rollback) restores the source
from its digest-checked preimage, RE-READS and digest-verifies the restored live bytes, and only then
discards the copy or reports a prestate (the rollback-side checkpoint). A source whose live bytes no
longer match its plan digest is drifted and is never archived or removed. A non-occupying source takes
only its retirement preimage at apply and stays frozen in place; its removal waits for the green
completion check and runs in the RETIREMENT_PHASE transaction, where a removal pairs instead with the
archive copy the run's COMMITTED base transaction published (its INTENT digest, re-read live before
anything is composed) or, for a move, with the create of its destination earlier in that same
transaction. An `rmdir` may target ONLY a directory this same transaction created; a
pre-existing live directory is never removed by this shell. No protected destination
(`_opf_adopt.protected_destination`, the ONE predicate the planner shares, its names compared
case-insensitively: a path that is not normalized and contained; a `.git` or `.aiqt` component at any
depth, the adoption journal's own tree included; the product-root pointers; the store control area; the
store tree's `.gitignore`) is an apply operand of ANY kind, save the mkdirs leading to this run's own
homes. The adoption archive and every evidence bundle are immutable: an op may only create beneath this
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
product root, and EVERY other store posture that cannot be evaluated (a malformed or unreadable pointer,
multiple machine stores, an undiscoverable root) refuses too (spec 14.2: an unreadable declaration or
detected input fails closed). Only the two first-adoption states adoption exists for are admitted:
NOT-ADOPTED, and a present `.working/` at the DEFAULT location carrying no valid manifest, re-proved by a
fresh discovery. A first adoption has no store and so no lease home; the pre-store single-writer control is
the coupled-init substrate's operation lock, which joins with the init-store slice. Disclosed residuals of
this slice, none of them a relaxation: bundle MEMBERSHIP (an off-inventory file inside a bundle) is not
reconciled here, only listed payloads; containment registration of the archive, Move and evidence homes on
homes 1 is part of the 1.3.0 activation, not this slice; investigation does not read this journal (the
planner's ancestry disclosure lists durable OPF history outside .working, and `.aiqt/adopt/journal` is one
more such home), so the stage driver's plan stage must refuse over a non-clean adoption journal through
journal_clean_or_refuse; the shipped `retire-file` vocabulary row declares a single `remove`, while spec
1.3.0 preserves the retirement preimage at apply and removes only after the green check, so the file ops
split each retire and move row across the two stages, and the base transaction's create of the archive
copy is the declared row's unlisted first half; the RETIREMENT_PHASE transaction refuses on a resolved
store exactly as every transaction here does (no lease join yet), so once init-store executes, a real
adoption's retirement stage refuses until the lease join lands; the retirement stage is bound to the plan
rows it is handed, not to the rows the base ran (the base INTENT records ops, not plan rows), so binding
both stages to the one approved plan is the stage driver's, through the approval it captures; a
retirement transaction that ROLLS BACK (a fault before its commit) still uses the run's one
RETIREMENT_PHASE transaction, so a retry of that stage refuses and the committed base's frozen sources stay
frozen in place until a fresh plan with its own run id (a retry rule for a rolled-back phase is the stage
driver's to settle); and occupancy is the plan row's fact, never re-derived from the tree, so a planner
misclassification of a non-occupying source as occupying archives and removes it at apply (its bytes
preserved byte-exact) rather than leaving it frozen until the green check. Interruption is exercised in
process through the journal's kill-point seam AND by real process death: the self-test's kill-injection
matrix (the migrate.py crash-harness model) runs each stage's transaction in a child interpreter that
os._exit()s at every kill point the transaction reaches, then reconciles in a fresh process, breaking the
dead owner's stale lock, and lands the tree on exactly its prestate or its verified poststate; the
rollback leg kills the recovery itself at every restore point.

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
import json
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
import _optlevel             # noqa: E402
from _opf_emit import EmitError, emit_checked  # noqa: E402

KIND = "adoption"
SESSION_ID = "opf-adopt"
OPERATION = "adopt-apply"
# The adoption journal root, at the PRODUCT root and outside `.working/` (see the module docstring).
JOURNAL_REL = ".aiqt/adopt/journal"
# The only plan format apply may take (spec 14.1); its schema lands in a later slice.
PLAN_V2_FORMAT = "opf.adoption.plan/v2"
# The phase of the run's retirement-stage transaction, which the stage driver runs only after the green
# completion check (spec 14.1); the base transaction is the apply stage.
RETIREMENT_PHASE = "retirement"
DIR_MODE = 0o755
FILE_MODE = 0o644
_NONCE_RE = re.compile(r"^[0-9a-f]{16}\Z")
# The store-tree control homes this engine may create beneath and never rewrite (spec 4.2, 14.2); which of
# their paths take a create is the shared protected-destination predicate's.
_CONTROL_HOMES = (store.ARCHIVE_REL, store.IMPORTED_REL)
# The Move root, derived from the public Move-destination constructor (spec 14.2), never re-spelled.
_MOVED_ROOT = store.moved_dest("x").rsplit("/", 1)[0]


class AdoptApplyError(Exception):
    """A fail-closed refusal or cannot-evaluate carrying the operator-facing reason (mapped to exit 2)."""


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def _within(path, home):
    return path == home or path.startswith(home + "/")


def _is_mode(value):
    return type(value) is int and 0 <= value <= 0o7777


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
    CANNOT-EVALUATE. Bundle membership (an unlisted file) is not reconciled here. CAPACITY LIMIT
    (disclosed): every inventory and payload read is bounded by the journal's contained-read cap
    (_journal._MAX_PRODUCT_READ_BYTES, 16 MiB), so a listed file over the cap is CANNOT-EVALUATE naming
    the cap, never truncated or slurped unbounded; the same ceiling bounds compose (_read_live), preimage
    capture and poststate verification, so no bundle this shell writes can carry an over-cap payload.
    Directory identities are RETAINED from the bundle listing through every inventory and payload read
    (_verify_bundle_at), so a directory concurrently swapped onto a listed pathname is never re-resolved
    mid-verification (round 3)."""
    if not is_run_id(run_id):
        return schema._cannot("bundle run id {!r} does not match the adoption grammar".format(run_id))
    root_fd = _open_product_root(product_root)
    try:
        return _verify_bundle_at(root_fd, run_id, evidence_home_rel(run_id))
    finally:
        store._close_fd_exc_safe(root_fd)


def _verify_bundle_at(root_fd, run_id, bundle, home_fd=None):
    # Round-3 identity retention (the read_lock_owner_at posture applied to the bundle): ONE descriptor
    # per directory, opened contained/no-follow beneath its retained parent and HELD from the bundle
    # listing through every inventory and payload read, so no read re-resolves a pathname from root_fd
    # after the listing. The bundle descriptor the listing used is the SAME descriptor every
    # bundle-relative read goes through, and a listed payload outside the bundle walks its own chain the
    # same way, sharing every already-opened ancestor, so all reads of one verification bind to one
    # directory identity per path: a directory swapped onto a pathname between the listing and a read is
    # never followed, and two directories neither of which verifies alone can never combine into one
    # false success. A component that cannot be opened contained (symlinked, wrong-type, unreadable) is
    # CANNOT-EVALUATE, never approximated. home_fd (round 4): the evidence-home descriptor a caller that
    # LISTED the home still holds; when given, it SEEDS the retained chain (a dup, so the caller's
    # descriptor stays open and the cleanup below owns only the dup) and the bundle is stat'ed and opened
    # beneath THAT held identity, never re-walked from root_fd, so an evidence home swapped onto its
    # pathname between the caller's listing and this verification can never contribute a bundle.
    dir_fds = dict()
    if home_fd is not None:
        # Round 7 MINOR: the subscript KEY is computed BEFORE the dup (Python evaluates an
        # assignment right-hand side first), so a _check_rel raise on a malformed bundle path can
        # never strand the just-duplicated home descriptor outside dir_fds, where the cleanup
        # below cannot close it.
        home_key = tuple(_journal._check_rel(bundle))[:-1]
        dir_fds[home_key] = os.dup(home_fd)

    def dir_at(parts):
        """The RETAINED dir fd for the relative directory `parts` (a tuple of components; () is the
        product root itself): each component is opened O_DIRECTORY|O_NOFOLLOW beneath its retained
        parent exactly once and reused for every later read of this verification."""
        if not parts:
            return root_fd
        fd = dir_fds.get(parts)
        if fd is None:
            pfd = dir_at(parts[:-1])
            try:
                fd = os.open(parts[-1], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=pfd)
            except FileNotFoundError:
                raise
            except OSError as exc:
                raise _journal.JournalError("cannot open contained directory component {!r} of "
                                            "{!r} ({})".format(parts[-1], "/".join(parts), exc))
            dir_fds[parts] = fd
        return fd

    def read_retained(relpath):
        """_read_contained's sibling over the RETAINED parent chain (contained, no-follow, single-link,
        capped at _journal._MAX_PRODUCT_READ_BYTES), never a fresh path walk from root_fd."""
        parts = _journal._check_rel(relpath)
        # O_NONBLOCK so a non-regular final component (e.g. a FIFO swapped in for the regular file)
        # returns at once instead of blocking forever; the fstat below then refuses it.
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                     dir_fd=dir_at(tuple(parts[:-1])))
        try:
            fst = os.fstat(fd)
            if not stat.S_ISREG(fst.st_mode):
                raise _journal.JournalError("contained path {!r} is not a regular file".format(relpath))
            if fst.st_nlink != 1:
                raise _journal.JournalError("contained control file {!r} has {} hard links; "
                                            "refusing to read a multiply-linked control file (a hardlink "
                                            "to an out-of-tree victim, never our singly-linked control "
                                            "file)".format(relpath, fst.st_nlink))
            return _journal._read_fd(fd, cap=_journal._MAX_PRODUCT_READ_BYTES), fst
        finally:
            store._close_fd_exc_safe(fd)

    try:
        try:
            if home_fd is not None:
                # presence and type read through the HELD home descriptor the caller's listing used,
                # never by re-walking `bundle` from root_fd (round 4).
                st = _journal._lstat_at(home_fd, _journal._check_rel(bundle)[-1])
            else:
                st = _journal._lstat_contained(root_fd, bundle)
            if st is None:
                return schema._invalid(["evidence bundle {!r} is missing".format(bundle)])
            if not stat.S_ISDIR(st.st_mode):
                return schema._cannot("evidence bundle {!r} is not a directory".format(bundle))
            dfd = dir_at(tuple(_journal._check_rel(bundle)))
            names = sorted(os.listdir(dfd))
        except (_journal.JournalError, OSError) as exc:
            return schema._cannot("cannot list evidence bundle {!r} ({})".format(bundle, exc))
        inventories = [name for name in names if store.is_evidence_inventory_name(name)]
        if not inventories:
            return schema._invalid(["evidence bundle {!r} has no inventory".format(bundle)])
        if "inventory.toml" not in inventories:
            return schema._cannot("evidence bundle {!r} has a phase inventory but no inventory.toml; a "
                                  "phase inventory never stands in for it (spec 4.2)".format(bundle))
        expected = dict()
        for name in inventories:
            rel = bundle + "/" + name
            try:
                raw, _fst = read_retained(rel)
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
                parts = _journal._check_rel(path)
                try:
                    pfd = dir_at(tuple(parts[:-1]))
                except FileNotFoundError:
                    findings.append("listed file {!r} is missing".format(path))
                    continue
                pst = _journal._lstat_at(pfd, parts[-1])
                if pst is None:
                    findings.append("listed file {!r} is missing".format(path))
                    continue
                if not stat.S_ISREG(pst.st_mode):
                    findings.append("listed entry {!r} is not a regular file".format(path))
                    continue
                # bounded by _MAX_PRODUCT_READ_BYTES (16 MiB): an over-cap listed payload cannot be hashed
                # here and is CANNOT-EVALUATE below, naming the cap (a disclosed capacity limit; the same
                # ceiling bounds compose/capture/poststate, so the shell never writes such a bundle).
                data, _fst = read_retained(path)
            except (_journal.JournalError, OSError) as exc:
                return schema._cannot("cannot read listed file {!r} ({})".format(path, exc))
            if len(data) != row["size"] or _sha256(data) != row["sha256"]:
                findings.append("listed file {!r} does not match its recorded size and sha256 (payload "
                                "drift)".format(path))
        return schema._ok() if not findings else schema._invalid(findings)
    finally:
        for fd in dir_fds.values():
            _journal._close_fd_quietly(fd)


# --- composition: preserve-first, derived inventories, immutable homes (spec 4.2, 14.2) ---------------

def _archive_root(run_id):
    """This run's whole adoption-archive home, derived from the public retire-preimage constructor
    (spec 4.2), never composed from private helpers."""
    return archive_rel(run_id, "x").rsplit("/", 1)[0]


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
    at capture, under its lock, before it takes the preimage (the pinned-remove idiom of the retired
    ingest execution coordinator)."""
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
        # A phase transaction's view of the run's COMMITTED base: the archive copies its INTENT published
        # ({path: content sha256}) and the mode each was created at ({path: mode}, the source's own mode),
        # set by run_adopt_transaction. And the source -> destination pairs this transaction relocates, each
        # removal paired with its destination's create (check_apply_ops).
        self.committed = {}
        self.committed_modes = {}
        self.moves = {}

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

    def frozen(self, source_path, plan_digest):
        """Re-observe one plan-enumerated source and require its live bytes to equal the plan digest (a
        drifted source MUST NOT be archived, retired, moved or removed, spec 14.2). Composes nothing.
        Returns (bytes, mode)."""
        expected = _plan_hex(plan_digest)
        fst, data = _read_live(self.root_fd, source_path)
        if fst is None:
            raise AdoptApplyError("plan-enumerated source {!r} is absent from the live tree; a fresh plan "
                                  "with its own approval is the remedy (spec 14.1)".format(source_path))
        if _sha256(data) != expected:
            raise AdoptApplyError("source {!r} is drifted: its live bytes no longer match its plan digest, "
                                  "so it MUST NOT be archived, retired, moved or removed; a fresh plan "
                                  "with its own approval is the remedy (spec 14.2)".format(source_path))
        return data, stat.S_IMODE(fst.st_mode)

    def require_absent(self, rel):
        """Re-observe one destination and refuse it occupied: a collision routes to a disposition, never
        an overwrite (spec 14.2). Composes nothing."""
        fst, _data = _read_live(self.root_fd, rel)
        if fst is not None:
            raise AdoptApplyError("{!r} is occupied; a collision routes to a disposition, never an "
                                  "overwrite (spec 14.2)".format(rel))

    def preserve(self, source_path, plan_digest):
        """Preserve one plan-enumerated source at apply: re-observe its live bytes, require them to equal
        the plan digest (frozen), and create the byte-identical copy at
        `.working/archive/adoption/<run-id>/<source-path>` with the source's own mode bits, so a private
        source is never widened and an executable one keeps its bits. The source itself stays live and
        frozen: a non-occupying source's retirement preimage. Returns (bytes, mode)."""
        data, mode = self.frozen(source_path, plan_digest)
        self.create(archive_rel(self.run_id, source_path), data, mode)
        return data, mode

    def archive_occupying(self, source_path, plan_digest):
        """Resolve one occupied destination preserve-first (spec 14.2): the verified archive copy's create
        op, then the pinned removal of the live source, in THIS transaction and in that order."""
        data, mode = self.preserve(source_path, plan_digest)
        self.ops.append(_pinned_remove(source_path, data, mode))

    def committed_copy(self, source_path, plan_digest):
        """The retirement stage's preservation check (spec 14.1 check 3, 14.2: digest verification precedes
        removal): the archive copy of one source must be one the run's COMMITTED base transaction published
        with the plan digest, and its live bytes must still equal it. Composes nothing; returns the
        archived bytes and the mode that INTENT created the copy at (the source's own mode)."""
        rel = archive_rel(self.run_id, source_path)
        expected = _plan_hex(plan_digest)
        mode = self.committed_modes.get(rel)
        if self.committed.get(rel) != expected or not _is_mode(mode):
            raise AdoptApplyError("{!r} has no archive copy the run's committed base transaction published "
                                  "with its plan digest; preservation precedes removal (spec 14.2), so "
                                  "nothing is retired or moved (fail-closed)".format(source_path))
        fst, data = _read_live(self.root_fd, rel)
        if fst is None or _sha256(data) != expected:
            raise AdoptApplyError("the archive copy {!r} is missing or no longer matches its plan digest; "
                                  "nothing is retired or moved (spec 14.2, fail-closed)".format(rel))
        return data, mode

    def retire(self, source_path, plan_digest):
        """Retirement stage (spec 14.1): remove one frozen non-occupying source, only after its committed
        retirement preimage re-verifies, through a removal pinned to the plan digest and the live mode."""
        if self.sealed:
            raise AdoptApplyError("the transaction is sealed by its inventory; nothing may follow it")
        self.committed_copy(source_path, plan_digest)
        data, mode = self.frozen(source_path, plan_digest)
        self.ops.append(_pinned_remove(source_path, data, mode))

    def relocate(self, source_path, destination, plan_digest, occupying):
        """Retirement stage (spec 14.2 Move): an occupying source, archived and removed at apply, has its
        committed archive copy re-verified and copied to the destination, the copy staying retained; a
        frozen non-occupying source has its verified live bytes created at the destination, then is
        removed through a pinned removal paired with that create. The destination takes the source's own
        mode bits in both branches (the live mode, or the mode the base archived the source at)."""
        if occupying:
            self.create(destination, *self.committed_copy(source_path, plan_digest))
            return
        data, mode = self.frozen(source_path, plan_digest)
        self.create(destination, data, mode)
        self.ops.append(_pinned_remove(source_path, data, mode))
        self.moves[source_path] = destination

    def seal(self):
        """Create this transaction's inventory, derived from its own op list, as its final op."""
        data = emit_inventory(self.run_id, derive_rows(self.run_id, self.ops, self.staged))
        self.create(self.inventory, data)
        self.sealed = True


def check_apply_ops(run_id, phase, ops, staged, committed=None, moves=None, committed_modes=None):
    """Re-prove the shell's invariants over ONE finished op list, pure and before any transaction
    opens; returns the findings (empty means admissible). Preserve-first: every removal AND every write
    (which destroys the live bytes exactly as a removal does) carries a pinned digest and mode and
    follows, in this same list, the create of its archive copy with that same digest and that same mode
    (spec 14.2; the copy keeps the source's mode bits exactly). In the
    RETIREMENT_PHASE transaction alone, a removal (never a write) may instead pair with the create of its
    move destination earlier in this list (`moves`, source -> destination) or, failing that, with the
    archive copy the run's COMMITTED base transaction published (`committed`, path -> content sha256 from
    that transaction's INTENT, and `committed_modes`, path -> the mode that INTENT created it at, compared
    when given), at the same digest (spec 14.1: retirement waits for the green check); this check is pure,
    so the live bytes of that committed copy are re-read by run_adopt_transaction (_committed_pairs_live); a write
    additionally needs staged bytes matching its own content digest, like a create. An rmdir may target
    only a directory an earlier mkdir in this same list creates (so, under one-op-per-path, no live
    directory is ever removed by this shell). A protected destination (_opf_adopt.protected_destination,
    the predicate the planner applies to every move destination and archive copy, its names compared
    case-insensitively: `.git` and `.aiqt` at any depth, the adoption journal's own tree included, the
    product-root pointers, the store control area and the store tree's `.gitignore`) is never an operand of
    any kind, save a mkdir of this run's own homes or
    their ancestors. Immutable homes: under `.working/archive/` and `.working/imported/` only a create
    beneath this run's own archive, own bundle, or the Move root (and the mkdirs leading there) is allowed,
    never a write, remove, or rmdir, and never another run's home (spec 14.2, 4.2). One op per path. The
    final op, and the only bundle-root inventory, is this transaction's own inventory, byte-equal to the inventory
    derived from the list's retained bytes (spec 4.2). Spec 4.2 MAY lets a phase inventory publish in the
    same transaction as the base to claim a promotion receipt without a digest cycle; this shell has no
    receipt to claim, so it deliberately takes the STRICTER exactly-one-inventory rule, and the receipt
    slice may relax it with its own vectors."""
    target = inventory_rel(run_id, phase)
    roots = (evidence_home_rel(run_id), _archive_root(run_id), _MOVED_ROOT)
    committed = committed if isinstance(committed, dict) else {}
    committed_modes = committed_modes if isinstance(committed_modes, dict) else {}
    moves = moves if isinstance(moves, dict) else {}
    if not isinstance(ops, list) or not ops or not isinstance(staged, dict):
        return ["the transaction carries no op list"]
    findings = []
    seen = set()
    created = {}
    made_dirs = set()
    for i, op in enumerate(ops):
        where = "op[{}]".format(i)
        if (not isinstance(op, dict) or op.get("op") not in _journal.OP_KINDS
                or not schema._is_contained_filepath(op.get("path"))):
            findings.append("{} is not a contained journal op".format(where))
            continue
        kind, path = op["op"], op["path"]
        protected = schema.protected_destination(path, run_id)
        if protected is not None and not (kind == "mkdir" and any(_within(r, path) for r in roots)):
            findings.append("{} would {} {!r}, a protected destination: {} (fail-closed)".format(
                where, kind, path, protected))
            continue
        if path in seen:
            findings.append("{} touches {!r} a second time (one op per path)".format(where, path))
        seen.add(path)
        if kind not in ("create", "mkdir") and any(_within(path, home) for home in _CONTROL_HOMES):
            findings.append("{} would {} {!r}: the adoption archive and evidence bundles are immutable "
                            "(spec 14.2, 4.2); only a create beneath this run's own homes is "
                            "allowed".format(where, kind, path))
        if kind == "create":
            digest = (op.get("poststate") or {}).get("content-sha256")
            data = staged.get(path)
            if not isinstance(data, bytes) or _sha256(data) != digest:
                findings.append("{} create {!r} has no staged bytes matching its content digest".format(
                    where, path))
            created[path] = (digest, (op.get("poststate") or {}).get("mode"))
        elif kind == "mkdir":
            made_dirs.add(path)
        elif kind == "rmdir":
            if path not in made_dirs:
                findings.append("{} rmdirs {!r}, which this transaction did not create; a live directory "
                                "is never removed by the apply shell (fail-closed)".format(where, path))
        elif kind in ("remove", "write"):
            verb = "removes" if kind == "remove" else "overwrites"
            if kind == "write":
                digest = (op.get("poststate") or {}).get("content-sha256")
                data = staged.get(path)
                if not isinstance(data, bytes) or _sha256(data) != digest:
                    findings.append("{} write {!r} has no staged bytes matching its content digest".format(
                        where, path))
            pin = op.get("source-poststate")
            if not (isinstance(pin, dict) and pin.get("kind") == "file" and isinstance(pin.get("sha256"), str)
                    and isinstance(pin.get("mode"), int)):
                findings.append("{} {} {!r} without a pinned file digest and mode "
                                "(source-poststate)".format(where, verb, path))
            else:
                rel = archive_rel(run_id, path)
                paired = created.get(rel)
                if paired is None and kind == "remove" and phase == RETIREMENT_PHASE:
                    paired = (created.get(moves[path]) if path in moves
                              else (committed[rel], committed_modes.get(rel, pin["mode"])) if rel in committed
                              else None)
                if paired is None:
                    findings.append("{} {} {!r} before, or without, its archive copy earlier in this "
                                    "transaction: preserve-first, verify then destroy, never reversed or "
                                    "split (spec 14.2)".format(where, verb, path))
                elif paired[0] != pin["sha256"]:
                    findings.append("{} {} {!r} whose pinned digest differs from its archive copy's "
                                    "content digest".format(where, verb, path))
                elif paired[1] != pin["mode"]:
                    findings.append("{} {} {!r} whose pinned mode {!r} differs from the mode {!r} its "
                                    "preserved copy is created at: a preservation keeps the source's mode "
                                    "bits exactly (fail-closed)".format(
                                        where, verb, path, pin["mode"], paired[1]))
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


def _committed_pairs_live(root_fd, run_id, phase, ops, committed, moves):
    """The live half of the committed pairing check_apply_ops proves pure (spec 14.1 check 3, 14.2: digest
    verification precedes removal): RE-READ, contained and single-linked, every committed archive copy a
    RETIREMENT_PHASE removal in this finished list pairs with (one neither created earlier in the list nor
    relocated to a move destination), and refuse a copy that is missing or no longer holds its INTENT
    digest. A hand-built list that appends a pinned removal without ApplyOps.retire is held to this rule
    too. Returns the findings; a copy that cannot be read refuses (AdoptApplyError)."""
    if phase != RETIREMENT_PHASE:
        return []
    findings = []
    created = set()
    for op in ops:
        if op["op"] == "create":
            created.add(op["path"])
        elif op["op"] == "remove" and op["path"] not in moves:
            rel = archive_rel(run_id, op["path"])
            if rel in created:
                continue
            fst, data = _read_live(root_fd, rel)
            if fst is None or _sha256(data) != committed.get(rel):
                findings.append("the committed archive copy {!r} a removal of {!r} pairs with is missing or "
                                "no longer matches its INTENT digest; nothing is retired (spec 14.2, "
                                "fail-closed)".format(rel, op["path"]))
    return findings


# --- the journaled transaction shell (reconcile-first; `.aiqt/adopt/journal`) --------------------------

def _journal_root(product_root):
    return Path(product_root) / JOURNAL_REL


def journal_state(root_fd, journal_root):
    """The adoption journal's state READ-ONLY, through the engine's own classification: (owner, opened),
    the recorded lock owner (None when no lock is held) and the sorted names of the transactions
    `_journal.classify_state` reads as open (INTENT without a terminal frame). A nothing-opened,
    complete, or rolled-back transaction is clean. An absent journal root is (None, []); a symlinked,
    dangling, non-directory, unreadable, or corrupt journal, or a symlinked or wrong-type entry in it (any
    entry but a transaction directory or a regular, singly-linked `lock` / `lock.break`), raises
    AdoptApplyError (fail-closed, never followed, skipped or read as absent). The lock and every entry are
    read through the ONE contained journal-root descriptor, never by re-resolving `journal_root` (which
    only names the returned entries), and each transaction is classified through its own directory
    descriptor HELD from the enumeration itself (round 4), so a transaction directory swapped onto its
    name after the enumeration can never read as clean. Nothing is written here."""
    try:
        st = _journal._lstat_contained(root_fd, JOURNAL_REL)
    except (_journal.JournalError, OSError) as exc:
        raise AdoptApplyError("cannot inspect the adoption journal {} ({}); "
                              "fail-closed".format(JOURNAL_REL, exc))
    if st is None:
        return None, []
    if not stat.S_ISDIR(st.st_mode):
        raise AdoptApplyError("the adoption journal {} is not a directory; fail-closed".format(JOURNAL_REL))
    try:
        jr_fd = _journal.open_journal_root_fd(root_fd, JOURNAL_REL)
    except (_journal.JournalError, OSError) as exc:
        raise AdoptApplyError("cannot open the adoption journal {} ({}); "
                              "fail-closed".format(JOURNAL_REL, exc))
    try:
        try:
            owner = _journal.read_lock_owner_at(jr_fd)
            # hold=True (round 4): every transaction directory descriptor is opened AT enumeration and
            # HELD through classification, and classify_state reads the frames through that same held
            # identity, so a transaction directory swapped onto its name after the enumeration (an
            # interrupted transaction renamed aside and replaced by an empty decoy) is still classified
            # from the enumerated directory's own frames, never reopened by name and read as clean.
            txns = _journal._journal_txn_dirs(jr_fd, journal_root, strict=True, hold=True)
            try:
                opened = sorted(t.name for t, tfd in txns
                                if _journal.classify_state(jr_fd, t, txn_fd=tfd) == "open")
            finally:
                for _t, tfd in txns:
                    _journal._close_fd_quietly(tfd)
        except (_journal.JournalError, OSError) as exc:
            raise AdoptApplyError("the adoption journal {} cannot be read ({}); "
                                  "fail-closed".format(JOURNAL_REL, exc))
    finally:
        _journal._close_fd_quietly(jr_fd)
    return owner, opened


def journal_clean_or_refuse(root_fd, journal_root):
    """The reconcile-first discipline (`opf record` step 1): inspect the adoption journal READ-ONLY
    (journal_state) and REFUSE on a held lock (a possibly-live owner is never seized) or any open
    transaction, directing the operator to the explicit reconcile() entry. An absent journal root is
    clean; an unreadable or corrupt journal refuses. Nothing is written here, recovery included."""
    owner, opened = journal_state(root_fd, journal_root)
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
    broken only through `_journal.reconcile_and_claim_stale`. Returns the (transaction, outcome) pairs;
    on the stale-lock path each outcome names what the break's own recovery DID (rolled-forward or
    rolled-back), never 'terminal' for work this call performed. The interrupted run itself stays
    refused, so the operator inspects the reconciled tree first."""
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
                # beneath the HELD jr_fd (round 3, the journal_state posture): the journal path is
                # never re-resolved after the contained open, so a concurrently swapped journal
                # cannot hide the held lock or substitute a decoy's.
                owner = _journal.read_lock_owner_at(jr_fd)
                txns = _journal._journal_txn_dirs(jr_fd, journal_root)
                opened = sorted(t.name for t in txns if _journal.classify_state(jr_fd, t) == "open")
                if owner is None and not opened:
                    return outcomes
                if owner is not None and not _journal.owner_confirmed_dead(owner):
                    raise AdoptApplyError("the adoption journal lock is held by a possibly-live owner "
                                          "(pid {}); it is never seized (fail-closed)".format(owner.get("pid")))
                stale = None
                if owner is not None:
                    # The stale-lock break reconciles every transaction ITSELF (under its arbitration
                    # lock), so the idempotent recover() re-run below would read each one 'terminal'.
                    # Record the pre-break states so the reported outcome names what that recovery DID.
                    stale = {t.name: _journal.classify_state(jr_fd, t) for t in txns}
                    if _journal.reconcile_and_claim_stale(journal_root, jr_fd, root_fd,
                                                          SESSION_ID) != "acquired":
                        raise AdoptApplyError("the adoption journal lock became live during "
                                              "reconciliation; it is never seized (fail-closed)")
                else:
                    _journal.acquire_lock(journal_root, SESSION_ID)
                try:
                    for txn in txns:
                        outcome = _journal.recover(jr_fd, txn, root_fd)
                        if stale is not None and stale.get(txn.name) == "open" and outcome == "terminal":
                            outcome = ("rolled-forward"
                                       if _journal.classify_state(jr_fd, txn) == "complete"
                                       else "rolled-back")
                        outcomes.append((txn.name, outcome))
                finally:
                    _journal.release_lock(journal_root)
            finally:
                _journal._close_fd_quietly(jr_fd)
        except (_journal.JournalError, OSError) as exc:
            raise AdoptApplyError("the adoption journal {} cannot be reconciled ({}); "
                                  "fail-closed".format(JOURNAL_REL, exc))
    finally:
        store._close_fd_exc_safe(root_fd)
    return outcomes


def _default_store_present_without_manifest(product_root):
    """True ONLY for the one CANNOT-EVALUATE posture adoption exists for (spec 14.2): neither pointer file
    exists (the resolver reached the DEFAULT location) and a FRESH discovery at the product root reports
    `.working/` present with no machine store ("present": a foreign store-shaped tree awaiting
    dispositions). "multiple" (ambiguous), "one" (a store raced in), "absent" (the resolver's
    cannot-evaluate came from something else), and every discovery error read False (fail-closed)."""
    try:
        root_fd = _open_product_root(product_root)
    except AdoptApplyError:
        return False
    try:
        try:
            status, _machine, _detail = store.discover_machine_store(root_fd, Path(product_root))
        except (store.StoreError, OSError):
            return False
    finally:
        store._close_fd_exc_safe(root_fd)
    return status == "present"


def _store_posture_or_refuse(product_root):
    """Spec 5.7: a run MUST hold the single-writer lease before mutating a store, and this slice has no
    lease join, so a product root that resolves a store refuses, and a pointer that resolves one OUTSIDE
    the product root is refused by name (adoption evidence and archives belong at that store root, which
    this slice does not support). Spec 14.2: every OTHER posture that cannot be evaluated (a malformed or
    unreadable pointer, multiple machine stores, an undiscoverable root) fails closed too. ONLY the two
    first-adoption states adoption exists for are admitted, neither of which has a store or a lease home:
    NOT-ADOPTED, and the DEFAULT location's `.working/` present WITHOUT a valid manifest, re-proved by a
    fresh discovery (never inferred from the resolver's detail text)."""
    res = store.resolve_store(product_root)
    if res.status == store.RESOLVED:
        if res.pointer_source != "default" and (
                res.store_root is None
                or Path(os.path.abspath(res.store_root)) != Path(os.path.abspath(product_root))):
            raise AdoptApplyError("a pointer ({}) names a store outside the product root; adoption "
                                  "evidence and archives belong at that store root, which this slice "
                                  "does not support (fail-closed)".format(res.pointer_source))
        raise AdoptApplyError("the product root resolves a store ({}); this apply shell carries no "
                              "single-writer lease join yet (spec 5.7), so it refuses before writing "
                              "(fail-closed)".format(res.machine_rel))
    if res.status == store.NOT_ADOPTED:
        return
    if res.pointer_source == "default" and _default_store_present_without_manifest(product_root):
        return
    raise AdoptApplyError("the store posture cannot be evaluated ({}); an ambiguous, malformed or "
                          "unreadable store input refuses before anything is written (spec 14.2, "
                          "fail-closed)".format(res.detail))


def _committed_base_or_refuse(root_fd, journal_root, run_id, phase):
    """Spec 4.2, 14.1: a phase transaction extends the run's COMMITTED base, so it requires the base
    journal transaction to classify complete AND the live inventory.toml to hold the exact bytes that
    transaction's INTENT published (bytes check_apply_ops proved derived and valid when the base
    composed). An inventory.toml on disk alone, hand-planted or swapped since the commit, never admits a
    phase (fail-closed). Returns the creates that INTENT published, ({path: content sha256}, {path:
    mode}), the committed preservation a retirement-stage removal pairs with and the mode it keeps."""
    base_rel = inventory_rel(run_id)
    try:
        if _journal._lstat_contained(root_fd, JOURNAL_REL + "/" + run_id) is None:
            raise AdoptApplyError("phase {!r} needs the run's COMMITTED base transaction, and none "
                                  "exists; an inventory.toml on disk never stands in for it (spec 4.2); "
                                  "nothing written (fail-closed)".format(phase))
        jr_fd = _journal.open_journal_root_fd(root_fd, JOURNAL_REL)
        try:
            if _journal.classify_state(jr_fd, journal_root / run_id) != "complete":
                raise AdoptApplyError("phase {!r} needs the run's COMMITTED base transaction, and {!r} "
                                      "is not complete; nothing written (fail-closed)".format(
                                          phase, run_id))
            frames, _torn, _good = _journal.read_frames(jr_fd, journal_root / run_id)
            intent = _journal._first(frames, _journal.F_INTENT)
        finally:
            _journal._close_fd_quietly(jr_fd)
    except (_journal.JournalError, OSError) as exc:
        raise AdoptApplyError("cannot inspect the run's base transaction ({}); fail-closed".format(exc))
    ops = intent.get("ops", []) if isinstance(intent, dict) else []
    published = next(((op.get("poststate") or {}).get("content-sha256") for op in ops
                      if isinstance(op, dict) and op.get("op") == "create"
                      and op.get("path") == base_rel), None)
    fst, data = _read_live(root_fd, base_rel)
    if published is None or fst is None or _sha256(data) != published:
        raise AdoptApplyError("phase {!r} needs the base inventory.toml the committed base transaction "
                              "published, and the live bytes are missing or do not match that "
                              "transaction's INTENT digest (spec 4.2); nothing written "
                              "(fail-closed)".format(phase))
    creates = [op for op in ops
               if isinstance(op, dict) and op.get("op") == "create" and isinstance(op.get("path"), str)]
    return ({op["path"]: (op.get("poststate") or {}).get("content-sha256") for op in creates},
            {op["path"]: (op.get("poststate") or {}).get("mode") for op in creates})


def run_adopt_transaction(product_root, run_id, compose, phase=None):
    """ONE journaled adoption transaction, the run's base transaction or one later phase's, through the
    shared 9.3 engine. Refusals BEFORE anything is written, in order: containment, a non-clean journal
    (reconcile-first: the adoption journal is inspected FIRST, per the module docstring), the store
    posture (no lease join, spec 5.7), an existing transaction of this run and phase, and for a phase
    anything but a committed, INTENT-digest-matched base inventory.toml (spec 4.2). Then, under the journal
    lock so observation and the journal's own capture are contiguous, compose(ops) fills a fresh ApplyOps
    against the live tree, the derived inventory seals it, check_apply_ops re-proves every invariant, and
    every committed archive copy a retirement removal pairs with is re-read live (_committed_pairs_live);
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
        journal_clean_or_refuse(root_fd, journal_root)
        _store_posture_or_refuse(product_root)
        try:
            prior = _journal._lstat_contained(root_fd, JOURNAL_REL + "/" + txn)
        except (_journal.JournalError, OSError) as exc:
            raise AdoptApplyError("cannot inspect the adoption journal ({}); fail-closed".format(exc))
        if prior is not None:
            raise AdoptApplyError("run {} already has its transaction {!r}: one run takes one transaction "
                                  "per phase, and changing approved work takes a fresh plan with its own run "
                                  "id (spec 14.1); nothing written (fail-closed)".format(run_id, txn))
        committed, committed_modes = {}, {}
        if phase is not None:
            committed, committed_modes = _committed_base_or_refuse(root_fd, journal_root, run_id, phase)
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
            ops.committed = dict(committed)
            ops.committed_modes = dict(committed_modes)
            compose(ops)
            ops.seal()
            findings = check_apply_ops(run_id, phase, ops.ops, ops.staged, committed, ops.moves, committed_modes)
            findings = findings or _committed_pairs_live(root_fd, run_id, phase, ops.ops, committed, ops.moves)
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
        store._close_fd_exc_safe(root_fd)


# --- the dispatch table: the three file ops execute (slice 2); every other op refuses ------------------

def _not_yet_executable(op_row, context=None):
    """The refusing handler behind every op no slice has landed yet: a not-yet-executable verdict. Later
    slices replace individual OP_HANDLERS entries with real executors; the self-test pins every entry
    outside EXECUTABLE_OPS to THIS handler and every such canonical row to a refusing status, so a
    silently-enabled op is a red."""
    name = op_row.get("op") if isinstance(op_row, dict) else None
    return schema.AdoptValidation(store.CANNOT_EVALUATE, [
        "op {!r} is not yet executable in this build; a later adoption slice lands it "
        "(fail-closed)".format(name)])


class OpContext:
    """What an executable handler composes against: the transaction's ApplyOps (its run, its phase and the
    live tree beneath its root descriptor), the plan's source rows keyed by path (a retire or move row's
    disposition and occupancy are plan facts, spec 14.1, never inferred from the tree), and the planned
    bytes of each create-file path, which the handler digest-checks against the row before staging them. A
    path two source rows claim is ambiguous and binds nothing, and a row whose path is not a string binds
    nothing either. `claimed` holds every create-file path and move destination the rows have named, so
    two rows can never land on one path (the live tree alone cannot see a path a row composed earlier in
    this transaction, or one a later stage creates)."""

    def __init__(self, ops, sources=(), content=None):
        self.ops = ops
        self.sources = {}
        for row in sources:
            path = row.get("path") if isinstance(row, dict) else None
            if isinstance(path, str):
                self.sources[path] = None if path in self.sources else row
        self.content = dict(content or {})
        self.claimed = set()

    def claim(self, path):
        """Claim one created path for this transaction's rows; a second claim refuses."""
        if path in self.claimed:
            raise AdoptApplyError("{!r} is named by two plan rows (a create-file path or move destination); "
                                  "a collision routes to a disposition, never an overwrite "
                                  "(fail-closed)".format(path))
        self.claimed.add(path)


def _stage(ops):
    """The stage a transaction executes file ops in: its base transaction is the apply stage and its
    RETIREMENT_PHASE transaction the retirement stage (spec 14.1); any other phase runs no file op."""
    if ops.phase is None:
        return "apply"
    if ops.phase == RETIREMENT_PHASE:
        return "retirement"
    raise AdoptApplyError("phase {!r} runs no file op: a file op composes only in the run's base "
                          "transaction (apply) or its {!r} transaction (fail-closed)".format(
                              ops.phase, RETIREMENT_PHASE))


def _bound_occupying(context, path, disposition, digest, destination=None):
    """Whether the ONE plan source row binding `path` with this disposition and digest occupies a managed
    destination; no such row (absent, ambiguous, or disagreeing with the op row) refuses, as does a row
    whose preservation is not the one this op lands (a non-occupying move's destination, else the run's
    archive copy; the planner's own pairing, spec 14.1). A source that is protected or lies in the store
    control area is never removable by this shell (check_apply_ops), so it refuses here, in the apply
    stage, before a base commits that a retirement stage could never finish."""
    row = context.sources.get(path)
    if not (isinstance(row, dict) and row.get("disposition") == disposition and row.get("digest") == digest
            and schema._is_bool(row.get("occupying"))):
        raise AdoptApplyError("no single plan source row binds {!r} as a {!r} source at this digest; its "
                              "occupancy is a plan fact, never inferred (fail-closed)".format(path, disposition))
    run_id = context.ops.run_id
    kept_at = destination if disposition == "move" and not row["occupying"] else archive_rel(run_id, path)
    if row.get("preservation") != kept_at:
        raise AdoptApplyError("the plan source row for {!r} records preservation {!r}, not {!r}, where this "
                              "op preserves it (fail-closed)".format(path, row.get("preservation"), kept_at))
    if schema._in_control_area(path) or schema.protected_destination(path, run_id) is not None or any(
            _within(path, home) for home in _CONTROL_HOMES):
        raise AdoptApplyError("{} source {!r} names the store control area or a protected path, which "
                              "this shell never removes (fail-closed)".format(disposition, path))
    return row["occupying"]


def _compose_create_file(op_row, context):
    """create-file, apply stage only: the planned bytes, digest-checked against content_digest, created at
    an absent path (ApplyOps.create re-observes it; a collision is never an overwrite). The store control
    area, this run's own archive and bundle included, is the engine's to write, never a plan row's: a row
    there could plant a forged preimage or an unlisted evidence payload. Reversal is the journal's
    pre-commit rollback, which removes the created file."""
    ops, path = context.ops, op_row["path"]
    if _stage(ops) != "apply":
        raise AdoptApplyError("create-file {!r} runs only in the apply stage (the run's base "
                              "transaction); nothing composed (fail-closed)".format(path))
    if schema._in_control_area(path) or schema.protected_destination(path, ops.run_id) is not None:
        raise AdoptApplyError("create-file {!r} names the store control area or a protected destination, "
                              "which only the engine's own preservation and inventory writes may use "
                              "(fail-closed)".format(path))
    data = context.content.get(path)
    if not isinstance(data, bytes) or "sha256:" + _sha256(data) != op_row["content_digest"]:
        raise AdoptApplyError("the planned bytes for create-file {!r} are missing or do not match its "
                              "content_digest (fail-closed)".format(path))
    context.claim(path)
    ops.create(path, data)


def _compose_retire_file(op_row, context):
    """retire-file. Apply stage: an occupying source is archived and removed preserve-first, a
    non-occupying one takes its retirement preimage and stays frozen in place. Retirement stage: a frozen
    source is removed once its committed preimage re-verifies; an occupying source, already out of the
    live tree, only has its archived bytes re-verified (recording its retirement is the receipt's, a later
    slice). Drift refuses in both stages. Reversal before commit is the journal's rollback, which restores
    a removed source from its digest-checked preimage."""
    ops, path, digest = context.ops, op_row["path"], op_row["preimage_digest"]
    occupying = _bound_occupying(context, path, "retire", digest)
    if _stage(ops) == "apply":
        if occupying:
            ops.archive_occupying(path, digest)
        else:
            ops.preserve(path, digest)
    elif occupying:
        ops.committed_copy(path, digest)
    else:
        ops.retire(path, digest)


def _compose_move_file(op_row, context):
    """move-file. In both stages the destination must be unprotected and, inside the store tree, strictly
    beneath the Move root (the planner's rule, spec 14.2; protected_destination alone exempts this run's
    own archive and bundle, where a move would plant a forged preimage or evidence payload). Apply stage:
    the destination must be absent; an occupying source is archived and removed preserve-first, a
    non-occupying one is only drift-checked and stays frozen. Retirement stage (spec 14.2: the relocation
    runs only after the green check): ApplyOps.relocate. The default destination
    `.working/archive/moved/<source-path>` is the planner's (store.moved_dest). Reversal before commit is
    the journal's rollback: the destination create is undone and a removed source restored."""
    ops = context.ops
    source, destination, digest = op_row["source"], op_row["destination"], op_row["source_digest"]
    occupying = _bound_occupying(context, source, "move", digest, destination)
    protected = schema.protected_destination(destination, ops.run_id)
    # the store-tree name compares case-insensitively, as protected_destination's names do, so a case
    # variant (`.Working/x`) that a case-insensitive filesystem lands inside the store is refused too.
    if (protected is None and _within(destination.casefold(), store.WORKING_DIRNAME)
            and not destination.startswith(_MOVED_ROOT + "/")):
        protected = "{!r} lies inside the store tree but not beneath {}/ (spec 14.2)".format(
            destination, _MOVED_ROOT)
    if protected is not None:
        raise AdoptApplyError("move-file destination is protected: {} (fail-closed)".format(protected))
    context.claim(destination)
    if _stage(ops) == "apply":
        ops.require_absent(destination)
        if occupying:
            ops.archive_occupying(source, digest)
        else:
            ops.frozen(source, digest)
    else:
        ops.relocate(source, destination, digest, occupying)


_FILE_OPS = {"create-file": _compose_create_file, "move-file": _compose_move_file,
             "retire-file": _compose_retire_file}
# The ops this build executes; the self-test pins the table to exactly this set.
EXECUTABLE_OPS = frozenset(_FILE_OPS)


def _execute_file_op(op_row, context=None):
    """The one slice-2 handler behind the three file ops: compose this row's journal ops into the
    transaction its OpContext carries and report VALID; any refusal (drift, a collision, a wrong stage, an
    unbound source, unplanned bytes) is CANNOT-EVALUATE naming the reason. Every refusal is raised before
    the row appends an op. Nothing is written here: the composed ops run only inside
    run_adopt_transaction, after check_apply_ops re-proves the whole list."""
    if not isinstance(context, OpContext) or not isinstance(context.ops, ApplyOps):
        return schema._cannot("op {!r} composes only inside an adoption transaction, through its "
                              "OpContext; nothing composed (fail-closed)".format(op_row.get("op")))
    try:
        _FILE_OPS[op_row["op"]](op_row, context)
    except AdoptApplyError as exc:
        return schema._cannot(str(exc))
    return schema._ok()


# Keyed by the closed ADOPT_OPS vocabulary; the self-test reconciles this table against
# ADOPT_OPS_BY_NAME in BOTH directions so it can neither drop nor invent an op.
OP_HANDLERS = {
    "install-pack": _not_yet_executable,
    "init-store": _not_yet_executable,
    "create-file": _execute_file_op,
    "plant-governance": _not_yet_executable,
    "register-unmanaged": _not_yet_executable,
    "move-file": _execute_file_op,
    "repoint-consumer": _not_yet_executable,
    "retire-file": _execute_file_op,
    "enable-hook": _not_yet_executable,
    "render-views": _not_yet_executable,
    "record-adoption": _not_yet_executable,
}


def compose_rows(rows, sources=(), content=None):
    """The compose callable run_adopt_transaction takes for a list of plan op rows: dispatch each row, in
    order, into the transaction through one OpContext, and refuse the whole transaction (AdoptApplyError,
    nothing written) on the first refusing verdict, an op whose slice has not landed included."""
    rows = list(rows)

    def compose(ops):
        context = OpContext(ops, sources, content)
        for i, row in enumerate(rows):
            verdict = dispatch(row, context)
            if verdict.status != store.VALID:
                raise AdoptApplyError("plan op[{}] ({!r}) refused: {}".format(
                    i, row.get("op") if isinstance(row, dict) else None, "; ".join(verdict.findings)))
    return compose


def dispatch(op_row, context=None):
    """Validate, then dispatch ONE plan op row. A malformed row propagates the validator's refusing
    verdict; an op with no registered handler (dispatch-roster drift) is CANNOT-EVALUATE, never a skip.
    dispatch itself never writes: an executable handler only composes journal ops into the transaction
    its OpContext carries, and every other handler refuses."""
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
    statuses, byte comparisons and journal states; each check asserting a refusal of the executable shell
    or of apply input also matches one reason keyword so the refusal is attributed to the rule under test
    (validator and dispatch gradings are asserted on their returned status, with a named finding matched
    where that finding is itself the contract). No git and no network; the only subprocesses are this
    module's own --kill-injection-child interpreters (the crash matrix, section 12), and every write lands
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
    import signal
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

    def dead_pid():
        """A pid with POSITIVE evidence of death (ProcessLookupError on signal 0), for the stale-lock
        vector. Nothing is spawned or signalled; EPERM or any ambiguity keeps searching."""
        pid = (1 << 22) - 1
        for _ in range(4096):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return pid
            except OSError:
                pass
            pid -= 1
        return None

    ZERO = "0" * 64
    now = datetime.datetime(2026, 9, 17, 12, 0, 0, tzinfo=datetime.timezone.utc)
    VALID, INVALID, CANNOT = store.VALID, store.INVALID, store.CANNOT_EVALUATE

    # 0: the dispatch roster reconciles against the closed vocabulary in BOTH directions, exactly the
    # three file ops (slice 2) are pinned to their executor and every other entry to the refusing handler,
    # and every canonical row refuses outside a transaction. A silently-enabled op is a red; the slice
    # that legitimately lands an op updates these pins in the same change.
    check("handlers-cover-vocabulary", set(OP_HANDLERS) == set(schema.ADOPT_OPS_BY_NAME))
    check("handlers-executable-exactly-the-file-ops",
          EXECUTABLE_OPS == {"create-file", "move-file", "retire-file"}
          and {n for n, h in OP_HANDLERS.items() if h is not _not_yet_executable} == EXECUTABLE_OPS
          and all(OP_HANDLERS[n] is _execute_file_op for n in EXECUTABLE_OPS))
    for name in sorted(schema.ADOPT_OP_NAMES - EXECUTABLE_OPS):
        res = dispatch(schema.canonical_op(name))
        check("op-{}-refuses-not-yet-executable".format(name),
              res.status == CANNOT and any("not yet executable" in f for f in res.findings))
    for name in sorted(EXECUTABLE_OPS):
        res = dispatch(schema.canonical_op(name))
        check("op-{}-refuses-without-transaction-context".format(name),
              res.status == CANNOT and any("OpContext" in f for f in res.findings))
    check("dispatch-out-of-vocab-cannot-eval", dispatch(dict(op="delete-everything")).status == CANNOT)
    check("dispatch-malformed-row-invalid", dispatch(dict(op="create-file", path="a/b")).status == INVALID)

    # round 3: the module introduction must name the LIVE status surface (`opf adopt status` reads both
    # adoption homes through this module) instead of calling the module dead code. The introduction is read
    # from the source, so the check does not depend on the interpreter level.
    intro = _optlevel.source_docstring(__file__) or ""
    check("module-intro-names-the-live-status-surface",
          "dead code" not in intro and "opf adopt status" in intro
          and "the CLI verb remain" not in intro)

    # 1: run identity. The homes grammar and the schema's shipped grammar agree on every vector; the mint
    # validates its own output; the import family and traversal spellings are refused.
    rid = mint_run_id(now, "0123456789abcdef")
    other_run = mint_run_id(now, "fedcba9876543210")
    check("mint-run-id-grammar", is_run_id(rid) and rid == "adopt-20260917T120000Z-0123456789abcdef")
    check("mint-run-id-deterministic", mint_run_id(now, "0123456789abcdef") == rid)
    check("mint-naive-now-refused",
          "aware UTC" in (refusal(mint_run_id, now.replace(tzinfo=None), "0123456789abcdef") or ""))
    check("mint-bad-nonce-refused", "16 lowercase hex" in (refusal(mint_run_id, now, "XYZ") or ""))
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
    check("homes-bad-phase-refused",
          "invalid evidence inventory phase" in (refusal(inventory_rel, rid, "Bad") or ""))
    check("homes-bad-run-refused", "invalid adoption run-id" in
          (refusal(evidence_home_rel, "imp-20260917T120000Z-0123456789abcdef") or ""))
    # item 11: the Move root and this run's archive root DERIVE from the public store constructors.
    check("homes-moved-root-from-store",
          _MOVED_ROOT == ".working/archive/moved" and store.moved_dest("a/b.md") == _MOVED_ROOT + "/a/b.md")
    check("homes-archive-root-from-store",
          _archive_root(rid) == ".working/archive/adoption/" + rid
          and archive_rel(rid, "a/b.md") == _archive_root(rid) + "/a/b.md")

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
    check("emit-malformed-refused", "inventory refused" in
          (refusal(emit_inventory, rid, [dict(path="docs/x.md", size=1, sha256=ZERO)]) or ""))

    # 3b: a path claimed by BOTH the base and a phase inventory of one bundle cannot evaluate (the
    # verifier's cross-inventory duplicate-claim guard, over a hand-built bundle).
    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        _root3 = Path(temp).resolve()
        _payload_rel = home + "/payload/a.md"
        (_root3 / _payload_rel).parent.mkdir(parents=True)
        (_root3 / _payload_rel).write_bytes(b"alpha\n")
        (_root3 / inventory_rel(rid)).write_bytes(
            emit_inventory(rid, [inventory_row(_payload_rel, b"alpha\n")]))
        (_root3 / inventory_rel(rid, "completion")).write_bytes(
            emit_inventory(rid, [inventory_row(_payload_rel, b"alpha\n")]))
        crossed = verify_bundle(_root3, rid)
        check("verify-cross-inventory-duplicate-cannot-eval",
              crossed.status == CANNOT and any("more than one inventory" in f for f in crossed.findings))

    # 3c (round 3): bundle verification RETAINS its directory identities from the listing through every
    # inventory and payload read, so a directory swapped onto the bundle pathname mid-verification is
    # never re-resolved and two directories neither of which verifies alone can never combine into one
    # false success. Vector A injects the swap immediately before the payload read (the round-3 QA
    # reproduction: reading the original's inventory, then the replacement's payload bytes, reported
    # VALID); vector B injects it immediately after the listing (the original's listing combined with
    # the replacement's inventory and payload). Both must instead report the ORIGINAL bundle's payload
    # drift, read through the retained descriptors.
    _swap_payload_rel = home + "/payload.txt"

    def _swap_fixture(base, tag, replacement_files):
        broot = base / tag
        (broot / _swap_payload_rel).parent.mkdir(parents=True)
        (broot / _swap_payload_rel).write_bytes(b"BAD!!")
        (broot / inventory_rel(rid)).write_bytes(
            emit_inventory(rid, [inventory_row(_swap_payload_rel, b"GOOD!")]))
        repl = broot / "replacement"
        repl.mkdir()
        for name, payload in replacement_files:
            (repl / name).write_bytes(payload)

        def swap():
            os.rename(broot / home, str(broot / home) + ".aside")
            os.rename(repl, broot / home)
        return broot, swap

    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        _swap_base = Path(temp).resolve()
        # vector A: the replacement alone is CANNOT-EVALUATE (malformed inventory), the original alone
        # is INVALID (payload drift); the swap fires immediately before the payload read.
        broot_a, swap_a = _swap_fixture(_swap_base, "read", (
            ("inventory.toml", b"invalid"), ("payload.txt", b"GOOD!")))
        check("verify-swap-fixture-original-invalid", verify_bundle(broot_a, rid).status == INVALID)
        _read_fired = []
        _real_lstat_at = _journal._lstat_at

        def _reading_swap(pfd, name):
            # the LIVE read path (round 4): _verify_bundle_at stats each listed payload through
            # _journal._lstat_at on its RETAINED parent immediately before read_retained reads it, so
            # hooking here injects the swap between the listing and the payload read. The round-3 hook
            # patched _journal._read_contained, which the retained-descriptor rewrite no longer calls for
            # bundle reads, so its swap never fired and the vector passed vacuously; the fired check below
            # keeps this vector honest.
            if name == "payload.txt" and not _read_fired:
                _read_fired.append(name)
                swap_a()
            return _real_lstat_at(pfd, name)

        with mock.patch.object(_journal, "_lstat_at", _reading_swap):
            swapped = verify_bundle(broot_a, rid)
        check("verify-swap-before-payload-read-injection-fired", _read_fired == ["payload.txt"])
        check("verify-swap-before-payload-read-still-original-drift",
              swapped.status == INVALID and any("payload drift" in f for f in swapped.findings))
        # vector B: the replacement alone is CANNOT-EVALUATE (a malformed phase inventory its own
        # listing would surface); the swap fires immediately after the bundle listing.
        broot_b, swap_b = _swap_fixture(_swap_base, "list", (
            ("inventory.toml", emit_inventory(rid, [inventory_row(_swap_payload_rel, b"GOOD!")])),
            ("payload.txt", b"GOOD!"), ("inventory-completion.toml", b"invalid")))
        _list_fired = []
        _real_listdir = os.listdir

        def _listing_swap(target):
            names = _real_listdir(target)
            if isinstance(target, int) and not _list_fired:
                _list_fired.append(target)
                swap_b()
            return names

        with mock.patch.object(os, "listdir", _listing_swap):
            swapped = verify_bundle(broot_b, rid)
        check("verify-swap-after-listing-injection-fired", _list_fired != [])
        check("verify-swap-after-listing-still-original-drift",
              swapped.status == INVALID and any("payload drift" in f for f in swapped.findings))

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
    # a write over a live path destroys its bytes exactly as a removal does, so it takes the SAME pinned,
    # same-transaction, digest-verified archive-copy pairing (spec 14.2); an rmdir may target only a
    # directory this transaction created; and the store control roots (_opf_store.STORE_ROOT_CONTROL_DIRS,
    # the adoption journal's own tree included) are never operands of any kind.
    new_body = b"# rendered view\n"

    def w(path, payload, pin=None):
        post = dict(kind="file")
        post["content-sha256"] = _sha256(payload)
        op = dict(op="write", path=path, poststate=post)
        if pin is not None:
            op["source-poststate"] = dict(kind="file", mode=FILE_MODE, sha256=_sha256(pin))
        return op

    check("compose-unpinned-write-refused", any("source-poststate" in f for f in findings_of(
        *sealed([w(src, new_body)], {src: new_body}))))
    check("compose-write-without-copy-refused", any("preserve-first" in f for f in findings_of(
        *sealed([w(src, new_body, pin=body)], {src: new_body}))))
    check("compose-write-pin-copy-mismatch-refused", any("differs" in f for f in findings_of(
        *sealed([c(copy, b"other\n"), w(src, new_body, pin=body)],
                {copy: b"other\n", src: new_body}))))
    check("compose-paired-write-admitted", findings_of(
        *sealed([c(copy, body), w(src, new_body, pin=body)], {copy: body, src: new_body})) == [])
    check("compose-live-rmdir-refused", any("did not create" in f for f in findings_of(
        *sealed([dict(op="rmdir", path="legacy/empty", poststate=dict(kind="absent"))], {}))))
    check("compose-control-root-create-refused", any("control root" in f for f in findings_of(
        *sealed([c(".git/hooks/post-checkout", body)], {".git/hooks/post-checkout": body}))))
    journal_file = JOURNAL_REL + "/x/frames.log"
    check("compose-journal-remove-refused", any("control root" in f for f in findings_of(
        *sealed([c(archive_rel(rid, journal_file), body), _pinned_remove(journal_file, body, FILE_MODE)],
                {archive_rel(rid, journal_file): body}))))
    mv = store.moved_dest("legacy/OLD.md")
    check("derive-rows-claims-move-destination",
          derive_rows(rid, [c(mv, body)], {mv: body}) == [inventory_row(mv, body)])
    # K1: no protected destination (_opf_adopt.protected_destination, the ONE predicate the planner shares) is
    # an operand: .git and .aiqt at any depth, every store control root (staging, journals and the imports
    # tree too, not only the archive and evidence homes), the product-root pointers and the store .gitignore.
    for label, path in (("nested-aiqt", "docs/.aiqt/x.md"), ("nested-git", "docs/.git/x"),
                        ("journals", ".working/journals/x"), ("staging", ".working/staging/x"),
                        ("imports", ".working/imports/x"), ("pointer", ".opf.toml"),
                        ("local-pointer", ".opf.local.toml"), ("store-gitignore", ".working/.gitignore"),
                        ("casefold-git", ".GIT/x"), ("casefold-pointer", ".OPF.toml"),
                        ("casefold-staging", ".Working/staging/x"), ("casefold-archive", ".WORKING/archive/x"),
                        ("casefold-gitignore", ".working/.GITIGNORE")):
        refused = findings_of(*sealed([c(path, body)], dict([(path, body)])))
        check("compose-protected-{}-create-refused".format(label),
              any("protected destination" in f for f in refused))

    def mkdir(path):
        return dict(op="mkdir", path=path, poststate=dict(kind="dir", mode=DIR_MODE))
    check("compose-protected-mkdir-refused", any("protected destination" in f for f in findings_of(
        *sealed([mkdir(".working/staging")], dict()))))
    check("compose-own-home-mkdirs-admitted", findings_of(*sealed(
        [mkdir(d) for d in (".working", ".working/archive", ".working/archive/moved",
                            ".working/archive/moved/legacy")] + [c(mv, body)], dict([(mv, body)]))) == [])

    # K1 parity: for each destination, a plan moving a source there validates exactly when apply admits the
    # create of it, both through the one shared predicate (its protected names compared case-insensitively).
    def move_plan(destination):
        p = schema.canonical_plan()
        digest = "sha256:" + _sha256(body)
        p["ops"].insert(0, dict(op="move-file", source="legacy/MOVE.md", destination=destination,
                                source_digest=digest))
        p["sources"] = sorted(p["sources"] + [dict(path="legacy/MOVE.md", digest=digest, disposition="move",
                                                   occupying=False, preservation=destination)],
                              key=lambda r: r["path"])
        p["effects"] = schema.derive_effects(p["ops"], p["sources"], schema.store_manifest(p["store"]))
        return p
    check("parity-canonical-plan-run", schema.canonical_plan()["run_id"] == rid)
    for path, admitted in (("adopter/moved.md", True), (store.moved_dest("legacy/MOVE.md"), True),
                           (store.moved_dest(".working/TODO.md"), True), (".aiqt/x.md", False),
                           ("docs/.aiqt/x.md", False), ("docs/.git/x", False), (".opf.toml", False),
                           (".opf.local.toml", False), (".working/.gitignore", False),
                           (".working/journals/x", False), (".working/staging/x", False),
                           (".working/imports/x", False), (".working/imported/x", False),
                           (archive_rel(other_run, "x.md"), False), (".GIT/x", False),
                           ("docs/.Aiqt/x.md", False), (".OPF.toml", False), (".Opf.Local.toml", False),
                           (".Working/staging/x", False), (".WORKING/.GITIGNORE", False)):
        planned = schema.validate_plan(move_plan(path)).status == VALID
        applied = findings_of(*sealed([c(path, body)], dict([(path, body)]))) == []
        check("parity-" + path, planned == applied == admitted)

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

    # The whole-tree state the file-op checks (section 11 on) compare: EVERY entry beneath the root, `.aiqt/`
    # included, as (kind, mode, bytes or link target), never followed; directories, symlinks and special
    # entries count, as do modes. journal=False leaves out exactly the adoption journal root's subtree and
    # its ancestor directories (the journal's own bookkeeping), for comparisons across a transaction.
    journal_dirs = tuple("/".join(JOURNAL_REL.split("/")[:i]) for i in range(1, JOURNAL_REL.count("/") + 2))

    def tree_state(root, journal=True):
        out = {}
        for dirpath, dirnames, filenames in os.walk(root):
            base = os.path.relpath(dirpath, root)
            for name in dirnames + filenames:
                rel = name if base == "." else base + "/" + name
                if not journal and (rel in journal_dirs or _within(rel, JOURNAL_REL)):
                    continue
                full = os.path.join(dirpath, name)
                st = os.lstat(full)
                mode = stat.S_IMODE(st.st_mode)
                if stat.S_ISLNK(st.st_mode):
                    out[rel] = ("link", mode, os.readlink(full))
                elif stat.S_ISDIR(st.st_mode):
                    out[rel] = ("dir", mode, None)
                elif stat.S_ISREG(st.st_mode):
                    out[rel] = ("file", mode, Path(full).read_bytes())
                else:
                    out[rel] = ("special", mode, None)
            if not journal:
                dirnames[:] = [d for d in dirnames if (d if base == "." else base + "/" + d) != JOURNAL_REL]
        return out

    def untouched_save_journal_dirs(before, root):
        """A refusal's whole-tree predicate: every entry of `before` is unchanged in kind, mode and bytes or
        target, and the ONLY new entries are the adoption journal root and its ancestors, as directories
        (the journal bookkeeping run_adopt_transaction creates before it composes); nothing else is added
        or removed, the journal root's own contents included."""
        after = tree_state(root)
        new = set(after) - set(before)
        return (all(k in journal_dirs and after[k][0] == "dir" for k in new)
                and dict((k, v) for k, v in after.items() if k not in new) == before)

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
        # a listed payload over the journal's contained-read cap cannot be hashed: CANNOT-EVALUATE naming
        # the cap (the disclosed capacity limit; compose, capture and poststate reads share the ceiling,
        # so the shell itself never writes such a bundle), never a truncated or unbounded read.
        archived.write_bytes(b"x" * (_journal._MAX_PRODUCT_READ_BYTES + 1))
        overcap = verify_bundle(root, rid)
        check("verify-over-cap-payload-cannot-eval",
              overcap.status == CANNOT and any("read cap" in f for f in overcap.findings))
        archived.unlink()
        archived.write_bytes(files[".working/TODO.md"])
        check("verify-restored-after-over-cap-valid", verify_bundle(root, rid).status == VALID)
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
        # an op against the adoption journal's own tree (a store control root) is refused, so a committed
        # record can never be archived away by a later run.
        frames_rel = JOURNAL_REL + "/" + rid + "/frames.log"
        frames_bytes = (root / frames_rel).read_bytes()
        journal_hit = refusal(run_adopt_transaction, root, other_run, lambda ops: ops.archive_occupying(
            frames_rel, plan_digest(frames_bytes)))
        check("apply-journal-operand-refused",
              journal_hit is not None and "control root" in journal_hit
              and (root / frames_rel).read_bytes() == frames_bytes and txn_state(root, rid) == "complete")
        # the phase gate binds the LIVE inventory.toml to the committed base transaction's INTENT digest,
        # so bytes swapped after the commit refuse a later phase.
        swapped = emit_inventory(rid, [inventory_row(home + "/decoy.md", b"decoy\n")])
        base.write_bytes(swapped)
        drifted_base = refusal(run_adopt_transaction, root, rid, lambda ops: ops.create(
            evidence_home_rel(rid) + "/audit/result.toml", b"green = true\n"), phase="audit")
        check("phase-swapped-inventory-refused",
              saved is not None and swapped != saved and drifted_base is not None
              and "INTENT" in drifted_base)
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
        # a hand-planted inventory.toml with NO base journal transaction never admits a phase (spec 4.2).
        planted = root / inventory_rel(rid)
        planted.parent.mkdir(parents=True, exist_ok=True)
        planted.write_bytes(b"not toml")
        forged = refusal(run_adopt_transaction, root, rid, lambda ops: ops.create(
            evidence_home_rel(rid) + "/completion/result.toml", b"x\n"), phase="completion")
        check("phase-planted-inventory-refused", forged is not None and "never stands in" in forged)

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

    # 5b: hand-built op lists through the EXECUTABLE shell: a write over a live path, an rmdir of a live
    # directory, and a create under a store control root are each refused before the transaction opens,
    # the tree untouched.
    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        root, files = fixture(temp)
        (root / "legacy/empty").mkdir(parents=True)
        before = snapshot(root)

        def hand_write(ops):
            new = b"# rendered view\n"
            post = dict(kind="file")
            post["content-sha256"] = _sha256(new)
            ops.ops.append(dict(op="write", path=".working/TODO.md", poststate=post))
            ops.staged[".working/TODO.md"] = new

        overwrote = refusal(run_adopt_transaction, root, rid, hand_write)
        check("apply-hand-built-write-refused",
              overwrote is not None and "source-poststate" in overwrote
              and (root / ".working/TODO.md").read_bytes() == files[".working/TODO.md"]
              and not (root / archive_rel(rid, ".working/TODO.md")).exists())

        def hand_rmdir(ops):
            ops.ops.append(dict(op="rmdir", path="legacy/empty", poststate=dict(kind="absent")))

        deleted = refusal(run_adopt_transaction, root, rid, hand_rmdir)
        check("apply-hand-built-rmdir-refused",
              deleted is not None and "did not create" in deleted and (root / "legacy/empty").is_dir())
        hooked = refusal(run_adopt_transaction, root, rid, lambda ops: ops.create(
            ".git/hooks/post-checkout", b"#!/bin/sh\necho hi\n", mode=0o755))
        check("apply-control-root-create-refused",
              hooked is not None and "control root" in hooked and not (root / ".git").exists()
              and snapshot(root) == before and lock_free(root))

    # 5c: the store-posture gate fails closed on EVERY cannot-evaluate posture except the one
    # first-adoption state the fixtures above already exercise (a present default .working/ without a
    # valid manifest): two machine stores (ambiguous), a malformed pointer, and a pointer resolving a
    # store OUTSIDE the product root each refuse with nothing written; NOT-ADOPTED is admitted.
    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        import _opf_init
        root = Path(temp).resolve()
        for sub in ("toml", "other"):
            (root / ".working" / sub).mkdir(parents=True)
            (root / ".working" / sub / "manifest.toml").write_text(_opf_init.build_manifest(),
                                                                   encoding="utf-8")
        before = snapshot(root)
        twin = refusal(run_adopt_transaction, root, rid, lambda ops: ops.archive_occupying(
            ".working/toml/manifest.toml",
            plan_digest((root / ".working/toml/manifest.toml").read_bytes())))
        check("posture-multiple-stores-refused",
              store.resolve_store(root).status == CANNOT and twin is not None
              and "cannot be evaluated" in twin and snapshot(root) == before
              and not (root / ".aiqt").exists())
    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        root = Path(temp).resolve()
        (root / ".opf.toml").write_bytes(b"this is [not toml")
        pointed = refusal(run_adopt_transaction, root, rid,
                          lambda ops: ops.create(evidence_home_rel(rid) + "/x.md", b"x\n"))
        check("posture-malformed-pointer-refused",
              store.resolve_store(root).status == CANNOT and pointed is not None
              and "cannot be evaluated" in pointed and not (root / ".aiqt").exists())
    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        import _opf_init
        pair = Path(temp).resolve()
        root, outside = pair / "product", pair / "elsewhere"
        (outside / ".working/toml").mkdir(parents=True)
        (outside / ".working/toml/manifest.toml").write_text(_opf_init.build_manifest(), encoding="utf-8")
        root.mkdir()
        (root / ".opf.toml").write_text('[store]\ntarget = "dir:' + str(outside) + '"\n', encoding="utf-8")
        outward = refusal(run_adopt_transaction, root, rid,
                          lambda ops: ops.create(evidence_home_rel(rid) + "/x.md", b"x\n"))
        check("posture-outside-pointer-refused",
              store.resolve_store(root).status == store.RESOLVED and outward is not None
              and "outside the product root" in outward
              and not (root / ".aiqt").exists() and not (root / ".working").exists())
    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        root = Path(temp).resolve()
        (root / "legacy").mkdir()
        (root / "legacy/RULES.md").write_bytes(b"old rules\n")
        was_unadopted = store.resolve_store(root).status == store.NOT_ADOPTED
        fresh, _why = attempt(run_adopt_transaction, root, rid,
                              lambda ops: ops.preserve("legacy/RULES.md", plan_digest(b"old rules\n")))
        check("not-adopted-root-admitted",
              was_unadopted and fresh == rid and verify_bundle(root, rid).status == VALID)

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

    # 6b: the spec 14.2 apply-side verification checkpoint. A same-length fault injected into the archive
    # copy's own destination write (the staged bytes verify; the DISK bytes differ) is caught by the
    # journal's read-back BEFORE the paired source removal runs: at every after-apply seam each source is
    # either live and byte-identical or archived byte-identically, and the transaction rolls back.
    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        root, files = fixture(temp)
        before = snapshot(root)
        target = archive_rel(rid, ".working/TODO.md")
        armed = []
        real_verify = _journal._verify_staged_digest

        def arming_verify(op, payload):
            real_verify(op, payload)
            if op["path"] == target:
                armed.append(True)

        real_write = _journal._write_all

        def faulty_write(fd, data):
            if armed:
                armed.clear()
                data = b"X" * len(data)      # same length, wrong bytes: only a read-back can catch it
            return real_write(fd, data)

        def preserved_now():
            for pth, payload in files.items():
                live, cp = root / pth, root / archive_rel(rid, pth)
                if live.exists() and live.read_bytes() == payload:
                    continue
                if cp.exists() and cp.read_bytes() == payload:
                    continue
                return False
            return True

        seams = []

        def observing_kill(name):
            if name.startswith("after-apply-"):
                seams.append(preserved_now())

        with mock.patch.object(_journal, "_verify_staged_digest", arming_verify), \
                mock.patch.object(_journal, "_write_all", faulty_write), \
                mock.patch.object(_journal, "_kill_point", observing_kill):
            corrupt = refusal(run_adopt_transaction, root, rid, compose_full(files))
        check("archive-write-fault-never-leaves-source-unpreserved", bool(seams) and all(seams))
        check("archive-write-fault-rolls-back",
              corrupt is not None and "rolled back" in corrupt and snapshot(root) == before
              and lock_free(root))

    # 6c: the spec 14.2 rollback-side checkpoint. A fault injected into the SOURCE-restoring write of a
    # genuine rollback is caught by the restored-bytes read-back: the reversal STOPS before the aborted
    # run's archive copy is discarded and before any prestate is reported, the journal lock is RETAINED
    # so the next run refuses into reconcile(), and the explicit reconcile then completes the rollback.
    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        root, files = fixture(temp)
        before = snapshot(root)
        src2 = ".working/TODO.md"
        real_verify = _journal._verify_staged_digest

        def failing_inventory(op, payload):
            if op["path"] == inventory_rel(rid):
                raise _journal.JournalError("injected failure at the inventory publication")
            return real_verify(op, payload)

        armed = []
        real_restore = _journal._restore_preimage

        def arming_restore(jr_fd, txn_dir, rfd, op, op_index=0):
            if op.get("op") == "remove" and op.get("path") == src2:
                armed.append(True)
            return real_restore(jr_fd, txn_dir, rfd, op, op_index)

        real_write = _journal._write_all

        def faulty_write(fd, data):
            if armed:
                armed.clear()
                data = b"X" * len(data)
            return real_write(fd, data)

        with mock.patch.object(_journal, "_verify_staged_digest", failing_inventory), \
                mock.patch.object(_journal, "_restore_preimage", arming_restore), \
                mock.patch.object(_journal, "_write_all", faulty_write):
            faulted = refusal(run_adopt_transaction, root, rid, compose_full(files))
        check("restore-fault-never-reports-prestate",
              faulted is not None and "rolled back" not in faulted and "reconcile" in faulted)
        check("restore-fault-retains-archive",
              (root / archive_rel(rid, src2)).exists()
              and (root / archive_rel(rid, src2)).read_bytes() == files[src2])
        check("restore-fault-retains-lock", not lock_free(root))
        _journal.release_lock(_journal_root(root))   # the crashed owner, in this in-process simulation
        check("restore-fault-reconciles-to-prestate",
              (rid, "rolled-back") in reconcile(root) and snapshot(root) == before and lock_free(root))

    # 6d: rollback ORDER is pinned, not only final equality: at the moment each archive copy's create-undo
    # discards it, its source has ALREADY been restored byte-identical (spec 14.2: restore, verify, then
    # discard, never reversed), so a reversal that unlinks the copies first goes red here.
    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        root, files = fixture(temp)
        before = snapshot(root)
        real_verify = _journal._verify_staged_digest

        def failing_inventory(op, payload):
            if op["path"] == inventory_rel(rid):
                raise _journal.JournalError("injected failure at the inventory publication")
            return real_verify(op, payload)

        real_restore = _journal._restore_preimage
        arch = _archive_root(rid) + "/"
        misordered = []

        def watching_restore(jr_fd, txn_dir, rfd, op, op_index=0):
            if op.get("op") == "create" and str(op.get("path", "")).startswith(arch):
                source = op["path"][len(arch):]
                live = root / source
                if source in files and not (live.exists() and live.read_bytes() == files[source]):
                    misordered.append(op["path"])
            return real_restore(jr_fd, txn_dir, rfd, op, op_index)

        with mock.patch.object(_journal, "_verify_staged_digest", failing_inventory), \
                mock.patch.object(_journal, "_restore_preimage", watching_restore):
            aborted2 = refusal(run_adopt_transaction, root, rid, compose_full(files))
        check("abort-restores-sources-before-archive-discard",
              aborted2 is not None and "rolled back" in aborted2 and not misordered
              and snapshot(root) == before and lock_free(root))

    # 6e: RESTARTABLE restoration of a read-only source (spec 14.2). The same fault as 6c, but the source
    # is READ-ONLY (0400) or writable (0644, the control leg): the restore checkpoint verifies the bytes
    # BEFORE the recorded prestate mode is installed, so the faulted attempt fails closed exactly as 6c
    # and the LATER reconcile still finishes to the exact prestate bytes AND mode once the fault is gone,
    # never wedging on an EACCES reopen of the half-restored file.
    for ro_mode in (0o644, 0o400):
        with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
            root = Path(temp).resolve()
            keep = "legacy/KEEP.md"
            prior = b"prior read-only bytes\n"
            (root / keep).parent.mkdir(parents=True)
            (root / keep).write_bytes(prior)
            (root / keep).chmod(ro_mode)

            def compose_keep(ops):
                ops.archive_occupying(keep, plan_digest(prior))

            real_verify = _journal._verify_staged_digest

            def failing_inventory(op, payload):
                if op["path"] == inventory_rel(rid):
                    raise _journal.JournalError("injected failure at the inventory publication")
                return real_verify(op, payload)

            armed = []
            real_restore = _journal._restore_preimage

            def arming_restore(jr_fd, txn_dir, rfd, op, op_index=0):
                if op.get("op") == "remove" and op.get("path") == keep:
                    armed.append(True)
                return real_restore(jr_fd, txn_dir, rfd, op, op_index)

            real_write = _journal._write_all

            def faulty_write(fd, data):
                if armed:
                    armed.clear()
                    data = b"X" * len(data)
                return real_write(fd, data)

            with mock.patch.object(_journal, "_verify_staged_digest", failing_inventory), \
                    mock.patch.object(_journal, "_restore_preimage", arming_restore), \
                    mock.patch.object(_journal, "_write_all", faulty_write):
                faulted = refusal(run_adopt_transaction, root, rid, compose_keep)
            tag = "restore-fault-mode-{:04o}".format(ro_mode)
            check(tag + "-fails-closed",
                  faulted is not None and "rolled back" not in faulted and "reconcile" in faulted
                  and txn_state(root, rid) == "open" and not lock_free(root))
            _journal.release_lock(_journal_root(root))   # the crashed owner, in this in-process simulation
            outcomes, _why = attempt(reconcile, root)
            check(tag + "-reconciles-to-exact-prestate",
                  outcomes is not None and (rid, "rolled-back") in outcomes
                  and (root / keep).read_bytes() == prior
                  and stat.S_IMODE((root / keep).lstat().st_mode) == ro_mode and lock_free(root))

    # 6f: the verification READ itself failing (an injected EIO) while a read-only source restores must
    # not wedge recovery either: the checkpoint runs before the prestate mode is installed, so the later
    # reconcile reopens the still-owner-writable file and lands the exact prestate bytes and mode.
    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        root = Path(temp).resolve()
        keep = "legacy/KEEP.md"
        prior = b"prior read-only bytes\n"
        (root / keep).parent.mkdir(parents=True)
        (root / keep).write_bytes(prior)
        (root / keep).chmod(0o400)

        def compose_keep(ops):
            ops.archive_occupying(keep, plan_digest(prior))

        real_verify = _journal._verify_staged_digest

        def failing_inventory(op, payload):
            if op["path"] == inventory_rel(rid):
                raise _journal.JournalError("injected failure at the inventory publication")
            return real_verify(op, payload)

        real_read_back = _journal._read_back_verify
        eio = [True]

        def eio_read_back(fd, expected_sha, path, what):
            if eio and what == "restored":
                eio.clear()
                raise OSError(5, "injected fault at the verification read")
            return real_read_back(fd, expected_sha, path, what)

        with mock.patch.object(_journal, "_verify_staged_digest", failing_inventory), \
                mock.patch.object(_journal, "_read_back_verify", eio_read_back):
            faulted = refusal(run_adopt_transaction, root, rid, compose_keep)
        check("verify-read-fault-fails-closed",
              faulted is not None and "rolled back" not in faulted and "reconcile" in faulted
              and txn_state(root, rid) == "open" and not lock_free(root))
        _journal.release_lock(_journal_root(root))   # the crashed owner, in this in-process simulation
        outcomes, _why = attempt(reconcile, root)
        check("verify-read-fault-reconciles-to-exact-prestate",
              outcomes is not None and (rid, "rolled-back") in outcomes
              and (root / keep).read_bytes() == prior
              and stat.S_IMODE((root / keep).lstat().st_mode) == 0o400 and lock_free(root))

    # 6g: the restore PRIMITIVE stays restartable at prestate modes the shell cannot even compose (its
    # bounded contained read cannot read a 0000 source, but the SHARED engine must finish any restore its
    # journal records): a faulted attempt fails closed AND reverts the temporary owner-write grant, and
    # the retry lands the exact prestate bytes and mode, on both the in-place (live file present) and the
    # recreate (live file absent) paths.
    for ro_mode in (0o400, 0o000):
        with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
            root = Path(temp).resolve()
            prior = b"the recorded preimage bytes\n"
            (root / "legacy").mkdir()
            live = root / "legacy/LOCKED.md"
            live.write_bytes(b"?" * len(prior))          # a faulted earlier restore's debris
            live.chmod(ro_mode)
            root_fd = store._open_dir_nofollow(root)
            try:
                _journal.ensure_journal_dirs(root_fd, JOURNAL_REL)
                txn_dir = _journal_root(root) / rid
                (txn_dir / "preimages").mkdir(parents=True)
                (txn_dir / "preimages/0").write_bytes(prior)
                op = dict(op="remove", path="legacy/LOCKED.md",
                          prestate=dict(kind="file", mode=ro_mode, size=len(prior), payload="0",
                                        sha256=_sha256(prior)))
                jr_fd = _journal.open_journal_root_fd(root_fd, JOURNAL_REL)
                try:
                    def try_restore():
                        try:
                            _journal._restore_preimage(jr_fd, txn_dir, root_fd, op)
                            return None
                        except _journal.JournalError as exc:
                            return str(exc)

                    real_write = _journal._write_all
                    armed = [True]

                    def faulty_write(fd, data):
                        if armed:
                            armed.clear()
                            data = b"X" * len(data)
                        return real_write(fd, data)

                    tag = "restore-primitive-mode-{:04o}".format(ro_mode)
                    with mock.patch.object(_journal, "_write_all", faulty_write):
                        faulted = try_restore()
                    check(tag + "-fault-fails-closed-and-reverts-the-grant",
                          faulted is not None and stat.S_IMODE(live.lstat().st_mode) == ro_mode)
                    retried = try_restore()              # the fault is gone: the restore must finish
                    got_mode = stat.S_IMODE(live.lstat().st_mode)
                    live.chmod(0o600)                    # the fixture's own grant, for the byte assert
                    check(tag + "-retry-restores-exact-prestate",
                          retried is None and got_mode == ro_mode and live.read_bytes() == prior)
                    live.unlink()                        # the RECREATE path: absent live file
                    armed.append(True)
                    with mock.patch.object(_journal, "_write_all", faulty_write):
                        refaulted = try_restore()
                    reretried = try_restore()            # the fault is gone: the restore must finish
                    got_mode = stat.S_IMODE(live.lstat().st_mode)
                    live.chmod(0o600)                    # the fixture's own grant, for the byte assert
                    check(tag + "-recreate-fault-then-retry-exact",
                          refaulted is not None and reretried is None and got_mode == ro_mode
                          and live.read_bytes() == prior)
                finally:
                    _journal._close_fd_quietly(jr_fd)
            finally:
                os.close(root_fd)

    # 6h: the apply-side checkpoint guards a PAIRED WRITE's own destination too (spec 14.2): a same-length
    # fault in the write's kernel-visible bytes (the staged bytes verify) raises AT the checkpoint and
    # HALTS the engine's op sequence, so the op after the faulted write never runs. This is the engine
    # seam the shell's write-as-removal pairing relies on; a checkpoint skipped on the write branch would
    # let the sequence continue and go red here.
    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        root = Path(temp).resolve()
        old_doc, witness = b"old doc bytes\n", b"witness\n"
        new_doc = b"NEW DOC BYTES\n"
        (root / "doc.md").write_bytes(old_doc)
        (root / "doc.md").chmod(0o644)
        (root / "witness.md").write_bytes(witness)
        (root / "witness.md").chmod(0o644)
        wop = dict(op="write", path="doc.md",
                   prestate=dict(kind="file", mode=0o644, size=len(old_doc), payload="0",
                                 sha256=_sha256(old_doc)),
                   poststate=dict(kind="file", mode=0o644))
        wop["poststate"]["content-sha256"] = _sha256(new_doc)
        rop = dict(op="remove", path="witness.md", poststate=dict(kind="absent"),
                   prestate=dict(kind="file", mode=0o644, size=len(witness), payload="1",
                                 sha256=_sha256(witness)))
        real_write = _journal._write_all
        armed = [True]

        def faulty_write(fd, data):
            if armed:
                armed.clear()
                data = b"X" * len(data)
            return real_write(fd, data)

        root_fd = store._open_dir_nofollow(root)
        try:
            with mock.patch.object(_journal, "_write_all", faulty_write):
                try:
                    _journal.apply_ops(root_fd, [wop, rop], lambda op: new_doc)
                    halted = None
                except _journal.JournalError as exc:
                    halted = str(exc)
        finally:
            os.close(root_fd)
        check("paired-write-destination-fault-fails-closed",
              halted is not None and "recorded digest" in halted)
        check("paired-write-destination-fault-halts-the-sequence",
              (root / "witness.md").exists() and (root / "witness.md").read_bytes() == witness)

    # 6i: the rollback-side checkpoint on the IN-PLACE restore path (a write op's undo, the live file
    # still present): a same-length fault in the restoring write REFUSES to report a rollback (the journal
    # stays open, fail-closed into recovery), and the recovery then lands the exact prestate; a reversal
    # that accepted the faulty in-place restore and published its terminal frame would go red here.
    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        root = Path(temp).resolve()
        old_doc, new_doc = b"old doc bytes\n", b"NEW DOC BYTES\n"
        (root / "doc.md").write_bytes(old_doc)
        (root / "doc.md").chmod(0o644)
        wop = dict(op="write", path="doc.md", poststate=dict(kind="file", mode=0o644))
        wop["poststate"]["content-sha256"] = _sha256(new_doc)
        never = dict(op="create", path="never.md", poststate=dict(kind="file", mode=0o644))
        never["poststate"]["content-sha256"] = _sha256(b"never staged\n")
        staged = dict()
        staged["doc.md"] = new_doc                       # never.md unstaged: raises INSIDE apply

        def reader(op):
            data = staged.get(op["path"])
            if not isinstance(data, bytes):
                raise _journal.JournalError("no staged bytes for {!r} (fail-closed)".format(op["path"]))
            return data

        armed = []
        real_restore = _journal._restore_preimage

        def arming_restore(jr_fd, txn_dir, rfd, op, op_index=0):
            if op.get("op") == "write":
                armed.append(True)
            return real_restore(jr_fd, txn_dir, rfd, op, op_index)

        real_write = _journal._write_all

        def faulty_write(fd, data):
            if armed:
                armed.clear()
                data = b"X" * len(data)
            return real_write(fd, data)

        root_fd = store._open_dir_nofollow(root)
        jr_fd = None
        try:
            _journal.ensure_journal_dirs(root_fd, JOURNAL_REL)
            jr_fd = _journal.open_journal_root_fd(root_fd, JOURNAL_REL)
            header = dict(kind=KIND, run_id=rid, phase="base", operation=OPERATION)
            with mock.patch.object(_journal, "_restore_preimage", arming_restore), \
                    mock.patch.object(_journal, "_write_all", faulty_write):
                try:
                    _journal.run_transaction(root_fd, jr_fd, _journal_root(root), rid, header,
                                             [wop, never], reader, SESSION_ID)
                    outcome = "complete"
                except _journal.JournalError:
                    outcome = _journal.classify_state(jr_fd, _journal_root(root) / rid)
            check("restore-in-place-fault-never-reports-rollback", outcome == "open")
            check("restore-in-place-fault-then-recover-exact",
                  _journal.recover(jr_fd, _journal_root(root) / rid, root_fd) == "rolled-back"
                  and (root / "doc.md").read_bytes() == old_doc
                  and stat.S_IMODE((root / "doc.md").lstat().st_mode) == 0o644)
        finally:
            if jr_fd is not None:
                _journal._close_fd_quietly(jr_fd)
            os.close(root_fd)

    # 6j: the temporary owner-write grant itself (fix #3). The grant is HARD-LINK GATED: a multiply-
    # linked unwritable target is refused BEFORE any chmod (codex round-3: a second name, here OUTSIDE
    # the product root, must stay exactly as found, under an injected fstat fault, under an uninjected
    # retry, and under an interruption raced into the reopen). On a singly-linked target the grant is
    # reverted on each exercised failed exit its handler observes (an fstat fault, an identity
    # refusal, an interruption at the reopen; the journal's own comments disclose the exits the
    # handler never sees), a raced-in symlink at the grant chmod fails closed as a JournalError (never a raw
    # ValueError), the grant requires OWNERSHIP (a non-owned unwritable file fails closed with a named
    # JournalError, simulated by an EPERM chmod), and the recreate path's verify-before-mode ordering
    # is pinned (at the restore checkpoint the recreated file still holds the temporary 0600, the
    # exact prestate mode installed only after). Round-4 additions: an interruption delivered
    # IMMEDIATELY after the grant chmod returns (codex's settrace SIGINT, which used to escape
    # between the chmod and the revert-protected reopen) now reaches the revert, and a fault AT
    # the post-checkpoint prestate-mode install, BEFORE its fchmod takes effect, DELIBERATELY
    # leaves the grant behind a NAMED JournalError, the next reconcile finishing directly through
    # the still-writable file. Round-5 addition: a fault at the durability fsync AFTER that fchmod
    # took effect leaves the exact prestate mode with NO grant, behind a JournalError naming that
    # state, the uninjected retry then landing the exact prestate bytes and mode.
    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        product = Path(temp).resolve() / "product"       # the product root under restore ...
        product.mkdir()
        outside = Path(temp).resolve() / "outside-link.md"   # ... and a second name OUTSIDE it
        prior = b"the recorded preimage bytes\n"
        (product / "legacy").mkdir()
        live = product / "legacy/LOCKED.md"

        def mode_of(p):
            return stat.S_IMODE(p.lstat().st_mode)

        def reset(linked=False):
            for f in (live, outside):
                if f.exists():
                    f.unlink()
            live.write_bytes(b"?" * len(prior))          # a faulted earlier restore's debris
            if linked:
                os.link(live, outside)
            live.chmod(0o400)

        root_fd = store._open_dir_nofollow(product)
        try:
            _journal.ensure_journal_dirs(root_fd, JOURNAL_REL)
            txn_dir = _journal_root(product) / rid
            (txn_dir / "preimages").mkdir(parents=True)
            (txn_dir / "preimages/0").write_bytes(prior)
            op = dict(op="remove", path="legacy/LOCKED.md",
                      prestate=dict(kind="file", mode=0o400, size=len(prior), payload="0",
                                    sha256=_sha256(prior)))
            jr_fd = _journal.open_journal_root_fd(root_fd, JOURNAL_REL)
            try:
                def try_restore():
                    try:
                        _journal._restore_preimage(jr_fd, txn_dir, root_fd, op)
                        return None
                    except _journal.JournalError as exc:
                        return str(exc)

                # the multiply-linked target is refused BEFORE any chmod: the injected identity-fstat
                # fault (codex's reproduction) is never even reached, and BOTH names keep 0400 exactly
                reset(linked=True)

                def eio_fstat(fd, _real=os.fstat, _ino=live.lstat().st_ino):
                    fst = _real(fd)
                    if fst.st_ino == _ino:
                        raise OSError(5, "injected fault at the identity fstat")
                    return fst

                with mock.patch.object(os, "fstat", eio_fstat):
                    refused = try_restore()
                check("grant-hardlink-refused-before-any-chmod",
                      refused is not None and "hard links" in refused
                      and mode_of(live) == 0o400 and mode_of(outside) == 0o400
                      and outside.lstat().st_nlink == 2)
                retried = try_restore()                  # uninjected retry: still refused, still 0400
                check("grant-hardlink-uninjected-retry-still-as-found",
                      retried is not None and "hard links" in retried
                      and mode_of(live) == 0o400 and mode_of(outside) == 0o400)

                # an interruption raced into the reopen window: for a multiply-linked inode there IS
                # no grant window (no chmod ever runs), so the outside name stays exactly as found
                reset(linked=True)
                opens = []

                def interrupting_open(p, flags, *a, _real=os.open, **kw):
                    if p == "LOCKED.md" and kw.get("dir_fd") is not None:
                        opens.append(p)
                        if len(opens) == 2:
                            raise KeyboardInterrupt()
                    return _real(p, flags, *a, **kw)

                with mock.patch.object(os, "open", interrupting_open):
                    try:
                        interrupted = try_restore()
                    except BaseException as exc:
                        interrupted = "escaped: " + type(exc).__name__
                check("grant-hardlink-interrupt-leaves-modes-as-found",
                      interrupted is not None and "hard links" in interrupted and len(opens) == 1
                      and mode_of(live) == 0o400 and mode_of(outside) == 0o400)

                # fault-and-retry on the singly-linked target: an fstat fault AFTER the grant reverts
                # the grant on that exit (fail-closed), and the uninjected retry lands the exact
                # prestate bytes and mode
                reset()
                armed = [True]

                def eio_fstat_once(fd, _real=os.fstat, _ino=live.lstat().st_ino):
                    fst = _real(fd)
                    if armed and fst.st_ino == _ino:
                        armed.clear()
                        raise OSError(5, "injected fault at the identity fstat")
                    return fst

                with mock.patch.object(os, "fstat", eio_fstat_once):
                    faulted = try_restore()
                check("grant-fstat-fault-fails-closed-and-reverts-the-grant",
                      faulted is not None and mode_of(live) == 0o400)
                refinished = try_restore()               # the fault is gone: the restore must finish
                check("grant-fstat-fault-retry-restores-exact-prestate",
                      refinished is None and mode_of(live) == 0o400 and live.read_bytes() == prior)

                # an identity refusal (a swapped inode) also reverts the grant before failing closed
                reset()

                def swapped_fstat(fd, _real=os.fstat, _ino=live.lstat().st_ino):
                    fst = _real(fd)
                    if fst.st_ino == _ino:
                        fake = list(fst)
                        fake[1] = fst.st_ino + 1         # a different inode: the identity check refuses
                        return os.stat_result(fake)
                    return fst

                with mock.patch.object(os, "fstat", swapped_fstat):
                    swapped = try_restore()
                check("grant-identity-refusal-reverts-the-grant",
                      swapped is not None and "swapped" in swapped and mode_of(live) == 0o400)

                # an interruption AT the reopen of a singly-linked target reverts the grant too
                reset()
                opens2 = []

                def interrupting_open2(p, flags, *a, _real=os.open, **kw):
                    if p == "LOCKED.md" and kw.get("dir_fd") is not None:
                        opens2.append(p)
                        if len(opens2) == 2:
                            raise KeyboardInterrupt()
                    return _real(p, flags, *a, **kw)

                escaped = []
                with mock.patch.object(os, "open", interrupting_open2):
                    try:
                        try_restore()
                    except KeyboardInterrupt:
                        escaped.append(True)
                check("grant-interrupt-at-reopen-reverts-the-grant",
                      escaped == [True] and mode_of(live) == 0o400)

                # a symlink raced in at the grant chmod is a JournalError, never a raw ValueError
                reset()

                def valueerror_chmod(p, m, *a, _real=os.chmod, **kw):
                    if p == "LOCKED.md" and kw.get("dir_fd") is not None and m == 0o600:
                        raise ValueError("chmod: cannot use dir_fd and follow_symlinks together")
                    return _real(p, m, *a, **kw)

                with mock.patch.object(os, "chmod", valueerror_chmod):
                    try:
                        raced = try_restore()
                    except ValueError:
                        raced = None                     # a raw ValueError escaped: red
                check("grant-raced-symlink-valueerror-is-journalerror",
                      raced is not None and "symlink" in raced and mode_of(live) == 0o400)

                # the grant requires OWNERSHIP: a non-owned unwritable file (the kernel refuses the
                # chmod with EPERM, simulated here) fails closed with the NAMED JournalError
                reset()

                def eperm_chmod(p, m, *a, _real=os.chmod, **kw):
                    if p == "LOCKED.md" and kw.get("dir_fd") is not None and m == 0o600:
                        raise PermissionError(1, "Operation not permitted")
                    return _real(p, m, *a, **kw)

                with mock.patch.object(os, "chmod", eperm_chmod):
                    denied = try_restore()
                check("grant-requires-ownership-fails-closed-named",
                      denied is not None and "ownership" in denied and mode_of(live) == 0o400)

                # codex round-4: an interruption delivered IMMEDIATELY after the grant chmod
                # returns (settrace fires SIGINT on the first traced line in _restore_preimage
                # after the real chmod applied the grant) used to escape between the chmod and
                # the revert-protected reopen with the 0600 grant still installed; the revert
                # protection now spans the grant chmod itself, so the KeyboardInterrupt escapes
                # with the mode already reverted to exactly as found
                reset()
                sigint_fired = []
                grant_applied = []

                def flagging_chmod(p, m, *a, _real=os.chmod, **kw):
                    result = _real(p, m, *a, **kw)
                    if p == "LOCKED.md" and kw.get("dir_fd") is not None and m == 0o600:
                        grant_applied.append(True)
                    return result

                restore_code = _journal._restore_preimage.__code__

                def sigint_tracer(frame, event, arg):
                    if frame.f_code is not restore_code:
                        return None
                    if event == "line" and grant_applied and not sigint_fired:
                        sigint_fired.append(True)
                        os.kill(os.getpid(), signal.SIGINT)
                    return sigint_tracer

                escaped_interrupt = []
                prior_trace = sys.gettrace()
                # The REAL SIGINT below must surface as a KeyboardInterrupt regardless of
                # the inherited disposition (a harness may launch this process with SIGINT
                # ignored, and Python then installs no default_int_handler): pin the default
                # handler for exactly this window and restore the inherited one after.
                prior_handler = signal.signal(signal.SIGINT, signal.default_int_handler)
                try:
                    with mock.patch.object(os, "chmod", flagging_chmod):
                        sys.settrace(sigint_tracer)
                        try:
                            try_restore()
                        except KeyboardInterrupt:
                            escaped_interrupt.append(True)
                finally:
                    sys.settrace(prior_trace)
                    signal.signal(signal.SIGINT, prior_handler)
                check("grant-interrupt-after-grant-chmod-reverts-the-grant",
                      escaped_interrupt == [True] and sigint_fired == [True]
                      and mode_of(live) == 0o400)
                check("grant-interrupt-after-grant-chmod-retry-restores-exact",
                      try_restore() is None and mode_of(live) == 0o400
                      and live.read_bytes() == prior)

                # claude round-4: a fault AT the post-checkpoint prestate-mode install is not an
                # anonymous escape silently retaining the grant: the checkpoint has already
                # verified the restored bytes, so this exit DELIBERATELY leaves the owner-write
                # grant (the recreate path's restartability posture) and says so in a NAMED
                # JournalError; the next reconcile then reopens the still-writable file and
                # finishes installing the exact prestate mode without another grant cycle
                reset()
                fchmod_armed = [True]

                def eio_prestate_fchmod(fd_, m, _real=os.fchmod):
                    if fchmod_armed and m == 0o400:
                        fchmod_armed.clear()
                        raise OSError(5, "injected fault at the prestate-mode fchmod")
                    return _real(fd_, m)

                with mock.patch.object(os, "fchmod", eio_prestate_fchmod):
                    retained = try_restore()
                check("grant-post-checkpoint-mode-fault-names-the-retained-grant",
                      retained is not None and "deliberately left" in retained
                      and mode_of(live) == 0o600 and live.read_bytes() == prior)
                check("grant-post-checkpoint-mode-fault-reconcile-finishes-directly",
                      try_restore() is None and mode_of(live) == 0o400
                      and live.read_bytes() == prior)

                # codex round-5 (= claude F1): the OTHER post-checkpoint state. A fault at the
                # durability fsync AFTER the prestate fchmod took effect leaves the live mode
                # ALREADY the exact prestate mode -- NO grant remains -- and the NAMED
                # JournalError must say so instead of claiming a retained grant; the uninjected
                # retry re-runs the whole grant cycle (the prestate mode is read-only) and lands
                # the exact prestate bytes and mode
                reset()
                fsync_fault_armed = []

                def arming_prestate_fchmod(fd_, m, _real=os.fchmod):
                    result = _real(fd_, m)
                    if m == 0o400:
                        fsync_fault_armed.append(True)
                    return result

                def eio_post_mode_fsync(fd_, _real=os.fsync):
                    if fsync_fault_armed:
                        fsync_fault_armed.clear()
                        raise OSError(5, "injected fault at the post-mode durability fsync")
                    return _real(fd_)

                with mock.patch.object(os, "fchmod", arming_prestate_fchmod), \
                        mock.patch.object(os, "fsync", eio_post_mode_fsync):
                    unsynced = try_restore()
                check("grant-post-mode-fsync-fault-names-no-grant-remains",
                      unsynced is not None and "no grant remains" in unsynced
                      and "deliberately left" not in unsynced
                      and mode_of(live) == 0o400 and live.read_bytes() == prior)
                check("grant-post-mode-fsync-fault-retry-restores-exact",
                      try_restore() is None and mode_of(live) == 0o400
                      and live.read_bytes() == prior)

                # the RECREATE path's verify-before-mode ordering is pinned: AT the restore checkpoint
                # the recreated file still holds the temporary owner-rw 0600 (so a faulted checkpoint
                # leaves a reopenable file), and the exact 0400 prestate mode is installed only after
                live.unlink()                            # absent live file: the recreate path
                interim = []
                real_read_back = _journal._read_back_verify

                def observing_read_back(fd, expected_sha, path_, what):
                    if what == "restored":
                        interim.append(stat.S_IMODE(os.fstat(fd).st_mode))
                    return real_read_back(fd, expected_sha, path_, what)

                with mock.patch.object(_journal, "_read_back_verify", observing_read_back):
                    recreated = try_restore()
                check("recreate-verifies-before-prestate-mode-installed",
                      recreated is None and interim == [0o600] and mode_of(live) == 0o400
                      and live.read_bytes() == prior)
            finally:
                _journal._close_fd_quietly(jr_fd)
        finally:
            os.close(root_fd)

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

    # 7 order: the EXPLICIT recovery's rollback order is pinned exactly as the in-run reversal's (6d): at
    # the moment recover()'s create-undo discards an archive copy, its source has ALREADY been restored
    # byte-identical, so an explicit reconcile that unlinked the copies first would go red here.
    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        root, files = fixture(temp)
        before = snapshot(root)
        man = ".working/toml/manifest.toml"
        gone = interrupt_when(lambda: (root / archive_rel(rid, man)).exists() and not (root / man).exists())
        with mock.patch.object(_journal, "_kill_point", gone):
            try:
                run_adopt_transaction(root, rid, compose_full(files))
            except (_Interrupt, AdoptApplyError):
                pass
        check("reconcile-order-fixture-left-open", txn_state(root, rid) == "open" and lock_free(root))
        arch = _archive_root(rid) + "/"
        misordered = []
        real_restore = _journal._restore_preimage

        def watching_restore(jr_fd, txn_dir, rfd, op, op_index=0):
            if op.get("op") == "create" and str(op.get("path", "")).startswith(arch):
                source = op["path"][len(arch):]
                live = root / source
                if source in files and not (live.exists() and live.read_bytes() == files[source]):
                    misordered.append(op["path"])
            return real_restore(jr_fd, txn_dir, rfd, op, op_index)

        with mock.patch.object(_journal, "_restore_preimage", watching_restore):
            outcomes, _why = attempt(reconcile, root)
        check("reconcile-restores-sources-before-archive-discard",
              outcomes is not None and (rid, "rolled-back") in outcomes and not misordered
              and snapshot(root) == before and lock_free(root))

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

    # 7b: the reconcile-first ORDER: with BOTH an open transaction and a resolvable store present, the
    # refusal is the journal's (the operator is pointed at reconcile()), never the lease refusal the
    # posture gate would raise, because the journal is inspected FIRST (the module-docstring discipline).
    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        import _opf_init
        root, files = fixture(temp)
        man = ".working/toml/manifest.toml"
        mid = interrupt_when(lambda: (root / archive_rel(rid, man)).exists())
        with mock.patch.object(_journal, "_kill_point", mid):
            try:
                run_adopt_transaction(root, rid, compose_full(files))
            except (_Interrupt, AdoptApplyError):
                pass
        (root / man).write_text(_opf_init.build_manifest(), encoding="utf-8")
        ordered = refusal(run_adopt_transaction, root, other_run, compose_full(files))
        check("journal-checked-before-store-posture",
              store.resolve_store(root).status == store.RESOLVED and txn_state(root, rid) == "open"
              and ordered is not None and "reconcile" in ordered and "lease" not in ordered)

    # 7c: a confirmed-dead owner's stale lock is broken only through _journal.reconcile_and_claim_stale,
    # and reconcile() reports the TRUE outcome of the recovery that break performed, never 'terminal' for
    # its own work.
    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        root, files = fixture(temp)
        before = snapshot(root)
        man = ".working/toml/manifest.toml"
        mid = interrupt_when(lambda: (root / archive_rel(rid, man)).exists() and (root / man).exists())
        with mock.patch.object(_journal, "_kill_point", mid):
            try:
                run_adopt_transaction(root, rid, compose_full(files))
            except (_Interrupt, AdoptApplyError):
                pass
        dead = dead_pid()
        owner_row = dict(uid=os.getuid(), pid=dead, session="opf-adopt-selftest-dead",
                         utc="2026-09-17T12:00:00Z")
        owner_row["pid-start"] = ""
        (_journal_root(root) / "lock").write_text(json.dumps(owner_row), encoding="utf-8")
        outcomes = reconcile(root) if dead is not None else []
        check("stale-lock-reconcile-reports-true-outcomes",
              dead is not None and txn_state(root, rid) == "rolled-back"
              and (rid, "rolled-back") in outcomes and snapshot(root) == before and lock_free(root))

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
        check("held-lock-refuses-transaction", "it is never seized" in
              (refusal(run_adopt_transaction, root, rid, compose_full(files)) or ""))
        check("held-lock-refuses-reconcile",
              "possibly-live owner" in (refusal(reconcile, root) or ""))
        _journal.release_lock(journal_root)

    # 8b (round 3): reconcile reads the journal lock beneath the HELD contained journal-root fd (the
    # round-2 journal_state posture), never by re-resolving the journal PATH: the journal path is
    # swapped for a symlink to an empty decoy right after the contained open (the deterministic
    # stand-in for a concurrent writer), and the product's own held lock must STILL refuse -- a
    # path-based owner read saw the decoy's absent lock and reconciled straight through to a clean
    # empty outcome list here.
    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        root, files = fixture(temp)
        journal_root = _journal_root(root)
        root_fd = store._open_dir_nofollow(root)
        try:
            _journal.ensure_journal_dirs(root_fd, JOURNAL_REL)
        finally:
            os.close(root_fd)
        _journal.acquire_lock(journal_root, "opf-adopt-selftest-live-peer")
        decoy = Path(temp).resolve() / "decoy"
        decoy.mkdir()
        _real_open_jr = _journal.open_journal_root_fd
        _jr_swap_fired = []

        def _racing_open_jr(rfd, rel):
            fd = _real_open_jr(rfd, rel)
            _jr_swap_fired.append(rel)   # the injection provably ran (round 4)
            os.rename(journal_root, str(journal_root) + ".moved")
            os.symlink(decoy, journal_root)
            return fd

        with mock.patch.object(_journal, "open_journal_root_fd", _racing_open_jr):
            _res, why = attempt(reconcile, root)
        check("reconcile-lock-read-beneath-held-jr-fd",
              _jr_swap_fired != [] and why is not None and "possibly-live owner" in why)

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
            check("observe-symlink-refused",
                  "not a regular file" in (refusal(observe_live, root_fd, "link.md") or ""))
            check("observe-traversal-refused",
                  "not a contained relative file path" in (refusal(observe_live, root_fd, "../escape") or ""))
        finally:
            os.close(root_fd)

    # round 7 MINOR 1: _verify_bundle_at computes the retained-chain KEY before duplicating the held
    # home descriptor, so a bundle path _check_rel refuses can never strand the dup outside dir_fds
    # (where the verification cleanup cannot close it). The malformed-bundle raise still propagates
    # and no duplicated descriptor survives it.
    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        root = Path(temp).resolve()
        (root / "home").mkdir()
        root_fd = store._open_dir_nofollow(root)
        home_fd = os.open("home", os.O_RDONLY | os.O_DIRECTORY, dir_fd=root_fd)
        _m1_seen = dict()
        _m1_real_dup = os.dup

        def _m1_dup(fd):
            nfd = _m1_real_dup(fd)
            _m1_seen["dup"] = nfd
            return nfd

        os.dup = _m1_dup
        try:
            try:
                _verify_bundle_at(root_fd, "x", "a/../b", home_fd=home_fd)
                _m1_out = "returned"
            except _journal.JournalError:
                _m1_out = "raised"
        finally:
            os.dup = _m1_real_dup
        check("r7-verify-bundle-malformed-rel-raises", _m1_out == "raised")
        _m1_leaked = False
        if "dup" in _m1_seen:
            try:
                os.fstat(_m1_seen["dup"])
                _m1_leaked = True
            except OSError:
                pass
        check("r7-verify-bundle-home-dup-never-stranded", not _m1_leaked)
        if _m1_leaked:                    # a pre-fix run leaks it; close so the failing suite stays clean
            try:
                os.close(_m1_seen["dup"])
            except OSError:
                pass
        os.close(home_fd)
        os.close(root_fd)

    # round 7 MINOR 2: _journal_txn_dirs(hold=True) builds the returned entry path BEFORE opening the
    # transaction descriptor, so a path construction that raises (a malformed journal_root) can never
    # strand a just-opened txn descriptor outside `out`, where the except-cleanup cannot close it. The
    # construction error still propagates and no held txn descriptor survives it.
    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        root = Path(temp).resolve()
        (root / "txn-1").mkdir()
        jr_fd = os.open(str(root), os.O_RDONLY | os.O_DIRECTORY)
        _m2_opened = []
        _m2_real_open = os.open

        def _m2_open(*args, **kwargs):
            fd = _m2_real_open(*args, **kwargs)
            if kwargs.get("dir_fd") == jr_fd:
                _m2_opened.append(fd)
            return fd

        os.open = _m2_open
        try:
            try:
                _journal._journal_txn_dirs(jr_fd, None, strict=True, hold=True)
                _m2_out = "returned"
            except TypeError:
                _m2_out = "raised"
        finally:
            os.open = _m2_real_open
        check("r7-txn-dirs-held-path-error-raises", _m2_out == "raised")
        _m2_left = []
        for _fd in _m2_opened:
            try:
                os.fstat(_fd)
            except OSError:
                continue
            _m2_left.append(_fd)
        check("r7-txn-dirs-no-held-descriptor-survives-path-error", not _m2_left)
        for _fd in _m2_left:              # a pre-fix run leaks it; close so the failing suite stays clean
            try:
                os.close(_fd)
            except OSError:
                pass
        os.close(jr_fd)

    # 10: apply takes only a plan/v2 (spec 14.1): a v1-marked plan is never apply input. The v1 schema no
    # longer ships (the plan-v2 schema replaced it, U8), and apply refuses on the format marker before it
    # reads any other field, so the v1 input is the bare marker.
    v1 = apply_plan(dict(format="opf.adoption.plan/v1"))
    check("apply-v1-plan-refused", v1.status == CANNOT and any("never apply input" in f for f in v1.findings))
    # and the schema's own canonical v2 plan is refused fail-closed, attributed to the rule under test: v2
    # validation and the approval binding land in their later adoption slice.
    canon = apply_plan(schema.canonical_plan())
    check("apply-canonical-v2-plan-refused-until-validated",
          schema.canonical_plan().get("format") == PLAN_V2_FORMAT and canon.status == CANNOT
          and any("later adoption slice" in f for f in canon.findings))
    v2 = apply_plan(dict(format=PLAN_V2_FORMAT))
    check("apply-v2-marked-plan-refused",
          v2.status == CANNOT and any("later adoption slice" in f for f in v2.findings))
    nontable = apply_plan([])
    check("apply-non-table-refused",
          nontable.status == CANNOT and any("not a table" in f for f in nontable.findings))

    # 11: the three file ops (slice 2), staged as spec 14.1 and 14.2 order them. The apply stage (the run's
    # base transaction) creates a planned file, takes a non-occupying retire source's preimage while it
    # stays frozen, archives an occupying retire or move source preserve-first, and leaves a non-occupying
    # move source frozen; the retirement stage (RETIREMENT_PHASE, after the green check) removes the
    # frozen retire source, relocates both move sources, and touches nothing for the occupying retire.
    # Each round trip below goes red if its op still refuses.
    fx_files = {"legacy/RULES.md": b"old rules\n", ".working/TODO.md": b"hand-kept todo\n",
                "legacy/MOVE.md": b"move me\n", "docs/VIEW.md": b"old view\n"}
    fx_new, fx_new_bytes = "adopter/NEW.md", b"planned\n"
    fx_moved = store.moved_dest("legacy/MOVE.md")
    fx_view_dest = "adopter/view-old.md"
    fx_sources = [dict(path="legacy/RULES.md", disposition="retire", occupying=False,
                       preservation=archive_rel(rid, "legacy/RULES.md")),
                  dict(path=".working/TODO.md", disposition="retire", occupying=True,
                       preservation=archive_rel(rid, ".working/TODO.md")),
                  dict(path="legacy/MOVE.md", disposition="move", occupying=False, preservation=fx_moved),
                  dict(path="docs/VIEW.md", disposition="move", occupying=True,
                       preservation=archive_rel(rid, "docs/VIEW.md"))]
    for row in fx_sources:
        row["digest"] = plan_digest(fx_files[row["path"]])
    fx_create = dict(op="create-file", path=fx_new, content_digest=plan_digest(fx_new_bytes))
    fx_disposed = [dict(op="retire-file", path="legacy/RULES.md",
                        preimage_digest=plan_digest(fx_files["legacy/RULES.md"])),
                   dict(op="retire-file", path=".working/TODO.md",
                        preimage_digest=plan_digest(fx_files[".working/TODO.md"])),
                   dict(op="move-file", source="legacy/MOVE.md", destination=fx_moved,
                        source_digest=plan_digest(fx_files["legacy/MOVE.md"])),
                   dict(op="move-file", source="docs/VIEW.md", destination=fx_view_dest,
                        source_digest=plan_digest(fx_files["docs/VIEW.md"]))]
    check("file-ops-fixture-rows-valid",
          all(schema.validate_op(r).status == VALID for r in [fx_create] + fx_disposed))

    def file_fixture(temp):
        root = Path(temp).resolve()
        for rel, payload in fx_files.items():
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_bytes(payload)
        return root

    def apply_stage(root, rows=None, sources=None, content=None):
        return attempt(run_adopt_transaction, root, rid, compose_rows(
            [fx_create] + fx_disposed if rows is None else rows, fx_sources if sources is None else sources,
            {fx_new: fx_new_bytes} if content is None else content))

    def retirement_stage(root, rows=None, sources=None):
        return attempt(run_adopt_transaction, root, rid, compose_rows(
            fx_disposed if rows is None else rows, fx_sources if sources is None else sources),
            phase=RETIREMENT_PHASE)

    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        root = file_fixture(temp)
        txn, why = apply_stage(root)
        after = snapshot(root)
        check("file-ops-apply-stage-commits", txn == rid and why is None and txn_state(root, rid) == "complete"
              and lock_free(root))
        check("create-file-creates-planned-bytes", after.get(fx_new) == fx_new_bytes)
        check("retire-file-apply-preimage-taken-source-frozen",
              after.get(archive_rel(rid, "legacy/RULES.md")) == fx_files["legacy/RULES.md"]
              and after.get("legacy/RULES.md") == fx_files["legacy/RULES.md"])
        check("retire-file-apply-occupying-archived-then-removed",
              after.get(archive_rel(rid, ".working/TODO.md")) == fx_files[".working/TODO.md"]
              and ".working/TODO.md" not in after)
        check("move-file-apply-non-occupying-frozen-not-relocated",
              after.get("legacy/MOVE.md") == fx_files["legacy/MOVE.md"] and fx_moved not in after
              and archive_rel(rid, "legacy/MOVE.md") not in after)
        check("move-file-apply-occupying-archived-not-relocated",
              after.get(archive_rel(rid, "docs/VIEW.md")) == fx_files["docs/VIEW.md"]
              and "docs/VIEW.md" not in after and fx_view_dest not in after)
        check("file-ops-apply-bundle-verifies", verify_bundle(root, rid).status == VALID)
        staged_tree = tree_state(root)
        late_create = refusal(run_adopt_transaction, root, rid, compose_rows(
            [fx_create], (), {fx_new: fx_new_bytes}), phase=RETIREMENT_PHASE)
        check("create-file-retirement-stage-refused-untouched-save-journal-dirs",
              late_create is not None and "apply stage" in late_create
              and untouched_save_journal_dirs(staged_tree, root))
        rtxn, why = retirement_stage(root)
        done = snapshot(root)
        check("file-ops-retirement-stage-commits",
              rtxn == rid + "." + RETIREMENT_PHASE and why is None
              and txn_state(root, rtxn) == "complete" and lock_free(root))
        check("retire-file-retirement-removes-frozen-source-preimage-retained",
              "legacy/RULES.md" not in done
              and done.get(archive_rel(rid, "legacy/RULES.md")) == fx_files["legacy/RULES.md"])
        check("retire-file-retirement-occupying-touches-nothing",
              ".working/TODO.md" not in done
              and done.get(archive_rel(rid, ".working/TODO.md")) == fx_files[".working/TODO.md"])
        check("move-file-retirement-relocates-frozen-source",
              done.get(fx_moved) == fx_files["legacy/MOVE.md"] and "legacy/MOVE.md" not in done)
        check("move-file-retirement-copies-archive-and-retains-it",
              done.get(fx_view_dest) == fx_files["docs/VIEW.md"]
              and done.get(archive_rel(rid, "docs/VIEW.md")) == fx_files["docs/VIEW.md"])
        rinv = tomllib.loads(done.get(inventory_rel(rid, RETIREMENT_PHASE), b"file = []").decode("utf-8"))
        check("file-ops-retirement-inventory-claims-move-root",
              [r["path"] for r in rinv["file"]] == [fx_moved] and verify_bundle(root, rid).status == VALID)

    # 11b: the per-op preimage flips. One byte of one bound source changes between plan and apply: the
    # op refuses as drifted (cannot-evaluate), and the tree is untouched, no journal transaction opened.
    for label, source in (("retire-frozen", "legacy/RULES.md"), ("retire-occupying", ".working/TODO.md"),
                          ("move-frozen", "legacy/MOVE.md"), ("move-occupying", "docs/VIEW.md")):
        with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
            root = file_fixture(temp)
            flipped = bytearray(fx_files[source])
            flipped[0] ^= 0x01
            (root / source).write_bytes(bytes(flipped))
            before = tree_state(root)
            _txn, why = apply_stage(root)
            check("preimage-flip-{}-apply-refused-untouched-save-journal-dirs".format(label),
                  why is not None and "drifted" in why and untouched_save_journal_dirs(before, root))
    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        root = file_fixture(temp)
        apply_stage(root)
        for source in ("legacy/RULES.md", "legacy/MOVE.md"):
            flipped = bytearray(fx_files[source])
            flipped[-1] ^= 0x01
            (root / source).write_bytes(bytes(flipped))
            before = tree_state(root)
            _txn, why = retirement_stage(root)
            check("preimage-flip-{}-retirement-refused-untouched-save-journal-dirs".format(source),
                  why is not None and "drifted" in why and untouched_save_journal_dirs(before, root))
            (root / source).write_bytes(fx_files[source])
        # the committed preservation re-verifies at retirement: a tampered archive copy refuses the whole
        # stage before anything is removed or relocated.
        copy_path = root / archive_rel(rid, "legacy/RULES.md")
        archived = copy_path.is_file()      # False when the apply stage itself refused: a recorded red
        if archived:
            copy_path.write_bytes(b"old rulez\n")
        before = tree_state(root)
        _txn, why = retirement_stage(root)
        check("retirement-tampered-preimage-refused-untouched-save-journal-dirs",
              archived and why is not None and "archive copy" in why
              and untouched_save_journal_dirs(before, root))
        if archived:
            copy_path.write_bytes(fx_files["legacy/RULES.md"])

    # 11c: preservation precedes removal. A retirement stage whose base never archived the source (the
    # base created only the planned file) refuses: nothing is retired that the committed base did not
    # preserve with the plan digest.
    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        root = file_fixture(temp)
        apply_stage(root, rows=[fx_create])
        before = tree_state(root)
        _txn, why = retirement_stage(root, rows=fx_disposed[:1])
        check("retirement-without-committed-preimage-refused-untouched-save-journal-dirs",
              why is not None and "committed base" in why and untouched_save_journal_dirs(before, root))

    # 11d: create-file's own preconditions and the plan bindings the handlers require, each refused with
    # the tree untouched: an occupied path, bytes that do not match content_digest, a path in the store
    # control area (this run's own archive, which check_apply_ops alone would admit), a source no plan row
    # binds or binds at another digest or twice, a move whose destination is already occupied, and a move
    # into this run's own archive (which protected_destination alone exempts).
    forged = archive_rel(rid, "legacy/FORGED.md")
    forged_row = dict(op="create-file", path=forged, content_digest=plan_digest(fx_new_bytes))
    forged_ops = [c(forged, fx_new_bytes)]
    check("create-file-control-area-admitted-by-shell-alone",
          findings_of(*sealed(forged_ops, {forged: fx_new_bytes})) == [])
    other_sources = [dict(r, digest=plan_digest(b"elsewhere\n")) if r["path"] == "legacy/RULES.md" else r
                     for r in fx_sources]
    twice = fx_sources + [dict(fx_sources[0])]

    def moved_to(destination):
        """fx_sources with the frozen move source's preservation at `destination` (the planner's pairing)."""
        return [dict(r, preservation=destination) if r["path"] == "legacy/MOVE.md" else r for r in fx_sources]

    def move_row(destination):
        return dict(fx_disposed[2], destination=destination)

    # two frozen move sources sent to ONE destination, and a retire source inside the Move root (a store
    # control-area path no retirement stage may remove).
    pair_dest = "adopter/D.md"
    pair_rows = [move_row(pair_dest), dict(op="move-file", source="legacy/RULES.md", destination=pair_dest,
                                           source_digest=plan_digest(fx_files["legacy/RULES.md"]))]
    pair_sources = [dict(r, preservation=pair_dest) for r in fx_sources if r["path"] == "legacy/MOVE.md"] + [
        dict(path="legacy/RULES.md", disposition="move", occupying=False, preservation=pair_dest,
             digest=plan_digest(fx_files["legacy/RULES.md"]))]
    in_root, in_root_bytes = store.moved_dest("old/x.md"), b"moved earlier\n"
    in_root_row = dict(op="retire-file", path=in_root, preimage_digest=plan_digest(in_root_bytes))
    in_root_sources = [dict(path=in_root, disposition="retire", occupying=False,
                            digest=plan_digest(in_root_bytes), preservation=archive_rel(rid, in_root))]
    for label, kwargs, reason in (
            ("create-occupied", dict(rows=[fx_create]), "occupied"),
            ("create-digest-mismatch", dict(rows=[fx_create], content={fx_new: b"unplanned\n"}),
             "content_digest"),
            ("create-control-area", dict(rows=[forged_row], content={forged: fx_new_bytes}), "control area"),
            ("retire-unbound", dict(rows=fx_disposed[:1], sources=fx_sources[1:]), "plan source row"),
            ("retire-other-digest", dict(rows=fx_disposed[:1], sources=other_sources), "plan source row"),
            ("retire-ambiguous", dict(rows=fx_disposed[:1], sources=twice), "plan source row"),
            ("move-destination-occupied", dict(rows=fx_disposed[2:3]), "occupied"),
            ("move-into-own-archive", dict(rows=[move_row(archive_rel(rid, "legacy/MOVE.md"))],
                                           sources=moved_to(archive_rel(rid, "legacy/MOVE.md"))), "beneath"),
            # a protected move destination refuses at APPLY, through protected_destination itself (a
            # non-occupying move composes nothing there, so without this rule the base would commit)
            ("move-protected-aiqt", dict(rows=[move_row(".aiqt/x.md")], sources=moved_to(".aiqt/x.md")),
             "destination is protected"),
            ("move-protected-nested-aiqt", dict(rows=[move_row("docs/.aiqt/x.md")],
                                                sources=moved_to("docs/.aiqt/x.md")), "destination is protected"),
            ("move-protected-git", dict(rows=[move_row(".git/x")], sources=moved_to(".git/x")),
             "destination is protected"),
            ("move-protected-pointer", dict(rows=[move_row(".opf.toml")], sources=moved_to(".opf.toml")),
             "destination is protected"),
            ("move-protected-local-pointer", dict(rows=[move_row(".opf.local.toml")],
                                                  sources=moved_to(".opf.local.toml")),
             "destination is protected"),
            # rows the retirement stage could never finish refuse at apply, before the base commits
            ("move-onto-create-file-path", dict(rows=[fx_create, move_row(fx_new)], sources=moved_to(fx_new)),
             "two plan rows"),
            ("two-moves-one-destination", dict(rows=pair_rows, sources=pair_sources), "two plan rows"),
            ("move-store-tree-case-variant", dict(rows=[move_row(".Working/stuff.md")],
                                                  sources=moved_to(".Working/stuff.md")),
             "inside the store tree"),
            ("retire-source-in-move-root", dict(rows=[in_root_row], sources=in_root_sources), "never removes"),
            ("move-preservation-mismatch", dict(rows=fx_disposed[2:3], sources=moved_to("adopter/elsewhere.md")),
             "records preservation")):
        with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
            root = file_fixture(temp)
            if label == "create-occupied":
                (root / fx_new).parent.mkdir(parents=True)
                (root / fx_new).write_bytes(b"adopter's own\n")
            if label == "move-destination-occupied":
                (root / fx_moved).parent.mkdir(parents=True)
                (root / fx_moved).write_bytes(b"already here\n")
            if label == "retire-source-in-move-root":
                (root / in_root).parent.mkdir(parents=True)
                (root / in_root).write_bytes(in_root_bytes)
            before = tree_state(root)
            _txn, why = apply_stage(root, **kwargs)
            check("file-op-{}-refused-untouched-save-journal-dirs".format(label),
                  why is not None and reason in why and untouched_save_journal_dirs(before, root)
                  and lock_free(root))
    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        root = file_fixture(temp)
        before = snapshot(root)
        wrong = refusal(run_adopt_transaction, root, rid, lambda ops: None)
        late = refusal(run_adopt_transaction, root, rid, compose_rows(fx_disposed[:1], fx_sources),
                       phase="completion")
        check("file-op-other-phase-refused", wrong is None and late is not None and "runs no file op" in late)
        unlanded = refusal(run_adopt_transaction, root, other_run,
                           compose_rows([schema.canonical_op("init-store")]))
        check("compose-rows-unlanded-op-refuses-whole-transaction",
              unlanded is not None and "not yet executable" in unlanded
              and not (root / JOURNAL_REL / other_run).exists())

    # 11e: the retirement-stage pairing rule over hand-built lists (check_apply_ops is pure). A frozen
    # source's removal pairs with its committed base archive copy, or with its move destination's create
    # earlier in the same list, ONLY in RETIREMENT_PHASE, and only at the pinned digest.
    gone = _pinned_remove(src, body, FILE_MODE)
    committed_copy = {copy: _sha256(body)}
    check("compose-retirement-committed-pair-admitted", check_apply_ops(
        rid, RETIREMENT_PHASE, *sealed([gone], {}, RETIREMENT_PHASE), committed=committed_copy) == [])
    check("compose-committed-pair-outside-retirement-refused", any(
        "preserve-first" in f for f in check_apply_ops(
            rid, "completion", *sealed([gone], {}, "completion"), committed=committed_copy)))
    check("compose-retirement-committed-pair-digest-refused", any("differs" in f for f in check_apply_ops(
        rid, RETIREMENT_PHASE, *sealed([gone], {}, RETIREMENT_PHASE), committed={copy: ZERO})))
    check("compose-retirement-write-never-committed-paired", any("preserve-first" in f for f in check_apply_ops(
        rid, RETIREMENT_PHASE, *sealed([w(src, new_body, pin=body)], {src: new_body}, RETIREMENT_PHASE),
        committed=committed_copy)))
    moved_to = store.moved_dest(src)
    check("compose-retirement-move-pair-admitted", check_apply_ops(
        rid, RETIREMENT_PHASE, *sealed([c(moved_to, body), gone], {moved_to: body}, RETIREMENT_PHASE),
        moves={src: moved_to}) == [])
    check("compose-move-pair-at-apply-refused", any("preserve-first" in f for f in check_apply_ops(
        rid, None, *sealed([c(moved_to, body), gone], {moved_to: body}), moves={src: moved_to})))
    # the preserved copy keeps the source's mode bits exactly: a removal pinned at one mode pairs only with
    # a copy created at that mode, in the same list or as the committed base's INTENT created it.
    check("compose-pin-copy-mode-mismatch-refused", any("pinned mode" in f for f in findings_of(
        *sealed([c(copy, body), _pinned_remove(src, body, 0o600)], {copy: body}))))
    check("compose-retirement-committed-pair-mode-refused", any("pinned mode" in f for f in check_apply_ops(
        rid, RETIREMENT_PHASE, *sealed([gone], {}, RETIREMENT_PHASE), committed=committed_copy,
        committed_modes={copy: 0o600})))

    # 11f: reversal. The retirement stage aborts at its final op (its inventory publication) and the
    # journal's reverse-order rollback restores the removed frozen sources byte-exact and removes the
    # relocated destinations; then an interruption through the kill-point seam, after a removal landed,
    # leaves an open transaction the explicit reconcile rolls back from the journal alone, byte-exact.
    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        root = file_fixture(temp)
        apply_stage(root)
        before = snapshot(root)
        real_verify = _journal._verify_staged_digest

        def failing_retirement_inventory(op, payload):
            if op["path"] == inventory_rel(rid, RETIREMENT_PHASE):
                raise _journal.JournalError("injected failure at the retirement inventory publication")
            return real_verify(op, payload)

        with mock.patch.object(_journal, "_verify_staged_digest", failing_retirement_inventory):
            aborted = refusal(run_adopt_transaction, root, rid, compose_rows(fx_disposed, fx_sources),
                              phase=RETIREMENT_PHASE)
        check("retirement-abort-reverses-byte-exact",
              aborted is not None and "rolled back" in aborted and snapshot(root) == before
              and txn_state(root, rid + "." + RETIREMENT_PHASE) == "rolled-back" and lock_free(root))
    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        root = file_fixture(temp)
        apply_stage(root)
        before = snapshot(root)
        removed = interrupt_when(lambda: not (root / "legacy/RULES.md").exists())
        with mock.patch.object(_journal, "_kill_point", removed):
            try:
                run_adopt_transaction(root, rid, compose_rows(fx_disposed, fx_sources), phase=RETIREMENT_PHASE)
                interrupted = False
            except (_Interrupt, AdoptApplyError) as exc:
                interrupted = isinstance(exc, _Interrupt)
        rtxn = rid + "." + RETIREMENT_PHASE
        check("retirement-interrupt-leaves-open-transaction",
              interrupted and txn_state(root, rtxn) == "open" and not (root / "legacy/RULES.md").exists())
        check("retirement-interrupt-reconciles-back-byte-exact",
              (rtxn, "rolled-back") in reconcile_without_store(root) and snapshot(root) == before
              and lock_free(root))

    # 11g: a HAND-BUILT retirement removal (a compose callback appending a pinned removal, never through
    # ApplyOps.retire) is held to the live preservation check too: the committed archive copy it pairs with
    # is re-read before the transaction opens, so a missing or modified copy refuses with the tree untouched,
    # while the intact copy admits the same removal (the control leg).
    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        root = file_fixture(temp)
        apply_stage(root)
        rules_copy = root / archive_rel(rid, "legacy/RULES.md")
        saved = rules_copy.read_bytes() if rules_copy.is_file() else None

        def hand_retire(ops):
            mode = stat.S_IMODE((root / "legacy/RULES.md").lstat().st_mode)
            ops.ops.append(_pinned_remove("legacy/RULES.md", fx_files["legacy/RULES.md"], mode))

        for label, tamper in (("missing", rules_copy.unlink),
                              ("modified", lambda: rules_copy.write_bytes(b"old rulez\n"))):
            if saved is not None:
                tamper()
            before = tree_state(root)
            why = refusal(run_adopt_transaction, root, rid, hand_retire, phase=RETIREMENT_PHASE)
            check("retirement-hand-built-removal-{}-archive-refused-untouched-save-journal-dirs".format(label),
                  saved is not None and why is not None and "committed archive copy" in why
                  and untouched_save_journal_dirs(before, root))
            if saved is not None:
                rules_copy.write_bytes(saved)
        htxn, _why = attempt(run_adopt_transaction, root, rid, hand_retire, phase=RETIREMENT_PHASE)
        check("retirement-hand-built-removal-intact-archive-admitted",
              htxn == rid + "." + RETIREMENT_PHASE and not (root / "legacy/RULES.md").exists())

    # 11h: every preserved copy and relocated file keeps its source's mode bits EXACTLY (the journal pins a
    # create's mode with fchmod, so a dropped mode would install FILE_MODE): a private 0600 source is never
    # widened and an executable 0755 one keeps its bits, through the apply stage's archive copies and the
    # retirement stage's relocations, live and from the committed archive.
    for mode in (0o600, 0o755):
        with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
            root = file_fixture(temp)
            for rel in fx_files:
                (root / rel).chmod(mode)

            def mode_of(rel):
                return stat.S_IMODE((root / rel).lstat().st_mode) if (root / rel).is_file() else None

            txn, _why = apply_stage(root)
            copies = [archive_rel(rid, p) for p in ("legacy/RULES.md", ".working/TODO.md", "docs/VIEW.md")]
            check("mode-{:04o}-apply-archive-copies-keep-source-mode".format(mode),
                  txn == rid and all(mode_of(p) == mode for p in copies))
            rtxn, _why = retirement_stage(root)
            check("mode-{:04o}-retirement-relocations-keep-source-mode".format(mode),
                  rtxn == rid + "." + RETIREMENT_PHASE and mode_of(fx_moved) == mode
                  and mode_of(fx_view_dest) == mode and all(mode_of(p) == mode for p in copies)
                  and verify_bundle(root, rid).status == VALID)

    # 11i: the retirement stage re-verifies an OCCUPYING retire source's archive copy too (spec 14.1 check
    # 3: it re-verifies the archived bytes against the plan digest before recording), although it composes
    # no removal for it: a modified or missing copy refuses the stage with the tree untouched.
    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        root = file_fixture(temp)
        apply_stage(root)
        todo_copy = root / archive_rel(rid, ".working/TODO.md")
        saved = todo_copy.read_bytes() if todo_copy.is_file() else None
        for label, tamper in (("modified", lambda: todo_copy.write_bytes(b"hand-kept todO\n")),
                              ("missing", todo_copy.unlink)):
            if saved is not None:
                tamper()
            before = tree_state(root)
            _txn, why = retirement_stage(root, rows=fx_disposed[1:2])
            check("retirement-occupying-retire-{}-archive-refused-untouched-save-journal-dirs".format(label),
                  saved is not None and why is not None and "archive copy" in why
                  and untouched_save_journal_dirs(before, root))
            if saved is not None:
                todo_copy.write_bytes(saved)

    # 11j: a source row whose path is not a string (unhashable included) binds nothing: the op refuses as
    # unbound (AdoptApplyError), never a raw TypeError out of the transaction shell.
    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        root = file_fixture(temp)
        try:
            _txn, why = apply_stage(root, rows=fx_disposed[:1], sources=[dict(fx_sources[0], path=["x"])])
            raw = None
        except TypeError as exc:
            why, raw = None, exc
        check("op-context-non-string-source-path-binds-nothing",
              raw is None and why is not None and "plan source row" in why and lock_free(root))

    # 12: the kill-injection matrix (the journal's crash harness, the migrate.py model): each stage's
    # transaction runs in a CHILD interpreter that os._exit()s (137) at one kill point, leaving only what
    # was already fsync'd; a FRESH child then reconciles, breaking the dead owner's stale lock, and an
    # in-process reconcile after it is a no-op (idempotent). The tree outside the journal must then be
    # EXACTLY its prestate (the transaction not complete) or the verified poststate a clean child commits
    # (the transaction complete, the bundle verifying), with no lock and no open transaction. The kill
    # points are every one the stage's transaction reaches, RECORDED from an in-process run, plus the two
    # torn frame publications. The apply stage covers preservation (archive copies) and preserve-first
    # removal; the retirement stage covers frozen-source removal and destination publication (live and
    # from the committed archive). The rollback leg kills the transaction one op short of its end, then
    # kills the recovery itself at every restore point and torn rollback frame; a final fresh recovery
    # lands the exact prestate.
    import subprocess

    def crash_child(temp, spec, kill=None):
        spec_file = Path(temp) / "kill-spec.json"
        spec_file.write_text(json.dumps(spec), encoding="utf-8")
        env = dict(os.environ)
        env.pop(_journal.KILL_ENV, None)
        if kill is not None:
            env[_journal.KILL_ENV] = kill
        return subprocess.run([sys.executable, "-I", "-B", os.path.abspath(__file__), "--kill-injection-child",
                               str(spec_file)], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                              timeout=300).returncode

    def stage_spec(root, phase):
        rows = [fx_create] + fx_disposed if phase is None else fx_disposed
        return dict(action="transaction", root=str(root), run_id=rid, phase=phase, rows=rows,
                    sources=fx_sources, content={fx_new: fx_new_bytes.hex()})

    def staged_fixture(temp, phase):
        root = file_fixture(Path(temp) / "root")
        if phase is not None:
            apply_stage(root)          # the committed base, in process: only the stage under test is killed
        return root

    def journal_quiet(root):
        root_fd = store._open_dir_nofollow(root)
        try:
            return journal_state(root_fd, _journal_root(root)) == (None, [])
        finally:
            store._close_fd_exc_safe(root_fd)

    for phase in (None, RETIREMENT_PHASE):
        stage = "apply" if phase is None else "retirement"
        txn_of = _txn_name(rid, phase)
        with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
            root = staged_fixture(temp, phase)
            points = []
            with mock.patch.object(_journal, "_kill_point", points.append):
                (apply_stage if phase is None else retirement_stage)(root)
        n = len([p for p in points if p.startswith("after-apply-")])
        check("kill-matrix-{}-points-recorded".format(stage),
              "after-lock" in points and "after-publish-COMPLETE" in points and n >= 4)
        with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
            root = staged_fixture(temp, phase)
            pre = tree_state(root, journal=False)
            clean = crash_child(temp, stage_spec(root, phase))
            post = tree_state(root, journal=False)
        check("kill-matrix-{}-clean-child-commits".format(stage), clean == 0 and post != pre)
        for point in points + ["torn:INTENT", "torn:COMPLETE"]:
            with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
                root = staged_fixture(temp, phase)
                pre = tree_state(root, journal=False)
                died = crash_child(temp, stage_spec(root, phase), kill=point)
                recovered = crash_child(temp, dict(action="reconcile", root=str(root)))
                again, _why = attempt(reconcile, root)
                state, txn_now = tree_state(root, journal=False), txn_state(root, txn_of)
                check("kill-{}-{}-recovers-to-prestate-or-verified-poststate".format(stage, point),
                      died == 137 and recovered == 0 and again == [] and journal_quiet(root)
                      and ((state == pre and txn_now != "complete")
                           or (state == post and txn_now == "complete"
                               and verify_bundle(root, rid).status == VALID)))
        for rpoint in (["torn:ROLLBACK-IN-PROGRESS"] + ["after-restore-{}".format(i) for i in range(n)]
                       + ["torn:ROLLBACK-COMPLETE"]):
            with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
                root = staged_fixture(temp, phase)
                pre = tree_state(root, journal=False)
                died = crash_child(temp, stage_spec(root, phase), kill="after-apply-{}".format(n - 2))
                rdied = crash_child(temp, dict(action="reconcile", root=str(root)), kill=rpoint)
                recovered = crash_child(temp, dict(action="reconcile", root=str(root)))
                again, _why = attempt(reconcile, root)
                check("kill-rollback-{}-{}-lands-exact-prestate".format(stage, rpoint),
                      died == 137 and rdied == 137 and recovered == 0 and again == [] and journal_quiet(root)
                      and tree_state(root, journal=False) == pre and txn_state(root, txn_of) == "rolled-back")

    if failures:
        print("OPF-ADOPT-APPLY SELF-TEST: FAIL ({} of {} checks failed)".format(len(failures), checked[0]))
        for f in failures:
            print("  FAILED: {}".format(f))
        return 1
    print("OPF-ADOPT-APPLY SELF-TEST: PASS ({} apply-engine checks; executable ops: {})".format(
        checked[0], ", ".join(sorted(EXECUTABLE_OPS))))
    return 0


def _kill_injection_child(spec_path):
    """The self-test's kill-injection child, `--kill-injection-child SPEC` (the migrate.py crash-harness
    model): in THIS fresh interpreter, run ONE adoption transaction of the plan rows SPEC names, or
    reconcile(), against SPEC's fixture root. The parent sets _journal.KILL_ENV, so the journal os._exit()s
    (137) at that named point exactly as a power loss would; otherwise 0 done, 2 refused."""
    try:
        spec = json.loads(Path(spec_path).read_text(encoding="utf-8"))
        if spec["action"] == "reconcile":
            reconcile(spec["root"])
        else:
            content = {path: bytes.fromhex(data) for path, data in spec["content"].items()}
            run_adopt_transaction(spec["root"], spec["run_id"],
                                  compose_rows(spec["rows"], spec["sources"], content), phase=spec["phase"])
    except (AdoptApplyError, OSError, ValueError, KeyError) as exc:
        print("kill-injection child refused: {}".format(exc), file=sys.stderr)
        return 2
    return 0


def main():
    args = sys.argv[1:]
    if len(args) == 2 and args[0] == "--kill-injection-child":
        return _kill_injection_child(args[1])
    if "--self-test" in args or "--selftest" in args:
        return self_test()
    print("usage: _opf_adopt_apply.py --self-test (a library module; the adoption verb is `opf adopt`)",
          file=sys.stderr)
    return 2


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-test"]:
        sys.exit(self_test())
    sys.exit(main())
