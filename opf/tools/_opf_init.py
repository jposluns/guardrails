#!/usr/bin/env python3
"""OPF init D1: pure, canonical clean-baseline manifest and ledger builders.

Each builder returns new-document TOML through _opf_emit.emit_checked, reparses it, and refuses
invalid output. No store discovery, publication, Git operation, or render call belongs here.
The manifest fixture in _opf_store remains independent of these production builders.

Defaults follow OPF-SPEC section 9, with optional modules disabled and no profile or vendor.
The initial Markdown view set is pinned; future registry additions do not expand it.
Validation here covers individual bootstrap documents, not whole-store or publication integrity.

The self-test loads the _byte_canon authority in-process, behind _opf_emit's backstop: a load or later call
that ends the process is exit 2 (cannot evaluate), and a KeyboardInterrupt is re-raised to stop the run.
That backstop guards only this self-test. A process ending in another self-test the opf.py aggregate runs
is caught by the aggregator's subprocess-per-unit runner only once the unit runner of #385 is on main; until
then the aggregate runs each unit in-process and nothing here catches it.

Run: python3 opf/tools/_opf_init.py --self-test
"""

import sys

if tuple(sys.version_info[:2]) < (3, 14):
    sys.stderr.write(
        "error: _opf_init.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
        "Nothing was run (cannot evaluate).\n"
        % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
    raise SystemExit(2)

from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _opf_emit       # noqa: E402
import _opf_store      # noqa: E402
from _opf_schema import SUPPORTED_SCHEMA, COUNTERS_TOP_KEYS, validate_counters  # noqa: E402
from _opf_release import (  # noqa: E402
    VERSION_TOP_KEYS, WORKLOG_TOP_KEYS, validate_version, validate_worklog,
)
from _opf_check import (  # noqa: E402
    COUNTERS_NAME, VERSION_NAME, WORKLOG_NAME, INDEX_SUFFIX, INDEX_TOP_KEYS,
)
# Metadata only: the registry also contains renderer callables, which are never invoked here.
from _opf_views import NAMED_VIEWS  # noqa: E402

import tomllib  # noqa: E402

_INITIAL_VIEW_NAMES = (
    "TODO.md", "BACKLOG.md", "PIPELINE.md", "DONE.md", "FINDINGS.md", "DECISIONS.md",
    "BLOCKS.md", "HANDOFF.md", "REFERENCES.md", "CONTRIBUTIONS.md", "WORKLOG.md",
    "VERSION.md", "DECISIONS.toml",
)
INDEX_TYPES = tuple(sorted(set(_opf_store.BASELINE_TYPES) - {"worklog"}))


class InitError(Exception):
    """A bootstrap document or requested index is invalid; nothing is returned."""


def _choice(value, vocabulary):
    """Pin the selected token, refusing schema drift rather than selecting by ordinal."""
    if value not in vocabulary:
        raise InitError("unsupported bootstrap token {!r}".format(value))
    return value


def _manifest_model():
    """Construct a fresh clean-adopter model; never reuse the validator's test fixture."""
    views = {}
    for name in _INITIAL_VIEW_NAMES:
        if name not in NAMED_VIEWS:
            raise InitError("missing bootstrap view metadata: {}".format(name))
        kind, sources, _renderer = NAMED_VIEWS[name]
        views[name] = {
            "kind": _choice(kind, _opf_store.VIEW_KINDS),
            "sources": list(sources),
            # OPF-SPEC 5.8; _opf_views._spec_destination assigns these names to store scope.
            "target": "{}/{}".format(_opf_store.WORKING_DIRNAME, name),
        }
    return {
        "opf": {
            "standard": _opf_store.STANDARD_TOKEN,
            "spec_version": _opf_store.SUPPORTED_SPEC_VERSION,
            "layout": _choice("inline", _opf_store.LAYOUTS),
            "posture": _choice("required", _opf_store.POSTURES),
            "import_status": _choice("none", _opf_store.IMPORT_STATES),
        },
        "store": {"sync_target": ""},
        "modules": {name: False for name in _opf_store.KNOWN_MODULES},
        "types": {name: {"namespace": ns} for name, ns in _opf_store.BASELINE_TYPES.items()},
        "providers": {
            "local-directory": {
                "handler": "builtin",
                "roles": [_choice(role, _opf_store.PROVIDER_ROLES) for role in ("create", "sync")],
            },
            "generic-git-remote": {
                "handler": "builtin",
                "roles": [_choice("sync", _opf_store.PROVIDER_ROLES)],
            },
        },
        "unmanaged": {"paths": []},
        "views": views,
        "deliverables": {
            "CHANGELOG.md": {
                "kind": _choice("curated", _opf_store.DELIVERABLE_KINDS),
                "target": "CHANGELOG.md",
            },
        },
        "archive": {"period": _choice("year", _opf_store.ARCHIVE_PERIODS)},
        "vendors": {"registered": []},
    }


def _emit_validated(document, name, validator):
    text = _opf_emit.emit_checked(document)
    result = validator(tomllib.loads(text))
    if result.status != _opf_store.VALID or result.findings:
        raise InitError("{}: bootstrap validation failed: {}".format(name, result.findings))
    return text


def build_manifest():
    """Return the generic adopter's manifest; no profile, foreign paths, or root VERSION."""
    return _emit_validated(_manifest_model(), _opf_store.MANIFEST_NAME,
                           _opf_store.validate_manifest)


def build_counters(*, seed=None):
    """Return a counters.toml high-water ledger for a fresh store.

    With `seed` omitted (the clean first-adoption bootstrap), returns a ZERO high-water for every
    baseline namespace, including worklog (WL), and for no other namespace: the legacy_fragment
    importer namespace (LF) is deprecated for new stores and MUST NOT be scaffolded (spec 8.1), so a
    fresh store carries no LF counter.

    With `seed` supplied (B6 re-adoption: a validated ancestral high-water, e.g. from
    _opf_init_operation.read_ancestral_counter_seed), the ancestral values are COPIED WITHOUT MUTATION,
    so the next allocation follows the ancestral high-water (including the WL prefix): EVERY baseline
    namespace MUST be present in the seed (PD-D2B-PR3-SCHEMA decision 6: a missing historical value is
    UNKNOWN, never a zero, so a missing one is a refusal); an importer namespace (LF) is accepted and
    copied when the ancestral store carried one, and is never required or invented when it did not;
    each value is a genuine non-negative 64-bit integer copied verbatim, and a seed namespace outside
    the baseline and importer roster refuses. The result is re-validated through
    validate_counters and refused on any finding; zero is NEVER substituted for a missing, unreadable, or
    malformed input (guard-input-soundness). SELECTION of the ancestral snapshot is the reader's / PR4's
    authority, not this builder's."""
    baseline = frozenset(_opf_store.BASELINE_TYPES.values())
    importer = frozenset(_opf_store.IMPORTER_TYPES.values())
    if seed is None:
        document = {"schema": SUPPORTED_SCHEMA, "counters": {ns: 0 for ns in baseline}}
        text = _opf_emit.emit_checked(document)
        _high, findings = validate_counters(tomllib.loads(text), known_namespaces=baseline)
        if findings:
            raise InitError("{}: bootstrap validation failed: {}".format(COUNTERS_NAME, findings))
        return text
    if type(seed) is not dict:
        raise InitError("counters seed must be a validated high-water mapping")
    for ns in sorted(baseline):
        if ns not in seed:
            raise InitError("counters seed is missing a high-water for namespace {!r}; a missing "
                            "ancestral value is unknown, and zero is never substituted".format(ns))
    counters = {}
    for ns, val in seed.items():
        if type(ns) is not str or ns not in baseline | importer:
            raise InitError("counters seed carries namespace {!r} outside the store roster".format(ns))
        # bool is an int subclass; a genuine high-water is never True/False. A value past the TOML
        # signed 64-bit range cannot be carried by a counters ledger, so it refuses too.
        if type(val) is not int or val < 0 or val > (1 << 63) - 1:
            raise InitError("counters seed value for {!r} is not a non-negative 64-bit "
                            "integer".format(ns))
        counters[ns] = val
    document = {"schema": SUPPORTED_SCHEMA, "counters": counters}
    text = _opf_emit.emit_checked(document)
    _high, findings = validate_counters(tomllib.loads(text), known_namespaces=baseline,
                                        optional_namespaces=importer)
    if findings:
        raise InitError("{}: bootstrap validation failed: {}".format(COUNTERS_NAME, findings))
    return text


def build_version():
    """Return an empty release and summary ledger."""
    return _emit_validated({"schema": SUPPORTED_SCHEMA, "release": [], "summary": []},
                           VERSION_NAME, validate_version)


def build_worklog():
    """Return an empty worklog, with no timestamp or synthetic attribution."""
    return _emit_validated({"schema": SUPPORTED_SCHEMA, "entry": []},
                           WORKLOG_NAME, validate_worklog)


def build_index(type_name):
    """Return an empty baseline non-ledger index; reject worklog and unknown names."""
    if type(type_name) is not str or type_name not in INDEX_TYPES:
        raise InitError("index requires a baseline non-ledger type name")
    text = _opf_emit.emit_checked({"schema": SUPPORTED_SCHEMA, "record": []})
    data = tomllib.loads(text)
    # The empty-index contract is stricter than the general index reader's optional record array.
    if (set(data) != INDEX_TOP_KEYS or type(data.get("schema")) is not int
            or data["schema"] != SUPPORTED_SCHEMA or data.get("record") != []):
        raise InitError("{}{}: invalid empty index".format(type_name, INDEX_SUFFIX))
    return text


# --- self-test --------------------------------------------------------------------------------------

def self_test():
    """The canonical --self-test entry's suite hook (F-SELFTEST-NO-MAIN). Run as this file's process
    entry (the module __name__ is "__main__"), it routes the suite through _fail_closed_main, which
    installs the fault recorders before any in-process load, settles them after the suite, and ends the
    process with os._exit, so sys.exit(self_test()) never returns and no atexit callback registered by
    loaded code runs after the verdict. Called in-process (the opf.py aggregate), it runs the suite
    plainly and returns its result; that aggregate entry is the disclosed HARDEN-GATE-ENTRY-WRAP residual
    until the unit runner of #385 lands."""
    if __name__ == "__main__":
        _fail_closed_main(_self_test)
    return _self_test()


def _self_test():
    """The self-test behind _opf_emit's backstop, so the in-process _byte_canon authority (loaded, then
    called) can never end it with its own status: any escaping exception is exit 2 with a fixed message,
    and a KeyboardInterrupt is re-raised fresh. This is the callable the canonical entry invokes."""
    return _opf_emit._backstop(_self_test_body, "_opf_init")


def _self_test_body():
    """Exercise canonical bytes, independent defaults, validators, and negative discrimination."""
    from copy import deepcopy

    failures = []
    checked = 0

    def check(name, condition):
        nonlocal checked
        checked += 1
        if not condition:
            failures.append(name)

    def valid(result):
        return result.status == _opf_store.VALID and not result.findings

    def rejects(thunk):
        try:
            thunk()
        except InitError:
            return True
        return False

    def seeded_or_none(seed):
        # A refused seed is a named check failure here, never a fail-closed harness error.
        try:
            return build_counters(seed=seed)
        except InitError:
            return None

    try:
        authority = _opf_emit._load_byte_canon_authority()
        manifest_text = build_manifest()
        manifest = tomllib.loads(manifest_text)
        check("manifest validates", valid(_opf_store.validate_manifest(manifest)))
        check("manifest repeat bytes", manifest_text == build_manifest())
        model = _manifest_model()
        check("manifest reversed insertion order",
              manifest_text == _opf_emit.emit_checked(dict(reversed(list(model.items())))))
        check("manifest top tables", set(manifest) == _opf_store.TOP_LEVEL_TABLES - {"profiles"})
        check("opf defaults", manifest["opf"] == {
            "standard": _opf_store.STANDARD_TOKEN, "spec_version": _opf_store.SUPPORTED_SPEC_VERSION,
            "layout": "inline", "posture": "required", "import_status": "none",
        })
        check("store default", manifest["store"] == {"sync_target": ""})
        check("modules disabled", set(manifest["modules"]) == set(_opf_store.KNOWN_MODULES)
              and all(value is False for value in manifest["modules"].values()))
        check("baseline type bindings", manifest["types"] == {
            name: {"namespace": ns} for name, ns in _opf_store.BASELINE_TYPES.items()
        })
        check("providers", manifest["providers"] == {
            "local-directory": {"handler": "builtin", "roles": ["create", "sync"]},
            "generic-git-remote": {"handler": "builtin", "roles": ["sync"]},
        })
        check("unmanaged empty", manifest["unmanaged"] == {"paths": []})
        check("vendors empty", manifest["vendors"] == {"registered": []})
        check("archive default", manifest["archive"] == {"period": "year"})
        check("changelog declaration", manifest["deliverables"] == {
            "CHANGELOG.md": {"kind": "curated", "target": "CHANGELOG.md"},
        })
        # Independent expected metadata, pinned to the D1 contract rather than NAMED_VIEWS.
        expected_views = {
            "TODO.md": ("composed", ["backlog_item", "block"]),
            "BACKLOG.md": ("composed", ["backlog_item", "block"]),
            "PIPELINE.md": ("composed", ["backlog_item", "block"]),
            "DONE.md": ("composed", ["done"]),
            "FINDINGS.md": ("composed", ["finding"]),
            "DECISIONS.md": ("composed", ["pending_decision", "autonomous_decision",
                                          "maintainer_decision", "preference_pattern"]),
            "BLOCKS.md": ("composed", ["block"]),
            "HANDOFF.md": ("composed", ["handoff"]),
            "REFERENCES.md": ("composed", ["reference"]),
            "CONTRIBUTIONS.md": ("composed", ["contribution"]),
            "WORKLOG.md": ("deterministic", ["worklog"]),
            "VERSION.md": ("deterministic", ["version"]),
            "DECISIONS.toml": ("projection", ["pending_decision", "autonomous_decision",
                                              "maintainer_decision", "preference_pattern"]),
        }
        check("pinned view inventory and destinations", manifest["views"] == {
            name: {"kind": kind, "sources": sources, "target": ".working/" + name}
            for name, (kind, sources) in expected_views.items()
        })
        for table, keys in (
                ("opf", _opf_store.OPF_KEYS), ("store", _opf_store.STORE_KEYS),
                ("unmanaged", _opf_store.UNMANAGED_KEYS), ("vendors", _opf_store.VENDORS_KEYS),
                ("archive", _opf_store.ARCHIVE_KEYS)):
            check(table + " keys", set(manifest[table]) == keys)
        for table, keys in (
                ("types", _opf_store.TYPE_KEYS), ("providers", _opf_store.PROVIDER_KEYS),
                ("views", _opf_store.VIEW_KEYS), ("deliverables", _opf_store.DELIVERABLE_KEYS)):
            check(table + " member keys", all(set(row) == keys for row in manifest[table].values()))

        # Pinned byte fixtures: only the shared schema marker is substituted. Neither the emitter nor
        # the production model derives the expected ordering, whitespace, or namespace inventory.
        schema_line = "schema = {}\n".format(SUPPORTED_SCHEMA)
        expected_counters = (
            schema_line + "\n[counters]\n"
            "AD = 0\nBI = 0\nBL = 0\nCN = 0\nDN = 0\nFN = 0\nHO = 0\nMD = 0\nPD = 0\n"
            "PP = 0\nRF = 0\nWL = 0\n"
        )
        expected_version = "release = []\n" + schema_line + "summary = []\n"
        expected_worklog = "entry = []\n" + schema_line
        expected_index = "record = []\n" + schema_line
        documents = {_opf_store.MANIFEST_NAME: manifest_text}
        for name, builder, expected, keys in (
                (COUNTERS_NAME, build_counters, expected_counters, COUNTERS_TOP_KEYS),
                (VERSION_NAME, build_version, expected_version, VERSION_TOP_KEYS),
                (WORKLOG_NAME, build_worklog, expected_worklog, WORKLOG_TOP_KEYS)):
            text = builder()
            documents[name] = text
            check(name + " canonical bytes", text.encode("utf-8") == expected.encode("ascii"))
            check(name + " repeat bytes", text == builder())
            check(name + " keys", set(tomllib.loads(text)) == keys)
        namespaces = frozenset(_opf_store.BASELINE_TYPES.values())
        counters = tomllib.loads(documents[COUNTERS_NAME])
        high, findings = validate_counters(counters, known_namespaces=namespaces)
        check("counters validate and are complete", not findings and high == {
            ns: 0 for ns in namespaces
        })
        # Spec 8.1: legacy_fragment is deprecated for new stores and MUST NOT be scaffolded, so a fresh
        # ledger carries no importer namespace (the revert-flip for a re-seeded LF, beside the pinned
        # "counters canonical bytes" check above).
        check("fresh counters contain no LF",
              "LF" not in counters["counters"]
              and not set(counters["counters"]) & frozenset(_opf_store.IMPORTER_TYPES.values()))
        # B6 re-adoption seed path: an ancestral high-water is copied without mutation, so the next
        # allocation follows it (including WL). Every baseline namespace is required; an importer
        # namespace (LF) is accepted and copied if present and never required. Each negative below
        # refuses rather than silently zeroing.
        baseline_ns = frozenset(_opf_store.BASELINE_TYPES.values())
        importer_ns = frozenset(_opf_store.IMPORTER_TYPES.values())
        good_seed = {ns: 0 for ns in baseline_ns | importer_ns}
        good_seed.update({"WL": 7, "BI": 3, "LF": 5})
        seeded = seeded_or_none(good_seed)
        seeded = tomllib.loads(seeded) if seeded is not None else {"counters": {}}
        check("seeded counters follow ancestral high-water",
              seeded["counters"].get("WL") == 7 and seeded["counters"].get("BI") == 3)
        check("seed with LF is accepted and copied",
              set(seeded["counters"]) == (baseline_ns | importer_ns)
              and seeded["counters"]["LF"] == 5)
        lf_free_seed = dict({ns: 0 for ns in baseline_ns}, WL=7)
        lf_free = seeded_or_none(lf_free_seed)
        lf_free = tomllib.loads(lf_free)["counters"] if lf_free is not None else {}
        check("seed without LF is accepted (LF is never required or invented)",
              set(lf_free) == baseline_ns and lf_free["WL"] == 7)
        check("seed with a value past the 64-bit range refuses",
              rejects(lambda: build_counters(seed=dict(good_seed, WL=1 << 63))))
        ceiling = seeded_or_none(dict(good_seed, WL=(1 << 63) - 1))
        check("seed at the 64-bit ceiling is accepted",
              ceiling is not None and tomllib.loads(ceiling)["counters"]["WL"] == (1 << 63) - 1)
        check("seeded counters revalidate", not validate_counters(
            seeded, known_namespaces=baseline_ns, optional_namespaces=importer_ns)[1])
        missing = {ns: 0 for ns in (baseline_ns | importer_ns) if ns != "WL"}
        check("seed missing a baseline namespace refuses",
              rejects(lambda: build_counters(seed=missing)))
        check("LF-free seed missing a baseline namespace refuses",
              rejects(lambda: build_counters(
                  seed={ns: 0 for ns in baseline_ns if ns != "HO"})))
        check("seed with a bool value refuses",
              rejects(lambda: build_counters(seed=dict(good_seed, WL=True))))
        check("seed with a negative value refuses",
              rejects(lambda: build_counters(seed=dict(good_seed, WL=-1))))
        check("seed with a namespace outside the roster refuses",
              rejects(lambda: build_counters(seed=dict(good_seed, ZZ=1))))
        check("non-mapping seed refuses",
              rejects(lambda: build_counters(seed=[1, 2, 3])))
        check("no-seed and empty-adoption bytes are the zero baseline",
              build_counters() == seeded_or_none({ns: 0 for ns in baseline_ns}))
        check("version validates", valid(validate_version(tomllib.loads(documents[VERSION_NAME]))))
        check("worklog validates", valid(validate_worklog(tomllib.loads(documents[WORKLOG_NAME]))))
        check("index roster", INDEX_TYPES == tuple(sorted(set(_opf_store.BASELINE_TYPES) - {"worklog"})))
        for type_name in sorted(set(_opf_store.BASELINE_TYPES) - {"worklog"}):
            name = type_name + INDEX_SUFFIX
            text = build_index(type_name)
            documents[name] = text
            check(name + " canonical bytes", text.encode("utf-8") == expected_index.encode("ascii"))
            check(name + " repeat bytes", text == build_index(type_name))
            data = tomllib.loads(text)
            check(name + " shape", set(data) == INDEX_TOP_KEYS
                  and type(data["schema"]) is int
                  and data == {"schema": SUPPORTED_SCHEMA, "record": []})
        for bad_name in ("worklog", "version", "unknown", "../done", "", None, [], True):
            check("index refuses {!r}".format(bad_name),
                  rejects(lambda: build_index(bad_name)))

        for name, text in documents.items():
            check(name + " byte-canon scan", not authority.scan_bytes(text.encode("utf-8")))
            check(name + " ASCII", text.isascii())
        for namespace in sorted(namespaces):
            bad = deepcopy(counters)
            del bad["counters"][namespace]
            _high, findings = validate_counters(bad, known_namespaces=namespaces)
            check("missing counter " + namespace, bool(findings))
        for label, table, key, value in (
                ("non-bool module", "modules", "governance", "false"),
                ("invalid posture", "opf", "posture", "unknown")):
            bad = deepcopy(manifest)
            bad[table][key] = value
            result = _opf_store.validate_manifest(bad)
            check(label, result.status == _opf_store.INVALID and bool(result.findings))
        bad = deepcopy(manifest)
        del bad["opf"]["layout"]
        result = _opf_store.validate_manifest(bad)
        check("missing layout", result.status == _opf_store.INVALID and bool(result.findings))
        for name, validator in ((VERSION_NAME, validate_version), (WORKLOG_NAME, validate_worklog)):
            bad = tomllib.loads(documents[name])
            bad["schema"] = 999
            result = validator(bad)
            check(name + " wrong schema", result.status == _opf_store.INVALID and bool(result.findings))
        bad = deepcopy(counters)
        bad["schema"] = 999
        _high, findings = validate_counters(bad, known_namespaces=namespaces)
        check("counters wrong schema", bool(findings))
        check("validated emission refuses corruption", rejects(lambda: _emit_validated(
            {"opf": {"standard": _opf_store.STANDARD_TOKEN}},
            _opf_store.MANIFEST_NAME, _opf_store.validate_manifest)))
    except Exception as exc:
        print("OPF-INIT SELF-TEST ERROR: {}; fail-closed".format(exc), file=sys.stderr)
        return 2

    for label, failure in _st_loaded_exit():
        check("loaded-exit/{}: {}".format(label, failure), failure is None)

    # The background fault channels of code loaded in process (atexit, worker thread, destructor), each
    # driven through the real _fail_closed_main entry in a child.
    fault_failures = _fault_channel_vectors()
    check("fault-channels ({})".format("; ".join(fault_failures) or "settled"), not fault_failures)

    if failures:
        for name in failures:
            print("FAIL: " + name, file=sys.stderr)
        print("OPF-INIT SELF-TEST: FAIL ({} of {} checks)".format(len(failures), checked),
              file=sys.stderr)
        return 1
    print("OPF-INIT SELF-TEST: PASS ({} checks)".format(checked))
    return 0


# Each case is the module the self-test loads as its _byte_canon authority: (label, source, interrupt). A
# load case ends the process while loading; the scan case ends it in the later authority.scan_bytes call. An
# interrupt case must re-raise a fresh KeyboardInterrupt (the probe exits 130); every other case must give 2.
_LOADED_EXIT_CASES = (
    ("load SystemExit(0)", "raise SystemExit(0)\n", False),
    ("load SystemExit(None)", "raise SystemExit\n", False),
    ("load KeyboardInterrupt", "raise KeyboardInterrupt\n", True),
    ("load GeneratorExit", "raise GeneratorExit\n", False),
    ("load BaseException subclass", "class B(BaseException):\n    pass\nraise B()\n", False),
    ("load SystemExit(code whose repr exits 0)",
     "class R:\n    def __repr__(self):\n        raise SystemExit(0)\n    __str__ = __repr__\n"
     "raise SystemExit(R())\n", False),
    ("load Exception whose str exits 0",
     "class E(Exception):\n    def __str__(self):\n        raise SystemExit(0)\n    __repr__ = __str__\n"
     "raise E()\n", False),
    ("scan_bytes SystemExit(0)", "def scan_bytes(data):\n    raise SystemExit(0)\n", False),
)

# Run in a child through the real entry: load this file by path, point only the authority loader at one case
# module (the vectors themselves are stubbed so the child never recurses), then exit with self_test()'s
# status, or 130 when self_test re-raises a fresh KeyboardInterrupt (3 when the loaded object escapes).
_LOADED_EXIT_PROBE = """import importlib.util, sys
tool, case = sys.argv[1:3]
sys.path.insert(0, tool.rsplit("/", 1)[0])
spec = importlib.util.spec_from_file_location("_opf_init_entry_probe", tool)
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)
def authority():
    spec = importlib.util.spec_from_file_location("_opf_init_loaded_exit", case)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
gate._opf_emit._load_byte_canon_authority = authority
gate._st_loaded_exit = lambda: ()
try:
    code = gate.self_test()
except KeyboardInterrupt as exc:
    sys.exit(130 if type(exc) is KeyboardInterrupt and exc.__suppress_context__ else 3)
sys.exit(code)
"""


def _st_loaded_exit():
    """Each case must give exit 2 with the fixed backstop message (an interrupt case: a fresh
    KeyboardInterrupt with the fixed interrupt message) through the real self_test in a child process, so
    the vector is red with the backstop removed from self_test. Yields (label, None or the failure)."""
    import subprocess
    import tempfile
    tool = str(Path(__file__).resolve())
    with tempfile.TemporaryDirectory(prefix="opf-init-loaded-exit-") as tmp:
        probe = Path(tmp) / "probe.py"
        probe.write_text(_LOADED_EXIT_PROBE, encoding="utf-8")
        for index, (label, body, interrupt) in enumerate(_LOADED_EXIT_CASES):
            case = Path(tmp) / "loaded_exit_{}.py".format(index)
            case.write_text(body, encoding="utf-8")
            try:
                child = subprocess.run([sys.executable, "-I", "-B", str(probe), tool, str(case)],
                                       capture_output=True, text=True, timeout=300)
            except (OSError, subprocess.SubprocessError) as exc:
                yield label, "probe did not run ({})".format(type(exc).__name__)
                continue
            want, message = (130, "_opf_init self-test interrupted: KeyboardInterrupt re-raised") if interrupt \
                else (2, "_opf_init self-test cannot evaluate: in-process code raised")
            if child.returncode != want or message not in child.stderr:
                yield label, "expected exit {} with the fixed message, got {}".format(want, child.returncode)
            else:
                yield label, None


def main():
    if sys.argv[1:] in (["--self-test"], ["--selftest"]):
        return self_test()
    print("usage: _opf_init.py --self-test (a library module; no live mode)", file=sys.stderr)
    return 2



# --- background fault channels of code loaded in process (atexit, worker thread, destructor) ----------
# Fail-closed settlement for the background fault channels of the code this gate loads and runs in
# process. _fail_closed_main installs the recorders BEFORE the gate's work (so before any in-process
# load) and settles them AFTER it: a fault the interpreter reports only to stderr (a destructor, weakref
# or similar callback through sys.unraisablehook; an unhandled exception ending a worker thread through
# threading.excepthook) is recorded and forces exit 2 over a passing verdict, never a silent pass. Before
# a passing verdict the settle waits (_SETTLE_SECONDS in all) for every other thread still running, so a
# worker's later fault is recorded instead of killed silently by the exit, and a thread still running at
# the bound fails the verdict closed (exit 2). The verdict then ends the process with os._exit, so an
# atexit callback registered by loaded code can never run after it (that channel is unreachable, not
# merely disclosed). Only a SystemExit with code None or 0 ending a worker thread is the interpreter's
# normal SUCCESSFUL thread exit (default-hook parity) and is not recorded; any other code is an
# unsuccessful exit a worker reported, so it is recorded.
# Each recorder chains to the hook it wrapped, so the usual traceback still reaches stderr after the
# marker line. The settle re-checks the record after the exit flushes, so a fault recorded while a
# flush was blocked on a full pipe still fails the verdict.
# Paths that end outside _gate_exit (a propagating KeyboardInterrupt, an escaping exception) already end
# non-zero, and an ACCIDENTAL fault in an atexit callback cannot turn that non-zero end into exit 0, so
# no background fault reads as a pass there. Residual: loaded code replacing these hooks, this record or
# the exit itself is the reporting-machinery channel disclosed once in the _opf_views class disclosure
# (D-411-HOSTILE-DISCLOSED).
_LOADED_FAULTS = []
_FAULT_MARKER = "LOADED-CODE-FAULT"


def _install_fault_hooks():
    # A local alias: opf.py's close-lifecycle census reads an attribute store through the bare module name
    # as shadowing that module.
    import threading as _threads

    previous_unraisable = sys.unraisablehook
    previous_thread = _threads.excepthook

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

    def _benign_thread_exit(args):
        # Only a worker thread ending by SystemExit(None) or SystemExit(0) is the interpreter's normal
        # SUCCESSFUL thread exit (default-hook parity). Any other SystemExit code is an unsuccessful exit
        # a worker reported, recorded like any other fault (never a silent pass); the code is read
        # through exact built-in types alone and never formatted.
        if args.exc_type is None or not issubclass(args.exc_type, SystemExit):
            return False
        code = getattr(args.exc_value, "code", None) if args.exc_value is not None else None
        return code is None or (type(code) is int and code == 0)

    def _record_thread(args):
        if _benign_thread_exit(args):
            previous_thread(args)
        else:
            _record("worker-thread", previous_thread, args)

    sys.unraisablehook = _record_unraisable
    _threads.excepthook = _record_thread


_SETTLE_SECONDS = 60.0


def _gate_exit(code):
    """Settle and END the process: before a passing verdict, wait (up to _SETTLE_SECONDS in all) for every
    other thread still running, so a fault it raises later is recorded, never killed silently by the exit,
    and fail closed (exit 2) if one still runs at the bound; force a recorded background fault to exit 2
    over a passing verdict, flush both streams, re-check the record AFTER the flushes (a fault recorded
    while a flush was blocked, on a full pipe say, must still fail the verdict; its late marker is written
    straight to fd 2, past the buffers), then os._exit, so no atexit callback registered by loaded code
    runs after the verdict (the gate's own cleanup runs inside its work; this gate registers no atexit work
    of its own)."""
    import os
    import threading as _threads
    import time
    if type(code) is bool:
        code = 1 if code else 0
    elif code is None:
        code = 0
    elif type(code) is not int:
        # The code object is never formatted: a loaded object's __str__ must not run here.
        sys.stderr.write("{}: a non-int SystemExit code at the gate entry; exit 1\n".format(_FAULT_MARKER))
        code = 1
    if code == 0:
        current = _threads.current_thread()
        deadline = time.monotonic() + _SETTLE_SECONDS
        while True:
            running = [worker for worker in _threads.enumerate()
                       if worker is not current and worker.is_alive()]
            remaining = deadline - time.monotonic()
            if not running or remaining <= 0:
                break
            running[0].join(min(remaining, 0.5))
        if running:
            sys.stderr.write("{}: a worker thread was still running at the verdict; fail-closed\n".format(
                _FAULT_MARKER))
            code = 2
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
    if code == 0 and (_LOADED_FAULTS or getattr(sys, "_loaded_code_fault", False)):
        try:
            os.write(2, ("{}: a background fault was recorded during the exit flush; "
                         "fail-closed\n".format(_FAULT_MARKER)).encode("utf-8"))
        except OSError:
            pass
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
gate._SETTLE_SECONDS = 2.0
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
    ("thread-systemexit", "import threading\n"
     "def fault():\n"
     "    raise SystemExit(7)\n"
     "worker = threading.Thread(target=fault)\n"
     "worker.start()\n"
     "worker.join()\n", 2, True, None),
    ("thread-systemexit-zero", "import sys\nimport threading\n"
     "def done():\n"
     "    sys.exit(0)\n"
     "worker = threading.Thread(target=done)\n"
     "worker.start()\n"
     "worker.join()\n", 0, False, None),
    ("finishing-thread", "import threading, time\n"
     "threading.Thread(target=time.sleep, args=(0.3,)).start()\n", 0, False, None),
    ("unjoined-thread", "import threading, time\n"
     "def fault():\n"
     "    time.sleep(0.5)\n"
     "    raise RuntimeError('fault-fixture-unjoined-thread')\n"
     "threading.Thread(target=fault).start()\n", 2, True, None),
    ("unfinished-thread", "import threading\n"
     "threading.Thread(target=threading.Event().wait, daemon=True).start()\n", 2, True, None),
)


# The flush-window channel: _gate_exit's fault decision must hold across the exit flushes. The fixture
# fills the child's stdout pipe to its exact capacity (F_GETPIPE_SZ) and leaves one byte in the stream's
# buffer, then arms a timer (SIGALRM unblocked: a caller's blocked mask is inherited) whose signal handler
# drops an object with a faulting destructor, so the fault is recorded while the exit flush is blocked on
# the full pipe (after the settle's thread wait, which a worker-thread fault would not outlast); the
# parent drains stdout only after stderr shows the fault marker. Exit 2 with the marker is required. Red
# when the fault decision runs only before the flushes: the child then ends 0 with the marker on stderr.
_FLUSH_WINDOW_FIXTURE = """import fcntl, os, signal, sys
os.write(1, b"x" * fcntl.fcntl(1, fcntl.F_GETPIPE_SZ))
sys.stdout.write("y")
class Fault:
    def __del__(self):
        raise RuntimeError("fault-fixture-flush-window")
def fault(signum, frame):
    Fault()
signal.signal(signal.SIGALRM, fault)
signal.pthread_sigmask(signal.SIG_UNBLOCK, [signal.SIGALRM])
signal.setitimer(signal.ITIMER_REAL, 1.0)
"""


def _flush_window_failures():
    """One failure string per flush-window requirement the child missed (see _FLUSH_WINDOW_FIXTURE)."""
    import subprocess
    import tempfile
    import threading
    failures = []
    tool = str(Path(__file__).resolve())
    with tempfile.TemporaryDirectory(prefix="opf-init-flush-window-") as tmp:
        probe = Path(tmp) / "probe.py"
        probe.write_text(_FAULT_CHANNEL_PROBE, encoding="utf-8")
        case = Path(tmp) / "flush_window.py"
        case.write_text(_FLUSH_WINDOW_FIXTURE, encoding="utf-8")
        try:
            child = subprocess.Popen([sys.executable, "-I", "-B", str(probe), tool, str(case)],
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except (OSError, subprocess.SubprocessError) as exc:
            return ["fault-channel/flush-window: probe did not run ({})".format(type(exc).__name__)]
        with child:
            killer = threading.Timer(120, child.kill)
            killer.start()
            try:
                header = b""
                while (_FAULT_MARKER + ":").encode("ascii") not in header:
                    line = child.stderr.readline()
                    if not line:
                        break
                    header += line
                child.stdout.read()
                trailer = child.stderr.read()
                code = child.wait()
            finally:
                killer.cancel()
        stderr_text = (header + trailer).decode("utf-8", "replace")
        if code != 2:
            failures.append("fault-channel/flush-window: expected exit 2, got {}".format(code))
        elif (_FAULT_MARKER + ":") not in stderr_text:
            failures.append("fault-channel/flush-window: fault marker absent")
    return failures


# The entry wiring, checked statically on this file's own source: the final __main__ block must be
# exactly _ENTRY_WIRING by AST (spacing and comments aside), so a rewiring that keeps _fail_closed_main
# defined but routes an entry branch around it (a plain sys.exit around the work, say) is red even though
# the channel probes above drive _fail_closed_main directly.
_ENTRY_WIRING = '''if __name__ == "__main__":
    if sys.argv[1:] == ["--self-test"]:
        sys.exit(self_test())
    _fail_closed_main(main)
'''


# For the canonical --self-test entry (F-SELFTEST-NO-MAIN), self_test itself must be the entry-aware
# wrapper, so sys.exit(self_test()) still settles fail-closed and ends with os._exit when this file is
# the process entry.
_SELF_TEST_WIRING = '''def self_test():
    if __name__ == "__main__":
        _fail_closed_main(_self_test)
    return _self_test()
'''

def _entry_wiring_failures():
    """One failure string per entry-wiring requirement this file's own source misses (see _ENTRY_WIRING)."""
    import ast
    failures = []
    try:
        tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    except (OSError, SyntaxError, ValueError) as exc:
        return ["fault-channel/entry-wiring: this file could not be parsed ({})".format(type(exc).__name__)]
    last = tree.body[-1] if tree.body else None
    expected = ast.parse(_ENTRY_WIRING).body[0]
    if last is None or ast.dump(last) != ast.dump(expected):
        failures.append("fault-channel/entry-wiring: the final __main__ block is not the expected "
                        "fail-closed form")
    wrappers = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "self_test"]
    body = list(wrappers[0].body) if len(wrappers) == 1 else []
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and type(body[0].value.value) is str:
        body = body[1:]
    expected_body = ast.parse(_SELF_TEST_WIRING).body[0].body
    if len(wrappers) != 1 or [ast.dump(node) for node in body] != [ast.dump(node) for node in expected_body]:
        failures.append("fault-channel/entry-wiring: self_test is not the entry-aware wrapper (the "
                        "canonical sys.exit(self_test()) must settle through _fail_closed_main)")
    return failures

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
    with tempfile.TemporaryDirectory(prefix="opf-init-fault-channel-") as tmp:
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
    failures.extend(_flush_window_failures())
    failures.extend(_entry_wiring_failures())
    return failures


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-test"]:
        sys.exit(self_test())
    _fail_closed_main(main)
