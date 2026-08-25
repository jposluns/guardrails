#!/usr/bin/env python3
"""Shared strict schema validators for the release gates (VER-CORE 2.4/6.5). Offline, stdlib only,
fail-closed.

These validators operate on ALREADY-PARSED TOML dicts, so the SAME strict validation runs on a
working-tree record and on a predecessor record read via `git show <commit>:<path>`. Round-1 defined a
"shared schema" the loader never actually called; this module is the ONE strict validator set that the
delta loader (check_release_delta) and the build loader (check_release_build) both call, on BOTH head and
predecessor objects, BEFORE any delta or audit computation (VC-4 round-2 findings 1 and 4). A schema
violation raises SchemaError; each caller maps it to its own fail-closed exit 2, never a silently
conservative verdict.

read_genesis in gen_manifest stays the lenient Step-2 COUNT check (it needs only the row count and
validates the two mandatory identity fields, accepting every documented field) and is intentionally
unaffected: a header-only record at Step 2 is not yet a complete attestation record. The delta and build
gates run AFTER attestation, where every PRESENT release row is a post-QA attestation row carrying the
complete record (spec 2.4 / releases.toml header / VER-CORE-SPEC.md:254), so here every field is required.
"""
import re
import sys
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_versions import _parse                    # noqa: E402  the shipped bare-SemVer parser
from gen_manifest import RELEASE_ROW_ALLOWED          # noqa: E402  the single shared allowed keyset

HEX64_RE = re.compile(r"^[0-9a-f]{64}$")

# The COMPLETE release-order row (spec 2.4 / releases.toml header / VER-CORE-SPEC.md:254): a present row is
# a post-QA ATTESTATION row carrying the whole record, so the delta and build gates require EVERY field.
RELEASE_ROW_REQUIRED = ("version", "tag", "tag_object_sha", "commit_sha",
                        "qa-sha256", "qa-store-path", "attestation-timestamps")
# order.toml's exact top-level schema (mirrors check_manifest.check_order_record, the single home of the
# operative-constant comparison; here only the structural top-level shape is asserted, on both objects).
ORDER_TOP_ALLOWED = {"format-version", "apex-corpus-id", "precedence-tier", "presentation-order"}


class SchemaError(Exception):
    """A record fails its strict schema: the gate cannot compute a trustworthy verdict. Each caller maps
    this to its own fail-closed exit 2."""


def _require_format_version(data, where):
    if not isinstance(data, dict):
        raise SchemaError("{}: not a table".format(where))
    if data.get("format-version") != 1:
        raise SchemaError("{}: format-version must be exactly 1".format(where))


def strict_release_row(row, where):
    """Full per-row validation of one release-order row: exactly the allowed keyset, every complete-record
    field present and well-typed, version a bare SemVer, qa-sha256 64 lowercase hex, qa-store-path a logical
    (never host-absolute) path, and attestation-timestamps a NON-EMPTY list of integer epochs."""
    if not isinstance(row, dict):
        raise SchemaError("{}: not a table".format(where))
    extra = set(row) - RELEASE_ROW_ALLOWED
    if extra:
        raise SchemaError("{}: unknown key(s): {} (not in the shared release-row schema)".format(
            where, ", ".join(sorted(extra))))
    missing = [k for k in RELEASE_ROW_REQUIRED if k not in row]
    if missing:
        raise SchemaError("{}: a present release row is a complete attestation record and must carry {}; "
                          "missing {} (2.4/L254)".format(where, ", ".join(RELEASE_ROW_REQUIRED),
                                                          ", ".join(missing)))
    for key in ("version", "tag", "tag_object_sha", "commit_sha", "qa-store-path"):
        v = row.get(key)
        if not isinstance(v, str) or not v:
            raise SchemaError("{}: {!r} must be a non-empty string".format(where, key))
    if _parse(row["version"]) is None:
        raise SchemaError("{}: malformed version {!r}".format(where, row["version"]))
    qa = row["qa-sha256"]
    if not isinstance(qa, str) or not HEX64_RE.fullmatch(qa):
        raise SchemaError("{}: qa-sha256 is not 64 lowercase hex".format(where))
    p = row["qa-store-path"]
    if p.startswith("/") or (len(p) > 1 and p[1] == ":"):
        raise SchemaError("{}: qa-store-path {!r} is host-absolute; the row records a logical store path "
                          "(portability)".format(where, p))
    ts = row["attestation-timestamps"]
    if not isinstance(ts, list) or not ts or not all(isinstance(t, int) and not isinstance(t, bool)
                                                     for t in ts):
        raise SchemaError("{}: attestation-timestamps must be a NON-EMPTY list of integer epochs".format(
            where))


def strict_releases(data, where):
    """format-version == 1, the exact top-level keyset {format-version, release}, and the full per-row
    schema for every present row. Returns the rows list (zero rows is a valid genesis/dormant state)."""
    _require_format_version(data, where)
    extra = set(data) - {"format-version", "release"}
    if extra:
        raise SchemaError("{}: unknown top-level key(s): {}".format(where, ", ".join(sorted(extra))))
    rows = data.get("release", [])
    if not isinstance(rows, list):
        raise SchemaError("{}: [[release]] is not an array".format(where))
    for i, row in enumerate(rows, 1):
        strict_release_row(row, "{} row #{}".format(where, i))
    return rows


def strict_order(data, where):
    """format-version == 1, apex-corpus-id 'prjint1', and the exact top-level keyset (structural only; the
    operative-constant comparison stays check_manifest's, on head)."""
    _require_format_version(data, where)
    if data.get("apex-corpus-id") != "prjint1":
        raise SchemaError("{}: apex-corpus-id must be 'prjint1'".format(where))
    extra = set(data) - ORDER_TOP_ALLOWED
    if extra:
        raise SchemaError("{}: unknown top-level key(s): {}".format(where, ", ".join(sorted(extra))))


def strict_clause_inventory(data, where):
    """Structural integrity of a clause inventory: a [[clause]] array of tables, each carrying a non-empty
    string clause-id (UNIQUE across the inventory), corpus-id, and canonical-text. Returns the rows list.
    The full 7.2 span/text/digest legs stay check_clauses'; this closes the round-2 hole where a duplicate
    or incomplete clause row silently collapsed in the delta gate's dict comprehension."""
    rows = data.get("clause")
    if not isinstance(rows, list):
        raise SchemaError("{}: no [[clause]] array".format(where))
    seen = set()
    out = []
    for i, row in enumerate(rows, 1):
        rw = "{} clause row #{}".format(where, i)
        if not isinstance(row, dict):
            raise SchemaError("{}: not a table".format(rw))
        cid = row.get("clause-id")
        if not isinstance(cid, str) or not cid:
            raise SchemaError("{}: missing or non-string clause-id".format(rw))
        if cid in seen:
            raise SchemaError("{}: duplicate clause-id {!r}".format(rw, cid))
        seen.add(cid)
        if not isinstance(row.get("corpus-id"), str) or not row["corpus-id"]:
            raise SchemaError("{}: missing or non-string corpus-id".format(rw))
        if not isinstance(row.get("canonical-text"), str):
            raise SchemaError("{}: missing or non-string canonical-text".format(rw))
        out.append(row)
    return out


def strict_id_history(data, where):
    """Structural integrity of the id-history register: only the born/tombstone/successor sections, each an
    array of tables with a non-empty string id and a bare-SemVer release field (born-release for born,
    retired-release for the two retirement sections), and a non-empty string successor-id on successor rows.
    Returns the parsed dict. The full 7.3 semantics stay check_clauses'; this closes the round-2 hole where
    a malformed register row was silently skipped by the delta gate's isinstance guards."""
    if not isinstance(data, dict):
        raise SchemaError("{}: not a table".format(where))
    extra = set(data) - {"born", "tombstone", "successor"}
    if extra:
        raise SchemaError("{}: unknown top-level key(s): {}".format(where, ", ".join(sorted(extra))))
    rel_key = {"born": "born-release", "tombstone": "retired-release", "successor": "retired-release"}
    for section in ("born", "tombstone", "successor"):
        rows = data.get(section, [])
        if not isinstance(rows, list):
            raise SchemaError("{}: the {} section is not an array of tables".format(where, section))
        for i, row in enumerate(rows, 1):
            rw = "{} {} row #{}".format(where, section, i)
            if not isinstance(row, dict):
                raise SchemaError("{}: not a table".format(rw))
            if not isinstance(row.get("id"), str) or not row["id"]:
                raise SchemaError("{}: missing or non-string id".format(rw))
            key = rel_key[section]
            if not isinstance(row.get(key), str) or _parse(row[key]) is None:
                raise SchemaError("{}: {} is not a bare SemVer".format(rw, key))
            if section == "successor" and (not isinstance(row.get("successor-id"), str)
                                           or not row["successor-id"]):
                raise SchemaError("{}: missing or non-string successor-id".format(rw))
    return data


# --- per-kind disposition evidence (6.6 / VER-CORE-SPEC.md:1037) ------------------------------------

def _is_well_formed_url(value):
    """A syntactically well-formed http(s) URL (scheme + netloc). Offline: reachability is NOT tested here
    (the gate does not touch the network); the spec's URL-VALIDATED-at-capture reachability is a build-time
    capture step, disclosed as a residual this offline gate does not perform."""
    if not isinstance(value, str) or not value:
        return False
    try:
        parsed = urlparse(value)
    except ValueError:
        return False
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def _is_iso_date(value):
    if not isinstance(value, str):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def default_correction_evidence_findings(row, where):
    """Validate a default-correction row's captured EVIDENCE per 6.6/L1037, not merely that the fields are
    nonempty strings: captured-source a well-formed http(s) URL, capture-date and observed-date valid ISO
    dates, observed-measurement present and carrying a digit (a measurement), and prefix-superset-reference
    a non-empty reference. A junk evidence value (the round-2 `captured-source = "s"`, an invalid date) is a
    malformed control and raises SchemaError (exit 2), never a licensed MINOR."""
    src = row.get("captured-source")
    if not _is_well_formed_url(src):
        raise SchemaError("{}: default-correction captured-source {!r} is not a well-formed http(s) URL "
                          "(6.6/L1039)".format(where, src))
    for key in ("capture-date", "observed-date"):
        if not _is_iso_date(row.get(key)):
            raise SchemaError("{}: default-correction {} {!r} is not a valid ISO date (6.6)".format(
                where, key, row.get(key)))
    meas = row.get("observed-measurement")
    if not isinstance(meas, str) or not meas or not any(ch.isdigit() for ch in meas):
        raise SchemaError("{}: default-correction observed-measurement {!r} is not a measurement (a value "
                          "carrying a digit; 6.6/L1038)".format(where, meas))
    ref = row.get("prefix-superset-reference")
    if not isinstance(ref, str) or not ref:
        raise SchemaError("{}: default-correction prefix-superset-reference is missing or empty "
                          "(6.6/L1039)".format(where))
