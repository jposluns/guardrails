"""Capability-bound store journal API. Existing writers remain on their legacy homes.

Store writers use this API for journal frames and terminal projections. Paths derive from
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


@contextlib.contextmanager
def _opened(cap, kind, run_id, create):
    _opf_store.txn_record(kind, run_id)  # validate both identity components before any I/O
    if not isinstance(cap, _opf_oplock.OpCapability):
        raise _journal.JournalError("store journal access requires a held OpCapability")
    if not cap._claim.acquire(blocking=False):
        raise _journal.JournalError("operation capability is being released or used")
    root_fd = jr_fd = None
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
        yield root_fd, jr_fd, Path(cap.store_root) / rel / run_id
    except (OSError, _opf_store.StoreError) as exc:
        raise _journal.JournalError("cannot open store journal: {}".format(exc))
    finally:
        for fd in (jr_fd, root_fd):
            if fd is not None:
                _journal._close_fd_quietly(fd)
        cap._claim.release()


def _existing_frames(jr_fd, txn_dir, kind, run_id):
    # The generic legacy reader treats an absent log as empty. Requested store recovery must
    # distinguish that from a present, empty pre-intent log before any truncate or append.
    st = _journal._lstat_contained(jr_fd, run_id + "/frames.log")
    if st is None or not stat.S_ISREG(st.st_mode):
        raise _journal.JournalError("requested recovery journal is missing or not a regular file")
    frames, _torn, _good = _journal.read_frames(jr_fd, txn_dir)
    _journal._validate_terminal_agreement(frames)
    intent = _journal._first(frames, _journal.F_INTENT)
    if intent is not None:
        header = intent.get("header")
        if (intent.get("txn") != run_id or not isinstance(header, dict)
                or set(header) != {"kind", "run_id", "operation_id"}
                or header.get("kind") != kind or header.get("run_id") != run_id
                or not isinstance(header.get("operation_id"), str) or not header["operation_id"]):
            raise _journal.JournalError("store journal identity does not match the requested operation")
    return frames


def _project(root_fd, jr_fd, txn_dir, kind, run_id):
    frames = _existing_frames(jr_fd, txn_dir, kind, run_id)
    intent = _journal._first(frames, _journal.F_INTENT)
    if intent is None:
        return
    state = _journal.classify_state(jr_fd, txn_dir)
    if state not in ("complete", "rolled-back"):
        raise _journal.JournalError("a transaction projection requires terminal journal evidence")
    record = dict(format="opf.journal.transaction/v1", kind=kind, run_id=run_id, state=state,
                  operation_id=intent["header"]["operation_id"], journal_rel=_opf_store.journal_root(kind))
    payload = _opf_emit.emit_checked(record).encode("utf-8")
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
    _journal._check_ordinary_ops(ops)
    with _opened(cap, kind, run_id, create=True) as (root_fd, jr_fd, txn_dir):
        header = dict(kind=kind, run_id=run_id, operation_id=cap.op_id)
        result = _journal.run_transaction(root_fd, jr_fd, txn_dir.parent, run_id, header,
                                          ops, staged_reader, cap.holder)
        _project(root_fd, jr_fd, txn_dir, kind, run_id)
        return result


def recover_transaction(cap, kind, run_id):
    """Recover a named existing transaction; missing machine-local evidence refuses."""
    with _opened(cap, kind, run_id, create=False) as (root_fd, jr_fd, txn_dir):
        _existing_frames(jr_fd, txn_dir, kind, run_id)
        result = _journal.recover(jr_fd, txn_dir, root_fd)
        _project(root_fd, jr_fd, txn_dir, kind, run_id)
        return result
