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
import ipaddress
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
from gen_rules import CID_RE, TIER_FACETS, CIA_FACETS, SLUG_RE  # noqa: E402  the authoritative corpus-id
#                              regex, the operative facet vocabularies (order controlled-vocab), and the
#                              authoritative slug syntax (renderer-id / artifact-id prefix)

HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
# A recorded git object id is a full lowercase hex sha1 (40) or sha256 (64); an abbreviated or
# mixed-case id is malformed input, exit 2 (round-3 finding 8).
OBJECTID_RE = re.compile(r"^([0-9a-f]{40}|[0-9a-f]{64})$")
_DECIMAL_RE = re.compile(r"^(0|[1-9][0-9]*)$")   # a git cat-file --batch object size (round-7 finding 3)


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


def has_control_char(s):
    """True if the string carries any C0 control character (0x00-0x1F) or DEL (0x7F). A single-line
    descriptive record field (a disposition impact/rationale/id, a 6.6 observed-measurement) carries none;
    the one legitimately MULTI-LINE record value, a clause canonical-text, is validated against its source
    bytes by check_clauses (source-digest consistency) rather than here, so it is not run through this."""
    return isinstance(s, str) and any(ord(ch) < 0x20 or ord(ch) == 0x7f for ch in s)


def is_canonical_relpath(p, allow_trailing_slash=False):
    """True if p is a CANONICAL repo-relative POSIX path (spec 4.1/L425): a non-empty string, not
    host-absolute, no backslash, no control character (the FULL C0 range 0x00-0x1F and DEL 0x7F, this
    round's #2), and no empty, '.' or '..' segment (so '../escape' is rejected). A trailing slash is
    permitted only for a directory target when allow_trailing_slash is set (renderer tree targets)."""
    if not isinstance(p, str) or not p or _is_host_absolute(p):
        return False
    if "\\" in p or any(ord(ch) < 0x20 or ord(ch) == 0x7f for ch in p):
        return False
    body = p[:-1] if (allow_trailing_slash and p.endswith("/")) else p
    if not body or body.endswith("/"):
        return False
    return all(seg not in ("", ".", "..") for seg in body.split("/"))

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
    # tag is EXACTLY "v" + version (2.1), carrying no control character: a tag that does not match its
    # version, or one with an embedded newline/tab, is a malformed row, not a valid ref (this round's #2).
    if any(ch in row["tag"] for ch in ("\x00", "\t", "\n", "\r")) or row["tag"] != "v" + row["version"]:
        raise SchemaError("{}: tag {!r} must be exactly 'v' + version ({!r}) with no control character".format(
            where, row["tag"], "v" + row["version"]))
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
    # qa-store-path is a CANONICAL repo-relative logical path (this round's #2): not merely non-host-
    # absolute, but with no '..', no './', no '//', no backslash, and no control character, so a traversal
    # ("../escape") or a non-canonical form ("qa/./record", "qa//record") cannot slip through.
    if not is_canonical_relpath(row["qa-store-path"]):
        raise SchemaError("{}: qa-store-path {!r} is not a canonical repo-relative path (no '.', '..', "
                          "'//', backslash, control character, or host-absolute form; 4.1/L425)".format(
                              where, row["qa-store-path"]))
    ts = row["attestation-timestamps"]
    if not isinstance(ts, list) or not ts or not all(
            isinstance(t, int) and not isinstance(t, bool) and t >= 0 for t in ts):
        raise SchemaError("{}: attestation-timestamps must be a NON-EMPTY list of NON-NEGATIVE integer "
                          "epochs".format(where))


def strict_releases(data, where):
    """format-version == 1, the exact top-level keyset {format-version, release}, and the full per-row
    schema for every present row. The array itself is APPEND-ONLY, oldest to newest: its versions are
    unique and STRICTLY INCREASING (round-6 finding 3), so two identical rows or an out-of-order pair is a
    malformed record even though each row is individually valid. Because a row's tag is exactly 'v' + its
    version (strict_release_row), strictly-increasing versions make the tags unique too. Returns the rows
    list (zero rows is a valid genesis/dormant state)."""
    _require_format_version(data, where)
    extra = set(data) - {"format-version", "release"}
    if extra:
        raise SchemaError("{}: unknown top-level key(s): {}".format(where, ", ".join(sorted(extra))))
    rows = data.get("release", [])
    if not isinstance(rows, list):
        raise SchemaError("{}: [[release]] is not an array".format(where))
    for i, row in enumerate(rows, 1):
        strict_release_row(row, "{} row #{}".format(where, i))
    # Array-level invariant (round-6 finding 3): per-row validation guarantees each version parses, so
    # compare the parsed tuples pairwise in array order. A version that does not strictly exceed its
    # predecessor (a duplicate is the equal case) is a non-append-only history, fail-closed.
    for i in range(1, len(rows)):
        if _parse(rows[i]["version"]) <= _parse(rows[i - 1]["version"]):
            raise SchemaError("{}: release versions must be unique and strictly increasing in array order; "
                              "row #{} {!r} does not exceed row #{} {!r}".format(
                                  where, i + 1, rows[i]["version"], i, rows[i - 1]["version"]))
    return rows


# The complete manifest schema, MIRRORED from check_manifest.load_manifest (the authoritative single home,
# not editable here): the EXACT mandatory top-level keyset, exact sources-row keyset, and the artifact-row
# keysets. MANIFEST_ARTIFACT_KEYS is kind-specific (round-5 finding 1): a file row carries exactly the base
# four, a managed-block row those four PLUS block-id; check_manifest allows block-id on any row, this gate
# is the stricter kind-specific form gen_manifest actually emits.
MANIFEST_TOP_KEYS = frozenset({"format-version", "release-version", "genesis", "tree-sha256", "sources",
                               "artifacts"})
MANIFEST_SOURCES_KEYS = frozenset({"path", "bytes", "sha256"})
MANIFEST_ARTIFACT_BASE = frozenset({"artifact-id", "path", "kind", "sha256"})
MANIFEST_ARTIFACT_KEYS = {"file": MANIFEST_ARTIFACT_BASE,
                          "managed-block": MANIFEST_ARTIFACT_BASE | {"block-id"}}
# The single canonical managed block (gen_manifest.build_artifacts): the CLAUDE.md RULES-INDEX block.
MANAGED_BLOCK_ID = "RULES-INDEX"
MANAGED_BLOCK_PATH = "CLAUDE.md"


def strict_manifest(data, where):
    """The COMPLETE manifest schema (4.1), mirrored from check_manifest.load_manifest and run on head AND
    predecessor objects in genesis, non-genesis, audit, pre-tag, and post-tag (round-5 finding 1: the prior
    validator rejected extra top-level keys but did not REQUIRE the exact keyset, so a missing [[sources]]/
    [[artifacts]] section defaulted to an empty list and a bogus artifact key slipped through). Requires the
    EXACT mandatory top-level keyset (a deleted-all-sources manifest omits the `sources` key and is rejected);
    format-version == 1; release-version a bare SemVer; genesis a real boolean; tree-sha256 64 lowercase hex;
    [[sources]] rows EXACTLY {path, bytes, sha256} (non-empty logical path, non-negative integer bytes,
    64-hex digest, no duplicate path, sorted bytewise); [[artifacts]] rows the EXACT kind-specific keyset
    (file: the base four; managed-block: the base four plus block-id) with a 64-hex sha256 and unique
    artifact-id. The full SOURCES-set-equality against the tracked tree stays check_manifest's."""
    if not isinstance(data, dict):
        raise SchemaError("{}: manifest is not a table".format(where))
    if set(data) != MANIFEST_TOP_KEYS:
        raise SchemaError("{}: top-level keys must be EXACTLY {} (found {})".format(
            where, sorted(MANIFEST_TOP_KEYS), sorted(data)))
    if data["format-version"] != 1:
        raise SchemaError("{}: format-version must be exactly 1".format(where))
    if not isinstance(data["release-version"], str) or _parse(data["release-version"]) is None:
        raise SchemaError("{}: release-version {!r} is not a bare SemVer".format(
            where, data["release-version"]))
    if not isinstance(data["genesis"], bool):
        raise SchemaError("{}: genesis must be a real boolean".format(where))
    if not isinstance(data["tree-sha256"], str) or not HEX64_RE.fullmatch(data["tree-sha256"]):
        raise SchemaError("{}: tree-sha256 is not 64 lowercase hex".format(where))
    if not isinstance(data["sources"], list):
        raise SchemaError("{}: [[sources]] is not an array".format(where))
    seen_paths = []
    for i, row in enumerate(data["sources"], 1):
        rw = "{} sources row #{}".format(where, i)
        if not isinstance(row, dict) or set(row) != MANIFEST_SOURCES_KEYS:
            raise SchemaError("{}: keys are not exactly {}".format(rw, sorted(MANIFEST_SOURCES_KEYS)))
        if not is_canonical_relpath(row["path"]):
            raise SchemaError("{}: path {!r} is not a canonical repo-relative path (4.1/L425)".format(
                rw, row["path"]))
        if not isinstance(row["bytes"], int) or isinstance(row["bytes"], bool) or row["bytes"] < 0:
            raise SchemaError("{}: bytes is not a non-negative integer".format(rw))
        if not isinstance(row["sha256"], str) or not HEX64_RE.fullmatch(row["sha256"]):
            raise SchemaError("{}: sha256 is not 64 lowercase hex".format(rw))
        if row["path"] in seen_paths:
            raise SchemaError("{}: duplicate sources path {!r}".format(rw, row["path"]))
        seen_paths.append(row["path"])
    if seen_paths != sorted(seen_paths):
        raise SchemaError("{}: sources are not sorted bytewise by path".format(where))
    if not isinstance(data["artifacts"], list):
        raise SchemaError("{}: [[artifacts]] is not an array".format(where))
    seen_ids = set()
    for i, row in enumerate(data["artifacts"], 1):
        rw = "{} artifacts row #{}".format(where, i)
        if not isinstance(row, dict):
            raise SchemaError("{}: not a table".format(rw))
        kind = row.get("kind")
        # TYPE-check kind BEFORE the dict membership test (round-7 finding 1): kind = ["file"] is unhashable
        # and would raise an uncaught TypeError (exit 1) instead of a SchemaError (exit 2).
        if not isinstance(kind, str) or kind not in MANIFEST_ARTIFACT_KEYS:
            raise SchemaError("{}: kind must be one of {}".format(rw, sorted(MANIFEST_ARTIFACT_KEYS)))
        if set(row) != MANIFEST_ARTIFACT_KEYS[kind]:
            raise SchemaError("{}: {} row keys must be EXACTLY {} (found {})".format(
                rw, kind, sorted(MANIFEST_ARTIFACT_KEYS[kind]), sorted(row)))
        if not isinstance(row["artifact-id"], str) or not row["artifact-id"]:
            raise SchemaError("{}: missing or non-string artifact-id".format(rw))
        if row["artifact-id"] in seen_ids:
            raise SchemaError("{}: duplicate artifact-id {!r}".format(rw, row["artifact-id"]))
        seen_ids.add(row["artifact-id"])
        if not is_canonical_relpath(row["path"]):
            raise SchemaError("{}: artifact path {!r} is not a canonical repo-relative path "
                              "(4.1/L425)".format(rw, row["path"]))
        if not isinstance(row["sha256"], str) or not HEX64_RE.fullmatch(row["sha256"]):
            raise SchemaError("{}: sha256 is not 64 lowercase hex".format(rw))
        if kind == "managed-block":
            # The managed-block artifact is the CLAUDE.md RULES-INDEX block; validate the block-id VALUE and
            # its bound path against the canonical set (round-6 finding 2: a block-id of 7 was accepted).
            if row["block-id"] != MANAGED_BLOCK_ID:
                raise SchemaError("{}: managed-block block-id must be {!r}, not {!r}".format(
                    rw, MANAGED_BLOCK_ID, row["block-id"]))
            if row["path"] != MANAGED_BLOCK_PATH:
                raise SchemaError("{}: managed-block path must be {!r}, not {!r}".format(
                    rw, MANAGED_BLOCK_PATH, row["path"]))
        # artifact-id is the GENERATED grammar '<renderer-id>:<path>' (a managed-block adds '#<block-id>'),
        # bound to the row's own path/block fields (this round's #4): a bare non-empty check let an embedded
        # newline through. Partition on the FIRST ':' (a renderer-id is a slug and never contains ':'); the
        # prefix must be a slug and the remainder must equal the row's own path (plus '#<block-id>' for the
        # managed block), so the id cannot carry a stray control character or drift from its binding.
        rid_prefix, sep, suffix = row["artifact-id"].partition(":")
        expected_suffix = (row["path"] + "#" + row["block-id"]) if kind == "managed-block" else row["path"]
        if sep != ":" or not SLUG_RE.fullmatch(rid_prefix) or suffix != expected_suffix:
            raise SchemaError("{}: artifact-id {!r} must be '<renderer-id-slug>:{}' bound to its "
                              "fields".format(rw, row["artifact-id"], expected_suffix))
    return data


RENDERER_ROW_KEYS = frozenset({"renderer-id", "entrypoint", "semantics-revision", "targets", "closure",
                               "code-digest"})


def strict_renderers(data, where):
    """EXHAUSTIVE renderer-declaration schema (6.5), on head AND predecessor objects (round-7 finding 8):
    format-version == 1; [[renderer]] a non-empty array whose every row carries EXACTLY
    {renderer-id, entrypoint, semantics-revision, targets, closure, code-digest}; a non-empty unique
    renderer-id; a canonical repo-relative entrypoint; a non-negative integer semantics-revision; a
    non-empty targets list of canonical repo-relative paths (a trailing slash allowed for a tree target); a
    non-empty closure list of canonical repo-relative paths whose FIRST element is the entrypoint (the
    closure/entrypoint binding); and a 64-hex code-digest. The freshness (closure recomputation) stays
    gen_renderers'; this asserts the exhaustive declaration shape on both objects."""
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
        if not isinstance(row, dict) or set(row) != RENDERER_ROW_KEYS:
            raise SchemaError("{}: keys are not EXACTLY {} (found {})".format(
                rw, sorted(RENDERER_ROW_KEYS), sorted(row) if isinstance(row, dict) else type(row).__name__))
        rid = row["renderer-id"]
        # renderer-id is the authoritative slug (this round's #3): SLUG_RE is the gate's authoritative
        # renderer-id syntax, so validate the DECLARATION against it rather than accept any non-empty string
        # (a value like "Bad ID" with a space or capital must fail on head AND predecessor objects).
        if not isinstance(rid, str) or not SLUG_RE.fullmatch(rid):
            raise SchemaError("{}: renderer-id {!r} is not a valid slug".format(rw, rid))
        if rid in seen:
            raise SchemaError("{}: duplicate renderer-id {!r}".format(rw, rid))
        seen.add(rid)
        if not is_canonical_relpath(row["entrypoint"]):
            raise SchemaError("{}: entrypoint {!r} is not a canonical repo-relative path".format(
                rw, row["entrypoint"]))
        rev = row["semantics-revision"]
        if not isinstance(rev, int) or isinstance(rev, bool) or rev < 0:
            raise SchemaError("{}: semantics-revision must be a non-negative integer".format(rw))
        targets = row["targets"]
        if not isinstance(targets, list) or not targets \
                or not all(is_canonical_relpath(t, allow_trailing_slash=True) for t in targets):
            raise SchemaError("{}: targets must be a non-empty list of canonical repo-relative paths".format(
                rw))
        closure = row["closure"]
        if not isinstance(closure, list) or not closure \
                or not all(is_canonical_relpath(c) for c in closure):
            raise SchemaError("{}: closure must be a non-empty list of canonical repo-relative paths".format(
                rw))
        if closure[0] != row["entrypoint"]:
            raise SchemaError("{}: closure[0] {!r} must be the entrypoint {!r} (closure/entrypoint "
                              "binding)".format(rw, closure[0], row["entrypoint"]))
        cd = row["code-digest"]
        if not isinstance(cd, str) or not HEX64_RE.fullmatch(cd):
            raise SchemaError("{}: code-digest is not 64 lowercase hex".format(rw))
    return data


ORDER_PRESENTATION_KEYS = {"families", "aiqt-facets", "security-facets", "tie-breaker"}
# The CONTROLLED presentation vocabularies (this round's #4), sourced from the authoritative operative
# constants: the AIQT facet codes are the union of gen_rules.TIER_FACETS, the security facet codes are
# gen_rules.CIA_FACETS, the three rule families are apex/aiqt/security, and the declared tie-break
# strategy is slug-bytewise. strict_order rejects an out-of-vocabulary entry or a duplicate here (exit 2);
# the operative-EQUALITY comparison (does the record list the WHOLE vocab, in order) stays check_manifest's
# exit-1 leg.
AIQT_FACET_VOCAB = frozenset().union(*TIER_FACETS.values())
SECURITY_FACET_VOCAB = frozenset(CIA_FACETS)
ORDER_FAMILY_VOCAB = frozenset({"apex", "aiqt", "security"})
ORDER_TIE_BREAKERS = frozenset({"slug-bytewise"})
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
    seen_ranks = set()
    seen_members = set()
    for i, t in enumerate(tiers, 1):
        tw = "{} precedence-tier #{}".format(where, i)
        if not isinstance(t, dict) or set(t) != {"rank", "members", "members-are-equal"}:
            raise SchemaError("{}: keys are not exactly rank/members/members-are-equal".format(tw))
        if not _is_int(t["rank"]) or t["rank"] < 0:
            raise SchemaError("{}: rank must be a non-negative integer".format(tw))
        if t["rank"] in seen_ranks:
            raise SchemaError("{}: duplicate precedence rank {}".format(tw, t["rank"]))
        seen_ranks.add(t["rank"])
        if not isinstance(t["members"], list) or not t["members"] \
                or not all(isinstance(m, str) and m for m in t["members"]):
            raise SchemaError("{}: members must be a non-empty list of strings".format(tw))
        # tier members are AIQT facet codes drawn from the controlled vocab (this round's #4) and each
        # facet has EXACTLY ONE rank: a GLOBAL seen_members set across ALL tiers rejects a facet placed in
        # two tiers as well as a repeat within one tier (this round's #5), never a MINOR finding.
        for m in t["members"]:
            if m in seen_members:
                raise SchemaError("{}: facet {!r} appears in more than one precedence tier (a facet has "
                                  "exactly one rank)".format(tw, m))
            seen_members.add(m)
        bad = [m for m in t["members"] if m not in AIQT_FACET_VOCAB]
        if bad:
            raise SchemaError("{}: member(s) {} are not AIQT facet codes {}".format(
                tw, sorted(bad), sorted(AIQT_FACET_VOCAB)))
        if not isinstance(t["members-are-equal"], bool):
            raise SchemaError("{}: members-are-equal must be a boolean".format(tw))
    pres = data.get("presentation-order")
    if not isinstance(pres, dict) or set(pres) != ORDER_PRESENTATION_KEYS:
        raise SchemaError("{}: [presentation-order] keys are not exactly {}".format(
            where, sorted(ORDER_PRESENTATION_KEYS)))
    # Each presentation list draws from its CONTROLLED vocabulary with no duplicate and no out-of-vocab
    # entry (this round's #4): families from apex/aiqt/security, aiqt-facets from the AIQT facet codes,
    # security-facets from the CIA facet codes. tie-breaker is one of the declared strategies.
    for key, vocab in (("families", ORDER_FAMILY_VOCAB), ("aiqt-facets", AIQT_FACET_VOCAB),
                       ("security-facets", SECURITY_FACET_VOCAB)):
        val = pres[key]
        if not isinstance(val, list) or not val or not all(isinstance(x, str) and x for x in val):
            raise SchemaError("{}: presentation-order.{} must be a non-empty list of strings".format(
                where, key))
        if len(set(val)) != len(val):
            raise SchemaError("{}: presentation-order.{} has a duplicate entry".format(where, key))
        bad = [x for x in val if x not in vocab]
        if bad:
            raise SchemaError("{}: presentation-order.{} out-of-vocabulary entr(y/ies) {} (valid: {})".format(
                where, key, sorted(bad), sorted(vocab)))
    if pres["tie-breaker"] not in ORDER_TIE_BREAKERS:
        raise SchemaError("{}: presentation-order.tie-breaker {!r} is not one of {}".format(
            where, pres["tie-breaker"], sorted(ORDER_TIE_BREAKERS)))


def strict_clause_inventory(data, where):
    """EXHAUSTIVE 7.2 clause-inventory schema (round-4 finding 2): a [[clause]] array of tables, each with
    EXACTLY the 7.2 keyset; a well-formed clause-id (UNIQUE) whose corpus part equals a well-formed
    corpus-id field; a non-empty source-path; positive integer start-line/end-line with end >= start; a
    non-empty canonical-text; and a 64-lowercase-hex source-digest. The full source-file span/text/digest
    CONSISTENCY (reading the rule sources) stays check_clauses'; this validates the record's own structure
    exhaustively on BOTH objects (the round-2/3 three-field guard accepted malformed ids and missing span/
    digest fields). The EXACT top-level keyset is required first (round-6 finding 3: an unknown top-level
    key like `bogus` was ignored). Returns the rows list."""
    if not isinstance(data, dict) or set(data) != {"clause"}:
        raise SchemaError("{}: top-level keys must be EXACTLY {{'clause'}} (found {})".format(
            where, sorted(data) if isinstance(data, dict) else type(data).__name__))
    rows = data["clause"]
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
        # source-path is a CANONICAL repo-relative path (round-5 finding 1): the prior non-empty check let a
        # non-canonical HEAD path ('.aiqt/core/rules/../rules/...') through, which check_clauses then RESOLVED
        # to the real file and passed, so the delta computed clean. is_canonical_relpath rejects '.', '..',
        # '//', backslash, control characters, and host-absolute forms on head AND predecessor objects.
        if not is_canonical_relpath(row.get("source-path")):
            raise SchemaError("{}: source-path {!r} is not a canonical repo-relative path (no '.', '..', "
                              "'//', backslash, control character, or host-absolute form; 4.1/L425)".format(
                                  rw, row.get("source-path")))
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
    for section in ("born", "tombstone", "successor"):
        rows = data.get(section, [])
        if not isinstance(rows, list):
            raise SchemaError("{}: the {} section is not an array of tables".format(where, section))
        # Array-level invariant (round-6 array-invariant audit): an id is unique WITHIN each section, so
        # the section carries at most one born (birth is once), one tombstone, or one successor row per id.
        # The born dedup existed; tombstone/successor are the symmetric gap this closes. Dedup is per section
        # (an id legitimately appears in born AND a retirement section: born then retired).
        seen_ids = set()
        for i, row in enumerate(rows, 1):
            rw = "{} {} row #{}".format(where, section, i)
            if not isinstance(row, dict) or set(row) != IDHISTORY_ROW_KEYS[section]:
                raise SchemaError("{}: keys are not exactly {}".format(
                    rw, sorted(IDHISTORY_ROW_KEYS[section])))
            if not _valid_register_id(row["id"]):
                raise SchemaError("{}: id {!r} is neither a well-formed corpus-id nor clause-id".format(
                    rw, row["id"]))
            if row["id"] in seen_ids:
                raise SchemaError("{}: duplicate {} row for {!r}".format(rw, section, row["id"]))
            seen_ids.add(row["id"])
            key = rel_key[section]
            if not isinstance(row[key], str) or _parse(row[key]) is None:
                raise SchemaError("{}: {} is not a bare SemVer".format(rw, key))
            if section == "successor" and not _valid_register_id(row["successor-id"]):
                raise SchemaError("{}: successor-id {!r} is not a well-formed id".format(
                    rw, row["successor-id"]))
    return data


# --- per-kind disposition evidence (6.6 / VER-CORE-SPEC.md:1037) ------------------------------------

# A host label is an LDH domain label (letter/digit/hyphen, not hyphen-bounded), the whole name at most 253
# chars; urlparse lowercases the host, so the class is lowercase. An IP literal is validated by ipaddress.
_URL_HOSTNAME_RE = re.compile(
    r"^(?=.{1,253}$)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*$")


def _is_valid_url_host(host):
    """True if host is a well-formed LDH domain name or an IP literal (v4, or v6 with the brackets already
    stripped by urlparse.hostname). A malformed host (embedded space, empty label, out-of-range octet) is
    rejected."""
    if not isinstance(host, str) or not host:
        return False
    if _URL_HOSTNAME_RE.fullmatch(host):
        if all(part.isdigit() for part in host.split(".")):
            try:                       # a dotted-numeric shape must be a valid IPv4 (octets 0..255)
                ipaddress.IPv4Address(host)
            except ValueError:
                return False
        return True
    try:                               # an IPv6 literal (urlparse.hostname has stripped the [] brackets)
        ipaddress.IPv6Address(host)
        return True
    except ValueError:
        return False


def _is_well_formed_url(value):
    """A syntactically well-formed http(s) URL under a DETERMINISTIC grammar (round-5 finding 3): scheme
    EXACTLY http or https; NO whitespace, control character, or backslash anywhere (urlparse otherwise
    accepts 'https://exa mple.com', a raw space in the host); a host that is a valid LDH domain name or IP
    literal; and a port, if present, an integer in 0..65535 (urlparse.port raises ValueError otherwise).
    Offline: reachability is NOT tested here (the gate does not touch the network); the spec's
    URL-VALIDATED-at-capture reachability is a build-time capture step, disclosed as a residual this offline
    gate does not perform."""
    if not isinstance(value, str) or not value:
        return False
    # A well-formed URL percent-encodes any space or control character, so a raw one (space, 0x00-0x1F, DEL)
    # or a backslash is malformed: reject before urlparse, which would otherwise absorb a space into netloc.
    if any(ord(ch) <= 0x20 or ord(ch) == 0x7f for ch in value) or "\\" in value:
        return False
    # Every percent escape is exactly '%' + two ASCII hex digits (round-6 finding 2): '%zz', a truncated
    # '%a', or a trailing '%' is a malformed escape that urlparse does not reject on its own.
    if re.search(r"%(?![0-9A-Fa-f]{2})", value):
        return False
    try:
        parsed = urlparse(value)
        host, port = parsed.hostname, parsed.port     # .port raises ValueError on a bad/oversized port
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https") or not _is_valid_url_host(host):
        return False
    return port is None or 0 <= port <= 65535


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
    if not isinstance(meas, str) or not meas or not any(ch.isdigit() for ch in meas) \
            or has_control_char(meas):
        raise SchemaError("{}: default-correction observed-measurement {!r} is not a measurement (a value "
                          "carrying a digit and no control character; 6.6/L1038)".format(where, meas))
    # prefix-superset-reference names a stored artefact by repo-relative path (the 6.6 evidence points at the
    # prefix-superset record, e.g. 'qa/prefix.toml'), so it is a CANONICAL repo-relative path, not merely a
    # non-empty string: a traversal or control character is a malformed reference (round-5 sweep).
    ref = row.get("prefix-superset-reference")
    if not is_canonical_relpath(ref):
        raise SchemaError("{}: default-correction prefix-superset-reference {!r} is not a canonical "
                          "repo-relative path (6.6/L1039)".format(where, ref))


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
    # Parse the batch protocol under the EXACT grammar (round-7 finding 3): for each REQUESTED oid the
    # response is "<oid> <type> <size>\n<body of exactly size bytes>\n". A malformed header, a wrong echoed
    # oid, an unknown type, a non-decimal size, a short body, or a missing trailing delimiter is a
    # cannot-evaluate SchemaError, never a raw ValueError or a silently-accepted truncated blob.
    out, i, result = proc.stdout, 0, {}
    for want in uniq:
        nl = out.find(b"\n", i)
        if nl == -1:
            raise SchemaError("git cat-file --batch: truncated header for {}".format(want))
        try:
            header = out[i:nl].decode("ascii")
        except UnicodeDecodeError:
            raise SchemaError("git cat-file --batch: non-ASCII header ({!r})".format(out[i:nl]))
        i = nl + 1
        parts = header.split(" ")
        if len(parts) == 2 and parts[1] == "missing":
            raise SchemaError("git cat-file --batch: object {} is missing".format(parts[0]))
        if len(parts) != 3:
            raise SchemaError("git cat-file --batch: malformed header {!r}".format(header))
        osha, otype, size_s = parts
        if osha != want:
            raise SchemaError("git cat-file --batch: echoed oid {!r} != requested {!r}".format(osha, want))
        if otype not in ("blob", "commit", "tree", "tag"):
            raise SchemaError("git cat-file --batch: unknown object type {!r}".format(otype))
        if not _DECIMAL_RE.fullmatch(size_s):
            raise SchemaError("git cat-file --batch: non-decimal size {!r}".format(size_s))
        size = int(size_s)
        if i + size > len(out):
            raise SchemaError("git cat-file --batch: short body for {} (declared {} bytes)".format(
                osha, size))
        result[osha] = out[i:i + size]
        i += size
        if out[i:i + 1] != b"\n":
            raise SchemaError("git cat-file --batch: missing trailing delimiter after {}".format(osha))
        i += 1
    # Require the WHOLE stream to be consumed (round-8 finding 7): trailing bytes after the last requested
    # object are protocol garbage, and returning without asserting i == len(out) silently accepted a valid
    # response followed by an unexpected trailer as clean blob data.
    if i != len(out):
        raise SchemaError("git cat-file --batch: {} trailing byte(s) after the last requested object; "
                          "fail-closed".format(len(out) - i))
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
        if mode not in ("100644", "100755"):
            raise SchemaError("tree {} blob {!r} has unsupported mode {}".format(
                commit, path_b.decode("utf-8", "replace"), mode))
        try:
            path = path_b.decode("utf-8")
        except UnicodeDecodeError:
            raise SchemaError("non-UTF-8 path in tree {}".format(commit))
        entries.append((mode, osha, path))
    blobs = _cat_file_batch(root, [osha for _mode, osha, _path in entries])
    dest_root = Path(dest).resolve()
    written = set()
    for mode, osha, path in entries:
        target = (Path(dest) / path).resolve()
        if target != dest_root and dest_root not in target.parents:
            raise SchemaError("path {!r} escapes the materialization root".format(path))
        # A filesystem error writing the raw tree is cannot-evaluate, not a clean result (round-5 finding 4).
        # PRESERVE the git file mode exactly (round-7 finding 4): a 100755 blob materialized non-executable
        # would let reproduction verify a DIFFERENT artifact from the candidate.
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(blobs[osha])
            os.chmod(target, 0o755 if mode == "100755" else 0o644)
        except OSError as exc:
            raise SchemaError("cannot materialize {!r} ({})".format(path, exc))
        written.add(path)
    return written
