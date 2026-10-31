#!/usr/bin/env python3
"""OPF adoption verdict scorer: one verdict for a product root, composed from the checks that already exist.

Input: the product root and nothing else (no transcript, no claim, no run id: the runs are the ones
`opf adopt status` lists). The scorer recomputes no check logic; it runs, in this fixed order:
  adopt-status    `opf adopt status --root ROOT` (the evidence bundles and the adoption journal)
  adopt-complete  _opf_adopt_complete.evaluate(ROOT, run, doctor=store_doctor(), prober=PROBER) per run,
                  graded by roster_green
  receipt-core    _opf_adopt.validate_receipt_core over the run's receipt core
  event-chain     _opf_adopt.validate_event_chain over the run's outcome events, rooted at the core digest
  store-doctor    `opf doctor --require-store --root ROOT`
  render-check    `opf render --check --root ROOT`

Verdict (exactly one, printed as canonical JSON with the deciding leg and the sha256 of each bundle file
the scorer itself read):
  NOT_ADOPTED        adopt-status is clean and lists no adoption run (exit 1)
  INVALID            some leg reports a finding; the first such leg in the order above decides (exit 1)
  CANNOT_EVALUATE    some leg could not be read or run, or reported cannot-evaluate; the first such leg
                     decides, never a pass (exit 2)
  ADOPTED_AND_VALID  every leg passes for every listed run (exit 0)

A finding outranks a cannot-evaluate, since a finding is a definite failure; both are non-pass.

PROBER: the shipped pack carries no live probe adapter (_opf_adopt_complete.PR6_DENY_PROBES_SHIPPED), so
PROBER is None and the completion roster's wiring check is CANNOT-EVALUATE; the self-test injects the
completion module's fixture surface to grade the composition.

NOT YET PROVEN AGAINST A REAL ADOPTED STORE: no adoption can complete on main yet (no live probe adapter
ships, and the completion fixture's store holds only its manifest, so the real store doctor and render
check cannot evaluate over it). The ADOPTED_AND_VALID and view-drift self-test vectors therefore stub the
store-doctor and render-check results: they grade how the scorer composes the legs, not that a real
adopted store scores ADOPTED_AND_VALID or that a real render detects view drift.

DISCLOSED RESIDUALS: the input digests cover the bundle files this module reads (plan, approval, receipt,
planning inventory, inventory, outcome events); the files the composed legs read on their own are not
digested here. The legs run in this process, one after another, so a tree changed between legs is not
detected.

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
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _opf_adopt as schema              # noqa: E402
import _opf_adopt_apply as apply         # noqa: E402
import _opf_adopt_complete as complete   # noqa: E402
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
# The outcome events a run's bundle may hold, numbered contiguously from event-0001.toml.
_EVENT_NAME = "event-{:04d}.toml"
_MAX_EVENTS = 9999
_STATUS_RUN = re.compile(r"opf adopt status: adoption run (\S+): evidence bundle at ")


class _Leg(Exception):
    """A leg outcome that ends that leg early."""

    def __init__(self, outcome, reason):
        super().__init__(reason)
        self.outcome, self.reason = outcome, reason


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
    return {0: PASS, 1: FINDING}.get(rc, CANNOT)


def _leg_status(root):
    rc, out, err = _opf_cli(["adopt", "status", "--root", root])
    runs = [m.group(1) for m in map(_STATUS_RUN.match, out.splitlines()) if m is not None]
    return _cli_outcome(rc), (out + err).strip(), runs


def _read_bundle(root_fd, rel, read):
    """The live bytes at `rel`, digested into `read`; absence raises a finding, a read refusal cannot."""
    try:
        _fst, data = apply._read_live(root_fd, rel)
    except apply.AdoptApplyError as exc:
        raise _Leg(CANNOT, "{}: {}".format(rel, exc))
    if data is None:
        raise _Leg(FINDING, "{} is absent".format(rel))
    read[rel] = _sha256(data)
    return data


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


def _leg_receipt(root_fd, run_id, read):
    receipt = _parsed(_read_bundle(root_fd, apply.receipt_rel(run_id), read), "the adoption receipt core")
    checked = schema.validate_receipt_core(receipt)
    return _graded(checked.status, checked.findings, "receipt core")


def _leg_events(root_fd, run_id, read):
    core = _read_bundle(root_fd, apply.receipt_rel(run_id), read)
    home = apply.evidence_home_rel(run_id)
    events = []
    for number in range(1, _MAX_EVENTS + 2):
        rel = home + "/" + _EVENT_NAME.format(number)
        try:
            _fst, data = apply._read_live(root_fd, rel)
        except apply.AdoptApplyError as exc:
            raise _Leg(CANNOT, "{}: {}".format(rel, exc))
        if data is None:
            break
        if number > _MAX_EVENTS:
            raise _Leg(CANNOT, "the outcome-event chain runs past {} events".format(_MAX_EVENTS))
        read[rel] = _sha256(data)
        events.append(_parsed(data, "outcome event " + rel))
    chain = schema.validate_event_chain(events, "sha256:" + _sha256(core))
    return _graded(chain.status, chain.findings, "outcome-event chain")


def _leg_cli(argv):
    rc, out, err = _opf_cli(argv)
    return _cli_outcome(rc), "exit {}: {}".format(rc, (out + err).strip())


def _run_leg(name, legs, call):
    try:
        outcome, reason = call()
    except _Leg as exc:
        outcome, reason = exc.outcome, exc.reason
    except Exception as exc:  # noqa: BLE001  a leg that cannot run is cannot-evaluate, never a pass
        outcome, reason = CANNOT, "the leg could not run ({!r})".format(exc)
    legs.append(dict(leg=name, outcome=outcome, reason=reason))


def score(product_root):
    """The verdict document for one product root (see the module docstring). Read-only."""
    root = os.path.abspath(str(product_root))
    legs, read = [], {}
    try:
        status_outcome, status_reason, runs = _leg_status(root)
    except Exception as exc:  # noqa: BLE001  fail closed
        status_outcome, status_reason, runs = CANNOT, "the leg could not run ({!r})".format(exc), []
    legs.append(dict(leg="adopt-status", outcome=status_outcome, reason=status_reason))
    if status_outcome == PASS and not runs:
        return _verdict(NOT_ADOPTED, "adopt-status", legs, read)
    root_fd = None
    try:
        root_fd = apply._open_product_root(root)
    except Exception as exc:  # noqa: BLE001  fail closed
        legs.append(dict(leg="adopt-complete", outcome=CANNOT, reason="cannot open the product root ({!r})".format(exc)))
    try:
        for run_id in runs if root_fd is not None else ():
            _run_leg("adopt-complete", legs, lambda: _leg_complete(root, run_id))
            if "receipt-core" in LEGS:
                _run_leg("receipt-core", legs, lambda: _leg_receipt(root_fd, run_id, read))
            if "event-chain" in LEGS:
                _run_leg("event-chain", legs, lambda: _leg_events(root_fd, run_id, read))
            for rel in (apply.plan_rel(run_id), apply.approval_rel(run_id), apply.planning_inventory_rel(run_id),
                        apply.inventory_rel(run_id)):
                try:
                    _read_bundle(root_fd, rel, read)
                except _Leg:
                    pass
    finally:
        if root_fd is not None:
            store._close_fd_exc_safe(root_fd)
    _run_leg("store-doctor", legs, lambda: _leg_cli(["doctor", "--require-store", "--root", root]))
    if "render-check" in LEGS:
        _run_leg("render-check", legs, lambda: _leg_cli(["render", "--check", "--root", root]))
    for outcome, verdict in ((FINDING, INVALID), (CANNOT, CANNOT_EVALUATE)):
        for leg in legs:
            if leg["outcome"] == outcome:
                return _verdict(verdict, leg["leg"], legs, read)
    return _verdict(ADOPTED_AND_VALID, "all-legs", legs, read)


def _verdict(verdict, decided_by, legs, read):
    return dict(verdict=verdict, decided_by=decided_by, legs=legs,
                inputs=[dict(path=rel, sha256=read[rel]) for rel in sorted(read)])


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

    def case(prepare=None, patches=()):
        with tempfile.TemporaryDirectory(prefix="opf-adopt-verdict-") as root:
            if prepare is not None:
                prepare(root)
            with contextlib.ExitStack() as stack:
                for target, attr, value in patches:
                    stack.enter_context(mock.patch.object(target, attr, value))
                return score(root)

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
    except Exception as exc:  # noqa: BLE001  a harness error is exit 2, never a pass
        print("_opf_adopt_verdict self-test: harness error: {!r}".format(exc), file=sys.stderr)
        return 2
    failed = [name for name, ok in results if not ok]
    for name, ok in results:
        print("{} {}".format("PASS" if ok else "FAIL", name))
    print("_opf_adopt_verdict self-test: {} checks, {} failed".format(len(results), len(failed)))
    return 1 if failed else 0


def main():
    args = sys.argv[1:]
    if args in (["--self-test"], ["--selftest"]):
        return self_test()
    if len(args) != 1 or args[0].startswith("-"):
        print("usage: _opf_adopt_verdict.py PRODUCT_ROOT | --self-test", file=sys.stderr)
        return 2
    doc = score(args[0])
    sys.stdout.write(canonical(doc))
    return EXIT_CODES[doc["verdict"]]


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
