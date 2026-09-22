"""Deterministic view generators and the closed transform vocabulary for the OPFiles (OPF) store
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
PRODUCT repository root (default: the cwd), never the pack's own tree via `_gen_common.repo_root()`. The
`opf render` CLI requires exactly one of `--check | --write` (the dispatcher rejects a bare `render` with
exit 2); `--check` (read-only drift detection) forwards to this engine, while `--write` is fail-closed and
deferred (see below). A live `opf render --root . --check` in THIS repo reports NOT APPLICABLE (the pack is
a readable non-adopter root: it exists and is readable but has no `.working/`), so the assurance rides the
`--self-test` render leg over synthetic stores, mirroring the crosswalk/doctor legs. Fail-closed: an
unresolvable store, a product root that disappears or becomes unreadable at the resolution boundary, or a
declared source that is missing or unreadable, STOPS (exit 2), never a silent empty or partial view nor a
false NOT-APPLICABLE pass; an empty store (its index files present but carrying no records) renders VALID
EMPTY views, which is distinct from cannot-evaluate.

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
obligation (F2); `validate_store` is the EXISTING U6 engine INTENDED FOR the deferred `opf doctor` verb
(doctor itself is not yet wired, so validate_store is the engine doctor WILL compose, not one it already
uses), and it is the gate `render` will compose for `--write`, but `render` does not yet COMPOSE that
write-gate invocation (VC-4, deferred), so WRITE mode FAILS CLOSED today and writes nothing. The public
`opf render --write` CLI verb returns exit 2, failing closed at the dispatcher BEFORE the root is resolved.
A direct-engine write (a `render(...)` call in write mode) is refused exit 2 ONLY once the root RESOLVES as
an adopter; a non-adopter root returns NOT APPLICABLE (0) FIRST, because non-adopter resolution PRECEDES the
write refusal. The module `__main__` defaults its command line to `--check`, so it never enters write mode.
The `opf render` CLI requires exactly one of `--check | --write` (no default): `--check` (read-only drift
detection) is IMPLEMENTED and keeps working, while `--write` stays fail-closed until VC-4 composes the gate.
This is a refusal pending the real composition, never a fabricated gate.
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
import _opf_emit          # noqa: E402  canonical TOML emitter (emit_checked) for the machine projection

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    sys.exit("error: opf/tools/_opf_views.py requires Python 3.11+ (tomllib).")

EXIT_OK = 0
EXIT_DRIFT = 1
EXIT_CANNOT_EVALUATE = 2

# The generated-header identity (spec 10.3). The generator version is this view generator's own contract
# version, independent of the store schema version; a change to the rendered shape bumps it so a stale
# hand-copy reads as drift.
GENERATOR_NAME = "opf-views"
GENERATOR_VERSION = "2"
SCHEMA_VERSION = _opf_schema.SUPPORTED_SCHEMA
REGEN_COMMAND = "opf render --write"

# spec 5.7/5.8/11: a mutating render composes the U6 store-integrity layer (validate_store: cross-record
# uniqueness, coverage, reconciliation, view drift, changelog/version integrity) on the WRITE path
# (VC-4/PR-C). render --write is now SOURCE-GATED-REGENERATE, three phases: (A) validate_store and refuse
# (exit 2, nothing written) unless _opf_check.source_integrity_ok holds, printing ONLY the source-attributed
# messages; (B) regenerate the OWNED deliverable set (the declared views + the VERSION deliverable) through
# the two-phase plan-everything-then-write path, capturing preimages; (C) re-validate and require every OWNED
# drift check to now pass, rolling back on a render/check disagreement. A stale OWNED deliverable is render's
# OUTPUT to fix, not a refusal; an AUTHORED residual (C-CHANGELOG-GATES, or an UNOWNED C-VERSION-FILE state)
# never blocks the regenerate but is surfaced with a manual remedy and a nonzero exit (1 finding / 2 cannot-
# evaluate), NEVER a `render --write` hint. This flag RECORDS that the gate is composed; the defence-in-depth
# guard at the single write choke point (_write_contained) reads it as an overlapping, before-the-write
# refusal so a mutating write can never bypass the composed gate through a different helper (defence-in-depth-
# default). The opf render CLI requires exactly one of --check / --write (no default), and --check stays
# strictly read-only (SECI-preview-has-no-side-effects).
_WRITE_GATE_COMPOSED = True

# The mode installed on a NEWLY-created view/deliverable file. An EXISTING target's own mode is preserved
# across the atomic replace instead (a restrictive mode is never widened); this default applies only when
# the target did not previously exist.
_VIEW_FILE_MODE = 0o644

WORKING_DIRNAME = _opf_store.WORKING_DIRNAME     # ".working": public targets sit OUTSIDE it, at product root

# The baseline record types (spec 8.1), with worklog and version resolved to their ledger files rather
# than a `<type>.index.toml` (spec 4.2). A view source name maps to exactly one store file through this.
BASELINE_TYPES = tuple(_opf_schema.BASELINE_SPECS)   # the nine baseline type names
_LEDGER_SOURCES = frozenset(("worklog", "version"))  # read their own files, no <type>.index.toml to mirror


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
    failure, never an empty pass (the check-fails-closed-on-unreadable rule).
    Disclosed residual (disclose-guard-residuals): _journal._read_contained opens the file no-follow with
    O_NONBLOCK and re-confirms S_ISREG on the OPENED fd, so a live writer that swaps the regular file for a
    FIFO between the lstat here and that open does NOT block: the non-blocking open returns at once and the
    fstat gate refuses the non-regular object (fail-closed). The narrower residual is a swap to a DIFFERENT
    regular file in that window, whose bytes would then be read and validated as TOML and schema-checked;
    this is a concurrent-writer race beyond static on-disk store content, named here rather than left implied."""
    try:
        st = _journal._lstat_contained(store_root_fd, relpath)
    except (_journal.JournalError, OSError) as exc:
        # OSError (not only JournalError): _open_parent's terminal os.dup(root_fd) is UNWRAPPED, so a bad
        # store_root_fd (EBADF) or fd exhaustion (EMFILE) raises a raw OSError that _lstat_contained does not
        # convert. Map it here (with the JournalError symlink/read-error case) to a ViewsError cannot-evaluate,
        # so an unreadable declared source never escapes as an uncaught OSError (check-fails-closed-on-unreadable).
        raise ViewsError("cannot stat {} ({})".format(relpath, exc))
    if st is None:
        return None
    if not stat.S_ISREG(st.st_mode):
        raise ViewsError("{} is present but is not a regular file (a FIFO, device, socket, or directory; "
                         "fail-closed, never opened)".format(relpath))
    try:
        raw, _ = _journal._read_contained(store_root_fd, relpath)
    except (_journal.JournalError, OSError) as exc:        # OSError caught for parity with the lstat path above
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
    schema = data.get("schema")
    if "schema" not in data or type(schema) is not int or schema != SCHEMA_VERSION:
        raise ViewsError("{} schema {!r} is not an integer equal to the supported schema {} (a present "
                         "index must carry an exact integer schema marker; fail-closed)".format(
                             relpath, schema, SCHEMA_VERSION))
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
    """Load and validate worklog.toml (spec 6.2); return (raw_bytes, [entry, ...]) in file order.
    Disclosed divergence (disclose-guard-residuals): unlike the index schema marker, which
    _load_records pins MANDATORY and exact, the ledger schema marker follows U3 optional-marker
    contract: _opf_release.validate_worklog type-pins a PRESENT marker (a non-integer or unsupported
    version is refused) but PERMITS an absent one. A schema-less ledger authored for another schema
    version is not caught here; grading an unsupported-schema-version ledger is U3/U6 remit (F2)."""
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
    """Load and validate version.toml (spec 6.1); return (raw_bytes, releases, summaries) in file order.
    Disclosed divergence (disclose-guard-residuals): the version ledger schema marker follows U3
    optional-marker contract, as _load_worklog documents: validate_version type-pins a PRESENT marker
    but permits an absent one, diverging from the index MANDATORY pin. An unsupported-schema-version
    ledger that omits the marker is routed to U3/U6, not caught here."""
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
        "<!-- GENERATED by {cmd} (opf/tools/_opf_views.py). DO NOT EDIT; edit the store and regenerate.\n"
        "sources: {sources}\n"
        "schema: {schema}; generator: {gen}/{genver}\n"
        "source-set-digest: {digest}\n"
        "regenerate: {cmd}\n"
        "-->\n"
    ).format(cmd=safe(REGEN_COMMAND), sources=sources, schema=safe(str(SCHEMA_VERSION)),
             gen=safe(GENERATOR_NAME), genver=safe(GENERATOR_VERSION),
             digest=safe(_source_set_digest(source_blobs)))


def _toml_header(source_blobs):
    """The do-not-edit header for a machine PROJECTION deliverable (spec 10.3/10.5): the SAME identity block
    as `_header` (source paths, schema and generator versions, source-set digest, regenerate command, and NO
    timestamp, so a re-render of unchanged sources is byte-identical), emitted as leading TOML `#` comment
    lines rather than the markdown HTML comment, because a `.toml` deliverable cannot carry an HTML comment.
    A `#` comment runs to end of line, so an interpolated value can only break the header by injecting a
    newline; every value passes through _toml_comment_safe (a newline/control scrubber that, unlike the
    HTML-comment scrubber, does NOT break `--` runs -- a `--` is inert in a `#` comment, and breaking it
    would render the `opf render --write` regenerate command non-runnable), so no value can forge a header
    field line and the regenerate command stays intact."""
    safe = _toml_comment_safe
    sources = ", ".join(safe(p) for p in sorted(source_blobs)) if source_blobs else "(none)"
    return (
        "# GENERATED by {cmd} (opf/tools/_opf_views.py). DO NOT EDIT; edit the store and regenerate.\n"
        "# sources: {sources}\n"
        "# schema: {schema}; generator: {gen}/{genver}\n"
        "# source-set-digest: {digest}\n"
        "# regenerate: {cmd}\n"
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
TRANSFORM_VOCAB_VERSION = "3"

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
    # 1.1.0 base-schema fields: contribution (recipient/dedup_class/content_digest/delivery) and
    # preference_pattern (context/rationale). maintainer_decision reuses `decision`.
    "recipient", "dedup_class", "content_digest", "delivery", "context", "rationale",
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
            bid = b.get("id")
            if not isinstance(bid, str):
                continue
            for sid in b.get("scopes", []) or []:
                if isinstance(sid, str):
                    blocked_by.setdefault(sid, []).append(bid)
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
#   grep -nE 'r\[|r\.get\(|e\.get\(|s\.get\(|ref\.get\(|actor\[' opf/tools/_opf_views.py
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


def _toml_comment_safe(value):
    """Encode a value for a TOML `#` comment sink (the machine-projection header, _toml_header). A `#`
    comment runs to end of line and is closed ONLY by a newline, so the sole framing hazard is an injected
    newline/CR (collapsed to a space) or a C0/DEL control (dropped, TAB preserved) forging a header field
    line. Unlike the HTML-comment sink, a `--` run is INERT inside a `#` comment, so the dash-run break is
    NOT applied here: it would corrupt a legitimate value such as the `opf render --write` regenerate command
    into a non-runnable `opf render - -write`. Same newline/control neutralization as _html_comment_safe,
    without the dash break."""
    s = str(value)
    s = s.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")
    return _MD_CTRL_RE.sub("", s)


def render_todo(src):
    """TODO: the actionable open and active backlog items (block-actionability join, then a state filter,
    then an ID-tie-broken sort). An item an unqualified active block scopes is hidden."""
    items, blocks = src["backlog_item"], src["block"]
    _blocked_by, hidden = join_actionability(items, blocks)
    actionable = t_filter(items, "is_actionable", hidden=hidden)
    ordered = t_sort(actionable, keys=("status",))
    lines = ["- {} ({}) {}".format(_md_text(r.get("id")), _md_text(_state(r)), _md_text(r.get("title", "")))
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
            "blocked by {}".format(", ".join(_md_text(b) for b in blocked_by[r.get("id")])) if r.get("id") in hidden
            else "not actionable ({})".format(_md_text(_state(r))))
        lines.append("- {} ({}) {} -- {}".format(
            _md_text(r.get("id")), _md_text(r.get("status", "")), _md_text(r.get("title", "")), note))
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
        return " [blocked by {}]".format(", ".join(_md_text(b) for b in blocked_by[r.get("id")])) \
            if r.get("id") in hidden else ""

    out = []
    for state, group in t_group(t_filter(items, "not_proposed"), "state",
                                order=("open", "active", "done", "dropped")):
        out.append("## {}".format(_md_text(state)))
        for r in group:
            out.append("- {} {}{}".format(_md_text(r.get("id")), _md_text(r.get("title", "")), _marker(r)))
        out.append("")
    proposed = t_sort(t_filter(items, "is_proposed"))
    if proposed:
        out.append("## awaiting ratification")
        for r in proposed:
            out.append("- {} ({}) {}{}".format(
                _md_text(r.get("id")), _md_text(r.get("status", "")), _md_text(r.get("title", "")), _marker(r)))
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
        lines.append("- {} {}{}".format(_md_text(r.get("id")), _md_text(r.get("title", "")), suffix))
    return _lines("DONE", lines)


def render_findings(src):
    """FINDINGS: findings grouped by status, then ID-sorted, each with its graded severity when present."""
    out = []
    for status, group in t_group(src["finding"], "status"):
        out.append("## {}".format(_md_text(status)))
        for r in group:
            sev = " (severity: {})".format(_md_text(r["severity"])) if "severity" in r else ""
            out.append("- {}{} {}".format(_md_text(r.get("id")), sev, _md_text(r.get("title", ""))))
        out.append("")
    body = "\n".join(out).rstrip("\n") if out else _EMPTY
    return "# {}\n\n{}\n".format("FINDINGS", body)


def render_decisions(src):
    """DECISIONS: the pending decisions (open), the effective resolutions (the head of each supersedes
    chain), the superseded resolutions, the withdrawn decisions, the proposals awaiting ratification, the
    autonomous decisions, the maintainer decisions, and the preference patterns, over the four
    decision-register sources (spec 8.5/10.2). A `/proposed` decision is NONTERMINAL (spec 8.4): it is
    EXCLUDED from the ratified supersession graph and from the effective/superseded sets, and surfaced under
    `Awaiting ratification` with its FULL status; an unqualified `withdrawn` decision is a valid terminal,
    listed under `Withdrawn decisions`, never dropped. Each maintainer_decision is a created-terminal ruling
    that may `exemplifies` the preference_pattern it instantiates (a projection of the record's OWN links,
    not a join). A preference_pattern lands under Active / Retired, and a `/proposed` distilled pattern
    awaiting ratification is surfaced with its FULL status, never mis-listed as active. Every valid record of
    every source thus lands in exactly one place."""
    pend, auto = src["pending_decision"], src["autonomous_decision"]
    md = src.get("maintainer_decision", [])
    pp = src.get("preference_pattern", [])
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
    out += (["- {} {}".format(_md_text(r.get("id")), _md_text(r.get("title", ""))) for r in open_pd] or [_EMPTY])
    out += ["", "## Effective resolutions"]
    if effective:
        for r in effective:
            sup = supersedes_map.get(r.get("id")) or []
            tail = " (supersedes {})".format(", ".join(_md_text(s) for s in sup)) if sup else ""
            out.append("- {} {}{}".format(_md_text(r.get("id")), _md_text(r.get("title", "")), tail))
    else:
        out.append(_EMPTY)
    out += ["", "## Superseded resolutions"]
    out += (["- {} {}".format(_md_text(r.get("id")), _md_text(r.get("title", ""))) for r in gone] or [_EMPTY])
    if withdrawn:
        out += ["", "## Withdrawn decisions"]
        out += ["- {} {}".format(_md_text(r.get("id")), _md_text(r.get("title", ""))) for r in withdrawn]
    if proposed:
        out += ["", "## Awaiting ratification"]
        out += ["- {} ({}) {}".format(
            _md_text(r.get("id")), _md_text(r.get("status", "")), _md_text(r.get("title", ""))) for r in proposed]
    out += ["", "## Autonomous decisions"]
    out += (["- {} {}".format(_md_text(r.get("id")), _md_text(r.get("title", ""))) for r in t_sort(auto)] or [_EMPTY])
    # Maintainer decisions: each created-terminal ruling in ID order, with an `exemplifies PP-n` tail read
    # from the record's OWN links (a projection, deduped and numerically ordered like every id-set join).
    out += ["", "## Maintainer decisions"]
    if md:
        for r in t_sort(md):
            ex = _sorted_ids(_links_of(r, "exemplifies"))
            tail = " (exemplifies {})".format(", ".join(_md_text(x) for x in ex)) if ex else ""
            out.append("- {} {}{}".format(_md_text(r.get("id")), _md_text(r.get("title", "")), tail))
    else:
        out.append(_EMPTY)
    # Preference patterns: the settled Active then Retired records, then any `/proposed` distilled pattern
    # awaiting ratification (a NONTERMINAL gated `active`, spec 8.4) shown with its FULL status. A pattern
    # thus lands in exactly one place, never mis-listed as active before a maintainer ratifies it.
    settled_pp = t_filter(pp, "not_proposed")
    active_pp = t_sort(t_filter(settled_pp, "state_in", states=("active",)))
    retired_pp = t_sort(t_filter(settled_pp, "state_in", states=("retired",)))
    proposed_pp = t_sort(t_filter(pp, "is_proposed"))
    out += ["", "## Preference patterns"]
    out += (["- {} (active) {}".format(_md_text(r.get("id")), _md_text(r.get("title", "")))
             for r in active_pp]
            + ["- {} (retired) {}".format(_md_text(r.get("id")), _md_text(r.get("title", "")))
               for r in retired_pp]
            + ["- {} ({}) {}".format(_md_text(r.get("id")), _md_text(r.get("status", "")),
                                     _md_text(r.get("title", ""))) for r in proposed_pp]
            or [_EMPTY])
    return "# {}\n\n{}\n".format("DECISIONS", "\n".join(out))


def render_contributions(src):
    """CONTRIBUTIONS: the outbound contributions (spec 8.5), grouped by lifecycle state
    Proposed / Sent / Acknowledged / Superseded / Withdrawn, each ID-sorted, projecting the delivery
    pointer (channel/ref/sent_at). A `sent/proposed` contribution is NONTERMINAL (spec 8.4): an
    assistant/automation lands it awaiting a maintainer's ratification, so it is held OUT of Sent and
    surfaced under `Awaiting ratification` with its FULL status. Every valid contribution status thus lands
    in exactly one place."""
    recs = src["contribution"]
    settled = t_filter(recs, "not_proposed")
    proposed_await = t_sort(t_filter(recs, "is_proposed"))

    def deliv(r):
        d = r.get("delivery") if isinstance(r.get("delivery"), dict) else {}
        parts = [d[k] for k in ("channel", "ref", "sent_at") if k in d]
        return " [{}]".format(", ".join(_md_text(x) for x in parts)) if parts else ""

    out = []
    for label, state in (("Proposed", "proposed"), ("Sent", "sent"),
                         ("Acknowledged", "acknowledged"), ("Superseded", "superseded"),
                         ("Withdrawn", "withdrawn")):
        group = t_sort(t_filter(settled, "state_in", states=(state,)))
        out += ["## {}".format(label)]
        out += (["- {} {}{}".format(_md_text(r.get("id")), _md_text(r.get("title", "")), deliv(r))
                 for r in group] or [_EMPTY])
        out += [""]
        if label == "Sent":
            out += ["## Awaiting ratification"]
            out += (["- {} ({}) {}{}".format(_md_text(r.get("id")), _md_text(r.get("status", "")),
                     _md_text(r.get("title", "")), deliv(r)) for r in proposed_await] or [_EMPTY])
            out += [""]
    return "# {}\n\n{}\n".format("CONTRIBUTIONS", "\n".join(out).rstrip("\n"))


# --- the DECISIONS.toml machine projection (spec 10.5) ------------------------------------------------
#
# The one PROJECTION-kind deliverable: a deterministic, byte-drift-gated TOML view of the four
# decision-register types, emitted through the canonical emitter (_opf_emit.emit_checked, which reparses
# and byte-canon-checks its own output) with a leading `#` do-not-edit header prepended by the driver
# (_toml_header). x-<vendor> tables are EXCLUDED (profile-owned data). The `[derived]` table carries the
# existing decision-resolution join output (spec 10.2), so the projection introduces NO new vocabulary.

PROJECTION_SCHEMA = 1   # the projection contract's OWN version, bumped when the projected shape changes

# The projected extra fields per source type (declared, closed): each type's schema `extra_keys`, so a
# field outside this map never reaches the projection. Envelope fields and links/refs are projected
# uniformly by _projection_row.
_PROJECTION_EXTRA = {
    "pending_decision": ("decision", "decided_at", "decided_by"),
    "autonomous_decision": ("classification", "action"),
    "maintainer_decision": ("decision",),
    "preference_pattern": ("context", "rationale"),
}


def _projection_row(record, extra):
    """Project one record to a projection row (spec 10.5: "the full base record"): the base envelope fields
    (including `type`, a self-describing row), a nested `actor` sub-table carrying the string-valued
    kind/id, the type's declared extra fields (`extra`), and `links`/`refs` as NESTED arrays of tables (the
    emitter excludes inline tables, so a link/ref set is projected as an array of tables, not the record
    files' inline-table form). The canonical emitter binds a per-row `actor` sub-table to its own element of
    an array of tables (verified by the views self-test), so the faithful nested envelope shape is projected
    rather than a flattened actor_kind/actor_id pair. A field the record does not carry is omitted (a stable
    declared shape)."""
    row = {}
    for k in ("id", "type", "status", "title", "created_at", "updated_at"):
        if k in record:
            row[k] = record[k]
    actor = record.get("actor")
    if isinstance(actor, dict):
        sub = {}
        if isinstance(actor.get("kind"), str):
            sub["kind"] = actor["kind"]
        if isinstance(actor.get("id"), str):
            sub["id"] = actor["id"]
        if sub:
            row["actor"] = sub
    if "summary" in record:
        row["summary"] = record["summary"]
    for k in extra:
        if k in record:
            row[k] = record[k]
    for field in ("links", "refs"):
        vals = [x for x in (record.get(field) or []) if isinstance(x, dict)]
        if vals:
            row[field] = vals
    return row


def render_decisions_toml(src):
    """DECISIONS.toml: the deterministic machine projection of the four decision-register types (spec 10.5).
    Returns the TOML BODY only; the driver prepends the `#` do-not-edit header (_toml_header). Each source
    is an array of tables of its projected rows, ID-sorted; the `[derived]` table carries the
    decision-resolution join's effective/superseded output over the SETTLED pending decisions (no vocabulary
    beyond spec 10.2). x-<vendor> tables are excluded (profile-owned). The body is emitted through
    _opf_emit.emit_checked, which reparses and byte-canon-proves its own output (fail-closed)."""
    sources = ("pending_decision", "autonomous_decision", "maintainer_decision", "preference_pattern")
    doc = {"projection": "decisions", "schema": PROJECTION_SCHEMA}
    for t in sources:
        doc[t] = [_projection_row(r, _PROJECTION_EXTRA[t]) for r in sorted(src.get(t, []), key=_id_key)]
    settled = t_filter(src.get("pending_decision", []), "not_proposed")
    current, superseded, _supersedes_map = join_resolution(settled)
    doc["derived"] = {"effective": _sorted_ids(current), "superseded": _sorted_ids(superseded)}
    return _opf_emit.emit_checked(doc)


def render_blocks(src):
    """BLOCKS: every block in ID order with its status, the records it scopes, and a proposed marker."""
    lines = []
    for r in t_sort(src["block"]):
        scopes = ", ".join(_md_text(s) for s in (r.get("scopes", []) or []))
        lines.append("- {} ({}) scopes [{}] -- {}".format(
            _md_text(r.get("id")), _md_text(r.get("status", "")), scopes, _md_text(r.get("title", ""))))
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
    out += (["- {} {}".format(_md_text(r.get("id")), _md_text(r.get("title", ""))) for r in cur] or [_EMPTY])
    out += ["", "## Superseded"]
    old = t_sort(t_filter(t_filter(handoffs, "state_in", states=("superseded",)), "not_proposed"))
    out += (["- {} {}".format(_md_text(r.get("id")), _md_text(r.get("title", ""))) for r in old] or [_EMPTY])
    proposed = t_sort(t_filter(handoffs, "is_proposed"))
    if proposed:
        out += ["", "## Awaiting ratification"]
        out += ["- {} ({}) {}".format(
            _md_text(r.get("id")), _md_text(r.get("status", "")), _md_text(r.get("title", ""))) for r in proposed]
    return "# {}\n\n{}\n".format("HANDOFF", "\n".join(out))


def render_references(src):
    """REFERENCES: the captured references in ID order, each with its captured refs."""
    lines = []
    for r in t_sort(src["reference"]):
        lines.append("- {} {}".format(_md_text(r.get("id")), _md_text(r.get("title", ""))))
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


# The DECLARED projection columns render_mirror emits for a 1:1 <TYPE>-INDEX.md mirror (spec 10.1), in
# render order. Hoisted to a module constant so the importer that recognizes a generated mirror face keys
# on the SAME authoritative column vocabulary (guard-input-soundness: the guard's input is derived from its
# source, never re-declared). Every entry is a registered PROJECT_COLUMNS name (t_project enforces it).
MIRROR_COLUMNS = ("type", "status", "title", "created_at", "updated_at", "date", "kind", "severity",
                  "decision", "decided_at", "decided_by", "classification", "action", "scopes",
                  "recipient", "dedup_class", "content_digest", "delivery", "context", "rationale")


def render_mirror(type_name, records):
    """A 1:1 <TYPE>-INDEX.md mirror (spec 10.1): each record in ID order rendered as a projection of its
    declared columns, mirroring the machine index for human reading. Every projected value is escaped for
    the markdown/HTML sink so a free-text column (decision, classification, action, title, ...) cannot
    forge structure or the do-not-edit header comment."""
    out = []
    for r in t_sort(records):
        out.append("## {}".format(_md_text(r.get("id"))))
        for col, val in t_project(r, MIRROR_COLUMNS):
            if isinstance(val, list):
                val = "[{}]".format(", ".join(_md_text(x) for x in val))
            elif isinstance(val, dict):
                # A table-valued column (contribution.delivery): its scalar key=value pairs in sorted key
                # order, so the human mirror shows the delivery pointer inline and deterministically.
                val = ", ".join("{}={}".format(_md_text(k), _md_text(val[k])) for k in sorted(val))
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
    "DECISIONS.md": ("composed", ("pending_decision", "autonomous_decision",
                                  "maintainer_decision", "preference_pattern"), render_decisions),
    "CONTRIBUTIONS.md": ("composed", ("contribution",), render_contributions),
    "DECISIONS.toml": ("projection", ("pending_decision", "autonomous_decision",
                                      "maintainer_decision", "preference_pattern"), render_decisions_toml),
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
    if type_name in _LEDGER_SOURCES:
        return None
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


def _write_contained(root_fd, relpath, text, check, preimages=None, scope=None):
    """Write `text` (UTF-8) to a contained regular file beneath root_fd, no-follow, or (check mode) return
    True when the on-disk bytes differ (the drift signal). This is U4's OWN view-write primitive, the
    no-follow counterpart of the `_journal` contained READS the module already uses: a symlinked
    destination, a symlinked path component, or a non-regular destination is REFUSED (ViewsError, a
    cannot-evaluate), never followed, so a manifest or a planted link cannot redirect a write off-tree.
    Fail-closed: any read/write error, or a parent directory that cannot be opened (including a symlinked or
    non-directory intermediate path component, which _open_parent signals as a JournalError rather than an
    OSError), is a ViewsError, never a silent skip. Byte-stable: an unchanged target is not rewritten.

    This is the single choke point EVERY view and deliverable write passes through, so the write-gate
    refusal is the DEFENCE-IN-DEPTH layer HERE at the write boundary, overlapping render()'s composed U6
    gate: the real store-integrity gate runs in render() (validate_store, VALID-only), and this guard is the
    low-cost before-any-truncation backstop that refuses a mutating write whenever _WRITE_GATE_COMPOSED is
    somehow False, so no helper (a direct _write_contained or _render_resolved call included) can open or
    truncate a file with the gate flag cleared (defence-in-depth-default). --check is read-only and
    unaffected. With the gate composed the flag is True, so this backstop passes and render()'s validate_store
    verdict is what actually gates the write (spec 5.7/5.8/11)."""
    if not check and not _WRITE_GATE_COMPOSED:
        raise ViewsError("refusing to write {}: store-integrity gate (U6 validate_store) not composed; a "
                         "mutating render is refused at the write boundary as a defence-in-depth backstop "
                         "(fail-closed, spec 5.7/5.8/11)".format(relpath))
    new_bytes = text.encode("utf-8")
    try:
        pfd, name = _journal._open_parent(root_fd, relpath)
    except (OSError, _journal.JournalError) as exc:
        # OSError includes FileNotFoundError (a missing parent dir); JournalError is how _open_parent signals a
        # symlinked or non-directory intermediate path component (it is NOT an OSError subclass, so the plain
        # `except OSError` here let it escape uncaught -- render()'s handler does not catch it either, so it
        # died as exit 1, colliding with EXIT_DRIFT). Both are fail-closed, mapped to a cannot-evaluate here.
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
        if preimages is not None:
            # PR-C Phase B: record the pre-write bytes of every target this render ACTUALLY rewrites (None
            # when the target did not exist -> a rollback deletes it), keyed by (scope, relpath) so a Phase-C
            # rollback knows which no-follow root to reopen. Recorded only for a REAL write, after the
            # byte-stable early return above, so an unchanged target is never marked for rollback.
            preimages[(scope, relpath)] = current
        # Reopen-TOCTOU hardening: never open the destination NAME for truncation. O_NOFOLLOW refuses a
        # symlink but NOT a hardlink or a regular-file swap raced in after the lstat/read above, so an
        # O_TRUNC of `name` could truncate a victim the attacker hardlinked in over the destination. Instead
        # write the new bytes to a fresh O_EXCL temp beneath the SAME parent fd and atomically rename it over
        # `name`: the rename re-points only the directory entry, so a raced hardlink/regular swap of `name`
        # loses the entry rather than having its inode truncated (the victim's own bytes stay intact).
        # Descriptor-relative throughout (dir_fd=pfd), never a re-resolved path.
        # A UNIQUE, exclusively-created temp NAME, never a fixed ".{name}.opf-tmp" a concurrent call could be
        # using and never an unconditional unlink of that fixed name (which could delete another live call's
        # temp). O_EXCL proves THIS call created the inode; a collision on the random name is a genuine
        # anomaly that fails closed (the OSError maps to a ViewsError below), never a clobber of an existing
        # file. Created 0o600 so the in-flight temp is not world-readable before the real mode is applied.
        tmpname = ".{}.opf-tmp.{}.{}".format(name, os.getpid(), os.urandom(8).hex())
        fd = os.open(tmpname, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600, dir_fd=pfd)
        # Wrap the WHOLE temp-file lifetime so ANY failure before the atomic rename succeeds (fchmod,
        # _write_all, fsync, or the rename itself) unlinks the temp descriptor-relative, never leaving an
        # orphan behind. The cleanup is best-effort and never masks the ORIGINAL error: the exception in
        # flight propagates through this finally unchanged (mapped to a ViewsError by the outer handler).
        _renamed = False
        try:
            try:
                # PRESERVE the destination's EXISTING mode across the atomic replace: installing the temp's
                # create mode would silently WIDEN a restrictive view (e.g. a 0600 target -> 0644). fchmod the
                # temp fd to the destination's current mode, or to the intended default for a new file, BEFORE
                # the rename. fchmod is not umask-masked, so the installed mode is deterministic regardless of
                # the process umask (test-hermeticity).
                os.fchmod(fd, stat.S_IMODE(st.st_mode) if st is not None else _VIEW_FILE_MODE)
                _journal._write_all(fd, new_bytes)
                os.fsync(fd)
            finally:
                os.close(fd)
            os.rename(tmpname, name, src_dir_fd=pfd, dst_dir_fd=pfd)   # atomic entry replace, no truncation
            _renamed = True
            os.fsync(pfd)                                 # the rename (a directory entry change) is durable
            return False
        finally:
            if not _renamed:
                try:
                    os.unlink(tmpname, dir_fd=pfd)         # never leave a temp behind on ANY pre-rename failure
                except OSError:
                    pass
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


def render(argv, observations=None):
    """`opf render [--root DIR] (--check | --write)`: render every declared view of the store at a PRODUCT
    root. Exactly one of --check / --write is required (a bare `render` is a usage error, exit 2). --check is
    strictly read-only and returns 0 clean, 1 on drift, 2 cannot-evaluate. --write is the MUTATING half and is
    SOURCE-GATED-REGENERATE (PR-C): after resolve_store succeeds it (A) composes the U6 gate and refuses
    (exit 2, nothing written) unless source integrity is sound (_opf_check.source_integrity_ok), which
    EXCLUDES the deliverable-drift checks render itself regenerates; (B) regenerates the owned set (the
    declared views + the VERSION deliverable); (C) re-validates and requires every OWNED drift check to now
    pass, rolling back on a render/check disagreement (exit 2). A drifted OWNED deliverable is thus render's
    OUTPUT, fixed when source integrity holds, not a refusal; a source problem is reported to fix first. An
    AUTHORED residual after a clean regenerate never blocks it but forces a nonzero exit: a C-CHANGELOG-GATES
    (or UNOWNED C-VERSION-FILE) FINDING exits 1 and a CANNOT-EVALUATE exits 2, each naming the MANUAL fix
    (edit CHANGELOG.md / declare-or-remove VERSION) and NEVER advertising a `render --write` re-run.
    `observations` is the inert git-derived facts object the git-aware caller (opf.py's render --write, via
    _opf_observe.gather) injects for the gate; validate_store reads no git itself, so a write can only ever
    pass when honest observations are supplied. NOT-ADOPTED reports NOT APPLICABLE and exits 0 (the pack's own
    `--root .` case). Two-phase like run_generator: every payload is rendered before any target is written, so
    a fail-closed source aborts before a single file is touched."""
    root = None
    check = None
    i = 0
    while i < len(argv):
        tok = argv[i]
        if tok in ("--check", "--write"):
            if check is not None:
                # Exactly one mode: both flags, or a repeated flag, is a usage error (fail-closed CLI, B3).
                print("opf render: give exactly one of --check / --write", file=sys.stderr)
                return EXIT_CANNOT_EVALUATE
            check = tok == "--check"
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
    if check is None:
        # Neither --check nor --write: a bare render never defaults into a write (a preview never mutates).
        # This matches opf.py's own _cmd_render parser (exactly one of --check / --write required).
        print("opf render: give exactly one of --check / --write", file=sys.stderr)
        return EXIT_CANNOT_EVALUATE
    if root is None:
        root = "."
    try:
        product_root = Path(os.path.abspath(root))
    except OSError as exc:
        # A relative --root (including the "." default) resolves through os.getcwd(); a deleted or
        # otherwise unresolvable current directory or root makes abspath raise. Map that root-resolution
        # filesystem error to a cannot-evaluate (exit 2) HERE, before render's own error handling, so an
        # uncaught FileNotFoundError never exits 1 and collides with the documented DRIFT code
        # (guard-input-soundness / fail-closed).
        print("opf render: cannot evaluate: cannot resolve product root {!r} ({})".format(root, exc),
              file=sys.stderr)
        return EXIT_CANNOT_EVALUATE

    res = _opf_store.resolve_store(product_root)
    if res.status == _opf_store.NOT_ADOPTED:
        print("opf render: NOT APPLICABLE ({} is not an OPFiles adopter root; {})".format(
            product_root, res.detail))
        return EXIT_OK
    if res.status != _opf_store.RESOLVED:
        print("opf render: cannot evaluate: {}".format(res.detail), file=sys.stderr)
        return EXIT_CANNOT_EVALUATE

    if check:
        return _render_resolved_store(product_root, res, check)

    # --- the mutating --write path: SOURCE-gate, regenerate the OWNED set, WITNESS the result (PR-C) -------
    # The import is CALL-TIME: _opf_check imports _opf_views at module top, so a top-level back-import would
    # be circular; a call-time import resolves against the fully loaded module.
    import _opf_check

    # Phase A -- SOURCE gate (nothing touched). validate_store grades the whole store; render gates ONLY on
    # SOURCE integrity (the 26 non-deliverable checks) via the engine predicate, so an out-of-date deliverable
    # (a drifted view or VERSION) is render's OUTPUT to fix, not a refusal. A source violation (a duplicate
    # id, untracked, no/malformed observations, an unreadable required input, any internal fault) refuses with
    # exit 2 and nothing written, printing ONLY the source-attributed messages so the false "regenerate"
    # remedy is never advertised over a store render will not touch (guard-input-soundness; never-advertise).
    pre = _opf_check.validate_store(res, observations=observations)
    if not _opf_check.source_integrity_ok(pre):
        print("opf render: cannot evaluate: refusing to write; store SOURCE integrity is not sound "
              "(U6 validate_store); nothing written", file=sys.stderr)
        for cid in _opf_check.REQUIRED_CHECKS:
            if cid in _opf_check.SOURCE_INTEGRITY_CHECKS:
                for m in pre.by_check.get(cid, []):
                    print("  {}: {}".format(pre.checks.get(cid, "?"), m), file=sys.stderr)
        for m in pre.unattributed:
            print("  UNATTRIBUTED: {}".format(m), file=sys.stderr)
        return EXIT_CANNOT_EVALUATE

    # Phase B -- regenerate the OWNED set (the declared views + the VERSION deliverable). plan_views renders
    # every payload before any target is written (stage-then-promote for this artefact class); a source or
    # layout fault (a per-record store, a declared VERSION with zero releases, a byte-canon-invalid render)
    # aborts writeless as exit 2 through _render_resolved_store's handler. `capture` collects the planned view
    # names and the in-memory preimage of every target actually rewritten, so Phase C can witness and roll
    # back. The OWNED set is EXACTLY the planned targets.
    capture = {"preimages": {}}
    rc = _render_resolved_store(product_root, res, False, capture=capture)
    if rc != EXIT_OK:
        return rc
    planned_names = set(capture.get("planned", ()))
    preimages = capture["preimages"]

    # Phase C -- witnessed re-validation. Re-grade with the SAME observations and require that source
    # integrity STILL holds AND every OWNED deliverable drift check now passes: C-VIEW-DRIFT ALWAYS (it grades
    # exactly the set plan_views just wrote), and C-VERSION-FILE only when "VERSION" was planned (render never
    # certifies, or fails on, a deliverable it did not and could not regenerate). A miss on an owned check is a
    # render/check DISAGREEMENT: roll back to the captured preimages, name the affected paths, exit 2.
    post = _opf_check.validate_store(res, observations=observations)
    owned_ok = (_opf_check.source_integrity_ok(post)
                and post.checks.get("C-VIEW-DRIFT") == "PASS"
                and ("VERSION" not in planned_names or post.checks.get("C-VERSION-FILE") == "PASS"))
    if not owned_ok:
        failed = _restore_preimages(product_root, res, preimages)
        # The rollback claim rests on OBSERVATION (claims-rest-on-observation, no-concealed-failure): assert a
        # completed rollback ONLY when _restore_preimages restored every captured target (failed empty). On a
        # partial failure state the rollback was INCOMPLETE and name the unrestored paths in the PRIMARY
        # statement, not merely a trailing warning that overstates the primary claim.
        if failed:
            rollback = ("the rollback was INCOMPLETE: these targets could NOT be restored and may be left "
                        "mid-write: {}".format(", ".join(sorted(failed))))
        else:
            rollback = "rolled back to the pre-write bytes"
        # Attribute the miss to the cause that ACTUALLY fired (claims-rest-on-observation): owned_ok is False on
        # EITHER a post-write SOURCE-integrity regression OR an owned-deliverable miss. A freshly-written view
        # tripping a SOURCE check is NOT an owned-deliverable disagreement, so label it a SOURCE-INTEGRITY
        # regression and evidence it from SOURCE_INTEGRITY_CHECKS (the same REQUIRED_CHECKS-ordered, source-
        # filtered print Phase A uses); otherwise keep the owned-deliverable wording and print C-VIEW-DRIFT /
        # C-VERSION-FILE. Either way roll back, print post.unattributed, and exit 2.
        if not _opf_check.source_integrity_ok(post):
            print("opf render: cannot evaluate: post-write validation found a SOURCE-INTEGRITY regression "
                  "after the regenerate; {}".format(rollback), file=sys.stderr)
            for cid in _opf_check.REQUIRED_CHECKS:
                if cid in _opf_check.SOURCE_INTEGRITY_CHECKS:
                    for m in post.by_check.get(cid, []):
                        print("  {}: {}".format(post.checks.get(cid, "?"), m), file=sys.stderr)
        else:
            print("opf render: cannot evaluate: post-write validation disagreed with the regenerate over an "
                  "OWNED deliverable; {}".format(rollback), file=sys.stderr)
            for m in post.by_check.get("C-VIEW-DRIFT", []) + post.by_check.get("C-VERSION-FILE", []):
                print("  {}".format(m), file=sys.stderr)
        for m in post.unattributed:
            print("  UNATTRIBUTED: {}".format(m), file=sys.stderr)
        return EXIT_CANNOT_EVALUATE

    # The OWNED set is now current. An AUTHORED C-CHANGELOG-GATES state, or an UNOWNED C-VERSION-FILE state (a
    # stale VERSION the manifest does not declare as a view), NEVER blocks or reverts the regenerate but is
    # surfaced with a MANUAL remedy and forces a nonzero exit -- never a `render --write` hint.
    return _residual_write_exit(post, planned_names)


def _render_resolved_store(product_root, res, check, capture=None):
    """render()'s post-resolution, post-gate body: open the store/product no-follow fds for a RESOLVED store
    and render (drift-check when `check`, write otherwise). `capture`, when a dict, receives the PR-C Phase-B
    witness data: capture["planned"] is set to the sorted planned view names, and capture["preimages"] (seeded
    by the caller) receives the pre-write bytes of every target actually rewritten, so render()'s Phase C can
    check ownership and roll back. Left None on the --check path and the ungated self-test write path.
    and render (drift-check when `check`, write otherwise) via _render_resolved, mapping U4's ViewsError (and
    defensively RecursionError/ValueError/OSError) to a controlled exit 2 and closing both fds in a finally.
    Extracted so the self-test can exercise the render/write LOGIC over synthetic stores WITHOUT re-composing
    the U6 gate (render() composes that gate on the write path before calling this; the gate itself is
    exercised by the dedicated valid/invalid render() vectors). A mutating write still passes through
    _write_contained's defence-in-depth guard (the module _WRITE_GATE_COMPOSED flag)."""
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
            return _render_resolved(store_root_fd, product_root_fd, machine_rel, check, capture)
        except (ViewsError, RecursionError, ValueError, OSError) as exc:
            # ViewsError is U4's cannot-evaluate; RecursionError, (defensively) ValueError, and OSError are
            # widened here as defence in depth, so a parse recursion or a raw OSError (e.g. an unwrapped
            # os.dup EBADF/EMFILE) escaping any inner path becomes a controlled exit 2 rather than an
            # uncontrolled traceback.
            print("opf render: cannot evaluate: {}".format(exc), file=sys.stderr)
            return EXIT_CANNOT_EVALUATE
    finally:
        os.close(store_root_fd)
        os.close(product_root_fd)


def plan_views(store_root_fd, machine_rel):
    """Phase 1 of the resolved-store render, extracted as a public READ-ONLY planner (OPF core-tooling U6
    reuses it for byte-level view-drift detection). Reads the manifest and every declared view source
    beneath store_root_fd, renders each declared target's full text, and returns the planned list of
    (view_name, scope, dest_relpath, text) WITHOUT writing anything. Raises ViewsError (a cannot-evaluate)
    on an unreadable manifest or source, a `per-record` store (deferred, F7), a view/kind/target mismatch,
    or a byte-canon-invalid render, exactly as the render path does; _render_resolved calls it and performs
    the writes. It makes no state-changing or outbound side effect (a planner is a preview)."""
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
    opf = manifest.get("opf")
    layout = opf.get("layout") if isinstance(opf, dict) else None
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

    # Render every target's full text from the loaded sources, before any target is written. Each target is
    # bound to its OWN spec destination; a manifest `target` that does not match is rejected, never silently
    # redirected.
    planned = []   # (view_name, scope, dest_relpath, text)
    import _byte_canon as check_byte_canon  # authoritative byte-canon leg; pure function over bytes, lazy like self_test
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
            if kind == "projection":
                # A machine projection (spec 10.5) carries the SAME identity header as a markdown view, but a
                # `.toml` deliverable takes it as leading `#` comment lines (_toml_header), never the HTML
                # comment. The renderer returns the TOML body; the driver prepends the header, so a projection
                # is byte-drift-gated exactly like every other declared view.
                text = _toml_header(source_blobs) + "\n" + body
            else:
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
    return planned


def _render_resolved(store_root_fd, product_root_fd, machine_rel, check, capture=None):
    """The resolved-store render, split out so its ViewsError maps to exit 2 in render()'s handler. Views
    write beneath store_root_fd (`.working/<name>`); the one public VERSION deliverable writes beneath
    product_root_fd. Every target is bound to its spec destination (never the manifest `target`) and
    written through U4's no-follow contained write. Phase 1 is the reusable `plan_views`; this performs the
    writes."""
    planned = plan_views(store_root_fd, machine_rel)
    if capture is not None:
        # PR-C: the OWNED set is exactly the planned targets. Record the planned view NAMES so render()'s
        # Phase C can decide C-VERSION-FILE ownership (owned iff "VERSION" is a declared, planned view).
        capture["planned"] = sorted(name for name, _s, _d, _t in planned)
    preimages = capture.get("preimages") if capture is not None else None

    # Phase 2: write (or drift-report under --check) each target in the stable (view-name) order, through
    # U4's no-follow contained write. `.working/<name>` writes beneath the store root; the public VERSION
    # writes beneath the product root.
    drift = False
    for _name, scope, dest_rel, text in sorted(planned, key=lambda p: p[0]):
        write_fd = product_root_fd if scope == "product" else store_root_fd
        if _write_contained(write_fd, dest_rel, text, check, preimages=preimages, scope=scope):
            print("drift: {}".format(dest_rel))
            drift = True
    if check and drift:
        print("run '{}' to regenerate".format(REGEN_COMMAND))
        return EXIT_DRIFT
    return EXIT_OK


def _residual_write_exit(post, planned_names):
    """Map any residual AUTHORED-deliverable state left after a clean OWNED regenerate to render's exit code
    (PR-C), surfacing it with a MANUAL remedy and NEVER a `render --write` hint (SETTLED policy; never-
    advertise, no-concealed-failure). C-CHANGELOG-GATES is always authored content render does not generate;
    C-VERSION-FILE is authored-owned only when "VERSION" is NOT a declared/planned view. A CANNOT-EVALUATE
    (EXIT_CANNOT_EVALUATE=2) dominates a FINDING (EXIT_DRIFT=1); a fully clean residual returns EXIT_OK=0
    (the exit ints are ordered, so max() gives the dominating code)."""
    worst = EXIT_OK
    # An UNOWNED C-VERSION-FILE state: a stale VERSION the manifest does not declare as a view is not render's
    # to regenerate or delete. Direct the maintainer to declare the view (so render owns and regenerates it) or
    # bring the root VERSION into line with the version ledger; removal is advised ONLY when the ledger has no
    # releases, because C-VERSION-FILE REQUIRES a root VERSION whenever the ledger has releases, so a blanket
    # "remove the file" would convert a stale-VERSION finding into a MISSING-VERSION one (harmful advice).
    # render deletes nothing.
    if "VERSION" not in planned_names:
        v = post.checks.get("C-VERSION-FILE")
        if v == "CANNOT-EVALUATE":
            worst = max(worst, EXIT_CANNOT_EVALUATE)
            for m in post.by_check.get("C-VERSION-FILE", []):
                print("opf render: cannot evaluate: {}".format(m), file=sys.stderr)
            print("opf render: the root VERSION deliverable is not a declared view; declare the VERSION view "
                  "so render owns and regenerates it, or bring the root VERSION into line with the version "
                  "ledger per the cannot-evaluate above (removing the root VERSION is valid ONLY when the "
                  "version ledger has no releases); then re-run 'opf doctor' (render does not own an "
                  "undeclared VERSION)", file=sys.stderr)
        elif v == "FINDING":
            worst = max(worst, EXIT_DRIFT)
            for m in post.by_check.get("C-VERSION-FILE", []):
                print("opf render: finding: {}".format(m))
            print("opf render: the root VERSION deliverable is not a declared view; declare the VERSION view "
                  "so render owns and regenerates it, or bring the root VERSION into line with the version "
                  "ledger per the finding above (removing the root VERSION is valid ONLY when the version "
                  "ledger has no releases); then re-run 'opf doctor' (render does not own an undeclared "
                  "VERSION)")
    # C-CHANGELOG-GATES is authored content render NEVER generates; surface it and name the manual fix. A
    # CANNOT-EVALUATE (e.g. a non-UTF-8 CHANGELOG) still exits 2 after the owned set was regenerated -- an
    # unreadable authored file does not hold the generated deliverables hostage (SETTLED).
    cg = post.checks.get("C-CHANGELOG-GATES")
    if cg == "CANNOT-EVALUATE":
        worst = max(worst, EXIT_CANNOT_EVALUATE)
        for m in post.by_check.get("C-CHANGELOG-GATES", []):
            print("opf render: cannot evaluate: {}".format(m), file=sys.stderr)
        print("opf render: CHANGELOG.md could not be evaluated; fix the named input above, then re-run "
              "'opf doctor' (render does not generate the curated changelog)", file=sys.stderr)
    elif cg == "FINDING":
        worst = max(worst, EXIT_DRIFT)
        for m in post.by_check.get("C-CHANGELOG-GATES", []):
            print("opf render: finding: {}".format(m))
        print("opf render: edit CHANGELOG.md to resolve the finding above, then re-run 'opf doctor' "
              "(render does not generate the curated changelog)")
    return worst


def _restore_contained(root_fd, relpath, old_bytes):
    """Best-effort rollback primitive (PR-C Phase-C owned-miss): restore a target this render rewrote to its
    captured preimage `old_bytes`, or DELETE it when old_bytes is None (this render created it). Atomic
    entry-replace beneath a no-follow parent fd, the SAME reopen-TOCTOU discipline _write_contained uses (an
    O_EXCL temp renamed over the entry, never an O_TRUNC of the destination name). Raises
    ViewsError/OSError/JournalError on failure so the caller can name the unrestored path; never a silent
    skip (no-concealed-failure)."""
    pfd, name = _journal._open_parent(root_fd, relpath)
    try:
        if old_bytes is None:
            try:
                os.unlink(name, dir_fd=pfd)               # the target did not exist pre-write: undo the create
            except FileNotFoundError:
                pass
            return
        st = _journal._lstat_at(pfd, name)
        if st is not None and not stat.S_ISREG(st.st_mode):
            raise ViewsError("refusing to restore {}: destination is not a regular file".format(relpath))
        tmpname = ".{}.opf-restore.{}.{}".format(name, os.getpid(), os.urandom(8).hex())
        fd = os.open(tmpname, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600, dir_fd=pfd)
        _renamed = False
        try:
            try:
                os.fchmod(fd, stat.S_IMODE(st.st_mode) if st is not None else _VIEW_FILE_MODE)
                _journal._write_all(fd, old_bytes)
                os.fsync(fd)
            finally:
                os.close(fd)
            os.rename(tmpname, name, src_dir_fd=pfd, dst_dir_fd=pfd)
            _renamed = True
            os.fsync(pfd)
        finally:
            if not _renamed:
                try:
                    os.unlink(tmpname, dir_fd=pfd)
                except OSError:
                    pass
    finally:
        os.close(pfd)


def _restore_preimages(product_root, res, preimages):
    """Best-effort rollback of a render/check disagreement (PR-C Phase-C owned-miss): restore each target this
    render actually rewrote to its captured preimage bytes, or delete a target this render newly created
    (preimage None). Reopens the no-follow store/product fds and restores descriptor-relative. Returns the
    list of relpaths it could NOT restore (empty on full success). Never raises: a failed restore is REPORTED,
    never masked (no-concealed-failure). The residual non-atomicity across multiple files is disclosed in the
    render docstring/spec; this restore is the best-effort recovery, not an all-or-nothing transaction."""
    failed = []
    store_root_fd = product_root_fd = None
    try:
        try:
            store_root_fd = _opf_store._open_store_root_fd(res.store_root, res.pointer_source != "default")
            product_root_fd = _opf_store._open_root_fd(product_root)
        except OSError as exc:
            # The reopen failed, so NOT ONE captured target could be restored. Return every affected relpath
            # (the relpath element of each preimage key), so the caller can NAME the paths that may be left
            # mid-write rather than a single opaque sentinel that LOSES them (DEFECT-1c; no-concealed-failure),
            # and still convey the reopen error alongside them. Non-raising.
            affected = sorted(relpath for (_scope, relpath) in preimages)
            return affected + ["<reopen for rollback failed: {}>".format(exc)]
        for (scope, relpath), old in preimages.items():
            fd = product_root_fd if scope == "product" else store_root_fd
            try:
                _restore_contained(fd, relpath, old)
            except (ViewsError, OSError, _journal.JournalError):
                failed.append(relpath)
    finally:
        if store_root_fd is not None:
            try:
                os.close(store_root_fd)
            except OSError:
                pass
        if product_root_fd is not None:
            try:
                os.close(product_root_fd)
            except OSError:
                pass
    return failed


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

    def render_write(root):
        """Drive the render WRITE path over a synthetic store WITHOUT the U6 store-integrity gate, so the
        render-LOGIC vectors below (transforms, joins, goldens, and the fail-closed source cases) exercise
        _render_resolved directly over stores that are NOT full valid whole-stores. The composed U6 write
        gate is exercised separately by the dedicated valid/invalid render() vectors near the end. Mirrors
        render()'s resolution handling (NOT-ADOPTED -> 0, non-RESOLVED -> 2) then calls the shared post-gate
        body; a mutating write still passes _write_contained's defence-in-depth guard (the module flag)."""
        product_root = Path(os.path.abspath(str(root)))
        res = _opf_store.resolve_store(product_root)
        if res.status == _opf_store.NOT_ADOPTED:
            return EXIT_OK
        if res.status != _opf_store.RESOLVED:
            return EXIT_CANNOT_EVALUATE
        return _render_resolved_store(product_root, res, False)

    # Every view and two mirrors, declared with the kind/sources this generator renders.
    manifest = "\n".join([
        "[opf]",
        'standard = "opf"',
        'spec_version = "1.1.0"',
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
        "[types.contribution]", 'namespace = "CN"',
        "[types.maintainer_decision]", 'namespace = "MD"',
        "[types.preference_pattern]", 'namespace = "PP"',
        "",
        "[vendors]", "registered = []",
        "",
        _view("TODO.md", "composed", ["backlog_item", "block"]),
        _view("BACKLOG.md", "composed", ["backlog_item", "block"]),
        _view("PIPELINE.md", "composed", ["backlog_item", "block"]),
        _view("DONE.md", "composed", ["done"]),
        _view("FINDINGS.md", "composed", ["finding"]),
        _view("DECISIONS.md", "composed",
              ["pending_decision", "autonomous_decision", "maintainer_decision", "preference_pattern"]),
        _view("CONTRIBUTIONS.md", "composed", ["contribution"]),
        _view("DECISIONS.toml", "projection",
              ["pending_decision", "autonomous_decision", "maintainer_decision", "preference_pattern"]),
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
        write_toml(root, "version.toml", "\n".join([
            "schema = 1", "", "[[release]]",
            'version = "0.1.0"',
            'date = "2026-01-01T00:00:00Z"',
            'worklog_span = []',
            'coverage_digest = "{}"'.format(_opf_release.coverage_digest([])),
        ]) + "\n")

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

        import signal as _signal
        import time as _time
        _fifo_dir = base / "fifo-src"; _fifo_dir.mkdir()
        os.mkfifo(str(_fifo_dir / "blk.index.toml"))
        _ffd = os.open(str(_fifo_dir), os.O_RDONLY | os.O_DIRECTORY)
        class _Watchdog(Exception):
            pass
        def _boom(_s, _f):
            raise _Watchdog()
        # G (self-test-discrimination): the LOCAL pre-open S_ISREG guard in _read_raw_and_parsed, not the
        # hardened downstream _journal._read_contained (which ALSO refuses a non-regular file with an
        # identical "not a regular file" diagnostic), must be what refuses the FIFO. Record whether the
        # downstream reader is reached: with the local guard present it is NEVER called, so removing that
        # guard (letting the FIFO fall through to _read_contained) flips this check red.
        _rc_calls = []
        _orig_rc = _journal._read_contained
        def _recording_rc(root_fd, relpath):
            _rc_calls.append(relpath)
            return _orig_rc(root_fd, relpath)
        _journal._read_contained = _recording_rc
        # C (test-hermeticity): snapshot the caller's SIGALRM disposition and mask, and its ITIMER_REAL +
        # pending state through the SHARED _opf_store.snapshot_caller_alarm helper; unblock SIGALRM for the
        # probe; and restore all of them so this watchdog leaves the ambient alarm state unchanged (never
        # cancelling a caller's timer, unblocking its SIGALRM, nor destroying its pending alarm).
        _prev = _signal.getsignal(_signal.SIGALRM)               # capture WITHOUT installing yet (F2)
        _have_mask = hasattr(_signal, "pthread_sigmask")
        _prev_mask = _signal.pthread_sigmask(_signal.SIG_BLOCK, []) if _have_mask else None
        _alarm_snap = _opf_store.snapshot_caller_alarm()         # ITIMER value/interval + pending (shared helper)
        _fifo_ok = False
        # F2 (round-10, class-width): the SIGALRM UNBLOCK and the timer ARM live INSIDE the try, so the
        # finally restores the caller's mask, disposition, and timer even if a signal fires during setup. An
        # ambient SIGALRM that is BLOCKED and already PENDING (the timer fired while blocked) would otherwise
        # be delivered the instant SIGALRM is unblocked and, with the unblock OUTSIDE the try/finally, would
        # raise _Watchdog out of the probe uncaught AND leave the caller's mask corrupted (SIGALRM
        # unblocked). Any inherited pending SIGALRM is first DISCARDED under SIG_IGN (POSIX: setting SIG_IGN
        # discards a pending signal whether or not it is blocked) so it cannot fire _boom spuriously; the
        # shared restore_caller_alarm RE-POSTS it on exit (round-15 F2) so the caller's pending alarm is
        # preserved, not destroyed.
        try:
            _signal.signal(_signal.SIGALRM, _signal.SIG_IGN)     # discard any inherited pending SIGALRM
            _signal.signal(_signal.SIGALRM, _boom)               # now install the watchdog handler
            if _have_mask:
                _signal.pthread_sigmask(_signal.SIG_UNBLOCK, {_signal.SIGALRM})
            _signal.setitimer(_signal.ITIMER_REAL, 5)
            try:
                _read_raw_and_parsed(_ffd, "blk.index.toml")
            except ViewsError:
                _fifo_ok = True
            except _Watchdog:
                _fifo_ok = False
        finally:
            _signal.setitimer(_signal.ITIMER_REAL, 0)
            _signal.signal(_signal.SIGALRM, _prev)
            if _have_mask:
                _signal.pthread_sigmask(_signal.SIG_SETMASK, _prev_mask)
            _opf_store.restore_caller_alarm(*_alarm_snap)        # shared elapsed-aware timer + pending restore
            _journal._read_contained = _orig_rc
            os.close(_ffd)
        check("fifo-source-fails-closed-not-hang", _fifo_ok and not _rc_calls)
        _slp = base / "symparent"; _slp.mkdir(); (_slp / "real").mkdir()
        (_slp / "real" / "x.index.toml").write_text("schema = 1\n", encoding="utf-8")
        (_slp / "toml").symlink_to("real")
        _spfd = os.open(str(_slp), os.O_RDONLY | os.O_DIRECTORY)
        try:
            _read_raw_and_parsed(_spfd, "toml/x.index.toml")
            _sp_ok = False
        except ViewsError:
            _sp_ok = True
        except _journal.JournalError:
            _sp_ok = False
        finally:
            os.close(_spfd)
        check("symlinked-parent-component-maps-to-viewserror", _sp_ok)
        # F5 (read-path fail-closed on an unreadable source): a bad store_root_fd (or fd exhaustion at
        # os.dup) makes _open_parent's terminal os.dup raise a RAW OSError that _lstat_contained does not
        # wrap; _read_raw_and_parsed must map it to a ViewsError (a cannot-evaluate), never let the OSError
        # escape the render boundary as an uncaught exit-1 traceback. A single-component relpath drives the
        # os.dup(root_fd) path with fd -1. Pre-fix (only `except JournalError`) this raised OSError; post-fix
        # it is a ViewsError, so the check discriminates the OSError-widening at the reader boundary.
        _f5_kind = None
        try:
            _read_raw_and_parsed(-1, "x.index.toml")
        except ViewsError:
            _f5_kind = "ViewsError"
        except OSError:
            _f5_kind = "OSError"
        check("read-bad-fd-maps-to-viewserror", _f5_kind == "ViewsError")
        # QA-1 (write-path sibling of the read-path check above): _write_contained's _open_parent call maps a
        # symlinked or non-directory intermediate component to a ViewsError, not an uncaught JournalError.
        # _open_parent signals that case as JournalError (NOT an OSError subclass), so the pre-fix
        # `except OSError` let it escape _write_contained; render()'s handler catches only
        # ViewsError/RecursionError/ValueError, so it died as exit 1, colliding with EXIT_DRIFT. check=True so
        # the write gate is not consulted; the failure is at the parent walk, before any write.
        _wslp = base / "wsymparent"; _wslp.mkdir(); (_wslp / "real").mkdir()
        (_wslp / "toml").symlink_to("real")
        _wspfd = os.open(str(_wslp), os.O_RDONLY | os.O_DIRECTORY)
        try:
            _write_contained(_wspfd, "toml/TODO.md", "x\n", True)
            _wsp_ok = False
        except ViewsError:
            _wsp_ok = True
        except _journal.JournalError:
            _wsp_ok = False
        finally:
            os.close(_wspfd)
        check("write-symlinked-parent-component-maps-to-viewserror", _wsp_ok)
        # F4 (reopen-TOCTOU, class B): _write_contained never truncates the destination NAME in place; it
        # writes a fresh O_EXCL temp and atomically renames it over the entry. So a destination raced to a
        # HARDLINK of a victim (O_NOFOLLOW refuses a symlink, NOT a hardlink) cannot have the victim's inode
        # truncated: the rename re-points only the directory entry, leaving the victim's bytes intact while
        # the store file receives the new content. Pre-fix (O_WRONLY|O_TRUNC of `name`) the shared inode was
        # truncated and rewritten through the hardlink, corrupting the victim; this vector FAILS pre-fix
        # (victim reads the new bytes) and passes post-fix (victim intact, destination updated).
        _f4dir = base / "f4-hardlink-swap"; _f4dir.mkdir()
        _f4victim = _f4dir / "victim"; _f4victim.write_text("VICTIM-INTACT", encoding="utf-8")
        os.link(str(_f4victim), str(_f4dir / "TODO.md"))   # destination is a hardlink to the victim inode
        _f4fd = os.open(str(_f4dir), os.O_RDONLY | os.O_DIRECTORY)
        try:
            _write_contained(_f4fd, "TODO.md", "NEW-VIEW-CONTENT\n", False)   # real write (gate composed)
        finally:
            os.close(_f4fd)
        check("write-hardlink-swap-victim-intact", _f4victim.read_text(encoding="utf-8") == "VICTIM-INTACT")
        check("write-hardlink-swap-dest-updated",
              (_f4dir / "TODO.md").read_text(encoding="utf-8") == "NEW-VIEW-CONTENT\n")
        # F(unique-temp): the write temp uses a UNIQUE, exclusively-created name, never a FIXED
        # ".{name}.opf-tmp" it would unconditionally unlink on collision -- which, under concurrency, is
        # ANOTHER live call's temp. Plant a file at the OLD fixed temp name and confirm a write leaves it
        # intact (the new unique name never addresses it). Pre-fix the write would collide on that fixed
        # name and unlink the planted file; post-fix it is untouched.
        _utdir = base / "unique-temp"; _utdir.mkdir()
        (_utdir / "TODO.md").write_text("OLD\n", encoding="utf-8")
        _utplanted = _utdir / ".TODO.md.opf-tmp"
        _utplanted.write_text("ANOTHER-CALLERS-TEMP", encoding="utf-8")
        _utfd = os.open(str(_utdir), os.O_RDONLY | os.O_DIRECTORY)
        try:
            _write_contained(_utfd, "TODO.md", "NEW\n", False)
        finally:
            os.close(_utfd)
        check("write-unique-temp-does-not-clobber-fixed-name",
              _utplanted.exists() and _utplanted.read_text(encoding="utf-8") == "ANOTHER-CALLERS-TEMP")
        check("write-unique-temp-dest-updated",
              (_utdir / "TODO.md").read_text(encoding="utf-8") == "NEW\n")
        # F(temp-cleanup): a failure DURING the temp-file lifetime (fchmod / write / fsync), BEFORE the atomic
        # rename, must leave NO temp behind and must re-raise the ORIGINAL error unmasked. Inject an ENOSPC
        # mid-write; post-fix the descriptor-relative unlink removes the orphan while the ViewsError-mapped
        # ENOSPC still surfaces. Pre-fix (cleanup only on a failed rename) the temp file leaks, so the
        # no-temp-leak assertion flips red.
        _tcdir = base / "temp-cleanup"; _tcdir.mkdir()
        (_tcdir / "TODO.md").write_text("OLD\n", encoding="utf-8")
        _tcfd = os.open(str(_tcdir), os.O_RDONLY | os.O_DIRECTORY)
        _tc_orig_write_all = _journal._write_all
        _journal._write_all = lambda fd, data: (_ for _ in ()).throw(OSError(28, "No space left on device"))
        _tc_err = None
        try:
            _write_contained(_tcfd, "TODO.md", "NEW\n", False)
        except ViewsError as _e:
            _tc_err = str(_e)
        finally:
            _journal._write_all = _tc_orig_write_all
            os.close(_tcfd)
        check("write-midfailure-surfaces-original-error",
              _tc_err is not None and "No space left" in _tc_err)
        check("write-midfailure-no-temp-leak",
              not any(".opf-tmp." in _n for _n in os.listdir(str(_tcdir))))
        # F(mode-preserve): the atomic replace PRESERVES the destination's existing mode; a restrictive 0600
        # view is not widened to the temp's create mode. The prestate mode is set explicitly (fchmod, not
        # umask), so the assertion is hermetic. Pre-fix the temp's 0644 create mode became the view's mode on
        # rename; post-fix the temp is fchmod'd to the destination's 0600 first.
        _mpdir = base / "mode-preserve"; _mpdir.mkdir()
        _mpview = _mpdir / "TODO.md"; _mpview.write_text("OLD\n", encoding="utf-8")
        os.chmod(str(_mpview), 0o600)
        _mpfd = os.open(str(_mpdir), os.O_RDONLY | os.O_DIRECTORY)
        try:
            _write_contained(_mpfd, "TODO.md", "NEW\n", False)
        finally:
            os.close(_mpfd)
        check("write-preserves-existing-restrictive-mode",
              stat.S_IMODE(os.stat(str(_mpview)).st_mode) == 0o600
              and _mpview.read_text(encoding="utf-8") == "NEW\n")
        # The positive mapping DISCRIMINATES the regex/baseline lookup: backlog_item IS a baseline type, so a
        # broken regex or baseline lookup flips it from "backlog_item" to None.
        check("mirror-type-resolves-record-type", _mirror_type("BACKLOG_ITEM-INDEX.md") == "backlog_item")
        # ROUND-6 codex: the _LEDGER_SOURCES exclusion (line ~985) IS load-bearing and needs its own
        # discriminating pin. "worklog" IS a key in _opf_schema.BASELINE_SPECS (a prior F12 note wrongly said
        # neither ledger name was), so WITHOUT the exclusion _mirror_type("WORKLOG-INDEX.md") would fall
        # through to the baseline lookup and return "worklog" (a ledger reads its own worklog.toml, it has no
        # <type>.index.toml to mirror). The exclusion returns None; removing it flips this pin from None to
        # "worklog". ("version" is NOT a baseline key, so a VERSION-INDEX.md pin would not discriminate.)
        check("mirror-type-worklog-ledger-rejected", _mirror_type("WORKLOG-INDEX.md") is None)
        check("mirror-type-worklog-is-baseline-key", "worklog" in _opf_schema.BASELINE_SPECS)
        _f5_bb, _f5_hid = join_actionability([dict(id="BI-1", status="open")],
                                             [dict(status="active", scopes=["BI-1"])])
        check("idless-block-hides-nothing", _f5_bb == {} and _f5_hid == set())
        _f5_body = render_backlog(dict(backlog_item=[dict(id="BI-1", status="open", title="t")],
                                       block=[dict(status="active", scopes=["BI-1"])]))
        check("idless-block-no-dangling-annotation",
              "blocked by \n" not in _f5_body and _f5_body.rstrip().endswith("actionable"))
        # QA-2 (sibling of the closed id-less-block finding): every composed record renderer indexes the
        # record id; an id-less record from a trusted caller must render gracefully (via .get("id"), the
        # posture render_worklog/render_mirror/_id_key already take), never raise an unmapped KeyError --
        # which render() maps nowhere, so it would escape as exit 1, colliding with EXIT_DRIFT. The round-1
        # fix hardened id-less BLOCKS in join_actionability; this closes the sibling record sinks across the
        # renderers. Each case FAILS pre-fix with KeyError('id') and passes post-fix.
        _idless_cases = [
            (render_todo, {"backlog_item": [{"status": "open", "title": "t"}], "block": []}),
            (render_backlog, {"backlog_item": [{"status": "open", "title": "t"}], "block": []}),
            (render_pipeline, {"backlog_item": [{"status": "open", "title": "t"}], "block": []}),
            (render_done, {"done": [{"status": "recorded", "title": "t"}]}),
            (render_findings, {"finding": [{"status": "open", "title": "t"}]}),
            (render_decisions, {"pending_decision": [{"status": "open", "title": "t"}],
                                "autonomous_decision": [{"status": "recorded", "title": "t"}]}),
            (render_blocks, {"block": [{"status": "active", "title": "t"}]}),
            (render_handoff, {"handoff": [{"status": "current", "title": "t"}]}),
            (render_references, {"reference": [{"status": "recorded", "title": "t"}]}),
        ]

        def _renders_without_keyerror(fn, arg):
            try:
                fn(arg)
                return True
            except KeyError:
                return False

        check("idless-record-renderers-no-keyerror",
              all(_renders_without_keyerror(fn, src) for fn, src in _idless_cases))
        _d01 = _opf_release.coverage_digest([dict(id="WL-1", date="2026-01-01T00:00:00Z",
            actor=dict(kind="maintainer"), kind="added", summary="first change")])
        _d02 = _opf_release.coverage_digest([dict(id="WL-1", date="2026-01-02T00:00:00Z",
            actor=dict(kind="maintainer"), kind="added", summary="first change")])
        check("coverage-digest-date-sensitive", _d01 != _d02)
        # F13: the stale word-presence pin ("O_NONBLOCK"/"lstat-to-open" in the docstring) is removed; it
        # asserted the presence of text, not behaviour, and pinned a docstring claim that _read_contained
        # "does not pass O_NONBLOCK" which is FALSE (it does). The LIVE behaviour the disclosure describes,
        # a writer-less FIFO source fails closed without hanging, is asserted by "fifo-source-fails-closed-
        # not-hang" above (an actual FIFO under a watchdog), which discriminates the real O_NONBLOCK guard.
        check("read-ledger-schema-divergence-disclosed",
              "optional-marker" in _load_worklog.__doc__ and "optional-marker" in _load_version.__doc__)
        check("entry-writes-fixed-date", 'date = "2026-01-01T00:00:00Z"' in _entry("WL-2", "fixed", "x"))

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
        # 1.1.0 base-schema mirror columns: contribution's identity fields and its delivery TABLE (rendered
        # as sorted key=value scalars, so a dict column does not dump a Python repr), and preference_pattern's
        # context/rationale. Free-text columns still escape (removing _md_text from any flips these).
        cnm = render_mirror("contribution", [{
            "id": "CN-1", "type": "contribution", "status": "sent", "title": "t",
            "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-02T00:00:00Z",
            "actor": {"kind": "maintainer"}, "recipient": P, "dedup_class": "dc", "content_digest": "sha256:x",
            "delivery": {"channel": "email", "ref": "m-1", "sent_at": "2026-01-01T00:00:00Z"}}])
        check("mirror-recipient-exact-escape", "- recipient: " + ESC in cnm)
        # The delivery TABLE renders as sorted key=value scalars; the `_` in the `sent_at` key escapes for the
        # markdown sink (sent\_at), exactly as every projected key/value does, never a raw Python dict repr.
        check("mirror-delivery-scalars", "- delivery: channel=email, ref=m-1, sent" + "\\" + "_at=2026-01-01T00:00:00Z" in cnm)
        check("mirror-delivery-no-dict-repr", "{'channel'" not in cnm and "{" not in cnm.split("- delivery:")[1].split("\n")[0])
        check("mirror-contribution-no-raw-payload", P not in cnm)
        ppm = render_mirror("preference_pattern", [{
            "id": "PP-1", "type": "preference_pattern", "status": "active", "title": "t",
            "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-02T00:00:00Z",
            "actor": {"kind": "maintainer"}, "context": P, "rationale": "because"}])
        check("mirror-context-exact-escape", "- context: " + ESC in ppm)
        check("mirror-rationale-present", "- rationale: because" in ppm)
        check("mirror-preference-no-raw-payload", P not in ppm)

        # DECISIONS.toml projection (unit level, no store): an EMPTY projection is first-class (the NOW
        # scaffold ships empty indexes), reparses, and carries empty arrays + an empty derived join. x-vendor
        # tables are excluded from a projected row.
        _empty_proj = render_decisions_toml({})
        _ep = tomllib.loads(_empty_proj)
        check("projection-empty-reparses", _ep["projection"] == "decisions" and _ep["schema"] == 1)
        check("projection-empty-arrays", _ep["pending_decision"] == [] and _ep["maintainer_decision"] == [])
        check("projection-empty-derived", _ep["derived"] == {"effective": [], "superseded": []})
        _xrow = render_decisions_toml({"maintainer_decision": [{
            "id": "MD-9", "type": "maintainer_decision", "status": "recorded", "title": "t",
            "decision": "d", "x-aiqt": {"rule": 5}, "actor": {"kind": "maintainer", "id": "jp"}}]})
        _xp = tomllib.loads(_xrow)
        check("projection-excludes-x-vendor", "x-aiqt" not in _xp["maintainer_decision"][0])
        # m3: the row is self-describing (`type`) and the actor is projected as a NESTED sub-table (the
        # faithful full-envelope shape, spec 10.5), not a flattened actor_kind/actor_id pair.
        check("projection-includes-type",
              _xp["maintainer_decision"][0].get("type") == "maintainer_decision")
        check("projection-includes-actor-subtable",
              _xp["maintainer_decision"][0].get("actor") == {"kind": "maintainer", "id": "jp"})
        # m3 binding verification: a TWO-row projection with a DISTINCT actor per row reparses with each
        # actor bound to its OWN row (the canonical emitter's per-element array-of-tables sub-table binding).
        _tworow = render_decisions_toml({"maintainer_decision": [
            {"id": "MD-1", "type": "maintainer_decision", "status": "recorded", "title": "t",
             "decision": "d", "actor": {"kind": "maintainer", "id": "a"}},
            {"id": "MD-2", "type": "maintainer_decision", "status": "recorded", "title": "t",
             "decision": "d", "actor": {"kind": "importer", "id": "b"}}]})
        _tw = tomllib.loads(_tworow)["maintainer_decision"]
        check("projection-actor-binds-per-row",
              _tw[0].get("actor") == {"kind": "maintainer", "id": "a"}
              and _tw[1].get("actor") == {"kind": "importer", "id": "b"})
        # A source name outside the projection's declared set is impossible (the sources tuple is fixed), but a
        # `/proposed` pending decision is EXCLUDED from the derived resolution join (settled-only, spec 8.4).
        _projd = render_decisions_toml({"pending_decision": [
            {"id": "PD-1", "status": "decided", "title": "t", "decision": "x"},
            {"id": "PD-2", "status": "decided/proposed", "title": "t", "decision": "y",
             "links": [{"rel": "supersedes", "id": "PD-1"}], "actor": {"kind": "assistant"}}]})
        _pjd = tomllib.loads(_projd)
        check("projection-derived-settled-only", _pjd["derived"] == {"effective": ["PD-1"], "superseded": []})

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
        import _byte_canon as check_byte_canon  # authoritative byte-canon leg; pure function over bytes
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
        # contribution: every lifecycle group populated so CONTRIBUTIONS.md and the DECISIONS.toml projection
        # exercise each state. CN-3 is `sent/proposed` (assistant-authored, awaiting ratification); the rest
        # are maintainer-authored. sent/acknowledged/superseded carry the full delivery bundle; proposed and
        # withdrawn carry none (sent_at is forbidden before a contribution is sent).
        write_toml(root, "contribution.index.toml", "\n".join([
            "schema = 1",
            _rec("CN-1", "contribution", "proposed", "proposed contribution",
                 recipient="grc", dedup_class="dc-1", content_digest="sha256:aaa"),
            _rec("CN-2", "contribution", "sent", "sent contribution",
                 recipient="grc", dedup_class="dc-2", content_digest="sha256:bbb",
                 delivery={"channel": "email", "ref": "m-2", "sent_at": "2026-09-01T00:00:00Z"}),
            _rec("CN-3", "contribution", "sent/proposed", "awaiting contribution", actor="assistant",
                 recipient="grc", dedup_class="dc-3", content_digest="sha256:ccc",
                 delivery={"channel": "email", "ref": "m-3", "sent_at": "2026-09-02T00:00:00Z"}),
            _rec("CN-4", "contribution", "acknowledged", "acked contribution",
                 recipient="grc", dedup_class="dc-4", content_digest="sha256:ddd",
                 delivery={"channel": "email", "ref": "m-4", "sent_at": "2026-09-03T00:00:00Z"}),
            _rec("CN-5", "contribution", "superseded", "superseded contribution",
                 recipient="grc", dedup_class="dc-5", content_digest="sha256:eee",
                 delivery={"channel": "email", "ref": "m-5", "sent_at": "2026-09-04T00:00:00Z"}),
            _rec("CN-6", "contribution", "withdrawn", "withdrawn contribution",
                 recipient="grc", dedup_class="dc-6", content_digest="sha256:fff"),
        ]) + "\n")
        # maintainer_decision: a ruling that exemplifies PP-1 (the exemplifies tail), and a bare ruling.
        write_toml(root, "maintainer_decision.index.toml", "\n".join([
            "schema = 1",
            _rec("MD-1", "maintainer_decision", "recorded", "first ruling",
                 decision="rule this way", links=[("exemplifies", "PP-1")]),
            _rec("MD-2", "maintainer_decision", "recorded", "second ruling",
                 decision="rule that way"),
        ]) + "\n")
        # preference_pattern: an active pattern, a retired one, and an assistant-distilled `active/proposed`
        # awaiting ratification (the Active / Retired / awaiting split).
        write_toml(root, "preference_pattern.index.toml", "\n".join([
            "schema = 1",
            _rec("PP-1", "preference_pattern", "active", "active pattern",
                 context="when X", rationale="because Y"),
            _rec("PP-2", "preference_pattern", "retired", "retired pattern",
                 context="when Z", rationale="because W"),
            _rec("PP-3", "preference_pattern", "active/proposed", "proposed pattern", actor="assistant",
                 context="when Q", rationale="because R"),
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
                {"id": "WL-2", "date": "2026-01-01T00:00:00Z", "actor": {"kind": "maintainer"},
                 "kind": "fixed", "summary": "second change"}])),
            "",
            "[[summary]]",
            'covers = "unreleased"',
            'status = "working"',
        ]) + "\n")

        # Write mode renders cleanly, then --check is clean (a byte-stable re-render).
        check("populated-write-ok", render_write(root) == EXIT_OK)
        check("populated-check-clean", render(["--root", str(root), "--check"]) == EXIT_OK)
        _wl_on_disk = tomllib.loads((root / WORKING_DIRNAME / "toml" / "worklog.toml").read_text(encoding="utf-8"))
        _ver_on_disk = tomllib.loads((root / WORKING_DIRNAME / "toml" / "version.toml").read_text(encoding="utf-8"))
        check("coverage-digest-reconciles-worklog",
              _opf_release.coverage_digest(_wl_on_disk.get("entry", []))
              == _ver_on_disk["release"][0]["coverage_digest"])

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
                             "## Autonomous decisions\n- AD-1 acted\n\n"
                             "## Maintainer decisions\n- MD-1 first ruling (exemplifies PP-1)\n"
                             "- MD-2 second ruling\n\n"
                             "## Preference patterns\n- PP-1 (active) active pattern\n"
                             "- PP-2 (retired) retired pattern\n"
                             "- PP-3 (active/proposed) proposed pattern\n"),
            "CONTRIBUTIONS.md": ("\n# CONTRIBUTIONS\n\n## Proposed\n- CN-1 proposed contribution\n\n"
                                 "## Sent\n- CN-2 sent contribution [email, m-2, 2026-09-01T00:00:00Z]\n\n"
                                 "## Awaiting ratification\n- CN-3 (sent/proposed) awaiting contribution "
                                 "[email, m-3, 2026-09-02T00:00:00Z]\n\n"
                                 "## Acknowledged\n- CN-4 acked contribution "
                                 "[email, m-4, 2026-09-03T00:00:00Z]\n\n"
                                 "## Superseded\n- CN-5 superseded contribution "
                                 "[email, m-5, 2026-09-04T00:00:00Z]\n\n"
                                 "## Withdrawn\n- CN-6 withdrawn contribution\n"),
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
        # DECISIONS.md widened to the maintainer_decision and preference_pattern sources: the exemplifies
        # tail is projected from the record's own links; PP splits Active / Retired / awaiting ratification.
        _mdi = decisions.index("## Maintainer decisions")
        _ppi = decisions.index("## Preference patterns")
        check("decisions-maintainer-exemplifies-tail", "MD-1 first ruling (exemplifies PP-1)" in decisions[_mdi:_ppi])
        check("decisions-maintainer-no-tail-when-none", "- MD-2 second ruling\n" in decisions[_mdi:_ppi])
        check("decisions-pp-active", "- PP-1 (active) active pattern" in decisions[_ppi:])
        check("decisions-pp-retired", "- PP-2 (retired) retired pattern" in decisions[_ppi:])
        check("decisions-pp-proposed-full-status", "- PP-3 (active/proposed) proposed pattern" in decisions[_ppi:])

        # CONTRIBUTIONS.md: each lifecycle group, with the delivery pointer projected and the `sent/proposed`
        # contribution held under Awaiting ratification (NONTERMINAL, spec 8.4), never under Sent.
        contributions = read_view("CONTRIBUTIONS.md")
        _senti = contributions.index("## Sent")
        _awaiti = contributions.index("## Awaiting ratification")
        _acki = contributions.index("## Acknowledged")
        check("contributions-sent-ratified-only", "CN-2" in contributions[_senti:_awaiti]
              and "CN-3" not in contributions[_senti:_awaiti])
        check("contributions-awaiting-holds-proposed", "CN-3 (sent/proposed) awaiting contribution"
              in contributions[_awaiti:_acki])
        check("contributions-delivery-pointer", "CN-2 sent contribution [email, m-2, 2026-09-01T00:00:00Z]"
              in contributions)
        check("contributions-proposed-no-delivery", "- CN-1 proposed contribution\n" in contributions)
        check("contributions-withdrawn-listed", "- CN-6 withdrawn contribution\n" in contributions)

        # DECISIONS.toml: the machine projection (spec 10.5). Its header is a `#` comment block (never the
        # HTML markdown header); its body reparses and carries the four sources, the projected extra fields,
        # nested link tables, and the derived resolution join. x-<vendor> data is excluded by construction.
        dtoml_text = read_view("DECISIONS.toml")
        check("projection-header-is-toml-comment", dtoml_text.startswith("# GENERATED by opf render"))
        check("projection-header-no-html-comment", "<!--" not in dtoml_text)
        check("projection-header-has-digest", "# source-set-digest: sha256:" in dtoml_text)
        check("projection-header-has-generator", "generator: opf-views/2" in dtoml_text)
        # R7: the TOML `#` header must carry the regenerate command INTACT and runnable. The HTML-comment
        # dash-run scrubber would corrupt `--write` into `- -write`; the TOML-specific scrubber leaves it
        # (a `--` is inert in a `#` comment). Fails without _toml_comment_safe.
        check("projection-header-regenerate-command-runnable",
              "# regenerate: opf render --write" in dtoml_text and "opf render - -write" not in dtoml_text)
        _proj = tomllib.loads(dtoml_text)
        check("projection-marker", _proj.get("projection") == "decisions" and _proj.get("schema") == 1)
        check("projection-pd-ids", [r["id"] for r in _proj["pending_decision"]] == ["PD-1", "PD-2", "PD-3"])
        check("projection-md-ids", [r["id"] for r in _proj["maintainer_decision"]] == ["MD-1", "MD-2"])
        check("projection-pp-ids", [r["id"] for r in _proj["preference_pattern"]] == ["PP-1", "PP-2", "PP-3"])
        check("projection-extra-field", any(r.get("decision") == "do X" for r in _proj["pending_decision"]))
        check("projection-nested-links",
              any(l == {"rel": "supersedes", "id": "PD-1"}
                  for r in _proj["pending_decision"] for l in r.get("links", [])))
        check("projection-derived-join",
              _proj["derived"] == {"effective": ["PD-2", "PD-3"], "superseded": ["PD-1"]})
        check("projection-excludes-proposed-pd-from-derived",
              "PD-3" in _proj["derived"]["effective"])   # PD-3 is open (settled), so it is a non-superseded head
        # Byte-stability: the projection is deterministic (no wall-clock content), so a re-render is a byte
        # no-op (already asserted store-wide by populated-check-clean; pinned here for the projection itself).
        check("projection-byte-stable", render_write(root) == EXIT_OK
              and read_view("DECISIONS.toml") == dtoml_text)

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
        check("empty-write-ok", render_write(eroot) == EXIT_OK)
        etodo = (eroot / WORKING_DIRNAME / "TODO.md").read_text(encoding="utf-8")
        check("empty-todo-valid-empty", _EMPTY in etodo and etodo.startswith("<!-- GENERATED"))
        check("empty-store-version-bytes", (eroot / "VERSION").read_text(encoding="utf-8") == "0.1.0\n")
        # The new 1.1.0 views render over EMPTY indexes too (the NOW scaffold ships empty indexes): the empty
        # CONTRIBUTIONS.md carries the empty marker under a group, and the empty DECISIONS.toml is a valid,
        # reparsing projection with empty arrays and an empty derived join.
        econtrib = (eroot / WORKING_DIRNAME / "CONTRIBUTIONS.md").read_text(encoding="utf-8")
        check("empty-contributions-valid-empty", _EMPTY in econtrib and econtrib.startswith("<!-- GENERATED"))
        edtoml = (eroot / WORKING_DIRNAME / "DECISIONS.toml").read_text(encoding="utf-8")
        check("empty-projection-is-toml-comment", edtoml.startswith("# GENERATED by opf render"))
        _epe = tomllib.loads(edtoml)
        check("empty-projection-reparses", _epe.get("projection") == "decisions"
              and _epe["pending_decision"] == [] and _epe["derived"] == {"effective": [], "superseded": []})

        # --- F3: a declared VERSION with an EMPTY version ledger fails closed, never a zero-byte write ----
        # (Runs under the scaffolded gate ON, so it reaches rendering and fails at F3, not the F1 write gate.)
        vroot = new_root()
        write_toml(vroot, "manifest.toml", manifest)
        empty_indexes(vroot)
        write_toml(vroot, "version.toml", "schema = 1\n")   # OVERRIDE: declared but no-release ledger
        check("empty-version-cannot-eval", render_write(vroot) == EXIT_CANNOT_EVALUATE)
        check("empty-version-not-zero-byte", not (vroot / "VERSION").exists())

        # --- a MISSING declared source fails closed (exit 2) --------------------------------------------
        mroot = new_root()
        write_toml(mroot, "manifest.toml", manifest)
        empty_indexes(mroot)
        (mroot / WORKING_DIRNAME / "toml" / "block.index.toml").unlink()   # TODO/BACKLOG/BLOCKS/BLOCK-INDEX need it
        check("missing-source-cannot-eval", render_write(mroot) == EXIT_CANNOT_EVALUATE)

        # --- a MALFORMED record fails closed (exit 2), never a silent partial view ----------------------
        broot = new_root()
        write_toml(broot, "manifest.toml", manifest)
        empty_indexes(broot)
        write_toml(broot, "finding.index.toml", "\n".join([
            "schema = 1",
            _rec("FN-1", "finding", "not-a-state", "bad status"),   # illegal status: INVALID record
        ]) + "\n")
        check("malformed-record-cannot-eval", render_write(broot) == EXIT_CANNOT_EVALUATE)

        s2root = new_root(); write_toml(s2root, "manifest.toml", manifest); empty_indexes(s2root)
        write_toml(s2root, "backlog_item.index.toml", "schema = 2\n")
        check("index-schema-pin-cannot-eval", render_write(s2root) == EXIT_CANNOT_EVALUATE)
        stroot = new_root(); write_toml(stroot, "manifest.toml", manifest); empty_indexes(stroot)
        write_toml(stroot, "backlog_item.index.toml", "schema = true\n")
        check("index-schema-bool-cannot-eval", render_write(stroot) == EXIT_CANNOT_EVALUATE)
        saroot = new_root(); write_toml(saroot, "manifest.toml", manifest); empty_indexes(saroot)
        write_toml(saroot, "backlog_item.index.toml", "record = []\n")
        check("index-schema-absent-cannot-eval", render_write(saroot) == EXIT_CANNOT_EVALUATE)
        ukroot = new_root(); write_toml(ukroot, "manifest.toml", manifest); empty_indexes(ukroot)
        write_toml(ukroot, "backlog_item.index.toml", "schema = 1\nbogus_table = 1\n")
        check("index-unknown-key-cannot-eval", render_write(ukroot) == EXIT_CANNOT_EVALUATE)
        naroot = new_root(); write_toml(naroot, "manifest.toml", manifest); empty_indexes(naroot)
        write_toml(naroot, "backlog_item.index.toml", "schema = 1\nrecord = 3\n")
        check("index-record-not-array-cannot-eval", render_write(naroot) == EXIT_CANNOT_EVALUATE)
        duroot = new_root()
        write_toml(duroot, "manifest.toml",
                   manifest.replace('sources = ["done"]', 'sources = ["done", "done"]'))
        empty_indexes(duroot)
        check("duplicate-source-cannot-eval", render_write(duroot) == EXIT_CANNOT_EVALUATE)

        # --- F-01: a manifest target that does not match the view's spec destination fails closed --------
        troot = new_root()
        write_toml(troot, "manifest.toml",
                   manifest.replace('target = "{}/TODO.md"'.format(WORKING_DIRNAME), 'target = "README.md"'))
        empty_indexes(troot)
        check("target-rebind-cannot-eval", render_write(troot) == EXIT_CANNOT_EVALUATE)
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
        check("symlink-dest-cannot-eval", render_write(symroot) == EXIT_CANNOT_EVALUATE)
        check("symlink-dest-not-followed", outside.read_text(encoding="utf-8") == "original\n")

        # --- F-03: an EXTRA declared source beyond a view's required set fails closed --------------------
        xroot = new_root()
        write_toml(xroot, "manifest.toml",
                   manifest.replace('sources = ["done"]', 'sources = ["done", "finding"]'))
        empty_indexes(xroot)
        check("extra-source-cannot-eval", render_write(xroot) == EXIT_CANNOT_EVALUATE)

        # --- F-03: the same extra declared source, now MALFORMED, also fails closed ----------------------
        xmroot = new_root()
        write_toml(xmroot, "manifest.toml",
                   manifest.replace('sources = ["done"]', 'sources = ["done", "finding"]'))
        empty_indexes(xmroot)
        write_toml(xmroot, "finding.index.toml", "\n".join([
            "schema = 1", _rec("FN-1", "finding", "not-a-state", "bad status")]) + "\n")
        check("extra-source-malformed-cannot-eval", render_write(xmroot) == EXIT_CANNOT_EVALUATE)

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
        check("resolution-fork-cannot-eval", render_write(fkroot) == EXIT_CANNOT_EVALUATE)

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
        check("resolution-cycle-cannot-eval", render_write(cyroot) == EXIT_CANNOT_EVALUATE)

        # --- F-07: deeply nested TOML trips tomllib recursion; mapped to cannot-evaluate, not a crash ---
        rroot = new_root()
        write_toml(rroot, "manifest.toml", manifest)
        empty_indexes(rroot)
        deep = "schema = 1\ndeep = " + "[" * 2000 + "]" * 2000 + "\n"   # nesting safely above the recursion limit
        write_toml(rroot, "finding.index.toml", deep)
        check("deep-toml-recursion-cannot-eval", render_write(rroot) == EXIT_CANNOT_EVALUATE)

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
            "[opf]",
            'standard = "opf"',
            'spec_version = "1.1.0"',
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
        check("forged-field-write-ok", render_write(forgeroot) == EXIT_OK)
        forged_view = (forgeroot / WORKING_DIRNAME / "REFERENCES.md").read_text(encoding="utf-8")
        check("forged-heading-neutralized", "\n# Forged heading" not in body_of(forged_view))
        check("forged-comment-neutralized", "<!-- injected -->" not in forged_view)

        # --- F-05: a per-record store is a CLEAR cannot-evaluate (deferred), not a silent inline render --
        prroot = new_root()
        write_toml(prroot, "manifest.toml", manifest.replace('layout = "inline"', 'layout = "per-record"'))
        empty_indexes(prroot)
        check("per-record-deferred-cannot-eval", render_write(prroot) == EXIT_CANNOT_EVALUATE)

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
        check("class-wide-injection-write-ok", render_write(injroot) == EXIT_OK)

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
                  render_write(_bcroot) == EXIT_CANNOT_EVALUATE)
            check("byte-canon-wrote-nothing-" + _bcls,
                  not (_bcroot / WORKING_DIRNAME / "TODO.md").exists())

        # round-3 (CLASS B positive): a SCHEMA-VALID backlog_item whose title merely ends in a SPACE
        # renders a body line ending in whitespace; _render_resolved now normalizes trailing line
        # whitespace before the byte-canon scan, so the render SUCCEEDS (EXIT_OK) and writes a clean
        # view. Pre round-3 this failed closed at exit 2 over benign trailing whitespace. Modelled on the
        # CLASS B store construction; proves the over-fire fix end to end, not only at a unit level.
        import _byte_canon as check_byte_canon  # authoritative byte-canon leg; pure function over bytes
        _tsroot = new_root()
        write_toml(_tsroot, "manifest.toml", manifest)
        empty_indexes(_tsroot)
        write_toml(_tsroot, "version.toml", _bc_version)
        write_toml(_tsroot, "backlog_item.index.toml", "\n".join([
            "schema = 1",
            _rec("BI-1", "backlog_item", "open", "trailing space title "),
        ]) + "\n")
        check("trailing-space-title-renders",
              render_write(_tsroot) == EXIT_OK)
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
              render_write(_tsnbroot) == EXIT_OK)
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
        check("proposed-store-write-ok", render_write(proproot) == EXIT_OK)

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

        # --- the composed U6 write gate (VC-4): render --write gates on validate_store == VALID ----------
        # A bare `render` (neither --check nor --write) is a usage error; --write on a NOT-ADOPTED root is
        # NOT APPLICABLE (0) and writes nothing (resolution precedes the gate); the CLI parser rejects a
        # dangling/empty/option-looking --root before the mode check. The gate ITSELF is exercised over a
        # whole-store-VALID synthetic store (the U6 clean-store shape, with every view pre-rendered so
        # C-VIEW-DRIFT passes) plus the inert git-derived observations validate_store consumes; --check stays
        # read-only throughout.
        na = base / "not-adopted"
        na.mkdir()
        check("render-requires-a-mode", render(["--root", str(na)]) == EXIT_CANNOT_EVALUATE)
        check("render-both-modes-refused",
              render(["--root", str(na), "--check", "--write"]) == EXIT_CANNOT_EVALUATE)
        check("write-not-adopted-not-applicable", render(["--root", str(na), "--write"]) == EXIT_OK)
        check("write-not-adopted-wrote-nothing", not (na / WORKING_DIRNAME).exists())
        # CLI fail-closed parse (parse precedes the mode check and resolution; na isolates from the gate).
        check("cli-dangling-root", render(["--root", str(na), "--root"]) == EXIT_CANNOT_EVALUATE)
        check("cli-unknown-arg", render(["--root", str(na), "--bogus"]) == EXIT_CANNOT_EVALUATE)
        check("cli-misspelled-check-refused", render(["--root", str(na), "--chek"]) == EXIT_CANNOT_EVALUATE)
        # B3: `--root` requires a non-empty, non-option-looking directory argument (an empty value would
        # normalize to the cwd, an option-looking token is a swallowed next flag); each is a parse-level 2.
        check("cli-empty-root", render(["--root", ""]) == EXIT_CANNOT_EVALUATE)
        check("cli-option-looking-root", render(["--root", "--bogus"]) == EXIT_CANNOT_EVALUATE)

        def _gate_store(mutate=None, declare_version=True):
            """Build a whole-store-VALID synthetic store that ALSO declares and pre-renders every view, plus
            the inert observations validate_store consumes, so the composed U6 write gate is exercised end to
            end. Mirrors the U6 clean-store shape (counters, populated indexes, worklog + archive coverage, a
            two-release version with published freeze digests, and the product VERSION + CHANGELOG) that
            validate_store grades VALID; the views are pre-rendered through the ungated write path so
            C-VIEW-DRIFT passes. `mutate(root, bi)` optionally edits the store AFTER pre-rendering (e.g. to
            inject a duplicate id), yielding a renderable-but-INVALID store whose views are now stale, the
            change-carries-check discriminator. Returns (root, observations). The fixture builders (_opf_emit
            / _opf_changelog / _opf_check) are imported at call time to keep the module-top imports unchanged
            and to avoid _opf_check's top-level back-import into this module."""
            import _opf_emit
            import _opf_changelog
            import _opf_check
            ts = "2026-06-01T00:00:00Z"
            def _wl(n):
                return {"id": "WL-{}".format(n), "date": "2026-06-{:02d}T00:00:00Z".format((n % 27) + 1),
                        "actor": {"kind": "maintainer"}, "kind": "added", "summary": "w{}".format(n)}
            def _env(rid, rtype, status, **over):
                r = {"id": rid, "type": rtype, "status": status, "title": "t", "created_at": ts,
                     "updated_at": ts, "actor": {"kind": "maintainer"}}
                r.update(over)
                return r
            def _bi(n, status):
                return _env("BI-{}".format(n), "backlog_item", status)
            def _dn(n, bid):
                return _env("DN-{}".format(n), "done", "recorded", links=[{"rel": "receipt_of", "id": bid}])
            def _ho(n):
                return _env("HO-{}".format(n), "handoff", "current")
            roster = (
                ("TODO.md", "composed", ["backlog_item", "block"]),
                ("BACKLOG.md", "composed", ["backlog_item", "block"]),
                ("PIPELINE.md", "composed", ["backlog_item", "block"]),
                ("DONE.md", "composed", ["done"]),
                ("FINDINGS.md", "composed", ["finding"]),
                ("DECISIONS.md", "composed",
                 ["pending_decision", "autonomous_decision", "maintainer_decision", "preference_pattern"]),
                ("CONTRIBUTIONS.md", "composed", ["contribution"]),
                ("DECISIONS.toml", "projection",
                 ["pending_decision", "autonomous_decision", "maintainer_decision", "preference_pattern"]),
                ("BLOCKS.md", "composed", ["block"]),
                ("HANDOFF.md", "composed", ["handoff"]),
                ("REFERENCES.md", "composed", ["reference"]),
                ("WORKLOG.md", "deterministic", ["worklog"]),
                ("VERSION.md", "deterministic", ["version"]),
                ("VERSION", "deterministic", ["version"]),
                ("BACKLOG_ITEM-INDEX.md", "deterministic", ["backlog_item"]),
                ("BLOCK-INDEX.md", "deterministic", ["block"]),
            )
            if not declare_version:
                # Drop the root VERSION deliverable from the declared views so C-VERSION-FILE is UNOWNED (render
                # never plans or regenerates it); the caller supplies a stale root VERSION to exercise the
                # unowned-VERSION residual remedy end to end. VERSION.md (the markdown mirror) stays declared.
                roster = tuple(r for r in roster if r[0] != "VERSION")
            views = {nm: {"kind": kd, "sources": list(srcs),
                          "target": nm if nm == "VERSION" else "{}/{}".format(WORKING_DIRNAME, nm)}
                     for nm, kd, srcs in roster}
            types = {t: {"namespace": ns} for t, ns in (
                ("backlog_item", "BI"), ("done", "DN"), ("worklog", "WL"), ("finding", "FN"),
                ("pending_decision", "PD"), ("handoff", "HO"), ("reference", "RF"),
                ("autonomous_decision", "AD"), ("block", "BL"),
                ("contribution", "CN"), ("maintainer_decision", "MD"), ("preference_pattern", "PP"))}
            man = {"opf": {"standard": "opf", "spec_version": "1.1.0", "layout": "inline",
                                  "posture": "required", "import_status": "none"},
                   "store": {"sync_target": ""}, "types": types, "vendors": {"registered": []},
                   "archive": {"period": "year"}, "views": views}
            cnt = {"schema": 1, "counters": {"BI": 2, "DN": 1, "WL": 4, "FN": 0, "PD": 0, "AD": 0,
                                             "BL": 0, "HO": 1, "RF": 0, "CN": 0, "MD": 0, "PP": 0}}
            full_wl = {n: _wl(n) for n in (1, 2, 3, 4)}
            d12 = _opf_release.compute_span_digest(full_wl, (1, 2))
            d34 = _opf_release.compute_span_digest(full_wl, (3, 4))
            def _rr(v, a, b, dig):
                return {"version": v, "date": ts, "worklog_span": ["WL-{}".format(a), "WL-{}".format(b)],
                        "coverage_digest": dig}
            cl = "\n".join(["# Changelog", "", "## unreleased", "", "## 1.1.0", "", "- 1.1.0 notes", "",
                            "## 1.0.0", "", "- 1.0.0 notes", ""]) + "\n"
            entries, _f = _opf_changelog._changelog_entries(cl)
            emap = {}
            for tok, text in entries:
                emap.setdefault(tok, []).append(text)
            digs = {tok: _opf_changelog.freeze_digest(emap[tok][0]) for tok in ("1.1.0", "1.0.0")}
            ver = {"schema": 1, "release": [_rr("1.0.0", 1, 2, d12), _rr("1.1.0", 3, 4, d34)],
                   "summary": [{"covers": "1.0.0", "status": "published", "digest": digs["1.0.0"]},
                               {"covers": "1.1.0", "status": "published", "digest": digs["1.1.0"]},
                               {"covers": "unreleased", "status": "working"}]}
            machine = {
                "manifest.toml": man, "counters.toml": cnt,
                "backlog_item.index.toml": {"schema": 1, "record": [_bi(1, "done"), _bi(2, "open")]},
                "done.index.toml": {"schema": 1, "record": [_dn(1, "BI-1")]},
                "finding.index.toml": {"schema": 1, "record": []},
                "pending_decision.index.toml": {"schema": 1, "record": []},
                "handoff.index.toml": {"schema": 1, "record": [_ho(1)]},
                "reference.index.toml": {"schema": 1, "record": []},
                "autonomous_decision.index.toml": {"schema": 1, "record": []},
                "block.index.toml": {"schema": 1, "record": []},
                "contribution.index.toml": {"schema": 1, "record": []},
                "maintainer_decision.index.toml": {"schema": 1, "record": []},
                "preference_pattern.index.toml": {"schema": 1, "record": []},
                "worklog.toml": {"schema": 1, "entry": [_wl(3), _wl(4)]}, "version.toml": ver,
                "archive/2026/archive.toml": {"schema": 1, "moved": [], "worklog_moved": [
                    {"span": ["WL-1", "WL-2"], "destination": "archive/2026/worklog.toml"}]},
                "archive/2026/worklog.toml": {"schema": 1, "entry": [_wl(1), _wl(2)]}}
            root = new_root()
            for rel, doc in machine.items():
                p = root / WORKING_DIRNAME / "toml" / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(_opf_emit.emit(doc), encoding="utf-8")
            (root / "CHANGELOG.md").write_text(cl, encoding="utf-8")
            render_write(root)   # pre-render every view (incl. the product VERSION) so C-VIEW-DRIFT passes
            obs = {"tracked": "tracked",
                   "prior": {"releases": [_rr("1.0.0", 1, 2, d12), _rr("1.1.0", 3, 4, d34)],
                             "counters_high": cnt["counters"],
                             "records": {"BI-1": ("backlog_item", "done"), "BI-2": ("backlog_item", "open"),
                                         "DN-1": ("done", "recorded"), "HO-1": ("handoff", "current")},
                             "digests": {"DN-1": _opf_check._record_digest(_dn(1, "BI-1"))}}}
            if mutate is not None:
                mutate(root, _bi)
            return root, obs

        # A VALID committed store: --write is permitted (byte-stable over the already-rendered views) and a
        # following --check is clean (0 -> writes -> 0).
        gvroot, gvobs = _gate_store()
        check("gate-valid-store-write-ok",
              render(["--root", str(gvroot), "--write"], observations=gvobs) == EXIT_OK)
        check("gate-valid-store-check-clean",
              render(["--root", str(gvroot), "--check"]) == EXIT_OK)
        # An untracked observation flips the SAME store to INVALID (C-TRACKED): --write is refused (exit 2).
        check("gate-untracked-invalid-write-refused",
              render(["--root", str(gvroot), "--write"],
                     observations=dict(gvobs, tracked="untracked")) == EXIT_CANNOT_EVALUATE)
        # No observations -> the topology/history anchors CANNOT-EVALUATE -> --write refused (exit 2).
        check("gate-no-observations-write-refused",
              render(["--root", str(gvroot), "--write"], observations=None) == EXIT_CANNOT_EVALUATE)

        # change-carries-check: a duplicate id injected AFTER the views were pre-rendered makes the store
        # INVALID (U6 C-ID-SPACE) with its views now STALE. render --write must REFUSE (exit 2) and leave the
        # target bytes UNCHANGED. Were the gate bypassed, render would re-render and REWRITE TODO.md to
        # include the duplicate, so this vector flips (wrote-something) without the composed gate.
        def _inject_dup(root, bi):
            import _opf_emit
            (root / WORKING_DIRNAME / "toml" / "backlog_item.index.toml").write_text(
                _opf_emit.emit({"schema": 1, "record": [bi(1, "done"), bi(2, "open"), bi(2, "open")]}),
                encoding="utf-8")
        gdroot, gdobs = _gate_store(mutate=_inject_dup)
        _dup_todo = gdroot / WORKING_DIRNAME / "TODO.md"
        _dup_before = _dup_todo.read_text(encoding="utf-8")
        check("gate-invalid-duplicate-id-write-refused",
              render(["--root", str(gdroot), "--write"], observations=gdobs) == EXIT_CANNOT_EVALUATE)
        check("gate-invalid-duplicate-id-wrote-nothing",
              _dup_todo.read_text(encoding="utf-8") == _dup_before)

        # --- PR-C: SOURCE-gated REGENERATE. A stale OWNED deliverable (a drifted view, a drifted VERSION, a
        # missing view target) is render's OUTPUT, not a refusal: SOURCE integrity is sound, so --write
        # REGENERATES it and Phase C witnesses the 1->0 transition. The OLD VALID-only gate REFUSED each of
        # these (they are INVALID stores on the deliverable checks); they pass ONLY after the source-gate
        # rework -- the fail-to-pass transition the fix rests on. `_gate_store_drifted(kind)` corrupts ONE
        # owned deliverable AFTER the pre-render so exactly that owned drift check fails while source integrity
        # stays sound.
        def _gate_store_drifted(kind):
            root, obs = _gate_store()
            if kind == "view":
                (root / WORKING_DIRNAME / "TODO.md").write_text("stale sentinel\n", encoding="utf-8")
            elif kind == "version":
                (root / "VERSION").write_text("0.0.0\n", encoding="utf-8")
            elif kind == "missing":
                (root / WORKING_DIRNAME / "TODO.md").unlink()
            else:
                raise AssertionError("unknown drift kind {!r}".format(kind))
            return root, obs

        for _kind, _target, _expect in (("view", WORKING_DIRNAME + "/TODO.md", None),
                                        ("version", "VERSION", "1.1.0\n"),
                                        ("missing", WORKING_DIRNAME + "/TODO.md", None)):
            _droot, _dobs = _gate_store_drifted(_kind)
            _tp = _droot / _target
            _before = _tp.read_text(encoding="utf-8") if _tp.exists() else None
            _cl_before = (_droot / "CHANGELOG.md").read_text(encoding="utf-8")
            check("prc-drifted-{}-write-ok".format(_kind),
                  render(["--root", str(_droot), "--write"], observations=_dobs) == EXIT_OK)
            _after = _tp.read_text(encoding="utf-8") if _tp.exists() else None
            check("prc-drifted-{}-regenerated".format(_kind), _after is not None and _after != _before)
            if _expect is not None:
                check("prc-drifted-{}-exact-bytes".format(_kind), _after == _expect)
            # A following --check is CLEAN: the witnessed 1->0 transition (change-carries-check).
            check("prc-drifted-{}-check-clean".format(_kind),
                  render(["--root", str(_droot), "--check"]) == EXIT_OK)
            # Whole-target-set witness (completeness): CHANGELOG.md is AUTHORED, byte-stable across the
            # regenerate; VERSION is regenerated to the latest release's exact bytes.
            check("prc-drifted-{}-changelog-untouched".format(_kind),
                  (_droot / "CHANGELOG.md").read_text(encoding="utf-8") == _cl_before)
            check("prc-drifted-{}-version-current".format(_kind),
                  (_droot / "VERSION").read_text(encoding="utf-8") == "1.1.0\n")

        # --- non-hostage: an AUTHORED changelog defect NEVER holds the generated deliverables hostage -------
        # A drifted view PLUS a changelog with a published release heading removed: --write REGENERATES the
        # view (owned) and exits 1 (EXIT_DRIFT) on the residual authored FINDING, leaving CHANGELOG.md bytes
        # UNTOUCHED; doctor stays INVALID solely on C-CHANGELOG-GATES.
        _nh_root, _nh_obs = _gate_store_drifted("view")
        _nh_todo = _nh_root / WORKING_DIRNAME / "TODO.md"
        (_nh_root / "CHANGELOG.md").write_text(
            "\n".join(["# Changelog", "", "## unreleased", "", "## 1.1.0", "", "- 1.1.0 notes", ""]) + "\n",
            encoding="utf-8")   # the published 1.0.0 section removed -> a C-CHANGELOG-GATES FINDING
        _nh_cl_before = (_nh_root / "CHANGELOG.md").read_text(encoding="utf-8")
        check("prc-nonhostage-finding-exit-drift",
              render(["--root", str(_nh_root), "--write"], observations=_nh_obs) == EXIT_DRIFT)
        check("prc-nonhostage-view-regenerated",
              _nh_todo.read_text(encoding="utf-8") != "stale sentinel\n")
        check("prc-nonhostage-changelog-untouched",
              (_nh_root / "CHANGELOG.md").read_text(encoding="utf-8") == _nh_cl_before)

        # A drifted view PLUS a NON-UTF-8 CHANGELOG: --write REGENERATES the view and exits 2 naming the
        # unevaluable input, leaving the changelog bytes untouched (SETTLED: regenerate-and-exit-2, never a
        # whole-write refusal).
        _nu_root2, _nu_obs = _gate_store_drifted("view")
        _nu_todo = _nu_root2 / WORKING_DIRNAME / "TODO.md"
        (_nu_root2 / "CHANGELOG.md").write_bytes(b"# Changelog\n\xff\xfe not utf-8\n")
        _nu_cl_before = (_nu_root2 / "CHANGELOG.md").read_bytes()
        check("prc-nonhostage-cannot-eval-exit-2",
              render(["--root", str(_nu_root2), "--write"], observations=_nu_obs) == EXIT_CANNOT_EVALUATE)
        check("prc-nonhostage-cannot-eval-view-regenerated",
              _nu_todo.read_text(encoding="utf-8") != "stale sentinel\n")
        check("prc-nonhostage-cannot-eval-changelog-untouched",
              (_nu_root2 / "CHANGELOG.md").read_bytes() == _nu_cl_before)

        # --- fail-safe: the _write_contained backstop refuses ANY mutating write when the gate flag is
        # cleared, source-sound or not (defence-in-depth-default). On a drifted store with the flag False,
        # --write exits 2 (Phase B's first write raises before touching any byte) and every target is
        # byte-unchanged; the flag is restored in a finally.
        _fs_root, _fs_obs = _gate_store_drifted("view")
        _fs_todo = _fs_root / WORKING_DIRNAME / "TODO.md"
        _fs_before = _fs_todo.read_text(encoding="utf-8")
        _saved_flag = _WRITE_GATE_COMPOSED
        try:
            globals()["_WRITE_GATE_COMPOSED"] = False
            check("prc-uncomposed-gate-write-refused",
                  render(["--root", str(_fs_root), "--write"], observations=_fs_obs) == EXIT_CANNOT_EVALUATE)
            check("prc-uncomposed-gate-wrote-nothing",
                  _fs_todo.read_text(encoding="utf-8") == _fs_before)
        finally:
            globals()["_WRITE_GATE_COMPOSED"] = _saved_flag

        # --- VECTOR A (change-carries-check): EXERCISE the Phase-C owned-miss ROLLBACK + corrected diagnostic.
        # The natural PR-C vectors above never reach owned-miss (a source-sound store regenerates cleanly and
        # Phase C witnesses 1->0), so this seam monkeypatches _opf_check.validate_store to FORCE a PRE=valid /
        # POST=owned-miss disagreement over an ALREADY-rewritten target. Phase B is UNAFFECTED (it renders
        # directly and calls no validate_store), so the write really happens and the preimage is really rolled
        # back. Three variants prove: exit 2 always; the OWNED-deliverable miss is labelled and rolled back with
        # a truthful success claim; a POST SOURCE-integrity regression is labelled a SOURCE regression printing
        # the source reason (DEFECT 1b); and a PARTIAL rollback failure states the rollback was INCOMPLETE and
        # names the paths in the PRIMARY line, never claiming plain success (DEFECT 1a). FAIL-TO-PASS: the
        # pre-fix branch prints the UNCONDITIONAL "rolled back to the pre-write bytes" and only the owned
        # C-VIEW-DRIFT / C-VERSION-FILE messages, so the source-regression and partial-failure assertions FAIL
        # on the old wording and PASS after DEFECT 1.
        import io
        import contextlib
        import _opf_check as _oc_seam

        class _FakeResult(object):
            def __init__(self, checks, by_check=None, unattributed=None):
                self.checks = checks
                self.by_check = by_check or {}
                self.unattributed = unattributed or []

        def _all_pass():
            return {cid: "PASS" for cid in _oc_seam.REQUIRED_CHECKS}

        def _seam_render(root, obs, post_result):
            """Force PRE=valid (source-sound, so Phase A proceeds) and POST=post_result across render's two
            validate_store calls, capturing stderr. Restores validate_store in a finally. source_integrity_ok
            is NOT patched: it reads the fake result's real .checks/.unattributed."""
            calls = [0]
            def _fake_validate(resolution, supported_profiles=None, *, observations=None):
                calls[0] += 1
                return _FakeResult(_all_pass()) if calls[0] == 1 else post_result
            _orig = _oc_seam.validate_store
            _oc_seam.validate_store = _fake_validate
            buf = io.StringIO()
            try:
                with contextlib.redirect_stderr(buf):
                    rc = render(["--root", str(root), "--write"], observations=obs)
            finally:
                _oc_seam.validate_store = _orig
            return rc, buf.getvalue()

        # Variant 1: an OWNED-deliverable disagreement (C-VIEW-DRIFT != PASS) over a rewritten target -> full
        # rollback. TODO.md == "stale sentinel\n" pre-write; Phase B regenerates it; Phase C rolls it back.
        _oa_root, _oa_obs = _gate_store_drifted("view")
        _oa_todo = _oa_root / WORKING_DIRNAME / "TODO.md"
        _oa_pre = _oa_todo.read_text(encoding="utf-8")
        _oa_post = _FakeResult(dict(_all_pass(), **{"C-VIEW-DRIFT": "FINDING"}),
                               by_check={"C-VIEW-DRIFT": ["TODO.md drifted from source"]})
        _oa_rc, _oa_err = _seam_render(_oa_root, _oa_obs, _oa_post)
        check("prc-ownedmiss-owned-exit-2", _oa_rc == EXIT_CANNOT_EVALUATE)
        check("prc-ownedmiss-owned-preimage-restored", _oa_todo.read_text(encoding="utf-8") == _oa_pre)
        check("prc-ownedmiss-owned-labelled-owned", "OWNED deliverable" in _oa_err)
        check("prc-ownedmiss-owned-rollback-success", "rolled back to the pre-write bytes" in _oa_err)

        # Variant 2: a POST SOURCE-INTEGRITY regression (a source check FINDING) must be LABELLED a source
        # regression and print the source reason, NOT mislabelled "OWNED deliverable" (DEFECT 1b).
        _sa_root, _sa_obs = _gate_store_drifted("view")
        _sa_todo = _sa_root / WORKING_DIRNAME / "TODO.md"
        _sa_pre = _sa_todo.read_text(encoding="utf-8")
        _sa_post = _FakeResult(dict(_all_pass(), **{"C-RECORDS": "FINDING"}),
                               by_check={"C-RECORDS": ["post-write record fault"]})
        _sa_rc, _sa_err = _seam_render(_sa_root, _sa_obs, _sa_post)
        check("prc-ownedmiss-source-exit-2", _sa_rc == EXIT_CANNOT_EVALUATE)
        check("prc-ownedmiss-source-preimage-restored", _sa_todo.read_text(encoding="utf-8") == _sa_pre)
        check("prc-ownedmiss-source-labelled-source", "SOURCE-INTEGRITY regression" in _sa_err)
        check("prc-ownedmiss-source-reason-printed", "post-write record fault" in _sa_err)
        check("prc-ownedmiss-source-not-mislabelled-owned", "OWNED deliverable" not in _sa_err)

        # Variant 3: a PARTIAL rollback failure (DEFECT 1a). Stub _restore_preimages (module-global, called
        # unqualified by render) to report an unrestored target; the corrected primary statement must say the
        # rollback was INCOMPLETE and name the path, and must NOT claim plain success.
        _pa_post = _FakeResult(dict(_all_pass(), **{"C-VIEW-DRIFT": "FINDING"}),
                               by_check={"C-VIEW-DRIFT": ["TODO.md drifted from source"]})
        _pa_unrestored = WORKING_DIRNAME + "/TODO.md"
        _saved_restore = globals()["_restore_preimages"]
        try:
            globals()["_restore_preimages"] = lambda *a, **k: [_pa_unrestored]
            _pa_root, _pa_obs = _gate_store_drifted("view")
            _pa_rc, _pa_err = _seam_render(_pa_root, _pa_obs, _pa_post)
        finally:
            globals()["_restore_preimages"] = _saved_restore
        check("prc-ownedmiss-partial-exit-2", _pa_rc == EXIT_CANNOT_EVALUATE)
        # DEFECT 1a hardening: the INCOMPLETE label AND the unrestored path must appear in the PRIMARY
        # diagnostic line (the FIRST stderr line of the owned-miss message), not merely somewhere in stderr,
        # so moving the failed-paths text onto a later line would NOT pass. Parse the first line and assert on
        # it; still reject the false unconditional "rolled back" success wording anywhere in stderr.
        _pa_first = _pa_err.splitlines()[0] if _pa_err else ""
        check("prc-ownedmiss-partial-incomplete-labelled", "INCOMPLETE" in _pa_first)
        check("prc-ownedmiss-partial-names-path", _pa_unrestored in _pa_first)
        check("prc-ownedmiss-partial-no-false-success",
              "rolled back to the pre-write bytes" not in _pa_err)

        # --- VECTOR B (change-carries-check): the unowned-VERSION residual remedy. Per the brief's sanctioned
        # alternative, drive _residual_write_exit DIRECTLY with a synthesized POST for an UNOWNED C-VERSION-FILE
        # FINDING (a fully-synthetic unowned-VERSION whole-store is awkward to build from the VERSION-declaring
        # gate fixtures) and capture its stdout. The version ledger HAS releases here, so C-VERSION-FILE REQUIRES
        # a root VERSION and "remove the file" would turn a stale-VERSION finding into a MISSING-VERSION one.
        # Asserts: exit 1 (EXIT_DRIFT); the finding is surfaced; the remedy does NOT carry the harmful
        # unconditional "remove the file" advice; and it still directs to declaring the view. The mapper makes
        # NO filesystem write, so render deletes nothing. FAIL-TO-PASS: the pre-fix wording contains "remove the
        # file", so the negative assertion FAILS now and PASSES after DEFECT 2.
        _rv_msg = "root VERSION 0.0.0 is stale; the version ledger's latest release is 1.1.0"
        _rv_post = _FakeResult({"C-VERSION-FILE": "FINDING", "C-CHANGELOG-GATES": "PASS"},
                               by_check={"C-VERSION-FILE": [_rv_msg]})
        _rv_buf = io.StringIO()
        with contextlib.redirect_stdout(_rv_buf):
            _rv_rc = _residual_write_exit(_rv_post, set())   # "VERSION" NOT planned -> the unowned branch
        _rv_out = _rv_buf.getvalue()
        check("prc-unowned-version-residual-exit-drift", _rv_rc == EXIT_DRIFT)
        check("prc-unowned-version-residual-finding-surfaced", _rv_msg in _rv_out)
        check("prc-unowned-version-residual-no-remove-advice", "remove the file" not in _rv_out)
        check("prc-unowned-version-residual-declare-advice", "declare the VERSION view" in _rv_out)

        # --- VECTOR B (END-TO-END): the unowned-VERSION residual remedy driven through the REAL caller, not a
        # synthesized _residual_write_exit call. Build a gate store that does NOT declare the root VERSION as a
        # view (so C-VERSION-FILE is UNOWNED) with the version ledger carrying releases, a STALE root VERSION on
        # disk, and a drifted OWNED view (TODO). render --write with honest observations exercises ownership
        # discovery, real validation, Phase-B REGENERATION of the owned view, Phase-C owned_ok (VERSION not
        # planned), and the caller's _residual_write_exit mapping the unowned C-VERSION-FILE FINDING to
        # EXIT_DRIFT. Asserts: exit 1 (EXIT_DRIFT); the owned view REGENERATED (no longer the stale sentinel);
        # the root VERSION still EXISTS and is byte-UNCHANGED (render owns and deletes nothing there); and the
        # remedy carries the corrected QUALIFIED wording, never the harmful unconditional "remove the file".
        # FAIL-TO-PASS: the pre-fix remedy carried "or remove the file", so the negative assertion FAILS then.
        _ev_root, _ev_obs = _gate_store(declare_version=False)
        _ev_todo = _ev_root / WORKING_DIRNAME / "TODO.md"
        _ev_todo.write_text("stale sentinel\n", encoding="utf-8")     # drift the OWNED view
        _ev_version = _ev_root / "VERSION"
        _ev_version.write_text("0.0.0\n", encoding="utf-8")           # a STALE, UNDECLARED root VERSION
        _ev_ver_before = _ev_version.read_bytes()
        _ev_buf = io.StringIO()
        with contextlib.redirect_stdout(_ev_buf):
            _ev_rc = render(["--root", str(_ev_root), "--write"], observations=_ev_obs)
        _ev_out = _ev_buf.getvalue()
        check("prc-unowned-version-e2e-exit-drift", _ev_rc == EXIT_DRIFT)
        check("prc-unowned-version-e2e-view-regenerated",
              _ev_todo.read_text(encoding="utf-8") != "stale sentinel\n")
        check("prc-unowned-version-e2e-root-version-exists", _ev_version.exists())
        check("prc-unowned-version-e2e-root-version-unchanged",
              _ev_version.read_bytes() == _ev_ver_before)
        check("prc-unowned-version-e2e-no-remove-advice", "remove the file" not in _ev_out)
        check("prc-unowned-version-e2e-qualified-remedy",
              "removing the root VERSION is valid ONLY when the version ledger has no releases" in _ev_out)
        check("prc-unowned-version-e2e-declare-advice", "declare the VERSION view" in _ev_out)

        # --- VECTOR C (change-carries-check): the reopen-failure path of _restore_preimages must return the
        # AFFECTED relpaths of EVERY captured target, not a single opaque sentinel (DEFECT-1c). Force the
        # rollback reopen to fail by monkeypatching _opf_store._open_store_root_fd to raise OSError, then call
        # _restore_preimages with a preimages dict of 2+ entries across DIFFERENT scopes/relpaths. Assert it
        # returns WITHOUT raising and the returned list NAMES both affected relpaths. The monkeypatch is
        # restored in a finally. FAIL-TO-PASS: reverting _restore_preimages to a single generic token loses the
        # relpaths, so both name assertions FAIL.
        _rp_root, _ = _gate_store()
        _rp_res = _opf_store.resolve_store(Path(os.path.abspath(str(_rp_root))))
        _rp_preimages = {("product", "VERSION"): None,
                         ("store", WORKING_DIRNAME + "/TODO.md"): b"stale todo\n"}
        def _rp_boom(*a, **k):
            raise OSError("forced reopen failure for rollback")
        _rp_saved = _opf_store._open_store_root_fd
        _rp_raised = False
        try:
            _opf_store._open_store_root_fd = _rp_boom
            try:
                _rp_failed = _restore_preimages(_rp_root, _rp_res, _rp_preimages)
            except Exception:
                _rp_raised = True
                _rp_failed = []
        finally:
            _opf_store._open_store_root_fd = _rp_saved
        check("prc-reopen-fail-no-exception", _rp_raised is False)
        check("prc-reopen-fail-names-version", "VERSION" in _rp_failed)
        check("prc-reopen-fail-names-todo", (WORKING_DIRNAME + "/TODO.md") in _rp_failed)

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
          "VERSION, the empty-VERSION-ledger fail-closed, the CLI fail-closed parse, and the PR-C source-"
          "gated write path (render --write refuses a SOURCE-integrity violation writing nothing, but "
          "REGENERATES a drifted OWNED deliverable -- a drifted view, a drifted VERSION, a missing target -- "
          "and witnesses the 1->0 transition; an authored CHANGELOG finding exits 1 and a changelog cannot-"
          "evaluate exits 2, each with a manual remedy and never a re-run hint; the source/deliverable "
          "partition and attribution predicate gate; the _write_contained backstop refuses any mutating "
          "write when the gate flag is cleared; --check unaffected)".format(checked[0]))
    return EXIT_OK


# --- self-test fixture builders (kept below the product code) ----------------------------------------

def _view(name, kind, sources):
    src = ", ".join('"{}"'.format(s) for s in sources)
    return '[views."{}"]\nkind = "{}"\nsources = [{}]\ntarget = "{}"\n'.format(
        name, kind, src, name if name == "VERSION" else "{}/{}".format(WORKING_DIRNAME, name))


def _rec(rid, rtype, status, title, *, actor="maintainer", severity=None, decision=None, decided_at=None,
         decided_by=None, classification=None, action=None, scopes=None, links=None, refs=None,
         context=None, rationale=None, recipient=None, dedup_class=None, content_digest=None, delivery=None):
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
    for _k, _v in (("context", context), ("rationale", rationale), ("recipient", recipient),
                   ("dedup_class", dedup_class), ("content_digest", content_digest)):
        if _v is not None:
            lines.append('{} = "{}"'.format(_k, _v))
    if delivery is not None:
        lines.append("delivery = {{ {} }}".format(
            ", ".join('{} = "{}"'.format(k, delivery[k]) for k in delivery)))
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
    # Until render COMPOSES the U6 store-integrity write-gate (VC-4), this module entry runs CHECK-only
    # (drift detection, never a write): a write requires that composition, so default the command line to
    # --check. validate_store itself EXISTS (U6); it is render's write-gate composition that is deferred. A
    # write invocation through any other entry still fails closed in render(). Appending
    # --check is a deliberate default, not a parse relaxation; the F10 parser still refuses any
    # unrecognized token alongside it.
    if "--check" not in _argv:
        _argv = _argv + ["--check"]
    sys.exit(render(_argv))
