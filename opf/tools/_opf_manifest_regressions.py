#!/usr/bin/env python3
"""Generated finding fixtures, with a source-derived emission-site census.

Base cases use the production validator unchanged. Profile/control cases supply
the real validator's optional control at the test seam: production intake is
base-only, but must transport any validator finding without probing a source.
This covers finding classes, not every possible malformed value. The generated
rows run against MANIFEST_CALLERS in _manifest_intake_regressions; plan_views
enters directly, before any declared source is read.
"""
import ast
import copy
import inspect
import sys

import _opf_init
import _opf_store


MANIFEST_CALLERS = ("loader", "views", "plan_views", "import",
                    "changelog", "absorb", "doctor")


def manifest_cases(check):
    valid = _opf_store.tomllib.loads(_opf_init.build_manifest())
    rows = []

    def add(name, path, value=None, *, delete=False):
        data = copy.deepcopy(valid)
        target = data
        for key in path[:-1]:
            target = target.setdefault(key, {})
        if delete:
            target.pop(path[-1], None)
        else:
            target[path[-1]] = value
        rows.append((name, data))

    rows.append(("manifest-not-table", []))
    add("identity", ("opf",), {})
    add("top-level-unknown", ("unknown",), {})
    add("opf-unknown", ("opf", "unknown"), True)
    add("opf-required", ("opf", "posture"), delete=True)
    add("spec-semver", ("opf", "spec_version"), "bad")
    add("spec-old", ("opf", "spec_version"), "0.0.0")
    for key in ("layout", "posture", "import_status"):
        add(key + "-type", ("opf", key), [])
        add(key + "-enum", ("opf", key), "unknown")
    add("homes-generation", ("opf", "homes"), True)
    add("worklog-generation", ("opf", "worklog"), True)
    add("worklog-ceiling", ("opf", "worklog"), 2)
    for section in ("store", "modules", "vendors", "types", "providers",
                    "views", "deliverables", "archive", "unmanaged", "profiles"):
        add(section + "-table", (section,), [])
    for section in ("store", "modules", "vendors", "archive", "unmanaged"):
        add(section + "-unknown", (section, "unknown"), True)
    add("store-sync-target", ("store", "sync_target"), [])
    add("module-boolean", ("modules", "governance"), "yes")
    add("vendors-list", ("vendors", "registered"), "x-demo")
    add("vendor-namespace", ("vendors", "registered"), ["bad"])
    add("types-empty", ("types",), {})
    add("type-table", ("types", "worklog"), [])
    add("type-unknown-key", ("types", "worklog", "unknown"), True)
    add("type-namespace-shape", ("types", "worklog", "namespace"), "bad")
    add("type-namespace-binding", ("types", "worklog", "namespace"), "ZZ")
    add("type-namespace-duplicate", ("types", "finding", "namespace"), "WL")
    add("type-module-disabled", ("types", "session_lease"), {"namespace": "SL"})
    add("type-reserved", ("types", "transaction"), {"namespace": "TX"})
    add("type-unknown", ("types", "unknown"), {"namespace": "ZZ"})
    for section in ("providers", "views", "deliverables"):
        add(section + "-entry-table", (section, "fixture"), [])
        add(section + "-entry-unknown", (section, "fixture"), {"unknown": True})
    add("provider-handler", ("providers", "fixture"), {"roles": ["create"]})
    add("provider-roles", ("providers", "fixture"), {"handler": "fixture"})
    for section in ("views", "deliverables"):
        kind = "deterministic" if section == "views" else "curated"
        good = {"kind": kind, "target": "fixture.md"}
        if section == "views":
            good["sources"] = ["worklog"]
        for field, value, suffix in (
                ("kind", "unknown", "kind"), ("target", "", "target"),
                ("target", "../escape", "containment")):
            add(section + "-" + suffix, (section, "fixture"), dict(good, **{field: value}))
        if section == "views":
            add("views-sources", (section, "fixture"), dict(good, sources=[]))
    add("archive-period", ("archive", "period"), "month")
    add("unmanaged-list", ("unmanaged", "paths"), "fixture")
    add("unmanaged-containment", ("unmanaged", "paths"), ["../escape"])
    # Multi-finding preservation and layout-sensitive import diagnostics.
    add("identity-inline", ("opf", "standard"), "other")
    data = copy.deepcopy(valid)
    data["opf"].update(standard="other", layout="per-record")
    rows.append(("identity-per-record", data))
    data = copy.deepcopy(valid)
    data["opf"].update(posture="unknown", import_status="unknown")
    rows.append(("multiple-findings", data))

    rows = [(name, data, None) for name, data in rows]
    rows.append(("profile-control-mapping", copy.deepcopy(valid), []))
    rows.append(("profile-control-entry", copy.deepcopy(valid), {"demo": "1"}))
    profile = {"version": "1.0.0", "base_compat": ">=1.2.0"}

    def prof(name, changes, *, base=None):
        data = copy.deepcopy(valid)
        data["profiles"] = {"demo": dict(profile, **changes)}
        for key, value in changes.items():
            if value is None:
                del data["profiles"]["demo"][key]
        if base:
            data["opf"].update(base)
        rows.append(("profile-" + name, data, {"demo": [1]}))

    prof("major", {"version": "bad"})
    prof("unknown-key", {"unknown": True})
    prof("compat-missing", {"base_compat": None})
    prof("base-unparseable", {}, base={"spec_version": "bad"})
    prof("compat-type", {"base_compat": []})
    prof("compat-empty", {"base_compat": ""})
    prof("compat-clause", {"base_compat": "bad"})
    prof("compat-excludes", {"base_compat": "<0.0.1"})
    prof("posture-enum", {"posture_floor": "unknown"})
    prof("posture-weakens", {"posture_floor": "off"}, base={"posture": "required"})
    prof("modules-list", {"required_modules": "governance"})
    prof("modules-disabled", {"required_modules": ["unknown"]})
    prof("namespace-shape", {"extension_namespace": "bad"})
    prof("namespace-unregistered", {"extension_namespace": "x-fixture"})

    # Enumerate the validator's helper call graph and finding emission sites.
    source = inspect.getsource(_opf_store)
    tree = ast.parse(source)
    functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
    names, pending = set(), ["validate_manifest"]
    while pending:
        name = pending.pop()
        if name in names:
            continue
        names.add(name)
        for node in ast.walk(functions[name]):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                called = node.func.id
                if called in functions:
                    pending.append(called)
    sites = set()
    for name in names:
        for node in ast.walk(functions[name]):
            emission = (
                isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "findings" and node.func.attr == "append")
            rejection = (
                isinstance(node, ast.Return) and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Name)
                and node.value.func.id == "ManifestValidation"
                and isinstance(node.value.args[0], ast.Name)
                and node.value.args[0].id == "CANNOT_EVALUATE")
            if emission or rejection:
                literals = [n.value for n in ast.walk(node)
                            if isinstance(n, ast.Constant) and isinstance(n.value, str)]
                # validate_manifest rejects a non-table supported profile at
                # _profile_major before calling this helper. Its defensive
                # not-table branch cannot emit through the public validator.
                if name == "_validate_supported_profile" and "{} is not a table" in literals:
                    continue
                sites.add(node.lineno)

    seen = set()
    filename = _opf_store.validate_manifest.__code__.co_filename

    def trace(frame, event, arg):
        if event == "line" and frame.f_code.co_filename == filename:
            seen.add(frame.f_lineno)
        return trace

    previous = sys.gettrace()
    results = []
    try:
        sys.settrace(trace)
        for name, data, control in rows:
            validation = _opf_store.validate_manifest(data, control)
            check("F1-generated-" + name + "-finding", bool(validation.findings))
            results.append((name, data, validation, control))
    finally:
        sys.settrace(previous)
    for line in sorted(sites):
        check("F1-validator-finding-site-" + str(line), line in seen)
    return results
