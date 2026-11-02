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
is caught by the aggregator's subprocess-per-unit runner only once that planned runner ships; until
then the aggregate runs each unit in-process and nothing here catches it.

Run: python3 opf/tools/_opf_init.py --self-test
"""

import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: _opf_init.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _opf_check      # noqa: E402
import _opf_emit       # noqa: E402
import _opf_store      # noqa: E402
from _opf_schema import (  # noqa: E402
    SUPPORTED_SCHEMA, COUNTERS_TOP_KEYS, IMPORTED_PREFIX, validate_counters,
)
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


def _manifest_model(homes2=False):
    """Construct a fresh clean-adopter model; never reuse the validator's test fixture.

    `homes2` (an explicit caller input, never a default) declares the homes-2 pair, spec_version
    2.0.0 with homes = 2 (spec 4.2), and is refused while this tooling keeps SUPPORTED_HOMES below
    2, so no default call reaches it."""
    if homes2 is not False and homes2 is not True:
        raise InitError("homes2 must be a boolean")
    if homes2 and _opf_store.SUPPORTED_HOMES < 2:
        raise InitError("a homes-2 manifest needs tooling that supports homes generation 2")
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
    base = {
        "standard": _opf_store.STANDARD_TOKEN,
        "spec_version": _opf_store.SUPPORTED_SPEC_VERSION,
        "layout": _choice("inline", _opf_store.LAYOUTS),
        "posture": _choice("required", _opf_store.POSTURES),
        "import_status": _choice("none", _opf_store.IMPORT_STATES),
    }
    if homes2:
        base.update(spec_version=_opf_store.HOMES2_SPEC_VERSION, homes=2)
    return {
        "opf": base,
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


def base_table_text(model):
    """The `[opf]` base table of the manifest `model` as TOML text: one `key = value` line per member,
    in the model's own key order (a string quoted, an integer bare). This is the spelling hand-written
    fixture manifests use, so a fixture takes its base declaration (spec_version, and homes where the
    model declares it) from the builder instead of restating it. The text is reparsed and refused
    unless it reads back as exactly the model's table."""
    base = model.get("opf") if isinstance(model, dict) else None
    if not isinstance(base, dict):
        raise InitError("the manifest model carries no [opf] base table")
    lines = ["[opf]"]
    for key, value in base.items():
        lines.append('{} = "{}"'.format(key, value) if type(value) is str
                     else "{} = {}".format(key, value))
    text = "\n".join(lines) + "\n"
    try:
        parsed = tomllib.loads(text)
    except (ValueError, RecursionError, MemoryError):
        parsed = None
    if parsed != {"opf": base}:
        raise InitError("[opf]: the base table does not read back as the model's own")
    return text


def build_manifest(homes2=False):
    """Return the generic adopter's manifest; no profile, foreign paths, or root VERSION.
    `homes2` selects the homes-2 declaration (_manifest_model)."""
    return _emit_validated(_manifest_model(homes2=homes2), _opf_store.MANIFEST_NAME,
                           _opf_store.validate_manifest)


def imported_counter_namespaces(manifest):
    """The namespaces whose imported counter row a store built from `manifest` carries (spec 4.2/8.2):
    one per enabled type that has an imported leaf, over the doctor's authoritative roster, and only
    while the manifest's imported series is active (_opf_check.imported_series_active). None when it
    is inactive (every store this tooling builds by default), so the clean-series ledger is unchanged."""
    if manifest is None or not _opf_check.imported_series_active(manifest):
        return None
    return _opf_check._imported_counter_namespaces(_opf_check._authoritative_types(
        _opf_check._enabled_modules(manifest), manifest.get("types")))


def build_counters(*, seed=None, manifest=None):
    """Return a counters.toml high-water ledger for a fresh store.

    With `seed` omitted (the clean first-adoption bootstrap), returns a ZERO high-water for every
    baseline namespace, including worklog (WL), and for no other namespace: the legacy_fragment
    importer namespace (LF) is deprecated for new stores and is never scaffolded (spec 8.1 reserves LF
    unassigned), so a fresh store carries no LF counter.

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
    authority, not this builder's.

    With `manifest` supplied and its imported series active (imported_counter_namespaces), the ledger
    also carries the quoted `"imported:<NS>"` row of each type that has an imported leaf (spec 4.2/8.2):
    at zero on the fresh path, and on the seed path copied from the seed, which MUST carry every one of
    them (spec 8.2: re-adoption never zero-seeds prior ancestry). Otherwise an imported key in a seed
    is outside the roster and refuses, exactly as with `manifest` omitted."""
    baseline = frozenset(_opf_store.BASELINE_TYPES.values())
    importer = frozenset(_opf_store.IMPORTER_TYPES.values())
    imported = imported_counter_namespaces(manifest)
    imported_keys = frozenset(IMPORTED_PREFIX + ns for ns in imported or ())
    if seed is None:
        document = {"schema": SUPPORTED_SCHEMA, "counters": {ns: 0 for ns in baseline | imported_keys}}
        text = _opf_emit.emit_checked(document)
        _high, findings = validate_counters(tomllib.loads(text), known_namespaces=baseline,
                                            imported_namespaces=imported)
        if findings:
            raise InitError("{}: bootstrap validation failed: {}".format(COUNTERS_NAME, findings))
        return text
    if type(seed) is not dict:
        raise InitError("counters seed must be a validated high-water mapping")
    for ns in sorted(baseline) + sorted(imported_keys):
        if ns not in seed:
            raise InitError("counters seed is missing a high-water for namespace {!r}; a missing "
                            "ancestral value is unknown, and zero is never substituted".format(ns))
    counters = {}
    for ns, val in seed.items():
        if type(ns) is not str or ns not in baseline | importer | imported_keys:
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
                                        optional_namespaces=importer, imported_namespaces=imported)
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


def build_imported_leaf(type_name):
    """Return an empty imported-series leaf (spec 4.2): `entry = []` for the worklog ledger's
    worklog.imported.toml and `record = []` for a <type>.imported.index.toml. A name with no imported
    leaf (legacy_fragment, a name outside the section 8.1 roster, a non-string) refuses, so this
    builder admits exactly the types _opf_store.imported_leaf_names admits."""
    names = _opf_store.imported_leaf_names((type_name,)) if type(type_name) is str else ()
    if not names:
        raise InitError("imported leaf requires a type the imported series carries")
    rows_key = "entry" if type_name == "worklog" else "record"
    text = _opf_emit.emit_checked({"schema": SUPPORTED_SCHEMA, rows_key: []})
    data = tomllib.loads(text)
    # The closed {schema, <rows>} shape the doctor's imported-leaf reader accepts, with no rows.
    if (set(data) != {"schema", rows_key} or type(data.get("schema")) is not int
            or data["schema"] != SUPPORTED_SCHEMA or data.get(rows_key) != []):
        raise InitError("{}: invalid empty imported leaf".format(sorted(names)[0]))
    return text


def imported_leaf_documents(manifest):
    """{leaf file name: text} of the empty imported leaves a store built from `manifest` starts with
    (spec 4.2): one per type of the doctor's authoritative roster that has one, and only while the
    manifest's imported series is active; {} otherwise, so a default store gains no file."""
    if not _opf_check.imported_series_active(manifest):
        return {}
    roster = _opf_check._authoritative_types(_opf_check._enabled_modules(manifest),
                                             manifest.get("types"))
    documents = {}
    for type_name in sorted(roster):
        for name in _opf_store.imported_leaf_names((type_name,)):
            documents[name] = build_imported_leaf(type_name)
    return documents


def bootstrap_documents(model, *, seed=None):
    """{machine-store file name: text} of every bootstrap document a store built from the manifest
    `model` (_manifest_model) starts with, in creation order: the manifest, the counters ledger
    (`seed` as build_counters takes it, with the model's imported counter rows), the version and
    worklog ledgers, one empty index per INDEX_TYPES member, then the empty imported leaves while the
    model's imported series is active (imported_leaf_documents). The plain init command and the init
    operation both build from here, so their rosters cannot drift; the default model adds no file."""
    documents = {
        _opf_store.MANIFEST_NAME: _emit_validated(model, _opf_store.MANIFEST_NAME,
                                                  _opf_store.validate_manifest),
        COUNTERS_NAME: build_counters(seed=seed, manifest=model),
        VERSION_NAME: build_version(),
        WORKLOG_NAME: build_worklog(),
    }
    for type_name in INDEX_TYPES:
        documents[type_name + INDEX_SUFFIX] = build_index(type_name)
    documents.update(imported_leaf_documents(model))
    return documents


# --- self-test --------------------------------------------------------------------------------------

# Whether this file is the process entry (python <file>) rather than an imported module (the opf.py
# aggregate). It is held as a value, so the final block stays the only __main__ guard in the file
# (tools/check_entry_guard.py) while self_test still routes an entry run through _fail_closed_main.
_RUN_AS_ENTRY = __name__ == "__main__"


def self_test():
    """The canonical --self-test entry's suite hook (F-SELFTEST-NO-MAIN). Run as this file's process
    entry (_RUN_AS_ENTRY), it routes the suite through _fail_closed_main, which
    installs the fault recorders before any in-process load, settles them after the suite, and ends the
    process with os._exit, so sys.exit(self_test()) never returns and no atexit callback registered by
    loaded code runs after the verdict. Called in-process (the opf.py aggregate), it runs the suite
    plainly and returns its result; that aggregate entry is a disclosed residual
    until the aggregate gains a subprocess-per-unit runner."""
    if _RUN_AS_ENTRY:
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
        # legacy_fragment is deprecated for new stores and is never scaffolded (spec 8.1 reserves its LF
        # namespace unassigned), so a fresh ledger carries no importer namespace (the revert-flip for a
        # re-seeded LF, beside the pinned "counters canonical bytes" check above).
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

        # The imported series (spec 4.2/8.2) is reached only through an explicit homes-2 manifest under
        # raised gates; each vector patches the shipped constants and restores them in finally.
        def gated(thunk, spec=_opf_store.IMPORTED_SPEC_VERSION):
            saved = (_opf_store.SUPPORTED_HOMES, _opf_store.SUPPORTED_SPEC_VERSION)
            _opf_store.SUPPORTED_HOMES = 2
            if spec is not None:
                _opf_store.SUPPORTED_SPEC_VERSION = spec
            try:
                return thunk()
            except InitError:
                return None
            finally:
                _opf_store.SUPPORTED_HOMES, _opf_store.SUPPORTED_SPEC_VERSION = saved

        def refusal(thunk):
            # The InitError message of a refusal, or None when the thunk did not refuse.
            try:
                thunk()
            except InitError as exc:
                return str(exc)
            return None

        check("homes-2 manifest refuses at the shipped constants",
              rejects(lambda: _manifest_model(homes2=True)) and rejects(lambda: build_manifest(True)))
        # homes2 is an explicit boolean: a stand-in is refused by name at the shipped constants and
        # with homes 2 supported, never read as the default or as a homes-2 request (red when the
        # boolean check is dropped: 0 and None then build the default model).
        for bad in (1, 0, "yes", None):
            check("homes2 {!r} refuses as a non-boolean".format(bad), all(
                "homes2 must be a boolean" in (message or "") for message in (
                    refusal(lambda: _manifest_model(homes2=bad)),
                    refusal(lambda: build_manifest(homes2=bad)),
                    gated(lambda: refusal(lambda: _manifest_model(homes2=bad))),
                    gated(lambda: refusal(lambda: build_manifest(homes2=bad))))))
        homes2_model = gated(lambda: _manifest_model(homes2=True), spec=None) or {"opf": {}}
        homes2_text = gated(lambda: build_manifest(homes2=True))
        check("homes-2 manifest declares the reserved pair", homes2_model["opf"].get("homes") == 2
              and homes2_model["opf"].get("spec_version") == _opf_store.HOMES2_SPEC_VERSION
              and homes2_text is not None
              and tomllib.loads(homes2_text)["opf"] == homes2_model["opf"])
        imported_ns = sorted(gated(lambda: imported_counter_namespaces(homes2_model)) or ())
        active_text = gated(lambda: build_counters(manifest=homes2_model))
        active_counters = tomllib.loads(active_text) if active_text is not None else None
        check("imported counter rows on an active manifest", active_counters is not None
              and imported_ns == sorted(_opf_store.BASELINE_TYPES.values())
              and active_counters["counters"] == dict({ns: 0 for ns in baseline_ns},
                                                       **{"imported:" + ns: 0 for ns in imported_ns})
              and not validate_counters(active_counters, known_namespaces=baseline_ns,
                                        imported_namespaces=imported_ns)[1])
        check("no imported rows on a 2.0.0 homes-2 manifest at the shipped constants",
              build_counters(manifest=homes2_model) == build_counters()
              and imported_leaf_documents(homes2_model) == {})
        check("no imported rows with homes 2 supported but not the series",
              gated(lambda: build_counters(manifest=homes2_model), spec=None) == build_counters()
              and gated(lambda: imported_leaf_documents(homes2_model), spec=None) == {})
        active_seed = dict({ns: 0 for ns in baseline_ns}, **{"imported:" + ns: 0 for ns in imported_ns})
        check("an active seed missing an imported row refuses", gated(lambda: build_counters(
            seed={k: v for k, v in active_seed.items() if k != "imported:BI"},
            manifest=homes2_model)) is None)
        # The seed loop names the missing imported row before any ledger is built (red when it covers
        # the baseline only: the later ledger validation still refuses, under another message).
        check("an active seed missing an imported row refuses by name",
              "counters seed is missing a high-water for namespace 'imported:BI'" in (
                  gated(lambda: refusal(lambda: build_counters(
                      seed={k: v for k, v in active_seed.items() if k != "imported:BI"},
                      manifest=homes2_model))) or ""))
        copied = gated(lambda: build_counters(seed=dict(active_seed, **{"imported:BI": 4}),
                                              manifest=homes2_model))
        check("an active seed copies its imported rows",
              copied is not None and tomllib.loads(copied)["counters"]["imported:BI"] == 4)
        check("an inactive seed carrying an imported row refuses",
              rejects(lambda: build_counters(seed=dict(good_seed, **{"imported:BI": 0}))))
        expected_record_leaf = "record = []\n" + schema_line
        for leaf_type in sorted(_opf_store.BASELINE_TYPES):
            (leaf_name,) = _opf_store.imported_leaf_names((leaf_type,))
            text = build_imported_leaf(leaf_type)
            documents[leaf_name] = text
            check(leaf_name + " imported leaf canonical bytes", text.encode("utf-8") == (
                expected_worklog if leaf_type == "worklog" else expected_record_leaf).encode("ascii"))
        for bad_name in ("legacy_fragment", "version", "unknown", "", None):
            check("imported leaf refuses {!r}".format(bad_name),
                  rejects(lambda: build_imported_leaf(bad_name)))
        check("imported leaves empty when inactive", imported_leaf_documents(homes2_model) == {}
              and imported_leaf_documents(model) == {})
        active_leaves = gated(lambda: imported_leaf_documents(homes2_model)) or {}
        check("imported leaves cover every baseline type",
              sorted(active_leaves) == sorted(_opf_store.imported_leaf_names(_opf_store.BASELINE_TYPES)))
        narrowed = deepcopy(homes2_model)
        del narrowed["types"]["finding"]
        check("imported leaves follow the authoritative roster",
              "finding.imported.index.toml" in (gated(lambda: imported_leaf_documents(narrowed)) or {}))
        # The leaves and the counter rows key on the one activation flag, so they cover the same types.
        check("imported leaves and counter rows agree",
              sorted(_opf_store.BASELINE_TYPES[t] for t in _opf_store.BASELINE_TYPES
                     if _opf_store.imported_leaf_names((t,)) & set(active_leaves)) == imported_ns)
        # The shared bootstrap builder: on the default model, exactly the individual builders'
        # documents in creation order (red on an added, dropped, reordered or changed document);
        # on the active homes-2 model, the same documents with the imported counter rows plus every
        # imported leaf after them (red when the leaves or the manifest-keyed counters are dropped);
        # and a seed reaches the counters builder unchanged.
        individual = [(_opf_store.MANIFEST_NAME, manifest_text), (COUNTERS_NAME, build_counters()),
                      (VERSION_NAME, build_version()), (WORKLOG_NAME, build_worklog())]
        individual += [(t + INDEX_SUFFIX, build_index(t)) for t in INDEX_TYPES]
        check("bootstrap documents on the default model are the individual builders' documents",
              list(bootstrap_documents(model).items()) == individual)
        active_docs = gated(lambda: bootstrap_documents(homes2_model)) or {}
        check("bootstrap documents on an active homes-2 model add the imported leaves last",
              list(active_docs)[:len(individual)] == [name for name, _text in individual]
              and list(active_docs)[len(individual):] == list(active_leaves)
              and active_docs.get(COUNTERS_NAME) == active_text
              and active_docs.get(_opf_store.MANIFEST_NAME) == homes2_text)
        check("bootstrap documents pass a seed to the counters builder",
              bootstrap_documents(model, seed=good_seed)[COUNTERS_NAME]
              == build_counters(seed=good_seed))
        # The fixture-facing [opf] spelling: the hand-written fixtures' exact lines on the default model
        # (red on a reordered, requoted or dropped line), the homes-2 pair on the explicit model, and a
        # refusal for a model with no base table or a value that does not read back.
        check("base table text is the fixtures' default [opf] lines",
              base_table_text(model) == '[opf]\nstandard = "opf"\nspec_version = "{}"\nlayout = "inline"\n'
              'posture = "required"\nimport_status = "none"\n'.format(_opf_store.SUPPORTED_SPEC_VERSION))
        homes2_base = gated(lambda: base_table_text(homes2_model), spec=None) or ""
        check("base table text carries the homes-2 pair",
              homes2_base.endswith('spec_version = "{}"\nlayout = "inline"\nposture = "required"\n'
                                   'import_status = "none"\nhomes = 2\n'.format(
                                       _opf_store.HOMES2_SPEC_VERSION))
              and tomllib.loads(homes2_base) == {"opf": homes2_model["opf"]})
        check("base table text refuses a model with no base table or a value that does not read back",
              rejects(lambda: base_table_text({}))
              and rejects(lambda: base_table_text({"opf": {"standard": 'a"b'}})))

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
# a passing verdict the settle waits (_SETTLE_SECONDS in all) for every other thread still running,
# threading's census and the runtime's live count of low-level _thread workers together, so a worker's
# later fault is recorded instead of killed silently by the exit, and a thread or low-level worker still
# running at the bound fails the verdict closed (exit 2). On Linux the settle also waits on the OS's own
# thread list for this process (_os_threads_pending), which still shows a worker while the runtime clears
# its thread state (its thread-local and context values' destructors run there, after the runtime's live
# count and threading's census already dropped it), a faulthandler watchdog and a thread a C extension
# starts. The verdict then ends the process with os._exit, so an
# atexit callback registered by loaded code can never run after it (that channel is unreachable, not
# merely disclosed). Only a SystemExit with code None or 0 ending a worker thread is the interpreter's
# normal SUCCESSFUL thread exit (default-hook parity) and is not recorded; any other code is an
# unsuccessful exit a worker reported, so it is recorded.
# A low-level _thread worker's SystemExit never reaches any hook (the interpreter drops it), so the
# start entries and threading's saved module aliases of them are wrapped from before the gate's work and
# a non-zero code ending such a worker is recorded as a fault there. The subinterpreter modules are made
# unimportable before the gate's work, because a thread inside a subinterpreter reports its faults to
# that interpreter's own hooks, never these recorders. Stated residuals, not closed: where the platform
# has no per-process thread list (outside Linux) a thread outside the Python runtime stays invisible to
# the census and the live count; and a process the loaded code starts is not followed, so one still
# running after this gate ends is outside its verdict.
# Each recorder chains to the hook it wrapped, so the usual traceback still reaches stderr after the
# marker line. After the exit flushes the settle stops the collector, blocks every signal, runs any
# handler already tripped, and re-reads the record and every liveness term, so a fault recorded or a
# worker a signal handler started while a flush was blocked on a full pipe still fails the verdict, and
# no loaded code can run between that last read and the exit.
# Paths that end outside _gate_exit (a propagating KeyboardInterrupt, an escaping exception) already end
# non-zero, and an ACCIDENTAL fault in an atexit callback cannot turn that non-zero end into exit 0, so
# no background fault reads as a pass there. Residual: loaded code replacing these hooks, this record or
# the exit itself is the reporting-machinery channel disclosed once in the _opf_views class disclosure
# (deliberately hostile loaded code is accepted there and not closed).
_LOADED_FAULTS = []
# A started low-level worker's liveness flag, set synchronously at the start call: the runtime
# live count misses the brief window before a just-started worker's OS thread registers, so the
# settle also waits while any flag is set, never breaking on that transient zero.
_LOADED_WORKERS = []
_FAULT_MARKER = "LOADED-CODE-FAULT"


def _install_fault_hooks():
    # A local alias: opf.py's close-lifecycle census reads an attribute store through the bare module name
    # as shadowing that module.
    import threading as _threads

    # The modules that create a subinterpreter are made unimportable (an import is an ImportError the
    # gate's work fails on): a thread inside one reports its faults to that interpreter's own hooks.
    for _name in ("_interpreters", "concurrent.interpreters", "_testinternalcapi", "_testcapi"):
        sys.modules[_name] = None

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

    import _thread as _lowlevel

    def _record_lowlevel_fault():
        _LOADED_FAULTS.append("lowlevel-worker")
        try:
            sys._loaded_code_fault = True
            sys.stderr.write("{}: a lowlevel-worker fault was recorded; a passing verdict will fail "
                             "closed\n".format(_FAULT_MARKER))
        except Exception:
            pass

    import types as _types
    _bootstrap = _threads.Thread._bootstrap

    def _genuine_bootstrap(target):
        # A thread start is threading's own only when its target is exactly a bound method whose function
        # is threading.Thread._bootstrap and whose instance is a threading.Thread (read through the exact
        # method type, never an attribute the target supplies). Such a worker's fault already reaches
        # threading.excepthook, so it is passed through untouched; wrapping it would also replace the
        # target an audit-based observer reads, making threading's own start look like a raw one.
        return (type(target) is _types.MethodType and target.__func__ is _bootstrap
                and isinstance(target.__self__, _threads.Thread))

    def _wrap_lowlevel(original):
        # A raw low-level worker's SystemExit reaches no hook (the interpreter drops it), so the start
        # entry wraps the worker's function and records a non-zero code as a fault (None or 0 is the
        # successful low-level exit, default-interpreter parity; the code is read through exact built-in
        # types alone and never formatted), and tracks the worker's liveness flag. A genuine threading
        # start is passed through untouched.
        def _start(function, *args, **kwargs):
            if _genuine_bootstrap(function):
                return original(function, *args, **kwargs)
            state = [True]
            def _run(*wargs, **wkwargs):
                try:
                    return function(*wargs, **wkwargs)
                except SystemExit as exc:
                    code = getattr(exc, "code", None)
                    if not (code is None or (type(code) is int and code == 0)):
                        _record_lowlevel_fault()
                    raise
                finally:
                    state[0] = False
            result = original(_run, *args, **kwargs)
            _LOADED_WORKERS.append(state)
            return result
        return _start

    _wrapped = []
    for _entry in ("start_new_thread", "start_new", "start_joinable_thread"):
        if hasattr(_lowlevel, _entry):
            _original = getattr(_lowlevel, _entry)
            _replacement = _wrap_lowlevel(_original)
            setattr(_lowlevel, _entry, _replacement)
            _wrapped.append((_original, _replacement))
    for _alias in dir(_threads):
        _value = getattr(_threads, _alias)
        for _original, _replacement in _wrapped:
            if _value is _original:
                setattr(_threads, _alias, _replacement)


# The OS's own list of this process's threads (Linux): it still shows a worker while the runtime clears
# its thread state, a faulthandler watchdog and a thread a C extension starts. None where the platform has
# no such list; the runtime's own counts then carry the settle alone (a stated residual).
_TASK_DIR = "/proc/self/task" if sys.platform.startswith("linux") else None


def _os_threads_pending():
    """True while the OS lists a thread of this process besides the calling one, and when that list
    exists but cannot be read (fail-closed); False where the platform has no such list."""
    import os
    if _TASK_DIR is None:
        return False
    try:
        return len(os.listdir(_TASK_DIR)) != 1
    except OSError:
        return True


def _drain_pending_signals():
    """A Python-level call: the interpreter runs any signal handler already tripped on entry here."""
    return None


_SETTLE_SECONDS = 60.0


def _gate_exit(code):
    """Settle and END the process: before a passing verdict, wait (up to _SETTLE_SECONDS in all) for every
    other thread still running, so a fault it raises later is recorded, never killed silently by the exit,
    and fail closed (exit 2) if one still runs at the bound (the census covers threading's threads and,
    through the runtime's live count, workers started through the low-level _thread entry points, so a
    low-level worker still running at the bound also fails closed, and on Linux the OS's own thread list
    covers a worker still clearing its thread state, a watchdog and a C extension's thread; elsewhere a
    thread outside the Python runtime is invisible: a stated residual); force a recorded background fault
    to exit 2 over a passing verdict, flush both streams, then stop the collector, block every signal, run
    any handler already tripped and re-read the record and every liveness term AFTER the flushes (a fault
    recorded or a worker started while a flush was blocked, on a full pipe say, must still fail the
    verdict; its late marker is written straight to fd 2, past the buffers), then os._exit, so no atexit
    callback registered by loaded code
    runs after the verdict (the gate's own cleanup runs inside its work; this gate registers no atexit work
    of its own)."""
    import os
    import threading as _threads
    import time
    import _thread as _lowlevel
    if type(code) is bool:
        code = 1 if code else 0
    elif code is None:
        code = 0
    elif type(code) is not int:
        # The code object is never formatted: a loaded object's __str__ must not run here.
        sys.stderr.write("{}: a non-int SystemExit code at the gate entry; exit 1\n".format(_FAULT_MARKER))
        code = 1
    low = code & 0xFF
    if code != 0 and low == 0:
        # os._exit truncates its status to the low 8 bits, so this non-zero code would end the
        # process with exit status 0, a silent pass; fail closed instead.
        sys.stderr.write("{}: a SystemExit code outside the exit-status range at the gate entry; exit 2\n".format(
            _FAULT_MARKER))
        code = 2
    else:
        # os._exit refuses a code outside the C int range (OverflowError) instead of exiting, and
        # that escape would let atexit callbacks registered by loaded code run after the verdict:
        # take the low byte, the status any in-range code would report, so the exit below always runs.
        code = low
    if code == 0:
        current = _threads.current_thread()
        deadline = time.monotonic() + _SETTLE_SECONDS
        while True:
            running = [worker for worker in _threads.enumerate()
                       if worker is not current and worker.is_alive()]
            pending = (_lowlevel._count() or any(_state[0] for _state in _LOADED_WORKERS)
                       or _os_threads_pending())
            remaining = deadline - time.monotonic()
            if (not running and not pending) or remaining <= 0:
                break
            if running:
                running[0].join(min(remaining, 0.5))
            else:
                time.sleep(min(remaining, 0.05))
        if running or pending:
            sys.stderr.write("{}: a worker thread or low-level worker was still running at the verdict; "
                             "fail-closed\n".format(_FAULT_MARKER))
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
    if code == 0:
        # The verdict stands only once no loaded code can run before the exit: the collector is stopped,
        # every signal is blocked (no other thread is left to take one), a handler already tripped runs
        # now, and the record and every liveness term are read afresh, so a fault or a worker a handler
        # started while a flush was blocked fails the verdict.
        import gc as _gc
        import signal as _signals
        try:
            _gc.disable()
            _signals.pthread_sigmask(_signals.SIG_BLOCK, _signals.valid_signals())
            _drain_pending_signals()
        except BaseException:  # noqa: BLE001  a handler's own escape fails the verdict, never the exit
            code = 2
        if (_LOADED_FAULTS or getattr(sys, "_loaded_code_fault", False) or _lowlevel._count()
                or any(_state[0] for _state in _LOADED_WORKERS) or _os_threads_pending()):
            code = 2
        if code != 0:
            try:
                os.write(2, ("{}: a background fault or a worker was recorded during the exit flush; "
                             "fail-closed\n".format(_FAULT_MARKER)).encode("utf-8"))
            except OSError:
                pass
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
    # Low-level _thread workers: the interpreter drops a SystemExit ending one without calling any hook,
    # so the wrapped start entry records a non-zero code as a fault (None or 0 stays the successful
    # low-level exit), and the settle waits on the runtime's live count, so one still running at the
    # bound fails the verdict closed while one that finishes is waited out.
    ("lowlevel-systemexit", "import _thread, threading\n"
     "done = threading.Event()\n"
     "def worker():\n"
     "    try:\n"
     "        raise SystemExit(7)\n"
     "    finally:\n"
     "        done.set()\n"
     "_thread.start_new_thread(worker, ())\n"
     "done.wait()\n", 2, True, None),
    ("lowlevel-systemexit-zero", "import _thread, threading\n"
     "done = threading.Event()\n"
     "def worker():\n"
     "    try:\n"
     "        raise SystemExit(0)\n"
     "    finally:\n"
     "        done.set()\n"
     "_thread.start_new_thread(worker, ())\n"
     "done.wait()\n", 0, False, None),
    ("lowlevel-finishing", "import _thread, time\n"
     "_thread.start_new_thread(time.sleep, (0.3,))\n", 0, False, None),
    ("lowlevel-unfinished", "import _thread, threading\n"
     "_thread.start_new_thread(threading.Event().wait, ())\n", 2, True, None),
    # A handled failed low-level start leaves no pending worker: red when the start flag is recorded
    # before the start call (the flag then stays set and the settle fails closed at the bound).
    ("lowlevel-failed-start", "import _thread\n"
     "try:\n"
     "    _thread.start_new_thread(print)\n"
     "except TypeError:\n"
     "    pass\n", 0, False, None),
    # Thread-state teardown: a worker's thread-local and context-variable values are destroyed while the
    # runtime clears its thread state, after its live count and threading's census already dropped it;
    # the OS thread list still shows the worker, so the settle waits and records the fault (red without
    # that term: each ends 0).
    ("lowlevel-local-destructor", "import _thread, threading, time\n"
     "entered = threading.Event()\n"
     "store = threading.local()\n"
     "class Held:\n"
     "    def __del__(self):\n"
     "        entered.set()\n"
     "        time.sleep(0.5)\n"
     "        raise RuntimeError('fault-fixture-local-destructor')\n"
     "def worker():\n"
     "    store.value = Held()\n"
     "_thread.start_new_thread(worker, ())\n"
     "entered.wait()\n", 2, True, None),
    ("thread-local-destructor", "import threading, time\n"
     "entered = threading.Event()\n"
     "store = threading.local()\n"
     "class Held:\n"
     "    def __del__(self):\n"
     "        entered.set()\n"
     "        time.sleep(0.5)\n"
     "        raise RuntimeError('fault-fixture-thread-local-destructor')\n"
     "def worker():\n"
     "    store.value = Held()\n"
     "threading.Thread(target=worker).start()\n"
     "entered.wait()\n", 2, True, None),
    ("contextvar-destructor", "import _thread, contextvars, threading, time\n"
     "entered = threading.Event()\n"
     "held = contextvars.ContextVar('held')\n"
     "class Held:\n"
     "    def __del__(self):\n"
     "        entered.set()\n"
     "        time.sleep(0.5)\n"
     "        raise RuntimeError('fault-fixture-contextvar-destructor')\n"
     "def worker():\n"
     "    held.set(Held())\n"
     "_thread.start_new_thread(worker, ())\n"
     "entered.wait()\n", 2, True, None),
    # The runtime's live count, the term that carries the settle where the platform has no thread list:
    # the fixture switches the OS list off in the probe's gate, and a low-level worker whose return value's
    # destructor still runs after its callback returned must still be waited out and recorded (red
    # without the live count: it ends 0).
    ("runtime-count-return-destructor", "import _thread, sys, threading, time\n"
     "sys.modules['__main__'].gate._TASK_DIR = None\n"
     "entered = threading.Event()\n"
     "class Result:\n"
     "    def __del__(self):\n"
     "        entered.set()\n"
     "        time.sleep(0.5)\n"
     "        raise RuntimeError('fault-fixture-return-destructor')\n"
     "def worker():\n"
     "    return Result()\n"
     "_thread.start_new_thread(worker, ())\n"
     "entered.wait()\n", 2, True, None),
    # The settle's own reads of those two terms: a teardown destructor or a return value's destructor
    # that finishes without a fault is waited out and passes (red without the settle-position read: the
    # re-read after the flushes then still sees the worker and fails the clean run closed).
    ("lowlevel-local-finishing", "import _thread, threading, time\n"
     "entered = threading.Event()\n"
     "store = threading.local()\n"
     "class Held:\n"
     "    def __del__(self):\n"
     "        entered.set()\n"
     "        time.sleep(0.3)\n"
     "def worker():\n"
     "    store.value = Held()\n"
     "_thread.start_new_thread(worker, ())\n"
     "entered.wait()\n", 0, False, None),
    ("runtime-count-return-finishing", "import _thread, sys, threading, time\n"
     "sys.modules['__main__'].gate._TASK_DIR = None\n"
     "entered = threading.Event()\n"
     "class Result:\n"
     "    def __del__(self):\n"
     "        entered.set()\n"
     "        time.sleep(0.3)\n"
     "def worker():\n"
     "    return Result()\n"
     "_thread.start_new_thread(worker, ())\n"
     "entered.wait()\n", 0, False, None),
    # Capability removal: every subinterpreter module is unimportable (red when any one is left
    # importable: the fixture then ends 3).
    ("subinterpreter-modules", "import importlib\n"
     "for name in ('_interpreters', 'concurrent.interpreters', '_testinternalcapi', '_testcapi'):\n"
     "    try:\n"
     "        importlib.import_module(name)\n"
     "    except ImportError:\n"
     "        continue\n"
     "    raise SystemExit(3)\n", 0, False, None),
    # An armed faulthandler watchdog is a C-level thread only the OS thread list shows; still armed at
    # the bound it fails the verdict closed (red without that term: it ends 0).
    ("watchdog-armed", "import faulthandler\n"
     "faulthandler.dump_traceback_later(600)\n", 2, True, None),
    # Exit-status range: os._exit truncates its status to the low 8 bits, so a non-zero SystemExit
    # code that is a multiple of 256 would end the child with status 0, a silent pass; the gate maps
    # it to 2. The neighbouring codes pass through (257 reaches the parent as its low byte, 1).
    ("exit-status-255", "raise SystemExit(255)\n", 255, False, None),
    ("exit-status-256", "raise SystemExit(256)\n", 2, True, None),
    ("exit-status-257", "raise SystemExit(257)\n", 1, False, None),
    ("exit-status-negative-256", "raise SystemExit(-256)\n", 2, True, None),
    # Codes around the ends of the C int range: os._exit raises OverflowError on a code outside that
    # range instead of exiting, which would let the loaded code's atexit callbacks run after the
    # verdict; the gate takes the low byte first, so the exit always runs. The wide codes (beyond
    # each end, with non-zero low bytes) reach the parent as their low bytes only through that
    # ordering. The boundary codes (2**31, just past the positive end, and -(2**31), exactly the
    # negative end) have a zero low byte and fail closed as exit 2; the multiple-of-256 rule alone
    # already gives both that outcome, so those two cases are preservation controls over the range
    # ends, not evidence that the ordering is required. The code at the positive end itself,
    # 2**31 - 1, has low byte 255 and is inside os._exit's accepted range.
    ("exit-status-wide-positive", "import atexit, sys\n"
     "atexit.register(sys.stderr.write, 'FAULT-FIXTURE-ATEXIT-RAN\\n')\n"
     "raise SystemExit(2**31 + 1)\n", 1, False, "FAULT-FIXTURE-ATEXIT-RAN"),
    ("exit-status-wide-negative", "import atexit, sys\n"
     "atexit.register(sys.stderr.write, 'FAULT-FIXTURE-ATEXIT-RAN\\n')\n"
     "raise SystemExit(-(2**31) - 1)\n", 255, False, "FAULT-FIXTURE-ATEXIT-RAN"),
    ("exit-status-boundary-positive", "raise SystemExit(2**31)\n", 2, True, None),
    ("exit-status-boundary-negative", "raise SystemExit(-(2**31))\n", 2, True, None),
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

# The same window with a start instead of a fault: the handler starts a low-level worker that never ends
# while the exit flush is blocked, then reports on fd 2, and the parent drains stdout only after that
# line. Exit 2 with the marker is required. Red when only the record is read after the flushes: the
# child then ends 0 with the worker still running.
_FLUSH_WINDOW_START_FIXTURE = """import _thread, fcntl, os, signal, sys, threading
os.write(1, b"x" * fcntl.fcntl(1, fcntl.F_GETPIPE_SZ))
sys.stdout.write("y")
def start(signum, frame):
    _thread.start_new_thread(threading.Event().wait, ())
    os.write(2, b"FAULT-FIXTURE-FLUSH-START\\n")
signal.signal(signal.SIGALRM, start)
signal.pthread_sigmask(signal.SIG_UNBLOCK, [signal.SIGALRM])
signal.setitimer(signal.ITIMER_REAL, 1.0)
"""


def _flush_window_failures():
    """One failure string per flush-window requirement a child missed (see _FLUSH_WINDOW_FIXTURE and
    _FLUSH_WINDOW_START_FIXTURE)."""
    import subprocess
    import tempfile
    import threading
    failures = []
    tool = str(Path(__file__).resolve())
    with tempfile.TemporaryDirectory(prefix="opf-init-flush-window-") as tmp:
        probe = Path(tmp) / "probe.py"
        probe.write_text(_FAULT_CHANNEL_PROBE, encoding="utf-8")
        for label, fixture, ready in (
                ("flush-window", _FLUSH_WINDOW_FIXTURE, _FAULT_MARKER + ":"),
                ("flush-window-start", _FLUSH_WINDOW_START_FIXTURE, "FAULT-FIXTURE-FLUSH-START")):
            case = Path(tmp) / (label + ".py")
            case.write_text(fixture, encoding="utf-8")
            try:
                child = subprocess.Popen([sys.executable, "-I", "-B", str(probe), tool, str(case)],
                                         stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            except (OSError, subprocess.SubprocessError) as exc:
                failures.append("fault-channel/{}: probe did not run ({})".format(label, type(exc).__name__))
                continue
            with child:
                killer = threading.Timer(120, child.kill)
                killer.start()
                try:
                    header = b""
                    while ready.encode("ascii") not in header:
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
                failures.append("fault-channel/{}: expected exit 2, got {}".format(label, code))
            elif (_FAULT_MARKER + ":") not in stderr_text:
                failures.append("fault-channel/{}: fault marker absent".format(label))
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
    if _RUN_AS_ENTRY:
        _fail_closed_main(_self_test)
    return _self_test()
'''

# The one binding of _RUN_AS_ENTRY, checked by _entry_wiring_failures: a rebinding (a constant, a later
# store) would route an entry run of self_test around _fail_closed_main.
_ENTRY_FLAG_WIRING = '_RUN_AS_ENTRY = __name__ == "__main__"\n'


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
    stores = [node for node in ast.walk(tree)
              if isinstance(node, ast.Name) and node.id == "_RUN_AS_ENTRY" and not isinstance(node.ctx, ast.Load)]
    flag = ast.dump(ast.parse(_ENTRY_FLAG_WIRING).body[0])
    if len(stores) != 1 or sum(ast.dump(node) == flag for node in tree.body) != 1:
        failures.append("fault-channel/entry-wiring: _RUN_AS_ENTRY is not bound exactly once, at module "
                        "scope, as the __main__ comparison")
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
