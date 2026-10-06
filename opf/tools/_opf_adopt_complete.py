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
    digest, and per CI row a digest-matched workflow member configures a step whose run command invokes
    the store assertion (`doctor --require-store` as a command word sequence of the opf program, or a run of
    a planned recipe member of the same row that does), parsed as YAML-shaped jobs and steps and shell
    commands, never a comment, a step name, an echo, a quoted string or a conditional or continue-on-error
    step, and undecodable or unparseable CI is CANNOT-EVALUATE; every planned view
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
digest-pinned planned CI members through a block-YAML subset and a shell subset (an anchor, a flow step, a
here-document or a multi-line plain scalar is CANNOT-EVALUATE), knows the opf program by its name (`opf`,
`opf.py`, or a python interpreter running `opf.py`) and a recipe function only by a body running a
parameter-expanded or opf program with "$@" (the function's status propagation and an environment override
of the program it runs are not modelled), reads only the step and job `if:`, `continue-on-error` and
`shell:` keys of the CI platform, and is not a run of CI.

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
    """A single-line YAML flow collection (`[main]`, `{a: b}`): kept opaque, never read as a step."""
    __slots__ = ("text",)

    def __init__(self, text):
        self.text = text


class _Workflow:
    """A reader for the block-YAML subset CI workflows are written in: block mappings with plain keys,
    block sequences, plain and quoted single-line scalars, literal and folded block scalars, and opaque
    single-line flow collections. Anything outside it (a tab in the indentation, a CR, an anchor, alias or
    tag, a quoted or duplicate key, a multi-line plain or quoted scalar, a document marker) raises
    Unevaluable: CI this check cannot parse is CANNOT-EVALUATE, never a pass and never a silent refusal."""

    def __init__(self, text, label):
        self.label, self.i, self.lines = label, 0, []
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
            if plain[-1] != {"[": "]", "{": "}"}[head] or plain.count(head) != plain.count(plain[-1]):
                self.fail("a multi-line or unbalanced flow collection")
            return _Flow(plain)
        if head in "&*!%@`|>" or ": " in plain or plain.endswith(":"):
            self.fail("an anchor, alias, tag, reserved indicator or nested mapping in a plain scalar")
        return plain

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


def _shell_subst_end(text, i, where):
    """The index just past the `$(...)`, `${...}`, `<(...)`, `>(...)` or backquote substitution opening at
    text[i]: its text stays inside the word, an argument and never a configured command."""
    if text[i] == "`":
        j = i + 1
        while j < len(text) and text[j] != "`":
            j += 2 if text[j] == "\\" else 1
        if j >= len(text):
            raise Unevaluable("{}: an unterminated backquote substitution".format(where))
        return j + 1
    opener = text[i + 1]
    closer = {"(": ")", "{": "}"}[opener]
    depth, j = 1, i + 2
    while j < len(text):
        ch = text[j]
        if ch == "\\":
            j += 2
            continue
        if ch == "'" and opener == "(":
            end = text.find("'", j + 1)
            if end < 0:
                break
            j = end + 1
            continue
        if ch == '"':
            j = _shell_dquote_end(text, j, where)
            continue
        if ch == "`" or (ch in "$<>" and text[j + 1:j + 2] in ("(", "{") and (ch == "$" or text[j + 1] == "(")):
            j = _shell_subst_end(text, j, where)
            continue
        if ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                return j + 1
        j += 1
    raise Unevaluable("{}: an unterminated substitution".format(where))


def _shell_dquote_end(text, i, where):
    """The index just past the double-quoted string opening at text[i]."""
    j = i + 1
    while j < len(text):
        ch = text[j]
        if ch == "\\":
            j += 2
        elif ch == '"':
            return j + 1
        elif ch == "`" or (ch == "$" and text[j + 1:j + 2] in ("(", "{")):
            j = _shell_subst_end(text, j, where)
        else:
            j += 1
    raise Unevaluable("{}: an unterminated double quote".format(where))


def _shell_tokens(text, where):
    """Shell text to tokens: ("w", word) after quote removal, ("op", operator) for the list, pipeline and
    subshell operators and newline. A `#` opening a word starts a comment, so a quoted `#` stays a word
    character. A here-document or an unterminated quote or substitution raises Unevaluable."""
    toks, word, i = [], None, 0
    while i < len(text):
        ch = text[i]
        if ch == "\\":
            if text[i + 1:i + 2] != "\n":
                word = (word or "") + text[i + 1:i + 2]
            i += 2
        elif ch == "'":
            end = text.find("'", i + 1)
            if end < 0:
                raise Unevaluable("{}: an unterminated single quote".format(where))
            word, i = (word or "") + text[i + 1:end], end + 1
        elif ch == '"':
            end = _shell_dquote_end(text, i, where)
            word, i = (word or "") + re.sub(r'\\([$`"\\\n])', lambda m: m.group(1).strip("\n"),
                                            text[i + 1:end - 1]), end
        elif ch == "`" or (ch == "$" and text[i + 1:i + 2] in ("(", "{")) or (
                ch == "(" and word and word[-1] in "<>"):
            start = i - 1 if ch == "(" else i
            end = _shell_subst_end(text, start, where)
            word, i = (word or "")[:len(word or "") - (ch == "(")] + text[start:end], end
        elif ch == "#" and word is None:
            while i < len(text) and text[i] != "\n":
                i += 1
        elif text.startswith("<<", i):
            raise Unevaluable("{}: a here-document is outside the shell subset this check reads".format(where))
        elif ch == "(" and word and text.startswith("()", i):
            word, i = word + "()", i + 2
        elif ch in " \t":
            if word is not None:
                toks.append(("w", word))
            word, i = None, i + 1
        elif ch in "\n;&|()" and not (ch in "&|" and word and word[-1] in "<>"):
            if word is not None:
                toks.append(("w", word))
            op = text[i:i + 2] if text[i:i + 2] in ("&&", "||", "|&", ";;") else ch
            toks.append(("op", op))
            word, i = None, i + len(op)
        else:
            word, i = (word or "") + ch, i + 1
    if word is not None:
        toks.append(("w", word))
    return toks


# Reserved words that open, continue or close a compound command, and the opener each closer matches.
_SHELL_OPENERS = ("if", "while", "until", "for", "select", "case", "{")
_SHELL_CLOSERS = {"fi": ("if",), "done": ("while", "until", "for", "select"), "esac": ("case",), "}": ("{",)}
_SHELL_JOINERS = ("then", "do", "else", "elif", "!")


def _shell_commands(text, where):
    """The simple commands of shell `text` as dicts: words (leading assignments kept), the operator before and
    after, `top` (outside every compound command, subshell and function body, not negated and not headed by
    a reserved word), and `fn` (the function whose body holds it, or None). Unbalanced compound nesting
    raises Unevaluable."""
    cmds, words, before, stack = [], [], None, []

    def close(kind):
        if not stack or stack[-1][0] not in kind:
            raise Unevaluable("{}: unbalanced shell compound commands".format(where))
        stack.pop()

    for kind, val in _shell_tokens(text, where) + [("op", None)]:
        if kind == "w":
            words.append(val)
            continue
        if words:
            lead, rest = [], list(words)
            while rest and (rest[0] in _SHELL_OPENERS or rest[0] in _SHELL_CLOSERS or rest[0] in _SHELL_JOINERS):
                head = rest.pop(0)
                lead.append(head)
                if head in _SHELL_CLOSERS:
                    close(_SHELL_CLOSERS[head])
                elif head == "{" and stack and stack[-1][0] == "fn":
                    stack[-1] = ("{", stack[-1][1])
                elif head in _SHELL_OPENERS:
                    stack.append((head, None))
                    if head in ("for", "select", "case"):
                        rest = []
            fn = next((name for _opened, name in reversed(stack) if name), None)
            if rest and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*\(\)", rest[0]):
                if rest[1:2] not in ([], ["{"]):
                    raise Unevaluable("{}: a function definition outside the subset".format(where))
                # `f()` alone waits for its `{`; `f() {` opens the body, whose first command may follow.
                stack.append(("{" if rest[1:] else "fn", rest[0][:-2]))
                fn, rest = rest[0][:-2], rest[2:]
            if rest:
                cmds.append(dict(words=rest, before=before, after=val, fn=fn, top=not stack and not lead))
            words = []
        elif val in ("\n", None) and before in ("&&", "||", "|", "|&"):
            continue  # a list or pipeline operator continues onto the next line
        elif not (val in ("\n", None, ";;", "(", ")") or before == ")" or (val == ";" and before in ("&", ";;"))):
            raise Unevaluable("{}: an empty command before {!r}".format(where, val))
        if val == "(":
            stack.append(("(", None))
        elif val == ")" and not (stack and stack[-1][0] == "case"):
            close(("(",))
        if val is not None:
            before = val
    if stack:
        raise Unevaluable("{}: unclosed shell compound commands".format(where))
    return cmds


_PYTHON_RE = re.compile(r"python(?:3(?:\.[0-9]+)?)?")
_ASSIGNMENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")
_EXPANSION_RE = re.compile(r"\$(?:[A-Za-z_][A-Za-z0-9_]*|\{[A-Za-z_][A-Za-z0-9_]*\})")


def _program(words):
    """`words` without leading variable assignments (the command's own words)."""
    k = 0
    while k < len(words) and _ASSIGNMENT_RE.match(words[k]):
        k += 1
    return words[k:]


def _opf_arguments(words, functions=()):
    """The words after the opf program when the command runs it (`opf`, a path to `opf` or `opf.py`, a
    python interpreter running `opf.py`, or a function the recipe defines to run it), else None."""
    words = _program(words)
    if not words:
        return None
    base = words[0].rsplit("/", 1)[-1]
    if base in ("opf", "opf.py") or words[0] in functions:
        return words[1:]
    if _PYTHON_RE.fullmatch(base):
        k = 1
        while k < len(words) and words[k].startswith("-"):
            k += 1
        if k < len(words) and words[k].rsplit("/", 1)[-1] == "opf.py":
            return words[k + 1:]
    return None


def _invokes_assertion(words, functions=()):
    """Whether one simple command runs the store assertion: the opf program, then the word `doctor`, then
    CI_STORE_ASSERTION as a whole later word (a command word sequence, never a substring)."""
    args = _opf_arguments(words, functions)
    return args is not None and args[:1] == ["doctor"] and CI_STORE_ASSERTION.decode("ascii") in args[1:]


def _fails_the_run(cmds, k, errexit):
    """Whether command k's failure fails its script: it is not negated, conditional, piped, backgrounded or
    inside a compound command or function body, and either the shell stops on error (errexit) and the
    command ends its list, or a following `|| exit` with a non-zero status propagates it, or (no errexit)
    it is the script's last command."""
    cmd = cmds[k]
    if not cmd["top"] or cmd["before"] not in (None, "\n", ";", "&"):
        return False
    if cmd["after"] == "||":
        handler = _program(cmds[k + 1]["words"]) if k + 1 < len(cmds) else []
        return handler[:1] == ["exit"] and handler[1:2] != ["0"] and cmds[k + 1]["after"] in (None, "\n", ";")
    if cmd["after"] not in (None, "\n", ";"):
        return False
    return errexit or k == len(cmds) - 1


def _errexit(cmds, base):
    """The shell's stop-on-error state for a script: `base` unless a `set` turns errexit off anywhere."""
    for cmd in cmds:
        words = _program(cmd["words"])
        if words[:1] == ["set"]:
            if any(w.startswith("+") and "e" in w[1:] for w in words[1:]):
                return False
            if any(w == "+o" and words[j + 2:j + 3] == ["errexit"] for j, w in enumerate(words[1:])):
                return False
    return base


def _recipe_asserts(path, data):
    """Whether a planned CI recipe (a shell script a workflow step runs) runs the store assertion as a
    command whose failure fails the recipe. A function the recipe defines counts as the opf program when
    its body runs a parameter-expanded or opf program with "$@"."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise Unevaluable("CI recipe {!r} is not UTF-8 text".format(path))
    cmds = _shell_commands(text, "CI recipe {!r}".format(path))
    functions = set()
    for cmd in cmds:
        words = _program(cmd["words"])
        if cmd["fn"] and words and "$@" in words[1:] and (
                _EXPANSION_RE.fullmatch(words[0]) or _opf_arguments(words) is not None):
            functions.add(cmd["fn"])
    errexit = _errexit(cmds, False)
    return any(_invokes_assertion(cmd["words"], functions) and _fails_the_run(cmds, k, errexit)
               for k, cmd in enumerate(cmds))


def _runs_recipe(words, recipes):
    """The planned recipe path a command runs (`sh PATH`, `bash PATH` or `PATH`, options before PATH
    allowed for the shell), else None."""
    words = _program(words)
    if words and words[0].rsplit("/", 1)[-1] in ("sh", "bash"):
        words = words[1:]
        while words and words[0].startswith("-") and words[0] not in ("-c", "-s"):
            words = words[1:]
    path = words[0] if words else ""
    while path.startswith("./"):
        path = path[2:]
    return path if path in recipes else None


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


def _ci_asserts_store(path, data, recipes=None):
    """Whether one CI workflow member configures a step whose run command invokes the store assertion
    (spec 14.1 check 4: CI asserts store presence and identity so absence cannot pass as NOT-APPLICABLE).
    The workflow is parsed as YAML-shaped jobs and steps; a step counts only when neither it nor its job is
    conditional or continue-on-error, its shell is bash or sh (each runs `-e`), and its `run:` holds a
    simple command, outside every quoted string, compound command and comment, that is the opf program's
    `doctor` with CI_STORE_ASSERTION as a whole word (an echo, a step name, an env value or a suffixed flag
    configures nothing), or that runs a planned recipe in `recipes` which itself runs the assertion
    (_recipe_asserts). Undecodable or unparseable CI raises Unevaluable: CANNOT-EVALUATE, never a pass."""
    recipes = recipes or {}
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise Unevaluable("CI workflow {!r} is not UTF-8 text".format(path))
    doc = _Workflow(text, path).document()
    jobs = doc.get("jobs") if isinstance(doc, dict) else None
    if not isinstance(jobs, dict):
        return False
    found = False
    for job_id, job in sorted(jobs.items()):
        if not isinstance(job, dict) or not isinstance(job.get("steps", []), list):
            raise Unevaluable("CI workflow {!r} job {!r} is not a mapping with a steps list".format(path, job_id))
        for n, step in enumerate(job.get("steps", [])):
            if not isinstance(step, dict):
                raise Unevaluable("CI workflow {!r} job {!r} step {} is not a mapping".format(path, job_id, n))
            run = step.get("run")
            if run is None:
                continue
            if not isinstance(run, str):
                raise Unevaluable("CI workflow {!r} job {!r} step {} run is not a string".format(path, job_id, n))
            where = "CI workflow {!r} job {!r} step {}".format(path, job_id, n)
            cmds = _shell_commands(run, where)
            shell = _shell_of(step, _run_defaults(job, where), _run_defaults(doc, where))
            if _gated(job) or _gated(step) or shell not in ("bash", "sh"):
                continue
            errexit = _errexit(cmds, True)
            for k, cmd in enumerate(cmds):
                if not _fails_the_run(cmds, k, errexit):
                    continue
                recipe = _runs_recipe(cmd["words"], recipes)
                if _invokes_assertion(cmd["words"]) or (recipe is not None
                                                        and _recipe_asserts(recipe, recipes[recipe])):
                    found = True
    return found


def _check_operational(ev, rep):
    """Spec 14.1 check 4: 'Operational readiness: the store resolves to the planned identity, and CI
    asserts store presence and identity so absence cannot pass as NOT-APPLICABLE. Doctor validity and
    declared-view byte drift are evaluated on the live tree, where apply has already rendered every
    declared view, a formerly occupied destination included, and any drift the plan does not account for
    fails the check. Consumer repointings match the plan.' The plan accounts for a view destination by
    binding its rendered member digest, nothing else: old occupant bytes there are unaccounted drift (spec
    14.1: once apply commits, restoring an archived file takes a fresh plan), and no doctor finding is ever
    set aside. CI asserts only through a configured step whose run command invokes the store assertion
    (_ci_asserts_store), never a comment, a step name or an echo; unparseable CI is CANNOT-EVALUATE."""
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
        # Every live workflow is parsed (no short-circuit), so unparseable CI is CANNOT-EVALUATE, never masked.
        asserted = [path for path, data in sorted(live.items())
                    if _is_workflow(path) and _ci_asserts_store(path, data, recipes)]
        if not asserted:
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
# A recipe-delegating workflow and recipe shaped like the shipped pack (opf/enforcement/ci): the step runs the
# planned recipe, and the recipe's own function runs the opf program with "$@" and its status propagates.
_CI_RECIPE_PATH = "opf/enforcement/ci/opf-ci.sh"
_CI_DELEGATING = (b"jobs:\n  opf:\n    steps:\n      - name: OPF CI floor (doctor --require-store)\n"
                  b"        run: sh opf/enforcement/ci/opf-ci.sh .\n")
_CI_RECIPE = (b"#!/bin/sh\nset -u\nroot=${1:-.}\n"
              b"run_step() {\n    \"$opf_python\" -I -B \"$opf_tool\" \"$@\"\n    rc=$?\n"
              b"    case \"$rc\" in\n        0|1|2) return \"$rc\" ;;\n    esac\n    return 2\n}\n"
              b"run_step doctor --require-store --root \"$root\" || exit $?\n"
              b"run_step render --check --root \"$root\"\n")
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
        # The round-2 counterexamples end to end: the assertion token only in a top-level block scalar, an
        # echo, a suffixed flag; undecodable and unparseable CI; a quoted `#` inside a real step.
        for label, ci_bytes, want in (
                ("name-block-scalar", b"name: |\n  --require-store\non: push\njobs:\n  test:\n"
                                      b"    runs-on: ubuntu-latest\n    steps:\n      - run: echo ok\n", INVALID),
                ("echo", b"jobs:\n  t:\n    steps:\n      - run: echo --require-store\n", INVALID),
                ("suffixed-flag", b"jobs:\n  t:\n    steps:\n      - run: opf doctor --require-store-fake\n",
                 INVALID),
                ("undecodable", b"\xff\xfe--require-store\n", CANNOT_EVALUATE),
                ("unparseable", b"jobs:\n  t:\n    steps:\n      - run: opf doctor --require-store 'x\n",
                 CANNOT_EVALUATE),
                ("quoted-hash-block", b"jobs:\n  t:\n    steps:\n      - run: |\n"
                                      b"          printf ' # '; opf doctor --require-store\n", VALID)):
            with mock.patch.object(here, "_CI_BYTES", ci_bytes):
                got = _case()
            check("check-4-ci-" + label, _only(got, {} if want == VALID else _red(OPERATIONAL, want)))
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
        # The CI configured-step rule, unit level: a step's run command invokes the assertion, nothing
        # else does; undecodable or unparseable CI raises (CANNOT-EVALUATE).
        steps = b"jobs:\n  t:\n    runs-on: x\n    steps:\n"

        def ci(step, recipes=None):
            return _ci_asserts_store(_CI_PATH, steps + step, recipes)

        def ci_cannot(data):
            try:
                _ci_asserts_store(_CI_PATH, data)
            except Unevaluable:
                return True
            return False

        for label, step in (
                ("run-line", b"      - run: opf doctor --require-store\n"),
                ("python-opf-py", b"      - run: python3 -I -B opf/tools/opf.py doctor --require-store\n"),
                ("double-quoted", b'      - run: "opf doctor --require-store"\n'),
                ("block-scalar", b"      - run: |\n          echo start\n          opf doctor --require-store\n"),
                ("folded-scalar", b"      - run: >\n          opf doctor\n          --require-store\n"),
                ("or-exit", b"      - run: opf doctor --require-store || exit 2\n"),
                ("set-e-off-last", b"      - run: |\n          set +e\n          opf doctor --require-store\n"),
                ("assignment-prefix", b"      - run: OPF_X=1 opf doctor --require-store\n"),
                ("sh-shell", b"      - shell: sh\n        run: opf doctor --require-store\n"),
                ("continue-on-error-false",
                 b"      - continue-on-error: false\n        run: opf doctor --require-store\n"),
                ("errexit-not-last", b"      - run: |\n          opf doctor --require-store\n          echo done\n"),
                ("sequence-at-key-indent", b"    - run: opf doctor --require-store\n")):
            check("ci-assert-" + label, ci(step))
        for label, step in (
                ("comment", b"      - run: echo  # opf doctor --require-store\n"),
                ("step-name", b"      - name: opf doctor --require-store\n        run: y\n"),
                ("env-value", b"      - env:\n          X: opf doctor --require-store\n        run: y\n"),
                ("echo", b"      - run: echo opf doctor --require-store\n"),
                ("quoted-one-word", b"      - run: opf 'doctor --require-store'\n"),
                ("suffixed-flag", b"      - run: opf doctor --require-store-fake\n"),
                ("other-verb", b"      - run: opf render --require-store\n"),
                ("or-true", b"      - run: opf doctor --require-store || true\n"),
                ("or-exit-0", b"      - run: opf doctor --require-store || exit 0\n"),
                ("and-list", b"      - run: opf doctor --require-store && true\n"),
                ("after-and", b"      - run: false && opf doctor --require-store\n"),
                ("piped", b"      - run: opf doctor --require-store | cat\n"),
                ("backgrounded", b"      - run: opf doctor --require-store &\n"),
                ("negated", b"      - run: '! opf doctor --require-store'\n"),
                ("in-if", b"      - run: |\n          if false; then\n            opf doctor --require-store\n"
                          b"          fi\n"),
                ("in-subshell-masked", b"      - run: |\n          (\n          opf doctor --require-store\n"
                                       b"          ) || true\n"),
                ("function-body", b"      - run: |\n          f() { opf doctor --require-store; }\n          true\n"),
                ("command-substitution", b"      - run: x $(opf doctor --require-store)\n"),
                ("set-e-off-mid", b"      - run: |\n          set +e\n          opf doctor --require-store\n"
                                  b"          true\n"),
                ("step-if", b"      - if: false\n        run: opf doctor --require-store\n"),
                ("continue-on-error", b"      - continue-on-error: true\n        run: opf doctor --require-store\n"),
                ("other-shell", b"      - shell: pwsh\n        run: opf doctor --require-store\n"),
                ("recipe-not-planned", b"      - run: sh opf/enforcement/ci/opf-ci.sh .\n"),
                ("echo-doctor", b"      - run: echo doctor --require-store\n"),
                ("comment-hides-command", b"      - run: |\n          echo hi # ; opf doctor --require-store\n"),
                ("set-plus-o-errexit-mid", b"      - run: |\n          set +o errexit\n"
                                           b"          opf doctor --require-store\n          true\n"),
                ("set-plus-euo-mid", b"      - run: |\n          set +euo pipefail\n"
                                     b"          opf doctor --require-store\n          true\n")):
            check("ci-refuse-" + label, not ci(step))
        check("ci-refuse-job-if", not _ci_asserts_store(_CI_PATH, b"jobs:\n  t:\n    if: false\n    steps:\n"
                                                                  b"      - run: opf doctor --require-store\n"))
        check("ci-refuse-workflow-default-shell", not _ci_asserts_store(
            _CI_PATH, b"defaults:\n  run:\n    shell: pwsh\n" + steps + b"      - run: opf doctor --require-store\n"))
        check("ci-refuse-job-default-shell", not _ci_asserts_store(
            _CI_PATH, b"jobs:\n  t:\n    defaults:\n      run:\n        shell: pwsh\n    steps:\n"
            b"      - run: opf doctor --require-store\n"))
        check("ci-refuse-sequence-value", not _ci_asserts_store(
            _CI_PATH, b"on:\n  push:\n    branches:\n      - opf doctor --require-store\n" + steps
            + b"      - run: echo\n"))
        recipes = {_CI_RECIPE_PATH: _CI_RECIPE}
        check("ci-assert-recipe-delegation", _ci_asserts_store(_CI_PATH, _CI_DELEGATING, recipes))
        check("ci-refuse-recipe-masked", not _ci_asserts_store(_CI_PATH, _CI_DELEGATING, {
            _CI_RECIPE_PATH: _CI_RECIPE.replace(b"|| exit $?", b"|| true")}))
        check("ci-refuse-recipe-echo-function", not _ci_asserts_store(_CI_PATH, _CI_DELEGATING, {
            _CI_RECIPE_PATH: _CI_RECIPE.replace(b'\"$opf_python\" -I -B \"$opf_tool\"', b"echo")}))
        check("ci-refuse-recipe-not-last-no-exit", not _ci_asserts_store(_CI_PATH, _CI_DELEGATING, {
            _CI_RECIPE_PATH: _CI_RECIPE.replace(b" || exit $?", b"")}))
        check("ci-assert-recipe-dot-slash", _ci_asserts_store(_CI_PATH, _CI_DELEGATING.replace(
            b"sh opf/", b"sh ./opf/"), recipes))
        check("ci-refuse-recipe-function-drops-args", not _ci_asserts_store(_CI_PATH, _CI_DELEGATING, {
            _CI_RECIPE_PATH: _CI_RECIPE.replace(b'\"$opf_tool\" \"$@\"', b'\"$opf_tool\" render')}))
        check("ci-assert-recipe-last-command", _ci_asserts_store(_CI_PATH, _CI_DELEGATING, {
            _CI_RECIPE_PATH: _CI_RECIPE.replace(b" || exit $?", b"").replace(
                b'run_step render --check --root \"$root\"\n', b"")}))
        check("ci-refuse-recipe-gated-step", not _ci_asserts_store(_CI_PATH, _CI_DELEGATING.replace(
            b"        run:", b"        if: false\n        run:"), recipes))
        for label, data in (("non-utf8", b"\xff\xfe--require-store\n"),
                            ("tab-indent", b"jobs:\n\tt: x\n"),
                            ("carriage-return", steps + b"      - run: opf doctor --require-store\r\n"),
                            ("unterminated-quote", steps + b"      - run: opf doctor --require-store 'x\n"),
                            ("flow-step", steps + b"      - {run: opf doctor --require-store}\n"),
                            ("here-document", steps + b"      - run: |\n          cat <<EOF\n"
                                              b"          opf doctor --require-store\n          EOF\n"),
                            ("duplicate-key", steps + b"      - run: a\n        run: opf doctor --require-store\n"),
                            ("multi-line-plain", steps + b"      - run: opf doctor\n          --require-store\n"),
                            ("anchor", steps + b"      - run: &a opf doctor --require-store\n"),
                            ("plain-scalar-cut-at-hash", steps + b"      - run: printf ' # '; opf doctor "
                                                         b"--require-store\n"),
                            ("unbalanced-compound", steps + b"      - run: |\n          if true; then\n"
                                                    b"          opf doctor --require-store\n"),
                            ("step-not-mapping", steps + b"      - opf doctor --require-store\n"),
                            ("defaults-not-mapping", b"defaults: [x]\n" + steps
                                                     + b"      - run: opf doctor --require-store\n"),
                            ("yaml-unterminated-quote", steps + b'      - run: "opf doctor --require-store\n'),
                            ("yaml-after-quote", steps + b'      - run: "true" opf doctor --require-store\n'),
                            ("alias", steps + b"      - run: *ref\n"),
                            ("flow-run", steps + b"      - run: [opf, doctor, --require-store]\n"),
                            ("block-less-indented", steps + b"      - run: |\n            opf doctor --require-store\n"
                                                    b"          true\n")):
            check("ci-cannot-" + label, ci_cannot(data))
        try:
            _ci_asserts_store(_CI_PATH, _CI_DELEGATING, {_CI_RECIPE_PATH: b"\xff\xfe run_step doctor\n"})
            check("ci-cannot-recipe-non-utf8", False)
        except Unevaluable:
            check("ci-cannot-recipe-non-utf8", True)
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
                _put(root, _CI_PATH, _CI_DELEGATING)
                _put(root, _CI_RECIPE_PATH, _CI_RECIPE)
                ev.plan = dict(plan, enforcement=[
                    dict(row, members=[dict(path=_CI_PATH, digest=_digest(_CI_DELEGATING)),
                                       dict(path=_CI_RECIPE_PATH, digest=_digest(_CI_RECIPE))])
                    if row["platform"] == "ci" else row for row in plan["enforcement"]])
                direct = _Report()
                _check_operational(ev, direct)
                check("check-4-recipe-delegation-direct", direct.result().status == VALID)
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
