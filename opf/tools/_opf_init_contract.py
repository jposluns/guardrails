"""OPF-D2B PR1: the pure, closed-schema `opf init` Keep-acceptance validator (contract frozen, no I/O).

This module is the PURE contract layer for the D2b `--decisions` Keep-only adoption model. It validates a
serialized Keep acceptance object against a supplied, already-observed context; it performs NO filesystem
discovery, reads, subprocess execution, clock access, random-ID allocation, locking, staging, publication,
or worklog allocation, and it accepts no filename, repository path to inspect, or already_locked/approved
boolean. A VALID result is structural validity plus equality with the supplied observation context; it is
NOT authorization, NOT proof the observer did its work, NOT current-filesystem state, and NOT publication
authority. Only the later operation layer (PR4-PR7), holding its own capability and re-establishing
observations, may act on an acceptance. Import's plan-never-auto-rests rule is unaffected.

Constants verified against source at repo HEAD bd815bd: ACTOR_KINDS mirrors opf/tools/_opf_schema.py;
the reserved managed set mirrors opf/tools/_opf_store.py (WORKING_DIRNAME=".working",
DEFAULT_MACHINE_SUBDIR="toml", store-level `imports`, archive) and the classify_containment authority
(opf/tools/_opf_check.py:1720) plus the pinned initial views (opf/tools/_opf_init.py:36) and the .opf.toml
pointer. Canonical JSON mirrors the import acceptance emitter (opf/tools/_opf_import.py:2074).

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
MAX_STRING_BYTES = 4096
MAX_FILE_SIZE = (1 << 63) - 1
MAX_MODE = 0o777

# The reserved managed set under the store, mirroring classify_containment (view targets + machine store +
# imports + archive) plus the pointer. A Keep decision NEVER covers any of these. Trailing-slash entries are
# directory prefixes; others are exact paths. .opf.toml is the product-root pointer (also excluded by the
# below-.working/ rule, kept here for defence in depth). The 13 pinned views mirror _opf_init.py.
_INITIAL_VIEWS = (
    "TODO.md", "BACKLOG.md", "PIPELINE.md", "DONE.md", "FINDINGS.md", "DECISIONS.md",
    "BLOCKS.md", "HANDOFF.md", "REFERENCES.md", "CONTRIBUTIONS.md", "WORKLOG.md",
    "VERSION.md", "DECISIONS.toml",
)
RESERVED = (
    (".opf.toml", False),
    (".working/toml/", True),
    (".working/imports/", True),
    (".working/archive/", True),
) + tuple((".working/" + v, False) for v in _INITIAL_VIEWS)


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
    no NaN/Infinity, single trailing newline."""
    return json.dumps(
        model, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("utf-8") + b"\n"


def _digest(b):
    return "sha256:" + hashlib.sha256(b).hexdigest()


_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


def _bad_string(s, max_len):
    """Return a reason string if s is not an acceptable bounded control-free UTF-8 string, else None."""
    if type(s) is not str:
        return "exact string required"
    b = s.encode("utf-8", errors="strict")  # str is already valid Unicode; length bound only
    if len(b) > max_len:
        return "exceeds max bytes {}".format(max_len)
    if _CONTROL_RE.search(s):
        return "control characters forbidden"
    return None


def _bad_path(p):
    """Return a reason if p is not a canonical, backslash-free, below-.working relative path, else None."""
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


def _is_reserved(path):
    for name, is_prefix in RESERVED:
        if is_prefix:
            if path == name.rstrip("/") or path.startswith(name):
                return True
        elif path == name:
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


def _validate_head(head):
    """Structurally validate the closed HEAD union. Return a reason string, or None if well-formed."""
    if type(head) is not dict or "kind" not in head:
        return "head must be an object with a kind"
    kind = head["kind"]
    if kind == "commit":
        if set(head.keys()) != {"kind", "oid", "binding"}:
            return "commit head keys"
        oid = head["oid"]
        if type(oid) is not str or not re.fullmatch(r"[0-9a-f]+", oid) or len(oid) not in (40, 64):
            return "commit oid must be 40 or 64 lowercase hex"
        b = head["binding"]
        if type(b) is not dict or "kind" not in b:
            return "commit head binding"
        if b["kind"] == "symbolic":
            if set(b.keys()) != {"kind", "ref"} or type(b["ref"]) is not str \
                    or not b["ref"].startswith("refs/heads/"):
                return "symbolic binding ref"
        elif b["kind"] == "detached":
            if set(b.keys()) != {"kind"}:
                return "detached binding keys"
        else:
            return "unknown head binding kind"
        return None
    if kind == "unborn":
        if set(head.keys()) != {"kind", "symbolic_ref", "target_ref_state"}:
            return "unborn head keys"
        if type(head["symbolic_ref"]) is not str or not head["symbolic_ref"].startswith("refs/heads/"):
            return "unborn symbolic_ref"
        if head["target_ref_state"] != "absent":
            return "unborn target_ref_state must be absent"
        return None
    return "unknown head kind"


def _bad_inventory(inv):
    """Fail-closed validation of the observed inventory. Return a reason string, or None if usable."""
    if type(inv) is not dict or inv.get("schema") != 1 or inv.get("scope") != ".working":
        return "inventory must be {schema:1, scope:'.working', entries:[...]}"
    entries = inv.get("entries")
    if type(entries) is not list:
        return "inventory entries must be a list"
    if len(entries) > MAX_INVENTORY_ENTRIES:
        return "inventory exceeds max entries"
    seen = set()
    aggregate = 0
    for e in entries:
        if type(e) is not dict or "path" not in e or e.get("kind") not in ("file", "directory"):
            return "malformed inventory entry"
        p = e["path"]
        if _bad_path(p) is not None:
            return "malformed inventory path"
        if p in seen:
            return "duplicate inventory path"
        seen.add(p)
        aggregate += len(p.encode("utf-8"))
    if aggregate > MAX_AGGREGATE_PATH_BYTES:
        return "inventory aggregate path bytes exceeded"
    return None


def validate_keep(raw_bytes, *, observed_context):
    """Validate a serialized Keep acceptance. Pure: no I/O, no mutation, no capability. See module docstring."""
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
                           parse_float=_parse_float, parse_int=_parse_int)
    except ValueError as exc:
        return _v("INVALID", "PARSE", "raw_bytes", str(exc))

    if type(model) is not dict:
        return _v("INVALID", "TYPE", "root", "must be a JSON object")
    if set(model.keys()) != {"schema", "operation", "binding", "head", "inventory_digest",
                             "actor", "decisions"}:
        return _v("INVALID", "SCHEMA", "root", "exact root keys required")

    canon_bytes = canonical_json_bytes(model)
    if len(canon_bytes) > MAX_CANONICAL_BYTES:
        return _v("INVALID", "LIMIT", "canonical_bytes", "exceeds canonical bytes limit")

    if model["schema"] is True or model["schema"] is False or model["schema"] != KEEP_SCHEMA:
        return _v("INVALID", "SCHEMA", "schema", "must equal KEEP_SCHEMA")
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
    if type(dig) is not str or not re.fullmatch(r"sha256:[0-9a-f]{64}", dig):
        return _v("INVALID", "SCHEMA", "inventory_digest", "must be sha256:<64 hex>")

    head_reason = _validate_head(model["head"])
    if head_reason is not None:
        return _v("INVALID", "SCHEMA", "head", head_reason)

    # Observation context: a parser result is never a capability; equality with the supplied, already
    # validated observation is the contract. A missing/malformed observation is CANNOT-EVALUATE.
    for key in ("binding", "head", "inventory"):
        if key not in observed_context:
            return _v("CANNOT-EVALUATE", "CONTEXT", key, "missing observation")
    inv_reason = _bad_inventory(observed_context["inventory"])
    if inv_reason is not None:
        return _v("CANNOT-EVALUATE", "CONTEXT", "inventory", inv_reason)
    if model["binding"] != observed_context["binding"]:
        return _v("CANNOT-EVALUATE", "CONTEXT", "binding", "binding mismatch")
    if model["head"] != observed_context["head"]:
        return _v("CANNOT-EVALUATE", "CONTEXT", "head", "head mismatch")
    if dig != _digest(canonical_json_bytes(observed_context["inventory"])):
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
        preason = _bad_path(path)
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
            return _v("INVALID", "RESERVED", loc + ".path", "reserved managed path")
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
            if type(bnd["mode"]) is not int or type(bnd["mode"]) is bool \
                    or type(bnd["size"]) is not int or type(bnd["size"]) is bool \
                    or type(bnd["digest"]) is not str:
                return _v("INVALID", "SCHEMA", loc + ".binding", "invalid binding value types")
            if bnd["mode"] < 0 or bnd["mode"] > MAX_MODE or bnd["size"] < 0 or bnd["size"] > MAX_FILE_SIZE:
                return _v("INVALID", "LIMIT", loc + ".binding", "binding value out of bounds")
            t = inv_map[path]
            if bnd["mode"] != t.get("mode") or bnd["size"] != t.get("size") or bnd["digest"] != t.get("digest"):
                return _v("CANNOT-EVALUATE", "CONTEXT", loc + ".binding", "file binding mismatch")
            if path in covered:
                return _v("INVALID", "COVERAGE", loc, "duplicate/overlapping coverage")
            covered.add(path)
        else:  # directory-group
            if inv_map[path]["kind"] != "directory":
                return _v("INVALID", "TYPE", loc, "inventory entry is not a directory")
            if set(bnd.keys()) != {"mode", "members_count", "members_digest"}:
                return _v("INVALID", "SCHEMA", loc + ".binding", "exact group binding keys required")
            if type(bnd["mode"]) is not int or type(bnd["mode"]) is bool \
                    or type(bnd["members_count"]) is not int or type(bnd["members_count"]) is bool \
                    or type(bnd["members_digest"]) is not str:
                return _v("INVALID", "SCHEMA", loc + ".binding", "invalid binding value types")
            if bnd["mode"] < 0 or bnd["mode"] > MAX_MODE or bnd["members_count"] < 0:
                return _v("INVALID", "LIMIT", loc + ".binding", "binding value out of bounds")
            members = sorted((e for e in entries if e["path"] == path or e["path"].startswith(path + "/")),
                             key=lambda e: e["path"].encode("utf-8"))
            if bnd["mode"] != inv_map[path].get("mode"):
                return _v("CANNOT-EVALUATE", "CONTEXT", loc + ".binding.mode", "mode mismatch")
            if bnd["members_count"] != len(members):
                return _v("CANNOT-EVALUATE", "CONTEXT", loc + ".binding.members_count", "count mismatch")
            if bnd["members_digest"] != _digest(canonical_json_bytes(members)):
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
# matrix (K-N*). No filesystem, subprocess, clock, or randomness is used.

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
        "schema": KEEP_SCHEMA, "operation": KEEP_OPERATION,
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
    checks = []

    def expect(label, raw, ctx, status):
        got = validate_keep(raw, observed_context=ctx).status
        checks.append((label, got == status, "{} != {}".format(got, status)))

    # K-P01: valid, empty decisions, empty inventory.
    ctx0 = _mk_ctx([])
    expect("K-P01-empty-valid", canonical_json_bytes(_mk_model(ctx0, [])), ctx0, "VALID")

    # K-P02: valid, one foreign file kept.
    ctx1 = _mk_ctx([_file_entry(".working/notes.txt")])
    expect("K-P02-file-keep-valid", canonical_json_bytes(_mk_model(ctx1, [_file_dec(".working/notes.txt")])),
           ctx1, "VALID")

    # K-P03: valid, directory-group covering a dir and its child file.
    entries3 = [{"path": ".working/d", "kind": "directory", "mode": 0o755},
                _file_entry(".working/d/x.txt")]
    ctx3 = _mk_ctx(entries3)
    members = sorted(entries3, key=lambda e: e["path"].encode("utf-8"))
    grp = {"path": ".working/d", "kind": "directory-group", "action": "keep", "reason": "legacy tree",
           "binding": {"mode": 0o755, "members_count": len(members),
                       "members_digest": _digest(canonical_json_bytes(members))}}
    expect("K-P03-group-valid", canonical_json_bytes(_mk_model(ctx3, [grp])), ctx3, "VALID")

    # Negative / fail-closed matrix.
    expect("K-N01-dup-key",
           b'{"schema":1,"schema":1,"operation":"opf-init-keep","binding":{},"head":{},'
           b'"inventory_digest":"sha256:' + b"0" * 64 + b'","actor":{"kind":"maintainer","id":"u"},'
           b'"decisions":[]}', ctx0, "INVALID")
    expect("K-N02-not-object", b'[]', ctx0, "INVALID")
    expect("K-N03-extra-root-key",
           canonical_json_bytes(_mk_model(ctx0, [], extra=1)), ctx0, "INVALID")
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
    # Path/coverage negatives (need a matching inventory so we reach the decision loop).
    ctxf = _mk_ctx([_file_entry(".working/f.txt")])
    expect("K-N12-backslash",
           canonical_json_bytes(_mk_model(ctxf, [_file_dec(".working\\f.txt")])), ctxf, "INVALID")
    expect("K-N13-absolute",
           canonical_json_bytes(_mk_model(ctxf, [_file_dec("/etc/passwd")])), ctxf, "INVALID")
    expect("K-N14-dotdot",
           canonical_json_bytes(_mk_model(ctxf, [_file_dec(".working/../x")])), ctxf, "INVALID")
    expect("K-N15-not-below-working",
           canonical_json_bytes(_mk_model(ctxf, [_file_dec("outside.txt")])), ctxf, "INVALID")
    ctxr = _mk_ctx([_file_entry(".working/TODO.md")])
    expect("K-N16-reserved-view",
           canonical_json_bytes(_mk_model(ctxr, [_file_dec(".working/TODO.md")])), ctxr, "INVALID")
    ctxm = _mk_ctx([_file_entry(".working/toml/x.toml")])
    expect("K-N17-reserved-machine",
           canonical_json_bytes(_mk_model(ctxm, [_file_dec(".working/toml/x.toml")])), ctxm, "INVALID")
    expect("K-N18-ghost",
           canonical_json_bytes(_mk_model(ctxf, [_file_dec(".working/absent.txt")])), ctxf, "INVALID")
    expect("K-N19-order",
           canonical_json_bytes(_mk_model(_mk_ctx([_file_entry(".working/a"), _file_entry(".working/b")]),
                                          [_file_dec(".working/b"), _file_dec(".working/a")])),
           _mk_ctx([_file_entry(".working/a"), _file_entry(".working/b")]), "INVALID")
    expect("K-N20-uncovered-file",
           canonical_json_bytes(_mk_model(ctxf, [])), ctxf, "INVALID")
    # CANNOT-EVALUATE cases.
    expect("K-N21-binding-mismatch",
           canonical_json_bytes(_mk_model(ctx0, [], binding={"x": 1})), ctx0, "CANNOT-EVALUATE")
    expect("K-N22-inv-digest-mismatch",
           canonical_json_bytes(_mk_model(ctx0, [], inventory_digest="sha256:" + "1" * 64)), ctx0,
           "CANNOT-EVALUATE")
    expect("K-N23-file-binding-mismatch",
           canonical_json_bytes(_mk_model(ctxf, [_file_dec(".working/f.txt", digest="sha256:" + "9" * 64)])),
           ctxf, "CANNOT-EVALUATE")
    # Malformed observation -> CANNOT-EVALUATE (fail-closed, not a silent pass).
    bad_ctx = {"binding": _mk_binding(), "head": ctx0["head"],
               "inventory": {"schema": 1, "scope": ".working",
                             "entries": [{"path": ".working/a", "kind": "file", "mode": 0, "size": 0,
                                          "digest": "sha256:" + "0" * 64},
                                         {"path": ".working/a", "kind": "file", "mode": 0, "size": 0,
                                          "digest": "sha256:" + "0" * 64}]}}
    m_badctx = _mk_model(ctx0, [])
    m_badctx["inventory_digest"] = _digest(canonical_json_bytes(bad_ctx["inventory"]))
    m_badctx["binding"] = bad_ctx["binding"]
    expect("K-N24-dup-inventory-path", canonical_json_bytes(m_badctx), bad_ctx, "CANNOT-EVALUATE")

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
