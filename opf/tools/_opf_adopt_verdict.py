#!/usr/bin/env python3
"""OPF adoption verdict scorer: one verdict for a product root, composed from the checks that already exist.

Input: the product root and nothing else (no transcript, no claim, no run id: the runs are the ones
`opf adopt status` lists). The scorer recomputes no check logic; it runs, in this fixed order:
  adopt-status    `opf adopt status --root ROOT` (the evidence bundles and the adoption journal)
  adopt-complete  _opf_adopt_complete.evaluate(ROOT, run, doctor=store_doctor(), prober=PROBER) per run,
                  graded by roster_green
  receipt-core    _opf_adopt.validate_receipt_core over the run's receipt core
  event-chain     _opf_adopt.validate_event_chain over the run's outcome events, rooted at the core digest,
                  then bound to this run (below)
  store-doctor    `opf doctor --require-store --root ROOT`
  render-check    `opf render --check --root ROOT`

The receipt core is read ONCE per run by this module and those bytes are what both receipt-core and
event-chain grade. The bundle verification below also runs ONCE per run, inside the first of those two
legs that runs, and every file this module reads for the run (receipt core, events and the digest-only
plan, approval and planning inventory) is attested against its parsed inventory rows before it is graded,
recorded as graded or digested: bytes that fail attestation are cannot-evaluate, never reported as graded
and never a finding (a swapped receipt core that would fail the schema included).
The event files are the ones the import completion evaluator accepts (_opf_import_complete._event_files):
every event-shaped name the bundle inventories list must be present, every one present must be listed,
each spelled event-<NNNN>.toml of its number, numbered contiguously from event-0001.toml; a gap, a
misspelled name or an unlisted event file is CANNOT_EVALUATE. The inventories it reconciles against are
the exact bytes _opf_adopt_apply.verify_bundle read, and a bundle that verification grades INVALID is a
finding. That verification re-reads every listed payload on its own, apart from the receipt core read
here and the event bytes read here, so the graded bytes are bound to it: the sha256 of the receipt core
bytes and of each event's bytes graded here must equal that path's row digest in those parsed
inventories, and a missing row or another digest (a file swapped between the reads) is CANNOT_EVALUATE.
The chain validator proves internal agreement only (it seeds the run from the genesis event and
never recomputes a recorded digest), so a VALID chain is then held, as in the import completion
evaluator, to every event naming this run and every recorded event_digest recomputing from the event's
own canonical bytes (_opf_adopt_apply.event_digest); either failing is a finding. Last, the latest
event's kind must be COMPLETING_EVENT (see the residuals); any other kind is CANNOT_EVALUATE.

Every leg, adopt-status included, runs under its own deadline (LEG_DEADLINE_SECONDS); a leg past it, a
leg raising any BaseException (a SystemExit from opf.main included), and a CLI leg returning anything but
exactly the int 0 or 1 are CANNOT_EVALUATE. Any exception outside a leg ends main() at exit 2.

Verdict (exactly one, printed as canonical JSON with the deciding leg, the sha256 of each bundle file
the scorer itself read with the legs that graded those exact bytes (an empty graded_by marks a file read
only for its digest, which no leg grades), and in unread_inputs each digest-only file that is absent (a
finding), could not be read, or whose bytes the bundle inventory does not attest (cannot-evaluate), with
its outcome and why):
  NOT_ADOPTED        adopt-status is clean and lists no adoption run (exit 1)
  INVALID            some leg reports a finding; the first such leg in the order above decides; with no
                     leg finding, an absent digest-only input is INVALID decided by `unread-inputs` (exit 1)
  CANNOT_EVALUATE    some leg could not be read or run, or reported cannot-evaluate; the first such leg
                     decides, never a pass; with every leg passing, any other unread_inputs row is
                     CANNOT_EVALUATE decided by `unread-inputs` (exit 2)
  ADOPTED_AND_VALID  every leg passes for every listed run and every input was read (exit 0)

A finding outranks a cannot-evaluate, since a finding is a definite failure; both are non-pass.

PROBER: the shipped pack carries no live probe adapter (_opf_adopt_complete.PR6_DENY_PROBES_SHIPPED), so
PROBER is None and the completion roster's wiring check is CANNOT-EVALUATE; the self-test injects the
completion module's fixture surface to grade the composition.

NOT YET PROVEN AGAINST A REAL ADOPTED STORE: no adoption can complete on main yet (no live probe adapter
ships, and the completion fixture's store holds only its manifest, so the real store doctor and render
check cannot evaluate over it). The ADOPTED_AND_VALID and view-drift self-test vectors therefore stub the
store-doctor and render-check results: they grade how the scorer composes the legs, not that a real
adopted store scores ADOPTED_AND_VALID or that a real render detects view drift.

DISCLOSED RESIDUALS: the input digests cover the bundle files this module reads (receipt, outcome events
and the bundle-root inventories, graded; plan, approval and planning inventory, digest only, graded by no
leg here, each held to its inventory row, and an unread or unattested one fails the verdict closed); the
files the composed legs read on their own are not digested here. A cannot-evaluate event listing (a gap,
or a misspelled, unlisted or listed-but-absent event file) ends the event-chain leg before any event is
read, so a finding in another event of that run (a forged event_digest, a foreign run, a broken chain) is
not reported: such a run scores CANNOT_EVALUATE, not INVALID (still never a pass). The legs run in this process, one after another, so a tree changed between legs is
not detected. OPF-SPEC section 14 requires an outcome-event chain but does not say which outcome kind
completes an adoption, and the shipped apply mints only the genesis `applied`; so COMPLETING_EVENT is
`applied`, and a chain whose latest event is any other kind (a later `failed`, and equally a later
`signed-off`) is CANNOT_EVALUATE until the spec decides. The chain validator allows `applied` only at the
genesis position, so no chain longer than one event can score ADOPTED_AND_VALID. OPF-SPEC section 14.1
says of import completion: "It MUST record the source's result in the adoption receipt's outcome-event
chain." So every product root with a post-adoption outcome event, a completed import included, scores
CANNOT_EVALUATE (or INVALID, for a second `applied`) until the spec names a completing kind.
A leg deadline is a SIGALRM interval timer, so it holds only on the main thread of a platform with
setitimer (elsewhere every leg is CANNOT_EVALUATE); the alarm raises inside the leg and repeats each
second until the leg yields, so a leg that swallows it every time is not stopped, though its result is
still CANNOT_EVALUATE. The deadlines are sized from the self-test fixture's run times, not a real store's.

Offline, stdlib only. It imports ONLY sibling `opf/tools/` modules.
Exit convention: 0 ADOPTED_AND_VALID, 1 NOT_ADOPTED or INVALID, 2 CANNOT_EVALUATE.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: _opf_adopt_verdict.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import contextlib
import hashlib
import io
import json
import os
import re
import signal
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _opf_adopt as schema              # noqa: E402
import _opf_adopt_apply as apply         # noqa: E402
import _opf_adopt_complete as complete   # noqa: E402
import _opf_import_complete as importc   # noqa: E402
import _opf_store as store               # noqa: E402

ADOPTED_AND_VALID, NOT_ADOPTED, INVALID, CANNOT_EVALUATE = (
    "ADOPTED_AND_VALID", "NOT_ADOPTED", "INVALID", "CANNOT_EVALUATE")
VERDICTS = (ADOPTED_AND_VALID, NOT_ADOPTED, INVALID, CANNOT_EVALUATE)
EXIT_CODES = dict([(ADOPTED_AND_VALID, 0), (NOT_ADOPTED, 1), (INVALID, 1), (CANNOT_EVALUATE, 2)])
LEGS = ("adopt-status", "adopt-complete", "receipt-core", "event-chain", "store-doctor", "render-check")
# Leg outcomes: a leg passes, finds, or cannot be evaluated.
PASS, FINDING, CANNOT = "pass", "finding", "cannot-evaluate"
# The wiring prober handed to the completion roster: none ships (see the module docstring).
PROBER = None
# The outcome kind the latest event must carry for the run to count as adopted: the genesis `applied`, the
# only kind the shipped apply mints (OPF-SPEC section 14 names no completing kind; module docstring). As
# `applied` is genesis-only, any post-adoption event, an import's included, is never ADOPTED_AND_VALID.
COMPLETING_EVENT = "applied"
# The OPF-SPEC section 14.1 sentence the residual cites: import completion appends to the chain.
SPEC_IMPORT_RECORDS_EVENT = ("It MUST record the source's result in the adoption receipt's outcome-event "
                             "chain.")
# Each leg's deadline in seconds, well above its run time over the self-test fixture (each leg under 2 s
# there); a leg past it is cannot-evaluate.
# The receipt-core leg runs the run's bundle verification first, so its deadline matches event-chain's.
LEG_DEADLINE_SECONDS = {"adopt-status": 120, "adopt-complete": 300, "receipt-core": 300, "event-chain": 300,
                        "store-doctor": 300, "render-check": 300}
_STATUS_RUN = re.compile(r"opf adopt status: adoption run (\S+): evidence bundle at ")


class _Leg(Exception):
    """A leg outcome that ends that leg early."""

    def __init__(self, outcome, reason):
        super().__init__(reason)
        self.outcome, self.reason = outcome, reason


class _Expired(BaseException):
    """Raised by the deadline alarm inside a leg that ran past its deadline."""


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def _opf_cli(argv):
    """(exit code, stdout, stderr) of one in-process `opf` CLI run."""
    import opf
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = opf.main(argv)
    return rc, out.getvalue(), err.getvalue()


def _cli_outcome(rc):
    """opf.py's _main_exit contract: only exactly the int 0 or 1 is a pass or a finding, so False, 0.0 and
    an int subclass are cannot-evaluate."""
    if type(rc) is not int:
        return CANNOT
    return {0: PASS, 1: FINDING}.get(rc, CANNOT)


def _leg_status(root):
    rc, out, err = _opf_cli(["adopt", "status", "--root", root])
    runs = [m.group(1) for m in map(_STATUS_RUN.match, out.splitlines()) if m is not None]
    return _cli_outcome(rc), (out + err).strip(), runs


def _read_bundle(root_fd, rel):
    """The live bytes at `rel`; absence raises a finding, a read refusal cannot."""
    try:
        _fst, data = apply._read_live(root_fd, rel)
    except apply.AdoptApplyError as exc:
        raise _Leg(CANNOT, "{}: {}".format(rel, exc))
    if data is None:
        raise _Leg(FINDING, "{} is absent".format(rel))
    return data


def _digested(read, rel, data, leg=None):
    """Record the sha256 of the exact bytes `data` read at `rel`, and `leg` among the legs that graded them.
    Bytes at one path that differ between two reads are cannot-evaluate, never two digests."""
    entry = read.setdefault(rel, dict(sha256=_sha256(data), graded_by=[]))
    if entry["sha256"] != _sha256(data):
        raise _Leg(CANNOT, "{} changed between two reads".format(rel))
    if leg is not None and leg not in entry["graded_by"]:
        entry["graded_by"].append(leg)
    return data


def _receipt_core(core):
    """The receipt core bytes read once for this run, or the outcome that read ended in."""
    if isinstance(core, _Leg):
        raise _Leg(core.outcome, core.reason)
    return core


def _parsed(data, label):
    try:
        return apply._canonical_toml(data, label)
    except apply.AdoptApplyError as exc:
        raise _Leg(CANNOT, str(exc))


def _graded(status, findings, label):
    if status == store.VALID:
        return PASS, label + ": VALID"
    return (FINDING if status == store.INVALID else CANNOT), "{}: {}: {}".format(label, status, "; ".join(findings))


def _leg_complete(root, run_id):
    results = complete.evaluate(root, run_id, doctor=complete.store_doctor(), prober=PROBER)
    if complete.roster_green(results):
        return PASS, "roster green"
    bad = [(name, r) for name, r in results.items() if r.status != store.VALID]
    outcome = FINDING if any(r.status == store.INVALID for _n, r in bad) else CANNOT
    return outcome, "; ".join("{}: {}: {}".format(name, r.status, "; ".join(r.findings)) for name, r in bad)


def _leg_receipt(root, core, run_id, read, bundle_memo):
    """The receipt core graded only once the bundle inventory attests its bytes: bytes swapped after the
    bundle verification are cannot-evaluate, never graded and never a finding."""
    core = _receipt_core(core)
    verified = _bundle_once(root, run_id, read, bundle_memo)
    if isinstance(verified, _Leg):
        raise _Leg(CANNOT, "the receipt core bytes cannot be attested: {}".format(verified.reason))
    _attested(verified[1], apply.receipt_rel(run_id), core)
    receipt = _parsed(core, "the adoption receipt core")
    _digested(read, apply.receipt_rel(run_id), core, "receipt-core")
    checked = schema.validate_receipt_core(receipt)
    return _graded(checked.status, checked.findings, "receipt core")


def _bundle_once(root, run_id, read, memo):
    """_verified_bundle's result for this run, or the _Leg it raised, computed once and shared by the legs
    that attest bytes against it (`memo` holds it per run)."""
    if "bundle" not in memo:
        memo["bundle"] = _attempt(lambda: _verified_bundle(root, run_id, read))
    return memo["bundle"]


def _claim(rows, path, sha256):
    """Record one inventory row; a path two rows claim is cannot-evaluate (verify_bundle refuses it first,
    and this guard keeps the attestation map single-valued should that ever change)."""
    if path in rows:
        raise _Leg(CANNOT, "{} is claimed by two bundle inventory rows".format(path))
    rows[path] = sha256


def _verified_bundle(root, run_id, read):
    """((verification, parsed inventories), {path: row sha256}) of the run's bundle: the bundle-root
    inventories are parsed from the exact bytes the bundle verification read (each digested as graded
    here). A bundle the verification grades INVALID is a finding; an inventory that cannot evaluate, a
    verification that cannot and a path two rows claim are cannot-evaluate."""
    raw = {}
    try:
        verified = apply.verify_bundle(root, run_id, inventory_bytes=raw)
    except apply.AdoptApplyError as exc:
        raise _Leg(CANNOT, "adoption evidence bundle: {}".format(exc))
    for rel in sorted(raw):
        _digested(read, rel, raw[rel], "event-chain")
    if verified.status != store.VALID:
        raise _Leg(FINDING if verified.status == store.INVALID else CANNOT,
                   "adoption evidence bundle: {}: {}".format(verified.status, "; ".join(verified.findings)))
    docs, rows = {}, {}
    for rel in sorted(raw):
        doc = _parsed(raw[rel], "the adoption bundle inventory " + rel)
        checked = apply.validate_inventory(doc, run_id)
        if checked.status == store.CANNOT_EVALUATE:
            raise _Leg(CANNOT, "the adoption bundle inventory {}: {}".format(rel, "; ".join(checked.findings)))
        docs[rel] = doc if checked.status == store.VALID else None
        for row in doc["file"] if docs[rel] is not None else ():
            _claim(rows, row["path"], row["sha256"])
    return (verified, docs), rows


def _event_paths(root_fd, run_id, bundle):
    """The run's outcome-event paths in chain order by the import completion evaluator's own rule
    (_opf_import_complete._event_files), reconciled against the verified bundle's parsed inventories. A
    gap and a misspelled, unlisted or listed-but-absent event file are cannot-evaluate."""
    ev = importc._Evaluation()
    ev.root_fd, ev.run_id, ev.bundle = root_fd, run_id, bundle
    try:
        return importc._event_files(ev, apply.evidence_home_rel(run_id))
    except importc.Unevaluable as exc:
        raise _Leg(CANNOT, str(exc))


def _attested(rows, rel, data):
    """`data`, the bytes graded here at `rel`, when the bundle inventory row the verification checked its
    own read against carries their sha256. The verification reads every payload apart from this module's
    reads, so a missing row or another digest (bytes swapped between the reads) is cannot-evaluate."""
    if rel not in rows:
        raise _Leg(CANNOT, "{} is listed by no bundle inventory, so the bytes graded here are not "
                           "attested".format(rel))
    if rows[rel] != _sha256(data):
        raise _Leg(CANNOT, "{}: the bytes graded here (sha256 {}) are not the bytes the bundle inventory "
                           "attests (sha256 {}); the file changed between the bundle verification and this "
                           "read".format(rel, _sha256(data), rows[rel]))
    return data


def _chain_unbound(events, run_id):
    """The findings binding a VALID chain to THIS run (the import completion evaluator's rule): the chain
    validator seeds the run from the genesis event and never recomputes a recorded digest, so every event
    must name this run and every recorded event_digest must be the digest of the event's own canonical
    bytes. A digest that cannot be computed is cannot-evaluate."""
    findings = []
    for i, event in enumerate(events):
        try:
            recomputed = apply.event_digest(event)
        except apply.AdoptApplyError as exc:
            raise _Leg(CANNOT, "outcome-event chain: event[{}]: {}".format(i, exc))
        if event["event_digest"] != recomputed:
            findings.append("event[{}] records an event_digest that is not the digest of its own canonical "
                            "bytes".format(i))
        if event["run_id"] != run_id:
            findings.append("event[{}] names run {!r}, not this run".format(i, event["run_id"]))
    return findings


def _leg_events(root, root_fd, run_id, core, read, bundle_memo):
    core = _receipt_core(core)
    verified = _bundle_once(root, run_id, read, bundle_memo)
    if isinstance(verified, _Leg):
        raise _Leg(verified.outcome, verified.reason)
    bundle, rows = verified
    events = []
    for rel in _event_paths(root_fd, run_id, bundle):
        try:
            _fst, data = apply._read_live(root_fd, rel)
        except apply.AdoptApplyError as exc:
            raise _Leg(CANNOT, "{}: {}".format(rel, exc))
        if data is None:
            raise _Leg(CANNOT, "outcome event {} vanished after the bundle listing".format(rel))
        # Attested before it is recorded, so bytes this leg refuses are never reported as graded.
        data = _digested(read, rel, _attested(rows, rel, data), "event-chain")
        events.append(_parsed(data, "outcome event " + rel))
    _digested(read, apply.receipt_rel(run_id), _attested(rows, apply.receipt_rel(run_id), core), "event-chain")
    chain = schema.validate_event_chain(events, "sha256:" + _sha256(core))
    if chain.status != store.VALID:
        return _graded(chain.status, chain.findings, "outcome-event chain")
    unbound = _chain_unbound(events, run_id)
    if unbound:
        return FINDING, "outcome-event chain: " + "; ".join(unbound)
    latest = events[-1]["kind"]
    if latest != COMPLETING_EVENT:
        return CANNOT, ("outcome-event chain: the latest outcome is {!r}, not {!r}; OPF-SPEC section 14 does "
                        "not say which outcome completes an adoption".format(latest, COMPLETING_EVENT))
    return PASS, "outcome-event chain: VALID, bound to this run, latest outcome {!r}".format(latest)


def _leg_cli(argv):
    rc, out, err = _opf_cli(argv)
    return _cli_outcome(rc), "exit {!r}: {}".format(rc, (out + err).strip())


def _bounded(name, call):
    """call() under the leg's deadline (LEG_DEADLINE_SECONDS[name]); a leg past it raises _Leg cannot, even
    when the leg caught the alarm and returned. The alarm repeats each second until the leg yields."""
    seconds = LEG_DEADLINE_SECONDS[name]
    if not hasattr(signal, "setitimer") or threading.current_thread() is not threading.main_thread():
        raise _Leg(CANNOT, "no leg deadline can be enforced here (a SIGALRM timer needs the main thread)")
    expired = []

    def alarm(_signum, _frame):
        expired.append(True)
        raise _Expired()

    previous = signal.signal(signal.SIGALRM, alarm)
    outer = (0.0, 0.0)
    try:
        try:
            try:
                outer = signal.setitimer(signal.ITIMER_REAL, seconds, 1.0)
                result = call()
            finally:
                signal.setitimer(signal.ITIMER_REAL, 0)
        except BaseException:
            if expired:
                raise _Leg(CANNOT, "the leg ran past its {} s deadline".format(seconds))
            raise
    finally:
        signal.signal(signal.SIGALRM, signal.SIG_DFL if previous is None else previous)
        if outer[0] > 0:
            signal.setitimer(signal.ITIMER_REAL, *outer)
    if expired:
        raise _Leg(CANNOT, "the leg ran past its {} s deadline".format(seconds))
    return result


def _run_leg(name, legs, call):
    try:
        outcome, reason = _bounded(name, call)
    except _Leg as exc:
        outcome, reason = exc.outcome, exc.reason
    except BaseException as exc:  # noqa: BLE001  a leg that cannot run (SystemExit included) is never a pass
        outcome, reason = CANNOT, "the leg could not run ({!r})".format(exc)
    legs.append(dict(leg=name, outcome=outcome, reason=reason))


def _attempt(call):
    """call()'s result, or the _Leg it raised."""
    try:
        return call()
    except _Leg as exc:
        return exc


def _digest_only(root_fd, rel, read, unread, verified):
    """Digest `rel` with no grading leg once the run's verified bundle inventory (`verified`, from
    _bundle_once) attests the bytes read, or report it in `unread` with its outcome and why: an absent file
    is a finding; a read refusal, a bundle that did not verify, a missing row and another digest (a file
    swapped after the bundle verification) are cannot-evaluate. Every such input is required (_decide)."""
    data = _attempt(lambda: _read_bundle(root_fd, rel))
    if not isinstance(data, _Leg):
        if isinstance(verified, _Leg):
            data = _Leg(CANNOT, "{}: the bytes read here cannot be attested: {}".format(rel, verified.reason))
        else:
            data = _attempt(lambda: _attested(verified[1], rel, data))
    if isinstance(data, _Leg):
        unread.append(dict(path=rel, outcome=data.outcome, reason=data.reason))
    else:
        _digested(read, rel, data)


def score(product_root):
    """The verdict document for one product root (see the module docstring). Read-only."""
    root = os.path.abspath(str(product_root))
    legs, read, unread = [], {}, []
    try:
        status_outcome, status_reason, runs = _bounded("adopt-status", lambda: _leg_status(root))
    except _Leg as exc:
        status_outcome, status_reason, runs = exc.outcome, exc.reason, []
    except BaseException as exc:  # noqa: BLE001  fail closed
        status_outcome, status_reason, runs = CANNOT, "the leg could not run ({!r})".format(exc), []
    legs.append(dict(leg="adopt-status", outcome=status_outcome, reason=status_reason))
    if status_outcome == PASS and not runs:
        return _verdict(NOT_ADOPTED, "adopt-status", legs, read, unread)
    root_fd = None
    try:
        root_fd = apply._open_product_root(root)
    except BaseException as exc:  # noqa: BLE001  fail closed
        legs.append(dict(leg="adopt-complete", outcome=CANNOT, reason="cannot open the product root ({!r})".format(exc)))
    try:
        for run_id in runs if root_fd is not None else ():
            _run_leg("adopt-complete", legs, lambda: _leg_complete(root, run_id))
            # One read of the receipt core: both legs grade these bytes, and their digest is reported.
            core = _attempt(lambda: _read_bundle(root_fd, apply.receipt_rel(run_id)))
            # One bundle verification per run: every file read here is attested against its rows.
            bundle_memo = {}
            if "receipt-core" in LEGS:
                _run_leg("receipt-core", legs, lambda: _leg_receipt(root, core, run_id, read, bundle_memo))
            if "event-chain" in LEGS:
                _run_leg("event-chain", legs, lambda: _leg_events(root, root_fd, run_id, core, read, bundle_memo))
            # Read for their digests only; no leg here grades them (graded_by stays empty).
            verified = bundle_memo.get("bundle", _Leg(CANNOT, "the bundle verification did not complete"))
            for rel in (apply.plan_rel(run_id), apply.approval_rel(run_id), apply.planning_inventory_rel(run_id)):
                _digest_only(root_fd, rel, read, unread, verified)
    finally:
        if root_fd is not None:
            store._close_fd_exc_safe(root_fd)
    _run_leg("store-doctor", legs, lambda: _leg_cli(["doctor", "--require-store", "--root", root]))
    if "render-check" in LEGS:
        _run_leg("render-check", legs, lambda: _leg_cli(["render", "--check", "--root", root]))
    return _verdict(*_decide(legs, unread), legs, read, unread)


def _decide(legs, unread):
    """(verdict, decided_by): the first finding leg, else an unread required input that is a finding (an
    absent file), else the first cannot-evaluate leg, else any other unread required input (fail closed),
    else ADOPTED_AND_VALID."""
    for outcome, verdict in ((FINDING, INVALID), (CANNOT, CANNOT_EVALUATE)):
        for leg in legs:
            if leg["outcome"] == outcome:
                return verdict, leg["leg"]
        if any(row["outcome"] == outcome for row in unread):
            return verdict, "unread-inputs"
    if unread:
        return CANNOT_EVALUATE, "unread-inputs"
    return ADOPTED_AND_VALID, "all-legs"


def _verdict(verdict, decided_by, legs, read, unread):
    return dict(verdict=verdict, decided_by=decided_by, legs=legs,
                inputs=[dict(path=rel, sha256=read[rel]["sha256"], graded_by=sorted(read[rel]["graded_by"]))
                        for rel in sorted(read)],
                unread_inputs=sorted(unread, key=lambda row: row["path"]))


def canonical(doc):
    return json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n"


# --- self-test ----------------------------------------------------------------------------------------

def _put(root, rel, data):
    path = os.path.join(root, *rel.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as out:
        out.write(data)


def _get(root, rel):
    with open(os.path.join(root, *rel.split("/")), "rb") as fh:
        return fh.read()


def _full_fixture(root):
    """The completion module's post-apply fixture plus the run's genesis `applied` outcome event chained
    from the receipt core digest, listed by the bundle inventory."""
    import tomllib
    from _opf_emit import emit_checked
    run = complete._RUN
    complete._fixture(root)
    core = _get(root, apply.receipt_rel(run))
    plan = tomllib.loads(_get(root, apply.plan_rel(run)).decode("utf-8"))
    event = dict(format=schema.EVENT_FORMAT, schema=schema.SCHEMA_VERSION, kind="applied", run_id=run,
                 revision=plan["revision"], recorded_at="2026-09-17T12:45:00Z",
                 previous_event_digest="sha256:" + _sha256(core))
    event["event_digest"] = apply.event_digest(event)
    _put(root, apply.genesis_event_rel(run), emit_checked(event).encode("utf-8"))
    _reinventory(root, extra=(apply.genesis_event_rel(run),))


def _reinventory(root, extra=()):
    import tomllib
    run = complete._RUN
    doc = tomllib.loads(_get(root, apply.inventory_rel(run)).decode("utf-8"))
    paths = [row["path"] for row in doc["file"]] + [p for p in extra if p not in [r["path"] for r in doc["file"]]]
    _put(root, apply.inventory_rel(run), apply.emit_inventory(
        run, [apply.inventory_row(p, _get(root, p)) for p in paths], plan_digest=doc["adoption"]["plan_digest"]))


def _break_chain(root):
    import tomllib
    from _opf_emit import emit_checked
    rel = apply.genesis_event_rel(complete._RUN)
    event = tomllib.loads(_get(root, rel).decode("utf-8"))
    event["previous_event_digest"] = "sha256:" + "0" * 64
    event["event_digest"] = apply.event_digest(event)
    _put(root, rel, emit_checked(event).encode("utf-8"))
    _reinventory(root)


def _full_fixture_then(mutate):
    return lambda root: (_full_fixture(root), mutate(root))


def _genesis(root):
    import tomllib
    return tomllib.loads(_get(root, apply.genesis_event_rel(complete._RUN)).decode("utf-8"))


def _put_event(root, rel, event, mint=True):
    """Write `event` at `rel`, its event_digest minted from its own bytes unless `mint` is false."""
    from _opf_emit import emit_checked
    if mint:
        event["event_digest"] = apply.event_digest(event)
    _put(root, rel, emit_checked(event).encode("utf-8"))


def _event_rel(name):
    return apply.evidence_home_rel(complete._RUN) + "/" + name


def _forged_digest(root):
    event = _genesis(root)
    event["event_digest"] = "sha256:" + "9" * 64
    _put_event(root, apply.genesis_event_rel(complete._RUN), event, mint=False)
    _reinventory(root)


def _foreign_run(root):
    event = _genesis(root)
    event["run_id"] = complete._RUN[:-1] + ("0" if complete._RUN[-1] != "0" else "1")
    _put_event(root, apply.genesis_event_rel(complete._RUN), event)
    _reinventory(root)


def _failed_event(genesis):
    return dict(genesis, kind="failed", recorded_at="2026-09-17T13:00:00Z",
                previous_event_digest=genesis["event_digest"])


def _latest_failed(root):
    _put_event(root, _event_rel("event-0002.toml"), _failed_event(_genesis(root)))
    _reinventory(root, extra=(_event_rel("event-0002.toml"),))


def _gap(root):
    _put(root, _event_rel("event-0003.toml"), b"x = [\n")
    _reinventory(root, extra=(_event_rel("event-0003.toml"),))


def _misspelled(root):
    _put_event(root, _event_rel("event-2.toml"), _failed_event(_genesis(root)))
    _reinventory(root, extra=(_event_rel("event-2.toml"),))


def _unlisted(root):
    _put_event(root, _event_rel("event-0002.toml"), _failed_event(_genesis(root)))


def _receipt_unlisted(root):
    import tomllib
    run = complete._RUN
    doc = tomllib.loads(_get(root, apply.inventory_rel(run)).decode("utf-8"))
    _put(root, apply.inventory_rel(run), apply.emit_inventory(
        run, [row for row in doc["file"] if row["path"] != apply.receipt_rel(run)],
        plan_digest=doc["adoption"]["plan_digest"]))


def _second_event(kind):
    def mutate(root):
        genesis = _genesis(root)
        _put_event(root, _event_rel("event-0002.toml"), dict(
            genesis, kind=kind, recorded_at="2026-09-17T13:00:00Z", previous_event_digest=genesis["event_digest"]))
        _reinventory(root, extra=(_event_rel("event-0002.toml"),))
    return mutate


SPEC_PATH = Path(__file__).resolve().parent.parent / "spec" / "OPF-SPEC.md"


def _spec_section(text, start, end):
    """The whitespace-flattened spec text from the line opening with `start` to the next opening with
    `end`; empty when `start` heads no line."""
    lines = text.splitlines()
    begin = next((i for i, line in enumerate(lines) if line.startswith(start)), None)
    if begin is None:
        return ""
    stop = next((i for i in range(begin + 1, len(lines)) if lines[i].startswith(end)), len(lines))
    return " ".join(" ".join(lines[begin:stop]).split())


def self_test():
    """Fixtures under TemporaryDirectory. The completion fixture's store holds only its manifest, so the
    real store doctor and render engine are cannot-evaluate over it: the vectors that need those legs green
    stub the doctor verdict and the render outcome, and say so in their ids. Exit 0 all pass, 1 a failed
    check, 2 a harness error."""
    _child_status_refusal_exit("_opf_adopt_verdict.py")
    import inspect
    from unittest import mock
    here = sys.modules[__name__]
    results = []

    def check(name, ok):
        results.append((name, bool(ok)))

    def case(prepare=None, patches=(), call=None):
        with tempfile.TemporaryDirectory(prefix="opf-adopt-verdict-") as root:
            if prepare is not None:
                prepare(root)
            with contextlib.ExitStack() as stack:
                for target, attr, value in patches:
                    stack.enter_context(mock.patch.object(target, attr, value))
                return (call or score)(root)

    import opf
    real_cli = _opf_cli

    def stub_cli(render_rc):
        def run(argv):
            if argv[0] == "doctor":
                return 0, "opf doctor: store integrity: VALID (stubbed)", ""
            if argv[0] == "render":
                return render_rc, "opf render: stubbed exit {}".format(render_rc), ""
            return real_cli(argv)
        return run

    green_doctor = (complete, "store_doctor", lambda observations=None: (lambda root: complete._Doctor()))
    switches = [(complete, "PR6_DENY_PROBES_SHIPPED", True), (complete, "U25_CI_IDENTITY_ASSERTED", True),
                (here, "PROBER", complete._Surface()), green_doctor]

    try:
        check("signature-takes-product-root-only",
              list(inspect.signature(score).parameters) == ["product_root"])
        bare = case()
        check("bare-tree-not-adopted", bare["verdict"] == NOT_ADOPTED and bare["decided_by"] == "adopt-status")
        main_full = case(_full_fixture, [(here, "PROBER", complete._Surface())])
        reasons = " ".join(leg["reason"] for leg in main_full["legs"])
        check("main-full-fixture-cannot-naming-deny-probe-switch",
              main_full["verdict"] == CANNOT_EVALUATE and "(PR6)" in reasons
              and not complete.PR6_DENY_PROBES_SHIPPED)
        check("main-real-legs-pass-status-receipt-chain",
              all(leg["outcome"] == PASS for leg in main_full["legs"]
                  if leg["leg"] in ("adopt-status", "receipt-core", "event-chain")))
        green = case(_full_fixture, switches + [(here, "_opf_cli", stub_cli(0))])
        check("switches-on-stubbed-doctor-render-adopted-and-valid",
              green["verdict"] == ADOPTED_AND_VALID and green["decided_by"] == "all-legs"
              and [leg["leg"] for leg in green["legs"]] == list(LEGS))
        check("inputs-digested", any(i["path"] == apply.genesis_event_rel(complete._RUN) for i in green["inputs"]))
        broken = case(lambda r: (_full_fixture(r), _break_chain(r)), switches + [(here, "_opf_cli", stub_cli(0))])
        check("broken-event-chain-non-pass", broken["verdict"] == INVALID and broken["decided_by"] == "event-chain")
        drift = case(_full_fixture, switches + [(here, "_opf_cli", stub_cli(1))])
        check("view-drift-stubbed-render-non-pass", drift["verdict"] == INVALID
              and drift["decided_by"] == "render-check")
        # Flips: each leg dropped turns its vector green, so each vector is red only through its own leg.
        without_chain = tuple(leg for leg in LEGS if leg != "event-chain")
        flip_chain = case(lambda r: (_full_fixture(r), _break_chain(r)),
                          switches + [(here, "_opf_cli", stub_cli(0)), (here, "LEGS", without_chain)])
        check("flip-drop-event-chain-leg-turns-green", flip_chain["verdict"] == ADOPTED_AND_VALID)
        without_render = tuple(leg for leg in LEGS if leg != "render-check")
        flip_render = case(_full_fixture,
                           switches + [(here, "_opf_cli", stub_cli(1)), (here, "LEGS", without_render)])
        check("flip-drop-render-check-leg-turns-green", flip_render["verdict"] == ADOPTED_AND_VALID)
        raising = case(_full_fixture, switches + [(here, "_opf_cli", stub_cli(0)),
                                                  (complete, "evaluate", mock.Mock(side_effect=OSError("x")))])
        check("leg-that-cannot-run-is-cannot-naming-it", raising["verdict"] == CANNOT_EVALUATE
              and raising["decided_by"] == "adopt-complete")
        check("exit-codes", [EXIT_CODES[v] for v in VERDICTS] == [0, 1, 1, 2])
        check("canonical-json-stable", canonical(green) == canonical(json.loads(canonical(green))))
        check("opf-cli-is-composed", callable(opf.main))
        on = switches + [(here, "_opf_cli", stub_cli(0))]

        def decided(doc, verdict, leg):
            return doc["verdict"] == verdict and doc["decided_by"] == leg

        # Each vector below is red only through its own guard: its flip patches that guard out (or back to
        # the reading it replaced) and the same tree then scores the pre-guard result.
        no_binding = (here, "_chain_unbound", lambda events, run_id: [])
        check("forged-event-digest-finding", decided(case(_full_fixture_then(_forged_digest), on),
                                                     INVALID, "event-chain"))
        check("flip-forged-event-digest-unbound-turns-green",
              case(_full_fixture_then(_forged_digest), on + [no_binding])["verdict"] == ADOPTED_AND_VALID)
        check("foreign-run-genesis-finding", decided(case(_full_fixture_then(_foreign_run), on),
                                                     INVALID, "event-chain"))
        check("flip-foreign-run-unbound-turns-green",
              case(_full_fixture_then(_foreign_run), on + [no_binding])["verdict"] == ADOPTED_AND_VALID)

        def probe_events(root_fd, run_id, bundle):
            # The reading this guard replaced: probe event-0001, event-0002, ... and stop at the first absent.
            out = []
            for number in range(1, importc.MAX_EVENTS + 1):
                rel = apply.evidence_home_rel(run_id) + "/" + importc.EVENT_NAME.format(number)
                if apply._read_live(root_fd, rel)[1] is None:
                    break
                out.append(rel)
            return out

        probing = (here, "_event_paths", probe_events)
        for label, prepare in (("gap-unparseable-event", _gap), ("misspelled-event", _misspelled)):
            check(label + "-cannot", decided(case(_full_fixture_then(prepare), on), CANNOT_EVALUATE, "event-chain"))
            check("flip-" + label + "-probe-turns-green",
                  case(_full_fixture_then(prepare), on + [probing])["verdict"] == ADOPTED_AND_VALID)
        unlisted = case(_full_fixture_then(_unlisted), on)
        check("unlisted-event-cannot", decided(unlisted, CANNOT_EVALUATE, "event-chain")
              and "no bundle inventory lists it" in unlisted["legs"][-3]["reason"])
        check("latest-failed-outcome-cannot", decided(case(_full_fixture_then(_latest_failed), on),
                                                      CANNOT_EVALUATE, "event-chain"))
        check("flip-latest-failed-as-completing-turns-green",
              case(_full_fixture_then(_latest_failed), on + [(here, "COMPLETING_EVENT", "failed")])["verdict"]
              == ADOPTED_AND_VALID)
        # No chain longer than one event scores ADOPTED_AND_VALID, whatever its second kind (an import's
        # recorded result included), and the residual says so citing the spec sentence that requires it.
        second = dict((kind, case(_full_fixture_then(_second_event(kind)), on)) for kind in schema.OUTCOME_EVENTS)
        check("every-post-adoption-event-non-pass", all(
            decided(doc, INVALID if kind == "applied" else CANNOT_EVALUATE, "event-chain")
            for kind, doc in second.items()))
        flat_doc = " ".join((__doc__ or "").split())
        spec_14_1 = _spec_section(SPEC_PATH.read_text(encoding="utf-8"), "### 14.1 ", "### 14.2 ")
        check("spec-14-1-carries-import-event-sentence", SPEC_IMPORT_RECORDS_EVENT in spec_14_1)
        check("spec-section-read-is-scoped-to-its-section", SPEC_IMPORT_RECORDS_EVENT not in _spec_section(
            SPEC_PATH.read_text(encoding="utf-8"), "### 14.2 ", "### 14.3 "))
        check("residual-cites-spec-import-event-and-cannot",
              '"' + SPEC_IMPORT_RECORDS_EVENT + '"' in flat_doc
              and "every product root with a post-adoption outcome event, a completed import included, scores "
                  "CANNOT_EVALUATE" in flat_doc)

        # The receipt core is read once; a second read would see other bytes.
        receipt_rel = apply.receipt_rel(complete._RUN)
        real_read = apply._read_live
        seen = dict(reads=0, fd=None, disk=None)

        def counting_read(root_fd, rel):
            # Only this module's own reads count: the completion leg reads the receipt on its own.
            fst, data = real_read(root_fd, rel)
            if rel == receipt_rel and sys._getframe(1).f_globals is vars(here):
                seen["reads"], seen["fd"] = seen["reads"] + 1, root_fd
                if seen["reads"] == 1:
                    seen["disk"] = data
                else:
                    data += b"\n"
            return fst, data

        def once(patches):
            seen.update(reads=0, fd=None, disk=None)
            doc = case(_full_fixture, on + [(apply, "_read_live", counting_read)] + patches)
            row = [i for i in doc["inputs"] if i["path"] == receipt_rel]
            return (seen["reads"] == 1 and doc["verdict"] == ADOPTED_AND_VALID and len(row) == 1
                    and row[0]["sha256"] == _sha256(seen["disk"] or b"")
                    and row[0]["graded_by"] == ["event-chain", "receipt-core"])

        check("receipt-read-once-digest-is-graded-bytes", once([]))
        check("flip-receipt-reread-per-leg-red", not once(
            [(here, "_receipt_core", lambda core: _read_bundle(seen["fd"], receipt_rel))]))
        check("digest-only-files-reported-not-graded", all(
            i["graded_by"] == [] for i in green["inputs"] if i["path"] in (
                apply.plan_rel(complete._RUN), apply.approval_rel(complete._RUN),
                apply.planning_inventory_rel(complete._RUN))) and any(
            i["path"] == apply.inventory_rel(complete._RUN) and i["graded_by"] == ["event-chain"]
            for i in green["inputs"]))

        # The bundle verification reads every payload through its own descriptors, never _read_live, so a
        # read patched here hands only this module's legs other bytes: a swap between the two reads.
        genesis_rel = apply.genesis_event_rel(complete._RUN)

        def reminted(data, **fields):
            import tomllib
            from _opf_emit import emit_checked
            doc = dict(tomllib.loads(data.decode("utf-8")), **fields)
            if "event_digest" in doc:
                doc["event_digest"] = apply.event_digest(doc)
            return emit_checked(doc).encode("utf-8")

        def swapped_core(data):
            import tomllib
            release = dict(tomllib.loads(data.decode("utf-8"))["release"], manifest_sha256="sha256:" + "4" * 64)
            return reminted(data, release=release)

        def swapping_read(both):
            # A valid genesis event chained from the core (and, when `both`, a valid core it chains from),
            # neither the bytes the bundle inventory attests.
            def read(root_fd, rel):
                fst, data = real_read(root_fd, rel)
                if data is None or sys._getframe(1).f_globals is not vars(here):
                    return fst, data
                if both and rel == receipt_rel:
                    return fst, swapped_core(data)
                if rel == genesis_rel and both:
                    core = swapped_core(real_read(root_fd, receipt_rel)[1])
                    return fst, reminted(data, previous_event_digest="sha256:" + _sha256(core))
                if rel == genesis_rel:
                    return fst, reminted(data, recorded_at="2026-09-17T12:46:00Z")
                return fst, data
            return read

        def digest_of(doc, rel):
            return [i["sha256"] for i in doc["inputs"] if i["path"] == rel]

        def leg_reason(doc, name):
            return " ".join(leg["reason"] for leg in doc["legs"] if leg["leg"] == name)

        unattested = (here, "_attested", lambda rows, rel, data: data)
        for label, both, swapped, decider in (("event", False, (genesis_rel,), "event-chain"),
                                              ("receipt-and-event", True, (genesis_rel, receipt_rel), "receipt-core")):
            swap = on + [(apply, "_read_live", swapping_read(both))]
            doc = case(_full_fixture, swap)
            check("swapped-" + label + "-unattested-cannot", decided(doc, CANNOT_EVALUATE, decider)
                  and "not the bytes the bundle inventory attests" in leg_reason(doc, "event-chain"))
            # Bytes a leg refused are never reported as graded by it (nor digested at all).
            check("swapped-" + label + "-refused-bytes-not-reported", all(
                digest_of(doc, rel) in ([], digest_of(green, rel)) for rel in swapped))
            flip = case(_full_fixture, swap + [unattested])
            check("flip-swapped-" + label + "-unbound-turns-green-on-unattested-digests",
                  flip["verdict"] == ADOPTED_AND_VALID and all(
                      len(digest_of(flip, rel)) == 1 and digest_of(flip, rel) != digest_of(green, rel)
                      for rel in swapped))
        no_row = case(_full_fixture_then(_receipt_unlisted), on)
        check("receipt-without-inventory-row-cannot", no_row["verdict"] != ADOPTED_AND_VALID and any(
            leg["leg"] == "event-chain" and leg["outcome"] == CANNOT and "listed by no bundle inventory"
            in leg["reason"] for leg in no_row["legs"]))
        check("flip-receipt-without-row-unbound-chain-passes", any(
            leg["leg"] == "event-chain" and leg["outcome"] == PASS
            for leg in case(_full_fixture_then(_receipt_unlisted), on + [unattested])["legs"]))

        # A swapped receipt core that would fail the schema is cannot-evaluate, never a receipt finding.
        def bad_core_read(root_fd, rel):
            fst, data = real_read(root_fd, rel)
            if rel == receipt_rel and data is not None and sys._getframe(1).f_globals is vars(here):
                return fst, reminted(data, schema=999)
            return fst, data

        bad_core = case(_full_fixture, on + [(apply, "_read_live", bad_core_read)])
        check("swapped-schema-failing-receipt-cannot-not-graded", decided(bad_core, CANNOT_EVALUATE, "receipt-core")
              and digest_of(bad_core, receipt_rel) == []
              and "not the bytes the bundle inventory attests" in leg_reason(bad_core, "receipt-core"))
        check("flip-swapped-schema-failing-receipt-unattested-invalid", decided(
            case(_full_fixture, on + [(apply, "_read_live", bad_core_read), unattested]), INVALID, "receipt-core"))

        # Each digest-only file is held to its inventory row: a swap after the verification, or a file no
        # row lists, is cannot-evaluate and its unattested digest is not reported.
        digest_only = (apply.plan_rel(complete._RUN), apply.approval_rel(complete._RUN),
                       apply.planning_inventory_rel(complete._RUN))

        def digest_only_swap(target):
            def read(root_fd, rel):
                fst, data = real_read(root_fd, rel)
                if rel == target and data is not None and sys._getframe(1).f_globals is vars(here):
                    return fst, data + b"# swapped after the bundle verification\n"
                return fst, data
            return read

        def unattested_digest_only(root_fd, rel, read, unread, verified):
            # The reading this guard replaced: the bytes read were digested with no inventory row check.
            data = _attempt(lambda: _read_bundle(root_fd, rel))
            if isinstance(data, _Leg):
                unread.append(dict(path=rel, outcome=data.outcome, reason=data.reason))
            else:
                _digested(read, rel, data)

        for rel in digest_only:
            swap = on + [(apply, "_read_live", digest_only_swap(rel))]
            doc = case(_full_fixture, swap)
            check("swapped-digest-only-{}-cannot".format(rel.rsplit("/", 1)[-1]),
                  decided(doc, CANNOT_EVALUATE, "unread-inputs") and digest_of(doc, rel) == []
                  and [(r["path"], r["outcome"]) for r in doc["unread_inputs"]] == [(rel, CANNOT)]
                  and "not the bytes the bundle inventory attests" in doc["unread_inputs"][0]["reason"])
            flip = case(_full_fixture, swap + [(here, "_digest_only", unattested_digest_only)])
            check("flip-swapped-digest-only-{}-unchecked-turns-green".format(rel.rsplit("/", 1)[-1]),
                  flip["verdict"] == ADOPTED_AND_VALID and digest_of(flip, rel) != digest_of(green, rel))
        real_verified = _verified_bundle
        approval_rel = apply.approval_rel(complete._RUN)

        def rowless_approval(root, run_id, read):
            bundle, rows = real_verified(root, run_id, read)
            return bundle, dict((p, d) for p, d in rows.items() if p != approval_rel)

        rowless = case(_full_fixture, on + [(here, "_verified_bundle", rowless_approval)])
        check("digest-only-without-inventory-row-cannot", decided(rowless, CANNOT_EVALUATE, "unread-inputs")
              and [r["path"] for r in rowless["unread_inputs"]] == [approval_rel]
              and "listed by no bundle inventory" in rowless["unread_inputs"][0]["reason"])
        check("flip-digest-only-without-row-unchecked-turns-green", case(_full_fixture, on + [
            (here, "_verified_bundle", rowless_approval),
            (here, "_digest_only", unattested_digest_only)])["verdict"] == ADOPTED_AND_VALID)

        # An absent digest-only file keeps its finding class: INVALID, decided by the unread input.
        def absent_approval(root_fd, rel):
            if rel == approval_rel and sys._getframe(1).f_globals is vars(here):
                return real_read(root_fd, rel)[0], None
            return real_read(root_fd, rel)

        absent = case(_full_fixture, on + [(apply, "_read_live", absent_approval)])
        check("absent-digest-only-file-finding", decided(absent, INVALID, "unread-inputs")
              and [(r["path"], r["outcome"]) for r in absent["unread_inputs"]] == [(approval_rel, FINDING)])

        real_decide = _decide

        def classless(legs, unread_rows):
            # The decision this guard replaced: every unread input was cannot-evaluate, whatever its class.
            return real_decide(legs, [dict(row, outcome=CANNOT) for row in unread_rows])

        check("flip-absent-digest-only-classless-cannot", decided(case(_full_fixture, on + [
            (apply, "_read_live", absent_approval), (here, "_decide", classless)]), CANNOT_EVALUATE, "unread-inputs"))

        # Two rows claiming one path: verify_bundle refuses that first, so it is stubbed here to hand back a
        # second bundle-root inventory carrying the same rows.
        real_verify = apply.verify_bundle

        def doubled_inventory(root, run_id, inventory_bytes=None):
            verified = real_verify(root, run_id, inventory_bytes=inventory_bytes)
            base = apply.inventory_rel(run_id)
            if inventory_bytes is not None:
                inventory_bytes[base.rsplit("/", 1)[0] + "/inventory-copy.toml"] = inventory_bytes[base]
            return verified

        doubled = case(_full_fixture, on + [(apply, "verify_bundle", doubled_inventory)])
        check("path-claimed-by-two-rows-cannot", decided(doubled, CANNOT_EVALUATE, "receipt-core")
              and "claimed by two bundle inventory rows" in leg_reason(doubled, "event-chain"))

        def last_row_wins(rows, path, sha256):
            # With no guard, a second row silently overwrites the first.
            rows[path] = sha256

        check("flip-two-rows-unguarded-turns-green", case(_full_fixture, on + [
            (apply, "verify_bundle", doubled_inventory), (here, "_claim", last_row_wins)])["verdict"]
            == ADOPTED_AND_VALID)

        # Disclosed: a cannot-evaluate event listing masks a finding in another event (never a pass).
        masked = case(_full_fixture_then(lambda r: (_forged_digest(r), _misspelled(r))), on)
        check("listing-cannot-masks-event-finding-disclosed", decided(masked, CANNOT_EVALUATE, "event-chain")
              and "such a run scores CANNOT_EVALUATE, not INVALID" in " ".join((__doc__ or "").split()))

        check("cli-outcome-exact-int-only", [_cli_outcome(v) for v in (0, 1, False, True, 0.0, 2, None)]
              == [PASS, FINDING, CANNOT, CANNOT, CANNOT, CANNOT, CANNOT])
        falsy = case(_full_fixture, switches + [(here, "_opf_cli", stub_cli(False))])
        check("leg-returning-false-cannot", decided(falsy, CANNOT_EVALUATE, "render-check"))
        check("flip-dict-lookup-false-turns-green", case(_full_fixture, switches + [
            (here, "_opf_cli", stub_cli(False)), (here, "_cli_outcome", lambda rc: {0: PASS, 1: FINDING}.get(rc, CANNOT))
        ])["verdict"] == ADOPTED_AND_VALID)

        def exiting_cli(argv):
            if argv[0] == "render":
                raise SystemExit(0)
            return stub_cli(0)(argv)

        exited = case(_full_fixture, switches + [(here, "_opf_cli", exiting_cli)])
        check("leg-raising-systemexit-cannot", decided(exited, CANNOT_EVALUATE, "render-check"))

        def exception_only_run_leg(name, legs, call):
            # The handler this guard replaced: only Exception was caught, so a SystemExit escaped.
            try:
                outcome, reason = call()
            except _Leg as exc:
                outcome, reason = exc.outcome, exc.reason
            except Exception as exc:  # noqa: BLE001
                outcome, reason = CANNOT, repr(exc)
            legs.append(dict(leg=name, outcome=outcome, reason=reason))

        try:
            case(_full_fixture, switches + [(here, "_opf_cli", exiting_cli), (here, "_run_leg", exception_only_run_leg)])
            escaped = False
        except SystemExit:
            escaped = True
        check("flip-exception-only-handler-systemexit-escapes", escaped)

        def run_main(root):
            with mock.patch.object(sys, "argv", ["_opf_adopt_verdict.py", root]), \
                    contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                return main()

        outside = [(apply, "plan_rel", mock.Mock(side_effect=RuntimeError("outside a leg")))]
        check("exception-outside-leg-main-exit-2", case(_full_fixture, on + outside, call=run_main) == 2)
        try:
            case(_full_fixture, on + outside)
            raised = False
        except RuntimeError:
            raised = True
        check("flip-score-alone-lets-exception-escape", raised)

        def swallowing(root_fd, rel, read, unread, verified):
            # The reading this guard replaced: an unreadable digest-only file was dropped silently.
            data = _attempt(lambda: _read_bundle(root_fd, rel))
            if not isinstance(data, _Leg):
                _digested(read, rel, data)

        # The status and completion legs read the approval too and would stop first on a real refusal, so
        # only this module's own read of it is refused here.
        approval = apply.approval_rel(complete._RUN)

        def refusing_read(root_fd, rel):
            if rel == approval and sys._getframe(1).f_globals is vars(here):
                raise apply.AdoptApplyError("refused for the self-test")
            return real_read(root_fd, rel)

        unreadable = on + [(apply, "_read_live", refusing_read)]
        unread = case(_full_fixture, unreadable)
        check("unreadable-digest-only-file-reported",
              [row["path"] for row in unread["unread_inputs"]] == [approval]
              and "refused for the self-test" in unread["unread_inputs"][0]["reason"]
              and approval not in [i["path"] for i in unread["inputs"]])
        check("flip-swallowed-unreadable-file-unreported",
              case(_full_fixture, unreadable + [(here, "_digest_only", swallowing)])["unread_inputs"] == [])
        check("unreadable-digest-only-file-cannot", decided(unread, CANNOT_EVALUATE, "unread-inputs"))

        def legs_only(legs, unread_rows):
            # The decision this guard replaced: an unread input was reported but never decided the verdict.
            for outcome, verdict in ((FINDING, INVALID), (CANNOT, CANNOT_EVALUATE)):
                for leg in legs:
                    if leg["outcome"] == outcome:
                        return verdict, leg["leg"]
            return ADOPTED_AND_VALID, "all-legs"

        flip_unread = case(_full_fixture, unreadable + [(here, "_decide", legs_only)])
        check("flip-unread-input-ignored-turns-green", flip_unread["verdict"] == ADOPTED_AND_VALID
              and [row["path"] for row in flip_unread["unread_inputs"]] == [approval])

        def slow_cli(argv):
            if argv[0] == "render":
                import time
                time.sleep(1.5)
            return stub_cli(0)(argv)

        short = dict(LEG_DEADLINE_SECONDS, **{"render-check": 0.2})
        late = case(_full_fixture, switches + [(here, "_opf_cli", slow_cli), (here, "LEG_DEADLINE_SECONDS", short)])
        check("leg-past-deadline-cannot", decided(late, CANNOT_EVALUATE, "render-check")
              and "deadline" in late["legs"][-1]["reason"])
        check("flip-leg-within-deadline-green", case(_full_fixture, switches + [
            (here, "_opf_cli", slow_cli)])["verdict"] == ADOPTED_AND_VALID)
    except Exception as exc:  # noqa: BLE001  a harness error is exit 2, never a pass
        print("_opf_adopt_verdict self-test: harness error: {!r}".format(exc), file=sys.stderr)
        return 2
    failed = [name for name, ok in results if not ok]
    for name, ok in results:
        print("{} {}".format("PASS" if ok else "FAIL", name))
    print("_opf_adopt_verdict self-test: {} checks, {} failed".format(len(results), len(failed)))
    return 1 if failed else 0


def main():
    """Exit 0, 1 or 2 by the module docstring; any exception, from a leg or outside one, ends at 2."""
    try:
        args = sys.argv[1:]
        if args in (["--self-test"], ["--selftest"]):
            return self_test()
        if len(args) != 1 or args[0].startswith("-"):
            print("usage: _opf_adopt_verdict.py PRODUCT_ROOT | --self-test", file=sys.stderr)
            return 2
        doc = score(args[0])
        sys.stdout.write(canonical(doc))
        return EXIT_CODES[doc["verdict"]]
    except BaseException as exc:  # noqa: BLE001  fail closed: never a pass, never exit 1
        try:
            print("error: _opf_adopt_verdict.py could not score ({!r}); cannot evaluate".format(exc),
                  file=sys.stderr)
        except BaseException:
            pass
        return 2


# The child-status guard: an inline copy of the canonical guard in opf/tools/_child_status.py, which
# tools/check_child_status_coverage.py pins this copy to by AST. The modules it imports hold child launch
# and wait calls, so under an ignored SIGCHLD a failing child they start would read as a pass. The guard
# runs as the first statement of self_test() and at the entry line before main(); the entry line comes
# after the --self-test statement that opf.py requires first in the __main__ block. An import of this
# module runs nothing.
# The status the probe child exits with; any value other than 0 and the refusal exit would do.
_CHILD_STATUS_PROBE_CODE = 77


def _child_status_refusal():
    """None when this process can read a child's exit status, otherwise the reason it cannot."""
    import os
    import signal
    if not hasattr(signal, "SIGCHLD"):
        return None
    disposition = signal.getsignal(signal.SIGCHLD)
    if disposition is not signal.SIG_DFL:
        return ("SIGCHLD is ignored (SIG_IGN) in this process" if disposition is signal.SIG_IGN
                else "SIGCHLD is not at its default disposition in this process")
    if not hasattr(os, "fork"):
        return None
    try:
        pid = os.fork()
    except OSError as exc:
        return "the child-status probe could not fork ({})".format(type(exc).__name__)
    if pid == 0:
        os._exit(_CHILD_STATUS_PROBE_CODE)
    try:
        waited, status = os.waitpid(pid, 0)
    except ChildProcessError:
        return "the probe child was reaped before its exit status could be read (ECHILD)"
    except OSError as exc:
        return "the probe child's exit status could not be read ({})".format(type(exc).__name__)
    if waited != pid or os.waitstatus_to_exitcode(status) != _CHILD_STATUS_PROBE_CODE:
        return "a probe child that exited {} was read back as wait status {}".format(
            _CHILD_STATUS_PROBE_CODE, status)
    return None


def _child_status_refusal_exit(name):
    """0 when a child's exit status is observable; otherwise write the named refusal and exit 2 at once
    (os._exit, so no exit-time stream flush can replace the status)."""
    import os
    reason = _child_status_refusal()
    if reason is None:
        return 0
    try:
        sys.stderr.write("error: {} refuses to run: {}, so a failed child would read as a success. Nothing "
                         "was run (cannot evaluate). Start it from a parent that leaves SIGCHLD at "
                         "SIG_DFL.\n".format(name, reason))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-test"]:
        sys.exit(self_test())
    sys.exit(_child_status_refusal_exit("_opf_adopt_verdict.py") or main())
