#!/usr/bin/env python3
"""OPF coupled-init resume substrate (OPF-D2B PR2 resume substrate).

SENSITIVE-TIER, crash-consistency-critical: this module persists the durable resume substrate a
coupled `opf init` operation leaves behind, so an interrupted operation can be classified and
resumed PLAN-AWARE, never guessed at and never blind-restored. Fail-closed throughout,
stdlib-only, Linux/macOS.

The substrate home is <control-root>/opf-init/, a SIBLING of the operation lock's opf-oplock/
control directory under the SAME authoritative control root (the common git directory the lock
module resolves through git itself, or the store root when .git is genuinely absent). It COMPOSES
the merged lock facility (_opf_oplock) rather than reinventing it: the control-root resolution
(_classify_git_entry plus _git_common_dir), the single-component control-home open
(_open_control_dir, parameterized for this second home), the no-follow dir_fd primitives, and the
exclusive fsynced control-file creation (_create_control_file, whose atomic staging-then-link
publication and failure cleanup this module inherits) are the lock module's own. So a substrate
writer killed mid-record leaves no torn record at the final name, only a staging leftover
(".<name>.opf-stage-<32 hex>"); the lock module sweeps such leftovers only from its own control
directory and machine store, so in an operation directory the classifier reads one as a foreign
entry (CANNOT-EVALUATE, preserved), never as a record.

Per held operation (an _opf_oplock.OpCapability) the substrate records, under ops/<op_id>/:

  1. plan.json, the frozen opf.init.plan/v1 document (OPF-INIT-D2B.md), persisted CREATE-ONLY
     (O_CREAT|O_EXCL) in the exact canonical JSON serialization the D2b contract defines, with
     the closed top-level key set, the scalar identity fields, and an operation_id equal to the
     held capability's op_id all validated BEFORE any filesystem write. Deep validation of the
     plan's composite values belongs to the plan's PRODUCER (PR3 and later), not to this
     persistence layer.
  2. Create-only PHASE RECORDS, NNNN-<phase>.json, a strictly contiguous append-only sequence
     (0001 upward) of opf.init.phase/v1 records; a record is never modified or removed, and
     before each append the on-disk tree is re-verified against the handle's retained record
     set: each record is RE-READ and its sha256 content digest checked against the digest
     recorded at creation. The digest is the authoritative content check (a filesystem may reuse
     a freed inode, so an unchanged device and inode cannot prove unchanged content, and a
     byte-identical replacement is definitionally the same record, so it does not refuse); the
     create-time device and inode are kept as defence-in-depth diagnostics. A foreign entry, a
     gap, or a record whose content changed refuses the append rather than being built upon.

Every substrate WRITE requires the LIVE held capability: the caller must be the recorded acquirer
(pid plus /proc start time), the capability must be unreleased, the retained anchor identity must
still hold, and the freshly resolved control root must carry the opf-oplock directory with the
capability's retained identity, so a write can never land under a different control tree than the
one the held flock excludes for.

OPF-D2B PR3a (PD-D2B-PR3-SCHEMA decisions 1 to 4) extends the substrate, never its ops/<op_id>/
record shape: the init PRE-STORE holder (_opf_oplock.InitHolder, the shared flock with no record and
no lease) is also a live writer, bound to its explicit product root rather than to the resolver (an
init store does not resolve until its manifest is published), so the plan and the first milestones
are persisted before the machine store exists; an operation handle is bound to the holder that began
or resumed it (by token), and only that holder or the capability attach_init_lease minted from it
extends it. resume_operation reopens an existing operation under its ORIGINAL id, never rewriting
plan.json; under the held lock the exact staging leftovers a killed publication leaves are swept
first, and an operation directory the sweep leaves empty (its plan was never published) is settled by
removal, reported. Two separately classified SIBLING homes, journals/ (the init effect journal) and
outcomes/<op_id>/ (bounded, create-only attempt outcomes), sit beside ops/ under opf-init/.

The RESUME CLASSIFIER (classify_operations) is READ-ONLY and PLAN-AWARE: it enumerates ops/ and
classifies each operation directory against its own plan.json into INTACT (a valid canonical plan
plus a contiguous, bounded, well-formed phase sequence; the resume input a later dispatch
consumes) or CANNOT-EVALUATE (anything unreadable, malformed, non-canonical, gapped, duplicated,
over-bound, foreign, or symlinked: fail-closed, named, and PRESERVED). It deletes NOTHING, ever.
A crashed operation's CONTROL legs (the lock's active.toml and lease) are cleared only by the
lock module's own explicit recovery (acquire_operation(recover=True), whose confirmed-dead gate
and _recover_stale are reused verbatim, never duplicated here); the substrate tree SURVIVES that
recovery, so the classifier keeps the evidence a resume decision needs. The journal's recover() and
reconcile-and-claim path is NEVER routed at init worktree or substrate paths: that path restores
a created entry by unlinking it back to absence, and a substrate record is resume evidence, so
its handling is the plan-aware classification above, not an unlink.

DISCLOSED RESIDUALS (this substrate does not cover): the classifier takes no lock, so a survey
raced by a LIVE writer can observe a mid-append tree (a resume dispatch runs it only after the
lock module's recovery has succeeded, which itself requires the recorded holder CONFIRMED DEAD);
a substrate home with no ops/ tree, or an empty ops/, reads as no recorded operation (the write
path creates both directories before the first record, so a crash between the two creates leaves
an empty home, which is genuinely nothing to resume); plan_digest is validated for GRAMMAR only
(sha256:<64 hex>), its digest BASIS being the plan producer's (PR3) to define and verify; deep
validation of the plan's composite members (versions, binding, head, acceptance, sets, and the
rest) is likewise the producer's; the phase-name vocabulary is not fixed here (PR3 names the
mutation phases); and everything the lock module itself discloses (advisory locking, the
stat-to-unlink adjacency, non-Linux identity degradation) applies unchanged to the composed legs.
Under the same cooperating-writer, no-lock model the pre-append re-verification is bounded, not
adversarial: the digest verify and the append are two steps, so an out-of-band writer that
retains an open descriptor to a record can mutate it AFTER the verify (the digest-verify-to-
append TOCTOU); the type and nlink hard refusals read a point-in-time fstat of the opened
object, so a hard link added after that fstat goes unobserved (the nlink-check TOCTOU); the
plan-membership re-check in the fresh listing is a NAME check, not a content re-verification, so a
same-name REPLACE of plan.json between its read and that listing is not detected (the plan-replace
TOCTOU); and a same-bytes-different-inode swap is ACCEPTED by design as harmless, the sha256 content digest
being the authoritative check and the retained identity a diagnostic only. Adversarial-grade
record integrity against a hostile writer with write access to the control root therefore
requires OS-level isolation of that root (the pack's system-hardening guidance,
SYSTEM-HARDENING.md), which this layer does not provide.

Run: python3 -I -B opf/tools/_opf_init_substrate.py --self-test
Exit: 0 self-test clean; 1 self-test failure; 2 refused precondition (missing containment
primitive or git binary), never a clean skip.
"""
import datetime
import hashlib
import json
import os
import re
import shutil
import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _containment        # noqa: E402
import _journal            # noqa: E402
import _opf_init_contract  # noqa: E402
import _opf_oplock         # noqa: E402
import _opf_store          # noqa: E402

# Fixed substrate names. The substrate home is a SIBLING of the lock's control directory under
# the same control root; ops/ holds one directory per operation id, and each operation directory
# holds exactly plan.json plus its create-only phase records.
SUBSTRATE_DIRNAME = "opf-init"
OPS_DIRNAME = "ops"
PLAN_NAME = "plan.json"
# OPF-D2B PR3a (PD-D2B-PR3-SCHEMA decision 4): separately classified SIBLING homes beneath opf-init/ for
# the init operation's effect journals and its attempt outcomes, so the ops/<op_id>/ record shape (exactly
# plan.json plus the create-only phase records) is kept unchanged. journals/ holds one framed journal
# transaction directory per effect group (the shared _journal framing, INTENT and COMPLETE only);
# outcomes/<op_id>/ holds create-only, contiguous NNNN-attempt.json records, one per attempt.
JOURNALS_DIRNAME = "journals"
OUTCOMES_DIRNAME = "outcomes"
HOME_KINDS = (OPS_DIRNAME, JOURNALS_DIRNAME, OUTCOMES_DIRNAME)
OUTCOME_FORMAT = "opf.init.outcome/v1"

# The frozen D2b plan identity (OPF-INIT-D2B.md): the closed top-level key set is the spec's Plan
# Format member list, frozen verbatim, and the PLAN schema is NOT this module's to extend. The
# phase record is this module's own v1 (PR2 owns the substrate records).
PLAN_FORMAT = "opf.init.plan/v1"
PLAN_OPERATION = "opf-init"
PHASE_FORMAT = "opf.init.phase/v1"
PLAN_TOP_KEYS = frozenset((
    "schema", "format", "operation", "operation_id", "versions", "binding", "head",
    "first_adoption", "inventory_digest", "acceptance", "application_time", "sets",
    "permitted_directories", "staging_set", "required_checks", "publication_boundaries",
    "recovery_policy", "plan_digest"))
PHASE_TOP_KEYS = frozenset(("schema", "format", "operation_id", "seq", "phase", "utc"))
_SCHEMA = 1

# Bounds: the plan rides the contract's frozen raw-bytes limit; a phase record rides the lock
# module's control-record cap; and the phase COUNT is bounded so a runaway writer cannot grow an
# operation directory unchecked (SECA resource-bounds).
MAX_PLAN_BYTES = _opf_init_contract.MAX_RAW_BYTES
MAX_PHASE_BYTES = _opf_oplock._MAX_RECORD_BYTES
MAX_PHASES = 128
# Attempt outcomes are diagnostics, bounded separately from the monotonic milestone phases so repeated
# retries never consume the phase bound (the plan's separate-bounded-semantics requirement).
MAX_OUTCOMES = 64
MAX_OUTCOME_BYTES = _opf_init_contract.MAX_RAW_BYTES

# An operation id is the capability's uuid4 string; a phase name is filename-safe by construction
# (lowercase alphanumerics with interior hyphens, bounded), so a record name never needs escaping
# and the control-character class is excluded structurally rather than filtered.
_OP_ID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")
_PHASE_NAME_PATTERN = r"[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?"
_PHASE_NAME_RE = re.compile(_PHASE_NAME_PATTERN + r"\Z")
_PHASE_FILE_RE = re.compile(r"([0-9]{4})-(" + _PHASE_NAME_PATTERN + r")\.json\Z")
_OUTCOME_FILE_RE = re.compile(r"([0-9]{4})-attempt\.json\Z")

# A phase record's utc field carries the oplock's _utc_now shape exactly (RFC 3339 UTC, second
# precision, Z suffix): the grammar is pinned here and the field ranges and calendar validity (no
# month 13, no February 30) are checked with strptime in _bad_phase_record.
_UTC_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
_UTC_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z\Z")

# Per-operation classification statuses, and the survey's own.
INTACT = "INTACT"
CANNOT_EVALUATE = "CANNOT-EVALUATE"
NO_SUBSTRATE = "NO-SUBSTRATE"
NO_OPERATIONS = "NO-OPERATIONS"
OPERATIONS = "OPERATIONS"


class InitSubstrateError(Exception):
    """A fail-closed resume-substrate error."""


class OpSubstrate:
    """The held per-operation substrate handle: inert data plus the retained descriptors.

    Carries the operation id, the retained ops/ and operation-directory descriptors and their
    identities, the plan's identity, sha256 content digest, and exact bytes, and the
    already-recorded phase roster (sequence, name, identity, digest) the pre-append tree
    re-verification checks against. Closing releases descriptors ONLY; the substrate tree is the
    durable record and is never removed by this handle.
    """
    __slots__ = ("op_id", "store_root", "_ops_fd", "_op_fd", "_op_ident", "_plan_ident",
                 "_plan_digest", "_plan_bytes", "_phases", "_next_seq", "_closed", "_token",
                 "swept")

    def __init__(self, op_id, store_root, ops_fd, op_fd, op_ident, plan_ident, plan_bytes,
                 token=None):
        self.op_id = op_id
        self.store_root = store_root
        # OPF-D2B PR3a: the init holder token this handle is bound to (None for a handle begun under
        # an ordinary capability). A write through this handle requires the same holder or the
        # capability attach_init_lease minted from it, so no other holder can extend it.
        self._token = token
        self.swept = ()
        self._ops_fd = ops_fd
        self._op_fd = op_fd
        self._op_ident = op_ident
        self._plan_ident = plan_ident
        self._plan_digest = hashlib.sha256(plan_bytes).hexdigest()
        self._plan_bytes = plan_bytes
        self._phases = []
        self._next_seq = 1
        self._closed = False


class OperationReport:
    """One classified operation: INTACT carries the parsed plan and the ordered (seq, phase)
    tuple; CANNOT-EVALUATE names the first defect in `detail` and the tree stays PRESERVED."""
    __slots__ = ("op_id", "status", "detail", "plan", "phases")

    def __init__(self, op_id, status, detail, plan, phases):
        self.op_id = op_id
        self.status = status
        self.detail = detail
        self.plan = plan
        self.phases = phases


class ResumeSurvey:
    """The classifier's whole answer: NO-SUBSTRATE (no opf-init home was ever created),
    NO-OPERATIONS (a home with nothing recorded), or OPERATIONS with one report per ops/ entry."""
    __slots__ = ("status", "operations")

    def __init__(self, status, operations):
        self.status = status
        self.operations = operations


# --- fail-closed record primitives ----------------------------------------------------------------


def _list_dir_fresh(parent_fd, name, ident, label):
    """List the directory `name` beneath a retained, already-trusted parent descriptor through a
    FRESH descriptor opened for this listing alone. os.listdir(fd) reads through a dup that
    SHARES the descriptor's open file description, including its directory read offset and, on
    btrfs, a readdir upper bound the kernel snapshots when the directory is opened, so a RETAINED
    dir fd lists a stale view (missing every entry created after the open, or nothing at all);
    and rewinding the retained descriptor is no cure, because lseek on a directory fd is EINVAL
    on macOS and a pre-2023 btrfs does not refresh its snapshot on the rewind. The fresh
    no-follow open beneath the retained parent refreshes the view without ever re-resolving the
    string path from the root, and the opened object must carry the caller's retained (device,
    inode) identity before it is trusted, so a swap between the caller's descriptor and this
    listing refuses rather than listing a different directory. Fail-closed throughout: raises
    InitSubstrateError, never a silent empty listing."""
    try:
        fd = os.open(name, _opf_oplock._DIR_OPEN_FLAGS, dir_fd=parent_fd)
    except OSError as exc:
        raise InitSubstrateError("cannot open {} for a fresh listing ({})".format(label, exc))
    try:
        st = os.fstat(fd)
        if (st.st_dev, st.st_ino) != ident:
            raise InitSubstrateError("{} identity changed under its parent; refusing the "
                                     "listing".format(label))
        return os.listdir(fd)
    except OSError as exc:
        raise InitSubstrateError("cannot list {} ({})".format(label, exc))
    finally:
        os.close(fd)


def _strict_json_loads(raw, label):
    """Parse a substrate record under the D2b contract's strict JSON discipline (duplicate keys,
    floats, NaN/Infinity, and out-of-range integers refused; container nesting bounded; the root
    an object). Raises InitSubstrateError; never returns a partial result."""
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise InitSubstrateError("{} is not decodable UTF-8".format(label))
    if not _opf_init_contract._nesting_ok(text):
        raise InitSubstrateError("{} exceeds the JSON nesting limit".format(label))
    try:
        doc = json.loads(text, object_pairs_hook=_opf_init_contract._reject_duplicates,
                         parse_float=_opf_init_contract._parse_float,
                         parse_int=_opf_init_contract._parse_int,
                         parse_constant=_opf_init_contract._parse_constant)
    except ValueError as exc:
        raise InitSubstrateError("{} is not strict JSON ({})".format(label, exc))
    if type(doc) is not dict:
        raise InitSubstrateError("{} is not a JSON object".format(label))
    return doc


def _canonical_or_refuse(doc, label):
    """The contract's canonical JSON bytes of `doc`, or a refusal (never a partial or ambient
    serialization)."""
    try:
        return _opf_init_contract.canonical_json_bytes(doc)
    except (ValueError, TypeError, UnicodeEncodeError) as exc:
        raise InitSubstrateError("{} cannot be canonicalized ({})".format(label, exc))


def _read_record_bytes(dir_fd, name, label, max_bytes):
    """Read a substrate record's BYTES beneath dir_fd, no-follow and fail-closed: the OPENED
    object (not just the name) must be a plain singly-linked regular file within the byte cap.
    Returns (raw bytes, the opened object's stat); ANY failure raises, so a record that cannot be
    read is a refusal, never nothing-to-check (check-fails-closed-on-unreadable); a mid-read
    OSError (an fstat or read failure AFTER a successful open) is contained as the same refusal,
    so a survey caller can CANNOT-EVALUATE the one record rather than abort whole."""
    try:
        fd = os.open(name, _opf_oplock._FILE_READ_FLAGS, dir_fd=dir_fd)
    except OSError as exc:
        raise InitSubstrateError("cannot read {} ({})".format(label, exc))
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise InitSubstrateError("{} is not a regular file".format(label))
        if st.st_nlink != 1:
            raise InitSubstrateError("{} link count is {}, expected exactly 1".format(
                label, st.st_nlink))
        if st.st_size > max_bytes:
            raise InitSubstrateError("{} is {} bytes, over the {}-byte cap".format(
                label, st.st_size, max_bytes))
        data = bytearray()
        while len(data) <= max_bytes:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            data += chunk
        if len(data) > max_bytes:
            raise InitSubstrateError("{} exceeds the {}-byte cap".format(label, max_bytes))
    except OSError as exc:
        raise InitSubstrateError("cannot read {} ({})".format(label, exc))
    finally:
        os.close(fd)
    return bytes(data), st


def _read_json_record(dir_fd, name, label, max_bytes):
    """Read and parse a substrate record beneath dir_fd (the _read_record_bytes no-follow
    discipline plus strict JSON). Returns (doc, raw bytes); ANY failure raises."""
    raw, _st = _read_record_bytes(dir_fd, name, label, max_bytes)
    return _strict_json_loads(raw, label), raw


def _validate_plan(raw, expected_op_id):
    """Validate plan BYTES as a persistable opf.init.plan/v1 record: the exact canonical
    serialization, the frozen closed top-level key set, the scalar identity fields, and an
    operation_id equal to the held capability's. Deep validation of the composite values is the
    plan PRODUCER's (PR3 and later); this layer refuses anything a later resume could not re-read
    fail-closed. Returns the parsed model."""
    if type(raw) is not bytes:
        raise InitSubstrateError("plan payload must be bytes")
    if not raw:
        raise InitSubstrateError("plan payload is empty")
    if len(raw) > MAX_PLAN_BYTES:
        raise InitSubstrateError("plan payload is {} bytes, over the {}-byte cap".format(
            len(raw), MAX_PLAN_BYTES))
    doc = _strict_json_loads(raw, "plan record")
    if _canonical_or_refuse(doc, "plan record") != raw:
        raise InitSubstrateError("plan record is not the exact canonical serialization; the "
                                 "substrate persists canonical bytes only")
    if set(doc) != set(PLAN_TOP_KEYS):
        raise InitSubstrateError("plan top-level keys {} do not equal the frozen set {}".format(
            sorted(doc), sorted(PLAN_TOP_KEYS)))
    if type(doc["schema"]) is not int or doc["schema"] != _SCHEMA:
        raise InitSubstrateError("plan schema must be the integer {}".format(_SCHEMA))
    if doc["format"] != PLAN_FORMAT:
        raise InitSubstrateError("plan format must be {!r}".format(PLAN_FORMAT))
    if doc["operation"] != PLAN_OPERATION:
        raise InitSubstrateError("plan operation must be {!r}".format(PLAN_OPERATION))
    op_id = doc["operation_id"]
    if type(op_id) is not str or not _OP_ID_RE.match(op_id):
        raise InitSubstrateError("plan operation_id is not a well-formed operation id")
    if op_id != expected_op_id:
        raise InitSubstrateError("plan operation_id {!r} does not equal the held capability's "
                                 "op_id {!r}".format(op_id, expected_op_id))
    dig = doc["plan_digest"]
    if type(dig) is not str or not _opf_init_contract._DIGEST_RE.match(dig):
        raise InitSubstrateError("plan_digest grammar must be sha256:<64 hex> (its digest BASIS "
                                 "is the plan producer's to verify)")
    return doc


def _bad_phase_record(doc, raw, op_id, seq, pname):
    """Reason string if a parsed phase record does not match its own file name and the frozen v1
    shape, else None (the classifier turns a reason into a CANNOT-EVALUATE, never a repair)."""
    try:
        if _opf_init_contract.canonical_json_bytes(doc) != raw:
            return "not the exact canonical serialization"
    except (ValueError, TypeError, UnicodeEncodeError):
        return "not canonicalizable"
    if set(doc) != set(PHASE_TOP_KEYS):
        return "top-level keys do not equal the frozen set"
    if type(doc["schema"]) is not int or doc["schema"] != _SCHEMA:
        return "schema must be the integer {}".format(_SCHEMA)
    if doc["format"] != PHASE_FORMAT:
        return "format must be {!r}".format(PHASE_FORMAT)
    if doc["operation_id"] != op_id:
        return "operation_id does not equal the operation directory name"
    if type(doc["seq"]) is not int or doc["seq"] != seq:
        return "seq does not equal the file name's sequence"
    if doc["phase"] != pname:
        return "phase does not equal the file name's phase"
    utc = doc["utc"]
    if type(utc) is not str or not _UTC_RE.match(utc):
        return "utc is not an RFC 3339 UTC timestamp of the recorded shape"
    try:
        datetime.datetime.strptime(utc, _UTC_FORMAT)
    except ValueError:
        return "utc is not a real calendar date and time"
    return None


# --- the substrate home (a sibling control home under the authoritative control root) -------------


def _open_control_root(store_root):
    """Resolve the RESOLVED store and open the AUTHORITATIVE control root exactly as the lock
    module does (three-way no-follow .git classification; git itself answers for a git store;
    never a fallback). Returns (control_root_fd, control_root_desc); the caller owns and closes
    the fd. Every failure refuses."""
    res = _opf_store.resolve_store(store_root)
    if res.status != _opf_store.RESOLVED:
        raise InitSubstrateError("no RESOLVED machine store at {} ({}: {}); the resume substrate "
                                 "requires a resolved store".format(
                                     store_root, res.status, res.detail))
    store_root_abs = str(res.store_root)
    try:
        store_fd = _opf_store._open_dir_nofollow(store_root_abs)
    except OSError as exc:
        raise InitSubstrateError("cannot open store root {} no-follow ({})".format(
            store_root_abs, exc))
    try:
        try:
            if _opf_oplock._classify_git_entry(store_fd, store_root_abs) == "absent":
                return os.dup(store_fd), store_root_abs
            desc = _opf_oplock._git_common_dir(store_root_abs)
        except _opf_oplock.OpLockError as exc:
            raise InitSubstrateError(str(exc))
        try:
            fd = _opf_store._open_dir_nofollow(desc)
        except OSError as exc:
            raise InitSubstrateError("cannot open common git dir {} no-follow ({})".format(
                desc, exc))
        return fd, desc
    finally:
        os.close(store_fd)


def _open_init_control_root(product_root):
    """The AUTHORITATIVE control root of an init operation's explicit product root (OPF-D2B PR3a):
    the common git directory git itself reports, opened no-follow, WITHOUT requiring a RESOLVED
    store (the store resolves only once init has published its manifest). The product root must be
    absolute and carry a .git entry; everything else refuses, never a fallback. Returns
    (control_root_fd, control_root_desc); the caller owns and closes the fd."""
    if type(product_root) is not str or not os.path.isabs(product_root):
        raise InitSubstrateError("init product root must be an absolute path")
    try:
        product_fd = _opf_store._open_dir_nofollow(product_root)
    except OSError as exc:
        raise InitSubstrateError("cannot open init product root {} no-follow ({})".format(
            product_root, exc))
    try:
        try:
            if _opf_oplock._classify_git_entry(product_fd, product_root) == "absent":
                raise InitSubstrateError("init product root {} carries no .git entry; refusing "
                                         "(the init binding is a git binding)".format(product_root))
            desc = _opf_oplock._git_common_dir(product_root)
        except _opf_oplock.OpLockError as exc:
            raise InitSubstrateError(str(exc))
        try:
            return _opf_store._open_dir_nofollow(desc), desc
        except OSError as exc:
            raise InitSubstrateError("cannot open common git dir {} no-follow ({})".format(
                desc, exc))
    finally:
        os.close(product_fd)


def _writer_root(writer):
    """(root, is_init) for a substrate writer: an InitHolder, or an init capability, binds to its
    explicit product root; an ordinary capability to its resolved store root."""
    if isinstance(writer, _opf_oplock.InitHolder):
        return writer.product_root, True
    if writer.init_root is not None:
        return writer.init_root, True
    return writer.store_root, False


def _open_writer_control_root(writer):
    root, is_init = _writer_root(writer)
    return _open_init_control_root(root) if is_init else _open_control_root(root)


def _require_live_capability(cap):
    """A substrate WRITE runs only under the LIVE held operation lock: an OpCapability or (OPF-D2B
    PR3a) an init pre-store InitHolder, unreleased and unspent, called by the recorded acquirer
    (pid plus /proc start time), with the retained anchor identity still holding (a regular,
    singly-linked file with the acquire-time device and inode). The gate is the lock module's own
    (_opf_oplock.require_live_holder), single-sourced. Anything else refuses before any write."""
    try:
        _opf_oplock.require_live_holder(cap)
    except _opf_oplock.OpLockError as exc:
        raise InitSubstrateError("substrate write refused: {}".format(exc))


def _require_bound_writer(sub, writer):
    """A write through `sub` requires a writer bound to the same operation: an ordinary
    capability whose op_id is the handle's; or, for a handle begun or resumed under an init holder,
    that same holder (by token) or the capability attach_init_lease minted from it (same token AND
    op_id). Never a different holder, and never an op-id comparison alone for an init handle."""
    if sub._token is None:
        if isinstance(writer, _opf_oplock.InitHolder) or sub.op_id != writer.op_id:
            raise InitSubstrateError("substrate handle op_id {!r} does not match the held "
                                     "capability's {!r}".format(sub.op_id,
                                                                getattr(writer, "op_id", None)))
        return
    if isinstance(writer, _opf_oplock.InitHolder):
        if writer.token is not sub._token:
            raise InitSubstrateError("substrate handle op_id {!r} is bound to a different init "
                                     "holder".format(sub.op_id))
        return
    if writer.init_token is not sub._token or writer.op_id != sub.op_id:
        raise InitSubstrateError("substrate handle op_id {!r} does not match the held "
                                 "capability's {!r}".format(sub.op_id, writer.op_id))


def _open_ops_for_write(cap):
    """The ops/ home for a WRITE (see _open_home_for_write)."""
    return _open_home_for_write(cap, OPS_DIRNAME)


def _open_home_for_write(cap, kind):
    """Open (creating each single component on genuine absence) the substrate home `kind` (ops/,
    journals/, or outcomes/) for a WRITE under the held capability or init holder. The freshly
    resolved control root must still carry the lock's opf-oplock directory with the writer's
    retained identity, so the write lands under the SAME control tree the held flock excludes for;
    a mismatch refuses. Returns the home dir fd; the caller owns and closes it."""
    if kind not in HOME_KINDS:
        raise InitSubstrateError("unknown substrate home {!r}".format(kind))
    control_root_fd, desc = _open_writer_control_root(cap)
    home_fd = None
    try:
        try:
            st = os.stat(_opf_oplock.CONTROL_DIRNAME, dir_fd=control_root_fd,
                         follow_symlinks=False)
        except OSError as exc:
            raise InitSubstrateError("cannot stat the sibling control directory under {} "
                                     "({})".format(desc, exc))
        if not stat.S_ISDIR(st.st_mode) or (st.st_dev, st.st_ino) != cap._ctl_ident:
            raise InitSubstrateError("the resolved control root {} does not carry the held "
                                     "capability's control directory; refusing to write the "
                                     "substrate under a different control tree".format(desc))
        try:
            home_fd = _opf_oplock._open_control_dir(control_root_fd, desc,
                                                    dirname=SUBSTRATE_DIRNAME)
            return _opf_oplock._open_control_dir(home_fd,
                                                 "{}/{}".format(desc, SUBSTRATE_DIRNAME),
                                                 dirname=kind)
        except _opf_oplock.OpLockError as exc:
            raise InitSubstrateError(str(exc))
    finally:
        if home_fd is not None:
            os.close(home_fd)
        os.close(control_root_fd)


# --- create the per-operation tree and its create-only records ------------------------------------


def begin_operation(cap, plan_bytes):
    """Create the per-operation substrate tree ops/<op_id>/ with its CREATE-ONLY plan.json under
    the LIVE held capability, and return the OpSubstrate handle later appends run through.

    Refuse-before-create throughout: the capability and the plan bytes are validated before any
    filesystem write; a pre-existing operation directory refuses (operation ids are never
    reused); and a failure after the directory exists unwinds ONLY this facility's own
    just-created artefacts (the verified just-written plan, then the empty directory), never a
    pre-existing record.
    """
    _require_live_capability(cap)
    if isinstance(cap, _opf_oplock.InitHolder):
        # OPF-D2B PR3a: an init holder is not yet bound to an operation; the operation is the one
        # the plan names (its id is the plan producer's fresh uuid4), and the handle is bound to
        # this holder's token so only this holder, or the capability minted from it, extends it.
        try:
            op_id = _strict_json_loads(plan_bytes, "plan record")["operation_id"] \
                if type(plan_bytes) is bytes else None
        except (InitSubstrateError, KeyError):
            op_id = None
        token = cap.token
    else:
        op_id = cap.op_id
        token = cap.init_token
    _validate_plan(plan_bytes, op_id)
    ops_fd = _open_ops_for_write(cap)
    op_fd = None
    plan_fd = None
    created_dir = False
    plan_created = False
    plan_ident = None
    try:
        try:
            os.mkdir(op_id, 0o755, dir_fd=ops_fd)
        except FileExistsError:
            raise InitSubstrateError("operation directory {} already exists; operation ids are "
                                     "never reused and a pre-existing tree is never adopted "
                                     "silently".format(op_id))
        except OSError as exc:
            raise InitSubstrateError("cannot create operation directory {} ({})".format(
                op_id, exc))
        created_dir = True
        try:
            os.fsync(ops_fd)
        except OSError as exc:
            raise InitSubstrateError("cannot fsync the ops directory after creation "
                                     "({})".format(exc))
        try:
            op_fd = _opf_oplock._open_dir_at(ops_fd, op_id, "ops/{}".format(op_id))
            _opf_oplock._validate_ctl_dir_fd(op_fd, "ops/{}".format(op_id))
            plan_fd, plan_ident = _opf_oplock._create_control_file(op_fd, PLAN_NAME, plan_bytes,
                                                                   "plan record")
        except _opf_oplock.OpLockError as exc:
            raise InitSubstrateError(str(exc))
        plan_created = True
        os.close(plan_fd)
        plan_fd = None
        op_st = os.fstat(op_fd)
        return OpSubstrate(op_id=op_id, store_root=_writer_root(cap)[0], ops_fd=ops_fd,
                           op_fd=op_fd, op_ident=(op_st.st_dev, op_st.st_ino),
                           plan_ident=plan_ident, plan_bytes=plan_bytes, token=token)
    except BaseException as exc:
        # Unwind ONLY this facility's own just-created artefacts; every unwind failure is
        # collected, never swallowed. The composed _create_control_file unlinks its own torn file
        # when that cleanup succeeds, leaving the directory empty and removable; when the cleanup
        # itself fails, it names the leftover and the rmdir error below is collected too.
        unwind = []
        if plan_created:
            try:
                _opf_oplock._verified_unlink(op_fd, PLAN_NAME, plan_ident, plan_bytes,
                                             "plan record (unwind)")
            except _opf_oplock.OpLockError as uexc:
                unwind.append(str(uexc))
        if created_dir:
            try:
                os.rmdir(op_id, dir_fd=ops_fd)
                os.fsync(ops_fd)
            except OSError as uexc:
                unwind.append("cannot remove the just-created operation directory "
                              "({})".format(uexc))
        for open_fd in (plan_fd, op_fd, ops_fd):
            if open_fd is not None:
                try:
                    os.close(open_fd)
                except OSError as uexc:
                    unwind.append("cannot close a substrate descriptor ({})".format(uexc))
        if unwind and isinstance(exc, Exception):
            raise InitSubstrateError("{}; additionally the unwind failed: {}".format(
                exc, "; ".join(unwind))) from exc
        raise


def _verify_tree(sub):
    """Re-verify the on-disk operation tree against the handle's retained record set before an
    append: the ops/ entry still names the retained operation directory, the directory holds
    EXACTLY the plan plus the already-recorded phase records, and each record, re-read no-follow
    as a plain singly-linked regular file, still carries the sha256 content digest recorded at
    its creation. The digest is the authoritative content check: a filesystem may reuse a freed
    inode, so an unchanged (device, inode) cannot prove unchanged content, and a byte-identical
    replacement is definitionally the same record, so a changed identity with matching content
    does not refuse; the retained identity stays as defence-in-depth diagnostics, naming an
    in-place rewrite apart from a replacement in the refusal. Anything else refuses fail-closed
    and PRESERVES the tree (a substrate record is create-only and never repaired in place)."""
    try:
        name_st = os.stat(sub.op_id, dir_fd=sub._ops_fd, follow_symlinks=False)
    except OSError as exc:
        raise InitSubstrateError("cannot stat operation directory {} ({})".format(sub.op_id, exc))
    if not stat.S_ISDIR(name_st.st_mode) \
            or (name_st.st_dev, name_st.st_ino) != sub._op_ident:
        raise InitSubstrateError("operation directory {} identity changed while held".format(
            sub.op_id))
    expected = {PLAN_NAME: (sub._plan_ident, sub._plan_digest, MAX_PLAN_BYTES)}
    for _seq, rname, rident, rdigest in sub._phases:
        expected[rname] = (rident, rdigest, MAX_PHASE_BYTES)
    present = sorted(_list_dir_fresh(sub._ops_fd, sub.op_id, sub._op_ident,
                                     "operation directory {}".format(sub.op_id)))
    if sorted(expected) != present:
        raise InitSubstrateError("operation directory {} does not hold exactly the recorded tree "
                                 "(expected {}, found {}); refusing the append".format(
                                     sub.op_id, sorted(expected), present))
    for rname, (rident, rdigest, rcap) in expected.items():
        raw, st = _read_record_bytes(sub._op_fd, rname, "recorded {}".format(rname), rcap)
        if hashlib.sha256(raw).hexdigest() != rdigest:
            how = "rewritten in place" if (st.st_dev, st.st_ino) == rident else "replaced"
            raise InitSubstrateError("recorded {} content changed while held ({}; a substrate "
                                     "record is create-only and never modified)".format(
                                         rname, how))


def record_phase(sub, cap, phase):
    """Append the next CREATE-ONLY phase record under the LIVE held capability. The sequence is
    strictly contiguous from 0001, the phase name is filename-safe by construction, the count is
    bounded, and the on-disk tree is re-verified against the handle's retained record set (the
    per-record sha256 content digests) before the append, so a foreign entry, a gap, or a record
    whose content changed refuses rather than being built upon. Returns the record's file
    name."""
    if not isinstance(sub, OpSubstrate):
        raise InitSubstrateError("record_phase requires an OpSubstrate handle")
    if sub._closed:
        raise InitSubstrateError("substrate handle already closed")
    _require_live_capability(cap)
    _require_bound_writer(sub, cap)
    if type(phase) is not str or not _PHASE_NAME_RE.match(phase):
        raise InitSubstrateError("phase name must be a bounded lowercase alphanumeric-and-hyphen "
                                 "token")
    seq = sub._next_seq
    if seq > MAX_PHASES:
        raise InitSubstrateError("phase count would exceed the {}-record bound".format(
            MAX_PHASES))
    _verify_tree(sub)
    doc = dict(schema=_SCHEMA, format=PHASE_FORMAT, operation_id=sub.op_id, seq=seq,
               phase=phase, utc=_opf_oplock._utc_now())
    payload = _canonical_or_refuse(doc, "phase record")
    if len(payload) > MAX_PHASE_BYTES:
        raise InitSubstrateError("phase record is {} bytes, over the {}-byte cap".format(
            len(payload), MAX_PHASE_BYTES))
    name = "{:04d}-{}.json".format(seq, phase)
    try:
        fd, ident = _opf_oplock._create_control_file(sub._op_fd, name, payload,
                                                     "phase record {}".format(name))
    except _opf_oplock.OpLockError as exc:
        raise InitSubstrateError(str(exc))
    os.close(fd)
    sub._phases.append((seq, name, ident, hashlib.sha256(payload).hexdigest()))
    sub._next_seq = seq + 1
    return name


def close_operation(sub):
    """Close the handle's retained descriptors. The substrate TREE is the durable resume record
    and is never removed here; closing releases descriptors only, and a handle closes once."""
    if not isinstance(sub, OpSubstrate):
        raise InitSubstrateError("close_operation requires an OpSubstrate handle")
    if sub._closed:
        raise InitSubstrateError("substrate handle already closed")
    errors = []
    for open_fd in (sub._op_fd, sub._ops_fd):
        try:
            os.close(open_fd)
        except OSError as exc:
            errors.append("cannot close a substrate descriptor ({})".format(exc))
    sub._closed = True
    if errors:
        raise InitSubstrateError("close completed with failures: " + "; ".join(errors))


# --- OPF-D2B PR3a: operation-bound resume, the interrupted-publication sweep, the sibling homes ------
#
# PD-D2B-PR3-SCHEMA decision 2: a retry resumes its ORIGINAL operation through resume_operation, an
# explicit operation-bound handle over the existing ops/<op_id>/ tree, re-read through contained
# descriptors; plan.json is never overwritten and no op id is mutated or fabricated. Decision 3: a
# publication (plan, phase, or outcome record) killed mid-way leaves at most the lock module's exact
# staging leftover, which the holder of the lock sweeps before it reads the tree, so a record killed
# between its link and its staging unlink is back to one link; an operation directory the sweep leaves
# EMPTY (its plan was never published) authorized nothing and is discarded explicitly, and reported.


def _split_staging_name(entry):
    """The record name a substrate staging leftover was staging (a plan, phase, or outcome record),
    or None when `entry` is not EXACTLY a staging name the lock module's publication could have
    produced for such a record."""
    marker = _opf_oplock._STAGING_MARKER
    if type(entry) is not str or not entry.startswith(".") or marker not in entry:
        return None
    inner = entry[1:entry.rindex(marker)]
    if inner != PLAN_NAME and not _PHASE_FILE_RE.match(inner) \
            and not _OUTCOME_FILE_RE.match(inner):
        return None
    return inner if _opf_oplock._is_staging_name(entry, inner) else None


def _sweep_record_staging(parent_fd, name, dir_fd, dir_ident, label):
    """Remove the staging leftovers a publication killed mid-way left in the record directory
    `name` (dir_fd) beneath parent_fd. Called ONLY under the held lock, so no cooperating publisher
    is mid-publication: the lock module's own sweep discipline. Only a plain regular file whose name
    is EXACTLY a plan, phase, or outcome staging name is removed; a staging name bound to anything
    else refuses (manual intervention). Returns the sorted names removed."""
    removed = []
    for entry in sorted(_list_dir_fresh(parent_fd, name, dir_ident, label)):
        if _split_staging_name(entry) is None:
            continue
        try:
            st = os.stat(entry, dir_fd=dir_fd, follow_symlinks=False)
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise InitSubstrateError("cannot stat staging leftover {} in {} ({})".format(
                entry, label, exc))
        if not stat.S_ISREG(st.st_mode):
            raise InitSubstrateError("staging leftover {} in {} is not a regular file; refusing "
                                     "(manual intervention required)".format(entry, label))
        try:
            os.unlink(entry, dir_fd=dir_fd)
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise InitSubstrateError("cannot remove staging leftover {} in {} ({})".format(
                entry, label, exc))
        removed.append(entry)
    if removed:
        try:
            os.fsync(dir_fd)
        except OSError as exc:
            raise InitSubstrateError("cannot fsync {} after removing staging leftovers "
                                     "({})".format(label, exc))
    return removed


def _open_existing_op_dir(ops_fd, operation_id):
    """Open the existing operation directory no-follow beneath ops_fd, validated as a control
    directory; returns (op_fd, op_ident). Absence, a symlink, or a wrong type refuses."""
    try:
        st = os.stat(operation_id, dir_fd=ops_fd, follow_symlinks=False)
    except FileNotFoundError:
        raise InitSubstrateError("no recorded operation {}".format(operation_id))
    except OSError as exc:
        raise InitSubstrateError("cannot stat operation directory {} ({})".format(
            operation_id, exc))
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
        raise InitSubstrateError("operation directory {} is not a plain directory".format(
            operation_id))
    label = "ops/{}".format(operation_id)
    try:
        op_fd = _opf_oplock._open_dir_at(ops_fd, operation_id, label)
    except _opf_oplock.OpLockError as exc:
        raise InitSubstrateError(str(exc))
    try:
        op_st = _opf_oplock._validate_ctl_dir_fd(op_fd, label)
    except _opf_oplock.OpLockError as exc:
        os.close(op_fd)
        raise InitSubstrateError(str(exc))
    if (op_st.st_dev, op_st.st_ino) != (st.st_dev, st.st_ino):
        os.close(op_fd)
        raise InitSubstrateError("operation directory {} changed between its stat and its "
                                 "open".format(operation_id))
    return op_fd, (op_st.st_dev, op_st.st_ino)


def resume_operation(writer, operation_id, expected_plan_digest):
    """Reopen the EXISTING operation `operation_id` under the live writer (an InitHolder, or the
    capability bound to that operation), returning an operation-bound OpSubstrate whose later
    record_phase appends continue the recorded sequence (decision 2). Under the held lock the
    operation directory's staging leftovers are swept first (decision 3); then the plan is re-read
    no-follow and validated, its plan_digest must EQUAL `expected_plan_digest` (the caller's
    re-derived basis: changed evidence refuses), and every phase record is re-read, shape-checked,
    and bound into the handle's roster by its sha256 content digest, exactly as begin_operation and
    record_phase would have recorded them. plan.json is NEVER overwritten; a foreign entry, a gap, a
    malformed record, or an over-bound tree refuses (the tree is preserved). The names the sweep
    removed are in the handle's `swept` attribute."""
    _require_live_capability(writer)
    if type(operation_id) is not str or not _OP_ID_RE.match(operation_id):
        raise InitSubstrateError("operation id {!r} is not well-formed".format(operation_id))
    if type(expected_plan_digest) is not str \
            or not _opf_init_contract._DIGEST_RE.match(expected_plan_digest):
        raise InitSubstrateError("expected plan digest must be sha256:<64 hex>")
    if isinstance(writer, _opf_oplock.InitHolder):
        token = writer.token
    else:
        if writer.op_id != operation_id:
            raise InitSubstrateError("the held capability is bound to operation {!r}, not "
                                     "{!r}".format(writer.op_id, operation_id))
        token = writer.init_token
    ops_fd = _open_ops_for_write(writer)
    op_fd = None
    try:
        op_fd, op_ident = _open_existing_op_dir(ops_fd, operation_id)
        label = "ops/{}".format(operation_id)
        swept = _sweep_record_staging(ops_fd, operation_id, op_fd, op_ident, label)
        plan_raw, plan_st = _read_record_bytes(op_fd, PLAN_NAME, "plan record", MAX_PLAN_BYTES)
        plan = _validate_plan(plan_raw, operation_id)
        if plan["plan_digest"] != expected_plan_digest:
            raise InitSubstrateError("operation {} plan_digest {} does not equal the expected {}; "
                                     "changed evidence is never resumed".format(
                                         operation_id, plan["plan_digest"], expected_plan_digest))
        entries = sorted(_list_dir_fresh(ops_fd, operation_id, op_ident, label))
        if len(entries) > MAX_PHASES + 1:
            raise InitSubstrateError("{} entries exceed the plan plus {}-phase-record bound".format(
                len(entries), MAX_PHASES))
        if PLAN_NAME not in entries:
            raise InitSubstrateError("plan record is absent from the fresh listing; refusing")
        roster = []
        for entry in entries:
            if entry == PLAN_NAME:
                continue
            m = _PHASE_FILE_RE.match(entry)
            if m is None:
                raise InitSubstrateError("foreign entry {!r} in operation directory {}".format(
                    entry, operation_id))
            seq, pname = int(m.group(1)), m.group(2)
            raw, st = _read_record_bytes(op_fd, entry, "phase record {}".format(entry),
                                         MAX_PHASE_BYTES)
            reason = _bad_phase_record(_strict_json_loads(raw, "phase record {}".format(entry)),
                                       raw, operation_id, seq, pname)
            if reason is not None:
                raise InitSubstrateError("phase record {}: {}".format(entry, reason))
            roster.append((seq, entry, (st.st_dev, st.st_ino), hashlib.sha256(raw).hexdigest()))
        roster.sort()
        if [r[0] for r in roster] != list(range(1, len(roster) + 1)):
            raise InitSubstrateError("phase sequence is not contiguous from 0001")
        sub = OpSubstrate(op_id=operation_id, store_root=_writer_root(writer)[0], ops_fd=ops_fd,
                          op_fd=op_fd, op_ident=op_ident,
                          plan_ident=(plan_st.st_dev, plan_st.st_ino), plan_bytes=plan_raw,
                          token=token)
        sub._phases = roster
        sub._next_seq = len(roster) + 1
        sub.swept = tuple(swept)
        return sub
    except BaseException:
        for open_fd in (op_fd, ops_fd):
            if open_fd is not None:
                try:
                    os.close(open_fd)
                except OSError:
                    pass
        raise


def recorded_phases(sub):
    """The handle's recorded (seq, phase-name) roster, in order (read from the handle, which was
    bound to the on-disk records at begin, resume, or append)."""
    if not isinstance(sub, OpSubstrate):
        raise InitSubstrateError("recorded_phases requires an OpSubstrate handle")
    return tuple((seq, _PHASE_FILE_RE.match(name).group(2)) for seq, name, _i, _d in sub._phases)


def settle_operation(writer, operation_id):
    """Under the live writer, sweep the operation directory's staging leftovers (decision 3) and,
    when that leaves it EMPTY, remove it: an operation whose plan was never published authorized no
    worktree write, so it carries no evidence to preserve. Returns (swept names, discarded). A
    directory with anything else left is untouched beyond the exact staging sweep (discarded False);
    the caller's classification then grades it."""
    _require_live_capability(writer)
    if type(operation_id) is not str or not _OP_ID_RE.match(operation_id):
        raise InitSubstrateError("operation id {!r} is not well-formed".format(operation_id))
    ops_fd = _open_ops_for_write(writer)
    try:
        op_fd, op_ident = _open_existing_op_dir(ops_fd, operation_id)
        label = "ops/{}".format(operation_id)
        try:
            swept = _sweep_record_staging(ops_fd, operation_id, op_fd, op_ident, label)
            remaining = _list_dir_fresh(ops_fd, operation_id, op_ident, label)
        finally:
            os.close(op_fd)
        if remaining:
            return tuple(swept), False
        try:
            os.rmdir(operation_id, dir_fd=ops_fd)
            os.fsync(ops_fd)
        except OSError as exc:
            raise InitSubstrateError("cannot remove the empty operation directory {} ({})".format(
                operation_id, exc))
        return tuple(swept), True
    finally:
        os.close(ops_fd)


def discard_empty_operation(writer, operation_id):
    """settle_operation that REFUSES unless the directory was discarded: the swept names on
    success; a directory with anything else left is preserved evidence, never discarded."""
    swept, discarded = settle_operation(writer, operation_id)
    if not discarded:
        raise InitSubstrateError("operation directory {} is not empty; it is preserved evidence, "
                                 "never discarded".format(operation_id))
    return swept


def open_journal_home(writer):
    """The opf-init/journals/ home descriptor for the init effect journal (decision 4), opened (and
    created on genuine absence) under the live writer; the caller owns and closes it."""
    _require_live_capability(writer)
    return _open_home_for_write(writer, JOURNALS_DIRNAME)


def _validate_outcome(raw, op_id):
    """Outcome record bytes: canonical, bounded, strict JSON carrying the frozen outcome format for
    this operation (the outcome envelope's own fields are the producer's, the init operation
    layer's, to validate deeply)."""
    if type(raw) is not bytes or not raw or len(raw) > MAX_OUTCOME_BYTES:
        raise InitSubstrateError("outcome payload must be non-empty bytes within {} bytes".format(
            MAX_OUTCOME_BYTES))
    doc = _strict_json_loads(raw, "outcome record")
    if _canonical_or_refuse(doc, "outcome record") != raw:
        raise InitSubstrateError("outcome record is not the exact canonical serialization")
    if doc.get("format") != OUTCOME_FORMAT or doc.get("operation_id") != op_id \
            or type(doc.get("schema")) is not int or doc.get("schema") != _SCHEMA:
        raise InitSubstrateError("outcome record must carry schema {}, format {!r}, and the "
                                 "operation id {}".format(_SCHEMA, OUTCOME_FORMAT, op_id))
    return doc


def record_outcome(sub, writer, payload):
    """Append one create-only attempt outcome, NNNN-attempt.json, under outcomes/<op_id>/ for the
    operation `sub` is bound to, through the live writer bound to it (an InitHolder, including the
    one detach_init_lease hands back, or the capability minted from it). The sequence is contiguous
    from 0001 and bounded by MAX_OUTCOMES, separately from the milestone phases; staging leftovers
    are swept first, and a foreign entry or a gap refuses. Returns the record name."""
    if not isinstance(sub, OpSubstrate):
        raise InitSubstrateError("record_outcome requires an OpSubstrate handle")
    _require_live_capability(writer)
    _require_bound_writer(sub, writer)
    _validate_outcome(payload, sub.op_id)
    home_fd = _open_home_for_write(writer, OUTCOMES_DIRNAME)
    op_fd = None
    try:
        control_home = "{}/{}".format(SUBSTRATE_DIRNAME, OUTCOMES_DIRNAME)
        try:
            op_fd = _opf_oplock._open_control_dir(home_fd, control_home, dirname=sub.op_id)
        except _opf_oplock.OpLockError as exc:
            raise InitSubstrateError(str(exc))
        st = os.fstat(op_fd)
        label = "outcomes/{}".format(sub.op_id)
        _sweep_record_staging(home_fd, sub.op_id, op_fd, (st.st_dev, st.st_ino), label)
        entries = sorted(_list_dir_fresh(home_fd, sub.op_id, (st.st_dev, st.st_ino), label))
        seqs = []
        for entry in entries:
            m = _OUTCOME_FILE_RE.match(entry)
            if m is None:
                raise InitSubstrateError("foreign entry {!r} in {}".format(entry, label))
            seqs.append(int(m.group(1)))
        if seqs != list(range(1, len(seqs) + 1)):
            raise InitSubstrateError("{} is not a contiguous outcome sequence".format(label))
        seq = len(seqs) + 1
        if seq > MAX_OUTCOMES:
            raise InitSubstrateError("outcome count would exceed the {}-record bound".format(
                MAX_OUTCOMES))
        name = "{:04d}-attempt.json".format(seq)
        try:
            fd, _ident = _opf_oplock._create_control_file(op_fd, name, payload,
                                                          "outcome record {}".format(name))
        except _opf_oplock.OpLockError as exc:
            raise InitSubstrateError(str(exc))
        os.close(fd)
        return name
    finally:
        for open_fd in (op_fd, home_fd):
            if open_fd is not None:
                os.close(open_fd)


def read_outcomes(product_root, operation_id):
    """READ-ONLY: the parsed attempt outcomes recorded for an init operation, in order (an empty
    tuple when none was ever recorded). A malformed, foreign, gapped, or unreadable entry refuses."""
    if type(operation_id) is not str or not _OP_ID_RE.match(operation_id):
        raise InitSubstrateError("operation id {!r} is not well-formed".format(operation_id))
    control_root_fd, desc = _open_init_control_root(product_root)
    fds = [control_root_fd]
    try:
        cur = control_root_fd
        for comp in (SUBSTRATE_DIRNAME, OUTCOMES_DIRNAME, operation_id):
            try:
                st = _opf_oplock._lstat_at(cur, comp, comp)
            except _opf_oplock.OpLockError as exc:
                raise InitSubstrateError(str(exc))
            if st is None:
                return ()
            if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
                raise InitSubstrateError("{} is not a plain directory".format(comp))
            try:
                nfd = _opf_oplock._open_dir_at(cur, comp, comp)
            except _opf_oplock.OpLockError as exc:
                raise InitSubstrateError(str(exc))
            fds.append(nfd)
            parent, cur = cur, nfd
        nst = os.fstat(cur)
        entries = sorted(_list_dir_fresh(parent, operation_id, (nst.st_dev, nst.st_ino),
                                         "outcomes/{}".format(operation_id)))
        out = []
        for i, entry in enumerate(entries, 1):
            m = _OUTCOME_FILE_RE.match(entry)
            if m is None or int(m.group(1)) != i:
                raise InitSubstrateError("outcomes/{} holds a foreign or out-of-sequence entry "
                                         "{!r}".format(operation_id, entry))
            doc, raw = _read_json_record(cur, entry, "outcome record " + entry, MAX_OUTCOME_BYTES)
            _validate_outcome(raw, operation_id)
            out.append(doc)
        return tuple(out)
    finally:
        for open_fd in reversed(fds):
            try:
                os.close(open_fd)
            except OSError:
                pass

# --- the plan-aware resume classifier (read-only) --------------------------------------------------


def _classify_entry(ops_fd, name):
    """Classify one ops/ entry against its own plan: INTACT or a named CANNOT-EVALUATE. A defect
    refuses THIS entry only, and nothing is deleted or repaired."""
    def ce(detail):
        return OperationReport(name, CANNOT_EVALUATE, detail, None, ())
    if not _OP_ID_RE.match(name):
        return ce("entry name is not a well-formed operation id")
    try:
        st = os.stat(name, dir_fd=ops_fd, follow_symlinks=False)
    except OSError as exc:
        return ce("cannot stat entry ({})".format(exc))
    if stat.S_ISLNK(st.st_mode):
        return ce("entry is a symlink; never followed")
    if not stat.S_ISDIR(st.st_mode):
        return ce("entry is not a directory")
    try:
        op_fd = _opf_oplock._open_dir_at(ops_fd, name, "ops/{}".format(name))
    except _opf_oplock.OpLockError as exc:
        return ce(str(exc))
    try:
        try:
            op_st = _opf_oplock._validate_ctl_dir_fd(op_fd, "ops/{}".format(name))
        except _opf_oplock.OpLockError as exc:
            return ce(str(exc))
        try:
            _doc, plan_raw = _read_json_record(op_fd, PLAN_NAME, "plan record", MAX_PLAN_BYTES)
            plan = _validate_plan(plan_raw, name)
        except InitSubstrateError as exc:
            return ce(str(exc))
        try:
            entries = sorted(_list_dir_fresh(ops_fd, name, (op_st.st_dev, op_st.st_ino),
                                             "ops/{}".format(name)))
        except InitSubstrateError as exc:
            return ce(str(exc))
        if len(entries) > MAX_PHASES + 1:
            return ce("{} entries exceed the plan plus {}-phase-record bound".format(
                len(entries), MAX_PHASES))
        if PLAN_NAME not in entries:
            return ce("plan record is absent from the fresh listing (removed after it was "
                      "read); refusing the stale plan")
        phases = []
        seen = set()
        for entry in entries:
            if entry == PLAN_NAME:
                continue
            m = _PHASE_FILE_RE.match(entry)
            if m is None:
                return ce("foreign entry {!r} in the operation directory".format(entry))
            seq = int(m.group(1))
            pname = m.group(2)
            if seq > MAX_PHASES:
                return ce("phase sequence {:04d} exceeds the {}-record bound".format(
                    seq, MAX_PHASES))
            if seq in seen:
                return ce("duplicate phase sequence {:04d}".format(seq))
            seen.add(seq)
            try:
                doc, raw = _read_json_record(op_fd, entry, "phase record {}".format(entry),
                                             MAX_PHASE_BYTES)
            except InitSubstrateError as exc:
                return ce(str(exc))
            reason = _bad_phase_record(doc, raw, name, seq, pname)
            if reason is not None:
                return ce("phase record {}: {}".format(entry, reason))
            phases.append((seq, pname))
        phases.sort()
        if [s for s, _p in phases] != list(range(1, len(phases) + 1)):
            return ce("phase sequence is not contiguous from 0001")
        return OperationReport(name, INTACT, "", plan, tuple(phases))
    finally:
        os.close(op_fd)


def classify_operations(store_root):
    """READ-ONLY plan-aware classification of the resume substrate at `store_root`.

    Returns a ResumeSurvey: NO-SUBSTRATE on a genuinely absent opf-init home, NO-OPERATIONS on a
    home with nothing recorded, else OPERATIONS with one OperationReport per ops/ entry (INTACT
    or a named per-entry CANNOT-EVALUATE). It deletes NOTHING and repairs NOTHING; a store or
    control root that cannot be resolved, or a substrate home or ops tree of the wrong type,
    refuses outright rather than reading as a clean survey. The classifier takes no lock: a
    resume dispatch runs it only after the lock module's explicit recovery has succeeded.
    """
    control_root_fd, desc = _open_control_root(store_root)
    return _classify_at(control_root_fd, desc)


def classify_init_operations(product_root):
    """READ-ONLY classify_operations for an init operation's explicit product root (OPF-D2B PR3a):
    the same plan-aware classification over the authoritative control root, WITHOUT requiring a
    RESOLVED store (an interrupted init has no resolvable store yet). Deletes and repairs nothing."""
    control_root_fd, desc = _open_init_control_root(product_root)
    return _classify_at(control_root_fd, desc)


def _classify_at(control_root_fd, desc):
    """The body of the read-only classifiers over an opened control root; closes it."""
    home_fd = None
    ops_fd = None
    try:
        home_label = "{}/{}".format(desc, SUBSTRATE_DIRNAME)
        try:
            st = _opf_oplock._lstat_at(control_root_fd, SUBSTRATE_DIRNAME, home_label)
        except _opf_oplock.OpLockError as exc:
            raise InitSubstrateError(str(exc))
        if st is None:
            return ResumeSurvey(NO_SUBSTRATE, ())
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
            raise InitSubstrateError("substrate home {} is not a plain directory; refusing "
                                     "(manual intervention required)".format(home_label))
        ops_label = "{}/{}".format(home_label, OPS_DIRNAME)
        try:
            home_fd = _opf_oplock._open_dir_at(control_root_fd, SUBSTRATE_DIRNAME, home_label)
            _opf_oplock._validate_ctl_dir_fd(home_fd, home_label)
            st = _opf_oplock._lstat_at(home_fd, OPS_DIRNAME, ops_label)
        except _opf_oplock.OpLockError as exc:
            raise InitSubstrateError(str(exc))
        if st is None:
            return ResumeSurvey(NO_OPERATIONS, ())
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
            raise InitSubstrateError("substrate ops tree {} is not a plain directory; refusing "
                                     "(manual intervention required)".format(ops_label))
        try:
            ops_fd = _opf_oplock._open_dir_at(home_fd, OPS_DIRNAME, ops_label)
            ops_st = _opf_oplock._validate_ctl_dir_fd(ops_fd, ops_label)
        except _opf_oplock.OpLockError as exc:
            raise InitSubstrateError(str(exc))
        names = sorted(_list_dir_fresh(home_fd, OPS_DIRNAME,
                                       (ops_st.st_dev, ops_st.st_ino), ops_label))
        if not names:
            return ResumeSurvey(NO_OPERATIONS, ())
        return ResumeSurvey(OPERATIONS, tuple(_classify_entry(ops_fd, n) for n in names))
    finally:
        for open_fd in (ops_fd, home_fd, control_root_fd):
            if open_fd is not None:
                try:
                    os.close(open_fd)
                except OSError:
                    pass


# --- self-test --------------------------------------------------------------------------------------


def _st_expect_refusal(fn, *args, needle=None, **kwargs):
    """Run fn expecting an InitSubstrateError; assert the message carries `needle` when given."""
    try:
        fn(*args, **kwargs)
    except InitSubstrateError as exc:
        if needle is not None and needle not in str(exc):
            raise AssertionError("refusal message {!r} lacks {!r}".format(str(exc), needle))
        return str(exc)
    raise AssertionError("{} did not refuse".format(getattr(fn, "__name__", fn)))


def _st_sub_ops(root):
    return os.path.join(root, ".git", SUBSTRATE_DIRNAME, OPS_DIRNAME)


def _st_plan_bytes(op_id, mutate=None):
    """Canonical opf.init.plan/v1 bytes for the fixture: the frozen top-key set with minimal
    values (deep value validation is the PR3 producer's, so empty composites are valid here)."""
    doc = dict(
        schema=1, format=PLAN_FORMAT, operation=PLAN_OPERATION, operation_id=op_id,
        versions=dict(spec_version="1.1.0"), binding=dict(), head=dict(), first_adoption=True,
        inventory_digest="sha256:" + "0" * 64, acceptance=dict(),
        application_time="2026-01-01T00:00:00Z", sets=dict(), permitted_directories=[],
        staging_set=[], required_checks=[], publication_boundaries=dict(),
        recovery_policy="resume", plan_digest="sha256:" + "0" * 64)
    if mutate is not None:
        mutate(doc)
    return _opf_init_contract.canonical_json_bytes(doc)


def _st_phase_bytes(op_id, seq, phase, utc="2026-01-01T00:00:00Z"):
    """Canonical opf.init.phase/v1 bytes for a synthetic classifier fixture."""
    return _opf_init_contract.canonical_json_bytes(dict(
        schema=1, format=PHASE_FORMAT, operation_id=op_id, seq=seq, phase=phase, utc=utc))


def _t_s1_sibling_home(d, env):
    """T-s1: the substrate home is the SIBLING <control-root>/opf-init/, never inside the
    opf-oplock control directory, and a linked worktree converges on the MAIN common git dir (the
    composed authoritative resolution); a non-git store roots both control homes at the store
    root."""
    main = _opf_oplock._st_git_store(d, "main", env)
    wt = os.path.join(d, "wt")
    _opf_oplock._st_git(["worktree", "add", "--detach", "-q", wt], main, env)
    cap = _opf_oplock.acquire_operation(wt, "opf-init")
    sub = begin_operation(cap, _st_plan_bytes(cap.op_id))
    plan = os.path.join(_st_sub_ops(main), cap.op_id, PLAN_NAME)
    assert os.path.isfile(plan), "plan not under the MAIN common git dir substrate home"
    ctl = _opf_oplock._st_ctl_dir(main)
    assert sorted(os.listdir(ctl)) == sorted([_opf_oplock.ANCHOR_NAME,
                                              _opf_oplock.ACTIVE_NAME]), \
        "the substrate must not nest inside the opf-oplock control directory"
    name = record_phase(sub, cap, "plan-recorded")
    assert name == "0001-plan-recorded.json", name
    assert os.path.isfile(os.path.join(_st_sub_ops(main), cap.op_id, name))
    close_operation(sub)
    _opf_oplock.release_operation(cap)
    assert os.path.isfile(plan), "release must never remove the substrate tree"
    nogit = os.path.join(d, "nogit")
    os.mkdir(nogit)
    _opf_oplock._st_store_tree(nogit)
    cap = _opf_oplock.acquire_operation(nogit, "opf-init")
    sub = begin_operation(cap, _st_plan_bytes(cap.op_id))
    assert os.path.isfile(os.path.join(nogit, SUBSTRATE_DIRNAME, OPS_DIRNAME, cap.op_id,
                                       PLAN_NAME)), "non-git store roots the home at the store"
    assert not os.path.exists(os.path.join(nogit, _opf_oplock.CONTROL_DIRNAME,
                                           SUBSTRATE_DIRNAME))
    close_operation(sub)
    _opf_oplock.release_operation(cap)


def _t_s2_capability_gate(d, env):
    """T-s2: a substrate write requires the LIVE held capability: a released capability, a
    non-capability, a forked child, and a broken anchor identity each refuse with nothing
    written."""
    root = _opf_oplock._st_git_store(d, "repo", env)
    cap = _opf_oplock.acquire_operation(root, "opf-init")
    _opf_oplock.release_operation(cap)
    _st_expect_refusal(begin_operation, cap, _st_plan_bytes(cap.op_id), needle="released")
    assert not os.path.exists(os.path.join(root, ".git", SUBSTRATE_DIRNAME)), \
        "a refused write must create nothing"
    _st_expect_refusal(begin_operation, None, b"x", needle="OpCapability")
    cap = _opf_oplock.acquire_operation(root, "opf-init")
    sub = begin_operation(cap, _st_plan_bytes(cap.op_id))
    op_dir = os.path.join(_st_sub_ops(root), cap.op_id)
    before = sorted(os.listdir(op_dir))
    sys.stdout.flush()
    sys.stderr.flush()
    pid = os.fork()
    if pid == 0:                          # the child: a different pid, same inherited state
        try:
            try:
                record_phase(sub, cap, "from-child")
            except InitSubstrateError:
                os._exit(0)
            os._exit(1)
        except BaseException:
            os._exit(2)
    _, status = os.waitpid(pid, 0)
    assert os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0, \
        "child substrate write must refuse with InitSubstrateError (status {})".format(status)
    assert sorted(os.listdir(op_dir)) == before, "the refused child must have written nothing"
    record_phase(sub, cap, "from-acquirer")
    close_operation(sub)
    _opf_oplock.release_operation(cap)
    # A broken anchor identity refuses the write (the flock no longer excludes anyone).
    root2 = _opf_oplock._st_git_store(d, "repo2", env)
    cap = _opf_oplock.acquire_operation(root2, "opf-init")
    sub = begin_operation(cap, _st_plan_bytes(cap.op_id))
    os.unlink(os.path.join(_opf_oplock._st_ctl_dir(root2), _opf_oplock.ANCHOR_NAME))
    _st_expect_refusal(record_phase, sub, cap, "next-phase", needle="anchor")
    close_operation(sub)
    _st_expect_refusal(record_phase, sub, cap, "next-phase", needle="closed")
    _st_expect_refusal(close_operation, sub, needle="closed")
    try:                                  # the release surfaces the anchor anomaly; legs still ran
        _opf_oplock.release_operation(cap)
    except _opf_oplock.OpLockError:
        pass


def _t_s3_plan_validation(d, env):
    """T-s3: the plan is validated BEFORE any filesystem write (canonical bytes, the frozen
    closed key set, the identity fields, the held capability's op_id) and persisted CREATE-ONLY
    (a duplicate begin refuses and the plan is preserved byte for byte)."""
    root = _opf_oplock._st_git_store(d, "repo", env)
    cap = _opf_oplock.acquire_operation(root, "opf-init")
    good = _st_plan_bytes(cap.op_id)
    other = _st_plan_bytes("11111111-1111-1111-1111-111111111111")
    _st_expect_refusal(begin_operation, cap, other, needle="op_id")
    _st_expect_refusal(begin_operation, cap, good + b"\n", needle="canonical")
    _st_expect_refusal(begin_operation, cap, b"", needle="empty")
    _st_expect_refusal(begin_operation, cap, "not-bytes", needle="bytes")
    _st_expect_refusal(begin_operation, cap,
                       _st_plan_bytes(cap.op_id, mutate=lambda doc: doc.update(extra=1)),
                       needle="top-level keys")
    _st_expect_refusal(begin_operation, cap,
                       _st_plan_bytes(cap.op_id, mutate=lambda doc: doc.pop("recovery_policy")),
                       needle="top-level keys")
    _st_expect_refusal(begin_operation, cap,
                       _st_plan_bytes(cap.op_id, mutate=lambda doc: doc.update(schema=True)),
                       needle="integer")
    _st_expect_refusal(begin_operation, cap,
                       _st_plan_bytes(cap.op_id, mutate=lambda doc: doc.update(format="x/v1")),
                       needle="format")
    _st_expect_refusal(
        begin_operation, cap,
        _st_plan_bytes(cap.op_id, mutate=lambda doc: doc.update(plan_digest="sha1:00")),
        needle="plan_digest")
    _st_expect_refusal(begin_operation, cap, b'12', needle="JSON object")
    assert not os.path.exists(os.path.join(root, ".git", SUBSTRATE_DIRNAME)), \
        "plan validation must precede any filesystem write"
    sub = begin_operation(cap, good)
    plan = os.path.join(_st_sub_ops(root), cap.op_id, PLAN_NAME)
    with open(plan, "rb") as fh:
        assert fh.read() == good, "plan bytes must equal the validated payload"
    _st_expect_refusal(begin_operation, cap, good, needle="already exists")
    with open(plan, "rb") as fh:
        assert fh.read() == good, "a refused duplicate begin must preserve the plan"
    close_operation(sub)
    _opf_oplock.release_operation(cap)


def _t_s4_phase_discipline(d, env):
    """T-s4: phase records are create-only and strictly contiguous; the tree is re-verified by
    CONTENT DIGEST before each append, so a foreign entry, an in-place rewrite, and a
    different-bytes replacement each refuse, while a byte-identical replacement (harmless,
    whatever inode the filesystem hands the copy) does not; phase names and the record count are
    bounded."""
    root = _opf_oplock._st_git_store(d, "repo", env)
    cap = _opf_oplock.acquire_operation(root, "opf-init")
    sub = begin_operation(cap, _st_plan_bytes(cap.op_id))
    n1 = record_phase(sub, cap, "observe")
    n2 = record_phase(sub, cap, "plan-recorded")
    assert (n1, n2) == ("0001-observe.json", "0002-plan-recorded.json"), (n1, n2)
    op_dir = os.path.join(_st_sub_ops(root), cap.op_id)
    with open(os.path.join(op_dir, n1), "rb") as fh:
        raw = fh.read()
    doc = json.loads(raw.decode("utf-8"))
    assert set(doc) == set(PHASE_TOP_KEYS), doc
    assert doc["seq"] == 1 and doc["phase"] == "observe" and doc["operation_id"] == cap.op_id
    assert _opf_init_contract.canonical_json_bytes(doc) == raw, \
        "a phase record is stored in the exact canonical serialization"
    for bad in ("", "UPPER", "has_underscore", "x" * 65, 123, "a\x00b", "-lead", "trail-"):
        _st_expect_refusal(record_phase, sub, cap, bad, needle="phase name")
    stray = os.path.join(op_dir, "junk")
    with open(stray, "w", encoding="utf-8") as fh:
        fh.write("stray\n")
    _st_expect_refusal(record_phase, sub, cap, "stage", needle="exactly the recorded tree")
    os.unlink(stray)
    record_phase(sub, cap, "stage")
    p1 = os.path.join(op_dir, n1)         # a byte-identical swap is the same record: no refusal
    with open(p1, "rb") as fh:
        same = fh.read()
    os.unlink(p1)
    with open(p1, "wb") as fh:
        fh.write(same)
    os.chmod(p1, 0o644)
    record_phase(sub, cap, "after-swap")
    with open(p1, "ab") as fh:            # an IN-PLACE rewrite (the inode unchanged) refuses
        fh.write(b"\n")
    _st_expect_refusal(record_phase, sub, cap, "finalize", needle="content changed")
    os.unlink(p1)                         # a DIFFERENT-bytes replacement refuses, whatever
    with open(p1, "wb") as fh:            # inode the filesystem hands the new file
        fh.write(_st_phase_bytes(cap.op_id, 1, "observe", utc="2026-01-01T00:00:01Z"))
    os.chmod(p1, 0o644)
    _st_expect_refusal(record_phase, sub, cap, "finalize", needle="content changed")
    sub._next_seq = MAX_PHASES + 1        # the count bound refuses before any further write
    _st_expect_refusal(record_phase, sub, cap, "overflow", needle="bound")
    close_operation(sub)
    # The lock legs are untouched by the substrate swap, so the release itself is clean.
    _opf_oplock.release_operation(cap)


def _t_s5_classifier(d, env):
    """T-s5: the classifier is read-only and plan-aware: NO-SUBSTRATE, then INTACT for a live
    tree, then per-entry CANNOT-EVALUATE for tamper, symlink, FIFO, gap, duplicate, foreign, and
    mismatched records, and it deletes and changes NOTHING."""
    root = _opf_oplock._st_git_store(d, "repo", env)
    assert classify_operations(root).status == NO_SUBSTRATE
    cap = _opf_oplock.acquire_operation(root, "opf-init")
    sub = begin_operation(cap, _st_plan_bytes(cap.op_id))
    record_phase(sub, cap, "observe")
    survey = classify_operations(root)
    assert survey.status == OPERATIONS and len(survey.operations) == 1
    rep = survey.operations[0]
    assert rep.op_id == cap.op_id and rep.status == INTACT, (rep.status, rep.detail)
    assert rep.phases == ((1, "observe"),), rep.phases
    assert rep.plan["operation_id"] == cap.op_id
    close_operation(sub)
    _opf_oplock.release_operation(cap)
    first_id = rep.op_id
    ops = _st_sub_ops(root)
    home = os.path.join(root, ".git", SUBSTRATE_DIRNAME)

    def snapshot():
        out = []
        for base, dirs, files in os.walk(home):
            for n in sorted(dirs) + sorted(files):
                p = os.path.join(base, n)
                st = os.lstat(p)
                out.append((os.path.relpath(p, root), st.st_ino, st.st_size))
        return out

    with open(os.path.join(ops, first_id, PLAN_NAME), "ab") as fh:
        fh.write(b"\n")                   # tampered: no longer the canonical serialization
    uid2 = "22222222-2222-2222-2222-222222222222"
    os.mkdir(os.path.join(ops, uid2))
    os.chmod(os.path.join(ops, uid2), 0o755)
    with open(os.path.join(ops, uid2, PLAN_NAME), "wb") as fh:
        fh.write(_st_plan_bytes(uid2))
    with open(os.path.join(ops, uid2, "0002-late.json"), "wb") as fh:
        fh.write(_st_phase_bytes(uid2, 2, "late"))     # a gap: 0001 is missing
    uid3 = "33333333-3333-3333-3333-333333333333"
    os.mkdir(os.path.join(ops, uid3))
    os.chmod(os.path.join(ops, uid3), 0o755)
    with open(os.path.join(ops, uid3, PLAN_NAME), "wb") as fh:
        fh.write(_st_plan_bytes(uid3))
    with open(os.path.join(ops, uid3, "0001-a.json"), "wb") as fh:
        fh.write(_st_phase_bytes(uid3, 1, "a"))
    with open(os.path.join(ops, uid3, "0001-b.json"), "wb") as fh:
        fh.write(_st_phase_bytes(uid3, 1, "b"))        # duplicate sequence 0001
    uid4 = "44444444-4444-4444-4444-444444444444"
    os.mkdir(os.path.join(ops, uid4))
    os.chmod(os.path.join(ops, uid4), 0o755)
    with open(os.path.join(ops, uid4, PLAN_NAME), "wb") as fh:
        fh.write(_st_plan_bytes(uid4))
    with open(os.path.join(ops, uid4, "notes.txt"), "w", encoding="utf-8") as fh:
        fh.write("foreign\n")                          # a foreign entry
    uid5 = "55555555-5555-5555-5555-555555555555"
    os.mkdir(os.path.join(ops, uid5))
    os.chmod(os.path.join(ops, uid5), 0o755)
    with open(os.path.join(ops, uid5, PLAN_NAME), "wb") as fh:
        fh.write(_st_plan_bytes(uid5))
    with open(os.path.join(ops, uid5, "0001-x.json"), "wb") as fh:
        fh.write(_st_phase_bytes(first_id, 1, "x"))    # operation_id mismatch inside the record
    uid6 = "66666666-6666-6666-6666-666666666666"
    os.symlink(os.path.join(ops, uid2), os.path.join(ops, uid6))
    uid7 = "77777777-7777-7777-7777-777777777777"
    os.mkdir(os.path.join(ops, uid7))
    os.chmod(os.path.join(ops, uid7), 0o755)
    os.mkfifo(os.path.join(ops, uid7, PLAN_NAME))      # a FIFO cannot block or pass
    os.mkdir(os.path.join(ops, "not-a-uuid"))
    os.chmod(os.path.join(ops, "not-a-uuid"), 0o755)
    before = snapshot()
    survey = classify_operations(root)
    assert survey.status == OPERATIONS and len(survey.operations) == 8, len(survey.operations)
    by_id = dict((r.op_id, r) for r in survey.operations)
    assert by_id[first_id].status == CANNOT_EVALUATE and "canonical" in by_id[first_id].detail
    assert by_id[uid2].status == CANNOT_EVALUATE and "contiguous" in by_id[uid2].detail
    assert by_id[uid3].status == CANNOT_EVALUATE and "duplicate" in by_id[uid3].detail
    assert by_id[uid4].status == CANNOT_EVALUATE and "foreign" in by_id[uid4].detail
    assert by_id[uid5].status == CANNOT_EVALUATE and "operation_id" in by_id[uid5].detail
    assert by_id[uid6].status == CANNOT_EVALUATE and "symlink" in by_id[uid6].detail
    assert by_id[uid7].status == CANNOT_EVALUATE and "regular file" in by_id[uid7].detail
    assert by_id["not-a-uuid"].status == CANNOT_EVALUATE
    assert "operation id" in by_id["not-a-uuid"].detail
    assert snapshot() == before, "the classifier must delete and change NOTHING"


def _t_s6_crash_resume(d, env):
    """T-s6: the COMPOSITION witness. A crashed holder (a forked child that acquires, persists a
    plan, records a phase, and exits without release) leaves the lock's control legs AND the
    substrate tree behind; the classifier reads the crashed tree; the lock module's explicit
    recovery (recover=True over a confirmed-dead holder, reusing _recover_stale verbatim) clears
    ONLY the control legs and the substrate tree SURVIVES byte for byte, so the plan-aware resume
    evidence is never blind-unlinked."""
    root = _opf_oplock._st_git_store(d, "repo", env)
    rfd, wfd = os.pipe()
    sys.stdout.flush()
    sys.stderr.flush()
    pid = os.fork()
    if pid == 0:                          # the child: acquire, persist, record, then CRASH
        os.close(rfd)
        try:
            cap = _opf_oplock.acquire_operation(root, "opf-init")
            sub = begin_operation(cap, _st_plan_bytes(cap.op_id))
            record_phase(sub, cap, "observe")
            os.write(wfd, cap.op_id.encode("utf-8"))
            os._exit(0)                   # no release: the crash leaves legs and substrate
        except BaseException:
            os._exit(3)
    os.close(wfd)
    data = b""
    while True:
        chunk = os.read(rfd, 4096)
        if not chunk:
            break
        data += chunk
    os.close(rfd)
    _, status = os.waitpid(pid, 0)
    assert os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0, status
    op_id = data.decode("utf-8")
    assert _OP_ID_RE.match(op_id), op_id
    active = os.path.join(_opf_oplock._st_ctl_dir(root), _opf_oplock.ACTIVE_NAME)
    lease = _opf_oplock._st_lease_path(root)
    assert os.path.exists(active) and os.path.exists(lease), \
        "the crash must leave the control legs behind"
    plan = os.path.join(_st_sub_ops(root), op_id, PLAN_NAME)
    phase1 = os.path.join(_st_sub_ops(root), op_id, "0001-observe.json")
    with open(plan, "rb") as fh:
        plan_before = fh.read()
    survey = classify_operations(root)
    assert survey.status == OPERATIONS and len(survey.operations) == 1
    assert survey.operations[0].status == INTACT
    assert survey.operations[0].phases == ((1, "observe"),)
    try:                                  # stale control legs refuse without explicit recovery
        _opf_oplock.acquire_operation(root, "opf-init")
    except _opf_oplock.OpLockError as exc:
        assert "stale" in str(exc), str(exc)
    else:
        raise AssertionError("stale control legs must refuse without recover=True")
    cap = _opf_oplock.acquire_operation(root, "opf-init", recover=True)
    with open(active, "rb") as fh:        # the control legs now belong to the NEW holder
        assert fh.read() == cap._active_bytes
    with open(plan, "rb") as fh:
        assert fh.read() == plan_before, \
            "recovery must PRESERVE the substrate tree (plan-aware resume evidence)"
    assert os.path.isfile(phase1), "recovery must not touch a phase record"
    sub = begin_operation(cap, _st_plan_bytes(cap.op_id))
    survey = classify_operations(root)
    assert survey.status == OPERATIONS and len(survey.operations) == 2
    assert all(rep.status == INTACT for rep in survey.operations)
    close_operation(sub)
    _opf_oplock.release_operation(cap)


def _t_s7_torn_plan(d, env):
    """T-s7: a torn plan write strands NOTHING: the partial file is unlinked (the composed
    control-file creator's cleanup) and the just-created operation directory is rolled back,
    leaving the operation re-beginnable."""
    root = _opf_oplock._st_git_store(d, "repo", env)
    cap = _opf_oplock.acquire_operation(root, "opf-init")
    saved_write_all = _journal._write_all

    def _boom(fd, payload):
        os.write(fd, payload[:5])         # a torn partial write, then fail
        raise OSError("simulated substrate-write failure (T-s7)")

    _journal._write_all = _boom
    try:
        _st_expect_refusal(begin_operation, cap, _st_plan_bytes(cap.op_id),
                           needle="cannot write")
    finally:
        _journal._write_all = saved_write_all
    ops = _st_sub_ops(root)
    assert os.listdir(ops) == [], "a torn plan write must roll back the operation directory"
    sub = begin_operation(cap, _st_plan_bytes(cap.op_id))
    close_operation(sub)
    _opf_oplock.release_operation(cap)


def _t_s8_fail_closed_roots(d, env):
    """T-s8: an unresolved store, a symlinked .git, and an exotic substrate home each refuse the
    classifier outright (fail-closed, never a silent clean survey); a home with nothing recorded
    is NO-OPERATIONS."""
    plain = os.path.join(d, "plain")
    os.mkdir(plain)
    _st_expect_refusal(classify_operations, plain, needle="RESOLVED")
    real = _opf_oplock._st_git_store(d, "real", env)
    sym = os.path.join(d, "symgit")
    os.mkdir(sym)
    _opf_oplock._st_store_tree(sym)
    os.symlink(os.path.join(real, ".git"), os.path.join(sym, ".git"))
    _st_expect_refusal(classify_operations, sym, needle="symlink")
    r2 = _opf_oplock._st_git_store(d, "r2", env)
    elsewhere = os.path.join(d, "elsewhere")
    os.mkdir(elsewhere)
    os.symlink(elsewhere, os.path.join(r2, ".git", SUBSTRATE_DIRNAME))
    _st_expect_refusal(classify_operations, r2, needle="not a plain directory")
    r3 = _opf_oplock._st_git_store(d, "r3", env)
    os.mkdir(os.path.join(r3, ".git", SUBSTRATE_DIRNAME))
    os.chmod(os.path.join(r3, ".git", SUBSTRATE_DIRNAME), 0o755)
    assert classify_operations(r3).status == NO_OPERATIONS
    os.mkdir(os.path.join(r3, ".git", SUBSTRATE_DIRNAME, OPS_DIRNAME))
    os.chmod(os.path.join(r3, ".git", SUBSTRATE_DIRNAME, OPS_DIRNAME), 0o755)
    assert classify_operations(r3).status == NO_OPERATIONS


def _t_s9_classifier_bounds(d, env):
    """T-s9: the READ-side classifier enforces the substrate's own bounds and timestamp grammar
    rather than trusting the writer: a phase sequence past MAX_PHASES (a count past the bound, or
    a single out-of-bound sequence number) is CANNOT-EVALUATE; a phase record whose utc is not a
    well-formed oplock timestamp (wrong shape, an out-of-range field, or an impossible calendar
    date) is CANNOT-EVALUATE; and a tree at exactly the bound with valid timestamps stays
    INTACT."""
    root = _opf_oplock._st_git_store(d, "repo", env)
    home = os.path.join(root, ".git", SUBSTRATE_DIRNAME)
    ops = _st_sub_ops(root)
    for p in (home, ops):
        os.mkdir(p)
        os.chmod(p, 0o755)

    def synth(uid, records):
        op_dir = os.path.join(ops, uid)
        os.mkdir(op_dir)
        os.chmod(op_dir, 0o755)
        with open(os.path.join(op_dir, PLAN_NAME), "wb") as fh:
            fh.write(_st_plan_bytes(uid))
        for rname, payload in records:
            with open(os.path.join(op_dir, rname), "wb") as fh:
                fh.write(payload)

    over = "11111111-1111-1111-1111-111111111111"
    synth(over, [("{:04d}-p.json".format(i), _st_phase_bytes(over, i, "p"))
                 for i in range(1, MAX_PHASES + 2)])
    lone = "22222222-2222-2222-2222-222222222222"
    synth(lone, [("9999-p.json", _st_phase_bytes(lone, 9999, "p"))])
    badutc = "33333333-3333-3333-3333-333333333333"
    synth(badutc, [("0001-p.json", _st_phase_bytes(badutc, 1, "p", utc="not-a-time"))])
    ranges = "44444444-4444-4444-4444-444444444444"
    synth(ranges, [("0001-p.json",
                    _st_phase_bytes(ranges, 1, "p", utc="2026-13-01T00:00:00Z"))])
    feb30 = "55555555-5555-5555-5555-555555555555"
    synth(feb30, [("0001-p.json",
                   _st_phase_bytes(feb30, 1, "p", utc="2026-02-30T00:00:00Z"))])
    full = "66666666-6666-6666-6666-666666666666"
    synth(full, [("{:04d}-p.json".format(i), _st_phase_bytes(full, i, "p"))
                 for i in range(1, MAX_PHASES + 1)])
    survey = classify_operations(root)
    assert survey.status == OPERATIONS and len(survey.operations) == 6, survey.status
    by_id = dict((r.op_id, r) for r in survey.operations)
    assert by_id[over].status == CANNOT_EVALUATE and "bound" in by_id[over].detail, \
        (by_id[over].status, by_id[over].detail)
    assert by_id[lone].status == CANNOT_EVALUATE and "bound" in by_id[lone].detail, \
        (by_id[lone].status, by_id[lone].detail)
    for uid in (badutc, ranges, feb30):
        assert by_id[uid].status == CANNOT_EVALUATE and "utc" in by_id[uid].detail, \
            (by_id[uid].status, by_id[uid].detail)
    assert by_id[full].status == INTACT, (by_id[full].status, by_id[full].detail)
    assert len(by_id[full].phases) == MAX_PHASES


def _t_s10_fresh_listing(d, env):
    """T-s10: every substrate listing runs through a FRESH per-listing descriptor and never
    rewinds a retained one with lseek: with os.lseek shimmed to refuse a directory descriptor
    exactly as macOS does (EINVAL), the whole write-verify-classify cycle still works, and the
    listing still observes an entry created after the retained descriptor was opened (no
    readdir-bound staleness)."""
    import errno
    root = _opf_oplock._st_git_store(d, "repo", env)
    saved_lseek = os.lseek

    def _darwin_lseek(fd, pos, how):
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError(errno.EINVAL, "lseek on a directory fd (macOS behaviour)")
        return saved_lseek(fd, pos, how)

    os.lseek = _darwin_lseek
    try:
        cap = _opf_oplock.acquire_operation(root, "opf-init")
        sub = begin_operation(cap, _st_plan_bytes(cap.op_id))
        record_phase(sub, cap, "observe")
        record_phase(sub, cap, "plan-recorded")
        op_dir = os.path.join(_st_sub_ops(root), cap.op_id)
        stray = os.path.join(op_dir, "junk")
        with open(stray, "w", encoding="utf-8") as fh:
            fh.write("stray\n")           # created AFTER sub._op_fd was opened: must be seen
        _st_expect_refusal(record_phase, sub, cap, "stage",
                           needle="exactly the recorded tree")
        os.unlink(stray)
        record_phase(sub, cap, "stage")
        survey = classify_operations(root)
        assert survey.status == OPERATIONS and len(survey.operations) == 1
        rep = survey.operations[0]
        assert rep.status == INTACT, (rep.status, rep.detail)
        assert rep.phases == ((1, "observe"), (2, "plan-recorded"), (3, "stage")), rep.phases
        close_operation(sub)
        _opf_oplock.release_operation(cap)
    finally:
        os.lseek = saved_lseek


def _t_s11_plan_membership(d, env):
    """T-s11: the classifier checks plan.json is a MEMBER of the SAME fresh listing the phase
    scan uses: a plan removed between the plan read and the listing is CANNOT-EVALUATE, never
    INTACT on the earlier stale bytes."""
    global _list_dir_fresh
    root = _opf_oplock._st_git_store(d, "repo", env)
    cap = _opf_oplock.acquire_operation(root, "opf-init")
    sub = begin_operation(cap, _st_plan_bytes(cap.op_id))
    record_phase(sub, cap, "observe")
    close_operation(sub)
    _opf_oplock.release_operation(cap)
    plan = os.path.join(_st_sub_ops(root), cap.op_id, PLAN_NAME)
    saved_list = _list_dir_fresh

    def _racing(parent_fd, name, ident, label):
        if name == cap.op_id and os.path.exists(plan):
            os.unlink(plan)               # raced away AFTER the plan read, BEFORE the listing
        return saved_list(parent_fd, name, ident, label)

    _list_dir_fresh = _racing
    try:
        survey = classify_operations(root)
    finally:
        _list_dir_fresh = saved_list
    assert survey.status == OPERATIONS and len(survey.operations) == 1
    rep = survey.operations[0]
    assert rep.status == CANNOT_EVALUATE and "absent from the fresh listing" in rep.detail, \
        (rep.status, rep.detail)


def _t_s12_early_count_guard(d, env):
    """T-s12: the EARLY whole-directory entry-count bound refuses an over-bound operation
    directory BEFORE any phase record is opened, on a shape the per-sequence bound alone never
    catches (every sequence within bound, the sole excess entry sorting last): the pre-read
    resource bound is the early guard's own coverage."""
    global _read_record_bytes
    root = _opf_oplock._st_git_store(d, "repo", env)
    home = os.path.join(root, ".git", SUBSTRATE_DIRNAME)
    ops = _st_sub_ops(root)
    for p in (home, ops):
        os.mkdir(p)
        os.chmod(p, 0o755)
    uid = "11111111-1111-1111-1111-111111111111"
    op_dir = os.path.join(ops, uid)
    os.mkdir(op_dir)
    os.chmod(op_dir, 0o755)
    with open(os.path.join(op_dir, PLAN_NAME), "wb") as fh:
        fh.write(_st_plan_bytes(uid))
    for i in range(1, MAX_PHASES + 1):    # every sequence IN bound and unique
        with open(os.path.join(op_dir, "{:04d}-p.json".format(i)), "wb") as fh:
            fh.write(_st_phase_bytes(uid, i, "p"))
    with open(os.path.join(op_dir, "zzzz-foreign"), "w", encoding="utf-8") as fh:
        fh.write("late\n")                # the count breaker, sorting AFTER every record
    saved_read = _read_record_bytes
    opened = []

    def _counting(dir_fd, name, label, max_bytes):
        if name != PLAN_NAME:
            opened.append(name)
        return saved_read(dir_fd, name, label, max_bytes)

    _read_record_bytes = _counting
    try:
        survey = classify_operations(root)
    finally:
        _read_record_bytes = saved_read
    assert survey.status == OPERATIONS and len(survey.operations) == 1
    rep = survey.operations[0]
    assert rep.status == CANNOT_EVALUATE and "entries exceed" in rep.detail, \
        (rep.status, rep.detail)
    assert opened == [], "the early bound must refuse before any phase record is opened"


def _t_s13_midread_containment(d, env):
    """T-s13: a mid-read OSError (an EIO AFTER a successful open) is contained as the declared
    InitSubstrateError: the classifier CANNOT-EVALUATEs the ONE affected entry while the survey
    continues, and the write path's pre-append re-verification refuses with the declared type,
    never a raw OSError."""
    import errno
    root = _opf_oplock._st_git_store(d, "repo", env)
    cap = _opf_oplock.acquire_operation(root, "opf-init")
    sub = begin_operation(cap, _st_plan_bytes(cap.op_id))
    record_phase(sub, cap, "observe")
    close_operation(sub)
    _opf_oplock.release_operation(cap)
    victim_id = cap.op_id
    cap = _opf_oplock.acquire_operation(root, "opf-init")
    sub = begin_operation(cap, _st_plan_bytes(cap.op_id))
    record_phase(sub, cap, "observe")
    saved_read = os.read

    def _eio_on(path):
        vst = os.lstat(path)

        def _reader(fd, n):
            st = os.fstat(fd)
            if (st.st_dev, st.st_ino) == (vst.st_dev, vst.st_ino):
                raise OSError(errno.EIO, "simulated mid-read I/O error (T-s13)")
            return saved_read(fd, n)
        return _reader

    os.read = _eio_on(os.path.join(_st_sub_ops(root), victim_id, "0001-observe.json"))
    try:
        survey = classify_operations(root)
    finally:
        os.read = saved_read
    assert survey.status == OPERATIONS and len(survey.operations) == 2
    by_id = dict((r.op_id, r) for r in survey.operations)
    assert by_id[victim_id].status == CANNOT_EVALUATE \
        and "cannot read" in by_id[victim_id].detail, \
        (by_id[victim_id].status, by_id[victim_id].detail)
    assert by_id[cap.op_id].status == INTACT, \
        (by_id[cap.op_id].status, by_id[cap.op_id].detail)
    os.read = _eio_on(os.path.join(_st_sub_ops(root), cap.op_id, "0001-observe.json"))
    try:                                  # the write path refuses with the DECLARED type
        _st_expect_refusal(record_phase, sub, cap, "stage", needle="cannot read")
    finally:
        os.read = saved_read
    record_phase(sub, cap, "stage")
    close_operation(sub)
    _opf_oplock.release_operation(cap)


_ST_INIT_OP = "abcdef01-2345-4678-9abc-def012345678"


def _st_init_home(root):
    return os.path.join(root, ".git", SUBSTRATE_DIRNAME)


def _st_child(fn):
    """Run fn() in a forked child; return its exit status (fn's return code, 99 on an exception)."""
    sys.stdout.flush()
    sys.stderr.flush()
    pid = os.fork()
    if pid == 0:
        try:
            os._exit(fn() or 0)
        except BaseException:
            import traceback
            traceback.print_exc()
            os._exit(99)
    _, status = os.waitpid(pid, 0)
    return status


def _t_s14_holder_writes_and_binding(d, env):
    """T-s14 (decisions 1 and 2): an init pre-store holder writes the plan and phases BEFORE any
    store exists (no RESOLVED store; the init control root is the explicit product root's common git
    dir); the handle is bound to that holder's token, so the capability attach_init_lease mints from
    it continues the same sequence, while a different holder, or an ordinary capability naming the
    same op id, is refused."""
    root = _opf_oplock._st_unadopted_repo(d, "repo", env)
    other = _opf_oplock._st_unadopted_repo(d, "other", env)
    holder = _opf_oplock.acquire_init_operation(root, "opf-init")
    sub = begin_operation(holder, _st_plan_bytes(_ST_INIT_OP))
    assert os.path.isfile(os.path.join(_st_init_home(root), OPS_DIRNAME, _ST_INIT_OP, PLAN_NAME))
    assert record_phase(sub, holder, "plan-recorded") == "0001-plan-recorded.json"
    foreign = _opf_oplock.acquire_init_operation(other, "opf-init")
    _st_expect_refusal(record_phase, sub, foreign, "x", needle="different init holder")
    _opf_oplock.release_init_holder(foreign)
    _opf_oplock._st_make_machine_dir(root)
    cap = _opf_oplock.attach_init_lease(holder, _ST_INIT_OP)
    assert record_phase(sub, cap, "dirs-verified") == "0002-dirs-verified.json"
    _st_expect_refusal(record_phase, sub, holder, "x", needle="spent")
    back = _opf_oplock.detach_init_lease(cap)
    assert record_phase(sub, back, "after-detach") == "0003-after-detach.json"
    close_operation(sub)
    _opf_oplock.release_init_holder(back)
    store = _opf_oplock._st_git_store(d, "store", env)
    plain = _opf_oplock.acquire_operation(store, "op")
    psub = begin_operation(plain, _st_plan_bytes(plain.op_id))
    close_operation(psub)
    _opf_oplock.release_operation(plain)
    survey = classify_init_operations(root)
    assert survey.status == OPERATIONS and survey.operations[0].status == INTACT
    assert survey.operations[0].phases == ((1, "plan-recorded"), (2, "dirs-verified"),
                                           (3, "after-detach"))


def _t_s15_resume_operation(d, env):
    """T-s15 (decision 2): a crashed operation is RESUMED under a fresh holder through
    resume_operation: the original op id, the plan bytes untouched, the phase roster re-bound, and
    the next append continues the sequence; a wrong plan digest, an unknown op id, or a gapped tree
    refuses and the tree is preserved."""
    root = _opf_oplock._st_unadopted_repo(d, "repo", env)
    plan = _st_plan_bytes(_ST_INIT_OP)

    def crash():
        h = _opf_oplock.acquire_init_operation(root, "opf-init")
        s = begin_operation(h, plan)
        record_phase(s, h, "plan-recorded")
        record_phase(s, h, "dirs-intent")
        return 0                          # exit without release or close: a crash
    status = _st_child(crash)
    assert os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0, status
    op_dir = os.path.join(_st_init_home(root), OPS_DIRNAME, _ST_INIT_OP)
    holder = _opf_oplock.acquire_init_operation(root, "opf-init")
    _st_expect_refusal(resume_operation, holder, _ST_INIT_OP, "sha256:" + "1" * 64,
                       needle="changed evidence")
    _st_expect_refusal(resume_operation, holder, "11111111-1111-1111-1111-111111111111",
                       "sha256:" + "0" * 64, needle="no recorded operation")
    sub = resume_operation(holder, _ST_INIT_OP, "sha256:" + "0" * 64)
    assert recorded_phases(sub) == ((1, "plan-recorded"), (2, "dirs-intent"))
    assert record_phase(sub, holder, "dirs-verified") == "0003-dirs-verified.json"
    with open(os.path.join(op_dir, PLAN_NAME), "rb") as fh:
        assert fh.read() == plan, "resume never rewrites the plan"
    close_operation(sub)
    os.unlink(os.path.join(op_dir, "0002-dirs-intent.json"))
    _st_expect_refusal(resume_operation, holder, _ST_INIT_OP, "sha256:" + "0" * 64,
                       needle="contiguous")
    assert os.path.isfile(os.path.join(op_dir, "0003-dirs-verified.json")), "preserved"
    _opf_oplock.release_init_holder(holder)


def _t_s16_staging_sweep(d, env):
    """T-s16 (decision 3): resume sweeps EXACTLY the lock module's staging leftovers of a plan or
    phase record (a lone staging file, and one killed after its link, sharing the record's inode),
    restoring the record's single link, and refuses a staging name bound to a non-regular entry and
    any other foreign entry, preserving them."""
    root = _opf_oplock._st_unadopted_repo(d, "repo", env)
    holder = _opf_oplock.acquire_init_operation(root, "opf-init")
    sub = begin_operation(holder, _st_plan_bytes(_ST_INIT_OP))
    record_phase(sub, holder, "plan-recorded")
    close_operation(sub)
    op_dir = os.path.join(_st_init_home(root), OPS_DIRNAME, _ST_INIT_OP)
    lone = "." + "0002-dirs-intent.json" + _opf_oplock._STAGING_MARKER + "a" * 32
    with open(os.path.join(op_dir, lone), "wb") as fh:
        fh.write(b"{")
    linked = "." + "0001-plan-recorded.json" + _opf_oplock._STAGING_MARKER + "b" * 32
    os.link(os.path.join(op_dir, "0001-plan-recorded.json"), os.path.join(op_dir, linked))
    assert classify_init_operations(root).operations[0].status == CANNOT_EVALUATE
    sub = resume_operation(holder, _ST_INIT_OP, "sha256:" + "0" * 64)
    assert sorted(sub.swept) == sorted([lone, linked]), sub.swept
    assert sorted(os.listdir(op_dir)) == ["0001-plan-recorded.json", PLAN_NAME]
    assert os.stat(os.path.join(op_dir, "0001-plan-recorded.json")).st_nlink == 1
    close_operation(sub)
    bad = "." + PLAN_NAME + _opf_oplock._STAGING_MARKER + "c" * 32
    os.mkdir(os.path.join(op_dir, bad))
    _st_expect_refusal(resume_operation, holder, _ST_INIT_OP, "sha256:" + "0" * 64,
                       needle="not a regular file")
    os.rmdir(os.path.join(op_dir, bad))
    with open(os.path.join(op_dir, ".plan.json.opf-stage-short"), "wb") as fh:
        fh.write(b"x")
    _st_expect_refusal(resume_operation, holder, _ST_INIT_OP, "sha256:" + "0" * 64,
                       needle="foreign entry")
    assert os.path.exists(os.path.join(op_dir, ".plan.json.opf-stage-short")), "preserved"
    _opf_oplock.release_init_holder(holder)


def _t_s17_kill_during_plan_publication(d, env):
    """T-s17 (decision 3): a REAL SIGKILL during the plan's atomic publication, (a) before its link
    (a staging leftover only) and (b) after its link but before the staging unlink (the plan with
    link count 2 beside its staging name), is handled on retry: (a) the operation directory is
    swept EMPTY and discarded explicitly (the plan never authorized anything); (b) the classifier
    reads the doubly-linked plan as CANNOT-EVALUATE and resume sweeps it back to one link and
    resumes the SAME operation. A non-empty directory is never discarded."""
    import signal as _signal
    for when in ("before-link", "after-link"):
        root = _opf_oplock._st_unadopted_repo(d, "repo-" + when, env)

        def crash():
            h = _opf_oplock.acquire_init_operation(root, "opf-init")
            real_link = os.link

            def killing_link(*args, **kwargs):
                if when == "after-link":
                    real_link(*args, **kwargs)
                os.kill(os.getpid(), _signal.SIGKILL)
            os.link = killing_link
            begin_operation(h, _st_plan_bytes(_ST_INIT_OP))
            return 5                      # unreachable: the kill lands inside the publication
        status = _st_child(crash)
        assert os.WIFSIGNALED(status) and os.WTERMSIG(status) == _signal.SIGKILL, status
        op_dir = os.path.join(_st_init_home(root), OPS_DIRNAME, _ST_INIT_OP)
        entries = sorted(os.listdir(op_dir))
        holder = _opf_oplock.acquire_init_operation(root, "opf-init")
        if when == "before-link":
            assert len(entries) == 1 and entries[0].startswith(".plan.json.opf-stage-"), entries
            swept = discard_empty_operation(holder, _ST_INIT_OP)
            assert len(swept) == 1 and not os.path.exists(op_dir), swept
            _st_expect_refusal(resume_operation, holder, _ST_INIT_OP, "sha256:" + "0" * 64,
                               needle="no recorded operation")
        else:
            assert PLAN_NAME in entries and len(entries) == 2, entries
            assert classify_init_operations(root).operations[0].status == CANNOT_EVALUATE
            _st_expect_refusal(discard_empty_operation, holder, _ST_INIT_OP,
                               needle="preserved evidence")
            assert settle_operation(holder, _ST_INIT_OP) == ((), False), "never discarded"
            sub = resume_operation(holder, _ST_INIT_OP, "sha256:" + "0" * 64)
            assert sorted(os.listdir(op_dir)) == [PLAN_NAME]
            record_phase(sub, holder, "plan-recorded")
            close_operation(sub)
            assert classify_init_operations(root).operations[0].status == INTACT
        _opf_oplock.release_init_holder(holder)


def _t_s18_outcomes_home(d, env):
    """T-s18 (decision 4): attempt outcomes live in the SIBLING outcomes/<op_id>/ home (the
    ops/<op_id>/ shape is unchanged), create-only, contiguous, canonical, and bound to the
    operation; a foreign entry or a malformed payload refuses; the holder detach_init_lease hands
    back may still record the outcome; read_outcomes returns them in order."""
    root = _opf_oplock._st_unadopted_repo(d, "repo", env)
    holder = _opf_oplock.acquire_init_operation(root, "opf-init")
    sub = begin_operation(holder, _st_plan_bytes(_ST_INIT_OP))

    def outcome(n):
        return _opf_init_contract.canonical_json_bytes(dict(
            schema=1, format=OUTCOME_FORMAT, operation_id=_ST_INIT_OP, attempt=n))
    assert record_outcome(sub, holder, outcome(1)) == "0001-attempt.json"
    _st_expect_refusal(record_outcome, sub, holder, outcome(2) + b"\n", needle="canonical")
    _st_expect_refusal(record_outcome, sub, holder, _opf_init_contract.canonical_json_bytes(
        dict(schema=1, format=OUTCOME_FORMAT, operation_id="x")), needle="operation id")
    _opf_oplock._st_make_machine_dir(root)
    cap = _opf_oplock.attach_init_lease(holder, _ST_INIT_OP)
    assert record_outcome(sub, cap, outcome(2)) == "0002-attempt.json"
    back = _opf_oplock.detach_init_lease(cap)
    assert record_outcome(sub, back, outcome(3)) == "0003-attempt.json"
    home = os.path.join(_st_init_home(root), OUTCOMES_DIRNAME, _ST_INIT_OP)
    assert sorted(os.listdir(os.path.join(_st_init_home(root), OPS_DIRNAME, _ST_INIT_OP))) \
        == [PLAN_NAME], "the ops/<op_id>/ record shape is unchanged"
    got = read_outcomes(root, _ST_INIT_OP)
    assert [o["attempt"] for o in got] == [1, 2, 3]
    with open(os.path.join(home, "notes"), "w", encoding="utf-8") as fh:
        fh.write("x\n")
    _st_expect_refusal(record_outcome, sub, back, outcome(4), needle="foreign entry")
    _st_expect_refusal(read_outcomes, root, _ST_INIT_OP, needle="foreign")
    close_operation(sub)
    _opf_oplock.release_init_holder(back)


def self_test():
    """Regression roster (the PR2 resume-substrate T-s witnesses), each a fail-to-pass
    discriminator against a named behaviour: the sibling-home placement under the composed
    authoritative control root (T-s1), the live-capability write gate (T-s2), the
    validate-before-write create-only plan (T-s3), the contiguous content-verified phase
    discipline (T-s4), the read-only plan-aware classifier over the defect matrix (T-s5), the
    crash-then-control-leg-recovery composition with the substrate preserved (T-s6), the torn
    plan write stranding nothing (T-s7), the fail-closed classifier roots (T-s8), the read-side
    classifier bounds and utc validity (T-s9), the lseek-free fresh-descriptor listings (T-s10),
    the plan-membership re-check in the same fresh listing (T-s11), the early entry-count bound
    refusing before any phase record read (T-s12), and mid-read OSError containment (T-s13). A missing
    containment primitive or git binary is a REFUSAL (non-zero), never a clean skip. The git
    fixtures are pinned hermetically exactly as the lock module's self-test pins them."""
    import tempfile
    import traceback

    if not _containment.probe():
        print("REFUSED: race-free containment primitive absent; the resume substrate cannot be "
              "exercised safely (fail-closed, non-zero)")
        return 2
    if shutil.which("git") is None:
        print("REFUSED: git binary not found; the self-test requires real git stores "
              "(fail-closed, non-zero)")
        return 2

    tests = (
        ("T-s1 sibling substrate home under the authoritative control root",
         _t_s1_sibling_home),
        ("T-s2 substrate writes require the live acquirer capability", _t_s2_capability_gate),
        ("T-s3 plan validated before write, persisted create-only", _t_s3_plan_validation),
        ("T-s4 contiguous, content-verified, bounded phase records", _t_s4_phase_discipline),
        ("T-s5 read-only plan-aware classifier over the defect matrix", _t_s5_classifier),
        ("T-s6 crash, classify, recover control legs, substrate preserved",
         _t_s6_crash_resume),
        ("T-s7 a torn plan write strands nothing", _t_s7_torn_plan),
        ("T-s8 classifier roots fail closed", _t_s8_fail_closed_roots),
        ("T-s9 read-side classifier bounds and utc validity", _t_s9_classifier_bounds),
        ("T-s10 fresh-descriptor listings, no lseek on a directory fd", _t_s10_fresh_listing),
        ("T-s11 plan membership re-checked in the same fresh listing", _t_s11_plan_membership),
        ("T-s12 early entry-count bound refuses before any phase record read",
         _t_s12_early_count_guard),
        ("T-s13 a mid-read OSError contains to one CANNOT-EVALUATE entry",
         _t_s13_midread_containment),
        ("T-s14 an init holder writes before the store exists; handles bind to the holder",
         _t_s14_holder_writes_and_binding),
        ("T-s15 a crashed operation resumes under its original id through resume_operation",
         _t_s15_resume_operation),
        ("T-s16 resume sweeps exact staging leftovers and refuses anything else",
         _t_s16_staging_sweep),
        ("T-s17 a SIGKILL during plan publication is discarded or resumed, never replaced",
         _t_s17_kill_during_plan_publication),
        ("T-s18 attempt outcomes live in the sibling outcomes home, bound and contiguous",
         _t_s18_outcomes_home),
    )

    base = os.path.realpath(tempfile.mkdtemp(prefix="opf-init-substrate-selftest-"))
    # Pin the git fixtures AND the composed rev-parse hermetically, exactly as the lock module's
    # self-test does: bind HOME and XDG_CONFIG_HOME (which the production _git_common_dir keeps,
    # scrubbing only GIT_*) plus GIT_CONFIG_GLOBAL/SYSTEM into the per-run temp dir, and restore
    # them afterwards, so no ambient user or system git config can affect a fixture command or
    # the module's own control-root resolution.
    _saved_env = {}
    for _k in ("HOME", "XDG_CONFIG_HOME", "GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM",
               "GIT_CONFIG_NOSYSTEM"):
        _saved_env[_k] = os.environ.get(_k)
    os.environ["HOME"] = base
    os.environ["XDG_CONFIG_HOME"] = os.path.join(base, "xdg")
    os.environ["GIT_CONFIG_GLOBAL"] = os.path.join(base, "gitconfig-global")
    os.environ["GIT_CONFIG_SYSTEM"] = os.path.join(base, "gitconfig-system")
    os.environ["GIT_CONFIG_NOSYSTEM"] = "1"
    os.makedirs(os.path.join(base, "xdg"), exist_ok=True)
    failures = []
    try:
        env = _opf_oplock._st_git_env(base)
        for index, (name, fn) in enumerate(tests):
            d = os.path.join(base, "t{:02d}".format(index))
            os.mkdir(d)
            try:
                fn(d, env)
                print("PASS  {}".format(name))
            except BaseException:
                failures.append(name)
                print("FAIL  {}".format(name))
                traceback.print_exc()
    finally:
        for _k, _v in _saved_env.items():
            if _v is None:
                os.environ.pop(_k, None)
            else:
                os.environ[_k] = _v
        shutil.rmtree(base, ignore_errors=True)
    if failures:
        print("SELF-TEST FAIL: {} of {} tests failed: {}".format(
            len(failures), len(tests), "; ".join(failures)))
        return 1
    print("SELF-TEST PASS")
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--self-test":
        sys.exit(self_test())
    sys.exit("usage: _opf_init_substrate.py --self-test")
