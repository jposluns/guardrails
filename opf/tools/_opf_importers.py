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
import bisect
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


def _is_canonical_source_path(p):
    """True iff `p` is a contained root-relative path in the SAME canonical form `_require_source` enforces
    (via `_journal._check_rel`): no '.', '..', empty, or otherwise non-canonical segment. A path alias such
    as `a/./b`, `a//b`, or `a/x/../b` names the SAME source as its canonical form but is a DISTINCT string,
    so accepting it at a report boundary would let an alias slip a second entry past the by-string
    source-uniqueness check (path aliases must not bypass source uniqueness). Both the producer and the
    report-validator boundaries enforce this predicate before the uniqueness check, matching the producer
    (`_require_source` / `_journal._check_rel`) so a canonicalization gap cannot open between them."""
    if not (isinstance(p, str) and _opf_store._is_contained_relpath(p)):
        return False
    try:
        _journal._check_rel(p)
    except _journal.JournalError:
        return False
    return True


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

# A TOP-LEVEL task item ONLY (no leading indentation): a task at column 0. A leading-whitespace task line is
# a NESTED item, outside the declared grammar (see _NESTED_TASK_RE), and is never flattened into a candidate.
_TASK_RE = re.compile(r"^[-*+]\s+\[(?P<mark>[ xX])\]\s+(?P<text>.*\S)\s*$")
# A NESTED (indented) task item: a task marker preceded by leading whitespace. Nesting expresses a
# parent/child relationship this v1 grammar does not model, so such a line is classified `ambiguous`
# (unresolved) rather than flattened into a second top-level candidate.
_NESTED_TASK_RE = re.compile(r"^\s+[-*+]\s+\[[ xX]\]\s")
_HEADING_RE = re.compile(r"^#{1,6}\s+\S")

_GITHUB_RESIDUAL = ("github-tasklist recognizes TOP-LEVEL GitHub-flavoured task-list items (`- [ ] ` / "
                    "`- [x] ` at column 0) as backlog_item candidates (unchecked -> open, checked -> done). "
                    "Assignees, labels, due dates, and multi-line bodies are OUTSIDE the declared grammar: "
                    "a non-task, non-heading, non-blank line is `unparsed` (never coerced), and a NESTED "
                    "(indented) task item is `ambiguous` (nesting is not modelled in v1 and is never "
                    "flattened into a second flat candidate); blank lines and structural headings are "
                    "`ignored_by_declared_rule`.")


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
        elif _NESTED_TASK_RE.match(line):
            cells.append(_cell(start, end, line_no, "ambiguous",
                               note="nested (indented) task item; nesting is outside the declared GFM "
                                    "task-list grammar and is not flattened into a candidate"))
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
# Changelog HEADING BOUNDARIES, recognized as boundaries INDEPENDENT of whether the heading text matches a
# supported grouping (version / unreleased / category), and independent of leading indentation or an empty
# heading body. A heading breaks the structural grouping, so it INVALIDATES the parser date/category
# context: with the context cleared a following bullet cannot inherit a prior version's date or a prior
# category's kind and be fabricated into a mapping across the boundary.
#   - `_KAC_H1_BOUNDARY_RE` : a level-1 heading (`# Changelog`, `# Appendix`, an indented `  # x`, or a bare
#     `#`). It is a top-level structural boundary (a title or a later top-level section), classified
#     `ignored_by_declared_rule`, and it RESETS the context so a later section cannot inherit a prior date.
#   - `_KAC_HEADING_BOUNDARY_RE` : a level-2..6 heading that no recognized rule matched (an unknown `### ...`
#     section, a malformed `## [2] - nonsense`, an indented `  ### x`, or a bare `##` / `###`). It is drift
#     (`ambiguous`) and likewise RESETS the context.
# The two are checked AFTER the recognized version / unreleased / category / bullet rules, so a real
# grouping is consumed first and only an unrecognized or malformed heading reaches these.
_KAC_H1_BOUNDARY_RE = re.compile(r"^\s*#(?:\s|$)")
_KAC_HEADING_BOUNDARY_RE = re.compile(r"^\s*#{2,6}(?:\s|$)")
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
                 "are `conflict`; free prose is `unparsed`. A HEADING is recognized as a structural boundary "
                 "INDEPENDENT of whether its text matches a supported grouping and independent of leading "
                 "indentation or an empty body: a level-1 heading (`# Changelog`, `# Appendix`, indented or "
                 "bare) is a top-level boundary (`ignored`), and an UNRECOGNIZED or malformed level-2..6 "
                 "grouping heading (an unknown `### ...` section, an indented `  ### ...`, a bare `##` / "
                 "`###`, or a `## [ver] - ...` whose date is not even YYYY-MM-DD shaped) is `ambiguous`. "
                 "EVERY heading boundary INVALIDATES the current date/category context, so entries after it "
                 "cannot inherit a prior version's date or a prior category's kind (they route to "
                 "`ambiguous`, never a mapping fabricated across the boundary). Multi-line entry bodies are "
                 "not recognized in v1.")


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
        elif _KAC_H1_BOUNDARY_RE.match(line):
            # A level-1 heading (the changelog title, or a later top-level section such as `# Appendix`,
            # including an indented or empty one) is a top-level structural boundary. It carries no
            # date/category grouping, so it RESETS the parser context: a following entry cannot inherit a
            # prior version's date or a prior category's kind across it (with the context cleared an orphaned
            # entry routes to `ambiguous` below). The title line itself is structural (`ignored`).
            cur_conflict = False
            cur_date = None
            cur_bad_date = None
            cur_kind = None
            cur_cat = None
            cells.append(_cell(start, end, line_no, "ignored_by_declared_rule",
                               note="changelog title / top-level heading (structural); date/category "
                                    "context reset at the boundary"))
        elif _KAC_HEADING_BOUNDARY_RE.match(line):
            # An unrecognized / malformed level-2..6 heading breaks the structural grouping, recognized as a
            # boundary independent of its text, its indentation, or an empty body (`### Unknown`, an indented
            # `  ### x`, a bare `##`, or a `## [2] - nonsense`). INVALIDATE the parser date/category context
            # so a following bullet cannot inherit a prior version's date or a prior category's kind and be
            # fabricated into a mapping across the boundary; with the context cleared those bullets route to
            # `ambiguous` (undated / unmapped) below. The heading itself is `ambiguous` drift, never silently
            # `unparsed` prose that leaves the context standing.
            cur_conflict = False
            cur_date = None
            cur_bad_date = None
            cur_kind = None
            cur_cat = None
            cells.append(_cell(start, end, line_no, "ambiguous",
                               note="unrecognized changelog heading (drift); date/category context "
                                    "invalidated so following entries cannot inherit it"))
        else:
            cells.append(_cell(start, end, line_no, "unparsed",
                               note="prose outside the Keep-a-Changelog entry grammar"))
    return _finalize(source, "keepachangelog", cells, candidates, _KAC_RESIDUAL)


# --- deterministic importer 3: exact AIQT-generated markdown faces ------------------------------------

_FACE_SENTINEL = b"<!-- GENERATED by "
_FACE_GENERATOR = "opf/tools/_opf_views.py"
# The generated-header FIELD lines that `_opf_views._header` emits between the opening sentinel line and the
# closing `-->` (spec 10.3): `sources:`, `schema:`, `source-set-digest:`, `regenerate:`. The header is a
# CONTIGUOUS run of the sentinel line plus these field lines, terminated by a line that is exactly `-->`.
# These prefixes track `_opf_views._header`'s output shape; the self-test drift-catcher renders a REAL face
# and asserts the importer recognizes it, so a change to the renderer header is caught at source.
_FACE_HEADER_FIELD_PREFIXES = ("sources:", "schema:", "source-set-digest:", "regenerate:")
# A generated RECORD line: every `_opf_views` view renders a record as `- <id> ...` where `<id>` is a
# `<NS>-<n>` record id (two-letter namespace, hyphen, positive integer with no leading zero) at column 0.
# A bullet that is NOT of this generator-emitted shape is drift (`ambiguous`), never blindly preserved.
_FACE_RECORD_RE = re.compile(r"^- [A-Z]{2}-[1-9][0-9]* ")
# A generated CONTEXTUAL REFERENCE ROW: `_opf_views.render_references` renders each captured ref under its
# record as a two-space-indented `  - <kind>: <locator>` row, where `<kind>` is one of the closed
# reference-capture kinds (spec 8.6, `_opf_schema.REF_KINDS`: path / url / doc). This is genuine generator
# output (not a hand edit), so it is `preserved_verbatim` like a record line. Tying the pattern to the
# closed kind vocabulary keeps this from re-admitting an arbitrary indented bullet (no unrestricted bullet
# acceptance): only the renderer's own contextual-row shape is recognized.
_FACE_REF_ROW_RE = re.compile(r"^  - (?:{}): \S".format("|".join(re.escape(k) for k in _opf_schema.REF_KINDS)))
# The view TITLE `_opf_views.render_references` emits (`_lines("REFERENCES", ...)`). A contextual
# reference row is generated ONLY inside this view, subordinate to a reference record line, so a row is
# recognized as generator output by its VIEW-and-record context, never by its `  - <kind>:` line prefix
# alone (a prefix-only match re-admits an orphan row under an unrelated view / section as clean).
_FACE_REFERENCES_TITLE = "REFERENCES"
# A generator-emitted level 2..6 ATX SUBSECTION heading in the face body: the multi-section views
# (render_decisions / render_pipeline / render_handoff / render_findings / render_contributions /
# render_version_md) emit `## <Subsection>` groupings at column 0. Like the H1 title it is structural and
# carries no importable record (records are the `- <NS>-<n>` bullets), so it is `ignored_by_declared_rule`,
# not drift; a multi-section render is therefore recognized clean rather than flagged as spurious drift.
_FACE_SUBHEADING_RE = re.compile(r"^#{2,6}\s+\S")
# A generated HTML comment is closed ONLY by a `--`-led terminator: the standard `-->` or the abrupt
# `--!>`. `_opf_views._html_comment_safe` neutralizes BOTH in every interpolated value, so neither can
# appear in a well-formed generated header (its opening sentinel line or any field line). A terminator on
# the OPENING sentinel line is an already-closed comment (later field-shaped content is body, not header),
# and the `--!>` alternate on any header line is a premature / forged close: either leaves header_end None
# (the comment is malformed, so NO line is trusted as header and the sentinel and every following line is
# `ambiguous` drift), so an early-closed / abruptly-closed header can no longer read as all-header/clean.
_FACE_COMMENT_TERMINATORS = ("-->", "--!>")
_FACE_EMPTY = "_No records._"

_FACE_RESIDUAL = ("aiqt-face recognizes an EXACT OPF/AIQT-generated markdown face by its generated "
                  "sentinel (`<!-- GENERATED by ... opf/tools/_opf_views.py ... -->`). An exact face is a "
                  "DERIVED projection the store REGENERATES (generated-artefact-source-only), so it carries "
                  "nothing NEW to import: the header is `ignored_by_declared_rule`, the H1 view title, a "
                  "generator-emitted level 2..6 ATX subsection heading (`## ...` etc., the multi-section "
                  "views' structural groupings), the empty-view marker, and blank lines are "
                  "`ignored_by_declared_rule`, and a rendered record line matching the generator's own "
                  "`- <NS>-<n> ...` record grammar (_opf_views' render output shape), and a two-space-indented "
                  "`  - <kind>: <locator>` contextual reference row recognized ONLY within the REFERENCES view "
                  "and under its owning reference record (render_references, `<kind>` from the closed spec-8.6 "
                  "vocabulary path / url / doc), not by its line prefix alone, is `preserved_verbatim` "
                  "with NO candidate (re-importing it would duplicate the store). A "
                  "bullet that is NOT of the generator-emitted record shape, a reference-row-shaped bullet "
                  "OUTSIDE the REFERENCES view or not under a reference record (an orphan / wrong-view row), "
                  "and any other body line that has DRIFTED from the exact generated grammar (a possible hand "
                  "edit), is "
                  "`ambiguous` and routes to review. The header is a CONTIGUOUS run of the sentinel line plus "
                  "the generated field lines `sources:` / `schema:` / `source-set-digest:` / `regenerate:` "
                  "in THAT FIXED ORDER, each exactly ONCE, terminated by a line that is exactly `-->`: the "
                  "terminator is recognized ONLY at the end of that full ordered field run, so a forged or "
                  "relocated `-->` later in the body, a terminator embedded on the OPENING sentinel line "
                  "(`... regenerate. -->`), an embedded terminator on a field line in the standard `-->` OR "
                  "the abrupt `--!>` form (`sources: x -->` / `sources: x --!>`, both neutralized by "
                  "_html_comment_safe in a genuine header), or an out-of-order / repeated / injected field "
                  "line cannot swallow real content into the header. If the header does not close cleanly at "
                  "its ordered field run (a terminator on the opening sentinel line, a dropped or mangled or "
                  "embedded `-->` / `--!>`, an injected / repeated / out-of-order field line, or a "
                  "sentinel-only source with no `-->` at all), the comment is malformed: NO line is trusted "
                  "as header and the opening sentinel and every following line is `ambiguous` drift, so an "
                  "unterminated / sentinel-only header is NON-clean rather than silently all-header/clean. "
                  "Faithful RECONSTRUCTION of records from a rendered face (the inline escape is one-way for "
                  "a markdown sink) is deferred to the assistant-guided path or an operator KEEP of the "
                  "original (spec 14.2).")


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
    # Locate the CLEAN header terminator. A generated header (_opf_views._header) is the opening sentinel
    # line (line 1), then the field lines `sources:` / `schema:` / `source-set-digest:` / `regenerate:` in
    # THAT FIXED ORDER, each exactly ONCE, then a line that is exactly `-->`. The scan below first validates
    # the OPENING sentinel line and then walks from line 2, accepting `-->` as the terminator ONLY at the end
    # of the FULL, ORDERED, unique field run. It rejects, so `header_end` stays None:
    #   - a terminator embedded on the OPENING sentinel line (`... regenerate. -->`): the comment is already
    #     closed on line 1, so any following field-shaped content is body, not header;
    #   - a field line that EMBEDS a terminator, the standard `-->` OR the abrupt `--!>` (`sources: x -->` /
    #     `sources: x --!>`): `_html_comment_safe` neutralizes BOTH in every interpolated value, so an
    #     embedded terminator can only be an early-closed / forged comment;
    #   - a field OUT OF ORDER, REPEATED, or an injected non-field line before the close (the injected line
    #     cannot advance the ordered run, so the run does not complete);
    #   - the absence of any clean terminator (a dropped / mangled `-->`, or a sentinel-only source).
    # When `header_end` stays None the generated-face comment is MALFORMED, so NO line is trusted as header:
    # the opening sentinel and every following line is `ambiguous` drift. This stops a forged / relocated
    # `-->` deeper in the body from swallowing real content into the header, AND makes an unclosed /
    # sentinel-only header NON-clean (it can no longer read as all-header and clean).
    lines = [_text(raw, s, e) for (s, e) in _tile_lines(raw)]
    header_end = None
    n_fields = len(_FACE_HEADER_FIELD_PREFIXES)
    fi = 0
    # The OPENING sentinel line (line 1) must itself carry NO comment terminator: a `-->` / `--!>` embedded
    # on the opening line is an already-closed comment, after which any field-shaped content is body, not
    # header. A genuine opening line carries neither (every interpolated value is `_html_comment_safe`d), so
    # a terminator here means the comment is malformed and header_end stays None (no line trusted as header).
    if lines and any(term in lines[0] for term in _FACE_COMMENT_TERMINATORS):
        header_end = None
    else:
        for idx in range(1, len(lines)):        # idx is 0-based, so this covers line_no idx+1 (line 1 excluded)
            t = lines[idx]
            if t.strip() == "-->":
                if fi == n_fields:              # the terminator closes the header ONLY after the full field run
                    header_end = idx + 1
                break
            if any(term in t for term in _FACE_COMMENT_TERMINATORS):
                break                           # an embedded / abrupt (`-->` / `--!>`) terminator: early-closed
            if fi < n_fields and t.startswith(_FACE_HEADER_FIELD_PREFIXES[fi]):
                fi += 1                         # the next field, IN ORDER: advance the run
                continue
            break                               # out-of-order / repeated / missing / non-field line: not clean
    in_refs_view = False   # True while the body sits under the REFERENCES view title: the only view whose
                           # records own the `  - <kind>: <locator>` contextual reference rows.
    ref_owner = False      # True immediately under a reference record line (or its run of ref rows), so a
                           # contextual reference row is recognized ONLY where render_references emits one and
                           # an orphan row under an unrelated section cannot read as generator output.
    for line_no, (start, end) in enumerate(_tile_lines(raw), start=1):
        line = _text(raw, start, end)
        if header_end is not None and line_no <= header_end:
            cells.append(_cell(start, end, line_no, "ignored_by_declared_rule",
                               note="generator header (identity / regenerate metadata)"))
            continue
        if header_end is None:
            # The header comment did not close cleanly at its ordered field run (a terminator on the opening
            # sentinel line, a dropped / mangled / relocated / embedded `-->` or abrupt `--!>`, an
            # out-of-order / repeated / injected field line, or a sentinel-only source with no `-->` at all):
            # the generated-face comment is malformed, so NO line is trusted as header. The opening sentinel
            # and every following line is `ambiguous` drift, which also makes a sentinel-only / unterminated /
            # early-closed face NON-clean rather than all-header/clean.
            cells.append(_cell(start, end, line_no, "ambiguous",
                               note="generated-face header comment did not close cleanly at its `-->` field "
                                    "run (drift / unterminated header)"))
            continue
        if not line.strip():
            ref_owner = False
            cells.append(_cell(start, end, line_no, "ignored_by_declared_rule", note="blank line"))
        elif _KAC_H1_RE.match(line):
            # The H1 view title sets the view context: only the REFERENCES view's records own contextual
            # reference rows, so track whether this face is that view (reset any pending ref-row context).
            in_refs_view = line.lstrip("#").strip() == _FACE_REFERENCES_TITLE
            ref_owner = False
            cells.append(_cell(start, end, line_no, "ignored_by_declared_rule", note="view title (structural)"))
        elif _FACE_SUBHEADING_RE.match(line):
            # A generator-emitted level 2..6 subsection heading (the multi-section views' groupings) is
            # structural and carries no importable record, mirroring the H1-title branch.
            ref_owner = False
            cells.append(_cell(start, end, line_no, "ignored_by_declared_rule",
                               note="view subsection heading (structural)"))
        elif line == _FACE_EMPTY:
            ref_owner = False
            cells.append(_cell(start, end, line_no, "ignored_by_declared_rule", note="empty-view marker"))
        elif _FACE_RECORD_RE.match(line):
            # A `- <NS>-<n> ...` record line; in the REFERENCES view it OWNS the contextual reference rows
            # rendered immediately under it (render_references emits each ref directly beneath its record).
            ref_owner = True
            cells.append(_cell(start, end, line_no, "preserved_verbatim",
                               note="derived view line (regenerated from the store; not re-imported)"))
        elif _FACE_REF_ROW_RE.match(line) and in_refs_view and ref_owner:
            # A contextual reference row recognized ONLY within the REFERENCES view and under its owning
            # reference record (the run of rows stays owned), never by its line prefix alone: an orphan row
            # under an unrelated section, or a row outside the REFERENCES view, falls through to `ambiguous`.
            cells.append(_cell(start, end, line_no, "preserved_verbatim",
                               note="derived contextual reference row (render_references; not re-imported)"))
        else:
            ref_owner = False
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
    # One entry per source identity (reject a doubled source at the producer boundary, never emit a report
    # that double-counts a source; the validator enforces the same invariant on an untrusted report).
    paths = [e["source_path"] for e in ordered]
    # Enforce the CANONICAL-PATH predicate BEFORE the uniqueness check, so a path alias (`a/./b`, `a//b`,
    # `a/x/../b`) cannot name the same source under a distinct string and slip past the by-string dedup.
    # This mirrors `_require_source` / `_journal._check_rel` at the producer boundary (the same predicate
    # the report-validator applies), so no canonicalization gap opens between producing and validating.
    non_canon = sorted({p for p in paths if not _is_canonical_source_path(p)})
    if non_canon:
        raise _cannot("lossy report source path is not canonical (a '.', '..', empty, or otherwise "
                      "non-canonical segment aliases the same source under a distinct string; a path alias "
                      "must not bypass source uniqueness): {}".format(", ".join(non_canon)))
    if len(paths) != len(set(paths)):
        dups = sorted({p for p in paths if paths.count(p) > 1})
        raise _cannot("lossy report names a source path more than once (a source must have exactly one loss "
                      "entry): {}".format(", ".join(dups)))
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
    # The report-validator boundary enforces the SAME canonical-path predicate as the producer
    # (`_require_source` / `_journal._check_rel`) BEFORE the by-string uniqueness check in
    # `validate_lossy_report`, so a path alias (`a/./b`, `a//b`, `a/x/../b`) cannot name the same source
    # under a distinct string and duplicate it past that check.
    if not (isinstance(sp, str) and _is_canonical_source_path(sp)):
        f.append("{}: source_path must be a canonical contained root-relative path (no '.', '..', empty, or "
                 "otherwise non-canonical segment)".format(where))
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
        # `LOSS_CLASSES` is a tuple, so membership compares by `==` and is crash-safe for an unhashable
        # (e.g. a malformed list) `cls`; the frozenset `_MAPPING_CLASSES` membership below would raise on
        # such a value, so it is guarded by `cls_ok` (only run when `cls` is a valid, hashable class).
        cls_ok = cls in LOSS_CLASSES
        if not cls_ok:
            f.append("{}: class {!r} is not one of {}".format(sw, cls, list(LOSS_CLASSES)))
        lr = s.get("line_range")
        if not (isinstance(lr, list) and len(lr) == 2 and all(type(x) is int for x in lr)
                and 1 <= lr[0] <= lr[1]):
            f.append("{}: line_range must be a 1-based [first, last] with first <= last".format(sw))
        if not isinstance(s.get("record"), str):
            f.append("{}: record must be a string".format(sw))
        elif s.get("record") and cls_ok and cls not in _MAPPING_CLASSES:
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
    # One entry per source IDENTITY: a source path names at most one entry, so the same source cannot be
    # accounted twice (two copies of one entry would each validate in isolation but double-count the source).
    seen_paths = set()
    dup_paths = set()
    for entry in sources:
        if isinstance(entry, dict):
            spath = entry.get("source_path")
            if isinstance(spath, str):
                if spath in seen_paths:
                    dup_paths.add(spath)
                seen_paths.add(spath)
    if dup_paths:
        f.append("lossy report names a source path more than once (a source must have exactly one loss "
                 "entry): {}".format(", ".join(sorted(dup_paths))))
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
    # Type-validate the untrusted scalars before use: a non-string `type` (e.g. {}) is unhashable and would
    # raise a TypeError from the `BASELINE_TYPES.get(ctype)` dict lookup below; fail closed to a named
    # cannot-evaluate verdict instead.
    if not isinstance(ctype, str):
        return _opf_schema.RecordValidation(_opf_store.CANNOT_EVALUATE,
                                            ["candidate.type must be a string"])
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
      1. the loss entry tiles [0, size) exactly and binds THIS source, validated against the RAW source
         BYTES (`_require_source` recomputes size + digest + line count from the bytes, so a caller-supplied
         digest / size that misdescribes the real bytes cannot bind), and the `clean` flag agrees with the
         loss spans (an untrusted `clean` flag is DERIVED-and-checked, never trusted);
      2. every proposal is accepted by `plan_import`'s OWN `_validate_proposals` (confinement, span within
         size, state vocabulary), so a proposal this layer emits is one plan_import will accept;
      3. the proposal set is EXACTLY the set the spans warrant (bijection, over the NORMALIZED proposals so
         a malformed proposal cannot crash the gate), a mapping ('mapped') span carries a NON-EMPTY candidate
         reference, and candidate <-> span references are UNIQUE and match exactly (no duplicate or missing
         candidate);
      4. every candidate envelope is VALID through the real record validator.
    A cannot-evaluate anywhere (a malformed report, an unreadable source, an unidentifiable candidate type)
    routes to CANNOT-EVALUATE (the safe outcome), never a clean pass; a schema INVALID or a mismatch is a
    FINDING. A cannot-evaluate is never a clean or a finding-free verdict (guard-input-soundness)."""
    if validate_record is None:
        validate_record = _opf_schema.validate_record
    findings = []
    cannot = False
    if not isinstance(result, ImporterResult):
        return CANNOT_EVALUATE, ["validate target is not an ImporterResult"]
    if result.verdict != CLEAN:
        return result.verdict, list(result.findings) or ["importer did not produce a clean result"]

    # Type-validate the untrusted top-level collections BEFORE any iteration / set-construction over them:
    # a non-list `candidates` (e.g. None) would raise a TypeError from the loops below, and a non-list
    # `proposals` from `_validate_proposals`. Fail closed to a named cannot-evaluate verdict.
    if not isinstance(result.candidates, list):
        return CANNOT_EVALUATE, ["result.candidates must be a list of candidate draft envelopes"]
    if not isinstance(result.proposals, list):
        return CANNOT_EVALUATE, ["result.proposals must be a list of inert model-proposal tables"]

    # Validate the SOURCE bytes at the boundary and bind the loss entry against values RECOMPUTED from the
    # raw bytes, not caller-supplied fields: a source whose declared digest / size misdescribes its real
    # bytes is CANNOT-EVALUATE here (guard-input-soundness), so the gate can never certify an accounting
    # against a source it did not actually measure.
    try:
        src_path, src_raw = _require_source(source)
    except _ImporterError as exc:
        return exc.verdict, [exc.message]
    src_digest = "sha256:" + _opf_import._sha256_hex(src_raw)
    src_size = len(src_raw)
    src_line_count = len(_tile_lines(src_raw))

    entry = result.lossy
    entry_findings = _validate_source_entry(entry, "lossy")
    if entry_findings:
        cannot = True
        findings.extend(entry_findings)
    else:
        if entry.get("source_path") != src_path:
            findings.append("lossy entry source_path does not bind the source")
        if entry.get("source_digest") != src_digest:
            findings.append("lossy entry source_digest does not bind the source bytes")
        if entry.get("size") != src_size:
            findings.append("lossy entry size does not bind the source size")
        # line_count / line_range are MEASURED against the real source, never accepted shape-only.
        if entry.get("line_count") != src_line_count:
            findings.append("lossy entry line_count {!r} does not match the measured source line count "
                            "({})".format(entry.get("line_count"), src_line_count))
        # Each span's line_range is DERIVED from its byte range against the real source and BOTH endpoints
        # are compared: the first physical line is the one containing the span's first byte and the last is
        # the one containing its last byte (`end - 1`). A merely in-range but FORGED line_range (e.g. [2, 2]
        # over a byte range that physically lies on line 1) no longer passes on the endpoint-under-count
        # check alone; it must equal the range the bytes actually occupy.
        line_starts = [ls for (ls, _le) in _tile_lines(src_raw)]
        for i, sp in enumerate(entry["span"]):
            rng = sp.get("range")
            lr = sp.get("line_range")
            if (isinstance(rng, list) and len(rng) == 2 and all(type(x) is int for x in rng)
                    and 0 <= rng[0] < rng[1] <= src_size
                    and isinstance(lr, list) and len(lr) == 2 and all(type(x) is int for x in lr)):
                first_line = bisect.bisect_right(line_starts, rng[0])
                last_line = bisect.bisect_right(line_starts, rng[1] - 1)
                if lr != [first_line, last_line]:
                    findings.append("lossy span[{}] line_range {} does not match the physical lines its "
                                    "byte range {} occupies ([{}, {}])".format(
                                        i, lr, rng, first_line, last_line))
        # The `clean` flag is DERIVED from the loss spans and the untrusted flag must agree with it: a
        # result.clean=True carrying an unresolved (ambiguous / conflict / unparsed) span is a FINDING.
        derived_clean = not any(sp["class"] in _UNCLEAN_CLASSES for sp in entry["span"])
        if bool(result.clean) != derived_clean:
            findings.append("result.clean {!r} contradicts the loss spans (clean iff no ambiguous / "
                            "conflict / unparsed span present)".format(bool(result.clean)))

    # 2. proposals accepted by plan_import's own validator (the composition contract). Keep the NORMALIZED
    #    output for the bijection so a malformed proposal is a verdict, never a KeyError from indexing it.
    normalized = None
    try:
        normalized = _opf_import._validate_proposals(result.proposals, {src_path: src_size})
    except _opf_import._StageError as exc:
        if exc.verdict == CANNOT_EVALUATE:
            cannot = True
        findings.append("proposals rejected by plan_import's validator: {}".format(exc.message))

    if not entry_findings:
        # 3a. proposal<->span bijection, over the normalized proposals (skipped when they did not validate).
        if normalized is not None:
            expected = sorted((tuple(pr["span"]), pr["suggested_state"], pr["note"])
                              for pr in (_proposal_for(src_path, sp) for sp in entry["span"])
                              if pr is not None)
            got = sorted((tuple(pr["span"]), pr["suggested_state"], pr["note"]) for pr in normalized)
            if got != expected:
                findings.append("the proposal set is not the exact set the loss spans warrant (a proposal "
                                "names a span the accounting does not carry, or an unclean span has no "
                                "proposal)")
        # 3b. candidate <-> span reference consistency: a 'mapped' span MUST carry a non-empty candidate
        #     reference, and references are UNIQUE and match exactly (multiplicity-aware, so a duplicate or
        #     missing candidate cannot hide behind a set comparison).
        for i, sp in enumerate(entry["span"]):
            if sp["class"] == "mapped" and not sp["record"]:
                findings.append("lossy span[{}] is class 'mapped' but carries no candidate record "
                                "reference".format(i))
        span_ref_list = [sp["record"] for sp in entry["span"] if sp["record"]]
        cand_ref_list = [c.get("draft_ref") for c in result.candidates if isinstance(c, dict)]
        # Type-validate the untrusted candidate draft-refs BEFORE any set-construction over them: a non-string
        # (e.g. a list) draft_ref is unhashable and would raise a TypeError from `set(cand_ref_list)`. A
        # malformed draft-ref is a FINDING, and the multiplicity / bijection comparisons (which need the set)
        # are skipped for this untrusted set once it is known malformed.
        if not all(isinstance(r, str) for r in cand_ref_list):
            findings.append("a candidate draft_ref is not a string (a draft_ref must be a string)")
        else:
            if len(span_ref_list) != len(set(span_ref_list)):
                findings.append("a candidate record reference is shared by more than one mapped span "
                                "(duplicate mapping)")
            if len(cand_ref_list) != len(set(cand_ref_list)):
                findings.append("a candidate draft-ref is shared by more than one candidate (duplicate "
                                "candidate)")
            if set(span_ref_list) != set(cand_ref_list):
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
    # The permanent-id check dereferences result.candidates; guard the untrusted structure the SAME way the
    # gate does. validate_importer_output already returned a named refusing verdict for a non-list candidates
    # (or a non-ImporterResult), so this second public entry point must never crash iterating a rejected
    # structure (e.g. candidates=None -> TypeError) rather than returning that verdict.
    candidates = (result.candidates if isinstance(result, ImporterResult)
                  and isinstance(result.candidates, list) else [])
    for i, cand in enumerate(candidates):
        if isinstance(cand, dict) and isinstance(cand.get("record"), dict) and cand["record"].get("id"):
            findings.append("assistant candidate[{}] carries a permanent id; a deterministic or assistant "
                            "importer assigns no id without human confirmation (spec 6)".format(i))
            if verdict == CLEAN:
                verdict = FINDING
    return verdict, findings


def is_clean(result):
    """True iff an importer result has no ambiguous / conflict / unparsed span (only such a result may
    auto-promote; a non-clean result may still produce a candidate for review). Cleanliness is DERIVED from
    the validated loss spans, NOT read from the (untrusted, possibly assistant-supplied) `result.clean`
    flag: a result carrying an unresolved span is never clean here even if its flag claims otherwise. The
    gate (`validate_importer_output`) separately REJECTS an inconsistent flag as a finding. Cleanliness is
    derived only from a WELL-FORMED loss entry: the entry's closed shape AND the tiling invariant (every
    span carries its range / accounting fields and the spans tile [0, size) exactly) are validated before
    any class is read, so a span with a RECOGNIZED class but missing ranges / accounting (`{"class":
    "mapped"}`), or an empty span list over a NON-empty source, is fail-closed to NON-clean rather than
    read as clean. A MALFORMED span (not a table, or carrying no recognized loss class) is likewise NON-clean:
    cleanliness cannot be derived from a span whose class is missing or unknown, so it is never a clean pass
    (guard-input-soundness). `LOSS_CLASSES` is a tuple, so the membership below is crash-safe for an
    unhashable class."""
    if not (isinstance(result, ImporterResult) and result.verdict == CLEAN):
        return False
    if not isinstance(result.lossy, dict):
        return False
    # Validate the loss entry's shape and its [0, size) tiling BEFORE deriving cleanliness from the spans:
    # a recognized-class-but-malformed span, or an empty span set over a non-empty source, is caught here
    # (fail-closed) rather than mis-read as clean from a membership check alone.
    if _validate_source_entry(result.lossy, "is_clean"):
        return False
    for sp in result.lossy["span"]:
        cls = sp.get("class")
        if cls not in LOSS_CLASSES:          # a missing / unknown / malformed class -> cannot derive clean
            return False
        if cls in _UNCLEAN_CLASSES:
            return False
    return True


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

    # --- codex-backstop fix-forward discriminators (each fails WITHOUT its fix) -------------------------

    # 17. MAJOR 1 (derive-clean-from-spans): an untrusted `clean=True` flag on a result carrying an
    # unresolved (unparsed) span is REJECTED. is_clean derives from the spans (never trusts the flag), and
    # the gate raises a FINDING on the inconsistent flag. Without the fix is_clean trusts the flag (True) and
    # the gate passes (CLEAN,[]).
    m1 = import_github_tasklist(src2)                        # src2 carries an `unparsed` prose span
    check("m1-is-clean-derives-not-trusts", m1.clean is False and is_clean(m1) is False)
    m1.clean = True                                          # forge the untrusted flag
    check("m1-is-clean-ignores-forged-flag", is_clean(m1) is False)
    check("m1-gate-rejects-inconsistent-clean", validate_importer_output(m1, src2)[0] == FINDING)

    # 18. MAJOR 2 face (record-grammar): a bullet that is NOT the generator's `- <NS>-<n> ...` record shape
    # is drift (`ambiguous`), not blindly `preserved_verbatim`. Without the fix the loose `^ *- \S` bullet
    # regex preserved it and the face read clean.
    face_forge = face.replace("- BI-1 (open): do a thing\n", "- an arbitrary hand-added bullet\n")
    m2a = import_aiqt_face(mk("legacy/FACE-forge.md", face_forge))
    check("m2a-nonrecord-bullet-ambiguous",
          m2a.verdict == CLEAN and m2a.clean is False and tiles(m2a.lossy)
          and any(s["class"] == "ambiguous" for s in m2a.lossy["span"])
          and not any(s["class"] == "preserved_verbatim" for s in m2a.lossy["span"]))
    # 18b. MAJOR 2 face (header boundary): a forged / relocated `-->` deeper in the body cannot swallow real
    # content into the header. Injected non-field lines before a later `-->` are `ambiguous` drift (not
    # `ignored_by_declared_rule` header). Without the fix the whole-file `-->` search swallowed them clean.
    face_swallow = face.replace(
        "regenerate: opf render --write\n-->\n",
        "regenerate: opf render --write\nINJECTED CONTENT LINE\nanother injected line\n-->\n")
    m2b = import_aiqt_face(mk("legacy/FACE-swallow.md", face_swallow))
    header_ignored = [s for s in m2b.lossy["span"] if s["class"] == "ignored_by_declared_rule"]
    check("m2b-forged-terminator-not-swallowed",
          m2b.verdict == CLEAN and m2b.clean is False and tiles(m2b.lossy)
          and any(s["class"] == "ambiguous" for s in m2b.lossy["span"])
          # only the opening sentinel line (line 1) remains header; the injected lines are NOT header
          and all(s["line_range"][0] == 1 for s in header_ignored))
    # 18c. MAJOR 2 face drift-catcher: a REAL face rendered by _opf_views (its genuine header field lines +
    # its `- <id> ...` record grammar) is recognized clean with a preserved_verbatim record line, tying the
    # importer's header-field prefixes and record regex to the actual renderer output (drift caught at source).
    import _opf_views
    genuine_face = (_opf_views._header({".working/toml/backlog_item.index.toml": b"data"})
                    + "\n" + _opf_views._lines("TODO", ["- BI-1 (open) do a thing"]))
    m2c = import_aiqt_face(mk("legacy/FACE-genuine.md", genuine_face))
    check("m2c-genuine-render-recognized",
          m2c.verdict == CLEAN and m2c.clean is True and tiles(m2c.lossy)
          and any(s["class"] == "preserved_verbatim" for s in m2c.lossy["span"]))

    # 19. MAJOR 3 (source-byte validation): the gate validates the SOURCE bytes via _require_source and binds
    # against values RECOMPUTED from the bytes, not caller-supplied fields. A source whose declared sha256 /
    # size LIE about its real bytes (here: raw carries real content but the record claims an EMPTY 0-byte
    # digest/size) is CANNOT-EVALUATE, and a loss entry matching the lie can no longer bind clean. Without
    # the fix the gate trusted source["sha256"] / source["size"], so the empty entry matched the lie and
    # passed with the real bytes unaccounted.
    lying_src = {"path": "legacy/lie.md", "raw": "- [ ] real\n".encode("utf-8"),
                 "sha256": _opf_import._sha256_hex(b""), "size": 0}   # raw != claimed empty digest/size
    empty_entry_res = import_github_tasklist(mk("legacy/lie.md", ""))  # a loss entry describing 0 bytes
    check("m3-source-bytes-validated",
          validate_importer_output(empty_entry_res, lying_src)[0] == CANNOT_EVALUATE)
    real10 = mk("legacy/ten.md", "- [ ] tenb\n")            # a real, well-formed source
    m3lc = import_github_tasklist(real10)
    m3lc.lossy["line_count"] = 999                          # a shape-valid but wrong measured line count
    check("m3-line-count-measured", validate_importer_output(m3lc, real10)[0] == FINDING)

    # 20. MAJOR 4 (source uniqueness): a report naming one source path twice is a finding, even though each
    # entry validates in isolation and the digest recomputes over the doubled set. Without the fix the same
    # source is silently counted twice.
    dup_payload = {"format": LOSSY_FORMAT, "schema": SCHEMA, "source": [r3.lossy, copy.deepcopy(r3.lossy)]}
    dup_report = dict(dup_payload, report_digest="sha256:" + _opf_import._sha256_hex(_lossy_bytes(dup_payload)))
    check("m4-duplicate-source-path-fails",
          any("more than once" in m for m in validate_lossy_report(dup_report)))

    # 21. MAJOR 5 (candidate consistency): a DUPLICATE candidate draft-ref (multiplicity), and a 'mapped'
    # span with an EMPTY record ref, are each a gate FINDING. Without the fix the set comparison discarded
    # multiplicity and excluded empty refs, so both passed.
    m5dup = import_github_tasklist(mk("legacy/dup.md", "- [ ] one\n"))
    m5dup.candidates = list(m5dup.candidates) + [copy.deepcopy(m5dup.candidates[0])]   # same draft_ref twice
    check("m5-duplicate-candidate-finding",
          validate_importer_output(m5dup, mk("legacy/dup.md", "- [ ] one\n"))[0] == FINDING)
    m5empty = import_github_tasklist(mk("legacy/dup2.md", "- [ ] one\n"))
    m5empty.lossy["span"][0]["record"] = ""                 # a mapped span with no candidate reference
    check("m5-mapped-span-empty-record-finding",
          validate_importer_output(m5empty, mk("legacy/dup2.md", "- [ ] one\n"))[0] == FINDING)

    # 22. MAJOR 6 (KAC context invalidation): an unrecognized changelog heading (`### Unknown`, or a
    # malformed `## [2] - nonsense`) invalidates the date/category context, so a following bullet cannot
    # inherit the prior version's date/category and be fabricated into a mapping. Without the fix the child
    # inherits and maps -> two candidates.
    kac_unknown = ("# Changelog\n\n## [1.2.0] - 2026-03-04\n\n### Added\n\n- real entry\n\n"
                   "### Unknown\n\n- inherited child\n")
    m6a = import_keepachangelog(mk("legacy/CLU.md", kac_unknown))
    check("m6-unknown-heading-invalidates-context",
          m6a.verdict == CLEAN and m6a.clean is False and tiles(m6a.lossy) and len(m6a.candidates) == 1)
    kac_badver = ("# Changelog\n\n## [1.0.0] - 2026-01-01\n\n### Added\n\n- ok\n\n"
                  "## [2] - nonsense\n\n- orphan\n")
    m6b = import_keepachangelog(mk("legacy/CLBV.md", kac_badver))
    check("m6-malformed-version-heading-invalidates-context",
          m6b.verdict == CLEAN and m6b.clean is False and tiles(m6b.lossy) and len(m6b.candidates) == 1
          and any(s["class"] == "ambiguous" for s in m6b.lossy["span"]))

    # 23. MINOR 7 (nested task): a NESTED (indented) task item is `ambiguous`, not flattened into a second
    # flat candidate. Without the fix the `^\s*` task regex accepted the indent and produced two candidates.
    m7 = import_github_tasklist(mk("legacy/nest.md", "- [ ] parent\n  - [x] child\n"))
    check("m7-nested-task-ambiguous",
          m7.verdict == CLEAN and m7.clean is False and tiles(m7.lossy) and len(m7.candidates) == 1
          and any(s["class"] == "ambiguous" for s in m7.lossy["span"]))

    # 24. MINOR 8 (proposal verdict, no KeyError): a malformed proposal shape (`[{}]`, and one with an
    # omitted `note`) returns a verdict, never a KeyError from directly indexing it. Without the fix the
    # bijection indexed the raw malformed proposals and crashed.
    m8 = import_github_tasklist(src)
    m8.proposals = [{}]
    check("m8-malformed-proposal-verdict-not-crash", validate_importer_output(m8, src)[0] == FINDING)
    m8b = import_github_tasklist(mk("legacy/note.md", "- [ ] one\n"))
    # a proposal with an omitted `note` (the reused validator permits it) still yields a verdict via the
    # NORMALIZED output; a raw-index bijection would KeyError on the missing note.
    m8b.proposals = [{"source_path": "legacy/note.md", "span": list(m8b.proposals[0]["span"]),
                      "suggested_state": "mapped"}]
    check("m8-omitted-note-proposal-verdict",
          validate_importer_output(m8b, mk("legacy/note.md", "- [ ] one\n"))[0] in (CLEAN, FINDING))

    # --- codex-confirmed residual fix-forward discriminators (each fails WITHOUT its fix) ---------------

    # 25. F1 (face header grammar): a malformed generated-face header no longer reads all-header/clean. An
    # early-closed comment (a field line embedding `-->`), an injected field-shaped line before the close,
    # and a sentinel-only unterminated header are each NON-clean (is_clean False; the header does not close
    # cleanly, so the sentinel and every following line is `ambiguous` drift). Without the fix the loose
    # any-prefix scan closed the header at a later / embedded `-->` and the sentinel-only case read clean.
    f1_embed = face.replace("sources: x\n", "sources: x -->\n")            # embedded terminator on a field line
    f1_open_embed = face.replace("and regenerate.\n", "and regenerate. -->\n")  # terminator on the OPENING sentinel line
    f1_abrupt = face.replace("sources: x\n", "sources: x --!>\n")          # the alternate `--!>` terminator (renderer-neutralized)
    f1_inject = face.replace("regenerate: opf render --write\n-->\n",
                             "regenerate: opf render --write\nsources: injected\n-->\n")  # repeated / injected field
    f1_sentinel = ("<!-- GENERATED by opf render --write (opf/tools/_opf_views.py). DO NOT EDIT; edit the "
                   "store and regenerate.\n")                              # sentinel-only, no `-->` at all
    for label, txt in (("embedded", f1_embed), ("opening-embedded", f1_open_embed), ("abrupt", f1_abrupt),
                       ("injected", f1_inject), ("sentinel-only", f1_sentinel)):
        rf1 = import_aiqt_face(mk("legacy/F1-{}.md".format(label), txt))
        check("f1-malformed-header-nonclean-{}".format(label),
              rf1.verdict == CLEAN and rf1.clean is False and is_clean(rf1) is False and tiles(rf1.lossy)
              and any(s["class"] == "ambiguous" for s in rf1.lossy["span"])
              and not any(s["class"] == "preserved_verbatim" for s in rf1.lossy["span"]))

    # 26. F2 (heading boundary independent of grouping): an indented `### Unknown`, a bare `###`, and an H1
    # `# Appendix` are each a structural boundary that RESETS the date/category context, so the following
    # bullet cannot inherit the prior version's date/category and be fabricated into a 2nd candidate. Without
    # the fix an indented / empty heading fell through to `unparsed` (context standing) and an H1 was ignored
    # without a reset, so the child inherited and mapped -> two candidates.
    for label, txt in (
            ("indented", "# Changelog\n\n## [1.2.0] - 2026-03-04\n\n### Added\n\n- real\n\n  ### Unknown\n\n- child\n"),
            ("bare", "# Changelog\n\n## [1.2.0] - 2026-03-04\n\n### Added\n\n- real\n\n###\n\n- child\n"),
            ("h1-appendix", "# Changelog\n\n## [1.2.0] - 2026-03-04\n\n### Added\n\n- real\n\n# Appendix\n\n- child\n")):
        rf2 = import_keepachangelog(mk("legacy/F2-{}.md".format(label), txt))
        check("f2-heading-boundary-resets-context-{}".format(label),
              rf2.verdict == CLEAN and rf2.clean is False and tiles(rf2.lossy)
              and len(rf2.candidates) == 1
              and any(s["class"] == "ambiguous" for s in rf2.lossy["span"]))

    # 27. F3 (path aliases bypass source uniqueness): `a/./b`, `a//b`, `a/x/../b` name the SAME source under
    # a distinct string; the report validator enforces the SAME canonical-path predicate as the producer, so
    # a non-canonical alias is a finding and cannot slip a duplicate past the by-string uniqueness check.
    # Without the fix each alias validated clean (returned []).
    f3base = import_keepachangelog(mk("legacy/F3.md",
                                      "# Changelog\n\n## [1.2.0] - 2026-03-04\n\n### Added\n\n- x\n")).lossy
    for alias in ("legacy/./F3.md", "legacy//F3.md", "legacy/a/../F3.md"):
        e = copy.deepcopy(f3base)
        e["source_path"] = alias
        f3payload = {"format": LOSSY_FORMAT, "schema": SCHEMA, "source": [e]}
        f3report = dict(f3payload, report_digest="sha256:" + _opf_import._sha256_hex(_lossy_bytes(f3payload)))
        check("f3-path-alias-not-canonical-{}".format(alias), validate_lossy_report(f3report) != [])

    # 28. F4 (malformed untrusted fields return a verdict, never a TypeError): a `candidates=None`, a
    # `draft_ref=[]`, a `type={}`, and a `span[].class=[]` are each type-validated before iteration / lookup /
    # set-construction and route to a NAMED refusing verdict. Without the fix each raised a TypeError
    # (iterating None, hashing a list into a set, a dict key into `.get()`, a list into a frozenset).
    f4src = mk("legacy/F4.md", "- [ ] one\n")
    f4a = import_github_tasklist(f4src)
    f4a.candidates = None
    check("f4-candidates-none-verdict", validate_importer_output(f4a, f4src)[0] == CANNOT_EVALUATE)
    f4b = import_github_tasklist(f4src)
    f4b.candidates[0]["draft_ref"] = []
    check("f4-draft-ref-nonstring-verdict", validate_importer_output(f4b, f4src)[0] == FINDING)
    f4c = import_github_tasklist(f4src)
    f4c.candidates[0]["type"] = {}
    check("f4-type-nonstring-verdict", validate_importer_output(f4c, f4src)[0] == CANNOT_EVALUATE)
    f4d = import_github_tasklist(f4src)
    f4d.lossy["span"][0]["class"] = []
    check("f4-span-class-nonstring-verdict", validate_importer_output(f4d, f4src)[0] == CANNOT_EVALUATE)
    # F4 residual: the SECOND public validation entry point, validate_assistant_output, must ALSO refuse a
    # rejected structure with a NAMED verdict rather than crash. candidates=None returns CANNOT_EVALUATE (the
    # gate's verdict), not a TypeError from the wrapper iterating None. Without the fix the wrapper crashed.
    f4e = import_github_tasklist(f4src)
    f4e.candidates = None
    check("f4-assistant-candidates-none-verdict",
          validate_assistant_output(f4e, f4src)[0] == CANNOT_EVALUATE)

    # 29. F5 (forged in-range line_range): a line_range is DERIVED from the span's byte range and BOTH
    # endpoints are compared, so a merely in-range forgery ([2, 2] over a byte range physically on line 1) is
    # a FINDING. Without the fix only the last endpoint's not-exceeding-the-line-count was checked, so an
    # in-range forgery passed.
    f5src = mk("legacy/F5.md", "- [ ] one\n- [x] two\n")                   # two physical lines
    f5 = import_github_tasklist(f5src)
    f5.lossy["span"][0]["line_range"] = [2, 2]                             # the first span physically lies on line 1
    check("f5-forged-line-range-finding", validate_importer_output(f5, f5src)[0] == FINDING)

    # 30. F6 (genuine render_references recognized): a REAL _opf_views.render_references projection (its `-
    # <NS>-<n> <title>` record line AND its two-space-indented `  - <kind>: <locator>` contextual reference
    # rows) reads CLEAN with preserved_verbatim rows, not `ambiguous`. Without the fix the record-grammar
    # tightening regressed the contextual rows to ambiguous (clean False). This is a real render_references
    # discriminator (distinct from the TODO-only genuine-render test above).
    import _opf_views
    ref_src = {"reference": [{"id": "RF-1", "type": "reference", "status": "recorded", "title": "a captured ref",
                              "refs": [{"kind": "path", "locator": "docs/spec.md", "note": "n"},
                                       {"kind": "url", "locator": "https://example.test/x", "note": "n"}]}]}
    genuine_refs = (_opf_views._header({".working/toml/reference.index.toml": b"data"})
                    + "\n" + _opf_views.render_references(ref_src))
    f6 = import_aiqt_face(mk("legacy/F6-refs.md", genuine_refs))
    check("f6-genuine-render-references-recognized",
          f6.verdict == CLEAN and f6.clean is True and tiles(f6.lossy)
          and sum(1 for s in f6.lossy["span"] if s["class"] == "preserved_verbatim") >= 2
          and not any(s["class"] == "ambiguous" for s in f6.lossy["span"]))
    # F6 REGRESSION discriminators: a reference-shaped `  - <kind>: <locator>` row is recognized ONLY inside
    # the REFERENCES view and under its owning record; a prefix-only match regressed both cases to clean.
    #   - ORPHAN: a row under an unrelated view (`# TODO`) with NO owning record is `ambiguous`, matching
    #     parent e9acbc8 (which had no ref-row recognition at all), not `preserved_verbatim`/clean.
    face_orphan = face.replace("- BI-1 (open): do a thing\n", "  - path: user-content\n")
    f6_orphan = import_aiqt_face(mk("legacy/F6-orphan.md", face_orphan))
    check("f6-orphan-ref-row-ambiguous",
          f6_orphan.verdict == CLEAN and f6_orphan.clean is False and tiles(f6_orphan.lossy)
          and any(s["class"] == "ambiguous" for s in f6_orphan.lossy["span"])
          and not any(s["class"] == "preserved_verbatim" for s in f6_orphan.lossy["span"]))
    #   - WRONG VIEW: a row under a real record but OUTSIDE the REFERENCES view (`# TODO`) is `ambiguous`
    #     (the record is still preserved_verbatim; only the mis-placed row drifts).
    face_wrongview = face.replace("- BI-1 (open): do a thing\n",
                                  "- BI-1 (open): do a thing\n  - path: user-content\n")
    f6_wrong = import_aiqt_face(mk("legacy/F6-wrongview.md", face_wrongview))
    check("f6-wrong-view-ref-row-ambiguous",
          f6_wrong.verdict == CLEAN and f6_wrong.clean is False and tiles(f6_wrong.lossy)
          and any(s["class"] == "ambiguous" for s in f6_wrong.lossy["span"])
          and any(s["class"] == "preserved_verbatim" for s in f6_wrong.lossy["span"]))

    # 32. N1 (multi-section faces recognized): a REAL multi-section _opf_views projection (render_decisions
    # emits `## Subsection` groupings at column 0) reads CLEAN, its subsection headings
    # `ignored_by_declared_rule` (structural), not spurious `ambiguous` drift. Without the fix each `## ...`
    # heading was ambiguous and the genuine projection read clean False (a fail-safe misclassification). The
    # subsection-heading recognition must NOT re-admit an orphan reference row (asserted above) or record-shaped
    # drift (asserted by the m2a non-record-bullet discriminator).
    dec_face = (_opf_views._header({".working/toml/pending_decision.index.toml": b"d"})
                + "\n" + _opf_views.render_decisions({"pending_decision": [], "autonomous_decision": []}))
    n1 = import_aiqt_face(mk("legacy/N1-decisions.md", dec_face))
    check("n1-multi-section-render-clean",
          n1.verdict == CLEAN and n1.clean is True and tiles(n1.lossy)
          and any(s["class"] == "ignored_by_declared_rule" and "subsection heading" in s["note"]
                  for s in n1.lossy["span"])
          and not any(s["class"] == "ambiguous" for s in n1.lossy["span"]))

    # 31. F7 (is_clean validates shape + tiling before deriving cleanliness): a span that is not a table
    # (`[None]`), carries no / an unknown class (`[{}]`, `[{"class": "bogus"}]`), a RECOGNIZED class but is
    # otherwise malformed / missing its range + accounting fields (`[{"class": "mapped"}]`), or an EMPTY span
    # list over a NON-empty source, each makes is_clean fail-closed to NON-clean rather than reading clean
    # from a class-membership check alone. Without the residual fix a recognized-class-but-malformed span and
    # an empty-span-over-nonempty-source both read clean (True).
    for label, spans in (("empty-table", [{}]), ("none-entry", [None]), ("unknown-class", [{"class": "bogus"}]),
                         ("recognized-but-malformed", [{"class": "mapped"}]), ("empty-over-nonempty", [])):
        f7 = import_github_tasklist(f4src)                      # f4src is a real, NON-empty source
        f7.lossy["span"] = spans
        check("f7-malformed-span-nonclean-{}".format(label), is_clean(f7) is False)

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
          "kind fail closed. Fix-forward discriminators (codex backstop): cleanliness is DERIVED from the "
          "loss spans so an untrusted clean flag with an unresolved span is rejected (is_clean + gate); the "
          "aiqt-face importer matches the generator's `- <NS>-<n>` record grammar (a non-record bullet is "
          "drift) and closes its header only at the contiguous field run's `-->` so a forged / relocated "
          "terminator cannot swallow content, tied to a REAL _opf_views render; the gate binds the loss "
          "entry against RECOMPUTED-from-bytes size / digest / measured line count; a lossy report rejects a "
          "duplicated source path; a 'mapped' span requires a unique non-empty candidate reference and "
          "candidate <-> span references match multiplicity-aware; an unrecognized changelog heading "
          "invalidates the date/category context so a following entry cannot inherit a fabricated mapping; a "
          "nested (indented) task item is ambiguous, not flattened; and a malformed proposal shape yields a "
          "verdict via the normalized proposals, never a KeyError. Codex-confirmed residual discriminators: "
          "a malformed generated-face header (an embedded / injected / sentinel-only unterminated header) is "
          "NON-clean; a changelog heading boundary (indented / bare / H1) resets the date/category context "
          "so a following entry cannot inherit a fabricated mapping; the producer and report-validator both "
          "enforce the canonical-path predicate so a path alias cannot bypass source uniqueness; a malformed "
          "untrusted field (candidates / draft_ref / type / span class) returns a named refusing verdict, "
          "never a TypeError; a forged in-range line_range is derived from the byte range at both endpoints "
          "and rejected; a genuine render_references projection (record line plus contextual `  - <kind>: "
          "<locator>` rows) is preserved_verbatim / clean; and is_clean fail-closes a malformed loss span "
          "(no or unknown class) to NON-clean.")
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
