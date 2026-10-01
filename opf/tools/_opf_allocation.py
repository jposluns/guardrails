"""Permanent record-ID reservation for ingest promotion (MIG-PR5 slice 1).

Allocation happens only under the held store operation capability AND the ingest _journal writer lock
(lock order: capability, then journal). A reservation is IRREVOCABLE and publication is reversible: the
reservation record is published exclusively and durably in the journal home, which no ordinary
publication or rollback operand can reach (_opf_store.require_ordinary_target), and counters.toml is then
advanced by an atomic replace through the pinned machine directory. A publication failure after that
point leaves gaps; it can never free a number, and _journal.recover never restores counters because
counters.toml is never a publication operand.

A retry with identical run/review/acceptance bindings reuses the SAME logical allocation and mints
nothing. A changed binding refuses: the run is retained as evidence and a fresh run is required, and its
reservation stays consumed.

Disclosed residuals (slice 1): this does not detect a coordinated malicious rewrite of both counters and
allocation history; it prevents reuse through cooperating apply, rollback, and retry. The crash matrix
is reconciled only in part: a durable reservation whose counter advance was lost is re-applied here
(componentwise maximum), while a leftover temporary refuses rather than being cleaned, pending the
slice-2 recovery design.

Its only caller, the ingest execution coordinator, retired with the import engine; the module is kept,
uncalled, with its own direct lane:

  _opf_allocation.py --self-test   the reservation vectors over a synthetic store
"""
import hashlib
import os
import stat
import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # the sibling helpers, for the direct --self-test lane
import _journal  # noqa: E402
import _opf_emit  # noqa: E402
import _opf_journal  # noqa: E402
import _opf_oplock  # noqa: E402
import _opf_schema  # noqa: E402
import _opf_store  # noqa: E402

RESERVATION_FORMAT = "opf.ingest.reservation/v1"
SCHEMA = 1
KIND = "ingest"
COUNTERS_NAME = "counters.toml"
BINDING_KEYS = frozenset(("run_id", "review_model_digest", "bundle_digest", "acceptance_digest"))
_TOP_KEYS = frozenset(("format", "schema", "run_id", "binding", "stamp", "counters_before",
                       "counters_after", "allocation"))
_TMP_PREFIX = ".counters.toml.opf-alloc-"


class AllocationError(Exception):
    """Allocation cannot proceed safely; nothing was reserved by the failing step."""


class Reservation:
    __slots__ = ("run_id", "ids", "stamp", "digest", "reused", "counters_after")

    def __init__(self, run_id, ids, stamp, digest, reused, counters_after):
        self.run_id = run_id
        self.ids = ids                      # logical key -> permanent id, in demand order
        self.stamp = stamp
        self.digest = digest                # "sha256:" + hex of the published reservation bytes
        self.reused = reused
        self.counters_after = counters_after


def _sha(data):
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _counter_map(value, where):
    high, findings = _opf_schema.validate_counters({"schema": SCHEMA, "counters": value})
    if findings or not isinstance(value, dict):
        raise AllocationError("{} is not a valid counters map ({})".format(where, "; ".join(findings)))
    return high


def _validate_reservation(model, run_id):
    """Closed-schema validation of one reservation record; every id lies in its namespace's reserved
    interval (before, after], so a record cannot claim a number it did not advance past."""
    if not (isinstance(model, dict) and set(model) == _TOP_KEYS):
        raise AllocationError("reservation for {} is not a closed reservation record".format(run_id))
    if (model["format"] != RESERVATION_FORMAT or type(model["schema"]) is not int
            or model["schema"] != SCHEMA or model["run_id"] != run_id):
        raise AllocationError("reservation for {} has a wrong format, schema, or run".format(run_id))
    binding = model["binding"]
    if not (isinstance(binding, dict) and set(binding) == BINDING_KEYS
            and all(isinstance(v, str) and v for v in binding.values()) and binding["run_id"] == run_id):
        raise AllocationError("reservation for {} carries a malformed binding".format(run_id))
    if not (isinstance(model["stamp"], str) and model["stamp"]):
        raise AllocationError("reservation for {} carries no stamp".format(run_id))
    before = _counter_map(model["counters_before"], "reservation counters_before")
    after = _counter_map(model["counters_after"], "reservation counters_after")
    if set(before) != set(after) or _opf_schema.check_monotonic(before, after):
        raise AllocationError("reservation for {} does not advance its counters monotonically".format(run_id))
    keys, ids = set(), set()
    alloc = model["allocation"]
    if not isinstance(alloc, list):
        raise AllocationError("reservation for {} carries no allocation list".format(run_id))
    for row in alloc:
        if not (isinstance(row, dict) and set(row) == {"key", "id"} and isinstance(row["key"], str)
                and row["key"] and row["key"] not in keys and row["id"] not in ids):
            raise AllocationError("reservation for {} carries a malformed or duplicate row".format(run_id))
        shape = _opf_schema._valid_id_shape(row["id"])
        if shape is None or shape[0] not in after or not before[shape[0]] < shape[1] <= after[shape[0]]:
            raise AllocationError("reservation for {}: {!r} lies outside its reserved interval".format(
                run_id, row["id"]))
        keys.add(row["key"])
        ids.add(row["id"])
    return after


def _read_regular(dir_fd, name, cap, what):
    """One bounded, no-follow, single-link read beneath a pinned directory: (bytes, fstat)."""
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=dir_fd)
    except OSError as exc:
        raise AllocationError("cannot open {} no-follow ({})".format(what, exc))
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1 or st.st_size > cap:
            raise AllocationError("{} is not a bounded, singly-linked regular file".format(what))
        try:
            data = _journal._read_fd(fd, cap=cap)
        except _journal.JournalError as exc:
            raise AllocationError("cannot read {} ({})".format(what, exc))
        return data, st
    finally:
        os.close(fd)


def _parse(data, what):
    try:
        return tomllib.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise AllocationError("{} is malformed ({})".format(what, exc))


def _publish_exclusive(dir_fd, name, payload, mode=0o644):
    """Complete bytes, exclusively created, fsynced with their directory, then re-read and compared."""
    try:
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, mode,
                     dir_fd=dir_fd)
    except OSError as exc:
        raise AllocationError("cannot exclusively create {} ({})".format(name, exc))
    try:
        os.fchmod(fd, mode)
        _journal._write_all(fd, payload)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.fsync(dir_fd)
    if _read_regular(dir_fd, name, len(payload), name)[0] != payload:
        raise AllocationError("{} does not read back as the bytes published".format(name))


def _replace_counters(machine_fd, prior_raw, prior_st, payload, run_id):
    """Atomic counters replacement through the pinned machine directory: an exclusive sibling temporary,
    fsynced and read back, then the prior target re-verified (identity and bytes) immediately before the
    rename, then the directory fsynced. The live file is never truncated in place."""
    tmp = _TMP_PREFIX + run_id
    _publish_exclusive(machine_fd, tmp, payload, stat.S_IMODE(prior_st.st_mode))
    try:
        raw, st = _read_regular(machine_fd, COUNTERS_NAME, _opf_store.MAX_STORE_READ_BYTES, COUNTERS_NAME)
        if (st.st_dev, st.st_ino) != (prior_st.st_dev, prior_st.st_ino) or raw != prior_raw:
            raise AllocationError("counters.toml changed under the held locks; refusing to replace it")
        os.rename(tmp, COUNTERS_NAME, src_dir_fd=machine_fd, dst_dir_fd=machine_fd)
    except BaseException:
        try:
            os.unlink(tmp, dir_fd=machine_fd)
        except OSError:
            pass
        raise
    os.fsync(machine_fd)


def _emit(model):
    return _opf_emit.emit_checked(model).encode("utf-8")


def reserve_ingest_ids(cap, run_id, binding, demand, stamp, live_ids):
    """Reserve permanent ids for `demand` (an ordered list of (logical key, namespace)) and advance
    counters.toml, or reuse this run's existing reservation. `live_ids` is every durable id seated in the
    active store and archive, read by the caller under the same locks. Returns a Reservation."""
    try:
        _opf_oplock.require_live_holder(cap)
    except _opf_oplock.OpLockError as exc:
        raise AllocationError("allocation requires the held store capability ({})".format(exc))
    if not _opf_journal.writer_lock_held(cap, KIND):
        raise AllocationError("allocation requires this process to hold the ingest journal writer lock")
    if not (isinstance(binding, dict) and set(binding) == BINDING_KEYS
            and all(isinstance(v, str) and v for v in binding.values()) and binding["run_id"] == run_id):
        raise AllocationError("allocation binding is malformed")
    keys = [k for k, _ns in demand]
    if len(set(keys)) != len(keys) or not all(isinstance(k, str) and k for k in keys):
        raise AllocationError("allocation demand carries a duplicate or malformed logical key")
    rec_rel = _opf_store.allocation_record(KIND, run_id)
    dir_rel, rec_name = rec_rel.rsplit("/", 1)
    root_fd = _opf_store._open_root_fd(cap.store_root)
    machine_fd = dir_fd = None
    try:
        machine_fd = _journal._open_dir_contained(root_fd, cap.machine_rel)
        st = os.fstat(machine_fd)
        if (st.st_dev, st.st_ino) != cap._machine_ident:
            raise AllocationError("machine store identity differs from the held capability")
        _journal.ensure_journal_dirs(root_fd, dir_rel)
        dir_fd = _journal._open_dir_contained(root_fd, dir_rel)
        return _reserve(machine_fd, dir_fd, run_id, rec_name, binding, demand, stamp, live_ids)
    except _journal.JournalError as exc:
        raise AllocationError(str(exc))
    finally:
        for fd in (dir_fd, machine_fd, root_fd):
            if fd is not None:
                _journal._close_fd_quietly(fd)


def _reconcile_records(dir_fd):
    """Every recorded reservation, validated: ({run: (model, raw)}, namespace floors, {id: run})."""
    records, floors, reserved = {}, {}, {}
    for name in sorted(os.listdir(dir_fd)):
        other = name[:-len(".toml")] if name.endswith(".toml") else None
        try:
            _opf_store.allocation_record(KIND, other)
        except ValueError:
            raise AllocationError("unexpected entry {!r} in the allocation home".format(name))
        raw, _st = _read_regular(dir_fd, name, _opf_store.MAX_STORE_READ_BYTES, name)
        model = _parse(raw, name)
        after = _validate_reservation(model, other)
        records[other] = (model, raw)
        for ns, n in after.items():
            floors[ns] = max(floors.get(ns, 0), n)
        for row in model["allocation"]:
            if row["id"] in reserved:
                raise AllocationError("id {} is reserved by both {} and {}".format(
                    row["id"], reserved[row["id"]], other))
            reserved[row["id"]] = other
    return records, floors, reserved


def _mint_ids(target, demand, live_ids, reserved):
    """Deterministic allocation in demand order from the reconciled high-water: (rows, counters)."""
    cur, rows = dict(target), []
    for key, ns in demand:
        try:
            rid, n = _opf_schema.next_id(cur, ns)
        except ValueError as exc:
            raise AllocationError(str(exc))
        cur[ns] = n
        if rid in live_ids or rid in reserved:
            raise AllocationError("minted id {} is already seated or reserved".format(rid))
        rows.append({"key": key, "id": rid})
    return rows, cur


def _reserve(machine_fd, dir_fd, run_id, rec_name, binding, demand, stamp, live_ids):
    # 1. Reconcile every recorded reservation first; an unreadable or foreign entry refuses allocation.
    records, floors, reserved = _reconcile_records(dir_fd)
    if any(n.startswith(_TMP_PREFIX) for n in os.listdir(machine_fd)):
        raise AllocationError("an interrupted counters replacement is present; recovery is required")
    # 2. Read live authority once: bounded, no-follow, single-link. A required namespace is never zero.
    prior_raw, prior_st = _read_regular(machine_fd, COUNTERS_NAME, _opf_store.MAX_STORE_READ_BYTES,
                                        COUNTERS_NAME)
    high, findings = _opf_schema.validate_counters(_parse(prior_raw, COUNTERS_NAME))
    if findings:
        raise AllocationError("counters.toml is malformed ({})".format("; ".join(findings)))
    if set(floors) - set(high):
        raise AllocationError("counters.toml lost namespace(s) {} that a reservation advanced".format(
            sorted(set(floors) - set(high))))
    target = dict((ns, max(n, floors.get(ns, 0))) for ns, n in high.items())
    for rid in live_ids:
        shape = _opf_schema._valid_id_shape(rid)
        if shape is None or shape[0] not in target or shape[1] > target[shape[0]]:
            raise AllocationError("durable id {!r} is not covered by counters.toml (a regressed or corrupt "
                                  "counter is never a licence to allocate)".format(rid))
    # 3. Reuse this run's reservation, or mint and publish a new one BEFORE advancing counters.
    existing = records.get(run_id)
    if existing is not None:
        model, raw = existing
        if model["binding"] != binding:
            raise AllocationError("run {} already reserved ids under different review/acceptance bindings; "
                                  "retain it as evidence and create a fresh run".format(run_id))
        if [(row["key"], _opf_schema._valid_id_shape(row["id"])[0]) for row in model["allocation"]] \
                != [(k, ns) for k, ns in demand]:
            raise AllocationError("run {}'s reservation does not match the verified allocation demand".format(
                run_id))
        reused = True
    else:
        rows, after = _mint_ids(target, demand, live_ids, reserved)
        model = dict(format=RESERVATION_FORMAT, schema=SCHEMA, run_id=run_id, binding=dict(binding),
                     stamp=stamp, counters_before=dict(target), counters_after=after, allocation=rows)
        raw = _emit(model)
        _validate_reservation(_parse(raw, rec_name), run_id)
        _publish_exclusive(dir_fd, rec_name, raw)   # durable: the reservation is now committed
        reused = False
    # 4. Advance counters to the componentwise maximum; never below what they were, never in place.
    for ns, n in model["counters_after"].items():
        target[ns] = max(target[ns], n)
    if _opf_schema.check_monotonic(high, target):
        raise AllocationError("counters would regress")
    if target != high:
        _replace_counters(machine_fd, prior_raw, prior_st, _emit(dict(schema=SCHEMA, counters=target)),
                          run_id)
    ids = dict((row["key"], row["id"]) for row in model["allocation"])
    return Reservation(run_id, ids, model["stamp"], _sha(raw), reused, dict(target))


# --- self-test: the direct allocation lane --------------------------------------------------------------

def self_test():
    """Allocation vectors over a synthetic inline store, ported from the retired ingest execution
    coordinator's suite (its retry-monotonicity and mint-move-toctou legs and its
    allocation/journal-lock-required discriminator), driven through the real capability and journal
    writer lock:
      capability-required   a released capability refuses before any read or write.
      journal-lock-required the capability alone refuses (the ingest journal writer lock is required too),
                            nothing reserved and counters byte-unchanged.
      mint-under-both-locks ids come from the live counters in demand order, the reservation record is
                            published in the journal home, and counters advance to exactly the minted ids.
      retry-same-allocation identical bindings reuse the SAME reservation and mint nothing.
      retry-no-regression   a counters file rolled back beneath the durable reservation is re-advanced to
                            the componentwise maximum on retry, never below it, and the ids are unchanged.
      binding-changed       a changed binding refuses and the consumed reservation stays as it was.
      reservation-floor     another run allocates above an earlier run's reservation even when counters were
                            rolled back, so a reserved number is never handed out twice.
      uncovered-live-id     a durable id above its counter refuses (a regressed counter is never a licence).
      interrupted-replace   a leftover counters temporary refuses until recovery.
    Returns 0 pass, 1 fail, 2 on a harness error."""
    import shutil
    import tempfile
    failures, count = [], [0]

    def check(name, cond):
        count[0] += 1
        if not cond:
            failures.append(name)

    def binding(run_id, digest="x"):
        return dict(run_id=run_id, review_model_digest=digest, bundle_digest="x", acceptance_digest="x")

    def refusal(cap, run_id, bind, demand, live_ids=()):
        try:
            reserve_ingest_ids(cap, run_id, bind, demand, "stamp", set(live_ids))
        except AllocationError as exc:
            return str(exc)
        return None

    run1 = "imp-20260910T120000Z-0000000000000001"
    run2 = "imp-20260910T120000Z-0000000000000002"
    demand = [("bi", "BI"), ("lf-a", "LF"), ("lf-b", "LF")]
    try:
        base = Path(tempfile.mkdtemp(prefix="opf-allocation-selftest-")).resolve()
    except OSError as exc:
        print("OPF-ALLOCATION SELF-TEST: harness error: cannot create the fixture ({})".format(exc))
        return 2
    try:
        root = base / "store"
        machine = root / _opf_store.WORKING_DIRNAME / _opf_store.DEFAULT_MACHINE_SUBDIR
        machine.mkdir(parents=True)
        (machine / _opf_store.MANIFEST_NAME).write_text("\n".join([
            "[opf]", 'standard = "opf"', 'spec_version = "{}"'.format(_opf_store.SUPPORTED_SPEC_VERSION),
            'layout = "inline"', 'posture = "required"', 'import_status = "none"', "", "[store]",
            'sync_target = ""', "", "[modules]", "governance = true", "", "[types.backlog_item]",
            'namespace = "BI"', "", "[vendors]", "registered = []", ""]) + "\n", encoding="utf-8")
        counters = machine / COUNTERS_NAME

        def set_counters(bi, lf):
            counters.write_text("schema = 1\n\n[counters]\nBI = {}\nLF = {}\nWL = 0\n".format(bi, lf),
                                encoding="utf-8")

        def high():
            return _opf_schema.validate_counters(tomllib.loads(counters.read_text(encoding="utf-8")))[0]

        def record(run_id):
            path = root / _opf_store.allocation_record(KIND, run_id)
            return path.read_bytes() if path.is_file() else None

        # Another run consumed LF numbers first: permanent ids come from the live counters.
        set_counters(0, 10)
        cap = _opf_oplock.acquire_operation(str(root), "allocation-selftest")
        released = False
        try:
            before = counters.read_bytes()
            message = refusal(cap, run1, binding(run1), demand)
            # Flip: dropping the journal-lock requirement mints BI-1 here and advances counters.
            check("journal-lock-required", message is not None and "journal writer lock" in message
                  and counters.read_bytes() == before and record(run1) is None)
            _opf_journal.acquire_writer_lock(cap, KIND)
            try:
                first = reserve_ingest_ids(cap, run1, binding(run1), demand, "stamp-1", set())
                published = record(run1)
                check("mint-under-both-locks", first.ids == {"bi": "BI-1", "lf-a": "LF-11", "lf-b": "LF-12"}
                      and first.reused is False and first.counters_after == dict(BI=1, LF=12, WL=0)
                      and high() == dict(BI=1, LF=12, WL=0))
                check("reservation-published", published is not None and first.digest == _sha(published)
                      and _validate_reservation(_parse(published, run1), run1) == dict(BI=1, LF=12, WL=0))
                # Flip: allocating anew on retry mints BI-2/LF-13.. (or refuses on the existing record).
                again = reserve_ingest_ids(cap, run1, binding(run1), demand, "stamp-2", set())
                check("retry-same-allocation", again.reused is True and again.ids == first.ids
                      and again.stamp == "stamp-1" and again.digest == first.digest
                      and record(run1) == published and high() == dict(BI=1, LF=12, WL=0))
                # A counters file rolled back beneath the durable reservation must not free its numbers.
                set_counters(0, 0)
                rolled = reserve_ingest_ids(cap, run1, binding(run1), demand, "stamp-3", set())
                check("retry-no-regression", rolled.reused is True and rolled.ids == first.ids
                      and high() == dict(BI=1, LF=12, WL=0))
                before = counters.read_bytes()
                message = refusal(cap, run1, binding(run1, digest="changed"), demand)
                check("binding-changed-refused", message is not None and "different review/acceptance" in message
                      and record(run1) == published and counters.read_bytes() == before)
                message = refusal(cap, run1, binding(run1), demand[:1])
                check("demand-changed-refused", message is not None and "allocation demand" in message
                      and record(run1) == published)
                # A later run allocates above the earlier reservation even under a rolled-back counter.
                set_counters(0, 0)
                second = reserve_ingest_ids(cap, run2, binding(run2), [("bi", "BI")], "stamp-4", set())
                check("reservation-floor", second.ids == {"bi": "BI-2"} and second.reused is False
                      and high() == dict(BI=2, LF=12, WL=0))
                message = refusal(cap, run2, binding(run2), [("bi", "BI")], live_ids=("BI-9",))
                check("uncovered-live-id-refused", message is not None and "not covered by counters" in message)
                leftover = machine / (_TMP_PREFIX + "interrupted")
                leftover.write_bytes(b"")
                message = refusal(cap, run2, binding(run2), [("bi", "BI")])
                check("interrupted-replace-refused", message is not None and "interrupted counters" in message)
                leftover.unlink()
            finally:
                _opf_journal.release_writer_lock(cap, KIND)
            _opf_oplock.release_operation(cap)
            released = True
            before = counters.read_bytes()
            message = refusal(cap, run2, binding(run2), [("bi", "BI")])
            check("capability-required", message is not None and "held store capability" in message
                  and counters.read_bytes() == before)
        finally:
            if not released:
                _opf_oplock.release_operation(cap)
    except (OSError, _opf_oplock.OpLockError, _journal.JournalError, AllocationError) as exc:
        print("OPF-ALLOCATION SELF-TEST: harness error: {!r}".format(exc))
        return 2
    finally:
        shutil.rmtree(base, ignore_errors=True)
    if failures:
        print("OPF-ALLOCATION SELF-TEST: FAIL ({} of {} checks failed)".format(len(failures), count[0]))
        for name in failures:
            print("  FAILED: {}".format(name))
        return 1
    print("OPF-ALLOCATION SELF-TEST: PASS ({} reservation checks)".format(count[0]))
    return 0


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if args == ["--self-test"]:
        return self_test()
    print("usage: _opf_allocation.py --self-test", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
