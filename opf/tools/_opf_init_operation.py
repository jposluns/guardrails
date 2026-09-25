#!/usr/bin/env python3
"""OPF coupled-init operation layer, slice PR3a (library milestone SOURCES-READY).

SENSITIVE-TIER, correctness-critical, stdlib-only, fail-closed, Linux/macOS. This module is the
init operation layer's first source mutation: it observes the explicit binding, plans the immutable
operation, persists that plan before any worktree write, creates the store's directories and source
files through a create-only, preserving journal, resumes an interrupted operation forward under its
ORIGINAL operation id, deduplicates exact poststates, emits the managed bootstrap provenance
(`.working/toml/init.toml`), and consumes a PINNED ancestral counters seed (B6). It is a LIBRARY
milestone: it adds no CLI verb, stages nothing in the git index (PR5), renders no view (PR3b), and
never reports coupled-init success (PR7). The opf init CLI retains the shipped base exit-0 store
scaffolding; wiring it to init_operation and exposing coupled-init CLI milestones are deferred to PR7.

The Architect's rulings it implements (PD-D2B-PR3-SCHEMA, decided 2026-09-24), by site:

  1. Bootstrap capability. The shared mutex is taken first (_opf_oplock.acquire_init_operation, a
     pre-store InitHolder: no control record, no lease); the plan and the directory journal are
     persisted under it; the machine directory is created through the journal; only then is the
     mandatory lease attached (attach_init_lease), minting an ordinary OpCapability. See
     run_init_sources.
  2. Resume identity. A retry resumes the original operation through the substrate's
     resume_operation (an operation-bound handle over the existing record tree); the capability the
     lease attach mints carries that operation's id from birth. No op id is mutated or fabricated.
  3. Torn files. Every planned file is staged COMPLETE under its plan-recorded staging name,
     fsynced, and read back, then published by link(2), which never replaces an existing name. A
     destination holding anything but the exact planned bytes, mode, type, and single link, a
     strict prefix included, is REFUSED and preserved, never repaired. A crash during plan or phase
     publication is handled by the substrate's exact staging sweep and empty-operation discard.
  4. Evidence layout and finalization. Effect journals and attempt outcomes live in the substrate's
     sibling homes (opf-init/journals/, opf-init/outcomes/<op_id>/); the ops/<op_id>/ record shape is
     unchanged. Durable completion is the `sources-ready` milestone, recorded only after the lease
     is detached (the last store write) and the final check has passed under the still-held mutex;
     the mutex is then released. A crash before `sources-ready` resumes; a release failure is a
     finalization failure reported beside, never in place of, the primary outcome.
  5. Plan and provenance. The provenance source_digest is sha256 over the canonical JSON of the
     sorted (path, content-digest) roster EXCLUDING init.toml; init.toml is TOML through
     _opf_emit.emit_checked; the plan_digest is sha256 over the canonical JSON of the plan WITHOUT
     its plan_digest member. The base spec_version bump, init.toml's C-CONTAINMENT membership, and the
     `opf upgrade` route live in _opf_store, _opf_check, and opf.py. A completed adoption is
     re-validated for HEALTH (it may carry legitimate later edits); a partial operation is held to
     BYTE-EXACT dedupe against its immutable plan.
  6. Newest ancestral counters. The reader consumes ONE pinned evidence commit, which must lie on
     the pinned HEAD's FIRST-PARENT line; a namespace the snapshot lacks is UNKNOWN (never zero) and
     refuses the plan, as does a nonzero module-namespace high-water the new store cannot carry; no
     maximum over history is taken. Selecting the commit is PR4's authority.
  7. Activation. Library milestones only; no status here is a CLI exit 0. A completed-adoption
     rerun takes and releases the lock and changes no adoption file and no index entry. Git
     staging stays deferred to PR5.

Exit mapping for a later CLI (decision 7, recorded here so PR7 cannot drift): SOURCES-READY and
ALREADY-INITIALIZED are library milestones and never map to coupled-init exit 0; exit 1 is reserved
for a fully evaluated, NON-mutating assessment that reports findings; every REFUSED, FAILED, or
CANNOT-EVALUATE result maps to exit 2.

DISCLOSED RESIDUALS: the git ignore-eligibility preflight D2a runs is not repeated here (it is a
staging precondition, PR5); the adoption observer that distinguishes a first adoption from a
committed deletion is PR4, so this layer refuses an existing store it did not itself record; a
same-uid writer racing the held mutex can still change a destination between the final check and a
later reader (the lock is advisory; OS isolation is SYSTEM-HARDENING's); a byte-identical file a
foreign writer plants after the intent is recorded is accepted as a dedupe (its content is exactly
the plan's); the durability claim is fsync-based and verified only against process death, not power
loss; everything the composed modules disclose applies unchanged.

Run: python3 -I -B opf/tools/_opf_init_operation.py --self-test
Exit: 0 self-test clean; 1 self-test failure; 2 refused precondition (no git binary or containment
primitive), never a clean skip.
"""
import base64
import binascii
import datetime
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _containment        # noqa: E402
import _journal            # noqa: E402
import _opf_check          # noqa: E402
import _opf_emit           # noqa: E402
import _opf_init           # noqa: E402
import _opf_init_contract  # noqa: E402
import _opf_init_substrate  # noqa: E402
import _opf_observe        # noqa: E402
import _opf_oplock         # noqa: E402
import _opf_store          # noqa: E402
from _opf_schema import SUPPORTED_SCHEMA, validate_counters, high_water  # noqa: E402

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    raise SystemExit("error: opf/tools/_opf_init_operation.py requires Python 3.11+ (tomllib)")

# The frozen managed provenance artifact (OPF-INIT-D2B.md "Bootstrap Provenance").
PROVENANCE_FORMAT = "opf.init.bootstrap/v1"
PROVENANCE_TOP_KEYS = frozenset((
    "schema", "format", "spec_version", "operation_id", "binding", "head", "first_adoption",
    "inventory_digest", "acceptance", "source_set", "source_digest"))
_SCHEMA = 1

# The store-relative machine home and the provenance file's own path (EXCLUDED from its own source
# digest; OPF-INIT-D2B.md). Derived from the resolver constants so this module cannot drift from the
# store layout the validator enforces.
_MACHINE_HOME = "{}/{}".format(_opf_store.WORKING_DIRNAME, _opf_store.DEFAULT_MACHINE_SUBDIR)
PROVENANCE_NAME = _opf_check.INIT_PROVENANCE_NAME
PROVENANCE_RELPATH = "{}/{}".format(_MACHINE_HOME, PROVENANCE_NAME)
CHANGELOG_RELPATH = "CHANGELOG.md"
_CHANGELOG_PAYLOAD = b"# Changelog\n"
LEASE_RELPATH = "{}/{}".format(_MACHINE_HOME, _opf_check.LEASE_NAME)

# The enumerated bootstrap SOURCE roster (store-relative), EXCLUDING init.toml. Derived from the D1
# builders (_opf_init) and the resolver so the roster cannot drift: the pointer, the four machine
# ledgers, and one index per non-worklog baseline type (a worklog is a ledger with no index). CHANGELOG.md
# is a source only when the approved plan creates it (an existing changelog is preserved-existing), so it
# is NOT a member of the fixed roster; a caller that creates it adds it to the payload set explicitly.
_MACHINE_LEDGERS = ("manifest.toml", "counters.toml", "version.toml", "worklog.toml")
_INDEX_TYPES = tuple(sorted(set(_opf_store.BASELINE_TYPES) - {"worklog"}))
BOOTSTRAP_SOURCE_ROSTER = tuple(sorted(
    (_opf_store.POINTER_REL,)
    + tuple("{}/{}".format(_MACHINE_HOME, name) for name in _MACHINE_LEDGERS)
    + tuple("{}/{}.index.toml".format(_MACHINE_HOME, t) for t in _INDEX_TYPES)))
COUNTERS_RELPATH = "{}/{}".format(_MACHINE_HOME, _opf_check.COUNTERS_NAME)

_OP_ID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")
_SHA1_RE = re.compile(r"[0-9a-f]{40}\Z")
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_UTC_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z\Z")
_UTC_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
_MAX_HIGH_WATER = (1 << 63) - 1   # the TOML integer range a counters ledger can carry

# Validation result statuses (a well-formed-and-matching provenance vs anything else; there is no
# cannot-evaluate external-context state at this layer, unlike the Keep validator).
VALID = "VALID"
INVALID = "INVALID"

# --- the PR3a plan contract (decision 5; the frozen opf.init.plan/v1 top-level shape is kept) --------
OPERATION = _opf_init_substrate.PLAN_OPERATION
INIT_GENERATOR = "opf.init.d2b-pr3a/1"
SOURCE_MODE = 0o644
DIR_MODE = 0o755
PERMITTED_DIRECTORIES = (_opf_store.WORKING_DIRNAME, _MACHINE_HOME)
PLAN_SET_KEYS = frozenset(("S", "V", "K", "E", "C"))
_S_KEYS = frozenset(("path", "mode", "size", "digest", "payload", "staging"))
_E_KEYS = frozenset(("path", "mode", "size", "digest"))
REQUIRED_CHECKS = ("source-poststate", "working-inventory", "provenance", "manifest-resolves",
                   "counters")
PUBLICATION_BOUNDARIES = {"milestone": "SOURCES-READY", "views": "deferred-pr3b",
                          "index": "deferred-pr5", "commit": "never"}
RECOVERY_POLICY = "resume-forward-preserve"
ACCEPTANCE_NONE = {"present": False}
MAX_SOURCE_BYTES = 1 << 20
_STAGE_MARKER = ".opf-init-stage-"

# The closed, ordered milestone vocabulary (plan step 4). Each milestone is recorded AT MOST ONCE and
# only in this order, so a retry never re-appends a milestone and the 128-record phase bound is never
# consumed by retries (attempt diagnostics go to the separately bounded outcomes home).
PHASES = ("plan-recorded", "dirs-intent", "dirs-verified", "sources-intent", "sources-verified",
          "sources-ready")
MILESTONE = "SOURCES-READY"

# Result statuses.
SOURCES_READY = "SOURCES-READY"
ALREADY_INITIALIZED = "ALREADY-INITIALIZED"
REFUSED = "REFUSED"
FAILED = "FAILED"
CANNOT_EVALUATE = "CANNOT-EVALUATE"


class InitOperationError(Exception):
    """A fail-closed init-operation error. `code` is REFUSED (a precondition or a conflict, nothing
    mutated by this attempt beyond what is reported), FAILED (a mutation step failed; the evidence is
    preserved), or CANNOT-EVALUATE (an input could not be read or answered)."""

    def __init__(self, message, code=REFUSED):
        super().__init__(message)
        self.code = code


class ProvenanceValidation:
    """status in {VALID, INVALID}; findings an ordered tuple of (code, location, detail); model set
    only on VALID. Never raised for untrusted `raw` input."""
    __slots__ = ("status", "findings", "model")

    def __init__(self, status, findings, model):
        self.status = status
        self.findings = findings
        self.model = model


class AncestralSeed:
    """A validated, pinned ancestral counters seed (B6): `counters` is the {namespace: high-water}
    map of the store roster namespaces the snapshot carries; `unknown` the sorted roster namespaces
    it LACKS (unknown, never zero); `module_counters` the module-tier namespaces it carries (which a
    new store with its modules disabled cannot hold); and `evidence` pins the commit oid, the blob
    oid, the store-relative counters path, and the sha256 content digest of the exact bytes."""
    __slots__ = ("counters", "unknown", "module_counters", "evidence")

    def __init__(self, counters, unknown, module_counters, evidence):
        self.counters = counters
        self.unknown = unknown
        self.module_counters = module_counters
        self.evidence = evidence


class InitResult:
    """The structured outcome of one run_init_sources call (never an exit code; see the module
    docstring for the decision-7 mapping). `status` is one of SOURCES-READY, ALREADY-INITIALIZED,
    REFUSED, FAILED, CANNOT-EVALUATE. `created` and `deduplicated` name the planned paths this
    attempt created or found byte-identical to the plan on resume; `conflicts` the preserved
    destinations that refused; `primary_failure` the first failure (code, detail) or None;
    `finalization_failures` every later release, outcome, or close failure, kept separately."""
    __slots__ = ("status", "operation_id", "plan_digest", "milestone", "created", "deduplicated",
                 "conflicts", "notes", "primary_failure", "finalization_failures", "phases")

    def __init__(self):
        self.status = CANNOT_EVALUATE
        self.operation_id = None
        self.plan_digest = None
        self.milestone = None
        self.created = []
        self.deduplicated = []
        self.conflicts = []
        self.notes = []
        self.primary_failure = None
        self.finalization_failures = []
        self.phases = ()


# --- the source-digest basis (init.toml excluded) -------------------------------------------------


def _oid_ok(oid, object_format):
    if type(oid) is not str:
        return False
    return bool((_SHA1_RE if object_format == "sha1" else _SHA256_RE).match(oid))


def _bad_source_path(path):
    """Reason if `path` is not an acceptable store-relative bootstrap source path (canonical relative,
    below-root, and NOT the provenance file itself), else None. init.toml is excluded from its own
    source digest, so it is never a member of the source set."""
    reason = _opf_init_contract._bad_relpath(path)
    if reason is not None:
        return reason
    if path == PROVENANCE_RELPATH:
        return "the provenance artifact {!r} is excluded from its own source digest".format(
            PROVENANCE_RELPATH)
    return None


def compute_bootstrap_source_digest(source_payloads):
    """Return (source_set, source_digest) over the enumerated bootstrap source set EXCLUDING init.toml.

    `source_payloads` is a mapping {store-relative-path: bytes}. Every path is a canonical below-root
    relative path and NONE may be the provenance artifact itself (`.working/toml/init.toml`): init.toml
    is excluded from its own source digest, so passing it is a refusal, not a silent inclusion. The
    digest basis is a sha256 over the canonical JSON of the sorted (path, content-digest) roster, so it
    binds each source's exact identity and content; `source_set` is that sorted path roster. Fail-closed:
    a non-mapping, a non-bytes payload, a malformed path, or the provenance file present each raise
    InitOperationError (guard-input-soundness; the digest is only as sound as its enumerated input)."""
    if type(source_payloads) is not dict:
        raise InitOperationError("source_payloads must be a mapping of store-relative path -> bytes")
    pairs = []
    for path in sorted(source_payloads):
        reason = _bad_source_path(path)
        if reason is not None:
            raise InitOperationError("source path {!r}: {}".format(path, reason))
        payload = source_payloads[path]
        if type(payload) is not bytes:
            raise InitOperationError("source payload for {!r} must be bytes".format(path))
        pairs.append([path, _opf_init_contract._digest(payload)])
    source_set = [p for p, _dig in pairs]
    basis = _opf_init_contract.canonical_json_bytes(pairs)
    return source_set, _opf_init_contract._digest(basis)


# --- the managed bootstrap provenance (opf.init.bootstrap/v1) --------------------------------------


def _bad_plan_basis(basis):
    """Reason if `basis` is not a well-formed provenance plan basis, else None. Structural (never merely
    equal to a trusted context): the scalar identity fields, the Binding, and the HEAD union are each
    validated through the D2b contract's own validators."""
    if type(basis) is not dict or set(basis.keys()) != {
        "spec_version", "operation_id", "binding", "head", "first_adoption",
        "inventory_digest", "acceptance",
    }:
        return "plan basis keys"
    if type(basis["spec_version"]) is not str \
            or _opf_init_contract._bad_string(basis["spec_version"],
                                              _opf_init_contract.MAX_STRING_BYTES) is not None:
        return "spec_version must be a bounded string"
    if type(basis["operation_id"]) is not str or not _OP_ID_RE.match(basis["operation_id"]):
        return "operation_id is not a well-formed operation id"
    reason = _opf_init_contract._bad_binding(basis["binding"])
    if reason is not None:
        return "binding: " + reason
    reason = _opf_init_contract._bad_head(basis["head"], basis["binding"]["object_format"])
    if reason is not None:
        return "head: " + reason
    if type(basis["first_adoption"]) is not bool:
        return "first_adoption must be a boolean"
    dig = basis["inventory_digest"]
    if type(dig) is not str or not _opf_init_contract._DIGEST_RE.match(dig):
        return "inventory_digest must be sha256:<64 hex>"
    if type(basis["acceptance"]) is not dict:
        return "acceptance must be a table"
    return None


def build_bootstrap_provenance(plan_basis, source_payloads):
    """Build the frozen `opf.init.bootstrap/v1` managed provenance TOML (`.working/toml/init.toml`).

    `plan_basis` carries the already-observed scalar identity fields, Binding, and HEAD; `source_payloads`
    is the {store-relative-path: bytes} bootstrap source set (init.toml excluded). The `source_digest` is
    computed over that source set EXCLUDING init.toml (compute_bootstrap_source_digest); the document is
    emitted through the staging contract (_opf_emit.emit_checked, which reparses and round-trip-proves the
    TOML) and then re-validated through validate_bootstrap_provenance, so nothing that does not reparse or
    does not match its own recomputed digest is ever returned. Fail-closed: a malformed basis or source
    set raises InitOperationError (never a silent degraded artifact)."""
    reason = _bad_plan_basis(plan_basis)
    if reason is not None:
        raise InitOperationError("provenance plan basis invalid: {}".format(reason))
    source_set, source_digest = compute_bootstrap_source_digest(source_payloads)
    document = {
        "schema": _SCHEMA,
        "format": PROVENANCE_FORMAT,
        "spec_version": plan_basis["spec_version"],
        "operation_id": plan_basis["operation_id"],
        "binding": plan_basis["binding"],
        "head": plan_basis["head"],
        "first_adoption": plan_basis["first_adoption"],
        "inventory_digest": plan_basis["inventory_digest"],
        "acceptance": plan_basis["acceptance"],
        "source_set": source_set,
        "source_digest": source_digest,
    }
    try:
        text = _opf_emit.emit_checked(document)
    except _opf_emit.EmitError as exc:
        raise InitOperationError("provenance did not round-trip through the staging contract "
                                 "({}); fail-closed".format(exc))
    check = validate_bootstrap_provenance(text.encode("utf-8"),
                                          expected_basis=plan_basis,
                                          expected_source_digest=source_digest,
                                          expected_source_set=source_set)
    if check.status != VALID:
        raise InitOperationError("built provenance failed its own re-validation: {}".format(
            check.findings))
    return text


def validate_bootstrap_provenance(raw, *, expected_basis=None, expected_source_digest=None,
                                  expected_source_set=None):
    """Validate serialized `opf.init.bootstrap/v1` provenance bytes. Pure and NEVER raises for the
    untrusted `raw`: every malformed input resolves to an INVALID ProvenanceValidation.

    Structural validation covers the exact top-level key set, schema/format markers, the scalar identity
    fields, the Binding and HEAD unions (through the D2b contract validators), the acceptance table, and
    the source_set / source_digest grammar (the source set is a sorted, unique roster of below-root
    relative paths that NEVER contains the provenance file itself). When the operation layer re-supplies
    the live re-observation (`expected_basis`, `expected_source_digest`, `expected_source_set`), a
    provenance whose bound identity, source set, or source digest does not match is INVALID: this is how
    a FORGED or STALE provenance (right shape, wrong bound state) is rejected rather than trusted."""
    if type(raw) is not bytes:
        return _pv(INVALID, "TYPE", "raw", "bytes required")
    if len(raw) > _opf_init_contract.MAX_RAW_BYTES:
        return _pv(INVALID, "LIMIT", "raw", "exceeds raw bytes limit")
    try:
        model = tomllib.loads(raw.decode("utf-8", errors="strict"))
    except UnicodeDecodeError:
        return _pv(INVALID, "ENCODING", "raw", "invalid UTF-8")
    except (tomllib.TOMLDecodeError, ValueError, RecursionError) as exc:
        return _pv(INVALID, "PARSE", "raw", "not TOML ({})".format(exc))
    if type(model) is not dict:
        return _pv(INVALID, "TYPE", "root", "must be a table")
    if set(model.keys()) != set(PROVENANCE_TOP_KEYS):
        return _pv(INVALID, "SCHEMA", "root", "exact top-level keys required")
    if type(model["schema"]) is not int or model["schema"] != _SCHEMA:
        return _pv(INVALID, "SCHEMA", "schema", "must be integer {}".format(_SCHEMA))
    if model["format"] != PROVENANCE_FORMAT:
        return _pv(INVALID, "SCHEMA", "format", "must equal {!r}".format(PROVENANCE_FORMAT))
    if type(model["spec_version"]) is not str \
            or _opf_init_contract._bad_string(model["spec_version"],
                                              _opf_init_contract.MAX_STRING_BYTES) is not None:
        return _pv(INVALID, "SCHEMA", "spec_version", "must be a bounded string")
    if type(model["operation_id"]) is not str or not _OP_ID_RE.match(model["operation_id"]):
        return _pv(INVALID, "SCHEMA", "operation_id", "not a well-formed operation id")
    reason = _opf_init_contract._bad_binding(model["binding"])
    if reason is not None:
        return _pv(INVALID, "SCHEMA", "binding", reason)
    reason = _opf_init_contract._bad_head(model["head"], model["binding"]["object_format"])
    if reason is not None:
        return _pv(INVALID, "SCHEMA", "head", reason)
    if type(model["first_adoption"]) is not bool:
        return _pv(INVALID, "SCHEMA", "first_adoption", "must be a boolean")
    if type(model["inventory_digest"]) is not str \
            or not _opf_init_contract._DIGEST_RE.match(model["inventory_digest"]):
        return _pv(INVALID, "SCHEMA", "inventory_digest", "must be sha256:<64 hex>")
    if type(model["acceptance"]) is not dict:
        return _pv(INVALID, "SCHEMA", "acceptance", "must be a table")
    src = model["source_set"]
    if type(src) is not list:
        return _pv(INVALID, "SCHEMA", "source_set", "must be a list")
    prev = None
    for i, path in enumerate(src):
        loc = "source_set[{}]".format(i)
        reason = _bad_source_path(path) if type(path) is str else "exact string required"
        if reason is not None:
            return _pv(INVALID, "SCHEMA", loc, reason)
        if prev is not None and path <= prev:
            return _pv(INVALID, "ORDER", "source_set", "not strictly increasing (sorted, unique)")
        prev = path
    if type(model["source_digest"]) is not str \
            or not _opf_init_contract._DIGEST_RE.match(model["source_digest"]):
        return _pv(INVALID, "SCHEMA", "source_digest", "must be sha256:<64 hex>")

    # Forged / stale rejection: when the live re-observation is supplied, the provenance's bound state
    # must match it exactly. A right-shaped provenance with the wrong bound identity, source set, or
    # source digest is INVALID, never trusted.
    if expected_basis is not None:
        if _bad_plan_basis(expected_basis) is not None:
            return _pv(INVALID, "CONTEXT", "expected_basis", "malformed expected basis")
        for key in ("spec_version", "operation_id", "binding", "head", "first_adoption",
                    "inventory_digest", "acceptance"):
            if model[key] != expected_basis[key]:
                return _pv(INVALID, "STALE", key, "does not match the observed basis")
    if expected_source_set is not None and src != list(expected_source_set):
        return _pv(INVALID, "STALE", "source_set", "does not match the observed source set")
    if expected_source_digest is not None and model["source_digest"] != expected_source_digest:
        return _pv(INVALID, "STALE", "source_digest", "does not match the recomputed source digest")
    return ProvenanceValidation(VALID, (), model)


def _pv(status, code, location, detail):
    return ProvenanceValidation(status, ((code, location, detail),), None)


# --- B6: read a PINNED ancestral counters seed through the hardened git surface --------------------


def _roster_namespaces():
    """(baseline, importer, module) namespace sets, derived from the store vocabulary."""
    baseline = frozenset(_opf_store.BASELINE_TYPES.values())
    importer = frozenset(_opf_store.IMPORTER_TYPES.values())
    module = frozenset(ns for ns, _mod in _opf_store.MODULE_TYPES.values())
    return baseline, importer, module


def read_ancestral_counter_seed(store_root, *, pinned_head, evidence_commit, prefix, object_format,
                                git=None, max_first_parent=1000000):
    """Read and validate the PINNED ancestral counters seed (B6). Returns an AncestralSeed or raises
    InitOperationError.

    Reads `<evidence_commit>:<prefix>/.working/toml/counters.toml` through the hardened, allowlist-
    scrubbed, no-replace-objects, no-lazy-fetch `_opf_observe._run_git` surface, then:
      - resolves the evidence commit and the pinned head to full object ids (rev-parse --verify);
      - verifies the evidence lies on the pinned head's FIRST-PARENT line (decision 6: the newest
        qualifying copy is chosen on the first-parent main line, so a commit reachable only through a
        merge's second parent is refused), enumerated by rev-list --first-parent, bounded; a shallow
        or truncated history that does not reach it cannot prove the ancestry and refuses;
      - resolves the counters blob's object id at that path and reads exactly that blob;
      - validates the TOML, schema, and namespace/value constraints (validate_counters), accepting
        exactly the store roster and module-tier namespaces;
      - returns the roster high-waters it carries, the roster namespaces it LACKS as `unknown` (never
        a zero), the module-tier high-waters as `module_counters`, and commit / blob / path /
        content-digest evidence.

    REFUSES an unavailable, malformed, off-line, or unprovable seed and NEVER falls back to an older
    readable candidate, to a maximum over history, or to silent zeros. SELECTION of the newest
    qualifying ancestor is PR4's authority: this consumes the single pinned commit it is given."""
    if object_format not in ("sha1", "sha256"):
        raise InitOperationError("object_format must be 'sha1' or 'sha256'")
    if not _oid_ok(pinned_head, object_format):
        raise InitOperationError("pinned_head is not a well-formed {} object id".format(object_format))
    if not _oid_ok(evidence_commit, object_format):
        raise InitOperationError("evidence_commit is not a well-formed {} object id".format(
            object_format))
    if type(prefix) is not str:
        raise InitOperationError("prefix must be a string ('' for the repository root)")
    if prefix != "" and _opf_init_contract._bad_relpath(prefix) is not None:
        raise InitOperationError("prefix is not a canonical below-root relative path")
    if type(max_first_parent) is not int or max_first_parent < 1:
        raise InitOperationError("max_first_parent must be a positive integer")
    git = git or _opf_observe._git_path()
    if git is None:
        raise InitOperationError("git binary not found; the ancestral counters seed cannot be read",
                                 CANNOT_EVALUATE)

    def _rev_parse(spec):
        outcome = _opf_observe._run_git(
            git, store_root, ["rev-parse", "--verify", "--end-of-options", spec])
        if not outcome.completed:
            raise InitOperationError("git could not run to resolve {!r} ({})".format(
                spec, outcome.err.strip()), CANNOT_EVALUATE)
        if outcome.rc != 0:
            raise InitOperationError("ancestral evidence {!r} does not resolve ({}); refusing rather "
                                     "than falling back".format(spec, outcome.err.strip()))
        return outcome.out.decode("utf-8", "strict").strip()

    evidence_oid = _rev_parse("{}^{{commit}}".format(evidence_commit))
    head_oid = _rev_parse("{}^{{commit}}".format(pinned_head))
    if not _oid_ok(evidence_oid, object_format) or not _oid_ok(head_oid, object_format):
        raise InitOperationError("git returned a malformed resolved object id; fail-closed")

    walk = _opf_observe._run_git(
        git, store_root, ["rev-list", "--first-parent", "--max-count={}".format(max_first_parent),
                          head_oid])
    if not walk.completed or walk.rc != 0:
        raise InitOperationError("git could not enumerate the first-parent line of {} ({}); "
                                 "fail-closed".format(head_oid, walk.err.strip()), CANNOT_EVALUATE)
    line = walk.out.decode("ascii", "strict").split("\n")
    if line[-1] != "":
        raise InitOperationError("git returned an unterminated first-parent listing; fail-closed",
                                 CANNOT_EVALUATE)
    if evidence_oid not in line[:-1]:
        raise InitOperationError("evidence commit {} is not on the first-parent line of the pinned "
                                 "head {} (within {} commits); a second-parent, sibling, or "
                                 "beyond-shallow-history commit cannot support the permanence "
                                 "claim".format(evidence_oid, head_oid, max_first_parent))

    counters_path = "{}/{}/counters.toml".format(
        "{}/{}".format(prefix, _opf_store.WORKING_DIRNAME) if prefix
        else _opf_store.WORKING_DIRNAME, _opf_store.DEFAULT_MACHINE_SUBDIR)
    blob_spec = "{}:{}".format(evidence_oid, counters_path)
    blob_oid = _rev_parse(blob_spec)
    if not _oid_ok(blob_oid, object_format):
        raise InitOperationError("git returned a malformed counters blob object id; fail-closed")

    read = _opf_observe._run_git(git, store_root, ["cat-file", "blob", blob_oid])
    if not read.completed:
        raise InitOperationError("git could not read the counters blob ({}); fail-closed".format(
            read.err.strip()), CANNOT_EVALUATE)
    if read.rc != 0:
        raise InitOperationError("counters blob {} is unreadable ({}); refusing rather than falling "
                                 "back".format(blob_oid, read.err.strip()))
    blob = read.out
    content_digest = "sha256:" + hashlib.sha256(blob).hexdigest()

    try:
        parsed = tomllib.loads(blob.decode("utf-8", errors="strict"))
    except UnicodeDecodeError:
        raise InitOperationError("ancestral counters blob is not decodable UTF-8; refusing")
    except (tomllib.TOMLDecodeError, ValueError, RecursionError) as exc:
        raise InitOperationError("ancestral counters blob is not valid TOML ({}); refusing".format(exc))

    baseline, importer, module = _roster_namespaces()
    # known_namespaces is EMPTY: no namespace is REQUIRED here, because a namespace the snapshot lacks
    # is reported as UNKNOWN below rather than as a malformed seed; every namespace outside the roster
    # and the module tier is still refused, and every value is still type-checked.
    values, findings = validate_counters(parsed, known_namespaces=frozenset(),
                                         optional_namespaces=baseline | importer | module)
    if findings:
        raise InitOperationError("ancestral counters are malformed ({}); refusing rather than falling "
                                 "back to zeros or an older candidate".format(findings))
    oversized = sorted(ns for ns, v in values.items() if v > _MAX_HIGH_WATER)
    if oversized:
        raise InitOperationError("ancestral counters {} exceed the TOML 64-bit integer range; "
                                 "refusing".format(", ".join(oversized)))
    counters = {ns: v for ns, v in values.items() if ns in baseline | importer}
    unknown = tuple(sorted((baseline | importer) - set(counters)))
    module_counters = {ns: v for ns, v in values.items() if ns in module}
    evidence = {
        "commit": evidence_oid,
        "blob": blob_oid,
        "path": counters_path,
        "content_digest": content_digest,
    }
    return AncestralSeed(counters, unknown, module_counters, evidence)


def seed_permanence_refusal(seed):
    """The reason an ancestral seed cannot support the permanence claim (decision 6), or None: a
    roster namespace the snapshot lacks is UNKNOWN and never becomes a zero; a NONZERO module-tier
    high-water would be dropped by a new store whose modules are disabled, so a later re-enable
    could reallocate its identifiers."""
    if seed.unknown:
        return ("the ancestral counters snapshot {} lacks namespace(s) {}; a missing historical "
                "high-water is UNKNOWN, never zero, so the snapshot cannot support the permanence "
                "claim".format(seed.evidence["commit"], ", ".join(seed.unknown)))
    lost = sorted(ns for ns, v in seed.module_counters.items() if v != 0)
    if lost:
        return ("the ancestral counters snapshot {} carries nonzero module-tier high-water(s) {} a "
                "new store with its modules disabled cannot hold; re-adopting would let a later "
                "re-enable reuse those identifiers".format(
                    seed.evidence["commit"],
                    ", ".join("{}={}".format(ns, seed.module_counters[ns]) for ns in lost)))
    return None


# --- observation: the explicit binding, HEAD, and the .working inventory (plan step 1) ---------------


def _abs_root(product_root):
    """The explicit, absolute, NUL-free product root (a control parameter, validated, never coerced,
    never resolved through a symlink or an ambient working directory)."""
    if not isinstance(product_root, (str, os.PathLike)):
        raise InitOperationError("product root must be a path")
    root = os.fspath(product_root)
    if type(root) is not str or not root or "\x00" in root or not os.path.isabs(root):
        raise InitOperationError("product root must be an absolute, NUL-free path, not {!r}".format(
            root))
    return os.path.abspath(root)


def _git_lines(git, root, args, count):
    """Run a read-only git observation and return exactly `count` newline-terminated lines, strict
    UTF-8; anything else is a CANNOT-EVALUATE refusal."""
    out = _opf_observe._run_git(git, root, args)
    if not out.completed or out.rc != 0:
        raise InitOperationError("git {} could not observe {} ({})".format(
            args[0], root, out.err.strip()), CANNOT_EVALUATE)
    try:
        text = out.out.decode("utf-8", "strict")
    except UnicodeDecodeError:
        raise InitOperationError("git {} returned undecodable output".format(args[0]),
                                 CANNOT_EVALUATE)
    lines = text.split("\n")
    if len(lines) != count + 1 or lines[-1] != "":
        raise InitOperationError("git {} returned an unexpected line count".format(args[0]),
                                 CANNOT_EVALUATE)
    return lines[:-1]


def _location(path, what):
    """The contract Location {path, identity} of an absolute directory, opened by the no-follow
    walk (a symlinked component refuses)."""
    try:
        fd = _opf_store._open_dir_nofollow(path)
    except OSError as exc:
        raise InitOperationError("cannot open {} {} no-follow ({})".format(what, path, exc),
                                 CANNOT_EVALUATE)
    try:
        st = os.fstat(fd)
    finally:
        os.close(fd)
    return {"path": path, "identity": {"device": st.st_dev, "inode": st.st_ino}}


def observe_binding(product_root, git=None):
    """Observe the explicit D2b Binding and HEAD of `product_root` through the hardened git surface
    (scrubbed environment, no replacement objects, no lazy fetch, an explicit -C binding). Returns
    (binding, head), each validated by the D2b contract's own validators. Refuses a non-repository,
    a bare repository, a product root that is not exactly <toplevel>/<prefix> (a symlinked or
    wrong root), an undecodable answer, a HEAD whose target cannot be resolved except as a proven
    unborn branch, and anything else it cannot answer (CANNOT-EVALUATE)."""
    root = _abs_root(product_root)
    git = git or _opf_observe._git_path()
    if git is None:
        raise InitOperationError("git binary not found on PATH", CANNOT_EVALUATE)
    inside, bare, toplevel, gitdir, common, prefix, fmt = _git_lines(
        git, root, ["rev-parse", "--is-inside-work-tree", "--is-bare-repository", "--show-toplevel",
                    "--absolute-git-dir", "--git-common-dir", "--show-prefix",
                    "--show-object-format"], 7)
    if inside != "true" or bare != "false":
        raise InitOperationError("{} is not a non-bare git worktree".format(root))
    (index_path,) = _git_lines(git, root, ["rev-parse", "--git-path", "index"], 1)
    if not os.path.isabs(common):
        common = os.path.join(root, common)
    if not os.path.isabs(index_path):
        index_path = os.path.join(root, index_path)
    prefix = prefix[:-1] if prefix.endswith("/") else prefix
    if os.path.abspath(os.path.join(toplevel, prefix)) != root:
        raise InitOperationError("product root {} is not the repository toplevel {} joined with its "
                                 "prefix {!r} (a symlinked or wrong root)".format(root, toplevel,
                                                                                prefix))
    binding = {
        "product_root": _location(root, "product root"),
        "repository_root": _location(os.path.abspath(toplevel), "repository root"),
        "worktree_git_directory": _location(os.path.abspath(gitdir), "worktree git directory"),
        "common_git_directory": _location(os.path.abspath(common), "common git directory"),
        "index_path": os.path.abspath(index_path),
        "product_prefix": prefix,
        "object_format": fmt,
    }
    reason = _opf_init_contract._bad_binding(binding)
    if reason is not None:
        raise InitOperationError("observed binding is malformed: {}".format(reason), CANNOT_EVALUATE)
    head = _observe_head(git, root, fmt)
    reason = _opf_init_contract._bad_head(head, fmt)
    if reason is not None:
        raise InitOperationError("observed HEAD is malformed or unsupported: {}".format(reason))
    return binding, head


def _observe_head(git, root, object_format):
    """The closed HEAD union. A symbolic HEAD whose target resolves is a commit head; one whose
    target is PROVEN absent (show-ref and rev-parse both answer "absent" cleanly) is unborn; a
    detached HEAD must resolve. A failed resolution is never taken as unborn proof."""
    sym = _opf_observe._run_git(git, root, ["symbolic-ref", "-q", "HEAD"])
    if not sym.completed or sym.rc not in (0, 1):
        raise InitOperationError("git could not read HEAD's binding ({})".format(sym.err.strip()),
                                 CANNOT_EVALUATE)
    ref = None
    if sym.rc == 0:
        try:
            text = sym.out.decode("utf-8", "strict")
        except UnicodeDecodeError:
            raise InitOperationError("HEAD's symbolic ref is undecodable", CANNOT_EVALUATE)
        if not text.endswith("\n") or "\n" in text[:-1]:
            raise InitOperationError("HEAD's symbolic ref is malformed", CANNOT_EVALUATE)
        ref = text[:-1]
    res = _opf_observe._run_git(git, root, ["rev-parse", "-q", "--verify", "HEAD^{commit}"])
    if not res.completed or res.rc not in (0, 1):
        raise InitOperationError("git could not resolve HEAD ({})".format(res.err.strip()),
                                 CANNOT_EVALUATE)
    if res.rc == 0:
        oid = res.out.decode("ascii", "strict").strip()
        binding = {"kind": "symbolic", "ref": ref} if ref is not None else {"kind": "detached"}
        return {"kind": "commit", "oid": oid, "binding": binding}
    if ref is None:
        raise InitOperationError("a detached HEAD does not resolve to a commit", CANNOT_EVALUATE)
    show = _opf_observe._run_git(git, root, ["show-ref", "--verify", "-q", ref])
    direct = _opf_observe._run_git(git, root, ["rev-parse", "-q", "--verify", ref])
    if not (show.completed and direct.completed and show.rc == 1 and direct.rc == 1
            and not direct.out and not show.out):
        raise InitOperationError("HEAD's target {} neither resolves nor is proven absent; a failed "
                                 "resolution is never unborn proof".format(ref), CANNOT_EVALUATE)
    return {"kind": "unborn", "symbolic_ref": ref, "target_ref_state": "absent"}


def _read_bounded_fd(fd, cap):
    """Read at most cap + 1 bytes from fd (a read error is CANNOT-EVALUATE)."""
    data = bytearray()
    try:
        while len(data) <= cap:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            data += chunk
    except OSError as exc:
        raise InitOperationError("read error ({})".format(exc), CANNOT_EVALUATE)
    return bytes(data)


def observe_inventory(root_fd):
    """The observed `.working` inventory model {schema: 1, scope: ".working", entries: [...]} beneath
    the opened product root, walked no-follow and bounded by the D2b contract limits: each directory
    with its mode, each regular file with its mode, size, and content digest. A symbolic link, a
    special file, a hard-linked file, an unreadable entry, or an exceeded bound refuses (never an
    entry that silently disappears from the inventory). An absent `.working` is the empty
    inventory."""
    entries = []
    budget = [0]

    def add(entry):
        budget[0] += len(entry["path"].encode("utf-8"))
        if len(entries) >= _opf_init_contract.MAX_INVENTORY_ENTRIES \
                or budget[0] > _opf_init_contract.MAX_AGGREGATE_PATH_BYTES:
            raise InitOperationError("the .working inventory exceeds its entry or path-byte bound",
                                     CANNOT_EVALUATE)
        entries.append(entry)

    def walk(dfd, rel, depth):
        try:
            names = sorted(os.listdir(dfd))
        except OSError as exc:
            raise InitOperationError("cannot list {} ({})".format(rel, exc), CANNOT_EVALUATE)
        for name in names:
            path = "{}/{}".format(rel, name)
            if _opf_init_contract._bad_relpath(path) is not None:
                raise InitOperationError("inventory path {!r} is not canonical".format(path),
                                         CANNOT_EVALUATE)
            try:
                st = os.stat(name, dir_fd=dfd, follow_symlinks=False)
            except OSError as exc:
                raise InitOperationError("cannot stat {} ({})".format(path, exc), CANNOT_EVALUATE)
            if stat.S_ISDIR(st.st_mode):
                add({"path": path, "kind": "directory", "mode": stat.S_IMODE(st.st_mode) & 0o777})
                if depth + 1 >= _opf_init_contract.MAX_PATH_DEPTH:
                    raise InitOperationError("the .working inventory exceeds its depth bound",
                                             CANNOT_EVALUATE)
                try:
                    cfd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                                  dir_fd=dfd)
                except OSError as exc:
                    raise InitOperationError("cannot open {} ({})".format(path, exc),
                                             CANNOT_EVALUATE)
                try:
                    walk(cfd, path, depth + 1)
                finally:
                    os.close(cfd)
            elif stat.S_ISREG(st.st_mode):
                data, fst = _read_regular(dfd, name, path, _opf_init_contract.MAX_RAW_BYTES)
                add({"path": path, "kind": "file", "mode": stat.S_IMODE(fst.st_mode) & 0o777,
                     "size": len(data), "digest": _opf_init_contract._digest(data)})
            else:
                raise InitOperationError("{} is a symbolic link or special file; the inventory "
                                         "refuses it".format(path))

    try:
        wst = os.stat(_opf_store.WORKING_DIRNAME, dir_fd=root_fd, follow_symlinks=False)
    except FileNotFoundError:
        wst = None
    except OSError as exc:
        raise InitOperationError("cannot stat .working ({})".format(exc), CANNOT_EVALUATE)
    if wst is not None:
        if not stat.S_ISDIR(wst.st_mode):
            raise InitOperationError(".working is not a plain directory")
        add({"path": _opf_store.WORKING_DIRNAME, "kind": "directory",
             "mode": stat.S_IMODE(wst.st_mode) & 0o777})
        try:
            wfd = os.open(_opf_store.WORKING_DIRNAME,
                          os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                          dir_fd=root_fd)
        except OSError as exc:
            raise InitOperationError("cannot open .working ({})".format(exc), CANNOT_EVALUATE)
        try:
            walk(wfd, _opf_store.WORKING_DIRNAME, 1)
        finally:
            os.close(wfd)
    # The contract's inventory scope lists entries BENEATH .working; the .working root itself is
    # carried separately so an empty .working directory is never read as absent.
    model = {"schema": 1, "scope": _opf_store.WORKING_DIRNAME,
             "entries": [e for e in entries if e["path"] != _opf_store.WORKING_DIRNAME]}
    reason = _opf_init_contract._bad_inventory(model)
    if reason is not None:
        raise InitOperationError("observed inventory is malformed: {}".format(reason),
                                 CANNOT_EVALUATE)
    return model, wst is not None


def _read_regular(dfd, name, label, cap):
    """Read a regular, singly-linked file beneath dfd, no-follow and non-blocking, bounded by `cap`;
    returns (bytes, fstat). A link count above one, a non-regular object, or an oversize file
    refuses (a hard link could make an adopted path track another inode)."""
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=dfd)
    except OSError as exc:
        raise InitOperationError("cannot open {} ({})".format(label, exc), CANNOT_EVALUATE)
    try:
        try:
            st = os.fstat(fd)
        except OSError as exc:
            raise InitOperationError("cannot fstat {} ({})".format(label, exc), CANNOT_EVALUATE)
        if not stat.S_ISREG(st.st_mode):
            raise InitOperationError("{} is not a regular file".format(label))
        if st.st_nlink != 1:
            raise InitOperationError("{} has {} links, not exactly one".format(label, st.st_nlink))
        if st.st_size > cap:
            raise InitOperationError("{} exceeds the {}-byte bound".format(label, cap))
        data = _read_bounded_fd(fd, cap)
        if len(data) > cap or len(data) != st.st_size:
            raise InitOperationError("{} changed size while it was read".format(label),
                                     CANNOT_EVALUATE)
        return data, st
    finally:
        os.close(fd)


def inventory_digest(model):
    return _opf_init_contract._digest(_opf_init_contract.canonical_json_bytes(model))


def _tracked_destinations(git, root, paths):
    """The planned destinations the git index already tracks (ls-files over literal pathspecs,
    beneath the explicit product root); an unanswerable index read is CANNOT-EVALUATE."""
    out = _opf_observe._run_git(git, root, ["--literal-pathspecs", "ls-files", "--cached", "-z",
                                            "--"] + sorted(paths))
    if not out.completed or out.rc != 0:
        raise InitOperationError("git could not read the index ({})".format(out.err.strip()),
                                 CANNOT_EVALUATE)
    return sorted(p for p in out.out.decode("utf-8", "replace").split("\x00") if p)


# --- the plan: payloads, the immutable envelope, and its deep validation (decision 5) --------------


def staging_name(relpath, operation_id):
    """The plan-recorded staging name of a planned file: a hidden sibling of the destination, bound
    to the operation (so it is ownership evidence the plan itself authorizes, never a guess)."""
    return ".{}{}{}".format(relpath.rsplit("/", 1)[-1], _STAGE_MARKER,
                            operation_id.replace("-", ""))


def _effect_order(paths):
    """The canonical creation order: the machine-store sources (sorted, init.toml last among them,
    so the provenance names a complete source set on disk before itself), then CHANGELOG.md, then
    the store pointer LAST (the pointer promises a store, so it appears only once the store exists)."""
    store = sorted(p for p in paths if p.startswith(_MACHINE_HOME + "/") and p != PROVENANCE_RELPATH)
    tail = [p for p in (PROVENANCE_RELPATH, CHANGELOG_RELPATH, _opf_store.POINTER_REL) if p in paths]
    return store + tail


def build_source_payloads(*, operation_id, binding, head, first_adoption, inventory_digest_value,
                          create_changelog, seed=None):
    """The exact bytes of every planned source {store-relative path: bytes}, init.toml included:
    the D1 builders' documents (counters seeded from a validated ancestral seed when one is given),
    the `dir:.` store pointer, CHANGELOG.md when the plan creates it, and the provenance computed
    over all of them EXCLUDING itself."""
    counters_seed = None
    if seed is not None:
        reason = seed_permanence_refusal(seed)
        if reason is not None:
            raise InitOperationError(reason)
        counters_seed = dict(seed.counters)
    try:
        documents = {
            "manifest.toml": _opf_init.build_manifest(),
            "counters.toml": _opf_init.build_counters(seed=counters_seed),
            "version.toml": _opf_init.build_version(),
            "worklog.toml": _opf_init.build_worklog(),
        }
        for tname in _opf_init.INDEX_TYPES:
            documents[tname + _opf_check.INDEX_SUFFIX] = _opf_init.build_index(tname)
    except _opf_init.InitError as exc:
        raise InitOperationError("a bootstrap builder refused: {}".format(exc))
    payloads = {"{}/{}".format(_MACHINE_HOME, n): t.encode("utf-8") for n, t in documents.items()}
    payloads[_opf_store.POINTER_REL] = _opf_emit.emit_checked(
        {"store": {"target": "dir:."}}).encode("utf-8")
    if create_changelog:
        payloads[CHANGELOG_RELPATH] = _CHANGELOG_PAYLOAD
    basis = {
        "spec_version": _opf_store.SUPPORTED_SPEC_VERSION,
        "operation_id": operation_id,
        "binding": binding,
        "head": head,
        "first_adoption": first_adoption,
        "inventory_digest": inventory_digest_value,
        "acceptance": dict(ACCEPTANCE_NONE),
    }
    payloads[PROVENANCE_RELPATH] = build_bootstrap_provenance(basis, dict(payloads)).encode("utf-8")
    return payloads


def compute_plan_digest(plan):
    """sha256 over the canonical JSON of the plan WITHOUT its plan_digest member (self-excluding)."""
    body = dict(plan)
    body.pop("plan_digest", None)
    return _opf_init_contract._digest(_opf_init_contract.canonical_json_bytes(body))


def build_init_plan(*, operation_id, binding, head, inventory_digest_value, application_time,
                    existing_changelog=None, seed=None):
    """Produce the immutable opf.init.plan/v1 envelope (a pure producer): returns (plan model, exact
    canonical bytes). `existing_changelog` is None when CHANGELOG.md is absent (the plan creates it)
    or its observed {path, mode, size, digest} entry, preserved in E. `seed` is a validated
    AncestralSeed for a re-adoption (first_adoption false), recorded as the counters source's basis
    so a retry never searches history again. The result is re-validated by validate_init_plan."""
    first_adoption = seed is None
    payloads = build_source_payloads(
        operation_id=operation_id, binding=binding, head=head, first_adoption=first_adoption,
        inventory_digest_value=inventory_digest_value,
        create_changelog=existing_changelog is None, seed=seed)
    sources = []
    for path in _effect_order(payloads):
        data = payloads[path]
        entry = {"path": path, "mode": SOURCE_MODE, "size": len(data),
                 "digest": _opf_init_contract._digest(data),
                 "payload": base64.b64encode(data).decode("ascii"),
                 "staging": staging_name(path, operation_id)}
        if path == COUNTERS_RELPATH:
            entry["basis"] = {"kind": "zero"} if seed is None else dict(
                kind="ancestral", module_counters=dict(seed.module_counters), **seed.evidence)
        sources.append(entry)
    plan = {
        "schema": 1,
        "format": _opf_init_substrate.PLAN_FORMAT,
        "operation": OPERATION,
        "operation_id": operation_id,
        "versions": {"spec_version": _opf_store.SUPPORTED_SPEC_VERSION,
                     "generator": INIT_GENERATOR, "provenance_format": PROVENANCE_FORMAT},
        "binding": binding,
        "head": head,
        "first_adoption": first_adoption,
        "inventory_digest": inventory_digest_value,
        "acceptance": dict(ACCEPTANCE_NONE),
        "application_time": application_time,
        "sets": {"S": sources, "V": [], "K": [],
                 "E": [] if existing_changelog is None else [dict(existing_changelog)],
                 "C": [{"path": LEASE_RELPATH, "kind": "lease"}]},
        "permitted_directories": [{"path": p, "mode": DIR_MODE} for p in PERMITTED_DIRECTORIES],
        "staging_set": [],
        "required_checks": list(REQUIRED_CHECKS),
        "publication_boundaries": dict(PUBLICATION_BOUNDARIES),
        "recovery_policy": RECOVERY_POLICY,
    }
    plan["plan_digest"] = compute_plan_digest(plan)
    raw = _opf_init_contract.canonical_json_bytes(plan)
    model = validate_init_plan(raw)
    return model, raw


def _plan_bad(detail):
    raise InitOperationError("plan invalid: {}".format(detail))


def validate_init_plan(raw, *, expected_binding=None, expected_head=None):
    """DEEP validation of plan bytes beyond the substrate's scalar checks (an externally supplied plan
    is inert data, never a capability): the exact canonical serialization and bounds; the frozen top
    keys; the pinned versions (a changed generator or spec version refuses); the Binding and HEAD
    (and, when given, equality with the live re-observation); the exact nested set schemas, the
    disjoint S/E/C sets, the exact source roster in the canonical effect order; each payload's
    base64, size, digest, mode, and plan-derived staging name; each payload's equality with the D1
    builders (the counters payload with its recorded basis); the provenance's validity against the
    plan's own basis and the recomputed source digest; the fixed directory, staging, check,
    boundary, and recovery members; and the recomputed plan digest. Returns the model; raises
    InitOperationError."""
    if type(raw) is not bytes or not raw or len(raw) > _opf_init_contract.MAX_RAW_BYTES:
        _plan_bad("plan bytes missing or over the {}-byte bound".format(
            _opf_init_contract.MAX_RAW_BYTES))
    try:
        plan = _opf_init_substrate._strict_json_loads(raw, "plan")
        _opf_init_substrate._validate_plan(raw, plan.get("operation_id"))
    except _opf_init_substrate.InitSubstrateError as exc:
        _plan_bad(str(exc))
    op_id = plan["operation_id"]
    if plan["versions"] != {"spec_version": _opf_store.SUPPORTED_SPEC_VERSION,
                            "generator": INIT_GENERATOR, "provenance_format": PROVENANCE_FORMAT}:
        _plan_bad("versions are not this generator's pinned versions")
    reason = _opf_init_contract._bad_binding(plan["binding"])
    if reason is not None:
        _plan_bad("binding: " + reason)
    fmt = plan["binding"]["object_format"]
    reason = _opf_init_contract._bad_head(plan["head"], fmt)
    if reason is not None:
        _plan_bad("head: " + reason)
    if expected_binding is not None and plan["binding"] != expected_binding:
        _plan_bad("binding does not equal the live re-observation (changed root, worktree, or "
                  "repository)")
    if expected_head is not None and plan["head"] != expected_head:
        _plan_bad("HEAD does not equal the live re-observation")
    if type(plan["first_adoption"]) is not bool:
        _plan_bad("first_adoption must be a boolean")
    if type(plan["inventory_digest"]) is not str \
            or not _opf_init_contract._DIGEST_RE.match(plan["inventory_digest"]):
        _plan_bad("inventory_digest grammar")
    if plan["acceptance"] != ACCEPTANCE_NONE:
        _plan_bad("acceptance must be {present: false} (Keep decisions are PR6)")
    at = plan["application_time"]
    try:
        if type(at) is not str or not _UTC_RE.match(at):
            raise ValueError
        datetime.datetime.strptime(at, _UTC_FORMAT)
    except ValueError:
        _plan_bad("application_time is not an RFC 3339 UTC timestamp")
    sets = plan["sets"]
    if type(sets) is not dict or set(sets) != PLAN_SET_KEYS:
        _plan_bad("sets must be exactly {S, V, K, E, C}")
    if sets["V"] != [] or sets["K"] != []:
        _plan_bad("V and K must be empty in a PR3a plan (views are PR3b, Keep is PR6)")
    if sets["C"] != [{"path": LEASE_RELPATH, "kind": "lease"}]:
        _plan_bad("C must be exactly the lease control record")
    existing = sets["E"]
    if type(existing) is not list or len(existing) > 1:
        _plan_bad("E must be a list of at most the one preserved CHANGELOG.md")
    for e in existing:
        if type(e) is not dict or set(e) != _E_KEYS or e["path"] != CHANGELOG_RELPATH \
                or type(e["mode"]) is not int or not 0 <= e["mode"] <= 0o777 \
                or type(e["size"]) is not int or e["size"] < 0 \
                or type(e["digest"]) is not str or not _opf_init_contract._DIGEST_RE.match(
                    e["digest"]):
            _plan_bad("E entry malformed")
    sources = sets["S"]
    if type(sources) is not list:
        _plan_bad("S must be a list")
    roster = set(BOOTSTRAP_SOURCE_ROSTER) | {PROVENANCE_RELPATH}
    if not existing:
        roster.add(CHANGELOG_RELPATH)
    paths = [s.get("path") if type(s) is dict else None for s in sources]
    if set(paths) != roster or len(paths) != len(roster):
        _plan_bad("S is not exactly the bootstrap source roster")
    if paths != _effect_order(set(paths)):
        _plan_bad("S is not in the canonical creation order")
    payloads = {}
    counters_basis = None
    for s in sources:
        keys = set(s)
        want = _S_KEYS | ({"basis"} if s["path"] == COUNTERS_RELPATH else set())
        if keys != want:
            _plan_bad("S entry {} keys".format(s["path"]))
        if type(s["mode"]) is not int or s["mode"] != SOURCE_MODE:
            _plan_bad("S entry {} mode must be {:o}".format(s["path"], SOURCE_MODE))
        if s["staging"] != staging_name(s["path"], op_id):
            _plan_bad("S entry {} staging name is not the plan-derived one".format(s["path"]))
        try:
            data = base64.b64decode(s["payload"].encode("ascii"), validate=True) \
                if type(s["payload"]) is str else None
        except (binascii.Error, UnicodeEncodeError, ValueError):
            data = None
        if data is None or type(s["size"]) is not int or len(data) != s["size"] \
                or len(data) > MAX_SOURCE_BYTES \
                or base64.b64encode(data).decode("ascii") != s["payload"]:
            _plan_bad("S entry {} payload is not canonical base64 of its size".format(s["path"]))
        if s["digest"] != _opf_init_contract._digest(data):
            _plan_bad("S entry {} digest does not match its payload".format(s["path"]))
        payloads[s["path"]] = data
        if s["path"] == COUNTERS_RELPATH:
            counters_basis = s["basis"]
    if plan["permitted_directories"] != [{"path": p, "mode": DIR_MODE}
                                         for p in PERMITTED_DIRECTORIES]:
        _plan_bad("permitted_directories must be exactly .working and its machine store")
    if plan["staging_set"] != []:
        _plan_bad("staging_set must be empty (tool git staging is PR5)")
    if plan["required_checks"] != list(REQUIRED_CHECKS):
        _plan_bad("required_checks is not this generator's roster")
    if plan["publication_boundaries"] != PUBLICATION_BOUNDARIES:
        _plan_bad("publication_boundaries is not the SOURCES-READY boundary")
    if plan["recovery_policy"] != RECOVERY_POLICY:
        _plan_bad("recovery_policy must be {!r}".format(RECOVERY_POLICY))
    if plan["plan_digest"] != compute_plan_digest(plan):
        _plan_bad("plan_digest does not match the recomputed digest")
    _validate_plan_payloads(plan, payloads, counters_basis)
    return plan


def _validate_plan_payloads(plan, payloads, counters_basis):
    """Each payload equals what the D1 builders produce (the counters from its recorded basis), and
    the provenance validates against the plan's own basis and the recomputed source digest."""
    fixed = {
        "{}/manifest.toml".format(_MACHINE_HOME): _opf_init.build_manifest(),
        "{}/version.toml".format(_MACHINE_HOME): _opf_init.build_version(),
        "{}/worklog.toml".format(_MACHINE_HOME): _opf_init.build_worklog(),
        _opf_store.POINTER_REL: _opf_emit.emit_checked({"store": {"target": "dir:."}}),
    }
    for tname in _opf_init.INDEX_TYPES:
        fixed["{}/{}{}".format(_MACHINE_HOME, tname, _opf_check.INDEX_SUFFIX)] = \
            _opf_init.build_index(tname)
    if CHANGELOG_RELPATH in payloads:
        fixed[CHANGELOG_RELPATH] = _CHANGELOG_PAYLOAD.decode("ascii")
    for path, text in fixed.items():
        if payloads[path] != text.encode("utf-8"):
            _plan_bad("S entry {} is not this generator's exact bytes".format(path))
    try:
        counters = tomllib.loads(payloads[COUNTERS_RELPATH].decode("utf-8", "strict"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError, ValueError):
        _plan_bad("the counters payload is not TOML")
    if type(counters_basis) is not dict:
        _plan_bad("the counters basis must be a table")
    kind = counters_basis.get("kind")
    if kind == "zero":
        if counters_basis != {"kind": "zero"} or not plan["first_adoption"]:
            _plan_bad("a zero counters basis belongs to a first adoption only")
        expected = _opf_init.build_counters()
    elif kind == "ancestral":
        if set(counters_basis) != {"kind", "module_counters", "commit", "blob", "path",
                                   "content_digest"} or plan["first_adoption"]:
            _plan_bad("an ancestral counters basis belongs to a re-adoption and carries its pinned "
                      "evidence")
        fmt = plan["binding"]["object_format"]
        if not (_oid_ok(counters_basis["commit"], fmt) and _oid_ok(counters_basis["blob"], fmt)
                and type(counters_basis["content_digest"]) is str
                and _opf_init_contract._DIGEST_RE.match(counters_basis["content_digest"])
                and type(counters_basis["path"]) is str
                and _opf_init_contract._bad_relpath(counters_basis["path"]) is None
                and type(counters_basis["module_counters"]) is dict
                and all(v == 0 for v in counters_basis["module_counters"].values())):
            _plan_bad("the ancestral counters evidence is malformed")
        try:
            expected = _opf_init.build_counters(seed=counters.get("counters"))
        except _opf_init.InitError as exc:
            _plan_bad("the counters payload is not a complete seeded ledger ({})".format(exc))
    else:
        _plan_bad("unknown counters basis kind {!r}".format(kind))
    if payloads[COUNTERS_RELPATH] != expected.encode("utf-8"):
        _plan_bad("the counters payload is not the builder's bytes for its basis")
    basis = {"spec_version": plan["versions"]["spec_version"],
             "operation_id": plan["operation_id"], "binding": plan["binding"],
             "head": plan["head"], "first_adoption": plan["first_adoption"],
             "inventory_digest": plan["inventory_digest"], "acceptance": plan["acceptance"]}
    others = {p: b for p, b in payloads.items() if p != PROVENANCE_RELPATH}
    source_set, source_digest = compute_bootstrap_source_digest(others)
    check = validate_bootstrap_provenance(payloads[PROVENANCE_RELPATH], expected_basis=basis,
                                          expected_source_digest=source_digest,
                                          expected_source_set=source_set)
    if check.status != VALID:
        _plan_bad("the provenance payload does not validate against the plan ({})".format(
            check.findings))
    if payloads[PROVENANCE_RELPATH] != build_bootstrap_provenance(basis, others).encode("utf-8"):
        _plan_bad("the provenance payload is not the builder's exact bytes")


def plan_payloads(plan):
    """{path: bytes} of a VALIDATED plan's sources, in creation order."""
    return {s["path"]: base64.b64decode(s["payload"]) for s in plan["sets"]["S"]}


# --- the preserving effect journal (decision 3; shared framing, INTENT and COMPLETE only) ------------

_ACCEPTED_FRAMES = ((), (_journal.F_INTENT,), (_journal.F_INTENT, _journal.F_COMPLETE))


def _txn_name(operation_id, group):
    return "{}-{}".format(operation_id, group)


def _journal_ensure_txn(jr_fd, txn):
    """Create the group's journal transaction directory and its frames.log exclusively on first
    use (each creation fsynced); an existing directory must be a plain directory. Idempotent across
    a crash between the two creations."""
    try:
        st = os.stat(txn, dir_fd=jr_fd, follow_symlinks=False)
    except FileNotFoundError:
        st = None
    except OSError as exc:
        raise InitOperationError("cannot stat journal {} ({})".format(txn, exc), CANNOT_EVALUATE)
    try:
        if st is None:
            os.mkdir(txn, 0o700, dir_fd=jr_fd)
            os.fsync(jr_fd)
        elif stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
            raise InitOperationError("journal {} is not a plain directory".format(txn))
        tfd = os.open(txn, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                      dir_fd=jr_fd)
        try:
            try:
                os.stat("frames.log", dir_fd=tfd, follow_symlinks=False)
                present = True
            except FileNotFoundError:
                present = False
        finally:
            os.close(tfd)
        if not present:
            _journal._create_frames_excl(jr_fd, txn)
    except OSError as exc:
        raise InitOperationError("cannot establish journal {} ({})".format(txn, exc), FAILED)
    except _journal.JournalError as exc:
        raise InitOperationError("cannot establish journal {} ({})".format(txn, exc), FAILED)


def _journal_frames(jr_fd, txn, notes):
    """The group's durable frames, validated for the PRESERVING init journal: only [], [INTENT],
    and [INTENT, COMPLETE], one txn id throughout. A torn tail is truncated before any append (the
    shared framing's never-written reading; product state is never rolled back, and recover() is
    never called on an init journal). Any rollback frame, other sequence, or corrupt frame refuses."""
    try:
        frames, torn, good = _journal.read_frames(jr_fd, txn)
        if torn:
            _journal._truncate_log(jr_fd, txn, good)
            notes.append("journal {}: a torn tail was truncated (treated as never written)".format(
                txn))
    except _journal.JournalError as exc:
        raise InitOperationError("journal {} is unreadable or corrupt ({})".format(txn, exc),
                                 CANNOT_EVALUATE)
    types = tuple(t for t, _obj in frames)
    if types not in _ACCEPTED_FRAMES:
        raise InitOperationError("journal {} holds the unsupported frame sequence {} (a preserving "
                                 "init journal has no rollback frames)".format(txn, list(types)),
                                 CANNOT_EVALUATE)
    for _t, obj in frames:
        if type(obj) is not dict or obj.get("txn") != txn:
            raise InitOperationError("journal {} holds a frame for another transaction".format(txn),
                                     CANNOT_EVALUATE)
    return frames


def _journal_publish(jr_fd, txn, ftype, obj):
    try:
        _journal.publish(jr_fd, txn, ftype, obj)
    except (_journal.JournalError, OSError) as exc:
        raise InitOperationError("cannot publish the {} frame of journal {} ({})".format(
            ftype, txn, exc), FAILED)


# --- directory and source effects (plan steps 5 and 6) --------------------------------------------


def _open_parent(root_fd, relpath):
    try:
        return _journal._open_parent(root_fd, relpath)
    except FileNotFoundError:
        raise InitOperationError("the parent of {} is absent".format(relpath), FAILED)
    except (_journal.JournalError, OSError) as exc:
        raise InitOperationError("cannot open the parent of {} no-follow ({})".format(relpath, exc),
                                 CANNOT_EVALUATE)


def _lstat(pfd, name, label):
    try:
        return os.stat(name, dir_fd=pfd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise InitOperationError("cannot stat {} ({})".format(label, exc), CANNOT_EVALUATE)


def _dir_modes(pfd, st, mode):
    """The acceptable exact modes of a planned directory: `mode`, plus S_ISGID when mkdir inherited
    it from a set-group-ID parent whose group it carries (the only special bit creation leaves)."""
    try:
        pst = os.fstat(pfd)
    except OSError as exc:
        raise InitOperationError("cannot fstat a directory parent ({})".format(exc),
                                 CANNOT_EVALUATE)
    if pst.st_mode & stat.S_ISGID and st.st_gid == pst.st_gid:
        return (mode, mode | stat.S_ISGID)
    return (mode,)


def _set_dir_mode(pfd, name, label, st, mode):
    """Set a directory's mode EXACTLY (umask-independent), bound to the stat'ed object and never
    following a link (the lock module's _chmod_bound), preserving an inherited S_ISGID."""
    target = max(_dir_modes(pfd, st, mode))
    try:
        _opf_oplock._chmod_bound(pfd, name, label, st, target)
    except _opf_oplock.OpLockError as exc:
        raise InitOperationError(str(exc), FAILED)
    after = _lstat(pfd, name, label)
    if after is None or not stat.S_ISDIR(after.st_mode) \
            or (after.st_dev, after.st_ino) != (st.st_dev, st.st_ino) \
            or stat.S_IMODE(after.st_mode) not in (target, mode):
        raise InitOperationError("{} changed, or is not at mode {:o}, after its mode was "
                                 "set".format(label, mode), FAILED)
    return after


def _fsync_dir_at(pfd, name, label):
    try:
        dfd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                      dir_fd=pfd)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
        os.fsync(pfd)
    except OSError as exc:
        raise InitOperationError("cannot fsync {} and its parent ({})".format(label, exc), FAILED)


def _apply_dirs(root_fd, plan, resuming):
    """Create each permitted directory parent-first, at its EXACT planned mode whatever the umask,
    each fsynced with its parent. An existing directory is accepted only on RESUME (a prior attempt
    recorded this group's intent): at its exact mode, or, when it is ours and its mode lies within
    the planned one (all an interrupted mkdir-then-chmod can leave), after its mode is completed. A
    symlink, a non-directory, a foreign owner, or any other mode refuses and is preserved. Returns
    {path: created | verified}."""
    out = {}
    for d in plan["permitted_directories"]:
        path, mode = d["path"], d["mode"]
        pfd, name = _open_parent(root_fd, path)
        try:
            st = _lstat(pfd, name, path)
            if st is None:
                try:
                    os.mkdir(name, mode, dir_fd=pfd)
                except OSError as exc:
                    raise InitOperationError("cannot create directory {} ({})".format(path, exc),
                                             FAILED)
                st = _lstat(pfd, name, path)
                if st is None or not stat.S_ISDIR(st.st_mode):
                    raise InitOperationError("directory {} vanished after creation".format(path),
                                             FAILED)
                _set_dir_mode(pfd, name, path, st, mode)
                out[path] = "created"
            else:
                if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
                    raise InitOperationError("{} exists and is not a plain directory; preserved "
                                             "and refused".format(path))
                if not resuming:
                    raise InitOperationError("{} appeared before this operation created it; "
                                             "preserved and refused".format(path))
                if st.st_uid != os.getuid():
                    raise InitOperationError("{} is owned by uid {}, not this operation's; "
                                             "refused".format(path, st.st_uid))
                modes = _dir_modes(pfd, st, mode)
                current = stat.S_IMODE(st.st_mode)
                if current not in modes:
                    if current & ~max(modes):
                        raise InitOperationError("{} has mode {:o}, outside the planned {:o}; "
                                                 "preserved and refused".format(path, current, mode))
                    _set_dir_mode(pfd, name, path, st, mode)
                out[path] = "verified"
            _fsync_dir_at(pfd, name, path)
        finally:
            os.close(pfd)
    return out


def _read_exact(pfd, name, label, size):
    """Read a destination for an exact-match decision: no-follow, non-blocking, regular, bounded
    at size + 1. Returns (bytes, fstat)."""
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=pfd)
    except OSError as exc:
        raise InitOperationError("cannot open {} ({})".format(label, exc), CANNOT_EVALUATE)
    try:
        try:
            st = os.fstat(fd)
        except OSError as exc:
            raise InitOperationError("cannot fstat {} ({})".format(label, exc), CANNOT_EVALUATE)
        if not stat.S_ISREG(st.st_mode):
            return None, st
        data = _read_bounded_fd(fd, size)
        return data, st
    finally:
        os.close(fd)


def _classify_dest(pfd, name, entry, data):
    """Three-way (plus refusal) classification of a planned file's destination: "absent"; "exact"
    (an opened, no-follow, singly-linked regular file whose bytes, size, and mode EXACTLY equal the
    plan, established by reading, never inferred from a name, header, or inode); anything else
    raises a REFUSED conflict (different bytes, a strict prefix included, a wrong mode, type, or
    link count), and an unreadable destination raises CANNOT-EVALUATE, never absence."""
    path = entry["path"]
    st = _lstat(pfd, name, path)
    if st is None:
        return "absent", None
    if not stat.S_ISREG(st.st_mode):
        raise InitOperationError("{} exists and is not a regular file; preserved and "
                                 "refused".format(path))
    got, fst = _read_exact(pfd, name, path, len(data))
    if got is None:
        raise InitOperationError("{} changed type while it was read; preserved and refused".format(
            path))
    if (fst.st_dev, fst.st_ino) != (st.st_dev, st.st_ino):
        raise InitOperationError("{} was replaced while it was classified; preserved and "
                                 "refused".format(path), CANNOT_EVALUATE)
    if fst.st_nlink != 1:
        raise InitOperationError("{} has {} links; preserved and refused".format(
            path, fst.st_nlink))
    if got != data:
        kind = "a strict prefix of" if data.startswith(got) and len(got) < len(data) \
            else "different from"
        raise InitOperationError("{} holds bytes {} the planned payload; preserved and refused, "
                                 "never repaired".format(path, kind))
    if stat.S_IMODE(fst.st_mode) != entry["mode"]:
        raise InitOperationError("{} holds the planned bytes at mode {:o}, not {:o}; preserved and "
                                 "refused".format(path, stat.S_IMODE(fst.st_mode), entry["mode"]))
    return "exact", fst


def _clean_stage(pfd, name, stage, path):
    """Settle the plan-recorded staging name before a file effect. A staging file this operation
    left (regular, one link) is garbage and removed; one killed after its link shares its inode
    with the destination (two links) and is removed so the destination keeps one link; a staging
    name bound to anything else refuses (manual intervention), preserved."""
    st = _lstat(pfd, stage, path + " staging")
    if st is None:
        return None
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        raise InitOperationError("the staging name of {} is not a regular file; refused".format(
            path))
    if st.st_nlink == 2:
        dst = _lstat(pfd, name, path)
        if dst is None or (dst.st_dev, dst.st_ino) != (st.st_dev, st.st_ino):
            raise InitOperationError("the staging name of {} is linked to something other than its "
                                     "destination; refused".format(path))
        outcome = "completed-publication"
    elif st.st_nlink == 1:
        outcome = "discarded-stage"
    else:
        raise InitOperationError("the staging name of {} has {} links; refused".format(
            path, st.st_nlink))
    try:
        os.unlink(stage, dir_fd=pfd)
        os.fsync(pfd)
    except OSError as exc:
        raise InitOperationError("cannot settle the staging name of {} ({})".format(path, exc),
                                 FAILED)
    return outcome


def _stage_and_publish(pfd, name, entry, data):
    """Stage the COMPLETE payload under the plan-recorded staging name (exclusive, no-follow, mode
    set exactly on the descriptor whatever the umask, written in full, fsynced, read back), then
    publish it by link(2), which never replaces an existing name, retire the staging name, and
    fsync the parent. Returns ("created", (dev, ino)) or, when the destination appeared meanwhile,
    the destination's classification ("exact" is a dedupe; anything else refuses)."""
    stage, path = entry["staging"], entry["path"]
    try:
        fd = os.open(stage, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                     0o600, dir_fd=pfd)
    except OSError as exc:
        raise InitOperationError("cannot create the staging file of {} ({})".format(path, exc),
                                 FAILED)
    try:
        try:
            os.fchmod(fd, entry["mode"])
            _journal._write_all(fd, data)
            os.fsync(fd)
            st = os.fstat(fd)
            back = bytearray()
            while len(back) < len(data) + 1:
                chunk = os.pread(fd, len(data) + 1 - len(back), len(back))
                if not chunk:
                    break
                back += chunk
        except OSError as exc:
            raise InitOperationError("cannot stage {} ({})".format(path, exc), FAILED)
        if bytes(back) != data or st.st_size != len(data) \
                or stat.S_IMODE(st.st_mode) != entry["mode"]:
            raise InitOperationError("the staged bytes or mode of {} are not the plan's".format(path),
                                     FAILED)
        ident = (st.st_dev, st.st_ino)
    finally:
        os.close(fd)
    try:
        os.link(stage, name, src_dir_fd=pfd, dst_dir_fd=pfd, follow_symlinks=False)
        linked = True
    except FileExistsError:
        linked = False
    except OSError as exc:
        raise InitOperationError("cannot publish {} ({}); its staging file is left for the next "
                                 "attempt to settle".format(path, exc), FAILED)
    try:
        os.unlink(stage, dir_fd=pfd)
        os.fsync(pfd)
    except OSError as exc:
        raise InitOperationError("cannot retire the staging name of {} ({})".format(path, exc),
                                 FAILED)
    if not linked:
        state, _st = _classify_dest(pfd, name, entry, data)
        return state, None
    return "created", ident


def _preclassify_sources(root_fd, plan, notes):
    """Plan step 8's continuation rule, checked BEFORE any effect of the group: every destination
    must be absent or an EXACT match of its planned payload, and every staging name absent or a
    plain file this operation left. Any conflict refuses here, so a resume creates nothing when some
    destination is not what the plan authorizes (the classification is repeated per file during
    publication, which still catches a race)."""
    for entry in plan["sets"]["S"]:
        data = base64.b64decode(entry["payload"])
        pfd, name = _open_parent(root_fd, entry["path"])
        try:
            # Settling this operation's own plan-named staging leftover first is what lets a
            # destination killed between its link and its staging retire (two links, the staging
            # name bound to it) classify as the exact single-link file it then is.
            settled = _clean_stage(pfd, name, entry["staging"], entry["path"])
            if settled is not None:
                notes.append("{}: {}".format(entry["path"], settled))
            _classify_dest(pfd, name, entry, data)
        finally:
            os.close(pfd)


def _apply_sources(root_fd, plan, notes):
    """Create or dedupe every planned source in plan order (decision 3), after the whole group has
    been pre-classified. Returns {path: created | deduplicated}. A conflict refuses, preserving the
    destination and everything created so far (the evidence a retry resumes from); nothing is ever
    overwritten, rolled back, or removed except this operation's own plan-named staging files."""
    _preclassify_sources(root_fd, plan, notes)
    out = {}
    for entry in plan["sets"]["S"]:
        data = base64.b64decode(entry["payload"])
        pfd, name = _open_parent(root_fd, entry["path"])
        try:
            settled = _clean_stage(pfd, name, entry["staging"], entry["path"])
            if settled is not None:
                notes.append("{}: {}".format(entry["path"], settled))
            state, _st = _classify_dest(pfd, name, entry, data)
            if state == "exact":
                out[entry["path"]] = "deduplicated"
                try:
                    os.fsync(pfd)
                except OSError as exc:
                    raise InitOperationError("cannot fsync the parent of {} ({})".format(
                        entry["path"], exc), FAILED)
                continue
            state, ident = _stage_and_publish(pfd, name, entry, data)
            if state == "created":
                got_state, fst = _classify_dest(pfd, name, entry, data)
                if got_state != "exact" or (fst.st_dev, fst.st_ino) != ident:
                    raise InitOperationError("{} is not the published inode after its "
                                             "publication".format(entry["path"]), FAILED)
            out[entry["path"]] = "created" if state == "created" else "deduplicated"
        finally:
            os.close(pfd)
    return out


# --- verification (plan steps 8 and 10) ----------------------------------------------------------


def _verify_dirs(root_fd, plan):
    posts = []
    for d in plan["permitted_directories"]:
        pfd, name = _open_parent(root_fd, d["path"])
        try:
            st = _lstat(pfd, name, d["path"])
            if st is None or stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode) \
                    or st.st_uid != os.getuid() \
                    or stat.S_IMODE(st.st_mode) not in _dir_modes(pfd, st, d["mode"]):
                raise InitOperationError("directory {} does not verify at mode {:o}".format(
                    d["path"], d["mode"]))
            posts.append({"path": d["path"], "kind": "directory",
                          "mode": stat.S_IMODE(st.st_mode)})
        finally:
            os.close(pfd)
    return posts


def _verify_sources(root_fd, plan):
    posts = []
    for entry in plan["sets"]["S"]:
        data = base64.b64decode(entry["payload"])
        pfd, name = _open_parent(root_fd, entry["path"])
        try:
            state, fst = _classify_dest(pfd, name, entry, data)
            if state != "exact":
                raise InitOperationError("{} is absent at verification".format(entry["path"]))
            if _lstat(pfd, entry["staging"], entry["path"] + " staging") is not None:
                raise InitOperationError("the staging name of {} is still present at "
                                         "verification".format(entry["path"]))
            posts.append({"path": entry["path"], "kind": "file", "mode": entry["mode"],
                          "size": entry["size"], "digest": entry["digest"]})
        finally:
            os.close(pfd)
    return posts


def _final_check(root, root_fd, plan, lease_held):
    """The fresh final observation behind SOURCES-READY: every planned directory and source exact;
    the .working inventory EXACTLY the planned tree (plus the lease while it is held); the local
    pointer absent; a preserved CHANGELOG.md unchanged; the provenance valid against the plan's
    basis and the source digest recomputed from the ON-DISK sources; the store RESOLVING at the
    product root to the planned machine store with a VALID manifest; and the counters valid.
    Returns the names of the checks executed (the plan's required roster, in order)."""
    _verify_dirs(root_fd, plan)
    _verify_sources(root_fd, plan)
    model, present = observe_inventory(root_fd)
    expect = {d["path"] for d in plan["permitted_directories"]
              if d["path"] != _opf_store.WORKING_DIRNAME}
    expect |= {s["path"] for s in plan["sets"]["S"]
               if s["path"].startswith(_opf_store.WORKING_DIRNAME + "/")}
    if lease_held:
        expect.add(LEASE_RELPATH)
    got = {e["path"] for e in model["entries"]}
    if not present or got != expect:
        raise InitOperationError("the .working tree is not exactly the planned tree (unexpected: "
                                 "{}; missing: {})".format(sorted(got - expect),
                                                           sorted(expect - got)))
    if _lstat(root_fd, _opf_store.LOCAL_POINTER_REL, _opf_store.LOCAL_POINTER_REL) is not None:
        raise InitOperationError("a local store pointer {} appeared; it would override the "
                                 "store".format(_opf_store.LOCAL_POINTER_REL))
    for e in plan["sets"]["E"]:
        data, fst = _read_regular(root_fd, e["path"], e["path"], _opf_init_contract.MAX_RAW_BYTES)
        if (stat.S_IMODE(fst.st_mode), len(data), _opf_init_contract._digest(data)) \
                != (e["mode"], e["size"], e["digest"]):
            raise InitOperationError("the preserved {} changed during the operation".format(
                e["path"]))
    on_disk = {}
    for entry in plan["sets"]["S"]:
        pfd, name = _open_parent(root_fd, entry["path"])
        try:
            on_disk[entry["path"]] = _read_exact(pfd, name, entry["path"], entry["size"])[0]
        finally:
            os.close(pfd)
    others = {p: b for p, b in on_disk.items() if p != PROVENANCE_RELPATH}
    source_set, source_digest = compute_bootstrap_source_digest(others)
    basis = {"spec_version": plan["versions"]["spec_version"],
             "operation_id": plan["operation_id"], "binding": plan["binding"],
             "head": plan["head"], "first_adoption": plan["first_adoption"],
             "inventory_digest": plan["inventory_digest"], "acceptance": plan["acceptance"]}
    check = validate_bootstrap_provenance(on_disk[PROVENANCE_RELPATH], expected_basis=basis,
                                          expected_source_digest=source_digest,
                                          expected_source_set=source_set)
    if check.status != VALID:
        raise InitOperationError("the on-disk provenance does not validate ({})".format(
            check.findings))
    res = _opf_store.resolve_store(root)
    if res.status != _opf_store.RESOLVED or os.path.abspath(str(res.store_root)) != root \
            or res.machine_rel != _MACHINE_HOME:
        raise InitOperationError("the store does not resolve to the planned machine store ({}: "
                                 "{})".format(res.status, res.detail))
    manifest = _opf_store.load_manifest(res)
    if manifest.status != _opf_store.VALID or manifest.findings:
        raise InitOperationError("the published manifest is not VALID ({})".format(
            manifest.findings))
    baseline, importer, _module = _roster_namespaces()
    counters = tomllib.loads(on_disk[COUNTERS_RELPATH].decode("utf-8"))
    _hw, findings = validate_counters(counters, known_namespaces=baseline,
                                      optional_namespaces=importer)
    if findings:
        raise InitOperationError("the published counters are not valid ({})".format(findings))
    return list(REQUIRED_CHECKS)


def _health_check(root, root_fd, plan, binding):
    """Validate immutable bootstrap provenance and CURRENT source health (decision 5).

    The digest is recomputed from the validated plan payloads, never from later edited
    sources. The plan's binding must still identify this root; its historical HEAD need
    not equal today's HEAD. Current source validation permits legitimate later edits.
    """
    plan = validate_init_plan(_opf_init_contract.canonical_json_bytes(plan),
                              expected_binding=binding)
    res = _opf_store.resolve_store(root)
    if res.status != _opf_store.RESOLVED or os.path.abspath(str(res.store_root)) != root \
            or res.machine_rel != _MACHINE_HOME:
        raise InitOperationError("the completed adoption's store does not resolve ({}: {})".format(
            res.status, res.detail))
    manifest = _opf_store.load_manifest(res)
    if manifest.status != _opf_store.VALID or manifest.findings:
        raise InitOperationError("the completed adoption's manifest is not VALID ({})".format(
            manifest.findings))
    prov_path = "{}/{}".format(res.machine_rel, PROVENANCE_NAME)
    pfd, name = _open_parent(root_fd, prov_path)
    try:
        raw, _st = _read_regular(pfd, name, prov_path, _opf_init_contract.MAX_RAW_BYTES)
    finally:
        os.close(pfd)
    basis = {"spec_version": plan["versions"]["spec_version"],
             "operation_id": plan["operation_id"], "binding": plan["binding"],
             "head": plan["head"], "first_adoption": plan["first_adoption"],
             "inventory_digest": plan["inventory_digest"], "acceptance": plan["acceptance"]}
    payloads = {p: b for p, b in plan_payloads(plan).items() if p != PROVENANCE_RELPATH}
    source_set, source_digest = compute_bootstrap_source_digest(payloads)
    check = validate_bootstrap_provenance(raw, expected_basis=basis,
                                          expected_source_digest=source_digest,
                                          expected_source_set=source_set)
    if check.status != VALID:
        raise InitOperationError("the completed adoption's provenance is invalid or names another "
                                 "operation ({})".format(check.findings))
    counters_rel = "{}/{}".format(res.machine_rel, _opf_check.COUNTERS_NAME)
    pfd, name = _open_parent(root_fd, counters_rel)
    try:
        raw, _st = _read_regular(pfd, name, counters_rel, _opf_init_contract.MAX_RAW_BYTES)
    finally:
        os.close(pfd)
    baseline, importer, _module = _roster_namespaces()
    try:
        counters = tomllib.loads(raw.decode("utf-8", "strict"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError, ValueError):
        raise InitOperationError("the completed adoption's counters are not TOML")
    _hw, findings = validate_counters(counters, known_namespaces=baseline,
                                      optional_namespaces=importer)
    if findings:
        raise InitOperationError("the completed adoption's counters are invalid ({})".format(
            findings))
    if _lstat(root_fd, _opf_store.POINTER_REL, _opf_store.POINTER_REL) is None:
        raise InitOperationError("the completed adoption's store pointer is absent")
    # This is a source milestone, not a whole-store/doctor verdict. Tracking,
    # deliverables and across-time history are outside current-source health.
    # Subtract from the authoritative roster so future checks fail closed by default.
    deferred = {"C-TRACKED", "C-HISTORY-APPEND-ONLY", "C-HISTORY-COUNTERS",
                "C-HISTORY-RESURRECTION"}
    # C-NO-DELETION reads only current sources (counters against present ids), so it stays
    # required. A re-adoption's validated plan carries the ancestral seed its counters copied
    # with no record restored; ids at or below it are not deletions, ids above it still are.
    floor = None
    if not plan["first_adoption"]:
        floor = tomllib.loads(payloads[COUNTERS_RELPATH].decode("utf-8"))["counters"]
    observations, _notes = _opf_observe.gather(res)
    health = _opf_check.validate_store(res, observations=observations, ancestral_floor=floor)
    required = set(_opf_check.source_checks(health)) - deferred
    bad = sorted(cid for cid in required if health.checks.get(cid) != "PASS")
    if health.unattributed or health.triage or bad:
        details = {cid: health.by_check.get(cid, []) for cid in bad}
        raise InitOperationError("the completed adoption's current sources are unhealthy "
                                 "(checks {}; details {}; unattributed {}; triage {})".format(
                                     bad, details, health.unattributed, health.triage))


# --- the operation: select, plan or resume, journal, attach, verify, finalize (plan steps 1-10) ----


def _utc_now():
    """RFC 3339 UTC from the clock at the event (timestamp-from-clock)."""
    return time.strftime(_UTC_FORMAT, time.gmtime())


def _phase_names(sub):
    names = [p for _s, p in _opf_init_substrate.recorded_phases(sub)]
    if names != list(PHASES[:len(names)]):
        raise InitOperationError("operation {} recorded the phase sequence {}, which is not a prefix "
                                 "of the closed milestone order {}".format(sub.op_id, names,
                                                                           list(PHASES)),
                                 CANNOT_EVALUATE)
    return names


class _Run:
    """The mutable state of one attempt (never shared across attempts)."""
    __slots__ = ("root", "root_fd", "holder", "cap", "back", "sub", "plan", "result", "phases",
                 "checks")

    def __init__(self, root, result):
        self.root = root
        self.root_fd = None
        self.holder = self.cap = self.back = self.sub = self.plan = None
        self.result = result
        self.phases = []
        self.checks = []

    def writer(self):
        """The live writer bound to this attempt's operation: the capability while the lease is
        attached, otherwise the pre-store holder or the holder the lease detach returned."""
        for w in (self.cap, self.back, self.holder):
            if w is not None and not w._released and not getattr(w, "_spent", False):
                return w
        return None

    def record(self, phase):
        if phase in self.phases:
            return
        expected = PHASES[len(self.phases)]
        if phase != expected:
            raise InitOperationError("milestone {} recorded out of order (next is {})".format(
                phase, expected), FAILED)
        try:
            _opf_init_substrate.record_phase(self.sub, self.writer(), phase)
        except _opf_init_substrate.InitSubstrateError as exc:
            raise InitOperationError("cannot record milestone {} ({})".format(phase, exc), FAILED)
        self.phases.append(phase)


def _run_group(run, group, effects, apply_fn, verify_fn):
    """One journaled effect group (decision 3): the journal INTENT (the exact effects, derived from
    the immutable plan) is published durably BEFORE any effect and must equal the plan's on resume;
    the `<group>-intent` milestone follows; the effects are applied (idempotent create-or-dedupe)
    unless the journal is COMPLETE, in which case the disk is re-verified and a contradiction
    refuses; the fresh poststate is verified and the COMPLETE frame published; the
    `<group>-verified` milestone follows. A milestone the journal contradicts (an intent milestone
    with no journaled intent) refuses."""
    writer = run.writer()
    txn = _txn_name(run.plan["operation_id"], group)
    intent = {"txn": txn, "operation_id": run.plan["operation_id"],
              "plan_digest": run.plan["plan_digest"], "group": group, "effects": effects}
    try:
        jr_fd = _opf_init_substrate.open_journal_home(writer)
    except _opf_init_substrate.InitSubstrateError as exc:
        raise InitOperationError("cannot open the init journal home ({})".format(exc), FAILED)
    try:
        _journal_ensure_txn(jr_fd, txn)
        frames = _journal_frames(jr_fd, txn, run.result.notes)
        resuming = bool(frames)
        if not frames:
            if group + "-intent" in run.phases:
                raise InitOperationError("milestone {}-intent is recorded but the journal holds no "
                                         "intent; contradictory evidence is preserved and "
                                         "refused".format(group), CANNOT_EVALUATE)
            _journal_publish(jr_fd, txn, _journal.F_INTENT, intent)
        elif frames[0][1] != intent:
            raise InitOperationError("journal {} records an intent that is not this plan's; "
                                     "changed evidence is preserved and refused".format(txn),
                                     CANNOT_EVALUATE)
        if group + "-verified" in run.phases and len(frames) != 2:
            raise InitOperationError("milestone {}-verified is recorded but journal {} is not "
                                     "COMPLETE; contradictory evidence is preserved and "
                                     "refused".format(group, txn), CANNOT_EVALUATE)
        run.record(group + "-intent")
        if len(frames) == 2:
            posts = verify_fn()
            if frames[1][1].get("poststates") != posts:
                raise InitOperationError("journal {} is COMPLETE but the disk contradicts its "
                                         "recorded poststates; preserved and refused".format(txn))
            for p in posts:
                run.result.deduplicated.append(p["path"])
        else:
            outcomes = apply_fn(resuming)
            posts = verify_fn()
            for path in sorted(outcomes):
                (run.result.created if outcomes[path] == "created"
                 else run.result.deduplicated).append(path)
            _journal_publish(jr_fd, txn, _journal.F_COMPLETE, {"txn": txn, "poststates": posts})
        run.record(group + "-verified")
    finally:
        os.close(jr_fd)


def _dir_effects(plan):
    return [{"kind": "mkdir", "path": d["path"], "mode": d["mode"]}
            for d in plan["permitted_directories"]]


def _source_effects(plan):
    return [{"kind": "create", "path": s["path"], "mode": s["mode"], "size": s["size"],
             "digest": s["digest"], "staging": s["staging"]} for s in plan["sets"]["S"]]


def _select(run, binding):
    """Select the operation under the held mutex (plan step 2): settle every recorded operation (the
    exact staging sweep; an operation left empty authorized nothing and is discarded, reported),
    then classify. Any CANNOT-EVALUATE operation refuses (preserved unevaluable evidence); more
    than one partial operation, or a partial beside a completed one, refuses (never chosen by
    timestamp or directory order); exactly one partial resumes; exactly one completed is a
    completed adoption; none is a fresh adoption."""
    try:
        survey = _opf_init_substrate.classify_init_operations(run.root)
        if survey.status == _opf_init_substrate.OPERATIONS:
            for rep in survey.operations:
                if not _OP_ID_RE.match(rep.op_id):
                    continue
                swept, discarded = _opf_init_substrate.settle_operation(run.holder, rep.op_id)
                for name in swept:
                    run.result.notes.append("ops/{}: swept staging leftover {}".format(
                        rep.op_id, name))
                if discarded:
                    run.result.notes.append("ops/{}: discarded an empty operation directory (its "
                                            "plan was never published)".format(rep.op_id))
            survey = _opf_init_substrate.classify_init_operations(run.root)
    except _opf_init_substrate.InitSubstrateError as exc:
        raise InitOperationError("the init substrate cannot be classified ({})".format(exc),
                                 CANNOT_EVALUATE)
    ops = survey.operations if survey.status == _opf_init_substrate.OPERATIONS else ()
    bad = [r for r in ops if r.status != _opf_init_substrate.INTACT]
    if bad:
        raise InitOperationError("recorded init operation(s) cannot be evaluated and are preserved: "
                                 "{}".format("; ".join("{}: {}".format(r.op_id, r.detail)
                                                       for r in bad)), CANNOT_EVALUATE)
    completed, partial = [], []
    for rep in ops:
        names = [p for _s, p in rep.phases]
        if names != list(PHASES[:len(names)]):
            raise InitOperationError("operation {} recorded an unsupported phase sequence {}".format(
                rep.op_id, names), CANNOT_EVALUATE)
        (completed if names and names[-1] == PHASES[-1] else partial).append(rep)
    if len(partial) > 1 or (partial and completed) or len(completed) > 1:
        raise InitOperationError("ambiguous init history ({} partial, {} completed operations); "
                                 "never chosen by timestamp or directory order".format(
                                     len(partial), len(completed)))
    if partial:
        return "resume", partial[0]
    if completed:
        return "completed", completed[0]
    return "fresh", None


def _fresh_plan(run, binding, head, ancestral, git):
    """Preflight a FRESH adoption under the mutex and produce its immutable plan: no pointer, no
    resolvable or partial store, no .working at all, every destination and staging name absent, no
    destination already tracked, and CHANGELOG.md either absent (created) or a plain file (preserved
    in E). With `ancestral` (a pinned evidence commit, PR4's selection) the counters are seeded
    from the validated snapshot and the plan records it."""
    import uuid
    root, root_fd = run.root, run.root_fd
    for pointer in (_opf_store.POINTER_REL, _opf_store.LOCAL_POINTER_REL):
        if _lstat(root_fd, pointer, pointer) is not None:
            raise InitOperationError("an existing store pointer {} is present".format(pointer))
    model, present = observe_inventory(root_fd)
    if present:
        raise InitOperationError("a .working directory already exists ({} entries); foreign content "
                                 "is never adopted by a fresh init".format(len(model["entries"])))
    res = _opf_store.resolve_store(root)
    if res.status == _opf_store.RESOLVED:
        raise InitOperationError("an existing store resolves at {} ({}); this layer adopts only an "
                                 "unadopted root (completed-adoption and committed-deletion "
                                 "classification is PR4)".format(root, res.detail))
    if res.status != _opf_store.NOT_ADOPTED:
        raise InitOperationError("store resolution refused: {}".format(res.detail),
                                 CANNOT_EVALUATE)
    op_id = str(uuid.uuid4())
    changelog = None
    st = _lstat(root_fd, CHANGELOG_RELPATH, CHANGELOG_RELPATH)
    if st is not None:
        data, fst = _read_regular(root_fd, CHANGELOG_RELPATH, CHANGELOG_RELPATH,
                                  _opf_init_contract.MAX_RAW_BYTES)
        changelog = {"path": CHANGELOG_RELPATH, "mode": stat.S_IMODE(fst.st_mode) & 0o777,
                     "size": len(data), "digest": _opf_init_contract._digest(data)}
    seed = None
    if ancestral is not None:
        if head["kind"] != "commit":
            raise InitOperationError("an ancestral seed needs a commit HEAD to pin its ancestry")
        seed = read_ancestral_counter_seed(
            root, pinned_head=head["oid"], evidence_commit=ancestral,
            prefix=binding["product_prefix"], object_format=binding["object_format"], git=git)
    plan, raw = build_init_plan(operation_id=op_id, binding=binding, head=head,
                                inventory_digest_value=inventory_digest(model),
                                application_time=_utc_now(), existing_changelog=changelog,
                                seed=seed)
    destinations = [s["path"] for s in plan["sets"]["S"]] + list(PERMITTED_DIRECTORIES)
    for s in plan["sets"]["S"]:
        for rel in (s["path"], "{}/{}".format(s["path"].rsplit("/", 1)[0], s["staging"])
                    if "/" in s["path"] else s["staging"]):
            try:
                present_now = _journal._lstat_contained(root_fd, rel) is not None
            except (_journal.JournalError, OSError) as exc:
                raise InitOperationError("cannot observe {} ({})".format(rel, exc), CANNOT_EVALUATE)
            if present_now:
                raise InitOperationError("planned destination {} already exists".format(rel))
    tracked = _tracked_destinations(git, root, destinations + [_opf_store.LOCAL_POINTER_REL])
    if tracked:
        raise InitOperationError("planned destination(s) already tracked by git: {}; a deletion "
                                 "present only in the worktree or index is never re-initialized "
                                 "over".format(tracked))
    return plan, raw


def _outcome(run, primary):
    """The frozen opf.init.outcome/v1 envelope for this attempt (the fields PR3a observes; the index
    and git-object observations are the deferred PR5 legs, recorded empty, never invented)."""
    r = run.result
    return {
        "schema": 1, "format": _opf_init_substrate.OUTCOME_FORMAT, "operation": OPERATION,
        "operation_id": run.plan["operation_id"], "binding": run.plan["binding"],
        "head": run.plan["head"], "plan": run.plan["plan_digest"],
        "last_attempted_phase": run.phases[-1] if run.phases else None,
        "primary_failure": primary,
        "finalization_failures": list(r.finalization_failures),
        "path_observations": [{"path": p, "state": "created"} for p in r.created]
        + [{"path": p, "state": "verified-identical"} for p in r.deduplicated]
        + [{"path": p, "state": "conflict"} for p in r.conflicts],
        "source_index_observations": [],
        "creation_evidence": sorted(r.created),
        "git_object_observations": [],
        "checks_executed": list(run.checks),
        "controls": {"lease": "detached" if run.back is not None else "held-or-released"},
        "durability": {"files": "fsync", "directories": "fsync", "power_loss": "unverified"},
        "completed_phases": list(run.phases),
        "rollback": "none",
    }


def _record_outcome(run, primary):
    writer = run.writer()
    if run.sub is None or run.plan is None or writer is None:
        if run.sub is not None:
            run.result.finalization_failures.append(
                "the attempt outcome was not persisted: no live writer remained to record it")
        return
    try:
        payload = _opf_init_contract.canonical_json_bytes(_outcome(run, primary))
        _opf_init_substrate.record_outcome(run.sub, writer, payload)
    except (_opf_init_substrate.InitSubstrateError, ValueError, TypeError) as exc:
        run.result.finalization_failures.append("the attempt outcome was not persisted: {}".format(
            exc))


def _release_all(run):
    """Release what this attempt still holds, in reverse lock order, each failure collected as a
    finalization failure (never masking the primary outcome)."""
    fails = run.result.finalization_failures
    if run.sub is not None:
        try:
            _opf_init_substrate.close_operation(run.sub)
        except _opf_init_substrate.InitSubstrateError as exc:
            fails.append("closing the operation handle: {}".format(exc))
        run.sub = None
    if run.cap is not None and not run.cap._released:
        try:
            _opf_oplock.release_operation(run.cap)
        except _opf_oplock.OpLockError as exc:
            fails.append("releasing the capability: {}".format(exc))
    for h in (run.back, run.holder):
        if h is not None and not h._released and not h._spent:
            try:
                _opf_oplock.release_init_holder(h)
            except _opf_oplock.OpLockError as exc:
                fails.append("releasing the init holder: {}".format(exc))
    if run.root_fd is not None:
        try:
            os.close(run.root_fd)
        except OSError as exc:
            fails.append("closing the product root: {}".format(exc))
        run.root_fd = None


def run_init_sources(product_root, *, ancestral=None, recover=False):
    """Run (or resume) the PR3a init operation on the EXPLICIT absolute product root and return an
    InitResult; never raises for an expected outcome. Takes the shared mutex before any write
    (pre-store holder), re-observes the binding and HEAD under it (a pre-lock observation never
    authorizes), selects the operation, and then: a completed adoption is health-validated and the
    lock released with no adoption file or index entry changed (ALREADY-INITIALIZED); a fresh
    adoption's plan is persisted (plan-recorded) BEFORE any worktree write; the directory group runs
    under the holder; the mandatory lease is attached; the source group runs under the capability;
    the lease is detached (the last store write), the final check runs under the still-held mutex,
    and only then is `sources-ready` (durable completion) recorded, the attempt outcome recorded, and
    the mutex released. Any failure preserves the worktree and the evidence and is reported with
    its code; release failures are reported separately. `ancestral` is a pinned evidence commit for
    a re-adoption's counters seed (PR4 selects it); `recover` is passed to the lock's explicit,
    confirmed-dead recovery."""
    result = InitResult()
    try:
        root = _abs_root(product_root)
        _journal.require_containment()
        if not _containment.probe():
            raise InitOperationError("race-free containment primitive absent", CANNOT_EVALUATE)
        git = _opf_observe._git_path()
        if git is None:
            raise InitOperationError("git binary not found on PATH", CANNOT_EVALUATE)
        pre_binding, pre_head = observe_binding(root, git)
    except (InitOperationError, _journal.JournalError) as exc:
        result.status = getattr(exc, "code", CANNOT_EVALUATE)
        result.primary_failure = {"code": result.status, "detail": str(exc)}
        return result
    run = _Run(root, result)
    primary = None
    try:
        try:
            run.holder = _opf_oplock.acquire_init_operation(root, OPERATION, recover=recover)
        except _opf_oplock.OpLockError as exc:
            raise InitOperationError("cannot take the operation mutex ({})".format(exc))
        binding, head = observe_binding(root, git)
        if (binding, head) != (pre_binding, pre_head):
            raise InitOperationError("the binding or HEAD changed while the mutex was taken; "
                                     "re-run against a settled repository")
        try:
            run.root_fd = _opf_store._open_dir_nofollow(root)
        except OSError as exc:
            raise InitOperationError("cannot open the product root ({})".format(exc),
                                     CANNOT_EVALUATE)
        rst = os.fstat(run.root_fd)
        if (rst.st_dev, rst.st_ino) != (binding["product_root"]["identity"]["device"],
                                        binding["product_root"]["identity"]["inode"]):
            raise InitOperationError("the product root changed identity after it was observed")
        kind, rep = _select(run, binding)
        if kind == "completed":
            _health_check(root, run.root_fd, rep.plan, binding)
            result.status = ALREADY_INITIALIZED
            result.operation_id = rep.op_id
            result.plan_digest = rep.plan["plan_digest"]
            result.phases = tuple(p for _s, p in rep.phases)
            return result
        if kind == "fresh":
            plan, raw = _fresh_plan(run, binding, head, ancestral, git)
            try:
                run.sub = _opf_init_substrate.begin_operation(run.holder, raw)
            except _opf_init_substrate.InitSubstrateError as exc:
                raise InitOperationError("cannot persist the plan ({})".format(exc), FAILED)
            run.plan = plan
            run.record("plan-recorded")
        else:
            try:
                run.sub = _opf_init_substrate.resume_operation(run.holder, rep.op_id,
                                                               rep.plan["plan_digest"])
            except _opf_init_substrate.InitSubstrateError as exc:
                raise InitOperationError("cannot resume operation {} ({})".format(rep.op_id, exc),
                                         CANNOT_EVALUATE)
            run.plan = validate_init_plan(run.sub._plan_bytes, expected_binding=binding,
                                          expected_head=head)
            run.phases = _phase_names(run.sub)
            if not run.phases:
                run.record("plan-recorded")
            result.notes.append("resumed operation {} after milestone {}".format(
                rep.op_id, run.phases[-1]))
        result.operation_id = run.plan["operation_id"]
        result.plan_digest = run.plan["plan_digest"]
        _run_group(run, "dirs", _dir_effects(run.plan),
                   lambda resuming: _apply_dirs(run.root_fd, run.plan, resuming),
                   lambda: _verify_dirs(run.root_fd, run.plan))
        try:
            run.cap = _opf_oplock.attach_init_lease(run.holder, run.plan["operation_id"])
        except _opf_oplock.OpLockError as exc:
            raise InitOperationError("cannot attach the mandatory lease ({})".format(exc), FAILED)
        _run_group(run, "sources", _source_effects(run.plan),
                   lambda resuming: _apply_sources(run.root_fd, run.plan, result.notes),
                   lambda: _verify_sources(run.root_fd, run.plan))
        try:
            run.back = _opf_oplock.detach_init_lease(run.cap)
        except _opf_oplock.OpLockError as exc:
            raise InitOperationError("the lease detach failed ({}); completion is not recorded, so "
                                     "a retry resumes".format(exc), FAILED)
        run.checks = _final_check(root, run.root_fd, run.plan, lease_held=False)
        run.record("sources-ready")
        result.status = SOURCES_READY
        result.milestone = MILESTONE
    except InitOperationError as exc:
        primary = {"code": exc.code, "detail": str(exc)}
        if "preserved and refused" in str(exc):
            result.conflicts.append(str(exc).split(" ", 1)[0])
    except (_opf_oplock.OpLockError, _opf_init_substrate.InitSubstrateError,
            _journal.JournalError, OSError, ValueError) as exc:
        primary = {"code": CANNOT_EVALUATE, "detail": "{}: {}".format(type(exc).__name__, exc)}
    finally:
        if primary is not None:
            result.primary_failure = primary
            result.status = FAILED if run.sub is not None and primary["code"] == FAILED \
                else primary["code"]
        if run.plan is not None:
            _record_outcome(run, primary)
        result.phases = tuple(run.phases) or result.phases
        _release_all(run)
    return result


def init_operation(product_root, *, ancestral=None, recover=False):
    """Public CLI seam; preserve the library result and decision-7 milestone boundary."""
    return run_init_sources(product_root, ancestral=ancestral, recover=recover)


# --- self-test ------------------------------------------------------------------------------------
#
# The physical tests drive the REAL operation in child processes: `--selftest-child ROOT KILL [SEED]`
# runs run_init_sources in a fresh interpreter, optionally SIGKILLing itself at a named point through
# hooks the child entry installs (production code carries no kill hooks), and prints the result as
# JSON. Each retry is a fresh process, as a real restart would be. Process-kill tests establish
# process-crash behaviour only; power-loss durability is NOT verified here.


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


def _mk_head():
    return {"kind": "commit", "oid": "a" * 40,
            "binding": {"kind": "symbolic", "ref": "refs/heads/main"}}


def _mk_basis(**over):
    basis = {
        "spec_version": _opf_store.SUPPORTED_SPEC_VERSION,
        "operation_id": "12345678-1234-1234-1234-1234567890ab",
        "binding": _mk_binding(),
        "head": _mk_head(),
        "first_adoption": True,
        "inventory_digest": "sha256:" + "0" * 64,
        "acceptance": {"present": False},
    }
    basis.update(over)
    return basis


def _mk_payloads(extra=None):
    payloads = {p: (p + "\n").encode("utf-8") for p in BOOTSTRAP_SOURCE_ROSTER}
    if extra:
        payloads.update(extra)
    return payloads


def _mk_plan(**over):
    args = dict(operation_id="12345678-1234-4234-8234-1234567890ab", binding=_mk_binding(),
                head=_mk_head(), inventory_digest_value="sha256:" + "0" * 64,
                application_time="2026-01-02T03:04:05Z")
    args.update(over)
    return build_init_plan(**args)


def _st_git_env(base):
    env = dict((k, v) for k, v in os.environ.items() if not k.startswith("GIT_"))
    env["HOME"] = base
    env["XDG_CONFIG_HOME"] = os.path.join(base, "xdg")
    env["GIT_CONFIG_GLOBAL"] = os.path.join(base, "gitconfig-global")
    env["GIT_CONFIG_SYSTEM"] = os.path.join(base, "gitconfig-system")
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_AUTHOR_NAME"] = "Test"
    env["GIT_AUTHOR_EMAIL"] = "test@example.invalid"
    env["GIT_COMMITTER_NAME"] = "Test"
    env["GIT_COMMITTER_EMAIL"] = "test@example.invalid"
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["LC_ALL"] = "C"
    os.makedirs(env["XDG_CONFIG_HOME"], exist_ok=True)
    return env


def _git(args, cwd, env, allow_fail=False, input_bytes=None):
    import subprocess
    proc = subprocess.run(["git", "-C", cwd] + args, env=env, input=input_bytes,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
    if proc.returncode != 0 and not allow_fail:
        raise RuntimeError("git {} failed: {}".format(args, proc.stderr.decode("utf-8", "replace")))
    return proc


def _write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)


def _counters_toml(values):
    """A canonical counters.toml body for a fixture: schema marker plus the given {ns: int}."""
    lines = ["schema = {}".format(SUPPORTED_SCHEMA), "", "[counters]"]
    for ns in sorted(values):
        lines.append("{} = {}".format(ns, values[ns]))
    return ("\n".join(lines) + "\n").encode("utf-8")


def _head(repo, env):
    return _git(["rev-parse", "HEAD"], repo, env).stdout.decode().strip()


def _seed_repo(root, env, values, prefix=""):
    """A git repo with a committed counters.toml under `prefix`: (repo, HEAD oid, bytes)."""
    os.makedirs(root, exist_ok=True)
    _git(["init", "-q", "-b", "main", "."], root, env)
    rel = os.path.join(prefix, ".working", "toml", "counters.toml") if prefix \
        else os.path.join(".working", "toml", "counters.toml")
    body = _counters_toml(values)
    _write(os.path.join(root, rel), body)
    _git(["add", "-A"], root, env)
    _git(["commit", "-q", "-m", "seed"], root, env)
    return root, _head(root, env), body


def _plain_repo(root, env):
    os.makedirs(root, exist_ok=True)
    _git(["init", "-q", "-b", "main", "."], root, env)
    _write(os.path.join(root, "README"), b"fixture\n")
    _git(["add", "-A"], root, env)
    _git(["commit", "-q", "-m", "fixture"], root, env)
    return root


def _tree_snapshot(root):
    """{relpath: (kind, mode, bytes or link target)} of every entry beneath root except .git."""
    snap = {}
    for base, dirs, files in os.walk(root):
        if os.path.relpath(base, root).split(os.sep)[0] == ".git":
            continue
        dirs[:] = [d for d in dirs if not (base == root and d == ".git")]
        for name in dirs + files:
            p = os.path.join(base, name)
            st = os.lstat(p)
            rel = os.path.relpath(p, root)
            if stat.S_ISLNK(st.st_mode):
                snap[rel] = ("link", 0, os.readlink(p))
            elif stat.S_ISDIR(st.st_mode):
                snap[rel] = ("dir", stat.S_IMODE(st.st_mode), None)
            elif stat.S_ISREG(st.st_mode):
                with open(p, "rb") as fh:
                    snap[rel] = ("file", stat.S_IMODE(st.st_mode), fh.read())
            else:
                snap[rel] = ("special", stat.S_IMODE(st.st_mode), None)
    return snap


def _child(root, env, kill="none", seed=None, recover=True, umask=None):
    """Run run_init_sources in a FRESH interpreter; returns (exit status, result dict or None)."""
    import subprocess
    argv = [sys.executable, "-I", "-B", os.path.abspath(__file__), "--selftest-child", root, kill,
            seed or "-", "1" if recover else "0", "-" if umask is None else "{:o}".format(umask)]
    proc = subprocess.run(argv, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          timeout=300)
    out = proc.stdout.decode("utf-8", "replace").strip().splitlines()
    doc = None
    if out and out[-1].startswith("{"):
        doc = json.loads(out[-1])
    return proc.returncode, doc, proc.stderr.decode("utf-8", "replace")


def _child_main(argv):
    """The --selftest-child entry: install the named kill hook, run once, print the result."""
    import signal
    root, kill, seed, recover, umask = argv
    if umask != "-":
        os.umask(int(umask, 8))
    this = sys.modules[__name__]

    def die():
        sys.stdout.flush()
        os.kill(os.getpid(), signal.SIGKILL)

    counter = {"n": 0}
    name, _sep, arg = kill.partition(":")
    if name in ("dirs-intent", "sources-intent", "sources-complete", "dirs-complete"):
        real_publish = _journal.publish
        group, what = name.split("-")
        ftype = _journal.F_INTENT if what == "intent" else _journal.F_COMPLETE

        def publish(jr_fd, txn, t, obj):
            real_publish(jr_fd, txn, t, obj)
            if t == ftype and txn.endswith("-" + group):
                die()
        _journal.publish = publish
    elif name == "dirs-applied":
        real_dirs = this._apply_dirs

        def apply_dirs(*a):
            out = real_dirs(*a)
            die()
            return out
        this._apply_dirs = apply_dirs
    elif name in ("attached", "detached"):
        target = "attach_init_lease" if name == "attached" else "detach_init_lease"
        real = getattr(_opf_oplock, target)

        def wrapped(*a):
            out = real(*a)
            die()
            return out
        setattr(_opf_oplock, target, wrapped)
    elif name == "source":
        real_stage = this._stage_and_publish

        def stage(*a):
            out = real_stage(*a)
            counter["n"] += 1
            if counter["n"] == int(arg):
                die()
            return out
        this._stage_and_publish = stage
    elif name in ("prelink", "postlink", "planlink"):
        real_link = os.link

        def link(src, dst, *a, **k):
            is_plan = dst == _opf_init_substrate.PLAN_NAME
            if name == "planlink":
                if is_plan:
                    die()
                return real_link(src, dst, *a, **k)
            if _STAGE_MARKER not in src:
                return real_link(src, dst, *a, **k)
            counter["n"] += 1
            if counter["n"] == int(arg) and name == "prelink":
                die()
            real_link(src, dst, *a, **k)
            if counter["n"] == int(arg) and name == "postlink":
                die()
        os.link = link
    elif name == "ready":
        real_record = _opf_init_substrate.record_phase

        def record(sub, writer, phase):
            out = real_record(sub, writer, phase)
            if phase == PHASES[-1]:
                die()
            return out
        _opf_init_substrate.record_phase = record
    elif name == "noreseed":
        def refuse(*_a, **_k):
            raise AssertionError("the ancestral history was read again on resume")
        this.read_ancestral_counter_seed = refuse
    elif name == "norecover":
        def refuse(*_a, **_k):
            raise AssertionError("a rollback path was invoked on an init journal")
        _journal.recover = _journal._restore_preimage = _journal.reconcile_and_claim_stale = refuse
    elif name != "none":
        raise SystemExit("unknown kill point {!r}".format(kill))
    res = run_init_sources(root, ancestral=None if seed == "-" else seed, recover=recover == "1")
    print(json.dumps({s: getattr(res, s) for s in InitResult.__slots__}, sort_keys=True))
    return 0


def _ops_dir(root):
    return os.path.join(root, ".git", _opf_init_substrate.SUBSTRATE_DIRNAME,
                        _opf_init_substrate.OPS_DIRNAME)


def _read_plan(root):
    ops = sorted(os.listdir(_ops_dir(root)))
    with open(os.path.join(_ops_dir(root), ops[0], _opf_init_substrate.PLAN_NAME), "rb") as fh:
        return ops, fh.read()


def _run_self_test():
    import signal
    import tempfile
    import traceback

    if shutil.which("git") is None:
        print("REFUSED: git binary not found; the init operation self-test requires real git stores "
              "(fail-closed, non-zero)")
        return 2
    if not _containment.probe():
        print("REFUSED: race-free containment primitive absent (fail-closed, non-zero)")
        return 2

    checks = []

    def ok(label, condition, detail=""):
        checks.append((label, bool(condition), detail))

    def refuses(label, thunk, needle=None):
        try:
            thunk()
        except InitOperationError as exc:
            ok(label, needle is None or needle in str(exc),
               "message {!r} lacks {!r}".format(str(exc), needle) if needle else "")
            return
        except Exception as exc:  # a refusal must be the DECLARED type, never a raw error
            ok(label, False, "raised {} not InitOperationError: {}".format(type(exc).__name__, exc))
            return
        ok(label, False, "did not refuse")

    # --- pure: source-digest basis (init.toml excluded) -------------------------------------------
    payloads = _mk_payloads()
    source_set, source_digest = compute_bootstrap_source_digest(payloads)
    ok("D-roster-excludes-init", PROVENANCE_RELPATH not in source_set)
    ok("D-roster-sorted-unique", source_set == sorted(set(source_set)) == sorted(source_set))
    ok("D-digest-grammar", bool(_opf_init_contract._DIGEST_RE.match(source_digest)))
    refuses("D-init-excluded",
            lambda: compute_bootstrap_source_digest(_mk_payloads(
                extra={PROVENANCE_RELPATH: b"forged"})), needle="init.toml")
    changed = dict(payloads)
    changed[".opf.toml"] = b"different\n"
    ok("D-digest-content-bound", compute_bootstrap_source_digest(changed)[1] != source_digest)
    refuses("D-nonbytes-payload",
            lambda: compute_bootstrap_source_digest({".opf.toml": "str"}), needle="bytes")
    refuses("D-badpath-payload",
            lambda: compute_bootstrap_source_digest({"../escape": b"x"}), needle="source path")
    # Pinned independent fixture for the digest basis (sha256 over canonical JSON of the sorted
    # [path, digest] roster): computed by hand here, never through the function under test.
    pairs = [[p, "sha256:" + hashlib.sha256(payloads[p]).hexdigest()] for p in sorted(payloads)]
    manual = "sha256:" + hashlib.sha256((json.dumps(pairs, separators=(",", ":"))
                                         + "\n").encode("ascii")).hexdigest()
    ok("D-basis-independent-fixture", manual == source_digest)

    # --- pure: provenance build + validate --------------------------------------------------------
    text = build_bootstrap_provenance(_mk_basis(), payloads)
    v = validate_bootstrap_provenance(text.encode("utf-8"))
    ok("P-build-validates", v.status == VALID, str(v.findings))
    ok("P-build-roundtrip", build_bootstrap_provenance(_mk_basis(), payloads) == text)
    ok("P-is-toml", tomllib.loads(text)["format"] == PROVENANCE_FORMAT)
    ok("P-init-not-in-source-set", PROVENANCE_RELPATH not in v.model["source_set"])
    ok("P-stale-op-id", validate_bootstrap_provenance(text.encode("utf-8"), expected_basis=_mk_basis(
        operation_id="ffffffff-ffff-ffff-ffff-ffffffffffff")).status == INVALID)
    ok("P-stale-source-digest", validate_bootstrap_provenance(
        text.encode("utf-8"), expected_source_digest="sha256:" + "9" * 64).status == INVALID)
    ok("P-matches-live", validate_bootstrap_provenance(
        text.encode("utf-8"), expected_basis=_mk_basis(), expected_source_digest=source_digest,
        expected_source_set=source_set).status == VALID)
    ok("P-not-bytes", validate_bootstrap_provenance("str").status == INVALID)
    ok("P-not-toml", validate_bootstrap_provenance(b"= = =").status == INVALID)
    good = tomllib.loads(text)
    ok("P-bad-schema", validate_bootstrap_provenance(
        _opf_emit.emit_checked(dict(good, schema=2)).encode("utf-8")).status == INVALID)
    with_init = json.loads(json.dumps(good))
    with_init["source_set"] = sorted(with_init["source_set"] + [PROVENANCE_RELPATH])
    ok("P-source-set-has-init", validate_bootstrap_provenance(
        _opf_emit.emit_checked(with_init).encode("utf-8")).status == INVALID)
    refuses("P-build-bad-basis",
            lambda: build_bootstrap_provenance(_mk_basis(first_adoption="yes"), payloads),
            needle="first_adoption")

    # --- pure: the plan (decision 5) ---------------------------------------------------------------
    plan, raw = _mk_plan()
    ok("L-plan-canonical", raw == _opf_init_contract.canonical_json_bytes(plan))
    ok("L-plan-repeat-bytes", _mk_plan()[1] == raw)
    ok("L-plan-digest-self-excluding", plan["plan_digest"] == compute_plan_digest(plan))
    body = dict(plan)
    del body["plan_digest"]
    ok("L-plan-digest-independent", plan["plan_digest"] == "sha256:" + hashlib.sha256(
        (json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
         + "\n").encode("ascii")).hexdigest())
    ok("L-roster", [s["path"] for s in plan["sets"]["S"]][-3:] == [
        PROVENANCE_RELPATH, CHANGELOG_RELPATH, _opf_store.POINTER_REL])
    ok("L-staging-bound", all(s["staging"].endswith(plan["operation_id"].replace("-", ""))
                              for s in plan["sets"]["S"]))
    prov = plan_payloads(plan)[PROVENANCE_RELPATH]
    others = {p: b for p, b in plan_payloads(plan).items() if p != PROVENANCE_RELPATH}
    ok("L-provenance-excludes-itself", tomllib.loads(prov.decode())["source_digest"]
       == compute_bootstrap_source_digest(others)[1])
    ok("L-changelog-existing-in-E", _mk_plan(existing_changelog={
        "path": CHANGELOG_RELPATH, "mode": 0o644, "size": 3, "digest": "sha256:" + "1" * 64})[0][
        "sets"]["E"][0]["path"] == CHANGELOG_RELPATH)

    def tampered(mutate):
        doc = json.loads(raw)
        mutate(doc)
        if "plan_digest" in doc:
            doc["plan_digest"] = compute_plan_digest(doc)
        return _opf_init_contract.canonical_json_bytes(doc)

    def s_entry(doc, path):
        return [s for s in doc["sets"]["S"] if s["path"] == path][0]

    def set_payload(doc, path, data):
        e = s_entry(doc, path)
        e["payload"] = base64.b64encode(data).decode("ascii")
        e["size"] = len(data)
        e["digest"] = _opf_init_contract._digest(data)

    for label, mutate, needle in (
            ("generator", lambda d: d["versions"].update(generator="other/1"), "versions"),
            ("spec-version", lambda d: d["versions"].update(spec_version="1.0.0"), "versions"),
            ("views-nonempty", lambda d: d["sets"].update(V=[{"path": "x"}]), "V and K"),
            ("staging-set", lambda d: d.update(staging_set=["x"]), "staging_set"),
            ("bad-staging", lambda d: d["sets"]["S"][0].update(staging=".x"), "staging name"),
            ("bad-mode", lambda d: d["sets"]["S"][0].update(mode=0o600), "mode"),
            ("digest-mismatch", lambda d: d["sets"]["S"][0].update(digest="sha256:" + "0" * 64),
             "digest"),
            ("order", lambda d: d["sets"]["S"].reverse(), "order"),
            ("dropped-source", lambda d: d["sets"]["S"].pop(0), "roster"),
            ("forged-manifest", lambda d: set_payload(d, "{}/manifest.toml".format(_MACHINE_HOME),
                                                      b"[opf]\n"), "exact bytes"),
            ("forged-provenance", lambda d: set_payload(d, PROVENANCE_RELPATH, b"x = 1\n"),
             "provenance"),
            ("zero-basis-readoption", lambda d: d.update(first_adoption=False), "first adoption"),
            ("changelog-both", lambda d: d["sets"].update(E=[{
                "path": CHANGELOG_RELPATH, "mode": 0o644, "size": 1,
                "digest": "sha256:" + "1" * 64}]), "roster"),
            ("bad-time", lambda d: d.update(application_time="2026-13-40T00:00:00Z"),
             "application_time"),
            ("acceptance", lambda d: d.update(acceptance={"present": True}), "acceptance"),
            ("dirs", lambda d: d["permitted_directories"].pop(), "permitted_directories"),
            ("control", lambda d: d["sets"].update(C=[]), "C must")):
        refuses("L-tamper-" + label, lambda m=mutate: validate_init_plan(tampered(m)), needle)
    refuses("L-tamper-plan-digest", lambda: validate_init_plan(
        _opf_init_contract.canonical_json_bytes(dict(json.loads(raw),
                                                     plan_digest="sha256:" + "2" * 64))),
            needle="plan_digest")
    refuses("L-noncanonical", lambda: validate_init_plan(raw + b"\n"), needle="canonical")
    refuses("L-expected-head", lambda: validate_init_plan(raw, expected_head=dict(
        _mk_head(), oid="b" * 40)), needle="HEAD")
    refuses("L-expected-binding", lambda: validate_init_plan(raw, expected_binding=dict(
        _mk_binding(), product_prefix="x")), needle="binding")
    ok("L-validates", validate_init_plan(raw, expected_binding=_mk_binding(),
                                         expected_head=_mk_head())["plan_digest"]
       == plan["plan_digest"])

    base = os.path.realpath(tempfile.mkdtemp(prefix="opf-init-op-selftest-"))
    env = _st_git_env(base)
    saved = {}
    for k in ("HOME", "XDG_CONFIG_HOME", "GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM",
              "GIT_CONFIG_NOSYSTEM"):
        saved[k] = os.environ.get(k)
        os.environ[k] = env[k]
    tests_run = []
    try:
        _b6_tests(base, env, ok, refuses)
        tests_run.append("b6")
        _physical_tests(base, env, ok, signal)
        tests_run.append("physical")
        import check_opf_init_qa
        ok("PR3a-QA-regressions", check_opf_init_qa.self_test() == 0)
    except Exception:
        ok("self-test-harness", False, traceback.format_exc())
    finally:
        for k, val in saved.items():
            if val is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = val
        shutil.rmtree(base, ignore_errors=True)

    failed = [(lbl, why) for (lbl, good_, why) in checks if not good_]
    for lbl, why in failed:
        sys.stderr.write("SELF-TEST FAIL {}: {}\n".format(lbl, why))
    print("filesystem under test: {} (TMPDIR={})".format(base, os.environ.get("TMPDIR", "")))
    if failed or tests_run != ["b6", "physical"]:
        sys.stderr.write("_opf_init_operation SELF-TEST: FAIL ({} of {})\n".format(
            len(failed), len(checks)))
        return 1
    print("_opf_init_operation SELF-TEST: PASS ({} checks)".format(len(checks)))
    return 0



def _b6_tests(base, env, ok, refuses):
    """B6 (decision 6) over real git stores: the pinned snapshot's high-waters, its unknown and
    module-tier namespaces, first-parent membership, and every refusal (never an older candidate,
    never a zero, never a maximum over history)."""
    import subprocess
    values = {ns: 0 for ns in _opf_store.BASELINE_TYPES.values()}
    values.update({"WL": 7, "BI": 3, "FN": 2, "LF": 5})
    repo, head, body = _seed_repo(os.path.join(base, "r1"), env, values)
    seed = read_ancestral_counter_seed(repo, pinned_head=head, evidence_commit=head, prefix="",
                                       object_format="sha1")
    ok("B1-nonzero-counters", high_water(seed.counters, "WL") == 7
       and high_water(seed.counters, "BI") == 3 and seed.unknown == ())
    ok("B1-content-digest", seed.evidence["content_digest"]
       == "sha256:" + hashlib.sha256(body).hexdigest())
    counters = tomllib.loads(_opf_init.build_counters(seed=seed.counters))
    ok("B2-wl-next-allocation", high_water(counters["counters"], "WL") + 1 == 8)
    # B3: a snapshot that predates LF: LF is UNKNOWN (never zero) and the plan refuses it.
    pre = {ns: 1 for ns in _opf_store.BASELINE_TYPES.values()}
    r3, h3, _b = _seed_repo(os.path.join(base, "r3"), env, pre)
    s3 = read_ancestral_counter_seed(r3, pinned_head=h3, evidence_commit=h3, prefix="",
                                     object_format="sha1")
    ok("B3-missing-is-unknown", s3.unknown == ("LF",) and "LF" not in s3.counters)
    refuses("B3-unknown-refuses-plan", lambda: _mk_plan(seed=s3), needle="UNKNOWN")
    refuses("B3-unknown-refuses-builder", lambda: build_source_payloads(
        operation_id="12345678-1234-4234-8234-1234567890ab", binding=_mk_binding(),
        head=_mk_head(), first_adoption=False, inventory_digest_value="sha256:" + "0" * 64,
        create_changelog=True, seed=s3), needle="never zero")
    # B4: module-tier namespaces: a zero is carried as evidence; a nonzero refuses (it would be lost).
    r4, h4, _b = _seed_repo(os.path.join(base, "r4"), env, dict(values, MA=0))
    s4 = read_ancestral_counter_seed(r4, pinned_head=h4, evidence_commit=h4, prefix="",
                                     object_format="sha1")
    ok("B4-module-zero-carried", s4.module_counters == {"MA": 0} and s4.unknown == ())
    ok("B4-module-zero-plans", _mk_plan(seed=s4)[0]["first_adoption"] is False)
    r4b, h4b, _b = _seed_repo(os.path.join(base, "r4b"), env, dict(values, MA=4))
    s4b = read_ancestral_counter_seed(r4b, pinned_head=h4b, evidence_commit=h4b, prefix="",
                                      object_format="sha1")
    refuses("B4-module-nonzero-refuses", lambda: _mk_plan(seed=s4b), needle="module-tier")
    # B5: malformed values and blobs refuse.
    for label, blob, needle in (
            ("bool", _counters_toml(values).replace(b"WL = 7", b"WL = true"), "malformed"),
            ("negative", _counters_toml(values).replace(b"WL = 7", b"WL = -1"), "malformed"),
            ("oversize", _counters_toml(values).replace(b"WL = 7", b"WL = 99999999999999999999"),
             "64-bit"),
            ("foreign-ns", _counters_toml(dict(values, ZZ=1)), "malformed"),
            ("not-toml", b"not = = toml", "TOML")):
        rb = os.path.join(base, "r5-" + label)
        os.makedirs(rb)
        _git(["init", "-q", "-b", "main", "."], rb, env)
        _write(os.path.join(rb, ".working", "toml", "counters.toml"), blob)
        _git(["add", "-A"], rb, env)
        _git(["commit", "-q", "-m", "bad"], rb, env)
        hb = _head(rb, env)
        refuses("B5-" + label, lambda r=rb, h=hb: read_ancestral_counter_seed(
            r, pinned_head=h, evidence_commit=h, prefix="", object_format="sha1"), needle=needle)
    # B6: a missing blob, an absent object, a changed prefix, a sibling commit all refuse.
    r6 = _plain_repo(os.path.join(base, "r6"), env)
    h6 = _head(r6, env)
    refuses("B6-missing-blob", lambda: read_ancestral_counter_seed(
        r6, pinned_head=h6, evidence_commit=h6, prefix="", object_format="sha1"),
        needle="does not resolve")
    refuses("B6-absent-object", lambda: read_ancestral_counter_seed(
        repo, pinned_head=head, evidence_commit="0" * 40, prefix="", object_format="sha1"),
        needle="does not resolve")
    refuses("B6-changed-prefix", lambda: read_ancestral_counter_seed(
        repo, pinned_head=head, evidence_commit=head, prefix="sub", object_format="sha1"),
        needle="does not resolve")
    r7, h7, _b = _seed_repo(os.path.join(base, "r7"), env, values, prefix="sub")
    s7 = read_ancestral_counter_seed(r7, pinned_head=h7, evidence_commit=h7, prefix="sub",
                                     object_format="sha1")
    ok("B7-nested-prefix", s7.evidence["path"] == "sub/.working/toml/counters.toml")
    # B8: the FIRST-PARENT rule. A side-branch commit merged in (reachable only through the merge's
    # second parent) refuses, although it IS an ancestor; the main-line commit before the merge is
    # accepted. A plain ancestor check would accept both (the flip of this witness).
    r8, h8, _b = _seed_repo(os.path.join(base, "r8"), env, dict(values, WL=1))
    _git(["checkout", "-q", "-b", "side"], r8, env)
    _write(os.path.join(r8, ".working", "toml", "counters.toml"), _counters_toml(dict(values,
                                                                                      WL=50)))
    _git(["commit", "-q", "-am", "side"], r8, env)
    side = _head(r8, env)
    _git(["checkout", "-q", "main"], r8, env)
    _write(os.path.join(r8, "main.txt"), b"m\n")
    _git(["add", "-A"], r8, env)
    _git(["commit", "-q", "-m", "main"], r8, env)
    _git(["merge", "-q", "--no-ff", "-s", "ours", "-m", "merge", "side"], r8, env)
    merged = _head(r8, env)
    anc = _git(["merge-base", "--is-ancestor", side, merged], r8, env, allow_fail=True)
    ok("B8-side-is-ancestor", anc.returncode == 0)
    refuses("B8-second-parent-refused", lambda: read_ancestral_counter_seed(
        r8, pinned_head=merged, evidence_commit=side, prefix="", object_format="sha1"),
        needle="first-parent")
    ok("B8-first-parent-accepted", high_water(read_ancestral_counter_seed(
        r8, pinned_head=merged, evidence_commit=h8, prefix="", object_format="sha1").counters,
        "WL") == 1)
    # B9: replacement objects never substitute the read bytes.
    forged = subprocess.run(["git", "-C", repo, "hash-object", "-w", "--stdin"],
                            input=_counters_toml(dict(values, WL=999)), env=env,
                            stdout=subprocess.PIPE).stdout.decode().strip()
    real = _git(["rev-parse", "HEAD:.working/toml/counters.toml"], repo, env).stdout.decode().strip()
    _git(["replace", real, forged], repo, env, allow_fail=True)
    ok("B9-no-replace-objects", high_water(read_ancestral_counter_seed(
        repo, pinned_head=head, evidence_commit=head, prefix="",
        object_format="sha1").counters, "WL") == 7)
    # B10: a shallow clone whose history does not reach the evidence refuses.
    r10, h10a, _b = _seed_repo(os.path.join(base, "r10"), env, values)
    for n in range(3):
        _write(os.path.join(r10, "f{}".format(n)), b"x\n")
        _git(["add", "-A"], r10, env)
        _git(["commit", "-q", "-m", "c{}".format(n)], r10, env)
    shallow = os.path.join(base, "r10-shallow")
    _git(["clone", "-q", "--depth", "1", "file://" + r10, shallow], base, env)
    h10 = _head(shallow, env)
    refuses("B10-shallow-refuses", lambda: read_ancestral_counter_seed(
        shallow, pinned_head=h10, evidence_commit=h10a, prefix="", object_format="sha1"))
    refuses("B10-bad-oid", lambda: read_ancestral_counter_seed(
        repo, pinned_head="xyz", evidence_commit=head, prefix="", object_format="sha1"),
        needle="well-formed")


def _read_or_none(path):
    try:
        with open(path, "rb") as fh:
            return fh.read()
    except OSError:
        return None


def _worktree_without_lease(root):
    """The worktree snapshot minus the lease control record (which an explicit recover=True clears
    from a confirmed-dead attempt, a control effect, never an adoption file)."""
    snap = _tree_snapshot(root)
    snap.pop(LEASE_RELPATH, None)
    return snap


def _snapshot_all(root):
    """The worktree snapshot plus the index bytes (the git index must never change)."""
    with open(os.path.join(root, ".git", "index"), "rb") as fh:
        index = fh.read()
    return _tree_snapshot(root), index


def _physical_tests(base, env, ok, signal):
    """The real-filesystem tests (the filesystem is the one TMPDIR names): fresh bootstrap, exact
    modes under differing umasks, completed rerun, kill and fresh-process retry at every named
    boundary, conflict preservation, journal safety, changed HEAD, B6 end to end, and topology."""
    all_sources = None

    # R1 fresh bootstrap under two umasks: exact modes, SOURCES-READY, nothing staged in the index.
    for umask in (0o022, 0o077):
        root = _plain_repo(os.path.join(base, "fresh-{:o}".format(umask)), env)
        _snap, index_before = _snapshot_all(root)
        rc, res, err = _child(root, env, umask=umask)
        ok("R1-{:o}-ready".format(umask), rc == 0 and res and res["status"] == SOURCES_READY,
           "{} {} {}".format(rc, res, err[-800:]))
        if not res or res["status"] != SOURCES_READY:
            continue
        ops, raw = _read_plan(root)
        plan = validate_init_plan(raw)
        all_sources = [s["path"] for s in plan["sets"]["S"]]
        ok("R1-{:o}-created-all".format(umask), sorted(res["created"]) == sorted(
            all_sources + [_opf_store.WORKING_DIRNAME, _MACHINE_HOME]), str(res["created"]))
        modes = {p: stat.S_IMODE(os.lstat(os.path.join(root, p)).st_mode) for p in all_sources}
        ok("R1-{:o}-file-modes".format(umask), set(modes.values()) == {SOURCE_MODE}, str(modes))
        ok("R1-{:o}-dir-modes".format(umask), stat.S_IMODE(os.lstat(os.path.join(
            root, _MACHINE_HOME)).st_mode) == DIR_MODE)
        with open(os.path.join(root, ".git", "index"), "rb") as fh:
            ok("R1-{:o}-index-untouched".format(umask), fh.read() == index_before)
        ok("R1-{:o}-no-lease".format(umask), not os.path.exists(os.path.join(root, LEASE_RELPATH)))
        ok("R1-{:o}-phases".format(umask), res["phases"] == list(PHASES), str(res["phases"]))
        outcomes = _opf_init_substrate.read_outcomes(root, ops[0])
        ok("R1-{:o}-outcome".format(umask), len(outcomes) == 1
           and outcomes[0]["completed_phases"] == list(PHASES))
        res_store = _opf_store.resolve_store(root)
        ok("R1-{:o}-resolves".format(umask), res_store.status == _opf_store.RESOLVED)
        manifest_model = tomllib.loads(open(os.path.join(root, _MACHINE_HOME, "manifest.toml"),
                                            encoding="utf-8").read())
        contained = _opf_check.classify_containment(manifest_model, _MACHINE_HOME)
        ok("R1-{:o}-init-toml-contained".format(umask), contained.managed_file(PROVENANCE_RELPATH)
           and not contained.managed_file("{}/stray.toml".format(_MACHINE_HOME)))
        with open(os.path.join(root, CHANGELOG_RELPATH), "rb") as fh:
            ok("R1-{:o}-changelog-created".format(umask), fh.read() == _CHANGELOG_PAYLOAD)

    # R2 a completed rerun changes NOTHING in the worktree or the index; the lock is released.
    root = os.path.join(base, "fresh-22")
    before = _snapshot_all(root)
    rc, res, err = _child(root, env)
    ok("R2-already-initialized", res and res["status"] == ALREADY_INITIALIZED, str(res) + err[-400:])
    ok("R2-nothing-changed", _snapshot_all(root) == before)
    rc, res, _err = _child(root, env)
    ok("R2-repeatable", res and res["status"] == ALREADY_INITIALIZED)

    # R2b a completed adoption with LEGITIMATE later edits (a record and its counter) is health-
    # validated, never held to its plan's bytes (decision 5): ALREADY-INITIALIZED, the edit untouched.
    root = os.path.join(base, "fresh-77")
    counters_path = os.path.join(root, COUNTERS_RELPATH)
    edited = open(counters_path, "rb").read().replace(b"BI = 0", b"BI = 1")
    _write(counters_path, edited)
    record = {"id": "BI-1", "type": "backlog_item", "status": "open", "title": "later",
              "created_at": "2026-06-01T00:00:00Z", "updated_at": "2026-06-01T00:00:00Z",
              "actor": {"kind": "maintainer"}}
    _write(os.path.join(root, _MACHINE_HOME, "backlog_item.index.toml"),
           _opf_emit.emit_checked({"schema": 1, "record": [record]}).encode("utf-8"))
    before = _snapshot_all(root)
    rc, res, err = _child(root, env)
    ok("R2b-edited-completed-is-healthy", res and res["status"] == ALREADY_INITIALIZED
       and _snapshot_all(root) == before, str(res) + err[-400:])

    # R3 an existing CHANGELOG.md is preserved byte for byte, recorded in E, never created.
    root = _plain_repo(os.path.join(base, "changelog"), env)
    _write(os.path.join(root, CHANGELOG_RELPATH), b"# Mine\n\nkeep me\n")
    _git(["add", "-A"], root, env)
    _git(["commit", "-q", "-m", "cl"], root, env)
    rc, res, err = _child(root, env)
    with open(os.path.join(root, CHANGELOG_RELPATH), "rb") as fh:
        ok("R3-changelog-preserved", res and res["status"] == SOURCES_READY
           and fh.read() == b"# Mine\n\nkeep me\n" and CHANGELOG_RELPATH not in res["created"],
           str(res) + err[-400:])

    # R4 kill at every registered boundary, then retry in a FRESH process until complete: the same
    # operation id and plan bytes, byte-identical sources, no second allocation, correct created
    # versus verified reporting, and a repeated retry that is a no-op.
    n_sources = len(all_sources or []) or 19
    points = ["dirs-intent", "dirs-applied", "dirs-complete", "attached", "sources-intent",
              "source:1", "source:5", "source:{}".format(n_sources), "prelink:3", "postlink:3",
              "sources-complete", "detached", "ready"]
    for point in points:
        root = _plain_repo(os.path.join(base, "kill-" + point.replace(":", "-")), env)
        rc, res, err = _child(root, env, kill=point)
        ok("R4-{}-killed".format(point), rc == -signal.SIGKILL, "rc {} {}".format(rc, err[-600:]))
        if point == "dirs-intent":
            ok("R4-dirs-intent-no-worktree-write", not os.path.lexists(os.path.join(
                root, _opf_store.WORKING_DIRNAME)))
        ops, raw = _read_plan(root)
        present = set(p for p in (all_sources or []) if os.path.lexists(os.path.join(root, p)))
        rc, res, err = _child(root, env)
        want = ALREADY_INITIALIZED if point == "ready" else SOURCES_READY
        ok("R4-{}-retry".format(point), res and res["status"] == want,
           "{} {}".format(res, err[-800:]))
        if not res:
            continue
        ops2, raw2 = _read_plan(root)
        ok("R4-{}-same-operation".format(point), ops2 == ops and raw2 == raw
           and res["operation_id"] == ops[0], "{} {}".format(ops, ops2))
        plan = validate_init_plan(raw2)
        payload = plan_payloads(plan)
        exact = all(_read_or_none(os.path.join(root, p)) == b for p, b in payload.items())
        ok("R4-{}-exact-sources".format(point), exact)
        if want == SOURCES_READY:
            ok("R4-{}-no-recreation".format(point), not (set(res["created"]) & present),
               "created {} present-before {}".format(res["created"], sorted(present)))
            ok("R4-{}-all-accounted".format(point), set(res["created"]) | set(res["deduplicated"])
               >= set(payload), str(res))
        rc, res2, _err = _child(root, env)
        ok("R4-{}-rerun-noop".format(point), res2 and res2["status"] == ALREADY_INITIALIZED)
        ok("R4-{}-single-operation".format(point), len(os.listdir(_ops_dir(root))) == 1)

    # R5 conflict preservation on RESUME: a planted object at a not-yet-created destination is
    # refused and preserved, never repaired or overwritten; nothing else changes.
    target = "{}/version.toml".format(_MACHINE_HOME)
    for label in ("wrong-bytes", "strict-prefix", "wrong-mode", "symlink", "dangling", "hardlink",
                  "fifo", "directory"):
        root = _plain_repo(os.path.join(base, "conflict-" + label), env)
        rc, _res, _err = _child(root, env, kill="source:1")
        ops, raw = _read_plan(root)
        plan = validate_init_plan(raw)
        data = plan_payloads(plan)[target]
        dest = os.path.join(root, target)
        ok("R5-{}-setup".format(label), not os.path.lexists(dest))
        if label == "wrong-bytes":
            _write(dest, b"x" + data)
            os.chmod(dest, 0o644)
        elif label == "strict-prefix":
            _write(dest, data[:len(data) // 2])
            os.chmod(dest, 0o644)
        elif label == "wrong-mode":
            _write(dest, data)
            os.chmod(dest, 0o600)
        elif label == "symlink":
            _write(os.path.join(root, "elsewhere"), data)
            os.symlink(os.path.join(root, "elsewhere"), dest)
        elif label == "dangling":
            os.symlink(os.path.join(root, "nowhere"), dest)
        elif label == "hardlink":
            _write(os.path.join(root, "other"), data)
            os.chmod(os.path.join(root, "other"), 0o644)
            os.link(os.path.join(root, "other"), dest)
        elif label == "fifo":
            os.mkfifo(dest)
        else:
            os.mkdir(dest)
        before = _worktree_without_lease(root)
        rc, res, err = _child(root, env)
        ok("R5-{}-refused".format(label), res and res["status"] == REFUSED
           and "preserved" in (res["primary_failure"] or {}).get("detail", ""),
           "{} {}".format(res, err[-400:]))
        after = _worktree_without_lease(root)
        ok("R5-{}-preserved".format(label), after.get(target) == before.get(target)
           and after == before, "diff {}".format(sorted(set(after.items()) ^ set(before.items()),
                                                        key=str)[:4]))

    # R6 fresh-adoption refusals: a pre-existing destination (identical bytes included, the F14
    # collision rule), foreign .working content, and a tracked-but-deleted destination.
    root = _plain_repo(os.path.join(base, "collide"), env)
    _write(os.path.join(root, ".opf.toml"), _opf_emit.emit_checked(
        {"store": {"target": "dir:."}}).encode("utf-8"))
    before = _tree_snapshot(root)
    rc, res, _err = _child(root, env)
    ok("R6-identical-collision-refused", res and res["status"] == REFUSED
       and _tree_snapshot(root) == before, str(res))
    root = _plain_repo(os.path.join(base, "foreign"), env)
    _write(os.path.join(root, ".working", "notes.md"), b"mine\n")
    before = _tree_snapshot(root)
    rc, res, _err = _child(root, env)
    ok("R6-foreign-working-refused", res and res["status"] == REFUSED
       and _tree_snapshot(root) == before, str(res))
    root = _plain_repo(os.path.join(base, "tracked"), env)
    _write(os.path.join(root, "CHANGELOG.md"), b"x\n")
    _git(["add", "-A"], root, env)
    _git(["commit", "-q", "-m", "cl"], root, env)
    os.unlink(os.path.join(root, "CHANGELOG.md"))
    rc, res, _err = _child(root, env)
    ok("R6-tracked-deletion-refused", res and res["status"] == REFUSED
       and "tracked" in res["primary_failure"]["detail"], str(res))

    # R7 journal safety: a torn journal tail is truncated and resumed; a rollback frame is never
    # accepted (preserved, CANNOT-EVALUATE); and no rollback path is ever invoked for init.
    root = _plain_repo(os.path.join(base, "torn"), env)
    rc, _res, _err = _child(root, env, kill="dirs-complete")
    ops, _raw = _read_plan(root)
    jdir = os.path.join(root, ".git", _opf_init_substrate.SUBSTRATE_DIRNAME,
                        _opf_init_substrate.JOURNALS_DIRNAME, _txn_name(ops[0], "sources"))
    os.makedirs(jdir, exist_ok=True)
    with open(os.path.join(jdir, "frames.log"), "ab") as fh:
        fh.write(_journal.MAGIC + b" INTENT 999 " + b"0" * 64 + b"\n{\"txn\"")
    rc, res, err = _child(root, env, kill="norecover")
    ok("R7-torn-tail-resumed", res and res["status"] == SOURCES_READY
       and any("torn tail" in n for n in res["notes"]), "{} {}".format(res, err[-400:]))
    root = _plain_repo(os.path.join(base, "rollback-frame"), env)
    rc, _res, _err = _child(root, env, kill="sources-intent")
    ops, _raw = _read_plan(root)
    jr = os.path.join(root, ".git", _opf_init_substrate.SUBSTRATE_DIRNAME,
                      _opf_init_substrate.JOURNALS_DIRNAME)
    jfd = os.open(jr, os.O_RDONLY | os.O_DIRECTORY)
    try:
        _journal.publish(jfd, _txn_name(ops[0], "sources"), _journal.F_RIP,
                         {"txn": _txn_name(ops[0], "sources")})
    finally:
        os.close(jfd)
    before = _worktree_without_lease(root)
    rc, res, _err = _child(root, env, kill="norecover")
    ok("R7-rollback-frame-refused", res and res["status"] == CANNOT_EVALUATE
       and _worktree_without_lease(root) == before, str(res))

    # R8 a kill during the PLAN's publication leaves an empty operation directory that the retry
    # discards (reported) before a fresh operation proceeds.
    root = _plain_repo(os.path.join(base, "planlink"), env)
    rc, _res, _err = _child(root, env, kill="planlink")
    ok("R8-killed", rc == -signal.SIGKILL)
    ok("R8-no-worktree-write-before-plan", not os.path.lexists(os.path.join(
        root, _opf_store.WORKING_DIRNAME)) and not os.path.lexists(os.path.join(
            root, _opf_store.POINTER_REL)))
    leftover = os.listdir(_ops_dir(root))
    rc, res, err = _child(root, env)
    ok("R8-discarded-and-ready", res and res["status"] == SOURCES_READY
       and any("discarded an empty operation" in n for n in res["notes"])
       and res["operation_id"] not in leftover, "{} {}".format(res, err[-400:]))

    # R9 a HEAD that moves between the crash and the retry refuses the resume (preserved).
    root = _plain_repo(os.path.join(base, "moved-head"), env)
    rc, _res, _err = _child(root, env, kill="source:2")
    _write(os.path.join(root, "later.txt"), b"l\n")
    _git(["add", "later.txt"], root, env)
    _git(["commit", "-q", "-m", "later"], root, env)
    before = _worktree_without_lease(root)
    rc, res, _err = _child(root, env)
    ok("R9-moved-head-refused", res and res["status"] == REFUSED and "HEAD" in
       res["primary_failure"]["detail"] and _worktree_without_lease(root) == before, str(res))

    # R10 B6 end to end: a committed adoption later deleted in a commit; re-adoption seeds the
    # counters from the pinned snapshot (WL next allocation 8), records the evidence in the plan,
    # and a retry after a kill never reads the history again.
    root = _plain_repo(os.path.join(base, "readopt"), env)
    values = {ns: 0 for ns in _opf_store.BASELINE_TYPES.values()}
    values.update({"WL": 7, "BI": 3, "LF": 2})
    _write(os.path.join(root, ".working", "toml", "counters.toml"), _counters_toml(values))
    _git(["add", "-A"], root, env)
    _git(["commit", "-q", "-m", "adopted"], root, env)
    evidence = _head(root, env)
    _git(["rm", "-q", "-r", ".working"], root, env)
    _git(["commit", "-q", "-m", "deleted"], root, env)
    rc, _res, _err = _child(root, env, kill="source:3", seed=evidence)
    rc, res, err = _child(root, env, kill="noreseed")
    ok("R10-readopted", res and res["status"] == SOURCES_READY, "{} {}".format(res, err[-600:]))
    with open(os.path.join(root, COUNTERS_RELPATH), "rb") as fh:
        c = tomllib.loads(fh.read().decode())["counters"]
    ok("R10-wl-next-is-8", high_water(c, "WL") + 1 == 8 and c["BI"] == 3 and c["LF"] == 2)
    _ops, raw = _read_plan(root)
    plan = validate_init_plan(raw)
    basis = [s for s in plan["sets"]["S"] if s["path"] == COUNTERS_RELPATH][0]["basis"]
    ok("R10-evidence-recorded", basis["kind"] == "ancestral" and basis["commit"] == evidence
       and plan["first_adoption"] is False)

    # R11 topology: a nested product prefix and a linked worktree both bind correctly; a symlinked
    # product root and a concurrent holder refuse.
    # A NESTED product prefix (no .git at the product root) is REFUSED, explicitly: the lock module
    # roots a .git-less store's control tree at the store root, so an init there could not share the
    # anchor a later acquire_operation on the resolved store would take (disclosed; the binding
    # itself observes the prefix, witnessed through observe_binding).
    repo = _plain_repo(os.path.join(base, "nested"), env)
    sub = os.path.join(repo, "prod")
    os.mkdir(sub)
    rc, res, err = _child(sub, env)
    ok("R11-nested-prefix-refused", res and res["status"] == REFUSED
       and "no .git entry" in res["primary_failure"]["detail"]
       and os.listdir(sub) == [], str(res) + err[-400:])
    ok("R11-nested-binding-observed", observe_binding(sub)[0]["product_prefix"] == "prod")
    main = _plain_repo(os.path.join(base, "wt-main"), env)
    wt = os.path.join(base, "wt-linked")
    _git(["worktree", "add", "-q", "--detach", wt], main, env)
    rc, res, err = _child(wt, env)
    ok("R11-linked-worktree", res and res["status"] == SOURCES_READY
       and os.path.isdir(_ops_dir(main)) and not os.path.exists(os.path.join(wt, ".git",
                                                                                "opf-init")),
       str(res) + err[-400:])
    plain_dir = os.path.join(base, "not-a-repo")
    os.mkdir(plain_dir)
    rc, res, _err = _child(plain_dir, env)
    ok("R11-nonrepository-refused", res and res["status"] in (REFUSED, CANNOT_EVALUATE)
       and os.listdir(plain_dir) == [], str(res))
    bare = os.path.join(base, "bare.git")
    _git(["init", "-q", "--bare", bare], base, env)
    rc, res, _err = _child(bare, env)
    ok("R11-bare-refused", res and res["status"] in (REFUSED, CANNOT_EVALUATE)
       and not os.path.exists(os.path.join(bare, _opf_store.WORKING_DIRNAME)), str(res))
    root = _plain_repo(os.path.join(base, "local-pointer"), env)
    _write(os.path.join(root, _opf_store.LOCAL_POINTER_REL), b'[store]\ntarget = "dir:."\n')
    before = _tree_snapshot(root)
    rc, res, _err = _child(root, env)
    ok("R11-local-pointer-refused", res and res["status"] == REFUSED
       and _tree_snapshot(root) == before, str(res))
    link = os.path.join(base, "symlinked-root")
    os.symlink(_plain_repo(os.path.join(base, "real-root"), env), link)
    rc, res, _err = _child(link, env)
    ok("R11-symlinked-root-refused", res and res["status"] in (REFUSED, CANNOT_EVALUATE), str(res))
    # R12 a failed fsync (in process, EIO injected) during source publication is FAILED, preserves
    # everything, never reaches a rollback path, and a fresh-process retry completes the SAME operation.
    for which in ("file", "parent"):
        root = _plain_repo(os.path.join(base, "fsync-" + which), env)
        real_fsync = os.fsync
        real_publish = _journal.publish
        state = {"armed": False, "fired": False}

        def publish_arm(jr_fd, txn, t, obj):
            real_publish(jr_fd, txn, t, obj)
            if t == _journal.F_INTENT and txn.endswith("-sources"):
                state["armed"] = True

        def fsync_fail(fd, which=which):
            if state["armed"] and not state["fired"]:
                is_dir = stat.S_ISDIR(os.fstat(fd).st_mode)
                if is_dir == (which == "parent"):
                    state["fired"] = True
                    raise OSError(5, "injected EIO")
            return real_fsync(fd)

        def refuse(*_a, **_k):
            raise AssertionError("a rollback path was invoked on an init journal")
        saved = (_journal.recover, _journal._restore_preimage, _journal.reconcile_and_claim_stale)
        os.fsync = fsync_fail
        _journal.publish = publish_arm
        _journal.recover = _journal._restore_preimage = _journal.reconcile_and_claim_stale = refuse
        try:
            res = run_init_sources(root)
        finally:
            os.fsync = real_fsync
            _journal.publish = real_publish
            _journal.recover, _journal._restore_preimage, _journal.reconcile_and_claim_stale = saved
        ok("R12-{}-fsync-failed".format(which), state["fired"] and res.status == FAILED
           and "EIO" in res.primary_failure["detail"], "{} {}".format(res.status,
                                                                      res.primary_failure))
        ok("R12-{}-released".format(which), not os.path.exists(os.path.join(root, LEASE_RELPATH))
           and not res.finalization_failures, str(res.finalization_failures))
        ops, _raw = _read_plan(root)
        rc, res2, err = _child(root, env)
        ok("R12-{}-retry-completes".format(which), res2 and res2["status"] == SOURCES_READY
           and res2["operation_id"] == ops[0], "{} {}".format(res2, err[-400:]))

    # R13 a destination REPLACED between its classification stat and its open is never trusted: the
    # identity check refuses it CANNOT-EVALUATE (a same-bytes replacement included).
    root = _plain_repo(os.path.join(base, "swap"), env)
    rc, _res, _err = _child(root, env, kill="source:2")
    ops, raw = _read_plan(root)
    plan = validate_init_plan(raw)
    entry = plan["sets"]["S"][0]
    data = plan_payloads(plan)[entry["path"]]
    this = sys.modules[__name__]
    real_read = this._read_exact

    def swapping_read(pfd, name, label, size):
        os.unlink(name, dir_fd=pfd)
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644, dir_fd=pfd)
        os.write(fd, data)
        os.fchmod(fd, 0o644)
        os.close(fd)
        return real_read(pfd, name, label, size)
    this._read_exact = swapping_read
    try:
        rfd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
        pfd, name = _journal._open_parent(rfd, entry["path"])
        try:
            _classify_dest(pfd, name, entry, data)
            ok("R13-replaced-refused", False, "a replaced destination was trusted")
        except InitOperationError as exc:
            ok("R13-replaced-refused", exc.code == CANNOT_EVALUATE and "replaced" in str(exc),
               str(exc))
        finally:
            os.close(pfd)
            os.close(rfd)
    finally:
        this._read_exact = real_read

    # R15 publication never replaces and never publishes unverified bytes: _stage_and_publish over an
    # existing destination refuses and preserves it (link, never rename or overwrite), and a staged
    # file whose read-back differs from the payload (a doubled write) is never linked.
    root = _plain_repo(os.path.join(base, "publish-unit"), env)
    plan, _raw = _mk_plan()
    entry = [e for e in plan["sets"]["S"] if e["path"] == CHANGELOG_RELPATH][0]
    data = plan_payloads(plan)[CHANGELOG_RELPATH]
    rfd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        _write(os.path.join(root, CHANGELOG_RELPATH), b"theirs\n")
        try:
            _stage_and_publish(rfd, CHANGELOG_RELPATH, entry, data)
            ok("R15-no-replace", False, "an existing destination was replaced")
        except InitOperationError as exc:
            ok("R15-no-replace", open(os.path.join(root, CHANGELOG_RELPATH), "rb").read()
               == b"theirs\n" and not os.path.exists(os.path.join(root, entry["staging"])),
               str(exc))
        os.unlink(os.path.join(root, CHANGELOG_RELPATH))
        real_write = _journal._write_all

        def doubled(fd, payload):
            real_write(fd, payload)
            real_write(fd, payload)
        _journal._write_all = doubled
        try:
            _stage_and_publish(rfd, CHANGELOG_RELPATH, entry, data)
            ok("R15-readback", False, "a doubled staged write was published")
        except InitOperationError as exc:
            ok("R15-readback", exc.code == FAILED and "staged" in str(exc)
               and not os.path.lexists(os.path.join(root, CHANGELOG_RELPATH)), str(exc))
        finally:
            _journal._write_all = real_write
        ok("R15-readback-leftover-settled", _clean_stage(rfd, CHANGELOG_RELPATH, entry["staging"],
                                                         CHANGELOG_RELPATH) == "discarded-stage")
        # A same-length corruption passes the size check, so only the read-back can refuse it.

        def corrupted(fd, payload):
            real_write(fd, bytes(reversed(payload)))
        _journal._write_all = corrupted
        try:
            _stage_and_publish(rfd, CHANGELOG_RELPATH, entry, data)
            ok("R15-readback-same-size", False, "a corrupted staged write was published")
        except InitOperationError as exc:
            ok("R15-readback-same-size", exc.code == FAILED and "staged" in str(exc)
               and not os.path.lexists(os.path.join(root, CHANGELOG_RELPATH)), str(exc))
        finally:
            _journal._write_all = real_write
    finally:
        os.close(rfd)

    # R16 a `dirs-verified` milestone whose journal is NOT complete (the COMPLETE frame lost) is
    # contradictory evidence: CANNOT-EVALUATE, preserved, never re-applied over.
    root = _plain_repo(os.path.join(base, "contradiction"), env)
    rc, _res, _err = _child(root, env, kill="sources-intent")
    ops, _raw = _read_plan(root)
    log = os.path.join(root, ".git", _opf_init_substrate.SUBSTRATE_DIRNAME,
                       _opf_init_substrate.JOURNALS_DIRNAME, _txn_name(ops[0], "dirs"), "frames.log")
    body = open(log, "rb").read()
    second = body.index(b"\n" + _journal.MAGIC + b" " + _journal.F_COMPLETE.encode())
    with open(log, "wb") as fh:
        fh.write(body[:second + 1])
    before = _worktree_without_lease(root)
    rc, res, _err = _child(root, env)
    ok("R16-verified-without-complete-refused", res and res["status"] == CANNOT_EVALUATE
       and "not COMPLETE" in res["primary_failure"]["detail"]
       and _worktree_without_lease(root) == before, str(res))

    # R14 a foreign entry inside the operation's control records is preserved unevaluable evidence:
    # the retry refuses CANNOT-EVALUATE and never starts a replacement operation.
    root = _plain_repo(os.path.join(base, "foreign-control"), env)
    rc, _res, _err = _child(root, env, kill="source:2")
    ops, _raw = _read_plan(root)
    _write(os.path.join(_ops_dir(root), ops[0], "notes.txt"), b"x\n")
    rc, res, _err = _child(root, env)
    ok("R14-foreign-control-refused", res and res["status"] == CANNOT_EVALUATE
       and os.path.exists(os.path.join(_ops_dir(root), ops[0], "notes.txt"))
       and sorted(os.listdir(_ops_dir(root))) == ops, str(res))

    root = _plain_repo(os.path.join(base, "contended"), env)
    holder = _opf_oplock.acquire_init_operation(root, "opf-init")
    try:
        rc, res, _err = _child(root, env)
        ok("R11-contention-refused", res and res["status"] == REFUSED
           and "contention" in res["primary_failure"]["detail"]
           and not os.path.exists(os.path.join(root, ".working")), str(res))
    finally:
        _opf_oplock.release_init_holder(holder)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] in ("--self-test", "--selftest"):
        sys.exit(_run_self_test())
    if len(sys.argv) == 7 and sys.argv[1] == "--selftest-child":
        sys.exit(_child_main(sys.argv[2:]))
    sys.stderr.write("usage: python3 -I -B _opf_init_operation.py --self-test "
                     "(a library module; no live mode)\n")
    sys.exit(2)
