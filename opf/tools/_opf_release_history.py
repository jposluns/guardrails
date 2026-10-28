#!/usr/bin/env python3
"""OPF release import-history parser and imported-ledger builder (OPF-SPEC 1.3.0 sections 6.1, 7.2, 8.8
and 14.3): pure, canonical and INERT. No command calls parse_history or build_imported_ledger in this
release; they ship ahead of the imported-release writer's command, and the module writes nothing: neither
function performs I/O (no file, clock, environment, network or process; importing the module resolves its
own file path to reach its sibling modules, the changelog parser's first call imports the vendored
CommonMark parser, and the self-test and CLI print their verdicts; the self-test builds its vectors in
memory).

parse_history(data) parses ONE `opf.release.import-history/v1` document, the recorded release instants of
a product's legacy release pipeline, which the imported-release writer turns into imported `version.toml`
rows. The format:

  schema = "opf.release.import-history/v1"   the one format this unit understands (any other is fail-closed)
  [source]                                    exactly one source; `path` is the legacy release pipeline
    path = "release-notes.toml"               file's product-relative canonical path, under the import
                                              batch's [source].path grammar (_opf_import_batch)
  [[release]]                                 one row per released version, at least one and at most
    version = "0.9.0"                         MAX_RELEASE_ROWS; the keys are exactly `version` (SemVer, the
    date = "2025-11-03T14:20:07Z"             ledger's own grammar, rows strictly ascending and unique) and
                                              `date`, both required

`date` is an RFC 3339 UTC timestamp (the ledger's own grammar, _opf_schema._valid_timestamp) taken from a
RECORDED instant, such as the tag or release commit time, never padded from a date-only value. A date-only
value is refused, and so is any value whose instant is UTC midnight (`T00:00:00Z`, its `+00:00` spelling,
or all-zero fractional seconds): a parser cannot tell a recorded midnight from a date padded to one, so the
date must come from a recorded instant.

Everything else on an imported ledger row is DERIVED by the writer, so it is REFUSED here anywhere in the
document (the top level, [source] and every row), each by a named finding: the writer-derived ledger members
`worklog_span`, `coverage_digest`, `imported`, `digest` and `summary`; any caller-supplied size or SHA-256
(spec 8.8: the writer "MUST recompute source size and SHA-256 from preserved bytes, never trusting
caller-supplied measurements"); and any prose or notes key (spec 6.1: the ledger "MUST NOT carry release
prose"; spec 14.3: changelog prose "MUST still come from the curator's act", so per-release notes stay in
CHANGELOG.md, and the writer computes each summary digest from that file's entry). Any other key is an
unknown key of a closed schema.

THE HISTORY FILE IS A PLAN SOURCE, RETIRED AFTER USE. It is neither a store document nor a ledger: the
adoption plan binds it as a source with a disposition, the imported-release writer reads it inside that
plan's run, and the plan retires it after use, so no later run re-reads it and no tool treats it as a
standing record of the product's releases (version.toml is that record). This module carries no code for
the retirement; the plan and the writer own it.

CANONICAL FORM (the opf.record.import-batch/v1 discipline of _opf_import_batch). A history is canonical
when its bytes equal its own canonical re-emission (emit_history): `schema` first, then `[source]`, then
every `[[release]]` in document order; in each table the `version` line first, then the remaining keys
sorted; every value an inline basic string with the batch's fixed escape set (_opf_import_batch's
_canon_string); LF line ends and one blank line before each table header; no comment. A comment, another
key order, another quoting and any other re-spelling is NOT canonical and the history is refused, so one
spelling of a history has one byte form. history_digest is the SHA-256 of the canonical bytes.

BOUNDED PREFLIGHT. A history over MAX_HISTORY_BYTES is refused unread, before decoding; once the schema is
identified, a [[release]] array longer than MAX_RELEASE_ROWS is refused ALONE, one finding, before any row
is graded; and no helper ever recurses into a value: the value grammar is strings only, so every other
value (an integer, a boolean, an array, a table, a native TOML date or date-time) is refused by its type,
never walked and never formatted (a key or string is shown only through _opf_schema._bounded_display).

Outcome model (single-sourced from _opf_store): CANNOT-EVALUATE for an input this unit cannot read under
v1 assumptions (not exactly bytes, over the byte bound, not UTF-8, not TOML, another or absent `schema`),
never a pass; INVALID for a parsed history that violates the format; VALID otherwise, and only then does
the History carry the source path, the rows and the digest.

NOT CHECKED BY parse_history (build_imported_ledger checks the CHANGELOG.md entry of each version and
applies the ledger rules to the rows it builds; the plan and the doctor own the rest): that `source.path`
names a source the adoption plan binds with a disposition, and that the file says what the history says;
that a date is the instant the version was really released, or no later than the run clock; and the order
of dates across rows (rows are ordered by version, and a maintenance release can ship after a later
version). DISCLOSED: the date grammar is the ledger's, so one instant has more than one accepted spelling
(`Z` or `+00:00`, fractional seconds with trailing zeros), and the canonical bytes and the digest are per
spelling.

THE IMPORTED-LEDGER BUILDER. build_imported_ledger(version_data, history, changelog_text) returns
(new_version_data, findings): a new ledger and no finding, or None and the findings, never a partial
ledger. It builds only from a VALID History holding at least one row (each row's version and date are
re-graded by the parser's own rules) and a version ledger validate_version grades VALID, and refuses, each
by a named finding: a ledger holding any witnessed row (imported rows form a prefix of the ledger, spec
6.1); a history version that collides with an existing row by string or by SemVer precedence; changelog
text that is not a str, that the changelog gate's heading scan cannot read, or that carries a heading the
changelog gate grades as a finding; and a release with no CHANGELOG.md entry headed by exactly its version,
or with more than one. It then appends, in history order, one `[[release]]` row per history row (version
and recorded date verbatim, `worklog_span = []`, the empty-coverage digest compute_span_digest({}, None),
`imported = true`) and one `[[summary]]` row (`covers` the version, `status = "published"`, `digest` the
freeze digest of that release's curated CHANGELOG.md entry, `imported = true`), and re-checks the whole new
ledger with validate_version: anything but VALID returns the findings and no ledger. Every digest is
computed here from preserved bytes; no caller-supplied size or digest is read, no prose is written, the
input ledger is never mutated, and no clock is read. NOT CHECKED BY THE BUILDER: the changelog gates over
the whole new ledger (a heading matching no summary row, or entries out of order, is the changelog gate's
finding once the ledger is written); the store write itself, the plan's retirement of the history file
and the doctor's prior-row checks, which the writer's command and the plan own.

Offline, stdlib only, fail-closed. It lives under `opf/tools/` and imports ONLY sibling `opf/tools/`
modules, so the standalone-closure property holds.

Exit convention (the repo's gates and the sibling OPF units): 0 clean, 1 a finding, 2 cannot-evaluate.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: _opf_release_history.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import copy
import hashlib
import re
import tomllib
from pathlib import Path

# Required for direct execution under python3 -I, including an opf-only copy.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _commonmark_headings        # noqa: E402
import _opf_changelog as changelog  # noqa: E402
import _opf_import_batch as batch  # noqa: E402
import _opf_release as release     # noqa: E402
import _opf_schema as schema       # noqa: E402
import _opf_store as store         # noqa: E402

VALID, INVALID, CANNOT_EVALUATE = store.VALID, store.INVALID, store.CANNOT_EVALUATE

# The one history format this unit understands; any other `schema` value is fail-closed, never parsed
# under v1 assumptions.
HISTORY_SCHEMA = "opf.release.import-history/v1"
# Inclusive byte bound on the whole history document, applied before decoding (a disclosed capacity
# limit, not a process sandbox). It mirrors the import batch bound, _opf_import_batch.MAX_BATCH_BYTES.
MAX_HISTORY_BYTES = 4 * 1024 * 1024
# Inclusive bound on the number of [[release]] rows, matching the adoption plan's entry bound
# (_opf_adopt_plan.MAX_ENTRIES).
MAX_RELEASE_ROWS = 10000
# The closed keysets, in canonical emission order where an order applies.
TOP_KEYS = ("schema", "source", "release")
SOURCE_KEYS = frozenset({"path"})
RELEASE_KEYS = frozenset({"version", "date"})
# The ledger members the imported-release writer derives for each row (spec 6.1, 14.3), refused by name
# wherever they appear.
WRITER_DERIVED_KEYS = ("coverage_digest", "digest", "imported", "summary", "worklog_span")
# Caller-supplied measurements, the import batch's own list so the two formats cannot drift (spec 8.8:
# the writer recomputes source size and SHA-256 from preserved bytes).
MEASUREMENT_KEYS = batch.MEASUREMENT_KEYS
# Prose and notes keys, refused by name (spec 6.1: the ledger carries no release prose; spec 14.3:
# changelog prose comes from the curator's act). A key holding `note` or `prose` anywhere in its
# casefolded spelling is a prose key too; any other key is refused as unknown in any case.
PROSE_KEYS = ("body", "changelog", "description", "message", "note", "notes", "prose", "text", "title")
# A date-only RFC 3339 full-date, refused by name: the date must come from a recorded instant.
_DATE_ONLY_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")

_NOT_BYTES = "release history must be exactly bytes (fail-closed)"


class HistoryRow:
    """One graded [[release]] row: the SemVer `version` and the RFC 3339 UTC `date`, verbatim."""
    __slots__ = ("version", "date")

    def __init__(self, version, date):
        self.version = version
        self.date = date


class History:
    __slots__ = ("status", "findings", "source_path", "rows", "history_digest")

    def __init__(self, status, findings=None, source_path=None, rows=(), history_digest=None):
        self.status = status                  # VALID / INVALID / CANNOT_EVALUATE
        self.findings = findings or []
        self.source_path = source_path        # the legacy release pipeline file, only when VALID
        self.rows = rows                      # tuple of HistoryRow in document order, only when VALID
        self.history_digest = history_digest  # SHA-256 hex of the canonical bytes, only when VALID


class _CanonError(ValueError):
    """A document the canonical emitter refuses (INVALID, fail-closed): emit_history never emits a best
    effort. A ValueError, so a direct caller refusing on ValueError refuses this too."""


# --- canonical emission --------------------------------------------------------------------------------

def _canon_scalar(key, value):
    """The canonical spelling of one value: an inline basic string, the batch's own spelling. Any other
    type is refused by its type name alone, never formatted or walked."""
    if type(value) is not str:
        raise _CanonError("the history has no canonical form: {} is a {} value (v1 values are inline basic "
                          "strings only; a date is an RFC 3339 UTC string)".format(
                              schema._bounded_display(key), type(value).__name__))
    return batch._canon_string(value)


def _canon_table_lines(table):
    # A non-string key has no TOML spelling; it is refused by its type before sorting could raise.
    if any(type(k) is not str for k in table):
        raise _CanonError("the history has no canonical form: a table holds a key that is not a string")
    order = [k for k in ("version",) if k in table]
    order += sorted(k for k in table if k != "version")
    return "".join("{} = {}\n".format(batch._canon_key(k), _canon_scalar(k, table[k])) for k in order)


def emit_history(doc):
    """The canonical emission of a parsed history document (see the module docstring). Raises _CanonError
    on a document outside the emittable shape or value grammar; parse_history grades that refusal, and a
    direct caller must treat it as a refused history, never emit a best effort. A top-level key outside the
    format is refused, never dropped, and so is a string no UTF-8 can encode (a lone surrogate)."""
    if type(doc) is not dict or type(doc.get("source")) is not dict:
        raise _CanonError("the history has no canonical form: it is not a table holding the [source] table")
    if any(type(k) is not str or k not in TOP_KEYS for k in doc):
        raise _CanonError("the history has no canonical form: it holds a top-level key outside schema, "
                          "source and release")
    rows = doc.get("release", [])
    if type(rows) is not list or any(type(row) is not dict for row in rows):
        raise _CanonError("the history has no canonical form: [[release]] is not an array of tables")
    pieces = ["schema = {}\n".format(_canon_scalar("schema", doc.get("schema")))]
    pieces.append("\n[source]\n" + _canon_table_lines(doc["source"]))
    for row in rows:
        pieces.append("\n[[release]]\n" + _canon_table_lines(row))
    try:
        return "".join(pieces).encode("utf-8")
    except UnicodeEncodeError:
        raise _CanonError("the history has no canonical form: a key or value holds a code point UTF-8 "
                          "cannot encode (a lone surrogate)") from None


# --- parsing -------------------------------------------------------------------------------------------

def _is_prose_key(key):
    folded = key.casefold()
    return folded in PROSE_KEYS or "note" in folded or "prose" in folded


def _closed_keys(table, where, allowed, findings):
    """The closed-keyset walk shared by the top level, the source table and each row, with the dedicated
    refusals first, so a writer-derived member, a caller-supplied measurement or a prose key is named as
    such, never as a mere unknown key."""
    for key in table:
        if key in allowed:
            continue
        if key in WRITER_DERIVED_KEYS:
            findings.append("{} carries {!r}, which the imported-release writer derives (spec 6.1, 14.3): a "
                            "history supplies only each release's version and recorded date".format(where, key))
        elif key in MEASUREMENT_KEYS:
            findings.append("{} {}".format(where, batch._MEASUREMENT_REFUSAL.format(key)))
        elif _is_prose_key(key):
            findings.append("{} carries the prose key {}: release prose stays in CHANGELOG.md, the curator's "
                            "act (spec 6.1, 14.3)".format(where, schema._bounded_display(key)))
        else:
            findings.append("{} unknown key {} (the history schema is closed)".format(
                where, schema._bounded_display(key)))


def _is_utc_midnight(value):
    """True when a valid RFC 3339 UTC timestamp names exactly UTC midnight, in any accepted spelling."""
    key = schema._instant_key(value)
    return key is not None and key[3:] == (0, 0, 0, "")


def _date_error(date, where):
    """None when `date` is a legal history date, else the finding."""
    if type(date) is not str:
        return ("{} date must be an RFC 3339 UTC string, not a {} value (a native TOML date or date-time "
                "has no canonical v1 form)".format(where, type(date).__name__))
    if _DATE_ONLY_RE.match(date) is not None:
        return ("{} date {} is a date-only value: the date must come from a recorded instant (the tag or "
                "release commit time), never padded from a date".format(where, schema._bounded_display(date)))
    if not schema._valid_timestamp(date):
        return "{} date {} is not an RFC 3339 UTC timestamp (spec 6.1)".format(
            where, schema._bounded_display(date))
    if _is_utc_midnight(date):
        return ("{} date {} is UTC midnight, which cannot be told from a date padded to midnight: the date "
                "must come from a recorded instant".format(where, schema._bounded_display(date)))
    return None


def _preflight_error(doc):
    """The bounded preflight run once the schema is identified and before any row is graded: a
    [[release]] array past MAX_RELEASE_ROWS is refused ALONE, one finding. Returns the refusing History,
    or None."""
    rows = doc.get("release")
    if type(rows) is list and len(rows) > MAX_RELEASE_ROWS:
        return History(INVALID, ["the history holds {} release rows, over the {} row bound "
                                 "(fail-closed)".format(len(rows), MAX_RELEASE_ROWS)])
    return None


def parse_history(data):
    """Parse and validate one history document (see the module docstring). Returns a History; its source
    path, rows and digest are set only when the status is VALID, so a refused history never yields rows or
    a digest. Pure: no file, clock, environment, network or process is read or touched."""
    if type(data) is not bytes:
        # Exactly bytes, by type identity, with one fixed message that never runs a method of the refused
        # object (the import batch's entry-gate stance).
        return History(CANNOT_EVALUATE, [_NOT_BYTES])
    if len(data) > MAX_HISTORY_BYTES:
        return History(CANNOT_EVALUATE, [
            "release history is {} bytes, over the {} byte bound (fail-closed)".format(
                len(data), MAX_HISTORY_BYTES)])
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        return History(CANNOT_EVALUATE, ["release history is not UTF-8 (fail-closed): {}".format(exc)])
    try:
        doc = tomllib.loads(text)
    except (ValueError, RecursionError, MemoryError) as exc:
        return History(CANNOT_EVALUATE, ["release history is not parseable TOML (fail-closed): "
                                         "{}".format(schema._bounded_display(str(exc)))])
    if doc.get("schema") != HISTORY_SCHEMA:
        return History(CANNOT_EVALUATE, [
            "release history schema {} is not {!r}: another format is never parsed under v1 assumptions "
            "(fail-closed)".format(schema._bounded_display(doc.get("schema")), HISTORY_SCHEMA)])
    refused = _preflight_error(doc)
    if refused is not None:
        return refused

    findings = []
    _closed_keys(doc, "history", frozenset(TOP_KEYS), findings)

    # Exactly one source (TOML cannot spell two [source] tables, so presence and shape are the whole check).
    src = doc.get("source")
    source_path = None
    if type(src) is not dict:
        findings.append("missing required table: [source] (exactly one source per history)")
    else:
        _closed_keys(src, "source", SOURCE_KEYS, findings)
        if "path" not in src:
            findings.append("missing required field: source.path")
        else:
            err = batch._source_path_error(src.get("path"))
            if err is not None:
                findings.append(err)
            else:
                source_path = src.get("path")

    arr = doc.get("release", [])
    if type(arr) is not list:
        findings.append("release must be an array of tables ([[release]])")
        arr = []
    elif not arr:
        findings.append("the history holds no release row: a history records at least one release")

    rows = []
    seen_versions = set()
    prev_key = None
    for i, row in enumerate(arr):
        where = "release[{}]".format(i)
        if type(row) is not dict:
            findings.append("{} must be a table".format(where))
            continue
        _closed_keys(row, where, RELEASE_KEYS, findings)
        version = row.get("version")
        key = None
        if "version" not in row:
            findings.append("{} missing required field: version".format(where))
        elif type(version) is not str:
            findings.append("{} version must be a SemVer string, not a {} value".format(
                where, type(version).__name__))
        else:
            key = release.parse_semver(version)
            if key is None:
                findings.append("{} version {} is not a valid SemVer string (spec 6.1)".format(
                    where, schema._bounded_display(version)))
            elif version in seen_versions:
                findings.append("{} version {} repeats an earlier row's version: versions are unique "
                                "(spec 6.1)".format(where, schema._bounded_display(version)))
            elif prev_key is not None and not prev_key < key:
                findings.append("{} version {} is not strictly greater than the previous row's version: rows "
                                "are strictly ascending (spec 6.1)".format(where, schema._bounded_display(version)))
        if key is not None:
            seen_versions.add(version)
            prev_key = key if (prev_key is None or prev_key < key) else prev_key
        date = row.get("date")
        date_err = None
        if "date" not in row:
            date_err = "{} missing required field: date".format(where)
        else:
            date_err = _date_error(date, where)
        if date_err is not None:
            findings.append(date_err)
        if key is not None and date_err is None:
            rows.append(HistoryRow(version, date))

    canonical = None
    try:
        canonical = emit_history(doc)
    except _CanonError as exc:
        findings.append(str(exc))
    if canonical is not None and canonical != data:
        findings.append("the history is not canonical: its canonical re-emission differs from the input at "
                        "byte {} (re-emitted bytes must equal the input; see the module docstring)".format(
                            batch._first_difference(canonical, data)))

    if findings:
        return History(INVALID, findings)
    return History(VALID, [], source_path, tuple(rows), hashlib.sha256(canonical).hexdigest())


# --- the imported-ledger builder -------------------------------------------------------------------------

def _history_findings(history):
    """The findings that refuse `history` as builder input: it must be a VALID History, as parse_history
    returns it, holding at least one HistoryRow. Each row's version and date are re-graded by the parser's
    own rules, so a hand-built History cannot carry a row parse_history refuses."""
    if type(history) is not History or history.status != VALID:
        return ["the history is not a VALID parsed history (build only from parse_history's VALID result)"]
    rows = history.rows
    if type(rows) is not tuple or any(type(row) is not HistoryRow for row in rows):
        return ["the history rows are not a tuple of HistoryRow (build only from parse_history's VALID result)"]
    if not rows:
        return ["the history holds no release row: a history records at least one release"]
    findings = []
    for i, row in enumerate(rows):
        where = "history release[{}]".format(i)
        if release.parse_semver(row.version) is None:
            findings.append("{} version {} is not a valid SemVer string (spec 6.1)".format(
                where, schema._bounded_display(row.version)))
        err = _date_error(row.date, where)
        if err is not None:
            findings.append(err)
    return findings


def _ledger_findings(releases, rows):
    """The findings that refuse the validated ledger rows `releases` as the base for the history `rows`:
    any witnessed row (a row without the imported flag; imported rows form a prefix of the ledger, so the
    writer extends only a ledger holding no witnessed row, spec 6.1, 14.3), and any history version that
    collides with an existing row, by the same string or the same SemVer precedence (a build-metadata
    variant)."""
    findings = []
    by_key = {}
    for i, row in enumerate(releases):
        version = row.get("version")
        if row.get("imported") is not True:
            findings.append("release #{} ({}) is witnessed (it carries no imported flag): imported rows form a "
                            "prefix of the ledger, so the writer refuses a ledger holding any witnessed row "
                            "(spec 6.1, 14.3)".format(i + 1, schema._bounded_display(version)))
        by_key.setdefault(release.parse_semver(version), version)
    for i, row in enumerate(rows):
        existing = by_key.get(release.parse_semver(row.version))
        if existing is not None:
            findings.append("history release[{}] version {} collides with the ledger's existing release {} "
                            "(versions are unique and strictly ascending, spec 6.1)".format(
                                i, schema._bounded_display(row.version), schema._bounded_display(existing)))
    return findings


def _changelog_entry_map(changelog_text, findings):
    """Map each CHANGELOG.md entry's covers token to its entry texts, through the changelog gate's own
    parser, or append the refusal and return None. A heading the gate grades as a finding refuses the
    build: a summary digest is never frozen over an entry whose heading the gate refuses."""
    if type(changelog_text) is not str:
        findings.append("the CHANGELOG.md text must be exactly a str, not a {} value (fail-closed)".format(
            type(changelog_text).__name__))
        return None
    try:
        entries, parse_findings = changelog._changelog_entries(changelog_text)
    except _commonmark_headings.HeadingScanError as exc:
        findings.append("cannot read the CHANGELOG.md entries: heading scan failed ({}): {}".format(
            exc.reason, schema._bounded_display(str(exc))))
        return None
    except UnicodeEncodeError:
        findings.append("cannot read the CHANGELOG.md entries: the text is not encodable as UTF-8")
        return None
    if parse_findings:
        findings.extend(parse_findings)
        return None
    entry_map = {}
    for token, entry_text in entries:
        entry_map.setdefault(token, []).append(entry_text)
    return entry_map


def _imported_release_row(row):
    """The imported [[release]] row for one history row: the version and recorded date verbatim, an empty
    worklog_span (a pre-migration release has no worklog entries) and the empty-coverage digest, computed
    here by the ledger's own scheme, never supplied (spec 6.1, 14.3)."""
    return {"version": row.version, "date": row.date, "worklog_span": [],
            "coverage_digest": release.compute_span_digest({}, None), "imported": True}


def _imported_summary_row(version, entry_text):
    """The published, imported [[summary]] row for one release: its freeze digest computed here from the
    release's curated CHANGELOG.md entry, never supplied, and no prose (spec 6.1, 7.2, 14.3)."""
    return {"covers": version, "status": "published", "digest": changelog.freeze_digest(entry_text),
            "imported": True}


def build_imported_ledger(version_data, history, changelog_text):
    """Build the version ledger that records the releases of a VALID History as imported rows (spec 6.1,
    14.3). Returns (new_version_data, findings): a new ledger and no finding, or None and the findings,
    never a partial ledger. `version_data` is the parsed version.toml, `history` the parse_history result
    and `changelog_text` the curated CHANGELOG.md text.

    It refuses a ledger that does not validate, a ledger holding any witnessed row, a history version that
    collides with an existing row, a history that is not VALID or holds no row, and a release whose
    version has no CHANGELOG.md entry headed by exactly that version, or more than one. It then appends,
    in history order, one imported [[release]] row per history row (the recorded date, an empty
    worklog_span, the empty-coverage digest) and one published, imported [[summary]] row whose digest is
    the freeze digest of that release's entry, and re-checks the new ledger with validate_version, which
    must be VALID. Every digest is computed here; no caller-supplied size or digest is read, and no prose
    is written.

    Pure: the input ledger is never mutated (the result is a deep copy), and no file, clock, environment,
    network or process is read or touched (the changelog parser's first call imports the vendored CommonMark
    parser through the import system)."""
    findings = _history_findings(history)
    if findings:
        return None, findings
    rows = history.rows
    checked = release.validate_version(version_data)
    if checked.status != VALID:
        return None, (["cannot build on a version ledger that does not validate (spec 6.1)"]
                      + list(checked.findings))
    findings.extend(_ledger_findings(checked.releases, rows))
    entry_map = _changelog_entry_map(changelog_text, findings)
    entries = []
    if entry_map is not None:
        for i, row in enumerate(rows):
            matched = entry_map.get(row.version, [])
            if not matched:
                findings.append("history release[{}] version {} has no CHANGELOG.md entry of its own: each "
                                "imported release's summary digest is frozen from its curated entry "
                                "(spec 7.2, 14.3)".format(i, schema._bounded_display(row.version)))
            elif len(matched) > 1:
                findings.append("history release[{}] version {} matches {} CHANGELOG.md entries: the freeze "
                                "digest is ambiguous (spec 7.2)".format(
                                    i, schema._bounded_display(row.version), len(matched)))
            else:
                entries.append(matched[0])
    if findings:
        return None, findings

    built = copy.deepcopy(version_data)
    built["release"] = list(built.get("release", [])) + [_imported_release_row(row) for row in rows]
    built["summary"] = list(built.get("summary", [])) + [
        _imported_summary_row(row.version, entry) for row, entry in zip(rows, entries)]
    rechecked = release.validate_version(built)
    if rechecked.status != VALID:
        return None, (["the built ledger does not validate, so nothing is returned (spec 6.1)"]
                      + list(rechecked.findings))
    return built, []


# --- self-test -------------------------------------------------------------------------------------------

# The hand-pinned canonical fixture. BYTE-EXACT on purpose: the acceptance vector pins the canonical form
# itself, so an emitter change that re-spells the format turns it red rather than silently moving the
# digest.
_FIXTURE = """\
schema = "opf.release.import-history/v1"

[source]
path = "release-notes.toml"

[[release]]
version = "0.9.0"
date = "2025-11-03T14:20:07Z"

[[release]]
version = "1.0.0"
date = "2026-01-12T09:02:41Z"
""".encode("utf-8")

_DATE = "2025-11-03T14:20:07Z"

# A curated CHANGELOG.md holding one entry per _FIXTURE release, and the two entry slices written out by
# hand, so the summary digests are checked against bytes the changelog parser did not cut.
_CHANGELOG = ("# Changelog\n\nA curated summary of the worklog.\n\n"
              "## 1.0.0 (2026-01-12)\n\n- the first stable release\n\n"
              "## 0.9.0 (2025-11-03)\n\n- the first public preview\n")
_ENTRY_100 = "## 1.0.0 (2026-01-12)\n\n- the first stable release\n\n"
_ENTRY_090 = "## 0.9.0 (2025-11-03)\n\n- the first public preview\n"
# The empty-coverage digest, pinned literally: `sha256:` and the SHA-256 of the scheme header line alone.
_EMPTY_COVERAGE = "sha256:e91c065ea5c1163990cf33408e517d24c89b8c4e544baf4a433a3ab6f57270f3"
# The clock reads the builder vectors make raise.
_CLOCK_NAMES = ("time", "time_ns", "monotonic", "monotonic_ns", "perf_counter", "perf_counter_ns",
                "process_time", "process_time_ns", "thread_time", "thread_time_ns", "localtime", "gmtime",
                "ctime", "asctime", "strftime")


def _edited(old, new):
    """The fixture with `old` replaced once by `new`; a missing needle is a harness error, never a
    silently unedited vector."""
    text = _FIXTURE.decode("utf-8")
    if old not in text:
        raise ValueError("fixture needle not found: {!r}".format(old))
    return text.replace(old, new, 1).encode("utf-8")


def _doc(rows, source=None, **top):
    """The canonical bytes of a history holding `rows` (each a table), built by emit_history so the vector
    differs from a VALID history only in what it names; the emitter itself is pinned by _FIXTURE."""
    doc = {"schema": HISTORY_SCHEMA, "source": {"path": "release-notes.toml"} if source is None else source,
           "release": rows}
    doc.update(top)
    return emit_history(doc)


def _refuses(result, status, text):
    """The vector contract for a refused history: the expected status, a finding carrying `text`, and no
    source path, rows or digest."""
    return (result.status == status and result.source_path is None and result.rows == ()
            and result.history_digest is None and any(text in f for f in result.findings))


def _sized(n):
    """A canonical, otherwise VALID history of exactly n bytes: one row whose version carries SemVer build
    metadata padded to length."""
    probe = _doc([{"version": "1.0.0+a", "date": _DATE}])
    pad = n - len(probe) + 1
    if pad < 1:
        raise ValueError("size {} is below the smallest padded history".format(n))
    data = _doc([{"version": "1.0.0+" + "a" * pad, "date": _DATE}])
    if len(data) != n:
        raise ValueError("padded history is {} bytes, not {}".format(len(data), n))
    return data


def _rows(n):
    """A canonical history of n strictly ascending rows."""
    return _doc([{"version": "0.0.{}".format(i), "date": _DATE} for i in range(1, n + 1)])


def _cut(text, old, new=""):
    """`text` with `old` replaced once by `new`; a missing needle is a harness error, never a silently
    unedited vector."""
    if old not in text:
        raise ValueError("changelog needle not found: {!r}".format(old))
    return text.replace(old, new, 1)


def _frozen(entry_text):
    """The freeze digest of an LF entry slice, computed here without the changelog module."""
    return "sha256:" + hashlib.sha256(entry_text.encode("utf-8")).hexdigest()


def _imported_ledger_row(version, date):
    return {"version": version, "date": date, "worklog_span": [], "coverage_digest": _EMPTY_COVERAGE,
            "imported": True}


def _strings(value):
    """Every string inside a ledger value: the keys and string values of its tables and arrays."""
    if type(value) is str:
        return [value]
    if type(value) is dict:
        return [s for k, v in value.items() for s in [k] + _strings(v)]
    if type(value) is list:
        return [s for v in value for s in _strings(v)]
    return []


def _built(result):
    """The release and summary rows of a builder result, or two empty lists when it refused."""
    data, findings = result
    if data is None or findings:
        return [], []
    return data.get("release", []), data.get("summary", [])


def _builder_vectors(check):
    """The build_imported_ledger vectors: acceptance over a fresh ledger, the row and summary contents,
    the refusals, the boundaries and the pure-function properties."""
    import datetime
    import time

    hist = parse_history(_FIXTURE)
    worklog = {"schema": 1, "entry": []}
    fresh = {"schema": 1}
    result = build_imported_ledger(fresh, hist, _CHANGELOG)
    rows, sums = _built(result)
    check("writer-builds-valid", result[0] is not None and result[1] == [] and len(rows) == 2
          and len(sums) == 2)
    check("writer-input-unchanged", fresh == {"schema": 1})
    check("writer-rows-verbatim", [(r.get("version"), r.get("date")) for r in rows]
          == [("0.9.0", "2025-11-03T14:20:07Z"), ("1.0.0", "2026-01-12T09:02:41Z")])
    check("writer-flag-true", len(rows) == 2 and all(r.get("imported") is True for r in rows))
    check("writer-span-empty", len(rows) == 2
          and all(type(r.get("worklog_span")) is list and r.get("worklog_span") == [] for r in rows))
    check("writer-digest-empty-coverage", len(rows) == 2
          and _EMPTY_COVERAGE == "sha256:" + hashlib.sha256(b"opf-worklog-coverage-v1").hexdigest()
          and all(r.get("coverage_digest") == _EMPTY_COVERAGE for r in rows))
    check("writer-summary-imported-published",
          [(s.get("covers"), s.get("status"), s.get("digest"), s.get("imported")) for s in sums]
          == [("0.9.0", "published", _frozen(_ENTRY_090), True), ("1.0.0", "published", _frozen(_ENTRY_100), True)]
          and all(type(s.get("imported")) is bool for s in sums))
    check("writer-output-validates", result[0] is not None
          and release.validate_version(result[0]).status == VALID
          and changelog.run_gates(result[0], worklog, _CHANGELOG).status == changelog.PASS)
    # No prose: each built row carries exactly the ledger members, and no CHANGELOG.md text (a heading, a
    # bullet or the preamble) appears anywhere in the ledger.
    prose = ("Changelog", "curated summary", "the first stable release", "the first public preview",
             "## 1.0.0", "## 0.9.0", "2026-01-12)", "2025-11-03)")
    check("writer-no-prose-in-ledger", result[0] is not None and len(rows) == 2 and len(sums) == 2
          and all(set(r) == {"version", "date", "worklog_span", "coverage_digest", "imported"} for r in rows)
          and all(set(s) == {"covers", "status", "digest", "imported"} for s in sums)
          and not any(p in s for p in prose for s in _strings(result[0])))
    check("writer-deterministic", build_imported_ledger({"schema": 1}, hist, _CHANGELOG) == result)

    # No clock: every clock read of the time module and datetime's now, utcnow and today raise while the
    # builder runs, and the result is the same ledger.
    class _ClockRead(Exception):
        pass

    def _raising_clock(*args, **kwargs):
        raise _ClockRead("the builder read the clock")

    class _NoClockDateTime(datetime.datetime):
        now = classmethod(_raising_clock)
        utcnow = classmethod(_raising_clock)
        today = classmethod(_raising_clock)

    class _NoClockDate(datetime.date):
        today = classmethod(_raising_clock)

    saved = {name: getattr(time, name) for name in _CLOCK_NAMES if hasattr(time, name)}
    saved_types = (datetime.datetime, datetime.date)
    clocked = None
    try:
        for name in saved:
            setattr(time, name, _raising_clock)
        datetime.datetime, datetime.date = _NoClockDateTime, _NoClockDate
        try:
            clocked = build_imported_ledger({"schema": 1}, hist, _CHANGELOG)
        except _ClockRead:
            clocked = None
    finally:
        for name, fn in saved.items():
            setattr(time, name, fn)
        datetime.datetime, datetime.date = saved_types
    check("writer-reads-no-clock", len(saved) == len(_CLOCK_NAMES) and clocked == result
          and result[0] is not None)

    # The ledger refusals. A witnessed row (no imported flag, here on an empty span so the flag alone
    # tells it from an imported row) refuses the build, before the history (D4); a version equal to an
    # existing imported row, by string or by SemVer precedence, collides.
    witnessed = {"version": "2.0.0", "date": "2026-02-01T10:00:00Z", "worklog_span": [],
                 "coverage_digest": _EMPTY_COVERAGE}
    data, got = build_imported_ledger({"schema": 1, "release": [witnessed]}, hist, _CHANGELOG)
    check("writer-refuses-after-witnessed", data is None
          and any("release #1 ('2.0.0') is witnessed" in f for f in got)
          and not any("collides" in f for f in got))
    data, got = build_imported_ledger({"schema": 1, "release": [_imported_ledger_row("0.9.0", _DATE)]},
                                      hist, _CHANGELOG)
    check("writer-refuses-version-collision", data is None
          and any("history release[0] version '0.9.0' collides with the ledger's existing release '0.9.0'" in f
                  for f in got) and not any("witnessed" in f for f in got))
    data, got = build_imported_ledger(
        {"schema": 1, "release": [_imported_ledger_row("1.0.0+legacy.1", _DATE)]}, hist, _CHANGELOG)
    check("writer-refuses-precedence-collision", data is None
          and any("version '1.0.0' collides with the ledger's existing release '1.0.0+legacy.1'" in f
                  for f in got))
    # Boundary: the history's first version collides with the ledger's first, witnessed row; both
    # refusals are named.
    data, got = build_imported_ledger({"schema": 1, "release": [dict(witnessed, version="0.9.0")]},
                                      hist, _CHANGELOG)
    check("writer-first-version-collides-with-witnessed", data is None
          and any("release #1 ('0.9.0') is witnessed" in f for f in got)
          and any("history release[0] version '0.9.0' collides" in f for f in got))
    # Just inside: a ledger of imported rows only, below the history, with its summaries and the working
    # tail, is extended, and the extension passes the changelog gates.
    cl_ext = ("# Changelog\n\n## unreleased\n\n- in progress\n\n" + _CHANGELOG.split("\n\n", 2)[2]
              + "\n## 0.8.0 (2025-06-30)\n\n- an earlier preview\n")
    base = {"schema": 1, "release": [_imported_ledger_row("0.8.0", "2025-06-30T08:15:00Z")],
            "summary": [{"covers": "unreleased", "status": "working"},
                        {"covers": "0.8.0", "status": "published", "imported": True,
                         "digest": _frozen("## 0.8.0 (2025-06-30)\n\n- an earlier preview\n")}]}
    data, got = build_imported_ledger(base, hist, cl_ext)
    check("writer-extends-imported-ledger", data is not None and got == []
          and [r.get("version") for r in data.get("release", [])] == ["0.8.0", "0.9.0", "1.0.0"]
          and [s.get("covers") for s in data.get("summary", [])] == ["unreleased", "0.8.0", "0.9.0", "1.0.0"]
          and changelog.run_gates(data, worklog, cl_ext).status == changelog.PASS
          and len(base["release"]) == 1 and len(base["summary"]) == 2)
    # A history below an existing imported row passes every input refusal, and only the re-check of the
    # output refuses it (rows strictly ascending, spec 6.1).
    data, got = build_imported_ledger({"schema": 1, "release": [_imported_ledger_row("2.0.0", _DATE)]},
                                      hist, _CHANGELOG)
    check("writer-recheck-refuses-invalid-output", data is None
          and got[:1] == ["the built ledger does not validate, so nothing is returned (spec 6.1)"]
          and any("not strictly greater" in f for f in got))
    data, got = build_imported_ledger({"schema": 2}, hist, _CHANGELOG)
    check("writer-refuses-invalid-ledger", data is None
          and got[:1] == ["cannot build on a version ledger that does not validate (spec 6.1)"])

    # The changelog refusals: a release with no entry, a range heading in place of the release's own,
    # two entries for one release, a heading the changelog gate refuses, and text that is not a str.
    data, got = build_imported_ledger({"schema": 1}, hist, _cut(_CHANGELOG, _ENTRY_100))
    check("writer-refuses-missing-changelog-entry", data is None and len(got) == 1
          and "history release[1] version '1.0.0' has no CHANGELOG.md entry" in got[0])
    data, got = build_imported_ledger({"schema": 1}, hist, _cut(_CHANGELOG, "## 1.0.0 (2026-01-12)",
                                                                 "## 0.9.0..1.0.0"))
    check("writer-refuses-range-heading", data is None
          and any("version '1.0.0' has no CHANGELOG.md entry" in f for f in got))
    data, got = build_imported_ledger({"schema": 1}, hist, _CHANGELOG + "\n" + _ENTRY_100)
    check("writer-refuses-duplicate-changelog-entry", data is None
          and any("version '1.0.0' matches 2 CHANGELOG.md entries" in f for f in got))
    data, got = build_imported_ledger({"schema": 1}, hist, _cut(_CHANGELOG, "## 1.0.0 (2026-01-12)",
                                                                 "## 1.0.0 the first stable"))
    check("writer-refuses-malformed-changelog-heading", data is None
          and any("not parenthesized date(s)" in f for f in got))
    data, got = build_imported_ledger({"schema": 1}, hist, _CHANGELOG.encode("utf-8"))
    check("writer-refuses-changelog-not-text", data is None and any("exactly a str" in f for f in got))

    # The history refusals: a refused history, an empty one, and a hand-built VALID history carrying a
    # date parse_history refuses; one row is just inside the empty bound.
    data, got = build_imported_ledger({"schema": 1}, parse_history(_doc([])), _CHANGELOG)
    check("writer-refuses-invalid-history", data is None
          and any("not a VALID parsed history" in f for f in got))
    data, got = build_imported_ledger({"schema": 1}, History(VALID, [], "release-notes.toml", (), "0" * 64),
                                      _CHANGELOG)
    check("writer-refuses-empty-history", data is None and any("holds no release row" in f for f in got))
    data, got = build_imported_ledger(
        {"schema": 1}, History(VALID, [], "release-notes.toml", (HistoryRow("1.0.0", "2026-01-12T00:00:00Z"),),
                               "0" * 64), _CHANGELOG)
    check("writer-rechecks-history-dates", data is None and any("is UTC midnight" in f for f in got))
    one = parse_history(_edited('[[release]]\nversion = "0.9.0"\ndate = "2025-11-03T14:20:07Z"\n\n', ""))
    cl_one = _cut(_CHANGELOG, _ENTRY_090).rstrip("\n") + "\n"
    result = build_imported_ledger({"schema": 1}, one, cl_one)
    rows, sums = _built(result)
    check("writer-one-row-valid", one.status == VALID and len(rows) == 1 and len(sums) == 1
          and rows[0].get("version") == "1.0.0"
          and sums[0].get("digest") == _frozen("## 1.0.0 (2026-01-12)\n\n- the first stable release\n")
          and changelog.run_gates(result[0], worklog, cl_one).status == changelog.PASS)


def self_test():
    """Pure in-memory vectors, judged on returned statuses, findings, bytes and ledgers: no file, process,
    network or clock (the builder vectors replace every clock read with one that raises, and restore it).
    Exit 0 all pass, 1 a failed check, 2 a harness error (fail-closed)."""
    results = []

    def check(name, ok):
        results.append((name, bool(ok)))

    try:
        # The bounds and the format id, pinned to literal values (never re-derived from the code).
        check("hist-constants-pinned", MAX_HISTORY_BYTES == 4194304 and MAX_RELEASE_ROWS == 10000
              and HISTORY_SCHEMA == "opf.release.import-history/v1")

        # Acceptance, byte-exact: the parsed model, the digest of the input bytes, and the emitter
        # reproducing the hand-pinned bytes.
        base = parse_history(_FIXTURE)
        check("hist-canonical-valid",
              base.status == VALID and base.findings == []
              and base.source_path == "release-notes.toml"
              and [(r.version, r.date) for r in base.rows]
              == [("0.9.0", "2025-11-03T14:20:07Z"), ("1.0.0", "2026-01-12T09:02:41Z")]
              and base.history_digest == hashlib.sha256(_FIXTURE).hexdigest()
              and emit_history(tomllib.loads(_FIXTURE.decode("utf-8"))) == _FIXTURE)
        check("hist-digest-deterministic", parse_history(_FIXTURE).history_digest == base.history_digest)

        # Canonical form: another key order, a comment, a CRLF line end, extra spacing and single-quote
        # re-quoting each refuse naming the first differing byte.
        got = parse_history(_edited('version = "0.9.0"\ndate = "2025-11-03T14:20:07Z"\n',
                                    'date = "2025-11-03T14:20:07Z"\nversion = "0.9.0"\n'))
        check("hist-noncanonical-order-refused", _refuses(got, INVALID, "not canonical"))
        got = parse_history(b"# exported from the release pipeline\n" + _FIXTURE)
        check("hist-comment-refused", _refuses(got, INVALID, "not canonical"))
        got = parse_history(_edited('version = "1.0.0"\n', 'version = "1.0.0" # the first stable\n'))
        check("hist-trailing-comment-refused", _refuses(got, INVALID, "not canonical"))
        got = parse_history(_FIXTURE.replace(b"\n", b"\r\n"))
        check("hist-crlf-refused", _refuses(got, INVALID, "not canonical"))
        got = parse_history(_edited('version = "1.0.0"', 'version  =  "1.0.0"'))
        check("hist-spacing-refused", _refuses(got, INVALID, "not canonical"))
        got = parse_history(_edited('version = "1.0.0"', "version = '1.0.0'"))
        check("hist-literal-string-refused", _refuses(got, INVALID, "not canonical"))
        got = parse_history(_edited('[[release]]\nversion = "0.9.0"\ndate = "2025-11-03T14:20:07Z"\n\n', ""))
        check("hist-one-row-valid", got.status == VALID and len(got.rows) == 1)

        # Dates: a date-only value and UTC midnight are refused by name; midnight in every accepted
        # spelling is AT the refused point, and the nearest recorded instants on either side of it
        # (one second and one millisecond after, one second before) stay VALID.
        got = parse_history(_edited('"2025-11-03T14:20:07Z"', '"2025-11-03"'))
        check("hist-date-only-refused", _refuses(got, INVALID, "is a date-only value"))
        got = parse_history(_edited('"2025-11-03T14:20:07Z"', "2025-11-03"))
        check("hist-native-date-refused", _refuses(got, INVALID, "native TOML date"))
        got = parse_history(_edited('"2025-11-03T14:20:07Z"', "2025-11-03T14:20:07Z"))
        check("hist-native-datetime-refused", _refuses(got, INVALID, "native TOML date"))
        got = parse_history(_edited('"2025-11-03T14:20:07Z"', '"2025-11-03T00:00:00Z"'))
        check("hist-midnight-refused", _refuses(got, INVALID, "is UTC midnight"))
        got = parse_history(_edited('"2025-11-03T14:20:07Z"', '"2025-11-03T00:00:00+00:00"'))
        check("hist-midnight-offset-refused", _refuses(got, INVALID, "is UTC midnight"))
        got = parse_history(_edited('"2025-11-03T14:20:07Z"', '"2025-11-03T00:00:00.000Z"'))
        check("hist-midnight-zero-fraction-refused", _refuses(got, INVALID, "is UTC midnight"))
        for name, value in (("one-second-after", "2025-11-03T00:00:01Z"),
                            ("one-millisecond-after", "2025-11-03T00:00:00.001Z"),
                            ("one-second-before", "2025-11-02T23:59:59Z")):
            got = parse_history(_edited('"2025-11-03T14:20:07Z"', '"{}"'.format(value)))
            check("hist-midnight-" + name + "-valid", got.status == VALID)
        got = parse_history(_edited('"2025-11-03T14:20:07Z"', '"2025-11-03T14:20:07+05:30"'))
        check("hist-non-utc-refused", _refuses(got, INVALID, "not an RFC 3339 UTC timestamp"))
        got = parse_history(_edited('"2025-11-03T14:20:07Z"', '"2025-02-30T14:20:07Z"'))
        check("hist-impossible-date-refused", _refuses(got, INVALID, "not an RFC 3339 UTC timestamp"))

        # Versions: strictly ascending by SemVer precedence and unique. Equal precedence (a build-metadata
        # variant) is AT the ascending rule's bound and refused; a prerelease before its release is just
        # inside it.
        got = parse_history(_doc([{"version": "1.0.0", "date": _DATE}, {"version": "0.9.0", "date": _DATE}]))
        check("hist-nonmonotonic-refused", _refuses(got, INVALID, "is not strictly greater"))
        got = parse_history(_doc([{"version": "1.0.0", "date": _DATE},
                                  {"version": "1.0.0+build.2", "date": _DATE}]))
        check("hist-equal-precedence-refused", _refuses(got, INVALID, "is not strictly greater"))
        got = parse_history(_doc([{"version": "1.0.0-rc.1", "date": _DATE},
                                  {"version": "1.0.0", "date": _DATE}]))
        check("hist-prerelease-then-release-valid", got.status == VALID)
        got = parse_history(_doc([{"version": "0.9.0", "date": _DATE}, {"version": "0.9.0", "date": _DATE}]))
        check("hist-duplicate-version-refused", _refuses(got, INVALID, "repeats an earlier row's version"))
        got = parse_history(_doc([{"version": "v1.0.0", "date": _DATE}]))
        check("hist-bad-semver-refused", _refuses(got, INVALID, "not a valid SemVer string"))
        got = parse_history(_edited('version = "0.9.0"', "version = 9"))
        check("hist-non-string-version-refused", _refuses(got, INVALID, "must be a SemVer string"))
        got = parse_history(_doc([{"date": _DATE}]))
        check("hist-missing-version-refused", _refuses(got, INVALID, "missing required field: version"))
        got = parse_history(_doc([{"version": "1.0.0"}]))
        check("hist-missing-date-refused", _refuses(got, INVALID, "missing required field: date"))

        # Writer-derived members, enumerated independently of WRITER_DERIVED_KEYS, refused by name in a
        # row, in [source] and at the top level.
        for key in ("worklog_span", "coverage_digest", "imported", "digest", "summary"):
            got = parse_history(_doc([{"version": "1.0.0", "date": _DATE, key: "x"}]))
            check("hist-{}-key-refused".format(key.replace("_", "-")),
                  _refuses(got, INVALID, "release[0] carries {!r}, which the imported-release writer "
                           "derives".format(key)))
            got = parse_history(_doc([{"version": "1.0.0", "date": _DATE}],
                                     source={"path": "release-notes.toml", key: "x"}))
            check("hist-source-{}-key-refused".format(key.replace("_", "-")),
                  _refuses(got, INVALID, "source carries {!r}".format(key)))
        got = parse_history(_FIXTURE + b'\n[summary]\ncovers = "1.0.0"\n')
        check("hist-top-level-summary-refused",
              _refuses(got, INVALID, "history carries 'summary', which the imported-release writer derives"))
        # Caller-supplied measurements, enumerated independently of MEASUREMENT_KEYS.
        for key in ("bytes", "sha256", "size", "source_sha256"):
            got = parse_history(_doc([{"version": "1.0.0", "date": _DATE, key: "x"}]))
            check("hist-row-measurement-{}-refused".format(key.replace("_", "-")),
                  _refuses(got, INVALID, "release[0] carries the caller-supplied measurement"))
            got = parse_history(_doc([{"version": "1.0.0", "date": _DATE}],
                                     source={"path": "release-notes.toml", key: "x"}))
            check("hist-source-measurement-{}-refused".format(key.replace("_", "-")),
                  _refuses(got, INVALID, "source carries the caller-supplied measurement"))
        # Prose: a listed prose key, and keys spelling `note` or `prose` inside a longer name; the rule
        # matches the casefolded key, so a capitalised listed name and an upper-case longer name are prose.
        for name, key in (("prose", "notes"), ("prose-description", "description"),
                          ("prose-release-notes", "release_notes"), ("prose-proseblock", "proseBlock"),
                          ("prose-casefold-listed", "Notes"), ("prose-casefold-longer", "RELEASE_NOTE")):
            got = parse_history(_doc([{"version": "1.0.0", "date": _DATE, key: "x"}]))
            check("hist-{}-key-refused".format(name), _refuses(got, INVALID, "carries the prose key"))
        got = parse_history(_doc([{"version": "1.0.0", "date": _DATE, "channel": "x"}]))
        check("hist-unknown-key-refused", _refuses(got, INVALID, "unknown key"))
        got = parse_history(_doc([{"version": "1.0.0", "date": _DATE, "flag": "x"}],
                                 source={"path": "release-notes.toml"}))
        check("hist-unknown-key-not-prose", any("unknown key" in f for f in got.findings)
              and not any("prose key" in f for f in got.findings))

        # Shape: no row, rows that are not an array of tables, no [source].
        got = parse_history(_doc([]))
        check("hist-empty-refused", _refuses(got, INVALID, "holds no release row"))
        got = parse_history(_edited("[[release]]", "[release]").replace(
            b'\n[[release]]\nversion = "1.0.0"\ndate = "2026-01-12T09:02:41Z"\n', b""))
        check("hist-release-table-refused", _refuses(got, INVALID, "must be an array of tables"))
        got = parse_history(_edited('[source]\npath = "release-notes.toml"\n\n', ""))
        check("hist-missing-source-refused", _refuses(got, INVALID, "missing required table: [source]"))
        got = parse_history(_edited('path = "release-notes.toml"\n', ""))
        check("hist-missing-source-path-refused", _refuses(got, INVALID, "missing required field: source.path"))

        # The source path: the batch [source].path grammar.
        got = parse_history(_edited('path = "release-notes.toml"', 'path = "/etc/release-notes.toml"'))
        check("hist-absolute-source-refused", _refuses(got, INVALID, "not a canonical product-relative"))
        got = parse_history(_edited('path = "release-notes.toml"', 'path = "../release-notes.toml"'))
        check("hist-parent-source-refused", _refuses(got, INVALID, "not a canonical product-relative"))
        got = parse_history(_edited('path = "release-notes.toml"', 'path = ".git/release-notes.toml"'))
        check("hist-control-area-source-refused", _refuses(got, INVALID, "lies inside a control area"))
        got = parse_history(_edited('path = "release-notes.toml"', 'path = "build/release-notes.toml"'))
        check("hist-nested-source-valid", got.status == VALID and got.source_path == "build/release-notes.toml")

        # The byte bound, inclusive: just inside and at the bound parse VALID; one byte past it is refused
        # unread.
        check("hist-size-just-inside-valid", parse_history(_sized(MAX_HISTORY_BYTES - 1)).status == VALID)
        check("hist-size-at-bound-valid", parse_history(_sized(MAX_HISTORY_BYTES)).status == VALID)
        got = parse_history(_sized(MAX_HISTORY_BYTES + 1))
        check("hist-oversize-refused", _refuses(got, CANNOT_EVALUATE, "over the 4194304 byte bound")
              and len(got.findings) == 1)
        # The bound runs before decoding: an oversized input that is not UTF-8 refuses on its size alone.
        got = parse_history(b"\xff" * (MAX_HISTORY_BYTES + 1))
        check("hist-oversize-before-decode", _refuses(got, CANNOT_EVALUATE, "over the 4194304 byte bound")
              and len(got.findings) == 1)

        # The row-count bound, inclusive: just inside and at the bound parse VALID; one row past it is
        # refused alone, before any row is graded.
        check("hist-rows-just-inside-valid", parse_history(_rows(MAX_RELEASE_ROWS - 1)).status == VALID)
        got = parse_history(_rows(MAX_RELEASE_ROWS))
        check("hist-rows-at-bound-valid", got.status == VALID and len(got.rows) == 10000)
        got = parse_history(_rows(MAX_RELEASE_ROWS + 1))
        check("hist-rows-over-bound-refused-alone", _refuses(got, INVALID, "over the 10000 row bound")
              and len(got.findings) == 1)

        # Entry gate and fail-closed reads.
        check("hist-not-bytes-cannot-evaluate",
              all(_refuses(parse_history(v), CANNOT_EVALUATE, "exactly bytes")
                  for v in (_FIXTURE.decode("utf-8"), bytearray(_FIXTURE), None)))
        check("hist-not-utf8-cannot-evaluate", _refuses(parse_history(b"\xff" + _FIXTURE), CANNOT_EVALUATE,
                                                        "not UTF-8"))
        check("hist-not-toml-cannot-evaluate", _refuses(parse_history(b"schema = \n"), CANNOT_EVALUATE,
                                                        "not parseable TOML"))
        got = parse_history(_edited("import-history/v1", "import-history/v2"))
        check("hist-schema-mismatch-cannot-evaluate", _refuses(got, CANNOT_EVALUATE, "is not"))
        got = parse_history(_edited('schema = "opf.release.import-history/v1"\n', ""))
        check("hist-schema-absent-cannot-evaluate", _refuses(got, CANNOT_EVALUATE, "is not"))

        # The emitter refuses a non-document and a non-string value directly, never emitting a best effort.
        for name, doc in (("non-document", "x"),
                          ("non-string-value", {"schema": HISTORY_SCHEMA, "source": {"path": "a"},
                                                "release": [{"version": "1.0.0", "date": 1}]}),
                          ("non-table-row", {"schema": HISTORY_SCHEMA, "source": {"path": "a"},
                                             "release": ["x"]})):
            try:
                emit_history(doc)
                refused = False
            except _CanonError:
                refused = True
            check("hist-emit-direct-" + name + "-refused", refused)
        # A direct call refuses, as the emitter's own ValueError subclass, a top-level key outside the
        # format (never dropped), a non-string key and a lone surrogate (never a TypeError or an encoding
        # error escaping).
        for name, doc in (("extra-top-key", {"schema": HISTORY_SCHEMA, "source": {"path": "a"},
                                             "release": [{"version": "1.0.0", "date": _DATE}], "summary": "x"}),
                          ("non-string-key", {"schema": HISTORY_SCHEMA, "source": {"path": "a"},
                                              "release": [{"version": "1.0.0", "date": _DATE, 1: "x"}]}),
                          ("surrogate", {"schema": HISTORY_SCHEMA, "source": {"path": "a\ud800"},
                                         "release": [{"version": "1.0.0", "date": _DATE}]})):
            try:
                emit_history(doc)
                refused = False
            except ValueError as exc:
                refused = type(exc) is _CanonError
            except Exception:  # noqa: BLE001  the wrong exception type is a failed check, not a harness error
                refused = False
            check("hist-emit-direct-" + name + "-refused", refused)
        # The batch escape set applies: a raw C1 control in a string is refused non-canonical (its
        # canonical spelling is the escape), beside the source path grammar's own refusal.
        got = parse_history(_edited('"release-notes.toml"', '"release\u009bnotes.toml"'))
        check("hist-raw-control-refused", got.status == INVALID
              and any("not canonical" in f for f in got.findings))

        _builder_vectors(check)

    except Exception as exc:  # noqa: BLE001  fail-closed backstop, never an uncaught exit-1 escape
        print("OPF-RELEASE-HISTORY SELF-TEST: harness error ({!r}); failing closed to exit 2".format(exc),
              file=sys.stderr)
        return 2
    failed = [name for name, ok in results if not ok]
    for name in failed:
        print("FAIL: " + name, file=sys.stderr)
    print("OPF-RELEASE-HISTORY SELF-TEST: {} checks, {} failed".format(len(results), len(failed)))
    return 1 if failed else 0


def main():
    if sys.argv[1:] == ["--self-test"]:
        return self_test()
    print("usage: _opf_release_history.py --self-test (a library module: parse_history(data), "
          "emit_history(doc), build_imported_ledger(version_data, history, changelog_text))", file=sys.stderr)
    return 2


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-test"]:
        sys.exit(self_test())
    sys.exit(main())
