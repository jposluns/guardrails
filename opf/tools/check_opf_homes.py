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
            "machine-local", "git add -f", "tracked staging or journals", "layout-", "preview-"),
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
             "declaration may name or contain them", ".working/archive/adoption/<run-id>/",
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

    run = "imp-20260917T120000Z-0123456789abcdef"
    machine = ".working/toml"
    manifest = {"opf": {"layout": "inline"}, "types": {}, "views": {}}
    homes = tuple(".working/" + name for name in ("imports", "imported", "archive", "staging", "journals"))
    def discover_read(_fd, rel):
        if rel.startswith(".working/journals/"):
            raise AssertionError("discovery read journal content")
        return {"opf": {"standard": store.STANDARD_TOKEN}}

    with patch.object(store, "_immediate_subdirs", return_value=["journals", "toml"]), \
            patch.object(store, "_read_toml_contained", discover_read):
        check("discovery-never-reads-journals",
              lambda: store.discover_machine_store(-1, "/store")[:2] == ("one", "toml"))
    cls = doctor.classify_containment(manifest, machine)
    check("classifier-roster", lambda: cls.control_roots == homes)
    check("evidence-roots-disjoint", lambda: cls.evidence_roots ==
          (".working/imported", ".working/archive") and cls.archive_root not in cls.evidence_roots)
    for home in homes:
        for operand in (home, home + "/child", ".working", "./" + home + "/"):
            altered = dict(manifest, unmanaged={"paths": [operand]})
            check("unmanaged-collision-" + operand,
                  lambda m=altered: bool(doctor.classify_containment(m, machine).colliding)
                  and not doctor.classify_containment(m, machine).valid_unmanaged)
    resolution = SimpleNamespace(machine_rel=machine, store_root=Path("/store"), product_root=Path("/store"))
    check("detect-checker-set-equality",
          lambda: ingest._managed_paths(resolution, manifest)[0] == {machine} | set(homes))
    import stat

    def detect_open(name, *_args, **_kwargs):
        if name != ".working":
            raise AssertionError("detect entered control home: " + name)
        return 99

    for mode in (stat.S_IFDIR, stat.S_IFREG):
        with patch.object(ingest.os, "open", side_effect=detect_open), patch.object(ingest.os, "close"), \
                patch.object(ingest.os, "listdir", return_value=[h.split("/")[-1] for h in homes]), \
                patch.object(ingest.os, "stat", return_value=SimpleNamespace(st_mode=mode)), \
                patch.object(ingest, "_digest_of", side_effect=AssertionError("read control bytes")):
            check("detect-never-reads-controls-" + str(mode), lambda: ingest._detect_store_scope(
                -1, ingest._managed_paths(resolution, manifest)[0], set(), set()) == [])
    for home in homes:
        check("frozen-row-refusal-" + home, lambda h=home: refuses(lambda: ingest.admit_row_scope("store", h, "")))
    check("move-resolved-ancestor", lambda: refuses(lambda: ingest.admit_move_boundary("ops", "ops/.working")))
    check("move-outside-preserved", lambda: ingest.admit_move_boundary("outside/file", "ops/.working") is None)

    files = {}
    directories = {".working"}
    listed = []
    unreadable = set()
    inventory = [None]

    def listing(_fd, rel):
        listed.append(rel)
        if rel == ".working/journals" or rel.startswith(".working/journals/"):
            raise AssertionError("doctor read journals")
        if rel in unreadable or rel in files:
            raise store.StoreError("unreadable or wrong-type input: " + rel)
        if rel not in directories:
            return None, None
        prefix = rel + "/"
        dirs = sorted(d[len(prefix):] for d in directories if d.startswith(prefix) and "/" not in d[len(prefix):])
        leaves = sorted(d[len(prefix):] for d in files if d.startswith(prefix) and "/" not in d[len(prefix):])
        return dirs, leaves

    def add(path, raw=b"x"):
        files[path] = raw
        parts = path.split("/")
        directories.update("/".join(parts[:n]) for n in range(1, len(parts)))

    def read_toml(_fd, rel, rep):
        if rel != machine + "/evidence.toml":
            raise AssertionError("unexpected inventory authority: " + rel)
        return (copy.deepcopy(inventory[0]), "ok") if inventory[0] is not None else (None, "absent")

    def read_bytes(_fd, rel, rep):
        if rel in unreadable:
            rep.cant("unreadable " + rel)
            return None, "error"
        return (files[rel], "ok") if rel in files else (None, "absent")

    def containment(partial=False):
        report = doctor._Report()
        report.ran("C-CONTAINMENT")
        doctor._check_containment(0, machine, manifest, "partial" if partial else "complete", report)
        return report

    def evidence():
        report = doctor._Report()
        report.ran("C-EVIDENCE-ENUM")
        doctor._check_evidence(0, machine, report)
        return report

    with patch.object(doctor, "_list_contained", listing), patch.object(doctor, "_read_toml", read_toml), \
            patch.object(doctor, "_read_bytes", read_bytes):
        stage = ".working/staging/import/" + run + "/plan.toml"
        add(stage)
        add(".working/journals/import/journal/inflight/frames.log", b"torn")
        check("staging-steady-finding", lambda: any(stage in s for s in containment().findings))
        check("staging-substantiated-triage", lambda: bool(containment(True).triage)
              and not containment(True).findings)
        del files[stage]
        check("empty-run-grades", lambda: any(run in s for s in containment().findings))
        check("empty-run-not-partial", lambda: bool(containment(True).findings) and not containment(True).triage)
        add(stage)
        add(".working/staging/unknown/run/file")
        check("unknown-kind-always-finding", lambda: any("invalid staging kind" in s
                                                       for s in containment(True).findings))
        files.clear()
        directories.clear()
        directories.update({".working", ".working/journals"})
        check("journals-pruned", lambda: not containment().findings and not containment().cannot)
        check("journals-never-listed", lambda: ".working/journals" not in listed)
        check("evidence-legacy-absent", lambda: not evidence().findings and not evidence().cannot)
        imported = ".working/imported/import/" + run + "/source"
        archived = ".working/archive/moved/notes.txt"
        for path in (imported, archived):
            add(path, b"retained")
        inventory[0] = {"format": "opf.evidence.inventory/v1", "file": [
            {"path": path, "size": 8, "sha256": hashlib.sha256(b"retained").hexdigest()}
            for path in (imported, archived)]}
        check("evidence-members-match", lambda: not evidence().findings and not evidence().cannot)
        check("evidence-not-containment-graded", lambda: not containment().findings)
        for path in (imported, archived):
            files[path + ".stray"] = b"rogue"
            check("evidence-off-inventory-" + path, lambda: bool(evidence().findings))
            del files[path + ".stray"]
            del files[path]
            check("evidence-missing-" + path, lambda: any(path in s for s in evidence().findings))
            files[path] = b"tampered"
            check("evidence-digest-" + path, lambda: any("mismatch" in s for s in evidence().findings))
            files[path] = b"retained"
            unreadable.add(path)
            check("evidence-unreadable-" + path, lambda: bool(evidence().cannot))
            unreadable.clear()
        directories.add(".working/archive/empty-stray")
        check("evidence-empty-stray", lambda: bool(evidence().findings))
        directories.remove(".working/archive/empty-stray")
        saved = copy.deepcopy(inventory[0])
        inventory[0]["file"].append(copy.deepcopy(inventory[0]["file"][0]))
        check("evidence-duplicate-refused", lambda: bool(evidence().cannot))
        inventory[0] = {"format": "unsupported", "file": []}
        check("evidence-format-refused", lambda: bool(evidence().cannot))
        inventory[0] = None
        check("evidence-no-inventory-refused", lambda: bool(evidence().findings))
        inventory[0] = saved
        unreadable.add(".working/imported")
        check("evidence-unreadable-root", lambda: bool(evidence().cannot))
        unreadable.clear()

    import _opf_init as init
    full_manifest = init._manifest_model()
    full_manifest["views"] = {}
    inventory[0] = None

    def doctor_toml(_fd, rel, _rep):
        if rel == machine + "/manifest.toml":
            return full_manifest, "ok"
        if rel.startswith(".working/journals/"):
            raise AssertionError("doctor read a journal record")
        return None, "absent"

    with patch.object(doctor, "_list_contained", listing), patch.object(doctor, "_read_toml", doctor_toml), \
            patch.object(doctor, "_read_bytes", read_bytes), patch.object(importer, "_sibling_ids", return_value=[]):
        report = doctor._Report()
        doctor._validate_opened_store(0, None, machine, None, {}, None, "default", True, report)
        check("doctor-dispatches-evidence", lambda: report.checks.get("C-EVIDENCE-ENUM") == "FINDING")
        check("doctor-check-roster", lambda: set(report.checks) == set(doctor.REQUIRED_CHECKS))
        check("doctor-never-lists-journals", lambda: not any(
            path == ".working/journals" or path.startswith(".working/journals/") for path in listed))

    # Every ordinary operation kind is checked before any I/O, including direct apply and recovery.
    with patch.object(journal, "_open_parent", side_effect=AssertionError("opened ordinary target")), \
            patch.object(journal, "_open_txn_beneath", side_effect=AssertionError("opened journal")), \
            patch.object(journal.os, "mkdir", side_effect=AssertionError("created journal")):
        for kind in journal.OP_KINDS:
            for target in (".working", ".working/journals", ".working/journals/import/journal/frame"):
                ops = [{"op": kind, "path": target}]
                check("ordinary-{}-{}".format(kind, target), lambda o=ops: refuses(
                    lambda: journal.apply_ops(-1, o, lambda _name: b"")))
                check("preimage-{}-{}".format(kind, target), lambda o=ops: refuses(
                    lambda: journal.capture_preimages(-1, Path("/unused"), -1, o)))
                check("transaction-{}-{}".format(kind, target), lambda o=ops: refuses(
                    lambda: journal.run_transaction(-1, -1, "/unused", run, {}, o, lambda _name: b"", "test")))
    check("ordinary-sibling-allowed",
          lambda: journal._check_ordinary_ops([{"op": "create", "path": ".working/journals-old/file"}]) is None)
    with patch.object(journal, "read_frames", return_value=(
            [(journal.F_INTENT, {"txn": run, "ops": [{"op": "remove", "path": ".working/journals"}]})], True, 1)), \
            patch.object(journal, "_truncate_log", side_effect=AssertionError("mutated before refusal")):
        check("recovery-target-refused-before-truncate", lambda: refuses(lambda: journal.recover(-1, run, -1)))
    for home in homes:
        check("adoption-target-" + home, lambda h=home: adopt.validate_op(
            {"op": "create-file", "path": h + "/file", "content_digest": "sha256:" + "0" * 64}).status != store.VALID)
    check("adoption-composed-member", lambda: adopt.validate_op(
        {"op": "install-pack", "target": ".working", "members": [
            {"path": "journals/file", "digest": "sha256:" + "0" * 64}]}).status != store.VALID)

    check("adoption-root-pack-preserved", lambda: adopt.validate_op(
        {"op": "install-pack", "target": ".", "members": [
            {"path": "notes.txt", "digest": "sha256:" + "0" * 64}]}).status == store.VALID)
    check("adoption-root-pack-journal-refused", lambda: adopt.validate_op(
        {"op": "install-pack", "target": ".", "members": [
            {"path": ".working/journals/file", "digest": "sha256:" + "0" * 64}]}).status != store.VALID)
    view_manifest = {"opf": {"layout": "inline"}, "views": {
        "VERSION": {"kind": "projection", "sources": [], "target": ".working/journals/file"}}}
    with patch.object(views, "_read_raw_and_parsed", return_value=(b"", view_manifest)), \
            patch.object(store, "validate_manifest", return_value=SimpleNamespace(status=store.VALID)), \
            patch.object(views, "_resolve_view", return_value=("projection", [], lambda _src: "1.0.0\n")), \
            patch.object(views, "_spec_destination", return_value=("store", ".working/journals/file")):
        check("view-journal-destination-refused", lambda: refuses(lambda: views.plan_views(-1, machine)))

    import _opf_adopt_plan as planning
    import _opf_emit as emit

    def investigation_refuses(path, unmanaged=False):
        model = dict(manifest, unmanaged={"paths": [path]}) if unmanaged else manifest
        resolved = SimpleNamespace(status=store.RESOLVED, machine_rel=machine)
        with patch.object(store._journal, "require_containment"), \
                patch.object(store, "_open_dir_nofollow", return_value=-1), \
                patch.object(planning.os, "fstat", return_value=SimpleNamespace(st_dev=1, st_ino=1)), \
                patch.object(planning.os, "close"), \
                patch.object(store, "_read_pointer_target", return_value=None), \
                patch.object(store, "resolve_store", return_value=resolved), \
                patch.object(store, "validate_manifest", return_value=SimpleNamespace(status=store.VALID)), \
                patch.object(planning, "_read_rel", return_value=emit.emit_checked(model).encode()), \
                patch.object(planning.os, "stat", side_effect=AssertionError("read excluded adoption source")):
            try:
                planning._inventory(Path("/store"), ["notes" if unmanaged else path + "/child"], [])
            except planning.PlanError:
                return True
        return False

    for home in homes:
        check("investigation-control-" + home, lambda h=home: investigation_refuses(h))
        check("investigation-collision-" + home, lambda h=home: investigation_refuses(h, True))

    check("internal-api-capability-required", lambda: refuses(
        lambda: home_journal.run_transaction(object(), "import", run, [], lambda _name: b"")))
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
        cap._machine_ident = (9, 999)
        check("internal-api-store-swap-refused", lambda: refuses(
            lambda: home_journal.recover_transaction(cap, "import", run)))
        check("internal-recovery-creates-nothing", lambda: mkdirs.call_count == 1)

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

    ignored = {}
    preview = "/store/.working/staging/preview/preview-run"

    def copytree(_root, _preview, **kwargs):
        hook = kwargs["ignore"]
        for root, names in (
                ("/store/.working", ["journals", "staging", "imported", "archive"]),
                ("/store/.working/staging/preview", ["preview-run", "sibling"]),
                ("/store/nested/.working", ["journals"]),
                ("/store/nested/.working/staging/preview", ["preview-run"]),
                ("/store/.working/imports", [run, "sibling"])):
            ignored[root] = hook(root, names)

    importer._assemble_preview(resolution, machine, {}, preview, SimpleNamespace(copytree=copytree), run)
    check("preview-journals-only", lambda: ignored["/store/.working"] == {"journals"})
    check("preview-self-only", lambda: ignored["/store/.working/staging/preview"] == {"preview-run"})
    check("preview-nested-retained", lambda: ignored["/store/nested/.working"] == set()
          and ignored["/store/nested/.working/staging/preview"] == set())
    check("preview-promoted-only", lambda: ignored["/store/.working/imports"] == {run})
    check("evidence-required-roster", lambda: "C-EVIDENCE-ENUM" in doctor.REQUIRED_CHECKS
          and "C-EVIDENCE-ENUM" in doctor.SOURCE_INTEGRITY_CHECKS)

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
                        if name == store.JOURNALS_DIRNAME:
                            return status == "present" and found is None
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
