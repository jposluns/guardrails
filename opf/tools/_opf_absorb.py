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
store, an unreadable/unparseable/inconsistent ledger, a malformed manifest, an unreadable CHANGELOG.md, a
`[types.done]`-declared but absent/malformed `done.index.toml`, or a covers token that names a rotated span
is a distinct CANNOT-EVALUATE (exit 2) that STOPS, never a silent partial draft. A non-adopter root is
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
import os
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
                         "entry links the receipt directly (a worklog row for the completion may be "
                         "missing)".format(dn, bi))
    for n in per_entry:
        per_entry[n] = sorted(set(per_entry[n]))
    return per_entry, sorted(set(notes))


def _bullet(entry, tokens):
    """One curated-draft bullet for a worklog entry: `- <summary> (<WL-id>[; closes <token>, ...])`. The
    summary is a validated single-line string (validate_worklog VALID) and the WL-id / closes tokens are
    validated id shapes, so the bullet cannot introduce a spurious `## ` entry boundary."""
    tag = entry.get("id")
    summary = entry.get("summary", "")
    if tokens:
        return "- {} ({}; closes {})".format(summary, tag, ", ".join(tokens))
    return "- {} ({})".format(summary, tag)


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
    """The body of an existing CHANGELOG entry (its bytes minus the heading line, with per-line trailing
    whitespace and surrounding blank lines trimmed). Used to roll a range draft up from the per-release
    entries it replaces, so a rollup draws from the tier directly below (spec 6.4), never the raw worklog."""
    nl = entry_text.find("\n")
    body = entry_text[nl + 1:] if nl != -1 else ""
    return "\n".join(line.rstrip() for line in body.split("\n")).strip("\n")


# --- the pure drafting core --------------------------------------------------------------------------

def draft_entry(version_data, worklog_data, done_records, changelog_text, covers,
                registered_vendors=frozenset(), done_enabled=True):
    """Draft ONE candidate CHANGELOG entry for `covers`. Returns (status, draft_text, notes, findings):
    OK with the entry str on success, else CANNOT_EVALUATE with a fail-closed message. The ledgers are
    validated here (fail-closed CANNOT_EVALUATE, the U3 seam), so the draft heading always parses under the
    gate. `done_records` is the validated receipt list, or None when the type is not enabled; `changelog_text`
    is consulted ONLY for a range rollup (tier-below sourcing). This is the pure core the store-level
    `evaluate` and the self-test drive; it reads no files and mutates nothing."""
    vv = validate_version(version_data)
    if vv.status != VALID:
        return (CANNOT_EVALUATE, None, [],
                ["cannot draft: version.toml does not validate against the release-ledger schema (run the "
                 "version gate first; drafting requires a consistent ledger)"] + vv.findings)
    wv = validate_worklog(worklog_data, registered_vendors=registered_vendors)
    if wv.status != VALID:
        return (CANNOT_EVALUATE, None, [],
                ["cannot draft: worklog.toml does not validate"] + wv.findings)
    by_id, id_findings = _entries_by_id(worklog_data)
    if id_findings:
        return CANNOT_EVALUATE, None, [], ["cannot draft: worklog ids are malformed"] + id_findings

    releases = vv.releases
    ledger_versions = [r.get("version") for r in releases if isinstance(r, dict) and "version" in r]
    parsed, err = _parse_covers(covers, ledger_versions)
    if err is not None:
        return CANNOT_EVALUATE, None, [], ["cannot draft: covers token {!r}: {}".format(covers, err)]
    kind = parsed[0]

    if kind == "unreleased":
        try:
            tail = tail_ids(releases, list(by_id.keys()))
        except ReleaseError as exc:
            return CANNOT_EVALUATE, None, [], ["cannot draft the unreleased tail: {}".format(exc)]
        span_entries = [by_id[n] for n in tail]
        text, notes = _span_draft("## unreleased", span_entries, done_records, done_enabled)
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
            missing = [n for n in range(span[0], span[1] + 1) if n not in by_id]
            if missing:
                return (CANNOT_EVALUATE, None, [],
                        ["cannot draft {}: worklog {} covered by its span {} not in the active worklog "
                         "(rotated to the archive; spec 12); archive-composed drafting is deferred".format(
                             version, ", ".join("WL-{}".format(n) for n in missing),
                             "are" if len(missing) > 1 else "is")])
            span_entries = [by_id[n] for n in range(span[0], span[1] + 1)]
        date = row.get("date")
        suffix = " ({})".format(date[:10]) if isinstance(date, str) and len(date) >= 10 else ""
        text, notes = _span_draft("## {}{}".format(version, suffix), span_entries, done_records, done_enabled)
        return OK, text, notes, []

    # range rollup: draw from the EXISTING per-release entries in range, never the raw worklog (spec 6.4).
    lo, hi = parsed[1]
    versions_in_range = ledger_versions[lo:hi + 1]
    try:
        cl_entries, _findings = _opf_changelog._changelog_entries(changelog_text)
    except _commonmark_headings.HeadingScanError as exc:
        return (CANNOT_EVALUATE, None, [],
                ["cannot draft {}: CHANGELOG.md heading scan failed ({}): {}".format(
                    covers, exc.reason, exc)])
    except UnicodeEncodeError as exc:
        return (CANNOT_EVALUATE, None, [],
                ["cannot draft {}: CHANGELOG.md is not encodable as UTF-8 ({})".format(covers, exc)])
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
    nothing. Fail-closed when the entry is absent, ambiguous, or the scan fails."""
    try:
        entries, _findings = _opf_changelog._changelog_entries(changelog_text)
    except _commonmark_headings.HeadingScanError as exc:
        return AbsorbResult(CANNOT_EVALUATE, findings=[
            "cannot compute the freeze digest: CHANGELOG.md heading scan failed ({}): {}".format(
                exc.reason, exc)])
    except UnicodeEncodeError as exc:
        return AbsorbResult(CANNOT_EVALUATE, findings=[
            "cannot compute the freeze digest: CHANGELOG.md is not encodable as UTF-8 ({})".format(exc)])
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
    draft bytes unchanged, still OK); a rotated span, a missing rollup source, a malformed covers token, and
    an empty tail; stdout purity (the draft parses under U5 as exactly one entry whose token round-trips
    _parse_covers); the curated-flow tie to the live gates (draft integrates and run_gates PASS; a published
    edit without re-publish -> FINDING); the freeze-digest assist equal to U5's freeze_digest; the CLI
    parser fail-closed cases; and end-to-end store resolution (NOT-APPLICABLE, cannot-evaluate, OK, done
    enabled/enriched, done not declared + note, malformed done index, freeze-digest, exit-map)."""
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

        # run() maps a store-level status to the process exit code through EXIT.
        check("disk-run-ok-exit-0",
              run(str(build_store(version_disk, worklog_disk, cl_disk, manifest_wl))) == 0)
        check("disk-run-not-applicable-exit-0", run(str(na_root)) == 0)
        check("disk-run-cannot-eval-exit-2",
              run(str(build_store("bad [[", worklog_disk, cl_disk, manifest_wl))) == 2)
    finally:
        shutil.rmtree(base_dir, ignore_errors=True)

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
