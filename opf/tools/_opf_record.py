#!/usr/bin/env python3
"""opf record: the stdlib record-authoring verb (OPF-SPEC.md section 8.8). Stdlib only, fail-closed.

  opf record create --type T --title S --actor KIND[:ID] [--summary S] [--link REL=ID]...
                    [--ref KIND LOCATOR NOTE]... [--field NAME=VALUE]... [--scope ID]... [--root DIR]
  opf record transition ID STATE --actor KIND[:ID] [--reason S] [--decision S --decided-by S] [--root DIR]
  opf record done-with-receipt BI-ID --actor maintainer[:ID] [--summary S] [--root DIR]
  opf record worklog-append --kind K --summary S --actor KIND[:ID] [--detail S] [--link REL=ID]...
                    [--ref KIND LOCATOR NOTE]... [--root DIR]
  _opf_record.py --self-test         the unit leg (also registered in opf.py --self-test)

`create`, `transition`, and `done-with-receipt` each append their own worklog entry (spec 6.2: one entry
per change) in the SAME journaled transaction as the change. The entry's `detail` opens with a fixed
lifecycle line (`opf-record create ID STATUS` or `opf-record transition ID FROM -> TO`): an informational
record of the lifecycle (spec 8.3), never rejection evidence. `transition` takes the target STATE only: an
assistant or automation author landing a terminal or gated state gets `/proposed` (spec 8.4), and the verb
then writes the state the record held at that moment into the record's own `proposed_from` field (spec
8.3/8.8), validated by _opf_schema (present only on a `/proposed` status, and only a legal predecessor
state for the type). A maintainer rejection of a `/proposed` record requires --reason, restores exactly the
recorded `proposed_from` state, and removes the field; leaving the `/proposed` status by ratification
removes it too. A proposed record that carries no `proposed_from` was proposed outside `transition`
(`create` itself lands a gated initial state at `/proposed` with no predecessor, and a canonical hand
edit can propose too, so the absence establishes no provenance): its rejection target cannot be verified
here, so the rejection refuses, never inventing a predecessor.
A pending_decision's `open -> decided`, landing bare or `/proposed`, writes its resolution bundle (spec
8.5) in the same act: --decision and --decided-by are required there and refused on every other
transition, `decided_by` is the operator's value (never inferred from --actor, since the recorder of an
answer is often not its decider), and `decided_at` is the operation's clock value. A ratification keeps
the bundle; a rejection of `decided/proposed` removes it with `proposed_from` (an open decision carries
none of it).
`done-with-receipt` is maintainer-only (any other actor is refused before the store is touched): it moves an
`active` or `done/proposed` backlog item to `done` and mints the one-to-one `done` receipt, linked
`receipt_of`, in the same transaction (spec 8.5). `transition` never lands a backlog item at unqualified
`done`, so no ratified item can exist without its receipt.

Every subcommand runs ONE shared operation sequence (_run_operation), the `opf upgrade` shell applied to
record authoring (spec 9.2 is the precedent; spec 8.8 is normative):
  1. resolve the store; reconcile any interrupted `opf record` journal FIRST. Recovery writes the store,
     so it runs only under the single-writer lease (a held lease refuses before any recovery write and is
     never seized) and only when every operand still holds a state the journal explains (an intervening
     edit is surfaced and refused, never overwritten); a reconciled interruption refuses this run, exit 2,
     so the operator inspects it before anything new is written;
  2. read the manifest; a `create --type` that is not an enabled baseline type, or a `transition` id
     outside every enabled transitionable type's namespace, refuses here, before any operand path is built
     from it; read counters.toml, worklog.toml, and the operand index (the type's `<type>.index.toml`, plus
     done.index.toml for a receipt), plus version.toml for the released-span boundary;
  3. PRECONDITION: re-emitting each operand's UNCHANGED parsed model reproduces its on-disk bytes exactly
     (a file carrying comments or non-canonical serialization refuses, bytes untouched; a hand edit or
     merge that leaves canonical bytes is NOT detectable by this check);
  4. plan the new models: claim the ids through the ONE allocation seam (claim_ids); require the
     CURRENT row a transition or done-with-receipt acts on to be schema-valid, its proposed_from
     included, BEFORE it is trusted or overwritten (_require_valid_current); compose and validate each
     record through the _opf_schema primitives (validate_transition for a status change); refuse an
     append into a released span;
  5. POSTCONDITION: each emitted document, reparsed, equals its prior bytes, reparsed, plus exactly the
     allowed delta, value for value and TYPE for type (the strict _opf_emit._model_equal comparison, so a
     True or 1.0 never reads as 1): the new rows appended, the counters advanced by exactly the claim,
     and for a transition the one named record's `status` and `updated_at` change plus its `proposed_from`
     write or removal and a pending_decision's resolution bundle write or removal. The delta is
     derived INDEPENDENTLY of the planner's rows, from a pre-planning copy of the request, the allocation
     result, the clock value, the planned-from bytes, and the schema rules;
  6. the planned-destination cleanliness gate and the single-writer lease (the shared _opf_write_guard
     shell, moved from opf.py), held across publication, render, and the final doctor;
  7. ONE _journal.run_transaction publishes every operand (counters first), rooted at
     .aiqt/record/journal, each operand pinned to the exact bytes it was planned from;
  8. render the declared views (--write), then require a full doctor VALID (a status change may leave
     only doctor's cannot-evaluate for exactly that record and from/to pair, pending until commit, and
     never a finding: _snapshot_pending);
  9. release the lease, THEN report: the ids, the files, and that the change is left UNCOMMITTED in the
     working tree (this verb never runs git add or commit).

The allocation seam: homes 1 claims each id by advancing counters.toml as an operand INSIDE the journaled
transaction, under the held lease, and reports ids only after the COMPLETE frame, so a rollback restores
the counters without ever un-publishing an observed id. Homes 2 (the irrevocable
journals/<kind>/allocations reservation, spec 4.2) is not active in this build, and the seam refuses it
fail-closed rather than claim through the homes-1 path.

Exit contract: 0 recorded and doctor-VALID, or carrying only that pending cannot-evaluate (uncommitted);
2 refusal or cannot-evaluate, with recovery text where the working tree changed. Exit 1 is not used (it
is doctor's own finding code).

DISCLOSED RESIDUALS: there is no pre-doctor, so a store invalid in a way the preconditions do not inspect
fails only at the final doctor, after publication (the change is then left for review with scoped
recovery text, as `opf upgrade` does); a completed transaction's journal directory is retained under
.aiqt/record/journal as local recovery evidence; the lease is not made observable at a sync target (no
sync runtime in this build, so the guarantee is single-host single-writer); two branches allocating from
the same committed counters can both claim an id, which spec 5.7's store-path merge policy and doctor's
C-ID-SPACE check (not this verb) catch; the byte-reproduction precondition proves serialization only, so
a hand edit or hand merge that leaves canonical bytes passes it and spec 5.7's integration-base rule stays
a separate requirement; recovery proves each operand's state under the lease, but the journal engine's
restore then rewrites without re-checking, so an edit landing in that window, or one that leaves an exact
byte prefix of the journaled preimage or planned bytes (read as a torn write), is not detected. A
transition changes `status` and `updated_at` only, plus `proposed_from` (written when it lands a
`/proposed` status, removed when it leaves one) and a pending_decision's resolution bundle (written by
`open -> decided`, removed by the rejection of `decided/proposed`), so a target state that requires
further fields (a `sent` contribution's delivery bundle) refuses at validate_record; posting a new
handoff does not supersede the previous one in the same act. The
pre-proposal state a rejection restores is read from the record's own `proposed_from` field, which this
verb wrote in the same journaled transaction that landed the `/proposed` status: a proposed record without
the field (proposed outside `transition`, which includes a record `create` landed at a `/proposed` initial
state, so the absence establishes no provenance) cannot be rejected here (refused, a predecessor is never
inferred or invented), and the worklog lifecycle line is INFORMATIONAL, never evidence. The
committed-history corroboration this verb once ran was RETIRED (UNATTENDED DECISION
PR2-REJECTION-PREMISE-REVIEW): it trusted the repository's own committed history, and three QA rounds
each found a way, ending at routine merge shapes of a reject-and-re-propose cycle, to make that history
corroborate a forged pre-proposal state. `proposed_from` is an ordinary canonical record field, and a
transition or done-with-receipt refuses to trust or overwrite a CURRENT row the schema grades invalid
(_require_valid_current), so a canonical hand edit of the record, this field included, is NOT detected
only while it keeps the row schema-valid (a legal-predecessor swap), exactly the byte-reproduction
residual above (spec 5.7's integration-base merge policy remains the control). A transition refuses
when the clock has not passed the record's recorded timestamps.
"""
import base64
import binascii
import copy
import datetime
import hashlib
import json
import os
import re
import shlex
import stat
import sys
import time
import tomllib
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
    "transition": ("--root", "--actor", "--reason", "--decision", "--decided-by"),
    "done-with-receipt": ("--root", "--actor", "--summary"),
    "worklog-append": ("--root", "--kind", "--summary", "--actor", "--detail", "--link", "--ref"),
}
_REQUIRED = {"create": ("--type", "--title", "--actor"), "transition": ("--actor",),
             "done-with-receipt": ("--actor",), "worklog-append": ("--kind", "--summary", "--actor")}
_REPEATED = frozenset(("--link", "--ref", "--field", "--scope"))
# The leading positional operands, named for the usage refusal.
_POSITIONALS = {"transition": ("ID", "STATE"), "done-with-receipt": ("BI-ID",)}
_ID_RE = re.compile(r"^([A-Z]{2})-([1-9][0-9]*)\Z")
BACKLOG = "backlog_item"
# The first line of every auto-appended worklog entry's `detail`: a fixed grammar recording the change,
# so a record's lifecycle (which the envelope does not carry, spec 8.3) is recoverable from the worklog.
# `create` writes `opf-record create ID STATUS`; `transition` and `done-with-receipt` write
# `opf-record transition ID FROM -> TO`.
_LIFECYCLE_RE = re.compile(r"^opf-record (create|transition) ([A-Z]{2}-[1-9][0-9]*) (?:(\S+) -> )?(\S+)\Z")
# The record field a proposing transition writes its pre-proposal state into (spec 8.3/8.8): what a
# maintainer rejection restores, and what leaving the `/proposed` status removes. Schema-validated by
# _opf_schema._validate_proposed_from (legal only on a `/proposed` status, a legal predecessor state).
PROPOSED_FROM = "proposed_from"
PENDING_DECISION = "pending_decision"
# The resolution bundle a pending_decision's `open -> decided` writes (spec 8.5, 8.8): all-or-none, all
# three keys on `decided`, none on `open` or `withdrawn`.
DECISION_BUNDLE = ("decision", "decided_at", "decided_by")
# The single-writer journal lock of one publication carries `opf-record.<token>` as its session, and the
# transaction directory it opens ends `.<token>`: a leftover lock names its own transaction by that token.
_TOKEN_RE = re.compile(r"^[0-9a-f]{32}\Z")


class RecordError(Exception):
    """A fail-closed refusal or cannot-evaluate carrying the operator-facing reason (mapped to exit 2)."""


class Request:
    """One parsed `opf record` invocation."""
    __slots__ = ("subcommand", "root", "values", "links", "refs", "fields", "scopes", "actor", "positionals")

    def __init__(self, subcommand):
        self.subcommand = subcommand
        self.positionals = []
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


def _require_maintainer(actor):
    """done-with-receipt is maintainer-only: the ratified `done` and its receipt are a maintainer act."""
    if actor["kind"] != "maintainer":
        raise RecordError("done-with-receipt is maintainer-only (spec 8.5); an {} reaching done uses opf record "
                          "transition, which lands done/proposed with no receipt until a maintainer "
                          "ratifies".format(actor["kind"]))


def _require_decision_pair(seen):
    """--decision and --decided-by come together (the resolution bundle, spec 8.5), never one without the
    other: a usage refusal, before any store is touched."""
    if ("--decision" in seen) != ("--decided-by" in seen):
        raise RecordError("transition: --decision and --decided-by are given together (the resolution bundle, "
                          "spec 8.5), never one without the other")


def parse_request(argv):
    """Parse `opf record <subcommand> ...` into a Request, or raise RecordError (a usage refusal, exit 2).
    Every option value must be present, non-empty, and must not start with `--` (so a swallowed next
    option is refused rather than read as a value); a single-valued option given twice refuses. The
    positional operands of `transition` and `done-with-receipt` come first and are grammar-checked here,
    and a non-maintainer `done-with-receipt` refuses here, all before any store is touched."""
    if not argv:
        raise RecordError("a subcommand is required: one of {}".format(", ".join(SUBCOMMANDS)))
    sub = argv[0]
    if sub not in SUBCOMMANDS:
        raise RecordError("unknown subcommand {!r}; known subcommands: {}".format(sub, ", ".join(SUBCOMMANDS)))
    req = Request(sub)
    allowed = _OPTIONS[sub]
    seen = set()
    names = _POSITIONALS.get(sub, ())
    req.positionals = list(argv[1:1 + len(names)])
    if len(req.positionals) != len(names) or any(v == "" or v.startswith("-") for v in req.positionals):
        raise RecordError("{}: requires the operand(s) {} before any option".format(sub, " ".join(names)))
    if names and not _ID_RE.match(req.positionals[0]):
        raise RecordError("{}: {!r} is not a record id (<NS>-<n>, spec 8.2)".format(sub, req.positionals[0]))
    i = 1 + len(names)
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
    if sub == "transition" and "/" in req.positionals[1]:
        raise RecordError("transition: give the target STATE, not {!r}; the '/proposed' qualifier follows from "
                          "the actor (spec 8.4)".format(req.positionals[1]))
    if sub == "transition":
        _require_decision_pair(seen)
    if sub == "done-with-receipt":
        _require_maintainer(req.actor)
        if _ID_RE.match(req.positionals[0]).group(1) != _opf_store.BASELINE_TYPES[BACKLOG]:
            raise RecordError("done-with-receipt: {} is not a backlog item id".format(req.positionals[0]))
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
    UNCHANGED parsed model must reproduce the on-disk bytes exactly, proving the file is canonically
    serialized and comment-free, so a whole-document regeneration loses nothing. A file carrying comments
    or non-canonical serialization refuses and is left untouched. It proves SERIALIZATION only: a hand edit
    or hand merge that leaves canonical bytes is not detectable here, so spec 5.7's integration-base merge
    policy remains a separate requirement this check does not enforce."""
    if _emit_bytes(operand.model) != operand.raw:
        raise RecordError("{} is not in canonical new-document form (it carries comments or non-canonical "
                          "serialization); refusing a whole-document rewrite that could lose content. "
                          "Restore it from the integration base and redo the operation there (spec 5.7); "
                          "nothing written (fail-closed)".format(operand.rel))


# --- the store context ------------------------------------------------------------------------------

class Context:
    """The resolved store and the models this operation plans from."""
    __slots__ = ("res", "root", "root_fd", "machine_rel", "manifest", "homes", "types", "vendors",
                 "counters", "version", "worklog", "done_index")

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
        self.worklog = None        # the worklog operand: every subcommand appends one entry
        self.done_index = None     # the done index operand, read by done-with-receipt only

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
            and type(model.get("schema")) is int    # exact int: True == 1 and 1.0 == 1 must not pass
            and model.get("schema") == _opf_schema.SUPPORTED_SCHEMA
            and isinstance(model.get("record", []), list)):
        raise RecordError("{} is not a schema-1 {{schema, record}} index; fail-closed".format(operand.rel))
    return model.get("record", [])


class Plan:
    """The planned publication: the operands in journal order (counters first, worklog last), the ids
    claimed, and for a status change its (id, from, to). It carries no copy of the appended or changed
    rows: the postcondition derives the allowed delta on its own (_expected_delta) and checks the emitted
    operands, and this triple, against that."""
    __slots__ = ("operands", "ids", "transition")

    def __init__(self, operands, ids, transition=None):
        self.operands = operands      # [Operand], counters first
        self.ids = ids                # the claimed ids, in claim order
        self.transition = transition  # (id, from status, to status) of a status change, else None


def _require_unseated(rows, rid, rel):
    if any(isinstance(r, dict) and r.get("id") == rid for r in rows):
        raise RecordError("the claimed id {} is already seated in {} (counters.toml is behind the store; a "
                          "regressed counter is never a licence to allocate); fail-closed".format(rid, rel))


def _worklog_entries(ctx):
    model = ctx.worklog.model
    if not (isinstance(model, dict) and set(model) <= _opf_release.WORKLOG_TOP_KEYS
            and type(model.get("schema")) is int    # exact int: True == 1 and 1.0 == 1 must not pass
            and model.get("schema") == _opf_schema.SUPPORTED_SCHEMA
            and isinstance(model.get("entry", []), list)):
        raise RecordError("{} is not a schema-1 worklog ledger; fail-closed".format(ctx.worklog.rel))
    return model.get("entry", [])


def _lifecycle_entry(ctx, wid, now, actor, kind, summary, detail, links):
    """The worklog entry a mutating subcommand appends for its own change (spec 6.2: one entry per change),
    validated as a worklog record and refused if its id would land inside a released span."""
    _require_unseated(_worklog_entries(ctx), wid, ctx.worklog.rel)
    _check_released_span(ctx.version, int(wid.split("-", 1)[1]))
    entry = {"id": wid, "date": _rfc3339(now), "actor": dict(actor), "kind": kind, "summary": summary,
             "detail": detail, "links": [dict(link) for link in links]}
    return _validated(entry, "worklog", ctx)


def _append_worklog(ctx, entry):
    ctx.worklog.new_model = copy.deepcopy(ctx.worklog.model)
    ctx.worklog.new_model["entry"] = list(ctx.worklog.new_model.get("entry", [])) + [entry]


def _require_valid_current(row, rtype, ctx, rid, rel):
    """The CURRENT row a transition or done-with-receipt acts on must itself be schema-valid BEFORE the
    verb trusts or overwrites it: a rejection restores the row's recorded `proposed_from`, a proposing
    transition writes the field, and leaving a proposal removes it, so acting on a schema-invalid row (a
    `proposed_from` naming an illegal predecessor, or a stray one on an unqualified status, each a doctor
    finding) would launder a doctor-INVALID store into a VALID one and erase the evidence (QA4 F1).
    Refused with every byte untouched; the operator repairs the record first (spec 5.7)."""
    rv = _opf_schema.validate_record(row, expected_type=rtype, registered_vendors=ctx.vendors)
    if rv.status != _opf_store.VALID:
        raise RecordError("{} in {} is not schema-valid as it stands ({}); a transition never trusts or "
                          "overwrites an invalid record (repair it under opf doctor first); nothing "
                          "written (fail-closed)".format(rid, rel, "; ".join(rv.findings)))


def _recorded_pre_proposal(ctx, row, rid, status):
    """The pre-proposal state a maintainer rejection restores: the record's OWN `proposed_from` field
    (spec 8.3/8.8), which this verb wrote in the same journaled transaction that landed the `/proposed`
    `status`, which _require_valid_current has just proved schema-valid on the current row (present only
    on a `/proposed` status, a legal predecessor state for the type), and which the rejection or
    ratification that leaves the proposal removes again. None when the record does not carry a string
    value there: it was proposed outside `transition` (a record `create` landed at a `/proposed` initial
    state, or a canonical hand edit), which establishes no provenance, so the rejection target cannot be
    verified and the caller refuses, never inferring or inventing a predecessor. The field is an ordinary
    canonical record field, so a canonical hand edit of it that keeps the row schema-valid is not
    detectable (the residual every field shares, see the module docstring); the worklog lifecycle line is
    informational, never evidence, and is deliberately not read here. `ctx` rides along as the seam the
    record gate's red-on-revert flip substitutes (a worklog-trusting reader) to prove this reader never
    consults worklog text."""
    value = row.get(PROPOSED_FROM)
    return value if isinstance(value, str) else None


def _parse_ts(value):
    try:
        return datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None


def _require_later(row, ts):
    """A transition's updated_at must be strictly later than every timestamp the record already carries:
    an equal updated_at would make the changed record read as its own creation snapshot (spec 8.3, 8.4)."""
    new = _parse_ts(ts)
    for key in ("created_at", "updated_at"):
        if key not in row:
            continue
        old = _parse_ts(row[key])
        if old is None:
            raise RecordError("{} {} {!r} is not an RFC 3339 timestamp; fail-closed".format(
                row.get("id"), key, row[key]))
        if new <= old:
            raise RecordError("the clock ({}) is not later than {} {} {}; a transition's updated_at must follow "
                              "it (spec 8.3). Nothing written; retry once the clock has advanced".format(
                                  ts, row.get("id"), key, row[key]))


def _locate(operand, rid):
    """The one row of `operand` carrying `rid`, or a refusal (absent, or seated more than once)."""
    rows = _index_rows(operand)
    hits = [r for r in rows if isinstance(r, dict) and r.get("id") == rid]
    if not hits:
        raise RecordError("{} is not a record in {}; fail-closed".format(rid, operand.rel))
    if len(hits) > 1:
        raise RecordError("{} is seated {} times in {} (a duplicate id, spec 8.2); fail-closed".format(
            rid, len(hits), operand.rel))
    return hits[0]


def _change_row(operand, rid, fields, drop=()):
    operand.new_model = copy.deepcopy(operand.model)
    for row in operand.new_model["record"]:
        if isinstance(row, dict) and row.get("id") == rid:
            for key in drop:
                row.pop(key, None)
            row.update(fields)


def _require_create_type(rtype, ctx):
    """`create` mints only an enabled baseline type, never one another subcommand owns. Returns its
    TypeSpec. _run_operation calls this (through _check_request) right after the manifest and BEFORE any
    operand read, because the index path is built from --type: an unknown or path-like --type gets this
    refusal, not a read failure. The planner re-checks through the same function."""
    if rtype in _OTHER_SUBCOMMAND:
        raise RecordError("create does not mint {} records; use opf record {} (fail-closed)".format(
            rtype, _OTHER_SUBCOMMAND[rtype]))
    spec = _opf_schema.BASELINE_SPECS.get(rtype)
    if spec is None or rtype not in ctx.types:
        raise RecordError("create supports the enabled baseline record types only, not {!r} (module-tier "
                          "record schemas have not shipped); fail-closed".format(rtype))
    return spec


def _check_request(req, ctx):
    """The request checks that need the manifest but must precede every operand read: the index path is
    built from `create --type` or from a `transition` id's namespace."""
    if req.subcommand == "create":
        _require_create_type(req.values["--type"], ctx)
    elif req.subcommand == "transition":
        _operand_rel(req, ctx)


def _plan_create(req, ctx, operand, now):
    """`create`: one new record of a baseline type in its initial state. An assistant or automation
    author entering a gated initial state lands `/proposed` (spec 8.4); a created-terminal factual type
    (reference, autonomous_decision, maintainer_decision) carries no qualifier. The id is claimed through
    the seam; the record is validated by validate_record before anything is planned further."""
    rtype = req.values["--type"]
    spec = _require_create_type(rtype, ctx)
    rows = _index_rows(operand)
    rid, wid = _claim(ctx, [spec.namespace, _opf_release.WL_NAMESPACE])
    _require_unseated(rows, rid, operand.rel)
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
    entry = _lifecycle_entry(ctx, wid, now, req.actor, "added",
                             "created {} ({}) at {}".format(rid, rtype, record["status"]),
                             "opf-record create {} {}".format(rid, record["status"]),
                             [{"rel": "relates", "id": rid}])
    operand.new_model = copy.deepcopy(operand.model)
    operand.new_model["record"] = list(operand.new_model.get("record", [])) + [record]
    _append_worklog(ctx, entry)
    return Plan([ctx.counters, operand, ctx.worklog], [rid, wid])


def _plan_worklog_append(req, ctx, operand, now):
    """`worklog-append`: one entry appended to the UNRELEASED tail of worklog.toml (spec 6.2). A worklog
    entry records a fact, so it takes no `/proposed` qualifier whatever the actor (its status is fixed
    `recorded`, spec 8.3/8.4). An id that would fall inside a released span refuses. A --detail may not
    open with the lifecycle grammar, which is reserved for this verb's own entries."""
    entries = _worklog_entries(ctx)
    ns = _opf_release.WL_NAMESPACE
    (wid,) = _claim(ctx, [ns])
    _require_unseated(entries, wid, operand.rel)
    _check_released_span(ctx.version, int(wid.split("-", 1)[1]))
    entry = {"id": wid, "date": _rfc3339(now), "actor": dict(req.actor), "kind": req.values["--kind"]}
    _envelope_extras(req, entry)
    if "--detail" in req.values:
        if _LIFECYCLE_RE.match(req.values["--detail"].split("\n", 1)[0]):
            raise RecordError("worklog-append --detail may not open with an opf-record lifecycle line; that "
                              "grammar is reserved for the entries opf record writes for its own changes (the "
                              "informational lifecycle log); fail-closed")
        entry["detail"] = req.values["--detail"]
    _validated(entry, "worklog", ctx)
    _append_worklog(ctx, entry)
    return Plan([ctx.counters, ctx.worklog], [wid])


def _derived_status(kind, spec, cur_state, target):
    """The status a transition to `target` lands: `/proposed` when an assistant or automation enters a
    terminal or gated state (spec 8.4); otherwise the bare state. A move to the current state (a
    ratification attempt) stays bare, so validate_transition judges it as ratification."""
    if kind in _opf_schema.PROPOSER_KINDS and target != cur_state and (target in spec.terminal
                                                                       or target in spec.gated):
        return target + "/proposed"
    return target


def _checked_reason(req):
    """The reason validate_transition judges a rejection by: the operator's --reason, verbatim, or None
    (a rejection's reason is never supplied here). The worklog entry records --reason from the request
    itself, so this seam feeds only the check."""
    return req.values.get("--reason")


def _require_receipt_path(rtype, to_status):
    """A backlog item lands at unqualified `done` only through done-with-receipt, which mints its receipt in
    the same act (spec 8.5), so `transition` never leaves a ratified item without its receipt."""
    if rtype == BACKLOG and to_status == "done":
        raise RecordError("a backlog item lands at unqualified done only through opf record done-with-receipt, "
                          "which mints its one-to-one done receipt in the same act (spec 8.5); fail-closed")


def _decides(rtype, cur_state, target):
    """The one transition that writes the resolution bundle: a pending_decision's `open -> decided`,
    landing bare (a maintainer) or `/proposed` (an assistant or automation filing an answer)."""
    return rtype == PENDING_DECISION and cur_state == "open" and target == "decided"


def _require_decision_options(req, rid, rtype, cur_state, target):
    """--decision and --decided-by (given together, checked by the parser) are required on a decide and
    refused on every other transition, the ratification of `decided/proposed` included (it keeps the
    bundle its proposal wrote), before anything is planned."""
    given = "--decision" in req.values
    if _decides(rtype, cur_state, target) and not given:
        raise RecordError("{} open -> decided writes the resolution bundle (decision, decided_at, decided_by; "
                          "spec 8.5), so it requires --decision and --decided-by (decided_by names the "
                          "decider and is never inferred from --actor); fail-closed".format(rid))
    if given and not _decides(rtype, cur_state, target):
        raise RecordError("--decision and --decided-by apply only to a pending_decision's open -> decided, not "
                          "{} {} -> {} (a ratification keeps the bundle its proposal wrote); "
                          "fail-closed".format(rid, cur_state, target))


def _resolution_bundle(req, ts):
    """The bundle a decide writes: --decision and --decided-by verbatim, and the operation's clock value as
    decided_at. decided_by is never inferred from --actor: the recorder (an assistant filing an answer)
    is often not the decider."""
    return {"decision": req.values["--decision"], "decided_at": ts, "decided_by": req.values["--decided-by"]}


def _proposal_keys(rtype, cur_state, rejection):
    """The keys leaving a `/proposed` status removes: always proposed_from, and on a rejection also the
    bundle its proposing transition wrote (a pending_decision's `decided/proposed` back to `open`, where
    the bundle is forbidden, spec 8.5). A ratification keeps the bundle."""
    if rejection and rtype == PENDING_DECISION and cur_state == "decided":
        return (PROPOSED_FROM,) + DECISION_BUNDLE
    return (PROPOSED_FROM,)


def _plan_transition(req, ctx, operand, now):
    """`transition ID STATE`: one status change checked by validate_transition (spec 8.4, 8.5). The target
    status is derived from the actor: an assistant or automation landing a terminal or gated state gets
    `/proposed`, and the record's pre-proposal state is then written into its own `proposed_from` field in
    the same act (spec 8.8). The current row must be schema-valid before it is trusted or overwritten
    (_require_valid_current). Leaving a `/proposed` status is maintainer-only: a rejection (--reason
    required) must return to exactly the recorded `proposed_from` state, and rejection and ratification
    each remove the field. A proposed record that carries no `proposed_from` (proposed outside
    `transition`, a record created at a `/proposed` initial state included) cannot be rejected here, and
    no predecessor is inferred or invented; the worklog lifecycle line is informational, never evidence. A
    backlog item never lands at unqualified `done` here (done-with-receipt mints the receipt in the same
    act). A pending_decision's `open -> decided` also writes its resolution bundle (_resolution_bundle),
    and the rejection of `decided/proposed` removes it (_proposal_keys). The change is `status`,
    `updated_at`, the `proposed_from` write or removal, and that bundle write or removal on that one
    record, plus its own worklog entry."""
    rid, target = req.positionals
    row = _locate(operand, rid)
    rtype = row.get("type")
    spec = _opf_schema.BASELINE_SPECS.get(rtype) if isinstance(rtype, str) else None
    if spec is None or ctx.types.get(rtype) != _ID_RE.match(rid).group(1):
        raise RecordError("{} in {} does not carry its index's baseline type; fail-closed".format(rid, operand.rel))
    _require_valid_current(row, rtype, ctx, rid, operand.rel)
    current = row.get("status")
    parsed, err = _opf_schema.parse_status(current, spec)
    if parsed is None:
        raise RecordError("{} status {!r} cannot be parsed ({}); fail-closed".format(rid, current, err))
    cur_state, cur_qual = parsed
    _require_decision_options(req, rid, rtype, cur_state, target)
    kind = req.actor["kind"]
    to_status = _derived_status(kind, spec, cur_state, target)
    _require_receipt_path(rtype, to_status)
    rejection = cur_qual == "proposed" and target != cur_state
    pre = _recorded_pre_proposal(ctx, row, rid, current) if rejection else None
    tc = _opf_schema.validate_transition(rtype, current, to_status, kind, pre_proposal_state=pre,
                                         reason=_checked_reason(req))
    if tc.status != _opf_store.VALID:
        note = ""
        if rejection and pre is None:
            note = ("; {} carries no pre-proposal state (no {} field: it was proposed outside opf record "
                    "transition, which includes a record created at a '/proposed' initial state, so "
                    "the absence establishes no provenance) and the rejection target cannot be verified; "
                    "no predecessor is inferred or invented, and the worklog lifecycle line is "
                    "informational, never evidence".format(rid, PROPOSED_FROM))
        raise RecordError("{} {} -> {} by a {} is {}: {}{} (fail-closed)".format(
            rid, current, to_status, kind, tc.status, "; ".join(tc.findings), note))
    ts = _rfc3339(now)
    _require_later(row, ts)
    fields = {"status": to_status, "updated_at": ts}
    if _decides(rtype, cur_state, target):
        fields.update(_resolution_bundle(req, ts))
    # Landing a `/proposed` status records the pre-proposal state in the record itself; leaving one (a
    # rejection or a ratification) removes it, and a rejection also removes the bundle its proposal
    # wrote. validate_transition proved the two never coincide (a `/proposed` record only ever moves to
    # an unqualified status).
    if to_status.endswith("/proposed"):
        fields[PROPOSED_FROM] = current
    drop = _proposal_keys(rtype, cur_state, rejection) if cur_qual == "proposed" else ()
    new_row = {key: value for key, value in row.items() if key not in drop}
    new_row.update(fields)
    _validated(new_row, rtype, ctx)
    (wid,) = _claim(ctx, [_opf_release.WL_NAMESPACE])
    detail = "opf-record transition {} {} -> {}".format(rid, current, to_status)
    if "--reason" in req.values:
        detail += "\nreason: " + req.values["--reason"]
    verb = "rejected" if rejection else "ratified" if cur_qual == "proposed" else "transitioned"
    entry = _lifecycle_entry(ctx, wid, now, req.actor, "changed",
                             "{} {} from {} to {}".format(verb, rid, current, to_status), detail,
                             [{"rel": "relates", "id": rid}])
    _change_row(operand, rid, fields, drop=drop)
    _append_worklog(ctx, entry)
    return Plan([ctx.counters, operand, ctx.worklog], [wid], transition=(rid, current, to_status))


def _plan_done_with_receipt(req, ctx, operand, now):
    """`done-with-receipt BI-ID` (maintainer-only, checked by the parser and again here): `active -> done`,
    or the ratification `done/proposed -> done`, AND the one-to-one `done` receipt linked `receipt_of`,
    both in this one publication (spec 8.5). A backlog item that already has a receipt refuses."""
    (rid,) = req.positionals
    _require_maintainer(req.actor)
    row = _locate(operand, rid)
    current = row.get("status")
    if row.get("type") != BACKLOG or current not in ("active", "done/proposed"):
        raise RecordError("done-with-receipt moves an active or done/proposed backlog item to done; {} is {!r} "
                          "(fail-closed)".format(rid, current))
    _require_valid_current(row, BACKLOG, ctx, rid, operand.rel)
    tc = _opf_schema.validate_transition(BACKLOG, current, "done", req.actor["kind"])
    if tc.status != _opf_store.VALID:
        raise RecordError("{} {} -> done is {}: {} (fail-closed)".format(rid, current, tc.status,
                                                                          "; ".join(tc.findings)))
    done_rows = _index_rows(ctx.done_index)
    held = [r.get("id") for r in done_rows if isinstance(r, dict) and any(
        isinstance(link, dict) and link.get("rel") == "receipt_of" and link.get("id") == rid
        for link in (r.get("links") if isinstance(r.get("links"), list) else []))]
    if held:
        raise RecordError("{} already has a done receipt ({}); the receipt is one-to-one (spec 8.5), "
                          "fail-closed".format(rid, ", ".join(map(str, held))))
    ts = _rfc3339(now)
    _require_later(row, ts)
    fields = {"status": "done", "updated_at": ts}
    # Ratifying done/proposed leaves the `/proposed` status, so the recorded pre-proposal state is
    # removed with it (spec 8.8); from active there is nothing to remove.
    drop = (PROPOSED_FROM,) if current == "done/proposed" else ()
    new_row = {key: value for key, value in row.items() if key not in drop}
    new_row.update(fields)
    _validated(new_row, BACKLOG, ctx)
    dn_ns = _opf_store.BASELINE_TYPES["done"]
    did, wid = _claim(ctx, [dn_ns, _opf_release.WL_NAMESPACE])
    _require_unseated(done_rows, did, ctx.done_index.rel)
    receipt = {"id": did, "type": "done", "status": "recorded", "title": row.get("title"), "created_at": ts,
               "updated_at": ts, "actor": dict(req.actor), "links": [{"rel": "receipt_of", "id": rid}]}
    if "--summary" in req.values:
        receipt["summary"] = req.values["--summary"]
    _validated(receipt, "done", ctx)
    verb = "ratified" if current == "done/proposed" else "completed"
    entry = _lifecycle_entry(ctx, wid, now, req.actor, "changed",
                             "{} {} from {} to done with receipt {}".format(verb, rid, current, did),
                             "opf-record transition {} {} -> done\nreceipt: {}".format(rid, current, did),
                             [{"rel": "relates", "id": rid}, {"rel": "relates", "id": did}])
    _change_row(operand, rid, fields, drop=drop)
    ctx.done_index.new_model = copy.deepcopy(ctx.done_index.model)
    ctx.done_index.new_model["record"] = list(ctx.done_index.new_model.get("record", [])) + [receipt]
    _append_worklog(ctx, entry)
    return Plan([ctx.counters, operand, ctx.done_index, ctx.worklog], [did, wid],
                transition=(rid, current, "done"))


_PLANNERS = {"create": _plan_create, "transition": _plan_transition,
             "done-with-receipt": _plan_done_with_receipt, "worklog-append": _plan_worklog_append}


def _postcondition_failed(what):
    return RecordError("postcondition failed: {}; nothing written (fail-closed)".format(what))


def _reparse(raw, rel):
    """A fresh model parsed from bytes: it shares no object with any model the planner built."""
    try:
        return tomllib.loads(raw.decode("utf-8"))
    except (AttributeError, UnicodeDecodeError, tomllib.TOMLDecodeError, RecursionError) as exc:
        raise _postcondition_failed("{} cannot be reparsed ({})".format(rel, exc))


def _expected_rels(req, ctx):
    """The operand set the request implies, in journal order: counters first, the worklog last, and between
    them the record index a create or a status change rewrites (plus done.index.toml for a receipt)."""
    worklog = ctx.rel(_opf_check.WORKLOG_NAME)
    if req.subcommand == "worklog-append":
        return [ctx.counters.rel, worklog]
    if req.subcommand == "done-with-receipt":
        return [ctx.counters.rel, ctx.rel(BACKLOG + _opf_check.INDEX_SUFFIX),
                ctx.rel("done" + _opf_check.INDEX_SUFFIX), worklog]
    return [ctx.counters.rel, _operand_rel(req, ctx), worklog]


def _prior_row(document, rid, rel):
    """The one row of a reparsed prior index carrying `rid` (the oracle's own lookup)."""
    rows = document.get("record") if isinstance(document, dict) else None
    hits = [r for r in rows if isinstance(r, dict) and r.get("id") == rid] if isinstance(rows, list) else []
    if len(hits) != 1:
        raise _postcondition_failed("{} is not seated exactly once in the prior {}".format(rid, rel))
    return hits[0]


def _expected_entry(wid, ts, actor, kind, summary, detail, targets):
    return {"id": wid, "date": ts, "actor": dict(actor), "kind": kind, "summary": summary, "detail": detail,
            "links": [{"rel": "relates", "id": target} for target in targets]}


def _expected_delta(req, ctx, raws, now):
    """The allowed delta, derived INDEPENDENTLY of the planner's output: ([(rel, expected model)] in
    journal order, the expected ids, the expected (id, from, to) of a status change or None). The inputs
    are the request (the caller passes a copy taken before planning), the allocation result (claim_ids
    over the high-water map reparsed from the bytes the plan was made from), the clock value, the
    planned-from bytes, and the schema rules (the type's namespace, initial state, terminal and gated
    states, and the proposer kinds of spec 8.4). Every baseline, including the one row a status change
    rewrites and its prior status, is reparsed from the planned-from bytes, so no expected row or table is
    an object any planned model holds. Deliberately not composed through the planner's helpers: a planner
    that drifts from these rules is refused, not mirrored. The oracle derives the delta only; the
    legality of a status change (validate_transition, the rejection's reason and pre-proposal state, the
    maintainer-only receipt) is the planner's refusal and is not judged again here."""
    ts = _rfc3339(now)
    sub = req.subcommand
    wl = _opf_release.WL_NAMESPACE
    rels = _expected_rels(req, ctx)
    docs = {rel: _reparse(raws[rel], rel) for rel in rels}
    if sub == "create":
        rtype = req.values["--type"]
        spec = _opf_schema.BASELINE_SPECS[rtype]
        demand = [spec.namespace, wl]
    elif sub == "done-with-receipt":
        demand = [_opf_store.BASELINE_TYPES["done"], wl]
    else:
        demand = [wl]
    counters = docs[ctx.counters.rel]
    high, findings = _counter_state(ctx, counters)
    if findings:
        raise _postcondition_failed("the prior counters.toml cannot license the claim")
    ids, new_high = claim_ids(ctx.homes, high, demand, known_complete=True)
    table = dict(counters.get("counters") or {})
    for ns in demand:
        if new_high[ns] != table.get(ns, 0) + 1:
            raise _postcondition_failed("the claim does not advance {} by exactly one".format(ns))
        table[ns] = new_high[ns]
    counters["counters"] = table
    wid = ids[-1]
    transition = None
    if sub in ("create", "worklog-append"):
        row = {"id": ids[0], "actor": dict(req.actor)}
        if sub == "create":
            proposed = req.actor["kind"] in _opf_schema.PROPOSER_KINDS and spec.initial in spec.gated
            row.update({"type": rtype, "status": spec.initial + ("/proposed" if proposed else ""),
                        "title": req.values["--title"], "created_at": ts, "updated_at": ts})
            row.update({name: value for name, value in req.fields})
            if req.scopes:
                row["scopes"] = list(req.scopes)
        else:
            row.update({"date": ts, "kind": req.values["--kind"]})
            if "--detail" in req.values:
                row["detail"] = req.values["--detail"]
        if "--summary" in req.values:
            row["summary"] = req.values["--summary"]
        if req.links:
            row["links"] = [{"rel": link["rel"], "id": link["id"]} for link in req.links]
        if req.refs:
            row["refs"] = [{"kind": ref["kind"], "locator": ref["locator"], "note": ref["note"]}
                           for ref in req.refs]
        if sub == "create":
            docs[rels[1]]["record"] = list(docs[rels[1]].get("record", [])) + [row]
            entry = _expected_entry(wid, ts, req.actor, "added",
                                    "created {} ({}) at {}".format(ids[0], rtype, row["status"]),
                                    "opf-record create {} {}".format(ids[0], row["status"]), [ids[0]])
        else:
            entry = row
    elif sub == "transition":
        rid, target = req.positionals
        prior = _prior_row(docs[rels[1]], rid, rels[1])
        current = prior.get("status")
        spec = _opf_schema.BASELINE_SPECS.get(prior.get("type")) if isinstance(prior.get("type"), str) else None
        if spec is None or not isinstance(current, str):
            raise _postcondition_failed("{} in the prior {} carries no baseline type and status".format(
                rid, rels[1]))
        cur_state, _sep, cur_qual = current.partition("/")
        proposed = (req.actor["kind"] in _opf_schema.PROPOSER_KINDS and target != cur_state
                    and (target in spec.terminal or target in spec.gated))
        to_status = target + ("/proposed" if proposed else "")
        # The proposed_from rule (spec 8.8), derived here on its own: leaving a `/proposed` status removes
        # the recorded pre-proposal state; landing one records the prior status in the row.
        if cur_qual == "proposed":
            prior.pop(PROPOSED_FROM, None)
        if proposed:
            prior[PROPOSED_FROM] = current
        # The resolution bundle rule (spec 8.5/8.8), derived here on its own, apart from the planner: the
        # rejection that leaves `decided/proposed` removes every key the schema's pending_decision
        # declaration lists (spec.extra_keys); `open -> decided`, bare or `/proposed`, writes the three
        # bundle keys named here, decided_at at the clock value and the other two from the request; a
        # ratification leaves the bundle unchanged. spec is the schema entry for the row's type, so the
        # guard below reads spec.name and compares it with the type-name literal; the open and decided
        # states are named here, not read from the schema.
        if spec.name == "pending_decision":
            if cur_qual == "proposed" and target != cur_state and cur_state == "decided":
                for key in spec.extra_keys:
                    prior.pop(key, None)
            if cur_state == "open" and target == "decided":
                prior.update({"decision": req.values.get("--decision"), "decided_at": ts,
                              "decided_by": req.values.get("--decided-by")})
        prior.update({"status": to_status, "updated_at": ts})
        verb = ("rejected" if target != cur_state else "ratified") if cur_qual == "proposed" else "transitioned"
        detail = "opf-record transition {} {} -> {}".format(rid, current, to_status)
        if "--reason" in req.values:
            detail += "\nreason: " + req.values["--reason"]
        entry = _expected_entry(wid, ts, req.actor, "changed",
                                "{} {} from {} to {}".format(verb, rid, current, to_status), detail, [rid])
        transition = (rid, current, to_status)
    else:
        (rid,) = req.positionals
        did = ids[0]
        prior = _prior_row(docs[rels[1]], rid, rels[1])
        current = prior.get("status")
        receipt = {"id": did, "type": "done", "status": "recorded", "title": prior.get("title"),
                   "created_at": ts, "updated_at": ts, "actor": dict(req.actor),
                   "links": [{"rel": "receipt_of", "id": rid}]}
        if "--summary" in req.values:
            receipt["summary"] = req.values["--summary"]
        if current == "done/proposed":
            prior.pop(PROPOSED_FROM, None)
        prior.update({"status": "done", "updated_at": ts})
        docs[rels[2]]["record"] = list(docs[rels[2]].get("record", [])) + [receipt]
        entry = _expected_entry(wid, ts, req.actor, "changed", "{} {} from {} to done with receipt {}".format(
            "ratified" if current == "done/proposed" else "completed", rid, current, did),
            "opf-record transition {} {} -> done\nreceipt: {}".format(rid, current, did), [rid, did])
        transition = (rid, current, "done")
    worklog = docs[rels[-1]]
    worklog["entry"] = list(worklog.get("entry", [])) + [entry]
    return [(rel, docs[rel]) for rel in rels], ids, transition


def _strict_equal(a, b):
    """The postcondition's comparator: strict, TYPE-AWARE structural equality (_opf_emit._model_equal, the
    round-trip comparator emit_checked already proves itself with), never Python's ==. Ordinary equality
    let a mutation that changed only a value's type (True for 1, 1.0 for 1) read as the allowed delta and
    publish (codex QA3 F2); here bool is never a bare int, int is never a float, and datetime is never a
    date, so such a mutation refuses before anything is written."""
    return _opf_emit._model_equal(a, b)


def _postcondition(plan, req, ctx, now):
    """POSTCONDITION (the spec 9.2 guard, applied to record authoring): every operand's EMITTED bytes,
    reparsed (a copy sharing nothing with the planner's rows), must equal EXACTLY its prior bytes,
    reparsed, plus the allowed delta _expected_delta derives on its own, value for value and TYPE for type
    (_strict_equal, so True and 1.0 never read as 1): each new row appended with the requested content
    (the record or receipt, and the operation's own worklog entry), the initial or target status the
    schema rules give, the clock's timestamps, and the claimed ids; for a status change exactly `status`,
    `updated_at`, the `proposed_from` write or removal, and a pending_decision's resolution bundle write or
    removal of the one named record; the counters advanced by
    exactly the claim; nothing else. A stray mutation of an existing record or of a new row, a lost or
    reordered row, a changed schema marker, a status change touching another field or another record, or
    a counter moved by anything but the claim refuses before anything is written, and so does a plan whose
    reported status change is not the one the oracle derives (the final gate trusts that triple). `req`
    must be a copy of the request taken before planning, so nothing the planner does can reach the oracle."""
    rels = [o.rel for o in plan.operands]
    want = _expected_rels(req, ctx)
    if rels != want:
        raise _postcondition_failed("the operand set {} is not {}".format(rels, want))
    raws = {o.rel: o.raw for o in plan.operands}
    expected, ids, transition = _expected_delta(req, ctx, raws, now)
    if list(plan.ids) != ids:
        raise _postcondition_failed("the plan does not report exactly the claimed ids {}".format(ids))
    if plan.transition != transition:
        raise _postcondition_failed("the plan reports the status change {!r}, not the requested {!r}".format(
            plan.transition, transition))
    for operand, (rel, model) in zip(plan.operands, expected):
        if operand.new_raw is None or not _strict_equal(_reparse(operand.new_raw, rel), model):
            raise _postcondition_failed("the emitted {} differs from its allowed delta (exactly the requested "
                                        "rows appended, the one requested status change, and the counters "
                                        "advanced by exactly the claim)".format(rel))


# --- the journal: startup reconciliation and the one journaled publication ---------------------------

def _journal_root(res):
    return Path(res.store_root) / JOURNAL_REL


def _journal_view(jr_fd, journal_root):
    """(owner, txns, states): the journal lock owner, the transaction directories, and each one's durable
    state. Read-only. JournalError on an unreadable lock or a corrupt journal."""
    owner = _journal.read_lock_owner(journal_root)
    txns = _journal._journal_txn_dirs(jr_fd, journal_root)
    return owner, txns, {t.name: _journal.classify_state(jr_fd, t) for t in txns}


def _refuse_live_owner(owner):
    if owner is not None and not _journal.owner_confirmed_dead(owner):
        raise RecordError("another opf record run holds the record journal lock (pid {}); it is never seized. "
                          "Wait for it to finish, then re-run (fail-closed)".format(owner.get("pid")))


def _planned_payloads(intent, ops):
    """Per op, the planned poststate bytes _publish records in the INTENT header, or None where absent or
    not proven against the op's poststate digest (a torn write of that op then cannot be explained)."""
    header = intent.get("header")
    staged = header.get("staged") if isinstance(header, dict) else None
    out = [None] * len(ops)
    if isinstance(staged, list) and len(staged) == len(ops):
        for i, (text, op) in enumerate(zip(staged, ops)):
            try:
                data = base64.b64decode(text, validate=True)
                if _sha256(data) == op["poststate"]["content-sha256"]:
                    out[i] = data
            except (binascii.Error, ValueError, TypeError, KeyError):
                pass
    return out


def _preimage_bytes(jr_fd, txn, prestate):
    """The retained preimage of one op, read contained beneath the journal root and proven against its
    recorded digest, or None."""
    rel = "{}/preimages/{}".format(Path(txn).name, prestate.get("payload"))
    try:
        pfd, name = _journal._open_parent(jr_fd, rel)
        try:
            data, _st = _journal._read_at(pfd, name, rel, cap=prestate["size"])
        finally:
            os.close(pfd)
    except (_journal.JournalError, OSError, KeyError, TypeError):
        return None
    return data if _sha256(data) == prestate.get("sha256") else None


def _operand_unexplained(root_fd, jr_fd, txn, op, planned, rolling_back):
    """Why one operand's current state is NOT one its open transaction explains, or None when it is.
    Explained: a singly-linked regular file at the journaled mode holding exactly the journaled preimage,
    exactly the planned poststate, a strict byte prefix of the planned bytes (the crash tore the write), or,
    once a rollback has begun, a strict byte prefix of the preimage (the crash tore the restore)."""
    try:
        pre, post = op["prestate"], op["poststate"]
        if op["op"] != "write" or pre.get("kind") != "file" or post.get("kind") != "file":
            return "it is not a file write this verb journals"
        data, st = _journal._read_contained(root_fd, op["path"], require_single_link=True)
    except (KeyError, TypeError, AttributeError):
        return "its journal entry is malformed"
    except (_journal.JournalError, OSError) as exc:
        return "it cannot be read as the journaled regular file ({})".format(exc)
    if stat.S_IMODE(st.st_mode) != pre.get("mode"):
        return "its mode is no longer the journaled mode"
    if _sha256(data) in (pre.get("sha256"), post.get("content-sha256")):
        return None
    if planned is not None and len(data) < len(planned) and planned.startswith(data):
        return None
    if rolling_back:
        preimage = _preimage_bytes(jr_fd, txn, pre)
        if preimage is not None and len(data) < len(preimage) and preimage.startswith(data):
            return None
    return ("it holds bytes that are neither the journaled preimage nor the planned poststate, nor a write of "
            "either torn by the interruption: an intervening edit")


def _unexplained_operands(root_fd, jr_fd, txns):
    """The recovery clean-state rule: one line per operand of an open transaction whose state that
    transaction does not explain (_operand_unexplained). Recovery refuses on any line, so an edit made
    since the interruption is surfaced, never overwritten. Read-only."""
    problems = []
    for txn in txns:
        frames, _torn, _good = _journal.read_frames(jr_fd, txn)
        _journal._validate_terminal_agreement(frames)
        intent = _journal._first(frames, _journal.F_INTENT)
        ops = intent.get("ops") if isinstance(intent, dict) else None
        if not isinstance(ops, list):
            problems.append("{}: its INTENT carries no operation list".format(txn.name))
            continue
        rolling_back = any(ftype == _journal.F_RIP for ftype, _obj in frames)
        for op, planned in zip(ops, _planned_payloads(intent, ops)):
            why = _operand_unexplained(root_fd, jr_fd, txn, op, planned, rolling_back)
            if why:
                path = op.get("path") if isinstance(op, dict) else None
                problems.append("{} (transaction {}): {}".format(path, txn.name, why))
    return problems


def _leftover_lock_outcome(owner, states):
    """The outcome line when a dead run left only its journal lock (every transaction already terminal).
    _publish takes the journal lock under the session `opf-record.<token>`, a fresh random token, and names
    the one transaction it then opens record-<subcommand>.<pid>.<time_ns>.<token>, so the dead run's own
    transaction is the one whose name ends with its lock's token: a binding the dead run wrote itself in
    both places, which a reused pid or a later timestamp cannot reproduce. A lock carrying no such token (one
    taken by recovery, or by an earlier build) is not attributed, and the line says so."""
    head = "a leftover journal lock of a dead run was released; every transaction was already terminal"
    session = owner.get("session") if isinstance(owner, dict) else None
    prefix, sep, token = session.partition(".") if isinstance(session, str) else ("", "", "")
    own = [name for name in states if name.startswith("record-") and name.rsplit(".", 1)[-1] == token]
    if prefix != SESSION_ID or not sep or not _TOKEN_RE.match(token) or len(own) > 1:
        return head + (" (which of them the dead run opened cannot be told: its lock carries no publication "
                       "token naming exactly one transaction; git status shows whether its record files "
                       "changed)")
    if not own:
        return head + " and the dead run had opened none, so it published nothing"
    name = own[0]
    state = states[name]
    if state == "complete":
        return head + (": the dead run's transaction {} is COMPLETE, so its publication is present in the "
                       "working tree; whether its render and final doctor ran, and what they reported, is not "
                       "recorded by the journal, so run opf doctor before relying on it".format(name))
    return head + ": the dead run's transaction {} {}, so it published nothing".format(
        name, "was rolled back" if state == "rolled-back" else "never opened")


def _with_recovery_lease(ctx, pending, recover):
    """Run `recover` holding the single-writer lease, claimed exactly as publication claims it. A present
    lease (a live peer's, or the interrupted run's own leftover) refuses before any recovery write and is
    never seized: releasing a leftover lease stays the operator's explicit reconciliation step. The lease
    is released on every exit; a release failure after a refusal is surfaced and never displaces it."""
    try:
        lease = _opf_write_guard.acquire_lease(ctx.root_fd, ctx.machine_rel, VERB)
    except _opf_write_guard.WriteGuardError as exc:
        raise RecordError("an interrupted opf record publication needs reconciliation ({}), and reconciliation "
                          "writes the store, so it runs only under the single-writer lease: {} Nothing was "
                          "written (fail-closed)".format(pending, exc))
    try:
        result = recover()
    except BaseException:
        try:
            _release(ctx, lease)
        except Exception as rel_exc:  # noqa: BLE001  surfaced, never displaces the original failure
            print("opf record: additionally, releasing the lease failed ({}); it is LEFT in place (never "
                  "seized, spec 5.7) and the failure above still governs.".format(rel_exc), file=sys.stderr)
        raise
    _release(ctx, lease)
    return result


def _recover_journal(ctx, jr_fd, journal_root):
    """Recovery proper, under the held lease: None when (re-read under the lease) nothing needs it, else
    the outcome lines. The clean-state rule runs BEFORE any journal or operand write: every open
    transaction's operands must hold a state it explains, else the run refuses naming each path, leaving
    the journal, its lock, and every operand exactly as found. Only then is a confirmed-dead owner's journal
    lock broken after every transaction reconciles to terminal (_journal.reconcile_and_claim_stale), or an
    unlocked open transaction recovered under a fresh journal lock. Recovery rolls each open transaction
    FORWARD when every poststate already verifies, else back from its preimages."""
    root_fd = ctx.root_fd
    try:
        owner, txns, before = _journal_view(jr_fd, journal_root)
        opened = sorted(n for n, s in before.items() if s == "open")
        if owner is None and not opened:
            return None
        _refuse_live_owner(owner)
        problems = _unexplained_operands(root_fd, jr_fd, [t for t in txns if t.name in opened])
        if problems:
            raise RecordError(
                "an interrupted opf record publication cannot be reconciled without overwriting a change made "
                "since it was interrupted: {}. Nothing was written; the journal and every operand are left "
                "exactly as found. Either restore each path to its journaled preimage (under {}/<transaction>/"
                "preimages, or from HEAD when it holds those bytes) and re-run, or keep the edit and retire the "
                "transaction by moving its directory out of {} yourself (fail-closed)".format(
                    "; ".join(problems), JOURNAL_REL, JOURNAL_REL))
        if owner is not None:
            if _journal.reconcile_and_claim_stale(journal_root, jr_fd, root_fd, SESSION_ID) != "acquired":
                raise RecordError("the record journal lock became live during reconciliation; it is never "
                                  "seized. Re-run once no opf record run is live (fail-closed)")
        else:
            _journal.acquire_lock(journal_root, SESSION_ID)
        try:
            for txn in txns:
                _journal.recover(jr_fd, txn, root_fd)
            after = {t.name: _journal.classify_state(jr_fd, t) for t in txns}
        finally:
            _journal.release_lock(journal_root)
    except (_journal.JournalError, OSError) as exc:
        raise RecordError("the record journal {} cannot be reconciled ({}); fail-closed".format(JOURNAL_REL, exc))
    outcomes = []
    for name in opened:
        if after.get(name) == "complete":
            outcomes.append("{} rolled FORWARD (its publication is present in the working tree, uncommitted; "
                            "its render and final doctor never ran)".format(name))
        elif after.get(name) == "rolled-back":
            outcomes.append("{} rolled BACK to its prestate".format(name))
        else:
            raise RecordError("the record journal transaction {} did not reconcile to a terminal state ({}); "
                              "fail-closed".format(name, after.get(name)))
    return outcomes or [_leftover_lock_outcome(owner, after)]


def _reconcile_journal(ctx):
    """Reconcile an interrupted `opf record` publication BEFORE anything else, then refuse this run.
    Nothing to do, and nothing written, when the journal root is absent, or when no journal lock is held
    and every transaction is terminal. Otherwise recovery is a STORE WRITE, so it runs only under the
    rules publication runs under: a journal lock held by a possibly-live owner is never seized; the
    single-writer lease is claimed exactly as publication claims it, so a present lease refuses before any
    recovery write (_with_recovery_lease); and every operand must hold a state its transaction explains
    (_unexplained_operands), so an intervening edit is surfaced and refused. The store then ends exactly
    at the prestate or exactly at the poststate, the lease is released, and the run refuses (exit 2)
    naming each outcome: the operator inspects the result before re-running."""
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
            owner, _txns, states = _journal_view(jr_fd, journal_root)
        except (_journal.JournalError, OSError) as exc:
            raise RecordError("the record journal {} cannot be reconciled ({}); fail-closed".format(
                JOURNAL_REL, exc))
        opened = sorted(n for n, s in states.items() if s == "open")
        if owner is None and not opened:
            return
        _refuse_live_owner(owner)
        pending = "open transaction(s) {}".format(", ".join(opened)) if opened else "a dead run's journal lock"
        outcomes = _with_recovery_lease(ctx, pending, lambda: _recover_journal(ctx, jr_fd, journal_root))
    finally:
        _journal._close_fd_quietly(jr_fd)
    if outcomes is None:
        return
    raise RecordError("an interrupted opf record run was reconciled before this operation: {}. Nothing was "
                      "recorded by this run. Inspect the store paths (git status) and run opf doctor, then "
                      "re-run".format("; ".join(outcomes)))


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


# How a failed publication's transaction is described when the journal lock is retained for reconciliation.
_FAILED_STATE = {"open": "still open",
                 "complete": "COMPLETE (its publication is present, with its render and final doctor never run)"}


def _publish(ctx, plan, subcommand):
    """ONE _journal.run_transaction publishes every operand, in dependency order (counters first). Each
    op is a `write` whose poststate is the planned bytes and whose `source-poststate` pins the exact bytes
    and mode the plan was made from: capture refuses (nothing opened) if the file changed since it was
    read, and apply re-verifies the captured preimage on the opened fd. Crash anywhere leaves the store
    exactly the prestate or exactly the poststate once recovered (_reconcile_journal on the next run).
    The INTENT header also carries every operand's planned bytes, so a later recovery can tell a write the
    crash tore (a byte prefix of them) from an intervening edit (_unexplained_operands); a publication
    whose INTENT would pass the journal-read cap is refused by the engine before it opens. Raises
    RecordError; the journal lock is released on every exit except a failure that may have left the
    transaction open, which keeps it for the next run's reconciliation."""
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
    # The token binds this run's journal lock to the one transaction it opens (_leftover_lock_outcome).
    token = os.urandom(16).hex()
    try:
        try:
            if _journal.read_lock_owner(journal_root) is not None:
                raise RecordError("the record journal lock is held; nothing written (fail-closed)")
            _journal.acquire_lock(journal_root, "{}.{}".format(SESSION_ID, token))
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
        txn_id = "record-{}.{}.{}.{}".format(subcommand, os.getpid(), time.time_ns(), token)
        header = {"unit": SESSION_ID, "kind": "record-" + subcommand,
                  "staged": [base64.b64encode(operand.new_raw).decode("ascii") for operand in plan.operands]}
        try:
            _journal.run_transaction(root_fd, jr_fd, journal_root, txn_id, header, ops,
                                     lambda op: content[op["path"]], SESSION_ID)
        except (_journal.JournalError, OSError) as exc:
            # An absent transaction directory reads as nothing-opened (read_frames), so a failure before
            # INTENT (the budget refusal, a failed mkdir or preimage capture) is told apart from one after it.
            try:
                state = _journal.classify_state(jr_fd, journal_root / txn_id)
            except _journal.JournalError:
                state = None
            if state == "nothing-opened":
                raise RecordError("the publication was refused before its transaction opened ({}); no operand "
                                  "was touched and nothing recorded (fail-closed)".format(exc))
            if state == "rolled-back":
                raise RecordError("the publication was refused and rolled back to the prestate ({}); nothing "
                                  "recorded (fail-closed)".format(exc))
            retain = True
            raise RecordError("the publication FAILED and its transaction {} is {} ({}); the journal lock is "
                              "retained so the next opf record run reconciles it (fail-closed)".format(
                                  txn_id, _FAILED_STATE.get(state, "in an unreadable state"), exc))
    finally:
        if held and not retain:
            try:
                _journal.release_lock(journal_root)
            except (_journal.JournalError, OSError) as exc:
                # Surfaced, never fatal here: the transaction reached a terminal state, and the next run
                # reconciles the leftover lock once this process has exited (_reconcile_journal).
                print("opf record: the record journal lock under {} could not be released ({}); it is left "
                      "in place, and the next opf record run reconciles it and refuses once, naming the "
                      "outcome.".format(JOURNAL_REL, exc), file=sys.stderr)
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


def _snapshot_pending(transition):
    """The predicate for the one doctor cannot-evaluate a transition this verb checked may leave until it
    is committed, or None. Doctor judges a status change against the prior committed snapshot (HEAD) over
    every actor kind: doctor's history comparison sees only the prior snapshot's type and status, which
    identify neither the transitioning actor nor the pre-proposal state; doctor validates `proposed_from`
    but does not use it as rejection evidence (spec 8.8), so an actor-dependent or rejection-shaped
    change is CANNOT-EVALUATE there (C-HISTORY-RESURRECTION) until
    the commit makes it the snapshot. This verb checked that exact change with the known actor, reason, and
    recorded pre-proposal state, and `transition` is the triple the postcondition proved equal to the
    oracle's own derivation of what was written, so it accepts that message, for that record and that
    from/to pair only. Callers also require the message to be a cannot-evaluate, never a finding; doctor's
    own grading is unchanged."""
    if transition is None:
        return None
    prefix = "C-HISTORY-RESURRECTION: prior record {!r} transition {!r} -> {!r} ".format(*transition)
    return lambda message: isinstance(message, str) and message.startswith(prefix)


def _final_gate(root, transition=None):
    """Require a full doctor VALID over the published store before anything is offered as recorded. The
    one exception is a transition this operation checked (`transition` = (id, from, to)): the doctor may
    then be CANNOT-EVALUATE with no finding, provided every cannot-evaluate line is the snapshot comparison
    of exactly that change (_snapshot_pending). Returns those accepted lines (empty when VALID)."""
    try:
        result = _doctor(root)
    except _PostPublication:
        raise
    except Exception as exc:  # noqa: BLE001  a doctor escape must not read as a recorded change
        raise _PostPublication("the final doctor could not run ({!r}); fail-closed".format(exc))
    accepted = _snapshot_pending(transition)
    if (accepted is not None and result.status == _opf_store.CANNOT_EVALUATE and not result.findings
            and result.cannot_evaluate and all(accepted(c) for c in result.cannot_evaluate)):
        return list(result.cannot_evaluate)
    if result.status != _opf_store.VALID:
        lines = ["the published store is NOT doctor-VALID ({}); nothing is offered as recorded".format(
            result.status)]
        lines += ["  FINDING: {}".format(f) for f in result.findings]
        lines += ["  CANNOT-EVALUATE: {}".format(c) for c in result.cannot_evaluate]
        raise _PostPublication("\n".join(lines))
    return []


def _render(root, transition=None):
    """Render the declared views over the published store. Exit 1 (views regenerated, an AUTHORED
    residual such as a changelog finding remains) is left for the final doctor to grade; exit 2 fails. The
    render's source gate accepts only the one pending cannot-evaluate of `transition` (_snapshot_pending)."""
    try:
        rres = _opf_store.resolve_store(Path(os.path.abspath(root)))
        robs, _notes = _opf_observe.gather(rres) if rres.status == _opf_store.RESOLVED else (None, [])
        rc = _opf_views.render(["--root", root, "--write"], observations=robs,
                               accepted=_snapshot_pending(transition))
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
             "with its record and worklog files, never one without the others; confirm no opf run is live, "
             "spec 5.7):",
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
    change = report.get("change")
    pending = report.get("doctor_pending") or []
    print("opf record {}: recorded {}{} and the store is {}; the change is left UNCOMMITTED in the working "
          "tree (this verb never runs git add or commit).".format(
              report["subcommand"], change + ", " if change else "", ", ".join(report["ids"]),
              "doctor-VALID" if not pending else "free of doctor findings"))
    for line in pending:
        print("opf record: until this change is committed, opf doctor reports: CANNOT-EVALUATE: {}. This verb "
              "checked the change with its actor, reason, and recorded pre-proposal state; the snapshot "
              "comparison clears once the commit makes it the prior snapshot.".format(line))
    print(json.dumps(report, sort_keys=True))
    print("opf record: review the change, then stage and commit these paths yourself (the journal under {} "
          "is local recovery evidence, not part of the change):".format(JOURNAL_REL))
    print("  git -C {} --literal-pathspecs add -- {}".format(
        shlex.quote(report["store_root"]), " ".join(shlex.quote(p) for p in report["store_paths"])))
    for p in report["product_paths"]:
        print("  git -C {} --literal-pathspecs add -- {}".format(shlex.quote(report["product_root"]),
                                                                 shlex.quote(p)))


def _release_failure_text(report):
    """What a failed lease release after the final gate reports: the final gate's recorded outcome, VALID or
    free of findings carrying only the accepted pending cannot-evaluate of this change, never a VALID the
    gate did not return."""
    pending = report.get("doctor_pending") or []
    reached = "doctor-VALID" if not pending else (
        "a doctor result free of findings, carrying only the accepted pending cannot-evaluate of {} until it is "
        "committed ({} line(s); opf doctor reports CANNOT-EVALUATE until then)".format(report.get("change"),
                                                                                    len(pending)))
    return ("opf record: the store reached {}, but the lease release failed; nothing is offered as recorded. "
            "Confirm no opf run is live (spec 5.7) and reconcile the lease before any further action.".format(
                reached))


def _conclude(ctx, lease, report):
    """R5: release the single-writer lease FIRST, then emit the success report, so success is never
    reported over a still-held or failed-to-release lease."""
    _release(ctx, lease)
    _emit_success(report)


# --- the shared operation sequence --------------------------------------------------------------------

def _operand_rel(req, ctx):
    """The primary operand: the type index a record is created in or transitioned in (the type named by
    the id's namespace), the backlog index for a receipt, or the worklog itself."""
    if req.subcommand == "worklog-append":
        return ctx.rel(_opf_check.WORKLOG_NAME)
    if req.subcommand == "create":
        return ctx.rel(req.values["--type"] + _opf_check.INDEX_SUFFIX)
    if req.subcommand == "done-with-receipt":
        return ctx.rel(BACKLOG + _opf_check.INDEX_SUFFIX)
    ns = _ID_RE.match(req.positionals[0]).group(1)
    owners = [t for t, n in ctx.types.items() if n == ns]
    if len(owners) != 1 or owners[0] not in _opf_schema.BASELINE_SPECS:
        raise RecordError("{} is not in the namespace of an enabled baseline record type; fail-closed".format(
            req.positionals[0]))
    if _opf_schema.BASELINE_SPECS[owners[0]].reduced:
        raise RecordError("a worklog entry records a fact and does not transition (spec 8.4); fail-closed")
    return ctx.rel(owners[0] + _opf_check.INDEX_SUFFIX)


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
        # 1. an interrupted earlier run is reconciled (under the lease), and refuses this one, before
        # anything is read.
        _reconcile_journal(ctx)
        # 2. read the models this operation plans from; the request checks that need the manifest run
        # before any operand path is built from the request.
        _load_manifest(ctx)
        _check_request(req, ctx)
        ctx.counters = _read_operand(root_fd, ctx.rel(_opf_check.COUNTERS_NAME))
        ctx.version = _read_operand(root_fd, ctx.rel(_opf_check.VERSION_NAME)).model
        ctx.worklog = _read_operand(root_fd, ctx.rel(_opf_check.WORKLOG_NAME))
        rel = _operand_rel(req, ctx)
        operand = ctx.worklog if rel == ctx.worklog.rel else _read_operand(root_fd, rel)
        if req.subcommand == "done-with-receipt":
            ctx.done_index = _read_operand(root_fd, ctx.rel("done" + _opf_check.INDEX_SUFFIX))
        # 3. PRECONDITION: every file this operation rewrites is canonical (byte reproduction).
        for op in (ctx.counters, operand, ctx.worklog, ctx.done_index):
            if op is not None:
                _require_canonical(op)
        # 4. plan (the claim goes through the one allocation seam) and emit, then 5. POSTCONDITION over
        # the emitted bytes, against the oracle's own copy of the request taken before planning.
        now = _clock_now()
        request = copy.deepcopy(req)
        plan = _PLANNERS[req.subcommand](req, ctx, operand, now)
        for op in plan.operands:
            op.new_raw = _emit_bytes(op.new_model)
        _postcondition(plan, request, ctx, now)
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
                _render(root, plan.transition)
                snapshot_pending = _final_gate(root, plan.transition)
            except _PostPublication as exc:
                raise _PostPublication("{}\n{}".format(exc, _recovery_text(ctx, scope)))
            # 9. release the lease, THEN report (R5).
            report = {"event": "recorded", "subcommand": req.subcommand, "ids": list(plan.ids),
                      "change": "{} {} -> {}".format(*plan.transition) if plan.transition else None,
                      "doctor_pending": snapshot_pending, "files": [op.rel for op in plan.operands],
                      "store_root": str(res.store_root), "store_paths": list(scope["store"]),
                      "product_root": str(product_root), "product_paths": list(scope["product"])}
            released = True
            try:
                _conclude(ctx, lease, report)
            except BaseException:
                print(_release_failure_text(report), file=sys.stderr)
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
    refusing), the counters completeness proof, the four planners (the derived `/proposed`, the recorded
    pre-proposal state and --reason of a rejection, the one-to-one receipt), the released-span check, the
    byte-reproduction precondition, and the allowed-delta postcondition. Judged on returned
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
          "transitions, resolution bundle, receipts, released span, precondition, postcondition)".format(
              checked[0]))
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
                        (["transition", "BI-1", "done"], "missing required"),
                        (["transition", "BI-1"], "requires the operand"),
                        (["transition", "--actor", "maintainer"], "requires the operand"),
                        (["transition", "bogus", "done", "--actor", "maintainer"], "not a record id"),
                        (["transition", "BI-1", "done/proposed", "--actor", "assistant"], "target STATE"),
                        (["transition", "PD-1", "decided", "--actor", "maintainer", "--decision", "x"],
                         "given together"),
                        (["transition", "PD-1", "decided", "--actor", "maintainer", "--decided-by", "x"],
                         "given together"),
                        (["create", "--type", "pending_decision", "--title", "t", "--actor", "maintainer",
                          "--decision", "x"], "unrecognized argument"),
                        (["transition", "BI-1", "done", "--actor", "maintainer", "--kind", "x"],
                         "unrecognized argument"),
                        (["done-with-receipt", "BI-1"], "missing required"),
                        (["done-with-receipt", "BI-1", "--actor", "assistant:c"], "maintainer-only"),
                        (["done-with-receipt", "BI-1", "--actor", "automation"], "maintainer-only"),
                        (["done-with-receipt", "FN-1", "--actor", "maintainer"], "not a backlog item"),
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
    req = parse_request(["transition", "FN-2", "fixed", "--actor", "assistant", "--reason", "r"])
    check("parse transition", req.positionals == ["FN-2", "fixed"] and req.values == {"--reason": "r"}
          and req.actor == {"kind": "assistant"})
    req = parse_request(["done-with-receipt", "BI-3", "--actor", "maintainer:j"])
    check("parse done-with-receipt", req.positionals == ["BI-3"] and req.actor == {"kind": "maintainer", "id": "j"})

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
        counters_model = {"schema": 1, "counters": dict(counters)}
        ctx.counters = Operand(".working/toml/counters.toml", _emit_bytes(counters_model), 0o644, counters_model)
        ctx.version = version or {"schema": 1, "release": [], "summary": []}
        ctx.worklog = _model_operand(".working/toml/worklog.toml", {"schema": 1, "entry": []})
        ctx.done_index = _model_operand(".working/toml/done.index.toml", {"schema": 1, "record": []})
        return ctx

    full = {ns: 0 for ns in _opf_store.BASELINE_TYPES.values()}
    ctx = ctx_of(full)
    check("the completeness proof holds for a complete map", _counter_state(ctx, ctx.counters.model)[1] == [])
    partial = dict(full)
    del partial["BI"]
    ctx = ctx_of(partial)
    check("a missing enabled namespace breaks the proof", _counter_state(ctx, ctx.counters.model)[1] != [])
    # Schema markers are exact integers everywhere this verb reads one (codex QA3 F1's sibling checks):
    # a bool or float marker (True == 1, 1.0 == 1 under ==) is refused, never read as schema 1.
    for marker in (True, 1.0):
        check("a non-integer index schema marker refuses ({!r})".format(marker), _refuses(
            lambda marker=marker: _index_rows(_model_operand("x", dict(schema=marker, record=[]))),
            "schema-1"))
        ctx = ctx_of(full)
        ctx.worklog = _model_operand(ctx.worklog.rel, dict(schema=marker, entry=[]))
        check("a non-integer worklog schema marker refuses ({!r})".format(marker),
              _refuses(lambda ctx=ctx: _worklog_entries(ctx), "schema-1"))
    _self_test_planners(check, ctx_of, full, now)


def _model_operand(rel, model):
    """A synthetic operand whose bytes are the canonical emission of its model (the oracle reparses them)."""
    return Operand(rel, _emit_bytes(model), 0o644, model)


def _self_test_planners(check, ctx_of, full, now):
    def plan(argv, rows=(), counters=None, entries=(), dones=()):
        c = ctx_of(counters or full)
        c.worklog = _model_operand(c.worklog.rel, {"schema": 1, "entry": copy.deepcopy(list(entries))})
        c.done_index = _model_operand(c.done_index.rel, {"schema": 1, "record": copy.deepcopy(list(dones))})
        r = parse_request(argv)
        if r.subcommand == "worklog-append":
            c.worklog = _model_operand(c.worklog.rel, {"schema": 1, "entry": copy.deepcopy(list(rows))})
            op = c.worklog
        else:
            op = _model_operand(_operand_rel(r, c), {"schema": 1, "record": copy.deepcopy(list(rows))})
        return _PLANNERS[r.subcommand](r, c, op, now), c, op

    def post(p, c, argv):
        """Emit the planned operands, then run the postcondition against a fresh parse of the request."""
        for o in p.operands:
            o.new_raw = _emit_bytes(o.new_model)
        return _postcondition(p, parse_request(argv), c, now)

    # -- the create planner --------------------------------------------------------------------------------
    p, c, op = plan(["create", "--type", "block", "--title", "b", "--actor", "assistant", "--scope", "BI-1"])
    check("an assistant block lands active/proposed",
          op.new_model["record"][-1]["status"] == "active/proposed" and p.ids == ["BL-1", "WL-1"])
    check("the claim advances exactly the block and worklog namespaces",
          c.counters.new_model["counters"] == dict(full, BL=1, WL=1))
    check("create appends its own worklog entry with the lifecycle line",
          c.worklog.new_model["entry"] == [{
              "id": "WL-1", "date": "2026-09-27T01:02:03Z", "actor": {"kind": "assistant"}, "kind": "added",
              "summary": "created BL-1 (block) at active/proposed",
              "detail": "opf-record create BL-1 active/proposed", "links": [{"rel": "relates", "id": "BL-1"}]}]
          and [o.rel for o in p.operands] == [c.counters.rel, op.rel, c.worklog.rel])
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
    check("worklog-append refuses a forged lifecycle line", _refuses(lambda: plan(
        ["worklog-append", "--kind", "changed", "--summary", "s", "--actor", "assistant", "--detail",
         "opf-record transition BI-1 active -> done/proposed"]), "reserved"))
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
    check("the genuine plan passes the postcondition", post(p, c, argv) is None)
    mutations = (
        ("an extra field mutation", lambda p, c, op: op.new_model["record"][0].__setitem__("title", "x")),
        ("a dropped existing row", lambda p, c, op: op.new_model["record"].pop(0)),
        ("an over-advanced counter", lambda p, c, op: c.counters.new_model["counters"].__setitem__("BI", 3)),
        ("another counter moved", lambda p, c, op: c.counters.new_model["counters"].__setitem__("FN", 1)),
        ("a changed schema marker", lambda p, c, op: op.new_model.__setitem__("schema", 2)),
        ("a changed appended status", lambda p, c, op: op.new_model["record"][-1].__setitem__("status", "active")),
        ("a changed appended title", lambda p, c, op: op.new_model["record"][-1].__setitem__("title", "x")),
        ("an extra appended field", lambda p, c, op: op.new_model["record"][-1].__setitem__("summary", "x")),
        ("a changed appended id", lambda p, c, op: op.new_model["record"][-1].__setitem__("id", "BI-9")),
        ("a bool schema marker (True must never read as 1)",
         lambda p, c, op: op.new_model.__setitem__("schema", True)),
        ("a float counter (2.0 must never read as 2)",
         lambda p, c, op: c.counters.new_model["counters"].__setitem__("BI", 2.0)),
        ("a second appended row", lambda p, c, op: op.new_model["record"].append(dict(op.new_model["record"][-1]))))
    for label, mutate in mutations:
        p, c, op = plan(argv, rows=base_rows, counters=dict(full, BI=1))
        mutate(p, c, op)
        check("the postcondition refuses {}".format(label),
              _refuses(lambda: post(p, c, argv), "postcondition failed"))
    wl_argv = ["worklog-append", "--kind", "added", "--summary", "s", "--actor", "assistant", "--detail", "d"]
    p, c, op = plan(wl_argv)
    check("the genuine worklog plan passes the postcondition", post(p, c, wl_argv) is None)
    for field, value in (("summary", "x"), ("kind", "removed"), ("detail", "x"), ("date", "2026-01-01T00:00:00Z")):
        p, c, op = plan(wl_argv)
        op.new_model["entry"][-1][field] = value
        check("the postcondition refuses a changed appended worklog {}".format(field),
              _refuses(lambda: post(p, c, wl_argv), "postcondition failed"))
    p, c, op = plan(argv)
    p.ids = ["BI-9"]
    check("the postcondition refuses rows not carrying the claimed ids",
          _refuses(lambda: post(p, c, argv), "claimed ids"))
    p, c, op = plan(argv)
    p.operands = p.operands[1:]
    check("the postcondition refuses a plan missing the counters operand",
          _refuses(lambda: post(p, c, argv), "operand set"))
    _self_test_transitions(check, plan, post, full, now)


def _self_test_transitions(check, plan, post, full, now):
    earlier = "2026-09-26T00:00:00Z"

    def bi(status, rid="BI-1", kind="assistant"):
        return {"id": rid, "type": "backlog_item", "status": status, "title": "t", "created_at": earlier,
                "updated_at": earlier, "actor": {"kind": kind}}

    def line(rid, frm, to):
        return {"id": "WL-1", "date": earlier, "actor": {"kind": "assistant"}, "kind": "changed", "summary": "s",
                "detail": "opf-record transition {} {} -> {}".format(rid, frm, to)}

    counters = dict(full, BI=2, WL=1)
    # -- transition: the derived qualifier, the delta, and its own worklog entry ----------------------------
    p, c, op = plan(["transition", "BI-1", "active", "--actor", "assistant"], rows=[bi("open")], counters=counters)
    check("an assistant open -> active is unqualified (not terminal, not gated)",
          op.new_model["record"][0]["status"] == "active" and p.ids == ["WL-2"])
    two = [bi("active"), bi("open", "BI-2")]
    p, c, op = plan(["transition", "BI-1", "done", "--actor", "assistant"], rows=two, counters=counters)
    check("an assistant active -> done lands done/proposed with no receipt, recording the pre-proposal "
          "state in the record's own field",
          op.new_model["record"][0]["status"] == "done/proposed"
          and op.new_model["record"][0][PROPOSED_FROM] == "active"
          and op.new_model["record"][0]["updated_at"] == "2026-09-27T01:02:03Z"
          and op.new_model["record"][1] == bi("open", "BI-2") and c.done_index.new_model is None)
    entry = c.worklog.new_model["entry"][-1]
    check("a transition's worklog entry carries the lifecycle line",
          entry["detail"] == "opf-record transition BI-1 active -> done/proposed" and entry["kind"] == "changed"
          and entry["links"] == [{"rel": "relates", "id": "BI-1"}])
    t_argv = ["transition", "BI-1", "done", "--actor", "assistant"]
    check("the transition passes the postcondition", post(p, c, t_argv) is None
          and p.transition == ("BI-1", "active", "done/proposed"))
    for label, mutate in (
            ("a second field on the transitioned record",
             lambda p, c, op: op.new_model["record"][0].__setitem__("title", "x")),
            ("another record's status", lambda p, c, op: op.new_model["record"][1].__setitem__("status", "active")),
            ("a bare terminal status for an assistant",
             lambda p, c, op: op.new_model["record"][0].__setitem__("status", "done")),
            ("an unchanged updated_at",
             lambda p, c, op: op.new_model["record"][0].__setitem__("updated_at", earlier)),
            ("a changed lifecycle line",
             lambda p, c, op: c.worklog.new_model["entry"][-1].__setitem__("detail", "opf-record transition x")),
            ("a reason the request did not give",
             lambda p, c, op: c.worklog.new_model["entry"][-1].__setitem__(
                 "detail", c.worklog.new_model["entry"][-1]["detail"] + "\nreason: r")),
            ("a dropped proposed_from on the proposal",
             lambda p, c, op: op.new_model["record"][0].pop(PROPOSED_FROM)),
            ("a forged proposed_from on the proposal",
             lambda p, c, op: op.new_model["record"][0].__setitem__(PROPOSED_FROM, "open")),
            ("a reported status change other than the requested one",
             lambda p, c, op: setattr(p, "transition", ("BI-1", "active", "done"))),
            ("a dropped worklog operand", lambda p, c, op: setattr(p, "operands", p.operands[:2]))):
        p, c, op = plan(t_argv, rows=two, counters=counters)
        mutate(p, c, op)
        check("the postcondition refuses {}".format(label),
              _refuses(lambda: post(p, c, t_argv), "postcondition failed"))
    finding = {"id": "FN-1", "type": "finding", "status": "open", "title": "f", "created_at": earlier,
               "updated_at": earlier, "actor": {"kind": "maintainer"}}
    p, c, op = plan(["transition", "FN-1", "fixed", "--actor", "automation"], rows=[finding],
                    counters=dict(counters, FN=1))
    check("an automation terminal transition lands /proposed", op.new_model["record"][0]["status"] == "fixed/proposed")
    # -- transition refusals ---------------------------------------------------------------------------------
    def pbi(pre="active", status="done/proposed", **kw):
        """A proposed backlog item carrying the writer-recorded pre-proposal state (spec 8.8)."""
        return dict(bi(status, **kw), proposed_from=pre)

    for argv, rows, needle in (
            (["transition", "BI-1", "done", "--actor", "maintainer"], [bi("active")], "done-with-receipt"),
            (["transition", "BI-1", "done", "--actor", "assistant"], [bi("open")], "illegal transition"),
            (["transition", "BI-1", "active", "--actor", "maintainer"], [pbi()], "recorded reason"),
            (["transition", "BI-1", "active", "--actor", "maintainer", "--reason", "r"], [bi("done/proposed")],
             "no pre-proposal state"),
            (["transition", "BI-1", "open", "--actor", "maintainer", "--reason", "r"], [pbi()],
             "actual pre-proposal state"),
            (["transition", "BI-1", "active", "--actor", "assistant", "--reason", "r"], [pbi()],
             "only a maintainer"),
            (["transition", "BI-2", "active", "--actor", "maintainer"], [bi("open")], "not a record"),
            (["transition", "BI-1", "active", "--actor", "maintainer"], [bi("open"), bi("open")],
             "seated 2 times"),
            (["transition", "WL-1", "recorded", "--actor", "maintainer"], [], "does not transition"),
            (["transition", "BI-1", "dropped", "--actor", "maintainer"],
             [dict(bi("open"), updated_at=_rfc3339(now))], "not later than")):
        check("transition refuses {}".format(needle), _refuses(
            lambda argv=argv, rows=rows: plan(argv, rows=rows, counters=counters), needle))
    # -- the maintainer rejection, back to the recorded pre-proposal state --------------------------------------
    reject = ["transition", "BI-1", "active", "--actor", "maintainer", "--reason", "not finished"]
    forged = [line("BI-1", "open", "done/proposed")]
    check("a proposal without the recorded field refuses whatever the worklog line claims (the line is "
          "informational, never evidence)", _refuses(
              lambda: plan(reject, rows=[bi("done/proposed")], counters=counters, entries=forged),
              "no pre-proposal state"))
    check("the forged line's own target refuses against the recorded field", _refuses(
        lambda: plan(["transition", "BI-1", "open", "--actor", "maintainer", "--reason", "r"],
                     rows=[pbi()], counters=counters, entries=forged), "actual pre-proposal state"))
    p, c, op = plan(reject, rows=[pbi()], counters=counters, entries=forged)
    check("a maintainer rejection restores exactly the recorded pre-proposal state and removes the field, "
          "whatever the worklog line claims",
          op.new_model["record"][0]["status"] == "active"
          and PROPOSED_FROM not in op.new_model["record"][0]
          and c.worklog.new_model["entry"][-1]["detail"]
          == "opf-record transition BI-1 done/proposed -> active\nreason: not finished")
    check("the rejection passes the postcondition (the field's removal is the oracle's own rule)",
          post(p, c, reject) is None and p.transition == ("BI-1", "done/proposed", "active"))
    p, c, op = plan(reject, rows=[pbi()], counters=counters)
    op.new_model["record"][0][PROPOSED_FROM] = "active"
    check("the postcondition refuses a rejection that keeps proposed_from",
          _refuses(lambda: post(p, c, reject), "postcondition failed"))
    check("the recorded pre-proposal state is the record's own field, never worklog text",
          _recorded_pre_proposal(None, pbi(), "BI-1", "done/proposed") == "active"
          and _recorded_pre_proposal(None, bi("done/proposed"), "BI-1", "done/proposed") is None
          and _recorded_pre_proposal(None, dict(bi("done/proposed"), proposed_from=3), "BI-1",
                                     "done/proposed") is None)
    # -- the current row is validated before it is trusted or overwritten (QA4 F1) ------------------------------
    check("a rejection refuses a schema-invalid proposed_from on the current row",
          _refuses(lambda: plan(reject, rows=[pbi(pre="open")], counters=counters), "not schema-valid"))
    check("done-with-receipt refuses a schema-invalid proposed_from on the current row",
          _refuses(lambda: plan(["done-with-receipt", "BI-1", "--actor", "maintainer"],
                                rows=[pbi(pre="open")], counters=counters), "not schema-valid"))
    check("a proposing transition refuses a stray proposed_from on an unqualified current row",
          _refuses(lambda: plan(t_argv, rows=[dict(bi("active"), proposed_from="open"), bi("open", "BI-2")],
                                counters=counters), "not schema-valid"))
    # -- done-with-receipt -------------------------------------------------------------------------------------
    for frm, kind in (("active", "maintainer"), ("done/proposed", "assistant")):
        d_argv = ["done-with-receipt", "BI-1", "--actor", "maintainer"]
        rows = [pbi(kind=kind)] if frm == "done/proposed" else [bi(frm, kind=kind)]
        p, c, op = plan(d_argv, rows=rows, counters=counters)
        receipt = c.done_index.new_model["record"][-1]
        check("done-with-receipt from {} lands done with one receipt_of receipt and no proposed_from".format(frm),
              op.new_model["record"][0]["status"] == "done" and p.ids == ["DN-1", "WL-2"]
              and PROPOSED_FROM not in op.new_model["record"][0]
              and receipt["links"] == [{"rel": "receipt_of", "id": "BI-1"}] and receipt["status"] == "recorded"
              and c.counters.new_model["counters"] == dict(counters, DN=1, WL=2)
              and post(p, c, d_argv) is None and p.transition == ("BI-1", frm, "done"))
    d_argv = ["done-with-receipt", "BI-1", "--actor", "maintainer"]
    for label, mutate in (
            ("a receipt with another title", lambda c: c.done_index.new_model["record"][-1].__setitem__("title", "x")),
            ("a receipt linked to another item", lambda c: c.done_index.new_model["record"][-1].__setitem__(
                "links", [{"rel": "receipt_of", "id": "BI-2"}])),
            ("a second receipt", lambda c: c.done_index.new_model["record"].append(
                dict(c.done_index.new_model["record"][-1], id="DN-2"))),
            ("a receipt counter over-advanced", lambda c: c.counters.new_model["counters"].__setitem__("DN", 2))):
        p, c, op = plan(d_argv, rows=[bi("done/proposed")], counters=counters)
        mutate(c)
        check("the postcondition refuses {}".format(label),
              _refuses(lambda: post(p, c, d_argv), "postcondition failed"))
    p, c, op = plan(d_argv, rows=[pbi()], counters=counters)
    op.new_model["record"][0][PROPOSED_FROM] = "active"
    check("the postcondition refuses a ratification that keeps proposed_from",
          _refuses(lambda: post(p, c, d_argv), "postcondition failed"))
    held = [{"id": "DN-1", "type": "done", "status": "recorded", "title": "t", "created_at": earlier,
             "updated_at": earlier, "actor": {"kind": "maintainer"}, "links": [{"rel": "receipt_of", "id": "BI-1"}]}]
    for status, dones, needle in (("open", (), "active or done/proposed"), ("done", (), "active or done/proposed"),
                                  ("dropped/proposed", (), "active or done/proposed"),
                                  ("active", held, "already has a done receipt")):
        check("done-with-receipt refuses {} ({})".format(needle, status), _refuses(
            lambda status=status, dones=dones: plan(["done-with-receipt", "BI-1", "--actor", "maintainer"],
                                                    rows=[bi(status)], counters=dict(counters, DN=1), dones=dones),
            needle))
    _self_test_decisions(check, plan, post, full, now)
    _self_test_pending(check)
    _self_test_leftover_lock(check)


def _self_test_decisions(check, plan, post, full, now):
    """The resolution bundle a pending_decision's `open -> decided` writes (bare or `/proposed`), keeps on
    ratification, and removes on rejection, the refusals of its options, and its postcondition vectors."""
    earlier = "2026-09-26T00:00:00Z"
    ts = _rfc3339(now)
    counters = dict(full, PD=1, WL=1)
    bundle = {"decision": "use X", "decided_at": ts, "decided_by": "the board"}

    def pd(status, kind="assistant", **kw):
        return dict({"id": "PD-1", "type": PENDING_DECISION, "status": status, "title": "t",
                     "created_at": earlier, "updated_at": earlier, "actor": {"kind": kind}}, **kw)

    decide = ["--decision", "use X", "--decided-by", "the board"]
    m_argv = ["transition", "PD-1", "decided", "--actor", "maintainer:j"] + decide
    p, c, op = plan(m_argv, rows=[pd("open")], counters=counters)
    got = op.new_model["record"][0]
    check("a maintainer decide lands bare decided with the whole bundle, decided_by from --decided-by",
          got == dict(pd("open"), status="decided", updated_at=ts, **bundle)
          and post(p, c, m_argv) is None and p.transition == ("PD-1", "open", "decided"))
    a_argv = ["transition", "PD-1", "decided", "--actor", "assistant:c"] + decide
    p, c, op = plan(a_argv, rows=[pd("open")], counters=counters)
    proposed = op.new_model["record"][0]
    check("an assistant decide lands decided/proposed with the bundle and proposed_from open",
          proposed == dict(pd("open"), status="decided/proposed", updated_at=ts, proposed_from="open", **bundle)
          and post(p, c, a_argv) is None)
    u_argv = ["transition", "PD-1", "decided", "--actor", "automation:ci"] + decide
    p, c, op = plan(u_argv, rows=[pd("open")], counters=counters)
    check("an automation decide lands decided/proposed with the bundle and proposed_from open",
          op.new_model["record"][0] == dict(pd("open"), status="decided/proposed", updated_at=ts,
                                            proposed_from="open", **bundle)
          and post(p, c, u_argv) is None and p.transition == ("PD-1", "open", "decided/proposed"))
    filed = dict(pd("decided/proposed", proposed_from="open"), decision="use X", decided_at=earlier,
                 decided_by="the board")
    r_argv = ["transition", "PD-1", "decided", "--actor", "maintainer"]
    p, c, op = plan(r_argv, rows=[filed], counters=counters)
    got = op.new_model["record"][0]
    check("a ratification keeps the bundle unchanged and removes proposed_from",
          got == dict({k: v for k, v in filed.items() if k != PROPOSED_FROM}, status="decided", updated_at=ts)
          and post(p, c, r_argv) is None)
    j_argv = ["transition", "PD-1", "open", "--actor", "maintainer", "--reason", "not the board's answer"]
    p, c, op = plan(j_argv, rows=[filed], counters=counters)
    got = op.new_model["record"][0]
    check("a rejection of decided/proposed restores open with no bundle and no proposed_from",
          got == dict(pd("decided/proposed"), status="open", updated_at=ts) and post(p, c, j_argv) is None)
    withdrawn = pd("withdrawn/proposed", proposed_from="open")
    w_argv = ["transition", "PD-1", "open", "--actor", "maintainer", "--reason", "r"]
    p, c, op = plan(w_argv, rows=[withdrawn], counters=counters)
    check("a rejection of withdrawn/proposed restores open (no bundle to remove)",
          op.new_model["record"][0] == dict(pd("open"), updated_at=ts) and post(p, c, w_argv) is None)
    for argv, rows, needle in (
            (["transition", "PD-1", "decided", "--actor", "maintainer"], [pd("open")], "requires --decision"),
            (["transition", "PD-1", "decided", "--actor", "assistant"], [pd("open")], "requires --decision"),
            (["transition", "PD-1", "decided", "--actor", "automation"], [pd("open")], "requires --decision"),
            (["transition", "PD-1", "withdrawn", "--actor", "maintainer"] + decide, [pd("open")], "apply only"),
            (r_argv + decide, [filed], "apply only"),
            (j_argv + decide, [filed], "apply only"),
            (["transition", "BI-1", "active", "--actor", "maintainer"] + decide,
             [{"id": "BI-1", "type": "backlog_item", "status": "open", "title": "t", "created_at": earlier,
               "updated_at": earlier, "actor": {"kind": "maintainer"}}], "apply only"),
            (["transition", "PD-1", "decided", "--actor", "maintainer", "--decision", "   ", "--decided-by", "b"],
             [pd("open")], "non-empty string")):
        check("a decision transition refuses {} ({})".format(needle, " ".join(argv[1:4])), _refuses(
            lambda argv=argv, rows=rows: plan(argv, rows=rows, counters=dict(counters, BI=1)), needle))
    for label, argv, rows, mutate in (
            ("a decided_by taken from --actor", m_argv, [pd("open")],
             lambda op: op.new_model["record"][0].__setitem__("decided_by", "maintainer:j")),
            ("a decided_at other than the clock", m_argv, [pd("open")],
             lambda op: op.new_model["record"][0].__setitem__("decided_at", earlier)),
            ("a decide with no bundle", m_argv, [pd("open")],
             lambda op: [op.new_model["record"][0].pop(k) for k in DECISION_BUNDLE]),
            ("a ratification that rewrites the decision", r_argv, [filed],
             lambda op: op.new_model["record"][0].__setitem__("decision", "use Y")),
            ("a rejection that keeps the decision", j_argv, [filed],
             lambda op: op.new_model["record"][0].__setitem__("decision", "use X"))):
        p, c, op = plan(argv, rows=rows, counters=counters)
        mutate(op)
        check("the postcondition refuses {}".format(label),
              _refuses(lambda argv=argv, p=p, c=c: post(p, c, argv), "postcondition failed"))


def _self_test_pending(check):
    """The one accepted snapshot cannot-evaluate: exactly this record and from/to pair, a cannot-evaluate
    and never a finding, and no other source check relieved."""
    from types import SimpleNamespace
    accepted = _snapshot_pending(("BI-1", "active", "done/proposed"))
    msg = ("C-HISTORY-RESURRECTION: prior record 'BI-1' transition 'active' -> 'done/proposed' is legal for "
           "some actor kinds but illegal for others")
    check("no transition accepts nothing", _snapshot_pending(None) is None)
    check("the pending predicate names exactly the transition", accepted(msg)
          and not accepted(msg.replace("'BI-1'", "'BI-2'")) and not accepted(msg.replace("'active'", "'open'")))
    cids = _opf_check.source_checks(SimpleNamespace(checks={}))

    def result(grade, by, cannot, cid="C-HISTORY-RESURRECTION"):
        checks = dict.fromkeys(cids, "PASS")
        checks[cid] = grade
        return SimpleNamespace(checks=checks, by_check={cid: by}, cannot_evaluate=cannot, unattributed=[])

    ok = _opf_check.source_integrity_ok
    check("the pending cannot-evaluate blocks render without the predicate", not ok(result("CANNOT-EVALUATE",
                                                                                          [msg], [msg])))
    check("the predicate relieves exactly that cannot-evaluate", ok(result("CANNOT-EVALUATE", [msg], [msg]),
                                                                    accepted))
    check("the predicate never relieves a finding", not ok(result("CANNOT-EVALUATE", [msg], []), accepted)
          and not ok(result("FINDING", [msg], []), accepted))
    check("the predicate never relieves another message in the check",
          not ok(result("CANNOT-EVALUATE", [msg, "x"], [msg, "x"]), accepted))
    other = "C-ID-SPACE: duplicate id 'BI-1'"
    check("the predicate never relieves another check's cannot-evaluate",
          not ok(result("CANNOT-EVALUATE", [other], [other], cid="C-ID-SPACE"), accepted))


def _self_test_leftover_lock(check):
    """A dead run's leftover journal lock over its COMPLETE transaction: the outcome states the durable fact
    only, never that render and the final doctor did not run (a run whose lock release failed after
    COMPLETE went on to render, run doctor, and report). The dead run's transaction is the one carrying its
    lock's token, never one that merely shares its pid and a later timestamp (a reused pid)."""
    utc = "2026-09-27T00:00:00Z"
    since = int(datetime.datetime(2026, 9, 27, tzinfo=datetime.timezone.utc).timestamp())
    token, other = "a" * 32, "b" * 32
    owner = {"pid": 4242, "utc": utc, "session": "{}.{}".format(SESSION_ID, token)}
    name = "record-create.4242.{}.{}".format((since + 1) * 10 ** 9, token)
    line = _leftover_lock_outcome(owner, {name: "complete"})
    check("a leftover lock over a COMPLETE transaction names it and asserts no render or doctor outcome",
          name in line and "COMPLETE" in line and "not recorded" in line and "never run" not in line)
    line = _leftover_lock_outcome(owner, {name: "rolled-back"})
    check("a leftover lock over a rolled-back transaction says nothing was published",
          "published nothing" in line)
    reused = "record-transition.4242.{}.{}".format((since + 5) * 10 ** 9, other)
    line = _leftover_lock_outcome(owner, {reused: "complete"})
    check("a reused pid's later COMPLETE transaction is not attributed to the dead run",
          reused not in line and "opened none" in line)
    line = _leftover_lock_outcome(owner, {reused: "complete", name: "rolled-back"})
    check("the token picks the dead run's own transaction among same-pid ones",
          name in line and reused not in line and "rolled back" in line)
    for label, lock in (("a recovery lock with no token", dict(owner, session=SESSION_ID)),
                        ("a malformed token", dict(owner, session=SESSION_ID + ".xyz"))):
        line = _leftover_lock_outcome(lock, {name: "complete"})
        check("{} is not attributed, and says so".format(label), name not in line and "cannot be told" in line)


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-test"]:
        sys.exit(self_test())
    print("usage: _opf_record.py --self-test (the verb is `opf record`)", file=sys.stderr)
    sys.exit(EXIT_MALFORMED)
