"""Capability-bound store journal API. Its first writer, the MIG-PR5 ingest promotion coordinator,
retired with the import engine; other writers keep their legacy homes.

Homes-2 store writers must use this API for journal frames, terminal projections and irrevocable ID
reservations. Paths derive from kind and run identity; callers cannot choose a destination. Recovery opens existing state
and refuses a missing journal. It never bootstraps a replacement recovery history.

This is an in-process cooperation boundary, not protection from a malicious same-user writer.
It inherits the journal's accident-recovery and single-writer assumptions. Callers must not
fork while using the capability. Homes 2 has no homes migration and no legacy journal transport
(spec 4.2): no API here reads, moves or recovers from a homes-1 journal.
"""
import contextlib
import os
import stat
import tomllib
from pathlib import Path

import _journal
import _opf_emit
import _opf_init_substrate
import _opf_oplock
import _opf_schema
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
def _held_root(cap):
    """Validate the held capability (the shared open discipline of every store-journal entry point:
    a live, unclaimed OpCapability whose store identity still matches) and yield the trusted
    store-root fd. A capability-state refusal (not an OpCapability, being released or used, no
    longer live) raises its own message, and so does a store whose identity differs from the held
    capability (the fstat re-check below); an OSError or StoreError from the identity re-check or
    the root open maps to the one "cannot open store journal" refusal; a failure the caller raises
    inside the yield propagates unchanged."""
    if not isinstance(cap, _opf_oplock.OpCapability):
        raise _journal.JournalError("store journal access requires a held OpCapability")
    if not cap._claim.acquire(blocking=False):
        raise _journal.JournalError("operation capability is being released or used")
    root_fd = None
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
        except (OSError, _opf_store.StoreError) as exc:
            raise _journal.JournalError("cannot open store journal: {}".format(exc))
        yield root_fd
    finally:
        if root_fd is not None:
            _journal._close_fd_quietly(root_fd)
        cap._claim.release()


@contextlib.contextmanager
def _opened(cap, kind, run_id, create):
    _opf_store.txn_record(kind, run_id)  # validate both identity components before any I/O
    with _held_root(cap) as root_fd:
        jr_fd = None
        try:
            try:
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
            if jr_fd is not None:
                _journal._close_fd_quietly(jr_fd)


# --- shared native-journal validation (PR B): pure, kind-generic, non-mutating -------------------------
# Typed journal evidence is read through EXACTLY these validators and the projection model below, so a
# writer and a reader cannot drift. All of them are
# pure over already-captured frames or identity strings: no descriptor is opened, no directory is created,
# and no state is mutated. Identity is validated through the shared _opf_store constructors, so every
# registered kind (import, ingest, adoption, layout, preview, and the journal-only record) is admitted
# by one grammar.

PROJECTION_FORMAT = "opf.journal.transaction/v1"
PROJECTION_STATES = ("complete", "rolled-back")
_RUN_HEADER_KEYS = frozenset(("kind", "run_id", "operation_id"))
# The one header widening (spec 8.8): kind `record` MAY additionally carry `staged`, the planned
# poststate bytes, so record recovery can tell a torn write from an intervening edit. Every other
# kind keeps the exact three-key header.
_STAGED_HEADER_KIND = "record"
_STAGED_HEADER_KEY = "staged"


def _validate_identity(kind, run_id):
    try:
        _opf_store.txn_record(kind, run_id)
    except ValueError as exc:
        raise _journal.JournalError(str(exc)) from exc


def projection_record(kind, run_id, state, operation_id):
    """The terminal transaction projection's exact model (the producer's only shape). Pure; raises
    JournalError on a non-terminal state, an invalid identity, or a malformed operation id."""
    _validate_identity(kind, run_id)
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
    accepted-sequence state machine, then the exact {kind, run_id, operation_id} header binding (kind
    `record` alone may additionally carry the optional `staged` key). Raises JournalError; returns the
    INTENT frame object (None for an empty sequence)."""
    _validate_identity(kind, run_id)
    _journal._validate_terminal_agreement(frames)
    intent = _journal._first(frames, _journal.F_INTENT)
    if intent is not None:
        header = intent.get("header")
        allowed = set(_RUN_HEADER_KEYS)
        if kind == _STAGED_HEADER_KIND:
            allowed.add(_STAGED_HEADER_KEY)
        if (intent.get("txn") != run_id or not isinstance(header, dict)
                or not set(_RUN_HEADER_KEYS) <= set(header) or not set(header) <= allowed
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
    # The ancestor fsyncs run on EVERY projection, an existing entry included: the payload derives
    # from the journal's own INTENT header (never the caller's capability), so a run killed
    # anywhere inside publication (a torn staging write, the link made but the staging name not
    # yet retired, or a kill between the link and the directory flush) IS retried here with
    # byte-identical bytes, under whatever capability recovers it, and presence cannot stand in
    # for durability.
    _journal.ensure_journal_dirs(root_fd, rel.rsplit("/", 1)[0])
    pfd, name = _journal._open_parent(root_fd, rel)
    try:
        # Garbage first, reserve_allocation's ordering: a leftover killed after its os.link (link
        # count 2, the complete projection's second name) is retired BEFORE the final name is
        # tested or read, so the single-link read below accepts the surviving projection instead
        # of refusing it on every retry.
        try:
            _opf_oplock._remove_staging_garbage(pfd, name, "transaction projection")
        except _opf_oplock.OpLockError as exc:
            raise _journal.JournalError(str(exc))
        prior = _journal._lstat_contained(root_fd, rel)
        if prior is not None:
            raw, _ = _journal._read_contained(root_fd, rel, require_single_link=True)
            if raw != payload:
                raise _journal.JournalError(
                    "terminal transaction projection differs from its journal")
            # Byte-equal: re-establish the containing directory's durability (the same directory
            # fsync a fresh publication ends with) before accepting the existing projection.
            os.fsync(pfd)
            return
        try:
            # Publish complete bytes atomically and exclusively. Recovery can retry after an
            # interrupted staging write; it never overwrites a conflicting projection.
            fd, _ident = _opf_oplock._create_control_file(pfd, name, payload,
                                                          "transaction projection")
            os.close(fd)
        except _opf_oplock.OpLockError as exc:
            raise _journal.JournalError(str(exc))
    finally:
        os.close(pfd)


def run_transaction(cap, kind, run_id, ops, staged_reader, staged=None):
    """Run ordinary ops under the held capability, then derive the terminal projection. `staged`, when
    given, is the per-op planned poststate payload the INTENT header retains for recovery's torn-write
    explanation; it rides kind `record` only and is refused on every other kind."""
    _check_ordinary_ops(ops)
    if staged is not None and kind != _STAGED_HEADER_KIND:
        raise _journal.JournalError("a staged header rides the record kind only")
    with _opened(cap, kind, run_id, create=True) as (root_fd, jr_fd, txn_dir):
        header = dict(kind=kind, run_id=run_id, operation_id=cap.op_id)
        if staged is not None:
            header[_STAGED_HEADER_KEY] = staged
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


# --- irrevocable ID reservations (homes plan PR-3): the homes-2 allocation seam ---------------------------
# One journals/<kind>/allocations/<run-id>.toml per run (spec 4.2/8.2), created BEFORE the reversible
# journaled publication and never rewritten, whatever the run's outcome. The seam itself derives the
# numbers it reserves (spec 8.2's next-ID rule): under the held capability it reads counters.toml and
# every other reservation of every journal kind (reservation_high), and claims, per demanded counter
# key, the demanded count above the larger of the two, so two runs never reserve the same number
# whatever their callers planned. Each reservation is read back by that scan, and the run's own
# entry by a repeat call that finds it present and judges it against the exact payload it would
# publish. A failure inside publication (a failed directory fsync included) removes the just-linked
# entry, leaving no reservation rather than a non-durable one, EXCEPT when that cleanup cannot
# prove the entry exclusively its own or cannot remove it (a failed stat or unlink, or a link
# count that does not match the names the publication bound): _create_control_file then KEEPS
# the entry and names it in the refusal (DEF-5), and the kept entry reads as reserved
# (conservative) until a repeat call under the same capability re-establishes its durability
# through the read-back below.
# The record writer reserves through it: opf record at homes 2 (_opf_record._reserve_claim). The
# record verb admits only a store declaring spec_version 2.0.0 with homes = 2 and refuses any other
# before any write (_opf_record._probe_admission).

ALLOCATION_FORMAT = "opf.journal.allocation/v1"
# The kinds whose runs may claim the imported series (spec 8.2): the import engine's kinds. Import
# and ingest share the import engine's grammar (_opf_store); adoption seeds counters rather than
# reserving, and every kind claims the clean series.
_IMPORTED_SERIES_KINDS = frozenset(("import", "ingest"))
# A claim value is a counters.toml high-water (spec 8.2): a TOML integer, so at most 2**63 - 1; a
# larger emitted value would be unreadable by a conforming non-Python TOML reader.
_CLAIM_VALUE_MAX = 2 ** 63 - 1


def _check_claims(kind, claims):
    """Validate a reservation's claims purely (spec 8.2): a non-empty map of counter key to that
    run's new high-water value (an int in 1.._CLAIM_VALUE_MAX; a bool is not an int here). The
    admitted keys are exactly the counter keys the store's counter grammar admits for the reserving
    kind, judged through the one shared key validator (_opf_schema._is_counter_key, never a second
    regex): every record namespace, plus the quoted imported-series "imported:<NS>" keys when
    `kind` is an import-engine kind. `kind` was validated by the caller's _validate_identity."""
    imported = kind in _IMPORTED_SERIES_KINDS
    if (not isinstance(claims, dict) or not claims
            or not all(_opf_schema._is_counter_key(ns, imported)
                       and type(n) is int and 1 <= n <= _CLAIM_VALUE_MAX
                       for ns, n in claims.items())):
        raise _journal.JournalError(
            "id reservation claims must be a non-empty map of counter key (a record namespace, "
            "plus a quoted \"imported:<NS>\" key for an import-engine kind) to the run's new "
            "high-water (an integer >= 1 and at most 2**63 - 1, TOML's integer range)")


def _check_demand(kind, demand):
    """Validate a reservation request's demand purely, before any capability use: a non-empty map of
    counter key to how many ids the run takes under that key (an int in 1.._CLAIM_VALUE_MAX; a bool
    is not an int here). The admitted keys are _check_claims' keys for the reserving kind (the one
    shared key validator, _opf_schema._is_counter_key)."""
    imported = kind in _IMPORTED_SERIES_KINDS
    if (not isinstance(demand, dict) or not demand
            or not all(_opf_schema._is_counter_key(key, imported)
                       and type(n) is int and 1 <= n <= _CLAIM_VALUE_MAX
                       for key, n in demand.items())):
        raise _journal.JournalError(
            "an id reservation demand must be a non-empty map of counter key (a record namespace, "
            "plus a quoted \"imported:<NS>\" key for an import-engine kind) to how many ids the run "
            "takes under it (an integer >= 1 and at most 2**63 - 1)")


class ReservationDerivationChanged(_journal.JournalError):
    """The claims derived under the held capability differ from the ones the caller planned and
    passed as `expected`: another reservation or a counters change landed in between. Raised before
    anything is written."""


def _allocations_rel(kind):
    """The homes-2 reservation home of one journal kind, derived from the one path authority
    (_opf_store.allocation_record, over a placeholder run id in that kind's own run grammar): the
    directory every run of the kind publishes its irrevocable id reservation into (spec 4.2/8.2).
    Raises KeyError or ValueError for a kind the store grammar does not admit."""
    placeholder = _opf_store._HOME_RUN_PREFIXES[kind] + "-00000000T000000Z-0000000000000000"
    return _opf_store.allocation_record(kind, placeholder).rsplit("/", 1)[0]


def _reservation_run_id(kind, entry):
    """The run id a reservation file name binds in `kind`'s home, or None when the name is not
    <kind-run-id>.toml (allocation_record is the one grammar authority)."""
    if not (isinstance(entry, str) and entry.endswith(".toml")):
        return None
    run_id = entry[:-len(".toml")]
    try:
        _opf_store.allocation_record(kind, run_id)
    except ValueError:
        return None
    return run_id


def _is_reservation_garbage(kind, entry):
    """True ONLY for a staging leftover of a reservation publication (the _opf_oplock staging pattern
    bound to a grammar-valid reservation name of `kind`): a process that died before its exclusive link
    published no reservation (reserve_allocation retires such a leftover as garbage under the held
    capability), so the scan skips it, never reads it, and never removes it."""
    head, marker, _hexpart = entry.rpartition(_opf_oplock._STAGING_MARKER)
    return (bool(marker) and entry.startswith(".") and _opf_oplock._is_staging_name(entry, head[1:])
            and _reservation_run_id(kind, head[1:]) is not None)


def _read_reservation(afd, kind, entry):
    """One reservation of `kind`, read fail-closed beneath that kind's already-open allocations
    directory fd, returning its validated claims map: the entry bytes must equal the exact canonical
    payload of the `kind` reservation its own name and content declare (reservation_payload: a reader
    requires exact equality, so content naming another kind or run conflicts), so an unreadable,
    non-regular, misnamed, malformed or conflicting entry refuses (JournalError), never a silent
    fallback."""
    home = _allocations_rel(kind)
    rel = "{}/{}".format(home, entry)
    run_id = _reservation_run_id(kind, entry)
    if run_id is None:
        raise _journal.JournalError("the {} allocations home {} holds {!r}, which is not an id "
                                    "reservation (<{}-run-id>.toml) or a staging leftover of one; "
                                    "manual intervention required (fail-closed)".format(
                                        kind, home, entry, kind))
    try:
        raw, _st = _journal._read_at(afd, entry, rel)
    except (_journal.JournalError, OSError) as exc:
        raise _journal.JournalError("cannot read the id reservation {} ({}); fail-closed".format(rel, exc))
    try:
        model = tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError, MemoryError) as exc:
        # ValueError: tomllib.TOMLDecodeError and an over-long integer literal; RecursionError and MemoryError:
        # deep nesting and a many-part key.
        raise _journal.JournalError("the id reservation {} is malformed ({}); fail-closed".format(rel, exc))
    claims = model.get("claims") if isinstance(model, dict) else None
    operation_id = model.get("operation_id") if isinstance(model, dict) else None
    try:
        expected = reservation_payload(kind, run_id, operation_id, claims)
    except _journal.JournalError as exc:
        raise _journal.JournalError("the id reservation {} is malformed ({}); fail-closed".format(rel, exc))
    if raw != expected:
        raise _journal.JournalError("the id reservation {} conflicts with the exact canonical reservation "
                                    "its name and content declare (a reservation is never rewritten, "
                                    "spec 8.2); manual intervention required (fail-closed)".format(rel))
    return dict(claims)


def _kind_reservation_high(root_fd, kind, home, skip=None):
    """The per-counter-key maximum over the reservations in one kind's allocations home `home`: an
    absent home reads as no reservations (the empty map); an unopenable or unlistable home, and any
    entry _read_reservation refuses, refuses. A staging leftover of the publication idiom is skipped
    (_is_reservation_garbage): it carries no reservation. `skip`, when given, is one entry name left
    out unread (the requesting run's own reservation, reserve_allocation)."""
    try:
        pfd, name = _journal._open_parent(root_fd, home)
    except FileNotFoundError:
        return {}
    except (_journal.JournalError, OSError) as exc:
        raise _journal.JournalError("cannot open the {} allocations home {} ({}); fail-closed".format(
            kind, home, exc))
    try:
        try:
            afd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=pfd)
        except FileNotFoundError:
            return {}
        except OSError as exc:
            raise _journal.JournalError("cannot open the {} allocations home {} ({}); fail-closed".format(
                kind, home, exc))
        try:
            try:
                entries = sorted(os.listdir(afd))
            except OSError as exc:
                raise _journal.JournalError("cannot list the {} allocations home {} ({}); fail-closed".format(
                    kind, home, exc))
            high = {}
            for entry in entries:
                if entry == skip or _is_reservation_garbage(kind, entry):
                    continue
                for key, value in _read_reservation(afd, kind, entry).items():
                    if value > high.get(key, 0):
                        high[key] = value
            return high
        finally:
            _journal._close_fd_quietly(afd)
    finally:
        _journal._close_fd_quietly(pfd)


def reservation_high(root_fd, exclude=None):
    """The per-counter-key maximum over EVERY existing id reservation in EVERY journal kind's
    allocations home, the spec 8.2 next-ID derivation input (every reservation's [claims] value for
    each counter key, whichever kind published it): the homes are enumerated from the authoritative
    _opf_store.JOURNAL_KINDS registry, read at call time, never one hard-coded home, and each
    reservation is validated against its own kind and file name (_read_reservation). `exclude`, a
    (kind, run_id) pair, leaves that one run's own reservation out of the scan. An absent home reads as
    no reservations of that kind; a kind whose home cannot be derived, and an unreadable, non-regular,
    misnamed, malformed or conflicting entry in any home, refuse (JournalError), never a silent
    fallback."""
    high = {}
    for kind in _opf_store.JOURNAL_KINDS:
        try:
            home = _allocations_rel(kind)
        except (KeyError, ValueError) as exc:
            raise _journal.JournalError("the journal kind {!r} has no reservation home this build can "
                                        "derive ({}); fail-closed".format(kind, exc))
        skip = exclude[1] + ".toml" if exclude is not None and exclude[0] == kind else None
        for key, value in _kind_reservation_high(root_fd, kind, home, skip).items():
            if value > high.get(key, 0):
                high[key] = value
    return high


def _counter_high(root_fd, machine_rel, keys):
    """The counters.toml high-water of each counter key in `keys`, read fail-closed beneath the held
    store root (spec 8.2). The file is read as a singly-linked regular file through a no-follow,
    non-blocking open, so a missing file or parent, a symlink, a FIFO, a second hard link, a file past
    the read cap and a read error each refuse; so do bytes that are not UTF-8 TOML, any finding of the
    shared counters validator (_opf_schema.validate_counters, over every imported-series key the file
    itself carries), and a requested key the file does not carry: an absent counter is never read as
    0, since only an explicit row starts a series at zero."""
    rel = "{}/{}".format(machine_rel, _opf_store.COUNTERS_NAME)
    try:
        raw, _st = _journal._read_contained(root_fd, rel, require_single_link=True)
    except (_journal.JournalError, OSError) as exc:
        raise _journal.JournalError("cannot read {} for the id derivation ({}); fail-closed".format(rel, exc))
    try:
        model = tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError, MemoryError) as exc:
        # ValueError: tomllib.TOMLDecodeError and an over-long integer literal; RecursionError and MemoryError:
        # deep nesting and a many-part key.
        raise _journal.JournalError("{} is malformed ({}); fail-closed".format(rel, exc))
    table = model.get("counters")
    prefix = _opf_schema.IMPORTED_PREFIX
    imported = frozenset(key[len(prefix):] for key in table if key.startswith(prefix)) \
        if isinstance(table, dict) else frozenset()
    high, findings = _opf_schema.validate_counters(model, imported_namespaces=imported)
    if findings:
        raise _journal.JournalError("{} cannot license an id derivation: {}; fail-closed".format(
            rel, "; ".join(findings)))
    for key in keys:
        if key not in high:
            raise _journal.JournalError("{} carries no high-water for {}; an absent counter is never read "
                                        "as 0 (spec 8.2); fail-closed".format(rel, key))
    return {key: high[key] for key in keys}


def _derive_claims(root_fd, machine_rel, kind, run_id, demand):
    """The spec 8.2 next-ID derivation for one run, under the held capability: per demanded counter key,
    the larger of the counters.toml high-water and every other reservation's [claims] value for that key
    (the run's own reservation excluded, so a repeat call derives what the first call did), plus the
    demanded count. A result past _CLAIM_VALUE_MAX refuses: the range is used up."""
    reserved = reservation_high(root_fd, exclude=(kind, run_id))
    counters = _counter_high(root_fd, machine_rel, sorted(demand))
    claims = {}
    for key in sorted(demand):
        base = max(counters[key], reserved.get(key, 0))
        if base + demand[key] > _CLAIM_VALUE_MAX:
            raise _journal.JournalError(
                "cannot reserve {} more {} id(s) above the high-water {}: the result would pass "
                "2**63 - 1, the largest value an id reservation can carry, so the range is used up "
                "(spec 8.2); fail-closed".format(demand[key], key, base))
        claims[key] = base + demand[key]
    return claims


def reservation_record(kind, run_id, operation_id, claims):
    """The irrevocable ID reservation's exact model (spec 8.2; the writer's only shape). Pure; raises
    JournalError on an invalid identity, a malformed operation id, or malformed claims."""
    _validate_identity(kind, run_id)
    if not isinstance(operation_id, str) or not operation_id:
        raise _journal.JournalError("an id reservation requires a non-empty operation id")
    _check_claims(kind, claims)
    return dict(format=ALLOCATION_FORMAT, kind=kind, run_id=run_id, operation_id=operation_id,
                claims=dict(sorted(claims.items())))


def reservation_payload(kind, run_id, operation_id, claims):
    """The canonical reservation bytes the writer publishes; a reader requires exact equality."""
    return _opf_emit.emit_checked(reservation_record(kind, run_id, operation_id, claims)).encode("utf-8")


def _existing_reservation(pfd, name, kind, run_id, payload):
    """Judge the entry already at the reservation's final name against the payload THIS call would
    publish, through a non-blocking, regular-file-checked read beneath the same parent handle.
    The payload embeds the capability's op_id, so byte-equal content can only be the completed
    reservation of an earlier call under this SAME capability: it is answered idempotently, the
    containing directory fsynced again before the payload is returned (the earlier publication may
    have failed after its link with a cleanup that could not remove the entry, so presence alone
    does not establish durability). A retry after a kill that re-enters through acquire_operation
    holds a NEW capability whose fresh op_id changes the payload, so it is refused as different
    bytes, and the killed run's leftover reservation stays, its numbers conservatively still
    reserved. A resumed init instead attaches the PERSISTED operation id (attach_init_lease), so
    its retry presents byte-identical content and the byte-equal answer IS crash repair, safe
    because the directory is fsynced before the payload is returned. Different
    bytes, a non-regular entry, and an unreadable entry each refuse with their own message. A
    refusal is NEVER evidence that the run holds its numbers; only the read-back reservation
    bytes are."""
    try:
        # O_NONBLOCK so a non-regular entry (e.g. a FIFO) returns at once instead of blocking on a
        # writer-less open; the fstat below then refuses it (mirrors _journal._read_at).
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=pfd)
    except OSError as exc:
        raise _journal.JournalError("cannot read back the existing {} id reservation for {} "
                                    "({})".format(kind, run_id, exc))
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise _journal.JournalError("the {} id reservation name for {} is bound to a "
                                        "non-regular entry; refusing (manual intervention "
                                        "required)".format(kind, run_id))
        try:
            existing = _journal._read_fd(fd, cap=_journal._MAX_PRODUCT_READ_BYTES)
        except _journal.JournalError as exc:
            raise _journal.JournalError("cannot read back the existing {} id reservation for {} "
                                        "({})".format(kind, run_id, exc))
    finally:
        _journal._close_fd_quietly(fd)
    if existing != payload:
        raise _journal.JournalError(
            "the {} id reservation for {} already exists with different content; a reservation is "
            "irrevocable and never rewritten, and this refusal does not establish that the run "
            "holds the requested numbers (spec 8.2)".format(kind, run_id))
    try:
        os.fsync(pfd)
    except OSError as exc:
        raise _journal.JournalError("cannot fsync the directory of the existing {} id reservation "
                                    "for {} ({})".format(kind, run_id, exc))
    return payload


def reserve_allocation(cap, kind, run_id, demand, expected=None):
    """Derive and create THE run's irrevocable ID reservation at _opf_store.allocation_record(kind,
    run_id) under the held capability (spec 4.2/8.2): exclusive, create-only, durable before return.
    `demand` maps each counter key to how many ids the run takes under it. The claims are derived here,
    inside the one held-capability scope that also publishes them (spec 8.2: one atomic claim under
    the store's lock): per key, the demanded count above the larger of the counters.toml high-water
    and every other reservation's [claims] value in every journal kind's home (_derive_claims). A
    missing, unreadable or invalid counters.toml, a demanded key it does not carry, any reservation
    the scan cannot read and validate, and a range that would pass 2**63 - 1 each refuse with nothing
    written, not even a directory. `expected`, when given, is the claims map the caller planned: a
    derivation that differs raises ReservationDerivationChanged, again before anything is written.
    The run's own entry is left out of the derivation, so a repeat call under the same capability
    derives the same claims while counters.toml and every other reservation are unchanged. After
    either moves, the repeat derives again: claims that now differ are refused as different bytes,
    and a change that leaves the demanded keys' maximum where it was derives the published claims
    again and is answered from the read-back entry. The destination
    derives from kind and run identity alone; no caller names a path. Publication is _project's
    atomic-exclusive idiom (_create_control_file): the full payload is written and fsynced at a
    staging name, linked exclusively to the final name, the staging name retired and the directory
    fsynced. A process killed before the link leaves no reservation; killed after it, the complete
    payload stands at the final name (durable once the publication's directory fsync ran; a kill
    between the link and that fsync leaves the entry present but possibly not yet durable), plus
    at most a staging leftover a later call removes as garbage under the held capability before
    judging the final name. An entry already at the reservation path is judged by
    _existing_reservation: the byte-identical payload of an earlier call under this SAME capability
    is answered idempotently (the directory fsynced again, the payload returned); different bytes,
    a non-regular entry, or an unreadable entry refuses, naming which. The payload embeds the
    capability's op_id, so a retry after a kill that re-enters through acquire_operation (which
    mints a fresh op_id) is refused as different bytes: the killed run's leftover reservation is
    the disclosed residual, its numbers conservatively still reserved. A resumed init attaches
    the PERSISTED operation id instead (attach_init_lease), so its byte-identical retry is
    answered as crash repair, safe because the directory is fsynced before the payload is
    returned. A refusal is NEVER evidence that the run holds its
    numbers; only the returned payload bytes are. Uniqueness across runs rests on every writer of
    counters.toml and of the allocations homes doing so under a held capability. Returns the
    published payload bytes."""
    _validate_identity(kind, run_id)
    _check_demand(kind, demand)
    if expected is not None:
        _check_claims(kind, expected)
    rel = _opf_store.allocation_record(kind, run_id)
    with _held_root(cap) as root_fd:
        claims = _derive_claims(root_fd, cap.machine_rel, kind, run_id, demand)
        if expected is not None and claims != expected:
            raise ReservationDerivationChanged(
                "the {} id derivation for {} changed from the planned claims {!r} to {!r} (another "
                "reservation or a counters change landed in between); nothing written".format(
                    kind, run_id, dict(sorted(expected.items())), claims))
        payload = reservation_payload(kind, run_id, cap.op_id, claims)
        pfd = None
        try:
            try:
                _journal.ensure_journal_dirs(root_fd, rel.rsplit("/", 1)[0])
                pfd, name = _journal._open_parent(root_fd, rel)
            except (OSError, _opf_store.StoreError) as exc:
                raise _journal.JournalError("cannot open store journal: {}".format(exc))
            try:
                # Garbage first, so a leftover killed after its os.link (link count 2, the complete
                # record's second name) is retired before the final name is judged; the read-back
                # below then judges the surviving reservation like any other existing one.
                _opf_oplock._remove_staging_garbage(pfd, name, "id reservation")
                if _journal._lstat_at(pfd, name) is not None:
                    return _existing_reservation(pfd, name, kind, run_id, payload)
                fd, _ident = _opf_oplock._create_control_file(pfd, name, payload, "id reservation")
                os.close(fd)
            except _opf_oplock.OpLockError as exc:
                if isinstance(exc.__cause__, FileExistsError):
                    # The final name appeared between the existence check and the exclusive link,
                    # unreachable under the held capability's single-writer discipline: refuse with
                    # the reservation's own meaning, never the oplock anchor-recovery advice, and
                    # leave judging the entry to a later call's read-back. Any cleanup-failure
                    # detail the publication reported (a torn staging record it could not remove
                    # or could not prove exclusively its own, a stat or link-count refusal that
                    # stopped the cleanup, or a descriptor close that failed) is carried over,
                    # not dropped with the advice.
                    message = ("the {} id reservation for {} appeared during publication; a retry "
                               "judges the existing entry against its own payload "
                               "(spec 8.2)".format(kind, run_id))
                    detail = "; additionally the torn record could not be cleaned up: "
                    _, kept, leftover = str(exc).partition(detail)
                    if kept:
                        message += detail + leftover
                    raise _journal.JournalError(message)
                raise _journal.JournalError(str(exc))
            except OSError as exc:
                raise _journal.JournalError("store journal operation failed: {}".format(exc))
        finally:
            if pfd is not None:
                _journal._close_fd_quietly(pfd)
    return payload


# --- publication attempts (MIG-PR5): one logical run, several journal attempts ---------------------------
# The attempt runner and its state and INTENT readers retired with their only caller, the ingest
# promotion coordinator; the attempt naming and frame validation remain (check_opf_homes exercises them).

_ATTEMPT_MAX = 9999
_ATTEMPT_HEADER_KEYS = frozenset(("kind", "run_id", "attempt", "operation_id"))


def attempt_txn(kind, run_id, attempt):
    """The journal transaction name of one publication attempt of a stable logical run. A retry after a
    terminal rollback takes a fresh attempt; what the run reserved stays with the run, not the attempt."""
    _validate_identity(kind, run_id)
    if type(attempt) is not int or not 1 <= attempt <= _ATTEMPT_MAX:
        raise _journal.JournalError("attempt must be an int in 1..{}".format(_ATTEMPT_MAX))
    return "{}.a{:04d}".format(run_id, attempt)


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
