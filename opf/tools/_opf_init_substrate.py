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
exclusive fsynced control-file creation (_create_control_file, whose torn-write cleanup this
module inherits) are the lock module's own.

Per held operation (an _opf_oplock.OpCapability) the substrate records, under ops/<op_id>/:

  1. plan.json, the frozen opf.init.plan/v1 document (OPF-INIT-D2B.md), persisted CREATE-ONLY
     (O_CREAT|O_EXCL) in the exact canonical JSON serialization the D2b contract defines, with
     the closed top-level key set, the scalar identity fields, and an operation_id equal to the
     held capability's op_id all validated BEFORE any filesystem write. Deep validation of the
     plan's composite values belongs to the plan's PRODUCER (PR3 and later), not to this
     persistence layer.
  2. Create-only PHASE RECORDS, NNNN-<phase>.json, a strictly contiguous append-only sequence
     (0001 upward) of opf.init.phase/v1 records; a record is never modified or removed, and
     before each append the on-disk tree is re-verified against the handle's retained identities
     (device and inode), so a foreign entry, a gap, or a swapped record refuses the append rather
     than being built upon.

Every substrate WRITE requires the LIVE held capability: the caller must be the recorded acquirer
(pid plus /proc start time), the capability must be unreleased, the retained anchor identity must
still hold, and the freshly resolved control root must carry the opf-oplock directory with the
capability's retained identity, so a write can never land under a different control tree than the
one the held flock excludes for.

The RESUME CLASSIFIER (classify_operations) is READ-ONLY and PLAN-AWARE: it enumerates ops/ and
classifies each operation directory against its own plan.json into INTACT (a valid canonical plan
plus a contiguous, well-formed phase sequence; the resume input a later dispatch consumes) or
CANNOT-EVALUATE (anything unreadable, malformed, non-canonical, gapped, duplicated, foreign, or
symlinked: fail-closed, named, and PRESERVED). It deletes NOTHING, ever. A crashed operation's
CONTROL legs (the lock's active.toml and lease) are cleared only by the lock module's own
explicit recovery (acquire_operation(recover=True), whose confirmed-dead gate and _recover_stale
are reused verbatim, never duplicated here); the substrate tree SURVIVES that recovery, so the
classifier keeps the evidence a resume decision needs. The journal's recover() and
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

Run: python3 -I -B opf/tools/_opf_init_substrate.py --self-test
Exit: 0 self-test clean; 1 self-test failure; 2 refused precondition (missing containment
primitive or git binary), never a clean skip.
"""
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

# An operation id is the capability's uuid4 string; a phase name is filename-safe by construction
# (lowercase alphanumerics with interior hyphens, bounded), so a record name never needs escaping
# and the control-character class is excluded structurally rather than filtered.
_OP_ID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")
_PHASE_NAME_PATTERN = r"[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?"
_PHASE_NAME_RE = re.compile(_PHASE_NAME_PATTERN + r"\Z")
_PHASE_FILE_RE = re.compile(r"([0-9]{4})-(" + _PHASE_NAME_PATTERN + r")\.json\Z")

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
    identities, the plan's identity and exact bytes, and the already-recorded phase roster the
    pre-append tree re-verification checks against. Closing releases descriptors ONLY; the
    substrate tree is the durable record and is never removed by this handle.
    """
    __slots__ = ("op_id", "store_root", "_ops_fd", "_op_fd", "_op_ident", "_plan_ident",
                 "_plan_bytes", "_phases", "_next_seq", "_closed")

    def __init__(self, op_id, store_root, ops_fd, op_fd, op_ident, plan_ident, plan_bytes):
        self.op_id = op_id
        self.store_root = store_root
        self._ops_fd = ops_fd
        self._op_fd = op_fd
        self._op_ident = op_ident
        self._plan_ident = plan_ident
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


def _listdir_fd(fd):
    """List an OPEN directory descriptor from offset zero. os.listdir(fd) reads through a dup
    that SHARES the descriptor's open file description, including its directory read offset and,
    on btrfs, a readdir upper bound the kernel snapshots when the directory is opened, so a
    RETAINED dir fd lists a stale view (missing every entry created after the open, or nothing
    at all) instead of the current tree. The explicit rewind resets the shared offset and
    refreshes that snapshot, and it stays bound to the trusted descriptor rather than reopening
    the directory by name, so no symlink-race window is introduced. Raises OSError exactly as
    os.listdir does; every caller already treats that as fail-closed."""
    os.lseek(fd, 0, os.SEEK_SET)
    return os.listdir(fd)


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


def _read_json_record(dir_fd, name, label, max_bytes):
    """Read a substrate record beneath dir_fd, no-follow and fail-closed: the OPENED object (not
    just the name) must be a plain singly-linked regular file within the byte cap, and the content
    strict JSON. Returns (doc, raw bytes); ANY failure raises, so a record the classifier cannot
    read is a refusal, never nothing-to-check (check-fails-closed-on-unreadable)."""
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
    finally:
        os.close(fd)
    raw = bytes(data)
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
    if type(utc) is not str or not utc or _opf_init_contract._CONTROL_RE.search(utc):
        return "utc must be a non-empty control-free string"
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


def _require_live_capability(cap):
    """A substrate WRITE runs only under the LIVE held operation lock: the exact OpCapability
    type, unreleased, called by the recorded acquirer (pid plus /proc start time), with the
    retained anchor identity still holding (a regular, singly-linked file with the acquire-time
    device and inode). Anything else refuses before any write."""
    if not isinstance(cap, _opf_oplock.OpCapability):
        raise InitSubstrateError("a substrate write requires a held OpCapability")
    if cap._released:
        raise InitSubstrateError("capability already released; a substrate write requires the "
                                 "held operation lock")
    pid = os.getpid()
    if pid != cap._acquirer_pid or _journal._pid_start(pid) != cap._acquirer_pid_start:
        raise InitSubstrateError("substrate write refused: caller (pid {}) is not the recorded "
                                 "acquirer (pid {})".format(pid, cap._acquirer_pid))
    try:
        st = os.fstat(cap._anchor_fd)
    except OSError as exc:
        raise InitSubstrateError("cannot fstat the held anchor ({})".format(exc))
    if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1 \
            or (st.st_dev, st.st_ino) != cap._anchor_ident:
        raise InitSubstrateError("the held anchor identity no longer holds; the flock no longer "
                                 "excludes anyone, so no substrate write may land")


def _open_ops_for_write(cap):
    """Open (creating each single component on genuine absence) the substrate ops/ home for a
    WRITE under the held capability. The freshly resolved control root must still carry the
    lock's opf-oplock directory with the capability's retained identity, so the write lands under
    the SAME control tree the held flock excludes for; a mismatch refuses. Returns the ops/ dir
    fd; the caller owns and closes it."""
    control_root_fd, desc = _open_control_root(cap.store_root)
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
                                                 dirname=OPS_DIRNAME)
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
    _validate_plan(plan_bytes, cap.op_id)
    ops_fd = _open_ops_for_write(cap)
    op_fd = None
    plan_fd = None
    created_dir = False
    plan_created = False
    plan_ident = None
    try:
        try:
            os.mkdir(cap.op_id, 0o755, dir_fd=ops_fd)
        except FileExistsError:
            raise InitSubstrateError("operation directory {} already exists; operation ids are "
                                     "never reused and a pre-existing tree is never adopted "
                                     "silently".format(cap.op_id))
        except OSError as exc:
            raise InitSubstrateError("cannot create operation directory {} ({})".format(
                cap.op_id, exc))
        created_dir = True
        try:
            os.fsync(ops_fd)
        except OSError as exc:
            raise InitSubstrateError("cannot fsync the ops directory after creation "
                                     "({})".format(exc))
        try:
            op_fd = _opf_oplock._open_dir_at(ops_fd, cap.op_id, "ops/{}".format(cap.op_id))
            _opf_oplock._validate_ctl_dir_fd(op_fd, "ops/{}".format(cap.op_id))
            plan_fd, plan_ident = _opf_oplock._create_control_file(op_fd, PLAN_NAME, plan_bytes,
                                                                   "plan record")
        except _opf_oplock.OpLockError as exc:
            raise InitSubstrateError(str(exc))
        plan_created = True
        os.close(plan_fd)
        plan_fd = None
        op_st = os.fstat(op_fd)
        return OpSubstrate(op_id=cap.op_id, store_root=cap.store_root, ops_fd=ops_fd,
                           op_fd=op_fd, op_ident=(op_st.st_dev, op_st.st_ino),
                           plan_ident=plan_ident, plan_bytes=plan_bytes)
    except BaseException as exc:
        # Unwind ONLY this facility's own just-created artefacts; every unwind failure is
        # collected, never swallowed. The composed _create_control_file has already unlinked its
        # own torn file, so on a write failure the directory is empty and removable.
        unwind = []
        if plan_created:
            try:
                _opf_oplock._verified_unlink(op_fd, PLAN_NAME, plan_ident, plan_bytes,
                                             "plan record (unwind)")
            except _opf_oplock.OpLockError as uexc:
                unwind.append(str(uexc))
        if created_dir:
            try:
                os.rmdir(cap.op_id, dir_fd=ops_fd)
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
    """Re-verify the on-disk operation tree against the handle's retained identities before an
    append: the ops/ entry still names the retained operation directory, and the directory holds
    EXACTLY the plan plus the already-recorded phase records, each still a plain singly-linked
    regular file with its recorded device and inode. Anything else refuses fail-closed and
    PRESERVES the tree (a substrate record is create-only and never repaired in place)."""
    try:
        name_st = os.stat(sub.op_id, dir_fd=sub._ops_fd, follow_symlinks=False)
    except OSError as exc:
        raise InitSubstrateError("cannot stat operation directory {} ({})".format(sub.op_id, exc))
    if not stat.S_ISDIR(name_st.st_mode) \
            or (name_st.st_dev, name_st.st_ino) != sub._op_ident:
        raise InitSubstrateError("operation directory {} identity changed while held".format(
            sub.op_id))
    expected = {PLAN_NAME: sub._plan_ident}
    for _seq, rname, rident in sub._phases:
        expected[rname] = rident
    try:
        present = sorted(_listdir_fd(sub._op_fd))
    except OSError as exc:
        raise InitSubstrateError("cannot list operation directory {} ({})".format(sub.op_id, exc))
    if sorted(expected) != present:
        raise InitSubstrateError("operation directory {} does not hold exactly the recorded tree "
                                 "(expected {}, found {}); refusing the append".format(
                                     sub.op_id, sorted(expected), present))
    for rname, rident in expected.items():
        try:
            st = os.stat(rname, dir_fd=sub._op_fd, follow_symlinks=False)
        except OSError as exc:
            raise InitSubstrateError("cannot stat recorded {} ({})".format(rname, exc))
        if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1 \
                or (st.st_dev, st.st_ino) != rident:
            raise InitSubstrateError("recorded {} identity changed while held (a substrate "
                                     "record is create-only and never replaced)".format(rname))


def record_phase(sub, cap, phase):
    """Append the next CREATE-ONLY phase record under the LIVE held capability. The sequence is
    strictly contiguous from 0001, the phase name is filename-safe by construction, the count is
    bounded, and the on-disk tree is re-verified against the handle's retained identities before
    the append, so a foreign entry, a gap, or a swapped record refuses rather than being built
    upon. Returns the record's file name."""
    if not isinstance(sub, OpSubstrate):
        raise InitSubstrateError("record_phase requires an OpSubstrate handle")
    if sub._closed:
        raise InitSubstrateError("substrate handle already closed")
    _require_live_capability(cap)
    if sub.op_id != cap.op_id:
        raise InitSubstrateError("substrate handle op_id {!r} does not match the held "
                                 "capability's {!r}".format(sub.op_id, cap.op_id))
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
    sub._phases.append((seq, name, ident))
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
            _opf_oplock._validate_ctl_dir_fd(op_fd, "ops/{}".format(name))
        except _opf_oplock.OpLockError as exc:
            return ce(str(exc))
        try:
            _doc, plan_raw = _read_json_record(op_fd, PLAN_NAME, "plan record", MAX_PLAN_BYTES)
            plan = _validate_plan(plan_raw, name)
        except InitSubstrateError as exc:
            return ce(str(exc))
        try:
            entries = sorted(_listdir_fd(op_fd))
        except OSError as exc:
            return ce("cannot list the operation directory ({})".format(exc))
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
            _opf_oplock._validate_ctl_dir_fd(ops_fd, ops_label)
        except _opf_oplock.OpLockError as exc:
            raise InitSubstrateError(str(exc))
        try:
            names = sorted(_listdir_fd(ops_fd))
        except OSError as exc:
            raise InitSubstrateError("cannot list the substrate ops tree ({})".format(exc))
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


def _st_phase_bytes(op_id, seq, phase):
    """Canonical opf.init.phase/v1 bytes for a synthetic classifier fixture."""
    return _opf_init_contract.canonical_json_bytes(dict(
        schema=1, format=PHASE_FORMAT, operation_id=op_id, seq=seq, phase=phase,
        utc="2026-01-01T00:00:00Z"))


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
    """T-s4: phase records are create-only and strictly contiguous; the tree is re-verified
    before each append, so a foreign entry or a swapped record refuses; phase names and the
    record count are bounded."""
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
    p1 = os.path.join(op_dir, n1)         # swap a record: same bytes, new inode
    with open(p1, "rb") as fh:
        same = fh.read()
    os.unlink(p1)
    with open(p1, "wb") as fh:
        fh.write(same)
    os.chmod(p1, 0o644)
    _st_expect_refusal(record_phase, sub, cap, "finalize", needle="identity")
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


def self_test():
    """Regression roster (the PR2 resume-substrate T-s witnesses), each a fail-to-pass
    discriminator against a named behaviour: the sibling-home placement under the composed
    authoritative control root (T-s1), the live-capability write gate (T-s2), the
    validate-before-write create-only plan (T-s3), the contiguous identity-verified phase
    discipline (T-s4), the read-only plan-aware classifier over the defect matrix (T-s5), the
    crash-then-control-leg-recovery composition with the substrate preserved (T-s6), the torn
    plan write stranding nothing (T-s7), and the fail-closed classifier roots (T-s8). A missing
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
        ("T-s4 contiguous, identity-verified, bounded phase records", _t_s4_phase_discipline),
        ("T-s5 read-only plan-aware classifier over the defect matrix", _t_s5_classifier),
        ("T-s6 crash, classify, recover control legs, substrate preserved",
         _t_s6_crash_resume),
        ("T-s7 a torn plan write strands nothing", _t_s7_torn_plan),
        ("T-s8 classifier roots fail closed", _t_s8_fail_closed_roots),
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
