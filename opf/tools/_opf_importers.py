#!/usr/bin/env python3
"""OPF importer layer + byte-range loss accounting (OPF-MIGRATE MIG-PR2).

The deterministic + assistant-guided importer layer that turns a legacy source file into (a) a set of
INERT candidate records, (b) a complete byte-range LOSS ACCOUNTING (the lossy-report), and (c) INERT
mapping PROPOSALS in the exact shape `_opf_import.plan_import`'s `proposals` channel consumes
(`_opf_import._validate_proposals`, `_opf_import.py`). This is the SHARED import capability spec 14.1
calls for: both declared-set `opf import` and the root-ingest MIGRATE disposition (MIG-PR3) consume it.
It COMPOSES `plan_import`; it re-implements NO scan / plan / stage machinery and stages NO run (the
run-dir claim and the `plan_import` call are MIG-PR3). This module is PURE: it operates on the in-memory
source records `_opf_import._read_sources` produces ({path, raw, body, sha256, size}) and touches no
filesystem, so a mapping never depends on ambient state and the self-test is fully hermetic.

What it delivers (spec 14.1, plan section 5):
  1. Deterministic importers (narrow, DECLARED grammar), each disclosing its residual coverage boundary
     where it is defined (disclose-guard-residuals) and guessing NOTHING outside its grammar:
       - github-tasklist : GitHub-flavoured markdown task-list items (`- [ ] ` / `- [x] `) -> backlog_item
       - keepachangelog  : Keep-a-Changelog (`## [ver] - date`, `### Category`, `- entry`) -> worklog
       - aiqt-face       : an EXACT OPF/AIQT-generated markdown face (the `_opf_views` projection). An exact
                           face is a DERIVED view the store regenerates, so it carries nothing NEW to import
                           (ignored / preserved, no candidate); a face that has DRIFTED from the exact
                           generated grammar (a possible hand edit) yields `ambiguous` spans for review.
  2. Loss accounting (`opf-ingest-lossy-report-v1`): every source BYTE range is classified into one of the
     closed classes {mapped, preserved_verbatim, ignored_by_declared_rule, ambiguous, conflict, unparsed},
     and the classes TILE [0, size) exactly (gap-free, non-overlapping). No bytes vanish from the
     accounting. Byte and line counts are MEASURED; a defaulted / inferred value is flagged per span and is
     never blended into a measured count (measured-and-estimated-figures-stay-separate). A size / resource
     limit is CANNOT-EVALUATE, never a truncation (no truncating sink; track-launched-work).
  3. The assistant-guided contract (`validate_assistant_output`): the adopter's assistant drafts the SAME
     structures for a non-conforming source; it is UNTRUSTED generated data (SECI-output-handling,
     inter-agent-trust), gated by the IDENTICAL validator, assigns NO permanent id without human
     confirmation, and a validator pass does NOT erase an unresolved loss span (ambiguous / conflict /
     unparsed) or replace human acceptance.
  4. The validator gate (`validate_importer_output`): the single yes/no for BOTH paths. It runs the loss
     tiling check, re-uses `plan_import`'s OWN `_validate_proposals` for proposal confinement, checks the
     proposal<->span bijection, and validates every candidate envelope through the real
     `_opf_schema.validate_record`. A cannot-evaluate anywhere is routed to the safe outcome (reject),
     never a clean pass (guard-input-soundness). Verdicts are judged by the returned value, never grepped
     (lightweight-verifier-workers).

Everything not confidently mapped is left for the downstream baseline to preserve as a legacy_fragment
(the merged `plan_import` whole-file quarantine); this module never drops a byte and never auto-promotes a
non-clean conversion (a source with any ambiguous / conflict / unparsed span has clean == False, so it may
still yield a candidate for review but cannot auto-promote).

Disclosed coverage residuals (part of the layer, not a footnote):
  - Non-UTF-8 sources are CANNOT-EVALUATE, inherited from `_read_sources` (this build is UTF-8 only).
  - A deterministic importer INVENTS no permanent id: an id-less source yields an id-less candidate draft
    (the counter mints at apply under the store lock, MIG-PR5); the validator injects a synthetic id for
    validation ONLY. Source ids, where a future finer importer preserves them, are reconciled against
    `counters.toml` at apply (spec 8.2), not here.
  - Record MINTING (a resting candidate) happens at review / apply (MIG-PR4/5), not at plan; this module's
    candidates are INERT drafts. Their envelope is validated here (schema-valid modulo the apply-time id).
  - An AIQT face is recognized by the exact generated SENTINEL; faithful record RECONSTRUCTION from a
    rendered face is NOT attempted (the inline escape is one-way for a markdown sink), it is deferred to
    the assistant-guided path or the operator KEEPs the original (spec 14.2 quarantine-and-keep).
  - Grammar boundaries per importer are disclosed on each `residual` string below.

Offline, stdlib only, fail-closed. It lives under `opf/tools/` and imports ONLY sibling `opf/tools/`
modules, so the standalone-closure property (OPF-SELF-CONTAIN) holds.

Exit convention (the sibling OPF units): 0 clean, 1 a finding, 2 cannot-evaluate.
"""
import copy
import datetime
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _journal        # noqa: E402  the contained-reader path predicate (source_path soundness)
import _opf_store      # noqa: E402  contained-relpath predicate, the store-read byte cap
import _opf_import     # noqa: E402  reuse the 0/1/2 verdicts, the proposals validator, digest / emit helpers
import _opf_schema     # noqa: E402  the REAL record validator both paths pass (the single validator gate)


# --- fixed formats, closed vocabularies, and the outcome model ---------------------------------------

# The 0/1/2 verdict contract, single-sourced from the import layer (no local mirror): one contract.
CLEAN = _opf_import.CLEAN                        # 0: a clean importer result
FINDING = _opf_import.FINDING                    # 1: a validation finding / an importer that declines
CANNOT_EVALUATE = _opf_import.CANNOT_EVALUATE    # 2: unreadable / malformed / out-of-subset (fail-closed)

# The lossy-report format token and schema marker (canonical `_opf_emit` TOML, the store's native
# byte-canonical form, matching inventory.toml / the ingest worksheet).
LOSSY_FORMAT = "opf-ingest-lossy-report-v1"
SCHEMA = 1

# The closed byte-range loss classes (plan section 5, the Codex byte-range model). Every source byte range
# is exactly one of these; the six TILE [0, size).
LOSS_CLASSES = ("mapped", "preserved_verbatim", "ignored_by_declared_rule",
                "ambiguous", "conflict", "unparsed")
# The classes that MAY feed a candidate record / a mapping proposal (so a `record` ref is legal only here).
_MAPPING_CLASSES = frozenset({"mapped", "preserved_verbatim", "ambiguous", "conflict"})
# The classes that make a conversion NON-clean (they block auto-promote; plan section 5).
_UNCLEAN_CLASSES = frozenset({"ambiguous", "conflict", "unparsed"})

# Closed keysets for the emitted / validated artefacts.
_REPORT_KEYS = frozenset({"format", "schema", "source", "report_digest"})
_SOURCE_KEYS = frozenset({"source_path", "source_digest", "size", "line_count", "span"})
_SPAN_KEYS = frozenset({"range", "class", "line_range", "record", "field", "defaulted", "note"})

_DIGEST_RE = _opf_import._DIGEST_RE              # `sha256:`+64 lowercase hex

# The candidate types this layer's deterministic importers mint (spec 6). Both are baseline types whose
# namespace comes from the section-8.1 taxonomy (never hard-coded here).
_BACKLOG_TYPE = "backlog_item"
_WORKLOG_TYPE = "worklog"

# A synthetic timestamp used ONLY to validate a draft envelope whose real updated_at is stamped at apply
# (never written into a shipped record); its presence in a draft would be a bug the assistant contract
# check catches.
_SYNTH_STAMP = "2026-01-01T00:00:00Z"

# The known deterministic importers, keyed by their declared kind. `run_importer` dispatches on this.
def _kinds():
    return {
        "github-tasklist": import_github_tasklist,
        "keepachangelog": import_keepachangelog,
        "aiqt-face": import_aiqt_face,
    }


class ImporterResult:
    """The inert output of one importer over one source. Judged by its verdict, never by grepping output.
    On a clean result: `lossy` is the source's complete byte-range loss entry, `proposals` the INERT
    `plan_import`-shape mapping proposals, `candidates` the id-less candidate record drafts, `clean` True
    iff the conversion has NO ambiguous / conflict / unparsed span (only a clean conversion may
    auto-promote), and `residual` the disclosed grammar boundary. A verdict of FINDING with empty payload
    means the importer DECLINED (the source is not of its declared kind)."""
    __slots__ = ("verdict", "findings", "kind", "source_path", "lossy", "proposals",
                 "candidates", "clean", "residual")

    def __init__(self, verdict, findings=None, kind="", source_path="", lossy=None,
                 proposals=None, candidates=None, clean=False, residual=""):
        self.verdict = verdict
        self.findings = findings or []
        self.kind = kind
        self.source_path = source_path
        self.lossy = lossy or {}
        self.proposals = proposals or []
        self.candidates = candidates or []
        self.clean = clean
        self.residual = residual


class _ImporterError(Exception):
    """An input an importer cannot use, carrying the verdict so a finding (verdict 1) and an
    unreadable / malformed input (verdict 2) are distinguished at the raise site (mirrors
    `_opf_import._StageError`)."""
    def __init__(self, verdict, message):
        super().__init__(message)
        self.verdict = verdict
        self.message = message


def _finding(msg):
    return _ImporterError(FINDING, msg)


def _cannot(msg):
    return _ImporterError(CANNOT_EVALUATE, msg)


# --- source-record intake ----------------------------------------------------------------------------

def _require_source(source):
    """Validate the in-memory source record shape (`_opf_import._read_sources` output) at the boundary,
    fail-closed. Returns (path, raw_bytes). A malformed record, a path the contained reader would reject,
    or non-UTF-8 bytes is CANNOT-EVALUATE (untrusted input validated at the boundary; the non-UTF-8
    residual is inherited from the scan pipeline)."""
    if not isinstance(source, dict):
        raise _cannot("source must be a {path, raw, sha256, size} record")
    path = source.get("path")
    raw = source.get("raw")
    sha = source.get("sha256")
    size = source.get("size")
    if not (isinstance(path, str) and _opf_store._is_contained_relpath(path)):
        raise _cannot("source.path must be a contained root-relative path")
    try:
        _journal._check_rel(path)
    except _journal.JournalError as exc:
        raise _cannot("source.path {!r} is not a path the contained reader accepts ({})".format(path, exc))
    if not isinstance(raw, (bytes, bytearray)):
        raise _cannot("source.raw must be bytes")
    raw = bytes(raw)
    if not (isinstance(sha, str) and _opf_import._HEX64_RE.match(sha) and sha == _opf_import._sha256_hex(raw)):
        raise _cannot("source.sha256 must be the 64-hex digest of source.raw")
    if not (type(size) is int and size == len(raw)):
        raise _cannot("source.size must equal len(source.raw)")
    try:
        raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _cannot("source {!r} is not UTF-8 decodable ({}); a non-UTF-8 source is unsupported in this "
                      "build (fail-closed, inherited residual)".format(path, exc))
    return path, raw


def _tile_lines(raw):
    """Split raw bytes into physical lines, each carrying its own trailing newline, as (start, end)
    half-open BYTE ranges that tile [0, len(raw)) exactly. `\\n` is a single byte and every UTF-8
    continuation byte is >= 0x80, so a line boundary never splits a multi-byte sequence. An empty source
    yields zero lines."""
    out = []
    i, n = 0, len(raw)
    while i < n:
        j = raw.find(b"\n", i)
        end = n if j == -1 else j + 1
        out.append((i, end))
        i = end
    return out


def _text(raw, start, end):
    """The decoded text of a line span WITHOUT its trailing newline / carriage return, for grammar
    matching. The span itself always covers the whole line including the newline in the loss accounting."""
    seg = raw[start:end]
    if seg.endswith(b"\n"):
        seg = seg[:-1]
    if seg.endswith(b"\r"):
        seg = seg[:-1]
    return seg.decode("utf-8")


# --- span assembly -----------------------------------------------------------------------------------

def _coalesce(cells):
    """Coalesce a per-line cell list into minimal contiguous spans: adjacent cells merge iff every
    classification field (class, record, field, defaulted, note) is identical and they abut. A mapped cell
    carries a UNIQUE record draft-ref, so mapped cells never merge with their neighbours (one span per
    candidate); ignored / unparsed / ambiguous runs with an identical note merge. The result still tiles
    the source exactly (merging preserves coverage)."""
    spans = []
    for c in cells:
        if spans:
            p = spans[-1]
            if (p["class"] == c["class"] and p["record"] == c["record"] and p["field"] == c["field"]
                    and p["defaulted"] == c["defaulted"] and p["note"] == c["note"]
                    and p["range"][1] == c["start"]):
                p["range"][1] = c["end"]
                p["line_range"][1] = c["line_no"]
                continue
        spans.append({"range": [c["start"], c["end"]], "class": c["class"],
                      "line_range": [c["line_no"], c["line_no"]], "record": c["record"],
                      "field": c["field"], "defaulted": c["defaulted"], "note": c["note"]})
    return spans


def _cell(start, end, line_no, cls, record="", field="", defaulted=False, note=""):
    return {"start": start, "end": end, "line_no": line_no, "class": cls, "record": record,
            "field": field, "defaulted": defaulted, "note": note}


def _proposal_for(path, span):
    """The INERT `plan_import`-shape proposal a span warrants, or None. A span feeds a mapping suggestion
    when it carries a candidate (mapped / preserved_verbatim with a record ref) or when it is an unclean
    mapping class the human must resolve (ambiguous -> `ambiguous`; conflict -> `cannot_evaluate`). An
    ignored / unparsed span, and a preserved span with no candidate (a derived-view line), warrants none
    (unparsed content is preserved by the downstream baseline legacy_fragment)."""
    cls = span["class"]
    if cls == "ambiguous":
        state = "ambiguous"
    elif cls == "conflict":
        state = "cannot_evaluate"
    elif cls in ("mapped", "preserved_verbatim") and span["record"]:
        state = "mapped"
    else:
        return None
    return {"source_path": path, "span": [span["range"][0], span["range"][1]],
            "suggested_state": state, "note": span["note"]}


def _finalize(source, kind, cells, candidates, residual):
    """Assemble the ImporterResult from classified cells: coalesce spans, build the per-source loss entry,
    derive the proposals from the spans, and set `clean`."""
    spans = _coalesce(cells)
    entry = {
        "source_path": source["path"],
        "source_digest": "sha256:" + source["sha256"],
        "size": source["size"],
        "line_count": (cells[-1]["line_no"] if cells else 0),
        "span": spans,
    }
    proposals = [pr for pr in (_proposal_for(source["path"], sp) for sp in spans) if pr is not None]
    proposals.sort(key=lambda p: (p["span"][0], p["span"][1]))
    clean = not any(sp["class"] in _UNCLEAN_CLASSES for sp in spans)
    return ImporterResult(CLEAN, kind=kind, source_path=source["path"], lossy=entry,
                          proposals=proposals, candidates=candidates, clean=clean, residual=residual)


def _import_provenance(path, line_no, note):
    """An import-provenance ref (spec 8.6 {kind, locator, note}); a `path`-kind locator at the source. This
    lets an importer-authored draft legitimately omit created_at (spec 8.3, the importer exception)."""
    return {"kind": "path", "locator": path,
            "note": "imported from {} line {} ({})".format(path, line_no, note)}


# --- deterministic importer 1: GitHub task-lists -> backlog_item --------------------------------------

_TASK_RE = re.compile(r"^\s*[-*+]\s+\[(?P<mark>[ xX])\]\s+(?P<text>.*\S)\s*$")
_HEADING_RE = re.compile(r"^#{1,6}\s+\S")

_GITHUB_RESIDUAL = ("github-tasklist recognizes GitHub-flavoured task-list items (`- [ ] ` / `- [x] `) as "
                    "backlog_item candidates (unchecked -> open, checked -> done). Nested items, assignees, "
                    "labels, due dates, and multi-line bodies are OUTSIDE the declared grammar: a "
                    "non-task, non-heading, non-blank line is `unparsed` (never coerced), blank lines and "
                    "structural headings are `ignored_by_declared_rule`.")


def import_github_tasklist(source):
    """Deterministic importer for a GitHub task-list markdown file (plan section 5). Applicable to any
    UTF-8 source; a source with no task item simply produces no candidate. Fail-closed on a malformed
    source record (CANNOT-EVALUATE)."""
    try:
        path, raw = _require_source(source)
    except _ImporterError as exc:
        return ImporterResult(exc.verdict, [exc.message], kind="github-tasklist",
                              source_path=source.get("path", "") if isinstance(source, dict) else "")
    cells, candidates = [], []
    draft = [0]
    for line_no, (start, end) in enumerate(_tile_lines(raw), start=1):
        line = _text(raw, start, end)
        m = _TASK_RE.match(line)
        if m:
            draft[0] += 1
            ref = "draft-{:04d}".format(draft[0])
            status = "done" if m.group("mark") in ("x", "X") else "open"
            rec = {
                "type": _BACKLOG_TYPE, "status": status, "title": m.group("text"),
                "actor": {"kind": "importer"},
                "refs": [_import_provenance(path, line_no, "GFM task-list item")],
            }
            candidates.append({"draft_ref": ref, "type": _BACKLOG_TYPE, "record": rec})
            cells.append(_cell(start, end, line_no, "mapped", record=ref, field="title",
                               note="GFM task-list item -> backlog_item"))
        elif not line.strip():
            cells.append(_cell(start, end, line_no, "ignored_by_declared_rule", note="blank line"))
        elif _HEADING_RE.match(line):
            cells.append(_cell(start, end, line_no, "ignored_by_declared_rule", note="structural heading"))
        else:
            cells.append(_cell(start, end, line_no, "unparsed",
                               note="line outside the GFM task-list grammar"))
    return _finalize(source, "github-tasklist", cells, candidates, _GITHUB_RESIDUAL)


# --- deterministic importer 2: Keep-a-Changelog -> worklog --------------------------------------------

_KAC_VERSION_RE = re.compile(r"^##\s+\[(?P<ver>[^\]]+)\]\s*-\s*(?P<date>\d{4}-\d{2}-\d{2})\s*$")
_KAC_UNRELEASED_RE = re.compile(r"^##\s+\[?[Uu]nreleased\]?\s*$")
_KAC_CATEGORY_RE = re.compile(r"^###\s+(?P<cat>Added|Changed|Deprecated|Removed|Fixed|Security)\s*$")
_KAC_H1_RE = re.compile(r"^#\s+\S")
_KAC_BULLET_RE = re.compile(r"^\s*[-*+]\s+(?P<text>.*\S)\s*$")
# The categories that map 1:1 to a WORKLOG_KIND (spec 6.2). `Deprecated` has no clean kind: an entry under
# it is `ambiguous` (never silently coerced to `changed`).
_CATEGORY_KIND = {"Added": "added", "Changed": "changed", "Removed": "removed",
                  "Fixed": "fixed", "Security": "security"}

_KAC_RESIDUAL = ("keepachangelog recognizes the Keep-a-Changelog structure (`## [version] - YYYY-MM-DD`, "
                 "`### Category`, `- entry`) and maps a DATED entry under a mapped category to a worklog "
                 "candidate; the entry's time-of-day is DEFAULTED to T00:00:00Z (the source carries a "
                 "calendar date only) and flagged `defaulted`. Release-pipeline records (version.toml + "
                 "release records) are spec 14.3 and OUT of base MIGRATE scope, so a version header is a "
                 "`ignored_by_declared_rule` grouping. An Unreleased / undated entry, an unmapped "
                 "category (e.g. Deprecated), and a version header whose date is not a real calendar date "
                 "(with the entries under it), is `ambiguous`; a duplicate version block and its entries "
                 "are `conflict`; free prose is `unparsed`. Multi-line entry bodies are not recognized in v1.")


def _is_valid_calendar_date(date):
    """True iff `date` (already constrained to the YYYY-MM-DD shape by _KAC_VERSION_RE) is a REAL calendar
    date. datetime.date.fromisoformat rejects an impossible month or day (2026-13-45, 2026-02-30), so an
    entry is never mapped onto a date that cannot exist (a syntactically-valid but invalid date is drift)."""
    try:
        datetime.date.fromisoformat(date)
    except ValueError:
        return False
    return True


def import_keepachangelog(source):
    """Deterministic importer for a Keep-a-Changelog file (plan section 5, mapped to OPF worklog per
    section 6). Applicable to any UTF-8 source. Fail-closed on a malformed source record."""
    try:
        path, raw = _require_source(source)
    except _ImporterError as exc:
        return ImporterResult(exc.verdict, [exc.message], kind="keepachangelog",
                              source_path=source.get("path", "") if isinstance(source, dict) else "")
    cells, candidates = [], []
    draft = [0]
    cur_date = None       # the current version's date as an RFC-3339 stamp, or None (Unreleased / none)
    cur_kind = None       # the current category's worklog kind, or None (unmapped / none)
    cur_cat = None
    cur_conflict = False  # True while inside a duplicate version block
    cur_bad_date = None   # the raw date string while inside a version block with an invalid calendar date
    seen_versions = set()
    for line_no, (start, end) in enumerate(_tile_lines(raw), start=1):
        line = _text(raw, start, end)
        mver = _KAC_VERSION_RE.match(line)
        mcat = _KAC_CATEGORY_RE.match(line)
        mbul = _KAC_BULLET_RE.match(line)
        if not line.strip():
            cells.append(_cell(start, end, line_no, "ignored_by_declared_rule", note="blank line"))
        elif mver:
            ver = mver.group("ver")
            date = mver.group("date")
            if ver in seen_versions:
                cur_conflict = True
                cells.append(_cell(start, end, line_no, "conflict",
                                   note="duplicate version block [{}]".format(ver)))
            elif not _is_valid_calendar_date(date):
                # A syntactically well-formed but calendar-INVALID date (e.g. 2026-13-45) maps to no real
                # date: the header is drift (`ambiguous`), and its entries route to `ambiguous` below rather
                # than being silently `mapped` onto an impossible date.
                seen_versions.add(ver)
                cur_conflict = False
                cur_date = None
                cur_bad_date = date
                cur_kind = None
                cur_cat = None
                cells.append(_cell(start, end, line_no, "ambiguous",
                                   note="version header [{}] carries an invalid calendar date {!r}".format(
                                       ver, date)))
            else:
                seen_versions.add(ver)
                cur_conflict = False
                cur_date = date + "T00:00:00Z"
                cur_bad_date = None
                cur_kind = None
                cur_cat = None
                cells.append(_cell(start, end, line_no, "ignored_by_declared_rule",
                                   note="release grouping [{}] (release records are spec 14.3)".format(ver)))
        elif _KAC_UNRELEASED_RE.match(line):
            cur_conflict = False
            cur_date = None
            cur_bad_date = None
            cur_kind = None
            cur_cat = None
            cells.append(_cell(start, end, line_no, "ignored_by_declared_rule",
                               note="unreleased grouping"))
        elif mcat:
            cur_cat = mcat.group("cat")
            cur_kind = _CATEGORY_KIND.get(cur_cat)
            cells.append(_cell(start, end, line_no, "ignored_by_declared_rule",
                               note="category grouping ({})".format(cur_cat)))
        elif mbul:
            text = mbul.group("text")
            if cur_conflict:
                cells.append(_cell(start, end, line_no, "conflict",
                                   note="entry under a duplicate version block"))
            elif cur_bad_date is not None:
                cells.append(_cell(start, end, line_no, "ambiguous",
                                   note="entry under a version header with an invalid calendar date "
                                        "{!r}".format(cur_bad_date)))
            elif cur_date is None:
                cells.append(_cell(start, end, line_no, "ambiguous",
                                   note="undated entry (Unreleased): a worklog entry requires a date"))
            elif cur_kind is None:
                cells.append(_cell(start, end, line_no, "ambiguous",
                                   note="category {!r} has no worklog-kind mapping (spec 6.2)".format(cur_cat)))
            else:
                draft[0] += 1
                ref = "draft-{:04d}".format(draft[0])
                rec = {
                    "date": cur_date, "actor": {"kind": "importer"}, "kind": cur_kind, "summary": text,
                    "refs": [_import_provenance(path, line_no, "Keep-a-Changelog entry")],
                }
                candidates.append({"draft_ref": ref, "type": _WORKLOG_TYPE, "record": rec})
                cells.append(_cell(start, end, line_no, "mapped", record=ref, field="summary",
                                   defaulted=True,
                                   note="changelog entry -> worklog (time-of-day defaulted)"))
        elif _KAC_H1_RE.match(line):
            cells.append(_cell(start, end, line_no, "ignored_by_declared_rule",
                               note="changelog title (structural)"))
        else:
            cells.append(_cell(start, end, line_no, "unparsed",
                               note="prose outside the Keep-a-Changelog entry grammar"))
    return _finalize(source, "keepachangelog", cells, candidates, _KAC_RESIDUAL)


# --- deterministic importer 3: exact AIQT-generated markdown faces ------------------------------------

_FACE_SENTINEL = b"<!-- GENERATED by "
_FACE_GENERATOR = "opf/tools/_opf_views.py"
_FACE_BULLET_RE = re.compile(r"^ *- \S")
_FACE_EMPTY = "_No records._"

_FACE_RESIDUAL = ("aiqt-face recognizes an EXACT OPF/AIQT-generated markdown face by its generated "
                  "sentinel (`<!-- GENERATED by ... opf/tools/_opf_views.py ... -->`). An exact face is a "
                  "DERIVED projection the store REGENERATES (generated-artefact-source-only), so it carries "
                  "nothing NEW to import: the header is `ignored_by_declared_rule`, the title / empty marker "
                  "/ blank lines are `ignored_by_declared_rule`, and a recognized rendered record line is "
                  "`preserved_verbatim` with NO candidate (re-importing it would duplicate the store). A "
                  "body line that has DRIFTED from the exact generated grammar (a possible hand edit) is "
                  "`ambiguous` and routes to review; likewise, if the header comment is unterminated (its "
                  "closing `-->` dropped or mangled), only the opening sentinel line is header and every "
                  "following line is `ambiguous` drift rather than silently swallowed as header. Faithful "
                  "RECONSTRUCTION of records from a rendered "
                  "face (the inline escape is one-way for a markdown sink) is deferred to the "
                  "assistant-guided path or an operator KEEP of the original (spec 14.2).")


def import_aiqt_face(source):
    """Deterministic importer for an EXACT OPF/AIQT-generated markdown face. It DECLINES (verdict FINDING,
    empty payload) when the source does not carry the generated sentinel, so the caller selects another
    importer. Fail-closed on a malformed source record (CANNOT-EVALUATE)."""
    try:
        path, raw = _require_source(source)
    except _ImporterError as exc:
        return ImporterResult(exc.verdict, [exc.message], kind="aiqt-face",
                              source_path=source.get("path", "") if isinstance(source, dict) else "")
    if not (raw.startswith(_FACE_SENTINEL) and _FACE_GENERATOR.encode("utf-8") in raw.split(b"\n", 1)[0]):
        return ImporterResult(FINDING, ["not an AIQT-generated face: missing the generated-header sentinel"],
                              kind="aiqt-face", source_path=path)
    cells = []
    in_header = True
    # An HTML comment closes at `-->`; a generated face's header ends with a line that is exactly `-->`
    # (_opf_views._header). If NO such line exists the header comment is unterminated (a hand edit dropped
    # or mangled the close, e.g. `--!>`): only the opening sentinel line is header, and every following line
    # is drift, so it is `ambiguous` (clean == False) rather than silently swallowed as header/ignored.
    header_closes = any(_text(raw, s, e).strip() == "-->" for s, e in _tile_lines(raw))
    for line_no, (start, end) in enumerate(_tile_lines(raw), start=1):
        line = _text(raw, start, end)
        if in_header:
            if not header_closes and line_no > 1:
                cells.append(_cell(start, end, line_no, "ambiguous",
                                   note="generated-face header comment is not closed by `-->` (drift)"))
                continue
            cells.append(_cell(start, end, line_no, "ignored_by_declared_rule",
                               note="generator header (identity / regenerate metadata)"))
            if line.strip() == "-->":
                in_header = False
            continue
        if not line.strip():
            cells.append(_cell(start, end, line_no, "ignored_by_declared_rule", note="blank line"))
        elif _KAC_H1_RE.match(line):
            cells.append(_cell(start, end, line_no, "ignored_by_declared_rule", note="view title (structural)"))
        elif line == _FACE_EMPTY:
            cells.append(_cell(start, end, line_no, "ignored_by_declared_rule", note="empty-view marker"))
        elif _FACE_BULLET_RE.match(line):
            cells.append(_cell(start, end, line_no, "preserved_verbatim",
                               note="derived view line (regenerated from the store; not re-imported)"))
        else:
            cells.append(_cell(start, end, line_no, "ambiguous",
                               note="face body drifted from the exact generated grammar (possible hand edit)"))
    # An exact face carries no candidate: it is a projection, not adopter content to import.
    return _finalize(source, "aiqt-face", cells, [], _FACE_RESIDUAL)


def run_importer(kind, source):
    """Dispatch to the named deterministic importer. An unknown kind is a finding (fail-closed)."""
    fn = _kinds().get(kind)
    if fn is None:
        return ImporterResult(FINDING, ["unknown importer kind {!r} (known: {})".format(
            kind, ", ".join(sorted(_kinds())))], kind=str(kind),
            source_path=source.get("path", "") if isinstance(source, dict) else "")
    return fn(source)


# --- loss accounting: emission and the completeness validator -----------------------------------------

def build_lossy_report(entries):
    """Assemble the canonical lossy-report payload from per-source loss entries and stamp its content
    digest. Sources sort by unsigned UTF-8 path bytes; the digest is `sha256:`+hex over the canonical
    serialization EXCLUDING its own digest field, so it is byte-identical across runs (like the ingest
    worksheet). Fail-closed on a value outside the constrained emit subset."""
    ordered = sorted(entries, key=lambda e: e["source_path"].encode("utf-8"))
    payload = {"format": LOSSY_FORMAT, "schema": SCHEMA, "source": ordered}
    digest = "sha256:" + _opf_import._sha256_hex(_lossy_bytes(payload))
    report = dict(payload)
    report["report_digest"] = digest
    return report, digest


def _lossy_bytes(payload):
    try:
        return _opf_import._emit_bytes(payload, "lossy report")
    except _opf_import._StageError as exc:
        raise _cannot("lossy report is not byte-canonically emittable ({})".format(exc.message))


def lossy_report_bytes(report):
    """The byte-canonical TOML bytes of a lossy report, held under the contained store-read cap so nothing
    is produced that a reader would later refuse (fail at the producer, never hand a consumer an input it
    cannot read; guard-input-soundness). Over-cap is CANNOT-EVALUATE, never a truncation."""
    data = _lossy_bytes(report)
    if len(data) > _opf_store.MAX_STORE_READ_BYTES:
        raise _cannot("lossy report is {} bytes, over the {}-byte contained store-read cap; it would be "
                      "unreadable (rejected at the producer boundary, never truncated)".format(
                          len(data), _opf_store.MAX_STORE_READ_BYTES))
    return data


def _validate_source_entry(entry, where):
    """Validate one per-source loss entry against its closed schema AND the load-bearing tiling invariant:
    the spans cover [0, size) exactly, gap-free and non-overlapping (a planted unaccounted range, a gap,
    or an overlap is a finding). Returns a findings list; empty means valid. Fail-closed on any malformed
    field (this runs over UNTRUSTED artefacts: an assistant's or a staged report)."""
    f = []
    if not isinstance(entry, dict):
        return ["{}: a source entry must be a table".format(where)]
    if set(entry) != _SOURCE_KEYS:
        f.append("{}: a source entry is a closed keyset {{source_path, source_digest, size, line_count, "
                 "span}}".format(where))
        return f
    sp = entry.get("source_path")
    if not (isinstance(sp, str) and _opf_store._is_contained_relpath(sp)):
        f.append("{}: source_path must be a contained root-relative path".format(where))
    if not (isinstance(entry.get("source_digest"), str) and _DIGEST_RE.match(entry["source_digest"])):
        f.append("{}: source_digest must be `sha256:`+64 lowercase hex".format(where))
    size = entry.get("size")
    if not (type(size) is int and size >= 0):
        f.append("{}: size must be a non-negative integer".format(where))
        size = None
    lc = entry.get("line_count")
    if not (type(lc) is int and lc >= 0):
        f.append("{}: line_count must be a non-negative integer".format(where))
    spans = entry.get("span")
    if not isinstance(spans, list):
        f.append("{}: span must be a list of byte-range spans".format(where))
        return f
    parsed = []
    for i, s in enumerate(spans):
        sw = "{} span[{}]".format(where, i)
        if not isinstance(s, dict) or set(s) != _SPAN_KEYS:
            f.append("{}: a span is a closed keyset {{range, class, line_range, record, field, defaulted, "
                     "note}}".format(sw))
            continue
        rng = s.get("range")
        ok_rng = (isinstance(rng, list) and len(rng) == 2 and all(type(x) is int for x in rng)
                  and 0 <= rng[0] < rng[1] and (size is None or rng[1] <= size))
        if not ok_rng:
            f.append("{}: range must be a non-empty [start, end) within [0, size]".format(sw))
        cls = s.get("class")
        if cls not in LOSS_CLASSES:
            f.append("{}: class {!r} is not one of {}".format(sw, cls, list(LOSS_CLASSES)))
        lr = s.get("line_range")
        if not (isinstance(lr, list) and len(lr) == 2 and all(type(x) is int for x in lr)
                and 1 <= lr[0] <= lr[1]):
            f.append("{}: line_range must be a 1-based [first, last] with first <= last".format(sw))
        if not isinstance(s.get("record"), str):
            f.append("{}: record must be a string".format(sw))
        elif s.get("record") and cls not in _MAPPING_CLASSES:
            f.append("{}: a non-empty record is legal only on a mapping-class span".format(sw))
        if not isinstance(s.get("field"), str):
            f.append("{}: field must be a string".format(sw))
        if type(s.get("defaulted")) is not bool:
            f.append("{}: defaulted must be a boolean".format(sw))
        if not isinstance(s.get("note"), str):
            f.append("{}: note must be a string".format(sw))
        if ok_rng:
            parsed.append((rng[0], rng[1]))
    # The tiling invariant (the loss-accounting-completeness guarantee): spans, sorted by start, cover
    # [0, size) with no gap and no overlap. An empty source (size 0) tiles with zero spans.
    if size is not None and not f:
        parsed.sort()
        cursor = 0
        for a, b in parsed:
            if a != cursor:
                if a > cursor:
                    f.append("{}: byte range [{}, {}) is unaccounted (a gap in the loss accounting)".format(
                        where, cursor, a))
                else:
                    f.append("{}: byte range [{}, {}) is double-accounted (an overlap)".format(where, a, b))
                break
            cursor = b
        else:
            if cursor != size:
                f.append("{}: byte range [{}, {}) is unaccounted (the accounting does not reach "
                         "size)".format(where, cursor, size))
    return f


def validate_lossy_report(report):
    """Validate a lossy report against its closed schema, the per-source tiling invariant, and its content
    digest. Returns a findings list (empty means valid). Fail-closed: an unparseable payload is findings,
    never a silent pass. This is the engine the gate's loss-accounting-completeness check drives."""
    if not isinstance(report, dict):
        return ["lossy report is not a table"]
    f = []
    extra = set(report) - _REPORT_KEYS
    if extra:
        f.append("lossy report carries unknown key(s): {}".format(", ".join(sorted(str(k) for k in extra))))
    if report.get("format") != LOSSY_FORMAT:
        f.append("lossy report.format is not {!r}".format(LOSSY_FORMAT))
    sch = report.get("schema")
    if not (type(sch) is int and sch == SCHEMA):
        f.append("lossy report.schema is not {!r}".format(SCHEMA))
    sources = report.get("source")
    if not isinstance(sources, list):
        f.append("lossy report.source must be a list of per-source entries")
        return f
    for i, entry in enumerate(sources):
        f.extend(_validate_source_entry(entry, "lossy report.source[{}]".format(i)))
    payload = {"format": report.get("format"), "schema": report.get("schema"), "source": sources}
    try:
        recomputed = "sha256:" + _opf_import._sha256_hex(_lossy_bytes(payload))
    except _ImporterError as exc:
        f.append("lossy report payload is not canonically emittable ({})".format(exc.message))
        return f
    if report.get("report_digest") != recomputed:
        f.append("report_digest {!r} does not recompute over the source set (got {!r})".format(
            report.get("report_digest"), recomputed))
    return f


# --- the validator gate: the single yes/no for BOTH paths ---------------------------------------------

def _validate_candidate(candidate, validate_record):
    """Validate one id-less candidate draft envelope through the REAL record validator (the single record
    schema authority both paths pass). A synthetic well-formed id, and (for a full envelope) a synthetic
    updated_at, are injected for VALIDATION ONLY (the counter mints the id and the apply clock stamps
    updated_at at promotion, MIG-PR5), so the draft is proven schema-valid modulo the apply-time mint.
    Returns a RecordValidation."""
    if not (isinstance(candidate, dict) and set(candidate) == {"draft_ref", "type", "record"}):
        return _opf_schema.RecordValidation(_opf_store.CANNOT_EVALUATE,
                                            ["a candidate is a closed {draft_ref, type, record} table"])
    ctype = candidate["type"]
    rec = candidate["record"]
    if not isinstance(rec, dict):
        return _opf_schema.RecordValidation(_opf_store.CANNOT_EVALUATE, ["candidate.record must be a table"])
    ns = _opf_schema.BASELINE_TYPES.get(ctype)
    if ns is None:
        return _opf_schema.RecordValidation(_opf_store.CANNOT_EVALUATE,
                                            ["candidate.type {!r} is not a baseline record type".format(ctype)])
    rec = copy.deepcopy(rec)
    if not rec.get("id"):
        rec["id"] = "{}-1".format(ns)
    if ctype != _WORKLOG_TYPE and not rec.get("updated_at"):
        rec["updated_at"] = _SYNTH_STAMP
    return validate_record(rec, expected_type=ctype)


def validate_importer_output(result, source, validate_record=None):
    """The single validator gate for BOTH the deterministic and the assistant-guided paths. Judged by the
    returned (verdict, findings), never by grepping. It checks, fail-closed:
      1. the loss entry tiles [0, size) exactly and binds THIS source (path / digest / size);
      2. every proposal is accepted by `plan_import`'s OWN `_validate_proposals` (confinement, span within
         size, state vocabulary), so a proposal this layer emits is one plan_import will accept;
      3. the proposal set is EXACTLY the set the spans warrant (bijection), so a proposal cannot name a
         span the accounting does not carry, and an unclean span cannot be hidden from review;
      4. every candidate envelope is VALID through the real record validator.
    A cannot-evaluate anywhere (a malformed report, an unidentifiable candidate type) routes to
    CANNOT-EVALUATE (the safe outcome), never a clean pass; a schema INVALID or a mismatch is a FINDING.
    A cannot-evaluate is never a clean or a finding-free verdict (guard-input-soundness)."""
    if validate_record is None:
        validate_record = _opf_schema.validate_record
    findings = []
    cannot = False
    if not isinstance(result, ImporterResult):
        return CANNOT_EVALUATE, ["validate target is not an ImporterResult"]
    if result.verdict != CLEAN:
        return result.verdict, list(result.findings) or ["importer did not produce a clean result"]

    entry = result.lossy
    entry_findings = _validate_source_entry(entry, "lossy")
    if entry_findings:
        cannot = True
        findings.extend(entry_findings)
    else:
        if entry.get("source_path") != source["path"]:
            findings.append("lossy entry source_path does not bind the source")
        if entry.get("source_digest") != "sha256:" + source["sha256"]:
            findings.append("lossy entry source_digest does not bind the source bytes")
        if entry.get("size") != source["size"]:
            findings.append("lossy entry size does not bind the source size")

    # 2. proposals accepted by plan_import's own validator (the composition contract).
    try:
        _opf_import._validate_proposals(result.proposals, {source["path"]: source["size"]})
    except _opf_import._StageError as exc:
        if exc.verdict == CANNOT_EVALUATE:
            cannot = True
        findings.append("proposals rejected by plan_import's validator: {}".format(exc.message))

    # 3. proposal<->span bijection.
    if not entry_findings:
        expected = sorted((tuple(pr["span"]), pr["suggested_state"], pr["note"])
                          for pr in (_proposal_for(source["path"], sp) for sp in entry["span"])
                          if pr is not None)
        got = sorted((tuple(pr["span"]), pr["suggested_state"], pr["note"]) for pr in result.proposals)
        if got != expected:
            findings.append("the proposal set is not the exact set the loss spans warrant (a proposal "
                            "names a span the accounting does not carry, or an unclean span has no "
                            "proposal)")
        # every mapping span with a record ref has a matching candidate, and vice versa.
        span_refs = {sp["record"] for sp in entry["span"] if sp["record"]}
        cand_refs = {c.get("draft_ref") for c in result.candidates if isinstance(c, dict)}
        if span_refs != cand_refs:
            findings.append("candidate draft-refs do not match the mapped spans exactly")

    # 4. candidate envelopes through the real record validator.
    for i, cand in enumerate(result.candidates):
        rv = _validate_candidate(cand, validate_record)
        if rv.status == _opf_store.CANNOT_EVALUATE:
            cannot = True
            findings.append("candidate[{}] cannot be evaluated ({})".format(i, "; ".join(rv.findings)))
        elif rv.status != _opf_store.VALID:
            findings.append("candidate[{}] is not a valid {} draft ({})".format(
                i, cand.get("type") if isinstance(cand, dict) else "?", "; ".join(rv.findings)))

    if cannot:
        return CANNOT_EVALUATE, findings
    if findings:
        return FINDING, findings
    return CLEAN, []


def validate_assistant_output(result, source, validate_record=None):
    """The assistant-guided contract (plan section 5.3). Assistant output is UNTRUSTED generated data
    (SECI-output-handling, inter-agent-trust): it drafts the SAME structures for a non-conforming source
    and passes the IDENTICAL validator gate as a deterministic importer, with two additional invariants:
      - it assigns NO permanent id (a candidate carrying an `id` is refused: no id without human
        confirmation, spec 6);
      - a validator PASS does NOT clear an unresolved loss span: an ambiguous / conflict / unparsed span
        keeps the result NON-clean, so it can never auto-promote and still requires human acceptance.
    Returns (verdict, findings). A clean gate result with a permanent id present is downgraded to a
    FINDING; the loss-span invariant is reported through `result.clean` and echoed here."""
    verdict, findings = validate_importer_output(result, source, validate_record)
    findings = list(findings)
    for i, cand in enumerate(result.candidates):
        if isinstance(cand, dict) and isinstance(cand.get("record"), dict) and cand["record"].get("id"):
            findings.append("assistant candidate[{}] carries a permanent id; a deterministic or assistant "
                            "importer assigns no id without human confirmation (spec 6)".format(i))
            if verdict == CLEAN:
                verdict = FINDING
    return verdict, findings


def is_clean(result):
    """True iff an importer result has no ambiguous / conflict / unparsed span (only such a result may
    auto-promote; a non-clean result may still produce a candidate for review)."""
    return isinstance(result, ImporterResult) and result.verdict == CLEAN and result.clean


# --- self-test ---------------------------------------------------------------------------------------

def self_test():
    """Importer + loss-accounting invariants over synthetic in-memory sources, one discriminating vector
    per guarantee (change-carries-check). Judged on returned verdict / finding values, never by grepping.
    Pure: no filesystem, no tempdir."""
    failures = []

    def check(label, cond):
        if not cond:
            failures.append(label)

    def mk(path, text):
        raw = text.encode("utf-8")
        return {"path": path, "raw": raw, "body": text,
                "sha256": _opf_import._sha256_hex(raw), "size": len(raw)}

    def tiles(entry):
        """True iff the entry's spans tile [0, size) exactly (independent recompute, not the validator)."""
        cursor = 0
        for s in sorted(entry["span"], key=lambda s: s["range"][0]):
            if s["range"][0] != cursor:
                return False
            cursor = s["range"][1]
        return cursor == entry["size"]

    # 1. github-tasklist: a clean task list maps to backlog_item candidates, tiles exactly, gate CLEAN.
    src = mk("legacy/TODO.md", "# Tasks\n\n- [ ] write the spec\n- [x] land the engine\n")
    r = import_github_tasklist(src)
    check("github-clean-verdict", r.verdict == CLEAN and r.clean is True)
    check("github-clean-tiles", tiles(r.lossy))
    check("github-clean-two-candidates",
          len(r.candidates) == 2 and {c["type"] for c in r.candidates} == {_BACKLOG_TYPE})
    check("github-clean-status-map",
          r.candidates[0]["record"]["status"] == "open" and r.candidates[1]["record"]["status"] == "done")
    check("github-clean-gate", validate_importer_output(r, src) == (CLEAN, []))
    # composition proof: the proposals this layer emits are exactly what plan_import accepts.
    norm = _opf_import._validate_proposals(r.proposals, {src["path"]: src["size"]})
    check("github-proposals-accepted-by-plan_import",
          len(norm) == 2 and all(p["suggested_state"] == "mapped" for p in norm))

    # 2. github-tasklist non-clean: a prose line is `unparsed` (never coerced) -> clean False, gate CLEAN.
    src2 = mk("legacy/TODO2.md", "- [ ] real task\nrandom prose line\n")
    r2 = import_github_tasklist(src2)
    check("github-unparsed-nonclean", r2.verdict == CLEAN and r2.clean is False
          and any(s["class"] == "unparsed" for s in r2.lossy["span"]))
    check("github-unparsed-still-well-formed", validate_importer_output(r2, src2)[0] == CLEAN)

    # 3. keepachangelog: a dated entry under a mapped category maps to a worklog draft (time defaulted).
    kac = ("# Changelog\n\n## [1.2.0] - 2026-03-04\n\n### Added\n\n- a new capability\n")
    src3 = mk("legacy/CHANGELOG.md", kac)
    r3 = import_keepachangelog(src3)
    check("kac-clean", r3.verdict == CLEAN and r3.clean is True and tiles(r3.lossy))
    check("kac-worklog-candidate",
          len(r3.candidates) == 1 and r3.candidates[0]["type"] == _WORKLOG_TYPE
          and r3.candidates[0]["record"]["date"] == "2026-03-04T00:00:00Z"
          and r3.candidates[0]["record"]["kind"] == "added")
    check("kac-time-defaulted",
          any(s["class"] == "mapped" and s["defaulted"] is True for s in r3.lossy["span"]))
    check("kac-clean-gate", validate_importer_output(r3, src3) == (CLEAN, []))

    # 4. keepachangelog ambiguous: an Unreleased entry has no date -> ambiguous span + `ambiguous` proposal.
    src4 = mk("legacy/CL2.md", "## [Unreleased]\n\n### Added\n\n- undated thing\n")
    r4 = import_keepachangelog(src4)
    check("kac-unreleased-ambiguous", r4.clean is False
          and any(s["class"] == "ambiguous" for s in r4.lossy["span"])
          and any(p["suggested_state"] == "ambiguous" for p in r4.proposals))

    # 5. keepachangelog conflict: a duplicate version block -> conflict span + `cannot_evaluate` proposal.
    src5 = mk("legacy/CL3.md",
              "## [1.0.0] - 2026-01-01\n\n### Added\n\n- x\n\n## [1.0.0] - 2026-01-02\n\n### Fixed\n\n- y\n")
    r5 = import_keepachangelog(src5)
    check("kac-duplicate-conflict", r5.clean is False
          and any(s["class"] == "conflict" for s in r5.lossy["span"])
          and any(p["suggested_state"] == "cannot_evaluate" for p in r5.proposals))

    # 5b. keepachangelog invalid calendar date DISCRIMINATOR: a syntactically-valid but impossible date
    # (2026-13-45) is NOT mapped clean; the header and its entry are both `ambiguous` (no mapped span, so an
    # entry never carries a date that cannot exist). Without the calendar check the entry would map clean.
    src5b = mk("legacy/CL4.md", "## [1.3.0] - 2026-13-45\n\n### Added\n\n- a thing on an impossible day\n")
    r5b = import_keepachangelog(src5b)
    check("kac-invalid-date-ambiguous", r5b.clean is False and tiles(r5b.lossy)
          and not any(s["class"] == "mapped" for s in r5b.lossy["span"])
          and sum(1 for s in r5b.lossy["span"] if s["class"] == "ambiguous") >= 2)

    # 6. aiqt-face exact: a generated face is a derived projection -> ignored/preserved, no candidate, clean.
    face = ("<!-- GENERATED by opf render --write (opf/tools/_opf_views.py). DO NOT EDIT; edit the store "
            "and regenerate.\nsources: x\nschema: 1; generator: opf-views/2\nsource-set-digest: sha256:z\n"
            "regenerate: opf render --write\n-->\n# TODO\n\n- BI-1 (open): do a thing\n")
    src6 = mk("legacy/TODO-view.md", face)
    r6 = import_aiqt_face(src6)
    check("face-exact-clean",
          r6.verdict == CLEAN and r6.clean is True and r6.candidates == [] and r6.proposals == []
          and tiles(r6.lossy)
          and any(s["class"] == "preserved_verbatim" for s in r6.lossy["span"]))
    check("face-exact-gate", validate_importer_output(r6, src6) == (CLEAN, []))

    # 7. aiqt-face drifted: a body line off the exact grammar -> ambiguous (possible hand edit), clean False.
    face_drift = face.replace("- BI-1 (open): do a thing\n", "a hand-added paragraph\n")
    src7 = mk("legacy/TODO-drift.md", face_drift)
    r7 = import_aiqt_face(src7)
    check("face-drift-ambiguous",
          r7.verdict == CLEAN and r7.clean is False
          and any(s["class"] == "ambiguous" for s in r7.lossy["span"]))

    # 7b. aiqt-face UNCLOSED header DISCRIMINATOR: a face whose header comment is missing its closing `-->`
    # must NOT be swallowed as all-header/clean; only line 1 is header and the body after it is `ambiguous`
    # (drift). Without the header-close check every line would be `ignored_by_declared_rule` and clean True.
    face_open = face.replace("regenerate: opf render --write\n-->\n", "regenerate: opf render --write\n")
    src7b = mk("legacy/TODO-open.md", face_open)
    r7b = import_aiqt_face(src7b)
    check("face-unclosed-header-ambiguous",
          r7b.verdict == CLEAN and r7b.clean is False and tiles(r7b.lossy)
          and any(s["class"] == "ambiguous" for s in r7b.lossy["span"]))

    # 8. aiqt-face declines a non-face source (verdict FINDING, empty payload).
    r8 = import_aiqt_face(mk("legacy/plain.md", "# just a doc\n"))
    check("face-declines-non-face", r8.verdict == FINDING and r8.candidates == [] and r8.lossy == {})

    # 9. loss-accounting completeness DISCRIMINATOR: a clean report validates; planting a gap FAILS.
    report, _dg = build_lossy_report([r3.lossy])
    check("lossy-clean-validates", validate_lossy_report(report) == [])
    holed = copy.deepcopy(report)
    holed["source"][0]["span"] = holed["source"][0]["span"][:-1]        # drop the last span -> a gap to size
    holed2, _dg2 = build_lossy_report(holed["source"])                  # restamp so only the GAP is under test
    check("lossy-gap-fails-completeness",
          any("unaccounted" in m for m in validate_lossy_report(holed2)))
    overlap = copy.deepcopy(report)
    if overlap["source"][0]["span"]:
        overlap["source"][0]["span"][0]["range"][1] += 1               # overlap the first two spans
    ov2, _dg3 = build_lossy_report(overlap["source"])
    check("lossy-overlap-fails", validate_lossy_report(ov2) != [])
    # digest drift is caught too (integrity anchor).
    tampered = dict(report, report_digest="sha256:" + "0" * 64)
    check("lossy-digest-drift-fails", validate_lossy_report(tampered) != [])

    # 10. proposals confinement DISCRIMINATOR: a hand-built out-of-range proposal is a finding at the gate.
    bad = import_github_tasklist(src)
    bad.proposals = list(bad.proposals) + [{"source_path": src["path"], "span": [0, src["size"] + 1],
                                            "suggested_state": "mapped", "note": ""}]
    check("gate-out-of-range-proposal-finding", validate_importer_output(bad, src)[0] == FINDING)

    # 11. cross-consistency DISCRIMINATOR: a proposal naming a span the accounting does not carry fails.
    bad2 = import_github_tasklist(src)
    bad2.proposals = [dict(bad2.proposals[0], span=[bad2.proposals[0]["span"][0],
                                                    bad2.proposals[0]["span"][1] - 1])]
    check("gate-proposal-span-mismatch-finding", validate_importer_output(bad2, src)[0] == FINDING)

    # 12. candidate schema DISCRIMINATOR: an invalid status on a candidate is a gate FINDING.
    bad3 = import_github_tasklist(mk("legacy/one.md", "- [ ] one\n"))
    bad3.candidates[0]["record"]["status"] = "not-a-state"
    check("gate-bad-candidate-status-finding", validate_importer_output(bad3, mk("legacy/one.md",
                                                                                 "- [ ] one\n"))[0] == FINDING)

    # 13. cannot-evaluate routing: an unidentifiable candidate type routes to CANNOT-EVALUATE, not a pass.
    bad4 = import_github_tasklist(mk("legacy/one2.md", "- [ ] one\n"))
    bad4.candidates[0]["type"] = "not_a_type"
    check("gate-unknown-type-cannot-evaluate",
          validate_importer_output(bad4, mk("legacy/one2.md", "- [ ] one\n"))[0] == CANNOT_EVALUATE)

    # 14. assistant contract: an assistant-shaped result passes the identical gate; a permanent id fails it.
    asrc = mk("legacy/assist.md", "- [ ] assisted item\n")
    ares = import_github_tasklist(asrc)          # a deterministic result stands in for the assistant shape
    check("assistant-passes-identical-gate", validate_assistant_output(ares, asrc) == (CLEAN, []))
    ares2 = import_github_tasklist(asrc)
    ares2.candidates[0]["record"]["id"] = "BI-7"
    check("assistant-permanent-id-refused", validate_assistant_output(ares2, asrc)[0] == FINDING)

    # 15. malformed source record -> CANNOT-EVALUATE (fail-closed at the boundary), never a silent skip.
    check("malformed-source-cannot-evaluate",
          import_github_tasklist({"path": "x", "raw": b"\xff\xfe", "sha256": "0" * 64, "size": 2}).verdict
          == CANNOT_EVALUATE)
    check("unknown-kind-finding", run_importer("nope", src).verdict == FINDING)

    # 16. empty source: zero lines, zero spans, tiles trivially, gate CLEAN.
    esrc = mk("legacy/empty.md", "")
    er = import_github_tasklist(esrc)
    check("empty-source-clean", er.verdict == CLEAN and er.lossy["span"] == []
          and er.lossy["size"] == 0 and validate_importer_output(er, esrc) == (CLEAN, []))

    if failures:
        for x in failures:
            print("OPF-IMPORTERS SELF-TEST FAIL: {}".format(x), file=sys.stderr)
        return 1
    print("OPF-IMPORTERS SELF-TEST PASS: three deterministic importers (github-tasklist -> backlog_item, "
          "keepachangelog -> worklog with a defaulted time-of-day, aiqt-face recognizing an exact "
          "generated projection and flagging drift ambiguous) tile every source byte into the six-class "
          "loss accounting, each with a fail-without-it discriminator (unparsed prose, undated ambiguity, "
          "duplicate-version conflict, drifted face); the loss report tiles [0, size) exactly and a planted "
          "gap / overlap / digest-drift fails validate_lossy_report; the validator gate re-uses "
          "plan_import's own proposal validator (confinement + composition), enforces the proposal<->span "
          "bijection, validates every candidate envelope through the real _opf_schema.validate_record, "
          "routes an unidentifiable candidate to CANNOT-EVALUATE, and the assistant contract passes the "
          "identical gate while refusing a permanent id; a malformed / non-UTF-8 source and an unknown "
          "kind fail closed.")
    return 0


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if args == ["--self-test"]:
        return self_test()
    if args:
        print("_opf_importers: unexpected argument(s): {}".format(" ".join(args)), file=sys.stderr)
        return 2
    print("_opf_importers: the shared import layer (deterministic importers + byte-range loss accounting + "
          "validator gate); run with --self-test to exercise it (no verb is wired in this slice, MIG-PR2).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
