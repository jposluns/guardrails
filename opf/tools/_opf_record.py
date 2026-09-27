#!/usr/bin/env python3
"""opf record: the stdlib record-authoring verb (OPF-SPEC.md section 8.8). Stdlib only, fail-closed.

  opf record create --type T --title S --actor KIND[:ID] [--summary S] [--link REL=ID]...
                    [--ref KIND LOCATOR NOTE]... [--field NAME=VALUE]... [--scope ID]... [--root DIR]
  opf record worklog-append --kind K --summary S --actor KIND[:ID] [--detail S] [--link REL=ID]...
                    [--ref KIND LOCATOR NOTE]... [--root DIR]
  opf record transition ...          recognized, NOT YET IMPLEMENTED in this build (fail-closed, exit 2)
  opf record done-with-receipt ...   recognized, NOT YET IMPLEMENTED in this build (fail-closed, exit 2)
  _opf_record.py --self-test         the unit leg (also registered in opf.py --self-test)

Every subcommand runs ONE shared operation sequence (_run_operation), the `opf upgrade` shell applied to
record authoring (spec 9.2 is the precedent; spec 8.8 is normative):
  1. resolve the store; reconcile any interrupted `opf record` journal FIRST (a reconciled interruption
     refuses this run, exit 2, so the operator inspects it before anything new is written);
  2. read the manifest, counters.toml, and the operand file (the type's `<type>.index.toml`, or
     worklog.toml), plus version.toml for the released-span boundary;
  3. PRECONDITION: re-emitting each operand's UNCHANGED parsed model reproduces its on-disk bytes exactly
     (a comment-bearing or hand-edited file refuses, bytes untouched);
  4. plan the new models: claim the ids through the ONE allocation seam (claim_ids), compose and
     validate the record through the _opf_schema primitives, refuse an append into a released span;
  5. POSTCONDITION: the old->new model diff equals exactly the allowed delta (the planned rows appended,
     the counters advanced by exactly the claim, nothing else), value for value;
  6. the planned-destination cleanliness gate and the single-writer lease (the shared _opf_write_guard
     shell, moved from opf.py), held across publication, render, and the final doctor;
  7. ONE _journal.run_transaction publishes every operand (counters first), rooted at
     .aiqt/record/journal, each operand pinned to the exact bytes it was planned from;
  8. render the declared views (--write), then require a full doctor VALID;
  9. release the lease, THEN report: the ids, the files, and that the change is left UNCOMMITTED in the
     working tree (this verb never runs git add or commit).

The allocation seam: homes 1 claims each id by advancing counters.toml as an operand INSIDE the journaled
transaction, under the held lease, and reports ids only after the COMPLETE frame, so a rollback restores
the counters without ever un-publishing an observed id. Homes 2 (the irrevocable
journals/<kind>/allocations reservation, spec 4.2) is not active in this build, and the seam refuses it
fail-closed rather than claim through the homes-1 path.

Exit contract: 0 recorded and doctor-VALID (uncommitted); 2 refusal or cannot-evaluate, with recovery
text where the working tree changed. Exit 1 is not used (it is doctor's own finding code).

DISCLOSED RESIDUALS: there is no pre-doctor, so a store invalid in a way the preconditions do not inspect
fails only at the final doctor, after publication (the change is then left for review with scoped
recovery text, as `opf upgrade` does); a completed transaction's journal directory is retained under
.aiqt/record/journal as local recovery evidence; the lease is not made observable at a sync target (no
sync runtime in this build, so the guarantee is single-host single-writer); two branches allocating from
the same committed counters can both claim an id, which spec 5.7's store-path merge policy and doctor's
C-ID-SPACE check (not this verb) catch.
"""
import copy
import datetime
import hashlib
import json
import os
import shlex
import stat
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _journal          # noqa: E402
import _opf_check        # noqa: E402
import _opf_emit         # noqa: E402
import _opf_observe      # noqa: E402
import _opf_release      # noqa: E402
import _opf_schema       # noqa: E402
import _opf_store        # noqa: E402
import _opf_views        # noqa: E402
import _opf_write_guard  # noqa: E402

EXIT_OK = 0
EXIT_MALFORMED = 2

VERB = "record"
SUBCOMMANDS = ("create", "transition", "done-with-receipt", "worklog-append")
IMPLEMENTED = ("create", "worklog-append")
# The journal root, at the STORE root and outside `.working/`, so it is never a store operand and never
# enters the containment walk (the .aiqt/import/journal precedent).
JOURNAL_REL = ".aiqt/record/journal"
SESSION_ID = "opf-record"
CLEAN_SCOPE = "record and render destinations"
# Record authors. An importer records imported history through `opf import`, never through this verb.
AUTHOR_KINDS = ("maintainer", "assistant", "automation")
# Baseline types minted by another subcommand, never by `create`.
_OTHER_SUBCOMMAND = {"done": "done-with-receipt", "worklog": "worklog-append"}
# Extra fields that are not scalar strings, set through their own options (or not settable in this build).
_NON_STRING_FIELDS = frozenset(("scopes", "delivery"))

_OPTIONS = {
    "create": ("--root", "--type", "--title", "--summary", "--actor", "--link", "--ref", "--field", "--scope"),
    "worklog-append": ("--root", "--kind", "--summary", "--actor", "--detail", "--link", "--ref"),
}
_REQUIRED = {"create": ("--type", "--title", "--actor"), "worklog-append": ("--kind", "--summary", "--actor")}
_REPEATED = frozenset(("--link", "--ref", "--field", "--scope"))


class RecordError(Exception):
    """A fail-closed refusal or cannot-evaluate carrying the operator-facing reason (mapped to exit 2)."""


class Request:
    """One parsed `opf record` invocation."""
    __slots__ = ("subcommand", "root", "values", "links", "refs", "fields", "scopes", "actor")

    def __init__(self, subcommand):
        self.subcommand = subcommand
        self.root = "."
        self.values = {}
        self.links = []
        self.refs = []
        self.fields = []
        self.scopes = []
        self.actor = None


def _clock_now():
    """The wall clock, read once per operation for created_at/updated_at/date (spec 8.3). A seam so a
    fixture can pin deterministic bytes."""
    return datetime.datetime.now(datetime.timezone.utc)


def _rfc3339(now):
    return now.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_actor(value):
    kind, sep, ident = value.partition(":")
    if kind not in AUTHOR_KINDS:
        raise RecordError("--actor kind {!r} is not one of {} (an importer records history through opf "
                          "import)".format(kind, ", ".join(AUTHOR_KINDS)))
    actor = {"kind": kind}
    if sep:
        if not ident.strip():
            raise RecordError("--actor {!r} carries an empty id after ':'".format(value))
        actor["id"] = ident
    return actor


def _parse_pair(option, value):
    name, sep, rest = value.partition("=")
    if not sep or not name or not rest:
        raise RecordError("{} requires NAME=VALUE, not {!r}".format(option, value))
    return name, rest


def parse_request(argv):
    """Parse `opf record <subcommand> ...` into a Request, or raise RecordError (a usage refusal, exit 2).
    A recognized-but-unimplemented subcommand refuses here, before any store is touched. Every option
    value must be present, non-empty, and must not start with `--` (so a swallowed next option is refused
    rather than read as a value); a single-valued option given twice refuses."""
    if not argv:
        raise RecordError("a subcommand is required: one of {}".format(", ".join(SUBCOMMANDS)))
    sub = argv[0]
    if sub not in SUBCOMMANDS:
        raise RecordError("unknown subcommand {!r}; known subcommands: {}".format(sub, ", ".join(SUBCOMMANDS)))
    if sub not in IMPLEMENTED:
        raise RecordError("{}: not yet implemented in this build (fail-closed)".format(sub))
    req = Request(sub)
    allowed = _OPTIONS[sub]
    seen = set()
    i = 1
    while i < len(argv):
        tok = argv[i]
        if tok not in allowed:
            raise RecordError("{}: unrecognized argument {!r}".format(sub, tok))
        arity = 3 if tok == "--ref" else 1
        vals = argv[i + 1:i + 1 + arity]
        if len(vals) != arity or any(v == "" or v.startswith("--") for v in vals):
            raise RecordError("{}: {} requires {} non-empty argument(s)".format(sub, tok, arity))
        if tok not in _REPEATED:
            if tok in seen:
                raise RecordError("{}: {} given more than once".format(sub, tok))
            seen.add(tok)
        if tok == "--root":
            if vals[0].startswith("-"):
                raise RecordError("{}: --root requires a directory argument, not {!r}".format(sub, vals[0]))
            req.root = vals[0]
        elif tok == "--actor":
            req.actor = _parse_actor(vals[0])
        elif tok == "--link":
            rel, rid = _parse_pair(tok, vals[0])
            req.links.append({"rel": rel, "id": rid})
        elif tok == "--ref":
            req.refs.append({"kind": vals[0], "locator": vals[1], "note": vals[2]})
        elif tok == "--field":
            req.fields.append(_parse_pair(tok, vals[0]))
        elif tok == "--scope":
            req.scopes.append(vals[0])
        else:
            req.values[tok] = vals[0]
        i += 1 + arity
    missing = [opt for opt in _REQUIRED[sub] if opt not in seen]
    if missing:
        raise RecordError("{}: missing required option(s) {}".format(sub, ", ".join(missing)))
    return req


# --- reading and the byte-reproduction precondition -------------------------------------------------

def _emit_bytes(model):
    """The ONLY serializer this verb writes through: emit_checked reparses its own output and proves it
    model-equal to the input before returning, so nothing that does not round-trip is ever published."""
    try:
        return _opf_emit.emit_checked(model).encode("utf-8")
    except _opf_emit.EmitError as exc:
        raise RecordError("the planned document does not round-trip through the canonical emitter ({}); "
                          "nothing written (fail-closed)".format(exc))


class Operand:
    """One store file this operation reads and (for a publication operand) rewrites."""
    __slots__ = ("rel", "raw", "mode", "model", "new_model", "new_raw")

    def __init__(self, rel, raw, mode, model):
        self.rel = rel
        self.raw = raw
        self.mode = mode
        self.model = model
        self.new_model = None
        self.new_raw = None


def _read_operand(root_fd, rel):
    """Read a contained, singly-linked regular store file no-follow and parse it. An absent file refuses;
    an unreadable or unparseable one is cannot-evaluate (never read as empty)."""
    import tomllib
    try:
        data, st = _journal._read_contained(root_fd, rel, require_single_link=True)
    except _journal.JournalError as exc:
        raise RecordError("cannot read {} ({}); fail-closed".format(rel, exc))
    if not stat.S_ISREG(st.st_mode):
        raise RecordError("{} is not a regular file; fail-closed".format(rel))
    try:
        model = tomllib.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError, RecursionError) as exc:
        raise RecordError("{} does not parse as TOML ({}); fail-closed".format(rel, exc))
    return Operand(rel, data, stat.S_IMODE(st.st_mode), model)


def _require_canonical(operand):
    """PRECONDITION (the spec 9.2 guard, applied to every file this operation rewrites): re-emitting the
    UNCHANGED parsed model must reproduce the on-disk bytes exactly, proving the file is canonical and
    comment-free, so a whole-document regeneration loses nothing. A hand-edited, hand-merged, or
    comment-bearing file refuses and is left untouched."""
    if _emit_bytes(operand.model) != operand.raw:
        raise RecordError("{} is not in canonical new-document form (hand-edited, hand-merged, or "
                          "comment-bearing); refusing a whole-document rewrite that could lose content. "
                          "Restore it from the integration base and redo the operation there (spec 5.7); "
                          "nothing written (fail-closed)".format(operand.rel))


# --- the store context ------------------------------------------------------------------------------

class Context:
    """The resolved store and the models this operation plans from."""
    __slots__ = ("res", "root", "root_fd", "machine_rel", "manifest", "homes", "types", "vendors",
                 "counters", "version")

    def __init__(self, res, root, root_fd):
        self.res = res
        self.root = root
        self.root_fd = root_fd
        self.machine_rel = res.machine_rel
        self.manifest = None
        self.homes = None
        self.types = None
        self.vendors = frozenset()
        self.counters = None
        self.version = None

    def rel(self, name):
        return "{}/{}".format(self.machine_rel, name)


def _load_manifest(ctx):
    """Read and validate the manifest (never an operand of this verb). It must be doctor-grade VALID at
    the tooling spec_version, in the inline layout: an older store takes `opf upgrade` first."""
    manifest = _read_operand(ctx.root_fd, ctx.rel(_opf_store.MANIFEST_NAME)).model
    mv = _opf_store.validate_manifest(manifest)
    if mv.status != _opf_store.VALID:
        raise RecordError("the store manifest is not VALID ({}); fail-closed".format("; ".join(mv.findings)))
    base = manifest.get(_opf_store.STANDARD_TOKEN)
    if base.get("spec_version") != _opf_store.SUPPORTED_SPEC_VERSION:
        raise RecordError("the store declares spec_version {!r}, not the tooling's {}; run opf upgrade "
                          "first (fail-closed)".format(base.get("spec_version"),
                                                       _opf_store.SUPPORTED_SPEC_VERSION))
    if base.get("layout") != "inline":
        raise RecordError("layout {!r} is not supported by this build (inline only); fail-closed".format(
            base.get("layout")))
    ctx.manifest = manifest
    ctx.homes = _opf_store.homes_generation(manifest)
    ctx.types = _opf_check._authoritative_types(_opf_check._enabled_modules(manifest), manifest.get("types"))
    reg = (manifest.get("vendors") or {}).get("registered")
    ctx.vendors = frozenset(reg) if isinstance(reg, list) else frozenset()


def _counter_state(ctx, counters_model):
    """(high, findings): the counters map validated with the COMPLETENESS control (every enabled type's
    namespace must carry a high-water, the C-COUNTERS rule). Empty findings is the proof that licenses
    next_id(..., known_complete=True): an absent counter can then never read as high-water 0 and reuse an
    existing id (spec 8.2)."""
    return _opf_schema.validate_counters(
        counters_model, known_namespaces=frozenset(ctx.types.values()),
        optional_namespaces=frozenset(_opf_store.IMPORTER_TYPES.values()))


def claim_ids(homes, high, demand, known_complete):
    """THE allocation seam (exactly one). `demand` is the ordered list of namespaces to claim; returns
    (ids, new_high). Homes 1: next_id per namespace from the proven-complete high-water map; the caller
    publishes the counters advance as an operand INSIDE the journaled transaction under the held lease, so
    the claim is one atomic act (spec 8.2) and ids are reported only after COMPLETE. Homes 2 delegates to
    the irrevocable journals/<kind>/allocations reservation (spec 4.2), which is not active in this build:
    it refuses fail-closed rather than claim through the homes-1 path."""
    if homes != 1:
        raise RecordError("homes-{} id allocation (an irrevocable journals/<kind>/allocations reservation, "
                          "spec 4.2) is not active in this build; refusing rather than claiming ids through "
                          "the homes-1 counters path (fail-closed)".format(homes))
    cur = dict(high)
    ids = []
    for ns in demand:
        try:
            rid, n = _opf_schema.next_id(cur, ns, known_complete=known_complete)
        except ValueError as exc:
            raise RecordError("cannot claim an id: {}; fail-closed".format(exc))
        cur[ns] = n
        ids.append(rid)
    return ids, cur


def _claim(ctx, demand):
    """Claim `demand` against the counters operand: the proof, then the seam, then the new model."""
    high, findings = _counter_state(ctx, ctx.counters.model)
    if findings:
        raise RecordError("counters.toml cannot license an allocation: {} (fail-closed)".format(
            "; ".join(findings)))
    ids, new_high = claim_ids(ctx.homes, high, demand, known_complete=True)
    new_model = copy.deepcopy(ctx.counters.model)
    new_model["counters"] = dict(new_model.get("counters") or {})
    for ns in demand:
        new_model["counters"][ns] = new_high[ns]
    ctx.counters.new_model = new_model
    return ids


def _check_released_span(version_model, wl_number):
    """A worklog entry MUST land in the unreleased tail, never inside a span a release has frozen by its
    coverage_digest (spec 6.2). The claimed WL id is normally above every released span; this refuses
    the case where it is not (a regressed counter or a hand-edited ledger), fail-closed."""
    findings = _opf_release.check_no_append_into_released(version_model, [wl_number])
    if findings:
        raise RecordError("worklog-append refused: {} (fail-closed)".format("; ".join(findings)))


def _validated(record, expected_type, ctx):
    rv = _opf_schema.validate_record(record, expected_type=expected_type, registered_vendors=ctx.vendors)
    if rv.status != _opf_store.VALID:
        raise RecordError("the planned {} record is not valid: {} (fail-closed)".format(
            expected_type, "; ".join(rv.findings)))
    return record


def _envelope_extras(req, record):
    if "--summary" in req.values:
        record["summary"] = req.values["--summary"]
    if req.links:
        record["links"] = [dict(link) for link in req.links]
    if req.refs:
        record["refs"] = [dict(ref) for ref in req.refs]


def _index_rows(operand):
    model = operand.model
    if not (isinstance(model, dict) and set(model) <= _opf_check.INDEX_TOP_KEYS
            and model.get("schema") == _opf_schema.SUPPORTED_SCHEMA
            and isinstance(model.get("record", []), list)):
        raise RecordError("{} is not a schema-1 {{schema, record}} index; fail-closed".format(operand.rel))
    return model.get("record", [])


class Plan:
    """The planned publication: the operands in journal order (counters first), the rows each appends,
    and the ids claimed. The postcondition checks the operands against exactly this delta."""
    __slots__ = ("operands", "appended", "claims", "ids")

    def __init__(self, operands, appended, claims, ids):
        self.operands = operands      # [Operand], counters first
        self.appended = appended      # {rel: (list key, [rows])}
        self.claims = claims          # {namespace: count}
        self.ids = ids                # the claimed ids, in claim order


def _plan_create(req, ctx, operand, now):
    """`create`: one new record of a baseline type in its initial state. An assistant or automation
    author entering a gated initial state lands `/proposed` (spec 8.4); a created-terminal factual type
    (reference, autonomous_decision, maintainer_decision) carries no qualifier. The id is claimed through
    the seam; the record is validated by validate_record before anything is planned further."""
    rtype = req.values["--type"]
    spec = _opf_schema.BASELINE_SPECS.get(rtype)
    if rtype in _OTHER_SUBCOMMAND:
        raise RecordError("create does not mint {} records; use opf record {} (fail-closed)".format(
            rtype, _OTHER_SUBCOMMAND[rtype]))
    if spec is None or rtype not in ctx.types:
        raise RecordError("create supports the enabled baseline record types only, not {!r} (module-tier "
                          "record schemas have not shipped); fail-closed".format(rtype))
    rows = _index_rows(operand)
    (rid,) = _claim(ctx, [spec.namespace])
    if any(isinstance(r, dict) and r.get("id") == rid for r in rows):
        raise RecordError("the claimed id {} is already seated in {} (counters.toml is behind the store; a "
                          "regressed counter is never a licence to allocate); fail-closed".format(rid, operand.rel))
    ts = _rfc3339(now)
    qual = "proposed" if req.actor["kind"] in _opf_schema.PROPOSER_KINDS and spec.initial in spec.gated else None
    record = {"id": rid, "type": rtype, "status": spec.initial if qual is None else spec.initial + "/" + qual,
              "title": req.values["--title"], "created_at": ts, "updated_at": ts, "actor": dict(req.actor)}
    _envelope_extras(req, record)
    for name, value in req.fields:
        if name not in spec.extra_keys or name in _NON_STRING_FIELDS:
            raise RecordError("--field {!r} is not a string field of {} (declared: {}); fail-closed".format(
                name, rtype, ", ".join(sorted(spec.extra_keys - _NON_STRING_FIELDS)) or "none"))
        if name in record:
            raise RecordError("--field {!r} given more than once; fail-closed".format(name))
        record[name] = value
    if req.scopes:
        if "scopes" not in spec.extra_keys:
            raise RecordError("--scope applies to block records only, not {}; fail-closed".format(rtype))
        record["scopes"] = list(req.scopes)
    _validated(record, rtype, ctx)
    # Append to a DEEP copy: sharing row objects with the old model would let a stray mutation of an
    # existing row alter both sides and slip past the postcondition.
    operand.new_model = copy.deepcopy(operand.model)
    operand.new_model["record"] = list(operand.new_model.get("record", [])) + [record]
    return Plan([ctx.counters, operand], {operand.rel: ("record", [record])}, {spec.namespace: 1}, [rid])


def _plan_worklog_append(req, ctx, operand, now):
    """`worklog-append`: one entry appended to the UNRELEASED tail of worklog.toml (spec 6.2). A worklog
    entry records a fact, so it takes no `/proposed` qualifier whatever the actor (its status is fixed
    `recorded`, spec 8.3/8.4). An id that would fall inside a released span refuses."""
    model = operand.model
    if not (isinstance(model, dict) and set(model) <= _opf_release.WORKLOG_TOP_KEYS
            and model.get("schema") == _opf_schema.SUPPORTED_SCHEMA
            and isinstance(model.get("entry", []), list)):
        raise RecordError("{} is not a schema-1 worklog ledger; fail-closed".format(operand.rel))
    entries = model.get("entry", [])
    ns = _opf_release.WL_NAMESPACE
    (wid,) = _claim(ctx, [ns])
    if any(isinstance(e, dict) and e.get("id") == wid for e in entries):
        raise RecordError("the claimed id {} is already seated in {} (counters.toml is behind the store); "
                          "fail-closed".format(wid, operand.rel))
    _check_released_span(ctx.version, int(wid.split("-", 1)[1]))
    entry = {"id": wid, "date": _rfc3339(now), "actor": dict(req.actor), "kind": req.values["--kind"]}
    _envelope_extras(req, entry)
    if "--detail" in req.values:
        entry["detail"] = req.values["--detail"]
    _validated(entry, "worklog", ctx)
    operand.new_model = copy.deepcopy(model)
    operand.new_model["entry"] = list(operand.new_model.get("entry", [])) + [entry]
    return Plan([ctx.counters, operand], {operand.rel: ("entry", [entry])}, {ns: 1}, [wid])


_PLANNERS = {"create": _plan_create, "worklog-append": _plan_worklog_append}


def _postcondition(plan, counters_rel):
    """POSTCONDITION (the spec 9.2 guard, applied to record authoring): for every operand, the new model
    must equal EXACTLY the old model plus the operation's allowed delta, value for value: the planned
    rows appended to the operand's row list, the counters advanced by exactly the claim, and nothing else.
    A stray mutation of an existing record, a lost or reordered row, a changed schema marker, or a counter
    moved by anything but the claim refuses before anything is written."""
    rels = [o.rel for o in plan.operands]
    if len(set(rels)) != len(rels) or counters_rel not in rels or set(plan.appended) - set(rels):
        raise RecordError("postcondition failed: the operand set does not match the planned delta; "
                          "nothing written (fail-closed)")
    for operand in plan.operands:
        expected = copy.deepcopy(operand.model)
        if operand.rel == counters_rel:
            table = dict(expected.get("counters") or {})
            for ns, count in plan.claims.items():
                table[ns] = table.get(ns, 0) + count
            expected["counters"] = table
        if operand.rel in plan.appended:
            key, rows = plan.appended[operand.rel]
            expected[key] = list(expected.get(key, [])) + list(rows)
        if operand.new_model != expected:
            raise RecordError("postcondition failed: the planned {} differs from its allowed delta (only the "
                              "planned rows appended and the counters advanced by exactly the claim); nothing "
                              "written (fail-closed)".format(operand.rel))
    appended_ids = [row.get("id") for _key, rows in plan.appended.values() for row in rows]
    if appended_ids != plan.ids:
        raise RecordError("postcondition failed: the appended rows do not carry exactly the claimed ids; "
                          "nothing written (fail-closed)")


# --- the journal: startup reconciliation and the one journaled publication ---------------------------

def _journal_root(res):
    return Path(res.store_root) / JOURNAL_REL


def _reconcile_journal(ctx):
    """Reconcile an interrupted `opf record` publication BEFORE anything else, then refuse this run.
    Nothing to do when the journal root is absent, or when no lock is held and every transaction is
    terminal. A lock held by a possibly-live owner is never seized. A confirmed-dead owner's lock is
    broken only after every transaction reconciles to terminal (_journal.reconcile_and_claim_stale); an
    open transaction without a lock is recovered under a fresh lock. Recovery rolls each open transaction
    FORWARD when every poststate already verifies, else back from its preimages, so the store is exactly
    the prestate or exactly the poststate. The run then refuses (exit 2) naming each outcome: the operator
    inspects the result, and reconciles the interrupted run's lease, before re-running."""
    root_fd = ctx.root_fd
    try:
        st = _journal._lstat_contained(root_fd, JOURNAL_REL)
    except (_journal.JournalError, OSError) as exc:
        raise RecordError("cannot inspect the record journal {} ({}); fail-closed".format(JOURNAL_REL, exc))
    if st is None:
        return
    if not stat.S_ISDIR(st.st_mode):
        raise RecordError("the record journal {} is not a directory; fail-closed".format(JOURNAL_REL))
    journal_root = _journal_root(ctx.res)
    try:
        jr_fd = _journal.open_journal_root_fd(root_fd, JOURNAL_REL)
    except (_journal.JournalError, OSError) as exc:
        raise RecordError("cannot open the record journal {} ({}); fail-closed".format(JOURNAL_REL, exc))
    try:
        try:
            owner = _journal.read_lock_owner(journal_root)
            txns = _journal._journal_txn_dirs(jr_fd, journal_root)
            before = {t.name: _journal.classify_state(jr_fd, t) for t in txns}
            opened = sorted(n for n, s in before.items() if s == "open")
            if owner is None and not opened:
                return
            if owner is not None:
                if not _journal.owner_confirmed_dead(owner):
                    raise RecordError("another opf record run holds the record journal lock (pid {}); it is "
                                      "never seized. Wait for it to finish, then re-run (fail-closed)".format(
                                          owner.get("pid")))
                if _journal.reconcile_and_claim_stale(journal_root, jr_fd, root_fd, SESSION_ID) != "acquired":
                    raise RecordError("the record journal lock became live during reconciliation; it is never "
                                      "seized. Re-run once no opf record run is live (fail-closed)")
            else:
                _journal.acquire_lock(journal_root, SESSION_ID)
                for txn in txns:
                    _journal.recover(jr_fd, txn, root_fd)
            try:
                after = {t.name: _journal.classify_state(jr_fd, t) for t in txns}
            finally:
                _journal.release_lock(journal_root)
        except _journal.JournalError as exc:
            raise RecordError("the record journal {} cannot be reconciled ({}); fail-closed".format(
                JOURNAL_REL, exc))
    finally:
        _journal._close_fd_quietly(jr_fd)
    outcomes = []
    for name in opened:
        if after.get(name) == "complete":
            outcomes.append("{} rolled FORWARD (its publication is present in the working tree, uncommitted; "
                            "its render and final doctor never ran)".format(name))
        else:
            outcomes.append("{} rolled BACK to its prestate".format(name))
    if not outcomes:
        outcomes.append("a leftover journal lock of a dead run was released; every transaction was already "
                        "terminal (a completed publication is present, uncommitted, with its render and final "
                        "doctor never run)")
    lease_rel = ctx.rel(_opf_check.LEASE_NAME)
    raise RecordError("an interrupted opf record run was reconciled before this operation: {}. Nothing was "
                      "recorded by this run. Inspect the store paths (git status), run opf doctor, and "
                      "release the interrupted run's lease {} yourself if it is still present and no opf run "
                      "is live (spec 5.7), then re-run".format("; ".join(outcomes), lease_rel))


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def _publish(ctx, plan, subcommand):
    """ONE _journal.run_transaction publishes every operand, in dependency order (counters first). Each
    op is a `write` whose poststate is the planned bytes and whose `source-poststate` pins the exact bytes
    and mode the plan was made from: capture refuses (nothing opened) if the file changed since it was
    read, and apply re-verifies the captured preimage on the opened fd. Crash anywhere leaves the store
    exactly the prestate or exactly the poststate once recovered (_reconcile_journal on the next run).
    Raises RecordError; the journal lock is released on every exit except a rollback that did not
    complete, which keeps it for the next run's reconciliation."""
    root_fd = ctx.root_fd
    journal_root = _journal_root(ctx.res)
    try:
        _journal.require_containment()
        _journal.ensure_journal_dirs(root_fd, JOURNAL_REL)
        jr_fd = _journal.open_journal_root_fd(root_fd, JOURNAL_REL)
    except (_journal.JournalError, OSError) as exc:
        raise RecordError("cannot prepare the record journal {} ({}); nothing written (fail-closed)".format(
            JOURNAL_REL, exc))
    held = retain = False
    try:
        try:
            if _journal.read_lock_owner(journal_root) is not None:
                raise RecordError("the record journal lock is held; nothing written (fail-closed)")
            _journal.acquire_lock(journal_root, SESSION_ID)
        except _journal.JournalError as exc:
            raise RecordError("cannot take the record journal lock ({}); nothing written (fail-closed)".format(exc))
        held = True
        ops = []
        content = {}
        for operand in plan.operands:
            ops.append({"op": "write", "path": operand.rel,
                        "poststate": {"kind": "file", "content-sha256": _sha256(operand.new_raw)},
                        "source-poststate": {"kind": "file", "mode": operand.mode,
                                             "sha256": _sha256(operand.raw)}})
            content[operand.rel] = operand.new_raw
        txn_id = "record-{}.{}.{}".format(subcommand, os.getpid(), time.time_ns())
        header = {"unit": SESSION_ID, "kind": "record-" + subcommand}
        try:
            _journal.run_transaction(root_fd, jr_fd, journal_root, txn_id, header, ops,
                                     lambda op: content[op["path"]], SESSION_ID)
        except _journal.JournalError as exc:
            try:
                state = _journal.classify_state(jr_fd, journal_root / txn_id)
            except _journal.JournalError:
                state = "open"
            if state in ("nothing-opened", "rolled-back"):
                raise RecordError("the publication was refused and rolled back to the prestate ({}); nothing "
                                  "recorded (fail-closed)".format(exc))
            retain = True
            raise RecordError("the publication FAILED and its rollback did not complete ({}); the journal "
                              "lock is retained so the next opf record run reconciles it (fail-closed)".format(exc))
    finally:
        if held and not retain:
            try:
                _journal.release_lock(journal_root)
            except (_journal.JournalError, OSError):
                pass   # a dead owner's leftover lock is reconciled by the next run (_reconcile_journal)
        _journal._close_fd_quietly(jr_fd)


# --- render, the final doctor, recovery, and the release-then-report ordering --------------------------

class _PostPublication(RecordError):
    """A failure AFTER the journaled publication completed: the working tree changed, so the refusal
    carries the scoped recovery text."""


def _doctor(root):
    """The full offline doctor over the working tree (validate_store with fresh git observations)."""
    res = _opf_store.resolve_store(Path(os.path.abspath(root)))
    if res.status != _opf_store.RESOLVED:
        raise _PostPublication("the store at {!r} no longer resolves ({}); fail-closed".format(root, res.detail))
    obs, _notes = _opf_observe.gather(res)
    return _opf_check.validate_store(res, observations=obs)


def _final_gate(root):
    """Require a full doctor VALID over the published store before anything is offered as recorded."""
    try:
        result = _doctor(root)
    except _PostPublication:
        raise
    except Exception as exc:  # noqa: BLE001  a doctor escape must not read as a recorded change
        raise _PostPublication("the final doctor could not run ({!r}); fail-closed".format(exc))
    if result.status != _opf_store.VALID:
        lines = ["the published store is NOT doctor-VALID ({}); nothing is offered as recorded".format(
            result.status)]
        lines += ["  FINDING: {}".format(f) for f in result.findings]
        lines += ["  CANNOT-EVALUATE: {}".format(c) for c in result.cannot_evaluate]
        raise _PostPublication("\n".join(lines))


def _render(root):
    """Render the declared views over the published store. Exit 1 (views regenerated, an AUTHORED
    residual such as a changelog finding remains) is left for the final doctor to grade; exit 2 fails."""
    try:
        rres = _opf_store.resolve_store(Path(os.path.abspath(root)))
        robs, _notes = _opf_observe.gather(rres) if rres.status == _opf_store.RESOLVED else (None, [])
        rc = _opf_views.render(["--root", root, "--write"], observations=robs)
    except Exception as exc:  # noqa: BLE001  a render escape must not read as a recorded change
        raise _PostPublication("the view render after publication failed ({!r})".format(exc))
    if rc not in (0, 1):
        raise _PostPublication("the view render after publication did not complete (rc={})".format(rc))


def _recovery_text(ctx, scope):
    """Scoped recovery advice for a post-publication failure. The cleanliness gate proved HEAD holds the
    pre-operation bytes of every planned path, so restoring the listed paths together discards the
    uncommitted publication as one unit (the claimed ids were never reported). Never a whole-tree restore."""
    store_root = str(ctx.res.store_root)
    product_root = str(ctx.res.product_root if ctx.res.product_root is not None else ctx.res.store_root)
    lines = ["opf record: the uncommitted change is left for review. Resolve the findings above and run opf "
             "doctor, or discard the publication by restoring these planned paths together (counters.toml "
             "with its record file, never one without the other; confirm no opf run is live, spec 5.7):",
             "  git -C {} --literal-pathspecs restore --staged --worktree -- {}".format(
                 shlex.quote(store_root), " ".join(shlex.quote(p) for p in scope["store"]))]
    for p in scope["product"]:
        lines.append("  git -C {} --literal-pathspecs restore --staged --worktree -- {}".format(
            shlex.quote(product_root), shlex.quote(p)))
    lines.append("  inspect first: git -C {} --literal-pathspecs status --ignored=matching "
                 "--untracked-files=all -- {}".format(shlex.quote(store_root),
                                                      " ".join(shlex.quote(p) for p in scope["store"])))
    lines.append("  Never run a whole-tree restore (git restore . / git reset --hard): it would destroy "
                 "unrelated uncommitted work.")
    return "\n".join(lines)


def _release(ctx, lease):
    _opf_write_guard.release_lease(ctx.root_fd, ctx.machine_rel, lease, VERB)


def _emit_success(report):
    print("opf record {}: recorded {} and the store is doctor-VALID; the change is left UNCOMMITTED in the "
          "working tree (this verb never runs git add or commit).".format(report["subcommand"],
                                                                           ", ".join(report["ids"])))
    print(json.dumps(report, sort_keys=True))
    print("opf record: review the change, then stage and commit these paths yourself (the journal under {} "
          "is local recovery evidence, not part of the change):".format(JOURNAL_REL))
    print("  git -C {} --literal-pathspecs add -- {}".format(
        shlex.quote(report["store_root"]), " ".join(shlex.quote(p) for p in report["store_paths"])))
    for p in report["product_paths"]:
        print("  git -C {} --literal-pathspecs add -- {}".format(shlex.quote(report["product_root"]),
                                                                 shlex.quote(p)))


def _conclude(ctx, lease, report):
    """R5: release the single-writer lease FIRST, then emit the success report, so success is never
    reported over a still-held or failed-to-release lease."""
    _release(ctx, lease)
    _emit_success(report)


# --- the shared operation sequence --------------------------------------------------------------------

def _operand_rel(req, ctx):
    if req.subcommand == "worklog-append":
        return ctx.rel(_opf_check.WORKLOG_NAME)
    return ctx.rel(req.values["--type"] + _opf_check.INDEX_SUFFIX)


def _run_operation(req):
    """The one operation sequence every subcommand shares (see the module docstring). Returns normally
    only after the lease is released and the success report emitted; raises RecordError otherwise."""
    root = req.root
    try:
        res = _opf_store.resolve_store(Path(os.path.abspath(root)))
    except Exception as exc:  # noqa: BLE001  a resolver escape is cannot-evaluate, never a mutation
        raise RecordError("unexpected error resolving the store at {!r} ({!r})".format(root, exc))
    if res.status == _opf_store.NOT_ADOPTED:
        raise RecordError("no OPF store at {!r} ({}); run `opf init` first".format(root, res.detail))
    if res.status != _opf_store.RESOLVED:
        raise RecordError("cannot evaluate the store at {!r}: {}".format(root, res.detail))
    try:
        root_fd = _opf_store._open_dir_nofollow(res.store_root)
    except OSError as exc:
        raise RecordError("cannot open the store root {} ({}); fail-closed".format(res.store_root, exc))
    try:
        ctx = Context(res, root, root_fd)
        # 1. an interrupted earlier run is reconciled, and refuses this one, before anything is read.
        _reconcile_journal(ctx)
        # 2. read the models this operation plans from.
        _load_manifest(ctx)
        ctx.counters = _read_operand(root_fd, ctx.rel(_opf_check.COUNTERS_NAME))
        ctx.version = _read_operand(root_fd, ctx.rel(_opf_check.VERSION_NAME)).model
        operand = _read_operand(root_fd, _operand_rel(req, ctx))
        # 3. PRECONDITION: every file this operation rewrites is canonical (byte reproduction).
        for op in (ctx.counters, operand):
            _require_canonical(op)
        # 4. plan (the claim goes through the one allocation seam), then 5. POSTCONDITION.
        plan = _PLANNERS[req.subcommand](req, ctx, operand, _clock_now())
        _postcondition(plan, ctx.counters.rel)
        for op in plan.operands:
            op.new_raw = _emit_bytes(op.new_model)
        # 6. the planned-destination cleanliness gate, then the single-writer lease.
        scope = _opf_write_guard.plan_write_scope(ctx.machine_rel, ctx.manifest,
                                                  [op.rel for op in plan.operands], VERB)
        _opf_write_guard.check_clean(res, scope, VERB, CLEAN_SCOPE)
        product_root = res.product_root if res.product_root is not None else res.store_root
        _opf_write_guard.check_ignored(
            res.store_root, [p for p in scope["store"] if _journal._lstat_contained(root_fd, p) is None], VERB)
        _opf_write_guard.check_ignored(
            product_root, [p for p in scope["product"]
                           if not os.path.lexists(os.path.join(str(product_root), p))], VERB)
        lease = _opf_write_guard.acquire_lease(root_fd, ctx.machine_rel, VERB)
        released = False
        try:
            # Under the held lease, the operands must still hold the exact bytes planned from (the
            # journal's capture pin re-checks the same bytes under its own lock).
            for op in plan.operands:
                if _read_operand(root_fd, op.rel).raw != op.raw:
                    raise RecordError("{} changed after it was read; nothing written (fail-closed)".format(op.rel))
            # 7. ONE journaled publication; 8. render, then the final doctor.
            _publish(ctx, plan, req.subcommand)
            try:
                _render(root)
                _final_gate(root)
            except _PostPublication as exc:
                raise _PostPublication("{}\n{}".format(exc, _recovery_text(ctx, scope)))
            # 9. release the lease, THEN report (R5).
            report = {"event": "recorded", "subcommand": req.subcommand, "ids": list(plan.ids),
                      "files": [op.rel for op in plan.operands],
                      "store_root": str(res.store_root), "store_paths": list(scope["store"]),
                      "product_root": str(product_root), "product_paths": list(scope["product"])}
            released = True
            try:
                _conclude(ctx, lease, report)
            except BaseException:
                print("opf record: the store reached doctor-VALID, but the lease release failed; nothing is "
                      "offered as recorded. Confirm no opf run is live (spec 5.7) and reconcile the lease "
                      "before any further action.", file=sys.stderr)
                raise
        finally:
            if not released:
                pending = sys.exc_info()[1]
                try:
                    _release(ctx, lease)
                except Exception as rel_exc:  # noqa: BLE001  surfaced, never displaces the original failure
                    if pending is None:
                        raise
                    print("opf record: additionally, releasing the lease failed ({}); it is LEFT in place "
                          "(never seized, spec 5.7) and the failure above still governs.".format(rel_exc),
                          file=sys.stderr)
    except _opf_write_guard.WriteGuardError as exc:
        raise RecordError(str(exc))
    finally:
        _journal._close_fd_quietly(root_fd)


def cli(argv):
    """`opf record ...`: parse, run, and map the outcome to the 0/2 exit contract. Never exit 1."""
    try:
        req = parse_request(list(argv))
        _run_operation(req)
        return EXIT_OK
    except RecordError as exc:
        sub = argv[0] if argv and argv[0] in SUBCOMMANDS else ""
        print("opf record{}: refused: {}; exit 2".format(" " + sub if sub else "", exc), file=sys.stderr)
        return EXIT_MALFORMED
    except Exception as exc:  # noqa: BLE001  class-width fail-closed backstop, never a false success
        print("opf record: cannot evaluate: unexpected error ({!r}); failing closed to exit 2".format(exc),
              file=sys.stderr)
        return EXIT_MALFORMED


# --- self-test (the unit leg; the end-to-end discriminators ride check_opf_record.py) -------------------

def self_test():
    """Unit vectors over synthetic models: the argument grammar, the allocation seam (homes 1 live, homes 2
    refusing), the counters completeness proof, the create and worklog-append planners, the released-span
    check, the byte-reproduction precondition, and the allowed-delta postcondition. Judged on returned
    values and refusals, never by grepping output. Returns 0 clean, 1 on a failure, 2 on a harness error."""
    failures = []
    checked = [0]

    def check(name, cond):
        checked[0] += 1
        if not cond:
            failures.append(name)

    try:
        _self_test_units(check)
    except Exception as exc:  # noqa: BLE001  a harness fault is cannot-evaluate, never a pass
        print("opf-record self-test: harness error ({!r}); fail-closed".format(exc), file=sys.stderr)
        return EXIT_MALFORMED
    if failures:
        print("opf-record self-test: FAIL ({} checks, {} failed):".format(checked[0], len(failures)),
              file=sys.stderr)
        for f in failures:
            print("  - " + f, file=sys.stderr)
        return 1
    print("opf-record self-test: PASS ({} checks: grammar, allocation seam, completeness proof, planners, "
          "released span, precondition, postcondition)".format(checked[0]))
    return EXIT_OK


def _refuses(fn, needle=""):
    try:
        fn()
    except RecordError as exc:
        return needle in str(exc)
    return False


def _self_test_units(check):
    from types import SimpleNamespace
    now = datetime.datetime(2026, 9, 27, 1, 2, 3, tzinfo=datetime.timezone.utc)
    # -- the argument grammar ----------------------------------------------------------------------------
    req = parse_request(["create", "--type", "backlog_item", "--title", "t", "--actor", "assistant:c",
                         "--link", "relates=BI-1", "--ref", "url", "https://x.invalid/a:b", "n"])
    check("parse create", req.values["--type"] == "backlog_item" and req.actor == {"kind": "assistant", "id": "c"}
          and req.links == [{"rel": "relates", "id": "BI-1"}]
          and req.refs == [{"kind": "url", "locator": "https://x.invalid/a:b", "note": "n"}] and req.root == ".")
    for bad, needle in ((["frobnicate"], "unknown subcommand"), ([], "subcommand is required"),
                        (["transition", "BI-1", "done"], "not yet implemented"),
                        (["done-with-receipt", "BI-1"], "not yet implemented"),
                        (["create", "--type", "backlog_item", "--title", "t"], "missing required"),
                        (["create", "--type", "a", "--type", "b", "--title", "t", "--actor", "maintainer"],
                         "more than once"),
                        (["create", "--type", "backlog_item", "--title", "--actor", "maintainer"], "requires"),
                        (["create", "--type", "backlog_item", "--title", "", "--actor", "maintainer"], "requires"),
                        (["create", "--ref", "url", "x"], "requires 3"),
                        (["create", "--actor", "importer"], "not one of"),
                        (["create", "--actor", "assistant:"], "empty id"),
                        (["create", "--link", "relates"], "NAME=VALUE"),
                        (["create", "--root", "-x"], "requires"),
                        (["worklog-append", "--type", "x"], "unrecognized argument")):
        check("parse refuses {!r}".format(bad), _refuses(lambda bad=bad: parse_request(bad), needle))

    # -- the allocation seam and the completeness proof ----------------------------------------------------
    ids, high = claim_ids(1, {"BI": 4, "WL": 0}, ["BI", "BI", "WL"], known_complete=True)
    check("homes-1 claims sequential ids", ids == ["BI-5", "BI-6", "WL-1"] and high == {"BI": 6, "WL": 1})
    check("homes-2 refuses fail-closed", _refuses(lambda: claim_ids(2, {"BI": 0}, ["BI"], True), "homes-2"))
    check("an absent counter without the proof refuses",
          _refuses(lambda: claim_ids(1, {"WL": 0}, ["BI"], known_complete=False), "cannot claim"))
    check("a non-taxonomy namespace refuses", _refuses(lambda: claim_ids(1, {"ZZ": 0}, ["ZZ"], True)))

    def ctx_of(counters, version=None):
        ctx = Context(SimpleNamespace(machine_rel=".working/toml"), ".", None)
        ctx.homes = 1
        ctx.types = dict(_opf_store.BASELINE_TYPES)
        ctx.counters = Operand(".working/toml/counters.toml", b"", 0o644,
                               {"schema": 1, "counters": dict(counters)})
        ctx.version = version or {"schema": 1, "release": [], "summary": []}
        return ctx

    full = {ns: 0 for ns in _opf_store.BASELINE_TYPES.values()}
    ctx = ctx_of(full)
    check("the completeness proof holds for a complete map", _counter_state(ctx, ctx.counters.model)[1] == [])
    partial = dict(full)
    del partial["BI"]
    ctx = ctx_of(partial)
    check("a missing enabled namespace breaks the proof", _counter_state(ctx, ctx.counters.model)[1] != [])
    _self_test_planners(check, ctx_of, full, now)


def _self_test_planners(check, ctx_of, full, now):
    def plan(argv, rows=(), counters=None):
        c = ctx_of(counters or full)
        r = parse_request(argv)
        rel = c.rel(r.values["--type"] + _opf_check.INDEX_SUFFIX) if r.subcommand == "create" \
            else c.rel(_opf_check.WORKLOG_NAME)
        key = "record" if r.subcommand == "create" else "entry"
        op = Operand(rel, b"", 0o644, {"schema": 1, key: list(rows)})
        return _PLANNERS[r.subcommand](r, c, op, now), c, op

    # -- the create planner --------------------------------------------------------------------------------
    p, c, op = plan(["create", "--type", "block", "--title", "b", "--actor", "assistant", "--scope", "BI-1"])
    check("an assistant block lands active/proposed",
          op.new_model["record"][-1]["status"] == "active/proposed" and p.ids == ["BL-1"])
    check("the claim advances exactly the block namespace", c.counters.new_model["counters"]["BL"] == 1)
    p, c, op = plan(["create", "--type", "block", "--title", "b", "--actor", "maintainer", "--scope", "BI-1"])
    check("a maintainer block is the bare grant", op.new_model["record"][-1]["status"] == "active")
    p, c, op = plan(["create", "--type", "reference", "--title", "r", "--actor", "assistant",
                     "--ref", "doc", "spec", "n"])
    check("a reference is created terminal, unqualified", op.new_model["record"][-1]["status"] == "recorded")
    check("timestamps come from the clock", op.new_model["record"][-1]["created_at"] == "2026-09-27T01:02:03Z")
    for argv, needle in (
            (["create", "--type", "done", "--title", "d", "--actor", "maintainer"], "done-with-receipt"),
            (["create", "--type", "worklog", "--title", "w", "--actor", "maintainer"], "worklog-append"),
            (["create", "--type", "maintainer_action", "--title", "m", "--actor", "maintainer"], "baseline"),
            (["create", "--type", "backlog_item", "--title", "b", "--actor", "maintainer", "--field",
              "severity=x"], "not a string field"),
            (["create", "--type", "backlog_item", "--title", "b", "--actor", "maintainer", "--scope",
              "BI-1"], "block records only"),
            (["create", "--type", "reference", "--title", "r", "--actor", "maintainer"], "not valid"),
            (["create", "--type", "maintainer_decision", "--title", "m", "--actor", "assistant", "--field",
              "decision=x"], "not valid")):
        check("create refuses {}".format(needle), _refuses(lambda argv=argv: plan(argv), needle))
    check("a seated claimed id refuses", _refuses(lambda: plan(
        ["create", "--type", "backlog_item", "--title", "b", "--actor", "maintainer"],
        rows=[{"id": "BI-1"}]), "already seated"))

    # -- worklog-append and the released-span check -------------------------------------------------------
    p, c, op = plan(["worklog-append", "--kind", "added", "--summary", "s", "--actor", "assistant"])
    entry = op.new_model["entry"][-1]
    check("worklog-append claims WL-1 with no status qualifier",
          p.ids == ["WL-1"] and entry["id"] == "WL-1" and "status" not in entry)
    released = {"schema": 1, "summary": [], "release": [{
        "version": "1.0.0", "date": "2026-01-01T00:00:00Z", "worklog_span": ["WL-1", "WL-2"],
        "coverage_digest": "sha256:" + "0" * 64}]}
    check("an id inside a released span refuses", _refuses(lambda: _check_released_span(released, 2), "released"))
    check("a tail id passes the released-span check", _check_released_span(released, 3) is None)

    # -- the precondition and the postcondition -----------------------------------------------------------
    canonical = _emit_bytes({"schema": 1, "record": []})
    check("a canonical operand passes",
          _require_canonical(Operand("x", canonical, 0o644, {"schema": 1, "record": []})) is None)
    check("a comment-bearing operand refuses", _refuses(lambda: _require_canonical(Operand(
        "x", b"# note\n" + canonical, 0o644, {"schema": 1, "record": []})), "canonical"))
    base_rows = [{"id": "BI-1", "title": "kept"}]
    argv = ["create", "--type", "backlog_item", "--title", "b", "--actor", "maintainer"]
    p, c, op = plan(argv, rows=base_rows, counters=dict(full, BI=1))
    check("the genuine plan passes the postcondition", _postcondition(p, c.counters.rel) is None)
    mutations = (
        ("an extra field mutation", lambda p, c, op: op.new_model["record"][0].__setitem__("title", "x")),
        ("a dropped existing row", lambda p, c, op: op.new_model["record"].pop(0)),
        ("an over-advanced counter", lambda p, c, op: c.counters.new_model["counters"].__setitem__("BI", 3)),
        ("another counter moved", lambda p, c, op: c.counters.new_model["counters"].__setitem__("FN", 1)),
        ("a changed schema marker", lambda p, c, op: op.new_model.__setitem__("schema", 2)))
    for label, mutate in mutations:
        p, c, op = plan(argv, rows=base_rows, counters=dict(full, BI=1))
        mutate(p, c, op)
        check("the postcondition refuses {}".format(label),
              _refuses(lambda: _postcondition(p, c.counters.rel), "postcondition failed"))
    p, c, op = plan(argv)
    p.ids = ["BI-9"]
    check("the postcondition refuses rows not carrying the claimed ids",
          _refuses(lambda: _postcondition(p, c.counters.rel), "claimed ids"))


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-test"]:
        sys.exit(self_test())
    print("usage: _opf_record.py --self-test (the verb is `opf record`)", file=sys.stderr)
    sys.exit(EXIT_MALFORMED)
