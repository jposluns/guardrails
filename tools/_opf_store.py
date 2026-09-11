#!/usr/bin/env python3
"""OPF (DevProcess) store resolution + manifest base/profile schema + discovery (OPF core-tooling U1).

Offline, stdlib only, fail-closed. This is the FOUNDATION module the later OPF units build on: it sets
the schema conventions and the resolution/discovery contract they follow, so it is deliberately strict
and idiomatic.

Two things live here, both from OPF-SPEC.md sections 4, 5, and 9:

  1. STORE RESOLUTION + DISCOVERY (spec 4.3 to 4.5). A DevProcess store is resolved from a PRODUCT
     REPOSITORY ROOT (the tool is aimed at one via --root, defaulting to the cwd) through a committed
     pointer `.opf.toml` (with an uncommitted `.opf.local.toml` override), NOT through a hardcoded path
     and NOT through `_gen_common.repo_root()`: an OPF store roots via the pointer or --root, not via a
     `.git` walk, because this repo is not itself a DevProcess adopter and an adopter store need not sit
     at a repo root the walk would find. Within the resolved STORE REPOSITORY ROOT the machine store is
     DISCOVERED as the single immediate subdirectory of `.working/` whose `manifest.toml` declares
     `standard = "devprocess"` in its `[devprocess]` base table (the standard name is `toml`, tried
     first, but the subdirectory is OBSERVED at each use, never hardcoded, spec 4.4/4.5).

  2. MANIFEST BASE/PROFILE SCHEMA + VALIDATOR (spec 9, 9.1). The base is the `[devprocess]` table plus
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

House SemVer parsing (`check_versions._parse`) is reused so the OPF tooling grades versions exactly as
the release gates do.

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
  - A mistyped `devprocess` token resolves to CANNOT-EVALUATE at BOTH a POINTER target and the default
    location (residual 17): a PRESENT `.working/` that carries no valid [devprocess] manifest is a
    present-but-invalid store, distinguishable from a fresh un-adopted repo (which has NO `.working/` at
    all) and never treated as absent, so `opf init` cannot overwrite it. Only a genuinely absent
    `.working/` with no pointer is NOT-ADOPTED (the `opf init` remedy).
  - A `dir:`/absolute-path pointer TARGET is opened by a component-by-component no-follow walk from the
    filesystem root, not through `Path.resolve()` (which would canonicalize symlinks away BEFORE the
    open): a symlinked store root or a symlinked ancestor of a pointer target is refused
    (CANNOT-EVALUATE), never silently followed off-tree, matching the contained no-follow discipline.
"""
import collections.abc
import operator
import os
import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _journal        # noqa: E402  contained (dir-fd, no-follow) readers + JournalError + containment probe
import _containment    # noqa: E402  the single race-free-primitive probe
from check_versions import _parse  # noqa: E402  the shipped bare-SemVer parser (major, minor, patch) or None

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    sys.exit("error: tools/_opf_store.py requires Python 3.11+ (tomllib).")


# --- fixed names and vocabularies (spec 4) ------------------------------------------------------------

POINTER_REL = ".opf.toml"              # committed store pointer, at the PRODUCT root (spec 4.3)
LOCAL_POINTER_REL = ".opf.local.toml"  # uncommitted machine-local override, resolved FIRST (spec 4.3)
WORKING_DIRNAME = ".working"           # fixed store-tree name at the STORE root (spec 4.4)
DEFAULT_MACHINE_SUBDIR = "toml"        # standard machine-store subdir name, tried first (spec 4.4)
MANIFEST_NAME = "manifest.toml"        # discovery marker filename (spec 4.5)
STANDARD_TOKEN = "devprocess"          # exact discovery token in [devprocess].standard (spec 4.5)
MAX_STORE_READ_BYTES = 1 << 20         # read cap for a contained store file (manifest/pointer); a larger
                                       # store input is refused rather than read unboundedly (SECA)

# Resolution outcomes.
RESOLVED = "RESOLVED"                  # a machine store was resolved and located
NOT_ADOPTED = "NOT-ADOPTED"           # no pointer and no default store: nothing to operate on (opf init)
# Validation outcomes.
VALID = "VALID"
INVALID = "INVALID"                    # a devprocess store whose schema is violated (a fail-closed finding)
# Shared fail-closed outcome.
CANNOT_EVALUATE = "CANNOT-EVALUATE"    # unreadable/unresolvable/ambiguous: stop, never a silent pass

# Base storage / posture / import-state vocabularies (spec 9).
LAYOUTS = ("inline", "per-record")
POSTURES = ("off", "warn", "required")
POSTURE_RANK = {"off": 0, "warn": 1, "required": 2}      # strictest wins (spec 11)
IMPORT_STATES = ("none", "partial", "complete")

# Base optional-capability modules (spec 9 [modules]; the roster is section 8.1's module families).
KNOWN_MODULES = ("governance", "delivery_assurance", "operational_policy",
                 "concurrent_operation", "decision_support")

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
}
# Module-tier types: type name -> (normative namespace, the module that must be enabled in [modules]).
MODULE_TYPES = {
    "maintainer_action": ("MA", "governance"),
    "maintainer_decision": ("MD", "governance"),
    "artifact": ("AR", "delivery_assurance"),
    "gate_run": ("GR", "delivery_assurance"),
    "release": ("RL", "delivery_assurance"),
    "waiver": ("WV", "delivery_assurance"),
    "mode": ("MO", "operational_policy"),
    "tier_assessment": ("TA", "operational_policy"),
    "session_lease": ("SL", "concurrent_operation"),
    "preference_pattern": ("PP", "decision_support"),
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
DEVPROCESS_KEYS = frozenset({"standard", "spec_version", "layout", "posture", "import_status"})
STORE_KEYS = frozenset({"sync_target"})
PROFILE_KEYS = frozenset({"version", "base_compat", "posture_floor", "required_modules",
                          "extension_namespace"})
PROFILE_OPTIONAL_EXTRA = frozenset({"verification_floor"})   # profile-namespaced extras a profile MAY add
TYPE_KEYS = frozenset({"namespace"})
PROVIDER_KEYS = frozenset({"handler", "roles"})
PROVIDER_ROLES = ("create", "auth", "sync")
VIEW_KEYS = frozenset({"kind", "sources", "target"})
VIEW_KINDS = ("deterministic", "composed")
DELIVERABLE_KEYS = frozenset({"kind", "target"})
DELIVERABLE_KINDS = ("curated",)
ARCHIVE_KEYS = frozenset({"period"})
ARCHIVE_PERIODS = ("year",)
UNMANAGED_KEYS = frozenset({"paths"})
VENDORS_KEYS = frozenset({"registered"})
TOP_LEVEL_TABLES = frozenset({"devprocess", "store", "modules", "profiles", "types", "providers",
                              "views", "deliverables", "archive", "unmanaged", "vendors"})

_NAMESPACE_OK = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
_EXTENSION_VENDOR_OK = frozenset("abcdefghijklmnopqrstuvwxyz0123456789-")   # ASCII x-<vendor> slug alphabet


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

def _open_root_fd(root):
    """Open a root directory fd with O_NOFOLLOW, so a symlinked final root component is refused rather
    than followed off-tree (the migrate.py/pin.py idiom). Raises OSError, mapped by the caller. Used for
    the PRODUCT root, the operator's trusted --root anchor."""
    return os.open(str(root), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)


def _open_dir_nofollow(abspath):
    """Open an ABSOLUTE directory path as a dir fd by walking each component from the filesystem root with
    O_DIRECTORY|O_NOFOLLOW, so a symlinked store root OR any symlinked ancestor is refused (ELOOP) rather
    than silently followed off-tree, matching the contained no-follow discipline (spec 4.5, 17; the
    symlink-resolution rule). `Path.resolve()` is deliberately NOT used to resolve a pointer target: it
    canonicalizes symlinks BEFORE the open, so a symlinked target would resolve away and open
    successfully. Raises OSError, which the caller maps to CANNOT-EVALUATE."""
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
    fd = os.open(parts[0], os.O_RDONLY | os.O_DIRECTORY)   # the filesystem root itself is never a symlink
    for comp in parts[1:]:
        try:
            nfd = os.open(comp, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
        finally:
            os.close(fd)                                    # close the parent whether or not the open raised
        fd = nfd
    return fd


def _open_store_root_fd(store_root, pointer):
    """Open a resolved store root as a dir fd. The DEFAULT location IS the product root (the operator's
    trusted --root anchor), opened final-component no-follow. A POINTER-named target (a `dir:`/absolute
    path) is opened by the no-follow walk from the filesystem root, so a symlinked store root or a
    symlinked ancestor is refused rather than followed. Raises OSError, mapped to CANNOT-EVALUATE."""
    if pointer:
        return _open_dir_nofollow(store_root)
    return _open_root_fd(store_root)


def _read_toml_contained(root_fd, relpath):
    """Read and parse a contained TOML file beneath root_fd, no-follow. Returns the parsed dict, or None
    when the file (or a parent) is absent. StoreError (a cannot-evaluate) on an unreadable file, a
    refused symlink, or a TOML/parse error: an unreadable input is a failure, never an empty pass."""
    try:
        st = _journal._lstat_contained(root_fd, relpath)
    except (_journal.JournalError, OSError) as exc:
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
    try:
        data, _ = _journal._read_contained(root_fd, relpath)
    except (_journal.JournalError, OSError) as exc:
        # _read_contained maps its open/read errors to JournalError, but its post-open os.fstat can still
        # raise a BARE OSError (a device/EIO-level failure) that would otherwise escape resolve_store
        # uncaught; catch OSError alongside JournalError here (as the _lstat_contained choke point above
        # already does) so any unreadable manifest/pointer is a fail-closed StoreError, never a crash
        # (check-fails-closed-on-unreadable).
        raise StoreError("cannot read {} ({})".format(relpath, exc))
    # Re-check the bytes ACTUALLY read against the cap: the pre-open lstat size is bound to the file as it
    # was BEFORE the open, so a store file swapped in or grown between that lstat and the open (a TOCTOU race
    # on an adopter-controlled tree) is refused here rather than parsed over the cap. Residual: the bytes are
    # read before this refusal, so a concurrently-growing file is bounded at classification, not mid-read
    # (SECA resource-bounds; disclose-guard-residuals).
    if len(data) > MAX_STORE_READ_BYTES:
        raise StoreError("{} read {} bytes, over the {}-byte store-read cap (a raced swap or growth past "
                         "the pre-open size; fail-closed)".format(relpath, len(data), MAX_STORE_READ_BYTES))
    try:
        return tomllib.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        # tomllib.TOMLDecodeError is a ValueError subclass, but an oversized BASE-10 integer literal (a
        # token past CPython's ~4300-digit limit) makes tomllib raise a BARE ValueError from int(), not a
        # TOMLDecodeError, so a hostile store file would otherwise escape resolve_store uncaught; catch the
        # ValueError base so a present-but-unparseable input is a fail-closed StoreError, never a crash
        # (check-fails-closed-on-unreadable; unreadable includes present-but-unparseable).
        raise StoreError("cannot parse {} ({})".format(relpath, exc))
    except RecursionError as exc:
        # A deeply-nested TOML value overflows the parser's recursion: a present-but-unparseable input is a
        # fail-closed StoreError (the caller routes it to CANNOT-EVALUATE), never an uncontrolled crash
        # (check-fails-closed-on-unreadable; unreadable includes present-but-unparseable).
        raise StoreError("cannot parse {} (input nesting is too deep; present but unparseable): {}".format(
            relpath, exc))


def _immediate_subdirs(store_root_fd, working_rel):
    """The immediate real subdirectory names of `working_rel` beneath store_root_fd, listed no-follow.
    A symlinked entry is skipped (never followed). Returns a sorted list of names. Raises StoreError
    when `working_rel` is present but is not a directory or is a refused symlink; returns None when it
    is absent (the caller reads absence as "no store found" rather than as an error)."""
    try:
        pfd, name = _journal._open_parent(store_root_fd, working_rel)
    except FileNotFoundError:
        return None
    except (OSError, _journal.JournalError) as exc:
        raise StoreError("cannot open the store tree parent of {} ({})".format(working_rel, exc))
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
        try:
            try:
                names = sorted(os.listdir(wfd))
            except OSError as exc:
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
        finally:
            os.close(wfd)
    finally:
        os.close(pfd)


# --- discovery (spec 4.5) ----------------------------------------------------------------------------

def discover_machine_store(store_root_fd, store_root):
    """Locate the single machine store under `.working/`. Returns (status, machine_dir, detail):
      "one"      exactly one immediate `.working/` subdir carries a devprocess manifest (machine_dir set)
      "absent"   `.working/` is absent (no store tree here at all: a genuinely un-adopted location)
      "present"  `.working/` EXISTS but no immediate subdir carries a valid devprocess manifest (a
                 present-but-invalid store, never treated as absent, spec residual 17)
      "multiple" more than one does (ambiguous: cannot choose)
    Raises StoreError (cannot-evaluate) on any read error, a non-directory `.working/`, or a refused
    symlink. The scan is EXHAUSTIVE and strict-unique: `toml` is the expected name (its match, when the
    result is otherwise unique, is reported as the machine_dir), but a second stray store is NOT masked
    (spec 4.5, residual 17)."""
    subdirs = _immediate_subdirs(store_root_fd, WORKING_DIRNAME)
    if subdirs is None:
        return "absent", None, "{}/ is absent".format(WORKING_DIRNAME)
    matches = []
    for name in subdirs:
        manifest_rel = "{}/{}/{}".format(WORKING_DIRNAME, name, MANIFEST_NAME)
        data = _read_toml_contained(store_root_fd, manifest_rel)   # StoreError propagates (fail-closed)
        if data is None:
            continue
        base = data.get("devprocess")
        if isinstance(base, dict) and base.get("standard") == STANDARD_TOKEN:
            matches.append(name)
    if not matches:
        return "present", None, "{}/ is present but no {}/*/{} declares standard = {!r}".format(
            WORKING_DIRNAME, WORKING_DIRNAME, MANIFEST_NAME, STANDARD_TOKEN)
    if len(matches) > 1:
        return "multiple", None, "{} machine stores declare the devprocess token: {}".format(
            len(matches), ", ".join(sorted(matches)))
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


def _read_pointer_target(product_root_fd, relpath):
    """The Target named by a pointer file at the product root, or None when the file is absent. Raises
    StoreError when the file is present but unreadable, unparseable, or carries no usable
    `[store].target` (a pointer that names nothing is malformed, not absent)."""
    data = _read_toml_contained(product_root_fd, relpath)
    if data is None:
        return None
    store = data.get("store")
    if not isinstance(store, dict) or "target" not in store:
        raise StoreError("{} has no [store].target".format(relpath))
    return classify_target(store["target"])


# --- resolution (spec 4.3) ---------------------------------------------------------------------------

def resolve_store(product_root):
    """Resolve the machine store from a product repository root (spec 4.3). Never raises for an expected
    outcome; returns a Resolution whose status is RESOLVED, NOT_ADOPTED, or CANNOT_EVALUATE.

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
    product_root = Path(os.path.abspath(product_root))
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
            return Resolution(CANNOT_EVALUATE, str(exc))
    finally:
        os.close(product_root_fd)

    if local is not None or committed is not None:
        # A pointer named the store: the override wins wholesale, else it completes with the committed
        # pointer (spec 4.3). From here every failure is CANNOT-EVALUATE, never a default fallback.
        target = local if local is not None else committed
        source = "local-override" if local is not None else "committed"
        try:
            store_root = _target_store_root(target, product_root)
        except StoreError as exc:
            return Resolution(CANNOT_EVALUATE, str(exc), target=target, pointer_source=source,
                              product_root=product_root)
        res = _resolve_at(store_root, source, target, pointer=True)
        res.product_root = product_root
        return res

    # Neither pointer file exists: try the default in-repo location.
    res = _resolve_at(product_root, "default", None, pointer=False)
    res.product_root = product_root
    return res


def _resolve_at(store_root, source, target, pointer):
    """Open the store root and run discovery there. `pointer` selects the fail-closed policy: a pointer
    that resolves to a root with no (or multiple) machine store is CANNOT-EVALUATE, while the default
    fallback reports NOT-ADOPTED for a genuinely empty location (spec 4.3/4.5)."""
    store_root = Path(store_root)
    if not store_root.exists():
        detail = "pointer target {} does not exist".format(store_root) if pointer \
            else "default store location {} does not exist".format(store_root)
        # An absent pointer target is unresolvable (cannot-evaluate); an absent default is not-adopted.
        return Resolution(CANNOT_EVALUATE if pointer else NOT_ADOPTED, detail, target=target,
                          pointer_source=source)
    if not store_root.is_dir():
        return Resolution(CANNOT_EVALUATE, "store root {} is not a directory".format(store_root),
                          target=target, pointer_source=source)
    try:
        # A pointer target is opened by the no-follow walk from the filesystem root (a symlinked store
        # root or ancestor is refused, MAJOR 2); the default location is the trusted product-root anchor.
        store_root_fd = _open_store_root_fd(store_root, pointer)
    except OSError as exc:
        return Resolution(CANNOT_EVALUATE, "cannot open store root {} ({})".format(store_root, exc),
                          target=target, pointer_source=source)
    try:
        try:
            status, machine_dir, detail = discover_machine_store(store_root_fd, store_root)
        except StoreError as exc:
            return Resolution(CANNOT_EVALUATE, str(exc), store_root=store_root, target=target,
                              pointer_source=source)
    finally:
        os.close(store_root_fd)

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
    # `.working/` carrying no valid [devprocess] manifest is a present-but-invalid store that must never
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


def _safe_display(value):
    """Render a value for a FINDING MESSAGE (or a controlled-refusal message) without tripping CPython's
    integer-string-conversion limit. tomllib parses a hexadecimal, octal, or binary integer literal
    (0x.../0o.../0b...) into an arbitrarily-large Python int with NO digit limit (the limit applies to
    base-10 only), so a value read from a parsed manifest, counters, or version file can be an oversized
    int whose repr() (or the repr() of a container holding one) raises ValueError; a message that formats
    such a value with {!r}/{}/str()/repr() would then crash with an uncontrolled ValueError. This returns
    repr(value) for every value whose repr is well-formed, byte for byte, so a small or normal value
    renders exactly as it did before; only for a value whose repr trips the limit does it return a
    deterministic, bounded marker, so the message is produced rather than crashing (fail-closed;
    guard-input-soundness). It is the shared message-rendering guard for the finding-message idiom across
    the three OPF pass-A validators (store, schema, release). Like _sorted_key_names it is for MESSAGE
    rendering ONLY; it is deliberately NOT used on the byte-stable coverage digest, where an oversized int
    fails closed with a ReleaseError instead (a marker in the digest would change the bytes; spec 6.1)."""
    try:
        return repr(value)
    except ValueError:
        if isinstance(value, int) and not isinstance(value, bool):
            return "<oversized-int: {} bits>".format(value.bit_length())
        return "<oversized-value>"


def _safe_str(value):
    """The str()-style companion of _safe_display, for a finding-message position that renders a value with
    {} (no surrounding repr quotes) rather than {!r}: a version string interpolated into a `release #N
    (<version>)` label, for instance. Returns str(value) for every value whose str is well-formed, byte for
    byte, so a normal string or int renders exactly as it did before; only a value whose str trips the
    base-10 integer-string-conversion limit (an oversized non-decimal int parsed from TOML, or a container
    holding one) returns the same bounded marker _safe_display uses (fail-closed). It is for MESSAGE
    rendering only, never the byte-stable coverage digest."""
    try:
        return str(value)
    except ValueError:
        if isinstance(value, int) and not isinstance(value, bool):
            return "<oversized-int: {} bits>".format(value.bit_length())
        return "<oversized-value>"


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


def validate_manifest(data, supported_profiles=None):
    """Validate a parsed manifest against the base schema and, for each SUPPORTED profile, the profile
    schema. Returns a ManifestValidation.

    supported_profiles is a mapping {profile_name: iterable of supported MAJOR ints}; empty or None
    means BASE-ONLY (every declared profile is recorded as present-but-unevaluated, never enforced,
    fail-SAFE). A profile the mapping does not name, or whose declared major is not in the supported
    set, is likewise recorded unevaluated. A profile the mapping DOES cover is enforced and FAILS
    CLOSED (a finding) on an unreadable, base-incompatible, weakening, or mis-registered instance
    (spec 9.1).

    Outcome: CANNOT-EVALUATE when the manifest is not a table or its `[devprocess]` base is absent or
    does not declare the devprocess token (it is not identifiably a devprocess store); INVALID when it
    is a devprocess store that violates the schema; VALID otherwise."""
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
    base = data.get("devprocess")
    if not isinstance(base, dict) or base.get("standard") != STANDARD_TOKEN:
        return ManifestValidation(CANNOT_EVALUATE,
                                  ["[devprocess].standard is absent or is not {!r} (not identifiably a "
                                   "devprocess store)".format(STANDARD_TOKEN)])

    findings = []
    _validate_top_level(data, findings)
    spec_tuple = _validate_base(base, findings)
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


def _validate_base(base, findings):
    """Validate the [devprocess] base table; returns the parsed spec_version tuple or None."""
    extra = set(base) - DEVPROCESS_KEYS
    if extra:
        findings.append("[devprocess] unknown key(s): {}".format(", ".join(_sorted_key_names(extra))))
    missing = [k for k in DEVPROCESS_KEYS if k not in base]
    if missing:
        findings.append("[devprocess] missing required key(s): {}".format(", ".join(sorted(missing))))
    spec_tuple = None
    sv = base.get("spec_version")
    if "spec_version" in base:
        spec_tuple = _parse(sv) if isinstance(sv, str) else None
        if spec_tuple is None:
            findings.append("[devprocess].spec_version {} is not a bare SemVer".format(_safe_display(sv)))
    # Each closed-vocabulary field is type-checked BEFORE its membership test (MAJOR 3), so a wrong-typed
    # value (e.g. posture as a list) is a fail-closed finding here rather than an unhashable-value crash
    # in a later rank/membership test.
    _check_enum(base, "layout", LAYOUTS, "[devprocess]", findings)
    _check_enum(base, "posture", POSTURES, "[devprocess]", findings)
    _check_enum(base, "import_status", IMPORT_STATES, "[devprocess]", findings)
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
        return ManifestValidation(CANNOT_EVALUATE, [str(exc)])
    finally:
        os.close(store_root_fd)
    if data is None:
        # Discovery already read this file, so its disappearance now is a race/fail-closed error.
        return ManifestValidation(CANNOT_EVALUATE, ["{} vanished after discovery".format(manifest_rel)])
    return validate_manifest(data, supported_profiles)


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
    def manifest_text(standard=STANDARD_TOKEN, spec_version="1.0.0", posture="required",
                      with_aiqt=False, aiqt_floor="required", aiqt_compat=">=1.0.0 <2.0.0",
                      aiqt_version="1.0.0", register_xaiqt=True, ops_enabled=True, with_acme=False,
                      extra_top=""):
        lines = [
            "[devprocess]",
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

        # 4: zero manifests at a POINTER target -> CANNOT-EVALUATE (pointer promised a store).
        empty_store = base / "empty-store"
        (empty_store / WORKING_DIRNAME).mkdir(parents=True)
        pr = build_store(make_working=False, pointer='[store]\ntarget = "dir:{}"\n'.format(empty_store))
        check("zero-at-pointer-cannot-eval", resolve_store(pr).status == CANNOT_EVALUATE)

        # 5: two machine stores under the default .working -> CANNOT-EVALUATE (ambiguous).
        root = build_store(machine_subdirs={"toml": manifest_text(), "toml2": manifest_text()})
        check("two-manifests-cannot-eval", resolve_store(root).status == CANNOT_EVALUATE)

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

        # 8b: validate() on a non-devprocess / not-a-table input -> CANNOT-EVALUATE.
        check("not-a-table-cannot-eval", validate_manifest([]).status == CANNOT_EVALUATE)
        check("no-base-cannot-eval", validate_manifest({"store": {}}).status == CANNOT_EVALUATE)

        # 8c: an unknown top-level table AND an unknown [devprocess] key are each findings. The input adds
        # BOTH a top-level [bogus] table and an unknown key UNDER [devprocess], so the comment matches what
        # is actually exercised.
        import tomllib as _t
        m_extra = _t.loads(manifest_text(extra_top="[bogus]\nx = 1").replace(
            'import_status = "none"', 'import_status = "none"\nmystery_key = 1'))
        mv_extra = validate_manifest(m_extra)
        check("unknown-top-table-invalid", mv_extra.status == INVALID)
        check("unknown-top-table-named", any("bogus" in f for f in mv_extra.findings))
        check("unknown-devprocess-key-named", any("mystery_key" in f for f in mv_extra.findings))

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
        prev_cwd = os.getcwd()
        try:
            os.chdir(str(base))
            rel_res = resolve_store(Path(rel_prod.name))       # a cwd-relative product root
        finally:
            os.chdir(prev_cwd)
        check("relative-root-resolves", rel_res.status == RESOLVED)
        check("relative-root-matches-abs", rel_res.store_root == abs_res.store_root)
        # ---- reconcile-draft fix vectors (fail pre-fix, pass post-fix) --------------------------------
        # M1: an oddly-named .working subdir (_check_rel refuses a backslash) -> CANNOT-EVALUATE, never an
        # uncaught JournalError out of resolve_store.
        _m1subs = dict()
        _m1subs[DEFAULT_MACHINE_SUBDIR] = manifest_text()
        _m1subs["bad" + chr(92) + "name"] = manifest_text()
        m1_root = build_store(machine_subdirs=_m1subs)
        check("m1-oddly-named-subdir-cannot-eval", resolve_store(m1_root).status == CANNOT_EVALUATE)

        # S1.3: a listing I/O error inside .working fails closed, never read as an empty (no-store) dir.
        s13_root = build_store(manifest=manifest_text())
        _real_listdir = os.listdir
        os.listdir = (lambda x: (_ for _ in ()).throw(OSError(5, "EIO"))
                      if isinstance(x, int) else _real_listdir(x))
        try:
            check("s13-listdir-io-error-cannot-eval", resolve_store(s13_root).status == CANNOT_EVALUATE)
        finally:
            os.listdir = _real_listdir

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

        # m8: a store file larger than the read cap is refused rather than read unboundedly.
        big_manifest = manifest_text() + chr(10) + "#" + ("x" * (MAX_STORE_READ_BYTES + 16))
        m8_root = build_store(manifest=big_manifest)
        check("m8-oversized-manifest-cannot-eval", resolve_store(m8_root).status == CANNOT_EVALUATE)

        # ---- round-2 reconcile-draft fix vectors (fail pre-fix, pass post-fix) ------------------------
        # The FIFO-hang alarm marker MUST NOT derive from OSError: a reader's own `except OSError`
        # (read_frames, read_lock_owner, _read_contained) would launder an OSError-derived marker such as
        # TimeoutError into a JournalError, so a genuine writer-less-FIFO hang would read as a refusal and the
        # check would pass while blocking. A distinct non-OSError marker propagates out of the reader instead,
        # so a hang is a check FAILURE, never a silent slow pass (self-test-discrimination).
        import signal as _signal

        class _HangMarker(Exception):
            pass

        def _refused_no_hang(thunk):
            """True when thunk() fails closed with a JournalError inside a 2s alarm; False when it HANGS (the
            marker fires) so a writer-less-FIFO blocking-open regression is a check failure, not a hung suite."""
            _prev = _signal.signal(_signal.SIGALRM, lambda *a: (_ for _ in ()).throw(_HangMarker()))
            try:
                _signal.setitimer(_signal.ITIMER_REAL, 2.0)
                try:
                    thunk()
                    return False
                except _journal.JournalError:
                    return True
                except _HangMarker:
                    return False
            finally:
                _signal.setitimer(_signal.ITIMER_REAL, 0)
                _signal.signal(_signal.SIGALRM, _prev)

        # M2: _read_contained does not hang on a writer-less FIFO (the raced regular-file->FIFO swap); it
        # returns a fail-closed JournalError at once. The non-OSError marker makes a blocking regression a
        # check failure (the earlier TimeoutError marker, an OSError subclass, was laundered to JournalError
        # by _read_contained's own `except OSError` and so passed even while blocking).
        _fd_dir = base / "m2-fifo"; _fd_dir.mkdir()
        os.mkfifo(str(_fd_dir / "f"))
        _rfd = os.open(str(_fd_dir), os.O_RDONLY | os.O_DIRECTORY)
        try:
            check("m2-fifo-no-hang-refused",
                  _refused_no_hang(lambda: _journal._read_contained(_rfd, "f")))
        finally:
            os.close(_rfd)

        # NEW-2: the FIFO-hang class survived at three sibling _journal readers (read_frames, read_lock_owner,
        # _read_at); each now opens O_NONBLOCK so a writer-less FIFO is refused at the fstat gate at once,
        # never a hang. A hostile on-disk tree can pre-plant these paths.
        _rf_dir = base / "n2-read-frames"; _rf_dir.mkdir()
        os.mkfifo(str(_rf_dir / "frames.log"))
        check("new2-read-frames-fifo-no-hang", _refused_no_hang(lambda: _journal.read_frames(_rf_dir)))
        _rl_dir = base / "n2-read-lock-owner"; _rl_dir.mkdir()
        os.mkfifo(str(_rl_dir / "lock"))
        check("new2-read-lock-owner-fifo-no-hang", _refused_no_hang(lambda: _journal.read_lock_owner(_rl_dir)))
        _ra_dir = base / "n2-read-at"; _ra_dir.mkdir()
        os.mkfifo(str(_ra_dir / "f"))
        _rafd = os.open(str(_ra_dir), os.O_RDONLY | os.O_DIRECTORY)
        try:
            check("new2-read-at-fifo-no-hang", _refused_no_hang(lambda: _journal._read_at(_rafd, "f", "f")))
        finally:
            os.close(_rafd)

        # NEW-1: a hostile store file with an oversized BASE-10 integer literal makes tomllib raise a bare
        # ValueError (not TOMLDecodeError); it maps to a fail-closed StoreError -> CANNOT-EVALUATE, never an
        # uncaught crash out of resolve_store.
        n1_root = build_store(manifest="[devprocess]\nbig = " + "9" * 5000 + "\n")
        # MINOR-3: run the probe INSIDE _guard so a reverted fix (resolve_store no longer mapping the bare
        # ValueError from tomllib's int() to CANNOT-EVALUATE) yields a NAMED counted failure, not an uncaught
        # traceback that aborts the suite. Still passes only on CANNOT-EVALUATE (no weakened detection).
        check("new1-oversized-int-literal-cannot-eval",
              _guard(lambda: resolve_store(n1_root).status) == CANNOT_EVALUATE)

        # NEW-3: an OSError from _read_contained (e.g. a post-open fstat EIO) maps to a fail-closed StoreError
        # -> CANNOT-EVALUATE, never an uncaught OSError out of resolve_store.
        n3_root = build_store(manifest=manifest_text())
        _real_rc = _journal._read_contained
        def _rc_oserror(_rfd_arg, _rel_arg):
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
        try:
            check("minor1-read-frames-oversize-refused",
                  _guard(lambda: (_journal.read_frames(_m1_frames), "read")[1]) == "RAISED")
            check("minor1-read-lock-owner-oversize-refused",
                  _guard(lambda: (_journal.read_lock_owner(_m1_lock), "read")[1]) == "RAISED")
        finally:
            _journal._MAX_JOURNAL_READ_BYTES = _real_jcap

        # ---- MINOR-2: symlink-race containment (fail pre-fix, pass post-fix) ---------------------------
        # publish and _truncate_log open frames.log CONTAINED (O_NOFOLLOW + dir-fd relative), so a symlinked
        # frames.log cannot redirect the append/ftruncate onto a victim file. Pre-fix each followed a
        # re-resolved absolute path and would mutate the victim (publish appends, _truncate_log truncates it
        # to 0); post-fix each fails closed (JournalError) and leaves the victim byte-for-byte intact. Both a
        # refusal check and a victim-intact check discriminate.
        _victim = base / "minor2-victim"
        _victim.write_bytes(b"VICTIM-INTACT")
        _m2_pub = base / "minor2-publish"; _m2_pub.mkdir()
        os.symlink(str(_victim), str(_m2_pub / "frames.log"))
        check("minor2-publish-symlink-refused",
              _guard(lambda: _journal.publish(_m2_pub, _journal.F_INTENT, {"txn": "t", "ops": []})) == "RAISED")
        check("minor2-publish-victim-intact", _victim.read_bytes() == b"VICTIM-INTACT")
        _m2_tr = base / "minor2-truncate"; _m2_tr.mkdir()
        os.symlink(str(_victim), str(_m2_tr / "frames.log"))
        check("minor2-truncate-symlink-refused",
              _guard(lambda: _journal._truncate_log(_m2_tr, 0)) == "RAISED")
        check("minor2-truncate-victim-intact", _victim.read_bytes() == b"VICTIM-INTACT")

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
    sys.exit(self_test())
