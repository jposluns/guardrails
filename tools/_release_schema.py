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
import os
import re
import subprocess
import sys
from datetime import date
from pathlib import Path, PurePosixPath, PureWindowsPath
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_versions import _parse                    # noqa: E402  the shipped bare-SemVer parser
from gen_manifest import RELEASE_ROW_ALLOWED          # noqa: E402  the single shared allowed keyset
from check_clauses import split_clause_id, _valid_register_id, SHA256_RE  # noqa: E402  authoritative
#                                       clause-id / register-id syntax and the source-digest regex (7.1/7.2)
from gen_rules import CID_RE                          # noqa: E402  the authoritative corpus-id regex

HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
# A recorded git object id is a full lowercase hex sha1 (40) or sha256 (64); an abbreviated or
# mixed-case id is malformed input, exit 2 (round-3 finding 8).
OBJECTID_RE = re.compile(r"^([0-9a-f]{40}|[0-9a-f]{64})$")


def _is_host_absolute(p):
    """True if p is a host-absolute path under EITHER POSIX or Windows semantics, including a Windows
    UNC (\\\\server\\share) or device (\\\\.\\, \\\\?\\) form and a drive-letter path (round-3 finding 9).
    A logical store path is repo-relative, so any of these is a portability violation."""
    if not isinstance(p, str) or not p:
        return True
    if p[0] in ("/", "\\"):                       # POSIX absolute, or a Windows rooted/UNC/device path
        return True
    if len(p) >= 2 and p[1] == ":":               # a drive-letter path (C:..., including C:relative)
        return True
    return PurePosixPath(p).is_absolute() or PureWindowsPath(p).is_absolute()

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
    # tag_object_sha / commit_sha carry a full lowercase git object id; an abbreviated or mixed-case value
    # is malformed input, exit 2 (finding 8). Resolution (does the object exist, does it match the tag) is
    # the build gate's, downstream.
    for key in ("tag_object_sha", "commit_sha"):
        if not OBJECTID_RE.fullmatch(row[key]):
            raise SchemaError("{}: {} {!r} is not a full lowercase git object id (40 or 64 hex)".format(
                where, key, row[key]))
    qa = row["qa-sha256"]
    if not isinstance(qa, str) or not HEX64_RE.fullmatch(qa):
        raise SchemaError("{}: qa-sha256 is not 64 lowercase hex".format(where))
    if _is_host_absolute(row["qa-store-path"]):
        raise SchemaError("{}: qa-store-path {!r} is host-absolute (POSIX, drive-letter, or Windows "
                          "UNC/device); the row records a logical store path (portability)".format(
                              where, row["qa-store-path"]))
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


MANIFEST_TOP_ALLOWED = {"format-version", "release-version", "genesis", "tree-sha256", "sources",
                        "artifacts"}
MANIFEST_ARTIFACT_KINDS = ("file", "managed-block")


def strict_manifest(data, where):
    """Strict structural schema of a release manifest (4.1), on head AND predecessor objects (round-3
    finding 1: genesis previously accepted a malformed manifest as clean). format-version == 1; the exact
    top-level keyset; release-version a bare SemVer; genesis a real boolean; tree-sha256 64 lowercase hex;
    [[sources]] an array of exactly {path, bytes, sha256} rows (a non-empty logical path, a non-negative
    integer byte count, a 64-hex digest, no duplicate path); [[artifacts]] an array of tables each carrying
    a non-empty artifact-id (unique), path, a known kind, and a 64-hex sha256. The full SOURCES-set-equality
    and re-hash stay check_manifest's; this closes the structural hole the delta gate branched on."""
    _require_format_version(data, where)
    extra = set(data) - MANIFEST_TOP_ALLOWED
    if extra:
        raise SchemaError("{}: unknown top-level key(s): {}".format(where, ", ".join(sorted(extra))))
    rv = data.get("release-version")
    if not isinstance(rv, str) or _parse(rv) is None:
        raise SchemaError("{}: release-version {!r} is not a bare SemVer".format(where, rv))
    if not isinstance(data.get("genesis"), bool):
        raise SchemaError("{}: genesis must be a boolean".format(where))
    tree = data.get("tree-sha256")
    if not isinstance(tree, str) or not HEX64_RE.fullmatch(tree):
        raise SchemaError("{}: tree-sha256 is not 64 lowercase hex".format(where))
    sources = data.get("sources", [])
    if not isinstance(sources, list):
        raise SchemaError("{}: [[sources]] is not an array".format(where))
    seen_paths = set()
    for i, row in enumerate(sources, 1):
        rw = "{} sources row #{}".format(where, i)
        if not isinstance(row, dict) or set(row) != {"path", "bytes", "sha256"}:
            raise SchemaError("{}: keys are not exactly path/bytes/sha256".format(rw))
        if not isinstance(row["path"], str) or not row["path"]:
            raise SchemaError("{}: missing or non-string path".format(rw))
        if not isinstance(row["bytes"], int) or isinstance(row["bytes"], bool) or row["bytes"] < 0:
            raise SchemaError("{}: bytes is not a non-negative integer".format(rw))
        if not isinstance(row["sha256"], str) or not HEX64_RE.fullmatch(row["sha256"]):
            raise SchemaError("{}: sha256 is not 64 lowercase hex".format(rw))
        if row["path"] in seen_paths:
            raise SchemaError("{}: duplicate sources path {!r}".format(rw, row["path"]))
        seen_paths.add(row["path"])
    artifacts = data.get("artifacts", [])
    if not isinstance(artifacts, list):
        raise SchemaError("{}: [[artifacts]] is not an array".format(where))
    seen_ids = set()
    for i, row in enumerate(artifacts, 1):
        rw = "{} artifacts row #{}".format(where, i)
        if not isinstance(row, dict):
            raise SchemaError("{}: not a table".format(rw))
        aid = row.get("artifact-id")
        if not isinstance(aid, str) or not aid:
            raise SchemaError("{}: missing or non-string artifact-id".format(rw))
        if aid in seen_ids:
            raise SchemaError("{}: duplicate artifact-id {!r}".format(rw, aid))
        seen_ids.add(aid)
        if not isinstance(row.get("path"), str) or not row["path"]:
            raise SchemaError("{}: missing or non-string path".format(rw))
        if row.get("kind") not in MANIFEST_ARTIFACT_KINDS:
            raise SchemaError("{}: kind must be one of {}".format(rw, list(MANIFEST_ARTIFACT_KINDS)))
        if not isinstance(row.get("sha256"), str) or not HEX64_RE.fullmatch(row["sha256"]):
            raise SchemaError("{}: sha256 is not 64 lowercase hex".format(rw))
    return data


def strict_renderers(data, where):
    """Strict structural schema of the renderer declaration (6.5), on head AND predecessor objects (round-3
    finding 4: a predecessor renderers.toml with format-version = 999 was accepted). format-version == 1;
    [[renderer]] a non-empty array of tables, each carrying a non-empty renderer-id (unique), a list
    targets, a list closure, a 64-hex code-digest, and an integer semantics-revision. The freshness
    (closure recomputation) stays gen_renderers'; this asserts the declaration shape on both objects."""
    _require_format_version(data, where)
    extra = set(data) - {"format-version", "renderer"}
    if extra:
        raise SchemaError("{}: unknown top-level key(s): {}".format(where, ", ".join(sorted(extra))))
    rows = data.get("renderer", [])
    if not isinstance(rows, list) or not rows:
        raise SchemaError("{}: [[renderer]] must be a non-empty array".format(where))
    seen = set()
    for i, row in enumerate(rows, 1):
        rw = "{} renderer row #{}".format(where, i)
        if not isinstance(row, dict):
            raise SchemaError("{}: not a table".format(rw))
        rid = row.get("renderer-id")
        if not isinstance(rid, str) or not rid:
            raise SchemaError("{}: missing or non-string renderer-id".format(rw))
        if rid in seen:
            raise SchemaError("{}: duplicate renderer-id {!r}".format(rw, rid))
        seen.add(rid)
        if not isinstance(row.get("targets"), list):
            raise SchemaError("{}: targets must be a list".format(rw))
        if not isinstance(row.get("closure"), list):
            raise SchemaError("{}: closure must be a list".format(rw))
        cd = row.get("code-digest")
        if not isinstance(cd, str) or not HEX64_RE.fullmatch(cd):
            raise SchemaError("{}: code-digest is not 64 lowercase hex".format(rw))
        if not isinstance(row.get("semantics-revision"), int) or isinstance(row.get("semantics-revision"),
                                                                            bool):
            raise SchemaError("{}: semantics-revision must be an integer".format(rw))
    return data


ORDER_PRESENTATION_KEYS = {"families", "aiqt-facets", "security-facets", "tie-breaker"}
CLAUSE_ROW_KEYS = frozenset({"clause-id", "corpus-id", "source-path", "start-line", "end-line",
                             "canonical-text", "source-digest"})
IDHISTORY_ROW_KEYS = {"born": {"id", "born-release"}, "tombstone": {"id", "retired-release"},
                      "successor": {"id", "retired-release", "successor-id"}}


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def strict_order(data, where):
    """EXHAUSTIVE order-record schema (round-4 finding 2): format-version == 1, apex-corpus-id 'prjint1',
    the exact top-level keyset, a NON-EMPTY [[precedence-tier]] array whose every row is exactly
    {rank:int, members:non-empty list of str, members-are-equal:bool}, and a [presentation-order] table
    carrying exactly {families, aiqt-facets, security-facets} lists of str plus a string tie-breaker. The
    operative-constant comparison (against gen_rules) stays check_manifest's; this asserts the structural
    shape on BOTH head and predecessor objects."""
    _require_format_version(data, where)
    if data.get("apex-corpus-id") != "prjint1":
        raise SchemaError("{}: apex-corpus-id must be 'prjint1'".format(where))
    extra = set(data) - ORDER_TOP_ALLOWED
    if extra:
        raise SchemaError("{}: unknown top-level key(s): {}".format(where, ", ".join(sorted(extra))))
    tiers = data.get("precedence-tier")
    if not isinstance(tiers, list) or not tiers:
        raise SchemaError("{}: [[precedence-tier]] must be a non-empty array".format(where))
    for i, t in enumerate(tiers, 1):
        tw = "{} precedence-tier #{}".format(where, i)
        if not isinstance(t, dict) or set(t) != {"rank", "members", "members-are-equal"}:
            raise SchemaError("{}: keys are not exactly rank/members/members-are-equal".format(tw))
        if not _is_int(t["rank"]):
            raise SchemaError("{}: rank must be an integer".format(tw))
        if not isinstance(t["members"], list) or not t["members"] \
                or not all(isinstance(m, str) and m for m in t["members"]):
            raise SchemaError("{}: members must be a non-empty list of strings".format(tw))
        if not isinstance(t["members-are-equal"], bool):
            raise SchemaError("{}: members-are-equal must be a boolean".format(tw))
    pres = data.get("presentation-order")
    if not isinstance(pres, dict) or set(pres) != ORDER_PRESENTATION_KEYS:
        raise SchemaError("{}: [presentation-order] keys are not exactly {}".format(
            where, sorted(ORDER_PRESENTATION_KEYS)))
    for key in ("families", "aiqt-facets", "security-facets"):
        if not isinstance(pres[key], list) or not all(isinstance(x, str) and x for x in pres[key]):
            raise SchemaError("{}: presentation-order.{} must be a list of strings".format(where, key))
    if not isinstance(pres["tie-breaker"], str) or not pres["tie-breaker"]:
        raise SchemaError("{}: presentation-order.tie-breaker must be a non-empty string".format(where))


def strict_clause_inventory(data, where):
    """EXHAUSTIVE 7.2 clause-inventory schema (round-4 finding 2): a [[clause]] array of tables, each with
    EXACTLY the 7.2 keyset; a well-formed clause-id (UNIQUE) whose corpus part equals a well-formed
    corpus-id field; a non-empty source-path; positive integer start-line/end-line with end >= start; a
    non-empty canonical-text; and a 64-lowercase-hex source-digest. The full source-file span/text/digest
    CONSISTENCY (reading the rule sources) stays check_clauses'; this validates the record's own structure
    exhaustively on BOTH objects (the round-2/3 three-field guard accepted malformed ids and missing span/
    digest fields). Returns the rows list."""
    rows = data.get("clause")
    if not isinstance(rows, list):
        raise SchemaError("{}: no [[clause]] array".format(where))
    seen = set()
    out = []
    for i, row in enumerate(rows, 1):
        rw = "{} clause row #{}".format(where, i)
        if not isinstance(row, dict):
            raise SchemaError("{}: not a table".format(rw))
        if set(row) != CLAUSE_ROW_KEYS:
            raise SchemaError("{}: keys are not exactly the 7.2 clause schema {}".format(
                rw, sorted(CLAUSE_ROW_KEYS)))
        cid = row["clause-id"]
        parsed = split_clause_id(cid) if isinstance(cid, str) else None
        if parsed is None:
            raise SchemaError("{}: clause-id {!r} is not <corpus-id>.<unpadded-ordinal> (7.1)".format(
                rw, cid))
        if cid in seen:
            raise SchemaError("{}: duplicate clause-id {!r}".format(rw, cid))
        seen.add(cid)
        corpus = row["corpus-id"]
        if not isinstance(corpus, str) or not CID_RE.fullmatch(corpus):
            raise SchemaError("{}: corpus-id {!r} is not a well-formed corpus-id".format(rw, corpus))
        if corpus != parsed[0]:
            raise SchemaError("{}: corpus-id {!r} does not match the clause-id corpus part {!r}".format(
                rw, corpus, parsed[0]))
        if not isinstance(row["source-path"], str) or not row["source-path"].strip():
            raise SchemaError("{}: missing or non-string source-path".format(rw))
        if not _is_int(row["start-line"]) or not _is_int(row["end-line"]) \
                or row["start-line"] < 1 or row["end-line"] < row["start-line"]:
            raise SchemaError("{}: start-line/end-line must be positive integers with end >= start".format(
                rw))
        if not isinstance(row["canonical-text"], str) or not row["canonical-text"]:
            raise SchemaError("{}: canonical-text must be a non-empty string".format(rw))
        if not isinstance(row["source-digest"], str) or not SHA256_RE.fullmatch(row["source-digest"]):
            raise SchemaError("{}: source-digest is not 64 lowercase hex".format(rw))
        out.append(row)
    return out


def strict_id_history(data, where):
    """EXHAUSTIVE 7.3 id-history schema (round-4 finding 2): only the born/tombstone/successor sections,
    each an array of tables with EXACTLY that section's keyset; every id a well-formed corpus-id or
    clause-id; every release a bare SemVer; a well-formed successor-id on successor rows; and NO duplicate
    born id. The full temporal/graph semantics stay check_clauses'; this closes the hole where a malformed
    id or a duplicate born row was accepted. Returns the parsed dict."""
    if not isinstance(data, dict):
        raise SchemaError("{}: not a table".format(where))
    extra = set(data) - set(IDHISTORY_ROW_KEYS)
    if extra:
        raise SchemaError("{}: unknown top-level key(s): {}".format(where, ", ".join(sorted(extra))))
    rel_key = {"born": "born-release", "tombstone": "retired-release", "successor": "retired-release"}
    born_ids = set()
    for section in ("born", "tombstone", "successor"):
        rows = data.get(section, [])
        if not isinstance(rows, list):
            raise SchemaError("{}: the {} section is not an array of tables".format(where, section))
        for i, row in enumerate(rows, 1):
            rw = "{} {} row #{}".format(where, section, i)
            if not isinstance(row, dict) or set(row) != IDHISTORY_ROW_KEYS[section]:
                raise SchemaError("{}: keys are not exactly {}".format(
                    rw, sorted(IDHISTORY_ROW_KEYS[section])))
            if not _valid_register_id(row["id"]):
                raise SchemaError("{}: id {!r} is neither a well-formed corpus-id nor clause-id".format(
                    rw, row["id"]))
            key = rel_key[section]
            if not isinstance(row[key], str) or _parse(row[key]) is None:
                raise SchemaError("{}: {} is not a bare SemVer".format(rw, key))
            if section == "born":
                if row["id"] in born_ids:
                    raise SchemaError("{}: duplicate born row for {!r}".format(rw, row["id"]))
                born_ids.add(row["id"])
            if section == "successor" and not _valid_register_id(row["successor-id"]):
                raise SchemaError("{}: successor-id {!r} is not a well-formed id".format(
                    rw, row["successor-id"]))
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


# --- filter-free tree materialization (round-4 finding 1) -------------------------------------------

def _cat_file_batch(root, shas):
    """{sha: raw bytes} for the given blob shas via ONE `git cat-file --batch` process (no checkout, so no
    smudge/clean filter and no gitattributes transformation runs). SchemaError on any git or protocol
    failure or a missing object (cannot-evaluate)."""
    uniq = list(dict.fromkeys(shas))
    if not uniq:
        return {}
    try:
        proc = subprocess.run(["git", "-C", str(root), "cat-file", "--batch"],
                              input=("\n".join(uniq) + "\n").encode("ascii"), capture_output=True)
    except OSError as exc:
        raise SchemaError("cannot launch git cat-file --batch ({})".format(exc))
    if proc.returncode != 0:
        raise SchemaError("git cat-file --batch failed: {}".format(
            proc.stderr.decode("utf-8", "replace").strip()))
    out, i, result = proc.stdout, 0, {}
    for _ in uniq:
        nl = out.find(b"\n", i)
        if nl == -1:
            raise SchemaError("truncated git cat-file --batch output")
        header = out[i:nl].decode("ascii", "replace")
        i = nl + 1
        parts = header.split(" ")
        if len(parts) < 2 or parts[1] == "missing":
            raise SchemaError("git cat-file --batch: object not found ({})".format(header))
        osha, size = parts[0], int(parts[2])
        result[osha] = out[i:i + size]
        i += size + 1  # skip the trailing newline the protocol appends
    return result


def materialize_tree_raw(root, commit, dest):
    """Write the committed tree at `commit` into `dest` from RAW blob bytes only (git ls-tree + cat-file),
    applying NO checkout smudge/clean filter and NO gitattributes transformation, so a hostile filter cannot
    substitute old bytes during a checkout the way `git worktree add`/`git checkout` would (round-4 finding
    1). Symlinks and gitlinks are rejected. Returns the set of written repo-relative paths. SchemaError on
    any git/materialization failure (cannot-evaluate)."""
    try:
        ls = subprocess.run(["git", "-C", str(root), "ls-tree", "-r", "-z", commit], capture_output=True)
    except OSError as exc:
        raise SchemaError("cannot launch git ls-tree ({})".format(exc))
    if ls.returncode != 0:
        raise SchemaError("cannot list tree {}: {}".format(
            commit, ls.stderr.decode("utf-8", "replace").strip()))
    entries = []
    for rec in ls.stdout.split(b"\x00"):
        if not rec:
            continue
        meta, _tab, path_b = rec.partition(b"\t")
        fields = meta.split(b" ")
        if len(fields) != 3:
            raise SchemaError("malformed ls-tree record in {}".format(commit))
        mode, otype, osha = fields[0].decode("ascii"), fields[1].decode("ascii"), fields[2].decode("ascii")
        if mode in ("120000", "160000"):
            raise SchemaError("tree {} contains a symlink/gitlink {!r}; rejected".format(
                commit, path_b.decode("utf-8", "replace")))
        if otype != "blob":
            continue
        try:
            path = path_b.decode("utf-8")
        except UnicodeDecodeError:
            raise SchemaError("non-UTF-8 path in tree {}".format(commit))
        entries.append((osha, path))
    blobs = _cat_file_batch(root, [osha for osha, _ in entries])
    dest_root = Path(dest).resolve()
    written = set()
    for osha, path in entries:
        target = (Path(dest) / path).resolve()
        if target != dest_root and dest_root not in target.parents:
            raise SchemaError("path {!r} escapes the materialization root".format(path))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(blobs[osha])
        written.add(path)
    return written
