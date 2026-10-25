#!/usr/bin/env python3
"""OPFiles (OPF) store resolution + manifest base/profile schema + discovery (OPF core-tooling U1).

Offline, stdlib only, fail-closed. This is the FOUNDATION module the later OPF units build on: it sets
the schema conventions and the resolution/discovery contract they follow, so it is deliberately strict
and idiomatic.

Two things live here, both from OPF-SPEC.md sections 4, 5, and 9:

  1. STORE RESOLUTION + DISCOVERY (spec 4.3 to 4.5). An OPFiles store is resolved from a PRODUCT
     REPOSITORY ROOT (the tool is aimed at one via --root, defaulting to the cwd) through a committed
     pointer `.opf.toml` (with an uncommitted `.opf.local.toml` override), NOT through a hardcoded path
     and NOT through `_gen_common.repo_root()`: an OPF store roots via the pointer or --root, not via a
     `.git` walk, because this repo is not itself an OPFiles adopter and an adopter store need not sit
     at a repo root the walk would find. Within the resolved STORE REPOSITORY ROOT the machine store is
     DISCOVERED as the single immediate subdirectory of `.working/` whose `manifest.toml` declares
     `standard = "opf"` in its `[opf]` base table (the standard name is `toml`, tried
     first, but the subdirectory is OBSERVED at each use, never hardcoded, spec 4.4/4.5).

  2. MANIFEST BASE/PROFILE SCHEMA + VALIDATOR (spec 9, 9.1). The base is the `[opf]` table plus
     the store, module, type, provider, view, deliverable, archive, unmanaged, and vendor sections. A
     profile is a `[profiles.<name>]` sub-table that may only ADD requirements: a base-only tool reads
     and validates the base alone and records each profile as present-but-unevaluated (fail-SAFE for an
     unknown profile), while a profile-aware tool additionally enforces each SUPPORTED profile and
     FAILS CLOSED on an unreadable, unparseable, base-incompatible, weakening, or mis-registered
     instance of a profile it does cover.

Fail-closed everywhere (spec 3 "Fail closed", 4.5, 17): an unreadable or unparseable input, a pointer
that does not resolve, and zero or multiple machine stores at a target are each a distinct
CANNOT-EVALUATE outcome that STOPS, never a silent empty or absent store. Every store-relative path is
read through the repo's contained (dir-handle-relative, no-follow) primitives in `_journal`, so a
symlink swapped in for a store component is refused rather than followed off-tree.

Path-root convention (spec 4.1): the pointer `.opf.toml`/`.opf.local.toml` and the public deliverables
are relative to the PRODUCT repository root; `.working/` and everything under it is relative to the
STORE repository root. The two roots coincide under the in-repo default.

House SemVer parsing (`_semver._parse`, the ONE shared source that AIQT's `check_versions` also imports)
is reused so the OPF tooling grades versions exactly as the release gates do.

Reference-tooling / spec ambiguities recorded for the finalizer (this unit resolves each the
fail-closed way and names it so the choice is reviewable, per disclose-guard-residuals):
  - `coverage_digest` canonicalization and several downstream schemas are left by spec 6.1 to "the
    schema release that follows this specification"; the `base_compat` range grammar is one such gap.
    U1 DEFINES a minimal, documented grammar (space-separated comparator clauses; see _match_base_compat)
    and nothing more.
  - Spec 4.5 says "exactly one immediate subdirectory ... trying toml first"; U1 reads this as an
    EXHAUSTIVE, strict-uniqueness scan (zero or more than one match is CANNOT-EVALUATE, per residual 17),
    with `toml` merely the expected name, rather than a short-circuit on `toml` that could mask a second
    stray store.
  - The base sections ([modules], [store], [types.*], [providers.*], [views.*], [deliverables.*],
    [archive], [unmanaged], [vendors]) are treated as CLOSED keysets (unknown key is a finding), matching
    the house closed-schema discipline; the spec's manifest is "illustrative (the schema release ... is
    normative)", so a later unit that needs a new manifest key extends the allowed set here in one place.
  - A mistyped `opf` token resolves to CANNOT-EVALUATE at BOTH a POINTER target and the default
    location (residual 17): a PRESENT `.working/` that carries no valid [opf] manifest is a
    present-but-invalid store, distinguishable from a fresh un-adopted repo (which has NO `.working/` at
    all) and never treated as absent, so `opf init` cannot overwrite it. Only a genuinely absent
    `.working/` with no pointer is NOT-ADOPTED (the `opf init` remedy).
  - A `dir:`/absolute-path pointer TARGET is opened by a component-by-component no-follow walk from the
    filesystem root, not through `Path.resolve()` (which would canonicalize symlinks away BEFORE the
    open): a symlinked store root or a symlinked ancestor of a pointer target is refused
    (CANNOT-EVALUATE), never silently followed off-tree, matching the contained no-follow discipline.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: _opf_store.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import collections.abc
import datetime
import operator
import os
import posixpath
import re
import stat
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _journal        # noqa: E402  contained (dir-fd, no-follow) readers + JournalError + containment probe
import _containment    # noqa: E402  the single race-free-primitive probe
from _semver import _parse  # noqa: E402  the shipped bare-SemVer parser (major, minor, patch) or None

import tomllib  # noqa: E402


# --- fixed names and vocabularies (spec 4) ------------------------------------------------------------

POINTER_REL = ".opf.toml"              # committed store pointer, at the PRODUCT root (spec 4.3)
LOCAL_POINTER_REL = ".opf.local.toml"  # uncommitted machine-local override, resolved FIRST (spec 4.3)
WORKING_DIRNAME = ".working"           # fixed store-tree name at the STORE root (spec 4.4)
# Store-root exclusions, anchored only at the STORE ROOT. In homes 2, .aiqt is AIQT-only
# content; OPF writes no state here. Until homes 2 is activated, legacy import state still
# lives there. Keep this exclusion for those stores and for AIQT-owned material afterwards.
# _opf_adopt derives its store-root exclusions from this tuple.
STORE_ROOT_CONTROL_DIRS = (".git", ".aiqt")
# Homes-2 topology is inert until writer activation (spec 4.2). Homes 2 has no homes migration: a
# store is at homes 2 only when created there, and no homes-1 store is migrated (spec 4.2 / 9.2).
# SUPPORTED_HOMES is the highest homes generation this tooling activates; until homes 2 activates,
# every store, whatever it declares, keeps its legacy grading (generation 1).
SUPPORTED_HOMES = 1
# Worklog 2 remains test-only until schema, doctor, and release support land.
SUPPORTED_WORKLOG = 1
EVIDENCE_INVENTORY_FORMAT = "opf.evidence.inventory/v1"  # spec 4.2
IMPORTED_DIRNAME = "imported"
ARCHIVE_DIRNAME_STORE = "archive"       # distinct from the machine-store record archive
STAGING_DIRNAME = "staging"
JOURNALS_DIRNAME = "journals"
IMPORTED_REL = "{}/{}".format(WORKING_DIRNAME, IMPORTED_DIRNAME)
ARCHIVE_REL = "{}/{}".format(WORKING_DIRNAME, ARCHIVE_DIRNAME_STORE)
STAGING_REL = "{}/{}".format(WORKING_DIRNAME, STAGING_DIRNAME)
JOURNALS_REL = "{}/{}".format(WORKING_DIRNAME, JOURNALS_DIRNAME)
STORE_TREE_CONTROL_DIRS = (IMPORTED_DIRNAME, ARCHIVE_DIRNAME_STORE, STAGING_DIRNAME, JOURNALS_DIRNAME)
RESERVED_MACHINE_SUBDIRS = ("imports",) + STORE_TREE_CONTROL_DIRS
STAGING_KINDS = ("import", "ingest", "adoption", "layout", "preview")
# The journal-home vocabulary: every staging kind plus the journal-only `record` kind (spec 4.2).
# Record names ONLY a journal home: journal_root, txn_record and allocation_record admit it, while
# stage_run, evidence_run and evidence_inventory keep refusing it, so `record` never gains a
# staging or imported home.
JOURNAL_KINDS = STAGING_KINDS + ("record",)
# Import and ingest share the import engine's grammar; adoption has its own existing grammar.
# Layout and preview reserve new prefixes with the same timestamp/hash shape; record reserves
# record- with that shape too (journal home only). This checks shape, not calendar validity,
# ownership, existence, symlinks, or permission to mutate a constructed path.
_HOME_RUN_PREFIXES = {"import": "imp", "ingest": "imp", "adoption": "adopt",
                      "layout": "layout", "preview": "preview", "record": "record"}
_HOME_RUN_SUFFIX = r"-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{16}"
# Evidence-bundle inventories: inventory.toml, then one inventory-<phase>.toml per later phase.
_EVIDENCE_INVENTORY_RE = re.compile(r"inventory(?:-[a-z][a-z0-9]{0,31})?\.toml")
GITIGNORE_BEGIN = "# >>> opf-managed >>>"
GITIGNORE_END = "# <<< opf-managed <<<"
DEFAULT_MACHINE_SUBDIR = "toml"        # standard machine-store subdir name, tried first (spec 4.4)
MANIFEST_NAME = "manifest.toml"        # discovery marker filename (spec 4.5)
COUNTERS_NAME = "counters.toml"        # the machine store's id high-water ledger (spec 8.2)
STANDARD_TOKEN = "opf"          # exact discovery token in [opf].standard (spec 4.5)
PRIOR_STANDARD_TOKEN = "devprocess"    # the RETIRED 1.0.0 discovery token (base table [devprocess]); the
                                       # OPFiles rebrand (1.1.0) renamed it to STANDARD_TOKEN. It is
                                       # recognized ONLY by `opf upgrade`, which passes it via accept_tokens
                                       # so a legacy 1.0.0 store can still be located and migrated (spec 9.2).
                                       # No other tool accepts it; the base table name always equals the
                                       # token the base declares, so acceptance is expressed as a token set.
MAX_STORE_READ_BYTES = 1 << 20         # read cap for a contained store file (manifest/pointer); a larger
                                       # store input is refused rather than read unboundedly (SECA)
SUPPORTED_SPEC_VERSION = "1.2.0"       # the OPFiles base spec_version this tooling implements; a store
                                       # declaring an OLDER spec_version is fail-closed with a distinct
                                       # finding stating that no store upgrade reaches 2.0.0,
                                       # naming the hand step that makes its tree foreign content (spec 9.2),
                                       # and one declaring a NEWER spec_version is fail-closed with an
                                       # upgrade-the-tooling finding, except only the reserved homes-2
                                       # declaration pair (spec_version 2.0.0 with homes = 2), which this
                                       # tooling grades under its 1.2.0 rules (spec 16.1) and which
                                       # homes_generation() keeps at generation 1 while SUPPORTED_HOMES is 1

# Resolution outcomes.
RESOLVED = "RESOLVED"                  # a machine store was resolved and located
NOT_ADOPTED = "NOT-ADOPTED"           # no pointer and no default store: nothing to operate on (opf init)
# Validation outcomes.
VALID = "VALID"
INVALID = "INVALID"                    # an opf store whose schema is violated (a fail-closed finding)
# Shared fail-closed outcome.
CANNOT_EVALUATE = "CANNOT-EVALUATE"    # unreadable/unresolvable/ambiguous: stop, never a silent pass

# Base storage / posture / import-state vocabularies (spec 9).
LAYOUTS = ("inline", "per-record")
POSTURES = ("off", "warn", "required")
POSTURE_RANK = {"off": 0, "warn": 1, "required": 2}      # strictest wins (spec 11)
IMPORT_STATES = ("none", "partial", "complete")

# Base optional-capability modules (spec 9 [modules]; the roster is section 8.1's module families).
KNOWN_MODULES = ("governance", "delivery_assurance", "operational_policy",
                 "concurrent_operation")

# Section 8.1 record-model type taxonomy (the single source-of-truth type-name -> namespace binding).
# A declared [types.<name>] is valid only when <name> is a known type carrying its NORMATIVE namespace;
# a module-tier type is valid only when its module is enabled in [modules]; the reserved-excluded type
# `transaction` is never valid; the namespace `CL` is reserved UNASSIGNED (no type). Kept here as one
# place so a later spec-schema release extends the roster in a single location.
BASELINE_TYPES = {
    "backlog_item": "BI",
    "done": "DN",
    "worklog": "WL",
    "finding": "FN",
    "pending_decision": "PD",
    "autonomous_decision": "AD",
    "block": "BL",
    "handoff": "HO",
    "reference": "RF",
    "contribution": "CN",
    "maintainer_decision": "MD",
    "preference_pattern": "PP",
}
# Module-tier types: type name -> (normative namespace, the module that must be enabled in [modules]).
MODULE_TYPES = {
    "maintainer_action": ("MA", "governance"),
    "artifact": ("AR", "delivery_assurance"),
    "gate_run": ("GR", "delivery_assurance"),
    "release": ("RL", "delivery_assurance"),
    "waiver": ("WV", "delivery_assurance"),
    "mode": ("MO", "operational_policy"),
    "tier_assessment": ("TA", "operational_policy"),
    "session_lease": ("SL", "concurrent_operation"),
}
# Importer-only quarantine type: a known, namespace-bound type gated by no module toggle (created only
# by an importer, never scaffolded, spec 8.1).
IMPORTER_TYPES = {
    "legacy_fragment": "LF",
}
# Reserved and EXCLUDED from the adopter standard: name and namespace reserved, never a valid declared
# type (spec 8.1); `transaction` may enter later only as a versioned module.
RESERVED_EXCLUDED_TYPES = {
    "transaction": "TX",
}

# Section shapes (closed keysets; see the ambiguity note in the module docstring).
OPF_KEYS = frozenset({"standard", "spec_version", "layout", "posture", "import_status"})
# Recognized-but-OPTIONAL [opf] keys: absent homes means legacy generation 1; required-ness waits for L4.
OPF_OPTIONAL_KEYS = frozenset({"homes", "worklog"})
STORE_KEYS = frozenset({"sync_target"})
PROFILE_KEYS = frozenset({"version", "base_compat", "posture_floor", "required_modules",
                          "extension_namespace"})
PROFILE_OPTIONAL_EXTRA = frozenset({"verification_floor"})   # profile-namespaced extras a profile MAY add
TYPE_KEYS = frozenset({"namespace"})
PROVIDER_KEYS = frozenset({"handler", "roles"})
PROVIDER_ROLES = ("create", "auth", "sync")
VIEW_KEYS = frozenset({"kind", "sources", "target"})
VIEW_KINDS = ("deterministic", "composed", "projection")
DELIVERABLE_KEYS = frozenset({"kind", "target"})
DELIVERABLE_KINDS = ("curated",)
ARCHIVE_KEYS = frozenset({"period"})
ARCHIVE_PERIODS = ("year",)
UNMANAGED_KEYS = frozenset({"paths"})
VENDORS_KEYS = frozenset({"registered"})
TOP_LEVEL_TABLES = frozenset({"opf", "store", "modules", "profiles", "types", "providers",
                              "views", "deliverables", "archive", "unmanaged", "vendors"})

_NAMESPACE_OK = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
_EXTENSION_VENDOR_OK = frozenset("abcdefghijklmnopqrstuvwxyz0123456789-")   # ASCII x-<vendor> slug alphabet


def _home_kind(kind, kinds=None):
    # `kinds` is the admitting vocabulary: STAGING_KINDS by default, JOURNAL_KINDS for the journal
    # constructors. Read from the module at call time, never bound at def time, so an edited
    # vocabulary is honoured wherever it is read.
    kinds = STAGING_KINDS if kinds is None else kinds
    if not isinstance(kind, str) or kind not in kinds:
        raise ValueError("unknown homes kind: {}".format(_safe_display(kind)))
    return kind


def _home_run(kind, run_id, kinds=None):
    kind = _home_kind(kind, kinds)
    pattern = _HOME_RUN_PREFIXES[kind] + _HOME_RUN_SUFFIX
    if not isinstance(run_id, str) or re.fullmatch(pattern, run_id) is None:
        raise ValueError("invalid {} run-id: {}".format(kind, _safe_display(run_id)))
    return "{}/{}".format(kind, run_id)


def _home_file(path):
    # Canonical file-operand grammar, matching _opf_adopt._is_contained_filepath.
    # Do not normalize: an aliased spelling is a refused input, never a different destination.
    if (not isinstance(path, str) or not path or path.startswith("/") or "\\" in path
            or re.match(r"^[A-Za-z]:", path)
            or any(c in ("", ".", "..") for c in path.split("/"))
            or any(ord(c) < 0x20 or 0x7f <= ord(c) <= 0x9f or c in ("\u2028", "\u2029") for c in path)):
        raise ValueError("invalid homes file path: {}".format(_safe_display(path)))
    return path


def stage_run(kind, run_id):
    """Store-root-relative staging path; no I/O or ownership claim."""
    return "{}/{}".format(STAGING_REL, _home_run(kind, run_id))


def evidence_run(kind, run_id):
    """Store-root-relative durable evidence path."""
    return "{}/{}".format(IMPORTED_REL, _home_run(kind, run_id))


def moved_dest(source_path):
    """Store-root-relative default Move destination, preserving source substructure."""
    return "{}/moved/{}".format(ARCHIVE_REL, _home_file(source_path))


def retire_preimage(run_id, path):
    """Store-root-relative adoption preimage path."""
    return "{}/{}/{}".format(ARCHIVE_REL, _home_run("adoption", run_id), _home_file(path))


def journal_root(kind):
    """Store-root-relative journal frames, disjoint from run projections."""
    return "{}/{}/journal".format(JOURNALS_REL, _home_kind(kind, JOURNAL_KINDS))


def txn_record(kind, run_id):
    """Store-root-relative transaction projection."""
    run = _home_run(kind, run_id, JOURNAL_KINDS)
    kind, run_id = run.split("/")
    return "{}/{}/runs/{}/transaction.toml".format(JOURNALS_REL, kind, run_id)


def allocation_record(kind, run_id):
    """Store-root-relative permanent ID reservation of one run. It lives in the journal home, so
    require_ordinary_target refuses it as a publication or rollback operand."""
    run = _home_run(kind, run_id, JOURNAL_KINDS)
    kind, run_id = run.split("/")
    return "{}/{}/allocations/{}.toml".format(JOURNALS_REL, kind, run_id)


def evidence_inventory(kind, run_id, phase=None):
    """Store-root-relative inventory of one evidence bundle. A later phase adds its own
    inventory-<phase>.toml beside the first; an inventory is never rewritten. The phase type is checked
    BEFORE the file name is built, so a non-str phase (a deeply nested table, an oversized int) refuses
    through the bounded renderer and is never formatted raw."""
    if phase is not None and type(phase) is not str:
        raise ValueError("invalid evidence inventory phase: {}".format(_safe_display(phase)))
    name = "inventory.toml" if phase is None else "inventory-{}.toml".format(phase)
    if not is_evidence_inventory_name(name):
        raise ValueError("invalid evidence inventory phase: {}".format(_safe_display(phase)))
    return "{}/{}".format(evidence_run(kind, run_id), name)


def is_evidence_inventory_name(name):
    """Whether a bundle-root file name is reserved for an evidence inventory."""
    return isinstance(name, str) and _EVIDENCE_INVENTORY_RE.fullmatch(name) is not None


HOMES2_SPEC_VERSION = "2.0.0"           # the only spec_version the homes-2 contract applies to (spec 4.2)


def homes_generation(manifest_data):
    """The store's active homes generation: 2 only when this tooling activates homes 2 and the
    manifest declares both exactly the integer 2 and spec_version 2.0.0, otherwise the legacy
    generation 1. Raising SUPPORTED_HOMES alone therefore activates nothing for a store at the
    current spec_version. Manifest validation, not this helper, reports a malformed or unknown
    declaration."""
    opf = manifest_data.get("opf") if isinstance(manifest_data, dict) else None
    declared = opf.get("homes") if isinstance(opf, dict) else None
    version = opf.get("spec_version") if isinstance(opf, dict) else None
    return 2 if (SUPPORTED_HOMES >= 2 and type(declared) is int and declared == 2
                 and version == HOMES2_SPEC_VERSION) else 1


# The imported-series machine-store leaves (spec 8.2/4.2): a store that declares spec_version
# IMPORTED_SPEC_VERSION or later keeps imported history beside the clean-series files, one leaf per
# enabled type except legacy_fragment; worklog, a ledger, uses worklog.imported.toml instead of an
# imported index. Inert until activation: the gate below requires this tooling's OWN support
# (SUPPORTED_SPEC_VERSION, currently 1.2.0, at or above IMPORTED_SPEC_VERSION), never the declared
# version alone, because C-MANIFEST ACCEPTS one newer declaration today, the reserved spec_version
# 2.0.0 / homes = 2 pair (spec 4.2).
IMPORTED_SPEC_VERSION = "1.3.0"        # the first base spec_version whose stores carry the imported series
IMPORTED_INDEX_SUFFIX = ".imported.index.toml"
IMPORTED_WORKLOG_NAME = "worklog.imported.toml"
# The namespaces the imported series admits: the baseline and module-tier bindings, never the
# importer-only legacy_fragment (LF), the reserved-excluded transaction (TX), or the unassigned CL
# (spec 8.1/8.2). _opf_schema.IMPORTED_NAMESPACES is built from these SAME taxonomy maps (that module
# imports this one, so the set is mirrored here rather than imported); the opf-check self-test
# reconciles the two rosters so they cannot drift.
_IMPORTED_SERIES_NAMESPACES = (frozenset(BASELINE_TYPES.values())
                               | frozenset(ns for ns, _module in MODULE_TYPES.values()))


def imported_series_supported():
    """True when this tooling ITSELF supports the imported series: SUPPORTED_SPEC_VERSION at or
    above IMPORTED_SPEC_VERSION, through the house SemVer parser. The ONE support comparison,
    shared by the imported_series_declared gate below and by the opf-check activation predicate
    imported_content_checks_landed, so the gate and that predicate cannot disagree about when
    the series is supported."""
    return _parse(SUPPORTED_SPEC_VERSION) >= _parse(IMPORTED_SPEC_VERSION)


def imported_series_declared(manifest_data):
    """True when the [opf] base table declares a spec_version of IMPORTED_SPEC_VERSION or later AND
    this tooling itself supports that series (imported_series_supported: SUPPORTED_SPEC_VERSION
    at or above IMPORTED_SPEC_VERSION): the version-and-support half of the activation flag the
    imported-series registrations read (spec 8.2/4.2). That flag, _opf_check.imported_series_active,
    also requires the graded homes-2 declaration (homes_generation), since the imported series
    activates only inside the 2.0.0 declaration.
    Graded through the house SemVer parser; an absent, non-string, or unparseable spec_version reads
    False (manifest validation, not this helper, reports it). The tooling-support condition mirrors
    homes_generation's gate on SUPPORTED_HOMES: validate_manifest does NOT fail every newer
    declaration closed (it accepts the reserved HOMES2_SPEC_VERSION / homes = 2 pair, spec 4.2), so a
    VALID store can declare 2.0.0 today, and reading the declaration alone would register the
    imported leaves on it while every imported-content check is still deferred. Raising
    SUPPORTED_SPEC_VERSION to IMPORTED_SPEC_VERSION is the spec 1.3.0 activation; this helper
    needs no edit then, but activation must also land the imported-content checks (C-IMPORTED-*):
    the opf-check activation tripwire refuses to certify an active imported series while that
    roster is empty, so raising the constant alone cannot ship."""
    opf = manifest_data.get("opf") if isinstance(manifest_data, dict) else None
    version = opf.get("spec_version") if isinstance(opf, dict) else None
    parsed = _parse(version) if isinstance(version, str) else None
    return (parsed is not None and parsed >= _parse(IMPORTED_SPEC_VERSION)
            and imported_series_supported())


def imported_leaf_names(enabled_types):
    """The imported-series managed-leaf FILENAMES for `enabled_types` (an iterable of enabled
    section-8.1 type names, e.g. the C-ROSTER type -> namespace mapping): one
    `<type>.imported.index.toml` per enabled non-ledger type, and worklog.imported.toml for the worklog
    ledger, whose imported home is a worklog-shaped ledger file, not an index (spec 4.2; the first
    imported-series release keeps these leaves inline in either layout). A type whose normative
    namespace lies outside _IMPORTED_SERIES_NAMESPACES -- the importer-only legacy_fragment (LF) -- and
    a name outside the section-8.1 roster contribute NO leaf (fail-closed), so the leaf roster derives
    from the same namespace set as the imported id and counter grammar and the two cannot drift
    (spec 8.2)."""
    names = set()
    for tname in enabled_types:
        namespace = BASELINE_TYPES.get(tname)
        if namespace is None and tname in MODULE_TYPES:
            namespace = MODULE_TYPES[tname][0]
        if namespace is None and tname in IMPORTER_TYPES:
            namespace = IMPORTER_TYPES[tname]
        if namespace not in _IMPORTED_SERIES_NAMESPACES:
            continue
        names.add(IMPORTED_WORKLOG_NAME if tname == "worklog" else tname + IMPORTED_INDEX_SUFFIX)
    return frozenset(names)


def store_control_roots(homes):
    """Store-relative control roots for a homes generation. A legacy store reserves only the
    imports staging tree; the homes-2 names are ordinary store paths there."""
    if type(homes) is not int or homes not in (1, 2):
        raise ValueError("unknown homes generation: {}".format(_safe_display(homes)))
    names = RESERVED_MACHINE_SUBDIRS if homes == 2 else RESERVED_MACHINE_SUBDIRS[:1]
    return tuple("{}/{}".format(WORKING_DIRNAME, n) for n in names)


def overlaps_home(path, home):
    """Whether a contained operand equals, contains, or lies within a reserved home. The comparison
    is byte-exact: on a case-insensitive or normalizing filesystem a differently cased or composed
    spelling can alias a home and is not caught here (disclosed residual, spec 4.2)."""
    if not _is_contained_relpath(path):
        raise ValueError("not a contained store-relative path: {}".format(_safe_display(path)))
    path = posixpath.normpath(path)
    return path == "." or path == home or path.startswith(home + "/") or home.startswith(path + "/")


def require_ordinary_target(path):
    """Homes-2 boundary: refuse journal operands, including ancestors. Internal journal writes use
    the held capability. Only homes-2 entry points call this (the capability-bound journal API and
    homes-2 view planning); legacy transactions keep their legacy operand handling."""
    if overlaps_home(path, JOURNALS_REL):
        raise ValueError("ordinary operation target {} overlaps the journal home".format(_safe_display(path)))


def render_homes_gitignore():
    """Pure homes-2 block renderer. This version does not install it in any store.
    This is not access control: git add -f can still stage ignored state.
    """
    ignored = (JOURNALS_DIRNAME, STAGING_DIRNAME)
    if not set(ignored) <= set(STORE_TREE_CONTROL_DIRS):
        raise ValueError("ignored homes are not registered store-tree control directories")
    return "\n".join((GITIGNORE_BEGIN,) + tuple("/{}/".format(n) for n in ignored) + (GITIGNORE_END, ""))


def homes_gitignore_matches(text):
    """Exact managed-block drift check; adopter text outside the block is preserved.
    Only block bytes are checked, not effective git rules or index state. Activation must
    inspect both before installation (_opf_write_guard.inspect_homes_gitignore is that
    read-only inspector); a tracked control path requires reviewed untracking.
    """
    if not isinstance(text, str):
        return False
    lines = text.splitlines(keepends=True)
    starts = [i for i, line in enumerate(lines) if line.rstrip("\r\n") == GITIGNORE_BEGIN]
    ends = [i for i, line in enumerate(lines) if line.rstrip("\r\n") == GITIGNORE_END]
    if len(starts) != 1 or len(ends) != 1 or starts[0] >= ends[0]:
        return False
    return "".join(lines[starts[0]:ends[0] + 1]) == render_homes_gitignore()


def _homes_gitignore_markers(text):
    """The BEGIN/END marker-line indexes of `text` over splitlines(keepends=True), recognized
    exactly as homes_gitignore_matches recognizes them (one source for the marker grammar)."""
    lines = text.splitlines(keepends=True)
    starts = [i for i, line in enumerate(lines) if line.rstrip("\r\n") == GITIGNORE_BEGIN]
    ends = [i for i, line in enumerate(lines) if line.rstrip("\r\n") == GITIGNORE_END]
    return lines, starts, ends


def preview_homes_gitignore_rewrite(existing):
    """The reviewed-rewrite preview for a drifted managed block: the exact replacement
    .working/.gitignore bytes, with the single BEGIN..END marker region replaced by
    render_homes_gitignore() and every adopter byte outside the markers preserved byte-exact
    (a surrogateescape round trip, so non-UTF-8 adopter bytes survive). Defined ONLY for a
    well-formed drifted block, exactly one BEGIN line before exactly one END line whose region
    is not the exact block; anything else raises ValueError: an exact block needs no rewrite, a
    file without markers is the append case plan_homes_gitignore handles, and duplicated,
    unbalanced or reversed markers leave no single region to replace, so no preview exists and
    the drift refusal stands. Nothing is applied here: the caller shows this preview for review
    and passes it back, with the reviewed bytes, as plan_homes_gitignore(existing,
    approved_rewrite=..., reviewed_existing=...) only on explicit approval, never silently."""
    if not isinstance(existing, bytes):
        raise ValueError("homes-gitignore rewrite preview needs the current file bytes")
    text = existing.decode("utf-8", "surrogateescape")
    if homes_gitignore_matches(text):
        raise ValueError("homes-gitignore rewrite preview: the managed block is already exact; "
                         "nothing to rewrite")
    lines, starts, ends = _homes_gitignore_markers(text)
    if len(starts) != 1 or len(ends) != 1 or starts[0] >= ends[0]:
        raise ValueError(
            "homes-gitignore-block-drift: the marker structure is not a single BEGIN..END region "
            "(duplicated, unbalanced or reversed markers), so no reviewed rewrite can be "
            "previewed; reconcile .working/.gitignore by hand")
    result = "".join(lines[:starts[0]]) + render_homes_gitignore() + "".join(lines[ends[0] + 1:])
    if not homes_gitignore_matches(result):
        raise ValueError("homes-gitignore rewrite preview failed its own drift check; refusing")
    return result.encode("utf-8", "surrogateescape")


def plan_homes_gitignore(existing, approved_rewrite=None, reviewed_existing=None):
    """Pure .working/.gitignore reconciliation planner: no git, no filesystem, no write.
    `existing` is the current file's bytes, or None when the file is absent. Returns the full
    new file bytes to install, or None when the file already carries the exact managed block
    (no change). Adopter text is preserved byte-exact: the result is `existing` plus the
    appended block (with one separating newline when `existing` does not end in one), or the
    file is left alone; the block is appended last, never inserted, and non-UTF-8 adopter
    bytes survive a surrogateescape round trip. Any marker line without an exact block match
    (a duplicated, unbalanced, reversed, drifted or CRLF block) raises the named ValueError
    refusal homes-gitignore-block-drift and is never repaired in place next to adopter text.
    The one sanctioned repair is the reviewed-rewrite path: pass the bytes
    preview_homes_gitignore_rewrite returned, after explicit review and approval, as
    `approved_rewrite`, with the exact file bytes that preview was computed from as
    `reviewed_existing`; the planner returns exactly those bytes, and only while `existing` still
    byte-matches `reviewed_existing` and a fresh preview still byte-matches the approval, so a file
    changed since review refuses (fail-closed). The prestate binding is what refuses an edit INSIDE
    the drifted block: the replacement discards the whole marker region, so such an edit yields the
    same preview and only the reviewed bytes can tell it apart. A supplied approval is checked
    BEFORE the create, no-op and append branches: a file now absent, marker-free or already exact is
    not the reviewed file, so the stale approval refuses rather than being dropped for another plan.
    Installation is not performed here: _opf_write_guard.inspect_homes_gitignore must gate the
    write on effective ignore rules and the index first, and the index is never mutated."""
    if approved_rewrite is not None and not isinstance(approved_rewrite, bytes):
        raise ValueError("homes-gitignore approved rewrite must be the previewed bytes")
    if approved_rewrite is not None:
        try:
            bound = (isinstance(existing, bytes) and reviewed_existing == existing
                     and approved_rewrite == preview_homes_gitignore_rewrite(existing))
        except ValueError:
            bound = False    # no preview exists for the current bytes: not the reviewed drifted file
        if not bound:
            raise ValueError(
                "homes-gitignore-block-drift: the current file does not byte-match the reviewed "
                "bytes, or the approved rewrite does not byte-match the current preview (the file "
                "changed since review, or the approval is stale); re-preview, re-review and "
                "re-approve (fail-closed)")
        return approved_rewrite
    block = render_homes_gitignore().encode("utf-8")
    if existing is None:
        return block
    if not isinstance(existing, bytes):
        raise ValueError("homes-gitignore planning needs the current file bytes or None")
    text = existing.decode("utf-8", "surrogateescape")
    if homes_gitignore_matches(text):
        return None
    _lines, starts, ends = _homes_gitignore_markers(text)
    if not starts and not ends:
        result = existing + (b"\n" if existing and not existing.endswith(b"\n") else b"") + block
        if not (result.startswith(existing)
                and homes_gitignore_matches(result.decode("utf-8", "surrogateescape"))):
            raise ValueError("homes-gitignore append postcondition failed; refusing to plan")
        return result
    raise ValueError(
        "homes-gitignore-block-drift: .working/.gitignore carries a marker line without the "
        "exact managed block (a hand-edited, duplicated, unbalanced, reversed or CRLF block). "
        "It is never rewritten silently next to adopter text: review the exact replacement "
        "(preview_homes_gitignore_rewrite) and pass it back as approved_rewrite, with the "
        "reviewed bytes as reviewed_existing, on explicit approval, or reconcile the file by hand")


class StoreError(Exception):
    """A store-side read or resolution cannot be completed (unreadable, unparseable, a refused symlink).
    Callers map it to a CANNOT-EVALUATE outcome: fail-closed, never a silent empty store."""


# --- small result carriers (the doctor.py Result idiom) ----------------------------------------------

class Resolution:
    __slots__ = ("status", "detail", "store_root", "machine_dir", "machine_rel", "pointer_source",
                 "target", "product_root")

    def __init__(self, status, detail="", store_root=None, machine_dir=None, machine_rel=None,
                 pointer_source=None, target=None, product_root=None):
        self.status = status              # RESOLVED / NOT_ADOPTED / CANNOT_EVALUATE
        self.detail = detail
        self.store_root = store_root      # Path to the resolved STORE repository root
        self.machine_dir = machine_dir    # the observed machine-store subdir name (e.g. "toml")
        self.machine_rel = machine_rel    # store-relative path to the machine store (".working/<dir>")
        self.pointer_source = pointer_source  # "local-override" / "committed" / "default"
        self.target = target              # the parsed Target the pointer named (or None for default)
        self.product_root = product_root  # the PRODUCT repository root this store was resolved from (the
                                          # authoritative binding for the product-scope deliverables VERSION
                                          # and CHANGELOG.md; explicit-binding-over-ambient-context)


class Target:
    __slots__ = ("kind", "value", "local")

    def __init__(self, kind, value, local):
        self.kind = kind                  # "dir" / "path" / "git" / "github" / "gitlab"
        self.value = value                # the raw target payload (after any scheme prefix)
        self.local = local                # bool: resolvable to a local filesystem path in this unit


class ManifestValidation:
    __slots__ = ("status", "findings", "unevaluated_profiles", "base", "profiles")

    def __init__(self, status, findings=None, unevaluated_profiles=None, base=None, profiles=None):
        self.status = status              # VALID / INVALID / CANNOT_EVALUATE
        self.findings = findings or []
        self.unevaluated_profiles = unevaluated_profiles or []
        self.base = base or {}
        self.profiles = profiles or {}


# --- contained reads (the pin.py idiom, mapped to StoreError) -----------------------------------------

def _close_fd_exc_safe(fd):
    """Close a descriptor in a `finally` or an `except` handler without letting a failing close REPLACE
    the exception already in flight there (#377; the semantics of #378's _journal._close_fd_yielding).
    An exception is in flight only when it is unwinding through, or being handled in, the CALLING frame
    (its traceback head is that frame): then the close is quiet and that exception propagates unchanged.
    Otherwise the close error propagates fail-closed, including in code reached normally from inside a
    CALLER's `except` block, whose exception is not in flight here. Either way the descriptor is closed
    with exactly ONE os.close (P1): a close that raises has still released the number (man 2 close: the
    Linux kernel "always releases the file descriptor early in the close operation", and retrying "is the
    wrong thing to do, since this may cause a reused file descriptor from another thread to be closed"),
    so a raise is never followed by an fstat or fcntl probe or a second close. An ExitStack callback runs
    in contextlib's frame, where this test cannot see the exception the stack is unwinding: register such
    a close with _close_fd_on_exit. Residuals (disclosed): a `finally` reached NORMALLY while lexically
    inside an `except` handler of the SAME function would read that handled exception as in flight (as in
    #378), and no call site is nested that way; a platform that keeps the descriptor on a failed close
    (HP-UX, and POSIX.1-2024 as close(2) reports it) leaks it until exit, which Linux does not, and macOS
    is assumed not to. A close error raised leaves marked (_journal._mark_fd_release_raised), so a handler
    reading OSError as a signal raises it (_journal._fd_release_fault)."""
    tb = sys.exc_info()[2]
    in_flight = tb is not None and tb.tb_frame is sys._getframe(1)
    try:
        os.close(fd)
    except OSError as exc:
        if not in_flight:
            _journal._mark_fd_release_raised(exc)
            raise


def _close_fd_on_exit(fd, exc_type, exc, tb):
    """The ExitStack form of _close_fd_exc_safe (#377 fix 2), registered on the acquisition statement as
    `stack.push(functools.partial(_close_fd_on_exit, fd := ...))`. ExitStack hands an exit callback the
    exception it is unwinding (the body's, or an earlier callback's close error), so the close is quiet
    while one is and propagates fail-closed when none is. One os.close either way (P1, as
    _close_fd_exc_safe): a raising close has released the number, which is never probed or closed again.
    Returns None, so it never suppresses. A close error raised leaves marked, as _close_fd_exc_safe's."""
    try:
        os.close(fd)
    except OSError as cexc:
        if exc is None:
            _journal._mark_fd_release_raised(cexc)
            raise


def _open_root_fd(root):
    """Open a root directory fd with O_NOFOLLOW, so a symlinked final root component is refused rather
    than followed off-tree (the migrate.py/pin.py idiom). Raises OSError, mapped by the caller. Used for
    the PRODUCT root, the operator's trusted --root anchor."""
    return os.open(str(root), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)


# The walk's TRAVERSE access mode: O_PATH where the platform has it (Linux), so a component that is only
# crossed or probed, never read, needs SEARCH (execute) permission alone, exactly as the kernel's own path
# resolution does. An ancestor with search but no read permission (mode 0711: a root-owned scratch root, or
# many systems' /home/<user>) is therefore walkable, where the old O_RDONLY-per-component walk failed it
# with EACCES and turned every store beneath it into CANNOT-EVALUATE. Where os.O_PATH is absent the walk
# keeps the previous O_RDONLY open for every component, so a search-only ancestor still fails EACCES there
# (disclosed residual, see _open_dir_nofollow). The symlink refusal is NOT weakened: O_PATH|O_NOFOLLOW on a
# symlink opens the link object itself, and the paired O_DIRECTORY then refuses it. The refusal is accepted
# as either errno of the no-follow pair, ENOTDIR or ELOOP, at an O_PATH step and an O_RDONLY step alike
# (current Linux reports ENOTDIR for both; the self-test pins the pair, never one errno per access mode),
# so a symlinked component still never resolves, with or without O_PATH.
_WALK_O_TRAVERSE = getattr(os, "O_PATH", os.O_RDONLY)


def _open_dir_nofollow(abspath, *, traverse_only=False):
    """Open an ABSOLUTE directory path as a dir fd by walking each component from the filesystem root with
    O_DIRECTORY|O_NOFOLLOW, so a symlinked store root OR any symlinked ancestor is refused (either errno of
    the no-follow pair, ENOTDIR or ELOOP, at an O_PATH step and an O_RDONLY step alike; see _WALK_O_TRAVERSE)
    rather than silently followed off-tree, matching the contained no-follow discipline (spec 4.5, 17; the
    symlink-resolution rule). INTERMEDIATE components are opened with _WALK_O_TRAVERSE (O_PATH, search
    permission only, where it exists), so an ancestor with search but no read permission no longer fails the
    walk; the FINAL component is opened O_RDONLY as before, so the caller receives a READABLE descriptor. With
    traverse_only=True the final component too is opened _WALK_O_TRAVERSE, for a caller that only TRAVERSES
    the result (fstat, or dir_fd-relative lookups, which need search permission only) and never reads it, so a
    search-only directory can itself be opened and probed; such a descriptor is NOT readable where O_PATH
    exists.
    Where os.O_PATH is absent every component keeps the previous O_RDONLY open and a search-only ancestor
    still refuses EACCES (disclosed residual; the refusal direction is fail-closed, never fail-open).
    `Path.resolve()` is deliberately NOT used to resolve a pointer target: it canonicalizes symlinks
    BEFORE the open, so a symlinked target would resolve away and open successfully. Raises OSError,
    which the caller maps to CANNOT-EVALUATE."""
    if "\x00" in str(abspath):
        # os.open raises ValueError (not the OSError this helper documents) for an embedded NUL; refuse it
        # here as OSError so a NUL-bearing pointer target fails closed to CANNOT-EVALUATE at the caller
        # rather than escaping unmapped (guard-input-soundness; defence in depth behind the caller's
        # existence pre-check that already shields the current resolve path).
        raise OSError("store root {!r} carries an embedded NUL byte".format(str(abspath)))
    parts = Path(abspath).parts
    # POSIX preserves a leading '//' as a distinct root anchor (pathlib yields '//' for exactly two leading
    # slashes; '///...' collapses to '/'), so accept both root spellings rather than falsely rejecting a
    # '//'-anchored path with a message claiming it is not absolute (over-fire; class 2).
    if not parts or parts[0] not in (os.sep, os.sep + os.sep):
        raise OSError("store root {!r} is not an absolute POSIX path".format(str(abspath)))
    # Descriptor ownership: `held` lists every descriptor this walk still owns, from the moment each open
    # returns until the return hands the last one to the caller. The protected region spans the root
    # open through the return itself. During a hand-off BOTH descriptors are held: the child is appended
    # BEFORE the parent leaves `held`, and the parent's ownership is cleared (popped) immediately before
    # its close, a single os.close (P1, #378: _journal._close_fd_propagating is a bare os.close; a raising
    # close has released its number, close(2), and it is never touched again), so a failing parent close
    # can neither leak the child nor double-close the parent. Any failure or interruption closes every
    # descriptor still held and re-raises. Residual (sub-line, disclosed): an interruption between
    # an open returning and its append, or between a pop and its close, leaks that one descriptor.
    final_access = _WALK_O_TRAVERSE if traverse_only else os.O_RDONLY
    held = []
    try:
        # The filesystem root itself is never a symlink. It is the FINAL component only for "/" or "//".
        root_access = final_access if len(parts) == 1 else _WALK_O_TRAVERSE
        held.append(os.open(parts[0], root_access | os.O_DIRECTORY))
        last = len(parts) - 1
        for index, comp in enumerate(parts[1:], 1):
            access = final_access if index == last else _WALK_O_TRAVERSE
            held.append(os.open(comp, access | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=held[-1]))
            try:
                _journal._close_fd_propagating(held.pop(-2))
            except BaseException as cexc:  # noqa: BLE001  marked: a caller never reads it as an open error
                _journal._mark_fd_release_raised(cexc)
                raise
        return held.pop()
    except BaseException as exc:
        closing = []    # what a close raised that is not an OSError: resolved against `exc` below
        while held:
            try:
                _journal._close_fd_propagating(held.pop())
            except OSError as cexc:
                exc.add_note("additionally the no-follow walk descriptor could not be closed ({})".format(
                    cexc))
            except BaseException as cexc:   # noqa: BLE001  keep closing; resolved below, never dropped
                closing.append(cexc)
        # the one first-interrupt selection: an interrupt in flight keeps propagating as itself, each
        # close exception noted on it, and none replaces it (_journal._yield_close_exceptions)
        _journal._yield_close_exceptions(exc, closing)
        raise


def _open_store_root_fd(store_root, pointer):
    """Open a resolved store root as a dir fd. The DEFAULT location IS the product root (the operator's
    trusted --root anchor), opened final-component no-follow. A POINTER-named target (a `dir:`/absolute
    path) is opened by the no-follow walk from the filesystem root, so a symlinked store root or a
    symlinked ancestor is refused rather than followed. Raises OSError, mapped to CANNOT-EVALUATE."""
    if pointer:
        return _open_dir_nofollow(store_root)
    return _open_root_fd(store_root)


def _read_toml_contained(root_fd, relpath, *, with_raw=False):
    """Read and parse a contained TOML file beneath root_fd, no-follow. Returns the parsed dict, or None
    when the file (or a parent) is absent. with_raw=True returns (original_bytes, parsed_dict) on
    presence, preserving the same read safeguards. StoreError (a cannot-evaluate) on an unreadable file, a
    refused symlink, or a TOML/parse error: an unreadable input is a failure, never an empty pass."""
    data = _read_store_bytes_contained(root_fd, relpath)
    if data is None:
        return None
    parsed = _parse_store_toml(relpath, data)
    return (data, parsed) if with_raw else parsed


def _read_store_bytes_contained(root_fd, relpath, allowance=None):
    """The byte half of _read_toml_contained: read a contained store CONTROL file's exact bytes beneath
    root_fd, no-follow, singly-linked, capped. Returns the raw bytes, or None when the file (or a
    parent) is absent; StoreError on any unreadable shape. Split out so the descriptor-bound discovery
    (discover_machine_store_fd) can keep the EXACT bytes it parses for the resolved manifest rather
    than re-opening it by path (round-5 defect 1). `allowance`, when given, meters the read: a
    one-element list holding the bytes the caller still allows its store reads, the read stopping at the
    smaller of MAX_STORE_READ_BYTES and that remainder (never a byte past it) and the bytes read deducted
    from it, a file that does not fit refused. None (every caller but source discovery) reads unmetered,
    exactly as before."""
    try:
        st = _journal._lstat_contained(root_fd, relpath)
    except (_journal.JournalError, OSError) as exc:
        _journal._fd_release_fault(exc)    # a close's exception is raised as itself, never a cannot-evaluate
        # _lstat_contained walks the contained parents no-follow and can raise JournalError (a backslash,
        # control-character, or refused/unreadable intermediate component) or OSError; neither is a
        # StoreError, so an unwrapped raise escapes resolve_store/discover/load_manifest uncaught. Map it to
        # a fail-closed StoreError (routed to CANNOT-EVALUATE), never an uncaught crash on an adopter-
        # controlled store tree (check-fails-closed-on-unreadable).
        raise StoreError("cannot stat {} ({})".format(relpath, exc))
    if st is None:
        return None
    # A non-regular entry (a FIFO, device, socket, or directory) is refused BEFORE any open: opening a
    # FIFO O_RDONLY with no writer blocks the process forever, so the regular-file gate is checked on the
    # lstat result rather than after _read_contained opens the target (check-fails-closed-on-unreadable,
    # SECA resource-bounds; an unbounded block is worse than a crash for the doctor verb).
    if not stat.S_ISREG(st.st_mode):
        raise StoreError("{} is present but is not a regular file (an exotic entry; fail-closed, never "
                         "opened)".format(relpath))
    # A store input is not read unboundedly: refuse a file larger than the read cap on the size the lstat
    # already returned, so a multi-gigabyte stray file in an adopter-controlled tree meets a controlled
    # refusal rather than exhausting memory during resolution (SECA resource-bounds). This lstat size is a
    # pre-open FAST REJECT bound to the size BEFORE the open; a file swapped or grown between this lstat and
    # the open is caught by the post-read length check below (disclose-guard-residuals).
    if st.st_size > MAX_STORE_READ_BYTES:
        raise StoreError("{} is {} bytes, over the {}-byte store-read cap (fail-closed)".format(
            relpath, st.st_size, MAX_STORE_READ_BYTES))
    limit = None
    if allowance is not None:
        # A metered read is bounded DURING the read, not only classified after it: a file grown between
        # this lstat and the read stops at the limit, so it can outrun neither the per-file cap nor the
        # budget the caller charged for its store reads (source discovery; SECA resource-bounds).
        limit = min(MAX_STORE_READ_BYTES, allowance[0])
        if st.st_size > limit:
            raise StoreError("{} is {} bytes, over the {} bytes left of the read allowance (fail-closed)".format(
                relpath, st.st_size, limit))
    try:
        # F-R17-A1: a STORE control file (a manifest or a `.opf.toml`/`.opf.local.toml` pointer) is refused
        # when it is multiply-linked. The decision is made on the OPENED fd's stat inside _read_contained,
        # not the pre-open _lstat_contained probe above, so a hardlink to an out-of-tree victim cannot let a
        # resolved store posture silently track the victim inode. This reader serves ONLY manifest and pointer
        # paths, so the single-link requirement covers both without touching generic product reads.
        if limit is None:
            data, _ = _journal._read_contained(root_fd, relpath, require_single_link=True)
        else:
            data, _ = _journal._read_contained(root_fd, relpath, require_single_link=True, cap=limit)
    except (_journal.JournalError, OSError) as exc:
        _journal._fd_release_fault(exc)    # a close's exception is raised as itself, never a cannot-evaluate
        # _read_contained maps its open/read errors to JournalError, but its post-open os.fstat can still
        # raise a BARE OSError (a device/EIO-level failure) that would otherwise escape resolve_store
        # uncaught; catch OSError alongside JournalError here (as the _lstat_contained choke point above
        # already does) so any unreadable manifest/pointer is a fail-closed StoreError, never a crash
        # (check-fails-closed-on-unreadable).
        raise StoreError("cannot read {} ({})".format(relpath, exc))
    # Re-check the bytes ACTUALLY read against the cap: the pre-open lstat size is bound to the file as it
    # was BEFORE the open, so a store file swapped in or grown between that lstat and the open (a TOCTOU race
    # on an adopter-controlled tree) is refused here rather than parsed over the cap. Residual: an unmetered
    # read reads the bytes before this refusal, so a concurrently-growing file is bounded at classification
    # (and by the journal product-read ceiling), not mid-read; a metered read stops at its limit instead
    # (SECA resource-bounds; disclose-guard-residuals).
    if len(data) > MAX_STORE_READ_BYTES:
        raise StoreError("{} read {} bytes, over the {}-byte store-read cap (a raced swap or growth past "
                         "the pre-open size; fail-closed)".format(relpath, len(data), MAX_STORE_READ_BYTES))
    if allowance is not None:
        allowance[0] -= len(data)
    return data


def _parse_store_toml(relpath, data):
    """The parse half of _read_toml_contained: parse store-control TOML bytes, with every
    present-but-unparseable class mapped to a fail-closed StoreError."""
    try:
        return tomllib.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        # tomllib.TOMLDecodeError is a ValueError subclass, but an oversized BASE-10 integer literal (a
        # token past CPython's ~4300-digit limit) makes tomllib raise a BARE ValueError from int(), not a
        # TOMLDecodeError, so a hostile store file would otherwise escape resolve_store uncaught; catch the
        # ValueError base so a present-but-unparseable input is a fail-closed StoreError, never a crash
        # (check-fails-closed-on-unreadable; unreadable includes present-but-unparseable).
        raise StoreError("cannot parse {} ({})".format(relpath, exc))
    except (RecursionError, MemoryError) as exc:
        # tomllib raises RecursionError on a deeply-nested value and, from CPython 3.14.8, on a key of more
        # than 1000 dotted parts, and a many-part key can exhaust memory first (MemoryError under a memory
        # limit): a present-but-unparseable input is a fail-closed StoreError (the caller routes it to
        # CANNOT-EVALUATE), never an uncontrolled crash, and its message names both causes
        # (check-fails-closed-on-unreadable; unreadable includes present-but-unparseable).
        raise StoreError("cannot parse {} (input nesting is too deep or a dotted key has too many parts; present "
                         "but unparseable): {}".format(relpath, exc))


# Self-test seam for that RecursionError class, shared by the opf self-tests. _ST_DEEP_TOML nests a value past
# the recursion limit; _ST_LONG_KEY_TOML is a 1001-part key, which raises only where tomllib caps key parts
# (CPython 3.14.8 on) or under _StKeyPartCap, which emulates that cap on an interpreter without it.
_ST_DEEP_TOML = "deep = " + "[" * 5000 + "]" * 5000 + "\n"
_ST_LONG_KEY_TOML = "k" + ".k" * 1000 + " = 1\n"


class _StKeyPartCap:
    """While held, tomllib refuses a key of more than 1000 parts with RecursionError (or the class given,
    MemoryError to emulate a many-part key under a memory limit): tomllib._parser.parse_key is rebound on
    entry and restored on exit. The given class is injected even when the underlying parse_key refuses the
    key first (an interpreter with its own cap, CPython 3.14.8 on, raises RecursionError inside it), so the
    class a vector asserts never depends on the host. Self-test only; a vector asserts the class, never
    this message."""

    def __init__(self, kind=RecursionError):
        self._kind = kind

    def __enter__(self):
        real = self._real = tomllib._parser.parse_key
        kind = self._kind

        def capped(src, pos):
            try:
                pos, key = real(src, pos)
            except (RecursionError, MemoryError):
                # the interpreter's own key-part cap fired first; still inject the requested class
                raise kind("emulated key-part cap") from None
            if len(key) > 1000:
                raise kind("emulated key-part cap")
            return pos, key
        tomllib._parser.parse_key = capped
        return self

    def __exit__(self, *exc_info):
        tomllib._parser.parse_key = self._real
        return False


def _immediate_subdirs(store_root_fd, working_rel):
    """The immediate real subdirectory names of `working_rel` beneath store_root_fd, listed no-follow.
    A symlinked entry is skipped (never followed). Returns a sorted list of names. Raises StoreError
    when `working_rel` is present but is not a directory or is a refused symlink; returns None when it
    is absent (the caller reads absence as "no store found" rather than as an error)."""
    wfd = _open_working_dir_fd(store_root_fd, working_rel)
    if wfd is None:
        return None
    try:
        return _list_real_subdirs(wfd, working_rel)
    finally:
        inflight = _journal._in_flight_in(sys._getframe())
        try:
            _close_fd_exc_safe(wfd)   # in THIS frame: the #377 frame test sees what is in flight
        except OSError:
            raise                       # #377: raised only with nothing in flight here
        except BaseException as exc:    # noqa: BLE001  the first interrupt propagates as itself
            _journal._yield_close_exceptions(inflight, [exc])


def _open_working_dir_fd(store_root_fd, working_rel):
    """The open half of _immediate_subdirs: open `working_rel` beneath store_root_fd no-follow and
    return the directory fd, HELD (the caller owns and closes it), or None when it is absent. Split out
    so the descriptor-bound discovery (discover_machine_store_fd) can keep the LISTING descriptor open
    while it reads each listed manifest beneath it (round-5 defect 1). StoreError as
    _immediate_subdirs: a present non-directory or a refused symlink fails closed."""
    try:
        pfd, name = _journal._open_parent(store_root_fd, working_rel)
    except FileNotFoundError as exc:
        _journal._fd_release_fault(exc)    # a close's exception is never read as absent
        return None
    except (OSError, _journal.JournalError) as exc:
        _journal._fd_release_fault(exc)    # nor as a cannot-evaluate
        raise StoreError("cannot open the store tree parent of {} ({})".format(working_rel, exc))
    wfd = None
    try:
        try:
            wfd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=pfd)
        except FileNotFoundError:
            return None
        except NotADirectoryError as exc:
            raise StoreError("{} is present but is not a directory ({})".format(working_rel, exc))
        except OSError as exc:
            # O_NOFOLLOW refuses a symlinked .working with ELOOP: refused, not followed, fail-closed.
            raise StoreError("cannot open {} no-follow ({})".format(working_rel, exc))
    finally:
        # ROUND-6 defect: the new descriptor is HELD in a local across the parent close, so a parent
        # close that reports an error cannot abandon the return and leak the just-opened `working_rel`
        # fd (one per discovery/resolution/investigation call). It is released quietly, the parent close
        # is _close_fd_exc_safe's single os.close (P1: a raising close has released its number, close(2),
        # and it is never touched again), and its close error propagates fail-closed unless an exception is
        # already in flight here, which then keeps propagating in its place.
        inflight = _journal._in_flight_in(sys._getframe())
        try:
            _close_fd_exc_safe(pfd)
        except OSError:
            if wfd is not None:
                _journal._close_fd_quietly(wfd)
            raise
        except BaseException as exc:    # noqa: BLE001  the first interrupt propagates as itself
            _journal._yield_close_exceptions(inflight, [exc])
    return wfd


def _list_real_subdirs(wfd, working_rel):
    """The listing half of _immediate_subdirs: the sorted immediate real subdirectory names of the
    directory held open at `wfd`, listed through a FRESH descriptor reopened as "." beneath the held
    one (F-JOURNAL-HELD-FD-LISTING: a listing read through a descriptor held from an earlier open can
    miss entries created since that open, so a store subdirectory created after the open would be
    silently undiscovered), each entry then stat'ed no-follow through the SAME held descriptor, so use
    stays bound to the directory identity the caller retains (no path re-resolution). A symlinked
    entry is skipped (never followed)."""
    try:
        names = sorted(_journal._listdir_fresh(wfd, working_rel))
    except (_journal.JournalError, OSError) as exc:
        _journal._fd_release_fault(exc)    # a close's exception is raised as itself, never a cannot-evaluate
        # A listing I/O error (EIO, or a state change after the open) is fail-closed, never read as
        # an empty (no-store) directory (check-fails-closed-on-unreadable).
        raise StoreError("cannot list {} ({})".format(working_rel, exc))
    subdirs = []
    for entry in names:
        try:
            est = os.stat(entry, dir_fd=wfd, follow_symlinks=False)
        except OSError as exc:
            raise StoreError("cannot stat {}/{} ({})".format(working_rel, entry, exc))
        if stat.S_ISDIR(est.st_mode):        # a real dir only; a symlink or file is not a store
            subdirs.append(entry)
    return subdirs


# --- discovery (spec 4.5) ----------------------------------------------------------------------------

def discover_machine_store(store_root_fd, store_root, accept_tokens=None):
    """Locate the single machine store under `.working/`. Returns (status, machine_dir, detail):
      "one"      exactly one immediate `.working/` subdir carries an opf manifest (machine_dir set)
      "absent"   `.working/` is absent (no store tree here at all: a genuinely un-adopted location)
      "present"  `.working/` EXISTS but no immediate subdir carries a valid opf manifest (a
                 present-but-invalid store, never treated as absent, spec residual 17)
      "multiple" more than one does (ambiguous: cannot choose)
    Raises StoreError (cannot-evaluate) on any read error, a non-directory `.working/`, or a refused
    symlink. The scan is EXHAUSTIVE and strict-unique: `toml` is the expected name (its match, when the
    result is otherwise unique, is reported as the machine_dir), but a second stray store is NOT masked
    (spec 4.5, residual 17).

    `accept_tokens` is the set of discovery tokens accepted here; None means the sole current
    STANDARD_TOKEN (the frozen general contract, spec 4.5). `opf upgrade` passes the legacy
    PRIOR_STANDARD_TOKEN alongside it so a 1.0.0 store carrying the retired `[devprocess]` base can be
    located for migration (spec 9.2). Because the base table name always equals the token the base
    declares, a candidate matches when, for some accepted token, `[<token>].standard == <token>`."""
    accept = (STANDARD_TOKEN,) if accept_tokens is None else tuple(accept_tokens)
    subdirs = _immediate_subdirs(store_root_fd, WORKING_DIRNAME)
    if subdirs is None:
        return "absent", None, "{}/ is absent".format(WORKING_DIRNAME)
    matches, legacy = _classify_working_names(
        subdirs,
        lambda name: _read_toml_contained(          # StoreError propagates (fail-closed)
            store_root_fd, "{}/{}/{}".format(WORKING_DIRNAME, name, MANIFEST_NAME)),
        accept)
    return _discovery_result(matches, legacy)


def discover_machine_store_fd(store_root_fd, accept_tokens=None, allowance=None):
    """Descriptor-bound sibling of discover_machine_store (round-5 defect 1): the `.working` LISTING
    descriptor is held for the whole discovery and every listed name's manifest is read BENEATH it,
    so a `.working` swapped in after the listing is never read (the rounds-2-4 invariant: every name
    read from a directory listing is read through the descriptor that listed it). Returns
    (status, machine_dir, detail, manifest_raw): the discover_machine_store triple plus, when the
    status is "one", the resolved manifest's EXACT bytes as read through the held descriptor (None
    otherwise), so a descriptor-bound caller (resolve_store_fd, the adoption planner) parses the very
    bytes discovery matched rather than re-opening the manifest by path. Statuses, details, StoreError
    behaviour and `accept_tokens` are exactly discover_machine_store's. `allowance` meters each manifest
    read (_read_store_bytes_contained); None reads unmetered."""
    accept = (STANDARD_TOKEN,) if accept_tokens is None else tuple(accept_tokens)
    wfd = _open_working_dir_fd(store_root_fd, WORKING_DIRNAME)
    if wfd is None:
        return "absent", None, "{}/ is absent".format(WORKING_DIRNAME), None
    try:
        names = _list_real_subdirs(wfd, WORKING_DIRNAME)
        raws = {}

        def load(name):
            rel = "{}/{}".format(name, MANIFEST_NAME)
            data = _read_store_bytes_contained(wfd, rel, allowance)   # StoreError propagates (fail-closed)
            if data is None:
                return None
            parsed = _parse_store_toml("{}/{}".format(WORKING_DIRNAME, rel), data)
            raws[name] = data
            return parsed

        matches, legacy = _classify_working_names(names, load, accept)
        status, machine_dir, detail = _discovery_result(matches, legacy)
        return status, machine_dir, detail, (raws.get(machine_dir) if status == "one" else None)
    finally:
        inflight = _journal._in_flight_in(sys._getframe())
        try:
            _close_fd_exc_safe(wfd)   # in THIS frame: the #377 frame test sees what is in flight
        except OSError:
            raise                       # #377: raised only with nothing in flight here
        except BaseException as exc:    # noqa: BLE001  the first interrupt propagates as itself
            _journal._yield_close_exceptions(inflight, [exc])


def _classify_working_names(names, load, accept):
    """The candidate classification both discovery variants share. `load(name)` returns the parsed
    `.working/<name>/manifest.toml` (None when absent) through that variant's own read binding, raising
    StoreError fail-closed on an unreadable or unparseable manifest. Returns (matches, legacy)."""
    matches = []
    legacy = []
    for name in names:
        data = load(name)
        if data is None:
            continue
        matched = False
        for token in accept:
            base = data.get(token)
            if isinstance(base, dict) and base.get("standard") == token:
                matches.append(name)
                matched = True
                break
        # m2: when the CURRENT discovery accepts no legacy token but a candidate carries the RETIRED
        # [devprocess] 1.0.0 base, collect it so the operator-facing detail can name the `opf upgrade`
        # remedy (spec 9.2), mirroring the older-spec finding _validate_base already surfaces. This only
        # ENRICHES the detail; the status stays "present", so resolution stays CANNOT-EVALUATE (fail-closed).
        if not matched and PRIOR_STANDARD_TOKEN not in accept:
            legacy_base = data.get(PRIOR_STANDARD_TOKEN)
            if isinstance(legacy_base, dict) and legacy_base.get("standard") == PRIOR_STANDARD_TOKEN:
                legacy.append(name)
    return matches, legacy


def _discovery_result(matches, legacy):
    """The (status, machine_dir, detail) verdict both discovery variants share (spec 4.5); raises
    StoreError for a reserved sole match."""
    if not matches:
        base_detail = "{}/ is present but no {}/*/{} declares standard = {!r}".format(
            WORKING_DIRNAME, WORKING_DIRNAME, MANIFEST_NAME, STANDARD_TOKEN)
        if legacy:
            locs = ", ".join("{}/{}".format(WORKING_DIRNAME, n) for n in sorted(legacy))
            base_detail += ("; a retired [{}] 1.0.0 store is present at {} (run `opf upgrade` to migrate "
                            "it, spec 9.2)".format(PRIOR_STANDARD_TOKEN, locs))
        return "present", None, base_detail
    if len(matches) > 1:
        return "multiple", None, "{} machine stores declare the opf token: {}".format(
            len(matches), ", ".join(sorted(matches)))
    # Retain the legacy staging reservation as well as the inert homes-2 names.
    # A reserved sole match is never a machine store, including during legacy-token discovery.
    if matches[0] in RESERVED_MACHINE_SUBDIRS:
        raise StoreError("the machine subdirectory name {!r} is reserved for store-level control "
                         "area {}/{} (spec 4.4); it cannot name the machine store".format(
                             matches[0], WORKING_DIRNAME, matches[0]))
    return "one", matches[0], "machine store at {}/{}".format(WORKING_DIRNAME, matches[0])


# --- the pointer (spec 4.3, 5.5) ----------------------------------------------------------------------

def classify_target(value):
    """Classify a pointer/target string into a Target (spec 5.5). Local targets (`dir:` and an absolute
    bare path) resolve to a filesystem store in this unit; remote git/host targets are recognized but
    marked non-local (they need a clone or sync, out of U1 scope, so discovery treats them as
    cannot-evaluate rather than guessing a local path). Returns a Target, or raises StoreError when the
    value is malformed (empty, or a bare RELATIVE path, which spec 4.3 forbids outside `dir:`)."""
    if not isinstance(value, str) or not value.strip():
        raise StoreError("pointer target is empty or not a string")
    v = value.strip()
    if v.startswith("dir:"):
        return Target("dir", v[len("dir:"):], local=True)
    if v.startswith("git:"):
        return Target("git", v[len("git:"):], local=False)
    if v.startswith("github:"):
        return Target("github", v[len("github:"):], local=False)
    if v.startswith("gitlab:"):
        return Target("gitlab", v[len("gitlab:"):], local=False)
    # A bare value: a scheme URL or an scp-like git URL (a colon before any slash) is a remote git
    # target; otherwise it is a filesystem path, which (outside `dir:`) MUST be absolute (spec 4.3).
    if "://" in v:
        return Target("git", v, local=False)
    slash = v.find("/")
    colon = v.find(":")
    if colon != -1 and (slash == -1 or colon < slash):
        return Target("git", v, local=False)          # scp-like host:path
    if not _is_absolute_path(v):
        raise StoreError("bare pointer path {!r} must be absolute (a relative path is allowed only via "
                         "dir:; spec 4.3)".format(v))
    return Target("path", v, local=True)


def _is_absolute_path(p):
    """POSIX-absolute test for a pointer path. The OPF tooling targets the supported POSIX platforms, so
    a leading '/' is the absolute marker."""
    return isinstance(p, str) and p.startswith("/")


def _target_store_root(target, product_root):
    """Resolve a LOCAL Target to a store repository root Path against the product root (spec 4.3: a
    relative `dir:` path is interpreted against the product root; any other path must be absolute).
    Raises StoreError for a non-local target (recognized but not resolvable in this unit)."""
    if not target.local:
        raise StoreError("pointer names a remote {} target ({!r}); resolving it needs a clone/sync "
                         "(out of this unit's scope)".format(target.kind, target.value))
    raw = target.value.strip()
    if not raw:
        raise StoreError("pointer target path is empty")
    path = Path(raw)
    if path.is_absolute():
        return path
    # A relative dir: path roots at the product repository root (the named fixed root for the pointer).
    # Joined WITHOUT Path.resolve(): resolve() would canonicalize and FOLLOW symlinks, defeating the
    # no-follow walk that opens the store root; the walk (_open_dir_nofollow) refuses any symlinked
    # component of the joined path instead (MAJOR 2).
    return Path(product_root) / path


def _read_pointer_target(product_root_fd, relpath, allowance=None):
    """The Target named by a pointer file at the product root, or None when the file is absent. Raises
    StoreError when the file is present but unreadable, unparseable, or carries no usable
    `[store].target` (a pointer that names nothing is malformed, not absent). `allowance` meters the
    read as _read_store_bytes_contained states; None reads unmetered."""
    if allowance is None:
        data = _read_toml_contained(product_root_fd, relpath)
    else:
        raw = _read_store_bytes_contained(product_root_fd, relpath, allowance)
        data = None if raw is None else _parse_store_toml(relpath, raw)
    if data is None:
        return None
    store = data.get("store")
    if not isinstance(store, dict) or "target" not in store:
        raise StoreError("{} has no [store].target".format(relpath))
    return classify_target(store["target"])


# --- resolution (spec 4.3) ---------------------------------------------------------------------------

def resolve_store(product_root, accept_tokens=None):
    """Resolve the machine store from a product repository root (spec 4.3). Never raises for an expected
    outcome; returns a Resolution whose status is RESOLVED, NOT_ADOPTED, or CANNOT_EVALUATE.

    `accept_tokens` selects the discovery tokens accepted during machine-store discovery; None is the
    frozen general contract (the sole current STANDARD_TOKEN, spec 4.5). Only `opf upgrade` widens it,
    passing (STANDARD_TOKEN, PRIOR_STANDARD_TOKEN) so a legacy 1.0.0 `[devprocess]` store resolves for
    migration (spec 9.2); every other caller keeps the default single-token discovery.

    Order (spec 4.3): the local override first, then the committed pointer; where NEITHER pointer file
    exists, the default location (`.working/` at the product root) is tried. A pointer that exists but
    does not resolve (a remote-only or malformed target, an unreachable store root, or zero/multiple
    manifests at the target) is CANNOT-EVALUATE and STOPS: it never falls back silently to the default,
    which could resolve a different store than the one intended. Only the total absence of both a pointer
    and a default store is NOT-ADOPTED (the `opf init` remedy)."""
    product_root = Path(product_root)
    if not product_root.exists():
        return Resolution(CANNOT_EVALUATE, "product root does not exist: {}".format(product_root))
    if not product_root.is_dir():
        return Resolution(CANNOT_EVALUATE, "product root is not a directory: {}".format(product_root))
    # Resolve the product root to an ABSOLUTE path in the security-sensitive components WITHOUT following
    # symlinks: os.path.abspath joins with the cwd and normalizes lexically, it does NOT canonicalize
    # symlinks (unlike Path.resolve()), so a relative --root (e.g. `.`) resolves identically to the same
    # root passed absolutely while the pointer-target no-follow walk still refuses symlinked components.
    # A relative dir: target joined to a relative product root would otherwise be rejected downstream by
    # _open_dir_nofollow as not absolute, so a valid relative --root refused a valid companion store
    # (MAJOR 2).
    try:
        product_root = Path(os.path.abspath(product_root))
    except OSError as exc:
        # os.path.abspath anchors a RELATIVE product root to os.getcwd(); when the process cwd has been
        # deleted or become unreadable, os.getcwd() raises OSError, which would otherwise ESCAPE this
        # resolver uncaught (it contracts to never raise for an expected outcome, only return a Resolution).
        # The abspath CHOICE is deliberate (it anchors a relative --root without following symlinks, MAJOR 2);
        # this only closes its ambient-cwd crash residual by mapping an unreadable cwd to a fail-closed
        # CANNOT-EVALUATE, exactly as every other unreadable input here is (check-fails-closed-on-unreadable).
        return Resolution(CANNOT_EVALUATE,
                          "cannot resolve product root to an absolute path (cwd unreadable?): {}".format(exc))
    if not _containment.probe():
        # A store read touches adopter-controlled paths; without the race-free primitive a read cannot be
        # done safely, so resolution fails closed rather than resolving over an unguarded name (spec 17).
        return Resolution(CANNOT_EVALUATE, "race-free containment primitive absent; fail-closed")
    try:
        product_root_fd = _open_root_fd(product_root)
    except OSError as exc:
        return Resolution(CANNOT_EVALUATE, "cannot open product root {} ({})".format(product_root, exc))
    try:
        try:
            local = _read_pointer_target(product_root_fd, LOCAL_POINTER_REL)
            # The local override wins WHOLESALE (spec 4.3): the committed pointer is consulted only when
            # no valid override exists, so a malformed committed pointer never fails a resolution the
            # override already settled (MAJOR 1).
            committed = _read_pointer_target(product_root_fd, POINTER_REL) if local is None else None
        except StoreError as exc:
            _journal._fd_release_fault(exc)    # a close's exception is raised as itself, never a cannot-evaluate
            return Resolution(CANNOT_EVALUATE, str(exc))
    finally:
        inflight = _journal._in_flight_in(sys._getframe())
        try:
            _close_fd_exc_safe(product_root_fd)   # in THIS frame: the #377 frame test sees what is in flight
        except OSError:
            raise                       # #377: raised only with nothing in flight here
        except BaseException as exc:    # noqa: BLE001  the first interrupt propagates as itself
            _journal._yield_close_exceptions(inflight, [exc])

    if local is not None or committed is not None:
        # A pointer named the store: the override wins wholesale, else it completes with the committed
        # pointer (spec 4.3). From here every failure is CANNOT-EVALUATE, never a default fallback.
        target = local if local is not None else committed
        source = "local-override" if local is not None else "committed"
        try:
            store_root = _target_store_root(target, product_root)
        except StoreError as exc:
            _journal._fd_release_fault(exc)    # a close's exception is raised as itself, never a cannot-evaluate
            return Resolution(CANNOT_EVALUATE, str(exc), target=target, pointer_source=source,
                              product_root=product_root)
        res = _resolve_at(store_root, source, target, pointer=True, accept_tokens=accept_tokens)
        res.product_root = product_root
        return res

    # Neither pointer file exists: try the default in-repo location.
    res = _resolve_at(product_root, "default", None, pointer=False, accept_tokens=accept_tokens)
    res.product_root = product_root
    return res


def _resolve_at(store_root, source, target, pointer, accept_tokens=None):
    """Open the store root and run discovery there. `pointer` selects the fail-closed policy: a pointer
    that resolves to a root with no (or multiple) machine store is CANNOT-EVALUATE, while the default
    fallback reports NOT-ADOPTED for a genuinely empty location (spec 4.3/4.5)."""
    store_root = Path(store_root)
    if not store_root.exists():
        detail = "pointer target {} does not exist".format(store_root) if pointer \
            else ("default store location {} disappeared at the resolution boundary "
                  "(removed, renamed, or made unreadable after the product root was opened)".format(
                      store_root))
        # An absent pointer target is unresolvable (cannot-evaluate). At the DEFAULT location the store
        # root IS the product root, which resolve_store already opened as a readable directory upstream, so
        # its absence HERE is a TOCTOU disappearance (removed/renamed at the resolution boundary): a product
        # root that could no longer be evaluated, NOT a genuinely un-adopted repo. It too is CANNOT-EVALUATE
        # (BLOCKER 1), never a false NOT-ADOPTED that render --check would report as a clean NOT-APPLICABLE
        # (exit 0). A readable non-adopter root that merely lacks `.working/` is still NOT-ADOPTED below
        # (discovery inspects it and returns absent), the successfully-inspected case NOT-ADOPTED is for.
        return Resolution(CANNOT_EVALUATE, detail, target=target, pointer_source=source)
    if not store_root.is_dir():
        return Resolution(CANNOT_EVALUATE, "store root {} is not a directory".format(store_root),
                          target=target, pointer_source=source)
    try:
        # A pointer target is opened by the no-follow walk from the filesystem root (a symlinked store
        # root or ancestor is refused, MAJOR 2); the default location is the trusted product-root anchor.
        store_root_fd = _open_store_root_fd(store_root, pointer)
    except OSError as exc:
        _journal._fd_release_fault(exc)    # the no-follow walk's close: raised as itself, never a cannot-evaluate
        return Resolution(CANNOT_EVALUATE, "cannot open store root {} ({})".format(store_root, exc),
                          target=target, pointer_source=source)
    try:
        try:
            status, machine_dir, detail = discover_machine_store(
                store_root_fd, store_root, accept_tokens=accept_tokens)
        except StoreError as exc:
            # a close's exception (marked as it left the close helper) is raised as itself, every fault
            # recorded on it kept, never this cannot-evaluate posture (_store_posture_or_refuse admits a
            # cannot-evaluate default location once fresh discovery succeeds, so it would vanish there)
            _journal._fd_release_fault(exc)
            return Resolution(CANNOT_EVALUATE, str(exc), store_root=store_root, target=target,
                              pointer_source=source)
    finally:
        inflight = _journal._in_flight_in(sys._getframe())
        try:
            _close_fd_exc_safe(store_root_fd)   # in THIS frame: the #377 frame test sees what is in flight
        except OSError:
            raise                       # #377: raised only with nothing in flight here
        except BaseException as exc:    # noqa: BLE001  the first interrupt propagates as itself
            _journal._yield_close_exceptions(inflight, [exc])

    if status == "one":
        return Resolution(RESOLVED, detail, store_root=store_root, machine_dir=machine_dir,
                          machine_rel="{}/{}".format(WORKING_DIRNAME, machine_dir),
                          pointer_source=source, target=target)
    if status == "multiple":
        return Resolution(CANNOT_EVALUATE, detail, store_root=store_root, pointer_source=source,
                          target=target)
    # No single machine store. A POINTER promised a store, so either shape (absent or present-invalid) is
    # cannot-evaluate. At the DEFAULT location we distinguish (BLOCKER 2, spec residual 17): an absent
    # `.working/` is a genuinely un-adopted repo (NOT-ADOPTED, opf init is the remedy), while a PRESENT
    # `.working/` carrying no valid [opf] manifest is a present-but-invalid store that must never
    # read as absent (CANNOT-EVALUATE), or opf init could overwrite it.
    if pointer:
        return Resolution(CANNOT_EVALUATE,
                          "pointer resolves to {} but {}".format(store_root, detail),
                          store_root=store_root, pointer_source=source, target=target)
    if status == "present":
        return Resolution(CANNOT_EVALUATE, detail, store_root=store_root, pointer_source=source,
                          target=target)
    return Resolution(NOT_ADOPTED, "no store found ({}); opf init is the remedy".format(detail),
                      store_root=store_root, pointer_source=source)


def resolve_store_fd(product_root_fd, product_root, accept_tokens=None, allowance=None):
    """Descriptor-bound resolve_store (round-5 defect 1), for a caller that already HOLDS the product
    root open and keeps that descriptor for every subsequent read (the adoption planner, which opened
    it with the _open_dir_nofollow walk). Nothing is re-resolved by path: the pointer files are read
    through the held descriptor, and discovery holds the `.working` listing descriptor and reads each
    listed manifest beneath it (discover_machine_store_fd), so a product root or `.working` swapped
    after the caller's open is never read. The held descriptor can bind only a store AT the product
    root (the default location, or a pointer whose target resolves to the product root itself); a
    pointer naming any OTHER root cannot be read through this descriptor and is CANNOT-EVALUATE
    fail-closed (the planner refuses such companion/remote pointers before resolving; path-based
    callers keep resolve_store). Returns (Resolution, manifest_raw): manifest_raw is the RESOLVED
    manifest's exact bytes as read through the listing descriptor (None unless RESOLVED), so the
    caller parses the same bytes discovery matched instead of re-opening the manifest by path.
    `product_root` is the path the held descriptor was opened from, used only for pointer-target
    resolution and reporting; `accept_tokens` as in resolve_store. `allowance` meters every pointer and
    manifest read (_read_store_bytes_contained), so a caller that charged resolution to its own budget
    (source discovery) is never read past that charge; a read that does not fit is CANNOT-EVALUATE. None
    (investigation and every other caller) reads unmetered. Statuses and details follow
    resolve_store/_resolve_at."""
    product_root = Path(product_root)
    if not _containment.probe():
        return Resolution(CANNOT_EVALUATE, "race-free containment primitive absent; fail-closed",
                          product_root=product_root), None
    try:
        local = _read_pointer_target(product_root_fd, LOCAL_POINTER_REL, allowance)
        # The local override wins WHOLESALE (spec 4.3), exactly as in resolve_store (MAJOR 1).
        committed = _read_pointer_target(product_root_fd, POINTER_REL, allowance) if local is None else None
    except StoreError as exc:
        _journal._fd_release_fault(exc)    # a close's exception is raised as itself, never a cannot-evaluate
        return Resolution(CANNOT_EVALUATE, str(exc), product_root=product_root), None
    pointer = local is not None or committed is not None
    target = local if local is not None else committed
    source = ("local-override" if local is not None
              else "committed" if committed is not None else "default")
    if pointer:
        try:
            store_root = _target_store_root(target, product_root)
        except StoreError as exc:
            _journal._fd_release_fault(exc)    # a close's exception is raised as itself, never a cannot-evaluate
            return Resolution(CANNOT_EVALUATE, str(exc), target=target, pointer_source=source,
                              product_root=product_root), None
        if store_root != product_root:
            return Resolution(
                CANNOT_EVALUATE,
                "pointer names a store at {}, outside the held product root {}; a descriptor-bound "
                "resolution cannot bind it (fail-closed)".format(store_root, product_root),
                target=target, pointer_source=source, product_root=product_root), None
    try:
        status, machine_dir, detail, manifest_raw = discover_machine_store_fd(
            product_root_fd, accept_tokens=accept_tokens, allowance=allowance)
    except StoreError as exc:
        _journal._fd_release_fault(exc)    # a close's exception is raised as itself, never a cannot-evaluate
        return Resolution(CANNOT_EVALUATE, str(exc), store_root=product_root, target=target,
                          pointer_source=source, product_root=product_root), None
    if status == "one":
        return Resolution(RESOLVED, detail, store_root=product_root, machine_dir=machine_dir,
                          machine_rel="{}/{}".format(WORKING_DIRNAME, machine_dir),
                          pointer_source=source, target=target,
                          product_root=product_root), manifest_raw
    if status == "multiple":
        return Resolution(CANNOT_EVALUATE, detail, store_root=product_root, pointer_source=source,
                          target=target, product_root=product_root), None
    # The same fail-closed split _resolve_at makes: a pointer that promised a store never falls back,
    # a present-but-invalid default store never reads as absent (spec residual 17).
    if pointer:
        return Resolution(CANNOT_EVALUATE,
                          "pointer resolves to {} but {}".format(product_root, detail),
                          store_root=product_root, pointer_source=source, target=target,
                          product_root=product_root), None
    if status == "present":
        return Resolution(CANNOT_EVALUATE, detail, store_root=product_root, pointer_source=source,
                          target=target, product_root=product_root), None
    return Resolution(NOT_ADOPTED, "no store found ({}); opf init is the remedy".format(detail),
                      store_root=product_root, pointer_source=source,
                      product_root=product_root), None


# --- SemVer and base_compat helpers (spec 9.1) -------------------------------------------------------

_COMPARATORS = (
    (">=", operator.ge), ("<=", operator.le), ("==", operator.eq),
    (">", operator.gt), ("<", operator.lt), ("=", operator.eq),
)


def _match_base_compat(range_str, version_tuple):
    """Evaluate a `base_compat` range against a parsed SemVer tuple. The grammar (DEFINED by this unit,
    see the module docstring) is one or more whitespace-separated comparator clauses ANDed together;
    each clause is an operator (>=, <=, >, <, =, ==) followed by a bare SemVer, and a bare version with
    no operator means exact equality. Returns (True, None) on a match, (False, None) on a clean
    non-match (base-incompatible), or (None, message) on a malformed range (a cannot-evaluate)."""
    if not isinstance(range_str, str):
        return None, "base_compat is not a string"
    clauses = range_str.split()
    if not clauses:
        return None, "base_compat is empty"
    # Validate EVERY clause of the declared range grammar FIRST (MINOR 1): a malformed clause is a
    # diagnostic error (cannot-evaluate) even when an earlier clause already fails the match, so
    # ">=2.0.0 garbage" is flagged malformed rather than short-circuiting to a silent (False, None).
    parsed = []
    for clause in clauses:
        op_fn = operator.eq
        rest = clause
        for token, fn in _COMPARATORS:
            if clause.startswith(token):
                op_fn, rest = fn, clause[len(token):]
                break
        want = _parse(rest)
        if want is None:
            return None, "malformed base_compat clause {!r}".format(clause)
        parsed.append((op_fn, want))
    for op_fn, want in parsed:
        if not op_fn(version_tuple, want):
            return False, None
    return True, None


# --- manifest schema (spec 9, 9.1) -------------------------------------------------------------------

def _valid_namespace(value):
    return isinstance(value, str) and len(value) == 2 and all(ch in _NAMESPACE_OK for ch in value)


def _valid_extension_namespace(value):
    """An `x-<vendor>` token: 'x-' plus a lowercase-ASCII-alphanumeric vendor slug (spec 8.7). The vendor
    alphabet is the explicit ASCII set, NOT str.islower()/str.isdigit(), which also admit non-ASCII letters
    and digits, so a Unicode slug that should be INVALID cannot pass a check meant for the ASCII token
    alphabet (guard-input-soundness)."""
    if not isinstance(value, str) or not value.startswith("x-"):
        return False
    vendor = value[2:]
    return bool(vendor) and all(ch in _EXTENSION_VENDOR_OK for ch in vendor) \
        and vendor[0] != "-" and vendor[-1] != "-"


# --- shared boundary type-guards (guard-input-soundness) ---------------------------------------------
# A CONTROL input (a caller-supplied allow-set, roster, namespace set, id collection, or counters map)
# that flows into a set()/frozenset() construction, an `x in <set|frozenset|dict>` membership, or a dict
# key lookup is validated at the boundary by these helpers, uniformly across the three OPF tooling
# modules, so a non-str / non-hashable / wrong-shape control fails CLOSED (a structured finding or a
# controlled refusal) rather than crashing with a TypeError/AttributeError or silently disabling a check
# (a bare string splatting into characters, a substring match, or a silent skip). Well-formed inputs are
# unchanged; only malformed shapes are newly rejected. These are the single, auditable place the pattern
# lives, so no site re-implements an ad-hoc inline guard.

def _require_mapping(value):
    """(value, True) when value is a dict, else ({}, False). A non-mapping control is malformed: the
    caller surfaces a finding and treats the empty mapping fail-closed, never a `.get`/`[]` crash."""
    if isinstance(value, dict):
        return value, True
    return {}, False


def _is_str_token_control(value):
    """True when value is a well-formed set-of-exact-string-tokens control: a concrete set/frozenset/
    list/tuple whose every element is a str. A bare string is NOT one (set() would splat it into
    characters and membership would substring-match); None is NOT one (a caller for which 'absent' is
    legitimate tests `value is None` FIRST). Used to reject a malformed token control before a membership
    test reaches it."""
    return (isinstance(value, (set, frozenset, list, tuple))
            and all(isinstance(x, str) for x in value))


def _str_token_set(value):
    """Normalize a set-of-exact-string-tokens control to a frozenset of its string members, FAIL-CLOSED:
    a well-formed control (see _is_str_token_control) yields exactly those tokens; any malformed shape (a
    bare string, a non-string element, a mapping, a scalar, None) yields the EMPTY frozenset, so
    exact-token membership admits NOTHING rather than splatting, substring-matching, or crashing."""
    return frozenset(value) if _is_str_token_control(value) else frozenset()


def _sorted_key_names(keys):
    """Order an iterable of keys for a FINDING MESSAGE as a sorted list of strings. Every element is
    coerced through _safe_str BEFORE the sort, so a heterogeneous or non-str key set (e.g. a hand-
    constructed table with mixed int/str keys, or an oversized-int key whose str() would trip CPython's
    integer-string-conversion limit) can neither crash sorted() on an int-vs-str type mismatch nor break the
    later ", ".join, which is always over strings. This is the shared total-sort for the unknown-key
    finding-message idiom (formerly a bare sorted over the surplus-key set) across the three OPF pass-A
    validators (schema, store, release). It
    is for ERROR-MESSAGE ORDERING ONLY; it is deliberately NOT used on the byte-stable coverage digest,
    where a non-str key fails closed with a ReleaseError instead (str-coercing a digest key would change
    the digest, so the digest path rejects rather than coerces)."""
    return sorted(_safe_str(k) for k in keys)


_DISPLAY_MAX_DEPTH = 32      # container levels a finding-message render descends before a depth marker
_DISPLAY_MAX_NODES = 512     # values (containers and scalars) a render copies before it stops
_DISPLAY_FULL_MAX_CHARS = 65536   # widest whole, uncut repr() a value within the two bounds above renders as
_DISPLAY_MAX_CHARS = 1024    # characters of bounded-render content before a cut (on a whole escape) and a marker
_DISPLAY_MARKER_MAX = 37     # longest marker ending a bounded rendering: "...<str of N chars>", N at sys.maxsize
_DISPLAY_NAME_MAX = 64       # characters of a type or timezone name a marker or a rendered datetime may carry
_DISPLAY_CONTAINERS = (dict, list, tuple, set, frozenset)       # exact types; a subclass is unsupported
_DISPLAY_TIMES = (datetime.datetime, datetime.date, datetime.time)   # exact types, as tomllib builds them
_DISPLAY_ATOM = re.compile(r"\\(?:x[0-9a-f]{2}|u[0-9a-f]{4}|U[0-9a-f]{8}|.)|.", re.DOTALL)
_DISPLAY_ATOM_MAX = 10       # the longest _DISPLAY_ATOM: a backslash, U and eight hex digits
_DISPLAY_STOP = ("stop",)    # the _display_snapshot entry where the copy reached _DISPLAY_MAX_NODES values
_DISPLAY_FULL_INT_BITS = 4 * _DISPLAY_FULL_MAX_CHARS   # an int of more bits has more digits than that width
_DISPLAY_INT_CEILING = 10 ** _DISPLAY_MAX_CHARS          # the least positive int whose repr() is too wide
_DISPLAY_INT_FLOOR = -(10 ** (_DISPLAY_MAX_CHARS - 1))   # the greatest negative int whose repr() is too wide
_DISPLAY_INT_BUDGET_BITS = 32 * _DISPLAY_FULL_INT_BITS   # int bits one render may convert, its conversions summed
_DISPLAY_SPENT = "...<int conversion budget spent>"   # ends a bounded rendering stopped at an int past that budget
_TYPE_NAME = type.__dict__["__name__"]   # the getter of type itself, so a metaclass __name__ never runs


def _display_kind(kind, kinds):
    """True when `kind` IS one of `kinds`. An identity test, never `in`: tuple membership falls back to ==, and a
    metaclass can define __eq__, so `in` could run caller code."""
    for k in kinds:
        if kind is k:
            return True
    return False


def _display_time_ok(value):
    """True when an exact date, datetime or time renders through the built-in repr() and str() alone: a date
    always; a datetime or time when its tzinfo is None, or an exact datetime.timezone whose offset is an exact
    timedelta and whose name is an exact str of at most _DISPLAY_NAME_MAX characters (tomllib builds only
    these). Any other tzinfo would have its own methods called, so such a value is unsupported."""
    if type(value) is datetime.date:
        return True
    tz = value.tzinfo
    if tz is None:
        return True
    if type(tz) is not datetime.timezone:
        return False
    name = tz.tzname(None)
    return (type(tz.utcoffset(None)) is datetime.timedelta and type(name) is str
            and len(name) <= _DISPLAY_NAME_MAX)


def _display_scalar_ok(value):
    """True when `value` is a supported non-container value, one whose built-in repr() runs no method a caller
    can define: an exact str, bytes, int, float or bool, None, or an exact date, datetime or time that
    _display_time_ok admits. Each is immutable and its type cannot be reassigned, so the answer, once given,
    holds for as long as the value is referenced."""
    kind = type(value)
    return (kind is str or kind is bytes or kind is int or kind is float or kind is bool or value is None
            or (_display_kind(kind, _DISPLAY_TIMES) and _display_time_ok(value)))


def _display_spend(budget, value):
    """True when the bit length of the exact int `value` fits in the bits the render may still convert
    (budget[0], which starts at _DISPLAY_INT_BUDGET_BITS), and then charges it; False, charging nothing, when it
    does not. Every conversion of an int the value holds is charged here first, so one render converts at
    most _DISPLAY_INT_BUDGET_BITS bits of such ints in all (a count the renderer formats itself, such as a
    bit length in a marker, is not charged). `budget` is the render's own list, [bits left, cleared],
    cleared being True once _display_full has tested every int of a complete copy and none trips."""
    bits = value.bit_length()
    if bits > budget[0]:
        return False
    budget[0] -= bits
    return True


def _display_int(value, budget):
    """The bounded rendering of an exact int: its repr() when that is at most _DISPLAY_MAX_CHARS characters,
    decided exactly before any conversion: it is exactly when _DISPLAY_INT_FLOOR < value < _DISPLAY_INT_CEILING
    (a negative int spends one character on its sign), and CPython compares two ints of different lengths by
    their lengths alone, so the test does not grow with the size of the int. A wider int, or one whose repr()
    trips the integer-string-conversion limit (which can be set as low as 640 digits), renders as the
    type-and-size marker <oversized-int: N bits>, built from the bit length without converting the int. Only
    the int that is the whole value, or one that does not trip the limit, reaches here with that marker:
    _display_render renders a container whose rendering reaches an int that trips the limit as
    <oversized-value> as a whole. None when the conversion budget cannot cover the conversion
    (_display_spend): the caller stops there with _DISPLAY_SPENT."""
    if _DISPLAY_INT_FLOOR < value < _DISPLAY_INT_CEILING:
        if not _display_spend(budget, value):
            return None
        try:
            return repr(value)
        except ValueError:
            pass
    return "<oversized-int: {} bits>".format(value.bit_length())


def _display_int_trips(value, budget):
    """True when repr() of the exact int `value` raises ValueError under the integer-string-conversion limit
    in force (sys.get_int_max_str_digits(), 0 for none): the oversized int of the whole-value rule, which
    renders alone as <oversized-int: N bits> and makes a container holding it <oversized-value>. An int of at
    most three bits per limit digit has at most that many digits and never trips. One of more than
    _DISPLAY_FULL_INT_BITS bits has more digits than any limit of at most _DISPLAY_FULL_MAX_CHARS digits, the
    default 4,300 among them, and CPython refuses it before converting, so it trips under such a limit; under
    a higher limit it is taken as not tripping without converting it (the residual _safe_display item 6
    names). Any other int is asked through repr() itself, so the answer is CPython's own: no count of digits
    can stand in for asking, since CPython 3.14.4 converts some ints of more digits than the limit
    (10 ** 10000, of 10,001 digits, under a limit of 10,000). That conversion is at most
    _DISPLAY_FULL_INT_BITS bits and is charged to the render's budget first (_display_spend); None, the int
    untested, when the budget cannot cover it."""
    limit = sys.get_int_max_str_digits()
    bits = value.bit_length()
    if not limit or bits <= 3 * limit:
        return False
    if bits > _DISPLAY_FULL_INT_BITS:
        return limit <= _DISPLAY_FULL_MAX_CHARS
    if not _display_spend(budget, value):
        return None
    try:
        repr(value)
    except ValueError:
        return True
    return False


def _display_take(out, room, text):
    """Append to `out` the longest prefix of `text` that fits in `room` characters and ends on a whole atom (a
    backslash escape in the x, u or U hex form, a backslash and the one character it escapes, or any other
    single character), so a cut never splits an escape repr() or _escape_line_unsafe wrote. Only the first
    room + _DISPLAY_ATOM_MAX characters are scanned. Returns (characters appended, whether all of `text` fit)."""
    if len(text) <= room:
        out.append(text)
        return len(text), True
    taken = 0
    for atom in _DISPLAY_ATOM.findall(text, 0, room + _DISPLAY_ATOM_MAX):
        if taken + len(atom) > room:
            break
        out.append(atom)
        taken += len(atom)
    return taken, False


def _display_unsupported(value):
    """The fixed marker for a value whose exact type is not on the allow-list: <unrenderable NAME>, NAME being
    its type name read through the __name__ getter of type itself (_TYPE_NAME, so a metaclass __name__ never
    runs), escaped and cut to _DISPLAY_NAME_MAX characters (a trailing ... marks a cut). A type name can be a
    str subclass of any length, so it is read only through the str slots themselves (str.__len__, and
    str.__getitem__ for an exact-str copy of its first _DISPLAY_NAME_MAX characters): no method of that
    subclass runs and the copy is bounded whatever the name length. Nothing else is read: the __repr__, __str__
    and __format__ of the value are never called."""
    name = _TYPE_NAME.__get__(type(value))
    head = str.__getitem__(name, slice(0, _DISPLAY_NAME_MAX))
    text, count = _escape_bounded(head, _DISPLAY_NAME_MAX)
    return "<unrenderable {}{}>".format(text, "" if count == str.__len__(name) else "...")


def _display_leaf(value, budget):
    """The bounded rendering of one non-container value, its work bounded by _DISPLAY_MAX_CHARS whatever the
    value size, because every supported scalar is sized BEFORE it is converted: a str is cut on the value to its
    first _DISPLAY_MAX_CHARS characters before repr() and, when cut, reports its length (...<str of N chars>);
    a bytes longer than _DISPLAY_MAX_CHARS renders as <oversized-bytes: N bytes> and an int whose repr() would
    be wider than _DISPLAY_MAX_CHARS characters (an exact test, _display_int) as <oversized-int: N bits>,
    neither building its repr() (an int inside a container that trips the integer-string-conversion limit
    never reaches here: _display_render renders the container as <oversized-value>); None, a bool, a float
    and a supported date or time render through their built-in repr(); any other value is unsupported
    (_display_unsupported). The text can exceed _DISPLAY_MAX_CHARS (a cut str repr() with its escapes); the
    caller bounds it with _display_take. None for an int the conversion budget cannot cover (_display_int)."""
    kind = type(value)
    if kind is str:
        if len(value) > _DISPLAY_MAX_CHARS:
            return "{}...<str of {} chars>".format(repr(value[:_DISPLAY_MAX_CHARS]), len(value))
        return repr(value)
    if kind is bytes:
        if len(value) > _DISPLAY_MAX_CHARS:
            return "<oversized-bytes: {} bytes>".format(len(value))
        return repr(value)
    if kind is int:
        return _display_int(value, budget)
    if _display_scalar_ok(value):
        return repr(value)
    return _display_unsupported(value)


def _display_brackets(kind, size):
    """The (open, close) text repr() puts around a built-in container of the exact type `kind` holding `size`
    members, so _display_render spells every container it renders exactly as repr() does."""
    if kind is dict:
        return "{", "}"
    if kind is list:
        return "[", "]"
    if kind is tuple:
        return "(", ",)" if size == 1 else ")"
    if not size:
        return kind.__name__ + "(", ")"
    return ("{", "}") if kind is set else (kind.__name__ + "({", "})")


def _display_snapshot(value):
    """One bounded private copy of `value`, taken before anything is rendered: _display_render reads only this
    copy, so a member another thread replaces after it was read is never rendered. Each member is read once
    for each occurrence the walk reaches: a container the value holds more than once (aliased, repeated, or
    on a cycle) is read again at each occurrence, each read checked as it is made, so its occurrences can
    differ when another thread changes it in between. An entry is ("leaf", v) for a supported scalar
    (_display_scalar_ok, so v is immutable and stays supported); ("text", marker) for an unsupported value (its
    _display_unsupported marker, built at once); ("deep", marker) for a container _DISPLAY_MAX_DEPTH levels
    deep (a depth marker, which _display_full reads as an incomplete copy); ("box", kind, members) for a
    container of an exact _DISPLAY_CONTAINERS type, members being a fresh list of the entries of its members
    (a dict as key, value, key, value); and _DISPLAY_STOP where the copy reached _DISPLAY_MAX_NODES values.
    The walk is depth first in rendering order, iterative, and steps through each container with its built-in
    iterator, so it copies at most _DISPLAY_MAX_NODES values and holds at most 2 * _DISPLAY_MAX_DEPTH + 1
    iterators whatever the size, depth or width of the input. A frame ends when its iterator raises
    StopIteration, never on a member compared with a marker, so any value supplied as data, an object of this
    module included, is copied as a member like any other. It reads only exact built-in types (never len() of
    a live container), so no method a caller can define runs."""
    nodes = 0
    top = []
    frames = [(iter((value,)), top, 0, False)]   # (member iterator, entries copied, member depth, dict pairs)
    while frames:
        members, copied, depth, pairs = frames[-1]
        try:
            item = next(members)
        except StopIteration:
            frames.pop()
            continue
        if pairs:
            frames.append((iter(item), copied, depth, False))   # a (key, value) pair from dict.items()
        else:
            nodes += 1
            if nodes > _DISPLAY_MAX_NODES:
                copied.append(_DISPLAY_STOP)
                break
            kind = type(item)
            if not _display_kind(kind, _DISPLAY_CONTAINERS):
                copied.append(("leaf", item) if _display_scalar_ok(item)
                              else ("text", _display_unsupported(item)))
            elif depth >= _DISPLAY_MAX_DEPTH:
                copied.append(("deep", "<{} nested deeper than {} levels>".format(kind.__name__,
                                                                                  _DISPLAY_MAX_DEPTH)))
            else:
                box = []
                copied.append(("box", kind, box))
                frames.append((iter(item.items() if kind is dict else item), box, depth + 1, kind is dict))
    return top[0]


def _display_render(snapshot, escape, budget):
    """The bounded rendering of a _display_snapshot, spelled as repr() spells it: a leaf through _display_leaf,
    a marker as itself, a box with the brackets and separators repr() writes. Rendering stops at
    _DISPLAY_MAX_CHARS characters, cut on a whole escape (_display_take) and followed by ...<truncated>;
    _DISPLAY_STOP stops it the same way. A container whose rendering reaches an int whose repr() trips the
    integer-string-conversion limit (_display_int_trips) before the cut renders as <oversized-value> as a
    whole, as _safe_display renders a complete copy holding one; an int past the cut is never read. An int
    the render's conversion budget cannot cover, to test or to render (_display_spend), stops the rendering
    where it stands, followed by _DISPLAY_SPENT; once _display_full has tested every int of a complete copy
    and none trips (budget[1]), no int is tested again. With `escape`, each piece is escaped
    (_escape_line_unsafe) BEFORE the cut, so escaping can never take the result over the bound; only the
    first room + _DISPLAY_ATOM_MAX characters of a piece are escaped, since no more of it can fit. It keeps
    its own work stack and never recurses; it reads only the snapshot."""
    out = []
    used = 0
    marker = ""
    stack = [snapshot]
    while stack:
        entry = stack.pop()
        if entry is _DISPLAY_STOP:
            marker = "...<truncated>"
            break
        if entry[0] == "leaf":
            value = entry[1]
            if entry is not snapshot and type(value) is int and not budget[1]:
                trips = _display_int_trips(value, budget)
                if trips:
                    return "<oversized-value>"
                if trips is None:
                    marker = _DISPLAY_SPENT
                    break
            piece = _display_leaf(value, budget)
            if piece is None:
                marker = _DISPLAY_SPENT
                break
        elif entry[0] != "box":
            piece = entry[1]   # a "text" or a "deep" marker
        else:
            kind, members = entry[1], entry[2]
            piece, close = _display_brackets(kind, len(members))
            stack.append(("text", close))
            for i in range(len(members) - 1, -1, -1):
                stack.append(members[i])
                if i:
                    stack.append(("text", ": " if kind is dict and i % 2 else ", "))
        room = _DISPLAY_MAX_CHARS - used
        if escape:
            piece = _escape_line_unsafe(piece[:room + _DISPLAY_ATOM_MAX])
        taken, whole = _display_take(out, room, piece)
        used += taken
        if not whole:
            marker = "...<truncated>"
            break
    return "".join(out) + marker


def _display_full_leaf(value, room, budget):
    """The whole repr() of one supported scalar when that is at most `room` characters, else None, each sized
    BEFORE it is converted so the work stays bounded by _DISPLAY_FULL_MAX_CHARS: a str whose length, with
    the two quotes, already exceeds `room` and a bytes whose length, with b and the quotes, does, are never
    converted, nor is an int of more than _DISPLAY_FULL_INT_BITS bits (more digits than any room) or one the
    render's conversion budget cannot cover (_display_spend). None, a bool, a float and a supported date or
    time have a short built-in repr()."""
    kind = type(value)
    if kind is str and len(value) + 2 > room:
        return None
    if kind is bytes and len(value) + 3 > room:
        return None
    if kind is int and (value.bit_length() > _DISPLAY_FULL_INT_BITS or not _display_spend(budget, value)):
        return None
    text = repr(value)
    return text if len(text) <= room else None


def _display_full(snapshot, escape, budget):
    """The whole-value rendering of a COMPLETE _display_snapshot (no depth marker, no _DISPLAY_STOP: every
    container at most _DISPLAY_MAX_DEPTH deep and at most _DISPLAY_MAX_NODES values in all), read from that
    copy alone, or None when the copy is not complete, when the render's conversion budget cannot cover a
    conversion the rendering needs (_display_spend), or when the rendering would be wider than
    _DISPLAY_FULL_MAX_CHARS characters, where the caller renders the bounded form (_display_render). The copy
    is found complete before any int is tested, so an incomplete copy renders in the bounded form whatever
    ints it holds. Within those limits it is what repr() gives: every int is tested first, so when any int
    the copy holds trips the integer-string-conversion limit (_display_int_trips) the result is
    <oversized-int: N bits> for the int that is the whole value and <oversized-value> for a container,
    whatever its width, as repr() raising ValueError gives; otherwise it records in budget[1] that no int
    trips, and the result is the repr() of the copy, uncut, each leaf through _display_full_leaf and each
    container with the brackets and separators repr() writes. With `escape`, each piece is escaped
    (_escape_line_unsafe) and the width counts the escaped text. It keeps its own work stack and never
    recurses."""
    todo = [snapshot]
    ints = []
    while todo:
        entry = todo.pop()
        if entry is _DISPLAY_STOP or entry[0] == "deep":
            return None
        if entry[0] == "box":
            todo.extend(entry[2])
        elif entry[0] == "leaf" and type(entry[1]) is int:
            ints.append(entry)
    for entry in ints:
        trips = _display_int_trips(entry[1], budget)
        if trips is None:
            return None
        if trips:
            if entry is snapshot:
                return "<oversized-int: {} bits>".format(entry[1].bit_length())
            return "<oversized-value>"
    budget[1] = True
    out = []
    used = 0
    stack = [snapshot]
    while stack:
        entry = stack.pop()
        if entry[0] == "leaf":
            piece = _display_full_leaf(entry[1], _DISPLAY_FULL_MAX_CHARS - used, budget)
            if piece is None:
                return None
        elif entry[0] == "text":
            piece = entry[1]
        else:
            kind, members = entry[1], entry[2]
            piece, close = _display_brackets(kind, len(members))
            stack.append(("text", close))
            for i in range(len(members) - 1, -1, -1):
                stack.append(members[i])
                if i:
                    stack.append(("text", ": " if kind is dict and i % 2 else ", "))
        if escape:
            piece = _escape_line_unsafe(piece)
        used += len(piece)
        if used > _DISPLAY_FULL_MAX_CHARS:
            return None
        out.append(piece)
    return "".join(out)


def _safe_display(value):
    """Render a value for a FINDING MESSAGE (or a controlled-refusal message), BOUNDED in work and output and
    NEVER RAISING (the one residual is item 6 below). Three hazards reach a message from a parsed manifest,
    counters, version, worklog or record file. First, tomllib parses a hexadecimal, octal, or binary integer
    literal (0x.../0o.../0b...) into an arbitrarily-large Python int with NO digit limit (the
    integer-string-conversion limit applies to base-10 only, and sys.set_int_max_str_digits(0) lifts it), so
    repr() of such an int either raises ValueError or runs in time and memory proportional to its size.
    Second, tomllib builds a dotted table header or dotted key iteratively, so a header such as
    [id.a.a ... .a] with 100,000 parts parses into a 100,000-level nested table whose repr() overflows the
    stack (a RecursionError, "Stack overflow ... while getting the repr of an object", on CPython 3.14;
    CPython 3.14.8 and later refuse a key of more than 1,000 dotted parts, so there one header nests at
    most 1,000 levels, still past any depth bound here). Third, a wide array or a long string or bytes
    value makes repr() run to megabytes. The contract:
      1. Supported types are an explicit allow-list of EXACT types: None, bool, int, float, str, bytes,
         datetime.date, datetime.datetime and datetime.time (with a tzinfo _display_time_ok admits), and
         dict, list, tuple, set and frozenset. Any other value, a subclass of a supported type included,
         renders as the fixed marker <unrenderable NAME> naming only its type (_display_unsupported): its
         __repr__, __str__, __format__, __eq__ and __hash__ are never called, and no metaclass method runs.
      2. Every supported scalar is sized BEFORE it is converted: against _DISPLAY_FULL_MAX_CHARS in the
         whole rendering (_display_full_leaf), and against _DISPLAY_MAX_CHARS in the bounded one
         (_display_leaf, where a bytes or an int over that bound renders a type-and-size marker without
         building its repr(), and a str is cut on the value).
      3. No container is passed to repr() or str(). The renderer takes ONE bounded copy first
         (_display_snapshot: exact types only, at most _DISPLAY_MAX_NODES values and _DISPLAY_MAX_DEPTH
         container levels) and renders only that copy (_display_full, else _display_render), spelling each
         value as repr() does. A member another thread replaces after it was read is never rendered, and no
         method a caller can define runs on the value. Each member is read once for each occurrence the copy
         reaches, so a container the value holds more than once (aliased, repeated, or on a cycle) is read at
         each occurrence, each read checked as it is made, and its occurrences can differ when another thread
         changes it in between.
      4. A value whose members are all supported and whose containers nest at most _DISPLAY_MAX_DEPTH deep
         and hold at most _DISPLAY_MAX_NODES values in all renders by the whole-value rule (_display_full):
         when an int it holds trips the integer-string-conversion limit (_display_int_trips), as
         <oversized-int: N bits> if it is that int and as <oversized-value> if it is a container, whatever
         its width (unless the conversion budget of item 5 runs out first, item 6); otherwise, when its
         repr() is at most _DISPLAY_FULL_MAX_CHARS characters, as repr(value), byte for byte and uncut. That
         is the whole byte-identity guarantee, and the conversion budget never stops such a value (item 5).
         Past any of those limits (depth, values, width), or when the budget cannot cover a conversion the
         whole-value rule needs, it renders in the bounded form (_display_render): content cut on a whole
         escape at _DISPLAY_MAX_CHARS characters and followed by ...<truncated>, a container whose rendering
         reaches an int that trips the limit before the cut rendered as <oversized-value> as a whole, an int
         wider than _DISPLAY_MAX_CHARS characters that does not trip it shown in place as its
         <oversized-int: N bits> size marker, and an int the budget cannot cover stopping the rendering
         there, followed by ...<int conversion budget spent>. So a value past a limit differs from repr() even when
         short (a scalar nested 33 levels deep shows a depth marker; a cyclic list shows its expansion up to
         the bounds). A str renders as repr() when that is at most _DISPLAY_FULL_MAX_CHARS characters, and
         otherwise as the longest whole-escape prefix of at most _DISPLAY_MAX_CHARS characters of the repr()
         of its first _DISPLAY_MAX_CHARS characters, followed by ...<str of N chars>.
      5. The result is at most _DISPLAY_FULL_MAX_CHARS characters: a whole rendering is at most that, and a
         bounded one at most _DISPLAY_MAX_CHARS + _DISPLAY_MARKER_MAX characters (content of at most
         _DISPLAY_MAX_CHARS characters, cut on a whole escape, and the one marker that ends it: ...<truncated>,
         14 characters, ...<int conversion budget spent>, 32, or ...<str of N chars>, at most 37, N being a str
         length and so at most sys.maxsize). The work is bounded by the _DISPLAY_ constants whatever the size,
         depth or width of the input, with one exception (below). An int is converted only when it has at
         most _DISPLAY_FULL_INT_BITS bits, and each conversion is charged its bit length to the render's
         budget before it is made (_display_spend), so the conversions of one render total at most
         _DISPLAY_INT_BUDGET_BITS bits (8,388,608). A value the byte-identity guarantee covers converts fewer
         than 440,000 bits: each int it converts, at most twice, has its digits in a repr() of at most
         _DISPLAY_FULL_MAX_CHARS characters. The exception: a set or dict
         whose table deletions left sparse is scanned by its built-in iterator up to the size the table
         reached, so stepping through its members takes time in proportion to that size, not to the members
         left. A value tomllib builds holds no set and no table it deleted from, so a parsed value cannot
         reach this.
      6. Residual: a MemoryError, a RuntimeError (another thread resizing a dict or set while it is copied) or
         a ValueError (another thread changing the integer-string-conversion limit during a render among
         them) yields the fixed placeholder <unrenderable-value>; an asynchronous exception (KeyboardInterrupt,
         or one a signal handler raises) is not caught and propagates. A container another thread changes
         while it is copied, in a way its iterator does not refuse (a list, a replaced dict value, a set
         changed at the same size), renders the members the copy read, each checked as it was read. A
         finalizer the garbage collector runs during a render is outside the value and is not prevented. With
         the integer-string-conversion limit raised above _DISPLAY_FULL_MAX_CHARS digits, an int of more than
         _DISPLAY_FULL_INT_BITS bits is never converted, so whether its repr() trips that limit is not
         decided: it is taken as not tripping, and, being wider than _DISPLAY_FULL_MAX_CHARS characters, it
         renders in the bounded form, as its size marker in place, where repr() could raise ValueError. The
         conversion budget changes no output with no limit or with a limit of at most the default 4,300
         digits: with no limit no int is converted to test it, and under such a limit an int that does not
         trip has at most 4,300 digits (14,285 bits), so a render converts at most 7,547,494 bits (the worst
         case tests 510 such ints before one of _DISPLAY_FULL_INT_BITS bits that trips), within
         _DISPLAY_INT_BUDGET_BITS. With the limit raised, a value whose ints need more than the budget
         to test or to render renders in the bounded form and stops at the first int the budget cannot cover,
         with ...<int conversion budget spent>, where without the budget it renders as <oversized-value> when
         an int it holds trips the limit and otherwise in the bounded form with each int in place.
    So the message is always produced and the caller still records its finding or refusal (fail-closed;
    guard-input-soundness). It is the shared message-rendering guard for the finding-message idiom across
    the three OPF pass-A validators (store, schema, release), the doctor and the record verb. Like
    _sorted_key_names it is for MESSAGE rendering ONLY; it is deliberately NOT used on the byte-stable
    coverage digest, where an oversized int or a too-deep value fails closed with a ReleaseError instead (a
    marker in the digest would change the bytes; spec 6.1)."""
    try:
        budget = [_DISPLAY_INT_BUDGET_BITS, False]
        if type(value) is str:
            text = _display_full_leaf(value, _DISPLAY_FULL_MAX_CHARS, budget)
            if text is not None:
                return text
            out = []
            _display_take(out, _DISPLAY_MAX_CHARS, repr(value[:_DISPLAY_MAX_CHARS]))
            return "{}...<str of {} chars>".format("".join(out), len(value))
        snapshot = _display_snapshot(value)
        text = _display_full(snapshot, False, budget)
        return _display_render(snapshot, False, budget) if text is None else text
    except (MemoryError, RuntimeError, ValueError):
        return "<unrenderable-value>"


def _escape_char(ch):
    """One character as _escape_line_unsafe writes it: itself when printable, else its backslash escape."""
    if ch.isprintable():
        return ch
    o = ord(ch)
    return ("\\x{:02x}".format(o) if o <= 0xff
            else "\\u{:04x}".format(o) if o <= 0xffff
            else "\\U{:08x}".format(o))


def _escape_line_unsafe(s):
    """Escape every NON-PRINTABLE character in `s` (a C0/C1 control such as newline, CR or tab, a
    zero-width or bidirectional format char, a line/paragraph separator, a surrogate or unassigned code
    point) to a backslash escape, leaving every printable character (letters, digits, punctuation, and the
    ordinary space) byte-for-byte. `str.isprintable()` is the authority for what may sit on a single
    diagnostic line. This keeps an untrusted value from forging an extra finding line ('x\\nFORGED') or
    hiding/steering text with format characters (codex round-6; guard-input-soundness / no-concealed-failure
    applied to a diagnostic renderer). Its output is unbounded (up to ten characters per input character);
    a finding message bounds it through _escape_bounded or _display_take."""
    return "".join(_escape_char(ch) for ch in s)


def _escape_bounded(s, room):
    """(text, count): _escape_line_unsafe of the longest prefix of the exact str `s` whose escaped form fits in
    `room` characters, cut only between whole escapes, and the number of characters of `s` it covers (equal
    to len(s) when nothing was cut). At most `room` characters of `s` are read, since each escapes to at
    least one character."""
    out = []
    used = 0
    count = 0
    for ch in s[:room]:
        piece = _escape_char(ch)
        used += len(piece)
        if used > room:
            break
        out.append(piece)
        count += 1
    return "".join(out), count


def _safe_str(value):
    """The str()-style companion of _safe_display, for a finding-message position that renders a value with
    {} (no surrounding repr quotes) rather than {!r}: a version string interpolated into a `release #N
    (<version>)` label, for instance. It shares the allow-list, the sizing before conversion, the bounded copy,
    the limits, the conversion budget and the residual of _safe_display, and its result is likewise at most
    _DISPLAY_FULL_MAX_CHARS characters. A str renders with every NON-PRINTABLE character escaped to a
    backslash form (_escape_line_unsafe), so a value carrying a control character cannot inject a second
    diagnostic line; a str whose ESCAPED form is at most _DISPLAY_FULL_MAX_CHARS characters renders as that
    escaped form, uncut, byte for byte, and a longer one is cut on a whole escape at _DISPLAY_MAX_CHARS
    characters (_escape_bounded) and followed by ...<str of N chars>. A supported date or time renders as its
    built-in str(). Every other value renders from its _display_snapshot as _safe_display renders it, by the
    whole-value rule within the limits (_display_full) and in the bounded form past them (_display_render),
    with each piece escaped BEFORE the width is measured or the cut is made, so escaping never takes the
    result over either bound: a built-in container renders as _safe_display does (its str() IS its repr(),
    and repr() escapes every character str.isprintable() rejects, so the escape changes nothing), an exact
    int, float, bool, None or bytes as its repr() (which equals its str()), an int that trips the
    integer-string-conversion limit as the oversized-int marker alone and as <oversized-value> for a
    container holding it, and an unsupported type as <unrenderable NAME>, and no __str__ or __repr__ a caller
    can define is called.
    Unlike _safe_display, which renders through repr() and so already
    escapes controls, this str()-based renderer must escape them itself (the store/release sibling the
    schema newline-hardening left exposed; codex round-6). It is for MESSAGE rendering only, never the
    byte-stable coverage digest."""
    try:
        kind = type(value)
        if kind is str:
            if len(value) <= _DISPLAY_FULL_MAX_CHARS:
                text, count = _escape_bounded(value, _DISPLAY_FULL_MAX_CHARS)
                if count == len(value):
                    return text
            text, count = _escape_bounded(value, _DISPLAY_MAX_CHARS)
            return "{}...<str of {} chars>".format(text, len(value))
        if _display_kind(kind, _DISPLAY_TIMES) and _display_time_ok(value):
            return _escape_line_unsafe(str(value))
        snapshot = _display_snapshot(value)
        budget = [_DISPLAY_INT_BUDGET_BITS, False]
        text = _display_full(snapshot, True, budget)
        return _display_render(snapshot, True, budget) if text is None else text
    except (MemoryError, RuntimeError, ValueError):
        return "<unrenderable-value>"


def _is_item_collection(value):
    """True when value is a non-string, non-mapping iterable safe to iterate and materialize (a list,
    tuple, set, frozenset, range, or a dict keys/values view). A bare string/bytes is excluded (it must
    not iterate into characters), and a mapping and a scalar are excluded. Guards the CONTAINER of an
    id/record collection so a non-iterable never crashes a `for` and a string never splats; the ELEMENTS
    remain the caller's to validate."""
    return (isinstance(value, collections.abc.Iterable)
            and not isinstance(value, (str, bytes, bytearray))
            and not isinstance(value, collections.abc.Mapping))


_MAX_SUPPORTED_MAJORS = 4096   # a profile supports a handful of majors; a larger control is malformed


def _materialize_majors(value):
    """Materialize a supported-profiles majors control to a concrete list of NON-NEGATIVE ints, FAIL-
    CLOSED. A well-formed control (a finite item collection whose every element is a non-negative int)
    yields that list; anything malformed yields None, so the caller fails closed to CANNOT-EVALUATE rather
    than crashing, hanging, or exhausting memory: a non-collection, a bare string/mapping/scalar, an
    element that is not an int, a boolean (an int subclass, excluded), a NEGATIVE major (no SemVer major is
    negative), an iterator that raises mid-iteration, or a control longer than _MAX_SUPPORTED_MAJORS (a
    range(10**12) or an unbounded generator is cut off at the cap, never fully materialized). This is the
    single boundary that normalizes the enforcement control's majors (guard-input-soundness, SECA resource-
    bounds); a one-shot iterator is consumed here exactly once and the returned list is reused."""
    if not _is_item_collection(value):
        return None
    out = []
    try:
        for m in value:
            if not isinstance(m, int) or isinstance(m, bool) or m < 0:
                return None
            out.append(m)
            if len(out) > _MAX_SUPPORTED_MAJORS:
                return None
    except Exception:
        return None
    return out


def validate_manifest(data, supported_profiles=None, *, manifest_rel=None):
    """Validate a parsed manifest against the base schema and, for each SUPPORTED profile, the profile
    schema. Returns a ManifestValidation.

    manifest_rel is the store-relative path the manifest was read from, where the caller knows it; the
    refusal of an older spec_version names it (spec 9.2), and None names "the store manifest" instead.

    supported_profiles is a mapping {profile_name: iterable of supported MAJOR ints}; empty or None
    means BASE-ONLY (every declared profile is recorded as present-but-unevaluated, never enforced,
    fail-SAFE). A profile the mapping does not name, or whose declared major is not in the supported
    set, is likewise recorded unevaluated. A profile the mapping DOES cover is enforced and FAILS
    CLOSED (a finding) on an unreadable, base-incompatible, weakening, or mis-registered instance
    (spec 9.1).

    Outcome: CANNOT-EVALUATE when the manifest is not a table or its `[opf]` base is absent or
    does not declare the opf token (it is not identifiably an opf store); INVALID when it
    is an opf store that violates the schema; VALID otherwise."""
    # supported_profiles is the caller's profile-enforcement control: a mapping {profile_name(str) ->
    # iterable of supported MAJOR ints}. None means BASE-ONLY (fail-safe). Any OTHER malformed shape (a
    # string, a bare list, a non-mapping, a non-string profile name, or a majors value that is not a
    # collection of genuine ints) is treated as UNPARSEABLE and fails CLOSED to CANNOT-EVALUATE, never
    # normalized to {} and read as a permissive base-only VALID: a malformed control must not silently
    # DISABLE profile enforcement (spec 9.1, guard-input-soundness). set(...) at the enforcement loop
    # below is then always over a validated list of ints (never a splatted string or an unhashable list).
    if supported_profiles is None:
        supported_profiles = {}
    supported_profiles, sp_ok = _require_mapping(supported_profiles)
    if not sp_ok:
        return ManifestValidation(CANNOT_EVALUATE,
                                  ["supported_profiles must be a mapping of profile name to a list of "
                                   "supported major versions; an unparseable profile-enforcement control "
                                   "fails closed and never silently disables enforcement (spec 9.1)"])
    # MATERIALIZE each majors value to a concrete list ONCE at this boundary. A value may be a one-shot
    # iterator (a generator or iter([...])): it is a valid item collection but is EXHAUSTED by the
    # element-validation below, and the enforcement loop further down consumes the value a SECOND time
    # (set(...) at ~line 697). An un-materialized iterator therefore reads as EMPTY at enforcement, silently
    # dropping a supported profile to "unevaluated" and letting a weakening profile validate VALID: a
    # fail-open (spec 9.1, guard-input-soundness). Capturing it here and using the SAME list for both
    # validation and enforcement consumes each value exactly once, so enforcement still fires.
    materialized_profiles = {}
    for _pname, _majors in supported_profiles.items():
        _mats = _materialize_majors(_majors)
        if not isinstance(_pname, str) or _mats is None:
            return ManifestValidation(CANNOT_EVALUATE,
                                      ["supported_profiles entry {} is malformed: each profile name (a "
                                       "string) maps to a bounded list of non-negative integer major "
                                       "versions (fail-closed; spec 9.1)".format(_safe_display(_pname))])
        materialized_profiles[_pname] = _mats
    supported_profiles = materialized_profiles
    if not isinstance(data, dict):
        return ManifestValidation(CANNOT_EVALUATE, ["manifest is not a table"])
    base = data.get("opf")
    if not isinstance(base, dict) or base.get("standard") != STANDARD_TOKEN:
        return ManifestValidation(CANNOT_EVALUATE,
                                  ["[opf].standard is absent or is not {!r} (not identifiably a "
                                   "opf store)".format(STANDARD_TOKEN)])

    # Refuse activation before any production validator can interpret generation-2
    # entries using the generation-1 schema/release rules. No schema activation here.
    if "worklog" in base:
        import _opf_worklog
        try:
            _opf_worklog.generation(data)
        except _opf_worklog.WorklogError as exc:
            return ManifestValidation(CANNOT_EVALUATE, [str(exc)])

    findings = []
    _validate_top_level(data, findings)
    spec_tuple = _validate_base(base, findings, manifest_rel)
    _validate_store_section(data.get("store"), findings)
    modules_enabled = _validate_modules(data.get("modules"), findings)
    registered_vendors = _validate_vendors(data.get("vendors"), findings)
    _validate_types(data.get("types"), modules_enabled, findings)
    _validate_providers(data.get("providers"), findings)
    _validate_views(data.get("views"), findings)
    _validate_deliverables(data.get("deliverables"), findings)
    _validate_archive(data.get("archive"), findings)
    _validate_unmanaged(data.get("unmanaged"), findings)

    profiles = data.get("profiles")
    parsed_profiles = {}
    unevaluated = []
    if profiles is not None and not isinstance(profiles, dict):
        findings.append("[profiles] is not a table")
        profiles = {}
    base_posture = base.get("posture")
    for name, prof in (profiles or {}).items():
        parsed_profiles[name] = prof
        supported_majors = set(supported_profiles.get(name, ()))
        if not supported_majors:
            unevaluated.append(name)      # a profile this tool does not cover: fail-safe, never a fail
            continue
        prof_major = _profile_major(prof)
        if prof_major is None:
            # The NAME is supported but its major cannot be determined (a non-dict profile, or a version
            # that is absent, non-string, or not a bare SemVer): a covered profile we cannot grade is a
            # FAIL-CLOSED finding (INVALID), never routed to unevaluated, which would skip all its gates
            # and let the manifest validate VALID (BLOCKER 1, spec 9.1).
            findings.append("[profiles.{}] is a supported profile but its major cannot be determined "
                            "(version absent, non-string, or not a bare SemVer); fail-closed".format(
                                _safe_display(name)))
            continue
        if prof_major not in supported_majors:
            # A genuinely UNSUPPORTED profile major is ignored for enforcement (spec 9.1), recorded
            # unevaluated rather than failing the base over it.
            unevaluated.append(name)
            continue
        _validate_supported_profile(name, prof, spec_tuple, base_posture, modules_enabled,
                                    registered_vendors, findings)

    status = INVALID if findings else VALID
    return ManifestValidation(status, findings, unevaluated, base, parsed_profiles)


def _validate_top_level(data, findings):
    extra = set(data) - TOP_LEVEL_TABLES
    if extra:
        findings.append("unknown top-level table(s): {}".format(", ".join(_sorted_key_names(extra))))


def _check_enum(table, key, allowed, where, findings):
    """Type-THEN-membership check for a closed-vocabulary scalar field. A wrong TYPE is a fail-closed
    finding (MAJOR 3: never a crash on an unhashable value, e.g. a list, reaching a later set/dict/rank
    test), and a well-typed but out-of-vocabulary value is the ordinary membership finding. Returns the
    value only when it is a clean, valid member, else None, so callers can rely on any returned value
    being a scalar string safe for a downstream rank/membership test."""
    if key not in table:
        return None
    value = table.get(key)
    if not isinstance(value, str):
        findings.append("{}.{} must be a string, not {}".format(where, key, type(value).__name__))
        return None
    if value not in allowed:
        findings.append("{}.{} {!r} is not one of {}".format(where, key, value, list(allowed)))
        return None
    return value


def _validate_base(base, findings, manifest_rel=None):
    """Validate the [opf] base table; returns the parsed spec_version tuple or None. manifest_rel is
    the path validate_manifest was given, named by the older-spec_version refusal."""
    extra = set(base) - OPF_KEYS - OPF_OPTIONAL_KEYS
    if extra:
        findings.append("[opf] unknown key(s): {}".format(", ".join(_sorted_key_names(extra))))
    missing = [k for k in OPF_KEYS if k not in base]
    if missing:
        findings.append("[opf] missing required key(s): {}".format(", ".join(sorted(missing))))
    spec_tuple = None
    sv = base.get("spec_version")
    if "spec_version" in base:
        spec_tuple = _parse(sv) if isinstance(sv, str) else None
        if spec_tuple is None:
            findings.append("[opf].spec_version {} is not a bare SemVer".format(_safe_display(sv)))
        elif spec_tuple < _parse(SUPPORTED_SPEC_VERSION) or (
                spec_tuple > _parse(SUPPORTED_SPEC_VERSION)
                and not (sv == HOMES2_SPEC_VERSION and base.get("homes") == 2)):
            # A store declaring an OLDER base spec_version than this tooling implements is fail-closed with
            # its OWN named, remedy-carrying finding, rather than cascading into the confusing C-ROSTER /
            # missing-type findings a newer roster would otherwise raise against the older store (spec 9.x).
            # A store declaring a NEWER base spec_version is fail-closed at validation the same way: older
            # tooling MUST refuse a 1.3.0 (or any above-supported) declaration rather than certify it
            # under legacy checks (spec 9.2). The one exception is the exact reserved homes-2 declaration
            # pair, spec_version 2.0.0 with homes = 2 (spec 4.2): homes_generation() gates its activation
            # on SUPPORTED_HOMES. Both mismatch findings share this ONE emission site:
            # the census in _opf_manifest_regressions pins the validator's reviewed emission-site set, and
            # a message selected above a shared censused append keeps each wording reviewable without
            # widening that set.
            # The older refusal names the manifest path and the declared spec_version, states that no
            # store upgrade reaches spec_version 2.0.0 (spec 9.2 defines none, and the 1.0.0 and 1.1.0
            # route to 1.2.0 is not one), and states the hand step spec 9.2 gives a tree under a refused older
            # manifest: the operator moves that manifest aside by hand (no OPF command writes it), after
            # which its files are ordinary foreign content for a first adoption.
            if spec_tuple < _parse(SUPPORTED_SPEC_VERSION):
                where = "the store manifest" if manifest_rel is None else _safe_display(manifest_rel)
                mismatch = ("{} declares [opf].spec_version {}, older than the {} this tooling "
                            "implements; no store upgrade reaches spec_version 2.0.0, so the store is "
                            "refused; to adopt this tree instead, first move {} aside by hand: "
                            "its files are then ordinary foreign content for a first adoption "
                            "(spec 9.2; fail-closed)".format(
                                where, _safe_display(sv), SUPPORTED_SPEC_VERSION, where))
            else:
                mismatch = ("[opf].spec_version {} is above the {} this tooling implements; "
                            "upgrade the tooling before operating on this store "
                            "(spec 9.2; fail-closed)".format(_safe_display(sv), SUPPORTED_SPEC_VERSION))
            findings.append(mismatch)
    # Each closed-vocabulary field is type-checked BEFORE its membership test (MAJOR 3), so a wrong-typed
    # value (e.g. posture as a list) is a fail-closed finding here rather than an unhashable-value crash
    # in a later rank/membership test.
    _check_enum(base, "layout", LAYOUTS, "[opf]", findings)
    _check_enum(base, "posture", POSTURES, "[opf]", findings)
    _check_enum(base, "import_status", IMPORT_STATES, "[opf]", findings)
    # `homes` is an OPTIONAL integer generation declaration (spec 4.2 / 9.2). When present it must be
    # the integer 1 (legacy) or 2 (homes-2); a non-integer, a bool (an int subclass), or an out-of-range
    # value is a fail-closed finding. Absence means generation 1 and is not a finding: the key becomes
    # REQUIRED only when homes 2 activates (L4). This validates the DECLARATION; homes_generation()
    # decides ACTIVATION and does not activate while SUPPORTED_HOMES == 1.
    if "homes" in base:
        homes = base["homes"]
        if type(homes) is not int or homes not in (1, 2):
            findings.append("[opf].homes {} is not an integer generation (1 or 2)".format(
                _safe_display(homes)))
    return spec_tuple


def _validate_store_section(store, findings):
    if store is None:
        return
    if not isinstance(store, dict):
        findings.append("[store] is not a table")
        return
    extra = set(store) - STORE_KEYS
    if extra:
        findings.append("[store] unknown key(s): {}".format(", ".join(_sorted_key_names(extra))))
    if "sync_target" in store and not isinstance(store.get("sync_target"), str):
        findings.append("[store].sync_target must be a string (empty under the in-repo default)")


def _validate_modules(modules, findings):
    """Validate [modules] and return the set of ENABLED module names (default: all off)."""
    enabled = set()
    if modules is None:
        return enabled
    if not isinstance(modules, dict):
        findings.append("[modules] is not a table")
        return enabled
    extra = set(modules) - set(KNOWN_MODULES)
    if extra:
        findings.append("[modules] unknown module(s): {} (known: {})".format(
            ", ".join(_sorted_key_names(extra)), ", ".join(KNOWN_MODULES)))
    for name, value in modules.items():
        if name not in KNOWN_MODULES:
            continue
        if not isinstance(value, bool):
            findings.append("[modules].{} must be a boolean".format(name))
        elif value:
            enabled.add(name)
    return enabled


def _validate_vendors(vendors, findings):
    """Validate [vendors] and return the set of registered x-<vendor> namespaces."""
    registered = set()
    if vendors is None:
        return registered
    if not isinstance(vendors, dict):
        findings.append("[vendors] is not a table")
        return registered
    extra = set(vendors) - VENDORS_KEYS
    if extra:
        findings.append("[vendors] unknown key(s): {}".format(", ".join(_sorted_key_names(extra))))
    reg = vendors.get("registered")
    if reg is None:
        return registered
    if not isinstance(reg, list) or not all(isinstance(x, str) for x in reg):
        findings.append("[vendors].registered must be a list of strings")
        return registered
    for ns in reg:
        if not _valid_extension_namespace(ns):
            findings.append("[vendors].registered entry {!r} is not a valid x-<vendor> namespace".format(ns))
        else:
            registered.add(ns)
    return registered


def _normative_namespace(name, modules_enabled, where, findings):
    """The normative section-8.1 namespace for a declared type name. Returns the namespace when the name
    is a valid declarable type using it, else appends a finding and returns None: an UNKNOWN name, the
    reserved-EXCLUDED `transaction`, or a MODULE-tier type whose module is not enabled in [modules]. This
    is the real section-8 taxonomy enforcement (not merely a two-letter shape + local-uniqueness check)."""
    if name in BASELINE_TYPES:
        return BASELINE_TYPES[name]
    if name in IMPORTER_TYPES:
        return IMPORTER_TYPES[name]
    if name in MODULE_TYPES:
        normative_ns, module = MODULE_TYPES[name]
        if module not in modules_enabled:
            findings.append("{} declares module type {} but its module {!r} is not enabled in "
                            "[modules] (spec 8.1)".format(where, _safe_display(name), module))
            return None
        return normative_ns
    if name in RESERVED_EXCLUDED_TYPES:
        findings.append("{} type {} is reserved and excluded from the adopter standard "
                        "(spec 8.1)".format(where, _safe_display(name)))
        return None
    findings.append("{} is not a known record type (spec 8.1)".format(where))
    return None


def _is_contained_relpath(p):
    """True when `p` is a ROOT-RELATIVE path that does not escape the root, checked PURELY LEXICALLY (the
    filesystem is never touched): view/deliverable targets and unmanaged paths are root-relative and
    contained (spec 5.8/14.2). Rejects an absolute path (a leading '/'), a Windows drive or UNC/backslash
    path, and any '..' traversal that resolves at or above the root."""
    if not isinstance(p, str) or not p:
        return False
    if p.startswith("/"):
        return False
    if "\\" in p or (len(p) >= 2 and p[1] == ":"):
        return False
    depth = 0
    for comp in p.split("/"):
        if comp in ("", "."):
            continue
        if comp == "..":
            depth -= 1
            if depth < 0:
                return False
        else:
            depth += 1
    return depth > 0        # a path that resolves AT the root (e.g. "." or "a/..") is not contained-below


def _validate_types(types, modules_enabled, findings):
    if types is not None and not isinstance(types, dict):
        findings.append("[types] is not a table")
        return
    # Spec 9 (line 782): the manifest registers one [types.<name>] table per enabled type. Baseline types
    # are always enabled, so an ABSENT or EMPTY [types] table registers none of the store's enabled types
    # and fails closed to INVALID. (Per-enabled-type completeness across the whole enabled set is a
    # store-level doctor cross-check, pass B, not this per-manifest schema pass.)
    enabled_types = set(BASELINE_TYPES) | {t for t, (_ns, module) in MODULE_TYPES.items()
                                           if module in modules_enabled}
    if not types:
        if enabled_types:
            findings.append("[types] must register at least one enabled type ([types.<name>] per enabled "
                            "type, spec 9); none are registered")
        return
    seen_ns = {}
    for name, tbl in types.items():
        where = "[types.{}]".format(_safe_display(name))
        if not isinstance(tbl, dict):
            findings.append("{} is not a table".format(where))
            continue
        extra = set(tbl) - TYPE_KEYS
        if extra:
            findings.append("{} unknown key(s): {}".format(where, ", ".join(_sorted_key_names(extra))))
        ns = tbl.get("namespace")
        if not _valid_namespace(ns):
            findings.append("{}.namespace {} is not a two-letter uppercase namespace".format(
                where, _safe_display(ns)))
            continue
        # Section 8.1 taxonomy: the type name must be a known baseline, importer, or enabled-module type,
        # and it MUST carry that type's normative namespace (a known type with the wrong namespace is a
        # finding, as is an unknown or reserved-excluded name).
        normative_ns = _normative_namespace(name, modules_enabled, where, findings)
        if normative_ns is not None and ns != normative_ns:
            findings.append("{}.namespace {!r} is not the normative namespace {!r} bound to type {} "
                            "(spec 8.1)".format(where, ns, normative_ns, _safe_display(name)))
        if ns in seen_ns:
            findings.append("namespace {!r} is bound to more than one type ({} and {}); the binding is "
                            "one-to-one (spec 8.2)".format(ns, _safe_str(seen_ns[ns]), _safe_str(name)))
        else:
            seen_ns[ns] = name


def _validate_providers(providers, findings):
    if providers is None:
        return
    if not isinstance(providers, dict):
        findings.append("[providers] is not a table")
        return
    for name, tbl in providers.items():
        where = "[providers.{}]".format(_safe_display(name))
        if not isinstance(tbl, dict):
            findings.append("{} is not a table".format(where))
            continue
        extra = set(tbl) - PROVIDER_KEYS
        if extra:
            findings.append("{} unknown key(s): {}".format(where, ", ".join(_sorted_key_names(extra))))
        if not isinstance(tbl.get("handler"), str) or not tbl.get("handler"):
            findings.append("{}.handler must be a non-empty string".format(where))
        roles = tbl.get("roles")
        if not isinstance(roles, list) or not roles or not all(r in PROVIDER_ROLES for r in roles):
            findings.append("{}.roles must be a non-empty list drawn from {}".format(
                where, list(PROVIDER_ROLES)))


def _validate_views(views, findings):
    if views is None:
        return
    if not isinstance(views, dict):
        findings.append("[views] is not a table")
        return
    for name, tbl in views.items():
        where = "[views.{}]".format(_safe_display(name))
        if not isinstance(tbl, dict):
            findings.append("{} is not a table".format(where))
            continue
        extra = set(tbl) - VIEW_KEYS
        if extra:
            findings.append("{} unknown key(s): {}".format(where, ", ".join(_sorted_key_names(extra))))
        if tbl.get("kind") not in VIEW_KINDS:
            findings.append("{}.kind {} is not one of {}".format(
                where, _safe_display(tbl.get("kind")), list(VIEW_KINDS)))
        sources = tbl.get("sources")
        if not isinstance(sources, list) or not sources or not all(isinstance(s, str) and s for s in sources):
            findings.append("{}.sources must be a non-empty list of strings".format(where))
        tgt = tbl.get("target")
        if not isinstance(tgt, str) or not tgt:
            findings.append("{}.target must be a non-empty string".format(where))
        elif not _is_contained_relpath(tgt):
            findings.append("{}.target {!r} must be a root-relative path that does not escape the root "
                            "(no absolute path, no '..' escape; spec 5.8/14.2)".format(where, tgt))


def _validate_deliverables(deliverables, findings):
    if deliverables is None:
        return
    if not isinstance(deliverables, dict):
        findings.append("[deliverables] is not a table")
        return
    for name, tbl in deliverables.items():
        where = "[deliverables.{}]".format(_safe_display(name))
        if not isinstance(tbl, dict):
            findings.append("{} is not a table".format(where))
            continue
        extra = set(tbl) - DELIVERABLE_KEYS
        if extra:
            findings.append("{} unknown key(s): {}".format(where, ", ".join(_sorted_key_names(extra))))
        if tbl.get("kind") not in DELIVERABLE_KINDS:
            findings.append("{}.kind {} is not one of {}".format(
                where, _safe_display(tbl.get("kind")), list(DELIVERABLE_KINDS)))
        tgt = tbl.get("target")
        if not isinstance(tgt, str) or not tgt:
            findings.append("{}.target must be a non-empty string".format(where))
        elif not _is_contained_relpath(tgt):
            findings.append("{}.target {!r} must be a root-relative path that does not escape the root "
                            "(no absolute path, no '..' escape; spec 5.8/14.2)".format(where, tgt))


def _validate_archive(archive, findings):
    if archive is None:
        return
    if not isinstance(archive, dict):
        findings.append("[archive] is not a table")
        return
    extra = set(archive) - ARCHIVE_KEYS
    if extra:
        findings.append("[archive] unknown key(s): {}".format(", ".join(_sorted_key_names(extra))))
    if "period" in archive and archive.get("period") not in ARCHIVE_PERIODS:
        findings.append("[archive].period {} is not one of {}".format(
            _safe_display(archive.get("period")), list(ARCHIVE_PERIODS)))


def _validate_unmanaged(unmanaged, findings):
    if unmanaged is None:
        return
    if not isinstance(unmanaged, dict):
        findings.append("[unmanaged] is not a table")
        return
    extra = set(unmanaged) - UNMANAGED_KEYS
    if extra:
        findings.append("[unmanaged] unknown key(s): {}".format(", ".join(_sorted_key_names(extra))))
    paths = unmanaged.get("paths")
    if paths is not None and (not isinstance(paths, list) or not all(isinstance(p, str) for p in paths)):
        findings.append("[unmanaged].paths must be a list of strings")
    elif isinstance(paths, list):
        # An enumerated unmanaged path is root-relative and contained (spec 14.2 unmanaged-path
        # containment); an absolute path or a '..' escape is rejected, fail-closed.
        for p in paths:
            if isinstance(p, str) and not _is_contained_relpath(p):
                findings.append("[unmanaged].paths entry {!r} must be a root-relative path that does not "
                                "escape the root (no absolute path, no '..' escape; spec 14.2)".format(p))


def _profile_major(prof):
    """The MAJOR int of a profile's own `version`, or None when it is absent or malformed."""
    if not isinstance(prof, dict):
        return None
    parsed = _parse(prof.get("version")) if isinstance(prof.get("version"), str) else None
    return None if parsed is None else parsed[0]


def _validate_supported_profile(name, prof, spec_tuple, base_posture, modules_enabled,
                                registered_vendors, findings):
    """Enforce one SUPPORTED profile (spec 9.1): a profile may only ADD requirements, so this fails
    closed on a base-incompatible instance, a posture_floor that WEAKENS the base, a required module
    that is not enabled, and an extension_namespace that is absent, malformed, or not registered in
    [vendors]."""
    where = "[profiles.{}]".format(_safe_display(name))
    if not isinstance(prof, dict):
        findings.append("{} is not a table".format(where))
        return
    extra = set(prof) - PROFILE_KEYS - PROFILE_OPTIONAL_EXTRA
    if extra:
        findings.append("{} unknown key(s): {}".format(where, ", ".join(_sorted_key_names(extra))))

    # base_compat vs the base spec_version: an out-of-range instance is base-incompatible, fail-closed.
    compat = prof.get("base_compat")
    if compat is None:
        findings.append("{} is missing base_compat".format(where))
    elif spec_tuple is None:
        findings.append("{} declares base_compat but the base spec_version is unparseable, so "
                        "compatibility cannot be confirmed (fail-closed)".format(where))
    else:
        ok, err = _match_base_compat(compat, spec_tuple)
        if err is not None:
            findings.append("{}.base_compat {}: {}".format(where, _safe_display(compat), err))
        elif not ok:
            findings.append("{}.base_compat {!r} does not admit the base spec_version".format(where, compat))

    # posture_floor may raise but never lower the effective posture (spec 11): a weaker floor is a
    # profile validation failure, surfaced, never applied.
    floor = prof.get("posture_floor")
    if floor is not None:
        if floor not in POSTURES:
            findings.append("{}.posture_floor {} is not one of {}".format(
                where, _safe_display(floor), list(POSTURES)))
        elif isinstance(base_posture, str) and base_posture in POSTURE_RANK \
                and POSTURE_RANK[floor] < POSTURE_RANK[base_posture]:
            findings.append("{}.posture_floor {!r} weakens the base posture {!r}; a profile may only add "
                            "requirements (spec 9.1/11)".format(where, floor, base_posture))

    # required_modules must all be enabled in [modules] (a profile adds a requirement, spec 9.1).
    req = prof.get("required_modules")
    if req is not None:
        if not isinstance(req, list) or not all(isinstance(m, str) for m in req):
            findings.append("{}.required_modules must be a list of strings".format(where))
        else:
            missing_mods = [m for m in req if m not in modules_enabled]
            if missing_mods:
                findings.append("{}.required_modules not enabled in [modules]: {}".format(
                    where, ", ".join(missing_mods)))

    # The profile's record-level extension_namespace must be registered in [vendors], or base-only
    # record validation would reject the profile's records (spec 8.7/9.1): a profile-setup failure.
    ns = prof.get("extension_namespace")
    if ns is not None:
        if not _valid_extension_namespace(ns):
            findings.append("{}.extension_namespace {} is not a valid x-<vendor> namespace".format(
                where, _safe_display(ns)))
        elif ns not in registered_vendors:
            findings.append("{}.extension_namespace {!r} is not registered in [vendors].registered "
                            "(base-only record validation would reject the profile's records)".format(where, ns))


# --- convenience: resolve then load + validate -------------------------------------------------------

def load_manifest(resolution, supported_profiles=None):
    """Read and validate the manifest of a RESOLVED store. Returns a ManifestValidation. A resolution
    that is not RESOLVED is a programming error here (callers check resolution.status first); an
    unreadable/unparseable manifest maps to CANNOT-EVALUATE."""
    if resolution.status != RESOLVED:
        return ManifestValidation(CANNOT_EVALUATE,
                                  ["store is not resolved ({})".format(resolution.status)])
    manifest_rel = "{}/{}".format(resolution.machine_rel, MANIFEST_NAME)
    try:
        # Reopen the store root the SAME no-follow way it was resolved: a pointer target ("committed" /
        # "local-override") through the walk that refuses symlinks, the default through the anchor open.
        store_root_fd = _open_store_root_fd(resolution.store_root,
                                            resolution.pointer_source != "default")
    except OSError as exc:
        return ManifestValidation(CANNOT_EVALUATE,
                                  ["cannot open store root {} ({})".format(resolution.store_root, exc)])
    try:
        data = _read_toml_contained(store_root_fd, manifest_rel)
    except StoreError as exc:
        _journal._fd_release_fault(exc)    # a close's exception is raised as itself, never a cannot-evaluate
        return ManifestValidation(CANNOT_EVALUATE, [str(exc)])
    finally:
        inflight = _journal._in_flight_in(sys._getframe())
        try:
            _close_fd_exc_safe(store_root_fd)   # in THIS frame: the #377 frame test sees what is in flight
        except OSError:
            raise                       # #377: raised only with nothing in flight here
        except BaseException as exc:    # noqa: BLE001  the first interrupt propagates as itself
            _journal._yield_close_exceptions(inflight, [exc])
    if data is None:
        # Discovery already read this file, so its disappearance now is a race/fail-closed error.
        return ManifestValidation(CANNOT_EVALUATE, ["{} vanished after discovery".format(manifest_rel)])
    return validate_manifest(data, supported_profiles, manifest_rel=manifest_rel)


# --- self-test ---------------------------------------------------------------------------------------

def self_test():
    """Store-resolution + discovery + manifest-schema invariants over synthetic trees. Judged on the
    returned status/finding values, never by grepping output. The tempdir is removed in a finally."""
    import tempfile
    import shutil

    try:
        _journal.require_containment()
    except _journal.JournalError as exc:
        print("OPF-STORE SELF-TEST ERROR: {}; fail-closed".format(exc), file=sys.stderr)
        return 2

    failures = []
    checked = 0

    def check(name, cond):
        nonlocal checked
        checked += 1
        if not cond:
            failures.append(name)

    def _guard(thunk, default="RAISED"):
        """Run thunk() and return its result, or the sentinel `default` if it raised. MINOR-3: a self-test
        vector whose fix, when reverted, throws an UNCAUGHT exception wraps its probe here, so a reverted fix
        yields a NAMED counted check FAILURE (the sentinel fails the assertion) rather than aborting the whole
        suite with a traceback. Detection is not weakened: the assertion still passes only on the fixed result
        (self-test-discrimination; the fail-closed reporting check-fails-closed-on-unreadable asks of the
        suite itself)."""
        try:
            return thunk()
        except Exception:
            return default

    # A minimal valid base + a valid aiqt profile, as a manifest text builder.
    def manifest_text(standard=STANDARD_TOKEN, spec_version=SUPPORTED_SPEC_VERSION, posture="required",
                      with_aiqt=False, aiqt_floor="required", aiqt_compat=">=1.0.0 <2.0.0",
                      aiqt_version="1.0.0", register_xaiqt=True, ops_enabled=True, with_acme=False,
                      extra_top=""):
        lines = [
            "[opf]",
            'standard = "{}"'.format(standard),
            'spec_version = "{}"'.format(spec_version),
            'layout = "inline"',
            'posture = "{}"'.format(posture),
            'import_status = "none"',
            "",
            "[store]",
            'sync_target = ""',
            "",
            "[modules]",
            "governance = true",
            "operational_policy = {}".format("true" if ops_enabled else "false"),
            "concurrent_operation = true",
            "",
            "[types.backlog_item]",
            'namespace = "BI"',
            "",
            "[vendors]",
            'registered = [{}]'.format('"x-aiqt"' if register_xaiqt else ""),
        ]
        if with_aiqt:
            lines += [
                "",
                "[profiles.aiqt]",
                'version = "{}"'.format(aiqt_version),
                'base_compat = "{}"'.format(aiqt_compat),
                'posture_floor = "{}"'.format(aiqt_floor),
                'required_modules = ["governance", "operational_policy", "concurrent_operation"]',
                'extension_namespace = "x-aiqt"',
                'verification_floor = "triple-family"',
            ]
        if with_acme:
            lines += ["", "[profiles.acme]", 'version = "0.1.0"', 'base_compat = ">=1.0.0 <2.0.0"']
        if extra_top:
            lines += ["", extra_top]
        return "\n".join(lines) + "\n"

    # .resolve() the fixture base so the synthetic stores have NO symlinked ancestor: the pointer-target
    # no-follow walk (MAJOR 2) refuses symlinked ancestors, and a symlinked TMPDIR ancestor would
    # otherwise perturb the absolute-dir: cases (test hermeticity). The deliberate symlink case builds its
    # own link under this real base.
    base = Path(tempfile.mkdtemp(prefix="opf-store-selftest-")).resolve()
    counter = [0]

    def build_store(manifest=None, machine_subdirs=None, pointer=None, local_pointer=None,
                    make_working=True):
        """Create a product/store root. manifest (text) goes under .working/toml/manifest.toml unless
        machine_subdirs ({subdir: text}) is given. pointer/local_pointer write the pointer files."""
        counter[0] += 1
        root = base / "case-{:02d}".format(counter[0])
        root.mkdir()
        if make_working:
            subs = machine_subdirs if machine_subdirs is not None else {DEFAULT_MACHINE_SUBDIR: manifest}
            for sub, text in subs.items():
                sd = root / WORKING_DIRNAME / sub
                sd.mkdir(parents=True)
                if text is not None:
                    (sd / MANIFEST_NAME).write_text(text, encoding="utf-8")
        if pointer is not None:
            (root / POINTER_REL).write_text(pointer, encoding="utf-8")
        if local_pointer is not None:
            (root / LOCAL_POINTER_REL).write_text(local_pointer, encoding="utf-8")
        return root

    try:
        # 1: a valid default in-repo store resolves and its base validates.
        root = build_store(manifest=manifest_text())
        res = resolve_store(root)
        check("valid-default-resolved", res.status == RESOLVED)
        check("valid-default-machine-dir", res.machine_dir == DEFAULT_MACHINE_SUBDIR)
        check("valid-default-source", res.pointer_source == "default")
        mv = load_manifest(res)
        check("valid-default-manifest-valid", mv.status == VALID and not mv.findings)

        # 2a: a valid store behind a committed dir: pointer (a separate store dir, pointed at absolutely).
        store_dir = base / "companion-store-{:02d}".format(counter[0])
        (store_dir / WORKING_DIRNAME / DEFAULT_MACHINE_SUBDIR).mkdir(parents=True)
        (store_dir / WORKING_DIRNAME / DEFAULT_MACHINE_SUBDIR / MANIFEST_NAME).write_text(
            manifest_text(), encoding="utf-8")
        pr2 = build_store(make_working=False,
                          pointer='[store]\ntarget = "dir:{}"\n'.format(store_dir))
        res = resolve_store(pr2)
        check("companion-pointer-resolved", res.status == RESOLVED and res.store_root == store_dir)
        check("companion-pointer-source", res.pointer_source == "committed")

        # 2b: the local override wins over the committed pointer.
        good_store = base / "override-store"
        (good_store / WORKING_DIRNAME / DEFAULT_MACHINE_SUBDIR).mkdir(parents=True)
        (good_store / WORKING_DIRNAME / DEFAULT_MACHINE_SUBDIR / MANIFEST_NAME).write_text(
            manifest_text(), encoding="utf-8")
        pr3 = build_store(make_working=False,
                          pointer='[store]\ntarget = "dir:/nonexistent-committed"\n',
                          local_pointer='[store]\ntarget = "dir:{}"\n'.format(good_store))
        res = resolve_store(pr3)
        check("override-wins", res.status == RESOLVED and res.pointer_source == "local-override")

        # 2c: a VALID local override still RESOLVES even when the lower-precedence committed pointer is
        # MALFORMED (MAJOR 1): the committed pointer is consulted only when no valid override exists, so
        # its malformation never fails a resolution the override already settled. The committed pointer
        # here has a [store] table with NO target (malformed), which would raise on its own.
        ovr_store = base / "override-only-store"
        (ovr_store / WORKING_DIRNAME / DEFAULT_MACHINE_SUBDIR).mkdir(parents=True)
        (ovr_store / WORKING_DIRNAME / DEFAULT_MACHINE_SUBDIR / MANIFEST_NAME).write_text(
            manifest_text(), encoding="utf-8")
        pr = build_store(make_working=False,
                         pointer='[store]\nnot_target = "dir:/whatever"\n',            # malformed: no target
                         local_pointer='[store]\ntarget = "dir:{}"\n'.format(ovr_store))
        res = resolve_store(pr)
        check("override-wins-over-malformed-committed",
              res.status == RESOLVED and res.pointer_source == "local-override")

        # 3: no pointer and no .working -> NOT-ADOPTED.
        root = build_store(make_working=False)
        check("no-store-not-adopted", resolve_store(root).status == NOT_ADOPTED)

        # 3b (TOCTOU): the product root is a readable directory when resolve_store opens
        # its fd, then is renamed/removed at the resolution boundary before the default-location check. That
        # disappearance is a product root that could no longer be evaluated (CANNOT-EVALUATE, exit 2), NEVER
        # a false NOT-ADOPTED that render --check would report as a clean NOT-APPLICABLE (exit 0). Simulate
        # the race by renaming the root away the instant its fd is opened: the open fd stays valid (the
        # pointer reads see no pointer), but the path-based existence check in _resolve_at now fails. The
        # second assertion confirms the patch actually fired, so a green cannot rest on the rename never
        # happening (self-test-discrimination).
        toctou_root = build_store(make_working=False)
        _real_orf = _open_root_fd
        _toctou = {"fired": False}

        def _toctou_orf(root, _r=_real_orf, _t=toctou_root, _s=_toctou):
            fd = _r(root)
            if not _s["fired"]:
                _s["fired"] = True
                os.rename(str(_t), str(_t) + "-moved")   # root disappears at the resolution boundary
            return fd
        globals()["_open_root_fd"] = _toctou_orf
        try:
            check("toctou-default-root-disappeared-cannot-eval",
                  _guard(lambda: resolve_store(toctou_root).status) == CANNOT_EVALUATE)
            check("toctou-open-was-exercised", _toctou["fired"])
        finally:
            globals()["_open_root_fd"] = _real_orf

        # 4: zero manifests at a POINTER target -> CANNOT-EVALUATE (pointer promised a store).
        empty_store = base / "empty-store"
        (empty_store / WORKING_DIRNAME).mkdir(parents=True)
        pr = build_store(make_working=False, pointer='[store]\ntarget = "dir:{}"\n'.format(empty_store))
        check("zero-at-pointer-cannot-eval", resolve_store(pr).status == CANNOT_EVALUATE)

        # 5: two machine stores under the default .working -> CANNOT-EVALUATE (ambiguous).
        root = build_store(machine_subdirs={"toml": manifest_text(), "toml2": manifest_text()})
        check("two-manifests-cannot-eval", resolve_store(root).status == CANNOT_EVALUATE)

        # 5b (OPF-IMPORTS-RELOCATE): `imports` is reserved at the store level for the fixed import-run staging
        # root `.working/imports/` (spec 4.4/14.1), so a machine store so named fails resolution closed ->
        # CANNOT-EVALUATE, never resolved as the machine store. A normal store keeps its `toml` machine dir.
        root = build_store(machine_subdirs={"imports": manifest_text()})
        check("reserved-imports-name-cannot-eval", resolve_store(root).status == CANNOT_EVALUATE)

        # 6: mistyped token via a POINTER -> CANNOT-EVALUATE (residual 17).
        typo_store = base / "typo-store"
        (typo_store / WORKING_DIRNAME / DEFAULT_MACHINE_SUBDIR).mkdir(parents=True)
        (typo_store / WORKING_DIRNAME / DEFAULT_MACHINE_SUBDIR / MANIFEST_NAME).write_text(
            manifest_text(standard="dev-process"), encoding="utf-8")
        pr = build_store(make_working=False, pointer='[store]\ntarget = "dir:{}"\n'.format(typo_store))
        check("typo-token-pointer-cannot-eval", resolve_store(pr).status == CANNOT_EVALUATE)

        # 6b: mistyped token at the DEFAULT (no pointer) -> CANNOT-EVALUATE (BLOCKER 2, spec residual 17):
        # a PRESENT `.working/` carrying no valid manifest is a present-but-invalid store, distinguishable
        # from a fresh un-adopted repo (case 3, which has NO `.working/`) and never treated as absent, so
        # opf init cannot overwrite it.
        root = build_store(manifest=manifest_text(standard="dev-process"))
        check("typo-token-default-cannot-eval", resolve_store(root).status == CANNOT_EVALUATE)

        # 6c (m2): a RETIRED [devprocess] 1.0.0 store discovered with the DEFAULT tokens is present-but-
        # legacy: resolution is CANNOT-EVALUATE (fail-closed, never resolved on the retired token) and the
        # operator-facing detail names the `opf upgrade` migration remedy (spec 9.2), mirroring the older-
        # spec finding _validate_base surfaces on an already-[opf] store.
        legacy_manifest = ('[devprocess]\nstandard = "devprocess"\nspec_version = "1.0.0"\n'
                           'layout = "inline"\nposture = "required"\nimport_status = "none"\n')
        legacy_res = resolve_store(build_store(manifest=legacy_manifest))
        check("legacy-devprocess-default-cannot-eval", legacy_res.status == CANNOT_EVALUATE)
        check("legacy-devprocess-names-upgrade-remedy",
              "devprocess" in legacy_res.detail and "opf upgrade" in legacy_res.detail)

        # 7: an unresolvable pointer (target dir absent) -> CANNOT-EVALUATE, no default fallback.
        root = build_store(manifest=manifest_text(),      # a valid default store IS present...
                           pointer='[store]\ntarget = "dir:/no/such/store/root"\n')
        res = resolve_store(root)
        check("unresolvable-pointer-cannot-eval", res.status == CANNOT_EVALUATE)

        # 7b: a remote-only git target -> CANNOT-EVALUATE (needs a clone/sync, out of scope).
        pr = build_store(make_working=False,
                         pointer='[store]\ntarget = "git@git.example.com:acme/ops.git"\n')
        check("remote-target-cannot-eval", resolve_store(pr).status == CANNOT_EVALUATE)

        # 7c: a bare RELATIVE path (forbidden outside dir:) -> CANNOT-EVALUATE (malformed pointer).
        pr = build_store(make_working=False, pointer='[store]\ntarget = "../relative-store"\n')
        check("bare-relative-cannot-eval", resolve_store(pr).status == CANNOT_EVALUATE)

        # 7d: an absolute dir: target with a symlinked ANCESTOR is refused by the no-follow walk (MAJOR 2).
        # The final component is a real dir, so the old final-only O_NOFOLLOW open would FOLLOW the
        # symlinked ancestor and return RESOLVED; the component-by-component walk refuses it. The store
        # behind the symlink IS valid, proving the refusal is the ancestor symlink, not a missing store.
        anc_real = base / "anc-real-dir"
        anc_store = anc_real / "store"
        (anc_store / WORKING_DIRNAME / DEFAULT_MACHINE_SUBDIR).mkdir(parents=True)
        (anc_store / WORKING_DIRNAME / DEFAULT_MACHINE_SUBDIR / MANIFEST_NAME).write_text(
            manifest_text(), encoding="utf-8")
        anc_link = base / "anc-link"
        os.symlink(str(anc_real), str(anc_link))          # anc_link -> anc_real: a symlinked ancestor
        pr = build_store(make_working=False,
                         pointer='[store]\ntarget = "dir:{}/store"\n'.format(anc_link))
        check("symlink-ancestor-refused", resolve_store(pr).status == CANNOT_EVALUATE)

        # 7e: a RELATIVE dir: target that is a symlink is refused (MAJOR 2, ~313): the removed
        # Path.resolve() would have canonicalized the symlink away and opened the real store behind it
        # (RESOLVED); the joined path is walked no-follow instead -> CANNOT-EVALUATE. The symlink lives
        # inside the product root and points at a valid store, so the refusal is the symlink, not absence.
        relroot = build_store(make_working=False)          # a product root with no store of its own
        rel_real = relroot / "rel-real-store"
        (rel_real / WORKING_DIRNAME / DEFAULT_MACHINE_SUBDIR).mkdir(parents=True)
        (rel_real / WORKING_DIRNAME / DEFAULT_MACHINE_SUBDIR / MANIFEST_NAME).write_text(
            manifest_text(), encoding="utf-8")
        os.symlink(str(rel_real), str(relroot / "rel-link-store"))   # a symlink inside the product root
        (relroot / POINTER_REL).write_text('[store]\ntarget = "dir:rel-link-store"\n', encoding="utf-8")
        check("symlink-relative-dir-refused", resolve_store(relroot).status == CANNOT_EVALUATE)

        # 8: malformed base fails closed. A discovered store (token present) whose base violates the
        # schema is INVALID, never VALID.
        bad_base = manifest_text().replace('posture = "required"', 'posture = "loose"')
        root = build_store(manifest=bad_base)
        res = resolve_store(root)
        check("bad-base-still-discovered", res.status == RESOLVED)
        mv = load_manifest(res)
        check("bad-base-invalid", mv.status == INVALID and any("posture" in f for f in mv.findings))

        # 8b: validate() on a non-opf / not-a-table input -> CANNOT-EVALUATE.
        check("not-a-table-cannot-eval", validate_manifest([]).status == CANNOT_EVALUATE)
        check("no-base-cannot-eval", validate_manifest({"store": {}}).status == CANNOT_EVALUATE)

        # 8c: an unknown top-level table AND an unknown [opf] key are each findings. The input adds
        # BOTH a top-level [bogus] table and an unknown key UNDER [opf], so the comment matches what
        # is actually exercised.
        import tomllib as _t
        m_extra = _t.loads(manifest_text(extra_top="[bogus]\nx = 1").replace(
            'import_status = "none"', 'import_status = "none"\nmystery_key = 1'))
        mv_extra = validate_manifest(m_extra)
        check("unknown-top-table-invalid", mv_extra.status == INVALID)
        check("unknown-top-table-named", any("bogus" in f for f in mv_extra.findings))
        check("unknown-opf-key-named", any("mystery_key" in f for f in mv_extra.findings))

        # 9: a profile is IGNORED by a base-only validator but ENFORCED by a profile-aware one.
        m_aiqt = _t.loads(manifest_text(with_aiqt=True))
        base_only = validate_manifest(m_aiqt)
        check("profile-base-only-valid", base_only.status == VALID)
        check("profile-base-only-unevaluated", "aiqt" in base_only.unevaluated_profiles)
        aware = validate_manifest(m_aiqt, supported_profiles={"aiqt": {1}})
        check("profile-aware-valid", aware.status == VALID and not aware.findings)
        check("profile-aware-not-unevaluated", "aiqt" not in aware.unevaluated_profiles)

        # 9b: an unknown profile (acme) stays unevaluated even in profile-aware mode (fail-safe).
        m_acme = _t.loads(manifest_text(with_aiqt=True, with_acme=True))
        aware = validate_manifest(m_acme, supported_profiles={"aiqt": {1}})
        check("unknown-profile-unevaluated", "acme" in aware.unevaluated_profiles and aware.status == VALID)

        # 9c: an unsupported profile MAJOR is ignored for enforcement (unevaluated), never a fail.
        m_v2 = _t.loads(manifest_text(with_aiqt=True, aiqt_version="2.0.0",
                                      aiqt_compat=">=2.0.0 <3.0.0"))
        aware = validate_manifest(m_v2, supported_profiles={"aiqt": {1}})
        check("unsupported-major-unevaluated", "aiqt" in aware.unevaluated_profiles and aware.status == VALID)

        # 10: a WEAKENING profile floor is surfaced (base required, floor warn).
        m_weak = _t.loads(manifest_text(with_aiqt=True, aiqt_floor="warn"))
        aware = validate_manifest(m_weak, supported_profiles={"aiqt": {1}})
        check("weakening-floor-invalid", aware.status == INVALID)
        check("weakening-floor-named", any("weakens the base posture" in f for f in aware.findings))

        # 10b: an extension_namespace not registered in [vendors] is surfaced.
        m_unreg = _t.loads(manifest_text(with_aiqt=True, register_xaiqt=False))
        aware = validate_manifest(m_unreg, supported_profiles={"aiqt": {1}})
        check("unregistered-ns-invalid", aware.status == INVALID)
        check("unregistered-ns-named", any("not registered in [vendors]" in f for f in aware.findings))

        # 10c: a required module not enabled is surfaced.
        m_nomod = _t.loads(manifest_text(with_aiqt=True, ops_enabled=False))
        aware = validate_manifest(m_nomod, supported_profiles={"aiqt": {1}})
        check("required-module-invalid", aware.status == INVALID)
        check("required-module-named", any("required_modules not enabled" in f for f in aware.findings))

        # 10d: a base-incompatible profile instance fails closed.
        m_incompat = _t.loads(manifest_text(with_aiqt=True, spec_version="1.0.0",
                                            aiqt_version="1.0.0", aiqt_compat=">=2.0.0 <3.0.0"))
        aware = validate_manifest(m_incompat, supported_profiles={"aiqt": {1}})
        check("base-incompat-invalid", aware.status == INVALID)
        check("base-incompat-named", any("does not admit the base spec_version" in f for f in aware.findings))

        # 10e: a COVERED profile (aiqt is supported) whose version is not a bare SemVer cannot be graded
        # and is a FAIL-CLOSED finding (INVALID), never silently routed to unevaluated, which would skip
        # all its gates and validate VALID (BLOCKER 1).
        m_badver = _t.loads(manifest_text(with_aiqt=True, aiqt_version="not-a-semver"))
        aware = validate_manifest(m_badver, supported_profiles={"aiqt": {1}})
        check("covered-profile-bad-version-invalid", aware.status == INVALID)
        check("covered-profile-bad-version-not-unevaluated", "aiqt" not in aware.unevaluated_profiles)
        check("covered-profile-bad-version-named",
              any("aiqt" in f and "major cannot be determined" in f for f in aware.findings))

        # 10f: a wrong-TYPED base field (posture as a LIST) is a fail-closed finding, NEVER a crash, even
        # with a supported profile whose weakening check reads the base posture through POSTURE_RANK
        # (MAJOR 3: an unhashable list would otherwise raise TypeError at the dict-membership test).
        m_badtype = _t.loads(manifest_text(with_aiqt=True).replace(
            'posture = "required"', 'posture = ["required"]'))
        aware = validate_manifest(m_badtype, supported_profiles={"aiqt": {1}})
        check("wrong-typed-posture-invalid", aware.status == INVALID)
        check("wrong-typed-posture-named", any("posture must be a string" in f for f in aware.findings))

        # 10g: a store declaring a spec_version OLDER than SUPPORTED_SPEC_VERSION is fail-closed with a
        # distinct finding stating that no store upgrade reaches 2.0.0 (not the confusing roster
        # cascade, and naming no upgrade command); a store at the current spec_version is clean.
        m_old = _t.loads(manifest_text(spec_version="1.0.0"))
        mv_old = validate_manifest(m_old)
        check("spec-version-floor-invalid", mv_old.status == INVALID)
        check("spec-version-floor-named",
              any("older than the" in f and "no store upgrade reaches spec_version 2.0.0" in f
                  and "opf upgrade" not in f for f in mv_old.findings))
        # The older refusal names the manifest path and the declared spec_version, states that no
        # store upgrade reaches 2.0.0 and that the store is refused, and states the hand step
        # (spec 9.2), pinned whole: rewording it, dropping the path or the hand step, or offering an
        # upgrade again, is red.
        floor_rel = ".working/toml/manifest.toml"
        floor_message = (
            "'.working/toml/manifest.toml' declares [opf].spec_version '1.0.0', older than the 1.2.0 "
            "this tooling implements; no store upgrade reaches spec_version 2.0.0, so the store is "
            "refused; to adopt this tree instead, first move '.working/toml/manifest.toml' "
            "aside by hand: its files are then ordinary foreign content for a first adoption "
            "(spec 9.2; fail-closed)")
        check("spec-version-floor-message-pinned",
              SUPPORTED_SPEC_VERSION == "1.2.0"
              and floor_message in validate_manifest(m_old, manifest_rel=floor_rel).findings)
        check("spec-version-floor-message-unnamed-path",
              any(f.startswith("the store manifest declares [opf].spec_version '1.0.0',")
                  and "no store upgrade reaches spec_version 2.0.0, so the store is refused" in f
                  and "first move the store manifest aside by hand" in f for f in mv_old.findings))
        check("spec-version-current-ok",
              validate_manifest(_t.loads(manifest_text(spec_version=SUPPORTED_SPEC_VERSION))).status == VALID)
        # 10g1: load_manifest threads the RESOLVED manifest's own path into validation, so the
        # older-spec_version refusal read through store resolution names that path, never the
        # "the store manifest" fallback.
        mv_old_loaded = load_manifest(resolve_store(
            build_store(manifest=manifest_text(spec_version="1.0.0"))))
        check("spec-version-floor-load-manifest-names-path",
              mv_old_loaded.status == INVALID and floor_message in mv_old_loaded.findings)

        # 10g2 (spec 9.2): a store declaring a spec_version ABOVE SUPPORTED_SPEC_VERSION is fail-closed at
        # validation with its own named finding, so older tooling never certifies a newer store (a bare
        # 1.3.0 declaration included) under legacy checks. The single carve-out is the exact reserved
        # homes-2 declaration pair (spec 4.2), which homes_generation() gates on SUPPORTED_HOMES; a
        # version-only 2.0.0, or an above-version paired with homes = 2, still refuses.
        mv_above = validate_manifest(_t.loads(manifest_text(spec_version="1.3.0")))
        check("spec-version-ceiling-invalid", mv_above.status == INVALID)
        check("spec-version-ceiling-named",
              any("above the" in f and "upgrade the tooling" in f for f in mv_above.findings))
        check("spec-version-ceiling-2.0.0-alone-invalid",
              validate_manifest(_t.loads(manifest_text(spec_version="2.0.0"))).status == INVALID)
        ceiling_anchor = 'import_status = "none"'
        homes2_pair = manifest_text(spec_version="2.0.0").replace(
            ceiling_anchor, ceiling_anchor + "\nhomes = 2")
        check("spec-version-ceiling-homes2-pair-recognized",
              validate_manifest(_t.loads(homes2_pair)).status == VALID)
        above_pair = manifest_text(spec_version="1.3.0").replace(
            ceiling_anchor, ceiling_anchor + "\nhomes = 2")
        check("spec-version-ceiling-non-homes2-pair-invalid",
              validate_manifest(_t.loads(above_pair)).status == INVALID)

        # 10h: [opf].homes is an optional, validated integer generation declaration.
        # Recognition does not activate homes 2 or require a declaration in legacy manifests.
        homes_manifest_text = manifest_text(spec_version=SUPPORTED_SPEC_VERSION)
        homes_anchor = 'import_status = "none"'
        check("homes-fixture-anchor", homes_manifest_text.count(homes_anchor) == 1)

        m_homes_absent = _t.loads(homes_manifest_text)
        mv_homes_absent = validate_manifest(m_homes_absent)
        check("homes-absent-valid",
              "homes" not in m_homes_absent["opf"]
              and mv_homes_absent.status == VALID
              and not any("homes" in f for f in mv_homes_absent.findings))
        check("homes-absent-legacy-generation",
              homes_generation(m_homes_absent) == 1)

        for generation in (1, 2):
            m_homes = _t.loads(homes_manifest_text.replace(
                homes_anchor, homes_anchor + "\nhomes = {}".format(generation)))
            mv_homes = validate_manifest(m_homes)
            check("homes-{}-valid".format(generation),
                  type(m_homes["opf"].get("homes")) is int
                  and m_homes["opf"]["homes"] == generation
                  and mv_homes.status == VALID
                  and not any("unknown key" in f or "[opf].homes" in f
                              for f in mv_homes.findings))

        for label, rhs in (
                ("zero", "0"),
                ("three", "3"),
                ("string", '"2"'),
                ("bool", "true")):
            # Parse first: these are valid TOML values whose declarations must fail validation.
            m_homes_bad = _t.loads(homes_manifest_text.replace(
                homes_anchor, homes_anchor + "\nhomes = " + rhs))
            mv_homes_bad = validate_manifest(m_homes_bad)
            check("homes-{}-invalid-named".format(label),
                  mv_homes_bad.status == INVALID
                  and any("[opf].homes" in f and "integer generation" in f
                          for f in mv_homes_bad.findings))

        # 11: the base_compat range grammar (defined here).
        check("compat-match", _match_base_compat(">=1.0.0 <2.0.0", (1, 2, 3)) == (True, None))
        check("compat-nomatch", _match_base_compat(">=2.0.0", (1, 0, 0))[0] is False)
        check("compat-malformed", _match_base_compat(">=x.y.z", (1, 0, 0))[0] is None)
        check("compat-exact", _match_base_compat("1.0.0", (1, 0, 0)) == (True, None))
        # MINOR 1: a malformed clause is flagged EVEN when an earlier clause already fails the match, so
        # the whole declared range is validated rather than short-circuiting to a silent (False, None).
        check("compat-malformed-clause-not-masked",
              _match_base_compat(">=2.0.0 garbage", (1, 0, 0))[0] is None)

        # 12: the section-8.1 record-model type taxonomy is ENFORCED (MAJOR 1), not merely a two-letter
        # shape + local-uniqueness check. An UNKNOWN type name is a finding.
        m_unknown = _t.loads(manifest_text(extra_top='[types.foobar]\nnamespace = "ZZ"'))
        mv_unknown = validate_manifest(m_unknown)
        check("taxonomy-unknown-type-invalid", mv_unknown.status == INVALID)
        check("taxonomy-unknown-type-named",
              any("foobar" in f and "not a known record type" in f for f in mv_unknown.findings))

        # 12b: a KNOWN type carrying the WRONG namespace is a finding (finding's normative ns is FN).
        m_wrongns = _t.loads(manifest_text(extra_top='[types.finding]\nnamespace = "ZZ"'))
        mv_wrongns = validate_manifest(m_wrongns)
        check("taxonomy-wrong-namespace-invalid", mv_wrongns.status == INVALID)
        check("taxonomy-wrong-namespace-named",
              any("not the normative namespace" in f and "finding" in f for f in mv_wrongns.findings))

        # 12c: the reserved-EXCLUDED type `transaction` (namespace TX) is never valid, even with its own
        # reserved namespace.
        m_txn = _t.loads(manifest_text(extra_top='[types.transaction]\nnamespace = "TX"'))
        mv_txn = validate_manifest(m_txn)
        check("taxonomy-reserved-transaction-invalid", mv_txn.status == INVALID)
        check("taxonomy-reserved-transaction-named",
              any("transaction" in f and "reserved and excluded" in f for f in mv_txn.findings))

        # 12d: a KNOWN baseline type carrying its NORMATIVE namespace is VALID (finding -> FN).
        m_goodtype = _t.loads(manifest_text(extra_top='[types.finding]\nnamespace = "FN"'))
        mv_goodtype = validate_manifest(m_goodtype)
        check("taxonomy-correct-binding-valid", mv_goodtype.status == VALID and not mv_goodtype.findings)

        # 12e: a MODULE-tier type is valid ONLY when its module is enabled. `artifact` (AR) belongs to
        # delivery_assurance, which manifest_text leaves OFF: declaring it is a finding...
        m_modoff = _t.loads(manifest_text(extra_top='[types.artifact]\nnamespace = "AR"'))
        mv_modoff = validate_manifest(m_modoff)
        check("taxonomy-module-type-disabled-invalid", mv_modoff.status == INVALID)
        check("taxonomy-module-type-disabled-named",
              any("artifact" in f and "not enabled in [modules]" in f for f in mv_modoff.findings))
        # ... and VALID once delivery_assurance is enabled.
        m_modon = _t.loads(manifest_text(extra_top='[types.artifact]\nnamespace = "AR"').replace(
            "concurrent_operation = true", "concurrent_operation = true\ndelivery_assurance = true"))
        mv_modon = validate_manifest(m_modon)
        check("taxonomy-module-type-enabled-valid", mv_modon.status == VALID and not mv_modon.findings)

        # FIX 4(a): an ABSENT [types] table registers none of the store's enabled types and is INVALID
        # (spec 9/782, one [types.<name>] per enabled type). manifest_text always carries [types] so
        # remove it to isolate the case.
        m_notypes = _t.loads(manifest_text())
        m_notypes.pop("types", None)
        mv_notypes = validate_manifest(m_notypes)
        check("fix4a-types-absent-invalid", mv_notypes.status == INVALID)
        check("fix4a-types-absent-named",
              any("must register at least one enabled type" in f for f in mv_notypes.findings))

        # FIX 4(b): view/deliverable targets and unmanaged paths are ROOT-RELATIVE and CONTAINED
        # (spec 5.8/14.2). A '..' escape, an absolute target, and an escaping unmanaged path are each
        # INVALID; a normal root-relative set is VALID.
        _view = '[views."X.md"]\nkind = "deterministic"\nsources = ["worklog"]\ntarget = {!r}'.format
        m_view_escape = _t.loads(manifest_text(extra_top=_view("../../outside")))
        check("fix4b-view-target-escape-invalid", validate_manifest(m_view_escape).status == INVALID)
        m_view_abs = _t.loads(manifest_text(extra_top=_view("/etc/passwd")))
        check("fix4b-view-target-absolute-invalid", validate_manifest(m_view_abs).status == INVALID)
        m_deliv_escape = _t.loads(manifest_text(
            extra_top='[deliverables."CHANGELOG.md"]\nkind = "curated"\ntarget = "../../outside"'))
        check("fix4b-deliverable-target-escape-invalid",
              validate_manifest(m_deliv_escape).status == INVALID)
        m_unmanaged_escape = _t.loads(manifest_text(extra_top='[unmanaged]\npaths = ["../outside"]'))
        check("fix4b-unmanaged-path-escape-invalid",
              validate_manifest(m_unmanaged_escape).status == INVALID)
        m_paths_ok = _t.loads(manifest_text(
            extra_top=_view(".working/X.md") + '\n\n[unmanaged]\npaths = ["README.md", "docs/legacy.md"]'))
        mv_paths_ok = validate_manifest(m_paths_ok)
        check("fix4b-contained-paths-valid", mv_paths_ok.status == VALID and not mv_paths_ok.findings)

        # 13: a RELATIVE product --root resolves IDENTICALLY to the same root passed absolutely (MAJOR 2).
        # Before the fix, a relative dir: companion joined to a relative product root produced a relative
        # store root that _open_dir_nofollow rejected as not absolute (CANNOT-EVALUATE); os.path.abspath
        # anchors it to the cwd without following symlinks, so the relative root now resolves.
        rel_prod = build_store(make_working=False)
        rel_comp = rel_prod / "rel-companion"
        (rel_comp / WORKING_DIRNAME / DEFAULT_MACHINE_SUBDIR).mkdir(parents=True)
        (rel_comp / WORKING_DIRNAME / DEFAULT_MACHINE_SUBDIR / MANIFEST_NAME).write_text(
            manifest_text(), encoding="utf-8")
        (rel_prod / POINTER_REL).write_text('[store]\ntarget = "dir:rel-companion"\n', encoding="utf-8")
        abs_res = resolve_store(rel_prod)
        check("relative-root-abs-baseline", abs_res.status == RESOLVED)
        # test-hermeticity: resolve the SAME cwd-relative product root WITHOUT os.chdir, which would mutate
        # the ambient process cwd (a test leaves the host as it found it, and shared process state is exactly
        # the kind of surrounding a test's verdict must not depend on or perturb). A CHILD process carries its
        # cwd via subprocess cwd=base instead, resolves the relative name there, and prints its status and
        # resolved store root for the parent to compare. This exercises os.path.abspath's cwd anchoring (the
        # MAJOR 2 relative-root path) exactly as before, but with no mutation of this process's cwd.
        from _opf_emit import run_status_owned
        import subprocess
        import json
        # test-hermeticity: launch the child ISOLATED (-I ignores PYTHON* env like PYTHONHOME/PYTHONPATH and
        # user site; -B suppresses __pycache__ writes into the tools dir), so a hostile ambient PYTHONHOME the
        # parent inherits cannot break the child interpreter, and supply the sibling-import path EXPLICITLY
        # inside the child (since -I ignores PYTHONPATH), so the child's verdict comes from store resolution,
        # not the ambient interpreter env.
        _child_src = (
            "import sys, json\n"
            "sys.path.insert(0, sys.argv[1])\n"
            "import _opf_store as S\n"
            "r = S.resolve_store(sys.argv[2])\n"
            "sys.stdout.write(json.dumps([r.status, None if r.store_root is None else str(r.store_root)]))\n"
            "return 0\n")
        try:
            _child = run_status_owned(
                [sys.executable, "-I", "-B", "-c", _child_src,
                 str(Path(__file__).resolve().parent), rel_prod.name],
                fixture_id="relative-root-resolves", cwd=str(base), capture_output=True, text=True)
            _rel_status, _rel_store = (json.loads(_child.stdout)
                                       if _child.returncode == 0 and _child.stdout else (None, None))
            check("relative-root-resolves", _rel_status == RESOLVED)
            check("relative-root-matches-abs",
                  _rel_store is not None and _rel_store == str(abs_res.store_root))
        except (RuntimeError, subprocess.SubprocessError, OSError, ValueError) as exc:
            print("relative-root-resolves", str(exc))
            check("relative-root-resolves", False)
            check("relative-root-matches-abs", False)

        # ITEM C (ambient-cwd crash): a RELATIVE product root reaches os.path.abspath, whose os.getcwd()
        # raises when the process cwd is deleted or unreadable. resolve_store must MAP that to
        # CANNOT-EVALUATE (its no-raise contract), never let the OSError escape uncaught. Simulate the
        # unreadable cwd by making os.getcwd raise; the pre-abspath exists()/is_dir() gates use os.stat(".")
        # (which does NOT consult getcwd) so they still pass and the abspath path is reached. _guard'd so a
        # reverted fix (the OSError escaping) is a NAMED counted failure, not a traceback that aborts the
        # suite; still passes only on CANNOT-EVALUATE (no weakened detection). getcwd restored in finally.
        _real_getcwd = os.getcwd
        os.getcwd = lambda: (_ for _ in ()).throw(FileNotFoundError(2, "No such file or directory"))
        try:
            check("itemC-abspath-cwd-unreadable-cannot-eval",
                  _guard(lambda: resolve_store(".").status) == CANNOT_EVALUATE)
        finally:
            os.getcwd = _real_getcwd
        # ---- reconcile-draft fix vectors (fail pre-fix, pass post-fix) --------------------------------
        # M1: an oddly-named .working subdir (_check_rel refuses a backslash) -> CANNOT-EVALUATE, never an
        # uncaught JournalError out of resolve_store.
        _m1subs = dict()
        _m1subs[DEFAULT_MACHINE_SUBDIR] = manifest_text()
        _m1subs["bad" + chr(92) + "name"] = manifest_text()
        m1_root = build_store(machine_subdirs=_m1subs)
        check("m1-oddly-named-subdir-cannot-eval", resolve_store(m1_root).status == CANNOT_EVALUATE)

        # S1.3: a listing I/O error inside .working fails closed, never read as an empty (no-store) dir.
        # The enumeration reads through os.scandir on the fresh listing descriptor (an int;
        # F-JOURNAL-HELD-FD-LISTING), so the fault is injected there.
        s13_root = build_store(manifest=manifest_text())
        _real_scandir = os.scandir
        os.scandir = (lambda x: (_ for _ in ()).throw(OSError(5, "EIO"))
                      if isinstance(x, int) else _real_scandir(x))
        try:
            check("s13-listing-io-error-cannot-eval", resolve_store(s13_root).status == CANNOT_EVALUATE)
        finally:
            os.scandir = _real_scandir

        # S2.2: a non-ASCII x-<vendor> slug is not a valid extension namespace (islower()/isdigit() admit
        # non-ASCII; the ASCII alphabet does not).
        check("s22-ascii-vendor-ok", _valid_extension_namespace("x-acme1"))
        check("s22-unicode-vendor-rejected", not _valid_extension_namespace("x-" + chr(0xe9)))
        check("s22-unicode-digit-vendor-rejected", not _valid_extension_namespace("x-" + chr(0xb2)))

        # S2.3: a NEGATIVE supported major is malformed -> CANNOT-EVALUATE, never silently routing a covered
        # (weakening) profile to unevaluated and validating VALID.
        m_weak2 = _t.loads(manifest_text(with_aiqt=True, aiqt_floor="warn"))
        _neg = dict(); _neg["aiqt"] = [-1]
        check("s23-negative-major-cannot-eval",
              validate_manifest(m_weak2, supported_profiles=_neg).status == CANNOT_EVALUATE)

        # S2.4/S2.5: an unbounded/exploding majors control (huge range, throwing iterator) is cut off or
        # caught at the materialization boundary -> CANNOT-EVALUATE, never an eager alloc/hang/escape.
        _hr = dict(); _hr["aiqt"] = range(10 ** 12)
        check("s24-huge-range-cannot-eval",
              validate_manifest(m_weak2, supported_profiles=_hr).status == CANNOT_EVALUATE)
        def _one_then_raise():
            yield 1
            raise OSError("boom")
        _thr = dict(); _thr["aiqt"] = _one_then_raise()
        check("s25-throwing-iterator-cannot-eval",
              validate_manifest(m_weak2, supported_profiles=_thr).status == CANNOT_EVALUATE)

        # m3: an oversized-int table key renders through _safe_display rather than crashing the validator
        # (including inside the fail-closed supported_profiles branch).
        _big = 10 ** 5000
        _spk = dict(); _spk[_big] = [1]
        check("m3-oversized-sp-key-cannot-eval",
              validate_manifest(_t.loads(manifest_text()), supported_profiles=_spk).status == CANNOT_EVALUATE)
        _md = _t.loads(manifest_text()); _tk = dict(); _tk[_big] = dict(namespace="BI"); _md["types"] = _tk
        check("m3-oversized-types-key-invalid", validate_manifest(_md).status == INVALID)

        # m4: a target/path resolving AT the root (".", "a/..") is not contained-below; a genuine contained
        # path is still accepted.
        check("m4-at-root-dot-rejected", not _is_contained_relpath("."))
        check("m4-at-root-dotdot-rejected", not _is_contained_relpath("a/.."))
        check("m4-contained-still-ok",
              _is_contained_relpath("a/../b") and _is_contained_relpath("docs/x.md"))

        # m5: a '//'-anchored absolute path is accepted as a root spelling, not falsely rejected. The probe
        # runs INSIDE the check via _guard: with the fix reverted _open_dir_nofollow("//") raises, which now
        # yields a NAMED counted failure rather than aborting the suite with an uncaught traceback (MINOR-3).
        def _double_slash_root_opens():
            _fd = _open_dir_nofollow("//")
            os.close(_fd)
            return "opened"
        check("m5-double-slash-root-accepted", _guard(_double_slash_root_opens) == "opened")

        # S1.4 (defence in depth): a NUL-bearing pointer target fails closed as OSError, honouring
        # _open_dir_nofollow's documented OSError contract rather than raising an unmapped ValueError.
        _kind = None
        try:
            _open_dir_nofollow("/tmp/a" + chr(0) + "b")
        except OSError:
            _kind = "OSError"
        except ValueError:
            _kind = "ValueError"
        check("s14-nul-target-oserror", _kind == "OSError")

        # travanc: the no-follow walk crosses a TRAVERSE-ONLY ancestor (search permission, no read; the
        # owner-bound spelling of the 0711 /home/<user> shape is 0111) without weakening symlink refusal.
        # Vector 1 FAILS WITHOUT THE FIX (the mutant: intermediate components opened O_RDONLY instead of
        # _WALK_O_TRAVERSE raises EACCES at "mid"); it discriminates only for a uid the mode bits bind
        # (not uid 0) on a platform with O_PATH, so those runs disclose a skip rather than passing silently.
        import errno as _ta_errno
        _ta = base / "travanc-searchonly"
        (_ta / "mid" / "leaf").mkdir(parents=True)
        if hasattr(os, "O_PATH") and os.geteuid() != 0:
            os.chmod(_ta / "mid", 0o111)
            try:
                def _ta_open():
                    _ta_fd = _open_dir_nofollow(str(_ta / "mid" / "leaf"))
                    try:
                        _ta_names = os.listdir(_ta_fd)   # the returned FINAL descriptor stays READABLE
                    finally:
                        os.close(_ta_fd)
                    return "opened" if _ta_names == [] else "unreadable-leaf"
                check("travanc-search-only-ancestor-opens", _guard(_ta_open) == "opened")

                def _ta_traverse():
                    _ta_fd = _open_dir_nofollow(str(_ta / "mid"), traverse_only=True)
                    try:
                        # traverse-only: the search-only directory ITSELF opens, and dir_fd-relative
                        # lookups (which need search permission only) work through it.
                        _ta_st = os.stat("leaf", dir_fd=_ta_fd, follow_symlinks=False)
                    finally:
                        os.close(_ta_fd)
                    return "probed" if stat.S_ISDIR(_ta_st.st_mode) else "not-a-dir"
                check("travanc-traverse-only-final-probes", _guard(_ta_traverse) == "probed")
            finally:
                os.chmod(_ta / "mid", 0o755)
        else:
            print("DISCLOSED SKIP: travanc-search-only-ancestor-opens and "
                  "travanc-traverse-only-final-probes not run (uid 0 is not bound by mode bits, or "
                  "os.O_PATH is absent and the walk keeps the O_RDONLY fallback)")
        # Symlink refusal is NOT relaxed by the O_PATH steps, leaf and intermediate alike. The errno is
        # pinned to the no-follow pair {ENOTDIR, ELOOP}: with O_DIRECTORY|O_NOFOLLOW a symlink refuses as
        # ENOTDIR on current Linux (verified: O_PATH and O_RDONLY modes alike, the link object is not a
        # directory); ELOOP is accepted as the pair's other no-follow refusal (no kernel reporting it was
        # exercised); both are refusals, and anything else (above all a successful open, the
        # symlink-following defect) fails this check.
        (_ta / "real" / "sub").mkdir(parents=True)
        os.symlink("real", _ta / "lnk")

        def _ta_errno_of(thunk):
            try:
                _ta_fd = thunk()
            except OSError as exc:
                return exc.errno
            os.close(_ta_fd)
            return "opened"
        _ta_refusals = (_ta_errno.ENOTDIR, _ta_errno.ELOOP)
        check("travanc-symlinked-leaf-still-refused",
              _ta_errno_of(lambda: _open_dir_nofollow(str(_ta / "lnk"))) in _ta_refusals)
        check("travanc-symlinked-intermediate-still-refused",
              _ta_errno_of(lambda: _open_dir_nofollow(str(_ta / "lnk" / "sub"))) in _ta_refusals)
        check("travanc-symlinked-leaf-traverse-only-still-refused",
              _ta_errno_of(lambda: _open_dir_nofollow(str(_ta / "lnk"), traverse_only=True))
              in _ta_refusals)

        # m8: a store file larger than the read cap is refused rather than read unboundedly.
        big_manifest = manifest_text() + chr(10) + "#" + ("x" * (MAX_STORE_READ_BYTES + 16))
        m8_root = build_store(manifest=big_manifest)
        check("m8-oversized-manifest-cannot-eval", resolve_store(m8_root).status == CANNOT_EVALUATE)

        # m8b: a METERED read (the allowance source discovery passes resolve_store_fd) deducts what it read,
        # refuses a file larger than the allowance left before opening it, and, for a file found larger only
        # at the read (one grown after its lstat), stops at the limit: the read never takes a byte past it.
        _m8b = base / "m8b-metered"; _m8b.mkdir()
        (_m8b / "f.toml").write_bytes(b"a = 1\n")
        _m8b_real_read = os.read
        _m8b_bytes = [0]

        def _m8b_read(fd_arg, n_arg):
            block = _m8b_real_read(fd_arg, n_arg)
            _m8b_bytes[0] += len(block)
            return block
        _m8b_fd = os.open(str(_m8b), os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.read = _m8b_read
            _m8b_left = [10]
            check("m8b-metered-read-deducts",
                  _guard(lambda: _read_store_bytes_contained(_m8b_fd, "f.toml", _m8b_left)) == b"a = 1\n"
                  and _m8b_left == [4])
            _m8b_left = [5]
            _m8b_bytes[0] = 0
            check("m8b-metered-over-allowance-refused-unread",
                  _guard(lambda: _read_store_bytes_contained(_m8b_fd, "f.toml", _m8b_left)) == "RAISED"
                  and _m8b_left == [5] and _m8b_bytes[0] == 0)
            _m8b_bytes[0] = 0
            check("m8b-stop-at-cap-reads-no-byte-past",
                  _guard(lambda: _journal._read_contained(_m8b_fd, "f.toml", cap=4)) == "RAISED"
                  and _m8b_bytes[0] == 4)
            check("m8b-stop-at-cap-exact-fit",
                  _guard(lambda: _journal._read_contained(_m8b_fd, "f.toml", cap=6)[0]) == b"a = 1\n")
        finally:
            os.read = _m8b_real_read
            os.close(_m8b_fd)

        # ---- round-2 reconcile-draft fix vectors (fail pre-fix, pass post-fix) ------------------------
        # The FIFO-hang alarm marker MUST NOT derive from OSError: a reader's own `except OSError`
        # (read_frames, read_lock_owner, _read_contained) would launder an OSError-derived marker such as
        # TimeoutError into a JournalError, so a genuine writer-less-FIFO hang would read as a refusal and the
        # check would pass while blocking. A distinct non-OSError marker propagates out of the reader instead,
        # so a hang is a check FAILURE, never a silent slow pass (self-test-discrimination).
        from _opf_emit import run_bounded

        def _refused_no_hang(thunk, keep_fds=()):
            """Run the refusal probe in a child; never borrow caller signal state. A
            pre-opened descriptor the probe relies on must be DECLARED via keep_fds
            (the run_bounded fd allowlist: subjects see only stdio plus declared
            descriptors), so an undeclared dirfd is a loud EBADF, never a leak."""
            def probe():
                try:
                    thunk()
                except _journal.JournalError:
                    return "REFUSED"
                return "ACCEPTED"
            # The bound is a hang guard, not a latency claim: correct code refuses in microseconds, but
            # PR #363 measured the in-process armed window at up to 73 ms under pinned-CPU contention
            # (24 busy siblings on one CPU), where 2 s gave only 27x against the required 50x; this probe
            # also pays a child start-up, so the bound is 20 s (above 270x on that measurement), costing
            # time only when a hang regression exists.
            return run_bounded(probe, timeout_s=20, keep_fds=keep_fds) == "REFUSED"

        # M2: _read_contained does not hang on a writer-less FIFO (the raced regular-file->FIFO swap); it
        # returns a fail-closed JournalError at once. The non-OSError marker makes a blocking regression a
        # check failure (the earlier TimeoutError marker, an OSError subclass, was laundered to JournalError
        # by _read_contained's own `except OSError` and so passed even while blocking).
        _fd_dir = base / "m2-fifo"; _fd_dir.mkdir()
        os.mkfifo(str(_fd_dir / "f"))
        _rfd = os.open(str(_fd_dir), os.O_RDONLY | os.O_DIRECTORY)
        try:
            check("m2-fifo-no-hang-refused",
                  _refused_no_hang(lambda: _journal._read_contained(_rfd, "f"), keep_fds=(_rfd,)))
        finally:
            os.close(_rfd)

        # NEW-2: the FIFO-hang class survived at three sibling _journal readers (read_frames, read_lock_owner,
        # _read_at); each now opens O_NONBLOCK so a writer-less FIFO is refused at the fstat gate at once,
        # never a hang. A hostile on-disk tree can pre-plant these paths.
        _rf_dir = base / "n2-read-frames"; _rf_dir.mkdir()
        os.mkfifo(str(_rf_dir / "frames.log"))
        _rf_jr = os.open(str(base), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            check("new2-read-frames-fifo-no-hang",
                  _refused_no_hang(lambda: _journal.read_frames(_rf_jr, _rf_dir), keep_fds=(_rf_jr,)))
        finally:
            os.close(_rf_jr)
        _rl_dir = base / "n2-read-lock-owner"; _rl_dir.mkdir()
        os.mkfifo(str(_rl_dir / "lock"))
        check("new2-read-lock-owner-fifo-no-hang", _refused_no_hang(lambda: _journal.read_lock_owner(_rl_dir)))
        _ra_dir = base / "n2-read-at"; _ra_dir.mkdir()
        os.mkfifo(str(_ra_dir / "f"))
        _rafd = os.open(str(_ra_dir), os.O_RDONLY | os.O_DIRECTORY)
        try:
            check("new2-read-at-fifo-no-hang",
                  _refused_no_hang(lambda: _journal._read_at(_rafd, "f", "f"), keep_fds=(_rafd,)))
        finally:
            os.close(_rafd)

        # NEW-1: a hostile store file with an oversized BASE-10 integer literal makes tomllib raise a bare
        # ValueError (not TOMLDecodeError); it maps to a fail-closed StoreError -> CANNOT-EVALUATE, never an
        # uncaught crash out of resolve_store.
        n1_root = build_store(manifest="[opf]\nbig = " + "9" * 5000 + "\n")
        # MINOR-3: run the probe INSIDE _guard so a reverted fix (resolve_store no longer mapping the bare
        # ValueError from tomllib's int() to CANNOT-EVALUATE) yields a NAMED counted failure, not an uncaught
        # traceback that aborts the suite. Still passes only on CANNOT-EVALUATE (no weakened detection).
        check("new1-oversized-int-literal-cannot-eval",
              _guard(lambda: resolve_store(n1_root).status) == CANNOT_EVALUATE)

        # NEW-3: an OSError from _read_contained (e.g. a post-open fstat EIO) maps to a fail-closed StoreError
        # -> CANNOT-EVALUATE, never an uncaught OSError out of resolve_store.
        n3_root = build_store(manifest=manifest_text())
        _real_rc = _journal._read_contained
        def _rc_oserror(_rfd_arg, _rel_arg, **_kw):
            raise OSError(5, "injected EIO on read")
        _journal._read_contained = _rc_oserror
        try:
            # MINOR-3: _guard the probe so a reverted fix (resolve_store catching only JournalError, letting
            # the injected OSError escape) is a NAMED counted failure, not an uncaught traceback aborting the
            # suite. Still passes only on CANNOT-EVALUATE (no weakened detection).
            check("new3-read-oserror-cannot-eval",
                  _guard(lambda: resolve_store(n3_root).status) == CANNOT_EVALUATE)
        finally:
            _journal._read_contained = _real_rc

        # #378 P1, the r5, r6 and r7 legs' own descriptor checks: a leg probes and closes by number only what
        # it can show is still its own. Each open is recorded in a ledger with its (st_dev, st_ino), taken the
        # moment the open returns, while the number is still held, and every close the leg's stub sees marks
        # that open released. An open counts as left open only when no close released it AND fstat still
        # returns the identity recorded at it, and only such an open is closed. A released number, the
        # injected one included, is never probed or closed again, so a number another lane took meanwhile is
        # never touched. The leg's own directories are unique to it; the walk's ancestors (/ and the
        # directories above the leg's tree) are opened by other lanes too, so for them it is the ledger that
        # shows the number was never released. Each leg runs twice: with its closes releasing their numbers,
        # and with another lane's file put on a number instead (dup2 of the leg's "other-lane" file onto the
        # number, which the stub still holds, so it is never free for a real lane meanwhile): the r7 legs' on
        # the number whose close is injected, the r5 and r6 legs' on the number the leg checks. That move is
        # one-shot per recorded open: only the close that releases the open moves the other-lane file onto
        # its number, and any later close of the number reaches the real close, so a second close by the
        # code under test closes the other-lane file. After that run the number must still name the
        # other-lane file (nothing closed it again, the code under test and the leg's cleanup included), and
        # only then does the leg close it, its own other-lane descriptor. The ledger is pid-gated, as
        # _opf_emit's run_bounded legs' is: it records the pid that armed it, and only in that process does an
        # open record, a close release or fire the injection, or a left-open check find or close anything
        # (these legs do not fork; a forked child would hold its own copy of the numbers, never the leg's).
        def _r7_ident(fd):
            """fd's (st_dev, st_ino); None once fd is closed (EBADF, the only error read as closed). Any other
            fstat error returns a cannot-evaluate string naming fd, never None that a caller reads as released."""
            try:
                st = os.fstat(fd)
            except OSError as exc:
                if exc.errno == 9:                      # EBADF
                    return None
                return "cannot evaluate descriptor {}: {!r}".format(fd, exc)
            return st.st_dev, st.st_ino

        def _r7_ledger():
            """A leg's ledger, armed in this process: its opens, and the pid it acts in."""
            return {"owner": os.getpid(), "opens": []}

        def _r7_mine(seen):
            """Whether this is the process that armed the leg's ledger: only there does its close fire."""
            return os.getpid() == seen["opened"]["owner"]

        def _r7_opened(ledger, fd, real_close=None):
            """Record fd's open with its identity, in the ledger's own process only. An open stand-in passes
            real_close, so a failing fstat closes the number it just opened (never released yet) instead of
            leaking it."""
            if os.getpid() != ledger["owner"]:
                return fd                               # a forked child's open: not the leg's to record
            try:
                st = os.fstat(fd)
            except BaseException:
                if real_close is not None:
                    real_close(fd)
                raise
            ledger["opens"].append({"fd": fd, "ident": (st.st_dev, st.st_ino), "released": False})
            return fd

        def _r7_released(ledger, fd):
            """A close of fd in the ledger's own process: the latest open of fd that no close has released
            yet is released now. Returns whether this close released a recorded open (False for a repeated
            close of a released number, and for any close in another process)."""
            if os.getpid() != ledger["owner"]:
                return False                            # a forked child's close: never the leg's release
            for entry in reversed(ledger["opens"]):
                if entry["fd"] == fd and not entry["released"]:
                    entry["released"] = True
                    return True
            return False

        def _r7_left_open(ledger):
            """The opens no close released whose number still names the file recorded at the open, or whose
            number fstat cannot read (cannot-evaluate, never read as released: the entry is returned with the
            descriptor named under "unreadable"); none outside the ledger's own process."""
            if os.getpid() != ledger["owner"]:
                return []
            left = []
            for entry in ledger["opens"]:
                if entry["released"]:
                    continue
                ident = _r7_ident(entry["fd"])
                if isinstance(ident, str):
                    entry["unreadable"] = ident
                    left.append(entry)
                elif ident == entry["ident"]:
                    left.append(entry)
            return left

        def _r7_close_left(ledger):
            """Close each open _r7_left_open finds (a regressed run's leak), so the failing suite stays clean. A
            number fstat cannot read is never closed (its file is unknown) and fails the suite naming it."""
            for entry in _r7_left_open(ledger):
                if "unreadable" in entry:
                    failures.append("r7-left-open-cannot-evaluate: " + entry["unreadable"])
                    continue
                entry["released"] = True
                try:
                    os.close(entry["fd"])
                except OSError:
                    pass

        _r7_other_path = base / "r7-other-lane"
        _r7_other_path.write_bytes(b"")
        _r7_other_fd = os.open(str(_r7_other_path), os.O_RDONLY)
        _r7_other = _r7_ident(_r7_other_fd)

        # The left-open check fails closed: a recorded, unreleased descriptor still open whose fstat fails EIO
        # is returned naming it, never read as released. An independent fstat must still see it open, else
        # the check proves nothing.
        _r7_eio_ledger = _r7_ledger()
        _r7_eio_fd = _r7_opened(_r7_eio_ledger, os.open(os.devnull, os.O_RDONLY), os.close)
        _r7_eio_real_fstat = os.fstat

        def _r7_eio_fstat(fd, *args, **kwargs):
            if fd == _r7_eio_fd:
                raise OSError(5, "injected census read failure")
            return _r7_eio_real_fstat(fd, *args, **kwargs)
        os.fstat = _r7_eio_fstat
        try:
            _r7_eio_left = _r7_left_open(_r7_eio_ledger)
        finally:
            os.fstat = _r7_eio_real_fstat
        check("r7-left-open-census-eio-fails-closed",
              [entry["fd"] for entry in _r7_eio_left] == [_r7_eio_fd]
              and "descriptor {}:".format(_r7_eio_fd) in _r7_eio_left[0].get("unreadable", "")
              and _r7_ident(_r7_eio_fd) == _r7_eio_ledger["opens"][0]["ident"])
        os.close(_r7_eio_fd)

        def _r7_fire(fd, real_close, reuse):
            """The injected close: release fd, or under `reuse` put the other-lane file on it, then raise."""
            if reuse:
                os.dup2(_r7_other_fd, fd)       # the number goes straight to another lane's file
            else:
                real_close(fd)                  # released first, as close(2) does on Linux
            raise OSError(5, "injected close failure after release")

        def _r7_let_close(fd, real_close, reuse):
            """The close that releases the recorded open a leg checks: release fd, or under `reuse` put the
            other-lane file on it instead (the number goes straight to another lane), so the leg's check must
            leave it alone. A stub calls this once per recorded open (its _r7_released returned True); a later
            close of the number goes to the real close, so a repeated close closes the other-lane file."""
            if reuse:
                os.dup2(_r7_other_fd, fd)
            else:
                real_close(fd)

        def _r7_reuse_kept(fd):
            """Whether fd, after a `reuse` run, still names the other-lane file: then nothing closed it again,
            and the leg closes it, its own other-lane descriptor."""
            if fd is None or isinstance(_r7_other, str) or _r7_ident(fd) != _r7_other:
                return False
            os.close(fd)
            return True

        # ROUND-5 defect 3: _read_contained's cleanup closes the FILE fd and the PARENT fd each in its
        # own try/finally, so a file close that reports an error (an EIO surfaced at close) cannot leak
        # the parent descriptor. Inject the failure at the REAL close (the number is still released, as
        # on Linux), then prove on the recorded descriptor that the parent is released afterwards, by the
        # ledger and the identity recorded at its open; the reuse run puts the other-lane file on the
        # parent's number at its close, and on the FILE fd's number at the injected close (its first close
        # only, as for the parent), so a second close of either number reaches the real close and closes the
        # other-lane file, which the reused-number checks turn red.
        _r5_dir = base / "r5-close-leak"; _r5_dir.mkdir()
        (_r5_dir / "f").write_bytes(b"x = 1\n")
        _r5_root = os.open(str(_r5_dir), os.O_RDONLY | os.O_DIRECTORY)
        _r5_real_open_parent = _journal._open_parent
        _r5_real_close = os.close
        for _r5_reuse in (False, True):
            _r5_tag = "-reused" if _r5_reuse else ""
            _r5_seen = dict(opened=_r7_ledger())

            def _r5_open_parent(root_fd, relpath):
                pfd, name = _r5_real_open_parent(root_fd, relpath)
                _r5_seen["pfd"] = _r7_opened(_r5_seen["opened"], pfd, _r5_real_close)
                return pfd, name

            def _r5_close(fd):
                _r5_first = _r7_released(_r5_seen["opened"], fd)
                if "pfd" in _r5_seen and fd != _r5_seen["pfd"] and "fired" not in _r5_seen and _r7_mine(_r5_seen):
                    # the first non-parent close after _open_parent returned is the FILE fd's close
                    _r5_seen["fired"] = True
                    _r5_seen["ffd"] = fd
                    _r7_fire(fd, _r5_real_close, _r5_reuse)
                if fd == _r5_seen.get("pfd") and _r5_first:
                    _r7_let_close(fd, _r5_real_close, _r5_reuse)   # the parent, which the leg checks
                    return
                _r5_real_close(fd)

            _journal._open_parent = _r5_open_parent
            os.close = _r5_close
            try:
                try:
                    _journal._read_contained(_r5_root, "f")
                    _r5_out = "returned"
                except (OSError, _journal.JournalError):
                    _r5_out = "raised"
            finally:
                os.close = _r5_real_close
                _journal._open_parent = _r5_real_open_parent
            check("r5-file-close-injection-fired" + _r5_tag, _r5_seen.get("fired") is True and "pfd" in _r5_seen)
            check("r5-file-close-error-propagates" + _r5_tag, _r5_out == "raised")
            check("r5-parent-fd-closed-after-file-close-error" + _r5_tag, not _r7_left_open(_r5_seen["opened"]))
            _r7_close_left(_r5_seen["opened"])        # a pre-fix run leaks it; closed so the suite stays clean
            if _r5_reuse:
                check("r5-parent-reused-number-never-closed", _r7_reuse_kept(_r5_seen.get("pfd")))
                check("r5-file-reused-number-never-closed", _r7_reuse_kept(_r5_seen.get("ffd")))
        os.close(_r5_root)

        # ROUND-6 defect (K9a fix 6): _open_working_dir_fd returned the freshly-opened `.working`
        # descriptor from inside a try whose finally closes the parent, so a parent close that reports
        # an error (the number still released, as on Linux) abandoned the return and leaked the child:
        # one descriptor per discovery/resolution/planner-investigation call. Inject the failure at the
        # REAL parent close; the error still propagates fail-closed, and the child descriptor -- recorded
        # at its open in the ledger with its identity -- is proven released afterwards; the reuse run puts
        # the other-lane file on the child's number at its close.
        _r6 = build_store(manifest=manifest_text())
        _r6_root = os.open(str(_r6), os.O_RDONLY | os.O_DIRECTORY)
        _r6_real_open_parent = _journal._open_parent
        _r6_real_open = os.open
        _r6_real_close = os.close
        for _r6_reuse in (False, True):
            _r6_tag = "-reused" if _r6_reuse else ""
            _r6_seen = dict(opened=_r7_ledger())

            def _r6_open_parent(root_fd, relpath):
                pfd, name = _r6_real_open_parent(root_fd, relpath)
                _r6_seen["pfd"] = _r7_opened(_r6_seen["opened"], pfd, _r6_real_close)
                return pfd, name

            def _r6_open(*args, **kwargs):
                fd = _r6_real_open(*args, **kwargs)
                if kwargs.get("dir_fd") is not None and kwargs.get("dir_fd") == _r6_seen.get("pfd"):
                    _r6_seen["wfd"] = _r7_opened(_r6_seen["opened"], fd, _r6_real_close)   # the child
                return fd

            def _r6_close(fd):
                _r6_first = _r7_released(_r6_seen["opened"], fd)
                if fd == _r6_seen.get("pfd") and "fired" not in _r6_seen and _r7_mine(_r6_seen):
                    _r6_seen["fired"] = True
                    _r7_fire(fd, _r6_real_close, False)
                if fd == _r6_seen.get("wfd") and _r6_first:
                    _r7_let_close(fd, _r6_real_close, _r6_reuse)   # the child, which the leg checks
                    return
                _r6_real_close(fd)

            _journal._open_parent = _r6_open_parent
            os.open = _r6_open
            os.close = _r6_close
            try:
                try:
                    _open_working_dir_fd(_r6_root, WORKING_DIRNAME)
                    _r6_out = "returned"
                except OSError:
                    _r6_out = "raised"
            finally:
                os.close = _r6_real_close
                os.open = _r6_real_open
                _journal._open_parent = _r6_real_open_parent
            check("r6-working-parent-close-injection-fired" + _r6_tag,
                  _r6_seen.get("fired") is True and "wfd" in _r6_seen)
            check("r6-working-parent-close-error-propagates" + _r6_tag, _r6_out == "raised")
            check("r6-working-fd-closed-after-parent-close-error" + _r6_tag, not _r7_left_open(_r6_seen["opened"]))
            _r7_close_left(_r6_seen["opened"])        # a pre-fix run leaks it; closed so the suite stays clean
            if _r6_reuse:
                check("r6-working-child-reused-number-never-closed", _r7_reuse_kept(_r6_seen.get("wfd")))
        os.close(_r6_root)

        # ROUND-6 sibling: open_journal_root_from_path returned the freshly-opened journal-root
        # descriptor from inside a try whose finally closes the operator-root fd, the same
        # abandoned-return shape. Inject the failure at the REAL root close; the error still propagates
        # fail-closed and the jr descriptor -- recorded at its open in the ledger with its identity -- is
        # proven released afterwards; the reuse run puts the other-lane file on jr's number at its close.
        _r6j = base / "r6-journal-close-leak"
        (_r6j / "j" / "r").mkdir(parents=True)
        _r6j_real_odc = _journal._open_dir_contained
        for _r6j_reuse in (False, True):
            _r6j_tag = "-reused" if _r6j_reuse else ""
            _r6j_seen = dict(opened=_r7_ledger())

            def _r6j_odc(root_fd, relpath):
                _r6j_seen["root_fd"] = _r7_opened(_r6j_seen["opened"], root_fd)
                fd = _r6j_real_odc(root_fd, relpath)
                _r6j_seen["jr"] = _r7_opened(_r6j_seen["opened"], fd, _r6_real_close)
                return fd

            def _r6j_close(fd):
                _r6j_first = _r7_released(_r6j_seen["opened"], fd)
                if fd == _r6j_seen.get("root_fd") and "fired" not in _r6j_seen and _r7_mine(_r6j_seen):
                    _r6j_seen["fired"] = True
                    _r7_fire(fd, _r6_real_close, False)
                if fd == _r6j_seen.get("jr") and _r6j_first:
                    _r7_let_close(fd, _r6_real_close, _r6j_reuse)   # the journal root, which the leg checks
                    return
                _r6_real_close(fd)

            _journal._open_dir_contained = _r6j_odc
            os.close = _r6j_close
            try:
                try:
                    _journal.open_journal_root_from_path(_r6j, "j/r")
                    _r6j_out = "returned"
                except OSError:
                    _r6j_out = "raised"
            finally:
                os.close = _r6_real_close
                _journal._open_dir_contained = _r6j_real_odc
            check("r6-journal-root-close-injection-fired" + _r6j_tag,
                  _r6j_seen.get("fired") is True and "jr" in _r6j_seen)
            check("r6-journal-root-close-error-propagates" + _r6j_tag, _r6j_out == "raised")
            check("r6-journal-fd-closed-after-root-close-error" + _r6j_tag, not _r7_left_open(_r6j_seen["opened"]))
            _r7_close_left(_r6j_seen["opened"])       # a pre-fix run leaks it; closed so the suite stays clean
            if _r6j_reuse:
                check("r6-journal-root-reused-number-never-closed", _r7_reuse_kept(_r6j_seen.get("jr")))

        # ROUND-7 (codex), under P1 (#378): a parent close in the no-follow walk that reports an error
        # (EINTR/EIO) must still propagate it fail-closed and leak nothing the walk opened. Each close is
        # a single _journal._close_fd_propagating (a bare os.close at #378); close(2) on Linux has released
        # the number by the time it reports the error, so the helper never touches it again. Inject that
        # shape: a one-shot close that RELEASES the number (in the reuse run, puts the other-lane file on
        # it instead) and then raises; the error still propagates fail-closed and NO descriptor the walk
        # opened survives. The one-close check (#377) counts what the injected number sees after its failed
        # close: every later os.close of it and every os.fstat of it (PROBE), until an open hands the number
        # out again; both must be zero. Two flips put a pre-P1 body back as _journal._close_fd_propagating
        # for a reuse run: RECLOSE (fstat, then close again) must turn the one-close check red with exactly
        # one more close, and the reused-number check red; PROBE (fstat after the failed close, no second
        # close) must turn the one-close check red by its fstat count alone, the other-lane file kept.
        _r7 = base / "r7-released-close"
        (_r7 / "d").mkdir(parents=True)
        _r7_real_open = os.open
        _r7_real_close = os.close
        _r7_real_fstat = os.fstat
        _r7_real_propagating = _journal._close_fd_propagating

        def _r7_flip_reclose(fd):
            """RECLOSE: the pre-P1 _journal._close_fd_propagating, its fstat-then-reclose recovery inlined."""
            try:
                os.close(fd)
                return
            except OSError as exc:
                first = exc
            try:
                os.fstat(fd)
            except OSError:
                raise first
            try:
                os.close(fd)                    # the retry close(2) warns against
            except OSError:
                pass
            raise first

        def _r7_flip_probe(fd):
            """PROBE: one close, then an fstat of the released number when it fails; never a second close."""
            try:
                os.close(fd)
                return
            except OSError as exc:
                first = exc
            try:
                os.fstat(fd)
            except OSError:
                pass
            raise first

        def _r7_after(seen, fd, what):
            """Count a close ("closes") or fstat ("probes") of the injected number after its failed close."""
            if seen.get("fired") is not None and fd == seen["fired"] and not seen["reopened"] and _r7_mine(seen):
                seen[what] += 1

        def _r7_one_close(seen):
            """The one-close check: the injection fired and its number saw no further close and no fstat."""
            return seen.get("fired") is not None and not seen["closes"] and not seen["probes"]

        def _r7_flip_red(seen, flip, kept):
            """A flipped reuse run is red by the flip's own assertions, every other assertion still green."""
            if seen.get("fired") is None or seen["out"] != "raised" or _r7_left_open(seen["opened"]):
                return False
            if flip == "reclose":
                return seen["closes"] == 1 and seen["probes"] >= 1 and not kept
            return not seen["closes"] and seen["probes"] >= 1 and kept

        def _r7_nofollow_run(reuse, flip=None):
            seen = {"opened": _r7_ledger(), "closes": 0, "probes": 0, "reopened": False}

            def _r7_open(*args, **kwargs):
                fd = _r7_real_open(*args, **kwargs)
                if fd == seen.get("fired"):
                    seen["reopened"] = True     # the number is handed out again: no longer the injected one
                return _r7_opened(seen["opened"], fd, _r7_real_close)

            def _r7_close(fd):
                _r7_after(seen, fd, "closes")
                _r7_released(seen["opened"], fd)
                if "fired" not in seen and _r7_mine(seen):
                    seen["fired"] = fd
                    _r7_fire(fd, _r7_real_close, reuse)
                _r7_real_close(fd)

            def _r7_fstat(fd, *args, **kwargs):
                _r7_after(seen, fd, "probes")
                return _r7_real_fstat(fd, *args, **kwargs)

            os.open, os.close, os.fstat = _r7_open, _r7_close, _r7_fstat
            if flip is not None:
                _journal._close_fd_propagating = flip
            try:
                try:
                    _open_dir_nofollow(str(_r7 / "d"))
                    seen["out"] = "returned"
                except OSError:
                    seen["out"] = "raised"
            finally:
                _journal._close_fd_propagating = _r7_real_propagating
                os.open, os.close, os.fstat = _r7_real_open, _r7_real_close, _r7_real_fstat
            return seen

        for _r7_reuse in (False, True):
            _r7_tag = "-reused" if _r7_reuse else ""
            _r7_seen = _r7_nofollow_run(_r7_reuse)
            check("r7-nofollow-released-close-injection-fired" + _r7_tag, "fired" in _r7_seen)
            check("r7-nofollow-released-close-error-propagates" + _r7_tag, _r7_seen["out"] == "raised")
            check("r7-nofollow-released-close-one-close" + _r7_tag, _r7_one_close(_r7_seen))
            check("r7-nofollow-no-walk-descriptor-survives-released-close" + _r7_tag,
                  not _r7_left_open(_r7_seen["opened"]))
            _r7_close_left(_r7_seen["opened"])
            if _r7_reuse:
                check("r7-nofollow-reused-number-never-closed", _r7_reuse_kept(_r7_seen.get("fired")))
        for _r7_flip, _r7_flip_body in (("reclose", _r7_flip_reclose), ("probe", _r7_flip_probe)):
            _r7_seen = _r7_nofollow_run(True, _r7_flip_body)
            _r7_one = _r7_one_close(_r7_seen)
            _r7_red = _r7_flip_red(_r7_seen, _r7_flip, _r7_reuse_kept(_r7_seen.get("fired")))
            _r7_close_left(_r7_seen["opened"])
            check("r7-nofollow-released-close-one-close-red-under-" + _r7_flip, not _r7_one and _r7_red)

        # ROUND-7 sibling on _open_working_dir_fd: the parent close raising after releasing its number
        # (close(2) on Linux; P1, #378) must leave neither the parent nor the held `.working` child open
        # when the error propagates fail-closed.
        _r7w = build_store(manifest=manifest_text())
        _r7w_root = os.open(str(_r7w), os.O_RDONLY | os.O_DIRECTORY)
        _r7w_real_open_parent = _journal._open_parent
        _r7w_real_open, _r7w_real_close, _r7w_real_fstat = os.open, os.close, os.fstat
        for _r7_reuse in (False, True):
            _r7_tag = "-reused" if _r7_reuse else ""
            _r7w_seen = {"opened": _r7_ledger()}

            def _r7w_open_parent(root_fd, relpath):
                pfd, name = _r7w_real_open_parent(root_fd, relpath)
                _r7w_seen["pfd"] = _r7_opened(_r7w_seen["opened"], pfd)
                return pfd, name

            def _r7w_open(*args, **kwargs):
                fd = _r7w_real_open(*args, **kwargs)
                if kwargs.get("dir_fd") is not None and kwargs.get("dir_fd") == _r7w_seen.get("pfd"):
                    _r7w_seen["wfd"] = _r7_opened(_r7w_seen["opened"], fd, _r7w_real_close)   # the child
                return fd

            def _r7w_close(fd):
                _r7_released(_r7w_seen["opened"], fd)
                if fd == _r7w_seen.get("pfd") and "fired" not in _r7w_seen and _r7_mine(_r7w_seen):
                    _r7w_seen["fired"] = True
                    _r7_fire(fd, _r7w_real_close, _r7_reuse)
                _r7w_real_close(fd)

            _journal._open_parent = _r7w_open_parent
            os.open = _r7w_open
            os.close = _r7w_close
            try:
                try:
                    _open_working_dir_fd(_r7w_root, WORKING_DIRNAME)
                    _r7w_out = "returned"
                except OSError:
                    _r7w_out = "raised"
            finally:
                os.close = _r7w_real_close
                os.open = _r7w_real_open
                _journal._open_parent = _r7w_real_open_parent
            check("r7-working-released-close-injection-fired" + _r7_tag,
                  _r7w_seen.get("fired") is True and "wfd" in _r7w_seen)
            check("r7-working-released-close-error-propagates" + _r7_tag, _r7w_out == "raised")
            check("r7-working-no-descriptor-survives-released-close" + _r7_tag,
                  not _r7_left_open(_r7w_seen["opened"]))
            _r7_close_left(_r7w_seen["opened"])
            if _r7_reuse:
                check("r7-working-reused-number-never-closed", _r7_reuse_kept(_r7w_seen.get("pfd")))

        # The same _open_working_dir_fd parent close under #377's own V1 vector. Its parent close is
        # _close_fd_exc_safe's (#377). V1, deterministic reuse: the injected parent close really releases
        # the number, dup2s an unrelated file onto it, and only then raises EINTR (and, separately, EIO). The
        # error must propagate fail-closed (RULE) with the held `.working` child released (LEAK); the parent
        # number must see exactly one os.close and no os.fstat afterwards (PROBE); and the unrelated
        # descriptor must keep its (st_dev, st_ino) (REUSE). The RECLOSE flip puts the old fstat-then-reclose
        # body back as _close_fd_exc_safe and must turn the vector red by REUSE.
        (base / "r7-unrelated").write_bytes(b"unrelated")
        _r7w_p1 = _close_fd_exc_safe

        def _r7w_reclose(fd):
            """RECLOSE: the pre-P1 _close_fd_exc_safe, its confirm-then-reclose recovery inlined."""
            tb = sys.exc_info()[2]
            quiet = tb is not None and tb.tb_frame is sys._getframe(1)
            try:
                os.close(fd)
                return
            except OSError as exc:
                first = exc
            try:
                os.fstat(fd)
            except OSError:
                if quiet:
                    return
                raise first
            try:
                os.close(fd)
            except OSError:
                pass
            if not quiet:
                raise first

        def _r7w_vector(err, census=None, leak=False):
            """The failed tags of one V1 run (empty: green). `census` replaces the leak check's fstat of the
            child, and `leak` keeps the child's close from releasing it (a regressed body), so a case can
            meet a still-open child whose census read fails."""
            seen = dict(closes=0, probes=0)
            other = _r7w_real_open(str(base / "r7-unrelated"), os.O_RDONLY)
            want = _r7w_real_fstat(other)

            def open_parent(root_fd, relpath):
                pfd, name = _r7w_real_open_parent(root_fd, relpath)
                seen["pfd"] = pfd
                return pfd, name

            def open_(*args, **kwargs):
                fd = _r7w_real_open(*args, **kwargs)
                if kwargs.get("dir_fd") is not None and kwargs.get("dir_fd") == seen.get("pfd"):
                    seen["wfd"] = fd          # the child opened beneath the recorded parent
                return fd

            def close(fd):
                if leak and fd == seen.get("wfd"):
                    return                    # the regressed body: the child is never released
                if fd == seen.get("pfd"):
                    seen["closes"] += 1
                    if "fired" not in seen:
                        seen["fired"] = OSError(err, "injected released-close failure; number reused")
                        _r7w_real_close(fd)
                        os.dup2(other, fd)    # an unrelated file takes the freed number
                        raise seen["fired"]
                _r7w_real_close(fd)

            def fstat(fd, *args, **kwargs):
                if fd == seen.get("pfd") and "fired" in seen:
                    seen["probes"] += 1
                return _r7w_real_fstat(fd, *args, **kwargs)

            got = None
            _journal._open_parent = open_parent
            os.open, os.close, os.fstat = open_, close, fstat
            try:
                _open_working_dir_fd(_r7w_root, WORKING_DIRNAME)
            except OSError as exc:
                got = exc
            finally:
                os.open, os.close, os.fstat = _r7w_real_open, _r7w_real_close, _r7w_real_fstat
                _journal._open_parent = _r7w_real_open_parent
            failed = []
            if "fired" not in seen or "wfd" not in seen:
                failed.append("injection")
            elif got is not seen["fired"]:
                failed.append("rule")
            if seen["closes"] != 1 or seen["probes"]:
                failed.append("probe")
            if "fired" in seen:
                try:
                    now = _r7w_real_fstat(seen["pfd"])
                except OSError:
                    failed.append("reuse")    # the unrelated descriptor was closed
                else:
                    if (now.st_dev, now.st_ino) != (want.st_dev, want.st_ino):
                        failed.append("reuse")    # another file holds the number now: never closed here
                    else:
                        _r7w_real_close(seen["pfd"])   # still the unrelated file this vector put there
            if "wfd" in seen:
                try:
                    (census or _r7w_real_fstat)(seen["wfd"])
                except OSError as exc:
                    if exc.errno != 9:        # only EBADF reads as closed; any other error fails naming it
                        failed.append("census-cannot-evaluate-fd-{}".format(seen["wfd"]))
                else:
                    failed.append("leak")
                    _r7w_real_close(seen["wfd"])
            _r7w_real_close(other)
            return failed

        for _r7w_err, _r7w_tag in ((4, "eintr"), (5, "eio")):
            check("r7-working-p1-one-close-no-probe-reuse-" + _r7w_tag, _r7w_vector(_r7w_err) == [])
            globals()["_close_fd_exc_safe"] = _r7w_reclose
            try:
                _r7w_red = _r7w_vector(_r7w_err)
            finally:
                globals()["_close_fd_exc_safe"] = _r7w_p1
            check("r7-working-reclose-flip-red-by-reuse-" + _r7w_tag,
                  "reuse" in _r7w_red and set(_r7w_red) <= set(("reuse", "probe")))
        # The leak check fails closed: a child left open whose census read fails EIO fails the vector naming
        # it, never read as closed. An independent fstat must still see it open, else the case proves nothing.
        _r7w_eio = []

        def _r7w_census(fd, *args, **kwargs):
            _r7w_eio.append(fd)
            raise OSError(5, "injected census read failure")
        _r7w_eio_got = _r7w_vector(5, _r7w_census, leak=True)
        try:
            _r7w_eio_open = bool(_r7w_eio) and _r7w_real_fstat(_r7w_eio[0]) is not None
        except OSError:
            _r7w_eio_open = False
        check("r7-working-leak-census-eio-fails-closed",
              _r7w_eio_open and _r7w_eio_got == ["census-cannot-evaluate-fd-{}".format(_r7w_eio[0])])
        if _r7w_eio_open:
            _r7w_real_close(_r7w_eio[0])      # the child the regressed body left open
        os.close(_r7w_root)

        # ROUND-7 sibling on open_journal_root_from_path: the operator-root close raising after releasing
        # its number (close(2) on Linux; P1, #378) must leave neither the root nor the held journal-root fd
        # open when the error propagates. The root close is _journal._close_fd_yielding's, which on this
        # normal path is the bare os.close of _journal._close_fd_propagating; the one-close check and its
        # RECLOSE and PROBE flips are the no-follow leg's above, applied to the root number.
        _r7j = base / "r7-journal-released-close"
        (_r7j / "j" / "r").mkdir(parents=True)
        _r7j_real_odc = _journal._open_dir_contained
        _r7j_real_close = os.close

        def _r7j_run(reuse, flip=None):
            seen = {"opened": _r7_ledger(), "closes": 0, "probes": 0, "reopened": False}

            def _r7j_odc(root_fd, relpath):
                seen["root_fd"] = _r7_opened(seen["opened"], root_fd)
                fd = _r7j_real_odc(root_fd, relpath)
                seen["jr"] = _r7_opened(seen["opened"], fd)
                return fd

            def _r7j_open(*args, **kwargs):
                fd = _r7_real_open(*args, **kwargs)
                if fd == seen.get("fired"):
                    seen["reopened"] = True     # the number is handed out again: no longer the injected one
                return fd

            def _r7j_close(fd):
                _r7_after(seen, fd, "closes")
                _r7_released(seen["opened"], fd)
                if fd == seen.get("root_fd") and "fired" not in seen and _r7_mine(seen):
                    seen["fired"] = fd
                    _r7_fire(fd, _r7j_real_close, reuse)
                _r7j_real_close(fd)

            def _r7j_fstat(fd, *args, **kwargs):
                _r7_after(seen, fd, "probes")
                return _r7_real_fstat(fd, *args, **kwargs)

            _journal._open_dir_contained = _r7j_odc
            os.open, os.close, os.fstat = _r7j_open, _r7j_close, _r7j_fstat
            if flip is not None:
                _journal._close_fd_propagating = flip
            try:
                try:
                    _journal.open_journal_root_from_path(_r7j, "j/r")
                    seen["out"] = "returned"
                except OSError:
                    seen["out"] = "raised"
            finally:
                _journal._close_fd_propagating = _r7_real_propagating
                os.open, os.close, os.fstat = _r7_real_open, _r7j_real_close, _r7_real_fstat
                _journal._open_dir_contained = _r7j_real_odc
            return seen

        for _r7_reuse in (False, True):
            _r7_tag = "-reused" if _r7_reuse else ""
            _r7j_seen = _r7j_run(_r7_reuse)
            check("r7-journal-released-close-injection-fired" + _r7_tag,
                  _r7j_seen.get("fired") is not None and "jr" in _r7j_seen)
            check("r7-journal-released-close-error-propagates" + _r7_tag, _r7j_seen["out"] == "raised")
            check("r7-journal-released-close-one-close" + _r7_tag, _r7_one_close(_r7j_seen))
            check("r7-journal-no-descriptor-survives-released-close" + _r7_tag,
                  not _r7_left_open(_r7j_seen["opened"]))
            _r7_close_left(_r7j_seen["opened"])
            if _r7_reuse:
                check("r7-journal-reused-number-never-closed", _r7_reuse_kept(_r7j_seen.get("fired")))
        for _r7_flip, _r7_flip_body in (("reclose", _r7_flip_reclose), ("probe", _r7_flip_probe)):
            _r7j_seen = _r7j_run(True, _r7_flip_body)
            _r7_one = _r7_one_close(_r7j_seen)
            _r7_red = _r7_flip_red(_r7j_seen, _r7_flip, _r7_reuse_kept(_r7j_seen.get("fired")))
            _r7_close_left(_r7j_seen["opened"])
            check("r7-journal-released-close-one-close-red-under-" + _r7_flip, not _r7_one and _r7_red)
        os.close(_r7_other_fd)

        # ROUND-5 defect 1: the descriptor-bound resolver matches resolve_store over a held root. The
        # default store resolves with the manifest's EXACT bytes (read through the .working listing
        # descriptor), an empty root is NOT-ADOPTED, and a companion pointer (a store at another root)
        # is refused fail-closed: it cannot be bound to the held descriptor (the planner refuses such
        # pointers before resolving; path-based callers keep resolve_store).
        _r5a = build_store(manifest=manifest_text())
        _r5a_fd = os.open(str(_r5a), os.O_RDONLY | os.O_DIRECTORY)
        try:
            _r5a_res, _r5a_raw = resolve_store_fd(_r5a_fd, _r5a)
        finally:
            os.close(_r5a_fd)
        check("r5-fd-default-resolved", _r5a_res.status == RESOLVED
              and _r5a_res.machine_rel == "{}/{}".format(WORKING_DIRNAME, DEFAULT_MACHINE_SUBDIR))
        check("r5-fd-manifest-bytes-exact",
              _r5a_raw == (_r5a / WORKING_DIRNAME / DEFAULT_MACHINE_SUBDIR / MANIFEST_NAME).read_bytes())
        _r5b = build_store(make_working=False)
        _r5b_fd = os.open(str(_r5b), os.O_RDONLY | os.O_DIRECTORY)
        try:
            _r5b_res, _r5b_raw = resolve_store_fd(_r5b_fd, _r5b)
        finally:
            os.close(_r5b_fd)
        check("r5-fd-empty-not-adopted", _r5b_res.status == NOT_ADOPTED and _r5b_raw is None)
        _r5c = build_store(make_working=False,
                           pointer='[store]\ntarget = "dir:{}"\n'.format(_r5a))
        _r5c_fd = os.open(str(_r5c), os.O_RDONLY | os.O_DIRECTORY)
        try:
            _r5c_res, _r5c_raw = resolve_store_fd(_r5c_fd, _r5c)
        finally:
            os.close(_r5c_fd)
        check("r5-fd-companion-pointer-refused",
              _r5c_res.status == CANNOT_EVALUATE and "outside the held product root" in _r5c_res.detail
              and _r5c_raw is None)

        # NEW-4: an oversized-int key rendered into a finding message no longer crashes the validator: the
        # unknown-key idiom (_sorted_key_names) and the duplicate-namespace message both render through
        # _safe_str. TOML keys are always strings, so these reach the validator only from a hand-built control
        # dict (an injected-boundary hardening).
        _n4a = _t.loads(manifest_text()); _n4a[10 ** 5000] = dict(x=1)
        check("new4-oversized-toplevel-key-invalid", validate_manifest(_n4a).status == INVALID)
        _n4b = _t.loads(manifest_text())
        _n4tk = dict(); _n4tk[10 ** 5000] = dict(namespace="BI"); _n4tk["backlog_item"] = dict(namespace="BI")
        _n4b["types"] = _n4tk
        check("new4-oversized-dup-ns-key-invalid", validate_manifest(_n4b).status == INVALID)

        # ROUND-6 codex: _safe_str (and _sorted_key_names, which renders each key through it) must ESCAPE
        # control characters so an untrusted key/value cannot forge a second diagnostic line ('x\nFORGED');
        # a printable value stays byte-for-byte. Pre-fix _safe_str returned str(value) verbatim and leaked
        # the literal newline. (_safe_display already escapes via repr(); this is its str()-based sibling.)
        _r6s = _safe_str("x" + chr(10) + "FORGED")
        check("r6-safe-str-escapes-newline", chr(10) not in _r6s and "\\x0a" in _r6s)
        check("r6-safe-str-printable-unchanged", _safe_str("1.2.3") == "1.2.3" and _safe_str(41) == "41")
        _r6z = _safe_str("a" + chr(0x200b) + "b")
        check("r6-safe-str-escapes-zero-width", chr(0x200b) not in _r6z and "\\u200b" in _r6z)
        # _sorted_key_names renders each key through _safe_str (return sorted(_safe_str(k) for k in keys)),
        # so the escaping above holds for the unknown-key idiom by construction; asserting _safe_str is the
        # class-root check (a direct _sorted_key_names call here is deliberately avoided so the opf-fuzz
        # call-site coverage scan does not count a self-test-only site the fuzz tracer never exercises).
        check("r6-safe-str-controls-composed",
              all(chr(10) not in _safe_str(k) for k in ("a" + chr(10) + "b", "plain")))

        # Deep, wide and long values: _safe_display and _safe_str are bounded and never raise, and within the
        # bounds they spell a value as repr() does. A table nested 100,000 levels deep (what tomllib builds from
        # a 100,000-part dotted header) runs in a bounded child, so a RecursionError ("Stack overflow ... while
        # getting the repr of an object") that escapes a renderer is the ERROR:RecursionError sentinel, never a
        # crash of this suite. Each check and a faulty renderer that turns it red: deep-table-bounded, one that
        # calls repr() on the table (the RecursionError, a RuntimeError, renders the placeholder
        # <unrenderable-value>) or has no depth bound (no depth marker then); wide-array-bounded and
        # long-str-bounded, one that never cuts at _DISPLAY_MAX_CHARS characters; small-values-byte-identical
        # (a value within the bounds whose repr() is at most _DISPLAY_FULL_MAX_CHARS characters renders as
        # repr(), uncut, and a str whose escaped form is that short as the escaped str()), one that spells a
        # supported value otherwise, a one-member tuple without its comma for instance;
        # depth-at-bound-byte-identical, a depth bound one level lower; depth-past-bound-marker, a depth bound
        # one level higher. A value past a limit renders in the bounded form by design (see _safe_display), so
        # a message is byte-identical to its earlier form only when every value it renders is within them.
        _dr_cap = _DISPLAY_MAX_CHARS + _DISPLAY_MARKER_MAX

        def _dr_deep_render():
            deep = cur = {}
            for _ in range(100000):
                cur["a"] = {}
                cur = cur["a"]
            shown, text = _safe_display(deep), _safe_str(deep)
            return "{}|{}".format(len(shown) <= _dr_cap and "nested deeper than" in shown,
                                  len(text) <= _dr_cap and "nested deeper than" in text)
        check("deeprender-deep-table-bounded", run_bounded(_dr_deep_render, timeout_s=60) == "True|True")
        _dr_wide = list(range(1000000))
        check("deeprender-wide-array-bounded",
              len(_safe_display(_dr_wide)) == _DISPLAY_MAX_CHARS + len("...<truncated>")
              and _safe_display(_dr_wide).endswith("...<truncated>")
              and _safe_str(_dr_wide) == _safe_display(_dr_wide))
        _dr_long = "x" * 1000000
        check("deeprender-long-str-bounded",
              _safe_display(_dr_long) == chr(39) + "x" * (_DISPLAY_MAX_CHARS - 1) + "...<str of 1000000 chars>"
              and _safe_str(_dr_long) == "x" * _DISPLAY_MAX_CHARS + "...<str of 1000000 chars>")
        _dr_hour = datetime.timedelta(hours=5)
        _dr_small = [None, True, 7, 1.5, "BI-1", "x" + chr(10) + "y", [1, "x", {"a": 2}], {"k": (1,)}, (), (1,),
                     set(), frozenset(), {3}, frozenset({4}), {"a": [[[]]]}, "x" * 1000, b"a" + bytes(2),
                     datetime.datetime(2026, 1, 2, 3, 4, 5, tzinfo=datetime.timezone.utc), datetime.time(1, 2, 3),
                     datetime.date(2026, 1, 2), datetime.datetime(2026, 1, 2, tzinfo=datetime.timezone(-_dr_hour))]
        check("deeprender-small-values-byte-identical",
              all(_safe_display(v) == repr(v) for v in _dr_small)
              and all(_safe_str(v) == _escape_line_unsafe(str(v)) for v in _dr_small))
        _dr_nest = _dr_cur = []
        for _ in range(_DISPLAY_MAX_DEPTH - 1):
            _dr_cur.append([])
            _dr_cur = _dr_cur[0]
        check("deeprender-depth-at-bound-byte-identical", _safe_display(_dr_nest) == repr(_dr_nest))
        check("deeprender-depth-past-bound-marker", _safe_display([_dr_nest]) == "[" * _DISPLAY_MAX_DEPTH
              + "<list nested deeper than {} levels>".format(_DISPLAY_MAX_DEPTH) + "]" * _DISPLAY_MAX_DEPTH)
        # The ESCAPED output is bounded exactly. Past the width limit (_DISPLAY_FULL_MAX_CHARS
        # characters of repr() or of escaped str()), content is cut on a whole escape at _DISPLAY_MAX_CHARS
        # characters, never splitting one, and followed by its marker (each marker is measured on renderer
        # output by deeprender-marker-max-is-the-longest-marker, below); at the width limit the escaped form is
        # whole, and one character more is cut. Each input is past the width limit (16,385 NULs escape to
        # 65,540 characters, 6,554 astral characters to 65,540). The escaped-str check is red on a renderer that
        # escapes after its cut, without a budget, and the whole-escape check on one whose cut splits an escape
        # (each piece it cuts is already a repr(), whose escapes are printable text).
        _dr_nul, _dr_astral = chr(0) * 16385, chr(0x1000C) * 6554
        check("deeprender-escaped-str-bound-exact",
              _safe_str(_dr_nul) == "\\x00" * 256 + "...<str of 16385 chars>"
              and _safe_str(_dr_astral) == "\\U0001000c" * 102 + "...<str of 6554 chars>"
              and _safe_str(chr(10) * 20000) == "\\x0a" * 256 + "...<str of 20000 chars>"
              and _safe_display(_dr_nul) == chr(39) + "\\x00" * 255 + "...<str of 16385 chars>"
              and _safe_str(chr(0) * 16384) == "\\x00" * 16384
              and _safe_display("x" * (_DISPLAY_FULL_MAX_CHARS - 2)) == repr("x" * (_DISPLAY_FULL_MAX_CHARS - 2))
              and _safe_display("x" * (_DISPLAY_FULL_MAX_CHARS - 1))
              == chr(39) + "x" * (_DISPLAY_MAX_CHARS - 1) + "...<str of 65535 chars>"
              and all(len(f(v)) <= _dr_cap for f in (_safe_display, _safe_str)
                      for v in (_dr_nul, _dr_astral, chr(0x2028) * 11000, bytes(16384), [_dr_astral] * 4)))
        check("deeprender-cut-on-whole-escape",
              _safe_display([chr(0x1000C) * 6554]) == "[" + chr(39) + "\\U0001000c" * 102 + "...<truncated>"
              and _safe_str([bytes(1024), "x" * _DISPLAY_FULL_MAX_CHARS])
              == "[b" + chr(39) + "\\x00" * 255 + "...<truncated>")
        # Every supported scalar is sized BEFORE it is converted, so rendering a bytes or an int far
        # over the bound neither builds its repr() nor allocates in proportion to it (a convert-first renderer
        # allocated about 2 MB for a 1,000,000-byte value, and about 1.7 MB for a 100,000-digit hexadecimal int
        # under a lifted integer-string limit). The traced peak covers the renders only. With the limit
        # lifted neither value trips it, and each is past the width limit, so the pair renders in the bounded
        # form with each size marker in place (an int that trips the limit makes its container
        # <oversized-value> instead, checked next).
        import tracemalloc
        _dr_bytes = b"x" * 1000000
        _dr_idlimit = sys.get_int_max_str_digits()
        sys.set_int_max_str_digits(0)
        _dr_trace = not tracemalloc.is_tracing()
        if _dr_trace:
            tracemalloc.start()
        try:
            _dr_hex = int("f" * 100000, 16)
            tracemalloc.reset_peak()
            _dr_base = tracemalloc.get_traced_memory()[0]
            _dr_sized = (_safe_display(_dr_bytes), _safe_str(_dr_bytes), _safe_display(_dr_hex),
                         _safe_str(_dr_hex), _safe_display([_dr_hex, _dr_bytes]))
            _dr_peak = tracemalloc.get_traced_memory()[1] - _dr_base
        finally:
            if _dr_trace:
                tracemalloc.stop()
            sys.set_int_max_str_digits(_dr_idlimit)
        check("deeprender-scalars-sized-before-repr",
              _dr_sized == ("<oversized-bytes: 1000000 bytes>",) * 2 + ("<oversized-int: 400000 bits>",) * 2
              + ("[<oversized-int: 400000 bits>, <oversized-bytes: 1000000 bytes>]",) and _dr_peak < 65536)
        # The whole-value rule for an int whose repr() trips the integer-string-conversion limit: alone it
        # renders as <oversized-int: N bits>, and a container holding it renders as <oversized-value> as a
        # whole, as repr() raising ValueError gives. That holds within the limits, whatever the width (a
        # 70,000-character str beside the int changes nothing), and past them for an int the bounded rendering
        # reaches before its cut (the list of 601 values is past the value bound); an int the copy never
        # reaches is never read. The int-to-str limit is pinned to the default and restored, so the verdict
        # comes from the code under test. Red on a renderer that skips the whole-value test of a complete copy
        # ([10 ** 5000] then renders as the placeholder <unrenderable-value>, its conversion raising) and on
        # one whose bounded form does not test the ints it reaches (the list of 601 values then shows the
        # int's size marker in place).
        _dr_idlimit = sys.get_int_max_str_digits()
        sys.set_int_max_str_digits(4300)
        try:
            _dr_unread = _safe_display([0] * 600 + [10 ** 5000])
            check("deeprender-oversized-int-in-container-whole-value-marker",
                  _safe_display(10 ** 5000) == "<oversized-int: 16610 bits>"
                  and _safe_str(10 ** 5000) == "<oversized-int: 16610 bits>"
                  and _safe_display([10 ** 5000]) == _safe_str([10 ** 5000]) == "<oversized-value>"
                  and _safe_display(["x" * 70000, 10 ** 5000]) == "<oversized-value>"
                  and _safe_display([10 ** 5000] + [0] * 600) == _safe_str([10 ** 5000] + [0] * 600)
                  == "<oversized-value>"
                  and _dr_unread.startswith("[0, 0, ") and _dr_unread.endswith("...<truncated>"))
            # A copy that is not complete (here it holds a depth marker) renders in the bounded form, and the
            # copy is found incomplete before any int is tested, so an int past the cut is never read: the int
            # below follows a 2,000-character str that the cut ends in. Red on a renderer that tests the ints
            # while it looks for a depth marker: it reaches the int, which trips the limit, before the marker,
            # and renders the whole value as <oversized-value>.
            _dr_past = [_dr_nest, "x" * 2000, 10 ** 5000]
            _dr_past_shown = (_safe_display(_dr_past), _safe_str(_dr_past))
        finally:
            sys.set_int_max_str_digits(_dr_idlimit)
        _dr_past_head = ("[" * _DISPLAY_MAX_DEPTH
                         + "<list nested deeper than {} levels>".format(_DISPLAY_MAX_DEPTH)
                         + "]" * (_DISPLAY_MAX_DEPTH - 1) + ", " + chr(39))
        check("deeprender-incomplete-copy-int-unread", _dr_past_shown
              == (_dr_past_head + "x" * (_DISPLAY_MAX_CHARS - len(_dr_past_head)) + "...<truncated>",) * 2)
        # A type off the allow-list (a container subclass, any other object) never has its __repr__,
        # __str__, metaclass __name__ or metaclass __eq__ run, so neither renderer can raise on it; it renders
        # a fixed marker naming only its type. A renderer that calls repr() raises RuntimeError from the
        # subclass __repr__/__str__.

        class _DrBad(list):
            def __repr__(self):
                raise RuntimeError("repr called")

            def __str__(self):
                raise RuntimeError("str called")

        class _DrMeta(type):
            @property
            def __name__(cls):
                raise RuntimeError("metaclass name called")

            def __eq__(cls, other):
                raise RuntimeError("metaclass eq called")
            __hash__ = type.__hash__

        class _DrOdd(metaclass=_DrMeta):
            pass

        class _DrTZ(datetime.tzinfo):
            def utcoffset(self, dt):
                raise RuntimeError("utcoffset called")

            def __repr__(self):
                raise RuntimeError("tzinfo repr called")
        _dr_unsupported = []
        _dr_tzbad = datetime.datetime(2026, 1, 1, tzinfo=_DrTZ())
        for _dr_v in (_DrBad(), [1, _DrBad()], _DrOdd(), {"k": _DrOdd()}, _dr_tzbad):
            try:
                _dr_unsupported.append((_safe_display(_dr_v), _safe_str(_dr_v)))
            except Exception as exc:  # noqa: BLE001  a subclass method escape: recorded, so the check fails
                _dr_unsupported.append(type(exc).__name__)
        check("deeprender-unsupported-types-never-called", _dr_unsupported == [
            ("<unrenderable _DrBad>",) * 2, ("[1, <unrenderable _DrBad>]",) * 2, ("<unrenderable _DrOdd>",) * 2,
            ("{" + chr(39) + "k" + chr(39) + ": <unrenderable _DrOdd>}",) * 2, ("<unrenderable datetime>",) * 2])
        # The store path helpers: a deeply nested value reaching a path helper refuses through the
        # bounded renderer. evidence_inventory checks the phase type before it formats the phase into a file
        # name, and store_control_roots renders its refusal through _safe_display. Red when evidence_inventory
        # formats the phase before that check, or store_control_roots renders the value with {!r}: either
        # raises RecursionError on the nested value. Bounded children.

        def _dr_path_helper(fn, prefix):
            def thunk():
                deep = cur = {}
                for _ in range(100000):
                    cur["a"] = {}
                    cur = cur["a"]
                try:
                    fn(deep)
                except ValueError as exc:
                    return "{}|{}|{}".format(str(exc).startswith(prefix), "nested deeper than" in str(exc),
                                             len(str(exc)) <= len(prefix) + _dr_cap)
                return "accepted"
            return thunk
        _dr_run = "imp-20260101T000000Z-" + "0" * 16
        check("deeprender-evidence-inventory-deep-phase-refused", run_bounded(_dr_path_helper(
            lambda v: evidence_inventory("import", _dr_run, v), "invalid evidence inventory phase: "),
            timeout_s=60) == "True|True|True")
        check("deeprender-store-control-roots-deep-homes-refused", run_bounded(_dr_path_helper(
            store_control_roots, "unknown homes generation: "), timeout_s=60) == "True|True|True")
        # A renderer reads each live member once per occurrence, into one bounded private copy,
        # and renders only that copy. A profile hook stands in for another thread: it replaces a member at the
        # N-th profile event of one render, for every N an unmutated render reaches, so each interleaving at
        # event granularity is tried deterministically. Wherever the replacement lands, the member read is the
        # original or the replacement, checked as it was read, so the result is one of two exact strings (both
        # must occur, so the hook is known to land) and the replacement __repr__ never runs. Red on a
        # renderer that runs repr() on the live container after a separate preflight: on the dict case, 24
        # interleavings of each renderer ran the replacement __repr__ (1,000 NULs, raw from _safe_display, and
        # 4,007 characters escaped from _safe_str).
        _dr_calls = []

        class _DrRace:
            def __repr__(self):
                _dr_calls.append("repr")
                return chr(0) * 1000
            __str__ = __repr__

        def _dr_race_sweep(render, make, mutate, allowed):
            events = [0]

            def count(frame, event, arg):
                events[0] += 1
            prior = sys.getprofile()
            sys.setprofile(count)
            try:
                render(make())
            finally:
                sys.setprofile(prior)
            shown_all, bad = set(), []
            for target in range(1, events[0] + 1):
                value, seen = make(), [0]

                def hook(frame, event, arg):
                    seen[0] += 1
                    if seen[0] == target:
                        mutate(value)
                del _dr_calls[:]
                sys.setprofile(hook)
                try:
                    shown = render(value)
                except Exception as exc:  # noqa: BLE001  an escape is recorded, so the check fails
                    shown = type(exc).__name__
                finally:
                    sys.setprofile(prior)
                shown_all.add(shown)
                if _dr_calls or shown not in allowed:
                    bad.append(target)
            return not bad and shown_all == set(allowed)
        _dr_q = chr(39)
        _dr_kq = "{" + _dr_q + "k" + _dr_q + ": "
        _dr_cases = ((lambda: {"k": 1}, lambda v: v.__setitem__("k", _DrRace()),
                      (_dr_kq + "1}", _dr_kq + "<unrenderable _DrRace>}")),
                     (lambda: [1], lambda v: v.__setitem__(0, _DrRace()), ("[1]", "[<unrenderable _DrRace>]")))
        check("deeprender-member-replaced-mid-render-never-rendered",
              all(_dr_race_sweep(f, make, put, allowed) for f in (_safe_display, _safe_str)
                  for make, put, allowed in _dr_cases))
        # Another thread resizing a dict or set while it is copied makes its iterator raise RuntimeError,
        # which both renderers turn into the fixed placeholder <unrenderable-value>. A profile hook grows a dict
        # and a set at each profile event of one render, as above, so the result is the original, the grown
        # value or the placeholder, and all three must occur. Red when RuntimeError is dropped from the except
        # clause of either renderer: the RuntimeError escapes.
        _dr_grown = ((lambda: {"k": 1}, lambda v: v.__setitem__("n", 2),
                      (_dr_kq + "1}", _dr_kq + "1, " + _dr_q + "n" + _dr_q + ": 2}", "<unrenderable-value>")),
                     (lambda: {1}, lambda v: v.add(2), ("{1}", "{1, 2}", "<unrenderable-value>")))
        check("deeprender-resized-mid-copy-placeholder",
              all(_dr_race_sweep(f, make, grow, allowed) for f in (_safe_display, _safe_str)
                  for make, grow, allowed in _dr_grown))
        # A type name is escaped, so a newline in it cannot forge a line, and since it can be a str
        # subclass of any length it is read only through the str slots: no method of the subclass runs and only
        # _DISPLAY_NAME_MAX characters are copied. The copy check (deeprender-type-name-copy-bounded) is red on
        # a renderer that copies a str-subclass name whole (about 1 MB traced for a 1,000,000-character
        # name); the escape check passes there and is red when the type name is not escaped.

        class _DrName(str):
            def __len__(self):
                raise RuntimeError("name len called")

            def __getitem__(self, key):
                raise RuntimeError("name getitem called")

            def __iter__(self):
                raise RuntimeError("name iter called")

            def __str__(self):
                raise RuntimeError("name str called")

        def _dr_named(name):
            kind = type("_DrNamed", (), {})
            kind.__name__ = name
            return kind()
        _dr_forged = "a" + chr(10) + "FORGED"
        check("deeprender-type-name-escaped",
              [f(_dr_named(n)) for f in (_safe_display, _safe_str) for n in (_dr_forged, _DrName(_dr_forged))]
              == ["<unrenderable a\\x0aFORGED>"] * 4)
        _dr_named_long = _dr_named(_DrName("N" * 1000000))
        _dr_trace = not tracemalloc.is_tracing()
        if _dr_trace:
            tracemalloc.start()
        try:
            tracemalloc.reset_peak()
            _dr_base = tracemalloc.get_traced_memory()[0]
            _dr_names = (_safe_display(_dr_named_long), _safe_str(_dr_named_long))
            _dr_peak = tracemalloc.get_traced_memory()[1] - _dr_base
        finally:
            if _dr_trace:
                tracemalloc.stop()
        check("deeprender-type-name-copy-bounded",
              _dr_names == ("<unrenderable " + "N" * _DISPLAY_NAME_MAX + "...>",) * 2 and _dr_peak < 65536)
        # The copy is bounded by count as well as by depth. A wide, shallow value of zero-cost leaves
        # (17 lists of 170 lists of 170 None: 491,300 None leaves and 2,908 lists, 494,208 values as the copy
        # counts them, one per occurrence) is copied only up to _DISPLAY_MAX_NODES values and renders the
        # exact bounded prefix. The text is the same with or without the count bound (the character
        # bound cuts first), so the copied-entry count is what goes red when the count bound is removed.

        def _dr_entries(entry):
            count, todo = 0, [entry]
            while todo:
                item = todo.pop()
                if item is not _DISPLAY_STOP:
                    count += 1
                    if item[0] == "box":
                        todo.extend(item[2])
            return count
        _dr_shallow = [[[None] * 170] * 170] * 17
        check("deeprender-wide-shallow-copy-bounded",
              _guard(lambda: _dr_entries(_display_snapshot(_dr_shallow))) == _DISPLAY_MAX_NODES
              and _safe_display(_dr_shallow) == "[[[" + ", ".join(["None"] * 170) + "], ...<truncated>")
        # A cut never splits a u-form escape (the x and U forms are checked above), in a container, in a
        # str rendered with repr() and in a str rendered escaped. 11,000 separators escape to 66,000
        # characters, past the width limit. Red on a renderer that cuts at the character bound inside an
        # escape: through _display_take for the container and the repr() str, through _escape_bounded for the
        # escaped str.
        _dr_sep = chr(0x2028) * 11000
        check("deeprender-cut-on-whole-u-escape",
              _safe_display([_dr_sep]) == "[" + chr(39) + "\\u2028" * 170 + "...<truncated>"
              and _safe_display(_dr_sep) == chr(39) + "\\u2028" * 170 + "...<str of 11000 chars>"
              and _safe_str(_dr_sep) == "\\u2028" * 170 + "...<str of 11000 chars>")
        # Escaping keeps the output budget for a container as for a scalar: _safe_str escapes each
        # piece BEFORE the cut, so the result stays within the bound however a piece escapes. No supported value
        # renders a non-printable character, so a leaf rendering of 2,000 NULs is substituted to reach that path
        # (escaping the whole rendered container after its cut, with no budget, overruns it). Each value has a str
        # past the width limit, so it renders in the bounded form, the one that reads _display_leaf.
        _dr_globals = globals()
        _dr_leaf = _dr_globals["_display_leaf"]
        _dr_globals["_display_leaf"] = lambda value, budget: chr(0) * 2000
        try:
            _dr_escaped = (_safe_str([None, "x" * _DISPLAY_FULL_MAX_CHARS]),
                           _safe_str({"k": None, "w": "x" * _DISPLAY_FULL_MAX_CHARS}))
        finally:
            _dr_globals["_display_leaf"] = _dr_leaf
        check("deeprender-container-escape-keeps-budget",
              _dr_escaped == ("[" + "\\x00" * 255 + "...<truncated>", "{" + "\\x00" * 255 + "...<truncated>"))
        # The int width test is exact at the width limit: an int whose repr() is exactly
        # _DISPLAY_FULL_MAX_CHARS characters (2 ** 217705, the largest power of two that wide, among them) or
        # one character less renders as repr(), uncut, and one a character wider renders in the bounded form,
        # as its size marker; a negative int spends one character on its sign. The bounded form's own test is
        # exact at _DISPLAY_MAX_CHARS too (an estimate from the bit length is not exact, so an int whose repr() is
        # exactly 1,024 characters, 2 ** 3401 among them, could render the marker): in a list past the value
        # bound, such an int renders as its digits, cut, and 10 ** 1024 as its marker. The int-to-str limit is
        # lifted and restored, so the width, not that limit, decides and the verdict comes from the code under
        # test.
        _dr_idlimit = sys.get_int_max_str_digits()
        sys.set_int_max_str_digits(0)
        try:
            _dr_fit = (2 ** 217705, 10 ** 65535, -(10 ** 65534), 10 ** 65536 - 1, -(10 ** 65535) + 1)
            _dr_inside = (10 ** 65535 - 1, -(10 ** 65534) + 1)
            check("deeprender-int-width-exact-at-bound",
                  all(len(repr(v)) == _DISPLAY_FULL_MAX_CHARS and _safe_display(v) == _safe_str(v) == repr(v)
                      for v in _dr_fit)
                  and all(len(repr(v)) == _DISPLAY_FULL_MAX_CHARS - 1
                          and _safe_display(v) == _safe_str(v) == repr(v) for v in _dr_inside)
                  and [f(v) for f in (_safe_display, _safe_str) for v in (10 ** 65536, -(10 ** 65535))]
                  == ["<oversized-int: 217706 bits>", "<oversized-int: 217703 bits>"] * 2
                  and _safe_display([2 ** 3401] + [0] * 600)
                  == "[" + repr(2 ** 3401)[:_DISPLAY_MAX_CHARS - 1] + "...<truncated>"
                  and _safe_display([10 ** 1024] + [0] * 600).startswith("[<oversized-int: 3402 bits>, 0, 0"))
        finally:
            sys.set_int_max_str_digits(_dr_idlimit)
        # A timezone renders through repr() only when it holds nothing a caller defines and its name is
        # at most _DISPLAY_NAME_MAX characters. A 64-character name renders as repr(), byte for byte; a
        # 65-character name, a timedelta-subclass offset and a str-subclass name each render the fixed marker
        # from both renderers, and no subclass __repr__ runs. Red on a renderer whose name-length test admits
        # 65 characters, and on one that admits an offset that is not an exact timedelta or a name that is not
        # an exact str: _safe_display then runs the subclass __repr__, whose RuntimeError renders the
        # placeholder <unrenderable-value>, and _safe_str renders the datetime as text.

        class _DrDelta(datetime.timedelta):
            def __repr__(self):
                raise RuntimeError("offset repr called")

        class _DrZoneName(str):
            def __repr__(self):
                raise RuntimeError("zone name repr called")
        _dr_at = datetime.datetime(2026, 1, 2, 3, 4, 5)
        _dr_zone64 = _dr_at.replace(tzinfo=datetime.timezone(_dr_hour, "N" * _DISPLAY_NAME_MAX))
        _dr_zones = (_dr_at.replace(tzinfo=datetime.timezone(_dr_hour, "N" * (_DISPLAY_NAME_MAX + 1))),
                     _dr_at.replace(tzinfo=datetime.timezone(_DrDelta(hours=5))),
                     _dr_at.replace(tzinfo=datetime.timezone(_dr_hour, _DrZoneName("n"))))
        check("deeprender-timezone-admission-exact",
              _safe_display(_dr_zone64) == repr(_dr_zone64) and _safe_display([_dr_zone64]) == repr([_dr_zone64])
              and [f(v) for f in (_safe_display, _safe_str) for v in _dr_zones] == ["<unrenderable datetime>"] * 6)
        # require_ordinary_target renders its refusal through _safe_display (a {!r} there renders a long
        # target whole). The target is a contained path under the journal home, past the width limit (more than
        # 70,000 characters), so its refusal is bounded.
        _dr_target = JOURNALS_REL + "/" + "a" * 70000
        try:
            require_ordinary_target(_dr_target)
            _dr_refusal = "accepted"
        except ValueError as exc:
            _dr_refusal = str(exc)
        _dr_expected = "ordinary operation target {}{}...<str of {} chars> overlaps the journal home".format(
            chr(39), _dr_target[:_DISPLAY_MAX_CHARS - 1], len(_dr_target))
        check("deeprender-ordinary-target-refusal-bounded", _dr_refusal == _dr_expected)
        # A frame of the copy ends when its iterator raises StopIteration, never on a member compared
        # with a marker, so whatever object a value holds renders the same at the root, in a list, as a dict key
        # and as a dict value. Each bare object() this module holds, a fresh one and the stop entry _DISPLAY_STOP
        # are tried against their exact renderings, and every other value this module holds renders in a list
        # and in a dict as it renders alone in a list, so a sentinel held here is caught whatever its type (a
        # value too long to place with three more members is skipped). Red on a renderer that ends a frame on
        # a module sentinel (a bare object()): supplied as data, that sentinel raises IndexError at the root,
        # ends a list or a dict early ([1] for [1, sentinel, 2, 3]) and, as a dict value, renders the next key
        # as its value.

        def _dr_positions(render, obj, mark):
            shown = []
            for value in (obj, [1, obj, 2, 3], {obj: 1}, {"k": obj, "j": 2}):
                try:
                    shown.append(render(value))
                except Exception as exc:  # noqa: BLE001  a sentinel escape: recorded, so the check fails
                    shown.append(type(exc).__name__)
            return shown == [mark, "[1, " + mark + ", 2, 3]", "{" + mark + ": 1}",
                             _dr_kq + mark + ", " + _dr_q + "j" + _dr_q + ": 2}"]

        def _dr_in_place(render, obj):
            try:
                inner = render([obj])[1:-1]
                if len(inner) > 500 or "...<" in inner:
                    return True   # too long to take three more members inside both bounds
                try:
                    keyed = {obj: 1}
                except TypeError:   # an unhashable value is never a dict key
                    keyed = None
                return (render([1, obj, 2, 3]) == "[1, " + inner + ", 2, 3]"
                        and render({"k": obj, "j": 2}) == _dr_kq + inner + ", " + _dr_q + "j" + _dr_q + ": 2}"
                        and (keyed is None or render(keyed) == "{" + inner + ": 1}"))
            except Exception:  # noqa: BLE001  an escape is recorded, so the check fails
                return False
        _dr_objects = [(v, "<unrenderable object>") for v in globals().values() if type(v) is object]
        _dr_objects += [(object(), "<unrenderable object>"), (_DISPLAY_STOP, repr(_DISPLAY_STOP))]
        check("deeprender-module-objects-render-as-data",
              all(_dr_positions(f, v, mark) for f in (_safe_display, _safe_str) for v, mark in _dr_objects)
              and all(_dr_in_place(f, v) for f in (_safe_display, _safe_str) for v in list(globals().values())))
        # The node bound stops a render with the truncation marker. A list holding one chain of 31
        # nested lists wrapping 0 sixteen times is 513 values as the copy counts them (each occurrence is copied),
        # so the copy stops before the last 0 and the render is the 1,007-character prefix of repr() before it,
        # then ...<truncated>, within the character bound. Red when the stop drops its marker (the same prefix,
        # unmarked) and when the node bound is removed (1,024 characters, cut by the character bound).
        _dr_chain = 0
        for _ in range(31):
            _dr_chain = [_dr_chain]
        _dr_link = "[" * 31
        check("deeprender-node-bound-stop-marked",
              _safe_display([_dr_chain] * 16) == _safe_str([_dr_chain] * 16)
              == "[" + ", ".join([_dr_link + "0" + "]" * 31] * 15) + ", " + _dr_link + "...<truncated>")
        # One render converts at most _DISPLAY_INT_BUDGET_BITS bits of int, all its conversions summed: each
        # is charged its bit length before it is made, and an int the budget cannot cover stops the bounded
        # rendering there with _DISPLAY_SPENT. For these renders the module's repr is replaced by one that sums
        # the bit lengths of the ints passed to it, so the total is measured. With the int-to-str limit raised
        # to 10,000 digits, each 33,000-bit int below converts without tripping it (9,934 digits), so a list of
        # 300 of them needs one conversion per int to test: 254 fit the budget, the 255th does not, and the
        # bounded rendering stops at the first int it reaches, after content of exactly _DISPLAY_MAX_CHARS
        # characters. Red on a renderer without the budget: it converts every int (10,131,000 bits) and ends in
        # ...<truncated>. At the default limit the budget is never reached, so it changes no output there: the
        # worst case, 510 ints of 14,285 bits (the most a 4,300-digit int has) tested before one of
        # _DISPLAY_FULL_INT_BITS bits that trips, converts exactly 7,547,494 bits, and 511 such ints with none
        # tripping convert 7,528,195 (511 tested, then 16 converted for the whole rendering until it is too
        # wide, and none tested again by the bounded one). Red on a budget below that worst case, and on a
        # bounded rendering that tests again the ints the whole-value test cleared (514,260 bits more). A value
        # the byte-identity guarantee covers stays far inside the budget at any limit: one int of 65,534
        # digits in a list, its repr() exactly _DISPLAY_FULL_MAX_CHARS characters, renders as repr() under a
        # limit of 65,534 digits and converts 435,392 bits (tested once, converted once more for the whole
        # rendering). Red on a budget below that. The budget admits a conversion of exactly the bits left and
        # refuses one of a bit more: [5] (3 bits) renders whole under a budget of 4 or 3 bits and stops under
        # 2. Red on a budget test off by one.
        _dr_repr = repr

        def _dr_counted(render, value):
            bits = [0]

            def counting(obj):
                if type(obj) is int:
                    bits[0] += obj.bit_length()
                return _dr_repr(obj)
            _dr_globals["repr"] = counting
            try:
                shown = render(value)
            finally:
                del _dr_globals["repr"]
            return shown, bits[0]
        _dr_idlimit = sys.get_int_max_str_digits()
        _dr_budget_bits = _DISPLAY_INT_BUDGET_BITS
        try:
            sys.set_int_max_str_digits(10000)
            _dr_spent_head = "[" + repr("x" * 1019) + ", "
            _dr_spent = [_dr_counted(f, ["x" * 1019] + [2 ** 32999 + 1] * 300) for f in (_safe_display, _safe_str)]
            sys.set_int_max_str_digits(4300)
            _dr_ok, _dr_trip = 10 ** 4300 - 1, 2 ** (_DISPLAY_FULL_INT_BITS - 1)
            _dr_worst = [_dr_counted(f, v) for f in (_safe_display, _safe_str)
                         for v in ([_dr_trip] + [_dr_ok] * 510, [_dr_ok] * 511)]
            sys.set_int_max_str_digits(65534)
            _dr_whole = [10 ** 65533]
            _dr_whole_repr = repr(_dr_whole)
            _dr_identity = [_dr_counted(f, _dr_whole) for f in (_safe_display, _safe_str)]
            _dr_edges = []
            for _dr_bits in (4, 3, 2):
                _dr_globals["_DISPLAY_INT_BUDGET_BITS"] = _dr_bits
                _dr_edges.append((_safe_display([5]), _safe_str([5])))
        finally:
            _dr_globals["_DISPLAY_INT_BUDGET_BITS"] = _dr_budget_bits
            sys.set_int_max_str_digits(_dr_idlimit)
        _dr_cut_ints = (("[" + ", ".join(["<oversized-int: 14285 bits>"] * 511))[:_DISPLAY_MAX_CHARS]
                        + "...<truncated>")
        check("deeprender-int-budget-bounds-conversions",
              _dr_spent == [(_dr_spent_head + _DISPLAY_SPENT, 254 * 33000)] * 2
              and len(_dr_spent_head) == _DISPLAY_MAX_CHARS and 254 * 33000 <= _DISPLAY_INT_BUDGET_BITS)
        check("deeprender-int-budget-unreached-at-default-limit",
              _dr_worst == [("<oversized-value>", 7547494), (_dr_cut_ints, 7528195)] * 2
              and 7547494 <= _DISPLAY_INT_BUDGET_BITS)
        check("deeprender-int-budget-spares-byte-identity",
              len(_dr_whole_repr) == _DISPLAY_FULL_MAX_CHARS and _dr_identity == [(_dr_whole_repr, 435392)] * 2)
        check("deeprender-int-budget-boundary-exact",
              _dr_edges == [("[5]", "[5]"), ("[5]", "[5]"), ("[" + _DISPLAY_SPENT,) * 2])
        # Each marker that ends a bounded rendering is measured on renderer output, at its longest reachable
        # form and after content of exactly _DISPLAY_MAX_CHARS characters: ...<str of N chars> from the str
        # path of both renderers, with N at sys.maxsize, the most len() returns (for these renders the module's
        # len is replaced by one that reports sys.maxsize for one 2,000-character str, and each render must
        # read it); ...<truncated> from the bounded form of both, where that str's own marker falls past the
        # cut; and ...<int conversion budget spent> from the budget renders above. The longest must be exactly
        # _DISPLAY_MARKER_MAX characters. Red on a renderer that emits a longer marker (...<str of N
        # characters>, 42 characters at sys.maxsize, or N with thousands separators, 43) and on a
        # _DISPLAY_MARKER_MAX other than the longest marker's length.
        _dr_huge = "x" * 2000
        _dr_reads = [0]
        _dr_len = len

        def _dr_len_at_max(obj):
            if obj is _dr_huge:
                _dr_reads[0] += 1
                return sys.maxsize
            return _dr_len(obj)
        _dr_ended = []
        _dr_globals["len"] = _dr_len_at_max
        try:
            for _dr_f, _dr_v in ((_safe_display, _dr_huge), (_safe_str, _dr_huge), (_safe_display, [_dr_huge]),
                                 (_safe_str, [_dr_huge])):
                _dr_reads[0] = 0
                _dr_ended.append((_dr_f(_dr_v), _dr_reads[0]))
        finally:
            del _dr_globals["len"]
        _dr_heads = ([chr(39) + "x" * (_DISPLAY_MAX_CHARS - 1), "x" * _DISPLAY_MAX_CHARS]
                     + ["[" + chr(39) + "x" * (_DISPLAY_MAX_CHARS - 2)] * 2 + [_dr_spent_head] * 2)
        _dr_ended = [shown for shown, reads in _dr_ended if reads] + [shown for shown, _ in _dr_spent]
        check("deeprender-marker-max-is-the-longest-marker",
              len(_dr_ended) == len(_dr_heads)
              and all(shown[:_DISPLAY_MAX_CHARS] == head for shown, head in zip(_dr_ended, _dr_heads))
              and max(len(shown) - _DISPLAY_MAX_CHARS for shown in _dr_ended) == _DISPLAY_MARKER_MAX)

        # NEW-5: the store-read cap is enforced on the bytes ACTUALLY read, not only the pre-open lstat, so a
        # file reporting a small size at lstat but reading over the cap (a raced swap) is refused. The lstat is
        # pinned small while the real file is over-cap.
        n5_root = build_store(manifest=manifest_text() + "\n#" + "x" * (MAX_STORE_READ_BYTES + 16))
        _real_lstat = _journal._lstat_contained
        def _small_lstat(_rfd_arg, _rel_arg):
            _stv = _real_lstat(_rfd_arg, _rel_arg)
            if _stv is not None and stat.S_ISREG(_stv.st_mode):
                _f = list(_stv); _f[stat.ST_SIZE] = 1
                return os.stat_result(_f)
            return _stv
        _journal._lstat_contained = _small_lstat
        try:
            check("new5-toctou-oversized-read-cannot-eval", resolve_store(n5_root).status == CANNOT_EVALUATE)
        finally:
            _journal._lstat_contained = _real_lstat

        # ---- MINOR-1: journal-reader memory bound (fail pre-fix, pass post-fix) ------------------------
        # The journal CONTROL readers (read_frames, read_lock_owner) cap how much they read, so a pre-planted
        # oversize journal file is refused fail-closed rather than slurped whole into memory. Discriminated
        # with the cap monkeypatched SMALL so the vector stays fast and deterministic: a WELL-FORMED control
        # file LARGER than the (patched) cap must be refused (JournalError) at the size gate, whereas with the
        # cap logic reverted each reader would read and parse it normally (no refusal). Restored in a finally.
        _m1_frames = base / "minor1-frames"; _m1_frames.mkdir()
        (_m1_frames / "frames.log").write_bytes(_journal._frame(_journal.F_INTENT, b'{"txn":"t","ops":[]}'))
        _m1_lock = base / "minor1-lock"; _m1_lock.mkdir()
        (_m1_lock / "lock").write_bytes(b'{"uid": 0, "pid": 1, "pid-start": "", "session": "s", "utc": "u"}')
        _real_jcap = _journal._MAX_JOURNAL_READ_BYTES
        _journal._MAX_JOURNAL_READ_BYTES = 8        # below either well-formed control file; above 0
        _m1_jr = os.open(str(base), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            check("minor1-read-frames-oversize-refused",
                  _guard(lambda: (_journal.read_frames(_m1_jr, _m1_frames), "read")[1]) == "RAISED")
            check("minor1-read-lock-owner-oversize-refused",
                  _guard(lambda: (_journal.read_lock_owner(_m1_lock), "read")[1]) == "RAISED")
        finally:
            _journal._MAX_JOURNAL_READ_BYTES = _real_jcap
            os.close(_m1_jr)

        # ---- ITEM A: product-file read ceiling (fail pre-fix, pass post-fix) ---------------------------
        # _read_contained / _read_at carry a HARD INCREMENTAL ceiling so a product file GROWN or swapped past
        # a caller's pre-open st.st_size (the store/changelog racing-grow residual) is refused fail-closed AT
        # the ceiling rather than slurped whole into memory, while a normal-size file reads unchanged.
        # Discriminated with the ceiling monkeypatched SMALL: a file LARGER than the (patched) ceiling must be
        # refused (JournalError) by BOTH readers, whereas with the cap reverted (cap=None) each would read it
        # whole (no refusal); an UNDER-ceiling file still reads through. Restored in a finally.
        _pa_dir = base / "itemA-product"; _pa_dir.mkdir()
        (_pa_dir / "big").write_bytes(b"x" * 4096)
        (_pa_dir / "small").write_bytes(b"ok")
        _pa_fd = os.open(str(_pa_dir), os.O_RDONLY | os.O_DIRECTORY)
        _pa_pfd = os.open(str(_pa_dir), os.O_RDONLY | os.O_DIRECTORY)
        _real_pcap = _journal._MAX_PRODUCT_READ_BYTES
        _journal._MAX_PRODUCT_READ_BYTES = 8        # below the 4096-byte file; above the 2-byte file
        try:
            check("itemA-read-contained-oversize-refused",
                  _guard(lambda: (_journal._read_contained(_pa_fd, "big"), "read")[1]) == "RAISED")
            check("itemA-read-at-oversize-refused",
                  _guard(lambda: (_journal._read_at(_pa_pfd, "big", "big"), "read")[1]) == "RAISED")
            check("itemA-read-contained-under-ceiling-ok",
                  _guard(lambda: _journal._read_contained(_pa_fd, "small")[0]) == b"ok")
        finally:
            _journal._MAX_PRODUCT_READ_BYTES = _real_pcap
            os.close(_pa_fd)
            os.close(_pa_pfd)

        # ---- MINOR-2: symlink-race containment (fail pre-fix, pass post-fix) ---------------------------
        # publish and _truncate_log open frames.log CONTAINED (O_NOFOLLOW + dir-fd relative), so a symlinked
        # frames.log cannot redirect the append/ftruncate onto a victim file. Pre-fix each followed a
        # re-resolved absolute path and would mutate the victim (publish appends, _truncate_log truncates it
        # to 0); post-fix each fails closed (JournalError) and leaves the victim byte-for-byte intact. Both a
        # refusal check and a victim-intact check discriminate.
        _victim = base / "minor2-victim"
        _victim.write_bytes(b"VICTIM-INTACT")
        _m2_jr = os.open(str(base), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            _m2_pub = base / "minor2-publish"; _m2_pub.mkdir()
            os.symlink(str(_victim), str(_m2_pub / "frames.log"))
            check("minor2-publish-symlink-refused",
                  _guard(lambda: _journal.publish(_m2_jr, _m2_pub, _journal.F_INTENT,
                                                  {"txn": "t", "ops": []})) == "RAISED")
            check("minor2-publish-victim-intact", _victim.read_bytes() == b"VICTIM-INTACT")
            _m2_tr = base / "minor2-truncate"; _m2_tr.mkdir()
            os.symlink(str(_victim), str(_m2_tr / "frames.log"))
            check("minor2-truncate-symlink-refused",
                  _guard(lambda: _journal._truncate_log(_m2_jr, _m2_tr, 0)) == "RAISED")
            check("minor2-truncate-victim-intact", _victim.read_bytes() == b"VICTIM-INTACT")
        finally:
            os.close(_m2_jr)

        # ---- F1: an ANCESTOR symlink on the txn dir's path fails closed (fail pre-fix, pass post-fix) ----
        # publish/read_frames/_truncate_log now reach the txn dir by a dir-fd-relative O_NOFOLLOW open
        # BENEATH a journal-root fd that is itself reached by a CONTAINED per-component no-follow walk from
        # the repo root (open_journal_root_fd). So a symlinked ANCESTOR component of the journal path is
        # refused fail-closed. Pre-fix each opened the txn dir by its ABSOLUTE path with O_NOFOLLOW guarding
        # only the FINAL txn component, so a symlinked journal-root ancestor was FOLLOWED and the frame op
        # escaped off-tree. Discriminated by planting a symlinked journal-root component: the contained walk
        # refuses it (ELOOP -> JournalError), where the reverted absolute-path open would have followed it.
        _f1root = base / "f1-ancestor"; _f1root.mkdir()
        (_f1root / "realj" / "txn").mkdir(parents=True)
        (_f1root / "realj" / "txn" / "frames.log").write_bytes(
            _journal._frame(_journal.F_INTENT, b'{"txn":"t","ops":[]}'))
        os.symlink("realj", str(_f1root / "jdir"))          # jdir -> realj: a symlinked journal-root ancestor
        _f1_rootfd = os.open(str(_f1root), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            # positive control: through the REAL journal root, the txn journal reads normally.
            _f1_realjr = _journal.open_journal_root_fd(_f1_rootfd, "realj")
            try:
                _f1_frames, _f1_torn, _f1_len = _journal.read_frames(_f1_realjr, _f1root / "realj" / "txn")
                check("f1-ancestor-real-path-reads", [t for t, _ in _f1_frames] == [_journal.F_INTENT])
            finally:
                os.close(_f1_realjr)
            # the symlinked ancestor 'jdir' is refused by the contained walk (this FAILS if the walk is
            # reverted to a symlink-following absolute-path open of the journal root).
            check("f1-ancestor-symlink-refused",
                  _guard(lambda: _journal.open_journal_root_fd(_f1_rootfd, "jdir")) == "RAISED")

            # capture_preimages leg: it reaches the txn dir and writes/fsyncs its preimages CONTAINED
            # beneath the trusted parent_fd, using only Path(txn_dir).name, so an ANCESTOR symlink on the
            # txn_dir path is IGNORED and the store lands under the real txn (parent_fd tree). Pre-fix it
            # built `pre = txn_dir/"preimages"` and `_fsync_path_dir(txn_dir)` by ABSOLUTE path, following an
            # ancestor symlink onto a DECOY off-tree. Discriminated by pointing txn_dir's absolute route
            # through a symlink to a decoy: post-fix the preimage lands under the real txn and the decoy
            # stays empty; a body reverted to the absolute-path store would write into the decoy instead.
            (_f1root / "data.txt").write_bytes(b"F1-CAPTURE-PAYLOAD")
            (_f1root / "realj" / "capt").mkdir()            # the REAL txn dir, beneath the real journal root
            (_f1root / "decoy" / "capt").mkdir(parents=True)  # a decoy the ancestor symlink resolves onto
            os.symlink("decoy", str(_f1root / "caplink"))   # caplink -> decoy: an ANCESTOR symlink on the txn path
            _f1_capjr = os.open(str(_f1root / "realj"), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                _journal.capture_preimages(
                    _f1_capjr, _f1root / "caplink" / "capt", _f1_rootfd, [{"op": "remove", "path": "data.txt"}])
            finally:
                os.close(_f1_capjr)
            check("f1-capture-preimage-contained-under-real-txn",
                  (_f1root / "realj" / "capt" / "preimages" / "0").is_file())
            check("f1-capture-preimage-not-under-ancestor-symlink-decoy",
                  not (_f1root / "decoy" / "capt" / "preimages").exists())
        finally:
            os.close(_f1_rootfd)

        # ---- F-R17-A1: a store CONTROL file hardlinked to an out-of-tree victim is refused ----------------
        # A manifest or pointer hardlinked to an external file passes O_NOFOLLOW and S_ISREG, so without the
        # opened-fd nlink==1 guard its resolved posture would track the victim inode. The manifest case masks
        # _lstat_contained to report a single link, proving the decision uses the OPENED fd, not the pre-open
        # lstat probe. A GENERIC product read keeps an intentional hardlink (the guard is opt-in, off by
        # default), so nlink==2 product reads are not over-restricted.
        a1_root = build_store(manifest=manifest_text())
        check("a1-precondition-resolved", resolve_store(a1_root).status == RESOLVED)
        a1_manifest = a1_root / WORKING_DIRNAME / DEFAULT_MACHINE_SUBDIR / MANIFEST_NAME
        a1_victim = base / "a1-manifest-victim.toml"
        a1_victim.write_text(manifest_text(), encoding="utf-8")
        a1_manifest.unlink()
        os.link(str(a1_victim), str(a1_manifest))              # manifest now nlink == 2
        _a1_real_lstat = _journal._lstat_contained

        def _a1_masked_lstat(root_fd, relpath):
            st = _a1_real_lstat(root_fd, relpath)
            if st is None or not relpath.endswith(MANIFEST_NAME):
                return st
            fields = list(st)
            fields[stat.ST_NLINK] = 1                          # hide the extra link from the pre-open probe
            return os.stat_result(fields)

        _journal._lstat_contained = _a1_masked_lstat
        try:
            a1_manifest_result = load_manifest(resolve_store(a1_root))
        finally:
            _journal._lstat_contained = _a1_real_lstat
        check("a1-hardlinked-manifest-cannot-eval",
              a1_manifest_result.status == CANNOT_EVALUATE)

        # a hardlinked COMMITTED pointer is refused during resolution
        a1_pstore = base / "a1-pointer-store"
        (a1_pstore / WORKING_DIRNAME / DEFAULT_MACHINE_SUBDIR).mkdir(parents=True)
        (a1_pstore / WORKING_DIRNAME / DEFAULT_MACHINE_SUBDIR / MANIFEST_NAME).write_text(
            manifest_text(), encoding="utf-8")
        a1_pr = build_store(make_working=False)
        a1_pvictim = base / "a1-pointer-victim.toml"
        a1_pvictim.write_text('[store]\ntarget = "dir:{}"\n'.format(a1_pstore), encoding="utf-8")
        os.link(str(a1_pvictim), str(a1_pr / POINTER_REL))     # committed pointer now nlink == 2
        check("a1-hardlinked-committed-pointer-cannot-eval",
              resolve_store(a1_pr).status == CANNOT_EVALUATE)

        # a hardlinked LOCAL pointer is refused during resolution
        a1_lpr = build_store(make_working=False)
        a1_lpvictim = base / "a1-local-pointer-victim.toml"
        a1_lpvictim.write_text('[store]\ntarget = "dir:{}"\n'.format(a1_pstore), encoding="utf-8")
        os.link(str(a1_lpvictim), str(a1_lpr / LOCAL_POINTER_REL))   # local pointer now nlink == 2
        check("a1-hardlinked-local-pointer-cannot-eval",
              resolve_store(a1_lpr).status == CANNOT_EVALUATE)

        # a GENERIC product hardlink read is NOT over-restricted (the guard is opt-in, off by default)
        a1_generic = base / "a1-generic-root"
        a1_generic.mkdir()
        a1_gvictim = base / "a1-generic-victim"
        a1_gvictim.write_bytes(b"intentional product hardlink")
        os.link(str(a1_gvictim), str(a1_generic / "product-data"))
        a1_gfd = os.open(str(a1_generic), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            a1_gdata, a1_gst = _journal._read_contained(a1_gfd, "product-data")
        finally:
            os.close(a1_gfd)
        check("a1-generic-product-hardlink-allowed",
              a1_gdata == b"intentional product hardlink" and a1_gst.st_nlink == 2)

        # F-JOURNAL-HELD-FD-LISTING: a store subdirectory created AFTER the held .working descriptor
        # was opened is still discovered: _list_real_subdirs reads the listing through a fresh
        # descriptor reopened as "." beneath the held one, never through the held descriptor itself
        # (a held descriptor's listing can miss every entry created since its open). FILESYSTEM-DEPENDENT
        # integration evidence: this native leg discriminates only where a held descriptor's listing is
        # stale (btrfs, not tmpfs); the seam leg after it discriminates on any filesystem.
        hf_working = base / "heldfd-root" / WORKING_DIRNAME
        hf_working.mkdir(parents=True)
        (hf_working / "early-store").mkdir()
        hf_wfd = os.open(str(hf_working), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            (hf_working / "late-store").mkdir()           # created AFTER the descriptor was opened
            hf_names = _guard(lambda: _list_real_subdirs(hf_wfd, WORKING_DIRNAME))
            check("heldfd-late-store-subdir-listed-through-fresh-descriptor",
                  hf_names == ["early-store", "late-store"])
        finally:
            os.close(hf_wfd)
        # The same late subdirectory under _journal._StStaleListing, held from the .working open: the seam
        # serves the stale view through the held descriptor and any dup of it, and the current view only
        # through a "." descriptor opened relative to it, so on ANY filesystem _list_real_subdirs must
        # list through a fresh descriptor.
        hs_working = base / "heldfd-seam-root" / WORKING_DIRNAME
        hs_working.mkdir(parents=True)
        (hs_working / "early-store").mkdir()
        hs_wfd = os.open(str(hs_working), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            with _journal._StStaleListing() as hs_seam:
                hs_seam.hold(hs_wfd)
                (hs_working / "late-store").mkdir()       # created AFTER the descriptor was opened
                hs_names = _guard(lambda: _list_real_subdirs(hs_wfd, WORKING_DIRNAME))
            check("heldfd-seam-late-store-subdir-listed-through-fresh-descriptor",
                  hs_names == ["early-store", "late-store"] and hs_seam.views
                  and "stale" not in hs_seam.views)
        finally:
            os.close(hs_wfd)

        # OPF-IMPORTED-SERIES IS-2: imported_leaf_names derives the imported machine-store leaf names
        # from an enabled-type roster: one <type>.imported.index.toml per non-ledger type, the worklog
        # ledger worklog.imported.toml, NO leaf for the importer-only legacy_fragment (its LF
        # namespace is outside the imported series, spec 8.2), and NO leaf for a name outside the
        # section-8.1 roster (fail-closed). The agreement with _opf_schema.IMPORTED_NAMESPACES is
        # reconciled in the opf-check self-test, beside the containment vectors that consume this
        # roster.
        _il_base = imported_leaf_names(BASELINE_TYPES)
        check("imported-leaf-names-baseline",
              _il_base == frozenset({IMPORTED_WORKLOG_NAME}
                                    | {t + IMPORTED_INDEX_SUFFIX
                                       for t in BASELINE_TYPES if t != "worklog"}))
        check("imported-leaf-names-lf-excluded",
              imported_leaf_names(dict(BASELINE_TYPES, legacy_fragment="LF")) == _il_base
              and imported_leaf_names(IMPORTER_TYPES) == frozenset())
        check("imported-leaf-names-module-tier",
              imported_leaf_names(("release",)) == frozenset({"release" + IMPORTED_INDEX_SUFFIX}))
        check("imported-leaf-names-unknown-skipped",
              imported_leaf_names(("nonsense", "transaction")) == frozenset())
        # The activation threshold is PINNED: imported_series_declared compares both arms (the
        # support arm through the shared imported_series_supported helper) against the
        # IMPORTED_SPEC_VERSION constant, so this equality anchors every sampled point below
        # (red when the constant itself drifts, e.g. to a multidigit 1.2.100 the interval
        # samples alone would not see; a drift to the sampled 1.2.10 the matrix already reds).
        check("imported-spec-version-pinned-130", IMPORTED_SPEC_VERSION == "1.3.0")
        # imported_series_declared: the activation gate requires BOTH the declared spec_version
        # (IMPORTED_SPEC_VERSION or later, through the house SemVer parser) AND this tooling's own
        # support (SUPPORTED_SPEC_VERSION at or above IMPORTED_SPEC_VERSION, the way homes_generation
        # gates on SUPPORTED_HOMES). A newer declaration ALONE answers False, including the reserved
        # 2.0.0 / homes = 2 pair validate_manifest accepts today (spec 4.2): without the
        # tooling-support condition that accepted store would register the imported leaves while
        # every imported-content check is still deferred. The current 1.2.0, an older, a non-string,
        # and an unparseable declaration answer False in every case (spec 8.2/4.2), INCLUDING while
        # support is raised: once the tooling-support clause is satisfied, only the declared-version
        # comparison keeps them out, so that arm is asserted on its False side under the patch (red
        # when the comparison is removed, or when its threshold drifts down to a SAMPLED point:
        # 1.2.0, 1.2.1, 1.2.9, or the multidigit 1.2.10 and 1.2.99 below. The samples establish
        # those points only, never the whole open 1.2.x range; the threshold constant itself is
        # pinned to 1.3.0 by its own check above).
        check("imported-series-declared-false-until-tooling-supports",
              not imported_series_declared({"opf": {"spec_version": "1.3.0"}})
              and not imported_series_declared({"opf": {"spec_version": "2.0.0"}})
              and not imported_series_declared(
                  {"opf": {"spec_version": HOMES2_SPEC_VERSION, "homes": 2}}))
        _isd_prev = SUPPORTED_SPEC_VERSION
        try:
            globals()["SUPPORTED_SPEC_VERSION"] = IMPORTED_SPEC_VERSION
            check("imported-series-declared-130-and-later-once-supported",
                  imported_series_declared({"opf": {"spec_version": "1.3.0"}})
                  and imported_series_declared({"opf": {"spec_version": "2.0.0"}}))
            check("imported-series-declared-below-130-false-even-once-supported",
                  not imported_series_declared({"opf": {"spec_version": "1.2.0"}})
                  and not imported_series_declared({"opf": {"spec_version": "1.2.1"}})
                  and not imported_series_declared({"opf": {"spec_version": "1.2.9"}})
                  and not imported_series_declared({"opf": {"spec_version": "1.0.0"}})
                  and not imported_series_declared({"opf": {"spec_version": "one.three"}}))
            # Multidigit patch declarations below the threshold: the house parser does not stop
            # at single-digit patches, so 1.2.10 and 1.2.99 parse, sit below 1.3.0, and answer
            # False under raised support. Sampled points only (red when the declared threshold
            # drifts down to 1.2.99, or past it to the lower 1.2.10).
            check("imported-series-declared-multidigit-patch-below-130-false",
                  not imported_series_declared({"opf": {"spec_version": "1.2.10"}})
                  and not imported_series_declared({"opf": {"spec_version": "1.2.99"}}))
            # Support ABOVE the threshold activates too: the support comparison is at-or-above,
            # not equality (red when its ">=" tightens to "==").
            globals()["SUPPORTED_SPEC_VERSION"] = "1.3.1"
            check("imported-series-declared-under-support-above-130",
                  imported_series_declared({"opf": {"spec_version": "1.3.0"}})
                  and imported_series_declared({"opf": {"spec_version": "1.3.1"}}))
            # Support BELOW the threshold stays inactive whatever the declaration: the support
            # arm also compares against IMPORTED_SPEC_VERSION, so the sampled support values
            # 1.2.1 and the multidigit 1.2.10 both answer False even for 1.3.0 and 2.0.0
            # declarations (red when the support threshold drifts down to the interior
            # multidigit 1.2.10, or past it to the lower 1.2.1). Sampled support points only,
            # the way the declared arm is sampled above.
            globals()["SUPPORTED_SPEC_VERSION"] = "1.2.1"
            _isd_sup_121 = (not imported_series_declared({"opf": {"spec_version": "1.3.0"}})
                            and not imported_series_declared({"opf": {"spec_version": "2.0.0"}}))
            globals()["SUPPORTED_SPEC_VERSION"] = "1.2.10"
            check("imported-series-declared-false-under-support-below-130",
                  _isd_sup_121
                  and not imported_series_declared({"opf": {"spec_version": "1.3.0"}})
                  and not imported_series_declared({"opf": {"spec_version": "2.0.0"}}))
        finally:
            globals()["SUPPORTED_SPEC_VERSION"] = _isd_prev
        check("imported-series-declared-current-and-malformed-false",
              not imported_series_declared({"opf": {"spec_version": SUPPORTED_SPEC_VERSION}})
              and not imported_series_declared({"opf": {"spec_version": "1.0.0"}})
              and not imported_series_declared({"opf": {"spec_version": "one.three"}})
              and not imported_series_declared({"opf": {"spec_version": 2}})
              and not imported_series_declared({"opf": {}})
              and not imported_series_declared(None))

        # tomllib's RecursionError (a deeply-nested value, and a 1001-part key under the emulated cap) is a
        # StoreError naming both causes, so a long dotted key is never reported as nesting alone.
        def recursion_refusal(text, capped):
            try:
                if capped:
                    with _StKeyPartCap(capped):
                        _parse_store_toml("m/manifest.toml", text.encode("utf-8"))
                else:
                    _parse_store_toml("m/manifest.toml", text.encode("utf-8"))
            except StoreError as exc:
                return type(exc.__context__).__name__, str(exc)
            except Exception as exc:  # noqa: BLE001  an escape is a failed check, never a suite abort
                return "escaped " + type(exc).__name__, ""
            return "parsed", ""
        # Message-free: the class tomllib raised and this module's own refusal text, never the parser's or the
        # emulation's message, so a native 3.14.8 cap with any message of its own still passes.
        rec_head = ("cannot parse m/manifest.toml (input nesting is too deep or a dotted key has too many parts; "
                    "present but unparseable): ")
        for label, text, capped, kind in (("deep-nesting", _ST_DEEP_TOML, None, "RecursionError"),
                                          ("1001-part-key", _ST_LONG_KEY_TOML, RecursionError, "RecursionError"),
                                          ("many-part-key-memory", _ST_LONG_KEY_TOML, MemoryError, "MemoryError")):
            got_kind, got_text = recursion_refusal(text, capped)
            check("recursion-{}-names-both-causes".format(label),
                  got_kind == kind and got_text.startswith(rec_head))
        # The emulation injects the class it is given even when an underlying key-part cap (here the
        # emulation itself, standing in for a CPython 3.14.8 interpreter cap) refuses the key first with
        # RecursionError: the memory vector must observe MemoryError, never the underlying class.
        with _StKeyPartCap(RecursionError):
            got_kind, got_text = recursion_refusal(_ST_LONG_KEY_TOML, MemoryError)
        check("recursion-many-part-key-memory-independent-of-underlying-cap",
              got_kind == "MemoryError" and got_text.startswith(rec_head))

    finally:
        shutil.rmtree(base, ignore_errors=True)

    if failures:
        print("OPF-STORE SELF-TEST: FAIL ({} of {} checks failed)".format(len(failures), checked))
        for f in failures:
            print("  FAILED: {}".format(f))
        return 1
    print("OPF-STORE SELF-TEST: PASS ({} store-resolution, discovery, and manifest-schema checks)".format(
        checked))
    return 0


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-test"]:
        sys.exit(self_test())
    sys.exit(self_test())
