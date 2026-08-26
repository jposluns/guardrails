#!/usr/bin/env python3
"""Crosswalk gate (VER-CORE 8.2, 8.4, 8.5, 8.6, 9.1). Offline, stdlib only, fail-closed.

HONESTY LABEL (disclosed on every surface): the 8.5 semantic verdict and the 8.2 completeness verdict
are HUMAN-JUDGMENT. This gate proves the verdict fields are PRESENT, well-formed, carry the fixed token,
and name a reviewer distinct from the author with a distinct declared family; it CANNOT prove that a real
cross-family review occurred. The upgrade to authenticated result records is deferred to the adopter-
experience report envelope (spec lines 1199 to 1202). The archive/quote chain (leg 2, 3, 6) is the anti-
self-certification property: a quote must be a verbatim substring of the successor clause's DECLARED
canonical-text, whose predecessor mirror is span-consistent with the immutable archived bytes.

CLASS-C RESIDUALS (disclosed, deferred to Step 7 / pin.py, post-1.0.0, out of the VC-6 slice):
  D5: the successor inventory is checked for clause-id uniqueness and per-row self-consistency (a declared
      span is 1-based with start <= end; a declared source-digest is 64-hex), and the quote is proven a
      verbatim substring of the row's DECLARED canonical-text. It is NOT resolved in the PINNED RELEASE, nor
      is the row's span-and-digest validated against that release's sources, so an adopter altering both a
      successor's canonical-text and its quote consistently is caught only by the pinned-release binding.
  D6: the gate validates STRUCTURAL completeness of the migration/archive state (a crosswalk requires its
      archive and the reviewed inventory to be present) but does NOT parse pin.toml for the migration MODE.

Exit convention: 0 clean, OR NOT APPLICABLE only when .aiqt/migration/, .aiqt/archive/, AND a migration-
mode pin are ALL structurally absent (a repo that never adopted); 1 a real finding; 2 malformed input, a
read error, or PARTIAL migration state (partial state is malformed, never dormant).

  check_crosswalk.py [--root DIR]   run the legs against an install (NA when not adopted)
  check_crosswalk.py --self-test    synthetic-tree honesty invariants
"""
import hashlib
import os
import re
import stat
import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    sys.exit("error: check_crosswalk.py requires Python 3.11+ (tomllib).")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _gen_common import repo_root  # noqa: E402
import _journal  # noqa: E402
# the SAME 9.1 component computation the engine binds cutovers to, and the SAME validated terminal
# classification the engine uses (fix #3), so the gate never selects an invalid or non-cutover terminal.
from migrate import components, _validated_completed_cutover  # noqa: E402

MIGRATION_REL = ".aiqt/migration"
ARCHIVE_REL = ".aiqt/archive"
PIN_REL = ".aiqt/pin.toml"
CROSSWALK_REL = ".aiqt/migration/crosswalk.toml"
INVENTORY_REL = ".aiqt/migration/successor-inventory.toml"
JOURNAL_REL = ".aiqt/migration/journal"
PRED_ID_RE = re.compile(r"^pre-([0-9a-f]{12})\.([1-9][0-9]*)$")
HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
_VERDICT_SEMANTIC = "equal-or-stronger"
_VERDICT_COMPLETE = "complete"


class GateError(Exception):
    """A fail-closed condition (malformed input, unreadable state, partial adoption): exit 2."""


def _load_toml(path):
    try:
        with open(path, "rb") as fh:
            return tomllib.load(fh)
    except FileNotFoundError:
        raise GateError("required input {} is absent".format(path))
    except (OSError, ValueError, tomllib.TOMLDecodeError) as exc:
        raise GateError("cannot read {} ({})".format(path, exc))


def _validate_row_container(doc, where, keys):
    """codex hardening: every array-of-tables the legs iterate MUST be a list of tables. A non-list value,
    or a non-dict row inside it, is a GateError (exit 2), never an uncaught AttributeError when a leg calls
    row.get(...) on a bare string or int."""
    for key in keys:
        val = doc.get(key)
        if val is None:
            continue
        if not isinstance(val, list):
            raise GateError("{}: [[{}]] must be an array of tables".format(where, key))
        for i, row in enumerate(val):
            if not isinstance(row, dict):
                raise GateError("{}: [[{}]] row {} is not a table".format(where, key, i))


# --- legs (each returns a list of finding strings; a GateError is exit 2) ------------------------------

def check_archive(cw, root):
    """8.2 archive-row schema (fix #6), immutability, and predecessor-id type floor (fix #5, C5). Per
    [[archive-file]] row: archive-sha256 is a 64-hex LOWERCASE digest resolving to <archive>/<sha>/payload
    whose recomputed digest equals it (immutability); the 8.2 REQUIRED fields legacy-path and owner are
    present and well-formed (8.2 spec: path, raw hash, predecessor-clause-ids, owner); a recorded size, when
    present, equals the archived payload length; and predecessor-clause-ids is a LIST of nonempty strings
    (a non-list or non-string element is a GateError exit 2, so a bare string can never satisfy membership
    as a substring) with NO duplicates, whose SET equals the exact set of [[predecessor]] ids declaring
    this archive hash (a phantom or missing id is a finding). An unreadable payload is fail-closed."""
    findings = []
    declared_by_hash = {}                                 # archive-sha256 -> set of [[predecessor]] ids (C5)
    for prow in cw.get("predecessor", []):
        psha, pid = prow.get("archive-sha256"), prow.get("clause-id")
        if isinstance(psha, str) and isinstance(pid, str):
            declared_by_hash.setdefault(psha, set()).add(pid)
    for row in cw.get("archive-file", []):
        sha = row.get("archive-sha256")
        if not (isinstance(sha, str) and HEX64_RE.match(sha)):
            findings.append("an archive-file row has no 64-hex lowercase archive-sha256")
            continue
        lp = row.get("legacy-path")
        if not (isinstance(lp, str) and lp.strip()):
            findings.append("archive-file {}: legacy-path is absent or not a non-empty string (8.2)"
                            .format(sha))
        owner = row.get("owner")
        if not ((isinstance(owner, str) and owner.strip())
                or (isinstance(owner, int) and not isinstance(owner, bool) and owner >= 0)):
            findings.append("archive-file {}: owner is absent or not a non-empty string or non-negative "
                            "uid (8.2)".format(sha))
        # C5 TYPE FLOOR: predecessor-clause-ids MUST be a list of nonempty strings (else exit 2), so no
        # bare string can satisfy the bidirectional membership check as a substring.
        pcids = row.get("predecessor-clause-ids")
        if not isinstance(pcids, list) or not all(isinstance(x, str) and x for x in pcids):
            raise GateError("archive-file {}: predecessor-clause-ids must be a list of nonempty strings "
                            "(C5 type floor; a bare string would satisfy membership as a substring)"
                            .format(sha))
        if len(pcids) != len(set(pcids)):
            findings.append("archive-file {}: predecessor-clause-ids has duplicate ids (C5)".format(sha))
        if set(pcids) != declared_by_hash.get(sha, set()):
            findings.append("archive-file {}: predecessor-clause-ids {} does not equal the exact set of "
                            "[[predecessor]] ids declaring this hash {} (phantom or missing id, C5)"
                            .format(sha, sorted(set(pcids)), sorted(declared_by_hash.get(sha, set()))))
        data = _read_archive_payload(root, sha)           # G8: no-follow read (a symlinked payload/ancestor is refused)
        if hashlib.sha256(data).hexdigest() != sha:
            findings.append("{}/{}/payload: archived bytes do not match their recorded hash"
                            .format(root / ARCHIVE_REL, sha))
        size = row.get("size")
        if size is not None and (isinstance(size, bool) or not isinstance(size, int)
                                 or size != len(data)):
            findings.append("archive-file {}: recorded size {!r} does not equal the archived payload "
                            "length {} (8.2)".format(sha, size, len(data)))
    return findings


def _read_all_fd(fd):
    chunks = []
    while True:
        block = os.read(fd, 1 << 20)
        if not block:
            break
        chunks.append(block)
    return b"".join(chunks)


def _open_archive_dir(root):
    """G8: open <root>/.aiqt/archive by opening the install root once and walking each ancestor component
    (.aiqt, archive) beneath it through an O_DIRECTORY|O_NOFOLLOW handle, so no symlinked ANCESTOR can
    redirect the archive read outside the tree. Returns the archive dir fd (caller closes). GateError
    (fail-closed) on a symlinked ancestor, a non-directory, or an I/O error."""
    try:
        fd = os.open(str(root), os.O_RDONLY | os.O_DIRECTORY)
    except OSError as exc:
        raise GateError("cannot open install root {} ({}); fail-closed".format(root, exc))
    try:
        for comp in ARCHIVE_REL.split("/"):
            nxt = os.open(comp, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = nxt
    except OSError as exc:
        os.close(fd)
        raise GateError("cannot open archive path component under {} through a no-follow handle ({}); a "
                        "symlinked ancestor is refused (8.2)".format(root, exc))
    return fd


def _read_archive_payload(root, sha):
    """G8 (8.2): read <root>/.aiqt/archive/<sha>/payload by opening the install root once and walking every
    component (.aiqt, archive, <sha>, payload) beneath it through NO-FOLLOW handles, so neither a symlinked
    ANCESTOR (.aiqt or archive) nor a symlinked <sha> entry or payload (which target-following
    exists()/read_bytes() would silently traverse to mutable external bytes) can redirect the read outside
    the tree. Each directory is opened O_DIRECTORY|O_NOFOLLOW and the payload O_NOFOLLOW, and the opened fd
    is fstat'd to require a REGULAR file before its bytes are hashed (the predecessor is PERMANENTLY retained
    in the archive as immutable bytes, never a redirectable link). GateError (fail-closed) on any symlink, a
    non-regular payload, or an I/O error."""
    archfd = _open_archive_dir(root)
    try:
        try:
            shafd = os.open(sha, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=archfd)
        except OSError as exc:
            raise GateError("cannot open archive entry {}/{} through a no-follow handle ({}); a symlinked "
                            "archive entry is refused (8.2)".format(root / ARCHIVE_REL, sha, exc))
        try:
            try:
                pfd = os.open("payload", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=shafd)
            except OSError as exc:
                raise GateError("cannot open archived payload for {} through a no-follow handle ({}); a "
                                "symlinked payload is refused (8.2)".format(sha, exc))
            try:
                if not stat.S_ISREG(os.fstat(pfd).st_mode):
                    raise GateError("archived payload for {} is not a regular file (8.2)".format(sha))
                return _read_all_fd(pfd)
            finally:
                os.close(pfd)
        finally:
            os.close(shafd)
    finally:
        os.close(archfd)


def _archived_text(root, sha):
    try:
        return _read_archive_payload(root, sha).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise GateError("cannot decode archived payload for {} ({})".format(sha, exc))


def check_predecessors(cw, root):
    """8.2/8.3/7.2 (C5): predecessor ids well-formed in the pre-<archive-hash-prefix>.<ordinal> namespace,
    prefix consistent with the row's archive hash, and unique; EVERY predecessor MUST carry a 64-hex
    LOWERCASE sha256 (a finding when absent or malformed, NEVER a silent skip that lets an empty hash
    bypass the span gate); source-digest equals the archive hash; the hash resolves BIDIRECTIONALLY to a
    declared [[archive-file]] row (its sha is declared AND that row lists this predecessor); the archived
    payload's digest is RECOMPUTED and must equal the declared sha (archive identity, never row-to-row
    equality); and the SPAN-AND-DIGEST TIE runs whenever the recomputed digest matches (a digest mismatch
    is itself a finding and short-circuits the span check, since a span against the wrong bytes is moot),
    never bypassed by an empty or malformed hash (7.2: the source span is a TIGHT whole-line window
    [start-line, end-line], and the canonical text is byte-exactly THAT window of the immutable archived
    bytes, never a mere substring)."""
    findings, seen = [], set()
    archive_index = {}                                    # archive-sha256 -> [[archive-file]] rows (C5)
    for arow in cw.get("archive-file", []):
        ash = arow.get("archive-sha256")
        if isinstance(ash, str):
            archive_index.setdefault(ash, []).append(arow)
    for row in cw.get("predecessor", []):
        pid = row.get("clause-id", "")
        sha = row.get("archive-sha256", "")
        # fullmatch, not match: `$` matches before a trailing LF, so a `pre-<hex>.<ordinal>\n` id would slip
        # past match() yet is malformed; fullmatch requires the WHOLE string to be the exact 8.3 shape.
        m = PRED_ID_RE.fullmatch(pid)
        if not m:
            findings.append("predecessor id {!r} is not in the 8.3 pre-<prefix>.<ordinal> namespace"
                            .format(pid))
        if pid in seen:
            findings.append("duplicate predecessor id {!r}".format(pid))
        seen.add(pid)
        # C5: a 64-hex lowercase archive-sha256 is REQUIRED; an absent/malformed hash is a finding and the
        # remaining hash-keyed checks cannot run (never a silent bypass of the span gate by "" == "").
        if not isinstance(sha, str) or not HEX64_RE.match(sha):
            findings.append("predecessor {!r}: archive-sha256 must be a 64-hex lowercase sha256 (absent or "
                            "malformed; the span gate is never bypassed by an empty hash)".format(pid))
            continue
        if m and m.group(1) != sha[:12]:
            findings.append("predecessor id {!r} hash-prefix disagrees with its archive-sha256".format(pid))
        if row.get("source-digest") != sha:
            findings.append("predecessor {!r}: source-digest must equal the archive-sha256 (8.2)".format(pid))
        # C5: resolve the hash bidirectionally to a declared [[archive-file]] row.
        arows = archive_index.get(sha, [])
        if not arows:
            findings.append("predecessor {!r}: archive-sha256 resolves to no declared [[archive-file]] row"
                            .format(pid))
        elif not any(pid in (a.get("predecessor-clause-ids") or []) for a in arows):
            findings.append("predecessor {!r}: its [[archive-file]] row does not list it in "
                            "predecessor-clause-ids (bidirectional resolution)".format(pid))
        # C5: RECOMPUTE the resolved payload's digest (archive identity), never trust row-to-row equality.
        data = _read_archive_payload(root, sha)           # G8: no-follow read (a symlinked payload/ancestor is refused)
        if hashlib.sha256(data).hexdigest() != sha:
            findings.append("predecessor {!r}: recomputed archived payload digest does not equal its "
                            "archive-sha256 (archive identity)".format(pid))
            continue                                       # wrong bytes: a span check against them is moot
        text = row.get("canonical-text", "")
        if not text:
            findings.append("predecessor {!r}: empty canonical-text".format(pid))
        else:                                              # C5: ALWAYS run the span check (never behind elif sha)
            findings += _check_predecessor_span(pid, row, root, sha, text)
    return findings


def _whole_line_window(text, start, end):
    """The byte-exact whole-line window [start, end] (1-based inclusive) of text, or None when the range
    exceeds the file's actual content lines. Lines are split on LF (3.2 byte-literal, no normalization);
    the window runs from the start of start-line through the end of end-line, WITHOUT a trailing LF, so it
    is the canonical text the 7.2 tight window records. A terminating LF yields a trailing empty element
    that is NOT a real content line; end-line must never address that PHANTOM element beyond the file's
    actual content lines (claude N2), so a single trailing empty element is dropped from the addressable
    range."""
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines = lines[:-1]
    if start < 1 or end > len(lines):
        return None
    return "\n".join(lines[start - 1:end])


def _substring_offsets(window, text):
    """Every start offset (including overlapping) at which text occurs as a contiguous byte-exact substring
    of window. Overlapping matches are counted (find at i+1), so an ambiguous window is caught, never
    silently reduced to a single hit."""
    offsets, i = [], window.find(text)
    while i != -1:
        offsets.append(i)
        i = window.find(text, i + 1)
    return offsets


def _check_predecessor_span(pid, row, root, sha, text):
    """The 7.2 tight whole-line window (F-226, spec 7.2 which supersedes the round-10 'byte-identical to the
    span's content' wording): start-line and end-line are 1-based integers with start-line <= end-line, and
    the canonical-text occurs EXACTLY ONCE as a contiguous byte-exact SUBSTRING within the
    [start-line, end-line] whole-line window of the archived bytes, BEGINNING on start-line and ENDING on
    end-line (a tight window). Zero or more than one occurrence is a FAIL and the gate never picks the first
    occurrence. This is the 8.2 span-and-digest tie against the immutable archived bytes; a canonical-text
    that merely shares its first/last line with markdown (a common heading/list prefix) is a valid substring
    of the window, not a rejection. A malformed, missing, or out-of-range span is a finding (fail-closed
    toward FAIL); an unreadable archived payload fails closed (GateError, via _archived_text)."""
    start = row.get("start-line")
    end = row.get("end-line")
    if (not isinstance(start, int) or not isinstance(end, int)
            or isinstance(start, bool) or isinstance(end, bool)):
        return ["predecessor {!r}: start-line and end-line must be integers (7.2 source span)".format(pid)]
    if start < 1 or end < start:
        return ["predecessor {!r}: source span [{}, {}] is not a valid 1-based whole-line window "
                "(start-line >= 1 and start-line <= end-line)".format(pid, start, end)]
    window = _whole_line_window(_archived_text(root, sha), start, end)
    if window is None:
        return ["predecessor {!r}: source span [{}, {}] exceeds the archived file's line count"
                .format(pid, start, end)]
    offsets = _substring_offsets(window, text)
    if len(offsets) == 0:
        return ["predecessor {!r}: canonical-text is not a byte-exact substring of the archived lines "
                "[{}, {}] (span-and-digest disagreement, 8.2/7.2)".format(pid, start, end)]
    if len(offsets) > 1:
        return ["predecessor {!r}: canonical-text occurs {} times in the window [{}, {}]; 7.2 requires "
                "EXACTLY ONE occurrence (the gate never picks the first)".format(pid, len(offsets), start, end)]
    s = offsets[0]
    # BEGINS on start-line (no newline precedes the match in the window) and ENDS on end-line (no newline
    # follows the match), so the declared window is TIGHT rather than loosely enclosing the canonical text.
    if "\n" in window[:s] or "\n" in window[s + len(text):]:
        return ["predecessor {!r}: canonical-text does not begin on start-line {} and end on end-line {} "
                "(the declared window is not tight, 7.2)".format(pid, start, end)]
    return []


def check_completeness(cw):
    """8.2 completeness attestation (HUMAN-JUDGMENT), 8.5 identity model: the fixed verdict token, and the
    identity fields PRESENT before the distinctness checks so an OMITTED enumerator or enumerator-family
    cannot pass the anti-self-certification floor vacuously (reconciliation 6). Presence: enumerator,
    enumerator-family, reviewer, reviewer-family; THEN reviewer name-distinct from the enumerator and
    reviewer-family distinct from the enumerator-family."""
    er = cw.get("enumeration-review")
    if not isinstance(er, dict):
        return ["no [enumeration-review] completeness attestation (8.2)"]
    findings = []
    if er.get("verdict") != _VERDICT_COMPLETE:
        findings.append("completeness verdict missing or not the fixed token {!r}".format(_VERDICT_COMPLETE))
    # C6: each identity field must be a NON-EMPTY, NON-WHITESPACE string (a whitespace-only or non-string
    # value never passes vacuously); distinctness compares the STRIPPED canonical values.
    vals = {}
    for field in ("enumerator", "enumerator-family", "reviewer", "reviewer-family"):
        v = er.get(field)
        if not (isinstance(v, str) and v.strip()):
            findings.append("completeness review: {} is absent or not a non-empty string (identity floor, "
                            "8.2/8.5)".format(field))
        else:
            vals[field] = v.strip()
    if "enumerator" in vals and "reviewer" in vals and vals["reviewer"] == vals["enumerator"]:
        findings.append("completeness reviewer is identical to the enumerator")
    if ("enumerator-family" in vals and "reviewer-family" in vals
            and vals["reviewer-family"] == vals["enumerator-family"]):
        findings.append("completeness reviewer family is identical to the enumerator family")
    return findings


def check_zero_unmapped(cw):
    """8.4/8.2: computed as set(predecessor ids) - set(mapping predecessor ids), NEVER trusted from an
    asserted unmatched list."""
    pred = {r.get("clause-id") for r in cw.get("predecessor", [])}
    mapped = {m.get("predecessor-clause-id") for m in cw.get("mapping", [])}
    missing = sorted(x for x in (pred - mapped) if x)
    return ["predecessor {!r} has no mapping (zero-unmapped violation, 8.4)".format(p) for p in missing]


def check_mapping_referential_integrity(cw):
    """Bidirectional referential integrity (reconciliation 6): check_zero_unmapped covers the
    predecessor->mapping direction; this covers mapping->predecessor. Every mapping row's
    predecessor-clause-id MUST resolve to a declared [[predecessor]] row; a mapping citing a nonexistent
    predecessor is a finding."""
    pred_ids = {r.get("clause-id") for r in cw.get("predecessor", [])}
    findings = []
    for row in cw.get("mapping", []):
        pid = row.get("predecessor-clause-id")
        if pid not in pred_ids:
            findings.append("mapping cites predecessor {!r} which resolves to no predecessor row "
                            "(referential integrity)".format(pid))
    return findings


def check_quote(row, inventory):
    """8.4: the successor id resolves in the pinned inventory and the successor-quote is a non-empty
    verbatim contiguous substring of THAT clause's canonical text. A Coverage phrase is never a quote."""
    sid = row.get("successor-clause-id")
    clause = inventory.get(sid)
    if clause is None:
        return "successor id {!r} does not resolve in the pinned inventory".format(sid)
    q = row.get("successor-quote", "")
    if not q or q not in clause.get("canonical-text", ""):
        return "successor-quote for {!r} is not a verbatim contiguous substring of the canonical text" \
            .format(sid)
    return None


def check_verdict(row):
    """8.5/8.6 mechanical floor: the fixed token, and the identity fields PRESENT before the distinctness
    checks so an OMITTED author or author-family cannot pass the anti-self-certification floor vacuously
    (reconciliation 6: an absent author made reviewer!=author trivially true). Presence: author,
    author-family, reviewer, reviewer-family (the identity floor 8.5/8.6 requires); THEN reviewer
    name-distinct from the author and reviewer-family distinct from the author-family. rationale and
    reviewed-utc are schema fields the spec does not gate for presence, so they are not required here."""
    problems = []
    if row.get("semantic-verdict") != _VERDICT_SEMANTIC:
        problems.append("verdict token missing or not {!r}".format(_VERDICT_SEMANTIC))
    # C6: each identity field must be a NON-EMPTY, NON-WHITESPACE string (a whitespace-only value like
    # " " or a truthy non-string never passes vacuously); distinctness compares STRIPPED canonical values.
    vals = {}
    for field in ("author", "author-family", "reviewer", "reviewer-family"):
        v = row.get(field)
        if not (isinstance(v, str) and v.strip()):
            problems.append("{} is absent or not a non-empty string (identity floor, 8.5)".format(field))
        else:
            vals[field] = v.strip()
    if "author" in vals and "reviewer" in vals and vals["reviewer"] == vals["author"]:
        problems.append("reviewer is identical to the author")
    if ("author-family" in vals and "reviewer-family" in vals
            and vals["reviewer-family"] == vals["author-family"]):
        problems.append("reviewer family is identical to the author family")
    return problems


def check_inventory(inv_rows):
    """D5 (spec 1193/1196), the CHEAP part fixed within the VC-6 slice: the successor inventory's clause-ids
    are UNIQUE (a duplicate is a finding, never silently deduped to last-wins), and each row is
    SELF-CONSISTENT: a non-empty clause-id and canonical-text; and, WHERE PRESENT, a valid 7.2 source span
    (start-line and end-line 1-based integers with start-line <= end-line) and a 64-hex lowercase
    source-digest.

    DISCLOSED RESIDUAL (disclose-guard-residuals, class-c): this gate proves the successor-quote is a
    verbatim substring of the row's DECLARED canonical-text and that the row is internally well-formed; it
    does NOT resolve the successor clause in the PINNED RELEASE, nor validate the row's span-and-digest
    against that release's sources. Binding the inventory to a pinned release identity is Step-7 (pin.py)
    territory, OUT of the VC-6 slice, so an adopter who alters both a successor's canonical-text and the
    quote consistently is caught only by that pinned-release binding, a tracked post-1.0.0 hardening."""
    findings, seen = [], set()
    for i, row in enumerate(inv_rows):
        cid = row.get("clause-id")
        if not (isinstance(cid, str) and cid.strip()):
            findings.append("successor inventory row {}: clause-id is absent or not a non-empty string"
                            .format(i))
        else:
            if cid in seen:
                findings.append("successor inventory has a duplicate clause-id {!r} (uniqueness, 7.2)"
                                .format(cid))
            seen.add(cid)
        ct = row.get("canonical-text")
        if not (isinstance(ct, str) and ct):
            findings.append("successor inventory {!r}: canonical-text is absent or not a non-empty string"
                            .format(cid))
        # 7.2 span/digest self-consistency WHERE PRESENT (their TRUTH against the pinned release sources is
        # deferred to Step 7; see the disclosed residual above).
        start, end = row.get("start-line"), row.get("end-line")
        if start is not None or end is not None:
            if (not isinstance(start, int) or isinstance(start, bool)
                    or not isinstance(end, int) or isinstance(end, bool) or start < 1 or end < start):
                findings.append("successor inventory {!r}: a declared source span must be 1-based integers "
                                "with start-line <= end-line (7.2 self-consistency)".format(cid))
        sd = row.get("source-digest")
        if sd is not None and not (isinstance(sd, str) and HEX64_RE.match(sd)):
            findings.append("successor inventory {!r}: source-digest, when present, must be a 64-hex "
                            "lowercase sha256 (7.2 self-consistency)".format(cid))
    return findings


def check_mappings(cw, inventory):
    """8.4/8.5/8.6: per mapping row, the quote chain and the verdict floor. Folds and splits are covered
    because EACH successor carries its own full row (8.6 lines 1223 to 1226)."""
    findings = []
    for row in cw.get("mapping", []):
        pid = row.get("predecessor-clause-id")
        q = check_quote(row, inventory)
        if q:
            findings.append("mapping {}: {}".format(pid, q))
        for p in check_verdict(row):
            findings.append("mapping {}: {}".format(pid, p))
    return findings


def pointer_warnings(cw):
    """8.6 lines 1220 to 1222: a pointer that is absent, present, or DISAGREEING changes NO verdict.
    Disagreement is an author-facing WARNING on stderr; the mapping row governs. Returns warning
    strings (never findings)."""
    warnings = []
    mapped = {}
    for m in cw.get("mapping", []):
        mapped.setdefault(m.get("predecessor-clause-id"), set()).add(m.get("successor-clause-id"))
    for h in cw.get("pointer-hint", []):
        pid = h.get("predecessor-clause-id")
        expected = set(h.get("expected-successor-ids", []))
        if expected and expected != mapped.get(pid, set()):
            warnings.append("pointer for {!r} disagrees with the reviewed mapping (hint only, no verdict "
                            "change)".format(pid))
    return warnings


def check_unit_coverage(root, cw):
    """9.1 (C7): each executed (COMPLETE) cutover transaction MUST have covered a WHOLE crosswalk
    component. do_cutover binds a cutover to a real connected component (rejecting an unknown --unit) and
    records the component's mapped predecessor set in the INTENT header; this leg re-derives the canonical
    components from the crosswalk (the SAME components() the engine used) and FAILs a completed cutover
    whose named unit is not a component, or whose recorded component-predecessor set does not equal that
    component's mapped predecessor set (a per-file / partial-component promotion). un-adopt reversals are
    exempt (they reverse a prior cutover, not a component).

    DISCLOSED RESIDUAL (disclose-guard-residuals): the binding is at CLAUSE-ID granularity. The gate ties
    the transaction's DECLARED whole-component membership to the crosswalk's components; the data model
    carries no map from individual file OPS to clause-ids, so the gate does not cross-check each file op
    against a clause-id. An engine-produced transaction always records the full component (do_cutover
    derives it from components(), never a subset), so this catches an unknown unit and a tampered or
    hand-written partial-component header; a forged header claiming a whole component it did not touch is
    outside this clause-level binding, exactly as the journal's accident-recovery model is."""
    journal_root = root / JOURNAL_REL
    if not journal_root.is_dir():
        return []
    findings = []
    comps = components(cw)
    component_preds = {key: sorted({m.get("predecessor-clause-id") for m in rows})
                       for key, rows in comps.items()}
    component_succs = {key: sorted({m.get("successor-clause-id") for m in rows})
                       for key, rows in comps.items()}
    have_completed = False
    for entry in sorted(journal_root.iterdir()):
        if not entry.is_dir():
            continue
        # fix #3: route through the SINGLE validated classification. Only a genuinely COMPLETE cutover
        # ([INTENT, COMPLETE] AND header kind == "cutover") is coverage-gated; a mismatched-txn or
        # invalid-sequence journal fails closed (GateError), and a rolled-back, open, un-adopt, or other
        # non-cutover terminal is not mis-selected as a completed cutover.
        try:
            intent = _validated_completed_cutover(entry)
        except _journal.JournalError as exc:
            raise GateError("corrupt journal transaction {} ({})".format(entry.name, exc))
        if intent is None:
            continue
        have_completed = True
        header = intent.get("header", {})
        unit = header.get("unit")
        if not unit:
            findings.append("terminal transaction {} names no unit (9.1)".format(entry.name))
            continue
        if unit not in component_preds:
            findings.append("terminal transaction {} names unit {!r} which is not a connected component "
                            "of the crosswalk (9.1)".format(entry.name, unit))
            continue
        # C7 (fix #4): the recorded predecessor AND successor sets must each be a list of strings that
        # equals the component's canonical mapped set. A split-component cutover that omits a successor is
        # caught here, not only a missing predecessor.
        recorded_p = header.get("component-predecessors")
        recorded_s = header.get("component-successors")
        if not _is_str_list(recorded_p) or not _is_str_list(recorded_s):
            findings.append("terminal transaction {} records a non-list component-predecessors/successors "
                            "header (9.1)".format(entry.name))
            continue
        if sorted(recorded_p) != component_preds[unit]:
            findings.append("terminal transaction {} did not cover the whole component {!r}: its recorded "
                            "predecessor set {} does not equal the component's mapped predecessor set {} "
                            "(9.1)".format(entry.name, unit, sorted(recorded_p), component_preds[unit]))
        if sorted(recorded_s) != component_succs[unit]:
            findings.append("terminal transaction {} did not cover the whole component {!r}: its recorded "
                            "successor set {} does not equal the component's mapped successor set {} "
                            "(9.1)".format(entry.name, unit, sorted(recorded_s), component_succs[unit]))
    if have_completed and not cw.get("mapping"):
        findings.append("a completed cutover exists but the crosswalk has no mapping rows (9.1)")
    return findings


def _is_str_list(value):
    """A list of strings (C7 fix #4 type-checked equality): a header field that is not a list of strings
    cannot be trusted as a recorded component membership set."""
    return isinstance(value, list) and all(isinstance(x, str) for x in value)


# --- applicability + orchestration --------------------------------------------------------------------

def _present_or_fail(path, expect_dir):
    """fix #9: STRUCTURAL presence via os.lstat, never target-following .exists() (which reads a broken
    symlink, or a cannot-evaluate error, as absence). Returns True for a real (non-symlink) entry of the
    expected kind, False on genuine ENOENT (true structural absence). A symlink where a real dir/file is
    expected, an entry of the wrong kind, or any other OSError (permission / I/O: cannot-evaluate) is a
    GateError (exit 2, fail-closed), NEVER read as NA."""
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise GateError("cannot evaluate migration state at {} ({}); fail-closed (never NA)"
                        .format(path, exc))
    kind = "directory" if expect_dir else "file"
    if stat.S_ISLNK(st.st_mode):
        raise GateError("migration state path {} is a symlink where a real {} is expected; fail-closed "
                        "(never NA)".format(path, kind))
    if expect_dir and not stat.S_ISDIR(st.st_mode):
        raise GateError("migration state path {} is not a directory; fail-closed".format(path))
    if not expect_dir and not stat.S_ISREG(st.st_mode):
        raise GateError("migration state path {} is not a regular file; fail-closed".format(path))
    return True


def _migration_state_present(root):
    """(migration_dir, archive_dir, pin) STRUCTURAL-presence booleans via os.lstat (fix #9). TOTAL absence
    of all three is NA; ANY present with the crosswalk missing is PARTIAL (exit 2). A broken symlink or a
    cannot-evaluate at any of the three fails closed (GateError), never NA (structural absence must be
    proven, not conflated with an unreadable or symlinked path)."""
    return (_present_or_fail(root / MIGRATION_REL, True),
            _present_or_fail(root / ARCHIVE_REL, True),
            _present_or_fail(root / PIN_REL, False))


def run(root):
    mig, arc, pin = _migration_state_present(root)
    if not (mig or arc or pin):
        print("NOT APPLICABLE: no migration state (.aiqt/migration, .aiqt/archive, and a pin are all "
              "absent); this repo is not an adopter install")
        return 0
    cw_path = root / CROSSWALK_REL
    if not cw_path.exists():
        raise GateError("PARTIAL migration state: {} is present but {} is absent (partial state is "
                        "malformed, never dormant)".format(
                            MIGRATION_REL if mig else (ARCHIVE_REL if arc else PIN_REL), CROSSWALK_REL))
    # D6: STRUCTURAL COMPLETENESS. A crosswalk exists, so its immutable archive MUST be structurally present
    # too; an empty crosswalk with no archive is PARTIAL (fail-open no more), never a clean PASS. The
    # reviewed successor inventory is likewise required (its absence fails closed via _load_toml below). The
    # state matrix: all three of (migration dir, archive, pin) absent is NA; a crosswalk present with the
    # archive absent is PARTIAL (exit 2). DISCLOSED RESIDUAL (class-c): this validates STRUCTURAL
    # completeness of the migration/archive state; it does NOT parse pin.toml for the migration MODE, which
    # is Step-7 (pin.py) territory, deferred out of the VC-6 slice.
    if not arc:
        raise GateError("PARTIAL migration state: {} is present but the archive {} is structurally absent "
                        "(a crosswalk requires its immutable archive; partial state is malformed, never "
                        "dormant)".format(CROSSWALK_REL, ARCHIVE_REL))
    cw = _load_toml(cw_path)
    _validate_row_container(cw, CROSSWALK_REL, ("archive-file", "predecessor", "mapping", "pointer-hint"))
    inv_doc = _load_toml(root / INVENTORY_REL)
    _validate_row_container(inv_doc, INVENTORY_REL, ("clause",))
    inv_rows = inv_doc.get("clause", [])
    inventory = {r.get("clause-id"): r for r in inv_rows}
    findings = []
    findings += check_archive(cw, root)
    findings += check_predecessors(cw, root)
    findings += check_completeness(cw)
    findings += check_inventory(inv_rows)                  # D5: inventory uniqueness + row self-consistency
    findings += check_zero_unmapped(cw)
    findings += check_mapping_referential_integrity(cw)
    findings += check_mappings(cw, inventory)
    findings += check_unit_coverage(root, cw)
    for w in pointer_warnings(cw):
        print("warning: {}".format(w), file=sys.stderr)
    if findings:
        print("FAIL: {} crosswalk finding(s):".format(len(findings)), file=sys.stderr)
        for f in findings:
            print("  - " + f, file=sys.stderr)
        return 1
    print("PASS: crosswalk archive/quote/verdict fields are well-formed (verdict truth is HUMAN-JUDGMENT; "
          "this gate proves field presence and distinctness, never that a real cross-family review "
          "occurred). DISCLOSED (class-c, deferred to Step 7 / pin.py, post-1.0.0): the successor-quote is "
          "proven a verbatim substring of the row's DECLARED canonical-text, NOT resolved against the "
          "pinned release; and structural completeness of the migration/archive state is validated, but the "
          "pin is not parsed for the migration MODE.")
    return 0


def main():
    args = sys.argv[1:]
    if "--self-test" in args:
        return self_test()
    root = repo_root()
    if "--root" in args:
        root = Path(args[args.index("--root") + 1]).resolve()
    try:
        return run(root)
    except GateError as exc:
        print("error: {}; fail-closed".format(exc), file=sys.stderr)
        return 2


# --- self-test ----------------------------------------------------------------------------------------
# Honesty invariants over synthetic trees: a clean one-to-one, a fold, and a split PASS; each violation
# class FAILs (wrong archive hash, span/text/digest disagreement, unmapped predecessor, unresolvable
# successor, a non-verbatim quote, a cross-clause quote, a missing/same-author/same-family verdict, a
# missing completeness review); a pointer disagreement changes NO verdict; malformed input exits 2; and
# PARTIAL migration state (a migration dir with no crosswalk) exits 2 while TOTAL absence is NA.

_SUCC_A = "The successor obligation is at least as strong as before."
_SUCC_B = "A second successor clause that folds two predecessors together."
_LEGACY_ONE = "Old obligation one, its full paragraph of legacy text."
_LEGACY_TWO = "Old obligation two, a different legacy paragraph entirely."


def _sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _build_install(base, crosswalk_text, legacy_files, inventory_rows, tamper=None):
    """A synthetic adopter install: archive each legacy file, write the crosswalk, the successor
    inventory, and a pin (so state is non-NA). `tamper` mutates an archived payload after capture."""
    root = base
    (root / ARCHIVE_REL).mkdir(parents=True, exist_ok=True)
    (root / MIGRATION_REL).mkdir(parents=True, exist_ok=True)
    for text in legacy_files:
        sha = _sha(text)
        entry = root / ARCHIVE_REL / sha
        entry.mkdir(parents=True, exist_ok=True)
        (entry / "payload").write_text(text if tamper is None or tamper != sha else text + "X",
                                       encoding="utf-8")
    _write(root / CROSSWALK_REL, crosswalk_text)
    inv = ["schema-version = 1"]
    for cid, ctext in inventory_rows:
        inv += ["", "[[clause]]", 'clause-id = "{}"'.format(cid),
                'canonical-text = "{}"'.format(ctext.replace('"', '\\"'))]
    _write(root / INVENTORY_REL, "\n".join(inv) + "\n")
    _write(root / PIN_REL, 'schema-version = 1\nadoption-path = "migration"\n')
    return root


def _clean_crosswalk(fold=False, split=False):
    sha1, sha2 = _sha(_LEGACY_ONE), _sha(_LEGACY_TWO)
    p1 = "pre-{}.1".format(sha1[:12])
    p2 = "pre-{}.1".format(sha2[:12])
    rows = ['schema-version = 1', '',
            '[enumeration-review]', 'enumerator = "ann"', 'enumerator-family = "claude"',
            'verdict = "complete"', 'rationale = "all enumerated"', 'reviewer = "bob"',
            'reviewer-family = "codex"', 'reviewed-utc = "2026-08-26T00:00:00Z"', '',
            '[[archive-file]]', 'legacy-path = "one.md"', 'archive-sha256 = "{}"'.format(sha1),
            'size = {}'.format(len(_LEGACY_ONE.encode())), 'owner = 0',
            'predecessor-clause-ids = ["{}"]'.format(p1), '',
            '[[archive-file]]', 'legacy-path = "two.md"', 'archive-sha256 = "{}"'.format(sha2),
            'size = {}'.format(len(_LEGACY_TWO.encode())), 'owner = 0',
            'predecessor-clause-ids = ["{}"]'.format(p2), '',
            '[[predecessor]]', 'clause-id = "{}"'.format(p1), 'archive-sha256 = "{}"'.format(sha1),
            'start-line = 1', 'end-line = 1',
            'canonical-text = "{}"'.format(_LEGACY_ONE), 'source-digest = "{}"'.format(sha1), '',
            '[[predecessor]]', 'clause-id = "{}"'.format(p2), 'archive-sha256 = "{}"'.format(sha2),
            'start-line = 1', 'end-line = 1',
            'canonical-text = "{}"'.format(_LEGACY_TWO), 'source-digest = "{}"'.format(sha2), '']

    def mapping(pid, sid, quote):
        return ['[[mapping]]', 'predecessor-clause-id = "{}"'.format(pid),
                'successor-clause-id = "{}"'.format(sid),
                'successor-quote = "{}"'.format(quote), 'author = "carol"', 'author-family = "claude"',
                'semantic-verdict = "equal-or-stronger"', 'rationale = "stronger"',
                'reviewer = "dave"', 'reviewer-family = "gemini"', 'reviewed-utc = "2026-08-26T00:00:00Z"',
                '']
    if fold:                                              # two predecessors -> one successor
        rows += mapping(p1, "succ.a", _SUCC_A) + mapping(p2, "succ.a", _SUCC_A)
    elif split:                                           # one predecessor -> two successors
        rows += mapping(p1, "succ.a", _SUCC_A) + mapping(p1, "succ.b", _SUCC_B)
        rows += mapping(p2, "succ.a", _SUCC_A)
    else:
        rows += mapping(p1, "succ.a", _SUCC_A) + mapping(p2, "succ.a", _SUCC_A)
    return "\n".join(rows) + "\n", p1, p2, sha1, sha2


def self_test():
    import io
    import shutil
    import tempfile
    from contextlib import redirect_stdout, redirect_stderr

    inv = [("succ.a", _SUCC_A), ("succ.b", _SUCC_B)]

    def run_quiet(root):
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            try:
                return run(Path(root))
            except GateError:
                return 2

    try:
        tmp = Path(tempfile.mkdtemp(prefix="aiqt-check-crosswalk-selftest-"))
    except OSError as exc:
        print("SELF-TEST ERROR: no writable temporary directory: {}".format(exc), file=sys.stderr)
        return 2
    failures = []
    n = 0
    try:
        # Clean one-to-one, fold, split all PASS.
        for label, kw in (("one-to-one", {}), ("fold", {"fold": True}), ("split", {"split": True})):
            text, p1, p2, s1, s2 = _clean_crosswalk(**kw)
            root = _build_install(tmp / ("clean-" + label), text, [_LEGACY_ONE, _LEGACY_TWO], inv)
            if run_quiet(root) != 0:
                failures.append("clean {} expected PASS (0)".format(label))
            n += 1

        base_text, p1, p2, s1, s2 = _clean_crosswalk()

        # TOTAL absence -> NA (0); PARTIAL (migration dir, no crosswalk) -> 2.
        na = tmp / "na"
        na.mkdir()
        if run_quiet(na) != 0:
            failures.append("total absence expected NA (0)")
        partial = tmp / "partial"
        (partial / MIGRATION_REL).mkdir(parents=True)
        if run_quiet(partial) != 2:
            failures.append("partial migration state expected exit 2")
        n += 2

        def broken(mutator, expect=1):
            text = mutator(base_text)
            root = _build_install(tmp / ("bad{}".format(n)), text, [_LEGACY_ONE, _LEGACY_TWO], inv)
            return run_quiet(root) == expect

        # Wrong archive hash: tamper an archived payload after capture -> archive mismatch (1).
        tampered_root = _build_install(tmp / "tamper", base_text, [_LEGACY_ONE, _LEGACY_TWO], inv,
                                       tamper=s1)
        if run_quiet(tampered_root) != 1:
            failures.append("a tampered archived payload expected FAIL (1)")
        n += 1

        checks = [
            ("span/text disagreement",
             lambda t: t.replace('canonical-text = "{}"'.format(_LEGACY_ONE),
                                  'canonical-text = "text not in the archived bytes"', 1)),
            ("source-digest mismatch",
             lambda t: t.replace('source-digest = "{}"'.format(s1),
                                 'source-digest = "{}"'.format("0" * 64), 1)),
            ("unmapped predecessor",
             lambda t: t.replace('[[mapping]]\npredecessor-clause-id = "{}"'.format(p2),
                                 '[[mapping]]\npredecessor-clause-id = "unused.x"', 1)),
            ("unresolvable successor",
             lambda t: t.replace('successor-clause-id = "succ.a"',
                                 'successor-clause-id = "ghost.z"', 1)),
            ("non-verbatim quote",
             lambda t: t.replace('successor-quote = "{}"'.format(_SUCC_A),
                                 'successor-quote = "a quote that is not present"', 1)),
            ("same-author reviewer",
             lambda t: t.replace('reviewer = "dave"', 'reviewer = "carol"')),
            ("same-family reviewer",
             lambda t: t.replace('reviewer-family = "gemini"', 'reviewer-family = "claude"')),
            ("missing semantic verdict",
             lambda t: t.replace('semantic-verdict = "equal-or-stronger"', 'semantic-verdict = ""')),
            ("missing completeness review",
             lambda t: t.replace('verdict = "complete"', 'verdict = "partial"')),
            ("bad 8.3 predecessor id",
             lambda t: t.replace('clause-id = "{}"'.format(p1), 'clause-id = "notpre.1"')),
            # C3: an 8.3 id ending in a trailing LF passes `$` (which matches before a final newline) yet is
            # malformed. With a CONSISTENT LF across the predecessor row, its archive predecessor-clause-ids,
            # and its mapping, every membership check still resolves, so ONLY the fullmatch shape leg catches
            # it; a plain match() would let it through (exit 0). tomllib parses the `\n` escape as a newline.
            ("trailing-LF 8.3 predecessor id",
             lambda t: t.replace(p1 + '"', p1 + '\\n"')),
            # fix #5: a missing or out-of-range predecessor span (both would PASS the old substring check).
            ("missing predecessor span",
             lambda t: t.replace('start-line = 1\nend-line = 1\n', '', 1)),
            ("out-of-range predecessor span",
             lambda t: t.replace('start-line = 1\nend-line = 1', 'start-line = 5\nend-line = 9', 1)),
            # fix #6: an omitted identity field must FAIL the floor before any distinctness check.
            ("omitted mapping author",
             lambda t: t.replace('author = "carol"\n', '', 1)),
            ("omitted mapping author-family",
             lambda t: t.replace('author-family = "claude"\n', '', 1)),
            ("omitted mapping reviewer-family",
             lambda t: t.replace('reviewer-family = "gemini"\n', '', 1)),
            ("omitted completeness enumerator-family",
             lambda t: t.replace('enumerator-family = "claude"\n', '', 1)),
            # fix #6: a mapping citing a nonexistent predecessor fails referential integrity (the other
            # direction from zero-unmapped; every real predecessor here stays mapped).
            ("mapping cites nonexistent predecessor",
             lambda t: t + ('\n[[mapping]]\npredecessor-clause-id = "pre-ffffffffffff.9"\n'
                            'successor-clause-id = "succ.a"\nsuccessor-quote = "{}"\nauthor = "carol"\n'
                            'author-family = "claude"\nsemantic-verdict = "equal-or-stronger"\n'
                            'rationale = "x"\nreviewer = "dave"\nreviewer-family = "gemini"\n'
                            'reviewed-utc = "2026-08-26T00:00:00Z"\n'.format(_SUCC_A))),
        ]
        for label, mut in checks:
            root = _build_install(tmp / ("bad-" + label.replace("/", "_").replace(" ", "_")),
                                  mut(base_text), [_LEGACY_ONE, _LEGACY_TWO], inv)
            if run_quiet(root) != 1:
                failures.append("{} expected FAIL (1)".format(label))
            n += 1

        # fix #5: over a MULTI-LINE archived file, a canonical-text that is PRESENT in the file but NOT at
        # the declared whole-line span FAILs (the tightened window, not a mere substring); the same text at
        # its CORRECT span PASSes. This is the case the old substring check let through.
        multi = "First line of the legacy rule.\nSecond obligation line here.\nThird trailing line.\n"
        msha = _sha(multi)
        mp = "pre-{}.1".format(msha[:12])

        def _span_crosswalk(start, end):
            rows = ['schema-version = 1', '',
                    '[enumeration-review]', 'enumerator = "ann"', 'enumerator-family = "claude"',
                    'verdict = "complete"', 'rationale = "x"', 'reviewer = "bob"',
                    'reviewer-family = "codex"', 'reviewed-utc = "2026-08-26T00:00:00Z"', '',
                    '[[archive-file]]', 'legacy-path = "m.md"', 'archive-sha256 = "{}"'.format(msha),
                    'size = {}'.format(len(multi.encode())), 'owner = 0',
                    'predecessor-clause-ids = ["{}"]'.format(mp), '',
                    '[[predecessor]]', 'clause-id = "{}"'.format(mp),
                    'archive-sha256 = "{}"'.format(msha),
                    'start-line = {}'.format(start), 'end-line = {}'.format(end),
                    'canonical-text = "Second obligation line here."',
                    'source-digest = "{}"'.format(msha), '',
                    '[[mapping]]', 'predecessor-clause-id = "{}"'.format(mp),
                    'successor-clause-id = "succ.a"', 'successor-quote = "{}"'.format(_SUCC_A),
                    'author = "carol"', 'author-family = "claude"',
                    'semantic-verdict = "equal-or-stronger"', 'rationale = "x"', 'reviewer = "dave"',
                    'reviewer-family = "gemini"', 'reviewed-utc = "2026-08-26T00:00:00Z"', '']
            return "\n".join(rows) + "\n"

        bad_span = _build_install(tmp / "span-mismatch", _span_crosswalk(1, 1), [multi], inv)
        if run_quiet(bad_span) != 1:
            failures.append("canonical-text present but NOT at the declared span expected FAIL (1)")
        n += 1
        ok_span = _build_install(tmp / "span-ok", _span_crosswalk(2, 2), [multi], inv)
        if run_quiet(ok_span) != 0:
            failures.append("a correct declared whole-line span expected PASS (0)")
        n += 1

        # A pointer disagreement is a WARNING, not a finding: still PASS (0).
        ptext = base_text + ('\n[[pointer-hint]]\npredecessor-clause-id = "{}"\n'
                             'expected-successor-ids = ["succ.b"]\ncoverage = ""\ncarrier = "body"\n'
                             .format(p1))
        proot = _build_install(tmp / "pointer", ptext, [_LEGACY_ONE, _LEGACY_TWO], inv)
        if run_quiet(proot) != 0:
            failures.append("a disagreeing pointer must not change the verdict (expected PASS 0)")
        n += 1

        # Malformed TOML -> exit 2.
        malformed = _build_install(tmp / "malformed", "schema-version = 1\n[[mapping\n",
                                   [_LEGACY_ONE, _LEGACY_TWO], inv)
        if run_quiet(malformed) != 2:
            failures.append("malformed crosswalk TOML expected exit 2")
        n += 1

        # C5: an empty archive-sha256 (with an empty source-digest) BYPASSED the old span gate ("" == "");
        # it now FAILs. Target only the p1 predecessor row (clause-id is unique to it).
        def _c5(mut):
            root = _build_install(tmp / ("c5-{}".format(n)), mut(base_text), [_LEGACY_ONE, _LEGACY_TWO], inv)
            return run_quiet(root)

        empty_sha = (base_text
                     .replace('clause-id = "{}"\narchive-sha256 = "{}"'.format(p1, s1),
                              'clause-id = "{}"\narchive-sha256 = ""'.format(p1))
                     .replace('source-digest = "{}"'.format(s1), 'source-digest = ""'))
        if _c5(lambda _t: empty_sha) != 1:
            failures.append("C5: an empty archive-sha256 must FAIL (span gate never bypassed by '' == '')")
        n += 1
        # uppercase sha: not a 64-hex LOWERCASE value -> FAIL (old code reached an unreadable payload).
        upper_sha = (base_text
                     .replace('clause-id = "{}"\narchive-sha256 = "{}"'.format(p1, s1),
                              'clause-id = "{}"\narchive-sha256 = "{}"'.format(p1, s1.upper()))
                     .replace('source-digest = "{}"'.format(s1), 'source-digest = "{}"'.format(s1.upper())))
        if _c5(lambda _t: upper_sha) != 1:
            failures.append("C5: an uppercase archive-sha256 must FAIL (64-hex lowercase required)")
        n += 1
        # a predecessor whose hash is NOT listed in any [[archive-file]] row FAILs bidirectional resolution.
        not_in_archive = base_text.replace('predecessor-clause-ids = ["{}"]'.format(p1),
                                            'predecessor-clause-ids = ["pre-000000000000.9"]')
        if _c5(lambda _t: not_in_archive) != 1:
            failures.append("C5: a predecessor hash not listed in [[archive-file]] must FAIL")
        n += 1

        # C5 (claude N2): a trailing-newline PHANTOM end-line (end-line addressing the empty element a
        # terminating LF produces, beyond the file's real content lines) FAILs; the correct in-range span
        # passes. The old split-on-LF window let end-line = content+1 address that phantom.
        phantom = "Alpha\nBeta\n"                          # 2 content lines + terminating LF -> phantom "" 3rd
        phsha = _sha(phantom)
        php = "pre-{}.1".format(phsha[:12])

        def _phantom_crosswalk(end, canonical):
            rows = ['schema-version = 1', '',
                    '[enumeration-review]', 'enumerator = "ann"', 'enumerator-family = "claude"',
                    'verdict = "complete"', 'rationale = "x"', 'reviewer = "bob"',
                    'reviewer-family = "codex"', 'reviewed-utc = "2026-08-26T00:00:00Z"', '',
                    '[[archive-file]]', 'legacy-path = "p.md"', 'archive-sha256 = "{}"'.format(phsha),
                    'size = {}'.format(len(phantom.encode())), 'owner = 0',
                    'predecessor-clause-ids = ["{}"]'.format(php), '',
                    '[[predecessor]]', 'clause-id = "{}"'.format(php),
                    'archive-sha256 = "{}"'.format(phsha),
                    'start-line = 1', 'end-line = {}'.format(end),
                    'canonical-text = "{}"'.format(canonical),
                    'source-digest = "{}"'.format(phsha), '',
                    '[[mapping]]', 'predecessor-clause-id = "{}"'.format(php),
                    'successor-clause-id = "succ.a"', 'successor-quote = "{}"'.format(_SUCC_A),
                    'author = "carol"', 'author-family = "claude"',
                    'semantic-verdict = "equal-or-stronger"', 'rationale = "x"', 'reviewer = "dave"',
                    'reviewer-family = "gemini"', 'reviewed-utc = "2026-08-26T00:00:00Z"', '']
            return "\n".join(rows) + "\n"

        bad_phantom = _build_install(tmp / "phantom-bad", _phantom_crosswalk(3, "Alpha\\nBeta\\n"),
                                     [phantom], inv)
        if run_quiet(bad_phantom) != 1:
            failures.append("C5: a phantom trailing-newline end-line expected FAIL (1)")
        n += 1
        ok_phantom = _build_install(tmp / "phantom-ok", _phantom_crosswalk(2, "Alpha\\nBeta"),
                                    [phantom], inv)
        if run_quiet(ok_phantom) != 0:
            failures.append("C5: a correct in-range end-line expected PASS (0)")
        n += 1

        # C6: whitespace-only and non-string identity fields FAIL (they passed vacuously before); a
        # stripped-identical reviewer collides with the author. Valid rows still pass (covered by clean).
        for label, mut in (
                ("whitespace mapping author",
                 lambda t: t.replace('author = "carol"', 'author = " "')),
                ("non-string mapping author",
                 lambda t: t.replace('author = "carol"', 'author = 5')),
                ("stripped-identical reviewer",
                 lambda t: t.replace('reviewer = "dave"', 'reviewer = " carol "')),
                ("whitespace completeness enumerator",
                 lambda t: t.replace('enumerator = "ann"', 'enumerator = " "'))):
            root = _build_install(tmp / ("c6-" + label.replace(" ", "_")), mut(base_text),
                                  [_LEGACY_ONE, _LEGACY_TWO], inv)
            if run_quiet(root) != 1:
                failures.append("C6: {} expected FAIL (1)".format(label))
            n += 1

        # C7: a COMPLETE journal transaction must cover a WHOLE crosswalk component. A whole-component txn
        # PASSes; a partial (subset) membership or an unknown unit FAILs the 9.1 leg.
        def _write_journal_txn(root, name, header):
            td = root / JOURNAL_REL / name
            td.mkdir(parents=True, exist_ok=True)
            _journal.publish(td, _journal.F_INTENT, {"txn": name, "header": header, "ops": []})
            _journal.publish(td, _journal.F_COMPLETE, {"txn": name})

        c7ok = _build_install(tmp / "c7-ok", base_text, [_LEGACY_ONE, _LEGACY_TWO], inv)
        groups7 = components(_load_toml(c7ok / CROSSWALK_REL))
        ukey = sorted(groups7)[0]
        wpreds = sorted({m["predecessor-clause-id"] for m in groups7[ukey]})
        wsuccs = sorted({m["successor-clause-id"] for m in groups7[ukey]})
        _write_journal_txn(c7ok, "txn.ok", {"unit": ukey, "kind": "cutover",
                                            "component-predecessors": wpreds,
                                            "component-successors": wsuccs})
        if run_quiet(c7ok) != 0:
            failures.append("C7: a whole-component cutover expected PASS (0)")
        n += 1
        c7part = _build_install(tmp / "c7-partial", base_text, [_LEGACY_ONE, _LEGACY_TWO], inv)
        _write_journal_txn(c7part, "txn.partial", {"unit": ukey, "kind": "cutover",
                                                   "component-predecessors": wpreds[:1],
                                                   "component-successors": wsuccs})
        if run_quiet(c7part) != 1:
            failures.append("C7: a partial-component (missing predecessor) cutover expected FAIL (1)")
        n += 1
        # fix #4: full predecessor set but a MISSING successor also FAILs (successors gated too).
        c7succ = _build_install(tmp / "c7-succ", base_text, [_LEGACY_ONE, _LEGACY_TWO], inv)
        _write_journal_txn(c7succ, "txn.succ", {"unit": ukey, "kind": "cutover",
                                                "component-predecessors": wpreds,
                                                "component-successors": wsuccs[:-1]})
        if run_quiet(c7succ) != 1:
            failures.append("fix #4: a cutover omitting a component successor expected FAIL (1)")
        n += 1
        # fix #4: a non-list component-successors header fails closed as a clean finding via the
        # type-checked equality (without the _is_str_list guard, sorted() on a non-list would crash).
        c7type = _build_install(tmp / "c7-type", base_text, [_LEGACY_ONE, _LEGACY_TWO], inv)
        _write_journal_txn(c7type, "txn.type", {"unit": ukey, "kind": "cutover",
                                                "component-predecessors": wpreds,
                                                "component-successors": 5})
        if run_quiet(c7type) != 1:
            failures.append("fix #4: a non-list component-successors header expected FAIL (1)")
        n += 1
        c7unk = _build_install(tmp / "c7-unknown", base_text, [_LEGACY_ONE, _LEGACY_TWO], inv)
        _write_journal_txn(c7unk, "txn.unknown", {"unit": "not-a-component", "kind": "cutover",
                                                  "component-predecessors": [], "component-successors": []})
        if run_quiet(c7unk) != 1:
            failures.append("C7: an unknown-unit cutover expected FAIL (1)")
        n += 1
        # fix #3 (C2 in check_unit_coverage): a mismatched-txn terminal journal fails closed (exit 2),
        # routed through the same validated classification the engine uses, not a bare COMPLETE acceptance.
        c7mm = _build_install(tmp / "c7-mismatch", base_text, [_LEGACY_ONE, _LEGACY_TWO], inv)
        mmtd = c7mm / JOURNAL_REL / "txn.mismatch"
        mmtd.mkdir(parents=True, exist_ok=True)
        _journal.publish(mmtd, _journal.F_INTENT, {"txn": "A", "header": {"unit": ukey, "kind": "cutover",
                         "component-predecessors": wpreds, "component-successors": wsuccs}, "ops": []})
        _journal.publish(mmtd, _journal.F_COMPLETE, {"txn": "B"})
        if run_quiet(c7mm) != 2:
            failures.append("fix #3: a mismatched-txn terminal must fail the coverage leg closed (exit 2)")
        n += 1
        # fix #3: a completed NON-cutover terminal (kind != cutover) is NOT mis-gated as a cutover; it is
        # simply not coverage-gated, so a whole-component real cutover alongside it still PASSes.
        c7nc = _build_install(tmp / "c7-noncutover", base_text, [_LEGACY_ONE, _LEGACY_TWO], inv)
        _write_journal_txn(c7nc, "txn.other", {"unit": "irrelevant", "kind": "un-adopt",
                                               "component-predecessors": [], "component-successors": []})
        _write_journal_txn(c7nc, "txn.real", {"unit": ukey, "kind": "cutover",
                                              "component-predecessors": wpreds,
                                              "component-successors": wsuccs})
        if run_quiet(c7nc) != 0:
            failures.append("fix #3: a non-cutover terminal must not be mis-gated (real cutover PASSes)")
        n += 1

        # codex: a non-dict row container is a GateError (exit 2), never an uncaught AttributeError.
        badrow = _build_install(tmp / "badrow", 'schema-version = 1\narchive-file = ["x"]\n',
                                [_LEGACY_ONE, _LEGACY_TWO], inv)
        if run_quiet(badrow) != 2:
            failures.append("codex: a non-dict archive-file row expected exit 2")
        n += 1

        # fix #5 (C5 type floor): a STRING predecessor-clause-ids (not a list) fails CLOSED (exit 2), so a
        # bare "pre-<prefix>.1" can never satisfy the bidirectional membership check as a substring.
        str_pcids = base_text.replace('predecessor-clause-ids = ["{}"]'.format(p1),
                                      'predecessor-clause-ids = "{}"'.format(p1))
        if run_quiet(_build_install(tmp / "c5-str-pcids", str_pcids, [_LEGACY_ONE, _LEGACY_TWO], inv)) != 2:
            failures.append("fix #5: a non-list predecessor-clause-ids must fail closed (exit 2)")
        n += 1
        # fix #5: a PHANTOM id in the archive-file row (one no [[predecessor]] declares) fails the exact-set
        # equality; a MISSING id (a predecessor declaring the hash but absent from the row) fails too.
        phantom_id = base_text.replace('predecessor-clause-ids = ["{}"]'.format(p1),
                                       'predecessor-clause-ids = ["{}", "pre-000000000000.9"]'.format(p1))
        if run_quiet(_build_install(tmp / "c5-phantom", phantom_id, [_LEGACY_ONE, _LEGACY_TWO], inv)) != 1:
            failures.append("fix #5: a phantom archive-file predecessor-clause-id must FAIL (1)")
        n += 1
        missing_id = base_text.replace('predecessor-clause-ids = ["{}"]'.format(p1),
                                       'predecessor-clause-ids = []')
        if run_quiet(_build_install(tmp / "c5-missing", missing_id, [_LEGACY_ONE, _LEGACY_TWO], inv)) != 1:
            failures.append("fix #5: an archive-file row missing a declared predecessor id must FAIL (1)")
        n += 1
        # fix #6: an archive-file row missing the 8.2-required owner FAILs; a wrong recorded size FAILs.
        no_owner = base_text.replace('owner = 0\n', '', 1)
        if run_quiet(_build_install(tmp / "c6-no-owner", no_owner, [_LEGACY_ONE, _LEGACY_TWO], inv)) != 1:
            failures.append("fix #6: an archive-file row with no owner must FAIL (1)")
        n += 1
        bad_size = base_text.replace('size = {}'.format(len(_LEGACY_ONE.encode())), 'size = 999999', 1)
        if run_quiet(_build_install(tmp / "c6-bad-size", bad_size, [_LEGACY_ONE, _LEGACY_TWO], inv)) != 1:
            failures.append("fix #6: an archive-file recorded size that mismatches the payload must FAIL (1)")
        n += 1

        # fix #9: NA proves STRUCTURAL absence. A BROKEN SYMLINK where .aiqt/migration is expected is a
        # cannot-evaluate, not absence, so it fails closed (exit 2), never NA.
        brk = tmp / "brokenlink"
        (brk / ".aiqt").mkdir(parents=True)
        os.symlink(str(brk / "nonexistent-target"), str(brk / MIGRATION_REL))
        if run_quiet(brk) != 2:
            failures.append("fix #9: a broken symlink at .aiqt/migration must fail closed (exit 2, never NA)")
        n += 1

        # G7: the 7.2 tight window is an exact-ONCE SUBSTRING beginning on start-line and ending on end-line,
        # NOT the whole window. A canonical-text that shares its first/last line with markdown (a heading
        # prefix) is a valid substring and PASSes (the regression the '== window' check wrongly REJECTED);
        # zero or two occurrences FAIL; a loose (not-tight) window FAILs.
        g7file = "## The retention obligation\nThe system MUST retain every record.\n"
        g7sha = _sha(g7file)
        g7p = "pre-{}.1".format(g7sha[:12])

        def _g7_crosswalk(start, end, canonical):
            rows = ['schema-version = 1', '',
                    '[enumeration-review]', 'enumerator = "ann"', 'enumerator-family = "claude"',
                    'verdict = "complete"', 'rationale = "x"', 'reviewer = "bob"',
                    'reviewer-family = "codex"', 'reviewed-utc = "2026-08-26T00:00:00Z"', '',
                    '[[archive-file]]', 'legacy-path = "g.md"', 'archive-sha256 = "{}"'.format(g7sha),
                    'size = {}'.format(len(g7file.encode())), 'owner = 0',
                    'predecessor-clause-ids = ["{}"]'.format(g7p), '',
                    '[[predecessor]]', 'clause-id = "{}"'.format(g7p),
                    'archive-sha256 = "{}"'.format(g7sha),
                    'start-line = {}'.format(start), 'end-line = {}'.format(end),
                    'canonical-text = "{}"'.format(canonical),
                    'source-digest = "{}"'.format(g7sha), '',
                    '[[mapping]]', 'predecessor-clause-id = "{}"'.format(g7p),
                    'successor-clause-id = "succ.a"', 'successor-quote = "{}"'.format(_SUCC_A),
                    'author = "carol"', 'author-family = "claude"',
                    'semantic-verdict = "equal-or-stronger"', 'rationale = "x"', 'reviewer = "dave"',
                    'reviewer-family = "gemini"', 'reviewed-utc = "2026-08-26T00:00:00Z"', '']
            return "\n".join(rows) + "\n"

        # a substring that DROPS the '## ' heading prefix: begins mid start-line, ends on end-line -> PASS.
        g7_sub = "The retention obligation\\nThe system MUST retain every record."
        g7ok = _build_install(tmp / "g7-substring-ok", _g7_crosswalk(1, 2, g7_sub), [g7file], inv)
        if run_quiet(g7ok) != 0:
            failures.append("G7: a canonical-text substring beginning on start-line and ending on end-line "
                            "must PASS (0)")
        n += 1
        # a not-tight window (canonical entirely on line 2, but start-line declared 1) -> FAIL.
        g7loose = _build_install(tmp / "g7-loose", _g7_crosswalk(1, 2, "The system MUST retain every record."),
                                 [g7file], inv)
        if run_quiet(g7loose) != 1:
            failures.append("G7: a loose window (canonical not beginning on start-line) must FAIL (1)")
        n += 1
        # two occurrences within the window -> ambiguous -> FAIL.
        g7twice = "AA\nAA\n"
        g7twsha = _sha(g7twice)
        g7twp = "pre-{}.1".format(g7twsha[:12])

        def _g7_twice(canonical):
            rows = ['schema-version = 1', '',
                    '[enumeration-review]', 'enumerator = "ann"', 'enumerator-family = "claude"',
                    'verdict = "complete"', 'rationale = "x"', 'reviewer = "bob"',
                    'reviewer-family = "codex"', 'reviewed-utc = "2026-08-26T00:00:00Z"', '',
                    '[[archive-file]]', 'legacy-path = "t.md"', 'archive-sha256 = "{}"'.format(g7twsha),
                    'size = {}'.format(len(g7twice.encode())), 'owner = 0',
                    'predecessor-clause-ids = ["{}"]'.format(g7twp), '',
                    '[[predecessor]]', 'clause-id = "{}"'.format(g7twp),
                    'archive-sha256 = "{}"'.format(g7twsha),
                    'start-line = 1', 'end-line = 2', 'canonical-text = "{}"'.format(canonical),
                    'source-digest = "{}"'.format(g7twsha), '',
                    '[[mapping]]', 'predecessor-clause-id = "{}"'.format(g7twp),
                    'successor-clause-id = "succ.a"', 'successor-quote = "{}"'.format(_SUCC_A),
                    'author = "carol"', 'author-family = "claude"',
                    'semantic-verdict = "equal-or-stronger"', 'rationale = "x"', 'reviewer = "dave"',
                    'reviewer-family = "gemini"', 'reviewed-utc = "2026-08-26T00:00:00Z"', '']
            return "\n".join(rows) + "\n"

        g7two = _build_install(tmp / "g7-twice", _g7_twice("AA"), [g7twice], inv)
        if run_quiet(g7two) != 1:
            failures.append("G7: a canonical-text occurring more than once in the window must FAIL (1)")
        n += 1

        # G8: an archived payload that is a SYMLINK (to external, matching bytes) is refused (exit 2); a
        # target-following read would have accepted the redirectable bytes.
        g8 = _build_install(tmp / "g8-symlink", base_text, [_LEGACY_ONE, _LEGACY_TWO], inv)
        g8payload = g8 / ARCHIVE_REL / s1 / "payload"
        g8ext = tmp / "g8-external-bytes"
        g8ext.write_text(_LEGACY_ONE, encoding="utf-8")     # matching bytes, so a following read would pass
        g8payload.unlink()
        os.symlink(str(g8ext), str(g8payload))
        if run_quiet(g8) != 2:
            failures.append("G8: a symlinked archived payload must be refused (exit 2, no-follow)")
        n += 1

        # G8 ANCESTOR: a symlinked .aiqt ANCESTOR (its target holding the real, matching archive) redirects
        # the whole archive; a target-following read would PASS (the presence lstats resolve through the
        # link and the moved bytes match). The no-follow ANCESTOR walk from the install root refuses it, so
        # the gate fails closed (exit 2), never a silent clean PASS on bytes reached through the link.
        g8anc = _build_install(tmp / "g8-ancestor", base_text, [_LEGACY_ONE, _LEGACY_TWO], inv)
        g8anc_real = tmp / "g8-ancestor-real-aiqt"
        shutil.move(str(g8anc / ".aiqt"), str(g8anc_real))
        os.symlink(str(g8anc_real), str(g8anc / ".aiqt"))
        if run_quiet(g8anc) != 2:
            failures.append("G8: a symlinked .aiqt ancestor must be refused (exit 2, no-follow ancestor walk)")
        n += 1

        # D5: successor-inventory self-consistency (cheap part). A DUPLICATE clause-id FAILs; a malformed
        # declared span or source-digest FAILs. (The pinned-release binding is disclosed, not gated.)
        dupinv = _build_install(tmp / "d5-dup", base_text, [_LEGACY_ONE, _LEGACY_TWO],
                                inv + [("succ.a", _SUCC_A)])
        if run_quiet(dupinv) != 1:
            failures.append("D5: a duplicate successor-inventory clause-id must FAIL (1)")
        n += 1
        d5span = _build_install(tmp / "d5-span", base_text, [_LEGACY_ONE, _LEGACY_TWO], inv)
        _write(d5span / INVENTORY_REL,
               'schema-version = 1\n\n[[clause]]\nclause-id = "succ.a"\n'
               'canonical-text = "{}"\nstart-line = 5\nend-line = 2\n\n[[clause]]\nclause-id = "succ.b"\n'
               'canonical-text = "{}"\n'.format(_SUCC_A.replace('"', '\\"'), _SUCC_B.replace('"', '\\"')))
        if run_quiet(d5span) != 1:
            failures.append("D5: a malformed successor-inventory source span must FAIL (1)")
        n += 1
        d5dig = _build_install(tmp / "d5-digest", base_text, [_LEGACY_ONE, _LEGACY_TWO], inv)
        _write(d5dig / INVENTORY_REL,
               'schema-version = 1\n\n[[clause]]\nclause-id = "succ.a"\n'
               'canonical-text = "{}"\nsource-digest = "NOTHEX"\n\n[[clause]]\nclause-id = "succ.b"\n'
               'canonical-text = "{}"\n'.format(_SUCC_A.replace('"', '\\"'), _SUCC_B.replace('"', '\\"')))
        if run_quiet(d5dig) != 1:
            failures.append("D5: a malformed successor-inventory source-digest must FAIL (1)")
        n += 1

        # D6: structural completeness. A crosswalk present with the ARCHIVE structurally ABSENT is PARTIAL
        # state and fails closed (exit 2), even when the crosswalk carries only an enumeration-review and no
        # rows (which reached PASS before: an empty-migration fail-open).
        d6 = tmp / "d6-partial"
        (d6 / MIGRATION_REL).mkdir(parents=True)
        _write(d6 / CROSSWALK_REL,
               'schema-version = 1\n\n[enumeration-review]\nenumerator = "ann"\nenumerator-family = "claude"\n'
               'verdict = "complete"\nrationale = "x"\nreviewer = "bob"\nreviewer-family = "codex"\n'
               'reviewed-utc = "2026-08-26T00:00:00Z"\n')
        _write(d6 / INVENTORY_REL, 'schema-version = 1\n')   # note: no .aiqt/archive, no pin
        if run_quiet(d6) != 2:
            failures.append("D6: a crosswalk with the archive structurally absent must fail closed (exit 2)")
        n += 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if failures:
        print("SELF-TEST FAIL:")
        for f in failures:
            print("  - " + f)
        return 1
    print("SELF-TEST PASS: clean one-to-one, fold, and split crosswalks pass across {} scenarios; a "
          "tampered archive, a span/text or source-digest disagreement, an unmapped predecessor, an "
          "unresolvable successor, a non-verbatim quote, a same-author or same-family verdict, a missing "
          "completeness review, and a malformed 8.3 id each FAIL; a disagreeing pointer is a warning that "
          "changes no verdict; malformed TOML and partial migration state fail closed (exit 2) while "
          "total absence is NA.".format(n))
    return 0


if __name__ == "__main__":
    sys.exit(main())
