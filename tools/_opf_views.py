"""Deterministic view generators and the closed transform vocabulary for the DevProcess (OPF) store
(OPF core-tooling, unit U4). Stdlib only; adopter-rooted; byte-reproducible.

This is the multi-source render side of `opf render`. Where the shared driver in `opf_render.py`
(run_generator) renders one or more targets from a SINGLE source TOML against the pack's own repo root,
U4 renders the composed views, the 1:1 index mirrors, and the root VERSION deliverable from a resolved
adopter store whose views draw on SEVERAL sources at once (a backlog view joins backlog_item against
block, a decisions view walks the pending_decision supersession chain). run_generator is single-source,
so U4 does NOT fork it; it reuses the low-level primitives (the store resolution and contained no-follow
reads of `_opf_store`/`_journal`, the record/ledger validators of `_opf_schema`/`_opf_release`, and the
fail-closed `reconcile` of `_gen_common`) and drives them with the same render-all-then-reconcile-each
two-phase ordering: every target's payload is rendered before any target is written, so a fail-closed
source aborts the whole render before a single file is touched, and the targets are then reconciled in a
stable (name-sorted) order.

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
"""
import hashlib
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _journal          # noqa: E402  contained (dir-fd, no-follow) readers + JournalError + containment probe
import _opf_store        # noqa: E402  store resolution + discovery + manifest base/profile schema
import _opf_schema       # noqa: E402  record envelope + baseline type schemas + status parsing + id shape
import _opf_release      # noqa: E402  version.toml + worklog.toml validators + SemVer
from _gen_common import reconcile  # noqa: E402  the fail-closed write/drift primitive shared with opf_render

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
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
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
    command. It carries NO timestamp, so a re-render of unchanged sources is byte-identical."""
    sources = ", ".join(sorted(source_blobs)) if source_blobs else "(none)"
    return (
        "<!-- GENERATED by {cmd} (tools/_opf_views.py). DO NOT EDIT; edit the store and regenerate.\n"
        "sources: {sources}\n"
        "schema: {schema}; generator: {gen}/{genver}\n"
        "source-set-digest: {digest}\n"
        "regenerate: {cmd}\n"
        "-->\n"
    ).format(cmd=REGEN_COMMAND, sources=sources, schema=SCHEMA_VERSION, gen=GENERATOR_NAME,
             genver=GENERATOR_VERSION, digest=_source_set_digest(source_blobs))


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


# --- the closed transform vocabulary (spec 10.2) -----------------------------------------------------

def t_filter(records, predicate):
    """Filter on a declared field predicate: keep each record for which predicate(record) is true. The
    predicate is drawn from the closed set of field predicates the renderers build (a state membership, a
    status membership, an actionability flag); no ad hoc composition enters here (spec 10.2)."""
    return [r for r in records if predicate(r)]


def t_sort(records, keys=()):
    """Sort on declared keys with the record ID as the FINAL tie-breaker (spec 10.2). `keys` is a tuple of
    field names, compared as their string values in order; the ID numeric key always terminates the sort so
    the order is total and deterministic even when the declared keys tie."""
    return sorted(records, key=lambda r: tuple(str(r.get(k, "")) for k in keys) + (_id_key(r),))


def t_group(records, field, order=None):
    """Group by a declared field (spec 10.2). Returns a list of (value, records) pairs; `order` fixes the
    leading group order and any remaining values follow sorted, so grouping is deterministic. Records
    within a group are ID-sorted."""
    buckets = {}
    for r in records:
        buckets.setdefault(str(r.get(field, "")), []).append(r)
    keys = list(order or ())
    keys += sorted(k for k in buckets if k not in keys)
    return [(k, t_sort(buckets[k])) for k in keys if k in buckets]


def t_project(record, columns):
    """Project declared columns (spec 10.2). Returns an ordered list of (column, value) pairs, omitting a
    column the record does not carry, so a view renders a stable, declared column set rather than the whole
    record."""
    return [(c, record[c]) for c in columns if c in record]


def join_actionability(items, blocks):
    """The block-actionability join (spec 8.5): an UNQUALIFIED `active` block HIDES every record it scopes.
    Returns (blocked_by, hidden): blocked_by maps a scoped record ID to the sorted block IDs that scope it,
    and hidden is the set of all scoped IDs. A block that is `active/proposed` (a proposal, not a grant) or
    in a terminal state (`released`, `expired`) never hides anything, so only a ratified active block
    counts, exactly as the actionability rule requires."""
    blocked_by = {}
    for b in blocks:
        if _state(b) == "active" and not _is_proposed(b):
            for sid in b.get("scopes", []) or []:
                if isinstance(sid, str):
                    blocked_by.setdefault(sid, []).append(b.get("id"))
    for sid in blocked_by:
        blocked_by[sid] = sorted(x for x in blocked_by[sid] if isinstance(x, str))
    return blocked_by, set(blocked_by)


def is_actionable(item, hidden):
    """A backlog item is actionable when its state is `open` or `active` and no unqualified active block
    scopes it (spec 8.5). `hidden` is the scoped-ID set from join_actionability."""
    return _state(item) in ("open", "active") and item.get("id") not in hidden


def join_resolution(decisions):
    """The decision-resolution supersession-chain join (spec 8.5): a decided decision may be superseded by
    a newer one linking `supersedes`, and exactly one current effective resolution exists per chain.
    Returns (current, superseded, supersedes_map): current is the set of decision IDs not superseded by any
    other decision in the set (the chain heads), superseded is the complementary set, and supersedes_map
    maps each decision ID to the sorted IDs it directly supersedes."""
    supersedes_map = {}
    superseded = set()
    ids = {d.get("id") for d in decisions if isinstance(d.get("id"), str)}
    for d in decisions:
        did = d.get("id")
        targets = sorted(t for t in _links_of(d, "supersedes") if t in ids)
        supersedes_map[did] = targets
        superseded.update(targets)
    current = {d.get("id") for d in decisions if isinstance(d.get("id"), str)} - superseded
    return current, superseded, supersedes_map


# --- view body renderers -----------------------------------------------------------------------------
#
# Each renderer takes a `src` dict mapping a declared source name to its loaded records (or, for the
# worklog/version ledgers, their loaded rows) and returns the markdown BODY (the header is prepended by
# the driver, except for the header-exempt root VERSION deliverable). Bodies are deterministic: every
# iteration is over an explicitly sorted or grouped sequence.

_EMPTY = "_No records._"


def _lines(title, body_lines):
    """Assemble a view body: a level-1 heading, a blank line, and either the body lines or the empty
    marker. Always ends with exactly one trailing newline."""
    body = "\n".join(body_lines) if body_lines else _EMPTY
    return "# {}\n\n{}\n".format(title, body)


def render_todo(src):
    """TODO: the actionable open and active backlog items (block-actionability join, then a state filter,
    then an ID-tie-broken sort). An item an unqualified active block scopes is hidden."""
    items, blocks = src["backlog_item"], src["block"]
    _blocked_by, hidden = join_actionability(items, blocks)
    actionable = t_filter(items, lambda r: is_actionable(r, hidden))
    ordered = t_sort(actionable, keys=("status",))
    lines = ["- {} ({}) {}".format(r["id"], _state(r), r.get("title", "")) for r in ordered]
    return _lines("TODO", lines)


def render_backlog(src):
    """BACKLOG: every backlog item in ID order, each annotated by the block-actionability join as
    actionable or blocked by the naming block(s)."""
    items, blocks = src["backlog_item"], src["block"]
    blocked_by, hidden = join_actionability(items, blocks)
    lines = []
    for r in t_sort(items):
        note = "actionable" if is_actionable(r, hidden) else (
            "blocked by {}".format(", ".join(blocked_by[r["id"]])) if r.get("id") in hidden
            else "not actionable ({})".format(_state(r)))
        lines.append("- {} ({}) {} -- {}".format(r["id"], r.get("status", ""), r.get("title", ""), note))
    return _lines("BACKLOG", lines)


def render_pipeline(src):
    """PIPELINE: one line per backlog item, grouped by state in the lifecycle order, with a blocked marker
    from the block-actionability join."""
    items, blocks = src["backlog_item"], src["block"]
    blocked_by, hidden = join_actionability(items, blocks)
    groups = t_group(items, "status", order=[])
    # Group by STATE, not the full status string, so `active` and `active/proposed` share a section.
    by_state = {}
    for r in items:
        by_state.setdefault(_state(r), []).append(r)
    order = ["open", "active", "done", "dropped"]
    keys = order + sorted(k for k in by_state if k not in order)
    out = []
    for state in keys:
        if state not in by_state:
            continue
        out.append("## {}".format(state))
        for r in t_sort(by_state[state]):
            marker = " [blocked by {}]".format(", ".join(blocked_by[r["id"]])) if r.get("id") in hidden else ""
            out.append("- {} {}{}".format(r["id"], r.get("title", ""), marker))
        out.append("")
    body = "\n".join(out).rstrip("\n") if out else _EMPTY
    return "# {}\n\n{}\n".format("PIPELINE", body)


def render_done(src):
    """DONE: the completion receipts in ID order, each naming the backlog item it is a receipt of."""
    lines = []
    for r in t_sort(src["done"]):
        recs = _links_of(r, "receipt_of")
        suffix = " (receipt_of {})".format(", ".join(recs)) if recs else ""
        lines.append("- {} {}{}".format(r["id"], r.get("title", ""), suffix))
    return _lines("DONE", lines)


def render_findings(src):
    """FINDINGS: findings grouped by status, then ID-sorted, each with its graded severity when present."""
    out = []
    for status, group in t_group(src["finding"], "status"):
        out.append("## {}".format(status))
        for r in group:
            sev = " (severity: {})".format(r["severity"]) if "severity" in r else ""
            out.append("- {}{} {}".format(r["id"], sev, r.get("title", "")))
        out.append("")
    body = "\n".join(out).rstrip("\n") if out else _EMPTY
    return "# {}\n\n{}\n".format("FINDINGS", body)


def render_decisions(src):
    """DECISIONS: the pending decisions (open), the effective resolutions (the head of each supersedes
    chain), the superseded resolutions, and the autonomous decisions, via the decision-resolution join."""
    pend, auto = src["pending_decision"], src["autonomous_decision"]
    current, superseded, supersedes_map = join_resolution(pend)
    open_pd = t_sort(t_filter(pend, lambda r: _state(r) == "open"))
    effective = t_sort([r for r in pend if _state(r) == "decided" and r.get("id") in current])
    gone = t_sort([r for r in pend if r.get("id") in superseded])
    out = ["## Pending decisions"]
    out += (["- {} {}".format(r["id"], r.get("title", "")) for r in open_pd] or [_EMPTY])
    out += ["", "## Effective resolutions"]
    if effective:
        for r in effective:
            sup = supersedes_map.get(r["id"]) or []
            tail = " (supersedes {})".format(", ".join(sup)) if sup else ""
            out.append("- {} {}{}".format(r["id"], r.get("title", ""), tail))
    else:
        out.append(_EMPTY)
    out += ["", "## Superseded resolutions"]
    out += (["- {} {}".format(r["id"], r.get("title", "")) for r in gone] or [_EMPTY])
    out += ["", "## Autonomous decisions"]
    out += (["- {} {}".format(r["id"], r.get("title", "")) for r in t_sort(auto)] or [_EMPTY])
    return "# {}\n\n{}\n".format("DECISIONS", "\n".join(out))


def render_blocks(src):
    """BLOCKS: every block in ID order with its status, the records it scopes, and a proposed marker."""
    lines = []
    for r in t_sort(src["block"]):
        scopes = ", ".join(str(s) for s in (r.get("scopes", []) or []))
        lines.append("- {} ({}) scopes [{}] -- {}".format(
            r["id"], r.get("status", ""), scopes, r.get("title", "")))
    return _lines("BLOCKS", lines)


def render_handoff(src):
    """HANDOFF: the single current handoff first (at most one exists, spec 8.5), then the superseded ones
    in ID order."""
    handoffs = src["handoff"]
    out = ["## Current"]
    cur = t_sort(t_filter(handoffs, lambda r: _state(r) == "current"))
    out += (["- {} {}".format(r["id"], r.get("title", "")) for r in cur] or [_EMPTY])
    out += ["", "## Superseded"]
    old = t_sort(t_filter(handoffs, lambda r: _state(r) == "superseded"))
    out += (["- {} {}".format(r["id"], r.get("title", "")) for r in old] or [_EMPTY])
    return "# {}\n\n{}\n".format("HANDOFF", "\n".join(out))


def render_references(src):
    """REFERENCES: the captured references in ID order, each with its captured refs."""
    lines = []
    for r in t_sort(src["reference"]):
        lines.append("- {} {}".format(r["id"], r.get("title", "")))
        for ref in r.get("refs", []) or []:
            if isinstance(ref, dict):
                lines.append("  - {}: {}".format(ref.get("kind", "?"), ref.get("locator", "")))
    return _lines("REFERENCES", lines)


def render_worklog(src):
    """WORKLOG: the durable worklog entries in ID (WL number) order, each with its date, change kind, and
    one-line summary (spec 6.2)."""
    entries = sorted(src["worklog"], key=_id_key)
    lines = ["- {} ({}) [{}] {}".format(
        e.get("id"), e.get("date", ""), e.get("kind", ""), e.get("summary", "")) for e in entries]
    return _lines("WORKLOG", lines)


def render_version_md(src):
    """VERSION.md: the optional human view of the version ledger (spec 6.1): each release with its date and
    worklog span, then the changelog summary rows."""
    releases, summaries = src["version"]["releases"], src["version"]["summaries"]
    out = ["## Releases"]
    if releases:
        for r in releases:
            span = r.get("worklog_span", [])
            span_txt = "{}..{}".format(span[0], span[1]) if len(span) == 2 else "(none)"
            out.append("- {} ({}) worklog {}".format(r.get("version"), r.get("date", ""), span_txt))
    else:
        out.append(_EMPTY)
    out += ["", "## Changelog summaries"]
    if summaries:
        for s in summaries:
            out.append("- {} ({})".format(s.get("covers"), s.get("status")))
    else:
        out.append(_EMPTY)
    return "# {}\n\n{}\n".format("VERSION", "\n".join(out))


def render_version_file(src):
    """The root VERSION deliverable: the LATEST release's version string as EXACT bytes plus a trailing LF
    (spec 6.1/5.8), matching the pack's own generated VERSION. HEADER-EXEMPT: its content is exact version
    bytes, so a do-not-edit header would corrupt it. An empty ledger (no releases) renders EMPTY bytes, the
    valid-empty state, rather than a fabricated version."""
    releases = src["version"]["releases"]
    latest, latest_key = None, None
    for r in releases:
        key = _opf_release.parse_semver(r.get("version"))
        if key is not None and (latest_key is None or key > latest_key):
            latest, latest_key = r.get("version"), key
    return "" if latest is None else "{}\n".format(latest)


def render_mirror(type_name, records):
    """A 1:1 <TYPE>-INDEX.md mirror (spec 10.1): each record in ID order rendered as a projection of its
    declared columns, mirroring the machine index for human reading."""
    columns = ("type", "status", "title", "created_at", "updated_at", "date", "kind", "severity",
               "decision", "decided_at", "decided_by", "classification", "action", "scopes")
    out = []
    for r in t_sort(records):
        out.append("## {}".format(r.get("id")))
        for col, val in t_project(r, columns):
            if isinstance(val, list):
                val = "[{}]".format(", ".join(str(x) for x in val))
            out.append("- {}: {}".format(col, val))
        actor = r.get("actor")
        if isinstance(actor, dict) and actor.get("kind"):
            out.append("- actor: {}".format(actor["kind"]))
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

def _target_path(product_root, store_root, target):
    """Resolve a view/deliverable target to an absolute path (spec 9): a target under `.working/` is
    relative to the STORE repository root; a public target (VERSION) is relative to the PRODUCT repository
    root, so the public deliverables land in the product repo whatever the store topology (spec 5.8)."""
    if target == WORKING_DIRNAME or target.startswith(WORKING_DIRNAME + "/"):
        return store_root / target
    return product_root / target


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
    before any target is reconciled, so a fail-closed source aborts before a single file is written."""
    check = "--check" in argv
    root = "."
    if "--root" in argv:
        i = argv.index("--root")
        if i + 1 >= len(argv):
            print("opf render: --root requires a directory argument", file=sys.stderr)
            return EXIT_CANNOT_EVALUATE
        root = argv[i + 1]
    product_root = Path(os.path.abspath(root))

    res = _opf_store.resolve_store(product_root)
    if res.status == _opf_store.NOT_ADOPTED:
        print("opf render: NOT APPLICABLE ({} is not a DevProcess adopter root; {})".format(
            product_root, res.detail))
        return EXIT_OK
    if res.status != _opf_store.RESOLVED:
        print("opf render: cannot evaluate: {}".format(res.detail), file=sys.stderr)
        return EXIT_CANNOT_EVALUATE

    store_root = res.store_root
    machine_rel = res.machine_rel
    pointer = res.pointer_source != "default"
    try:
        store_root_fd = _opf_store._open_store_root_fd(store_root, pointer)
    except OSError as exc:
        print("opf render: cannot open store root {} ({})".format(store_root, exc), file=sys.stderr)
        return EXIT_CANNOT_EVALUATE
    try:
        try:
            return _render_resolved(store_root_fd, product_root, store_root, machine_rel, check)
        except ViewsError as exc:
            print("opf render: cannot evaluate: {}".format(exc), file=sys.stderr)
            return EXIT_CANNOT_EVALUATE
    finally:
        os.close(store_root_fd)


def _render_resolved(store_root_fd, product_root, store_root, machine_rel, check):
    """The resolved-store render, split out so its ViewsError maps to exit 2 in render()'s handler."""
    manifest_rel = "{}/{}".format(machine_rel, _opf_store.MANIFEST_NAME)
    got = _read_raw_and_parsed(store_root_fd, manifest_rel)
    if got is None:
        raise ViewsError("{} vanished after discovery".format(manifest_rel))
    manifest = got[1]
    mv = _opf_store.validate_manifest(manifest)
    if mv.status != _opf_store.VALID:
        raise ViewsError("manifest is not valid: {}".format("; ".join(mv.findings) or mv.status))

    views = manifest.get("views", {})
    if not isinstance(views, dict):
        raise ViewsError("[views] is not a table")
    registered_vendors, registered_kinds = _registered_vendors_and_kinds(manifest)

    # Load every source any declared view needs, ONCE, caching its raw bytes (for per-view digests) and its
    # validated rows. A source that is missing, unreadable, or malformed raises ViewsError (exit 2).
    needed = set()
    for name, tbl in views.items():
        _kind, required, _renderer = _resolve_view(name)
        needed.update(required)
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

    # Phase 1: render every target's full text from the loaded sources, before any target is reconciled.
    planned = []   # (label, abspath, text)
    for name in sorted(views):
        tbl = views[name]
        kind, required, renderer = _resolve_view(name)
        declared_kind = tbl.get("kind")
        if declared_kind != kind:
            raise ViewsError("view {!r} is declared kind {!r} but this generator renders it as {!r} "
                             "(spec 10.1)".format(name, declared_kind, kind))
        declared_sources = tbl.get("sources", [])
        missing = [s for s in required if s not in (declared_sources or [])]
        if missing:
            raise ViewsError("view {!r} needs source(s) {} but its manifest [views] entry declares {} "
                             "(spec 10.2)".format(name, missing, declared_sources))
        target = tbl.get("target")
        src = {s: rows_by_source[s] for s in required}
        body = renderer(src)
        if name == "VERSION":
            text = body                                   # header-exempt exact-bytes deliverable (spec 6.1)
        else:
            source_blobs = {_source_relpath(machine_rel, s): raw_by_relpath[_source_relpath(machine_rel, s)]
                            for s in required}
            text = _header(source_blobs) + "\n" + body
        planned.append((target, _target_path(product_root, store_root, target), text))

    # Phase 2: reconcile each target in the stable (target-name) order, writing (or drift-reporting) each.
    drift = False
    for label, abspath, text in sorted(planned, key=lambda p: p[0]):
        if reconcile(abspath, text, check):
            print("drift: {}".format(label))
            drift = True
    if check and drift:
        print("run '{}' to regenerate".format(REGEN_COMMAND))
        return EXIT_DRIFT
    return EXIT_OK


# --- self-test (the opf.py --self-test render leg) ---------------------------------------------------

def self_test():
    """Render-leg self-test over SYNTHETIC stores. Judged on returned exit codes and rendered bytes, never
    by grepping output. Exercises: a golden render per view; drift detection; each transform (predicate
    filter, sort with ID tie-break, group, project); the block-actionability join (an unqualified active
    block hides an item, an active/proposed block does not); the decision supersession-chain join; a
    missing and a malformed declared source failing closed (exit 2); and an empty store rendering valid
    empty views. The tempdir is removed in a finally. Exit 0 pass, 1 fail, 2 error."""
    import tempfile
    import shutil

    try:
        _journal.require_containment()
    except _journal.JournalError as exc:
        print("OPF-VIEWS SELF-TEST ERROR: {}; fail-closed".format(exc), file=sys.stderr)
        return EXIT_CANNOT_EVALUATE

    failures = []
    checked = [0]

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

    # Every view and one mirror, declared with the kind/sources this generator renders.
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
    ]) + "\n"

    def empty_indexes(root):
        for t in BASELINE_TYPES:
            if t != "worklog":
                write_toml(root, "{}.index.toml".format(t), "schema = 1\n")
        write_toml(root, "worklog.toml", "schema = 1\n")
        write_toml(root, "version.toml", "schema = 1\n")

    try:
        # --- a POPULATED store, exercising every transform and both joins -------------------------------
        root = new_root()
        write_toml(root, "manifest.toml", manifest)
        # backlog_item: BI-10 open (actionable), BI-2 active (hidden by an UNQUALIFIED active block BL-1),
        # BI-3 open (scoped only by an active/PROPOSED block BL-2, so still actionable), BI-4 done.
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
        # pending_decision: PD-2 supersedes PD-1, so PD-2 is the effective head and PD-1 is superseded.
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
        # block-actionability join: BI-2 hidden by the unqualified active BL-1; BI-3 present (BL-2 proposed).
        check("join-block-hides-active-block", "BI-2" not in todo)
        check("join-block-keeps-proposed-block", "BI-3" in todo)
        check("todo-keeps-open", "BI-10" in todo)
        check("todo-drops-done", "BI-4" not in todo)
        # sort with ID tie-break: BI-3 (number 3) precedes BI-10 (number 10) despite lexical order.
        check("sort-id-tiebreak-numeric", todo.index("BI-3") < todo.index("BI-10"))
        # header contract: present, names sources, carries the digest + regen command, NO timestamp.
        check("header-present", todo.startswith("<!-- GENERATED by opf render"))
        check("header-has-sources", "sources: .working/toml/backlog_item.index.toml" in todo)
        check("header-has-digest", "source-set-digest: sha256:" in todo)
        check("header-has-regen", "regenerate: opf render" in todo)
        check("header-no-timestamp", not re.search(r"\d{4}-\d\d-\d\dT\d\d:\d\d", todo.split("-->", 1)[0]))

        backlog = read_view("BACKLOG.md")
        check("backlog-annotates-blocked", "blocked by BL-1" in backlog)
        check("backlog-annotates-actionable", "actionable" in backlog)

        pipeline = read_view("PIPELINE.md")
        # group by state: the lifecycle sections appear in order.
        check("pipeline-groups-in-order",
              0 < pipeline.index("## open") < pipeline.index("## active") < pipeline.index("## done"))
        check("pipeline-blocked-marker", "[blocked by BL-1]" in pipeline)

        decisions = read_view("DECISIONS.md")
        # supersession chain: PD-2 is the effective head naming PD-1; PD-1 is under Superseded; PD-3 open.
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
        check("handoff-current-first",
              handoff.index("HO-2") < handoff.index("## Superseded"))

        version_file = (root / "VERSION").read_text(encoding="utf-8")
        check("version-file-exact-bytes", version_file == "1.3.0\n")
        check("version-file-no-header", not version_file.startswith("<!--"))

        mirror = read_view("BACKLOG_ITEM-INDEX.md")
        check("mirror-title", "# BACKLOG_ITEM index" in mirror and mirror.startswith("<!-- GENERATED"))
        check("mirror-projects-record", "## BI-2" in mirror and "- status: active" in mirror)

        # drift: hand-edit a rendered view; --check must report exit 1 with a drift line.
        (root / WORKING_DIRNAME / "TODO.md").write_text("hand edited\n", encoding="utf-8")
        check("drift-detected", render(["--root", str(root), "--check"]) == EXIT_DRIFT)

        # --- an EMPTY store renders VALID EMPTY views (distinct from cannot-evaluate) -------------------
        eroot = new_root()
        write_toml(eroot, "manifest.toml", manifest)
        empty_indexes(eroot)
        check("empty-write-ok", render(["--root", str(eroot)]) == EXIT_OK)
        etodo = (eroot / WORKING_DIRNAME / "TODO.md").read_text(encoding="utf-8")
        check("empty-todo-valid-empty", _EMPTY in etodo and etodo.startswith("<!-- GENERATED"))
        check("empty-version-empty-bytes",
              (eroot / "VERSION").read_text(encoding="utf-8") == "")

        # --- a MISSING declared source fails closed (exit 2) --------------------------------------------
        mroot = new_root()
        write_toml(mroot, "manifest.toml", manifest)
        empty_indexes(mroot)
        (mroot / WORKING_DIRNAME / "toml" / "block.index.toml").unlink()   # TODO/BACKLOG/BLOCKS need it
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

        # --- an unresolvable / not-adopted root ---------------------------------------------------------
        na = base / "not-adopted"
        na.mkdir()
        check("not-adopted-not-applicable", render(["--root", str(na)]) == EXIT_OK)

    finally:
        shutil.rmtree(base, ignore_errors=True)

    if failures:
        print("OPF-VIEWS SELF-TEST FAIL ({} checks, {} failed):".format(checked[0], len(failures)),
              file=sys.stderr)
        for f in failures:
            print("  - " + f, file=sys.stderr)
        return EXIT_DRIFT
    print("opf-views self-test: PASS ({} checks) -- golden renders, drift detection, the filter/sort/"
          "group/project transforms, the block-actionability and decision-supersession joins, "
          "missing-and-malformed-source fail-closed, and empty-store valid-empty views".format(checked[0]))
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
    sys.exit(render(sys.argv[1:]))
