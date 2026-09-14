#!/usr/bin/env python3
"""OPF init D1: pure, canonical clean-baseline manifest and ledger builders.

Each builder returns new-document TOML through _opf_emit.emit_checked, reparses it, and refuses
invalid output. No store discovery, publication, Git operation, or render call belongs here.
The manifest fixture in _opf_store remains independent of these production builders.

Defaults follow OPF-SPEC section 9, with optional modules disabled and no profile or vendor.
The initial Markdown view set is pinned; future registry additions do not expand it.
Validation here covers individual bootstrap documents, not whole-store or publication integrity.

Run: python3 tools/_opf_init.py --self-test
"""

import sys
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

try:
    import tomllib
except ModuleNotFoundError:
    raise SystemExit("error: Python 3.11+ is required (tomllib)")

_INITIAL_VIEW_NAMES = (
    "TODO.md", "BACKLOG.md", "PIPELINE.md", "DONE.md", "FINDINGS.md", "DECISIONS.md",
    "BLOCKS.md", "HANDOFF.md", "REFERENCES.md", "WORKLOG.md", "VERSION.md",
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
        "devprocess": {
            "standard": _opf_store.STANDARD_TOKEN,
            "spec_version": "1.0.0",
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


def build_counters():
    """Return a zero high-water for every baseline namespace, including worklog."""
    namespaces = frozenset(_opf_store.BASELINE_TYPES.values())
    document = {"schema": SUPPORTED_SCHEMA, "counters": {ns: 0 for ns in namespaces}}
    text = _opf_emit.emit_checked(document)
    _high, findings = validate_counters(tomllib.loads(text), known_namespaces=namespaces)
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
        check("devprocess defaults", manifest["devprocess"] == {
            "standard": _opf_store.STANDARD_TOKEN, "spec_version": "1.0.0",
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
            "DECISIONS.md": ("composed", ["pending_decision", "autonomous_decision"]),
            "BLOCKS.md": ("composed", ["block"]),
            "HANDOFF.md": ("composed", ["handoff"]),
            "REFERENCES.md": ("composed", ["reference"]),
            "WORKLOG.md": ("deterministic", ["worklog"]),
            "VERSION.md": ("deterministic", ["version"]),
        }
        check("pinned view inventory and destinations", manifest["views"] == {
            name: {"kind": kind, "sources": sources, "target": ".working/" + name}
            for name, (kind, sources) in expected_views.items()
        })
        for table, keys in (
                ("devprocess", _opf_store.DEVPROCESS_KEYS), ("store", _opf_store.STORE_KEYS),
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
            "AD = 0\nBI = 0\nBL = 0\nDN = 0\nFN = 0\nHO = 0\nPD = 0\nRF = 0\nWL = 0\n"
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
                ("invalid posture", "devprocess", "posture", "unknown")):
            bad = deepcopy(manifest)
            bad[table][key] = value
            result = _opf_store.validate_manifest(bad)
            check(label, result.status == _opf_store.INVALID and bool(result.findings))
        bad = deepcopy(manifest)
        del bad["devprocess"]["layout"]
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
            {"devprocess": {"standard": _opf_store.STANDARD_TOKEN}},
            _opf_store.MANIFEST_NAME, _opf_store.validate_manifest)))
    except Exception as exc:
        print("OPF-INIT SELF-TEST ERROR: {}; fail-closed".format(exc), file=sys.stderr)
        return 2

    if failures:
        for name in failures:
            print("FAIL: " + name, file=sys.stderr)
        print("OPF-INIT SELF-TEST: FAIL ({} of {} checks)".format(len(failures), checked),
              file=sys.stderr)
        return 1
    print("OPF-INIT SELF-TEST: PASS ({} checks)".format(checked))
    return 0


def main():
    if sys.argv[1:] in (["--self-test"], ["--selftest"]):
        return self_test()
    print("usage: _opf_init.py --self-test (a library module; no live mode)", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
