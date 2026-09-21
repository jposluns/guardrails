"""OPF-D2B PR1: the pure, closed-schema `opf init` Keep-acceptance validator (contract frozen, no I/O).

This module is the PURE contract layer for the D2b `--decisions` Keep-only adoption model. It validates a
serialized Keep acceptance object against a supplied, already-observed context; it performs NO filesystem
discovery, reads, subprocess execution, clock access, random-ID allocation, locking, staging, publication,
or worklog allocation, and it accepts no filename, repository path to inspect, or already_locked/approved
boolean. A VALID result is structural validity plus equality with the supplied observation context; it is
NOT authorization, NOT proof the observer did its work, NOT current-filesystem state, and NOT publication
authority. Only the later operation layer (PR4-PR7), holding its own capability and re-establishing
observations, may act on an acceptance. Import's plan-never-auto-rests rule is unaffected.

validate_keep NEVER raises: every malformed input, including a malformed observation context, resolves to
one of the three statuses (VALID, INVALID, CANNOT-EVALUATE). A malformed ACCEPTANCE is INVALID; a malformed
or unusable OBSERVATION is CANNOT-EVALUATE (a parser result is never a capability, so an unusable
observation can never read as a pass).

Constants verified against source at repo HEAD: ACTOR_KINDS mirrors opf/tools/_opf_schema.py; the reserved
managed set mirrors opf/tools/_opf_store.py (WORKING_DIRNAME=".working", DEFAULT_MACHINE_SUBDIR="toml",
store-level `imports`, archive) and the classify_containment authority (opf/tools/_opf_check.py) plus the
pinned initial views (opf/tools/_opf_init.py) and the .opf.toml pointer. Canonical JSON mirrors the import
acceptance emitter (opf/tools/_opf_import.py).

Run: python3 -I -B opf/tools/_opf_init_contract.py --self-test
Exit: 0 self-test clean; 2 self-test failure.
"""
import hashlib
import json
import re
import sys

KEEP_SCHEMA = 1
KEEP_OPERATION = "opf-init-keep"

# Verified: opf/tools/_opf_schema.py ACTOR_KINDS (NOT person/bot/infra).
ACTOR_KINDS = ("maintainer", "assistant", "automation", "importer")

# Frozen v1 limits (contract constants, never caller-adjustable).
MAX_RAW_BYTES = 1048576
MAX_CANONICAL_BYTES = 1048576
MAX_JSON_NESTING = 16          # root container at depth 1
MAX_DECISIONS = 4096
MAX_INVENTORY_ENTRIES = 4096
MAX_PATH_DEPTH = 32
MAX_AGGREGATE_PATH_BYTES = 1048576
MAX_PATH_BYTES = 4096
MAX_COMPONENT_BYTES = 255
MAX_ACTOR_ID_BYTES = 256
MAX_REASON_BYTES = 4096
MAX_STRING_BYTES = 4096        # the default bound for a general contract string (paths, refs, object ids)
MAX_FILE_SIZE = (1 << 63) - 1
MAX_MODE = 0o777
MAX_IDENTITY_INT = (1 << 64) - 1   # device/inode are unsigned 64-bit

# The reserved managed set under the store, mirroring classify_containment (view targets + machine store +
# imports + archive) plus the pointer. A Keep decision NEVER covers any of these, NOR any path beneath one
# of them (the reserved-ancestor rule): a reserved destination occupied as a directory does not open its
# subtree to Keep. Trailing-slash entries are directory prefixes; others are exact paths that also forbid
# descendants. .opf.toml is the product-root pointer (also excluded by the below-.working/ rule; kept for
# defence in depth). The 13 pinned views mirror _opf_init.py _INITIAL_VIEW_NAMES.
_INITIAL_VIEWS = (
    "TODO.md", "BACKLOG.md", "PIPELINE.md", "DONE.md", "FINDINGS.md", "DECISIONS.md",
    "BLOCKS.md", "HANDOFF.md", "REFERENCES.md", "CONTRIBUTIONS.md", "WORKLOG.md",
    "VERSION.md", "DECISIONS.toml",
)
_RESERVED = (
    ".opf.toml",
    ".working/toml",
    ".working/imports",
    ".working/archive",
) + tuple(".working/" + v for v in _INITIAL_VIEWS)

_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f\ufeff]")
_HEX_RE = re.compile(r"[0-9a-f]+\Z")


class KeepValidation:
    """status in {VALID, INVALID, CANNOT-EVALUATE}; findings an ordered tuple of (code, location, detail);
    model/canonical_bytes/acceptance_digest set only on VALID."""

    def __init__(self, status, findings, model, canonical_bytes, acceptance_digest):
        self.status = status
        self.findings = findings
        self.model = model
        self.canonical_bytes = canonical_bytes
        self.acceptance_digest = acceptance_digest


def _v(status, code, location, detail):
    return KeepValidation(status, ((code, location, detail),), None, None, None)


def canonical_json_bytes(model):
    """Canonical JSON per the import acceptance emitter: sorted keys, compact separators, ASCII escaping,
    no NaN/Infinity, single trailing newline. May raise on a non-serializable model or a lone surrogate;
    callers that face untrusted input wrap it (see _safe_canonical)."""
    return json.dumps(
        model, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("utf-8") + b"\n"


def _safe_canonical(model):
    """Canonical bytes or None if the model cannot be canonicalized (a lone surrogate, a non-finite float
    that slipped in, or a non-serializable value). Never raises."""
    try:
        return canonical_json_bytes(model)
    except (ValueError, TypeError, UnicodeEncodeError):
        return None


def _digest(b):
    return "sha256:" + hashlib.sha256(b).hexdigest()


def _is_int(x):
    """A real JSON integer, never a bool (bool is an int subclass; True == 1 in dict equality)."""
    return type(x) is int


def _bad_string(s, max_len):
    """Reason string if s is not an acceptable bounded, control-free, encodable UTF-8 string, else None.
    Never raises: a lone surrogate that cannot encode is a reason, not an exception."""
    if type(s) is not str:
        return "exact string required"
    try:
        b = s.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        return "not encodable UTF-8 (lone surrogate)"
    if len(b) > max_len:
        return "exceeds max bytes {}".format(max_len)
    if _CONTROL_RE.search(s):
        return "control characters forbidden"
    return None


def _bad_relpath(p):
    """Reason if p is not a canonical, backslash-free, below-root relative path, else None."""
    reason = _bad_string(p, MAX_PATH_BYTES)
    if reason:
        return reason
    if "\\" in p:
        return "backslash forbidden"
    if p.startswith("/") or "//" in p or p.endswith("/"):
        return "absolute, repeated, or trailing separator forbidden"
    parts = p.split("/")
    if len(parts) > MAX_PATH_DEPTH:
        return "exceeds max path depth {}".format(MAX_PATH_DEPTH)
    for part in parts:
        if part in ("", ".", ".."):
            return "invalid path component {!r}".format(part)
        if len(part.encode("utf-8")) > MAX_COMPONENT_BYTES:
            return "path component exceeds max bytes {}".format(MAX_COMPONENT_BYTES)
    return None


_REF_FORBIDDEN = set(" ~^:?*[\\\x7f") | {chr(c) for c in range(0x20)}


def _bad_ref(ref):
    """Reason if ref is not a well-formed refs/heads/... name, else None. A conservative subset of
    git check-ref-format: below refs/heads/, no forbidden character (space ~ ^ : ? * [ backslash,
    control chars, DEL), no '..', no '@{', no '//', no trailing '/' or '.lock', and no component that
    is empty, starts with '.', or ends with '.' or '.lock'."""
    reason = _bad_string(ref, MAX_STRING_BYTES)
    if reason:
        return reason
    if not ref.startswith("refs/heads/"):
        return "must be under refs/heads/"
    if any(ch in _REF_FORBIDDEN for ch in ref) or "@{" in ref or ".." in ref or ref.endswith(".lock"):
        return "malformed ref (forbidden character or sequence)"
    rest = ref[len("refs/heads/"):]
    if rest == "" or rest.endswith("/") or "//" in rest:
        return "malformed ref body"
    for comp in rest.split("/"):
        if comp in ("", ".", "..") or comp.startswith(".") or comp.endswith(".") or comp.endswith(".lock"):
            return "malformed ref component {!r}".format(comp)
    return None


def _is_reserved(path):
    """True if path is a reserved managed destination OR lies beneath one (the reserved-ancestor rule)."""
    for name in _RESERVED:
        if path == name or path.startswith(name + "/"):
            return True
    return False


def _reject_duplicates(pairs):
    d = {}
    for k, val in pairs:
        if k in d:
            raise ValueError("duplicate key {!r}".format(k))
        d[k] = val
    return d


def _parse_float(_s):
    raise ValueError("floats are forbidden")


def _parse_int(s):
    val = int(s)
    if val < -(1 << 63) or val > (1 << 64) - 1:
        raise ValueError("integer out of supported range")
    return val


def _parse_constant(_s):
    raise ValueError("NaN/Infinity forbidden")


def _nesting_ok(text):
    """Bound container nesting with a string/escape-aware scan (braces inside strings do not count)."""
    depth = 0
    seen = 0
    in_str = False
    escape = False
    for ch in text:
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
        else:
            if ch == '"':
                in_str = True
            elif ch in "{[":
                depth += 1
                if depth > seen:
                    seen = depth
            elif ch in "}]":
                depth -= 1
    return seen <= MAX_JSON_NESTING


def _bad_location(loc):
    """Reason if loc is not a well-formed Location {path: str, identity: {device, inode}}, else None."""
    if type(loc) is not dict or set(loc.keys()) != {"path", "identity"}:
        return "location keys"
    reason = _bad_string(loc["path"], MAX_STRING_BYTES)
    if reason:
        return "location path: " + reason
    if not loc["path"].startswith("/"):
        return "location path must be a non-empty absolute path"
    ident = loc["identity"]
    if type(ident) is not dict or set(ident.keys()) != {"device", "inode"}:
        return "identity keys"
    for k in ("device", "inode"):
        if not _is_int(ident[k]) or ident[k] < 0 or ident[k] > MAX_IDENTITY_INT:
            return "identity {} must be an unsigned 64-bit integer".format(k)
    return None


def _bad_binding(b):
    """Reason if b is not a structurally well-formed Binding, else None. Structural, not merely equal to a
    trusted context: bool-as-int, missing/extra keys, non-string paths, and a bad object_format all refuse."""
    if type(b) is not dict or set(b.keys()) != {
        "product_root", "repository_root", "worktree_git_directory", "common_git_directory",
        "index_path", "product_prefix", "object_format",
    }:
        return "binding keys"
    for k in ("product_root", "repository_root", "worktree_git_directory", "common_git_directory"):
        reason = _bad_location(b[k])
        if reason:
            return "{}: {}".format(k, reason)
    reason = _bad_string(b["index_path"], MAX_STRING_BYTES)
    if reason:
        return "index_path: " + reason
    if not b["index_path"].startswith("/"):
        return "index_path must be a non-empty absolute path"
    # product_prefix is a repository-relative path or "" for the repository root.
    if type(b["product_prefix"]) is not str:
        return "product_prefix must be a string"
    if b["product_prefix"] != "":
        reason = _bad_relpath(b["product_prefix"])
        if reason:
            return "product_prefix: " + reason
    if b["object_format"] not in ("sha1", "sha256"):
        return "object_format must be sha1 or sha256"
    return None


def _bad_head(head, object_format):
    """Reason if head is not a structurally well-formed closed HEAD union, else None."""
    if type(head) is not dict or "kind" not in head:
        return "head must be an object with a kind"
    kind = head["kind"]
    if kind == "commit":
        if set(head.keys()) != {"kind", "oid", "binding"}:
            return "commit head keys"
        oid = head["oid"]
        if type(oid) is not str or not _HEX_RE.match(oid) \
                or len(oid) != (40 if object_format == "sha1" else 64):
            return "commit oid length must match object_format"
        b = head["binding"]
        if type(b) is not dict or "kind" not in b:
            return "commit head binding"
        if b["kind"] == "symbolic":
            if set(b.keys()) != {"kind", "ref"}:
                return "symbolic binding keys"
            reason = _bad_ref(b["ref"])
            if reason:
                return "symbolic ref: " + reason
        elif b["kind"] == "detached":
            if set(b.keys()) != {"kind"}:
                return "detached binding keys"
        else:
            return "unknown head binding kind"
        return None
    if kind == "unborn":
        if set(head.keys()) != {"kind", "symbolic_ref", "target_ref_state"}:
            return "unborn head keys"
        reason = _bad_ref(head["symbolic_ref"])
        if reason:
            return "unborn symbolic_ref: " + reason
        if head["target_ref_state"] != "absent":
            return "unborn target_ref_state must be absent"
        return None
    return "unknown head kind"


def _bad_inventory(inv):
    """Fail-closed validation of the observed inventory, including per-entry metadata and topology. Return
    a reason string, or None if usable. Never raises."""
    if type(inv) is not dict or set(inv.keys()) != {"schema", "scope", "entries"} or type(inv.get("schema")) is not int or inv.get("schema") != 1 or inv.get("scope") != ".working":
        return "inventory must be {schema:1, scope:'.working', entries:[...]}"
    entries = inv.get("entries")
    if type(entries) is not list:
        return "inventory entries must be a list"
    if len(entries) > MAX_INVENTORY_ENTRIES:
        return "inventory exceeds max entries"
    seen = set()
    files = set()
    dirs = set()
    aggregate = 0
    for e in entries:
        if type(e) is not dict or "path" not in e or e.get("kind") not in ("file", "directory"):
            return "malformed inventory entry"
        p = e["path"]
        if _bad_relpath(p) is not None:
            return "malformed inventory path"
        if not p.startswith(".working/"):
            return "inventory entry path outside .working scope"
        if p in seen:
            return "duplicate inventory path"
        seen.add(p)
        aggregate += len(p.encode("utf-8"))
        if e["kind"] == "file":
            if set(e.keys()) != {"path", "kind", "mode", "size", "digest"}:
                return "file entry keys"
            if not _is_int(e["mode"]) or e["mode"] < 0 or e["mode"] > MAX_MODE:
                return "file mode"
            if not _is_int(e["size"]) or e["size"] < 0 or e["size"] > MAX_FILE_SIZE:
                return "file size"
            if type(e["digest"]) is not str or not _DIGEST_RE.match(e["digest"]):
                return "file digest grammar"
            files.add(p)
        else:
            if set(e.keys()) != {"path", "kind", "mode"}:
                return "directory entry keys"
            if not _is_int(e["mode"]) or e["mode"] < 0 or e["mode"] > MAX_MODE:
                return "directory mode"
            dirs.add(p)
    if aggregate > MAX_AGGREGATE_PATH_BYTES:
        return "inventory aggregate path bytes exceeded"
    # Topology: a file may not have a descendant (an impossible tree), and every descendant's immediate
    # ancestors must be directories present in the inventory is NOT required here (a sparse inventory is
    # permitted), but a path under a FILE is a contradiction.
    for p in seen:
        i = p.rfind("/")
        while i != -1:
            parent = p[:i]
            if parent in files:
                return "inventory has a path under a file entry"
            i = parent.rfind("/")
    return None


def validate_keep(raw_bytes, *, observed_context):
    """Validate a serialized Keep acceptance. Pure, never raises: see module docstring."""
    if type(raw_bytes) is not bytes:
        return _v("INVALID", "TYPE", "raw_bytes", "bytes required")
    if len(raw_bytes) > MAX_RAW_BYTES:
        return _v("INVALID", "LIMIT", "raw_bytes", "exceeds raw bytes limit")
    try:
        text = raw_bytes.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return _v("INVALID", "ENCODING", "raw_bytes", "invalid UTF-8")
    if not _nesting_ok(text):
        return _v("INVALID", "LIMIT", "nesting", "exceeds JSON nesting limit")
    try:
        model = json.loads(text, object_pairs_hook=_reject_duplicates,
                           parse_float=_parse_float, parse_int=_parse_int,
                           parse_constant=_parse_constant)
    except ValueError as exc:
        return _v("INVALID", "PARSE", "raw_bytes", str(exc))

    if type(model) is not dict:
        return _v("INVALID", "TYPE", "root", "must be a JSON object")
    if set(model.keys()) != {"schema", "operation", "binding", "head", "inventory_digest",
                             "actor", "decisions"}:
        return _v("INVALID", "SCHEMA", "root", "exact root keys required")

    canon_bytes = _safe_canonical(model)
    if canon_bytes is None:
        return _v("INVALID", "ENCODING", "root", "acceptance not canonicalizable")
    if len(canon_bytes) > MAX_CANONICAL_BYTES:
        return _v("INVALID", "LIMIT", "canonical_bytes", "exceeds canonical bytes limit")

    if type(model["schema"]) is not int or model["schema"] != 1:
        return _v("INVALID", "SCHEMA", "schema", "must be integer 1")
    if model["operation"] != KEEP_OPERATION:
        return _v("INVALID", "SCHEMA", "operation", "must equal KEEP_OPERATION")

    actor = model["actor"]
    if type(actor) is not dict or set(actor.keys()) != {"kind", "id"}:
        return _v("INVALID", "SCHEMA", "actor", "exact actor keys required")
    if actor["kind"] not in ACTOR_KINDS:
        return _v("INVALID", "SCHEMA", "actor.kind", "unknown actor kind")
    reason = _bad_string(actor["id"], MAX_ACTOR_ID_BYTES)
    if reason is not None or not actor["id"].strip():
        return _v("INVALID", "SCHEMA", "actor.id", reason or "blank actor id")

    dig = model["inventory_digest"]
    if type(dig) is not str or not _DIGEST_RE.match(dig):
        return _v("INVALID", "SCHEMA", "inventory_digest", "must be sha256:<64 hex>")

    # Structural validation of the acceptance binding and head (not merely equality with the observation).
    reason = _bad_binding(model["binding"])
    if reason is not None:
        return _v("INVALID", "SCHEMA", "binding", reason)
    reason = _bad_head(model["head"], model["binding"]["object_format"])
    if reason is not None:
        return _v("INVALID", "SCHEMA", "head", reason)

    # Observation context: a parser result is never a capability; equality with the supplied, already
    # validated observation is the contract. A missing/malformed/unusable observation is CANNOT-EVALUATE.
    if type(observed_context) is not dict:
        return _v("CANNOT-EVALUATE", "CONTEXT", "observed_context", "observation must be a mapping")
    for key in ("binding", "head", "inventory"):
        if key not in observed_context:
            return _v("CANNOT-EVALUATE", "CONTEXT", key, "missing observation")
    if _bad_binding(observed_context["binding"]) is not None:
        return _v("CANNOT-EVALUATE", "CONTEXT", "binding", "malformed observed binding")
    if _bad_head(observed_context["head"], observed_context["binding"]["object_format"]) is not None:
        return _v("CANNOT-EVALUATE", "CONTEXT", "head", "malformed observed head")
    inv_reason = _bad_inventory(observed_context["inventory"])
    if inv_reason is not None:
        return _v("CANNOT-EVALUATE", "CONTEXT", "inventory", inv_reason)
    if model["binding"] != observed_context["binding"]:
        return _v("CANNOT-EVALUATE", "CONTEXT", "binding", "binding mismatch")
    if model["head"] != observed_context["head"]:
        return _v("CANNOT-EVALUATE", "CONTEXT", "head", "head mismatch")
    obs_inv_canon = _safe_canonical(observed_context["inventory"])
    if obs_inv_canon is None:
        return _v("CANNOT-EVALUATE", "CONTEXT", "inventory", "observed inventory not canonicalizable")
    if dig != _digest(obs_inv_canon):
        return _v("CANNOT-EVALUATE", "CONTEXT", "inventory_digest", "inventory digest mismatch")

    entries = observed_context["inventory"]["entries"]
    inv_map = {e["path"]: e for e in entries}

    decisions = model["decisions"]
    if type(decisions) is not list:
        return _v("INVALID", "TYPE", "decisions", "must be a list")
    if len(decisions) > MAX_DECISIONS:
        return _v("INVALID", "LIMIT", "decisions", "exceeds max decisions")

    covered = set()
    prev = None
    for i, dec in enumerate(decisions):
        loc = "decisions[{}]".format(i)
        if type(dec) is not dict or set(dec.keys()) != {"path", "kind", "binding", "action", "reason"}:
            return _v("INVALID", "SCHEMA", loc, "exact decision keys required")
        path = dec["path"]
        preason = _bad_relpath(path)
        if preason is not None:
            return _v("INVALID", "PATH", loc + ".path", preason)
        if not path.startswith(".working/"):
            return _v("INVALID", "PATH", loc + ".path", "must be below .working/")
        if prev is not None and path.encode("utf-8") <= prev:
            return _v("INVALID", "ORDER", "decisions", "not in strictly increasing UTF-8 path order")
        prev = path.encode("utf-8")
        if dec["action"] != "keep":
            return _v("INVALID", "SCHEMA", loc + ".action", "must be 'keep'")
        rreason = _bad_string(dec["reason"], MAX_REASON_BYTES)
        if rreason is not None or not dec["reason"].strip():
            return _v("INVALID", "SCHEMA", loc + ".reason", rreason or "blank reason")
        if _is_reserved(path):
            return _v("INVALID", "RESERVED", loc + ".path", "reserved managed path or descendant")
        kind = dec["kind"]
        if kind not in ("file", "directory-group"):
            return _v("INVALID", "SCHEMA", loc + ".kind", "unknown decision kind")
        if path not in inv_map:
            return _v("INVALID", "GHOST", loc, "path not in inventory")

        bnd = dec["binding"]
        if type(bnd) is not dict:
            return _v("INVALID", "SCHEMA", loc + ".binding", "binding must be an object")

        if kind == "file":
            if inv_map[path]["kind"] != "file":
                return _v("INVALID", "TYPE", loc, "inventory entry is not a file")
            if set(bnd.keys()) != {"mode", "size", "digest"}:
                return _v("INVALID", "SCHEMA", loc + ".binding", "exact file binding keys required")
            if not _is_int(bnd["mode"]) or not _is_int(bnd["size"]) or type(bnd["digest"]) is not str:
                return _v("INVALID", "SCHEMA", loc + ".binding", "invalid binding value types")
            if bnd["mode"] < 0 or bnd["mode"] > MAX_MODE or bnd["size"] < 0 or bnd["size"] > MAX_FILE_SIZE:
                return _v("INVALID", "LIMIT", loc + ".binding", "binding value out of bounds")
            if not _DIGEST_RE.match(bnd["digest"]):
                return _v("INVALID", "SCHEMA", loc + ".binding.digest", "digest grammar")
            t = inv_map[path]
            if bnd["mode"] != t["mode"] or bnd["size"] != t["size"] or bnd["digest"] != t["digest"]:
                return _v("CANNOT-EVALUATE", "CONTEXT", loc + ".binding", "file binding mismatch")
            if path in covered:
                return _v("INVALID", "COVERAGE", loc, "duplicate/overlapping coverage")
            covered.add(path)
        else:  # directory-group
            if inv_map[path]["kind"] != "directory":
                return _v("INVALID", "TYPE", loc, "inventory entry is not a directory")
            if set(bnd.keys()) != {"mode", "members_count", "members_digest"}:
                return _v("INVALID", "SCHEMA", loc + ".binding", "exact group binding keys required")
            if not _is_int(bnd["mode"]) or not _is_int(bnd["members_count"]) \
                    or type(bnd["members_digest"]) is not str:
                return _v("INVALID", "SCHEMA", loc + ".binding", "invalid binding value types")
            if bnd["mode"] < 0 or bnd["mode"] > MAX_MODE or bnd["members_count"] < 0:
                return _v("INVALID", "LIMIT", loc + ".binding", "binding value out of bounds")
            if not _DIGEST_RE.match(bnd["members_digest"]):
                return _v("INVALID", "SCHEMA", loc + ".binding.members_digest", "digest grammar")
            members = sorted((e for e in entries if e["path"] == path or e["path"].startswith(path + "/")),
                             key=lambda e: e["path"].encode("utf-8"))
            if bnd["mode"] != inv_map[path]["mode"]:
                return _v("CANNOT-EVALUATE", "CONTEXT", loc + ".binding.mode", "mode mismatch")
            if bnd["members_count"] != len(members):
                return _v("CANNOT-EVALUATE", "CONTEXT", loc + ".binding.members_count", "count mismatch")
            mcanon = _safe_canonical(members)
            if mcanon is None or bnd["members_digest"] != _digest(mcanon):
                return _v("CANNOT-EVALUATE", "CONTEXT", loc + ".binding.members_digest", "digest mismatch")
            for m in members:
                if _is_reserved(m["path"]):
                    return _v("INVALID", "RESERVED", loc, "group contains reserved path {}".format(m["path"]))
                if m["path"] in covered:
                    return _v("INVALID", "COVERAGE", loc, "overlapping coverage at {}".format(m["path"]))
                covered.add(m["path"])

    # Exact coverage: every eligible foreign file and every empty foreign directory is covered once.
    for e in entries:
        p = e["path"]
        if p in covered or _is_reserved(p):
            continue
        if e["kind"] == "file":
            return _v("INVALID", "COVERAGE", "inventory", "uncovered foreign file {}".format(p))
        if e["kind"] == "directory":
            has_child = any(o["path"].startswith(p + "/") for o in entries)
            if not has_child:
                return _v("INVALID", "COVERAGE", "inventory", "uncovered empty directory {}".format(p))

    return KeepValidation("VALID", (), model, canon_bytes, _digest(canon_bytes))


# --------------------------------------------------------------------------------------------------------
# Self-test: in-memory bytes + synthetic observation contexts. Positive (K-P*) and the negative/fail-closed
# matrix (K-N*), including a regression per QA-round-1 finding. No filesystem/subprocess/clock/randomness.

def _mk_binding():
    ident = {"device": 1, "inode": 2}
    return {
        "product_root": {"path": "/a", "identity": ident},
        "repository_root": {"path": "/a", "identity": ident},
        "worktree_git_directory": {"path": "/a/.git", "identity": {"device": 1, "inode": 3}},
        "common_git_directory": {"path": "/a/.git", "identity": {"device": 1, "inode": 3}},
        "index_path": "/a/.git/index",
        "product_prefix": "",
        "object_format": "sha1",
    }


def _mk_ctx(entries):
    inv = {"schema": 1, "scope": ".working", "entries": entries}
    return {"binding": _mk_binding(), "head": {"kind": "commit", "oid": "a" * 40,
            "binding": {"kind": "symbolic", "ref": "refs/heads/main"}}, "inventory": inv}


def _mk_model(ctx, decisions, **over):
    m = {
        "schema": 1, "operation": KEEP_OPERATION,
        "binding": ctx["binding"], "head": ctx["head"],
        "inventory_digest": _digest(canonical_json_bytes(ctx["inventory"])),
        "actor": {"kind": "maintainer", "id": "u"}, "decisions": decisions,
    }
    m.update(over)
    return m


def _file_entry(path, digest="sha256:" + "0" * 64):
    return {"path": path, "kind": "file", "mode": 0o644, "size": 3, "digest": digest}


def _file_dec(path, digest="sha256:" + "0" * 64):
    return {"path": path, "kind": "file", "action": "keep", "reason": "legacy",
            "binding": {"mode": 0o644, "size": 3, "digest": digest}}


def _run_self_test():
    import copy
    checks = []

    def expect(label, raw, ctx, status):
        try:
            got = validate_keep(raw, observed_context=ctx).status
        except Exception as exc:  # the validator must NEVER raise (QA-R1 finding 5)
            got = "RAISED " + type(exc).__name__
        checks.append((label, got == status, "{} != {}".format(got, status)))

    # K-P: positives.
    ctx0 = _mk_ctx([])
    expect("K-P01-empty-valid", canonical_json_bytes(_mk_model(ctx0, [])), ctx0, "VALID")
    ctx1 = _mk_ctx([_file_entry(".working/notes.txt")])
    expect("K-P02-file-keep", canonical_json_bytes(_mk_model(ctx1, [_file_dec(".working/notes.txt")])),
           ctx1, "VALID")
    entries3 = [{"path": ".working/d", "kind": "directory", "mode": 0o755}, _file_entry(".working/d/x.txt")]
    ctx3 = _mk_ctx(entries3)
    mem = sorted(entries3, key=lambda e: e["path"].encode("utf-8"))
    grp = {"path": ".working/d", "kind": "directory-group", "action": "keep", "reason": "tree",
           "binding": {"mode": 0o755, "members_count": len(mem),
                       "members_digest": _digest(canonical_json_bytes(mem))}}
    expect("K-P03-group", canonical_json_bytes(_mk_model(ctx3, [grp])), ctx3, "VALID")

    # K-N: structural / parse negatives.
    expect("K-N01-dup-key",
           b'{"schema":1,"schema":1,"operation":"opf-init-keep","binding":{},"head":{},'
           b'"inventory_digest":"sha256:' + b"0" * 64 + b'","actor":{"kind":"maintainer","id":"u"},'
           b'"decisions":[]}', ctx0, "INVALID")
    expect("K-N02-not-object", b'[]', ctx0, "INVALID")
    expect("K-N03-extra-root-key", canonical_json_bytes(_mk_model(ctx0, [], extra=1)), ctx0, "INVALID")
    expect("K-N04-bad-schema", canonical_json_bytes(_mk_model(ctx0, [], schema=2)), ctx0, "INVALID")
    expect("K-N05-bool-schema", canonical_json_bytes(_mk_model(ctx0, [], schema=True)), ctx0, "INVALID")
    expect("K-N06-bad-operation", canonical_json_bytes(_mk_model(ctx0, [], operation="x")), ctx0, "INVALID")
    expect("K-N07-bad-actor-kind",
           canonical_json_bytes(_mk_model(ctx0, [], actor={"kind": "person", "id": "u"})), ctx0, "INVALID")
    expect("K-N08-blank-actor-id",
           canonical_json_bytes(_mk_model(ctx0, [], actor={"kind": "maintainer", "id": "  "})), ctx0,
           "INVALID")
    expect("K-N09-float", b'{"schema":1.0}', ctx0, "INVALID")
    expect("K-N10-nesting", b'{"a":' + b"[" * 40 + b"]" * 40 + b"}", ctx0, "INVALID")
    expect("K-N11-bad-utf8", b'{"a":"\xff"}', ctx0, "INVALID")
    ctxf = _mk_ctx([_file_entry(".working/f.txt")])
    expect("K-N12-backslash",
           canonical_json_bytes(_mk_model(ctxf, [_file_dec(".working\\f.txt")])), ctxf, "INVALID")
    expect("K-N13-absolute", canonical_json_bytes(_mk_model(ctxf, [_file_dec("/etc/passwd")])), ctxf,
           "INVALID")
    expect("K-N14-dotdot", canonical_json_bytes(_mk_model(ctxf, [_file_dec(".working/../x")])), ctxf,
           "INVALID")
    expect("K-N15-not-below-working",
           canonical_json_bytes(_mk_model(ctxf, [_file_dec("outside.txt")])), ctxf, "INVALID")
    ctxr = _mk_ctx([_file_entry(".working/TODO.md")])
    expect("K-N16-reserved-view",
           canonical_json_bytes(_mk_model(ctxr, [_file_dec(".working/TODO.md")])), ctxr, "INVALID")
    ctxm = _mk_ctx([_file_entry(".working/toml/x.toml")])
    expect("K-N17-reserved-machine",
           canonical_json_bytes(_mk_model(ctxm, [_file_dec(".working/toml/x.toml")])), ctxm, "INVALID")
    expect("K-N18-ghost", canonical_json_bytes(_mk_model(ctxf, [_file_dec(".working/absent.txt")])), ctxf,
           "INVALID")
    ctxab = _mk_ctx([_file_entry(".working/a"), _file_entry(".working/b")])
    expect("K-N19-order",
           canonical_json_bytes(_mk_model(ctxab, [_file_dec(".working/b"), _file_dec(".working/a")])),
           ctxab, "INVALID")
    expect("K-N20-uncovered-file", canonical_json_bytes(_mk_model(ctxf, [])), ctxf, "INVALID")
    expect("K-N21-binding-mismatch",
           canonical_json_bytes(_mk_model(ctx0, [], binding=dict(_mk_binding(), index_path="/other/.git/index"))),
           ctx0, "CANNOT-EVALUATE")
    expect("K-N22-inv-digest-mismatch",
           canonical_json_bytes(_mk_model(ctx0, [], inventory_digest="sha256:" + "1" * 64)), ctx0,
           "CANNOT-EVALUATE")
    expect("K-N23-file-binding-mismatch",
           canonical_json_bytes(_mk_model(ctxf, [_file_dec(".working/f.txt", digest="sha256:" + "9" * 64)])),
           ctxf, "CANNOT-EVALUATE")

    # QA-R1 regression cases.
    # F1: bool-as-int in the acceptance binding (device=True) is INVALID (structural), not VALID.
    m_f1 = copy.deepcopy(_mk_model(ctx0, []))
    m_f1["binding"]["product_root"]["identity"]["device"] = True
    expect("K-N24-f1-bool-binding", canonical_json_bytes(m_f1), ctx0, "INVALID")
    # F1: a malformed observed binding is CANNOT-EVALUATE, not VALID/raise.
    ctx_badbind = _mk_ctx([])
    ctx_badbind["binding"] = None
    expect("K-N25-f1-null-obs-binding", canonical_json_bytes(_mk_model(ctx0, [])), ctx_badbind,
           "CANNOT-EVALUATE")
    # F2: a directory-group over an inventory whose child lacks metadata is CANNOT-EVALUATE.
    bad_entries = [{"path": ".working/d", "kind": "directory", "mode": 0o755},
                   {"path": ".working/d/x", "kind": "file"}]
    ctx_f2 = {"binding": _mk_binding(), "head": ctx0["head"],
              "inventory": {"schema": 1, "scope": ".working", "entries": bad_entries}}
    g2 = {"path": ".working/d", "kind": "directory-group", "action": "keep", "reason": "legacy",
          "binding": {"mode": 0o755, "members_count": 2, "members_digest": "sha256:" + "0" * 64}}
    m_f2 = _mk_model(ctx0, [g2])
    m_f2["inventory_digest"] = "sha256:" + "0" * 64  # irrelevant; inventory is malformed first
    expect("K-N26-f2-malformed-inventory-meta", canonical_json_bytes(m_f2), ctx_f2, "CANNOT-EVALUATE")
    # F2: a file entry with a descendant (impossible topology) is CANNOT-EVALUATE.
    ctx_topo = {"binding": _mk_binding(), "head": ctx0["head"],
                "inventory": {"schema": 1, "scope": ".working",
                              "entries": [_file_entry(".working/a"), _file_entry(".working/a/b")]}}
    expect("K-N27-f2-file-with-descendant", canonical_json_bytes(_mk_model(ctx0, [])), ctx_topo,
           "CANNOT-EVALUATE")
    # F3: a reserved view occupied as a directory does not open its subtree to a Keep.
    ctx_f3 = _mk_ctx([{"path": ".working/TODO.md", "kind": "directory", "mode": 0o755},
                      _file_entry(".working/TODO.md/x")])
    expect("K-N28-f3-reserved-ancestor",
           canonical_json_bytes(_mk_model(ctx_f3, [_file_dec(".working/TODO.md/x")])), ctx_f3, "INVALID")
    # F4: a malformed HEAD ref is INVALID (acceptance).
    m_f4 = copy.deepcopy(_mk_model(ctx0, []))
    m_f4["head"]["binding"]["ref"] = "refs/heads/../x"
    expect("K-N29-f4-bad-ref", canonical_json_bytes(m_f4), ctx0, "INVALID")
    # F5: NaN never raises; it is INVALID.
    raw_nan = canonical_json_bytes(_mk_model(ctx0, [])).replace(b'"schema":1', b'"schema":NaN')
    expect("K-N30-f5-nan", raw_nan, ctx0, "INVALID")
    # F5: a lone surrogate in actor.id never raises; it is INVALID.
    expect("K-N31-f5-surrogate", canonical_json_bytes(_mk_model(ctx0, [])).replace(
        b'"id":"u"', b'"id":"\\ud800"'), ctx0, "INVALID")
    # F5: a non-dict observed_context never raises; it is CANNOT-EVALUATE.
    expect("K-N32-f5-nondict-ctx", canonical_json_bytes(_mk_model(ctx0, [])), None, "CANNOT-EVALUATE")

    # QA-R2 regressions.
    m_ref = copy.deepcopy(_mk_model(ctx0, []))
    m_ref["head"]["binding"]["ref"] = "refs/heads/a:b"
    expect("K-N33-r2-bad-ref-char", canonical_json_bytes(m_ref), ctx0, "INVALID")
    m_ref2 = copy.deepcopy(_mk_model(ctx0, []))
    m_ref2["head"]["binding"]["ref"] = "refs/heads/main."
    expect("K-N34-r2-ref-trailing-dot", canonical_json_bytes(m_ref2), ctx0, "INVALID")
    m_emp = copy.deepcopy(_mk_model(ctx0, []))
    m_emp["binding"]["product_root"]["path"] = ""
    expect("K-N35-r2-empty-location", canonical_json_bytes(m_emp), ctx0, "INVALID")
    m_oid = copy.deepcopy(_mk_model(ctx0, []))
    m_oid["head"]["oid"] = "a" * 64
    expect("K-N36-r2-oid-format-mismatch", canonical_json_bytes(m_oid), ctx0, "INVALID")
    ctx_scope = {"binding": _mk_binding(), "head": ctx0["head"],
                 "inventory": {"schema": 1, "scope": ".working", "entries": [_file_entry(".opf.toml")]}}
    expect("K-N37-r2-inventory-scope", canonical_json_bytes(_mk_model(ctx0, [])), ctx_scope, "CANNOT-EVALUATE")
    ctx_extra = {"binding": _mk_binding(), "head": ctx0["head"],
                 "inventory": {"schema": 1, "scope": ".working", "entries": [], "extra": True}}
    expect("K-N38-r2-inventory-unknown-key", canonical_json_bytes(_mk_model(ctx0, [])), ctx_extra, "CANNOT-EVALUATE")
    m_c1 = copy.deepcopy(_mk_model(ctx0, []))
    m_c1["actor"]["id"] = "a\u0080b"
    expect("K-N39-r2-c1-control", canonical_json_bytes(m_c1), ctx0, "INVALID")

    # QA-R3: every pinned reserved view is refused as a Keep (defense in depth for the _RESERVED set,
    # so a source mutation that drops a view from _RESERVED is caught here, not only by the gate).
    for _view in _INITIAL_VIEWS:
        _vp = ".working/" + _view
        _cv = _mk_ctx([_file_entry(_vp)])
        expect("K-N40-reserved-view-" + _view,
               canonical_json_bytes(_mk_model(_cv, [_file_dec(_vp)])), _cv, "INVALID")

    failed = [(lbl, why) for (lbl, ok, why) in checks if not ok]
    for lbl, why in failed:
        sys.stderr.write("SELF-TEST FAIL {}: {}\n".format(lbl, why))
    if failed:
        sys.exit(2)
    sys.stdout.write("PASS _opf_init_contract self-test: {} cases\n".format(len(checks)))
    sys.exit(0)


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        _run_self_test()
    sys.stderr.write("usage: python3 -I -B _opf_init_contract.py --self-test\n")
    sys.exit(2)
