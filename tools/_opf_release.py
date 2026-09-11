#!/usr/bin/env python3
"""OPF (DevProcess) release triad: version.toml + worklog.toml + span tiling + coverage digests (U3).

Offline, stdlib only, fail-closed. This is the DELICATE release-triad manager the later OPF units
(changelog gates U5, doctor U6) build on. It validates the two machine ledgers of the release triad and
implements the RELEASE CUT that freezes the unreleased worklog tail into a released span. It extends the
conventions of U1 (`_opf_store`: the VALID/INVALID/CANNOT-EVALUATE outcome model, contained fail-closed
reads) and U2 (`_opf_schema`: the record envelope, the reduced worklog entry, the `<NS>-<n>` id grammar,
the RFC 3339 UTC timestamp validator) rather than duplicating them; those primitives are imported.

Four things live here, all from OPF-SPEC.md sections 6 and 7 (and the round-8 terminality model of 8.4):

  1. version.toml, THE VERSION AND RELEASE LEDGER (spec 6.1). A `schema` marker plus append-only,
     immutable `[[release]]` rows (version / date / worklog_span / coverage_digest) and `[[summary]]`
     rows (covers / status / digest / superseded_by) that back the curated changelog. Release versions
     are SemVer, unique, and MONOTONIC in row order; spans are CONTIGUOUS and NON-OVERLAPPING in ID
     order so the released worklog tiles exactly and the unreleased tail is everything after the last
     span (spec 6.1).

  2. worklog.toml, THE DURABLE OPERATIONAL RECORD (spec 6.2). A `schema` marker plus append-only
     `[[entry]]` rows, each a reduced-envelope `worklog` record validated through U2's `validate_record`.
     The worklog is durable and MUTABLE-UNTIL-RELEASE: an unreleased entry (in the tail) is pre-terminal
     and may be corrected in place; once a release freezes its span the entry is terminal and immutable;
     no entry is ever deleted (spec 6.2, 8.4, 13).

  3. THE COVERAGE-DIGEST CANONICALIZATION (spec 6.1). Spec 6.1 leaves the exact canonicalization "to the
     schema release that follows this specification"; THIS unit DEFINES it (scheme `opf-worklog-coverage-v1`,
     the ambiguity note below): a deterministic digest over the FULL content of the covered worklog
     entries, taken in ID order, with keys canonically ordered so the digest is reorder-invariant across
     the entry array and within every table. Freezing a span records this digest; a later edit to a
     frozen entry, or an append into a released span, changes the recomputed digest and is a gate failure.

  4. THE RELEASE CUT (spec 6.1, 6.2, 8.4, round-8 terminality). Freezing the current unreleased worklog
     span into a released span keyed to a new version: it computes the tail (`released_end`+1 .. the last
     worklog id), records a new `[[release]]` row (new version, date, the tail span, the tail's coverage
     digest), and leaves the store so the new unreleased tail is EMPTY. The cut is a PURE function over
     inert data (workers-produce-inert-data): it returns a NEW version.toml value and never mutates the
     worklog or deletes anything. It fails closed on any inconsistent state (a non-monotonic new version,
     a ledger that does not already tile, a gap in the tail, a duplicate version).

Enforcing the round-8 rules exactly (spec 8.4, 6.2, 12, 13):
  - An unreleased worklog entry is PRE-TERMINAL and mutable; a released span is FROZEN and immutable and
    is never rewritten (`check_frozen_coverage`, `check_no_append_into_released`).
  - NOTHING is ever deleted (`check_no_deletion`): an id present before is present after, in active or
    archive.
  - Rotation of released spans is ARCHIVAL MOVEMENT only (`check_ids_partition`,
    `check_rotation_only_released`): every id exists in exactly one active-or-archive location, and only
    released, frozen spans may rotate, never the mutable unreleased tail (spec 12).

Every malformed, illegal, or unidentifiable input fails closed to INVALID or CANNOT-EVALUATE, never a
silent VALID (spec 3 "Fail closed"; the check-fails-closed-on-unreadable rule: a present-but-unparseable
ledger is a refusing failure that names the fault, never silent absence).

Reference-tooling / spec ambiguities recorded for the finalizer (each resolved the strict, fail-closed
way and named so the choice is reviewable, per disclose-guard-residuals). The spec's own version.toml is
"illustrative"; this unit is the schema release that follows it for the release triad, and DEFINES the
following where sections 6 and 7 leave a gap:
  - THE COVERAGE-DIGEST CANONICALIZATION is defined here as scheme `opf-worklog-coverage-v1`: the literal
    header line `opf-worklog-coverage-v1`, then, for the covered entries in ascending WL-number order,
    one canonical serialization per entry, newline-separated; the digest is `sha256:` + the hex SHA-256
    of the UTF-8 bytes. Each entry is serialized recursively with every table's keys in sorted order and
    every array in its given order, so the digest is invariant to reordering the entry array or the keys
    within a table but sensitive to any content change. An unexpected value type (only str / int / bool /
    float / list / table are expected in a validated worklog entry) fails the canonicalization CLOSED
    (ReleaseError), never a silent digest over a lossy serialization. An EMPTY span digests the header alone, a
    well-defined constant (spec 6.1 permits an empty span for a release with no worklog entries; spec 14.3
    pre-migration releases). The finalizer may re-fix the scheme in one place if adopters need another.
  - version.toml / worklog.toml FILE SHAPE is defined here as an optional top-level `schema` int plus the
    `[[release]]` / `[[summary]]` (version.toml) or `[[entry]]` (worklog.toml) arrays of tables, matching
    Appendix B/C; unknown top-level keys and unknown row keys are fail-closed findings (closed keyset,
    spec 8.3 discipline applied to the ledgers). SemVer is validated to SemVer 2.0.0 precedence including
    pre-release ordering; build metadata is accepted and ignored for precedence, per SemVer 2.0.0.
  - SPAN TILING is defined here as: the non-empty release spans, taken in release-row order, start at WL-1
    and each next non-empty span starts at the previous span's end + 1 (contiguous, non-overlapping, in ID
    order, spec 6.1); an empty span (a release with no new worklog entries, spec 6.1 / 14.3) contributes
    no coverage and does not advance the tiling cursor, so it may sit anywhere. A gap, an overlap, a
    non-monotone or malformed span, or a first non-empty span not starting at WL-1 is a fail-closed finding.
  - SUMMARY-ROW validation here is STRUCTURAL: covers token parses (a version, an `a..b` range over
    contiguous released versions in ledger order, or `unreleased`) and refers only to ledger versions;
    status is one of working / published / superseded; `digest` is required and well-formed once published
    or superseded; `superseded_by` is present exactly when superseded and is itself a well-formed covers
    token; `unreleased` carries status working. The FACTS gates that tile the ledger exactly and match
    CHANGELOG.md headings 1:1 (spec 7.1) and recompute freeze digests (spec 7.2) are U5's changelog gates,
    which build on these carriers; the boundary is called out so U5 does not re-derive it.
  - THE REQUIRED-BUMP computation (the minimum version bump for a change, spec 6.1) is explicitly OUT OF
    SCOPE here: spec 6.1 names it "a consumer of the ledger, not part of the base standard's definition".
    This unit is that ledger; the release-delta consumer is not built here (build-plan U3 does not assign
    it), and is noted for the finalizer rather than stubbed.
"""
import hashlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
# U1 supplies the outcome model; U2 supplies the reduced-worklog record validator, the id-shape helper,
# and the RFC 3339 UTC timestamp validator. Reuse rather than re-declare (single source of truth).
from _opf_store import (  # noqa: E402
    VALID, INVALID, CANNOT_EVALUATE, _is_item_collection, _sorted_key_names, _safe_display, _safe_str,
)
from _opf_schema import (  # noqa: E402
    validate_record, _valid_id_shape, _valid_timestamp, SUPPORTED_SCHEMA,
)
# U8 supplies the deterministic float SPELLING rule; reuse it so the coverage digest and the emitter agree
# byte-for-byte on floats (M2), rather than re-deriving the signed-zero / non-finite handling here.
from _opf_emit import _canonical_float, EmitError  # noqa: E402


WL_NAMESPACE = "WL"                       # the worklog type's namespace (spec 8.1)

# version.toml / worklog.toml layout (defined here; see the file-shape ambiguity note).
VERSION_TOP_KEYS = frozenset({"schema", "release", "summary"})
WORKLOG_TOP_KEYS = frozenset({"schema", "entry"})
RELEASE_KEYS = frozenset({"version", "date", "worklog_span", "coverage_digest"})
SUMMARY_KEYS = frozenset({"covers", "status", "digest", "superseded_by"})

SUMMARY_STATUSES = ("working", "published", "superseded")
UNRELEASED = "unreleased"                 # the reserved covers token for the working tail (spec 6.1)

# A coverage / freeze digest is `sha256:` + 64 lowercase hex (the form of every spec example, Appendix B).
# Anchored with \Z (the true end of string), not $, which also matches just before a trailing newline and
# would admit a newline-terminated digest read from a store file (fail-open on the format gate).
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}\Z")

# The canonicalization scheme header (see the coverage-digest ambiguity note). Versioned so a future
# scheme is distinguishable from this one by construction.
COVERAGE_SCHEME = "opf-worklog-coverage-v1"

# The maximum table/array nesting depth _canonical will recurse through (defence-in-depth; see FIX 2 in
# _canonical). A real worklog entry nests only a handful of levels (an extension table, a links/refs
# array of tables), so this bound is generous by orders of magnitude, yet it sits far below the
# interpreter's default recursion limit so a controlled ReleaseError always fires before an uncontrolled
# RecursionError. It changes no output for any real (shallow) input.
_MAX_CANONICAL_DEPTH = 100

# SemVer 2.0.0 core + optional -prerelease + optional +build (build ignored for precedence).
_SEMVER_RE = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-((?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*)"
    r"(?:\.(?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*))*))?"
    # \Z, not $: $ also matches before a trailing newline, admitting a newline-terminated version string.
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?\Z")


class ReleaseError(Exception):
    """A release-triad canonicalization cannot be completed (an unexpected value type in a worklog
    entry). Callers map it to a CANNOT-EVALUATE / fail-closed outcome, never a silent digest."""


# --- small result carriers (the U2 RecordValidation idiom) -------------------------------------------

class VersionValidation:
    __slots__ = ("status", "findings", "releases", "summaries")

    def __init__(self, status, findings=None, releases=None, summaries=None):
        self.status = status              # VALID / INVALID / CANNOT_EVALUATE
        self.findings = findings or []
        self.releases = releases or []    # the parsed [[release]] rows, in file order
        self.summaries = summaries or []  # the parsed [[summary]] rows, in file order


class WorklogValidation:
    __slots__ = ("status", "findings", "entry_ids")

    def __init__(self, status, findings=None, entry_ids=None):
        self.status = status              # VALID / INVALID / CANNOT_EVALUATE
        self.findings = findings or []
        self.entry_ids = entry_ids or []  # the WL-numbers of the parsed entries, in file order


class CutResult:
    __slots__ = ("status", "findings", "version_data", "frozen_span", "coverage_digest")

    def __init__(self, status, findings=None, version_data=None, frozen_span=None, coverage_digest=None):
        self.status = status              # VALID (cut computed) / INVALID / CANNOT_EVALUATE
        self.findings = findings or []
        self.version_data = version_data  # the NEW version.toml value with the release row appended
        self.frozen_span = frozen_span    # the ["WL-a", "WL-b"] span the cut froze, or [] for an empty cut
        self.coverage_digest = coverage_digest


# --- SemVer (spec 6.1: SemVer versions, unique and monotonic) ----------------------------------------

def parse_semver(value):
    """A SemVer 2.0.0 precedence key for `value`, or None when it is not a valid SemVer string. The key
    is a tuple comparable so a<b iff a has lower precedence: (major, minor, patch, release_rank,
    prerelease_key). release_rank is 1 for a version with no prerelease and 0 for a prerelease, so a
    release outranks any prerelease of the same core; prerelease identifiers compare numeric-before-
    alphanumeric with numeric parts as ints and a shorter identifier set as lower precedence (SemVer 2.0.0
    para 11). Build metadata is accepted and ignored for precedence."""
    if not isinstance(value, str):
        return None
    m = _SEMVER_RE.match(value)
    if not m:
        return None
    try:
        major, minor, patch = int(m.group(1)), int(m.group(2)), int(m.group(3))
        pre = m.group(4)
        if pre is None:
            return (major, minor, patch, 1, ())
        ids = []
        for ident in pre.split("."):
            if ident.isdigit():
                # A numeric identifier (no leading zero, enforced by the regex) compares as an int, and
                # numeric identifiers always have lower precedence than alphanumeric ones (0 before 1 below).
                ids.append((0, int(ident)))  # a numeric prerelease identifier (oversized -> ValueError below)
            else:
                ids.append((1, ident))
        return (major, minor, patch, 0, tuple(ids))
    except ValueError:
        # An oversized numeric core or numeric prerelease identifier (CPython refuses int() on a string of
        # more than 4300 digits) is a MALFORMED SemVer, not an uncontrolled crash: return None like any
        # non-SemVer input (guard-input-soundness; mirrors check_clauses.split_clause_id's ordinal guard).
        return None


# --- WL id helpers -----------------------------------------------------------------------------------

def _wl_num(value):
    """The positive integer n of a well-formed `WL-<n>` id, or None for anything else (a malformed id or
    a non-worklog namespace)."""
    shape = _valid_id_shape(value)
    if shape is None or shape[0] != WL_NAMESPACE:
        return None
    return shape[1]


def _wl_id_set(values, where, findings):
    """Normalize an id-collection CONTROL to a set of its WL-numbers, FAIL-CLOSED (guard-input-soundness;
    spec 8.2). A bare string, a mapping, a scalar, or None is a malformed collection: a finding and an
    EMPTY set, never a string splatting into characters and never a `for`-crash on a non-iterable. A
    non-WL-number element (a list/dict, a bool, a non-positive int, or a malformed id) is a per-element
    finding, skipped, so set() never raises on an unhashable element and a malformed member never
    clean-passes. Mirrors the int-or-_wl_num element idiom the standalone worklog guards already use."""
    if not _is_item_collection(values):
        findings.append("{}: must be an iterable of WL-numbers, not {}".format(
            where, type(values).__name__))
        return set()
    out = set()
    # Accumulate the per-element findings locally and append them SORTED, so a `set` input (some callers
    # pass the union of active and archive ids) cannot leak its nondeterministic iteration order into the
    # finding stream and break the house sorted-output constraint. _safe_display renders an oversized
    # element without tripping repr()'s base-10 integer-string-conversion limit.
    local = []
    for v in values:
        n = v if isinstance(v, int) and not isinstance(v, bool) else _wl_num(v)
        if n is None or n < 1:
            local.append("{}: entry {} is not a well-formed WL-<n> id (spec 8.2)".format(
                where, _safe_display(v)))
            continue
        out.add(n)
    findings.extend(sorted(local))
    return out


def _parse_span(span, findings, where):
    """Parse a `worklog_span` value into (start, end) WL-numbers, or None for an empty span, appending a
    finding and returning False for a malformed one. A non-empty span is a two-element array of WL ids
    with start <= end (spec 6.1)."""
    if span == []:
        return None
    if not isinstance(span, list) or len(span) != 2:
        findings.append("{}: worklog_span must be a two-element [start, end] array or [] (spec 6.1)".format(where))
        return False
    a, b = _wl_num(span[0]), _wl_num(span[1])
    if a is None or b is None:
        findings.append("{}: worklog_span entries must be well-formed WL-<n> ids, got {}".format(
            where, _safe_display(span)))
        return False
    if a > b:
        findings.append("{}: worklog_span start {} is after end {} (spec 6.1)".format(where, span[0], span[1]))
        return False
    return (a, b)


# --- coverage-digest canonicalization (spec 6.1; scheme defined here) --------------------------------

def _canonical(value, _depth=0):
    """A deterministic canonical string for a validated worklog value: tables have their keys in sorted
    order, arrays keep their order, strings are JSON-escaped (a stable, reversible escaping), ints and
    bools have a fixed spelling, and a finite float is spelled with U8's shared float rule (signed zero
    canonicalized, a non-finite float fails closed) so the digest and the emitter agree byte-for-byte
    (spec 8.7 permits a finite float in a registered extension; M2). An unexpected type fails CLOSED
    (ReleaseError) rather than serializing lossily, so the digest can never be computed over a value the
    canonicalization does not fully cover. `_depth` is the recursion level, bounded by FIX 2 below."""
    # FIX 2 (defence-in-depth for a currently-unreachable-from-TOML input): a Python structure nested
    # deeper than _MAX_CANONICAL_DEPTH would recurse until the interpreter raises an uncontrolled
    # RecursionError. Such depth cannot arrive via tomllib (its own parser recursion guard fires first at
    # every recursion limit), so this is unreachable from parsed TOML; the bound is here so the
    # fail-closed posture never rests on tomllib's limit. Over-depth maps to the module's controlled
    # ReleaseError, never a RecursionError. The bound is far above any real worklog nesting, so it changes
    # no output for a real (shallow) input.
    if _depth > _MAX_CANONICAL_DEPTH:
        raise ReleaseError("cannot canonicalize a worklog value nested deeper than {} levels (a real "
                           "worklog entry nests only a handful of levels)".format(_MAX_CANONICAL_DEPTH))
    # bool is an int subclass; test it first so True/False never spell as 1/0.
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        # FIX 1: CPython raises ValueError on str() of an int whose BASE-10 length exceeds the
        # interpreter's integer-string-conversion limit (4300 digits by default). The limit applies to
        # base-10 only, so tomllib parses a hexadecimal, octal, or binary integer literal (0x.../0o.../
        # 0b...) into an arbitrarily-large int with NO digit limit: such a value IS reachable from parsed
        # TOML (a worklog field carrying an oversized non-decimal int), and this guard maps it to the
        # module's controlled ReleaseError, never an uncontrolled ValueError. A normal-magnitude int
        # spells byte-identically, so the digest is preserved for every real input.
        try:
            return str(value)
        except ValueError as exc:
            raise ReleaseError("cannot canonicalize an oversized integer ({})".format(exc))
    if isinstance(value, float):
        # A finite float digests cleanly (reusing U8's spelling); a non-finite float has no canonical form
        # and fails closed, mapped to ReleaseError like every other uncanonicalizable value (M2).
        try:
            return _canonical_float(value)
        except EmitError as exc:
            raise ReleaseError(str(exc))
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        return "[" + ",".join(_canonical(v, _depth + 1) for v in value) + "]"
    if isinstance(value, dict):
        # Keys are ordered so the digest is invariant to table key-order. A parsed-TOML table always
        # has str keys, so this orders cleanly; a hand-constructed table with a non-str key (the only
        # way a non-str key can arrive, since tomllib keys are always strings) cannot be ordered against
        # a str key and would crash sorted() on a type mismatch, so it fails CLOSED with a ReleaseError.
        # We REJECT rather than str-coerce the key: coercing would silently change the digest bytes for a
        # non-str key, whereas rejecting keeps the digest BYTE-IDENTICAL for every real all-str-key table
        # and turns the uncanonicalizable table into a controlled refusal (guard-input-soundness; M2).
        for k in value:
            if not isinstance(k, str):
                raise ReleaseError("cannot canonicalize a table with a non-string key {!r} (type {}); a "
                                   "worklog entry table has string keys only".format(k, type(k).__name__))
        return "{" + ",".join(
            json.dumps(k, ensure_ascii=False) + ":" + _canonical(v, _depth + 1)
            for k, v in sorted(value.items())) + "}"
    raise ReleaseError("cannot canonicalize value of type {} (worklog entries carry only str/int/bool/"
                       "float/array/table)".format(type(value).__name__))


def coverage_digest(entries):
    """The coverage digest over an iterable of worklog entry tables (spec 6.1, scheme
    `opf-worklog-coverage-v1`). Entries are ordered by WL-number ascending here, so the digest is
    invariant to the order they are passed in (reorder-invariant across the entry array); each entry is
    canonicalized with sorted keys, so it is also invariant to key order within a table. Returns
    `sha256:<hex>`. Raises ReleaseError (fail-closed) on an entry that is not a table, lacks a WL id, or
    carries an uncanonicalizable value."""
    if not _is_item_collection(entries):
        # `entries` is a control; a non-iterable would crash the `for` and a bare string would splat into
        # characters. Fail closed (ReleaseError) rather than a TypeError (guard-input-soundness; spec 6.1).
        raise ReleaseError("worklog entries must be an iterable of tables, not {}".format(
            type(entries).__name__))
    keyed = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise ReleaseError("a worklog entry is not a table")
        n = _wl_num(entry.get("id"))
        if n is None:
            raise ReleaseError("a worklog entry lacks a well-formed WL-<n> id: {}".format(
                _safe_display(entry.get("id"))))
        keyed.append((n, entry))
    keyed.sort(key=lambda t: t[0])
    parts = [COVERAGE_SCHEME]
    parts.extend(_canonical(entry) for _, entry in keyed)
    payload = "\n".join(parts).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _entries_by_id(worklog_data):
    """Map WL-number -> entry table for the active worklog entries, or (None, findings) on a malformed
    worklog. A duplicate WL-number is a finding (spec 8.2: ids are never reused)."""
    findings = []
    by_id = {}
    entries = worklog_data.get("entry", []) if isinstance(worklog_data, dict) else None
    if entries is None:
        entries = []
    if not isinstance(entries, list):
        return None, ["worklog [[entry]] is not an array of tables"]
    for i, entry in enumerate(entries):
        if not isinstance(entry, dict):
            findings.append("worklog entry #{} is not a table".format(i + 1))
            continue
        n = _wl_num(entry.get("id"))
        if n is None:
            findings.append("worklog entry #{} lacks a well-formed WL-<n> id: {}".format(
                i + 1, _safe_display(entry.get("id"))))
            continue
        if n in by_id:
            findings.append("duplicate worklog id WL-{}: ids are never reused (spec 8.2)".format(n))
            continue
        by_id[n] = entry
    return by_id, findings


def compute_span_digest(entries_by_id, span):
    """The coverage digest over the entries a span covers, drawn from an id->entry map. `span` is a
    parsed (start, end) tuple or None (an empty span). Raises ReleaseError (fail-closed) when a covered
    id is absent from the map: a span cannot be digested against a worklog missing its entries (the
    entries may sit in the archive, which the caller must merge in first; spec 12)."""
    if span is None:
        return coverage_digest([])
    if not isinstance(entries_by_id, dict):
        # `entries_by_id` is a control (the id->entry map); a non-table would crash on .get(n). Fail
        # closed (ReleaseError) rather than an AttributeError (guard-input-soundness; spec 12).
        raise ReleaseError("cannot digest a span: the id->entry map is not a table, got {}".format(
            type(entries_by_id).__name__))
    if not (isinstance(span, (tuple, list)) and len(span) == 2
            and all(isinstance(x, int) and not isinstance(x, bool) for x in span)):
        # `span` is a (start, end) pair of WL-numbers or None (guarded above). A non-pair would crash on
        # the unpacking or on range(); fail closed (ReleaseError) rather than a TypeError (spec 6.1).
        raise ReleaseError("cannot digest a span: span must be a (start, end) pair of WL-numbers or "
                           "None, got {!r}".format(span))
    start, end = span
    covered = []
    for n in range(start, end + 1):
        entry = entries_by_id.get(n)
        if entry is None:
            raise ReleaseError("worklog entry WL-{} covered by a span is absent (merge the archive; "
                               "spec 12)".format(n))
        covered.append(entry)
    return coverage_digest(covered)


# --- span tiling (spec 6.1) --------------------------------------------------------------------------

def _tile_releases(releases, findings):
    """Validate that the non-empty release spans tile the released worklog exactly (contiguous,
    non-overlapping, in ID order, starting at WL-1) and return the released end WL-number (0 when no span
    covers anything). Empty spans contribute nothing and do not advance the cursor (spec 6.1 / 14.3).
    Appends a finding per gap, overlap, malformed span, or wrong start. `releases` is the parsed row list;
    each row must already carry a `worklog_span` value."""
    cursor = 0                            # the highest WL-number tiled so far
    for i, row in enumerate(releases):
        where = "release #{} ({})".format(i + 1, _safe_str(row.get("version", "?")))
        parsed = _parse_span(row.get("worklog_span"), findings, where)
        if parsed is False:
            continue                      # malformed span already reported; skip the tiling step for it
        if parsed is None:
            continue                      # an empty span covers nothing
        start, end = parsed
        expected = cursor + 1
        if start != expected:
            if start > expected:
                findings.append("{}: span starts at WL-{} but WL-{} is uncovered (gap; spans must tile, "
                                "spec 6.1)".format(where, start, expected))
            else:
                findings.append("{}: span starts at WL-{}, at or before the covered end WL-{} (overlap; "
                                "spec 6.1)".format(where, start, cursor))
            # Advance the cursor to the furthest covered id so a later row is judged against real coverage.
            cursor = max(cursor, end)
        else:
            cursor = end
    return cursor


def _releases_or_finding(version_data, findings):
    """Extract the [[release]] rows from a parsed version ledger for the fail-closed guard helpers, or
    append a cannot-evaluate finding and return None when the ledger is not a table, its `release` value is
    not a list, OR any release ROW is not a table. A guard that cannot read its own input reports that,
    never a silent empty clean pass, and a malformed row is not silently skipped (the
    check-fails-closed-on-unreadable rule; M8). A `schema` marker other than the supported version is also
    a cannot-evaluate: the standalone guards must not parse an unsupported-schema ledger under v{supported}
    assumptions and read it as clean (M3)."""
    if not isinstance(version_data, dict):
        findings.append("cannot evaluate: version ledger is not a table")
        return None
    schema = version_data.get("schema")
    if schema is not None and (type(schema) is not int or schema != SUPPORTED_SCHEMA):
        findings.append("cannot evaluate: version.toml schema {} is not the supported schema version {} "
                        "(fail-closed; do not parse under v{} assumptions; M3)".format(
                            _safe_display(schema), SUPPORTED_SCHEMA, SUPPORTED_SCHEMA))
        return None
    releases = version_data.get("release", [])
    if not isinstance(releases, list):
        findings.append("cannot evaluate: version.toml [[release]] is not an array of tables")
        return None
    for i, row in enumerate(releases):
        if not isinstance(row, dict):
            findings.append("cannot evaluate: release #{} is not a table (spec 6.1)".format(i + 1))
            return None
    return releases


def released_end(releases):
    """The highest WL-number covered by any release span (0 when none), computed WITHOUT re-reporting
    tiling findings. Used by the release cut and rotation checks to find the frozen/unreleased boundary.
    Fails closed (ReleaseError) on a non-list ledger, OR on a MALFORMED span, rather than returning a
    silent under-computed 0 that reads as an empty released history (the check-fails-closed-on-unreadable
    rule; M3). A malformed span is dropped by no silent path here: it raises, so a guard that composes
    released_end surfaces the unreadable ledger rather than reading it as clean."""
    if not isinstance(releases, list):
        raise ReleaseError("cannot compute released end: [[release]] is not an array of tables")
    end = 0
    for row in releases:
        if not isinstance(row, dict):
            # A non-table release row is an unreadable ledger element, not an empty span: fail CLOSED
            # (ReleaseError) rather than silently skipping it and under-computing the released end to 0,
            # which would read an unreadable history as "nothing released" (the check-fails-closed-on-
            # unreadable rule; spec 6.1). Matches the sibling check_no_append_into_released's refusal.
            raise ReleaseError("cannot compute released end: a release row is not a table (spec 6.1)")
        span_findings = []
        parsed = _parse_span(row.get("worklog_span"), span_findings, "release")
        if parsed is False:
            raise ReleaseError("cannot compute released end: " + "; ".join(span_findings))
        if parsed:                       # a (start, end) tuple; None (an empty span) contributes nothing
            end = max(end, parsed[1])
    return end


# --- version.toml validation (spec 6.1) --------------------------------------------------------------

def validate_version(data):
    """Validate a parsed version.toml (spec 6.1). Returns a VersionValidation. CANNOT-EVALUATE when the
    input is not a table; INVALID when a well-formed table violates the ledger schema; VALID otherwise.
    Structural only: it validates the release rows (version SemVer + uniqueness + monotonicity, date,
    span shape + tiling, digest format) and the summary rows (covers token, status, digest presence,
    superseded_by), but leaves the changelog FACTS gates (tile-the-ledger-exactly, heading 1:1, freeze
    recompute) to U5 and the coverage-digest recompute to `check_frozen_coverage`."""
    if not isinstance(data, dict):
        return VersionValidation(CANNOT_EVALUATE, ["version.toml is not a table"])

    findings = []
    extra = set(data) - VERSION_TOP_KEYS
    if extra:
        findings.append("version.toml unknown top-level key(s): {}".format(
            ", ".join(_sorted_key_names(extra))))
    if "schema" in data:
        if type(data.get("schema")) is not int:
            findings.append("version.toml schema must be an integer")
        elif data.get("schema") != SUPPORTED_SCHEMA:
            findings.append("version.toml schema {} is not the supported schema version {} (fail-closed; "
                            "do not parse under v{} assumptions)".format(
                                _safe_display(data.get("schema")), SUPPORTED_SCHEMA, SUPPORTED_SCHEMA))

    releases = data.get("release", [])
    if not isinstance(releases, list):
        return VersionValidation(INVALID, findings + ["[[release]] is not an array of tables"])
    summaries = data.get("summary", [])
    if not isinstance(summaries, list):
        return VersionValidation(INVALID, findings + ["[[summary]] is not an array of tables"])

    ledger_versions = []                  # released version strings, in row order (for covers-token lookup)
    prev_key = None
    for i, row in enumerate(releases):
        where = "release #{}".format(i + 1)
        if not isinstance(row, dict):
            findings.append("{} is not a table".format(where))
            continue
        row_extra = set(row) - RELEASE_KEYS
        if row_extra:
            findings.append("{}: unknown key(s): {}".format(where, ", ".join(_sorted_key_names(row_extra))))
        for req in ("version", "date", "worklog_span", "coverage_digest"):
            if req not in row:
                findings.append("{}: missing required field: {}".format(where, req))

        version = row.get("version")
        key = parse_semver(version) if "version" in row else None
        if "version" in row and key is None:
            findings.append("{}: version {} is not a valid SemVer string (spec 6.1)".format(
                where, _safe_display(version)))
        elif key is not None:
            if version in ledger_versions:
                findings.append("{}: version {!r} is not unique in the ledger (spec 6.1)".format(where, version))
            elif prev_key is not None and not (prev_key < key):
                findings.append("{}: version {!r} is not strictly greater than the previous release "
                                "(versions are monotonic, spec 6.1)".format(where, version))
            prev_key = key if (prev_key is None or prev_key < key) else prev_key
        if "version" in row:
            ledger_versions.append(version)

        if "date" in row and not _valid_timestamp(row.get("date")):
            findings.append("{}: date must be an RFC 3339 UTC timestamp (spec 6.1)".format(where))
        if "worklog_span" in row:
            _parse_span(row.get("worklog_span"), findings, where)
        if "coverage_digest" in row and not _valid_digest(row.get("coverage_digest")):
            findings.append("{}: coverage_digest must be 'sha256:<64 hex>' (spec 6.1)".format(where))

    # Spans must tile the released worklog exactly (contiguous, non-overlapping, from WL-1; spec 6.1).
    _tile_releases([r for r in releases if isinstance(r, dict)], findings)

    _validate_summaries(summaries, ledger_versions, findings)

    return VersionValidation(INVALID if findings else VALID, findings, releases, summaries)


def _valid_digest(value):
    return isinstance(value, str) and _DIGEST_RE.match(value) is not None


def _parse_covers(token, ledger_versions):
    """Resolve a covers token against the ledger versions (in row order). Returns (kind, detail):
    ("unreleased", None); ("single", version); or ("range", (lo_index, hi_index)). Returns (None, message)
    when the token is malformed or refers to a version absent from the ledger (spec 6.1, 7.1)."""
    if not isinstance(token, str) or not token:
        return None, "covers must be a non-empty string"
    if token == UNRELEASED:
        return ("unreleased", None), None
    if ".." in token:
        parts = token.split("..")
        if len(parts) != 2 or not parts[0] or not parts[1]:
            return None, "covers range {!r} must be 'a..b'".format(token)
        lo, hi = parts
        if lo not in ledger_versions or hi not in ledger_versions:
            return None, "covers range {!r} names a version absent from the ledger".format(token)
        li, hi_i = ledger_versions.index(lo), ledger_versions.index(hi)
        if li > hi_i:
            return None, "covers range {!r} is not in ledger order (a after b)".format(token)
        return ("range", (li, hi_i)), None
    if token not in ledger_versions:
        return None, "covers {!r} names a version absent from the ledger".format(token)
    return ("single", token), None


def _covers_range(parsed, ledger_versions):
    """The (lo_index, hi_index) ledger-order span a parsed covers token spans, or None for `unreleased`.
    Used to check that a rollup summary COVERS the summary it supersedes (spec 6.4; M10)."""
    kind, detail = parsed
    if kind == "single":
        idx = ledger_versions.index(detail)
        return (idx, idx)
    if kind == "range":
        return detail
    return None


def _validate_summaries(summaries, ledger_versions, findings):
    """Validate the [[summary]] rows structurally (spec 6.1). See the summary-row ambiguity note; the
    tile-the-ledger-exactly and heading-1:1 FACTS gates are U5's."""
    seen_covers = set()
    seen_unreleased = False
    # Pre-pass: map each well-formed covers token to its parsed (kind, detail), so a superseded row's
    # superseded_by can be checked to name an EXISTING rollup summary row that covers it (spec 6.1/6.4; M10).
    covers_index = {}
    for row in summaries:
        if not isinstance(row, dict) or "covers" not in row:
            continue
        parsed, err = _parse_covers(row.get("covers"), ledger_versions)
        if err is None:
            covers_index[row.get("covers")] = parsed
    for i, row in enumerate(summaries):
        where = "summary #{}".format(i + 1)
        if not isinstance(row, dict):
            findings.append("{} is not a table".format(where))
            continue
        row_extra = set(row) - SUMMARY_KEYS
        if row_extra:
            findings.append("{}: unknown key(s): {}".format(where, ", ".join(_sorted_key_names(row_extra))))

        covers = row.get("covers")
        kind = None
        if "covers" not in row:
            findings.append("{}: missing required field: covers".format(where))
        else:
            parsed, err = _parse_covers(covers, ledger_versions)
            if err is not None:
                findings.append("{}: {}".format(where, err))
            else:
                kind = parsed[0]
                if covers in seen_covers:
                    findings.append("{}: covers {!r} is duplicated across summary rows".format(where, covers))
                seen_covers.add(covers)
                if kind == "unreleased":
                    if seen_unreleased:
                        findings.append("{}: more than one 'unreleased' summary row (spec 6.1)".format(where))
                    seen_unreleased = True

        status = row.get("status")
        if "status" not in row:
            findings.append("{}: missing required field: status".format(where))
        elif status not in SUMMARY_STATUSES:
            findings.append("{}: status {} is not one of {} (spec 6.1)".format(
                where, _safe_display(status), list(SUMMARY_STATUSES)))

        # digest is required once published or superseded; and never carried by a working row.
        has_digest = "digest" in row
        if status in ("published", "superseded"):
            if not has_digest:
                findings.append("{}: a {} summary must carry a freeze digest (spec 6.1)".format(where, status))
            elif not _valid_digest(row.get("digest")):
                findings.append("{}: digest must be 'sha256:<64 hex>'".format(where))
        elif has_digest:
            findings.append("{}: a working summary carries no digest until it is published (spec 6.1)".format(where))

        # superseded_by is present EXACTLY when superseded, and is itself a covers token (the rollup).
        has_sb = "superseded_by" in row
        if status == "superseded":
            if not has_sb:
                findings.append("{}: a superseded summary must name its superseded_by rollup (spec 6.1)".format(where))
            else:
                sb = row.get("superseded_by")
                sb_parsed, err = _parse_covers(sb, ledger_versions)
                if err is not None:
                    findings.append("{}: superseded_by: {}".format(where, err))
                elif sb == covers:
                    # A summary cannot supersede itself (spec 6.1/6.4): superseded_by names the rollup that
                    # REPLACED this row, which is a different row (M10).
                    findings.append("{}: superseded_by must not name the row itself; a summary cannot "
                                    "supersede itself (spec 6.1/6.4)".format(where))
                elif sb == UNRELEASED:
                    # The working tail is never a rollup: it is status working (enforced below) and can
                    # never have superseded a released summary, so superseded_by must not name it. Without
                    # this an 'unreleased' token routes _covers_range to None, the covering check
                    # (roll_range is not None) is skipped, and the row fails OPEN to VALID whenever an
                    # 'unreleased' summary row is present (spec 6.1/6.4; fail-closed to INVALID).
                    findings.append("{}: superseded_by must name a rollup summary, not the working "
                                    "'unreleased' tail (spec 6.1/6.4)".format(where))
                elif sb not in covers_index:
                    # superseded_by must name an EXISTING rollup summary row (spec 6.1: "the covers token
                    # of the rollup summary that replaced this one"), not merely a ledger-valid token (M10).
                    findings.append("{}: superseded_by {!r} names no existing rollup summary row "
                                    "(spec 6.1/6.4)".format(where, sb))
                else:
                    # The rollup must COVER this summary's range (spec 6.4: a range summary supersedes only
                    # the summaries within its range).
                    # `covers` may be an UNHASHABLE malformed value (a list/table already flagged above);
                    # gate the dict membership on isinstance(str) so `covers in covers_index` cannot raise
                    # TypeError on an unhashable key (fail-closed to the recorded covers finding, no crash).
                    this_range = _covers_range(covers_index[covers], ledger_versions) \
                        if isinstance(covers, str) and covers in covers_index else None
                    roll_range = _covers_range(sb_parsed, ledger_versions)
                    if this_range is not None and roll_range is not None:
                        if tuple(roll_range) == tuple(this_range):
                            # A rollup whose covered range EQUALS this row's own range rolls up nothing and
                            # supersedes the same versions; distinct tokens for one range (e.g. "1.0.0" and
                            # "1.0.0..1.0.0") otherwise slip the token-level self-reference guard above and
                            # admit a supersession CYCLE with no surviving rollup (spec 6.4; fail-closed).
                            findings.append("{}: superseded_by {!r} covers exactly this summary's own "
                                            "range; a rollup must roll up more than the row it supersedes "
                                            "(spec 6.4)".format(where, sb))
                        elif not (roll_range[0] <= this_range[0] and this_range[1] <= roll_range[1]):
                            findings.append("{}: superseded_by {!r} does not cover this summary's range; a "
                                            "rollup supersedes only the summaries within its range "
                                            "(spec 6.4)".format(where, sb))
        elif has_sb:
            findings.append("{}: superseded_by is present only on a superseded summary (spec 6.1)".format(where))

        # The unreleased working tail is a working row; it is never published or superseded (spec 6.1).
        if kind == "unreleased" and status is not None and status != "working":
            findings.append("{}: the 'unreleased' summary is always working, not {} (spec 6.1)".format(
                where, _safe_display(status)))


# --- worklog.toml validation (spec 6.2) --------------------------------------------------------------

def validate_worklog(data, registered_vendors=frozenset(), registered_kinds=None):
    """Validate a parsed worklog.toml (spec 6.2). Returns a WorklogValidation. CANNOT-EVALUATE when the
    input is not a table; INVALID when a well-formed table has a malformed entry; VALID otherwise. Each
    `[[entry]]` is validated as a reduced-envelope `worklog` record through U2's `validate_record`; ids
    are unique WL ids (spec 8.2). `registered_kinds` is the manifest's additional worklog change kinds
    (spec 6.2), threaded to `validate_record`; the built-in kinds remain the default when None. The
    tail-vs-frozen boundary and the coverage-digest recompute are the caller's (release cut /
    `check_frozen_coverage`), which read the version ledger alongside."""
    if not isinstance(data, dict):
        return WorklogValidation(CANNOT_EVALUATE, ["worklog.toml is not a table"])

    findings = []
    extra = set(data) - WORKLOG_TOP_KEYS
    if extra:
        findings.append("worklog.toml unknown top-level key(s): {}".format(
            ", ".join(_sorted_key_names(extra))))
    if "schema" in data:
        if type(data.get("schema")) is not int:
            findings.append("worklog.toml schema must be an integer")
        elif data.get("schema") != SUPPORTED_SCHEMA:
            findings.append("worklog.toml schema {} is not the supported schema version {} (fail-closed; "
                            "do not parse under v{} assumptions)".format(
                                _safe_display(data.get("schema")), SUPPORTED_SCHEMA, SUPPORTED_SCHEMA))

    entries = data.get("entry", [])
    if not isinstance(entries, list):
        return WorklogValidation(INVALID, findings + ["[[entry]] is not an array of tables"])

    entry_ids = []
    seen = set()
    for i, entry in enumerate(entries):
        where = "worklog entry #{}".format(i + 1)
        rv = validate_record(entry, expected_type="worklog", registered_vendors=registered_vendors,
                             registered_kinds=registered_kinds)
        if rv.status != VALID:
            findings.extend("{}: {}".format(where, f) for f in rv.findings)
            continue
        n = _wl_num(entry.get("id"))
        if n is None:
            findings.append("{}: id is not a well-formed WL-<n> id".format(where))
            continue
        if n in seen:
            findings.append("{}: duplicate worklog id WL-{} (ids are never reused, spec 8.2)".format(where, n))
            continue
        seen.add(n)
        entry_ids.append(n)

    return WorklogValidation(INVALID if findings else VALID, findings, entry_ids)


# --- the release cut (spec 6.1, 6.2, 8.4) ------------------------------------------------------------

def _verify_append_only(prior_releases, candidate_releases, findings):
    """Confirm `candidate_releases` extends `prior_releases` by APPEND ONLY: every pre-existing row is
    byte-identical (compared at the canonical-byte level, so key order does not matter) and the candidate
    adds rows only at the end (spec 6.1: release rows are append-only and immutable). Appends a finding
    per rewritten historical row and per row that vanished or shrank the ledger. Used by the release cut,
    which holds both the prior and the candidate ledger; a standalone validate_version cannot detect a
    rewritten historical row without the prior (that is U6 doctor's job with stored history)."""
    if len(candidate_releases) < len(prior_releases):
        findings.append("the candidate ledger has fewer release rows than the prior ({} < {}); release "
                        "rows are append-only (spec 6.1)".format(len(candidate_releases), len(prior_releases)))
    for i, prior_row in enumerate(prior_releases):
        if i >= len(candidate_releases):
            break                          # already reported as a shrink above
        try:
            same = _canonical(prior_row) == _canonical(candidate_releases[i])
        except ReleaseError as exc:
            findings.append("release #{}: cannot canonicalize a release row to check immutability "
                            "({})".format(i + 1, exc))
            continue
        if not same:
            findings.append("release #{} ({}) was rewritten; a pre-existing release row is immutable and "
                            "may only be appended after (spec 6.1)".format(
                                i + 1, _safe_str(prior_row.get("version", "?")) if isinstance(prior_row, dict)
                                else "?"))


def release_cut(version_data, worklog_data, new_version, date,
                registered_vendors=frozenset(), registered_kinds=None):
    """Freeze the current unreleased worklog tail into a released span keyed to `new_version` (spec 6.1,
    6.2). Returns a CutResult carrying a NEW version.toml value with the release row appended; it is a
    PURE per-ledger function (workers-produce-inert-data): the worklog is never mutated and nothing is ever
    deleted. It answers only from version.toml + the ACTIVE worklog.toml. As defense-in-depth it enforces
    frozen coverage over the released spans it can see (the non-rotated case, every prior released entry
    still in the active worklog), failing closed to INVALID on a mutated frozen span; whole-store integrity
    for the ROTATED/archive case (frozen coverage across active+archive, archive enumeration, no-deletion,
    partition) is deferred to the store-level validator, not the cut (see the fail-closed seam for a rotated
    store below).

    Fails closed (INVALID / CANNOT-EVALUATE, no cut computed) on any inconsistent state:
      - the current ledger or worklog does not validate (an inconsistent state cannot be cut from);
      - `new_version` is not a valid SemVer, is already in the ledger, or is not strictly greater than the
        last release (versions are monotonic, spec 6.1);
      - the store is ROTATED (a prior released span's entries are not all present in the active worklog): a
        pure per-ledger cut cannot verify prior-frozen integrity across the archive, so it returns
        CANNOT-EVALUATE and directs the caller to the store-level validator rather than silently skipping
        the check (the fail-closed seam between the units);
      - a prior frozen released span the cut CAN see (non-rotated) was mutated: a covered entry's edit
        changes the recomputed coverage digest, so the cut returns INVALID rather than certifying a cut
        over a rewritten frozen span (defense-in-depth, spec 6.2/13);
      - the unreleased tail is not a contiguous run from released_end+1 to the last worklog id (a gap in
        the worklog would leave a span that cannot tile).

    The new release row covers [released_end+1 .. last worklog id] (or [] when the tail is empty, spec 6.1
    / 14.3) with the tail's coverage digest. After the cut the new unreleased tail is EMPTY by construction
    (the new span reaches the last worklog id); the caller may assert this with `tail_ids`.

    Append-only immutability: the returned ledger preserves every pre-existing release row byte-identically
    and appends only the new row (spec 6.1). Standalone validate_version cannot detect a rewritten
    historical row without the prior ledger (that residual belongs to the store validator / doctor with
    stored history); the cut, holding both prior and candidate, enforces append-only on its OWN
    construction here.

    `registered_vendors` and `registered_kinds` are the manifest's registered x-<vendor> extensions and
    additional worklog change kinds (spec 6.2, 8.7); they are threaded into validate_worklog so a worklog
    using a manifest-registered kind or extension validates through the cut, matching validate_worklog (M6)."""
    vv = validate_version(version_data)
    if vv.status == CANNOT_EVALUATE:
        return CutResult(CANNOT_EVALUATE, ["cannot cut: version.toml does not evaluate"] + vv.findings)
    if vv.status != VALID:
        return CutResult(INVALID, ["cannot cut from an inconsistent ledger"] + vv.findings)
    wv = validate_worklog(worklog_data, registered_vendors=registered_vendors,
                          registered_kinds=registered_kinds)
    if wv.status == CANNOT_EVALUATE:
        return CutResult(CANNOT_EVALUATE, ["cannot cut: worklog.toml does not evaluate"] + wv.findings)
    if wv.status != VALID:
        return CutResult(INVALID, ["cannot cut from an inconsistent worklog"] + wv.findings)

    findings = []
    new_key = parse_semver(new_version)
    if new_key is None:
        return CutResult(INVALID, ["new version {} is not a valid SemVer string (spec 6.1)".format(
            _safe_display(new_version))])
    ledger_versions = [r.get("version") for r in vv.releases if isinstance(r, dict)]
    if new_version in ledger_versions:
        findings.append("new version {!r} is already in the ledger (spec 6.1)".format(new_version))
    last_key = None
    for v in ledger_versions:
        k = parse_semver(v)
        if k is not None and (last_key is None or k > last_key):
            last_key = k
    if last_key is not None and not (last_key < new_key):
        findings.append("new version {!r} is not strictly greater than the latest release (monotonic, "
                        "spec 6.1)".format(new_version))
    if not _valid_timestamp(date):
        findings.append("date must be an RFC 3339 UTC timestamp (spec 6.1)")
    if findings:
        return CutResult(INVALID, findings)

    by_id, id_findings = _entries_by_id(worklog_data)
    if id_findings:
        return CutResult(INVALID, ["cannot cut: worklog ids are malformed"] + id_findings)

    # Fail-closed seam with the store-level validator: a pure per-ledger cut cannot verify that a prior
    # released span is still intact once that span has been ROTATED to the archive (it would have to consume
    # a raw archive iterable, which cannot be told apart from an ad-hoc disappearance). If any id in a prior
    # released span (1 .. released_end) is not present in the ACTIVE worklog, the store is rotated: return
    # CANNOT-EVALUATE and direct the caller to the store validator, rather than silently skipping the
    # prior-frozen check. In a non-rotated store every prior released entry is still in the active worklog,
    # so the cut may assume prior frozen spans are intact (the store validator certifies them across
    # active+archive) and proceeds. This is the revert of the M7 archived-entries overreach.
    prior_end = released_end(vv.releases)
    if any(n not in by_id for n in range(1, prior_end + 1)):
        return CutResult(CANNOT_EVALUATE, [
            "cannot cut: prior frozen integrity of a rotated span must be verified at the store level; "
            "call validate_store first"])

    # Defense-in-depth, fail-closed: over the spans this pure per-ledger cut CAN see (the non-rotated case
    # reached here, where every prior released entry is still present in the active worklog) enforce frozen
    # coverage now, so a cut can never certify VALID over a MUTATED already-frozen released span. A digest
    # mismatch on a prior span means a frozen-covered entry was edited: fail closed to INVALID. The
    # rotated/archive case is deferred above to the store-level validator (validate_store), unchanged.
    frozen_findings = check_frozen_coverage(version_data, by_id)
    if frozen_findings:
        return CutResult(INVALID, ["cannot cut: a frozen released span was mutated"] + frozen_findings)

    end = prior_end
    all_nums = sorted(by_id)
    tail = [n for n in all_nums if n > end]

    if not tail:
        span_value = []
        span_parsed = None
    else:
        # The tail MUST be the contiguous run end+1 .. max, or a span cannot tile (spec 6.1). Checked by
        # COUNT over the present ids (tail is a sorted set of distinct WL-numbers all > end), never by
        # materializing list(range(end + 1, tail[-1] + 1)): that list is sized by the DECLARED max id, not
        # the entry count, so a single large-id entry (a plausible WL-1000000000 typo) forces an O(max_id)
        # allocation and an uncaught MemoryError. The contiguous run end+1..max holds exactly (max - end)
        # ids from end+1, so len(tail) == tail[-1] - end and tail[0] == end + 1 iff it tiles (RANGE-BOUNDS;
        # SECA-resource-bounds; fail-closed to INVALID on a gap).
        if tail[0] != end + 1 or tail[-1] - end != len(tail):
            return CutResult(INVALID, ["cannot cut: the unreleased tail WL-{}..WL-{} is not contiguous "
                                       "(a gap would break span tiling, spec 6.1)".format(end + 1, tail[-1])])
        span_value = ["WL-{}".format(tail[0]), "WL-{}".format(tail[-1])]
        span_parsed = (tail[0], tail[-1])

    try:
        digest = compute_span_digest(by_id, span_parsed)
    except ReleaseError as exc:
        return CutResult(CANNOT_EVALUATE, ["cannot cut: coverage digest failed closed ({})".format(exc)])

    new_row = {
        "version": new_version,
        "date": date,
        "worklog_span": span_value,
        "coverage_digest": digest,
    }
    # Build a NEW version.toml value: append the release row, carry schema and summaries unchanged. The
    # summary/changelog rollup is a separate curated step (U5); the cut touches the release ledger only.
    new_version_data = {}
    if "schema" in version_data:
        new_version_data["schema"] = version_data["schema"]
    new_version_data["release"] = list(vv.releases) + [new_row]
    if "summary" in version_data:
        new_version_data["summary"] = list(version_data["summary"])

    # Append-only immutability: every pre-existing release row must be byte-identical in the output, only
    # the new row appended. This guards the cut's own construction against ever rewriting history (spec
    # 6.1). Standalone validate_version cannot catch a rewritten historical row without the prior ledger
    # (that belongs to U6 doctor with stored history); the cut, holding both, enforces it here.
    append_findings = []
    _verify_append_only(list(vv.releases), new_version_data["release"], append_findings)
    if append_findings:
        return CutResult(INVALID, ["cannot cut: release rows must be append-only"] + append_findings)

    return CutResult(VALID, [], new_version_data, span_value, digest)


def tail_ids(releases, worklog_ids):
    """The WL-numbers of the unreleased tail: every worklog id greater than the released end. `releases`
    is the parsed release-row list; `worklog_ids` an iterable of WL-numbers. Used to assert the post-cut
    empty-tail invariant (spec 6.2: the release cut leaves the new unreleased tail empty)."""
    end = released_end(releases)
    if not _is_item_collection(worklog_ids):
        # worklog_ids is a control; a non-iterable would crash and a bare string would compare
        # character-vs-int. Fail closed (ReleaseError) rather than a TypeError (guard-input-soundness).
        raise ReleaseError("cannot compute the tail: worklog ids must be an iterable of WL-numbers, not "
                           "{}".format(type(worklog_ids).__name__))
    tail = []
    for n in worklog_ids:
        if not (isinstance(n, int) and not isinstance(n, bool)):
            raise ReleaseError("cannot compute the tail: worklog id {!r} is not a WL-number".format(n))
        if n < 1:
            # A WL-number is a POSITIVE integer (spec 8.2); a non-positive id (WL-0, a negative) is
            # malformed, not a tail member to be silently dropped by the `n > end` filter, which would let
            # it falsely satisfy an empty-tail assertion. Fail closed (guard-input-soundness).
            raise ReleaseError("cannot compute the tail: worklog id {} is not a positive WL-<n> id "
                               "(spec 8.2)".format(_safe_display(n)))
        if n > end:
            tail.append(n)
    return sorted(tail)


# --- frozen-span / no-deletion / rotation guards (spec 6.2, 8.4, 12, 13) -----------------------------

def check_frozen_coverage(version_data, entries_by_id):
    """Confirm every released span's stored coverage_digest still matches the current worklog entries
    (spec 6.1, 7.2, 13): a frozen span is immutable, so an edit to a covered entry, or an append of a new
    entry INTO a released span, changes the recomputed digest and is a failure. `entries_by_id` maps
    WL-number -> entry (active merged with archive; spec 12). Returns a finding per mismatch or per span
    whose entries are missing (fail-closed). Ignores empty spans' presence of all covered entries but
    still checks their digest."""
    findings = []
    releases = _releases_or_finding(version_data, findings)
    if releases is None:
        return findings
    for i, row in enumerate(releases):
        if not isinstance(row, dict):
            continue
        where = "release #{} ({})".format(i + 1, _safe_str(row.get("version", "?")))
        span = _parse_span(row.get("worklog_span"), findings, where)
        if span is False:
            continue
        stored = row.get("coverage_digest")
        try:
            recomputed = compute_span_digest(entries_by_id, span if span else None)
        except ReleaseError as exc:
            findings.append("{}: cannot recompute coverage digest ({})".format(where, exc))
            continue
        if stored != recomputed:
            findings.append("{}: coverage_digest mismatch (a frozen released span was rewritten or an "
                            "entry it covers was edited; spec 6.2/13)".format(where))
    return findings


def check_no_append_into_released(version_data, candidate_ids):
    """A new or corrected worklog entry MUST land in the unreleased tail, never inside an already-released
    span (spec 6.2). Returns a finding per candidate WL-number at or below the released end. `candidate_ids`
    is an iterable of WL-numbers (a malformed id is a finding)."""
    findings = []
    releases = _releases_or_finding(version_data, findings)
    if releases is None:
        return findings
    if not _is_item_collection(candidate_ids):
        # A non-iterable would crash the `for`; a bare string would splat into characters. Fail closed
        # with a finding rather than a TypeError or a splat (guard-input-soundness; spec 6.2).
        findings.append("candidate id-collection must be an iterable of WL-numbers, not {} (spec 6.2)".format(
            type(candidate_ids).__name__))
        return findings
    try:
        end = released_end(releases)
    except ReleaseError as exc:
        findings.append("cannot evaluate: {}".format(exc))
        return findings
    # Accumulate per-id findings locally and append them SORTED, so a `set` candidate collection cannot
    # leak its nondeterministic iteration order into the finding stream (house sorted-output constraint).
    local = []
    for cid in candidate_ids:
        n = cid if isinstance(cid, int) and not isinstance(cid, bool) else _wl_num(cid)
        if n is None:
            local.append("candidate worklog id {} is not a well-formed WL-<n> id".format(_safe_display(cid)))
        elif n < 1:
            # A WL-number is a positive integer (spec 8.2); a non-positive raw int is malformed, not a
            # released-span member (m3 sibling sweep).
            local.append("candidate worklog id {} is not a positive WL-<n> id (spec 8.2)".format(
                _safe_display(cid)))
        elif n <= end:
            local.append("worklog id WL-{} falls in an already-released span (<= released end WL-{}); a "
                         "post-release correction is a NEW entry in the unreleased tail (spec 6.2)".format(n, end))
    findings.extend(sorted(local))
    return findings


def check_no_deletion(old_ids, new_ids):
    """Nothing is ever deleted (spec 6.2, 13): every WL-number present before must still be present after,
    in the active worklog OR the archive. `old_ids` / `new_ids` are iterables of WL-numbers (pass the
    UNION of active and archive ids for `new_ids`). Returns a finding per vanished id."""
    findings = []
    now = _wl_id_set(new_ids, "check_no_deletion new id-collection", findings)
    old = _wl_id_set(old_ids, "check_no_deletion old id-collection", findings)
    for n in sorted(old):
        if n not in now:
            findings.append("worklog id WL-{} vanished: no worklog entry is ever deleted (spec 6.2/13)".format(
                _safe_str(n)))
    return findings


def _count_expected_absent(expected_ids, present, where, findings):
    """The number of `expected_ids` (the authoritative id space, a range 1..high-water) absent from
    `present` (a set of WL-numbers), computed WITHOUT materializing a collection sized by the declared
    high-water. A range spanning a spoofed large id (a store entry WL-1000000000) would make
    `_wl_id_set`'s `out.add(n)` loop an O(high-water) allocation and MemoryError, and a per-missing-id
    finding would be O(high-water) too; instead the count/identity discipline the release-cut tail check
    uses answers the interval question in O(present) (RANGE-BOUNDS; SECA-resource-bounds;
    guard-input-soundness). Since `present` holds distinct WL-numbers, the count of present ids that fall
    in `expected_ids` equals |present intersect expected|, so len(expected) minus that count is the number
    of expected ids covered by NEITHER location. The authoritative id space MUST be a `range` 1..high-water
    (the documented contract): a range is sized and membership-tested WITHOUT materializing its members,
    and its members ARE the WL-numbers, so `present` (already-normalized WL-numbers) tests against it
    directly. A NON-range collection is a fail-closed finding, never membership-tested raw: its elements
    are neither deduplicated nor normalized here, so a duplicate would inflate len() and an un-normalized
    element (a bare "WL-1") would never match a normalized present id, each reporting a FALSE loss
    (over-fire). A range too large to size (>= 2**63 members, e.g. a spoofed 19-digit high-water id
    WL-9223372036854775808, whose len() raises OverflowError) is likewise a fail-closed finding. A control
    that cannot answer is a fail-closed finding, never a silent zero."""
    if not isinstance(expected_ids, range):
        # The id space is a RANGE by contract. A non-range collection (a list/set/tuple/generator) cannot
        # be both bounded (never materialized, RANGE-BOUNDS) AND element-normalized here, so its raw
        # elements would size and membership-test WRONG and report a FALSE loss; fail closed to a
        # cannot-evaluate finding rather than over-fire (guard-input-soundness; the fail direction stays
        # closed, never a false pass).
        findings.append("{}: must be a range id space (a range 1..high-water), not {}".format(
            where, type(expected_ids).__name__))
        return 0
    try:
        expected_n = len(expected_ids)
    except (TypeError, OverflowError):
        # OverflowError: a range with >= 2**63 members cannot convert its length to a C ssize_t (a spoofed
        # 19-digit high-water id, WL-9223372036854775808). TypeError is defence-in-depth (the range guard
        # above already excludes an unsized space). Either routes to the same fail-closed finding rather
        # than an uncontrolled crash (SECA-resource-bounds; guard-input-soundness).
        findings.append("{}: id space is too large to size (a range with >= 2**63 members; a spoofed "
                        "high-water id); cannot evaluate the partition (spec 12/13)".format(where))
        return 0
    covered = 0
    for n in present:
        try:
            in_expected = n in expected_ids
        except TypeError:
            findings.append("{}: is not a membership-testable id space ({})".format(
                where, type(expected_ids).__name__))
            return 0
        if in_expected:
            covered += 1
    return expected_n - covered


def check_ids_partition(active_ids, archive_ids, expected_ids=None):
    """Rotation is archival MOVEMENT, never duplication or loss (spec 12): every worklog id exists in
    EXACTLY ONE of the active worklog or the archive. Returns a finding per id present in both, and, when
    an authoritative `expected_ids` set is given (e.g. 1..high-water), a finding naming the COUNT of
    expected ids present in NEITHER location, bounded by the present set so a range 1..high-water spanning
    a spoofed large id cannot force an O(high-water) allocation (M9; RANGE-BOUNDS). Without
    `expected_ids` the guard cannot see a loss (its inputs are the two locations, not the id space it
    should cover), so the authoritative set is threaded in per guard-input-soundness."""
    findings = []
    active = _wl_id_set(active_ids, "check_ids_partition active id-collection", findings)
    archive = _wl_id_set(archive_ids, "check_ids_partition archive id-collection", findings)
    for n in sorted(active & archive):
        findings.append("worklog id WL-{} is in BOTH the active worklog and the archive; rotation is a "
                        "move, an id lives in exactly one location (spec 12)".format(_safe_str(n)))
    if expected_ids is not None:
        # Detect a LOST id (present in NEITHER location) WITHOUT materializing a set sized by the declared
        # high-water: an `expected_ids` range spanning a spoofed large id would OOM _wl_id_set here, and a
        # per-missing-id finding would itself be O(high-water). Answer the interval question by the
        # count/identity discipline over the (bounded) present set instead (RANGE-BOUNDS).
        present = active | archive
        absent = _count_expected_absent(
            expected_ids, present, "check_ids_partition expected id-collection", findings)
        if absent > 0:
            findings.append("{} expected worklog id(s) are in NEITHER the active worklog nor the archive; "
                            "rotation is a move, never a loss (spec 12/13)".format(absent))
    return findings


def check_rotation_only_released(rotated_ids, version_data):
    """Only released, frozen worklog spans may rotate; the mutable unreleased tail never rotates whatever
    its age or size (spec 12). Returns a finding per rotated WL-number beyond the released end.
    `rotated_ids` is an iterable of WL-numbers being moved to the archive."""
    findings = []
    releases = _releases_or_finding(version_data, findings)
    if releases is None:
        return findings
    if not _is_item_collection(rotated_ids):
        # A non-iterable would crash the `for`; a bare string would splat into characters. Fail closed
        # with a finding rather than a TypeError or a splat (guard-input-soundness; spec 12).
        findings.append("rotated id-collection must be an iterable of WL-numbers, not {} (spec 12)".format(
            type(rotated_ids).__name__))
        return findings
    try:
        end = released_end(releases)
    except ReleaseError as exc:
        findings.append("cannot evaluate: {}".format(exc))
        return findings
    # Accumulate per-id findings locally and append them SORTED, so a `set` rotated collection cannot leak
    # its nondeterministic iteration order into the finding stream (house sorted-output constraint).
    local = []
    for rid in rotated_ids:
        n = rid if isinstance(rid, int) and not isinstance(rid, bool) else _wl_num(rid)
        if n is None:
            local.append("rotated worklog id {} is not a well-formed WL-<n> id".format(_safe_display(rid)))
        elif n < 1:
            # A WL-number is a positive integer (spec 8.2); a non-positive raw int (e.g. WL-0) is outside
            # the id grammar and must not slip the > released-end test (m3).
            local.append("rotated worklog id {} is not a positive WL-<n> id (spec 8.2)".format(
                _safe_display(rid)))
        elif n > end:
            local.append("worklog id WL-{} is in the unreleased tail (> released end WL-{}) and must "
                         "never rotate (spec 12)".format(_safe_str(n), end))
    findings.extend(sorted(local))
    return findings


# --- self-test ---------------------------------------------------------------------------------------

def self_test():
    """Release-triad invariants over synthetic version.toml / worklog.toml vectors. Judged on the returned
    status / finding values, never by grepping output (the isolate-verifiers rule). Returns 0 clean, 1 on
    a failed check, 2 on a fail-closed error."""
    failures = []
    checked = 0

    def check(name, cond):
        nonlocal checked
        checked += 1
        if not cond:
            failures.append(name)

    def entry(n, kind="added", summary="s", **extra):
        e = {"id": "WL-{}".format(n), "date": "2026-06-{:02d}T00:00:00Z".format((n % 27) + 1),
             "actor": {"kind": "maintainer"}, "kind": kind, "summary": summary}
        e.update(extra)
        return e

    # --- 1: a VALID version.toml + worklog.toml (the everyday shape, Appendix B/C) --------------------
    worklog = {"schema": 1, "entry": [entry(1), entry(2), entry(3), entry(4)]}
    by_id, idf = _entries_by_id(worklog)
    check("valid-worklog-ids", not idf)
    wl_ok = validate_worklog(worklog)
    check("valid-worklog-status", wl_ok.status == VALID)
    check("valid-worklog-ids-list", wl_ok.entry_ids == [1, 2, 3, 4])
    dig12 = compute_span_digest(by_id, (1, 2))
    vok = {
        "schema": 1,
        "release": [
            {"version": "1.0.0", "date": "2026-06-01T00:00:00Z",
             "worklog_span": ["WL-1", "WL-2"], "coverage_digest": dig12},
        ],
        "summary": [
            {"covers": "unreleased", "status": "working"},
            {"covers": "1.0.0", "status": "published",
             "digest": "sha256:" + "a" * 64},
        ],
    }
    vv = validate_version(vok)
    check("valid-version-status", vv.status == VALID)
    check("valid-version-releases", len(vv.releases) == 1)

    # --- 2: a NON-MONOTONIC version is rejected -------------------------------------------------------
    v_nonmono = {"release": [
        {"version": "2.0.0", "date": "2026-06-01T00:00:00Z", "worklog_span": [], "coverage_digest": "sha256:" + "0" * 64},
        {"version": "1.0.0", "date": "2026-06-02T00:00:00Z", "worklog_span": [], "coverage_digest": "sha256:" + "0" * 64},
    ]}
    check("non-monotonic-invalid", validate_version(v_nonmono).status == INVALID)
    # a duplicate version is also a monotonicity/uniqueness failure
    v_dup = {"release": [
        {"version": "1.0.0", "date": "2026-06-01T00:00:00Z", "worklog_span": [], "coverage_digest": "sha256:" + "0" * 64},
        {"version": "1.0.0", "date": "2026-06-02T00:00:00Z", "worklog_span": [], "coverage_digest": "sha256:" + "0" * 64},
    ]}
    check("duplicate-version-invalid", validate_version(v_dup).status == INVALID)
    # SemVer precedence: a prerelease is lower than its release, and 1.0.0 < 1.0.1 < 1.1.0 < 2.0.0.
    check("semver-prerelease-below-release", parse_semver("1.0.0-rc.1") < parse_semver("1.0.0"))
    check("semver-core-order", parse_semver("1.0.0") < parse_semver("1.0.1") < parse_semver("1.1.0") < parse_semver("2.0.0"))
    check("semver-prerelease-numeric-order", parse_semver("1.0.0-alpha.1") < parse_semver("1.0.0-alpha.2"))
    check("semver-build-ignored", parse_semver("1.0.0+a") == parse_semver("1.0.0+b"))
    check("semver-bad-rejected", parse_semver("1.0") is None and parse_semver("01.0.0") is None)

    # --- 3 & 5: a REWRITE / MUTATION of a released (frozen) span is detected --------------------------
    frozen_ver = {"release": [
        {"version": "1.0.0", "date": "2026-06-01T00:00:00Z",
         "worklog_span": ["WL-1", "WL-2"], "coverage_digest": dig12},
    ]}
    check("frozen-coverage-ok", not check_frozen_coverage(frozen_ver, by_id))
    mutated = {1: entry(1, summary="EDITED after freeze"), 2: by_id[2]}
    check("frozen-mutation-detected", bool(check_frozen_coverage(frozen_ver, mutated)))
    # deletion of a frozen-covered entry is detected too
    check("deletion-detected", bool(check_no_deletion([1, 2, 3], [1, 3])))
    check("no-deletion-ok", not check_no_deletion([1, 2, 3], [1, 2, 3, 4]))

    # append INTO a released span rejected; a tail id is fine
    check("append-into-released-rejected", bool(check_no_append_into_released(frozen_ver, [2])))
    check("append-into-tail-ok", not check_no_append_into_released(frozen_ver, [3, 4]))

    # --- 4 & 6: a RELEASE CUT freezes the tail, appends the row, empties the new tail -----------------
    cut = release_cut(vok, worklog, "1.1.0", "2026-06-15T00:00:00Z")
    check("cut-valid", cut.status == VALID)
    check("cut-froze-tail", cut.frozen_span == ["WL-3", "WL-4"])
    check("cut-appended-row", len(cut.version_data["release"]) == 2)
    check("cut-row-version", cut.version_data["release"][-1]["version"] == "1.1.0")
    check("cut-digest-matches", cut.coverage_digest == compute_span_digest(by_id, (3, 4)))
    # 6: after the cut the new unreleased tail is EMPTY (spec 6.2)
    check("cut-empty-new-tail", tail_ids(cut.version_data["release"], by_id.keys()) == [])
    # the new ledger tiles and validates cleanly, and its frozen coverage recomputes
    check("cut-ledger-valid", validate_version(cut.version_data).status == VALID)
    check("cut-frozen-recompute", not check_frozen_coverage(cut.version_data, by_id))

    # a cut with a NON-MONOTONIC new version fails closed
    check("cut-non-monotonic-invalid", release_cut(vok, worklog, "0.9.0", "2026-06-15T00:00:00Z").status == INVALID)
    check("cut-duplicate-version-invalid", release_cut(vok, worklog, "1.0.0", "2026-06-15T00:00:00Z").status == INVALID)
    # a cut from an inconsistent ledger fails closed (does not compute a cut)
    check("cut-inconsistent-ledger-invalid", release_cut(v_nonmono, worklog, "3.0.0", "2026-06-15T00:00:00Z").status == INVALID)

    # --- empty-span release: a cut with no new worklog entries yields an empty span -------------------
    worklog_all = {"entry": [entry(1), entry(2), entry(3), entry(4)]}
    empty_cut = release_cut(cut.version_data, worklog_all, "1.2.0", "2026-07-01T00:00:00Z")
    check("empty-cut-valid", empty_cut.status == VALID)
    check("empty-cut-empty-span", empty_cut.frozen_span == [])
    check("empty-cut-digest", empty_cut.coverage_digest == coverage_digest([]))
    empty_ver = {"release": [{"version": "1.0.0", "date": "2026-06-01T00:00:00Z",
                             "worklog_span": [], "coverage_digest": coverage_digest([])}]}
    check("empty-span-release-valid", validate_version(empty_ver).status == VALID)

    # --- tiling gap / overlap ------------------------------------------------------------------------
    gap = {"release": [
        {"version": "1.0.0", "date": "2026-06-01T00:00:00Z", "worklog_span": ["WL-1", "WL-2"], "coverage_digest": dig12},
        {"version": "1.1.0", "date": "2026-06-02T00:00:00Z", "worklog_span": ["WL-4", "WL-5"], "coverage_digest": "sha256:" + "0" * 64},
    ]}
    check("tiling-gap-invalid", validate_version(gap).status == INVALID)
    overlap = {"release": [
        {"version": "1.0.0", "date": "2026-06-01T00:00:00Z", "worklog_span": ["WL-1", "WL-3"], "coverage_digest": "sha256:" + "0" * 64},
        {"version": "1.1.0", "date": "2026-06-02T00:00:00Z", "worklog_span": ["WL-3", "WL-5"], "coverage_digest": "sha256:" + "0" * 64},
    ]}
    check("tiling-overlap-invalid", validate_version(overlap).status == INVALID)
    not_start_one = {"release": [
        {"version": "1.0.0", "date": "2026-06-01T00:00:00Z", "worklog_span": ["WL-2", "WL-3"], "coverage_digest": "sha256:" + "0" * 64},
    ]}
    check("tiling-not-start-one-invalid", validate_version(not_start_one).status == INVALID)

    # --- canonicalization determinism: reorder-invariant across the array AND within a table ----------
    a = entry(1, summary="one", links=[{"rel": "resolves", "id": "BI-1"}])
    b = entry(2, summary="two")
    # same entries in reverse order -> same digest (entries sorted by WL-number)
    check("digest-array-reorder-invariant", coverage_digest([a, b]) == coverage_digest([b, a]))
    # a table with the same key/values in a different insertion order -> same digest (keys sorted)
    a_reordered = {"summary": "one", "kind": "added", "actor": {"kind": "maintainer"},
                   "date": a["date"], "id": "WL-1", "links": [{"id": "BI-1", "rel": "resolves"}]}
    check("digest-key-reorder-invariant", coverage_digest([a]) == coverage_digest([a_reordered]))
    # a content change DOES change the digest
    check("digest-content-sensitive", coverage_digest([a]) != coverage_digest([entry(1, summary="CHANGED")]))
    # canonicalization fails CLOSED on an unexpected value type (None is not a TOML/worklog value)
    try:
        _canonical({"bad": None})
        check("canonical-fails-closed", False)
    except ReleaseError:
        check("canonical-fails-closed", True)

    # --- M2: a FINITE float in a registered extension digests cleanly; a NON-FINITE float fails closed ---
    # A finite float is spelled with U8's shared rule (digest and emitter agree byte-for-byte, spec 8.7).
    check("m2-finite-float-canonical-ok", _canonical(1.5) == repr(1.5))
    check("m2-finite-float-signed-zero-canonical", _canonical(-0.0) == _canonical(0.0))
    # end-to-end: a worklog entry carrying a finite float in a registered extension cuts VALID (before the
    # fix _canonical raised on the float and the cut returned CANNOT-EVALUATE).
    wl_float = {"schema": 1, "entry": [entry(1), entry(2), entry(3),
                                       entry(4, **{"x-acme": {"score": 1.5}})]}
    cut_float = release_cut(vok, wl_float, "1.1.0", "2026-06-15T00:00:00Z",
                            registered_vendors=frozenset({"x-acme"}))
    check("m2-cut-with-finite-float-ok", cut_float.status == VALID)
    # a non-finite float has no canonical form and fails closed (ReleaseError), fed to the cut as
    # CANNOT-EVALUATE via compute_span_digest.
    nonfinite_failclosed = True
    for bad in (float("inf"), float("-inf"), float("nan")):
        try:
            _canonical(bad)
            nonfinite_failclosed = False
        except ReleaseError:
            pass
    check("m2-nonfinite-float-failclosed", nonfinite_failclosed)
    wl_nonfinite = {"schema": 1, "entry": [entry(1), entry(2), entry(3),
                                           entry(4, **{"x-acme": {"score": float("inf")}})]}
    check("m2-cut-with-nonfinite-float-cannot-eval",
          release_cut(vok, wl_nonfinite, "1.1.0", "2026-06-15T00:00:00Z",
                      registered_vendors=frozenset({"x-acme"})).status == CANNOT_EVALUATE)

    # --- rotation is archival movement only ----------------------------------------------------------
    check("rotation-partition-ok", not check_ids_partition([3, 4], [1, 2]))
    check("rotation-partition-dup-invalid", bool(check_ids_partition([2, 3], [1, 2])))
    check("rotation-only-released-ok", not check_rotation_only_released([1, 2], frozen_ver))
    check("rotation-tail-invalid", bool(check_rotation_only_released([3], frozen_ver)))

    # --- worklog / version fail-closed on non-tables --------------------------------------------------
    check("worklog-not-table-cannot-eval", validate_worklog([]).status == CANNOT_EVALUATE)
    check("version-not-table-cannot-eval", validate_version([]).status == CANNOT_EVALUATE)
    check("worklog-bad-entry-invalid", validate_worklog({"entry": [{"id": "WL-1"}]}).status == INVALID)
    check("summary-covers-unknown-invalid", validate_version(
        {"release": [], "summary": [{"covers": "9.9.9", "status": "working"}]}).status == INVALID)
    check("summary-published-needs-digest-invalid", validate_version(
        {"release": [{"version": "1.0.0", "date": "2026-06-01T00:00:00Z", "worklog_span": [],
                      "coverage_digest": coverage_digest([])}],
         "summary": [{"covers": "1.0.0", "status": "published"}]}).status == INVALID)

    # --- B2 (reverted M7 overreach): release_cut is a PURE per-ledger cut; prior-frozen integrity across
    # the archive is the store validator's, not the cut's. In a NON-ROTATED store (every prior released
    # entry still in the active worklog) the cut assumes prior frozen spans intact and proceeds; when the
    # store is ROTATED (a prior released id absent from the active worklog) the cut returns CANNOT-EVALUATE
    # and directs the caller to the store validator, rather than silently skipping the check (fail-closed
    # seam). NOTE: detecting an EDITED (not moved) prior-frozen entry in a NON-ROTATED store is enforced by
    # the cut ITSELF (FIX 1 below): it recomputes frozen coverage over the spans it can see and returns
    # INVALID. Only the ROTATED/DELETED case (a prior released id absent from the active worklog, where an
    # edit cannot be told from a move) is deferred to the store validator (PASS B).
    check("cut-non-rotated-intact-ok",
          release_cut(vok, worklog, "1.1.0", "2026-06-15T00:00:00Z").status == VALID)
    # FIX 1: a NON-ROTATED store whose already-frozen WL-1 was EDITED after freeze must NOT cut VALID. The
    # 1.0.0 span covers WL-1..WL-2 with dig12 (the original content); editing WL-1 changes its recomputed
    # coverage digest, so the cut now enforces frozen coverage over the spans it can see and returns
    # INVALID (before the fix it certified VALID, deferring the check to a store validator that does not
    # yet exist).
    wl_frozen_edited = {"schema": 1, "entry": [entry(1, summary="EDITED after freeze"),
                                               entry(2), entry(3), entry(4)]}
    cut_frozen_edited = release_cut(vok, wl_frozen_edited, "1.1.0", "2026-06-15T00:00:00Z")
    check("cut-frozen-mutation-invalid", cut_frozen_edited.status == INVALID)
    check("cut-frozen-mutation-named",
          any("frozen released span was mutated" in f for f in cut_frozen_edited.findings))
    # WL-1,2 are the prior released span (1.0.0); a store that rotated them out of the active worklog is
    # rotated, so the cut cannot verify their frozen integrity alone and fails closed to CANNOT-EVALUATE.
    active_rotated = {"schema": 1, "entry": [entry(3), entry(4)]}
    check("cut-rotated-store-cannot-eval",
          release_cut(vok, active_rotated, "1.1.0", "2026-06-15T00:00:00Z").status == CANNOT_EVALUATE)
    # a deleted prior-released entry is indistinguishable from a rotation to a per-ledger cut, so it too
    # fails closed to CANNOT-EVALUATE (never a silent VALID); the store validator separates the two cases
    # (no-deletion across active+archive; PASS B).
    worklog_del = {"schema": 1, "entry": [entry(2), entry(3), entry(4)]}
    check("cut-missing-prior-released-cannot-eval",
          release_cut(vok, worklog_del, "1.1.0", "2026-06-15T00:00:00Z").status == CANNOT_EVALUATE)

    # --- B3: release rows are append-only; a rewritten historical row fails closed --------------------
    prior_rows = list(vok["release"])
    new_row_ok = {"version": "1.1.0", "date": "2026-06-15T00:00:00Z",
                  "worklog_span": ["WL-3", "WL-4"], "coverage_digest": compute_span_digest(by_id, (3, 4))}
    af_clean = []
    _verify_append_only(prior_rows, prior_rows + [new_row_ok], af_clean)
    check("append-only-clean-ok", not af_clean)
    rewritten = [dict(prior_rows[0], worklog_span=["WL-1", "WL-3"])]   # historical 1.0.0 span rewritten
    af_rewrite = []
    _verify_append_only(prior_rows, rewritten + [new_row_ok], af_rewrite)
    check("append-only-rewrite-detected", bool(af_rewrite))
    af_shrink = []
    _verify_append_only(prior_rows, [], af_shrink)                     # ledger shrank (row dropped)
    check("append-only-shrink-detected", bool(af_shrink))
    # the real cut's output is append-only over the prior ledger
    af_cut = []
    _verify_append_only(prior_rows, cut.version_data["release"], af_cut)
    check("cut-output-append-only", not af_cut)

    # --- M7: the guard helpers fail closed on an unreadable-shaped version ledger ---------------------
    check("frozen-coverage-nonlist-ledger-cannot-eval", bool(check_frozen_coverage([], {})))
    check("frozen-coverage-release-not-list-cannot-eval", bool(check_frozen_coverage({"release": "x"}, {})))
    check("append-into-released-nondict-ledger-cannot-eval", bool(check_no_append_into_released(42, [3])))
    check("rotation-only-released-nondict-ledger-cannot-eval", bool(check_rotation_only_released([3], [])))
    try:
        released_end("not-a-list")
        check("released-end-nonlist-fails-closed", False)
    except ReleaseError:
        check("released-end-nonlist-fails-closed", True)
    # control: a well-formed ledger still evaluates cleanly (no over-rejection)
    check("frozen-coverage-good-ledger-ok", not check_frozen_coverage(frozen_ver, by_id))

    # --- M3: released_end and the standalone guards fail closed on a MALFORMED SPAN or UNSUPPORTED ------
    # SCHEMA ledger, never returning a silent under-computed end / [] that reads as an unreadable ledger
    # being clean (the check-fails-closed-on-unreadable rule).
    bad_span_releases = [{"version": "1.0.0", "date": "2026-06-01T00:00:00Z",
                         "worklog_span": ["WL-1", "oops"], "coverage_digest": dig12}]
    bad_span_ver = {"release": bad_span_releases}
    try:
        released_end(bad_span_releases)
        check("m3-released-end-malformed-span-fails-closed", False)
    except ReleaseError:
        check("m3-released-end-malformed-span-fails-closed", True)
    # append with a lone malformed span: under the bug the end under-computes to 0 and candidate WL-3 reads
    # as a clean tail id (a FALSE clean); the fix fails closed.
    check("m3-append-malformed-span-cannot-eval",
          bool(check_no_append_into_released(bad_span_ver, [3])))
    # rotation needs a VALID span alongside the malformed one so the bug (end=5, dropping the bad span)
    # reads rotating WL-3 as clean; the fix fails closed instead of judging against an under-computed end.
    mixed_span_ver = {"release": [
        {"version": "1.0.0", "date": "2026-06-01T00:00:00Z",
         "worklog_span": ["WL-1", "WL-5"], "coverage_digest": "sha256:" + "0" * 64},
        {"version": "1.1.0", "date": "2026-06-02T00:00:00Z",
         "worklog_span": ["WL-6", "oops"], "coverage_digest": dig12}]}
    check("m3-rotation-malformed-span-cannot-eval",
          bool(check_rotation_only_released([3], mixed_span_ver)))
    check("m3-frozen-coverage-malformed-span-cannot-eval",   # control: already caught per-row by _parse_span
          bool(check_frozen_coverage(bad_span_ver, by_id)))
    unsupported_schema_ver = {"schema": 999, "release": []}
    check("m3-frozen-coverage-bad-schema-cannot-eval",
          bool(check_frozen_coverage(unsupported_schema_ver, {})))
    check("m3-append-bad-schema-cannot-eval",
          bool(check_no_append_into_released(unsupported_schema_ver, [3])))
    # rotation needs a VALID span under the bad schema so that, WITHOUT the schema gate, rotating WL-3
    # (within WL-1..WL-5) reads as clean; the schema gate fails it closed to cannot-eval instead.
    bad_schema_span_ver = {"schema": 999, "release": [
        {"version": "1.0.0", "date": "2026-06-01T00:00:00Z",
         "worklog_span": ["WL-1", "WL-5"], "coverage_digest": "sha256:" + "0" * 64}]}
    check("m3-rotation-bad-schema-cannot-eval",
          bool(check_rotation_only_released([3], bad_schema_span_ver)))
    # control: a good ledger still evaluates cleanly (no over-rejection)
    check("m3-good-ledger-guards-ok",
          not check_no_append_into_released(frozen_ver, [3, 4])
          and not check_rotation_only_released([1, 2], frozen_ver))

    # --- M8: a schema field other than the supported version fails closed -----------------------------
    check("version-bad-schema-invalid", validate_version({"schema": 999, "release": []}).status == INVALID)
    check("worklog-bad-schema-invalid", validate_worklog({"schema": 999, "entry": []}).status == INVALID)
    check("version-good-schema-ok", validate_version({"schema": 1, "release": []}).status == VALID)

    # --- M4: a manifest-registered worklog kind validates through validate_worklog --------------------
    wl_custom = {"entry": [entry(1, kind="perf")]}
    check("release-worklog-manifest-kind-ok",
          validate_worklog(wl_custom, registered_kinds=["perf"]).status == VALID)
    check("release-worklog-manifest-kind-unregistered-invalid",
          validate_worklog(wl_custom).status == INVALID)

    # ----- round-2 spec-conformance hardening -------------------------------------------------------
    # M6: release_cut threads registered kinds/vendors into validate_worklog (a manifest-registered kind or
    # x-<vendor> on a tail entry validates through the cut; unregistered fails).
    worklog_perf = {"schema": 1, "entry": [entry(1), entry(2), entry(3, kind="perf"), entry(4)]}
    check("m6-cut-registered-kind-ok",
          release_cut(vok, worklog_perf, "1.1.0", "2026-06-15T00:00:00Z",
                      registered_kinds=["perf"]).status == VALID)
    check("m6-cut-unregistered-kind-invalid",
          release_cut(vok, worklog_perf, "1.1.0", "2026-06-15T00:00:00Z").status == INVALID)
    worklog_vendor = {"schema": 1, "entry": [entry(1), entry(2), entry(3),
                                             entry(4, **{"x-aiqt": {"note": "n"}})]}
    check("m6-cut-registered-vendor-ok",
          release_cut(vok, worklog_vendor, "1.1.0", "2026-06-15T00:00:00Z",
                      registered_vendors=frozenset({"x-aiqt"})).status == VALID)
    check("m6-cut-unregistered-vendor-invalid",
          release_cut(vok, worklog_vendor, "1.1.0", "2026-06-15T00:00:00Z").status == INVALID)

    # M7 archived-entries path REVERTED: release_cut no longer accepts a raw archive iterable (a pure
    # per-ledger cut cannot tell a conforming rotation from an ad-hoc disappearance). A cut against a
    # rotated store now fails closed to CANNOT-EVALUATE (see the B2 "cut-rotated-store-cannot-eval" leg);
    # the M6 registered-kinds/vendors threading above STAYS. Store-wide frozen integrity across
    # active+archive is the store validator's (PASS B).

    # M8: the frozen-span guards fail closed on a malformed (non-table) release ROW, never silently skip it.
    check("m8-frozen-coverage-nontable-row-cannot-eval",
          bool(check_frozen_coverage({"release": [42]}, {})))
    good_row = {"version": "1.0.0", "date": "2026-06-01T00:00:00Z",
                "worklog_span": [], "coverage_digest": coverage_digest([])}
    check("m8-append-into-released-nontable-row-cannot-eval",
          bool(check_no_append_into_released({"release": [good_row, 42]}, [3])))
    check("m8-rotation-only-released-nontable-row-cannot-eval",
          bool(check_rotation_only_released([3], {"release": [42]})))
    # control: a well-formed all-table ledger still evaluates cleanly (no over-rejection)
    check("m8-good-rows-ok", not check_frozen_coverage(frozen_ver, by_id))

    # M9: check_ids_partition detects LOSS given the authoritative expected-id set (1..high-water).
    check("m9-partition-loss-detected", bool(check_ids_partition([1], [], expected_ids=range(1, 3))))
    check("m9-partition-no-loss-ok", not check_ids_partition([1, 2], [], expected_ids=range(1, 3)))
    check("m9-partition-loss-across-active-archive-ok",
          not check_ids_partition([2], [1], expected_ids=range(1, 3)))
    check("m9-partition-no-expected-backward-compat", not check_ids_partition([1], []))

    # M10: a superseded summary's superseded_by must name an EXISTING rollup that COVERS it, never itself.
    D = "sha256:" + "a" * 64
    EMPTY = coverage_digest([])
    sup_releases = [
        {"version": "1.0.0", "date": "2026-06-01T00:00:00Z", "worklog_span": [], "coverage_digest": EMPTY},
        {"version": "1.1.0", "date": "2026-06-02T00:00:00Z", "worklog_span": [], "coverage_digest": EMPTY},
    ]
    sup_base = {"release": sup_releases, "summary": [
        {"covers": "unreleased", "status": "working"},
        {"covers": "1.0.0", "status": "superseded", "digest": D, "superseded_by": "1.0.0..1.1.0"},
        {"covers": "1.1.0", "status": "superseded", "digest": D, "superseded_by": "1.0.0..1.1.0"},
        {"covers": "1.0.0..1.1.0", "status": "published", "digest": D},
    ]}
    check("m10-valid-supersession-ok", validate_version(sup_base).status == VALID)
    sup_self = {"release": sup_releases, "summary": [
        {"covers": "1.0.0", "status": "superseded", "digest": D, "superseded_by": "1.0.0"}]}
    check("m10-self-reference-invalid", validate_version(sup_self).status == INVALID)
    sup_missing = {"release": sup_releases, "summary": [
        {"covers": "1.0.0", "status": "superseded", "digest": D, "superseded_by": "1.0.0..1.1.0"}]}
    check("m10-nonexistent-rollup-invalid", validate_version(sup_missing).status == INVALID)
    sup_notcover = {"release": sup_releases, "summary": [
        {"covers": "1.1.0", "status": "superseded", "digest": D, "superseded_by": "1.0.0"},
        {"covers": "1.0.0", "status": "published", "digest": D}]}
    check("m10-rollup-not-covering-invalid", validate_version(sup_notcover).status == INVALID)

    # m3: check_rotation_only_released rejects a non-positive rotated WL-number (WL-0); the sibling
    # check_no_append_into_released rejects it too.
    check("m3-rotation-nonpositive-invalid", bool(check_rotation_only_released([0], frozen_ver)))
    check("m3-rotation-negative-invalid", bool(check_rotation_only_released([-1], frozen_ver)))
    check("m3-append-nonpositive-invalid", bool(check_no_append_into_released(frozen_ver, [0])))

    # --- retro fixes: discriminating vectors for the three confirmed defects ---------------------
    # BLOCKER (RANGE-BOUNDS): a single large-id tail entry is judged by COUNT, not by materializing a
    # range sized by the declared max id. Empty ledger + WL-1000000000: the tail is not contiguous from
    # WL-1, so the cut returns a controlled INVALID in O(1). Before the fix release_cut built
    # list(range(1, 1000000001)) and raised an uncaught MemoryError, never reaching this INVALID.
    wl_bigid = {"schema": 1, "entry": [entry(1000000000)]}
    bigid_cut = release_cut({"release": []}, wl_bigid, "1.0.0", "2026-06-15T00:00:00Z")
    check("blocker-large-id-tail-bounded-invalid", bigid_cut.status == INVALID)
    # MINOR (SELF-TEST DISCRIMINATION): a reversed span in a NON-FIRST position (start > end) tiles its
    # start against the cursor but drives coverage backward; the _parse_span a > b guard is the sole
    # layer against it. Pin the guard: without it this ledger fails OPEN to VALID.
    reversed_span_ver = {"release": [
        {"version": "1.0.0", "date": "2026-06-01T00:00:00Z",
         "worklog_span": ["WL-1", "WL-2"], "coverage_digest": dig12},
        {"version": "1.1.0", "date": "2026-06-02T00:00:00Z",
         "worklog_span": ["WL-3", "WL-1"], "coverage_digest": "sha256:" + "0" * 64}]}
    check("reversed-span-non-first-invalid", validate_version(reversed_span_ver).status == INVALID)
    # MAJOR (FAIL-OPEN): superseded_by must not name the working 'unreleased' tail. With an
    # 'unreleased' summary row present the token sits in covers_index and _covers_range returns None,
    # so the covering check is skipped and the row failed OPEN to VALID before the fix; now INVALID.
    sup_by_unreleased = {"release": sup_releases, "summary": [
        {"covers": "unreleased", "status": "working"},
        {"covers": "1.0.0", "status": "superseded", "digest": D, "superseded_by": "unreleased"}]}
    check("m10-superseded-by-unreleased-invalid", validate_version(sup_by_unreleased).status == INVALID)

    # ----- round-2 fix-forward QA: reconciled NEW/residual findings ---------------------------------
    # BLOCKER (RANGE-BOUNDS): the expected-id LOSS check is bounded by the PRESENT set, never by the
    # declared high-water. A range 1..10^9 (a spoofed WL-1000000000 store entry) reports loss in O(1);
    # before the fix _wl_id_set(expected_ids) materialized it into a set and raised an uncaught MemoryError.
    huge_expected = check_ids_partition([1], [], expected_ids=range(1, 1000000001))
    check("blocker-partition-huge-expected-bounded", bool(huge_expected))
    check("partition-huge-expected-still-detects-loss",
          not check_ids_partition([1], [], expected_ids=range(1, 2)))

    # MAJOR (FAIL-OPEN, format gate): a trailing newline no longer passes the digest / SemVer regex
    # (`$` matched before a final newline; `\Z` anchors the whole string).
    check("digest-trailing-newline-rejected", not _valid_digest("sha256:" + "0" * 64 + "\n"))
    check("semver-trailing-newline-rejected", parse_semver("1.0.0\n") is None)
    check("semver-valid-still-parses", parse_semver("1.0.0") is not None)

    # MAJOR (fail-CRASH -> fail-closed): an oversized non-decimal int status renders through _safe_display,
    # so validate_version returns INVALID instead of an uncontrolled ValueError from repr().
    big_int_status = int("f" * 4000, 16)   # a >4300-decimal-digit int; repr() trips CPython's limit
    check("summary-oversized-int-status-invalid", validate_version(
        {"release": [], "summary": [{"covers": "unreleased", "status": big_int_status}]}).status == INVALID)

    # MAJOR (fail-CRASH -> fail-closed): an UNHASHABLE covers value no longer crashes the covering check
    # (`covers in covers_index`); the malformed covers is a finding and the row is INVALID.
    covers_unhashable = {"release": sup_releases, "summary": [
        {"covers": [], "status": "superseded", "digest": D, "superseded_by": "1.0.0..1.1.0"},
        {"covers": "1.0.0..1.1.0", "status": "published", "digest": D}]}
    check("covers-unhashable-invalid", validate_version(covers_unhashable).status == INVALID)

    # MAJOR (FAIL-OPEN): a supersession whose rollup range EQUALS the superseded row's own range (distinct
    # tokens "1.0.0" and "1.0.0..1.0.0" for the same range) is a cycle with no surviving rollup; the
    # token-level self-reference guard missed it, the range-equality guard catches it (INVALID).
    sup_cycle = {"release": sup_releases, "summary": [
        {"covers": "1.0.0", "status": "superseded", "digest": D, "superseded_by": "1.0.0..1.0.0"},
        {"covers": "1.0.0..1.0.0", "status": "superseded", "digest": D, "superseded_by": "1.0.0"}]}
    check("m10-equal-range-supersession-cycle-invalid", validate_version(sup_cycle).status == INVALID)

    # MINOR (fail-closed): tail_ids raises on a non-positive id rather than silently dropping it through
    # the `n > end` filter (which would let a malformed id falsely satisfy an empty-tail assertion).
    try:
        tail_ids([], [0])
        check("tail-ids-nonpositive-fails-closed", False)
    except ReleaseError:
        check("tail-ids-nonpositive-fails-closed", True)

    # MINOR (determinism / house sorted-output): the id-collection guards emit findings in SORTED order
    # regardless of input iteration order (a set input would otherwise be nondeterministic).
    check("wl-id-set-findings-sorted",
          check_no_deletion(["mB", "mA"], []) == sorted(check_no_deletion(["mB", "mA"], [])))
    appf_order = check_no_append_into_released(frozen_ver, [2, 1])
    check("append-findings-sorted", appf_order == sorted(appf_order))
    rotf_order = check_rotation_only_released([4, 3], frozen_ver)
    check("rotation-findings-sorted", rotf_order == sorted(rotf_order))

    # MINOR (self-test discrimination, Fable): pin the load-bearing contiguity clause tail[-1]-end !=
    # len(tail). An interior-gap tail (WL-1, WL-3 over an empty ledger) must cut INVALID with the
    # not-contiguous finding; removing the clause degrades it to CANNOT-EVALUATE with a different message.
    wl_gap_tail = {"schema": 1, "entry": [entry(1), entry(3)]}
    gap_cut = release_cut({"release": []}, wl_gap_tail, "1.0.0", "2026-06-15T00:00:00Z")
    check("cut-interior-gap-tail-invalid", gap_cut.status == INVALID)
    check("cut-interior-gap-tail-named", any("not contiguous" in f for f in gap_cut.findings))

    # --- retro hardening (opf-hardening branch): three whole-file adversarial-QA findings ------------
    # NF-1 (fail-closed soundness): _count_expected_absent's len(expected_ids) raises OverflowError, not
    # TypeError, for a range with >= 2**63 members (a spoofed 19-digit high-water id). Before the fix
    # check_ids_partition crashed uncontrolled here; now it fails closed to a cannot-evaluate finding and
    # reports NO false loss.
    nf1 = check_ids_partition([1], [], expected_ids=range(1, 2**63 + 2))
    check("nf1-oversized-range-len-failclosed", bool(nf1) and not any("NEITHER" in f for f in nf1))
    # NF-2 (oversized-int class closure): three WELL-FORMED-branch findings format an ACCEPTED WL-number
    # via "WL-{}".format(n); an oversized non-decimal int (a TOML hex literal passing _wl_id_set's n >= 1)
    # made str(n) raise ValueError uncaught. Rendered through _safe_str they fail closed instead. `big`
    # trips CPython's base-10 integer-string-conversion limit (> 4300 digits).
    big = int("f" * 4000, 16)
    check("nf2-partition-both-location-oversized-safe",
          any("oversized-int" in f for f in check_ids_partition([big], [big])))
    check("nf2-no-deletion-vanished-oversized-safe",
          any("oversized-int" in f for f in check_no_deletion([big], [])))
    check("nf2-rotation-tail-oversized-safe",
          any("oversized-int" in f for f in check_rotation_only_released([big], frozen_ver)))
    # NF-4 (over-fire regression): _count_expected_absent must not membership-test RAW expected-id
    # elements. A non-range expected_ids (a duplicate list, or an un-normalized "WL-1") reported a FALSE
    # loss before the fix; now it fails closed to a cannot-evaluate finding and reports NO loss.
    nf4_dup = check_ids_partition([1, 2], [], expected_ids=[1, 1, 2])
    check("nf4-duplicate-expected-no-false-loss", not any("NEITHER" in f for f in nf4_dup))
    nf4_norm = check_ids_partition([1], [], expected_ids=["WL-1"])
    check("nf4-unnormalized-expected-no-false-loss", not any("NEITHER" in f for f in nf4_norm))

    if failures:
        print("OPF-RELEASE SELF-TEST: FAIL ({} of {} checks failed)".format(len(failures), checked))
        for f in failures:
            print("  FAILED: {}".format(f))
        return 1
    print("OPF-RELEASE SELF-TEST: PASS ({} version.toml, worklog.toml, tiling, digest, and "
          "release-cut checks)".format(checked))
    return 0


if __name__ == "__main__":
    sys.exit(self_test())
