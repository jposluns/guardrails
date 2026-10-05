#!/usr/bin/env python3
"""P0 contract vectors and source reversions; runner fixtures use private scratch.

The D2a fixture retains the landed source-only builders' bytes in memory before
any P0 guard is exercised. Reversions receive those same intact fixtures.
Runner verification executes the real standalone shell dispatcher with other
gates intercepted by an executable PATH fixture (no shell function stands in
for python3), then requires this suite's actual assertion identities and
reconciles the fixture's recorded calls against the runner's registrations.
That check proves P0 dispatch, not that the other standalone gates passed.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    sys.stderr.write(
        "error: check_opf_init_p0.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
        "Nothing was run (cannot evaluate).\n"
        % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
    raise SystemExit(2)

import argparse
import copy
import hashlib
import subprocess
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _opf_init as builders  # noqa: E402
import _opf_init_contract as contract  # noqa: E402
import _opf_init_operation as operation  # noqa: E402
import _opf_init_p0 as p0  # noqa: E402

OP_ID = "abcdef01-2345-4678-9abc-def012345678"


def check(condition, identity):
    if not condition:
        raise AssertionError(identity)


def fixtures():
    ctx = contract._mk_ctx([])
    plan, raw = operation.build_init_plan(
        operation_id=OP_ID, binding=ctx["binding"], head=ctx["head"],
        inventory_digest_value=contract._digest(contract.canonical_json_bytes(ctx["inventory"])),
        application_time="2026-01-01T00:00:00Z")
    kept, kept_raw = operation.build_init_plan(
        operation_id=OP_ID, binding=ctx["binding"], head=ctx["head"],
        inventory_digest_value=plan["inventory_digest"], application_time=plan["application_time"],
        existing_changelog={"path": "CHANGELOG.md", "mode": 0o644, "size": 3,
                            "digest": contract._digest(b"old")})
    # The source-only fixture follows opf._cmd_init, not the coupled plan's provenance roster.
    home = operation._MACHINE_HOME + "/"
    d2a = {
        home + "manifest.toml": builders.build_manifest().encode(),
        home + "counters.toml": builders.build_counters().encode(),
        home + "version.toml": builders.build_version().encode(),
        home + "worklog.toml": builders.build_worklog().encode(),
        ".opf.toml": b'[store]\ntarget = "dir:."\n',
        "CHANGELOG.md": b"# Changelog\n",
    }
    for name in builders.INDEX_TYPES:
        d2a[home + name + ".index.toml"] = builders.build_index(name).encode()
    check(operation.PROVENANCE_RELPATH not in d2a, "fixture/d2a-no-provenance")
    old = dict(d2a)
    old[p0.MANIFEST_PATH] = old[p0.MANIFEST_PATH].replace(b'"1.2.0"', b'"1.1.0"')
    result = operation.InitResult()
    result.status = operation.VIEWS_READY
    run = types.SimpleNamespace(result=result, plan=plan, phases=[], checks=[], back=None)
    outcome = contract.canonical_json_bytes(operation._outcome(run, None))
    return plan, raw, kept, kept_raw, d2a, old, outcome


def vectors(f):
    plan, raw, kept, kept_raw, d2a, old, outcome = f
    cases = []

    def add(name, call, status="VALID", code=None):
        cases.append((name, call, status, code))

    def altered(change, source=plan):
        doc = copy.deepcopy(source)
        change(doc)
        doc["plan_digest"] = operation.compute_plan_digest(doc)
        return contract.canonical_json_bytes(doc)

    def plan_case(name, change, code, source=plan, completed=False):
        data = altered(change, source)
        add(name, lambda m: m.validate_plan(data, completed=completed), "REFUSED", code)

    add("accept/plan", lambda m: m.validate_plan(raw))
    add("accept/preserved-changelog", lambda m: m.validate_plan(kept_raw))
    add("accept/outcome", lambda m: m.validate_outcome(outcome, operation_id=OP_ID))
    add("accept/empty-staging", lambda m: m.validate_staging_membership(raw, []))
    add("accept/hypothetical-S",
        lambda m: m.validate_staging_membership(raw, [e["path"] for e in plan["sets"]["S"]]))
    plan_case("refuse/overlap",
              lambda d: d["sets"]["V"].append(copy.deepcopy(d["sets"]["C"][0])), "OVERLAP")
    plan_case("refuse/unclassified-S",
              lambda d: d["sets"]["S"].append({"path": ".working/unknown.toml"}), "UNCLASSIFIED")
    plan_case("refuse/unclassified-V",
              lambda d: d["sets"]["V"].append({"path": ".working/EXTRA.md"}), "UNCLASSIFIED")
    plan_case("refuse/sets-key", lambda d: d["sets"].update(I=[]), "SETS")
    plan_case("refuse/K", lambda d: d["sets"]["K"].append({"path": ".working/kept.txt"}), "SETS")
    plan_case("refuse/C", lambda d: d["sets"].update(C=[]), "UNCLASSIFIED")
    plan_case("refuse/E", lambda d: d["sets"].update(E=[{"path": "OTHER.md"}]), "UNCLASSIFIED")
    plan_case("refuse/order", lambda d: d["sets"]["S"].reverse(), "SETS")
    for name, value in (("unknown", "opf.init.plan/v999"), ("older", "opf.init.plan/v0")):
        plan_case("refuse/plan-version-" + name, lambda d, v=value: d.update(format=v), "VERSION")
    plan_case("refuse/plan-schema", lambda d: d.update(schema=True), "VERSION")
    for key in ("spec_version", "generator", "provenance_format", "views_generator"):
        plan_case("refuse/pin-" + key,
                  lambda d, k=key: d["versions"].update({k: "unknown"}), "VERSION")
    historical = altered(lambda d: d["versions"]["views_generator"].update(version="recorded-old"))
    add("accept/completed-recorded-generator",
        lambda m: m.validate_plan(historical, completed=True))
    add("refuse/interrupted-recorded-generator",
        lambda m: m.validate_plan(historical), "REFUSED", "VERSION")
    plan_case("refuse/completed-spec-pin",
              lambda d: d["versions"].update(spec_version="1.1.0"), "VERSION", completed=True)
    plan_case("refuse/staging-v1",
              lambda d: d.update(staging_set=[d["sets"]["S"][0]["path"]]), "STAGING")
    plan_case("refuse/boundaries",
              lambda d: d["publication_boundaries"].update(index="staged"), "STAGING")
    plan_case("refuse/integration-key", lambda d: d.update(integration_set=[]), "MALFORMED")
    plan_case("refuse/recovery", lambda d: d.update(recovery_policy="reinterpret"), "MALFORMED")
    plan_case("refuse/source-mode",
              lambda d: d["sets"]["S"][0].update(mode=0o600), "MALFORMED")
    plan_case("refuse/hidden-stage",
              lambda d: d["sets"]["S"][0].update(staging="other"), "MALFORMED")
    plan_case("refuse/payload",
              lambda d: d["sets"]["S"][0].update(payload=""), "MALFORMED")
    plan_case("refuse/E-schema",
              lambda d: d["sets"]["E"][0].update(extra=True), "MALFORMED", source=kept)
    for label, path in (
        ("V", plan["sets"]["V"][0]["path"]), ("K", ".working/kept.txt"),
        ("E", "CHANGELOG.md"), ("C", operation.LEASE_RELPATH),
        ("directory", ".working/toml"), ("unclassified", ".adapter"),
    ):
        # K is hypothetical: v1 has no admitted Keep entry. This path remains non-S.
        add("refuse/staged-" + label,
            lambda m, p=path: m.validate_staging_membership(kept_raw, [p]), "REFUSED", "STAGING")
    add("refuse/staged-duplicate",
        lambda m: m.validate_staging_membership(raw, [".opf.toml", ".opf.toml"]),
        "REFUSED", "STAGING")
    doc = operation._opf_init_substrate._strict_json_loads(outcome, "fixture")
    for name, value in (("unknown", "opf.init.outcome/v999"), ("older", "opf.init.outcome/v0")):
        bad = contract.canonical_json_bytes(dict(doc, format=value))
        add("refuse/outcome-version-" + name,
            lambda m, b=bad: m.validate_outcome(b, operation_id=OP_ID), "REFUSED", "VERSION")
    add("refuse/outcome-binding",
        lambda m: m.validate_outcome(outcome, operation_id="0" * 36), "REFUSED", "SCHEMA")
    for status in sorted(p0.RESULT_STATUSES):
        def result_case(m, s=status):
            r = operation.InitResult()
            r.status = s
            return m.validate_result(r, contract_kind="coupled")
        add("accept/result-" + status, result_case)
    add("accept/source-only-zero", lambda m: m.validate_result(0, contract_kind="source-only"))
    add("refuse/zero-as-coupled", lambda m: m.validate_result(0, contract_kind="coupled"),
        "REFUSED", "RESULT")
    add("refuse/coupled-as-source",
        lambda m: m.validate_result(operation.InitResult(), contract_kind="source-only"),
        "REFUSED", "RESULT")
    def bad_status(m):
        r = operation.InitResult()
        r.status = "SOURCES-READY"
        return m.validate_result(r, contract_kind="coupled")
    add("refuse/result-status", bad_status, "REFUSED", "RESULT")
    def missing_status(m):
        r = operation.InitResult()
        del r.status
        return m.validate_result(r, contract_kind="coupled")
    add("refuse/result-missing-status", missing_status, "REFUSED", "RESULT")
    manifest = d2a[p0.MANIFEST_PATH]
    add("compat/d2a-retained", lambda m: m.validate_compatibility(manifest))
    provenance = operation.plan_payloads(plan)[operation.PROVENANCE_RELPATH]
    add("compat/provenance-present",
        lambda m: m.validate_compatibility(manifest, provenance=provenance))
    add("compat/bad-provenance",
        lambda m: m.validate_compatibility(manifest, provenance=b"not toml"),
        "REFUSED", "COMPATIBILITY")
    add("compat/old-needs-upgrade", lambda m: m.validate_compatibility(old[p0.MANIFEST_PATH]),
        "REFUSED", "VERSION")
    add("compat/version-only-upgrade", lambda m: m.validate_upgrade_delta(old, d2a))
    invented = dict(d2a, **{operation.PROVENANCE_RELPATH: provenance})
    add("compat/no-invented-provenance",
        lambda m: m.validate_upgrade_delta(old, invented), "REFUSED", "UPGRADE")
    changed = dict(d2a)
    changed[operation.COUNTERS_RELPATH] += b"# changed\n"
    add("compat/no-counter-change",
        lambda m: m.validate_upgrade_delta(old, changed), "REFUSED", "UPGRADE")
    other = dict(d2a)
    other[p0.MANIFEST_PATH] = manifest.replace(b'posture = "required"', b'posture = "optional"')
    add("compat/no-other-field", lambda m: m.validate_upgrade_delta(old, other), "REFUSED", "UPGRADE")
    def homes(m):
        # Verify the generation fence even if a later resolver activates generation 2.
        from unittest.mock import patch
        with patch.object(m.store, "SUPPORTED_HOMES", 2):
            return m.validate_compatibility(manifest)
    add("compat/homes-fence", homes, "REFUSED", "HOMES")
    for name, value in (("missing", None), ("empty", b""), ("not-object", b"[]\n"),
                        ("duplicate", b'{"schema":1,"schema":1}\n'), ("truncated", raw[:-4])):
        add("input/" + name, lambda m, v=value: m.validate_plan(v),
            "CANNOT-EVALUATE" if value is None else "REFUSED",
            "INPUT" if value is None or value == b"" else "MALFORMED")
    return cases


def fingerprint(f):
    # Includes each retained file body, not a success marker or merely its path.
    return hashlib.sha256(repr(f).encode()).hexdigest()


def run_case(module, case, f):
    name, call, status, code = case
    before = fingerprint(f)
    got = call(module)
    check(got.status == status and (code is None or
          len(got.findings) == 1 and got.findings[0][0] == code), name)
    check(fingerprint(f) == before, name + "/fixture-unchanged")


def run_vectors(module, f):
    cases = vectors(f)
    check(len({c[0] for c in cases}) == len(cases), "harness/unique-identities")
    for case in cases:
        run_case(module, case, f)
        print("PASS " + case[0])
    return tuple(c[0] for c in cases)


# Exact source edits, each disabling one admission/refusal guard or dispatch.
# A named assertion is the only accepted RED. Other exceptions are harness failures.
REVERSIONS = (
    ("plan-dispatch", 'return _plan(raw, completed)',
     'raise _Refusal("DISPATCH", "plan", "v1 dispatch removed")', "accept/plan"),
    ("outcome-dispatch", 'return substrate._validate_outcome(raw, operation_id)',
     'raise _Refusal("DISPATCH", "outcome", "v1 dispatch removed")', "accept/outcome"),
    ("overlap", 'len(paths) == len(set(paths))', 'True', "refuse/overlap"),
    ("source-roster", 'set(sources) == roster and len(sources) == len(roster)',
     'True', "refuse/unclassified-S"),
    ("view-roster", 'sets["V"] == views', 'True', "refuse/unclassified-V"),
    ("plan-version", '_version(doc, PLAN_FORMATS, "plan")', 'pass', "refuse/plan-version-unknown"),
    ("outcome-version", '_version(doc, OUTCOME_FORMATS, "outcome")', 'pass',
     "refuse/outcome-version-unknown"),
    ("pins", 'plan["versions"] == expected', 'True', "refuse/pin-generator"),
    ("v1-staging", 'plan["staging_set"] == []', 'True', "refuse/staging-v1"),
    ("publication", 'plan["publication_boundaries"] == operation.PUBLICATION_BOUNDARIES',
     'True', "refuse/boundaries"),
    ("F19-admission", 'set(staged_paths) <= sources', 'False', "accept/empty-staging"),
    ("result-admission", 'type(value) is operation.InitResult and type(getattr(value, "status", None)) is str\n                 and value.status in RESULT_STATUSES',
     'False', "accept/result-VIEWS-READY"),
    ("source-type", 'type(value) is int and value in (0, 2)',
     'True', "refuse/coupled-as-source"),
    ("F19", 'set(staged_paths) <= sources', 'True', "refuse/staged-V"),
    ("optional-provenance", 'if provenance is not None:', 'if True:', "compat/d2a-retained"),
    ("upgrade-files", 'set(before) == set(after)\n             and all(before[p] == after[p] for p in before if p != MANIFEST_PATH)',
     'True', "compat/no-invented-provenance"),
    ("upgrade-field", 'new == expected', 'True', "compat/no-other-field"),
    ("homes", 'store.SUPPORTED_HOMES == 1 and store.homes_generation(manifest) == 1',
     'True', "compat/homes-fence"),
    ("source-result", 'type(value) is int and value in (0, 2)',
     'False', "accept/source-only-zero"),
    ("upgrade-dispatch", 'return new', 'raise _Refusal("DISPATCH", "upgrade", "removed")',
     "compat/version-only-upgrade"),
    ("completed-generator", 'if completed:', 'if False:', "accept/completed-recorded-generator"),
    ("coupled-type", 'type(value) is operation.InitResult and type(getattr(value, "status", None)) is str\n                 and value.status in RESULT_STATUSES',
     'True', "refuse/zero-as-coupled"),
)


def _names_identity(exc, identity):
    """True only when exc is an exact AssertionError whose args are exactly (identity,), or, for identity
    None, whose one argument is any exact str. Only exact built-in
    types are read (type(exc), the args tuple of an exact AssertionError and an exact str in it), so no code
    from the caught instance runs (no __str__, __eq__, __class__ or args property of a subclass or of an
    argument), and the comparison cannot itself end the process."""
    if type(exc) is not AssertionError:
        return False
    args = exc.args
    return len(args) == 1 and type(args[0]) is str and (identity is None or args[0] == identity)


def red_on_revert(source, f):
    cases = {c[0]: c for c in vectors(f)}
    expanded = []
    for row in REVERSIONS:
        expanded.append(row)
        guard, old, new, _identity = row
        extra = {
            "F19-admission": ("accept/hypothetical-S",),
            "result-admission": tuple("accept/result-" + s for s in sorted(p0.RESULT_STATUSES)
                                      if s != "VIEWS-READY"),
            "coupled-type": ("refuse/result-status", "refuse/result-missing-status"),
            "F19": ("refuse/staged-K", "refuse/staged-E", "refuse/staged-C"),
            "upgrade-files": ("compat/no-counter-change",),
            "pins": ("refuse/pin-spec_version", "refuse/pin-provenance_format",
                     "refuse/pin-views_generator", "refuse/interrupted-recorded-generator",
                     "refuse/completed-spec-pin"),
            "plan-version": ("refuse/plan-version-older", "refuse/plan-schema"),
            "outcome-version": ("refuse/outcome-version-older",),
            "plan-dispatch": ("accept/preserved-changelog",),
        }.get(guard, ())
        expanded.extend((guard, old, new, identity) for identity in extra)
    for guard, old, new, identity in expanded:
        check(source.count(old) == 1, "revert/" + guard + "/unique-source")
        module = types.ModuleType("_opf_init_p0_reverted")
        module.__file__ = p0.__file__
        # The reverted source is loaded and called in this process: a process ending from it (SystemExit 0
        # or None, GeneratorExit, any other BaseException) is a harness failure, CANNOT-EVALUATE (exit 2) in
        # main, never this gate's status; a KeyboardInterrupt propagates so an operator's Ctrl-C stops the run.
        # No handler here runs code from the caught instance: an AssertionError subclass from the reverted
        # source (whose __str__ could raise SystemExit 0) is cannot-evaluate with a fixed message, and so is an
        # exact AssertionError without one exact-str argument; any other is compared through _names_identity
        # alone. A KeyboardInterrupt of exactly that class propagates; a subclass, which only loaded code
        # raises, is cannot-evaluate and is never re-raised.
        try:
            exec(compile(source.replace(old, new), module.__file__, "exec"), module.__dict__)
        except Exception:
            raise
        except KeyboardInterrupt as exc:
            if type(exc) is KeyboardInterrupt:
                raise
            raise RuntimeError("revert/" + guard + ": the reverted source raised a KeyboardInterrupt subclass at "
                               "load; fail-closed") from None
        except BaseException:  # noqa: BLE001  a process ending at load is cannot-evaluate, never a pass
            raise RuntimeError("revert/" + guard + ": the reverted source ended the process at load; "
                               "fail-closed") from None
        before = fingerprint(f)
        try:
            run_case(module, cases[identity], f)
        except AssertionError as exc:
            if type(exc) is not AssertionError:
                raise RuntimeError("revert/" + guard + ": the reverted source raised an AssertionError subclass "
                                   "in a call; fail-closed") from None
            if not _names_identity(exc, None):
                raise RuntimeError("revert/" + guard + ": the reverted source raised an AssertionError without "
                                   "one exact-str argument in a call; fail-closed") from None
            check(_names_identity(exc, identity), "revert/" + guard + "/wrong-assertion")
        except Exception:
            raise
        except KeyboardInterrupt as exc:
            if type(exc) is KeyboardInterrupt:
                raise
            raise RuntimeError("revert/" + guard + ": the reverted source raised a KeyboardInterrupt subclass in "
                               "a call; fail-closed") from None
        except BaseException:  # noqa: BLE001  a process ending in a call is cannot-evaluate, never a pass
            raise RuntimeError("revert/" + guard + ": the reverted source ended the process in a call; "
                               "fail-closed") from None
        else:
            raise AssertionError("revert/" + guard + "/not-red")
        check(fingerprint(f) == before, "revert/" + guard + "/fixture-changed")
        print("RED {} -> {}".format(guard, identity))


# The KeyboardInterrupt a poisoned reverted source raises on purpose. Only it is recorded by
# red_on_revert_loaded_exit; any other KeyboardInterrupt, such as an operator's real Ctrl-C landing in the
# vector's window, propagates.
LOADED_EXIT_INTERRUPT = "opf-init-p0-self-test-poisoned-revert"


def _is_interrupt(exc, sent):
    """True only for an exact KeyboardInterrupt whose args are exactly (sent,), read through exact built-in
    types alone, so a recorder never runs code from the caught instance (an args property or an argument's
    __eq__ that raises SystemExit 0, for example); any other KeyboardInterrupt propagates."""
    if type(exc) is not KeyboardInterrupt:
        return False
    args = exc.args
    return len(args) == 1 and type(args[0]) is str and args[0] == sent


def _interrupt_to_raise(exc):
    """The KeyboardInterrupt _propagate_interrupt raises for the caught KeyboardInterrupt `exc`, chosen by the
    exact class alone: `exc` itself, unchanged, when its class is exactly KeyboardInterrupt (what an
    operator's Ctrl-C raises); for a subclass, which only loaded code raises, a fresh KeyboardInterrupt with
    its context suppressed, so no code from the caught instance runs (a __notes__ property that raises
    SystemExit 0 while the interpreter reports it, for example). It raises nothing, so the vector over it
    catches no KeyboardInterrupt."""
    if type(exc) is KeyboardInterrupt:
        return exc
    fresh = KeyboardInterrupt()
    fresh.__suppress_context__ = True
    return fresh


def _propagate_interrupt(exc):
    """Raise what _interrupt_to_raise gives for the caught KeyboardInterrupt `exc`: an exact one propagates
    unchanged, and a subclass is never re-raised."""
    raise _interrupt_to_raise(exc)


def _interrupt_filter_outcomes(sent):
    """The interrupt filters over hostile inputs: _is_interrupt of an exact KeyboardInterrupt(sent), of a
    subclass whose args property returns (sent,), and of an exact KeyboardInterrupt whose argument is a str
    subclass or an object whose __eq__ is always true; whether _interrupt_to_raise gives an exact
    KeyboardInterrupt back unchanged and, for that subclass, a fresh exact KeyboardInterrupt with no args and
    its context suppressed; and the names of any instance code they ran. Nothing here raises or catches a
    KeyboardInterrupt, so an operator's Ctrl-C arriving here propagates unchanged (_real_sigint_propagates
    is red if it does not). Expected: (True, False, False, False, True, [])."""
    ran = []

    class _ArgsProperty(KeyboardInterrupt):
        @property
        def args(self):
            ran.append("args")
            return (sent,)

        def __str__(self):
            ran.append("str")
            return sent

    class _StrEq(str):
        def __eq__(self, other):
            ran.append("str-subclass-eq")
            return True

        __hash__ = str.__hash__

    class _AnyEq:
        def __eq__(self, other):
            ran.append("eq")
            return True

        __hash__ = object.__hash__

    outcomes = [_is_interrupt(KeyboardInterrupt(sent), sent), _is_interrupt(_ArgsProperty(), sent),
                _is_interrupt(KeyboardInterrupt(_StrEq(sent)), sent),
                _is_interrupt(KeyboardInterrupt(_AnyEq()), sent)]
    own = KeyboardInterrupt(sent)
    fresh = _interrupt_to_raise(_ArgsProperty())
    outcomes.append(_interrupt_to_raise(own) is own and type(fresh) is KeyboardInterrupt and fresh.args == ()
                    and fresh.__suppress_context__ is True)
    return tuple(outcomes) + (ran,)


# Run in a child by _real_sigint_propagates: load the file named first by path, make its first
# _interrupt_to_raise call deliver a real SIGINT to the child (as an operator's Ctrl-C does; a call outside the
# main thread exits 3 instead), then run _interrupt_filter_outcomes. The child must end by that interrupt.
_REAL_SIGINT_PROBE = """import importlib.util, os, signal, sys, threading
signal.signal(signal.SIGINT, signal.default_int_handler)
sys.path.insert(0, os.path.dirname(sys.argv[1]))
spec = importlib.util.spec_from_file_location("_real_sigint_probe_target", sys.argv[1])
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)
real = gate._interrupt_to_raise
def hooked(exc):
    gate._interrupt_to_raise = real
    if threading.current_thread() is not threading.main_thread():
        sys.exit(3)
    signal.raise_signal(signal.SIGINT)
    return real(exc)
gate._interrupt_to_raise = hooked
gate._interrupt_filter_outcomes(sys.argv[2])
print("returned")
"""


def _real_sigint_propagates(sent):
    """True when a real SIGINT delivered inside _interrupt_filter_outcomes, in a child process, ends that child
    as an uncaught KeyboardInterrupt. Red if anything there catches a KeyboardInterrupt in the main thread (the
    child then returns) or classifies in a worker thread (the hook exits 3 there). This process catches no
    KeyboardInterrupt here: an operator's Ctrl-C reaches subprocess.run, which re-raises it."""
    import signal
    import subprocess
    import tempfile
    with tempfile.TemporaryDirectory(prefix="interrupt-filter-sigint-") as tmp:
        probe = Path(tmp) / "probe.py"
        probe.write_text(_REAL_SIGINT_PROBE, encoding="utf-8")
        child = subprocess.run([sys.executable, "-I", "-B", str(probe), str(Path(__file__).resolve()), sent],
                               capture_output=True, text=True, timeout=120)
    return (child.returncode in (-signal.SIGINT, 130) and "returned" not in child.stdout
            and "KeyboardInterrupt" in child.stderr)


def _loaded_exit_outcome(source, body, f):
    """The class of what escapes red_on_revert over `source` with `body` appended, or None. Only
    LOADED_EXIT_INTERRUPT is recorded as KeyboardInterrupt; any other KeyboardInterrupt propagates."""
    try:
        red_on_revert(source + "\n" + body, f)
    except KeyboardInterrupt as exc:
        if not _is_interrupt(exc, LOADED_EXIT_INTERRUPT):
            _propagate_interrupt(exc)
        return KeyboardInterrupt
    except BaseException as exc:  # noqa: BLE001  any other escape is recorded, never this test's own end
        return type(exc)
    return None


def red_on_revert_loaded_exit(source, f):
    """A reverted source that ends the process at load is a harness failure through red_on_revert (the
    RuntimeError main reports as CANNOT-EVALUATE, exit 2), never an early exit with the loaded code's status;
    a KeyboardInterrupt propagates as itself. Red if the load guard in red_on_revert is removed, and red if
    the recorder records a KeyboardInterrupt other than the poisoned source's own."""
    for label, body, want in (("SystemExit(0)", "raise SystemExit(0)\n", RuntimeError),
                              ("SystemExit(None)", "raise SystemExit\n", RuntimeError),
                              ("GeneratorExit", "raise GeneratorExit\n", RuntimeError),
                              ("BaseException subclass", "class B(BaseException):\n    pass\nraise B()\n",
                               RuntimeError),
                              ("KeyboardInterrupt", "raise KeyboardInterrupt({!r})\n".format(LOADED_EXIT_INTERRUPT),
                               KeyboardInterrupt),
                              ("KeyboardInterrupt subclass that exits 0",
                               HOSTILE_STR_BODY.format("KeyboardInterrupt", "_StrExits()") + "raise _StrExits()\n",
                               RuntimeError)):
        check(_loaded_exit_outcome(source, body, f) is want, "revert/loaded-exit/" + label)
        print("PASS revert/loaded-exit/" + label)
    # Any other KeyboardInterrupt (here one carrying another value, standing in for a real Ctrl-C) propagates.
    other = LOADED_EXIT_INTERRUPT + "-other"
    try:
        _loaded_exit_outcome(source, "raise KeyboardInterrupt({!r})\n".format(other), f)
    except KeyboardInterrupt as exc:
        if not _is_interrupt(exc, other):
            _propagate_interrupt(exc)
        propagated = True
    else:
        propagated = False
    check(propagated, "revert/loaded-exit/other-interrupt-not-recorded")
    print("PASS revert/loaded-exit/other-interrupt-not-recorded")
    # The recorder filters run no code from the caught instance. Red if _is_interrupt reads an args property
    # or compares through an argument's __eq__, or _propagate_interrupt re-raises a subclass.
    check(_interrupt_filter_outcomes(LOADED_EXIT_INTERRUPT) == (True, False, False, False, True, []),
          "revert/interrupt-filters-run-no-instance-code")
    print("PASS revert/interrupt-filters-run-no-instance-code")
    # A real SIGINT inside the interrupt filters ends the run as an interrupt. Red if a probe there catches it.
    check(_real_sigint_propagates(LOADED_EXIT_INTERRUPT), "revert/interrupt-filter-real-sigint-propagates")
    print("PASS revert/interrupt-filter-real-sigint-propagates")


# Appended to a reverted source: every public function the reverted module defines is replaced by one that
# raises the expression formatted in second, after a class _StrExits of the base formatted in first whose
# __str__ (and so format and repr), __eq__ and __notes__ raise SystemExit(0). A handler that stringifies,
# formats or compares the caught instance, or re-raises a KeyboardInterrupt subclass as itself (the
# interpreter reads its __notes__ when it reports it), then ends the gate with status 0.
HOSTILE_STR_BODY = (
    "class _StrExits({}):\n"
    "    def __str__(self):\n"
    "        raise SystemExit(0)\n"
    "\n"
    "    __repr__ = __str__\n"
    "\n"
    "    def __eq__(self, other):\n"
    "        raise SystemExit(0)\n"
    "\n"
    "    __hash__ = object.__hash__\n"
    "\n"
    "    @property\n"
    "    def __notes__(self):\n"
    "        raise SystemExit(0)\n"
    "\n\n"
    "def _raise_str_exits(*args, **kwargs):\n"
    "    raise {}\n"
    "\n\n"
    "for _n, _v in list(globals().items()):\n"
    "    if type(_v) is type(_raise_str_exits) and _v.__module__ == __name__ and not _n.startswith('_'):\n"
    "        globals()[_n] = _raise_str_exits\n")

# (label, the base of _StrExits, what a call into the reverted source raises).
HOSTILE_STR_CASES = (("AssertionError", "AssertionError", "_StrExits()"),
                     ("Exception", "Exception", "_StrExits()"),
                     ("KeyboardInterrupt", "KeyboardInterrupt", "_StrExits()"),
                     ("assertion-with-hostile-argument", "object", "AssertionError(_StrExits())"))


def red_on_revert_hostile_str(source, f):
    """Each HOSTILE_STR_CASES call into the reverted source is a harness failure, exit 2 from main itself,
    never exit 0 from a handler that stringifies, formats or compares the caught instance or re-raises it:
    an AssertionError subclass, an exact AssertionError whose argument is not an exact str and a
    KeyboardInterrupt subclass each make red_on_revert raise its fixed RuntimeError, and an Exception subclass
    reaches main's handler, which gives 2 through _failure_status without reading it. Each runs through main
    (its fixtures replaced, for that one run, by red_on_revert over the hostile source), so it is red if
    red_on_revert, main's handler or _failure_status runs code from the caught instance."""
    import contextlib
    import io
    names = globals()
    real_fixtures = names["fixtures"]
    for label, base, raised in HOSTILE_STR_CASES:
        reached = []

        def _hostile_fixtures(hostile=source + "\n" + HOSTILE_STR_BODY.format(base, raised), label=label):
            reached.append(True)
            red_on_revert(hostile, f)
            raise AssertionError("revert/hostile-str/" + label + "/not-raised")
        names["fixtures"] = _hostile_fixtures
        try:
            # fixtures() is main's first call inside its handler and never returns here, so no flag is read
            # before the escape reaches the handler; --vectors-only keeps main in its narrowest mode regardless.
            with contextlib.redirect_stderr(io.StringIO()):
                got = main(["--vectors-only"])
        except KeyboardInterrupt as exc:
            if type(exc) is KeyboardInterrupt:
                raise
            got = "a KeyboardInterrupt subclass"  # recorded, never re-raised; only loaded code raises one
        except BaseException as escaped:  # noqa: BLE001  recorded, never this test's own end
            got = type(escaped)
        finally:
            names["fixtures"] = real_fixtures
        check(reached == [True], "revert/hostile-str/" + label + "/fixtures-not-reached")
        check(type(got) is int and got == 2, "revert/hostile-str/" + label)
        print("PASS revert/hostile-str/" + label)


# Prefixed to the runner text in place of a python3 shell function. A
# read-only PATH pins executable lookup for the runner's own shell, so bare
# python3 keeps resolving to the executable fixture.
RUNNER_PATH_PIN = "readonly PATH\n"


def runner_check(expected, text=None, *, fail_own=0):
    import errno
    import fcntl
    import os
    import shlex
    import shutil
    import signal
    import stat
    import tempfile
    from contextlib import ExitStack

    identity = "runner/declared-test-executes"
    # Vector-only entry points never call this registration check.
    # Refuse an escaped fixture invocation before any shell can launch.
    if "p0_log" in os.environ:
        raise RuntimeError(identity + "/cannot-evaluate/recursion")
    # env -i removes the environment marker, but preserves pass_fds. Both
    # twins recognize the same private-file payload, without an env-supplied
    # descriptor number. /dev/fd must be enumerable; inspection errors refuse.
    marker_bytes = b"OPF runner registration recursion v1\n"
    try:
        for entry in os.listdir("/dev/fd"):
            fd = int(entry)
            try:
                info = os.fstat(fd)
            except OSError as exc:
                # The descriptor used to list /dev/fd has already closed.
                if exc.errno == errno.EBADF:
                    continue
                raise
            if stat.S_ISREG(info.st_mode) and info.st_size == len(marker_bytes):
                # Admit only readable descriptors. O_PATH has O_RDONLY access
                # bits but cannot be read. A failed flag query still refuses.
                flags = fcntl.fcntl(fd, fcntl.F_GETFL)
                if (flags & getattr(os, "O_PATH", 0)
                        or flags & os.O_ACCMODE not in (os.O_RDONLY, os.O_RDWR)):
                    continue
                # Readable candidates still fail closed on inspection errors.
                if os.pread(fd, len(marker_bytes), 0) == marker_bytes:
                    raise RuntimeError(identity + "/cannot-evaluate/recursion")
    except (OSError, ValueError) as exc:
        raise RuntimeError(identity + "/cannot-evaluate/recursion-marker") from exc
    if type(fail_own) is not int or fail_own not in (0, 1, 2, 7):
        raise ValueError(identity + "/invalid-failure-code")
    here = Path(__file__).resolve().parent
    runner = here / "run_all_checks.sh"
    source = runner.read_text(encoding="utf-8") if text is None else text
    bash = shutil.which("bash")
    if bash is None:
        raise RuntimeError(identity + "/cannot-evaluate/bash")
    bash = os.path.abspath(bash)
    # The on-disk runner is the call roster, including for a mutated source.
    # This parser covers its one-line run_gate registrations, not general
    # shell; malformed registrations or unresolved dollars refuse before launch.
    own_argv = tuple(os.fsencode(arg) for arg in (
        "-I", "-B", str(here / "check_opf_init_p0.py"), "--self-test", "--red-on-revert"))
    roster = []
    try:
        for line in runner.read_text(encoding="utf-8").splitlines():
            if line.lstrip().startswith("run_gate "):
                words = [word.replace("$here", str(here)) for word in shlex.split(line)]
                if (any("$" in word for word in words)
                        or len(words) < 6 or words[2:5] != ["python3", "-I", "-B"]):
                    raise ValueError(line)
                roster.append(tuple(os.fsencode(word) for word in words[3:]))
    except (OSError, ValueError) as exc:
        raise RuntimeError(identity + "/cannot-evaluate/roster") from exc
    if roster.count(own_argv) != 1:
        raise RuntimeError(identity + "/cannot-evaluate/roster")

    # This self-test asserts that the runner dispatches THIS suite exactly
    # once with its exact argv (RED duplicate-own-call and wrong-own-argv,
    # using the runtime argv log), and propagates exits 1, 2 and 7 (RED
    # own-suite-failure variants, discriminated by swallowed-own-failure
    # and tolerate-1/tolerate-2). Other nonzero statuses are not injected.
    # Every recorded call is also reconciled against the on-disk run_gate
    # roster (/invocations; RED unexpected-call and dropped-sibling): each
    # registered gate once, in order, with its exact argv, and no other
    # python3 call. Sibling suites are not run, so their own assertions
    # remain outside this check.
    # Fix-4 accounting: in-runner PATH changes remain covered, now by the
    # read-only PATH pin rather than a shell function (PASS in-runner-path;
    # RED removed-path-pin, which removes the pin). No function or
    # alias stands in for python3. Shells: bash --noprofile --norc runs the
    # runner, /bin/sh the fixture. The PATH fixture also covers child
    # shells, command and env forms, each run as a dispatch route
    # (PASS route-command, route-env, route-child-shell).
    # PD-SFS-THREAT-BOUND: runners written to defeat this check are outside
    # scope, including wrappers recognizing injected output and swallowing
    # real SELF-TEST FAIL, PACK-MANIFEST FAIL or CANNOT diagnostics, and bare
    # alternate interpreter names such as python3.14. No mechanism covers them.
    # Absolute paths, command -p, hash -p, and a PATH replaced in a child
    # process (env PATH=... or a child shell that reassigns it) remain outside
    # interception: the pin binds only the runner's own shell. This runner
    # does not set -e: a rejected PATH assignment can abort the rest of its
    # line while later lines continue; a prefix assignment runs the command
    # with the pinned PATH. Skipped or unintercepted registered calls fail
    # invocation reconciliation; this detects bypasses, not prevents them.
    # Deliberately closing inherited descriptors
    # while clearing the environment can bypass recursion refusal and spawn
    # nested sessions outside timeout killpg containment. Not a process sandbox.
    # Intercepted siblings return 0; their failure propagation is outside
    # this check too.
    # The runner, its fixtures, this harness, and the fixed mutations exercised
    # here launch no route to python3 other than the executable fixtures or the
    # declared interpreter by absolute path on declared suite inputs (their
    # other launches: bash, /bin/sh running the fixture, env, and dirname). The
    # forwarded --vectors-only run and the modules it imports, themselves
    # declared suite inputs, take no process-launching or service route on that
    # path, and the environment filter below drops credential carriers from the
    # passed environment. This bounded code review, not PATH interception
    # alone, is the basis for omitting isolation.
    fixture = r'''#!/bin/sh
printf '%s\0' "$#" "$@" >> "$p0_log" || exit 2
if [ "$#" -eq 5 ] && [ "$1" = "-I" ] && [ "$2" = "-B" ] \
    && [ "$3" = "$p0_test" ] && [ "$4" = "--self-test" ] \
    && [ "$5" = "--red-on-revert" ]; then
  if [ "$p0_fail_own" -ne 0 ]; then
    "$p0_python" -I -B "$p0_test" --self-test --vectors-only || exit "$?"
    exit "$p0_fail_own"
  fi
  exec "$p0_python" -I -B "$p0_test" --self-test --vectors-only
fi
case " $* " in *check_opf_init_p0.py*) exit 2;; esac
exit 0
'''
    # Preserve ordinary caller variables (including CI) so conditional
    # dispatch is exercised. Remove the execution controls and the credential
    # carriers matched below, then pin configuration and fixture variables;
    # not an environment sandbox: only the named patterns are dropped.
    # Match main's caller-cwd dispatch while keeping fixture files private.
    caller_cwd = Path.cwd()
    with tempfile.TemporaryDirectory(prefix="opf-p0-registration-") as tmp, ExitStack() as resources:
        os.chmod(tmp, 0o700)
        marker = resources.enter_context(tempfile.TemporaryFile(dir=tmp))
        marker.write(marker_bytes)
        marker.flush()
        if os.pathsep in tmp:
            raise RuntimeError(identity + "/cannot-evaluate/pathsep")
        executable = Path(tmp) / "python3"
        executable.write_text(fixture, encoding="utf-8")
        executable.chmod(0o700)
        log = Path(tmp) / "argv.log"
        log.write_bytes(b"")
        log.chmod(0o600)
        env = {name: value for name, value in os.environ.items()
               if name not in ("BASH_ENV", "ENV", "SHELLOPTS", "BASHOPTS", "PS4")
               and not name.upper().startswith(("GIT_", "BASH_FUNC_", "PYTHON", "LD_",
                                                "SSH_", "AWS_", "GPG_"))
               and not any(marker in name.upper() for marker in
                           ("TOKEN", "SECRET", "PASSWORD", "PASSPHRASE",
                            "CREDENTIAL", "APIKEY", "API_KEY", "ACCESS_KEY",
                            "AUTH"))}
        env.update({name: tmp for name in env if name.startswith("XDG_")})
        env.update({"PATH": tmp + os.pathsep + os.defpath, "TMPDIR": tmp,
                    "HOME": tmp, "XDG_CONFIG_HOME": tmp, "XDG_CACHE_HOME": tmp,
                    "XDG_DATA_HOME": tmp, "XDG_STATE_HOME": tmp,
                    "XDG_CONFIG_DIRS": tmp, "XDG_DATA_DIRS": tmp,
                    "XDG_RUNTIME_DIR": tmp, "LC_ALL": "C",
                    "PYTHONDONTWRITEBYTECODE": "1", "p0_log": str(log),
                    "p0_python": sys.executable,
                    "p0_test": str(here / "check_opf_init_p0.py"),
                    "p0_fail_own": str(fail_own)})

        def run_shell(body):
            with subprocess.Popen(
                    [bash, "--noprofile", "--norc", "-c", body, str(runner)],
                    cwd=caller_cwd, env=env, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, text=True, start_new_session=True,
                    pass_fds=(marker.fileno(),)) as proc:
                try:
                    stdout, stderr = proc.communicate(timeout=30)
                except subprocess.TimeoutExpired:
                    # Kill descendants even when the shell itself has exited.
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    try:
                        proc.communicate(timeout=5)
                    except subprocess.TimeoutExpired:
                        # An escaped session may still hold a pipe. Closing it
                        # bounds collection; such processes are not contained.
                        proc.stdout.close()
                        proc.stderr.close()
                    raise RuntimeError(identity + "/cannot-evaluate/timeout") from None
                return subprocess.CompletedProcess(proc.args, proc.returncode, stdout, stderr)

        # Probe with exactly the runner's cwd, flags and environment. A noexec
        # fixture or unusable PATH must never fall through to the real gates.
        # Requires dirname on this PATH and executable /bin/sh; absence
        # fails closed as cannot-evaluate/interception, never a clean skip.
        probe = run_shell("type -P dirname >/dev/null && test -x /bin/sh && "
                          "type -P python3")
        if probe.returncode != 0 or probe.stdout != str(executable) + "\n":
            raise RuntimeError(identity + "/cannot-evaluate/interception")
        # No function stands in for python3: the pin keeps bare lookup on
        # the executable fixture even when the runner assigns PATH itself.
        proc = run_shell(RUNNER_PATH_PIN + source)
        try:
            argv_log = log.read_bytes()
        except OSError as exc:
            raise RuntimeError(identity + "/cannot-evaluate/argv-log") from exc

    # NUL-framed records begin with argc. Any argument containing THIS
    # basename counts as an own-call attempt, even embedded -c source; require
    # one exact argv. Incidental mentions are conservatively attempts too.
    # Dynamically constructed names without that substring are not classified.
    # Diagnose malformed own calls before their exit or missing output.
    # Absent calls retain the existing return-code/pass-lines identities.
    fields = argv_log.split(b"\0")
    if fields.pop() != b"":
        raise AssertionError(identity + "/own-argv")
    own = []
    calls = []
    offset = 0
    basename = os.fsencode(Path(env["p0_test"]).name)
    while offset < len(fields):
        count = fields[offset]
        offset += 1
        try:
            argc = int(count)
        except ValueError as exc:
            raise AssertionError(identity + "/own-argv") from exc
        if count != str(argc).encode("ascii") or argc < 0 or argc > len(fields) - offset:
            raise AssertionError(identity + "/own-argv")
        argv = tuple(fields[offset:offset + argc])
        offset += argc
        calls.append(argv)
        if any(basename in arg for arg in argv):
            own.append(argv)
    if own and own != [own_argv]:
        raise AssertionError(identity + "/own-argv")

    reached = tuple(line[5:] for line in proc.stdout.splitlines() if line.startswith("PASS "))
    check(proc.returncode == 0, identity + "/return-code")
    check(reached == expected, identity + "/pass-lines")

    if own != [own_argv]:
        raise AssertionError(identity + "/own-argv")
    # Missing, extra, reordered or re-argued sibling calls are unexpected
    # invocation evidence, even when every earlier identity passed.
    if calls != roster:
        raise AssertionError(identity + "/invocations")
    return roster


def runner_routes(identity):
    import os
    import shlex
    import shutil

    # Declared dispatch routes beyond run_gate's direct "$@": command
    # (which bypasses shell functions), env, and a child bash process.
    # Each reaches python3 by PATH lookup, so only the fixture can answer.
    runner = Path(__file__).resolve().parent / "run_all_checks.sh"
    source = runner.read_text(encoding="utf-8")
    dispatch = 'if "$@"; then'
    if source.count(dispatch) != 1:
        raise AssertionError(identity + "/red-fixture")
    bash = shutil.which("bash")
    if bash is None:
        raise RuntimeError(identity + "/cannot-evaluate/bash")
    child = shlex.quote(os.path.abspath(bash)) + """ --noprofile --norc -c 'exec "$@"' _"""
    for route, prefix in (("command", "command"), ("env", "env"),
                          ("child-shell", child)):
        yield route, source.replace(dispatch, "if " + prefix + ' "$@"; then', 1)


def _runner_non_readable_fd_checks():
    import os
    import subprocess
    import tempfile

    kinds = [("write-only", os.O_WRONLY | os.O_APPEND)]
    if hasattr(os, "O_PATH"):
        kinds.append(("path", os.O_PATH))
    # Run the full self-test once per non-readable inherited 37-byte fd kind.
    # Suppress only this self-spawning helper in each child; keep its other
    # vectors, registration checks and REDs enabled.
    for kind, flags in kinds:
        identity = "runner/declared-test-executes/inherited-" + kind + "-fd"
        with tempfile.TemporaryDirectory(prefix="opf-" + kind + "-fd-") as tmp:
            os.chmod(tmp, 0o700)
            log = Path(tmp) / "ordinary.log"
            log.write_bytes(b"x" * 37)
            log.chmod(0o600)
            fd = os.open(log, flags)
            try:
                child = (
                    "import fcntl, importlib, os, sys\n"
                    "from pathlib import Path\n"
                    "from unittest.mock import patch\n"
                    f"assert os.fstat({fd}).st_size == 37\n"
                    f"flags = fcntl.fcntl({fd}, fcntl.F_GETFL)\n"
                    f"assert flags & os.O_ACCMODE == {flags & os.O_ACCMODE}\n"
                    f"assert flags & {flags} == {flags}\n"
                    "sys.argv = sys.argv[1:]\n"
                    "sys.path.insert(0, str(Path(sys.argv[0]).parent))\n"
                    "module = importlib.import_module(Path(sys.argv[0]).stem)\n"
                    "with patch.object(module, '_runner_non_readable_fd_checks'):\n"
                    "    raise SystemExit(module.main())\n")
                try:
                    # Deliberate fast-fail ceiling for this regression child.
                    proc = subprocess.run(
                        [sys.executable, "-I", "-B", "-c", child,
                         str(Path(__file__).resolve()), "--self-test", "--red-on-revert"],
                        pass_fds=(fd,), capture_output=True, text=True, timeout=300)
                except Exception as exc:
                    print(identity + ": child launch failed: {!r}".format(exc), file=sys.stderr)
                    raise AssertionError(identity) from exc
                if proc.returncode != 0:
                    # Surface the child's own diagnostics; the identity stays exact.
                    print(identity + ": child rc={}\n{}".format(proc.returncode, proc.stderr),
                          file=sys.stderr)
                    raise AssertionError(identity)
            finally:
                os.close(fd)
        print("PASS " + identity)


def runner_red_checks(expected):
    import os
    import shlex
    import subprocess
    import tempfile
    from unittest.mock import patch

    runner = Path(__file__).resolve().parent / "run_all_checks.sh"
    source = runner.read_text(encoding="utf-8")
    identity = "runner/declared-test-executes"
    anchor = '  local name="$1"; shift\n'
    if source.count(anchor) != 1:
        raise AssertionError(identity + "/red-fixture")

    def red(label, call, error, wanted):
        try:
            call()
        except error as exc:
            if str(exc) != wanted:
                raise AssertionError(identity + "/" + label + "/wrong-red") from exc
        except Exception as exc:
            raise AssertionError(identity + "/" + label + "/wrong-error") from exc
        else:
            raise AssertionError(identity + "/" + label + "/not-red")
        print("RED {} -> {}".format(label, wanted))

    # Exercise the actual environment passed to the runner, using synthetic
    # values only. Check lowercase and mixed-case credential names as well as
    # uppercase names, while preserving ordinary conditional-dispatch inputs.
    credentials = ("GH_TOKEN", "api_token", "GitHub_ToKeN", "client_secret",
                   "db_password", "key_passphrase", "cloud_credential",
                   "service_apikey", "service_api_key", "service_access_key",
                   "npm_config__authToken", "ssh_agent", "Aws_Profile", "gpg_home")
    popen = subprocess.Popen

    def checked_environment(*args, **kwargs):
        env = kwargs["env"]
        if any(name in env for name in credentials):
            raise AssertionError(identity + "/credential-environment")
        if env.get("CI") != "true" or env.get("BUILD_NUMBER") != "fixture-build":
            raise AssertionError(identity + "/ordinary-environment")
        return popen(*args, **kwargs)

    with patch.dict(os.environ, dict.fromkeys(credentials, "synthetic-only") | {
            "CI": "true", "BUILD_NUMBER": "fixture-build"}), \
            patch("subprocess.Popen", side_effect=checked_environment) as launch:
        runner_check(expected)
        if not launch.called:
            raise AssertionError(identity + "/environment-not-exercised")
    print("PASS " + identity + "/credential-environment")

    # Unsupported expansion in any registration word refuses before launch.
    original_read_text = Path.read_text
    for label, old, new in (
        ("roster-braced-here", "$here/", "${here}/"),
        ("roster-variable", "$here/", "$other/"),
        ("roster-name-variable", "opf-homes-selftest", "opf-$other-selftest"),
    ):
        roster_source = source.replace(old, new, 1)
        if roster_source == source:
            raise AssertionError(identity + "/red-fixture")

        def read_roster(path, *args, **kwargs):
            if path == runner:
                return roster_source
            return original_read_text(path, *args, **kwargs)

        with patch.object(Path, "read_text", read_roster), \
                patch("subprocess.Popen", side_effect=AssertionError(
                    identity + "/roster/unexpected-launch")) as launch:
            red(label, lambda: runner_check(expected), RuntimeError,
                identity + "/cannot-evaluate/roster")
            if launch.called:
                raise AssertionError(identity + "/roster/unexpected-launch")

    # Set CI in the caller, not in the constructed runner environment.
    with patch.dict(os.environ, {"CI": "true"}):
        red("ci-conditional-skip", lambda: runner_check(
            expected, source.replace(
                anchor, anchor + '  case "${CI:-}:$name" in '
                '?*:opf-init-p0-selftest) return 0;; esac\n', 1)),
            AssertionError, identity + "/pass-lines")

    # Exercise the lost main case from the repository (or standalone) root.
    from contextlib import chdir
    root = runner.parents[2]
    relative_runner = runner.relative_to(root)
    with chdir(root):
        if not relative_runner.is_file():
            raise AssertionError(identity + "/cwd-fixture")
        red("cwd-conditional-skip", lambda: runner_check(
            expected, source.replace(
                anchor, anchor + '  if [ -e ' + shlex.quote(str(relative_runner)) + ' ]; then '
                'case "$name" in opf-init-p0-selftest) return 0;; esac; fi\n', 1)),
            AssertionError, identity + "/pass-lines")

    # Exit from the dispatcher before the runner can report success.
    red("return-code", lambda: runner_check(
        expected, source.replace(anchor, anchor + "  exit 1\n", 1)),
        AssertionError, identity + "/return-code")

    # Run the real vector leg, then fail THIS suite inside the fixture.
    def own_failure(text, status=7):
        label = "own-suite-failure" if status == 7 else "own-suite-failure-" + str(status)
        red(label, lambda: runner_check(
            expected, text, fail_own=status), AssertionError, identity + "/return-code")

    for status in (1, 2, 7):
        own_failure(source, status)
    propagation = ('  if "$@"; then :; else\n'
                   '    local rc=$?\n'
                   '    failed=1\n'
                   '    failed_names="${failed_names:+${failed_names}, }${name}"\n'
                   '    echo "GATE FAILED: ${name} (exit ${rc})"\n'
                   '  fi')
    if source.count(propagation) != 1:
        raise AssertionError(identity + "/red-fixture")

    # The failure RED must go not-red if run_gate swallows the suite's exit.
    red("swallowed-own-failure", lambda: own_failure(
        source.replace(propagation, '  "$@" || true', 1)),
        AssertionError, identity + "/own-suite-failure/not-red")

    # Selective wrappers must defeat the corresponding failure RED.
    for status in (1, 2):
        wrapper = ('tolerate_own() { "$@"; local rc=$?; if [ "$rc" -eq '
                   + str(status) + ' ]; then return 0; fi; return "$rc"; }\n')
        mutant = wrapper + source.replace(
            anchor, anchor + '  set -- tolerate_own "$@"\n', 1)
        red("tolerate-" + str(status),
            lambda: own_failure(mutant, status), AssertionError,
            identity + "/own-suite-failure-" + str(status) + "/not-red")

    # Suppressed output cannot hide additional or re-argued own-suite calls.
    for label, command in (
        ("duplicate-own-call", '  "$@" >/dev/null 2>&1'),
        ("wrong-own-argv", '  "$@" --unexpected >/dev/null 2>&1 || true'),
        ("embedded-own-call", '  python3 -I -B -c "import runpy; '
         "runpy.run_path('$here/check_opf_init_p0.py', run_name='__main__')"
         '" --self-test --red-on-revert >/dev/null 2>&1 || true'),
    ):
        red(label, lambda command=command: runner_check(
            expected, source.replace(propagation, propagation + "\n" + command, 1)),
            AssertionError, identity + "/own-argv")

    # Drop a required flag from exactly this suite's registration.
    own_lines = [line for line in source.splitlines(keepends=True)
                 if line.startswith('run_gate "opf-init-p0-selftest"')]
    if len(own_lines) != 1 or own_lines[0].count(" --red-on-revert") != 1:
        raise AssertionError(identity + "/red-fixture")
    red("dropped-own-flag", lambda: runner_check(
        expected, source.replace(own_lines[0],
                                 own_lines[0].replace(" --red-on-revert", "", 1), 1)),
        AssertionError, identity + "/own-argv")

    # Extra or missing sibling calls are unexpected invocation evidence.
    siblings = [line for line in source.splitlines(keepends=True)
                if line.startswith("run_gate ") and line not in own_lines]
    if not siblings:
        raise AssertionError(identity + "/red-fixture")
    for label, text in (
        ("unexpected-call", source.replace(
            propagation, propagation + "\n  python3 -I -B -c pass >/dev/null 2>&1 || true", 1)),
        ("dropped-sibling", source.replace(siblings[0], "", 1)),
    ):
        red(label, lambda text=text: runner_check(expected, text),
            AssertionError, identity + "/invocations")

    # Presence, including an empty value, must refuse before any bash launch.
    for value in ("", "nested-argv.log"):
        with patch.dict(os.environ, {"p0_log": value}):
            with patch("subprocess.Popen", side_effect=AssertionError(
                    identity + "/recursion/unexpected-launch")) as launch:
                red("nested-invocation", lambda: runner_check(expected), RuntimeError,
                    identity + "/cannot-evaluate/recursion")
                if launch.call_count:
                    raise AssertionError(identity + "/recursion/unexpected-launch")

    # Scrub only our registration's environment. Substitute a bounded entry
    # probe for the suite script: it loads the real runner_check, but forbids
    # every nested Popen even if the recursion guard is reverted. A nonzero
    # outer runner exit alone is insufficient: require the exact refusal and
    # zero launch attempts recorded by the child after env -i.
    # Replacing sys.executable with PATH-selected python3 is non-discriminating
    # on a host where sys.executable is /usr/bin/python3; this case alone
    # does not verify alternate-interpreter selection on that host.
    with tempfile.TemporaryDirectory(prefix="opf-recursion-red-") as tmp:
        os.chmod(tmp, 0o700)
        report = Path(tmp) / "refusal.txt"
        nested = Path(tmp) / Path(__file__).name
        nested.write_text(
            "import os, runpy\n"
            "from pathlib import Path\n"
            "from unittest.mock import patch\n"
            f"scope = runpy.run_path({str(Path(__file__).resolve())!r})\n"
            f"assert {'p0_log'!r} not in os.environ\n"
            "with patch('subprocess.Popen', side_effect=AssertionError('nested launch')) as launch:\n"
            "    try:\n"
            "        scope['runner_check'](())\n"
            "    except RuntimeError as exc:\n"
            "        assert not launch.called\n"
            f"        Path({str(report)!r}).write_text(str(exc), encoding='utf-8')\n"
            "    else:\n"
            "        raise AssertionError('recursion accepted')\n"
            "raise SystemExit(2)\n", encoding="utf-8")
        nested.chmod(0o600)
        own_line = own_lines[0]
        script_arg = '"$here/check_opf_init_p0.py"'
        if own_line.count("python3 ") != 1 or own_line.count(script_arg) != 1:
            raise AssertionError(identity + "/red-fixture")
        scrubbed = own_line.replace(
            "python3 ", "env -i PATH=/usr/bin:/bin " + shlex.quote(sys.executable) + " ",
            1).replace(script_arg, shlex.quote(str(nested)), 1)
        refusal_identity = identity + "/scrubbed-environment/wrong-refusal"
        try:
            try:
                runner_check(expected, source.replace(own_line, scrubbed, 1))
            except AssertionError as exc:
                if str(exc) != identity + "/return-code":
                    raise
            else:
                raise AssertionError("scrubbed runner accepted")
            refusal = report.read_text(encoding="utf-8")
        except Exception as exc:
            # Missing/unreadable reports and unexpected runner outcomes are
            # failures of this RED, not harness cannot-evaluate outcomes.
            raise AssertionError(refusal_identity) from exc
        if refusal != identity + "/cannot-evaluate/recursion":
            raise AssertionError(refusal_identity)
        # The child writes this exact report only after asserting zero Popen
        # attempts. Announce the RED only once that evidence has been read.
        print("RED scrubbed-environment -> " + identity + "/cannot-evaluate/recursion")
        print("PASS " + identity + "/scrubbed-environment/no-nested-launch")

    # A harmless competing executable makes removing the PATH pin safe.
    # Check its own evidence too: zero calls with the pin, the complete
    # on-disk roster without it. Exact NUL framing rejects malformed logs.
    with tempfile.TemporaryDirectory(prefix="opf-runner-path-") as tmp:
        os.chmod(tmp, 0o700)
        if os.pathsep in tmp:
            raise RuntimeError(identity + "/cannot-evaluate/pathsep")
        competing_log = Path(tmp) / "competing-argv.log"
        competing_log.write_bytes(b"")
        competing_log.chmod(0o600)
        stub = Path(tmp) / "python3"
        stub.write_text(
            "#!/bin/sh\nprintf '%s\\0' \"$#\" \"$@\" >> "
            + shlex.quote(str(competing_log)) + " || exit 2\nexit 0\n", encoding="utf-8")
        stub.chmod(0o700)

        def check_competing_log(calls):
            try:
                actual = competing_log.read_bytes()
            except OSError as exc:
                raise RuntimeError(identity + "/cannot-evaluate/competing-argv-log") from exc
            wanted = b"".join(
                b"\0".join((str(len(argv)).encode("ascii"), *argv)) + b"\0"
                for argv in calls)
            if actual != wanted:
                raise AssertionError(identity + "/competing-invocations")

        changed_path = "PATH=" + shlex.quote(tmp) + ":$PATH\n" + source
        roster = runner_check(expected, changed_path)
        check_competing_log([])
        print("PASS " + identity + "/in-runner-path")

        original_popen = subprocess.Popen
        removed = 0

        def without_pin(*args, **kwargs):
            nonlocal removed
            command = list(args[0])
            if command[4].startswith(RUNNER_PATH_PIN):
                command[4] = command[4][len(RUNNER_PATH_PIN):]
                removed += 1
            return original_popen(command, *args[1:], **kwargs)

        with patch("subprocess.Popen", side_effect=without_pin):
            red("removed-path-pin", lambda: runner_check(expected, changed_path),
                AssertionError, identity + "/pass-lines")
        if removed != 1:
            raise AssertionError(identity + "/in-runner-path/removed-count")

        check_competing_log(roster)
        print("PASS " + identity + "/removed-path-pin/invocations")
        # The empty-log assertion discriminates: without the pin, calls appear.
        red("competing-log-not-empty", lambda: check_competing_log([]),
            AssertionError, identity + "/competing-invocations")
        competing_log.unlink()
        red("missing-competing-log", lambda: check_competing_log(roster),
            RuntimeError, identity + "/cannot-evaluate/competing-argv-log")
        competing_log.write_bytes(b"malformed")
        red("malformed-competing-log", lambda: check_competing_log(roster),
            AssertionError, identity + "/competing-invocations")

    # Remove every execute bit, including for root. Permit only the probe:
    # a reverted interception guard must never launch the real runner.
    original_chmod = Path.chmod
    original_popen = subprocess.Popen
    launches = 0
    probe_body = ("type -P dirname >/dev/null && test -x /bin/sh && "
                  "type -P python3")

    def non_executable(path, mode, *args, **kwargs):
        if path.name == "python3":
            mode = 0o600
        return original_chmod(path, mode, *args, **kwargs)

    def probe_only(*args, **kwargs):
        nonlocal launches
        launches += 1
        command = args[0] if args else kwargs.get("args", ())
        if (launches > 1 or len(command) != 6 or command[3] != "-c"
                or command[4] != probe_body):
            raise AssertionError(identity + "/interception/unexpected-launch")
        return original_popen(*args, **kwargs)

    with patch.object(Path, "chmod", non_executable), \
            patch("subprocess.Popen", side_effect=probe_only):
        red("non-executable-fixture", lambda: runner_check(expected), RuntimeError,
            identity + "/cannot-evaluate/interception")
        if launches != 1:
            raise AssertionError(identity + "/interception/launch-count")

    # Remove dirname from the probe's PATH while retaining the fixture.
    # The exact-body/launch-count guard also prevents a bypassed probe from
    # reaching a real runner when this interception check is reverted.
    launches = 0

    def without_dirname(*args, **kwargs):
        kwargs["env"] = dict(kwargs["env"], PATH=kwargs["env"]["TMPDIR"])
        return probe_only(*args, **kwargs)

    with patch("subprocess.Popen", side_effect=without_dirname):
        red("missing-dirname", lambda: runner_check(expected), RuntimeError,
            identity + "/cannot-evaluate/interception")
        if launches != 1:
            raise AssertionError(identity + "/interception/launch-count")

    # Discriminates only where TMPDIR has no default ACL and the process
    # lacks CAP_DAC_OVERRIDE (root commonly has it in CI containers).
    # Either a default ACL overriding umask or that capability makes this
    # case non-discriminating: it can pass with or without the chmods.
    saved = os.umask(0o200)
    try:
        try:
            runner_check(expected)
        except Exception as exc:
            raise AssertionError(identity + "/umask-0200") from exc
    finally:
        os.umask(saved)
    print("PASS " + identity + "/umask-0200")

    # Block every launch so a reverted guard cannot execute a real gate.
    # Reset tempfile's cache as well as TMPDIR to exercise this exact directory.
    with tempfile.TemporaryDirectory(prefix="opf-path" + os.pathsep) as tmp:
        with patch.dict(os.environ, {"TMPDIR": tmp}), patch.object(tempfile, "tempdir", tmp):
            with patch("subprocess.Popen", side_effect=AssertionError(
                    identity + "/pathsep/unexpected-launch")) as launch:
                red("tmpdir-pathsep", lambda: runner_check(expected), RuntimeError,
                    identity + "/cannot-evaluate/pathsep")
                if launch.call_count:
                    raise AssertionError(identity + "/pathsep/unexpected-launch")

    _runner_non_readable_fd_checks()


def _failure_status(exc):
    """main's status for an Exception escaping the harness, with its stderr line: 1 (SELF-TEST FAIL) for an
    exact AssertionError, 2 (CANNOT-EVALUATE) for any other. The line carries this harness's own text only
    when exc is an exact AssertionError or RuntimeError (what this module raises) whose one argument is an
    exact str; any other exception, a subclass of either included, gets a fixed line. So no code from the
    caught instance runs (no __str__, __repr__, __format__, __class__ or args property of a subclass or of an
    argument), and an exception whose __str__ raises SystemExit 0 is exit 2, never 0."""
    kind = type(exc)
    args = exc.args if kind is AssertionError or kind is RuntimeError else ()
    text = args[0] if len(args) == 1 and type(args[0]) is str else None
    if kind is AssertionError:
        print("SELF-TEST FAIL " + (text if text is not None else "(an assertion without a harness identity)"),
              file=sys.stderr)
        return 1
    print("CANNOT-EVALUATE P0 harness: " + (text if text is not None else
                                           "an exception other than this harness's own; fail-closed"),
          file=sys.stderr)
    return 2


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--red-on-revert", action="store_true")
    parser.add_argument("--vectors-only", action="store_true")
    args = parser.parse_args(argv)
    try:
        f = fixtures()
        ids = run_vectors(p0, f)
        if not args.vectors_only:
            if args.red_on_revert:
                red_on_revert_loaded_exit(Path(p0.__file__).read_text(encoding="utf-8"), f)
                red_on_revert_hostile_str(Path(p0.__file__).read_text(encoding="utf-8"), f)
                red_on_revert(Path(p0.__file__).read_text(encoding="utf-8"), f)
            runner_check(ids)
            print("PASS runner/declared-test-executes")
            for route, text in runner_routes("runner/declared-test-executes"):
                runner_check(ids, text)
                print("PASS runner/declared-test-executes/route-" + route)
            if args.red_on_revert:
                runner = Path(__file__).resolve().parent / "run_all_checks.sh"
                text = runner.read_text(encoding="utf-8")
                lines = [line for line in text.splitlines(keepends=True)
                         if line.startswith('run_gate "opf-init-p0-selftest"')]
                check(len(lines) == 1, "runner/unique-registration")
                try:
                    runner_check(ids, text.replace(lines[0], ""))
                except AssertionError as exc:
                    check(str(exc) == "runner/declared-test-executes/pass-lines",
                          "runner/wrong-red")
                else:
                    raise AssertionError("runner/registration-not-red")
                print("RED own-dispatch -> runner/declared-test-executes/pass-lines")
                runner_red_checks(ids)
        if args.self_test and not args.vectors_only:
            fault_failures = _fault_channel_vectors()
            check(not fault_failures, "harness/fault-channels: " + "; ".join(fault_failures))
            print("PASS harness/fault-channels")
        return 0
    except Exception as exc:
        return _failure_status(exc)



# --- background fault channels of code loaded in process (atexit, worker thread, destructor) ----------
# Fail-closed settlement for the background fault channels of the code this gate loads and runs in
# process. _fail_closed_main installs the recorders BEFORE the gate's work (so before any in-process
# load) and settles them AFTER it: a fault the interpreter reports only to stderr (a destructor, weakref
# or similar callback through sys.unraisablehook; an unhandled exception ending a worker thread through
# threading.excepthook) is recorded and forces exit 2 over a passing verdict, never a silent pass; the
# verdict then ends the process with os._exit, so an atexit callback registered by loaded code can never
# run after it (that channel is unreachable, not merely disclosed). A SystemExit ending a worker thread
# is the interpreter's normal thread exit (default-hook parity) and is not recorded. Each recorder
# chains to the hook it wrapped, so the usual traceback still reaches stderr after the marker line.
# Paths that end outside _gate_exit (a propagating KeyboardInterrupt, an escaping exception) already end
# non-zero, and an ACCIDENTAL fault in an atexit callback cannot turn that non-zero end into exit 0, so
# no background fault reads as a pass there. Residual: loaded code replacing these hooks, this record or
# the exit itself is the reporting-machinery channel disclosed once in the _opf_views class disclosure
# (D-411-HOSTILE-DISCLOSED).
_LOADED_FAULTS = []
_FAULT_MARKER = "LOADED-CODE-FAULT"


def _install_fault_hooks():
    import threading

    previous_unraisable = sys.unraisablehook
    previous_thread = threading.excepthook

    def _record(channel, chained, args):
        _LOADED_FAULTS.append(channel)
        try:
            sys._loaded_code_fault = True
            sys.stderr.write("{}: a {} fault was recorded; a passing verdict will fail closed\n".format(
                _FAULT_MARKER, channel))
        finally:
            chained(args)

    def _record_unraisable(args):
        _record("destructor-or-callback", previous_unraisable, args)

    def _record_thread(args):
        if args.exc_type is not None and issubclass(args.exc_type, SystemExit):
            previous_thread(args)
        else:
            _record("worker-thread", previous_thread, args)

    sys.unraisablehook = _record_unraisable
    threading.excepthook = _record_thread


def _gate_exit(code):
    """Settle and END the process: flush both streams, force a recorded background fault to exit 2 over a
    passing verdict, then os._exit, so no atexit callback registered by loaded code runs after the verdict
    (the gate's own cleanup runs inside its work; this gate registers no atexit work of its own)."""
    import os
    if type(code) is bool:
        code = 1 if code else 0
    elif code is None:
        code = 0
    elif type(code) is not int:
        # The code object is never formatted: a loaded object's __str__ must not run here.
        sys.stderr.write("{}: a non-int SystemExit code at the gate entry; exit 1\n".format(_FAULT_MARKER))
        code = 1
    if code == 0 and (_LOADED_FAULTS or getattr(sys, "_loaded_code_fault", False)):
        sys.stderr.write("{}: {} background fault(s) from loaded code over a passing verdict; "
                         "fail-closed\n".format(_FAULT_MARKER, len(_LOADED_FAULTS) or 1))
        code = 2
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:
        if code == 0:
            code = 2
    os._exit(code)


def _fail_closed_main(run):
    """The canonical process entry: install the fault recorders, run the gate's own work, settle, end."""
    _install_fault_hooks()
    try:
        code = run()
    except SystemExit as exc:
        code = exc.code
    _gate_exit(code)


# The background fault channels, each driven through the gate's REAL _fail_closed_main in a child that
# loads one fixture the way this gate loads code in process: a clean load passes; an atexit callback
# registered by loaded code never runs (unreachable behind os._exit); a worker-thread fault and a
# destructor fault are recorded and force exit 2.
_FAULT_CHANNEL_PROBE = """import importlib.util, sys
tool, fixture = sys.argv[1:3]
sys.path.insert(0, tool.rsplit("/", 1)[0])
spec = importlib.util.spec_from_file_location("_fault_channel_probe_target", tool)
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)
def load():
    case = importlib.util.spec_from_file_location("_fault_channel_fixture", fixture)
    module = importlib.util.module_from_spec(case)
    case.loader.exec_module(module)
    return 0
gate._fail_closed_main(load)
"""

# (label, fixture source, expected exit, marker required, text that must NOT appear on stderr)
_FAULT_CHANNEL_CASES = (
    ("clean", "x = 1\n", 0, False, None),
    ("atexit", "import atexit, sys\n"
     "def callback():\n"
     "    sys.stderr.write('FAULT-FIXTURE-ATEXIT-RAN\\n')\n"
     "    raise RuntimeError('fault-fixture-atexit')\n"
     "atexit.register(callback)\n", 0, False, "FAULT-FIXTURE-ATEXIT-RAN"),
    ("joined-thread", "import threading\n"
     "def fault():\n"
     "    raise RuntimeError('fault-fixture-thread')\n"
     "worker = threading.Thread(target=fault)\n"
     "worker.start()\n"
     "worker.join()\n", 2, True, None),
    ("destructor", "import gc\n"
     "class Fault:\n"
     "    def __del__(self):\n"
     "        raise RuntimeError('fault-fixture-destructor')\n"
     "Fault()\n"
     "gc.collect()\n", 2, True, None),
)


def _fault_channel_vectors():
    """One failure string per fault-channel case whose child did not end as required: the expected exit
    code, the fault marker exactly when a fault must be recorded, and for the atexit case no trace of the
    callback (it must never run). Red without the entry wrapper (the probe then ends 1, AttributeError on
    _fail_closed_main, where clean and atexit expect 0) and red with the wrapper reverted to a plain exit
    (the thread and destructor children then end 0 with the default hooks' output alone, where exit 2 with
    the marker is required, and the atexit child then runs the callback)."""
    import subprocess
    import tempfile
    failures = []
    tool = str(Path(__file__).resolve())
    with tempfile.TemporaryDirectory(prefix="opf-init-p0-fault-channel-") as tmp:
        probe = Path(tmp) / "probe.py"
        probe.write_text(_FAULT_CHANNEL_PROBE, encoding="utf-8")
        for index, (label, body, expected, marked, absent) in enumerate(_FAULT_CHANNEL_CASES):
            case = Path(tmp) / "fault_channel_{}.py".format(index)
            case.write_text(body, encoding="utf-8")
            try:
                child = subprocess.run([sys.executable, "-I", "-B", str(probe), tool, str(case)],
                                       capture_output=True, text=True, timeout=120)
            except (OSError, subprocess.SubprocessError) as exc:
                failures.append("fault-channel/{}: probe did not run ({})".format(label, type(exc).__name__))
                continue
            if child.returncode != expected:
                failures.append("fault-channel/{}: expected exit {}, got {}".format(
                    label, expected, child.returncode))
            elif marked != ((_FAULT_MARKER + ":") in child.stderr):
                failures.append("fault-channel/{}: fault marker {}".format(
                    label, "absent" if marked else "present"))
            elif absent is not None and absent in child.stderr:
                failures.append("fault-channel/{}: the atexit callback ran".format(label))
    return failures


if __name__ == "__main__":
    _fail_closed_main(main)
