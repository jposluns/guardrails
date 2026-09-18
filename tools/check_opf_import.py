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
  - scan-determinism     : scan_import over the same inputs in reversed declaration order yields an equal
                           inventory digest, and an unreadable/absent declared source fails closed (exit 2).
  - transaction-schema   : the per-run apply-promotion transaction record (PR-C), when present at the store-
                           root `.aiqt/import/<run-id>/transaction.toml` (OUTSIDE .working/, so it survives
                           the terminal run-dir deletion), is well-formed: schema/format/run-id binding, a
                           known state, digest-shaped bindings, and allocation/restore_ref tables.
  - transaction-consistency : the transaction record's state machine, and that the attributed acceptance is
                           archived (`.aiqt/import-archive/<run-id>/acceptance.json`) once state >= published.

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
authenticity (the reserved signature seam is the upgrade path). A passing gate proves nothing about those.

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
    # Group C (OPF-IMPORT-APPLY, PR-C): the per-run transaction record the promotion writes OUTSIDE .working/
    # at the store-root `.aiqt/import/<run-id>/transaction.toml`, conditionally present (absent = not yet
    # applied). transaction-schema validates its shape; transaction-consistency validates the state machine
    # and that the archived acceptance exists once the record's state reaches published (spec 14.1 / apply).
    "transaction-schema", "transaction-consistency",
)


def _sha256_hex(data):
    return hashlib.sha256(data).hexdigest()


class _GateError(Exception):
    """A staged-run artefact the gate cannot read or parse: a fail-closed CANNOT-EVALUATE (never nothing
    to check). Carried out of a check as that check's FINDING; a harness-level read failure raises to the
    caller as EXIT_ERROR."""


def _load_toml(path):
    """Parse a required TOML artefact, fail-closed. A missing or unparseable required artefact is a
    _GateError (never silently treated as absent / empty)."""
    import tomllib
    try:
        with open(path, "rb") as fh:
            return tomllib.load(fh)
    except FileNotFoundError:
        raise _GateError("required artefact absent: {}".format(path))
    except (OSError, ValueError, RecursionError) as exc:
        # RecursionError joins the tuple (R8-F1): deeply-nested TOML (a malicious staged artefact) drives
        # tomllib into unbounded recursion, which is neither an OSError nor a ValueError, so without it a
        # crafted artefact crashes the gate on the direct call path instead of failing closed to a located
        # FINDING. MemoryError/KeyboardInterrupt/SystemExit still propagate.
        raise _GateError("required artefact unreadable/unparseable: {} ({})".format(path, exc))


def check_staged_run(run_dir):
    """Run the explicit check registry over one staged run directory. Returns an ordered dict
    check-id -> (ok: bool, detail: str). Each check fails closed on an artefact it cannot read: an
    unreadable required input is that check's FINDING, never a silent pass."""
    import json
    import re
    import _opf_import as imp

    run_dir = Path(run_dir)
    results = {}

    def record(cid, ok, detail=""):
        results[cid] = (bool(ok), detail)

    # --- staged-run-structure -----------------------------------------------------------------------
    required = ["run.toml", "plan.toml", "mappings.toml", "report.toml", "inventory.toml",
                "IMPORT-REPORT.md"]
    missing = [r for r in required if not (run_dir / r).is_file()]
    name_ok = bool(re.match(_RUN_ID_RE_TEXT, run_dir.name))
    record("staged-run-structure", not missing and name_ok,
           "missing {}".format(missing) if missing else ("run-dir name {!r} is not a run id".format(
               run_dir.name) if not name_ok else ""))

    # Parse the core artefacts once, fail-closed; a parse failure fails the structure check and every
    # dependent check (an unreadable input is never nothing-to-check).
    try:
        run = _load_toml(run_dir / "run.toml")
        # plan.toml is parsed for validity here (fail-closed on unparseable); its raw bytes are re-read by
        # report-binding-digests to recompute the plan_digest.
        _load_toml(run_dir / "plan.toml")
        mappings = _load_toml(run_dir / "mappings.toml")
        report = _load_toml(run_dir / "report.toml")
        inventory = _load_toml(run_dir / "inventory.toml")
    except _GateError as exc:
        # A malformed/unreadable required artefact fails the whole run closed. Derive the recorded set
        # from the single-source registry (not a hand-maintained tuple) so this early-return path can
        # never drift from EXPECTED_CHECKS and silently omit a future check.
        for cid in EXPECTED_CHECKS:
            record(cid, False, str(exc))
        return results

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
            p = run_dir / entry["path"]
            try:
                data = p.read_bytes()
            except (OSError, ValueError, RecursionError) as exc:
                # ValueError covers an embedded-NUL path (open() raises ValueError, not OSError) so a
                # crafted report.toml artifact path is a located FINDING, not an uncaught crash; RecursionError
                # is the class sibling for a pathological path (R8-F1). MemoryError/KeyboardInterrupt/SystemExit
                # still propagate.
                art_ok, art_detail = False, "enumerated artefact unreadable: {} ({})".format(p, exc)
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
        lf_path = run_dir / "fragments" / "legacy_fragment.index.toml"
        lf_records = []
        lf_read_ok = True
        if quarantine_rows or lf_path.is_file():
            try:
                lf_records = _load_toml(lf_path).get("record", [])
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
        body_path = run_dir / "sources" / s["sha256"]
        try:
            body = body_path.read_bytes()
        except (OSError, ValueError, RecursionError) as exc:
            # ValueError covers an embedded-NUL sha256 (the path component is untrusted staged data, so
            # open() raises ValueError, not OSError) so it is a located FINDING, not an uncaught crash;
            # RecursionError is the class sibling (R8-F1). MemoryError/KeyboardInterrupt/SystemExit propagate.
            src_ok, src_detail = False, "preserved source bytes absent/unreadable: {} ({})".format(
                body_path, exc)
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
        plan_bytes = (run_dir / "plan.toml").read_bytes()
    except OSError as exc:
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
        proposals = _load_toml(run_dir / "proposals.toml")
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
                span = pr.get("span") if isinstance(pr, dict) else None
                if not (isinstance(pr, dict) and pr.get("origin") == imp._MODEL_PROPOSAL_ORIGIN
                        and isinstance(pr.get("source_path"), str)
                        and isinstance(span, list) and len(span) == 2 and all(type(x) is int for x in span)
                        and isinstance(pr.get("suggested_state"), str)
                        and pr.get("suggested_state") in imp.MAPPING_STATES):
                    pa_ok, pa_detail = False, "a proposals.toml row is malformed or not origin=model_proposal"
                    break
    if pa_ok:
        try:
            norm = [{"source_path": pr["source_path"], "span": list(pr["span"]),
                     "suggested_state": pr["suggested_state"], "note": pr.get("note", "")}
                    for pr in proposals.get("proposal", [])]
            expected_md = imp._render_report_md(inventory.get("inventory_digest"),
                                                inventory.get("fragment"), norm, run_dir.name)
            # Compare BYTES, not universal-newline-normalized text: read_text() would silently fold a CRLF
            # IMPORT-REPORT.md into an LF match, so a byte-non-reproducible report (e.g. CRLF line endings)
            # would pass. The rendered surface is LF (utf-8), so an exact byte compare catches it.
            actual_md = (run_dir / "IMPORT-REPORT.md").read_bytes()
            if expected_md.encode("utf-8") != actual_md:
                pa_ok, pa_detail = False, ("IMPORT-REPORT.md is not byte-reproducible from inventory.toml "
                                           "+ proposals.toml + run id")
        # IndexError joins the tuple as a defence-in-depth backstop (marginal cost: one exception name): the
        # proposal-span row-check above now rejects a malformed proposal span, but _render_report_md also
        # indexes each inventory FRAGMENT span (frag["span"][0/1]), which this check does not shape-validate
        # upstream, so a malformed inventory fragment span becomes the located "cannot reproduce" FINDING
        # rather than an uncaught IndexError propagating to a direct check_staged_run caller.
        except (OSError, KeyError, TypeError, ValueError, IndexError) as exc:
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
    acc_path = run_dir / imp.ACCEPTANCE_NAME
    # Classify with a no-follow lstat three-way (SECI-symlink-resolution), never Path.exists(): a DANGLING
    # symlink would answer exists() False and be misread as "not yet reviewed" (a false PASS). Genuine
    # absence (no dentry) is the pre-review PASS; a regular file is evaluated; a symlink or any other
    # non-regular entry is a FINDING (fail-closed), never pass-as-absent.
    try:
        acc_st = os.lstat(acc_path)
    except FileNotFoundError:
        acc_st = None
    except OSError as exc:
        acc_st = exc
    if acc_st is None:
        for cid in acc_checks:
            record(cid, True, "not yet reviewed")
    elif isinstance(acc_st, OSError):
        for cid in acc_checks:
            record(cid, False, "acceptance.json cannot be classified ({})".format(acc_st))
    elif not stat.S_ISREG(acc_st.st_mode):
        for cid in acc_checks:
            record(cid, False, "acceptance.json is present but not a regular file")
    else:
        acc = None
        acc_err = ""
        try:
            acc = json.loads(acc_path.read_bytes().decode("utf-8"))
        except (OSError, ValueError, RecursionError) as exc:
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

    # --- Group C: the per-run transaction record (apply-promotion, PR-C) --------------------------------
    # The record lives OUTSIDE .working/ at the store-root `.aiqt/import/<run-id>/transaction.toml` (D2/D3:
    # it survives the terminal run-dir deletion and never enters the store containment walk). It is
    # CONDITIONALLY PRESENT (mirroring acceptance.json): absent = "run not yet applied", recorded as a PASS
    # for both Group C checks; present = validated for schema and state-machine consistency. The store root
    # is derived from the run dir (<store>/.working/imports/<run-id>), so the gate finds the record at its
    # relocated home without a separate argument.
    store_root = run_dir.parent.parent.parent
    run_name = run_dir.name
    txn_path = store_root / imp.IMPORT_OPS_REL / run_name / imp.TRANSACTION_NAME
    if not txn_path.is_file():
        record("transaction-schema", True, "no transaction record (run not yet applied)")
        record("transaction-consistency", True, "no transaction record (run not yet applied)")
    else:
        try:
            txn = _load_toml(txn_path)
        except _GateError as exc:
            record("transaction-schema", False, str(exc))
            record("transaction-consistency", False, "transaction record unreadable/unparseable")
        else:
            # transaction-schema: shape of the record, validated by the SHARED
            # imp._validate_transaction_record so the gate and the apply idempotency no-op cannot drift
            # (PRC-F2). The run binding is the run dir name.
            ts_ok, ts_detail = imp._validate_transaction_record(txn, run_name)
            record("transaction-schema", ts_ok, ts_detail)

            # transaction-consistency: the state-machine invariant that the attributed acceptance exists once
            # the record's state reaches published (>= published). The apply relocates acceptance.json to the
            # durable archive at `.aiqt/import-archive/<run-id>/acceptance.json`, so its presence there is the
            # authoritative post-deletion witness of the review-to-apply binding.
            tc_ok, tc_detail = True, ""
            if txn.get("state") in ("published", "complete"):
                arch_acc = store_root / imp.IMPORT_ARCHIVE_REL / run_name / imp.ACCEPTANCE_NAME
                if not arch_acc.is_file():
                    tc_ok, tc_detail = False, ("state is >= published but the archived acceptance.json is "
                                               "absent (acceptance must exist once state reaches published)")
            record("transaction-consistency", tc_ok, tc_detail)

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

    try:
        # --- clean run: every check PASSes -----------------------------------------------------------
        clean = stage_clean()
        clean_results = check_staged_run(clean)
        for cid, (ok, detail) in clean_results.items():
            expect("clean:{}:{}".format(cid, detail), ok)
        # The emitted check-set must be EXACTLY the declared registry (no omission, no stray): an omitted
        # check can never read as a clean pass.
        expect("clean-registry-complete", set(clean_results) == set(EXPECTED_CHECKS))

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

        # (a) no record present: both Group C checks PASS ("run not yet applied"), asserted on the clean run.
        expect("txn-absent-schema-pass", clean_results["transaction-schema"][0] is True)
        expect("txn-absent-consistency-pass", clean_results["transaction-consistency"][0] is True)
        # (b) a valid COMPLETE record + an archived acceptance: both PASS.
        tcb = stage_clean()
        write_txn(tcb.parent.parent.parent, tcb.name)
        write_archived_acceptance(tcb.parent.parent.parent, tcb.name)
        tcb_res = check_staged_run(tcb)
        expect("txn-valid-schema-pass", tcb_res["transaction-schema"][0] is True)
        expect("txn-valid-consistency-pass", tcb_res["transaction-consistency"][0] is True)
        # (c) a malformed record (wrong format): transaction-schema FINDING.
        tcc = stage_clean()
        write_txn(tcc.parent.parent.parent, tcc.name, format="wrong/format/v9")
        write_archived_acceptance(tcc.parent.parent.parent, tcc.name)
        expect("disc-txn-schema", check_staged_run(tcc)["transaction-schema"][0] is False)
        # (d) state >= published but the archived acceptance is absent: transaction-consistency FINDING.
        tcd = stage_clean()
        write_txn(tcd.parent.parent.parent, tcd.name, state="published")
        expect("disc-txn-consistency", check_staged_run(tcd)["transaction-consistency"][0] is False)

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
