#!/usr/bin/env python3
"""Generated finding fixtures, with a source-derived emission-site census.

Base cases use the production validator unchanged. Profile/control cases enter
doctor with supported_profiles; production base-only intake receives no injected
control. This covers finding classes, not every possible malformed value. Base
rows run against MANIFEST_CALLERS at their public boundaries; profile rows run
through doctor's real profile handling.
"""
import ast
import copy
import inspect
import sys

import _opf_init
import _opf_store


MANIFEST_CALLERS = ("loader", "views", "plan_views", "import",
                    "changelog", "absorb", "doctor")


def _finding_sites(source, check):
    """Closed syntax census over the validator's local, literal call graph.

    Reject accumulator aliases/mutations and unfamiliar result construction.
    Emissions must start on a line containing no other statement or condition;
    a line event alone cannot prove an inline conditional's body executed.
    Dynamic dispatch, imported emitters, reflection and deliberate AST spoofing
    remain outside this static census; changes to those require manual review.
    """
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
    parents = {child: parent for parent in ast.walk(tree)
               for child in ast.iter_child_nodes(parent)}
    unsupported = []
    sites = set()
    # Exempt only this exact early-return defence, in its original position.
    # A reachable append with identical wording is still an emission.
    defence = ast.parse("""
if not isinstance(prof, dict):
    findings.append("{} is not a table".format(where))
    return
""").body[0]
    helper = functions["_validate_supported_profile"]
    guarded = helper.body[2] if len(helper.body) > 2 else None
    exempt = (guarded.body[0].value
              if isinstance(guarded, ast.If)
              and ast.dump(guarded) == ast.dump(defence) else None)
    for name in names:
        for node in ast.walk(functions[name]):
            parent = parents.get(node)
            if isinstance(node, ast.Name) and node.id == "findings":
                # Only a fresh empty accumulator, append, checked helper argument,
                # status test, and the final constructor may consume this name.
                allowed = (
                    isinstance(parent, ast.Assign) and parent.targets == [node]
                    and isinstance(parent.value, ast.List) and not parent.value.elts)
                if isinstance(parent, ast.Attribute):
                    call = parents.get(parent)
                    allowed = (parent.attr == "append" and isinstance(call, ast.Call)
                               and call.func is parent and len(call.args) == 1
                               and not call.keywords
                               and isinstance(parents.get(call), ast.Expr))
                elif isinstance(parent, ast.Call) and isinstance(parent.func, ast.Name):
                    callee = functions.get(parent.func.id)
                    if callee is not None and node in parent.args:
                        i = parent.args.index(node)
                        params = callee.args.posonlyargs + callee.args.args
                        allowed = i < len(params) and params[i].arg == "findings"
                    elif parent.func.id == "ManifestValidation":
                        allowed = ast.unparse(parent) == (
                            "ManifestValidation(status, findings, unevaluated, base, parsed_profiles)")
                elif isinstance(parent, ast.IfExp):
                    allowed = ast.unparse(parent) == "INVALID if findings else VALID"
                if not allowed:
                    unsupported.append((name, node.lineno, "findings use"))
            if isinstance(node, ast.Name) and node.id == "ManifestValidation":
                allowed = (
                    isinstance(parent, ast.Call) and parent.func is node
                    and isinstance(parents.get(parent), ast.Return)
                    and not parent.keywords and (
                        ast.unparse(parent) ==
                        "ManifestValidation(status, findings, unevaluated, base, parsed_profiles)"
                        or (len(parent.args) == 2
                            and isinstance(parent.args[0], ast.Name)
                            and parent.args[0].id == "CANNOT_EVALUATE"
                            and isinstance(parent.args[1], ast.List)
                            and len(parent.args[1].elts) == 1)))
                if not allowed:
                    unsupported.append((name, node.lineno, "validation construction"))
            emission = (
                isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "findings" and node.func.attr == "append")
            rejection = (
                isinstance(node, ast.Return) and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Name)
                and node.value.func.id == "ManifestValidation"
                and bool(node.value.args)
                and isinstance(node.value.args[0], ast.Name)
                and node.value.args[0].id == "CANNOT_EVALUATE")
            if emission or rejection:
                # _profile_major rejects non-tables before this helper is called.
                if node is exempt:
                    continue
                sites.add((node.lineno, node.col_offset, node.end_lineno, node.end_col_offset))

    # Coverage below uses Python line events. Overlapping line spans (including
    # an append after a multiline call's closing parenthesis) are ambiguous.
    lines = [line for start, _, end, _ in sites for line in range(start, end + 1)]
    check("F2h-census-unambiguous-lines", len(set(lines)) == len(lines))

    # A simple suite (if/while/for/with/try/match case on one line) can
    # emit a line event without executing its append/return. Requiring the
    # emission to start the physical line also rejects semicolon predecessors.
    source_lines = source.splitlines()
    check("F2j-census-emission-only-lines", all(
        not source_lines[start - 1][:column].strip()
        for start, column, _, _ in sites))

    check("F2g-census-recognized-emissions", not unsupported)
    # HEAD 3b5ea91: 67 append sites - 1 unreachable profile-table defence
    # + 5 CANNOT_EVALUATE returns = 71 reachable emission sites.
    check("F2g-census-site-count-71", len(sites) == 71)
    return sites


def _census_regressions(source, check):
    # Keep the count unchanged while adding an unrecognized emission style.
    extended = source.replace("    findings = []", "    findings = []; findings.extend([])", 1)
    failures = []
    _finding_sites(extended, lambda name, ok: failures.append(name) if not ok else None)
    check("F2g-census-rejects-extend", failures == ["F2g-census-recognized-emissions"])

    # Remove a genuine emission without changing the supported syntax.
    tree = ast.parse(source)
    function = next(n for n in tree.body
                    if isinstance(n, ast.FunctionDef) and n.name == "_validate_top_level")
    emission = function.body[1].body[0]
    assert isinstance(emission, ast.Expr) and isinstance(emission.value, ast.Call)
    function.body[1].body[0] = ast.Pass()
    failures = []
    _finding_sites(ast.unparse(tree), lambda name, ok: failures.append(name) if not ok else None)
    check("F2g-census-rejects-shrink", failures == ["F2g-census-site-count-71"])

    tree = ast.parse(source)
    helper = next(n for n in tree.body
                  if isinstance(n, ast.FunctionDef) and n.name == "_validate_supported_profile")
    # Reachable for a supported profile missing base_compat; same wording as the
    # unreachable defence must not exempt this different guarded structure.
    helper.body.insert(3, ast.parse("""
if not isinstance(prof.get("base_compat"), str):
    findings.append("{} is not a table".format(where))
""").body[0])
    failures = []
    _finding_sites(ast.unparse(tree), lambda name, ok: failures.append(name) if not ok else None)
    check("F2h-census-rejects-reachable-same-wording",
          "F2g-census-site-count-71" in failures)

    duplicate = source.replace(
        'findings.append("[profiles] is not a table")',
        'findings.append("[profiles] is not a table"); findings.append("new emission")', 1)
    check("F2h-census-same-line-mutant-installed", duplicate != source)
    failures = []
    _finding_sites(duplicate, lambda name, ok: failures.append(name) if not ok else None)
    check("F2h-census-rejects-same-line",
          "F2g-census-site-count-71" in failures
          and "F2h-census-unambiguous-lines" in failures)


    # Only the condition executes for existing generated rows; the append is
    # reachable at >=9.9.9. A line hit must not certify that append's execution.
    emission = ('findings.append("{}.base_compat {!r} does not admit the base '
                'spec_version".format(where, compat))')
    conditional = source.replace(emission, 'if compat == ">=9.9.9": ' + emission, 1)
    check("F2j-census-conditional-mutant-installed", conditional != source)
    failures = []
    _finding_sites(conditional, lambda name, ok: failures.append(name) if not ok else None)
    check("F2j-census-rejects-conditional-only-line",
          failures == ["F2j-census-emission-only-lines"])


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

    source = inspect.getsource(_opf_store)
    sites = _finding_sites(source, check)
    _census_regressions(source, check)

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
    for span in sorted(sites):
        check("F1-validator-finding-site-" + str(span), span[0] in seen)
    return results
