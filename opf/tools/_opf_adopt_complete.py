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
    store root, release manifest and the approval itself, each receipt file row names a plan source with its
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
    recorded exclusion (the store control area counts as one, spec 14.2); a duplicate-path, non-table,
    alien-kind or non-string-path entry row is ambiguous or malformed accounting input, refused as
    CANNOT-EVALUATE, never a pass; every plan source is an observed file whose digest equals its plan
    digest.
  3 preservation-restore: the run's evidence bundle verifies from disk (_opf_adopt_apply.verify_bundle, the
    homes-1 evidence digest verification: every inventory and every payload it lists); every
    archive-preserved source (retire, migrate and occupying, spec 14.2) sits at its run archive path,
    digest-matched and claimed by the bundle inventory, and the restore exercise reproduces each preserved
    file's bytes in scratch. A non-occupying move source is preserved at its move destination only by the
    post-green relocation (spec 14.2), so before green this check verifies its frozen live bytes against
    the plan digest instead and requires no archive copy of it.
  4 operational-readiness: the store resolves to the planned identity (an absent store is a finding, so a
    de-adopted repository cannot pass as NOT-APPLICABLE); every CI enforcement member is live at its plan
    digest, and per CI row a digest-matched workflow member configures the store assertion in canonical
    form (adoption writes that step itself): a step whose run value is exactly one canonical assertion line
    (`opf doctor --require-store`, optionally with the recipe's `--root .`, or `sh` running a planned recipe
    member of the same row that is the pack's shipped recipe) with no `if:`, no continue-on-error other
    than false and a bash or sh shell (_ci_asserts_store). A step that mentions the assertion in any other
    form is CANNOT-EVALUATE, named, never VALID or INVALID, since this check proves nothing about other
    shell; with no step mentioning it the check is INVALID; undecodable or unparseable CI (a control
    character, an unparsed flow collection) is CANNOT-EVALUATE; every planned view
    destination holds exactly its planned bytes, a formerly occupied destination included (spec 14.1: once
    apply commits, restoring an archived file to the live tree takes a fresh plan, so old occupant bytes
    there are drift the plan does not account for); every consumer repointing holds its planned new bytes;
    and every doctor finding fails the check, none set aside (doctor evidence whose findings or
    cannot_evaluate is missing or not a list is CANNOT-EVALUATE, never read as empty).
  6 retirement-readiness: each retire-disposed source that occupied no managed destination is present live
    with its plan digest; each archived occupying source still equals its plan digest in the archive.

DISCLOSED RESIDUALS: the receipt core schema requires a null after_digest for retire and move rows, which
TOML cannot spell, so check 1 compares only the receipt rows present and cannot require one row per source;
check 2 trusts the planning inventory the caller supplies once it re-seals to the plan's bound digest;
check 4's doctor verdict is only as complete as the caller's observations; the CI assertion test parses the
digest-pinned planned CI members through a block-YAML subset (an anchor, a flow step, a control character or
a multi-line plain scalar is CANNOT-EVALUATE), accepts only the canonical step forms and reads no other
shell, knows the opf program by its name (`opf`, or `python3 -I -B` running a relative `opf.py`) and the
recipe only by byte equality with the pack's shipped recipe, does not model what an earlier `uses:` step, the
runner image or a container puts on the path or into the environment, nor which triggers run the workflow,
and is not a run of CI.

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
import _opf_store as store           # noqa: E402
from _opf_emit import EmitError, emit_checked  # noqa: E402

VALID, INVALID, CANNOT_EVALUATE = store.VALID, store.INVALID, store.CANNOT_EVALUATE
# The roster in the spec's numbered order (the plan's own vocabulary), and the five this slice evaluates.
CHECKS = schema.COMPLETION_CHECKS
AUTHORITY, DISCOVERY, PRESERVATION, OPERATIONAL, WIRING, RETIREMENT = CHECKS
EVALUATED = (AUTHORITY, DISCOVERY, PRESERVATION, OPERATIONAL, RETIREMENT)
PLANNING_INVENTORY_FORMAT = "opf.adoption.planning-inventory/v1"
# The CI floor's store-presence and identity assertion (opf doctor --require-store, spec 14.1 check 4).
CI_STORE_ASSERTION = b"--require-store"
_WIRING_NOTE = ("check 5 (wiring) is not evaluated by this slice: its enforcement probes spawn processes; "
                "the roster is incomplete until it runs (never a pass)")
# A block-mapping entry of the CI workflow subset: a plain key, a colon, and the rest of the line.
_YAML_KEY_RE = re.compile(r"([A-Za-z0-9_][A-Za-z0-9_.-]*) *:(?: +(.*))?$")
# Characters the YAML reader refuses anywhere (CANNOT-EVALUATE): the C0 controls except tab, LF and CR (a
# CR is refused on its own), DEL, the C1 controls, a byte-order mark and the two noncharacters.
_YAML_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f\ufeff\ufffe\uffff]")
# A plain scalar inside a flow collection: no indicator, comma, bracket, colon or hash anywhere.
_FLOW_PLAIN_RE = re.compile(r"[A-Za-z0-9_./+~][A-Za-z0-9_./+~*@-]*(?: +[A-Za-z0-9_./+~*@-]+)*")
# The planning inventory's closed entry-kind vocabulary (_opf_adopt_plan's observation rows).
ENTRY_KINDS = ("absent", "directory", "excluded", "file")


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


def _under(path, prefix):
    return path == prefix or path.startswith(prefix + "/")


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
            member_root = op["store_root" if op["op"] == "init-store" else "target"]
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
    """Check 1's receipt leg: the receipt core binds this run and the approved plan, and records its
    approval and the plan's sources as the plan binds them."""
    plan = ev.plan
    want = dict(run_id=ev.run_id, product=plan["product"], plan_digest=plan["plan_digest"],
                inventory_digest=plan["inventory_digest"], store_root=plan["store"]["store_root"])
    for key in sorted(want):
        if receipt[key] != want[key]:
            rep.finding("receipt {} {!r} does not match the approved plan's {!r}".format(
                key, receipt[key], want[key]))
    if receipt["release"]["manifest_sha256"] != plan["release"]["manifest_sha256"]:
        rep.finding("receipt release manifest_sha256 does not match the approved plan's release")
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

def _check_discovery(ev, rep):
    """Spec 14.1 check 2: 'Discovery accounting: every inventory entry has a disposition or recorded
    exclusion.' Every inventoried file, wherever it lies, and every candidate and empty directory needs a
    plan disposition or a recorded exclusion; an ambiguous (duplicate-path) or malformed entry row is
    CANNOT-EVALUATE, never a pass."""
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
    lists = ("exclusions", "entries", "candidates", "empty_directories")
    if not (isinstance(obs, dict) and all(isinstance(obs.get(k), list) for k in lists)):
        raise Unevaluable("the planning inventory's observation lacks {}".format(", ".join(lists)))
    try:
        excluded = [row["path"] for row in obs["exclusions"]]
    except (TypeError, KeyError) as exc:
        raise Unevaluable("the planning inventory's exclusions are malformed ({!r})".format(exc))
    entries = {}
    for i, row in enumerate(obs["entries"]):
        # Ambiguous or malformed accounting input never grades: it is CANNOT-EVALUATE, never a pass.
        if not (isinstance(row, dict) and isinstance(row.get("path"), str) and row["path"]
                and row.get("kind") in ENTRY_KINDS):
            raise Unevaluable("planning inventory entry [{}] is malformed (a table with a non-empty string "
                              "path and a kind in {} is required)".format(i, "/".join(ENTRY_KINDS)))
        if row["kind"] == "file" and not schema._is_digest(row.get("digest")):
            raise Unevaluable("planning inventory file entry {!r} carries no well-formed digest".format(
                row["path"]))
        if row["path"] in entries:
            raise Unevaluable("planning inventory entry {!r} is duplicated: the accounting is "
                              "ambiguous".format(row["path"]))
        entries[row["path"]] = row
    needing = set(obs["candidates"]) | set(obs["empty_directories"])
    needing |= {p for p, row in entries.items() if row["kind"] == "file"}
    if not all(isinstance(p, str) for p in list(needing) + excluded):
        raise Unevaluable("the planning inventory names a non-string path")
    disposed = {row["path"] for row in _sources(ev)}
    for path in sorted(needing):
        if path in disposed or schema._in_control_area(path) or any(_under(path, e) for e in excluded):
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
        rows = _canonical(inventory, "the bundle inventory").get("file")
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


def _is_workflow(path):
    return path.endswith((".yml", ".yaml"))


class _Flow:
    """A single-line YAML flow collection (`[main]`, `{a: b}`), parsed in full by _Workflow._flow_node but
    kept opaque: never read as jobs, a step or a run value."""
    __slots__ = ("text",)

    def __init__(self, text):
        self.text = text


class _Workflow:
    """A reader for the block-YAML subset CI workflows are written in: block mappings with plain keys,
    block sequences, plain and quoted single-line scalars, literal and folded block scalars, and single-line
    flow collections of plain and quoted scalars, parsed in full and kept opaque. Anything outside it (a
    control character, a tab in the indentation, a CR, an anchor, alias or tag, a quoted or duplicate key, a
    multi-line plain or quoted scalar, a document marker, a flow collection with an empty or trailing entry,
    a flow pair outside a flow mapping) raises Unevaluable: CI this check cannot parse is CANNOT-EVALUATE,
    never a pass and never a silent refusal."""

    def __init__(self, text, label):
        self.label, self.i, self.lines = label, 0, []
        control = _YAML_CONTROL_RE.search(text)
        if control is not None:
            self.i = text.count("\n", 0, control.start())
            self.fail("control character U+{:04X}".format(ord(control.group())))
        for raw in text.split("\n"):
            body = raw.lstrip(" ")
            self.lines.append([len(raw) - len(body), body, raw])
        for n, (_indent, body, raw) in enumerate(self.lines):
            if body.startswith("\t") or "\r" in raw:
                self.i = n
                self.fail("a tab in the indentation or a carriage return")

    def fail(self, why):
        raise Unevaluable("CI workflow {!r} line {}: {}, outside the YAML subset this check parses".format(
            self.label, self.i + 1, why))

    def _next(self):
        while self.i < len(self.lines) and (not self.lines[self.i][1] or self.lines[self.i][1][0] == "#"):
            self.i += 1
        return self.lines[self.i] if self.i < len(self.lines) else None

    def document(self):
        line = self._next()
        value = None if line is None else self._node(0)
        if self._next() is not None:
            self.fail("content after the document's top level")
        return value

    def _node(self, indent):
        line = self._next()
        if line is None or line[0] < indent:
            return None
        if line[1] == "-" or line[1].startswith("- "):
            return self._sequence(line[0])
        if _YAML_KEY_RE.match(line[1]):
            return self._mapping(line[0])
        return self.fail("a block-level scalar or an unsupported construct")

    def _mapping(self, indent):
        out = {}
        while True:
            line = self._next()
            if line is None or line[0] < indent:
                return out
            key = _YAML_KEY_RE.match(line[1]) if line[0] == indent else None
            if key is None:
                self.fail("a mapping line that is not `key: value` at the mapping's indentation")
            if key.group(1) in out:
                self.fail("duplicate key {!r}".format(key.group(1)))
            out[key.group(1)] = self._value(indent, key.group(2) or "")

    def _sequence(self, indent):
        out = []
        while True:
            line = self._next()
            if line is None or line[0] < indent:
                return out
            if line[0] > indent or not (line[1] == "-" or line[1].startswith("- ")):
                self.fail("a block-sequence line that is not `- item` at the sequence's indentation")
            rest = line[1][1:]
            item = rest.lstrip(" ")
            if not item or item[0] == "#":
                self.i += 1
                out.append(self._node(indent + 1))
            elif item.startswith("- "):
                self.fail("a nested inline sequence")
            elif _YAML_KEY_RE.match(item):
                # `- key: value` opens a mapping whose keys align with this first key's column.
                column = indent + 1 + len(rest) - len(item)
                self.lines[self.i] = [column, item, line[2]]
                out.append(self._mapping(column))
            else:
                out.append(self._value(indent, item))

    def _value(self, indent, rest):
        self.i += 1
        plain = _strip_plain_comment(rest)
        if not plain:
            line = self._next()
            if line is not None and line[0] == indent and (line[1] == "-" or line[1].startswith("- ")):
                return self._sequence(indent)
            return self._node(indent + 1)
        if re.fullmatch(r"[|>](?:[+-]?[1-9]?|[1-9][+-])", plain):
            return self._block(indent, plain)
        value = self._scalar(rest, plain)
        line = self._next()
        if line is not None and line[0] > indent:
            self.fail("a multi-line plain scalar or unexpected indentation")
        return value

    def _scalar(self, rest, plain):
        head = rest[0]
        if head in "'\"":
            text, j = [], 1
            while j < len(rest):
                if head == "'" and rest[j:j + 2] == "''":
                    text.append("'")
                    j += 2
                elif rest[j] == head:
                    break
                elif head == '"' and rest[j] == "\\":
                    escape = {"\\": "\\", '"': '"', "/": "/", "n": "\n", "t": "\t"}.get(rest[j + 1:j + 2])
                    if escape is None:
                        self.fail("a double-quoted escape outside the subset")
                    text.append(escape)
                    j += 2
                else:
                    text.append(rest[j])
                    j += 1
            if j >= len(rest) or _strip_plain_comment(rest[j + 1:]):
                self.fail("an unterminated quoted scalar or content after its closing quote")
            return "".join(text)
        if head in "[{":
            if self._flow_node(plain, 0, 0) != len(plain):
                self.fail("content after a flow collection")
            return _Flow(plain)
        if (head in "&*!%@`|>,]}" or plain[:2] in ("- ", "? ", ": ") or plain in ("-", "?", ":")
                or ": " in plain or plain.endswith(":")):
            self.fail("an anchor, alias, tag, reserved indicator or nested mapping in a plain scalar")
        return plain

    def _flow_node(self, text, j, depth):
        """The index just past the flow node at text[j] (after spaces): a flow sequence or mapping, a
        single- or double-quoted scalar, or a plain scalar of _FLOW_PLAIN_RE. Every entry is checked, so an
        empty entry (`[a,,b]`), a trailing comma, a missing separator or a bracket left open fails."""
        while text[j:j + 1] == " ":
            j += 1
        head = text[j:j + 1]
        if depth > 16:
            self.fail("a flow collection nested too deeply")
        if head in ("[", "{"):
            closer, j, first = "]" if head == "[" else "}", j + 1, True
            while True:
                while text[j:j + 1] == " ":
                    j += 1
                if text[j:j + 1] == closer and first:
                    return j + 1
                if not first:
                    if text[j:j + 1] == closer:
                        return j + 1
                    if text[j:j + 1] != ",":
                        self.fail("a flow collection entry not followed by `,` or its closing bracket")
                    j += 1
                    while text[j:j + 1] == " ":
                        j += 1
                first = False
                if head == "{":
                    key = _FLOW_PLAIN_RE.match(text, j)
                    if key is None or text[key.end():key.end() + 2] != ": ":
                        self.fail("a flow mapping entry that is not `key: value`")
                    j = key.end() + 2
                j = self._flow_node(text, j, depth + 1)
        if head == "'":
            j += 1
            while True:
                end = text.find("'", j)
                if end < 0:
                    self.fail("an unterminated quoted scalar in a flow collection")
                if text[end + 1:end + 2] != "'":
                    return end + 1
                j = end + 2
        if head == '"':
            j += 1
            while j < len(text) and text[j] != '"':
                if text[j] == "\\" and text[j + 1:j + 2] not in ("\\", '"', "/", "n", "t"):
                    self.fail("a double-quoted escape outside the subset")
                j += 2 if text[j] == "\\" else 1
            if j >= len(text):
                self.fail("an unterminated quoted scalar in a flow collection")
            return j + 1
        plain = _FLOW_PLAIN_RE.match(text, j)
        if plain is None:
            self.fail("a flow collection entry outside the subset (empty, an indicator or a flow pair)")
        return plain.end()

    def _block(self, indent, indicator):
        raw = []
        while self.i < len(self.lines) and (not self.lines[self.i][1] or self.lines[self.i][0] > indent):
            raw.append(self.lines[self.i])
            self.i += 1
        content = [line for line in raw if line[1]]
        if not content:
            return ""
        digits = [ch for ch in indicator if ch.isdigit()]
        width = indent + int(digits[0]) if digits else content[0][0]
        if any(line[0] < width for line in content):
            self.fail("a block-scalar line indented less than its content")
        lines = [line[2][width:] for line in raw]
        if indicator[0] == "|":
            return "\n".join(lines)
        if any(line[0] > width for line in content):
            self.fail("a more-indented line in a folded block scalar")
        paragraphs, current = [], []
        for line in lines:
            if line.strip():
                current.append(line)
            elif current:
                paragraphs.append(" ".join(current))
                current = []
        return "\n".join(paragraphs + ([" ".join(current)] if current else []))


def _strip_plain_comment(rest):
    """`rest` with a trailing YAML comment (a `#` at its start or after whitespace) removed, stripped."""
    for i, ch in enumerate(rest):
        if ch == "#" and (i == 0 or rest[i - 1] in " \t"):
            return rest[:i].strip()
    return rest.strip()


# The canonical CI assertion (spec 14.1 check 4). Adoption writes the CI step itself, so a step counts only
# in one exact form, and this check proves nothing about what any other shell text does: the opf program
# (`opf`, or `python3 -I -B` running a relative `opf.py`, the recipe's own launch) with exactly the
# arguments `doctor --require-store`, optionally followed by the recipe's `--root .`; or `sh` running a
# planned recipe member of the same CI row (optionally `./`-prefixed) with no argument or the root `.`, the
# invocation the recipe's usage line names, where the recipe's bytes are the pack's shipped recipe.
_CANONICAL_ROOT = "."
_OPF_PY_RE = re.compile(r"(?:[A-Za-z0-9_-]+/)*opf\.py")
_SHIPPED_RECIPE = Path(__file__).resolve().parent.parent / "enforcement" / "ci" / "opf-ci.sh"
# The only keys a canonical step may carry: an `if:`, an `env:`, a `working-directory:` or a `uses:` can
# skip the step, change the program it resolves or move the root `.` it asserts.
_CANONICAL_STEP_KEYS = frozenset(("name", "id", "run", "shell", "continue-on-error", "timeout-minutes"))


def _canonical_assertion(run, planned):
    """The canonical form `run` is exactly: ("doctor", None), ("recipe", path) for a path in `planned`,
    else None. `run` is one line (one trailing newline allowed) of single-space-separated words."""
    words = (run[:-1] if run.endswith("\n") else run).split(" ")
    flag = CI_STORE_ASSERTION.decode("ascii")
    if words[:1] == ["opf"]:
        args = words[1:]
    elif words[:3] == ["python3", "-I", "-B"] and len(words) > 3 and _OPF_PY_RE.fullmatch(words[3]):
        args = words[4:]
    elif words[:1] == ["sh"] and len(words) in (2, 3) and words[2:] in ([], [_CANONICAL_ROOT]):
        path = words[1][2:] if words[1].startswith("./") else words[1]
        return ("recipe", path) if path in planned else None
    else:
        return None
    return ("doctor", None) if args in (["doctor", flag], ["doctor", flag, "--root", _CANONICAL_ROOT]) else None


def _mentions_assertion(run, planned):
    """Whether a run value mentions the store assertion: CI_STORE_ASSERTION as a whole option word
    (`--require-store-fake` is another option), or the file name of a planned recipe or the shipped one."""
    flag = re.escape(CI_STORE_ASSERTION.decode("ascii"))
    if re.search(r"(?<![A-Za-z0-9_-])" + flag + r"(?![A-Za-z0-9_-])", run):
        return True
    return any(name in run for name in {path.rsplit("/", 1)[-1] for path in planned} | {_SHIPPED_RECIPE.name})


def _shipped_recipe():
    """The pack's shipped CI recipe bytes (opf/enforcement/ci/opf-ci.sh beside these tools)."""
    try:
        return _SHIPPED_RECIPE.read_bytes()
    except OSError as exc:
        raise Unevaluable("cannot read the pack's shipped CI recipe {} ({})".format(_SHIPPED_RECIPE, exc))


def _gated(table):
    """Whether a job or step can be skipped or its failure ignored (an `if:`, or continue-on-error not false)."""
    return "if" in table or table.get("continue-on-error", "false") != "false"


def _run_defaults(table, where):
    """The `defaults: run:` mapping of a workflow or job ({} when absent); a malformed one is Unevaluable."""
    defaults = table.get("defaults", {})
    run = defaults.get("run", {}) if isinstance(defaults, dict) else None
    if not isinstance(run, dict):
        raise Unevaluable("{}: defaults is not a mapping with a run mapping".format(where))
    return run


def _shell_of(*tables):
    """The step shell: the first `shell:` among the step and the job and workflow `defaults: run:`."""
    for table in tables:
        if "shell" in table:
            return table["shell"]
    return "bash"


def _ci_asserts_store(path, data, recipes=None, planned=None):
    """Whether one CI workflow member configures the store assertion (spec 14.1 check 4: CI asserts store
    presence and identity so absence cannot pass as NOT-APPLICABLE), as (asserted, refused). `recipes` maps
    each live, digest-matched planned recipe path to its bytes; `planned` lists every planned recipe path of
    the row (default: the keys of `recipes`). A step asserts only in canonical form: its run value is
    exactly one canonical assertion line (_canonical_assertion), a recipe one naming a live recipe whose
    bytes are the pack's shipped recipe; the step carries only _CANONICAL_STEP_KEYS, no `if:` and no
    continue-on-error other than false; its job carries no `if:`, no such continue-on-error and no `env:`,
    and neither the job nor the workflow sets `env:` or a default working directory; its shell (the step's,
    else the job's and then the workflow's default) is bash or sh; and no earlier step of its job runs
    shell (it could export variables or PATH entries into the step). `refused` names every other step whose
    run value mentions the assertion (_mentions_assertion): the caller reports those CANNOT-EVALUATE, never
    VALID and never INVALID, because this check proves nothing about what other shell text does. A step
    running a planned recipe that is absent or drifted asserts nothing and is not refused (the member
    finding names it). Undecodable or unparseable CI raises Unevaluable: CANNOT-EVALUATE, never a pass."""
    recipes = recipes or {}
    planned = list(recipes) if planned is None else list(planned)
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise Unevaluable("CI workflow {!r} is not UTF-8 text".format(path))
    doc = _Workflow(text, path).document()
    if not isinstance(doc, dict):
        return False, []
    jobs = doc.get("jobs", {})
    if not isinstance(jobs, dict):
        raise Unevaluable("CI workflow {!r} jobs is not a block mapping".format(path))
    asserted, refused = False, []
    for job_id, job in sorted(jobs.items()):
        if not isinstance(job, dict) or not isinstance(job.get("steps", []), list):
            raise Unevaluable("CI workflow {!r} job {!r} is not a mapping with a steps list".format(path, job_id))
        shelled = False
        for n, step in enumerate(job.get("steps", [])):
            if not isinstance(step, dict):
                raise Unevaluable("CI workflow {!r} job {!r} step {} is not a mapping".format(path, job_id, n))
            run = step.get("run")
            if run is None:
                continue
            if not isinstance(run, str):
                raise Unevaluable("CI workflow {!r} job {!r} step {} run is not a string".format(path, job_id, n))
            where = "CI workflow {!r} job {!r} step {}".format(path, job_id, n)
            defaults = (_run_defaults(job, where), _run_defaults(doc, where))
            earlier, shelled = shelled, True
            form = _canonical_assertion(run, planned)
            why = None
            if form is None:
                why = "its run value is not exactly one canonical assertion line"
            elif not set(step) <= _CANONICAL_STEP_KEYS or _gated(step):
                why = "the step carries a key outside the canonical step ({})".format(
                    ", ".join(sorted(set(step) - _CANONICAL_STEP_KEYS)) or "continue-on-error")
            elif _gated(job) or "env" in job or "env" in doc:
                why = "its job is conditional or continue-on-error, or the job or workflow sets env"
            elif any("working-directory" in table for table in defaults):
                why = "a default working directory moves the root it asserts"
            elif _shell_of(step, *defaults) not in ("bash", "sh"):
                why = "its shell is not bash or sh"
            elif earlier:
                why = "an earlier step of its job runs shell"
            elif form[0] == "recipe" and form[1] not in recipes:
                continue
            elif form[0] == "recipe" and recipes[form[1]] != _shipped_recipe():
                why = "the planned recipe {!r} is not the pack's shipped recipe {}".format(
                    form[1], _SHIPPED_RECIPE.name)
            if why is None:
                asserted = True
            elif _mentions_assertion(run, planned):
                refused.append("{} mentions the store assertion but is not canonical: {}".format(where, why))
    return asserted, refused


def _check_operational(ev, rep):
    """Spec 14.1 check 4: 'Operational readiness: the store resolves to the planned identity, and CI
    asserts store presence and identity so absence cannot pass as NOT-APPLICABLE. Doctor validity and
    declared-view byte drift are evaluated on the live tree, where apply has already rendered every
    declared view, a formerly occupied destination included, and any drift the plan does not account for
    fails the check. Consumer repointings match the plan.' The plan accounts for a view destination by
    binding its rendered member digest, nothing else: old occupant bytes there are unaccounted drift (spec
    14.1: once apply commits, restoring an archived file takes a fresh plan), and no doctor finding is ever
    set aside. CI asserts only through a canonical step (_ci_asserts_store), never a comment, a step name or
    an echo; a non-canonical mention of the assertion and unparseable CI are CANNOT-EVALUATE."""
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
        live = {}
        for member in row["members"]:
            data = _read(ev, member["path"])
            if data is None:
                rep.finding("CI member {!r} is absent: nothing asserts store presence".format(member["path"]))
            elif _digest(data) != member["digest"]:
                rep.finding("CI member {!r} does not match its planned bytes".format(member["path"]))
            else:
                live[member["path"]] = data
        recipes = {path: data for path, data in live.items() if not _is_workflow(path)}
        planned = [member["path"] for member in row["members"] if not _is_workflow(member["path"])]
        # Every live workflow is parsed (no short-circuit), so unparseable CI is CANNOT-EVALUATE, never masked.
        verdicts = [_ci_asserts_store(path, data, recipes, planned)
                    for path, data in sorted(live.items()) if _is_workflow(path)]
        refused = [why for _asserted, whys in verdicts for why in whys]
        if any(asserted for asserted, _whys in verdicts):
            pass
        elif refused:
            rep.cannot.append("no CI step asserts the store in canonical form, and this check proves nothing "
                              "about other shell: " + "; ".join(refused))
        else:
            rep.finding("no live, digest-matched CI workflow configures a step whose run command invokes "
                        "doctor {}: a comment, a step name, an echo or a conditional step configures "
                        "nothing, so CI does not assert store presence and "
                        "identity".format(CI_STORE_ASSERTION.decode("ascii")))
    occupants = {s["path"]: s["digest"] for s in _sources(ev) if s["occupying"]}
    for op in plan["ops"]:
        if op["op"] == "render-views":
            for member in op["members"]:
                dest = schema._compose(op["store_root"], member["path"])
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
_CI_BYTES = (b"name: OPF\non:\n  push:\n    branches: [main]\njobs:\n  opf:\n    runs-on: ubuntu-latest\n"
             b"    steps:\n      - uses: actions/checkout@v4\n"
             b"      - name: OPF CI floor\n        run: python3 -I -B opf/tools/opf.py doctor --require-store\n")
_CI_PATH = ".github/workflows/opf.yml"
# A recipe-delegating workflow shaped like the shipped pack (opf/enforcement/ci): the step runs the planned
# recipe in the invocation its usage line names, and the planned recipe is the pack's shipped recipe.
_CI_RECIPE_PATH = "opf/enforcement/ci/opf-ci.sh"
_CI_DELEGATING = (b"jobs:\n  opf:\n    steps:\n      - name: OPF CI floor (doctor --require-store)\n"
                  b"        run: sh opf/enforcement/ci/opf-ci.sh .\n")
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
    under a recorded exclusion, the store scaffolded, every enforcement member planted, and the run's
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
    for row in bindings["enforcement"]:
        if row["platform"] == "ci":
            row["members"] = [dict(path=".github/workflows/opf.yml", digest=_digest(_CI_BYTES))]
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
    entries.append(dict(path=_CONSUMER, kind="file", size=len(_CONSUMER_OLD), digest=_digest(_CONSUMER_OLD)))
    entries.sort(key=lambda row: row["path"])
    inventory = _sealed(dict(format=PLANNING_INVENTORY_FORMAT, decisions=[], observation=dict(
        exclusions=[dict(path=".working/toml", reason="machine-store"),
                    dict(path="consumer", reason="adopter-owned consumers, repointed by the plan")],
        entries=entries, candidates=sorted(_LIVE), empty_directories=[])), "inventory_digest")
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
    _put(root, ".github/workflows/opf.yml", _CI_BYTES)
    for rel, data in _PACK.items():
        _put(root, rel, data)
    return _emit(inventory)


def _case(mutate=None):
    with tempfile.TemporaryDirectory(prefix="opf-adopt-complete-") as root:
        kwargs = dict(planning_inventory=_fixture(root), doctor=_Doctor())
        if mutate is not None:
            kwargs.update(mutate(root, kwargs["planning_inventory"]) or {})
        return evaluate(root, _RUN, **kwargs)


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


def _flip_2(root, inv):
    return dict(planning_inventory=_reseal_inventory(inv, candidates=sorted(_LIVE) + ["stray.md"]))


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
        base = _case()
        check("baseline-green-1-4-6", _only(base, {}))
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
        check("flip-2-names-unaccounted", _says(_case(_flip_2), DISCOVERY, "'stray.md' has neither"))
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
              and _says(rebound, AUTHORITY, "does not match the approved plan's"))
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
                  and _says(got, AUTHORITY, "receipt {} ".format(key)))
        released = _case(lambda r, i: _rereceipt(r, lambda d: d["release"].update(
            manifest_sha256=other)))
        check("check-1-receipt-release-bound", _only(released, _red(AUTHORITY))
              and _says(released, AUTHORITY, "receipt release manifest_sha256"))
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
        excluded = _case(lambda r, i: dict(planning_inventory=_reseal_inventory(
            i, candidates=sorted(_LIVE) + [".working/toml/x.toml", "stray.md"])))
        check("check-2-excluded-entry-accounted", _says(excluded, DISCOVERY, "'stray.md' has neither")
              and not _says(excluded, DISCOVERY, "x.toml' has neither"))
        rekeyed = _case(lambda r, i: dict(planning_inventory=_reseal_inventory(
            i, exclusions=[dict(path=".working/toml", reason="machine")])))
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
            i, entries=[row for row in _entries(i) if row["path"] != "legacy/RULES.md"])))
        check("check-2-source-not-inventoried-red",
              _says(uninv, DISCOVERY, "'legacy/RULES.md' is not an inventoried file"))
        stray_entry = dict(path="legacy/unaccounted.md", kind="file", size=2, digest="sha256:" + "b" * 64)
        anywhere = _case(lambda r, i: dict(planning_inventory=_reseal_inventory(
            i, entries=_entries(i) + [stray_entry])))
        check("check-2-inventoried-file-outside-working-red", _only(anywhere, _red(DISCOVERY))
              and _says(anywhere, DISCOVERY, "'legacy/unaccounted.md' has neither"))
        dup_entry = dict(path="legacy/RULES.md", kind="file", size=1, digest="sha256:" + "a" * 64)
        check("closed-duplicate-entry", _only(_case(lambda r, i: dict(planning_inventory=_reseal_inventory(
            i, entries=_entries(i) + [dup_entry]))), _red(DISCOVERY, CANNOT_EVALUATE)))
        check("closed-malformed-entry", _only(_case(lambda r, i: dict(planning_inventory=_reseal_inventory(
            i, entries=_entries(i) + [dict(path=123, kind="nonsense")]))), _red(DISCOVERY, CANNOT_EVALUATE)))
        check("closed-undigested-file-entry", _only(_case(lambda r, i: dict(
            planning_inventory=_reseal_inventory(i, entries=_entries(i) + [dict(
                path="legacy/undigested.md", kind="file")]))), _red(DISCOVERY, CANNOT_EVALUATE)))
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
        with mock.patch.object(here, "CI_STORE_ASSERTION", b"--not-in-the-ci-member"):
            check("check-4-ci-assertion-required", _only(_case(), _red(OPERATIONAL)))
        commented = (b"# opf doctor --require-store\njobs:\n  t:\n    steps:\n"
                     b"      - name: opf doctor --require-store\n        run: sh ci.sh .\n")
        with mock.patch.object(here, "_CI_BYTES", commented):
            lipsvc = _case()
        check("check-4-ci-comment-not-a-step", _only(lipsvc, _red(OPERATIONAL))
              and _says(lipsvc, OPERATIONAL, "run command invokes"))
        # The round-2 and round-3 counterexamples end to end: the assertion token only in a top-level block
        # scalar or a suffixed flag configures nothing (INVALID); every step that mentions the assertion in a
        # non-canonical form is CANNOT-EVALUATE, never VALID and never INVALID (unreachable after `exit 0` or
        # `set -n`, an `|| exit 256` or `|| exit "$ZERO"` handler, an interpreter option, an and-list, an
        # echo, a quoted hash); malformed YAML (an empty flow entry, a NUL byte) and undecodable or
        # unparseable CI are CANNOT-EVALUATE.
        def _run_step(script):
            return _CI_BYTES.replace(b"        run: python3 -I -B opf/tools/opf.py doctor --require-store\n",
                                     b"        run: |\n" + b"".join(b"          " + line + b"\n"
                                                             for line in script.split(b"\n")))

        for label, ci_bytes, want in (
                ("name-block-scalar", b"name: |\n  --require-store\non: push\njobs:\n  test:\n"
                                      b"    runs-on: ubuntu-latest\n    steps:\n      - run: echo ok\n", INVALID),
                ("suffixed-flag", b"jobs:\n  t:\n    steps:\n      - run: opf doctor --require-store-fake\n",
                 INVALID),
                ("echo", b"jobs:\n  t:\n    steps:\n      - run: echo --require-store\n", CANNOT_EVALUATE),
                ("undecodable", b"\xff\xfe--require-store\n", CANNOT_EVALUATE),
                ("unparseable", b"jobs:\n  t:\n    steps:\n      - run: opf doctor --require-store 'x\n",
                 CANNOT_EVALUATE),
                ("quoted-hash-block", _run_step(b"printf ' # '; opf doctor --require-store"), CANNOT_EVALUATE),
                ("literal-block-canonical", _run_step(b"opf doctor --require-store"), VALID),
                ("unreachable-after-exit-0", _run_step(b"exit 0\nopf doctor --require-store"), CANNOT_EVALUATE),
                ("unreachable-after-set-n", _run_step(b"set -n\nopf doctor --require-store"), CANNOT_EVALUATE),
                ("or-exit-256", _run_step(b"opf doctor --require-store || exit 256"), CANNOT_EVALUATE),
                ("or-exit-expansion", _run_step(b"ZERO=0\nopf doctor --require-store || exit \"$ZERO\""),
                 CANNOT_EVALUATE),
                ("interpreter-option", _run_step(b"python3 --version opf.py doctor --require-store"),
                 CANNOT_EVALUATE),
                ("and-list", _run_step(b"opf doctor --require-store && true"), CANNOT_EVALUATE),
                ("flow-empty-entry", b"env: [a,,b]\n" + _CI_BYTES, CANNOT_EVALUATE),
                ("flow-empty-entry-on", _CI_BYTES.replace(b"[main]", b"[main,,b]"), CANNOT_EVALUATE),
                ("nul-byte", _CI_BYTES.replace(b"name: OPF", b"name: O\x00PF"), CANNOT_EVALUATE)):
            with mock.patch.object(here, "_CI_BYTES", ci_bytes):
                got = _case()
            check("check-4-ci-" + label, _only(got, {} if want == VALID else _red(OPERATIONAL, want))
                  and (want != CANNOT_EVALUATE or _says(got, OPERATIONAL, "cannot evaluate")))
        ciabsent = _case(lambda r, i: _remove(r, ".github/workflows/opf.yml"))
        check("check-4-ci-member-absent", _only(ciabsent, _red(OPERATIONAL))
              and _says(ciabsent, OPERATIONAL, "is absent: nothing asserts store presence"))
        cidrift = _case(lambda r, i: _put(r, ".github/workflows/opf.yml", _CI_BYTES + b"# extra\n"))
        check("check-4-ci-member-drifted-red", _only(cidrift, _red(OPERATIONAL))
              and _says(cidrift, OPERATIONAL, "does not match its planned bytes"))
        noview = _case(lambda r, i: _remove(r, ".working/TODO.md"))
        check("check-4-view-missing-red", _only(noview, _red(OPERATIONAL))
              and _says(noview, OPERATIONAL, "is missing"))
        repointed = _case(lambda r, i: _put(r, _CONSUMER, b"stale consumer\n"))
        check("check-4-repoint-drifted-red", _only(repointed, _red(OPERATIONAL))
              and _says(repointed, OPERATIONAL, "planned repointing"))
        # The CI canonical-step rule, unit level: VALID only for a canonical step, CANNOT-EVALUATE for a step
        # that mentions the assertion in any other form and for unparseable CI, INVALID when no step
        # mentions it.
        steps = b"jobs:\n  t:\n    runs-on: x\n    steps:\n"
        recipe = _shipped_recipe()

        def ci(data, recipes=None, planned=None):
            try:
                asserted, refused = _ci_asserts_store(_CI_PATH, data, recipes, planned)
            except Unevaluable:
                return CANNOT_EVALUATE
            return VALID if asserted else CANNOT_EVALUATE if refused else INVALID

        def block(script):
            return b"      - run: |\n" + b"".join(b"          " + line + b"\n" for line in script.split(b"\n"))

        delegating = b"      - run: sh opf/enforcement/ci/opf-ci.sh .\n"
        planned = {_CI_RECIPE_PATH: recipe}
        for label, data, recipes, want in (
                ("run-line", steps + b"      - run: opf doctor --require-store\n", None, VALID),
                ("python-opf-py", steps + b"      - run: python3 -I -B opf/tools/opf.py doctor --require-store\n",
                 None, VALID),
                ("double-quoted", steps + b'      - run: "opf doctor --require-store"\n', None, VALID),
                ("literal-block", steps + block(b"opf doctor --require-store"), None, VALID),
                ("folded-scalar", steps + b"      - run: >\n          opf doctor\n          --require-store\n",
                 None, VALID),
                ("root-dot", steps + b"      - run: opf doctor --require-store --root .\n", None, VALID),
                ("sh-shell", steps + b"      - shell: sh\n        run: opf doctor --require-store\n", None, VALID),
                ("bash-shell", steps + b"      - shell: bash\n        run: opf doctor --require-store\n", None,
                 VALID),
                ("continue-on-error-false",
                 steps + b"      - continue-on-error: false\n        run: opf doctor --require-store\n", None, VALID),
                ("canonical-keys", steps + b"      - name: n\n        id: i\n        timeout-minutes: 5\n"
                                           b"        run: opf doctor --require-store\n", None, VALID),
                ("after-uses-step", steps + b"      - uses: actions/checkout@v4\n"
                                            b"      - run: opf doctor --require-store\n", None, VALID),
                ("sequence-at-key-indent", steps + b"    - run: opf doctor --require-store\n", None, VALID),
                ("flow-collections-parsed", b"on: {push: {branches: [main, 'release/**', \"a\\\"b\"]}, x: []}\n"
                                            + steps + b"      - run: opf doctor --require-store\n", None, VALID),
                ("canonical-beside-refused", steps + b"      - run: opf doctor --require-store\n"
                                             b"      - run: opf doctor --require-store || true\n", None, VALID),
                ("recipe-delegation", steps + delegating, planned, VALID),
                ("recipe-dot-slash", steps + delegating.replace(b"sh opf/", b"sh ./opf/"), planned, VALID),
                ("recipe-no-root", steps + delegating.replace(b".sh .\n", b".sh\n"), planned, VALID),
                ("comment", steps + b"      - run: echo  # opf doctor --require-store\n", None, INVALID),
                ("step-name", steps + b"      - name: opf doctor --require-store\n        run: y\n", None, INVALID),
                ("env-value", steps + b"      - env:\n          X: opf doctor --require-store\n        run: y\n",
                 None, INVALID),
                ("suffixed-flag", steps + b"      - run: opf doctor --require-store-fake\n", None, INVALID),
                ("plain-scalar-cut-at-hash", steps + b"      - run: printf ' # '; opf doctor --require-store\n",
                 None, INVALID),
                ("sequence-value", b"on:\n  push:\n    branches:\n      - opf doctor --require-store\n" + steps
                 + b"      - run: echo\n", None, INVALID),
                ("no-run-step", steps + b"      - uses: opf/doctor-require-store@v1\n", None, INVALID),
                ("block-two-lines", steps + block(b"echo start\nopf doctor --require-store"), None, CANNOT_EVALUATE),
                ("errexit-not-last", steps + block(b"opf doctor --require-store\necho done"), None, CANNOT_EVALUATE),
                ("set-e-off-last", steps + block(b"set +e\nopf doctor --require-store"), None, CANNOT_EVALUATE),
                ("exit-0-first", steps + block(b"exit 0\nopf doctor --require-store"), None, CANNOT_EVALUATE),
                ("set-n-first", steps + block(b"set -n\nopf doctor --require-store"), None, CANNOT_EVALUATE),
                ("or-exit-2", steps + b"      - run: opf doctor --require-store || exit 2\n", None, CANNOT_EVALUATE),
                ("or-exit-256", steps + b"      - run: opf doctor --require-store || exit 256\n", None,
                 CANNOT_EVALUATE),
                ("or-true", steps + b"      - run: opf doctor --require-store || true\n", None, CANNOT_EVALUATE),
                ("and-list", steps + b"      - run: opf doctor --require-store && true\n", None, CANNOT_EVALUATE),
                ("after-and", steps + b"      - run: false && opf doctor --require-store\n", None, CANNOT_EVALUATE),
                ("piped", steps + b"      - run: opf doctor --require-store | cat\n", None, CANNOT_EVALUATE),
                ("backgrounded", steps + b"      - run: opf doctor --require-store &\n", None, CANNOT_EVALUATE),
                ("negated", steps + b"      - run: '! opf doctor --require-store'\n", None, CANNOT_EVALUATE),
                ("in-if", steps + block(b"if false; then\n  opf doctor --require-store\nfi"), None,
                 CANNOT_EVALUATE),
                ("function-body", steps + block(b"f() { opf doctor --require-store; }"), None, CANNOT_EVALUATE),
                ("command-substitution", steps + b"      - run: x $(opf doctor --require-store)\n", None,
                 CANNOT_EVALUATE),
                ("comment-hides-command", steps + block(b"echo hi # ; opf doctor --require-store"), None,
                 CANNOT_EVALUATE),
                ("echo", steps + b"      - run: echo opf doctor --require-store\n", None, CANNOT_EVALUATE),
                ("quoted-one-word", steps + b"      - run: opf 'doctor --require-store'\n", None, CANNOT_EVALUATE),
                ("other-verb", steps + b"      - run: opf render --require-store\n", None, CANNOT_EVALUATE),
                ("assignment-prefix", steps + b"      - run: OPF_X=1 opf doctor --require-store\n", None,
                 CANNOT_EVALUATE),
                ("double-space", steps + b"      - run: opf  doctor --require-store\n", None, CANNOT_EVALUATE),
                ("leading-space", steps + b'      - run: " opf doctor --require-store"\n', None, CANNOT_EVALUATE),
                ("option-order", steps + b"      - run: opf doctor --root . --require-store\n", None,
                 CANNOT_EVALUATE),
                ("root-elsewhere", steps + b"      - run: opf doctor --require-store --root /srv/other\n", None,
                 CANNOT_EVALUATE),
                ("extra-option", steps + b"      - run: opf doctor --require-store --json\n", None,
                 CANNOT_EVALUATE),
                ("python-option", steps + b"      - run: python3 --version opf.py doctor --require-store\n", None,
                 CANNOT_EVALUATE),
                ("python-not-isolated", steps + b"      - run: python3 opf/tools/opf.py doctor --require-store\n",
                 None, CANNOT_EVALUATE),
                ("python-parent-path", steps + b"      - run: python3 -I -B ../opf.py doctor --require-store\n",
                 None, CANNOT_EVALUATE),
                ("step-if", steps + b"      - if: false\n        run: opf doctor --require-store\n", None,
                 CANNOT_EVALUATE),
                ("continue-on-error", steps + b"      - continue-on-error: true\n        run: opf doctor "
                                              b"--require-store\n", None, CANNOT_EVALUATE),
                ("step-env", steps + b"      - env:\n          BASH_ENV: x\n        run: opf doctor --require-store\n",
                 None, CANNOT_EVALUATE),
                ("step-working-directory", steps + b"      - working-directory: other\n"
                                                   b"        run: opf doctor --require-store\n", None, CANNOT_EVALUATE),
                ("other-shell", steps + b"      - shell: pwsh\n        run: opf doctor --require-store\n", None,
                 CANNOT_EVALUATE),
                ("custom-shell", steps + b"      - shell: bash {0}\n        run: opf doctor --require-store\n", None,
                 CANNOT_EVALUATE),
                ("earlier-run-step", steps + b"      - run: echo PATH=x >> \"$GITHUB_ENV\"\n"
                                             b"      - run: opf doctor --require-store\n", None, CANNOT_EVALUATE),
                ("job-if", b"jobs:\n  t:\n    if: false\n    steps:\n      - run: opf doctor --require-store\n", None,
                 CANNOT_EVALUATE),
                ("job-continue-on-error", b"jobs:\n  t:\n    continue-on-error: true\n    steps:\n"
                                          b"      - run: opf doctor --require-store\n", None, CANNOT_EVALUATE),
                ("job-env", b"jobs:\n  t:\n    env:\n      SHELLOPTS: noexec\n    steps:\n"
                            b"      - run: opf doctor --require-store\n", None, CANNOT_EVALUATE),
                ("workflow-env", b"env:\n  OPF_PYTHON: 'true'\n" + steps
                 + b"      - run: opf doctor --require-store\n", None, CANNOT_EVALUATE),
                ("job-default-working-directory", b"jobs:\n  t:\n    defaults:\n      run:\n"
                                                  b"        working-directory: other\n    steps:\n"
                                                  b"      - run: opf doctor --require-store\n", None, CANNOT_EVALUATE),
                ("workflow-default-working-directory", b"defaults:\n  run:\n    working-directory: other\n"
                 + steps + b"      - run: opf doctor --require-store\n", None, CANNOT_EVALUATE),
                ("workflow-default-shell", b"defaults:\n  run:\n    shell: pwsh\n" + steps
                 + b"      - run: opf doctor --require-store\n", None, CANNOT_EVALUATE),
                ("job-default-shell", b"jobs:\n  t:\n    defaults:\n      run:\n        shell: pwsh\n    steps:\n"
                                      b"      - run: opf doctor --require-store\n", None, CANNOT_EVALUATE),
                ("recipe-not-planned", steps + delegating, {}, CANNOT_EVALUATE),
                ("recipe-sh-n", steps + delegating.replace(b"sh opf/", b"sh -n opf/"), planned, CANNOT_EVALUATE),
                ("recipe-bash", steps + delegating.replace(b"sh opf/", b"bash opf/"), planned, CANNOT_EVALUATE),
                ("recipe-other-root", steps + delegating.replace(b".sh .\n", b".sh /srv/other\n"), planned,
                 CANNOT_EVALUATE),
                ("recipe-gated-step", steps + b"      - if: false\n" + delegating.replace(b"      - ", b"        "),
                 planned, CANNOT_EVALUATE),
                ("recipe-masked", steps + delegating, {_CI_RECIPE_PATH: recipe.replace(b"|| exit $?", b"|| true")},
                 CANNOT_EVALUATE),
                ("recipe-exit-0-first", steps + delegating, {_CI_RECIPE_PATH: recipe.replace(
                    b"\nrun_step doctor", b"\nexit 0\nrun_step doctor")}, CANNOT_EVALUATE),
                ("recipe-echo-function", steps + delegating, {_CI_RECIPE_PATH: recipe.replace(
                    b'"$opf_python" -I -B "$opf_tool"', b"echo")}, CANNOT_EVALUATE),
                ("recipe-function-drops-args", steps + delegating, {_CI_RECIPE_PATH: recipe.replace(
                    b'"$opf_tool" "$@"', b'"$opf_tool" render')}, CANNOT_EVALUATE),
                ("recipe-non-utf8", steps + delegating, {_CI_RECIPE_PATH: b"\xff\xfe run_step doctor\n"},
                 CANNOT_EVALUATE)):
            check("ci-" + label, ci(data, recipes) == want)
        # The pack's own workflow template (opf/enforcement/ci) is canonical with its recipe planned, and a
        # refused mention without it.
        template = (_SHIPPED_RECIPE.parent / "github-actions.yml").read_bytes()
        check("ci-shipped-template", ci(template, planned) == VALID and ci(template) == CANNOT_EVALUATE)
        # A planned recipe that is absent or drifted asserts nothing (INVALID, the member finding names it);
        # a non-canonical run of it is still refused.
        check("ci-recipe-planned-absent", ci(steps + delegating, {}, [_CI_RECIPE_PATH]) == INVALID)
        check("ci-recipe-planned-absent-mentions", ci(steps + b"      - run: sh -n opf/enforcement/ci/opf-ci.sh .\n",
                                                      {}, [_CI_RECIPE_PATH]) == CANNOT_EVALUATE)
        # Unparseable CI and every construct the YAML reader does not fully parse are CANNOT-EVALUATE.
        good = steps + b"      - run: opf doctor --require-store\n"
        for label, data in (("non-utf8", b"\xff\xfe--require-store\n"),
                            ("tab-indent", b"jobs:\n\tt: x\n"),
                            ("carriage-return", steps + b"      - run: opf doctor --require-store\r\n"),
                            ("nul-byte", good.replace(b"runs-on: x", b"runs-on: x\x00")),
                            ("c0-control", good.replace(b"runs-on: x", b"runs-on: x\x01")),
                            ("escape-control", good.replace(b"runs-on: x", b"runs-on: x\x1b[0m")),
                            ("delete-control", good.replace(b"runs-on: x", b"runs-on: x\x7f")),
                            ("c1-control", good.replace(b"runs-on: x", "runs-on: x\x85".encode("utf-8"))),
                            ("byte-order-mark", good.replace(b"runs-on: x", "runs-on: x\ufeff".encode("utf-8"))),
                            ("unterminated-quote", steps + b"      - run: opf doctor --require-store 'x\n"),
                            ("flow-step", steps + b"      - {run: opf doctor --require-store}\n"),
                            ("here-document", steps + block(b"cat <<EOF\nopf doctor --require-store\nEOF")),
                            ("duplicate-key", steps + b"      - run: a\n        run: opf doctor --require-store\n"),
                            ("multi-line-plain", steps + b"      - run: opf doctor\n          --require-store\n"),
                            ("anchor", steps + b"      - run: &a opf doctor --require-store\n"),
                            ("unbalanced-compound", steps + block(b"if true; then\nopf doctor --require-store")),
                            ("step-not-mapping", steps + b"      - opf doctor --require-store\n"),
                            ("defaults-not-mapping", b"defaults: [x]\n" + good),
                            ("yaml-unterminated-quote", steps + b'      - run: "opf doctor --require-store\n'),
                            ("yaml-after-quote", steps + b'      - run: "true" opf doctor --require-store\n'),
                            ("alias", steps + b"      - run: *ref\n"),
                            ("flow-run", steps + b"      - run: [opf, doctor, --require-store]\n"),
                            ("flow-empty-entry", b"on: [a,,b]\n" + good),
                            ("flow-leading-comma", b"on: [,a]\n" + good),
                            ("flow-trailing-comma", b"on: [a,]\n" + good),
                            ("flow-unclosed", b"on: [a, [b]\n" + good),
                            ("flow-extra-closer", b"on: [a]]\n" + good),
                            ("flow-content-after", b"on: [a] b\n" + good),
                            ("flow-pair-in-sequence", b"on: [a: b]\n" + good),
                            ("flow-mapping-no-space", b"on: {a:b}\n" + good),
                            ("flow-mapping-no-value", b"on: {a}\n" + good),
                            ("flow-mapping-key-colon", b"on: {a:xb}\n" + good),
                            ("flow-nested-too-deep", b"on: " + b"[" * 20 + b"]" * 20 + b"\n" + good),
                            ("flow-bad-escape", b'on: ["a\\qb"]\n' + good),
                            ("flow-unterminated-quote", b"on: ['a]\n" + good),
                            ("flow-indicator-entry", b"on: [&a b]\n" + good),
                            ("plain-head-comma", b"name: ,x\n" + good),
                            ("plain-head-closer", b"name: ]x\n" + good),
                            ("plain-sequence-entry", b"name: - x\n" + good),
                            ("jobs-flow", b"jobs: {}\n"),
                            ("jobs-scalar", b"jobs: x\n"),
                            ("block-less-indented", steps + b"      - run: |\n            opf doctor --require-store\n"
                                                    b"          true\n")):
            check("ci-cannot-" + label, ci(data) == CANNOT_EVALUATE)
        # The reader vectors above are load-bearing: each carrier key alone leaves the canonical step VALID.
        check("ci-reader-carriers-valid", ci(b"on: [a, b]\n" + good) == VALID
              and ci(b"on: " + b"[" * 16 + b"]" * 16 + b"\n" + good) == VALID
              and ci(good.replace(b"runs-on: x", b"runs-on: x\ty")) == VALID and ci(b"name: x\n" + good) == VALID)
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

                def _delegated(workflow, planned_recipe, live_recipe=None):
                    _put(root, _CI_PATH, workflow)
                    _put(root, _CI_RECIPE_PATH, planned_recipe if live_recipe is None else live_recipe)
                    ev.plan = dict(plan, enforcement=[
                        dict(row, members=[dict(path=_CI_PATH, digest=_digest(workflow)),
                                           dict(path=_CI_RECIPE_PATH, digest=_digest(planned_recipe))])
                        if row["platform"] == "ci" else row for row in plan["enforcement"]])
                    report = _Report()
                    try:
                        _check_operational(ev, report)
                    except Unevaluable as exc:
                        report.cannot.append(str(exc))
                    return report.result()

                check("check-4-recipe-delegation-direct", _delegated(_CI_DELEGATING, recipe).status == VALID)
                # The round-3 interpreter-option reproduction at the recipe step: `sh -n` reads the recipe
                # without running it, so the step is not canonical (CANNOT-EVALUATE, named).
                sh_n = _delegated(_CI_DELEGATING.replace(b"run: sh ", b"run: sh -n "), recipe)
                check("check-4-recipe-sh-n-direct", sh_n.status == CANNOT_EVALUATE
                      and any("not canonical" in f for f in sh_n.findings))
                check("check-4-recipe-not-shipped-direct", _delegated(_CI_DELEGATING, recipe.replace(
                    b"|| exit $?", b"|| true")).status == CANNOT_EVALUATE)
                drifted = _delegated(_CI_DELEGATING, recipe, live_recipe=recipe + b"# edited\n")
                check("check-4-recipe-drifted-direct", drifted.status == INVALID
                      and any("does not match its planned bytes" in f for f in drifted.findings))
                _put(root, _CI_PATH, _CI_BYTES)
                ev.plan = dict(plan, enforcement=[row for row in plan["enforcement"]
                                                 if row["platform"] != "ci"])
                direct = _Report()
                _check_operational(ev, direct)
                check("check-4-no-ci-row-red",
                      any("binds no CI enforcement row" in f for f in direct.findings))
                import tomllib
                inv2 = _reseal_inventory(inv, candidates=sorted(_LIVE) + ["stray.md"])
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

                check("check-2-empty-directory-accounted", _discovery(empty_directories=["empty/dir"]) == [
                    "inventory entry 'empty/dir' has neither a disposition nor a recorded exclusion"])
                check("check-2-control-area-exempt", _discovery(
                    candidates=sorted(_LIVE) + [".working/imports/x.md"]) == [])
                check("check-2-alien-kind-cannot", _discovery(entries=_entries(inv) + [dict(
                    path="legacy/odd.md", kind="symlink")]) is None)
                redigested = [dict(row, digest="sha256:" + "c" * 64) if row["path"] == "legacy/RULES.md" else row
                              for row in _entries(inv)]
                check("check-2-source-digest-bound", _discovery(entries=redigested) == [
                    "plan source 'legacy/RULES.md' is not an inventoried file with its plan digest"])
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
                      and _CI_PATH not in wanted and ".working/TODO.md" not in wanted)
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
