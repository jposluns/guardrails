#!/usr/bin/env python3
"""OPF adoption convergence: inert schemas and read-only investigation/planning.

PR-A supplies the `AdoptionPlan`, receipt-core and outcome-event schemas, the closed eleven-op
ADOPT_OPS vocabulary, and pure fail-closed validators. Validators receive already-parsed objects and
decide well-formedness only; they never read bytes themselves.

PR-B adds the investigate() and plan() library entry points, implemented by the sibling
_opf_adopt_plan module. They inspect an explicitly scoped repository and return canonical inert
observation, inventory and plan bytes. They never persist those outputs or run an operation.
The registered self-test also exercises synthetic temporary fixtures.

The plan format is `opf.adoption.plan/v2` (OPF-SPEC 14.1). Beyond the ordered op program it binds the
observed revision, the store identity (root, machine store, first adoption or re-adoption), every source
with its digest, disposition, managed-destination occupancy and preservation destination, the exact
file-level effects, the tool release with its independent anchor, the prompt pack, the enforcement pack
per platform with its residuals, the completion-check roster with the retirement rule, and the import
policy with its migrate scope. v2 replaces v1 in place and a v1 marker is refused: no v1 plan was ever
persisted. A `migrate` source is kept for post-adoption import (spec 14.2), so a v2 plan carries no
import op; the former import-file vocabulary row retired with the import engine.

PR-C2 adds explicit HTTPS gathering and non-executing quarantine through the lazy
public gather_release() wrapper. Observations confer no trust or apply authority.

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

Validators and planning are offline; release gathering is an explicit HTTPS edge.
Stdlib only, fail-closed. It lives under `opf/tools/` and imports ONLY sibling `opf/tools/`
modules (the shared outcome model from `_opf_store`, the closed effect vocabulary `OP_KINDS` from
`_journal` as inert data), so it introduces no upward edge into `tools/` and the standalone-closure property
holds.

Exit convention (the repo's gates and the sibling OPF units): 0 clean, 1 a finding, 2 cannot-evaluate.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    sys.stderr.write(
        "error: _opf_adopt.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
        "Nothing was run (cannot evaluate).\n"
        % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
    raise SystemExit(2)

import re
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
# Single-source the outcome model (U1) rather than re-declaring it, exactly as _opf_schema does.
from _opf_store import VALID, INVALID, CANNOT_EVALUATE  # noqa: E402
# Pure path constructors and names, never I/O: the adoption preimage home, the Move archive root and the
# machine-store naming that a plan's preservation destinations and store identity are checked against, and
# the store-root control directories and pointers that no adoption destination may name.
from _opf_store import (  # noqa: E402
    ARCHIVE_REL, IMPORTED_REL, LOCAL_POINTER_REL, MANIFEST_NAME, POINTER_REL, RESERVED_MACHINE_SUBDIRS,
    STORE_ROOT_CONTROL_DIRS, WORKING_DIRNAME, evidence_run, retire_preimage)
# The shared bare SemVer grammar, for the release and prompt-pack versions a plan binds.
from _semver import _parse as _parse_semver  # noqa: E402
# The closed journal effect vocabulary, imported as INERT DATA (a tuple of primitive names). This is a
# reference, never a call: PR-A performs no transaction. Sourcing it here keeps every op's declared journal
# semantics provably a subset of the real primitive set, so the vocabulary cannot drift from the engine.
from _journal import OP_KINDS as JOURNAL_PRIMITIVES  # noqa: E402


# --- fixed formats, versions, and closed vocabularies ------------------------------------------------

SCHEMA_VERSION = 1                          # the one schema version this module understands
PLAN_FORMAT = "opf.adoption.plan/v2"        # the AdoptionPlan format marker (spec 14.1 binding roster)
RECEIPT_FORMAT = "opf.adoption.receipt/v1"  # the immutable receipt-core format marker (b.4)
EVENT_FORMAT = "opf.adoption.event/v1"      # the append-only outcome-event format marker (b.4)

# Product identity (b.1): one bundle converges either product. Neutral tokens, no AIQT-profile leakage.
PRODUCT_IDENTITIES = ("aiqt", "opf")

# Per-file disposition vocabulary (OPF-SPEC 14.2, reconciled in b.5): the closed set of resolutions a plan
# may record for a foreign file. `keep` is realized by register-unmanaged, `move` by move-file, `retire` by
# retire-file. `migrate` keeps the source for post-adoption import (spec 14.2): the plan records its source
# row and import scope and mints no op.
DISPOSITIONS = ("keep", "migrate", "move", "retire")

# Plan-v2 binding vocabularies (OPF-SPEC 14.1). The tokens are this module's spellings of the spec's concepts.
# Investigation's first-adoption / re-adoption distinction (spec 14), bound in the plan's store identity.
ADOPTION_KINDS = ("first-adoption", "re-adoption")
# The clean-start completion-check roster, in the spec's numbered order, and the one retirement rule:
# retirement requires green checks and matching bytes.
COMPLETION_CHECKS = ("authority-freshness", "discovery-accounting", "preservation-restore",
                     "operational-readiness", "wiring", "retirement-readiness")
RETIREMENT_RULES = ("green-checks-and-matching-bytes",)
# The enforcement-pack floor (spec 14.1): CI checks, staged-snapshot pre-commit checks, and for each
# supported assistant platform a verified deny hook or instructions. A plan binds one row per platform, in
# this order, and each row's means must be one its platform allows.
ENFORCEMENT_MEANS = {
    "ci": ("ci-checks",),
    "pre-commit": ("staged-pre-commit",),
    "claude-code": ("deny-hook", "instructions"),
    "codex": ("deny-hook", "instructions"),
    "gemini-cli": ("deny-hook", "instructions"),
    "cursor": ("deny-hook", "instructions"),
}
ENFORCEMENT_PLATFORMS = tuple(ENFORCEMENT_MEANS)
_ENFORCEMENT_MEANS_ALL = tuple(sorted(set(m for means in ENFORCEMENT_MEANS.values() for m in means)))
# The closed residual disclosures of the enforcement floor (spec 14.1): server-side branch protection is
# adopter-attested, and per-clone hook installation and bypass, canonical hand edits, shell or interpreter
# wrapping, same-user tampering and unverified platform denial are not eliminated by the pack.
ENFORCEMENT_RESIDUALS = ("server-side-protection-adopter-attested", "per-clone-installation-and-bypass",
                         "canonical-hand-edits", "shell-or-interpreter-wrapping", "same-user-tampering",
                         "unverified-platform-denial")
# The residuals the spec requires the pack to disclose: each must appear on at least one platform row.
PACK_RESIDUALS = ("per-clone-installation-and-bypass", "canonical-hand-edits",
                  "shell-or-interpreter-wrapping", "same-user-tampering", "unverified-platform-denial")
# The disclosure each means requires on its own row: CI relies on adopter-attested server-side protection,
# the staged pre-commit check discloses per-clone installation, a deny hook discloses wrapping around it, and
# instructions on a platform without verifiable denial disclose that the denial is unverified.
ENFORCEMENT_REQUIRED_RESIDUALS = {
    "ci-checks": ("server-side-protection-adopter-attested",),
    "staged-pre-commit": ("per-clone-installation-and-bypass",),
    "deny-hook": ("shell-or-interpreter-wrapping",),
    "instructions": ("unverified-platform-denial",),
}
# The ops that can install an enforcement member: a pack or governance creation, or a hook registration.
ENFORCEMENT_INSTALL_OPS = ("install-pack", "plant-governance", "enable-hook")
# The store control area an adoption plan never selects or writes, in every homes generation: the reserved
# children archive/, imported/, staging/ and journals/ (spec 14.2, which carries no homes qualifier) and the
# legacy imports tree homes 1 already reserves. The plan's own preservation copies under this run's adoption
# archive and move destinations strictly beneath the Move archive are the only writes that land there.
ADOPTION_CONTROL_ROOTS = tuple(WORKING_DIRNAME + "/" + name for name in RESERVED_MACHINE_SUBDIRS)
# The store tree's own ignore file, where homes-2 init renders the managed block (spec 4.2). No adoption
# destination may name it; composed under the store root like the control area.
STORE_GITIGNORE_REL = WORKING_DIRNAME + "/.gitignore"
# The import policy (spec 8.3 and 14.1): an absent historical field is accounted for by an `unrecorded` row
# carrying one of these closed reasons, unmappable text is retained verbatim, and the skip policy says
# whether a migrate-disposed source may complete through a recorded skip instead of an imported record.
MISSINGNESS_REASONS = ("not_recorded_in_source", "unparsed", "ambiguous", "conflicting", "not_applicable")
UNPARSED_POLICIES = ("retain-verbatim",)
SKIP_POLICIES = ("no-skip", "attributed-skip")

# The append-only outcome-event kinds (b.4). `applied` is the genesis outcome; the rest chain after it.
OUTCOME_EVENTS = ("applied", "premerge-validated", "merged", "postmerge-validated",
                  "failed", "recovered", "signed-off")

# Digest form, matching the store's exactly ("sha256:" + 64 lowercase hex).
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}\Z")
# RFC 3339 UTC instant, SHAPE only (Z or +00:00; optional fractional seconds). The engine PRs bind to
# _opf_schema's calendar-validated check; PR-A validates the lexical shape so a receipt/event timestamp is
# well-formed. A native (unquoted) TOML datetime is a non-string and is rejected as a wrong type.
_TS_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]+)?(Z|\+00:00)\Z")
# The adoption run-id grammar `adopt-<UTCSTAMP>Z-<hash16>`, analogous to the import `imp-` grammar. It is
# a SHAPE oracle for the finalizer; PR-A neither mints nor parses beyond this shape.
_RUN_ID_RE = re.compile(r"^adopt-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{16}\Z")
# The IMPORT run-id grammar `imp-<UTCSTAMP>Z-<hash16>`, the AUTHORITATIVE grammar of the store's import
# home (_opf_store: the `import` prefix of _HOME_RUN_PREFIXES plus _HOME_RUN_SUFFIX). It is MIRRORED here
# (this module validates an already-parsed receipt/plan) and MUST match that grammar byte for byte; the
# self-test asserts the equality so the mirror cannot drift. A receipt import-run id is validated against
# THIS grammar, not a generic token check, so a traversing or malformed id (e.g. "../escape") is refused,
# never accepted.
_IMPORT_RUN_ID_RE = re.compile(r"^imp-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{16}\Z")
# The observed revision a plan binds (spec 14.1): a git object id of 40 (SHA-1) or 64 (SHA-256) lowercase
# hex digits. Investigation never enters .git, so the caller supplies it and only its shape is checked.
_REVISION_RE = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?\Z")

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
# soundness): (a) the `migrate` disposition's after_digest PRESENCE, because under spec 14.2 an occupying
# migrate source is archived and its managed destination then initialized or rendered, while a non-occupying
# one stays frozen live until its recorded retirement removes it, so the after side depends on occupancy and
# on the receipt's stage and only migrate's before_digest (a pre-existing file) is pinned here; (b) equality
# of a plan's own plan_digest/inventory_digest with a receipt's, or of an import run's acceptance_digest or
# the release manifest_sha256 with any other field, because these bind ACROSS artefacts (a plan, a receipt,
# a release) or to real bytes, and a single-artefact validator cannot reconcile them; (c) duplicate-freeness
# / cross-references WITHIN the plan `ops` list (two ops touching one path, a create then an incompatible
# op), because `ops` is an ordered program, not a keyed collection, and its cross-op preconditions are
# apply-time engine checks. These need a second artefact, real bytes, or the engine, which PR-A never
# touches. A v2 plan's own summaries are the exception, because both sides live in the one artefact: its
# `effects` must equal the effects its ops and sources name, no two of those effects may create, replace or
# repoint one path (the frozen manifest's registration chain is one sequential rewrite, checked link by
# link, and a file at the manifest is created only by init-store's scaffold), no creation may land on a
# source left live at apply, no replacement or repointing may rewrite such a source (it stays
# byte-identical until its recorded retirement, spec 14.1 and 14.2), and its `sources` must correspond one
# to one with its disposition ops.
#
# Plan-v2 binding residuals (spec 14.1), shape and internal consistency only: the observed `revision` is
# caller-asserted (investigation never enters .git); the release anchor agreement compares the two recorded
# digests and never fetches the anchor; prompt-pack digests name bytes this module never reads; enforcement
# members are caller-declared path/digest pairs, each tied to an install-pack, plant-governance or
# enable-hook row of the same plan that installs exactly those bytes, but never resolved against a real
# enforcement pack, whose payload and build-time denial verification this module never reads; the residual
# tokens are closed and each means' required disclosure is enforced, which proves a disclosure is recorded,
# not that it is complete for a live platform; a source's `occupying` flag is the planner's classification
# against the planned managed destinations, which a lone plan cannot re-derive. `effects` enumerates every
# file output an op names (init-store and render-views members, the record-adoption receipt core, pack,
# governance and hook installs, moves, retires and repointings) plus source preservation copies and migrate
# removals; the member, receipt and hook digests are caller-asserted postimages, and the append-only outcome
# events that record-adoption later chains to the receipt core record apply outcomes, so they are bound by
# that chain rather than enumerated here. Each register-unmanaged row is the manifest replacement it performs:
# the rows chain in program order from init-store's scaffolded manifest, while the first link of a store that
# already resolves names observed bytes this lone plan cannot see (the planner binds it to the observation).
#
# Control area (spec 14.2, no homes qualifier): in every homes generation a source, a kept entry, a creation,
# a replacement or a repointing that equals, contains or lies within ADOPTION_CONTROL_ROOTS is INVALID, the
# plan's own adoption-archive preservation copies and move destinations strictly beneath the Move archive
# excepted; homes 2 adds validate_op's operand refusal on top. Required program: a first adoption must carry
# init-store; a re-adoption may be a store that already resolves, which takes none, so the validator cannot
# require it there. render-views is not required here because the declared view set is the manifest's, which
# a lone plan cannot re-derive (the planner requires it whenever views are declared), and record-adoption is
# never required: its receipt core binds this plan's own plan_digest and the approval recorded after the plan
# freezes, so a plan cannot bind that core's final digest, and the spec 14 receipt obligation falls to apply
# and completion. Re-adoption binds the adoption kind only: the plan pins no ancestral counter snapshot (spec
# 8.2), and a resolved store's counters.toml, inside the excluded machine store, is digest-bound nowhere in
# the plan or inventory; selecting and pinning the seeding snapshot is the later seed step's
# (_opf_init_operation.read_ancestral_counter_seed), not this plan's. Frame: the store tree, Move archive,
# control roots, directory homes, adoption preimage homes and receipt check compose under store_root, while
# source paths are spelled from the product root; the planner emits store_root ".", where the two frames
# coincide, so the product-relative retire_preimage homes it records already satisfy the composed check.
# validate_plan refuses any other store_root, so a plan is never judged in a root apply does not share
# (apply resolves protected destinations at the product root).


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
    the store's import home, see _IMPORT_RUN_ID_RE), not a generic token: a traversing or malformed id is
    refused."""
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
#   "members"   a list of {path (file), digest} tables, each path relative to the op's target or store_root:
#               the exact files an install-pack, init-store or render-views row writes.
# Path-field classification, grounded in each field's meaning: a create/retire/move/import/enable-hook/
# record-adoption operand names a FILE (path/source/destination/registration_path/receipt_path/entry ->
# "filepath"); a store root or install root names a DIRECTORY (store_root/target -> "dirpath").
_FIELD_KINDS = {
    "target": "dirpath", "store_root": "dirpath", "path": "filepath", "source": "filepath",
    "destination": "filepath", "registration_path": "filepath", "receipt_path": "filepath",
    "entry": "filepath",
    "content_digest": "digest", "source_digest": "digest", "preimage_digest": "digest",
    "old_digest": "digest", "new_digest": "digest",
    "receipt_core_digest": "digest",
    "source_member": "token", "plugin_entry": "token", "note": "token",
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
        "init-store", ("store_root", "members"), ("mkdir", "create"),
        "remove the scaffolded store, restoring NOT-ADOPTED",
        ("resolve_store reports NOT-ADOPTED at store_root; every foreign .working file carries a "
         "disposition first (never a blind opf init over populated content); the scaffold writes exactly "
         "its members, the frozen machine store's manifest among them",)),
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
        "register-unmanaged", ("entry", "old_digest", "new_digest"), ("write",),
        "restore the manifest preimage (drop the [unmanaged] entry)",
        ("the manifest [unmanaged] entry is emitted via emit_checked; no collision with a managed name; the "
         "frozen store manifest's live bytes match old_digest and the rewritten manifest matches new_digest",),
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
        "render-views", ("store_root", "members"), ("create", "write"),
        "remove the views this op created (the render engine refuses to write over an invalid store)",
        ("delegates to the render engine, which itself refuses to write over an invalid store; it writes "
         "exactly its members, one per declared view destination",)),
    AdoptOp(
        "record-adoption", ("receipt_path", "receipt_core_digest"), ("create", "write"),
        "remove or restore the receipt artefacts to their prior state",
        ("writes the immutable receipt core plus the append-only outcome events (b.4)",)),
)

# The op-name -> AdoptOp map and the closed name set. A plan op outside this set is a cannot-evaluate.
ADOPT_OPS_BY_NAME = {op.name: op for op in ADOPT_OPS}
ADOPT_OP_NAMES = frozenset(ADOPT_OPS_BY_NAME)
# The ops that carry a members list, and the directory field each member path is relative to.
_MEMBER_ROOTS = {"install-pack": "target", "init-store": "store_root", "render-views": "store_root"}


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
        "old_digest": _ZERO, "new_digest": _ZERO,
        "receipt_core_digest": _ZERO,
        "source_member": ".aiqt/core/rules/rules.toml", "plugin_entry": "opf-governance",
        "members": [{"path": "adopter/.aiqt/core/rules/rules.toml", "digest": _ZERO}],
    }
    op = ADOPT_OPS_BY_NAME[name]
    row = {"op": name}
    for field in op.required_inputs:
        row[field] = _sample[field]
    # init-store scaffolds the default machine store and render-views writes a declared view, so their
    # store-relative members sit inside the store tree rather than at the pack sample.
    scaffold = {"init-store": WORKING_DIRNAME + "/toml/" + MANIFEST_NAME,
                "render-views": WORKING_DIRNAME + "/TODO.md"}
    if name in scaffold:
        row["members"] = [{"path": scaffold[name], "digest": _ZERO}]
    return row


def canonical_plan_bindings():
    """The caller-supplied plan-v2 binding inputs (spec 14.1, PLAN_BINDING_INPUTS) in minimal VALID form:
    the observed revision, the tool release with its independent anchor, the prompt pack, one enforcement
    row per platform with its residuals, and the skip policy. The planner derives every other v2 field.
    Each call returns fresh tables."""
    def row(platform, means, path, residuals):
        return {"platform": platform, "means": means,
                "members": [{"path": path, "digest": "sha256:" + "9" * 64}], "residuals": list(residuals)}
    return {
        "revision": "0123456789abcdef0123456789abcdef01234567",
        "release": {"version": "1.3.0", "manifest_sha256": "sha256:" + "3" * 64,
                    "anchor": "release-tag", "anchor_sha256": "sha256:" + "3" * 64},
        "prompt_pack": {"version": "0.1.0", "digest": "sha256:" + "8" * 64},
        "enforcement": [
            row("ci", "ci-checks", ".github/workflows/opf.yml", ["server-side-protection-adopter-attested"]),
            row("pre-commit", "staged-pre-commit", ".opf/hooks/pre-commit",
                ["per-clone-installation-and-bypass", "canonical-hand-edits", "same-user-tampering"]),
            row("claude-code", "deny-hook", ".claude/settings.json",
                ["shell-or-interpreter-wrapping", "same-user-tampering"]),
            row("codex", "instructions", "AGENTS.md",
                ["unverified-platform-denial", "shell-or-interpreter-wrapping"]),
            row("gemini-cli", "instructions", "GEMINI.md",
                ["unverified-platform-denial", "shell-or-interpreter-wrapping"]),
            row("cursor", "instructions", ".cursor/rules/opf.mdc",
                ["unverified-platform-denial", "shell-or-interpreter-wrapping"]),
        ],
        "skip_policy": "no-skip",
    }


def enforcement_install_op(enforcement):
    """An install-pack row at the product root planting exactly the members the enforcement rows name, the
    minimal op that ties each member to the op installing its bytes (spec 14.1). A member two rows share is
    planted once. Each call returns a fresh row."""
    members = []
    for row in enforcement:
        for member in row["members"]:
            if member not in members:
                members.append(dict(member))
    return {"op": "install-pack", "target": ".", "members": members}


def plan_completion():
    """The completion table every v2 plan binds: the full check roster and the retirement rule."""
    return {"checks": list(COMPLETION_CHECKS), "retirement": RETIREMENT_RULES[0]}


def plan_import_policy(skip_policy, sources):
    """The import policy a v2 plan binds: the closed missingness reasons, verbatim retention of unparsed
    text, the adopter's skip policy, and the scope, exactly the migrate-disposed source paths in order."""
    return {"missingness": list(MISSINGNESS_REASONS), "unparsed": UNPARSED_POLICIES[0], "skip": skip_policy,
            "scope": sorted(row["path"] for row in sources if row["disposition"] == "migrate")}


def canonical_plan():
    """A minimal VALID AdoptionPlan (`opf.adoption.plan/v2`): the format/version markers, product identity,
    the two digests binding the plan and inventory, the run id, the spec 14.1 binding roster, and an ordered
    ops list from the closed vocabulary. One non-occupying retire source carries its adoption preimage home
    and its retire-file row, the store is scaffolded, rendered and receipted, and one install-pack row plants
    every enforcement member, so the sources, effects, store-identity and enforcement cross-checks are all
    exercised."""
    run_id = "adopt-20260917T120000Z-0123456789abcdef"
    retire = {"op": "retire-file", "path": "legacy/RULES.md", "preimage_digest": "sha256:" + "6" * 64}
    bindings = canonical_plan_bindings()
    ops = [retire, canonical_op("init-store"), canonical_op("render-views"),
           enforcement_install_op(bindings["enforcement"]), canonical_op("record-adoption")]
    sources = [{"path": retire["path"], "digest": retire["preimage_digest"], "disposition": "retire",
                "occupying": False, "preservation": retire_preimage(run_id, retire["path"])}]
    return {
        "format": PLAN_FORMAT,
        "schema": SCHEMA_VERSION,
        "product": "aiqt",
        "plan_digest": "sha256:" + "1" * 64,
        "inventory_digest": "sha256:" + "2" * 64,
        "run_id": run_id,
        "revision": bindings["revision"],
        "store": {"store_root": ".", "machine_rel": WORKING_DIRNAME + "/toml", "adoption": "first-adoption"},
        "sources": sources,
        "effects": derive_effects(ops, sources, WORKING_DIRNAME + "/toml/" + MANIFEST_NAME),
        "release": bindings["release"],
        "prompt_pack": bindings["prompt_pack"],
        "enforcement": bindings["enforcement"],
        "completion": plan_completion(),
        "import_policy": plan_import_policy(bindings["skip_policy"], sources),
        "ops": ops,
    }


def _compose(root, path):
    """A member path composed under its op's target or store_root directory."""
    return path if root == "." else root + "/" + path


def store_manifest(store):
    """The product-relative path of the frozen store's manifest, the file each register-unmanaged row
    rewrites, or None when the store table is not a well-formed identity (it then carries a finding)."""
    if not (isinstance(store, dict) and _is_contained_relpath(store.get("store_root"))
            and _is_machine_rel(store.get("machine_rel"))):
        return None
    return _compose(store["store_root"], store["machine_rel"] + "/" + MANIFEST_NAME)


def derive_effects(ops, sources, manifest=None):
    """The exact file-level effects a plan names (spec 14.1: creations, replacements, removals and consumer
    repointings), derived purely from already-VALID op and source rows so the bound summary cannot drift
    from the program. Every member an install-pack, init-store or render-views row writes is a creation at
    its composed path, and the record-adoption receipt core is a creation at receipt_path with its digest. A
    move is a creation at its destination and a removal at its source; a retire row is a removal; a hook
    registration is a replacement. Each register-unmanaged row rewrites the frozen store manifest
    (`manifest`, from store_manifest), so it is a replacement there from its old to its new digest; with no
    usable manifest path (a malformed store table, already a finding) it adds nothing. A source preserved
    under the adoption archive (every retire and migrate source, and every occupying source, spec 14.2) adds
    that archive copy as a creation, and a migrate source's later removal is a removal. Each list is sorted,
    so the derivation is deterministic. Which stage performs an effect (apply, or the recorded retirement
    after a green check) is the engine's, not recorded here."""
    creations, replacements, removals, repointings = [], [], [], []
    for row in ops:
        name = row["op"]
        if name == "register-unmanaged" and manifest is not None:
            replacements.append({"path": manifest, "old_digest": row["old_digest"],
                                 "new_digest": row["new_digest"]})
        elif name in ("create-file", "plant-governance"):
            creations.append({"path": row["path"], "digest": row["content_digest"]})
        elif name in _MEMBER_ROOTS:
            for member in row["members"]:
                creations.append({"path": _compose(row[_MEMBER_ROOTS[name]], member["path"]),
                                  "digest": member["digest"]})
        elif name == "record-adoption":
            creations.append({"path": row["receipt_path"], "digest": row["receipt_core_digest"]})
        elif name == "move-file":
            creations.append({"path": row["destination"], "digest": row["source_digest"]})
            removals.append({"path": row["source"], "digest": row["source_digest"]})
        elif name == "retire-file":
            removals.append({"path": row["path"], "digest": row["preimage_digest"]})
        elif name == "enable-hook":
            replacements.append({"path": row["registration_path"], "old_digest": row["old_digest"],
                                 "new_digest": row["new_digest"]})
        elif name == "repoint-consumer":
            repointings.append({"path": row["path"], "old_digest": row["old_digest"],
                                "new_digest": row["new_digest"]})
    for row in sources:
        if row["disposition"] in ("retire", "migrate") or (row["disposition"] == "move" and row["occupying"]):
            creations.append({"path": row["preservation"], "digest": row["digest"]})
        if row["disposition"] == "migrate":
            removals.append({"path": row["path"], "digest": row["digest"]})

    def order(rows):
        return sorted(rows, key=lambda r: sorted(r.items()))
    return {"creations": order(creations), "replacements": order(replacements),
            "removals": order(removals), "repointings": order(repointings)}


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
PLAN_REQUIRED = ("format", "schema", "product", "plan_digest", "inventory_digest", "run_id", "revision",
                 "store", "sources", "effects", "release", "prompt_pack", "enforcement", "completion",
                 "import_policy", "ops")
PLAN_OPTIONAL = ("created_at",)
# The plan-v2 binding sub-schemas (spec 14.1), each closed.
PLAN_STORE_REQUIRED = ("store_root", "machine_rel", "adoption")
PLAN_SOURCE_REQUIRED = ("path", "digest", "disposition", "occupying")
PLAN_SOURCE_OPTIONAL = ("preservation",)
PLAN_RELEASE_REQUIRED = ("version", "manifest_sha256", "anchor", "anchor_sha256")
PROMPT_PACK_REQUIRED = ("version", "digest")
ENFORCEMENT_REQUIRED = ("platform", "means", "members", "residuals")
COMPLETION_REQUIRED = ("checks", "retirement")
IMPORT_POLICY_REQUIRED = ("missingness", "unparsed", "skip", "scope")
# The caller-supplied binding inputs of the planner; it derives every other v2 field.
PLAN_BINDING_INPUTS = ("revision", "release", "prompt_pack", "enforcement", "skip_policy")

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


def validate_op(row, homes=1):
    """Validate ONE AdoptionPlan op row against its ADOPT_OPS definition. A non-table is CANNOT-EVALUATE; an
    op name outside ADOPT_OPS is CANNOT-EVALUATE (out-of-vocabulary refused, never a skip); a table with a
    known op but a missing/unknown field or a malformed field shape is INVALID. `homes` is the store's
    active homes generation: a legacy store (1, the default) keeps its legacy op-level disposition with no
    control-area refusal; in homes 2 an operand that equals, contains, or lies within a store control
    root is INVALID. An adoption plan refuses the control area in every generation (spec 14.2), which
    validate_plan decides over the whole plan, since only there are its own preservation copies and Move
    archive destinations known."""
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
    if homes >= 2:
        findings.extend(_control_operand_findings(name, row, homes))
    return _ok() if not findings else _invalid(findings)


def _control_operand_findings(name, row, homes):
    """Homes-2 control-area refusal for one op row. Directory identities (store_root) are not
    mutation operands. Members are relative to their op's target or store_root, so their composed
    destinations are checked as well as ordinary file operands. Evidence receipts and retire
    preimages are not plan operands: the apply engine derives them from the homes constructors
    through the capability-bound journal API, so a plan row naming a control home is refused."""
    import posixpath
    from _opf_store import store_control_roots, overlaps_home
    operands = [row[f] for f in row if _FIELD_KINDS.get(f) == "filepath" and isinstance(row[f], str)]
    root = row.get(_MEMBER_ROOTS.get(name, ""))
    if isinstance(root, str):
        if name == "install-pack" and root != ".":
            operands.append(root)
        for member in row.get("members", []) if isinstance(row.get("members"), list) else []:
            if isinstance(member, dict) and isinstance(member.get("path"), str):
                operands.append(posixpath.join(root, member["path"]))
    findings = []
    for operand in operands:
        try:
            if any(overlaps_home(operand, home) for home in store_control_roots(homes)):
                raise ValueError("adoption operand overlaps reserved store control area: {!r}".format(operand))
        except ValueError as exc:
            findings.append(str(exc))
    return findings


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


def _is_revision(value):
    return isinstance(value, str) and bool(_REVISION_RE.match(value))


def _is_semver(value):
    return isinstance(value, str) and _parse_semver(value) is not None


def _is_under(path, parent):
    return path == parent or path.startswith(parent + "/")


def _is_machine_rel(value):
    """A machine-store locator: exactly one direct `.working/` child that is not a reserved store
    subdirectory (spec 4.4), the only shape resolve_store reports as a machine_rel."""
    if not _is_contained_filepath(value):
        return False
    parts = value.split("/")
    return len(parts) == 2 and parts[0] == WORKING_DIRNAME and parts[1] not in RESERVED_MACHINE_SUBDIRS


def _preimage_home(run_id, path):
    """The run's adoption preimage home for a source path (spec 14.2), or None when either is unusable."""
    if run_id is None or path is None:
        return None
    try:
        return retire_preimage(run_id, path)
    except ValueError:
        return None


def validate_plan(plan, homes=1):
    """Validate an AdoptionPlan (`opf.adoption.plan/v2`). A non-table is CANNOT-EVALUATE; an unknown
    product, a per-op out-of-vocabulary name, or an out-of-vocabulary binding token (the store's adoption
    kind, a source disposition, an enforcement platform or means, a completion check, the retirement rule,
    an import-policy token) propagates CANNOT-EVALUATE; any other schema violation is INVALID. The store
    identity's store_root must be the product root ".", the one root the planner freezes and apply resolves.
    `homes` is the store's active homes generation, applied to every op row as validate_op applies it and to
    every source path."""
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
    run_id = plan.get("run_id")
    if "run_id" not in missing and not (isinstance(run_id, str) and _RUN_ID_RE.match(run_id)):
        findings.append("plan run_id does not match the adoption run-id grammar")
    if not (isinstance(run_id, str) and _RUN_ID_RE.match(run_id)):
        run_id = None
    if "created_at" in plan and not _is_timestamp(plan["created_at"]):
        findings.append("plan created_at is not an RFC 3339 UTC instant")
    if "revision" not in missing and not _is_revision(plan.get("revision")):
        findings.append("plan revision is not a 40- or 64-digit lowercase hex object id")
    res = _validate_plan_bindings(plan, missing, findings)
    if res is not None:
        return res
    before = len(findings)
    if "sources" not in missing:
        frozen = _frozen_store(plan)
        res = _validate_plan_sources(plan.get("sources"), run_id, homes, findings,
                                     frozen[0] if frozen is not None else ".")
        if res is not None:
            return res
    sources_clean = "sources" not in missing and run_id is not None and len(findings) == before
    ops = plan.get("ops")
    if "ops" not in missing:
        if not isinstance(ops, list):
            return _cannot("plan 'ops' is not a list")
        if not ops:
            findings.append("plan 'ops' is empty (a plan must carry at least one op)")
        ops_clean = True
        for i, row in enumerate(ops):
            res = validate_op(row, homes=homes)
            if res.status == CANNOT_EVALUATE:
                return _cannot("plan op[{}]: {}".format(i, "; ".join(res.findings)))
            if res.status == INVALID:
                findings.extend("plan op[{}]: {}".format(i, f) for f in res.findings)
                ops_clean = False
        if ops_clean:
            _cross_check_plan(plan, missing, sources_clean, findings)
    return _ok() if not findings else _invalid(findings)


def _validate_plan_sources(rows, run_id, homes, findings, root="."):
    """Validate the per-source rows (spec 14.1: every source path, byte digest, disposition and preservation
    destination). Returns an AdoptValidation ONLY to short-circuit CANNOT-EVALUATE (an out-of-vocabulary
    disposition); otherwise appends findings and returns None. `occupying` records whether the source sits
    at a planned managed destination, a declared view or a machine-store path (spec 14.2). A kept source
    must not occupy one and records no preservation. A retire or migrate source, and any occupying source,
    is preserved at the run's adoption preimage home composed under the frozen store root `root` (spec
    14.2: the adoption archive is a store-tree home, so the archive copy lands inside the frozen store); a
    non-occupying move source is preserved at its move destination, which _cross_check_plan matches against
    its move-file row. In every homes generation no
    source equals, contains or lies within the store control area under `root` (spec 14.2): a control area,
    committed adoption-archive originals included, is never adoption content, so it is never kept,
    registered unmanaged, moved, retired or migrated."""
    if not isinstance(rows, list):
        findings.append("plan sources is not a list")
        return None
    seen = set()
    for i, row in enumerate(rows):
        label = "plan sources[{}]".format(i)
        if not isinstance(row, dict):
            findings.append(label + " is not a table")
            continue
        missing, unknown = _closed_keyset(row, PLAN_SOURCE_REQUIRED, PLAN_SOURCE_OPTIONAL)
        for k in missing:
            findings.append("{} is missing {!r}".format(label, k))
        for k in unknown:
            findings.append("{} carries unknown key {!r}".format(label, k))
        disp = row.get("disposition")
        if "disposition" in row and disp not in DISPOSITIONS:
            return _cannot("{} disposition {!r} is outside DISPOSITIONS".format(label, disp))
        path = row.get("path")
        if not _is_contained_filepath(path):
            if "path" in row:
                findings.append(label + " path is not a contained relative file path")
            path = None
        elif path in seen:
            findings.append("{} path {!r} is a duplicate of an earlier row".format(label, path))
        else:
            seen.add(path)
        if "digest" in row and not _is_digest(row["digest"]):
            findings.append(label + " digest is not a well-formed digest")
        occupying = row.get("occupying")
        if "occupying" in row and not _is_bool(occupying):
            findings.append(label + " occupying is not a boolean")
            occupying = None
        if disp == "keep":
            if occupying is True:
                findings.append(label + " is kept but occupies a managed destination (spec 14.2 refuses it)")
            if "preservation" in row:
                findings.append(label + " is kept in place, so it records no preservation destination")
        elif disp in DISPOSITIONS:
            pres = row.get("preservation")
            home = _preimage_home(run_id, path)
            if home is not None:
                home = _compose(root, home)  # the adoption archive lies inside the frozen store tree
            if "preservation" not in row:
                findings.append("{} disposition {!r} records no preservation destination".format(label, disp))
            elif not _is_contained_filepath(pres):
                findings.append(label + " preservation is not a contained relative file path")
            elif (disp != "move" or occupying is True) and home is not None and pres != home:
                findings.append("{} preservation is not the run's adoption preimage home {!r}".format(
                    label, home))
        if path is not None and _in_control_area(path, root):
            findings.append("{} path {!r} selects the reserved store control area as adoption content "
                            "(spec 14.2)".format(label, path))
        if homes >= 2 and path is not None:
            findings.extend(_control_operand_findings("sources", {"path": path}, homes))
    return None


def _in_control_area(path, root="."):
    """Whether a product-relative path equals, contains or lies within an ADOPTION_CONTROL_ROOTS entry of
    the store at `root`."""
    return any(_is_under(path, area) or _is_under(area, path)
               for area in (_compose(root, name) for name in ADOPTION_CONTROL_ROOTS))


def protected_destination(path, run_id=None, root="."):
    """Why a product-relative path is a protected destination, or None when it is not. The planner
    (validate_plan, for every move destination and archive preservation copy) and the apply shell
    (_opf_adopt_apply.check_apply_ops, for every journal operand) both call this ONE predicate, so for any
    one path it gives both sides the same answer. That is the whole parity claim: each side also applies
    its own other rules, and the plan side's (its path shapes, the store-tree Move archive rule, the
    control-area effect rule) may refuse a destination apply admits, which is the safe direction.
    Protected: any path that is not a normalized, contained relative file path (_is_contained_filepath: an
    empty, `.` or `..` segment, an absolute or backslash form, or a control character), refused here
    whatever its callers already filter; a `.git` or `.aiqt` component at any depth
    (STORE_ROOT_CONTROL_DIRS: the version-control area and AIQT's tree, the legacy adoption journal
    included); the product-root pointers `.opf.toml` and `.opf.local.toml` (spec 4.3); the store control
    area (ADOPTION_CONTROL_ROOTS, spec 14.2) other than strictly beneath this run's own adoption archive,
    this run's own evidence bundle or the Move archive root; and the store tree's `.gitignore`. The control
    area and `.gitignore` compose under the store root `root`; the pointers stay at the product root.
    Protected names compare case-insensitively (str.casefold), so on a case-insensitive filesystem `.GIT/x`,
    `.OPF.toml` or `.Working/staging/x` is protected too; the three exemptions match only their exact
    spelling, so a case variant of an exempt home is protected.

    DISCLOSED-RESIDUAL (disclose-guard-residuals): the planner applies this predicate to the destinations
    its dispositions name (move destinations, archive preservation copies), not to caller-supplied op rows
    (create-file, install-pack, plant-governance, render-views, init-store, record-adoption, enable-hook,
    repoint-consumer) or source removals. Apply refuses every one of those at a protected path, so such a
    plan fails closed at apply; the op slices that make those rows executable settle their own
    exceptions. casefold is neither Unicode normalization nor every filesystem's case table: a filesystem
    whose case mapping casefold does not share (for example one mapping U+0131, dotless i, to `I`) can
    still alias a protected name."""
    if not _is_contained_filepath(path):
        return ("{!r} is not a normalized, contained relative file path (an empty, `.` or `..` segment, an "
                "absolute or backslash form, or a control character), so it is never an adoption "
                "destination or apply operand".format(path))
    folded = path.casefold()
    parts = folded.split("/")
    for name in STORE_ROOT_CONTROL_DIRS:
        if name.casefold() in parts:
            return ("{!r} names the store control root {}/ at some depth: the version-control area and the "
                    ".aiqt tree, the legacy adoption journal included, are never adoption destinations or "
                    "apply operands".format(path, name))
    for pointer in (POINTER_REL, LOCAL_POINTER_REL):
        if _is_under(folded, pointer.casefold()):
            return "{!r} is the store pointer {}, never an adoption destination (spec 4.3)".format(
                path, pointer)
    exempt = [_compose(root, ARCHIVE_REL + "/moved")]
    home = _preimage_home(run_id, "f")
    if home is not None:
        exempt += [_compose(root, home.rsplit("/", 1)[0]), _compose(root, evidence_run("adoption", run_id))]
    areas = [_compose(root, name).casefold() for name in ADOPTION_CONTROL_ROOTS]
    if (any(_is_under(folded, area) or _is_under(area, folded) for area in areas)
            and not any(path.startswith(e + "/") for e in exempt)):
        return ("{!r} lies in the reserved store control area: adoption writes land there only beneath this "
                "run's own adoption archive, its own evidence bundle or the Move archive root, and another "
                "run's archive or bundle is immutable (spec 14.2, 4.2)".format(path))
    gitignore = _compose(root, STORE_GITIGNORE_REL)
    if _is_under(folded, gitignore.casefold()):
        return "{!r} is the store tree's {}, never an adoption destination (spec 4.2)".format(path, gitignore)
    return None


def _validate_plan_bindings(plan, missing, findings):
    """Validate the plan-v2 binding tables (spec 14.1): the store identity, the tool release with its
    independent anchor, the prompt pack, the enforcement pack per platform with residuals, the completion
    roster with its retirement rule, and the import policy. Returns an AdoptValidation ONLY to short-circuit
    CANNOT-EVALUATE (an out-of-vocabulary token); otherwise appends findings and returns None."""
    if "store" not in missing:
        st = _validate_subtable(plan.get("store"), PLAN_STORE_REQUIRED, "plan store", findings)
        if st is not None:
            if "adoption" in st and st["adoption"] not in ADOPTION_KINDS:
                return _cannot("plan store.adoption {!r} is outside ADOPTION_KINDS".format(st["adoption"]))
            if "store_root" in st and not _is_contained_relpath(st["store_root"]):
                findings.append("plan store.store_root is not a contained relative path")
            elif "store_root" in st and st["store_root"] != ".":
                # the planner freezes the product root and refuses a store outside it, and apply resolves
                # protected destinations there, so a plan rooted elsewhere is refused rather than judged in
                # a root apply does not share
                findings.append("plan store.store_root {!r} is not the product root '.'".format(
                    st["store_root"]))
            if "machine_rel" in st and not _is_machine_rel(st["machine_rel"]):
                findings.append("plan store.machine_rel is not a direct, unreserved .working child")
    if "release" not in missing:
        rel = _validate_subtable(plan.get("release"), PLAN_RELEASE_REQUIRED, "plan release", findings)
        if rel is not None:
            if "version" in rel and not _is_semver(rel["version"]):
                findings.append("plan release.version is not a bare SemVer version")
            if "anchor" in rel and not _is_token(rel["anchor"]):
                findings.append("plan release.anchor is not a non-empty token")
            for key in ("manifest_sha256", "anchor_sha256"):
                if key in rel and not _is_digest(rel[key]):
                    findings.append("plan release.{} is not a well-formed digest".format(key))
            # the plan binds a release whose manifest was checked against an independent anchor, so the two
            # recorded digests must agree; a disagreeing release cannot be approved.
            if (_is_digest(rel.get("manifest_sha256")) and _is_digest(rel.get("anchor_sha256"))
                    and rel["manifest_sha256"] != rel["anchor_sha256"]):
                findings.append("plan release.manifest_sha256 disagrees with its independent anchor")
    if "prompt_pack" not in missing:
        pack = _validate_subtable(plan.get("prompt_pack"), PROMPT_PACK_REQUIRED, "plan prompt_pack", findings)
        if pack is not None:
            if "version" in pack and not _is_semver(pack["version"]):
                findings.append("plan prompt_pack.version is not a bare SemVer version")
            if "digest" in pack and not _is_digest(pack["digest"]):
                findings.append("plan prompt_pack.digest is not a well-formed digest")
    if "enforcement" not in missing:
        res = _validate_enforcement(plan.get("enforcement"), findings)
        if res is not None:
            return res
    if "completion" not in missing:
        comp = _validate_subtable(plan.get("completion"), COMPLETION_REQUIRED, "plan completion", findings)
        if comp is not None:
            checks = comp.get("checks")
            if "checks" in comp and not isinstance(checks, list):
                findings.append("plan completion.checks is not a list")
            elif "checks" in comp:
                for check_id in checks:
                    if check_id not in COMPLETION_CHECKS:
                        return _cannot("plan completion check {!r} is outside COMPLETION_CHECKS".format(
                            check_id))
                if checks != list(COMPLETION_CHECKS):
                    findings.append("plan completion.checks is not the complete spec 14.1 roster in order")
            if "retirement" in comp and comp["retirement"] not in RETIREMENT_RULES:
                return _cannot("plan completion.retirement {!r} is outside RETIREMENT_RULES".format(
                    comp["retirement"]))
    if "import_policy" not in missing:
        return _validate_import_policy(plan.get("import_policy"), findings)
    return None


def _validate_import_policy(table, findings):
    """Validate the import policy (spec 8.3, 14.1): the closed missingness reasons in order, the unparsed-
    text policy, the skip policy and a scope of contained file paths (_cross_check_plan matches the scope to
    the migrate sources). Returns an AdoptValidation ONLY to short-circuit CANNOT-EVALUATE."""
    pol = _validate_subtable(table, IMPORT_POLICY_REQUIRED, "plan import_policy", findings)
    if pol is None:
        return None
    reasons = pol.get("missingness")
    if "missingness" in pol and not isinstance(reasons, list):
        findings.append("plan import_policy.missingness is not a list")
    elif "missingness" in pol:
        for reason in reasons:
            if reason not in MISSINGNESS_REASONS:
                return _cannot("plan import_policy missingness reason {!r} is outside "
                               "MISSINGNESS_REASONS".format(reason))
        if reasons != list(MISSINGNESS_REASONS):
            findings.append("plan import_policy.missingness is not the closed reason set in order")
    for key, vocab in (("unparsed", UNPARSED_POLICIES), ("skip", SKIP_POLICIES)):
        if key in pol and pol[key] not in vocab:
            return _cannot("plan import_policy.{} {!r} is outside its closed vocabulary".format(
                key, pol[key]))
    scope = pol.get("scope")
    if "scope" in pol and not (isinstance(scope, list) and all(_is_contained_filepath(p) for p in scope)):
        findings.append("plan import_policy.scope is not a list of contained file paths")
    return None


def _validate_enforcement(rows, findings):
    """Validate the enforcement-pack binding (spec 14.1): exactly one row per ENFORCEMENT_PLATFORMS entry,
    in that order, each naming a means its platform allows, a non-empty member list and a non-empty,
    duplicate-free list of closed residual tokens carrying the disclosure its means requires, with every
    PACK_RESIDUALS disclosure on at least one row. _cross_check_plan ties each member to the op installing it.
    Returns an AdoptValidation ONLY to short-circuit CANNOT-EVALUATE (an out-of-vocabulary platform, means
    or residual); otherwise appends findings and returns None."""
    if not isinstance(rows, list):
        findings.append("plan enforcement is not a list of platform rows")
        return None
    platforms = []
    disclosed = set()
    for i, row in enumerate(rows):
        label = "plan enforcement[{}]".format(i)
        if _validate_subtable(row, ENFORCEMENT_REQUIRED, label, findings) is None:
            continue
        platform, means = row.get("platform"), row.get("means")
        if "platform" in row and platform not in ENFORCEMENT_PLATFORMS:
            return _cannot("{} platform {!r} is outside ENFORCEMENT_PLATFORMS".format(label, platform))
        if "means" in row and means not in _ENFORCEMENT_MEANS_ALL:
            return _cannot("{} means {!r} is outside the enforcement means vocabulary".format(label, means))
        if "platform" in row:
            platforms.append(platform)
            if "means" in row and means not in ENFORCEMENT_MEANS[platform]:
                findings.append("{} means {!r} is not one platform {!r} allows".format(
                    label, means, platform))
        if "members" in row and not _valid_field("members", row["members"]):
            findings.append(label + " members is not a non-empty, duplicate-free path/digest list")
        residuals = row.get("residuals")
        # every platform discloses its residual coverage (spec 14.1), so an empty list is refused.
        if "residuals" in row and not (_is_token_list(residuals) and residuals):
            findings.append(label + " residuals is not a non-empty list of tokens")
        elif "residuals" in row and len(set(residuals)) != len(residuals):
            findings.append(label + " residuals carries a duplicate entry")
        elif "residuals" in row:
            for residual in residuals:
                if residual not in ENFORCEMENT_RESIDUALS:
                    return _cannot("{} residual {!r} is outside ENFORCEMENT_RESIDUALS".format(
                        label, residual))
            disclosed.update(residuals)
            lacking = [r for r in ENFORCEMENT_REQUIRED_RESIDUALS.get(means, ()) if r not in residuals]
            if lacking:
                findings.append("{} means {!r} omits its required residual disclosure {}".format(
                    label, means, ", ".join(lacking)))
    if platforms != list(ENFORCEMENT_PLATFORMS):
        findings.append("plan enforcement does not cover each supported platform exactly once, in order")
    lacking = [r for r in PACK_RESIDUALS if r not in disclosed]
    if lacking:
        findings.append("plan enforcement discloses no row carrying the spec 14.1 residual {}".format(
            ", ".join(lacking)))
    return None


def _cross_check_plan(plan, missing, sources_clean, findings):
    """Internal consistency of a plan whose ops are all VALID: a move destination inside the frozen store's
    tree lies strictly beneath its Move archive root (spec 14.2); every
    op agrees with the frozen store identity; the manifest is rewritten only by its registration chain; every
    enforcement member is installed by an op; and, when the source rows are clean, `effects` equals the
    effects the ops and sources name, those effects collide nowhere and write no control area, each keep,
    retire and move source matches exactly one register-unmanaged, retire-file or move-file row (and no such
    row lacks a source), a non-occupying move is preserved at its move destination, and the import scope is
    exactly the migrate-disposed sources. No move destination and no archive preservation copy is a
    protected destination (protected_destination, the one predicate the apply shell also applies)."""
    ops = plan["ops"]
    frozen = _frozen_store(plan)
    root = frozen[0] if frozen is not None else "."
    tree, moved_root = _compose(root, WORKING_DIRNAME), _compose(root, ARCHIVE_REL + "/moved")
    run_id = plan.get("run_id")
    run_id = run_id if isinstance(run_id, str) and _RUN_ID_RE.match(run_id) else None
    for row in ops:
        # strictly beneath: the Move archive root is the directory later moves land in, never a file target
        if (row["op"] == "move-file" and _is_under(row["destination"], tree)
                and not row["destination"].startswith(moved_root + "/")):
            findings.append("plan move-file destination {!r} is inside the store tree but not beneath "
                            "{}/ (spec 14.2)".format(row["destination"], moved_root))
        # the destination predicate apply shares (protected_destination): no move lands on a protected path
        protected = None
        if row["op"] == "move-file":
            protected = protected_destination(row["destination"], run_id, root)
        if protected is not None:
            findings.append("plan move-file destination is protected: " + protected)
    _store_identity_findings(plan, findings)
    _registration_chain_findings(plan, findings)
    _enforcement_install_findings(plan, findings)
    if not sources_clean:
        return                          # the source rows already carry a finding; nothing sound to compare
    sources = plan["sources"]
    for row in sources:
        # an archive preservation copy is a destination too: the shared predicate refuses a protected one
        if row["disposition"] in ("retire", "migrate") or row["occupying"] is True:
            protected = protected_destination(row["preservation"], run_id, root)
            if protected is not None:
                findings.append("plan source {!r} preservation is protected: {}".format(
                    row["path"], protected))
    effects = derive_effects(ops, sources, store_manifest(plan.get("store")))
    if "effects" not in missing and plan["effects"] != effects:
        findings.append("plan effects do not equal the effects its ops and sources name")
    _effect_collision_findings(plan, effects, findings)
    _control_area_effect_findings(plan, effects, findings)
    wanted, have, destinations = [], [], {}
    for row in sources:
        if row["disposition"] == "keep":
            wanted.append(("register-unmanaged", row["path"]))
        elif row["disposition"] in ("retire", "move"):
            wanted.append((row["disposition"] + "-file", row["path"], row["digest"]))
    for row in ops:
        if row["op"] == "register-unmanaged":
            have.append(("register-unmanaged", row["entry"]))
        elif row["op"] == "retire-file":
            have.append(("retire-file", row["path"], row["preimage_digest"]))
        elif row["op"] == "move-file":
            have.append(("move-file", row["source"], row["source_digest"]))
            destinations[row["source"]] = row["destination"]
    if sorted(wanted) != sorted(have):
        findings.append("plan sources do not correspond one to one with its disposition ops")
    for row in sources:
        if (row["disposition"] == "move" and not row["occupying"]
                and row["path"] in destinations and row["preservation"] != destinations[row["path"]]):
            findings.append("plan source {!r} is a non-occupying move not preserved at its move "
                            "destination".format(row["path"]))
    pol = plan.get("import_policy")
    migrated = sorted(row["path"] for row in sources if row["disposition"] == "migrate")
    if isinstance(pol, dict) and isinstance(pol.get("scope"), list) and pol["scope"] != migrated:
        findings.append("plan import_policy.scope is not exactly the migrate-disposed sources, in order")


def _frozen_store(plan):
    """The plan's (store_root, machine_rel) when its store identity is well formed, else None (the store
    table already carries a finding)."""
    st = plan.get("store")
    if (isinstance(st, dict) and _is_contained_relpath(st.get("store_root"))
            and _is_machine_rel(st.get("machine_rel"))):
        return st["store_root"], st["machine_rel"]
    return None


def _store_identity_findings(plan, findings):
    """Every op that targets a store agrees with the frozen store identity (spec 14.1): a store_root operand
    names the frozen store root, the receipt lies inside that store's tree, and init-store scaffolds the
    frozen machine store (its manifest among its members) inside the store tree and no other machine
    directory. A first adoption has no store yet, so it must carry the init-store row that scaffolds one
    (spec 14); a re-adoption may name a store that already resolves, which takes none."""
    frozen = _frozen_store(plan)
    if frozen is None:
        return
    root, machine = frozen
    tree = _compose(root, WORKING_DIRNAME)
    manifest = machine + "/" + MANIFEST_NAME
    if (plan["store"].get("adoption") == "first-adoption"
            and not any(row["op"] == "init-store" for row in plan["ops"])):
        findings.append("plan is a first adoption but carries no init-store row to scaffold its store "
                        "(spec 14)")
    for i, row in enumerate(plan["ops"]):
        label = "plan op[{}] {!r}".format(i, row["op"])
        if "store_root" in row and row["store_root"] != root:
            findings.append("{} targets store_root {!r}, contradicting the frozen store identity {!r}".format(
                label, row["store_root"], root))
        if row["op"] == "record-adoption" and not row["receipt_path"].startswith(tree + "/"):
            findings.append("{} receipt_path {!r} lies outside the frozen store tree {}/".format(
                label, row["receipt_path"], tree))
        if row["op"] == "init-store":
            paths = [member["path"] for member in row["members"]]
            if manifest not in paths:
                findings.append("{} does not scaffold the frozen machine store's manifest {!r}".format(
                    label, manifest))
            for path in paths:
                parts = path.split("/")
                if not path.startswith(WORKING_DIRNAME + "/"):
                    findings.append("{} scaffolds {!r} outside the frozen store tree {}/".format(
                        label, path, tree))
                elif len(parts) > 2 and "/".join(parts[:2]) != machine:
                    findings.append("{} scaffolds {!r} outside the frozen machine store {}/".format(
                        label, path, machine))


def _registration_chain_findings(plan, findings):
    """The frozen store manifest is rewritten only by its registration chain (spec 14.1 exact effects, 14.2
    Keep). Each register-unmanaged row changes the manifest bytes, and in program order its old digest is the
    manifest the program leaves before it: init-store's scaffolded manifest, or the previous registration's
    new digest. A registration ahead of init-store, and any enable-hook or repoint-consumer row naming the
    manifest, is refused; so is any other op creating a file at the manifest (a create-file or
    plant-governance path, a move destination, a record-adoption receipt, or a composed install-pack or
    render-views member there would fork the chain's one rewrite or plant an unchained manifest). For a
    store that already resolves the first link names the observed manifest, which the planner binds and
    this lone plan cannot see."""
    manifest = store_manifest(plan.get("store"))
    if manifest is None:
        return                          # the store table already carries a finding
    ops = plan["ops"]
    inits = [i for i, row in enumerate(ops) if row["op"] == "init-store"]
    current = None
    for i, row in enumerate(ops):
        label = "plan op[{}] {!r}".format(i, row["op"])
        if row["op"] == "init-store":
            for member in row["members"]:
                if _compose(row["store_root"], member["path"]) == manifest:
                    current = member["digest"]
        elif row["op"] == "register-unmanaged":
            if inits and i < inits[0]:
                findings.append(label + " registers an [unmanaged] entry before init-store scaffolds the "
                                "manifest")
            elif current is not None and row["old_digest"] != current:
                findings.append(label + " does not rewrite the manifest bytes the program leaves before it")
            if row["new_digest"] == row["old_digest"]:
                findings.append(label + " leaves the manifest bytes unchanged, so it registers nothing")
            current = row["new_digest"]
        elif ((row["op"] == "enable-hook" and row["registration_path"] == manifest)
              or (row["op"] == "repoint-consumer" and row["path"] == manifest)):
            findings.append(label + " rewrites the frozen store manifest outside its registration chain")
        elif _op_creates_at(row, manifest):
            findings.append(label + " creates a file at the frozen store manifest, which only init-store's "
                            "scaffold creates (spec 14.1: the chain is the manifest's one rewrite)")


def _op_creates_at(row, path):
    """Whether a VALID op row other than init-store names a creation at `path`, mirroring exactly the
    creation rows derive_effects draws from it: a create-file or plant-governance path, a move-file
    destination, a record-adoption receipt, or an install-pack/render-views member composed under its
    root directory."""
    name = row["op"]
    if name in ("create-file", "plant-governance"):
        return row["path"] == path
    if name == "move-file":
        return row["destination"] == path
    if name == "record-adoption":
        return row["receipt_path"] == path
    if name in ("install-pack", "render-views"):
        return any(_compose(row[_MEMBER_ROOTS[name]], m["path"]) == path for m in row["members"])
    return False


def _enforcement_install_findings(plan, findings):
    """Each enforcement member is installed by an op of the same plan with exactly its bytes (spec 14.1):
    an install-pack member or plant-governance creation at its path and digest, or an enable-hook
    registration whose new digest is its digest. Members are caller-declared path/digest pairs; this ties
    them to the program, never to a real enforcement pack, which this module never reads."""
    rows = plan.get("enforcement")
    if not isinstance(rows, list):
        return
    installed = set()
    for row in plan["ops"]:
        if row["op"] == "install-pack":
            installed.update((_compose(row["target"], m["path"]), m["digest"]) for m in row["members"])
        elif row["op"] == "plant-governance":
            installed.add((row["path"], row["content_digest"]))
        elif row["op"] == "enable-hook":
            installed.add((row["registration_path"], row["new_digest"]))
    for i, row in enumerate(rows):
        if not (isinstance(row, dict) and _valid_field("members", row.get("members"))):
            continue                    # the row already carries a finding
        for member in row["members"]:
            if (member["path"], member["digest"]) not in installed:
                findings.append("plan enforcement[{}] member {!r} is installed by no {} row with its "
                                "digest".format(i, member["path"], ", ".join(ENFORCEMENT_INSTALL_OPS)))


def _directory_homes(plan):
    """The store directory homes no file target may equal or contain (spec 4.2, 14.2): the store tree, each
    reserved child, the Move archive, the adoption archive and evidence roots, this run's adoption archive,
    and the frozen machine store."""
    homes = [WORKING_DIRNAME, ARCHIVE_REL + "/moved", ARCHIVE_REL + "/adoption", IMPORTED_REL + "/adoption"]
    homes += [WORKING_DIRNAME + "/" + name for name in RESERVED_MACHINE_SUBDIRS]
    run_home = _preimage_home(plan["run_id"], "f")
    if run_home is not None:
        homes.append(run_home.rsplit("/", 1)[0])
    frozen = _frozen_store(plan)
    if frozen is not None:
        homes = [_compose(frozen[0], home) for home in homes + [frozen[1]]]
    return homes


def _effect_collision_findings(plan, effects, findings):
    """The effects collide nowhere (spec 14.2: an occupied destination is a collision finding, never an
    overwrite). No path is created, replaced or repointed twice; the manifest replacements of the
    registration chain are one sequential rewrite, checked link by link in _registration_chain_findings,
    which also refuses any non-init creation at the manifest, so this exemption cannot swallow one. No
    creation lands on a source left live at apply, and no replacement or repointing rewrites one: a kept
    source, and every non-occupying source, stays frozen and byte-identical in place until its recorded
    retirement (spec 14.1, 14.2); an occupying source is archived and removed at apply, so its managed
    destination may then be written. No created file is an ancestor of another claimed or live path, or a
    descendant of one, and no created file equals or contains a store directory home. Called only on clean
    source rows and run id."""
    manifest = store_manifest(plan.get("store"))
    claimed = set()
    for kind in ("creations", "replacements", "repointings"):
        for row in effects[kind]:
            if kind == "replacements" and row["path"] == manifest:
                claimed.add(row["path"])
                continue
            if row["path"] in claimed:
                findings.append("plan effects claim {!r} twice (a collision is refused, never frozen)".format(
                    row["path"]))
            claimed.add(row["path"])
    live = set(row["path"] for row in plan["sources"] if row["disposition"] == "keep" or not row["occupying"])
    created = set(row["path"] for row in effects["creations"])
    for path in sorted(created & live):
        findings.append("plan creates {!r} where a source stays live at apply".format(path))
    rewritten = set(row["path"] for kind in ("replacements", "repointings") for row in effects[kind])
    for path in sorted(rewritten & live):
        findings.append("plan rewrites {!r}, a source that stays live and byte-identical until its recorded "
                        "retirement (spec 14.1, 14.2)".format(path))
    others = claimed | live
    for path in sorted(others):
        parts = path.split("/")
        for depth in range(1, len(parts)):
            ancestor = "/".join(parts[:depth])
            if ancestor in created or (path in created and ancestor in others):
                findings.append("plan needs {!r} as a file and as a directory of {!r}".format(ancestor, path))
    homes = _directory_homes(plan)
    for path in sorted(created):
        if any(_is_under(home, path) for home in homes):
            findings.append("plan creates file {!r} at or above a store directory home".format(path))


def _control_area_effect_findings(plan, effects, findings):
    """No effect writes the store control area, in any homes generation (spec 14.2), save the plan's own
    preservation copies at this run's adoption preimage homes and move destinations strictly beneath the Move
    archive. Any other creation, replacement or repointing that equals, contains or lies within a control
    root is refused, so a committed adoption-archive or Move-archive original is never overwritten and
    nothing unbound is planted there. Removals are source paths, which _validate_plan_sources keeps out of
    the control area. Called only on clean source rows and run id."""
    frozen = _frozen_store(plan)
    root = frozen[0] if frozen is not None else "."
    moved_root = _compose(root, ARCHIVE_REL + "/moved") + "/"
    allowed = set(row["preservation"] for row in plan["sources"]
                  if row["disposition"] in ("retire", "migrate") or row["occupying"])
    allowed.update(row["destination"] for row in plan["ops"]
                   if row["op"] == "move-file" and row["destination"].startswith(moved_root))
    for kind in ("creations", "replacements", "repointings"):
        for row in effects[kind]:
            if row["path"] not in allowed and _in_control_area(row["path"], root):
                findings.append("plan writes {!r} into the reserved store control area (spec 14.2)".format(
                    row["path"]))


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
        # DISPOSITIONS mapping: keep->register-unmanaged, move->move-file, retire->retire-file, and migrate
        # mints no op, its source kept for post-adoption import). Every disposition here resolves a DETECTED
        # PRE-EXISTING foreign file, so its before_digest (the prior bytes) MUST be present. `retire`
        # (retire-file, journal `remove`) and `move` (move-file, which relocates the file out of the store,
        # journal `create`+`remove`) both leave the store path empty, so after_digest MUST be absent. `keep`
        # (register-unmanaged: the file is left "exactly where it is, untouched", spec 14.2) keeps the file in
        # place unchanged, so after_digest MUST be present and, being untouched, MUST equal before_digest. A
        # `migrate` source is archived and its managed destination re-initialized or re-rendered when it
        # occupies one, and stays frozen live until its recorded retirement otherwise (spec 14.2), so ITS
        # after_digest presence is engine-semantics, disclosed in the DISCLOSED-RESIDUAL block, not enforced
        # here.
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
        # the run_id is validated against the AUTHORITATIVE import run-id grammar (mirrored from _opf_store),
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
    check("adopt-ops-count-11", len(ADOPT_OPS) == 11)
    check("adopt-ops-names-unique", len(ADOPT_OP_NAMES) == len(ADOPT_OPS))
    check("adopt-ops-expected-names",
          ADOPT_OP_NAMES == frozenset({
              "install-pack", "init-store", "create-file", "plant-governance",
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
    # no sources either, so the empty-ops finding is the only one (v2 cross-checks stay silent)
    empty_ops["sources"] = []; empty_ops["effects"] = derive_effects([], [])
    check("plan-empty-ops-invalid", validate_plan(empty_ops).status == INVALID)
    bad_fmt = canonical_plan(); bad_fmt["format"] = "opf.adoption.plan/v3"
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

    # 10b (fix 2, import run-id grammar): the receipt import_runs[].run_id validates against the
    # AUTHORITATIVE `imp-<UTCSTAMP>Z-<hash16>` grammar (mirrored from _opf_store's import home), not a
    # generic token. Reproduces codex round-4 #3 (import_runs run_id "../escape" was VALID; must be INVALID).
    r_esc = canonical_receipt_core(); r_esc["import_runs"][0]["run_id"] = "../escape"
    check("receipt-import-run-id-traversal-invalid", validate_receipt_core(r_esc).status == INVALID)
    r_bad = canonical_receipt_core(); r_bad["import_runs"][0]["run_id"] = "imp-nope"
    check("receipt-import-run-id-nonconforming-invalid", validate_receipt_core(r_bad).status == INVALID)
    r_good = canonical_receipt_core()
    r_good["import_runs"][0]["run_id"] = "imp-20260101T000000Z-abcdef0123456789"
    check("receipt-import-run-id-valid", validate_receipt_core(r_good).status == VALID)
    # the mirror MUST match the store's import-home grammar byte for byte so it cannot drift (no filesystem,
    # journal, or network at self-test time).
    import _opf_store  # noqa: E402
    check("import-run-id-mirror-matches-store",
          _IMPORT_RUN_ID_RE.pattern
          == "^" + _opf_store._HOME_RUN_PREFIXES["import"] + _opf_store._HOME_RUN_SUFFIX + r"\Z")

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

    # 14: plan-v2 binding roster (spec 14.1). Each vector FAILS if its corresponding check is reverted.
    import copy
    # 14a: every binding field is REQUIRED; dropping any one is INVALID.
    for key in ("run_id", "revision", "store", "sources", "effects", "release", "prompt_pack",
                "enforcement", "completion", "import_policy"):
        p_miss = canonical_plan(); del p_miss[key]
        check("plan-v2-missing-{}-invalid".format(key), validate_plan(p_miss).status == INVALID)
    # 14b: the replaced v1 marker is refused, never read as v2 (v2 replaces v1 in place).
    p_v1 = canonical_plan(); p_v1["format"] = "opf.adoption.plan/v1"
    check("plan-v1-format-refused", validate_plan(p_v1).status == INVALID)

    def _mutated(base, mutate, refresh=False, homes=1):
        p = copy.deepcopy(base)
        mutate(p)
        if refresh:  # re-derive effects, so the vector isolates the one check it names
            p["effects"] = derive_effects(p["ops"], p["sources"], store_manifest(p.get("store")))
        return validate_plan(p, homes=homes).status
    _D5 = "sha256:" + "5" * 64
    _extra_retire = {"op": "retire-file", "path": "legacy/OTHER.md", "preimage_digest": _D5}
    base_plan = canonical_plan()

    def _swap(rows, i, j):
        rows[i], rows[j] = rows[j], rows[i]
    # 14c: one malformed-shape or inconsistency vector per binding rule -> INVALID.
    for label, mutate, refresh in (
            ("revision-not-hex", lambda p: p.update(revision="HEAD"), False),
            ("revision-short", lambda p: p.update(revision="0123456"), False),
            ("store-unknown-key", lambda p: p["store"].update(extra="x"), False),
            ("store-root-absolute", lambda p: p["store"].update(store_root="/abs"), False),
            ("store-machine-rel-reserved",
             lambda p: p["store"].update(machine_rel=".working/staging"), False),
            ("store-machine-rel-nested", lambda p: p["store"].update(machine_rel=".working/toml/x"), False),
            ("source-occupying-not-bool", lambda p: p["sources"][0].update(occupying="no"), False),
            ("source-missing-preservation", lambda p: p["sources"][0].pop("preservation"), False),
            ("source-foreign-preservation",
             lambda p: p["sources"][0].update(preservation="elsewhere/R.md"), True),
            ("op-without-source", lambda p: p["ops"].append(dict(_extra_retire)), True),
            ("effects-drift", lambda p: p["effects"]["removals"].clear(), False),
            ("release-anchor-disagrees", lambda p: p["release"].update(anchor_sha256=_D5), False),
            ("release-bad-version", lambda p: p["release"].update(version="v1.3.0"), False),
            ("release-bad-manifest", lambda p: p["release"].update(manifest_sha256="nope"), False),
            ("release-missing-anchor", lambda p: p["release"].pop("anchor"), False),
            ("release-empty-anchor", lambda p: p["release"].update(anchor=""), False),
            ("prompt-pack-bad-digest", lambda p: p["prompt_pack"].update(digest="nope"), False),
            ("prompt-pack-bad-version", lambda p: p["prompt_pack"].update(version="latest"), False),
            ("enforcement-not-list", lambda p: p.update(enforcement={}), False),
            ("enforcement-missing-platform", lambda p: p["enforcement"].pop(), False),
            ("enforcement-reordered", lambda p: _swap(p["enforcement"], 2, 3), False),
            ("enforcement-duplicate-platform",
             lambda p: p["enforcement"].__setitem__(5, dict(p["enforcement"][4])), False),
            ("enforcement-wrong-means", lambda p: p["enforcement"][0].update(means="instructions"), False),
            ("enforcement-empty-residuals", lambda p: p["enforcement"][0].update(residuals=[]), False),
            ("enforcement-duplicate-residual",
             lambda p: p["enforcement"][0].update(residuals=["a", "a"]), False),
            ("enforcement-empty-members", lambda p: p["enforcement"][0].update(members=[]), False),
            ("enforcement-unknown-key", lambda p: p["enforcement"][0].update(extra="x"), False),
            ("completion-roster-short", lambda p: p["completion"]["checks"].pop(), False),
            ("completion-roster-reordered", lambda p: p["completion"]["checks"].reverse(), False),
            ("missingness-short", lambda p: p["import_policy"]["missingness"].pop(), False),
            ("missingness-not-list", lambda p: p["import_policy"].update(missingness="all"), False),
            ("import-scope-drift", lambda p: p["import_policy"].update(scope=["legacy/RULES.md"]), False),
            ("import-scope-not-list", lambda p: p["import_policy"].update(scope="legacy/RULES.md"), False)):
        check("plan-v2-{}-invalid".format(label), _mutated(base_plan, mutate, refresh) == INVALID)
    # 14d: an out-of-vocabulary binding token -> CANNOT-EVALUATE (the module outcome model).
    for label, mutate in (
            ("store-adoption", lambda p: p["store"].update(adoption="third-adoption")),
            ("source-disposition", lambda p: p["sources"][0].update(disposition="delete")),
            ("enforcement-platform", lambda p: p["enforcement"][0].update(platform="emacs")),
            ("enforcement-means", lambda p: p["enforcement"][0].update(means="prayer")),
            ("completion-check", lambda p: p["completion"]["checks"].append("vibes")),
            ("completion-retirement", lambda p: p["completion"].update(retirement="always")),
            ("missingness-reason", lambda p: p["import_policy"]["missingness"].append("zero-fill")),
            ("import-unparsed", lambda p: p["import_policy"].update(unparsed="drop")),
            ("import-skip", lambda p: p["import_policy"].update(skip="silent")),
            # the former import-file row retired with the import engine: it is out of vocabulary now.
            ("import-file-op", lambda p: p["ops"].append(
                {"op": "import-file", "import_run_id": "imp-20260917T120000Z-0123456789abcdef",
                 "acceptance_digest": "sha256:" + "0" * 64}))):
        check("plan-v2-{}-out-of-vocab-cannot-eval".format(label),
              _mutated(base_plan, mutate) == CANNOT_EVALUATE)
    # A plan with no disposition ops and no sources is VALID; a non-list `sources` there is INVALID on its
    # own shape (the op/source correspondence has nothing to compare).
    bare = canonical_plan()
    bare["ops"] = bare["ops"][1:]; bare["sources"] = []; bare["effects"] = derive_effects(bare["ops"], [])
    check("plan-v2-no-sources-valid", validate_plan(bare).status == VALID)
    check("plan-v2-sources-not-list-invalid", _mutated(bare, lambda p: p.update(sources={})) == INVALID)

    # 14e: every disposition together is VALID: keep (no preservation, not occupying, its registration
    # rewriting the scaffolded manifest), a plain move (preserved at its destination), migrate (no op,
    # preimage home, in the import scope), and occupying retire, move and migrate sources, each archived at
    # the adoption preimage home (spec 14.2).
    _MANIFEST, _D0, _D4 = ".working/toml/manifest.toml", "sha256:" + "0" * 64, "sha256:" + "4" * 64
    full = canonical_plan()
    home = lambda path: retire_preimage(full["run_id"], path)  # noqa: E731
    full["ops"] = [
        {"op": "move-file", "source": "adopter/MOVE.md", "destination": "archive/MOVE.md",
         "source_digest": _D5},
        {"op": "move-file", "source": ".working/toml/counters.toml", "destination": "archive/counters.toml",
         "source_digest": _D5},
        {"op": "retire-file", "path": ".working/TODO.md", "preimage_digest": _D5},
    ] + full["ops"] + [{"op": "register-unmanaged", "entry": "adopter/KEEP.md", "old_digest": _D0,
                         "new_digest": _D4}]
    full["sources"] += [
        {"path": "adopter/KEEP.md", "digest": _D5, "disposition": "keep", "occupying": False},
        {"path": "adopter/MOVE.md", "digest": _D5, "disposition": "move", "occupying": False,
         "preservation": "archive/MOVE.md"},
        {"path": "adopter/OLD.md", "digest": _D5, "disposition": "migrate", "occupying": False,
         "preservation": home("adopter/OLD.md")},
        {"path": ".working/TODO.md", "digest": _D5, "disposition": "retire", "occupying": True,
         "preservation": home(".working/TODO.md")},
        {"path": ".working/toml/counters.toml", "digest": _D5, "disposition": "move", "occupying": True,
         "preservation": home(".working/toml/counters.toml")},
        {"path": ".working/FINDINGS.md", "digest": _D5, "disposition": "migrate", "occupying": True,
         "preservation": home(".working/FINDINGS.md")}]
    full["effects"] = derive_effects(full["ops"], full["sources"], _MANIFEST)
    full["import_policy"]["scope"] = [".working/FINDINGS.md", "adopter/OLD.md"]
    check("plan-v2-all-dispositions-valid", validate_plan(full).status == VALID)
    _in_moved = ".working/archive/moved/adopter/MOVE.md"
    in_moved = lambda p: (p["ops"][0].update(destination=_in_moved),  # noqa: E731
                          p["sources"][2].update(preservation=_in_moved))
    check("plan-v2-move-beneath-moved-root-valid", _mutated(full, in_moved, True) == VALID)
    for label, mutate, refresh in (
            ("migrate-outside-scope", lambda p: p["import_policy"].update(scope=["adopter/OLD.md"]), False),
            ("keep-with-preservation",
             lambda p: p["sources"][1].update(preservation="archive/KEEP.md"), False),
            ("keep-occupying", lambda p: p["sources"][1].update(occupying=True), False),
            ("move-preserved-elsewhere",
             lambda p: p["sources"][2].update(preservation="archive/OTHER.md"), True),
            ("occupying-move-at-destination",
             lambda p: p["sources"][5].update(preservation="archive/counters.toml"), True),
            ("occupying-flag-dropped", lambda p: p["sources"][5].update(occupying=False), True),
            ("migrate-moved-home", lambda p: p["sources"][3].update(preservation="archive/OLD.md"), True),
            # a migrate row has no op, so its digest and duplicate checks are the only guards on it
            ("source-bad-digest", lambda p: p["sources"][3].update(digest="nope"), True),
            ("source-duplicate-path",
             lambda p: (p["sources"].append(dict(p["sources"][3])), p["import_policy"].update(
                 scope=[".working/FINDINGS.md", "adopter/OLD.md", "adopter/OLD.md"])), True),
            ("move-into-store-outside-moved",
             lambda p: (p["ops"][0].update(destination=".working/notes/MOVE.md"),
                        p["sources"][2].update(preservation=".working/notes/MOVE.md")), True)):
        check("plan-v2-{}-invalid".format(label), _mutated(full, mutate, refresh) == INVALID)
    # 14f: a source inside a store control root, including a migrate source that has no op row, is refused
    # in homes 2 and, since spec 14.2 carries no homes qualifier, in the legacy generation too; the same plan
    # with ordinary sources is VALID in homes 2.
    ctrl = copy.deepcopy(full)
    ctrl["sources"][3] = dict(ctrl["sources"][3], path=".working/journals/old.md",
                              preservation=home(".working/journals/old.md"))
    ctrl["effects"] = derive_effects(ctrl["ops"], ctrl["sources"], _MANIFEST)
    ctrl["import_policy"]["scope"] = [".working/FINDINGS.md", ".working/journals/old.md"]
    check("plan-v2-homes2-ordinary-sources-valid", validate_plan(full, homes=2).status == VALID)
    check("plan-v2-homes2-control-source-refused", validate_plan(ctrl, homes=2).status == INVALID)
    check("plan-v2-legacy-control-source-refused", validate_plan(ctrl).status == INVALID)
    # 14g: derive_effects composes install-pack members under a non-root target only, adds a migrate
    # source's archive copy and later removal, and adds nothing for a plain move beyond its op row.
    _member = [{"path": "rules.toml", "digest": _D5}]
    check("effects-install-pack-root-target", derive_effects(
        [{"op": "install-pack", "target": ".", "members": _member}], [])["creations"]
        == [{"path": "rules.toml", "digest": _D5}])
    check("effects-install-pack-nested-target", derive_effects(
        [{"op": "install-pack", "target": "a/b", "members": _member}], [])["creations"]
        == [{"path": "a/b/rules.toml", "digest": _D5}])
    _mig = {"path": "a.md", "digest": _D5, "disposition": "migrate", "occupying": False,
            "preservation": "p/a.md"}
    check("effects-migrate-preserved-and-removed", derive_effects([], [_mig]) == {
        "creations": [{"path": "p/a.md", "digest": _D5}], "replacements": [],
        "removals": [{"path": "a.md", "digest": _D5}], "repointings": []})
    check("effects-plain-move-source-adds-nothing", derive_effects(
        [], [dict(_mig, disposition="move", preservation="b.md")])["creations"] == [])

    # 15: plan-v2 exactness (U8 fix round 1). Each vector FAILS if its corresponding check is reverted.
    # 15a: every store-targeting op agrees with the frozen store identity.
    _pres = base_plan["sources"][0]["preservation"]
    _create = lambda path: {"op": "create-file", "path": path, "content_digest": _D5}  # noqa: E731
    _run_home = _pres[:-len("/legacy/RULES.md")]
    # the init-store, render-views and record-adoption outputs of the canonical plan
    _generated = (".working/toml/manifest.toml", ".working/TODO.md", ".working/toml/adoption.toml")
    for label, mutate, refresh in (
            ("init-other-store-root", lambda p: p["ops"][1].update(store_root="other-product"), True),
            ("render-other-store-root", lambda p: p["ops"][2].update(store_root="other-product"), True),
            ("receipt-outside-store", lambda p: p["ops"][4].update(receipt_path="receipt.toml"), True),
            ("init-without-frozen-manifest", lambda p: p["ops"][1].update(
                members=[{"path": ".working/toml/counters.toml", "digest": _D5}]), True),
            ("init-other-machine-store", lambda p: p["ops"][1]["members"].append(
                {"path": ".working/data/manifest.toml", "digest": _D5}), True),
            ("store-machine-rel-contradicts-init",
             lambda p: p["store"].update(machine_rel=".working/data"), True),
            # 15b: the effects are exact: generated outputs cannot be dropped, and every enforcement member is
            # installed by an op with its bytes.
            ("effects-omit-generated", lambda p: p["effects"].update(creations=[
                r for r in p["effects"]["creations"] if r["path"] not in _generated]), False),
            ("enforcement-uninstalled", lambda p: p["ops"].pop(3), True),
            ("enforcement-digest-mismatch", lambda p: p["ops"][3]["members"][0].update(digest=_D5), True),
            ("enforcement-member-elsewhere",
             lambda p: p["enforcement"][0]["members"][0].update(path=".github/workflows/other.yml"), True),
            # 15c: each means' required residual disclosure, and every pack residual on some row.
            ("residual-omitted-ci",
             lambda p: p["enforcement"][0].update(residuals=["canonical-hand-edits"]), False),
            ("residual-omitted-pre-commit", lambda p: p["enforcement"][1].update(
                residuals=["canonical-hand-edits", "same-user-tampering"]), False),
            ("residual-omitted-deny-hook", lambda p: p["enforcement"][2].update(
                residuals=["same-user-tampering"]), False),
            ("residual-omitted-instructions", lambda p: p["enforcement"][3].update(
                residuals=["shell-or-interpreter-wrapping"]), False),
            ("pack-residual-undisclosed", lambda p: p["enforcement"][1].update(
                residuals=["per-clone-installation-and-bypass", "same-user-tampering"]), False),
            # 15d: no file target equals or contains a store directory home, the Move archive root included.
            ("move-onto-moved-root", lambda p: (
                p["sources"][0].update(disposition="move", preservation=".working/archive/moved"),
                p["ops"].__setitem__(0, {"op": "move-file", "source": "legacy/RULES.md",
                                         "source_digest": "sha256:" + "6" * 64,
                                         "destination": ".working/archive/moved"})), True),
            # (the evidence root and the Move archive root hold nothing else here; they, and the run's
            # archive, are control area too, so the control-area rule also refuses them, while the machine
            # store is refused by the home check alone)
            ("create-at-evidence-root",
             lambda p: p["ops"].append(_create(".working/imported/adoption")), True),
            ("create-at-moved-root", lambda p: p["ops"].append(_create(".working/archive/moved")), True),
            ("create-at-run-archive", lambda p: p["ops"].append(_create(_run_home)), True),
            ("create-at-machine-store", lambda p: p["ops"].append(_create(".working/toml")), True),
            # 15e: no two effects claim one path, and no creation lands on a live source or its directory.
            ("create-twice", lambda p: p["ops"].append(_create(".working/TODO.md")), True),
            ("create-at-preservation", lambda p: p["ops"].append(_create(_pres)), True),
            ("create-at-live-source", lambda p: p["ops"].append(_create("legacy/RULES.md")), True),
            ("create-live-source-directory", lambda p: p["ops"].append(_create("legacy")), True)):
        check("plan-v2-{}-invalid".format(label), _mutated(base_plan, mutate, refresh) == INVALID)
    check("plan-v2-residual-out-of-vocab-cannot-eval", _mutated(base_plan, lambda p: [
        row.update(residuals=["none"]) for row in p["enforcement"]]) == CANNOT_EVALUATE)
    for label, mutate in (
            ("two-moves-one-destination", lambda p: p["ops"][1].update(destination="archive/MOVE.md")),
            ("render-over-live-view", lambda p: p["sources"][4].update(occupying=False))):
        check("plan-v2-{}-invalid".format(label), _mutated(full, mutate, True) == INVALID)
    # Counter-vectors: a deny hook merged into an existing registration, or a governance adapter, installs its
    # member as well as a pack does; an occupying view is archived before render writes it (plan above).
    _claude = base_plan["enforcement"][2]["members"][0]
    _codex = base_plan["enforcement"][3]["members"][0]

    def _other_installs(p):
        p["ops"][3]["members"] = [m for m in p["ops"][3]["members"] if m not in (_claude, _codex)]
        p["ops"] += [{"op": "enable-hook", "registration_path": _claude["path"], "plugin_entry": "opf",
                      "old_digest": _D5, "new_digest": _claude["digest"]},
                     {"op": "plant-governance", "path": _codex["path"], "content_digest": _codex["digest"],
                      "source_member": "AGENTS.md"}]
    check("plan-v2-hook-and-governance-installs-valid", _mutated(base_plan, _other_installs, True) == VALID)
    check("plan-v2-hook-new-digest-mismatch-invalid", _mutated(base_plan, lambda p: (
        _other_installs(p), p["ops"][-2].update(new_digest=_D5)), True) == INVALID)
    # 15f: derive_effects enumerates init, render and receipt outputs as creations at their composed paths.
    check("effects-generated-outputs", derive_effects([
        {"op": "init-store", "store_root": "s", "members": [{"path": "a.toml", "digest": _D5}]},
        {"op": "render-views", "store_root": ".", "members": [{"path": "v.md", "digest": _D5}]},
        {"op": "record-adoption", "receipt_path": "r.toml", "receipt_core_digest": _D5}], [])["creations"]
        == [{"digest": _D5, "path": "r.toml"}, {"digest": _D5, "path": "s/a.toml"},
            {"digest": _D5, "path": "v.md"}])
    # 15g: the closed token spellings are pinned literally, so a spelling drift in any vocabulary goes red
    # rather than passing against the module's own constant.
    check("token-plan-format", PLAN_FORMAT == "opf.adoption.plan/v2")
    check("token-dispositions", DISPOSITIONS == ("keep", "migrate", "move", "retire"))
    check("token-adoption-kinds", ADOPTION_KINDS == ("first-adoption", "re-adoption"))
    check("token-completion-checks", COMPLETION_CHECKS == (
        "authority-freshness", "discovery-accounting", "preservation-restore", "operational-readiness",
        "wiring", "retirement-readiness"))
    check("token-retirement-rules", RETIREMENT_RULES == ("green-checks-and-matching-bytes",))
    check("token-enforcement-platforms", ENFORCEMENT_PLATFORMS == (
        "ci", "pre-commit", "claude-code", "codex", "gemini-cli", "cursor"))
    check("token-enforcement-means", ENFORCEMENT_MEANS == {
        "ci": ("ci-checks",), "pre-commit": ("staged-pre-commit",),
        "claude-code": ("deny-hook", "instructions"), "codex": ("deny-hook", "instructions"),
        "gemini-cli": ("deny-hook", "instructions"), "cursor": ("deny-hook", "instructions")})
    check("token-enforcement-residuals", ENFORCEMENT_RESIDUALS == (
        "server-side-protection-adopter-attested", "per-clone-installation-and-bypass",
        "canonical-hand-edits", "shell-or-interpreter-wrapping", "same-user-tampering",
        "unverified-platform-denial"))
    check("token-pack-residuals", PACK_RESIDUALS == (
        "per-clone-installation-and-bypass", "canonical-hand-edits", "shell-or-interpreter-wrapping",
        "same-user-tampering", "unverified-platform-denial"))
    check("token-required-residuals", ENFORCEMENT_REQUIRED_RESIDUALS == {
        "ci-checks": ("server-side-protection-adopter-attested",),
        "staged-pre-commit": ("per-clone-installation-and-bypass",),
        "deny-hook": ("shell-or-interpreter-wrapping",), "instructions": ("unverified-platform-denial",)})
    check("token-enforcement-install-ops", ENFORCEMENT_INSTALL_OPS == (
        "install-pack", "plant-governance", "enable-hook"))
    check("token-missingness-reasons", MISSINGNESS_REASONS == (
        "not_recorded_in_source", "unparsed", "ambiguous", "conflicting", "not_applicable"))
    check("token-unparsed-policies", UNPARSED_POLICIES == ("retain-verbatim",))
    check("token-skip-policies", SKIP_POLICIES == ("no-skip", "attributed-skip"))

    # 16: plan-v2 exactness (U8 fix round 2). Each vector FAILS if its corresponding check is reverted.
    _D7 = "sha256:" + "7" * 64
    _repoint = lambda path: {"op": "repoint-consumer", "path": path, "old_digest": _D5,  # noqa: E731
                             "new_digest": _D4}
    _hook = lambda path: {"op": "enable-hook", "registration_path": path, "plugin_entry": "opf",  # noqa: E731
                          "old_digest": _D5, "new_digest": _D4}
    _old_archive = ".working/archive/adoption/adopt-20250101T000000Z-0123456789abcdef/old.md"
    # A store rooted at `sub`: every store op names it, and its one source moves out of the store tree.
    # validate_plan refuses it (K1 fix 1: the planner freezes store_root "." and apply resolves protected
    # destinations there, so no plan is judged in a root apply does not share). The root-composed rules
    # still take the frozen root, so _sub_findings runs them below that gate to keep them discriminated.
    sub_plan = copy.deepcopy(base_plan)
    for table in (sub_plan["store"], sub_plan["ops"][1], sub_plan["ops"][2]):
        table["store_root"] = "sub"
    sub_plan["ops"][4]["receipt_path"] = "sub/.working/toml/adoption.toml"
    sub_plan["ops"][0] = {"op": "move-file", "source": "legacy/RULES.md", "destination": "elsewhere.md",
                          "source_digest": "sha256:" + "6" * 64}
    sub_plan["sources"][0].update(disposition="move", preservation="elsewhere.md")
    sub_plan["effects"] = derive_effects(sub_plan["ops"], sub_plan["sources"],
                                         store_manifest(sub_plan["store"]))
    check("plan-v2-sub-store-root-refused", validate_plan(sub_plan).status == INVALID)

    def _sub_findings(base, mutate=lambda p: None):
        p = copy.deepcopy(base)
        mutate(p)
        p["effects"] = derive_effects(p["ops"], p["sources"], store_manifest(p["store"]))
        findings = []
        _validate_plan_sources(p["sources"], p["run_id"], 1, findings, _frozen_store(p)[0])
        _cross_check_plan(p, [], not findings, findings)
        return findings
    check("plan-v2-sub-store-composed-rules-clean", _sub_findings(sub_plan) == [])

    def _sub_move(destination):
        return lambda p: (p["ops"][0].update(destination=destination),
                          p["sources"][0].update(preservation=destination))
    for label, base, mutate in (
            # 16a: a source left live at apply (keep, and every non-occupying source) is never replaced or
            # repointed: it stays byte-identical until its recorded retirement.
            ("repoint-retire-source", base_plan, lambda p: p["ops"].append(_repoint("legacy/RULES.md"))),
            ("hook-retire-source", base_plan, lambda p: p["ops"].append(_hook("legacy/RULES.md"))),
            ("repoint-keep-source", full, lambda p: p["ops"].append(_repoint("adopter/KEEP.md"))),
            ("hook-keep-source", full, lambda p: p["ops"].append(_hook("adopter/KEEP.md"))),
            ("repoint-move-source", full, lambda p: p["ops"].append(_repoint("adopter/MOVE.md"))),
            ("repoint-migrate-source", full, lambda p: p["ops"].append(_repoint("adopter/OLD.md"))),
            # 16b: each registration's manifest rewrite is an exact effect, chained in program order.
            ("registration-chain-broken", full, lambda p: p["ops"][8].update(old_digest=_D5)),
            ("registration-unchanged-manifest", full, lambda p: p["ops"][8].update(new_digest=_D0)),
            ("registration-before-init", full, lambda p: p["ops"].insert(0, p["ops"].pop(8))),
            ("hook-rewrites-manifest", full, lambda p: p["ops"].append(_hook(_MANIFEST))),
            # 16c: the control area is never selected, kept, created into, rewritten, relocated or removed, in
            # the legacy generation as in homes 2 (spec 14.2).
            ("keep-in-adoption-archive", full, lambda p: (
                p["sources"][1].update(path=_old_archive), p["ops"][8].update(entry=_old_archive))),
            ("retire-adoption-archive-original", base_plan, lambda p: (
                p["sources"][0].update(path=_old_archive, preservation=home(_old_archive)),
                p["ops"][0].update(path=_old_archive))),
            ("create-in-run-archive", base_plan, lambda p: p["ops"].append(_create(_run_home + "/extra.md"))),
            ("create-in-moved-archive", base_plan,
             lambda p: p["ops"].append(_create(".working/archive/moved/planted.md"))),
            ("create-in-staging", base_plan, lambda p: p["ops"].append(_create(".working/staging/x.md"))),
            ("create-in-journals", base_plan, lambda p: p["ops"].append(_create(".working/journals/x.md"))),
            ("create-in-imported", base_plan, lambda p: p["ops"].append(_create(".working/imported/x.md"))),
            ("create-in-imports", base_plan, lambda p: p["ops"].append(_create(".working/imports/x.md"))),
            ("hook-in-control-area", base_plan, lambda p: p["ops"].append(_hook(".working/staging/x.json"))),
            # 16d: a first adoption carries the init-store row that scaffolds its store.
            ("first-adoption-without-init", base_plan, lambda p: p["ops"].pop(1)),
            # 16f: init-store scaffolds only inside the store tree.
            ("init-member-at-version", base_plan, lambda p: p["ops"][1]["members"].append(
                {"path": "VERSION", "digest": _D5})),
            ("init-member-outside-store", base_plan, lambda p: p["ops"][1]["members"].append(
                {"path": "src/app.py", "digest": _D5}))):
        check("plan-v2-{}-invalid".format(label), _mutated(base, mutate, True) == INVALID)
    # 16e: the Move archive rule composes under a non-root store_root (below the root gate).
    check("plan-v2-move-into-sub-store-outside-moved-invalid",
          _sub_findings(sub_plan, _sub_move("sub/.working/toml/moved.md")) != [])
    check("plan-v2-effects-omit-registration-invalid",
          _mutated(full, lambda p: p["effects"]["replacements"].clear()) == INVALID)
    check("effects-registration-rewrites-manifest", full["effects"]["replacements"] == [
        {"path": _MANIFEST, "old_digest": _D0, "new_digest": _D4}])
    # Counter-vectors: two keeps chain their registrations, a consumer outside the sources may be repointed,
    # a re-adoption of a resolved store takes no init-store, and (below the root gate) a move beneath the sub
    # store's Move archive plans.
    for label, base, mutate in (
            ("two-registrations-chain", full, lambda p: (
                p["sources"].append({"path": "adopter/KEEP2.md", "digest": _D5, "disposition": "keep",
                                     "occupying": False}),
                p["ops"].append({"op": "register-unmanaged", "entry": "adopter/KEEP2.md", "old_digest": _D4,
                                 "new_digest": _D7}))),
            ("repoint-other-consumer", base_plan, lambda p: p["ops"].append(_repoint("docs/consumer.md"))),
            ("re-adoption-without-init", base_plan, lambda p: (
                p["ops"].pop(1), p["store"].update(adoption="re-adoption")))):
        check("plan-v2-{}-valid".format(label), _mutated(base, mutate, True) == VALID)
    check("plan-v2-move-into-sub-store-moved-valid",
          _sub_findings(sub_plan, _sub_move("sub/.working/archive/moved/moved.md")) == [])

    # 17: U8 fix round 3 discriminators. Each refusal vector FAILS if its corresponding fix is reverted; the
    # re-adoption keep-chain counter-vector passes either way and guards against over-refusal.
    # 17a (fix 1): preservation destinations compose under a non-root store_root (below the root gate, see
    # 16). A retire source of the `sub` store is preserved inside the frozen store, at
    # sub/.working/archive/adoption/<run>/..., and a preservation at the product-root archive home, outside
    # the frozen store, is refused (spec 14.2).
    sub_retire = copy.deepcopy(sub_plan)
    sub_retire["ops"][0] = {"op": "retire-file", "path": "legacy/RULES.md",
                            "preimage_digest": "sha256:" + "6" * 64}
    sub_retire["sources"][0].update(disposition="retire",
                                    preservation="sub/" + home("legacy/RULES.md"))
    sub_retire["effects"] = derive_effects(sub_retire["ops"], sub_retire["sources"],
                                           store_manifest(sub_retire["store"]))
    check("plan-v2-sub-store-preserved-in-store-valid", _sub_findings(sub_retire) == [])
    check("plan-v2-sub-store-preservation-outside-store-invalid", _sub_findings(
        sub_retire, lambda p: p["sources"][0].update(preservation=home("legacy/RULES.md"))) != [])
    # 17b (fix 2): a file at the frozen store manifest is created only by init-store's scaffold. A
    # re-adoption of a resolved store (no init-store) whose program also creates the manifest would fork
    # the registration chain's one rewrite, so it is refused; the same re-adoption without that creation
    # stays VALID (the chain's first link names the observed manifest the planner binds).
    re_create = copy.deepcopy(base_plan)
    re_create["store"]["adoption"] = "re-adoption"
    re_create["ops"].pop(1)
    re_create["sources"].append({"path": "adopter/KEEP.md", "digest": _D5, "disposition": "keep",
                                 "occupying": False})
    re_create["ops"].append({"op": "register-unmanaged", "entry": "adopter/KEEP.md",
                             "old_digest": _D0, "new_digest": _D4})
    check("plan-v2-re-adoption-keep-chain-valid", _mutated(re_create, lambda p: None, True) == VALID)
    check("plan-v2-re-adoption-manifest-creation-invalid",
          _mutated(re_create, lambda p: p["ops"].append(_create(_MANIFEST)), True) == INVALID)

    # 18: K1 fix round 1. Each refusal vector FAILS if its fix is reverted; the counter-vectors guard the
    # predicate against over-refusal. (Fix 1, the product-root store_root gate, is plan-v2-sub-store-root-
    # refused in 16.)
    _rid = base_plan["run_id"]

    def _protected(path):
        """protected_destination's reason, or None. A raise is no reason: the predicate itself refuses
        every malformed path rather than relying on its callers' filter."""
        try:
            return protected_destination(path, _rid)
        except Exception:  # noqa: BLE001
            return None
    # 18a (fix 2): protected names compare case-insensitively, so a case-insensitive filesystem cannot
    # alias .git, .aiqt, the pointers, the control area or the store .gitignore. The exemptions match only
    # their exact spelling, so a case variant of the Move archive or of this run's homes is protected too.
    for path in (".GIT/x", "docs/.Git/hooks/pre-commit", ".AIQT/x.md", "docs/.Aiqt/x.md", ".OPF.toml",
                 ".Opf.Local.toml", ".OPF.TOML/x", ".Working/staging/x", ".WORKING/journals/x",
                 ".working/Imports/x", ".working/ARCHIVE/adoption/x", ".Working/.gitignore",
                 ".working/.GITIGNORE", ".Working/archive/moved/x.md"):
        check("protected-casefold-" + path, _protected(path) is not None)
    for path in (".working/archive/moved/legacy/x.md", retire_preimage(_rid, "legacy/x.md"),
                 evidence_run("adoption", _rid) + "/x.md", ".working/TODO.md", "docs/gitnotes.md",
                 "docs/.github/x.yml", "docs/.aiqtx/x.md", "opf.toml", ".working/gitignore"):
        check("protected-ordinary-admitted-" + path, _protected(path) is None)
    # 18b (fix 3): the predicate refuses any path that is not a normalized, contained relative file path,
    # so a traversal spelling cannot reach the exemptions or step past a protected name.
    for label, path in (("dotdot-into-control", ".working/archive/moved/../staging/x"),
                        ("dotdot-to-pointer", ".working/archive/moved/../../.opf.toml"),
                        ("dotdot-escape", "../outside.md"), ("absolute", "/etc/x"),
                        ("backslash", "docs\\.git\\x"), ("empty", ""), ("repeated-slash", "docs//x.md"),
                        ("dot-segment", "./docs/x.md"), ("trailing-slash", "docs/"), ("dot", "."),
                        ("drive", "C:/x"), ("control-char", "docs/x\n.md"), ("non-str", None)):
        check("protected-malformed-" + label, _protected(path) is not None)

    from _opf_adopt_observe import self_test as observing_self_test
    observing_rc = observing_self_test(vectors_only=True)
    check("https-observer-quarantine-suite",
          type(observing_rc) is int and observing_rc == 0)

    from _opf_adopt_plan import self_test as planning_self_test
    planning_rc = planning_self_test()
    check("read-only-investigate-plan-suite",
          type(planning_rc) is int and planning_rc == 0)

    if failures:
        print("OPF-ADOPT SELF-TEST: FAIL ({} of {} checks failed)".format(len(failures), checked[0]))
        for f in failures:
            print("  FAILED: {}".format(f))
        return 1
    print("OPF-ADOPT SELF-TEST: PASS ({} schema / vocabulary / planning / observer checks)".format(
        checked[0]))
    return 0


def gather_release(request, policy):
    """Obtain untrusted observations; never authorize adoption or apply."""
    previous_path = list(sys.path)
    previous_bytecode = sys.dont_write_bytecode
    try:
        try:
            sys.dont_write_bytecode = True
            from _opf_adopt_observe import gather_release as observe_release
        finally:
            sys.dont_write_bytecode = previous_bytecode
            sys.path[:] = previous_path
        # Observation has no lazy module loads. Finish restoration before the
        # inner operation acquires rollback authority. Interpreter return and
        # inlined-call boundaries remain residuals (see the observer contract).
        return observe_release(request, policy)
    except Exception as exc:
        return (
            {"status": CANNOT_EVALUATE},
            [{"status": CANNOT_EVALUATE, "phase": "observer",
              "detail": "observer import/call failed: " + type(exc).__name__}],
        )


def investigate(product_root, *, sources, targets=()):
    """Read-only investigation; see _opf_adopt_plan for scope and residuals."""
    from _opf_adopt_plan import investigate as investigate_readonly
    return investigate_readonly(product_root, sources=sources, targets=targets)


def plan(product_root, *, sources, expected_observation_digest, product, decisions,
         ops, now, run_nonce, bindings, targets=()):
    """Freeze an inert proposal. This does not capture acceptance or authorize apply."""
    from _opf_adopt_plan import plan as plan_readonly
    return plan_readonly(
        product_root, sources=sources, targets=targets,
        expected_observation_digest=expected_observation_digest,
        product=product, decisions=decisions, ops=ops, now=now, run_nonce=run_nonce,
        bindings=bindings,
    )


def main():
    args = sys.argv[1:]
    if "--self-test" in args or "--selftest" in args:
        return self_test()
    print("usage: _opf_adopt.py --self-test (a library module; the adoption verb is `opf adopt`)",
          file=sys.stderr)
    return 2


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-test"]:
        sys.exit(self_test())
    sys.exit(main())
