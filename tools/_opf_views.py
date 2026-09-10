"""Deterministic view generators and the closed transform vocabulary for the DevProcess (OPF) store
(OPF core-tooling, unit U4). Stdlib only; adopter-rooted; byte-reproducible.

This is the multi-source render side of `opf render`. Where the shared driver in `opf_render.py`
(run_generator) renders one or more targets from a SINGLE source TOML against the pack's own repo root,
U4 renders the composed views, the 1:1 index mirrors, and the root VERSION deliverable from a resolved
adopter store whose views draw on SEVERAL sources at once (a backlog view joins backlog_item against
block, a decisions view walks the pending_decision supersession chain). run_generator is single-source,
so U4 does NOT fork it; it reuses the low-level primitives (the store resolution and contained no-follow
reads of `_opf_store`/`_journal`, the record/ledger validators of `_opf_schema`/`_opf_release`, and its
OWN no-follow contained write, the write counterpart of those no-follow reads) and drives them with the
same render-all-then-write-each two-phase ordering: every target's payload is rendered before any target
is written, so a fail-closed source aborts the whole render before a single file is touched, and the
targets are then written (or drift-reported under --check) in a stable (name-sorted) order.

What it renders (spec 10.1, driven by the manifest [views.*] map):
  - the COMPOSED views TODO/BACKLOG/PIPELINE/DONE/FINDINGS/DECISIONS/BLOCKS/HANDOFF/REFERENCES, each
    composed from its declared sources through the transform vocabulary below;
  - the deterministic single-source views WORKLOG.md and the optional VERSION.md;
  - the 1:1 <TYPE>-INDEX.md mirrors, one per enabled baseline type where declared;
  - the root VERSION deliverable, the latest release's version as exact bytes (spec 5.8/6.1).

The transform vocabulary is CLOSED and versioned (spec 10.2): filter on declared field predicates; sort
on declared keys with the record ID as the final tie-breaker; group by a declared field; project declared
columns; plus EXACTLY TWO named joins, the block-actionability join (an unqualified active block HIDES the
records it scopes, spec 8.5) and the decision-resolution supersession-chain join (the current effective
resolution is the head of each supersedes chain, spec 8.5). Nothing beyond this vocabulary enters a
generator; a new composition shape is a specification version bump, not ad hoc logic here.

Determinism (spec 10.3): UTF-8, LF, stable ordering, no locale-dependent sorting, no wall-clock content,
no network, and no model. Every generated .md view OPENS with a do-not-edit header naming its source
paths, the schema and generator versions, a digest of its source set, and the regeneration command, with
NO timestamp. The root VERSION deliverable is the one exception the spec carves out: its content is the
version string as EXACT bytes (spec 6.1), so it carries no header, exactly as the pack's own generated
VERSION file does.

Adopter-rooted, like doctor.py/migrate.py/conformance.py: `opf render --root DIR` resolves the store at a
PRODUCT repository root (default: the cwd), never the pack's own tree via `_gen_common.repo_root()`. A
live `opf render --root .` in THIS repo reports NOT APPLICABLE (the pack is not a DevProcess adopter), so
the assurance rides the `--self-test` render leg over synthetic stores, mirroring the crosswalk/doctor
legs. Fail-closed: an unresolvable store, or a declared source that is missing or unreadable, STOPS
(exit 2), never a silent empty or partial view; an empty store (its index files present but carrying no
records) renders VALID EMPTY views, which is distinct from cannot-evaluate.

Every target is bound to the view's OWN spec destination (spec 5.8/9), never to the manifest `target`: a
manifest cannot rebind a known view or deliverable off its destination, and view outputs are written
through a no-follow CONTAINED write, so a symlinked destination is refused rather than followed.

Scope and disclosed deferrals: U4 renders the `inline` store layout only. A manifest declaring
`layout = "per-record"` is a CLEAR cannot-evaluate (per-record support is deferred, F7), never
mis-reported as a downstream malformed-record error, and never silently rendered as if inline. The
`<TYPE>-INDEX.md` mirrors cover the baseline record types only; module-type mirrors are DEFERRED until
module-type record schemas ship (F8; `_opf_schema.validate_record` knows only the baseline specs, so no
module-type mirror can be rendered regardless). A mutating render MUST gate on the U6 `validate_store`
store-integrity layer (cross-record uniqueness, coverage, and reconciliation), a tracked spec-11
obligation (F2); that layer does not exist yet and `render` does not compose it (VC-4, deferred), so
WRITE mode FAILS CLOSED today: an ungated write through any entry (the unwired verb, the module
`__main__`, or a direct `render(...)` call) is REFUSED (cannot-evaluate, exit 2) and writes nothing,
while `--check` (read-only drift detection) keeps working and the CLI defaults to check-only. This is a
refusal pending the real gate, never a fabricated one.
"""
import hashlib
import html
import os
import re
import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _journal          # noqa: E402  contained (dir-fd, no-follow) readers + JournalError + containment probe
import _opf_store        # noqa: E402  store resolution + discovery + manifest base/profile schema
import _opf_schema       # noqa: E402  record envelope + baseline type schemas + status parsing + id shape
import _opf_release      # noqa: E402  version.toml + worklog.toml validators + SemVer

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    sys.exit("error: tools/_opf_views.py requires Python 3.11+ (tomllib).")

EXIT_OK = 0
EXIT_DRIFT = 1
EXIT_CANNOT_EVALUATE = 2

# The generated-header identity (spec 10.3). The generator version is this view generator's own contract
# version, independent of the store schema version; a change to the rendered shape bumps it so a stale
# hand-copy reads as drift.
GENERATOR_NAME = "opf-views"
GENERATOR_VERSION = "1"
SCHEMA_VERSION = _opf_schema.SUPPORTED_SCHEMA
REGEN_COMMAND = "opf render"

# spec 5.7/5.8/11: a mutating render must gate on the U6 store-integrity layer (validate_store:
# cross-record uniqueness, coverage, reconciliation). That layer does not exist yet and render does
# not compose it (VC-4, deferred), so WRITE mode fails closed: an ungated write is refused. VC-4 flips
# this to True only WHEN it wires the actual gate invocation; until then every write is refused. The
# CLI runs check-only meanwhile. This is not a fabricated gate; it is a refusal pending the real one.
_WRITE_GATE_COMPOSED = False

WORKING_DIRNAME = _opf_store.WORKING_DIRNAME     # ".working": public targets sit OUTSIDE it, at product root

# The baseline record types (spec 8.1), with worklog and version resolved to their ledger files rather
# than a `<type>.index.toml` (spec 4.2). A view source name maps to exactly one store file through this.
BASELINE_TYPES = tuple(_opf_schema.BASELINE_SPECS)   # the nine baseline type names


class ViewsError(Exception):
    """A render cannot be completed against the resolved store: a missing or unreadable declared source, a
    malformed record or ledger, or a manifest a view references inconsistently. Callers map it to a
    CANNOT-EVALUATE outcome (exit 2): fail-closed, never a silent empty or partial view."""


# --- source-name -> store file resolution (spec 4.2) -------------------------------------------------

def _source_relpath(machine_rel, name):
    """The store-relative path of a view source. A baseline record type reads its `<type>.index.toml`
    inline index (spec 9, `layout = "inline"`); the worklog and version ledgers read their own single
    files (spec 4.2, 6.1). An unknown source name is a manifest/tooling inconsistency, fail-closed."""
    if name == "worklog":
        return "{}/worklog.toml".format(machine_rel)
    if name == "version":
        return "{}/version.toml".format(machine_rel)
    if name in _opf_schema.BASELINE_SPECS:
        return "{}/{}.index.toml".format(machine_rel, name)
    raise ViewsError("view source {!r} names no baseline type, worklog, or version ledger "
                     "(spec 8.1/10.2)".format(name))


# --- contained reads (the _opf_store idiom, mapped to ViewsError) ------------------------------------

def _read_raw_and_parsed(store_root_fd, relpath):
    """Read a declared source file's raw bytes AND its parsed table through a no-follow fd. Returns
    (raw_bytes, parsed_dict), or None when the file is ABSENT. ViewsError (a cannot-evaluate) on an
    unreadable file, a refused symlink, or a parse error: a present-but-unreadable declared source is a
    failure, never an empty pass (the check-fails-closed-on-unreadable rule)."""
    st = _journal._lstat_contained(store_root_fd, relpath)
    if st is None:
        return None
    try:
        raw, _ = _journal._read_contained(store_root_fd, relpath)
    except _journal.JournalError as exc:
        raise ViewsError("cannot read {} ({})".format(relpath, exc))
    try:
        return raw, tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError, RecursionError, ValueError) as exc:
        # RecursionError: deeply nested TOML (e.g. hundreds of nested arrays) trips tomllib's recursive
        # descent; it is NOT a TOMLDecodeError, so catch it (and defensively ValueError) here and map it to
        # a cannot-evaluate rather than letting an uncontrolled traceback escape. This is U4's OWN tomllib
        # parse on the store's TOML, distinct from any emitter recursion elsewhere.
        raise ViewsError("cannot parse {} ({})".format(relpath, exc))


def _load_records(store_root_fd, relpath, type_name, registered_vendors, registered_kinds):
    """Load and validate the `[[record]]` array of one baseline type's inline index (spec 8, Appendix A).
    A MISSING declared index is a cannot-evaluate (a view that declares the type expects its index to
    exist); a PRESENT index whose schema, shape, or any record is malformed is a cannot-evaluate that
    names the record; a present index with zero records is a VALID EMPTY set."""
    got = _read_raw_and_parsed(store_root_fd, relpath)
    if got is None:
        raise ViewsError("declared source {} is missing (a view declares type {!r}, so its index must "
                         "exist; an empty type is an empty index, not an absent one)".format(relpath, type_name))
    raw, data = got
    if not isinstance(data, dict):
        raise ViewsError("{} is not a table".format(relpath))
    extra = set(data) - {"schema", "record"}
    if extra:
        raise ViewsError("{} has unknown top-level key(s): {}".format(relpath, ", ".join(sorted(extra))))
    if "schema" in data and data.get("schema") != SCHEMA_VERSION:
        raise ViewsError("{} schema {!r} is not the supported schema {} (fail-closed)".format(
            relpath, data.get("schema"), SCHEMA_VERSION))
    records = data.get("record", [])
    if not isinstance(records, list):
        raise ViewsError("{}: [[record]] is not an array of tables".format(relpath))
    out = []
    for i, rec in enumerate(records):
        rv = _opf_schema.validate_record(rec, expected_type=type_name,
                                         registered_vendors=registered_vendors,
                                         registered_kinds=registered_kinds)
        if rv.status != _opf_store.VALID:
            raise ViewsError("{}: record #{} ({}) is malformed: {}".format(
                relpath, i + 1, rv.id or "?", "; ".join(rv.findings)))
        out.append(rec)
    return raw, out


def _load_worklog(store_root_fd, relpath, registered_vendors, registered_kinds):
    """Load and validate worklog.toml (spec 6.2); return (raw_bytes, [entry, ...]) in file order."""
    got = _read_raw_and_parsed(store_root_fd, relpath)
    if got is None:
        raise ViewsError("declared source {} is missing (the worklog ledger must exist)".format(relpath))
    raw, data = got
    wv = _opf_release.validate_worklog(data, registered_vendors=registered_vendors,
                                       registered_kinds=registered_kinds)
    if wv.status != _opf_store.VALID:
        raise ViewsError("{} is malformed: {}".format(relpath, "; ".join(wv.findings)))
    return raw, list(data.get("entry", []) if isinstance(data.get("entry"), list) else [])


def _load_version(store_root_fd, relpath):
    """Load and validate version.toml (spec 6.1); return (raw_bytes, releases, summaries) in file order."""
    got = _read_raw_and_parsed(store_root_fd, relpath)
    if got is None:
        raise ViewsError("declared source {} is missing (the version ledger must exist)".format(relpath))
    raw, data = got
    vv = _opf_release.validate_version(data)
    if vv.status != _opf_store.VALID:
        raise ViewsError("{} is malformed: {}".format(relpath, "; ".join(vv.findings)))
    releases = data.get("release", []) if isinstance(data.get("release"), list) else []
    summaries = data.get("summary", []) if isinstance(data.get("summary"), list) else []
    return raw, releases, summaries


# --- the generated header (spec 10.3) ----------------------------------------------------------------

def _source_set_digest(source_blobs):
    """A deterministic digest over a view's source set: each source's store-relative path and its raw
    bytes, in sorted-path order, length-framed so no two source sets can collide. This is the header's
    source-set digest; a hand-edit to any source changes it and a regenerate is then required."""
    hasher = hashlib.sha256()
    for relpath in sorted(source_blobs):
        raw = source_blobs[relpath]
        hasher.update(relpath.encode("utf-8"))
        hasher.update(b"\0")
        hasher.update(str(len(raw)).encode("ascii"))
        hasher.update(b"\0")
        hasher.update(raw)
    return "sha256:" + hasher.hexdigest()


def _header(source_blobs):
    """The do-not-edit header block (spec 10.3): a leading HTML comment, invisible in rendered markdown,
    naming the source paths, the schema and generator versions, the source-set digest, and the regenerate
    command. It carries NO timestamp, so a re-render of unchanged sources is byte-identical. EVERY value
    interpolated into the comment (each source path, whose machine-dir component is DISCOVERED and could
    bear `-->`, and every other field) is passed through _html_comment_safe so no value can close the
    comment early and turn the remainder into active HTML (B2)."""
    safe = _html_comment_safe
    sources = ", ".join(safe(p) for p in sorted(source_blobs)) if source_blobs else "(none)"
    return (
        "<!-- GENERATED by {cmd} (tools/_opf_views.py). DO NOT EDIT; edit the store and regenerate.\n"
        "sources: {sources}\n"
        "schema: {schema}; generator: {gen}/{genver}\n"
        "source-set-digest: {digest}\n"
        "regenerate: {cmd}\n"
        "-->\n"
    ).format(cmd=safe(REGEN_COMMAND), sources=sources, schema=safe(str(SCHEMA_VERSION)),
             gen=safe(GENERATOR_NAME), genver=safe(GENERATOR_VERSION),
             digest=safe(_source_set_digest(source_blobs)))


# --- record field helpers ----------------------------------------------------------------------------

def _state(record):
    """The state token of a record's status (the part before any `/proposed` qualifier, spec 8.4)."""
    return str(record.get("status", "")).split("/", 1)[0]


def _is_proposed(record):
    """True when a record's status carries the `/proposed` qualifier (spec 8.4)."""
    return str(record.get("status", "")).endswith("/proposed")


def _id_key(record):
    """A numeric sort key for a record ID (namespace, number), so IDs order by number not lexically
    (BI-2 before BI-10). A malformed ID sorts last under a sentinel, but records are validated before
    they reach a renderer, so this is defence in depth."""
    shape = _opf_schema._valid_id_shape(record.get("id"))
    return shape if shape is not None else ("~~", 1 << 62)


def _links_of(record, rel):
    """The link target IDs of a record for one closed-vocabulary relation (spec 8.6)."""
    out = []
    for link in record.get("links", []) or []:
        if isinstance(link, dict) and link.get("rel") == rel and isinstance(link.get("id"), str):
            out.append(link["id"])
    return out


def _sorted_ids(ids):
    """Dedup a set of record IDs and order them by the module's numeric record-id key (`_id_key`), so an
    annotation that joins a set of ids reads BL-2 before BL-10 with no duplicate (MAJOR-1). Non-string
    entries are dropped. This is the SINGLE ordering used by every id-set annotation join in the module
    (block-actionability, decision supersession, done receipts), so none of them re-sorts lexically or
    appends a duplicate per scope/link occurrence."""
    return sorted({i for i in ids if isinstance(i, str)}, key=lambda i: _id_key({"id": i}))


# --- the closed transform vocabulary (spec 10.2) -----------------------------------------------------

# The CLOSED, versioned transform vocabulary (spec 10.2). Every predicate, sort key, group field, and
# projection column a renderer may name is registered here; a name outside a registry is a ViewsError
# (a cannot-evaluate), never a silent empty group, empty projection, or "" sort key. TRANSFORM_VOCAB
# bumps when the set changes (a new composition shape is a spec version bump, not ad hoc logic).
TRANSFORM_VOCAB_VERSION = "2"

# group fields resolve through an accessor so a DECLARED derived field (lifecycle state) is part of the
# closed vocabulary rather than an ad hoc bypass; a raw field reads r.get(field).
_GROUP_ACCESSORS = {
    "status": lambda r: str(r.get("status", "")),
    "state": _state,                      # declared derived lifecycle field (spec 8.4)
}
GROUP_FIELDS = frozenset(_GROUP_ACCESSORS)
SORT_KEYS = frozenset({"status"})
PROJECT_COLUMNS = frozenset({
    "type", "status", "title", "created_at", "updated_at", "date", "kind", "severity",
    "decision", "decided_at", "decided_by", "classification", "action", "scopes",
})
# named predicate factories (t_filter takes a NAME, not an arbitrary callable). The inner lambdas look
# up is_actionable / _state at CALL time, so this dict can be defined before is_actionable below.
# `id_in` selects by membership in a caller-supplied ID set (the decision-resolution join's `current`
# and `superseded` sets), so render_decisions routes its effective/superseded selections through the
# CLOSED vocabulary rather than an ad hoc in-line predicate (F5/M1).
_FILTER_PREDICATES = {
    "is_actionable": lambda hidden: (lambda r: is_actionable(r, hidden)),
    "state_in": lambda states: (lambda r: _state(r) in states),
    "id_in": lambda ids: (lambda r: r.get("id") in ids),
    # A `/proposed` record is NONTERMINAL (spec 8.4): these split a set into the proposals awaiting
    # ratification and the settled (unqualified) records, so a view surfaces a proposal DISTINCTLY rather
    # than under its effective/terminal state, and drops no valid record. `_is_proposed` is looked up at
    # CALL time (like is_actionable/_state above), so these entries can be registered before it is needed.
    "is_proposed": lambda: (lambda r: _is_proposed(r)),
    "not_proposed": lambda: (lambda r: not _is_proposed(r)),
}
FILTER_PREDICATES = frozenset(_FILTER_PREDICATES)


def t_filter(records, name, **kwargs):
    """Filter on a DECLARED, named predicate from the closed vocabulary (spec 10.2). `name` selects a
    registered predicate factory; `kwargs` binds its context. An unknown predicate name is a
    ViewsError, never a silent pass-through of an arbitrary callable."""
    factory = _FILTER_PREDICATES.get(name)
    if factory is None:
        raise ViewsError("unknown filter predicate {!r} (spec 10.2 closed transform vocabulary "
                         "v{})".format(name, TRANSFORM_VOCAB_VERSION))
    predicate = factory(**kwargs)
    return [r for r in records if predicate(r)]


def t_sort(records, keys=()):
    """Sort on DECLARED keys with the record ID as the FINAL tie-breaker (spec 10.2). Each key must be
    a registered sort key; an unknown key is a ViewsError, never a silent "" comparison."""
    for k in keys:
        if k not in SORT_KEYS:
            raise ViewsError("unknown sort key {!r} (spec 10.2 closed transform vocabulary v{})".format(
                k, TRANSFORM_VOCAB_VERSION))
    return sorted(records, key=lambda r: tuple(str(r.get(k, "")) for k in keys) + (_id_key(r),))


def t_group(records, field, order=None):
    """Group by a DECLARED field (spec 10.2), which may be a raw record field or a declared derived
    field (lifecycle state). An unknown group field is a ViewsError, never a silent empty-name group.
    Returns (value, records) pairs; `order` fixes the leading group order, remaining values follow
    sorted; records within a group are ID-sorted."""
    accessor = _GROUP_ACCESSORS.get(field)
    if accessor is None:
        raise ViewsError("unknown group field {!r} (spec 10.2 closed transform vocabulary v{})".format(
            field, TRANSFORM_VOCAB_VERSION))
    buckets = {}
    for r in records:
        buckets.setdefault(str(accessor(r)), []).append(r)
    keys = list(order or ())
    keys += sorted(k for k in buckets if k not in keys)
    return [(k, t_sort(buckets[k])) for k in keys if k in buckets]


def t_project(record, columns):
    """Project DECLARED columns (spec 10.2). Each requested column must be a registered projection
    column; an unknown column is a ViewsError, never a silent []. A registered column the record does
    not carry is still omitted (a stable declared column set), which is distinct from an unregistered
    column name."""
    for c in columns:
        if c not in PROJECT_COLUMNS:
            raise ViewsError("unknown projection column {!r} (spec 10.2 closed transform vocabulary "
                             "v{})".format(c, TRANSFORM_VOCAB_VERSION))
    return [(c, record[c]) for c in columns if c in record]


def join_actionability(items, blocks):
    """The block-actionability join (spec 8.5): an UNQUALIFIED `active` block HIDES every record it scopes.
    Returns (blocked_by, hidden): blocked_by maps a scoped record ID to the block IDs that scope it, DEDUPED
    and ordered by the numeric record-id key (BL-2 before BL-10, MAJOR-1), and hidden is the set of all
    scoped IDs. A block that is `active/proposed` (a proposal, not a grant) or in a terminal state
    (`released`, `expired`) never hides anything, so only a ratified active block counts, exactly as the
    actionability rule requires."""
    blocked_by = {}
    for b in blocks:
        if _state(b) == "active" and not _is_proposed(b):
            for sid in b.get("scopes", []) or []:
                if isinstance(sid, str):
                    blocked_by.setdefault(sid, []).append(b.get("id"))
    for sid in blocked_by:
        blocked_by[sid] = _sorted_ids(blocked_by[sid])   # dedup + numeric id order (BL-2 before BL-10, MAJOR-1)
    return blocked_by, set(blocked_by)


def is_actionable(item, hidden):
    """A backlog item is actionable when its state is `open` or `active` and no unqualified active block
    scopes it (spec 8.5). `hidden` is the scoped-ID set from join_actionability."""
    return _state(item) in ("open", "active") and item.get("id") not in hidden


def _detect_resolution_cycle(supersedes_map):
    """Return one ID lying on a supersedes cycle, or None when the graph is acyclic. An iterative
    three-colour DFS (never recursion), so a very long chain cannot exhaust the interpreter stack and a
    cycle is found in bounded time rather than looping unboundedly (spec 8.5: a supersession chain is
    acyclic). GREY marks a node on the current DFS path; an edge back to a GREY node is a cycle, and a
    self-supersede (an edge from a node to itself) is caught as such."""
    WHITE, GREY, BLACK = 0, 1, 2
    color = dict((n, WHITE) for n in supersedes_map)
    for start in supersedes_map:
        if color[start] != WHITE:
            continue
        stack = [(start, 0)]
        while stack:
            node, idx = stack[-1]
            if idx == 0:
                color[node] = GREY
            targets = supersedes_map.get(node, ())
            if idx < len(targets):
                stack[-1] = (node, idx + 1)
                nxt = targets[idx]
                nxt_color = color.get(nxt, BLACK)
                if nxt_color == GREY:
                    return nxt                            # back-edge to a node on the current path: a cycle
                if nxt_color == WHITE:
                    stack.append((nxt, 0))
            else:
                color[node] = BLACK
                stack.pop()
    return None


def join_resolution(decisions):
    """The decision-resolution supersession-chain join (spec 8.5): a decided decision may be superseded by
    a newer one linking `supersedes`, and exactly one current effective resolution exists per chain.
    Returns (current, superseded, supersedes_map): current is the set of decision IDs not superseded by any
    other decision in the set (the chain heads), superseded is the complementary set, and supersedes_map
    maps each decision ID to the sorted IDs it directly supersedes. A FORK (a decision superseded by more
    than one other), a CYCLE, or any chain resolving to more than one head is an INVALID graph and raises
    ViewsError (a cannot-evaluate), never returned as multiple current heads; the single-head happy path
    returns unchanged."""
    supersedes_map = {}
    superseded = set()
    ids = {d.get("id") for d in decisions if isinstance(d.get("id"), str)}
    # Build supersedes edges from state == "decided" decisions ONLY, matching the effective/gone filters
    # and this join's fork/cycle ownership: a supersedes link on a non-decided (open/withdrawn) decision
    # does not yet resolve anything, so it must not remove a decided effective resolution from `current`
    # and mislabel it "Superseded". Cross-record resolution GRADING otherwise remains U6 validate_store's
    # remit (F2); this closes only the mislabel window. The all-decided case is unchanged.
    for d in decisions:
        did = d.get("id")
        if _state(d) != "decided":
            continue
        targets = _sorted_ids(t for t in _links_of(d, "supersedes") if t in ids)
        supersedes_map[did] = targets
        superseded.update(targets)
    # Spec 8.5/10.2: exactly ONE current effective resolution per supersession chain. A FORK (one decision
    # superseded by more than one other) forces a chain node with in-degree > 1 and leaves the chain with
    # two heads; refusing that, together with refusing a CYCLE, is equivalent to requiring one head per
    # connected chain, since any two-head connected chain must contain such an in-degree > 1 node. Both are
    # cannot-evaluate: fail closed rather than render an invalid DECISIONS view with two effective heads.
    indegree = {}
    for _did, targets in supersedes_map.items():
        for t in targets:
            indegree[t] = indegree.get(t, 0) + 1
    forked = sorted(t for t, count in indegree.items() if count > 1)
    if forked:
        raise ViewsError("decision resolution graph forks: {} superseded by more than one decision "
                         "(spec 8.5 requires one current effective resolution per chain); fail-closed"
                         .format(", ".join(forked)))
    cycle_id = _detect_resolution_cycle(supersedes_map)
    if cycle_id is not None:
        raise ViewsError("decision resolution graph has a supersedes cycle at {} (spec 8.5: a supersession "
                         "chain is acyclic); fail-closed".format(cycle_id))
    current = {d.get("id") for d in decisions if isinstance(d.get("id"), str)} - superseded
    return current, superseded, supersedes_map


# --- view body renderers -----------------------------------------------------------------------------
#
# Each renderer takes a `src` dict mapping a declared source name to its loaded records (or, for the
# worklog/version ledgers, their loaded rows) and returns the markdown BODY (the header is prepended by
# the driver, except for the header-exempt root VERSION deliverable). Bodies are deterministic: every
# iteration is over an explicitly sorted or grouped sequence.

# F4 class-wide probe (spec 10.3): every record/ledger field value interpolated into a rendered body
# passes through _md_text, so a free-text field cannot forge a heading, a list item, an active link, an
# HTML comment, or any other inline Markdown/HTML structure. Author-declared probe token, reconciled by
# hand against these renderers (the file is small):
#   grep -nE 'r\[|r\.get\(|e\.get\(|s\.get\(|ref\.get\(|actor\[' tools/_opf_views.py
#   then confirm every hit reaching an output/format string is wrapped in _md_text.
# Current run of this probe shows ZERO unescaped record-field sinks in a rendered body. Schema-
# constrained fields (id, status/state, timestamps, scopes/link ids) are routed through _md_text too, as
# defence in depth per guard-input-soundness; _md_text is a no-op on a conformant value that carries no
# Markdown metacharacter, so the only conformant value it alters is one whose text legitimately contains
# a metacharacter (e.g. the word-internal '_' of a type name like `backlog_item`, escaped to render
# identically), which the mirror goldens pin exactly.

_EMPTY = "_No records._"


def _lines(title, body_lines):
    """Assemble a view body: a level-1 heading, a blank line, and either the body lines or the empty
    marker. Always ends with exactly one trailing newline."""
    body = "\n".join(body_lines) if body_lines else _EMPTY
    return "# {}\n\n{}\n".format(title, body)


_MD_CTRL_RE = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# CommonMark 2.4 backslash-escapable ASCII punctuation that is structurally ACTIVE for a free-text value
# in this sink's position. A view value is a single line (newlines are collapsed below) always emitted
# behind a structural prefix (`- `, `## `, `<col>: `), never at column 0. In that position the active
# inline constructs are emphasis/strong (* _), code spans (`), links, images and reference definitions
# ([ ] ( )), and the GFM table (|) and strikethrough (~) marks; the list/heading/quote lead-ins (# + !)
# and grouping braces ({ }) are escaped too, along with the escaping backslash itself. A backslash before
# any of these renders it as that literal character with NO visible backslash (CommonMark 2.4), so
# `[pwn](url)`, `*bold*`, backtick-code, `|pipe|`, `# head`, `!img`, `~strike~`, autolinks, and
# reference-style links all render inert. The HTML metacharacters & < > are entity-escaped separately
# below (belt-and-suspenders against a raw-HTML sink), so they are not in this set. Characters that carry
# NO inline meaning in this always-prefixed, single-line position (-, ., :, /) are deliberately left
# unescaped: no block construct can start below column 0 and no inline construct uses them once brackets
# and pipes are neutralized, so escaping them would change no rendered output while churning every id,
# timestamp, and SemVer the sink also carries (disclose-guard-residuals: this boundary is stated, not
# implied). The class covered is therefore complete for this sink's grammar, not a best-effort denylist.
_MD_ESCAPE_RE = re.compile(r"([\\`*_{}\[\]()#+!|~])")

# GFM EXTENDED autolinks (a GFM extension beyond CommonMark 2.4) turn a BARE trigger into a clickable
# link with no angle brackets: an `http(s)://...` URL, a bare `www.` domain, and a bare `email@domain`
# (GFM spec 6.9, Autolinks (extension)). GitHub renders these with a POST-PROCESS that runs over the
# parsed inline tree AFTER HTML entity references are resolved into text and adjacent text nodes are
# consolidated, so replacing a trigger character with a numeric character reference (`&#46;`, `&#58;`,
# `&#64;`) does NOT prevent the autolink: cmark-gfm decodes the reference back to the literal character
# and then autolinks it. The post-process does, however, scan only TEXT nodes and never CODE nodes, so a
# trigger inside a CODE SPAN is never autolinked. A URL/email is therefore neutralized ROBUSTLY and
# renderer-agnostically by wrapping it in a code span (backticks): every character is preserved
# byte-for-byte (only the font becomes monospace); the WRAP itself introduces only backticks and spaces
# (no zero-width or bidi character), but it preserves whatever codepoints the token already carried, so
# byte-canon-clean OUTPUT is guaranteed not here but by the view generator's whole-body fail-closed scan
# (see _render_resolved); a raw `<` inside the token is rendered inert too.
# The matcher is a deliberate SUPERSET of GFM's three trigger grammars, so every form GFM would autolink
# -- and any additional URL scheme its extension may recognise -- is wrapped; over-wrapping a token GFM
# would not have autolinked only renders it monospace, never leaves it active. A URL/www token ends at
# whitespace or `<` (mirroring where GFM ends a URL autolink), so a following `<tag>` is left to the
# `<`/`>` entity escaping rather than swallowed into the span.
# Named autolink fragments: production (_MD_AUTOLINK_TOKEN_RE) composes these three arms, and the test
# oracle compiles the SAME www source, so the two www matchers cannot drift (round-5 QA: identity by
# construction). Each arm is its own named constant so the self-test can FREEZE it against a reviewed
# literal: any edit to a neutralizer arm (url/www/email, not www alone) breaks a frozen pin, closing the
# whole matcher-edit class (round-8 QA found the url/email arms unfrozen). cmark-gfm does not autolink www
# after an alphanumeric, so the not-after-alnum boundary matches cmark-gfm + production; [^\s<]+ accepts
# Unicode hosts.
_URL_AUTOLINK_FRAGMENT = r"[A-Za-z][A-Za-z0-9+.\-]*://[^\s<]+"        # any scheme URL (http/https/...)
_WWW_AUTOLINK_FRAGMENT = r"(?<![A-Za-z0-9])www\.[^\s<]+"              # bare www. autolink (shared fragment)
_EMAIL_AUTOLINK_FRAGMENT = r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9_\-]+(?:\.[A-Za-z0-9_\-]+)+"  # bare email address
_MD_AUTOLINK_TOKEN_RE = re.compile(
    "(" + _URL_AUTOLINK_FRAGMENT + "|" + _WWW_AUTOLINK_FRAGMENT + "|" + _EMAIL_AUTOLINK_FRAGMENT + ")",
    re.IGNORECASE | re.VERBOSE,
)


def _md_escape_inline(s):
    """CommonMark/HTML escape for a NON-autolink plain-text segment: backslash-escape every
    structurally-active inline Markdown metacharacter (rendering it as the literal character with no
    visible backslash), then entity-escape the HTML metacharacters `&`, `<`, `>` so an embedded `<!--`
    cannot open a comment and a `<tag>` cannot pass through as raw HTML. Order is load-bearing: the
    backslash pass never emits `&`/`<`/`>`, and the entity pass runs after it. A segment carrying none of
    these renders byte-for-byte unchanged. This is byte-identical to the pre-F-U4AUTOLINK escape passes,
    so every non-autolink value (every id, timestamp, SemVer, and the mixed-metacharacter goldens)
    renders exactly as before."""
    s = _MD_ESCAPE_RE.sub(r"\\\1", s)
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _md_code_span(token):
    """Wrap a raw autolink token in a CommonMark code span so a GFM renderer treats it as inert CODE
    (never an autolink or raw HTML), preserving every character. The fence is one backtick longer than
    the longest backtick run inside the token (CommonMark 6.3); when the token begins or ends with a
    backtick, a single padding space is added inside each fence (CommonMark strips one leading + trailing
    space from a code span that is not all-spaces), so a token that itself contains backticks still
    renders exactly. The token is a single line (newlines already collapsed upstream) and never
    all-spaces (it contains `://`, `www.`, or `@`), so the code span is well-formed and no padding space
    reaches a line end (it sits before the closing fence, never trailing)."""
    runs = re.findall(r"`+", token)
    fence = "`" * ((max((len(r) for r in runs), default=0)) + 1)
    pad = " " if token.startswith("`") or token.endswith("`") else ""
    return "{f}{p}{t}{p}{f}".format(f=fence, p=pad, t=token)


def _md_text(value):
    """Escape a free-text record field for the markdown/HTML view sink (spec 10.3). A record value is
    DATA, never markdown or HTML structure: it must render as LITERAL text and must forge no heading,
    list item, link, emphasis, code, table cell, raw HTML, header comment, or ACTIVE AUTOLINK. Inline
    constructs are escaped and autolinks code-span-wrapped unconditionally; BLOCK-structure inertness
    rests on the single-line output being emitted behind a structural prefix (never column 0), so a
    leading `-`/`N.`/indent cannot begin a line -- and a first-position sink is additionally guarded by
    the upstream schema constraining every first-position field (id, version, status, covers, span,
    ref.kind), so the free-text fields (title, locator, note, summary) always render mid-line.

    Newlines/carriage returns collapse to a single space (a view field is one line) and other C0/DEL
    control characters are dropped (U+0009 TAB is preserved as inline whitespace, inert mid-line); then
    the value is split on the GFM extended-autolink triggers. Each
    autolink token (a bare URL, `www.` domain, or email) is wrapped in a CODE SPAN, which a GFM renderer
    never autolinks and never treats as raw HTML, so the address renders as inert monospace text with
    every character preserved. Every non-token segment is escaped by `_md_escape_inline`. A value with no
    autolink trigger takes only the escape path and is byte-identical to the pre-F-U4AUTOLINK output.

    Adjacent autolink tokens with no text between them (an email whose `+` starts the next address, say)
    are COALESCED into ONE code span over the original contiguous substring, so two abutting spans never
    put a closing and an opening fence flush and form a two-backtick run that CommonMark reads as a single
    span with the fence backticks left visible inside it (fidelity: every character preserved).

    Disclosed residual (disclose-guard-residuals): a neutralized URL/email renders in MONOSPACE (a code
    span) rather than proportional text; its characters are byte-identical, only the font differs. The
    matcher is a superset of GFM's three trigger grammars (any `scheme://`, `www.`, `local@domain`), so
    schemes beyond http/https are wrapped too. The CommonMark angle-bracket autolink `<scheme:...>` is
    still handled by the `<`/`>` entity escaping in `_md_escape_inline`, not here. This transform strips
    only C0/DEL controls; it does NOT strip the zero-width/bidi codepoints the store owner's own free text
    may carry, and (like every field) preserves them inside the wrap. Byte-canon-clean OUTPUT is therefore
    guaranteed NOT by this sink but by the view generator, which scans every fully-assembled view body with
    the authoritative check_byte_canon.scan_bytes and FAILS CLOSED (cannot-evaluate) before any drift
    compare or write, so a forbidden codepoint refuses the render rather than reaching a view (see
    _render_resolved)."""
    s = str(value)
    s = s.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")
    s = _MD_CTRL_RE.sub("", s)
    # Coalesce CONTIGUOUS autolink matches (no text between them) into ONE code span over the original
    # substring: two abutting code spans would sit fence-to-fence, forming a two-backtick run CommonMark
    # reads as a single span with the fences left visible inside it (C). Merging keeps one clean span over
    # the raw run, every character preserved and no autolink surviving.
    spans = []
    for m in _MD_AUTOLINK_TOKEN_RE.finditer(s):
        if spans and m.start() == spans[-1][1]:
            spans[-1] = (spans[-1][0], m.end())
        else:
            spans.append((m.start(), m.end()))
    out = []
    pos = 0
    for start, end in spans:
        out.append(_md_escape_inline(s[pos:start]))
        out.append(_md_code_span(s[start:end]))
        pos = end
    out.append(_md_escape_inline(s[pos:]))
    return "".join(out)


# The generated header (spec 10.3) interpolates DISCOVERED source paths (the machine-dir component is NOT
# restricted to `toml`) into an HTML comment. A comment is closed ONLY by a `--`-led sequence (`-->`, or
# the abrupt `--!>`), so a value bearing `-->` could close the comment early and turn the remainder into
# active HTML (B2). Entities do NOT decode inside a comment, so `&gt;`-style escaping is inert here; the
# sink is made safe by ALTERING the characters themselves. Breaking every `--` run (inserting a space after
# each hyphen that is immediately followed by another) makes both `-->` and `--!>` impossible to form, and
# newlines/controls collapse so a value cannot inject a fake header field line. A legitimate source path
# carries no `--`, so this is a no-op on it (disclose-guard-residuals: the transform alters only a
# `--`-bearing or control-bearing value, minimally; the header's own terminating `-->` is emitted by
# _header, never by an interpolated value, so exactly one `-->` remains in a well-formed header).
_MD_COMMENT_DASHES_RE = re.compile(r"-(?=-)")


def _html_comment_safe(value):
    """Encode a value for the HTML-comment sink of the generated header (spec 10.3), so no interpolated
    value (a discovered source path or machine-dir component especially) can close the comment early. See
    the note above _MD_COMMENT_DASHES_RE for the mechanism and its disclosed residual. Newlines/CR collapse
    to a space and other C0/DEL controls are dropped (U+0009 TAB is preserved as inert whitespace inside the
    comment), so a value cannot inject a fake header field line or an early closer."""
    s = str(value)
    s = s.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")
    s = _MD_CTRL_RE.sub("", s)
    return _MD_COMMENT_DASHES_RE.sub("- ", s)


def render_todo(src):
    """TODO: the actionable open and active backlog items (block-actionability join, then a state filter,
    then an ID-tie-broken sort). An item an unqualified active block scopes is hidden."""
    items, blocks = src["backlog_item"], src["block"]
    _blocked_by, hidden = join_actionability(items, blocks)
    actionable = t_filter(items, "is_actionable", hidden=hidden)
    ordered = t_sort(actionable, keys=("status",))
    lines = ["- {} ({}) {}".format(_md_text(r["id"]), _md_text(_state(r)), _md_text(r.get("title", "")))
             for r in ordered]
    return _lines("TODO", lines)


def render_backlog(src):
    """BACKLOG: every backlog item in ID order, each annotated by the block-actionability join as
    actionable or blocked by the naming block(s)."""
    items, blocks = src["backlog_item"], src["block"]
    blocked_by, hidden = join_actionability(items, blocks)
    lines = []
    for r in t_sort(items):
        note = "actionable" if is_actionable(r, hidden) else (
            "blocked by {}".format(", ".join(_md_text(b) for b in blocked_by[r["id"]])) if r.get("id") in hidden
            else "not actionable ({})".format(_md_text(_state(r))))
        lines.append("- {} ({}) {} -- {}".format(
            _md_text(r["id"]), _md_text(r.get("status", "")), _md_text(r.get("title", "")), note))
    return _lines("BACKLOG", lines)


def render_pipeline(src):
    """PIPELINE: one line per backlog item, grouped by the DECLARED derived lifecycle state (spec
    10.2/8.4) in lifecycle order, with a blocked marker from the block-actionability join. A `/proposed`
    item is NONTERMINAL (spec 8.4): it is held OUT of its effective-state group and surfaced in a trailing
    `awaiting ratification` section showing its FULL status, never mis-grouped under its proposed target
    state. The settled/proposed split and the state grouping both run through the closed transform
    vocabulary (registered predicates and a registered derived group field), not ad hoc generator logic."""
    items, blocks = src["backlog_item"], src["block"]
    blocked_by, hidden = join_actionability(items, blocks)

    def _marker(r):
        return " [blocked by {}]".format(", ".join(_md_text(b) for b in blocked_by[r["id"]])) \
            if r.get("id") in hidden else ""

    out = []
    for state, group in t_group(t_filter(items, "not_proposed"), "state",
                                order=("open", "active", "done", "dropped")):
        out.append("## {}".format(_md_text(state)))
        for r in group:
            out.append("- {} {}{}".format(_md_text(r["id"]), _md_text(r.get("title", "")), _marker(r)))
        out.append("")
    proposed = t_sort(t_filter(items, "is_proposed"))
    if proposed:
        out.append("## awaiting ratification")
        for r in proposed:
            out.append("- {} ({}) {}{}".format(
                _md_text(r["id"]), _md_text(r.get("status", "")), _md_text(r.get("title", "")), _marker(r)))
        out.append("")
    body = "\n".join(out).rstrip("\n") if out else _EMPTY
    return "# {}\n\n{}\n".format("PIPELINE", body)


def render_done(src):
    """DONE: the completion receipts in ID order, each naming the backlog item it is a receipt of (the
    receipt IDs deduped and ordered by the numeric record-id key, like every id-set annotation join)."""
    lines = []
    for r in t_sort(src["done"]):
        recs = _sorted_ids(_links_of(r, "receipt_of"))
        suffix = " (receipt_of {})".format(", ".join(_md_text(x) for x in recs)) if recs else ""
        lines.append("- {} {}{}".format(_md_text(r["id"]), _md_text(r.get("title", "")), suffix))
    return _lines("DONE", lines)


def render_findings(src):
    """FINDINGS: findings grouped by status, then ID-sorted, each with its graded severity when present."""
    out = []
    for status, group in t_group(src["finding"], "status"):
        out.append("## {}".format(_md_text(status)))
        for r in group:
            sev = " (severity: {})".format(_md_text(r["severity"])) if "severity" in r else ""
            out.append("- {}{} {}".format(_md_text(r["id"]), sev, _md_text(r.get("title", ""))))
        out.append("")
    body = "\n".join(out).rstrip("\n") if out else _EMPTY
    return "# {}\n\n{}\n".format("FINDINGS", body)


def render_decisions(src):
    """DECISIONS: the pending decisions (open), the effective resolutions (the head of each supersedes
    chain), the superseded resolutions, the withdrawn decisions, the proposals awaiting ratification, and
    the autonomous decisions, via the decision-resolution join. A `/proposed` decision is NONTERMINAL
    (spec 8.4): it is EXCLUDED from the ratified supersession graph and from the effective/superseded sets,
    and surfaced under `Awaiting ratification` with its FULL status; an unqualified `withdrawn` decision is
    a valid terminal, listed under `Withdrawn decisions`, never dropped. Every valid pending_decision
    status (open, decided head, decided superseded, withdrawn, decided/proposed, withdrawn/proposed) thus
    lands in exactly one place."""
    pend, auto = src["pending_decision"], src["autonomous_decision"]
    settled = t_filter(pend, "not_proposed")
    proposed = t_sort(t_filter(pend, "is_proposed"))
    # The ratified supersession graph is over the SETTLED decisions only: a proposal does not yet supersede
    # anything, so it neither becomes an effective head nor removes another decision from `current`.
    current, superseded, supersedes_map = join_resolution(settled)
    decided = t_filter(settled, "state_in", states=("decided",))
    open_pd = t_sort(t_filter(settled, "state_in", states=("open",)))
    effective = t_sort(t_filter(decided, "id_in", ids=current))
    gone = t_sort(t_filter(decided, "id_in", ids=superseded))
    withdrawn = t_sort(t_filter(settled, "state_in", states=("withdrawn",)))
    out = ["## Pending decisions"]
    out += (["- {} {}".format(_md_text(r["id"]), _md_text(r.get("title", ""))) for r in open_pd] or [_EMPTY])
    out += ["", "## Effective resolutions"]
    if effective:
        for r in effective:
            sup = supersedes_map.get(r["id"]) or []
            tail = " (supersedes {})".format(", ".join(_md_text(s) for s in sup)) if sup else ""
            out.append("- {} {}{}".format(_md_text(r["id"]), _md_text(r.get("title", "")), tail))
    else:
        out.append(_EMPTY)
    out += ["", "## Superseded resolutions"]
    out += (["- {} {}".format(_md_text(r["id"]), _md_text(r.get("title", ""))) for r in gone] or [_EMPTY])
    if withdrawn:
        out += ["", "## Withdrawn decisions"]
        out += ["- {} {}".format(_md_text(r["id"]), _md_text(r.get("title", ""))) for r in withdrawn]
    if proposed:
        out += ["", "## Awaiting ratification"]
        out += ["- {} ({}) {}".format(
            _md_text(r["id"]), _md_text(r.get("status", "")), _md_text(r.get("title", ""))) for r in proposed]
    out += ["", "## Autonomous decisions"]
    out += (["- {} {}".format(_md_text(r["id"]), _md_text(r.get("title", ""))) for r in t_sort(auto)] or [_EMPTY])
    return "# {}\n\n{}\n".format("DECISIONS", "\n".join(out))


def render_blocks(src):
    """BLOCKS: every block in ID order with its status, the records it scopes, and a proposed marker."""
    lines = []
    for r in t_sort(src["block"]):
        scopes = ", ".join(_md_text(s) for s in (r.get("scopes", []) or []))
        lines.append("- {} ({}) scopes [{}] -- {}".format(
            _md_text(r["id"]), _md_text(r.get("status", "")), scopes, _md_text(r.get("title", ""))))
    return _lines("BLOCKS", lines)


def render_handoff(src):
    """HANDOFF: the single current handoff first (at most one exists, spec 8.5), then the superseded ones
    in ID order, and finally any proposal awaiting ratification. A `superseded/proposed` handoff is
    NONTERMINAL (spec 8.4): it is held OUT of the superseded (terminal) list and surfaced under
    `Awaiting ratification` with its FULL status, never shown as already superseded. Every valid handoff
    status (current, superseded, superseded/proposed) thus lands in exactly one place."""
    handoffs = src["handoff"]
    out = ["## Current"]
    cur = t_sort(t_filter(handoffs, "state_in", states=("current",)))
    out += (["- {} {}".format(_md_text(r["id"]), _md_text(r.get("title", ""))) for r in cur] or [_EMPTY])
    out += ["", "## Superseded"]
    old = t_sort(t_filter(t_filter(handoffs, "state_in", states=("superseded",)), "not_proposed"))
    out += (["- {} {}".format(_md_text(r["id"]), _md_text(r.get("title", ""))) for r in old] or [_EMPTY])
    proposed = t_sort(t_filter(handoffs, "is_proposed"))
    if proposed:
        out += ["", "## Awaiting ratification"]
        out += ["- {} ({}) {}".format(
            _md_text(r["id"]), _md_text(r.get("status", "")), _md_text(r.get("title", ""))) for r in proposed]
    return "# {}\n\n{}\n".format("HANDOFF", "\n".join(out))


def render_references(src):
    """REFERENCES: the captured references in ID order, each with its captured refs."""
    lines = []
    for r in t_sort(src["reference"]):
        lines.append("- {} {}".format(_md_text(r["id"]), _md_text(r.get("title", ""))))
        for ref in r.get("refs", []) or []:
            if isinstance(ref, dict):
                lines.append("  - {}: {}".format(
                    _md_text(ref.get("kind", "?")), _md_text(ref.get("locator", ""))))
    return _lines("REFERENCES", lines)


def render_worklog(src):
    """WORKLOG: the durable worklog entries in ID (WL number) order, each with its date, change kind, and
    one-line summary (spec 6.2)."""
    entries = sorted(src["worklog"], key=_id_key)
    lines = ["- {} ({}) [{}] {}".format(
        _md_text(e.get("id")), _md_text(e.get("date", "")), _md_text(e.get("kind", "")),
        _md_text(e.get("summary", ""))) for e in entries]
    return _lines("WORKLOG", lines)


def render_version_md(src):
    """VERSION.md: the optional human view of the version ledger (spec 6.1): each release with its date and
    worklog span, then the changelog summary rows."""
    releases, summaries = src["version"]["releases"], src["version"]["summaries"]
    out = ["## Releases"]
    if releases:
        for r in releases:
            span = r.get("worklog_span", [])
            span_txt = "{}..{}".format(_md_text(span[0]), _md_text(span[1])) if len(span) == 2 else "(none)"
            out.append("- {} ({}) worklog {}".format(
                _md_text(r.get("version")), _md_text(r.get("date", "")), span_txt))
    else:
        out.append(_EMPTY)
    out += ["", "## Changelog summaries"]
    if summaries:
        for s in summaries:
            out.append("- {} ({})".format(_md_text(s.get("covers")), _md_text(s.get("status"))))
    else:
        out.append(_EMPTY)
    return "# {}\n\n{}\n".format("VERSION", "\n".join(out))


def render_version_file(src):
    """The root VERSION deliverable: the LATEST release's version string as EXACT bytes plus a
    trailing LF (spec 6.1/5.8), matching the pack's own generated VERSION. HEADER-EXEMPT: its content
    is exact version bytes, so a do-not-edit header would corrupt it. A declared VERSION with NO
    release to render is a cannot-evaluate (fail-closed), never a zero-byte write: there is no latest
    release whose bytes to emit, so an empty result would be a fabricated, spec-violating VERSION."""
    releases = src["version"]["releases"]
    latest, latest_key = None, None
    for r in releases:
        key = _opf_release.parse_semver(r.get("version"))
        if key is not None and (latest_key is None or key > latest_key):
            latest, latest_key = r.get("version"), key
    if latest is None:
        raise ViewsError("VERSION deliverable declared but the version ledger has no release to "
                         "render (spec 6.1: VERSION is the latest release's SemVer bytes); "
                         "fail-closed, never a zero-byte write")
    return "{}\n".format(latest)


def render_mirror(type_name, records):
    """A 1:1 <TYPE>-INDEX.md mirror (spec 10.1): each record in ID order rendered as a projection of its
    declared columns, mirroring the machine index for human reading. Every projected value is escaped for
    the markdown/HTML sink so a free-text column (decision, classification, action, title, ...) cannot
    forge structure or the do-not-edit header comment."""
    columns = ("type", "status", "title", "created_at", "updated_at", "date", "kind", "severity",
               "decision", "decided_at", "decided_by", "classification", "action", "scopes")
    out = []
    for r in t_sort(records):
        out.append("## {}".format(_md_text(r.get("id"))))
        for col, val in t_project(r, columns):
            if isinstance(val, list):
                val = "[{}]".format(", ".join(_md_text(x) for x in val))
            else:
                val = _md_text(val)
            out.append("- {}: {}".format(col, val))
        actor = r.get("actor")
        if isinstance(actor, dict) and actor.get("kind"):
            out.append("- actor: {}".format(_md_text(actor["kind"])))
        out.append("")
    title = "{} index".format(type_name.upper())
    body = "\n".join(out).rstrip("\n") if out else _EMPTY
    return "# {}\n\n{}\n".format(title, body)


# --- the view registry -------------------------------------------------------------------------------
# Each NAMED view maps its manifest view-name to (kind, required-source-names, renderer). The 1:1 mirror
# views are matched by the <TYPE>-INDEX.md pattern instead, so a mirror is available for any enabled type
# without a per-type registry entry.

NAMED_VIEWS = {
    "TODO.md": ("composed", ("backlog_item", "block"), render_todo),
    "BACKLOG.md": ("composed", ("backlog_item", "block"), render_backlog),
    "PIPELINE.md": ("composed", ("backlog_item", "block"), render_pipeline),
    "DONE.md": ("composed", ("done",), render_done),
    "FINDINGS.md": ("composed", ("finding",), render_findings),
    "DECISIONS.md": ("composed", ("pending_decision", "autonomous_decision"), render_decisions),
    "BLOCKS.md": ("composed", ("block",), render_blocks),
    "HANDOFF.md": ("composed", ("handoff",), render_handoff),
    "REFERENCES.md": ("composed", ("reference",), render_references),
    "WORKLOG.md": ("deterministic", ("worklog",), render_worklog),
    "VERSION.md": ("deterministic", ("version",), render_version_md),
    "VERSION": ("deterministic", ("version",), render_version_file),
}

_MIRROR_RE = re.compile(r"^([A-Z0-9_]+)-INDEX\.md$")


def _mirror_type(view_name):
    """The baseline type a `<TYPE>-INDEX.md` mirror name refers to (spec 10.1), or None when the name is
    not a mirror or names no baseline type."""
    m = _MIRROR_RE.match(view_name)
    if not m:
        return None
    type_name = m.group(1).lower()
    return type_name if type_name in _opf_schema.BASELINE_SPECS else None


def _resolve_view(view_name):
    """Resolve a declared view name to (kind, required-sources, renderer). A mirror name renders through
    render_mirror bound to its type; a named view comes from NAMED_VIEWS. An unrecognized view name is a
    cannot-evaluate: this generator cannot render a view outside the closed vocabulary (spec 10.2), so it
    fails closed rather than silently skipping a declared deliverable."""
    if view_name in NAMED_VIEWS:
        return NAMED_VIEWS[view_name]
    mtype = _mirror_type(view_name)
    if mtype is not None:
        return ("deterministic", (mtype,),
                lambda src, _t=mtype: render_mirror(_t, src[_t]))
    raise ViewsError("view {!r} is not a view this generator renders (spec 10.1/10.2 closed "
                     "vocabulary)".format(view_name))


# --- the driver --------------------------------------------------------------------------------------

def _spec_destination(view_name):
    """The spec-bound destination of a view, derived from the view's OWN identity, never from the manifest
    `target` (spec 5.8/9). Every named view and every <TYPE>-INDEX.md mirror writes to `.working/<name>`
    at the STORE root; the root VERSION deliverable is the one public target, at the PRODUCT root. Returns
    (scope, relpath) where scope is "store" or "product" and relpath is the destination path relative to
    that root. A manifest cannot rebind a known deliverable off this destination: the caller rejects a
    declared target that does not match it, fail-closed."""
    if view_name == "VERSION":
        return "product", "VERSION"
    return "store", "{}/{}".format(WORKING_DIRNAME, view_name)


def _write_contained(root_fd, relpath, text, check):
    """Write `text` (UTF-8) to a contained regular file beneath root_fd, no-follow, or (check mode) return
    True when the on-disk bytes differ (the drift signal). This is U4's OWN view-write primitive, the
    no-follow counterpart of the `_journal` contained READS the module already uses: a symlinked
    destination, a symlinked path component, or a non-regular destination is REFUSED (ViewsError, a
    cannot-evaluate), never followed, so a manifest or a planted link cannot redirect a write off-tree.
    Fail-closed: any read/write error, or a parent directory that cannot be opened, is a ViewsError, never
    a silent skip. Byte-stable: an unchanged target is not rewritten.

    This is the single choke point EVERY view and deliverable write passes through, so the F1 write-gate
    refusal is enforced HERE at the write boundary, not only at the render() entry: while the U6 store-
    integrity gate is uncomposed, no real write can open or truncate a file regardless of which helper
    reached this sink (a direct _write_contained or _render_resolved call included). --check is read-only
    and unaffected. The render() guard is retained as an early, before-any-fd refusal (defence in depth)."""
    if not check and not _WRITE_GATE_COMPOSED:
        raise ViewsError("refusing to write {}: store-integrity gate (U6 validate_store) not composed; a "
                         "mutating render is refused at the write boundary until VC-4 wires the gate "
                         "(fail-closed pending U6, spec 5.7/5.8/11)".format(relpath))
    new_bytes = text.encode("utf-8")
    try:
        pfd, name = _journal._open_parent(root_fd, relpath)
    except OSError as exc:                                 # includes FileNotFoundError (a missing parent dir)
        raise ViewsError("cannot open parent of {} for write ({})".format(relpath, exc))
    try:
        st = _journal._lstat_at(pfd, name)
        if st is not None and not stat.S_ISREG(st.st_mode):
            raise ViewsError("refusing to write {}: destination is not a regular file (a symlink or "
                             "special file is never followed; fail-closed)".format(relpath))
        current = _journal._read_at(pfd, name, relpath)[0] if st is not None else None
        if check:
            return current != new_bytes
        if current == new_bytes:
            return False                                  # already current: byte-stable, no rewrite
        if st is None:
            fd = os.open(name, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o644, dir_fd=pfd)
        else:
            fd = os.open(name, os.O_WRONLY | os.O_TRUNC | os.O_NOFOLLOW, dir_fd=pfd)
        try:
            _journal._write_all(fd, new_bytes)
        finally:
            os.close(fd)
        return False
    except _journal.JournalError as exc:                  # a swapped-in non-regular file at read time
        raise ViewsError("cannot write {} ({})".format(relpath, exc))
    except OSError as exc:                                 # ELOOP (a component/target raced to a symlink), etc.
        raise ViewsError("cannot write {} ({})".format(relpath, exc))
    finally:
        os.close(pfd)


def _check_declared_sources(view_name, required, declared):
    """Validate a view's manifest `sources` against its required set (spec 10.2): `sources` must be a list
    of source-name strings equal AS A SET to `required`, with no missing, extra, duplicate, or ill-typed
    entry. A mismatch is a ViewsError (a cannot-evaluate) naming the view and the offending source(s), so
    an extra declared source, which is otherwise neither read, validated, nor entered into the header
    digest, fails closed rather than being silently ignored."""
    if not isinstance(declared, list) or any(not isinstance(s, str) for s in declared):
        raise ViewsError("view {!r} [views] `sources` must be a list of source-name strings "
                         "(spec 10.2)".format(view_name))
    if len(declared) != len(set(declared)):
        dupes = sorted(s for s in set(declared) if declared.count(s) > 1)
        raise ViewsError("view {!r} declares duplicate source(s) {} (spec 10.2)".format(
            view_name, ", ".join(dupes)))
    declared_set = set(declared)
    required_set = set(required)
    if declared_set != required_set:
        missing = sorted(required_set - declared_set)
        extra = sorted(declared_set - required_set)
        raise ViewsError("view {!r} declared sources {} do not equal its required set {} "
                         "(missing {}, extra {}; spec 10.2)".format(
                             view_name, sorted(declared_set), sorted(required_set), missing, extra))


def _registered_vendors_and_kinds(manifest):
    """The manifest's registered `x-<vendor>` extension namespaces and any additional worklog change kinds,
    passed to the record and worklog validators so a store's declared extensions are accepted (spec 8.7)."""
    vendors = manifest.get("vendors", {})
    registered = frozenset(v for v in (vendors.get("registered", []) if isinstance(vendors, dict) else [])
                           if isinstance(v, str))
    return registered, None


def render(argv):
    """`opf render [--root DIR] [--check]`: render every declared view of the store at a PRODUCT root.
    Returns 0 clean, 1 on drift under --check, 2 cannot-evaluate. NOT-ADOPTED reports NOT APPLICABLE and
    exits 0 (the pack's own `--root .` case). Two-phase like run_generator: every payload is rendered
    before any target is written, so a fail-closed source aborts before a single file is touched."""
    root = None
    check = False
    i = 0
    while i < len(argv):
        tok = argv[i]
        if tok == "--check":
            check = True
            i += 1
        elif tok == "--root":
            if i + 1 >= len(argv):
                print("opf render: --root requires a directory argument", file=sys.stderr)
                return EXIT_CANNOT_EVALUATE
            if root is not None:
                print("opf render: --root given more than once", file=sys.stderr)
                return EXIT_CANNOT_EVALUATE
            val = argv[i + 1]
            if val == "" or val.startswith("-"):
                # A directory argument is required: an empty string (which would normalize to the cwd) or
                # an option-looking token (a swallowed next flag, e.g. `--root --check`) is a parser-level
                # cannot-evaluate, never a silent default-to-cwd or a consumed option (fail-closed CLI, B3).
                print("opf render: --root requires a non-empty directory argument, not {!r}".format(val),
                      file=sys.stderr)
                return EXIT_CANNOT_EVALUATE
            root = val
            i += 2
        else:
            print("opf render: unrecognized argument {!r}".format(tok), file=sys.stderr)
            return EXIT_CANNOT_EVALUATE
    if root is None:
        root = "."
    product_root = Path(os.path.abspath(root))

    res = _opf_store.resolve_store(product_root)
    if res.status == _opf_store.NOT_ADOPTED:
        print("opf render: NOT APPLICABLE ({} is not a DevProcess adopter root; {})".format(
            product_root, res.detail))
        return EXIT_OK
    if res.status != _opf_store.RESOLVED:
        print("opf render: cannot evaluate: {}".format(res.detail), file=sys.stderr)
        return EXIT_CANNOT_EVALUATE

    if not check and not _WRITE_GATE_COMPOSED:
        # spec 5.7/5.8/11 (F1/F2): a mutating render must gate on the U6 store-integrity layer, which
        # does not exist yet and render does not compose (VC-4, deferred). Refuse the ungated write here,
        # BEFORE any fd is opened or any payload rendered, so nothing on disk is touched. --check reads
        # only and is unaffected.
        print("opf render: cannot evaluate: store-integrity gate (U6 validate_store) not available; "
              "render write refused", file=sys.stderr)
        return EXIT_CANNOT_EVALUATE

    store_root = res.store_root
    machine_rel = res.machine_rel
    # OPF store paths are UTF-8 by convention. A DISCOVERED machine-dir name that is not valid UTF-8 arrives
    # here surrogate-escaped in machine_rel; validate it EXPLICITLY now, before rendering ANY view, so a
    # non-UTF-8 machine-dir name is rejected fail-closed (exit 2) UNIVERSALLY and independent of which views
    # are declared (a header-exempt VERSION-only store included), not only when a Markdown view's
    # _source_set_digest encodes the path. The round-trip re-decodes cleanly for a valid-UTF-8 machine_rel
    # and raises UnicodeError on a surrogate-escaped non-UTF-8 byte; that is mapped to a cannot-evaluate here
    # (disclose-guard-residuals: an explicit refusal of a non-UTF-8 store path, never silently handled).
    try:
        machine_rel.encode("utf-8", "surrogateescape").decode("utf-8")
    except UnicodeError:
        print("opf render: cannot evaluate: store machine-directory name is not valid UTF-8 "
              "(OPF store paths are UTF-8 by convention); render refused", file=sys.stderr)
        return EXIT_CANNOT_EVALUATE
    pointer = res.pointer_source != "default"
    try:
        store_root_fd = _opf_store._open_store_root_fd(store_root, pointer)
    except OSError as exc:
        print("opf render: cannot open store root {} ({})".format(store_root, exc), file=sys.stderr)
        return EXIT_CANNOT_EVALUATE
    try:
        product_root_fd = _opf_store._open_root_fd(product_root)   # no-follow fd for the public VERSION write
    except OSError as exc:
        os.close(store_root_fd)
        print("opf render: cannot open product root {} ({})".format(product_root, exc), file=sys.stderr)
        return EXIT_CANNOT_EVALUATE
    try:
        try:
            return _render_resolved(store_root_fd, product_root_fd, machine_rel, check)
        except (ViewsError, RecursionError, ValueError) as exc:
            # ViewsError is U4's cannot-evaluate; RecursionError and (defensively) ValueError are widened
            # here as defence in depth, so a parse recursion escaping any inner path becomes a controlled
            # exit 2 rather than an uncontrolled traceback.
            print("opf render: cannot evaluate: {}".format(exc), file=sys.stderr)
            return EXIT_CANNOT_EVALUATE
    finally:
        os.close(store_root_fd)
        os.close(product_root_fd)


def _render_resolved(store_root_fd, product_root_fd, machine_rel, check):
    """The resolved-store render, split out so its ViewsError maps to exit 2 in render()'s handler. Views
    write beneath store_root_fd (`.working/<name>`); the one public VERSION deliverable writes beneath
    product_root_fd. Every target is bound to its spec destination (never the manifest `target`) and
    written through U4's no-follow contained write."""
    manifest_rel = "{}/{}".format(machine_rel, _opf_store.MANIFEST_NAME)
    got = _read_raw_and_parsed(store_root_fd, manifest_rel)
    if got is None:
        raise ViewsError("{} vanished after discovery".format(manifest_rel))
    manifest = got[1]
    mv = _opf_store.validate_manifest(manifest)
    if mv.status != _opf_store.VALID:
        raise ViewsError("manifest is not valid: {}".format("; ".join(mv.findings) or mv.status))

    # U4 renders the `inline` layout only. A `per-record` store is a CLEAR cannot-evaluate (deferred),
    # detected here from the manifest rather than mis-reported as a downstream malformed-record error and
    # never silently rendered as if inline.
    devprocess = manifest.get("devprocess")
    layout = devprocess.get("layout") if isinstance(devprocess, dict) else None
    if layout == "per-record":
        raise ViewsError("per-record layout not yet supported by U4 views; deferred")

    views = manifest.get("views", {})
    if not isinstance(views, dict):
        raise ViewsError("[views] is not a table")
    registered_vendors, registered_kinds = _registered_vendors_and_kinds(manifest)

    # Validate each view's declared sources against its required set AND collect the union to load. Per view
    # the declared `sources` must EQUAL the required set (no missing, extra, duplicate, or ill-typed entry);
    # an extra declared source would otherwise be neither read, validated, nor entered into the header
    # digest, so an unreadable extra could pass silently. Loading the union (== required after the equality
    # check) reads every declared source, so an unreadable or malformed one fails closed.
    needed = set()
    for name, tbl in views.items():
        if not isinstance(tbl, dict):
            raise ViewsError("[views.{}] is not a table".format(name))
        _kind, required, _renderer = _resolve_view(name)
        declared = tbl.get("sources", [])
        _check_declared_sources(name, required, declared)
        needed.update(declared)
    raw_by_relpath = {}
    rows_by_source = {}
    for name in sorted(needed):
        relpath = _source_relpath(machine_rel, name)
        if name == "worklog":
            raw, entries = _load_worklog(store_root_fd, relpath, registered_vendors, registered_kinds)
            rows_by_source[name] = entries
        elif name == "version":
            raw, releases, summaries = _load_version(store_root_fd, relpath)
            rows_by_source[name] = {"releases": releases, "summaries": summaries}
        else:
            raw, records = _load_records(store_root_fd, relpath, name, registered_vendors, registered_kinds)
            rows_by_source[name] = records
        raw_by_relpath[relpath] = raw

    # Phase 1: render every target's full text from the loaded sources, before any target is written. Each
    # target is bound to its OWN spec destination; a manifest `target` that does not match is rejected,
    # never silently redirected.
    planned = []   # (view_name, scope, dest_relpath, text)
    import check_byte_canon  # authoritative byte-canon leg; pure function over bytes, lazy like self_test
    for name in sorted(views):
        tbl = views[name]
        kind, required, renderer = _resolve_view(name)
        declared_kind = tbl.get("kind")
        if declared_kind != kind:
            raise ViewsError("view {!r} is declared kind {!r} but this generator renders it as {!r} "
                             "(spec 10.1)".format(name, declared_kind, kind))
        scope, dest_rel = _spec_destination(name)
        declared_target = tbl.get("target")
        if declared_target != dest_rel:
            raise ViewsError("view {!r} declares target {!r} but its spec destination is {!r}; a manifest "
                             "cannot rebind a view off its spec destination (fail-closed, spec 5.8/9)"
                             .format(name, declared_target, dest_rel))
        src = {s: rows_by_source[s] for s in required}
        body = renderer(src)
        if name == "VERSION":
            text = body                                   # header-exempt exact-bytes deliverable (spec 6.1)
        else:
            source_blobs = {_source_relpath(machine_rel, s): raw_by_relpath[_source_relpath(machine_rel, s)]
                            for s in required}
            text = _header(source_blobs) + "\n" + body
        # Defence in depth over the whole-corpus byte-canon gate (codex-B): a schema-valid free-text field
        # may carry a zero-width or bidi-control codepoint that _md_text does NOT strip (it drops only
        # C0/DEL), which would render a view body whose bytes FAIL the authoritative byte-canon scan.
        # GUARANTEE clean output by FAILING CLOSED here, before any drift compare or write: never emit
        # invalid bytes, never silently alter the owner's text.
        # Strip trailing whitespace from every rendered line with Python's Unicode str.rstrip() (no arg),
        # the SAME predicate check_byte_canon uses to detect a trailing-whitespace line (body != body
        # .rstrip()): a schema-valid free-text field may end in a space OR another Unicode whitespace
        # codepoint (U+00A0, U+1680, U+2000, U+202F, U+205F, U+3000, ...), which would put benign trailing
        # whitespace at a line end and (round-2) refuse the whole render. Matching check_byte_canon's own
        # predicate here normalizes out EVERY Unicode trailing-whitespace codepoint it would flag, not only
        # ASCII space/tab. Trailing line whitespace is a rendering artefact, not owner content, so
        # normalizing it is benign and deterministic; genuinely FORBIDDEN non-whitespace codepoints
        # (zero-width/bidi, e.g. U+200B/U+202E) are NOT stripped by rstrip() and still fail closed in the
        # byte-canon scan below. Goldens carry no trailing whitespace, so this is a no-op for them.
        text = "\n".join(line.rstrip() for line in text.split("\n"))
        canon = check_byte_canon.scan_bytes(text.encode("utf-8"))
        if canon:
            raise ViewsError("view {!r} would emit byte-canon-invalid output ({}); refusing rather than "
                             "emitting or silently altering owner text".format(name, "; ".join(canon)))
        planned.append((name, scope, dest_rel, text))

    # Phase 2: write (or drift-report under --check) each target in the stable (view-name) order, through
    # U4's no-follow contained write. `.working/<name>` writes beneath the store root; the public VERSION
    # writes beneath the product root.
    drift = False
    for _name, scope, dest_rel, text in sorted(planned, key=lambda p: p[0]):
        write_fd = product_root_fd if scope == "product" else store_root_fd
        if _write_contained(write_fd, dest_rel, text, check):
            print("drift: {}".format(dest_rel))
            drift = True
    if check and drift:
        print("run '{}' to regenerate".format(REGEN_COMMAND))
        return EXIT_DRIFT
    return EXIT_OK


# --- self-test (the opf.py --self-test render leg) ---------------------------------------------------

# CommonMark ASCII punctuation escapable with a backslash (CommonMark 2.4); a backslash before one of
# these yields the literal character (so `\`` is a literal backtick, NEVER a code-span fence).
_ASCII_PUNCT = frozenset("""!"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~""")

# The oracle's autolink recognisers, applied to a DECODED out-of-code text run. The URL and email
# recognisers are intentionally BOUNDARY-FREE, making the oracle a SUPERSET of cmark-gfm's detection for
# those two: cmark-gfm rewinds a URL to its scheme and autolinks email mid-text (e.g. `1http://a.example`),
# so the oracle must NOT false-negative a digit/letter/punct-prefixed URL or email trigger. Over-detection
# on raw input only STRENGTHENS teeth; it never causes a false-pass, because the production wrap code-spans
# every token, so the oracle returns [] on the emitted output. The www recogniser is now IDENTICAL to the
# production _MD_AUTOLINK_TOKEN_RE www arm BY CONSTRUCTION: both compile the shared _WWW_AUTOLINK_FRAGMENT
# (`(?<![A-Za-z0-9])www\.[^\s<]+`) with the SAME flags (re.IGNORECASE | re.VERBOSE), so the oracle detects
# exactly the www tokens production wraps - no Unicode-host false-negative, and oracle-www cannot drift from
# production-www in text OR in flags: identity holds by construction (one source, not two equal literals),
# so a future fragment edit - even one using a verbose-sensitive character - cannot make the two diverge.
# (URL stays http/https/ftp and email stays boundary-free, matching cmark-gfm's autolink
# set.) The URL schemes are cmark-gfm's
# http/https/ftp (GFM 6.9); the email form additionally recognises the optional mailto:/xmpp: prefix
# cmark-gfm folds into the link. Tokens end at whitespace or `<`, as a GFM URL autolink does.
_ORACLE_URL_RE = re.compile(r"(?:https?|ftp)://[^\s<]+", re.IGNORECASE)
_ORACLE_WWW_RE = re.compile(_WWW_AUTOLINK_FRAGMENT, re.IGNORECASE | re.VERBOSE)
_ORACLE_EMAIL_RE = re.compile(
    r"(?:mailto:|xmpp:)?[A-Za-z0-9.\-_+]+@[A-Za-z0-9\-_]+(?:\.[A-Za-z0-9\-_]+)+", re.IGNORECASE)
# A `;`-terminated HTML entity reference, with numeric references limited to CommonMark's grammar: 1-7
# decimal or 1-6 hex digits (so an 8-digit `&#00000058;` is NOT a valid reference and stays literal).
# cmark-gfm decodes a reference ONLY when the terminating `;` is present, so `&#58` (no `;`) stays literal
# and does NOT break an autolink; the oracle enforces the same by decoding only whole `&...;` tokens
# (guard-input-soundness).
_ORACLE_ENTITY_RE = re.compile(r"&(?:#[0-9]{1,7}|#[xX][0-9A-Fa-f]{1,6}|[A-Za-z][A-Za-z0-9]*);")


def _oracle_find_closing_fence(s, start, runlen):
    """The RAW index at or after `start` of the first backtick run of EXACTLY `runlen` (CommonMark 6.3
    closing-fence rule), or None. The scan is RAW: INSIDE a code span a backslash is literal and a backtick
    closes the span, so backslashes are ignored when locating the closer (verified against a real
    CommonMark renderer)."""
    n = len(s)
    k = start
    while k < n:
        if s[k] == "`":
            m = k
            while m < n and s[m] == "`":
                m += 1
            if m - k == runlen:
                return k
            k = m
        else:
            k += 1
    return None


def _oracle_text_runs(s):
    r"""Split `s` into the maximal RAW TEXT runs that lie OUTSIDE code spans (backslash escapes are NOT
    resolved here; `_oracle_decode_run` does that per run). Models the cmark-gfm code-span fact the
    autolink decision turns on: code spans are located by RAW backtick runs FIRST. OUTSIDE a code span a
    backslash-escaped backtick is a LITERAL backtick and does NOT open a span; INSIDE a span a backslash is
    literal and a backtick closes the span (verified against a real CommonMark renderer). An unescaped
    opening run of N backticks is closed by the next RAW run of EXACTLY N; content between is a CODE node,
    excluded because the autolink post-process never scans it. A backtick reached as a backslash escape is
    NOT an opener (it is literal text, resolved later); an opening run with no equal-length closer is
    literal backtick text."""
    n = len(s)
    i = 0
    runs = []
    cur = []
    while i < n:
        c = s[i]
        if c == "\\" and i + 1 < n:
            # An escaped char (incl. an escaped backtick) is literal text, never a fence opener; keep both
            # bytes RAW so _oracle_decode_run resolves the escape after code spans are removed.
            cur.append(s[i])
            cur.append(s[i + 1])
            i += 2
            continue
        if c == "`":
            j = i
            while j < n and s[j] == "`":
                j += 1
            runlen = j - i
            close = _oracle_find_closing_fence(s, j, runlen)
            if close is None:
                cur.append("`" * runlen)     # unclosed opener: literal backticks
                i = j
            else:
                runs.append("".join(cur)); cur = []
                i = close + runlen           # skip the code-span content and both fences
            continue
        cur.append(c)
        i += 1
    runs.append("".join(cur))
    return runs


def _oracle_decode_run(run):
    r"""Resolve a RAW out-of-code text run to the text cmark-gfm autolinks over: a backslash before an
    ASCII-punctuation char yields that literal char (and consumes it, so `\&#46;` cannot then form an
    entity), a lone backslash is literal, and a `;`-terminated entity reference is decoded. The two are
    mutually exclusive by position (a single left-to-right pass), matching CommonMark's inline scan."""
    out = []
    i = 0
    n = len(run)
    while i < n:
        c = run[i]
        if c == "\\" and i + 1 < n and run[i + 1] in _ASCII_PUNCT:
            out.append(run[i + 1])
            i += 2
            continue
        if c == "&":
            m = _ORACLE_ENTITY_RE.match(run, i)
            if m:
                dec = html.unescape(m.group(0))
                if dec != m.group(0):
                    out.append(dec)
                    i = m.end()
                    continue
            out.append("&")
            i += 1
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _oracle_trim_email(tok):
    """Apply cmark-gfm's extended-email tail rule (GFM 6.9): a trailing `.` is not part of the address,
    and the last character of the domain must not be `-` or `_`, else the address does NOT autolink.
    Returns the autolinked email, or None when the tail rule rejects it."""
    while tok.endswith("."):
        tok = tok[:-1]
    if not tok or tok[-1] in "-_":
        return None
    return tok


def _gfm_autolinks(markdown):
    """MINIMAL cmark-gfm autolink-extension oracle for the EXTENDED forms (www / url with schemes
    http/https/ftp / email with an optional mailto:/xmpp: prefix) -- the autolink forms this pack must
    defeat. It reproduces the parts of cmark-gfm's autolink POST-PROCESS that decide whether a bare
    URL/email becomes an active link, and nothing else (it is NOT a general Markdown parser). Cites GFM
    spec 6.9 (Autolinks (extension)).

      1. Code spans are located by RAW backtick runs FIRST and their content EXCLUDED (a CODE node is
         never autolink-scanned; OUTSIDE a span a backslash-escaped backtick is literal text and does NOT
         open a span, while INSIDE a span a backtick closes it -- verified against a real CommonMark
         renderer, not an escaped-backtick-fence assumption).
      2. Each remaining out-of-code TEXT run is decoded (backslash escapes, then `;`-terminated HTML
         entity references) exactly once, left to right -- cmark-gfm resolves references BEFORE autolinking
         (why `www&#46;example.com` autolinks) but ONLY when `;`-terminated (why `www&#46example.com` does
         not). Entities inside a code span are NOT decoded, which is why decoding runs per out-of-code run,
         after code spans are removed.
      3. The decoded run is scanned for the triggers: the URL and email recognisers deliberately have NO
         left-boundary precondition (a superset of cmark-gfm, which rewinds them), while the www recogniser
         keeps the not-after-alphanumeric boundary to match cmark-gfm's www rule and the production matcher.
         An email match is additionally put through the extended-email tail rule.

    Disclosed residual (disclose-guard-residuals): this oracle is a TEST instrument, not the production
    neutralizer. It was validated by a ONE-TIME differential against a real CommonMark+linkify renderer:
    production leaves ZERO active links across the adversarial inputs, and the oracle is a DELIBERATE
    SUPERSET of cmark-gfm's detection (the dropped URL/email left boundary above; the www rule keeps its
    not-after-alphanumeric boundary) so it can only over-detect on raw input, never false-pass on emitted
    output. Relatedly, the whole-body byte-canon scan in _render_resolved
    calls check_byte_canon.scan_bytes with allowances=(), which is correct because no view declares a
    byte-canon allowance today; a future per-view allowance would need threading through to that call.

    Returns the list of autolinked substrings (empty when nothing autolinks)."""
    found = []
    for run in _oracle_text_runs(markdown):
        text = _oracle_decode_run(run)
        found.extend(m.group(0) for m in _ORACLE_URL_RE.finditer(text))
        found.extend(m.group(0) for m in _ORACLE_WWW_RE.finditer(text))
        for m in _ORACLE_EMAIL_RE.finditer(text):
            email = _oracle_trim_email(m.group(0))
            if email is not None:
                found.append(email)
    return found


def self_test():
    """Render-leg self-test over SYNTHETIC stores. Judged on returned exit codes and rendered bytes, never
    by grepping output. Exercises: INDEPENDENT expected-bytes goldens per view (a hand-authored expected
    body pinned for EVERY view, so corrupting any renderer fails the test); drift detection; each transform
    (predicate filter, sort with ID tie-break, group, project); the block-actionability join (an unqualified
    active block hides an item, an active/proposed block does not) WITH deduped numeric-order block
    annotations (MAJOR-1); the decision supersession-chain join, with a fork and a cycle refused fail-closed;
    GFM extended-autolink neutralization in _md_text (bare http/https/www/email rendered inert, B1); the
    generated header's HTML-comment sink refusing an interpolated `-->`/`--!>` early close (B2); every
    NONTERMINAL `/proposed` record and the valid `withdrawn` terminal surfaced correctly in
    PIPELINE/DECISIONS/HANDOFF (never as effective/terminal, never omitted, B3); spec-destination target
    binding (a rebinding manifest target refused) and a no-follow contained write (a symlinked destination
    refused, its target left untouched); per-view declared-source equality (an extra declared source
    refused); deeply nested TOML mapped to cannot-evaluate; a free-text field that cannot forge markdown
    structure; a per-record layout reported as a clear deferral; a missing and a malformed declared source
    failing closed (exit 2); and an empty store rendering valid empty views. The tempdir is removed in a
    finally. Exit 0 pass, 1 fail, 2 error."""
    import tempfile
    import shutil

    try:
        _journal.require_containment()
    except _journal.JournalError as exc:
        print("OPF-VIEWS SELF-TEST ERROR: {}; fail-closed".format(exc), file=sys.stderr)
        return EXIT_CANNOT_EVALUATE

    failures = []
    checked = [0]
    global _WRITE_GATE_COMPOSED   # F1 scaffold: write-dependent cases toggle this True, restored below

    def check(name, cond):
        checked[0] += 1
        if not cond:
            failures.append(name)

    base = Path(tempfile.mkdtemp(prefix="opf-views-selftest-")).resolve()
    counter = [0]

    def new_root():
        counter[0] += 1
        root = base / "case-{:02d}".format(counter[0])
        (root / WORKING_DIRNAME / "toml").mkdir(parents=True)
        return root

    def write_toml(root, name, text):
        (root / WORKING_DIRNAME / "toml" / name).write_text(text, encoding="utf-8")

    # Every view and two mirrors, declared with the kind/sources this generator renders.
    manifest = "\n".join([
        "[devprocess]",
        'standard = "devprocess"',
        'spec_version = "1.0.0"',
        'layout = "inline"',
        'posture = "required"',
        'import_status = "none"',
        "",
        "[store]",
        'sync_target = ""',
        "",
        "[modules]",
        "governance = true",
        "operational_policy = true",
        "concurrent_operation = true",
        "",
        "[types.backlog_item]", 'namespace = "BI"',
        "[types.done]", 'namespace = "DN"',
        "[types.worklog]", 'namespace = "WL"',
        "[types.finding]", 'namespace = "FN"',
        "[types.pending_decision]", 'namespace = "PD"',
        "[types.autonomous_decision]", 'namespace = "AD"',
        "[types.block]", 'namespace = "BL"',
        "[types.handoff]", 'namespace = "HO"',
        "[types.reference]", 'namespace = "RF"',
        "",
        "[vendors]", "registered = []",
        "",
        _view("TODO.md", "composed", ["backlog_item", "block"]),
        _view("BACKLOG.md", "composed", ["backlog_item", "block"]),
        _view("PIPELINE.md", "composed", ["backlog_item", "block"]),
        _view("DONE.md", "composed", ["done"]),
        _view("FINDINGS.md", "composed", ["finding"]),
        _view("DECISIONS.md", "composed", ["pending_decision", "autonomous_decision"]),
        _view("BLOCKS.md", "composed", ["block"]),
        _view("HANDOFF.md", "composed", ["handoff"]),
        _view("REFERENCES.md", "composed", ["reference"]),
        _view("WORKLOG.md", "deterministic", ["worklog"]),
        _view("VERSION.md", "deterministic", ["version"]),
        _view("VERSION", "deterministic", ["version"]),
        _view("BACKLOG_ITEM-INDEX.md", "deterministic", ["backlog_item"]),
        _view("BLOCK-INDEX.md", "deterministic", ["block"]),                   # NEW: a list-field + actor mirror
    ]) + "\n"

    def empty_indexes(root):
        for t in BASELINE_TYPES:
            if t != "worklog":
                write_toml(root, "{}.index.toml".format(t), "schema = 1\n")
        write_toml(root, "worklog.toml", "schema = 1\n")
        write_toml(root, "version.toml", "schema = 1\n")

    try:
        # One Markdown-injection payload reused across the free-text-sink vectors below, and its EXACT
        # escaped form. P mixes the inline metacharacter classes at once: link ([x](y)), emphasis (*b*),
        # code span (`z`), table cell (|c|), strikethrough (~d~), heading/list lead-in (#e, !f), autolink/
        # raw-HTML (<g>), and entity (&h). ESC is precisely what _md_text must render P to (backslash pass
        # then entity pass). Pinned as constants so every sink assertion checks the SAME exact bytes, and
        # so removing _md_text from ANY one free-text sink (or under-escaping any class) flips an assertion.
        P = "[x](y)*b*`z`|c|~d~#e!f<g>&h"
        ESC = r"\[x\]\(y\)\*b\*\`z\`\|c\|\~d\~\#e\!f&lt;g&gt;&amp;h"

        # --- pure-unit discriminating vectors (no store, no write, gate-independent) -----------------
        # F9(1): the source-set digest is a function of BOTH each source path and its bytes. The suite
        # strips the header via body_of, so no golden observes the digest; a constant-digest mutant of
        # _source_set_digest would pass every other check. These two flip to FAIL for such a mutant.
        check("digest-source-sensitive", _source_set_digest({"p": b"A"}) != _source_set_digest({"p": b"B"}))
        check("digest-path-sensitive", _source_set_digest({"p": b"A"}) != _source_set_digest({"q": b"A"}))

        # F5: the closed transform vocabulary refuses an unknown filter/sort/group/project name.
        def raises_views_error(fn):
            try:
                fn()
                return False
            except ViewsError:
                return True

        check("sort-key-closed", raises_views_error(lambda: t_sort([{"id": "BI-1"}], keys=("bogus",))))
        check("group-field-closed", raises_views_error(lambda: t_group([{"id": "BI-1"}], "bogus")))
        check("project-column-closed", raises_views_error(lambda: t_project({"id": "BI-1"}, ("bogus",))))
        check("filter-predicate-closed", raises_views_error(lambda: t_filter([], "bogus")))

        # F4 (cited sink, B2 class): a spec-VALID free-text severity renders as LITERAL text, forging no
        # link, emphasis, code, table cell, strikethrough, heading, or HTML comment. The record is a valid
        # finding envelope: severity is graded at or after the fix decision, so status is terminal 'fixed'
        # with the actor and timestamps a full envelope carries (the prior vector used status='open' with a
        # severity, which the schema REJECTS, so it misrepresented a valid store; M3). The exact escaped
        # bytes are pinned, so dropping _md_text from the severity sink flips the assertion.
        forged = {"id": "FN-9", "type": "finding", "status": "fixed", "severity": P, "title": "t",
                  "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-02T00:00:00Z",
                  "actor": {"kind": "maintainer"}}
        finj = render_findings({"finding": [forged]})
        check("severity-injection-exact-escape", "(severity: " + ESC + ")" in finj)
        check("severity-injection-no-active-link", "[x](y)" not in finj)
        check("severity-injection-no-raw-payload", P not in finj)
        # The original newline-collapse + comment-neutralization forge, now on a VALID 'fixed' envelope.
        forged2 = {"id": "FN-8", "type": "finding", "status": "fixed",
                   "severity": "minor)\n# FORGED\n<!-- injected -->", "title": "t",
                   "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-02T00:00:00Z",
                   "actor": {"kind": "maintainer"}}
        finj2 = render_findings({"finding": [forged2]})
        check("severity-injection-no-forged-heading", "\n# FORGED" not in finj2)
        check("severity-injection-no-injected-comment", "<!-- injected -->" not in finj2)

        # F4 (class width, version-summary sink): a forged/injecting changelog `covers` is neutralized.
        # Exact escape pinned with P; the newline+comment forge checked separately. render_version_md is a
        # unit call, so validate_version's covers grammar does not constrain the payload here.
        vinj = render_version_md({"version": {"releases": [],
            "summaries": [{"covers": P, "status": "working"}]}})
        check("covers-injection-exact-escape", "- " + ESC + " (working)" in vinj)
        check("covers-injection-no-raw-payload", P not in vinj)
        vinj2 = render_version_md({"version": {"releases": [],
            "summaries": [{"covers": "x)\n# FORGED\n<!-- injected -->", "status": "working"}]}})
        check("covers-injection-no-forged-heading", "\n# FORGED" not in vinj2)
        check("covers-injection-no-injected-comment", "<!-- injected -->" not in vinj2)

        # F4 (class width, MIRROR projection sinks): the decision / classification / action columns escape
        # too. Exercised as unit render_mirror calls so no extra manifest mirror is needed; exact bytes
        # pinned, so removing _md_text from a projected column flips an assertion.
        pdm = render_mirror("pending_decision", [{
            "id": "PD-1", "type": "pending_decision", "status": "decided", "title": "t",
            "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-02T00:00:00Z",
            "actor": {"kind": "maintainer"}, "decision": P,
            "decided_at": "2026-01-02T00:00:00Z", "decided_by": "maintainer"}])
        check("mirror-decision-exact-escape", "- decision: " + ESC in pdm)
        check("mirror-decision-no-raw-payload", P not in pdm)
        adm = render_mirror("autonomous_decision", [{
            "id": "AD-1", "type": "autonomous_decision", "status": "recorded", "title": "t",
            "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-02T00:00:00Z",
            "actor": {"kind": "maintainer"}, "classification": P, "action": P}])
        check("mirror-classification-exact-escape", "- classification: " + ESC in adm)
        check("mirror-action-exact-escape", "- action: " + ESC in adm)
        check("mirror-autonomous-no-raw-payload", P not in adm)

        # F6 (unit-level on join_resolution): duplicate identical supersedes links do not trip the fork
        # check. Both decisions are `decided`, so they source supersedes edges (per join_resolution's
        # decided-only edge rule).
        pd1 = {"id": "PD-1", "status": "decided", "links": []}
        pd2 = {"id": "PD-2", "status": "decided", "links": [{"rel": "supersedes", "id": "PD-1"},
                                                            {"rel": "supersedes", "id": "PD-1"}]}
        try:
            _cur, _sup, _smap = join_resolution([pd1, pd2])
            dupok = (_cur == {"PD-2"} and _sup == {"PD-1"} and _smap["PD-2"] == ["PD-1"])
        except ViewsError:
            dupok = False
        check("duplicate-supersedes-link-no-false-fork", dupok)

        # FIX A (join_resolution supersedes-map ordering): a decision superseding several others lists them
        # DEDUPED and in numeric id order (PD-2 before PD-10), the SINGLE _sorted_ids ordering every id-set
        # annotation join uses. Reverting _sorted_ids in this sink to append/lexical order yields
        # ["PD-10", "PD-2"] (or a duplicate PD-10), flipping this; the single-link populated-store golden
        # below does not discriminate it (one target, no order or dup to observe).
        _msrc = {"id": "PD-1", "status": "decided",
                 "links": [{"rel": "supersedes", "id": "PD-10"},
                           {"rel": "supersedes", "id": "PD-2"},
                           {"rel": "supersedes", "id": "PD-10"}]}
        try:
            _mc, _ms, _msm = join_resolution([_msrc, {"id": "PD-2", "links": []},
                                              {"id": "PD-10", "links": []}])
            suporder_ok = (_msm["PD-1"] == ["PD-2", "PD-10"] and _mc == {"PD-1"})
        except ViewsError:
            suporder_ok = False
        check("supersedes-map-dedup-numeric-order", suporder_ok)

        # FIX B (supersedes-graph harden): a supersedes link on a NON-DECIDED (open/withdrawn) decision must
        # NOT supersede its target, so a decided effective resolution is never mislabeled "Superseded" by a
        # non-decided decision's link. PD-1 is a decided head; PD-2 is WITHDRAWN yet links supersedes PD-1.
        # PD-1 must stay in `current` and out of `superseded`. Sourcing edges from every decision regardless
        # of state would put PD-1 in `superseded`, flipping this. (Cross-record resolution GRADING otherwise
        # remains U6 validate_store's remit, F2; this closes only the mislabel window.)
        _decd = {"id": "PD-1", "status": "decided", "links": []}
        _wdrw = {"id": "PD-2", "status": "withdrawn", "links": [{"rel": "supersedes", "id": "PD-1"}]}
        try:
            _wc, _ws, _wsm = join_resolution([_decd, _wdrw])
            nondecided_ok = ("PD-1" in _wc and "PD-1" not in _ws)
        except ViewsError:
            nondecided_ok = False
        check("nondecided-supersedes-does-not-mark-target", nondecided_ok)

        # CLASS 1 (B1, F-U4AUTOLINK): _md_text renders GFM EXTENDED autolinks INERT by wrapping each
        # trigger in a CODE SPAN, which cmark-gfm never autolinks. Judged by RENDERING each sample through
        # the minimal autolink oracle (_gfm_autolinks) and asserting NO active link -- not by source-byte
        # matching, the prior test's flaw. The oracle decodes entities and skips code spans, so it models
        # GitHub's post-process (GFM 6.9). Exact output bytes are also pinned (fidelity: characters
        # preserved, only fenced), so reverting to the entity trick or dropping the wrap flips each check.

        # (a) Oracle teeth: the RAW forms autolink, and -- decisively -- the OLD entity output STILL
        # autolinks once entities are decoded (the render-aware proof the prior byte-only test was
        # inadequate). These are fix-independent; they validate the oracle and document the defeat.
        check("oracle-teeth-www-raw", _gfm_autolinks("see www.example.com now") != [])
        check("oracle-teeth-http-raw", _gfm_autolinks("at http://x.example ok") != [])
        check("oracle-teeth-https-raw", _gfm_autolinks("at https://x.example ok") != [])
        check("oracle-teeth-email-raw", _gfm_autolinks("mail a@b.example please") != [])
        check("oracle-teeth-old-entity-www", _gfm_autolinks("www&#46;example.com") != [])
        check("oracle-teeth-old-entity-http", _gfm_autolinks("http&#58;//x.example") != [])
        check("oracle-teeth-old-entity-email", _gfm_autolinks("a&#64;b.example") != [])
        # The oracle does NOT over-fire on inert text (no false alarm), so a clean neutralization is
        # trusted: a code span, a plain sentence, and a schema-shaped timestamp all yield no autolink.
        check("oracle-no-false-alarm-codespan", _gfm_autolinks("`www.example.com`") == [])
        check("oracle-no-false-alarm-plain", _gfm_autolinks("a normal sentence, no links.") == [])
        check("oracle-no-false-alarm-timestamp", _gfm_autolinks("2026-01-01T00:00:00Z") == [])
        # A `www.` preceded by an alphanumeric is NOT autolinked by cmark-gfm, so production leaves
        # `1www`/`xwww` plain and the oracle's restored www left boundary does not match them either.
        check("autolink-1www-inert", _gfm_autolinks(_md_text("1www.example.com")) == [])
        check("autolink-xwww-inert", _gfm_autolinks(_md_text("xwww.example.com")) == [])
        # round-5: pin the www boundary on BOTH production and the oracle so a future change to either is caught.
        # (a) production leaves an alphanumeric-prefixed www PLAIN (cmark-gfm does not autolink it):
        check("md-text-1www-plain", _md_text("1www.example.com") == "1www.example.com")
        check("md-text-xwww-plain", _md_text("xwww.example.com") == "xwww.example.com")
        check("md-text-ourwww-plain", _md_text("ourwww.site.com") == "ourwww.site.com")
        # (b) the oracle DETECTS a genuine www autolink at each valid boundary (raw-input teeth), incl Unicode host:
        check("oracle-www-boundary-start", _gfm_autolinks("www.example.com") != [])
        check("oracle-www-boundary-space", _gfm_autolinks("see www.example.com") != [])
        check("oracle-www-boundary-dot", _gfm_autolinks(".www.example.com") != [])
        check("oracle-www-boundary-hyphen", _gfm_autolinks("-www.example.com") != [])
        check("oracle-www-boundary-paren", _gfm_autolinks("(www.example.com") != [])
        check("oracle-www-unicode-host", _gfm_autolinks("www.é.com") != [])
        # (c) the oracle does NOT match an alphanumeric-prefixed www (matches production + cmark-gfm):
        check("oracle-www-no-alnum-prefix", _gfm_autolinks("1www.example.com") == [])
        # round-8: the oracle compiles the shared www fragment with the SAME flags as production, so a future
        # fragment edit (even one using verbose-sensitive chars) cannot diverge between the two. Pin EXACT flag
        # -set equality (not mere containment: an added flag such as re.ASCII changes `\s` and would diverge the
        # two while both still contain IGNORECASE|VERBOSE), AND that both carry the required IGNORECASE|VERBOSE.
        check("oracle-www-flags-match-production",
              _ORACLE_WWW_RE.flags == _MD_AUTOLINK_TOKEN_RE.flags
              and (_ORACLE_WWW_RE.flags & (re.IGNORECASE | re.VERBOSE)) == (re.IGNORECASE | re.VERBOSE))
        # Frozen-literal closure of the whole fragment/flag-mutation class: ANY edit to the reviewed fragment
        # source or to the compiled flag set breaks these pins, so a behaviour-changing edit cannot pass the
        # suite green even when it mutates production and oracle together (which the equality pin alone allows).
        check("www-fragment-frozen", _WWW_AUTOLINK_FRAGMENT == r"(?<![A-Za-z0-9])www\.[^\s<]+")
        check("www-matcher-flags-frozen",
              _ORACLE_WWW_RE.flags == (re.IGNORECASE | re.VERBOSE | re.UNICODE)
              and _MD_AUTOLINK_TOKEN_RE.flags == (re.IGNORECASE | re.VERBOSE | re.UNICODE))
        check("md-text-wwwX-missing-dot-plain", _md_text("wwwX") == "wwwX")
        check("oracle-wwwX-missing-dot-no-match", _gfm_autolinks("wwwX") == [])
        # `wwwXy` and `wwwx.com` pin the dot as LITERAL, not a wildcard (an unescaped-dot edit would wrap/match
        # these, where the bare-`wwwX` pin stays blind: the fragment's trailing [^\s<]+ needs a char after it).
        check("md-text-wwwXy-literal-dot-plain", _md_text("wwwXy") == "wwwXy")
        check("oracle-wwwXy-literal-dot-no-match", _gfm_autolinks("wwwXy") == [])
        check("md-text-wwwxdotcom-literal-dot-plain", _md_text("wwwx.com") == "wwwx.com")
        check("oracle-wwwxdotcom-literal-dot-no-match", _gfm_autolinks("wwwx.com") == [])
        # round-9: round-8 froze only www; codex found the email arm unfrozen, so a digit-leading-local-part
        # edit regressed neutralization while the suite stayed green. Freeze EVERY production arm and assert
        # the matcher is EXACTLY their composition, closing the whole matcher-edit class (any arm edit fires).
        check("url-arm-frozen", _URL_AUTOLINK_FRAGMENT == r"[A-Za-z][A-Za-z0-9+.\-]*://[^\s<]+")
        check("email-arm-frozen",
              _EMAIL_AUTOLINK_FRAGMENT == r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9_\-]+(?:\.[A-Za-z0-9_\-]+)+")
        check("prod-token-re-composed-from-arms",
              _MD_AUTOLINK_TOKEN_RE.pattern
              == "(" + _URL_AUTOLINK_FRAGMENT + "|" + _WWW_AUTOLINK_FRAGMENT + "|" + _EMAIL_AUTOLINK_FRAGMENT + ")")
        # Freeze the oracle's own detection patterns + flags: a weakened oracle would silently under-detect and
        # make the broad inertness invariant pass more easily (a test-integrity regression), so pin each literal.
        check("oracle-url-re-frozen",
              _ORACLE_URL_RE.pattern == r"(?:https?|ftp)://[^\s<]+"
              and _ORACLE_URL_RE.flags == (re.IGNORECASE | re.UNICODE))
        check("oracle-email-re-frozen",
              _ORACLE_EMAIL_RE.pattern == r"(?:mailto:|xmpp:)?[A-Za-z0-9.\-_+]+@[A-Za-z0-9\-_]+(?:\.[A-Za-z0-9\-_]+)+"
              and _ORACLE_EMAIL_RE.flags == (re.IGNORECASE | re.UNICODE))
        check("oracle-entity-re-frozen",
              _ORACLE_ENTITY_RE.pattern == r"&(?:#[0-9]{1,7}|#[xX][0-9A-Fa-f]{1,6}|[A-Za-z][A-Za-z0-9]*);"
              and _ORACLE_ENTITY_RE.flags == re.UNICODE)
        # round-9: control-strip teeth (round-8 QA: disabling _MD_CTRL_RE left the suite green, and byte-canon
        # does not flag C0/DEL, so the strip was un-backstopped). Freeze the control class and pin the strip.
        check("md-ctrl-re-frozen", _MD_CTRL_RE.pattern == "[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
        check("md-text-strips-control", _md_text("a\x00b\x07c\x1bd\x7fe") == "abcde")
        # behaviour pin for the exact codex round-8 witness: a digit-leading email local part is neutralized.
        check("md-text-digit-email-inert", _gfm_autolinks(_md_text("1@b.example")) == [])
        # round-10 (codex round-9 MAJOR): freeze the ORACLE www PATTERN too, not just its flags. It must
        # compile the shared frozen fragment; an oracle-only www edit (e.g. restricting to .com) weakened
        # detection and made the inertness invariant pass more easily while every existing pin stayed green.
        check("oracle-www-re-frozen",
              _ORACLE_WWW_RE.pattern == _WWW_AUTOLINK_FRAGMENT
              and _ORACLE_WWW_RE.flags == (re.IGNORECASE | re.VERBOSE | re.UNICODE))
        # round-10 (gemini round-9 7.1): freeze the inline-escape set. The golden P below omits \ _ { } +,
        # so dropping any of those from _MD_ESCAPE_RE passed green (markdown injection in a free-text value).
        check("md-escape-re-frozen", _MD_ESCAPE_RE.pattern == r"([\\`*_{}\[\]()#+!|~])")
        # and exercise the escaping of each metacharacter the golden P omits (backslash/underscore/plus/braces),
        # so a regression in _md_escape_inline or the set is caught at the behaviour level too.
        check("md-text-escapes-backslash", _md_text("a\\b") == "a\\\\b")
        check("md-text-escapes-underscore", _md_text("_a_") == "\\_a\\_")
        check("md-text-escapes-plus", _md_text("a+b") == "a\\+b")
        check("md-text-escapes-braces", _md_text("a{b}c") == "a\\{b\\}c")
        # round-10 (gemini round-9 7.2, codex corroborated): the code-span fence must exceed the LONGEST
        # backtick run in the token. The single-backtick autolink test could not distinguish a fence that is
        # always 2 wide, so a url with a 2-backtick run broke out of the span and autolinked. Pin the exact
        # fence width for a multi-run token AND the emitted-output inertness for 2- and 3-backtick-run urls.
        check("md-code-span-2run-exact-fence", _md_text("http://y/``z") == "```http://y/``z```")
        check("md-code-span-2run-inert", _gfm_autolinks(_md_text("http://y/``http://z.example")) == [])
        check("md-code-span-3run-inert", _gfm_autolinks(_md_text("http://y/```http://z.example")) == [])
        # round-11 (codex round-10 MAJOR): the newline collapse is LOAD-BEARING. It guarantees the value
        # renders as a SINGLE LINE, so a line-start block marker in the value (- + * list, # heading, >
        # quote, 1. ordered, ` fence, | table, 4-space indent) can never begin a new line and forge block
        # structure. md-text-collapses-cr tested only CR, and the no-forged-heading pin actually tests
        # #-escaping (which survives a newline regression), so a surgical LF-only regression forged a sibling
        # list item while the suite stayed green. Pin LF collapse directly AND the single-line-output
        # invariant over every CommonMark line-ending x block-marker pair (closes the whole class).
        check("md-text-collapses-lf", _md_text("a\nb") == "a b")
        check("md-text-lf-no-forged-list", _md_text("a\n- FORGED") == "a - FORGED")
        _nl_markers = ["- ", "+ ", "* ", "# ", "> ", "1. ", "`", "|", "    "]
        _nl_vectors = [nl + mk + "x" for nl in ("\n", "\r", "\r\n") for mk in _nl_markers]
        check("md-text-output-single-line",
              all("\n" not in _md_text("v)" + s) and "\r" not in _md_text("v)" + s) for s in _nl_vectors))
        # round-12 (codex round-11 BLOCKER+2 MAJOR, each confirmed via a real CommonMark parser): the
        # round-10/11 pins exercised only SINGLE-occurrence inputs, so a bounded-count regression
        # (.replace(...,1) / sub(...,count=1) / first-backtick-run only) left a LATER occurrence to break out
        # (forged list item, autolink breakout, early comment close). Close the repeated-occurrence / multi-run
        # class: discrete multi-occurrence witnesses here, plus the seeded fuzz over the 3 invariants below.
        check("md-text-multi-newline-single-line",
              "\n" not in _md_text("minor)\nbenign\n- FORGED") and "\r" not in _md_text("a\rb\rc\r- X"))
        check("md-text-collapses-crlf-single-space", _md_text("a\r\nb") == "a b")
        check("md-code-span-multi-run-inert", _gfm_autolinks(_md_text("http://y/``a```http://z.example")) == [])
        check("md-code-span-4run-inert", _gfm_autolinks(_md_text("http://y/````http://z.example")) == [])
        check("md-text-adjacent-triple-single-span",
              _md_text("a@b.com+a@c.com+a@d.com") == "`a@b.com+a@c.com+a@d.com`")
        # header sink: freeze the dash-breaking regex and pin EXACT neutralization of REPEATED -- runs plus
        # the newline and control legs in a discovered source path (codex: a count=1 dash break left a later
        # -->, closing the comment early around active <details> HTML; gemini: the newline/control legs had
        # no teeth). A multi-dash render pin asserts exactly one terminator survives.
        check("md-comment-dashes-re-frozen", _MD_COMMENT_DASHES_RE.pattern == r"-(?=-)")
        check("html-comment-breaks-multi-dash", _html_comment_safe("a--b--c") == "a- -b- -c")
        check("html-comment-collapses-newlines", _html_comment_safe("a\r\nb\rc\nd") == "a b c d")
        check("html-comment-strips-controls", _html_comment_safe("a\x00b\x07c\x7fd") == "abcd")
        _hdr3 = _header({".working/x--benign--><details open>ACTIVE</details>/backlog_item.index.toml": b"d"})
        check("header-comment-multi-dash-single-terminator", _hdr3.count("-->") == 1 and "--!>" not in _hdr3)
        # Seeded deterministic fuzz (test-hermeticity: a fixed seed yields the same verdict everywhere) over
        # the security invariants, with MULTI-occurrence inputs so any bounded-count / multi-run regression
        # that leaves a survivor fails on some generated input (not just the single-occurrence pins above).
        import random as _random
        _rng = _random.Random(778812)
        _fz_atoms = ["http://a.ex", "www.b.ex", "c@d.ex", "`", "``", "```", "````", "benign", "x)", "&#38;",
                     "\\", "-", "--", "---", "_", "|", "<i>", "<details open>", "</x>", "[a]", "(b)", "*x*",
                     "&", "<!--", "-->", "\t"]
        _fz_ends = ["\n", "\r", "\r\n"]
        _fz_marks = ["- ", "+ ", "* ", "# ", "> ", "1. ", "`", "|", "    ", "~~"]
        _sl_ok = _al_ok = _rh_ok = True
        for _ in range(1500):
            _p = []
            for _j in range(_rng.randint(1, 9)):
                _p.append(_rng.choice(_fz_atoms))
                if _rng.random() < 0.55:
                    _p.append(_rng.choice(_fz_ends) + _rng.choice(_fz_marks))
            _o = _md_text("v)" + "".join(_p))
            if "\n" in _o or "\r" in _o:
                _sl_ok = False
            if _gfm_autolinks(_o) != []:
                _al_ok = False
            # raw-`<` inert: the matcher excludes `<` from every autolink token and the segment escaper
            # entity-encodes it, so a raw `<` in output means a bounded/partial entity escape let an active
            # tag survive (codex round-12). `>` legitimately appears inside a wrapped URL token, so only `<`
            # (which opens a tag/comment) is the raw-HTML-inert signal.
            if "<" in _o:
                _rh_ok = False
        check("fuzz-md-text-single-line", _sl_ok)
        check("fuzz-md-text-autolink-inert", _al_ok)
        check("fuzz-md-text-raw-angle-inert", _rh_ok)
        # MULTIPLE source paths per header: a regression sanitizing only the first (or a subset) of the
        # interpolated sources leaves a later path's --> to close the comment early (codex round-12).
        _hdr_ok = True
        _dash_atoms = ["-", "--", "---", "-->", "--!>", "a", "b", "/", "x", "."]
        for _ in range(800):
            _paths = {}
            for _k in range(_rng.randint(1, 4)):
                _seg = "".join(_rng.choice(_dash_atoms) for _ in range(_rng.randint(1, 12)))
                _paths[".working/" + _seg + "/f" + str(_k) + ".index.toml"] = b"d"
            _h = _header(_paths)
            if _h.count("-->") != 1 or "--!>" in _h:
                _hdr_ok = False
        check("fuzz-header-comment-single-terminator", _hdr_ok)
        # round-14 (gemini round-13): _html_comment_safe has its OWN newline/control collapse and dash break;
        # fuzz it DIRECTLY over a RICH atom set (dashes, newlines, controls, tags) at multiple occurrences, so
        # a bounded collapse/strip/break leaves a survivor (a forged header field line, a surviving control, or
        # an active closer). Complete invariant: no -- run, no surviving newline, no control char.
        _hcs_ok = True
        _hcs_atoms = ["-", "--", "-->", "--!>", "\n", "\r", "\r\n", "\x00", "\x07", "\x7f", "a", "/", "<x>", ".", "\t"]
        for _ in range(800):
            _r = _html_comment_safe("".join(_rng.choice(_hcs_atoms) for _ in range(_rng.randint(1, 14))))
            if "--" in _r or "\n" in _r or "\r" in _r or _MD_CTRL_RE.search(_r):
                _hcs_ok = False
        check("fuzz-html-comment-safe-complete", _hcs_ok)
        check("html-comment-multi-newline-collapsed",
              "\n" not in _html_comment_safe("dir\nX\n digest: forged") and "\r" not in _html_comment_safe("a\rb\rc"))
        # round-15 (codex round-14 MINOR-2): a bounded CRLF .replace(...,1) is security-neutral (the following
        # unbounded \r/\n passes strip the remnant) but yields an extra space; pin the exact single-space
        # collapse of MULTIPLE CRLF in both sinks so the redundant special-case cannot silently regress.
        check("md-text-multi-crlf-single-space", _md_text("a\r\nb\r\nc") == "a b c")
        check("html-comment-multi-crlf-single-space", _html_comment_safe("a\r\nb\r\nc") == "a b c")
        # round-15 (codex round-14 MINOR-1): U+0009 TAB is DELIBERATELY preserved as inline whitespace (not a
        # stripped C0/DEL control; _MD_CTRL_RE excludes it). It is inert in every sink position: the value is
        # always emitted behind a structural prefix, never at column 0, so a TAB cannot indent a code block.
        # Pin the preservation + inertness so the tolerance is explicit and exercised.
        check("md-text-tab-preserved-inert",
              _md_text("a\tb") == "a\tb" and _gfm_autolinks(_md_text("x\t- y\t# z")) == []
              and "\n" not in _md_text("x\t- y\t# z") and "<" not in _md_text("x\t<i>y"))
        # round-17 (codex/gemini/claude round-16): the round-16 AST check was construct-specific (defeated by a
        # str.replace(count=) keyword, re.subn, str.split maxsplit, a method alias, or a loop break/islice) and
        # unsound (false-positive on module-level re.sub); the fixed 300-count pins missed a realistic cap at or
        # above the tested count. Replace both with CONSTRUCT-AGNOSTIC high-count BEHAVIOUR pins that assert the
        # OUTPUT at N occurrences, so a pass bounded BELOW N by ANY construct leaves a survivor and trips.
        _HC = 4096
        _N = _HC + 1  # _HC+1 occurrences so a bound of exactly _HC leaves one survivor (off-by-one, round-17 QA)
        check("md-text-hi-count-autolink-inert", _gfm_autolinks(_md_text(" ".join(["http://z.example"] * _N))) == [])
        check("md-text-hi-count-single-line",
              "\n" not in _md_text(("x\n- F") * _N) and "\r" not in _md_text(("a\r") * _N))
        check("md-text-hi-count-crlf-single-space", _md_text("a\r\n" * _N) == "a " * _N)
        check("md-text-hi-count-control-stripped", _MD_CTRL_RE.search(_md_text("a\x07" * _N)) is None)
        # soup carries EVERY _MD_ESCAPE_RE metacharacter (incl. backslash -- round-18 QA: an omitted char let a
        # per-char bound on it, e.g. backslash, escape the escape-completeness check) plus & < >.
        _md_soup = _md_text("\\`*_{}[]()#+!|~&<>" * _N)
        check("md-text-hi-count-escape-complete",
              not _MD_ESCAPE_RE.search(re.sub(r"\\.", "", _md_soup)) and "<" not in _md_soup and ">" not in _md_soup
              and _md_soup.count("&") == _md_soup.count("&amp;") + _md_soup.count("&lt;") + _md_soup.count("&gt;"))
        check("html-comment-hi-count-dash-broken", "--" not in _html_comment_safe("-" * (_N * 2)))
        check("html-comment-hi-count-single-line",
              "\n" not in _html_comment_safe(("a\n") * _N) and "\r" not in _html_comment_safe(("b\r") * _N))
        check("html-comment-hi-count-crlf-single-space", _html_comment_safe("a\r\n" * _N) == "a " * _N)
        check("html-comment-hi-count-control-stripped", _MD_CTRL_RE.search(_html_comment_safe("a\x07" * _N)) is None)
        # INHERENT RESIDUAL (disclose-guard-residuals): record fields (e.g. refs[].locator/.note) carry NO
        # schema size ceiling, so the neutralization passes MUST be unbounded and "every occurrence is
        # neutralized" is a UNIVERSAL invariant a finite corpus cannot prove. These pins catch a pass bounded
        # below _N occurrences by ANY construct (count/keyword-count/subn/maxsplit/alias/loop-break/islice);
        # the production passes are all unbounded as shipped. A regression bounding a pass AT OR ABOVE _N,
        # which fires only on a field with more than _N occurrences, is caught by the MANDATORY review of any
        # change to these security-critical sinks, not by this corpus.
        # round-13 discrete witnesses (codex round-12 MAJORs): raw-HTML inertness (a multi-tag value keeps no
        # raw `<`), escape completeness (no active metacharacter survives un-escaped), and a MULTI-SOURCE header.
        check("md-text-raw-html-inert", "<" not in _md_text("benign <i> <details open>ACTIVE</details>"))
        # round-14 (gemini round-13): escape-completeness covers ALL three entity chars (&, <, >) at multiple
        # occurrences, not just <: after the backslash pass no bare metacharacter survives, no raw < or >
        # remains, and every & begins one of the three named entities (a bounded .replace(...,1) leaves a bare
        # one). Soup carries repeated &, <, >, and every _MD_ESCAPE_RE metacharacter.
        _esc_soup = _md_escape_inline("[a](b)&c&d<e><f>*g*_h_~i~|j|#k!l")
        check("md-escape-completeness-no-bare-metachar",
              not _MD_ESCAPE_RE.search(re.sub(r"\\.", "", _esc_soup))
              and "<" not in _esc_soup and ">" not in _esc_soup
              and _esc_soup.count("&") == _esc_soup.count("&amp;") + _esc_soup.count("&lt;") + _esc_soup.count("&gt;"))
        check("header-comment-multi-source-single-terminator",
              _header({".working/x--a--><x open>A</x>/backlog_item.index.toml": b"d",
                       ".working/x--a--><x open>A</x>/block.index.toml": b"e"}).count("-->") == 1)
        # Disclosed residual: the frozen pins close the matcher-edit class (any arm/flag/oracle edit is caught);
        # the exact-byte/behaviour pins + broad fuzz cover the wrapping/escaping logic on exercised shapes. An
        # edit that changes output only on an UNEXERCISED shape without touching a frozen matcher is the residual
        # the broad fuzz mitigates.
        # (d) round-5: pin PRODUCTION (_md_text), not only the oracle, at every positive www boundary and a
        # Unicode host, so a production-side boundary/Unicode-host regression is caught. For each case,
        # production WRAPS the www token in a code span AND the emitted output is autolink-inert.
        check("md-text-wraps-www-start", "`www.example.com`" in _md_text("www.example.com"))
        check("md-text-www-start-inert", _gfm_autolinks(_md_text("www.example.com")) == [])
        check("md-text-wraps-www-space", "`www.example.com`" in _md_text("see www.example.com"))
        check("md-text-www-space-inert", _gfm_autolinks(_md_text("see www.example.com")) == [])
        check("md-text-wraps-www-dot", "`www.example.com`" in _md_text(".www.example.com"))
        check("md-text-www-dot-inert", _gfm_autolinks(_md_text(".www.example.com")) == [])
        check("md-text-wraps-www-hyphen", "`www.example.com`" in _md_text("-www.example.com"))
        check("md-text-www-hyphen-inert", _gfm_autolinks(_md_text("-www.example.com")) == [])
        check("md-text-wraps-www-paren", "`www.example.com`" in _md_text("(www.example.com"))
        check("md-text-www-paren-inert", _gfm_autolinks(_md_text("(www.example.com")) == [])
        check("md-text-wraps-www-unicode", "`www.é.com`" in _md_text("www.é.com"))
        check("md-text-www-unicode-inert", _gfm_autolinks(_md_text("www.é.com")) == [])

        # (b) The FIX renders each form inert (fails under the old entity _md_text, which still autolinks).
        check("autolink-www-inert", _gfm_autolinks(_md_text("www.example.com")) == [])
        check("autolink-http-inert", _gfm_autolinks(_md_text("http://x.example")) == [])
        check("autolink-https-inert", _gfm_autolinks(_md_text("https://x.example")) == [])
        check("autolink-email-inert", _gfm_autolinks(_md_text("a@b.example")) == [])
        check("autolink-www-inert-midsentence", _gfm_autolinks(_md_text("see www.example.com now")) == [])
        check("autolink-http-inert-midsentence", _gfm_autolinks(_md_text("at http://x.example ok")) == [])
        check("autolink-email-inert-midsentence", _gfm_autolinks(_md_text("mail a@b.example please")) == [])
        # A value mixing inline metacharacters AND an autolink: the metachars escape, the URL is fenced,
        # and no autolink survives (exercises the escape/wrap interleave and the escaped-backtick guard).
        check("autolink-mixed-inert", _gfm_autolinks(_md_text("*b* `c` www.x.com and a@b.example")) == [])

        # (c) Exact-bytes pins (fidelity: every character preserved inside a code span). Each FAILS under
        # the old entity output (e.g. "www&#46;example.com") and holds only for the code-span wrap.
        check("autolink-www-exact", _md_text("www.example.com") == "`www.example.com`")
        check("autolink-http-exact", _md_text("http://x.example") == "`http://x.example`")
        check("autolink-https-exact", _md_text("https://x.example") == "`https://x.example`")
        check("autolink-email-exact", _md_text("a@b.example") == "`a@b.example`")
        # No over-fire: a schema-shaped timestamp (no `://`, `www.`, `@`) is untouched, byte-for-byte.
        check("autolink-no-overfire-timestamp", _md_text("2026-01-01T00:00:00Z") == "2026-01-01T00:00:00Z")
        # A code-span token that itself contains backticks fences correctly (longer fence + padding).
        check("autolink-token-with-backtick",
              _md_text("http://x/`q`") == "`` http://x/`q` ``")
        check("autolink-token-with-backtick-inert", _gfm_autolinks(_md_text("http://x/`q`")) == [])

        # (d) FINDING A: the oracle is faithful to cmark-gfm's autolink DECISION. Each vector FAILS on the
        # pre-fix oracle and holds only for the faithful one.
        # A.1 ORDERING: INSIDE a code span a backslash is literal and a backtick closes the span, so the
        # span closes at the 2nd backtick, leaving the URL in a TEXT run and ACTIVE.
        check("oracle-codespan-first-ordering", _gfm_autolinks("`x\\` http://e.com`") != [])
        # A.2 PROTOCOLS: ftp:// and mailto:/xmpp: autolink; the oracle needs teeth, the wrap keeps inert.
        check("oracle-teeth-ftp-raw", _gfm_autolinks("see ftp://x.example ok") != [])
        check("oracle-teeth-mailto-raw", _gfm_autolinks("mail mailto:a@b.example ok") != [])
        check("oracle-teeth-xmpp-raw", _gfm_autolinks("at xmpp:a@b.example ok") != [])
        check("autolink-ftp-inert", _gfm_autolinks(_md_text("ftp://x.example")) == [])
        check("autolink-mailto-inert", _gfm_autolinks(_md_text("mailto:a@b.example")) == [])
        check("autolink-xmpp-inert", _gfm_autolinks(_md_text("xmpp:a@b.example")) == [])
        # A.3 ENTITIES: a numeric reference decodes ONLY when `;`-terminated.
        check("oracle-entity-requires-semicolon", _gfm_autolinks("http&#58//x.example") == [])
        check("oracle-entity-with-semicolon-teeth", _gfm_autolinks("http&#58;//x.example") != [])
        # A.4 EMAIL DOMAIN: a trailing `_` or `-` is not a valid autolink; a real one is.
        check("oracle-email-trailing-underscore-rejected", _gfm_autolinks("mail a@b.c_ please") == [])
        check("oracle-email-trailing-hyphen-rejected", _gfm_autolinks("mail a@b.c- please") == [])
        check("oracle-email-normal-teeth", _gfm_autolinks("mail a@b.cd please") != [])
        # round-3: the oracle is a SUPERSET (no left-boundary false-negative) - a digit/punct-prefixed URL
        # is detected (cmark-gfm autolinks it after the digit); production still wraps it inert.
        check("oracle-detects-digit-prefixed-url", _gfm_autolinks("1http://a.example") != [])
        check("oracle-detects-delimiter-prefixed-email", _gfm_autolinks("x:a@b.example") != [])
        check("autolink-digit-prefixed-url-inert", _gfm_autolinks(_md_text("1http://a.example")) == [])
        # round-3: an OVERLONG numeric reference (8 decimal digits) is NOT a valid CommonMark entity, so the
        # oracle leaves it literal (no spurious decode -> no false autolink).
        check("oracle-overlong-entity-literal", _gfm_autolinks("http&#00000058;//x.example") == [])
        # round-3: a free-text field with TRAILING WHITESPACE renders CLEAN (not refused) - trailing line
        # whitespace is normalized out (by _render_resolved, not _md_text) before the byte-canon scan. The
        # prior unit assertion here was a tautology (it compared an expression with itself); the real proof
        # is the end-to-end `trailing-space-title-renders` / `trailing-nbsp-title-renders` cases in CLASS B,
        # which drive a full render and read the emitted bytes.

        # FINDING C: adjacent autolink tokens (no text between) COALESCE into ONE code span over the
        # original substring, so no touching backtick run forms and no fence leaks into the content.
        # Pre-fix output was "`a@b.com``+a@c.com`" (visible double backtick).
        check("autolink-adjacent-single-span", _md_text("a@b.com+a@c.com") == "`a@b.com+a@c.com`")
        check("autolink-adjacent-no-double-backtick", "``" not in _md_text("a@b.com+a@c.com"))
        check("autolink-adjacent-inert", _gfm_autolinks(_md_text("a@b.com+a@c.com")) == [])
        # A single space between two addresses is real text, so they stay TWO separate spans (unchanged).
        check("autolink-nonadjacent-two-spans",
              _md_text("a@b.com a@c.com") == "`a@b.com` `a@c.com`")

        # F-U4AUTOLINK byte-canon: a rendered view carrying a URL free-text field is byte-canon clean
        # (no forbidden zero-width/bidi codepoint, no CR, single trailing newline, no trailing
        # whitespace) AND renders the URL inert. Byte-canon is judged by the AUTHORITATIVE gate's own
        # scan_bytes over the real body bytes, not a reimplementation; the whole-view CI byte-canon gate
        # is the standing guarantee, this pins it at the unit level. The control body (raw URL, no wrap)
        # is what the oracle WOULD autolink, giving the check teeth.
        import check_byte_canon  # authoritative byte-canon leg; pure function over bytes
        url_finding = {"id": "FN-7", "type": "finding", "status": "fixed",
                       "title": "see http://ex.example/a?b=1&c=2 and mail a@ex.example",
                       "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-02T00:00:00Z",
                       "actor": {"kind": "maintainer"}}
        url_body = render_findings({"finding": [url_finding]})
        check("autolink-view-byte-canon-clean", check_byte_canon.scan_bytes(url_body.encode("utf-8")) == [])
        check("autolink-view-no-active-link", _gfm_autolinks(url_body) == [])
        check("autolink-view-oracle-teeth",
              _gfm_autolinks("see http://ex.example/a?b=1&c=2 and mail a@ex.example") != [])
        # The URL's raw `&` inside the code span stays literal (code, not an entity), so the address
        # renders exactly; the value carried no autolink out.
        check("autolink-view-url-preserved", "`http://ex.example/a?b=1&c=2`" in url_body)

        # FINDING B teeth (fix-independent): _md_text drops only C0/DEL, so a zero-width or bidi-control
        # codepoint in a free-text field SURVIVES into the rendered text -- the bytes the byte-canon scan
        # forbids. These document the hole the whole-body fail-closed scan (in _render_resolved) closes.
        for _lbl, _cp in (("zwsp", 0x200B), ("zwnj", 0x200C), ("zwj", 0x200D),
                          ("wj", 0x2060), ("bom", 0xFEFF), ("rlo", 0x202E)):
            _mt = _md_text("a" + chr(_cp) + "b")
            check("md-text-passes-forbidden-" + _lbl, chr(_cp) in _mt)
            check("byte-canon-flags-forbidden-" + _lbl,
                  check_byte_canon.scan_bytes(_mt.encode("utf-8")) != [])
        # A lone CR is a DIFFERENT case: _md_text COLLAPSES it to a space, so it never reaches a body; the
        # scan's CR leg is defence in depth for any non-_md_text path (schema also rejects a CR in title).
        check("md-text-collapses-cr", _md_text("a\rb") == "a b")
        check("byte-canon-cr-teeth", check_byte_canon.scan_bytes(b"a\rb\n") != [])

        # CLASS 2 (B2): the generated header interpolates DISCOVERED source paths into an HTML comment; a
        # machine-dir component bearing `-->` (or the abrupt `--!>`) must NOT close the comment early. Every
        # comment closer needs `--`, which _html_comment_safe breaks, so only the header's own terminating
        # `-->` remains. Reverting the encoding leaves the injected sequence, so the count/absence flips.
        _hdr = _header({".working/x--><details open>ACTIVE/backlog_item.index.toml": b"data"})
        check("header-comment-single-terminator", _hdr.count("-->") == 1)
        check("header-comment-no-abrupt-close", "--!>" not in _hdr)
        _hdr2 = _header({".working/a--!>b/x.index.toml": b"d"})
        check("header-comment-abrupt-neutralized", "--!>" not in _hdr2 and _hdr2.count("-->") == 1)

        # CLASS 4 (MAJOR-1): a record scoped by more than one active block is annotated with the block IDs
        # DEDUPED and ordered by the numeric record-id key (BL-2 before BL-10), never appended per scope
        # occurrence and sorted lexically. BL-2 scopes BI-5 twice (a duplicate occurrence); the annotation
        # must still read [BL-2, BL-10]. Reverting to append+lexical yields [BL-10, BL-2, BL-2].
        _bb, _hid = join_actionability(
            [{"id": "BI-5", "status": "open"}],
            [{"id": "BL-10", "status": "active", "scopes": ["BI-5"]},
             {"id": "BL-2", "status": "active", "scopes": ["BI-5", "BI-5"]}])
        check("block-annotation-dedup-numeric-order", _bb.get("BI-5") == ["BL-2", "BL-10"])

        # FIX A (render_done receipt ordering): a done record naming several receipts lists them DEDUPED and
        # in numeric id order (BI-2 before BI-10), the same _sorted_ids ordering. Reverting _sorted_ids in
        # render_done to append/lexical order yields "BI-10, BI-2" (or a duplicate), flipping this; the
        # single-receipt populated-store golden (DN-1 receipt_of BI-4) does not discriminate it. Exercised
        # as a unit render_done call, matching the other unit-level sink vectors.
        _donebody = render_done({"done": [{"id": "DN-1", "title": "r", "links": [
            {"rel": "receipt_of", "id": "BI-10"},
            {"rel": "receipt_of", "id": "BI-2"},
            {"rel": "receipt_of", "id": "BI-10"}]}]})
        check("done-receipt-dedup-numeric-order", "(receipt_of BI-2, BI-10)" in _donebody)

        _saved_gate = _WRITE_GATE_COMPOSED
        try:
            _WRITE_GATE_COMPOSED = True   # F1 test scaffold: exercise real rendered bytes and let the
                                          # full-render fail-closed cases reach _render_resolved and
                                          # fail for their intended reason, not merely the write gate.
            # --- a POPULATED store, exercising every transform and both joins -------------------------------
            root = new_root()
            write_toml(root, "manifest.toml", manifest)
            write_toml(root, "backlog_item.index.toml", "\n".join([
                "schema = 1",
                _rec("BI-10", "backlog_item", "open", "ten open"),
                _rec("BI-2", "backlog_item", "active", "two active"),
                _rec("BI-3", "backlog_item", "open", "three open"),
                _rec("BI-4", "backlog_item", "done", "four done"),
            ]) + "\n")
            write_toml(root, "block.index.toml", "\n".join([
                "schema = 1",
                _rec("BL-1", "block", "active", "grant", scopes=["BI-2"]),
                _rec("BL-2", "block", "active/proposed", "proposal", actor="assistant", scopes=["BI-3"]),
            ]) + "\n")
            write_toml(root, "done.index.toml", "\n".join([
                "schema = 1",
                _rec("DN-1", "done", "recorded", "receipt", links=[("receipt_of", "BI-4")]),
            ]) + "\n")
            write_toml(root, "finding.index.toml", "\n".join([
                "schema = 1",
                _rec("FN-1", "finding", "open", "open finding"),
                _rec("FN-2", "finding", "fixed", "fixed finding", severity="minor"),
            ]) + "\n")
            write_toml(root, "pending_decision.index.toml", "\n".join([
                "schema = 1",
                _rec("PD-1", "pending_decision", "decided", "first ruling",
                     decision="do X", decided_at="2026-09-01T00:00:00Z", decided_by="maintainer"),
                _rec("PD-2", "pending_decision", "decided", "revised ruling",
                     decision="do Y", decided_at="2026-09-02T00:00:00Z", decided_by="maintainer",
                     links=[("supersedes", "PD-1")]),
                _rec("PD-3", "pending_decision", "open", "still open"),
            ]) + "\n")
            write_toml(root, "autonomous_decision.index.toml", "\n".join([
                "schema = 1",
                _rec("AD-1", "autonomous_decision", "recorded", "acted",
                     classification="ACT", action="did the thing"),
            ]) + "\n")
            write_toml(root, "handoff.index.toml", "\n".join([
                "schema = 1",
                _rec("HO-1", "handoff", "superseded", "old handoff"),
                _rec("HO-2", "handoff", "current", "current handoff"),
            ]) + "\n")
            write_toml(root, "reference.index.toml", "\n".join([
                "schema = 1",
                _rec("RF-1", "reference", "recorded", "a source",
                     refs=[("doc", "OPF-SPEC.md 10.2")]),
            ]) + "\n")
            write_toml(root, "worklog.toml", "\n".join([
                "schema = 1",
                _entry("WL-1", "added", "first change"),
                _entry("WL-2", "fixed", "second change"),
            ]) + "\n")
            write_toml(root, "version.toml", "\n".join([
                "schema = 1",
                "",
                "[[release]]",
                'version = "1.3.0"',
                'date = "2026-08-30T00:00:00Z"',
                'worklog_span = ["WL-1", "WL-2"]',
                'coverage_digest = "{}"'.format(_opf_release.coverage_digest([
                    {"id": "WL-1", "date": "2026-01-01T00:00:00Z", "actor": {"kind": "maintainer"},
                     "kind": "added", "summary": "first change"},
                    {"id": "WL-2", "date": "2026-01-02T00:00:00Z", "actor": {"kind": "maintainer"},
                     "kind": "fixed", "summary": "second change"}])),
                "",
                "[[summary]]",
                'covers = "unreleased"',
                'status = "working"',
            ]) + "\n")

            # Write mode renders cleanly, then --check is clean (a byte-stable re-render).
            check("populated-write-ok", render(["--root", str(root)]) == EXIT_OK)
            check("populated-check-clean", render(["--root", str(root), "--check"]) == EXIT_OK)

            def read_view(name):
                return (root / WORKING_DIRNAME / name).read_text(encoding="utf-8")

            todo = read_view("TODO.md")
            check("join-block-hides-active-block", "BI-2" not in todo)
            check("join-block-keeps-proposed-block", "BI-3" in todo)
            check("todo-keeps-open", "BI-10" in todo)
            check("todo-drops-done", "BI-4" not in todo)
            check("sort-id-tiebreak-numeric", todo.index("BI-3") < todo.index("BI-10"))
            check("header-present", todo.startswith("<!-- GENERATED by opf render"))
            check("header-has-sources", "sources: .working/toml/backlog_item.index.toml" in todo)
            check("header-has-digest", "source-set-digest: sha256:" in todo)
            check("header-has-regen", "regenerate: opf render" in todo)
            check("header-no-timestamp", not re.search(r"\d{4}-\d\d-\d\dT\d\d:\d\d", todo.split("-->", 1)[0]))

            # --- INDEPENDENT expected-bytes goldens for EVERY view (F-09) ------------------------------------
            # Each golden is a HAND-AUTHORED constant, NOT produced by this module's renderers; it pins the
            # exact bytes AFTER the do-not-edit header (the header carries a source-set digest, so the body is
            # the byte-stable region), compared with ==, so corrupting ANY renderer flips this to FAIL. A
            # LIST-valued field (block scopes) and an ACTOR dict are golden-covered by the two mirror goldens.
            def body_of(text):
                marker = "-->\n"
                return text[text.index(marker) + len(marker):]

            goldens = {
                "TODO.md": "\n# TODO\n\n- BI-3 (open) three open\n- BI-10 (open) ten open\n",
                "BACKLOG.md": ("\n# BACKLOG\n\n- BI-2 (active) two active -- blocked by BL-1\n"
                               "- BI-3 (open) three open -- actionable\n"
                               "- BI-4 (done) four done -- not actionable (done)\n"
                               "- BI-10 (open) ten open -- actionable\n"),
                "PIPELINE.md": ("\n# PIPELINE\n\n## open\n- BI-3 three open\n- BI-10 ten open\n\n"
                                "## active\n- BI-2 two active [blocked by BL-1]\n\n## done\n- BI-4 four done\n"),
                "DONE.md": "\n# DONE\n\n- DN-1 receipt (receipt_of BI-4)\n",
                "FINDINGS.md": ("\n# FINDINGS\n\n## fixed\n- FN-2 (severity: minor) fixed finding\n\n"
                                "## open\n- FN-1 open finding\n"),
                "DECISIONS.md": ("\n# DECISIONS\n\n## Pending decisions\n- PD-3 still open\n\n"
                                 "## Effective resolutions\n- PD-2 revised ruling (supersedes PD-1)\n\n"
                                 "## Superseded resolutions\n- PD-1 first ruling\n\n"
                                 "## Autonomous decisions\n- AD-1 acted\n"),
                "BLOCKS.md": ("\n# BLOCKS\n\n- BL-1 (active) scopes [BI-2] -- grant\n"
                              "- BL-2 (active/proposed) scopes [BI-3] -- proposal\n"),
                "HANDOFF.md": ("\n# HANDOFF\n\n## Current\n- HO-2 current handoff\n\n"
                               "## Superseded\n- HO-1 old handoff\n"),
                "REFERENCES.md": "\n# REFERENCES\n\n- RF-1 a source\n  - doc: OPF-SPEC.md 10.2\n",
                "WORKLOG.md": ("\n# WORKLOG\n\n- WL-1 (2026-01-01T00:00:00Z) [added] first change\n"
                               "- WL-2 (2026-01-01T00:00:00Z) [fixed] second change\n"),
                "VERSION.md": ("\n# VERSION\n\n## Releases\n"
                               "- 1.3.0 (2026-08-30T00:00:00Z) worklog WL-1..WL-2\n\n"
                               "## Changelog summaries\n- unreleased (working)\n"),
                "BACKLOG_ITEM-INDEX.md": (
                    "\n# BACKLOG_ITEM index\n\n"
                    "## BI-2\n- type: backlog\\_item\n- status: active\n- title: two active\n"
                    "- created_at: 2026-01-01T00:00:00Z\n- updated_at: 2026-01-02T00:00:00Z\n- actor: maintainer\n\n"
                    "## BI-3\n- type: backlog\\_item\n- status: open\n- title: three open\n"
                    "- created_at: 2026-01-01T00:00:00Z\n- updated_at: 2026-01-02T00:00:00Z\n- actor: maintainer\n\n"
                    "## BI-4\n- type: backlog\\_item\n- status: done\n- title: four done\n"
                    "- created_at: 2026-01-01T00:00:00Z\n- updated_at: 2026-01-02T00:00:00Z\n- actor: maintainer\n\n"
                    "## BI-10\n- type: backlog\\_item\n- status: open\n- title: ten open\n"
                    "- created_at: 2026-01-01T00:00:00Z\n- updated_at: 2026-01-02T00:00:00Z\n- actor: maintainer\n"),
                "BLOCK-INDEX.md": (
                    "\n# BLOCK index\n\n"
                    "## BL-1\n- type: block\n- status: active\n- title: grant\n"
                    "- created_at: 2026-01-01T00:00:00Z\n- updated_at: 2026-01-02T00:00:00Z\n- scopes: [BI-2]\n"
                    "- actor: maintainer\n\n"
                    "## BL-2\n- type: block\n- status: active/proposed\n- title: proposal\n"
                    "- created_at: 2026-01-01T00:00:00Z\n- updated_at: 2026-01-02T00:00:00Z\n- scopes: [BI-3]\n"
                    "- actor: assistant\n"),
            }
            for gname, gbody in sorted(goldens.items()):
                check("golden-body-" + gname, body_of(read_view(gname)) == gbody)
            check("golden-version-file-bytes",
                  (root / "VERSION").read_text(encoding="utf-8") == "1.3.0\n")

            # Retained substring checks (redundant with the goldens, kept as readable anchors).
            backlog = read_view("BACKLOG.md")
            check("backlog-annotates-blocked", "blocked by BL-1" in backlog)
            check("backlog-annotates-actionable", "actionable" in backlog)
            pipeline = read_view("PIPELINE.md")
            check("pipeline-groups-in-order",
                  0 < pipeline.index("## open") < pipeline.index("## active") < pipeline.index("## done"))
            check("pipeline-blocked-marker", "[blocked by BL-1]" in pipeline)
            decisions = read_view("DECISIONS.md")
            eff = decisions.index("## Effective resolutions")
            sup = decisions.index("## Superseded resolutions")
            check("resolution-head-effective", "PD-2" in decisions[eff:sup])
            check("resolution-head-names-superseded", "supersedes PD-1" in decisions[eff:sup])
            check("resolution-superseded-listed", "PD-1" in decisions[sup:])
            check("resolution-open-listed", "PD-3" in decisions[:eff])
            check("resolution-autonomous-listed", "AD-1" in decisions)
            done = read_view("DONE.md")
            check("done-project-receipt", "receipt_of BI-4" in done)
            findings = read_view("FINDINGS.md")
            check("findings-project-severity", "severity: minor" in findings)
            check("findings-group-status", "## open" in findings and "## fixed" in findings)
            handoff = read_view("HANDOFF.md")
            check("handoff-current-first", handoff.index("HO-2") < handoff.index("## Superseded"))
            version_file = (root / "VERSION").read_text(encoding="utf-8")
            check("version-file-exact-bytes", version_file == "1.3.0\n")
            check("version-file-no-header", not version_file.startswith("<!--"))
            mirror = read_view("BACKLOG_ITEM-INDEX.md")
            check("mirror-title", "# BACKLOG_ITEM index" in mirror and mirror.startswith("<!-- GENERATED"))
            check("mirror-projects-record", "## BI-2" in mirror and "- status: active" in mirror)

            # drift: hand-edit a rendered view; --check must report exit 1 with a drift line.
            (root / WORKING_DIRNAME / "TODO.md").write_text("hand edited\n", encoding="utf-8")
            check("drift-detected", render(["--root", str(root), "--check"]) == EXIT_DRIFT)

            # --- an EMPTY store renders VALID EMPTY views; its declared VERSION carries one release ----------
            eroot = new_root()
            write_toml(eroot, "manifest.toml", manifest)
            empty_indexes(eroot)
            # The record indexes stay empty, but the DECLARED VERSION deliverable needs a latest release to
            # render (spec 6.1): one release, empty span, empty-coverage digest (validate_version is structural
            # and does not cross-check the worklog, so this is VALID).
            write_toml(eroot, "version.toml", "\n".join([
                "schema = 1", "", "[[release]]",
                'version = "0.1.0"',
                'date = "2026-01-01T00:00:00Z"',
                'worklog_span = []',
                'coverage_digest = "{}"'.format(_opf_release.coverage_digest([])),
            ]) + "\n")
            check("empty-write-ok", render(["--root", str(eroot)]) == EXIT_OK)
            etodo = (eroot / WORKING_DIRNAME / "TODO.md").read_text(encoding="utf-8")
            check("empty-todo-valid-empty", _EMPTY in etodo and etodo.startswith("<!-- GENERATED"))
            check("empty-store-version-bytes", (eroot / "VERSION").read_text(encoding="utf-8") == "0.1.0\n")

            # --- F3: a declared VERSION with an EMPTY version ledger fails closed, never a zero-byte write ----
            # (Runs under the scaffolded gate ON, so it reaches rendering and fails at F3, not the F1 write gate.)
            vroot = new_root()
            write_toml(vroot, "manifest.toml", manifest)
            empty_indexes(vroot)                       # version.toml is "schema = 1\n": declared, but no release
            check("empty-version-cannot-eval", render(["--root", str(vroot)]) == EXIT_CANNOT_EVALUATE)
            check("empty-version-not-zero-byte", not (vroot / "VERSION").exists())

            # --- a MISSING declared source fails closed (exit 2) --------------------------------------------
            mroot = new_root()
            write_toml(mroot, "manifest.toml", manifest)
            empty_indexes(mroot)
            (mroot / WORKING_DIRNAME / "toml" / "block.index.toml").unlink()   # TODO/BACKLOG/BLOCKS/BLOCK-INDEX need it
            check("missing-source-cannot-eval", render(["--root", str(mroot)]) == EXIT_CANNOT_EVALUATE)

            # --- a MALFORMED record fails closed (exit 2), never a silent partial view ----------------------
            broot = new_root()
            write_toml(broot, "manifest.toml", manifest)
            empty_indexes(broot)
            write_toml(broot, "finding.index.toml", "\n".join([
                "schema = 1",
                _rec("FN-1", "finding", "not-a-state", "bad status"),   # illegal status: INVALID record
            ]) + "\n")
            check("malformed-record-cannot-eval", render(["--root", str(broot)]) == EXIT_CANNOT_EVALUATE)

            # --- F-01: a manifest target that does not match the view's spec destination fails closed --------
            troot = new_root()
            write_toml(troot, "manifest.toml",
                       manifest.replace('target = "{}/TODO.md"'.format(WORKING_DIRNAME), 'target = "README.md"'))
            empty_indexes(troot)
            check("target-rebind-cannot-eval", render(["--root", str(troot)]) == EXIT_CANNOT_EVALUATE)
            check("target-rebind-not-written", not (troot / "README.md").exists())

            # --- F-01: a pre-planted symlink destination is refused (no-follow), the link target untouched ---
            symroot = new_root()
            write_toml(symroot, "manifest.toml", manifest)
            empty_indexes(symroot)
            write_toml(symroot, "version.toml", "\n".join([
                "schema = 1", "", "[[release]]",
                'version = "0.1.0"',
                'date = "2026-01-01T00:00:00Z"',
                'worklog_span = []',
                'coverage_digest = "{}"'.format(_opf_release.coverage_digest([])),
            ]) + "\n")
            outside = symroot / "outside.txt"
            outside.write_text("original\n", encoding="utf-8")
            (symroot / WORKING_DIRNAME / "TODO.md").symlink_to(outside)
            check("symlink-dest-cannot-eval", render(["--root", str(symroot)]) == EXIT_CANNOT_EVALUATE)
            check("symlink-dest-not-followed", outside.read_text(encoding="utf-8") == "original\n")

            # --- F-03: an EXTRA declared source beyond a view's required set fails closed --------------------
            xroot = new_root()
            write_toml(xroot, "manifest.toml",
                       manifest.replace('sources = ["done"]', 'sources = ["done", "finding"]'))
            empty_indexes(xroot)
            check("extra-source-cannot-eval", render(["--root", str(xroot)]) == EXIT_CANNOT_EVALUATE)

            # --- F-03: the same extra declared source, now MALFORMED, also fails closed ----------------------
            xmroot = new_root()
            write_toml(xmroot, "manifest.toml",
                       manifest.replace('sources = ["done"]', 'sources = ["done", "finding"]'))
            empty_indexes(xmroot)
            write_toml(xmroot, "finding.index.toml", "\n".join([
                "schema = 1", _rec("FN-1", "finding", "not-a-state", "bad status")]) + "\n")
            check("extra-source-malformed-cannot-eval", render(["--root", str(xmroot)]) == EXIT_CANNOT_EVALUATE)

            # --- F-04: a FORK in the decision-resolution graph (two heads for one chain) fails closed --------
            fkroot = new_root()
            write_toml(fkroot, "manifest.toml", manifest)
            empty_indexes(fkroot)
            write_toml(fkroot, "pending_decision.index.toml", "\n".join([
                "schema = 1",
                _rec("PD-1", "pending_decision", "decided", "base", decision="d",
                     decided_at="2026-09-01T00:00:00Z", decided_by="maintainer"),
                _rec("PD-2", "pending_decision", "decided", "two", decision="d",
                     decided_at="2026-09-02T00:00:00Z", decided_by="maintainer", links=[("supersedes", "PD-1")]),
                _rec("PD-3", "pending_decision", "decided", "three", decision="d",
                     decided_at="2026-09-03T00:00:00Z", decided_by="maintainer", links=[("supersedes", "PD-1")]),
            ]) + "\n")
            check("resolution-fork-cannot-eval", render(["--root", str(fkroot)]) == EXIT_CANNOT_EVALUATE)

            # --- F-04: a CYCLE in the decision-resolution graph fails closed (and does not hang) ------------
            cyroot = new_root()
            write_toml(cyroot, "manifest.toml", manifest)
            empty_indexes(cyroot)
            write_toml(cyroot, "pending_decision.index.toml", "\n".join([
                "schema = 1",
                _rec("PD-1", "pending_decision", "decided", "one", decision="d",
                     decided_at="2026-09-01T00:00:00Z", decided_by="maintainer", links=[("supersedes", "PD-2")]),
                _rec("PD-2", "pending_decision", "decided", "two", decision="d",
                     decided_at="2026-09-02T00:00:00Z", decided_by="maintainer", links=[("supersedes", "PD-1")]),
            ]) + "\n")
            check("resolution-cycle-cannot-eval", render(["--root", str(cyroot)]) == EXIT_CANNOT_EVALUATE)

            # --- F-07: deeply nested TOML trips tomllib recursion; mapped to cannot-evaluate, not a crash ---
            rroot = new_root()
            write_toml(rroot, "manifest.toml", manifest)
            empty_indexes(rroot)
            deep = "schema = 1\ndeep = " + "[" * 2000 + "]" * 2000 + "\n"   # nesting safely above the recursion limit
            write_toml(rroot, "finding.index.toml", deep)
            check("deep-toml-recursion-cannot-eval", render(["--root", str(rroot)]) == EXIT_CANNOT_EVALUATE)

            # --- a non-UTF-8 machine-dir name is rejected fail-closed UNIVERSALLY, even for a header-exempt
            # VERSION-only store. Before render()'s explicit early machine_rel UTF-8 check this store rendered
            # exit 0: the VERSION deliverable is header-exempt, so _source_set_digest never encoded the bad
            # path and nothing else keyed on its UTF-8 validity. A discovered machine-dir name is UTF-8 by
            # convention; a surrogate-escaped non-UTF-8 byte must be refused (exit 2) regardless of which
            # views are declared. This vector FAILS if that early check is reverted.
            nuroot = base / "case-nonutf8"
            nuworking = nuroot / WORKING_DIRNAME
            nuworking.mkdir(parents=True)
            nu_machine = os.fsencode(str(nuworking)) + b"/bad-\xff-dir"   # non-UTF-8 machine-dir name (bytes)
            os.mkdir(nu_machine)
            version_only_manifest = "\n".join([
                "[devprocess]",
                'standard = "devprocess"',
                'spec_version = "1.0.0"',
                'layout = "inline"',
                'posture = "required"',
                'import_status = "none"',
                "",
                "[store]",
                'sync_target = ""',
                "",
                "[modules]",
                "governance = true",
                "operational_policy = true",
                "concurrent_operation = true",
                "",
                "[types.backlog_item]", 'namespace = "BI"',
                "[types.done]", 'namespace = "DN"',
                "[types.worklog]", 'namespace = "WL"',
                "[types.finding]", 'namespace = "FN"',
                "[types.pending_decision]", 'namespace = "PD"',
                "[types.autonomous_decision]", 'namespace = "AD"',
                "[types.block]", 'namespace = "BL"',
                "[types.handoff]", 'namespace = "HO"',
                "[types.reference]", 'namespace = "RF"',
                "",
                "[vendors]", "registered = []",
                "",
                _view("VERSION", "deterministic", ["version"]),
            ]) + "\n"
            with open(os.path.join(nu_machine, b"manifest.toml"), "w", encoding="utf-8") as _mf:
                _mf.write(version_only_manifest)
            with open(os.path.join(nu_machine, b"version.toml"), "w", encoding="utf-8") as _vf:
                _vf.write("\n".join([
                    "schema = 1", "", "[[release]]",
                    'version = "0.1.0"',
                    'date = "2026-01-01T00:00:00Z"',
                    'worklog_span = []',
                    'coverage_digest = "{}"'.format(_opf_release.coverage_digest([])),
                ]) + "\n")
            # A matching root VERSION, so pre-fix --check would drift-compare to exit 0; post-fix the early
            # UTF-8 refusal returns exit 2 BEFORE any view is rendered or compared.
            (nuroot / "VERSION").write_text("0.1.0\n", encoding="utf-8")
            check("nonutf8-machine-dir-cannot-eval",
                  render(["--root", str(nuroot), "--check"]) == EXIT_CANNOT_EVALUATE)

            # --- F-08: a free-text field cannot forge markdown/HTML structure (escaped, render still clean) --
            forgeroot = new_root()
            write_toml(forgeroot, "manifest.toml", manifest)
            empty_indexes(forgeroot)
            write_toml(forgeroot, "version.toml", "\n".join([
                "schema = 1", "", "[[release]]",
                'version = "0.1.0"',
                'date = "2026-01-01T00:00:00Z"',
                'worklog_span = []',
                'coverage_digest = "{}"'.format(_opf_release.coverage_digest([])),
            ]) + "\n")
            write_toml(forgeroot, "reference.index.toml", "\n".join([
                "schema = 1",
                _rec("RF-1", "reference", "recorded", "a source",
                     refs=[("doc", "safe\\n# Forged heading\\n<!-- injected -->")]),
            ]) + "\n")
            check("forged-field-write-ok", render(["--root", str(forgeroot)]) == EXIT_OK)
            forged_view = (forgeroot / WORKING_DIRNAME / "REFERENCES.md").read_text(encoding="utf-8")
            check("forged-heading-neutralized", "\n# Forged heading" not in body_of(forged_view))
            check("forged-comment-neutralized", "<!-- injected -->" not in forged_view)

            # --- F-05: a per-record store is a CLEAR cannot-evaluate (deferred), not a silent inline render --
            prroot = new_root()
            write_toml(prroot, "manifest.toml", manifest.replace('layout = "inline"', 'layout = "per-record"'))
            empty_indexes(prroot)
            check("per-record-deferred-cannot-eval", render(["--root", str(prroot)]) == EXIT_CANNOT_EVALUATE)

            # --- B2 class-wide: a VALID record in EVERY free-text sink carries the P payload; each view
            # renders it as LITERAL text. This drives the FULL pipeline (validate -> render -> write ->
            # read) under the scaffolded gate, so every record is a schema-valid envelope, not a hand-built
            # dict. Each affected view is asserted to contain the EXACT escaped bytes ESC and NOT the raw
            # payload P, so removing _md_text from any sink that feeds that view flips an assertion. The
            # schema-CONSTRAINED sinks (id, status/state, scopes, link ids, timestamps) cannot carry a
            # metacharacter, so their escaping is a verified no-op; the free-text sinks are the movable ones.
            injroot = new_root()
            write_toml(injroot, "manifest.toml", manifest)
            empty_indexes(injroot)
            write_toml(injroot, "backlog_item.index.toml", "\n".join([
                "schema = 1",
                _rec("BI-1", "backlog_item", "open", P),
            ]) + "\n")
            write_toml(injroot, "block.index.toml", "\n".join([
                "schema = 1",
                _rec("BL-1", "block", "active", P, scopes=["BI-2"]),   # scopes BI-2, not BI-1: BI-1 stays actionable
            ]) + "\n")
            write_toml(injroot, "done.index.toml", "\n".join([
                "schema = 1",
                _rec("DN-1", "done", "recorded", P, links=[("receipt_of", "BI-1")]),
            ]) + "\n")
            write_toml(injroot, "finding.index.toml", "\n".join([
                "schema = 1",
                _rec("FN-1", "finding", "fixed", "f", severity=P),
            ]) + "\n")
            write_toml(injroot, "pending_decision.index.toml", "\n".join([
                "schema = 1",
                _rec("PD-1", "pending_decision", "decided", P, decision=P,
                     decided_at="2026-09-01T00:00:00Z", decided_by="maintainer"),
            ]) + "\n")
            write_toml(injroot, "autonomous_decision.index.toml", "\n".join([
                "schema = 1",
                _rec("AD-1", "autonomous_decision", "recorded", P, classification=P, action=P),
            ]) + "\n")
            write_toml(injroot, "handoff.index.toml", "\n".join([
                "schema = 1",
                _rec("HO-1", "handoff", "current", P),
            ]) + "\n")
            write_toml(injroot, "reference.index.toml", "\n".join([
                "schema = 1",
                _rec("RF-1", "reference", "recorded", P, refs=[("doc", P)]),
            ]) + "\n")
            write_toml(injroot, "worklog.toml", "\n".join([
                "schema = 1",
                _entry("WL-1", "added", P),
            ]) + "\n")
            write_toml(injroot, "version.toml", "\n".join([
                "schema = 1", "", "[[release]]",
                'version = "0.1.0"',
                'date = "2026-01-01T00:00:00Z"',
                'worklog_span = []',
                'coverage_digest = "{}"'.format(_opf_release.coverage_digest([])),
            ]) + "\n")
            check("class-wide-injection-write-ok", render(["--root", str(injroot)]) == EXIT_OK)

            def iview(name):
                return body_of((injroot / WORKING_DIRNAME / name).read_text(encoding="utf-8"))

            # Every view whose body carries a free-text sink fed by P must contain ESC and never the raw
            # P: TODO/BACKLOG/PIPELINE (backlog title), DONE (done title), FINDINGS (severity), DECISIONS
            # (pending + autonomous titles), BLOCKS (block title), HANDOFF (handoff title), REFERENCES
            # (reference title + ref locator), WORKLOG (summary), and the two declared mirrors (title
            # projection). VERSION/VERSION.md are covered by the unit render_version_md vector above
            # (their release/summary fields are grammar-constrained).
            for _vn in ("TODO.md", "BACKLOG.md", "PIPELINE.md", "DONE.md", "FINDINGS.md",
                        "DECISIONS.md", "BLOCKS.md", "HANDOFF.md", "REFERENCES.md", "WORKLOG.md",
                        "BACKLOG_ITEM-INDEX.md", "BLOCK-INDEX.md"):
                _body = iview(_vn)
                check("class-wide-esc-present-" + _vn, ESC in _body)
                check("class-wide-no-raw-payload-" + _vn, P not in _body)

            # CLASS B (codex, full-render fail-closed): a SCHEMA-VALID backlog_item whose title carries a
            # zero-width or bidi-control codepoint renders a view body whose bytes fail the authoritative
            # byte-canon scan. The generator FAILS CLOSED (cannot-evaluate) and writes NOTHING, rather than
            # emitting an invalid view. Driven under the scaffolded write gate; pre-fix this rendered clean
            # (exit 0) and wrote invalid bytes. A lone CR is excluded: _md_text collapses it upstream.
            _bc_version = "\n".join([
                "schema = 1", "", "[[release]]",
                'version = "0.1.0"',
                'date = "2026-01-01T00:00:00Z"',
                'worklog_span = []',
                'coverage_digest = "{}"'.format(_opf_release.coverage_digest([])),
            ]) + "\n"
            for _bcls, _bcp in (("zero-width-u200b", 0x200B), ("zero-width-u200c", 0x200C),
                                ("zero-width-u200d", 0x200D), ("word-joiner-u2060", 0x2060),
                                ("bom-ufeff", 0xFEFF), ("bidi-rlo-u202e", 0x202E)):
                _bcroot = new_root()
                write_toml(_bcroot, "manifest.toml", manifest)
                empty_indexes(_bcroot)
                write_toml(_bcroot, "version.toml", _bc_version)
                write_toml(_bcroot, "backlog_item.index.toml", "\n".join([
                    "schema = 1",
                    _rec("BI-1", "backlog_item", "open", "title" + chr(_bcp) + "here"),
                ]) + "\n")
                check("byte-canon-fail-closed-" + _bcls,
                      render(["--root", str(_bcroot)]) == EXIT_CANNOT_EVALUATE)
                check("byte-canon-wrote-nothing-" + _bcls,
                      not (_bcroot / WORKING_DIRNAME / "TODO.md").exists())

            # round-3 (CLASS B positive): a SCHEMA-VALID backlog_item whose title merely ends in a SPACE
            # renders a body line ending in whitespace; _render_resolved now normalizes trailing line
            # whitespace before the byte-canon scan, so the render SUCCEEDS (EXIT_OK) and writes a clean
            # view. Pre round-3 this failed closed at exit 2 over benign trailing whitespace. Modelled on the
            # CLASS B store construction; proves the over-fire fix end to end, not only at a unit level.
            import check_byte_canon  # authoritative byte-canon leg; pure function over bytes
            _tsroot = new_root()
            write_toml(_tsroot, "manifest.toml", manifest)
            empty_indexes(_tsroot)
            write_toml(_tsroot, "version.toml", _bc_version)
            write_toml(_tsroot, "backlog_item.index.toml", "\n".join([
                "schema = 1",
                _rec("BI-1", "backlog_item", "open", "trailing space title "),
            ]) + "\n")
            check("trailing-space-title-renders",
                  render(["--root", str(_tsroot)]) == EXIT_OK)
            _tstodo = (_tsroot / WORKING_DIRNAME / "TODO.md").read_text(encoding="utf-8")
            # (a) the title text survived, minus its trailing whitespace (no rendered line ends in space/tab);
            # (b) exactly one terminal newline; (c) the emitted bytes pass the authoritative byte-canon scan.
            check("trailing-space-title-text-survived", "trailing space title" in _tstodo)
            check("trailing-space-title-no-trailing-ws",
                  all(not ln.endswith((" ", "\t")) for ln in _tstodo.split("\n")))
            check("trailing-space-title-one-terminal-newline",
                  _tstodo.endswith("\n") and not _tstodo.endswith("\n\n"))
            check("trailing-space-title-byte-canon-clean",
                  check_byte_canon.scan_bytes(_tstodo.encode("utf-8")) == [])

            # round-3 (Unicode fix): a title ending in a NON-BREAKING SPACE (U+00A0) is ALSO normalized out
            # by _render_resolved's Unicode str.rstrip() (the SAME predicate check_byte_canon uses), so it
            # renders clean (EXIT_OK) rather than over-firing the byte-canon refusal. This proves the fix
            # covers the Unicode trailing-whitespace codepoints, not only ASCII space/tab.
            _tsnbroot = new_root()
            write_toml(_tsnbroot, "manifest.toml", manifest)
            empty_indexes(_tsnbroot)
            write_toml(_tsnbroot, "version.toml", _bc_version)
            write_toml(_tsnbroot, "backlog_item.index.toml", "\n".join([
                "schema = 1",
                _rec("BI-1", "backlog_item", "open", "trailing nbsp title" + chr(0x00A0)),
            ]) + "\n")
            check("trailing-nbsp-title-renders",
                  render(["--root", str(_tsnbroot)]) == EXIT_OK)
            _tsnbtodo = (_tsnbroot / WORKING_DIRNAME / "TODO.md").read_text(encoding="utf-8")
            check("trailing-nbsp-title-text-survived", "trailing nbsp title" in _tsnbtodo)
            check("trailing-nbsp-title-normalized", chr(0x00A0) not in _tsnbtodo)
            check("trailing-nbsp-title-byte-canon-clean",
                  check_byte_canon.scan_bytes(_tsnbtodo.encode("utf-8")) == [])

            # CLASS 3 (B3): /proposed is NONTERMINAL, and `withdrawn` is a valid terminal. In PIPELINE,
            # DECISIONS, and HANDOFF a proposed record is surfaced as AWAITING RATIFICATION (never under its
            # effective/terminal state, never omitted), and no valid status is dropped. This full VALID
            # store carries a done/proposed backlog item, a withdrawn and a decided/proposed decision, and a
            # superseded/proposed handoff; reverting any Class-3 renderer flips an assertion below. Runs
            # under the scaffolded write gate so the full validate->render->write->read pipeline exercises
            # schema-valid envelopes (a decided/proposed carries the full resolution bundle, spec 8.5).
            proproot = new_root()
            write_toml(proproot, "manifest.toml", manifest)
            empty_indexes(proproot)
            write_toml(proproot, "backlog_item.index.toml", "\n".join([
                "schema = 1",
                _rec("BI-1", "backlog_item", "open", "still open"),
                _rec("BI-7", "backlog_item", "done/proposed", "proposed done", actor="assistant"),
            ]) + "\n")
            write_toml(proproot, "pending_decision.index.toml", "\n".join([
                "schema = 1",
                _rec("PD-1", "pending_decision", "withdrawn", "withdrew it"),
                _rec("PD-2", "pending_decision", "decided/proposed", "proposed ruling", actor="assistant",
                     decision="do Z", decided_at="2026-09-02T00:00:00Z", decided_by="maintainer"),
            ]) + "\n")
            write_toml(proproot, "handoff.index.toml", "\n".join([
                "schema = 1",
                _rec("HO-1", "handoff", "current", "the current handoff"),
                _rec("HO-2", "handoff", "superseded/proposed", "proposed supersede", actor="assistant"),
            ]) + "\n")
            write_toml(proproot, "version.toml", "\n".join([
                "schema = 1", "", "[[release]]",
                'version = "0.1.0"',
                'date = "2026-01-01T00:00:00Z"',
                'worklog_span = []',
                'coverage_digest = "{}"'.format(_opf_release.coverage_digest([])),
            ]) + "\n")
            check("proposed-store-write-ok", render(["--root", str(proproot)]) == EXIT_OK)

            def pview(name):
                return (proproot / WORKING_DIRNAME / name).read_text(encoding="utf-8")

            ppl = pview("PIPELINE.md")
            check("pipeline-proposed-awaiting-section", "## awaiting ratification" in ppl)
            check("pipeline-proposed-shows-full-status", "done/proposed" in ppl)
            check("pipeline-proposed-not-in-effective-group", "## done" not in ppl)
            pdec = pview("DECISIONS.md")
            check("decisions-withdrawn-section", "## Withdrawn decisions" in pdec)
            check("decisions-withdrawn-record-listed", "PD-1" in pdec)
            check("decisions-proposed-awaiting-section", "## Awaiting ratification" in pdec)
            check("decisions-proposed-shows-full-status", "decided/proposed" in pdec)
            _eff = pdec.index("## Effective resolutions")
            _awa = pdec.index("## Awaiting ratification")
            check("decisions-proposed-not-effective", "PD-2" in pdec[_awa:] and "PD-2" not in pdec[_eff:_awa])
            pho = pview("HANDOFF.md")
            check("handoff-proposed-awaiting-section", "## Awaiting ratification" in pho)
            check("handoff-proposed-shows-full-status", "superseded/proposed" in pho)
            _sup = pho.index("## Superseded")
            _hawa = pho.index("## Awaiting ratification")
            check("handoff-proposed-not-in-superseded", "HO-2" in pho[_hawa:] and "HO-2" not in pho[_sup:_hawa])
        finally:
            _WRITE_GATE_COMPOSED = _saved_gate

        # --- gate-OFF fail-safe region (F1 write gate at its default False) -------------------------
        na = base / "not-adopted"
        na.mkdir()
        check("not-adopted-not-applicable", render(["--root", str(na)]) == EXIT_OK)
        # F1: an ungated WRITE via any entry is refused (cannot-evaluate) and writes nothing. wroot carries
        # a VALID one-release version.toml (like eroot/symroot), so that with the F1 sentinel flipped True
        # the render would proceed to a clean write (exit 0) rather than fail at F3's empty-VERSION guard;
        # that makes this pair FAIL for the F1 mutant (unmasking the write-gate test, M2), while at the real
        # default (gate False) the write is refused at exit 2 and nothing is written.
        wroot = new_root()
        write_toml(wroot, "manifest.toml", manifest)
        empty_indexes(wroot)
        write_toml(wroot, "version.toml", "\n".join([
            "schema = 1", "", "[[release]]",
            'version = "0.1.0"',
            'date = "2026-01-01T00:00:00Z"',
            'worklog_span = []',
            'coverage_digest = "{}"'.format(_opf_release.coverage_digest([])),
        ]) + "\n")
        check("write-refused-without-integrity-gate", render(["--root", str(wroot)]) == EXIT_CANNOT_EVALUATE)
        check("write-refused-wrote-nothing", not (wroot / WORKING_DIRNAME / "TODO.md").exists())
        # --check is unaffected by the write gate: eroot's views were already written under the scaffold
        # above, and eroot is byte-stable (nothing edited it), so a read-only re-check is clean.
        check("check-mode-works-without-gate", render(["--root", str(eroot), "--check"]) == EXIT_OK)
        # F10 CLI fail-closed parse (parse precedes resolution; na isolates this from the write gate).
        check("cli-dangling-root", render(["--root", str(na), "--root"]) == EXIT_CANNOT_EVALUATE)
        check("cli-unknown-arg", render(["--root", str(na), "--bogus"]) == EXIT_CANNOT_EVALUATE)
        check("cli-misspelled-check-refused", render(["--root", str(na), "--chek"]) == EXIT_CANNOT_EVALUATE)

        # B3: `--root` requires a non-empty, non-option-looking directory argument. An empty value (which
        # would silently normalize to the cwd) and an option-looking token (a swallowed next flag) are each
        # a parser-level cannot-evaluate (exit 2), fail-closed. Parse precedes store resolution, so these
        # are independent of the write gate.
        check("cli-empty-root", render(["--root", ""]) == EXIT_CANNOT_EVALUATE)
        check("cli-option-looking-root", render(["--root", "--bogus"]) == EXIT_CANNOT_EVALUATE)

        # F9(4): individually record-valid but cross-record NONCONFORMANT stores can no longer be
        # silently WRITTEN. These assert the F1 write-refusal fail-safe ONLY: the cross-record
        # uniqueness / handoff / resolution GRADING remains U6 validate_store's job (F2, deferred).
        # They do NOT claim U4 grades cross-record conformance now.
        dupidroot = new_root()
        write_toml(dupidroot, "manifest.toml", manifest)
        empty_indexes(dupidroot)
        write_toml(dupidroot, "backlog_item.index.toml", "\n".join([
            "schema = 1",
            _rec("BI-1", "backlog_item", "open", "first"),
            _rec("BI-1", "backlog_item", "open", "duplicate id"),   # cross-record duplicate ID
        ]) + "\n")
        check("duplicate-id-write-refused", render(["--root", str(dupidroot)]) == EXIT_CANNOT_EVALUATE)

        dangroot = new_root()
        write_toml(dangroot, "manifest.toml", manifest)
        empty_indexes(dangroot)
        write_toml(dangroot, "pending_decision.index.toml", "\n".join([
            "schema = 1",
            _rec("PD-3", "pending_decision", "decided", "dangling", decision="d",
                 decided_at="2026-09-03T00:00:00Z", decided_by="maintainer",
                 links=[("supersedes", "PD-999")]),                 # target absent from the store
        ]) + "\n")
        check("dangling-supersedes-write-refused", render(["--root", str(dangroot)]) == EXIT_CANNOT_EVALUATE)

        multihoroot = new_root()
        write_toml(multihoroot, "manifest.toml", manifest)
        empty_indexes(multihoroot)
        write_toml(multihoroot, "handoff.index.toml", "\n".join([
            "schema = 1",
            _rec("HO-1", "handoff", "current", "one current"),
            _rec("HO-2", "handoff", "current", "two current"),      # spec 8.5 allows at most one
        ]) + "\n")
        check("multiple-current-handoff-write-refused",
              render(["--root", str(multihoroot)]) == EXIT_CANNOT_EVALUATE)

    finally:
        shutil.rmtree(base, ignore_errors=True)

    if failures:
        print("OPF-VIEWS SELF-TEST FAIL ({} checks, {} failed):".format(checked[0], len(failures)),
              file=sys.stderr)
        for f in failures:
            print("  - " + f, file=sys.stderr)
        return EXIT_DRIFT
    print("opf-views self-test: PASS ({} checks) -- independent expected-bytes goldens per view, drift "
          "detection, the closed transform vocabulary (unknown filter/sort/group/project refused), the "
          "source-set-digest sensitivity, the block-actionability join with deduped numeric-order "
          "annotations and the decision-supersession join (fork/cycle refused, duplicate supersedes link "
          "tolerated), GFM extended-autolink neutralization (bare http/https/www/email inert) via the "
          "render-faithful autolink oracle (code-span-first ordering, http/https/ftp + mailto/xmpp, "
          "strict entity termination, email tail rule) and adjacent-token coalescing, the "
          "header HTML-comment sink (interpolated -->/--!> neutralized), NONTERMINAL /proposed and valid "
          "withdrawn records surfaced as awaiting-ratification in PIPELINE/DECISIONS/HANDOFF, "
          "spec-destination target binding and no-follow contained writes, declared-source equality, "
          "deep-TOML and free-text (severity and covers) fail-closed AND whole-body byte-canon "
          "fail-closed (zero-width/bidi refused, never emitted), per-record and module-type "
          "deferrals, missing-and-malformed-source fail-closed, empty-store valid-empty with a rendered "
          "VERSION, the empty-VERSION-ledger fail-closed, the CLI fail-closed parse, and the F1 "
          "write-refusal fail-safe (ungated write refused, --check unaffected)".format(checked[0]))
    return EXIT_OK


# --- self-test fixture builders (kept below the product code) ----------------------------------------

def _view(name, kind, sources):
    src = ", ".join('"{}"'.format(s) for s in sources)
    return '[views."{}"]\nkind = "{}"\nsources = [{}]\ntarget = "{}"\n'.format(
        name, kind, src, name if name == "VERSION" else "{}/{}".format(WORKING_DIRNAME, name))


def _rec(rid, rtype, status, title, *, actor="maintainer", severity=None, decision=None, decided_at=None,
         decided_by=None, classification=None, action=None, scopes=None, links=None, refs=None):
    """A synthetic full-envelope record as TOML text (spec 8.3). Timestamps are fixed literals so the
    fixture is byte-stable (this is a fixture author, not a live clock). `actor` selects the actor kind: a
    `/proposed` status is legal only for an assistant or automation actor (spec 8.4/8.5)."""
    lines = ["", "[[record]]",
             'id = "{}"'.format(rid),
             'type = "{}"'.format(rtype),
             'status = "{}"'.format(status),
             'title = "{}"'.format(title),
             'created_at = "2026-01-01T00:00:00Z"',
             'updated_at = "2026-01-02T00:00:00Z"',
             'actor = {{ kind = "{}" }}'.format(actor)]
    if severity is not None:
        lines.append('severity = "{}"'.format(severity))
    if decision is not None:
        lines.append('decision = "{}"'.format(decision))
    if decided_at is not None:
        lines.append('decided_at = "{}"'.format(decided_at))
    if decided_by is not None:
        lines.append('decided_by = "{}"'.format(decided_by))
    if classification is not None:
        lines.append('classification = "{}"'.format(classification))
    if action is not None:
        lines.append('action = "{}"'.format(action))
    if scopes is not None:
        lines.append("scopes = [{}]".format(", ".join('"{}"'.format(s) for s in scopes)))
    if links is not None:
        lines.append("links = [{}]".format(", ".join(
            '{{ rel = "{}", id = "{}" }}'.format(rel, tid) for rel, tid in links)))
    if refs is not None:
        lines.append("refs = [{}]".format(", ".join(
            '{{ kind = "{}", locator = "{}", note = "" }}'.format(kind, loc) for kind, loc in refs)))
    return "\n".join(lines) + "\n"


def _entry(wid, kind, summary):
    """A synthetic worklog entry as TOML text (reduced envelope, spec 8.3)."""
    return "\n".join([
        "", "[[entry]]",
        'id = "{}"'.format(wid),
        'date = "2026-01-01T00:00:00Z"',
        "actor = { kind = \"maintainer\" }",
        'kind = "{}"'.format(kind),
        'summary = "{}"'.format(summary),
    ]) + "\n"


if __name__ == "__main__":
    _argv = sys.argv[1:]
    # Until the U6 store-integrity gate lands, the CLI runs CHECK-only (drift detection, never a
    # write): a write requires that gate, which does not exist yet, so default the command line to
    # --check. A write invocation through any other entry still fails closed in render(). Appending
    # --check is a deliberate default, not a parse relaxation; the F10 parser still refuses any
    # unrecognized token alongside it.
    if "--check" not in _argv:
        _argv = _argv + ["--check"]
    sys.exit(render(_argv))
