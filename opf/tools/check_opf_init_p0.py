#!/usr/bin/env python3
"""P0 contract vectors and source reversions. No store, Git or scratch effects.

The D2a fixture retains the landed source-only builders' bytes in memory before
any P0 guard is exercised. Reversions receive those same intact fixtures.
Runner verification executes the real standalone shell dispatcher with other
gates intercepted, then requires this suite's actual assertion identities.
That check proves P0 dispatch, not that the other standalone gates passed.
"""
import argparse
import copy
import hashlib
import subprocess
import sys
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
        exec(compile(source.replace(old, new), module.__file__, "exec"), module.__dict__)
        before = fingerprint(f)
        try:
            run_case(module, cases[identity], f)
        except AssertionError as exc:
            check(str(exc) == identity, "revert/" + guard + "/wrong-assertion")
        else:
            raise AssertionError("revert/" + guard + "/not-red")
        check(fingerprint(f) == before, "revert/" + guard + "/fixture-changed")
        print("RED {} -> {}".format(guard, identity))


def runner_check(expected, text=None):
    here = Path(__file__).resolve().parent
    runner = here / "run_all_checks.sh"
    source = runner.read_text(encoding="utf-8") if text is None else text
    # Run the real dispatcher text, preserving its branches/exit behaviour. Intercept
    # other gate commands and reduce only this suite to its non-recursive vector leg.
    prefix = r'''
python3() {
  if [ "$#" -eq 5 ] && [ "$1" = "-I" ] && [ "$2" = "-B" ] \
      && [ "$3" = "$p0_test" ] && [ "$4" = "--self-test" ] \
      && [ "$5" = "--red-on-revert" ]; then
    "$p0_python" -I -B "$p0_test" --self-test --vectors-only
  else
    case " $* " in *check_opf_init_p0.py*) return 2;; esac
    return 0
  fi
}
p0_python="$1"
p0_test="$2"
'''
    proc = subprocess.run(
        ["bash", "-c", prefix + source, str(runner), sys.executable, str(here / Path(__file__).name)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)
    reached = tuple(line[5:] for line in proc.stdout.splitlines() if line.startswith("PASS "))
    check(proc.returncode == 0 and reached == expected, "runner/declared-test-executes")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--red-on-revert", action="store_true")
    parser.add_argument("--vectors-only", action="store_true")
    args = parser.parse_args()
    try:
        f = fixtures()
        ids = run_vectors(p0, f)
        if not args.vectors_only:
            if args.red_on_revert:
                red_on_revert(Path(p0.__file__).read_text(encoding="utf-8"), f)
            runner_check(ids)
            print("PASS runner/declared-test-executes")
            if args.red_on_revert:
                runner = Path(__file__).resolve().parent / "run_all_checks.sh"
                text = runner.read_text(encoding="utf-8")
                lines = [line for line in text.splitlines(keepends=True)
                         if line.startswith('run_gate "opf-init-p0-selftest"')]
                check(len(lines) == 1, "runner/unique-registration")
                try:
                    runner_check(ids, text.replace(lines[0], ""))
                except AssertionError as exc:
                    check(str(exc) == "runner/declared-test-executes", "runner/wrong-red")
                else:
                    raise AssertionError("runner/registration-not-red")
                print("RED runner-registration -> runner/declared-test-executes")
        return 0
    except AssertionError as exc:
        print("SELF-TEST FAIL " + str(exc), file=sys.stderr)
        return 1
    except Exception as exc:
        print("CANNOT-EVALUATE P0 harness: {}".format(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
