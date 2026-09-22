#!/usr/bin/env python3
"""OPF adoption convergence: inert schemas and read-only investigation/planning.

PR-A supplies the `AdoptionPlan`, receipt-core and outcome-event schemas, the closed twelve-op
ADOPT_OPS vocabulary, and pure fail-closed validators. Validators receive already-parsed objects and
decide well-formedness only; they never read bytes themselves.

PR-B adds the investigate() and plan() library entry points, implemented by the sibling
_opf_adopt_plan module. They inspect an explicitly scoped repository and return canonical inert
observation, inventory and plan bytes. They never persist those outputs or run an operation.
The registered self-test also exercises synthetic temporary fixtures.

Apply, trust verification, acceptance capture, the adoption doctor, behavioral probes, and the CLI
entry point remain later slices. In particular, enable-hook remains a vocabulary row only: this
module neither computes a harness-specific registration merge nor activates a hook. A VALID frozen
proposal is not execution authorization or an ADOPTED_AND_VALID verdict.

Base-neutral by construction (H-7 ratified): the adoption receipt is a STORE-LEVEL artefact whose vocabulary
stays adopter-neutral. Nothing here is an AIQT-profile-specific record type; `product` is the only identity
field and it is one of the two neutral tokens in `PRODUCT_IDENTITIES` (AIQT-including-OPF vs standalone OPF).

Outcome model (the U1 / U2 house idiom, single-sourced from `_opf_store`): every validator returns an
`AdoptValidation` carrying one of VALID / INVALID / CANNOT-EVALUATE plus findings. The mapping, applied
uniformly and never yielding a silent VALID for bad input (spec 3 "Fail closed"):
  - VALID           the input is a well-formed instance of the schema / op.
  - CANNOT-EVALUATE  the input is UNPARSEABLE in the structural sense (not a table / wrong container type) OR
                    carries an OUT-OF-VOCABULARY closed token (an op name outside ADOPT_OPS, an unknown
                    outcome-event kind, an unknown product identity or disposition). The evaluator cannot
                    decide such input under v1 assumptions, so it refuses rather than guesses.
  - INVALID         the input is a table but VIOLATES the schema: a missing required field, an unknown extra
                    key (schemas are CLOSED), a malformed digest / timestamp / path shape, an empty required
                    list, or a broken outcome-event chain.
Both non-VALID verdicts are REFUSING; neither is a pass. This distinction mirrors `_opf_schema` (a non-table
or roster error is CANNOT-EVALUATE; a schema violation is INVALID) so the later engine and doctor compose it
without a translation layer.

Offline, stdlib only, fail-closed. It lives under `opf/tools/` and imports ONLY sibling `opf/tools/`
modules (the shared outcome model from `_opf_store`, the closed effect vocabulary `OP_KINDS` from
`_journal` as inert data), so it introduces no upward edge into `tools/` and the standalone-closure property
holds.

Exit convention (the repo's gates and the sibling OPF units): 0 clean, 1 a finding, 2 cannot-evaluate.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
# Single-source the outcome model (U1) rather than re-declaring it, exactly as _opf_schema does.
from _opf_store import VALID, INVALID, CANNOT_EVALUATE  # noqa: E402
# The closed journal effect vocabulary, imported as INERT DATA (a tuple of primitive names). This is a
# reference, never a call: PR-A performs no transaction. Sourcing it here keeps every op's declared journal
# semantics provably a subset of the real primitive set, so the vocabulary cannot drift from the engine.
from _journal import OP_KINDS as JOURNAL_PRIMITIVES  # noqa: E402


# --- fixed formats, versions, and closed vocabularies ------------------------------------------------

SCHEMA_VERSION = 1                          # the one schema version this module understands
PLAN_FORMAT = "opf.adoption.plan/v1"        # the AdoptionPlan format marker
RECEIPT_FORMAT = "opf.adoption.receipt/v1"  # the immutable receipt-core format marker (b.4)
EVENT_FORMAT = "opf.adoption.event/v1"      # the append-only outcome-event format marker (b.4)

# Product identity (b.1): one bundle converges either product. Neutral tokens, no AIQT-profile leakage.
PRODUCT_IDENTITIES = ("aiqt", "opf")

# Per-file disposition vocabulary (OPF-SPEC 14.2, reconciled in b.5): the closed set of resolutions a plan
# may record for a foreign file. `keep` is realized by register-unmanaged, `migrate` by import-file, `move`
# by move-file, `retire` by retire-file.
DISPOSITIONS = ("keep", "migrate", "move", "retire")

# The append-only outcome-event kinds (b.4). `applied` is the genesis outcome; the rest chain after it.
OUTCOME_EVENTS = ("applied", "premerge-validated", "merged", "postmerge-validated",
                  "failed", "recovered", "signed-off")

# Digest form, matching the store and _opf_import._DIGEST_RE exactly ("sha256:" + 64 lowercase hex).
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}\Z")
# RFC 3339 UTC instant, SHAPE only (Z or +00:00; optional fractional seconds). The engine PRs bind to
# _opf_schema's calendar-validated check; PR-A validates the lexical shape so a receipt/event timestamp is
# well-formed. A native (unquoted) TOML datetime is a non-string and is rejected as a wrong type.
_TS_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]+)?(Z|\+00:00)\Z")
# The adoption run-id grammar `adopt-<UTCSTAMP>Z-<hash16>`, analogous to _opf_import's `imp-` grammar. It is
# a SHAPE oracle for the finalizer; PR-A neither mints nor parses beyond this shape.
_RUN_ID_RE = re.compile(r"^adopt-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{16}\Z")
# The IMPORT run-id grammar `imp-<UTCSTAMP>Z-<hash16>`, the AUTHORITATIVE grammar minted and enforced by the
# sibling _opf_import (its `_RUN_ID_RE`, ~line 190). It is MIRRORED here (this module validates an
# already-parsed receipt/plan, so it does not import _opf_import's engine) and MUST match the sibling's
# pattern byte for byte; the self-test asserts that equality against _opf_import._RUN_ID_RE so the mirror
# cannot drift. A receipt import-run id or a plan's import_run_id field is validated against THIS grammar,
# not a generic token check, so a traversing or malformed id (e.g. "../escape") is refused, never accepted.
_IMPORT_RUN_ID_RE = re.compile(r"^imp-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{16}\Z")

# DISCLOSED-RESIDUAL (disclose-guard-residuals): this is an INERT data-model validator; it decides
# well-formedness only. Beyond the lexical calendar-shape residual noted at _TS_RE above, it deliberately
# does NOT check, and DEFERS to the engine PRs: calendar-date validity (a shape-valid timestamp may name a
# date that does not exist); content availability (that a well-formed digest names real, retrievable bytes,
# that a manifest_sha256 matches an actual release, or that a plan/inventory/receipt-core digest names the
# artefact actually built or applied); and filesystem reality (that a contained relpath exists, is absent,
# or is writable). Those questions need bytes, a journal, or a live store, which PR-A never touches. The
# checks PR-A does make are internal well-formedness and internal cross-field consistency (an approval's
# digests match the receipt's own top-level digests; the per-file disposition agrees with its before/after
# digest presence per spec 14.2; every keyed collection is duplicate-free; the outcome-event chain links and
# does not cycle), not any claim about the outside world.
#
# Consistency residuals deliberately NOT enforced here, disclosed rather than invented (guard-input-
# soundness): (a) the `migrate` disposition's after_digest PRESENCE, because spec 14.2 leaves migrate free
# either to land a generated view at the path or to clear it, so only migrate's before_digest (a pre-existing
# file) is pinned here; (b) equality of a plan's own plan_digest/inventory_digest with a receipt's, or of an
# import run's acceptance_digest or the release manifest_sha256 with any other field, because these bind
# ACROSS artefacts (a plan, a receipt, a release) or to real bytes, and a single-artefact validator cannot
# reconcile them; (c) duplicate-freeness / cross-references WITHIN the plan `ops` list (two ops touching one
# path, a create then an incompatible op), because `ops` is an ordered program, not a keyed collection, and
# its cross-op preconditions are apply-time engine checks. These need a second artefact, real bytes, or the
# engine, which PR-A never touches.


# --- result carrier (the U1 ManifestValidation / U2 RecordValidation idiom) --------------------------

class AdoptValidation:
    __slots__ = ("status", "findings")

    def __init__(self, status, findings=None):
        self.status = status              # VALID / INVALID / CANNOT_EVALUATE
        self.findings = findings or []


def _cannot(msg):
    return AdoptValidation(CANNOT_EVALUATE, [msg])


def _invalid(findings):
    return AdoptValidation(INVALID, list(findings))


def _ok():
    return AdoptValidation(VALID, [])


# --- shared field-shape predicates (pure) ------------------------------------------------------------

def _is_contained_relpath(value):
    """A store-relative, contained path: a non-empty str with no control character, not absolute, and with
    no `.` or `..` component (guard-input-soundness: a plan/receipt path is an adopter-store-relative
    locator, never an absolute or traversing path). Every control character is rejected, mirroring
    `_is_token`'s no-control rule (C0 controls and DEL, the C1 controls 0x80-0x9F, and the Unicode line/
    paragraph separators U+2028/U+2029, so NUL is one case of the wider class): an embedded newline or tab
    in a path would otherwise be a latent line-injection vector for the line-oriented TOML the engine PRs
    parse. Windows drive/backslash forms are refused too."""
    if not isinstance(value, str) or not value:
        return False
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F or 0x80 <= ord(ch) <= 0x9F
           or ch in ("\u2028", "\u2029") for ch in value):
        return False
    if value.startswith("/") or value.startswith("\\"):
        return False
    if re.match(r"^[A-Za-z]:", value):        # a drive-lettered absolute path
        return False
    if "\\" in value:                         # any backslash is refused (no Windows path forms)
        return False
    if value == ".":                          # the store root itself (e.g. store_root = ".")
        return True
    parts = value.split("/")
    return all(p not in ("", ".", "..") for p in parts)


def _is_contained_filepath(value):
    """A store-relative, contained FILE path: `_is_contained_relpath` AND not the store-root directory `.`
    (guard-input-soundness / field-precision: a file operand names a FILE, so its locator can never be `.`,
    the directory itself). `_is_contained_relpath` already rejects every other pure-directory form (a
    trailing slash yields an empty component, refused), so `.` is the only extra case a file field must
    reject beyond the contained-relpath check. A store-root or directory field keeps the `.` allowance and
    uses `_is_contained_relpath`; a create/retire/move/import/enable-hook/record-adoption operand uses this."""
    return _is_contained_relpath(value) and value != "."


def _is_import_run_id(value):
    """An import run-id validated against its AUTHORITATIVE grammar `imp-<UTCSTAMP>Z-<hash16>` (mirrored from
    _opf_import, see _IMPORT_RUN_ID_RE), not a generic token: a traversing or malformed id is refused."""
    return isinstance(value, str) and bool(_IMPORT_RUN_ID_RE.match(value))


def _is_digest(value):
    return isinstance(value, str) and bool(_DIGEST_RE.match(value))


def _is_token(value):
    """A non-empty single-line string token (no control characters, no line breaks). Rejects C0 controls
    and DEL, the C1 controls (0x80-0x9F, which include NEL U+0085), and the Unicode line/paragraph
    separators U+2028/U+2029, so the guard matches its no-control/no-line-break contract across the whole
    code-point space rather than only the ASCII range."""
    if not isinstance(value, str) or not value:
        return False
    return not any(ord(ch) < 0x20 or ord(ch) == 0x7F or 0x80 <= ord(ch) <= 0x9F
                   or ch in ("\u2028", "\u2029") for ch in value)


def _is_timestamp(value):
    return isinstance(value, str) and bool(_TS_RE.match(value))


def _is_bool(value):
    return isinstance(value, bool)


def _is_schema_version(value):
    """The schema field must be the supported integer version, not a value that merely compares equal to
    it. Python treats True == 1 and 1.0 == 1, so a bool or a float is refused by type before the value
    check (a wrong-TYPED schema is INVALID, never a silent accept)."""
    return isinstance(value, int) and not isinstance(value, bool) and value == SCHEMA_VERSION


# --- the closed operation vocabulary (ADOPT_OPS): DATA ONLY, no execution -----------------------------

# Field kinds for every input a plan op row may carry. A single source so validate_op cannot drift from the
# op definitions. Kinds and their predicates (field-precision: a predicate matches the field's real
# semantics, never a more permissive proxy):
#   "filepath"  a contained relpath that names a FILE operand -> _is_contained_filepath (rejects `.`).
#   "dirpath"   a contained relpath that names a DIRECTORY / store root -> _is_contained_relpath (allows `.`).
#   "digest"    sha256:hex -> _is_digest.  "token" a non-empty single-line str -> _is_token.
#   "import_run_id"  an import run-id -> _is_import_run_id (the `imp-...` grammar, not a generic token).
#   "members"   a list of {path (file), digest} tables.
# Path-field classification, grounded in each field's meaning: a create/retire/move/import/enable-hook/
# record-adoption operand names a FILE (path/source/destination/registration_path/receipt_path/entry ->
# "filepath"); a store root or install root names a DIRECTORY (store_root/target -> "dirpath").
_FIELD_KINDS = {
    "target": "dirpath", "store_root": "dirpath", "path": "filepath", "source": "filepath",
    "destination": "filepath", "registration_path": "filepath", "receipt_path": "filepath",
    "entry": "filepath",
    "content_digest": "digest", "source_digest": "digest", "preimage_digest": "digest",
    "old_digest": "digest", "new_digest": "digest", "acceptance_digest": "digest",
    "receipt_core_digest": "digest",
    "source_member": "token", "plugin_entry": "token", "import_run_id": "import_run_id", "note": "token",
    "members": "members",
}


class AdoptOp:
    """One member of the closed vocabulary. Pure metadata: the required/optional input field names, the
    fail-closed PRECONDITION descriptions, the JOURNAL primitives this op compiles to at apply time (a
    subset of the real _journal.OP_KINDS, asserted in the self-test), and the REVERSAL description. Nothing
    here executes; the apply-half engine (PR-D) reads this metadata to build and invert transactions."""

    __slots__ = ("name", "required_inputs", "optional_inputs", "preconditions", "journal", "reversal")

    def __init__(self, name, required_inputs, journal, reversal, preconditions, optional_inputs=()):
        self.name = name
        self.required_inputs = tuple(required_inputs)
        self.optional_inputs = tuple(optional_inputs)
        self.journal = tuple(journal)
        self.reversal = reversal
        self.preconditions = tuple(preconditions)


ADOPT_OPS = (
    AdoptOp(
        "install-pack", ("target", "members"), ("mkdir", "create"),
        "remove the planted members and any directories this op created, restoring the prior absence",
        ("every member digest is verified against the frozen pack inventory (no install from an unchecked "
         "archive); each member target path is absent or disposition-routed",)),
    AdoptOp(
        "init-store", ("store_root",), ("mkdir", "create"),
        "remove the scaffolded store, restoring NOT-ADOPTED",
        ("resolve_store reports NOT-ADOPTED at store_root; every foreign .working file carries a "
         "disposition first (never a blind opf init over populated content)",)),
    AdoptOp(
        "create-file", ("path", "content_digest"), ("create",),
        "remove the created file",
        ("the path is absent; a collision routes to a disposition, never an overwrite",),
        optional_inputs=("note",)),
    AdoptOp(
        "plant-governance", ("path", "content_digest", "source_member"), ("create",),
        "remove the planted adapter, restoring the prior absence",
        ("the bytes passed the b.5 trust gate (manifest sha256 and the agreed ROOT); the path is absent (a "
         "pre-existing different file routes to retire-file + create-file under an explicit plan row)",)),
    AdoptOp(
        "import-file", ("import_run_id", "acceptance_digest"), ("create", "write", "remove"),
        "the staged-import cutover's own journalled inverse (apply_import restores the preimage)",
        ("delegates to the existing staged-import machinery and apply_import's journalled cutover (never "
         "forks it); per-fragment attributed acceptance is preserved; an unresolved fragment blocks",)),
    AdoptOp(
        "register-unmanaged", ("entry",), ("write",),
        "restore the manifest preimage (drop the [unmanaged] entry)",
        ("the manifest [unmanaged] entry is emitted via emit_checked; no collision with a managed name",),
        optional_inputs=("note",)),
    AdoptOp(
        "move-file", ("source", "destination", "source_digest"), ("create", "remove"),
        "move the destination back to the source path",
        ("the destination is absent; the source live bytes match source_digest (preimage-matched)",)),
    AdoptOp(
        "retire-file", ("path", "preimage_digest"), ("remove",),
        "recreate the file from the recorded preimage bytes",
        ("the live bytes match preimage_digest exactly; a drifted file is cannot-evaluate, never a blind "
         "retire",)),
    AdoptOp(
        "repoint-consumer", ("path", "old_digest", "new_digest"), ("write",),
        "restore the recorded old bytes of the consumer file",
        ("the recorded old state is validated; whole-file parse-and-re-emit replacement, never a regex "
         "substitution over arbitrary repository text",)),
    AdoptOp(
        "enable-hook", ("registration_path", "plugin_entry", "old_digest", "new_digest"), ("write",),
        "restore the prior registration bytes (the pre-merge registration surface)",
        ("DECLARED ONLY in PR-A, neither implemented nor executed here; writes executable-on-load "
         "configuration, so it is threat-modelled before implementation and surfaced in the one informed "
         "yes; a structured JSON merge, re-emitted byte-exact, never a blind append",)),
    AdoptOp(
        "render-views", ("store_root",), ("create", "write"),
        "remove the views this op created (the render engine refuses to write over an invalid store)",
        ("delegates to the render engine, which itself refuses to write over an invalid store",)),
    AdoptOp(
        "record-adoption", ("receipt_path", "receipt_core_digest"), ("create", "write"),
        "remove or restore the receipt artefacts to their prior state",
        ("writes the immutable receipt core plus the append-only outcome events (b.4)",)),
)

# The op-name -> AdoptOp map and the closed name set. A plan op outside this set is a cannot-evaluate.
ADOPT_OPS_BY_NAME = {op.name: op for op in ADOPT_OPS}
ADOPT_OP_NAMES = frozenset(ADOPT_OPS_BY_NAME)


# --- canonical shapes (minimal well-formed instances; also the accept-a-valid-one self-test vectors) --

def canonical_op(name):
    """A minimal VALID plan op row for `name`, carrying exactly its required inputs with well-formed shapes.
    Used as the canonical example and as the self-test's accept vector for every op."""
    _ZERO = "sha256:" + "0" * 64
    _sample = {
        "target": "adopter/.aiqt/core", "store_root": ".", "path": "adopter/.opf.toml",
        "source": "legacy/RULES.md", "destination": "adopter/rules/legacy.md",
        "registration_path": ".claude/settings.json", "receipt_path": ".working/toml/adoption.toml",
        "entry": "adopter/LEGACY.md",
        "content_digest": _ZERO, "source_digest": _ZERO, "preimage_digest": _ZERO,
        "old_digest": _ZERO, "new_digest": _ZERO, "acceptance_digest": _ZERO,
        "receipt_core_digest": _ZERO,
        "source_member": ".aiqt/core/rules/rules.toml", "plugin_entry": "opf-governance",
        "import_run_id": "imp-20260917T120000Z-0123456789abcdef",
        "members": [{"path": "adopter/.aiqt/core/rules/rules.toml", "digest": _ZERO}],
    }
    op = ADOPT_OPS_BY_NAME[name]
    row = {"op": name}
    for field in op.required_inputs:
        row[field] = _sample[field]
    return row


def canonical_plan():
    """A minimal VALID AdoptionPlan (b.4 shape): the format/version markers, product identity, the two
    digests binding the plan and inventory, and an ordered ops list from the closed vocabulary."""
    return {
        "format": PLAN_FORMAT,
        "schema": SCHEMA_VERSION,
        "product": "aiqt",
        "plan_digest": "sha256:" + "1" * 64,
        "inventory_digest": "sha256:" + "2" * 64,
        "ops": [canonical_op("init-store"), canonical_op("record-adoption")],
    }


def canonical_receipt_core():
    """A minimal VALID receipt core (`opf.adoption.receipt/v1`, b.4): run id, plan/inventory digests, product
    and store bindings, release identity (manifest_sha256, independent-anchor observation + agreement flag),
    approval record, per-file before/after digests with dispositions, import runs with acceptance digests,
    governance and wiring tables, transaction ids, required checks and probes, and coverage residuals."""
    return {
        "format": RECEIPT_FORMAT,
        "schema": SCHEMA_VERSION,
        "run_id": "adopt-20260917T120000Z-0123456789abcdef",
        "product": "aiqt",
        "plan_digest": "sha256:" + "1" * 64,
        "inventory_digest": "sha256:" + "2" * 64,
        "store_root": ".",
        "release": {
            "manifest_sha256": "sha256:" + "3" * 64,
            "independent_anchor_observed": True,
            "anchor_agreement": True,
        },
        "approval": {
            "actor": "maintainer",
            "approved_at": "2026-09-17T12:00:00Z",
            "plan_digest": "sha256:" + "1" * 64,
            "inventory_digest": "sha256:" + "2" * 64,
        },
        "files": [
            {"path": "adopter/.opf.toml", "before_digest": "sha256:" + "4" * 64,
             "after_digest": "sha256:" + "4" * 64, "disposition": "keep"},
        ],
        "import_runs": [
            {"run_id": "imp-20260917T120000Z-0123456789abcdef", "acceptance_digest": "sha256:" + "5" * 64},
        ],
        "governance": {},
        "wiring": {},
        "transaction_ids": ["TX-1"],
        "required_checks": ["A-STORE", "A-STORE-INTEGRITY"],
        "probes": ["deny-default-control"],
        "coverage_residuals": [],
    }


def canonical_outcome_event(kind="applied", previous_event_digest=None):
    """A minimal VALID outcome event (`opf.adoption.event/v1`, b.4). The genesis event (`applied`) chains
    from the receipt-core digest supplied by the caller; a later event chains from the prior event digest."""
    return {
        "format": EVENT_FORMAT,
        "schema": SCHEMA_VERSION,
        "kind": kind,
        "run_id": "adopt-20260917T120000Z-0123456789abcdef",
        "revision": "0123456789abcdef0123456789abcdef01234567",
        "recorded_at": "2026-09-17T12:00:00Z",
        "event_digest": "sha256:" + "a" * 64,
        "previous_event_digest": previous_event_digest or ("sha256:" + "9" * 64),
    }


# Closed key-sets for the top-level schemas (schemas are CLOSED: an unknown key is INVALID).
PLAN_REQUIRED = ("format", "schema", "product", "plan_digest", "inventory_digest", "ops")
PLAN_OPTIONAL = ("run_id", "created_at")

RECEIPT_CORE_REQUIRED = (
    "format", "schema", "run_id", "product", "plan_digest", "inventory_digest", "store_root",
    "release", "approval", "files", "import_runs", "governance", "wiring", "transaction_ids",
    "required_checks", "probes", "coverage_residuals")
RECEIPT_CORE_OPTIONAL = ()

RELEASE_REQUIRED = ("manifest_sha256", "independent_anchor_observed", "anchor_agreement")
APPROVAL_REQUIRED = ("actor", "approved_at", "plan_digest", "inventory_digest")

OUTCOME_EVENT_REQUIRED = (
    "format", "schema", "kind", "run_id", "revision", "recorded_at", "event_digest",
    "previous_event_digest")
OUTCOME_EVENT_OPTIONAL = ("note",)


# --- validators (pure; every bad input is a refusing verdict, never a silent pass) -------------------

def _closed_keyset(table, required, optional):
    """Return (missing, unknown) for a closed schema keyset. Both empty means the keyset is exact. Every
    schema key is a string, so a non-string dict key (an int, a native TOML datetime, a float) can never be
    allowed and always lands in `unknown`, where it is reported as a closed-schema violation; the keys are
    ordered by `str(k)` so a mix of key types cannot raise TypeError (a validator never crashes on
    adversarial already-parsed input, it returns a refusing verdict)."""
    keys = set(table)
    allowed = set(required) | set(optional)
    missing = [k for k in required if k not in keys]
    unknown = sorted(keys - allowed, key=str)
    return missing, unknown


def validate_op(row):
    """Validate ONE AdoptionPlan op row against its ADOPT_OPS definition. A non-table is CANNOT-EVALUATE; an
    op name outside ADOPT_OPS is CANNOT-EVALUATE (out-of-vocabulary refused, never a skip); a table with a
    known op but a missing/unknown field or a malformed field shape is INVALID."""
    if not isinstance(row, dict):
        return _cannot("op row is not a table")
    name = row.get("op")
    if not isinstance(name, str) or not name:
        return _cannot("op row has no string 'op' selector")
    if name not in ADOPT_OP_NAMES:
        return _cannot("op {!r} is outside the closed ADOPT_OPS vocabulary".format(name))
    spec = ADOPT_OPS_BY_NAME[name]
    findings = []
    missing, unknown = _closed_keyset(row, ("op",) + spec.required_inputs, spec.optional_inputs)
    for k in missing:
        findings.append("op {!r} is missing required input {!r}".format(name, k))
    for k in unknown:
        findings.append("op {!r} carries unknown key {!r} (schemas are closed)".format(name, k))
    for field in set(row) - {"op"}:
        if field not in _FIELD_KINDS:
            continue  # an unknown key was already reported above; do not shape-check it
        if not _valid_field(field, row[field]):
            findings.append("op {!r} field {!r} has a malformed value".format(name, field))
    return _ok() if not findings else _invalid(findings)


def _valid_field(field, value):
    kind = _FIELD_KINDS[field]
    if kind == "filepath":
        return _is_contained_filepath(value)
    if kind == "dirpath":
        return _is_contained_relpath(value)
    if kind == "digest":
        return _is_digest(value)
    if kind == "token":
        return _is_token(value)
    if kind == "import_run_id":
        return _is_import_run_id(value)
    if kind == "members":
        if not isinstance(value, list) or not value:
            return False
        seen_paths = set()
        for m in value:
            if not isinstance(m, dict):
                return False
            if set(m) != {"path", "digest"}:
                return False
            # a member names a FILE planted into the pack tree, so its path is a file operand (rejects `.`).
            if not (_is_contained_filepath(m["path"]) and _is_digest(m["digest"])):
                return False
            if m["path"] in seen_paths:   # two members for one path is a contradictory install record
                return False
            seen_paths.add(m["path"])
        return True
    return False  # an unclassified field kind fails closed


def validate_plan(plan):
    """Validate an AdoptionPlan. A non-table is CANNOT-EVALUATE; an unknown product / a per-op
    out-of-vocabulary name propagates CANNOT-EVALUATE; any other schema violation is INVALID."""
    if not isinstance(plan, dict):
        return _cannot("adoption plan is not a table")
    findings = []
    missing, unknown = _closed_keyset(plan, PLAN_REQUIRED, PLAN_OPTIONAL)
    for k in missing:
        findings.append("plan is missing required key {!r}".format(k))
    for k in unknown:
        findings.append("plan carries unknown key {!r} (schemas are closed)".format(k))
    if plan.get("format") != PLAN_FORMAT and "format" not in missing:
        findings.append("plan format is not {!r}".format(PLAN_FORMAT))
    if "schema" not in missing and not _is_schema_version(plan.get("schema")):
        findings.append("plan schema is not the supported version {}".format(SCHEMA_VERSION))
    product = plan.get("product")
    if "product" not in missing and product not in PRODUCT_IDENTITIES:
        return _cannot("plan product {!r} is outside PRODUCT_IDENTITIES".format(product))
    for key in ("plan_digest", "inventory_digest"):
        if key not in missing and not _is_digest(plan.get(key)):
            findings.append("plan {} is not a well-formed digest".format(key))
    if "run_id" in plan and not (isinstance(plan["run_id"], str) and _RUN_ID_RE.match(plan["run_id"])):
        findings.append("plan run_id does not match the adoption run-id grammar")
    if "created_at" in plan and not _is_timestamp(plan["created_at"]):
        findings.append("plan created_at is not an RFC 3339 UTC instant")
    ops = plan.get("ops")
    if "ops" not in missing:
        if not isinstance(ops, list):
            return _cannot("plan 'ops' is not a list")
        if not ops:
            findings.append("plan 'ops' is empty (a plan must carry at least one op)")
        for i, row in enumerate(ops):
            res = validate_op(row)
            if res.status == CANNOT_EVALUATE:
                return _cannot("plan op[{}]: {}".format(i, "; ".join(res.findings)))
            if res.status == INVALID:
                findings.extend("plan op[{}]: {}".format(i, f) for f in res.findings)
    return _ok() if not findings else _invalid(findings)


def _validate_subtable(table, required, label, findings):
    """Validate a required nested table exists and carries exactly its required keys. Returns the table or
    None. A missing/non-table subtable is a finding (INVALID); an out-of-vocabulary token is handled by the
    caller."""
    if not isinstance(table, dict):
        findings.append("{} is not a table".format(label))
        return None
    missing, unknown = _closed_keyset(table, required, ())
    for k in missing:
        findings.append("{} is missing required key {!r}".format(label, k))
    for k in unknown:
        findings.append("{} carries unknown key {!r}".format(label, k))
    return table


def validate_receipt_core(receipt):
    """Validate an adoption receipt core (`opf.adoption.receipt/v1`). A non-table is CANNOT-EVALUATE; an
    unknown product or an unknown per-file disposition is CANNOT-EVALUATE; other violations are INVALID."""
    if not isinstance(receipt, dict):
        return _cannot("receipt core is not a table")
    findings = []
    missing, unknown = _closed_keyset(receipt, RECEIPT_CORE_REQUIRED, RECEIPT_CORE_OPTIONAL)
    for k in missing:
        findings.append("receipt is missing required key {!r}".format(k))
    for k in unknown:
        findings.append("receipt carries unknown key {!r} (schemas are closed)".format(k))
    if receipt.get("format") != RECEIPT_FORMAT and "format" not in missing:
        findings.append("receipt format is not {!r}".format(RECEIPT_FORMAT))
    if "schema" not in missing and not _is_schema_version(receipt.get("schema")):
        findings.append("receipt schema is not the supported version {}".format(SCHEMA_VERSION))
    if "run_id" not in missing and not (isinstance(receipt.get("run_id"), str)
                                        and _RUN_ID_RE.match(receipt["run_id"])):
        findings.append("receipt run_id does not match the adoption run-id grammar")
    product = receipt.get("product")
    if "product" not in missing and product not in PRODUCT_IDENTITIES:
        return _cannot("receipt product {!r} is outside PRODUCT_IDENTITIES".format(product))
    for key in ("plan_digest", "inventory_digest"):
        if key not in missing and not _is_digest(receipt.get(key)):
            findings.append("receipt {} is not a well-formed digest".format(key))
    if "store_root" not in missing and not _is_contained_relpath(receipt.get("store_root")):
        findings.append("receipt store_root is not a contained relative path")
    # release identity
    if "release" not in missing:
        rel = _validate_subtable(receipt.get("release"), RELEASE_REQUIRED, "receipt release", findings)
        if rel is not None:
            if "manifest_sha256" in rel and not _is_digest(rel["manifest_sha256"]):
                findings.append("receipt release.manifest_sha256 is not a well-formed digest")
            for flag in ("independent_anchor_observed", "anchor_agreement"):
                if flag in rel and not _is_bool(rel[flag]):
                    findings.append("receipt release.{} is not a boolean".format(flag))
    # approval record
    if "approval" not in missing:
        appr = _validate_subtable(receipt.get("approval"), APPROVAL_REQUIRED, "receipt approval", findings)
        if appr is not None:
            if "approved_at" in appr and not _is_timestamp(appr["approved_at"]):
                findings.append("receipt approval.approved_at is not an RFC 3339 UTC instant")
            for key in ("plan_digest", "inventory_digest"):
                if key in appr and not _is_digest(appr[key]):
                    findings.append("receipt approval.{} is not a well-formed digest".format(key))
            # the approval attests to the plan/inventory it approved; when the receipt's own top-level
            # digest and the approval's are both present and well-formed they must be equal, so an approval
            # cannot attest to different content than the receipt records (a governance violation).
            for key in ("plan_digest", "inventory_digest"):
                top = receipt.get(key)
                sub = appr.get(key)
                if _is_digest(top) and _is_digest(sub) and top != sub:
                    findings.append("receipt approval.{0} does not match the receipt {0} (approval attests "
                                    "to different content)".format(key))
            if "actor" in appr and not _is_token(appr["actor"]):
                findings.append("receipt approval.actor is not a non-empty token")
    # per-file before/after digests with dispositions
    if "files" not in missing:
        res = _validate_files(receipt.get("files"), findings)
        if res is not None:              # a CANNOT-EVALUATE (out-of-vocab disposition) short-circuits
            return res
    # import runs with acceptance digests
    if "import_runs" not in missing:
        _validate_import_runs(receipt.get("import_runs"), findings)
    # list-of-token fields. Each is an identifier / label set (transaction ids, required-check ids, probe
    # ids, coverage-residual ids): a repeated entry is a contradictory record, so membership is unique.
    for key in ("transaction_ids", "required_checks", "probes", "coverage_residuals"):
        if key in missing:
            continue
        value = receipt.get(key)
        if not _is_token_list(value):
            findings.append("receipt {} is not a list of tokens".format(key))
        elif len(set(value)) != len(value):
            findings.append("receipt {} carries a duplicate entry (it is an identifier set)".format(key))
    # governance and wiring stay CONTENT-OPAQUE (engine-deferred: their values / inner schema are not
    # validated here), but they must still be well-formed TABLES, so a non-string KEY is refused: a
    # non-string key is structurally malformed for any TOML/JSON table, exactly as the closed schemas'
    # non-string keys are refused by _closed_keyset. (release/approval are already closed-validated; the
    # sweep found no other isinstance-dict-only opaque field.)
    for key in ("governance", "wiring"):
        if key in missing:
            continue
        value = receipt.get(key)
        if not isinstance(value, dict):
            findings.append("receipt {} is not a table".format(key))
        elif any(not isinstance(k, str) for k in value):
            findings.append("receipt {} has a non-string key".format(key))
    return _ok() if not findings else _invalid(findings)


def _is_token_list(value):
    return isinstance(value, list) and all(_is_token(v) for v in value)


def _validate_files(files, findings):
    """Validate the per-file table. Returns an AdoptValidation ONLY when it must short-circuit CANNOT-
    EVALUATE (an out-of-vocabulary disposition); otherwise appends findings and returns None."""
    if not isinstance(files, list):
        findings.append("receipt files is not a list")
        return None
    seen_paths = set()                   # each usable string path may key at most one row (no duplicates)
    for i, row in enumerate(files):
        if not isinstance(row, dict):
            findings.append("receipt files[{}] is not a table".format(i))
            continue
        allowed = {"path", "before_digest", "after_digest", "disposition", "preservation_ref"}
        missing = [k for k in ("path", "before_digest", "after_digest", "disposition") if k not in row]
        unknown = sorted(set(row) - allowed, key=str)  # key=str so a non-string key cannot raise
        for k in missing:
            findings.append("receipt files[{}] is missing {!r}".format(i, k))
        for k in unknown:
            findings.append("receipt files[{}] carries unknown key {!r}".format(i, k))
        # a MISSING disposition was reported INVALID just above (missing required key -> INVALID); only a
        # disposition that is PRESENT but out of the closed vocabulary is a refusing cannot-evaluate.
        if "disposition" in row and row["disposition"] not in DISPOSITIONS:
            return _cannot("receipt files[{}] disposition {!r} is outside DISPOSITIONS".format(
                i, row["disposition"]))
        # a per-file record names a FILE, so its path is a file operand (a contained relpath, never `.`).
        if "path" in row and not _is_contained_filepath(row["path"]):
            findings.append("receipt files[{}] path is not a contained relative file path".format(i))
        # a path appearing on more than one row is a contradictory record; only rows with a usable string
        # path are compared (a missing/non-string path was already reported above).
        if isinstance(row.get("path"), str):
            if row["path"] in seen_paths:
                findings.append("receipt files[{}] path {!r} is a duplicate of an earlier row".format(
                    i, row["path"]))
            seen_paths.add(row["path"])
        # a present before/after value must be a well-formed digest; a null records the file's absence on
        # that side (see the disposition<->digest reconciliation below for when each side may be null).
        for key in ("before_digest", "after_digest"):
            if row.get(key) is not None and not _is_digest(row.get(key)):
                findings.append("receipt files[{}] {} is neither null nor a digest".format(i, key))
        if "preservation_ref" in row and not _is_token(row["preservation_ref"]):
            findings.append("receipt files[{}] preservation_ref is not a token".format(i))
        # disposition <-> digest reconciliation (OPF-SPEC 14.2 KEEP/MIGRATE/MOVE, and the module's own
        # DISPOSITIONS mapping: keep->register-unmanaged, migrate->import-file, move->move-file,
        # retire->retire-file). Every disposition here resolves a DETECTED PRE-EXISTING foreign file, so its
        # before_digest (the prior bytes) MUST be present. `retire` (retire-file, journal `remove`) and
        # `move` (move-file, which relocates the file out of the store, journal `create`+`remove`) both
        # leave the store path empty, so after_digest MUST be absent. `keep` (register-unmanaged: the file
        # is left "exactly where it is, untouched", spec 14.2) keeps the file in place unchanged, so
        # after_digest MUST be present and, being untouched, MUST equal before_digest. `migrate`
        # (import-file) may leave a generated view at the path or clear it (spec 14.2), so ITS after_digest
        # presence is engine-semantics, disclosed in the DISCLOSED-RESIDUAL block, not enforced here.
        disp = row.get("disposition")
        if disp in DISPOSITIONS:
            before_present = row.get("before_digest") is not None
            after_present = row.get("after_digest") is not None
            if not before_present:
                findings.append("receipt files[{}] disposition {!r} records a null before_digest, but it "
                                "resolves a pre-existing file whose prior bytes must be recorded".format(
                                    i, disp))
            if disp in ("retire", "move") and after_present:
                findings.append("receipt files[{}] disposition {!r} records an after_digest, but it "
                                "removes the file from the store path (after_digest must be null)".format(
                                    i, disp))
            if disp == "keep":
                if not after_present:
                    findings.append("receipt files[{}] disposition 'keep' records a null after_digest, but "
                                    "a kept file is left in place (a keep recording a deletion is "
                                    "contradictory)".format(i))
                else:
                    b, a = row.get("before_digest"), row.get("after_digest")
                    if _is_digest(b) and _is_digest(a) and b != a:
                        findings.append("receipt files[{}] disposition 'keep' records before_digest != "
                                        "after_digest, but a kept file is left untouched".format(i))
    return None


def _validate_import_runs(runs, findings):
    if not isinstance(runs, list):
        findings.append("receipt import_runs is not a list")
        return
    seen_run_ids = set()                 # each import run_id keys at most one row (no duplicates)
    for i, row in enumerate(runs):
        if not isinstance(row, dict) or set(row) != {"run_id", "acceptance_digest"}:
            findings.append("receipt import_runs[{}] is not a {{run_id, acceptance_digest}} table".format(i))
            continue
        # the run_id is validated against the AUTHORITATIVE import run-id grammar (mirrored from _opf_import),
        # not a generic token, so a traversing or malformed id (e.g. "../escape") is refused.
        if not _is_import_run_id(row["run_id"]):
            findings.append("receipt import_runs[{}] run_id does not match the import run-id grammar".format(i))
        if not _is_digest(row["acceptance_digest"]):
            findings.append("receipt import_runs[{}] acceptance_digest is not a digest".format(i))
        # a run_id appearing on more than one row is a contradictory record (two acceptance digests for one
        # import run); only rows with a usable string run_id are compared (a non-string was reported above).
        if isinstance(row.get("run_id"), str):
            if row["run_id"] in seen_run_ids:
                findings.append("receipt import_runs[{}] run_id {!r} is a duplicate of an earlier row".format(
                    i, row["run_id"]))
            seen_run_ids.add(row["run_id"])


def validate_outcome_event(event):
    """Validate ONE append-only outcome event (`opf.adoption.event/v1`). A non-table is CANNOT-EVALUATE; an
    out-of-vocabulary kind is CANNOT-EVALUATE; other violations are INVALID. Chain linkage across events is
    checked by validate_event_chain, not here."""
    if not isinstance(event, dict):
        return _cannot("outcome event is not a table")
    findings = []
    missing, unknown = _closed_keyset(event, OUTCOME_EVENT_REQUIRED, OUTCOME_EVENT_OPTIONAL)
    for k in missing:
        findings.append("event is missing required key {!r}".format(k))
    for k in unknown:
        findings.append("event carries unknown key {!r} (schemas are closed)".format(k))
    if event.get("format") != EVENT_FORMAT and "format" not in missing:
        findings.append("event format is not {!r}".format(EVENT_FORMAT))
    if "schema" not in missing and not _is_schema_version(event.get("schema")):
        findings.append("event schema is not the supported version {}".format(SCHEMA_VERSION))
    kind = event.get("kind")
    if "kind" not in missing and kind not in OUTCOME_EVENTS:
        return _cannot("event kind {!r} is outside the closed OUTCOME_EVENTS vocabulary".format(kind))
    if "run_id" not in missing and not (isinstance(event.get("run_id"), str)
                                        and _RUN_ID_RE.match(event["run_id"])):
        findings.append("event run_id does not match the adoption run-id grammar")
    if "revision" not in missing and not _is_token(event.get("revision")):
        findings.append("event revision is not a non-empty token")
    if "recorded_at" not in missing and not _is_timestamp(event.get("recorded_at")):
        findings.append("event recorded_at is not an RFC 3339 UTC instant")
    for key in ("event_digest", "previous_event_digest"):
        if key not in missing and not _is_digest(event.get(key)):
            findings.append("event {} is not a well-formed digest".format(key))
    if "note" in event and not _is_token(event["note"]):
        findings.append("event note is not a token")
    return _ok() if not findings else _invalid(findings)


def validate_event_chain(events, root_digest):
    """Validate an ordered list of outcome events as an append-only chain rooted at `root_digest` (the
    receipt-core digest). Each event must itself be VALID; events[0].previous_event_digest must equal
    root_digest; events[i].previous_event_digest must equal events[i-1].event_digest; all events share the
    run_id; and the chain carries EXACTLY ONE genesis: the first event's kind must be the genesis `applied`
    and no later event (i>0) may carry that kind (a second `applied` is a second genesis, refused). A
    non-list chain, a bad root, or a per-event CANNOT-EVALUATE refuses; an EMPTY chain is INVALID (a chain is
    a required list and one with no genesis event is a malformed instance, an empty required list per the
    module outcome model above), as is a linkage break. Once an event is itself INVALID its linkage fields
    cannot be trusted, so downstream linkage findings are suppressed rather than compared against stale prior
    state (the chain is already INVALID either way); the per-event validity of each later event is still
    checked and a later CANNOT-EVALUATE still refuses, so this is a finding-quality choice, never a relaxation
    of the fail-closed verdict."""
    if not _is_digest(root_digest):
        return _cannot("event-chain root_digest is not a well-formed digest")
    if not isinstance(events, list):
        return _cannot("event chain is not a list")
    if not events:
        return _invalid(["event chain is empty (no genesis outcome event)"])
    findings = []
    prior_digest = root_digest
    run_id = None
    # Cleared the moment an event is itself INVALID: from that point the chain is structurally broken, its
    # subsequent linkage fields (previous_event_digest, event_digest) cannot be trusted, and comparing them
    # against the stale prior state left behind would spawn misleading, structurally-incorrect linkage
    # findings. So once it is cleared the linkage checks are skipped; the per-event validity of each later
    # event is still evaluated above, and the overall verdict stays INVALID (fail-closed), it just stops
    # emitting linkage noise on an already-INVALID chain.
    linkage_intact = True
    # Seed the seen-set with the chain root so an event whose event_digest equals the root digest is caught
    # as a cycle (a chain returning to its root), not only a duplicate of a later event's digest.
    seen_digests = {root_digest}       # every event_digest observed in the chain, plus the root (append-only)
    for i, event in enumerate(events):
        res = validate_outcome_event(event)
        if res.status == CANNOT_EVALUATE:
            return _cannot("event[{}]: {}".format(i, "; ".join(res.findings)))
        if res.status == INVALID:
            findings.extend("event[{}]: {}".format(i, f) for f in res.findings)
            linkage_intact = False   # this event's linkage fields are untrustworthy; break the chain here
            continue  # cannot trust this event's linkage fields; keep collecting other findings
        if not linkage_intact:
            continue  # a prior event already broke the chain (INVALID); do not compare against stale state
        if i == 0:
            if event["kind"] != "applied":
                findings.append("event[0] kind is {!r}, not the genesis 'applied'".format(event["kind"]))
            run_id = event["run_id"]
        else:
            # exactly one genesis: a non-genesis position carrying the genesis kind is a SECOND genesis.
            if event["kind"] == "applied":
                findings.append("event[{}] kind is the genesis 'applied' at a non-genesis position (an "
                                "append-only chain has exactly one genesis)".format(i))
            if event["run_id"] != run_id:
                findings.append("event[{}] run_id differs from the genesis run_id".format(i))
        if event["previous_event_digest"] != prior_digest:
            findings.append("event[{}] previous_event_digest does not chain to the prior digest".format(i))
        # append-only integrity: an event may neither link to itself nor repeat a digest already in the
        # chain, so a self-loop or a duplicate/cycle is refused even when the pairwise links line up.
        if event["event_digest"] == event["previous_event_digest"]:
            findings.append("event[{}] event_digest equals its previous_event_digest (self-loop)".format(i))
        if event["event_digest"] in seen_digests:
            findings.append("event[{}] event_digest duplicates an earlier event (cycle)".format(i))
        seen_digests.add(event["event_digest"])
        prior_digest = event["event_digest"]
    return _ok() if not findings else _invalid(findings)


# --- self-test ---------------------------------------------------------------------------------------

def self_test():
    """Fail-closed invariants over synthetic vectors: every validator ACCEPTS its canonical shape and
    REJECTS a malformed case, and an op outside ADOPT_OPS is refused. Judged on the returned status values,
    never by grepping output (spec 8; the isolate-verifiers rule). No filesystem, no journal, no network."""
    failures = []
    checked = [0]

    def check(name, cond):
        checked[0] += 1
        if not cond:
            failures.append(name)

    # 0: the vocabulary is internally consistent and closed.
    check("adopt-ops-count-12", len(ADOPT_OPS) == 12)
    check("adopt-ops-names-unique", len(ADOPT_OP_NAMES) == len(ADOPT_OPS))
    check("adopt-ops-expected-names",
          ADOPT_OP_NAMES == frozenset({
              "install-pack", "init-store", "create-file", "plant-governance", "import-file",
              "register-unmanaged", "move-file", "retire-file", "repoint-consumer", "enable-hook",
              "render-views", "record-adoption"}))
    # every op's journal metadata is a subset of the REAL _journal primitive set (no drift from the engine).
    check("adopt-ops-journal-subset",
          all(set(op.journal) <= set(JOURNAL_PRIMITIVES) and op.journal for op in ADOPT_OPS))
    # every op declares a reversal and at least one precondition (fail-closed metadata is present).
    check("adopt-ops-reversal-and-precond",
          all(op.reversal and op.preconditions for op in ADOPT_OPS))
    # every declared input field is classified (validate_op can shape-check it).
    check("adopt-ops-fields-classified",
          all(f in _FIELD_KINDS for op in ADOPT_OPS for f in (op.required_inputs + op.optional_inputs)))

    # 1: validate_op accepts the canonical row for EVERY op, and rejects a malformed instance of each.
    for name in sorted(ADOPT_OP_NAMES):
        check("op-{}-canonical-valid".format(name), validate_op(canonical_op(name)).status == VALID)
        bad = canonical_op(name)
        # drop the first required input -> INVALID (missing required field).
        first_req = ADOPT_OPS_BY_NAME[name].required_inputs[0]
        del bad[first_req]
        check("op-{}-missing-required-invalid".format(name), validate_op(bad).status == INVALID)
        # add an unknown key -> INVALID (closed keyset).
        extra = canonical_op(name)
        extra["surprise"] = "x"
        check("op-{}-unknown-key-invalid".format(name), validate_op(extra).status == INVALID)

    # 2: THE out-of-vocabulary discriminator: an op outside ADOPT_OPS is refused CANNOT-EVALUATE, never a
    # silent skip or pass.
    check("op-outside-vocab-cannot-eval",
          validate_op({"op": "delete-everything"}).status == CANNOT_EVALUATE)
    check("op-no-selector-cannot-eval", validate_op({"path": "x"}).status == CANNOT_EVALUATE)
    check("op-not-a-table-cannot-eval", validate_op(["init-store"]).status == CANNOT_EVALUATE)
    # a malformed field shape (absolute path, traversal, bad digest) is INVALID, not a pass.
    check("op-absolute-path-invalid",
          validate_op({"op": "create-file", "path": "/etc/passwd",
                       "content_digest": "sha256:" + "0" * 64}).status == INVALID)
    check("op-traversal-path-invalid",
          validate_op({"op": "create-file", "path": "../escape",
                       "content_digest": "sha256:" + "0" * 64}).status == INVALID)
    check("op-bad-digest-invalid",
          validate_op({"op": "create-file", "path": "a/b", "content_digest": "deadbeef"}).status == INVALID)
    check("op-bad-members-invalid",
          validate_op({"op": "install-pack", "target": "a/b", "members": []}).status == INVALID)

    # 3: validate_plan accepts the canonical plan; rejects each malformed variant.
    check("plan-canonical-valid", validate_plan(canonical_plan()).status == VALID)
    check("plan-not-a-table-cannot-eval", validate_plan([]).status == CANNOT_EVALUATE)
    bad_product = canonical_plan(); bad_product["product"] = "sap"
    check("plan-bad-product-cannot-eval", validate_plan(bad_product).status == CANNOT_EVALUATE)
    bad_op = canonical_plan(); bad_op["ops"] = [{"op": "nuke"}]
    check("plan-op-out-of-vocab-cannot-eval", validate_plan(bad_op).status == CANNOT_EVALUATE)
    empty_ops = canonical_plan(); empty_ops["ops"] = []
    check("plan-empty-ops-invalid", validate_plan(empty_ops).status == INVALID)
    bad_fmt = canonical_plan(); bad_fmt["format"] = "opf.adoption.plan/v2"
    check("plan-bad-format-invalid", validate_plan(bad_fmt).status == INVALID)
    bad_digest = canonical_plan(); bad_digest["plan_digest"] = "nope"
    check("plan-bad-digest-invalid", validate_plan(bad_digest).status == INVALID)
    missing_key = canonical_plan(); del missing_key["inventory_digest"]
    check("plan-missing-key-invalid", validate_plan(missing_key).status == INVALID)
    unknown_key = canonical_plan(); unknown_key["extra"] = 1
    check("plan-unknown-key-invalid", validate_plan(unknown_key).status == INVALID)
    ops_not_list = canonical_plan(); ops_not_list["ops"] = {}
    check("plan-ops-not-list-cannot-eval", validate_plan(ops_not_list).status == CANNOT_EVALUATE)

    # 4: validate_receipt_core accepts the canonical receipt; rejects malformed variants.
    check("receipt-canonical-valid", validate_receipt_core(canonical_receipt_core()).status == VALID)
    check("receipt-not-a-table-cannot-eval", validate_receipt_core("x").status == CANNOT_EVALUATE)
    r_prod = canonical_receipt_core(); r_prod["product"] = "widget"
    check("receipt-bad-product-cannot-eval", validate_receipt_core(r_prod).status == CANNOT_EVALUATE)
    r_disp = canonical_receipt_core(); r_disp["files"][0]["disposition"] = "delete"
    check("receipt-bad-disposition-cannot-eval", validate_receipt_core(r_disp).status == CANNOT_EVALUATE)
    r_fmt = canonical_receipt_core(); r_fmt["format"] = "other"
    check("receipt-bad-format-invalid", validate_receipt_core(r_fmt).status == INVALID)
    r_run = canonical_receipt_core(); r_run["run_id"] = "imp-20260917T120000Z-0123456789abcdef"
    check("receipt-wrong-runid-grammar-invalid", validate_receipt_core(r_run).status == INVALID)
    r_rel = canonical_receipt_core(); r_rel["release"]["anchor_agreement"] = "yes"
    check("receipt-nonbool-anchor-invalid", validate_receipt_core(r_rel).status == INVALID)
    r_manifest = canonical_receipt_core(); r_manifest["release"]["manifest_sha256"] = "nope"
    check("receipt-bad-manifest-digest-invalid", validate_receipt_core(r_manifest).status == INVALID)
    r_file = canonical_receipt_core(); r_file["files"][0]["after_digest"] = "bad"
    check("receipt-bad-file-digest-invalid", validate_receipt_core(r_file).status == INVALID)
    r_miss = canonical_receipt_core(); del r_miss["approval"]
    check("receipt-missing-approval-invalid", validate_receipt_core(r_miss).status == INVALID)
    # a retired file legitimately carries a null after_digest (created file: null before_digest) -> VALID.
    r_null = canonical_receipt_core()
    r_null["files"][0] = {"path": "adopter/old.md", "before_digest": "sha256:" + "6" * 64,
                          "after_digest": None, "disposition": "retire"}
    check("receipt-null-after-on-retire-valid", validate_receipt_core(r_null).status == VALID)

    # 5: validate_outcome_event accepts the canonical event; rejects malformed variants.
    check("event-canonical-valid", validate_outcome_event(canonical_outcome_event()).status == VALID)
    check("event-not-a-table-cannot-eval", validate_outcome_event(3).status == CANNOT_EVALUATE)
    e_kind = canonical_outcome_event(); e_kind["kind"] = "exploded"
    check("event-bad-kind-cannot-eval", validate_outcome_event(e_kind).status == CANNOT_EVALUATE)
    e_fmt = canonical_outcome_event(); e_fmt["format"] = "x"
    check("event-bad-format-invalid", validate_outcome_event(e_fmt).status == INVALID)
    e_dig = canonical_outcome_event(); e_dig["previous_event_digest"] = "nope"
    check("event-bad-prev-digest-invalid", validate_outcome_event(e_dig).status == INVALID)
    e_ts = canonical_outcome_event(); e_ts["recorded_at"] = "yesterday"
    check("event-bad-timestamp-invalid", validate_outcome_event(e_ts).status == INVALID)

    # 6: validate_event_chain accepts a well-formed chain and rejects a broken link / bad genesis.
    root = "sha256:" + "7" * 64
    genesis = canonical_outcome_event(kind="applied", previous_event_digest=root)
    genesis["event_digest"] = "sha256:" + "b" * 64
    second = canonical_outcome_event(kind="premerge-validated",
                                     previous_event_digest=genesis["event_digest"])
    second["event_digest"] = "sha256:" + "c" * 64
    check("chain-valid", validate_event_chain([genesis, second], root).status == VALID)
    check("chain-empty-invalid", validate_event_chain([], root).status == INVALID)
    check("chain-bad-root-cannot-eval", validate_event_chain([genesis], "nope").status == CANNOT_EVALUATE)
    # a genesis that is not `applied` is INVALID.
    non_genesis = canonical_outcome_event(kind="merged", previous_event_digest=root)
    check("chain-nongenesis-first-invalid",
          validate_event_chain([non_genesis], root).status == INVALID)
    # a broken link between events is INVALID.
    broken = canonical_outcome_event(kind="merged", previous_event_digest="sha256:" + "d" * 64)
    broken["event_digest"] = "sha256:" + "e" * 64
    check("chain-broken-link-invalid",
          validate_event_chain([genesis, broken], root).status == INVALID)
    # a run_id mismatch between events is INVALID.
    other_run = canonical_outcome_event(kind="merged", previous_event_digest=genesis["event_digest"])
    other_run["event_digest"] = "sha256:" + "f" * 64
    other_run["run_id"] = "adopt-20260917T120000Z-fedcba9876543210"
    check("chain-runid-mismatch-invalid",
          validate_event_chain([genesis, other_run], root).status == INVALID)

    # 7: change-carries-check discriminators. Each vector FAILS if its corresponding fix is reverted.
    # 7a: a schema field that is a bool or a float (True == 1, 1.0 == 1 in Python) is INVALID, not a
    # silent accept, across validate_plan, validate_receipt_core, and validate_outcome_event.
    for bad_schema in (True, 1.0):
        p_sch = canonical_plan(); p_sch["schema"] = bad_schema
        check("plan-schema-{!r}-typed-invalid".format(bad_schema),
              validate_plan(p_sch).status == INVALID)
        r_sch = canonical_receipt_core(); r_sch["schema"] = bad_schema
        check("receipt-schema-{!r}-typed-invalid".format(bad_schema),
              validate_receipt_core(r_sch).status == INVALID)
        e_sch = canonical_outcome_event(); e_sch["schema"] = bad_schema
        check("event-schema-{!r}-typed-invalid".format(bad_schema),
              validate_outcome_event(e_sch).status == INVALID)
    # 7b: an op path carrying a backslash (a Windows path form) is INVALID via validate_op.
    check("op-backslash-path-invalid",
          validate_op({"op": "create-file", "path": "a\\b",
                       "content_digest": "sha256:" + "0" * 64}).status == INVALID)
    # 7c: a self-linking chain (event_digest == previous_event_digest, two copies) is INVALID.
    loop_root = "sha256:" + "a" * 64
    loop_ev = canonical_outcome_event(previous_event_digest=loop_root)  # event_digest is also "a" * 64
    check("chain-self-loop-invalid",
          validate_event_chain([loop_ev, dict(loop_ev)], loop_root).status == INVALID)
    # 7d: a files[] row missing `disposition` is INVALID (missing required key), not cannot-evaluate.
    r_nodisp = canonical_receipt_core(); del r_nodisp["files"][0]["disposition"]
    check("receipt-missing-disposition-invalid", validate_receipt_core(r_nodisp).status == INVALID)

    # 8: round-3 fix discriminators. Each vector FAILS if its corresponding fix is reverted.
    # 8a (fix A): a chain whose final event_digest returns to the chain root is a cycle, INVALID even
    # though each pairwise link lines up (the seen-set is seeded with the root).
    cyc_root = "sha256:" + "7" * 64
    cyc_g = canonical_outcome_event(previous_event_digest=cyc_root)      # event_digest is "a" * 64
    cyc_e = canonical_outcome_event(kind="merged", previous_event_digest=cyc_g["event_digest"])
    cyc_e["event_digest"] = cyc_root
    check("chain-return-to-root-invalid",
          validate_event_chain([cyc_g, cyc_e], cyc_root).status == INVALID)
    # 8b (fix B): a receipt whose files[] repeats a path is a contradictory record, INVALID; and an
    # install-pack members list with two entries sharing a path is INVALID via validate_op.
    r_dup = canonical_receipt_core()
    r_dup["files"].append({"path": r_dup["files"][0]["path"], "before_digest": "sha256:" + "6" * 64,
                           "after_digest": None, "disposition": "retire"})
    check("receipt-duplicate-file-path-invalid", validate_receipt_core(r_dup).status == INVALID)
    check("op-duplicate-members-path-invalid",
          validate_op({"op": "install-pack", "target": "a/b",
                       "members": [{"path": "a/b", "digest": "sha256:" + "0" * 64},
                                   {"path": "a/b", "digest": "sha256:" + "1" * 64}]}).status == INVALID)
    # 8c (fix C): an approval digest that does not match the receipt's own top-level digest is INVALID (the
    # approval would attest to different content than the receipt records).
    r_appr_plan = canonical_receipt_core(); r_appr_plan["approval"]["plan_digest"] = "sha256:" + "8" * 64
    check("receipt-approval-plan-mismatch-invalid", validate_receipt_core(r_appr_plan).status == INVALID)
    r_appr_inv = canonical_receipt_core(); r_appr_inv["approval"]["inventory_digest"] = "sha256:" + "8" * 64
    check("receipt-approval-inventory-mismatch-invalid",
          validate_receipt_core(r_appr_inv).status == INVALID)
    # 8d (fix D): _is_token rejects a C1 control and the Unicode line/paragraph separators, matching its
    # no-control/no-line-break contract across the whole code-point space.
    check("is-token-c1-control-false", _is_token("a\x85b") is False)
    check("is-token-line-separator-false", _is_token("a\u2028b") is False)

    # 9: premise-shift round (consistency-by-construction + fail-closed robustness). Each vector FAILS if
    # its corresponding fix is reverted.
    # 9a (part 1): a non-string dict key is a schema violation, refused (INVALID), NEVER a crash. A
    # validator never raises on adversarial already-parsed input. Reproduces codex's mixed-type-key vector.
    p_key = canonical_plan(); p_key.update({0: "x", "unknown": "y"})
    check("plan-nonstring-key-refused", validate_plan(p_key).status == INVALID)
    r_key = canonical_receipt_core(); r_key[0] = "x"
    check("receipt-nonstring-key-refused", validate_receipt_core(r_key).status == INVALID)
    e_key = canonical_outcome_event(); e_key[0] = "x"
    check("event-nonstring-key-refused", validate_outcome_event(e_key).status == INVALID)
    o_key = canonical_op("create-file"); o_key[0] = "x"
    check("op-nonstring-key-refused", validate_op(o_key).status == INVALID)
    # a native (non-string) key mixed with a string unknown key still sorts and refuses, not raises.
    r_mixkey = canonical_receipt_core(); r_mixkey.update({0: "x", "zzz": "y"})
    check("receipt-mixed-key-types-refused", validate_receipt_core(r_mixkey).status == INVALID)
    # a non-string key mixed with a string unknown key INSIDE a files[] row hits the row-level key sort
    # with mixed types; it too refuses, never raises (a single key alone never forces a comparison).
    r_filekey = canonical_receipt_core(); r_filekey["files"][0].update({0: "x", "zzz": "y"})
    check("receipt-files-row-nonstring-key-refused", validate_receipt_core(r_filekey).status == INVALID)

    # 9b (part 2): disposition <-> digest reconciliation (spec 14.2). Each contradictory combination is
    # INVALID; each combination the spec leaves open (migrate's after side) stays VALID.
    def _file_row(disp, before, after):
        r = canonical_receipt_core()
        r["files"][0] = {"path": "adopter/legacy.md", "before_digest": before,
                         "after_digest": after, "disposition": disp}
        return r
    _D6, _D7 = "sha256:" + "6" * 64, "sha256:" + "7" * 64
    check("disp-retire-after-present-invalid",
          validate_receipt_core(_file_row("retire", _D6, _D7)).status == INVALID)
    check("disp-move-after-present-invalid",
          validate_receipt_core(_file_row("move", _D6, _D7)).status == INVALID)
    check("disp-keep-after-null-invalid",
          validate_receipt_core(_file_row("keep", _D6, None)).status == INVALID)
    check("disp-keep-before-null-invalid",
          validate_receipt_core(_file_row("keep", None, _D6)).status == INVALID)
    check("disp-retire-before-null-invalid",
          validate_receipt_core(_file_row("retire", None, None)).status == INVALID)
    check("disp-keep-before-neq-after-invalid",
          validate_receipt_core(_file_row("keep", _D6, _D7)).status == INVALID)
    check("disp-migrate-before-null-invalid",
          validate_receipt_core(_file_row("migrate", None, _D6)).status == INVALID)
    # migrate leaves its after side to the engine (disclosed residual): both present and null are VALID.
    check("disp-migrate-after-present-valid",
          validate_receipt_core(_file_row("migrate", _D6, _D7)).status == VALID)
    check("disp-migrate-after-null-valid",
          validate_receipt_core(_file_row("migrate", _D6, None)).status == VALID)
    # a consistent retire (before present, after null) and a consistent keep (before == after) stay VALID.
    check("disp-retire-consistent-valid",
          validate_receipt_core(_file_row("retire", _D6, None)).status == VALID)
    check("disp-keep-consistent-valid",
          validate_receipt_core(_file_row("keep", _D6, _D6)).status == VALID)

    # 9c (part 3): a duplicate import_runs run_id (here with a conflicting acceptance_digest) is INVALID.
    r_dup_run = canonical_receipt_core()
    r_dup_run["import_runs"].append({"run_id": r_dup_run["import_runs"][0]["run_id"],
                                     "acceptance_digest": "sha256:" + "8" * 64})
    check("import-runs-duplicate-runid-invalid", validate_receipt_core(r_dup_run).status == INVALID)

    # 9d (part 3 sweep): every identifier/label token-list is a set; a duplicate entry is INVALID.
    r_dup_tx = canonical_receipt_core(); r_dup_tx["transaction_ids"] = ["TX-1", "TX-1"]
    check("transaction-ids-duplicate-invalid", validate_receipt_core(r_dup_tx).status == INVALID)
    r_dup_chk = canonical_receipt_core(); r_dup_chk["required_checks"] = ["A-STORE", "A-STORE"]
    check("required-checks-duplicate-invalid", validate_receipt_core(r_dup_chk).status == INVALID)
    r_dup_probe = canonical_receipt_core(); r_dup_probe["probes"] = ["p", "p"]
    check("probes-duplicate-invalid", validate_receipt_core(r_dup_probe).status == INVALID)
    r_dup_res = canonical_receipt_core(); r_dup_res["coverage_residuals"] = ["res-a", "res-a"]
    check("coverage-residuals-duplicate-invalid", validate_receipt_core(r_dup_res).status == INVALID)

    # 10: round-4 field-precision discriminators (a field's predicate matches its real semantics, not a more
    # permissive proxy). Each vector FAILS if its corresponding fix is reverted.
    # 10a (fix 1, file-path precision): a FILE operand can never be the store-root directory `.`, so every
    # file field rejects it (INVALID), while a DIRECTORY / store-root field still accepts it (VALID).
    # Reproduces codex round-4 #2 (create-file path ".", and the same for retire/move/enable-hook/record).
    for op_name, file_field in (
            ("create-file", "path"), ("retire-file", "path"), ("move-file", "source"),
            ("move-file", "destination"), ("enable-hook", "registration_path"),
            ("record-adoption", "receipt_path"), ("register-unmanaged", "entry")):
        dot = canonical_op(op_name); dot[file_field] = "."
        check("op-{}-{}-dot-invalid".format(op_name, file_field), validate_op(dot).status == INVALID)
    # an install-pack member names a file, so a member path `.` is INVALID.
    m_dot = canonical_op("install-pack"); m_dot["members"] = [{"path": ".", "digest": "sha256:" + "0" * 64}]
    check("op-install-pack-member-dot-invalid", validate_op(m_dot).status == INVALID)
    # a receipt per-file record names a file, so a files[] path `.` is INVALID.
    r_file_dot = canonical_receipt_core(); r_file_dot["files"][0]["path"] = "."
    check("receipt-file-path-dot-invalid", validate_receipt_core(r_file_dot).status == INVALID)
    # DIRECTORY / store-root fields still accept `.`: install-pack target, init-store store_root, receipt
    # store_root. These are the counter-vectors proving the fix did not over-reach into directory fields.
    t_dot = canonical_op("install-pack"); t_dot["target"] = "."
    check("op-install-pack-target-dot-valid", validate_op(t_dot).status == VALID)
    s_dot = canonical_op("init-store"); s_dot["store_root"] = "."
    check("op-init-store-root-dot-valid", validate_op(s_dot).status == VALID)
    r_root_dot = canonical_receipt_core(); r_root_dot["store_root"] = "."
    check("receipt-store-root-dot-valid", validate_receipt_core(r_root_dot).status == VALID)

    # 10b (fix 2, import run-id grammar): the op import_run_id field and receipt import_runs[].run_id
    # validate against the AUTHORITATIVE `imp-<UTCSTAMP>Z-<hash16>` grammar (mirrored from _opf_import), not a
    # generic token. Reproduces codex round-4 #3 (import_runs run_id "../escape" was VALID; must be INVALID).
    op_esc = canonical_op("import-file"); op_esc["import_run_id"] = "../escape"
    check("op-import-run-id-traversal-invalid", validate_op(op_esc).status == INVALID)
    op_bad = canonical_op("import-file"); op_bad["import_run_id"] = "imp-not-a-valid-id"
    check("op-import-run-id-nonconforming-invalid", validate_op(op_bad).status == INVALID)
    op_good = canonical_op("import-file"); op_good["import_run_id"] = "imp-20260101T000000Z-abcdef0123456789"
    check("op-import-run-id-valid", validate_op(op_good).status == VALID)
    r_esc = canonical_receipt_core(); r_esc["import_runs"][0]["run_id"] = "../escape"
    check("receipt-import-run-id-traversal-invalid", validate_receipt_core(r_esc).status == INVALID)
    r_bad = canonical_receipt_core(); r_bad["import_runs"][0]["run_id"] = "imp-nope"
    check("receipt-import-run-id-nonconforming-invalid", validate_receipt_core(r_bad).status == INVALID)
    r_good = canonical_receipt_core()
    r_good["import_runs"][0]["run_id"] = "imp-20260101T000000Z-abcdef0123456789"
    check("receipt-import-run-id-valid", validate_receipt_core(r_good).status == VALID)
    # the mirror MUST match _opf_import's authoritative grammar byte for byte so it cannot drift; import the
    # sibling only to read its pattern (no filesystem, journal, or network at self-test time).
    import _opf_import  # noqa: E402
    check("import-run-id-mirror-matches-sibling",
          _IMPORT_RUN_ID_RE.pattern == _opf_import._RUN_ID_RE.pattern)

    # 11: round-5 fix discriminators. Each vector FAILS if its corresponding fix is reverted.
    # 11a (fix A, path control-character rejection): a path field carrying an embedded control character
    # (a newline, a tab, DEL, a C1 control, or a Unicode line/paragraph separator) is INVALID, so it cannot
    # become a line-injection vector for the line-oriented TOML the engine PRs parse. Applies to every path
    # field, file and dir, since both route through _is_contained_relpath.
    check("op-newline-filepath-invalid",
          validate_op({"op": "create-file", "path": "a\nb",
                       "content_digest": "sha256:" + "0" * 64}).status == INVALID)
    check("op-tab-filepath-invalid",
          validate_op({"op": "create-file", "path": "a\tb",
                       "content_digest": "sha256:" + "0" * 64}).status == INVALID)
    # a DIRECTORY / store-root field is covered too (it routes through the same predicate).
    ctrl_root = canonical_op("init-store"); ctrl_root["store_root"] = "a\nb"
    check("op-newline-dirpath-invalid", validate_op(ctrl_root).status == INVALID)
    r_ctrl_root = canonical_receipt_core(); r_ctrl_root["store_root"] = "sub\ndir"
    check("receipt-newline-store-root-invalid", validate_receipt_core(r_ctrl_root).status == INVALID)

    # 11b (fix B, opaque-table string-key enforcement): governance and wiring stay CONTENT-OPAQUE (their
    # values / inner schema are engine-deferred, not validated), but a non-string KEY is structurally
    # malformed for any TOML/JSON table, so it is INVALID; a string-keyed table with arbitrary opaque
    # values stays VALID (the content-opacity counter-vector).
    r_gov_key = canonical_receipt_core(); r_gov_key["governance"] = {0: "x"}
    check("receipt-governance-nonstring-key-invalid", validate_receipt_core(r_gov_key).status == INVALID)
    r_wir_key = canonical_receipt_core(); r_wir_key["wiring"] = {1.5: "y"}
    check("receipt-wiring-nonstring-key-invalid", validate_receipt_core(r_wir_key).status == INVALID)
    r_gov_ok = canonical_receipt_core(); r_gov_ok["governance"] = {"policy": {"nested": [1, 2]}}
    check("receipt-governance-string-key-opaque-valid", validate_receipt_core(r_gov_ok).status == VALID)

    # 11c: fix C's discriminator is the "chain-empty-invalid" vector in section 6 above (an empty chain is
    # INVALID, an empty required list per the module outcome model, not CANNOT-EVALUATE); reverting fix C
    # flips it back to CANNOT-EVALUATE and fails the self-test.

    # 11d (fix D, lowercase-only digest guarantee): the digest grammar is lowercase hex only, so an
    # UPPERCASE-hex digest is REJECTED. This flip-guards the [0-9a-f]{64} regex: loosening it to
    # [0-9a-fA-F]{64} would newly accept these vectors and flip the self-test red.
    check("digest-uppercase-hex-rejected", _is_digest("sha256:" + "A" * 64) is False)
    r_upper = canonical_receipt_core(); r_upper["release"]["manifest_sha256"] = "sha256:" + "A" * 64
    check("receipt-uppercase-digest-invalid", validate_receipt_core(r_upper).status == INVALID)

    # 12: round-7 fix discriminators. Each vector FAILS if its corresponding fix is reverted.
    # 12a (fix B, second-genesis rejection): an append-only chain has EXACTLY ONE genesis. A non-genesis
    # position (i>0) carrying the genesis kind `applied` is a second genesis, INVALID, even when it chains
    # correctly, shares the run_id, and repeats no digest. This vector is a two-event chain whose only defect
    # is the second `applied`; reverting the non-genesis-position check flips it back to VALID.
    sg_root = "sha256:" + "7" * 64
    sg_genesis = canonical_outcome_event(kind="applied", previous_event_digest=sg_root)
    sg_genesis["event_digest"] = "sha256:" + "b" * 64
    sg_second = canonical_outcome_event(kind="applied", previous_event_digest=sg_genesis["event_digest"])
    sg_second["event_digest"] = "sha256:" + "c" * 64
    check("chain-second-genesis-invalid",
          validate_event_chain([sg_genesis, sg_second], sg_root).status == INVALID)

    # 12b (fix C, digest PREFIX discriminator): the digest grammar requires the literal `sha256:` prefix, not
    # merely 64 lowercase-hex characters. A wrong-prefix digest (`sha1:` + 64 hex) and a bare 64-hex with no
    # prefix are both REJECTED. This flip-guards the PREFIX portion of _DIGEST_RE that the 11d lowercase-hex
    # vectors do not cover: dropping the `sha256:` prefix (accepting bare hex) or altering it (accepting
    # `sha1:`) would newly accept these vectors and flip the self-test red.
    check("digest-wrong-prefix-rejected", _is_digest("sha1:" + "0" * 64) is False)
    check("digest-no-prefix-rejected", _is_digest("0" * 64) is False)
    r_wrongpref = canonical_receipt_core(); r_wrongpref["release"]["manifest_sha256"] = "sha1:" + "0" * 64
    check("receipt-wrong-prefix-digest-invalid", validate_receipt_core(r_wrongpref).status == INVALID)

    # 12c (fix D, event-chain linkage after an INVALID intermediate event): this is a finding-QUALITY change
    # with NO status transition, so no status-judged vector can discriminate it (the self-test judges on the
    # returned status, never by grepping finding text, per spec 8). Once an event is itself INVALID its digest
    # fields cannot be trusted, so validate_event_chain now suppresses downstream linkage findings rather than
    # comparing later events against stale prior state and emitting misleading, structurally-incorrect
    # findings; the chain stays INVALID either way. The intact-chain path is unchanged and stays guarded by
    # the section-6 chain vectors (all built from individually-VALID events), so no new vector is added here.

    # 13: round-9 digest length/charset coverage discriminators. The 11d (uppercase-hex) and 12b (prefix)
    # vectors flip-guard the CHARSET-case and PREFIX portions of _DIGEST_RE, but neither exercises its LENGTH
    # bound nor its hex-only charset within the lowercase range. These vectors close that gap: a digest whose
    # hex body is the wrong LENGTH (not exactly 64) and a right-length digest carrying a NON-HEX character
    # (e.g. 'g') are both REJECTED. Loosening the {64} count or widening [0-9a-f] to any letter would newly
    # accept these vectors and flip the self-test red.
    check("digest-wrong-length-rejected", _is_digest("sha256:" + "0" * 63) is False)
    check("digest-non-hex-charset-rejected", _is_digest("sha256:" + "g" * 64) is False)
    r_badlen = canonical_receipt_core(); r_badlen["release"]["manifest_sha256"] = "sha256:" + "0" * 63
    check("receipt-wrong-length-digest-invalid", validate_receipt_core(r_badlen).status == INVALID)
    r_nonhex = canonical_receipt_core(); r_nonhex["release"]["manifest_sha256"] = "sha256:" + "g" * 64
    check("receipt-non-hex-digest-invalid", validate_receipt_core(r_nonhex).status == INVALID)

    from _opf_adopt_plan import self_test as planning_self_test
    planning_rc = planning_self_test()
    check("read-only-investigate-plan-suite",
          type(planning_rc) is int and planning_rc == 0)

    if failures:
        print("OPF-ADOPT SELF-TEST: FAIL ({} of {} checks failed)".format(len(failures), checked[0]))
        for f in failures:
            print("  FAILED: {}".format(f))
        return 1
    print("OPF-ADOPT SELF-TEST: PASS ({} schema / vocabulary / planning checks)".format(
        checked[0]))
    return 0


def investigate(product_root, *, sources, targets=()):
    """Read-only investigation; see _opf_adopt_plan for scope and residuals."""
    from _opf_adopt_plan import investigate as investigate_readonly
    return investigate_readonly(product_root, sources=sources, targets=targets)


def plan(product_root, *, sources, expected_observation_digest, product, decisions,
         ops, now, run_nonce, targets=()):
    """Freeze an inert proposal. This does not capture acceptance or authorize apply."""
    from _opf_adopt_plan import plan as plan_readonly
    return plan_readonly(
        product_root, sources=sources, targets=targets,
        expected_observation_digest=expected_observation_digest,
        product=product, decisions=decisions, ops=ops, now=now, run_nonce=run_nonce,
    )


def main():
    args = sys.argv[1:]
    if "--self-test" in args or "--selftest" in args:
        return self_test()
    print("usage: _opf_adopt.py --self-test (a library module; the adoption verb is a later PR)",
          file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
