#!/usr/bin/env python3
"""Homes contract, control-boundary and gitignore drift gate (writers still use legacy homes).

Checks documented topology against the constructor authority. Text checks protect the planned
contract, not runtime conformance to homes 2. No store is mutated or required to install a block.
Residual: wording checks require pinned text to be present; except for the self-test
reconstruction of section 9.2, they do not detect added contradictory claims that leave the pins intact.
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
    "9.2": (
        'The homes-generation upgrade targets spec_version = "2.0.0" with required integer '
        '[opf].homes = 2.',
        'Absent or 1 denotes legacy homes for migration; unknown future generations are refused.',
        'The runtime supported version and init format remain unchanged until homes 2 is activated.',
        'The migration refuses a store resolved outside the product root until a multi-root '
        'coordinator exists.',
        'Unproven legacy .archive/ entries remain in place with a standing finding until '
        'dispositioned.',
        'A base-schema version bump ships a tested, in-place store-schema upgrade (opf upgrade).',
        'The upgrade is idempotent.',
        'A purely schema-level bump is additive, using atomic replacement of existing files, '
        'create-only writes for new index files, and regeneration of declared views through '
        'exclusively created temporary files followed by atomic rename.',
        'These writes are sequential, with recovery scope held in memory, not a durable '
        'transaction journal.',
        'A homes-generation bump additionally relocates OPF control areas as a versioned, '
        'journaled, fail-closed relocation.',
        'Every destination is digest-verified before its source is removed.',
        'Both kinds of upgrade run under the store consistency contract and the single-writer lease '
        '(section 5.7).',
        'It fails closed on an unresolvable store, a declared spec_version ABOVE the tooling, a '
        'divergence, a held lease, or any populated state that contradicts its preconditions; it '
        'never lowers the fail-closed floor.',
        'Before any write it enforces two fail-closed preconditions: it claims the single-writer '
        'lease (section 5.7) and holds it across the whole mutation, and it verifies the working '
        'tree is clean, including ignored files, over the planned schema and render destinations '
        '(store and product roots) and the index collision candidates, so the committed HEAD is a '
        'verified restore path for that scope; a held lease or a dirty store refuses, and a dirty '
        'store is asked to commit its own changes, never restored by the tool.',
        'After applying the schema delta, it regenerates the declared views and requires a full '
        "doctor VALID before offering the uncommitted change for the adopter's own "
        'branch-and-merge.',
        'It never stages or commits the change.',
        'The manifest and counters rewrite is a model regeneration through the canonical '
        'new-document emitter, never a textual round-trip edit, bounded by two guards: a '
        'precondition that re-emitting the UNCHANGED parsed model reproduces the on-disk bytes '
        'exactly (proving the file is canonical and comment-free, so nothing can be lost), failing '
        'closed otherwise; and a postcondition that the model diff equals exactly the allowed '
        'delta, failing closed otherwise.',
        'The allowed delta is expressed as ensure-present and ensure-absent over the whole 1.0.0 '
        'origin family, so a governance-enabled, a decision_support-enabled, a bare, and a '
        'view-omitting 1.0.0 store all migrate under one rule and the normative text cannot diverge '
        'from the tooling.',
        'For the 1.0.0 to 1.1.0 upgrade the allowed delta is: rename the base table [devprocess] to '
        '[opf] and its standard discovery token from devprocess to opf (the OPFiles rebrand), '
        'carrying every other base field over unchanged; bump spec_version to 1.1.0; remove the '
        'retired decision_support module key where present; add each of the [types] rows for '
        'contribution, maintainer_decision, and preference_pattern not already declared by an '
        'enabled 1.0.0 module (a governance-enabled store already declares maintainer_decision and '
        'a decision_support-enabled store preference_pattern; the row moves from module tier to '
        'baseline unchanged); add the two new view rows (CONTRIBUTIONS.md and the DECISIONS.toml '
        "projection); widen the existing DECISIONS.md composed view's sources from the two 1.0.0 "
        'decision sources (pending_decision, autonomous_decision) to the four required at 1.1.0 by '
        'adding maintainer_decision and preference_pattern where that view is declared (a 1.0.0 '
        'store that declares no DECISIONS.md gains none and stays valid, since no composed view is '
        'required); extend counters.toml with the CN/MD/PP zeros while preserving every existing '
        'high-water; and create each missing empty *.index.toml file for the three baseline types, '
        'skipping any that already exist (such as a maintainer_decision.index.toml where governance '
        'was enabled, whose records are preserved byte-for-byte).',
        'The upgrade weakens nothing: preference_pattern simply moves to always-on, so a populated '
        'decision-support index is kept as is.',
        'Base spec 1.2.0 admits one new managed machine-store file, .working/toml/init.toml: the '
        'bootstrap provenance a coupled opf init records (its format is frozen in OPF-INIT-D2B).',
        'It is a managed leaf when present and is never required, so a store without it stays '
        'valid.',
        'For the 1.1.0 to 1.2.0 upgrade the allowed schema delta is the spec_version bump alone: no '
        'other manifest field, schema file, or counter changes, and no provenance is created for an '
        'existing store (none is ever fabricated).',
        'Declared views are then regenerated, so a stale committed view can change.',
        'A 1.0.0 store takes the 1.0.0 delta above directly to 1.2.0.',
    ),
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


def _staged_generation_self_test(check):
    """Public-boundary refusals, including empty inventories and both run kinds."""
    from unittest.mock import patch
    import check_opf_import as gate
    import _opf_import as imp
    import _opf_ingest as ingest

    cases = (
        ("ordinary-unsupplied-generation-cannot", ((2, None),)),
        ("ordinary-invalid-generation-cannot", tuple(
            (ceiling, bad) for ceiling in (1, 2)
            for bad in (True, False, 1.0, 2.0, 1.5, float("nan"), "1", "2", 0, -1, 3))),
        ("ordinary-unsupported-generation-cannot", ((1, 2),)),
    )
    outcomes = {name: [] for name, _ in cases}
    unprobed, unopened, legacy, ordered = [], [], [], []
    for is_ingest in (False, True):
        for empty in (False, True):
            rd, files = imp._memory_ingest_run()
            rd.close = lambda: None
            if not is_ingest:
                for name in imp._INGEST_RUN_MARKERS:
                    files.pop(name, None)
                    rd.tree.pop(name, None)
                inv = rd.load_toml(imp.INVENTORY_NAME)
                proposals = [dict(p, _origin=p["origin"])
                             for p in rd.load_toml(imp.PROPOSALS_NAME)["proposal"]]
                files[imp.REPORT_MD_NAME] = imp._render_report_md(
                    inv["inventory_digest"], inv["fragment"], proposals, rd.path.name).encode()
            if empty:
                # Refusal must not depend on inventory rows or their consistency with other artefacts.
                files[imp.INVENTORY_NAME] = imp._emit_bytes(imp._build_inventory([])[0], imp.INVENTORY_NAME)
            expected_failures = {"transaction-schema", "transaction-consistency"}
            if empty:
                expected_failures.add("report-binding-digests")
                expected_failures.update(
                    ("ingest-source-binding", "ingest-report-reproducibility")
                    if is_ingest else ("proposals-artifact",))
            with patch.object(gate, "_RunDir", return_value=rd) as open_run, \
                    patch.object(gate, "_ingest_store_fd", return_value=None) as locate, \
                    patch.object(gate, "_staged_run_store_fd",
                                 side_effect=gate._GateError("synthetic store unavailable")) as transaction:
                for name, values in cases:
                    for ceiling, bad in values:
                        with patch.object(store, "SUPPORTED_HOMES", ceiling):
                            open_run.reset_mock()
                            locate.reset_mock()
                            transaction.reset_mock()
                            result = (gate.check_staged_run(rd.path) if bad is None
                                      else gate.check_staged_run(rd.path, homes=bad))
                            reason = ("was not supplied" if bad is None else "supplied homes generation")
                            outcomes[name].append(
                                tuple(result) == gate.EXPECTED_CHECKS and all(
                                    not ok and detail.startswith("cannot evaluate:")
                                    and str(rd.path) in detail and reason in detail
                                    for ok, detail in result.values()))
                            unopened.append(not open_run.called)
                            unprobed.append(not locate.called and not transaction.called)
                for ceiling in (1, 2):
                    with patch.object(store, "SUPPORTED_HOMES", ceiling):
                        baseline = list(gate._check_staged_run(rd, homes=1).items())
                        result = gate.check_staged_run(rd.path, homes=1)
                        # Registry-complete: every expected id exactly once (order is pinned by the
                        # baseline comparison below, since results are not emitted in registry order).
                        ordered.append(len(result) == len(gate.EXPECTED_CHECKS) and
                                       set(result) == set(gate.EXPECTED_CHECKS))
                        legacy.append(list(result.items()) == baseline and
                                      {cid: ok for cid, (ok, _detail) in result.items()} ==
                                      {cid: cid not in expected_failures for cid in gate.EXPECTED_CHECKS})
                        if ceiling == 1:
                            legacy.append(list(gate.check_staged_run(rd.path).items()) == baseline)
    for name, values in outcomes.items():
        check(name, lambda v=values: all(v))
    check("staged-generation-no-store-probe", lambda: all(unprobed))
    check("staged-generation-no-run-open", lambda: all(unopened))
    check("staged-generation-registry-complete", lambda: all(ordered))
    check("staged-generation-legacy-values-and-order", lambda: all(legacy))
    with patch.object(gate, "_gate_homes", side_effect=gate._GateError("generation policy sentinel")):
        check("staged-generation-shared-row-policy", lambda: gate._row_scope_error(
            ingest, [], None, 1) == "cannot evaluate: generation policy sentinel")


def _staged_root_self_test(check):
    """Exercise physical root binding independently of transaction support.

    Root depth, custom-machine, pointer and decoy cases are controls: they already pass on
    the predecessor. The detached homes-2 binding case discriminates the new refusal.
    """
    import errno
    import os
    import shutil
    from unittest.mock import patch
    import check_opf_import as gate
    import _opf_import as imp

    def grade(run, generation=2):
        with patch.object(store, "SUPPORTED_HOMES", 2):
            return gate.check_staged_run(run, homes=generation)

    def bound(run, root):
        with patch.object(store, "SUPPORTED_HOMES", 2):
            rd = gate._RunDir(run)
            try:
                fd = gate._staged_run_store_fd(rd, 2)
                try:
                    actual, expected = os.fstat(fd), os.stat(root)
                    return (actual.st_dev, actual.st_ino) == (expected.st_dev, expected.st_ino)
                finally:
                    os.close(fd)
            finally:
                rd.close()

    def binding_refused(run):
        with patch.object(store, "SUPPORTED_HOMES", 2):
            rd = gate._RunDir(run)
            try:
                try:
                    fd = gate._staged_run_store_fd(rd, 2)
                except gate._GateError as exc:
                    return str(exc) == "no registered store binding for homes generation 2"
                os.close(fd)
                return False
            finally:
                rd.close()

    def refused(result, needle):
        return all(not result[cid][0] and needle in result[cid][1]
                   for cid in gate._TRANSACTION_CHECKS)

    with tempfile.TemporaryDirectory(prefix="opf-staged-root-") as tmp:
        base = Path(tmp).resolve()
        for kind in ("import", "ingest"):
            root = base / kind
            machine = root / ".working" / "custom"
            machine.mkdir(parents=True)
            (machine / "manifest.toml").write_text('[opf]\nstandard = "opf"\n', encoding="utf-8")
            run = gate._self_test_gate_generation_disk(
                root, "accepted" if kind == "import" else None, location=kind)
            check("staged-root-homes2-" + kind, lambda: bound(run, root))
            resolution = store.resolve_store(root)
            check("staged-root-custom-machine-" + kind, lambda:
                  resolution.status == store.RESOLVED and resolution.machine_dir == "custom"
                  and bound(run, resolution.store_root))
            product = base / (kind + "-product")
            product.mkdir()
            (product / store.POINTER_REL).write_text(
                '[store]\ntarget = "dir:{}"\n'.format(root), encoding="utf-8")
            resolution = store.resolve_store(product)
            check("staged-root-pointer-" + kind, lambda:
                  resolution.status == store.RESOLVED and resolution.store_root == root
                  and bound(run, resolution.store_root))
            check("staged-root-generation-mismatch-" + kind, lambda:
                  refused(grade(run, 1), "registered outside homes generation 1"))
            record = root / imp._txn_record_rel(run.name)
            record.parent.mkdir(parents=True)
            record.write_bytes(b"state =\n")
            # The depth-three decoy is absent; diagnose the actual root's corruption.
            check("staged-root-wrong-level-decoy-" + kind, lambda:
                  not grade(run)["transaction-schema"][0]
                  and "unreadable/unparseable" in grade(run)["transaction-schema"][1])
            record.unlink()
            clean = grade(run)
            check("staged-root-no-transaction-" + kind, lambda:
                  clean["staged-run-structure"] == (True, "")
                  and all(clean[cid] == (
                      True, gate._HOMES2_NO_LEGACY_TRANSACTION_DETAIL)
                      for cid in gate._TRANSACTION_CHECKS))
            # Inject only after binding: the real constructor has already classified both homes.
            real_bind, real_stage = gate._staged_run_store_fd, store.stage_run
            for error in (gate._BindingRefusal, RuntimeError):
                opened = []
                def bind_then_arm(rd, generation):
                    fd = real_bind(rd, generation)
                    opened.append(fd)
                    return fd
                def fail_kind(*args):
                    if opened:
                        raise error("kind-check sentinel")
                    return real_stage(*args)
                with patch.object(gate, "_staged_run_store_fd", side_effect=bind_then_arm), \
                        patch.object(store, "stage_run", side_effect=fail_kind):
                    observed = grade(run)
                closed = False
                if opened:
                    try:
                        os.fstat(opened[0])
                    except OSError as exc:
                        closed = exc.errno == errno.EBADF
                check("staged-root-kind-exception-{}-{}".format(error.__name__, kind), lambda:
                      bool(opened) and closed and refused(observed, "kind-check sentinel"))

            # Attempts belong to the coordinator, including open and rolled-back journals.
            # These real frames pin the standalone gate's deliberately narrower transaction scope.
            if kind == "ingest":
                import _journal
                import _opf_journal
                journal = root / store.journal_root(kind)
                journal.mkdir(parents=True)
                attempt = journal / _opf_journal.attempt_txn(kind, run.name, 1)
                attempt.mkdir()
                jfd = store._open_root_fd(journal)
                try:
                    _journal.publish(jfd, attempt, _journal.F_INTENT, dict(
                        txn=attempt.name, header=dict(kind=kind, run_id=run.name, attempt=1,
                                                     operation_id="synthetic-operation"), ops=[]))
                    for state in ("open", "rolled-back"):
                        if state == "rolled-back":
                            for frame in (_journal.F_RIP, _journal.F_RC):
                                _journal.publish(jfd, attempt, frame, {"txn": attempt.name})
                        check("staged-root-attempt-" + state, lambda:
                              _journal.classify_state(jfd, attempt) == state
                              and grade(run) == clean)
                    _journal.publish(jfd, attempt, _journal.F_INTENT, {"txn": attempt.name})
                    malformed = False
                    try:
                        _journal.classify_state(jfd, attempt)
                    except _journal.JournalError:
                        malformed = True
                    check("staged-root-attempt-malformed", lambda:
                          malformed and grade(run) == clean)
                finally:
                    os.close(jfd)
                shutil.rmtree(attempt)
            # A typed projection/journal is not interchangeable with durable review acceptance.
            # Probe both namespaces even when the staging kind differs; empty bytes still count.
            for txn_kind in ("import", "ingest"):
                typed = root / store.txn_record(txn_kind, run.name)
                typed.parent.mkdir(parents=True)
                typed_decoy = root / ".working" / store.txn_record(txn_kind, run.name)
                typed_decoy.parent.mkdir(parents=True)
                typed_decoy.write_bytes(b"state =\n")
                check("staged-root-typed-ignore-decoy-{}-{}".format(txn_kind, kind), lambda:
                      grade(run) == clean)
                projection = imp._emit_bytes(dict(
                    format="opf.journal.transaction/v1", kind=txn_kind, run_id=run.name,
                    state="complete", operation_id="synthetic-operation",
                    journal_rel=store.journal_root(txn_kind)), "typed projection")
                for label, payload in (("empty", b""), ("malformed", b"state =\n"),
                                       ("projection", projection)):
                    typed.write_bytes(payload)
                    observed = grade(run)
                    check("staged-root-typed-{}-{}-{}".format(label, txn_kind, kind), lambda:
                          refused(observed, "typed transaction evidence is not supported")
                          and all(observed[cid] == clean[cid] for cid in gate.EXPECTED_CHECKS
                                  if cid not in gate._TRANSACTION_CHECKS))
                    typed.unlink()
                typed.symlink_to(root / "absent-typed-target")
                check("staged-root-typed-symlink-{}-{}".format(txn_kind, kind), lambda:
                      refused(grade(run), "typed transaction evidence cannot be classified"))
                typed.unlink()
                os.mkfifo(typed)
                check("staged-root-typed-fifo-{}-{}".format(txn_kind, kind), lambda:
                      refused(grade(run), "typed transaction evidence cannot be classified"))
                typed.unlink()
                parent = typed.parent
                moved_typed = parent.with_name(parent.name + "-saved")
                parent.rename(moved_typed)
                parent.symlink_to(moved_typed, target_is_directory=True)
                check("staged-root-typed-parent-{}-{}".format(txn_kind, kind), lambda:
                      refused(grade(run), "typed transaction evidence cannot be classified"))
                parent.unlink()
                moved_typed.rename(parent)
                journal = root / store.journal_root(txn_kind)
                journal.mkdir(parents=True, exist_ok=True)
                (journal / "lock").write_bytes(b"writer lock")
                check("staged-root-typed-empty-journal-{}-{}".format(txn_kind, kind), lambda:
                      grade(run) == clean)
                single = journal / run.name
                single.mkdir()
                check("staged-root-typed-journal-{}-{}".format(txn_kind, kind), lambda:
                      refused(grade(run), "typed transaction evidence is not supported"))
                single.rmdir()
                # Unreadable typed controls must not collapse into the absent positive above.
                real_read = gate._read_store_control
                def denied_typed(fd, rel):
                    if rel == store.txn_record(txn_kind, run.name):
                        raise gate._GateError("typed control denied")
                    return real_read(fd, rel)
                with patch.object(gate, "_read_store_control", side_effect=denied_typed):
                    check("staged-root-typed-unreadable-{}-{}".format(txn_kind, kind), lambda:
                          refused(grade(run), "typed control denied"))
            typed.write_bytes(b"state =\n")
            record.write_bytes(b"state =\n")
            check("staged-root-typed-retains-legacy-corruption-" + kind, lambda:
                  refused(grade(run), "typed transaction evidence is not supported")
                  and "unreadable/unparseable" in grade(run)["transaction-schema"][1])
            typed.unlink()
            record.unlink()
            decoy = root / ".working" / imp._txn_record_rel(run.name)
            decoy.parent.mkdir(parents=True)
            decoy.write_bytes(b"state =\n")
            check("staged-root-ignore-decoy-" + kind, lambda:
                  grade(run) == clean)
            os.mkfifo(record)
            check("staged-root-fifo-" + kind, lambda:
                  "not a regular file" in grade(run)["transaction-schema"][1])
            record.unlink()
            original = record.parent
            moved = root / "moved-control"
            original.rename(moved)
            original.symlink_to(moved, target_is_directory=True)
            check("staged-root-symlink-control-" + kind, lambda:
                  not grade(run)["transaction-schema"][0]
                  and "no-follow" in grade(run)["transaction-schema"][1])
            original.unlink()
            moved.rename(original)
            # Deny only the physical store ascent, after _bind_run_name has succeeded.
            # The run remains readable and its staged-data checks must still grade.
            real_open, real_home = os.open, gate._physical_home
            denied_steps = []
            def denied(path, flags, *args, **kwargs):
                if path == "..":
                    denied_steps.append(path)
                    raise PermissionError("ancestor denied")
                return real_open(path, flags, *args, **kwargs)
            def denied_home(rd, rel):
                with patch.object(gate.os, "open", side_effect=denied):
                    return real_home(rd, rel)
            with patch.object(gate, "_physical_home", side_effect=denied_home):
                observed = grade(run)
            check("staged-root-unreadable-ancestor-" + kind, lambda:
                  bool(denied_steps) and refused(observed, "ancestor denied")
                  and observed["staged-run-structure"] == clean["staged-run-structure"]
                  and observed["report-schema"] == clean["report-schema"]
                  and observed["artifact-digest-integrity"] == clean["artifact-digest-integrity"])
            check("staged-root-readable-ancestor-" + kind, lambda: grade(run) == clean)
            detached = base / (kind + "-detached") / "a" / "b" / run.name
            shutil.copytree(run, detached)
            check("staged-root-detached-legacy-" + kind, lambda:
                  all(grade(detached, 1)[cid] == (True, "no transaction record (run not yet applied)")
                      for cid in gate._TRANSACTION_CHECKS))
            check("staged-root-detached-binding-discriminator-" + kind, lambda:
                  binding_refused(detached))
            check("staged-root-detached-homes2-" + kind, lambda:
                  refused(grade(detached), "no registered store binding for homes generation 2"))
            other_kind = "ingest" if kind == "import" else "import"
            crossed = root / store.stage_run(other_kind, run.name)
            crossed.parent.mkdir(parents=True)
            run.rename(crossed)
            try:
                observed = grade(crossed)
                check("staged-root-cross-kind-" + kind, lambda:
                      observed["staged-run-structure"] == (
                          False, "staging kind does not match {} run content".format(kind))
                      and observed["report-schema"] == clean["report-schema"])
            finally:
                crossed.rename(run)
            check("staged-root-matching-kind-" + kind, lambda: grade(run) == clean)
            misplaced = root / ".working" / "staging" / "preview" / run.name
            misplaced.parent.mkdir(parents=True)
            run.rename(misplaced)
            try:
                check("staged-root-kind-mismatch-" + kind, lambda:
                      refused(grade(misplaced), "no registered store binding for homes generation 2"))
            finally:
                misplaced.rename(run)
            claimant = base / (kind + "-claimant")
            claimed = claimant / store.stage_run(kind, run.name)
            claimed.parent.mkdir(parents=True)
            claimed.symlink_to(run, target_is_directory=True)
            route = claimant / "route"
            route.symlink_to(root, target_is_directory=True)
            check("staged-root-ambiguous-" + kind, lambda:
                  refused(grade(route / run.relative_to(root)), "ambiguous second store claim"))


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
    # value (a bool, a float, NaN) cannot evaluate, whatever the tooling supports and whatever the row, and so
    # does a supplied 2 on tooling that does not support homes 2.
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
    with patch.object(store, "SUPPORTED_HOMES", 1):
        check("gate-generation-unsupported-2-cannot",
              lambda: gated(journal_row, 2).startswith("cannot evaluate"))
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
    # The roster is pinned independently of the live REQUIRED_CHECKS constant: sha256 over the 29 legacy check
    # ids in report order joined by a newline, UTF-8, so an added, removed or reordered check changes it.
    legacy_roster_sha256 = "38de4ed345c62237a4f01e194a72b3d7af13d552717ba3db3363abbbacd30eee"

    def legacy_roster(rep):
        return len(rep.checks) == 29 and hashlib.sha256("\n".join(rep.checks).encode("utf-8")).hexdigest() \
            == legacy_roster_sha256
    check("doctor-legacy-roster-exact", lambda: legacy_roster(legacy_report)
          and tuple(legacy_report.checks) == doctor.REQUIRED_CHECKS and "C-EVIDENCE-ENUM" not in doctor.REQUIRED_CHECKS)
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
    check("doctor-activated-legacy-roster", lambda: legacy_roster(activated_legacy)
          and activated_legacy.residuals == legacy_report.residuals)
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
    # OPF-D2B PR3a: the acquirer identity now spans (pid, pid-start), so this foreign-acquirer
    # capability must carry a pid-start too; -1 keeps the identity foreign, yielding a clean refusal
    # rather than an unset-slot AttributeError.
    cap._acquirer_pid_start = -1
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
    _staged_generation_self_test(check)
    _staged_root_self_test(check)
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

    # OPF-D2B PR3a (decision 5): the base spec_version bump 1.1.0 -> 1.2.0 is an intended change
    # (init.toml provenance, with a tested opf upgrade route), no longer inert.
    check("inert-version", lambda: store.SUPPORTED_SPEC_VERSION == "1.2.0")
    check("inert-homes", lambda: store.SUPPORTED_HOMES == 1
          and store.homes_generation({"opf": {"homes": 2}}) == 1)
    check("inert-init", lambda: init._manifest_model()["opf"]["spec_version"] == "1.2.0"
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
    # Delete the wrapped sentence in place: normalization must not hide a lost requirement.
    body = _sections(text)["9.2"]
    mutated, removed = re.subn(r"The upgrade\s+is idempotent\.", "", body)
    check("spec-flip-9.2-idempotence", lambda: removed == 1 and
          "spec 9.2 missing contract: The upgrade is idempotent." in
          contract_findings(text.replace(body, mutated, 1)))
    # Pin every normative sentence in 9.2 and exercise each deletion independently.
    normalized = " ".join(body.replace("`", "").split())

    def replace_body(replacement):
        # Keep the body separate from both its heading and the following section.
        return text.replace(body, "\n\n" + replacement + "\n\n", 1)

    check("spec-control-9.2-undeleted", lambda:
          contract_findings(replace_body(normalized)) == [])
    def pins_cover_section():
        return " ".join(_CONTRACT["9.2"]) == normalized

    check("spec-control-9.2-pin-coverage", pins_cover_section)
    # Mutate the registry itself: per-pin deletion tests cannot notice a dropped pin.
    from unittest.mock import patch
    with patch.dict(_CONTRACT, {"9.2": tuple(
            pin for pin in _CONTRACT["9.2"]
            if pin != "Every destination is digest-verified before its source is removed.")}):
        check("spec-flip-9.2-dropped-pin", lambda: not pins_cover_section())
    for fragment in _CONTRACT["9.2"]:
        mutated = replace_body(normalized.replace(fragment, "", 1))
        check("spec-flip-9.2-" + fragment, lambda f=fragment, m=mutated:
              normalized.count(f) == 1 and
              contract_findings(m) == ["spec 9.2 missing contract: " + f])
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
