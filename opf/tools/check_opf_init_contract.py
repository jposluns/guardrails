"""OPF-D2B PR1: contract/source consistency gate for the init Keep contract.

Verifies the frozen contract constants stay aligned with their source authorities and that the PR1
contract surface is registered. Fail-closed: exit 2 (CANNOT-EVALUATE) on a missing/unreadable authority
or a changed source shape that means the contract can no longer be checked; exit 1 (DRIFT) on a concrete
mismatch; exit 0 clean. Run: python3 -I -B opf/tools/check_opf_init_contract.py [--self-test].
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ACTOR_LINE = 'ACTOR_KINDS = ("maintainer", "assistant", "automation", "importer")'


def _cant(msg):
    sys.stderr.write("CANNOT-EVALUATE: {}\n".format(msg))
    sys.exit(2)


def _drift(msg):
    sys.stderr.write("DRIFT: {}\n".format(msg))
    sys.exit(1)


def _read(rel):
    p = ROOT / rel
    if not p.is_file():
        _cant("missing input: {}".format(rel))
    try:
        return p.read_text(encoding="utf-8")
    except Exception as exc:
        _cant("unreadable {}: {}".format(rel, exc))


def _checks():
    contract = _read("opf/tools/_opf_init_contract.py")
    schema = _read("opf/tools/_opf_schema.py")
    store = _read("opf/tools/_opf_store.py")
    check = _read("opf/tools/_opf_check.py")
    init = _read("opf/tools/_opf_init.py")
    opf = _read("opf/tools/opf.py")
    spec = _read("opf/spec/OPF-INIT-D2B.md")
    review = _read("opf/spec/OPF-INIT-D2B-REVIEW.md")

    # C-ACTOR: the schema authority holds its shape, and the validator mirrors it exactly.
    if ACTOR_LINE not in schema:
        _cant("_opf_schema.py ACTOR_KINDS shape changed; re-verify the contract")
    if ACTOR_LINE not in contract:
        _drift("_opf_init_contract.py ACTOR_KINDS does not mirror _opf_schema.py")

    # C-KEEP-IDS: frozen operation id and schema in the validator, and named in the spec.
    if 'KEEP_OPERATION = "opf-init-keep"' not in contract or "KEEP_SCHEMA = 1" not in contract:
        _drift("_opf_init_contract.py frozen KEEP ids changed")
    if "opf-init-keep" not in spec:
        _drift("OPF-INIT-D2B.md does not state the opf-init-keep operation")

    # C-VIEWS: the pinned initial-views authority still exists.
    if "_INITIAL_VIEW_NAMES" not in init:
        _cant("_opf_init.py _INITIAL_VIEW_NAMES missing")

    # C-D2A: the shipped D2a success wording is present (D2b must not silently retrofit an envelope).
    if "tracking and rendering are pending." not in opf:
        _cant("D2a success wording changed at source; re-verify the D2a/D2b boundary")

    # C-SPEC-VERSION: the base spec_version the schema-compat obligation targets.
    if 'SUPPORTED_SPEC_VERSION = "1.1.0"' not in store:
        _cant("_opf_store.py SUPPORTED_SPEC_VERSION changed; re-verify the schema-compat boundary")

    # C-CHECKER-ROSTER: the required-checks authority the coupled success contract depends on.
    if "REQUIRED_CHECKS" not in check:
        _cant("_opf_check.py REQUIRED_CHECKS missing")

    # C-REVIEW: the durable review register enumerates F01..F30.
    for n in range(1, 31):
        if "F{:02d}".format(n) not in review:
            _drift("review register missing F{:02d}".format(n))

    # C-RUNNERS: the gate is registered in both runners and the standalone-closure subset.
    for rel in ("tools/run_all_checks.sh", "opf/tools/run_all_checks.sh",
                "tools/check_opf_standalone_closure.py"):
        if "check_opf_init_contract.py" not in _read(rel):
            _drift("check_opf_init_contract.py not registered in {}".format(rel))

    sys.stdout.write("PASS check_opf_init_contract: contract/source consistency\n")
    sys.exit(0)


def _self_test():
    # Git-free / source-free: confirm the frozen ACTOR line is the 4-kind tuple and the F-range is 1..30.
    assert ACTOR_LINE.count('"') == 8, "ACTOR_LINE shape"
    labels = ["F{:02d}".format(n) for n in range(1, 31)]
    assert labels[0] == "F01" and labels[-1] == "F30" and len(labels) == 30, "F-range"
    sys.stdout.write("PASS check_opf_init_contract self-test\n")
    sys.exit(0)


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        _self_test()
    _checks()
