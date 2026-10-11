#!/usr/bin/env python3
"""OPF adoption apply engine, slices 1 and 5: the apply SHELL and its three finish ops (OPF-SPEC 1.3.0).

This slice (the clean-start adoption track's first apply unit) supplies the engine SKELETON only, shaped
by spec 1.3.0 sections 4.2, 5.7 and 14: run identity; the evidence-bundle, archive and Move homes, all
derived from the `_opf_store` homes constructors (never re-spelled here); the `opf.evidence.inventory/v1`
inventory DERIVED from the run's own transaction op list and published in that SAME transaction as the
retained bytes it lists (spec 4.2), a base `inventory.toml` then one `inventory-<phase>.toml` per later
phase, never rewritten; the homes-1 bundle verification the completion checks carry themselves while
C-EVIDENCE-ENUM is inactive (spec 14.1), which RE-READS the inventories and payload digests from disk;
the preserve-first composition of spec 14.2; live re-observation of every operand; one journaled
transaction per (run, phase), reconcile-first; and a dispatch table keyed by the closed eleven-op
ADOPT_OPS vocabulary. Slice 5 makes the three finish ops executable: plant-governance (create-only
planting of a pack member that passed the b.5 trust gate, verify_pack_member), render-views (create-only view
publication of the render engine's planned bytes, composed into the journaled transaction) and record-adoption (the immutable receipt core and its genesis outcome event in the
run's own evidence bundle, adoption_record). Every other op returns a refusing not-yet-executable verdict:
the file ops, init-store composition, approval capture, hook activation, the completion checks,
retirement, the stage driver, and the MUTATING CLI subcommands (approve, apply, complete, reconcile)
remain later slices; the read-only `opf adopt` subcommands plan and status
shipped with K9a. Live outside the self-test fixtures today: `opf adopt status` opens and lists the
evidence home in opf.py through the _journal containment primitives, then grades each listed bundle
through this module's _verify_bundle_at (beneath the HELD home descriptor it is passed) and the journal
through journal_state, with _open_product_root anchoring both reads to one product-root descriptor;
every mutating entry -- the transaction shell, reconcile() and the dispatch table -- stays reachable
only from the self-test until those slices land.

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
only its retirement preimage at apply and stays frozen in place (its removal waits for the green
completion check, a later slice). An `rmdir` may target ONLY a directory this same transaction created; a
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
journal_clean_or_refuse; the shipped `retire-file` vocabulary row is a single `remove`, while spec 1.3.0
preserves the retirement preimage at apply and removes only after the green check, a vocabulary split for
the op slices; interruption is exercised in-process through the journal's kill-point seam, and
subprocess kill-injection arrives with the file ops. The finish ops add their own: all three finish ops
compose into this shell's transactions, which refuse a resolved store (no lease join), so once init-store
has run, a real adoption's plant, render and receipt transactions refuse until the lease join lands;
render-views composes create-only view publications through this journal (an occupied view destination
refuses fail-closed in this slice; the spec 14.2 preserve-then-render write for a plan-enumerated
occupying source composes with the file-ops slice), delegating only read-only planning and the U6 source
gate to the render and check engines; the receipt core's content (files, approval,
transaction ids, checks, probes) is the stage driver's to assemble, held here only to the shipped
validator and the plan bindings adoption_record names; and the trust gate proves member bytes against the
agreed release inventory, never the publisher's authenticity beyond it.

Outcome model: single-sourced from `_opf_store` exactly as the sibling `_opf_adopt` does; the inventory
grading is the doctor's own shared validator (`_opf_check._evidence_rows`), so a malformed inventory or a
path claimed twice is CANNOT-EVALUATE and the legacy ingest format is the named `legacy-ingest-inventory`
finding (spec 4.2). Filesystem entry points raise `AdoptApplyError` (a fail-closed refusal, exit 2).

Offline, stdlib only, fail-closed. It lives under `opf/tools/` and imports ONLY sibling `opf/tools/`
modules, so the standalone-closure property holds.

Exit convention (the repo's gates and the sibling OPF units): 0 clean, 1 a finding, 2 cannot-evaluate.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: _opf_adopt_apply.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import datetime
import hashlib
import os
import re
import stat
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
    opens; returns the findings (empty means admissible). Preserve-first: every removal AND every write
    (which destroys the live bytes exactly as a removal does) carries a pinned digest and mode and
    follows, in this same list, the create of its archive copy with that same digest (spec 14.2); a write
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
    promotion receipt to claim (the adoption receipt is a bundle member its own transaction's inventory
    claims), so it deliberately takes the STRICTER exactly-one-inventory rule, and a later slice may relax
    it with its own vectors."""
    target = inventory_rel(run_id, phase)
    roots = (evidence_home_rel(run_id), _archive_root(run_id), _MOVED_ROOT)
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
            created[path] = digest
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
            elif archive_rel(run_id, path) not in created:
                findings.append("{} {} {!r} before, or without, its archive copy earlier in this "
                                "transaction: preserve-first, verify then destroy, never reversed or split "
                                "(spec 14.2)".format(where, verb, path))
            elif created.get(archive_rel(run_id, path)) != pin["sha256"]:
                findings.append("{} {} {!r} whose pinned digest differs from its archive copy's "
                                "content digest".format(where, verb, path))
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
    phase (fail-closed)."""
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


def run_adopt_transaction(product_root, run_id, compose, phase=None):
    """ONE journaled adoption transaction, the run's base transaction or one later phase's, through the
    shared 9.3 engine. Refusals BEFORE anything is written, in order: containment, a non-clean journal
    (reconcile-first: the adoption journal is inspected FIRST, per the module docstring), the store
    posture (no lease join, spec 5.7), an existing transaction of this run and phase, and for a phase
    anything but a committed, INTENT-digest-matched base inventory.toml (spec 4.2). Then, under the journal
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
        if phase is not None:
            _committed_base_or_refuse(root_fd, journal_root, run_id, phase)
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
        store._close_fd_exc_safe(root_fd)


# --- the finish ops: plant-governance, render-views, record-adoption (slice 5) --------------------------

# The run's receipt core and its genesis outcome event are members of the run's OWN evidence bundle (spec
# 4.2: `imported/<kind>/<run-id>/` holds the run's approvals and receipts; spec 14.2: adoption evidence is
# committed and immutable there, and append-only outcome events retain the receipt's history). So both are
# claimed by the transaction's derived inventory and never rewritten; each later event is a further bundle
# member that a later phase publishes.
RECEIPT_NAME = "receipt.toml"
GENESIS_EVENT_NAME = "event-0001.toml"


def receipt_rel(run_id):
    """`.working/imported/adoption/<run-id>/receipt.toml`, the run's immutable receipt core."""
    return evidence_home_rel(run_id) + "/" + RECEIPT_NAME


def genesis_event_rel(run_id):
    """`.working/imported/adoption/<run-id>/event-0001.toml`, the run's genesis `applied` outcome event."""
    return evidence_home_rel(run_id) + "/" + GENESIS_EVENT_NAME


def _composing_ops(op_row, context):
    """The ApplyOps a composing finish op appends to, carried as context["ops"] by the compose callable of
    one adoption transaction; anything else refuses, so such an op never writes outside the shell."""
    ops = context.get("ops") if isinstance(context, dict) else None
    if not isinstance(ops, ApplyOps):
        raise AdoptApplyError("op {!r} composes only inside an adoption transaction, and its context carries "
                              "no ApplyOps; nothing composed".format(op_row["op"]))
    return ops


def _context_plan(context):
    plan = context.get("plan") if isinstance(context, dict) else None
    if not isinstance(plan, dict):
        raise AdoptApplyError("its context carries no approved plan (fail-closed)")
    return plan


def verify_pack_member(plan, pack, source_member):
    """The b.5 trust gate for ONE pack member (spec 14.1: the release identity, bound by digest). `pack`
    carries the release manifest's exact bytes (`manifest`), the caller-observed `root.txt` bytes (`root`)
    and member bytes keyed by source path (`members`). The agreed ROOT is the plan's release.manifest_sha256,
    which must equal the plan's own anchor_sha256 (two fields of the ONE approved plan, frozen at approval;
    observing the anchor at its independent source is the planner's b.5 capture, never re-done here); the
    observed root.txt and the sha256 of the exact manifest bytes must both equal that ROOT; the manifest
    must parse VALID through the shipped _opf_pack_manifest grammar, its tree-sha256 must recompute from
    its own source rows (the consumer obligation that grammar leaves), and its release-version must BE the
    approved release's version, so the approved identity can never name one version while the digest-agreed
    pack carries another; and the member's bytes must match the size and sha256 its one source row records
    in that frozen inventory. Returns (bytes, bare-hex sha256); any failure raises AdoptApplyError. Passing
    proves the bytes are the ones the agreed release lists, never that its publisher is authentic beyond
    the anchor (the self-asserted-identity residual, spec 14.1)."""
    import _opf_pack_manifest as pack_manifest
    release = plan.get("release")
    agreed = release.get("manifest_sha256") if isinstance(release, dict) else None
    if not (schema._is_digest(agreed) and release.get("anchor_sha256") == agreed):
        raise AdoptApplyError("the plan's release manifest_sha256 does not agree with its independent anchor, "
                              "so there is no agreed ROOT (fail-closed)")
    if not isinstance(pack, dict):
        raise AdoptApplyError("its context carries no pack manifest, root.txt and member bytes (fail-closed)")
    root = pack_manifest.parse_root_txt(pack.get("root"))
    if not isinstance(root, str):
        raise AdoptApplyError("the observed root.txt is refused: {}".format("; ".join(root.findings)))
    if "sha256:" + root != agreed:
        raise AdoptApplyError("the observed root.txt names a ROOT other than the plan's agreed release ROOT")
    manifest = pack.get("manifest")
    if type(manifest) is not bytes or "sha256:" + pack_manifest.compute_root(manifest) != agreed:
        raise AdoptApplyError("the pack manifest bytes do not hash to the agreed ROOT (a substituted or "
                              "altered manifest)")
    document, checked = pack_manifest.parse_manifest(manifest)
    if checked.status != store.VALID:
        raise AdoptApplyError("the pack manifest is refused ({}): {}".format(
            checked.status, "; ".join(checked.findings)))
    if pack_manifest.compute_tree(document["sources"]) != document["tree-sha256"]:
        raise AdoptApplyError("the pack manifest's tree-sha256 does not recompute from its own source rows")
    if document["release-version"] != release.get("version"):
        raise AdoptApplyError("the pack manifest's release-version {!r} is not the approved release version "
                              "{!r}; the approved release identity is contradictory (fail-closed)".format(
                                  document["release-version"], release.get("version")))
    rows = [row for row in document["sources"] if row["path"] == source_member]
    if len(rows) != 1:
        raise AdoptApplyError("source_member {!r} is not a source row of the frozen pack "
                              "inventory".format(source_member))
    members = pack.get("members")
    data = members.get(source_member) if isinstance(members, dict) else None
    if type(data) is not bytes or len(data) != rows[0]["bytes"] or _sha256(data) != rows[0]["sha256"]:
        raise AdoptApplyError("pack member {!r} does not match the size and sha256 the frozen pack "
                              "inventory records (a forged or altered member)".format(source_member))
    return data, rows[0]["sha256"]


def _plant_governance(op_row, context=None):
    """plant-governance (spec 14.1): create-only planting of ONE pack member whose bytes passed the trust
    gate (verify_pack_member, against context["plan"] and context["pack"]) and whose verified digest is the
    row's content_digest, composed into the transaction context["ops"] carries. ApplyOps.create re-observes
    the path absent (a pre-existing different file routes to retire-file plus create-file under explicit
    plan rows, never an overwrite). The store tree is the engine's, never a governance adapter's, so a path
    there refuses, as do every protected destination and the product-root VERSION deliverable (spec 14.2
    names it render-managed; declared views live inside the store tree, refused here, and a planned
    collision with one is the plan validator's). Reversal before commit is the journal's rollback,
    which removes the planted file. Every refusal is CANNOT-EVALUATE and precedes any filesystem
    publication; one raised inside ApplyOps.create (a path component that is not a directory) can leave
    context["ops"] partly composed, so the compose callable MUST raise on any refusing verdict, discarding
    the whole transaction before it opens (run_adopt_transaction then writes nothing)."""
    try:
        ops = _composing_ops(op_row, context)
        path = op_row["path"]
        if (_within(path.casefold(), store.WORKING_DIRNAME)
                or schema.protected_destination(path, ops.run_id) is not None):
            raise AdoptApplyError("{!r} lies in the store tree or is a protected destination, where no "
                                  "governance adapter is planted (fail-closed)".format(path))
        if path.casefold() == "version":
            raise AdoptApplyError("{!r} is the product-root VERSION deliverable, a render-managed "
                                  "destination (spec 14.2), where no governance adapter is planted "
                                  "(fail-closed)".format(path))
        data, digest = verify_pack_member(_context_plan(context), context.get("pack"), op_row["source_member"])
        if "sha256:" + digest != op_row["content_digest"]:
            raise AdoptApplyError("the verified member {!r} is not the content_digest the row "
                                  "binds".format(op_row["source_member"]))
        ops.create(path, data)
    except AdoptApplyError as exc:
        return schema._cannot("plant-governance refused: {}".format(exc))
    return schema._ok()


def _planned_views(res):
    """{product-relative destination: bytes} of every declared view the render engine's own read-only
    planner (_opf_views.plan_views) renders for the resolved store `res`; writes nothing."""
    import _opf_views
    try:
        fd = store._open_store_root_fd(res.store_root, False)
    except OSError as exc:
        raise AdoptApplyError("cannot open the store root ({}); fail-closed".format(exc))
    try:
        planned = _opf_views.plan_views(fd, res.machine_rel)
    except (_opf_views.ViewsError, RecursionError, ValueError, OSError) as exc:
        raise AdoptApplyError("the render engine cannot plan the declared views ({})".format(exc))
    finally:
        store._close_fd_exc_safe(fd)
    return {dest: text.encode("utf-8") for _name, _scope, dest, text in planned}


def _render_views(op_row, context=None):
    """render-views (spec 14, 14.2): compose, into the transaction context["ops"] carries, the CREATE of
    every declared view with the exact approved bytes, so the views land through the same journaled
    adoption transaction, per-op preimage checks, reversal (the journal's rollback removes them from their
    captured absent preimages) and adoption-journal lock as every other composing op. `context` carries
    `product_root` (the transaction's own product root, bound to ops.root_fd by directory identity), `plan`
    (its frozen `store` identity) and `observations` (the inert git-derived facts the git-aware caller
    gathers, exactly as `opf render --write` does through _opf_observe.gather).

    Preconditions, all before anything is composed: the plan's frozen DEFAULT store resolves at the product
    root, which is the transaction's own root; the row's members are EXACTLY the declared view destinations
    the render engine's own read-only planner renders, and each member digest is the digest of the bytes it
    renders, so apply reproduces the approved bytes or refuses, never writes others; the U6 source gate
    (_opf_check.validate_store with the caller's observations, judged by source_integrity_ok) passes, so no
    view is rendered over a store whose source integrity is not sound; every authored deliverable this op
    does not own (C-CHANGELOG-GATES always, C-VERSION-FILE unless a VERSION view is planned) already
    passes, so an authored residual refuses with NOTHING written, never after a write; and every
    destination is observed ABSENT, contained and no-follow. An occupied view destination refuses
    fail-closed in this slice (spec 14.2: adopter content is never absorbed, deleted or overwritten; the
    preserve-then-render write for a plan-enumerated occupying source composes with the file-ops slice).
    The journal then captures each create's absent prestate under its lock and digest-verifies the written
    poststate: a destination raced in between compose and that capture is refused there as a prestate
    violation before the transaction's INTENT publishes (nothing opened), and the create itself is
    exclusive and no-follow; an arbitrary concurrent writer is outside the journal's model. Every handler
    refusal is CANNOT-EVALUATE and precedes any filesystem publication; the preconditions above refuse
    before anything is composed, while one raised inside ApplyOps.create can leave context["ops"] partly
    composed, so the compose callable MUST raise on any refusing verdict, discarding the whole transaction
    before it opens (run_adopt_transaction then writes nothing)."""
    try:
        _render_views_compose(op_row, context)
    except AdoptApplyError as exc:
        return schema._cannot("render-views refused: {}".format(exc))
    return schema._ok()


def _render_views_compose(op_row, context):
    import _opf_check
    ops = _composing_ops(op_row, context)
    plan = _context_plan(context)
    product_root = context.get("product_root")
    if not isinstance(product_root, (str, os.PathLike)):
        raise AdoptApplyError("its context carries no product_root (fail-closed)")
    frozen = plan.get("store") if isinstance(plan.get("store"), dict) else {}
    res = store.resolve_store(product_root)
    if not (res.status == store.RESOLVED and res.pointer_source == "default"
            and op_row["store_root"] == "." == frozen.get("store_root")
            and res.machine_rel == frozen.get("machine_rel")
            and Path(os.path.abspath(res.store_root)) == Path(os.path.abspath(product_root))):
        raise AdoptApplyError("render-views renders only the plan's frozen default store at the product "
                              "root, and it must resolve (status {}, plan store {!r}); nothing "
                              "composed".format(res.status, frozen))
    try:
        fd_st = os.fstat(ops.root_fd)
        ctx_st = os.stat(os.path.abspath(product_root))
    except OSError as exc:
        raise AdoptApplyError("cannot bind the context product root to the transaction root ({}); "
                              "fail-closed".format(exc))
    if (fd_st.st_dev, fd_st.st_ino) != (ctx_st.st_dev, ctx_st.st_ino):
        raise AdoptApplyError("the context product_root is not the transaction's own product root, so the "
                              "composed views would land in a tree the store checks never saw; nothing "
                              "composed (fail-closed)")
    planned = _planned_views(res)
    members = {m["path"]: m["digest"] for m in op_row["members"]}
    if set(members) != set(planned):
        raise AdoptApplyError("the row's members are not exactly the declared view destinations {}; "
                              "nothing composed".format(sorted(planned)))
    stale = sorted(p for p in planned if "sha256:" + _sha256(planned[p]) != members[p])
    if stale:
        raise AdoptApplyError("the render engine renders bytes other than the row binds at {}; apply "
                              "reproduces the approved bytes or refuses, never writes others (nothing "
                              "composed)".format(", ".join(stale)))
    report = _opf_check.validate_store(res, observations=context.get("observations"))
    if not _opf_check.source_integrity_ok(report):
        raise AdoptApplyError("the store's SOURCE integrity is not sound (U6 validate_store), so no view is "
                              "rendered over it; nothing composed")
    owned = {"C-VIEW-DRIFT"} | ({"C-VERSION-FILE"} if "VERSION" in planned else set())
    residual = sorted(cid for cid in _opf_check.DELIVERABLE_DRIFT_CHECKS
                      if cid not in owned and report.checks.get(cid) != "PASS")
    if residual:
        raise AdoptApplyError("authored deliverable(s) {} must already pass; render-views renders only its "
                              "own declared views and repairs nothing authored; nothing composed".format(
                                  ", ".join(residual)))
    occupied = sorted(p for p in planned if observe_live(ops.root_fd, p)["kind"] != "absent")
    if occupied:
        raise AdoptApplyError("view destination(s) {} are occupied; adopter content is never absorbed or "
                              "overwritten (spec 14.2), and an occupying source routes to its preserve-first "
                              "disposition with the file ops; nothing composed".format(", ".join(occupied)))
    for path in sorted(planned):
        ops.create(path, planned[path])


def event_digest(event):
    """The event_digest of ONE outcome event (the spec 14.2 append-only events): the sha256 of the event's
    canonical bytes with the `event_digest` key omitted, so the digest can never cover itself. The ONE
    definition both the mint and the recomputation check use: adoption_record re-parses the bytes it
    emitted and requires the recorded digest to recompute through this helper, so a recorded digest that
    is not the canonical-bytes digest can never publish. Raises AdoptApplyError fail-closed."""
    if not isinstance(event, dict):
        raise AdoptApplyError("an outcome event must be a table")
    body = {key: event[key] for key in event if key != "event_digest"}
    try:
        return "sha256:" + _sha256(emit_checked(body).encode("utf-8"))
    except EmitError as exc:
        raise AdoptApplyError("the outcome event cannot be emitted canonically ({}); fail-closed".format(exc))


def adoption_record(run_id, plan, receipt, now):
    """(receipt core bytes, genesis event bytes) of one adoption run (spec 14, 14.1), pure. The receipt core
    the stage driver assembles must validate through the shipped validate_receipt_core, which also enforces
    that the embedded approval attests the receipt's own plan_digest and inventory_digest; it must bind this
    run and the approved plan's product, plan_digest, inventory_digest, store root and release manifest, and
    record an observed, agreeing independent anchor. The genesis `applied` event chains from the sha256 of
    the canonical core bytes; its event_digest is the named event_digest helper's (the sha256 of its own
    canonical bytes without that key), required to RECOMPUTE from the emitted bytes before anything returns;
    the one-event chain must validate through the shipped validate_event_chain. `now` is the injected,
    aware-UTC recorded_at instant. Raises AdoptApplyError on any refusal."""
    checked = schema.validate_receipt_core(receipt)
    if checked.status != store.VALID:
        raise AdoptApplyError("the receipt core is refused ({}): {}".format(
            checked.status, "; ".join(checked.findings)))
    if receipt["run_id"] != run_id or plan.get("run_id") != run_id:
        raise AdoptApplyError("the receipt core, the plan and the transaction name different adoption runs")
    unbound = [key for key in ("product", "plan_digest", "inventory_digest") if receipt[key] != plan.get(key)]
    frozen, release = plan.get("store"), plan.get("release")
    if not (isinstance(frozen, dict) and receipt["store_root"] == frozen.get("store_root")):
        unbound.append("store_root")
    if not (isinstance(release, dict)
            and receipt["release"]["manifest_sha256"] == release.get("manifest_sha256")):
        unbound.append("release.manifest_sha256")
    if unbound:
        raise AdoptApplyError("the receipt core does not bind the approved plan's {}".format(
            ", ".join(unbound)))
    anchor = receipt["release"]
    if anchor["independent_anchor_observed"] is not True or anchor["anchor_agreement"] is not True:
        raise AdoptApplyError("the receipt records no observed, agreeing independent release anchor")
    if (type(now) is not datetime.datetime or type(now.tzinfo) is not datetime.timezone
            or now.utcoffset() != datetime.timedelta(0)):
        raise AdoptApplyError("now must be a clock-derived aware UTC datetime")
    try:
        core = emit_checked(receipt).encode("utf-8")
        event = dict(format=schema.EVENT_FORMAT, schema=schema.SCHEMA_VERSION, kind="applied", run_id=run_id,
                     revision=plan.get("revision"), recorded_at=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                     previous_event_digest="sha256:" + _sha256(core))
        event["event_digest"] = event_digest(event)
        event_bytes = emit_checked(event).encode("utf-8")
    except EmitError as exc:
        raise AdoptApplyError("the adoption record cannot be emitted canonically ({}); "
                              "fail-closed".format(exc))
    # Defence in depth: emit_checked already refuses bytes that do not round-trip, so by construction no
    # vector reaches this refusal; the self-test pins the contract with an independent recompute of the
    # canonical event body instead.
    reparsed = tomllib.loads(event_bytes.decode("utf-8"))
    if reparsed.get("event_digest") != event_digest(reparsed):
        raise AdoptApplyError("the genesis event_digest does not recompute from the emitted event's own "
                              "canonical bytes (fail-closed)")
    chain = schema.validate_event_chain([tomllib.loads(event_bytes.decode("utf-8"))], "sha256:" + _sha256(core))
    if chain.status != store.VALID:
        raise AdoptApplyError("the genesis outcome event is refused ({}): {}".format(
            chain.status, "; ".join(chain.findings)))
    return core, event_bytes


def minted_record_row(run_id, core):
    """The stage driver's record-adoption row MINT (spec 14, 14.1): the ONE executable row shape, minted
    only once the approval exists and the receipt core's canonical bytes are assembled, binding this run's
    bundle receipt path and the core's digest. The EXPLICIT stage-driver rule, pinned in the self-test: the
    executable row is never a plan operand -- validate_plan itself refuses a plan whose record-adoption row
    names this bundle receipt path (the receipt creation is a reserved-control-area effect, spec 14.2) --
    and the plan binds the receipt through the approval digests the core must embed (validate_receipt_core
    plus the bindings _record_adoption enforces), while the genesis event is bound by chaining from the
    core digest this row carries and by its recomputed event_digest."""
    if not isinstance(core, bytes):
        raise AdoptApplyError("minted_record_row needs the receipt core's canonical bytes (fail-closed)")
    return dict(op="record-adoption", receipt_path=receipt_rel(run_id),
                receipt_core_digest="sha256:" + _sha256(core))



def _record_adoption(op_row, context=None):
    """record-adoption (spec 14, 14.1): compose, into the transaction context["ops"] carries, the create of
    the run's immutable receipt core at its bundle receipt_rel and of its genesis `applied` outcome event
    beside it (adoption_record, from context["plan"], context["receipt"] and context["now"]). The row must
    name exactly that receipt path and bind the emitted core's digest: as the planner records, no frozen
    plan can carry that digest (the core binds the approval captured after the plan freezes), so the stage
    driver MINTS this row once the approval exists (minted_record_row, the one mint; validate_plan
    refuses the bundle receipt path as a reserved-control-area creation, so the executable row is never a
    plan operand, the explicit stage-driver rule the self-test pins). Both files are create-only, so a run
    records exactly one genesis: both destinations are observed absent before either create is composed,
    so a second record-adoption of the same run, in this transaction or a later one, refuses on the
    occupied path with neither file composed. Reversal before commit is the journal's rollback, which
    removes both files. Every refusal is CANNOT-EVALUATE and precedes any filesystem publication; one
    raised inside ApplyOps.create (a path component that is not a directory) can leave context["ops"]
    partly composed, so the compose callable MUST raise on any refusing verdict, discarding the whole
    transaction before it opens (run_adopt_transaction then writes nothing)."""
    try:
        ops = _composing_ops(op_row, context)
        if op_row["receipt_path"] != receipt_rel(ops.run_id):
            raise AdoptApplyError("receipt_path {!r} is not this run's bundle receipt {!r}".format(
                op_row["receipt_path"], receipt_rel(ops.run_id)))
        core, event = adoption_record(ops.run_id, _context_plan(context), context.get("receipt"),
                                      context.get("now"))
        if "sha256:" + _sha256(core) != op_row["receipt_core_digest"]:
            raise AdoptApplyError("the row's receipt_core_digest is not the digest of the emitted core")
        dests = (receipt_rel(ops.run_id), genesis_event_rel(ops.run_id))
        for rel in dests:
            if rel in ops.staged:
                raise AdoptApplyError("{!r} is already composed in this transaction: one run records "
                                      "exactly one genesis".format(rel))
        occupied = [rel for rel in dests if observe_live(ops.root_fd, rel)["kind"] != "absent"]
        if occupied:
            raise AdoptApplyError("{} already occupied: one run records exactly one genesis, so neither "
                                  "the receipt nor its genesis event is composed".format(
                                      ", ".join(repr(rel) for rel in occupied)))
        ops.create(dests[0], core)
        ops.create(dests[1], event)
    except AdoptApplyError as exc:
        return schema._cannot("record-adoption refused: {}".format(exc))
    return schema._ok()


# --- the dispatch table: every op but the three finish ops refuses not-yet-executable --------------

def _not_yet_executable(op_row, context=None):
    """The refusing not-yet-executable verdict behind every dispatch entry no slice has landed yet. Each
    landing slice replaces its OP_HANDLERS entry with a real executor; the self-test pins every OTHER entry
    to THIS handler and every such canonical row to a refusing status, so a silently-enabled op is a red."""
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
    "plant-governance": _plant_governance,
    "register-unmanaged": _not_yet_executable,
    "move-file": _not_yet_executable,
    "repoint-consumer": _not_yet_executable,
    "retire-file": _not_yet_executable,
    "enable-hook": _not_yet_executable,
    "render-views": _render_views,
    "record-adoption": _record_adoption,
}


def dispatch(op_row, context=None):
    """Validate, then dispatch ONE plan op row. A malformed row propagates the validator's refusing
    verdict; an op with no registered handler (dispatch-roster drift) is CANNOT-EVALUATE, never a skip.
    dispatch itself never writes: the three finish ops compose into the transaction their context
    carries, and every other handler refuses with no side effect."""
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
    where that finding is itself the contract). No git, no network, no subprocess; every write lands
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
    import json
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

    # 0: the dispatch roster reconciles against the closed vocabulary in BOTH directions, every entry but the
    # three landed finish ops (slice 5) is pinned to the refusing handler, and every canonical row refuses
    # (each finish op's for want of its context, before anything is observed). A silently-enabled op is a
    # red; the slice that legitimately lands an op updates these pins in the same change.
    landed = dict([("plant-governance", _plant_governance), ("render-views", _render_views),
                   ("record-adoption", _record_adoption)])
    check("handlers-cover-vocabulary", set(OP_HANDLERS) == set(schema.ADOPT_OPS_BY_NAME))
    check("handlers-all-refusing-but-the-finish-ops",
          all(h is _not_yet_executable for n, h in OP_HANDLERS.items() if n not in landed))
    check("handlers-finish-ops-landed", all(OP_HANDLERS[n] is h for n, h in landed.items()))
    for name, needle in (("plant-governance", "no ApplyOps"), ("record-adoption", "no ApplyOps"),
                         ("render-views", "no ApplyOps")):
        res = dispatch(schema.canonical_op(name))
        check("op-{}-refuses-without-context".format(name),
              res.status == CANNOT and any(needle in f for f in res.findings))
    for name in sorted(schema.ADOPT_OP_NAMES - set(landed)):
        res = dispatch(schema.canonical_op(name))
        check("op-{}-refuses-not-yet-executable".format(name),
              res.status == CANNOT and any("not yet executable" in f for f in res.findings))
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

    # 11: the finish ops (slice 5), see _finish_ops_self_test.
    _finish_ops_self_test(check)

    if failures:
        print("OPF-ADOPT-APPLY SELF-TEST: FAIL ({} of {} checks failed)".format(len(failures), checked[0]))
        for f in failures:
            print("  FAILED: {}".format(f))
        return 1
    print("OPF-ADOPT-APPLY SELF-TEST: PASS ({} apply-shell checks; three executable ops, the finish "
          "ops)".format(checked[0]))
    return 0


def _finish_ops_self_test(check):
    """Slice 5: plant-governance, record-adoption and render-views. The two composing ops run through the
    journaled shell over throwaway NOT-ADOPTED fixtures; render-views runs over a resolvable store built from
    the init builders' own bytes, with the inert observations a git-aware caller would gather injected, and
    its expected bytes come from an independent twin store the render engine writes directly. Each refusal
    is attributed by a reason keyword and judged with the tree unchanged."""
    import contextlib
    import io
    import tempfile
    from unittest import mock
    import _opf_check
    import _opf_init
    import _opf_observe
    import _opf_pack_manifest as pack_manifest
    import _opf_views
    valid, invalid, cannot = store.VALID, store.INVALID, store.CANNOT_EVALUATE
    now = datetime.datetime(2026, 9, 17, 12, 0, 0, tzinfo=datetime.timezone.utc)
    rid = mint_run_id(now, "0123456789abcdef")

    def refused(res, needle):
        return res.status == cannot and any(needle in f for f in res.findings)

    def snapshot(root):
        return {str(p.relative_to(root)): p.read_bytes() for p in sorted(Path(root).rglob("*"))
                if p.is_file() and not p.is_symlink() and not str(p.relative_to(root)).startswith(".aiqt/")}

    def composer(rows, **context):
        def compose(ops):
            for row in rows:
                verdict = dispatch(row, dict(context, ops=ops))
                if verdict.status != valid:
                    raise AdoptApplyError("; ".join(verdict.findings))
        return compose

    def run(root, rows, phase=None, **context):
        """(transaction name, None) or (None, refusal text): one adoption transaction over `rows`."""
        try:
            return run_adopt_transaction(root, rid, composer(rows, **context), phase), None
        except AdoptApplyError as exc:
            return None, str(exc)

    # The release: one governance member, its frozen pack inventory (a manifest in the shipped grammar whose
    # TREE recomputes from its source rows) and the agreed ROOT the plan binds and root.txt reports.
    member = b"# Governance adapter\nRecords are written through opf record, never by hand.\n"
    source = "governance/AGENTS.md"

    def manifest_bytes(data, tree=None):
        rows = [dict(path=source, bytes=len(data), sha256=_sha256(data))]
        q = chr(34)
        lines = ["format-version = 1", "release-version = " + q + "1.3.0" + q, "genesis = true",
                 "tree-sha256 = " + q + (tree or pack_manifest.compute_tree(rows)) + q,
                 "", "[[sources]]", "path = " + q + source + q, "bytes = " + str(len(data)),
                 "sha256 = " + q + _sha256(data) + q,
                 "", "[[artifacts]]", "artifact-id = " + q + "file:" + source + q, "path = " + q + source + q,
                 "kind = " + q + "file" + q, "sha256 = " + q + _sha256(data) + q]
        return ("\n".join(lines) + "\n").encode("utf-8")

    def release(manifest, plan=None, anchor=None):
        """(plan, pack) agreeing on the ROOT of `manifest` unless `anchor` names another one."""
        agreed = "sha256:" + pack_manifest.compute_root(manifest)
        plan = dict(plan or base_plan)
        plan["release"] = dict(plan["release"], manifest_sha256=agreed, anchor_sha256=anchor or agreed)
        pack = dict(manifest=manifest, root=(agreed + "\n").encode("ascii"), members=dict([(source, member)]))
        return plan, pack

    base_plan = schema.canonical_plan()
    manifest = manifest_bytes(member)
    plan, pack = release(manifest)
    plant = dict(op="plant-governance", path="AGENTS.md", content_digest="sha256:" + _sha256(member),
                 source_member=source)
    receipt = schema.canonical_receipt_core()
    receipt["release"] = dict(receipt["release"], manifest_sha256=plan["release"]["manifest_sha256"])
    core, event = adoption_record(rid, plan, receipt, now)
    record = minted_record_row(rid, core)
    ctx = dict(plan=plan, pack=pack, receipt=receipt, now=now)
    check("finish-fixture-pack-manifest-valid", pack_manifest.parse_manifest(manifest)[1].status == valid)
    check("finish-fixture-rows-validate", plan["run_id"] == rid and schema.validate_op(plant).status == valid
          and schema.validate_op(record).status == valid)
    check("finish-receipt-in-own-bundle", receipt_rel(rid) == evidence_home_rel(rid) + "/receipt.toml"
          and genesis_event_rel(rid) == evidence_home_rel(rid) + "/event-0001.toml")
    check("record-row-mint-is-the-executable-row",
          record == dict(op="record-adoption", receipt_path=receipt_rel(rid),
                         receipt_core_digest="sha256:" + _sha256(core))
          and schema.validate_op(record).status == valid)
    # M3: the genesis event's digest recomputes from its own canonical bytes without the event_digest key,
    # through the named helper AND an independent inline recomputation, so a minted digest that is not the
    # canonical-bytes digest is a red here and a refusal inside adoption_record itself.
    genesis_doc = tomllib.loads(event.decode("utf-8"))
    genesis_body = dict((k, genesis_doc[k]) for k in genesis_doc if k != "event_digest")
    check("genesis-event-digest-recomputes",
          genesis_doc["event_digest"] == "sha256:" + _sha256(emit_checked(genesis_body).encode("utf-8"))
          and genesis_doc["event_digest"] == event_digest(genesis_doc))
    # The explicit stage-driver rule behind the mint: the executable record-adoption row is never a plan
    # operand. validate_plan itself refuses a plan whose record-adoption row names the run's bundle receipt
    # (the receipt creation is a reserved-control-area effect, spec 14.2), so the minted row can never be
    # smuggled through an approval; the plan binds the receipt through the approval digests the core must
    # embed (validate_receipt_core plus _record_adoption's bindings).
    minted_ops = [dict(record) if r.get("op") == "record-adoption" else r for r in base_plan["ops"]]
    minted_plan = dict(base_plan, ops=minted_ops,
                       effects=schema.derive_effects(minted_ops, base_plan["sources"],
                                                     schema.store_manifest(base_plan["store"])))
    minted_graded = schema.validate_plan(minted_plan)
    check("record-minted-row-never-plannable",
          schema.validate_plan(base_plan).status == valid and minted_graded.status == invalid
          and any("control area" in f and receipt_rel(rid) in f for f in minted_graded.findings))
    # and on homes 2 even validate_op refuses the minted row's receipt operand, so the homes-2 activation
    # slice must route the receipt through the capability-bound journal API (or land its own explicit rule)
    # before this build's SUPPORTED_HOMES rises -- the fact is pinned so it cannot drift silently.
    check("record-minted-row-homes2-operand-refused",
          schema.validate_op(record, homes=1).status == valid
          and schema.validate_op(record, homes=2).status == invalid)

    # The two composing ops over a NOT-ADOPTED root: the verified member is planted create-only, and the
    # receipt core and its genesis event land in the run's own bundle, claimed by the derived inventory.
    with tempfile.TemporaryDirectory(prefix="opf-adopt-finish-") as temp:
        root = Path(temp).resolve()
        txn, why = run(root, [plant, record], **ctx)
        after = snapshot(root)
        check("finish-plant-and-record-commit", txn == rid and why is None)
        check("plant-governance-planted-verified-bytes", after.get("AGENTS.md") == member)
        check("record-adoption-receipt-and-genesis-written",
              after.get(receipt_rel(rid)) == core and after.get(genesis_event_rel(rid)) == event)
        listed = []
        if inventory_rel(rid) in after:
            listed = [r["path"] for r in tomllib.loads(after[inventory_rel(rid)].decode("utf-8"))["file"]]
        check("record-adoption-bundle-verifies", verify_bundle(root, rid).status == valid
              and receipt_rel(rid) in listed and genesis_event_rel(rid) in listed)
        disk_core, disk_event = after.get(receipt_rel(rid), b""), after.get(genesis_event_rel(rid), b"x=")
        check("record-adoption-reparsed-core-and-chain-valid",
              schema.validate_receipt_core(tomllib.loads(disk_core.decode("utf-8"))).status == valid
              and schema.validate_event_chain([tomllib.loads(disk_event.decode("utf-8"))],
                                              "sha256:" + _sha256(disk_core)).status == valid)
        # one genesis per run: a later phase of the same run recording again refuses on the occupied receipt.
        before = snapshot(root)
        txn, why = run(root, [record], phase="completion", **ctx)
        check("record-adoption-second-genesis-later-phase-refused",
              txn is None and "occupied" in (why or "") and snapshot(root) == before)
    # both destinations are observed before either is composed: an occupied genesis event beside an absent
    # receipt refuses with NOTHING composed, not with the receipt create already appended.
    with tempfile.TemporaryDirectory(prefix="opf-adopt-finish-") as temp:
        root = Path(temp).resolve()
        planted = root / genesis_event_rel(rid)
        planted.parent.mkdir(parents=True)
        planted.write_bytes(b"the adopter's own bytes\n")
        before, seen = snapshot(root), []

        def probe(ops):
            verdict = dispatch(dict(record), dict(ctx, ops=ops))
            seen.append((verdict, list(ops.ops), dict(ops.staged)))
            raise AdoptApplyError("probe")
        try:
            run_adopt_transaction(root, rid, probe)
        except AdoptApplyError:
            pass
        check("record-adoption-occupied-event-composes-nothing",
              len(seen) == 1 and refused(seen[0][0], "neither the receipt nor its genesis event is composed")
              and seen[0][1] == [] and seen[0][2] == {} and snapshot(root) == before)

    def refuses_untouched(label, rows, needle, **changes):
        with tempfile.TemporaryDirectory(prefix="opf-adopt-finish-") as temp:
            root = Path(temp).resolve()
            txn, why = run(root, rows, **dict(ctx, **changes))
            check(label, txn is None and needle in (why or "") and snapshot(root) == dict())

    # The trust gate (b.5): each single mutation refuses with nothing written.
    forged = bytearray(member)
    forged[0] ^= 1
    refuses_untouched("plant-forged-member-refused", [plant], "forged",
                      pack=dict(pack, members=dict([(source, bytes(forged))])))
    altered = manifest.replace(b"1.3.0", b"1.3.1")
    refuses_untouched("plant-substituted-manifest-refused", [plant], "do not hash to the agreed ROOT",
                      pack=dict(pack, manifest=altered))
    other_root = ("sha256:" + "7" * 64 + "\n").encode("ascii")
    refuses_untouched("plant-other-root-txt-refused", [plant], "names a ROOT other",
                      pack=dict(pack, root=other_root))
    refuses_untouched("plant-disagreeing-anchor-refused", [plant], "no agreed ROOT",
                      plan=release(manifest, anchor="sha256:" + "7" * 64)[0])
    untree = manifest_bytes(member, tree="0" * 64)
    plan_t, pack_t = release(untree)
    refuses_untouched("plant-tree-not-recomputing-refused", [plant], "does not recompute",
                      plan=plan_t, pack=pack_t)
    refuses_untouched("plant-unbound-content-digest-refused",
                      [dict(plant, content_digest="sha256:" + "7" * 64)], "not the content_digest")
    refuses_untouched("plant-unknown-source-member-refused",
                      [dict(plant, source_member="governance/OTHER.md")], "not a source row")
    refuses_untouched("plant-store-tree-path-refused", [dict(plant, path=".working/AGENTS.md")], "store tree")
    # the approved release identity reconciles with the digest-agreed pack manifest: a plan naming
    # another version over the same agreed bytes is contradictory and refuses, nothing written.
    version_plan = dict(plan)
    version_plan["release"] = dict(plan["release"], version="9.9.9")
    refuses_untouched("plant-release-version-mismatch-refused", [plant],
                      "not the approved release version", plan=version_plan)
    # the product-root VERSION deliverable is a render-managed destination (spec 14.2), never a plant target.
    refuses_untouched("plant-version-deliverable-refused", [dict(plant, path="VERSION")],
                      "VERSION deliverable")
    with tempfile.TemporaryDirectory(prefix="opf-adopt-finish-") as temp:
        root = Path(temp).resolve()
        (root / "AGENTS.md").write_bytes(b"the adopter's own file\n")
        txn, why = run(root, [plant], **ctx)
        check("plant-occupied-destination-refused", txn is None and "occupied" in (why or "")
              and snapshot(root) == dict([("AGENTS.md", b"the adopter's own file\n")]))

    # The receipt (b.4): the shipped validator discriminates an approval attesting a different plan, the
    # receipt must bind THIS plan, and the shipped chain validator refuses a second genesis.
    stray = "sha256:" + "7" * 64
    wrong_approval = dict(receipt, approval=dict(receipt["approval"], plan_digest=stray))
    graded = schema.validate_receipt_core(wrong_approval)
    check("receipt-approval-digest-flip-invalid", graded.status == invalid
          and any("approval attests" in f for f in graded.findings))
    refuses_untouched("record-approval-digest-flip-refused", [record], "approval attests",
                      receipt=wrong_approval)
    other_plan = dict(receipt, plan_digest=stray, approval=dict(receipt["approval"], plan_digest=stray))
    check("receipt-other-plan-validates-alone", schema.validate_receipt_core(other_plan).status == valid)
    refuses_untouched("record-unbound-plan-digest-refused", [record], "plan_digest", receipt=other_plan)
    # each receipt binding beyond plan_digest is pinned by its own refusal: the run identities (both
    # legs), product, inventory digest, store root and release manifest.
    other_rid = mint_run_id(now, "fedcba9876543210")
    refuses_untouched("record-other-run-receipt-refused", [record], "different adoption runs",
                      receipt=dict(receipt, run_id=other_rid))
    refuses_untouched("record-other-run-plan-refused", [record], "different adoption runs",
                      plan=dict(plan, run_id=other_rid))
    refuses_untouched("record-unbound-product-refused", [record], "plan's product",
                      receipt=dict(receipt, product="opf"))
    refuses_untouched("record-unbound-inventory-digest-refused", [record], "inventory_digest",
                      receipt=dict(receipt, inventory_digest=stray,
                                   approval=dict(receipt["approval"], inventory_digest=stray)))
    refuses_untouched("record-unbound-store-root-refused", [record], "store_root",
                      receipt=dict(receipt, store_root="elsewhere"))
    refuses_untouched("record-unbound-release-manifest-refused", [record], "release.manifest_sha256",
                      receipt=dict(receipt, release=dict(receipt["release"], manifest_sha256=stray)))
    refuses_untouched("record-anchor-disagreement-refused", [record], "agreeing independent",
                      receipt=dict(receipt, release=dict(receipt["release"], anchor_agreement=False)))
    refuses_untouched("record-anchor-unobserved-refused", [record], "agreeing independent",
                      receipt=dict(receipt, release=dict(receipt["release"], independent_anchor_observed=False,
                                                         anchor_agreement=True)))
    refuses_untouched("record-unbound-core-digest-refused",
                      [dict(record, receipt_core_digest=stray)], "receipt_core_digest")
    refuses_untouched("record-receipt-outside-bundle-refused",
                      [dict(record, receipt_path=".working/toml/adoption.toml")], "bundle receipt")
    refuses_untouched("record-naive-now-refused", [record], "aware UTC", now=now.replace(tzinfo=None))
    refuses_untouched("record-second-genesis-same-transaction-refused", [record, record], "exactly one genesis")
    genesis = tomllib.loads(event.decode("utf-8"))
    second = dict(genesis, previous_event_digest=genesis["event_digest"], event_digest=stray)
    chained = schema.validate_event_chain([genesis, second], "sha256:" + _sha256(core))
    check("event-chain-second-genesis-invalid", chained.status == invalid
          and any("genesis" in f for f in chained.findings))
    with mock.patch.object(schema, "validate_event_chain", return_value=schema._invalid(["stub"])):
        refuses_untouched("record-consults-the-chain-validator", [record], "genesis outcome event is refused")
    for name in ("plant-governance", "record-adoption"):
        res = dispatch(dict(plant if name == "plant-governance" else record), dict(ctx))
        check("{}-without-transaction-refused".format(name), refused(res, "no ApplyOps"))

    # render-views composes into the journaled shell: the approved bytes land through the same adoption
    # transaction, preimage checks, reversal and journal lock as every other composing op (spec 14, 14.2).
    # The expected bytes come from a twin store the render engine writes directly, so the composed creates
    # are pinned byte-for-byte to the engine's own output.
    views = sorted(v["target"] for v in tomllib.loads(_opf_init.build_manifest())["views"].values())
    obs = dict(tracked="tracked", prior=_opf_observe._empty_prior())
    _this = sys.modules[__name__]

    def lease_stand_in():
        # The shell refuses a resolved store (no single-writer lease join yet, spec 5.7). This stand-in
        # admits the fixture's resolved store so the composed render transaction is exercised end to end:
        # exactly the seam the lease-join slice will occupy. The UNPATCHED refusal is pinned below
        # (render-views-resolved-store-refused-without-lease).
        return mock.patch.object(_this, "_store_posture_or_refuse", lambda product_root: None)

    def built_store(parent, name, changelog=True):
        root = Path(parent).resolve() / name
        machine = root / store.WORKING_DIRNAME / store.DEFAULT_MACHINE_SUBDIR
        machine.mkdir(parents=True)
        docs = dict([(store.MANIFEST_NAME, _opf_init.build_manifest()), ("counters.toml", _opf_init.build_counters()),
                     ("version.toml", _opf_init.build_version()), ("worklog.toml", _opf_init.build_worklog())])
        for type_name in _opf_init.INDEX_TYPES:
            docs[type_name + _opf_check.INDEX_SUFFIX] = _opf_init.build_index(type_name)
        for name_, text in docs.items():
            (machine / name_).write_text(text, encoding="utf-8")
        if changelog:
            (root / "CHANGELOG.md").write_bytes(b"# Changelog\n")
        return root

    with tempfile.TemporaryDirectory(prefix="opf-adopt-render-") as temp:
        oracle = built_store(temp, "oracle")
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            rc = _opf_views.render(["--root", str(oracle), "--write"], observations=obs)
        rendered = dict((v, (oracle / v).read_bytes()) for v in views if (oracle / v).is_file())
        check("render-oracle-twin-rendered", rc == _opf_views.EXIT_OK and sorted(rendered) == views)
        row = dict(op="render-views", store_root=".",
                   members=[dict(path=v, digest="sha256:" + _sha256(rendered.get(v, b""))) for v in views])
        check("render-row-validates", schema.validate_op(row).status == valid)

        def rctx(root, **changes):
            return dict(dict(product_root=str(root), plan=plan, observations=obs), **changes)

        # the real posture first: WITHOUT the lease join, the shell refuses the resolved store before the
        # handler composes anything, the same spec 5.7 posture plant-governance and record-adoption take.
        root = built_store(temp, "posture")
        before = snapshot(root)
        txn, why = run(root, [row], **rctx(root))
        check("render-views-resolved-store-refused-without-lease",
              txn is None and "lease" in (why or "") and snapshot(root) == before)

        # the committed render: ONE journaled adoption transaction composes the creates; the written views
        # are byte-identical to the engine twin's; the journal holds the completed transaction under a
        # freed lock; nothing else changed but the transaction's own (empty) derived inventory.
        root = built_store(temp, "apply")
        before = snapshot(root)
        with lease_stand_in():
            txn, why = run(root, [row], **rctx(root))
        after = snapshot(root)
        jr_fd = _journal.open_journal_root_from_path(root, JOURNAL_REL)
        try:
            committed = _journal.classify_state(jr_fd, _journal_root(root) / rid)
        finally:
            _journal._close_fd_quietly(jr_fd)
        check("render-views-writes-exactly-the-approved-bytes", txn == rid and why is None
              and all(after.get(v) == rendered.get(v) for v in views)
              and set(after) - set(before) == set(views) | set([inventory_rel(rid)]))
        check("render-views-journaled-transaction-complete",
              committed == "complete" and _journal.read_lock_owner(_journal_root(root)) is None)

        # the reversal (spec 14): an injected failure at the transaction's final op rolls every composed
        # view back from its journaled (absent) preimage; the tree is the exact prestate.
        root = built_store(temp, "abort")
        before = snapshot(root)
        real_verify = _journal._verify_staged_digest

        def failing_inventory(op, payload):
            if op["path"] == inventory_rel(rid):
                raise _journal.JournalError("injected failure at the inventory publication")
            return real_verify(op, payload)

        with lease_stand_in(), mock.patch.object(_journal, "_verify_staged_digest", failing_inventory):
            txn, why = run(root, [row], **rctx(root))
        check("render-views-abort-restores-prestate",
              txn is None and "rolled back" in (why or "") and snapshot(root) == before)

        # preservation (spec 14.2): an adopter's live file at a declared view destination refuses the
        # whole transaction with the live bytes untouched -- never absorbed, deleted or overwritten.
        adopter = b"ADOPTER HAND-MAINTAINED CONTENT\n"
        root = built_store(temp, "occupied")
        (root / views[0]).write_bytes(adopter)
        before = snapshot(root)
        with lease_stand_in():
            txn, why = run(root, [row], **rctx(root))
        check("render-views-occupied-destination-preserved",
              txn is None and "are occupied; adopter content is never absorbed" in (why or "")
              and (root / views[0]).read_bytes() == adopter and snapshot(root) == before)

        def refuses_uncomposed(label, name, needle, rrow=row, changelog=True, **changes):
            root = built_store(temp, name, changelog)
            before = snapshot(root)
            with lease_stand_in():
                txn, why = run(root, [rrow], **dict(rctx(root), **changes))
            check(label, txn is None and needle in (why or "") and snapshot(root) == before)

        # the U6 source gate, now BEFORE anything is composed (an untracked observation, then none at all).
        refuses_uncomposed("render-views-source-gate-refuses-untracked", "untracked", "SOURCE integrity",
                           observations=dict(obs, tracked="untracked"))
        refuses_uncomposed("render-views-source-gate-refuses-without-observations", "noobs",
                           "SOURCE integrity", observations=None)
        bent = [dict(m, digest="sha256:" + "7" * 64) if m["path"] == views[0] else m for m in row["members"]]
        refuses_uncomposed("render-views-unbound-bytes-refused", "bent", "other than the row binds",
                           rrow=dict(row, members=bent))
        refuses_uncomposed("render-views-missing-member-refused", "short", "not exactly the declared view",
                           rrow=dict(row, members=row["members"][1:]))
        other_store = dict(plan, store=dict(plan["store"], machine_rel=".working/data"))
        refuses_uncomposed("render-views-other-store-refused", "other", "frozen default store",
                           plan=other_store)
        # an authored residual (an absent CHANGELOG.md) now refuses with NOTHING written, never after a
        # write the delegated engine would have left in place.
        refuses_uncomposed("render-views-authored-residual-refused", "residual", "C-CHANGELOG-GATES",
                           changelog=False)
        # a drifted root VERSION is an authored residual too: no declared view targets VERSION, so
        # C-VERSION-FILE is not render-views' own and refuses with nothing written.
        root = built_store(temp, "version")
        (root / "VERSION").write_bytes(b"9.9.9\n")
        before = snapshot(root)
        with lease_stand_in():
            txn, why = run(root, [row], **rctx(root))
        check("render-views-drifted-version-residual-refused",
              txn is None and "C-VERSION-FILE" in (why or "") and snapshot(root) == before)
        # a context product root other than the transaction's own root refuses by directory identity, so
        # composed views can never land in a tree the store checks never saw.
        refuses_uncomposed("render-views-foreign-product-root-refused", "foreign",
                           "transaction's own product root", product_root=str(oracle))
        # the frozen-default-store identity is pinned leg by leg: a non-default pointer source and a store
        # root other than the product root each refuse even when every other leg matches (crafted
        # resolutions; the default fixture cannot produce either shape).
        root = built_store(temp, "identity")
        genuine = store.resolve_store(root)

        def res_variant(status=genuine.status, **changes):
            fields = dict(store_root=genuine.store_root, machine_dir=genuine.machine_dir,
                          machine_rel=genuine.machine_rel, pointer_source=genuine.pointer_source,
                          target=genuine.target, product_root=genuine.product_root)
            fields.update(changes)
            return store.Resolution(status, genuine.detail, **fields)

        for label, fake in (("render-views-pointer-source-refused", res_variant(pointer_source="committed")),
                            ("render-views-foreign-store-root-refused", res_variant(store_root=oracle)),
                            ("render-views-unresolved-status-refused",
                             res_variant(status=store.CANNOT_EVALUATE))):
            before = snapshot(root)
            with lease_stand_in(), mock.patch.object(store, "resolve_store", return_value=fake):
                txn, why = run(root, [row], **rctx(root))
            check(label, txn is None and "frozen default store" in (why or "") and snapshot(root) == before)

        bare = Path(temp).resolve() / "bare"
        bare.mkdir()
        txn, why = run(bare, [row], **rctx(bare))
        check("render-views-not-adopted-refused", txn is None and "must resolve" in (why or "")
              and snapshot(bare) == dict())
        # reconcile-first: a held adoption journal lock refuses the transaction before the handler runs
        # (never seized) -- the shell's own gate now guards render-views exactly as the other finish ops.
        root = built_store(temp, "locked")
        before = snapshot(root)
        root_fd = _open_product_root(root)
        try:
            _journal.ensure_journal_dirs(root_fd, JOURNAL_REL)
        finally:
            store._close_fd_exc_safe(root_fd)
        _journal.acquire_lock(_journal_root(root), "opf-adopt-selftest-peer")
        try:
            with lease_stand_in():
                txn, why = run(root, [row], **rctx(root))
            check("render-views-held-journal-lock-refused",
                  txn is None and "never seized" in (why or "") and snapshot(root) == before)
        finally:
            _journal.release_lock(_journal_root(root))
    check("render-views-without-transaction-refused", refused(dispatch(row), "no ApplyOps"))


def main():
    args = sys.argv[1:]
    if "--self-test" in args or "--selftest" in args:
        return self_test()
    print("usage: _opf_adopt_apply.py --self-test (a library module; the adoption verb is `opf adopt`)",
          file=sys.stderr)
    return 2


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-test"]:
        sys.exit(self_test())
    sys.exit(main())
