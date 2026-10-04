#!/usr/bin/env python3
"""OPF adoption apply engine, slices 1 and 3: the apply SHELL and init-store, its one executable op (spec 1.3.0).

This slice (the clean-start adoption track's first apply unit) supplies the engine SKELETON only, shaped
by spec 1.3.0 sections 4.2, 5.7 and 14: run identity; the evidence-bundle, archive and Move homes, all
derived from the `_opf_store` homes constructors (never re-spelled here); the `opf.evidence.inventory/v1`
inventory DERIVED from the run's own transaction op list and published in that SAME transaction as the
retained bytes it lists (spec 4.2), a base `inventory.toml` then one `inventory-<phase>.toml` per later
phase, never rewritten; the homes-1 bundle verification the completion checks carry themselves while
C-EVIDENCE-ENUM is inactive (spec 14.1), which RE-READS the inventories and payload digests from disk;
the preserve-first composition of spec 14.2; live re-observation of every operand; one journaled
transaction per (run, phase), reconcile-first; and a dispatch table keyed by the closed eleven-op
ADOPT_OPS vocabulary in which every op but init-store returns a refusing not-yet-executable verdict. Slice 3
lands init-store, composed over the coupled-init substrate (_init_store). No other operation
executes: the file ops, trust verification, approval capture, hook activation,
rendering, receipt writing, the completion checks, retirement, and the MUTATING CLI subcommands (approve,
apply, complete, reconcile) remain later slices; the read-only `opf adopt` subcommands plan and status
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
multiple machine stores, an undiscoverable root) refuses too (spec 14.2: an unreadable declaration or detected
input fails closed). Only the two first-adoption states adoption exists for are admitted: NOT-ADOPTED, and a
present `.working/` at the DEFAULT location carrying no valid manifest, re-proved by a fresh discovery. A
first adoption has no store and so no lease home; the pre-store single-writer control is THIS journal's
lock, which init-store holds across the whole substrate run (one lock shared by both writers; the
substrate's operation mutex is taken inside it), inside its own durable-intent transaction whose
journal-only recovery is the op's declared reversal (see _init_store). The coupled-init substrate admits
the plan-enumerated control-area homes and frozen non-occupying sources beneath `.working/` through its
admitted-inventory seam, re-verified under its mutex and recorded in its immutable plan; plain `opf init`
and the bare substrate stay unweakened (an existing `.working/` still refuses a plain fresh init exactly
as before, pinned by the substrate's own self-test). Disclosed residuals of these slices, none of them a
relaxation: init.toml's member digest is never plan-bindable (it embeds the operation id, time and HEAD),
so the row binds its PATH with the explicit all-zero unbound marker and the live bytes are verified
against the operation's validated recorded plan instead; the context plan's approval binding (plan_digest
against the captured approval) and full plan-schema validation are the stage driver's, not init-store's,
which validates exactly the fields it consumes; bundle MEMBERSHIP (an off-inventory file inside a bundle)
is not reconciled by the shell's verifier, only listed payloads — init-store's admitted-inventory
derivation DOES refuse an unlisted file beneath the bundle home; containment registration of the archive,
Move and evidence homes on homes 1 is part of the 1.3.0 activation, not this slice; investigation does
not read this journal (the planner's ancestry disclosure lists durable OPF history outside .working, and
`.aiqt/adopt/journal` is one more such home), so the stage driver's plan stage must refuse over a
non-clean adoption journal through journal_clean_or_refuse; the shipped `retire-file` vocabulary row is a
single `remove`, while spec 1.3.0 preserves the retirement preimage at apply and removes only after the
green check, a vocabulary split for the op slices; the shell's interruption is exercised in-process
through the journal's kill-point seam, while init-store's SIGKILLs the whole engine in a child dispatch
mid-publication, under the held lock and open transaction.

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
import _opf_init_operation as init_op  # noqa: E402
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
    receipt to claim, so it deliberately takes the STRICTER exactly-one-inventory rule, and the receipt
    slice may relax it with its own vectors."""
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
    lock. An open init-store transaction is preverified first (_init_store_reversal_preverify): its
    rollback removes only the operation's own publication, so foreign content at a member path
    refuses the whole reconciliation fail-closed, preserved. A lock held by a possibly-live owner
    refuses (never seized); a confirmed-dead owner's lock is
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
                # Before ANY rollback can run (the stale-lock break below reconciles every
                # transaction itself), prove each open init-store transaction's reversal removes
                # only the operation's own publication: a foreign file at a member path is
                # preserved and the whole reconciliation refuses fail-closed
                # (_init_store_reversal_preverify).
                for t in txns:
                    if t.name.endswith("." + INIT_STORE_PHASE) and t.name in opened:
                        _init_store_reversal_preverify(root_fd, product_root, jr_fd, t)
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
            # THE STALE-PREFLIGHT GUARD (QA round 2): the refusals above ran BEFORE the lock, and
            # the other writer (an init-store dispatch) takes THIS SAME lock, so its whole
            # transaction may have opened, published a store and completed between those checks
            # and this acquisition (or an interrupted one may have appeared). Re-prove every
            # consequential prerequisite HERE, under the held lock, before anything composes: no
            # open transaction appeared, the store posture is still a first-adoption state, this
            # run and phase still have no transaction, and a phase's committed base still
            # verifies. The pre-lock copies stay as cheap early refusals only; nothing composed
            # before the lock authorizes anything after it.
            _owner, opened = journal_state(root_fd, journal_root)
            if opened:
                raise AdoptApplyError("interrupted adoption transaction(s) {} appeared before the "
                                      "lock was acquired and must be reconciled first (run "
                                      "reconcile()); nothing written (fail-closed)".format(
                                          ", ".join(opened)))
            _store_posture_or_refuse(product_root)
            try:
                prior = _journal._lstat_contained(root_fd, JOURNAL_REL + "/" + txn)
            except (_journal.JournalError, OSError) as exc:
                raise AdoptApplyError("cannot inspect the adoption journal ({}); "
                                      "fail-closed".format(exc))
            if prior is not None:
                raise AdoptApplyError("run {} grew its transaction {!r} before the lock was "
                                      "acquired: one run takes one transaction per phase, and "
                                      "changing approved work takes a fresh plan with its own "
                                      "run id (spec 14.1); nothing written "
                                      "(fail-closed)".format(run_id, txn))
            if phase is not None:
                _committed_base_or_refuse(root_fd, journal_root, run_id, phase)
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


# --- init-store: the coupled-init substrate inside the adoption transaction (slice 3) ---------------------

# The phase suffix of the one init-store transaction an adoption run may take in this journal.
INIT_STORE_PHASE = "initstore"
# The EXPLICIT unbound-digest marker: a member whose bytes cannot be bound at plan time (init.toml,
# which embeds the operation id, time and HEAD) must carry exactly this value, so a digest that only
# LOOKS bound is refused; any plan-bindable member carrying it is refused the other way.
UNBOUND_DIGEST = "sha256:" + "0" * 64


def _init_store_scaffold(first_adoption, changelog_created):
    """(manifest path, {path: digest or None}) of the COMPLETE creation set of the coupled-init
    substrate's scaffold: a sha256:<hex> digest for every payload the D1 builders fix at plan time
    (the machine ledgers, with a zero-seeded counters only on a first adoption; the per-type
    indexes; the root pointer; and CHANGELOG.md when the plan creates it), and None where the bytes
    are not knowable HERE before the operation runs: init.toml, which embeds the operation id, time
    and HEAD and so is never plan-bindable (the row carries the explicit UNBOUND_DIGEST marker and
    the live bytes are verified against the operation's validated recorded plan instead); and each
    declared initial view and a re-adoption's seeded counters, which ARE plan-bindable (the planner
    derives them from the pinned sources and snapshot), carried in the row and verified after
    publication. The view destinations come from the default manifest's own declared views, and the
    machine manifest digest pins the default manifest (baseline record types only, every module
    tier off)."""
    import _opf_check
    import _opf_init
    manifest = _opf_init.build_manifest()
    documents = {
        store.MANIFEST_NAME: manifest,
        "version.toml": _opf_init.build_version(),
        "worklog.toml": _opf_init.build_worklog(),
    }
    if first_adoption:
        documents["counters.toml"] = _opf_init.build_counters()
    for tname in _opf_init.INDEX_TYPES:
        documents[tname + _opf_check.INDEX_SUFFIX] = _opf_init.build_index(tname)
    machine = "{}/{}".format(store.WORKING_DIRNAME, store.DEFAULT_MACHINE_SUBDIR)
    scaffold = {"{}/{}".format(machine, n): "sha256:" + _sha256(t.encode("utf-8"))
                for n, t in documents.items()}
    if not first_adoption:
        scaffold[init_op.COUNTERS_RELPATH] = None
    scaffold[store.POINTER_REL] = "sha256:" + _sha256(
        emit_checked({"store": {"target": "dir:."}}).encode("utf-8"))
    if changelog_created:
        scaffold[init_op.CHANGELOG_RELPATH] = "sha256:" + _sha256(init_op._CHANGELOG_PAYLOAD)
    scaffold[init_op.PROVENANCE_RELPATH] = None
    for view in tomllib.loads(manifest)["views"].values():
        scaffold[view["target"]] = None
    return "{}/{}".format(machine, store.MANIFEST_NAME), scaffold


def _init_store_members_or_refuse(op_row, scaffold, manifest_rel):
    """{path: digest} of the row's members, refused unless they are EXACTLY the scaffold's complete
    creation set ("the scaffold writes exactly its members": a missing member is refused like a
    stray one) with the manifest digest-bound to the default manifest, every plan-bindable digest
    bound (the builders' exact bytes for the fixed payloads, checked HERE before anything is
    written), and the one plan-unbindable member (init.toml) carrying the explicit UNBOUND_DIGEST
    marker, never a value that only looks bound."""
    members = {m["path"]: m["digest"] for m in op_row["members"]}
    missing = sorted(set(scaffold) - set(members))
    stray = sorted(set(members) - set(scaffold))
    if missing or stray:
        raise AdoptApplyError("the row's members are not exactly the scaffold's creations (missing: "
                              "{}; outside it: {}); the scaffold writes exactly its members, the "
                              "frozen machine store's manifest ({}) among them (nothing "
                              "written)".format(missing, stray, manifest_rel))
    for path in sorted(scaffold):
        digest = scaffold[path]
        if digest is None:
            if path == init_op.PROVENANCE_RELPATH:
                if members[path] != UNBOUND_DIGEST:
                    raise AdoptApplyError("member {} embeds the operation id, time and HEAD, so its "
                                          "digest cannot be bound at plan time: the row carries the "
                                          "explicit all-zero unbound marker, never a value that only "
                                          "looks bound (nothing written)".format(path))
            elif members[path] == UNBOUND_DIGEST:
                raise AdoptApplyError("member {} carries the unbound marker and its digest is "
                                      "plan-bindable (the planner derives it from the pinned sources); "
                                      "bind it (nothing written)".format(path))
        elif members[path] != digest:
            raise AdoptApplyError("member {} binds digest {} where the substrate's builders fix {}; "
                                  "nothing written".format(path, members[path], digest))
    return members


def _init_store_external_destinations():
    """The default manifest's declared view and deliverable destinations OUTSIDE `.working/` (today
    the CHANGELOG.md deliverable; every declared view is store-scope). Derived from the manifest
    itself so the disposition preflight can never drift from what init publishes around."""
    import _opf_init
    doc = tomllib.loads(_opf_init.build_manifest())
    targets = {d.get("target") for d in doc.get("deliverables", {}).values() if isinstance(d, dict)}
    targets |= {v.get("target") for v in doc.get("views", {}).values() if isinstance(v, dict)}
    return sorted(t for t in targets
                  if isinstance(t, str) and not _within(t, store.WORKING_DIRNAME))


def _init_store_sources_or_refuse(plan):
    """Every consumed sources-row field (path, digest, disposition, occupying, preservation)
    validated fail-closed through the planner's own _validate_plan_sources BEFORE either the
    disposition preflight or the admission consumes a row -- independent of whether `.working`
    exists (codex round 3, finding 3): a malformed row refuses with nothing written, never
    reads as non-occupying, never freezes a source, never authorizes a copy."""
    findings = []
    short = schema._validate_plan_sources(plan["sources"], plan["run_id"], 1, findings)
    if short is not None or findings:
        raise AdoptApplyError("the context plan's sources rows do not validate ({}); every "
                              "consumed sources-row field (path, digest, disposition, occupying, "
                              "preservation) is validated fail-closed before it justifies any "
                              "disposition or admission (nothing written)".format(
                                  "; ".join(findings) if findings else "; ".join(short.findings)))


def _init_store_dispositions_or_refuse(root_fd, plan):
    """Spec 14: every pre-existing file at a declared view or deliverable destination outside
    `.working/` MUST carry a plan disposition, its live bytes digest-bound, before init-store; an
    undispositioned or drifted one refuses with nothing written. (The substrate itself preserves a
    pre-existing CHANGELOG.md byte-exact in its plan's E set and refuses existing store
    pointers.) The rows are validated fail-closed FIRST (_init_store_sources_or_refuse), so a
    malformed row refuses here even when `.working` is absent and the admission never runs."""
    _init_store_sources_or_refuse(plan)
    rows = {row.get("path"): row for row in plan["sources"] if isinstance(row, dict)}
    for target in _init_store_external_destinations():
        live = observe_live(root_fd, target)
        if live["kind"] == "absent":
            continue
        row = rows.get(target)
        if not isinstance(row, dict) or row.get("disposition") not in schema.DISPOSITIONS:
            raise AdoptApplyError("undispositioned declared destination {}: every pre-existing file "
                                  "at a declared deliverable or view destination outside .working "
                                  "carries a plan disposition before init-store (spec 14; nothing "
                                  "written)".format(target))
        if row.get("digest") != "sha256:" + live["sha256"]:
            raise AdoptApplyError("declared destination {} drifted from its plan disposition's "
                                  "digest; nothing written".format(target))


def _init_store_bundle_paths(root_fd, product_root, run_id, model):
    """{path: digest} of this run's evidence-bundle files admissible beneath `.working/`: the
    bundle-root inventories plus EXACTLY the payloads they list, and nothing else — and only after
    verify_bundle has re-proved every listed payload on disk. Empty when nothing lies beneath the
    bundle home; an invalid or unevaluable bundle refuses (never partially admitted)."""
    import _opf_check
    home = evidence_home_rel(run_id)
    if not any(e["kind"] == "file" and _within(e["path"], home) for e in model["entries"]):
        return {}
    checked = verify_bundle(product_root, run_id)
    if checked.status != store.VALID:
        raise AdoptApplyError("the evidence bundle of {} does not verify ({}); fail-closed".format(
            run_id, "; ".join(checked.findings)))
    out = {}
    for entry in model["entries"]:
        if entry["kind"] != "file" or not _within(entry["path"], home):
            continue
        name = entry["path"][len(home) + 1:]
        if "/" in name or not store.is_evidence_inventory_name(name):
            continue
        out[entry["path"]] = entry["digest"]
        _fst, data = _read_live(root_fd, entry["path"])
        try:
            rows = _opf_check._evidence_rows(home, KIND, run_id,
                                             tomllib.loads(data.decode("utf-8")))
        except (ValueError, TypeError, KeyError, UnicodeDecodeError,
                tomllib.TOMLDecodeError) as exc:
            raise AdoptApplyError("the evidence inventory {} cannot be read ({}); "
                                  "fail-closed".format(entry["path"], exc))
        for row in rows:
            # Listed paths are product-root-relative (a bundle may list payloads outside its home,
            # e.g. archive copies); only those beneath .working can appear in the inventory at all.
            out[row["path"]] = "sha256:" + row["sha256"]
    return out


def _init_store_archival_committed(jr_fd, journal_root, source_path, digest, preservation):
    """Whether a COMMITTED adoption transaction of this journal archived the occupying file at
    `source_path` preserve-first (spec 14.2): its durable intent creates the preservation copy
    at `preservation` with this digest AND removes the source pinned to the same digest. A
    sources row alone proves PLANNED work; only this committed archival evidence (plus the
    live, digest-matched preservation copy the caller requires) can admit the emptied machine
    directory. No journal descriptor reads as no evidence, the fail-closed direction."""
    if jr_fd is None or journal_root is None:
        return False
    hexd = _plan_hex(digest)
    try:
        for txn_dir in _journal._journal_txn_dirs(jr_fd, journal_root):
            if _journal.classify_state(jr_fd, txn_dir) != "complete":
                continue
            frames, _torn, _good = _journal.read_frames(jr_fd, txn_dir)
            intent = _journal._first(frames, _journal.F_INTENT)
            ops = intent.get("ops") if isinstance(intent, dict) else None
            if not isinstance(ops, list):
                continue
            preserved = any(isinstance(o, dict) and o.get("op") == "create"
                            and o.get("path") == preservation
                            and (o.get("poststate") or {}).get("content-sha256") == hexd
                            for o in ops)
            removed = any(isinstance(o, dict) and o.get("op") == "remove"
                          and o.get("path") == source_path
                          and (o.get("source-poststate") or {}).get("sha256") == hexd
                          for o in ops)
            if preserved and removed:
                return True
    except (_journal.JournalError, OSError) as exc:
        raise AdoptApplyError("cannot search the adoption journal for the occupying file's "
                              "archival transaction ({}); fail-closed".format(exc))
    return False


def _init_store_admitted(root_fd, product_root, plan, jr_fd=None, journal_root=None):
    """The blind-init guard and the substrate seam's admitted inventory (spec 14, 14.2): observe the
    live `.working` tree and justify EVERY file in it from the approved plan, else refuse with
    nothing written. Justified, and so admitted — each entry's path, mode, size and digest handed to
    the substrate, which RE-VERIFIES them under its own operation mutex and records them in its
    immutable plan: a non-occupying plan source frozen in place whose live bytes match the row's
    digest; a preservation copy at a row's recorded control-area preservation destination with that
    row's digest; and this run's evidence bundle (its inventories and exactly the payloads they
    list), only after verify_bundle re-proves every listed payload. An undispositioned foreign file,
    a drifted one, an unlisted control-area file, or a directory no admitted file lies beneath
    refuses -- EXCEPT the machine home itself when COMPLETED ARCHIVAL EVIDENCE proves the shell
    archived an occupying machine-store file out of it (spec 14.2: "After the archival the
    destination is an ordinary managed path"): the occupying row's preservation copy must lie
    LIVE at its recorded destination, digest-matched, and the archival transaction must be
    COMMITTED in this adoption journal (_init_store_archival_committed) -- a validated row
    alone proves planned work, not completed archival. Only then is the emptied, pre-existing
    directory admitted as a pre-existing planned directory, its mode pre-checked HERE against
    the planned one (before the run's intent opens, so a wrong-mode tree never burns the run
    id), which the substrate re-verifies and preserves. Every consumed
    sources-row field (path, digest, disposition, occupying, preservation) is validated
    fail-closed through the planner's own _validate_plan_sources BEFORE it justifies anything: a
    malformed row refuses, never reads as non-occupying or authorizes a copy. Returns
    (admitted file entries, admitted pre-existing planned directories); an absent `.working`
    returns ((), ())."""
    try:
        model, present = init_op.observe_inventory(root_fd)
    except init_op.InitOperationError as exc:
        raise AdoptApplyError("the .working tree cannot be observed ({}); fail-closed".format(exc))
    if not present:
        return (), ()
    _init_store_sources_or_refuse(plan)
    machine = "{}/{}".format(store.WORKING_DIRNAME, store.DEFAULT_MACHINE_SUBDIR)
    frozen, copies, occ_rows = {}, {}, []
    for row in plan["sources"]:
        path, digest = row["path"], row["digest"]
        if row["occupying"] is True and _within(path, machine):
            occ_rows.append(row)
        if row["occupying"] is False and row["disposition"] in schema.DISPOSITIONS \
                and _within(path, store.WORKING_DIRNAME) and not _within(path, machine):
            frozen[path] = digest
        pres = row.get("preservation")
        if row["disposition"] != "keep" and isinstance(pres, str) \
                and any(_within(pres, home) for home in _CONTROL_HOMES):
            copies[pres] = digest
    bundle = _init_store_bundle_paths(root_fd, product_root, plan["run_id"], model)
    admitted, foreign = [], []
    for entry in model["entries"]:
        if entry["kind"] != "file":
            continue
        path = entry["path"]
        want = frozen.get(path) or copies.get(path) or bundle.get(path)
        if want is None or want != entry["digest"]:
            foreign.append(path)
            continue
        admitted.append({"path": path, "mode": entry["mode"], "size": entry["size"],
                         "digest": entry["digest"]})
    if foreign:
        raise AdoptApplyError("undispositioned foreign .working content {}: every foreign .working "
                              "file carries a plan disposition first (a source frozen in place, a "
                              "recorded preservation copy, or this run's verified evidence bundle, "
                              "live bytes digest-matched), never a blind init over populated content "
                              "(spec 14; nothing written)".format(", ".join(sorted(foreign))))
    dirs = {e["path"] for e in model["entries"] if e["kind"] == "directory"}
    stray = sorted(dirs - init_op._admitted_dirs([e["path"] for e in admitted]))
    admitted_dirs = ()
    if machine in stray and occ_rows:
        # Spec 14.2: "After the archival the destination is an ordinary managed path: apply
        # initializes the machine file or renders the view immediately." The archival removed the
        # occupying FILE; its pre-existing parent directory survives (the shell never removes a
        # pre-existing directory) and holds no file (one would have refused as foreign above).
        # The admission is derived from VERIFIED ARCHIVAL EVIDENCE, never the planned row alone:
        # every occupying machine row's preservation copy must lie LIVE at its recorded
        # destination, digest-matched (it was admitted above), and a COMMITTED adoption
        # transaction of this journal must record that archival preserve-first. Only then is the
        # directory admitted as a pre-existing planned directory: the substrate re-verifies it
        # under its mutex (a plain directory, owned, at exactly its planned mode) and PRESERVES
        # it through the run and its reversal.
        admitted_paths = dict((e["path"], e["digest"]) for e in admitted)
        for orow in occ_rows:
            pres = orow.get("preservation")
            if not (isinstance(pres, str) and admitted_paths.get(pres) == orow["digest"]
                    and _init_store_archival_committed(jr_fd, journal_root, orow["path"],
                                                       orow["digest"], pres)):
                raise AdoptApplyError("the pre-existing machine directory {} is admitted only "
                                      "on completed archival evidence: the occupying row's "
                                      "preservation copy live and digest-matched at its "
                                      "recorded destination AND the archival transaction "
                                      "COMMITTED in this adoption journal; a sources row alone "
                                      "proves planned work, not completed archival (spec 14.2; "
                                      "nothing written)".format(machine))
        stray.remove(machine)
        admitted_dirs = (store.WORKING_DIRNAME, machine)
        # The admitted-directory MODE is pre-checked HERE, before the run's intent opens, so a
        # wrong-mode pre-existing tree (a umask-002 adopter) refuses with nothing written and
        # the run id never burnt; the substrate re-proves the same fact under its mutex.
        dmode = dict((e["path"], e["mode"]) for e in model["entries"]
                     if e["kind"] == "directory")
        try:
            wst = os.stat(store.WORKING_DIRNAME, dir_fd=root_fd, follow_symlinks=False)
            dmode[store.WORKING_DIRNAME] = stat.S_IMODE(wst.st_mode) & 0o777
        except OSError as exc:
            raise AdoptApplyError("cannot stat .working ({}); fail-closed".format(exc))
        for dpath in admitted_dirs:
            if dpath == store.WORKING_DIRNAME and admitted:
                continue   # with admitted files the substrate records the observed mode instead
            if dmode.get(dpath) != init_op.DIR_MODE:
                raise AdoptApplyError("admitted pre-existing directory {} holds mode {}, not "
                                      "the planned {}; it is preserved as found, never "
                                      "rewritten, and refused BEFORE the run's intent opens "
                                      "(nothing written)".format(
                                          dpath, "%o" % dmode.get(dpath, 0),
                                          "%o" % init_op.DIR_MODE))
    if stray:
        raise AdoptApplyError("foreign .working directory(ies) {} hold no admitted file; a blind "
                              "init over them is refused (spec 14; nothing written)".format(stray))
    if not admitted and not admitted_dirs:
        raise AdoptApplyError("a .working directory exists holding nothing the plan admits; a blind "
                              "init over it is refused (spec 14; nothing written)")
    return tuple(admitted), admitted_dirs


def _init_store_record(product_root):
    """The substrate's recorded history: None when it records nothing, the ONE recorded
    operation's report otherwise, WHATEVER its status -- intact (partial or completed), or the
    unevaluable debris an interrupted record discard left (its directory still names the
    operation id, the binding the caller uses: _init_store_orphan_or_refuse either binds it to
    this journal's own rolled-back init-store intent and finishes the discard, or refuses).
    More than one operation refuses: init-store never picks among histories."""
    import _opf_init_substrate
    try:
        survey = _opf_init_substrate.classify_init_operations(str(product_root))
    except (_opf_init_substrate.InitSubstrateError, OSError) as exc:
        raise AdoptApplyError("the coupled-init substrate cannot be classified ({}); "
                              "fail-closed".format(exc))
    ops = survey.operations if survey.status == _opf_init_substrate.OPERATIONS else ()
    if not ops:
        return None
    if len(ops) != 1:
        raise AdoptApplyError("the coupled-init substrate already records init history ({} "
                              "operation(s), first {}); init-store never adopts over recorded "
                              "history or picks among operations (fail-closed)".format(
                                  len(ops), ops[0].status))
    return ops[0]


def _init_store_orphan_or_refuse(product_root, root_fd, jr_fd, journal_root, record, recover):
    """ONE recorded substrate operation found before a fresh init-store: admissible ONLY as the
    reversed remnant of THIS journal's own init-store work — a ROLLED-BACK init-store transaction
    must name the operation id in its durable intent (the binding the intent exists for; the
    record's DIRECTORY name carries the id even when an interrupted record discard left it
    unevaluable) and every creation that intent recorded must be absent live (its rollback
    restored them from the journal's preimages). Then, under the EXPLICIT context recover flag,
    its leftovers are scrubbed and the record discarded under the substrate's own mutex (a record
    whose effects were reversed authorizes nothing), so a fresh plan can run; the scrub is
    restartable at every point (_init_store_scrub discards the record LAST, the recovery
    discriminator preserved until the filesystem cleanup is durably complete). Anything else — an
    initialized store's completed operation, a partial or unevaluable record no intent of this
    journal names — refuses fail-closed: recorded init history is never adopted, resumed across
    runs, or silently chosen."""
    intent = None
    try:
        for txn_dir in _journal._journal_txn_dirs(jr_fd, journal_root):
            if not txn_dir.name.endswith("." + INIT_STORE_PHASE):
                continue
            if _journal.classify_state(jr_fd, txn_dir) != "rolled-back":
                continue
            frames, _torn, _good = _journal.read_frames(jr_fd, txn_dir)
            doc = _journal._first(frames, _journal.F_INTENT)
            header = doc.get("header") if isinstance(doc, dict) else None
            if isinstance(header, dict) and header.get("init_operation_id") == record.op_id:
                intent = doc
                break
    except (_journal.JournalError, OSError) as exc:
        raise AdoptApplyError("cannot search the adoption journal for the recorded operation's "
                              "init-store intent ({}); fail-closed".format(exc))
    if intent is None:
        raise AdoptApplyError("the coupled-init substrate already records init history (operation "
                              "{}) that no rolled-back init-store transaction of this adoption "
                              "journal names; it is never adopted, resumed across runs, or silently "
                              "chosen (fail-closed)".format(record.op_id))
    for op in intent.get("ops", []):
        try:
            present = _journal._lstat_contained(root_fd, op.get("path")) is not None
        except (_journal.JournalError, OSError) as exc:
            raise AdoptApplyError("cannot observe {} ({}); fail-closed".format(op.get("path"), exc))
        if present:
            raise AdoptApplyError("the rolled-back init-store transaction's effect {} is still "
                                  "present; its reversal did not restore the prestate "
                                  "(fail-closed)".format(op.get("path")))
    if not recover:
        raise AdoptApplyError("discarding the reversed operation {} record needs the EXPLICIT "
                              "context recover flag, never an implicit side effect".format(
                                  record.op_id))
    header = intent["header"]
    _init_store_scrub(product_root, root_fd, record.op_id,
                      members=[op.get("path") for op in intent.get("ops", [])
                               if isinstance(op, dict) and isinstance(op.get("path"), str)],
                      working_created=header.get("working_created") is True,
                      machine_created=header.get("machine_created") is not False,
                      recover=recover)


def _init_store_scrub(product_root, root_fd, operation_id, members, working_created,
                      machine_created, recover):
    """Clear a REVERSED init-store operation's remnants under the substrate's own mutex, so the
    tree returns to its prestate (NOT-ADOPTED, or the admitted content alone), in an order
    RESTARTABLE AT EVERY POINT from the durable adoption journal plus the still-recorded
    operation: FIRST the operation's payload staging leftovers beside each member destination (a
    kill between a member's staging write and its link, or between the link and the staging
    unlink, leaves one, its name embedding the operation id -- root-level destinations such as
    CHANGELOG.md and the store pointer included, so no leftover publication byte outlives the
    reversal), then the lease control file when the interruption left one, then the then-empty
    CREATED planned directories (`.working` and the machine home only when this journal's intent
    created them: an admitted pre-existing directory is preserved as found), and ONLY THEN the
    operation record (its journals and outcomes stay, preserved attempt evidence). Each affected
    parent directory is fsynced after the lease unlink and after every directory removal, so
    the cleanup is DURABLE before the record discard can be: a host crash can never leave the
    record deletion durable while a directory removal is not. The record is the recovery
    discriminator that lets the next dispatch find the reversed operation again, so it is
    discarded LAST, once every filesystem effect is durably gone; a kill inside the discard itself
    leaves a partial record whose directory still names the operation id, which the next dispatch
    re-binds to the rolled-back intent and finishes discarding. The journal's preimage rollback
    has already removed the member files; this clears only what that rollback cannot name."""
    import _opf_init_substrate
    import _opf_oplock
    machine = "{}/{}".format(store.WORKING_DIRNAME, store.DEFAULT_MACHINE_SUBDIR)
    try:
        holder = _opf_oplock.acquire_init_operation(str(product_root), init_op.OPERATION,
                                                    recover=recover)
    except _opf_oplock.OpLockError as exc:
        raise AdoptApplyError("cannot take the substrate operation mutex to scrub the reversed "
                              "operation ({}); fail-closed".format(exc))
    try:
        try:
            for path in sorted(members):
                parent, _sep, _name = path.rpartition("/")
                stage_rel = (parent + "/" if parent else "") + init_op.staging_name(
                    path, operation_id)
                st = _journal._lstat_contained(root_fd, stage_rel)
                if st is None:
                    continue
                if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
                    raise AdoptApplyError("the reversed operation's staging name {} is not a "
                                          "plain singly-linked regular file; it is preserved for "
                                          "inspection (fail-closed)".format(stage_rel))
                sfd, sname = _journal._open_parent(root_fd, stage_rel)
                try:
                    os.unlink(sname, dir_fd=sfd)
                    os.fsync(sfd)
                finally:
                    _journal._close_fd_quietly(sfd)
            st = _journal._lstat_contained(root_fd, init_op.LEASE_RELPATH)
            if st is not None and stat.S_ISREG(st.st_mode):
                lfd, lname = _journal._open_parent(root_fd, init_op.LEASE_RELPATH)
                try:
                    os.unlink(lname, dir_fd=lfd)
                    os.fsync(lfd)
                finally:
                    _journal._close_fd_quietly(lfd)
            created = ([machine] if machine_created else []) \
                + ([store.WORKING_DIRNAME] if working_created else [])
            for rel in created:
                st = _journal._lstat_contained(root_fd, rel)
                if st is None:
                    continue
                if not stat.S_ISDIR(st.st_mode):
                    raise AdoptApplyError("{} is not a directory at reversal; "
                                          "fail-closed".format(rel))
                dfd, dname = _journal._open_parent(root_fd, rel)
                try:
                    try:
                        os.rmdir(dname, dir_fd=dfd)
                    except OSError as exc:
                        raise AdoptApplyError("cannot remove the reversed directory {} ({}); it "
                                              "is preserved for inspection "
                                              "(fail-closed)".format(rel, exc))
                    os.fsync(dfd)
                finally:
                    _journal._close_fd_quietly(dfd)
        except (_journal.JournalError, OSError) as exc:
            raise AdoptApplyError("cannot scrub the reversed operation's control files ({}); "
                                  "fail-closed".format(exc))
        try:
            survey = _opf_init_substrate.classify_init_operations(str(product_root))
            ops = survey.operations if survey.status == _opf_init_substrate.OPERATIONS else ()
            if any(r.op_id == operation_id for r in ops):
                _swept, discarded = _opf_init_substrate.settle_operation(holder, operation_id)
                if not discarded:
                    _opf_init_substrate.discard_reversed_operation(holder, operation_id)
        except (_opf_init_substrate.InitSubstrateError, OSError) as exc:
            raise AdoptApplyError("cannot discard the reversed operation record {} ({}); "
                                  "fail-closed".format(operation_id, exc))
    finally:
        if not holder._released and not holder._spent:
            try:
                _opf_oplock.release_init_holder(holder)
            except _opf_oplock.OpLockError:
                pass   # a leftover mutex refuses the next run into explicit recovery, never seized


def _init_store_intent_ops(members):
    """The durable-intent ops of the one init-store transaction: one `create` per member, each
    carrying its poststate digest where the plan binds one. init.toml is never plan-bindable, so the
    journal's forward election can NEVER fire on an open init-store transaction: recovery from the
    journal alone always rolls BACK to the preserved prestate (every created member removed, after
    _init_store_reversal_preverify proves each live member is the operation's own publication),
    which is the op's declared reversal — "remove the scaffolded store, restoring NOT-ADOPTED"
    (spec 14.2). The planned directories, the payload staging names and the transient lease are
    not ops (a preserving rollback cannot name them); _init_store_scrub clears them under the
    substrate mutex, restartably, and only then discards the operation record."""
    ops = []
    for path in sorted(members):
        op = {"op": "create", "path": path, "mode": FILE_MODE}
        if members[path] != UNBOUND_DIGEST:
            op["poststate"] = {"kind": "file", "mode": FILE_MODE,
                               "content-sha256": members[path][len("sha256:"):]}
        ops.append(op)
    return ops


def _init_store_txn_clear(jr_fd, txn):
    """Clear the frame-less debris a crash left in this run's OWN init-store transaction
    directory between its mkdir and its INTENT (the caller classified it nothing-opened, so no
    INTENT was ever durable and nothing beyond the journal directories was written): remove
    frames.log (empty or torn) and any captured preimage payloads, so the run's retry opens the
    transaction as if fresh. Anything else in the directory refuses fail-closed (preserved for
    inspection), and a directory holding an INTENT never reaches here (the caller refuses it
    first)."""
    try:
        tfd = os.open(txn, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=jr_fd)
    except OSError as exc:
        raise AdoptApplyError("cannot reopen this run's frame-less init-store transaction "
                              "directory ({}); fail-closed".format(exc))
    try:
        for entry in sorted(os.listdir(tfd)):
            st = os.stat(entry, dir_fd=tfd, follow_symlinks=False)
            if entry == "frames.log" and stat.S_ISREG(st.st_mode):
                os.unlink(entry, dir_fd=tfd)
            elif entry == "preimages" and not stat.S_ISLNK(st.st_mode) \
                    and stat.S_ISDIR(st.st_mode):
                pfd = os.open(entry, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=tfd)
                try:
                    for name in sorted(os.listdir(pfd)):
                        pst = os.stat(name, dir_fd=pfd, follow_symlinks=False)
                        if not stat.S_ISREG(pst.st_mode):
                            raise AdoptApplyError("this run's frame-less init-store transaction "
                                                  "holds a non-regular preimage entry {!r}; it "
                                                  "is preserved for inspection "
                                                  "(fail-closed)".format(name))
                        os.unlink(name, dir_fd=pfd)
                    os.fsync(pfd)
                finally:
                    _journal._close_fd_quietly(pfd)
            else:
                raise AdoptApplyError("this run's frame-less init-store transaction directory "
                                      "holds {!r}; it is preserved for inspection "
                                      "(fail-closed)".format(entry))
        os.fsync(tfd)
    except OSError as exc:
        raise AdoptApplyError("cannot clear this run's frame-less init-store transaction "
                              "directory ({}); fail-closed".format(exc))
    finally:
        _journal._close_fd_quietly(tfd)


def _init_store_txn_begin(root_fd, jr_fd, journal_root, txn, header, ops):
    """Open the init-store transaction and make its reversal durable BEFORE the substrate writes
    (the 9.3 framing run_transaction uses): the transaction directory and its frames.log, the
    captured preimages — every planned creation proved ABSENT here, before anything is written —
    then the INTENT frame binding the run, the adoption kind, the ancestral seed, the minted
    operation id, the admitted inventory and every member digest. The APPLY is the coupled-init
    substrate's own journaled publication, never apply_ops. The frame budget is bounded by
    construction: the member roster and the admitted inventory are both capped far below the
    journal's read cap."""
    txn_dir = journal_root / txn
    try:
        try:
            os.mkdir(txn, 0o777, dir_fd=jr_fd)
        except FileExistsError:
            # A crash between a prior attempt's mkdir HERE and its INTENT left a frame-less
            # transaction directory (the caller classified it nothing-opened: no INTENT was ever
            # durable, so nothing beyond the journal directories was written). The run id is not
            # burnt by its own pre-INTENT crash: clear the crash's partial frames.log and captured
            # preimage payloads so the exclusive creates below cannot trip on debris, then open
            # the transaction as if fresh.
            _init_store_txn_clear(jr_fd, txn)
        os.fsync(jr_fd)
        _journal._create_frames_excl(jr_fd, txn_dir)
        _journal.capture_preimages(jr_fd, txn_dir, root_fd, ops)
        _journal.publish(jr_fd, txn_dir, _journal.F_INTENT,
                         {"txn": txn, "header": header, "ops": ops})
    except (_journal.JournalError, OSError) as exc:
        raise AdoptApplyError("the init-store transaction was refused before it opened ({}); "
                              "nothing written beyond the journal directories "
                              "(fail-closed)".format(exc))


def _init_store_view_digests(product_root, operation_id):
    """Best-effort READ-ONLY load of the operation's own views-group journal INTENT: {path:
    bare-hex sha256} of the views the operation itself planned to publish, durable BEFORE the
    first view write, so the reversal can recognize the operation's own view bytes (a view's
    plan-time digest lives in the row, and the row's binding may be exactly what the
    postcondition refuted). Returns {} when the substrate, journal or intent is absent or
    unreadable: then only the operation's own plan-bound bytes are recognized, the fail-closed
    direction (a live view the reversal cannot attribute refuses, preserved)."""
    import _opf_init_substrate
    out = {}
    control_fd = home_fd = jr_fd = None
    try:
        try:
            control_fd, _desc = _opf_init_substrate._open_init_control_root(str(product_root))
        except _opf_init_substrate.InitSubstrateError:
            return out
        if control_fd is None:
            return out
        home_fd = os.open(_opf_init_substrate.SUBSTRATE_DIRNAME,
                          os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=control_fd)
        jr_fd = os.open(_opf_init_substrate.JOURNALS_DIRNAME,
                        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=home_fd)
        frames, _torn, _good = _journal.read_frames(
            jr_fd, Path(init_op._txn_name(operation_id, "views")))
        intent = _journal._first(frames, _journal.F_INTENT)
        if isinstance(intent, dict) and intent.get("operation_id") == operation_id:
            for eff in intent.get("effects", []):
                if isinstance(eff, dict) and isinstance(eff.get("path"), str) \
                        and isinstance(eff.get("digest"), str) \
                        and eff["digest"].startswith("sha256:"):
                    out[eff["path"]] = eff["digest"][len("sha256:"):]
    except (_journal.JournalError, OSError):
        return {}
    finally:
        for fd in (jr_fd, home_fd, control_fd):
            if fd is not None:
                _journal._close_fd_quietly(fd)
    return out


def _init_store_recorded_plan(product_root, op_id):
    """The operation's OWN recorded plan: ops/<op_id>/plan.json read no-follow beneath the
    substrate's control root and validated through the substrate's own plan validator for that
    operation id, REGARDLESS of the record's phase-record status (a kill inside a phase-record
    publication leaves an exact staging-name leftover that classifies the whole record
    CANNOT-EVALUATE without touching the plan record's integrity). None when the substrate, the
    record or the plan is absent or invalid: the fail-closed direction (a live member with no
    attributable plan evidence is preserved, never removed)."""
    import _opf_init_substrate
    if not isinstance(op_id, str) or not _opf_init_substrate._OP_ID_RE.match(op_id):
        return None
    control_fd = home_fd = ops_fd = op_fd = None
    try:
        try:
            control_fd, _desc = _opf_init_substrate._open_init_control_root(str(product_root))
        except _opf_init_substrate.InitSubstrateError:
            return None
        if control_fd is None:
            return None
        home_fd = os.open(_opf_init_substrate.SUBSTRATE_DIRNAME,
                          os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=control_fd)
        ops_fd = os.open(_opf_init_substrate.OPS_DIRNAME,
                         os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=home_fd)
        op_fd, _ident = _opf_init_substrate._open_existing_op_dir(ops_fd, op_id)
        _doc, raw = _opf_init_substrate._read_json_record(
            op_fd, _opf_init_substrate.PLAN_NAME, "plan record",
            _opf_init_substrate.MAX_PLAN_BYTES)
        return _opf_init_substrate._validate_plan(raw, op_id)
    except (_opf_init_substrate.InitSubstrateError, OSError):
        return None
    finally:
        for fd in (op_fd, ops_fd, home_fd, control_fd):
            if fd is not None:
                _journal._close_fd_quietly(fd)


def _init_store_reversal_preverify(root_fd, product_root, jr_fd, txn_dir):
    """The reversal removes EXACTLY what the operation created and nothing else (spec 14.2):
    every recorded creation still PRESENT live must be proven the operation's own publication by
    ATTRIBUTABLE SUBSTRATE EVIDENCE -- the digest the operation's own recorded plan binds to the
    path (read through the substrate's own plan validator, WHATEVER the record's phase-record
    status: _init_store_recorded_plan, so a kill inside a phase-record publication never strands
    the reversal), or the digest the operation's own views-group journal INTENT bound before the
    first view write -- BEFORE the journal rollback unlinks it. The adoption row's digest NEVER
    authorizes a removal by itself: a row bound to foreign bytes would otherwise delete the very
    foreign file it happens to match. Conflicting or absent evidence PRESERVES the file: live
    content that matches neither evidence source, a non-regular entry, or a member with no
    readable recorded plan is a FOREIGN write that landed after the preimages proved absence --
    the reversal refuses fail-closed, the transaction left open for inspection (remove or move
    the foreign content aside, then reconcile()). RESIDUAL (disclosed): the verification and
    the rollback's unlink are two observations, so a same-user adversarial swap between them
    stays outside the journal's quiescence guarantee, and a foreign file whose bytes EQUAL the
    substrate's own published payload is indistinguishable from it and is removed."""
    try:
        frames, _torn, _good = _journal.read_frames(jr_fd, txn_dir)
        intent = _journal._first(frames, _journal.F_INTENT)
    except (_journal.JournalError, OSError) as exc:
        raise AdoptApplyError("cannot read the init-store intent before its reversal ({}); "
                              "fail-closed".format(exc))
    if not isinstance(intent, dict):
        return
    header = intent.get("header") if isinstance(intent.get("header"), dict) else {}
    op_id = header.get("init_operation_id")

    def live_member_sha(path):
        try:
            st = _journal._lstat_contained(root_fd, path)
        except (_journal.JournalError, OSError) as exc:
            raise AdoptApplyError("cannot observe {} before its reversal ({}); "
                                  "fail-closed".format(path, exc))
        if st is None:
            return None
        foreign = AdoptApplyError("the reversal of {} found a non-regular, multiply-linked or "
                                  "unreadable entry at {}; foreign content is preserved, never "
                                  "removed by the reversal (fail-closed)".format(
                                      Path(txn_dir).name, path))
        if not stat.S_ISREG(st.st_mode):
            raise foreign
        if st.st_nlink != 1:
            # The ONE legitimate two-link state: a crash between the member's link(2) and its
            # staging unlink leaves the destination sharing its inode with the operation's OWN
            # plan-recorded staging name (the substrate's completed-publication classification);
            # any other extra link is foreign.
            parent, _sep, _name = path.rpartition("/")
            stage_rel = (parent + "/" if parent else "") \
                + init_op.staging_name(path, op_id if isinstance(op_id, str) else "")
            try:
                sst = _journal._lstat_contained(root_fd, stage_rel)
            except (_journal.JournalError, OSError):
                sst = None
            if st.st_nlink != 2 or sst is None \
                    or (sst.st_dev, sst.st_ino) != (st.st_dev, st.st_ino):
                raise foreign
        try:
            data, _fst = _journal._read_contained(root_fd, path)
        except (_journal.JournalError, OSError):
            raise foreign
        return _sha256(data)

    live_sha = {}
    for op in intent.get("ops", []):
        if not isinstance(op, dict) or op.get("op") != "create":
            continue
        path = op.get("path")
        got = live_member_sha(path)
        if got is None:
            continue
        live_sha[path] = got
    if not live_sha:
        return
    plan_sha = {}
    recorded = _init_store_recorded_plan(product_root, op_id)
    if isinstance(recorded, dict):
        try:
            plan_sha = {e["path"]: e["digest"][len("sha256:"):]
                        for e in recorded["sets"]["S"]
                        if isinstance(e, dict) and isinstance(e.get("path"), str)
                        and isinstance(e.get("digest"), str)
                        and e["digest"].startswith("sha256:")}
        except (KeyError, TypeError):
            plan_sha = {}
    view_sha = _init_store_view_digests(product_root, op_id)
    for path in sorted(live_sha):
        got = live_sha[path]
        accept = {d for d in (plan_sha.get(path), view_sha.get(path)) if d is not None}
        if got not in accept:
            raise AdoptApplyError("the reversal of {} found live content at {} that is not the "
                                  "operation's own publication; a foreign write is preserved, "
                                  "never removed by the reversal, which removes exactly what the "
                                  "operation created (spec 14.2); the transaction stays open for "
                                  "inspection (fail-closed)".format(Path(txn_dir).name, path))


def _init_store_reverse(product_root, root_fd, jr_fd, journal_root, txn, operation_id,
                        working_created, machine_created, members):
    """Execute the op's declared reversal NOW, from the transaction's own durable intent: prove
    every live member is the operation's own publication (_init_store_reversal_preverify: the
    reversal removes exactly what the operation created and nothing else), roll the open
    transaction back from its preimages (the forward election cannot fire, see
    _init_store_intent_ops), then scrub the substrate operation's remnants -- payload staging
    leftovers, lease, created directories -- and discard its record under the substrate mutex
    (_init_store_scrub, record LAST). A failure BEFORE the rollback completes leaves the
    transaction OPEN or ROLLBACK-IN-PROGRESS, so every later run refuses into reconcile(); a
    failure DURING the scrub leaves it terminal ROLLED-BACK beside the still-recorded operation,
    which the next init-store dispatch re-binds to this intent and re-scrubs under the EXPLICIT
    recover flag (fail-closed either way, never a silent half-reversal). Admitted .working
    content, a preserved CHANGELOG.md and admitted pre-existing directories were never ops, so
    the rollback cannot touch them."""
    import _opf_init_substrate
    _init_store_reversal_preverify(root_fd, product_root, jr_fd, journal_root / txn)
    try:
        outcome = _journal.recover(jr_fd, journal_root / txn, root_fd)
    except (_journal.JournalError, OSError) as exc:
        raise AdoptApplyError("the init-store reversal could not roll back ({}); the transaction "
                              "stays open for reconcile() (fail-closed)".format(exc))
    if outcome != "rolled-back":
        raise AdoptApplyError("the init-store transaction {} reconciled {} rather than rolling "
                              "back; fail-closed".format(txn, outcome))
    try:
        survey = _opf_init_substrate.classify_init_operations(str(product_root))
        ops = survey.operations if survey.status == _opf_init_substrate.OPERATIONS else ()
        recorded = any(r.op_id == operation_id for r in ops)
    except (_opf_init_substrate.InitSubstrateError, OSError) as exc:
        raise AdoptApplyError("the reversed substrate record cannot be classified ({}); "
                              "fail-closed".format(exc))
    if recorded:
        _init_store_scrub(product_root, root_fd, operation_id, members, working_created,
                          machine_created, recover=False)


def _init_store_postcondition(root_fd, product_root, members, operation_id):
    """Why the published scaffold does not verify, or None: the operation's validated recorded plan
    must create EXACTLY the member paths; every member's LIVE bytes, re-read contained, must match
    the row's digest; and init.toml, the one plan-unbindable member, must equal that recorded plan's
    own payload byte-exact (the residual the row cannot carry; see the module residuals)."""
    import _opf_init_substrate
    recorded = None
    try:
        survey = _opf_init_substrate.classify_init_operations(str(product_root))
        ops = survey.operations if survey.status == _opf_init_substrate.OPERATIONS else ()
        rep = next((r for r in ops if r.op_id == operation_id), None)
        if rep is not None and rep.status == _opf_init_substrate.INTACT:
            recorded = rep.plan
    except (_opf_init_substrate.InitSubstrateError, OSError):
        recorded = None
    if not isinstance(recorded, dict):
        return "operation {} left no intact recorded plan".format(operation_id)
    created = {e["path"] for key in ("S", "V") for e in recorded["sets"][key]}
    if created != set(members):
        return "operation {} planned creations {} where the row binds {}".format(
            operation_id, sorted(created), sorted(members))
    plan_payload = next((e["payload"] for e in recorded["sets"]["S"]
                         if e["path"] == init_op.PROVENANCE_RELPATH), None)
    for path in sorted(members):
        live = observe_live(root_fd, path)
        if live["kind"] != "file":
            return "operation {} did not publish {}".format(operation_id, path)
        if path == init_op.PROVENANCE_RELPATH:
            import base64
            try:
                want = _sha256(base64.b64decode(plan_payload, validate=True))
            except (TypeError, ValueError):
                want = None
            if want is None or want != live["sha256"]:
                return "operation {} published {} whose bytes do not equal its validated recorded " \
                       "plan's payload".format(operation_id, path)
        elif "sha256:" + live["sha256"] != members[path]:
            return "operation {} published {} whose live bytes do not match the row's member " \
                   "digest".format(operation_id, path)
    return None


def _init_store(op_row, context=None):
    """init-store (spec 14, 14.2): scaffold the default machine store by COMPOSING the coupled-init
    substrate's journaled, create-only operation (`_opf_init_operation.run_init_operation`) INSIDE
    this engine's own adoption transaction — never the thin `opf init` CLI path and never
    re-implemented here. `context` carries `product_root` (absolute), `plan` (its `run_id`, `store`
    identity and `sources` dispositions), `ancestral` (the pinned evidence commit of a re-adoption's
    counters seed, else None) and `recover` (default False: EXPLICIT recovery, never implicit).

    One lock shared by both writers: the handler takes THIS journal's lock (the same one
    run_adopt_transaction takes) before observing anything it acts on and holds it across the
    substrate's whole run, so no adoption transaction can interleave between the reconcile-first
    check, the preflights and the publication; the substrate's own operation mutex is taken inside
    it (lock order: adoption journal, then substrate mutex).

    Durable intent, recovery and reversal: before the substrate runs, the run's init-store
    transaction captures preimages (every creation proved absent) and publishes an INTENT binding
    the run id, adoption kind, ancestral seed, minted substrate operation id, admitted .working
    inventory and every member digest. A crash leaves that transaction open, so every later run
    refuses into reconcile(), whose journal-only recovery always rolls init-store BACK (init.toml is
    never plan-bindable, so the forward election cannot fire): the declared reversal, "remove the
    scaffolded store, restoring NOT-ADOPTED". The reversal removes EXACTLY what the operation
    created and nothing else: every live member is proven the operation's own publication before
    the rollback unlinks it (_init_store_reversal_preverify; foreign content at a member path is
    preserved and the reversal refuses), and the scrub clears the payload staging leftovers, the
    lease and the created directories BEFORE discarding the record LAST, so the reversed
    substrate record (the recovery discriminator) survives until the filesystem cleanup is
    complete and is discarded, under the EXPLICIT recover flag, by the next init-store dispatch
    that finds its rolled-back intent (an interrupted scrub is simply finished). A
    post-publication verification failure executes the same reversal immediately. A reversed or
    completed run never reruns under the same run id: changing approved work takes a fresh plan with
    its own run id (spec 14.1).

    Preconditions, before anything is written: the row targets the frozen default machine store at
    the product root; its members are EXACTLY the scaffold's complete creation set, every
    plan-bindable digest bound (fixed payloads checked against the builders here) and init.toml
    carrying the explicit unbound marker; a re-adoption names an ancestral seed (counters are seeded
    from the pinned high-water, never from zero) and a first adoption names none; the adoption
    journal is clean (reconcile-first); the store posture is one of the two first-adoption states;
    every pre-existing file at a declared external destination carries a digest-matched disposition;
    and every live `.working` file is justified by the plan (_init_store_admitted) — the substrate
    re-verifies the admitted inventory under its own mutex and records it in its immutable plan.

    VALID only when the substrate reached VIEWS-READY under the intent's operation id, its recorded
    plan creates exactly the members, and every member's live bytes verify. That is THIS OP's
    verdict; a `views-ready` milestone alone is never adoption success (spec 14), which still needs
    render-views, record-adoption and the completion checks. Every other outcome refuses
    CANNOT-EVALUATE after the reversal above. RESIDUAL (disclosed): init.toml's digest is bound by
    path and verified against the operation's validated recorded plan, never by a plan-time digest;
    and the context plan's own approval binding (plan_digest against the captured approval) is the
    stage driver's, not this op's."""
    try:
        return _init_store_run(op_row, context)
    except AdoptApplyError as exc:
        return schema._cannot("init-store refused: {}".format(exc))


def _init_store_run(op_row, context):
    import uuid
    ctx = context if isinstance(context, dict) else {}
    product_root, plan, ancestral = ctx.get("product_root"), ctx.get("plan"), ctx.get("ancestral")
    recover = ctx.get("recover", False)
    if not isinstance(recover, bool):
        raise AdoptApplyError("its context recover flag {!r} is not a bool (fail-closed)".format(recover))
    if not (isinstance(plan, dict) and isinstance(plan.get("store"), dict)
            and isinstance(plan.get("sources"), list)):
        raise AdoptApplyError("its context carries no approved plan store identity and sources (fail-closed)")
    run_id = plan.get("run_id")
    if not is_run_id(run_id):
        raise AdoptApplyError("its context plan carries no adoption run id (the run's init-store "
                              "transaction is named by it); fail-closed")
    frozen = plan["store"]
    kind = frozen.get("adoption")
    if kind not in schema.ADOPTION_KINDS:
        raise AdoptApplyError("plan store adoption {!r} is outside {}".format(kind, schema.ADOPTION_KINDS))
    if kind == "re-adoption" and not isinstance(ancestral, str):
        raise AdoptApplyError("a re-adoption seeds its counters from a pinned ancestral snapshot, never from "
                              "zero (decision 6), and no evidence commit was supplied")
    if kind == "first-adoption" and ancestral is not None:
        raise AdoptApplyError("a first adoption has no ancestry, so an ancestral counters seed contradicts it")
    variant = init_op.CHANGELOG_RELPATH in {m.get("path") for m in op_row["members"]
                                            if isinstance(m, dict)}
    manifest_rel, scaffold = _init_store_scaffold(kind == "first-adoption", variant)
    machine = manifest_rel.rsplit("/", 1)[0]
    if op_row["store_root"] != "." or frozen.get("store_root") != "." or frozen.get("machine_rel") != machine:
        raise AdoptApplyError("the coupled-init substrate scaffolds only the default machine store {} at the "
                              "product root (row store_root {!r}, plan store {!r})".format(
                                  machine, op_row["store_root"], frozen))
    members = _init_store_members_or_refuse(op_row, scaffold, manifest_rel)
    txn = _txn_name(run_id, INIT_STORE_PHASE)
    root_fd = _open_product_root(product_root)
    journal_root = _journal_root(product_root)
    jr_fd = None
    held = False
    try:
        try:
            _journal.require_containment()
        except _journal.JournalError as exc:
            raise AdoptApplyError("{} (fail-closed)".format(exc))
        journal_clean_or_refuse(root_fd, journal_root)
        try:
            _journal.ensure_journal_dirs(root_fd, JOURNAL_REL)
            jr_fd = _journal.open_journal_root_fd(root_fd, JOURNAL_REL)
        except (_journal.JournalError, OSError) as exc:
            raise AdoptApplyError("cannot prepare the adoption journal {} ({}); nothing "
                                  "written (fail-closed)".format(JOURNAL_REL, exc))
        try:
            _journal.acquire_lock(journal_root, SESSION_ID)
        except _journal.JournalError as exc:
            raise AdoptApplyError("cannot take the adoption journal lock ({}); nothing "
                                  "written (fail-closed)".format(exc))
        held = True
        # Under the ONE lock both writers share (the shell's transactions take this same lock), so
        # nothing can interleave between these observations and the substrate's publication.
        _owner, opened = journal_state(root_fd, journal_root)
        if opened:
            raise AdoptApplyError("interrupted adoption transaction(s) {} must be reconciled before "
                                  "this run (run reconcile()); nothing written (fail-closed)".format(
                                      ", ".join(opened)))
        state = "nothing-opened"
        try:
            if _journal._lstat_contained(root_fd, JOURNAL_REL + "/" + txn) is not None:
                state = _journal.classify_state(jr_fd, journal_root / txn)
        except (_journal.JournalError, OSError) as exc:
            raise AdoptApplyError("cannot classify this run's init-store transaction ({}); "
                                  "fail-closed".format(exc))
        if state == "complete":
            raise AdoptApplyError("run {} already completed its init-store transaction: one run takes "
                                  "one transaction per phase, and changing approved work takes a fresh "
                                  "plan with its own run id (spec 14.1); nothing written "
                                  "(fail-closed)".format(run_id))
        if state != "nothing-opened":
            raise AdoptApplyError("run {}'s init-store transaction was reversed ({}); a retried or "
                                  "changed run takes a fresh plan with its own run id (spec 14.1); "
                                  "nothing written (fail-closed)".format(run_id, state))
        record = _init_store_record(product_root)
        if record is not None:
            _init_store_orphan_or_refuse(product_root, root_fd, jr_fd, journal_root, record, recover)
        _store_posture_or_refuse(product_root)
        _init_store_dispositions_or_refuse(root_fd, plan)
        # The row decided whether CHANGELOG.md is a creation; the live tree must agree: a
        # pre-existing (dispositioned) changelog is preserved, never a member, and an absent one is
        # created, so it is one.
        if (observe_live(root_fd, init_op.CHANGELOG_RELPATH)["kind"] == "file") == variant:
            raise AdoptApplyError("the row's members are not exactly the scaffold's creations: "
                                  "CHANGELOG.md {} (nothing written beyond the journal "
                                  "directories)".format(
                                      "pre-exists dispositioned and is preserved, never a member"
                                      if variant else
                                      "is absent, so the scaffold creates it as a member"))
        admitted, admitted_dirs = _init_store_admitted(root_fd, product_root, plan,
                                                        jr_fd, journal_root)
        operation_id = str(uuid.uuid4())
        working_created = not admitted and store.WORKING_DIRNAME not in admitted_dirs
        machine_created = machine not in admitted_dirs
        header = dict(kind=KIND, run_id=run_id, phase=INIT_STORE_PHASE, operation=OPERATION,
                      op="init-store", adoption=kind, ancestral=ancestral,
                      init_operation_id=operation_id, working_created=working_created,
                      machine_created=machine_created, admitted=[dict(e) for e in admitted],
                      admitted_dirs=list(admitted_dirs))
        _init_store_txn_begin(root_fd, jr_fd, journal_root, txn, header,
                              _init_store_intent_ops(members))
        result = init_op.run_init_operation(str(product_root), ancestral=ancestral, recover=recover,
                                            admitted=admitted, operation_id=operation_id,
                                            admitted_dirs=admitted_dirs)
        if result.status != init_op.VIEWS_READY:
            failure = "the coupled-init substrate ended {} ({})".format(
                result.status, (result.primary_failure or {}).get("detail"))
        elif result.operation_id != operation_id:
            failure = "the substrate ran operation {} where the intent authorized {}".format(
                result.operation_id, operation_id)
        else:
            failure = _init_store_postcondition(root_fd, product_root, members, operation_id)
        if failure is not None:
            _init_store_reverse(product_root, root_fd, jr_fd, journal_root, txn, operation_id,
                                working_created=working_created,
                                machine_created=machine_created, members=sorted(members))
            raise AdoptApplyError("{}; the scaffold was reversed from the transaction's durable "
                                  "intent, restoring NOT-ADOPTED, and a retried or changed run takes "
                                  "a fresh plan with its own run id (spec 14.1, 14.2)".format(failure))
        try:
            _journal.publish(jr_fd, journal_root / txn, _journal.F_COMPLETE, {"txn": txn})
        except (_journal.JournalError, OSError) as exc:
            raise AdoptApplyError("the scaffold is published but its transaction cannot complete "
                                  "({}); reconcile() reverses it (fail-closed)".format(exc))
        return schema._ok()
    finally:
        if held:
            try:
                _journal.release_lock(journal_root)
            except (_journal.JournalError, OSError):
                pass   # a leftover lock refuses the next run into reconcile(), never a silent seize
        if jr_fd is not None:
            _journal._close_fd_quietly(jr_fd)
        store._close_fd_exc_safe(root_fd)


# --- the dispatch table: every op but init-store refuses not-yet-executable -----------------------------

def _not_yet_executable(op_row, context=None):
    """The refusing not-yet-executable verdict behind every dispatch entry no slice has landed yet. Each
    landing slice replaces its OP_HANDLERS entry with a real executor; the self-test pins every OTHER entry
    to THIS handler and every canonical row to a refusing status, so a silently-enabled op is a red."""
    name = op_row.get("op") if isinstance(op_row, dict) else None
    return schema.AdoptValidation(store.CANNOT_EVALUATE, [
        "op {!r} is not yet executable in this build; a later adoption slice lands it "
        "(fail-closed)".format(name)])


# Keyed by the closed ADOPT_OPS vocabulary; the self-test reconciles this table against
# ADOPT_OPS_BY_NAME in BOTH directions so it can neither drop nor invent an op.
OP_HANDLERS = {
    "install-pack": _not_yet_executable,
    "init-store": _init_store,
    "create-file": _not_yet_executable,
    "plant-governance": _not_yet_executable,
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
    Only init-store executes (given its context); every other handler refuses with no side effect."""
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
    where that finding is itself the contract). No network; only the init-store vectors use git (real
    fixture repositories, which the substrate binds) and a subprocess (a child dispatch of THIS engine,
    SIGKILLed inside the substrate's publication); every write lands under its own
    TemporaryDirectory."""
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
    # landed init-store is pinned to the refusing handler, and every canonical row refuses (init-store's for
    # want of a plan context, before anything is observed). A silently-enabled op is a red; the slice that
    # legitimately lands an op updates these pins in the same change.
    landed = frozenset(("init-store",))
    check("handlers-cover-vocabulary", set(OP_HANDLERS) == set(schema.ADOPT_OPS_BY_NAME))
    check("handlers-all-refusing-but-init-store",
          all(h is _not_yet_executable for n, h in OP_HANDLERS.items() if n not in landed))
    check("handler-init-store-landed", OP_HANDLERS["init-store"] is _init_store)
    res = dispatch(schema.canonical_op("init-store"))
    check("op-init-store-refuses-without-context",
          res.status == CANNOT and any("no approved plan" in f for f in res.findings))
    for name in sorted(schema.ADOPT_OP_NAMES - landed):
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

    # THE STALE-PREFLIGHT GUARD (QA round 2): the pre-lock journal and posture checks never
    # authorize composition. A cooperating writer (init-store takes this same lock) that opens a
    # transaction, or publishes a store, between the preflight and the acquisition is caught by
    # the re-proof UNDER the held lock, before anything composes.
    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        root = Path(temp).resolve()
        real_acquire = _journal.acquire_lock

        def plant_open_txn(journal_root, session_id):
            real_acquire(journal_root, session_id)
            rfd = _open_product_root(root)
            try:
                jfd = _journal.open_journal_root_fd(rfd, JOURNAL_REL)
                try:
                    os.mkdir("foreignrun", dir_fd=jfd)
                    _journal._create_frames_excl(jfd, _journal_root(root) / "foreignrun")
                    _journal.publish(jfd, _journal_root(root) / "foreignrun", _journal.F_INTENT,
                                     {"txn": "foreignrun", "header": {}, "ops": []})
                finally:
                    _journal._close_fd_quietly(jfd)
            finally:
                store._close_fd_exc_safe(rfd)

        with mock.patch.object(_journal, "acquire_lock", plant_open_txn):
            raced = refusal(run_adopt_transaction, root, rid, lambda ops: ops.create(
                evidence_home_rel(rid) + "/x.md", b"x\n"))
        check("stale-preflight-open-txn-rechecked-under-lock",
              raced is not None and "appeared before the lock" in raced
              and not (root / JOURNAL_REL / rid).exists() and lock_free(root))

    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        root = Path(temp).resolve()
        real_acquire = _journal.acquire_lock

        def plant_pointer(journal_root, session_id):
            real_acquire(journal_root, session_id)
            (root / store.POINTER_REL).write_bytes(b"store = 1\n")

        with mock.patch.object(_journal, "acquire_lock", plant_pointer):
            raced = refusal(run_adopt_transaction, root, rid, lambda ops: ops.create(
                evidence_home_rel(rid) + "/x.md", b"x\n"))
        check("stale-preflight-posture-rechecked-under-lock",
              raced is not None and "store posture" in raced
              and not (root / JOURNAL_REL / rid).exists() and lock_free(root))

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

    # 11: init-store over the coupled-init substrate (slice 3; real git fixtures, see _init_store_self_test).
    _init_store_self_test(check)

    if failures:
        print("OPF-ADOPT-APPLY SELF-TEST: FAIL ({} of {} checks failed)".format(len(failures), checked[0]))
        for f in failures:
            print("  FAILED: {}".format(f))
        return 1
    print("OPF-ADOPT-APPLY SELF-TEST: PASS ({} apply-shell checks; one executable op, init-store)".format(
        checked[0]))
    return 0


def _init_store_self_test(check):
    """Slice 3: init-store composed over the coupled-init substrate inside the adoption transaction.
    The precondition vectors are pure; the executing vectors run over REAL git fixtures (the
    substrate binds the repository and HEAD) under the substrate's own pinned git lifecycle, and the
    interrupted run is a child process dispatching THROUGH THIS ENGINE, SIGKILLed inside the
    substrate's publication (--selftest-init-store-child), so recovery is proved over the adoption
    journal, not the substrate alone. Each refusal is attributed by a reason keyword and judged on
    BEHAVIOUR: trees compared, operations counted, transactions classified."""
    import shutil
    import tempfile
    import _opf_oplock
    cannot = store.CANNOT_EVALUATE
    manifest_rel, scaffold = _init_store_scaffold(True, True)

    def members_for(sc, fake="sha256:" + "a" * 64):
        out = []
        for path in sorted(sc):
            digest = sc[path]
            if path == init_op.PROVENANCE_RELPATH:
                digest = UNBOUND_DIGEST
            elif digest is None:
                digest = fake
            out.append(dict(path=path, digest=digest))
        return out

    def ctx(root, adoption="first-adoption", sources=(), ancestral=None, run_id=None,
            recover=False):
        plan = dict(run_id=run_id or "adopt-20260917T120000Z-0123456789abcdef",
                    store=dict(store_root=".", machine_rel=manifest_rel.rsplit("/", 1)[0],
                               adoption=adoption),
                    sources=[dict(r) for r in sources])
        out = dict(product_root=root, plan=plan, ancestral=ancestral)
        if recover:
            out["recover"] = True
        return out

    def refused(res, needle):
        return res.status == cannot and any(needle in f for f in res.findings)

    # The handler's own derivations pin the substrate's: the manifest path and the default manifest's
    # exact digest (baseline record types only, every module tier off), and a scaffold that is the
    # COMPLETE creation set: machine sources, init.toml, the root pointer, CHANGELOG.md when created,
    # and every declared view.
    import _opf_init
    default = tomllib.loads(_opf_init.build_manifest())
    row = dict(op="init-store", store_root=".", members=members_for(scaffold))
    bound = {m["path"]: m["digest"] for m in row["members"]}
    check("init-store-manifest-is-the-substrate-manifest", manifest_rel == init_op._MANIFEST_RELPATH
          and bound[manifest_rel] == "sha256:" + _sha256(_opf_init.build_manifest().encode("utf-8")))
    check("init-store-default-manifest-baseline-only", not any(default["modules"].values())
          and set(default["types"]) == set(store.BASELINE_TYPES))
    check("init-store-scaffold-is-the-complete-creation-set",
          init_op.PROVENANCE_RELPATH in scaffold and store.POINTER_REL in scaffold
          and init_op.CHANGELOG_RELPATH in scaffold
          and {v["target"] for v in default["views"].values()} <= set(scaffold)
          and set(init_op.BOOTSTRAP_SOURCE_ROSTER) <= set(scaffold))
    check("init-store-external-destinations-derived",
          _init_store_external_destinations() == [init_op.CHANGELOG_RELPATH])
    check("init-store-row-validates", schema.validate_op(row).status == store.VALID)

    with tempfile.TemporaryDirectory() as tmp:
        bare = os.path.join(tmp, "bare")
        os.mkdir(bare)
        missing = dict(row, members=[m for m in row["members"] if m["path"] != store.POINTER_REL])
        stray = dict(row, members=row["members"] + [dict(path=".working/notes.md",
                                                         digest="sha256:" + "0" * 64)])
        bound_init = dict(row, members=[dict(m, digest="sha256:" + "d" * 64)
                                        if m["path"] == init_op.PROVENANCE_RELPATH else m
                                        for m in row["members"]])
        unbound_view = dict(row, members=[dict(m, digest=UNBOUND_DIGEST)
                                          if m["path"] == ".working/TODO.md" else m
                                          for m in row["members"]])
        wrong_fixed = dict(row, members=[dict(m, digest="sha256:" + "c" * 64)
                                         if m["path"].endswith("/version.toml") else m
                                         for m in row["members"]])
        other = ctx(bare)
        other["plan"]["store"]["machine_rel"] = store.WORKING_DIRNAME + "/other"
        check("init-store-no-context-refused", refused(dispatch(row), "no approved plan"))
        check("init-store-no-run-id-refused",
              refused(dispatch(row, ctx(bare, run_id="imp-20260917T120000Z-0123456789abcdef")),
                      "no adoption run id"))
        check("init-store-missing-member-refused",
              refused(dispatch(missing, ctx(bare)), "not exactly the scaffold"))
        check("init-store-stray-member-refused",
              refused(dispatch(stray, ctx(bare)), "not exactly the scaffold"))
        check("init-store-bound-looking-init-toml-refused",
              refused(dispatch(bound_init, ctx(bare)), "cannot be bound at plan time"))
        check("init-store-unbound-marker-on-bindable-member-refused",
              refused(dispatch(unbound_view, ctx(bare)), "bind it"))
        check("init-store-fixed-payload-digest-refused",
              refused(dispatch(wrong_fixed, ctx(bare)), "builders fix"))
        check("init-store-other-machine-store-refused", refused(dispatch(row, other),
                                                                "default machine store"))
        check("init-store-readoption-without-seed-refused",
              refused(dispatch(row, ctx(bare, "re-adoption")), "never from"))
        check("init-store-first-adoption-with-seed-refused",
              refused(dispatch(row, ctx(bare, ancestral="0" * 40)), "no ancestry"))
        check("init-store-preconditions-wrote-nothing", os.listdir(bare) == [])
    # Fail-closed sources-row validation (round 2): a malformed consumed field never reads as
    # non-occupying, never freezes a source, and never authorizes a control-area copy; the
    # planner's own _validate_plan_sources is the ONE validator for these fields.
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp).resolve()
        rel = store.WORKING_DIRNAME + "/notes.md"
        (root / store.WORKING_DIRNAME).mkdir()
        (root / rel).write_bytes(b"adopter notes\n")
        digest = "sha256:" + _sha256(b"adopter notes\n")
        run = "adopt-20260917T120000Z-0123456789abcdef"
        rfd = _open_product_root(root)
        try:
            def admitted_err(sources):
                try:
                    _init_store_admitted(rfd, root, dict(run_id=run, sources=sources))
                except AdoptApplyError as exc:
                    return str(exc)
                return None

            stringy = dict(path=rel, digest=digest, disposition="keep", occupying="true")
            check("init-store-nonbool-occupying-refused-fail-closed",
                  "validated fail-closed" in (admitted_err([stringy]) or ""))
            shred = dict(path=rel, digest=digest, disposition="shred", occupying=False,
                         preservation=store.IMPORTED_REL + "/x.md")
            check("init-store-invalid-disposition-copy-refused-fail-closed",
                  "validated fail-closed" in (admitted_err([shred]) or ""))
            wrong_home = dict(path=rel, digest=digest, disposition="retire", occupying=False,
                              preservation=store.ARCHIVE_REL + "/adoption/other/x.md")
            check("init-store-wrong-preservation-home-refused-fail-closed",
                  "validated fail-closed" in (admitted_err([wrong_home]) or ""))
            good = dict(path=rel, digest=digest, disposition="keep", occupying=False)
            adm, adirs = _init_store_admitted(rfd, root, dict(run_id=run, sources=[good]))
            check("init-store-valid-row-still-admits",
                  [e["path"] for e in adm] == [rel] and adirs == ())
        finally:
            store._close_fd_exc_safe(rfd)

    if shutil.which("git") is None:
        check("init-store-git-available", False)
        return
    _opf_oplock._st_with_git_lifecycle(lambda: _init_store_git_checks(check, ctx, refused))


def _init_store_child(root, env, kill, row, context):
    """Run ONE dispatch in a fresh interpreter with the named substrate kill hook armed; returns the
    child's exit status (negative on the expected SIGKILL)."""
    import base64
    import json
    import subprocess
    doc = dict(row=row, ctx=dict(context, product_root=None))
    blob = base64.b64encode(json.dumps(doc).encode("utf-8")).decode("ascii")
    argv = [sys.executable, "-I", "-B", os.path.abspath(__file__),
            "--selftest-init-store-child", root, kill, blob]
    proc = subprocess.run(argv, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          timeout=300)
    return proc.returncode


def _init_store_child_main(argv):
    """The --selftest-init-store-child entry: install the substrate's source-publication kill hook
    (production code carries no kill hooks), dispatch ONE init-store row in this interpreter, print
    the status. The self-test SIGKILLs the ENGINE mid-publication this way — under its held adoption
    journal lock and open transaction — exactly as a crash would."""
    import base64
    import json
    import signal
    root, kill, blob = argv
    doc = json.loads(base64.b64decode(blob))
    name, _sep, arg = kill.partition(":")

    def die():
        sys.stdout.flush()
        os.kill(os.getpid(), signal.SIGKILL)

    def plant_stage(pfd, entry, data):
        fd = os.open(entry["staging"], os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=pfd)
        try:
            os.fchmod(fd, entry["mode"])
            _journal._write_all(fd, data)
            os.fsync(fd)
        finally:
            os.close(fd)

    if name == "source":
        counter = {"n": 0}
        real_stage = init_op._stage_and_publish

        def stage(pfd, pname, entry, data):
            out = real_stage(pfd, pname, entry, data)
            counter["n"] += 1
            if counter["n"] == int(arg):
                die()
            return out
        init_op._stage_and_publish = stage
    elif name == "stage":
        # crash AFTER the named member's staging file exists, BEFORE its link: the payload
        # staging leftover the reversal's scrub must remove (codex round 2, finding 2)
        real_stage = init_op._stage_and_publish

        def stage(pfd, pname, entry, data):
            if entry["path"] == arg:
                plant_stage(pfd, entry, data)
                die()
            return real_stage(pfd, pname, entry, data)
        init_op._stage_and_publish = stage
    elif name == "linked":
        # crash AFTER the named member's destination link, BEFORE the staging unlink
        real_stage = init_op._stage_and_publish

        def stage(pfd, pname, entry, data):
            if entry["path"] == arg:
                plant_stage(pfd, entry, data)
                os.link(entry["staging"], pname, src_dir_fd=pfd, dst_dir_fd=pfd,
                        follow_symlinks=False)
                die()
            return real_stage(pfd, pname, entry, data)
        init_op._stage_and_publish = stage
    elif name == "txnmkdir":
        # crash between the init-store transaction's mkdir and its frames.log
        real_frames = _journal._create_frames_excl

        def frames(jr_fd, txn_dir):
            if str(txn_dir).endswith("." + INIT_STORE_PHASE):
                die()
            return real_frames(jr_fd, txn_dir)
        _journal._create_frames_excl = frames
    elif name == "intent":
        # crash after the INTENT is durable, before the substrate writes anything
        def run(*_a, **_k):
            die()
        init_op.run_init_operation = run
    elif name == "phasestage":
        # crash INSIDE the substrate's publication of the named phase record: the record's
        # staging file exists in the operation directory (an exact staging-name leftover that
        # classifies the record CANNOT-EVALUATE), the record itself does not (claude round 3)
        import _opf_init_substrate
        import _opf_oplock
        real_record = _opf_init_substrate.record_phase

        def record(sub, cap, phase):
            if phase == arg:
                pname = "%04d-%s.json" % (sub._next_seq, phase)
                fd = os.open(_opf_oplock._staging_name(pname),
                             os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600,
                             dir_fd=sub._op_fd)
                _journal._write_all(fd, b"torn")
                os.fsync(fd)
                os.close(fd)
                die()
            return real_record(sub, cap, phase)
        _opf_init_substrate.record_phase = record
    elif name == "scrubdir":
        # crash immediately AFTER the recovery scrub removes the named reversed directory,
        # BEFORE the record discard: the still-recorded operation is the recovery
        # discriminator, so the record-LAST order keeps the next dispatch recoverable
        real_rmdir = os.rmdir

        def rmdir(dname, *a, **k):
            real_rmdir(dname, *a, **k)
            if dname == arg:
                die()
        os.rmdir = rmdir
        # the containment probe checks these functions by IDENTITY against supports_dir_fd
        os.supports_dir_fd.add(rmdir)
    elif name == "cleanup":
        # crash INSIDE the reversed-record discard of a RECOVERY dispatch; "partial" first
        # removes one record file, simulating a kill between the discard's own unlinks
        import _opf_init_substrate

        def discard(_writer, op_id):
            if arg == "partial":
                os.unlink(os.path.join(init_op._ops_dir(root), op_id,
                                       _opf_init_substrate.PLAN_NAME))
            die()
        _opf_init_substrate.discard_reversed_operation = discard
    elif name != "none":
        return 2
    res = dispatch(doc["row"], dict(doc["ctx"], product_root=root))
    print(json.dumps({"status": res.status, "findings": res.findings}))
    return 0


def _init_store_git_checks(check, ctx, refused):
    """The executing init-store vectors over real git fixtures (see _init_store_self_test)."""
    import tempfile
    import _opf_init
    from _opf_schema import high_water
    valid = store.VALID
    manifest_rel, scaffold = _init_store_scaffold(True, True)

    def op_ids(root):
        ops = init_op._ops_dir(root)
        return sorted(os.listdir(ops)) if os.path.isdir(ops) else []

    def snap(root):
        """The worktree snapshot MINUS this engine's own journal home (which legitimately persists
        transactions and their preimages across a reversal)."""
        return {rel: v for rel, v in init_op._tree_snapshot(root).items()
                if rel.split(os.sep)[0] != ".aiqt"}

    def live_digest(root, rel):
        with open(os.path.join(root, rel), "rb") as fh:
            return "sha256:" + _sha256(fh.read())

    def txn_state(root, run):
        root_fd = _open_product_root(root)
        try:
            jr_fd = _journal.open_journal_root_fd(root_fd, JOURNAL_REL)
            try:
                return _journal.classify_state(
                    jr_fd, _journal_root(root) / _txn_name(run, INIT_STORE_PHASE))
            finally:
                _journal._close_fd_quietly(jr_fd)
        finally:
            store._close_fd_exc_safe(root_fd)

    with tempfile.TemporaryDirectory(prefix="opf-adopt-init-") as base:
        base = os.path.realpath(base)
        env = init_op._st_git_env(base)
        nonce = ("%016x" % n for n in range(1, 128))

        def rid():
            return mint_run_id(datetime.datetime(2026, 9, 17, 12, 0, 0,
                                                 tzinfo=datetime.timezone.utc), next(nonce))

        # The probe: the SUBSTRATE itself publishes one store in a scratch repo, and the
        # planner-bindable view digests are harvested from it (each view is a pure function of the
        # planned sources, so the harvest is repository-independent; the probe never meets the
        # engine under test).
        probe = init_op._plain_repo(os.path.join(base, "probe"), env)
        pres = init_op.run_init_operation(probe)
        check("init-store-probe-views-ready", pres.status == init_op.VIEWS_READY)
        members = []
        for path in sorted(scaffold):
            digest = scaffold[path]
            if path == init_op.PROVENANCE_RELPATH:
                digest = UNBOUND_DIGEST
            elif digest is None:
                digest = live_digest(probe, path)
            members.append(dict(path=path, digest=digest))
        row = dict(op="init-store", store_root=".", members=members)
        check("init-store-full-row-validates", schema.validate_op(row).status == valid)

        # A first adoption over NOT-ADOPTED: the substrate publishes the default store inside the
        # run's COMPLETE init-store transaction; every member's live bytes match the row. The same
        # run never reruns, and a fresh run over the initialized store refuses on its history.
        root = init_op._plain_repo(os.path.join(base, "fresh"), env)
        run = rid()
        res = dispatch(row, ctx(root, run_id=run))
        check("init-store-first-adoption-views-ready", res.status == valid
              and store.resolve_store(root).status == store.RESOLVED and len(op_ids(root)) == 1
              and live_digest(root, manifest_rel) == scaffold[manifest_rel]
              and txn_state(root, run) == "complete")
        check("init-store-same-run-rerun-refused",
              refused(dispatch(row, ctx(root, run_id=run)), "already completed its init-store"))
        before = snap(root)
        check("init-store-initialized-store-refused",
              refused(dispatch(row, ctx(root, run_id=rid())), "already records init history")
              and snap(root) == before)

        # THE REVERSAL (claude 4 / codex 3): a member digest the published bytes cannot match (a
        # deferred view digest, so every pre-write check passes) rolls the WHOLE scaffold back from
        # the transaction's durable intent: tree byte-identical, no store, no operation record, the
        # transaction terminal ROLLED-BACK. The reversed run id is burnt; a fresh run then succeeds.
        root = init_op._plain_repo(os.path.join(base, "reverse"), env)
        wrongv = [dict(m, digest="sha256:" + "b" * 64) if m["path"] == ".working/TODO.md"
                  else dict(m) for m in members]
        before = snap(root)
        run = rid()
        res = dispatch(dict(row, members=wrongv), ctx(root, run_id=run))
        check("init-store-member-mismatch-reversed", refused(res, "restoring NOT-ADOPTED")
              and snap(root) == before and op_ids(root) == []
              and store.resolve_store(root).status != store.RESOLVED
              and txn_state(root, run) == "rolled-back")
        check("init-store-reversed-run-never-reruns",
              refused(dispatch(row, ctx(root, run_id=run)), "was reversed"))
        res = dispatch(row, ctx(root, run_id=rid()))
        check("init-store-fresh-run-after-reversal", res.status == valid
              and store.resolve_store(root).status == store.RESOLVED)

        # A wrong PLAN-FIXED digest refuses before anything at all is written (no journal home).
        root = init_op._plain_repo(os.path.join(base, "fixed"), env)
        wrongf = [dict(m, digest="sha256:" + "c" * 64) if m["path"].endswith("/version.toml")
                  else dict(m) for m in members]
        before = init_op._tree_snapshot(root)
        check("init-store-fixed-digest-refused-pre-write",
              refused(dispatch(dict(row, members=wrongf), ctx(root, run_id=rid())), "builders fix")
              and init_op._tree_snapshot(root) == before)

        # Reconcile-first: an unreadable adoption journal lock refuses before the substrate.
        root = init_op._plain_repo(os.path.join(base, "journal"), env)
        os.makedirs(os.path.join(root, JOURNAL_REL))
        with open(os.path.join(root, JOURNAL_REL, "lock"), "wb") as fh:
            fh.write(b"not a lock\n")
        before = init_op._tree_snapshot(root)
        check("init-store-journal-first", refused(dispatch(row, ctx(root, run_id=rid())),
                                                  "adoption journal")
              and init_op._tree_snapshot(root) == before and op_ids(root) == [])

        # THE BLIND-INIT DISCRIMINATOR (codex 7 / claude 5): the SAME foreign .working fixture is
        # refused undispositioned and ADMITTED once the plan dispositions it — the substrate then
        # verifies and preserves the frozen source byte-exact through the whole publication.
        root = init_op._plain_repo(os.path.join(base, "blind"), env)
        payload = b"adopter notes\n"
        init_op._write(os.path.join(root, store.WORKING_DIRNAME, "notes.md"), payload)
        before = snap(root)
        check("init-store-undispositioned-working-refused",
              refused(dispatch(row, ctx(root, run_id=rid())), "undispositioned foreign")
              and snap(root) == before and op_ids(root) == [])
        frozen_row = dict(path=".working/notes.md", digest="sha256:" + _sha256(payload),
                          disposition="keep", occupying=False)
        res = dispatch(row, ctx(root, run_id=rid(), sources=[frozen_row]))
        with open(os.path.join(root, store.WORKING_DIRNAME, "notes.md"), "rb") as fh:
            intact = fh.read() == payload
        check("init-store-dispositioned-working-admitted", res.status == valid and intact
              and store.resolve_store(root).status == store.RESOLVED)
        root = init_op._plain_repo(os.path.join(base, "driftsrc"), env)
        init_op._write(os.path.join(root, store.WORKING_DIRNAME, "notes.md"), b"drifted\n")
        check("init-store-drifted-frozen-source-refused",
              refused(dispatch(row, ctx(root, run_id=rid(), sources=[frozen_row])),
                      "undispositioned foreign") and op_ids(root) == [])

        # This run's evidence bundle is admitted only VERIFIED: the inventories plus exactly the
        # payloads they list; one tampered byte refuses the whole bundle.
        root = init_op._plain_repo(os.path.join(base, "bundle"), env)
        run = rid()
        home = evidence_home_rel(run)
        data = b"evidence payload\n"
        init_op._write(os.path.join(root, home, "observe.json"), data)
        inv = emit_inventory(run, [inventory_row(home + "/observe.json", data)])
        init_op._write(os.path.join(root, home, "inventory.toml"), inv)
        res = dispatch(row, ctx(root, run_id=run))
        check("init-store-verified-bundle-admitted", res.status == valid
              and store.resolve_store(root).status == store.RESOLVED
              and live_digest(root, home + "/observe.json") == "sha256:" + _sha256(data))
        root = init_op._plain_repo(os.path.join(base, "badbundle"), env)
        run = rid()
        home = evidence_home_rel(run)
        init_op._write(os.path.join(root, home, "observe.json"), b"tampered\n")
        init_op._write(os.path.join(root, home, "inventory.toml"),
                       emit_inventory(run, [inventory_row(home + "/observe.json", data)]))
        check("init-store-tampered-bundle-refused",
              refused(dispatch(row, ctx(root, run_id=run)), "does not verify")
              and op_ids(root) == [])

        # CHANGELOG.md disposition (codex 6 / claude 3): a pre-existing changelog with no plan row
        # refuses; dispositioned, it is preserved byte-exact and never a member; a members list
        # still naming it is a set mismatch.
        root = init_op._plain_repo(os.path.join(base, "changelog"), env)
        body = b"# My changes\n"
        init_op._write(os.path.join(root, init_op.CHANGELOG_RELPATH), body)
        mem_nolog = [m for m in members if m["path"] != init_op.CHANGELOG_RELPATH]
        check("init-store-existing-changelog-undispositioned-refused",
              refused(dispatch(dict(row, members=mem_nolog), ctx(root, run_id=rid())),
                      "undispositioned declared destination"))
        logrow = dict(path=init_op.CHANGELOG_RELPATH, digest="sha256:" + _sha256(body),
                      disposition="keep", occupying=False)
        res = dispatch(dict(row, members=mem_nolog), ctx(root, run_id=rid(), sources=[logrow]))
        with open(os.path.join(root, init_op.CHANGELOG_RELPATH), "rb") as fh:
            preserved = fh.read() == body
        check("init-store-dispositioned-changelog-preserved", res.status == valid and preserved)
        root = init_op._plain_repo(os.path.join(base, "changelog2"), env)
        init_op._write(os.path.join(root, init_op.CHANGELOG_RELPATH), body)
        check("init-store-preserved-changelog-never-a-member",
              refused(dispatch(row, ctx(root, run_id=rid(), sources=[logrow])),
                      "not exactly the scaffold"))

        # SOURCE VALIDATION BEFORE CONSUMPTION, .working ABSENT (codex round 3, finding 3): a
        # malformed external CHANGELOG.md row (a string occupying) refuses fail-closed BEFORE
        # the disposition preflight consumes it, with nothing written -- the planner's
        # _validate_plan_sources runs independent of whether .working exists.
        root = init_op._plain_repo(os.path.join(base, "extmalformed"), env)
        init_op._write(os.path.join(root, init_op.CHANGELOG_RELPATH), body)
        badlog = dict(logrow, occupying="true")
        before = snap(root)
        check("init-store-malformed-external-row-refused-fail-closed",
              refused(dispatch(dict(row, members=mem_nolog),
                               ctx(root, run_id=rid(), sources=[badlog])),
                      "validated fail-closed")
              and snap(root) == before and op_ids(root) == [])

        # The counters flip: a re-adoption whose ancestral store allocated WL-7 seeds from that
        # pinned high-water (next WL 8, never a reused 1), its counters member digest-bound to the
        # seeded builder bytes; a snapshot missing a baseline namespace refuses inside the substrate
        # (UNKNOWN, never zero) and the scaffold is REVERSED, tree unchanged.
        values = dict.fromkeys(sorted(set(store.BASELINE_TYPES.values())), 0)
        values.update(WL=7, BI=3)
        seeded = _opf_init.build_counters(seed=dict(values)).encode("utf-8")
        missing_ns = {k: v for k, v in values.items() if k != "HO"}
        for name, counters in (("readopt", values), ("unknown", missing_ns)):
            root = init_op._plain_repo(os.path.join(base, name), env)
            init_op._write(os.path.join(root, init_op.COUNTERS_RELPATH),
                           init_op._counters_toml(counters))
            init_op._git(["add", "-A"], root, env)
            init_op._git(["commit", "-q", "-m", "adopted"], root, env)
            evidence = init_op._head(root, env)
            init_op._git(["rm", "-q", "-r", store.WORKING_DIRNAME], root, env)
            init_op._git(["commit", "-q", "-m", "deleted"], root, env)
            mem = [dict(m, digest="sha256:" + _sha256(seeded))
                   if m["path"] == init_op.COUNTERS_RELPATH else dict(m) for m in members]
            before = snap(root)
            run = rid()
            res = dispatch(dict(row, members=mem), ctx(root, "re-adoption", ancestral=evidence,
                                                       run_id=run))
            if name == "readopt":
                got = {}
                if res.status == valid:
                    with open(os.path.join(root, init_op.COUNTERS_RELPATH), "rb") as fh:
                        got = tomllib.loads(fh.read().decode("utf-8"))["counters"]
                check("init-store-readoption-seeds-ancestral-high-water", res.status == valid
                      and high_water(got, "WL") + 1 == 8 and got.get("BI") == 3)
            else:
                check("init-store-missing-namespace-reversed", refused(res, "UNKNOWN")
                      and snap(root) == before and op_ids(root) == []
                      and txn_state(root, run) == "rolled-back")

        # RECOVERY (claude 1 / codex 2, 4): the ENGINE is SIGKILLed inside the substrate's
        # publication, under its held adoption lock and open transaction. Every later run refuses
        # into reconcile(); reconcile reverses the scaffold from the journal ALONE; the orphaned
        # substrate record is discarded only under the EXPLICIT recover flag; a fresh run then
        # succeeds. A partial substrate operation is never resumed across adoption runs.
        root = init_op._plain_repo(os.path.join(base, "kill"), env)
        before = snap(root)
        rc = _init_store_child(root, env, "source:2", row, ctx(root, run_id=rid()))
        partial = op_ids(root)
        check("init-store-killed-engine-left-partial", rc != 0 and len(partial) == 1
              and store.resolve_store(root).status != store.RESOLVED)
        # THE PLAIN-SUBSTRATE COUPLING (round 2): a plain `opf init` resume can never finish or
        # publish the adoption-started partial operation; its plan records the coupled recovery
        # policy and refuses any resume, so the store stays unpublished and the adoption
        # transaction stays the only recovery path.
        plain = init_op.run_init_operation(root, recover=True)
        check("init-store-plain-resume-of-coupled-partial-refused",
              plain.status != init_op.VIEWS_READY
              and "never resumed or published outside"
              in (plain.primary_failure or {}).get("detail", "")
              and store.resolve_store(root).status != store.RESOLVED
              and op_ids(root) == partial)
        check("init-store-interrupted-run-refuses-into-reconcile",
              refused(dispatch(row, ctx(root, run_id=rid())), "never seized")
              and op_ids(root) == partial)
        outcomes = reconcile(root)
        gone = all(not os.path.lexists(os.path.join(root, m["path"])) for m in members)
        check("init-store-reconcile-reverses-from-journal-alone",
              "rolled-back" in [outcome for _t, outcome in outcomes] and gone
              and op_ids(root) == partial)
        check("init-store-orphan-discard-needs-explicit-recover",
              refused(dispatch(row, ctx(root, run_id=rid())), "EXPLICIT context recover")
              and op_ids(root) == partial)
        res = dispatch(row, ctx(root, run_id=rid(), recover=True))
        check("init-store-fresh-run-after-recovery", res.status == valid
              and store.resolve_store(root).status == store.RESOLVED
              and len(op_ids(root)) == 1 and partial[0] not in op_ids(root)
              and {rel: v for rel, v in snap(root).items()
                   if rel.split(os.sep)[0] != store.WORKING_DIRNAME
                   and rel != init_op.CHANGELOG_RELPATH
                   and rel != store.POINTER_REL} == before)

        # THE REVERSAL REMOVES ONLY ITS OWN PUBLICATION (round 2): a foreign write that lands at
        # a member path between the durable intent and the substrate is PRESERVED -- the reversal
        # refuses fail-closed, the transaction stays open, and reconcile() refuses the same way.
        # First a poststate-bound member (CHANGELOG.md), then the one unbindable member
        # (init.toml, whose only binding is the operation's recorded plan, absent here).
        real_run = init_op.run_init_operation
        for fixture, target, payload in (
                ("foreignlog", init_op.CHANGELOG_RELPATH, b"the user's own changelog\n"),
                ("foreigninit", init_op.PROVENANCE_RELPATH, b"not the operation's provenance\n")):
            root = init_op._plain_repo(os.path.join(base, fixture), env)
            run = rid()

            def write_then_run(*a, **k):
                init_op._write(os.path.join(root, target), payload)
                return real_run(*a, **k)

            init_op.run_init_operation = write_then_run
            try:
                res = dispatch(row, ctx(root, run_id=run))
            finally:
                init_op.run_init_operation = real_run

            def foreign_kept():
                return init_op._read_or_none(os.path.join(root, target)) == payload

            check("init-store-foreign-{}-preserved-not-reversed".format(target),
                  refused(res, "foreign") and foreign_kept()
                  and txn_state(root, run) == "open")
            rec_err = None
            try:
                reconcile(root)
            except AdoptApplyError as exc:
                rec_err = str(exc)
            check("init-store-reconcile-preserves-foreign-{}".format(target),
                  rec_err is not None and "preserved" in rec_err and foreign_kept()
                  and txn_state(root, run) == "open")

        # THE FOREIGN-VIEW BINDING (codex round 3, finding 1): a row that binds the deferred
        # TODO.md view digest to FOREIGN bytes never authorizes their removal -- the reversal
        # accepts only attributable substrate publication evidence (the operation's own
        # recorded plan and its views-group intent), never the adoption row alone, so the
        # injected file is preserved and the reversal refuses with the transaction open.
        root = init_op._plain_repo(os.path.join(base, "foreignview"), env)
        run = rid()
        foreign_view = b"user data that the renderer never publishes\n"
        memf = [dict(m, digest="sha256:" + _sha256(foreign_view))
                if m["path"] == ".working/TODO.md" else dict(m) for m in members]

        def write_view_then_run(*a, **k):
            init_op._write(os.path.join(root, ".working", "TODO.md"), foreign_view)
            return real_run(*a, **k)

        init_op.run_init_operation = write_view_then_run
        try:
            res = dispatch(dict(row, members=memf), ctx(root, run_id=run))
        finally:
            init_op.run_init_operation = real_run
        check("init-store-foreign-view-binding-preserved",
              refused(res, "foreign")
              and init_op._read_or_none(os.path.join(root, ".working", "TODO.md"))
              == foreign_view and txn_state(root, run) == "open")

        # THE OCCUPIED MACHINE PATH COMPOSITION (codex round 2, spec 14.2: "After the archival
        # the destination is an ordinary managed path"): the shell archives the occupying
        # machine-store file preserve-first; the emptied PRE-EXISTING machine directory is then
        # admitted through the plan's own occupying row and init-store publishes with no manual
        # directory removal. On a reversal the pre-existing directory is preserved as found.
        occ_bytes = b"# a foreign manifest-shaped file\n"
        for fixture, mem in (("occupied", members), ("occupied2", wrongv)):
            root = init_op._plain_repo(os.path.join(base, fixture), env)
            init_op._write(os.path.join(root, manifest_rel), occ_bytes)
            run = rid()
            run_adopt_transaction(root, run, lambda ops: ops.archive_occupying(
                manifest_rel, "sha256:" + _sha256(occ_bytes)))
            occ_row = dict(path=manifest_rel, digest="sha256:" + _sha256(occ_bytes),
                           disposition="retire", occupying=True,
                           preservation=archive_rel(run, manifest_rel))
            before = snap(root)
            res = dispatch(dict(row, members=mem), ctx(root, run_id=run, sources=[occ_row]))
            if fixture == "occupied":
                check("init-store-after-occupying-archival-admitted", res.status == valid
                      and store.resolve_store(root).status == store.RESOLVED
                      and live_digest(root, manifest_rel) == scaffold[manifest_rel]
                      and live_digest(root, archive_rel(run, manifest_rel))
                      == "sha256:" + _sha256(occ_bytes))
            else:
                check("init-store-reversal-preserves-preexisting-machine-dir",
                      refused(res, "restoring NOT-ADOPTED") and snap(root) == before
                      and txn_state(root, run) == "rolled-back" and op_ids(root) == [])

        # ARCHIVAL EVIDENCE, NOT PLANNING (codex round 3, finding 2): an occupying row whose
        # archival never ran admits nothing -- the emptied machine directory is admitted only
        # with the preservation copy live, digest-matched, AND the archival transaction
        # COMMITTED in this adoption journal; here neither holds and nothing is written.
        root = init_op._plain_repo(os.path.join(base, "noarchive"), env)
        machine_dir = manifest_rel.rsplit("/", 1)[0]
        os.makedirs(os.path.join(root, machine_dir))
        os.chmod(os.path.join(root, store.WORKING_DIRNAME), 0o755)
        os.chmod(os.path.join(root, machine_dir), 0o755)
        run = rid()
        occ_row = dict(path=manifest_rel, digest="sha256:" + _sha256(occ_bytes),
                       disposition="retire", occupying=True,
                       preservation=archive_rel(run, manifest_rel))
        before = snap(root)
        check("init-store-unarchived-occupying-row-refused",
              refused(dispatch(row, ctx(root, run_id=run, sources=[occ_row])),
                      "completed archival")
              and snap(root) == before and op_ids(root) == [])

        # THE ADMITTED-DIRECTORY MODE PRE-CHECK (claude round 3, R3-5): a wrong-mode machine
        # directory (a umask-002 adopter) refuses BEFORE the run's intent opens -- nothing
        # recorded -- and the SAME run id then succeeds once the mode is corrected, proving
        # the id was never burnt by the refusal.
        root = init_op._plain_repo(os.path.join(base, "mode775"), env)
        init_op._write(os.path.join(root, manifest_rel), occ_bytes)
        run = rid()
        run_adopt_transaction(root, run, lambda ops: ops.archive_occupying(
            manifest_rel, "sha256:" + _sha256(occ_bytes)))
        occ_row = dict(path=manifest_rel, digest="sha256:" + _sha256(occ_bytes),
                       disposition="retire", occupying=True,
                       preservation=archive_rel(run, manifest_rel))
        os.chmod(os.path.join(root, machine_dir), 0o775)
        check("init-store-wrong-mode-admitted-dir-refused-pre-intent",
              refused(dispatch(row, ctx(root, run_id=run, sources=[occ_row])), "holds mode")
              and op_ids(root) == [])
        os.chmod(os.path.join(root, machine_dir), 0o755)
        res = dispatch(row, ctx(root, run_id=run, sources=[occ_row]))
        check("init-store-unburnt-run-id-reruns-after-mode-fix", res.status == valid
              and store.resolve_store(root).status == store.RESOLVED)

        # CRASH AT EVERY POINT (round 2): the transaction mkdir, the INTENT, a member's staging
        # write (machine-dir and root-level), between a member's link and its staging unlink,
        # after a full member publication, and inside the recovery cleanup (at the record discard
        # and mid-discard). Every point recovers to a fresh publication over a tree judged WHOLE:
        # byte-identical outside the published store, no staging leftover anywhere, exactly one
        # (fresh) operation record.
        def stage_leftovers(r):
            found = []
            for dirpath, _dirs, files in os.walk(r):
                if os.path.relpath(dirpath, r).split(os.sep)[0] == ".git":
                    continue
                found += [n for n in files if init_op._STAGE_MARKER in n]
            return found

        def outside_store(r):
            return {rel: v for rel, v in snap(r).items()
                    if rel.split(os.sep)[0] != store.WORKING_DIRNAME
                    and rel != init_op.CHANGELOG_RELPATH and rel != store.POINTER_REL}

        # the pre-INTENT mkdir crash: nothing was durable beyond the directory, so the SAME run
        # id simply reruns (its frame-less debris is cleared), never a burnt run id.
        root = init_op._plain_repo(os.path.join(base, "killmkdir"), env)
        before = snap(root)
        run = rid()
        rc = _init_store_child(root, env, "txnmkdir", row, ctx(root, run_id=run))
        reconcile(root)
        res = dispatch(row, ctx(root, run_id=run))
        check("init-store-crash-at-txn-mkdir-reruns-same-run", rc != 0 and res.status == valid
              and store.resolve_store(root).status == store.RESOLVED
              and txn_state(root, run) == "complete"
              and stage_leftovers(root) == [] and outside_store(root) == before)

        version_rel = "{}/version.toml".format(manifest_rel.rsplit("/", 1)[0])
        # phasestage:* SIGKILLs INSIDE a phase-record publication (claude round 3, R3-1: the
        # staging leftover classifies the record CANNOT-EVALUATE, and the reversal must still
        # bind init.toml through the operation's own plan record); scrubdir:* SIGKILLs the
        # RECOVERY dispatch right after a reversed directory's removal, BEFORE the record
        # discard (claude round 3, R3-2: under the pinned record-LAST order the still-recorded
        # operation lets the next dispatch finish the scrub; a record-FIRST scrub strands the
        # tree with no recovery discriminator).
        two_stage = ("cleanup", "scrubdir")
        machine_base = manifest_rel.rsplit("/", 1)[0].rsplit("/", 1)[1]
        for i, kill in enumerate(("intent", "stage:" + version_rel,
                                  "linked:" + init_op.CHANGELOG_RELPATH,
                                  "stage:" + init_op.CHANGELOG_RELPATH, "source:2",
                                  "phasestage:sources-ready", "phasestage:views-ready",
                                  "cleanup:entry", "cleanup:partial",
                                  "scrubdir:" + machine_base)):
            root = init_op._plain_repo(os.path.join(base, "kill{}".format(i)), env)
            before = snap(root)
            rc = _init_store_child(root, env,
                                   "source:2" if kill.partition(":")[0] in two_stage else kill,
                                   row, ctx(root, run_id=rid()))
            reconcile(root)
            killed = rc != 0
            if kill.partition(":")[0] in two_stage:
                killed = killed and _init_store_child(root, env, kill, row,
                                                      ctx(root, run_id=rid(),
                                                          recover=True)) != 0
                reconcile(root)
            res = dispatch(row, ctx(root, run_id=rid(), recover=True))
            check("init-store-crash-{}-recovers-whole-tree".format(
                      kill.replace(":", "-").replace("/", "-").replace(".", "")),
                  killed and res.status == valid
                  and store.resolve_store(root).status == store.RESOLVED
                  and stage_leftovers(root) == [] and len(op_ids(root)) == 1
                  and outside_store(root) == before)


def main():
    args = sys.argv[1:]
    if args[:1] == ["--selftest-init-store-child"]:
        return _init_store_child_main(args[1:])
    if "--self-test" in args or "--selftest" in args:
        return self_test()
    print("usage: _opf_adopt_apply.py --self-test (a library module; the adoption verb is `opf adopt`)",
          file=sys.stderr)
    return 2


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-test"]:
        sys.exit(self_test())
    sys.exit(main())
