#!/usr/bin/env python3
"""OPFiles (OPF) changelog ABSORB drafter: the machine-DRAFTING side of the curated changelog model.

Offline, stdlib only, fail-closed, READ-ONLY. This unit is the assistance half of spec 6.3/7.3: the
changelog is "machine-drafted and human-curated ... not a deterministic render, and not byte-drift-gated",
and its GATING half already ships in `_opf_changelog` (U5: range coverage + freeze). This drafter emits ONE
candidate CHANGELOG.md entry for a declared range so a human can curate it into `CHANGELOG.md`; it is NOT a
spec-10.1 view, carries no do-not-edit header, is never drift-compared against `CHANGELOG.md`, and writes
NOTHING (workers-produce-inert-data; SECI preview-has-no-side-effects; spec 7.3 "drafting is assistance,
not authority").

  opf absorb [--root DIR] [--covers TOKEN]                 draft one candidate entry to STDOUT
  opf absorb [--root DIR]  --covers TOKEN --freeze-digest  print the freeze digest of the CURATED entry

The draft bytes go to STDOUT alone; the banner, notes, and cannot-evaluate diagnostics go to STDERR, so
`opf absorb ... > candidate.md` yields exactly the entry (heading through end). `--covers` defaults to
`unreleased`; a single version token drafts that release's per-span entry; a range token `a..b` drafts a
ROLLUP from the EXISTING per-release CHANGELOG entries in range (spec 6.4, never the raw worklog wholesale).

COMPOSITION, not duplication (the pattern every OPF unit follows: one module, one self_test, one _parse_cli,
one leg in opf.py). This drafter REUSES rather than re-implements:
  - U5 (`_opf_changelog`): `_load_inputs` (the hardened contained reader for version/worklog/manifest/
    CHANGELOG.md, with the pre-open FIFO/oversize/utf-8 guards), `_changelog_entries` (the vendored-Marko
    fail-closed heading scanner), `freeze_digest`, and `CHANGELOG_REL`. The parsing, digest scheme, and gate
    logic stay in U5; this unit never re-derives them.
  - U3 (`_opf_release`): `validate_version` / `validate_worklog`, `_entries_by_id`, `_parse_covers` /
    `_covers_range` (the same covers grammar the gates enforce, so a draft heading always parses under the
    gate), `_parse_span`, `released_end`, `tail_ids`, `_wl_num`, `UNRELEASED`.
  - U2 (`_opf_schema`): `validate_record` for `done` receipts, `_valid_id_shape`, `SUPPORTED_SCHEMA`.
  - U1 (`_opf_store`): store resolution, the contained TOML reader, and the section-8.1 namespace bindings.

FAIL-CLOSED everywhere (spec 3; check-fails-closed-on-unreadable; guard-input-soundness): an unresolvable
store, an unreadable/unparseable/inconsistent ledger, a malformed manifest, or an unreadable CHANGELOG.md is
a distinct CANNOT-EVALUATE (exit 2) that STOPS, never a silent partial draft. BOTH modes first run the
SHARED ledger-consistency floor: they resolve the store, read the inputs, and validate the version + worklog
ledgers and the covers token against the ledger (`_validated_inputs`), so `--freeze-digest` never digests
past a broken ledger or a covers token that fails to parse or resolve against the ledger. Wherever the curated CHANGELOG entries are consumed (a
range rollup, or the freeze digest of an entry), a CHANGELOG.md whose entry headings do not all parse is
CANNOT-EVALUATE too: U5's malformed-heading findings are propagated, never discarded. Two further checks are
DRAFT-specific, because they guard the worklog->draft synthesis that `--freeze-digest` does not run: a
`[types.done]`-declared but absent/malformed `done.index.toml` (the done-receipt join), and a covers token
that names a rotated span (the worklog entries a per-release draft would compose). A non-adopter root is
NOT-APPLICABLE (exit 0), exactly like `_opf_changelog` / doctor; this pack's own `--root .` lands there.

The done join (a tool convention, spec 6.3 fixes only the heading grammar; disclose-guard-residuals):
`done` completion receipts ENRICH the draft, they are NOT a gated set (the ledger+worklog is the
authoritative index of releasable change). A span worklog entry that links a `done` record's own `DN-<n>`
id gets that receipt's completion framing on its bullet ("closes BI-<m>"). A `done` receipt whose
`receipt_of` backlog item is referenced by the span's worklog entries but whose `DN-<n>` id NO span entry
links is surfaced as a STDERR draft-note (a visible prompt that a worklog row may be missing), never a
finding and never silently absorbed.

DETERMINISM is a testing property, not a spec claim (presenting the draft as a 10.1 view would recreate the
retired deterministic model): stable WL-id ordering, kind-grouped bullets, and dates taken only from ledger
rows (never the clock), so the self-test can golden the output; the standard does not rely on it, and adopters
may draft otherwise. Adopter-rooted like the rest of the tooling; the assurance rides the `--self-test` leg
over synthetic stores, reached through `opf/tools/opf.py --self-test` as the `opf-absorb` leg.
"""
import html
import os
import re
import string
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _commonmark_headings  # noqa: E402  the HeadingScanError type raised by U5's _changelog_entries
import _journal              # noqa: E402  containment probe (the self-test builds on-disk stores)
import _opf_store            # noqa: E402  U1: store resolution + contained reader + section-8.1 namespaces
import _opf_schema           # noqa: E402  U2: validate_record (done) + _valid_id_shape + SUPPORTED_SCHEMA
import _opf_release          # noqa: E402  U3: ledger validators + covers/span parsing + tail/id helpers
import _opf_changelog        # noqa: E402  U5: _load_inputs + _changelog_entries + freeze_digest + CHANGELOG_REL
from _opf_store import VALID, CANNOT_EVALUATE, RESOLVED, NOT_ADOPTED  # noqa: E402
from _opf_release import (  # noqa: E402
    validate_version, validate_worklog, _entries_by_id, _parse_covers,
    _parse_span, tail_ids, _wl_num, UNRELEASED, ReleaseError,
)

# Section-8.1 namespaces this drafter reasons about (the single source-of-truth binding lives in U1).
_BI_NS = _opf_store.BASELINE_TYPES["backlog_item"]   # "BI"
_DN_NS = _opf_store.BASELINE_TYPES["done"]            # "DN"

# Kind -> changelog section, in the spec-6.2 WORKLOG_KINDS order. A kind outside this map (only reachable
# through a manifest-registered kind, which the default worklog validation this unit runs would already
# reject) is grouped under its capitalized name, appended after the known sections, so the drafter never
# silently drops an entry it could not place.
_KIND_ORDER = ("added", "changed", "fixed", "removed", "security", "docs", "infra")
_KIND_TITLES = {"added": "Added", "changed": "Changed", "fixed": "Fixed", "removed": "Removed",
                "security": "Security", "docs": "Docs", "infra": "Infra"}

# The most missing WL-ids a rotated-span refusal names outright; the remainder is reported as a count, so
# the refusal stays bounded however wide the ledger span's numeric interval is (SECA bounded consumption).
_MISSING_ID_SAMPLE = 20

# The underline of a level-2 CommonMark setext heading: up to three leading spaces, one or more '-', then
# only whitespace. Used to cut a setext entry heading (title line(s) + underline) out of a rollup source.
_SETEXT_UNDERLINE_RE = re.compile(r"^ {0,3}-+[ \t]*$")

# Drafter outcomes (the doctor.py / _opf_changelog PASS/NA/cannot-evaluate idiom, named for this drafter).
OK = "OK"                                  # a candidate draft (or a freeze digest) was produced -> exit 0
NOT_APPLICABLE = "NOT-APPLICABLE"          # not an OPFiles adopter -> degrade, never fake output (exit 0)
# CANNOT_EVALUATE (imported) -> exit 2: unreadable / unparseable / unresolvable / inconsistent / rotated.

EXIT = {OK: 0, NOT_APPLICABLE: 0, CANNOT_EVALUATE: 2}


class AbsorbResult:
    __slots__ = ("status", "draft", "notes", "findings")

    def __init__(self, status, draft=None, notes=None, findings=None):
        self.status = status              # OK / NOT_APPLICABLE / CANNOT_EVALUATE
        self.draft = draft                # the candidate entry bytes (str) / the freeze digest, on OK
        self.notes = notes or []          # informational STDERR draft-notes (never a finding)
        self.findings = findings or []    # NOT-APPLICABLE / cannot-evaluate diagnostics


# --- draft rendering (a tool convention; spec 6.3 fixes only the heading grammar) --------------------

def _receipt_bi(record):
    """The `receipt_of` backlog-item id of a `done` record, or None. validate_record has already confirmed
    at most one such link and that it points to the BI namespace, so the first is authoritative."""
    for link in (record.get("links") or []):
        if isinstance(link, dict) and link.get("rel") == "receipt_of":
            return link.get("id")
    return None


def _enrichment(span_entries, done_records):
    """Join `done` receipts onto the span. Returns (per_entry_tokens, notes): `per_entry_tokens` maps a
    span entry's WL-number to the sorted "closes" tokens its linked receipts contribute; `notes` names each
    receipt whose backlog item the span references but whose own DN-id no span entry links. `done_records`
    is the validated receipt list, or None when the `done` type is not enabled (no join, no notes)."""
    if not done_records:
        return {}, []
    entry_links = {}                      # WL-number -> set of linked ids (any namespace)
    span_link_ids = set()
    for entry in span_entries:
        n = _wl_num(entry.get("id"))
        ids = {link.get("id") for link in (entry.get("links") or [])
               if isinstance(link, dict) and isinstance(link.get("id"), str)}
        entry_links[n] = ids
        span_link_ids |= ids
    span_bis = {i for i in span_link_ids
                if (_opf_schema._valid_id_shape(i) or (None,))[0] == _BI_NS}
    per_entry = {}
    notes = []
    for rec in done_records:
        dn = rec.get("id")
        bi = _receipt_bi(rec)
        token = bi if bi else dn          # a standalone importer receipt with no BI falls back to its DN
        if dn in span_link_ids:
            for n, ids in entry_links.items():
                if dn in ids:
                    per_entry.setdefault(n, []).append(token)
        elif bi is not None and bi in span_bis:
            notes.append("done receipt {} closes {}, which this span's worklog references, but no worklog "
                         "entry in this span links the receipt directly (a worklog row for the completion "
                         "may be missing)".format(dn, bi))
    for n in per_entry:
        per_entry[n] = sorted(set(per_entry[n]))
    return per_entry, sorted(set(notes))


# --- markdown-safe summary emission (a summary must render as literal inline bullet text) -------------
# A worklog summary is validated single-line prose (validate_worklog VALID), but it is otherwise free text a
# maintainer wrote, so it may BEGIN a CommonMark block construct (a nested list, heading, fenced or indented
# code block, blockquote, HTML block, thematic break) or, worst, a link-reference definition that consumes
# the whole bullet and can swallow the following bullet with it, dropping the summary. Which constructs a
# given summary triggers cannot be decided by a regex over its leading token: an escaped-bracket link label
# still defines a reference, a mid-line HTML comment still renders invisibly, an autolink is a legitimate
# inline construct. So the emission is RENDER-VERIFIED. Three escape candidates, least-escaped first, are
# each rendered through the vendored Marko, and the first whose drafted bullet ROUND-TRIPS is emitted: the
# summary renders as the LITERAL text the maintainer authored, in a single inline run of its own <li>, a
# sibling of a trailing sentinel bullet that must survive. Emission is NOT total: it FAILS CLOSED (raising
# _UnsafeSummary, which draft_entry turns into a CANNOT_EVALUATE naming the WL-id) rather than emit an
# unverified value. When the vendored Marko is available and NO candidate round-trips (a summary carrying a
# character CommonMark cannot render as literal text, such as a NUL that CommonMark replaces with U+FFFD), it
# fails closed instead of emitting the corrupted bullet. When Marko is UNAVAILABLE it cannot render, so the
# full escape is trusted only for characters the CommonMark backslash-escape guarantee covers (ASCII
# punctuation and ordinary text); a summary carrying a C0 control or DEL, which that guarantee does not cover,
# fails closed rather than emit an unverified bullet. A blank/whitespace-only summary (rejected upstream;
# defensive here) likewise fails closed rather than return raw whitespace. Leading/trailing whitespace,
# invisible in inline rendering, is stripped: the literal contract is over the summary's visible text
# (disclose-guard-residuals).
_ASCII_PUNCT = frozenset(string.punctuation)   # the 32 CommonMark ASCII-punctuation chars, each backslash-escapable

# The characters the full escape CANNOT make literal on its own: the CommonMark backslash-escape guarantee
# covers ASCII punctuation and ordinary text, not the C0 control range (U+0000-U+001F, of which CommonMark
# MUST replace U+0000 with U+FFFD) or DEL (U+007F). When Marko is unavailable the emission cannot render to
# check, so a summary carrying any of these fails closed rather than trust the full escape over a character
# outside the guarantee. (The line-break C0 controls are already rejected upstream by the single-line
# summary rule; this set is the defensive, renderer-absent floor.)
_MD_UNRENDERABLE = frozenset([chr(c) for c in range(0x00, 0x20)] + [chr(0x7f)])


class _UnsafeSummary(Exception):
    """A worklog summary that cannot be emitted as a literal inline bullet: no escape candidate round-trips
    through the vendored Marko (a character CommonMark cannot render as literal text, such as a NUL), or, with
    Marko unavailable, it carries a control character the full escape cannot make literal, or it is blank.
    Raised by `_md_safe_summary`/`_bullet`, propagated through `_render_span_body`/`_span_draft`, and turned
    into a CANNOT_EVALUATE draft naming the offending WL-id by `draft_entry`, so the drafter NEVER emits a
    corrupted or block-opening bullet (fail-closed; guard-input-soundness; no-concealed-failure)."""

    def __init__(self, reason, wl_id=None):
        self.reason = reason
        self.wl_id = wl_id
        super().__init__(reason if wl_id is None else "{}: {}".format(wl_id, reason))


# The leading block-significant token a minimal (candidate-1) escape neutralizes. This tier is a PREFERENCE
# only: whatever it produces is emitted solely when the render-verification confirms it round-trips, so an
# imperfect match here never emits an unsafe bullet, it only forgoes the cleaner minimal escape and lets the
# full-escape candidate take over.
_MD_ATX_RE = re.compile(r"#{1,6}([ \t]|$)")                   # ATX heading: 1-6 '#' then space/tab/eol
_MD_FENCE_RE = re.compile(r"(`{3,}|~{3,})")                   # fenced code: 3+ backticks or tildes
_MD_BULLET_RE = re.compile(r"[-*+]([ \t]|$)")                 # bullet-list marker (a nested list)
_MD_ORDERED_RE = re.compile(r"\d{1,9}([.)])([ \t]|$)")        # ordered-list marker: digits then '.'/')'
_MD_TBREAK_RE = re.compile(r"([-*_])[ \t]*(?:\1[ \t]*){2,}$")  # thematic break: 3+ of -, *, or _
_MD_HTML_RE = re.compile(r"<[a-zA-Z/!?]")                     # HTML block start: '<tag', '</', '<!', '<?'
_MD_LINKREF_RE = re.compile(r"\[[^\]\n]*\]:")                 # link-reference definition: [label]:

# The sentinel bullet the round-trip probe appends: a plain literal whose own <li> must survive as a sibling
# of the summary's, proving the summary neither opened a block nor swallowed the bullet that follows it.
_MD_SENTINEL = "- OPF_ABSORB_RENDER_SENTINEL"
_MD_RT_PRE = "<ul>\n<li>"
_MD_RT_POST = "</li>\n<li>OPF_ABSORB_RENDER_SENTINEL</li>\n</ul>\n"


def _md_minimal_escape(stripped):
    """Candidate 1: backslash-escape only the leading block-significant token of `stripped` (the token the
    earlier selective escape targeted). A preference tier, emitted only if the render-verification passes."""
    esc_at = None
    c = stripped[0]
    if c == "#" and _MD_ATX_RE.match(stripped):
        esc_at = 0
    elif c in "`~" and _MD_FENCE_RE.match(stripped):
        esc_at = 0
    elif c == ">":                            # a blockquote marker (the space after '>' is optional)
        esc_at = 0
    elif c == "<" and _MD_HTML_RE.match(stripped):
        esc_at = 0
    elif c == "[" and _MD_LINKREF_RE.match(stripped):
        esc_at = 0
    elif c in "-*+" and (_MD_BULLET_RE.match(stripped) or _MD_TBREAK_RE.match(stripped)):
        esc_at = 0
    elif c == "_" and _MD_TBREAK_RE.match(stripped):
        esc_at = 0
    elif c.isdigit():
        m = _MD_ORDERED_RE.match(stripped)
        if m:
            esc_at = m.start(1)               # escape the '.'/')' so the digits are not an ordered marker
    if esc_at is None:
        return stripped
    return stripped[:esc_at] + "\\" + stripped[esc_at:]


def _md_full_escape(stripped):
    """Candidate 2: backslash-escape EVERY ASCII-punctuation character. CommonMark guarantees each renders as
    its literal self, so this candidate round-trips for ASCII punctuation and ordinary text; it is NOT total,
    because that guarantee does not extend to a control character CommonMark replaces or drops (a NUL becomes
    U+FFFD), which the escape leaves untouched and cannot make literal."""
    return "".join("\\" + ch if ch in _ASCII_PUNCT else ch for ch in stripped)


def _md_bullet_round_trips(marko, candidate, target, suffix):
    """True when the drafted bullet `- <candidate><suffix>`, followed by the sentinel bullet, renders through
    the vendored `marko` as exactly two sibling <li> of one <ul>: the first the LITERAL `target + suffix` in a
    single inline run (no block opened, no tag emitted, no newline), the second the surviving sentinel.
    `target` is the intended literal (the stripped summary); `candidate` is its escaped form. Judged on the
    RENDERED HTML, not on the escape logic: the render is the authority for whether the summary is literal,
    not the regexes that produced the candidate (guard-input-soundness; isolate-verifiers by result signal)."""
    try:
        out = marko.convert("- " + candidate + suffix + "\n" + _MD_SENTINEL + "\n")
    except Exception:
        return False                          # a parser hiccup on this candidate: treat as not round-tripping
    if not (out.startswith(_MD_RT_PRE) and out.endswith(_MD_RT_POST)):
        return False                          # a block opened, or the sentinel did not survive as a sibling
    inner = out[len(_MD_RT_PRE):len(out) - len(_MD_RT_POST)]
    if "<" in inner or ">" in inner or "\n" in inner:
        return False                          # a literal text run escapes '<'/'>'; a raw one means a tag/block
    return html.unescape(inner) == target + suffix


def _md_safe_summary(summary, suffix, marko):
    """Return `summary` escaped so the drafted bullet `- <return><suffix>` renders as the LITERAL text the
    maintainer authored, in a single inline run of its own <li>, and can never open a CommonMark block, drop
    the summary, or swallow the following bullet. Three candidates are tried least-escaped first, the raw
    summary, then a minimal leading-token escape, then a full ASCII-punctuation escape, and the first whose
    drafted bullet round-trips through the vendored `marko` is returned. Every path either returns a
    render-verified value (Marko available) or a statically-guaranteed-literal full escape (Marko unavailable,
    control-free), or raises `_UnsafeSummary` rather than return an unverified value; `marko` is the loaded
    vendored Marko module, or None when it is unavailable, in which case the full escape is returned only when
    no un-renderable control character is present, else the emission fails closed. The bullet's own suffix
    (the id/closes parenthetical) is passed in and
    verified as part of the bullet, because it participates in the parse, for example as a link-reference-
    definition title. Leading/trailing whitespace, invisible in inline rendering, is stripped; the literal
    contract is over the summary's visible text (disclose-guard-residuals)."""
    target = (summary or "").strip()
    if not target:                           # blank/whitespace-only (rejected upstream): never return raw
        raise _UnsafeSummary("a blank or whitespace-only summary, which cannot be emitted as a literal bullet")
    candidates = (target, _md_minimal_escape(target), _md_full_escape(target))
    if marko is not None:
        for candidate in candidates:
            if _md_bullet_round_trips(marko, candidate, target, suffix):
                return candidate
        raise _UnsafeSummary("a summary that cannot be rendered as literal inline text (no raw, minimal, or "
                             "full-escape candidate round-trips through the vendored Marko)")
    bad = sorted({ch for ch in target if ch in _MD_UNRENDERABLE})
    if bad:                                  # marko unavailable: fail closed on a char the escape cannot verify
        raise _UnsafeSummary(
            "a summary carrying a control character the full escape cannot make literal ({}), with no "
            "vendored Marko available to render-verify".format(
                ", ".join("U+{:04X}".format(ord(ch)) for ch in bad)))
    return candidates[2]                      # marko unavailable, no unrenderable char: the full escape


def _bullet(entry, tokens):
    """One curated-draft bullet for a worklog entry: `- <summary> (<WL-id>[; closes <token>, ...])`. The
    WL-id / closes tokens are validated id shapes. The summary is free maintainer prose, so it is passed
    through `_md_safe_summary` together with the bullet's own suffix (the id/closes parenthetical
    participates in the parse, e.g. as a link-reference-definition title), which RENDER-VERIFIES through the
    vendored Marko that the drafted bullet renders as the literal authored text and cannot open a spurious
    block (a nested list, heading, code fence, blockquote, HTML block, thematic break, or link-reference
    definition) that would drop the summary or swallow the following bullet. The Marko loader is reached at
    runtime here, the drafter verifying its own output; when it is unavailable the summary is emitted through
    the full escape only if no un-renderable control character is present, else the emission FAILS CLOSED by
    raising _UnsafeSummary (carrying this entry's WL-id), which draft_entry turns into a CANNOT_EVALUATE rather
    than emit a corrupted or block-opening bullet."""
    tag = entry.get("id")
    if tokens:
        suffix = " ({}; closes {})".format(tag, ", ".join(tokens))
    else:
        suffix = " ({})".format(tag)
    try:
        marko = _commonmark_headings._load_marko()
    except _commonmark_headings.HeadingScanError:
        marko = None                          # a missing/unverifiable vendored Marko: no render, pre-check only
    try:
        summary = _md_safe_summary(entry.get("summary", ""), suffix, marko)
    except _UnsafeSummary as exc:             # name the offending WL-id, then propagate to a CANNOT_EVALUATE
        raise _UnsafeSummary(exc.reason, wl_id=tag)
    return "- " + summary + suffix


def _render_span_body(span_entries, per_entry_tokens):
    """The kind-grouped body of a per-release / unreleased draft. Sections run in WORKLOG_KINDS order (then
    any leftover kind, sorted), and within a section entries run by ascending WL-number. An empty span
    yields a single placeholder bullet so the draft is still one well-formed entry."""
    if not span_entries:
        return "- (no worklog entries in this range)"
    groups = {}
    for entry in span_entries:
        groups.setdefault(entry.get("kind"), []).append(entry)
    ordered = [k for k in _KIND_ORDER if k in groups] + sorted(k for k in groups if k not in _KIND_ORDER)
    blocks = []
    for kind in ordered:
        title = _KIND_TITLES.get(kind, str(kind).capitalize())
        lines = ["### " + title]
        for entry in sorted(groups[kind], key=lambda e: _wl_num(e.get("id")) or 0):
            lines.append(_bullet(entry, per_entry_tokens.get(_wl_num(entry.get("id")), [])))
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def _span_draft(heading, span_entries, done_records, done_enabled):
    """Assemble one span draft (heading + kind-grouped body) and its notes."""
    per_entry, notes = _enrichment(span_entries, done_records)
    if not done_enabled:
        notes = list(notes) + [
            "the `done` type is not declared in the manifest; drafting from the worklog only (done "
            "completion receipts are not incorporated)"]
    body = _render_span_body(span_entries, per_entry)
    return "{}\n\n{}\n".format(heading, body), notes


def _entry_body(entry_text):
    """The body of an existing CHANGELOG entry: its bytes minus the entry HEADING, with the surrounding
    blank lines trimmed and the content lines otherwise preserved byte-for-byte (a trailing double-space
    hard break is Markdown, never junk whitespace to rstrip away). U5 recognizes BOTH heading forms
    (`_changelog_entries`), so both are cut here: an ATX heading is its single `## token` line; a setext
    heading is its title line(s) PLUS the `-` underline that follows them (an underline left behind would
    inject a thematic break into the rollup). The caller passes an entry whose token is a ledger version,
    so an ATX first line always starts `##` and a setext title line never starts `#` (an unescaped `# `
    line would itself be an ATX heading, not a setext title). Used to roll a range draft up from the
    per-release entries it replaces, so a rollup draws from the tier directly below (spec 6.4), never the
    raw worklog."""
    lines = entry_text.split("\n")
    if lines[0].lstrip().startswith("#"):
        end = 1                           # ATX: the heading is exactly its one line
    else:
        end = 1                           # setext: skip the title line(s) to the underline; a title line
        while end < len(lines) and _SETEXT_UNDERLINE_RE.match(lines[end]) is None:
            end += 1                      # can never match the underline shape (a bare `---` line would
        end += 1                          # already have terminated the title paragraph), then drop it too
    body = lines[end:]
    while body and not body[0].strip():
        del body[0]
    while body and not body[-1].strip():
        del body[-1]
    return "\n".join(body)


# --- the pure drafting core --------------------------------------------------------------------------

def _validated_inputs(version_data, worklog_data, covers, registered_vendors, what="draft"):
    """The shared fail-closed preconditions of BOTH modes (draft and --freeze-digest): the version and
    worklog ledgers validate (the U3 seam), the worklog ids resolve, and the covers token parses against
    the ledger versions (`_parse_covers`, the same grammar the gates enforce). Returns (releases,
    ledger_versions, by_id, parsed_covers, findings): the validated carriers with `findings` None on
    success, else Nones with the CANNOT-EVALUATE findings. `what` names the refused operation in the
    messages, so the freeze-digest assist refuses on the same inputs the draft path does rather than
    digesting past a broken ledger or a covers token that fails to parse or resolve against the ledger
    (guard-input-soundness;
    check-fails-closed-on-unreadable)."""
    vv = validate_version(version_data)
    if vv.status != VALID:
        return None, None, None, None, (
            ["cannot {}: version.toml does not validate against the release-ledger schema (run the "
             "version gate first; a consistent ledger is required)".format(what)] + vv.findings)
    wv = validate_worklog(worklog_data, registered_vendors=registered_vendors)
    if wv.status != VALID:
        return None, None, None, None, (
            ["cannot {}: worklog.toml does not validate".format(what)] + wv.findings)
    by_id, id_findings = _entries_by_id(worklog_data)
    if id_findings:
        return None, None, None, None, (
            ["cannot {}: worklog ids are malformed".format(what)] + id_findings)
    releases = vv.releases
    ledger_versions = [r.get("version") for r in releases if isinstance(r, dict) and "version" in r]
    parsed, err = _parse_covers(covers, ledger_versions)
    if err is not None:
        return None, None, None, None, (
            ["cannot {}: covers token {!r}: {}".format(what, covers, err)])
    return releases, ledger_versions, by_id, parsed, None


def _unsafe_finding(exc):
    """The fail-closed CANNOT-EVALUATE finding for a summary that cannot be emitted as a literal bullet,
    naming the offending WL-id and the reason, so the refusal points a curator at the exact worklog row to
    fix (no-concealed-failure; guard-input-soundness)."""
    who = exc.wl_id if exc.wl_id else "a worklog entry"
    return ("cannot draft: worklog entry {} has {}; the drafter fails closed rather than emit a corrupted or "
            "block-opening changelog bullet (fix the summary in worklog.toml)".format(who, exc.reason))


def draft_entry(version_data, worklog_data, done_records, changelog_text, covers,
                registered_vendors=frozenset(), done_enabled=True):
    """Draft ONE candidate CHANGELOG entry for `covers`. Returns (status, draft_text, notes, findings):
    OK with the entry str on success, else CANNOT_EVALUATE with a fail-closed message. The ledgers are
    validated here through `_validated_inputs` (fail-closed CANNOT_EVALUATE, the U3 seam), so the draft
    heading always parses under the gate. `done_records` is the validated receipt list, or None when the
    type is not enabled; `changelog_text` is consulted ONLY for a range rollup (tier-below sourcing). This
    is the pure core the store-level `evaluate` and the self-test drive; it reads no files and mutates
    nothing."""
    releases, ledger_versions, by_id, parsed, vfindings = _validated_inputs(
        version_data, worklog_data, covers, registered_vendors)
    if vfindings is not None:
        return CANNOT_EVALUATE, None, [], vfindings
    kind = parsed[0]

    if kind == "unreleased":
        try:
            tail = tail_ids(releases, list(by_id.keys()))
        except ReleaseError as exc:
            return CANNOT_EVALUATE, None, [], ["cannot draft the unreleased tail: {}".format(exc)]
        span_entries = [by_id[n] for n in tail]
        try:
            text, notes = _span_draft("## unreleased", span_entries, done_records, done_enabled)
        except _UnsafeSummary as exc:
            return CANNOT_EVALUATE, None, [], [_unsafe_finding(exc)]
        return OK, text, notes, []

    if kind == "single":
        version = parsed[1]
        row = next((r for r in releases if isinstance(r, dict) and r.get("version") == version), None)
        if row is None:                   # defensive: a parsed single always names a ledger release
            return CANNOT_EVALUATE, None, [], ["cannot draft {}: no matching release row".format(version)]
        span_findings = []
        span = _parse_span(row.get("worklog_span"), span_findings, "release {}".format(version))
        if span is False:
            return (CANNOT_EVALUATE, None, [],
                    ["cannot draft {}: {}".format(version, "; ".join(span_findings))])
        span_entries = []
        if span:                          # (start, end); None is an empty span
            lo_n, hi_n = span
            present = sorted(n for n in by_id if lo_n <= n <= hi_n)
            missing_count = (hi_n - lo_n + 1) - len(present)
            if missing_count:
                # The missing ids are located by walking the gaps between the PRESENT ids, never by
                # materializing the span's numeric interval, and the refusal names at most
                # _MISSING_ID_SAMPLE of them plus a count of the rest, so an implausibly wide ledger
                # span refuses with the same located CANNOT-EVALUATE instead of exhausting memory
                # (SECA bounded consumption).
                sample = []
                cursor = lo_n
                for n in present + [hi_n + 1]:
                    while cursor < n and len(sample) < _MISSING_ID_SAMPLE:
                        sample.append(cursor)
                        cursor += 1
                    if len(sample) >= _MISSING_ID_SAMPLE:
                        break
                    cursor = n + 1
                shown = ", ".join("WL-{}".format(n) for n in sample)
                if missing_count > len(sample):
                    shown += ", and {} more".format(missing_count - len(sample))
                return (CANNOT_EVALUATE, None, [],
                        ["cannot draft {}: worklog {} covered by its span {} not in the active worklog "
                         "(rotated to the archive; spec 12); archive-composed drafting is deferred".format(
                             version, shown, "are" if missing_count > 1 else "is")])
            span_entries = [by_id[n] for n in present]
        date = row.get("date")
        suffix = " ({})".format(date[:10]) if isinstance(date, str) and len(date) >= 10 else ""
        try:
            text, notes = _span_draft(
                "## {}{}".format(version, suffix), span_entries, done_records, done_enabled)
        except _UnsafeSummary as exc:
            return CANNOT_EVALUATE, None, [], [_unsafe_finding(exc)]
        return OK, text, notes, []

    # range rollup: draw from the EXISTING per-release entries in range, never the raw worklog (spec 6.4).
    lo, hi = parsed[1]
    versions_in_range = ledger_versions[lo:hi + 1]
    try:
        cl_entries, cl_findings = _opf_changelog._changelog_entries(changelog_text)
    except _commonmark_headings.HeadingScanError as exc:
        return (CANNOT_EVALUATE, None, [],
                ["cannot draft {}: CHANGELOG.md heading scan failed ({}): {}".format(
                    covers, exc.reason, exc)])
    except UnicodeEncodeError as exc:
        return (CANNOT_EVALUATE, None, [],
                ["cannot draft {}: CHANGELOG.md is not encodable as UTF-8 ({})".format(covers, exc)])
    if cl_findings:
        # U5's malformed-heading findings are NOT discarded: a bare `##`, a multiline setext title, or a
        # non-date heading suffix means an entry heading did not parse, so the per-release entries this
        # rollup would summarize cannot be trusted (a bare `##` silently drops the content beneath it, and
        # can shift where entry boundaries fall). Fail closed rather than roll up over a mis-parsed source
        # (check-fails-closed-on-unreadable; no-concealed-failure).
        return (CANNOT_EVALUATE, None, [],
                ["cannot roll up {}: CHANGELOG.md has malformed entry heading(s), so its per-release entries "
                 "cannot be trusted as the rollup source; fix the CHANGELOG.md headings first: {}".format(
                     covers, "; ".join(cl_findings))])
    by_token = {}
    for token, entry_text in cl_entries:
        by_token.setdefault(token, []).append(entry_text)
    parts = []
    for version in reversed(versions_in_range):   # descending, matching the entry-order convention
        matches = by_token.get(version, [])
        if len(matches) == 0:
            return (CANNOT_EVALUATE, None, [],
                    ["cannot roll up {}: CHANGELOG.md has no published '## {}' entry to summarize (a range "
                     "rollup draws from the per-release entries it replaces, spec 6.4, never the raw "
                     "worklog)".format(covers, version)])
        if len(matches) > 1:
            return (CANNOT_EVALUATE, None, [],
                    ["cannot roll up {}: CHANGELOG.md has more than one '## {}' entry (ambiguous)".format(
                        covers, version)])
        parts.append("**{}**\n\n{}".format(version, _entry_body(matches[0])))
    return OK, "## {}\n\n{}\n".format(covers, "\n\n".join(parts)), [], []


# --- store resolution + input reads (composing U5's hardened reader) ---------------------------------

def _validate_done_index(data, registered_vendors):
    """Validate a parsed `done.index.toml` inline index and return (records, error): the `[[record]]` list
    on success, or a fail-closed message. Mirrors the U4 `_load_records` schema/shape/record discipline
    (guard-input-soundness; check-fails-closed-on-unreadable) without importing the view generator."""
    if not isinstance(data, dict):
        return None, "done.index.toml is not a table"
    extra = set(data) - {"schema", "record"}
    if extra:
        return None, "done.index.toml has unknown top-level key(s): {}".format(", ".join(sorted(extra)))
    schema = data.get("schema")
    if "schema" not in data or type(schema) is not int or schema != _opf_schema.SUPPORTED_SCHEMA:
        return None, ("done.index.toml schema {!r} is not the supported schema {} (a present index must "
                      "carry an exact integer schema marker; fail-closed)".format(
                          schema, _opf_schema.SUPPORTED_SCHEMA))
    records = data.get("record", [])
    if not isinstance(records, list):
        return None, "done.index.toml: [[record]] is not an array of tables"
    out = []
    for i, rec in enumerate(records):
        rv = _opf_schema.validate_record(rec, expected_type="done", registered_vendors=registered_vendors)
        if rv.status != VALID:
            return None, "done.index.toml record #{} ({}) is malformed: {}".format(
                i + 1, rv.id or "?", "; ".join(rv.findings))
        out.append(rec)
    dup_findings = _opf_schema.check_unique_ids([rec.get("id") for rec in out])
    if dup_findings:
        # A duplicated DN id would make the completion join ambiguous (one worklog link closing every
        # record that reuses the id), so a conflicting index refuses, reusing U2's uniqueness check.
        return None, "done.index.toml: {}".format("; ".join(dup_findings))
    return out, None


def _load_done(resolution, registered_vendors):
    """Read the `done` receipts of a RESOLVED store, contained and no-follow. Returns
    (done_enabled, records, error): (False, None, None) when `[types.done]` is not declared (worklog-only
    draft); (True, [...], None) when it is declared and its index validates; (None, None, message) on a
    fail-closed cannot-evaluate (a declared type's index is absent, unreadable, or malformed). The manifest
    has already been validated in full by U5's `_load_inputs`, so this reads only its `[types]` table to
    decide whether the receipt join applies."""
    pointer = resolution.pointer_source != "default"
    try:
        store_fd = _opf_store._open_store_root_fd(resolution.store_root, pointer)
    except OSError as exc:
        return None, None, "cannot open store root {} ({})".format(resolution.store_root, exc)
    try:
        try:
            manifest_data = _opf_store._read_toml_contained(
                store_fd, resolution.machine_rel + "/" + _opf_store.MANIFEST_NAME)
            types = manifest_data.get("types") if isinstance(manifest_data, dict) else None
            if not (isinstance(types, dict) and "done" in types):
                return False, None, None      # the done type is not enabled: worklog-only draft
            done_data = _opf_store._read_toml_contained(
                store_fd, resolution.machine_rel + "/done.index.toml")
        except (_opf_store.StoreError, OSError) as exc:
            # A StoreError (unreadable/unparseable) or a raw OSError from the contained read is the
            # fail-closed cannot-evaluate (check-fails-closed-on-unreadable), never a silent empty join.
            return None, None, str(exc)
    finally:
        os.close(store_fd)
    if done_data is None:
        return None, None, ("done.index.toml is absent but [types.done] is declared (a declared type's index "
                            "must exist; fail-closed, check-fails-closed-on-unreadable)")
    records, err = _validate_done_index(done_data, registered_vendors)
    if err is not None:
        return None, None, err
    return True, records, None


def _freeze_digest_of(changelog_text, covers):
    """The read-only publication assist: the freeze digest of the CURATED `## <covers>` CHANGELOG.md entry,
    reusing U5's `_changelog_entries` + `freeze_digest`, so the human never hand-computes the sha256. Writes
    nothing. Fail-closed when the entry is absent, ambiguous, the scan fails, or CHANGELOG.md carries a
    malformed entry heading (U5's heading findings are propagated, never discarded, so a heading such as
    `## <covers> NOT-A-DATE` cannot digest at exit 0)."""
    try:
        entries, cl_findings = _opf_changelog._changelog_entries(changelog_text)
    except _commonmark_headings.HeadingScanError as exc:
        return AbsorbResult(CANNOT_EVALUATE, findings=[
            "cannot compute the freeze digest: CHANGELOG.md heading scan failed ({}): {}".format(
                exc.reason, exc)])
    except UnicodeEncodeError as exc:
        return AbsorbResult(CANNOT_EVALUATE, findings=[
            "cannot compute the freeze digest: CHANGELOG.md is not encodable as UTF-8 ({})".format(exc)])
    if cl_findings:
        # A malformed heading still yields its covers token, so the entry would otherwise digest at exit 0
        # while its heading is malformed (e.g. `## 1.0.0 NOT-A-DATE`, or a bare `##` elsewhere). Fail closed
        # on any heading finding rather than digest over a mis-parsed CHANGELOG (check-fails-closed-on-unreadable).
        return AbsorbResult(CANNOT_EVALUATE, findings=[
            "cannot compute the freeze digest: CHANGELOG.md has malformed entry heading(s), so its entries "
            "cannot be trusted; fix the CHANGELOG.md headings first: {}".format("; ".join(cl_findings))])
    matches = [e for t, e in entries if t == covers]
    if len(matches) == 0:
        return AbsorbResult(CANNOT_EVALUATE, findings=[
            "cannot compute the freeze digest: CHANGELOG.md has no '## {}' entry".format(covers)])
    if len(matches) > 1:
        return AbsorbResult(CANNOT_EVALUATE, findings=[
            "cannot compute the freeze digest: CHANGELOG.md has more than one '## {}' entry "
            "(ambiguous)".format(covers)])
    return AbsorbResult(OK, draft=_opf_changelog.freeze_digest(matches[0]))


def evaluate(product_root, covers=UNRELEASED, mode="draft"):
    """Resolve the store at `product_root` and draft (or, in `freeze-digest` mode, digest) the `covers`
    entry. Returns an AbsorbResult: NOT-APPLICABLE for a non-adopter root (this pack's own `--root .`);
    CANNOT-EVALUATE (fail closed) when the store cannot resolve or a required input is unreadable,
    unparseable, or inconsistent; OK otherwise. Reads only; mutates nothing."""
    try:
        product_root = Path(os.path.abspath(product_root))
    except OSError as exc:
        return AbsorbResult(CANNOT_EVALUATE,
                            findings=["cannot resolve --root path {!r} ({})".format(product_root, exc)])
    if not product_root.exists():
        return AbsorbResult(CANNOT_EVALUATE, findings=["--root path does not exist: {}".format(product_root)])
    if not product_root.is_dir():
        return AbsorbResult(CANNOT_EVALUATE,
                            findings=["--root path is not a directory: {}".format(product_root)])
    res = _opf_store.resolve_store(product_root)
    if res.status == NOT_ADOPTED:
        return AbsorbResult(NOT_APPLICABLE,
                            findings=["not an OPFiles adopter ({}); the changelog drafter does not "
                                      "apply".format(res.detail)])
    if res.status != RESOLVED:
        return AbsorbResult(CANNOT_EVALUATE, findings=[res.detail])

    version_data, worklog_data, changelog_text, registered_vendors, error = \
        _opf_changelog._load_inputs(res, product_root)
    if error is not None:
        return AbsorbResult(CANNOT_EVALUATE, findings=[error])

    if mode == "freeze-digest":
        # The freeze-digest assist runs the SAME ledger-consistency floor as the draft mode (the shared
        # `_validated_inputs` preconditions: the version + worklog ledgers and the covers token), NOT the
        # draft-only done-index and rotated-span checks, which guard the worklog->draft synthesis freeze does
        # not perform. A ledger that does not validate, or a covers token that fails to parse or resolve
        # against the ledger, is CANNOT-EVALUATE (`unreleased` and a valid in-ledger range DO resolve and are
        # digested when the entry is present), never a digest over an unvalidated store (guard-input-soundness).
        _releases, _versions, _by_id, _parsed, vfindings = _validated_inputs(
            version_data, worklog_data, covers, registered_vendors, what="compute the freeze digest")
        if vfindings is not None:
            return AbsorbResult(CANNOT_EVALUATE, findings=vfindings)
        return _freeze_digest_of(changelog_text, covers)

    done_enabled, done_records, derr = _load_done(res, registered_vendors)
    if derr is not None:
        return AbsorbResult(CANNOT_EVALUATE, findings=[derr])
    status, text, notes, findings = draft_entry(
        version_data, worklog_data, done_records if done_enabled else None, changelog_text, covers,
        registered_vendors=registered_vendors, done_enabled=done_enabled)
    return AbsorbResult(status, draft=text, notes=notes, findings=findings)


def run(root, covers=UNRELEASED, mode="draft"):
    """Draft under `root` and print: the candidate bytes (or the digest) to STDOUT, the banner + notes to
    STDERR. Returns the aggregate exit code (0 draft / NA, 2 cannot-evaluate)."""
    result = evaluate(root, covers, mode)
    if result.status == OK:
        if mode == "freeze-digest":
            print("opf absorb: freeze digest of the curated '## {}' CHANGELOG.md entry -- record it on the "
                  "[[summary]] row at publication; the tool writes nothing".format(covers), file=sys.stderr)
        else:
            print("opf absorb: DRAFT candidate entry for {!r} on stdout -- a machine draft for human "
                  "curation, NOT authoritative; edit it into CHANGELOG.md and publish through the recorded "
                  "freeze flow (the tool writes nothing)".format(covers), file=sys.stderr)
        for note in result.notes:
            print("opf absorb: note: {}".format(note), file=sys.stderr)
        sys.stdout.write(result.draft)
        if not result.draft.endswith("\n"):
            sys.stdout.write("\n")
    elif result.status == NOT_APPLICABLE:
        print("opf absorb: NOT APPLICABLE ({})".format("; ".join(result.findings)), file=sys.stderr)
    else:
        print("opf absorb: cannot evaluate:", file=sys.stderr)
        for f in result.findings:
            print("  - {}".format(f), file=sys.stderr)
    return EXIT[result.status]


# --- self-test ----------------------------------------------------------------------------------------

def self_test():
    """Draft invariants over synthetic in-memory ledgers and on-disk stores, judged on returned values,
    never by grepping output (the isolate-verifiers rule). Returns 0 clean, 1 on a failed check, 2 on a
    fail-closed harness error. Vectors: golden unreleased / per-release / range-rollup drafts (byte-exact);
    the rollup tier-below discriminator (mutated worklog prose leaves the rollup unchanged); done enrichment
    (a DN-linked receipt appears; removing the done read flips it); the unlinked-in-span done note (present,
    draft bytes unchanged, still OK); a rotated span, a duplicate done id, an implausibly wide span (a
    bounded refusal, the interval never materialized), a setext-headed rollup source (title and underline
    dropped, hard breaks preserved), a missing rollup source, a malformed covers token, and
    an empty tail; stdout purity (the draft parses under U5 as exactly one entry whose token round-trips
    _parse_covers); the curated-flow tie to the live gates (draft integrates and run_gates PASS; a published
    edit without re-publish -> FINDING); the freeze-digest assist equal to U5's freeze_digest; markdown-safe
    summary emission (each summary renders as the literal authored text through the vendored
    Marko renderer and never opens a block or swallows a following bullet, escalating to a full ASCII-
    punctuation escape when a lesser candidate does not round-trip; and FAILING CLOSED to a CANNOT_EVALUATE
    naming the WL-id when a summary cannot be rendered literally (a NUL) or is blank, both Marko-available and
    Marko-unavailable, so no unverified value is ever emitted); U5
    malformed-heading findings propagated fail-closed in BOTH the range rollup and the freeze digest; the CLI
    parser fail-closed cases; and end-to-end store resolution (NOT-APPLICABLE, cannot-evaluate, OK, done
    enabled/enriched, done not declared + note, malformed done index, freeze-digest, freeze-digest
    fail-closed on a broken ledger and on an unledgered covers token, exit-map)."""
    import tempfile
    import shutil

    try:
        _journal.require_containment()
    except _journal.JournalError as exc:
        print("OPF-ABSORB SELF-TEST ERROR: {}; fail-closed".format(exc), file=sys.stderr)
        return 2

    failures = []
    checked = 0

    def check(name, cond):
        nonlocal checked
        checked += 1
        if not cond:
            failures.append(name)

    def entry(n, kind="added", summary="s", links=None):
        e = {"id": "WL-{}".format(n), "date": "2026-06-{:02d}T00:00:00Z".format((n % 27) + 1),
             "actor": {"kind": "maintainer"}, "kind": kind, "summary": summary}
        if links is not None:
            e["links"] = links
        return e

    def freeze_of(changelog_text, token):
        entries, _f = _opf_changelog._changelog_entries(changelog_text)
        for t, e in entries:
            if t == token:
                return _opf_changelog.freeze_digest(e)
        raise AssertionError("no entry {!r}".format(token))

    # --- shared base: two releases (1.0.0 = WL-1; 1.1.0 = WL-2..WL-3), unreleased tail = WL-4 ----------
    worklog = {"schema": 1, "entry": [
        entry(1, "added", "first release"), entry(2, "added", "second release"),
        entry(3, "fixed", "a fix"), entry(4, "added", "a feature")]}
    by_id, _ = _entries_by_id(worklog)
    dig1 = _opf_release.compute_span_digest(by_id, (1, 1))
    dig23 = _opf_release.compute_span_digest(by_id, (2, 3))

    cl = ("# Changelog\n\n"
          "## unreleased\n\n### Added\n- a feature (WL-4)\n\n"
          "## 1.1.0 (2026-06-15)\n\n### Added\n- second release (WL-2)\n\n### Fixed\n- a fix (WL-3)\n\n"
          "## 1.0.0 (2026-06-01)\n\n### Added\n- first release (WL-1)\n")

    def base_version(changelog_text):
        return {"schema": 1,
                "release": [
                    {"version": "1.0.0", "date": "2026-06-01T00:00:00Z",
                     "worklog_span": ["WL-1", "WL-1"], "coverage_digest": dig1},
                    {"version": "1.1.0", "date": "2026-06-15T00:00:00Z",
                     "worklog_span": ["WL-2", "WL-3"], "coverage_digest": dig23}],
                "summary": [
                    {"covers": "unreleased", "status": "working"},
                    {"covers": "1.0.0", "status": "published", "digest": freeze_of(changelog_text, "1.0.0")},
                    {"covers": "1.1.0", "status": "published", "digest": freeze_of(changelog_text, "1.1.0")}]}

    vbase = base_version(cl)

    # 1: golden unreleased draft (byte-exact; stable WL-id order, kind grouping).
    st, text, notes, _f = draft_entry(vbase, worklog, None, cl, "unreleased")
    check("golden-unreleased-draft",
          st == OK and text == "## unreleased\n\n### Added\n- a feature (WL-4)\n" and notes == [])

    # 2: golden per-release draft, dated from the ledger row (never the clock).
    st, text, _n, _f = draft_entry(vbase, worklog, None, cl, "1.1.0")
    check("golden-perrelease-draft",
          st == OK and text == ("## 1.1.0 (2026-06-15)\n\n### Added\n- second release (WL-2)\n\n"
                                "### Fixed\n- a fix (WL-3)\n"))

    # 3: golden range rollup, sourced from the per-release ENTRIES (spec 6.4).
    golden_rollup = ("## 1.0.0..1.1.0\n\n**1.1.0**\n\n### Added\n- second release (WL-2)\n\n"
                     "### Fixed\n- a fix (WL-3)\n\n**1.0.0**\n\n### Added\n- first release (WL-1)\n")
    st, text, _n, _f = draft_entry(vbase, worklog, None, cl, "1.0.0..1.1.0")
    check("golden-rollup-draft", st == OK and text == golden_rollup)

    # 3b: DISCRIMINATOR -- mutating worklog PROSE leaves the rollup unchanged (proves tier-below sourcing,
    # not raw-worklog sourcing). If the rollup read the worklog, this would diverge.
    worklog_mut = {"schema": 1, "entry": [
        entry(1, "added", "first release"), entry(2, "added", "MUTATED PROSE"),
        entry(3, "fixed", "a fix"), entry(4, "added", "a feature")]}
    st, text, _n, _f = draft_entry(vbase, worklog_mut, None, cl, "1.0.0..1.1.0")
    check("rollup-tier-below-not-worklog", st == OK and text == golden_rollup)

    # 4: done ENRICHMENT -- WL-4 links DN-1 (receipt_of BI-7); the bullet gains "closes BI-7". Removing the
    # done read flips it.
    done1 = {"id": "DN-1", "type": "done", "status": "recorded", "title": "receipt",
             "created_at": "2026-06-20T00:00:00Z", "updated_at": "2026-06-20T00:00:00Z",
             "actor": {"kind": "maintainer"}, "links": [{"rel": "receipt_of", "id": "BI-7"}]}
    worklog_dn = {"schema": 1, "entry": [
        entry(1, "added", "first release"), entry(2, "added", "second release"), entry(3, "fixed", "a fix"),
        entry(4, "added", "a feature", links=[{"rel": "relates", "id": "DN-1"}])]}
    st, text, _n, _f = draft_entry(vbase, worklog_dn, [done1], cl, "unreleased")
    check("done-enrichment-present",
          st == OK and text == "## unreleased\n\n### Added\n- a feature (WL-4; closes BI-7)\n")
    st2, text2, _n, _f = draft_entry(vbase, worklog_dn, None, cl, "unreleased")   # done read removed
    check("done-enrichment-flip",
          st2 == OK and text2 == "## unreleased\n\n### Added\n- a feature (WL-4)\n")

    # 5: UNLINKED-in-span done note -- WL-4 links only BI-7 (not DN-1); the receipt is surfaced as a note,
    # the draft bytes are unchanged from the no-done baseline, and the status stays OK (note, not finding).
    worklog_bi = {"schema": 1, "entry": [
        entry(1, "added", "first release"), entry(2, "added", "second release"), entry(3, "fixed", "a fix"),
        entry(4, "added", "a feature", links=[{"rel": "resolves", "id": "BI-7"}])]}
    st, text, notes, _f = draft_entry(vbase, worklog_bi, [done1], cl, "unreleased")
    check("unlinked-done-note",
          st == OK and text == "## unreleased\n\n### Added\n- a feature (WL-4)\n"
          and any("DN-1" in n and "may be missing" in n for n in notes))

    # 6: a ROTATED span (1.0.0 covers WL-1, absent from the active worklog) -> CANNOT-EVALUATE naming it.
    worklog_rot = {"schema": 1, "entry": [entry(2, "added", "second release"), entry(3, "fixed", "a fix"),
                                          entry(4, "added", "a feature")]}
    st, _t, _n, findings = draft_entry(vbase, worklog_rot, None, cl, "1.0.0")
    check("rotated-span-cannot-eval",
          st == CANNOT_EVALUATE and any("WL-1" in f and "rotated" in f for f in findings))

    # 6b: a range rollup whose source per-release entry is missing from CHANGELOG.md -> CANNOT-EVALUATE.
    cl_missing = ("# Changelog\n\n## unreleased\n\n- x\n\n"
                  "## 1.1.0 (2026-06-15)\n\n- only 1.1.0 here\n")
    st, _t, _n, findings = draft_entry(vbase, worklog, None, cl_missing, "1.0.0..1.1.0")
    check("rollup-missing-source-cannot-eval",
          st == CANNOT_EVALUATE and any("no published '## 1.0.0' entry" in f for f in findings))

    # 6c: a malformed covers token -> CANNOT-EVALUATE (fail-closed control).
    st, _t, _n, _f = draft_entry(vbase, worklog, None, cl, "9.9.9")
    check("malformed-covers-cannot-eval", st == CANNOT_EVALUATE)

    # 6d: an empty unreleased tail drafts one well-formed placeholder entry.
    worklog_full = {"schema": 1, "entry": [entry(1, "added", "a"), entry(2, "added", "b")]}
    by_id_full, _ = _entries_by_id(worklog_full)
    v_full = {"schema": 1,
              "release": [{"version": "1.0.0", "date": "2026-06-01T00:00:00Z",
                           "worklog_span": ["WL-1", "WL-2"],
                           "coverage_digest": _opf_release.compute_span_digest(by_id_full, (1, 2))}],
              "summary": [{"covers": "unreleased", "status": "working"}]}
    st, text, _n, _f = draft_entry(v_full, worklog_full, None, cl, "unreleased")
    check("empty-tail-placeholder",
          st == OK and text == "## unreleased\n\n- (no worklog entries in this range)\n")

    # 6e: a DUPLICATE DN id in the done index refuses (one worklog link to the id would otherwise close
    # every record that reuses it, an ambiguous completion join), through U2's uniqueness check.
    recs, derr = _validate_done_index({"schema": 1, "record": [done1, dict(done1)]}, frozenset())
    check("done-duplicate-dn-rejected",
          recs is None and derr is not None and "duplicate id 'DN-1'" in derr)

    # 6f: an implausibly wide release span refuses with the located missing-id CANNOT-EVALUATE, bounded:
    # the refusal names a capped sample plus a count of the rest, and the finding text stays small (the
    # billion-wide interval is never materialized; a materializing check would exhaust memory here).
    v_huge = {"schema": 1,
              "release": [{"version": "1.0.0", "date": "2026-06-01T00:00:00Z",
                           "worklog_span": ["WL-1", "WL-1000000000"],
                           "coverage_digest": "sha256:" + "0" * 64}],
              "summary": [{"covers": "unreleased", "status": "working"},
                          {"covers": "1.0.0", "status": "published", "digest": "sha256:" + "0" * 64}]}
    st, _t, _n, findings = draft_entry(v_huge, worklog, None, cl, "1.0.0")
    check("huge-span-bounded-refusal",
          st == CANNOT_EVALUATE and any("rotated" in f and "WL-5" in f and "more" in f for f in findings)
          and all(len(f) < 1000 for f in findings))

    # 6g: rollup extraction preserves Markdown semantics: a SETEXT-headed source entry loses its title AND
    # underline (no injected thematic break), and a trailing double-space hard break in a body line
    # survives (content lines are never rstripped). U5 recognizes both heading forms; so does the cut.
    cl_setext = ("# Changelog\n\n"
                 "## unreleased\n\n### Added\n- a feature (WL-4)\n\n"
                 "## 1.1.0 (2026-06-15)\n\n### Added\n- second release (WL-2)\n\n"
                 "### Fixed\n- a fix (WL-3)\n\n"
                 "1.0.0\n---\n\n### Added\n- before  \n  after (WL-1)\n")
    st, text, _n, _f = draft_entry(vbase, worklog, None, cl_setext, "1.0.0..1.1.0")
    check("rollup-setext-and-hard-break",
          st == OK and text == ("## 1.0.0..1.1.0\n\n**1.1.0**\n\n### Added\n- second release (WL-2)\n\n"
                                "### Fixed\n- a fix (WL-3)\n\n**1.0.0**\n\n### Added\n"
                                "- before  \n  after (WL-1)\n"))

    # 7: STDOUT PURITY -- every draft parses under U5 as exactly one entry whose token round-trips
    # _parse_covers against the ledger. Catches a draft that would break the very gate it feeds.
    def one_entry_token(draft_text, expect_token):
        entries, _f = _opf_changelog._changelog_entries(draft_text)
        if len(entries) != 1 or entries[0][0] != expect_token:
            return False
        _p, err = _parse_covers(expect_token, ["1.0.0", "1.1.0"])
        return err is None
    du, _n, _f = draft_entry(vbase, worklog, None, cl, "unreleased")[1:4] if False else (None, None, None)
    check("stdout-purity-unreleased",
          one_entry_token(draft_entry(vbase, worklog, None, cl, "unreleased")[1], "unreleased"))
    check("stdout-purity-single",
          one_entry_token(draft_entry(vbase, worklog, None, cl, "1.1.0")[1], "1.1.0"))
    check("stdout-purity-range",
          one_entry_token(draft_entry(vbase, worklog, None, cl, "1.0.0..1.1.0")[1], "1.0.0..1.1.0"))

    # 8: CURATED-FLOW tie to the LIVE gates -- the base triad passes U5 run_gates (draft integrates, and a
    # WORKING entry is edited with no ceremony), while an edit to a PUBLISHED entry without a re-publish is
    # a freeze FINDING. Re-asserts there is no byte-drift gate on CHANGELOG.md.
    check("curated-flow-run-gates-pass", _opf_changelog.run_gates(vbase, worklog, cl).status == "PASS")
    cl_edit = cl.replace("- first release (WL-1)\n", "- first release, reworded (WL-1)\n")
    check("published-edit-freeze-finding",
          _opf_changelog.run_gates(vbase, worklog, cl_edit).status == "FINDING")

    # 9: the freeze-digest assist equals U5's freeze_digest of the parsed entry.
    check("freeze-digest-assist",
          _freeze_digest_of(cl, "1.0.0").draft == freeze_of(cl, "1.0.0")
          and _freeze_digest_of(cl, "no-such").status == CANNOT_EVALUATE)

    # 10: CLI parser fail-closed cases.
    check("cli-self-test-alone", _parse_cli(["--self-test"]) == ("self-test", None, None, None, None))
    check("cli-defaults", _parse_cli([]) == ("run", ".", UNRELEASED, "draft", None))
    check("cli-covers-root",
          _parse_cli(["--covers", "1.0.0", "--root", "x"]) == ("run", "x", "1.0.0", "draft", None))
    check("cli-freeze-digest", _parse_cli(["--freeze-digest", "--covers", "1.0.0"])[3] == "freeze-digest")
    check("cli-root-needs-value", _parse_cli(["--root"])[4] is not None)
    check("cli-root-empty", _parse_cli(["--root", "  "])[4] is not None)
    check("cli-covers-needs-value", _parse_cli(["--covers"])[4] is not None)
    check("cli-covers-empty", _parse_cli(["--covers", ""])[4] is not None)
    check("cli-unknown-arg", _parse_cli(["--bogus"])[4] is not None)
    check("cli-self-test-not-combinable", _parse_cli(["--self-test", "--root", "x"])[4] is not None)

    # 11: exit-map pin.
    check("exit-map", EXIT[OK] == 0 and EXIT[NOT_APPLICABLE] == 0 and EXIT[CANNOT_EVALUATE] == 2)

    # --- end-to-end store resolution over synthetic on-disk stores -----------------------------------
    manifest_wl = ("[opf]\n"
                   'standard = "opf"\n'
                   'spec_version = "' + _opf_store.SUPPORTED_SPEC_VERSION + '"\n'
                   'layout = "inline"\n'
                   'posture = "required"\n'
                   'import_status = "none"\n\n'
                   "[types.worklog]\n"
                   'namespace = "WL"\n')
    manifest_done = manifest_wl + '\n[types.done]\nnamespace = "DN"\n'

    cl_disk = ("# Changelog\n\n"
               "## unreleased\n\n### Added\n- a feature (WL-2)\n\n"
               "## 1.0.0 (2026-06-01)\n\n### Added\n- first release (WL-1)\n")
    wl1d = {"id": "WL-1", "date": "2026-06-02T00:00:00Z", "actor": {"kind": "maintainer"},
            "kind": "added", "summary": "first release"}
    dig1d = _opf_release.compute_span_digest({1: wl1d}, (1, 1))
    version_disk = ("schema = 1\n\n"
                    "[[release]]\n"
                    'version = "1.0.0"\n'
                    'date = "2026-06-01T00:00:00Z"\n'
                    'worklog_span = ["WL-1", "WL-1"]\n'
                    'coverage_digest = "{}"\n\n'.format(dig1d) +
                    "[[summary]]\n"
                    'covers = "unreleased"\n'
                    'status = "working"\n\n'
                    "[[summary]]\n"
                    'covers = "1.0.0"\n'
                    'status = "published"\n'
                    'digest = "{}"\n'.format(freeze_of(cl_disk, "1.0.0")))
    worklog_disk = ("schema = 1\n\n"
                    "[[entry]]\n"
                    'id = "WL-1"\n'
                    'date = "2026-06-02T00:00:00Z"\n'
                    'kind = "added"\n'
                    'summary = "first release"\n\n'
                    "[entry.actor]\n"
                    'kind = "maintainer"\n\n'
                    "[[entry]]\n"
                    'id = "WL-2"\n'
                    'date = "2026-06-03T00:00:00Z"\n'
                    'kind = "added"\n'
                    'summary = "a feature"\n\n'
                    "[entry.actor]\n"
                    'kind = "maintainer"\n')
    worklog_disk_dn = (worklog_disk + '\n[[entry.links]]\nrel = "relates"\nid = "DN-1"\n')
    done_disk = ("schema = 1\n\n"
                 "[[record]]\n"
                 'id = "DN-1"\n'
                 'type = "done"\n'
                 'status = "recorded"\n'
                 'title = "receipt"\n'
                 'created_at = "2026-06-20T00:00:00Z"\n'
                 'updated_at = "2026-06-20T00:00:00Z"\n'
                 'summary = "closed BI-7"\n\n'
                 "[record.actor]\n"
                 'kind = "maintainer"\n\n'
                 "[[record.links]]\n"
                 'rel = "receipt_of"\n'
                 'id = "BI-7"\n')
    done_disk_bad = done_disk.replace("schema = 1", "schema = 2", 1)

    base_dir = Path(tempfile.mkdtemp(prefix="opf-absorb-selftest-")).resolve()
    counter = [0]

    def build_store(version_src, worklog_src, changelog_src, manifest_src, done_src=None):
        counter[0] += 1
        root = base_dir / "case-{:02d}".format(counter[0])
        machine = root / _opf_store.WORKING_DIRNAME / _opf_store.DEFAULT_MACHINE_SUBDIR
        machine.mkdir(parents=True)
        (machine / _opf_store.MANIFEST_NAME).write_text(manifest_src, encoding="utf-8")
        (machine / "version.toml").write_text(version_src, encoding="utf-8")
        (machine / "worklog.toml").write_text(worklog_src, encoding="utf-8")
        if changelog_src is not None:
            (root / _opf_changelog.CHANGELOG_REL).write_text(changelog_src, encoding="utf-8")
        if done_src is not None:
            (machine / "done.index.toml").write_text(done_src, encoding="utf-8")
        return root

    try:
        # NOT an OPFiles adopter -> NOT APPLICABLE.
        na_root = base_dir / "not-adopted"
        na_root.mkdir()
        check("disk-not-applicable", evaluate(na_root).status == NOT_APPLICABLE)

        # A valid triad WITHOUT [types.done]: OK draft plus the disclosed "done not enabled" note.
        r = evaluate(build_store(version_disk, worklog_disk, cl_disk, manifest_wl), "unreleased")
        check("disk-done-not-declared",
              r.status == OK and r.draft == "## unreleased\n\n### Added\n- a feature (WL-2)\n"
              and any("not declared" in n for n in r.notes))

        # A valid triad WITH [types.done] and a DN-linked worklog entry: the receipt enriches the bullet.
        r = evaluate(build_store(version_disk, worklog_disk_dn, cl_disk, manifest_done, done_disk),
                     "unreleased")
        check("disk-done-enrichment",
              r.status == OK and r.draft == "## unreleased\n\n### Added\n- a feature (WL-2; closes BI-7)\n")

        # [types.done] declared but the index schema is wrong -> CANNOT-EVALUATE (check-fails-closed).
        check("disk-done-malformed-cannot-eval",
              evaluate(build_store(version_disk, worklog_disk_dn, cl_disk, manifest_done, done_disk_bad),
                       "unreleased").status == CANNOT_EVALUATE)

        # [types.done] declared but the index is absent -> CANNOT-EVALUATE.
        check("disk-done-absent-cannot-eval",
              evaluate(build_store(version_disk, worklog_disk, cl_disk, manifest_done),
                       "unreleased").status == CANNOT_EVALUATE)

        # An unparseable version.toml -> CANNOT-EVALUATE (the U5 reader seam).
        check("disk-unparseable-version-cannot-eval",
              evaluate(build_store("this is [[ not valid toml", worklog_disk, cl_disk, manifest_wl)).status
              == CANNOT_EVALUATE)

        # A missing CHANGELOG.md is a required input for the reused U5 reader -> CANNOT-EVALUATE.
        check("disk-missing-changelog-cannot-eval",
              evaluate(build_store(version_disk, worklog_disk, None, manifest_wl)).status
              == CANNOT_EVALUATE)

        # The freeze-digest assist over a resolved store equals U5's freeze_digest of the entry.
        fd_store = build_store(version_disk, worklog_disk, cl_disk, manifest_wl)
        check("disk-freeze-digest",
              evaluate(fd_store, "1.0.0", mode="freeze-digest").draft == freeze_of(cl_disk, "1.0.0"))

        # freeze-digest fails CLOSED on the same inputs the draft mode validates: a schema-broken version
        # ledger refuses even though the '## 1.0.0' entry itself is digestible ...
        version_disk_bad = version_disk.replace("schema = 1", "schema = 999", 1)
        check("disk-freeze-digest-broken-ledger-cannot-eval",
              evaluate(build_store(version_disk_bad, worklog_disk, cl_disk, manifest_wl),
                       "1.0.0", mode="freeze-digest").status == CANNOT_EVALUATE)
        # ... and a SINGLE-RELEASE covers token present as a CHANGELOG heading but ABSENT from the ledger
        # refuses (the assist never digests a single-release entry no ledger release names; `unreleased` and a
        # valid in-ledger range parse and are accepted).
        cl_stray = cl_disk + "\n## 9.9.9 (2026-06-30)\n\n- a stray entry no ledger release covers\n"
        check("disk-freeze-digest-unledgered-covers-cannot-eval",
              evaluate(build_store(version_disk, worklog_disk, cl_stray, manifest_wl),
                       "9.9.9", mode="freeze-digest").status == CANNOT_EVALUATE)

        # run() maps a store-level status to the process exit code through EXIT.
        check("disk-run-ok-exit-0",
              run(str(build_store(version_disk, worklog_disk, cl_disk, manifest_wl))) == 0)
        check("disk-run-not-applicable-exit-0", run(str(na_root)) == 0)
        check("disk-run-cannot-eval-exit-2",
              run(str(build_store("bad [[", worklog_disk, cl_disk, manifest_wl))) == 2)
    finally:
        shutil.rmtree(base_dir, ignore_errors=True)

    # 12: FIX 1 -- RENDER-VERIFIED markdown-safe summary emission. Each summary is emitted so the DRAFTED
    # BULLET renders as the LITERAL authored text in its own <li>, never opening a block, dropping the
    # summary, or swallowing the following bullet. Each vector is rendered through the vendored Marko (a
    # parse-only / heading-count test cannot see this loss) and checked to survive literal beside its sibling
    # sentinel; the emission escalates to a full ASCII-punctuation escape whenever a lesser candidate does not
    # round-trip, so it is TOTAL. The renderer is reached through U5's provenance-checked `_load_marko`, off
    # the gate's per-parse path.
    _marko = _commonmark_headings._load_marko()
    _rt_pre, _rt_post = "<ul>\n<li>", "</li>\n<li>SENT (WL-9)</li>\n</ul>\n"

    def renders_literal(s, suffix=" (WL-4)"):
        # Emit the bullet exactly as _bullet would (through _md_safe_summary + the real Marko), then confirm
        # the DRAFTED bullet renders the literal summary text and keeps the sentinel a sibling <li>.
        safe = _md_safe_summary(s, suffix, _marko)
        out = _marko.convert("- " + safe + suffix + "\n- SENT (WL-9)\n")
        if not (out.startswith(_rt_pre) and out.endswith(_rt_post)):
            return False
        inner = out[len(_rt_pre):len(out) - len(_rt_post)]
        return "<" not in inner and ">" not in inner and "\n" not in inner \
            and html.unescape(inner) == s.strip() + suffix

    # block-construct-leading / content-dropping / over-escaped hazards: each must render literal + survive.
    for _lbl, _haz in [("linkref", "[x]: /url"), ("linkref-escbracket", "[x\\]]: /url"),
                       ("linkref-escbracket2", "[x\\]y]: z"), ("atx-comment", "# visible <!-- LOST -->"),
                       ("blockquote-emph", "> *important*"), ("autolink", "<https://example.com>"),
                       ("fence", "```py"), ("nested-bullet", "- nested"), ("ordered", "1. ordered"),
                       ("html-block", "<div> literal"), ("tab-indent", "\tplain")]:
        check("md-safe-summary-renders-" + _lbl, renders_literal(_haz))
    # ordinary summaries render literal too (raw or minimal candidate); an inline-markup summary escalates to
    # a render-verified escape, which is CORRECT under the literal-prose contract.
    for _lbl, _ok in [("plain", "Add feature"), ("scope", "[scope] did X"),
                      ("versionish", "1.0 support"), ("emph-code", "use *emphasis* and `code`")]:
        check("md-safe-summary-ordinary-" + _lbl, renders_literal(_ok))
    # ordinary block-free prose is emitted VERBATIM (the raw candidate is accepted, no escaping).
    check("md-safe-summary-plain-verbatim",
          _md_safe_summary("Add feature", " (WL-4)", _marko) == "Add feature")
    check("md-safe-summary-versionish-verbatim",
          _md_safe_summary("1.0 support", " (WL-4)", _marko) == "1.0 support")
    check("md-safe-summary-scope-verbatim",
          _md_safe_summary("[scope] did X", " (WL-4)", _marko) == "[scope] did X")
    # the closes-token suffix participates in the parse (a link-ref-definition title), so a bullet is verified
    # against its REAL suffix, not a generic one.
    check("md-safe-summary-renders-linkref-with-closes",
          renders_literal("[x]: /url", " (WL-4; closes BI-7)"))

    # the FULL-ESCAPE fallback path is exercised: a summary whose minimal (candidate-1) escape still does not
    # round-trip (an escaped-bracket link-ref definition; a mid-line HTML comment; inline emphasis/code) is
    # escalated all the way to the full ASCII-punctuation escape, and that emitted bullet still renders literal.
    for _lbl, _s in [("escbr", "[x\\]]: /url"), ("comment", "# visible <!-- LOST -->"),
                     ("emph", "use *emphasis* and `code`"), ("bq-emph", "> *important*")]:
        _r = _md_safe_summary(_s, " (WL-4)", _marko)
        check("md-safe-summary-escalates-to-full-" + _lbl,
              _r == _md_full_escape(_s.strip()) and renders_literal(_s))
    # a Marko-UNAVAILABLE emission (marko=None) falls to the always-safe full escape WITHOUT a render, and
    # _bullet itself degrades the same way when the loader raises HeadingScanError.
    check("md-safe-summary-marko-unavailable-full-escape",
          _md_safe_summary("# heading-like", " (WL-4)", None) == _md_full_escape("# heading-like")
          and _md_safe_summary("[x]: /url", " (WL-4)", None) == _md_full_escape("[x]: /url"))

    # FAIL-CLOSED invariant (replaces the retired totality check): _md_safe_summary never returns an
    # unverified value -- over odd non-blank inputs and over the NUL/blank vectors the fix targets, each call
    # either RAISES _UnsafeSummary or returns a value whose drafted bullet renders literal.
    def _raises_or_round_trips(s):
        try:
            _md_safe_summary(s, " (WL-4)", _marko)
        except _UnsafeSummary:
            return True
        return renders_literal(s)
    check("md-safe-summary-nonblank-returns-and-round-trips",
          all(_raises_or_round_trips(_s)
              for _s in ["#", "```", "<", "[", "-", "*", "1.", "\\", "&amp;", "a<b>c", "***", "---"]))
    check("md-safe-summary-never-unverified-return",
          all(_raises_or_round_trips(_s)
              for _s in ["a\x00b", "\x00", "a\x01b", "a\tb", "   ", " ", "", "\t", "# h", "> q", "[x]: /u"]))
    for _lbl, _s in [("empty", ""), ("spaces", "   "), ("single-space", " "), ("tab-only", "\t")]:
        _blank_raised = False
        try:
            _md_safe_summary(_s, " (WL-4)", _marko)
        except _UnsafeSummary:
            _blank_raised = True
        check("md-safe-summary-blank-fails-closed-" + _lbl, _blank_raised)

    _saved_load = _commonmark_headings._load_marko
    try:
        def _raise_load():
            raise _commonmark_headings.HeadingScanError(
                "forced unavailable", _commonmark_headings.REASON_VENDOR_UNREADABLE)
        _commonmark_headings._load_marko = _raise_load
        check("bullet-marko-unavailable-full-escape",
              _bullet(entry(4, "added", "[x]: /url"), []) == "- " + _md_full_escape("[x]: /url") + " (WL-4)")
    finally:
        _commonmark_headings._load_marko = _saved_load

    # 12b: FIX 1 END-TO-END -- a summary that cannot render literally (a NUL, which passes validate_worklog
    # but Marko replaces with U+FFFD) makes draft_entry FAIL CLOSED to a CANNOT_EVALUATE naming the offending
    # WL-id, at BOTH _span_draft call sites (unreleased tail and per-release span), Marko-available and
    # Marko-unavailable; and _bullet fails closed on a blank summary (the defensive seam upstream rejects).
    worklog_nul = {"schema": 1, "entry": [
        entry(1, "added", "first release"), entry(2, "added", "second release"),
        entry(3, "fixed", "a fix"), entry(4, "added", "a\x00b")]}
    st, _t, _n, findings = draft_entry(vbase, worklog_nul, None, cl, "unreleased")
    check("nul-summary-cannot-eval-marko-available",
          st == CANNOT_EVALUATE and any("WL-4" in f and "literal" in f for f in findings))
    _saved_load2 = _commonmark_headings._load_marko
    try:
        def _raise_load2():
            raise _commonmark_headings.HeadingScanError(
                "forced unavailable", _commonmark_headings.REASON_VENDOR_UNREADABLE)
        _commonmark_headings._load_marko = _raise_load2
        st, _t, _n, findings = draft_entry(vbase, worklog_nul, None, cl, "unreleased")
        check("nul-summary-cannot-eval-marko-unavailable",
              st == CANNOT_EVALUATE and any("WL-4" in f and "control character" in f for f in findings))
    finally:
        _commonmark_headings._load_marko = _saved_load2
    # the OTHER _span_draft site: a NUL in a per-release span (WL-1) also fails closed, naming WL-1.
    worklog_nul1 = {"schema": 1, "entry": [
        entry(1, "added", "a\x00b"), entry(2, "added", "second release"),
        entry(3, "fixed", "a fix"), entry(4, "added", "a feature")]}
    st, _t, _n, findings = draft_entry(vbase, worklog_nul1, None, cl, "1.0.0")
    check("nul-summary-single-span-cannot-eval",
          st == CANNOT_EVALUATE and any("WL-1" in f for f in findings))
    # _bullet fails closed on a blank summary, carrying this entry's WL-id (validate_worklog rejects blank
    # upstream, so this defensive seam is exercised directly at _bullet, never reaching draft_entry).
    for _lbl, _s in [("four-space", "    "), ("single-space", " "), ("empty", "")]:
        _bullet_wl = None
        try:
            _bullet(entry(4, "added", _s), [])
        except _UnsafeSummary as _exc:
            _bullet_wl = _exc.wl_id
        check("bullet-blank-fails-closed-" + _lbl, _bullet_wl == "WL-4")
    # the finding helper names both the offending WL-id and the reason.
    _uf = _unsafe_finding(_UnsafeSummary("SOME REASON TEXT", wl_id="WL-42"))
    check("unsafe-finding-names-wl-id", "WL-42" in _uf and "SOME REASON TEXT" in _uf)

    # 13: FIX 2 -- U5's malformed-heading findings are propagated fail-closed, never discarded. A rollup over
    # a changelog with a malformed bare '##' between two valid entries (content silently dropped), and a
    # freeze digest whose target heading is malformed, are each CANNOT-EVALUATE naming the finding
    # (check-fails-closed-on-unreadable; no-concealed-failure).
    cl_malformed = ("# Changelog\n\n## 1.1.0 (2026-06-15)\n\n### Added\n- second release (WL-2)\n\n"
                    "##\n\ncontent under a bare heading that would be silently dropped\n\n"
                    "## 1.0.0 (2026-06-01)\n\n### Added\n- first release (WL-1)\n")
    st, _t, _n, findings = draft_entry(vbase, worklog, None, cl_malformed, "1.0.0..1.1.0")
    check("rollup-malformed-heading-cannot-eval",
          st == CANNOT_EVALUATE and any("malformed entry heading" in f for f in findings))
    check("freeze-malformed-heading-cannot-eval",
          _freeze_digest_of("# Changelog\n\n## 1.0.0 NOT-A-DATE\n\n### Added\n- a (WL-1)\n", "1.0.0").status
          == CANNOT_EVALUATE)
    check("freeze-bare-heading-cannot-eval",
          _freeze_digest_of("# Changelog\n\n## 1.0.0 (2026-06-01)\n\n- a\n\n##\n\ntail\n", "1.0.0").status
          == CANNOT_EVALUATE)

    if failures:
        print("OPF-ABSORB SELF-TEST: FAIL ({} of {} checks failed)".format(len(failures), checked))
        for f in failures:
            print("  FAILED: {}".format(f))
        return 1
    print("OPF-ABSORB SELF-TEST: PASS ({} draft, enrichment, rollup, and store-resolution checks)".format(
        checked))
    return 0


def _parse_cli(args):
    """Parse the module CLI, fail-closed. Returns (kind, root, covers, mode, error): `kind` is "self-test"
    or "run"; on "run", `root` defaults to "." and `covers` to `unreleased`, and `mode` is "draft" or
    "freeze-digest". A missing/empty --root or --covers value, an unknown argument, or --self-test combined
    with anything else is a usage error (guard-input-soundness); --self-test is honoured only alone."""
    if args == ["--self-test"]:
        return "self-test", None, None, None, None
    root = "."
    covers = UNRELEASED
    mode = "draft"
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--root":
            if i + 1 >= len(args):
                return None, None, None, None, "--root requires a value"
            root = args[i + 1]
            if not root.strip():
                return None, None, None, None, "--root requires a non-empty value"
            i += 2
            continue
        if arg == "--covers":
            if i + 1 >= len(args):
                return None, None, None, None, "--covers requires a value"
            covers = args[i + 1]
            if not covers.strip():
                return None, None, None, None, "--covers requires a non-empty value"
            i += 2
            continue
        if arg == "--freeze-digest":
            mode = "freeze-digest"
            i += 1
            continue
        return None, None, None, None, "unknown argument {!r}".format(arg)
    return "run", root, covers, mode, None


def main():
    kind, root, covers, mode, error = _parse_cli(sys.argv[1:])
    if error is not None:
        print("opf absorb: {} (fail-closed)".format(error), file=sys.stderr)
        return EXIT[CANNOT_EVALUATE]
    if kind == "self-test":
        return self_test()
    return run(root, covers, mode)


if __name__ == "__main__":
    sys.exit(main())
