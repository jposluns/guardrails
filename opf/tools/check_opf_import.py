#!/usr/bin/env python3
"""OPF import-operation gate: structural + invariant validation of a staged import run and the scan layer.

A thin gate in the check_opf_*.py family (deliberately NOT gen_*.py: an OPF operation targets an adopter
--root with no fixed repo-relative target, so it gates as a self-test plus a NOT-APPLICABLE live leg,
exactly the posture opf.py / check_opf_doctor.py document). It owns an explicit registry of checks over a
STAGED import run (`.working/imports/<run-id>/`, the artefacts `_opf_import.plan_import` / `stage_import`
produce) AND over the read-only `scan_import` enumerator, and it fails CLOSED on any fixture or artefact it
cannot read (an unreadable input is a failure, never nothing-to-check). Verdicts are judged by the gate's
own termination status, never grepped from output.

Checks (each with a fail-without-it discriminator exercised by --self-test):
  - staged-run-structure : run.toml / plan.toml / mappings.toml / report.toml / inventory.toml /
                           IMPORT-REPORT.md present and parseable; the run-dir name matches the run-id grammar.
  - report-schema        : report.toml carries schema, the matching run_id, verdict 0, promotion_ready,
                           and an artefact list.
  - artifact-digest-integrity : every path report.toml enumerates exists under the run dir and its bytes
                                hash to the recorded sha256.
  - mapping-totality     : per source, the mapping spans tile [0, size) exactly (sorted, gap-free,
                           overlap-free, ending at the recorded byte length): nothing is dropped.
  - mapping-state-vocab  : every mapping row's state is one of the eight spec-14.1 mapping states.
  - mapping-origin-vocab : every mapping row's origin is one of the spec-14.1 provenance origins (a
                           deleted or out-of-vocabulary origin would silently bypass the acceptance gate).
  - lf-bijection         : the quarantine-state mappings correspond one-to-one to the legacy_fragment
                           records BY (source_path, span), not a bare count, so every quarantined
                           fragment is preserved exactly once and a count-preserving swap is caught.
  - lf-quad-completeness : every legacy_fragment carries the full provenance quad (source_path,
                           source_digest, span, run_id) plus the preserved body.
  - source-preservation  : every source's preserved bytes (sources/<sha256>) hash to the recorded digest,
                           so an import never rewrote or lost an original.
  - inventory-digest     : the staged inventory's recorded inventory_digest recomputes over its canonical
                           payload (determinism / integrity anchor).
  - report-binding-digests : report.toml's plan_digest recomputes over the staged plan.toml bytes and its
                           inventory_digest matches inventory.toml (the promotion-ready binding surface).
  - proposals-artifact   : proposals.toml is present, schema/run-id correct, every row stamped
                           origin=model_proposal, and IMPORT-REPORT.md is byte-reproducible from
                           inventory.toml + proposals.toml + run id.
  - acceptance-schema    : the conditionally-present acceptance.json (opf.import.acceptance/v1) is valid.
  - acceptance-binding   : acceptance.json binds the run (run id + plan_digest + inventory_digest) and
                           every decision echoes its staged mapping origin/proposed_state.
  - acceptance-attribution : acceptance.json carries a non-empty, self-asserted actor.declared.
  - acceptance-completeness : exactly one decision per plan fragment, and every model_proposal-origin
                           mapping resting in a resting state carries an explicit accept.
  - ingest-run-structure : the frozen ingest-review.toml bundle (MIG-PR4a) is present and structurally
                           well-formed (the shared _opf_import._validate_staged_ingest_bundle). Ingest markers
                           are classified through the SUPPLIED run dir's own no-follow listing, never a
                           reconstructed conventional path. Genuinely absent on an ordinary run => non-
                           applicable PASS; ingest markers present but the bundle absent, or the bundle
                           present-malformed => FINDING (read-only).
  - ingest-source-binding : the bundle's binding.plan_digest / inventory_digest equal report.toml's own
                           (never redefined), binding.inventory_toml_digest / ingest_actions_digest RECOMPUTE
                           over the staged bytes, the embedded worksheet/options are valid (reused _opf_ingest
                           validators), the frozen scoped crosswalk is RE-DERIVED-AND-EQUAL (whole rows, worksheet
                           order, type-strict) from the worksheet through plan_ingest's own builders under the
                           re-anchor base RECOVERED as a consistency binding over every crosswalk store row and
                           declared keep action (never "." by default; contradictory evidence is a located
                           cannot-evaluate), each worksheet row's sha256/size equals its staged source identity,
                           duplicate run/inventory identities are rejected before any set is built, every
                           worksheet row passes the SHARED static scope admissibility (_opf_ingest.admit_row_scope:
                           a store row under `.working/`, a declared row outside every store subtree), the options
                           correspond one-to-one to the move/migrate rows, the frozen include is admissible and
                           covers every declared-scope row under detection's own matcher, and inventory.toml is
                           RE-DERIVED-AND-EQUAL from the preserved sources/<sha> bytes (E4).
  - ingest-disposition-totality : the frozen worksheet + options pass plan_ingest's own PURE admission
                           (per-disposition option fields, the note rule, the move boundary, duplicate / self /
                           nested move destinations, and a destination equal to, beneath, or an ancestor of ANY
                           resolved worksheet source); every worksheet disposition is represented exactly once
                           (keep/move via an ingest-actions.toml action, migrate via a bundle migrate row); the
                           whole ingest-actions.toml document is RE-DERIVED-AND-EQUAL in worksheet order (E5 keep
                           unmanaged_path + sha256/size + move dest), and every migrate row equals its re-derived
                           scaffold plus ONLY the two importer-derived counts (E1 importer_kind bound to the
                           options; E6 resolved bound to the recovered base).
  - ingest-draft-loss-binding : binding.candidates_draft_digest and binding.proposals_digest RECOMPUTE over
                           the staged bytes, EVERY candidate is a closed {draft_ref, type, record, source_path,
                           importer_kind} envelope whose stamps type-check and whose importer_kind equals its
                           migrate row's (E1/E2), in the producer's shared sort_candidate_rows order, validated fail-closed and never skip-counted (E8) through the
                           real record validator, the draft references of each importer output satisfy the
                           SHARED candidate_ref_findings (non-empty, unique), every proposal is importer-origin
                           and confined to a migrate row's resolved source, each migrate row's candidate/proposal
                           counts correspond to the staged evidence, ALL proposals validate through plan_import's
                           own _validate_proposals, and the whole proposals.toml EQUALS the producer's shared
                           _proposals_model over the sorted validated rows; frozen loss validates byte tiling,
                           proposal/span and candidate/span correspondence. The importer is never re-run.
  - ingest-report-reproducibility : the staged IMPORT-REPORT.md (with its ingest section) byte-reproduces
                           from the validated model via _render_report_md + _render_ingest_review_md.
  - ingest-artefact-completeness : the staged run dir, enumerated by a fail-closed no-follow listing, is
                           reconciled against the CLOSED _INGEST_ARTEFACT_REGISTRY (an unexpected or non-regular
                           entry, or a missing registered one, is a FINDING); every registered TOML document's
                           envelope is validated type-exact before any field is consumed (an invalid one fails
                           every ingest check closed); run.toml / plan.toml / mappings.toml / the legacy_fragment
                           index / report.toml are RE-DERIVED byte-exact from the preserved source bytes and
                           run.toml's own nonce/staged_at (the run id re-derives), candidate/counters.toml is
                           VALIDATED (schema, the LF high-water equals the last minted LF id), and a registry
                           entry whose owning check passed without running its mechanism is a FINDING.
  - scan-determinism     : scan_import over the same inputs in reversed declaration order yields an equal
                           inventory digest, and an unreadable/absent declared source fails closed (exit 2).
  - transaction-schema   : the per-run apply-promotion transaction record (PR-C), when present at the store-
                           root `.aiqt/import/<run-id>/transaction.toml` (OUTSIDE .working/, so it survives
                           the terminal run-dir deletion), is well-formed: schema/format/run-id binding, a
                           known state, digest-shaped bindings, and allocation/restore_ref tables.
  - transaction-consistency : the transaction record's state machine, and that the attributed acceptance is
                           archived (`.aiqt/import-archive/<run-id>/acceptance.json`) once state >= published.
                           Both store-root control paths are read beneath a store-root descriptor opened from
                           the run-dir descriptor, no-follow on EVERY component (_read_store_control): a
                           symlinked parent, a FIFO, or any non-regular entry is a FINDING, never followed.
                           The shared journal `.aiqt/import/journal` and the archive path are classified
                           ALWAYS (whatever the record's state, or with no record); a present record must be
                           bound by its journal transaction (INTENT names this run; a complete record's bytes
                           hash to the journal-recorded create). The record's state is reconciled against the
                           journal, never self-declared: the sole producer writes only state=complete and apply
                           refuses any other state, so a prepared / published record is a FINDING, and the only
                           accepted present record is complete + terminal-COMPLETE + byte-hash-bound. The reserved
                           journal-root names lock / lock.break must be regular, singly-linked, openable files.

acceptance.json is CONDITIONALLY PRESENT: absent until a run is reviewed, so an absent acceptance.json is a
legitimate pre-review state recorded as a PASS ("not yet reviewed") for the four acceptance checks; a
present-but-unparseable/malformed acceptance.json is a FINDING (check-fails-closed); a present-and-valid one
runs the binding, attribution, and completeness recompute independently over the staged bytes (a
defence-in-depth re-derivation, not a re-run of the review tool: the acceptance record is unauthenticated).

The --self-test also DELEGATES to the operation-layer module suite (_opf_import.self_test()) and requires
it green, so the module's scan/plan/review/apply unit invariants (including the apply-promotion behaviour
checks: promoted / idempotent no-op / reject-blocked / acceptance-required / composition-gate-fail-closed,
the review acceptance-capture invariants, and plan-leaves-the-active-store-unchanged) run wherever this
CI-registered gate runs.

Disclosed coverage limits (part of the gate, not a footnote): the apply-promotion live-store cutover's deep
crash/journal/restore invariants (journaled preimage rollback, crash-recovery replay, single-writer lock
reconcile) are exercised by the _opf_import module suite (through apply_import) and by _journal's own
crash-injection harness, NOT by this staged-run gate, which validates the RESULTING transaction record shape
and state-machine consistency (Group C above); the semantic correctness of a mapping, and the AUTHENTICITY of
a named acceptance actor (actor impersonation, review-time backdating, or fabrication by any principal with
write access to the run dir) are gate-blind: the gate guards the review-to-promotion BINDING, not identity
authenticity (the reserved signature seam is the upgrade path). Likewise the Group C journal bind proves the
record is the one its journal INTENT recorded, not that the journal itself is authentic: a principal with
write access to the store can author a self-consistent INTENT + record pair. A passing gate proves nothing
about those.

This repository is not an OPFiles adopter (it has no store to import into), so even though the `opf
import` verb is now wired (OPF-IMPORT-VERB, opf.py `_cmd_import`) there is no staged import run to check
live: the live leg prints NOT APPLICABLE and exits 0, spec-honest like the doctor/drift legs in
run_all_checks.sh; the assurance rides the --self-test leg over synthetic staged runs. Offline, stdlib
only, fail-closed, launched isolated
(-I -B). The tempdir is removed in a finally (test-hermeticity).
"""
import hashlib
import os
import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # for the self-test's sibling imports below

EXIT_OK = 0
EXIT_FINDING = 1
EXIT_ERROR = 2

# The run-id grammar for the staged run DIR name. The tail anchor is \Z (end of string), NOT $ (which also
# matches just before a trailing newline): a run dir renamed with a trailing "\n" must fail staged-run-
# structure, never pass the id grammar (guard-input-soundness over an untrusted filesystem name).
_RUN_ID_RE_TEXT = r"^imp-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{16}\Z"

# The authoritative registry of staged-run checks. check_staged_run() reconciles its emitted result set
# against this (fail-closed: a declared check that did not run is recorded as a FINDING, never a silent
# omission a caller could read as a pass), and the self-test asserts the clean-run keyset equals it.
EXPECTED_CHECKS = (
    "staged-run-structure", "report-schema", "artifact-digest-integrity", "mapping-totality",
    "mapping-state-vocab", "mapping-origin-vocab", "lf-bijection", "lf-quad-completeness", "source-preservation",
    "inventory-digest", "report-binding-digests", "proposals-artifact",
    "acceptance-schema", "acceptance-binding", "acceptance-attribution", "acceptance-completeness",
    # MIG-PR4b (root-ingest review): the frozen ingest-review.toml bundle, conditionally present (absent on an
    # ordinary run => the six checks are non-applicable PASSes). These perform the binding-digest RECOMPUTE
    # deferred by MIG-PR4a and the correspondence validation, read-only; they enable no acceptance capture.
    "ingest-run-structure", "ingest-source-binding", "ingest-disposition-totality",
    "ingest-draft-loss-binding", "ingest-report-reproducibility", "ingest-artefact-completeness",
    # Group C (OPF-IMPORT-APPLY, PR-C): the per-run transaction record the promotion writes OUTSIDE .working/
    # at the store-root `.aiqt/import/<run-id>/transaction.toml`, conditionally present (absent = not yet
    # applied). transaction-schema validates its shape; transaction-consistency validates the state machine
    # and that the archived acceptance exists once the record's state reaches published (spec 14.1 / apply).
    "transaction-schema", "transaction-consistency",
    "ingest-acceptance-binding", "ingest-acceptance-completeness",
)


def _sha256_hex(data):
    return hashlib.sha256(data).hexdigest()


class _GateError(Exception):
    """A staged-run artefact the gate cannot read or parse: a fail-closed CANNOT-EVALUATE (never nothing
    to check). Carried out of a check as that check's FINDING; a harness-level read failure raises to the
    caller as EXIT_ERROR."""


def _parse_toml_bytes(data, where):
    """Parse TOML bytes already read through a regular-file-validated open, fail-closed to _GateError."""
    import tomllib
    try:
        return tomllib.loads(data.decode("utf-8"))
    except (ValueError, RecursionError) as exc:
        # RecursionError joins the tuple (R8-F1): deeply-nested TOML (a malicious staged artefact) drives
        # tomllib into unbounded recursion, which is neither an OSError nor a ValueError, so without it a
        # crafted artefact crashes the gate on the direct call path instead of failing closed to a located
        # FINDING. ValueError covers UnicodeDecodeError, TOMLDecodeError, and the bare over-long-integer
        # ValueError. MemoryError/KeyboardInterrupt/SystemExit still propagate.
        raise _GateError("required artefact unreadable/unparseable: {} ({})".format(where, exc))


def _load_toml(path):
    """Parse a required TOML artefact named by PATH (a self-test fixture; the gate itself reads run-dir
    artefacts through _RunDir and store-root control records through _read_store_control), fail-closed. The final component is opened O_NOFOLLOW | O_NONBLOCK and validated a REGULAR
    file on the opened descriptor BEFORE a byte is read (A-M2), so a FIFO, device, or symlink in its place is
    a located _GateError, never a read that blocks forever or follows a link. A missing or unparseable
    required artefact is a _GateError (never silently treated as absent / empty). Run-dir artefacts are read
    through _RunDir instead (descriptor-bound to the supplied run directory)."""
    try:
        fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        raise _GateError("required artefact absent: {}".format(path))
    except (OSError, ValueError) as exc:
        raise _GateError("required artefact unreadable: {} ({})".format(path, exc))
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise _GateError("required artefact is not a regular file: {} (fail-closed, never read)".format(path))
        chunks = []
        while True:
            block = os.read(fd, 1 << 16)
            if not block:
                break
            chunks.append(block)
    except OSError as exc:
        raise _GateError("required artefact unreadable: {} ({})".format(path, exc))
    finally:
        os.close(fd)
    return _parse_toml_bytes(b"".join(chunks), path)


def _read_store_control(store_fd, rel):
    """Round-4 F3: read one STORE-RELATIVE control path (the Group C transaction record or the archived
    acceptance) beneath the ALREADY-OPEN store-root descriptor `store_fd`, never by string-path resolution.
    Every component is walked no-follow (each intermediate O_DIRECTORY | O_NOFOLLOW beneath the previous
    descriptor, `_journal._open_parent`), the final component is classified by a no-follow lstat bound to
    that parent, and a regular file is read through the contained O_NOFOLLOW | O_NONBLOCK open whose fstat
    re-confirms S_ISREG before a byte is read (`_journal._read_contained`). Three-way: returns None ONLY on
    genuine absence (no dentry for the final component or for a parent component); returns the bytes for a
    regular file; raises _GateError (a located CANNOT-EVALUATE) for everything else, a symlinked or
    non-directory parent component (so a link to a directory elsewhere in, or outside, the store is never
    followed), a symlink, FIFO, or other non-regular final entry, an unreadable entry, or a path the
    containment walk refuses. A present-but-unreadable or non-regular path is therefore never followed and
    never read as absent."""
    import _journal
    try:
        st = _journal._lstat_contained(store_fd, rel)
    except (_journal.JournalError, OSError, ValueError, TypeError) as exc:
        raise _GateError("store control path {!r} cannot be classified no-follow beneath the store root "
                         "({}); fail-closed, never followed and never read as absent".format(rel, exc))
    if st is None:
        return None
    if not stat.S_ISREG(st.st_mode):
        raise _GateError("store control path {!r} is present but not a regular file (a symlink, FIFO, or "
                         "other entry); fail-closed, never followed or read".format(rel))
    try:
        data, _st = _journal._read_contained(store_fd, rel)
    except (_journal.JournalError, OSError, ValueError, TypeError) as exc:
        raise _GateError("store control path {!r} is present but unreadable through the contained no-follow "
                         "open ({}); fail-closed".format(rel, exc))
    return data


# Round-5 F1: the closed set of REGULAR control files the shared import journal root carries beside its
# per-transaction directories (the O_EXCL writer lock and the stale-lock arbitration inode, _journal).
_JOURNAL_ROOT_FILES = ("lock", "lock.break")


def _classify_import_journal(store_fd, journal_rel):
    """Round-5 F1: classify the shared import journal hierarchy beneath the store-root descriptor, ALWAYS
    (independent of any transaction record), through descriptor containment only. The root is lstat-ed
    no-follow beneath its contained parent (`_journal._lstat_contained`) and opened by the same O_DIRECTORY |
    O_NOFOLLOW walk the engine uses (`_journal._open_dir_contained`); every level below is enumerated and
    classified bound to its OWN opened descriptor, never a re-resolved path. Three-way: returns None ONLY on
    genuine absence; returns the open journal-root descriptor (the caller closes it) when every entry has the
    expected type; raises _GateError (a located CANNOT-EVALUATE) for everything else: a non-directory, symlinked,
    or unopenable/unlistable root; a root entry other than a transaction directory or a regular lock /
    lock.break (a reserved name is dispatched first and is never a transaction directory; it must be a
    regular, singly-linked file that opens contained no-follow, round-6 F2); a transaction directory entry other than a regular singly-linked frames.log or a preimages
    directory; a preimages entry other than a regular file."""
    import _journal

    def fail(what, exc=None):
        raise _GateError("import journal {} ({}); fail-closed, never followed and never read as "
                         "absent".format(what, exc) if exc is not None else
                         "import journal {}; fail-closed, never followed and never read as absent".format(what))

    def entries(dfd, where):
        try:
            names = sorted(os.listdir(dfd))
        except OSError as exc:
            fail("directory {!r} cannot be listed".format(where), exc)
        out = []
        for name in names:
            try:
                out.append((name, os.stat(name, dir_fd=dfd, follow_symlinks=False)))
            except OSError as exc:
                fail("entry {!r} cannot be classified no-follow".format(where + "/" + name), exc)
        return out

    def open_sub(dfd, name, where):
        try:
            return os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=dfd)
        except OSError as exc:
            fail("directory {!r} cannot be opened contained no-follow".format(where), exc)

    try:
        st = _journal._lstat_contained(store_fd, journal_rel)
    except (_journal.JournalError, OSError, ValueError, TypeError) as exc:
        fail("root {!r} cannot be classified no-follow beneath the store root".format(journal_rel), exc)
    if st is None:
        return None
    if not stat.S_ISDIR(st.st_mode):
        fail("root {!r} is present but not a directory (a symlink, FIFO, or other entry)".format(journal_rel))
    try:
        jfd = _journal._open_dir_contained(store_fd, journal_rel)
    except (_journal.JournalError, OSError, ValueError, TypeError) as exc:
        fail("root {!r} cannot be opened contained no-follow".format(journal_rel), exc)
    try:
        for name, est in entries(jfd, journal_rel):
            where = journal_rel + "/" + name
            if name in _JOURNAL_ROOT_FILES:
                # Round-6 F2: a RESERVED name is dispatched BEFORE the transaction-directory branch, so a
                # directory (or any non-regular entry) planted at `lock` / `lock.break` is never classified as
                # a transaction. It must be what the engine itself accepts: a regular, singly-linked file
                # (read_lock_owner / reconcile_and_claim_stale refuse a non-regular or hardlinked inode), and
                # READABLE through a contained no-follow non-blocking open whose fstat re-confirms the type (an
                # unopenable one, e.g. mode 000, would fail the engine's own lock read, so it is a located
                # CANNOT-EVALUATE here, never passed unopened).
                if not stat.S_ISREG(est.st_mode) or est.st_nlink != 1:
                    fail("reserved entry {!r} is not a regular singly-linked file".format(where))
                try:
                    rfd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=jfd)
                except OSError as exc:
                    fail("reserved entry {!r} cannot be opened contained no-follow".format(where), exc)
                try:
                    ost = os.fstat(rfd)
                finally:
                    os.close(rfd)
                if not stat.S_ISREG(ost.st_mode) or ost.st_nlink != 1:
                    fail("reserved entry {!r} is not a regular singly-linked file once opened".format(where))
                continue
            if not stat.S_ISDIR(est.st_mode):
                fail("entry {!r} is neither a transaction directory nor a regular lock file".format(where))
            tfd = open_sub(jfd, name, where)
            try:
                for tname, tst in entries(tfd, where):
                    twhere = where + "/" + tname
                    if tname == "frames.log" and stat.S_ISREG(tst.st_mode) and tst.st_nlink == 1:
                        continue
                    if tname != "preimages" or not stat.S_ISDIR(tst.st_mode):
                        fail("entry {!r} is not a regular singly-linked frames.log or a preimages "
                             "directory".format(twhere))
                    pfd = open_sub(tfd, tname, twhere)
                    try:
                        for pname, pst in entries(pfd, twhere):
                            if not stat.S_ISREG(pst.st_mode):
                                fail("preimage {!r} is not a regular file".format(twhere + "/" + pname))
                    finally:
                        os.close(pfd)
            finally:
                os.close(tfd)
    except BaseException:
        os.close(jfd)
        raise
    return jfd


def _journal_binds_record(jfd, txn, txn_bytes, run_id, txn_rel):
    """Round-5 F1: the journal control files RELEVANT to a present transaction record, mirroring the apply
    idempotency reconcile (_opf_import, R3-C2 / R4-C1) so the gate and the promotion cannot drift. The record's
    restore_ref must name the conventional journal and its own txn_id; that transaction's frames.log (read
    beneath the already-classified journal-root descriptor by `_journal.read_frames`) must be an accepted frame
    sequence whose INTENT binds THIS run (txn, unit, kind import-apply, plan_digest). The record's state is
    reconciled against the journal, never taken from the record (round-6 F1): the producer writes ONLY a
    complete record and apply refuses any other, so a prepared / published record is a FINDING whatever the
    journal says, and a complete record needs a terminal COMPLETE journal and bytes that hash to the create the
    INTENT recorded for this record (the create hashes the state=complete record, so no downgraded or modified
    record can match). Round-7: that create must be the ONLY INTENT op on the record path, with the producer's
    exact poststate (imp._txn_record_intent_problem). Returns '' when bound, else the located reason."""
    import _journal
    import _opf_import as imp
    txn_id = txn.get("txn_id")
    rr = txn.get("restore_ref") if isinstance(txn.get("restore_ref"), dict) else {}
    if rr.get("journal_rel") != imp.IMPORT_JOURNAL_REL or rr.get("txn_id") != txn_id:
        return "restore_ref does not name the import journal {!r} and this record's txn_id".format(
            imp.IMPORT_JOURNAL_REL)
    if not isinstance(txn_id, str) or not txn_id or "/" in txn_id or txn_id in (".", ".."):
        return "txn_id {!r} is not a single journal transaction name".format(txn_id)
    if jfd is None:
        return "the import journal is absent, so transaction {!r} is not journal-bound".format(txn_id)
    try:
        frames, torn, _gl = _journal.read_frames(jfd, txn_id)
        _journal._validate_terminal_agreement(frames)
    except (_journal.JournalError, OSError, ValueError, TypeError) as exc:
        return "cannot evaluate: journal transaction {!r} unreadable or invalid ({})".format(txn_id, exc)
    types = [t for t, _o in frames]
    if torn or _journal.F_INTENT not in types:
        return "journal transaction {!r} is absent, torn, or carries no INTENT".format(txn_id)
    intent = frames[0][1]
    hdr = intent.get("header") if isinstance(intent, dict) else None
    if not (isinstance(hdr, dict) and intent.get("txn") == txn_id and hdr.get("unit") == run_id
            and hdr.get("kind") == "import-apply" and hdr.get("plan_digest") == txn.get("plan_digest")):
        return "journal transaction {!r} INTENT does not bind this run, kind, and plan digest".format(txn_id)
    # Round-6 F1: the record's state is reconciled against the JOURNAL, never trusted from the record itself.
    # The sole producer (_opf_import._build_publication_ops) journals and writes ONLY a state=complete record,
    # inside the same transaction whose INTENT records its create; apply's idempotency reconcile refuses any
    # non-complete record (CANNOT-EVALUATE, "reconcile through recover"), so prepared / published is never a
    # resting state and a record carrying one can never hash to the journal-recorded create. It is therefore a
    # FINDING whatever the journal's state, so a record cannot downgrade itself out of the byte-hash bind below.
    jstate = "COMPLETE" if _journal.F_COMPLETE in types else (
        "ROLLBACK-COMPLETE" if _journal.F_RC in types else "open (non-terminal)")
    if txn.get("state") != "complete":
        return ("record state {!r} is not 'complete' (journal transaction {!r} is {}): the producer writes only "
                "a complete record and apply refuses a non-complete one, so it is never a resting state and its "
                "self-declared state and allocation are not trusted (fail-closed)".format(
                    txn.get("state"), txn_id, jstate))
    if _journal.F_COMPLETE not in types:
        return ("state is complete but journal transaction {!r} is {}, not terminal COMPLETE (an interrupted or "
                "rolled-back promotion; reconcile through recover)".format(txn_id, jstate))
    # Round-7 (codex round-6 MEDIUM): the INTENT ops on the record path must be EXACTLY the producer's one
    # create (kind "file", FILE_MODE, content-sha256 of the record bytes), never the first create/write match:
    # a later conflicting write/remove, a duplicate, or a create re-kinded "absent" is a journal contradicting
    # itself or the record. The SAME helper binds apply's idempotency reconcile (R4-C1), so the two cannot drift.
    problem = imp._txn_record_intent_problem(intent.get("ops"), txn_rel, txn_bytes)
    if problem:
        return "journal transaction {!r}: {}".format(txn_id, problem)
    return ""


class _RunDir:
    """A-M1/A-M2: the SUPPLIED staged run directory, opened ONCE no-follow as a directory descriptor, its
    entries CLASSIFIED FIRST by a fail-closed no-follow listing (`tree`: relpath -> "file" / "dir" / "other"),
    and every artefact then read through ONE contained, no-follow, NON-BLOCKING open beneath that descriptor
    (`_journal._read_contained`: each intermediate component O_DIRECTORY | O_NOFOLLOW, the final component
    O_NOFOLLOW | O_NONBLOCK, validated S_ISREG on the opened descriptor before a byte is read, capped). So
    a FIFO, device, socket, or symlink standing in for an artefact is a located _GateError, never a hang or
    a followed link, and every check reads the SAME directory the classification observed (never a
    reconstructed conventional path: whether the run is an ingest run is decided from THIS listing)."""

    def __init__(self, run_dir):
        self.path = Path(run_dir)
        try:
            self.fd = os.open(str(run_dir), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        except (OSError, ValueError) as exc:
            raise _GateError("cannot open the staged run dir no-follow ({})".format(exc))
        try:
            self.tree = _list_run_tree(self.fd)
        except BaseException:
            os.close(self.fd)
            raise

    def close(self):
        os.close(self.fd)

    def kind(self, rel):
        """The classified kind of a run-relative entry ("file" / "dir" / "other"), or None when absent."""
        return self.tree.get(rel)

    def read_bytes(self, rel):
        import _journal
        try:
            data, _st = _journal._read_contained(self.fd, rel)
        except (_journal.JournalError, OSError, ValueError, TypeError) as exc:
            raise _GateError("staged artefact {!r} absent, non-regular, or unreadable ({})".format(rel, exc))
        return data

    def load_toml(self, rel):
        return _parse_toml_bytes(self.read_bytes(rel), rel)


# MIG-PR4b ARTEFACT REGISTRY (R1, the class fix). The CLOSED set of staged artefacts an ingest run carries,
# each mapped to exactly ONE review mechanism, so the completeness claim ("enumerate the ARTEFACTS") is
# mechanized rather than asserted. ingest-artefact-completeness enumerates the staged run directory with a
# FAIL-CLOSED no-follow listing and reconciles it against this registry: an entry outside it is a FINDING, an
# expected entry missing is a FINDING, and a registry entry whose owning check PASSED without running its
# mechanism is a FINDING (the registry cannot silently outgrow the gate). The mechanisms:
#   - RE-DERIVE : the staged artefact must EQUAL (bytes, or a type-strict closed structure) the artefact the
#                 producer's own pure builder re-derives from the frozen inputs + the preserved source bytes.
#   - VALIDATE  : importer-derived or non-deterministic content that cannot be re-derived without re-running
#                 the importer or reading the live store; its schema/envelope/invariants are validated and it
#                 is bound to the re-derived artefacts wherever a binding exists.
#   - DIGEST-ONLY-DISCLOSED : bound by digest only, its content a disclosed residual. No artefact currently
#                 needs it (every entry below is re-derived or validated); the vocabulary stays closed.
# Each row: (run-relative name, mechanism, owning check, envelope, presence). envelope is None for a non-TOML
# artefact, else (format attribute name on _opf_import or None, carries schema, carries run_id); a field the
# producer never writes must be ABSENT (R2: every consumed document's envelope is validated, type-exact,
# before any field of it is consumed). presence: "always"; "if-sources" (only when run.toml names at least one
# source); "per-source" (one content-addressed `sources/<sha256>` per distinct run.toml source digest).
_MECH_REDERIVE = "RE-DERIVE"
_MECH_VALIDATE = "VALIDATE"
_MECH_DIGEST_ONLY = "DIGEST-ONLY-DISCLOSED"
_INGEST_MECHANISMS = (_MECH_REDERIVE, _MECH_VALIDATE, _MECH_DIGEST_ONLY)
_INGEST_ARTEFACT_REGISTRY = (
    ("run.toml", _MECH_REDERIVE, "ingest-artefact-completeness", (None, True, True), "always"),
    ("plan.toml", _MECH_REDERIVE, "ingest-artefact-completeness", (None, False, False), "always"),
    ("mappings.toml", _MECH_REDERIVE, "ingest-artefact-completeness", (None, True, False), "always"),
    ("fragments/legacy_fragment.index.toml", _MECH_REDERIVE, "ingest-artefact-completeness",
     (None, True, False), "if-sources"),
    ("candidate/counters.toml", _MECH_VALIDATE, "ingest-artefact-completeness", (None, True, False), "always"),
    ("sources/<sha256>", _MECH_VALIDATE, "ingest-artefact-completeness", None, "per-source"),
    ("report.toml", _MECH_REDERIVE, "ingest-artefact-completeness", (None, True, True), "always"),
    ("inventory.toml", _MECH_REDERIVE, "ingest-source-binding", ("INVENTORY_FORMAT", False, False), "always"),
    ("ingest-review.toml", _MECH_VALIDATE, "ingest-source-binding", ("INGEST_REVIEW_FORMAT", True, True),
     "always"),
    ("ingest-actions.toml", _MECH_REDERIVE, "ingest-disposition-totality",
     ("INGEST_ACTIONS_FORMAT", True, True), "always"),
    ("candidates_draft.toml", _MECH_VALIDATE, "ingest-draft-loss-binding", (None, True, True), "always"),
    ("proposals.toml", _MECH_VALIDATE, "ingest-draft-loss-binding", (None, True, True), "always"),
    ("IMPORT-REPORT.md", _MECH_REDERIVE, "ingest-report-reproducibility", None, "always"),
)
# Round-4 F5: the CLOSED top-level shape of every VALIDATE-mechanism staged TOML document (a RE-DERIVE
# document is already closed by its byte / whole-structure equality with the re-derivation). A field outside
# the producer's shape fails every ingest check closed, before any field of the document is consumed; the
# self-test asserts every VALIDATE-mechanism TOML registry entry has a row here, so a new one cannot slip in
# open. The ingest-review.toml sets are the shared structural validator's own (single authority).
_INGEST_DOC_KEYS = {
    "candidate/counters.toml": frozenset(("schema", "counters")),
    "candidates_draft.toml": frozenset(("schema", "run_id", "candidate")),
    "proposals.toml": frozenset(("schema", "run_id", "proposal")),
    "ingest-review.toml": None,   # resolved to _opf_import._INGEST_REVIEW_KEYS at use (the shared authority)
}
# The staged subdirectories an ingest run carries (the producer's mkdir set), reconciled like the files.
_INGEST_ARTEFACT_DIRS = (("candidate", "always"), ("sources", "always"), ("fragments", "if-sources"))
_INGEST_CHECK_IDS = ("ingest-source-binding", "ingest-disposition-totality", "ingest-draft-loss-binding",
                     "ingest-report-reproducibility", "ingest-artefact-completeness")


def _strict_eq(a, b):
    """R9 TYPE-STRICT recursive equality for every re-derived comparison: the types must match EXACTLY at
    every level (a bool is not an int, a float is not an int, so `7.0 == 7` and `True == 1` are unequal),
    tables must carry the SAME keyset (a closed structure: an extra or missing key is unequal), and arrays
    compare element-wise in order."""
    if type(a) is not type(b):
        return False
    if isinstance(a, dict):
        return a.keys() == b.keys() and all(_strict_eq(a[k], b[k]) for k in a)
    if isinstance(a, list):
        return len(a) == len(b) and all(_strict_eq(x, y) for x, y in zip(a, b))
    return a == b


def _duplicates(values):
    """The values occurring more than once, in first-repeat order (R3: rejected BEFORE any set or dict is
    built over them, so a last-wins or set reduction can never mask a drifted duplicate). The caller
    type-checks the values hashable first."""
    seen, dups = set(), []
    for v in values:
        if v in seen and v not in dups:
            dups.append(v)
        seen.add(v)
    return dups


def _list_run_tree(top):
    """R1: enumerate the staged run directory FAIL-CLOSED and no-follow, beneath the ALREADY-OPEN no-follow
    run-dir descriptor `top` (each immediate subdirectory opened O_NOFOLLOW beneath it, every entry
    classified by a no-follow stat). Returns {relpath: kind} with
    kind "file" (regular), "dir", or "other" (a symlink, FIFO, or any non-regular entry); only the immediate
    subdirectories are descended (every registered artefact lies at depth <= 1, so a deeper directory is
    itself an unexpected entry). An unopenable/unlistable/unstattable entry raises _GateError (CANNOT-
    EVALUATE), never an empty or partial listing."""
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    out = {}

    def walk(dfd, prefix, depth):
        try:
            names = os.listdir(dfd)
        except OSError as exc:
            raise _GateError("cannot list the staged run dir {!r} ({})".format(prefix or ".", exc))
        for name in sorted(names):
            rel = prefix + name
            try:
                st = os.stat(name, dir_fd=dfd, follow_symlinks=False)
            except OSError as exc:
                raise _GateError("cannot classify staged entry {!r} ({})".format(rel, exc))
            if stat.S_ISDIR(st.st_mode):
                out[rel] = "dir"
                if depth == 0:
                    try:
                        cfd = os.open(name, flags, dir_fd=dfd)
                    except OSError as exc:
                        raise _GateError("cannot open staged subdirectory {!r} no-follow ({})".format(rel, exc))
                    try:
                        walk(cfd, rel + "/", depth + 1)
                    finally:
                        os.close(cfd)
            elif stat.S_ISREG(st.st_mode):
                out[rel] = "file"
            else:
                out[rel] = "other"

    walk(top, "", 0)
    return out


def _envelope_findings(doc, env, run_id, imp):
    """R2: the envelope of one staged TOML document against its registry spec (format attribute name or
    None, carries schema, carries run_id), type-exact: format a str equal to the producer's token, schema a
    strict int (bool excluded) equal to the version, run_id a str naming this run dir; a field the producer
    never writes must be ABSENT. Returns "" when valid, else the located defect."""
    fmt_attr, has_schema, has_run_id = env
    if not isinstance(doc, dict):
        return "the document is not a table"
    if fmt_attr is not None:
        fmt = getattr(imp, fmt_attr)
        if not (isinstance(doc.get("format"), str) and doc["format"] == fmt):
            return "format is not {!r}".format(fmt)
    elif "format" in doc:
        return "carries a format key the producer never writes"
    if has_schema:
        if not (type(doc.get("schema")) is int and doc["schema"] == imp.SCHEMA):
            return "schema is not the strict integer {!r}".format(imp.SCHEMA)
    elif "schema" in doc:
        return "carries a schema key the producer never writes"
    if has_run_id:
        if not (isinstance(doc.get("run_id"), str) and doc["run_id"] == run_id):
            return "run_id is not a string naming this run dir"
    elif "run_id" in doc:
        return "carries a run_id key the producer never writes"
    return ""


def _row_scope_error(ing, ws_rows, base, homes):
    """A-M3: the first worksheet row that fails the shared static scope admissibility
    (_opf_ingest.admit_row_scope) under the recovered re-anchor `base` and the store's homes generation
    `homes`, as a located finding, else "". This gate reads no manifest, so the generation is the caller's;
    an unsupplied one (None) is the legacy generation only while no later generation can be active, and
    otherwise cannot evaluate rather than grade a store by the wrong reserved roots. A supplied generation
    other than the integer 1 or 2 (a bool, a float, NaN, or any other value), or one above the generation the
    tooling supports, cannot evaluate either."""
    import _opf_store
    if homes is None:
        if _opf_store.SUPPORTED_HOMES >= 2:
            return "cannot evaluate: the store's homes generation was not supplied to this manifest-free gate"
        homes = 1
    elif type(homes) is not int or homes not in (1, 2) or homes > _opf_store.SUPPORTED_HOMES:
        return ("cannot evaluate: the supplied homes generation {!r} is not 1 or 2, or is above the tooling's "
                "supported generation {}".format(homes, _opf_store.SUPPORTED_HOMES))
    for r in ws_rows:
        try:
            ing.admit_row_scope(r["scope"], r["source_path"], base, homes=homes)
        except ing._DetectError as exc:
            return "worksheet row {!r} is not scope-admissible ({})".format(r["source_path"], exc.message)
    return ""


def _verify_ingest_review_model(rd, bundle, run, report, inventory, homes=None):
    """MIG-PR4b RE-DERIVATION gate: prove the frozen ingest-review.toml bundle plus the staged run are a
    FAITHFUL record of what plan_ingest produced, by RE-DERIVING each DETERMINISTIC staged artefact from the
    authoritative frozen/staged inputs (the frozen worksheet + options + include + the preserved source bytes
    + run.toml's nonce/staging instant) through the producer's OWN pure builders (_opf_ingest.derive_* /
    resolve_by_scope / admit_* / validate_include_patterns / include_matches, and _opf_import._baseline_plan
    / _run_toml_model / _mappings_model / _lf_record / _run_report_model / _run_id / _build_inventory) and
    requiring the staged artefact to EQUAL the re-derivation, bytes or type-strict closed structure. The
    `run` / `report` / `inventory` arguments are the caller's parses; this gate re-reads every registered
    document itself so each is envelope-validated before use. Returns a dict of the five non-structure
    ingest checks -> (ok, detail); each fails closed on any unreadable/malformed input. READ-ONLY: it opens
    no writer, re-runs no importer, and enables no acceptance capture.

    COMPLETENESS LEDGER (mechanized by _INGEST_ARTEFACT_REGISTRY; this list matches it exactly):
      RE-DERIVE  run.toml             completeness: bytes == _run_toml_model over run.toml's own sources/nonce/
                                      staged_at, and the RUN ID re-derives (_run_id) from those + the plan.
      RE-DERIVE  plan.toml            completeness: bytes == _baseline_plan over the preserved sources.
      RE-DERIVE  mappings.toml        completeness: bytes == _mappings_model over the baseline plan.
      RE-DERIVE  fragments/legacy_fragment.index.toml  completeness: bytes == the _lf_record list (each body
                                      the preserved bytes over its span, full provenance quad); the minted
                                      ids are VALIDATED (LF-shaped, consecutive, within the counters).
      VALIDATE   candidate/counters.toml  completeness: closed {schema, counters}, validate_counters, the LF
                                      high-water equals the last minted LF id, every minted id within it.
      VALIDATE   sources/<sha256>     completeness: one per distinct run.toml digest, bytes hash to the name
                                      and match the recorded size (the preserved bytes every re-derivation
                                      rests on).
      RE-DERIVE  report.toml          completeness: bytes == _run_report_model over the staged candidate set.
      RE-DERIVE  inventory.toml       source-binding: == _build_inventory over the preserved bytes (E4).
      VALIDATE   ingest-review.toml   source-binding: the shared 4a structural validator, validate_worksheet /
                                      validate_options, every row scope-admissible (admit_row_scope, A-M3),
                                      the crosswalk RE-DERIVED whole (derive_crosswalk_row, ordered, closed
                                      rows), include (G1) and binding digests recomputed.
      RE-DERIVE  ingest-actions.toml  disposition-totality: the whole document == the actions re-derived in
                                      worksheet order (admit_keep_unmanaged / derive_keep_action /
                                      derive_move_action), after the PURE admissibility (admit_row_binding /
                                      admit_move_boundary / admit_move_dest) of the frozen worksheet + options;
                                      the migrate rows == derive_migrate_scaffold plus ONLY the two
                                      importer-derived counts (validated in draft-loss).
      VALIDATE   candidates_draft.toml  draft-loss: closed document + envelope per candidate, the real record
                                      validator (a present `id` refused), the producer's candidate order,
                                      the shared candidate_ref_findings per importer output (R5), the
                                      importer_kind stamp, and the per-migrate-row counts.
      VALIDATE   proposals.toml       draft-loss: every row importer_proposal-origin (plan_ingest emits no
                                      model proposal), confined to a MIGRATE resolved source, ALL rows through
                                      _validate_proposals, the whole document == _proposals_model over the
                                      sorted validated rows (A-m4), and the per-migrate-row counts (R4).
      RE-DERIVE  IMPORT-REPORT.md     report-reproducibility: bytes == _render_report_md +
                                      _render_ingest_review_md over the validated model.
    Plus the staged subdirectories candidate/, sources/, fragments/ (the last only with sources). Anything
    else in the run dir (an unexpected file, a symlink, a stray acceptance.json: ingest review capture is
    refused) is a FINDING; a registered entry absent is a FINDING.

    RE-ANCHOR BASE (R7), a CONSISTENCY binding, never authenticity: the gate has no live store (it is
    check_staged_run(run_dir); the product tree may be gone), so the store-under-product base is recovered
    from ALL the frozen evidence that can observe it: each crosswalk STORE row (resolved = `<base>/<source>`)
    and each DECLARED KEEP action (unmanaged_path a normalized, non-escaping trailing path of its source, so
    source = `<base>/<unmanaged>`). Every implied base must be IDENTICAL; contradictory evidence is a located
    CANNOT-EVALUATE. With NO such evidence the base is UNOBSERVABLE and is never equated with "." (it is
    None): no derivation then depends on it (a declared row resolves by identity, a store keep is verbatim),
    and the relocated-store working-tree arm of the move boundary and of the include check falls back to the
    literal product-root `.working` only (disclosed).

    Frozen loss entries now drive byte tiling, physical line ranges, proposal/span bijection, and candidate
    references through validate_importer_output over staged source bytes. This proves internal consistency,
    not that the named importer produced the frozen result.

    Residual boundaries (authenticity vs consistency; disclose-guard-residuals): even the full re-derivation
    proves CONSISTENCY and FAITHFULNESS-TO-THE-DETERMINISTIC-PRODUCER, never AUTHENTICITY. It cannot prove the
    frozen worksheet or include set described the real product tree at plan time (there is no fresh detect at
    review time; the gate binds the worksheet to the STAGED bytes and each declared row to the frozen include
    under the detection matcher, not to a live tree), nor that the frozen candidates/proposals are what THAT
    importer would emit from the source bytes (the importer is deliberately never re-run; bounded by the run
    being NON-PROMOTING, the reserved signature seam is the path), nor that the recovered base is the store's
    real location (a consistency binding over the frozen evidence), nor that counters.toml's NON-LF high-
    water marks reflect the live store at plan time (they are read from the store, not derivable), nor
    actor/backdating/run-dir authenticity (the gate guards the review-to-promotion BINDING, not identity; same
    posture as the module docstring's disclosed coverage limits). The 4a structural reader already proved the
    bundle's shape; every staged-artefact read here is still guarded and fails closed."""
    import datetime
    import posixpath

    import _opf_import as imp
    import _opf_ingest as ing
    import _opf_importers as importers
    import _opf_schema
    import _opf_store

    run_dir = rd.path
    out = {}
    covered = set()   # registry entries whose mechanism actually ran to success (reconciled at the end)
    binding = bundle["binding"]
    run_id = run_dir.name
    crosswalk = bundle["crosswalk"]
    ws_rows = bundle["worksheet"]["row"]
    frag_name = "fragments/legacy_fragment.index.toml"

    def _staged_digest(name):
        return "sha256:" + _sha256_hex(rd.read_bytes(name))

    # ---- R1/R2: read every registered staged TOML document ONCE and validate its ENVELOPE before any field
    #      of it is consumed. An unreadable/unparseable document or an invalid envelope fails EVERY ingest check
    #      closed (each of them consumes run.toml or the documents it binds), never a partial evaluation. ----
    run_srcs_raw = run.get("source") if isinstance(run, dict) else None
    has_sources = isinstance(run_srcs_raw, list) and len(run_srcs_raw) > 0
    docs = {}
    pre_err = ""
    for name, _mech, _owner, env, presence in _INGEST_ARTEFACT_REGISTRY:
        if env is None or (presence == "if-sources" and not has_sources):
            continue
        try:
            doc = rd.load_toml(name)
        except _GateError as exc:
            pre_err = "cannot evaluate: {}".format(exc)
            break
        env_err = _envelope_findings(doc, env, run_id, imp)
        if env_err:
            pre_err = ("{} envelope invalid ({}); no field of it is consumed (fail-closed)".format(name, env_err))
            break
        if name in _INGEST_DOC_KEYS:
            closed = _INGEST_DOC_KEYS[name] or imp._INGEST_REVIEW_KEYS
            unknown = set(doc) - closed
            if unknown:
                pre_err = ("{} carries field(s) outside the producer's closed document shape {}; no field of it "
                           "is consumed (fail-closed)".format(name, sorted(str(k) for k in unknown)))
                break
        docs[name] = doc
    if pre_err:
        for cid in _INGEST_CHECK_IDS:
            out[cid] = (False, pre_err)
        return out
    run = docs["run.toml"]
    report = docs["report.toml"]
    inventory = docs[imp.INVENTORY_NAME]
    actions_doc = docs[imp.INGEST_ACTIONS_NAME]

    # ---- R3: run.toml source rows validated CLOSED {path, sha256, size} and duplicate-free ONCE, before any
    #      set or dict is built over them (the producer refuses a duplicate source path). ----
    run_src_err = ""
    run_srcs = run.get("source")
    if not isinstance(run_srcs, list):
        run_src_err = "run.toml source list malformed"
    else:
        for s in run_srcs:
            if not (isinstance(s, dict) and set(s) == {"path", "sha256", "size"}
                    and isinstance(s["path"], str) and isinstance(s["sha256"], str)
                    and imp._HEX64_RE.match(s["sha256"]) and type(s["size"]) is int and s["size"] >= 0):
                run_src_err = "run.toml carries a malformed source row {!r}".format(s)
                break
        if not run_src_err and _duplicates([s["path"] for s in run_srcs]):
            run_src_err = ("run.toml carries a duplicate source path {} (rejected before any set or dict is "
                           "built over the rows)".format(_duplicates([s["path"] for s in run_srcs])))

    def _base_ok(b):
        """A recovered base is "." or a normalized, relative, non-escaping POSIX path."""
        return isinstance(b, str) and (b == "." or (posixpath.normpath(b) == b and not b.startswith("/")
                                                   and ".." not in b.split("/")))

    def _recover_base():
        """R7: recover the store-under-product re-anchor base as a CONSISTENCY binding over ALL the frozen
        evidence that observes it (crosswalk STORE rows and DECLARED KEEP actions; see the docstring). Returns
        (base, err): base None when no evidence observes it (UNOBSERVABLE, never "."); err a located
        CANNOT-EVALUATE when the evidence is malformed or contradictory."""
        implied = []
        for c in crosswalk:
            if not isinstance(c, dict):
                return None, "cannot evaluate: a crosswalk row is not a table (cannot recover the re-anchor base)"
            if c.get("scope") != "store":
                continue
            sp = c.get("source_path"); rsp = c.get("resolved_source_path")
            if not (isinstance(sp, str) and isinstance(rsp, str)):
                return None, "cannot evaluate: a crosswalk store row carries a non-string source/resolved path"
            if rsp == sp:
                b = "."
            elif rsp.endswith("/" + sp):
                b = rsp[:-(len(sp) + 1)]
            else:
                return None, ("cannot evaluate: crosswalk store row {!r} resolved {!r} is not a uniform "
                              "re-anchoring of its source path".format(sp, rsp))
            implied.append(("crosswalk store row {!r}".format(sp), b))
        acts = actions_doc.get("action")
        if not (isinstance(acts, list) and all(isinstance(a, dict) for a in acts)):
            return None, ("cannot evaluate: ingest-actions.toml action list malformed (cannot recover the "
                          "re-anchor base)")
        keep_acts = {}
        for a in acts:
            if a.get("kind") != "keep" or a.get("scope") != "declared":
                continue
            k = a.get("source_path")
            if not isinstance(k, str) or k in keep_acts:
                return None, ("cannot evaluate: a declared keep action is malformed or duplicated (cannot "
                              "recover the re-anchor base)")
            keep_acts[k] = a
        for r in ws_rows:
            if not (isinstance(r, dict) and r.get("scope") == "declared" and r.get("disposition") == "keep"):
                continue
            sp = r.get("source_path")
            a = keep_acts.get(sp) if isinstance(sp, str) else None
            if a is None:
                continue   # a missing keep action is disposition-totality's FINDING, not base evidence
            up = a.get("unmanaged_path")
            if not (isinstance(up, str) and up != "." and _base_ok(up)):
                return None, ("cannot evaluate: declared keep {!r} unmanaged_path {!r} is not a normalized, "
                              "non-escaping store-relative path".format(sp, up))
            if up == sp:
                b = "."
            elif sp.endswith("/" + up):
                b = sp[:-(len(up) + 1)]
            else:
                return None, ("cannot evaluate: declared keep {!r} unmanaged_path {!r} is not a trailing path "
                              "of its source (no re-anchor base maps one onto the other)".format(sp, up))
            implied.append(("declared keep {!r}".format(sp), b))
        for label, b in implied:
            if not _base_ok(b):
                return None, ("cannot evaluate: {} implies a non-normalized or escaping re-anchor base "
                              "{!r}".format(label, b))
        bases = sorted({b for _label, b in implied})
        if len(bases) > 1:
            return None, ("cannot evaluate: the frozen evidence implies more than one re-anchor base {} "
                          "(contradictory crosswalk store rows / declared keep actions)".format(bases))
        return (bases[0] if bases else None), ""

    # base is recovered once and reused by the crosswalk, include, action and migrate re-derivations; a
    # recovery failure fails the base-dependent arms closed.
    base, base_err = _recover_base()
    store_working_rel = (None if base is None
                         else posixpath.normpath(posixpath.join(base, _opf_store.WORKING_DIRNAME)))

    # ---- ingest-source-binding: bound-digest recompute + worksheet/options validity + crosswalk RE-DERIVE-
    #      AND-EQUAL + include (G1) + inventory RE-DERIVE-AND-EQUAL + options/identity correspondence ----
    ok, detail = True, ""
    try:
        inv_srcs = inventory.get("source")
        if binding["plan_digest"] != report.get("plan_digest"):
            ok, detail = False, "binding.plan_digest does not equal report.toml plan_digest (redefined binding)"
        elif binding["inventory_digest"] != report.get("inventory_digest"):
            ok, detail = False, "binding.inventory_digest does not equal report.toml inventory_digest"
        elif binding["inventory_toml_digest"] != _staged_digest("inventory.toml"):
            ok, detail = False, "binding.inventory_toml_digest does not recompute over inventory.toml bytes"
        # FIX 1a: binding.ingest_actions_digest recompute (MIG-PR4a bound it but deferred the recompute).
        elif binding["ingest_actions_digest"] != _staged_digest(imp.INGEST_ACTIONS_NAME):
            ok, detail = False, ("binding.ingest_actions_digest does not recompute over ingest-actions.toml "
                                 "bytes")
        else:
            wf = ing.validate_worksheet(bundle["worksheet"])
            of = ing.validate_options(bundle["options"])
            if wf:
                ok, detail = False, "embedded worksheet invalid: " + "; ".join(wf)
            elif of:
                ok, detail = False, "embedded options invalid: " + "; ".join(of)
            elif run_src_err:
                ok, detail = False, run_src_err
            elif not (isinstance(inv_srcs, list)
                      and all(isinstance(s, dict) and isinstance(s.get("path"), str) for s in inv_srcs)):
                ok, detail = False, "inventory.toml source list malformed (cannot bind crosswalk)"
            elif _duplicates([s["path"] for s in inv_srcs]):
                # R3: a duplicate inventory identity is rejected BEFORE the set below is built over it.
                ok, detail = False, ("inventory.toml carries a duplicate source path {} (rejected before any set "
                                     "is built)".format(_duplicates([s["path"] for s in inv_srcs])))
            elif base_err:
                ok, detail = False, base_err
            elif _duplicates([(r["scope"], r["source_path"]) for r in ws_rows]):
                ok, detail = False, "worksheet carries a duplicate (scope, source_path) row"
            elif (scope_err := _row_scope_error(ing, ws_rows, base, homes)):
                # A-M3 (STATIC SCOPE ADMISSIBILITY): a row re-scoped across the store / product boundary
                # re-derives FAITHFULLY (the crosswalk, keep action and resolved path all follow the forged
                # scope), so equality alone cannot refuse it; the shared admit_row_scope applies the scope
                # boundaries detection enforces, decided from the frozen path and the recovered base.
                ok, detail = False, scope_err
            else:
                # CROSSWALK RE-DERIVE-AND-EQUAL (E1 disposition / E6 resolved / E8 dup / R10 whole row):
                # re-derive EVERY crosswalk row, in worksheet order, through the SAME derive_crosswalk_row +
                # resolve_by_scope the planner used and require the frozen crosswalk to EQUAL the list,
                # type-strict and closed (an extra key, a reordered, duplicated, missing or drifted row is
                # unequal), so no separate per-field predicate can drift from the producer.
                expected_cw = [ing.derive_crosswalk_row(
                    r, ing.resolve_by_scope(base, r["scope"], r["source_path"])) for r in ws_rows]
                if not _strict_eq(crosswalk, expected_cw):
                    ok, detail = False, ("frozen crosswalk does not equal the crosswalk re-derived from the "
                                         "worksheet (scope/source/resolved/disposition, a closed row keyset, "
                                         "worksheet order, or a duplicate/missing/extra row)")
                else:
                    resolved = [c["resolved_source_path"] for c in crosswalk]
                    run_set = {s["path"] for s in run_srcs}
                    inv_set = {s["path"] for s in inv_srcs}
                    # C2: two DISTINCT (scope, source_path) keys resolving to ONE product path (e.g. a store row
                    # and a declared row naming the same file under an inline store) survive the crosswalk
                    # equality above (each row re-derives faithfully) and the set equalities below (a set
                    # cannot see multiplicity), so this multiplicity guard is the one that catches them. It
                    # intentionally OVERLAPS the producer's refusal (plan_ingest refuses two rows resolving to
                    # one path) and the run.toml duplicate guard (R3), as defence in depth.
                    if _duplicates(resolved):
                        ok, detail = False, ("crosswalk binds two rows to one resolved source (duplicate "
                                             "resolved_source_path)")
                    elif set(resolved) != run_set:
                        ok, detail = False, "crosswalk resolved sources do not equal run.toml source identities"
                    elif set(resolved) != inv_set:
                        ok, detail = False, ("crosswalk resolved sources do not equal inventory.toml source "
                                             "identities")
                    else:
                        # FIX 2 (per-source identity vs staged source): each worksheet row's frozen sha256/size
                        # must EQUAL, type-strict, the identity of the staged source it resolves to through the
                        # crosswalk. The staged run.toml sha256 is bare hex, prefixed to the worksheet's
                        # "sha256:"+hex grammar.
                        cw_resolved = {(c["scope"], c["source_path"]): c["resolved_source_path"]
                                       for c in crosswalk}
                        run_by_path = {s["path"]: s for s in run_srcs}
                        for wr in ws_rows:
                            staged = run_by_path.get(cw_resolved.get((wr["scope"], wr["source_path"])))
                            if staged is None:
                                ok, detail = False, ("worksheet row {!r} does not resolve to a staged source "
                                                     "identity".format(wr["source_path"]))
                                break
                            if not (_strict_eq("sha256:" + staged["sha256"], wr["sha256"])
                                    and _strict_eq(staged["size"], wr["size"])):
                                ok, detail = False, ("worksheet row {!r} sha256/size does not equal the staged "
                                                     "source identity".format(wr["source_path"]))
                                break
                        # FIX 3 (options<->disposition + importer_kind registry): (a) the options (scope,
                        # source_path) set equals the move/migrate worksheet-row key set; (b) each frozen migrate
                        # importer_kind is a REGISTERED importer (validate_options already checks the options
                        # doc's kinds, and the migrate scaffold re-derive binds migrate.importer_kind to the
                        # options; this registry arm is the defence-in-depth sibling kept in this check).
                        ws_disp = {(r["scope"], r["source_path"]): r["disposition"] for r in ws_rows}
                        if ok:
                            opt_rows = bundle["options"]["option"]
                            opt_keys = [(o["scope"], o["source_path"]) for o in opt_rows]
                            need_opt = {k for k, d in ws_disp.items() if d in ("move", "migrate")}
                            if _duplicates(opt_keys):
                                ok, detail = False, ("embedded options carry a duplicate (scope, source_path) "
                                                     "row")
                            elif set(opt_keys) != need_opt:
                                ok, detail = False, ("embedded options do not correspond one-to-one to the "
                                                     "move/migrate worksheet rows")
                            else:
                                for m in bundle["migrate"]:
                                    if m.get("importer_kind") not in importers.IMPORTER_KINDS:
                                        ok, detail = False, ("migrate row {!r} names an unregistered "
                                                             "importer_kind {!r}".format(
                                                                 m.get("source_path"), m.get("importer_kind")))
                                        break
                        # G1 (the frozen read-space): include_declared must equal (include is not None) as the
                        # writer froze it (an undeclared include is frozen as []), each declared pattern must be
                        # admissible under detection's own validate_include_patterns, and every DECLARED-scope
                        # worksheet row must fall inside the include set under detection's own include_matches
                        # (a declared row needs a declared include; store scope is mandatory and include-free).
                        # Whether the include set described the live tree is a disclosed authenticity residual.
                        if ok:
                            inc = bundle["include"]
                            declared_rows = [r for r in ws_rows if r["scope"] == "declared"]
                            if not bundle["include_declared"]:
                                if inc != []:
                                    ok, detail = False, ("bundle include_declared is false but include is "
                                                         "non-empty (include_declared must equal include is "
                                                         "not None)")
                                elif declared_rows:
                                    ok, detail = False, ("declared-scope worksheet row {!r} but no --include was "
                                                         "declared (declared scope requires a declared "
                                                         "include)".format(declared_rows[0]["source_path"]))
                            else:
                                try:
                                    ing.validate_include_patterns(inc, ing.include_scope_prefixes(
                                        store_working_rel))
                                except ing._DetectError as exc:
                                    ok, detail = False, "frozen include is not admissible: {}".format(exc.message)
                                if ok:
                                    for r in declared_rows:
                                        if not any(ing.include_matches(p, r["source_path"]) for p in inc):
                                            ok, detail = False, ("declared-scope worksheet row {!r} falls "
                                                                 "outside the frozen include set".format(
                                                                     r["source_path"]))
                                            break
                        # E4 (inventory RE-DERIVE-AND-EQUAL): re-run _build_inventory over the PRESERVED
                        # `sources/<sha>` bytes (run.toml path -> its content-addressed body) and require the
                        # frozen inventory.toml to EQUAL it, type-strict, binding every inventory identity
                        # (source size/sha256, fragment span/id/digest, inventory_digest) to the bytes preserved.
                        if ok:
                            rebuilt = []
                            for s in run_srcs:
                                body = rd.read_bytes("sources/" + s["sha256"])
                                rebuilt.append({"path": s["path"], "sha256": _sha256_hex(body), "size": len(body)})
                            if not _strict_eq(imp._build_inventory(rebuilt)[0], inventory):
                                ok, detail = False, ("inventory.toml does not equal the inventory re-derived "
                                                     "from the preserved source bytes (identity/fragment "
                                                     "mismatch)")
    except (_GateError, imp._StageError, OSError, ValueError, RecursionError, KeyError, TypeError,
            IndexError) as exc:
        ok, detail = False, "ingest-source-binding failed closed ({!r})".format(exc)
    if ok:
        covered.update({"inventory.toml", "ingest-review.toml"})
    out["ingest-source-binding"] = (ok, detail)

    # ---- ingest-disposition-totality: PURE admissibility of the frozen worksheet + options (R6), every
    #      disposition represented once, and the actions document + migrate scaffold RE-DERIVED-AND-EQUAL ----
    ok, detail = True, ""
    try:
        # E8: duplicate rejection BEFORE building the disposition map (a last-wins reduction must not mask a
        # drifted duplicate row).
        ws_keys = [(r.get("scope"), r.get("source_path")) for r in ws_rows]
        opt_rows = bundle["options"]["option"]
        opt_keys = [(o.get("scope"), o.get("source_path")) for o in opt_rows]
        if _duplicates(ws_keys):
            ok, detail = False, "worksheet carries a duplicate (scope, source_path) row"
        elif _duplicates(opt_keys):
            ok, detail = False, "embedded options carry a duplicate (scope, source_path) row"
        elif base_err:
            ok, detail = False, base_err
        else:
            ws_disp = {(r["scope"], r["source_path"]): r["disposition"] for r in ws_rows}
            ws_by_key = {(r["scope"], r["source_path"]): r for r in ws_rows}
            unexpected = sorted({d for d in ws_disp.values() if d not in ("keep", "move", "migrate")})
            opt_by_key = dict(zip(opt_keys, opt_rows))
            if unexpected:
                ok, detail = False, "worksheet carries a non-stageable disposition {}".format(unexpected)
            else:
                # R6 (ADMISSIBILITY): equality with a constructor does not prove the frozen input was one the
                # planner would accept, so the frozen worksheet + options pass plan_ingest's OWN pure admission
                # (admit_row_binding: per-disposition option fields + the note rule; admit_move_boundary +
                # admit_move_dest: the pure move boundary, duplicate destination, move-to-self and nesting), in
                # worksheet order. The live no-overwrite lstat is plan-time only (no live tree at review).
                move_dests = {}
                # round-4 F2: every move destination is admitted against the COMPLETE resolved source set of the
                # frozen worksheet (the planner's own set, re-derived through the same resolve_by_scope under the
                # recovered base), so a destination equal to, beneath, or an ancestor of ANY source is refused at
                # parity with plan_ingest's live no-overwrite refusal.
                all_sources = {p for p in (ing.resolve_by_scope(base, r["scope"], r["source_path"])
                                           for r in ws_rows) if p is not None}
                for r in ws_rows:
                    try:
                        opt = opt_by_key.get((r["scope"], r["source_path"]))
                        ing.admit_row_binding(r, opt)
                        if r["disposition"] == "move":
                            dest = ing.derive_move_dest(opt, r["source_path"])
                            ing.admit_move_boundary(dest, store_working_rel)
                            ing.admit_move_dest(r["source_path"], dest, move_dests, all_sources)
                    except ing._DetectError as exc:
                        ok, detail = False, ("worksheet row {!r} is not admissible under plan_ingest's own "
                                             "admission ({})".format(r["source_path"], exc.message))
                        break
            if ok:
                keep_move_keys = {k for k, d in ws_disp.items() if d in ("keep", "move")}
                migrate_keys = {k for k, d in ws_disp.items() if d == "migrate"}
                actions_list = actions_doc.get("action")
                if not isinstance(actions_list, list):
                    ok, detail = False, "ingest-actions.toml action list malformed"
                else:
                    act_keys = [(a.get("scope"), a.get("source_path")) for a in actions_list
                                if isinstance(a, dict)]
                    if len(act_keys) != len(actions_list):
                        ok, detail = False, "an ingest-actions.toml action is not a table"
                    elif _duplicates(act_keys):
                        ok, detail = False, ("ingest-actions.toml carries a duplicate (scope, source_path) "
                                             "action")
                    elif set(act_keys) != keep_move_keys:
                        ok, detail = False, ("ingest-actions.toml actions do not correspond one-to-one to the "
                                             "keep/move worksheet rows")
                    else:
                        # E5 (ACTION RE-DERIVE-AND-EQUAL): re-derive each keep/move action, in worksheet order,
                        # through the SAME builders (keep -> admit_keep_unmanaged + derive_keep_action; move ->
                        # derive_move_action with derive_move_dest) and require each frozen action, then the
                        # WHOLE document (envelope + action order), to EQUAL it type-strict (R9/R10).
                        frozen_by_key = dict(zip(act_keys, actions_list))
                        expected_actions = []
                        for r in ws_rows:
                            key = (r["scope"], r["source_path"])
                            if ws_disp[key] == "keep":
                                try:
                                    unmanaged = ing.admit_keep_unmanaged(base, r["scope"], r["source_path"])
                                except ing._DetectError as exc:
                                    ok, detail = False, ("keep row {!r} has no admissible [unmanaged].paths "
                                                         "spelling under the recovered base ({})".format(
                                                             r["source_path"], exc.message))
                                    break
                                expected_act = ing.derive_keep_action(r, unmanaged)
                            elif ws_disp[key] == "move":
                                expected_act = ing.derive_move_action(
                                    r, ing.derive_move_dest(opt_by_key[key], r["source_path"]))
                            else:
                                continue
                            if not _strict_eq(frozen_by_key[key], expected_act):
                                ok, detail = False, ("ingest-actions action for {!r} does not equal the action "
                                                     "re-derived from the worksheet/options".format(
                                                         r["source_path"]))
                                break
                            expected_actions.append(expected_act)
                        if ok and not _strict_eq(actions_doc, {
                                "format": imp.INGEST_ACTIONS_FORMAT, "schema": imp.SCHEMA, "run_id": run_id,
                                "action": expected_actions}):
                            ok, detail = False, ("ingest-actions.toml does not equal the document re-derived "
                                                 "from the worksheet/options (action order or an extra key)")
                        if ok:
                            mig_keys = [(m["scope"], m["source_path"]) for m in bundle["migrate"]]
                            ws_mig_order = [(r["scope"], r["source_path"]) for r in ws_rows
                                            if r["disposition"] == "migrate"]
                            if _duplicates(mig_keys):
                                ok, detail = False, ("bundle migrate list carries a duplicate (scope, "
                                                     "source_path) row")
                            elif set(mig_keys) != migrate_keys:
                                ok, detail = False, ("bundle migrate rows do not correspond one-to-one to the "
                                                     "migrate worksheet rows")
                            elif mig_keys != ws_mig_order:
                                ok, detail = False, ("bundle migrate rows are not in the worksheet order the "
                                                     "planner records them in")
                            else:
                                # MIGRATE SCAFFOLD RE-DERIVE-AND-EQUAL (E1 importer_kind bound to OPTIONS; E6
                                # resolved bound to the recovered base; R10 whole row): each frozen migrate row
                                # carries EXACTLY the scaffold keys plus the two importer-derived counts, and
                                # with ONLY those two removed it EQUALS the re-derived scaffold type-strict. The
                                # counts are validated separately (ingest-draft-loss-binding).
                                for m in bundle["migrate"]:
                                    key = (m["scope"], m["source_path"])
                                    r = ws_by_key[key]
                                    expected_scaffold = ing.derive_migrate_scaffold(
                                        r, ing.resolve_by_scope(base, r["scope"], r["source_path"]),
                                        opt_by_key[key].get("importer_kind"))
                                    counts = {"candidate_count", "proposal_count", "loss"}
                                    if set(m) != set(expected_scaffold) | counts:
                                        ok, detail = False, ("migrate row {!r} is not the closed scaffold + "
                                                             "counts keyset".format(r["source_path"]))
                                        break
                                    projected = {k: v for k, v in m.items() if k not in counts}
                                    if not _strict_eq(projected, expected_scaffold):
                                        ok, detail = False, ("migrate row {!r} does not equal the scaffold "
                                                             "re-derived from the worksheet/options "
                                                             "(resolved/importer_kind)".format(r["source_path"]))
                                        break
    except (_GateError, imp._StageError, OSError, ValueError, RecursionError, KeyError, TypeError,
            IndexError, AttributeError) as exc:
        ok, detail = False, "ingest-disposition-totality failed closed ({!r})".format(exc)
    if ok:
        covered.add(imp.INGEST_ACTIONS_NAME)
    out["ingest-disposition-totality"] = (ok, detail)

    # ---- ingest-draft-loss-binding: draft/proposal digests + candidate typing (E2) + fail-closed (E8) +
    #      importer_kind stamp (E1) + shared draft-ref invariants (R5) + proposal origin/confinement (R4) +
    #      counts (importer never re-run) ----
    ok, detail = True, ""
    try:
        if binding["candidates_draft_digest"] != _staged_digest(imp.CANDIDATES_DRAFT_NAME):
            ok, detail = False, ("binding.candidates_draft_digest does not recompute over "
                                 "candidates_draft.toml bytes")
        elif binding["proposals_digest"] != _staged_digest(imp.PROPOSALS_NAME):
            ok, detail = False, "binding.proposals_digest does not recompute over proposals.toml bytes"
        elif run_src_err:
            ok, detail = False, run_src_err
        else:
            cands = docs[imp.CANDIDATES_DRAFT_NAME].get("candidate")
            props = docs[imp.PROPOSALS_NAME].get("proposal")
            mig_sps = [m["source_path"] for m in bundle["migrate"]]
            if not (isinstance(cands, list) and isinstance(props, list)):
                ok, detail = False, "candidates_draft.toml/proposals.toml list malformed"
            elif _duplicates(mig_sps):
                ok, detail = False, "bundle migrate list carries a duplicate source_path"
            else:
                mig_by_sp = {m["source_path"]: m for m in bundle["migrate"]}
                # E2/E8 (candidate ENVELOPE typing, FAIL-CLOSED over EVERY candidate): a staged candidate is a
                # CLOSED {draft_ref, type, record, source_path, importer_kind} keyset; draft_ref/source_path are
                # strings; source_path names a migrate row; importer_kind is a string EQUAL to that migrate row's
                # (E1 stamp). Any non-conforming candidate is a FINDING, never skipped/skip-counted (the prior
                # gate silently dropped a malformed candidate from cand_by_sp). Then the {draft_ref, type,
                # record} envelope is validated through the real record validator (FIX 4a).
                cand_by_sp = {}
                refs_by_sp = {}
                for i, c in enumerate(cands):
                    if not (isinstance(c, dict)
                            and set(c) == {"draft_ref", "type", "record", "source_path", "importer_kind"}):
                        ok, detail = False, ("candidates_draft.toml candidate[{}] is not a closed "
                                             "{{draft_ref, type, record, source_path, importer_kind}} "
                                             "envelope".format(i))
                        break
                    if not isinstance(c["draft_ref"], str):
                        ok, detail = False, "candidates_draft.toml candidate[{}] draft_ref is not a string".format(i)
                        break
                    # C3: this str guard is SUBSUMED by the migrate-row lookup just below (a non-string
                    # source_path names no migrate row); it is kept deliberately so the located type finding
                    # precedes the dict membership (an unhashable value there would otherwise raise TypeError).
                    if not isinstance(c["source_path"], str):
                        ok, detail = False, ("candidates_draft.toml candidate[{}] source_path is not a "
                                             "string".format(i))
                        break
                    m = mig_by_sp.get(c["source_path"])
                    if m is None:
                        ok, detail = False, ("candidates_draft.toml candidate[{}] source_path {!r} names no "
                                             "migrate row".format(i, c["source_path"]))
                        break
                    if not (isinstance(c["importer_kind"], str) and c["importer_kind"] == m["importer_kind"]):
                        ok, detail = False, ("candidates_draft.toml candidate[{}] importer_kind does not equal "
                                             "its migrate row's importer_kind (E1 stamp)".format(i))
                        break
                    envp = {k: c[k] for k in ("draft_ref", "type", "record")}
                    rv = importers._validate_candidate(envp, _opf_schema.validate_record)
                    if rv.status != imp._opf_store.VALID:
                        ok, detail = False, ("candidates_draft.toml candidate[{}] is not a valid record "
                                             "envelope ({})".format(i, "; ".join(rv.findings)))
                        break
                    cand_by_sp[c["source_path"]] = cand_by_sp.get(c["source_path"], 0) + 1
                    refs_by_sp.setdefault(c["source_path"], []).append(c["draft_ref"])
                if ok:
                    # R5: the candidate-reference invariants (non-empty, unique) per importer output (one
                    # migrate source), through the SAME candidate_ref_findings validate_importer_output applies.
                    for sp in sorted(refs_by_sp):
                        rf = importers.candidate_ref_findings(refs_by_sp[sp])
                        if rf:
                            ok, detail = False, ("candidates_draft.toml drafts for {!r} violate the candidate "
                                                 "draft-ref invariants: {}".format(sp, "; ".join(rf)))
                            break
                if ok and not _strict_eq(cands, ing.sort_candidate_rows(cands, bundle["migrate"])):
                    # round-4 F6 (CANDIDATE ORDER): the frozen drafts must be in the producer's normalized
                    # order (the SHARED sort_candidate_rows: migrate-row order, then draft_ref bytes), so a
                    # reordered candidates_draft.toml is a FINDING, as the proposals order is (A-m4).
                    ok, detail = False, ("candidates_draft.toml candidates are not in the order the planner "
                                         "stages them (migrate-row order, then draft_ref)")
                if ok:
                    # R4 (proposal ORIGIN + CONFINEMENT): plan_ingest emits ZERO model-origin proposals, so every
                    # proposal row of an ingest run must be importer_proposal-origin (a model_proposal row is a
                    # FINDING), and every importer proposal must name a MIGRATE row's RESOLVED source (the
                    # importer runs only over migrate bytes; a keep/move or ghost source is a FINDING).
                    mig_resolved = {m["resolved_source_path"] for m in bundle["migrate"]}
                    imp_prop_by_rp = {}
                    for i, p in enumerate(props):
                        if not isinstance(p, dict):
                            ok, detail = False, "proposals.toml proposal[{}] is not a table".format(i)
                            break
                        if p.get("origin") != imp._IMPORTER_PROPOSAL_ORIGIN:
                            ok, detail = False, ("proposals.toml proposal[{}] origin {!r} is not {!r} (an ingest "
                                                 "run carries importer proposals only)".format(
                                                     i, p.get("origin"), imp._IMPORTER_PROPOSAL_ORIGIN))
                            break
                        if not (isinstance(p.get("source_path"), str) and p["source_path"] in mig_resolved):
                            ok, detail = False, ("proposals.toml proposal[{}] source_path {!r} is not a migrate "
                                                 "row's resolved source".format(i, p.get("source_path")))
                            break
                        imp_prop_by_rp[p["source_path"]] = imp_prop_by_rp.get(p["source_path"], 0) + 1
                if ok:
                    for m in bundle["migrate"]:
                        if cand_by_sp.get(m["source_path"], 0) != m["candidate_count"]:
                            ok, detail = False, ("migrate row {!r} candidate_count {} does not match the staged "
                                                 "candidates_draft.toml".format(m["source_path"],
                                                                                m["candidate_count"]))
                            break
                        if imp_prop_by_rp.get(m["resolved_source_path"], 0) != m["proposal_count"]:
                            ok, detail = False, ("migrate row {!r} proposal_count {} does not match the staged "
                                                 "importer proposals".format(m["source_path"],
                                                                             m["proposal_count"]))
                            break
                if ok:
                    # Reuse plan_import's OWN proposal validator over ALL proposal rows (confinement to a staged
                    # source, span within [0, size], state vocabulary) against the staged source sizes; strip
                    # the provenance stamp so the closed {source_path, span, suggested_state, note} keyset
                    # holds. (The importer is NEVER re-run; only its frozen proposals are re-validated.)
                    sizes = {s["path"]: s["size"] for s in run_srcs}
                    try:
                        norm_props = imp._validate_proposals(
                            [{k: v for k, v in p.items() if k != "origin"} for p in props], sizes)
                    except imp._StageError as exc:
                        ok, detail = False, "proposals do not validate: {}".format(exc.message)
                    else:
                        # A-m4 (PROPOSALS DOCUMENT RE-DERIVE-AND-EQUAL): the staged proposals.toml must EQUAL,
                        # type-strict, the document the producer constructs from those validated rows (every
                        # row importer-origin, checked above) through the SHARED _sort_proposal_rows +
                        # _proposals_model, so a reordered, denormalized, or extra-keyed document is a FINDING.
                        # The importer is never re-run: only its frozen rows are re-normalized.
                        expected_props = imp._proposals_model(run_id, imp._sort_proposal_rows(
                            [dict(p, _origin=imp._IMPORTER_PROPOSAL_ORIGIN) for p in norm_props]))
                        if not _strict_eq(docs[imp.PROPOSALS_NAME], expected_props):
                            ok, detail = False, ("proposals.toml does not equal the document the planner "
                                                 "constructs from its validated rows (row order, "
                                                 "normalization, or an extra key)")
    except (_GateError, imp._StageError, OSError, ValueError, RecursionError, KeyError, TypeError,
            IndexError) as exc:
        ok, detail = False, "ingest-draft-loss-binding failed closed ({!r})".format(exc)
    if ok:
        try:
            imp._validate_frozen_losses(rd, bundle, docs)
        except Exception as exc:
            ok, detail = False, "frozen loss validation failed ({})".format(exc)
    if ok:
        covered.update({imp.CANDIDATES_DRAFT_NAME, imp.PROPOSALS_NAME})
    out["ingest-draft-loss-binding"] = (ok, detail)

    # ---- ingest-report-reproducibility: composed IMPORT-REPORT.md byte-reproduces from the validated model -
    ok, detail = True, ""
    try:
        prows = docs[imp.PROPOSALS_NAME].get("proposal", [])
        norm = [{"source_path": pr["source_path"], "span": list(pr["span"]),
                 "suggested_state": pr["suggested_state"], "note": pr.get("note", ""),
                 "_origin": pr["origin"]}
                for pr in prows]
        ordinary = imp._render_report_md(inventory.get("inventory_digest"), inventory.get("fragment"),
                                         norm, run_dir.name)
        section = imp._render_ingest_review_md(imp._ingest_render_model(
            run_dir.name, bundle["crosswalk"], bundle["migrate"],
            docs[imp.INGEST_ACTIONS_NAME]["action"], docs[imp.CANDIDATES_DRAFT_NAME]["candidate"]))
        expected = (ordinary + section).encode("utf-8")
        actual = rd.read_bytes("IMPORT-REPORT.md")
        if expected != actual:
            ok, detail = False, ("IMPORT-REPORT.md (with its ingest section) is not byte-reproducible from "
                                 "the validated model")
    except imp._StageError as exc:
        # C-m2: a duplicated decision-unit id is refused by the shared render model (CANNOT-EVALUATE).
        ok, detail = False, "cannot reproduce IMPORT-REPORT.md ({})".format(exc.message)
    except (_GateError, OSError, KeyError, TypeError, ValueError, IndexError, RecursionError) as exc:
        ok, detail = False, "cannot reproduce IMPORT-REPORT.md ({!r})".format(exc)
    if ok:
        covered.add("IMPORT-REPORT.md")
    out["ingest-report-reproducibility"] = (ok, detail)

    # ---- ingest-artefact-completeness (R1): the fail-closed listing reconciled against the CLOSED registry,
    #      and the BASELINE artefacts (run/plan/mappings/fragments/report) RE-DERIVED byte-exact from the
    #      preserved source bytes + run.toml's nonce/staged_at, the counters VALIDATED ----
    ok, detail = True, ""
    try:
        tree = rd.tree
        if run_src_err:
            ok, detail = False, run_src_err
        else:
            digests = sorted({s["sha256"] for s in run_srcs})
            expected_files = {name for name, _m, _o, _e, presence in _INGEST_ARTEFACT_REGISTRY
                              if presence == "always" or (presence == "if-sources" and has_sources)}
            expected_files |= {"sources/" + d for d in digests}
            expected_dirs = {d for d, presence in _INGEST_ARTEFACT_DIRS
                             if presence == "always" or (presence == "if-sources" and has_sources)}
            files = {rel for rel, kind in tree.items() if kind == "file"}
            dirs = {rel for rel, kind in tree.items() if kind == "dir"}
            other = sorted(rel for rel, kind in tree.items() if kind == "other")
            unexpected = sorted((files - expected_files) | (dirs - expected_dirs))
            missing = sorted((expected_files - files) | (expected_dirs - dirs))
            if other:
                ok, detail = False, ("staged run carries a non-regular entry (symlink/FIFO/other) {} "
                                     "(fail-closed)".format(other))
            elif unexpected:
                ok, detail = False, ("staged run carries artefact(s) outside the ingest artefact registry: "
                                     "{}".format(unexpected))
            elif missing:
                ok, detail = False, "staged run is missing registered artefact(s): {}".format(missing)
        if ok:
            # sources/<sha256> (VALIDATE): the preserved bytes hash to their content-address name and match the
            # recorded size; every re-derivation below rests on exactly these bytes.
            raws = {}
            for s in run_srcs:
                raw = rd.read_bytes("sources/" + s["sha256"])
                if _sha256_hex(raw) != s["sha256"] or len(raw) != s["size"]:
                    ok, detail = False, ("preserved source bytes for {!r} do not hash to / size as run.toml "
                                         "records".format(s["path"]))
                    break
                raws[s["path"]] = raw
        if ok:
            covered.add("sources/<sha256>")
            sources = [{"path": s["path"], "raw": raws[s["path"]], "sha256": s["sha256"], "size": s["size"]}
                       for s in run_srcs]
            # plan.toml + run.toml (RE-DERIVE): the baseline plan over the preserved sources, and the run id
            # re-derived from those sources + that plan + run.toml's own nonce and staging instant, so run.toml's
            # free fields are bound to the run-dir name and the whole run.toml byte-re-derives.
            plan_model = imp._baseline_plan(sources)
            plan_bytes = imp._emit_bytes(plan_model, "plan")
            stamp = run.get("staged_at")
            nonce = run.get("nonce")
            if rd.read_bytes("plan.toml") != plan_bytes:
                ok, detail = False, ("plan.toml does not equal the baseline plan re-derived from the preserved "
                                     "source bytes")
            elif not (isinstance(stamp, str) and imp._RFC3339_UTC_RE.match(stamp)):
                ok, detail = False, "run.toml staged_at is not an RFC 3339 UTC instant"
            else:
                imp._require_nonce(nonce)
                now = datetime.datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(
                    tzinfo=datetime.timezone.utc)
                if imp._run_id(sources, plan_bytes, now, nonce) != run_id:
                    ok, detail = False, ("the run id does not re-derive from run.toml's sources/nonce/staged_at "
                                         "and the baseline plan")
                elif rd.read_bytes("run.toml") != imp._emit_bytes(
                        imp._run_toml_model(run_id, stamp, nonce, sources), "run.toml"):
                    ok, detail = False, "run.toml does not equal the run record re-derived from its own identity"
        if ok:
            covered.update({"run.toml", "plan.toml"})
            # mappings.toml + the legacy_fragment index (RE-DERIVE): walk the baseline plan exactly as staging
            # does (sources sorted, one row per span); every quarantine span yields the _lf_record built from
            # the preserved bytes over its span. The minted ids are the one non-derivable input (they come from
            # the live counters), so they are VALIDATED: LF-shaped and consecutive in mint order.
            staged_lf = docs[frag_name].get("record") if has_sources else []
            lf_ids = ([r.get("id") for r in staged_lf if isinstance(r, dict)]
                      if isinstance(staged_lf, list) else None)
            lf_shapes = [_opf_schema._valid_id_shape(i) for i in (lf_ids or [])]
            if lf_ids is None or len(lf_ids) != len(staged_lf):
                ok, detail = False, "the legacy_fragment index record list is malformed"
            elif not all(sh is not None and sh[0] == "LF" for sh in lf_shapes):
                ok, detail = False, "a legacy_fragment record id is not a well-formed LF id"
            elif any(b[1] != a[1] + 1 for a, b in zip(lf_shapes, lf_shapes[1:])):
                ok, detail = False, "the legacy_fragment ids are not consecutive in mint order"
            else:
                mapping_states = {}
                state_counts = {st: 0 for st in imp.MAPPING_STATES}
                lf_expected = []
                sha_of = {s["path"]: s["sha256"] for s in sources}
                for sp in sorted(plan_model["fragments"]):
                    for row in plan_model["fragments"][sp]:
                        start, end = row["span"]
                        mapping_states.setdefault(sp, []).append({"span": [start, end], "state": row["state"],
                                                                  "target": row.get("target"),
                                                                  "origin": row["origin"]})
                        state_counts[row["state"]] += 1
                        if row["state"] in imp._QUARANTINE_STATES:
                            if len(lf_expected) >= len(lf_ids):
                                break
                            lf_expected.append(imp._lf_record(
                                lf_ids[len(lf_expected)], sp, sha_of[sp], start, end, run_id, stamp,
                                raws[sp][start:end].decode("utf-8")))
                n_quarantine = sum(state_counts[st] for st in imp._QUARANTINE_STATES)
                if rd.read_bytes("mappings.toml") != imp._emit_bytes(
                        imp._mappings_model(mapping_states), "mappings.toml"):
                    ok, detail = False, "mappings.toml does not equal the mappings re-derived from the baseline plan"
                elif len(lf_ids) != n_quarantine:
                    ok, detail = False, ("the legacy_fragment index carries {} records but the baseline plan "
                                         "quarantines {} spans".format(len(lf_ids), n_quarantine))
                elif has_sources and rd.read_bytes(frag_name) != imp._emit_bytes(
                        {"schema": imp.SCHEMA, "record": lf_expected}, frag_name):
                    ok, detail = False, ("the legacy_fragment index does not equal the records re-derived from "
                                         "the preserved bytes (a fragment body, provenance field, or title "
                                         "drifted)")
        if ok:
            covered.update({"mappings.toml", frag_name} if has_sources else {"mappings.toml"})
            # candidate/counters.toml (VALIDATE): a closed {schema, counters} table the counters validator
            # accepts, whose LF high-water is the LAST minted LF id (mint advances it by one per fragment) and
            # within which every minted id falls. The non-LF high-water marks come from the live store at plan
            # time (not derivable here; a disclosed residual).
            cdoc = docs["candidate/counters.toml"]
            high, cfindings = _opf_schema.validate_counters(cdoc)
            if set(cdoc) != {"schema", "counters"}:
                ok, detail = False, "candidate/counters.toml is not the closed {schema, counters} table"
            elif cfindings:
                ok, detail = False, "candidate/counters.toml is invalid: {}".format("; ".join(cfindings))
            elif lf_shapes and not _strict_eq(high.get("LF"), lf_shapes[-1][1]):
                ok, detail = False, ("candidate/counters.toml LF high-water {!r} is not the last minted LF id "
                                     "{}".format(high.get("LF"), lf_ids[-1]))
            elif _opf_schema.check_ids_within_counters(lf_ids, high):
                ok, detail = False, "a minted legacy_fragment id is not within candidate/counters.toml"
        if ok:
            covered.add("candidate/counters.toml")
            # report.toml (RE-DERIVE): the promotion-ready marker over the staged candidate set (every staged
            # artefact above, each already re-derived or validated), the re-derived plan bytes and inventory
            # digest, and the state counts of the baseline walk.
            all_body = {name: rd.read_bytes(name)
                        for name in ("run.toml", "plan.toml", "mappings.toml", "candidate/counters.toml")}
            if has_sources:
                all_body[frag_name] = rd.read_bytes(frag_name)
            for s in run_srcs:
                all_body["sources/" + s["sha256"]] = raws[s["path"]]
            inv_digest = imp._build_inventory(
                [{"path": s["path"], "sha256": s["sha256"], "size": s["size"]} for s in run_srcs])[1]
            if rd.read_bytes("report.toml") != imp._emit_bytes(imp._run_report_model(
                    run_id, plan_bytes, inv_digest, state_counts, bool(lf_expected), all_body), "report.toml"):
                ok, detail = False, "report.toml does not equal the report re-derived from the staged candidate set"
        if ok:
            covered.add("report.toml")
    except imp._StageError as exc:
        ok, detail = False, "ingest-artefact-completeness failed closed ({})".format(exc.message)
    except (_GateError, OSError, ValueError, RecursionError, KeyError, TypeError, IndexError,
            AttributeError) as exc:
        ok, detail = False, "ingest-artefact-completeness failed closed ({!r})".format(exc)
    out["ingest-artefact-completeness"] = (ok, detail)

    # Registry reconciliation (fail-closed): a registered artefact whose OWNING check passed without its
    # mechanism running is a FINDING, so the registry cannot claim a mechanism the gate never exercised.
    for name, mech, owner, _env, presence in _INGEST_ARTEFACT_REGISTRY:
        if presence == "if-sources" and not has_sources:
            continue
        if out.get(owner, (False, ""))[0] and name not in covered:
            out["ingest-artefact-completeness"] = (False, (
                "registry entry {!r} ({}) is owned by {} which passed without running its mechanism "
                "(registry reconciliation: fail-closed)".format(name, mech, owner)))
            break

    return out


# Durable acceptance is outside the staged registry. A staged copy remains an unexpected artefact.
_INGEST_EVIDENCE_REGISTRY = (("acceptance.json", "VALIDATE", "ingest-acceptance-binding", "if-reviewed"),)
_INGEST_ACCEPTANCE_CHECKS = ("ingest-acceptance-binding", "ingest-acceptance-completeness")


def _gate_homes(homes):
    """The generation the gate evaluates under: an unsupplied generation is the legacy generation 1 only
    while no later generation can be active (the rule _row_scope_error applies)."""
    import _opf_store
    if homes is None:
        if _opf_store.SUPPORTED_HOMES >= 2:
            raise _GateError("the store's homes generation was not supplied to this manifest-free gate")
        return 1
    return homes


def _ingest_store_fd(rd, homes=None):
    """Bind the store to this opened run, through the shared generation-aware run-location constructor
    (_opf_import._import_run_locations) and inode comparison. Returns None for a detached copy (no
    store-relative home matches): it cannot establish absence of durable acceptance, so an ingest run's
    durable checks refuse, while an ordinary run is classified from its own listing (A-M1). Staged snapshot
    checks still evaluate the supplied bytes.
    """
    import _journal
    import _opf_import as imp
    for rel in imp._import_run_locations(rd.path.name, _gate_homes(homes)):
        if tuple(rd.path.parts[-len(rel.split("/")):]) != tuple(rel.split("/")):
            continue
        depth = len(rel.split("/"))
        fd = os.open("/".join([".."] * depth), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=rd.fd)
        try:
            check_fd = _journal._open_dir_contained(fd, rel)
            try:
                a, b = os.fstat(check_fd), os.fstat(rd.fd)
                if (a.st_dev, a.st_ino) != (b.st_dev, b.st_ino):
                    raise _GateError("run identity changed while locating durable evidence")
            finally:
                os.close(check_fd)
        except BaseException:
            os.close(fd)
            raise
        return fd
    return None


def _ingest_acceptance_checks(rd, homes=None):
    import _opf_import as imp
    ids = ("acceptance-schema", "acceptance-binding", "acceptance-attribution", "acceptance-completeness")
    ids += _INGEST_ACCEPTANCE_CHECKS
    try:
        if _INGEST_EVIDENCE_REGISTRY != ((imp.ACCEPTANCE_NAME, "VALIDATE",
                                        "ingest-acceptance-binding", "if-reviewed"),):
            raise _GateError("durable acceptance registry does not match its validator")
        # Generation 1 has no durable home: `.working/imported` is ordinary content there, never probed.
        if _gate_homes(homes) != imp.INGEST_HOMES_GENERATION:
            return {cid: (True, "not applicable: homes generation 1 has no durable acceptance home")
                    for cid in ids}
        fd = _ingest_store_fd(rd, homes)
        if fd is None:
            raise _GateError("durable acceptance cannot be located from a detached run; "
                             "retain its store-relative home")
        try:
            acceptance = imp._read_ingest_acceptance(fd, rd.path.name)
        finally:
            os.close(fd)
        if acceptance is None:
            return {cid: (True, "not yet reviewed") for cid in ids}
        snapshot = imp._ingest_snapshot(rd, homes)
        binding, complete, rejected = imp.validate_ingest_acceptance(snapshot, acceptance)
        detail = "recorded rejection; not promotable" if rejected else "review recorded; execution unavailable"
        return {cid: (not (complete if cid.endswith("completeness") else binding),
                      "; ".join(complete if cid.endswith("completeness") else binding) or detail) for cid in ids}
    except Exception as exc:
        return {cid: (False, "durable acceptance cannot be evaluated ({}); create a fresh run".format(exc))
                for cid in ids}


def check_staged_run(run_dir, homes=None):
    """Run the explicit check registry over one staged run directory. Returns an ordered dict
    check-id -> (ok: bool, detail: str). Each check fails closed on an artefact it cannot read: an
    unreadable required input is that check's FINDING, never a silent pass. The SUPPLIED directory is opened
    once no-follow and its entries classified FIRST (_RunDir); every run-dir artefact is then read through
    that one descriptor with a non-blocking, regular-file-validated open, so an unopenable or unlistable run
    dir fails every check closed and a FIFO or symlink in an artefact's place is a located FINDING, never a
    hang (A-M1/A-M2). `homes` is the store's homes generation for a caller that read the store manifest; the
    ingest scope check fails closed on an unsupplied generation once a later generation can be active."""
    try:
        rd = _RunDir(run_dir)
    except _GateError as exc:
        return {cid: (False, "cannot evaluate: {}".format(exc)) for cid in EXPECTED_CHECKS}
    try:
        return _check_staged_run(rd, homes)
    finally:
        rd.close()


def _check_staged_run(rd, homes=None):
    """check_staged_run over the opened, classified run directory `rd` (a _RunDir)."""
    import json
    import re
    import _opf_import as imp

    run_dir = rd.path
    results = {}

    def record(cid, ok, detail=""):
        results[cid] = (bool(ok), detail)

    # --- staged-run-structure -----------------------------------------------------------------------
    required = ["run.toml", "plan.toml", "mappings.toml", "report.toml", "inventory.toml",
                "IMPORT-REPORT.md"]
    missing = [r for r in required if rd.kind(r) != "file"]
    name_ok = bool(re.match(_RUN_ID_RE_TEXT, run_dir.name))
    record("staged-run-structure", not missing and name_ok,
           "missing {}".format(missing) if missing else ("run-dir name {!r} is not a run id".format(
               run_dir.name) if not name_ok else ""))

    # Parse the core artefacts once, fail-closed; a parse failure fails the structure check and every
    # dependent check (an unreadable input is never nothing-to-check).
    try:
        run = rd.load_toml("run.toml")
        # plan.toml is parsed for validity here (fail-closed on unparseable); its raw bytes are re-read by
        # report-binding-digests to recompute the plan_digest.
        rd.load_toml("plan.toml")
        mappings = rd.load_toml("mappings.toml")
        report = rd.load_toml("report.toml")
        inventory = rd.load_toml("inventory.toml")
    except _GateError as exc:
        # A malformed/unreadable required artefact fails the whole run closed. Derive the recorded set
        # from the single-source registry (not a hand-maintained tuple) so this early-return path can
        # never drift from EXPECTED_CHECKS and silently omit a future check.
        for cid in EXPECTED_CHECKS:
            record(cid, False, str(exc))
        return results

    # --- ingest-review bundle detection (MIG-PR4b) --------------------------------------------------
    # The frozen ingest-review.toml bundle (MIG-PR4a) is CONDITIONALLY PRESENT. Detect it ONCE, fail-closed,
    # so the six ingest checks AND the proposals-artifact report compare below agree on whether this is an
    # ingest run. A-M1: the ingest markers (_opf_import._INGEST_RUN_MARKERS) are classified through the
    # SUPPLIED run directory's own no-follow listing (rd.tree, bound to the descriptor every check reads
    # through), never through a reconstructed conventional `<store>/.working/imports/<run-id>` path: a
    # detached or relocated copy of an ingest run is still an ingest run, and another directory's absence is
    # never read as non-applicability. ANY entry at a marker name (a regular file, or a FIFO/symlink in its
    # place) marks the run as ingest. The bundle is read through the same descriptor-bound reader and
    # validated by the SHARED structural validator the review path applies (_validate_staged_ingest_bundle).
    # This is read-only: it opens no writer, mutates nothing.
    ingest_bundle = None
    ingest_is_run = False
    ingest_load_failed = False
    ingest_load_detail = ""
    durable_unavailable = ""
    marker = next((name for name in imp._INGEST_RUN_MARKERS if rd.kind(name) is not None), None)
    if marker is None:
        try:
            # The durable home is control storage only in generation 2; in generation 1 it and the homes-2
            # staging names are ordinary content, never probed. A detached copy has no store to consult.
            fd = _ingest_store_fd(rd, homes) if _gate_homes(homes) == imp.INGEST_HOMES_GENERATION else None
            if fd is not None:
                try:
                    import _journal
                    if _journal._lstat_contained(fd, imp._ingest_acceptance_home(run_dir.name)) is not None:
                        marker = "durable import evidence"
                finally:
                    os.close(fd)
            if marker is None and rd.kind(imp.ACCEPTANCE_NAME) == "file":
                acc_marker = imp._strict_json(rd.read_bytes(imp.ACCEPTANCE_NAME))
                if isinstance(acc_marker, dict) and ("ingest" in acc_marker
                                                     or acc_marker.get("format") == imp.INGEST_ACCEPTANCE_FORMAT):
                    marker = imp.ACCEPTANCE_NAME
        except Exception as exc:
            durable_unavailable = "durable acceptance cannot be located ({})".format(exc)
    if marker is not None:
        ingest_is_run = True
        if rd.kind(imp.INGEST_REVIEW_NAME) is None:
            ingest_load_failed = True
            ingest_load_detail = ("ingest markers present (marker {!r}) but the review bundle "
                                  "ingest-review.toml is absent (partial ingest run)".format(marker))
        else:
            try:
                ingest_bundle = imp._validate_staged_ingest_bundle(rd.load_toml(imp.INGEST_REVIEW_NAME),
                                                                   run_dir.name)
            except _GateError as exc:
                ingest_load_failed = True
                ingest_load_detail = "ingest-review bundle present but unreadable ({})".format(exc)
            except imp._StageError as exc:
                ingest_load_failed = True
                ingest_load_detail = "ingest-review bundle present but malformed ({})".format(exc.message)

    # --- report-schema ------------------------------------------------------------------------------
    # report.verdict AND report.schema must each be a Python INT, not merely == their literal: Python's
    # `False == 0` and `True == 1` are True, so a `verdict = false` (bool) or `schema = true` (bool) run would
    # slip past a bare `== 0` / `== 1` and pass every gate check on a not-promotion-ready or schema-malformed
    # run. Require the integer type on both (`type(x) is int` excludes bool), mirroring the review-side strict-
    # int guard in _opf_import._load_staged_run_for_review; promotion_ready stays a strict `is True` bool.
    # (R5-F2: schema is the class sibling of the verdict strict-int guard, over the same untrusted staged field.)
    schema_ok = (type(report.get("schema")) is int and report.get("schema") == 1
                 and report.get("run_id") == run_dir.name
                 and type(report.get("verdict")) is int and report.get("verdict") == 0
                 and report.get("promotion_ready") is True
                 and isinstance(report.get("artifact"), list))
    record("report-schema", schema_ok,
           "" if schema_ok else "report.toml schema/run_id/verdict/promotion_ready/artifact malformed")

    # --- artifact-digest-integrity ------------------------------------------------------------------
    # report.get("artifact") is UNTRUSTED staged data: a non-list (int, bool, ...) would make the
    # `for entry in ...` loop raise TypeError ("'int' object is not iterable"). Guard the shape FIRST so a
    # malformed artifact is a located FINDING for ALL callers (the gate CLI AND the review delegation),
    # never an uncaught raise (guard-input-soundness / check-fails-closed-on-unreadable).
    art_ok = True
    art_detail = ""
    artifact = report.get("artifact")
    if not isinstance(artifact, list):
        art_ok, art_detail = False, "report.toml artifact is not a list"
    else:
        for entry in artifact:
            if not (isinstance(entry, dict) and isinstance(entry.get("path"), str)
                    and isinstance(entry.get("sha256"), str)):
                art_ok, art_detail = False, "malformed artifact entry {!r}".format(entry)
                break
            try:
                data = rd.read_bytes(entry["path"])
            except _GateError as exc:
                # The descriptor-bound contained reader refuses an embedded-NUL / control-character path, a
                # `..` escape, a symlink, and a non-regular entry, so a crafted report.toml artifact path is a
                # located FINDING, never an uncaught crash, a followed link, or a blocking read (R8-F1, A-M2).
                art_ok, art_detail = False, "enumerated artefact unreadable: {}".format(exc)
                break
            if _sha256_hex(data) != entry["sha256"]:
                art_ok, art_detail = False, "artefact {} bytes do not match recorded digest".format(
                    entry["path"])
                break
    record("artifact-digest-integrity", art_ok, art_detail)

    # --- mapping rows, source records ---------------------------------------------------------------
    rows = mappings.get("mapping")
    sources = run.get("source")
    if not isinstance(rows, list) or not isinstance(sources, list):
        for cid in ("mapping-totality", "mapping-state-vocab", "mapping-origin-vocab", "lf-bijection",
                    "lf-quad-completeness"):
            record(cid, False, "mappings.toml `mapping` or run.toml `source` is not an array")
    else:
        # mapping-state-vocab. The isinstance(str) guard precedes the membership test: rows are UNTRUSTED
        # staged data, so an unhashable state ([] or {}) is a located FINDING, never a TypeError.
        vocab_ok = all(isinstance(r, dict) and isinstance(r.get("state"), str)
                       and r.get("state") in imp.MAPPING_STATES for r in rows)
        record("mapping-state-vocab", vocab_ok,
               "" if vocab_ok else "a mapping row carries a state outside the 8-state vocabulary")

        # mapping-origin-vocab: every row carries a valid provenance origin (imp._ORIGIN_SET). A deleted or
        # out-of-vocabulary origin would let a staged run bypass the acceptance gate (the acceptance layer
        # keys the model_proposal-requires-accept rule off origin), so it is a FINDING here.
        origin_ok = all(isinstance(r, dict) and isinstance(r.get("origin"), str)
                        and r.get("origin") in imp._ORIGIN_SET for r in rows)
        record("mapping-origin-vocab", origin_ok,
               "" if origin_ok else "a mapping row carries an origin outside the provenance vocabulary")

        # mapping-totality: per source, spans tile [0, size) exactly.
        by_source = {}
        for r in rows:
            if isinstance(r, dict) and isinstance(r.get("source_path"), str):
                by_source.setdefault(r["source_path"], []).append(r.get("span"))
        tot_ok = True
        tot_detail = ""
        for s in sources:
            if not (isinstance(s, dict) and isinstance(s.get("path"), str)
                    and type(s.get("size")) is int):
                tot_ok, tot_detail = False, "malformed run.toml source record {!r}".format(s)
                break
            spans = by_source.get(s["path"], [])
            norm = []
            for sp in spans:
                if not (isinstance(sp, list) and len(sp) == 2 and all(type(x) is int for x in sp)):
                    tot_ok, tot_detail = False, "malformed span for {}".format(s["path"])
                    break
                norm.append((sp[0], sp[1]))
            if not tot_ok:
                break
            norm.sort()
            cursor = 0
            for a, b in norm:
                if a != cursor or b < a:
                    tot_ok, tot_detail = False, "spans do not tile {} at offset {}".format(
                        s["path"], cursor)
                    break
                cursor = b
            if not tot_ok:
                break
            if cursor != s["size"]:
                tot_ok, tot_detail = False, "spans for {} end at {} but the source is {} bytes".format(
                    s["path"], cursor, s["size"])
                break
        record("mapping-totality", tot_ok, tot_detail)

        # lf-bijection: quarantine-state mappings <-> legacy_fragment records, one to one.
        quarantine_rows = [r for r in rows if isinstance(r, dict) and isinstance(r.get("state"), str)
                           and r.get("state") in imp._QUARANTINE_STATES]
        lf_rel = "fragments/legacy_fragment.index.toml"
        lf_records = []
        lf_read_ok = True
        # Any entry at the index path (a regular file, or a FIFO/symlink/other in its place) is read through
        # the regular-file-validated reader, so a non-regular index is a FINDING, never read as absent.
        if quarantine_rows or rd.kind(lf_rel) is not None:
            try:
                lf_records = rd.load_toml(lf_rel).get("record", [])
            except _GateError as exc:
                lf_read_ok = False
                record("lf-bijection", False, str(exc))
        if lf_read_ok:
            if not isinstance(lf_records, list):
                record("lf-bijection", False, "legacy_fragment index is not an array")
            else:
                # A true one-to-one correspondence by (source_path, span), not a bare count: a
                # count-preserving swap (one record duplicated over another) must still FINDING, because a
                # quarantined fragment would then have no matching record (the no-drop invariant).
                def _lf_keys(recs):
                    keys = []
                    for r in recs:
                        d = r if isinstance(r, dict) else {}
                        sp = d.get("span")
                        keys.append((d.get("source_path"), tuple(sp) if isinstance(sp, list) else None))
                    return sorted(keys, key=lambda k: (str(k[0]), str(k[1])))
                bij_ok = _lf_keys(quarantine_rows) == _lf_keys(lf_records)
                record("lf-bijection", bij_ok,
                       "" if bij_ok else "quarantine mappings do not correspond one-to-one to "
                       "legacy_fragment records by (source_path, span): {} quarantine vs {} lf".format(
                           len(quarantine_rows), len(lf_records)))

        # lf-quad-completeness: every LF carries the full provenance quad + body.
        quad = ("source_path", "source_digest", "span", "run_id", "body")
        if isinstance(lf_records, list):
            quad_ok = all(isinstance(rec, dict) and all(k in rec for k in quad) for rec in lf_records)
            record("lf-quad-completeness", quad_ok,
                   "" if quad_ok else "a legacy_fragment omits a provenance-quad field")
        else:
            record("lf-quad-completeness", False, "legacy_fragment index is not an array")

    # --- source-preservation ------------------------------------------------------------------------
    src_ok = True
    src_detail = ""
    for s in (sources if isinstance(sources, list) else []):
        if not (isinstance(s, dict) and isinstance(s.get("sha256"), str)):
            src_ok, src_detail = False, "malformed source record {!r}".format(s)
            break
        try:
            body = rd.read_bytes("sources/" + s["sha256"])
        except _GateError as exc:
            # The sha256 path component is untrusted staged data: the contained reader refuses an embedded-NUL
            # or control character, a `/` or `..` escape, a symlink, and a non-regular entry, each a located
            # FINDING, never an uncaught crash or a blocking read (R8-F1, A-M2).
            src_ok, src_detail = False, "preserved source bytes absent/unreadable: {}".format(exc)
            break
        if _sha256_hex(body) != s["sha256"]:
            src_ok, src_detail = False, "preserved bytes for {} do not hash to the recorded digest".format(
                s["sha256"])
            break
    record("source-preservation", src_ok, src_detail)

    # --- inventory-digest ---------------------------------------------------------------------------
    inv_ok = False
    inv_detail = ""
    recorded = inventory.get("inventory_digest")
    payload = {k: v for k, v in inventory.items() if k != "inventory_digest"}
    try:
        recomputed = "sha256:" + _sha256_hex(imp._emit_bytes(payload, "inventory"))
        inv_ok = isinstance(recorded, str) and recorded == recomputed
        inv_detail = "" if inv_ok else "recorded inventory_digest does not recompute"
    except imp._StageError as exc:
        inv_detail = "inventory payload not canonically emittable: {}".format(exc.message)
    record("inventory-digest", inv_ok, inv_detail)

    # --- report-binding-digests ---------------------------------------------------------------------
    # report.toml carries the promotion-ready binding: plan_digest recomputes over the staged plan.toml
    # bytes, and inventory_digest matches inventory.toml's own recorded digest (which inventory-digest
    # already recomputes), so the two anchors an acceptance record binds cannot silently drift.
    rb_ok = True
    rb_detail = ""
    try:
        plan_bytes = rd.read_bytes("plan.toml")
    except _GateError as exc:
        rb_ok, rb_detail = False, "plan.toml unreadable ({})".format(exc)
    if rb_ok:
        exp_plan = "sha256:" + _sha256_hex(plan_bytes)
        rec_plan = report.get("plan_digest")
        rec_inv = report.get("inventory_digest")
        if not (isinstance(rec_plan, str) and rec_plan == exp_plan):
            rb_ok, rb_detail = False, "report.toml plan_digest does not recompute over plan.toml bytes"
        elif not (isinstance(rec_inv, str) and rec_inv == inventory.get("inventory_digest")):
            rb_ok, rb_detail = False, "report.toml inventory_digest does not match inventory.toml"
    record("report-binding-digests", rb_ok, rb_detail)

    # --- proposals-artifact -------------------------------------------------------------------------
    # proposals.toml is present, schema/run-id correct, every row stamped origin=model_proposal, and the
    # human-readable IMPORT-REPORT.md is byte-reproducible from inventory.toml + proposals.toml + run id
    # (the report is a derivative, never independently authored).
    pa_ok = True
    pa_detail = ""
    proposals = None
    try:
        proposals = rd.load_toml("proposals.toml")
    except _GateError as exc:
        pa_ok, pa_detail = False, str(exc)
    if pa_ok:
        prows = proposals.get("proposal")
        # proposals.schema is strict-int (`type(x) is int`, bool excluded) for the same bool-slip reason as
        # report-schema: a `schema = true` proposals.toml would satisfy `True == 1` and slip a bare `== 1`
        # (R5-F2, the class sibling over the untrusted proposals field).
        if not (type(proposals.get("schema")) is int and proposals.get("schema") == 1
                and proposals.get("run_id") == run_dir.name and isinstance(prows, list)):
            pa_ok, pa_detail = False, "proposals.toml schema/run_id/proposal array malformed"
        else:
            for pr in prows:
                # span mirrors _validate_proposals: a 2-element int list, not merely a list. A list that is
                # not a [start, end] int pair ([] or [5]) is a located row FINDING here rather than an
                # uncaught IndexError when _render_report_md below indexes span[0]/span[1] over this UNTRUSTED
                # staged proposal (R6-F1, the exception-coverage class sibling of the artifact-list guard).
                # MIG-PR4a: admit the DECLARED proposal-provenance vocabulary (imp._PROPOSAL_ORIGIN_VALUES:
                # model_proposal AND the MIG-PR3 importer_proposal), not model_proposal alone. Pre-fix a real
                # staged MIGRATE run (whose importer suggestions rest as origin=importer_proposal) failed this
                # check, so no migrate run could ever pass the gate and thus could never be reviewed. An origin
                # OUTSIDE the closed vocabulary is still rejected here (reject unknown/conflicting origins).
                span = pr.get("span") if isinstance(pr, dict) else None
                if not (isinstance(pr, dict) and pr.get("origin") in imp._PROPOSAL_ORIGIN_VALUES
                        and isinstance(pr.get("source_path"), str)
                        and isinstance(span, list) and len(span) == 2 and all(type(x) is int for x in span)
                        and isinstance(pr.get("suggested_state"), str)
                        and pr.get("suggested_state") in imp.MAPPING_STATES):
                    pa_ok, pa_detail = False, ("a proposals.toml row is malformed or carries an origin "
                                               "outside the proposal-provenance vocabulary")
                    break
    # MIG-PR4b: for an INGEST run the staged IMPORT-REPORT.md carries a composed ingest section, so the
    # whole-file byte reproduction is owned by ingest-report-reproducibility (which reproduces the ordinary
    # section AND the ingest section from the validated model). proposals-artifact keeps its proposals.toml
    # schema/provenance duty above; it delegates the report byte compare here rather than false-FINDING on the
    # extra section. An ordinary (non-ingest) run keeps the whole-file compare exactly as before.
    if pa_ok and not ingest_is_run:
        try:
            # MIG-PR4a: RETAIN each row's provenance during report normalization. _render_report_md renders
            # `origin=<p._origin>` per proposal, so the reproduced report must carry the SAME per-row origin
            # the staged proposals.toml records; without it every row reproduced as the default model_proposal
            # and an importer_proposal row's report line no longer byte-matched the staged IMPORT-REPORT.md.
            norm = [{"source_path": pr["source_path"], "span": list(pr["span"]),
                     "suggested_state": pr["suggested_state"], "note": pr.get("note", ""),
                     "_origin": pr["origin"]}
                    for pr in proposals.get("proposal", [])]
            expected_md = imp._render_report_md(inventory.get("inventory_digest"),
                                                inventory.get("fragment"), norm, run_dir.name)
            # Compare BYTES, not universal-newline-normalized text: read_text() would silently fold a CRLF
            # IMPORT-REPORT.md into an LF match, so a byte-non-reproducible report (e.g. CRLF line endings)
            # would pass. The rendered surface is LF (utf-8), so an exact byte compare catches it.
            actual_md = rd.read_bytes("IMPORT-REPORT.md")
            if expected_md.encode("utf-8") != actual_md:
                pa_ok, pa_detail = False, ("IMPORT-REPORT.md is not byte-reproducible from inventory.toml "
                                           "+ proposals.toml + run id")
        # IndexError joins the tuple as a defence-in-depth backstop (marginal cost: one exception name): the
        # proposal-span row-check above now rejects a malformed proposal span, but _render_report_md also
        # indexes each inventory FRAGMENT span (frag["span"][0/1]), which this check does not shape-validate
        # upstream, so a malformed inventory fragment span becomes the located "cannot reproduce" FINDING
        # rather than an uncaught IndexError propagating to a direct check_staged_run caller.
        except (_GateError, OSError, KeyError, TypeError, ValueError, IndexError) as exc:
            pa_ok, pa_detail = False, "cannot reproduce IMPORT-REPORT.md ({})".format(exc)
    record("proposals-artifact", pa_ok, pa_detail)

    # --- acceptance.json (conditionally present) ----------------------------------------------------
    # ABSENT is a legitimate pre-review state, recorded as a PASS ("not yet reviewed"). Present-but-
    # unparseable is fail-closed (every acceptance check FINDINGs). Present-and-parseable runs the
    # independent recompute: schema validity, the {run_id, plan_digest, inventory_digest} binding plus a
    # per-decision echo cross-check, actor attribution, and one-decision-per-fragment completeness with the
    # model_proposal-resting-requires-accept rule. This is a defence-in-depth recompute over the staged
    # bytes, not a re-run of the review tool (the acceptance record is unauthenticated; the gate re-derives).
    acc_checks = ("acceptance-schema", "acceptance-binding", "acceptance-attribution",
                  "acceptance-completeness")
    # Classify through the run-dir listing (a no-follow three-way, SECI-symlink-resolution), never
    # Path.exists(): a DANGLING symlink would answer exists() False and be misread as "not yet reviewed" (a
    # false PASS). Genuine absence (no dentry) is the pre-review PASS; a regular file is evaluated (read
    # through the regular-file-validated reader); a symlink or any other non-regular entry is a FINDING
    # (fail-closed), never pass-as-absent. An unclassifiable entry already failed the listing closed.
    acc_kind = rd.kind(imp.ACCEPTANCE_NAME)
    # Durable acceptance exists only in generation 2. In generation 1 every run keeps main's staged
    # acceptance grading below; an unsupplied generation that cannot be graded fails the durable path closed.
    try:
        legacy_generation = _gate_homes(homes) != imp.INGEST_HOMES_GENERATION
    except _GateError:
        legacy_generation = False
    if ingest_is_run and not legacy_generation:
        for cid, (ok, detail) in _ingest_acceptance_checks(rd, homes).items():
            record(cid, ok, detail)
    elif acc_kind is None:
        for cid in acc_checks:
            record(cid, True, "not yet reviewed")
    elif acc_kind != "file":
        for cid in acc_checks:
            record(cid, False, "acceptance.json is present but not a regular file")
    else:
        acc = None
        acc_err = ""
        try:
            acc = imp._strict_json(rd.read_bytes(imp.ACCEPTANCE_NAME))
        except (_GateError, ValueError, RecursionError) as exc:
            # RecursionError joins the tuple (R8-F1): deeply-nested JSON (a malicious staged acceptance.json,
            # even under the size cap) drives json.loads into unbounded recursion, which is neither an OSError
            # nor a ValueError; without it a crafted record crashes the gate instead of every acceptance check
            # failing closed. MemoryError/KeyboardInterrupt/SystemExit still propagate.
            acc_err = "acceptance.json present but unreadable/unparseable ({})".format(exc)
        if acc is None or not isinstance(acc, dict):
            for cid in acc_checks:
                record(cid, False, acc_err or "acceptance.json is not a JSON object")
        else:
            # acceptance-schema: structural v1 validity (an independent field-and-type recompute).
            sch = imp._validate_acceptance(acc)
            record("acceptance-schema", not sch, "" if not sch else "; ".join(sch))

            # acceptance-attribution: a non-empty, self-asserted actor.declared.
            actor = acc.get("actor")
            attr_ok = (isinstance(actor, dict) and isinstance(actor.get("declared"), str)
                       and bool(actor["declared"].strip()))
            record("acceptance-attribution", attr_ok,
                   "" if attr_ok else "acceptance.json actor.declared is missing or empty")

            # Correlate inventory fragments <-> mapping rows for the binding/completeness recompute.
            frag_by_id = {}
            key_meta = {}
            corr_ok = True
            inv_frags = inventory.get("fragment")
            map_rows = mappings.get("mapping") if isinstance(mappings, dict) else None
            if not (isinstance(inv_frags, list) and isinstance(map_rows, list)):
                corr_ok = False
            else:
                for fr in inv_frags:
                    # The span elements are validated as ints BEFORE constructing the (source_path,
                    # tuple(span)) key: a nested-unhashable span ([[], []]) is length-2 but tuple(span) is
                    # then unhashable, so an int-element guard keeps the key hashable and a malformed span a
                    # located FINDING (corr_ok False), never an uncaught TypeError at the dict membership.
                    if not (isinstance(fr, dict) and isinstance(fr.get("fragment_id"), str)
                            and isinstance(fr.get("source_path"), str) and isinstance(fr.get("span"), list)
                            and len(fr["span"]) == 2 and all(type(x) is int for x in fr["span"])):
                        corr_ok = False
                        break
                    frag_by_id[fr["fragment_id"]] = (fr["source_path"], tuple(fr["span"]))
                for row in (map_rows if corr_ok else []):
                    # Same int-element span guard before the (source_path, tuple(span)) key_meta key: an
                    # unhashable nested span would otherwise raise a TypeError at the key assignment.
                    if not (isinstance(row, dict) and isinstance(row.get("source_path"), str)
                            and isinstance(row.get("span"), list) and len(row["span"]) == 2
                            and all(type(x) is int for x in row["span"])):
                        corr_ok = False
                        break
                    key_meta[(row["source_path"], tuple(row["span"]))] = {
                        "origin": row.get("origin"), "state": row.get("state")}

            decisions = acc.get("decisions")
            decisions = decisions if isinstance(decisions, list) else []

            # acceptance-binding: run id + both digests match the run, and every decision echoes the staged
            # mapping origin/proposed_state.
            bind_ok = corr_ok
            bind_detail = "" if corr_ok else "inventory/mappings correlation malformed (cannot bind)"
            if corr_ok:
                if not (acc.get("run_id") == run_dir.name
                        and acc.get("plan_digest") == report.get("plan_digest")
                        and acc.get("inventory_digest") == report.get("inventory_digest")):
                    bind_ok, bind_detail = False, "acceptance run_id/plan_digest/inventory_digest do not bind the run"
                else:
                    for d in decisions:
                        if not isinstance(d, dict):
                            bind_ok, bind_detail = False, "a decision is not an object"
                            break
                        fid = d.get("fragment_id")
                        # A non-str (e.g. unhashable []/{}) fragment_id cannot key the `in frag_by_id` dict
                        # membership (it would raise TypeError); a malformed decision fragment_id over this
                        # UNTRUSTED acceptance.json is a binding FINDING, fail-closed, never a crash.
                        if not isinstance(fid, str):
                            bind_ok, bind_detail = False, "a decision fragment_id is not a string"
                            break
                        if fid in frag_by_id:
                            meta = key_meta.get(frag_by_id[fid], {})
                            if (d.get("origin") != meta.get("origin")
                                    or d.get("proposed_state") != meta.get("state")):
                                bind_ok, bind_detail = False, ("a decision echoes an origin/proposed_state "
                                                               "that does not match its staged mapping")
                                break
            record("acceptance-binding", bind_ok, bind_detail)

            # acceptance-completeness: exactly one decision per fragment (no missing, unknown, or duplicate)
            # and every model_proposal-origin mapping resting in a resting state carries an explicit accept.
            comp_ok = corr_ok
            comp_detail = "" if corr_ok else "inventory/mappings correlation malformed (cannot check)"
            if corr_ok:
                ids = [d.get("fragment_id") for d in decisions if isinstance(d, dict)]
                if len(ids) != len(decisions):
                    comp_ok, comp_detail = False, "a decision is not an object"
                elif not all(isinstance(i, str) for i in ids):
                    # An unhashable ([] or {}) fragment_id would raise TypeError at the set() below; a
                    # malformed decision fragment_id is a located FINDING, fail-closed, never a crash.
                    comp_ok, comp_detail = False, "a decision fragment_id is not a string"
                elif len(set(ids)) != len(ids):
                    comp_ok, comp_detail = False, "a fragment carries more than one decision"
                elif set(ids) != set(frag_by_id):
                    comp_ok, comp_detail = False, ("decisions do not cover exactly the inventory fragments "
                                                   "(a missing or unknown fragment)")
                else:
                    accepted = {d.get("fragment_id") for d in decisions
                                if isinstance(d, dict) and d.get("decision") == "accept"}
                    for fid, key in frag_by_id.items():
                        meta = key_meta.get(key, {})
                        # isinstance(str) guard before the membership test: key_meta.state comes from an
                        # UNVALIDATED mapping row (the correlation above checks only source_path/span), so an
                        # unhashable state ([] or {}) would otherwise raise TypeError at the _RESTING_STATES
                        # frozenset membership; a non-str state simply is not a resting state here.
                        if (meta.get("origin") == imp._MODEL_PROPOSAL_ORIGIN
                                and isinstance(meta.get("state"), str)
                                and meta.get("state") in imp._RESTING_STATES and fid not in accepted):
                            comp_ok, comp_detail = False, ("a model_proposal resting mapping lacks an "
                                                           "explicit accept")
                            break
            record("acceptance-completeness", comp_ok, comp_detail)

    # --- ingest-review checks (MIG-PR4b): read-only semantic gate over the frozen bundle -----------------
    # CONDITIONALLY PRESENT. On an ordinary run (no ingest markers) all six are non-applicable PASSes. On an
    # ingest run whose bundle is absent/malformed/unclassifiable the whole ingest surface is a located FINDING
    # (fail-closed, never nothing-to-check). On a well-formed bundle, ingest-run-structure PASSes and the
    # shared validator performs the binding-digest recompute and correspondence for the other five.
    # Acceptance has a separate durable home and its own read-only checks.
    _ingest_ids = ("ingest-run-structure",) + _INGEST_CHECK_IDS
    if not ingest_is_run:
        for cid in _ingest_ids:
            record(cid, True, "not an ingest run")
    elif ingest_load_failed or ingest_bundle is None:
        record("ingest-run-structure", False, ingest_load_detail or "ingest bundle could not be loaded")
        for cid in _ingest_ids[1:]:
            record(cid, False, "ingest review bundle unavailable; cannot evaluate ({})".format(
                ingest_load_detail))
    else:
        record("ingest-run-structure", True, "")
        # The whole shared-validator step is fail-closed: a first-party contract violation (a raise, a
        # malformed return) becomes located FINDINGs, never an uncaught crash for a check_staged_run caller.
        try:
            ing_results = _verify_ingest_review_model(rd, ingest_bundle, run, report, inventory, homes)
        except Exception as exc:   # noqa: BLE001 - fail-closed; KeyboardInterrupt/SystemExit still propagate
            ing_results = {}
            _vfail = "ingest review validator raised ({!r})".format(exc)
        else:
            _vfail = ""
        for cid in _ingest_ids[1:]:
            if cid in ing_results:
                ok, detail = ing_results[cid]
                record(cid, ok, detail)
            else:
                record(cid, False, _vfail or "ingest check did not run (fail-closed)")

    if not ingest_is_run:
        for cid in _INGEST_ACCEPTANCE_CHECKS:
            record(cid, not durable_unavailable, durable_unavailable or "not an ingest run")
    elif legacy_generation:
        for cid in _INGEST_ACCEPTANCE_CHECKS:
            record(cid, True, "not applicable: homes generation 1 has no durable acceptance home")

    # --- Group C: the per-run transaction record (apply-promotion, PR-C) --------------------------------
    # The record lives OUTSIDE .working/ at the store-root `.aiqt/import/<run-id>/transaction.toml` (D2/D3:
    # it survives the terminal run-dir deletion and never enters the store containment walk). It is
    # CONDITIONALLY PRESENT (mirroring acceptance.json): absent = "run not yet applied", recorded as a PASS
    # for both Group C checks; present = validated for schema and state-machine consistency. The store root
    # is derived from the run dir (<store>/.working/imports/<run-id>), so the gate finds the record at its
    # relocated home without a separate argument.
    # Round-4 F3: the store root is opened BENEATH THE SUPPLIED RUN-DIR DESCRIPTOR (`../../..` from rd.fd: a
    # `..` component is never a symlink), so it is the physical store holding the directory the run-dir
    # classification observed, never a re-resolved string path; every store-relative control path below is
    # then read through _read_store_control (a no-follow descriptor walk on EVERY component, non-blocking,
    # fstat regular-file check). A symlinked parent component (to a directory in or outside the store), a
    # FIFO, or any non-regular entry is a located FINDING, never followed and never read as "absent".
    run_name = run_dir.name
    txn_rel = "{}/{}/{}".format(imp.IMPORT_OPS_REL, run_name, imp.TRANSACTION_NAME)
    arch_rel = "{}/{}/{}".format(imp.IMPORT_ARCHIVE_REL, run_name, imp.ACCEPTANCE_NAME)
    try:
        store_fd = os.open("../../..", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=rd.fd)
    except (OSError, ValueError) as exc:
        store_fd = None
        for cid in ("transaction-schema", "transaction-consistency"):
            record(cid, False, "cannot evaluate: cannot open the store root beneath the run dir no-follow "
                               "({})".format(exc))
    if store_fd is not None:
        jfd = None
        try:
            # Round-5 F1 + F2: the journal hierarchy and the archived acceptance path are classified ALWAYS,
            # independent of the record's presence or state (a FIFO, a dangling symlink, or an unreadable
            # directory there is a located FINDING even with no record); only the REQUIREMENT that the archived
            # acceptance EXIST depends on the state (>= published).
            ctl_problems = []
            journal_ok = True
            try:
                jfd = _classify_import_journal(store_fd, imp.IMPORT_JOURNAL_REL)
            except _GateError as exc:
                journal_ok = False
                ctl_problems.append("cannot evaluate: {}".format(exc))
            try:
                arch = _read_store_control(store_fd, arch_rel)
            except _GateError as exc:
                arch = None
                ctl_problems.append("cannot evaluate: archived acceptance ({})".format(exc))
            txn_err = ""
            try:
                txn_bytes = _read_store_control(store_fd, txn_rel)
            except _GateError as exc:
                txn_bytes, txn_err = None, str(exc)
            if txn_err:
                record("transaction-schema", False, "cannot evaluate: {}".format(txn_err))
                record("transaction-consistency", False, "; ".join(
                    ["cannot evaluate: transaction record unreadable ({})".format(txn_err)] + ctl_problems))
            elif txn_bytes is None:
                record("transaction-schema", True, "no transaction record (run not yet applied)")
                record("transaction-consistency", not ctl_problems,
                       "; ".join(ctl_problems) or "no transaction record (run not yet applied)")
            else:
                try:
                    txn = _parse_toml_bytes(txn_bytes, txn_rel)
                except _GateError as exc:
                    record("transaction-schema", False, str(exc))
                    record("transaction-consistency", False, "transaction record unreadable/unparseable")
                else:
                    # transaction-schema: shape of the record, validated by the SHARED
                    # imp._validate_transaction_record so the gate and the apply idempotency no-op cannot drift
                    # (PRC-F2). The run binding is the run dir name.
                    ts_ok, ts_detail = imp._validate_transaction_record(txn, run_name)
                    record("transaction-schema", ts_ok, ts_detail)

                    # transaction-consistency: the state-machine invariant that the attributed acceptance
                    # exists once the record's state reaches published (>= published). The apply relocates
                    # acceptance.json to the durable archive at `.aiqt/import-archive/<run-id>/acceptance.json`,
                    # so its presence there, as a REGULAR file read through the same contained no-follow walk,
                    # is the authoritative post-deletion witness of the review-to-apply binding.
                    # The archive path was already classified above (always); here only its REQUIRED
                    # presence at >= published is added, then the journal binding of the present record.
                    tc_problems = list(ctl_problems)
                    if isinstance(txn, dict) and txn.get("state") in ("published", "complete") \
                            and arch is None and not any("archived acceptance" in c for c in ctl_problems):
                        tc_problems.append("state is >= published but the archived acceptance.json is absent "
                                           "(acceptance must exist once state reaches published)")
                    if not isinstance(txn, dict):
                        tc_problems.append("transaction record is not a table")
                    elif journal_ok:   # a journal that failed classification is already a located problem
                        bind = _journal_binds_record(jfd, txn, txn_bytes, run_name, txn_rel)
                        if bind:
                            tc_problems.append(bind)
                    record("transaction-consistency", not tc_problems, "; ".join(tc_problems))
        finally:
            if jfd is not None:
                os.close(jfd)
            os.close(store_fd)

    # Registry reconciliation (guard-input-soundness): every declared check MUST have produced a result;
    # one that did not run is recorded as a FINDING, never a silent omission a caller could read as pass.
    for cid in EXPECTED_CHECKS:
        if cid not in results:
            record(cid, False, "check did not run (registry reconciliation: fail-closed)")

    return results


def _self_test():
    """Build synthetic staged runs and assert every registered check PASSes on a clean run and FINDINGs on
    its own discriminator (a single deliberate mutation), plus the scan-layer checks. Returns 0 clean, 1 on
    a failing assertion, 2 on a harness error (a fixture could not be built)."""
    import contextlib
    import datetime
    import io
    import json
    import shutil
    import tempfile

    import _opf_import as imp
    import _opf_emit

    failures = []

    def expect(label, cond):
        if not cond:
            failures.append(label)

    NOW = datetime.datetime(2026, 9, 9, 12, 0, 0, tzinfo=datetime.timezone.utc)

    def manifest_text():
        return "\n".join([
            "[opf]", 'standard = "opf"', 'spec_version = "{}"'.format(imp._opf_store.SUPPORTED_SPEC_VERSION),
            'layout = "inline"', 'posture = "required"', 'import_status = "none"',
            "", "[store]", 'sync_target = ""',
            "", "[modules]", "governance = true",
            "", "[types.backlog_item]", 'namespace = "BI"',
            "", "[vendors]", 'registered = []', "",
        ]) + "\n"

    base = Path(tempfile.mkdtemp(prefix="opf-import-gate-selftest-")).resolve()
    counter = [0]

    def build_store(sources):
        counter[0] += 1
        root = base / "case-{:02d}".format(counter[0])
        machine = root / ".working" / "toml"
        machine.mkdir(parents=True)
        (machine / "manifest.toml").write_text(manifest_text(), encoding="utf-8")
        (machine / "counters.toml").write_text("schema = 1\n\n[counters]\nBI = 0\nLF = 0\nWL = 0\n",
                                                encoding="utf-8")
        for rel, text in sources.items():
            (root / rel).write_text(text, encoding="utf-8")
        return root, machine

    def stage_clean():
        root, machine = build_store({"a.txt": "hello", "b.txt": "worldww"})
        pr = imp.plan_import(root, ["a.txt", "b.txt"], now=NOW, run_nonce="gate-nonce")
        if pr.verdict != 0 or not pr.run_id:
            raise OSError("harness: could not stage a clean run ({}: {})".format(pr.verdict, pr.findings))
        return machine.parent / "imports" / pr.run_id

    def accept_all_decisions(run_dir, verb="accept"):
        inv = _load_toml(run_dir / "inventory.toml")
        mp = _load_toml(run_dir / "mappings.toml")
        by_key = {(r["source_path"], tuple(r["span"])): r for r in mp["mapping"]}
        out = []
        for fr in inv["fragment"]:
            row = by_key[(fr["source_path"], tuple(fr["span"]))]
            out.append({"fragment_id": fr["fragment_id"], "decision": verb,
                        "origin": row["origin"], "proposed_state": row["state"]})
        return out

    def review_clean():
        """A clean staged run that has been reviewed: acceptance.json present, schema-valid, binding."""
        root, machine = build_store({"a.txt": "hello", "b.txt": "worldww"})
        pr = imp.plan_import(root, ["a.txt", "b.txt"], now=NOW, run_nonce="gate-nonce")
        if pr.verdict != 0 or not pr.run_id:
            raise OSError("harness: could not stage a clean run for review ({}: {})".format(
                pr.verdict, pr.findings))
        run_dir = machine.parent / "imports" / pr.run_id
        rr = imp.review_import(root, pr.run_id, actor="Gate Reviewer",
                               decisions=accept_all_decisions(run_dir), now=NOW)
        if rr.verdict != 0:
            raise OSError("harness: could not review a clean run ({}: {})".format(rr.verdict, rr.findings))
        return run_dir

    def review_model_proposal():
        """A reviewed run whose single mapping is a model_proposal resting in `ignored` (a quarantine AND
        resting state, so lf-bijection stays coherent), accepted. The report digest for the edited
        mappings.toml is refreshed so only the acceptance layer is under test."""
        root, machine = build_store({"a.txt": "hello"})
        pr = imp.plan_import(root, ["a.txt"], now=NOW, run_nonce="gate-nonce")
        if pr.verdict != 0 or not pr.run_id:
            raise OSError("harness: could not stage a model-proposal run ({}: {})".format(
                pr.verdict, pr.findings))
        run_dir = machine.parent / "imports" / pr.run_id
        mp = _load_toml(run_dir / "mappings.toml")
        mp["mapping"][0]["origin"] = imp._MODEL_PROPOSAL_ORIGIN
        mp["mapping"][0]["state"] = "ignored"
        (run_dir / "mappings.toml").write_text(_opf_emit.emit(mp), encoding="utf-8")
        rewrite_report_digest(run_dir, "mappings.toml")
        rr = imp.review_import(root, pr.run_id, actor="Gate Reviewer",
                               decisions=accept_all_decisions(run_dir), now=NOW)
        if rr.verdict != 0:
            raise OSError("harness: could not review a model-proposal run ({}: {})".format(
                rr.verdict, rr.findings))
        return run_dir

    def copy_run(run_dir):
        # Preserve the original imp-... run-id BASENAME (under a unique parent) so a clean copy still
        # passes staged-run-structure (run-id grammar) and report-schema (report.run_id == dir name);
        # otherwise every copy would fail those on the rename alone and no discriminator would isolate.
        parent = base / "mut-{:03d}".format(counter[0] * 100 + len(list(base.glob("mut-*"))))
        parent.mkdir()
        dest = parent / run_dir.name
        shutil.copytree(str(run_dir), str(dest))
        return dest

    def rewrite_report_digest(run_dir, rel_path):
        """After mutating an enumerated artefact, refresh its digest in report.toml so the
        artifact-digest-integrity check stays coherent and the TARGET check is the one that fires
        (Fable's keep-other-fields-coherent discriminator discipline)."""
        report = _load_toml(run_dir / "report.toml")
        new_sha = _sha256_hex((run_dir / rel_path).read_bytes())
        for entry in report.get("artifact", []):
            if entry.get("path") == rel_path:
                entry["sha256"] = new_sha
        (run_dir / "report.toml").write_text(_opf_emit.emit(report), encoding="utf-8")

    # Round-6 (test hermeticity): the genuine Group C controls drive a real in-process apply_import, so an
    # INHERITED journal crash-injection variable would kill this process mid-run; neutralize it for the whole
    # self-test and restore it in the finally (mirrors the module suite's R4-C2).
    import _journal as _journal_env
    _saved_kill_env = os.environ.pop(_journal_env.KILL_ENV, None)
    try:
        # --- clean run: every check PASSes -----------------------------------------------------------
        clean = stage_clean()
        clean_results = check_staged_run(clean)
        for cid, (ok, detail) in clean_results.items():
            expect("clean:{}:{}".format(cid, detail), ok)
        # The emitted check-set must be EXACTLY the declared registry (no omission, no stray): an omitted
        # check can never read as a clean pass.
        expect("clean-registry-complete", set(clean_results) == set(EXPECTED_CHECKS))
        # The MIG-PR4b artefact registry is itself well-formed: unique names, every mechanism from the closed
        # vocabulary, every owner a declared ingest check, and every envelope a (format attr, bool, bool) whose
        # format attribute names a real _opf_import constant (an unresolvable envelope would fail every
        # ingest run closed, so it is caught here rather than on a live run).
        _reg_names = [row[0] for row in _INGEST_ARTEFACT_REGISTRY]
        expect("ingest-artefact-registry-well-formed",
               len(set(_reg_names)) == len(_reg_names)
               and all(mech in _INGEST_MECHANISMS and owner in _INGEST_CHECK_IDS
                       and presence in ("always", "if-sources", "per-source")
                       and (env is None or (len(env) == 3 and (env[0] is None or isinstance(
                           getattr(imp, env[0], None), str)) and all(type(x) is bool for x in env[1:])))
                       for _name, mech, owner, env, presence in _INGEST_ARTEFACT_REGISTRY)
               and set(_INGEST_CHECK_IDS) <= set(EXPECTED_CHECKS))
        # Round-4 F5: every VALIDATE-mechanism TOML registry entry carries a CLOSED top-level shape (a RE-DERIVE
        # document is closed by its equality with the re-derivation), and no shape names an unregistered entry.
        expect("ingest-doc-keys-cover-validate-docs",
               {n for n, mech, _o, env, _p in _INGEST_ARTEFACT_REGISTRY if mech == _MECH_VALIDATE and env}
               == set(_INGEST_DOC_KEYS)
               and isinstance(imp._INGEST_REVIEW_KEYS, frozenset))

        # --- Group C discriminators (PR-C): the per-run transaction record at its relocated store-root home
        # `.aiqt/import/<run-id>/transaction.toml`, and the archived acceptance at `.aiqt/import-archive/
        # <run-id>/acceptance.json`. Each check PASSes when absent (not yet applied) and on a valid record,
        # and FINDINGs on a targeted mutation (change-carries-check flip evidence). -----------------------
        def write_txn(store, rid, **over):
            model = {"schema": 1, "format": imp.TRANSACTION_FORMAT, "run_id": rid, "state": "complete",
                     "txn_id": "t1", "plan_digest": "sha256:" + "0" * 64,
                     "inventory_digest": "sha256:" + "1" * 64, "allocation": {"LF": ["LF-1"]},
                     "restore_ref": {"txn_id": "t1", "journal_rel": imp.IMPORT_JOURNAL_REL,
                                     "observed_head": ""}}
            model.update(over)
            d = store / imp.IMPORT_OPS_REL / rid
            d.mkdir(parents=True, exist_ok=True)
            (d / imp.TRANSACTION_NAME).write_text(_opf_emit.emit(model), encoding="utf-8")

        def write_archived_acceptance(store, rid):
            d = store / imp.IMPORT_ARCHIVE_REL / rid
            d.mkdir(parents=True, exist_ok=True)
            (d / imp.ACCEPTANCE_NAME).write_text("{}\n", encoding="utf-8")

        import _journal

        def write_journal(store, rid, txn_id="t1", frames=("INTENT", "COMPLETE"), unit=None):
            rec_rel = "{}/{}/{}".format(imp.IMPORT_OPS_REL, rid, imp.TRANSACTION_NAME)
            sha = _sha256_hex((store / rec_rel).read_bytes())
            tdir = store / imp.IMPORT_JOURNAL_REL / txn_id
            (tdir / "preimages").mkdir(parents=True, exist_ok=True)
            objs = {"INTENT": {"txn": txn_id, "header": {"unit": unit or rid, "kind": "import-apply",
                                                         "plan_digest": "sha256:" + "0" * 64},
                               "ops": [{"op": "create", "path": rec_rel,
                                        "poststate": {"kind": "file", "mode": imp.FILE_MODE,
                                                      "content-sha256": sha}}]},
                    "COMPLETE": {"txn": txn_id}}
            (tdir / "frames.log").write_bytes(b"".join(
                _journal._frame(ft, json.dumps(objs[ft], sort_keys=True, separators=(",", ":")).encode())
                for ft in frames))

        # (a) no record present: both Group C checks PASS ("run not yet applied"), asserted on the clean run.
        expect("txn-absent-schema-pass", clean_results["transaction-schema"][0] is True)
        expect("txn-absent-consistency-pass", clean_results["transaction-consistency"][0] is True)
        # (b) a valid COMPLETE record + an archived acceptance: both PASS.
        tcb = stage_clean()
        write_txn(tcb.parent.parent.parent, tcb.name)
        write_archived_acceptance(tcb.parent.parent.parent, tcb.name)
        write_journal(tcb.parent.parent.parent, tcb.name)
        tcb_res = check_staged_run(tcb)
        expect("txn-valid-schema-pass", tcb_res["transaction-schema"][0] is True)
        expect("txn-valid-consistency-pass", tcb_res["transaction-consistency"][0] is True)
        # (c) a malformed record (wrong format): transaction-schema FINDING.
        tcc = stage_clean()
        write_txn(tcc.parent.parent.parent, tcc.name, format="wrong/format/v9")
        write_archived_acceptance(tcc.parent.parent.parent, tcc.name)
        expect("disc-txn-schema", check_staged_run(tcc)["transaction-schema"][0] is False)
        # (d) state >= published but the archived acceptance is absent: asserted below as "disc-txn-consistency"
        # on a GENUINE, journal-bound applied store with ONLY the archive removed (round-6 F3: the former
        # fixture, a journal-less published record, failed on the journal bind whether or not the archive
        # guard existed, so it no longer discriminated).
        # Round-4 F3 (STORE-RELATIVE CONTROL PATHS READ THROUGH DESCRIPTOR CONTAINMENT): each vector below
        # carries a VALID record and a VALID archived acceptance reachable only by FOLLOWING a link (or a
        # non-regular entry in place), so the pre-fix string-path lstat / _load_toml / is_file gate PASSED the
        # first three; every one is now a located FINDING, never followed and never read as "absent".
        outside = base / "r4f3-outside"
        # (e) `.aiqt/import` and `.aiqt/import-archive` symlinked to directories OUTSIDE the store.
        tce = stage_clean()
        store_e = tce.parent.parent.parent
        write_txn(outside / "ops", tce.name)
        write_archived_acceptance(outside / "arc", tce.name)
        (store_e / ".aiqt").mkdir()
        os.symlink(str(outside / "ops" / imp.IMPORT_OPS_REL), str(store_e / imp.IMPORT_OPS_REL))
        os.symlink(str(outside / "arc" / imp.IMPORT_ARCHIVE_REL), str(store_e / imp.IMPORT_ARCHIVE_REL))
        tce_res = check_staged_run(tce)
        expect("pr4b-disc-r4f3-txn-parent-symlink-outside-store",
               tce_res["transaction-schema"][0] is False and tce_res["transaction-consistency"][0] is False
               and "no-follow" in tce_res["transaction-schema"][1])
        # (f) the per-run `<run-id>` directory a symlink to another directory INSIDE the store.
        tcf = stage_clean()
        store_f = tcf.parent.parent.parent
        write_txn(store_f / "r4f3-elsewhere", tcf.name)
        write_archived_acceptance(store_f, tcf.name)
        (store_f / imp.IMPORT_OPS_REL).mkdir(parents=True)
        os.symlink(str(store_f / "r4f3-elsewhere" / imp.IMPORT_OPS_REL / tcf.name),
                   str(store_f / imp.IMPORT_OPS_REL / tcf.name))
        tcf_res = check_staged_run(tcf)
        expect("pr4b-disc-r4f3-txn-parent-symlink-in-store",
               tcf_res["transaction-schema"][0] is False and "no-follow" in tcf_res["transaction-schema"][1])
        # (g) a valid record whose archived acceptance.json is a symlink to a regular file outside the store.
        tcg = stage_clean()
        store_g = tcg.parent.parent.parent
        write_txn(store_g, tcg.name)
        write_journal(store_g, tcg.name)   # round-6 F3 sweep: journal-bound, so ONLY the archive can fail
        (store_g / imp.IMPORT_ARCHIVE_REL / tcg.name).mkdir(parents=True)
        os.symlink(str(outside / "arc" / imp.IMPORT_ARCHIVE_REL / tce.name / imp.ACCEPTANCE_NAME),
                   str(store_g / imp.IMPORT_ARCHIVE_REL / tcg.name / imp.ACCEPTANCE_NAME))
        tcg_res = check_staged_run(tcg)
        expect("pr4b-disc-r4f3-archived-acceptance-symlink",
               tcg_res["transaction-schema"][0] is True and tcg_res["transaction-consistency"][0] is False
               and "not a regular file" in tcg_res["transaction-consistency"][1])
        # (h) the archived acceptance.json a FIFO: a located non-regular FINDING (pre-fix it read as "absent").
        tch = stage_clean()
        store_h = tch.parent.parent.parent
        write_txn(store_h, tch.name)
        write_journal(store_h, tch.name)   # round-6 F3 sweep: journal-bound, so ONLY the archive can fail
        (store_h / imp.IMPORT_ARCHIVE_REL / tch.name).mkdir(parents=True)
        os.mkfifo(str(store_h / imp.IMPORT_ARCHIVE_REL / tch.name / imp.ACCEPTANCE_NAME))
        tch_res = check_staged_run(tch)
        expect("pr4b-disc-r4f3-archived-acceptance-fifo",
               tch_res["transaction-consistency"][0] is False
               and "not a regular file" in tch_res["transaction-consistency"][1])
        # (i) transaction.toml itself a FIFO: a located non-regular FINDING, never a blocking read.
        tci = stage_clean()
        store_i = tci.parent.parent.parent
        (store_i / imp.IMPORT_OPS_REL / tci.name).mkdir(parents=True)
        os.mkfifo(str(store_i / imp.IMPORT_OPS_REL / tci.name / imp.TRANSACTION_NAME))
        tci_res = check_staged_run(tci)
        expect("pr4b-disc-r4f3-txn-fifo",
               tci_res["transaction-schema"][0] is False and "not a regular file" in tci_res["transaction-schema"][1])

        # Round-5 (codex delta review, F1 + F2): the JOURNAL hierarchy `.aiqt/import/journal` and the ARCHIVE
        # path are classified ALWAYS, through the same contained no-follow walk, independent of the transaction
        # record's presence or state; only the REQUIREMENT that the archived acceptance exist depends on the
        # state (>= published), and a present record must be bound by its own journal transaction (the INTENT
        # names this run, and a complete record's bytes hash to the journal-recorded create). Each vector below
        # PASSED all checks before the fix (a complete record + a regular archived acceptance included).
        def applied(**over):
            """A staged run with a valid record (default complete), its bound journal, and a regular
            archived acceptance: both Group C checks PASS until one mutation is applied."""
            run = stage_clean()
            store = run.parent.parent.parent
            write_txn(store, run.name, **over)
            write_journal(store, run.name, frames=("INTENT", "COMPLETE") if over.get("state", "complete")
                          == "complete" else ("INTENT",))
            write_archived_acceptance(store, run.name)
            return run, store

        def txc(run):
            res = check_staged_run(run)
            return res["transaction-schema"], res["transaction-consistency"]

        r5b, _s = applied()
        expect("pr4b-r5-applied-clean", all(ok for ok, _d in txc(r5b)))
        # Round-6 F1: a PREPARED record is never a resting state (the producer writes only complete, apply
        # refuses any other), so the former "applied-prepared-clean" control is now a discriminator that EXPECTS
        # the located non-complete-state FINDING (here over an open, INTENT-only journal).
        r5p, _s = applied(state="prepared")
        _ts, tc = txc(r5p)
        expect("pr4b-r6-disc-prepared-open-journal", tc[0] is False and "is not 'complete'" in tc[1])
        jrel = imp.IMPORT_JOURNAL_REL
        # The classification (not the record bind) is the mechanism under test in the vectors below, so each
        # asserts the classifier's own located prefix ("cannot evaluate: import journal"), never the bare word
        # "journal" that the bind's messages also carry (round-6 F3 masking sweep).
        jcls = "cannot evaluate: import journal"
        # F1: the journal root a FIFO / a dangling symlink / an unreadable directory; an entry beneath it a
        # FIFO; the record's own frames.log a FIFO.
        for label, mutate in (
                ("fifo", lambda s: (shutil.rmtree(str(s / jrel)), os.mkfifo(str(s / jrel)))),
                ("dangling-symlink", lambda s: (shutil.rmtree(str(s / jrel)),
                                                os.symlink(str(s / "r5-nowhere"), str(s / jrel)))),
                ("entry-fifo", lambda s: os.mkfifo(str(s / jrel / "lock"))),
                ("frames-fifo", lambda s: ((s / jrel / "t1" / "frames.log").unlink(),
                                           os.mkfifo(str(s / jrel / "t1" / "frames.log")))),
                ("preimage-symlink", lambda s: os.symlink("/etc/hostname",
                                                          str(s / jrel / "t1" / "preimages" / "0")))):
            r5, s5 = applied()
            mutate(s5)
            _ts, tc = txc(r5)
            expect("pr4b-disc-r5-journal-" + label, tc[0] is False and jcls in tc[1])
        r5u, s5u = applied()
        os.chmod(str(s5u / jrel), 0)
        try:
            _ts, tc = txc(r5u)
            expect("pr4b-disc-r5-journal-unreadable", os.geteuid() == 0 or (tc[0] is False and jcls in tc[1]))
        finally:
            os.chmod(str(s5u / jrel), 0o700)
        # F1 with NO transaction record: a FIFO journal root is still a located FINDING.
        r5n = stage_clean()
        (r5n.parent.parent.parent / imp.IMPORT_OPS_REL).mkdir(parents=True)
        os.mkfifo(str(r5n.parent.parent.parent / jrel))
        _ts, tc = txc(r5n)
        expect("pr4b-disc-r5-journal-fifo-no-txn", tc[0] is False and jcls in tc[1])
        # Round-6 F2: the RESERVED journal-root names are dispatched before the transaction-directory branch and
        # must be regular, singly-linked, openable files (what the engine's own lock logic accepts). A directory
        # at lock / lock.break, a hardlinked lock, and a mode-000 (unopenable) lock were each classified clean
        # before the fix; a genuine regular lock + lock.break pair still passes (positive control).
        for label, mutate in (
                ("lock-dir", lambda s: (s / jrel / "lock").mkdir()),
                ("lock-break-dir", lambda s: (s / jrel / "lock.break").mkdir()),
                ("lock-break-fifo", lambda s: os.mkfifo(str(s / jrel / "lock.break"))),
                ("lock-hardlink", lambda s: ((s / "r6-victim").write_text("{}"),
                                             os.link(str(s / "r6-victim"), str(s / jrel / "lock"))))):
            r6, s6 = applied()
            mutate(s6)
            _ts, tc = txc(r6)
            expect("pr4b-r6-disc-reserved-" + label,
                   tc[0] is False and jcls in tc[1] and "reserved entry" in tc[1])
        r6m, s6m = applied()
        (s6m / jrel / "lock").write_text("{}")
        os.chmod(str(s6m / jrel / "lock"), 0)
        try:
            _ts, tc = txc(r6m)
            expect("pr4b-r6-disc-reserved-lock-mode-000", os.geteuid() == 0 or (
                tc[0] is False and "reserved entry" in tc[1] and "cannot be opened" in tc[1]))
        finally:
            os.chmod(str(s6m / jrel / "lock"), 0o600)
        r6c, s6c = applied()
        (s6c / jrel / "lock").write_text('{"pid": 1}')
        (s6c / jrel / "lock.break").write_bytes(b"")
        expect("pr4b-r6-reserved-regular-pair-clean", all(ok for ok, _d in txc(r6c)))
        # F1 (the control files relevant to the state): a complete record whose journal is absent, open, bound
        # to another run, or whose bytes no longer hash to the journal-recorded create. Each asserts its OWN
        # located reason (round-6 F3 masking sweep).
        r5a, s5a = applied()
        shutil.rmtree(str(s5a / jrel / "t1"))
        _ts, tc = txc(r5a)
        expect("pr4b-disc-r5-journal-txn-absent", tc[0] is False and "journal transaction 't1'" in tc[1])
        r5o, s5o = applied()
        write_journal(s5o, r5o.name, frames=("INTENT",))
        _ts, tc = txc(r5o)
        expect("pr4b-disc-r5-journal-not-terminal", tc[0] is False and "not terminal COMPLETE" in tc[1])
        r5f, s5f = applied()
        write_journal(s5f, r5f.name, unit="imp-20260101T000000Z-0000000000000000")
        _ts, tc = txc(r5f)
        expect("pr4b-disc-r5-journal-foreign-run", tc[0] is False and "does not bind this run" in tc[1])
        r5h, s5h = applied()
        rec5 = s5h / imp.IMPORT_OPS_REL / r5h.name / imp.TRANSACTION_NAME
        rec5.write_bytes(rec5.read_bytes() + b"\n")
        _ts, tc = txc(r5h)
        expect("pr4b-disc-r5-journal-record-hash", tc[0] is False and "do not hash" in tc[1])
        # Round-6 F1 (the reviewer's reproduction): the passing complete-record fixture with its state
        # DOWNGRADED to published / prepared and its allocation rewritten to LF-999, the INTENT + COMPLETE
        # journal left unchanged. Before the fix the bind returned early for any non-complete record, so both
        # passed every check.
        for st in ("published", "prepared"):
            r6d, s6d = applied()
            write_txn(s6d, r6d.name, state=st, allocation={"LF": ["LF-999"]})
            _ts, tc = txc(r6d)
            expect("pr4b-r6-disc-downgraded-" + st + "-terminal-journal",
                   tc[0] is False and "is not 'complete'" in tc[1] and "is COMPLETE" in tc[1])
        # F2: the archive path is inspected whatever the transaction state: a dangling acceptance symlink with
        # NO record, the per-run archive directory a FIFO with NO record, a FIFO acceptance with NO record (a
        # regular per-run archive directory; no state requires the acceptance, yet its type is still checked).
        arel = imp.IMPORT_ARCHIVE_REL
        r5d = stage_clean()
        s5d = r5d.parent.parent.parent
        (s5d / arel / r5d.name).mkdir(parents=True)
        os.symlink(str(s5d / "r5-nowhere"), str(s5d / arel / r5d.name / imp.ACCEPTANCE_NAME))
        _ts, tc = txc(r5d)
        expect("pr4b-disc-r5-archive-dangling-no-txn", tc[0] is False and "archived acceptance" in tc[1])
        r5r = stage_clean()
        s5r = r5r.parent.parent.parent
        (s5r / arel).mkdir(parents=True)
        os.mkfifo(str(s5r / arel / r5r.name))
        _ts, tc = txc(r5r)
        expect("pr4b-disc-r5-archive-rundir-fifo-no-txn", tc[0] is False and "archived acceptance" in tc[1])
        r5q = stage_clean()
        s5q = r5q.parent.parent.parent
        (s5q / arel / r5q.name).mkdir(parents=True)
        os.mkfifo(str(s5q / arel / r5q.name / imp.ACCEPTANCE_NAME))
        _ts, tc = txc(r5q)
        expect("pr4b-disc-r5-archive-fifo-no-txn", tc[0] is False and "archived acceptance" in tc[1]
               and "not a regular file" in tc[1])

        # Round-6 (GENUINE controls): every Group C state the gate accepts, and the interrupted state it must
        # refuse, built by the REAL producer: plan_import -> review_import -> apply_import over a doctor-
        # composable store (the module suite's build_apply_store shape), never a hand-written record/journal.
        # The genuine apply deletes the staging run dir as its terminal journaled op, so the gate (addressed by
        # a run dir) can see a genuine complete record only beside a RESTORED pre-apply copy of that run dir;
        # every Group C artefact (record, journal, archive) is the producer's own.
        import _opf_init
        import _opf_views
        import subprocess

        def build_apply_store():
            counter[0] += 1
            root = base / "genuine-{:02d}".format(counter[0])
            mdir = root / ".working" / "toml"
            mdir.mkdir(parents=True)
            (mdir / "manifest.toml").write_text(_opf_emit.emit_checked(_opf_init._manifest_model()),
                                                encoding="utf-8")
            nss = list(imp._opf_store.BASELINE_TYPES.values()) + ["LF"]
            (mdir / "counters.toml").write_text(_opf_emit.emit_checked(
                {"schema": imp.SCHEMA, "counters": {ns: 0 for ns in nss}}), encoding="utf-8")
            (mdir / "version.toml").write_text(_opf_init.build_version(), encoding="utf-8")
            (mdir / "worklog.toml").write_text(_opf_init.build_worklog(), encoding="utf-8")
            for t in _opf_init.INDEX_TYPES:
                (mdir / "{}.index.toml".format(t)).write_text(_opf_init.build_index(t), encoding="utf-8")
            (mdir / "legacy_fragment.index.toml").write_text(
                _opf_emit.emit_checked({"schema": imp.SCHEMA, "record": []}), encoding="utf-8")
            (root / "CHANGELOG.md").write_text("# Changelog\n", encoding="utf-8")
            (root / "a.txt").write_bytes(b"genuine legacy source body")
            _opf_views._render_resolved_store(root, imp._opf_store.resolve_store(root), False,
                                              capture={"preimages": {}})
            return root, mdir

        def genuine_reviewed():
            root, mdir = build_apply_store()
            pr = imp.plan_import(root, ["a.txt"], now=NOW, run_nonce="gate-genuine")
            if pr.verdict != 0 or not pr.run_id:
                raise OSError("harness: genuine plan failed ({}: {})".format(pr.verdict, pr.findings))
            run_dir = mdir.parent / "imports" / pr.run_id
            rr = imp.review_import(root, pr.run_id, actor="Gate Reviewer",
                                   decisions=accept_all_decisions(run_dir), now=NOW)
            if rr.verdict != 0:
                raise OSError("harness: genuine review failed ({}: {})".format(rr.verdict, rr.findings))
            keep = base / "genuine-keep-{:02d}".format(counter[0])
            shutil.copytree(str(run_dir), str(keep))
            return root, run_dir, keep

        g_root, g_run, g_keep = genuine_reviewed()
        g_ap = imp.apply_import(g_root, g_run.name, now=NOW)
        if not (g_ap.verdict == 0 and g_ap.promoted is True and g_ap.outcome == "promoted"
                and not g_run.exists()):
            raise OSError("harness: genuine apply did not promote ({}: {})".format(g_ap.verdict, g_ap.findings))
        shutil.copytree(str(g_keep), str(g_run))

        def genuine_clone():
            counter[0] += 1
            dst = base / "genuine-clone-{:02d}".format(counter[0])
            shutil.copytree(str(g_root), str(dst), symlinks=True)
            return dst / g_run.relative_to(g_root), dst

        g_res = check_staged_run(g_run)
        expect("pr4b-r6-genuine-complete-all-pass", all(ok for ok, _d in g_res.values()))
        g_rec = g_root / imp.IMPORT_OPS_REL / g_run.name / imp.TRANSACTION_NAME
        expect("pr4b-r6-genuine-complete-is-complete",
               _load_toml(g_rec).get("state") == "complete" and g_res["transaction-consistency"][0] is True)
        # F3: "disc-txn-consistency" on that VALID journal-bound baseline, removing ONLY the archived acceptance.
        g3, g3s = genuine_clone()
        (g3s / imp.IMPORT_ARCHIVE_REL / g3.name / imp.ACCEPTANCE_NAME).unlink()
        _ts, tc = txc(g3)
        expect("disc-txn-consistency", tc[0] is False and "archived acceptance.json is absent" in tc[1])
        # F1 on the genuine store: the producer's complete record downgraded (published / prepared, allocation
        # LF-999), the genuine terminal journal unchanged.
        for st in ("published", "prepared"):
            g1, g1s = genuine_clone()
            rec_path = g1s / imp.IMPORT_OPS_REL / g1.name / imp.TRANSACTION_NAME
            rec = _load_toml(rec_path)
            rec["state"] = st
            rec["allocation"] = {"LF": ["LF-999"]}
            rec_path.write_text(_opf_emit.emit(rec), encoding="utf-8")
            _ts, tc = txc(g1)
            expect("pr4b-r6-disc-genuine-downgraded-" + st,
                   tc[0] is False and "is not 'complete'" in tc[1] and "is COMPLETE" in tc[1])

        # Round-7 (codex round-6 MEDIUM): the INTENT ops touching the record path must be EXACTLY the one
        # create the producer journals (kind "file", FILE_MODE, content-sha256 of the record bytes). The
        # pre-fix bind took the FIRST create/write op for the path and checked only its hash, so each vector
        # below (the genuine terminal journal re-encoded with ONE INTENT mutation, record untouched) PASSED
        # every check. The re-encode-only control proves the harness re-framing alone changes nothing.
        def mutate_genuine_intent(mutate):
            gm, gms = genuine_clone()
            m_rec_rel = "{}/{}/{}".format(imp.IMPORT_OPS_REL, gm.name, imp.TRANSACTION_NAME)
            m_txn_id = _load_toml(gms / m_rec_rel).get("txn_id")
            m_jfd = os.open(str(gms / imp.IMPORT_JOURNAL_REL), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                m_frames, m_torn, _m_gl = _journal.read_frames(m_jfd, m_txn_id)
            finally:
                os.close(m_jfd)
            if m_torn or [t for t, _o in m_frames] != [_journal.F_INTENT, _journal.F_COMPLETE]:
                raise OSError("harness: the genuine journal is not a clean [INTENT, COMPLETE] sequence")
            mutate(m_frames[0][1]["ops"], m_rec_rel)
            (gms / imp.IMPORT_JOURNAL_REL / m_txn_id / "frames.log").write_bytes(b"".join(
                _journal._frame(ft, json.dumps(obj, sort_keys=True, separators=(",", ":"),
                                               ensure_ascii=True).encode()) for ft, obj in m_frames))
            return txc(gm)

        def rec_op_of(ops, rel):
            return [o for o in ops if o.get("path") == rel][0]

        _ts, tc = mutate_genuine_intent(lambda ops, rel: None)
        expect("pr4b-r7-genuine-reencoded-intent-clean", _ts[0] is True and tc[0] is True)
        r7_vectors = (
            # (a) the record create's poststate kind "file" -> "absent", its hash kept.
            ("create-kind-absent", lambda ops, rel: rec_op_of(ops, rel)["poststate"].update(kind="absent")),
            # (b) a second, later write op for the record path with a different poststate hash.
            ("second-write", lambda ops, rel: ops.append(
                {"op": "write", "path": rel, "poststate": {"kind": "file", "content-sha256": "e" * 64}})),
            ("duplicate-create", lambda ops, rel: ops.append(json.loads(json.dumps(rec_op_of(ops, rel))))),
            ("later-remove", lambda ops, rel: ops.append(
                {"op": "remove", "path": rel, "poststate": {"kind": "absent"}})),
            ("create-as-write", lambda ops, rel: rec_op_of(ops, rel).update(op="write")),
            ("create-mode-changed", lambda ops, rel: rec_op_of(ops, rel)["poststate"].update(mode=0o600)),
            ("create-mode-dropped", lambda ops, rel: rec_op_of(ops, rel)["poststate"].pop("mode")),
            ("create-extra-poststate-key", lambda ops, rel: rec_op_of(ops, rel)["poststate"].update(x=1)),
            ("alias-path-remove", lambda ops, rel: ops.append(
                {"op": "remove", "path": rel.replace("/", "/./", 1), "poststate": {"kind": "absent"}})),
            ("create-dropped", lambda ops, rel: ops.remove(rec_op_of(ops, rel))),
        )
        for vname, vmut in r7_vectors:
            _ts, tc = mutate_genuine_intent(vmut)
            expect("pr4b-r7-disc-genuine-intent-" + vname,
                   _ts[0] is True and tc[0] is False and "transaction record" in tc[1])

        # A GENUINE interrupted promotion: a real apply_import killed (the journal's own crash-injection point)
        # right after it CREATED this run's transaction record and before the terminal run-dir deletion, so the
        # producer's complete record, archive, lock, and open INTENT journal all exist beside the live run dir.
        # The gate must refuse it (not terminal COMPLETE); after a genuine recover (rolled back) the run is
        # un-applied again and both Group C checks PASS (the genuine rolled-back control).
        c_root, c_run, _c_keep = genuine_reviewed()
        c_rec_rel = "{}/{}/{}".format(imp.IMPORT_OPS_REL, c_run.name, imp.TRANSACTION_NAME)
        child = "\n".join([
            "import datetime, os, sys",
            "from pathlib import Path",
            "sys.path.insert(0, sys.argv[1])",
            "import _opf_import as imp",
            "import _journal",
            "original = imp._build_publication_ops",
            "def arm(*args, **kwargs):",
            "    ops, content = original(*args, **kwargs)",
            "    index = next(i for i, op in enumerate(ops) if op['path'] == sys.argv[4])",
            "    os.environ[_journal.KILL_ENV] = 'after-apply-{}'.format(index)",
            "    return ops, content",
            "imp._build_publication_ops = arm",
            "result = imp.apply_import(Path(sys.argv[2]), sys.argv[3],",
            "                          now=datetime.datetime.fromisoformat(sys.argv[5]))",
            "raise SystemExit(result.verdict)",
        ])
        c_env = {k: v for k, v in os.environ.items() if k != _journal.KILL_ENV}
        c_cp = subprocess.run([sys.executable, "-I", "-B", "-c", child, str(Path(__file__).resolve().parent),
                               str(c_root), c_run.name, c_rec_rel, NOW.isoformat()],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=300, env=c_env)
        if c_cp.returncode != 137 or not (c_root / c_rec_rel).is_file() or not c_run.is_dir():
            raise OSError("harness: genuine crash did not stop after the record create (rc {}: {!r})".format(
                c_cp.returncode, c_cp.stderr[-400:]))
        _ts, tc = txc(c_run)
        expect("pr4b-r6-genuine-interrupted-refused",
               _ts[0] is True and tc[0] is False and "open (non-terminal)" in tc[1]
               and "reserved entry" not in tc[1] and _load_toml(c_root / c_rec_rel).get("state") == "complete")
        # F1 over the genuine OPEN journal: the producer's record downgraded to prepared is still refused.
        counter[0] += 1
        c1s = base / "genuine-clone-{:02d}".format(counter[0])
        shutil.copytree(str(c_root), str(c1s), symlinks=True)
        c1 = c1s / c_run.relative_to(c_root)
        c1_rec = c1s / c_rec_rel
        c1_model = _load_toml(c1_rec)
        c1_model["state"] = "prepared"
        c1_rec.write_text(_opf_emit.emit(c1_model), encoding="utf-8")
        _ts, tc = txc(c1)
        expect("pr4b-r6-disc-genuine-prepared-open-journal",
               tc[0] is False and "is not 'complete'" in tc[1] and "open (non-terminal)" in tc[1])
        c_jroot = c_root / imp.IMPORT_JOURNAL_REL
        c_rfd = imp._opf_store._open_store_root_fd(c_root, False)
        try:
            c_jfd = _journal.open_journal_root_fd(c_rfd, imp.IMPORT_JOURNAL_REL)
            try:
                if imp._claim_apply_lock(c_jroot, c_jfd, c_rfd) != "acquired":
                    raise OSError("harness: genuine recover could not claim the dead owner's lock")
                _journal.release_lock(c_jroot)
                c_states = [_journal.classify_state(c_jfd, t) for t in _journal._journal_txn_dirs(c_jfd, c_jroot)]
            finally:
                os.close(c_jfd)
        finally:
            os.close(c_rfd)
        c_res = check_staged_run(c_run)
        expect("pr4b-r6-genuine-rolled-back-all-pass",
               c_states == ["rolled-back"] and not (c_root / c_rec_rel).exists()
               and all(ok for ok, _d in c_res.values()))

        # --- one discriminator per check: a single mutation flips its TARGET check to FINDING ---------
        # structure: remove plan.toml.
        m = copy_run(clean)
        (m / "plan.toml").unlink()
        expect("disc-structure", check_staged_run(m)["staged-run-structure"][0] is False)

        # structure (parse): a present-but-UNPARSEABLE plan.toml fails closed (not merely is_file()).
        m = copy_run(clean)
        (m / "plan.toml").write_bytes(b"not valid toml [")
        expect("disc-structure-malformed-plan", check_staged_run(m)["staged-run-structure"][0] is False)

        # N6: a run dir renamed with a TRAILING NEWLINE must fail staged-run-structure. The run-id grammar
        # is \Z-anchored, not $ (which also matches just before a final "\n"), so "imp-...\n" is not a run
        # id. (report-schema also fires on the changed dir name; the target here is staged-run-structure.)
        m = copy_run(clean)
        nl_dir = m.parent / (m.name + "\n")
        m.rename(nl_dir)
        expect("disc-structure-trailing-newline",
               check_staged_run(nl_dir)["staged-run-structure"][0] is False)

        # report-schema: flip verdict to 1 (report.toml is not in its own artefact list, so the digest
        # check stays green).
        m = copy_run(clean)
        rep = _load_toml(m / "report.toml")
        rep["verdict"] = 1
        (m / "report.toml").write_text(_opf_emit.emit(rep), encoding="utf-8")
        expect("disc-report-schema", check_staged_run(m)["report-schema"][0] is False)

        # report-schema (strict-int verdict): a report.toml verdict of `false` (a bool) must NOT pass via
        # Python's `False == 0`. The `type(...) is int` guard (bool excluded) makes it a FINDING, so a
        # not-promotion-ready run cannot clear all 16 checks. report.toml is not in its own artefact list, so
        # the digest check stays green and only report-schema fires.
        m = copy_run(clean)
        rep = _load_toml(m / "report.toml")
        rep["verdict"] = False
        (m / "report.toml").write_text(_opf_emit.emit(rep), encoding="utf-8")
        expect("disc-report-schema-bool-verdict", check_staged_run(m)["report-schema"][0] is False)

        # report-schema (strict-int schema, R5-F2): a report.toml schema of `true` (a bool) must NOT pass via
        # Python's `True == 1`. The `type(...) is int` guard (bool excluded) makes it a FINDING, the class
        # sibling of the bool-verdict discriminator above. report.toml is not in its own artefact list, so the
        # digest check stays green and only report-schema fires.
        m = copy_run(clean)
        rep = _load_toml(m / "report.toml")
        rep["schema"] = True
        (m / "report.toml").write_text(_opf_emit.emit(rep), encoding="utf-8")
        expect("disc-report-schema-bool-schema", check_staged_run(m)["report-schema"][0] is False)

        # artifact-digest-integrity: append an inert TOML comment to run.toml WITHOUT refreshing its
        # recorded digest (run.toml still parses identically, so only the digest check fires).
        m = copy_run(clean)
        with open(m / "run.toml", "ab") as fh:
            fh.write(b"\n# tampered\n")
        expect("disc-artifact-digest", check_staged_run(m)["artifact-digest-integrity"][0] is False)

        # artifact-digest-integrity (non-list shape, R5-F1): a report.toml `artifact` of a NON-LIST type
        # (int or bool) must be a located FINDING, never an uncaught TypeError from iterating a
        # non-iterable. The gate must fail closed for ALL callers so the review->check_staged_run
        # delegation cannot crash instead of returning verdict 2. report.toml is the marker, so
        # report-schema ALSO fires (its artifact must be a list) - that is fine; the target here is that
        # artifact-digest-integrity is False AND that check_staged_run itself did not raise.
        for bad_artifact in (1, False):
            m = copy_run(clean)
            rep = _load_toml(m / "report.toml")
            rep["artifact"] = bad_artifact
            (m / "report.toml").write_text(_opf_emit.emit(rep), encoding="utf-8")
            try:
                res = check_staged_run(m)
            except Exception as exc:  # the guard makes this unreachable; without it the loop raises
                expect("disc-artifact-non-list-noraise:{!r}:{!r}".format(bad_artifact, exc), False)
            else:
                expect("disc-artifact-non-list:{!r}".format(bad_artifact),
                       res["artifact-digest-integrity"][0] is False)

        # mapping-totality: truncate one span's end so its source is no longer tiled (row count and states
        # unchanged, so bijection and vocab stay coherent); refresh the mappings.toml digest.
        m = copy_run(clean)
        mp = _load_toml(m / "mappings.toml")
        mp["mapping"][0]["span"] = [0, mp["mapping"][0]["span"][1] - 1]
        (m / "mappings.toml").write_text(_opf_emit.emit(mp), encoding="utf-8")
        rewrite_report_digest(m, "mappings.toml")
        expect("disc-mapping-totality", check_staged_run(m)["mapping-totality"][0] is False)

        # mapping-state-vocab: set a row state outside the vocabulary; refresh the digest.
        m = copy_run(clean)
        mp = _load_toml(m / "mappings.toml")
        mp["mapping"][0]["state"] = "renamed"
        (m / "mappings.toml").write_text(_opf_emit.emit(mp), encoding="utf-8")
        rewrite_report_digest(m, "mappings.toml")
        expect("disc-state-vocab", check_staged_run(m)["mapping-state-vocab"][0] is False)

        # mapping-origin-vocab: delete one row's origin (state/spans unchanged, so state-vocab, totality and
        # bijection stay coherent); refresh the digest so only the origin-vocab check fires.
        m = copy_run(clean)
        mp = _load_toml(m / "mappings.toml")
        del mp["mapping"][0]["origin"]
        (m / "mappings.toml").write_text(_opf_emit.emit(mp), encoding="utf-8")
        rewrite_report_digest(m, "mappings.toml")
        expect("disc-origin-vocab-deleted", check_staged_run(m)["mapping-origin-vocab"][0] is False)

        # mapping-origin-vocab: set a row's origin outside the provenance vocabulary; refresh the digest.
        m = copy_run(clean)
        mp = _load_toml(m / "mappings.toml")
        mp["mapping"][0]["origin"] = "guessed"
        (m / "mappings.toml").write_text(_opf_emit.emit(mp), encoding="utf-8")
        rewrite_report_digest(m, "mappings.toml")
        expect("disc-origin-vocab-bad", check_staged_run(m)["mapping-origin-vocab"][0] is False)

        # N4: an UNHASHABLE mapping origin/state ([]) is a located FINDING, never a TypeError at the
        # membership test. origin=[] is the real crash flip (imp._ORIGIN_SET is a frozenset); state=[] is
        # the defensive sibling (imp.MAPPING_STATES is a tuple). Refresh the digest so the vocab check fires.
        m = copy_run(clean)
        mp = _load_toml(m / "mappings.toml")
        mp["mapping"][0]["origin"] = []
        (m / "mappings.toml").write_text(_opf_emit.emit(mp), encoding="utf-8")
        rewrite_report_digest(m, "mappings.toml")
        expect("disc-origin-vocab-unhashable", check_staged_run(m)["mapping-origin-vocab"][0] is False)

        m = copy_run(clean)
        mp = _load_toml(m / "mappings.toml")
        mp["mapping"][0]["state"] = []
        (m / "mappings.toml").write_text(_opf_emit.emit(mp), encoding="utf-8")
        rewrite_report_digest(m, "mappings.toml")
        expect("disc-state-vocab-unhashable", check_staged_run(m)["mapping-state-vocab"][0] is False)

        # lf-bijection: delete one legacy_fragment record; refresh the digest.
        m = copy_run(clean)
        lf = _load_toml(m / "fragments" / "legacy_fragment.index.toml")
        lf["record"] = lf["record"][:-1]
        (m / "fragments" / "legacy_fragment.index.toml").write_text(_opf_emit.emit(lf), encoding="utf-8")
        rewrite_report_digest(m, "fragments/legacy_fragment.index.toml")
        expect("disc-lf-bijection", check_staged_run(m)["lf-bijection"][0] is False)

        # lf-bijection (count-preserving): duplicate one record over another so the COUNT is unchanged but
        # a quarantined source loses its correspondence; the keyed check must still FINDING.
        m = copy_run(clean)
        lf = _load_toml(m / "fragments" / "legacy_fragment.index.toml")
        if len(lf["record"]) >= 2:
            lf["record"][1] = dict(lf["record"][0])
            (m / "fragments" / "legacy_fragment.index.toml").write_text(
                _opf_emit.emit(lf), encoding="utf-8")
            rewrite_report_digest(m, "fragments/legacy_fragment.index.toml")
            expect("disc-lf-bijection-swap", check_staged_run(m)["lf-bijection"][0] is False)

        # lf-quad-completeness: drop the `span` field from one legacy_fragment; refresh the digest.
        m = copy_run(clean)
        lf = _load_toml(m / "fragments" / "legacy_fragment.index.toml")
        del lf["record"][0]["span"]
        (m / "fragments" / "legacy_fragment.index.toml").write_text(_opf_emit.emit(lf), encoding="utf-8")
        rewrite_report_digest(m, "fragments/legacy_fragment.index.toml")
        expect("disc-lf-quad", check_staged_run(m)["lf-quad-completeness"][0] is False)

        # source-preservation: tamper preserved source bytes; refresh the report digest for that file so
        # only the preservation check (bytes no longer hash to the recorded/named digest) fires.
        m = copy_run(clean)
        run = _load_toml(m / "run.toml")
        victim = run["source"][0]["sha256"]
        (m / "sources" / victim).write_bytes(b"tampered-bytes")
        rewrite_report_digest(m, "sources/" + victim)
        expect("disc-source-preservation", check_staged_run(m)["source-preservation"][0] is False)

        # inventory-digest: corrupt the recorded inventory_digest (inventory.toml is not in report's
        # artefact list, so only the inventory-digest check fires).
        m = copy_run(clean)
        inv = _load_toml(m / "inventory.toml")
        inv["inventory_digest"] = "sha256:" + ("0" * 64)
        (m / "inventory.toml").write_text(_opf_emit.emit(inv), encoding="utf-8")
        expect("disc-inventory-digest", check_staged_run(m)["inventory-digest"][0] is False)

        # report-binding-digests: corrupt report.toml's plan_digest (report.toml is not in its own artefact
        # list, so the digest-integrity check stays green and only the binding-digest check fires).
        m = copy_run(clean)
        rep = _load_toml(m / "report.toml")
        rep["plan_digest"] = "sha256:" + ("0" * 64)
        (m / "report.toml").write_text(_opf_emit.emit(rep), encoding="utf-8")
        expect("disc-report-binding-digests", check_staged_run(m)["report-binding-digests"][0] is False)

        # proposals-artifact: tamper proposals.toml (proposals.toml is not in report's artefact list, so
        # only the proposals-artifact check fires).
        m = copy_run(clean)
        props = _load_toml(m / "proposals.toml")
        props["run_id"] = "imp-20260101T000000Z-0000000000000000"
        (m / "proposals.toml").write_text(_opf_emit.emit(props), encoding="utf-8")
        expect("disc-proposals-artifact", check_staged_run(m)["proposals-artifact"][0] is False)

        # proposals-artifact (strict-int schema, R5-F2): a proposals.toml schema of `true` (a bool) must NOT
        # pass via Python's `True == 1`. The `type(...) is int` guard (bool excluded) makes it a FINDING, the
        # class sibling of the report-schema bool-schema discriminator. proposals.toml is not in report's
        # artefact list, so only the proposals-artifact check fires.
        m = copy_run(clean)
        props = _load_toml(m / "proposals.toml")
        props["schema"] = True
        (m / "proposals.toml").write_text(_opf_emit.emit(props), encoding="utf-8")
        expect("disc-proposals-artifact-bool-schema", check_staged_run(m)["proposals-artifact"][0] is False)

        # proposals-artifact (byte reproducibility): rewrite IMPORT-REPORT.md line endings LF->CRLF. The
        # rendered surface is LF, so a byte compare (not a universal-newline read) must FINDING; refresh the
        # report digest for IMPORT-REPORT.md so artifact-digest-integrity stays green and only this check fires.
        m = copy_run(clean)
        crlf = (m / "IMPORT-REPORT.md").read_bytes().replace(b"\n", b"\r\n")
        (m / "IMPORT-REPORT.md").write_bytes(crlf)
        rewrite_report_digest(m, "IMPORT-REPORT.md")
        expect("disc-proposals-artifact-crlf", check_staged_run(m)["proposals-artifact"][0] is False)

        # proposals-artifact (R6-F1, proposal span shape): a proposal span that is a list but NOT a 2-element
        # int pair ([] or [5]) must be a located row FINDING, never an uncaught IndexError when
        # _render_report_md indexes span[0]/span[1]. proposals.toml is not in report's artefact list, so only
        # the proposals-artifact check fires. Without the row-check's 2-element-int guard this raises out of
        # check_staged_run instead of returning a False result.
        # Each span discriminator asserts the located ROW FINDING (primary boundary guard), not merely a
        # False result: the IndexError backstop below would also flip this to False, so isolating the
        # row-check detail proves the primary guard fired (removing only the row-check guard flips the detail
        # to "cannot reproduce" and fails this assertion, per change-carries-check).
        for bad_span in ([], [5]):
            m = copy_run(clean)
            props = _load_toml(m / "proposals.toml")
            props["proposal"] = [{"origin": imp._MODEL_PROPOSAL_ORIGIN, "source_path": "a.txt",
                                  "span": bad_span, "suggested_state": "unmapped", "note": ""}]
            (m / "proposals.toml").write_text(_opf_emit.emit(props), encoding="utf-8")
            pa = check_staged_run(m)["proposals-artifact"]
            expect("disc-proposals-artifact-span-{}".format(len(bad_span)),
                   pa[0] is False and "row is malformed" in pa[1])

        # proposals-artifact (R6-F1, IndexError backstop): a malformed inventory FRAGMENT span ([]) reaches
        # _render_report_md (which indexes frag["span"][0]/[1]) because the proposals-artifact check does not
        # shape-validate inventory fragments upstream. The proposal rows stay valid so the render is reached;
        # the IndexError-in-except backstop turns the fragment crash into the located "cannot reproduce"
        # FINDING rather than an uncaught IndexError out of check_staged_run.
        m = copy_run(clean)
        inv = _load_toml(m / "inventory.toml")
        inv["fragment"][0]["span"] = []
        (m / "inventory.toml").write_text(_opf_emit.emit(inv), encoding="utf-8")
        pa = check_staged_run(m)["proposals-artifact"]
        expect("disc-proposals-artifact-fragment-span-index",
               pa[0] is False and "cannot reproduce" in pa[1])

        # proposals-artifact (MIG-PR4a proposal-provenance vocabulary): the gate admits the DECLARED
        # vocabulary (imp._PROPOSAL_ORIGIN_VALUES: model_proposal AND the MIG-PR3 importer_proposal), so a
        # real staged run whose proposals rest as origin=importer_proposal PASSes; an origin OUTSIDE the
        # closed vocabulary is still rejected. Co-locates the vocabulary widening's fail-without-it in this
        # gate's OWN self-test (change-carries-check): pre-fix (model_proposal alone admitted) the
        # importer_proposal run FINDINGs, so the PASS leg FAILS without the widening.
        iroot, imachine = build_store({"a.txt": "aaaa"})
        importer = {"source_path": "a.txt", "span": [0, 2], "suggested_state": "mapped"}
        ipr = imp.plan_import(iroot, ["a.txt"], importer_proposals=[importer],
                              now=NOW, run_nonce="gate-nonce")
        if ipr.verdict != 0 or not ipr.run_id:
            raise OSError("harness: could not stage an importer-proposal run ({}: {})".format(
                ipr.verdict, ipr.findings))
        irun = imachine.parent / "imports" / ipr.run_id
        # guard against a vacuous pass: the staged proposals really carry the importer_proposal origin.
        iprops = _load_toml(irun / "proposals.toml")
        expect("proposals-artifact-importer-origin-row-present",
               any(p.get("origin") == imp._IMPORTER_PROPOSAL_ORIGIN
                   for p in iprops.get("proposal", [])))
        expect("proposals-artifact-importer-origin",
               check_staged_run(irun)["proposals-artifact"][0] is True)
        # an origin OUTSIDE the closed proposal-provenance vocabulary is still REJECTED (the widening admits
        # the declared set, not anything): a "guessed" origin FINDINGs at the vocabulary arm. The staged
        # IMPORT-REPORT.md is REGENERATED to byte-reproduce the guessed-origin proposals (a coherent report)
        # and report.toml's digest refreshed, so the report-reproduction arm PASSES and the ONLY remaining
        # rejection cause is the origin-vocab guard. This isolates the guard: reverting `pr.get("origin") in
        # imp._PROPOSAL_ORIGIN_VALUES` to True makes this discriminator FLIP to accept (verified). Without the
        # coherent report a stale-report mismatch would reject independently and mask a reverted guard (codex).
        m = copy_run(irun)
        props = _load_toml(m / "proposals.toml")
        for pr in props["proposal"]:
            pr["origin"] = "guessed"
        (m / "proposals.toml").write_text(_opf_emit.emit(props), encoding="utf-8")
        m_inv = _load_toml(m / "inventory.toml")
        m_norm = [{"source_path": pr["source_path"], "span": list(pr["span"]),
                   "suggested_state": pr["suggested_state"], "note": pr.get("note", ""),
                   "_origin": pr["origin"]}
                  for pr in props.get("proposal", [])]
        (m / "IMPORT-REPORT.md").write_bytes(
            imp._render_report_md(m_inv.get("inventory_digest"), m_inv.get("fragment"),
                                  m_norm, m.name).encode("utf-8"))
        rewrite_report_digest(m, "IMPORT-REPORT.md")
        expect("disc-proposals-origin-unknown", check_staged_run(m)["proposals-artifact"][0] is False)

        # --- acceptance.json (conditionally present): absent PASSes, present-and-valid PASSes, and each
        #     new acceptance check FINDINGs on its single mutation (acceptance.json is not in report's
        #     artefact list, so a mutation trips only the acceptance layer). ------------------------------
        # absent: recorded PASS "not yet reviewed" on the (unreviewed) clean run.
        expect("acceptance-absent-pass",
               all(check_staged_run(clean)[cid][0] for cid in
                   ("acceptance-schema", "acceptance-binding", "acceptance-attribution",
                    "acceptance-completeness")))
        # present-and-valid: a reviewed run passes every acceptance check.
        reviewed = review_clean()
        rres = check_staged_run(reviewed)
        expect("acceptance-present-valid",
               all(rres[cid][0] for cid in ("acceptance-schema", "acceptance-binding",
                                            "acceptance-attribution", "acceptance-completeness")))

        # present-but-unparseable: every acceptance check FINDINGs (fail-closed), never a clean pass.
        m = copy_run(reviewed)
        (m / imp.ACCEPTANCE_NAME).write_bytes(b"{ not valid json")
        munp = check_staged_run(m)
        expect("acceptance-unparseable-all-finding",
               all(munp[cid][0] is False for cid in ("acceptance-schema", "acceptance-binding",
                                                     "acceptance-attribution", "acceptance-completeness")))

        # present-but-not-a-regular-file: a DANGLING acceptance.json symlink must be a FINDING (fail-closed),
        # never pass-as-absent "not yet reviewed" (Path.exists() would answer False and mis-pass it).
        m = copy_run(reviewed)
        (m / imp.ACCEPTANCE_NAME).unlink()
        (m / imp.ACCEPTANCE_NAME).symlink_to("acceptance-target-does-not-exist")
        mdang = check_staged_run(m)
        expect("acceptance-dangling-symlink-all-finding",
               all(mdang[cid][0] is False for cid in ("acceptance-schema", "acceptance-binding",
                                                      "acceptance-attribution", "acceptance-completeness")))

        # Generation 1 (item 3): `.working/imported` and the homes-2 staging names are ordinary content, never
        # probed. Flip: probing the durable home in generation 1 (the marker or gate guard) meets this regular
        # FILE as a non-directory control path: review refuses and both ingest-acceptance checks fail.
        h1_root, h1_machine = build_store({"a.txt": "hello"})
        h1 = imp.plan_import(h1_root, ["a.txt"], now=NOW, run_nonce="gate-homes1-imported")
        h1_run = h1_machine.parent / "imports" / h1.run_id
        (h1_machine.parent / "imported").write_text("ordinary user content\n", encoding="utf-8")
        h1_rr = imp.review_import(h1_root, h1.run_id, actor="Gate Reviewer",
                                  decisions=accept_all_decisions(h1_run), now=NOW)
        h1_res = check_staged_run(h1_run)
        expect("homes1-imported-file-ordinary", h1_rr.verdict == 0 and all(ok for ok, _d in h1_res.values())
               and h1_res["ingest-acceptance-binding"][1] == "not an ingest run")
        # Flip: raising for a detached copy (a store required for an ordinary run) fails both
        # ingest-acceptance checks here, in either generation; main passes every check (A-M1).
        import unittest.mock
        import _opf_store
        detached = copy_run(reviewed)
        det1 = check_staged_run(detached)
        with unittest.mock.patch.object(_opf_store, "SUPPORTED_HOMES", 2):
            det2 = check_staged_run(detached, homes=2)
        expect("detached-ordinary-copy", all(ok for ok, _d in det1.values())
               and all(ok for ok, _d in det2.values()))

        # acceptance-schema: a wrong `format` keeps every other field intact, so only the schema check fires.
        m = copy_run(reviewed)
        acc = json.loads((m / imp.ACCEPTANCE_NAME).read_text())
        acc["format"] = "opf.import.acceptance/v2"
        (m / imp.ACCEPTANCE_NAME).write_text(json.dumps(acc), encoding="utf-8")
        expect("disc-acceptance-schema", check_staged_run(m)["acceptance-schema"][0] is False)

        # acceptance-attribution: blank the self-asserted actor.declared.
        m = copy_run(reviewed)
        acc = json.loads((m / imp.ACCEPTANCE_NAME).read_text())
        acc["actor"]["declared"] = ""
        (m / imp.ACCEPTANCE_NAME).write_text(json.dumps(acc), encoding="utf-8")
        expect("disc-acceptance-attribution", check_staged_run(m)["acceptance-attribution"][0] is False)

        # acceptance-binding: a stale plan_digest breaks the {run_id, plan_digest, inventory_digest} binding.
        m = copy_run(reviewed)
        acc = json.loads((m / imp.ACCEPTANCE_NAME).read_text())
        acc["plan_digest"] = "sha256:" + ("0" * 64)
        (m / imp.ACCEPTANCE_NAME).write_text(json.dumps(acc), encoding="utf-8")
        expect("disc-acceptance-binding", check_staged_run(m)["acceptance-binding"][0] is False)

        # acceptance-completeness: drop one decision so a fragment is left with no decision.
        m = copy_run(reviewed)
        acc = json.loads((m / imp.ACCEPTANCE_NAME).read_text())
        acc["decisions"] = acc["decisions"][:-1]
        (m / imp.ACCEPTANCE_NAME).write_text(json.dumps(acc), encoding="utf-8")
        expect("disc-acceptance-completeness", check_staged_run(m)["acceptance-completeness"][0] is False)

        # N4: an UNHASHABLE decision fragment_id ([]) is a located FINDING at BOTH the schema and binding
        # checks (and completeness), never a TypeError at the `in frag_by_id` dict membership or the
        # set(ids) build. Fail-closed across the acceptance layer.
        m = copy_run(reviewed)
        acc = json.loads((m / imp.ACCEPTANCE_NAME).read_text())
        acc["decisions"][0]["fragment_id"] = []
        (m / imp.ACCEPTANCE_NAME).write_text(json.dumps(acc), encoding="utf-8")
        macc = check_staged_run(m)
        expect("disc-acceptance-fragment-id-unhashable-schema", macc["acceptance-schema"][0] is False)
        expect("disc-acceptance-fragment-id-unhashable-binding", macc["acceptance-binding"][0] is False)
        expect("disc-acceptance-fragment-id-unhashable-completeness",
               macc["acceptance-completeness"][0] is False)

        # N4: an UNHASHABLE mapping state ([]) with origin=model_proposal must not crash acceptance-
        # completeness at the _RESTING_STATES membership (key_meta.state comes from an unvalidated mapping
        # row); check_staged_run returns a full result set and mapping-state-vocab FINDINGs the bad state.
        m = copy_run(review_model_proposal())
        mp = _load_toml(m / "mappings.toml")
        mp["mapping"][0]["state"] = []
        (m / "mappings.toml").write_text(_opf_emit.emit(mp), encoding="utf-8")
        rewrite_report_digest(m, "mappings.toml")
        mstate = check_staged_run(m)
        expect("disc-acceptance-completeness-unhashable-state-nocrash",
               set(mstate) == set(EXPECTED_CHECKS) and mstate["mapping-state-vocab"][0] is False)

        # F4: a nested-unhashable span ([[], []]) in a mapping row or an inventory fragment makes the
        # (source_path, tuple(span)) key unhashable. The int-element span guard keeps check_staged_run
        # returning a FULL result set with the acceptance correlation a located FINDING, never an uncaught
        # TypeError at the key membership. The U8 emitter refuses a nested array, so the span is injected as
        # raw text (only corruption or an attacker with staging write access produces such a span).
        def inject_nested_span(path, span):
            old = "span = [{}, {}]".format(span[0], span[1])
            txt = path.read_text(encoding="utf-8")
            if old not in txt:
                raise OSError("harness: could not locate {!r} to inject a nested span".format(old))
            path.write_text(txt.replace(old, "span = [[], []]", 1), encoding="utf-8")

        m = copy_run(reviewed)
        inject_nested_span(m / "mappings.toml", _load_toml(m / "mappings.toml")["mapping"][0]["span"])
        rewrite_report_digest(m, "mappings.toml")
        mnest_map = check_staged_run(m)
        expect("disc-acceptance-span-unhashable-mapping-nocrash",
               set(mnest_map) == set(EXPECTED_CHECKS) and mnest_map["acceptance-binding"][0] is False)

        m = copy_run(reviewed)
        inject_nested_span(m / "inventory.toml", _load_toml(m / "inventory.toml")["fragment"][0]["span"])
        mnest_inv = check_staged_run(m)
        expect("disc-acceptance-span-unhashable-fragment-nocrash",
               set(mnest_inv) == set(EXPECTED_CHECKS) and mnest_inv["acceptance-binding"][0] is False)

        # acceptance-completeness (model_proposal): a model_proposal resting mapping whose decision is a
        # reject (not an accept) is an incomplete acceptance, even with full coverage.
        m = copy_run(review_model_proposal())
        acc = json.loads((m / imp.ACCEPTANCE_NAME).read_text())
        for d in acc["decisions"]:
            d["decision"] = "reject"
        (m / imp.ACCEPTANCE_NAME).write_text(json.dumps(acc), encoding="utf-8")
        expect("disc-acceptance-model-proposal-omission",
               check_staged_run(m)["acceptance-completeness"][0] is False)

        # fail-closed read: a run dir missing every artefact is all-FINDING, never a clean pass.
        empty = base / "empty-run"
        empty.mkdir()
        empty_results = check_staged_run(empty)
        expect("fail-closed-empty", all(not ok for ok, _ in empty_results.values()))

        # --- R8-F1: untrusted read/parse boundaries fail closed to a located FINDING, never an uncaught
        #     crash on the DIRECT gate-call path (check-fails-closed-on-unreadable). Each mutation is a
        #     malicious staged artefact whose too-narrow except tuple would otherwise let an exotic exception
        #     escape; the flip in each case (narrowing the boundary's except back) turns the FINDING into an
        #     uncaught RecursionError/ValueError out of check_staged_run. -----------------------------------
        # (a) deeply-nested TOML -> tomllib RecursionError (neither OSError nor ValueError). _load_toml's
        #     broadened (OSError, ValueError, RecursionError) makes it a located FINDING. proposals.toml is
        #     loaded in its own check and is not in report's artefact list, so ONLY proposals-artifact fires.
        m = copy_run(clean)
        (m / "proposals.toml").write_text("a = " + "[" * 3000 + "]" * 3000, encoding="utf-8")
        r8_toml = check_staged_run(m)
        expect("disc-r8f1-deep-nested-toml-proposals",
               set(r8_toml) == set(EXPECTED_CHECKS) and r8_toml["proposals-artifact"][0] is False)

        # (b) deeply-nested acceptance.json -> json.loads RecursionError (under the size cap). The broadened
        #     except makes every acceptance check a located FINDING (fail-closed), never a crash.
        m = copy_run(reviewed)
        (m / imp.ACCEPTANCE_NAME).write_text("[" * 100000 + "]" * 100000, encoding="utf-8")
        r8_json = check_staged_run(m)
        expect("disc-r8f1-deep-nested-json-acceptance",
               set(r8_json) == set(EXPECTED_CHECKS)
               and all(r8_json[cid][0] is False for cid in
                       ("acceptance-schema", "acceptance-binding", "acceptance-attribution",
                        "acceptance-completeness")))

        # (c) embedded-NUL artefact path -> open() raises ValueError, not OSError. The broadened
        #     (OSError, ValueError, RecursionError) at the artefact read makes it a located FINDING. The NUL is
        #     injected via a backslash-u escape (the emitter refuses a raw control byte), replacing a unique
        #     sentinel token so tomllib decodes it back to the NUL path; the entry is appended last so the
        #     untouched entries pass first and only artifact-digest-integrity fires.
        m = copy_run(clean)
        rep = _load_toml(m / "report.toml")
        rep["artifact"].append({"path": "NULPATHSENTINEL", "sha256": "0" * 64})
        (m / "report.toml").write_text(_opf_emit.emit(rep), encoding="utf-8")
        txt = (m / "report.toml").read_text(encoding="utf-8").replace("NULPATHSENTINEL", "a\\u0000b")
        (m / "report.toml").write_text(txt, encoding="utf-8")
        r8_art = check_staged_run(m)
        expect("disc-r8f1-embedded-nul-artifact-path",
               set(r8_art) == set(EXPECTED_CHECKS) and r8_art["artifact-digest-integrity"][0] is False)

        # (d) embedded-NUL source-body path -> the sources/<sha256> read raises ValueError, not OSError (the
        #     path component is untrusted staged data). The broadened except makes source-preservation a
        #     located FINDING. The run.toml digest is refreshed so artifact-digest-integrity stays coherent.
        m = copy_run(clean)
        run = _load_toml(m / "run.toml")
        run["source"][0]["sha256"] = "NULSHASENTINEL"
        (m / "run.toml").write_text(_opf_emit.emit(run), encoding="utf-8")
        txt = (m / "run.toml").read_text(encoding="utf-8").replace("NULSHASENTINEL", "a\\u0000b")
        (m / "run.toml").write_text(txt, encoding="utf-8")
        rewrite_report_digest(m, "run.toml")
        r8_src = check_staged_run(m)
        expect("disc-r8f1-embedded-nul-source-path",
               set(r8_src) == set(EXPECTED_CHECKS) and r8_src["source-preservation"][0] is False)

        # --- scan-determinism + scan fail-closed -----------------------------------------------------
        sroot, _sm = build_store({"a.txt": "aaaa", "b.txt": "bbbbbb"})
        s1 = imp.scan_import(sroot, ["a.txt", "b.txt"])
        s2 = imp.scan_import(sroot, ["b.txt", "a.txt"])
        expect("scan-determinism",
               s1.verdict == 0 and s2.verdict == 0 and s1.inventory_digest == s2.inventory_digest)
        expect("scan-fail-closed-absent", imp.scan_import(sroot, ["a.txt", "missing.txt"]).verdict == 2)

        # The operation-layer module's own unit suite (scan/plan/apply internals, including the
        # apply-deferred-cannot-evaluate and plan-leaves-the-active-store-unchanged invariants) is part of
        # this gate's assurance and must run in CI: delegate to it and require it green.
        expect("module-self-test", imp.self_test() == 0)
    except OSError as exc:
        print("check_opf_import self-test: harness error: {}".format(exc), file=sys.stderr)
        shutil.rmtree(str(base), ignore_errors=True)
        return EXIT_ERROR
    finally:
        shutil.rmtree(str(base), ignore_errors=True)
        if _saved_kill_env is not None:
            os.environ[_journal_env.KILL_ENV] = _saved_kill_env

    if failures:
        for f in failures:
            print("check_opf_import self-test: FAIL: {}".format(f), file=sys.stderr)
        return EXIT_FINDING
    print("check_opf_import self-test: PASS (staged-run structure/report/artifact-digest/mapping-totality/"
          "state-vocab/origin-vocab/lf-bijection/lf-quad/source-preservation/inventory-digest/report-binding-digests/"
          "proposals-artifact each PASS on a clean run and FINDING on its discriminator; acceptance-schema/"
          "binding/attribution/completeness PASS absent (not yet reviewed) and present-and-valid, FINDING "
          "on each single mutation and all-FINDING when unparseable; scan determinism + fail-closed; empty "
          "run all-FINDING)")
    return EXIT_OK


def main(argv=None):
    # Final class-width backstop: any residual, unforeseen error routes to a located cannot-evaluate (exit
    # 2), never a false-0 or an uncaught exit-1 escape. KeyboardInterrupt/SystemExit stay uncaught.
    try:
        args = list(sys.argv[1:] if argv is None else argv)
        if args == ["--self-test"]:
            return _self_test()
        if args:
            print("check_opf_import: unexpected argument(s): {}".format(" ".join(args)), file=sys.stderr)
            return EXIT_ERROR
        # Live leg: the `opf import` verb is now wired (opf.py `_cmd_import`), but this repo is not an
        # OPFiles adopter and has no staged import run to check live. NOT APPLICABLE, exit 0 (the
        # doctor/drift non-adopter posture); the assurance rides the --self-test leg over synthetic runs.
        print("check_opf_import: NOT APPLICABLE (this repository is not an OPFiles adopter, so there is "
              "no staged `opf import` run to check live; the --self-test leg carries the assurance)")
        return EXIT_OK
    except Exception as exc:  # noqa: BLE001  fail-closed backstop, never a false-0 or uncaught exit-1
        print("check_opf_import: cannot evaluate: unexpected error in the import-operation gate ({!r}); "
              "failing closed to exit 2".format(exc), file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
