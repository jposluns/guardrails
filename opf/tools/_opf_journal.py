"""Capability-bound store journal API. Its first writer is the MIG-PR5 ingest promotion coordinator
(publication attempts and the ingest writer lock); other writers keep their legacy homes.

Homes-2 store writers must use this API for journal frames and terminal projections. Paths derive from
kind and run identity; callers cannot choose a destination. Recovery opens existing state
and refuses a missing journal. It never bootstraps a replacement recovery history.

This is an in-process cooperation boundary, not protection from a malicious same-user writer.
It inherits the journal's accident-recovery and single-writer assumptions. Callers must not
fork while using the capability. Legacy journal transport belongs to the migration reader;
it must preserve original frame bytes and bind their new location through a receipt.
"""
import contextlib
import os
import stat
from pathlib import Path

import _journal
import _opf_emit
import _opf_init_substrate
import _opf_oplock
import _opf_store


def _check_ordinary_ops(ops):
    """Validate a whole homes-2 batch before any mutation or torn-tail truncate. This boundary is
    this API's alone: the legacy journal engine keeps its legacy operand handling unchanged."""
    if not isinstance(ops, (list, tuple)):
        raise _journal.JournalError("ordinary operations must be a list or tuple")
    for op in ops:
        if not isinstance(op, dict) or op.get("op") not in _journal.OP_KINDS:
            raise _journal.JournalError("malformed ordinary operation")
        _journal._check_rel(op.get("path"))
        try:
            _opf_store.require_ordinary_target(op["path"])
        except ValueError as exc:
            raise _journal.JournalError(str(exc))


@contextlib.contextmanager
def _opened(cap, kind, run_id, create):
    _opf_store.txn_record(kind, run_id)  # validate both identity components before any I/O
    if not isinstance(cap, _opf_oplock.OpCapability):
        raise _journal.JournalError("store journal access requires a held OpCapability")
    if not cap._claim.acquire(blocking=False):
        raise _journal.JournalError("operation capability is being released or used")
    root_fd = jr_fd = None
    try:
        try:
            if cap._claimant is not None:
                raise _journal.JournalError("operation capability is being released")
            try:
                _opf_init_substrate._require_live_capability(cap)
            except _opf_init_substrate.InitSubstrateError as exc:
                raise _journal.JournalError(str(exc))
            root_fd = _opf_store._open_root_fd(cap.store_root)
            machine_fd = _journal._open_dir_contained(root_fd, cap.machine_rel)
            try:
                st = os.fstat(machine_fd)
                if (st.st_dev, st.st_ino) != cap._machine_ident:
                    raise _journal.JournalError("store identity differs from the held capability")
            finally:
                os.close(machine_fd)
            rel = _opf_store.journal_root(kind)
            if create:
                _journal.ensure_journal_dirs(root_fd, rel)
            jr_fd = _journal.open_journal_root_fd(root_fd, rel)
        except (OSError, _opf_store.StoreError) as exc:
            raise _journal.JournalError("cannot open store journal: {}".format(exc))
        # A failure inside the caller's operation is attributed to the operation, not the open.
        try:
            yield root_fd, jr_fd, Path(cap.store_root) / rel / run_id
        except (OSError, _opf_store.StoreError) as exc:
            raise _journal.JournalError("store journal operation failed: {}".format(exc))
    finally:
        for fd in (jr_fd, root_fd):
            if fd is not None:
                _journal._close_fd_quietly(fd)
        cap._claim.release()


# --- shared native-journal validation (PR B): pure, kind-generic, non-mutating -------------------------
# The staged-run gate (check_opf_import, generation 2) grades typed evidence through EXACTLY these
# validators and the projection model below, so the writer and the reader cannot drift. All of them are
# pure over already-captured frames or identity strings: no descriptor is opened, no directory is created,
# and no state is mutated. Identity is validated through the shared _opf_store constructors, so every
# registered kind (import, ingest, adoption, layout, preview) is admitted by one grammar.

PROJECTION_FORMAT = "opf.journal.transaction/v1"
PROJECTION_STATES = ("complete", "rolled-back")
_RUN_HEADER_KEYS = frozenset(("kind", "run_id", "operation_id"))


def projection_record(kind, run_id, state, operation_id):
    """The terminal transaction projection's exact model (the producer's only shape). Pure; raises
    JournalError on a non-terminal state, an invalid identity, or a malformed operation id."""
    _opf_store.txn_record(kind, run_id)  # validate both identity components (kind-generic)
    if state not in PROJECTION_STATES:
        raise _journal.JournalError("a transaction projection requires terminal journal evidence")
    if not isinstance(operation_id, str) or not operation_id:
        raise _journal.JournalError("a transaction projection requires a non-empty operation id")
    return dict(format=PROJECTION_FORMAT, kind=kind, run_id=run_id, state=state,
                operation_id=operation_id, journal_rel=_opf_store.journal_root(kind))


def projection_payload(kind, run_id, state, operation_id):
    """The canonical projection bytes the producer publishes; a reader requires exact equality."""
    return _opf_emit.emit_checked(projection_record(kind, run_id, state, operation_id)).encode("utf-8")


def state_of_frames(frames):
    """The C2 state of ONE captured frame sequence (read_frames already drops a torn tail), so identity
    and state are judged over the same observation instead of separate re-reads."""
    types = [t for t, _ in frames]
    if _journal.F_INTENT not in types:
        return "nothing-opened"
    if _journal.F_COMPLETE in types:
        return "complete"
    if _journal.F_RC in types:
        return "rolled-back"
    return "open"


def check_run_frames(frames, kind, run_id):
    """Pure validation of one single-transaction frame sequence against the requested identity: the C2
    accepted-sequence state machine, then the exact {kind, run_id, operation_id} header binding. Raises
    JournalError; returns the INTENT frame object (None for an empty sequence)."""
    _journal._validate_terminal_agreement(frames)
    intent = _journal._first(frames, _journal.F_INTENT)
    if intent is not None:
        header = intent.get("header")
        if (intent.get("txn") != run_id or not isinstance(header, dict)
                or set(header) != set(_RUN_HEADER_KEYS)
                or header.get("kind") != kind or header.get("run_id") != run_id
                or not isinstance(header.get("operation_id"), str) or not header["operation_id"]):
            raise _journal.JournalError("store journal identity does not match the requested operation")
    return intent


def _existing_frames(jr_fd, txn_dir, kind, run_id):
    # The generic legacy reader treats an absent log as empty. Requested store recovery must
    # distinguish that from a present, empty pre-intent log before any truncate or append.
    st = _journal._lstat_contained(jr_fd, run_id + "/frames.log")
    if st is None or not stat.S_ISREG(st.st_mode):
        raise _journal.JournalError("requested recovery journal is missing or not a regular file")
    frames, _torn, _good = _journal.read_frames(jr_fd, txn_dir)
    check_run_frames(frames, kind, run_id)
    return frames


def _project(root_fd, jr_fd, txn_dir, kind, run_id):
    frames = _existing_frames(jr_fd, txn_dir, kind, run_id)
    intent = _journal._first(frames, _journal.F_INTENT)
    if intent is None:
        return
    # projection_record refuses a non-terminal state; frames were validated by _existing_frames, so the
    # state is judged over the same captured sequence rather than a separate re-read.
    payload = projection_payload(kind, run_id, state_of_frames(frames), intent["header"]["operation_id"])
    rel = _opf_store.txn_record(kind, run_id)
    prior = _journal._lstat_contained(root_fd, rel)
    if prior is not None:
        raw, _ = _journal._read_contained(root_fd, rel, require_single_link=True)
        if raw != payload:
            raise _journal.JournalError("terminal transaction projection differs from its journal")
        return
    _journal.ensure_journal_dirs(root_fd, rel.rsplit("/", 1)[0])
    pfd, name = _journal._open_parent(root_fd, rel)
    try:
        # Publish complete bytes atomically and exclusively. Recovery can retry after an
        # interrupted staging write; it never overwrites a conflicting projection.
        _opf_oplock._remove_staging_garbage(pfd, name, "transaction projection")
        fd, _ident = _opf_oplock._create_control_file(pfd, name, payload, "transaction projection")
        os.close(fd)
    except _opf_oplock.OpLockError as exc:
        raise _journal.JournalError(str(exc))
    finally:
        os.close(pfd)


def run_transaction(cap, kind, run_id, ops, staged_reader):
    """Run ordinary ops under the held capability, then derive the terminal projection."""
    _check_ordinary_ops(ops)
    with _opened(cap, kind, run_id, create=True) as (root_fd, jr_fd, txn_dir):
        header = dict(kind=kind, run_id=run_id, operation_id=cap.op_id)
        result = _journal.run_transaction(root_fd, jr_fd, txn_dir.parent, run_id, header,
                                          ops, staged_reader, cap.holder)
        _project(root_fd, jr_fd, txn_dir, kind, run_id)
        return result


def recover_transaction(cap, kind, run_id):
    """Recover a named existing transaction; missing machine-local evidence refuses."""
    with _opened(cap, kind, run_id, create=False) as (root_fd, jr_fd, txn_dir):
        frames = _existing_frames(jr_fd, txn_dir, kind, run_id)
        # Only an open transaction is acted on, so only its operands are checked, before recover can
        # truncate a torn tail. A terminal journal is history: it stays inert and its projection binds it.
        types = [t for t, _ in frames]
        intent = _journal._first(frames, _journal.F_INTENT)
        if intent is not None and _journal.F_COMPLETE not in types and _journal.F_RC not in types:
            _check_ordinary_ops(intent.get("ops"))
        result = _journal.recover(jr_fd, txn_dir, root_fd)
        _project(root_fd, jr_fd, txn_dir, kind, run_id)
        return result


# --- publication attempts (MIG-PR5): one logical run, several journal attempts ---------------------------

_ATTEMPT_MAX = 9999
_ATTEMPT_HEADER_KEYS = frozenset(("kind", "run_id", "attempt", "operation_id"))


def attempt_txn(kind, run_id, attempt):
    """The journal transaction name of one publication attempt of a stable logical run. A retry after a
    terminal rollback takes a fresh attempt; what the run reserved stays with the run, not the attempt."""
    _opf_store.txn_record(kind, run_id)  # validate both identity components
    if type(attempt) is not int or not 1 <= attempt <= _ATTEMPT_MAX:
        raise _journal.JournalError("attempt must be an int in 1..{}".format(_ATTEMPT_MAX))
    return "{}.a{:04d}".format(run_id, attempt)


def _attempt_of(kind, run_id, name):
    """The attempt number a journal entry names for this run, None for another run's entry. An entry
    carrying this run's prefix in any other spelling is refused, never skipped."""
    if name != run_id and not name.startswith(run_id + "."):
        return None
    suffix = name[len(run_id) + 2:] if name.startswith(run_id + ".a") else ""
    if len(suffix) == 4 and suffix.isdigit() and suffix.isascii():
        n = int(suffix)
        if 1 <= n <= _ATTEMPT_MAX and attempt_txn(kind, run_id, n) == name:
            return n
    raise _journal.JournalError("journal entry {!r} is not an attempt of run {}".format(name, run_id))


def attempt_states(cap, kind, run_id):
    """{attempt: state} for every recorded publication attempt of a run, classified contained."""
    with _opened(cap, kind, run_id, create=True) as (_root_fd, jr_fd, txn_dir):
        states = {}
        for entry in _journal._journal_txn_dirs(jr_fd, txn_dir.parent):
            n = _attempt_of(kind, run_id, entry.name)
            if n is not None:
                states[n] = _journal.classify_state(jr_fd, entry)
        return states


def check_attempt_frames(frames, kind, run_id, attempt):
    """Pure validation of one publication attempt's frame sequence against its exact identity (the C2
    accepted sequences, then the closed attempt header). Raises JournalError; returns the INTENT frame
    object (None for an empty sequence)."""
    txn = attempt_txn(kind, run_id, attempt)
    _journal._validate_terminal_agreement(frames)
    intent = _journal._first(frames, _journal.F_INTENT)
    if intent is not None:
        header = intent.get("header") if isinstance(intent, dict) else None
        if not (isinstance(header, dict) and set(header) == _ATTEMPT_HEADER_KEYS
                and intent.get("txn") == txn and header.get("kind") == kind
                and header.get("run_id") == run_id and header.get("attempt") == attempt
                and isinstance(header.get("operation_id"), str) and header["operation_id"]):
            raise _journal.JournalError("attempt journal identity does not match {}".format(txn))
    return intent


def read_attempt_intent(jr_fd, kind, run_id, attempt):
    """Read-only: the INTENT of a COMPLETE attempt, read beneath an ALREADY-OPEN journal-root descriptor
    and bound to its own identity; anything else refuses. Shared by the capability wrapper below and the
    generation-2 staged-run gate, which must never enter the create-capable _opened path."""
    txn = attempt_txn(kind, run_id, attempt)
    frames, _torn, _good = _journal.read_frames(jr_fd, txn)
    _journal._validate_terminal_agreement(frames)
    if state_of_frames(frames) != "complete":
        raise _journal.JournalError("attempt {} is not complete".format(txn))
    intent = check_attempt_frames(frames, kind, run_id, attempt)
    if intent is None:
        raise _journal.JournalError("attempt {} is not complete".format(txn))
    return intent


def attempt_intent(cap, kind, run_id, attempt):
    """The INTENT of a COMPLETE attempt, under the held capability (read_attempt_intent does the work)."""
    with _opened(cap, kind, run_id, create=False) as (_root_fd, jr_fd, _txn_dir):
        return read_attempt_intent(jr_fd, kind, run_id, attempt)


def run_attempt_transaction(cap, kind, run_id, attempt, ops, staged_reader):
    """Run one publication attempt under the held capability. No projection is written: the caller's
    completion receipt is an operation of the same transaction, bound by its INTENT digest."""
    txn = attempt_txn(kind, run_id, attempt)
    _check_ordinary_ops(ops)
    with _opened(cap, kind, run_id, create=True) as (root_fd, jr_fd, txn_dir):
        header = dict(kind=kind, run_id=run_id, attempt=attempt, operation_id=cap.op_id)
        return _journal.run_transaction(root_fd, jr_fd, txn_dir.parent, txn, header,
                                        ops, staged_reader, cap.holder)


def _writer_lock_root(cap, kind):
    return Path(cap.store_root) / _opf_store.journal_root(kind)


def acquire_writer_lock(cap, kind):
    """Take the kind's _journal writer lock beneath the held capability (lock order: capability, then
    journal). An existing lock refuses: breaking a stale one is recovery's step, not this call's."""
    rel = _opf_store.journal_root(kind)
    if not isinstance(cap, _opf_oplock.OpCapability):
        raise _journal.JournalError("the journal writer lock requires a held OpCapability")
    try:
        _opf_init_substrate._require_live_capability(cap)
    except _opf_init_substrate.InitSubstrateError as exc:
        raise _journal.JournalError(str(exc))
    try:
        root_fd = _opf_store._open_root_fd(cap.store_root)
    except (OSError, _opf_store.StoreError) as exc:
        raise _journal.JournalError("cannot open store journal: {}".format(exc))
    try:
        _journal.ensure_journal_dirs(root_fd, rel)
    finally:
        _journal._close_fd_quietly(root_fd)
    _journal.acquire_lock(_writer_lock_root(cap, kind), session_id=cap.holder)


def writer_lock_held(cap, kind):
    """Whether this process owns the kind's _journal writer lock while the capability is live."""
    try:
        _opf_init_substrate._require_live_capability(cap)
        owner = _journal.read_lock_owner(_writer_lock_root(cap, kind))
    except (_opf_init_substrate.InitSubstrateError, _journal.JournalError):
        return False
    return owner is not None and _journal._owner_is_current(owner)


def release_writer_lock(cap, kind):
    _journal.release_lock(_writer_lock_root(cap, kind))
