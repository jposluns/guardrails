#!/usr/bin/env python3
"""OPF import completion: the read-only per-source evaluator (OPF-SPEC 1.3.0 section 14.1).

Spec sections implemented: 14.1's per-source import completion ("For each migrate-disposed source, import
completion MUST verify the preserved original's digest (the archived copy under section 14.2 for a source
that occupied a managed destination, the frozen live file and its apply-archived retirement preimage
otherwise), at least one imported record referencing that source or a recorded skip under the approved
policy, and doctor VALID over both the strict store and the imported series, evaluated on the live
tree"); 14.1's homes-1 clause (the completion checks re-read inventories and payload digests themselves,
because C-EVIDENCE-ENUM is inactive until homes 2), whose re-reads this evaluator keeps on homes 2 as well,
as defence in depth; and 8.8's correction chain ("Such a corrected import is a fresh run ... and source
completion (section 14.1) evaluates the correcting run"). Recording the result in the receipt's
outcome-event chain, the retirement transaction, `import_status` and the `opf import` modes are later units;
this module decides and records nothing.

evaluate_source(product_root, run_id, source, doctor=...) grades ONE migrate-disposed source of the
adoption run `run_id`, in four legs, each reporting under one SourceResult:
  receipt: the adoption receipt core this import runs under (spec 8.8: import refuses without one) is
    present, validates and binds this run and the approved plan (the apply shell's own
    receipt_binding_refusals), and its outcome-event chain (event-0001.toml, the genesis, then
    event-0002.toml and on, read in order up to the first absent name and at most MAX_EVENTS) validates
    through the shipped chain validator, rooted at the receipt core's digest;
  preserved: the run's adoption bundle verifies from disk (_opf_adopt_apply.verify_bundle: every inventory
    and every payload it lists); the source's preserved copy under .working/archive/adoption/<run-id>/
    holds its plan digest and is claimed by the bundle inventory; and a non-occupying source's frozen live
    file still holds its plan digest (an occupying source left the live tree at apply);
  imports: the imported records referencing the source are those, in the imported managed leaves
    (<type>.imported.index.toml per type except legacy_fragment, and worklog.imported.toml, inline in the
    machine store), whose import.source is exactly the source's preserved original
    .working/imported/import/<import-run>/originals/<source-path> of their own import.run. Every import run
    holding such a record re-reads its bundle inventory (the doctor's own row validator, kind import) and
    every payload it lists, claims the original, the original holds the plan digest, and each referencing
    record's source_sha256 is the original's. The run completion evaluates is the one run no other of those
    runs corrects (a record of the correcting run links supersedes or corrects to a record of the corrected
    one); none or more than one is ambiguous, CANNOT-EVALUATE. The source completes on at least one record
    of that run, or, with no record, on a recorded skip under the plan's skip policy attributed-skip: an
    outcome event of kind SKIP_EVENT_KIND naming the source (SKIP_EVENT_SOURCE) and its actor
    (SKIP_EVENT_ACTOR). A skip recorded under any other policy is a finding;
  doctor: the caller's doctor run over the live tree is VALID, with no finding and no cannot-evaluate, and
    grades each of the four section 8.3 imported-series checks (IMPORTED_SERIES_CHECKS) PASS. An INVALID
    doctor keeps its own findings; a VALID one whose roster lacks an imported-series check, as every doctor
    of this release does, is CANNOT-EVALUATE naming the missing checks, so no source is green until the
    imported-series doctor checks ship.

Read-only: every live read is contained and no-follow through the apply shell's own reader
(_opf_adopt_apply._read_live, single-linked regular files only, capped), every TOML input is admitted only in
its canonical emitted form (_opf_adopt_apply._canonical_toml), nothing is written and no process is spawned
by this module. The doctor's git observations are a process-spawning gather, so the caller supplies the
doctor run (store_doctor builds one from caller-supplied inert observations); with no doctor the doctor leg
is CANNOT-EVALUATE, never a pass.

Outcome model (single-sourced from _opf_store): VALID (green: every term holds), INVALID (incomplete, with
findings) or CANNOT-EVALUATE (an input that cannot be read, parsed or disambiguated, never a pass), which
dominates INVALID. An absent required artefact (no receipt, no preserved copy, no live frozen source, no
import inventory, no record and no skip) is a finding; an unreadable or malformed one is CANNOT-EVALUATE. A
source the approved plan does not dispose `migrate` and scope for import is CANNOT-EVALUATE: completion
evaluates only those.

DISCLOSED RESIDUALS: the outcome-event schema of this release carries no per-source kind and no `source`
key, so the shipped chain validator refuses a chain holding a skip event (an out-of-vocabulary kind is
CANNOT-EVALUATE) and no source can complete through a recorded skip until a later release registers
SKIP_EVENT_KIND and its keys in that schema; the skip shape here (a dedicated kind naming the source and
its actor, honoured only under attributed-skip) is the import plan's proposal and is pending that
schema's decision. Event files after the genesis are taken to follow its event-<NNNN>.toml naming, read
until the first absent number, so a file past a gap is not read, and event and bundle membership (an
unlisted file in a bundle) is not reconciled here. The referencing records are matched on their import
table alone; their envelope, ids, links and segregation are the doctor's imported-series checks, which the
doctor leg requires. A non-canonical spelling of a TOML input is CANNOT-EVALUATE, as in the adoption
completion evaluator. Correction is read from the referencing records' supersedes and corrects links
only; a batch's own declaration of the run it corrects is the writer's and is not read. The doctor verdict
is only as complete as the caller's observations. Green proves source accounting and preservation, never
byte-level mapping coverage or semantic fidelity (spec 14.1).

Offline, stdlib only, fail-closed. It lives under `opf/tools/` and imports ONLY sibling `opf/tools/`
modules, so the standalone-closure property holds.

Exit convention (the repo's gates and the sibling OPF units): 0 clean, 1 a finding, 2 cannot-evaluate.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: _opf_import_complete.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import hashlib
import os
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _opf_adopt as schema          # noqa: E402
import _opf_adopt_apply as apply     # noqa: E402
import _opf_schema as records        # noqa: E402
import _opf_store as store           # noqa: E402
from _opf_emit import emit_checked   # noqa: E402

VALID, INVALID, CANNOT_EVALUATE = store.VALID, store.INVALID, store.CANNOT_EVALUATE
# The four section 8.3 imported-series checks the doctor roster carries once the imported series activates
# (spec 4.2): doctor VALID "over both the strict store and the imported series" grades each PASS.
IMPORTED_SERIES_CHECKS = ("C-IMPORTED-SCHEMA", "C-IMPORTED-IDS", "C-IMPORTED-PROVENANCE",
                          "C-IMPORTED-SEGREGATION")
# The imported managed leaves (spec 4.2, 8.3), inline in the machine store in either layout: one index per
# enabled type except the importer-only legacy_fragment, and the imported worklog on its own number line.
IMPORTED_INDEX_SUFFIX = ".imported.index.toml"
IMPORTED_WORKLOG_NAME = "worklog.imported.toml"
IMPORTED_TYPES = tuple(sorted(
    [name for name, ns in store.BASELINE_TYPES.items() if ns in records.IMPORTED_NAMESPACES]
    + [name for name, (ns, _module) in store.MODULE_TYPES.items() if ns in records.IMPORTED_NAMESPACES]))
# The link relations by which a correcting run's records replace the corrected run's (spec 8.8).
CORRECTING_RELS = ("supersedes", "corrects")
# The recorded skip (spec 14.1 "a recorded skip under the approved policy"): an outcome event of this kind,
# naming the skipped plan source and the actor who skipped it, honoured only under the plan's skip policy
# SKIP_POLICY. The event schema of this release does not register the kind or the two keys, so its chain
# validator refuses such an event (module docstring, residuals).
SKIP_EVENT_KIND = "source-skipped"
SKIP_EVENT_SOURCE = "source"
SKIP_EVENT_ACTOR = "actor"
SKIP_POLICY = "attributed-skip"
# The outcome events beside the receipt: the genesis event-0001.toml (_opf_adopt_apply.GENESIS_EVENT_NAME),
# then one numbered file per later event, read in order up to the first absent number.
EVENT_NAME = "event-{:04d}.toml"
MAX_EVENTS = 9999


class Unevaluable(Exception):
    """An input the evaluator cannot read, parse or disambiguate: CANNOT-EVALUATE, never a pass."""


class SourceResult:
    """One migrate-disposed source's import-completion verdict (spec 14.1): `status` VALID, INVALID or
    CANNOT-EVALUATE with its `findings`; `import_run` the import run completion evaluated (the correcting
    run when the source was corrected, spec 8.8) or None; `records` the sorted ids of that run's records
    referencing the source; `skipped` True when completion rests on a recorded skip instead."""
    __slots__ = ("status", "findings", "source", "import_run", "records", "skipped")

    def __init__(self, status, findings=(), source=None, import_run=None, records=(), skipped=False):
        self.status, self.findings, self.source = status, list(findings), source
        self.import_run, self.records, self.skipped = import_run, list(records), skipped


class _Report:
    def __init__(self):
        self.findings, self.cannot = [], []

    def finding(self, msg):
        self.findings.append(msg)


class _Evaluation:
    __slots__ = ("product_root", "root_fd", "run_id", "source", "plan", "row", "doctor", "events",
                 "import_run", "records", "skipped")


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


def original_rel(import_run, source):
    """`.working/imported/import/<import-run>/originals/<source-path>`, the preserved original an import
    run reads its source from (spec 8.8), or None when the run id or the path is malformed."""
    try:
        return "{}/originals/{}".format(store.evidence_run("import", import_run), store._home_file(source))
    except ValueError:
        return None


def _bind(ev):
    """The approved plan this run froze and the plan row of the source, which must be migrate-disposed (spec
    14.1: import may touch only the migrate-disposed sources the plan binds; the plan validator holds the
    import policy's scope to exactly those sources, and the store root to the product root, so store
    paths read as product paths)."""
    if not apply.is_run_id(ev.run_id):
        raise Unevaluable("run id {!r} does not match the adoption grammar".format(ev.run_id))
    plan_bytes = _read(ev, apply.plan_rel(ev.run_id))
    if plan_bytes is None:
        raise Unevaluable("no approved plan in the run's evidence bundle ({})".format(apply.plan_rel(ev.run_id)))
    try:
        ev.plan = apply.frozen_plan(plan_bytes)
    except apply.AdoptApplyError as exc:
        raise Unevaluable(str(exc))
    if ev.plan["run_id"] != ev.run_id:
        raise Unevaluable("the bundle's plan names run {!r}".format(ev.plan["run_id"]))
    rows = [row for row in ev.plan["sources"] if row["path"] == ev.source]
    if not (len(rows) == 1 and rows[0]["disposition"] == "migrate"):
        raise Unevaluable("{!r} is not a migrate-disposed source the approved plan scopes for import; import "
                          "completion evaluates only those (spec 14.1)".format(ev.source))
    ev.row = rows[0]


# --- the receipt and its outcome-event chain ----------------------------------------------------------

def _check_receipt(ev, rep):
    """The adoption receipt this import runs under (spec 8.8: import refuses without one) and its
    outcome-event chain, where spec 14.1 records each source's result and a skip is recorded. The receipt
    core validates and binds this run and the approved plan by the apply shell's own rule; the chain
    validates through the shipped validator, rooted at the core's digest. An INVALID chain is reported and
    none of its events is read as a skip."""
    rel = apply.receipt_rel(ev.run_id)
    core = _read(ev, rel)
    if core is None:
        ev.events = []
        rep.finding("no adoption receipt in the run's evidence bundle ({}): import runs only under an adoption "
                    "receipt (spec 8.8)".format(rel))
        return
    receipt = _canonical(core, "the adoption receipt core")
    checked = schema.validate_receipt_core(receipt)
    if checked.status == CANNOT_EVALUATE:
        raise Unevaluable("receipt core: " + "; ".join(checked.findings))
    for f in checked.findings:
        rep.finding("receipt core: " + f)
    if checked.status == VALID:
        for refusal in apply.receipt_binding_refusals(ev.run_id, ev.plan, receipt):
            rep.finding("receipt: " + refusal)
    events = []
    home = apply.evidence_home_rel(ev.run_id)
    for number in range(1, MAX_EVENTS + 2):
        name = home + "/" + EVENT_NAME.format(number)
        data = _read(ev, name)
        if data is None:
            break
        if number > MAX_EVENTS:
            raise Unevaluable("the outcome-event chain runs past its bound of {} events; a longer chain is "
                              "never read in part".format(MAX_EVENTS))
        events.append(_canonical(data, "outcome event " + name))
    chain = schema.validate_event_chain(events, _digest(core))
    if chain.status == CANNOT_EVALUATE:
        raise Unevaluable("outcome-event chain: " + "; ".join(chain.findings))
    for f in chain.findings:
        rep.finding("outcome-event chain: " + f)
    ev.events = events if chain.status == VALID else []


# --- the preserved original -------------------------------------------------------------------------

def _check_preserved(ev, rep):
    """Spec 14.1: 'the preserved original's digest (the archived copy under section 14.2 for a source that
    occupied a managed destination, the frozen live file and its apply-archived retirement preimage
    otherwise)'. The run's evidence bundle verifies from disk first, every inventory and every payload it
    lists (the homes-1 re-read of spec 14.1, kept on homes 2), and the preserved copy must be one the
    bundle inventory claims."""
    row = ev.row
    try:
        bundle = apply.verify_bundle(ev.product_root, ev.run_id)
    except apply.AdoptApplyError as exc:
        raise Unevaluable(str(exc))
    if bundle.status == CANNOT_EVALUATE:
        raise Unevaluable("adoption evidence bundle: " + "; ".join(bundle.findings))
    for f in bundle.findings:
        rep.finding("adoption evidence bundle: " + f)
    listed = set()
    inventory = _read(ev, apply.inventory_rel(ev.run_id))
    if inventory is not None:
        doc = _canonical(inventory, "the adoption bundle inventory")
        checked = apply.validate_inventory(doc, ev.run_id)
        if checked.status == CANNOT_EVALUATE:
            raise Unevaluable("the adoption bundle inventory: " + "; ".join(checked.findings))
        listed = {r["path"] for r in doc["file"]} if checked.status == VALID else set()
    preserved = apply.archive_rel(ev.run_id, row["path"])
    if row.get("preservation") != preserved:
        rep.finding("{!r} is preserved at {!r}, not under this run's adoption archive".format(
            row["path"], row.get("preservation")))
    data = _read(ev, preserved)
    if data is None:
        rep.finding("the preserved copy {!r} of {!r} is missing from the adoption archive".format(
            preserved, row["path"]))
    elif _digest(data) != row["digest"]:
        rep.finding("the preserved copy {!r} does not match the plan digest of {!r}".format(preserved, row["path"]))
    elif preserved not in listed:
        rep.finding("the preserved copy {!r} is not claimed by the run's bundle inventory".format(preserved))
    if not row["occupying"]:
        live = _read(ev, row["path"])
        if live is None:
            rep.finding("the frozen live source {!r} is absent from the live tree before its recorded "
                        "retirement".format(row["path"]))
        elif _digest(live) != row["digest"]:
            rep.finding("the frozen live source {!r} no longer matches its plan digest: a drifted source is "
                        "never complete (spec 14.1)".format(row["path"]))


# --- the imported records, their runs and a recorded skip ---------------------------------------------

def _imported_rows(ev):
    """Every (leaf path, type, row) of the imported managed leaves present in the machine store. A leaf
    holds exactly `schema` (the supported schema) and its rows (`record` in an index, `entry` in the
    imported worklog), each a table; anything else is CANNOT-EVALUATE."""
    machine = ev.plan["store"]["machine_rel"]
    leaves = [(name, name + IMPORTED_INDEX_SUFFIX, "record") for name in IMPORTED_TYPES if name != "worklog"]
    leaves.append(("worklog", IMPORTED_WORKLOG_NAME, "entry"))
    out = []
    for tname, leaf, key in leaves:
        rel = machine + "/" + leaf
        data = _read(ev, rel)
        if data is None:
            continue
        doc = _canonical(data, "the imported leaf " + rel)
        rows = doc.get(key)
        if not (set(doc) == {"schema", key} and type(doc["schema"]) is int
                and doc["schema"] == records.SUPPORTED_SCHEMA and isinstance(rows, list)
                and all(isinstance(r, dict) for r in rows)):
            raise Unevaluable("the imported leaf {} does not hold exactly schema = {} and an array of {} "
                              "tables".format(rel, records.SUPPORTED_SCHEMA, key))
        out.extend((rel, tname, r) for r in rows)
    return out


def _referencing_runs(ev):
    """{import run: [(leaf path, type, record)]} of the records whose import.source is exactly the
    source's preserved original under their own import.run (original_rel)."""
    runs = {}
    for rel, tname, record in _imported_rows(ev):
        table = record.get("import")
        run = table.get("run") if isinstance(table, dict) else None
        original = original_rel(run, ev.source) if type(run) is str else None
        if original is not None and table.get("source") == original:
            runs.setdefault(run, []).append((rel, tname, record))
    return runs


def _ids(entries):
    return {r["id"] for _rel, _tname, r in entries if type(r.get("id")) is str}


def correcting_run(runs):
    """Spec 8.8: 'Such a corrected import is a fresh run; its records MUST link `supersedes` or `corrects`
    to the imported records they replace ... and source completion (section 14.1) evaluates the correcting
    run.' Run B corrects run A when a record of B links one of CORRECTING_RELS to a record of A. Returns the
    one run no other run corrects, None when `runs` is empty; none (a correction cycle) or more than one
    (two uncorrected batches for one source, which the writer refuses) raises Unevaluable."""
    if not runs:
        return None
    corrected = set()
    for run, entries in runs.items():
        targets = set()
        for _rel, _tname, record in entries:
            links = record.get("links")
            for link in links if isinstance(links, list) else ():
                if (isinstance(link, dict) and link.get("rel") in CORRECTING_RELS
                        and type(link.get("id")) is str):
                    targets.add(link["id"])
        corrected.update(other for other in runs if other != run and targets & _ids(runs[other]))
    heads = sorted(set(runs) - corrected)
    if len(heads) != 1:
        raise Unevaluable("the import runs {} referencing the source leave {} uncorrected, so which run "
                          "completion evaluates is ambiguous (spec 8.8 admits one completed batch per "
                          "correction chain)".format(sorted(runs), heads or "none"))
    return heads[0]


def _check_import_run(ev, rep, run, entries):
    """One import run's evidence (spec 8.8, 14.1): its bundle inventory, re-read and held to the doctor's own
    row validator, every payload it lists at its recorded size and digest, the source's preserved original
    claimed and at the plan digest, and each referencing record's source_sha256 the original's."""
    import _opf_check
    bundle = store.evidence_run("import", run)
    inv_rel = store.evidence_inventory("import", run)
    data = _read(ev, inv_rel)
    if data is None:
        rep.finding("import run {} has no evidence inventory ({}), so its preserved original is "
                    "unaccounted".format(run, inv_rel))
        return
    doc = _canonical(data, "the import inventory " + inv_rel)
    try:
        claimed = {}
        for row in _opf_check._evidence_rows(bundle, "import", run, doc):
            if row["path"] in claimed:
                raise ValueError("{!r} is claimed more than once".format(row["path"]))
            claimed[row["path"]] = row
    except _opf_check._LegacyIngestInventory as exc:
        rep.finding("{}: {}".format(inv_rel, exc))
        return
    except (TypeError, ValueError, KeyError) as exc:
        raise Unevaluable("the import inventory {} is malformed: {}".format(inv_rel, exc))
    for path in sorted(claimed):
        payload = _read(ev, path)
        if payload is None:
            rep.finding("import run {}: listed file {!r} is missing".format(run, path))
        elif len(payload) != claimed[path]["size"] or _digest(payload) != "sha256:" + claimed[path]["sha256"]:
            rep.finding("import run {}: listed file {!r} does not hold its recorded size and digest".format(
                run, path))
    original = original_rel(run, ev.source)
    row = claimed.get(original)
    if row is None:
        rep.finding("import run {}: the preserved original {!r} is not claimed by its inventory".format(
            run, original))
        return
    if "sha256:" + row["sha256"] != ev.row["digest"]:
        rep.finding("import run {}: the preserved original {!r} does not match the plan digest of {!r}".format(
            run, original, ev.source))
    for rel, _tname, record in entries:
        if record["import"].get("source_sha256") != row["sha256"]:
            rep.finding("{}: record {} records a source_sha256 other than its preserved original {!r}".format(
                rel, record.get("id") if type(record.get("id")) is str else "(no id)", original))


def _check_imports(ev, rep):
    """Spec 14.1: 'at least one imported record referencing that source or a recorded skip under the
    approved policy'. Every import run referencing the source has its evidence re-read (_check_import_run),
    a corrected run's included (spec 8.8 keeps it immutable); completion evaluates the correcting run
    (correcting_run). With no referencing record, a recorded skip completes the source only under the
    plan's skip policy attributed-skip and only when it names its actor; a skip recorded under any other
    policy is a finding whether or not records exist."""
    runs = _referencing_runs(ev)
    head = correcting_run(runs)
    for run in sorted(runs):
        _check_import_run(ev, rep, run, runs[run])
    policy = ev.plan["import_policy"]["skip"]
    skips = [e for e in (ev.events or []) if e.get("kind") == SKIP_EVENT_KIND
             and e.get(SKIP_EVENT_SOURCE) == ev.source]
    if skips and policy != SKIP_POLICY:
        rep.finding("a skip of {!r} is recorded, but the approved skip policy is {!r}: only {!r} lets a "
                    "recorded skip complete a source (spec 14.1)".format(ev.source, policy, SKIP_POLICY))
    if head is not None:
        ev.import_run, ev.records = head, sorted(_ids(runs[head]))
        return
    if not skips:
        rep.finding("no imported record references {!r} (an import.source of "
                    ".working/imported/import/<run>/originals/{}) and no skip of it is recorded: the source is "
                    "not complete (spec 14.1)".format(ev.source, ev.source))
    else:
        # Under another policy the finding above already leaves the source incomplete.
        if all(schema._is_token(e.get(SKIP_EVENT_ACTOR)) for e in skips):
            ev.skipped = True
        else:
            rep.finding("a recorded skip of {!r} names no actor: an attributed skip names who skipped "
                        "it".format(ev.source))


# --- doctor validity on the live tree ---------------------------------------------------------------

def store_doctor(observations=None):
    """A doctor callable: resolve the store and run the whole-store validator over the caller's inert
    git-derived observations (gathering them spawns git, which this module never does)."""
    def run(product_root):
        import _opf_check
        return _opf_check.validate_store(store.resolve_store(Path(product_root)), observations=observations)
    return run


def _check_doctor(ev, rep):
    """Spec 14.1: 'doctor VALID over both the strict store and the imported series, evaluated on the live
    tree'. Missing or non-list doctor evidence is never read as empty. An INVALID doctor reports every
    finding, none set aside; a VALID one must grade each IMPORTED_SERIES_CHECKS entry PASS, or the imported
    series was not evaluated and the leg is CANNOT-EVALUATE naming the checks missing."""
    if ev.doctor is None:
        raise Unevaluable("no doctor run was supplied; doctor validity is never assumed")
    result = ev.doctor(ev.product_root)
    status = getattr(result, "status", None)
    cannot = getattr(result, "cannot_evaluate", None)
    findings = getattr(result, "findings", None)
    if not isinstance(cannot, list) or not isinstance(findings, list):
        raise Unevaluable("doctor evidence is not list-shaped (cannot_evaluate {}, findings {})".format(
            type(cannot).__name__, type(findings).__name__))
    if cannot or status not in (VALID, INVALID):
        raise Unevaluable("doctor: {} {}".format(status, "; ".join(map(str, cannot))))
    if status == INVALID and not findings:
        raise Unevaluable("doctor reports INVALID with no finding")
    for f in findings:
        rep.finding("doctor: {}".format(f))
    if status == INVALID:
        return
    checks = getattr(result, "checks", None)
    if not isinstance(checks, dict):
        raise Unevaluable("doctor evidence carries no per-check verdict map, so the imported series is not "
                          "shown graded")
    missing = [cid for cid in IMPORTED_SERIES_CHECKS if cid not in checks]
    if missing:
        raise Unevaluable("the doctor did not grade the imported series: {} absent from its roster (spec 8.3 "
                          "and 4.2 add them when the imported series activates)".format(", ".join(missing)))
    ungraded = ["{} {}".format(cid, checks[cid]) for cid in IMPORTED_SERIES_CHECKS if checks[cid] != "PASS"]
    if ungraded:
        raise Unevaluable("doctor reports VALID while grading {}".format(", ".join(ungraded)))


_LEGS = ("_check_receipt", "_check_preserved", "_check_imports", "_check_doctor")


def evaluate_source(product_root, run_id, source, *, doctor=None):
    """The import-completion verdict of ONE migrate-disposed `source` of the adoption run `run_id` (spec
    14.1), as a SourceResult. Each leg (_LEGS) reports into one result; a leg that cannot evaluate stops
    only itself. Read-only over the live tree; every unreadable input is CANNOT-EVALUATE."""
    ev = _Evaluation()
    ev.product_root, ev.run_id, ev.source, ev.doctor = str(product_root), run_id, source, doctor
    ev.plan, ev.row, ev.root_fd, ev.events = None, None, None, None
    ev.import_run, ev.records, ev.skipped = None, [], False
    rep = _Report()
    try:
        ev.root_fd = apply._open_product_root(product_root)
        _bind(ev)
        for name in _LEGS:
            try:
                globals()[name](ev, rep)
            except Unevaluable as exc:
                rep.cannot.append(str(exc))
            except Exception as exc:  # noqa: BLE001  any other escape (an injected doctor's included) fails closed
                rep.cannot.append("{} raised {!r}; failing closed".format(name, exc))
    except (Unevaluable, apply.AdoptApplyError) as exc:
        rep.cannot.append(str(exc))
    finally:
        if ev.root_fd is not None:
            store._close_fd_exc_safe(ev.root_fd)
    if rep.cannot:
        return SourceResult(CANNOT_EVALUATE, ["cannot evaluate: " + c for c in rep.cannot] + rep.findings,
                            source=source)
    if rep.findings:
        return SourceResult(INVALID, rep.findings, source=source, import_run=ev.import_run, records=ev.records)
    return SourceResult(VALID, (), source=source, import_run=ev.import_run, records=ev.records,
                        skipped=ev.skipped)


# --- self-test ----------------------------------------------------------------------------------------

_RUN = "adopt-20260917T120000Z-0123456789abcdef"
_IMP = "imp-20260918T090000Z-0123456789abcdef"
_IMP2 = "imp-20260919T090000Z-fedcba9876543210"
_SOURCE = "notes/old.md"          # a non-occupying migrate source, frozen live with its retirement preimage
_OCCUPYING = ".working/TODO.md"   # an occupying migrate source, archived at apply; its view is rendered there
_RETIRED = "legacy/RULES.md"      # a retire source, frozen live with its retirement preimage: never imported
_LIVE = {_SOURCE: b"old notes\n", _OCCUPYING: b"old todo\n", _RETIRED: b"old rules\n"}
_RENDERED = b"rendered todo view\n"
_MACHINE = ".working/toml"
_LEAF = _MACHINE + "/backlog_item" + IMPORTED_INDEX_SUFFIX
_WORKLOG_LEAF = _MACHINE + "/" + IMPORTED_WORKLOG_NAME


def _put(root, rel, data):
    path = os.path.join(root, *rel.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as out:
        out.write(data)


def _bytes(root, rel):
    with open(os.path.join(root, *rel.split("/")), "rb") as fh:
        return fh.read()


def _remove(root, rel):
    os.remove(os.path.join(root, *rel.split("/")))


def _emit(doc):
    return emit_checked(doc).encode("utf-8")


def _sealed(doc, field):
    body = dict(doc)
    body.pop(field, None)
    return dict(body, **dict([(field, _digest(_emit(body)))]))


def _record(n, run, source=_SOURCE, links=(), sha256=None):
    """A VALID imported backlog_item of import run `run` whose import.source is `source`'s original."""
    record = dict(id="imported:BI-{}".format(n), type="backlog_item", status="done", title="Old item {}".format(n),
                  created_at="2025-03-01T10:00:00Z", updated_at="2025-03-01T10:00:00Z",
                  actor=dict(kind="importer", id="import-assistant"))
    record["import"] = dict(source=original_rel(run, source), run=run, imported_at="2026-09-18T09:00:00Z",
                            source_sha256=sha256 or hashlib.sha256(_LIVE[source]).hexdigest())
    if links:
        record["links"] = [dict(rel=rel, id=target) for rel, target in links]
    return record


def _bundle_files(root, homes, skip_inventories):
    """{store-relative path: bytes} of every file under the given homes, bundle-root inventories aside."""
    out = {}
    for home in homes:
        for base, _dirs, files in os.walk(os.path.join(root, *home.split("/"))):
            for name in files:
                rel = os.path.relpath(os.path.join(base, name), root).replace(os.sep, "/")
                if skip_inventories and base == os.path.join(root, *home.split("/")) \
                        and store.is_evidence_inventory_name(name):
                    continue
                out[rel] = _bytes(root, rel)
    return out


def _reinventory(root, drop=()):
    """Re-derive the adoption bundle inventory over the bundle's and the run archive's current bytes, minus
    `drop`, so a vector's edit reaches only the leg it targets (verify_bundle stays VALID)."""
    files = _bundle_files(root, (apply.evidence_home_rel(_RUN), store.ARCHIVE_REL + "/adoption/" + _RUN), True)
    plan = apply._canonical_toml(files[apply.plan_rel(_RUN)], "the fixture plan")
    rows = [apply.inventory_row(rel, data) for rel, data in sorted(files.items()) if rel not in drop]
    _put(root, apply.inventory_rel(_RUN), apply.emit_inventory(_RUN, rows, plan_digest=plan["plan_digest"]))


def _reimport(root, run, drop=(), **top):
    """Re-derive one import run's inventory over its bundle's current bytes, minus `drop`; `top` replaces
    or adds top-level keys (a malformed or legacy inventory)."""
    files = _bundle_files(root, (store.evidence_run("import", run),), True)
    rows = [dict(path=rel, size=len(data), sha256=hashlib.sha256(data).hexdigest())
            for rel, data in sorted(files.items()) if rel not in drop]
    _put(root, store.evidence_inventory("import", run),
         _emit(dict(dict(format=store.EVIDENCE_INVENTORY_FORMAT, file=rows), **top)))


def _event(prior, kind, **extra):
    """One outcome event of this run chained after `prior` (the shipped event shape plus `extra` keys)."""
    event = dict(format=schema.EVENT_FORMAT, schema=schema.SCHEMA_VERSION, kind=kind, run_id=_RUN,
                 revision=prior["revision"], recorded_at="2026-09-20T10:00:00Z",
                 previous_event_digest=prior["event_digest"], **extra)
    event["event_digest"] = apply.event_digest(event)
    return event


def _fixture(root, skip_policy="no-skip", imports=None, events=()):
    """A post-apply, post-import product tree for one adoption run: a non-occupying migrate source frozen live
    with its retirement preimage archived, an occupying migrate source archived with its view rendered in its
    place, a retire source frozen live with its preimage archived, the run's bundle (plan, approval, receipt core, the genesis event and the (kind, keys) `events`
    chained after it) under one inventory, and per `imports` entry (import run, source, records) that
    source's original in the run's bundle under its inventory and the records in the backlog_item imported
    leaf. The default imports one record of _SOURCE in _IMP."""
    import datetime
    if imports is None:
        imports = [(_IMP, _SOURCE, [_record(1, _IMP)])]
    sources = sorted([dict(path=path, digest=_digest(_LIVE[path]), disposition=disposition, occupying=occupying,
                           preservation=apply.archive_rel(_RUN, path))
                      for path, disposition, occupying in ((_SOURCE, "migrate", False), (_OCCUPYING, "migrate", True),
                                                           (_RETIRED, "retire", False))],
                     key=lambda r: r["path"])
    bindings = schema.canonical_plan_bindings()
    init = schema.canonical_op("init-store")
    init["members"] = [dict(path=_MACHINE + "/manifest.toml", digest=_digest(b"manifest\n"))]
    render = schema.canonical_op("render-views")
    render["members"] = [dict(path=_OCCUPYING, digest=_digest(_RENDERED))]
    ops = [dict(op="retire-file", path=_RETIRED, preimage_digest=_digest(_LIVE[_RETIRED])), init, render,
           schema.enforcement_install_op(bindings["enforcement"])]
    plan = _sealed(dict(format=schema.PLAN_FORMAT, schema=schema.SCHEMA_VERSION, product="aiqt",
                        inventory_digest="sha256:" + "2" * 64, run_id=_RUN, revision=bindings["revision"],
                        store=dict(store_root=".", machine_rel=_MACHINE, adoption="first-adoption"),
                        sources=sources, effects=schema.derive_effects(ops, sources, _MACHINE + "/manifest.toml",
                                                                       _RUN),
                        release=bindings["release"], prompt_pack=bindings["prompt_pack"],
                        enforcement=bindings["enforcement"], completion=schema.plan_completion(),
                        import_policy=schema.plan_import_policy(skip_policy, sources), ops=ops), "plan_digest")
    approval = dict(actor="maintainer", approved_at="2026-09-17T12:30:00Z", plan_digest=plan["plan_digest"],
                    inventory_digest=plan["inventory_digest"])
    receipt = schema.canonical_receipt_core()
    receipt.update(run_id=_RUN, plan_digest=plan["plan_digest"], inventory_digest=plan["inventory_digest"],
                   approval=approval, import_runs=[])
    receipt["release"]["manifest_sha256"] = plan["release"]["manifest_sha256"]
    receipt["files"] = [dict(path=_SOURCE, before_digest=_digest(_LIVE[_SOURCE]),
                             after_digest=_digest(_LIVE[_SOURCE]), disposition="migrate")]
    core, genesis = apply.adoption_record(_RUN, plan, receipt, datetime.datetime(
        2026, 9, 17, 13, 0, tzinfo=datetime.timezone.utc))
    chain = [apply._canonical_toml(genesis, "the fixture genesis event")]
    for kind, extra in events:
        chain.append(_event(chain[-1], kind, **extra))
    home = apply.evidence_home_rel(_RUN)
    _put(root, apply.plan_rel(_RUN), _emit(plan))
    _put(root, apply.approval_rel(_RUN), _emit(approval))
    _put(root, apply.receipt_rel(_RUN), core)
    _put(root, home + "/" + EVENT_NAME.format(1), genesis)
    for number, event in enumerate(chain[1:], 2):
        _put(root, home + "/" + EVENT_NAME.format(number), _emit(event))
    for row in sources:
        _put(root, row["preservation"], _LIVE[row["path"]])
    _reinventory(root)
    _put(root, _SOURCE, _LIVE[_SOURCE])
    _put(root, _RETIRED, _LIVE[_RETIRED])
    _put(root, _OCCUPYING, _RENDERED)
    leaf, runs = [], []
    for run, source, recs in imports:
        _put(root, original_rel(run, source), _LIVE[source])
        runs.append(run)
        leaf.extend(recs)
    for run in sorted(set(runs)):
        _reimport(root, run)
    if leaf:
        _put(root, _LEAF, _emit(dict(schema=records.SUPPORTED_SCHEMA, record=leaf)))


# The doctor roster of this release (the strict store's checks) and the four imported-series checks.
def _roster():
    import _opf_check
    return tuple(_opf_check.REQUIRED_CHECKS)


class _Doctor:
    """A doctor stub returning one fixed StoreValidation-shaped verdict; by default VALID with every strict
    and imported-series check graded PASS."""

    def __init__(self, status=VALID, findings=(), cannot=(), checks=None):
        self.status, self.findings, self.cannot_evaluate = status, list(findings), list(cannot)
        self.checks = (dict((cid, "PASS") for cid in _roster() + IMPORTED_SERIES_CHECKS)
                       if checks is None else checks)

    def __call__(self, product_root):
        return self


def _case(mutate=None, source=_SOURCE, **fixture):
    with tempfile.TemporaryDirectory(prefix="opf-import-complete-") as root:
        _fixture(root, **fixture)
        kwargs = dict(doctor=_Doctor())
        if mutate is not None:
            kwargs.update(mutate(root) or {})
        return evaluate_source(root, _RUN, source, **kwargs)


def _says(result, text):
    return any(text in f for f in result.findings)


def _tree(root):
    """Every entry under root: (mode, mtime_ns, bytes or None for a directory), by relative path."""
    out = {}
    for base, dirs, files in os.walk(root):
        for name in dirs + files:
            path = os.path.join(base, name)
            st = os.lstat(path)
            out[os.path.relpath(path, root)] = (st.st_mode, st.st_mtime_ns,
                                                _bytes(root, os.path.relpath(path, root)) if name in files
                                                else None)
    return out


def _registered_skip():
    """The event schema as a later release registers the recorded skip: SKIP_EVENT_KIND in the closed
    vocabulary and its two keys optional (this release registers neither)."""
    from unittest import mock
    return mock.patch.multiple(schema, OUTCOME_EVENTS=schema.OUTCOME_EVENTS + (SKIP_EVENT_KIND,),
                               OUTCOME_EVENT_OPTIONAL=schema.OUTCOME_EVENT_OPTIONAL + (SKIP_EVENT_SOURCE,
                                                                                       SKIP_EVENT_ACTOR))


def _skip(source=_SOURCE, actor="maintainer"):
    extra = dict([(SKIP_EVENT_SOURCE, source)])
    if actor is not None:
        extra[SKIP_EVENT_ACTOR] = actor
    return (SKIP_EVENT_KIND, extra)


def self_test():
    """Synthetic post-apply, post-import fixtures under TemporaryDirectory, judged on the returned status and
    a named finding. No network and no process: the doctor is a stub. Exit 0 all pass, 1 a failed check, 2 a
    harness error."""
    from unittest import mock
    here = sys.modules[__name__]
    results = []

    def check(name, ok):
        results.append((name, bool(ok)))

    def edit(rel, data, reseal=True):
        def mutate(root):
            _put(root, rel, data)
            if reseal:
                _reinventory(root)
        return mutate

    try:
        check("fixture-record-is-valid-imported", records.validate_imported_record(
            _record(1, _IMP), expected_type="backlog_item").status == VALID)
        check("event-name-is-the-genesis-name", EVENT_NAME.format(1) == apply.GENESIS_EVENT_NAME)
        check("imported-types-exclude-legacy-fragment", "legacy_fragment" not in IMPORTED_TYPES
              and "backlog_item" in IMPORTED_TYPES and "worklog" in IMPORTED_TYPES)

        # Green: one record of the source in one import run, every leg holding.
        base = _case()
        check("green-record", base.status == VALID and base.findings == [] and base.import_run == _IMP
              and base.records == ["imported:BI-1"] and not base.skipped and base.source == _SOURCE)
        occupying = _case(source=_OCCUPYING, imports=[(_IMP, _OCCUPYING, [_record(1, _IMP, _OCCUPYING)])])
        check("green-occupying-source", occupying.status == VALID and occupying.records == ["imported:BI-1"])

        def moved_to_worklog(root):
            _remove(root, _LEAF)
            _put(root, _WORKLOG_LEAF, _emit(dict(schema=records.SUPPORTED_SCHEMA, entry=[_record(1, _IMP)])))
        worklog = _case(moved_to_worklog)
        check("green-imported-worklog-leaf-read", worklog.status == VALID and worklog.records == ["imported:BI-1"])

        # Neither a record nor a skip: incomplete.
        none = _case(imports=[])
        check("no-record-no-skip-incomplete", none.status == INVALID
              and _says(none, "no imported record references 'notes/old.md'") and none.import_run is None)
        other = _case(imports=[(_IMP, _OCCUPYING, [_record(1, _IMP, _OCCUPYING)])])
        check("record-of-another-source-not-counted", other.status == INVALID
              and _says(other, "no imported record references"))
        foreign = _record(1, _IMP)
        foreign["import"]["source"] = original_rel(_IMP2, _SOURCE)
        misfiled = _case(imports=[(_IMP, _SOURCE, [foreign])])
        check("record-naming-another-runs-original-not-counted", misfiled.status == INVALID
              and _says(misfiled, "no imported record references"))

        # A recorded skip. This release's event schema refuses the skip kind, so the chain cannot evaluate.
        pinned = _case(imports=[], skip_policy=SKIP_POLICY, events=[_skip()])
        check("skip-unregistered-kind-cannot", pinned.status == CANNOT_EVALUATE
              and _says(pinned, "outside the closed OUTCOME_EVENTS vocabulary"))
        with _registered_skip():
            skipped = _case(imports=[], skip_policy=SKIP_POLICY, events=[_skip()])
            check("skip-attributed-green", skipped.status == VALID and skipped.skipped
                  and skipped.import_run is None and skipped.records == [])
            noskip = _case(imports=[], events=[_skip()])
            check("skip-under-no-skip-incomplete", noskip.status == INVALID
                  and _says(noskip, "the approved skip policy is 'no-skip'") and not noskip.skipped)
            both = _case(events=[_skip()])
            check("skip-under-no-skip-with-records-still-a-finding", both.status == INVALID
                  and _says(both, "the approved skip policy is 'no-skip'"))
            elsewhere = _case(imports=[], skip_policy=SKIP_POLICY, events=[_skip(_OCCUPYING)])
            check("skip-of-another-source-not-counted", elsewhere.status == INVALID
                  and _says(elsewhere, "no imported record references"))
            for label, actor in (("empty", ""), ("absent", None)):
                anonymous = _case(imports=[], skip_policy=SKIP_POLICY, events=[_skip(actor=actor)])
                check("skip-without-actor-" + label + "-incomplete", anonymous.status == INVALID
                      and _says(anonymous, "names no actor") and not anonymous.skipped)
            records_and_skip = _case(skip_policy=SKIP_POLICY, events=[_skip()])
            check("skip-beside-records-records-govern", records_and_skip.status == VALID
                  and not records_and_skip.skipped and records_and_skip.records == ["imported:BI-1"])
            signed = _case(imports=[], skip_policy=SKIP_POLICY, events=[("signed-off", _skip()[1])])
            check("event-of-another-kind-not-a-skip", signed.status == INVALID
                  and _says(signed, "no imported record references") and not signed.skipped)

            def broken_link(root):
                event = _event(dict(revision="0123456789abcdef0123456789abcdef01234567",
                                    event_digest="sha256:" + "7" * 64), SKIP_EVENT_KIND,
                               **_skip()[1])
                _put(root, apply.evidence_home_rel(_RUN) + "/" + EVENT_NAME.format(2), _emit(event))
                _reinventory(root)
            unchained = _case(broken_link, imports=[], skip_policy=SKIP_POLICY)
            check("skip-in-broken-chain-not-read", unchained.status == INVALID
                  and _says(unchained, "outcome-event chain: event[1] previous_event_digest")
                  and _says(unchained, "no imported record references") and not unchained.skipped)

        # The preserved original: each digest mismatch names the path.
        preimage = apply.archive_rel(_RUN, _SOURCE)
        flipped = _case(edit(preimage, b"edited notes\n"))
        check("preimage-mismatch-names-path", flipped.status == INVALID and _says(
            flipped, "the preserved copy {!r} does not match the plan digest of {!r}".format(preimage, _SOURCE)))

        def preimage_gone(root):
            _remove(root, preimage)
            _reinventory(root)
        gone = _case(preimage_gone)
        check("preimage-missing-names-path", gone.status == INVALID
              and _says(gone, "the preserved copy {!r} of {!r} is missing".format(preimage, _SOURCE)))
        unclaimed = _case(lambda root: _reinventory(root, drop=(preimage,)))
        check("preimage-unclaimed-finding", unclaimed.status == INVALID
              and _says(unclaimed, "{!r} is not claimed by the run's bundle inventory".format(preimage)))
        drifted = _case(edit(_SOURCE, b"edited live notes\n", reseal=False))
        check("live-source-drift-names-path", drifted.status == INVALID
              and _says(drifted, "the frozen live source 'notes/old.md' no longer matches its plan digest"))
        absent = _case(lambda root: _remove(root, _SOURCE))
        check("live-source-absent-finding", absent.status == INVALID
              and _says(absent, "the frozen live source 'notes/old.md' is absent"))
        archived = apply.archive_rel(_RUN, _OCCUPYING)
        occupied = _case(edit(archived, b"edited todo\n"), source=_OCCUPYING,
                         imports=[(_IMP, _OCCUPYING, [_record(1, _IMP, _OCCUPYING)])])
        check("occupying-archive-mismatch-names-path", occupied.status == INVALID and _says(
            occupied, "the preserved copy {!r} does not match the plan digest of {!r}".format(archived, _OCCUPYING)))
        rerendered = _case(edit(_OCCUPYING, b"another view\n", reseal=False), source=_OCCUPYING,
                           imports=[(_IMP, _OCCUPYING, [_record(1, _IMP, _OCCUPYING)])])
        check("occupying-live-destination-not-read", rerendered.status == VALID)
        tampered = _case(edit(apply.approval_rel(_RUN), b"actor = \"someone\"\n", reseal=False))
        check("bundle-payload-tamper-finding", tampered.status == INVALID
              and _says(tampered, "adoption evidence bundle:"))

        def phase_only(root):
            _put(root, apply.inventory_rel(_RUN, "retirement"), _bytes(root, apply.inventory_rel(_RUN)))
            _remove(root, apply.inventory_rel(_RUN))
        phased = _case(phase_only)
        check("bundle-phase-inventory-without-base-cannot", phased.status == CANNOT_EVALUATE
              and _says(phased, "adoption evidence bundle:"))
        with tempfile.TemporaryDirectory(prefix="opf-import-complete-") as root:
            _fixture(root)
            ev = _Evaluation()
            ev.product_root, ev.run_id, ev.source, ev.root_fd = root, _RUN, _SOURCE, None
            direct = _Report()
            try:
                ev.root_fd = apply._open_product_root(root)
                _bind(ev)
                ev.row = dict(ev.row, preservation=store.moved_dest(_SOURCE))
                _check_preserved(ev, direct)
            finally:
                if ev.root_fd is not None:
                    store._close_fd_exc_safe(ev.root_fd)
            check("preserved-elsewhere-finding", direct.findings == [
                "'notes/old.md' is preserved at '.working/archive/moved/notes/old.md', not under this run's "
                "adoption archive"])

        # The bundle inventory this leg re-reads after verify_bundle is held to the apply shell's own
        # inventory validator too (verify_bundle is stubbed VALID here, so only that validator grades it).
        def foreign_row(root):
            doc = apply._canonical_toml(_bytes(root, apply.inventory_rel(_RUN)), "the fixture inventory")
            doc["file"].append(apply.inventory_row("../escape.md", b"x"))
            _put(root, apply.inventory_rel(_RUN), _emit(doc))
        with mock.patch.object(apply, "verify_bundle", lambda *a: schema._ok()):
            revalidated = _case(foreign_row)
        check("bundle-inventory-revalidated-cannot", revalidated.status == CANNOT_EVALUATE
              and _says(revalidated, "the adoption bundle inventory: "))

        def symlinked(root):
            _remove(root, _SOURCE)
            os.symlink("../" + preimage, os.path.join(root, *_SOURCE.split("/")))
        check("symlinked-live-source-cannot", _case(symlinked).status == CANNOT_EVALUATE)

        # The receipt and its outcome-event chain.
        def no_receipt(root):
            _remove(root, apply.receipt_rel(_RUN))
            _reinventory(root)
        receiptless = _case(no_receipt)
        check("receipt-absent-incomplete", receiptless.status == INVALID
              and _says(receiptless, "no adoption receipt in the run's evidence bundle"))
        check("receipt-garbage-cannot", _case(edit(apply.receipt_rel(_RUN), b"x = [\n")).status == CANNOT_EVALUATE)

        def foreign_inventory(root):
            receipt = apply._canonical_toml(_bytes(root, apply.receipt_rel(_RUN)), "the fixture receipt")
            receipt["inventory_digest"] = receipt["approval"]["inventory_digest"] = "sha256:" + "4" * 64
            _put(root, apply.receipt_rel(_RUN), _emit(receipt))
            _reinventory(root)
        unbound = _case(foreign_inventory)
        check("receipt-binding-refusal-finding", unbound.status == INVALID
              and _says(unbound, "receipt: the receipt core does not bind the approved plan's inventory_digest"))

        def rereceipt(change):
            def mutate(root):
                receipt = apply._canonical_toml(_bytes(root, apply.receipt_rel(_RUN)), "the fixture receipt")
                change(receipt)
                _put(root, apply.receipt_rel(_RUN), _emit(receipt))
                _reinventory(root)
            return mutate
        extra = _case(rereceipt(lambda receipt: receipt.update(extra="x")))
        check("receipt-core-invalid-finding", extra.status == INVALID and _says(extra, "receipt core: ")
              and not _says(extra, "receipt: "))
        alien = _case(rereceipt(lambda receipt: receipt["files"][0].update(disposition="teleport")))
        check("receipt-core-out-of-vocabulary-cannot", alien.status == CANNOT_EVALUATE
              and _says(alien, "cannot evaluate: receipt core: "))

        def broken_chain(root):
            event = _event(dict(revision="0123456789abcdef0123456789abcdef01234567",
                                event_digest="sha256:" + "7" * 64), "signed-off")
            _put(root, apply.evidence_home_rel(_RUN) + "/" + EVENT_NAME.format(2), _emit(event))
            _reinventory(root)
        chainbreak = _case(broken_chain)
        check("event-chain-break-finding", chainbreak.status == INVALID
              and _says(chainbreak, "outcome-event chain: event[1] previous_event_digest"))
        check("event-garbage-cannot", _case(edit(apply.genesis_event_rel(_RUN), b"x = [\n")).status
              == CANNOT_EVALUATE)
        # The chain bound, each side of it: just inside, exactly at, and just past MAX_EVENTS (patched to 2).
        with mock.patch.object(here, "MAX_EVENTS", 2):
            for label, count, want in (("inside", 1, VALID), ("at", 2, VALID), ("past", 3, CANNOT_EVALUATE)):
                bounded = _case(events=[("signed-off", {})] * (count - 1))
                check("event-bound-" + label, bounded.status == want and (
                    want == VALID or _says(bounded, "runs past its bound of 2 events")))

        # Import runs and the correction chain.
        corrected = [(_IMP, _SOURCE, [_record(1, _IMP)]),
                     (_IMP2, _SOURCE, [_record(2, _IMP2, links=[("supersedes", "imported:BI-1")])])]
        chained = _case(imports=corrected)
        check("correction-evaluates-correcting-run", chained.status == VALID and chained.import_run == _IMP2
              and chained.records == ["imported:BI-2"])
        corrects = _case(imports=[corrected[0], (_IMP2, _SOURCE, [_record(2, _IMP2, links=[(
            "corrects", "imported:BI-1")])])])
        check("correction-by-corrects-link", corrects.status == VALID and corrects.import_run == _IMP2)
        related = _case(imports=[corrected[0], (_IMP2, _SOURCE, [_record(2, _IMP2, links=[(
            "relates", "imported:BI-1")])])])
        check("correction-two-uncorrected-runs-cannot", related.status == CANNOT_EVALUATE
              and _says(related, "is ambiguous"))
        cycle = _case(imports=[(_IMP, _SOURCE, [_record(1, _IMP, links=[("supersedes", "imported:BI-2")])]),
                               corrected[1]])
        check("correction-cycle-cannot", cycle.status == CANNOT_EVALUATE and _says(cycle, "leave none uncorrected"))
        head_original = original_rel(_IMP2, _SOURCE)

        def head_drift(root):
            _put(root, head_original, b"other notes\n")
            _reimport(root, _IMP2)
        drift = _case(head_drift, imports=corrected)
        check("correcting-run-original-mismatch-names-run", drift.status == INVALID and _says(
            drift, "import run {}: the preserved original {!r} does not match the plan digest".format(
                _IMP2, head_original)))
        orphaned = _case(lambda root: _remove(root, store.evidence_inventory("import", _IMP)), imports=corrected)
        check("corrected-run-evidence-still-read", orphaned.status == INVALID
              and _says(orphaned, "import run {} has no evidence inventory".format(_IMP)))
        original = original_rel(_IMP, _SOURCE)
        check("import-inventory-missing-finding", _case(
            lambda root: _remove(root, store.evidence_inventory("import", _IMP))).status == INVALID)
        unlisted = _case(lambda root: _reimport(root, _IMP, drop=(original,)))
        check("import-original-unclaimed-finding", unlisted.status == INVALID
              and _says(unlisted, "the preserved original {!r} is not claimed".format(original)))

        def original_drift(root):
            _put(root, original, b"other notes\n")
            _reimport(root, _IMP)
        mismatch = _case(original_drift)
        check("import-original-mismatch-names-path", mismatch.status == INVALID and _says(
            mismatch, "the preserved original {!r} does not match the plan digest".format(original)))
        payload = _case(lambda root: _put(root, original, b"other notes\n"))
        check("import-payload-tamper-finding", payload.status == INVALID
              and _says(payload, "listed file {!r} does not hold its recorded size and digest".format(original)))
        gone_payload = _case(lambda root: _remove(root, original))
        check("import-payload-missing-finding", gone_payload.status == INVALID
              and _says(gone_payload, "listed file {!r} is missing".format(original)))
        sha = _case(imports=[(_IMP, _SOURCE, [_record(1, _IMP, sha256="0" * 64)])])
        check("record-source-sha256-mismatch-finding", sha.status == INVALID
              and _says(sha, "imported:BI-1 records a source_sha256 other than its preserved original"))
        legacy = _case(lambda root: _reimport(root, _IMP, format="opf.ingest.evidence-inventory/v1"))
        check("import-inventory-legacy-format-finding", legacy.status == INVALID
              and _says(legacy, "legacy-ingest-inventory"))
        check("import-inventory-malformed-cannot", _case(
            lambda root: _reimport(root, _IMP, adoption=dict(run_id=_RUN))).status == CANNOT_EVALUATE)

        def doubled(root):
            row = dict(path=original, size=len(_LIVE[_SOURCE]), sha256=hashlib.sha256(_LIVE[_SOURCE]).hexdigest())
            _put(root, store.evidence_inventory("import", _IMP),
                 _emit(dict(format=store.EVIDENCE_INVENTORY_FORMAT, file=[row, dict(row)])))
        twice = _case(doubled)
        check("import-inventory-path-claimed-twice-cannot", twice.status == CANNOT_EVALUATE
              and _says(twice, "is claimed more than once"))
        check("imported-leaf-without-schema-cannot", _case(
            lambda root: _put(root, _LEAF, _emit(dict(record=[_record(1, _IMP)])))).status == CANNOT_EVALUATE)
        check("imported-leaf-garbage-cannot", _case(
            lambda root: _put(root, _LEAF, b"record = [\n")).status == CANNOT_EVALUATE)

        # Doctor validity on the live tree.
        sick = _case(lambda root: dict(doctor=_Doctor(INVALID, ["C-LINKS: a dangling link"])))
        check("doctor-invalid-incomplete", sick.status == INVALID and _says(sick, "doctor: C-LINKS: a dangling link"))
        strict = dict((cid, "PASS") for cid in _roster())
        pinned_roster = _case(lambda root: dict(doctor=_Doctor(checks=strict)))
        check("doctor-without-imported-series-cannot", pinned_roster.status == CANNOT_EVALUATE
              and _says(pinned_roster, ", ".join(IMPORTED_SERIES_CHECKS) + " absent from its roster"))
        kept = _case(lambda root: dict(doctor=_Doctor(INVALID, ["C-LINKS: a dangling link"], checks=strict)))
        check("doctor-invalid-keeps-its-reason", kept.status == INVALID
              and _says(kept, "doctor: C-LINKS: a dangling link") and not _says(kept, "absent from its roster"))
        graded = dict((cid, "PASS") for cid in _roster() + IMPORTED_SERIES_CHECKS)
        graded["C-IMPORTED-IDS"] = "FINDING"
        notpass = _case(lambda root: dict(doctor=_Doctor(checks=graded)))
        check("doctor-valid-imported-check-not-pass-cannot", notpass.status == CANNOT_EVALUATE
              and _says(notpass, "doctor reports VALID while grading C-IMPORTED-IDS FINDING"))
        mapless = _case(lambda root: dict(doctor=_Doctor(checks=list(IMPORTED_SERIES_CHECKS))))
        check("doctor-no-check-map-cannot", mapless.status == CANNOT_EVALUATE
              and _says(mapless, "carries no per-check verdict map"))
        check("doctor-absent-cannot", _says(_case(lambda root: dict(doctor=None)), "no doctor run was supplied"))
        check("doctor-cannot-evaluate-cannot", _case(
            lambda root: dict(doctor=_Doctor(CANNOT_EVALUATE, cannot=["x"]))).status == CANNOT_EVALUATE)
        check("doctor-valid-with-cannot-list-cannot", _case(
            lambda root: dict(doctor=_Doctor(VALID, cannot=["x"]))).status == CANNOT_EVALUATE)
        check("doctor-invalid-without-finding-cannot", _case(
            lambda root: dict(doctor=_Doctor(INVALID))).status == CANNOT_EVALUATE)
        lopsided = _Doctor()
        lopsided.findings = None
        unshaped = _case(lambda root: dict(doctor=lopsided))
        check("doctor-findings-not-a-list-cannot", unshaped.status == CANNOT_EVALUATE
              and _says(unshaped, "doctor evidence is not list-shaped"))

        def raising(product_root):
            raise RuntimeError("doctor exploded")
        check("doctor-raising-cannot", _says(_case(lambda root: dict(doctor=raising)), "doctor exploded"))

        # Binding: only a migrate source the plan scopes is evaluated, under a readable plan of this run.
        check("source-not-in-plan-cannot", _case(source="legacy/other.md").status == CANNOT_EVALUATE)
        retired = _case(source=_RETIRED)
        check("retire-source-cannot", retired.status == CANNOT_EVALUATE
              and _says(retired, "'legacy/RULES.md' is not a migrate-disposed source"))

        def other_run(root):
            # A self-consistent plan the same fixture freezes for another run, placed in this run's bundle.
            other = "adopt-20261001T000000Z-fedcba9876543210"
            with tempfile.TemporaryDirectory(prefix="opf-import-complete-") as elsewhere:
                with mock.patch.object(here, "_RUN", other):
                    _fixture(elsewhere)
                _put(root, apply.plan_rel(_RUN), _bytes(elsewhere, apply.plan_rel(other)))
        check("plan-naming-another-run-cannot", _says(_case(other_run), "the bundle's plan names run"))
        check("run-id-malformed-cannot", evaluate_source("/", "adopt-nope", _SOURCE).status == CANNOT_EVALUATE)
        check("plan-absent-cannot", _case(lambda root: _remove(root, apply.plan_rel(_RUN))).status
              == CANNOT_EVALUATE)
        # A leg that cannot evaluate stops only itself: the other legs still report.
        def two_faults(root):
            _put(root, preimage, b"edited notes\n")
            _reinventory(root)
            return dict(doctor=None)
        partial = _case(two_faults)
        check("leg-cannot-stops-only-itself", partial.status == CANNOT_EVALUATE
              and _says(partial, "no doctor run was supplied") and _says(partial, "does not match the plan digest"))
        # Each leg runs: a probe in its place is reported.
        for name in _LEGS:
            def probe(ev, rep, _name=name):
                rep.finding("probe " + _name)
            with mock.patch.object(here, name, probe):
                probed = _case()
            check("dispatch-" + name.strip("_") + "-load-bearing", probed.status == INVALID
                  and _says(probed, "probe " + name))

        # Read-only: the tree, bytes, modes and modification times, is unchanged by a green and a red run.
        for label, fixture in (("green", dict()), ("incomplete", dict(imports=[]))):
            with tempfile.TemporaryDirectory(prefix="opf-import-complete-") as root:
                _fixture(root, **fixture)
                before = _tree(root)
                verdict = evaluate_source(root, _RUN, _SOURCE, doctor=_Doctor())
                check("snapshot-" + label + "-unchanged", _tree(root) == before
                      and verdict.status == (VALID if label == "green" else INVALID))
    except Exception as exc:  # noqa: BLE001  fail-closed backstop, never an uncaught exit-1 escape
        print("OPF-IMPORT-COMPLETE SELF-TEST: harness error ({!r}); failing closed to exit 2".format(exc),
              file=sys.stderr)
        return 2
    finally:
        mock.patch.stopall()
    failed = [name for name, ok in results if not ok]
    for name in failed:
        print("FAIL: " + name, file=sys.stderr)
    print("OPF-IMPORT-COMPLETE SELF-TEST: {} checks, {} failed".format(len(results), len(failed)))
    return 1 if failed else 0


def main():
    if sys.argv[1:] in (["--self-test"], ["--selftest"]):
        return self_test()
    print("usage: _opf_import_complete.py --self-test (a library module: evaluate_source())", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
