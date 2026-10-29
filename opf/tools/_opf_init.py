#!/usr/bin/env python3
"""OPF init D1: pure, canonical clean-baseline manifest and ledger builders.

Each builder returns new-document TOML through _opf_emit.emit_checked, reparses it, and refuses
invalid output. No store discovery, publication, Git operation, or render call belongs here.
The manifest fixture in _opf_store remains independent of these production builders.

Defaults follow OPF-SPEC section 9, with optional modules disabled and no profile or vendor.
The initial Markdown view set is pinned; future registry additions do not expand it.
Validation here covers individual bootstrap documents, not whole-store or publication integrity.

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
    if sys.argv[1:] == ["--self-test"]:
        sys.exit(self_test())
    sys.exit(main())
