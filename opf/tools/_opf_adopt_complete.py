#!/usr/bin/env python3
"""OPF adoption completion evaluator, slice 1: the read-only deterministic checks 1 to 4 and 6 (OPF-SPEC 1.3.0).

Spec sections implemented: 14.1 (the clean-start completion-check roster: checks 1 authority and freshness,
2 discovery accounting, 3 preservation and restore, 4 operational readiness and 6 retirement readiness; a
completion-check failure or cannot-evaluate reports incomplete and retires nothing), 14.1's homes-1 clause
(the completion checks re-read inventories and payload digests themselves, because C-EVIDENCE-ENUM is
inactive until homes 2) and 4.2 (C-EVIDENCE-ENUM MUST NOT run or read anything on homes 1; the section 14.1
completion checks carry the evidence digest verification). Check 5 (wiring: the enforcement-pack probes) is
NOT evaluated here: its probes run processes and land in a later slice, so the roster this module reports is
never green on its own (roster_green requires all six checks VALID).

Read-only: every live read is contained and no-follow through the apply shell's own reader
(_opf_adopt_apply._read_live, single-linked regular files only), nothing is written to the live tree, and no
process is spawned. The one write is check 3's restore exercise, which copies archived bytes into a private
TemporaryDirectory scratch and digest-verifies the copy there; it never restores over the live tree.

Inputs: the product root, the adoption run id, the adopter-held planning inventory bytes
(`opf.adoption.planning-inventory/v1`, the bytes the plan's inventory_digest seals; apply persists the plan
and approval in the run's bundle, not the planning inventory), and a doctor callable (product_root to the
_opf_check StoreValidation shape: status, findings, cannot_evaluate). The doctor's git observations are a
process-spawning gather, so the caller supplies the run (store_doctor builds one from caller-supplied inert
observations); with no doctor, check 4 is CANNOT-EVALUATE, never a pass.

Outcome model (single-sourced from _opf_store): each check is VALID (green), INVALID (red, with findings) or
CANNOT-EVALUATE (an input it cannot read or parse, never a pass). An absent required artefact (no approval,
no receipt, an absent preimage or live file) is a finding; an unreadable or malformed one is
CANNOT-EVALUATE. A plan that does not re-prove (frozen_plan) leaves every check CANNOT-EVALUATE.

Check ownership, so one mutation flips one check (the canonical single-mutation flips of the self-test).
Each check's own docstring quotes the spec 14.1 roster sentence it enforces; the division of labour is:
  1 authority-freshness: the frozen plan re-proves and names this run, the bundle's approval binds it
    (approval_findings), the receipt core validates and binds the run, product, plan and inventory digests,
    store root and release manifest and records both release anchor flags (independent_anchor_observed,
    anchor_agreement) True (the apply shell's own receipt_binding_refusals, the rule adoption_record
    refuses by), it records the approval itself, each receipt file row names a plan source with its
    disposition and before_digest, the live preimages of the sources the plan leaves live and no other
    check owns (keep, and non-occupying migrate) still equal their plan digests, and every planned
    destination apply has already written and no other check owns (init-store and non-CI install-pack
    members, create-file and plant-governance paths, enable-hook registrations, and the registration
    chain's final manifest) holds exactly its planned bytes. The retire-disposed live files are check 6's,
    so an edited retire source flips check 6 only; the non-occupying move sources are check 3's; the
    rendered views, CI members and consumer repointings are check 4's; a move-file destination is written
    only by the post-green relocation, so before green it is no live destination; and the receipt is
    compared by content in the receipt leg, because outcome events append to its file.
  2 discovery-accounting: the planning inventory re-seals to the plan's inventory_digest; every candidate,
    empty directory and inventoried file entry, wherever it lies, has a plan disposition or lies under a
    recorded exclusion (the store control area counts as one, spec 14.2); every entry row must be one the
    planner records, by the planner's own row rule (_opf_adopt_plan.entry_row_problem, which _inventory
    calls on every row it walks: a canonical path by _opf_adopt_plan._path, a kind in its vocabulary,
    exactly that kind's fields, a file row's size a byte count and its digest well formed, kind="excluded"
    exactly under a recorded exclusion by _opf_adopt_plan._under), and a duplicate-path row is ambiguous;
    either is CANNOT-EVALUATE naming the row, never a pass; every exclusion, candidate and empty-directory
    path must pass _opf_adopt_plan._path too; the recorded source roots must be as the planner records
    them (_opf_adopt_plan._roots); and the redundant candidates and empty_directories lists are RE-DERIVED
    from the entries by the planner's own derivations (_opf_adopt_plan.candidate_files and
    empty_directories): each recorded path must be one the derivation yields over every root, and under the
    candidate roots the inventory itself fixes (the source roots and .working) the recorded list must EQUAL
    the derivation, so an omitted candidate or empty directory is CANNOT-EVALUATE, never green; every plan
    source is an observed file whose digest equals its plan digest.
  3 preservation-restore: the run's evidence bundle verifies from disk (_opf_adopt_apply.verify_bundle, the
    homes-1 evidence digest verification: every inventory and every payload it lists), and the bundle
    inventory this check re-reads must again pass the apply shell's own inventory validator
    (_opf_adopt_apply.validate_inventory), else CANNOT-EVALUATE; every
    archive-preserved source (retire, migrate and occupying, spec 14.2) sits at its run archive path,
    digest-matched and claimed by the bundle inventory, and the restore exercise reproduces each preserved
    file's bytes in scratch. A non-occupying move source is preserved at its move destination only by the
    post-green relocation (spec 14.2), so before green this check verifies its frozen live bytes against
    the plan digest instead and requires no archive copy of it.
  4 operational-readiness: the store resolves to the planned identity (an absent store is a finding, so a
    de-adopted repository cannot pass as NOT-APPLICABLE); the CI leg is proven only by template
    identity, never by reading workflow syntax (_ci_template_identity): per CI row the plan names
    exactly one workflow member, a file directly under .github/workflows/ (default
    .github/workflows/opf.yml), whose recorded digest is the pack's shipped template
    opf/enforcement/ci/github-actions.yml and whose live bytes equal that template; every CI member is live
    at its plan digest; and the recipe the template runs, opf/enforcement/ci/opf-ci.sh, is live and equals
    the pack's shipped recipe by digest. The two reference files are themselves authenticated: each is
    read contained and no-follow under the pack root and must equal the sha256 embedded in this module
    (generated and drift-gated by tools/gen_secret_patterns.py), so a reference replaced, symlinked or
    unreadable in the working tree is CANNOT-EVALUATE. Any other workflow content, any other path, an
    absent or modified copy, an absent or modified recipe, or a shipped file that cannot be read is
    CANNOT-EVALUATE, the reason telling the adopter to install the template unmodified (OPF_TOOL, which
    moves the CLI the recipe launches, is unsupported in this release: the plan models no variant); every
    planned view
    destination holds exactly its planned bytes, a formerly occupied destination included (spec 14.1: once
    apply commits, restoring an archived file to the live tree takes a fresh plan, so old occupant bytes
    there are drift the plan does not account for); every consumer repointing holds its planned new bytes;
    and every doctor finding fails the check, none set aside (doctor evidence whose findings or
    cannot_evaluate is missing or not a list is CANNOT-EVALUATE, never read as empty). Check 4 is NEVER
    VALID in this release: the shipped floor asserts store presence, not identity (the CI identity
    assertion is the deferred U25), so with every other condition met the check is CANNOT-EVALUATE naming
    U25; an earlier failure keeps its own reason. U25_CI_IDENTITY_ASSERTED is the one switch that lets
    it grade VALID, to be set only in the release that ships U25.
  6 retirement-readiness: each retire-disposed source that occupied no managed destination is present live
    with its plan digest; each archived occupying source still equals its plan digest in the archive.

DISCLOSED RESIDUALS: the receipt core schema requires a null after_digest for retire and move rows, which
TOML cannot spell, so check 1 compares only the receipt rows present and cannot require one row per source;
check 2 trusts the planning inventory the caller supplies once it re-seals to the plan's bound digest, and
re-derives the candidates and empty directories exactly only under the source roots and .working: the
deliverable destinations, the other candidate roots, come from the store manifest, which the inventory does
not record, so outside those roots a recorded path must be derivable from the entries but an omission is
not seen there (every file entry and every recorded empty directory is accounted wherever it lies);
check 4 is CANNOT-EVALUATE until U25 ships the CI store-identity assertion (U25_CI_IDENTITY_ASSERTED), so
the roster cannot be green in this release on check 4 alone either; the reference digests authenticate
the CI floor files against this module's own embedded constants, so they are only as trustworthy as this
module's bytes (an attacker who can rewrite the evaluator itself is out of scope);
check 4's doctor verdict is only as complete as the caller's observations; check 4's CI leg proves byte
identity with the shipped template and recipe, not what GitHub runs: it does not see another workflow in the
repository that cancels or shadows this one (for example a concurrency group with cancel-in-progress),
branch-protection or ruleset settings that do not require this workflow's check, or GitHub-side settings
that disable Actions or this workflow; it does not read the opf/tools/opf.py the recipe launches, the
remote actions the template names by tag (actions/checkout, actions/setup-python) or the runner image; it
compares against the template beside this evaluator, so a later pack release that changes the template
leaves a plan whose recorded workflow digest pins the earlier template CANNOT-EVALUATE until it is planned
again; and it is not a run of CI.

Offline, stdlib only, fail-closed. It lives under `opf/tools/` and imports ONLY sibling `opf/tools/`
modules, so the standalone-closure property holds.

Exit convention (the repo's gates and the sibling OPF units): 0 clean, 1 a finding, 2 cannot-evaluate.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    sys.stderr.write(
        "error: _opf_adopt_complete.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
        "Nothing was run (cannot evaluate).\n"
        % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
    raise SystemExit(2)

import hashlib
import os
import re
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _opf_adopt as schema          # noqa: E402
import _opf_adopt_apply as apply     # noqa: E402
import _opf_adopt_plan as planner    # noqa: E402
import _opf_store as store           # noqa: E402
from _opf_emit import EmitError, emit_checked  # noqa: E402

VALID, INVALID, CANNOT_EVALUATE = store.VALID, store.INVALID, store.CANNOT_EVALUATE
# The roster in the spec's numbered order (the plan's own vocabulary), and the five this slice evaluates.
CHECKS = schema.COMPLETION_CHECKS
AUTHORITY, DISCOVERY, PRESERVATION, OPERATIONAL, WIRING, RETIREMENT = CHECKS
EVALUATED = (AUTHORITY, DISCOVERY, PRESERVATION, OPERATIONAL, RETIREMENT)
PLANNING_INVENTORY_FORMAT = "opf.adoption.planning-inventory/v1"
_WIRING_NOTE = ("check 5 (wiring) is not evaluated by this slice: its enforcement probes spawn processes; "
                "the roster is incomplete until it runs (never a pass)")
# The planning inventory's closed entry-kind vocabulary: the planner's own (_opf_adopt_plan's observation rows).
ENTRY_KINDS = planner.ENTRY_KINDS


class Unevaluable(Exception):
    """An input the check cannot read or parse: the check is CANNOT-EVALUATE, never a pass."""


class CheckResult:
    __slots__ = ("status", "findings")

    def __init__(self, status, findings=()):
        self.status = status
        self.findings = list(findings)


class _Report:
    def __init__(self):
        self.findings, self.cannot = [], []

    def finding(self, msg):
        self.findings.append(msg)

    def result(self):
        if self.cannot:
            return CheckResult(CANNOT_EVALUATE, ["cannot evaluate: " + c for c in self.cannot] + self.findings)
        return CheckResult(INVALID if self.findings else VALID, self.findings)


class _Evaluation:
    __slots__ = ("product_root", "root_fd", "run_id", "plan", "planning_inventory", "doctor")


def _digest(data):
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _read(ev, relpath):
    """The live bytes at `relpath` (contained, no-follow, single-linked), or None when absent."""
    try:
        _fst, data = apply._read_live(ev.root_fd, relpath)
    except apply.AdoptApplyError as exc:
        raise Unevaluable(str(exc))
    return data


def _canonical(data, label):
    try:
        return apply._canonical_toml(data, label)
    except apply.AdoptApplyError as exc:
        raise Unevaluable(str(exc))


def _sources(ev):
    return ev.plan["sources"]


# --- check 1: authority and freshness -----------------------------------------------------------------

def _planned_destinations(ev):
    """Check 1's destination sweep: the {path: digest} of every planned destination apply has already
    written and no other roster check owns. Render-views members, CI enforcement members and consumer
    repointings are check 4's; the record-adoption receipt is compared by content in the receipt leg
    (outcome events append to its file); a move-file destination is written only by the post-green
    relocation (spec 14.2), so before green it is not a live destination. A register-unmanaged chain
    rewrites the frozen store manifest, so the chain's last new_digest replaces any scaffolded member
    digest there (_opf_adopt's registration-chain validation pins the chain to program order)."""
    plan = ev.plan
    ci = {m["path"] for row in plan["enforcement"] if row["platform"] == "ci" for m in row["members"]}
    wanted = {}
    for op in plan["ops"]:
        if op["op"] in ("create-file", "plant-governance"):
            wanted[op["path"]] = op["content_digest"]
        elif op["op"] in ("init-store", "install-pack"):
            member_root = op[schema._MEMBER_ROOTS[op["op"]]]
            for member in op["members"]:
                dest = schema._compose(member_root, member["path"])
                if dest not in ci:
                    wanted[dest] = member["digest"]
        elif op["op"] == "enable-hook":
            wanted[op["registration_path"]] = op["new_digest"]
    manifest = schema.store_manifest(plan["store"])
    for op in plan["ops"]:
        if op["op"] == "register-unmanaged" and manifest is not None:
            wanted[manifest] = op["new_digest"]
    return wanted


def _check_authority(ev, rep):
    """Spec 14.1 check 1: 'Authority and freshness: roots, destinations and live preimages still match
    the approved plan.' Roots and the binding chain: the approval and the receipt bind this run and plan;
    destinations: _planned_destinations; live preimages: the sources the plan leaves live that no other
    check owns (keep, and non-occupying migrate)."""
    plan, run_id = ev.plan, ev.run_id
    approval_bytes = _read(ev, apply.approval_rel(run_id))
    approval = None
    if approval_bytes is None:
        rep.finding("no approval in the run's evidence bundle ({}): nothing authorizes this run".format(
            apply.approval_rel(run_id)))
    else:
        approval = _canonical(approval_bytes, "the run's approval")
        for f in apply.approval_findings(approval, plan):
            rep.finding("approval: " + f)
    receipt_bytes = _read(ev, apply.receipt_rel(run_id))
    receipt = None
    if receipt_bytes is None:
        rep.finding("no adoption receipt in the run's evidence bundle ({})".format(apply.receipt_rel(run_id)))
    else:
        receipt = _canonical(receipt_bytes, "the adoption receipt core")
        checked = schema.validate_receipt_core(receipt)
        if checked.status == CANNOT_EVALUATE:
            raise Unevaluable("receipt core: " + "; ".join(checked.findings))
        if checked.status != VALID:
            for f in checked.findings:
                rep.finding("receipt core: " + f)
            receipt = None
    if receipt is not None:
        _receipt_binding(ev, rep, receipt, approval)
    for source in _sources(ev):
        if not (source["disposition"] == "keep"
                or (source["disposition"] == "migrate" and not source["occupying"])):
            continue
        data = _read(ev, source["path"])
        if data is None:
            rep.finding("live preimage {!r} ({}) is absent from the live tree".format(
                source["path"], source["disposition"]))
        elif _digest(data) != source["digest"]:
            rep.finding("live preimage {!r} ({}) no longer matches its plan digest: bound-item drift takes a "
                        "fresh plan (spec 14.1)".format(source["path"], source["disposition"]))
    wanted = _planned_destinations(ev)
    for dest in sorted(wanted):
        data = _read(ev, dest)
        if data is None:
            rep.finding("planned destination {!r} is absent from the live tree".format(dest))
        elif _digest(data) != wanted[dest]:
            rep.finding("planned destination {!r} no longer holds its planned bytes: bound-item drift "
                        "takes a fresh plan (spec 14.1)".format(dest))


def _receipt_binding(ev, rep, receipt, approval):
    """Check 1's receipt leg: the receipt core binds this run and the approved plan and records both
    release anchor flags True, by the apply shell's own rule (receipt_binding_refusals,
    the refusals adoption_record raises), and records its approval and the plan's sources as the plan binds
    them."""
    plan = ev.plan
    for refusal in apply.receipt_binding_refusals(ev.run_id, plan, receipt):
        rep.finding("receipt: " + refusal + " (adoption_record refuses it)")
    if approval is not None and receipt["approval"] != approval:
        rep.finding("the receipt's approval is not the run's recorded approval")
    by_path = dict((row["path"], row) for row in _sources(ev))
    for row in receipt["files"]:
        source = by_path.get(row["path"])
        if source is None:
            rep.finding("receipt file {!r} is not a source of the approved plan".format(row["path"]))
        elif row["disposition"] != source["disposition"] or row["before_digest"] != source["digest"]:
            rep.finding("receipt file {!r} records {} {} but the approved plan binds {} {}".format(
                row["path"], row["disposition"], row["before_digest"], source["disposition"],
                source["digest"]))


# --- check 2: discovery accounting --------------------------------------------------------------------

def _inventory_path(value, label):
    """`value` held to the planning inventory producer's own path validator (_opf_adopt_plan._path: a
    contained, canonical file or directory path with no `.` or `..` component and no .git component), or
    Unevaluable naming it: a path the planner never records is malformed accounting input, never a pass."""
    try:
        return planner._path(value)
    except (planner.PlanError, TypeError, ValueError) as exc:
        raise Unevaluable("planning inventory {} path {!r} is not a path the planner records ({})".format(
            label, value, exc))


def _check_discovery(ev, rep):
    """Spec 14.1 check 2: 'Discovery accounting: every inventory entry has a disposition or recorded
    exclusion.' Every inventoried file, wherever it lies, and every candidate and empty directory needs a
    plan disposition or a recorded exclusion; an ambiguous (duplicate-path) or malformed entry row is
    CANNOT-EVALUATE, never a pass. Every path is first held to the planner's own validator
    (_inventory_path), so the containment test below only ever sees canonical contained paths, and an
    entry recorded excluded must lie under a recorded exclusion by the planner's own matching rule
    (_opf_adopt_plan._under: the producer records kind="excluded" exactly for such a path), so a
    contradictory excluded entry is CANNOT-EVALUATE, never skipped. The whole row rule is the planner's
    (_opf_adopt_plan.entry_row_problem), and the redundant candidates and empty_directories lists are
    re-derived from the entries by the planner's own derivations and must agree (module docstring, check 2)."""
    if ev.planning_inventory is None:
        raise Unevaluable("the planning inventory was not supplied; discovery accounting needs its entries")
    doc = _canonical(ev.planning_inventory, "the planning inventory")
    if doc.get("format") != PLANNING_INVENTORY_FORMAT:
        raise Unevaluable("the planning inventory format {!r} is not {}".format(
            doc.get("format"), PLANNING_INVENTORY_FORMAT))
    body = dict(doc)
    claimed = body.pop("inventory_digest", None)
    try:
        sealed = _digest(emit_checked(body).encode("utf-8"))
    except EmitError as exc:
        raise Unevaluable("the planning inventory cannot be re-sealed ({})".format(exc))
    if claimed != sealed:
        rep.finding("the planning inventory's inventory_digest does not seal its own bytes")
    if claimed != ev.plan["inventory_digest"]:
        rep.finding("the planning inventory {!r} is not the one the approved plan binds ({!r})".format(
            claimed, ev.plan["inventory_digest"]))
    obs = doc.get("observation")
    lists = ("exclusions", "entries", "candidates", "empty_directories", "sources")
    if not (isinstance(obs, dict) and all(isinstance(obs.get(k), list) for k in lists)):
        raise Unevaluable("the planning inventory's observation lacks {}".format(", ".join(lists)))
    try:
        excluded = [_inventory_path(row["path"], "exclusion") for row in obs["exclusions"]]
    except (TypeError, KeyError) as exc:
        raise Unevaluable("the planning inventory's exclusions are malformed ({!r})".format(exc))
    entries = {}
    for i, row in enumerate(obs["entries"]):
        # Ambiguous, malformed or contradictory accounting input never grades: it is CANNOT-EVALUATE, never
        # a pass. The row rule is the planner's own, the one _inventory holds every row it records to.
        problem = planner.entry_row_problem(row, obs["exclusions"])
        if problem is not None:
            raise Unevaluable("planning inventory entry [{}]: {}; the planner never records such a row "
                              "(a malformed or contradictory record, never skipped)".format(i, problem))
        if row["path"] in entries:
            raise Unevaluable("planning inventory entry {!r} is duplicated: the accounting is "
                              "ambiguous".format(row["path"]))
        entries[row["path"]] = row
    rows = list(entries.values())
    try:
        roots = planner._roots(obs["sources"])
    except (planner.PlanError, TypeError, ValueError) as exc:
        raise Unevaluable("the planning inventory's source roots are not roots the planner records ({})".format(
            exc))
    if roots != obs["sources"]:
        raise Unevaluable("the planning inventory's source roots are not recorded sorted, as the planner "
                          "records them")
    # The planner's candidate roots are the source roots, .working and the deliverable destinations; the
    # last come from the store manifest, which the inventory does not record, so the re-derivation is exact
    # under the first two only (a disclosed residual).
    roots = roots + [".working"]
    recorded = {}
    for label, derive in (("candidates", planner.candidate_files),
                          ("empty_directories", planner.empty_directories)):
        listed = [_inventory_path(p, label.replace("_", " ")) for p in obs[label]]
        if listed != sorted(set(listed)):
            raise Unevaluable("the planning inventory's {} are not sorted and duplicate-free, as the "
                              "planner records them".format(label))
        stray = sorted(set(listed) - set(derive(rows, None)))
        if stray:
            raise Unevaluable("the planning inventory's {} name {}, which the planner's derivation from "
                              "its entries never yields: a contradictory record".format(label, stray))
        derived = derive(rows, roots)
        within = [p for p in listed if any(planner._under(p, root) for root in roots)]
        if within != derived:
            raise Unevaluable("the planning inventory's {} omit {}, which the planner derives from its "
                              "entries under the candidate roots {}: a redundant list that disagrees with "
                              "the entries it is derived from is never trusted".format(
                                  label, sorted(set(derived) - set(within)), roots))
        recorded[label] = listed
    needing = set(recorded["candidates"]) | set(recorded["empty_directories"])
    needing |= {p for p, row in entries.items() if row["kind"] == "file"}
    disposed = {row["path"] for row in _sources(ev)}
    for path in sorted(needing):
        if path in disposed or schema._in_control_area(path) or any(planner._under(path, e) for e in excluded):
            continue
        rep.finding("inventory entry {!r} has neither a disposition nor a recorded exclusion".format(path))
    for source in _sources(ev):
        row = entries.get(source["path"])
        if not (isinstance(row, dict) and row.get("kind") == "file" and row.get("digest") == source["digest"]):
            rep.finding("plan source {!r} is not an inventoried file with its plan digest".format(source["path"]))


# --- check 3: preservation and restore ----------------------------------------------------------------

def _restore_exercise(items):
    """Restore each (source path, archived bytes, plan digest) into a private scratch tree, never over the
    live tree, and return the source paths whose restored bytes do not reproduce the plan digest."""
    failed = []
    with tempfile.TemporaryDirectory(prefix="opf-adopt-restore-") as scratch:
        for path, data, digest in items:
            target = os.path.join(scratch, *path.split("/"))
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "xb") as out:
                out.write(data)
            with open(target, "rb") as back:
                if _digest(back.read()) != digest:
                    failed.append(path)
    return failed


def _check_preservation(ev, rep):
    """Spec 14.1 check 3: 'Preservation and restore: each retirement preimage, preserved at apply for a
    retire-disposed file and for a non-occupying migrate-disposed source alike, and each archived occupying
    source (section 14.2) exists, digest-matched, under `.working/archive/adoption/<run-id>/`, and a
    restore exercise reproduces its bytes.' A non-occupying move source has no archive copy before green:
    spec 14.2 relocates it only after the plan-bound green completion check, with digest-verified
    preservation, so this check accepts its move-destination preservation and verifies its frozen LIVE
    bytes against the plan digest instead."""
    try:
        bundle = apply.verify_bundle(ev.product_root, ev.run_id)
    except apply.AdoptApplyError as exc:
        raise Unevaluable(str(exc))
    if bundle.status == CANNOT_EVALUATE:
        raise Unevaluable("evidence bundle: " + "; ".join(bundle.findings))
    for f in bundle.findings:
        rep.finding("evidence bundle: " + f)
    listed = set()
    inventory = _read(ev, apply.inventory_rel(ev.run_id))
    if inventory is not None:
        doc = _canonical(inventory, "the bundle inventory")
        # The re-read inventory is held to the apply shell's own validator, as verify_bundle's read was.
        checked = apply.validate_inventory(doc, ev.run_id)
        if checked.status == CANNOT_EVALUATE:
            raise Unevaluable("the bundle inventory: " + "; ".join(checked.findings))
        rows = doc.get("file")
        if not (isinstance(rows, list) and all(isinstance(r, dict) and isinstance(r.get("path"), str)
                                                for r in rows)):
            raise Unevaluable("the bundle inventory carries no well-formed file rows")
        listed = {r["path"] for r in rows}
    items = []
    for source in _sources(ev):
        preserved = source.get("preservation")
        if preserved is None:
            continue
        if source["disposition"] == "move" and not source["occupying"]:
            # Pre-green the move has not run (spec 14.2): the frozen live source IS the preserved bytes.
            data = _read(ev, source["path"])
            if data is None:
                rep.finding("move-disposed {!r} is absent from the live tree, so nothing preserves at its "
                            "move destination".format(source["path"]))
            elif _digest(data) != source["digest"]:
                rep.finding("move-disposed {!r} no longer matches its plan digest: a drifted source MUST "
                            "NOT be moved (spec 14.2)".format(source["path"]))
            continue
        if preserved != apply.archive_rel(ev.run_id, source["path"]):
            rep.finding("{!r} is preserved at {!r}, not under this run's adoption archive".format(
                source["path"], preserved))
            continue
        data = _read(ev, preserved)
        if data is None:
            rep.finding("preimage {!r} of {!r} is missing from the adoption archive".format(
                preserved, source["path"]))
            continue
        if _digest(data) != source["digest"]:
            rep.finding("preimage {!r} does not match the plan digest of {!r}".format(preserved, source["path"]))
            continue
        if preserved not in listed:
            rep.finding("preimage {!r} is not claimed by the run's bundle inventory".format(preserved))
        items.append((source["path"], data, source["digest"]))
    try:
        failed = _restore_exercise(items)
    except OSError as exc:
        raise Unevaluable("the restore exercise could not run in scratch ({})".format(exc))
    for path in failed:
        rep.finding("the restore exercise did not reproduce the bytes of {!r}".format(path))


# --- check 4: operational readiness -------------------------------------------------------------------

def store_doctor(observations=None):
    """A doctor callable for check 4: resolve the store and run the whole-store validator over the caller's
    inert git-derived observations (gathering them spawns git, which this slice never does)."""
    def run(product_root):
        import _opf_check
        return _opf_check.validate_store(store.resolve_store(Path(product_root)), observations=observations)
    return run


# The pack's shipped CI floor (opf/enforcement/ci beside these tools): the GitHub Actions template an adopter
# installs unmodified (its header: "Copy this file to .github/workflows/opf.yml") and the recipe that
# template runs. Check 4 proves the CI leg by byte identity with these two files, never by reading workflow
# syntax, so any other workflow content is CANNOT-EVALUATE. The reference files sit in a writable tree, so
# each is read contained and no-follow from the pack root (_shipped) and authenticated against the digest
# embedded below; a mismatch, a symlink or a read error is CANNOT-EVALUATE.
_PACK_ROOT = Path(__file__).resolve().parent.parent
_SHIPPED_TEMPLATE = "enforcement/ci/github-actions.yml"
_SHIPPED_RECIPE = "enforcement/ci/opf-ci.sh"
# The released digests of those two files (bare-hex sha256), generated and drift-gated: editing either file
# without regenerating fails tools/gen_secret_patterns.py --check in CI. Never hand-edit this region.
# BEGIN generated OPF CI floor digests (source: opf/enforcement/ci/; regenerate with tools/gen_secret_patterns.py)
_SHIPPED_TEMPLATE_SHA256 = '3048d80af20e7c6699aa54d908c6bddb905fe1f4b056b85c30559e5e94be5262'
_SHIPPED_RECIPE_SHA256 = '0036e97e68163c6c6df6f3b3260cf5c38e19a0d3d8a3bb713afef2355a2f8104'
# END generated OPF CI floor digests
# Spec 14.1 check 4 requires that "CI asserts store presence and identity". The shipped recipe asserts
# presence only (doctor --require-store, then render --check); the CI identity assertion is the deferred
# U25 (the template's own header says so). This is the one switch for it: while it is False, check 4's CI
# leg is CANNOT-EVALUATE naming U25 once every other check-4 condition holds, so check 4 is never VALID on
# a presence-only floor. Set it True only in the release whose shipped recipe performs the U25 identity
# assertion (that recipe change regenerates _SHIPPED_RECIPE_SHA256 above).
U25_CI_IDENTITY_ASSERTED = False
_U25_DEFERRED = ("the shipped CI floor asserts store presence only (doctor --require-store, then render "
                 "--check); spec 14.1 check 4 also requires CI to assert store identity, which is the "
                 "deferred CI receipt-identity comparison (U25), so this check cannot be VALID until U25 "
                 "ships")
# The repository path the template's one run step names (`run: sh opf/enforcement/ci/opf-ci.sh .`): the
# pack sits at opf/, and the recipe launches opf/tools/opf.py beside it unless OPF_TOOL is set.
_CI_RECIPE_REL = "opf/enforcement/ci/opf-ci.sh"
_TEMPLATE_RUN_LINE = b"        run: sh " + _CI_RECIPE_REL.encode("ascii") + b" .\n"
# GitHub reads workflows only from files directly under .github/workflows/: a plan naming a workflow
# elsewhere, a subdirectory included, names a file GitHub never runs.
_WORKFLOW_DIR = ".github/workflows/"
_WORKFLOW_NAME_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*\.ya?ml")
_INSTALL_UNMODIFIED = ("install the pack's template opf/enforcement/ci/github-actions.yml unmodified at the "
                       "workflow path the plan names (default .github/workflows/opf.yml), with the recipe "
                       "opf/enforcement/ci/opf-ci.sh unmodified, and plan the adoption again; a modified, "
                       "extended or relocated copy is not supported in this release, and neither is "
                       "OPF_TOOL (the pack at a path other than opf/)")


def _is_workflow(path):
    return path.endswith((".yml", ".yaml"))


def _shipped(rel, expected):
    """The authenticated bytes of one shipped CI floor file: read contained and no-follow under the pack
    root (the apply shell's reader: a symlink, special or multiply-linked file refuses) and equal to the
    `expected` bare-hex sha256 embedded in this evaluator. Absent, unreadable, symlinked or mismatched is
    Unevaluable (fail closed), never trusted as the reference."""
    root_fd = None
    try:
        root_fd = apply._open_product_root(_PACK_ROOT)
        _fst, data = apply._read_live(root_fd, rel)
    except apply.AdoptApplyError as exc:
        raise Unevaluable("cannot read the pack's shipped CI file {} ({})".format(rel, exc))
    finally:
        if root_fd is not None:
            store._close_fd_exc_safe(root_fd)
    if data is None:
        raise Unevaluable("cannot read the pack's shipped CI file {} (absent)".format(rel))
    if hashlib.sha256(data).hexdigest() != expected:
        raise Unevaluable("the pack's shipped CI file {} does not match the digest embedded in this evaluator "
                          "(regenerate with tools/gen_secret_patterns.py when the release changes it); an "
                          "unauthenticated reference is never trusted".format(rel))
    return data


def _shipped_floor():
    """The authenticated (template, recipe) bytes of the pack's shipped CI floor."""
    return (_shipped(_SHIPPED_TEMPLATE, _SHIPPED_TEMPLATE_SHA256),
            _shipped(_SHIPPED_RECIPE, _SHIPPED_RECIPE_SHA256))


def _ci_template_identity(ev, row):
    """Check 4's CI leg for one CI enforcement row: the reasons the row does not prove the shipped CI floor
    installed unmodified, [] when it does. The row names exactly one workflow member, a file directly under
    .github/workflows/; the plan records that member at the shipped template's digest and its live bytes
    equal the template; every member of the row is live at its plan digest (a planned recipe member at the
    shipped recipe's digest); and the recipe the template runs (_CI_RECIPE_REL) is live and equals the
    shipped recipe by digest, planned or not. No workflow syntax is read. A shipped file that cannot be
    read or does not match its embedded digest (_shipped), or a shipped template that does not run that
    recipe, is Unevaluable."""
    template, recipe = _shipped_floor()
    if _TEMPLATE_RUN_LINE not in template:
        raise Unevaluable("the pack's shipped template {} does not run {}: the pack is inconsistent".format(
            _SHIPPED_TEMPLATE, _CI_RECIPE_REL))
    why = []
    workflows = [member["path"] for member in row["members"] if _is_workflow(member["path"])]
    if len(workflows) != 1:
        why.append("the CI row names {} workflow members, not exactly one".format(len(workflows)))
    for member in row["members"]:
        path, data = member["path"], _read(ev, member["path"])
        if data is None:
            why.append("CI member {!r} is absent".format(path))
        elif _digest(data) != member["digest"]:
            why.append("CI member {!r} does not match its planned bytes".format(path))
        if _is_workflow(path):
            name = path[len(_WORKFLOW_DIR):] if path.startswith(_WORKFLOW_DIR) else ""
            if _WORKFLOW_NAME_RE.fullmatch(name) is None:
                why.append("CI workflow {!r} is not a file directly under {}, where GitHub reads "
                           "workflows".format(path, _WORKFLOW_DIR))
            if member["digest"] != _digest(template):
                why.append("the plan records CI workflow {!r} at a digest that is not the pack's shipped "
                           "template {}".format(path, Path(_SHIPPED_TEMPLATE).name))
            elif data is not None and data != template:
                why.append("live CI workflow {!r} is not byte-identical to the pack's shipped template "
                           "{}".format(path, Path(_SHIPPED_TEMPLATE).name))
        elif path == _CI_RECIPE_REL and member["digest"] != _digest(recipe):
            why.append("the plan records the CI recipe {!r} at a digest that is not the pack's shipped "
                       "recipe".format(path))
    live = _read(ev, _CI_RECIPE_REL)
    if live is None:
        why.append("the recipe the template runs, {!r}, is absent".format(_CI_RECIPE_REL))
    elif _digest(live) != _digest(recipe):
        why.append("the recipe the template runs, {!r}, is not the pack's shipped recipe".format(_CI_RECIPE_REL))
    return why


def _check_operational(ev, rep):
    """Spec 14.1 check 4: 'Operational readiness: the store resolves to the planned identity, and CI
    asserts store presence and identity so absence cannot pass as NOT-APPLICABLE. Doctor validity and
    declared-view byte drift are evaluated on the live tree, where apply has already rendered every
    declared view, a formerly occupied destination included, and any drift the plan does not account for
    fails the check. Consumer repointings match the plan.' The plan accounts for a view destination by
    binding its rendered member digest, nothing else: old occupant bytes there are unaccounted drift (spec
    14.1: once apply commits, restoring an archived file takes a fresh plan), and no doctor finding is ever
    set aside. CI asserts only through the pack's shipped template and recipe installed unmodified
    (_ci_template_identity); any other CI is CANNOT-EVALUATE, never VALID. That floor asserts store
    presence, not identity (U25 is deferred), so while U25_CI_IDENTITY_ASSERTED is False a check with no
    other finding or cannot-evaluate reason is CANNOT-EVALUATE naming U25; an earlier failure keeps its own
    reason."""
    plan = ev.plan
    resolution = store.resolve_store(Path(ev.product_root))
    if resolution.status == store.NOT_ADOPTED:
        rep.finding("no store resolves at the product root: a de-adopted repository fails check 4, never "
                    "NOT-APPLICABLE")
    elif resolution.status != store.RESOLVED:
        raise Unevaluable("the store does not resolve ({}: {})".format(resolution.status, resolution.detail))
    else:
        try:
            same = os.path.samefile(resolution.store_root,
                                    os.path.join(ev.product_root, plan["store"]["store_root"]))
        except OSError as exc:
            raise Unevaluable("cannot compare the resolved store root ({})".format(exc))
        if not same or resolution.machine_rel != plan["store"]["machine_rel"]:
            rep.finding("the store resolves to {} {!r}, not the planned identity {!r} {!r}".format(
                resolution.store_root, resolution.machine_rel, plan["store"]["store_root"],
                plan["store"]["machine_rel"]))
    ci = [row for row in plan["enforcement"] if row["platform"] == "ci"]
    if not ci:
        rep.finding("the plan binds no CI enforcement row, so CI cannot assert store presence")
    for row in ci:
        why = _ci_template_identity(ev, row)
        if why:
            rep.cannot.append("CI is not the pack's shipped CI floor installed unmodified, and this check proves "
                              "the CI leg only by that byte identity: {}; {}".format(
                                  "; ".join(why), _INSTALL_UNMODIFIED))
    occupants = {s["path"]: s["digest"] for s in _sources(ev) if s["occupying"]}
    for op in plan["ops"]:
        if op["op"] == "render-views":
            for member in op["members"]:
                dest = schema._compose(op[schema._MEMBER_ROOTS[op["op"]]], member["path"])
                data = _read(ev, dest)
                if data is None:
                    rep.finding("planned view {!r} is missing".format(dest))
                elif _digest(data) == member["digest"]:
                    continue
                elif _digest(data) == occupants.get(dest):
                    rep.finding("planned view destination {!r} still holds the old occupant's bytes: once "
                                "apply commits, restoring an archived file to the live tree takes a fresh "
                                "plan, so this drift fails the check (spec 14.1)".format(dest))
                else:
                    rep.finding("planned view destination {!r} holds bytes the plan does not account "
                                "for".format(dest))
        elif op["op"] == "repoint-consumer":
            data = _read(ev, op["path"])
            if data is None or _digest(data) != op["new_digest"]:
                rep.finding("consumer {!r} does not hold its planned repointing".format(op["path"]))
    if ev.doctor is None:
        raise Unevaluable("no doctor run was supplied; doctor validity is never assumed")
    result = ev.doctor(ev.product_root)
    status = getattr(result, "status", None)
    cannot = getattr(result, "cannot_evaluate", None)
    findings = getattr(result, "findings", None)
    # Missing or non-list doctor evidence is never read as empty: it is CANNOT-EVALUATE.
    if not isinstance(cannot, list) or not isinstance(findings, list):
        raise Unevaluable("doctor evidence is not list-shaped (cannot_evaluate {}, findings {})".format(
            type(cannot).__name__, type(findings).__name__))
    if cannot or status not in (VALID, INVALID):
        raise Unevaluable("doctor: {} {}".format(status, "; ".join(map(str, cannot))))
    if status == INVALID and not findings:
        raise Unevaluable("doctor reports INVALID with no finding")
    for f in findings:
        # Spec 14.1 check 4: any drift the plan does not account for fails the check, and the plan accounts
        # only by binding digests, so every doctor finding is reported; none is set aside.
        rep.finding("doctor: {}".format(f))
    if not U25_CI_IDENTITY_ASSERTED and not rep.findings and not rep.cannot:
        rep.cannot.append(_U25_DEFERRED)


# --- check 6: retirement readiness --------------------------------------------------------------------

def _check_retirement(ev, rep):
    """Spec 14.1 check 6: 'Retirement readiness: each retire-disposed file that occupied no managed
    destination is present in the live tree and its live bytes still equal its plan digest, and each
    archived occupying source still equals its plan digest in the archive.'"""
    for source in _sources(ev):
        if source["occupying"]:
            data = _read(ev, source["preservation"])
            if data is None or _digest(data) != source["digest"]:
                rep.finding("archived occupying source {!r} no longer equals its plan digest in the "
                            "archive".format(source["path"]))
        elif source["disposition"] == "retire":
            data = _read(ev, source["path"])
            if data is None:
                rep.finding("retire-disposed {!r} is absent from the live tree".format(source["path"]))
            elif _digest(data) != source["digest"]:
                rep.finding("retire-disposed {!r} changed since the plan: a changed old file MUST NOT be "
                            "retired (spec 14.1)".format(source["path"]))


_CHECK_FUNCTIONS = {AUTHORITY: "_check_authority", DISCOVERY: "_check_discovery",
                    PRESERVATION: "_check_preservation", OPERATIONAL: "_check_operational",
                    RETIREMENT: "_check_retirement"}


def _run_check(name, ev):
    rep = _Report()
    try:
        globals()[_CHECK_FUNCTIONS[name]](ev, rep)
    except Unevaluable as exc:
        rep.cannot.append(str(exc))
    except Exception as exc:  # noqa: BLE001  any other escape (an injected doctor's included) fails closed
        rep.cannot.append("{} raised {!r}; failing closed".format(name, exc))
    return rep.result()


def evaluate(product_root, run_id, *, planning_inventory=None, doctor=None):
    """The roster verdicts {check name: CheckResult} in the spec's order (spec 14.1). Check 5 is always
    CANNOT-EVALUATE here. Read-only over the live tree; every unreadable input is CANNOT-EVALUATE."""
    ev = _Evaluation()
    ev.product_root, ev.run_id, ev.doctor = str(product_root), run_id, doctor
    ev.planning_inventory, ev.plan, ev.root_fd = planning_inventory, None, None
    try:
        ev.root_fd = apply._open_product_root(product_root)
        if not apply.is_run_id(run_id):
            raise Unevaluable("run id {!r} does not match the adoption grammar".format(run_id))
        plan_bytes = _read(ev, apply.plan_rel(run_id))
        if plan_bytes is None:
            raise Unevaluable("no approved plan in the run's evidence bundle ({})".format(apply.plan_rel(run_id)))
        try:
            ev.plan = apply.frozen_plan(plan_bytes)
        except apply.AdoptApplyError as exc:
            raise Unevaluable(str(exc))
        if ev.plan["run_id"] != run_id:
            raise Unevaluable("the bundle's plan names run {!r}".format(ev.plan["run_id"]))
        results = {name: (CheckResult(CANNOT_EVALUATE, [_WIRING_NOTE]) if name == WIRING
                          else _run_check(name, ev)) for name in CHECKS}
    except (Unevaluable, apply.AdoptApplyError) as exc:
        results = {name: CheckResult(CANNOT_EVALUATE, ["cannot evaluate: {}".format(exc)]) for name in CHECKS}
    finally:
        if ev.root_fd is not None:
            store._close_fd_exc_safe(ev.root_fd)
    return results


def roster_green(results):
    """True only when every one of the six roster checks is VALID (spec 14.1: retirement needs green)."""
    return all(name in results and results[name].status == VALID for name in CHECKS)


# --- self-test ----------------------------------------------------------------------------------------

_RUN = "adopt-20260917T120000Z-0123456789abcdef"
# The fixture's CI floor: None installs the pack's shipped template and recipe; a vector patches either with
# other bytes, which the fixture's plan then records, so the change is digest-bound (a post-apply tree).
_CI_BYTES = None
_CI_RECIPE_BYTES = None
# Extra planning inventory entry rows a vector adds to the fixture; the fixture seals and binds them (its plan,
# approval, receipt and bundle inventory), so no seal or binding failure can stand in for the check graded.
_EXTRA_ENTRIES = ()
# Extra empty_directories paths, and (path, changes) edits to existing entry rows, sealed and bound alike.
_EXTRA_EMPTY = ()
_ENTRY_EDITS = ()
# The planner's recorded source roots for the fixture (sorted, non-overlapping, as _opf_adopt_plan._roots).
_SOURCE_ROOTS = ["adopter", "legacy", "notes"]
_CI_PATH = ".github/workflows/opf.yml"
_RENDERED = b"rendered todo view\n"
_MOVE_DEST = ".working/archive/moved/adopter/MOVE.md"
_CONSUMER = "consumer/USES.md"
_CONSUMER_OLD, _CONSUMER_NEW = b"old consumer\n", b"new consumer\n"
# The non-CI enforcement members install-pack plants (check 1's destination sweep owns them).
_PACK = {".opf/hooks/pre-commit": b"#!/bin/sh\nexit 0\n", ".claude/settings.json": b"{}\n",
         "AGENTS.md": b"agents instructions\n", "GEMINI.md": b"gemini instructions\n",
         ".cursor/rules/opf.mdc": b"cursor instructions\n"}
_LIVE = {"legacy/RULES.md": b"old rules\n", "notes/old.md": b"old notes\n", ".working/TODO.md": b"old todo\n",
         "adopter/MOVE.md": b"move me\n"}


def _ci_floor():
    """The fixture's (workflow, recipe) bytes: the shipped files unless a vector patches them."""
    template, recipe = _shipped_floor() if _CI_BYTES is None or _CI_RECIPE_BYTES is None else (None, None)
    return (template if _CI_BYTES is None else _CI_BYTES,
            recipe if _CI_RECIPE_BYTES is None else _CI_RECIPE_BYTES)


class _Doctor:
    """A doctor stub returning one fixed StoreValidation-shaped verdict."""

    def __init__(self, status=VALID, findings=(), cannot=()):
        self.status, self.findings, self.cannot_evaluate = status, list(findings), list(cannot)

    def __call__(self, product_root):
        return self


def _put(root, rel, data):
    path = os.path.join(root, *rel.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as out:
        out.write(data)


def _emit(doc):
    return emit_checked(doc).encode("utf-8")


def _sealed(doc, field):
    body = dict(doc)
    body.pop(field, None)
    return dict(body, **dict([(field, _digest(_emit(body)))]))


def _fixture(root):
    """A post-apply product tree for one run: a retired non-occupying source frozen live, a migrate source
    frozen live, a non-occupying move source frozen live (its relocation waits for green, spec 14.2), an
    occupying source at the TODO.md view destination archived and its view rendered, a repointed consumer
    under a recorded exclusion, the store scaffolded, every enforcement member planted (the CI floor is the
    shipped template and recipe unless a vector patches _CI_BYTES or _CI_RECIPE_BYTES), and the run's
    bundle (plan, approval, receipt, inventory). Returns the planning inventory bytes."""
    import _opf_init
    manifest = _opf_init.build_manifest().encode("utf-8")
    sources = []
    for path, disposition, occupying in (("legacy/RULES.md", "retire", False), ("notes/old.md", "migrate", False),
                                         (".working/TODO.md", "retire", True), ("adopter/MOVE.md", "move", False)):
        preservation = _MOVE_DEST if disposition == "move" else apply.archive_rel(_RUN, path)
        sources.append(dict(path=path, digest=_digest(_LIVE[path]), disposition=disposition,
                            occupying=occupying, preservation=preservation))
    sources.sort(key=lambda row: row["path"])
    bindings = schema.canonical_plan_bindings()
    workflow, recipe = _ci_floor()
    for row in bindings["enforcement"]:
        if row["platform"] == "ci":
            row["members"] = [dict(path=_CI_PATH, digest=_digest(workflow)),
                              dict(path=_CI_RECIPE_REL, digest=_digest(recipe))]
        else:
            row["members"] = [dict(path=m["path"], digest=_digest(_PACK[m["path"]])) for m in row["members"]]
    init = schema.canonical_op("init-store")
    init["members"] = [dict(path=".working/toml/manifest.toml", digest=_digest(manifest))]
    render = schema.canonical_op("render-views")
    render["members"] = [dict(path=".working/TODO.md", digest=_digest(_RENDERED))]
    ops = [dict(op="retire-file", path=s["path"], preimage_digest=s["digest"])
           for s in sources if s["disposition"] == "retire"]
    ops += [dict(op="move-file", source="adopter/MOVE.md", destination=_MOVE_DEST,
                 source_digest=_digest(_LIVE["adopter/MOVE.md"])),
            dict(op="repoint-consumer", path=_CONSUMER, old_digest=_digest(_CONSUMER_OLD),
                 new_digest=_digest(_CONSUMER_NEW)),
            init, render, schema.enforcement_install_op(bindings["enforcement"])]
    entries = [dict(path=p, kind="file", size=len(_LIVE[p]), digest=_digest(_LIVE[p])) for p in sorted(_LIVE)]
    # The consumer lies under a recorded exclusion, so the planner records it excluded and never reads it.
    entries.append(dict(path=_CONSUMER, kind="excluded"))
    entries += [dict(row) for row in _EXTRA_ENTRIES]
    for path, changes in _ENTRY_EDITS:
        for row in entries:
            if row["path"] == path:
                row.update(changes)
    entries.sort(key=lambda row: row["path"])
    inventory = _sealed(dict(format=PLANNING_INVENTORY_FORMAT, decisions=[], observation=dict(
        exclusions=[dict(path=".working/toml", reason="machine-store"),
                    dict(path="consumer", reason="adopter-owned consumers, repointed by the plan")],
        entries=entries, candidates=sorted(_LIVE), empty_directories=sorted(_EXTRA_EMPTY),
        sources=list(_SOURCE_ROOTS))), "inventory_digest")
    plan = dict(format=schema.PLAN_FORMAT, schema=schema.SCHEMA_VERSION, product="aiqt",
                inventory_digest=inventory["inventory_digest"], run_id=_RUN, revision=bindings["revision"],
                store=dict(store_root=".", machine_rel=".working/toml", adoption="first-adoption"),
                sources=sources, effects=schema.derive_effects(ops, sources, ".working/toml/manifest.toml"),
                release=bindings["release"], prompt_pack=bindings["prompt_pack"],
                enforcement=bindings["enforcement"], completion=schema.plan_completion(),
                import_policy=schema.plan_import_policy(bindings["skip_policy"], sources), ops=ops)
    plan = _sealed(plan, "plan_digest")
    approval = dict(actor="maintainer", approved_at="2026-09-17T12:30:00Z", plan_digest=plan["plan_digest"],
                    inventory_digest=plan["inventory_digest"])
    receipt = schema.canonical_receipt_core()
    receipt.update(run_id=_RUN, plan_digest=plan["plan_digest"], inventory_digest=plan["inventory_digest"],
                   approval=approval, import_runs=[])
    receipt["release"]["manifest_sha256"] = plan["release"]["manifest_sha256"]
    receipt["files"] = [dict(path="notes/old.md", before_digest=_digest(_LIVE["notes/old.md"]),
                             after_digest=_digest(_LIVE["notes/old.md"]), disposition="migrate")]
    bundle = {apply.plan_rel(_RUN): _emit(plan), apply.approval_rel(_RUN): _emit(approval),
              apply.receipt_rel(_RUN): _emit(receipt)}
    for s in sources:
        if s["disposition"] in ("retire", "migrate") or s["occupying"]:
            bundle[s["preservation"]] = _LIVE[s["path"]]
    for rel, data in bundle.items():
        _put(root, rel, data)
    _put(root, apply.inventory_rel(_RUN), apply.emit_inventory(
        _RUN, [apply.inventory_row(rel, data) for rel, data in bundle.items()]))
    for rel in ("legacy/RULES.md", "notes/old.md", "adopter/MOVE.md"):
        _put(root, rel, _LIVE[rel])
    _put(root, _CONSUMER, _CONSUMER_NEW)
    _put(root, ".working/TODO.md", _RENDERED)
    _put(root, ".working/toml/manifest.toml", manifest)
    _put(root, _CI_PATH, workflow)
    _put(root, _CI_RECIPE_REL, recipe)
    for rel, data in _PACK.items():
        _put(root, rel, data)
    return _emit(inventory)


def _case(mutate=None):
    with tempfile.TemporaryDirectory(prefix="opf-adopt-complete-") as root:
        kwargs = dict(planning_inventory=_fixture(root), doctor=_Doctor())
        if mutate is not None:
            kwargs.update(mutate(root, kwargs["planning_inventory"]) or {})
        return evaluate(root, _RUN, **kwargs)


def _reference_case(template=None, recipe=None, symlinked=()):
    """The round-10 reproductions: the pack's reference CI floor replaced in a scratch pack root, `template`
    or `recipe` bytes standing in for the shipped file, installed live and recorded by the plan (as an edit
    matched by the live copy would be); each pack-relative name in `symlinked` becomes a symlink to its
    bytes outside the pack root. With nothing replaced and nothing symlinked it is the genuine floor."""
    from unittest import mock
    here = sys.modules[__name__]
    genuine = _shipped_floor()
    floor = dict([(_SHIPPED_TEMPLATE, genuine[0] if template is None else template),
                  (_SHIPPED_RECIPE, genuine[1] if recipe is None else recipe)])
    with tempfile.TemporaryDirectory(prefix="opf-adopt-complete-pack-") as scratch:
        pack = os.path.join(scratch, "opf")
        for rel, data in floor.items():
            if rel in symlinked:
                _put(scratch, "elsewhere/" + rel, data)
                os.makedirs(os.path.dirname(os.path.join(pack, *rel.split("/"))), exist_ok=True)
                os.symlink(os.path.join(scratch, "elsewhere", *rel.split("/")), os.path.join(pack, *rel.split("/")))
            else:
                _put(pack, rel, data)
        with mock.patch.object(here, "_PACK_ROOT", Path(pack)), \
                mock.patch.object(here, "_CI_BYTES", floor[_SHIPPED_TEMPLATE]), \
                mock.patch.object(here, "_CI_RECIPE_BYTES", floor[_SHIPPED_RECIPE]):
            return _case()


def _only(results, expected):
    """True when each evaluated check has its expected status (VALID unless named) and check 5 stays
    CANNOT-EVALUATE."""
    return (results[WIRING].status == CANNOT_EVALUATE
            and all(results[name].status == expected.get(name, VALID) for name in EVALUATED))


def _says(results, name, text):
    return any(text in f for f in results[name].findings)


def _reseal_inventory(inv, **changes):
    import tomllib
    doc = tomllib.loads(inv.decode("utf-8"))
    doc["observation"].update(changes)
    return _emit(_sealed(doc, "inventory_digest"))


def _remove(root, rel):
    os.remove(os.path.join(root, *rel.split("/")))


def _append(root, rel, data):
    with open(os.path.join(root, *rel.split("/")), "ab") as fh:
        fh.write(data)


def _retoml(root, rel, mutate):
    """Parse a fixture TOML artefact, mutate it in place, and write it back canonically re-emitted."""
    import tomllib
    with open(os.path.join(root, *rel.split("/")), "rb") as fh:
        doc = tomllib.load(fh)
    mutate(doc)
    _put(root, rel, _emit(doc))


def _rereceipt(root, mutate):
    """Rewrite the run's receipt through `mutate`, then re-seal the bundle inventory over the bundle's
    current bytes, so the tamper reaches only check 1's receipt leg (check 3's bundle verification holds)."""
    import tomllib
    _retoml(root, apply.receipt_rel(_RUN), mutate)
    with open(os.path.join(root, *apply.inventory_rel(_RUN).split("/")), "rb") as fh:
        rows = tomllib.load(fh)["file"]
    fresh = []
    for row in rows:
        with open(os.path.join(root, *row["path"].split("/")), "rb") as fh:
            fresh.append(apply.inventory_row(row["path"], fh.read()))
    _put(root, apply.inventory_rel(_RUN), apply.emit_inventory(_RUN, fresh))


def _toml_file(root, rel):
    """A fixture TOML artefact, parsed."""
    import tomllib
    with open(os.path.join(root, *rel.split("/")), "rb") as fh:
        return tomllib.load(fh)


def _entries(inv):
    import tomllib
    return tomllib.loads(inv.decode("utf-8"))["observation"]["entries"]


def _unclaim(root, rel):
    """Drop `rel` from the run's bundle inventory, re-emitted through the canonical emitter."""
    import tomllib
    with open(os.path.join(root, *apply.inventory_rel(_RUN).split("/")), "rb") as fh:
        rows = [r for r in tomllib.load(fh)["file"] if r["path"] != rel]
    _put(root, apply.inventory_rel(_RUN), apply.emit_inventory(_RUN, rows))


_DRIFT = ("C-VIEW-DRIFT: view 'TODO.md' target '.working/TODO.md' has drifted from its generated source "
          "(spec 5.8/10)")


def _flip_1(root, inv):
    _put(root, "notes/old.md", b"edited notes\n")


# An unaccounted file under a recorded source root, recorded as the planner would: a file entry and a candidate.
_STRAY = dict(path="legacy/stray.md", kind="file", size=6, digest=_digest(b"stray\n"))


def _strayed(inv, entries=(), candidates=()):
    """The planning inventory with _STRAY (and any further entry rows and candidates) recorded, resealed."""
    return _reseal_inventory(inv, entries=_entries(inv) + [_STRAY] + list(entries),
                             candidates=sorted(sorted(_LIVE) + [_STRAY["path"]] + list(candidates)))


def _flip_2(root, inv):
    return dict(planning_inventory=_strayed(inv))


def _flip_3(root, inv):
    _remove(root, apply.archive_rel(_RUN, "legacy/RULES.md"))


def _flip_4(root, inv):
    _put(root, ".working/TODO.md", b"unplanned bytes\n")


def _occupied(root, inv):
    _put(root, ".working/TODO.md", _LIVE[".working/TODO.md"])
    return dict(doctor=_Doctor(INVALID, [_DRIFT]))


def _flip_6(root, inv):
    _put(root, "legacy/RULES.md", b"edited rules\n")


def _symlinked(root, inv):
    _remove(root, "legacy/RULES.md")
    os.symlink("../notes/old.md", os.path.join(root, "legacy", "RULES.md"))


# Each roster check's canonical single-mutation flip (the spec numbers the checks, so the flips are fixed).
_FLIPS = ((AUTHORITY, _flip_1), (DISCOVERY, _flip_2), (PRESERVATION, _flip_3), (OPERATIONAL, _flip_4),
          (RETIREMENT, _flip_6))


def _tree(root):
    out = {}
    for base, _dirs, files in os.walk(root):
        for name in files:
            with open(os.path.join(base, name), "rb") as fh:
                out[os.path.relpath(os.path.join(base, name), root)] = fh.read()
    return out


def _red(name, status=INVALID):
    return dict([(name, status)])


def self_test():
    """Synthetic post-apply fixtures under TemporaryDirectory, judged on each check's returned status and a
    named finding. No network and no process: the doctor is a stub, or the store validator with no
    observations. Exit 0 all pass, 1 a failed check, 2 a harness error."""
    from unittest import mock
    here = sys.modules[__name__]
    results = []

    def check(name, ok):
        results.append((name, bool(ok)))

    try:
        # The U25 switch as shipped (off): the genuine floor installed unmodified leaves check 4
        # CANNOT-EVALUATE naming U25 and nothing else, every other check green; an earlier check-4 failure
        # keeps its own reason (never U25), whether a finding (de-adopted), a modified live recipe, or a
        # reference file that does not match its embedded digest.
        def _u25_only(results):
            return (_only(results, _red(OPERATIONAL, CANNOT_EVALUATE))
                    and results[OPERATIONAL].findings == ["cannot evaluate: " + _U25_DEFERRED])

        shipped_off = _case()
        check("check-4-u25-off-shipped-floor-cannot", not U25_CI_IDENTITY_ASSERTED and _u25_only(shipped_off)
              and _says(shipped_off, OPERATIONAL, "(U25)"))
        gone = type("Resolution", (), dict(status=store.NOT_ADOPTED, detail="no store"))()
        with mock.patch.object(store, "resolve_store", lambda *a, **k: gone):
            absent_off = _case()
        check("check-4-u25-off-finding-keeps-reason", _only(absent_off, _red(OPERATIONAL))
              and _says(absent_off, OPERATIONAL, "never NOT-APPLICABLE")
              and not _says(absent_off, OPERATIONAL, "U25"))
        recipe_off = _case(lambda r, i: _append(r, _CI_RECIPE_REL, b"# x\n"))
        check("check-4-u25-off-ci-reason-kept", _only(recipe_off, _red(OPERATIONAL, CANNOT_EVALUATE))
              and _says(recipe_off, OPERATIONAL, "is not the pack's shipped recipe")
              and not _says(recipe_off, OPERATIONAL, "U25"))
        exit0 = b"#!/bin/sh\nexit 0\n"
        reference_off = _reference_case(recipe=exit0)
        check("check-4-u25-off-reference-reason-kept", _only(reference_off, _red(OPERATIONAL, CANNOT_EVALUATE))
              and _says(reference_off, OPERATIONAL, "does not match the digest embedded")
              and not _says(reference_off, OPERATIONAL, "U25"))
        # Every vector below runs with the switch on, the state U25 ships: the check-4 machinery is graded
        # as it will be then, and the switch-on baseline is VALID.
        mock.patch.object(here, "U25_CI_IDENTITY_ASSERTED", True).start()
        base = _case()
        check("baseline-green-1-4-6", _only(base, {}))
        check("check-4-u25-switch-on-valid", U25_CI_IDENTITY_ASSERTED and _only(base, {}))
        check("roster-never-green-without-check-5", not roster_green(base))
        greenable = dict(base, **dict([(WIRING, CheckResult(VALID))]))
        check("roster-green-needs-all-six", roster_green(greenable))
        for name in CHECKS:
            check("roster-red-on-" + name + "-invalid",
                  not roster_green(dict(greenable, **dict([(name, CheckResult(INVALID))]))))
            check("roster-red-on-" + name + "-cannot",
                  not roster_green(dict(greenable, **dict([(name, CheckResult(CANNOT_EVALUATE))]))))
        partial = dict(greenable)
        del partial[RETIREMENT]
        check("roster-red-on-missing-name", not roster_green(partial))
        for name, flip in _FLIPS:
            check("flip-" + name + "-red-only", _only(_case(flip), _red(name)))
        check("flip-1-names-live-preimage", _says(_case(_flip_1), AUTHORITY, "live preimage 'notes/old.md'"))
        check("flip-2-names-unaccounted", _says(_case(_flip_2), DISCOVERY, "'legacy/stray.md' has neither"))
        check("flip-3-names-missing-preimage", _says(_case(_flip_3), PRESERVATION, "missing from the adoption"))
        check("flip-4-names-unplanned", _says(_case(_flip_4), OPERATIONAL, "does not account"))
        check("flip-6-names-changed", _says(_case(_flip_6), RETIREMENT, "MUST NOT be retired"))
        # Check 1: the approval and receipt binding legs, then the planned-destination sweep.
        noauth = _case(lambda r, i: _remove(r, apply.approval_rel(_RUN)))
        check("check-1-approval-absent-red", noauth[AUTHORITY].status == INVALID
              and _says(noauth, AUTHORITY, "nothing authorizes"))
        unbound = _case(lambda r, i: _retoml(r, apply.approval_rel(_RUN),
                                             lambda d: d.update(plan_digest="sha256:" + "f" * 64)))
        check("check-1-approval-unbound-red", unbound[AUTHORITY].status == INVALID
              and _says(unbound, AUTHORITY, "approval:"))
        rebound = _case(lambda r, i: _retoml(r, apply.receipt_rel(_RUN), lambda d: d.update(
            run_id="adopt-20260917T120000Z-aaaaaaaaaaaaaaaa")))
        check("check-1-receipt-unbound-red", rebound[AUTHORITY].status == INVALID
              and _says(rebound, AUTHORITY, "name different adoption runs"))
        # One vector per receipt-binding comparison: each key, the release manifest, the approval, and the
        # file rows (a foreign path, a changed disposition, a changed before_digest). The receipt core's own
        # approval must agree with its plan and inventory digests, so those tampers move both together.
        other = "sha256:" + "e" * 64
        for key, value in (("product", "opf"), ("plan_digest", other), ("inventory_digest", other),
                           ("store_root", "sub")):
            def _bind(d, key=key, value=value):
                d[key] = value
                if key in d["approval"]:
                    d["approval"][key] = value
            got = _case(lambda r, i, f=_bind: _rereceipt(r, f))
            check("check-1-receipt-{}-bound".format(key), _only(got, _red(AUTHORITY))
                  and _says(got, AUTHORITY, "does not bind the approved plan's {}".format(key)))
        released = _case(lambda r, i: _rereceipt(r, lambda d: d["release"].update(
            manifest_sha256=other)))
        check("check-1-receipt-release-bound", _only(released, _red(AUTHORITY))
              and _says(released, AUTHORITY, "does not bind the approved plan's release.manifest_sha256"))
        reapproved = _case(lambda r, i: _rereceipt(r, lambda d: d["approval"].update(
            actor="someone-else")))
        check("check-1-receipt-approval-bound", _only(reapproved, _red(AUTHORITY))
              and _says(reapproved, AUTHORITY, "not the run's recorded approval"))
        for label, change, text in (("foreign-path", dict(path="elsewhere/X.md"), "is not a source"),
                                    ("disposition", dict(disposition="keep"), "but the approved plan binds"),
                                    ("before-digest", dict(before_digest=other), "but the approved plan binds")):
            got = _case(lambda r, i, c=change: _rereceipt(r, lambda d: d["files"][0].update(c)))
            check("check-1-receipt-file-" + label, _only(got, _red(AUTHORITY)) and _says(got, AUTHORITY, text))
        norec = _case(lambda r, i: _remove(r, apply.receipt_rel(_RUN)))
        check("check-1-receipt-absent-red", norec[AUTHORITY].status == INVALID
              and _says(norec, AUTHORITY, "no adoption receipt"))
        badcore = _case(lambda r, i: _rereceipt(r, lambda d: d.update(store_root="../x")))
        check("check-1-receipt-core-invalid-red", _only(badcore, _red(AUTHORITY))
              and _says(badcore, AUTHORITY, "receipt core:"))
        alien = _case(lambda r, i: _rereceipt(r, lambda d: d.update(product="zzz")))
        check("check-1-receipt-core-cannot", _only(alien, _red(AUTHORITY, CANNOT_EVALUATE)))
        gone_pre = _case(lambda r, i: _remove(r, "notes/old.md"))
        check("check-1-live-preimage-absent-red", _only(gone_pre, _red(AUTHORITY))
              and _says(gone_pre, AUTHORITY, "is absent from the live tree"))
        dest = _case(lambda r, i: _put(r, ".claude/settings.json", b"tampered bytes\n"))
        check("flip-1-destination-drift", _only(dest, _red(AUTHORITY))
              and _says(dest, AUTHORITY, "planned destination '.claude/settings.json'"))
        check("flip-1-destination-absent", _only(_case(lambda r, i: _remove(r, ".opf/hooks/pre-commit")),
                                                 _red(AUTHORITY)))
        drifted = _case(lambda r, i: _append(r, ".working/toml/manifest.toml", b"# drift\n"))
        check("check-1-manifest-drift-red", _only(drifted, _red(AUTHORITY))
              and _says(drifted, AUTHORITY, "planned destination '.working/toml/manifest.toml'"))
        # Check 2: the seal and binding legs, whole-tree accounting, ambiguous or malformed entries.
        excluded = _case(lambda r, i: dict(planning_inventory=_strayed(
            i, entries=[dict(path=".working/toml/x.toml", kind="excluded")])))
        check("check-2-excluded-entry-accounted", _says(excluded, DISCOVERY, "'legacy/stray.md' has neither")
              and not _says(excluded, DISCOVERY, "x.toml' has neither"))
        rekeyed = _case(lambda r, i: dict(planning_inventory=_reseal_inventory(
            i, exclusions=[dict(path=".working/toml", reason="machine"), dict(path="consumer", reason="other")])))
        check("check-2-unbound-inventory-red", rekeyed[DISCOVERY].status == INVALID
              and _says(rekeyed, DISCOVERY, "not the one the approved plan binds"))

        def _tamper_seal(inv):
            import tomllib
            doc = tomllib.loads(inv.decode("utf-8"))
            doc["decisions"] = [dict(note="tampered after sealing")]
            return _emit(doc)

        check("check-2-tampered-seal-red", _says(_case(lambda r, i: dict(planning_inventory=_tamper_seal(i))),
                                                 DISCOVERY, "does not seal its own bytes"))
        uninv = _case(lambda r, i: dict(planning_inventory=_reseal_inventory(
            i, entries=[row for row in _entries(i) if row["path"] != "legacy/RULES.md"],
            candidates=[p for p in sorted(_LIVE) if p != "legacy/RULES.md"])))
        check("check-2-source-not-inventoried-red",
              _says(uninv, DISCOVERY, "'legacy/RULES.md' is not an inventoried file"))
        stray_entry = dict(path="elsewhere/unaccounted.md", kind="file", size=2, digest="sha256:" + "b" * 64)
        anywhere = _case(lambda r, i: dict(planning_inventory=_reseal_inventory(
            i, entries=_entries(i) + [stray_entry])))
        check("check-2-inventoried-file-outside-working-red", _only(anywhere, _red(DISCOVERY))
              and _says(anywhere, DISCOVERY, "'elsewhere/unaccounted.md' has neither"))
        dup_entry = dict(path="legacy/RULES.md", kind="file", size=1, digest="sha256:" + "a" * 64)
        check("closed-duplicate-entry", _only(_case(lambda r, i: dict(planning_inventory=_reseal_inventory(
            i, entries=_entries(i) + [dup_entry]))), _red(DISCOVERY, CANNOT_EVALUATE)))
        check("closed-malformed-entry", _only(_case(lambda r, i: dict(planning_inventory=_reseal_inventory(
            i, entries=_entries(i) + [dict(path=123, kind="nonsense")]))), _red(DISCOVERY, CANNOT_EVALUATE)))
        check("closed-undigested-file-entry", _only(_case(lambda r, i: dict(
            planning_inventory=_reseal_inventory(i, entries=_entries(i) + [dict(
                path="legacy/undigested.md", kind="file")]))), _red(DISCOVERY, CANNOT_EVALUATE)))

        # Round 12: entries the planner never records, each sealed and bound by the fixture itself
        # (_EXTRA_ENTRIES), so every other check stays VALID: an excluded entry under no recorded exclusion
        # is a contradictory record and a non-canonical path escaping a recorded exclusion is malformed; both
        # are CANNOT-EVALUATE naming the entry, while the same file entry at a canonical path stays INVALID.
        def _with_entries(*rows):
            with mock.patch.object(here, "_EXTRA_ENTRIES", rows):
                return _case()

        foreign = dict(path="foreign.md", kind="file", size=1, digest=_digest(b"x"))
        control = _with_entries(foreign)
        check("check-2-unexcluded-file-control-red", _only(control, _red(DISCOVERY))
              and _says(control, DISCOVERY, "'foreign.md' has neither"))
        orphan = _with_entries(dict(path="foreign.md", kind="excluded"))
        check("check-2-excluded-without-exclusion-cannot", _only(orphan, _red(DISCOVERY, CANNOT_EVALUATE))
              and _says(orphan, DISCOVERY, "'foreign.md' is recorded excluded but lies under no recorded"))
        escaping = _with_entries(dict(foreign, path="consumer/../foreign.md"))
        check("check-2-noncanonical-entry-cannot", _only(escaping, _red(DISCOVERY, CANNOT_EVALUATE))
              and _says(escaping, DISCOVERY, "entry path 'consumer/../foreign.md'"))
        check("check-2-excluded-under-exclusion-valid", _only(_with_entries(dict(
            path=".working/toml/x.toml", kind="excluded")), {}))
        # Round 13: each producer invariant is re-checked by calling the producer's own function. Check 1:
        # a receipt whose release anchor flags are not both True is refused by adoption_record, and check 1
        # now says so through the same receipt_binding_refusals; the genuine receipt passes both.
        import datetime
        now = datetime.datetime.now(datetime.timezone.utc)

        def _record(mutate=None):
            with tempfile.TemporaryDirectory(prefix="opf-adopt-complete-") as root:
                _fixture(root)
                if mutate is not None:
                    _rereceipt(root, mutate)
                plan_doc = _toml_file(root, apply.plan_rel(_RUN))
                receipt_doc = _toml_file(root, apply.receipt_rel(_RUN))
            try:
                apply.adoption_record(_RUN, plan_doc, receipt_doc, now)
            except apply.AdoptApplyError as exc:
                return str(exc)
            return None

        check("check-1-r13-genuine-receipt-producer-accepts", _record() is None)
        anchorless = "the receipt records no observed, agreeing"
        for flag in ("independent_anchor_observed", "anchor_agreement"):
            def _unanchor(d, flag=flag):
                d["release"][flag] = False
            got = _case(lambda r, i, f=_unanchor: _rereceipt(r, f))
            check("check-1-r13-anchor-" + flag + "-red", _only(got, _red(AUTHORITY))
                  and _says(got, AUTHORITY, anchorless) and anchorless in (_record(_unanchor) or ""))
            with mock.patch.object(apply, "receipt_binding_refusals", lambda *a: []):
                check("check-1-r13-anchor-" + flag + "-load-bearing",
                      _only(_case(lambda r, i, f=_unanchor: _rereceipt(r, f)), {}))
        # Check 2: an empty directory omitted from empty_directories (the r12 reproduction) is
        # CANNOT-EVALUATE, the redundant list re-derived from the entries by the planner's own derivation;
        # the discriminating control records it and is INVALID, unaccounted.
        hollow = dict(path=".working/unaccounted-empty", kind="directory")
        omitted = _with_entries(hollow)
        check("check-2-r13-omitted-empty-directory-cannot", _only(omitted, _red(DISCOVERY, CANNOT_EVALUATE))
              and _says(omitted, DISCOVERY, "empty_directories omit ['.working/unaccounted-empty']"))
        with mock.patch.object(here, "_EXTRA_EMPTY", (hollow["path"],)):
            recorded = _with_entries(hollow)
        check("check-2-r13-recorded-empty-directory-control-red", _only(recorded, _red(DISCOVERY))
              and _says(recorded, DISCOVERY, "'.working/unaccounted-empty' has neither"))
        with mock.patch.object(planner, "empty_directories", lambda rows, roots: []):
            check("check-2-r13-empty-derivation-load-bearing", _only(_with_entries(hollow), {}))
        # Check 2: a file row is held to the planner's own row rule (entry_row_problem): the r12
        # reproduction (size -1) and each other row the planner never records are CANNOT-EVALUATE.
        def _edited(path, **changes):
            with mock.patch.object(here, "_ENTRY_EDITS", ((path, changes),)):
                return _case()

        for label, path, changes, text in (
                ("size-negative", "legacy/RULES.md", dict(size=-1), "size -1"),
                ("size-bool", "legacy/RULES.md", dict(size=True), "size True"),
                ("size-over-bound", "legacy/RULES.md", dict(size=planner.MAX_FILE_BYTES + 1),
                 "is not the byte count"),
                ("size-string", "legacy/RULES.md", dict(size="10"), "is not the byte count"),
                ("extra-field", "legacy/RULES.md", dict(note="x"), "records fields"),
                ("excluded-with-digest", _CONSUMER, dict(digest=_digest(b"x")), "records fields")):
            got = _edited(path, **changes)
            check("check-2-r13-entry-" + label + "-cannot", _only(got, _red(DISCOVERY, CANNOT_EVALUATE))
                  and _says(got, DISCOVERY, text))
        under = _with_entries(dict(path="consumer/other.md", kind="file", size=1, digest=_digest(b"x")))
        check("check-2-r13-file-under-exclusion-cannot", _only(under, _red(DISCOVERY, CANNOT_EVALUATE))
              and _says(under, DISCOVERY, "lies under a recorded exclusion"))
        with mock.patch.object(planner, "entry_row_problem", lambda row, exclusions: None):
            check("check-2-r13-row-rule-load-bearing", _only(_edited("legacy/RULES.md", size=-1), {}))
        # Check 4: the formerly occupied destination, the doctor contract, store identity, CI and views.
        occ = _case(_occupied)
        check("check-4-old-occupant-red", _only(occ, _red(OPERATIONAL)))
        check("check-4-old-occupant-named", _says(occ, OPERATIONAL, "still holds the old occupant's bytes")
              and _says(occ, OPERATIONAL, "doctor: C-VIEW-DRIFT"))
        check("check-4-doctor-drift-red", _only(_case(lambda r, i: dict(doctor=_Doctor(INVALID, [_DRIFT]))),
                                                _red(OPERATIONAL)))
        check("check-4-doctor-invalid-red", _only(_case(lambda r, i: dict(doctor=_Doctor(INVALID, ["C-X: y"]))),
                                                  _red(OPERATIONAL)))
        check("check-4-doctor-cannot", _only(_case(lambda r, i: dict(doctor=_Doctor(CANNOT_EVALUATE, [], ["z"]))),
                                             _red(OPERATIONAL, CANNOT_EVALUATE)))
        check("check-4-doctor-valid-with-cannot-list", _only(_case(lambda r, i: dict(
            doctor=_Doctor(VALID, [], ["z"]))), _red(OPERATIONAL, CANNOT_EVALUATE)))
        check("check-4-doctor-alien-status", _only(_case(lambda r, i: dict(doctor=_Doctor("GREENISH"))),
                                                   _red(OPERATIONAL, CANNOT_EVALUATE)))
        check("check-4-doctor-invalid-no-findings", _only(_case(lambda r, i: dict(doctor=_Doctor(INVALID))),
                                                          _red(OPERATIONAL, CANNOT_EVALUATE)))
        broken = _Doctor(VALID)
        broken.findings = ("C-X: y",)  # iterable, but not the list the doctor contract promises
        check("check-4-doctor-findings-not-list", _only(_case(lambda r, i: dict(doctor=broken)),
                                                        _red(OPERATIONAL, CANNOT_EVALUATE)))

        # Missing or non-list doctor evidence is CANNOT-EVALUATE, never read as an empty list.
        for label, value in (("absent", None), ("none", None), ("false", False), ("zero", 0), ("dict", {}),
                             ("string", ""), ("tuple", ())):
            for field in ("cannot_evaluate", "findings"):
                shaped = _Doctor(VALID)
                if label == "absent":
                    delattr(shaped, field)
                else:
                    setattr(shaped, field, value)
                check("check-4-doctor-{}-{}-cannot".format(field, label), _only(
                    _case(lambda r, i, d=shaped: dict(doctor=d)), _red(OPERATIONAL, CANNOT_EVALUATE)))

        def _raising(product_root):
            raise RuntimeError("doctor crashed")

        check("check-4-doctor-raises-cannot", _only(_case(lambda r, i: dict(doctor=_raising)),
                                                    _red(OPERATIONAL, CANNOT_EVALUATE)))
        check("check-4-no-doctor-cannot", _only(_case(lambda r, i: dict(doctor=None)),
                                                _red(OPERATIONAL, CANNOT_EVALUATE)))
        check("check-4-store-doctor-unobserved-not-green",
              _case(lambda r, i: dict(doctor=store_doctor()))[OPERATIONAL].status != VALID)
        check("check-4-manifest-removed-not-green", _only(_case(lambda r, i: _remove(
            r, ".working/toml/manifest.toml")),
            dict([(OPERATIONAL, CANNOT_EVALUATE), (AUTHORITY, INVALID)])))
        gone = type("Resolution", (), dict(status=store.NOT_ADOPTED, detail="no store"))()
        with mock.patch.object(store, "resolve_store", lambda *a, **k: gone):
            absent = _case()
        check("check-4-de-adopted-red", _only(absent, _red(OPERATIONAL))
              and _says(absent, OPERATIONAL, "never NOT-APPLICABLE"))
        real_resolve = store.resolve_store

        def _misresolve(path, **kw):
            found = real_resolve(path, **kw)
            return type("Resolution", (), dict(status=found.status, store_root=found.store_root,
                                               machine_rel=".working/other", detail=None))()

        with mock.patch.object(store, "resolve_store", _misresolve):
            misid = _case()
        check("check-4-store-identity-red", _only(misid, _red(OPERATIONAL))
              and _says(misid, OPERATIONAL, "not the planned identity"))

        def _misrooted(path, **kw):
            found = real_resolve(path, **kw)
            return type("Resolution", (), dict(status=found.status, store_root=tempfile.gettempdir(),
                                               machine_rel=found.machine_rel, detail=None))()

        with mock.patch.object(store, "resolve_store", _misrooted):
            misroot = _case()
        check("check-4-store-root-identity-red", _only(misroot, _red(OPERATIONAL))
              and _says(misroot, OPERATIONAL, "not the planned identity"))
        # The CI leg by template identity (_ci_template_identity): the shipped template and recipe installed
        # verbatim are VALID; any other workflow or recipe is CANNOT-EVALUATE, the reason naming the template
        # to install unmodified and OPF_TOOL as unsupported, never VALID and never INVALID. Each vector changes
        # only the CI floor, so only check 4 moves (_only).
        template, recipe = _shipped_floor()
        verbatim = _case()
        with tempfile.TemporaryDirectory(prefix="opf-adopt-complete-") as root:
            _fixture(root)
            with open(os.path.join(root, *_CI_PATH.split("/")), "rb") as fh:
                installed = fh.read()
        check("check-4-ci-template-verbatim-valid", _only(verbatim, {}) and installed == template
              and _TEMPLATE_RUN_LINE in template and b"Copy this file to .github/workflows/opf.yml" in template)

        def _ci_cannot(results, *texts):
            return (_only(results, _red(OPERATIONAL, CANNOT_EVALUATE))
                    and _says(results, OPERATIONAL, "install the pack's template")
                    and _says(results, OPERATIONAL, "OPF_TOOL")
                    and all(_says(results, OPERATIONAL, text) for text in texts))

        flipped = template.replace(b"name: OPF\n", b"name: OPG\n", 1)
        command = b"sh " + _CI_RECIPE_REL.encode("ascii") + b" ."
        # A modified copy the plan records, the round-9 reproductions among them (rebased on the shipped
        # template): a canonical job beside a schema error, a step without run carrying a broken with:, two
        # invalid job ids, and |- and | chomping of the run line. Each is CANNOT-EVALUATE, the reader they
        # exercised being gone.
        for label, bad in (
                ("byte-changed", flipped),
                ("comment-added", template + b"# a local note\n"),
                ("r9-canonical-job-masks-schema-error", template + (
                    b"  bad:\n    runs-on: ubuntu-latest\n    permissions: {contents: banana}\n"
                    b"    steps:\n      - run: opf doctor --require-store\n")),
                ("r9-step-without-run", template.replace(b"uses: actions/checkout@v4",
                                                         b"uses: actions/checkout@v4\n        with: [broken]")),
                ("r9-job-id-dotted", template.replace(b"  opf:", b"  opf.bad:")),
                ("r9-job-id-digit", template.replace(b"  opf:", b'  "1opf":')),
                ("r9-strip-chomping", template.replace(b"run: " + command + b"\n",
                                                       b"run: |-\n          " + command + b"\n\n\n")),
                ("r9-clip-chomping", template.replace(b"run: " + command + b"\n",
                                                      b"run: |\n          " + command + b"\n\n\n"))):
            with mock.patch.object(here, "_CI_BYTES", bad):
                got = _case()
            check("check-4-ci-" + label, bad != template and _ci_cannot(got, "is not the pack's shipped template"))
        # The identity leg is load-bearing: with it emptied, the modified copy the plan records is VALID.
        with mock.patch.object(here, "_CI_BYTES", flipped), \
                mock.patch.object(here, "_ci_template_identity", lambda ev, row: []):
            check("check-4-ci-identity-load-bearing", _only(_case(), {}))
        check("check-4-ci-edited-after-plan", _ci_cannot(_case(lambda r, i: _put(r, _CI_PATH, flipped)),
                                                         "does not match its planned bytes", "is not byte-identical"))
        check("check-4-ci-member-absent", _ci_cannot(_case(lambda r, i: _remove(r, _CI_PATH)),
                                                     "'.github/workflows/opf.yml' is absent"))

        def _relocated(root, inv):
            _put(root, ".github/workflows/other.yml", template)
            _remove(root, _CI_PATH)

        check("check-4-ci-other-path", _ci_cannot(_case(_relocated), "'.github/workflows/opf.yml' is absent"))
        for path in ("ci/opf.yml", ".github/workflows/sub/opf.yml", ".github/opf.yml", ".github/workflows/.yml"):
            with mock.patch.object(here, "_CI_PATH", path):
                got = _case()
            check("check-4-ci-not-a-workflow-path-" + path, _ci_cannot(got, "is not a file directly under"))
        with mock.patch.object(here, "_CI_PATH", ".github/workflows/floor.yaml"):
            check("check-4-ci-plan-named-path-valid", _only(_case(), {}))
        check("check-4-ci-recipe-edited", _ci_cannot(_case(lambda r, i: _put(r, _CI_RECIPE_REL, recipe + b"# x\n")),
                                                     "does not match its planned bytes",
                                                     "is not the pack's shipped recipe"))
        weakened = recipe.replace(b"|| exit $?", b"|| true")
        with mock.patch.object(here, "_CI_RECIPE_BYTES", weakened):
            got = _case()
        check("check-4-ci-recipe-modified-planned", weakened != recipe and _ci_cannot(
            got, "at a digest that is not the pack's shipped recipe", "is not the pack's shipped recipe"))
        check("check-4-ci-recipe-absent", _ci_cannot(_case(lambda r, i: _remove(r, _CI_RECIPE_REL)),
                                                     "'opf/enforcement/ci/opf-ci.sh' is absent"))
        with mock.patch.object(here, "_TEMPLATE_RUN_LINE", b"        run: sh elsewhere/opf-ci.sh .\n"):
            got = _case()
        check("check-4-ci-template-inconsistent-cannot", _only(got, _red(OPERATIONAL, CANNOT_EVALUATE))
              and _says(got, OPERATIONAL, "the pack is inconsistent"))
        # The reference files are authenticated (_shipped): the round-10 reproductions, each a reference
        # file replaced or symlinked in the pack and matched by the live copy the plan records, were VALID
        # before and are CANNOT-EVALUATE now; the genuine floor in the same scratch pack root stays VALID,
        # and each embedded digest is load-bearing.
        def _unauthenticated(results, text):
            return _only(results, _red(OPERATIONAL, CANNOT_EVALUATE)) and _says(results, OPERATIONAL, text)

        if_false = template.replace(b"    runs-on: ubuntu-latest\n", b"    if: false\n    runs-on: ubuntu-latest\n")
        check("check-4-r10-scratch-pack-genuine-valid", _only(_reference_case(), {}))
        for label, kwargs, text in (
                ("reference-recipe-exit-0", dict(recipe=exit0), "does not match the digest embedded"),
                ("reference-recipe-weakened", dict(recipe=weakened), "does not match the digest embedded"),
                ("reference-template-if-false", dict(template=if_false), "does not match the digest embedded"),
                ("reference-recipe-symlink-exit-0", dict(recipe=exit0, symlinked=(_SHIPPED_RECIPE,)),
                 "a symlink or special entry is refused"),
                ("reference-recipe-symlink-genuine", dict(symlinked=(_SHIPPED_RECIPE,)),
                 "a symlink or special entry is refused"),
                ("reference-template-symlink-genuine", dict(symlinked=(_SHIPPED_TEMPLATE,)),
                 "a symlink or special entry is refused")):
            check("check-4-r10-" + label, if_false != template and _TEMPLATE_RUN_LINE in if_false
                  and _unauthenticated(_reference_case(**kwargs), text))
        for attr in ("_SHIPPED_TEMPLATE_SHA256", "_SHIPPED_RECIPE_SHA256"):
            with mock.patch.object(here, "_CI_BYTES", template), mock.patch.object(here, "_CI_RECIPE_BYTES", recipe), \
                    mock.patch.object(here, attr, "0" * 64):
                got = _case()
            check("check-4-embedded-digest-load-bearing-" + attr, _unauthenticated(
                got, "does not match the digest embedded"))
        noview = _case(lambda r, i: _remove(r, ".working/TODO.md"))
        check("check-4-view-missing-red", _only(noview, _red(OPERATIONAL))
              and _says(noview, OPERATIONAL, "is missing"))
        repointed = _case(lambda r, i: _put(r, _CONSUMER, b"stale consumer\n"))
        check("check-4-repoint-drifted-red", _only(repointed, _red(OPERATIONAL))
              and _says(repointed, OPERATIONAL, "planned repointing"))
        # Check 3: archive preservation, bundle claims, the move source, and the restore exercise.
        corrupt = _case(lambda r, i: _put(r, apply.archive_rel(_RUN, "legacy/RULES.md"), b"other\n"))
        check("check-3-preimage-corrupted", _only(corrupt, _red(PRESERVATION))
              and _says(corrupt, PRESERVATION, "does not match the plan digest of"))
        unclaimed = _case(lambda r, i: _unclaim(r, apply.archive_rel(_RUN, "legacy/RULES.md")))
        check("check-3-unclaimed-preimage-red", _only(unclaimed, _red(PRESERVATION))
              and _says(unclaimed, PRESERVATION, "not claimed by the run's bundle inventory"))
        moved = _case(lambda r, i: _put(r, "adopter/MOVE.md", b"EDITED\n"))
        check("check-3-move-source-drifted-red", _only(moved, _red(PRESERVATION))
              and _says(moved, PRESERVATION, "MUST NOT be moved"))
        check("check-3-move-source-absent-red", _only(_case(lambda r, i: _remove(r, "adopter/MOVE.md")),
                                                      _red(PRESERVATION)))
        check("check-3-restore-mismatch-found", _restore_exercise([("a/b.md", b"x", _digest(b"y"))]) == ["a/b.md"])
        check("check-3-restore-match-clean", _restore_exercise([("a/b.md", b"x", _digest(b"x"))]) == [])
        with mock.patch.object(here, "_restore_exercise", lambda items: [row[0] for row in items]):
            check("check-3-restore-load-bearing", _only(_case(), _red(PRESERVATION)))
        check("check-6-retire-absent", _only(_case(lambda r, i: _remove(r, "legacy/RULES.md")), _red(RETIREMENT)))
        check("check-6-occupying-archive-changed", _case(lambda r, i: _put(
            r, apply.archive_rel(_RUN, ".working/TODO.md"), b"x\n"))[RETIREMENT].status == INVALID)
        # Fail closed: unreadable or unparseable input is CANNOT-EVALUATE, never a pass.
        every = dict((name, CANNOT_EVALUATE) for name in EVALUATED)
        check("closed-plan-garbage", _only(_case(lambda r, i: _put(r, apply.plan_rel(_RUN), b"x = [")), every))
        check("closed-plan-absent", _only(_case(lambda r, i: _remove(r, apply.plan_rel(_RUN))), every))
        check("closed-approval-garbage", _only(_case(lambda r, i: _put(r, apply.approval_rel(_RUN), b"= 1")),
                                               dict([(AUTHORITY, CANNOT_EVALUATE), (PRESERVATION, INVALID)])))
        check("closed-receipt-garbage", _case(lambda r, i: _put(
            r, apply.receipt_rel(_RUN), b"[["))[AUTHORITY].status == CANNOT_EVALUATE)
        check("closed-inventory-absent", _only(_case(lambda r, i: dict(planning_inventory=None)),
                                               _red(DISCOVERY, CANNOT_EVALUATE)))
        check("closed-inventory-garbage", _only(_case(lambda r, i: dict(planning_inventory=b"x = [")),
                                                _red(DISCOVERY, CANNOT_EVALUATE)))
        check("closed-bundle-inventory-garbage", _case(lambda r, i: _put(
            r, apply.inventory_rel(_RUN), b"x = ["))[PRESERVATION].status == CANNOT_EVALUATE)
        check("closed-symlink-retire-source", _case(_symlinked)[RETIREMENT].status == CANNOT_EVALUATE)
        with mock.patch.object(apply, "verify_bundle", lambda *a: schema._cannot("bundle unreadable")):
            check("closed-bundle-cannot-load-bearing", _only(_case(), _red(PRESERVATION, CANNOT_EVALUATE)))
        check("closed-bad-run-id", all(r.status == CANNOT_EVALUATE for r in evaluate("/", "../x").values()))
        other_run = "adopt-20260917T120000Z-aaaaaaaaaaaaaaaa"

        def _other_run_plan(r, i):
            # A self-consistent VALID plan for another run (its preservation homes renamed with it).
            doc = _toml_file(r, apply.plan_rel(_RUN))
            doc["sources"] = [dict(row, preservation=row["preservation"].replace(_RUN, other_run))
                              for row in doc["sources"]]
            doc["effects"] = schema.derive_effects(doc["ops"], doc["sources"], ".working/toml/manifest.toml")
            _put(r, apply.plan_rel(_RUN), _emit(_sealed(dict(doc, run_id=other_run), "plan_digest")))

        renamed = _case(_other_run_plan)
        check("closed-plan-names-other-run", _only(renamed, every)
              and _says(renamed, AUTHORITY, "names run"))
        with tempfile.TemporaryDirectory(prefix="opf-adopt-complete-") as root:
            inv = _fixture(root)
            before = _tree(root)
            evaluate(root, _RUN, planning_inventory=inv, doctor=_Doctor())
            check("read-only-live-tree", _tree(root) == before)
        # Direct-call vectors for legs a VALID frozen plan cannot reach end to end: a foreign preservation
        # destination and a missing CI row (frozen_plan refuses both plans), and check 2's accounting leg
        # alone (the canonical _flip_2 also moves the bound inventory digest).
        with tempfile.TemporaryDirectory(prefix="opf-adopt-complete-") as root:
            inv = _fixture(root)
            ev = _Evaluation()
            ev.product_root, ev.run_id, ev.doctor, ev.planning_inventory = root, _RUN, _Doctor(), inv
            ev.root_fd = apply._open_product_root(root)
            try:
                plan = apply.frozen_plan(_read(ev, apply.plan_rel(_RUN)))
                tampered = [dict(row) for row in plan["sources"]]
                for row in tampered:
                    if row["path"] == "legacy/RULES.md":
                        row["preservation"] = "elsewhere/RULES.md"
                ev.plan = dict(plan, sources=tampered)
                direct = _Report()
                _check_preservation(ev, direct)
                check("check-3-foreign-preservation-red", direct.result().status == INVALID
                      and any("not under this run's adoption archive" in f for f in direct.findings))

                def _ci_direct(members):
                    ev.plan = dict(plan, enforcement=[dict(row, members=members) if row["platform"] == "ci"
                                                      else row for row in plan["enforcement"]])
                    report = _Report()
                    try:
                        _check_operational(ev, report)
                    except Unevaluable as exc:
                        report.cannot.append(str(exc))
                    return report.result()

                def _named(result, text):
                    return result.status == CANNOT_EVALUATE and any(text in f for f in result.findings)

                # The recipe is read live whether or not the CI row plans it; the row names exactly one
                # workflow; a shipped file that cannot be read fails closed.
                workflow_only = [dict(path=_CI_PATH, digest=_digest(template))]
                check("check-4-ci-recipe-unplanned-direct", _ci_direct(workflow_only).status == VALID)
                _put(root, ".github/workflows/second.yml", template)
                check("check-4-ci-two-workflows-direct", _named(_ci_direct(workflow_only + [dict(
                    path=".github/workflows/second.yml", digest=_digest(template))]), "2 workflow members"))
                check("check-4-ci-no-workflow-direct", _named(_ci_direct(
                    [dict(path=_CI_RECIPE_REL, digest=_digest(recipe))]), "0 workflow members"))
                for attr, name in (("_SHIPPED_TEMPLATE", "github-actions.yml"), ("_SHIPPED_RECIPE", "opf-ci.sh")):
                    with mock.patch.object(here, attr, "enforcement/ci/absent/" + name):
                        unread = _ci_direct(workflow_only)
                    check("check-4-ci-shipped-unreadable-" + name,
                          _named(unread, "cannot read the pack's shipped CI file"))
                _remove(root, _CI_RECIPE_REL)
                check("check-4-ci-recipe-unplanned-absent-direct", _named(
                    _ci_direct(workflow_only), "the recipe the template runs, 'opf/enforcement/ci/opf-ci.sh', is absent"))
                ev.plan = dict(plan, enforcement=[row for row in plan["enforcement"]
                                                 if row["platform"] != "ci"])
                direct = _Report()
                _check_operational(ev, direct)
                check("check-4-no-ci-row-red",
                      any("binds no CI enforcement row" in f for f in direct.findings))
                import tomllib
                inv2 = _strayed(inv)
                ev.planning_inventory = inv2
                ev.plan = dict(plan, inventory_digest=tomllib.loads(
                    inv2.decode("utf-8"))["inventory_digest"])
                direct = _Report()
                _check_discovery(ev, direct)
                check("check-2-accounting-only-red", direct.result().status == INVALID
                      and len(direct.findings) > 0
                      and all("has neither a disposition" in f for f in direct.findings))

                def _discovery(**changes):
                    resealed = _reseal_inventory(inv, **changes)
                    ev.planning_inventory = resealed
                    ev.plan = dict(plan, inventory_digest=tomllib.loads(resealed.decode("utf-8"))["inventory_digest"])
                    report = _Report()
                    try:
                        _check_discovery(ev, report)
                    except Unevaluable:
                        return None
                    return report.findings

                check("check-2-empty-directory-accounted", _discovery(entries=_entries(inv) + [dict(
                    path="empty/dir", kind="directory")], empty_directories=["empty/dir"]) == [
                    "inventory entry 'empty/dir' has neither a disposition nor a recorded exclusion"])
                check("check-2-control-area-exempt", _discovery(entries=_entries(inv) + [dict(
                    path=".working/imports/x.md", kind="file", size=1, digest=_digest(b"x"))]) == [])
                check("check-2-alien-kind-cannot", _discovery(entries=_entries(inv) + [dict(
                    path="legacy/odd.md", kind="symlink")]) is None)
                redigested = [dict(row, digest="sha256:" + "c" * 64) if row["path"] == "legacy/RULES.md" else row
                              for row in _entries(inv)]
                check("check-2-source-digest-bound", _discovery(entries=redigested) == [
                    "plan source 'legacy/RULES.md' is not an inventoried file with its plan digest"])
                # Every other path check 2 tests containment on is held to the planner's validator too.
                recorded = tomllib.loads(inv.decode("utf-8"))["observation"]["exclusions"]
                check("check-2-noncanonical-exclusion-cannot", _discovery(
                    exclusions=recorded + [dict(path="consumer/..", reason="x")]) is None)
                check("check-2-noncanonical-candidate-cannot", _discovery(
                    candidates=sorted(_LIVE) + ["consumer/../stray.md"]) is None)
                check("check-2-git-candidate-cannot", _discovery(candidates=sorted(_LIVE) + [".git/config"]) is None)
                check("check-2-noncanonical-empty-directory-cannot", _discovery(
                    empty_directories=["empty//dir"]) is None)

                # Round 13: the redundant lists re-derived by the planner's own derivations, and the source
                # roots held to the planner's own _roots; each disagreement is CANNOT-EVALUATE naming it.
                def _refused(**changes):
                    resealed = _reseal_inventory(inv, **changes)
                    ev.planning_inventory = resealed
                    ev.plan = dict(plan, inventory_digest=tomllib.loads(resealed.decode("utf-8"))["inventory_digest"])
                    try:
                        _check_discovery(ev, _Report())
                    except Unevaluable as exc:
                        return str(exc)
                    return ""

                ghost = dict(path="legacy/ghost.md", kind="file", size=1, digest=_digest(b"g"))
                hollow_dir = dict(path="legacy/hollow", kind="directory")
                for label, changes, text in (
                        ("candidate-without-entry", dict(candidates=sorted(sorted(_LIVE) + ["legacy/ghost.md"])),
                         "candidates name ['legacy/ghost.md']"),
                        ("candidate-omitted-under-root", dict(entries=_entries(inv) + [ghost]),
                         "candidates omit ['legacy/ghost.md']"),
                        ("candidates-unsorted", dict(candidates=sorted(_LIVE, reverse=True)), "not sorted"),
                        ("candidates-duplicated", dict(candidates=sorted(sorted(_LIVE) + ["legacy/RULES.md"])),
                         "duplicate-free"),
                        ("empty-directory-without-entry", dict(empty_directories=["legacy/hollow"]),
                         "empty_directories name ['legacy/hollow']"),
                        ("empty-directory-with-descendant", dict(entries=_entries(inv) + [dict(
                            path="legacy", kind="directory")], empty_directories=["legacy"]),
                         "empty_directories name ['legacy']"),
                        ("empty-directory-omitted-under-root", dict(entries=_entries(inv) + [hollow_dir]),
                         "empty_directories omit ['legacy/hollow']"),
                        ("sources-overlapping", dict(sources=["legacy", "legacy/sub"]), "overlapping"),
                        ("sources-unsorted", dict(sources=["notes", "legacy", "adopter"]), "recorded sorted"),
                        ("sources-noncanonical", dict(sources=["legacy/../notes"]), "source roots"),
                        ("sources-not-list", dict(sources="legacy"), "lacks")):
                    check("check-2-r13-" + label + "-cannot", text in _refused(**changes))
                # The controls: the same rows recorded as the planner records them grade, never refuse.
                check("check-2-r13-candidate-recorded-control", _refused(
                    entries=_entries(inv) + [ghost], candidates=sorted(sorted(_LIVE) + [ghost["path"]])) == "")
                check("check-2-r13-empty-directory-recorded-control", _refused(
                    entries=_entries(inv) + [hollow_dir], empty_directories=["legacy/hollow"]) == "")
                # Check 1's destination sweep over every op kind it owns (the fixture plants none of these).
                extra = [dict(op="create-file", path="docs/NEW.md", content_digest="sha256:" + "1" * 64),
                         dict(op="plant-governance", path="CLAUDE.md", content_digest="sha256:" + "2" * 64),
                         dict(op="enable-hook", registration_path=".opf/hooks/registry.json",
                              new_digest="sha256:" + "3" * 64),
                         dict(op="register-unmanaged", new_digest="sha256:" + "4" * 64)]
                ev.plan = dict(plan, ops=plan["ops"] + extra)
                wanted = _planned_destinations(ev)
                check("check-1-destinations-every-op-kind", wanted.get("docs/NEW.md") == "sha256:" + "1" * 64
                      and wanted.get("CLAUDE.md") == "sha256:" + "2" * 64
                      and wanted.get(".opf/hooks/registry.json") == "sha256:" + "3" * 64
                      and wanted.get(".working/toml/manifest.toml") == "sha256:" + "4" * 64
                      and _CI_PATH not in wanted and _CI_RECIPE_REL not in wanted
                      and ".working/TODO.md" not in wanted)
                # Check 3 re-reads the bundle inventory after verify_bundle: that read is held to the apply
                # shell's own inventory validator too (here verify_bundle is stubbed VALID, so only it grades).
                ev.plan = plan
                bundled = _toml_file(root, apply.inventory_rel(_RUN))
                bundled["file"].append(apply.inventory_row("../escape.md", b"x"))
                _put(root, apply.inventory_rel(_RUN), _emit(bundled))
                direct = _Report()
                with mock.patch.object(apply, "verify_bundle", lambda *a: schema._ok()):
                    try:
                        _check_preservation(ev, direct)
                    except Unevaluable as exc:
                        direct.cannot.append(str(exc))
                check("check-3-bundle-inventory-validated", direct.result().status == CANNOT_EVALUATE
                      and any("the bundle inventory:" in f for f in direct.result().findings))
            finally:
                store._close_fd_exc_safe(ev.root_fd)
        # Dispatch wiring: each roster name runs exactly its own check function. (These replace the former
        # removal-mutant vectors, which could not fail: an emptied check body always grades VALID.)
        for name in EVALUATED:
            probe = "dispatch-probe-" + name

            def _probing(ev, rep, _probe=probe):
                rep.finding(_probe)

            with mock.patch.object(here, _CHECK_FUNCTIONS[name], _probing):
                probed = _case()
            check("dispatch-" + name + "-load-bearing", probed[name].status == INVALID
                  and _says(probed, name, probe)
                  and all(probed[n].status == VALID for n in EVALUATED if n != name))
    except Exception as exc:  # noqa: BLE001  fail-closed backstop, never an uncaught exit-1 escape
        print("OPF-ADOPT-COMPLETE SELF-TEST: harness error ({!r}); failing closed to exit 2".format(exc),
              file=sys.stderr)
        return 2
    finally:
        mock.patch.stopall()
    failed = [name for name, ok in results if not ok]
    for name in failed:
        print("FAIL: " + name, file=sys.stderr)
    print("OPF-ADOPT-COMPLETE SELF-TEST: {} checks, {} failed".format(len(results), len(failed)))
    return 1 if failed else 0


def main():
    if sys.argv[1:] in (["--self-test"], ["--selftest"]):
        return self_test()
    print("usage: _opf_adopt_complete.py --self-test (a library module: evaluate() and roster_green())",
          file=sys.stderr)
    return 2


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-test"]:
        sys.exit(self_test())
    sys.exit(main())
