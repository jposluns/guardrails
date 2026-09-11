#!/usr/bin/env python3
"""OPF (DevProcess) changelog gates: range coverage + freeze over version.toml + CHANGELOG.md (U5).

Offline, stdlib only, fail-closed. This is the FACTS layer over the release triad U3 (`_opf_release`)
carries: U3 validates version.toml / worklog.toml structurally (SemVer, span tiling, the coverage-digest
canonicalization, the summary-row schema); THIS unit adds the two deterministic gates spec section 7
defines over the curated `CHANGELOG.md`, which U3's docstring explicitly leaves to U5:

  1. RANGE COVERAGE (spec 7.1). From version.toml and CHANGELOG.md alone: every non-superseded,
     non-unreleased `[[summary]]` row's `covers` token parses and refers only to ledger versions (U3
     already asserts this); those rows TILE the release ledger EXACTLY (every released version covered
     once, no gap, no overlap, no duplicate); and they match the CHANGELOG entry headings 1:1 (every
     release-or-range summary row has exactly one matching `## <covers>` heading and every release-or-
     range heading matches exactly one such row), while the optional `## unreleased` heading, when
     present, matches the `covers = "unreleased"` working row rather than a released row.

  2. FREEZE (spec 7.2). For every PUBLISHED summary, recompute its freeze digest over the EXACT bytes of
     its CHANGELOG.md entry (LF-normalized, from its heading line up to but not including the next entry
     heading or end of file) and compare it to the digest recorded on the `[[summary]]` row; and recompute
     the underlying `coverage_digest`s of the releases against the current worklog (reusing U3's
     `check_frozen_coverage`). A mismatch on either is a finding: an edit to a published entry that did
     not go through the recorded re-publish flow breaks the freeze digest, and an edit to the facts the
     summary rests on breaks a coverage digest. A `working` summary (including the unreleased tail) is
     edited with no ceremony, so it takes no freeze digest and is not checked here.

The curated CHANGELOG.md is a DELIVERABLE gated on FACTS, not a byte-drift view (spec 6.3, 10.4): it
carries NO do-not-edit header and is never reconciled byte-for-byte. These two gates are its whole gate.

Adopter-rooted, exactly like doctor.py / migrate.py (and the rest of the OPF tooling): the gates operate
against a PRODUCT repository root named by --root (default: the cwd), resolving the store through
`_opf_store` discovery, NEVER through `_gen_common.repo_root()`. This pack is not a DevProcess adopter,
so a live `--root .` here resolves NOT-ADOPTED and reports NOT APPLICABLE (exit 0); the assurance rides
the `--self-test` leg over synthetic stores, reached through `tools/opf.py --self-test` as the changelog
leg (mirroring how `_opf_store` / `_opf_schema` / `_opf_release` / `_opf_emit` register their legs).

Fail-closed everywhere (spec 3 "Fail closed"; the check-fails-closed-on-unreadable rule): an unresolvable
store, an unreadable or unparseable version.toml, worklog.toml, or CHANGELOG.md, or a version.toml /
worklog.toml that does not validate against the release-triad schema, is a distinct CANNOT-EVALUATE
outcome (exit 2) that STOPS, never a silent clean pass. A resolved but structurally inconsistent ledger
is a cannot-evaluate here rather than a U5 finding: the changelog FACTS gates require a consistent ledger,
and those structural findings belong to the version gate (the fail-closed seam between the units, matching
`_opf_release.release_cut`'s "cannot cut from an inconsistent ledger").

Reference-tooling / spec ambiguities recorded for the finalizer (each resolved the strict, fail-closed
way and named so the choice is reviewable, per disclose-guard-residuals):
  - THE FREEZE-DIGEST SCHEME. Spec 7.2 fixes the freeze digest's INPUT bytes (the entry from its heading
    line to the next heading or EOF, LF line endings) but does not pin the hash. This unit defines it as
    `sha256:` + the hex SHA-256 of those UTF-8 bytes, matching the `sha256:<64 hex>` form every ledger
    digest already takes (U3's `_valid_digest`, Appendix B). The finalizer may re-fix it in one place.
  - ENTRY-HEADING RECOGNITION. Spec 6.3 says an entry begins with `## <covers>` optionally followed by
    parenthesized dates. Heading recognition is delegated to the vendored Marko 2.2.4 CommonMark parser
    through the narrow, parse-only `_commonmark_headings` adapter (tools/_vendor/marko/, import-pinned and
    fail-closed): an entry heading is a DIRECT-child-of-Document level-2 heading, ATX (`## covers`) or
    setext (`covers\n---`). Because recognition is over the parsed AST, a `## ` inside a fenced or indented
    code block, an HTML block, a block quote, or a list is NOT a boundary, and a deeper `### ` sub-heading
    is not either. This unit then reads the covers token as the first whitespace-delimited word of the
    heading payload the adapter returns, and requires the remainder to be EMPTY or a single parenthesized
    group of ISO date(s) (`(YYYY-MM-DD)`, one or more dates joined by `,`, `..`, or ` to `). A heading with
    no token, or with any other trailing text (including a multiline setext title), is a finding
    (fail-closed and over-strict); the heading still counts as an entry boundary, so a malformed entry is
    reported rather than silently merged into the previous one. A parser exception, a missing or invalid
    source span, a vendored-import / provenance mismatch, or oversized input (> 1 MiB) is a distinct
    CANNOT-EVALUATE, never "no entries" (the adapter raises `HeadingScanError`, caught in `run_gates`).
  - CHANGELOG LOCATION. Spec 5.8 / 6.3 fix the public `CHANGELOG.md` at the PRODUCT repository root, and
    hold it byte-identical there across every store topology. This unit reads `<product root>/CHANGELOG.md`
    accordingly; honouring a non-default `[deliverables."CHANGELOG.md"].target` from the manifest is a
    deferred refinement (the default target IS `CHANGELOG.md` at the product root) and is noted, not
    silently assumed away.
  - THE COVERAGE-DIGEST RECOMPUTE reuses U3's `check_frozen_coverage` over the ACTIVE worklog. A ROTATED
    store (a covered entry moved to the archive, spec 12) makes that recompute fail closed with a "cannot
    recompute" finding here, because U5 reads only the active worklog and no archive-worklog RESOLVER
    exists yet. This is a DEFERRED spec-12 conformance OBLIGATION on U6 (the store-level validate_store /
    doctor, which composes active + archive before the recompute), NOT a permanent limitation of the gate:
    U5 deliberately adds no archive-reading, drawing the same active-worklog seam `_opf_release.release_cut`
    draws, and the rotated-store recompute is tracked to close in U6. Noted rather than silently skipped.
  - SINGLE-SNAPSHOT LIMITATION (disclose-guard-residuals). These gates evaluate ONE snapshot of
    version.toml + worklog.toml + CHANGELOG.md together. A COORDINATED rewrite that changes a worklog
    FACT, the release `coverage_digest` that rests on it, and the entry's freeze `digest` all consistently
    leaves the snapshot internally consistent, so neither the freeze gate nor the coverage-digest recompute
    can detect it: the recompute confirms the digest matches the (rewritten) worklog, and the freeze digest
    matches the (rewritten) entry. Detecting such a rewrite requires an IMMUTABLE PRIOR reference U5
    structurally lacks: the prior-committed version.toml release rows (spec 6.1 makes them append-only and
    immutable), the git history, or the SECI published release digest. This is a TRACKED U6 validate_store /
    release-integrity obligation (the store level composes the prior reference), not something a
    single-snapshot facts gate can close. Noted rather than left implied.
"""
import datetime
import hashlib
import os
import re
import stat
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _commonmark_headings  # noqa: E402  U5: vendored-Marko CommonMark heading recognition (parse-only, fail-closed)
import _journal        # noqa: E402  contained (dir-fd, no-follow) reader + JournalError + containment probe
import _opf_store      # noqa: E402  U1: store resolution + discovery + the contained TOML reader
import _opf_release    # noqa: E402  U3: version.toml / worklog.toml validators + covers parsing + coverage recompute
# U1 supplies the outcome vocabulary the release-triad validators return; reuse it so U5 grades a ledger
# exactly as U3 does rather than re-declaring VALID / CANNOT-EVALUATE.
from _opf_store import VALID, CANNOT_EVALUATE, RESOLVED, NOT_ADOPTED  # noqa: E402
# U3 helpers reused rather than re-implemented (single source of truth): the two ledger validators, the
# covers-token parser and its ledger-order range, the active-worklog id map, and the coverage-digest
# recompute. Importing underscore-prefixed helpers across the _opf_* modules is the house idiom (U3 itself
# imports `_valid_id_shape` / `_valid_timestamp` from U2 and `_canonical_float` from U8).
from _opf_release import (  # noqa: E402
    validate_version, validate_worklog, _entries_by_id, _parse_covers, _covers_range,
    check_frozen_coverage, UNRELEASED,
)

CHANGELOG_REL = "CHANGELOG.md"            # the curated public changelog, at the product root (spec 5.8/6.3)

# Store-level outcomes (the doctor.py PASS/FAIL/NA/MALFORMED idiom, named for this gate; exit 0/0/1/2).
PASS = "PASS"
FINDING = "FINDING"                       # a gate finding -> exit 1
NOT_APPLICABLE = "NOT-APPLICABLE"         # not a DevProcess adopter -> degrade, never fake a pass (exit 0)
# CANNOT_EVALUATE (imported) -> exit 2: unreadable / unparseable / unresolvable / inconsistent carriers.

EXIT = {PASS: 0, NOT_APPLICABLE: 0, FINDING: 1, CANNOT_EVALUATE: 2}


class ChangelogResult:
    __slots__ = ("status", "findings")

    def __init__(self, status, findings=None):
        self.status = status              # PASS / FINDING / NOT_APPLICABLE / CANNOT_EVALUATE
        self.findings = findings or []


# --- freeze digest + CHANGELOG parsing (spec 6.3, 7.2; schemes defined here) --------------------------

def _normalize_lf(text):
    """LF-normalize CHANGELOG text before the freeze digest is taken (spec 7.2: LF line endings), so the
    digest is invariant to a CRLF or lone-CR checkout and depends only on the entry's logical bytes."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def freeze_digest(entry_text):
    """The freeze digest of one CHANGELOG.md entry (spec 7.2, scheme defined here): `sha256:` + the hex
    SHA-256 of the entry's LF-normalized UTF-8 bytes. `entry_text` is the exact slice from an entry's
    heading line up to but not including the next entry heading or end of file (see `_changelog_entries`).
    The bytes are LF-normalized here before hashing, so a DIRECT caller passing CRLF or lone-CR text gets
    the same digest as the live parse path (which normalizes the whole CHANGELOG before slicing); on the
    already-LF live path the normalization is idempotent, so it is invariant there."""
    return "sha256:" + hashlib.sha256(_normalize_lf(entry_text).encode("utf-8")).hexdigest()


# The only text spec 6.3 permits after the covers token on an entry heading: nothing, or a single
# parenthesized group of ISO date(s) -- one or more dates joined by ',', '..', or ' to '. Anything else
# (arbitrary trailing prose) is a fail-closed finding. Over-strict by design: an unusual but legitimate
# date rendering is rejected rather than silently admitting junk (disclose-guard-residuals).
_HEADING_DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_HEADING_DATE_SUFFIX_RE = re.compile(
    r"^\(\s*[0-9]{4}-[0-9]{2}-[0-9]{2}"
    r"(?:\s*(?:,|\.\.| to )\s*[0-9]{4}-[0-9]{2}-[0-9]{2})*\s*\)$")   # one or more dates (spec 6.3)


def _valid_heading_date_suffix(suffix):
    """True when `suffix` is the only text spec 6.3 permits after an entry's covers token: a single
    parenthesized group of one or more ISO dates joined by ',', '..', or ' to '. Digits are ASCII [0-9]
    (never a Unicode digit) and each date is calendar-validated (a shape like (2026-99-99) is rejected).
    Fail-closed and over-strict by design."""
    if _HEADING_DATE_SUFFIX_RE.match(suffix) is None:
        return False
    for token in _HEADING_DATE_RE.findall(suffix):
        year, month, day = (int(part) for part in token.split("-"))
        try:
            datetime.date(year, month, day)
        except ValueError:
            return False
    return True


def _changelog_entries(text):
    """Parse the CHANGELOG into its entries. Returns (entries, findings): `entries` is a list of
    (covers_token, entry_text) in document order, where `entry_text` is the exact byte slice from the
    entry's heading start up to but not including the next entry heading or end of file (LF-normalized,
    spec 7.2); `findings` names any entry heading that carries no covers token, or that carries text after
    the covers token that is not parenthesized ISO date(s) (spec 6.3).

    Heading recognition is delegated to the vendored-Marko `_commonmark_headings` adapter: an entry heading
    is a DIRECT-child-of-Document level-2 CommonMark heading (ATX or setext), so a `## ` inside a fenced or
    indented code block, an HTML block, a block quote, or a list is not a boundary, and a deeper `### ` is
    not either. A heading with a valid token but a malformed suffix, or a multiline setext title, is still
    recorded as an entry (its token participates in coverage and heading matching), so the finding is
    additive rather than compounding into an unmatched heading; a heading with an empty payload is a
    boundary that yields a no-token finding and no entry, exactly as before.

    Raises `_commonmark_headings.HeadingScanError` (a distinct fail-closed cannot-evaluate) on a parser
    exception, a missing or invalid source span, a vendored-import / provenance mismatch, or oversized
    input; `run_gates` catches it and returns CANNOT-EVALUATE, so a scan that cannot be trusted is never
    reported as "no entries" (guard-input-soundness; check-fails-closed-on-unreadable)."""
    scan = _commonmark_headings.scan_entry_headings(text)
    norm = scan.normalized_text           # the same LF-normalized string the parser saw; slice and length come from it
    heads = []                            # (start_offset, token_or_None, payload) for each direct H2, in document order
    for heading in scan.headings:
        payload = heading.payload
        token = payload.split(None, 1)[0] if payload else None
        heads.append((heading.start, token, payload))
    entries = []
    findings = []
    for i, (start, token, rest) in enumerate(heads):
        end = heads[i + 1][0] if i + 1 < len(heads) else len(norm)
        if token is None:
            findings.append("CHANGELOG.md has a '## ' heading with no covers token (spec 6.3)")
            continue
        if "\n" in rest:                  # a multiline setext entry heading: the token and any date suffix
            # span more than one line. An entry heading must be a single line; record a finding (additive,
            # like a malformed suffix) rather than letting the interior newline be stripped away unseen so
            # that a date on a second line validates as a clean single-line suffix.
            findings.append("CHANGELOG.md has a multiline '## ' entry heading (a setext title spanning "
                            "multiple lines); an entry heading must be a single line (spec 6.3)")
        suffix = rest[len(token):].strip()
        if suffix and not _valid_heading_date_suffix(suffix):
            findings.append("CHANGELOG.md heading {!r} carries text after the covers token that is not "
                            "parenthesized date(s) (spec 6.3)".format(rest))
        entries.append((token, norm[start:end]))
    return entries, findings


# --- gate 1: range coverage (spec 7.1) ----------------------------------------------------------------

def check_range_coverage(releases, summaries, heading_tokens):
    """The range-coverage gate (spec 7.1) over the VALIDATED release rows, summary rows, and the ordered
    CHANGELOG entry-heading tokens. Returns a list of findings (empty when clean). It confirms the
    non-superseded, non-unreleased summary rows tile the ledger exactly (no gap, no overlap, no duplicate)
    and match the CHANGELOG headings 1:1, with the optional `## unreleased` heading matching the
    unreleased working row. It also asserts every released-or-range summary row is published (frozen); a
    released row left `status = "working"` tiles coverage yet the freeze gate skips it, so it is caught
    here (spec 7.2/7.3). The caller passes VALIDATED carriers (U3 `validate_version` VALID), so covers
    tokens already parse and refer to ledger versions; this gate re-parses defensively and fails closed on
    a malformed token rather than trusting it."""
    findings = []
    ledger_versions = [r.get("version") for r in releases if isinstance(r, dict) and "version" in r]

    relevant = [row for row in summaries
                if isinstance(row, dict)
                and row.get("status") != "superseded"
                and row.get("covers") != UNRELEASED]

    # Tiling: every released version is covered by exactly one relevant summary row (spec 7.1).
    coverage_count = [0] * len(ledger_versions)
    relevant_tokens = []
    for row in relevant:
        covers = row.get("covers")
        # A non-superseded, non-unreleased (released-or-range) summary must be published (frozen): only the
        # unreleased working tail carries status "working". A released row left working tiles coverage but
        # the freeze gate skips it (it carries no digest), so it must be caught here (spec 7.2/7.3).
        if row.get("status") != "published":
            findings.append("summary covers {!r} is a released-or-range row with status {!r}; a "
                            "non-superseded, non-unreleased summary must be published (frozen) "
                            "(spec 7.2/7.3)".format(covers, row.get("status")))
        parsed, err = _parse_covers(covers, ledger_versions)
        if err is not None:               # a VALID ledger has already ruled this out; fail closed if not
            findings.append("summary covers {!r}: {} (spec 7.1)".format(covers, err))
            continue
        relevant_tokens.append(covers)
        rng = _covers_range(parsed, ledger_versions)
        if rng is None:                   # defensive: a non-unreleased relevant row always resolves a range
            continue
        for i in range(rng[0], rng[1] + 1):
            coverage_count[i] += 1
    for i, count in enumerate(coverage_count):
        if count == 0:
            findings.append("released version {!r} is covered by no changelog summary row (a coverage gap; "
                            "the summaries must tile the ledger exactly, spec 7.1)".format(ledger_versions[i]))
        elif count > 1:
            findings.append("released version {!r} is covered by {} changelog summary rows (a coverage "
                            "overlap; the summaries must tile the ledger exactly, spec 7.1)".format(
                                ledger_versions[i], count))

    # 1:1 heading matching (spec 7.1). Released-or-range headings match the relevant rows; the optional
    # `## unreleased` heading matches the unreleased working row.
    release_headings = Counter(t for t in heading_tokens if t != UNRELEASED)
    unreleased_headings = [t for t in heading_tokens if t == UNRELEASED]

    seen = set()
    for t in relevant_tokens:
        if t in seen:                     # a VALID ledger has no duplicate covers; fail closed if not
            findings.append("summary covers {!r} is duplicated across changelog summary rows "
                            "(spec 7.1)".format(t))
        seen.add(t)
    for t in sorted(seen):
        count = release_headings.get(t, 0)
        if count == 0:
            findings.append("summary row {!r} has no matching '## {}' entry heading in CHANGELOG.md "
                            "(spec 7.1)".format(t, t))
        elif count > 1:
            findings.append("CHANGELOG.md has {} '## {}' entry headings; each release-or-range summary "
                            "matches exactly one heading (spec 7.1)".format(count, t))
    for t in sorted(release_headings):
        if t not in seen:
            findings.append("CHANGELOG.md '## {}' entry heading matches no non-superseded, non-unreleased "
                            "summary row (a superseded entry leaves CHANGELOG.md, and every heading matches "
                            "exactly one row, spec 7.1/6.4)".format(t))

    has_unreleased_row = any(isinstance(row, dict) and row.get("covers") == UNRELEASED for row in summaries)
    if len(unreleased_headings) > 1:
        findings.append("CHANGELOG.md has more than one '## unreleased' heading (spec 6.3/7.1)")
    if unreleased_headings and not has_unreleased_row:
        findings.append("CHANGELOG.md has an '## unreleased' heading but version.toml has no "
                        "covers = \"unreleased\" summary row (spec 7.1)")
    return findings


def check_entry_order(releases, heading_tokens):
    """The entry-ordering gate (spec 6.3): CHANGELOG entries run in DESCENDING order by the highest
    ledger version each entry covers, with the optional `## unreleased` entry FIRST. Returns a list of
    findings (empty when clean). Order is decided by the ledger INDEX a covers token resolves to (via the
    same U3 covers parser the coverage gate uses), never by a lexical compare of version tokens (a string
    sort would rank `## 1.10.0` above `## 1.9.0`). A malformed or unparseable token is left to the
    range-coverage gate and skipped here rather than double-reported. Reports the FIRST heading in
    document order that violates the required order (a misplaced `## unreleased`, or a release-or-range
    heading that is not strictly below the one before it), then stops."""
    findings = []
    ledger_versions = [r.get("version") for r in releases if isinstance(r, dict) and "version" in r]
    prev_high = None                      # highest covered ledger index of the previous release-or-range heading
    for pos, token in enumerate(heading_tokens):
        if token == UNRELEASED:
            if pos != 0:
                findings.append("CHANGELOG.md '## unreleased' entry is not first; the unreleased entry "
                                "leads the changelog and released entries follow in descending order "
                                "(spec 6.3)")
                break
            continue
        parsed, err = _parse_covers(token, ledger_versions)
        if err is not None:               # a malformed token is the range-coverage gate's finding
            continue
        rng = _covers_range(parsed, ledger_versions)
        if rng is None:                   # defensive: a non-unreleased token always resolves a range
            continue
        high = rng[1]                     # ledger index of the HIGHEST version this entry covers
        if prev_high is not None and high >= prev_high:
            findings.append("CHANGELOG.md entry '## {}' is out of order; entries run in descending order "
                            "by highest covered version, with the unreleased entry first (spec 6.3)".format(
                                token))
            break
        prev_high = high
    return findings


# --- gate 2: freeze and re-publish (spec 7.2) ---------------------------------------------------------

def check_freeze(summaries, version_data, entries_by_id, entry_map):
    """The freeze gate (spec 7.2). For every PUBLISHED summary row, recompute the freeze digest of its
    CHANGELOG.md entry and compare it to the recorded `digest`; then recompute the underlying release
    coverage digests against the current worklog (reusing U3's `check_frozen_coverage`). Returns a list of
    findings (empty when clean). `entry_map` maps a covers token to the list of CHANGELOG entry-byte
    strings for it (a duplicated heading yields more than one, which is an ambiguous freeze). `entries_by_id`
    maps a WL-number to its active worklog entry (for the coverage recompute). A `working` summary carries
    no freeze digest and is not checked (spec 7.2)."""
    findings = []
    for row in summaries:
        if not isinstance(row, dict) or row.get("status") != "published":
            continue
        covers = row.get("covers")
        entries = entry_map.get(covers, [])
        if len(entries) == 0:
            findings.append("published summary {!r} has no matching CHANGELOG.md entry to freeze; a "
                            "published summary must appear in CHANGELOG.md (spec 7.2)".format(covers))
            continue
        if len(entries) > 1:
            findings.append("published summary {!r} matches {} CHANGELOG.md entries; the freeze digest is "
                            "ambiguous (spec 7.2)".format(covers, len(entries)))
            continue
        if row.get("digest") != freeze_digest(entries[0]):
            findings.append("published summary {!r}: the CHANGELOG.md entry does not match its recorded "
                            "freeze digest (the published entry was edited without a recorded "
                            "re-publication; the re-publish flow updates the digest in the same change, "
                            "spec 7.2)".format(covers))

    # The underlying facts must hold too: recompute every release's coverage digest against the current
    # worklog (spec 7.2). A change to the facts a summary rests on breaks a coverage digest and lands as
    # new worklog entries, not a re-wording (spec 6.2/7.2). Reuses U3's guard over the active worklog.
    for f in check_frozen_coverage(version_data, entries_by_id):
        findings.append("coverage-digest recompute: {}".format(f))
    return findings


# --- both gates over a resolved store's parsed inputs -------------------------------------------------

def run_gates(version_data, worklog_data, changelog_text, registered_vendors=frozenset()):
    """Run both changelog gates over the PARSED inputs. Returns a ChangelogResult. CANNOT-EVALUATE (fail
    closed) when version.toml or worklog.toml does not validate against the release-triad schema (the
    changelog FACTS gates require a consistent ledger and worklog; those structural findings belong to the
    version / worklog gates, the fail-closed seam with U3); FINDING when a gate finds a coverage, ordering,
    or freeze violation; PASS otherwise. `registered_vendors` is the manifest's `[vendors].registered`
    x-<vendor> set (spec 8.7/9), threaded into worklog validation so a worklog entry using a
    manifest-registered vendor extension validates rather than failing closed as unregistered; the live
    `evaluate` path supplies the real set, the self-test drives specific sets, and the default empty set is
    the base-only case. This is the pure core the store-level `evaluate` and the self-test drive."""
    vv = validate_version(version_data)
    if vv.status != VALID:
        return ChangelogResult(CANNOT_EVALUATE,
                               ["cannot evaluate the changelog gates: version.toml does not validate "
                                "against the release-ledger schema (run the version gate first; the "
                                "changelog gates require a consistent ledger)"] + vv.findings)
    wv = validate_worklog(worklog_data, registered_vendors=registered_vendors)
    if wv.status != VALID:
        return ChangelogResult(CANNOT_EVALUATE,
                               ["cannot evaluate the changelog freeze gate: worklog.toml does not "
                                "validate (the freeze gate recomputes coverage digests against the "
                                "worklog)"] + wv.findings)
    by_id, id_findings = _entries_by_id(worklog_data)
    if id_findings:                       # a VALID worklog has clean ids; fail closed if not
        return ChangelogResult(CANNOT_EVALUATE,
                               ["cannot evaluate: worklog ids are malformed"] + id_findings)

    try:
        cl_entries, findings = _changelog_entries(changelog_text)
    except _commonmark_headings.HeadingScanError as exc:
        # A fail-closed cannot-evaluate from the vendored-Marko heading scanner (oversized input, a parser
        # fault, a missing/invalid source span, or a vendored-import / provenance mismatch) is a distinct
        # cannot-evaluate here, never "no entries" (guard-input-soundness; check-fails-closed-on-unreadable).
        return ChangelogResult(CANNOT_EVALUATE,
                               ["cannot evaluate the changelog gates: heading scan failed ({}): {}".format(
                                   exc.reason, exc)])
    except UnicodeEncodeError as exc:
        # The heading scanner sizes its input with text.encode("utf-8") BEFORE parsing; a str carrying a
        # lone surrogate (e.g. a caller that decoded with errors="surrogateescape") makes that encode raise
        # UnicodeEncodeError, which is not a HeadingScanError. The live evaluate() path decodes strict utf-8
        # and cannot produce a surrogate, but run_gates is an exported composition surface, so an
        # un-encodable changelog is a distinct fail-closed cannot-evaluate here, never an escaping crash
        # (guard-input-soundness; check-fails-closed-on-unreadable).
        return ChangelogResult(CANNOT_EVALUATE,
                               ["cannot evaluate the changelog gates: changelog text is not encodable as "
                                "UTF-8 ({})".format(exc)])
    findings = list(findings)
    entry_map = {}
    heading_tokens = []
    for token, entry_text in cl_entries:
        entry_map.setdefault(token, []).append(entry_text)
        heading_tokens.append(token)

    findings.extend(check_range_coverage(vv.releases, vv.summaries, heading_tokens))
    findings.extend(check_entry_order(vv.releases, heading_tokens))
    findings.extend(check_freeze(vv.summaries, version_data, by_id, entry_map))
    return ChangelogResult(FINDING if findings else PASS, findings)


# --- store resolution + input reads (spec 4.3, 5.8, 6.3) ----------------------------------------------

def _load_inputs(resolution, product_root):
    """Read the four inputs of a RESOLVED store, contained and no-follow. Returns
    (version_data, worklog_data, changelog_text, registered_vendors, error): the first four parsed on
    success and `error` None, or all-None (with an empty registered-vendors set) plus a fail-closed
    message string. version.toml / worklog.toml / manifest.toml are read from the machine store;
    CHANGELOG.md from the product repository root (spec 5.8/6.3). `registered_vendors` is the manifest's
    `[vendors].registered` x-<vendor> set (spec 8.7/9), threaded into the worklog validation so a
    manifest-registered vendor extension is not wrongly reported unregistered. An absent, unreadable,
    unparseable, or non-UTF-8 required input, or a manifest that does not validate in full against the
    manifest schema (spec 4.5/9), is a fail-closed cannot-evaluate message, never a silent empty pass (the
    check-fails-closed-on-unreadable rule)."""
    pointer = resolution.pointer_source != "default"
    try:
        store_fd = _opf_store._open_store_root_fd(resolution.store_root, pointer)
    except OSError as exc:
        return None, None, None, frozenset(), "cannot open store root {} ({})".format(
            resolution.store_root, exc)
    try:
        try:
            version_data = _opf_store._read_toml_contained(store_fd, resolution.machine_rel + "/version.toml")
            worklog_data = _opf_store._read_toml_contained(store_fd, resolution.machine_rel + "/worklog.toml")
            manifest_data = _opf_store._read_toml_contained(
                store_fd, resolution.machine_rel + "/" + _opf_store.MANIFEST_NAME)
        except (_opf_store.StoreError, OSError) as exc:
            # A StoreError (unreadable/unparseable) OR a raw OSError (e.g. a PermissionError from the
            # contained lstat inside _read_toml_contained) is the fail-closed cannot-evaluate (spec 3/7.1;
            # check-fails-closed-on-unreadable): an unreadable ledger must never escape as an uncaught error.
            return None, None, None, frozenset(), str(exc)
    finally:
        os.close(store_fd)
    if version_data is None:
        return None, None, None, frozenset(), ("version.toml is absent from the resolved store (a required "
                                                "input; fail-closed, spec 6.1)")
    if worklog_data is None:
        return None, None, None, frozenset(), ("worklog.toml is absent from the resolved store (a required "
                                                "input; fail-closed, spec 6.2)")
    if manifest_data is None:
        return None, None, None, frozenset(), ("manifest.toml is absent from the resolved store (a required "
                                                "input; fail-closed, spec 9)")

    # The manifest must validate in FULL against the release-triad manifest schema (spec 4.5/9), not only
    # its [vendors]: an invalid manifest is a fail-closed cannot-evaluate, never a silent partial pass.
    # validate_manifest returns CANNOT-EVALUATE for a non-table / non-devprocess manifest and INVALID for a
    # schema violation; either is fail-closed here.
    mv = _opf_store.validate_manifest(manifest_data)
    if mv.status != VALID:
        return None, None, None, frozenset(), ("manifest.toml does not validate against the manifest "
                                                "schema: {} (fail-closed, spec 4.5/9)".format(
                                                    "; ".join(mv.findings)))
    # Extract the registered x-<vendor> namespaces (spec 8.7/9) to thread into worklog validation so a
    # manifest-registered vendor extension is not wrongly reported unregistered (guard-input-soundness).
    vendor_findings = []
    registered_vendors = _opf_store._validate_vendors(manifest_data.get("vendors"), vendor_findings)

    try:
        product_fd = _opf_store._open_root_fd(product_root)
    except OSError as exc:
        return None, None, None, frozenset(), "cannot open product root {} ({})".format(product_root, exc)
    try:
        try:
            # A non-regular CHANGELOG.md (a FIFO, device, socket, or directory) is refused BEFORE any open:
            # opening a FIFO O_RDONLY with no writer blocks the process forever, so the regular-file gate is
            # checked on the lstat result rather than after _read_contained opens the target, exactly as
            # _opf_store._read_toml_contained guards the ledger reads (check-fails-closed-on-unreadable, SECA
            # resource-bounds; an unbounded block is worse than a crash for a CI gate).
            st = _journal._lstat_contained(product_fd, CHANGELOG_REL)
            if st is None:
                return None, None, None, frozenset(), ("{} is absent from the product root (a required "
                                                        "input; fail-closed, spec 5.8/6.3)".format(CHANGELOG_REL))
            if not stat.S_ISREG(st.st_mode):
                return None, None, None, frozenset(), ("{} is present but is not a regular file (an exotic "
                                                        "entry; fail-closed, never opened)".format(CHANGELOG_REL))
            raw, _ = _journal._read_contained(product_fd, CHANGELOG_REL)
        except (_journal.JournalError, OSError) as exc:
            return None, None, None, frozenset(), "cannot read {} ({})".format(CHANGELOG_REL, exc)
    finally:
        os.close(product_fd)
    try:
        changelog_text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        return None, None, None, frozenset(), "{} is not valid UTF-8 ({})".format(CHANGELOG_REL, exc)
    return version_data, worklog_data, changelog_text, registered_vendors, None


def evaluate(product_root):
    """Resolve the store at `product_root` and run both changelog gates. Returns a ChangelogResult:
    NOT-APPLICABLE when the root is not a DevProcess adopter (like doctor.py / migrate.py; this pack's own
    `--root .` lands here); CANNOT-EVALUATE (fail closed) when the store cannot resolve or a required input
    is unreadable, unparseable, or structurally inconsistent; FINDING on a coverage or freeze violation;
    PASS otherwise."""
    try:
        product_root = Path(os.path.abspath(product_root))
    except OSError as exc:
        # os.path.abspath resolves a relative root against the process cwd; a removed or unresolvable cwd
        # makes it raise (e.g. FileNotFoundError). An unresolvable --root is a fail-closed cannot-evaluate,
        # never an escaping crash (the module's fail-closed contract; check-fails-closed-on-unreadable).
        return ChangelogResult(CANNOT_EVALUATE,
                               ["cannot resolve --root path {!r} ({})".format(product_root, exc)])
    if not product_root.exists():
        return ChangelogResult(CANNOT_EVALUATE, ["--root path does not exist: {}".format(product_root)])
    if not product_root.is_dir():
        return ChangelogResult(CANNOT_EVALUATE, ["--root path is not a directory: {}".format(product_root)])
    res = _opf_store.resolve_store(product_root)
    if res.status == NOT_ADOPTED:
        return ChangelogResult(NOT_APPLICABLE,
                               ["not a DevProcess adopter ({}); the changelog gates do not apply".format(
                                   res.detail)])
    if res.status != RESOLVED:
        return ChangelogResult(CANNOT_EVALUATE, [res.detail])
    version_data, worklog_data, changelog_text, registered_vendors, error = _load_inputs(res, product_root)
    if error is not None:
        return ChangelogResult(CANNOT_EVALUATE, [error])
    return run_gates(version_data, worklog_data, changelog_text, registered_vendors)


def run(root):
    """Run the changelog gates under `root` and return the aggregate exit code (0 pass / NA, 1 finding,
    2 cannot-evaluate). Prints a concise status line and one line per finding."""
    result = evaluate(root)
    try:
        shown = Path(os.path.abspath(root))
    except OSError:
        shown = root                      # an unresolvable cwd already yielded CANNOT-EVALUATE in evaluate
    print("OPF changelog gates: {}".format(shown))
    print("  status: {}".format(result.status))
    for f in result.findings:
        print("  - {}".format(f))
    return EXIT[result.status]


# --- self-test ----------------------------------------------------------------------------------------

def self_test():
    """Range-coverage + freeze invariants over synthetic in-memory ledgers and on-disk stores. Judged on
    the returned status / finding values, never by grepping output (the isolate-verifiers rule). Returns
    0 clean, 1 on a failed check, 2 on a fail-closed error. Vectors: coverage gap, coverage overlap, an
    unmatched heading, a freeze-digest mismatch, a re-publish flow that keeps covers + coverage digests
    stable, a changed-facts coverage-digest break, a golden external freeze digest, a three-release
    interior-coverage pass, an entry-ordering finding, a heading-suffix finding, a fenced-code heading that
    is not an entry, an indented-false-fence unmatched heading, a released-working summary finding, a
    calendar-invalid heading date, a multi-date heading suffix pass, a non-final golden freeze digest,
    freeze_digest LF-normalization, the CLI arg-parser fail-closed cases (including an empty and a blank
    --root), a missing-[types] manifest failing closed,
    registered / unregistered x-vendor cases, unreadable / unparseable / inconsistent input failing closed,
    and end-to-end store resolution (NOT-APPLICABLE, cannot-evaluate, PASS, and FINDING) including a
    registered-vendor pass and an injected-OSError fail-closed path. Fail-closed store-input hardening: a
    FIFO CHANGELOG.md refused as non-regular (never blocking), a non-UTF-8 CHANGELOG.md, an un-encodable
    (lone-surrogate) changelog, an unresolvable --root, run()'s exit-code mapping, and the two unreleased
    heading vectors (a heading with no summary row, and a misplaced not-first entry)."""
    import tempfile
    import shutil

    try:
        _journal.require_containment()
    except _journal.JournalError as exc:
        print("OPF-CHANGELOG SELF-TEST ERROR: {}; fail-closed".format(exc), file=sys.stderr)
        return 2

    failures = []
    checked = 0

    def check(name, cond):
        nonlocal checked
        checked += 1
        if not cond:
            failures.append(name)

    def entry(n, summary="s"):
        return {"id": "WL-{}".format(n), "date": "2026-06-{:02d}T00:00:00Z".format((n % 27) + 1),
                "actor": {"kind": "maintainer"}, "kind": "added", "summary": summary}

    def freeze_of(changelog_text, token):
        entries, _ = _changelog_entries(changelog_text)
        by_token = {}
        for t, e in entries:
            by_token.setdefault(t, []).append(e)
        return freeze_digest(by_token[token][0])

    # --- shared base: a two-release ledger with a four-entry worklog ---------------------------------
    worklog = {"schema": 1, "entry": [entry(1), entry(2), entry(3), entry(4)]}
    by_id, _ = _entries_by_id(worklog)
    dig12 = _opf_release.compute_span_digest(by_id, (1, 2))
    dig34 = _opf_release.compute_span_digest(by_id, (3, 4))

    cl = ("# Changelog\n\nA curated summary of the worklog.\n\n"
          "## unreleased\n\n- in progress\n\n"
          "## 1.1.0 (2026-06-15)\n\n- second release\n\n"
          "## 1.0.0 (2026-06-01)\n\n- first release\n")

    def base_version(changelog_text):
        return {"schema": 1,
                "release": [
                    {"version": "1.0.0", "date": "2026-06-01T00:00:00Z",
                     "worklog_span": ["WL-1", "WL-2"], "coverage_digest": dig12},
                    {"version": "1.1.0", "date": "2026-06-15T00:00:00Z",
                     "worklog_span": ["WL-3", "WL-4"], "coverage_digest": dig34},
                ],
                "summary": [
                    {"covers": "unreleased", "status": "working"},
                    {"covers": "1.0.0", "status": "published", "digest": freeze_of(changelog_text, "1.0.0")},
                    {"covers": "1.1.0", "status": "published", "digest": freeze_of(changelog_text, "1.1.0")},
                ]}

    vbase = base_version(cl)

    # 1: the everyday shape validates clean.
    check("base-pass", run_gates(vbase, worklog, cl).status == PASS)

    # 1b: an INDEPENDENT GOLDEN freeze digest over a FIXED entry slice, computed EXTERNALLY (printf | sha256sum
    # over the exact bytes `## 1.0.0 (2026-06-01)\n\n- first release\n`), NOT by calling freeze_digest().
    # Catches a silent change to the digest scheme (hash algorithm, `sha256:` prefix, or the heading-to-EOF
    # byte selection): any such change makes the production freeze_of() diverge from this hand-computed value.
    GOLDEN_1_0_0 = "sha256:79f3fe923209c415fba0e58d3b1361606a80129a43b8c690261ec84d5253ac6e"
    check("golden-freeze-digest", freeze_of(cl, "1.0.0") == GOLDEN_1_0_0)

    # 1b-nonfinal (F4): an INDEPENDENT GOLDEN over a NON-FINAL entry's slice, computed EXTERNALLY
    # (printf '## 1.1.0 (2026-06-15)\n\n- second release\n\n' | sha256sum over the exact heading-through-
    # but-excluding-the-next-heading bytes, spec 7.2), NOT via freeze_digest(). This is the discriminating
    # oracle for the UPPER slice boundary: GOLDEN_1_0_0 covers the FINAL entry (heading to EOF), so a
    # slicer mutation `end = len(text)` survives it; this non-final golden fails under that mutation, since
    # freeze_of("1.1.0") would then absorb the 1.0.0 entry and diverge from the hand-computed value.
    GOLDEN_1_1_0 = "sha256:fc9c98279da30f5b84e24277ab6fbbef414fc84e4cdb1a321816ad776fe856d3"
    check("golden-freeze-digest-nonfinal", freeze_of(cl, "1.1.0") == GOLDEN_1_1_0)

    # 1c: a range over THREE releases must cover the INTERIOR member (1.1.0), not only its endpoints.
    # Catches a literal-endpoints tiling bug that would leave 1.1.0 uncovered (a false coverage gap).
    worklog3 = {"schema": 1, "entry": [entry(1), entry(2), entry(3), entry(4), entry(5), entry(6)]}
    by_id3, _ = _entries_by_id(worklog3)
    cl3 = ("# Changelog\n\nSummary.\n\n"
           "## unreleased\n\n- x\n\n"
           "## 1.0.0..1.2.0\n\n- rollup of three releases\n")
    v3 = {"schema": 1,
          "release": [
              {"version": "1.0.0", "date": "2026-06-01T00:00:00Z", "worklog_span": ["WL-1", "WL-2"],
               "coverage_digest": _opf_release.compute_span_digest(by_id3, (1, 2))},
              {"version": "1.1.0", "date": "2026-06-08T00:00:00Z", "worklog_span": ["WL-3", "WL-4"],
               "coverage_digest": _opf_release.compute_span_digest(by_id3, (3, 4))},
              {"version": "1.2.0", "date": "2026-06-15T00:00:00Z", "worklog_span": ["WL-5", "WL-6"],
               "coverage_digest": _opf_release.compute_span_digest(by_id3, (5, 6))}],
          "summary": [
              {"covers": "unreleased", "status": "working"},
              {"covers": "1.0.0..1.2.0", "status": "published", "digest": freeze_of(cl3, "1.0.0..1.2.0")}]}
    check("range-covers-interior-pass", run_gates(v3, worklog3, cl3).status == PASS)

    # 2: a coverage GAP (1.0.0 covered by no summary row).
    cl_gap = ("# Changelog\n\nSummary.\n\n"
              "## unreleased\n\n- x\n\n"
              "## 1.1.0 (2026-06-15)\n\n- second\n")
    v_gap = {"schema": 1, "release": vbase["release"],
             "summary": [{"covers": "unreleased", "status": "working"},
                         {"covers": "1.1.0", "status": "published", "digest": freeze_of(cl_gap, "1.1.0")}]}
    r_gap = run_gates(v_gap, worklog, cl_gap)
    check("coverage-gap-finding", r_gap.status == FINDING and any("gap" in f for f in r_gap.findings))

    # 3: a coverage OVERLAP (a range summary alongside the two singles, each with a matching heading).
    cl_ov = ("# Changelog\n\nSummary.\n\n"
             "## unreleased\n\n- x\n\n"
             "## 1.0.0..1.1.0\n\n- rollup\n\n"
             "## 1.1.0 (2026-06-15)\n\n- second\n\n"
             "## 1.0.0 (2026-06-01)\n\n- first\n")
    v_ov = {"schema": 1, "release": vbase["release"],
            "summary": [{"covers": "unreleased", "status": "working"},
                        {"covers": "1.0.0..1.1.0", "status": "published", "digest": freeze_of(cl_ov, "1.0.0..1.1.0")},
                        {"covers": "1.0.0", "status": "published", "digest": freeze_of(cl_ov, "1.0.0")},
                        {"covers": "1.1.0", "status": "published", "digest": freeze_of(cl_ov, "1.1.0")}]}
    r_ov = run_gates(v_ov, worklog, cl_ov)
    check("coverage-overlap-finding", r_ov.status == FINDING and any("overlap" in f for f in r_ov.findings))

    # 4: an UNMATCHED CHANGELOG heading (a heading with no summary row).
    cl_extra = cl.replace("## 1.0.0 (2026-06-01)\n\n- first release\n",
                          "## 1.0.0 (2026-06-01)\n\n- first release\n\n## 2.5.0\n\n- ghost\n")
    r_extra = run_gates(base_version(cl_extra), worklog, cl_extra)
    check("unmatched-heading-finding",
          r_extra.status == FINDING and any("2.5.0" in f for f in r_extra.findings))

    # 4b (M4): a descending-ORDER violation (released entries in ASCENDING order) is a finding, even though
    # the entries still tile the ledger and freeze clean. Catches removal of the entry-ordering gate.
    cl_order = ("# Changelog\n\nSummary.\n\n"
                "## unreleased\n\n- x\n\n"
                "## 1.0.0 (2026-06-01)\n\n- first\n\n"
                "## 1.1.0 (2026-06-15)\n\n- second\n")
    r_order = run_gates(base_version(cl_order), worklog, cl_order)
    check("entry-order-finding",
          r_order.status == FINDING and any("order" in f for f in r_order.findings))

    # 4c (M5): a valid token followed by junk (`## 1.0.0 trailing junk (2026-06-01)`) is a finding; without
    # the suffix grammar it clean-passes (the junk is silently dropped after the first word).
    cl_suffix = cl.replace("## 1.0.0 (2026-06-01)", "## 1.0.0 trailing junk (2026-06-01)")
    r_suffix = run_gates(base_version(cl_suffix), worklog, cl_suffix)
    check("heading-suffix-finding",
          r_suffix.status == FINDING and any("after the covers token" in f for f in r_suffix.findings))

    # F8: a multiline setext entry heading (token and date on separate lines) records a finding rather
    # than passing when the interior newline is stripped into a clean single-line suffix (the pre-fix
    # fail-open). A genuine single-line ATX entry heading raises no such finding.
    check("multiline-setext-heading-finding",
          any("multiline" in f for f in _changelog_entries("1.0.0\n(2026-01-01)\n---\n")[1]))
    check("single-line-heading-no-multiline-finding",
          not any("multiline" in f for f in _changelog_entries("## 1.0.0 (2026-01-01)\n\nbody\n")[1]))

    # 4d (M3): a worklog entry carrying a manifest-registered x-<vendor> extension VALIDATES only when the
    # registered set is threaded into run_gates -> validate_worklog; the same worklog with an empty set
    # fails closed (CANNOT-EVALUATE). Catches run_gates dropping registered_vendors on the floor.
    ev4 = entry(4)
    ev4["x-acme"] = {"note": "n"}
    worklog_vendor = {"schema": 1, "entry": [entry(1), entry(2), entry(3), ev4]}
    by_id_v, _ = _entries_by_id(worklog_vendor)
    v_vendor = {"schema": 1,
                "release": [
                    {"version": "1.0.0", "date": "2026-06-01T00:00:00Z", "worklog_span": ["WL-1", "WL-2"],
                     "coverage_digest": _opf_release.compute_span_digest(by_id_v, (1, 2))},
                    {"version": "1.1.0", "date": "2026-06-15T00:00:00Z", "worklog_span": ["WL-3", "WL-4"],
                     "coverage_digest": _opf_release.compute_span_digest(by_id_v, (3, 4))}],
                "summary": [
                    {"covers": "unreleased", "status": "working"},
                    {"covers": "1.0.0", "status": "published", "digest": freeze_of(cl, "1.0.0")},
                    {"covers": "1.1.0", "status": "published", "digest": freeze_of(cl, "1.1.0")}]}
    check("m3-registered-vendor-pass",
          run_gates(v_vendor, worklog_vendor, cl, registered_vendors=frozenset({"x-acme"})).status == PASS)
    check("m3-unregistered-vendor-cannot-eval",
          run_gates(v_vendor, worklog_vendor, cl).status == CANNOT_EVALUATE)

    # 5: a FREEZE-DIGEST MISMATCH (the 1.0.0 entry edited, its recorded digest left stale).
    cl_edit = cl.replace("- first release\n", "- first release, reworded\n")
    r_freeze = run_gates(base_version(cl), worklog, cl_edit)
    check("freeze-mismatch-finding",
          r_freeze.status == FINDING and any("freeze digest" in f for f in r_freeze.findings))

    # 6: a RE-PUBLISH that keeps covers + coverage digests stable (edit the prose AND the digest) -> PASS.
    check("republish-stable-pass", run_gates(base_version(cl_edit), worklog, cl_edit).status == PASS)

    # 6b: a change to the FACTS (a covered worklog entry edited) breaks the coverage digest -> FINDING.
    worklog_facts = {"schema": 1, "entry": [entry(1, summary="FACTS CHANGED"), entry(2), entry(3), entry(4)]}
    r_facts = run_gates(vbase, worklog_facts, cl)
    check("changed-facts-finding",
          r_facts.status == FINDING and any("coverage" in f for f in r_facts.findings))

    # 7: fail-closed on unreadable / inconsistent carriers.
    check("version-not-table-cannot-eval", run_gates([], worklog, cl).status == CANNOT_EVALUATE)
    check("worklog-not-table-cannot-eval", run_gates(vbase, [], cl).status == CANNOT_EVALUATE)
    v_inconsistent = {"schema": 1, "release": [
        {"version": "2.0.0", "date": "2026-06-01T00:00:00Z", "worklog_span": [], "coverage_digest": "sha256:" + "0" * 64},
        {"version": "1.0.0", "date": "2026-06-02T00:00:00Z", "worklog_span": [], "coverage_digest": "sha256:" + "0" * 64}]}
    check("inconsistent-ledger-cannot-eval", run_gates(v_inconsistent, worklog, cl).status == CANNOT_EVALUATE)

    # F2: a "## " heading INSIDE a fenced code block is not an entry. Pre-fix the fenced 1.0.0 heading is
    # counted and freezes clean (PASS); post-fix it is fenced content, so 1.0.0 has no matching heading and
    # no entry to freeze (FINDING). The digest is a DIRECT freeze_digest over the pre-fix entry byte-slice
    # (freeze_of cannot see the token post-fix).
    worklog_f2 = {"schema": 1, "entry": [entry(1), entry(2)]}
    by_id_f2, _ = _entries_by_id(worklog_f2)
    dig_f2 = _opf_release.compute_span_digest(by_id_f2, (1, 2))
    cl_fence = ("# Changelog\n\nSummary.\n\n"
                "## unreleased\n\n- x\n\n"
                "```\n"
                "## 1.0.0 (2026-06-01)\n\n- first\n"
                "```\n")
    fenced_1_0_0_bytes = "## 1.0.0 (2026-06-01)\n\n- first\n```\n"
    v_f2 = {"schema": 1,
            "release": [{"version": "1.0.0", "date": "2026-06-01T00:00:00Z",
                         "worklog_span": ["WL-1", "WL-2"], "coverage_digest": dig_f2}],
            "summary": [{"covers": "unreleased", "status": "working"},
                        {"covers": "1.0.0", "status": "published",
                         "digest": freeze_digest(fenced_1_0_0_bytes)}]}
    check("f2-fenced-code-heading-not-an-entry-finding",
          run_gates(v_f2, worklog_f2, cl_fence).status == FINDING)

    # F1: a 4-space-INDENTED fence run is INDENTED CODE, not a fence opener (CommonMark), so it cannot
    # hide a later UNINDENTED "## " entry heading. Post-fix the indented ``` opens no fence, so "## 9.9.9"
    # is a real, unmatched release heading (FINDING). Pre-fix the strip()-first form entered fence mode on
    # the indented backticks and suppressed "## 9.9.9", clean-passing. The recorded 1.0.0 digest is
    # freeze_of over the (post-fix) heading-to-"## 9.9.9" slice, so the freeze gate is clean and the sole
    # finding is the unmatched heading.
    worklog_if = {"schema": 1, "entry": [entry(1)]}
    by_id_if, _ = _entries_by_id(worklog_if)
    dig_if = _opf_release.compute_span_digest(by_id_if, (1, 1))
    cl_if = ("# Changelog\n\nSummary.\n\n"
             "## unreleased\n\n- x\n\n"
             "## 1.0.0 (2026-06-01)\n\n- real release\n\n"
             "    ```\n"
             "## 9.9.9\n\n- unmatched real level-2 heading\n")
    v_if = {"schema": 1,
            "release": [{"version": "1.0.0", "date": "2026-06-01T00:00:00Z",
                         "worklog_span": ["WL-1", "WL-1"], "coverage_digest": dig_if}],
            "summary": [{"covers": "unreleased", "status": "working"},
                        {"covers": "1.0.0", "status": "published",
                         "digest": freeze_of(cl_if, "1.0.0")}]}
    r_if = run_gates(v_if, worklog_if, cl_if)
    check("f1-indented-fence-not-a-fence-unmatched-heading-finding",
          r_if.status == FINDING and any("9.9.9" in f for f in r_if.findings))

    # F3-sub: freeze_digest normalizes line endings, so a direct CRLF caller matches the LF parse path.
    # Pre-fix the CRLF bytes are hashed raw and diverge. (entry_lf is exactly the GOLDEN_1_0_0 slice.)
    entry_lf = "## 1.0.0 (2026-06-01)\n\n- first release\n"
    check("f3sub-freeze-digest-normalizes-crlf",
          freeze_digest(entry_lf.replace("\n", "\r\n")) == freeze_digest(entry_lf))
    check("f3sub-freeze-digest-matches-golden", freeze_digest(entry_lf) == GOLDEN_1_0_0)

    # F4: a released summary row left status "working" (no freeze digest) must be a FINDING; pre-fix it
    # tiles coverage and the freeze gate skips it, so it clean-passes. (U3 permits working on a released row.)
    v_f4 = {"schema": 1, "release": vbase["release"],
            "summary": [{"covers": "unreleased", "status": "working"},
                        {"covers": "1.0.0", "status": "working"},
                        {"covers": "1.1.0", "status": "published", "digest": freeze_of(cl, "1.1.0")}]}
    r_f4 = run_gates(v_f4, worklog, cl)
    check("f4-released-working-summary-finding",
          r_f4.status == FINDING and any("must be published" in f for f in r_f4.findings))

    # F8: a calendar-invalid heading date (2026-99-99) must be a FINDING; pre-fix the count-only regex
    # accepts 99 as a month/day and clean-passes.
    cl_f8 = cl.replace("## 1.0.0 (2026-06-01)", "## 1.0.0 (2026-99-99)")
    r_f8 = run_gates(base_version(cl_f8), worklog, cl_f8)
    check("f8-calendar-invalid-date-finding",
          r_f8.status == FINDING and any("after the covers token" in f for f in r_f8.findings))

    # F5: spec 6.3 permits a parenthesized list of ONE OR MORE dates after the covers token; the round-2
    # grammar capped it at two. A three-date suffix must PASS post-fix; pre-fix the `?` (0-or-1) repetition
    # rejected the third date as "text after the covers token", a false positive.
    cl_f5 = cl.replace("## 1.0.0 (2026-06-01)", "## 1.0.0 (2026-06-01, 2026-06-02, 2026-06-03)")
    check("f5-multi-date-suffix-pass", run_gates(base_version(cl_f5), worklog, cl_f5).status == PASS)

    # F7: the CLI arg parser is fail-closed. In-process (no subprocess): a missing --root value or an
    # unknown arg is a usage error, --root with a value is accepted, and --self-test is honoured only alone.
    check("f7-root-missing-value-usage-error", _parse_cli(["--root"])[2] is not None)
    check("f7-unknown-arg-usage-error", _parse_cli(["--frobnicate"])[2] is not None)
    check("f7-root-with-value-ok", _parse_cli(["--root", "/some/path"]) == ("run", "/some/path", None))
    check("f7-self-test-sole-mode", _parse_cli(["--self-test"]) == ("self-test", None, None))
    check("f7-self-test-not-sole-usage-error", _parse_cli(["--self-test", "--root", "x"])[2] is not None)
    check("f3-root-empty-value-usage-error", _parse_cli(["--root", ""])[2] is not None)
    check("f3-root-blank-value-usage-error", _parse_cli(["--root", "   "])[2] is not None)

    # F3 (Fable/self-test-blind): a '## unreleased' heading with NO covers="unreleased" summary row is a
    # FINDING (spec 7.1). This branch is the sole layer; deleting it yields a silent fail-open.
    cl_f3 = ("# Changelog\n\n## unreleased\n\n- wip\n\n"
             "## 1.1.0 (2026-06-15)\n\n- second\n\n## 1.0.0 (2026-06-01)\n\n- first\n")
    v_f3 = dict(schema=1, release=vbase["release"],
                summary=[dict(covers="1.0.0", status="published", digest=freeze_of(cl_f3, "1.0.0")),
                         dict(covers="1.1.0", status="published", digest=freeze_of(cl_f3, "1.1.0"))])
    r_f3 = run_gates(v_f3, worklog, cl_f3)
    check("f3-unreleased-heading-no-row-finding",
          r_f3.status == FINDING and any("has an '## unreleased' heading but" in f for f in r_f3.findings))

    # F4 (Fable/self-test-blind): a misplaced '## unreleased' entry (not first) is a FINDING (spec 6.3),
    # even when the released headings alone are correctly descending. The sole ordering vector (cl_order)
    # puts unreleased first, so it never exercises this branch.
    cl_f4 = ("# Changelog\n\n## 1.1.0 (2026-06-15)\n\n- second\n\n## unreleased\n\n- wip\n\n"
             "## 1.0.0 (2026-06-01)\n\n- first\n")
    r_f4 = run_gates(base_version(cl_f4), worklog, cl_f4)
    check("f4-unreleased-not-first-finding",
          r_f4.status == FINDING and any("is not first" in f for f in r_f4.findings))

    # F6 (Fable): a changelog str carrying a lone surrogate is not encodable as UTF-8; run_gates returns a
    # distinct CANNOT-EVALUATE rather than letting the scanner's pre-parse encode() escape.
    check("f6-surrogate-changelog-cannot-eval",
          run_gates(vbase, worklog, cl + chr(0xD800)).status == CANNOT_EVALUATE)

    # codex slice-2: an unresolvable --root (os.path.abspath raising, as when the process cwd was removed)
    # is a fail-closed CANNOT-EVALUATE, not an escaping crash. Inject the raise (matching the M1 style).
    saved_abspath = os.path.abspath
    def _raise_abspath(*_a, **_k):
        raise FileNotFoundError(2, "simulated unresolvable cwd")
    os.path.abspath = _raise_abspath
    try:
        abs_ok = evaluate(".").status == CANNOT_EVALUATE
    except OSError:
        abs_ok = False                    # pre-fix: the OSError escaped evaluate uncaught
    finally:
        os.path.abspath = saved_abspath
    check("abspath-unresolvable-root-cannot-eval", abs_ok)

    # --- end-to-end store resolution over synthetic on-disk stores -----------------------------------
    manifest = ("[devprocess]\n"
                'standard = "devprocess"\n'
                'spec_version = "1.0.0"\n'
                'layout = "inline"\n'
                'posture = "required"\n'
                'import_status = "none"\n\n'
                "[types.worklog]\n"
                'namespace = "WL"\n')
    cl_disk = ("# Changelog\n\nA curated summary of the worklog.\n\n"
               "## unreleased\n\n- in progress\n\n"
               "## 1.0.0 (2026-06-01)\n\n- first release\n")
    wl_disk = {"schema": 1, "entry": [entry(1)]}
    by_id_disk, _ = _entries_by_id(wl_disk)
    dig11 = _opf_release.compute_span_digest(by_id_disk, (1, 1))
    version_text = ("schema = 1\n\n"
                    "[[release]]\n"
                    'version = "1.0.0"\n'
                    'date = "2026-06-01T00:00:00Z"\n'
                    'worklog_span = ["WL-1", "WL-1"]\n'
                    'coverage_digest = "{}"\n\n'.format(dig11) +
                    "[[summary]]\n"
                    'covers = "unreleased"\n'
                    'status = "working"\n\n'
                    "[[summary]]\n"
                    'covers = "1.0.0"\n'
                    'status = "published"\n'
                    'digest = "{}"\n'.format(freeze_of(cl_disk, "1.0.0")))
    worklog_text = ("schema = 1\n\n"
                    "[[entry]]\n"
                    'id = "WL-1"\n'
                    'date = "{}"\n'.format(entry(1)["date"]) +
                    'kind = "added"\n'
                    'summary = "s"\n\n'
                    "[entry.actor]\n"
                    'kind = "maintainer"\n')

    base_dir = Path(tempfile.mkdtemp(prefix="opf-changelog-selftest-")).resolve()
    counter = [0]

    def build_store(version_src, worklog_src, changelog_src, manifest_src=manifest):
        counter[0] += 1
        root = base_dir / "case-{:02d}".format(counter[0])
        machine = root / _opf_store.WORKING_DIRNAME / _opf_store.DEFAULT_MACHINE_SUBDIR
        machine.mkdir(parents=True)
        (machine / _opf_store.MANIFEST_NAME).write_text(manifest_src, encoding="utf-8")
        (machine / "version.toml").write_text(version_src, encoding="utf-8")
        (machine / "worklog.toml").write_text(worklog_src, encoding="utf-8")
        if changelog_src is not None:
            (root / CHANGELOG_REL).write_text(changelog_src, encoding="utf-8")
        return root

    try:
        # NOT a DevProcess adopter (no .working, no pointer) -> NOT APPLICABLE.
        na_root = base_dir / "not-adopted"
        na_root.mkdir()
        check("disk-not-applicable", evaluate(na_root).status == NOT_APPLICABLE)

        # A fully valid triad resolves and passes end-to-end.
        check("disk-pass", evaluate(build_store(version_text, worklog_text, cl_disk)).status == PASS)

        # An absent CHANGELOG.md is a required-input fail-closed cannot-evaluate.
        check("disk-missing-changelog-cannot-eval",
              evaluate(build_store(version_text, worklog_text, None)).status == CANNOT_EVALUATE)

        # An unparseable version.toml is fail-closed cannot-evaluate.
        check("disk-unparseable-version-cannot-eval",
              evaluate(build_store("this is [[ not valid toml", worklog_text, cl_disk)).status
              == CANNOT_EVALUATE)

        # A valid triad whose published entry was edited without a re-publication is a FINDING.
        cl_disk_edit = cl_disk.replace("- first release\n", "- first release EDITED\n")
        check("disk-freeze-finding",
              evaluate(build_store(version_text, worklog_text, cl_disk_edit)).status == FINDING)

        # F2 (Fable): run() maps the store-level status to the process exit code through EXIT; the suite
        # otherwise judges evaluate()/run_gates statuses and never the process-boundary verdict, so a
        # mutation like EXIT[FINDING]->0 passes unseen. Exercise run() end to end on a PASS and a FINDING
        # store, and pin the EXIT map itself.
        check("f2-run-pass-exit-0", run(str(build_store(version_text, worklog_text, cl_disk))) == 0)
        check("f2-run-finding-exit-1", run(str(build_store(version_text, worklog_text, cl_disk_edit))) == 1)
        check("f2-exit-map-values",
              EXIT[PASS] == 0 and EXIT[NOT_APPLICABLE] == 0 and EXIT[FINDING] == 1
              and EXIT[CANNOT_EVALUATE] == 2)

        # F5 (Fable/codex): the non-UTF-8 CHANGELOG.md handler (a distinct CANNOT-EVALUATE) had no
        # discriminating vector because build_store writes utf-8 text; write raw invalid bytes directly.
        # Deleting the UnicodeDecodeError handler in _load_inputs makes this escape as a crash.
        nonutf8_root = build_store(version_text, worklog_text, cl_disk)
        (nonutf8_root / CHANGELOG_REL).write_bytes(bytes([0x23, 0x0a, 0xff, 0xfe, 0x0a]))
        check("disk-non-utf8-changelog-cannot-eval", evaluate(nonutf8_root).status == CANNOT_EVALUATE)

        # F1 (Fable/codex, BLOCKER): a FIFO at CHANGELOG.md must fail closed as a non-regular input, never
        # block. _load_inputs lstat-checks S_ISREG BEFORE opening (mirroring _read_toml_contained), so a
        # reader-only FIFO can never hang. A SIGALRM watchdog bounds a pre-fix regression (which blocks in
        # os.open) so the suite fails fast; post-fix the guard returns a "not a regular file" cannot-evaluate
        # well within it, and the alarm never fires.
        import signal as _signal
        fifo_root = build_store(version_text, worklog_text, None)
        os.mkfifo(str(fifo_root / CHANGELOG_REL))
        def _fifo_watchdog(_signum, _frame):
            raise TimeoutError("evaluate() blocked on the FIFO changelog (pre-fix hang)")
        _old_alarm = _signal.signal(_signal.SIGALRM, _fifo_watchdog)
        _signal.alarm(5)
        try:
            r_fifo = evaluate(fifo_root)
        finally:
            _signal.alarm(0)
            _signal.signal(_signal.SIGALRM, _old_alarm)
        check("f1-fifo-changelog-not-regular-fail-closed",
              r_fifo.status == CANNOT_EVALUATE and any("not a regular file" in f for f in r_fifo.findings))

        # F5: a manifest that omits the required [types] section must fail the FULL manifest validator and
        # come back CANNOT-EVALUATE. Pre-fix only [vendors] was checked, so this clean-passed.
        manifest_no_types = ("[devprocess]\n"
                             'standard = "devprocess"\n'
                             'spec_version = "1.0.0"\n'
                             'layout = "inline"\n'
                             'posture = "required"\n'
                             'import_status = "none"\n')
        check("f5-manifest-missing-types-cannot-eval",
              evaluate(build_store(version_text, worklog_text, cl_disk, manifest_no_types)).status
              == CANNOT_EVALUATE)

        # M3 end-to-end: a store whose MANIFEST registers x-acme and whose worklog USES x-acme resolves
        # and PASSES only because _load_inputs reads the manifest and threads [vendors].registered through
        # evaluate -> run_gates -> validate_worklog. If that plumbing is absent, validate_worklog rejects
        # the x-acme entry and evaluate returns CANNOT-EVALUATE, so this positive vector discriminates it.
        ev_disk = entry(1)
        ev_disk["x-acme"] = {"note": "n"}
        wl_vendor = {"schema": 1, "entry": [ev_disk]}
        by_id_wv, _ = _entries_by_id(wl_vendor)
        dig11v = _opf_release.compute_span_digest(by_id_wv, (1, 1))
        version_vendor_text = ("schema = 1\n\n"
                               "[[release]]\n"
                               'version = "1.0.0"\n'
                               'date = "2026-06-01T00:00:00Z"\n'
                               'worklog_span = ["WL-1", "WL-1"]\n'
                               'coverage_digest = "{}"\n\n'.format(dig11v) +
                               "[[summary]]\n"
                               'covers = "unreleased"\n'
                               'status = "working"\n\n'
                               "[[summary]]\n"
                               'covers = "1.0.0"\n'
                               'status = "published"\n'
                               'digest = "{}"\n'.format(freeze_of(cl_disk, "1.0.0")))
        worklog_vendor_text = worklog_text + '\n[entry."x-acme"]\nnote = "n"\n'
        manifest_vendor = manifest + '\n[vendors]\nregistered = ["x-acme"]\n'
        check("disk-m3-registered-vendor-pass",
              evaluate(build_store(version_vendor_text, worklog_vendor_text, cl_disk,
                                   manifest_vendor)).status == PASS)

        # M1 end-to-end: a raw OSError raised by the contained ledger read must be caught in _load_inputs
        # and returned as a fail-closed cannot-evaluate MESSAGE, never propagated. Inject one after a store
        # has resolved (resolution already read its manifest, so resolve_store is unaffected). With the fix
        # (`except (StoreError, OSError)`) m1_err is a message; without it the OSError escapes _load_inputs.
        m1_root = build_store(version_text, worklog_text, cl_disk)
        res_m1 = _opf_store.resolve_store(m1_root)
        saved_reader = _opf_store._read_toml_contained

        def _raise_oserror(*_a, **_k):
            raise PermissionError(13, "simulated unreadable ledger")

        _opf_store._read_toml_contained = _raise_oserror
        try:
            _, _, _, _, m1_err = _load_inputs(res_m1, m1_root)
            m1_ok = m1_err is not None
        except OSError:
            m1_ok = False                 # M1 unfixed: the OSError escaped _load_inputs uncaught
        finally:
            _opf_store._read_toml_contained = saved_reader
        check("m1-unreadable-ledger-oserror-cannot-eval", m1_ok)
    finally:
        shutil.rmtree(base_dir, ignore_errors=True)

    if failures:
        print("OPF-CHANGELOG SELF-TEST: FAIL ({} of {} checks failed)".format(len(failures), checked))
        for f in failures:
            print("  FAILED: {}".format(f))
        return 1
    print("OPF-CHANGELOG SELF-TEST: PASS ({} range-coverage, freeze, and store-resolution checks)".format(
        checked))
    return 0


def _parse_cli(args):
    """Parse the module CLI, fail-closed. Returns (mode, root, error): `mode` is "self-test" or "run";
    on "run" `root` is the --root value (default "."); `error` is None on success, or a usage-error
    message string that the caller treats as cannot-evaluate. A missing, empty, or whitespace-only --root value, an unknown argument,
    or --self-test combined with anything else is a usage error (fail-closed control, guard-input-soundness);
    --self-test is honoured only as the sole argument."""
    if args == ["--self-test"]:
        return "self-test", None, None
    root = "."
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--root":
            if i + 1 >= len(args):
                return None, None, "--root requires a value"
            root = args[i + 1]
            if not root.strip():          # an empty or whitespace-only root names no target: fail closed
                return None, None, "--root requires a non-empty value"   # (guard-input-soundness)
            i += 2
            continue
        return None, None, "unknown argument {!r}".format(arg)
    return "run", root, None


def main():
    mode, root, error = _parse_cli(sys.argv[1:])
    if error is not None:
        print("OPF changelog gates: {} (fail-closed)".format(error), file=sys.stderr)
        return EXIT[CANNOT_EVALUATE]
    if mode == "self-test":
        return self_test()
    return run(root)


if __name__ == "__main__":
    sys.exit(main())
