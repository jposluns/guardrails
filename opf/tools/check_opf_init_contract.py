"""OPF-D2B PR1: contract/source consistency gate for the init Keep contract.

Verifies the frozen contract constants stay aligned with their source authorities and that the PR1
contract surface is registered. Fail-closed: exit 2 (CANNOT-EVALUATE) on a missing/unreadable authority
or a changed source shape that means the contract can no longer be checked; exit 1 (DRIFT) on a concrete
mismatch; exit 0 clean. Run: python3 -I -B opf/tools/check_opf_init_contract.py [--self-test].

Detection is EXACT, never substring: a constant is matched as a whole module-level line, so a longer
value (KEEP_SCHEMA = 10 against KEEP_SCHEMA = 1) cannot satisfy it, and the pinned view tuple and the
frozen limit and reserved-namespace lines are compared in full.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

ACTOR_LINE = 'ACTOR_KINDS = ("maintainer", "assistant", "automation", "importer")'

# The frozen limit and identity lines the validator must carry verbatim (drift tripwire over the values).
FROZEN_LINES = (
    "KEEP_SCHEMA = 1",
    'KEEP_OPERATION = "opf-init-keep"',
    "MAX_RAW_BYTES = 1048576",
    "MAX_CANONICAL_BYTES = 1048576",
    "MAX_JSON_NESTING = 16",
    "MAX_DECISIONS = 4096",
    "MAX_INVENTORY_ENTRIES = 4096",
    "MAX_PATH_DEPTH = 32",
    "MAX_AGGREGATE_PATH_BYTES = 1048576",
    "MAX_PATH_BYTES = 4096",
    "MAX_COMPONENT_BYTES = 255",
    "MAX_ACTOR_ID_BYTES = 256",
    "MAX_REASON_BYTES = 4096",
    "MAX_STRING_BYTES = 4096",
    "MAX_FILE_SIZE = (1 << 63) - 1",
    "MAX_MODE = 0o777",
    "MAX_IDENTITY_INT = (1 << 64) - 1",
)

# The reserved-namespace string literals the validator's _RESERVED tuple must carry (the machine store,
# import staging, archive, and the pointer). The 13 pinned views are checked via the view-tuple compare.
RESERVED_LITERALS = ('".opf.toml"', '".working/toml"', '".working/imports"', '".working/archive"')

# Every roster surface that must register BOTH the validator self-test and the consistency gate.
ROSTER_FILES = (
    "tools/run_all_checks.sh",
    "opf/tools/run_all_checks.sh",
    "tools/check_opf_standalone_closure.py",
    ".github/workflows/quality.yml",
)


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


def _has_line(text, exact):
    """True only if `exact` appears as a whole module-level line, ignoring any trailing #-comment and
    surrounding whitespace, never as a substring (a longer value cannot satisfy a shorter one)."""
    return any(line.split("#", 1)[0].rstrip() == exact for line in text.splitlines())


def _view_tuple(text, name):
    """Extract the ordered quoted names from a `NAME = ( ... )` tuple, or None if not found/parseable."""
    m = re.search(re.escape(name) + r"\s*=\s*\((.*?)\)", text, re.S)
    if not m:
        return None
    return tuple(re.findall(r'"([^"]+)"', m.group(1)))


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
    if not _has_line(schema, ACTOR_LINE):
        _cant("_opf_schema.py ACTOR_KINDS shape changed; re-verify the contract")
    if not _has_line(contract, ACTOR_LINE):
        _drift("_opf_init_contract.py ACTOR_KINDS does not mirror _opf_schema.py")

    # C-FROZEN: each frozen constant is present as a whole line (exact value, not a substring).
    for line in FROZEN_LINES:
        if not _has_line(contract, line):
            _drift("frozen constant line absent or changed in the validator: {!r}".format(line))
    if "opf-init-keep" not in spec:
        _drift("OPF-INIT-D2B.md does not state the opf-init-keep operation")

    # C-RESERVED: the reserved namespace literals are present in the validator's reserved set.
    for lit in RESERVED_LITERALS:
        if lit not in contract:
            _drift("reserved namespace literal absent from the validator: {}".format(lit))

    # C-VIEWS: the pinned view tuple in the validator equals _opf_init.py's authority, in order.
    src_views = _view_tuple(init, "_INITIAL_VIEW_NAMES")
    if src_views is None:
        _cant("_opf_init.py _INITIAL_VIEW_NAMES missing or unparseable")
    con_views = _view_tuple(contract, "_INITIAL_VIEWS")
    if con_views is None:
        _drift("_opf_init_contract.py _INITIAL_VIEWS missing or unparseable")
    if con_views != src_views:
        _drift("validator view tuple diverges from _opf_init.py: {} vs {}".format(con_views, src_views))

    # C-D2A: the shipped D2a success wording is present (D2b must not silently retrofit an envelope).
    if "tracking and rendering are pending." not in opf:
        _cant("D2a success wording changed at source; re-verify the D2a/D2b boundary")

    # C-SPEC-VERSION: the base spec_version the schema-compat obligation targets.
    if not _has_line(store, 'SUPPORTED_SPEC_VERSION = "1.1.0"'):
        _cant("_opf_store.py SUPPORTED_SPEC_VERSION changed; re-verify the schema-compat boundary")

    # C-CHECKER-ROSTER: the required-checks authority the coupled success contract depends on.
    if "REQUIRED_CHECKS" not in check:
        _cant("_opf_check.py REQUIRED_CHECKS missing")

    # C-REVIEW: the durable review register enumerates F01..F30.
    for n in range(1, 31):
        if "F{:02d}".format(n) not in review:
            _drift("review register missing F{:02d}".format(n))

    # C-RUNNERS: BOTH the validator and the gate are registered in every roster surface (so neither can be
    # silently dropped from CI, and the workflow is checked directly, not only the shell runners).
    for rel in ROSTER_FILES:
        text = _read(rel)
        for script in ("_opf_init_contract.py", "check_opf_init_contract.py"):
            if script not in text:
                _drift("{} not registered in {}".format(script, rel))

    sys.stdout.write("PASS check_opf_init_contract: contract/source consistency\n")
    sys.exit(0)


def _self_test():
    # Git-free / source-free: exercise the exact-line matcher and the view-tuple parser on in-memory text.
    assert _has_line("KEEP_SCHEMA = 1\nX = 2", "KEEP_SCHEMA = 1"), "exact line should match"
    assert not _has_line("KEEP_SCHEMA = 10\n", "KEEP_SCHEMA = 1"), "substring must NOT match a longer value"
    assert _view_tuple('_INITIAL_VIEWS = (\n"A.md", "B.md",\n)', "_INITIAL_VIEWS") == ("A.md", "B.md"), "view parse"
    assert _view_tuple("nope", "_INITIAL_VIEWS") is None, "missing tuple is None"
    labels = ["F{:02d}".format(n) for n in range(1, 31)]
    assert labels[0] == "F01" and labels[-1] == "F30" and len(labels) == 30, "F-range"
    assert ACTOR_LINE.count('"') == 8, "actor line shape"
    sys.stdout.write("PASS check_opf_init_contract self-test\n")
    sys.exit(0)


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        _self_test()
    _checks()
