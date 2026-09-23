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
import _opf_views      # noqa: E402  the renderer whose faces this layer recognizes: single-source the mirror
                       # column vocabulary, the empty marker, and the mirror-title -> type resolver (no cycle:
                       # _opf_views imports none of the importer/import modules)


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
# The VALUE requirement is at least one NON-whitespace character ANYWHERE after the single separator
# space (`.*\S`): the locator is the verbatim schema-valid value (_opf_schema._validate_refs requires a
# non-BLANK string, at least one character `.strip()` keeps), which may BEGIN with whitespace, and
# render_references emits it verbatim after the `: ` separator, so anchoring `\S` immediately after the
# separator read a GENUINE leading-space-locator row as false drift; but a schema-valid locator is never
# ALL whitespace, so the round-3 bare `.` also matched a whitespace-only value and read `  - doc:  ` as a
# genuine row (a false clean). `.*\S` accepts every schema-valid locator (leading space included) and
# rejects a whitespace-only / empty value to `ambiguous` (drift). The kind alternation, the 2-space
# indent, and the REFERENCES-view / owning-record context gating stay unchanged.
_FACE_REF_ROW_RE = re.compile(r"^  - (?:{}): .*\S".format("|".join(re.escape(k) for k in _opf_schema.REF_KINDS)))
# The view TITLE `_opf_views.render_references` emits (`_lines("REFERENCES", ...)`). A contextual
# reference row is generated ONLY inside this view, subordinate to a reference record line, so a row is
# recognized as generator output by its VIEW-and-record context, never by its `  - <kind>:` line prefix
# alone (a prefix-only match re-admits an orphan row under an unrelated view / section as clean).
_FACE_REFERENCES_TITLE = "REFERENCES"
# A generator-emitted SUBSECTION heading in the face body is EXACTLY H2: the multi-section views
# (render_decisions / render_pipeline / render_handoff / render_findings / render_contributions /
# render_version_md), render_mirror's `## <id>` record headings, and render_version_md's subsections all
# emit `## <text>` (two hashes, one space) at column 0, and NO renderer emits an H3..H6 heading. Like the
# H1 title a recognized H2 is structural and carries no importable record (records are the `- <NS>-<n>`
# bullets), so it is `ignored_by_declared_rule`, not drift; a multi-section render is therefore recognized
# clean rather than flagged as spurious drift. Recognition requires EXACTLY two hashes plus a space
# (`### x` has `#` where the space must be, so it does not match): a deeper heading is NOT generator
# output, so it establishes no mirror ownership, no VERSION subsection, and no structural subheading, and
# falls through to `ambiguous` drift (the previous `^#{2,6}` match let an H3..H6 heading, its level
# discarded by the `lstrip("#")`, forge mirror / VERSION ownership).
_FACE_H2_RE = re.compile(r"^## \S")
# A generated HTML comment is closed ONLY by a `--`-led terminator: the standard `-->` or the abrupt
# `--!>`. `_opf_views._html_comment_safe` neutralizes BOTH in every interpolated value, so neither can
# appear in a well-formed generated header (its opening sentinel line or any field line). A terminator on
# the OPENING sentinel line is an already-closed comment (later field-shaped content is body, not header),
# and the `--!>` alternate on any header line is a premature / forged close: either leaves header_end None
# (the comment is malformed, so NO line is trusted as header and the sentinel and every following line is
# `ambiguous` drift), so an early-closed / abruptly-closed header can no longer read as all-header/clean.
_FACE_COMMENT_TERMINATORS = ("-->", "--!>")
# The empty-view marker, single-sourced from the renderer (was a local literal). Every view family renders
# it identically, so it is `ignored_by_declared_rule` in all three grammars below.
_FACE_EMPTY = _opf_views._EMPTY

# --- view-family grammars (spec 10.1/6.1) --------------------------------------------------------------
# Beyond the record-list views (the `- <NS>-<n> ...` bullets recognized above), _opf_views renders two
# OTHER face shapes the importer must recognize so an EXACT generated mirror or VERSION.md face reads clean
# rather than as drift: the 1:1 <TYPE>-INDEX.md mirror (render_mirror) and VERSION.md (render_version_md).
# The face's grammar is gated on the family the FIRST structural H1 title selects, generalizing the
# view-context mechanism the REFERENCES sub-state already uses. An unknown H1 title falls back to the tight
# record-list family (never a permissive read).
_FACE_VERSION_TITLE = "VERSION"
# A mirror H1 title is `<TYPE> index` (render_mirror: `"{} index".format(type_name.upper())`). The token is
# resolved through _opf_views._mirror_type (reusing the authoritative _LEDGER_SOURCES exclusion AND the
# BASELINE_SPECS membership test), never a re-encoded type list, so the importer's mirror set cannot drift
# from the renderer's (guard-input-soundness).
_FACE_MIRROR_TITLE_RE = re.compile(r"^(?P<tok>[A-Z0-9_]+) index$")
# A mirror COLUMN row: `- <col>: <val>` where <col> is one of render_mirror's declared columns
# (MIRROR_COLUMNS, single-sourced from _opf_views) plus the `actor` pseudo-column render_mirror also emits.
# The value is OPTIONAL: the write path strips per-line trailing whitespace (_opf_views.py str.rstrip), so
# an empty-valued column is `- <col>:` on disk (and `- <col>: ` before that normalization); BOTH are
# accepted, and nothing shorter. Anchoring `:` after the closed key alternation keeps a hand-added
# `- word: something` bullet (an unknown key) out (no unrestricted-bullet acceptance).
_FACE_MIRROR_COLS = _opf_views.MIRROR_COLUMNS + ("actor",)
_FACE_MIRROR_COL_RE = re.compile(
    r"^- (?:{}):(?: .*)?$".format("|".join(re.escape(c) for c in _FACE_MIRROR_COLS)))
# VERSION.md subsection titles (render_version_md) and its two anchored row shapes. A release row is
# `- <ver> (<date>) worklog <span>`; a summary row is `- <covers> (<status>)`. Each is recognized ONLY under
# its owning subsection (shaped, not context-only, recognition), so a row of the wrong shape, or under the
# wrong subsection, is drift. The two shapes are NOT disjoint: an EMPTY-SPAN release row renders as
# `- <ver> (<date>) worklog (none)`, which ends in a parenthesized token and so ALSO matches the summary
# shape; the summaries branch therefore EXCLUDES a release-shaped row (a genuine summary row carries no
# ` worklog <span>` tail; one whose own text completes the release shape is fail-closed to drift, a
# residual disclosed below).
_FACE_VERSION_RELEASES = "Releases"
_FACE_VERSION_SUMMARIES = "Changelog summaries"
_FACE_VERSION_RELEASE_RE = re.compile(r"^- .+ \(.+\) worklog .+$")
_FACE_VERSION_SUMMARY_RE = re.compile(r"^- .+ \(.+\)$")


def _face_family(title):
    """The view family a face's H1 title selects (its leading `#` already stripped and the text stripped):
    `version`, `mirror`, or the default `record` list family. The mirror resolution reuses
    _opf_views._mirror_type so the ledger-source exclusion and BASELINE_SPECS membership are never
    re-encoded here; an unknown title resolves to the tight record-list grammar, never a permissive read."""
    if title == _FACE_VERSION_TITLE:
        return "version"
    m = _FACE_MIRROR_TITLE_RE.match(title)
    if m and _opf_views._mirror_type("{}-INDEX.md".format(m.group("tok"))) is not None:
        return "mirror"
    return "record"

_FACE_RESIDUAL = ("aiqt-face recognizes an EXACT OPF/AIQT-generated markdown face by its generated "
                  "sentinel (`<!-- GENERATED by ... opf/tools/_opf_views.py ... -->`). An exact face is a "
                  "DERIVED projection the store REGENERATES (generated-artefact-source-only), so it carries "
                  "nothing NEW to import: the header and blank lines are `ignored_by_declared_rule`, and the "
                  "body is read under a grammar GATED on the view FAMILY the FIRST structural H1 title "
                  "selects; BEFORE that first H1 no family grammar is live, so any non-blank body line "
                  "ahead of the title is `ambiguous` drift (a genuine face opens with its single H1). "
                  "Three closed families: (1) RECORD-LIST (the default, and the family for any "
                  "unknown H1 title): the H1 view title and a generator-emitted exactly-H2 `## ...`"
                  "subsection heading are `ignored_by_declared_rule`; a `- <NS>-<n> ...` record line "
                  "(_opf_views' render shape) is `preserved_verbatim`; a two-space-indented `  - <kind>: "
                  "<locator>` contextual reference row is `preserved_verbatim` ONLY inside the REFERENCES "
                  "view and under its owning reference record (render_references, `<kind>` from the closed "
                  "spec-8.6 vocabulary path / url / doc), not by its prefix alone. (2) MIRROR (H1 `# <TYPE> "
                  "index`, where <TYPE> resolves through _opf_views._mirror_type): a `## <id>` heading whose "
                  "id passes the schema id shape is `ignored_by_declared_rule` and OWNS the run of "
                  "`- <col>: <val>` column rows beneath it, whose <col> is in render_mirror's closed column "
                  "vocabulary (MIRROR_COLUMNS + `actor`); ownership resets on a blank line, a heading, or the "
                  "empty marker. (3) VERSION (H1 `# VERSION`): the subsections `## Releases` and "
                  "`## Changelog summaries` are `ignored_by_declared_rule` and each OWNS its shaped rows (a "
                  "`- <ver> (<date>) worklog <span>` release row under Releases, a `- <covers> (<status>)` "
                  "summary row under Summaries; a summary row is recognized ONLY when it is not ALSO "
                  "release-shaped, because an EMPTY-SPAN release row `- <ver> (<date>) worklog (none)` ends "
                  "in a parenthesized token and would otherwise read as a summary). The empty-view marker "
                  "is `ignored_by_declared_rule` in every "
                  "family. An off-grammar readable body line, one that matches NO shape the live family's "
                  "grammar declares, is `ambiguous` and routes to review (clean == False, so it cannot "
                  "auto-promote); recognition is by SHAPE, never byte-exactness, so this catches shape "
                  "drift, not byte drift (the ACCEPTED byte-mimic residual below). Shape drift caught this "
                  "way: a bullet that is NOT the family's declared "
                  "shape, an orphan or unknown-key mirror column row (or one whose owning `## <id>` did not "
                  "precede it, or was separated by a blank), a non-id `## ` heading in a mirror, a VERSION "
                  "row of the wrong shape or outside its subsection, an alien VERSION subsection, a "
                  "reference-row-shaped bullet outside the REFERENCES view or not under a reference record, "
                  "an H3..H6 heading in ANY family (H1 and exactly-H2 are the only generator heading "
                  "levels, so a deeper heading establishes no ownership, subsection, or structural "
                  "subheading), a release-shaped row under the Summaries subsection, and a SECOND H1 "
                  "anywhere in the body (the generator emits exactly one view title, so a "
                  "later H1 is a hand edit). The header is a CONTIGUOUS run of the sentinel line plus the "
                  "generated field lines `sources:` / `schema:` / `source-set-digest:` / `regenerate:` in "
                  "THAT FIXED ORDER, each exactly ONCE, terminated by a line that is exactly `-->` at "
                  "column 0 with no leading or trailing whitespace (an indented or padded close is NOT a "
                  "close): a forged "
                  "or relocated `-->`, a terminator embedded on the opening sentinel line, an embedded `-->` "
                  "or abrupt `--!>` on a field line, or an out-of-order / repeated / injected field line "
                  "cannot swallow real content into the header; if the header does not close cleanly at its "
                  "ordered field run (including a sentinel-only source with no `-->`), the comment is "
                  "malformed, so NO line is trusted as header and the opening sentinel and every following "
                  "line is `ambiguous` drift (an unterminated / sentinel-only header is NON-clean). "
                  "A face whose header closes cleanly but that carries NO body line beyond at most its "
                  "single H1 view title (header-only, or a bare title with nothing after it) is TRUNCATED "
                  "generated output and reads NON-clean: every renderer emits at least one non-blank body "
                  "line after its H1 (the empty-view marker or a subsection heading). "
                  "DISCLOSED RESIDUALS. The ACCEPTED byte-mimic class (maintainer-accepted: within a "
                  "genuine generated header the importer recognizes a face by SHAPE, so a hand-authored "
                  "file that byte-mimics a generated face reads clean; byte-EXACTNESS against the store is "
                  "the render DRIFT GATE's job, not the importer's, the tolerance is bounded to shape-valid "
                  "content the importer preserves verbatim downstream, and the safe failure direction on a "
                  "non-mimicking input is false-DRIFT to review, never a silent import): (a) STRUCTURAL "
                  "lines are matched by shape, the heading TEXT compared after `#`-strip plus whitespace "
                  "normalization (the H1 prefix is `#` plus any whitespace run, the H2 prefix exactly "
                  "`## `), so a whitespace-variant face reads clean: `#  TITLE`, a `#`-tab title, trailing "
                  "spaces on a heading or on most rows, or a CRLF-converted face. (b) Structural "
                  "MULTIPLICITY and ORDER inside a genuine face are NOT enforced: a duplicated or "
                  "reordered subsection or `## <id>` heading, a repeated column or record row, or an "
                  "appended shape-valid row reads clean; the drift gate catches the byte drift against the "
                  "store. (c) Content drift INSIDE a well-shaped bullet (an edited title, version, column "
                  "value, or renumbered id that stays shape-valid) reads `preserved_verbatim` (the face "
                  "importer never reconstructs records). FAIL-SAFE residuals (a false DRIFT routed to "
                  "review, never a false clean): a degenerate reference locator that is schema-valid yet "
                  "ESCAPES to a whitespace-only rendering (an all-control-character locator; the markdown "
                  "sink drops C0/DEL controls) renders a value-less row that reads `ambiguous`; by the same "
                  "escape, a GENUINE record whose TITLE (or other free-text projected field) is control-only "
                  "collapses to a bare `- <id>` row (the escape leaves only the trailing separator space, "
                  "which the write path's per-line trailing-whitespace normalization strips), off the record "
                  "shape, so it reads `ambiguous` and routes to review, never a false clean; the store's own "
                  "record validation and the render drift gate own record completeness for both degenerate "
                  "escapes; a GENUINE "
                  "summary row whose own text carries a ` worklog ` tail that completes the release shape "
                  "is fail-closed to `ambiguous` under Summaries; and a trailing-whitespace variant of the "
                  "end-anchored summary row shape likewise reads `ambiguous`. Record-list exactly-H2 `## ` "
                  "subheadings remain generically ignored (per-view "
                  "subsection vocabularies are not enumerated); a non-UTF-8 source stays CANNOT-EVALUATE "
                  "(inherited). Faithful RECONSTRUCTION of records from a rendered face (the inline escape is "
                  "one-way for a markdown sink) is deferred to the assistant-guided path or an operator KEEP "
                  "of the original (spec 14.2).")


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
            # The clean close is EXACTLY `-->` with no leading or trailing whitespace (_text has already
            # stripped only the trailing newline / CR; the renderer emits the close at column 0 unpadded).
            # An indented `  -->` is NOT a close: it falls to the embedded-terminator break below, so the
            # header stays malformed (every line ambiguous) rather than closing at a hand-moved terminator.
            if t == "-->":
                if fi == n_fields:              # the terminator closes the header ONLY after the full field run
                    header_end = idx + 1
                break
            if any(term in t for term in _FACE_COMMENT_TERMINATORS):
                break                           # an embedded / abrupt (`-->` / `--!>`) terminator: early-closed
            if fi < n_fields and t.startswith(_FACE_HEADER_FIELD_PREFIXES[fi]):
                fi += 1                         # the next field, IN ORDER: advance the run
                continue
            break                               # out-of-order / repeated / missing / non-field line: not clean
    family = None          # the view family the FIRST structural H1 selects: record-list / mirror / version.
    in_refs_view = False   # record family: True under the REFERENCES view title, whose records own the
                           # `  - <kind>: <locator>` contextual reference rows.
    ref_owner = False      # record family: True immediately under a reference record line (or its run of ref
                           # rows), so a contextual reference row is recognized ONLY where render_references
                           # emits one; an orphan row under an unrelated section cannot read as generator output.
    id_owner = False       # mirror family: True immediately under a `## <id>` mirror record heading, so a
                           # `- <col>: <val>` column row is recognized ONLY where render_mirror emits one.
    version_section = None # version family: the owning VERSION.md subsection (`releases` / `summaries`), or None.
    body_seen = False      # True once any non-blank body line OTHER than the first structural H1 (the view
                           # title) is seen past the header: every renderer emits at least one such line (the
                           # empty-view marker or a subsection heading; the empty-source roster self-test pins
                           # it), so a face still False here at finalization is header-only or title-only,
                           # truncated generated output, made non-clean below.
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
            # A blank line RESETS per-record ownership in every family (a mirror record's column run and a
            # REFERENCES record's ref-row run both terminate at the blank render_mirror / render_references
            # emit between records). The VERSION subsection persists (no genuine row follows a blank).
            ref_owner = False
            id_owner = False
            cells.append(_cell(start, end, line_no, "ignored_by_declared_rule", note="blank line"))
            continue
        if _KAC_H1_RE.match(line):
            title = line.lstrip("#").strip()
            if family is None:
                # The FIRST structural H1 selects the view family. REFERENCES is a record-list view whose
                # sub-state (in_refs_view) still governs its contextual ref rows.
                family = _face_family(title)
                in_refs_view = (family == "record" and title == _FACE_REFERENCES_TITLE)
                ref_owner = False
                id_owner = False
                version_section = None
                cells.append(_cell(start, end, line_no, "ignored_by_declared_rule",
                                   note="view title (structural)"))
            else:
                # A SECOND H1 in the body: every generator view emits exactly ONE view title, so a later H1
                # is a hand edit (drift). It resets any pending per-record ownership.
                ref_owner = False
                id_owner = False
                body_seen = True
                cells.append(_cell(start, end, line_no, "ambiguous",
                                   note="a second H1 in the face body (the generator emits exactly one view "
                                        "title; possible hand edit)"))
            continue
        # Every non-blank body line past the header, other than the FIRST structural H1 (the view title),
        # marks the face as carrying a body; a face that never reaches here or the second-H1 branch above
        # is header-only or title-only, truncated generated output, made non-clean at finalization.
        body_seen = True
        if family is None:
            # No structural H1 has selected a view family yet. A genuine generated face opens with its
            # single H1 view title as its FIRST content line (the header comment is handled above and a
            # blank line stays ignored), so ANY non-blank body line here is drift: no family-specific
            # grammar (a record bullet, a contextual ref row, a mirror column, a VERSION row, an H2
            # subheading, the empty marker) is accepted before a structural title has selected the family.
            # Pre-fix the record-list fall-through below recognized a `- <NS>-<n>` bullet here, so content
            # prepended ahead of the H1 bypassed the family gate and read preserved_verbatim / clean.
            cells.append(_cell(start, end, line_no, "ambiguous",
                               note="face body content before the view title (a generated face opens with "
                                    "its single H1; possible hand edit)"))
            continue
        if family == "mirror":
            if _FACE_H2_RE.match(line):
                heading = line.lstrip("#").strip()
                if _opf_schema._ID_RE.match(heading):
                    # A `## <id>` mirror record heading OWNS the column rows rendered immediately beneath it.
                    id_owner = True
                    cells.append(_cell(start, end, line_no, "ignored_by_declared_rule",
                                       note="mirror record heading (structural)"))
                else:
                    id_owner = False
                    cells.append(_cell(start, end, line_no, "ambiguous",
                                       note="mirror `## ` heading is not a record id (possible hand edit)"))
            elif line == _FACE_EMPTY:
                id_owner = False
                cells.append(_cell(start, end, line_no, "ignored_by_declared_rule", note="empty-view marker"))
            elif _FACE_MIRROR_COL_RE.match(line) and id_owner:
                # A `- <col>: <val>` column row of render_mirror's closed vocabulary, under its owning id.
                cells.append(_cell(start, end, line_no, "preserved_verbatim",
                                   note="derived mirror column row (render_mirror; not re-imported)"))
            else:
                # An orphan column row (no owning `## <id>`), an unknown-key bullet, or any other drift.
                id_owner = False
                cells.append(_cell(start, end, line_no, "ambiguous",
                                   note="mirror body drifted from the exact generated grammar (an orphan or "
                                        "unknown-column bullet, or possible hand edit)"))
            continue
        if family == "version":
            if _FACE_H2_RE.match(line):
                heading = line.lstrip("#").strip()
                if heading == _FACE_VERSION_RELEASES:
                    version_section = "releases"
                    cells.append(_cell(start, end, line_no, "ignored_by_declared_rule",
                                       note="VERSION subsection heading (structural)"))
                elif heading == _FACE_VERSION_SUMMARIES:
                    version_section = "summaries"
                    cells.append(_cell(start, end, line_no, "ignored_by_declared_rule",
                                       note="VERSION subsection heading (structural)"))
                else:
                    version_section = None
                    cells.append(_cell(start, end, line_no, "ambiguous",
                                       note="VERSION `## ` heading is not a declared subsection (possible "
                                            "hand edit)"))
            elif line == _FACE_EMPTY:
                cells.append(_cell(start, end, line_no, "ignored_by_declared_rule", note="empty-view marker"))
            elif version_section == "releases" and _FACE_VERSION_RELEASE_RE.match(line):
                cells.append(_cell(start, end, line_no, "preserved_verbatim",
                                   note="derived VERSION release row (render_version_md; not re-imported)"))
            # A summary row is recognized ONLY when it is not ALSO release-shaped: the summary shape is a
            # sub-shape of an empty-span release row (`- <ver> (<date>) worklog (none)` ends in a
            # parenthesized token), so a release row moved under Summaries falls to the else below (drift).
            elif (version_section == "summaries" and _FACE_VERSION_SUMMARY_RE.match(line)
                  and not _FACE_VERSION_RELEASE_RE.match(line)):
                cells.append(_cell(start, end, line_no, "preserved_verbatim",
                                   note="derived VERSION summary row (render_version_md; not re-imported)"))
            else:
                # A row of the wrong shape, a row outside any subsection, or a row under the wrong subsection.
                cells.append(_cell(start, end, line_no, "ambiguous",
                                   note="VERSION body drifted from the exact generated grammar (a row of the "
                                        "wrong shape or outside its subsection; possible hand edit)"))
            continue
        # The record-list family (family == "record"; an unknown H1 title also selects it): byte-identical
        # to the pre-existing grammar. A pre-H1 line (family is None) no longer reaches here: the family
        # gate above routes it to `ambiguous` before any family grammar is live.
        if _FACE_H2_RE.match(line):
            # A generator-emitted exactly-H2 subsection heading (the multi-section views' groupings; no
            # renderer emits a deeper level) is structural and carries no importable record, mirroring the
            # H1-title branch. An H3..H6 heading is not generator output and falls to `ambiguous` below.
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
    if header_end is not None and not body_seen:
        # A face whose header closed cleanly but that carries NO body line beyond at most its single H1
        # view title (a header-only face, family never selected; or a bare title with nothing after it)
        # is TRUNCATED generated output, never clean: every renderer emits at least one non-blank body
        # line after its H1 (the empty-view marker or a subsection heading; the empty-source roster
        # self-test pins that for every NAMED_VIEWS face, every mirror, and VERSION.md). Mirroring the
        # malformed-header path, NO line of such a face is trusted: every cell reclassifies to `ambiguous`
        # drift, so the face derives clean False and routes to review. (A malformed header, header_end
        # None, is already all-ambiguous above and needs nothing here.)
        for c in cells:
            c["class"] = "ambiguous"
            c["note"] = ("generated face carries no view body (header-only or title-only truncated face; "
                         "a genuine face renders at least its empty-view marker)")
    # An exact face carries no candidate: it is a projection, not adopter content to import.
    return _finalize(source, "aiqt-face", cells, [], _FACE_RESIDUAL)


IMPORTER_KINDS = tuple(sorted(_kinds()))   # the closed, public deterministic-importer-kind vocabulary
                                           # (MIG-PR3 --ingest-options validates importer_kind against this);
                                           # derived from _kinds() (the single dispatch source, no drift), and
                                           # placed after the importer defs so _kinds() resolves at load time.


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
    updated_at, are injected for VALIDATION ONLY and only where the key is ABSENT (the counter mints the id
    and the apply clock stamps updated_at at promotion, MIG-PR5), so the draft is proven schema-valid modulo
    the apply-time mint. A PRESENT id is refused (INVALID) and a PRESENT updated_at is validated as given.
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
    # ABSENT is distinguished from PRESENT-but-falsy (round-4 F1): a placeholder is synthesized ONLY for a
    # key the draft does not carry. A draft is id-less by contract (the counter mints at apply), so a PRESENT
    # `id` of any value (a valid-looking id, or False / 0 / "" / [] / {}) is refused rather than silently
    # replaced by the synthetic one; a PRESENT `updated_at` is never overwritten, so a falsy or malformed
    # value reaches the real record validator and is judged there.
    if "id" in rec:
        return _opf_schema.RecordValidation(_opf_store.INVALID, [
            "a candidate draft carries an `id` ({!r}); a draft is id-less, the counter mints the id at "
            "apply".format(rec["id"])])
    rec = copy.deepcopy(rec)
    rec["id"] = "{}-1".format(ns)
    if ctype != _WORKLOG_TYPE and "updated_at" not in rec:
        rec["updated_at"] = _SYNTH_STAMP
    return validate_record(rec, expected_type=ctype)


def candidate_ref_findings(refs):
    """The candidate draft-reference invariants over ONE importer output's candidate draft_refs (in order):
    every reference is a string, non-empty, and unique within that output. Returns a findings list (empty
    == valid). The SINGLE authority for what a legal draft reference is, shared by validate_importer_output
    (plan time) and the MIG-PR4b review gate over the frozen candidates_draft.toml (grouped per migrate
    source), so the two cannot drift. Type-validated BEFORE any set construction: a non-string (e.g. a list)
    reference is unhashable, so the multiplicity check is skipped once the set is known malformed."""
    if not all(isinstance(r, str) for r in refs):
        return ["a candidate draft_ref is not a string (a draft_ref must be a string)"]
    findings = []
    if any(r == "" for r in refs):
        findings.append("a candidate draft_ref is empty (a draft_ref must be a non-empty string)")
    if len(refs) != len(set(refs)):
        findings.append("a candidate draft-ref is shared by more than one candidate (duplicate candidate)")
    return findings


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
        # The candidate-reference invariants (string, non-empty, unique within this importer output) come from
        # the SHARED candidate_ref_findings authority the MIG-PR4b review gate also applies to the frozen
        # drafts, so the plan-time gate and the review gate cannot drift on what a legal draft reference is.
        findings.extend(candidate_ref_findings(cand_ref_list))
        if all(isinstance(r, str) for r in cand_ref_list):
            if len(span_ref_list) != len(set(span_ref_list)):
                findings.append("a candidate record reference is shared by more than one mapped span "
                                "(duplicate mapping)")
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
        # Key PRESENCE, never truthiness (round-4 F1 sibling): a falsy `id` (False, 0, "") is still an id.
        if isinstance(cand, dict) and isinstance(cand.get("record"), dict) and "id" in cand["record"]:
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
    # 14b. round-4 F1 (ABSENT vs PRESENT-FALSY): the placeholder id / updated_at is synthesized ONLY for an
    # ABSENT key. A PRESENT falsy id (False, 0, "", [], {}) is refused by the shared _validate_candidate
    # (INVALID), and so by the validator gate and the assistant contract; a PRESENT falsy updated_at reaches
    # the real record validator unreplaced. The pre-fix `not rec.get(...)` synthesized over each and passed.
    fsrc = mk("legacy/falsy.md", "- [ ] falsy item\n")
    base_cand = import_github_tasklist(fsrc).candidates[0]
    check("r4f1-absent-id-still-synthesized",
          _validate_candidate(base_cand, _opf_schema.validate_record).status == _opf_store.VALID)
    for tag, field, val in (("id-false", "id", False), ("id-zero", "id", 0), ("id-empty-str", "id", ""),
                            ("id-empty-list", "id", []), ("id-empty-table", "id", {}),
                            ("updated-at-false", "updated_at", False), ("updated-at-empty-str", "updated_at", "")):
        fc = copy.deepcopy(base_cand)
        fc["record"][field] = val
        check("pr4b-disc-r4f1-validator-" + tag,
              _validate_candidate(fc, _opf_schema.validate_record).status == _opf_store.INVALID)
        fres = import_github_tasklist(fsrc)
        fres.candidates[0]["record"][field] = val
        check("pr4b-disc-r4f1-gate-" + tag, validate_importer_output(fres, fsrc)[0] == FINDING)
    fres = import_github_tasklist(fsrc)
    fres.candidates[0]["record"]["id"] = 0
    check("pr4b-disc-r4f1-assistant-falsy-id", validate_assistant_output(fres, fsrc)[0] == FINDING
          and any("permanent id" in f for f in validate_assistant_output(fres, fsrc)[1]))

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
    # LEADING-SPACE LOCATOR (round 3, codex MED-2): a schema-valid locator may BEGIN with whitespace
    # (_opf_schema._validate_refs requires only a non-BLANK string) and render_references emits it
    # verbatim (`  - doc:  OPF-SPEC.md 8`), so the genuine row must read preserved_verbatim / clean.
    # Pre-fix the ref-row regex demanded a NON-SPACE right after the separator, so this genuine
    # generator output read as false drift (ambiguous, clean False).
    ref_sp = {"reference": [{"id": "RF-2", "type": "reference", "status": "recorded", "title": "a ref",
                             "refs": [{"kind": "doc", "locator": " OPF-SPEC.md 8", "note": "n"}]}]}
    genuine_sp = (_opf_views._header({".working/toml/reference.index.toml": b"data"})
                  + "\n" + _opf_views.render_references(ref_sp))
    f6_sp = import_aiqt_face(mk("legacy/F6-lead-space.md", genuine_sp))
    check("f6-leading-space-locator-clean",
          f6_sp.verdict == CLEAN and f6_sp.clean is True and tiles(f6_sp.lossy)
          and any(s["class"] == "preserved_verbatim" and "reference row" in s["note"]
                  for s in f6_sp.lossy["span"])
          and not any(s["class"] == "ambiguous" for s in f6_sp.lossy["span"]))
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

    # --- OPF-MIG-PR2-FU: view-family grammar completeness + malformed-input hardening -------------------
    # The face importer must recognize an EXACT generated face of EVERY deliverable view, not only the
    # record-list views. The roster is enumerated from the AUTHORITATIVE indexes (_opf_views.NAMED_VIEWS
    # and _opf_schema.BASELINE_SPECS minus the ledger sources) through the REAL renderers, never a hand
    # list (guard-input-soundness, completeness-claim-enumerates-its-set, change-carries-check). COVERAGE,
    # stated precisely (disclose-guard-residuals): ROSTER 1 exercises every markdown view's STRUCTURAL
    # grammar (H1 title, subsection headings, empty marker) over EMPTY sources only, so it catches a new or
    # renamed view whose structure the importer does not recognize, but NOT a row shape a renderer emits
    # only when POPULATED. Populated row shapes are pinned separately: for every mirror (ROSTER 2), for
    # VERSION.md (a release + a summary row below), and for the record-list family (the populated TODO /
    # DONE faces in the round-2 block below, plus the render_references and render_decisions fixtures
    # above). A composed view whose populated rendering is not synthesized here (BACKLOG / PIPELINE /
    # FINDINGS / CONTRIBUTIONS / BLOCKS / HANDOFF / WORKLOG) is covered only insofar as it emits the shared
    # `- <NS>-<n>` record grammar those fixtures pin; a renderer change that emits a NEW populated-only row
    # shape for such a view must land with its own populated fixture here. Each clean-face check below
    # FAILS on pre-fix code (a mirror column row / a VERSION row / a second H1 read `ambiguous` today).
    import _opf_views as _v

    def _face(body):
        """Wrap a rendered view body in a REAL generated header, exactly as the write path does
        (_header + a blank line + the body), so the sentinel routes it to import_aiqt_face."""
        return _v._header({".working/toml/x.index.toml": b"data"}) + "\n" + body

    def _norm(text):
        """The write path's per-line trailing-whitespace normalization (_opf_views.py str.rstrip), so the
        test also covers the ON-DISK shape (an empty-valued mirror column is `- <col>:`), not only the
        pre-normalization render."""
        return "\n".join(l.rstrip() for l in text.split("\n"))

    def _clean_face(text):
        r = import_aiqt_face(mk("legacy/rf.md", text))
        return (r.verdict == CLEAN and r.clean is True and r.candidates == [] and r.proposals == []
                and tiles(r.lossy) and any(s["class"] == "preserved_verbatim" for s in r.lossy["span"])
                and not any(s["class"] == "ambiguous" for s in r.lossy["span"]))

    def _ambiguous(text):
        r = import_aiqt_face(mk("legacy/ra.md", text))
        return (r.verdict == CLEAN and r.clean is False and tiles(r.lossy)
                and any(s["class"] == "ambiguous" for s in r.lossy["span"]))

    def _empty_src(sources):
        return {s: ({"releases": [], "summaries": []} if s == "version" else []) for s in sources}

    # ROSTER 1: every markdown FACE view in NAMED_VIEWS renders clean (empty sources exercise every view's
    # structural grammar: H1 title, subsection headings, empty marker). The two NON-face roster entries (the
    # DECISIONS.toml projection and the raw VERSION deliverable) are exempted by their kind / identity read
    # from NAMED_VIEWS itself, never a hand list, and asserted to DECLINE (no generated markdown sentinel).
    _roster_faces = 0
    for _view, (_kind, _sources, _renderer) in _v.NAMED_VIEWS.items():
        if _kind == "projection" or _view == "VERSION":
            if _kind == "projection":
                _txt = _v._toml_header({".working/toml/x.index.toml": b"d"}) + "\n" + _renderer(_empty_src(_sources))
            else:
                _txt = _v.render_version_file({"version": {"releases": [
                    {"version": "1.0.5", "date": "2026-01-01", "worklog_span": ["WL-1", "WL-9"]}],
                    "summaries": []}})
            _rr = import_aiqt_face(mk("legacy/roster-{}".format(_view), _txt))
            check("roster-declines-{}".format(_view), _rr.verdict == FINDING and _rr.lossy == {})
            continue
        _roster_faces += 1
        _rv = import_aiqt_face(mk("legacy/roster-{}".format(_view), _face(_renderer(_empty_src(_sources)))))
        check("roster-face-clean-{}".format(_view),
              _rv.verdict == CLEAN and _rv.clean is True and tiles(_rv.lossy)
              and not any(s["class"] == "ambiguous" for s in _rv.lossy["span"]))
    check("roster-covers-every-markdown-view", _roster_faces == sum(
        1 for _n, (_k, _s, _r) in _v.NAMED_VIEWS.items() if _k != "projection" and _n != "VERSION"))

    # ROSTER 2: every MIRRORABLE baseline type's <TYPE>-INDEX.md mirror (render_mirror) reads clean, with a
    # preserved_verbatim column row. The type roster is BASELINE_SPECS minus the ledger sources, the SAME
    # authoritative exclusion _mirror_type applies, so the importer's mirror set cannot drift from the
    # renderer's. Fails pre-fix (Gap A: mirror column rows read `ambiguous`).
    _mirror_rec = {"id": "AA-1", "type": "x", "status": "open", "title": "a mirror title",
                   "created_at": _SYNTH_STAMP, "kind": "",           # an EMPTY-valued column (on-disk `- kind:`)
                   "scopes": ["BI-1", "BI-2"],                       # a list-valued column
                   "delivery": {"channel": "peer", "ref": "r-1"},    # a table-valued column
                   "actor": {"kind": "importer"}}
    _mirror_types = [t for t in _opf_schema.BASELINE_SPECS
                     if _v._mirror_type("{}-INDEX.md".format(t.upper())) is not None]
    check("mirror-roster-nonempty", len(_mirror_types) >= 1)
    for _t in _mirror_types:
        _mface = _face(_v.render_mirror(_t, [dict(_mirror_rec, type=_t)]))
        check("mirror-face-clean-{}".format(_t), _clean_face(_mface))
        check("mirror-face-clean-normalized-{}".format(_t), _clean_face(_norm(_mface)))  # on-disk shape too

    # VERSION.md (render_version_md): a non-empty ledger (a release row + a summary row) and the empty ledger
    # both read clean. VERSION.md is `deterministic` in NAMED_VIEWS and gets the HTML header, so it routes to
    # the face importer; before this change every VERSION row read `ambiguous` (Gap B).
    _ver_full = _v.render_version_md({"version": {
        "releases": [{"version": "1.0.5", "date": "2026-01-01", "worklog_span": ["WL-1", "WL-9"]}],
        "summaries": [{"covers": "1.0.5", "status": "released"}]}})
    check("version-md-clean", _clean_face(_face(_ver_full)))
    _rve = import_aiqt_face(mk("legacy/version-md-empty.md",
                               _face(_v.render_version_md({"version": {"releases": [], "summaries": []}}))))
    check("version-md-empty-clean",
          _rve.verdict == CLEAN and _rve.clean is True and tiles(_rve.lossy)
          and not any(s["class"] == "ambiguous" for s in _rve.lossy["span"]))
    _rme = import_aiqt_face(mk("legacy/mirror-empty.md", _face(_v.render_mirror("backlog_item", []))))
    check("mirror-empty-clean",
          _rme.verdict == CLEAN and _rme.clean is True and tiles(_rme.lossy)
          and not any(s["class"] == "ambiguous" for s in _rme.lossy["span"]))

    # MALFORMED-INPUT HARDENING: each off-grammar line exercised here stays `ambiguous` (clean False,
    # tiling), never CANNOT_EVALUATE (the disposition stays review-routed per spec 14.1) and never a
    # permissive read.
    _mirror_face = _face(_v.render_mirror("backlog_item", [dict(_mirror_rec, type="backlog_item")]))
    _ver_face = _face(_ver_full)
    # a column bullet BEFORE any `## <id>` heading (orphan): id_owner not yet held -> ambiguous.
    check("mirror-orphan-column-ambiguous",
          _ambiguous(_mirror_face.replace("## AA-1\n", "- title: orphan\n## AA-1\n")))
    # an UNKNOWN column key under a valid id heading -> ambiguous (closed column vocabulary).
    check("mirror-unknown-column-ambiguous",
          _ambiguous(_mirror_face.replace("- title: a mirror title\n", "- notacol: x\n")))
    # a `## ` heading that is not a record id -> ambiguous.
    check("mirror-bad-id-heading-ambiguous",
          _ambiguous(_mirror_face.replace("## AA-1\n", "## NOT AN ID\n")))
    # ownership does NOT survive a blank line: a column row after the record's terminating blank -> ambiguous.
    check("mirror-column-after-blank-ambiguous",
          _ambiguous(_mirror_face.rstrip("\n") + "\n\n- title: stray\n"))
    # a mirror column bullet under an UNKNOWN H1 title falls back to the record-list family -> ambiguous.
    check("unknown-h1-record-fallback",
          _ambiguous(_mirror_face.replace("# BACKLOG_ITEM index", "# NOT A VIEW")))
    # a VERSION release row before any `## Releases` subsection (section None) -> ambiguous.
    check("version-row-outside-subsection-ambiguous",
          _ambiguous(_ver_face.replace(
              "## Releases\n", "- 1.0.0 (2026-01-01) worklog WL-1..WL-2\n## Releases\n")))
    # an ALIEN `## ` subsection in VERSION context -> ambiguous.
    check("version-alien-subsection-ambiguous",
          _ambiguous(_ver_face.replace("## Changelog summaries", "## Other Section")))
    # a VERSION release row inside a record-list (TODO) face stays ambiguous (family gating; Gemini's edge).
    check("version-row-in-record-view-ambiguous",
          _ambiguous(face.replace("- BI-1 (open): do a thing\n",
                                  "- 1.0.0 (2026-01-01) worklog WL-1..WL-2\n")))
    # a SECOND H1 in an otherwise exact face is drift (the newly-found gap): ambiguous, clean False.
    check("second-h1-ambiguous",
          _ambiguous(face.replace("- BI-1 (open): do a thing\n",
                                  "# Injected Heading\n\n- BI-1 (open): do a thing\n")))

    # --- OPF-MIG-PR2-FU round 2: codex-confirmed false-clean discriminators (each fails pre-fix) --------
    # R2-1 (MAJOR: a release row under `## Changelog summaries`): an EMPTY-SPAN release row renders as
    # `- <ver> (<date>) worklog (none)`, which ALSO matches the summary shape `- <covers> (<status>)`, so
    # pre-fix the summaries branch accepted it as a preserved_verbatim summary and the face read clean.
    # The summaries branch now excludes a release-shaped row -> ambiguous (drift).
    check("version-release-under-summaries-ambiguous",
          _ambiguous(_ver_face.replace("- 1.0.5 (released)\n",
                                       "- 1.0.0 (2026-01-01T00:00:00Z) worklog (none)\n")))
    # R2-2 (MAJOR: exactly-H2 recognition): no renderer emits an H3..H6 heading, so a deeper heading
    # establishes NO mirror ownership, NO VERSION subsection, and NO record structural subheading; each is
    # `ambiguous` drift. Pre-fix `^#{2,6}` accepted the heading and `lstrip("#")` discarded its level, so
    # an H3/H6 `### <id>` owned mirror column rows and a `### Releases` owned release rows (false-clean).
    check("mirror-h3-ownership-ambiguous", _ambiguous(_mirror_face.replace("## AA-1\n", "### AA-1\n")))
    check("mirror-h6-ownership-ambiguous", _ambiguous(_mirror_face.replace("## AA-1\n", "###### AA-1\n")))
    check("version-h3-subsection-ambiguous",
          _ambiguous(_ver_face.replace("## Releases\n", "### Releases\n")))
    check("version-h6-subsection-ambiguous",
          _ambiguous(_ver_face.replace("## Changelog summaries\n", "###### Changelog summaries\n")))
    check("record-h3-subheading-ambiguous",
          _ambiguous(face.replace("# TODO\n\n", "# TODO\n\n### Injected\n\n")))
    # R2-3 (MINOR: indented header close): the renderer emits the closing `-->` at column 0; an INDENTED
    # `  -->` is not a clean close, so the header is malformed and the whole face is ambiguous / NON-clean.
    # Pre-fix the `.strip()` comparison accepted the indented close and the face read clean.
    check("header-close-indented-ambiguous", _ambiguous(face.replace("\n-->\n", "\n  -->\n")))
    # R2-4 (roster narrowing witness): a POPULATED record-list face through the REAL renderers reads clean
    # with a preserved_verbatim record row, pinning the record family's populated row shape (the
    # empty-source roster above cannot see a populated-only row; coverage stated at the roster comment).
    check("populated-todo-face-clean", _clean_face(_face(_v.render_todo(dict(
        backlog_item=[dict(id="BI-7", type="backlog_item", status="open", title="a populated item")],
        block=[])))))
    check("populated-done-face-clean", _clean_face(_face(_v.render_done(dict(
        done=[dict(id="DN-1", type="done", title="a completed thing")])))))

    # --- OPF-MIG-PR2-FU round 3: codex round-2 QA discriminators (each fails pre-fix) -------------------
    # R3-1 (MAJOR: pre-H1 body content bypassed the family gate): family starts None, and the record-list
    # fall-through recognized a `- <NS>-<n>` bullet BEFORE any H1 had selected a family, so a bullet
    # prepended ahead of the H1 of a genuine mirror face AND of the genuine VERSION face read
    # preserved_verbatim and the face stayed clean (the exact codex repro). Any non-blank, non-header
    # pre-H1 line is now `ambiguous` drift (a genuine face opens with its single H1).
    check("pre-h1-bullet-in-mirror-ambiguous",
          _ambiguous(_mirror_face.replace("# BACKLOG_ITEM index", "- BI-9 (open) x\n# BACKLOG_ITEM index")))
    check("pre-h1-bullet-in-version-ambiguous",
          _ambiguous(_ver_face.replace("# VERSION", "- BI-9 (open) x\n# VERSION")))
    # The pre-H1 line ITSELF is the ambiguous span (never preserved_verbatim), and the genuine body after
    # the H1 stays recognized (the family gate rejects only what precedes the title).
    r3 = import_aiqt_face(mk("legacy/R3-preh1.md",
                             _ver_face.replace("# VERSION", "- BI-9 (open) x\n# VERSION")))
    check("pre-h1-line-itself-ambiguous",
          any(s["class"] == "ambiguous" and "before the view title" in s["note"] for s in r3.lossy["span"])
          and any(s["class"] == "preserved_verbatim" for s in r3.lossy["span"]))

    # --- OPF-MIG-PR2-FU round 4: round-3 QA discriminators (the three false-clean checks fail pre-fix; --
    # the empty-value ref row never matched even the round-3 regex, so it is a regression guard).
    # R4-1 (MAJOR, self-introduced in round 3: a whitespace-only ref-row value read clean): the round-3
    # bare `.` value requirement also matched a whitespace-only value, so `  - doc:  ` under a genuine
    # REFERENCES record read preserved_verbatim / clean, though a schema-valid locator is never ALL
    # whitespace (_opf_schema._validate_refs strips it). The requirement is now `.*\S` (at least one
    # non-whitespace character ANYWHERE in the value): a whitespace-only row and an empty-value row are
    # both `ambiguous` drift, while the genuine leading-space locator stays clean (the f6 check above).
    refs_face = face.replace("# TODO", "# REFERENCES")
    check("ws-only-ref-value-ambiguous",
          _ambiguous(refs_face.replace("- BI-1 (open): do a thing\n",
                                       "- RF-1 (recorded): a ref\n  - doc:  \n")))
    check("empty-ref-value-ambiguous",
          _ambiguous(refs_face.replace("- BI-1 (open): do a thing\n",
                                       "- RF-1 (recorded): a ref\n  - doc:\n")))
    # R4-2 (MED: a sentinel-bearing face with no view body read clean): a header-only face (no H1 ever
    # seen) and a header plus a bare `# VERSION` title with NOTHING after it both reached finalization
    # with only ignored cells and read clean, though every renderer emits at least one body line after
    # its H1 (the empty-source roster above pins that). Both now reclassify to all-`ambiguous` (truncated
    # generated output), clean False; the genuine EMPTY faces stay clean (the roster, mirror-empty, and
    # version-md-empty checks above).
    check("header-only-face-ambiguous", _ambiguous(_face("")))
    check("title-only-version-face-ambiguous", _ambiguous(_face("# VERSION\n")))

    # --- OPF-MIG-PR2-FU round 5: DISCLOSURE discriminator (round-4 codex QA MEDIUM, maintainer-ACCEPTED --
    # residual; disclosure-only, no recognition change). A genuine record whose TITLE is control-only
    # escapes through the REAL renderer (_opf_views._md_text drops C0/DEL controls) to `- <id> `, and the
    # write path's trailing-whitespace normalization (_norm, the on-disk shape) strips the separator space
    # to a bare `- <id>` row, off the `- <NS>-<n> ` record shape: a KNOWN safe-direction false-drift
    # (ambiguous, review-routed, never a false clean), disclosed in _FACE_RESIDUAL; the store's record
    # validation and the render drift gate own record completeness. Pinning it keeps a later recognition
    # change from silently flipping the disclosed direction.
    check("control-only-title-id-collapse-ambiguous",
          _ambiguous(_norm(_face(_v.render_done(dict(
              done=[dict(id="DN-1", type="done", title="\x01\x02\x03")]))))))

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
          "(no or unknown class) to NON-clean. OPF-MIG-PR2-FU round-2 discriminators (codex QA): an "
          "empty-span release row moved under the Changelog-summaries subsection is drift, never a summary "
          "(the two row shapes overlap, so the summaries branch excludes the release shape); a subheading "
          "is recognized at exactly H2, so an H3..H6 heading forges no mirror ownership, VERSION "
          "subsection, or structural subheading; an indented header close is a malformed header (the face "
          "is NON-clean); and a POPULATED record-list face (TODO / DONE through the real renderers) reads "
          "clean, pinning the record family's populated row shape. OPF-MIG-PR2-FU round-3 discriminators "
          "(codex QA round 2): pre-H1 body content is drift, never preserved (no family grammar is live "
          "before the single H1 view title selects one, so a record bullet prepended ahead of the H1 of a "
          "genuine mirror or VERSION face is ambiguous); and a genuine contextual reference row whose "
          "schema-valid locator begins with whitespace reads preserved_verbatim / clean (the ref-row value "
          "requirement no longer anchors a non-space at the separator). OPF-MIG-PR2-FU round-4 "
          "discriminators (round-3 QA): the ref-row value requires at least one non-whitespace character "
          "ANYWHERE after the separator, so a whitespace-only or empty value is drift while the "
          "leading-space locator stays clean; and a truncated generated face (header-only, or a bare H1 "
          "view title with nothing after it) reclassifies to all-ambiguous / non-clean, while every "
          "genuine empty render (the roster, mirror-empty, and version-md-empty checks) stays clean. "
          "OPF-MIG-PR2-FU round-5 disclosure discriminator (round-4 QA, maintainer-accepted residual): a "
          "genuine record whose control-only title escapes through the real renderer and the on-disk "
          "trailing-whitespace normalization to a bare `- <id>` row reads ambiguous / non-clean, the "
          "disclosed safe-direction false-drift, never a false clean.")
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
