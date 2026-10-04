#!/usr/bin/env python3
"""OPF adoption apply engine, slices 1, 3 and 5: the apply SHELL, init-store and the three finish ops (OPF-SPEC 1.3.0).

This slice (the clean-start adoption track's first apply unit) supplies the engine SKELETON only, shaped
by spec 1.3.0 sections 4.2, 5.7 and 14: run identity; the evidence-bundle, archive and Move homes, all
derived from the `_opf_store` homes constructors (never re-spelled here); the `opf.evidence.inventory/v1`
inventory DERIVED from the run's own transaction op list and published in that SAME transaction as the
retained bytes it lists (spec 4.2), a base `inventory.toml` then one `inventory-<phase>.toml` per later
phase, never rewritten; the homes-1 bundle verification the completion checks carry themselves while
C-EVIDENCE-ENUM is inactive (spec 14.1), which RE-READS the inventories and payload digests from disk;
the preserve-first composition of spec 14.2; live re-observation of every operand; one journaled
transaction per (run, phase), reconcile-first; and a dispatch table keyed by the closed eleven-op
ADOPT_OPS vocabulary. Slice 3 lands init-store, composed over the coupled-init substrate
(_init_store). Slice 5 makes the three finish ops executable: plant-governance (create-only
planting of a pack member that passed the b.5 trust gate, verify_pack_member), render-views (create-only view
publication of the render engine's planned bytes, composed into the journaled transaction) and record-adoption (the immutable receipt core and its genesis outcome event in the
run's own evidence bundle, adoption_record). The stage driver
adds the plan-v2 apply-input gate, the one approval and the apply stage (spec 14.1): `opf adopt approve`
re-proves a frozen plan from its own bytes, re-derives it over the live tree and prints the approval
binding its plan_digest and inventory_digest, writing nothing; `opf adopt apply` admits only that approved
pair, persists both in the run's evidence bundle within the run's one base transaction, and dispatches
every plan op through the table and then the driver's mandatory receipt stage, so while the driver's
mandatory receipt stage is unlanded, apply refuses before anything is written. Every other op returns a
refusing not-yet-executable verdict: the file ops, hook activation, the
completion checks, retirement, and the `complete` and `reconcile` CLI subcommands remain later slices;
the read-only `opf adopt` subcommands plan and status shipped with K9a. Live outside
the self-test fixtures today: `opf adopt status` opens and lists the evidence home in opf.py through the
_journal containment primitives, then grades each listed bundle through this module's _verify_bundle_at
(beneath the HELD home descriptor it is passed) and the journal through journal_state, with
_open_product_root anchoring both reads to one product-root descriptor; plan, approve and apply refuse
over a non-clean adoption journal (require_clean_journal); the transaction shell is reachable from
`opf adopt apply` only past the unlanded-op gate and the driver's mandatory receipt stage (unlanded in
this build), which no plan passes, and reconcile() stays reachable only from the self-test.

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

Stage driver (spec 14, 14.1): a plan is admitted only as its own canonical bytes whose plan_digest
re-seals and which the schema grades VALID; the approval is the receipt's APPROVAL_REQUIRED shape, its
plan_digest and inventory_digest equal to the plan's and its approved_at no earlier than the instant the
plan froze. Freshness is two observations: the live product revision (observe_revision, one read-only
git query) must equal the plan's bound revision, and re-derivation, the planner re-run over the live tree
with the worksheet that froze the plan and the plan's own run instant and nonce, must reproduce the plan
byte for byte; a moved revision, a changed observed item (sources, targets, store identity, ancestry) or
a changed worksheet refuses into a fresh plan with its own single approval. Replay admission precedes
freshness: a run whose base transaction exists refuses on the one-apply rule itself. Apply dispatches
every plan op in plan order in the apply stage through one context table (`ops`, the transaction's
ApplyOps; `plan`; `approval`; `product_root`; `stage`), the seam the op slices compose through, then
composes the driver's mandatory receipt stage (DRIVER_STAGES; spec 14 ends apply with the receipt and its
outcome-event chain, which no plan row can carry), and refuses any composed remove or write of a plan
source that occupies no managed destination: such a source stays frozen in place until the retirement
stage after a green completion check, and apply takes only its preimage. The driver, not handler
convention, enforces the composition rules: handlers run under a per-thread composition guard that
refuses any direct filesystem or process effect and any transaction of their own (so a slice composes
into `ops`, init-store's substrate included), and composition first runs as a write-free preflight
before the journal is prepared, so a preflight refusal writes nothing at all; a refusal of the second
composition, under the journal lock, releases the lock and removes the journal directories the run
created, so it too leaves the tree as it found it, unless a release or a removal fails or may not be
durable (a directory a concurrent run has populated meanwhile stays): the refusal then names each such
leftover in place of "nothing written". Disclosed:
digests bind the approval to one plan, never the actor's authenticity (self-asserted identity and
same-user tampering stay spec 14.1 residuals); the release, prompt_pack, enforcement and skip_policy
bindings are worksheet-asserted, so re-derivation proves the worksheet still freezes the plan, not that
the tool release or packs in use match it (the trust-verification slice observes them); the composition
guard is an audit hook, not a sandbox (an already-open writable descriptor, ctypes, a thread already
running before composition, since starting one is refused, or an unaudited entry point stays outside
it); the re-derivation and the transaction are not one snapshot, so each op re-observes its own operands
under the journal lock; one run takes one base transaction, so an approved
plan applies at most once.

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
`.aiqt/adopt/journal` is one more such home), so the stage driver refuses plan, approve and apply over a
non-clean adoption journal (require_clean_journal); the shipped `retire-file` vocabulary row is a
single `remove`, while spec 1.3.0 preserves the retirement preimage at apply and removes only after the
green check, a vocabulary split for the op slices; the shell's interruption is exercised in-process
through the journal's kill-point seam, while init-store's SIGKILLs the whole engine in a child dispatch
mid-publication, under the held lock and open transaction, and further
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
    sys.stderr.write(
        "error: _opf_adopt_apply.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
        "Nothing was run (cannot evaluate).\n"
        % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
    raise SystemExit(2)

import contextlib
import copy
import datetime
import hashlib
import os
import re
import shutil
import stat
import subprocess
import threading
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
# The only plan format apply may take (spec 14.1), the schema's own marker.
PLAN_V2_FORMAT = schema.PLAN_FORMAT
# The run's evidence-bundle members apply persists: the approved plan and its captured approval (spec 14.1).
PLAN_NAME = "plan.toml"
APPROVAL_NAME = "approval.toml"
# The stage every plan op composes in during apply; the retirement stage follows a green completion check.
APPLY_STAGE = "apply"
# The driver's mandatory receipt stage (spec 14), composed after every plan row whatever the plan carries.
RECEIPT_STAGE = "receipt"
# The bound on the one read-only git query that observes the live product revision (observe_revision).
_GIT_TIMEOUT_SECONDS = 30
_GIT_DIAGNOSIS_CHARS = 300
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


class AdoptCommittedLockError(AdoptApplyError):
    """NOT a refusal: the transaction `txn` COMMITTED, so its product-tree changes LANDED and are not rolled
    back, but its journal lock release left the lock or may not be durable. A subclass of AdoptApplyError,
    so the CLI's exit mapping is unchanged."""

    def __init__(self, txn, note):
        super().__init__("the adoption transaction {} COMMITTED: its product-tree changes LANDED and are not "
                         "rolled back; {}".format(txn, note))
        self.txn = txn


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
                              "single-writer lease join yet (spec 5.7), so it refuses "
                              "(fail-closed)".format(res.machine_rel))
    if res.status == store.NOT_ADOPTED:
        return
    if res.pointer_source == "default" and _default_store_present_without_manifest(product_root):
        return
    raise AdoptApplyError("the store posture cannot be evaluated ({}); an ambiguous, malformed or "
                          "unreadable store input refuses (spec 14.2, "
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


def _compose_checked(root_fd, run_id, phase, compose):
    """Compose ONE transaction against the live tree beneath `root_fd`, read-only: compose(ops) fills a fresh
    ApplyOps, the derived inventory seals it, and check_apply_ops re-proves every invariant, so a refusal here
    has written nothing. Returns the sealed ApplyOps."""
    ops = ApplyOps(root_fd, run_id, phase)
    compose(ops)
    ops.seal()
    findings = check_apply_ops(run_id, phase, ops.ops, ops.staged)
    if findings:
        raise AdoptApplyError("the composed transaction is refused before it opens: {}".format(
            "; ".join(findings)))
    return ops


def _absent_journal_dirs(root_fd):
    """The JOURNAL_REL directories, shallowest first, absent beneath `root_fd` before this run prepares the
    journal: exactly those ensure_journal_dirs creates, so a refusal before the transaction opens can
    remove them again (_remove_journal_dirs)."""
    parts = JOURNAL_REL.split("/")
    for i in range(len(parts)):
        try:
            present = _journal._lstat_contained(root_fd, "/".join(parts[:i + 1]))
        except (_journal.JournalError, OSError) as exc:
            raise AdoptApplyError("cannot inspect the adoption journal ({}); fail-closed".format(exc))
        if present is None:
            return ["/".join(parts[:j + 1]) for j in range(i, len(parts))]
    return []


def _remove_journal_dirs(root_fd, created):
    """Deepest first, remove the journal directories this run created, once its refusal has released the
    lock, so a refusal whose sweep returns ([], []) leaves the tree as it found it (the caller names
    anything else). rmdir only, never forced: a directory that is no
    longer empty (a transaction record, a retained or concurrent run's lock) stays in place. A directory
    the walk cannot reach (an ancestor it cannot open, such as one created with mode 000 under a 0777
    umask) or cannot remove never ends the sweep: every shallower one is still attempted, since an empty
    ancestor is removable through its own parent. Returns (stays, unconfirmed): what stays, each with its
    reason, omitting anything beneath a directory this sweep removed or found absent (an rmdir succeeds
    only on an empty directory); and what it removed whose parent fsync then failed, so the removal may
    not be durable. ([], []) when the tree is as found."""
    left = []
    unconfirmed = []
    gone = []
    for rel in reversed(created):
        try:
            pfd, name = _journal._open_parent(root_fd, rel)
        except FileNotFoundError:
            continue    # never created: a preparation that failed part-way
        except (_journal.JournalError, OSError) as exc:
            left.append((rel, "not reached: {}".format(exc)))
            continue
        try:
            try:
                os.rmdir(name, dir_fd=pfd)
            except FileNotFoundError:
                gone.append(rel)
                continue
            except OSError as exc:
                left.append((rel, "not removed: {}".format(exc)))
                continue
            gone.append(rel)
            try:
                os.fsync(pfd)
            except OSError as exc:
                unconfirmed.append("{} (parent fsync failed: {})".format(rel, exc))
        finally:
            _journal._close_fd_quietly(pfd)
    return (["{} ({})".format(rel, why) for rel, why in left
             if not any(rel.startswith(g + "/") for g in gone)], unconfirmed)


_NOTHING_WRITTEN = "nothing written (fail-closed)"
# The trailing claims a refusal reason may carry from a shared helper. _refusal_text strips them, so a
# refusal of run_adopt_transaction carries only the claim derived from its observation.
_CLAIM_TAILS = ("; " + _NOTHING_WRITTEN, "; nothing was written (fail-closed)", " (fail-closed)", "; fail-closed")


def _entry_kind(st):
    if stat.S_ISDIR(st.st_mode):
        return "directory"
    if stat.S_ISREG(st.st_mode):
        return "file"
    return "symlink" if stat.S_ISLNK(st.st_mode) else "special entry"


def _journal_listing(root_fd):
    """A no-follow listing of the adoption journal tree beneath the held product-root descriptor: each
    JOURNAL_REL component, the direct entries of each ancestor, and every entry beneath the journal root,
    keyed by relative path to (type, st_dev, st_ino). A directory the walk traverses is keyed by the fstat
    of the descriptor it opened, never by the stat by name before the open: one whose identity differs
    between the two (swapped in between) is keyed to ("unlisted", reason) and not traversed. A directory
    the walk cannot open or list is keyed `<dir>/` to ("unlisted", reason), a key no entry name can take
    (a name is never empty), so a listing never stands for what it did not see."""
    found = {}

    def opened_as(rel, st, fd):
        """True when the descriptor `fd` opened is the directory `st` stated, keying `rel` by that
        descriptor's fstat; else `rel` is keyed unlisted, naming the change."""
        try:
            fst = os.fstat(fd)
        except OSError as exc:
            found[rel] = ("unlisted", str(exc))
            return False
        if not stat.S_ISDIR(fst.st_mode) or (fst.st_dev, fst.st_ino) != (st.st_dev, st.st_ino):
            found[rel] = ("unlisted", "it changed between this listing's stat and its open (st_dev {}, st_ino "
                          "{}, then st_dev {}, st_ino {})".format(st.st_dev, st.st_ino, fst.st_dev, fst.st_ino))
            return False
        found[rel] = (_entry_kind(fst), fst.st_dev, fst.st_ino)
        return True

    def walk(dfd, rel, deep):
        try:
            names = sorted(os.listdir(dfd))
        except OSError as exc:
            found[rel + "/"] = ("unlisted", str(exc))
            return
        for name in names:
            sub = rel + "/" + name
            try:
                st = os.stat(name, dir_fd=dfd, follow_symlinks=False)
            except OSError as exc:
                found[sub] = ("unlisted", str(exc))
                continue
            found[sub] = (_entry_kind(st), st.st_dev, st.st_ino)
            if deep and stat.S_ISDIR(st.st_mode):
                try:
                    cfd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=dfd)
                except OSError as exc:
                    found[sub + "/"] = ("unlisted", str(exc))
                    continue
                try:
                    if opened_as(sub, st, cfd):
                        walk(cfd, sub, True)
                finally:
                    _journal._close_fd_quietly(cfd)

    parts = JOURNAL_REL.split("/")
    opened = []
    try:
        cur = root_fd
        for i, comp in enumerate(parts):
            rel = "/".join(parts[:i + 1])
            try:
                st = os.stat(comp, dir_fd=cur, follow_symlinks=False)
            except FileNotFoundError:
                break
            except OSError as exc:
                found[rel] = ("unlisted", str(exc))
                break
            found[rel] = (_entry_kind(st), st.st_dev, st.st_ino)
            if not stat.S_ISDIR(st.st_mode):
                break
            try:
                cur = os.open(comp, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=cur)
            except OSError as exc:
                found[rel + "/"] = ("unlisted", str(exc))
                break
            opened.append(cur)
            if not opened_as(rel, st, cur):
                break
            walk(cur, rel, i == len(parts) - 1)
    finally:
        for fd in reversed(opened):
            _journal._close_fd_quietly(fd)
    return found


def _journal_components():
    """The JOURNAL_REL path components, shallowest first: .aiqt, .aiqt/adopt, .aiqt/adopt/journal."""
    parts = JOURNAL_REL.split("/")
    return ["/".join(parts[:i + 1]) for i in range(len(parts))]


def _entry_named(rel, seen, created, txn, mine, maybe_mine):
    """One entry present now that the run's first listing did not hold, named with what is true of it:
    (text, True when it is this run's or may be). The lock is this run's ONLY when it is the inode this
    run's acquire wrote (`mine`); with no such identity it may be this run's only while `maybe_mine` (an
    acquire of this run may have created it), else it is another run's. A changed component of the journal
    path itself may be this run's when it is a directory: its preparation may have recreated it after
    `created` was taken; it creates nothing else there."""
    named = "{} ({})".format(rel, seen[0])
    journal = JOURNAL_REL + "/"
    if rel in created:
        if seen[0] == "directory":
            return named + ", a journal directory this run created", True
        return named + ", now a {} where this run had created a journal directory".format(seen[0]), True
    if rel == journal + txn or rel.startswith(journal + txn + "/"):
        return named + ", in this run's transaction record {!r}, which the journal keeps".format(txn), True
    if rel == journal + "lock":
        if mine is not None and seen[1:] == mine[:2]:
            return named + ", this run's journal lock", True
        if mine is not None or not maybe_mine:
            return named + ", another run's journal lock, not this run's", False
        return named + ", a journal lock", True
    if rel in _journal_components():
        if seen[0] == "directory":
            return named + ", a component of the journal path that changed since this run began, possibly " \
                           "recreated by this run", True
        return named + ", a component of the journal path that changed since this run began, now a {}, " \
                       "which this run never creates there".format(seen[0]), False
    if (rel.startswith(journal) and "/" not in rel[len(journal):] and seen[0] != "directory"
            and rel != journal + "lock.break"):
        return named + (", a stray entry: the next run refuses on it as unreadable and reconcile() does not "
                        "touch it, so no sanctioned path clears it and it stays"), False
    return named, False


def _observed(before, after, created, txn, mine=None, maybe_mine=False):
    """What the run's closing listing shows against its first one: ([], False) when the two are equal and
    each saw everything, else (clauses, ours). A directory EITHER listing could not open or list makes the
    journal not fully observed, which is named first and always stands in place of the claim, even when
    both listings failed alike. Then each entry present now that was not (or not as the same type and
    inode), under a lead-in that attributes them to this run only when one of them is (or may be) its
    own, then each entry gone. An entry either listing could not state hides itself and everything below
    it; a directory either listing stated but could not list (keyed `<dir>/`) hides only what is below it,
    so an entry the closing listing observed absent is always named gone. `ours` is True when any clause
    concerns what this run wrote or cannot rule out."""
    blind = {}
    for listing in (before, after):
        for rel, seen in listing.items():
            if seen[0] == "unlisted":
                blind.setdefault(rel, seen[1])
    said = []
    if blind:
        said.append("the adoption journal could not be fully observed, so what this run left there is not "
                    "known: {}".format("; ".join("{} could not be listed ({})".format(rel, why)
                                                 for rel, why in sorted(blind.items()))))
    named = [_entry_named(rel, after[rel], created, txn, mine, maybe_mine)
             for rel in sorted(rel for rel in after if rel not in blind and after[rel] != before.get(rel))]
    ours = bool(blind) or any(own for _text, own in named)
    if named:
        said.append(("the adoption journal now holds entries it did not hold when this run began: " if ours
                     else "the adoption journal now holds entries it did not hold when this run began, not "
                     "attributed to this run: ") + ", ".join(text for text, _own in named))
    gone = sorted(rel for rel in before if rel not in after and not any(
        rel.startswith(key) if key.endswith("/") else rel == key or rel.startswith(key + "/")
        for key in blind))
    if gone:
        said.append("entries present when this run began are gone: {}".format(", ".join(gone)))
    return said, ours


def _journal_unbound(jr_fd, held, after):
    """None when the closing listing observed the journal directory this run wrote to: the journal path
    still resolves to the identity (st_dev, st_ino) of the descriptor the run held, or it is absent and
    that held directory is unlinked (this run's cleanup removed it, empty). Else the clause saying the
    binding changed, or could not be observed (the listing could not see the journal path or one of its
    ancestors), in place of the claim: what the directory this run wrote to now holds is unobserved.
    Called while the descriptor is still held, so its inode cannot be reused by a later directory."""
    if jr_fd is None or held is None:
        return None
    now = after.get(JOURNAL_REL)
    if now is not None and now[0] == "directory" and now[1:] == held:
        return None
    if now is None:
        try:
            if os.fstat(jr_fd).st_nlink == 0:
                return None
        except OSError:
            pass
    if now is not None and now[0] == "unlisted" or now is None and any(
            after.get(r, ("",))[0] == "unlisted" or after.get(r + "/", ("",))[0] == "unlisted"
            for r in _journal_components()[:-1]):
        return ("the adoption journal path {} could not be observed, so whether it still resolves to the "
                "journal directory this run wrote to (st_dev {}, st_ino {}), and what that directory holds, "
                "is not known".format(JOURNAL_REL, held[0], held[1]))
    return ("the adoption journal path {} no longer resolves to the journal directory this run wrote to "
            "(st_dev {}, st_ino {}), so what that directory holds was not observed".format(
                JOURNAL_REL, held[0], held[1]))


def _refusal_text(head, observed=None, ours=True):
    """THE composer of every refusal of run_adopt_transaction: the reason, stripped of any trailing claim a
    shared helper wrote, then "nothing written" ONLY when `observed` is [] (the run's two journal listings
    are equal and complete, bound to the journal directory it wrote to, its cleanup durable, and its lock
    released durably or never taken), else the observed leftovers in place of it, said to be this run's
    only when `ours` (something observed is, or may be, its own). No observation (None) makes no claim."""
    for tail in _CLAIM_TAILS:
        if head.endswith(tail):
            head = head[:-len(tail)]
            break
    if observed is None:
        return head + " (fail-closed)"
    if not observed:
        return head + "; " + _NOTHING_WRITTEN
    if not ours:
        return "{}; {} (fail-closed)".format(head, "; ".join(observed))
    return "{}; NOT everything this run wrote is confirmed undone: {} (fail-closed)".format(
        head, "; ".join(observed))


def _unreadable_lock(exc, at_acquire=False):
    """The clause for a journal lock that cannot be read: never blind-removed, refused by the next run and
    by reconcile() alike, so no sanctioned path clears it, and the clause says so and names it. Only on
    the acquire path can it be this run's own unfinished write."""
    return ("a journal lock is present but unreadable ({}){}; it is never blind-removed, the next run and "
            "reconcile() both refuse on it, and no sanctioned path clears it, so {}/lock STAYS".format(
                exc, ", possibly this run's own unfinished write" if at_acquire else "", JOURNAL_REL))


def _lock_identity(jr_fd):
    """The journal lock beneath the held journal descriptor, no-follow: (st_dev, st_ino, its bytes), or
    None when absent. JournalError or OSError when what is present cannot be read."""
    try:
        lfd = os.open("lock", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=jr_fd)
    except FileNotFoundError:
        return None
    try:
        st = os.fstat(lfd)
        if not stat.S_ISREG(st.st_mode):
            raise _journal.JournalError("journal lock is not a regular file (fail-closed)")
        return st.st_dev, st.st_ino, _journal._read_fd(lfd, cap=_journal._MAX_JOURNAL_READ_BYTES)
    finally:
        _journal._close_fd_quietly(lfd)


def _release_outcome(jr_fd, journal_root, mine):
    """Release this run's own journal lock (ownership-checked) and read back, beneath the held journal
    descriptor, what that left, judged by the lock's identity, never by process identity: ("stays", why)
    ONLY when the lock present is the inode and content this run's acquire wrote (`mine`); ("unidentified",
    None) when a lock is present and `mine` is None; ("unreadable", error) when what is present cannot be
    read; ("unconfirmed", why) when no lock of this run's is present but the release raised or was
    interrupted, so it may not be durable; else ("released", None). The third element is an interrupt (any
    other BaseException) raised inside the release: the read-back still runs, and the caller re-raises
    it with the outcome attached. ("altered", why) when the inode this run's acquire wrote is present with
    other or malformed content: this run's lock was altered and stays, never presumed released.
    Residual (disclosed): the identity is the inode and the bytes acquire_lock writes, which carry no value
    unique to one acquire (uid, pid, pid-start, session, utc to the second); another acquire in this same
    process, with this session, in the same second, that is given back the freed inode reads as this run's
    lock (only ever over-reported as staying). A per-acquire token would change the journal lock format
    other readers validate, so none is added here."""
    failed = interrupt = None
    try:
        _journal.release_lock(journal_root)
    except (_journal.JournalError, OSError) as exc:
        failed = exc
    except BaseException as exc:
        interrupt = exc
        failed = "its release was interrupted ({!r})".format(exc)
    try:
        now = _lock_identity(jr_fd)
    except (_journal.JournalError, OSError) as exc:
        return "unreadable", exc, interrupt
    if now is not None and mine is not None and now == mine:
        return "stays", failed or "its release left it", interrupt
    if now is not None and mine is not None and now[:2] == mine[:2]:
        return "altered", "the inode its acquire wrote now holds other content{}".format(
            "; {}".format(failed) if failed is not None else ""), interrupt
    if now is not None and mine is None:
        return "unidentified", None, interrupt
    if failed is not None:
        return "unconfirmed", failed, interrupt
    return "released", None, interrupt


def _release_note(jr_fd, journal_root, mine):
    """The run's normal lock release, at the end of a transaction or a refusal under the lock: (state,
    clause, interrupt), the clause None when the lock is released, else naming what stays or what may not
    be durable, reported as _failed_lock_state reports it on the acquire path, never swallowed."""
    state, detail, interrupt = _release_outcome(jr_fd, journal_root, mine)
    if state == "released":
        return state, None, interrupt
    if state == "unreadable":
        return state, _unreadable_lock(detail), interrupt
    if state == "unidentified":
        return state, ("a journal lock {}/lock is present after this run's release, and this run could not "
                       "read back the lock it wrote to tell whether it is its own: the next run refuses on "
                       "it".format(JOURNAL_REL)), interrupt
    if state == "unconfirmed":
        return state, ("this run's journal lock was released, but the release may not be durable "
                       "({})".format(detail)), interrupt
    if state == "altered":
        return state, _altered_lock(detail), interrupt
    return state, ("this run's journal lock STAYS ({}): the next run refuses on it, and reconcile() breaks "
                   "it once this process has exited".format(detail)), interrupt


def _altered_lock(detail):
    """The clause for this run's lock altered in place: an unresolved outcome, never a release."""
    return ("this run's journal lock was altered and stays ({}/lock: {}); it is not released, and the next "
            "run refuses on it".format(JOURNAL_REL, detail))


def _interrupted_lock_state(jr_fd, exc):
    """What an interrupt, or another exception `exc`, inside acquire_lock left, OBSERVED beneath the held
    journal descriptor, never presumed: (state, clause). No lock present is "untaken"; a present lock this
    run cannot tell from another's (`exc` may have come before or after its create) is named and stays. An
    Exception is described as an error, only any other BaseException as an interrupt."""
    try:
        now = _lock_identity(jr_fd)
    except (_journal.JournalError, OSError) as exc:
        return "unreadable", _unreadable_lock(exc, at_acquire=True)
    if now is None:
        return "untaken", None
    return "unidentified", ("a journal lock {}/lock is present after this run's lock acquire {}, and this run "
                            "cannot tell whether it is its own: it stays, and the next run refuses on "
                            "it".format(JOURNAL_REL, "failed with an error ({!r})".format(exc)
                                        if isinstance(exc, Exception) else "was interrupted"))


def _failed_lock_state(jr_fd, journal_root, error):
    """What an acquire_lock that failed with an OSError left, read beneath the held journal descriptor, so
    the refusal reports it precisely: (state, clause, interrupt) as _release_note's. acquire_lock creates
    the lock BEFORE it writes and synchronizes it, so such a failure can leave this run's own lock, and it
    says so (error.lock_created). Only a lock this call created is released again (ownership-checked, as
    every refusal under the lock releases it), its identity taken first, and what stays, or a release
    whose durability is unconfirmed, is named. A lock this call did not create (another run's, another
    thread's of this same process, or any lock when the create itself failed) is never touched, nor is
    an unreadable one."""
    if not getattr(error, "lock_created", False):
        return "untaken", "this run left no journal lock (its create failed), and no lock present is touched", None
    try:
        owner = _journal.read_lock_owner_at(jr_fd)
        mine = _lock_identity(jr_fd)
    except (_journal.JournalError, OSError) as exc:
        return "unreadable", _unreadable_lock(exc, at_acquire=True), None
    if owner is None:
        return "untaken", "this run left no journal lock", None
    if not _journal._owner_is_current(owner):
        return "untaken", ("the journal lock present is another run's (pid {}), not this "
                           "run's".format(owner.get("pid"))), None
    state, detail, interrupt = _release_outcome(jr_fd, journal_root, mine)
    if state == "released":
        return state, "this run's own journal lock WAS created, then released again", interrupt
    if state == "unreadable":
        return state, _unreadable_lock(detail), interrupt
    if state == "unconfirmed":
        return state, ("this run's own journal lock WAS created, then released again, but the release may "
                       "not be durable ({})".format(detail)), interrupt
    if state == "altered":
        return state, _altered_lock(detail), interrupt
    return state, ("this run's own journal lock WAS created and STAYS ({}): the next run refuses on it, and "
                   "reconcile() breaks it once this process has exited".format(detail)), interrupt


def _lock_said(lock_state, lock_note):
    """The lock outcome as one clause: the release's own clause when it named one, else the state's."""
    return lock_note or {"released": "this run's journal lock was released",
                         "untaken": "this run holds no journal lock",
                         "retained": "the journal lock is retained"}.get(
                             lock_state, "this run's journal lock outcome was not observed")


def run_adopt_transaction(product_root, run_id, compose, phase=None):
    """ONE journaled adoption transaction, the run's base transaction or one later phase's, through the
    shared 9.3 engine. Refusals BEFORE anything is written, in order: containment, a non-clean journal
    (reconcile-first: the adoption journal is inspected FIRST, per the module docstring), the store
    posture (no lease join, spec 5.7), an existing transaction of this run and phase, and for a phase
    anything but a committed, INTENT-digest-matched base inventory.toml (spec 4.2). Then, under the journal
    lock so observation and the journal's own capture are contiguous, compose(ops) fills a fresh ApplyOps
    against the live tree, the derived inventory seals it, and check_apply_ops re-proves every invariant;
    a refusal there releases the lock and removes the journal directories this run created
    (_remove_journal_dirs). EVERY refusal here is worded by ONE composer (_refusal_text), and the claim
    "nothing written" is DERIVED, never hand-written: the run takes a no-follow listing of the adoption
    journal tree (_journal_listing) before it creates anything and again at the refusal, after its cleanup,
    and the claim stands ONLY when the two are equal and each saw the whole tree, the journal path still
    resolves to the directory this run held (_journal_unbound, checked before that descriptor is closed),
    the cleanup durable, and the lock released durably (or never taken); otherwise the refusal names what
    could not be observed and every entry present now that was not before, attributed to this run only
    when one is (or may be) its own (a
    pre-INTENT transaction directory, its frames.log and preimages included), what its cleanup left or may
    not have made durable, and what its lock release left, in place of the claim. A leftover only the
    reconcile-first discipline clears is left to it rather than to hand removal, and one no sanctioned
    path clears is named as such. The lock release is read back by identity (the inode and content this
    run's acquire wrote, _lock_identity), never by process identity; at the end of a committed transaction
    a release that leaves the lock or may not be durable raises AdoptCommittedLockError, whose changes
    LANDED. An interrupt (any BaseException but a refusal), one raised inside the release included,
    propagates as itself, with the transaction state and the observed outcome attached as a note.
    Disclosed too: a concurrent run that
    prepared the journal but has not yet taken its lock can find the directory removed by that cleanup,
    and then refuses at the lock. A failure that may have left the transaction open RETAINS the lock (and
    the journal) so every later run refuses into reconcile().
    A transaction opened from inside the stage driver's composition (a plan op handler opening its own)
    refuses before anything else: one run takes one base transaction. Returns the transaction name."""
    denied = getattr(_COMPOSITION, "denied", None)
    if denied is not None:
        denied.append("a nested adoption transaction")
        raise AdoptApplyError(_refusal_text("a plan op handler may not open its own adoption transaction: every "
                                            "op composes into the driver's one base transaction (spec 14.1, "
                                            "14.2)"))
    txn = _txn_name(run_id, phase)
    if not callable(compose):
        raise AdoptApplyError(_refusal_text("compose must be a callable that fills the transaction's ApplyOps"))
    root_fd = _open_product_root(product_root)
    journal_root = _journal_root(product_root)
    jr_fd = jr_id = mine = before = interrupted = None
    maybe_mine = False
    created = []
    failure = None
    lock_state, lock_note = "untaken", None
    held = retain = done = entered = False
    try:
        try:
            _journal.require_containment()
        except _journal.JournalError as exc:
            raise AdoptApplyError("{} (fail-closed)".format(exc))
        before = _journal_listing(root_fd)     # BEFORE this run creates anything
        journal_clean_or_refuse(root_fd, journal_root)
        _store_posture_or_refuse(product_root)
        try:
            prior = _journal._lstat_contained(root_fd, JOURNAL_REL + "/" + txn)
        except (_journal.JournalError, OSError) as exc:
            raise AdoptApplyError("cannot inspect the adoption journal ({}); fail-closed".format(exc))
        if prior is not None:
            raise AdoptApplyError("run {} already has its transaction {!r}: one run takes one transaction "
                                  "per phase, and changing approved work takes a fresh plan with its own run "
                                  "id (spec 14.1)".format(run_id, txn))
        if phase is not None:
            _committed_base_or_refuse(root_fd, journal_root, run_id, phase)
        created = _absent_journal_dirs(root_fd)
        try:
            _journal.ensure_journal_dirs(root_fd, JOURNAL_REL)
            jr_fd = _journal.open_journal_root_fd(root_fd, JOURNAL_REL)
            jr_st = os.fstat(jr_fd)
            jr_id = (jr_st.st_dev, jr_st.st_ino)    # the journal directory this run writes to
        except (_journal.JournalError, OSError) as exc:
            raise AdoptApplyError("cannot prepare the adoption journal {} ({})".format(JOURNAL_REL, exc))
        try:
            maybe_mine = True
            try:
                _journal.acquire_lock(journal_root, SESSION_ID)
            except _journal.JournalError as exc:   # the O_EXCL create refused: the lock was never this run's
                maybe_mine = False
                raise AdoptApplyError("cannot take the adoption journal lock ({})".format(exc))
            except OSError as exc:
                maybe_mine = getattr(exc, "lock_created", False)
                lock_state, clause, interrupted = _failed_lock_state(jr_fd, journal_root, exc)
                if interrupted is not None or lock_state not in ("untaken", "released"):
                    lock_note = clause      # named once, by the composer, in place of the claim
                    if interrupted is not None:
                        raise interrupted
                    raise AdoptApplyError("cannot take the adoption journal lock ({})".format(exc))
                raise AdoptApplyError("cannot take the adoption journal lock ({}); {}".format(exc, clause))
            except BaseException as exc:   # an interrupt or other error inside acquire: observed, never presumed
                lock_state, lock_note = _interrupted_lock_state(jr_fd, exc)
                raise
            held = True
            lock_state = "held"
            try:
                mine = _lock_identity(jr_fd)    # what THIS acquire wrote, so the release reads back by identity
                maybe_mine = mine is None
            except (_journal.JournalError, OSError) as exc:
                raise AdoptApplyError("cannot read back the adoption journal lock this run took ({})".format(exc))
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
            ops = _compose_checked(root_fd, run_id, phase, compose)
            staged = dict(ops.staged)

            def staged_reader(op):
                data = staged.get(op["path"])
                if not isinstance(data, bytes):
                    # raised INSIDE apply, so the engine rolls back rather than leaving an open transaction.
                    raise _journal.JournalError("no staged bytes for {!r} (fail-closed)".format(op["path"]))
                return data

            header = dict(kind=KIND, run_id=run_id, phase=phase or "base", operation=OPERATION)
            entered = True      # from here the journal may hold this transaction's records by design
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
                    entered = False
                    raise AdoptApplyError("the adoption transaction was refused before it opened "
                                          "({})".format(exc))
                if state == "rolled-back":
                    raise AdoptApplyError("the adoption transaction was refused and rolled back to the "
                                          "prestate ({}): the product tree is as it was, and the journal "
                                          "keeps this transaction's terminal record".format(exc))
                retain = True
                lock_state = "retained"
                raise AdoptApplyError("the adoption transaction {} FAILED and is {} ({}); the journal lock "
                                      "is retained so the next run refuses into reconcile() "
                                      "(fail-closed)".format(txn, state or "in an unreadable state", exc))
            done = True
            return txn
        finally:
            if held and not retain:
                lock_state, lock_note, interrupted = _release_note(jr_fd, journal_root, mine)
                if interrupted is not None:
                    raise interrupted
    except BaseException as exc:
        failure = exc
        raise
    finally:
        left, unconfirmed = [], []
        observed = said = None
        ours = True
        try:
            if created and not (done or retain):
                left, unconfirmed = _remove_journal_dirs(root_fd, created)
            if before is not None and not done:
                said = [lock_note] if lock_note else []
                if (left or unconfirmed) and not entered:
                    if left:
                        said.append("the refusal's cleanup is INCOMPLETE: journal directories this run created "
                                    "STAY: {}".format("; ".join(left)))
                    if unconfirmed:
                        said.append("the refusal's cleanup is UNCONFIRMED: journal directories this run created "
                                    "were removed, but the removal may not be durable: {}".format(
                                        "; ".join(unconfirmed)))
                    said[-1] += (". They are left to the reconcile-first discipline, never to hand removal: a "
                                 "later run inspects the adoption journal before it writes, reuses these "
                                 "directories, and refuses into reconcile() on a held lock or an open "
                                 "transaction there")
                if lock_state not in ("untaken", "released") and not lock_note:
                    said.append(_lock_said(lock_state, lock_note))
                after = _journal_listing(root_fd)
                delta, ours = _observed(before, after, created, txn, mine, maybe_mine)
                unbound = _journal_unbound(jr_fd, jr_id, after)
                ours = ours or bool(said) or unbound is not None
                observed = said + ([unbound] if unbound else []) + delta
        finally:
            if jr_fd is not None:   # held through the closing observation, so its identity stays this run's
                _journal._close_fd_quietly(jr_fd)
            store._close_fd_exc_safe(root_fd)
        if failure is not None and (not isinstance(failure, Exception) or failure is interrupted):
            # an interrupt still propagates as itself, with the transaction state and the outcome named
            if failure is interrupted or observed:
                if done:
                    phase_said = "the adoption transaction {} COMMITTED: its product-tree changes LANDED".format(txn)
                elif entered:
                    phase_said = ("the adoption transaction {} was entered and is NOT confirmed committed or "
                                  "undone".format(txn))
                else:
                    phase_said = "the adoption transaction {} did not open".format(txn)
                lock_said = _lock_said(lock_state, lock_note)
                failure.add_note("; ".join([phase_said] + ([] if lock_said in (observed or []) else [lock_said])
                                           + (observed or [])) + " (fail-closed)")
        elif failure is None:
            if lock_note:
                raise AdoptCommittedLockError(txn, lock_note)
        elif isinstance(failure, AdoptApplyError):
            text = _refusal_text(str(failure), observed, ours)
            if text != str(failure):
                raise AdoptApplyError(text) from failure
        elif said:
            raise AdoptApplyError(_refusal_text(str(failure), observed, ours)) from failure
        elif observed:
            failure.add_note("; ".join(observed) + " (fail-closed)")   # not a refusal: it propagates as itself


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
    return _init_store_bundle_listed(root_fd, model, run_id)


def _init_store_bundle_listed(root_fd, model, run_id):
    """{path: digest} of `run_id`'s bundle files among the observed `.working` entries: the
    bundle-root inventories plus the payloads they list. The caller has ALREADY verified the
    bundle (verify_bundle / _verify_bundle_at); an unreadable or malformed inventory still
    refuses here, never reads as an empty listing."""
    import _opf_check
    home = evidence_home_rel(run_id)
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


def _init_store_prior_committed_ops(jr_fd, journal_root, prior):
    """(creates, archivals) a PRIOR adoption run committed in this journal: creates maps path to
    the bare-hex content sha256 of every `create` op across the prior run's COMPLETE
    transactions (its init-store transaction, which publishes no adoption artefact, excluded);
    archivals lists (source_path, hex_sha256, preservation) for every pinned `remove` whose
    preservation copy the SAME complete transaction created with the same digest -- the
    committed preserve-first archival shape of spec 14.2. Raises on an unreadable journal
    (fail-closed, never an empty answer)."""
    creates, archivals = {}, []
    for txn_dir in _journal._journal_txn_dirs(jr_fd, journal_root):
        name = txn_dir.name
        if name != prior and not name.startswith(prior + "."):
            continue
        if name == _txn_name(prior, INIT_STORE_PHASE):
            continue
        if _journal.classify_state(jr_fd, txn_dir) != "complete":
            continue
        frames, _torn, _good = _journal.read_frames(jr_fd, txn_dir)
        intent = _journal._first(frames, _journal.F_INTENT)
        ops = intent.get("ops") if isinstance(intent, dict) else None
        if not isinstance(ops, list):
            continue
        txn_creates = {}
        for op in ops:
            if isinstance(op, dict) and op.get("op") == "create" \
                    and isinstance(op.get("path"), str):
                hexd = (op.get("poststate") or {}).get("content-sha256")
                if isinstance(hexd, str):
                    txn_creates[op["path"]] = hexd
        for op in ops:
            if not (isinstance(op, dict) and op.get("op") == "remove"
                    and isinstance(op.get("path"), str)):
                continue
            pin = (op.get("source-poststate") or {}).get("sha256")
            try:
                pres = archive_rel(prior, op["path"])
            except AdoptApplyError:
                continue
            if isinstance(pin, str) and txn_creates.get(pres) == pin:
                archivals.append((op["path"], pin, pres))
        creates.update(txn_creates)
    return creates, archivals


def _init_store_prior_run_paths(root_fd, run_id, model, machine, jr_fd, journal_root):
    """{path: digest} of PRIOR REVERSED runs' committed artefacts admissible beneath the control
    homes, plus whether a proven prior run's COMMITTED archival emptied the machine directory
    (claude round 4, F1). Spec 14.1 still burns a reversed run id -- a reversed run never
    reruns -- but a FRESH run may continue over the prior run's committed work, admitted ONLY
    when each artefact is PROVEN, per prior run id a control-home file names: (1) that run's
    init-store transaction is terminal ROLLED-BACK in this adoption journal (the one sanctioned
    way its id was burnt), (2) its evidence bundle verifies (_verify_bundle_at for that run id,
    re-reading every listed payload), and (3) EVERY listed artefact lies within that run's
    RETAINED-EVIDENCE domains -- the SAME _evidence_eligible predicate the composer derives
    inventory rows from (its own bundle, its own adoption archive, or a default Move
    destination), plus its own bundle-root inventories -- and was created by a transaction
    COMPLETE in this journal, its live
    bytes digest-matched (so a hand-planted or tampered artefact is never admitted). The whole
    prior bundle is admitted or none of it. The machine directory is admitted only on such a
    prior run's COMMITTED archival: a complete transaction that created the preservation copy
    and removed a machine-store source pinned to the same digest, the copy live and admitted.
    Anything not so proven stays foreign and refuses; no journal descriptor proves nothing
    (fail-closed)."""
    out, machine_archived = {}, False
    if jr_fd is None or journal_root is None:
        return out, machine_archived
    priors = set()
    for entry in model["entries"]:
        if entry["kind"] != "file":
            continue
        for home in _CONTROL_HOMES:
            prefix = home + "/" + KIND + "/"
            if entry["path"].startswith(prefix):
                rid = entry["path"][len(prefix):].split("/", 1)[0]
                if rid != run_id and is_run_id(rid):
                    priors.add(rid)
    for prior in sorted(priors):
        txn = _txn_name(prior, INIT_STORE_PHASE)
        try:
            if _journal._lstat_contained(root_fd, JOURNAL_REL + "/" + txn) is None:
                continue
            if _journal.classify_state(jr_fd, journal_root / txn) != "rolled-back":
                continue
        except (_journal.JournalError, OSError) as exc:
            raise AdoptApplyError("cannot classify prior run {}'s init-store transaction ({}); "
                                  "fail-closed".format(prior, exc))
        if _verify_bundle_at(root_fd, prior, evidence_home_rel(prior)).status != store.VALID:
            continue
        try:
            creates, archivals = _init_store_prior_committed_ops(jr_fd, journal_root, prior)
        except (_journal.JournalError, OSError) as exc:
            raise AdoptApplyError("cannot search the adoption journal for prior run {}'s "
                                  "committed transactions ({}); fail-closed".format(prior, exc))
        listed = _init_store_bundle_listed(root_fd, model, prior)
        # The admissible domains are EXACTLY the retained-evidence domains this module already
        # defines (_evidence_eligible: own bundle, own adoption archive, default Move
        # destinations -- never re-spelled here) plus the prior run's own bundle-root
        # inventories, which _evidence_eligible deliberately excludes from derived rows but
        # which the committed transaction created all the same (codex round 5, finding 1: a
        # committed Move payload is retained evidence and must not strand the bundle).
        admitted = {path: digest for path, digest in listed.items()
                    if (_evidence_eligible(prior, path) or _bundle_root_inventory(prior, path))
                    and creates.get(path) == _plan_hex(digest)}
        if not admitted or set(admitted) != set(listed):
            continue   # an artefact not proven committed keeps the whole bundle foreign
        out.update(admitted)
        for source_path, hexd, pres in archivals:
            if _within(source_path, machine) and admitted.get(pres) == "sha256:" + hexd:
                machine_archived = True
    return out, machine_archived


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
    alone proves planned work, not completed archival. A PRIOR run's committed artefacts (its
    verified evidence bundle and archive copies) are admitted, and the machine home with them,
    only through _init_store_prior_run_paths: that run's init-store transaction terminal
    ROLLED-BACK, its bundle verified, every artefact created by a COMPLETE transaction of this
    journal, and the machine home only on its committed archival -- so a fresh run continues a
    reversed run's committed work without re-running its burnt id (claude round 4, F1;
    spec 14.1). Only then is the emptied, pre-existing
    directory admitted as a pre-existing planned directory, its mode pre-checked HERE (before
    the run's intent opens, so a wrong-mode tree never burns the run id) at the FULL
    stat.S_IMODE the substrate compares: the machine directory against the planned mode
    (codex round 4), and `.working` with admitted files against the substrate's own two
    facts -- it records `.working`'s observed mode MASKED to 0o777 and re-compares the full
    S_IMODE against that record, so a special bit (a setgid 02755) can never pass, and it
    refuses a group- or world-writable mode outright (claude round 5, N2). The substrate
    re-verifies and preserves what it admits.
    Every consumed
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
    prior, prior_machine = _init_store_prior_run_paths(root_fd, plan["run_id"], model, machine,
                                                       jr_fd, journal_root)
    admitted, foreign = [], []
    for entry in model["entries"]:
        if entry["kind"] != "file":
            continue
        path = entry["path"]
        want = frozen.get(path) or copies.get(path) or bundle.get(path) or prior.get(path)
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
    if machine in stray and (occ_rows or prior_machine):
        # Spec 14.2: "After the archival the destination is an ordinary managed path: apply
        # initializes the machine file or renders the view immediately." The archival removed the
        # occupying FILE; its pre-existing parent directory survives (the shell never removes a
        # pre-existing directory) and holds no file (one would have refused as foreign above).
        # The admission is derived from VERIFIED ARCHIVAL EVIDENCE, never the planned row alone:
        # every occupying machine row's preservation copy must lie LIVE at its recorded
        # destination, digest-matched (it was admitted above), and a COMMITTED adoption
        # transaction of this journal must record that archival preserve-first -- or, with no
        # occupying row, a PRIOR reversed run's PROVEN committed archival emptied the directory
        # (_init_store_prior_run_paths, claude round 4 F1). Only then is the
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
        # the run id never burnt; the substrate re-proves the same fact under its mutex. The
        # comparison reads the SAME full stat.S_IMODE value the substrate compares (codex
        # round 4, finding 2): observe_inventory's entries mask to 0o777, so a special bit
        # (a setgid 02755 directory) would otherwise pass here and burn the run id on the
        # substrate's own post-intent refusal.
        for dpath in admitted_dirs:
            try:
                dst = os.stat(dpath, dir_fd=root_fd, follow_symlinks=False)
            except OSError as exc:
                raise AdoptApplyError("cannot stat {} ({}); fail-closed".format(dpath, exc))
            if not stat.S_ISDIR(dst.st_mode):
                raise AdoptApplyError("admitted pre-existing path {} is no longer a directory; "
                                      "fail-closed (nothing written)".format(dpath))
            if dpath == store.WORKING_DIRNAME and admitted:
                # With admitted files the substrate records `.working`'s observed mode MASKED
                # to 0o777 and re-compares the FULL stat.S_IMODE against that record under its
                # mutex, and refuses a group- or world-writable mode; the SAME two facts are
                # pre-checked HERE so a special-bit or writable `.working` refuses BEFORE the
                # run's intent opens and never burns the run id (claude round 5, N2).
                wmode = stat.S_IMODE(dst.st_mode)
                if wmode != (wmode & 0o777):
                    raise AdoptApplyError("admitted pre-existing directory {} holds mode {}, "
                                          "whose special bit the substrate's 0o777-masked "
                                          "recorded mode can never match; it is preserved as "
                                          "found, never rewritten, and refused BEFORE the "
                                          "run's intent opens (nothing written)".format(
                                              dpath, "%o" % wmode))
                if wmode & 0o022:
                    raise AdoptApplyError("the admitted {} directory mode {} is group- or "
                                          "world-writable; a store is never published beneath "
                                          "a home writable by others, and it is refused "
                                          "BEFORE the run's intent opens (nothing "
                                          "written)".format(dpath, "%o" % wmode))
                continue
            if stat.S_IMODE(dst.st_mode) != init_op.DIR_MODE:
                raise AdoptApplyError("admitted pre-existing directory {} holds mode {}, not "
                                      "the planned {}; it is preserved as found, never "
                                      "rewritten, and refused BEFORE the run's intent opens "
                                      "(nothing written)".format(
                                          dpath, "%o" % stat.S_IMODE(dst.st_mode),
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


def _fsync_surviving_parent(root_fd, rel):
    """Make the ABSENCE of the cleanup target `rel` durable: fsync its nearest SURVIVING
    ancestor directory -- the directory whose entry set records the removal -- walking up past
    ancestors that were themselves removed, to the product root if need be. A restarted scrub
    cannot tell a target that never existed from one whose removal a crash separated from its
    parent fsync, so the barrier is (re-)established for EVERY absent cleanup target before the
    operation record may be discarded (codex round 4, finding 1). Fail-closed: an ancestor that
    cannot be classified or fsynced refuses, never reads as durable."""
    parts = rel.split("/")
    keep = 0
    for i in range(len(parts) - 1, 0, -1):
        prefix = "/".join(parts[:i])
        try:
            st = _journal._lstat_contained(root_fd, prefix)
        except (_journal.JournalError, OSError) as exc:
            raise AdoptApplyError("cannot observe {} to make {}'s absence durable ({}); "
                                  "fail-closed".format(prefix, rel, exc))
        if st is None:
            continue
        if not stat.S_ISDIR(st.st_mode):
            raise AdoptApplyError("the surviving ancestor {} of the absent cleanup target {} is "
                                  "not a directory; fail-closed".format(prefix, rel))
        keep = i
        break
    try:
        # _open_parent opens the components BEFORE the last, so handing it the surviving
        # ancestor plus its (removed) child component opens exactly that ancestor, contained.
        pfd, _name = _journal._open_parent(root_fd, "/".join(parts[:keep + 1]))
    except (_journal.JournalError, OSError) as exc:
        raise AdoptApplyError("cannot open the surviving parent of the absent cleanup target {} "
                              "({}); fail-closed".format(rel, exc))
    try:
        os.fsync(pfd)
    finally:
        _journal._close_fd_quietly(pfd)


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
    operation record (its journals and outcomes stay, preserved attempt evidence). Each removal's
    parent directory is fsynced right after its unlink or rmdir, and a cleanup target already
    ABSENT on entry -- a restarted scrub cannot tell one that never existed from one whose
    removal a crash separated from its parent fsync -- has its nearest surviving ancestor
    fsynced (_fsync_surviving_parent), so EVERY target's absence is durable BEFORE the record
    discard is issued: the record deletion cannot become durable while a cleanup removal is
    not, because each removal's durability barrier has already completed when the discard
    begins (the self-test pins this syscall order). The record is the recovery
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
                    _fsync_surviving_parent(root_fd, stage_rel)
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
            elif st is None:
                _fsync_surviving_parent(root_fd, init_op.LEASE_RELPATH)
            created = ([machine] if machine_created else []) \
                + ([store.WORKING_DIRNAME] if working_created else [])
            for rel in created:
                st = _journal._lstat_contained(root_fd, rel)
                if st is None:
                    _fsync_surviving_parent(root_fd, rel)
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
    its own run id (spec 14.1) -- and that FRESH run admits the reversed run's committed artefacts
    (its verified evidence bundle, its archive copies, and the machine directory its committed
    archival emptied) once each is proven (_init_store_prior_run_paths), so an earlier phase's
    committed work is continued, never re-done, refused as foreign, or stranded (claude round 4,
    F1).

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


# --- the dispatch table: every op but init-store and the three finish ops refuses not-yet-executable --

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
    "init-store": _init_store,
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
    Only init-store executes (given its context); the three finish ops compose into the transaction
    their context carries, and every other handler refuses with no side effect."""
    checked = schema.validate_op(op_row)
    if checked.status != store.VALID:
        return checked
    handler = OP_HANDLERS.get(op_row["op"])
    if handler is None:
        return schema.AdoptValidation(store.CANNOT_EVALUATE, [
            "op {!r} has no registered handler (dispatch roster drift; fail-closed)".format(op_row["op"])])
    return handler(op_row, context)


# --- the stage driver: plan, the one approval, apply (spec 14, 14.1) -----------------------------------

def plan_rel(run_id):
    """`<bundle>/plan.toml`: the approved frozen plan's exact bytes, which apply persists (spec 14.1)."""
    return evidence_home_rel(run_id) + "/" + PLAN_NAME


def approval_rel(run_id):
    """`<bundle>/approval.toml`: the captured approval's exact bytes, which apply persists beside its plan."""
    return evidence_home_rel(run_id) + "/" + APPROVAL_NAME


def require_clean_journal(product_root):
    """The stage driver's reconcile-first gate for plan, approve and apply alike, read-only: investigation
    does not read the adoption journal, so a stage over an interrupted transaction or a held lock refuses
    here (journal_clean_or_refuse) and directs the operator to reconcile()."""
    try:
        _journal.require_containment()
    except _journal.JournalError as exc:
        raise AdoptApplyError("{} (fail-closed)".format(exc))
    root_fd = _open_product_root(product_root)
    try:
        journal_clean_or_refuse(root_fd, _journal_root(product_root))
    finally:
        store._close_fd_exc_safe(root_fd)


def _require_unapplied(product_root, run_id):
    """Replay admission (spec 14.1: one approval, one apply), read-only and BEFORE freshness, so a second
    apply of a run refuses on this rule itself, never on whatever drift its own first apply left behind: a
    run whose base transaction the adoption journal already holds, in any state, refuses.
    run_adopt_transaction re-proves the rule under the journal lock."""
    txn = _txn_name(run_id, None)
    root_fd = _open_product_root(product_root)
    try:
        prior = _journal._lstat_contained(root_fd, JOURNAL_REL + "/" + txn)
    except (_journal.JournalError, OSError) as exc:
        raise AdoptApplyError("cannot inspect the adoption journal ({}); fail-closed".format(exc))
    finally:
        store._close_fd_exc_safe(root_fd)
    if prior is not None:
        raise AdoptApplyError("run {} already has its transaction {!r}: an approved plan applies at most once, "
                              "and changing approved work takes a fresh plan with its own run id (spec 14.1); "
                              "nothing written (fail-closed)".format(run_id, txn))


def observe_revision(product_root):
    """The live product revision, OBSERVED (spec 14.1: the plan binds the observed revision, and any
    bound-item drift refuses): the commit HEAD names at `product_root`, from ONE read-only
    `git rev-parse --verify` with an explicit -C binding, replacement objects off and every GIT_* variable
    scrubbed, so an ambient GIT_DIR or GIT_WORK_TREE cannot redirect the answer. Investigation never enters
    .git and takes the revision from the worksheet; approve and apply observe it here. Missing git, a root
    in no repository, an unborn HEAD, a failed or timed-out query, or an answer that is not one 40- or
    64-digit object id refuses: an unreadable or unverifiable revision is never assumed fresh."""
    git = shutil.which("git")
    if git is None:
        raise AdoptApplyError("git is not on PATH, so the live product revision cannot be observed; an "
                              "unverifiable revision is never assumed fresh (spec 14.1, fail-closed)")
    env = dict((k, v) for k, v in os.environ.items() if not k.startswith("GIT_"))
    try:
        proc = subprocess.run([git, "--no-replace-objects", "-C", str(product_root), "rev-parse", "--verify",
                               "--quiet", "HEAD^{commit}"], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, env=env, timeout=_GIT_TIMEOUT_SECONDS)
    except (OSError, subprocess.SubprocessError) as exc:
        raise AdoptApplyError("the live product revision cannot be observed ({}); an unverifiable revision is "
                              "never assumed fresh (spec 14.1, fail-closed)".format(exc))
    answer = proc.stdout.decode("ascii", errors="replace").strip() if proc.returncode == 0 else None
    if answer is None or not schema._is_revision(answer):
        said = "".join(c if c.isprintable() else " " for c in (proc.stderr or b"").decode("utf-8", "replace"))
        said = " ".join(said.split())[:_GIT_DIAGNOSIS_CHARS]
        raise AdoptApplyError("the live product revision at {} cannot be observed (git rev-parse exit {}: no "
                              "repository, an unborn HEAD, a repository git refuses, or an unreadable answer{}); "
                              "an unverifiable revision is never assumed fresh (spec 14.1, fail-closed)".format(
                                  product_root, proc.returncode, "; git said: " + said if said else ""))
    return answer


# The composition guard (spec 14.1, 14.2): while the stage driver composes, a plan op handler may only stage
# into the transaction's ApplyOps; a direct filesystem or process effect, or a transaction of its own, would
# land before the driver's checks and outside the run's one base transaction. One process-wide audit hook,
# installed on the first composition and armed per thread only inside one, refuses each such effect and
# records it, so a handler that swallows the refusal still refuses the whole composition.
_COMPOSITION = threading.local()
_COMPOSITION_HOOK = []
_WRITE_OPEN_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
_EFFECT_EVENTS = frozenset((
    "os.mkdir", "os.rmdir", "os.remove", "os.rename", "os.link", "os.symlink", "os.truncate", "os.chmod",
    "os.chown", "os.chflags", "os.lchflags", "os.utime", "os.mkfifo", "os.mknod", "os.setxattr",
    "os.removexattr", "os.fork", "os.forkpty", "os.exec", "os.posix_spawn", "os.spawn", "os.system",
    "os.startfile", "os.kill", "os.killpg", "subprocess.Popen", "pty.spawn", "shutil.copyfile",
    "shutil.copymode", "shutil.copystat", "shutil.copytree", "shutil.chown", "shutil.move", "shutil.rmtree",
    "shutil.make_archive", "shutil.unpack_archive", "tempfile.mkstemp", "tempfile.mkdtemp",
    "_thread.start_new_thread", "_thread.start_joinable_thread"))


def _composition_audit(event, args):
    denied = getattr(_COMPOSITION, "denied", None)
    if denied is None:
        return
    if event == "open":
        mode = args[1] if len(args) > 1 else None
        flags = args[2] if len(args) > 2 else None
        if not ((isinstance(flags, int) and flags & _WRITE_OPEN_FLAGS)
                or (isinstance(mode, str) and any(c in mode for c in "wax+"))):
            return
    elif event not in _EFFECT_EVENTS:
        return
    denied.append(event)
    raise AdoptApplyError("a plan op handler attempted a direct effect ({}) while the stage driver composes; "
                          "a handler only stages into the transaction's ApplyOps, so nothing lands before the "
                          "driver's checks or outside the run's one base transaction (fail-closed)".format(event))


@contextlib.contextmanager
def _composing():
    """Arm the composition guard on this thread for one composition; a composition already armed here (a
    handler re-entering the driver) refuses. After the body, any recorded effect refuses the composition even
    where a handler caught the guard's own refusal. Starting a thread is an effect (the guard arms per
    thread). Disclosed: an audit hook is not a sandbox; a write through an already-open writable descriptor,
    ctypes, a thread already running before composition, or an entry point CPython does not audit stays
    outside it."""
    if getattr(_COMPOSITION, "denied", None) is not None:
        raise AdoptApplyError("a composition is already in progress on this thread; a plan op handler may not "
                              "re-enter the stage driver (fail-closed)")
    if not _COMPOSITION_HOOK:
        sys.addaudithook(_composition_audit)
        _COMPOSITION_HOOK.append(_composition_audit)
    denied = []
    _COMPOSITION.denied = denied
    try:
        yield
    finally:
        _COMPOSITION.denied = None
    if denied:
        raise AdoptApplyError("a plan op handler attempted direct effect(s) {} while the stage driver composed "
                              "and continued past the refusal; the composition refuses (fail-closed)".format(
                                  ", ".join(denied)))


def _receipt_not_yet_composable(context=None):
    """The mandatory receipt stage's slice-1 composer: a refusing not-yet-executable verdict, so apply
    refuses before anything is written until the record-adoption slice replaces DRIVER_STAGES[RECEIPT_STAGE]."""
    return schema.AdoptValidation(store.CANNOT_EVALUATE, [
        "the adoption receipt and its outcome-event chain are not yet composable in this build; a later "
        "adoption slice lands the receipt stage (fail-closed)"])


# The driver's mandatory stages (spec 14: apply ends with an adoption receipt plus its outcome-event chain).
# No plan can carry that receipt, since its core binds the approval recorded after the plan freezes
# (_opf_adopt_plan), so the obligation is the driver's: composed after every plan row, inside the run's one
# base transaction, whatever rows the plan carries. A record-adoption row a plan does carry dispatches like
# any other row; the receipt slice reconciles that row with this stage.
DRIVER_STAGES = {RECEIPT_STAGE: _receipt_not_yet_composable}


def _canonical_toml(data, label):
    """Parse `data` as TOML that is byte-identical to its own emit_checked rendering, or refuse: a frozen
    artefact admits no second spelling (a comment, a reordering or a whitespace change is a hand edit)."""
    if not isinstance(data, bytes):
        raise AdoptApplyError("{} must be bytes".format(label))
    try:
        doc = tomllib.loads(data.decode("utf-8"))
        canonical = emit_checked(doc).encode("utf-8")
    except (ValueError, RecursionError, EmitError) as exc:   # UnicodeDecodeError, TOMLDecodeError included
        raise AdoptApplyError("{} is unreadable or malformed TOML ({}); fail-closed".format(label, exc))
    if canonical != data:
        raise AdoptApplyError("{} is not in its canonical emitted form (a hand edit, comment or reordering); "
                              "only the exact frozen bytes are admitted (fail-closed)".format(label))
    return doc


def frozen_plan(plan_bytes):
    """Re-prove one frozen plan from its own bytes (spec 14.1): canonical TOML, the `opf.adoption.plan/v2`
    format (any other is never apply input), VALID under the schema validator, and a plan_digest that
    re-seals, the planner's own seal: the digest of the canonical emission of the plan without its
    plan_digest. Returns the parsed plan; raises AdoptApplyError."""
    doc = _canonical_toml(plan_bytes, "the adoption plan")
    if doc.get("format") != PLAN_V2_FORMAT:
        raise AdoptApplyError("apply takes only an approved {} plan (spec 14.1); {!r} binds none of the "
                              "v2 roster, so it is never apply input (fail-closed)".format(
                                  PLAN_V2_FORMAT, doc.get("format")))
    checked = schema.validate_plan(doc)
    if checked.status != store.VALID:
        raise AdoptApplyError("the adoption plan is not a VALID {} plan: {} (fail-closed)".format(
            PLAN_V2_FORMAT, "; ".join(checked.findings)))
    body = dict(doc)
    claimed = body.pop("plan_digest")
    try:
        sealed = "sha256:" + _sha256(emit_checked(body).encode("utf-8"))
    except EmitError as exc:
        raise AdoptApplyError("the adoption plan cannot be re-sealed ({}); fail-closed".format(exc))
    if sealed != claimed:
        raise AdoptApplyError("the adoption plan's plan_digest does not seal its own bytes (an edited plan); a "
                              "fresh plan with its own approval is the remedy (spec 14.1, fail-closed)")
    return doc


def _run_instant(run_id):
    """The aware UTC instant an adoption run id's stamp names (the plan's own freezing instant)."""
    if not isinstance(run_id, str):
        raise AdoptApplyError("plan run id {!r} is not a string; fail-closed".format(run_id))
    stamp = run_id[len("adopt-"):len("adopt-") + len("YYYYMMDDTHHMMSSZ")]
    try:
        return datetime.datetime.strptime(stamp, "%Y%m%dT%H%M%SZ").replace(tzinfo=datetime.timezone.utc)
    except ValueError as exc:
        raise AdoptApplyError("plan run id {!r} names no calendar instant ({}); fail-closed".format(run_id, exc))


def approval_findings(approval, plan_doc):
    """The findings against one captured approval for one plan, empty when it binds (spec 14.1): the closed
    APPROVAL_REQUIRED keyset (the receipt's own approval shape), a token actor, an RFC 3339 UTC approved_at
    no earlier than the instant the plan froze (its run id's stamp; the approval follows its plan), and a plan_digest AND an
    inventory_digest equal to the plan's own, hence that whole plan."""
    findings = []
    if schema._validate_subtable(approval, schema.APPROVAL_REQUIRED, "approval", findings) is None or findings:
        return findings
    if not schema._is_token(approval["actor"]):
        findings.append("approval actor is not a non-empty single-line token")
    if not schema._is_timestamp(approval["approved_at"]):
        findings.append("approval approved_at is not an RFC 3339 UTC instant")
    else:
        try:
            approved = datetime.datetime.fromisoformat(approval["approved_at"])
            planned = _run_instant(plan_doc.get("run_id"))
            follows = approved >= planned
        except (AdoptApplyError, TypeError, ValueError) as exc:
            findings.append("approval approved_at cannot be ordered after the plan's run instant ({})".format(exc))
        else:
            if not follows:
                findings.append("approval approved_at {!r} precedes the instant its plan froze ({}, its run id's "
                                "stamp): the one approval MUST follow its concrete plan (spec 14.1)".format(
                                    approval["approved_at"], planned.strftime("%Y-%m-%dT%H:%M:%SZ")))
    for key in ("plan_digest", "inventory_digest"):
        if approval[key] != plan_doc.get(key):
            findings.append("approval {0} {1!r} does not bind this plan's {0} {2!r}; an approval binds exactly "
                            "one plan, and a changed plan takes its own single approval (spec 14.1)".format(
                                key, approval[key], plan_doc.get(key)))
    return findings


def apply_plan(plan_doc, approval=None):
    """The apply-input gate, pure and write-free (spec 14.1): only an `opf.adoption.plan/v2` plan the schema
    validator grades VALID, with a captured approval binding its plan_digest and inventory_digest, is apply
    input. Any other format, the shipped v1 schema included, is refused on the marker before any other field
    is read; an invalid v2 plan propagates the validator's verdict; a v2 plan with no approval, or whose
    approval binds another plan, is refused. VALID admits the pair to the stage driver (run_apply) and is
    never by itself a write."""
    if not isinstance(plan_doc, dict):
        return schema._cannot("adoption plan is not a table")
    if plan_doc.get("format") != PLAN_V2_FORMAT:
        return schema._cannot("apply takes only an approved {} plan (spec 14.1); {!r} binds none of the "
                              "v2 roster, so it is never apply input (fail-closed)".format(
                                  PLAN_V2_FORMAT, plan_doc.get("format")))
    checked = schema.validate_plan(plan_doc)
    if checked.status != store.VALID:
        return checked
    if approval is None:
        return schema._cannot("apply takes an APPROVED plan, and no captured approval binds this one (spec "
                              "14.1); apply refuses (fail-closed)")
    findings = approval_findings(approval, plan_doc)
    if findings:
        return schema.AdoptValidation(store.CANNOT_EVALUATE, findings)
    return schema._ok()


def rederive_or_refuse(product_root, plan_doc, plan_bytes, worksheet):
    """Bound-item freshness (spec 14.1: any bound-item drift refuses into a fresh plan with its own single
    approval), read-only as the planner is: re-run the planner over the LIVE tree with the worksheet that
    froze the plan and the plan's own run instant and nonce, and require the byte-identical plan; but first
    the live product revision is OBSERVED (observe_revision) and must equal the plan's bound revision, so a
    revision-only change (an empty commit advancing HEAD) refuses. A changed observation (the sources,
    targets, store identity and ancestry the planner observes) refuses on the planner's own stale-inventory
    binding, and a change to a worksheet input (a decision, an op, a binding) freezes a different plan and
    refuses. Disclosed: the release, prompt_pack, enforcement and skip_policy bindings are worksheet-asserted,
    not observed here, so re-derivation proves only that the worksheet still freezes this plan, never that
    the tool release, prompt pack or enforcement pack in use match it; observing them is the trust-verification
    slice's. `worksheet` is the parsed planning worksheet: sources, targets, expected_observation_digest,
    product, decisions, ops, bindings."""
    import _opf_adopt_plan as planner
    observed = observe_revision(product_root)
    if observed != plan_doc["revision"]:
        raise AdoptApplyError("the product revision moved: the plan binds {} and the live HEAD is {}; any "
                              "bound-item drift refuses into a fresh plan with its own single approval (spec "
                              "14.1, fail-closed)".format(plan_doc["revision"], observed))
    run_id = plan_doc["run_id"]
    now = _run_instant(run_id)
    try:
        sheet = copy.deepcopy(worksheet)
        res = planner.plan(product_root, sources=sheet["sources"], targets=sheet["targets"],
                           expected_observation_digest=sheet["expected_observation_digest"],
                           product=sheet["product"], decisions=sheet["decisions"], ops=sheet["ops"],
                           now=now, run_nonce=run_id[-16:], bindings=sheet["bindings"])
    except (KeyError, TypeError) as exc:
        raise AdoptApplyError("the planning worksheet cannot re-derive the plan ({!r}); fail-closed".format(exc))
    if res.status != store.VALID:
        reasons = list(res.findings) + ["unresolved source disposition: " + u for u in res.unresolved]
        raise AdoptApplyError("the live tree no longer freezes this plan ({}); any bound-item drift refuses "
                              "into a fresh plan with its own single approval (spec 14.1, fail-closed)".format(
                                  "; ".join(reasons)))
    if res.plan != plan_bytes:
        raise AdoptApplyError("re-planning from the worksheet over the live tree freezes a different plan than "
                              "{}: a bound item drifted, so a fresh plan with its own single approval is the "
                              "remedy (spec 14.1, fail-closed)".format(plan_doc["plan_digest"]))


def capture_approval(product_root, plan_bytes, worksheet, actor, now):
    """The approve stage, the one approval (spec 14.1), write-free: re-prove the frozen plan from its bytes,
    refuse over a non-clean adoption journal or a run already applied, re-derive the plan over the live
    tree and its observed revision (rederive_or_refuse, so a moved revision, a stale observation or a changed
    worksheet refuses into a fresh plan), refuse an approval instant before the plan froze, then
    return the canonical
    approval bytes, in the receipt's APPROVAL_REQUIRED shape, binding the plan's plan_digest and
    inventory_digest. `now` is the clock instant of the approval. The adopter holds those bytes until apply,
    which persists them with the plan in the run's evidence bundle; nothing is written here."""
    plan_doc = frozen_plan(plan_bytes)
    require_clean_journal(product_root)
    _require_unapplied(product_root, plan_doc["run_id"])
    rederive_or_refuse(product_root, plan_doc, plan_bytes, worksheet)
    if (type(now) is not datetime.datetime or type(now.tzinfo) is not datetime.timezone
            or now.utcoffset() != datetime.timedelta(0)):
        raise AdoptApplyError("now must be a clock-derived aware UTC datetime")
    approval = dict(actor=actor, approved_at=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    plan_digest=plan_doc["plan_digest"], inventory_digest=plan_doc["inventory_digest"])
    findings = approval_findings(approval, plan_doc)
    if findings:
        raise AdoptApplyError("the approval cannot be captured: {} (fail-closed)".format("; ".join(findings)))
    try:
        return emit_checked(approval).encode("utf-8")
    except EmitError as exc:
        raise AdoptApplyError("the approval cannot be emitted canonically ({}); fail-closed".format(exc))


def run_apply(product_root, plan_bytes, approval_bytes, worksheet):
    """The apply stage (spec 14, 14.1, 14.2). Before anything is written, in order: re-prove the frozen
    plan (frozen_plan) and the captured approval from their own canonical bytes; admit the pair through
    apply_plan (the approval binds this plan's plan_digest and inventory_digest and follows its plan);
    refuse over a non-clean adoption journal; refuse a run already applied (_require_unapplied, replay
    admission ahead of freshness); re-derive the plan over the live tree and its observed revision
    (rederive_or_refuse); and refuse while any plan op's slice, or the driver's mandatory receipt stage
    (DRIVER_STAGES), has not landed, so an unlanded step refuses the whole apply fail-closed by
    construction, never half of it.

    Composition is the driver's, and so are its rules. One compose function stages the plan and approval
    bytes in the run's evidence bundle, dispatches EVERY plan op in plan order in the apply stage, each with
    one context table (`ops`, the transaction's ApplyOps; `plan`; `approval`; `product_root`; `stage`), then
    composes the mandatory receipt stage whatever rows the plan carries; the first refusing verdict refuses
    the whole composition. Handlers run under the composition guard (_composing): a handler only stages into
    `ops`, so a direct filesystem or process effect, or a transaction of its own, refuses the composition (a
    slice that would delegate to its own journaled operation, init-store's substrate included, composes into
    `ops` instead). Retirement partition (spec 14.1, 14.2): a plan source that occupies no managed
    destination stays frozen, byte-identical in place, until the retirement stage after a green completion
    check, so the apply stage may take only its preimage: a composed remove or write of any non-occupying
    plan source refuses. The compose function first runs as a write-free preflight against the live tree,
    BEFORE the journal is prepared, so a handler, receipt, partition or invariant refusal writes nothing at
    all; then ONE base transaction (run_adopt_transaction) composes it again under the journal lock, every
    check re-proved there, and commits it. A refusal of that second composition (a stateful handler, or the
    tree drifting between the passes) releases the lock and removes the journal directories the run
    created, so it too leaves the tree as it found it, unless a release or a removal fails or may not be
    durable (a directory a concurrent run has populated meanwhile stays, disclosed): run_adopt_transaction
    then names each such leftover in place of "nothing written".
    Returns the transaction name."""
    plan_doc = frozen_plan(plan_bytes)
    approval = _canonical_toml(approval_bytes, "the adoption approval")
    gate = apply_plan(plan_doc, approval)
    if gate.status != store.VALID:
        raise AdoptApplyError("the approval does not admit this plan: {} (fail-closed)".format(
            "; ".join(gate.findings)))
    run_id = plan_doc["run_id"]
    require_clean_journal(product_root)
    _require_unapplied(product_root, run_id)
    rederive_or_refuse(product_root, plan_doc, plan_bytes, worksheet)
    rows = list(plan_doc["ops"])
    unlanded = []
    ops_unlanded = sorted(set(row["op"] for row in rows
                              if OP_HANDLERS.get(row["op"], _not_yet_executable) is _not_yet_executable))
    if ops_unlanded:
        unlanded.append("plan op(s) " + ", ".join(ops_unlanded))
    if DRIVER_STAGES.get(RECEIPT_STAGE, _receipt_not_yet_composable) is _receipt_not_yet_composable:
        unlanded.append("the mandatory {} stage".format(RECEIPT_STAGE))
    if unlanded:
        raise AdoptApplyError("{} not yet executable in this build; a later adoption slice lands each, and apply "
                              "refuses before anything is written (fail-closed)".format(" and ".join(unlanded)))
    frozen = set(row["path"] for row in plan_doc["sources"] if not row["occupying"])

    def compose(ops):
        with _composing():
            ops.create(plan_rel(run_id), plan_bytes)
            ops.create(approval_rel(run_id), approval_bytes)
            context = dict(ops=ops, plan=plan_doc, approval=approval, product_root=product_root,
                           stage=APPLY_STAGE)
            for i, row in enumerate(rows):
                verdict = dispatch(row, context)
                if verdict.status != store.VALID:
                    raise AdoptApplyError("plan op[{}] ({!r}) refused: {}".format(
                        i, row["op"], "; ".join(verdict.findings)))
            verdict = DRIVER_STAGES[RECEIPT_STAGE](context)
            if verdict.status != store.VALID:
                raise AdoptApplyError("the mandatory {} stage refused: {}".format(
                    RECEIPT_STAGE, "; ".join(verdict.findings)))
        touched = sorted(set(op["path"] for op in ops.ops
                             if op.get("op") in ("remove", "write") and op.get("path") in frozen))
        if touched:
            raise AdoptApplyError("the apply stage would remove or rewrite non-occupying plan source(s) {}; "
                                  "each stays frozen, byte-identical in place, until the retirement stage after "
                                  "a green completion check (spec 14.1, 14.2), and apply takes only its "
                                  "preimage (fail-closed)".format(", ".join(touched)))

    root_fd = _open_product_root(product_root)
    try:
        _compose_checked(root_fd, run_id, None, compose)
    finally:
        store._close_fd_exc_safe(root_fd)
    return run_adopt_transaction(product_root, run_id, compose)


def _selftest_git_commit(root):
    """Self-test fixtures only: make `root` a git repository when it is not one, then advance its HEAD by
    one EMPTY commit through plumbing (mktree, commit-tree, update-ref; no hook, template or signing, a
    pinned identity and date), so only the revision moves. Returns the new HEAD, or None when git is
    unavailable or any step fails (the caller records that as a failed check)."""
    git = shutil.which("git")
    if git is None:
        return None
    env = dict((k, v) for k, v in os.environ.items() if not k.startswith("GIT_"))
    env.update(GIT_AUTHOR_NAME="fixture", GIT_AUTHOR_EMAIL="fixture@example.invalid",
               GIT_COMMITTER_NAME="fixture", GIT_COMMITTER_EMAIL="fixture@example.invalid",
               GIT_AUTHOR_DATE="2026-01-01T00:00:00Z", GIT_COMMITTER_DATE="2026-01-01T00:00:00Z")
    where = str(root)
    try:
        if not os.path.isdir(os.path.join(where, ".git")):
            subprocess.run([git, "-C", where, "-c", "init.templateDir=", "init", "-q"],
                           stdin=subprocess.DEVNULL, capture_output=True, env=env, timeout=60, check=True)
        tree = subprocess.run([git, "-C", where, "mktree"], input=b"", capture_output=True, env=env,
                              timeout=60, check=True).stdout.decode("ascii").strip()
        head = subprocess.run([git, "-C", where, "rev-parse", "--verify", "--quiet", "HEAD^{commit}"],
                              stdin=subprocess.DEVNULL, capture_output=True, env=env, timeout=60)
        if head.returncode == 0:
            made = subprocess.run([git, "-C", where, "commit-tree", "--no-gpg-sign", "-p",
                                   head.stdout.decode("ascii").strip(), "-m", "fixture", tree],
                                  stdin=subprocess.DEVNULL, capture_output=True, env=env, timeout=60,
                                  check=True)
        else:
            made = subprocess.run([git, "-C", where, "commit-tree", "--no-gpg-sign", "-m", "fixture", tree],
                                  stdin=subprocess.DEVNULL, capture_output=True, env=env, timeout=60,
                                  check=True)
        commit = made.stdout.decode("ascii").strip()
        subprocess.run([git, "-C", where, "update-ref", "HEAD", commit], stdin=subprocess.DEVNULL,
                       capture_output=True, env=env, timeout=60, check=True)
    except (OSError, subprocess.SubprocessError, UnicodeDecodeError):
        return None
    return commit


def _selftest_git_set_head(root, commit):
    """Self-test fixtures only: point `root`'s HEAD back at `commit` (update-ref); True when it did."""
    git = shutil.which("git")
    if git is None or not isinstance(commit, str):
        return False
    env = dict((k, v) for k, v in os.environ.items() if not k.startswith("GIT_"))
    try:
        proc = subprocess.run([git, "-C", str(root), "update-ref", "HEAD", commit], stdin=subprocess.DEVNULL,
                              capture_output=True, env=env, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0


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
    # landed ops (init-store, slice 3; the three finish ops, slice 5) are pinned to their handlers, every
    # other entry to the refusing handler, and every canonical row refuses (each landed op's for want of
    # its context, before anything is observed). A silently-enabled op is a red; the slice that
    # legitimately lands an op updates these pins in the same change.
    landed = dict([("init-store", _init_store), ("plant-governance", _plant_governance),
                   ("render-views", _render_views), ("record-adoption", _record_adoption)])
    check("handlers-cover-vocabulary", set(OP_HANDLERS) == set(schema.ADOPT_OPS_BY_NAME))
    check("handlers-all-refusing-but-the-landed-ops",
          all(h is _not_yet_executable for n, h in OP_HANDLERS.items() if n not in landed))
    check("handlers-landed-ops-pinned", all(OP_HANDLERS[n] is h for n, h in landed.items()))
    check("handler-init-store-landed", OP_HANDLERS["init-store"] is _init_store)
    res = dispatch(schema.canonical_op("init-store"))
    check("op-init-store-refuses-without-context",
          res.status == CANNOT and any("no approved plan" in f for f in res.findings))
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
        # the posture refusals carry no mid-message write claim of their own, so the composed text never
        # contradicts itself (red against "refuses before anything is written" / "before writing").
        check("posture-refusal-no-own-claim", twin is not None and "before anything" not in twin
              and twin.count("nothing written") <= 1)
        os.unlink(root / ".working/other/manifest.toml")
        os.rmdir(root / ".working/other")
        leased = refusal(run_adopt_transaction, root, rid, lambda ops: ops.create(
            evidence_home_rel(rid) + "/x.md", b"x\n"))
        check("posture-resolved-refusal-no-own-claim", store.resolve_store(root).status == store.RESOLVED
              and leased is not None and "lease join" in leased and "before writing" not in leased)
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
              and "nothing written" not in aborted and "terminal record" in aborted
              and txn_state(root, rid) == "rolled-back" and lock_free(root))
        check("abort-restores-prestate", snapshot(root) == before)

    # 6a: a committed transaction whose lock release fails names the lock that stays, never a silent
    # success over it (red against a release that swallows its failure).
    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        root, files = fixture(temp)

        def release_refused(journal_root):
            raise OSError("an injected lock release fault")
        with mock.patch.object(_journal, "release_lock", release_refused):
            kept = refusal(run_adopt_transaction, root, rid, compose_full(files))
        check("commit-lock-release-failure-named", kept is not None and "COMMITTED" in kept
              and "lock STAYS" in kept and txn_state(root, rid) == "complete" and not lock_free(root))
    # 6a': the post-COMMIT release failure is its own type, carrying the transaction, and says the changes
    # LANDED (red against the plain refusal type); the exit mapping is AdoptApplyError's, unchanged.
    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        root, files = fixture(temp)
        with mock.patch.object(_journal, "release_lock", release_refused):
            try:
                run_adopt_transaction(root, rid, compose_full(files))
                landed_error = None
            except AdoptApplyError as exc:
                landed_error = exc
        check("commit-lock-release-typed-landed", isinstance(landed_error, AdoptCommittedLockError)
              and landed_error.txn == rid and "LANDED" in str(landed_error)
              and "lock STAYS" in str(landed_error) and txn_state(root, rid) == "complete")
    # 6a'': an interrupt raised inside the release after a committed transaction propagates as itself, with
    # the COMMITTED state and the observed lock outcome attached (red against a release that drops both).
    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        root, files = fixture(temp)

        def release_interrupted(journal_root):
            raise KeyboardInterrupt("injected release interrupt")
        notes = None
        with mock.patch.object(_journal, "release_lock", release_interrupted):
            try:
                run_adopt_transaction(root, rid, compose_full(files))
            except KeyboardInterrupt as exc:
                notes = " ".join(getattr(exc, "__notes__", []))
        check("commit-release-interrupt-outcome-noted", notes is not None and "COMMITTED" in notes
              and "LANDED" in notes and "lock STAYS" in notes and txn_state(root, rid) == "complete"
              and not lock_free(root))
    # 6a''': the release read-back judges the lock by what THIS acquire wrote, never by process identity: a
    # lock another thread of this same process takes after this run's release is not "this run's lock
    # STAYS" (red against a read-back decided by process identity).
    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        root, files = fixture(temp)
        real_release = _journal.release_lock

        def peer_thread_after(journal_root):
            real_release(journal_root)
            _journal.acquire_lock(journal_root, "opf-adopt-selftest-thread")
        with mock.patch.object(_journal, "release_lock", peer_thread_after):
            done_txn, why = attempt(run_adopt_transaction, root, rid, compose_full(files))
        check("commit-release-peer-lock-not-own", done_txn == rid and why is None and not lock_free(root))
    # 6a'''': a refusal past the journal's own pre-INTENT capture (its transaction directory, frames.log and
    # preimages written) never says "nothing written": the composer names each entry the run's two journal
    # listings differ by, over a journal that predates the run and over one the run created (red against
    # the hand-written claim of a "refused before it opened" branch).
    for prebuilt in (True, False):
        with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
            root, files = fixture(temp)
            if prebuilt:
                os.makedirs(root / JOURNAL_REL)
            real_capture = _journal.capture_preimages

            def capture_then_refuse(*args):
                real_capture(*args)
                raise _journal.JournalError("injected refusal after capture, before INTENT")
            with mock.patch.object(_journal, "capture_preimages", capture_then_refuse):
                err = refusal(run_adopt_transaction, root, rid, compose_full(files))
            txn_rel = JOURNAL_REL + "/" + rid
            check("pre-intent-capture-leftover-named-" + ("prebuilt" if prebuilt else "created"),
                  "refused before it opened" in (err or "") and "nothing written" not in (err or "")
                  and "{} (directory)".format(txn_rel) in (err or "")
                  and "{}/frames.log (file)".format(txn_rel) in (err or "")
                  and "{}/preimages".format(txn_rel) in (err or "")
                  and (root / txn_rel / "frames.log").is_file() and lock_free(root))
    # 6a5: a journal listing that could not read part of the tree never authorizes "nothing written", even
    # when both listings failed alike; it says the journal could not be fully observed (red against a
    # comparison in which two equal "unlisted" markers vanish). The fault hits the listing only.
    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        root, files = fixture(temp)
        os.makedirs(root / JOURNAL_REL)
        jst = os.stat(root / JOURNAL_REL)
        real_capture, real_listdir = _journal.capture_preimages, os.listdir

        def capture_then_refuse(*args):
            real_capture(*args)
            raise _journal.JournalError("injected refusal after capture, before INTENT")

        def journal_unlistable(target="."):
            if isinstance(target, int) and (os.fstat(target).st_dev, os.fstat(target).st_ino) == (
                    jst.st_dev, jst.st_ino):
                raise PermissionError(13, "an injected listing fault")
            return real_listdir(target)
        with mock.patch.object(_journal, "capture_preimages", capture_then_refuse), \
                mock.patch.object(os, "listdir", journal_unlistable):
            err = refusal(run_adopt_transaction, root, rid, compose_full(files))
        check("pre-intent-unlisted-journal-no-claim", "refused before it opened" in (err or "")
              and "nothing written" not in (err or "") and "could not be fully observed" in (err or "")
              and (root / JOURNAL_REL / rid / "frames.log").is_file())
    # 6a6: the closing observation is bound to the journal directory this run wrote to: one swapped in
    # during preparation, then moved away with the original restored, never reads as "nothing written"
    # (red against a closing listing compared by path alone).
    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        root, files = fixture(temp)
        jpath = root / JOURNAL_REL
        os.makedirs(jpath)
        real_capture, real_ensure, real_release = (_journal.capture_preimages, _journal.ensure_journal_dirs,
                                                   _journal.release_lock)

        def capture_then_refuse(*args):
            real_capture(*args)
            raise _journal.JournalError("injected refusal after capture, before INTENT")

        def swap_in(*args):
            os.rename(jpath, root / "journal-aside")
            return real_ensure(*args)

        def release_then_restore(journal_root):
            real_release(journal_root)
            os.rename(jpath, root / "retained-journal")
            os.rename(root / "journal-aside", jpath)
        with mock.patch.object(_journal, "capture_preimages", capture_then_refuse), \
                mock.patch.object(_journal, "ensure_journal_dirs", swap_in), \
                mock.patch.object(_journal, "release_lock", release_then_restore):
            err = refusal(run_adopt_transaction, root, rid, compose_full(files))
        check("pre-intent-journal-swap-restore-named", "refused before it opened" in (err or "")
              and "nothing written" not in (err or "") and "no longer resolves" in (err or "")
              and (root / "retained-journal" / rid / "frames.log").is_file())
    # 6a7: content changed in place on the inode this run's acquire wrote (here malformed) is an unresolved
    # outcome, never a release: a committed transaction raises AdoptCommittedLockError naming the altered
    # lock (red against a read-back that presumes release on any other content).
    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        root, files = fixture(temp)
        real_release = _journal.release_lock

        def malformed_then_release(journal_root):
            (Path(journal_root) / "lock").write_bytes(b"{")
            real_release(journal_root)
        with mock.patch.object(_journal, "release_lock", malformed_then_release):
            try:
                run_adopt_transaction(root, rid, compose_full(files))
                altered_error = None
            except AdoptApplyError as exc:
                altered_error = exc
        check("commit-release-altered-lock-unresolved", isinstance(altered_error, AdoptCommittedLockError)
              and "altered and stays" in str(altered_error) and "lock was released" not in str(altered_error)
              and (root / JOURNAL_REL / "lock").read_bytes() == b"{")
    # 6a8: another run's lock, planted between the clean check and this run's acquire, is never this run's:
    # the refusal attributes nothing to this run and names the lock as another run's (red against the
    # "NOT everything this run wrote" lead-in, or an unattributed lock label).
    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        root, files = fixture(temp)
        os.makedirs(root / JOURNAL_REL)
        real_acquire = _journal.acquire_lock

        def foreign_first(journal_root, session_id):
            (Path(journal_root) / "lock").write_bytes(json.dumps(dict(
                uid=os.getuid(), pid=os.getppid(), session="another-run", **{"pid-start": ""}, utc="2026-01-01T00:00:00Z")).encode())
            return real_acquire(journal_root, session_id)
        with mock.patch.object(_journal, "acquire_lock", foreign_first):
            err = refusal(run_adopt_transaction, root, rid, compose_full(files))
        check("acquire-foreign-lock-not-attributed", "cannot take the adoption journal lock" in (err or "")
              and "not attributed to this run" in (err or "") and "another run's journal lock" in (err or "")
              and "this run wrote" not in (err or "") and "nothing written" not in (err or "")
              and not lock_free(root))
    # 6a9: an interrupt inside acquire after the lock file exists records the lock as observed, never as
    # "this run holds no journal lock" (red against a lock state presumed untaken).
    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        root, files = fixture(temp)
        os.makedirs(root / JOURNAL_REL)

        def created_then_interrupted(journal_root, session_id):
            real_acquire(journal_root, session_id)
            raise KeyboardInterrupt("injected acquire interrupt")
        notes = None
        with mock.patch.object(_journal, "acquire_lock", created_then_interrupted):
            try:
                run_adopt_transaction(root, rid, compose_full(files))
            except KeyboardInterrupt as exc:
                notes = " ".join(getattr(exc, "__notes__", []))
        check("acquire-interrupt-lock-observed", notes is not None and "holds no journal lock" not in notes
              and "lock acquire was interrupted" in notes and not lock_free(root))
    # 6a10: the closing listing keys the journal by the descriptor it opened, never by its stat by name: a
    # swap timed between that stat and that open, over the journal this run wrote to (swap) or with the
    # original restored there (swap and restore), never reads as "nothing written" and names the change
    # (red against a listing keyed by the stat before its open). The same swap at `adopt` never states as
    # gone the journal beneath it, which that listing did not see (red against "gone" for unseen entries).
    for comp, restore in (("journal", False), ("journal", True), ("adopt", False), ("adopt", True)):
        with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
            root, files = fixture(temp)
            os.makedirs(root / JOURNAL_REL)
            jpath = root / JOURNAL_REL if comp == "journal" else root / JOURNAL_REL.rsplit("/", 1)[0]
            armed, started = [], []
            real_capture, real_ensure, real_release, real_open = (
                _journal.capture_preimages, _journal.ensure_journal_dirs, _journal.release_lock, os.open)

            def capture_then_refuse(*args):
                real_capture(*args)
                raise _journal.JournalError("injected refusal after capture, before INTENT")

            def swap_in(*args):
                if restore:
                    os.rename(jpath, root / "journal-aside")
                return real_ensure(*args)

            def swap_before_open(path, flags, *args, **kwargs):
                if armed and path == comp and flags & os.O_DIRECTORY:
                    armed.clear()
                    os.rename(jpath, root / "retained-journal")
                    if restore:
                        os.rename(root / "journal-aside", jpath)
                    else:
                        os.mkdir(jpath)
                return real_open(path, flags, *args, **kwargs)
            opener = mock.patch.object(os, "open", swap_before_open)

            def release_then_arm(journal_root):
                # os.open is wrapped only from here, past require_containment, so the next open of the
                # journal by name is the closing listing's, right after its stat
                real_release(journal_root)
                armed.append(True)
                started.append(opener.start())
            try:
                with mock.patch.object(_journal, "capture_preimages", capture_then_refuse), \
                        mock.patch.object(_journal, "ensure_journal_dirs", swap_in), \
                        mock.patch.object(_journal, "release_lock", release_then_arm):
                    err = refusal(run_adopt_transaction, root, rid, compose_full(files))
            finally:
                if started:
                    opener.stop()
            held = root / "retained-journal" / ("" if comp == "journal" else "journal")
            check("pre-intent-{}-stat-open-swap-named".format(comp) + ("-restore" if restore else ""),
                  "refused before it opened" in (err or "") and "nothing written" not in (err or "")
                  and "{} could not be listed (it changed between this listing's stat and its open".format(
                      jpath.relative_to(root).as_posix()) in (err or "") and "this run wrote" in (err or "")
                  and " are gone" not in (err or "") and (held / rid / "frames.log").is_file())
    # 6a11: a journal path component that changed since the run began (a concurrent cleanup removed the
    # journal tree after this run took what it creates, so its preparation recreated it) may be this run's:
    # the refusal never hides it under the neutral lead-in (red against attributing it to no one).
    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        root, files = fixture(temp)
        os.makedirs(root / JOURNAL_REL)
        real_ensure = _journal.ensure_journal_dirs

        def cleaned_then_ensure(*args):
            for rel in reversed(_journal_components()):
                os.rmdir(root / rel)
            return real_ensure(*args)

        def compose_refused(ops):
            raise AdoptApplyError("an injected compose refusal")
        with mock.patch.object(_journal, "ensure_journal_dirs", cleaned_then_ensure):
            err = refusal(run_adopt_transaction, root, rid, compose_refused)
        check("journal-component-recreated-attributed", "injected compose refusal" in (err or "")
              and "not attributed to this run" not in (err or "") and "this run wrote" in (err or "")
              and "{} (directory), a component of the journal path".format(JOURNAL_REL) in (err or "")
              and "nothing written" not in (err or ""))
    # 6a11b: only a directory at a journal path component may be this run's recreation; anything else there
    # is named by its type, never as possibly recreated by this run (red against the label for any type).
    comp_dir = _entry_named(JOURNAL_REL, ("directory", 1, 2), [], rid, None, False)
    comp_file = _entry_named(JOURNAL_REL, ("file", 1, 2), [], rid, None, False)
    check("journal-component-type-named", comp_dir[1] and "possibly recreated by this run" in comp_dir[0]
          and not comp_file[1] and "possibly recreated" not in comp_file[0]
          and "{} (file), a component of the journal path that changed since this run began, now a file".format(
              JOURNAL_REL) in comp_file[0])
    # 6a11c: an entry at a journal directory this run created is named by its observed type: only a
    # directory there is "a journal directory this run created" (red against that label for any type).
    made_dir = _entry_named(JOURNAL_REL, ("directory", 1, 2), [JOURNAL_REL], rid, None, False)
    made_file = _entry_named(JOURNAL_REL, ("file", 1, 2), [JOURNAL_REL], rid, None, False)
    check("journal-created-type-named", made_dir == ("{} (directory), a journal directory this run "
                                                     "created".format(JOURNAL_REL), True)
          and made_file == ("{} (file), now a file where this run had created a journal directory".format(
              JOURNAL_REL), True))
    # 6a12: a journal path the closing listing could not reach is said to be unobserved, never to "no
    # longer resolve" (red against the binding clause that treats an unreached path as absent), and the
    # journal beneath the unlisted `adopt` is never said to be gone (red against "gone" for unseen entries).
    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        root, files = fixture(temp)
        os.makedirs(root / JOURNAL_REL)
        armed = []
        real_capture, real_release, real_open = _journal.capture_preimages, _journal.release_lock, os.open

        def capture_then_refuse(*args):
            real_capture(*args)
            raise _journal.JournalError("injected refusal after capture, before INTENT")

        def adopt_unopenable(path, flags, *args, **kwargs):
            if path == "adopt" and flags & os.O_DIRECTORY:
                raise PermissionError(13, "an injected open fault")
            return real_open(path, flags, *args, **kwargs)
        opener = mock.patch.object(os, "open", adopt_unopenable)

        def release_then_arm(journal_root):
            real_release(journal_root)      # os.open is wrapped from here: the closing listing's opens
            armed.append(True)
            opener.start()
        try:
            with mock.patch.object(_journal, "capture_preimages", capture_then_refuse), \
                    mock.patch.object(_journal, "release_lock", release_then_arm):
                err = refusal(run_adopt_transaction, root, rid, compose_full(files))
        finally:
            if armed:
                opener.stop()
        check("pre-intent-unreached-journal-unobserved", "refused before it opened" in (err or "")
              and "nothing written" not in (err or "") and "no longer resolves" not in (err or "")
              and "{} could not be observed".format(JOURNAL_REL) in (err or "")
              and " are gone" not in (err or ""))
    # 6a12b: a directory the opening listing stated but could not list hides only what is below it: the
    # journal, removed before the closing listing observed it absent, is named gone (red against a
    # "contents not listed" key that hides the directory itself).
    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        root, files = fixture(temp)
        os.makedirs(root / JOURNAL_REL)
        jst = os.stat(root / JOURNAL_REL)
        real_listdir, faults = os.listdir, []

        def journal_unlistable_once(target="."):
            if not faults and isinstance(target, int) and (os.fstat(target).st_dev, os.fstat(target).st_ino) == (
                    jst.st_dev, jst.st_ino):
                faults.append(True)     # the opening listing's only: the closing one lists what is there
                raise PermissionError(13, "an injected listing fault")
            return real_listdir(target)

        def removed_then_refused(*args):
            os.rmdir(root / JOURNAL_REL)
            raise OSError("an injected preparation fault")
        with mock.patch.object(os, "listdir", journal_unlistable_once), \
                mock.patch.object(_journal, "ensure_journal_dirs", removed_then_refused):
            err = refusal(run_adopt_transaction, root, rid, compose_full(files))
        check("pre-intent-unlisted-journal-gone-named", "cannot prepare the adoption journal" in (err or "")
              and "{}/ could not be listed".format(JOURNAL_REL) in (err or "")
              and "entries present when this run began are gone: {}".format(JOURNAL_REL) in (err or "")
              and "nothing written" not in (err or "") and not (root / JOURNAL_REL).exists())
    # 6a12c: the same rule read directly, one level up (red against hiding `adopt` under its own key); and
    # an entry literally named "*" whose stat fails is that entry alone, never its directory's contents
    # (red against a "contents not listed" key an entry name can take).
    adopt = JOURNAL_REL.rsplit("/", 1)[0]
    said, _ours = _observed({".aiqt": ("directory", 1, 1), adopt: ("directory", 1, 2),
                             adopt + "/": ("unlisted", "an injected fault")}, {".aiqt": ("directory", 1, 1)}, [], rid)
    check("observed-unlisted-contents-dir-gone", ("entries present when this run began are gone: " + adopt) in said
          and "{}/ could not be listed (an injected fault)".format(adopt) in said[0])
    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        jdir = Path(temp) / JOURNAL_REL
        os.makedirs(jdir)
        (jdir / "x").write_bytes(b"")
        (jdir / "*").write_bytes(b"")
        real_stat = os.stat

        def star_unstatable(path, *args, **kwargs):
            if path == "*":
                raise PermissionError(13, "an injected stat fault")
            return real_stat(path, *args, **kwargs)
        listing_fd = store._open_dir_nofollow(Path(temp).resolve())
        try:
            first = _journal_listing(listing_fd)
            os.unlink(jdir / "x")
            with mock.patch.object(os, "stat", star_unstatable):
                second = _journal_listing(listing_fd)
        finally:
            os.close(listing_fd)
        said, _ours = _observed(first, second, [], rid)
        check("observed-star-entry-not-contents", second.get(JOURNAL_REL + "/*", ("",))[0] == "unlisted"
              and JOURNAL_REL + "/" not in second
              and ("entries present when this run began are gone: {}/x".format(JOURNAL_REL)) in said)
    # 6a13: an ordinary exception inside acquire, after the lock file exists, is described as an error,
    # never as an interrupt (red against the interrupt wording for every exception).
    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        root, files = fixture(temp)
        os.makedirs(root / JOURNAL_REL)

        def created_then_failed(journal_root, session_id):
            real_acquire(journal_root, session_id)
            raise ValueError("an injected acquire error")
        with mock.patch.object(_journal, "acquire_lock", created_then_failed):
            err = refusal(run_adopt_transaction, root, rid, compose_full(files))
        check("acquire-error-lock-not-interrupt", "lock acquire failed with an error" in (err or "")
              and "interrupted" not in (err or "") and "nothing written" not in (err or "")
              and not lock_free(root))

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
    # the schema's own canonical v2 plan is apply input ONLY with a captured approval binding both of its
    # digests (spec 14.1): no approval, an approval naming another plan_digest or inventory_digest (each
    # flip red against a gate that ignores the binding), an unknown approval key and a multi-line actor
    # are each refused; the bare v2 marker propagates the validator's verdict.
    canon_plan = schema.canonical_plan()
    check("apply-plan-format-is-the-schema-marker", PLAN_V2_FORMAT == schema.PLAN_FORMAT
          and canon_plan.get("format") == PLAN_V2_FORMAT)
    canon = apply_plan(canon_plan)
    check("apply-canonical-v2-plan-without-approval-refused",
          canon.status == CANNOT and any("no captured approval" in f for f in canon.findings))
    bound = dict(actor="adopter", approved_at="2026-09-17T12:00:00Z", plan_digest=canon_plan["plan_digest"],
                 inventory_digest=canon_plan["inventory_digest"])
    check("apply-canonical-v2-plan-with-binding-approval-valid", apply_plan(canon_plan, bound).status == VALID)
    for key in ("plan_digest", "inventory_digest"):
        other = dict(bound)
        other[key] = "sha256:" + "f" * 64
        res = apply_plan(canon_plan, other)
        check("apply-approval-{}-flip-refused".format(key),
              res.status == CANNOT and any("does not bind" in f for f in res.findings))
    res = apply_plan(canon_plan, dict(bound, note="also approves something else"))
    check("apply-approval-unknown-key-refused",
          res.status == CANNOT and any("unknown key" in f for f in res.findings))
    res = apply_plan(canon_plan, dict(bound, actor="adopter\nsecond line"))
    check("apply-approval-multiline-actor-refused",
          res.status == CANNOT and any("actor" in f for f in res.findings))
    # the one approval follows its plan (spec 14.1): an approved_at before the plan's created_at is refused (red
    # against a gate that checks the timestamp's shape alone).
    res = apply_plan(canon_plan, dict(bound, approved_at="1999-01-01T00:00:00Z"))
    check("apply-approval-before-plan-refused",
          res.status == CANNOT and any("precedes" in f for f in res.findings))
    v2 = apply_plan(dict(format=PLAN_V2_FORMAT))
    check("apply-v2-marked-plan-refused",
          v2.status == INVALID and any("missing required key" in f for f in v2.findings))
    nontable = apply_plan([])
    check("apply-non-table-refused",
          nontable.status == CANNOT and any("not a table" in f for f in nontable.findings))


    # 11: the stage driver over a fixture the real planner froze (a non-occupying retire source and a kept
    # file at a NOT-ADOPTED root that is a git repository, its HEAD the plan's bound revision). Approve is
    # write-free and binds the plan's two digests; a moved revision, an unobservable one, a stale tree or a
    # changed worksheet refuses into a fresh plan; a hand-edited or unsealed plan refuses; an approval for
    # another plan, or one before its plan, refuses; a non-clean adoption journal refuses the stage; with
    # every op still refusing, apply refuses before ANY write. With composing handlers patched in for the
    # landed-op case, apply still refuses while the driver's mandatory receipt stage is unlanded; with it
    # patched in too, apply persists the plan and approval in the run's bundle, dispatches every row in plan
    # order in the apply stage and then the receipt stage, and leaves the frozen retire source in place. The
    # retirement-partition flip, a handler writing the live tree directly, one swallowing that refusal, one
    # opening its own transaction and one starting a thread each refuse with the WHOLE tree unchanged, as
    # does a receipt stage refusing only on the second, locked composition; a second apply, and an approve
    # of the applied run, refuse on the one-apply rule itself.
    import copy as _copy   # this function binds `copy` as a local name (section 4), shadowing the module
    import shutil
    import _opf_adopt_plan as planner
    import _opf_init

    def _digest(data):
        return "sha256:" + _sha256(data)

    def _bytes(path):
        """A file's bytes, or None when absent, so a missing file is a recorded check failure."""
        return path.read_bytes() if path.is_file() else None

    def _snapshot(root):
        """Every path beneath root but the fixture repository's own .git, which only the revision vectors
        move (through git itself), with each file's bytes."""
        return dict((str(p.relative_to(root)), p.read_bytes() if p.is_file() else None)
                    for p in sorted(root.rglob("*")) if p.relative_to(root).parts[0] != ".git")

    def _sheet(root, revision):
        bindings = dict(schema.canonical_plan_bindings(), revision=revision or "0" * 40)
        manifest = _opf_init.build_manifest()
        views = sorted(v["target"] for v in tomllib.loads(manifest)["views"].values())
        rows = [dict(op="init-store", store_root=".", members=[dict(
                    path=".working/toml/manifest.toml", digest=_digest(manifest.encode("utf-8")))]),
                dict(op="render-views", store_root=".",
                     members=[dict(path=v, digest=_digest(v.encode("utf-8"))) for v in views]),
                schema.enforcement_install_op(bindings["enforcement"])]
        sheet = dict(sources=["keep.md", "legacy.md"], targets=[".opf/hooks/pre-commit"], product="opf",
                     decisions=[dict(path="keep.md", disposition="keep", actor="fixture"),
                                dict(path="legacy.md", disposition="retire", actor="fixture")],
                     ops=rows, bindings=bindings)
        obs = planner.investigate(root, sources=sheet["sources"], targets=sheet["targets"])
        sheet["expected_observation_digest"] = (
            tomllib.loads(obs.observation.decode("utf-8"))["observation_digest"] if obs.observation else "")
        return sheet

    def _freeze(root, sheet, nonce="0123456789abcdef"):
        res = planner.plan(root, now=now, run_nonce=nonce, **_copy.deepcopy(sheet))
        return res.plan if res.status == VALID else None

    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        root = Path(temp).resolve()
        (root / "keep.md").write_bytes(b"kept\n")
        (root / "legacy.md").write_bytes(b"legacy rules\n")
        head = _selftest_git_commit(root)
        check("driver-fixture-git-revision", head is not None and attempt(observe_revision, root)[0] == head)
        sheet = _sheet(root, head)
        plan_bytes = _freeze(root, sheet)
        check("driver-fixture-plan-frozen", plan_bytes is not None)
        plan_bytes = plan_bytes or b""
        plan_doc, _err = attempt(frozen_plan, plan_bytes)
        check("driver-frozen-plan-reproved", isinstance(plan_doc, dict)
              and plan_doc == tomllib.loads(plan_bytes.decode("utf-8")))
        plan_doc = plan_doc or dict(run_id=rid, sources=[], ops=[])
        frid = plan_doc["run_id"]
        check("driver-frozen-plan-non-canonical-refused", "canonical emitted form" in
              (refusal(frozen_plan, plan_bytes + b"# an adopter note\n") or ""))
        edited = dict(plan_doc, created_at="2026-01-01T00:00:00Z")
        check("driver-frozen-plan-unsealed-edit-refused", "does not seal" in
              (refusal(frozen_plan, emit_checked(edited).encode("utf-8")) or ""))
        v1_bytes = emit_checked(dict(plan_doc, format="opf.adoption.plan/v1")).encode("utf-8")
        check("driver-frozen-plan-v1-refused", "never apply input" in (refusal(frozen_plan, v1_bytes) or ""))

        # approve: write-free, the receipt's approval shape, binding both plan digests.
        before = _snapshot(root)
        approval_bytes, err = attempt(capture_approval, root, plan_bytes, sheet, "adopter", now)
        check("driver-approve-captures", isinstance(approval_bytes, bytes) and err is None)
        approval_bytes = approval_bytes or b""
        approval_doc = tomllib.loads(approval_bytes.decode("utf-8")) if approval_bytes else dict()
        check("driver-approval-binds-plan", set(approval_doc) == set(schema.APPROVAL_REQUIRED)
              and approval_doc.get("plan_digest") == plan_doc.get("plan_digest")
              and approval_doc.get("inventory_digest") == plan_doc.get("inventory_digest")
              and approval_doc.get("approved_at") == "2026-09-17T12:00:00Z")
        check("driver-approve-writes-nothing", _snapshot(root) == before)
        check("driver-approve-multiline-actor-refused", "actor" in
              (refusal(capture_approval, root, plan_bytes, sheet, "adopter\nsecond", now) or ""))
        # the approval follows its plan: an approval instant before the plan's created_at is refused.
        check("driver-approve-before-plan-refused", "precedes" in
              (refusal(capture_approval, root, plan_bytes, sheet, "adopter",
                       now - datetime.timedelta(days=1)) or ""))
        # revision-only drift: an EMPTY commit advances HEAD with every inventoried byte unchanged, so only
        # the observed revision can see it; approve and apply both refuse into a fresh plan.
        moved = _selftest_git_commit(root)
        check("driver-fixture-revision-moved", moved is not None and moved != head and _snapshot(root) == before)
        check("driver-approve-revision-drift-refused", "revision moved" in
              (refusal(capture_approval, root, plan_bytes, sheet, "adopter", now) or ""))
        check("driver-apply-revision-drift-refused", "revision moved" in
              (refusal(run_apply, root, plan_bytes, approval_bytes, sheet) or ""))
        check("driver-fixture-revision-restored", _selftest_git_set_head(root, head)
              and attempt(observe_revision, root)[0] == head)
        # an unobservable revision (no repository at the root) refuses, never assumed fresh.
        os.rename(root / ".git", root / "git-aside")
        check("driver-approve-unobservable-revision-refused", "revision" in
              (refusal(capture_approval, root, plan_bytes, sheet, "adopter", now) or ""))
        os.rename(root / "git-aside", root / ".git")
        # git's own diagnosis (here a repository another user owns) reaches the refusal, printable and bounded.
        said = b"fatal: detected dubious ownership in repository at '/x'\n\x1b[31m" + b"y" * 2000
        with mock.patch.object(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 128, b"", said)):
            err = refusal(observe_revision, root) or ""
        check("driver-unobservable-revision-names-git-diagnosis", "dubious ownership" in err
              and "\x1b" not in err and len(err) < 1000)
        # stale observation: a source's bytes change after the plan froze -> refuse into a fresh plan.
        (root / "legacy.md").write_bytes(b"edited after planning\n")
        check("driver-approve-stale-observation-refused", "fresh plan" in
              (refusal(capture_approval, root, plan_bytes, sheet, "adopter", now) or ""))
        (root / "legacy.md").write_bytes(b"legacy rules\n")
        # a changed worksheet (an attributed decision revised) freezes a different plan -> refused.
        revised = _copy.deepcopy(sheet)
        revised["decisions"][1]["actor"] = "another"
        check("driver-approve-revised-worksheet-refused", "different plan" in
              (refusal(capture_approval, root, plan_bytes, revised, "adopter", now) or ""))
        # reconcile-first: an interrupted adoption transaction refuses the stage (the planner never reads
        # the adoption journal, so without this gate approve would capture over it).
        (root / JOURNAL_REL / "txn").mkdir(parents=True)
        jfd = os.open(str(root / JOURNAL_REL), os.O_RDONLY | os.O_DIRECTORY)
        try:
            _journal.publish(jfd, root / JOURNAL_REL / "txn", _journal.F_INTENT, dict(txn="txn", ops=[]))
        finally:
            os.close(jfd)
        check("driver-approve-open-journal-refused", "must be reconciled" in
              (refusal(capture_approval, root, plan_bytes, sheet, "adopter", now) or ""))
        check("driver-plan-stage-open-journal-refused", "must be reconciled" in
              (refusal(require_clean_journal, root) or ""))
        shutil.rmtree(root / ".aiqt")
        check("driver-fixture-restored", _snapshot(root) == before)

        # apply in THIS build: every op still refuses, so apply refuses before any write (no journal, no
        # bundle), and an approval for ANOTHER plan of the same tree refuses on the binding first.
        err = refusal(run_apply, root, plan_bytes, approval_bytes, sheet)
        check("driver-apply-unlanded-ops-refused", "not yet executable" in (err or "")
              and "retire-file" in (err or ""))
        check("driver-apply-unlanded-writes-nothing", _snapshot(root) == before)
        other_plan = _freeze(root, sheet, nonce="fedcba9876543210") or b""
        check("driver-apply-approval-binding-flip-refused", "does not bind" in
              (refusal(run_apply, root, other_plan, approval_bytes, sheet) or ""))
        check("driver-apply-edited-approval-refused", "canonical emitted form" in
              (refusal(run_apply, root, plan_bytes, approval_bytes + b"# also\n", sheet) or ""))
        early = emit_checked(dict(approval_doc, approved_at="1999-01-01T00:00:00Z")).encode("utf-8")
        check("driver-apply-approval-before-plan-refused", "precedes" in
              (refusal(run_apply, root, plan_bytes, early, sheet) or ""))
        check("driver-apply-refusals-write-nothing", _snapshot(root) == before)

        # landed ops (patched handlers that compose through the context table).
        seen = []

        def composing(op_row, context):
            seen.append((op_row["op"], context.get("stage"), context["plan"]["plan_digest"],
                         context["approval"]["actor"], context.get("product_root")))
            if op_row["op"] == "retire-file":
                context["ops"].preserve(op_row["path"], op_row["preimage_digest"])
            return schema._ok()

        def receipt(context):
            seen.append((RECEIPT_STAGE, context.get("stage"), context["plan"]["plan_digest"],
                         context["approval"]["actor"], context.get("product_root")))
            return schema._ok()

        def over_eager(op_row, context):
            # a retire handler that removes a non-occupying source at apply (archive-then-remove, which
            # check_apply_ops alone admits as a valid preserve-first pair).
            if op_row["op"] == "retire-file":
                context["ops"].archive_occupying(op_row["path"], op_row["preimage_digest"])
            return schema._ok()

        def rogue_write(op_row, context):
            # a handler writing the live tree directly instead of staging into context["ops"].
            with open(os.path.join(str(context["product_root"]), "rogue.txt"), "wb") as fh:
                fh.write(b"written outside the transaction\n")
            return schema._ok()

        def swallowing(op_row, context):
            # a handler that catches the guard's refusal of its direct mkdir and reports success anyway.
            try:
                os.mkdir(os.path.join(str(context["product_root"]), "rogue-dir"))
            except AdoptApplyError:
                pass
            return schema._ok()

        def own_transaction(op_row, context):
            # a handler opening its own journaled transaction (an init-store delegating to its substrate).
            try:
                run_adopt_transaction(context["product_root"], context["plan"]["run_id"], lambda ops: None)
            except AdoptApplyError:
                pass
            return schema._ok()

        def threaded(op_row, context):
            # a handler handing its direct write to a thread of its own, outside the per-thread guard.
            worker = threading.Thread(target=(Path(str(context["product_root"])) / "via-thread").write_bytes,
                                      args=(b"written from another thread\n",))
            worker.start()
            worker.join()
            return schema._ok()

        passes = []

        def second_pass_refusing(context):
            # VALID in the write-free preflight, refusing when the transaction composes again.
            passes.append(context.get("stage"))
            return schema._ok() if len(passes) == 1 else schema._cannot("a second-pass refusal")

        def landed(handler, stage=receipt):
            stack = contextlib.ExitStack()
            stack.enter_context(mock.patch.dict(OP_HANDLERS, dict.fromkeys(OP_HANDLERS, handler)))
            if stage is not None:
                stack.enter_context(mock.patch.dict(DRIVER_STAGES, {RECEIPT_STAGE: stage}))
            return stack
        # the mandatory receipt stage (spec 14): the planner-produced plan carries no record-adoption row, and
        # with every row's handler landed apply still refuses before any write while the receipt stage is
        # unlanded (red against a driver that enumerates plan rows only).
        rows_named = [row["op"] for row in plan_doc["ops"]]
        with landed(composing, stage=None):
            err = refusal(run_apply, root, plan_bytes, approval_bytes, sheet)
        check("driver-apply-receipt-stage-unlanded-refused", "record-adoption" not in rows_named
              and "mandatory receipt stage" in (err or "") and "plan op(s)" not in (err or "") and not seen
              and _snapshot(root) == before)
        with landed(over_eager):
            err = refusal(run_apply, root, plan_bytes, approval_bytes, sheet)
        check("driver-retire-partition-flip-refused", "stays frozen" in (err or "") and "legacy.md" in (err or ""))
        # the partition refusal lands in the write-free preflight: the WHOLE tree is unchanged, no journal
        # directory included (red against a composition that first runs under the prepared journal).
        check("driver-retire-partition-source-untouched",
              _bytes(root / "legacy.md") == b"legacy rules\n" and _snapshot(root) == before)
        # composition rules the driver enforces, not handler convention: a direct write, a direct mkdir
        # whose refusal the handler swallows, and a transaction of the handler's own each refuse the apply
        # with the whole tree unchanged (each red against a driver that trusts its handlers).
        with landed(rogue_write):
            err = refusal(run_apply, root, plan_bytes, approval_bytes, sheet)
        check("driver-apply-handler-direct-write-refused", "direct effect" in (err or "")
              and not (root / "rogue.txt").exists() and _snapshot(root) == before)
        with landed(swallowing):
            err = refusal(run_apply, root, plan_bytes, approval_bytes, sheet)
        check("driver-apply-handler-swallowed-refusal-refused", "continued past the refusal" in (err or "")
              and not (root / "rogue-dir").exists() and _snapshot(root) == before)
        with landed(own_transaction):
            err = refusal(run_apply, root, plan_bytes, approval_bytes, sheet)
        check("driver-apply-handler-own-transaction-refused", "nested adoption transaction" in (err or "")
              and _snapshot(root) == before)
        # a handler handing its write to a thread it starts (the guard arms per thread) refuses at the start.
        with landed(threaded):
            err = refusal(run_apply, root, plan_bytes, approval_bytes, sheet)
        check("driver-apply-handler-thread-start-refused", "direct effect" in (err or "")
              and not (root / "via-thread").exists() and _snapshot(root) == before)
        # a refusal of the SECOND composition, under the journal lock after the preflight passed (a stateful
        # composer, or the tree drifting between the passes), also leaves the WHOLE tree unchanged: the
        # journal directories the run created are removed again (red against a driver that leaves them).
        del passes[:]
        with landed(composing, stage=second_pass_refusing):
            err = refusal(run_apply, root, plan_bytes, approval_bytes, sheet)
        check("driver-apply-second-pass-refusal-tree-untouched", "second-pass refusal" in (err or "")
              and len(passes) == 2 and _snapshot(root) == before)
        # only the directories the run created: a journal ancestor that predates the run stays in place.
        shutil.rmtree(root / ".aiqt", ignore_errors=True)   # isolate from a left-behind journal above
        (root / JOURNAL_REL).parent.mkdir(parents=True)
        ancestor = _snapshot(root)
        del passes[:]
        with landed(composing, stage=second_pass_refusing):
            err = refusal(run_apply, root, plan_bytes, approval_bytes, sheet)
        check("driver-apply-second-pass-refusal-keeps-prior-dirs", "second-pass refusal" in (err or "")
              and len(passes) == 2 and _snapshot(root) == ancestor)
        shutil.rmtree(root / ".aiqt", ignore_errors=True)
        check("driver-fixture-restored-after-second-pass", _snapshot(root) == before)
        # a journal preparation that fails part-way (its first directory made) leaves the tree unchanged too.

        def partial_journal(root_fd, journal_rel):
            os.mkdir(journal_rel.split("/")[0], dir_fd=root_fd)
            raise OSError("an injected journal preparation fault")
        with landed(composing), mock.patch.object(_journal, "ensure_journal_dirs", partial_journal):
            err = refusal(run_apply, root, plan_bytes, approval_bytes, sheet)
        check("driver-apply-partial-journal-refusal-tree-untouched", "cannot prepare" in (err or "")
              and _snapshot(root) == before)
        shutil.rmtree(root / ".aiqt", ignore_errors=True)
        # a first directory the cleanup walk cannot open (mode 000, as a 0777 umask makes it; the walk is
        # also refused by patch, so the vector holds under any uid) is still removed through its parent: the
        # unreachable deeper directories never end the sweep (red against a cleanup that stops at them).
        real_open_parent = _journal._open_parent
        sealed = []

        def sealed_journal(root_fd, journal_rel):
            os.mkdir(journal_rel.split("/")[0], 0o000, dir_fd=root_fd)
            sealed.append(journal_rel)
            raise PermissionError("an injected journal preparation fault")

        def sealed_walk(root_fd, relpath):
            if sealed and "/" in relpath and relpath.split("/")[0] == JOURNAL_REL.split("/")[0]:
                raise _journal.JournalError("cannot open contained directory component (injected EACCES)")
            return real_open_parent(root_fd, relpath)
        with landed(composing), mock.patch.object(_journal, "ensure_journal_dirs", sealed_journal), \
                mock.patch.object(_journal, "_open_parent", sealed_walk):
            err = refusal(run_apply, root, plan_bytes, approval_bytes, sheet)
        check("driver-apply-sealed-journal-ancestor-removed", sealed and "cannot prepare" in (err or "")
              and "INCOMPLETE" not in (err or "") and _snapshot(root) == before)
        if os.path.lexists(root / ".aiqt"):
            os.chmod(root / ".aiqt", 0o700)
        shutil.rmtree(root / ".aiqt", ignore_errors=True)
        # a directory the cleanup cannot remove (populated meanwhile) is named in the refusal, with every
        # ancestor it keeps, never a bare "nothing written" (red against a cleanup that reports nothing),
        # and left to the reconcile-first discipline, never to hand removal (red against a refusal that
        # directs the operator to remove what may hold a concurrent run's lock).

        def populated_journal(root_fd, journal_rel):
            os.makedirs(root / journal_rel)
            (root / journal_rel / "stray").write_bytes(b"populated meanwhile\n")
            raise OSError("an injected journal preparation fault")
        with landed(composing), mock.patch.object(_journal, "ensure_journal_dirs", populated_journal):
            err = refusal(run_apply, root, plan_bytes, approval_bytes, sheet)
        rels = ["/".join(JOURNAL_REL.split("/")[:i + 1]) for i in range(len(JOURNAL_REL.split("/")))]
        check("driver-apply-incomplete-cleanup-named", "cannot prepare" in (err or "")
              and "cleanup is INCOMPLETE" in (err or "") and "nothing written" not in (err or "")
              and "no sanctioned path clears" in (err or "")
              and all("{} (not removed".format(r) in (err or "") for r in rels)
              and "remove them" not in (err or "") and "never to hand removal" in (err or "")
              and "reconcile()" in (err or "") and (root / JOURNAL_REL / "stray").is_file())
        # the stray entry itself is named, and only it carries "no sanctioned path clears" (red against a
        # note that names only its parent and attaches that text to every leftover).
        check("driver-apply-stray-entry-named", "{}/stray (file), a stray entry".format(JOURNAL_REL) in (err or "")
              and (err or "").count("no sanctioned path clears") == 1)
        shutil.rmtree(root / ".aiqt", ignore_errors=True)
        # a removal whose parent fsync fails is reported as possibly not durable, never as clean, and never
        # as a directory that stays (red against a refusal that calls it a leftover).
        real_fsync = os.fsync

        def fsync_failing(fd):
            if (root / ".aiqt").exists():
                return real_fsync(fd)
            raise OSError("an injected fsync fault")
        with landed(composing), mock.patch.object(_journal, "ensure_journal_dirs", partial_journal), \
                mock.patch.object(os, "fsync", fsync_failing):
            err = refusal(run_apply, root, plan_bytes, approval_bytes, sheet)
        check("driver-apply-cleanup-fsync-failure-named", "cannot prepare" in (err or "")
              and "may not be durable" in (err or "") and "cleanup is UNCONFIRMED" in (err or "")
              and "nothing written" not in (err or "") and "INCOMPLETE" not in (err or "")
              and "STAY" not in (err or "") and _snapshot(root) == before)
        shutil.rmtree(root / ".aiqt", ignore_errors=True)
        # a lock-file failure other than a held lock (here the journal directory vanishing under a peer's
        # cleanup) is the named lock refusal, not a raw OSError past the driver.

        def vanishing_lock(journal_root, session_id):
            raise FileNotFoundError(2, "No such file or directory", str(journal_root / "lock"))
        with landed(composing), mock.patch.object(_journal, "acquire_lock", vanishing_lock):
            err = refusal(run_apply, root, plan_bytes, approval_bytes, sheet)
        check("driver-apply-lock-oserror-named-refusal", "cannot take the adoption journal lock" in (err or "")
              and "left no journal lock" in (err or "") and _snapshot(root) == before)
        shutil.rmtree(root / ".aiqt", ignore_errors=True)
        # a lock failure PAST the lock's creation, with the journal directories predating the run (so no
        # directory cleanup runs), names what it left: this run's own lock released again, a release whose
        # durability is unconfirmed, a lock that stays, or an unreadable lock left in place; never "nothing
        # written" over a lock left behind (each red against a refusal that neither releases nor names it).
        os.makedirs(root / JOURNAL_REL)
        prior = _snapshot(root)
        lock_path = root / JOURNAL_REL / "lock"
        real_fsync_dir = _journal._fsync_path_dir
        real_write_all = _journal._write_all
        faults = []

        def failing(real, times):
            def faulty(*args):
                if len(faults) < times:
                    faults.append(args)
                    raise OSError("an injected lock fault")
                return real(*args)
            return faulty
        with landed(composing), mock.patch.object(_journal, "_fsync_path_dir", failing(real_fsync_dir, 1)):
            err = refusal(run_apply, root, plan_bytes, approval_bytes, sheet)
        # its lock released durably and the journal listing as found, "nothing written" is the DERIVED claim
        check("driver-apply-lock-sync-failure-released", "cannot take the adoption journal lock" in (err or "")
              and "WAS created, then released again" in (err or "") and "may not be durable" not in (err or "")
              and (err or "").endswith("; nothing written (fail-closed)") and _snapshot(root) == prior)
        del faults[:]
        with landed(composing), mock.patch.object(_journal, "_fsync_path_dir", failing(real_fsync_dir, 2)):
            err = refusal(run_apply, root, plan_bytes, approval_bytes, sheet)
        check("driver-apply-lock-release-unconfirmed-named", "released again, but the release may not be "
              "durable" in (err or "") and "nothing written" not in (err or "") and _snapshot(root) == prior)
        del faults[:]
        with landed(composing), mock.patch.object(_journal, "_fsync_path_dir", failing(real_fsync_dir, 1)), \
                mock.patch.object(_journal, "release_lock", lambda journal_root: None):
            err = refusal(run_apply, root, plan_bytes, approval_bytes, sheet)
        stayed = lock_path.is_file()
        with landed(composing):
            later = refusal(run_apply, root, plan_bytes, approval_bytes, sheet)
        check("driver-apply-lock-left-named", "WAS created and STAYS" in (err or "")
              and "nothing written" not in (err or "") and stayed and "lock is held" in (later or ""))
        if os.path.lexists(lock_path):
            os.unlink(lock_path)
        del faults[:]
        with landed(composing), mock.patch.object(_journal, "_write_all", failing(real_write_all, 1)):
            err = refusal(run_apply, root, plan_bytes, approval_bytes, sheet)
        check("driver-apply-lock-unreadable-named", "present but unreadable" in (err or "")
              and "no sanctioned path clears it" in (err or "") and "lock STAYS" in (err or "")
              and "nothing written" not in (err or "") and lock_path.is_file())
        if os.path.lexists(lock_path):
            os.unlink(lock_path)
        # a lock this call did NOT create (here another thread of this same process takes it before this
        # call's create fails with EMFILE) is never released, though process identity matches (red against
        # a release decided by process identity).
        real_acquire = _journal.acquire_lock

        def peer_thread_first(journal_root, session_id):
            real_acquire(journal_root, "opf-adopt-selftest-thread")
            raise OSError(24, "Too many open files")
        with landed(composing), mock.patch.object(_journal, "acquire_lock", peer_thread_first):
            err = refusal(run_apply, root, plan_bytes, approval_bytes, sheet)
        check("driver-apply-lock-not-created-untouched", "left no journal lock" in (err or "")
              and lock_path.is_file())
        if os.path.lexists(lock_path):
            os.unlink(lock_path)
        # the normal release, on a refusal taken under the lock (here before the transaction opens),
        # names a lock that stays or a release that may not be durable, never "nothing written" over it
        # (each red against a release that swallows its failure).

        def unopened(*args):
            raise _journal.JournalError("injected refusal before transaction creation")

        def release_failing(journal_root):
            raise OSError("an injected lock release fault")
        with landed(composing), mock.patch.object(_journal, "run_transaction", unopened), \
                mock.patch.object(_journal, "release_lock", release_failing):
            err = refusal(run_apply, root, plan_bytes, approval_bytes, sheet)
        check("driver-apply-release-failure-named", "refused before it opened" in (err or "")
              and "lock STAYS" in (err or "") and "nothing written" not in (err or "") and lock_path.is_file())
        if os.path.lexists(lock_path):
            os.unlink(lock_path)
        # an unreadable lock on the RELEASE path is never called "possibly this run's own unfinished write"
        # (its write finished at acquire; red against the acquire-path wording reused here).

        def release_to_symlink(journal_root):
            os.unlink(str(journal_root / "lock"))
            os.symlink("elsewhere", str(journal_root / "lock"))
        with landed(composing), mock.patch.object(_journal, "run_transaction", unopened), \
                mock.patch.object(_journal, "release_lock", release_to_symlink):
            err = refusal(run_apply, root, plan_bytes, approval_bytes, sheet)
        check("driver-apply-release-unreadable-named", "present but unreadable" in (err or "")
              and "unfinished write" not in (err or "") and "nothing written" not in (err or "")
              and "{}/lock (symlink)".format(JOURNAL_REL) in (err or ""))
        if os.path.lexists(lock_path):
            os.unlink(lock_path)
        syncs = []

        def second_sync_failing(path):
            syncs.append(path)
            if len(syncs) == 2:
                raise OSError("an injected release fsync fault")
            return real_fsync_dir(path)
        with landed(composing), mock.patch.object(_journal, "run_transaction", unopened), \
                mock.patch.object(_journal, "_fsync_path_dir", second_sync_failing):
            err = refusal(run_apply, root, plan_bytes, approval_bytes, sheet)
        check("driver-apply-release-unconfirmed-named", "refused before it opened" in (err or "")
              and "released, but the release may not be durable" in (err or "")
              and "nothing written" not in (err or "") and _snapshot(root) == prior)
        shutil.rmtree(root / ".aiqt", ignore_errors=True)
        del seen[:]
        (root / "keep.md").write_bytes(b"kept, then edited\n")
        with landed(composing):
            err = refusal(run_apply, root, plan_bytes, approval_bytes, sheet)
        check("driver-apply-drift-after-approval-refused", "fresh plan" in (err or "") and not seen)
        (root / "keep.md").write_bytes(b"kept\n")
        with landed(composing):
            txn, err = attempt(run_apply, root, plan_bytes, approval_bytes, sheet)
        check("driver-apply-commits-with-landed-ops", txn == frid and err is None)
        # every row in plan order, then the receipt stage, in the write-free preflight and again in the
        # transaction, each in the apply stage with the plan, approval and product root.
        check("driver-apply-dispatches-every-row-in-order",
              [s[0] for s in seen] == (rows_named + [RECEIPT_STAGE]) * 2
              and all(s[1:] == (APPLY_STAGE, plan_doc["plan_digest"], "adopter", root) for s in seen))
        check("driver-apply-frozen-source-in-place-with-preimage",
              _bytes(root / "legacy.md") == b"legacy rules\n"
              and _bytes(root / archive_rel(frid, "legacy.md")) == b"legacy rules\n")
        check("driver-apply-persists-plan-and-approval",
              _bytes(root / plan_rel(frid)) == plan_bytes and _bytes(root / approval_rel(frid)) == approval_bytes)
        bundle_rows = tomllib.loads((_bytes(root / inventory_rel(frid)) or b"").decode("utf-8")).get("file", [])
        check("driver-apply-bundle-verifies", verify_bundle(root, frid).status == VALID
              and sorted(r["path"] for r in bundle_rows)
              == sorted([plan_rel(frid), approval_rel(frid), archive_rel(frid, "legacy.md")]))
        # one approval, one apply: the second apply refuses on that rule itself, ahead of the freshness
        # refusal its own first apply would otherwise trigger, with the evidence unchanged.
        applied = _snapshot(root)
        del seen[:]
        with landed(composing):
            again = refusal(run_apply, root, plan_bytes, approval_bytes, sheet)
        check("driver-apply-one-approval-one-apply", "already has its transaction" in (again or "")
              and not seen and _snapshot(root) == applied)
        # and approve refuses an applied run on the same rule, ahead of freshness.
        check("driver-approve-after-apply-refused", "already has its transaction" in
              (refusal(capture_approval, root, plan_bytes, sheet, "adopter", now) or "")
              and _snapshot(root) == applied)

    # 12: the finish ops (slice 5), see _finish_ops_self_test.
    _finish_ops_self_test(check)

    # 13: init-store over the coupled-init substrate (slice 3; real git fixtures, see _init_store_self_test).
    _init_store_self_test(check)

    if failures:
        print("OPF-ADOPT-APPLY SELF-TEST: FAIL ({} of {} checks failed)".format(len(failures), checked[0]))
        for f in failures:
            print("  FAILED: {}".format(f))
        return 1
    print("OPF-ADOPT-APPLY SELF-TEST: PASS ({} apply-shell checks; four executable ops: init-store "
          "and the finish ops)".format(checked[0]))
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

        # THE COMMITTED-TRANSACTION HALF OF THE ARCHIVAL PROOF (claude round 4, F2): a LIVE
        # digest-matched preservation copy admits the emptied machine directory ONLY beside a
        # COMPLETE preserve-AND-remove transaction of this journal. P1: a hand-placed copy with
        # no archival transaction at all; P3: a REAL archival that ROLLED BACK, the copy then
        # hand-placed; P4: a COMMITTED preserve-only transaction that never removed the source.
        # Each refuses with nothing written; each goes green if one conjunct of
        # _init_store_archival_committed (or the whole call) is dropped.
        occ_digest = "sha256:" + _sha256(occ_bytes)
        root = init_op._plain_repo(os.path.join(base, "handcopy"), env)
        run = rid()
        occ_row = dict(path=manifest_rel, digest=occ_digest, disposition="retire",
                       occupying=True, preservation=archive_rel(run, manifest_rel))
        os.makedirs(os.path.join(root, machine_dir))
        init_op._write(os.path.join(root, archive_rel(run, manifest_rel)), occ_bytes)
        os.chmod(os.path.join(root, store.WORKING_DIRNAME), 0o755)
        os.chmod(os.path.join(root, machine_dir), 0o755)
        before = snap(root)
        check("init-store-live-copy-without-archival-refused",
              refused(dispatch(row, ctx(root, run_id=run, sources=[occ_row])),
                      "completed archival")
              and snap(root) == before and op_ids(root) == [])

        root = init_op._plain_repo(os.path.join(base, "rbarchival"), env)
        init_op._write(os.path.join(root, manifest_rel), occ_bytes)
        run = rid()
        real_staged_verify = _journal._verify_staged_digest

        def failing_inventory_for(txn_run):
            def failing(op, payload):
                if op["path"] == inventory_rel(txn_run):
                    raise _journal.JournalError("injected failure at the inventory publication")
                return real_staged_verify(op, payload)
            return failing

        rolled = None
        _journal._verify_staged_digest = failing_inventory_for(run)
        try:
            try:
                run_adopt_transaction(root, run, lambda ops: ops.archive_occupying(
                    manifest_rel, occ_digest))
            except AdoptApplyError as exc:
                rolled = str(exc)
        finally:
            _journal._verify_staged_digest = real_staged_verify
        os.unlink(os.path.join(root, manifest_rel))
        init_op._write(os.path.join(root, archive_rel(run, manifest_rel)), occ_bytes)
        occ_row = dict(path=manifest_rel, digest=occ_digest, disposition="retire",
                       occupying=True, preservation=archive_rel(run, manifest_rel))
        before = snap(root)
        check("init-store-rolled-back-archival-refused",
              rolled is not None and "rolled back to the prestate" in rolled
              and refused(dispatch(row, ctx(root, run_id=run, sources=[occ_row])),
                          "completed archival")
              and snap(root) == before and op_ids(root) == [])

        root = init_op._plain_repo(os.path.join(base, "presonly"), env)
        init_op._write(os.path.join(root, manifest_rel), occ_bytes)
        run = rid()
        run_adopt_transaction(root, run, lambda ops: ops.preserve(manifest_rel, occ_digest))
        os.unlink(os.path.join(root, manifest_rel))
        occ_row = dict(path=manifest_rel, digest=occ_digest, disposition="retire",
                       occupying=True, preservation=archive_rel(run, manifest_rel))
        before = snap(root)
        check("init-store-preserve-only-transaction-refused",
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
        # THE FULL-S_IMODE PRE-CHECK (codex round 4, finding 2): a setgid 02755 machine
        # directory is NOT the planned 0755 under the same full stat.S_IMODE comparison the
        # substrate makes, so it refuses BEFORE the intent too (observe_inventory's 0o777 mask
        # would have read it as 755); the run id is still unburnt, proven by the 0755 rerun.
        os.chmod(os.path.join(root, machine_dir), 0o2755)
        check("init-store-setgid-admitted-dir-refused-pre-intent",
              refused(dispatch(row, ctx(root, run_id=run, sources=[occ_row])), "holds mode 2755")
              and op_ids(root) == [])
        os.chmod(os.path.join(root, machine_dir), 0o755)
        res = dispatch(row, ctx(root, run_id=run, sources=[occ_row]))
        check("init-store-unburnt-run-id-reruns-after-mode-fix", res.status == valid
              and store.resolve_store(root).status == store.RESOLVED)

        # THE `.working` FULL-MODE PRE-CHECK (claude round 5, N2): with admitted files the
        # substrate records `.working`'s observed mode MASKED to 0o777 and re-compares the
        # FULL stat.S_IMODE against that record under its mutex (so a special bit can never
        # pass), and refuses a group- or world-writable mode; the SAME two facts are
        # pre-checked before the run's intent opens, so each wrong `.working` mode refuses
        # with no operation record and the SAME run id reruns once the mode is corrected --
        # never a burnt id.
        root = init_op._plain_repo(os.path.join(base, "wmode"), env)
        init_op._write(os.path.join(root, manifest_rel), occ_bytes)
        run = rid()
        run_adopt_transaction(root, run, lambda ops: ops.archive_occupying(
            manifest_rel, "sha256:" + _sha256(occ_bytes)))
        occ_row = dict(path=manifest_rel, digest="sha256:" + _sha256(occ_bytes),
                       disposition="retire", occupying=True,
                       preservation=archive_rel(run, manifest_rel))
        os.chmod(os.path.join(root, machine_dir), 0o755)
        for wmode, why in ((0o2755, "holds mode 2755"), (0o1755, "holds mode 1755"),
                           (0o775, "group- or world-writable")):
            os.chmod(os.path.join(root, store.WORKING_DIRNAME), wmode)
            check("init-store-working-mode-{:o}-refused-pre-intent".format(wmode),
                  refused(dispatch(row, ctx(root, run_id=run, sources=[occ_row])), why)
                  and op_ids(root) == [])
        os.chmod(os.path.join(root, store.WORKING_DIRNAME), 0o755)
        res = dispatch(row, ctx(root, run_id=run, sources=[occ_row]))
        check("init-store-working-unburnt-run-id-reruns-after-mode-fix", res.status == valid
              and store.resolve_store(root).status == store.RESOLVED)

        # THE PRIOR REVERSED RUN'S COMMITTED ARTEFACTS (claude round 4, F1; the orchestrator
        # ruling): spec 14.1 keeps the reversed run id burnt, and a FRESH run continues the
        # occupied-machine composition by admitting the PRIOR run's committed artefacts -- the
        # prior bundle VERIFIES, every artefact was created by a COMPLETE transaction of this
        # journal, the prior init-store transaction is terminal ROLLED-BACK, and the machine
        # directory is admitted on the prior run's COMMITTED archival. Both reversal shapes:
        # the postcondition refusal, and SIGKILL inside the publication plus reconcile().
        for fixture, kill in (("prior", None), ("prior2", "source:2")):
            root = init_op._plain_repo(os.path.join(base, fixture), env)
            init_op._write(os.path.join(root, manifest_rel), occ_bytes)
            r1 = rid()
            run_adopt_transaction(root, r1, lambda ops: ops.archive_occupying(
                manifest_rel, occ_digest))
            occ_row = dict(path=manifest_rel, digest=occ_digest, disposition="retire",
                           occupying=True, preservation=archive_rel(r1, manifest_rel))
            if kill is None:
                reversed_r1 = refused(dispatch(dict(row, members=wrongv),
                                               ctx(root, run_id=r1, sources=[occ_row])),
                                      "restoring NOT-ADOPTED")
                need_recover = False
            else:
                reversed_r1 = _init_store_child(root, env, kill, row,
                                                ctx(root, run_id=r1, sources=[occ_row])) != 0
                reconcile(root)
                need_recover = True
            burnt = refused(dispatch(row, ctx(root, run_id=r1, sources=[occ_row],
                                              recover=need_recover)), "was reversed")
            r2 = rid()
            res = dispatch(row, ctx(root, run_id=r2, recover=need_recover))
            check("init-store-prior-committed-artefacts-admitted-{}".format(
                      "reversal" if kill is None else "kill"),
                  reversed_r1 and burnt and res.status == valid
                  and store.resolve_store(root).status == store.RESOLVED
                  and txn_state(root, r2) == "complete"
                  and live_digest(root, archive_rel(r1, manifest_rel)) == occ_digest
                  and live_digest(root, manifest_rel) == scaffold[manifest_rel])

        # THE MOVE-PAYLOAD CONTINUATION (codex round 5, finding 1): a prior reversed run whose
        # COMMITTED transaction also created a default Move destination -- retained evidence
        # under the SAME _evidence_eligible domains the composer derives inventory rows from --
        # is admitted whole by the fresh run; an admission re-spelled to the bundle and archive
        # homes alone strands this bundle and goes red here.
        root = init_op._plain_repo(os.path.join(base, "priormove"), env)
        init_op._write(os.path.join(root, manifest_rel), occ_bytes)
        moved_rel = store.moved_dest("prior-note.md")
        moved_bytes = b"a prior run's committed Move payload" + b"\x0a"
        r1 = rid()

        def compose_move(ops):
            ops.archive_occupying(manifest_rel, occ_digest)
            ops.create(moved_rel, moved_bytes)

        run_adopt_transaction(root, r1, compose_move)
        occ_row = dict(path=manifest_rel, digest=occ_digest, disposition="retire",
                       occupying=True, preservation=archive_rel(r1, manifest_rel))
        reversed_r1 = refused(dispatch(dict(row, members=wrongv),
                                       ctx(root, run_id=r1, sources=[occ_row])),
                              "restoring NOT-ADOPTED")
        burnt = refused(dispatch(row, ctx(root, run_id=r1, sources=[occ_row])), "was reversed")
        res = dispatch(row, ctx(root, run_id=rid()))
        check("init-store-prior-move-payload-admitted-after-reversal",
              reversed_r1 and burnt and res.status == valid
              and store.resolve_store(root).status == store.RESOLVED
              and live_digest(root, moved_rel) == "sha256:" + _sha256(moved_bytes)
              and live_digest(root, archive_rel(r1, manifest_rel)) == occ_digest
              and live_digest(root, manifest_rel) == scaffold[manifest_rel])

        # The negatives stay foreign and refuse with nothing written: a prior bundle that no
        # longer verifies (a tampered archive copy), and a prior archival that ROLLED BACK with
        # the copy and a verifying inventory hand-placed afterwards (neither a committed
        # transaction nor a rolled-back prior init-store transaction proves them).
        root = init_op._plain_repo(os.path.join(base, "priorbad"), env)
        init_op._write(os.path.join(root, manifest_rel), occ_bytes)
        r1 = rid()
        run_adopt_transaction(root, r1, lambda ops: ops.archive_occupying(
            manifest_rel, occ_digest))
        occ_row = dict(path=manifest_rel, digest=occ_digest, disposition="retire",
                       occupying=True, preservation=archive_rel(r1, manifest_rel))
        tampered = refused(dispatch(dict(row, members=wrongv),
                                    ctx(root, run_id=r1, sources=[occ_row])),
                           "restoring NOT-ADOPTED")
        with open(os.path.join(root, archive_rel(r1, manifest_rel)), "wb") as fh:
            fh.write(b"tampered copy" + b"\x0a")
        before = snap(root)
        check("init-store-prior-unverified-bundle-refused",
              tampered and refused(dispatch(row, ctx(root, run_id=rid())),
                                   "undispositioned foreign")
              and snap(root) == before and op_ids(root) == [])

        root = init_op._plain_repo(os.path.join(base, "priorrb"), env)
        init_op._write(os.path.join(root, manifest_rel), occ_bytes)
        r1 = rid()
        rolled = None
        _journal._verify_staged_digest = failing_inventory_for(r1)
        try:
            try:
                run_adopt_transaction(root, r1, lambda ops: ops.archive_occupying(
                    manifest_rel, occ_digest))
            except AdoptApplyError as exc:
                rolled = str(exc)
        finally:
            _journal._verify_staged_digest = real_staged_verify
        os.unlink(os.path.join(root, manifest_rel))
        init_op._write(os.path.join(root, archive_rel(r1, manifest_rel)), occ_bytes)
        init_op._write(os.path.join(root, inventory_rel(r1)), emit_inventory(
            r1, [inventory_row(archive_rel(r1, manifest_rel), occ_bytes)]))
        before = snap(root)
        check("init-store-prior-rolled-back-archival-refused",
              rolled is not None and "rolled back to the prestate" in rolled
              and refused(dispatch(row, ctx(root, run_id=rid())),
                          "undispositioned foreign")
              and snap(root) == before and op_ids(root) == [])

        # THE TERMINAL-ROLLED-BACK CONJUNCT PINNED (claude round 5, N1a): a prior run's REAL
        # committed archival whose init-store transaction NEVER RAN proves nothing -- its id
        # was never burnt the one sanctioned way -- so the fresh run refuses with the
        # snapshot unchanged and no operation record; dropping the terminal-ROLLED-BACK gate
        # (and its absent-transaction sibling) admits it and goes red here.
        root = init_op._plain_repo(os.path.join(base, "priornoinit"), env)
        init_op._write(os.path.join(root, manifest_rel), occ_bytes)
        r1 = rid()
        run_adopt_transaction(root, r1, lambda ops: ops.archive_occupying(
            manifest_rel, occ_digest))
        before = snap(root)
        check("init-store-prior-without-init-store-txn-refused",
              refused(dispatch(row, ctx(root, run_id=rid())), "undispositioned foreign")
              and snap(root) == before and op_ids(root) == [])

        # THE COMMITTED-CREATION CONJUNCT PINNED (claude round 5, N1b): an extra artefact
        # hand-planted in the reversed prior run's archive home and LISTED by a rewritten,
        # self-consistent inventory still refuses -- no COMPLETE transaction of this journal
        # created it (nor the rewritten inventory's own bytes) -- with the snapshot unchanged
        # and no operation record; dropping the committed-creation equality admits it and
        # goes red here.
        root = init_op._plain_repo(os.path.join(base, "priorextra"), env)
        init_op._write(os.path.join(root, manifest_rel), occ_bytes)
        r1 = rid()
        run_adopt_transaction(root, r1, lambda ops: ops.archive_occupying(
            manifest_rel, occ_digest))
        occ_row = dict(path=manifest_rel, digest=occ_digest, disposition="retire",
                       occupying=True, preservation=archive_rel(r1, manifest_rel))
        reversed_r1 = refused(dispatch(dict(row, members=wrongv),
                                       ctx(root, run_id=r1, sources=[occ_row])),
                              "restoring NOT-ADOPTED")
        planted = b"hand-planted, never committed" + b"\x0a"
        init_op._write(os.path.join(root, archive_rel(r1, "planted.txt")), planted)
        init_op._write(os.path.join(root, inventory_rel(r1)), emit_inventory(r1, [
            inventory_row(archive_rel(r1, manifest_rel), occ_bytes),
            inventory_row(archive_rel(r1, "planted.txt"), planted)]))
        before = snap(root)
        check("init-store-prior-extralisted-artefact-refused",
              reversed_r1
              and refused(dispatch(row, ctx(root, run_id=rid())), "undispositioned foreign")
              and snap(root) == before and op_ids(root) == [])

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

        # THE SCRUB'S DURABILITY ORDER (codex round 4, finding 1; claude round 4, F3), pinned
        # by a MOCKED SYSCALL-ORDER TRACE over one real scrub: EVERY cleanup unlink and rmdir
        # in the pre-discard trace -- each one, not just some (claude round 5, N3: an
        # any-form pairing stayed green when the lease-unlink fsync alone was dropped) -- is
        # immediately followed by an fsync of the SAME parent directory (matched by the
        # descriptor's device and inode, so a dropped parent fsync goes red), a cleanup target
        # already ABSENT on entry to a RESTARTED scrub has its nearest surviving ancestor
        # fsynced, and every one of these barriers precedes the record discard.
        import _opf_init_substrate

        def dir_ident(fd):
            if not isinstance(fd, int):
                return None
            try:
                fst = os.fstat(fd)
            except OSError:
                return None
            return (fst.st_dev, fst.st_ino)

        def path_ident(dpath):
            dfd = os.open(dpath, os.O_RDONLY | os.O_DIRECTORY)
            try:
                return dir_ident(dfd)
            finally:
                os.close(dfd)

        def traced_scrub(r):
            root_fd = _open_product_root(r)
            intent = None
            try:
                jr_fd = _journal.open_journal_root_fd(root_fd, JOURNAL_REL)
                try:
                    for txn_dir in _journal._journal_txn_dirs(jr_fd, _journal_root(r)):
                        if not txn_dir.name.endswith("." + INIT_STORE_PHASE):
                            continue
                        if _journal.classify_state(jr_fd, txn_dir) != "rolled-back":
                            continue
                        frames, _torn, _good = _journal.read_frames(jr_fd, txn_dir)
                        intent = _journal._first(frames, _journal.F_INTENT)
                        break
                finally:
                    _journal._close_fd_quietly(jr_fd)
                header = intent["header"]
                trace = []
                real_unlink, real_rmdir, real_fsync = os.unlink, os.rmdir, os.fsync
                real_settle = _opf_init_substrate.settle_operation
                real_discard = _opf_init_substrate.discard_reversed_operation

                def t_unlink(name, *a, **k):
                    trace.append(("unlink", dir_ident(k.get("dir_fd"))))
                    return real_unlink(name, *a, **k)

                def t_rmdir(name, *a, **k):
                    trace.append(("rmdir", dir_ident(k.get("dir_fd"))))
                    return real_rmdir(name, *a, **k)

                def t_fsync(fd):
                    trace.append(("fsync", dir_ident(fd)))
                    return real_fsync(fd)

                def t_settle(holder, op_id):
                    trace.append(("discard", None))
                    return real_settle(holder, op_id)

                def t_discard(holder, op_id):
                    trace.append(("discard", None))
                    return real_discard(holder, op_id)

                os.unlink, os.rmdir, os.fsync = t_unlink, t_rmdir, t_fsync
                os.supports_dir_fd.add(t_unlink)
                os.supports_dir_fd.add(t_rmdir)
                _opf_init_substrate.settle_operation = t_settle
                _opf_init_substrate.discard_reversed_operation = t_discard
                try:
                    _init_store_scrub(r, root_fd, header["init_operation_id"],
                                      members=[op["path"] for op in intent.get("ops", [])
                                               if isinstance(op, dict)
                                               and isinstance(op.get("path"), str)],
                                      working_created=header.get("working_created") is True,
                                      machine_created=header.get("machine_created")
                                      is not False,
                                      recover=True)
                finally:
                    os.unlink, os.rmdir, os.fsync = real_unlink, real_rmdir, real_fsync
                    os.supports_dir_fd.discard(t_unlink)
                    os.supports_dir_fd.discard(t_rmdir)
                    _opf_init_substrate.settle_operation = real_settle
                    _opf_init_substrate.discard_reversed_operation = real_discard
            finally:
                store._close_fd_exc_safe(root_fd)
            return trace

        def before_discard(trace):
            cut = trace.index(("discard", None)) if ("discard", None) in trace else -1
            return trace[:cut] if cut >= 0 else None

        def labeled(head):
            return [e for e in head if e[1] is not None]

        def paired(seq, kind, ident):
            return any(e == (kind, ident) and i + 1 < len(seq)
                       and seq[i + 1] == ("fsync", ident)
                       for i, e in enumerate(seq))

        def every_paired(head):
            # Over the RAW pre-discard trace: each unlink/rmdir is IMMEDIATELY followed by
            # the fsync of its own parent, so no removal -- the lease's included -- can ride
            # on a sibling pair in the same directory (claude round 5, N3).
            return all(e[0] not in ("unlink", "rmdir")
                       or (i + 1 < len(head) and head[i + 1] == ("fsync", e[1]))
                       for i, e in enumerate(head))

        root = init_op._plain_repo(os.path.join(base, "scrubtrace"), env)
        rc1 = _init_store_child(root, env, "stage:" + init_op.CHANGELOG_RELPATH, row,
                                ctx(root, run_id=rid()))
        reconcile(root)
        root_id = path_ident(root)
        working_id = path_ident(os.path.join(root, store.WORKING_DIRNAME))
        machine_id = path_ident(os.path.join(root, machine_dir))
        # The scrub's OWN lease branch is exercised by planting a leftover lease AFTER the
        # mutex acquisition: the oplock's recovery deletes a verified stale lease itself
        # (fsynced) during acquire, so the organic fixture never reaches the scrub's lease
        # unlink and its fsync would otherwise go unpinned (claude round 5, N3).
        import _opf_oplock
        real_acquire = _opf_oplock.acquire_init_operation

        def plant_lease_after_acquire(*a, **k):
            holder = real_acquire(*a, **k)
            init_op._write(os.path.join(root, init_op.LEASE_RELPATH), b"leftover lease\x0a")
            return holder

        _opf_oplock.acquire_init_operation = plant_lease_after_acquire
        try:
            head = before_discard(traced_scrub(root))
        finally:
            _opf_oplock.acquire_init_operation = real_acquire
        seq = labeled(head) if head is not None else []
        check("init-store-scrub-order-removal-then-parent-fsync-then-discard",
              rc1 != 0 and head is not None
              and every_paired(head)                        # EVERY removal, lease included
              and paired(seq, "unlink", root_id)            # the staging leftover
              and paired(seq, "unlink", machine_id)         # the planted leftover lease
              and paired(seq, "rmdir", working_id)          # the created machine directory
              and paired(seq, "rmdir", root_id))            # the created .working
        res = dispatch(row, ctx(root, run_id=rid(), recover=True))
        check("init-store-scrub-order-tree-recovers", res.status == valid
              and store.resolve_store(root).status == store.RESOLVED)

        root = init_op._plain_repo(os.path.join(base, "scrubtrace2"), env)
        rc1 = _init_store_child(root, env, "source:2", row, ctx(root, run_id=rid()))
        reconcile(root)
        rc2 = _init_store_child(root, env, "scrubdir:" + machine_base, row,
                                ctx(root, run_id=rid(), recover=True))
        reconcile(root)
        working_id = path_ident(os.path.join(root, store.WORKING_DIRNAME))
        gone = not os.path.lexists(os.path.join(root, machine_dir))
        head = before_discard(traced_scrub(root))
        seq = labeled(head) if head is not None else []
        check("init-store-scrub-restart-absent-target-fsync-before-discard",
              rc1 != 0 and rc2 != 0 and gone and head is not None and every_paired(head)
              and ("fsync", working_id) in seq)             # the removed machine directory's
        res = dispatch(row, ctx(root, run_id=rid(), recover=True))   # surviving parent
        check("init-store-scrub-restart-recovers", res.status == valid
              and store.resolve_store(root).status == store.RESOLVED
              and stage_leftovers(root) == [] and len(op_ids(root)) == 1)


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
