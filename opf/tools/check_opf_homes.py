#!/usr/bin/env python3
"""Homes contract, control-boundary and gitignore drift gate (writers still use legacy homes).

Checks documented topology against the constructor authority. Text checks protect the planned
contract, not runtime conformance to homes 2. No store is mutated or required to install a block.
"""
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _opf_store as store  # noqa: E402

SPEC = Path(__file__).resolve().parents[1] / "spec" / "OPF-SPEC.md"
# Scope each wording check to its operative section, never a whole-document negative scan.
_CONTRACT = {
    "4.2": ('spec_version = "2.0.0"', "[opf].homes = 2", "until homes 2 is activated", "git common directory",
            "machine-local", "git add -f", "tracked staging or journals", "layout-", "preview-",
            "keeps its legacy grading", "inventory-<phase>.toml", "an evidence commit changes only its bundle folder",
            "claimed by exactly one row", "every payload file", "is itself schema-checked",
            "A phase inventory never substitutes for a missing inventory.toml",
            "In homes 2, ordinary transaction operands", "keep their legacy operand handling",
            "check roster and residuals are unchanged", "byte-exact",
            "fails closed on a reserved-name match or ambiguity"),
    "4.4": ("MUST NOT be used as a machine subdirectory", "legacy content cannot be re-absorbed"),
    "9.2": ('spec_version = "2.0.0"', "unknown future generations are refused",
            "idempotent and journaled", "Every destination is digest-verified before its source is removed",
            "clean over exactly the paths", "full doctor VALID", "standing finding", "multi-root coordinator"),
    "12": ("Neither is scanned as the other", "retained indefinitely", "independent re-read",
           "age alone never authorizes deletion"),
    "14.1": (".working/staging/import/<run-id>/", ".working/staging/ingest/<run-id>/",
             ".working/imported/import/<run-id>/", "review remove nothing",
             "Destination durability and digest verification precede source removal",
             "same recoverable transaction", "Acceptance binds the removal action",
             "acceptance never authorizes removal", "plan-time cannot-evaluate",
             "Reclamation is journaled and idempotent", "Read-only commands never clean staging"),
    "14.2": (".working/staging/import/<run-id>/", ".working/archive/moved/<source-path>",
             "collision finding, never an overwrite", "outside the managed store",
             "only beneath", "multi-root coordinator", "no adoption option selects them",
             "declaration may equal, contain, or lie within them", ".working/archive/adoption/<run-id>/",
             ".working/imported/adoption/<run-id>/", ".working/journals/adoption/"),
    "15": ("adoption provenance", "OPF operates without it", "Only homes migration",
           "explicitly inventoried", "without touching unrelated AIQT material"),
}


def _sections(text):
    headings = list(re.finditer(r"^#{2,3} ([0-9]+(?:\.[0-9]+)?)\.? .*?$", text, re.MULTILINE))
    return {m[1]: text[m.end():headings[i + 1].start() if i + 1 < len(headings) else len(text)]
            for i, m in enumerate(headings)}


def contract_findings(text):
    sections = _sections(text)
    findings = []
    for section, fragments in _CONTRACT.items():
        body = " ".join(sections.get(section, "").replace("`", "").split())
        for fragment in fragments:
            if fragment not in body:
                findings.append("spec {} missing contract: {}".format(section, fragment))
    layout = sections.get("4.2", "")
    reservation = sections.get("4.4", "")
    for name in store.RESERVED_MACHINE_SUBDIRS:
        if "`{}`".format(name) not in reservation:
            findings.append("spec 4.4 missing reserved name: " + name)
    for name in store.STORE_TREE_CONTROL_DIRS:
        if name + "/" not in layout:
            findings.append("spec 4.2 missing control home: " + name)
    for kind in store.STAGING_KINDS:
        if "`{}`".format(kind) not in layout:
            findings.append("spec 4.2 missing kind: " + kind)
    blocks = re.findall(r"^```gitignore\n(.*?)^```$", layout, re.MULTILINE | re.DOTALL)
    if len(blocks) != 1 or blocks[0] != store.render_homes_gitignore():
        findings.append("spec 4.2 managed gitignore block differs from renderer")
    return findings



def boundary_self_test():
    """Exercise the read-only boundaries with explicit in-memory filesystem observations."""
    import contextlib
    import copy
    import hashlib
    from types import SimpleNamespace
    from unittest.mock import patch
    import _journal as journal
    import _opf_adopt as adopt
    import _opf_check as doctor
    import _opf_import as importer
    import _opf_ingest as ingest
    import _opf_journal as home_journal
    import _opf_views as views

    failures = []
    checked = []

    def check(name, thunk):
        checked.append(name)
        try:
            if not thunk():
                failures.append(name)
        except Exception as exc:
            failures.append("{} ({})".format(name, exc))

    def refuses(thunk):
        try:
            thunk()
        except (ValueError, journal.JournalError, ingest._DetectError, views.ViewsError):
            return True
        return False

    def refusal(thunk):
        # The refusal message, or None when the thunk completed; any other exception propagates.
        try:
            thunk()
        except (ValueError, journal.JournalError, ingest._DetectError, views.ViewsError) as exc:
            return str(exc)
        return None

    class _Reached(Exception):
        """A mocked first I/O was reached: nothing refused the operation before it."""

    run = "imp-20260917T120000Z-0123456789abcdef"
    adopt_run = "adopt-20260917T120000Z-0123456789abcdef"
    machine = ".working/toml"
    manifest = {"opf": {"layout": "inline"}, "types": {}, "views": {}}
    manifest2 = {"opf": {"layout": "inline", "homes": 2, "spec_version": "2.0.0"}, "types": {}, "views": {}}
    homes = tuple(".working/" + name for name in ("imports", "imported", "archive", "staging", "journals"))
    legacy_root = homes[0]

    def active():
        # Homes 2 is activated only inside the fixture; shipped tooling grades every store as legacy.
        return patch.object(store, "SUPPORTED_HOMES", 2)

    # Discovery precedes the manifest: a candidate under journals/ still fails closed as ambiguous.
    with patch.object(store, "_immediate_subdirs", return_value=["journals", "toml"]), \
            patch.object(store, "_read_toml_contained", return_value={"opf": {"standard": store.STANDARD_TOKEN}}):
        check("discovery-journals-candidate-ambiguous",
              lambda: store.discover_machine_store(-1, "/store")[0] == "multiple")
    cls = doctor.classify_containment(manifest, machine)
    check("classifier-legacy-roster", lambda: cls.homes == 1 and cls.control_roots == (legacy_root,)
          and cls.evidence_roots == ())
    check("classifier-declaration-inert", lambda: doctor.classify_containment(manifest2, machine).homes == 1)
    # Latent activation is gated: raising SUPPORTED_HOMES activates only a store declaring both homes 2
    # and spec_version 2.0.0, never a current-version store or a non-integer declaration.
    with active():
        check("activation-homes2-declared", lambda: store.homes_generation(manifest2) == 2)
        for label, opf in (("current-version", {"homes": 2, "spec_version": "1.1.0"}),
                           ("no-version", {"homes": 2}), ("boolean", {"homes": True, "spec_version": "2.0.0"}),
                           ("string", {"homes": "2", "spec_version": "2.0.0"})):
            check("activation-gated-" + label, lambda o=opf: store.homes_generation({"opf": o}) == 1)
    with active():
        check("classifier-activated-legacy", lambda: doctor.classify_containment(manifest, machine).homes == 1)
        cls2 = doctor.classify_containment(manifest2, machine)
        check("classifier-roster", lambda: cls2.homes == 2 and cls2.control_roots == homes)
        check("evidence-roots-disjoint", lambda: cls2.evidence_roots ==
              (".working/imported", ".working/archive") and cls2.archive_root not in cls2.evidence_roots)
        for home in homes:
            for operand in (home, home + "/child", ".working", "./" + home + "/"):
                altered = dict(manifest2, unmanaged={"paths": [operand]})
                check("unmanaged-collision-" + operand,
                      lambda m=altered: bool(doctor.classify_containment(m, machine).colliding)
                      and not doctor.classify_containment(m, machine).valid_unmanaged)
    for home in homes[1:]:
        for operand in (home, home + "/child"):
            check("unmanaged-legacy-valid-" + operand, lambda o=operand: doctor.classify_containment(
                dict(manifest, unmanaged={"paths": [o]}), machine).valid_unmanaged == [o])
    check("unmanaged-legacy-imports-collision", lambda: bool(doctor.classify_containment(
        dict(manifest, unmanaged={"paths": [legacy_root]}), machine).colliding))
    ledger = machine + "/evidence.toml"
    check("unmanaged-legacy-evidence-ledger", lambda: doctor.classify_containment(
        dict(manifest, unmanaged={"paths": [ledger]}), machine).valid_unmanaged == [ledger])
    resolution = SimpleNamespace(machine_rel=machine, store_root=Path("/store"), product_root=Path("/store"))
    check("detect-legacy-set-equality",
          lambda: ingest._managed_paths(resolution, manifest)[0] == {machine, legacy_root})
    with active():
        check("detect-checker-set-equality",
              lambda: ingest._managed_paths(resolution, manifest2)[0] == {machine} | set(homes))
    import stat

    def detect_open(name, *_args, **_kwargs):
        if name != ".working":
            raise AssertionError("detect entered control home: " + name)
        return 99

    for mode in (stat.S_IFDIR, stat.S_IFREG):
        with active(), patch.object(ingest.os, "open", side_effect=detect_open), \
                patch.object(ingest.os, "close"), \
                patch.object(ingest.os, "listdir", return_value=[h.split("/")[-1] for h in homes]), \
                patch.object(ingest.os, "stat", return_value=SimpleNamespace(st_mode=mode)), \
                patch.object(ingest, "_digest_of", side_effect=AssertionError("read control bytes")):
            check("detect-never-reads-controls-" + str(mode), lambda: ingest._detect_store_scope(
                -1, ingest._managed_paths(resolution, manifest2)[0], set(), set(), homes=2) == [])
    with patch.object(ingest.os, "open", side_effect=detect_open), patch.object(ingest.os, "close"), \
            patch.object(ingest.os, "listdir", return_value=[h.split("/")[-1] for h in homes]), \
            patch.object(ingest.os, "stat", return_value=SimpleNamespace(st_mode=stat.S_IFREG)), \
            patch.object(ingest, "_digest_of", return_value=("sha256:" + "0" * 64, 0)):
        # The legacy (homes 1) walk prunes directories only, so a regular FILE named like a home is a row.
        check("detect-legacy-control-file-rows", lambda: len(ingest._detect_store_scope(
            -1, ingest._managed_paths(resolution, manifest)[0], set(), set())) == len(homes))
    for home in homes:
        check("frozen-row-refusal-" + home,
              lambda h=home: refuses(lambda: ingest.admit_row_scope("store", h, "", homes=2)))
    check("frozen-row-legacy-imports", lambda: refusal(lambda: ingest.admit_row_scope("store", legacy_root, ""))
          == "store-scope row {0!r} lies in the reserved imports tree {0!r}, which detection prunes "
          "wholesale".format(legacy_root))
    for home in homes[1:]:
        check("frozen-row-legacy-" + home, lambda h=home: ingest.admit_row_scope("store", h + "/file", "") is None)
    # The manifest-free review gate accepts a supplied generation only as the integer 1 or 2. Any other
    # value (a bool, a float, NaN) cannot evaluate, whatever the tooling supports and whatever the row.
    import check_opf_import as gate
    journal_row = dict(scope="store", source_path=".working/journals/x")
    imports_row = dict(scope="store", source_path=legacy_root + "/x")

    def gated(row, generation):
        return gate._row_scope_error(ingest, [row], None, generation)

    for supported in (1, 2):
        with patch.object(store, "SUPPORTED_HOMES", supported):
            for bad in (0, False, True, 1.5, float("nan"), "2", 3):
                for label, gated_row in (("journals", journal_row), ("imports", imports_row)):
                    check("gate-generation-cannot-{}-{}-{!r}".format(supported, label, bad),
                          lambda r=gated_row, b=bad: gated(r, b).startswith("cannot evaluate"))
    with active():
        check("gate-generation-homes2-journal-refused",
              lambda: "reserved store control area" in gated(journal_row, 2))
        check("gate-generation-legacy-journal-admitted", lambda: gated(journal_row, 1) == "")
        check("gate-generation-legacy-imports-refused", lambda: "reserved imports tree" in gated(imports_row, 1))
    check("move-outside-preserved", lambda: ingest.admit_move_boundary("outside/file", "ops/.working") is None)
    check("move-store-refused", lambda: refuses(lambda: ingest.admit_move_boundary("ops/.working/x", "ops/.working")))

    files = {}
    directories = {".working"}
    listed = []
    unreadable = set()
    vanished = set()
    inventories = {}

    def listing(_fd, rel):
        listed.append(rel)
        if rel == ".working/journals" or rel.startswith(".working/journals/"):
            raise AssertionError("doctor read journals")
        if rel in unreadable or rel in files:
            raise store.StoreError("unreadable or wrong-type input: " + rel)
        if rel not in directories or rel in vanished:
            return None, None
        prefix = rel + "/"
        dirs = sorted(d[len(prefix):] for d in directories if d.startswith(prefix) and "/" not in d[len(prefix):])
        leaves = sorted(d[len(prefix):] for d in files if d.startswith(prefix) and "/" not in d[len(prefix):])
        return dirs, leaves

    def add(path, raw=b"x"):
        files[path] = raw
        parts = path.split("/")
        directories.update("/".join(parts[:n]) for n in range(1, len(parts)))

    def reset():
        files.clear()
        directories.clear()
        directories.add(".working")
        inventories.clear()
        unreadable.clear()
        vanished.clear()

    def read_toml(_fd, rel, rep):
        if rel.startswith(".working/journals/") or not store.is_evidence_inventory_name(rel.rsplit("/", 1)[-1]):
            raise AssertionError("unexpected inventory authority: " + rel)
        if rel in unreadable:
            rep.cant("unreadable " + rel)
            return None, "error"
        return (copy.deepcopy(inventories[rel]), "ok") if rel in inventories else (None, "absent")

    def read_bytes(_fd, rel, rep):
        if rel in unreadable:
            rep.cant("unreadable " + rel)
            return None, "error"
        return (files[rel], "ok") if rel in files else (None, "absent")

    def containment(partial=False, model=None):
        report = doctor._Report()
        report.ran("C-CONTAINMENT")
        doctor._check_containment(0, machine, model or manifest, "partial" if partial else "complete", report)
        return report

    def evidence(generation=2):
        report = doctor._Report()
        report.ran("C-EVIDENCE-ENUM")
        doctor._check_evidence(0, generation, report)
        return report

    def doc(*rows):
        return {"format": "opf.evidence.inventory/v1", "file": list(rows)}

    def row(path, raw=b"retained"):
        return {"path": path, "size": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}

    def graded(report, path):
        return any(repr(path) in message for message in report.findings)

    with patch.object(doctor, "_list_contained", listing), patch.object(doctor, "_read_toml", read_toml), \
            patch.object(doctor, "_read_bytes", read_bytes):
        stage = ".working/staging/import/" + run + "/plan.toml"
        add(stage)
        add(".working/journals/import/journal/inflight/frames.log", b"torn")
        # A legacy store keeps its legacy grading: each homes-2 name is one unregistered path.
        check("legacy-staging-graded", lambda: graded(containment(), ".working/staging"))
        check("legacy-journals-graded", lambda: graded(containment(), ".working/journals"))
        check("legacy-staging-not-partial", lambda: not containment(True).triage
              and any("no active" in message for message in containment(True).findings))
        with active():
            check("staging-steady-finding", lambda: any(stage in s for s in containment(model=manifest2).findings))
            check("staging-substantiated-triage", lambda: bool(containment(True, manifest2).triage)
                  and not containment(True, manifest2).findings)
            del files[stage]
            check("empty-run-grades", lambda: any(run in s for s in containment(model=manifest2).findings))
            check("empty-run-not-partial", lambda: bool(containment(True, manifest2).findings)
                  and not containment(True, manifest2).triage)
            add(stage)
            add(".working/staging/unknown/run/file")
            check("unknown-kind-always-finding", lambda: any("invalid staging kind" in s
                                                           for s in containment(True, manifest2).findings))
            reset()
            directories.add(".working/journals")
            del listed[:]
            check("journals-pruned", lambda: not containment(model=manifest2).findings
                  and not containment(model=manifest2).cannot)
            check("journals-never-listed", lambda: ".working/journals" not in listed)
            check("evidence-absent-clean", lambda: not evidence().findings and not evidence().cannot)
            bundle = ".working/imported/import/" + run
            adoption = ".working/imported/adoption/" + adopt_run
            source = bundle + "/sources/notes.txt"
            moved = ".working/archive/moved/old/notes.txt"
            receipt = adoption + "/receipt.toml"
            preimage = ".working/archive/adoption/" + adopt_run + "/AGENTS.md"
            members = (source, moved, receipt, preimage)
            for path in members:
                add(path, b"retained")
            for inventory, rows in ((bundle + "/inventory.toml", (row(source), row(moved))),
                                    (adoption + "/inventory.toml", (row(receipt), row(preimage)))):
                add(inventory, b"")
                inventories[inventory] = doc(*rows)
            check("evidence-members-match", lambda: not evidence().findings and not evidence().cannot)
            check("evidence-not-containment-graded", lambda: not containment(model=manifest2).findings)
        # Nothing reads a homes-1 store's evidence homes; its containment walk grades them as before.
        def legacy_reads_nothing():
            count = len(listed)
            with patch.object(doctor, "_read_toml", side_effect=AssertionError("legacy inventory read")), \
                    patch.object(doctor, "_read_bytes", side_effect=AssertionError("legacy evidence read")):
                legacy = evidence(1)
            return not legacy.findings and not legacy.cannot and len(listed) == count

        check("evidence-legacy-reads-nothing", legacy_reads_nothing)
        check("evidence-legacy-contained", lambda: graded(containment(), ".working/imported")
              and graded(containment(), ".working/archive"))
        with active():
            for path in members:
                files[path + ".stray"] = b"rogue"
                check("evidence-off-inventory-" + path, lambda p=path: graded(evidence(), p + ".stray"))
                del files[path + ".stray"]
                del files[path]
                check("evidence-missing-" + path, lambda p=path: graded(evidence(), p))
                files[path] = b"tampered"
                check("evidence-digest-" + path, lambda: any("mismatch" in s for s in evidence().findings))
                files[path] = b"retained"
                unreadable.add(path)
                check("evidence-unreadable-" + path, lambda: bool(evidence().cannot))
                unreadable.clear()
            for extra in (".working/archive/empty-stray", bundle + "/empty", ".working/imported/other",
                          ".working/imported/import/not-a-run", ".working/archive/adoption/" + run):
                directories.add(extra)
                check("evidence-stray-directory-" + extra, lambda: bool(evidence().findings)
                      and not evidence().cannot)
                directories.remove(extra)
            orphan = ".working/imported/import/" + run.replace("0123", "4567")
            add(orphan + "/file")
            check("evidence-bundle-without-inventory", lambda: any("has no inventory" in s
                                                                 for s in evidence().findings))
            del files[orphan + "/file"]
            directories.discard(orphan)
            # A bundle its parent listed but that is gone when it is listed itself is a race, not an
            # empty bundle: its claims are unknown, so the walk cannot evaluate rather than pass.
            raced = ".working/imported/import/" + run.replace("0123", "89ab")
            directories.add(raced)
            vanished.add(raced)
            check("evidence-bundle-vanished-cannot", lambda: any(
                "vanished" in s for s in evidence().cannot) and not evidence().findings)
            vanished.clear()
            directories.discard(raced)
            probe = adoption + "/probe.toml"
            add(probe, b"retained")
            check("evidence-unclaimed-phase-member", lambda: graded(evidence(), probe))
            add(adoption + "/inventory-evidence.toml", b"")
            inventories[adoption + "/inventory-evidence.toml"] = doc(row(probe))
            check("evidence-phase-inventory", lambda: not evidence().findings and not evidence().cannot)
            # A phase inventory never substitutes for a missing inventory.toml: the founding claims are
            # unknown, so the bundle cannot evaluate, even when the phase inventory lists every member.
            first = adoption + "/inventory.toml"
            founding = inventories.pop(first)
            del files[first]
            inventories[adoption + "/inventory-evidence.toml"] = doc(row(probe), *founding["file"])
            check("evidence-phase-without-first-cannot", lambda: any(
                "no inventory.toml" in s for s in evidence().cannot) and not evidence().findings)
            add(first, b"")
            inventories[first] = founding
            inventories[adoption + "/inventory-evidence.toml"] = doc(row(probe))
            check("evidence-phase-restored", lambda: not evidence().findings and not evidence().cannot)
            inventory = bundle + "/inventory.toml"
            good = copy.deepcopy(inventories)
            # A claim on another bundle's member or another run's preimage is refused on its own, so the
            # rightful owner's inventory drops that row here rather than masking it as a duplicate claim.
            # An import bundle claiming a preimage path under its OWN run id is refused by its kind alone.
            sole = {"cross-bundle": doc(row(preimage)), "foreign-preimage": doc(row(receipt))}
            own_preimage = ".working/archive/adoption/" + run + "/file"
            # Every malformed or unreadable inventory is cannot-evaluate and grades nothing partially.
            for label, broken in (
                    ("format", dict(doc(), format="unsupported")),
                    ("extra-key", dict(doc(row(source), row(moved)), note="x")),
                    ("no-file-array", {"format": "opf.evidence.inventory/v1"}),
                    ("row-not-table", doc("x")),
                    ("row-extra-key", doc(dict(row(source), note="x"), row(moved))),
                    ("foreign-kind", doc(dict(row(source), path=".working/imported/other/" + run + "/x"))),
                    ("wrong-prefix", doc(dict(row(source), path=source[len(".working/"):]), row(moved))),
                    ("cross-bundle", doc(row(source), row(moved), row(receipt))),
                    ("foreign-preimage", doc(row(source), row(moved), row(preimage))),
                    ("import-own-run-preimage", doc(row(source), row(moved), row(own_preimage))),
                    ("lists-inventory", doc(row(source), row(moved), row(inventory))),
                    ("boolean-size", doc(dict(row(source), size=True), row(moved))),
                    ("negative-size", doc(dict(row(source), size=-1), row(moved))),
                    ("uppercase-digest", doc(dict(row(source), sha256=row(source)["sha256"].upper()), row(moved))),
                    ("duplicate", doc(row(source), row(source), row(moved)))):
                inventories[inventory] = broken
                inventories[adoption + "/inventory.toml"] = sole.get(label, good[adoption + "/inventory.toml"])
                check("evidence-malformed-" + label, lambda: bool(evidence().cannot) and not evidence().findings)
            inventories.update(copy.deepcopy(good))
            inventories[adoption + "/inventory.toml"] = doc(row(receipt), row(preimage), row(moved))
            check("evidence-cross-duplicate-refused", lambda: bool(evidence().cannot))
            inventories.update(copy.deepcopy(good))
            # An unreadable or vanished inventory leaves its bundle's claims unknown: cannot-evaluate
            # only, never a partial reconciliation grading the rest of the homes without those claims.
            unreadable.add(inventory)
            check("evidence-unreadable-inventory", lambda: bool(evidence().cannot) and not evidence().findings)
            unreadable.clear()
            del inventories[inventory]
            check("evidence-vanished-inventory", lambda: bool(evidence().cannot) and not evidence().findings)
            inventories.update(copy.deepcopy(good))
            unreadable.add(".working/imported")
            check("evidence-unreadable-root", lambda: bool(evidence().cannot))
            unreadable.clear()

    import _opf_init as init
    full_manifest = init._manifest_model()
    full_manifest["views"] = {}
    full_manifest2 = copy.deepcopy(full_manifest)
    full_manifest2["opf"]["homes"] = 2
    full_manifest2["opf"]["spec_version"] = "2.0.0"
    reset()
    add(".working/imported/import/" + run + "/sources/notes.txt")
    # A journal on disk: legacy grades it as an ordinary path, homes 2 must skip it unread.
    add(".working/journals/import/journal/" + run + "/frames.log", b"torn")

    def dispatch(model):
        def doctor_toml(_fd, rel, _rep):
            if rel == machine + "/manifest.toml":
                return copy.deepcopy(model), "ok"
            if rel.startswith(".working/journals/"):
                raise AssertionError("doctor read a journal record")
            return None, "absent"

        del listed[:]
        with patch.object(doctor, "_list_contained", listing), patch.object(doctor, "_read_toml", doctor_toml), \
                patch.object(doctor, "_read_bytes", read_bytes), \
                patch.object(importer, "_sibling_ids", return_value=[]):
            report = doctor._Report()
            doctor._validate_opened_store(0, None, machine, None, {}, None, "default", True, report)
        return report.result(), list(listed)

    def contained(result, path):
        return any(repr(path) in message for message in result.by_check.get("C-CONTAINMENT", []))

    # A legacy (homes 1) report keeps the legacy roster, order and count and the pinned legacy residual
    # text, with the homes-2 names graded as ordinary paths and no evidence read.
    legacy_report, legacy_listed = dispatch(full_manifest)
    check("doctor-legacy-roster-exact", lambda: tuple(legacy_report.checks) == doctor.REQUIRED_CHECKS
          and "C-EVIDENCE-ENUM" not in doctor.REQUIRED_CHECKS and len(legacy_report.checks) == 29)
    # The pin is independent of the live _RESIDUALS constant: sha256 over the 12 legacy residuals joined by
    # a newline, UTF-8. Any added, removed, reordered or reworded legacy residual changes it.
    legacy_residuals_sha256 = "6901f8734d2293e714c521e9b74836a188627839d7d8a08a3888b4a9cc7a8a0b"
    check("doctor-legacy-residuals-unchanged", lambda: len(legacy_report.residuals) == 12
          and hashlib.sha256("\n".join(legacy_report.residuals).encode("utf-8")).hexdigest()
          == legacy_residuals_sha256
          and not any(r in legacy_report.residuals for r in doctor._HOMES2_RESIDUALS))
    check("doctor-legacy-evidence-inert", lambda: not any(
        path.startswith(".working/imported/") for path in legacy_listed))
    check("doctor-legacy-contains-homes", lambda: contained(legacy_report, ".working/imported")
          and contained(legacy_report, ".working/journals"))
    with active():
        report, homes2_listed = dispatch(full_manifest2)
    check("doctor-homes2-roster", lambda: tuple(report.checks) == doctor.required_checks(2)
          and len(report.checks) == 30)
    check("doctor-dispatches-evidence", lambda: report.checks.get("C-EVIDENCE-ENUM") == "FINDING")
    check("doctor-homes2-residual", lambda: all(r in report.residuals for r in doctor._HOMES2_RESIDUALS))
    check("doctor-skips-journals", lambda: not contained(report, ".working/journals")
          and not any(p == ".working/journals" or p.startswith(".working/journals/") for p in homes2_listed))
    # Activated tooling alone widens nothing: a legacy manifest keeps the legacy roster and residuals.
    with active():
        activated_legacy, _ = dispatch(full_manifest)
    check("doctor-activated-legacy-roster", lambda: tuple(activated_legacy.checks) == doctor.REQUIRED_CHECKS
          and len(activated_legacy.checks) == 29 and activated_legacy.residuals == legacy_report.residuals)
    # The render source gate follows the report's roster: a homes-2 evidence finding refuses it, and a
    # legacy report is gated on exactly SOURCE_INTEGRITY_CHECKS.
    flagged = dict.fromkeys(doctor.required_checks(2), "PASS")
    flagged["C-EVIDENCE-ENUM"] = "FINDING"
    check("source-gate-homes2-evidence", lambda: not doctor.source_integrity_ok(
        doctor.StoreValidation(doctor.INVALID, checks=flagged)))
    check("source-gate-legacy-roster", lambda: set(doctor.source_checks(doctor.StoreValidation(
        doctor.VALID, checks=dict.fromkeys(doctor.REQUIRED_CHECKS, "PASS")))) == doctor.SOURCE_INTEGRITY_CHECKS)

    def roster(generation, ran):
        rep = doctor._Report()
        rep.require_homes(generation)
        for cid in ran:
            rep.ran(cid)
        return rep.result()

    check("roster-homes2-skipped-evidence", lambda: roster(2, doctor.REQUIRED_CHECKS).checks.get(
        "C-EVIDENCE-ENUM") == "CANNOT-EVALUATE")
    check("roster-legacy-evidence-unknown", lambda: any("C-EVIDENCE-ENUM" in m for m in roster(
        1, doctor.required_checks(2)).unattributed))

    # Homes 1 keeps the legacy operand handling: the legacy engine applies no journal-operand refusal,
    # so each legacy entry point reaches its first I/O whatever the operand.
    targets = (".working", ".working/journals", ".working/journals/import/journal/frame")

    def reached(thunk):
        try:
            thunk()
        except _Reached:
            return True
        return False

    for kind in journal.OP_KINDS:
        for target in targets:
            ops = [dict(op=kind, path=target)]
            with patch.object(journal, "_open_parent", side_effect=_Reached):
                check("legacy-apply-{}-{}".format(kind, target), lambda o=ops: reached(
                    lambda: journal.apply_ops(-1, o, lambda _name: b"")))
            with patch.object(journal, "_open_txn_beneath", side_effect=_Reached):
                check("legacy-preimage-{}-{}".format(kind, target), lambda o=ops: reached(
                    lambda: journal.capture_preimages(-1, Path("/unused"), -1, o)))
            with patch.object(journal.os, "mkdir", side_effect=_Reached):
                check("legacy-transaction-{}-{}".format(kind, target), lambda o=ops: reached(
                    lambda: journal.run_transaction(-1, -1, "/unused", run, dict(), o, lambda _name: b"",
                                                    "test")))

    # Homes 2: the capability-bound API refuses every kind before it opens anything.
    with patch.object(home_journal, "_opened", side_effect=AssertionError("opened the store journal")):
        for kind in journal.OP_KINDS:
            for target in targets:
                ops = [dict(op=kind, path=target)]
                check("homes2-transaction-{}-{}".format(kind, target), lambda o=ops: "journal home" in (
                    refusal(lambda: home_journal.run_transaction(object(), "import", run, o,
                                                                 lambda _name: b"")) or ""))
    check("ordinary-sibling-allowed", lambda: home_journal._check_ordinary_ops(
        [dict(op="create", path=".working/journals-old/file")]) is None)

    def legacy_recovery(frame_types):
        truncated = []
        intent = dict(txn=run, ops=[dict(op="remove", path=".working/journals")])
        frames = [(t, intent if t == journal.F_INTENT else dict(txn=run)) for t in frame_types]
        with patch.object(journal, "read_frames", return_value=(frames, True, 1)), \
                patch.object(journal, "_truncate_log", side_effect=lambda *_args: truncated.append(True)), \
                patch.object(journal, "_poststate_verifies", return_value=True), \
                patch.object(journal, "_restore_preimage"), patch.object(journal, "publish"):
            return journal.recover(-1, run, -1), len(truncated)

    # Legacy (homes 1) recovery truncates the torn tail and acts on an open transaction's operands.
    check("legacy-recovery-open-rolls-forward", lambda: legacy_recovery([journal.F_INTENT]) == ("rolled-forward", 1))
    check("legacy-recovery-rollback-open-rolls-back",
          lambda: legacy_recovery([journal.F_INTENT, journal.F_RIP]) == ("rolled-back", 1))
    check("legacy-recovery-terminal-truncated",
          lambda: legacy_recovery([journal.F_INTENT, journal.F_COMPLETE]) == ("terminal", 1))

    @contextlib.contextmanager
    def opened(*_args, **_kwargs):
        yield -1, -1, Path("/unused") / run

    def homes2_recovery(frame_types):
        intent = dict(txn=run, ops=[dict(op="remove", path=".working/journals")])
        frames = [(t, intent if t == journal.F_INTENT else dict(txn=run)) for t in frame_types]
        with patch.object(home_journal, "_opened", opened), \
                patch.object(home_journal, "_existing_frames", return_value=frames), \
                patch.object(home_journal, "_project"), \
                patch.object(journal, "recover", return_value="terminal") as recovered:
            outcome = refusal(lambda: home_journal.recover_transaction(object(), "import", run))
            if outcome is not None:
                outcome = "refused" if "journal home" in outcome else outcome
            return outcome or "recovered", recovered.call_count

    # Only an open homes-2 transaction is acted on, so it is refused before recover can truncate or
    # mutate anything. A terminal journal is inert history and reaches recover unchanged.
    check("homes2-recovery-open-refused", lambda: homes2_recovery([journal.F_INTENT]) == ("refused", 0))
    check("homes2-recovery-rollback-open-refused",
          lambda: homes2_recovery([journal.F_INTENT, journal.F_RIP]) == ("refused", 0))
    check("homes2-recovery-complete-terminal",
          lambda: homes2_recovery([journal.F_INTENT, journal.F_COMPLETE]) == ("recovered", 1))
    check("homes2-recovery-rolled-back-terminal",
          lambda: homes2_recovery([journal.F_INTENT, journal.F_RIP, journal.F_RC]) == ("recovered", 1))
    create = {"op": "create-file", "content_digest": "sha256:" + "0" * 64}
    for home in homes:
        check("adoption-target-" + home, lambda h=home: adopt.validate_op(
            dict(create, path=h + "/file"), homes=2).status != store.VALID)
        check("adoption-legacy-target-" + home, lambda h=home: adopt.validate_op(
            dict(create, path=h + "/file")).status == store.VALID)
    check("adoption-composed-member", lambda: adopt.validate_op(
        {"op": "install-pack", "target": ".working", "members": [
            {"path": "journals/file", "digest": "sha256:" + "0" * 64}]}, homes=2).status != store.VALID)

    check("adoption-root-pack-preserved", lambda: adopt.validate_op(
        {"op": "install-pack", "target": ".", "members": [
            {"path": "notes.txt", "digest": "sha256:" + "0" * 64}]}, homes=2).status == store.VALID)
    check("adoption-root-pack-journal-refused", lambda: adopt.validate_op(
        {"op": "install-pack", "target": ".", "members": [
            {"path": ".working/journals/file", "digest": "sha256:" + "0" * 64}]}, homes=2).status != store.VALID)
    def view_plan(model):
        view_manifest = dict(model, views=dict(VERSION=dict(kind="projection", sources=[],
                                                            target=".working/journals/file")))
        with patch.object(views, "_read_raw_and_parsed", return_value=(b"", view_manifest)), \
                patch.object(store, "validate_manifest", return_value=SimpleNamespace(status=store.VALID)), \
                patch.object(views, "_resolve_view", return_value=("projection", [], lambda _src: "1.0.0\n")), \
                patch.object(views, "_spec_destination", return_value=("store", ".working/journals/file")):
            return refusal(lambda: views.plan_views(-1, machine)) or ""

    with active():
        check("view-journal-destination-refused", lambda: "journal home" in view_plan(manifest2))
        check("view-legacy-journal-destination-planned", lambda: "journal home" not in view_plan(manifest))

    import _opf_adopt_plan as planning
    import _opf_emit as emit

    class _Enumerated(Exception):
        pass

    def investigation(path, unmanaged=False, generation=1):
        model = manifest2 if generation == 2 else manifest
        model = dict(model, unmanaged={"paths": [path]}) if unmanaged else model
        resolved = SimpleNamespace(status=store.RESOLVED, machine_rel=machine)
        with patch.object(store, "SUPPORTED_HOMES", generation), \
                patch.object(store._journal, "require_containment"), \
                patch.object(store, "_open_dir_nofollow", return_value=-1), \
                patch.object(planning.os, "fstat", return_value=SimpleNamespace(st_dev=1, st_ino=1)), \
                patch.object(planning.os, "close"), \
                patch.object(store, "_read_pointer_target", return_value=None), \
                patch.object(store, "resolve_store", return_value=resolved), \
                patch.object(store, "validate_manifest", return_value=SimpleNamespace(status=store.VALID)), \
                patch.object(planning, "_read_rel", return_value=emit.emit_checked(model).encode()), \
                patch.object(store._journal, "_open_parent", side_effect=_Enumerated), \
                patch.object(planning.os, "stat", side_effect=_Enumerated):
            try:
                planning._inventory(Path("/store"), ["notes" if unmanaged else path + "/child"], [])
            except planning.PlanError:
                return "refused"
            except _Enumerated:
                return "enumerated"
        return "completed"

    # Homes 2 excludes and reserves every control root; a legacy store reserves only imports.
    for home in homes:
        check("investigation-control-" + home, lambda h=home: investigation(h, generation=2) == "refused")
        check("investigation-collision-" + home, lambda h=home: investigation(h, True, 2) == "refused")
        check("investigation-legacy-source-" + home, lambda h=home: investigation(h) == "enumerated")
    check("investigation-legacy-imports-collision", lambda: investigation(legacy_root, True) == "refused")
    for home in homes[1:]:
        check("investigation-legacy-unmanaged-" + home, lambda h=home: investigation(h, True) == "enumerated")

    def unresolved_manifest(name):
        directory = SimpleNamespace(st_mode=stat.S_IFDIR)
        with patch.object(store._journal, "require_containment"), \
                patch.object(store, "_open_dir_nofollow", return_value=-1), \
                patch.object(planning.os, "fstat", return_value=SimpleNamespace(st_dev=1, st_ino=1)), \
                patch.object(planning.os, "close"), \
                patch.object(planning.os, "open", return_value=-1), \
                patch.object(planning.os, "scandir",
                             return_value=contextlib.nullcontext([SimpleNamespace(name=name)])), \
                patch.object(planning.os, "stat", return_value=directory), \
                patch.object(store, "_read_pointer_target", return_value=None), \
                patch.object(store, "resolve_store", return_value=SimpleNamespace(
                    status=store.CANNOT_EVALUATE, detail="fixture")), \
                patch.object(store._journal, "_lstat_at", return_value=directory), \
                patch.object(store._journal, "_open_parent", side_effect=_Enumerated):
            try:
                planning._inventory(Path("/store"), ["notes"], [])
            except planning.PlanError as exc:
                return "requires repair" in str(exc)
        return False

    # An unresolved store with a candidate manifest under a reserved name still requires repair.
    for name in store.RESERVED_MACHINE_SUBDIRS:
        check("investigation-unresolved-reserved-" + name, lambda n=name: unresolved_manifest(n))

    # Every generation-dependent call site derives the generation from the store's own manifest read, so a
    # homes-2 store reaches the control-area refusal there and a legacy store is graded exactly as before.
    import datetime
    import tomllib
    same = SimpleNamespace(st_dev=1, st_ino=1, st_mode=stat.S_IFDIR, st_nlink=1, st_size=0,
                           st_mtime_ns=0, st_ctime_ns=0)
    control_op = dict(create, path=".working/journals/file")
    utc = datetime.datetime(2026, 9, 17, 12, tzinfo=datetime.timezone.utc)

    def planned(generation, op, supported=None):
        # `generation` selects the manifest; `supported` (default: the same) is the activated tooling.
        model = manifest2 if generation == 2 else manifest
        resolved = SimpleNamespace(status=store.RESOLVED, machine_rel=machine, detail="", pointer_source="default")
        with patch.object(store, "SUPPORTED_HOMES", generation if supported is None else supported), \
                patch.object(store._journal, "require_containment"), \
                patch.object(store, "_open_dir_nofollow", return_value=-1), \
                patch.object(planning.os, "fstat", return_value=same), \
                patch.object(planning.os, "close"), \
                patch.object(store, "_read_pointer_target", return_value=None), \
                patch.object(store, "resolve_store", return_value=resolved), \
                patch.object(store, "validate_manifest", return_value=SimpleNamespace(status=store.VALID)), \
                patch.object(planning, "_read_rel", return_value=emit.emit_checked(model).encode()), \
                patch.object(store._journal, "_open_parent", side_effect=FileNotFoundError), \
                patch.object(adopt, "validate_plan", wraps=adopt.validate_plan) as frozen:
            observed = planning.investigate(Path("/store"), sources=[])
            result = planning.plan(
                Path("/store"), sources=[], product="opf", decisions=[], ops=[op],
                expected_observation_digest=tomllib.loads(observed.observation.decode())["observation_digest"],
                now=utc, run_nonce="0123456789abcdef")
        return result, [c.kwargs.get("homes") for c in frozen.call_args_list]

    homes2_plan, _ = planned(2, control_op)
    legacy_plan, legacy_frozen = planned(1, control_op)
    ordinary_plan, ordinary_frozen = planned(2, dict(create, path="notes.txt"))
    # Each op loop refuses on its own (its exact findings), not only through the frozen-plan revalidation.
    refused = tuple(adopt.validate_op(control_op, homes=2).findings)
    check("plan-homes2-control-op-refused", lambda: homes2_plan.status == store.INVALID and homes2_plan.plan is None
          and bool(refused) and homes2_plan.findings == refused)
    control_retire = dict(op="retire-file", path=".working/journals/file", preimage_digest="sha256:" + "0" * 64)
    with patch.object(planning, "_decisions", return_value=([control_retire], [])):
        retired, _ = planned(2, dict(create, path="notes.txt"))
        legacy_retired, _ = planned(1, dict(create, path="notes.txt"))
    check("plan-homes2-control-disposition-refused", lambda: retired.status == store.INVALID
          and retired.findings == tuple(adopt.validate_op(control_retire, homes=2).findings) != ())
    check("plan-legacy-control-disposition-planned", lambda: legacy_retired.status == store.VALID)
    check("plan-legacy-control-op-planned", lambda: legacy_plan.status == store.VALID and legacy_frozen == [1])
    activated_plan, activated_frozen = planned(1, control_op, supported=2)
    check("plan-activated-legacy-generation", lambda: activated_plan.status == store.VALID
          and activated_frozen == [1])
    check("plan-homes2-frozen-plan-bound", lambda: ordinary_plan.status == store.VALID and ordinary_frozen == [2])
    check("plan-validate-homes2-refused", lambda: adopt.validate_plan(
        tomllib.loads(legacy_plan.plan.decode()), homes=2).status == store.INVALID)
    check("plan-validate-legacy-unchanged", lambda: adopt.validate_plan(
        tomllib.loads(legacy_plan.plan.decode())).status == store.VALID)

    same_root = SimpleNamespace(status=store.RESOLVED, machine_rel=machine, store_root=Path("/store"),
                                product_root=Path("/store"), pointer_source="default", detail="")

    def detected_rows(generation, model=manifest2):
        with patch.object(store, "SUPPORTED_HOMES", generation), \
                patch.object(store, "_open_store_root_fd", return_value=-1), patch.object(ingest.os, "close"), \
                patch.object(store, "_read_toml_contained", return_value=copy.deepcopy(model)), \
                patch.object(store, "validate_manifest", return_value=SimpleNamespace(status=store.VALID)), \
                patch.object(ingest, "_managed_paths", return_value=(set(), set(), set(), set(), set())), \
                patch.object(ingest, "_detect_store_scope", return_value=[]) as walked:
            return ingest._detect_rows("/store", same_root, None), walked.call_args.kwargs.get("homes")

    check("detect-rows-homes2-generation", lambda: detected_rows(2) == (([], 2), 2))
    check("detect-rows-legacy-generation", lambda: detected_rows(1) == (([], 1), 1))
    check("detect-rows-activated-legacy-generation", lambda: detected_rows(2, manifest) == (([], 1), 1))
    with patch.object(journal, "require_containment"), patch.object(store, "resolve_store", return_value=same_root), \
            patch.object(store, "load_manifest", return_value=SimpleNamespace(status=store.VALID, findings=[])), \
            patch.object(ingest, "_detect_rows", return_value=([], 2)), \
            patch.object(ingest, "validate_worksheet", return_value=[]):
        check("detect-result-carries-generation", lambda: ingest.detect("/store").homes == 2)

    def ingest_planned(generation):
        journal_row = dict(scope="store", source_path=".working/journals/notes.md", disposition="keep", note="")
        with patch.object(journal, "require_containment"), \
                patch.object(ingest, "validate_worksheet", return_value=[]), \
                patch.object(ingest, "validate_options", return_value=[]), \
                patch.object(ingest, "detect", return_value=ingest.DetectResult(ingest.CLEAN, homes=generation)), \
                patch.object(ingest, "_reconcile_worksheet_against_detect"), \
                patch.object(store, "resolve_store", return_value=same_root), \
                patch.object(store, "_open_root_fd", return_value=-1), patch.object(ingest.os, "close"), \
                patch.object(ingest, "_digest_of", side_effect=_Reached):
            try:
                result = ingest.plan_ingest("/store", dict(row=[journal_row]), dict(option=[]), now=utc,
                                            run_nonce="0123456789abcdef")
            except _Reached:
                return "admitted"
        return result.verdict, " ".join(result.findings)

    check("ingest-plan-homes2-control-row-refused", lambda: ingest_planned(2)[0] == ingest.FINDING
          and "reserved store control area" in ingest_planned(2)[1])
    check("ingest-plan-legacy-row-admitted", lambda: ingest_planned(1) == "admitted")

    check("internal-api-capability-required", lambda: refuses(
        lambda: home_journal.run_transaction(object(), "import", run, [], lambda _name: b"")))
    with patch.object(home_journal._opf_oplock, "OpCapability", object):
        check("internal-api-identity-required", lambda: refuses(
            lambda: home_journal.recover_transaction(object(), "import", "../elsewhere")))
    with patch.object(journal, "_lstat_contained", return_value=None), \
            patch.object(journal, "read_frames", side_effect=AssertionError("missing journal was read as empty")):
        check("internal-recovery-missing-refused",
              lambda: refuses(lambda: home_journal._existing_frames(-1, run, "import", run)))

    import threading
    import _opf_oplock as oplock
    cap = oplock.OpCapability.__new__(oplock.OpCapability)
    cap._claim = threading.Lock()
    cap._claimant = None
    cap._released = True
    check("internal-api-released-refused", lambda: refuses(
        lambda: home_journal.recover_transaction(cap, "import", run)))
    cap._released = False
    cap._acquirer_pid = -1
    check("internal-api-foreign-acquirer-refused", lambda: refuses(
        lambda: home_journal.recover_transaction(cap, "import", run)))
    cap._claim.acquire()
    try:
        check("internal-api-busy-refused", lambda: refuses(
            lambda: home_journal.recover_transaction(cap, "import", run)))
    finally:
        cap._claim.release()
    header = {"kind": "import", "run_id": run, "operation_id": "test"}
    frames = [(journal.F_INTENT, {"txn": run, "header": header, "ops": []})]
    import stat
    with patch.object(journal, "_lstat_contained", return_value=SimpleNamespace(st_mode=stat.S_IFREG)), \
            patch.object(journal, "read_frames", return_value=(frames, False, 0)):
        check("internal-api-record-identity", lambda: home_journal._existing_frames(-1, run, "import", run) == frames)
        header["kind"] = "layout"
        check("internal-api-foreign-record-refused",
              lambda: refuses(lambda: home_journal._existing_frames(-1, run, "import", run)))

    # Observe the internal API's derived arguments and payloads with filesystem writes denied
    # by mocks. Real crash/durability and capability integration remain the runtime suite's job.
    import os
    import tomllib
    cap._acquirer_pid = os.getpid()
    cap._acquirer_pid_start = journal._pid_start(os.getpid())
    cap._anchor_fd = 101
    cap._anchor_ident = (1, 101)
    cap._machine_ident = (1, 102)
    cap.store_root = Path("/store")
    cap.machine_rel = machine
    cap.op_id = "held-operation"
    cap.holder = "fixture"

    def fstat(fd):
        return SimpleNamespace(st_dev=1, st_ino=fd, st_nlink=1,
                               st_mode=stat.S_IFREG if fd == 101 else stat.S_IFDIR)

    with patch.object(home_journal.os, "fstat", side_effect=fstat), \
            patch.object(home_journal.os, "close"), \
            patch.object(store, "_open_root_fd", return_value=100), \
            patch.object(journal, "_open_dir_contained", return_value=102), \
            patch.object(journal, "open_journal_root_fd", return_value=103), \
            patch.object(journal, "ensure_journal_dirs") as mkdirs, \
            patch.object(journal, "run_transaction", return_value="complete") as transaction, \
            patch.object(home_journal, "_project") as projection:
        check("internal-api-held-run", lambda: home_journal.run_transaction(
            cap, "import", run, [], lambda _name: b"") == "complete")
        check("internal-api-derived-frame-home", lambda: transaction.call_args.args[2] ==
              Path("/store/.working/journals/import/journal"))
        check("internal-api-bound-operation", lambda: transaction.call_args.args[4] ==
              {"kind": "import", "run_id": run, "operation_id": cap.op_id})
        check("internal-api-projects-terminal", lambda: projection.call_args.args[3:] == ("import", run))

        def attributed(thunk):
            try:
                thunk()
            except journal.JournalError as exc:
                return "operation failed" in str(exc) and "cannot open" not in str(exc)
            return False

        # Recovery with a matching identity reaches the open phase and must create nothing there.
        mkdirs.reset_mock()
        with patch.object(home_journal, "_existing_frames", return_value=frames), \
                patch.object(journal, "recover", return_value="terminal") as recovered:
            check("internal-recovery-held", lambda: home_journal.recover_transaction(
                cap, "import", run) == "terminal" and recovered.call_count == 1)
            check("internal-recovery-creates-nothing", lambda: mkdirs.call_count == 0)
        # A failure inside the operation is attributed to the operation, not to opening the journal.
        transaction.side_effect = OSError("disk full")
        check("internal-api-operation-error-attributed", lambda: attributed(
            lambda: home_journal.run_transaction(cap, "import", run, [], lambda _name: b"")))
        transaction.side_effect = None
        cap._machine_ident = (9, 999)
        check("internal-api-store-swap-refused", lambda: refuses(
            lambda: home_journal.recover_transaction(cap, "import", run)))

    header["kind"] = "import"
    header["operation_id"] = "original-operation"
    record_home = ".working/journals/import/runs/" + run
    with patch.object(home_journal, "_existing_frames", return_value=frames), \
            patch.object(journal, "classify_state", return_value="complete") as classify, \
            patch.object(journal, "_lstat_contained", return_value=None) as prior, \
            patch.object(journal, "ensure_journal_dirs") as mkdirs, \
            patch.object(journal, "_open_parent", return_value=(104, "transaction.toml")), \
            patch.object(oplock, "_remove_staging_garbage"), \
            patch.object(oplock, "_create_control_file", return_value=(105, (1, 105))) as publish, \
            patch.object(home_journal.os, "close"):
        home_journal._project(100, 103, Path("/unused") / run, "import", run)
        payload = publish.call_args.args[2]
        record = tomllib.loads(payload.decode())
        check("internal-projection-derived-home", lambda: mkdirs.call_args.args == (100, record_home))
        check("internal-projection-typed-record", lambda: record == {
            "format": "opf.journal.transaction/v1", "kind": "import", "run_id": run, "state": "complete",
            "operation_id": "original-operation", "journal_rel": ".working/journals/import/journal"})
        classify.return_value = "open"
        check("internal-projection-open-refused", lambda: refuses(
            lambda: home_journal._project(100, 103, Path("/unused") / run, "import", run)))
        classify.return_value = "complete"
        prior.return_value = object()
        with patch.object(journal, "_read_contained", return_value=(b"corrupt", None)):
            check("internal-projection-conflict-refused", lambda: refuses(
                lambda: home_journal._project(100, 103, Path("/unused") / run, "import", run)))
        with patch.object(journal, "_read_contained", return_value=(payload, None)):
            check("internal-projection-idempotent", lambda: home_journal._project(
                100, 103, Path("/unused") / run, "import", run) is None and publish.call_count == 1)

    preview = "/store/.working/staging/preview/preview-run"

    def preview_ignored(generation):
        ignored = {}

        def copytree(_root, _preview, **kwargs):
            hook = kwargs["ignore"]
            for root, names in (
                    ("/store/.working", ["journals", "staging", "imported", "archive"]),
                    ("/store/.working/staging/preview", ["preview-run", "sibling"]),
                    ("/store/nested/.working", ["journals"]),
                    ("/store/nested/.working/staging/preview", ["preview-run"]),
                    ("/store/.working/imports", [run, "sibling"])):
                ignored[root] = hook(root, names)

        importer._assemble_preview(resolution, machine, {}, preview, SimpleNamespace(copytree=copytree), run,
                                   homes=generation)
        return ignored

    legacy_ignored = preview_ignored(1)
    ignored = preview_ignored(2)
    check("preview-legacy-journals-retained", lambda: legacy_ignored["/store/.working"] == set())
    check("preview-legacy-self-retained", lambda: legacy_ignored["/store/.working/staging/preview"] == set())
    check("preview-journals-only", lambda: ignored["/store/.working"] == {"journals"})
    check("preview-self-only", lambda: ignored["/store/.working/staging/preview"] == {"preview-run"})
    check("preview-nested-retained", lambda: ignored["/store/nested/.working"] == set()
          and ignored["/store/nested/.working/staging/preview"] == set())
    check("preview-promoted-only", lambda: ignored["/store/.working/imports"] == {run})
    check("evidence-homes2-roster", lambda: "C-EVIDENCE-ENUM" not in doctor.REQUIRED_CHECKS
          and doctor.required_checks(2).index("C-EVIDENCE-ENUM") == doctor.REQUIRED_CHECKS.index("C-ARCHIVE-ENUM") + 1
          and "C-EVIDENCE-ENUM" in doctor.source_checks(
              SimpleNamespace(checks=dict.fromkeys(doctor.required_checks(2)))))

    for failure in failures:
        print("FAIL: " + failure)
    print("OPF-HOMES BOUNDARY SELF-TEST: {} ({} checks)".format("FAILED" if failures else "OK", len(checked)))
    return 1 if failures else 0


def self_test():
    import _opf_adopt as adopt
    import _opf_import as importer
    import _opf_init as init
    failures = []
    checked = 0

    def check(name, thunk):
        nonlocal checked
        checked += 1
        try:
            if not thunk():
                failures.append(name)
        except Exception as exc:  # a reversal must be a named failed check, not a silent skip
            failures.append("{} ({})".format(name, exc))

    def refuses(thunk):
        try:
            thunk()
        except ValueError:
            return True
        return False

    suffix = "-20260917T120000Z-0123456789abcdef"
    prefixes = {"import": "imp", "ingest": "imp", "adoption": "adopt", "layout": "layout", "preview": "preview"}
    check("control-boundaries", lambda: boundary_self_test() == 0)
    check("kinds", lambda: store.STAGING_KINDS == tuple(prefixes))
    check("homes", lambda: store.STORE_TREE_CONTROL_DIRS == ("imported", "archive", "staging", "journals"))
    check("reservations", lambda: store.RESERVED_MACHINE_SUBDIRS ==
          ("imports", "imported", "archive", "staging", "journals"))
    for constant, expected in (("IMPORTED_REL", ".working/imported"), ("ARCHIVE_REL", ".working/archive"),
                               ("STAGING_REL", ".working/staging"), ("JOURNALS_REL", ".working/journals")):
        check(constant, lambda c=constant, e=expected: getattr(store, c) == e)
    for kind, prefix in prefixes.items():
        run = prefix + suffix
        check("stage-" + kind, lambda: store.stage_run(kind, run) == ".working/staging/{}/{}".format(kind, run))
        check("evidence-" + kind, lambda: store.evidence_run(kind, run) ==
              ".working/imported/{}/{}".format(kind, run))
        check("journal-" + kind, lambda: store.journal_root(kind) == ".working/journals/{}/journal".format(kind))
        check("transaction-" + kind, lambda: store.txn_record(kind, run) ==
              ".working/journals/{}/runs/{}/transaction.toml".format(kind, run))
        for bad in (None, [], "", ".", "..", "journal", run + "\n", run + "/x", "x/" + run,
                    run.upper(), "wrong" + suffix):
            for function in (store.stage_run, store.evidence_run, store.txn_record):
                check("run-refusal-{}-{}-{!r}".format(function.__name__, kind, bad),
                      lambda: refuses(lambda: function(kind, bad)))
    for bad in (None, [], "", "adopt", "migration", "../import", "import/preview"):
        check("kind-refusal-{!r}".format(bad), lambda: refuses(lambda: store.journal_root(bad)))
        for function in (store.stage_run, store.evidence_run, store.txn_record):
            check("kind-refusal-{}-{!r}".format(function.__name__, bad),
                  lambda: refuses(lambda: function(bad, "imp" + suffix)))
    run = "adopt" + suffix
    for path in ("notes/a.md", "adoption/a.md", "space name.txt", "\u00e9.txt"):
        check("move-" + path, lambda: store.moved_dest(path) == ".working/archive/moved/" + path)
        check("preimage-" + path, lambda: store.retire_preimage(run, path) ==
              ".working/archive/adoption/{}/{}".format(run, path))
        check("file-owner-" + path, lambda: adopt._is_contained_filepath(path))
    for bad in (None, [], "", "/", ".", "..", "./a", "a/../b", "a//b", "a/", "/a", "C:a",
                "a\\b", "a\x00b", "a\nb", "a\x7fb", "a\x85b", "a\u2028b", "a\u2029b"):
        check("move-refusal-{!r}".format(bad), lambda: refuses(lambda: store.moved_dest(bad)))
        check("preimage-refusal-{!r}".format(bad), lambda: refuses(lambda: store.retire_preimage(run, bad)))
        check("file-owner-refusal-{!r}".format(bad), lambda: not adopt._is_contained_filepath(bad))
    check("preimage-run-refusal", lambda: refuses(lambda: store.retire_preimage("imp" + suffix, "a")))
    check("import-grammar-owner", lambda: re.compile("^imp" + store._HOME_RUN_SUFFIX + r"\Z").pattern ==
          importer._RUN_ID_RE.pattern)
    check("adoption-grammar-owner", lambda: re.compile("^adopt" + store._HOME_RUN_SUFFIX + r"\Z").pattern ==
          adopt._RUN_ID_RE.pattern)
    import_run = "imp" + suffix
    check("inventory-first", lambda: store.evidence_inventory("import", import_run) ==
          ".working/imported/import/{}/inventory.toml".format(import_run))
    check("inventory-phase", lambda: store.evidence_inventory("import", import_run, "evidence") ==
          ".working/imported/import/{}/inventory-evidence.toml".format(import_run))
    for bad in ("", "Evidence", "1st", "a/b", "a" * 33, 5, []):
        check("inventory-phase-refusal-{!r}".format(bad),
              lambda: refuses(lambda: store.evidence_inventory("import", import_run, bad)))
    check("inventory-run-refusal", lambda: refuses(lambda: store.evidence_inventory("import", "adopt" + suffix)))

    # Real discovery, including legacy-token upgrade discovery and an arbitrary custom machine name.
    with tempfile.TemporaryDirectory(prefix="opf-homes-") as tmp:
        for token in (store.STANDARD_TOKEN, store.PRIOR_STANDARD_TOKEN):
            for name in ("imports", "imported", "archive", "staging", "journals", "toml", "custom"):
                root = Path(tmp) / token / name
                machine = root / ".working" / name
                machine.mkdir(parents=True)
                (machine / "manifest.toml").write_text(
                    '[{}]\nstandard = "{}"\n'.format(token, token), encoding="utf-8")
                fd = store._open_root_fd(root)
                try:
                    def discovery():
                        try:
                            status, found, _ = store.discover_machine_store(fd, root, accept_tokens=(token,))
                        except store.StoreError:
                            return name in ("imports", "imported", "archive", "staging", "journals")
                        return name in ("toml", "custom") and status == "one" and found == name
                    check("discovery-{}-{}".format(token, name), discovery)
                finally:
                    store.os.close(fd)

    block = "# >>> opf-managed >>>\n/journals/\n/staging/\n# <<< opf-managed <<<\n"
    check("gitignore-render", lambda: store.render_homes_gitignore() == block)
    for text in (block, "# adopter\n" + block + "/local/\n"):
        check("gitignore-match", lambda: store.homes_gitignore_matches(text))
    for bad in (None, "", block + block, block.replace("/staging/", "/imported/"),
                block.replace("/journals/\n", ""), block.replace("\n", "\r\n"), block.rstrip("\n"),
                block.replace("# <<< opf-managed <<<", "# >>> opf-managed >>>")):
        check("gitignore-drift-{!r}".format(bad), lambda: not store.homes_gitignore_matches(bad))

    check("inert-version", lambda: store.SUPPORTED_SPEC_VERSION == "1.1.0")
    check("inert-homes", lambda: store.SUPPORTED_HOMES == 1
          and store.homes_generation({"opf": {"homes": 2}}) == 1)
    check("inert-init", lambda: init._manifest_model()["opf"]["spec_version"] == "1.1.0"
          and "homes" not in init._manifest_model()["opf"])
    check("inert-import", lambda: (importer.IMPORTS_REL, importer.IMPORT_OPS_REL, importer.IMPORT_ARCHIVE_REL) ==
          (".working/imports", ".aiqt/import", ".aiqt/import-archive"))
    check("inert-root-exclusions", lambda: store.STORE_ROOT_CONTROL_DIRS == (".git", ".aiqt"))
    source = Path(store.__file__).read_text(encoding="utf-8")
    check("transitional-comment", lambda: "In homes 2, .aiqt is AIQT-only" in source
          and "Until homes 2 is activated, legacy import state still" in source)
    text = SPEC.read_text(encoding="utf-8")
    check("spec-contract", lambda: not contract_findings(text))
    # Each operative section independently discriminates: removing it must fail the drift gate.
    for section, body in _sections(text).items():
        if section in _CONTRACT:
            check("spec-flip-" + section, lambda: bool(contract_findings(text.replace(body, "\n", 1))))
    for failure in failures:
        print("FAIL: " + failure)
    print("OPF-HOMES SELF-TEST: {} ({} checks)".format("FAILED" if failures else "OK", checked))
    return 1 if failures else 0


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    try:
        if args == ["--self-test"]:
            return self_test()
        if args:
            print("check_opf_homes: unexpected arguments", file=sys.stderr)
            return 2
        findings = contract_findings(SPEC.read_text(encoding="utf-8"))
        for finding in findings:
            print("check_opf_homes: " + finding)
        return 1 if findings else 0
    except (OSError, UnicodeError, ValueError, AttributeError) as exc:
        print("check_opf_homes: cannot evaluate: {}".format(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
