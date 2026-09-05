#!/usr/bin/env python3
"""QA-suite surface discovery seam and the shared result contract (QA foundation).

Every QA audit discovers the adopter's assurance surfaces (backlog, gate roster, git, register,
session state, session transcript, held reference) through ONE seam, and emits its verdict in ONE
result contract, so a "missing surface" can never quietly read as a passing audit.

DISCOVERY (the seam). Surfaces are declared in `.aiqt/assurance.toml` and resolved by config, never by
importing adopter Python. Resolution order, fail-loud (Section D of the QA-suite plan):
  1. an explicit `--config PATH`
  2. the AIQT_ASSURANCE_CONFIG environment variable
  3. the nearest parent `.aiqt/assurance.toml` walking up from the start directory
  4. narrow portable defaults (root TODO.md as a GFM task-list backlog, tools/check_*.py as the gate
     roster, the current git repo, the Quality workflow), with NO inferred tracker, transcript, inbox,
     or decision authority.
An explicit `--config` or AIQT_ASSURANCE_CONFIG that is absent, unreadable, or malformed is a LOUD
error (a raised ConfigError), never a silent fall-through to a lower step: a caller that pinned a config
meant that config. Only when neither step 1 nor step 2 is set does resolution walk to steps 3 and 4.

RESULT CONTRACT. A normalized surface, and every audit verdict built on it, carries a status drawn from
a fixed four-value set:
  PASS          the surface resolved / the audit's property holds, on observed evidence
  FAIL          the audit's property is observably violated
  UNVERIFIABLE  the evidence needed to decide is missing, unreadable, or ambiguous
  SKIP          an OPTIONAL surface is disabled by config (a visible, declared no-op, never silent)
Two invariants the contract enforces, so a false green is unreachable:
  - missing evidence can NEVER read as PASS: an absent surface resolves to UNVERIFIABLE, not PASS;
  - an OPTIONAL-disabled surface is SKIP, never "malformed-required" and never PASS.
A REQUIRED surface that is disabled in config is a configuration fault (UNVERIFIABLE, malformed), kept
distinct from an OPTIONAL-disabled SKIP.

stdlib only (tomllib is stdlib on 3.11+). `--self-test` proves the discriminating property: the adapter
returns UNVERIFIABLE (never PASS) on a missing REQUIRED surface, so deleting that guard fails the test.
"""
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    sys.exit("error: _qa_adapter.py requires Python 3.11+ (tomllib).")

# --- the shared result contract ---------------------------------------------------------------------
PASS = "PASS"
FAIL = "FAIL"
UNVERIFIABLE = "UNVERIFIABLE"
SKIP = "SKIP"
STATUSES = (PASS, FAIL, UNVERIFIABLE, SKIP)

# Unavailability kinds, carried on a surface so a digest can DISTINGUISH required-unavailable from
# optional-disabled (a false-green dashboard is the risk this labelling prevents).
KIND_AVAILABLE = "available"
KIND_REQUIRED_UNAVAILABLE = "required-unavailable"
KIND_OPTIONAL_UNAVAILABLE = "optional-unavailable"
KIND_OPTIONAL_DISABLED = "optional-disabled"
KIND_REQUIRED_DISABLED = "required-disabled-malformed"
KIND_UNKNOWN_ADAPTER = "unknown-adapter"
KIND_UNDECLARED = "undeclared-surface"
KIND_ADAPTER_ERROR = "adapter-error"
# A config that binds a canonically-recognized surface to a DIFFERENT adapter type than its canonical one
# (e.g. rebinding backlog from gfm-task-list to record-file to point it at any present file) is a config
# fault, kept distinct so a substituted adapter resolves UNVERIFIABLE/adapter-mismatch, never a PASS.
KIND_ADAPTER_MISMATCH = "adapter-mismatch"

RESULT_SCHEMA = "aiqt-qa-result/1"

# The mandatory keys every result object carries. PRESENCE is required for EVERY status (a missing key is
# refused before any type or consistency check), so a result that OMITS a key can never serialize as a clean
# line. `surface` above all: it is legitimately None-VALUED, so without a presence check a missing 'surface'
# key would be indistinguishable from surface=None (read with .get()) and slip through. The value-level
# checks below then constrain each present key.
MANDATORY_RESULT_KEYS = ("schema", "audit", "status", "summary", "surface", "required", "kind", "evidence")


class ConfigError(Exception):
    """A pinned config (explicit --config or AIQT_ASSURANCE_CONFIG) is absent, unreadable, or malformed.
    Raised loudly so resolution never silently falls through to a lower-precedence source."""


def make_result(audit, status, summary, surface=None, required=None, kind=None, evidence=None):
    """Build a normalized result object. `evidence` is a list of provenance strings (path:line, a git
    ref, a run id): the located basis for the verdict, never the verdict itself. `evidence` must be a
    LIST; a non-list (a bare string above all) is a LOUD error, never coerced with list(), because
    list('x:1') SPLITS the string into single characters ['x', ':', '1'], each of which would then satisfy
    the non-empty-string-list check and let a hollow PASS through. Every result is routed through the
    shared validate_result, the SINGLE choke point, so no construction path can hand back a malformed
    result: a status outside the fixed set, or (for a PASS) a missing available kind or evidence that is
    not a non-empty list of non-empty strings, is refused here, at construction, not only at emit."""
    if status not in STATUSES:
        raise ValueError("status {!r} is not one of {}".format(status, ", ".join(STATUSES)))
    if evidence is None:
        evidence = []
    if not isinstance(evidence, list):
        raise ValueError("a result's evidence must be a list, got {}".format(type(evidence).__name__))
    return validate_result({
        "schema": RESULT_SCHEMA,
        "audit": audit,
        "status": status,
        "summary": summary,
        "surface": surface,
        "required": required,
        "kind": kind,
        "evidence": list(evidence),
    })


def validate_result(result):
    """Enforce the mutual consistency of a result-contract object so a caller cannot hand-build a false
    green (for example status PASS with a required-unavailable kind and no evidence). The result must
    carry the exact RESULT_SCHEMA tag: a substituted or missing schema is a different contract and is
    refused for every status, before any status/kind/evidence check. `evidence` must be a
    LIST; a PASS additionally requires it to be a NON-EMPTY list of NON-EMPTY strings, so an empty string or
    a non-list can never stand in for located evidence. A PASS must carry the available kind, and a SKIP the
    optional-disabled kind. The available kind is legitimate on a PASS and on a FAIL: a FAIL on an AVAILABLE
    surface is a real verdict (the surface was present and probed, and the audit's own check observed the
    property violated), so only UNVERIFIABLE and SKIP, the missing-evidence and declared-no-op states, may
    not wear an 'available' label. Raises ValueError on an inconsistent result; returns it unchanged when
    consistent. is_pass alone checks only the status field, so this is the guard that keeps status, kind, and
    evidence from disagreeing."""
    # STRUCTURAL PRESENCE: every mandatory key is PRESENT for EVERY status, refused before any value-level
    # check, so no construction path (make_result, emit, or a hand-built dict) can OMIT a key. This is what
    # keeps a missing None-valued 'surface' key from reading as surface=None; the type/value checks below
    # then constrain each present key.
    for key in MANDATORY_RESULT_KEYS:
        if key not in result:
            raise ValueError("a result is missing the mandatory key {!r}".format(key))
    # The schema tag is part of the contract, not decoration: a result carrying a SUBSTITUTED schema
    # ("aiqt-qa-result/999") or NO schema field is written to a different, unhonoured contract and is
    # refused here for EVERY status, so a substitution or omission can never serialize as a clean line.
    if result.get("schema") != RESULT_SCHEMA:
        raise ValueError("a result's schema must be {!r}, got {!r}".format(RESULT_SCHEMA, result.get("schema")))
    status = result.get("status")
    if status not in STATUSES:
        raise ValueError("status {!r} is not one of {}".format(status, ", ".join(STATUSES)))
    # STRUCTURAL COMPLETENESS: the mandatory identity fields are present and correctly typed for EVERY status,
    # so no construction path (make_result, emit, or a hand-built dict) yields a structurally-incomplete or
    # wrong-typed result. `audit` and `summary` name the audit and its verdict (non-empty strings); `surface`
    # is the surface name (a string) or None when the result is not tied to one; `required` is a bool.
    for field in ("audit", "summary"):
        value = result.get(field)
        if not isinstance(value, str) or not value:
            raise ValueError("a result's {!r} must be a non-empty string, got {!r}".format(field, value))
    surface = result.get("surface")
    if surface is not None and not isinstance(surface, str):
        raise ValueError("a result's 'surface' must be a string or None, got {!r}".format(surface))
    required = result.get("required")
    if not isinstance(required, bool):
        raise ValueError("a result's 'required' must be a bool, got {!r}".format(required))
    # CROSS-FIELD CONSISTENCY: a REQUIRED surface can never be SKIP. SKIP is the OPTIONAL-disabled declared
    # no-op, so a required=true SKIP is a structurally inconsistent combination, refused here (kept distinct
    # from a required surface disabled in config, which resolves UNVERIFIABLE/required-disabled upstream).
    if status == SKIP and required:
        raise ValueError("a SKIP result cannot be marked required (a required surface is never a SKIP)")
    kind = result.get("kind")
    evidence = result.get("evidence")
    if not isinstance(evidence, list):
        raise ValueError("a result's evidence must be a list, got {}".format(type(evidence).__name__))
    # Evidence ELEMENTS must be non-empty strings for EVERY status, uniformly, not only for a PASS: a FAIL
    # or UNVERIFIABLE carrying evidence=[7] or [""] is a malformed provenance record and is refused here.
    # A non-PASS status may legitimately carry an EMPTY list (no located evidence); a PASS may not.
    if not all(isinstance(e, str) and e for e in evidence):
        raise ValueError("a result's evidence must be a list of non-empty strings")
    if status == PASS:
        if kind != KIND_AVAILABLE:
            raise ValueError("a PASS result must carry the {!r} kind, got {!r}".format(KIND_AVAILABLE, kind))
        if not evidence:
            raise ValueError("a PASS result must carry a non-empty list of non-empty evidence strings")
    elif status == SKIP:
        # A SKIP is the OPTIONAL-disabled declared no-op, so it carries EXACTLY the optional-disabled kind:
        # an inconsistent kind (required-unavailable, available, an arbitrary string) or a non-string kind on
        # a SKIP is refused, the same status/kind consistency the contract holds the other statuses to.
        if kind != KIND_OPTIONAL_DISABLED:
            raise ValueError("a SKIP result must carry the {!r} kind, got {!r}".format(
                KIND_OPTIONAL_DISABLED, kind))
    elif status == UNVERIFIABLE and kind == KIND_AVAILABLE:
        raise ValueError("kind {!r} cannot pair with status {!r}".format(KIND_AVAILABLE, status))
    return result


def emit(result):
    """Serialize a result object to a single deterministic JSON line (sorted keys, no wall clock in the
    object itself), so an audit's output is a stable, parseable evidence record. Every result the module
    emits is routed through validate_result first, so an inconsistent result (a PASS with an unavailable
    kind or no evidence) can never be serialized as a clean line."""
    validate_result(result)
    return json.dumps(result, sort_keys=True)


def is_pass(result):
    return result.get("status") == PASS


# --- config loading + resolution --------------------------------------------------------------------
ENV_VAR = "AIQT_ASSURANCE_CONFIG"
CONFIG_RELPARTS = (".aiqt", "assurance.toml")

# Narrow portable defaults (resolution step 4). No inferred tracker/transcript/inbox/decision authority.
PORTABLE_DEFAULTS = {
    "schema-version": 1,
    "surfaces": {
        "backlog": {"adapter": "gfm-task-list", "required": True, "enabled": True, "path": "TODO.md"},
        "gate_roster": {"adapter": "glob-roster", "required": True, "enabled": True,
                        "dir": "tools", "pattern": "check_*.py"},
        "git": {"adapter": "git", "required": True, "enabled": True,
                "protected-branch": "main", "remote-landing-provider": "github"},
        "register": {"adapter": "record-file", "required": False, "enabled": False, "path": ""},
        "session_state": {"adapter": "record-file", "required": False, "enabled": False, "path": ""},
        "session": {"adapter": "transcript-inbox", "required": False, "enabled": False},
        "reference": {"adapter": "held-source", "required": False, "enabled": False},
    },
}

# The canonical roster of surfaces every QA discovery is expected to account for. discover_all enumerates
# this whole set (plus any extra a config declares), so a REQUIRED surface omitted entirely from a config
# resolves UNVERIFIABLE rather than being silently skipped.
EXPECTED_SURFACES = tuple(PORTABLE_DEFAULTS["surfaces"])

# The KNOWN schema versions this loader understands. A schema-version that parses as an integer but is
# not a known version (e.g. 999) is a loud ConfigError, never loaded on the strength of being an int: an
# unknown schema is a config written for a different, unhonoured contract, not a silent accept.
SUPPORTED_SCHEMA_VERSIONS = (1,)

# The adapter-specific config fields each shipped adapter reads, all string-typed. load_config type-checks
# every field an adapter declares (a list-valued 'protected-branch' or a table-valued
# 'remote-landing-provider' is a loud ConfigError), so a wrong-typed adapter field can never load and then
# read as a PASS. This mirrors the ADAPTERS registry below; a new adapter's string fields are added here.
ADAPTER_STR_FIELDS = {
    "gfm-task-list": ("path",),
    "glob-roster": ("dir", "pattern"),
    "git": ("protected-branch", "remote-landing-provider"),
    "record-file": ("path",),
    "transcript-inbox": ("path",),
    "held-source": ("path",),
}


def config_arg(argv):
    """Extract the value of a `--config` option, in either the separate `--config PATH` or the `=`-joined
    `--config=PATH` form. Every malformed spelling is a LOUD error (ValueError), never a silent fall-through
    to a lower-precedence resolution step, because a caller that typed --config meant to pin a config:
      - `--config` with no following token (it is the last one) has no operand;
      - `--config ""` (an empty operand) pins nothing;
      - `--config` immediately followed by another option (a leading '-', e.g. `--config --self-test`) has
        no operand, and the next option is NOT swallowed as the path;
      - `--config=` with an empty value pins nothing;
      - a DUPLICATE `--config` (either form) is ambiguous, refused rather than silently first-wins.
    Returns None when --config is absent."""
    found = False
    value = None
    i = 0
    while i < len(argv):
        tok = argv[i]
        if tok == "--config":
            if found:
                raise ValueError("--config given more than once")
            found = True
            if i + 1 >= len(argv):
                raise ValueError("--config requires a path operand")
            nxt = argv[i + 1]
            if nxt.startswith("-"):
                raise ValueError("--config requires a path operand, got option {!r}".format(nxt))
            if nxt == "":
                raise ValueError("--config requires a non-empty path operand")
            value = nxt
            i += 2
            continue
        if tok.startswith("--config="):
            if found:
                raise ValueError("--config given more than once")
            found = True
            v = tok[len("--config="):]
            if v == "":
                raise ValueError("--config requires a non-empty path operand")
            value = v
            i += 1
            continue
        i += 1
    return value


def reject_unknown_options(argv, known_flags):
    """Return the first UNRECOGNIZED option token in argv, or None when every token is recognized. A token is
    recognized when it is a known flag (in `known_flags`), or part of a --config option (either `--config
    PATH` with its separate operand, or `--config=PATH`); anything else (a bare unknown flag such as
    `--self-testx`, or a stray positional) is unrecognized. The caller turns a non-None return into a LOUD
    exit 2, so an unknown option can never silently run a default path and a misspelled `--self-testx` can
    never masquerade as `--self-test` (or silently skip it). config_arg has already validated the --config
    operand when this runs, so this only SKIPS it, never re-validates it."""
    i = 0
    while i < len(argv):
        tok = argv[i]
        if tok == "--config":
            i += 2  # skip the option and its (already-validated) separate operand
            continue
        if tok.startswith("--config="):
            i += 1
            continue
        if tok in known_flags:
            i += 1
            continue
        return tok
    return None


def load_config(path):
    """Parse an assurance.toml. Raises ConfigError on an absent, unreadable, or malformed file, so a
    pinned config that cannot be honoured is loud, never a silent empty pass. Presence is classified with
    the shared _classify_presence (an S_ISREG check BEFORE any open) so a NON-REGULAR pinned config, a FIFO
    above all whose open() would BLOCK waiting for a writer, fails closed as a ConfigError rather than
    hanging the load; a broken symlink is likewise a loud present-but-unreadable fault, and only a genuinely
    absent path or a readable regular file passes the classifier to the read below."""
    p = Path(path)
    state, why = _classify_presence(p)
    if state == _ABSENT:
        raise ConfigError("cannot read assurance config {}: no such file".format(path))
    if state == _UNREADABLE:
        raise ConfigError("assurance config {} is present but unreadable ({}); fail closed".format(path, why))
    try:
        raw = p.read_bytes()
    except OSError as exc:
        raise ConfigError("cannot read assurance config {}: {}".format(path, exc))
    try:
        cfg = tomllib.loads(raw.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise ConfigError("malformed assurance config {}: {}".format(path, exc))
    # SCHEMA validation, past mere parseability: the config must be a table with a [surfaces] table, and
    # each declared surface must be a table whose `adapter` is a string and whose `required`/`enabled`
    # (where present) are booleans. A wrong-typed or structurally-invalid entry is a loud ConfigError, not
    # a silent empty pass. (tomllib rejects a TOML integer standing in for a bool, so isinstance(_, bool)
    # is the right test: an int flag would already have parsed as int, which this refuses.)
    if not isinstance(cfg, dict):
        raise ConfigError("assurance config {} is not a table".format(path))
    # REQUIRED top-level keys must be PRESENT and correctly typed, so a config that OMITS a mandatory key
    # (or gives it a wrong-typed value, e.g. a string-valued schema-version) is a loud fault, never a
    # silent downgrade that a type-check-only-present loop would wave through. schema-version is an integer
    # (tomllib parses a TOML bool as bool, which is refused); surfaces is a table.
    version = cfg.get("schema-version")
    if not isinstance(version, int) or isinstance(version, bool):
        raise ConfigError("assurance config {}: required key 'schema-version' must be a present integer".format(path))
    if version not in SUPPORTED_SCHEMA_VERSIONS:
        raise ConfigError("assurance config {}: schema-version {} is not a known version (supported: {})".format(
            path, version, ", ".join(str(v) for v in SUPPORTED_SCHEMA_VERSIONS)))
    surfaces = cfg.get("surfaces")
    if not isinstance(surfaces, dict):
        raise ConfigError("assurance config {} has no [surfaces] table".format(path))
    for name, spec in surfaces.items():
        if not isinstance(spec, dict):
            raise ConfigError("assurance config {}: surface '{}' must be a table, got {}".format(
                path, name, type(spec).__name__))
        adapter = spec.get("adapter")
        if adapter is not None and not isinstance(adapter, str):
            raise ConfigError("assurance config {}: surface '{}' adapter must be a string".format(path, name))
        for flag in ("required", "enabled"):
            if flag in spec and not isinstance(spec[flag], bool):
                raise ConfigError("assurance config {}: surface '{}' {} must be a boolean".format(
                    path, name, flag))
        # ADAPTER-SPECIFIC field typing: every field the declared adapter reads is string-typed, so a
        # list-valued 'protected-branch' or a table-valued 'remote-landing-provider' is a loud fault, not
        # a value that loads and then lets the surface read as a PASS on a wrong-typed control.
        for field in ADAPTER_STR_FIELDS.get(adapter, ()):
            if field in spec and not isinstance(spec[field], str):
                raise ConfigError("assurance config {}: surface '{}' field '{}' must be a string".format(
                    path, name, field))
    return cfg


def find_nearest_config(start):
    """Walk up from `start` returning the nearest existing .aiqt/assurance.toml, or None. Uses the shared
    _classify_presence so a candidate that is a PRESENT dentry but unreadable (a BROKEN SYMLINK whose target
    is missing, or an unreadable/non-regular file) FAILS CLOSED as a ConfigError rather than reading as
    absent and falling through to a lower-precedence source (portable defaults). os.stat FOLLOWS a symlink,
    so a dangling nearest config would otherwise raise FileNotFoundError and be skipped exactly like a
    genuinely absent path: this is the class-wide broken-symlink fail-closed, mirroring
    tools/check_internal_names.py. Only a genuinely ABSENT candidate walks up to the next ancestor."""
    start = Path(start).resolve()
    for anc in [start, *start.parents]:
        cand = anc.joinpath(*CONFIG_RELPARTS)
        state, why = _classify_presence(cand)
        if state == _ABSENT:
            continue
        if state == _UNREADABLE:
            raise ConfigError("candidate config {} is present but unreadable ({}); fail closed".format(cand, why))
        return cand
    return None


def _config_root(config_path):
    """Derive the tree root that a config's relative surface paths resolve under, CONSISTENTLY across every
    selection step. A standard config at <root>/.aiqt/assurance.toml roots at <root> (the .aiqt parent's
    parent); a config living anywhere else roots at its own parent directory. This is the single rule all
    three explicit/env/nearest steps share, so an explicit or env selection of a standard .aiqt config no
    longer resolves surface paths one directory too deep."""
    parent = Path(config_path).resolve().parent
    if parent.name == ".aiqt":
        return parent.parent
    return parent


def resolve_config(explicit=None, environ=None, start=None):
    """Resolve (cfg, root, provenance) by the fail-loud order above. `root` is the tree the config's
    relative surface paths resolve under, derived by _config_root (or `start` for portable defaults)."""
    environ = os.environ if environ is None else environ
    start = Path(start or Path.cwd()).resolve()
    # PINNED SOURCES (explicit --config, then AIQT_ASSURANCE_CONFIG) share ONE handling so they cannot
    # diverge: a source is PINNED when it is PRESENT, and a present-but-EMPTY pinned source (whether
    # --config "" or an env var set to "") is a LOUD ConfigError, never a silent fall-through to a
    # lower-precedence step. Presence is tested by membership, not truthiness, so an explicitly empty env
    # var is honoured as pinned-but-empty rather than read as absent. A non-empty pinned source is loaded
    # loud (load_config raises on an absent, unreadable, or malformed file), also never a fall-through.
    if explicit is not None:
        pinned = ("explicit --config", explicit, "explicit --config {}".format(explicit))
    elif ENV_VAR in environ:
        pinned = ("{} environment variable".format(ENV_VAR), environ[ENV_VAR],
                  "{}={}".format(ENV_VAR, environ[ENV_VAR]))
    else:
        pinned = None
    if pinned is not None:
        label, value, provenance = pinned
        if not value:
            raise ConfigError("{} is set but empty; a pinned config source must name a path".format(label))
        cfg = load_config(value)                          # steps 1 and 2: loud on failure, no fall-through
        return cfg, _config_root(value), provenance
    nearest = find_nearest_config(start)                 # step 3
    if nearest is not None:
        cfg = load_config(nearest)
        return cfg, _config_root(nearest), "nearest {}".format(nearest)
    return PORTABLE_DEFAULTS, start, "portable defaults"  # step 4


# --- shipped adapter types (selected by config; each returns an availability probe) ------------------
# An adapter returns (available: bool, detail: str, provenance: list[str], target: str|None). It probes
# ONLY whether the surface resolves to real, readable evidence; it never decides an audit's verdict.

# Shared "present-but-unreadable" classification, used CLASS-WIDE by every path whose presence GATES a
# result (the nearest-config walk, the file-backed adapters, and the glob gate roster), so the broken-symlink
# fail-closed posture is applied at every such site rather than one at a time. A presence-gating path is in
# exactly one of three states, and a gate must treat them differently:
#   _ABSENT      no dentry at all (genuinely missing): a `continue` up the walk, or a lower-precedence
#                source, is legitimate.
#   _UNREADABLE  a PRESENT dentry the gate cannot read as the regular file it needs: a BROKEN SYMLINK (the
#                target is missing, so os.stat FOLLOWS the link and raises FileNotFoundError exactly as for
#                an absent path, yet os.path.islink stays true, which is the discriminator), a directory or
#                other non-regular file, or a regular file that does not open and read (a permission or I/O
#                error). This is FAIL-CLOSED territory: it never reads as absent, mirroring the broken-symlink
#                fail-closed guard in tools/check_internal_names.py.
#   _READABLE    a regular file that opens and reads.
_ABSENT, _UNREADABLE, _READABLE = "absent", "unreadable", "readable"


def _classify_presence(path):
    """Classify a presence-gating path into (_ABSENT|_UNREADABLE|_READABLE, why). A genuinely absent path is
    _ABSENT; a broken symlink, a non-regular file, and an unreadable regular file are each _UNREADABLE
    (present but not the readable file the surface needs); a regular file that opens and reads a byte is
    _READABLE. The broken-symlink case is the subtle one os.stat alone gets wrong: os.stat FOLLOWS the link,
    so a dangling link raises FileNotFoundError just like an absent path, and os.path.islink (using lstat,
    which succeeds on the link itself) is what tells a present-but-dangling dentry from a truly missing one."""
    try:
        st = os.stat(path)
    except FileNotFoundError:
        return (_UNREADABLE, "broken symlink (target missing)") if os.path.islink(path) else (_ABSENT, "absent")
    except OSError as exc:
        return _UNREADABLE, "not statable ({})".format(exc)
    if not stat.S_ISREG(st.st_mode):
        return _UNREADABLE, "not a regular file"
    try:
        with open(path, "rb") as fh:
            fh.read(1)
    except OSError as exc:
        return _UNREADABLE, "not readable ({})".format(exc)
    return _READABLE, ""


def _probe_readable_file(target):
    """A file-backed surface is available only when `target` is a REGULAR file that actually opens and reads.
    Thin wrapper over the shared _classify_presence: available is True only for _READABLE, so an absent path,
    a broken symlink, a directory or other non-regular file, and an unreadable regular file all resolve
    available=False (which discover() turns into UNVERIFIABLE). `why` names the reason for the detail string."""
    state, why = _classify_presence(target)
    return state == _READABLE, why


def _adapter_gfm_task_list(spec, root):
    rel = spec.get("path", "")
    if not rel:
        return False, "no backlog path configured", [], None
    target = (root / rel)
    ok, why = _probe_readable_file(target)
    if not ok:
        return False, "backlog {}".format(why), [], str(target)
    return True, "GFM task-list backlog present", [str(target)], str(target)


def _adapter_glob_roster(spec, root):
    d = root / spec.get("dir", "tools")
    pattern = spec.get("pattern", "check_*.py")
    try:
        candidates = sorted(d.glob(pattern))
    except OSError as exc:
        return False, "gate roster dir not readable ({})".format(exc), [], str(d)
    # A counted roster member must be a READABLE regular file, the same standard the file-backed surfaces
    # hold to. A candidate that is PRESENT-but-unreadable (a broken symlink, or an unreadable/non-regular
    # file) is an input the roster cannot read: it FAILS THE ROSTER CLOSED (the caller's not-available branch
    # -> UNVERIFIABLE), never silently filtered out while the remaining readable gates still read PASS. A
    # glob match is always a present dentry, so a not-_READABLE candidate is always present-but-unreadable,
    # never genuinely absent; the same class-wide broken-symlink/unreadable fail-closed applied everywhere.
    matches = []
    for p in candidates:
        state, why = _classify_presence(p)
        if state == _READABLE:
            matches.append(p.name)
        elif state == _UNREADABLE:
            return False, "gate roster candidate {} present but unreadable ({}); fail closed".format(
                p.name, why), [], str(d)
    if not matches:
        return False, "no readable gates match {}/{}".format(spec.get("dir", "tools"), pattern), [], str(d)
    sample = ", ".join(matches[:3]) + (" ..." if len(matches) > 3 else "")
    return True, "{} gate(s) in roster ({})".format(len(matches), sample), [str(d / m) for m in matches[:3]], str(d)


_COMMIT_ID_HEXDIGITS = frozenset("0123456789abcdef")


def _is_commit_id(value):
    """A git commit id is a lowercase-hex object name: 40 hex characters for sha1, 64 for sha256. A value
    of any other length, uppercased, or carrying a non-hex character is not a resolved object id (a decoy
    git's garbage line, an abbreviated id, an error string) and must never back a PASS."""
    return isinstance(value, str) and len(value) in (40, 64) and all(c in _COMMIT_ID_HEXDIGITS for c in value)


# Resolve the git executable ONCE, via a trusted ABSOLUTE path where one is discoverable, to shrink the
# window in which a PATH edit DURING the run could swap in a decoy `git`. shutil.which can return a RELATIVE
# path when PATH carries a relative component, and a later cwd change would then reselect a different
# executable, so the which result is absolutized (os.path.abspath, pinned against the cwd at import) to keep
# the frozen-executable property. Fall back to the bare name "git" (PATH-resolved per call) only when git is
# not on PATH at import, since a portable tool must not hard-fail merely because git is only reachable
# through PATH. This is a mitigation, not a categorical defence: a decoy already FIRST on PATH at process
# launch is exactly what shutil.which itself resolves, so a PATH-controlled precise decoy `git` remains an
# OS/adopter-hardening residual, not something a portable tool can fully close. The self-test overrides this
# module global to bind a decoy `git`.
def _resolve_git(which_result):
    """Absolutize the git path so a cwd change cannot reselect a different executable. `which_result` is the
    shutil.which("git") value (an absolute path normally, but possibly RELATIVE when PATH carries a relative
    component, or None when git is not found). Return os.path.abspath of a found path (absolute, whether the
    input was relative or already absolute), or the bare name "git" (PATH-resolved per call) when none."""
    return os.path.abspath(which_result) if which_result else "git"


_GIT = _resolve_git(shutil.which("git"))


def _adapter_git(spec, root):
    # SANITIZE the environment for every git subprocess: an ambient GIT_DIR / GIT_WORK_TREE / GIT_COMMON_DIR
    # (or any other GIT_-prefixed redirect) is honoured by git OVER cwd, so an inherited GIT_DIR pointing at
    # a different repository made a non-repo root read PASS for that other repo. Scrub every GIT_* variable
    # (an allowlist stance, not an enumerated family) so the probe is bound to `root` alone, and run cwd=root.
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    try:
        proc = subprocess.run([_GIT, "rev-parse", "--show-toplevel"], cwd=str(root),
                              capture_output=True, text=True, timeout=10, env=env)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, "git not runnable ({})".format(exc), [], None
    if proc.returncode != 0:
        return False, "not a git repository", [], None
    top = proc.stdout.strip()
    # The toplevel git resolves must BE `root` (realpath-equal), not merely SOME repository: a toplevel that
    # differs from root means the evidence is bound to a different tree (an ambient redirect, or root sitting
    # inside an unrelated repo), which is UNVERIFIABLE (evidence not bound to root), never a PASS.
    if not top or os.path.realpath(top) != os.path.realpath(str(root)):
        return False, "git toplevel '{}' does not resolve to the surface root".format(top), [], None
    branch = spec.get("protected-branch", "main")
    # A PASS must be backed by the protected branch existing as an actual BRANCH ref (refs/heads/<branch>),
    # real located evidence, not merely by any commit-ish: a raw 40-hex SHA, a tag, HEAD, or an ancestry
    # expression is NOT a branch. Resolve it at CLASS WIDTH in two steps so a revision/refname expression
    # cannot slip through:
    #  (a) VALIDATE the branch-name grammar FIRST. `git rev-parse --verify refs/heads/<value>` APPLIES
    #      revision operators, so a configured value carrying a revision suffix (a `~0`, `^{commit}`, or
    #      `@{0}`) or other refname metacharacter would resolve THROUGH the operator and read PASS with the
    #      expression itself carried as branch evidence. Reject a leading-dash or empty value up front (so a
    #      value shaped like a git option is never handed to git as one), then reject anything git's own
    #      grammar (check-ref-format --branch) does not accept: a malformed value is UNVERIFIABLE, never PASS.
    if not branch or branch.startswith("-"):
        return False, "protected-branch value '{}' is not a valid branch name".format(branch), [top], top
    try:
        crf = subprocess.run([_GIT, "check-ref-format", "--branch", branch], cwd=str(root),
                             capture_output=True, text=True, timeout=10, env=env)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, "git not runnable ({})".format(exc), [], None
    if crf.returncode != 0:
        return False, "protected-branch value '{}' is not a valid branch name".format(branch), [top], top
    #  (b) Resolve the EXACT ref WITHOUT revision interpretation. `git show-ref --verify -- refs/heads/
    #      <branch>` matches the LITERAL ref and does NOT apply revision suffixes (unlike rev-parse), so the
    #      grammar-valid value resolves as the branch it names or not at all, never as an operator applied to
    #      it. Carry refs/heads/<branch>@<sha> (the exact ref, not an arbitrary sha alias) as evidence.
    ref = "refs/heads/{}".format(branch)
    try:
        bp = subprocess.run([_GIT, "show-ref", "--verify", "--", ref], cwd=str(root),
                            capture_output=True, text=True, timeout=10, env=env)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, "git not runnable ({})".format(exc), [], None
    if bp.returncode != 0:
        return False, "protected branch '{}' does not exist as a branch ref in the repository".format(branch), [top], top
    #  (c) PARSE git's ACTUAL output and DERIVE the evidence from it, never a self-constructed string.
    #      show-ref --verify prints exactly one line, "<sha> <ref>", for the matched ref. A git that
    #      returns a MISMATCHED ref (a wrong-ref bug, or a decoy `git` that emits refs/heads/main for a
    #      request of refs/heads/<branch>) or a malformed line must NOT read PASS on a ref the caller
    #      merely asked for, so require both: the RETURNED ref field EXACTLY equals the requested
    #      refs/heads/<branch>, AND the returned sha is a strict 40-hex (sha1) or 64-hex (sha256)
    #      lowercase object id (a decoy's garbage line is not a resolved ref). Any mismatch or malformed
    #      field is UNVERIFIABLE, never a PASS. The evidence carried is git's own returned ref and sha.
    fields = bp.stdout.split()
    if len(fields) != 2:
        return False, "protected branch '{}' show-ref returned a malformed line".format(branch), [top], top
    sha, returned_ref = fields[0], fields[1]
    if returned_ref != ref:
        return False, "protected branch '{}' resolved to a mismatched ref '{}'".format(branch, returned_ref), [top], top
    if not _is_commit_id(sha):
        return False, "protected branch '{}' resolved to a non-sha commit id".format(branch), [top], top
    provider = spec.get("remote-landing-provider", "")
    detail = "git root at repo toplevel, protected branch '{}' at {}".format(branch, sha[:12])
    if provider:
        detail += ", remote-landing provider '{}'".format(provider)
    return True, detail, [top, "{}@{}".format(returned_ref, sha)], top


def _adapter_record_file(spec, root):
    rel = spec.get("path", "")
    if not rel:
        return False, "no record path configured", [], None
    target = root / rel
    ok, why = _probe_readable_file(target)
    if not ok:
        return False, "record {}".format(why), [], str(target)
    return True, "record present", [str(target)], str(target)


def _adapter_transcript_inbox(spec, root):
    rel = spec.get("path", "")
    if not rel:
        return False, "no session surface configured", [], None
    target = root / rel
    ok, why = _probe_readable_file(target)
    if not ok:
        return False, "session surface {}".format(why), [], str(target)
    return True, "session surface present", [str(target)], str(target)


def _adapter_held_source(spec, root):
    rel = spec.get("path", "")
    if not rel:
        return False, "no reference base configured", [], None
    target = root / rel
    ok, why = _probe_readable_file(target)
    if not ok:
        return False, "reference base {}".format(why), [], str(target)
    return True, "reference base present", [str(target)], str(target)


ADAPTERS = {
    "gfm-task-list": _adapter_gfm_task_list,
    "glob-roster": _adapter_glob_roster,
    "git": _adapter_git,
    "record-file": _adapter_record_file,
    "transcript-inbox": _adapter_transcript_inbox,
    "held-source": _adapter_held_source,
}


def _validate_probe_return(ret):
    """An adapter must return a 4-tuple (available: bool, detail: str, provenance: list[str], target:
    str|None). A malformed return (wrong arity, a non-bool `available` such as the truthy string "false",
    a non-str detail, a non-list or non-string-element `provenance`, a non-str/non-None target) is an
    ADAPTER FAULT, not evidence: the caller turns it into UNVERIFIABLE/adapter-error so an untyped truthy
    value can never reach a PASS. Returns (ok, why)."""
    if not isinstance(ret, tuple) or len(ret) != 4:
        return False, "expected a 4-tuple (available, detail, provenance, target)"
    available, detail, provenance, target = ret
    if not isinstance(available, bool):
        return False, "available must be a bool, got {}".format(type(available).__name__)
    if not isinstance(detail, str):
        return False, "detail must be a str, got {}".format(type(detail).__name__)
    if not isinstance(provenance, list) or not all(isinstance(p, str) and p for p in provenance):
        return False, "provenance must be a list of non-empty strings"
    if target is not None and not isinstance(target, str):
        return False, "target must be a str or None, got {}".format(type(target).__name__)
    return True, ""


def _surface(name, adapter, required, enabled, available, status, kind, detail, provenance, target):
    """Construct a normalized surface record. This is the SINGLE construction point every discovery path
    routes through, so it also fail-safes the false-green case at the boundary: a record marked PASS is
    handed to a caller only when it is genuinely available, carries the available kind, and carries located
    evidence (a non-empty list of non-empty strings). An inconsistent PASS is DOWNGRADED to UNVERIFIABLE/
    adapter-error (never raised), so a direct discover()/discover_all() result can never carry a false PASS
    yet discovery never crashes mid-walk."""
    # A non-list provenance never char-splits: list('x:1') would yield ['x', ':', '1'] and, at PASS,
    # satisfy the non-empty-string-list check below with hollow single-character "evidence". Only a real
    # list is copied, and only its NON-EMPTY STRING elements survive, uniformly for every status, so a
    # would-be UNVERIFIABLE/FAIL record can never carry a [7] or [""] element any more than a PASS can.
    # Anything else (a bare string above all, or None) becomes empty evidence, which then DOWNGRADES a
    # would-be PASS to UNVERIFIABLE rather than handing back a false green.
    evidence = [e for e in provenance if isinstance(e, str) and e] if isinstance(provenance, list) else []
    if status == PASS and not (available is True and kind == KIND_AVAILABLE
                               and evidence and all(isinstance(e, str) and e for e in evidence)):
        status, kind, available = UNVERIFIABLE, KIND_ADAPTER_ERROR, False
        detail = "inconsistent PASS downgraded (missing evidence or wrong kind): {}".format(detail)
    return {"name": name, "adapter": adapter, "required": required, "enabled": enabled,
            "available": available, "status": status, "kind": kind, "detail": detail,
            "evidence": evidence, "target": target}


def discover(name, cfg, root):
    """Resolve ONE named surface to a normalized record. The status assignment is the contract's core
    guard: an enabled-but-absent surface takes the `not available` branch to UNVERIFIABLE, so missing
    evidence never reads as PASS. Deleting that branch (letting it fall to PASS) is exactly what the
    self-test's discriminating case catches."""
    surfaces = cfg.get("surfaces", {})
    spec = surfaces.get(name)
    if not isinstance(spec, dict):
        # Undeclared: either an EXPECTED surface (in the canonical roster) omitted from the config, or a
        # name asked for that the config never declares. Either way it is UNVERIFIABLE (loud), never a
        # silent skip; for an expected surface the required flag is carried from the canonical default so a
        # required omission is visibly required-missing rather than reading as an unknown extra.
        expected = PORTABLE_DEFAULTS["surfaces"].get(name)
        req = bool(expected.get("required", False)) if isinstance(expected, dict) else None
        detail = ("expected surface '{}' omitted from config".format(name) if expected is not None
                  else "surface '{}' not declared in config".format(name))
        return _surface(name, None, req, None, False, UNVERIFIABLE, KIND_UNDECLARED, detail, [], None)
    adapter = spec.get("adapter")
    # A surface's REQUIRED flag is validated against the canonical PORTABLE_DEFAULTS and can never be
    # DOWNGRADED below it: a config that omits `required`, sets it false, or leaves a canonically-required
    # surface present-but-empty (an empty [surfaces.backlog]) or enabled=false must not silently demote a
    # canonically-REQUIRED surface to an optional SKIP. The canonical requirement is ORed in, so such a
    # surface, when disabled/empty in config, resolves to the required-disabled malformed UNVERIFIABLE
    # below, never an optional-disabled SKIP. Applies to every canonically-required surface, not just backlog.
    canonical = PORTABLE_DEFAULTS["surfaces"].get(name)
    canonical_required = bool(canonical.get("required", False)) if isinstance(canonical, dict) else False
    required = bool(spec.get("required", False)) or canonical_required
    enabled = bool(spec.get("enabled", False))

    # ADAPTER-TYPE SUBSTITUTION GUARD: a canonically-recognized surface must be bound to its CANONICAL
    # adapter type. Consulting PORTABLE_DEFAULTS only for `required` (never the adapter type) let a config
    # rebind a canonically-required surface (backlog/gate_roster/git) to a DIFFERENT adapter, e.g. the
    # record-file adapter aimed at any present file, and read PASS on that decoy. When the config NAMES an
    # adapter that differs from the canonical one for this surface, it is a config fault resolving
    # UNVERIFIABLE/adapter-mismatch, never a PASS. An OMITTED adapter (None) is not a substitution: it
    # flows to the disabled/unknown-adapter handling below (a present-but-empty required surface is a
    # required-disabled malformed, not a mismatch). Applies to every canonical surface, not just backlog.
    canonical_adapter = canonical.get("adapter") if isinstance(canonical, dict) else None
    if canonical_adapter is not None and adapter is not None and adapter != canonical_adapter:
        return _surface(name, adapter, required, enabled, False, UNVERIFIABLE, KIND_ADAPTER_MISMATCH,
                        "surface '{}' is bound to adapter {!r} but its canonical adapter is {!r}".format(
                            name, adapter, canonical_adapter), [], None)

    if not enabled:
        # A REQUIRED surface disabled in config is a configuration fault, kept DISTINCT from an
        # OPTIONAL-disabled SKIP so a digest never mislabels a malformed-required as an ordinary skip.
        if required:
            return _surface(name, adapter, required, enabled, False, UNVERIFIABLE, KIND_REQUIRED_DISABLED,
                            "required surface disabled in config (malformed)", [], None)
        return _surface(name, adapter, required, enabled, False, SKIP, KIND_OPTIONAL_DISABLED,
                        "optional surface disabled", [], None)

    probe = ADAPTERS.get(adapter)
    if probe is None:
        return _surface(name, adapter, required, enabled, False, UNVERIFIABLE, KIND_UNKNOWN_ADAPTER,
                        "unknown adapter type {!r}".format(adapter), [], None)

    try:
        ret = probe(spec, root)
    except Exception as exc:  # noqa: BLE001  an adapter that RAISES is a cannot-evaluate, never a crash
        # An adapter fault is UNVERIFIABLE with an adapter-error kind, so a broken probe surfaces loudly
        # instead of crashing discovery or (worse) reading as a pass. Deleting this wrap lets the exception
        # escape and crash the caller, which is exactly what the self-test's raising-adapter case catches.
        return _surface(name, adapter, required, enabled, False, UNVERIFIABLE, KIND_ADAPTER_ERROR,
                        "adapter {!r} raised: {}".format(adapter, exc), [], None)
    ok, why = _validate_probe_return(ret)
    if not ok:
        # A MALFORMED adapter return is an adapter fault, not evidence: a truthy string like "false" as
        # `available`, or a None provenance, must never reach the PASS branch below. It resolves
        # UNVERIFIABLE/adapter-error, the same fail-safe posture as a raising probe.
        return _surface(name, adapter, required, enabled, False, UNVERIFIABLE, KIND_ADAPTER_ERROR,
                        "adapter {!r} returned a malformed probe ({})".format(adapter, why), [], None)
    available, detail, provenance, target = ret
    if not available:
        # DISCRIMINATING GUARD: missing/unreadable evidence is UNVERIFIABLE, never PASS. The kind
        # distinguishes required-unavailable (loud) from optional-unavailable (visible).
        kind = KIND_REQUIRED_UNAVAILABLE if required else KIND_OPTIONAL_UNAVAILABLE
        return _surface(name, adapter, required, enabled, False, UNVERIFIABLE, kind, detail, provenance, target)
    return _surface(name, adapter, required, enabled, True, PASS, KIND_AVAILABLE, detail, provenance, target)


def discover_all(cfg, root):
    """Discover every EXPECTED surface plus any extra the config declares, so a REQUIRED surface omitted
    ENTIRELY from the config is still enumerated and resolves UNVERIFIABLE (loud), never silently skipped.
    The config's declared surfaces come first in their declared order (a stable dict iteration); any
    expected surface the config does not declare follows. The expected set is the canonical roster the
    portable defaults define."""
    declared = list(cfg.get("surfaces", {}))
    names = list(declared)
    for name in EXPECTED_SURFACES:
        if name not in names:
            names.append(name)
    return [discover(name, cfg, root) for name in names]


def render_digest(surfaces, extra_lines=None):
    """A COMPACT one-line-per-surface digest that keeps required-unavailable and optional-disabled
    visually distinct, so no absent surface hides in a green summary."""
    lines = ["QA surface digest:"]
    for s in surfaces:
        lines.append("  [{status:<12}] {name:<14} {kind:<26} {detail}".format(
            status=s["status"], name=s["name"], kind=s["kind"] or "", detail=s["detail"]))
    for line in (extra_lines or []):
        lines.append(line)
    return "\n".join(lines)


# --- self-test --------------------------------------------------------------------------------------
def _self_test():
    import shutil
    import tempfile

    failures = []
    tmp = Path(tempfile.mkdtemp(prefix="aiqt-qa-adapter-selftest-"))
    try:
        # A minimal root: a present gate roster, an absent backlog.
        (tmp / ".aiqt").mkdir()
        (tmp / "tools").mkdir()
        (tmp / "tools" / "check_example.py").write_text("# gate\n", encoding="utf-8")

        cfg = {
            "surfaces": {
                "present_req": {"adapter": "glob-roster", "required": True, "enabled": True,
                                "dir": "tools", "pattern": "check_*.py"},
                "absent_req": {"adapter": "gfm-task-list", "required": True, "enabled": True,
                               "path": "TODO.md"},
                "absent_opt": {"adapter": "held-source", "required": False, "enabled": True,
                               "path": "REFERENCE.md"},
                "disabled_opt": {"adapter": "held-source", "required": False, "enabled": False},
                "disabled_req": {"adapter": "record-file", "required": True, "enabled": False},
                "bad_adapter": {"adapter": "no-such-type", "required": False, "enabled": True},
            }
        }

        # 1. A present required surface -> PASS.
        r = discover("present_req", cfg, tmp)
        if r["status"] != PASS:
            failures.append("present required surface expected PASS, got {}".format(r["status"]))

        # 2. DISCRIMINATING: a MISSING required surface -> UNVERIFIABLE, never PASS. If the `not
        #    available` guard in discover() were removed (falling through to PASS), this case fails.
        r = discover("absent_req", cfg, tmp)
        if r["status"] != UNVERIFIABLE:
            failures.append("missing required surface expected UNVERIFIABLE, got {} (missing evidence "
                            "must never read as PASS)".format(r["status"]))
        if r["kind"] != KIND_REQUIRED_UNAVAILABLE:
            failures.append("missing required surface expected kind required-unavailable, got {}".format(r["kind"]))

        # 3. An OPTIONAL-disabled surface -> SKIP, and NEVER malformed-required.
        r = discover("disabled_opt", cfg, tmp)
        if r["status"] != SKIP or r["kind"] != KIND_OPTIONAL_DISABLED:
            failures.append("optional-disabled surface expected SKIP/optional-disabled, got {}/{}".format(
                r["status"], r["kind"]))

        # 4. An OPTIONAL-enabled-but-absent surface -> UNVERIFIABLE (visible), distinct from a skip.
        r = discover("absent_opt", cfg, tmp)
        if r["status"] != UNVERIFIABLE or r["kind"] != KIND_OPTIONAL_UNAVAILABLE:
            failures.append("optional-enabled-absent expected UNVERIFIABLE/optional-unavailable, got {}/{}".format(
                r["status"], r["kind"]))

        # 5. A REQUIRED surface disabled in config -> UNVERIFIABLE malformed, NEVER an optional skip.
        r = discover("disabled_req", cfg, tmp)
        if r["status"] != UNVERIFIABLE or r["kind"] != KIND_REQUIRED_DISABLED:
            failures.append("required-disabled surface expected UNVERIFIABLE/malformed, got {}/{}".format(
                r["status"], r["kind"]))

        # 6. An unknown adapter type -> UNVERIFIABLE, never a silent pass.
        r = discover("bad_adapter", cfg, tmp)
        if r["status"] != UNVERIFIABLE:
            failures.append("unknown adapter expected UNVERIFIABLE, got {}".format(r["status"]))

        # 7. Result contract rejects a status outside the fixed set.
        try:
            make_result("x", "GREEN", "bad")
            failures.append("make_result accepted an out-of-set status")
        except ValueError:
            pass

        # 8. Config resolution is FAIL-LOUD: a pinned explicit config that does not exist raises
        #    ConfigError, never a silent fall-through to portable defaults.
        try:
            resolve_config(explicit=str(tmp / "nope.toml"))
            failures.append("resolve_config silently accepted a missing explicit config")
        except ConfigError:
            pass

        # 9. A real committed config resolves as the nearest config, not portable defaults.
        (tmp / ".aiqt" / "assurance.toml").write_text(
            "schema-version = 1\n[surfaces.git]\nadapter='git'\nrequired=true\nenabled=true\n"
            "protected-branch='main'\n", encoding="utf-8")
        cfg2, root2, prov = resolve_config(start=tmp, environ={})
        if "nearest" not in prov:
            failures.append("resolve_config expected to find the nearest config, got provenance {!r}".format(prov))

        # 10. DISCRIMINATING (existence vs readable): a file-backed surface whose path is a DIRECTORY
        #     resolves available=False -> UNVERIFIABLE, never PASS. A bare os.stat probe would stat the
        #     directory happily and read PASS, so removing the regular-file probe fails this case.
        (tmp / "not-a-file").mkdir()
        dir_cfg = {"surfaces": {"dir_surface": {"adapter": "record-file", "required": True,
                   "enabled": True, "path": "not-a-file"}}}
        r = discover("dir_surface", dir_cfg, tmp)
        if r["status"] != UNVERIFIABLE:
            failures.append("a directory at a file-backed surface expected UNVERIFIABLE, got {} "
                            "(existence is not readability)".format(r["status"]))

        # 11. DISCRIMINATING (result-contract consistency): a hand-built inconsistent result (PASS with an
        #     unavailable kind and no evidence) is rejected by emit's validate_result. Removing that guard
        #     lets the false green serialize, failing this case.
        try:
            emit({"schema": RESULT_SCHEMA, "audit": "x", "status": PASS, "summary": "s", "surface": None,
                  "required": True, "kind": KIND_REQUIRED_UNAVAILABLE, "evidence": []})
            failures.append("emit accepted an inconsistent PASS/required-unavailable/no-evidence result")
        except ValueError:
            pass

        # 12. DISCRIMINATING (config schema): a wrong-typed surface entry (backlog as a string, not a
        #     table) raises ConfigError. Removing the schema validation lets it load, failing this case.
        bad_cfg = tmp / "bad.toml"
        bad_cfg.write_text('schema-version = 1\n[surfaces]\nbacklog = "not a table"\n', encoding="utf-8")
        try:
            load_config(bad_cfg)
            failures.append("load_config accepted a wrong-typed surface entry (schema not validated)")
        except ConfigError:
            pass

        # 13. DISCRIMINATING (root off-by-one): an EXPLICIT --config at <root>/.aiqt/assurance.toml
        #     resolves gate_roster to PASS, the same as the nearest-config walk. If the root were computed
        #     as the config's own parent (<root>/.aiqt), the tools/ roster would resolve one directory too
        #     deep and read UNVERIFIABLE, failing this case.
        (tmp / ".aiqt" / "assurance.toml").write_text(
            "schema-version = 1\n[surfaces.gate_roster]\nadapter='glob-roster'\nrequired=true\n"
            "enabled=true\ndir='tools'\npattern='check_*.py'\n", encoding="utf-8")
        cfg3, root3, prov3 = resolve_config(explicit=str(tmp / ".aiqt" / "assurance.toml"), environ={})
        r = discover("gate_roster", cfg3, root3)
        if r["status"] != PASS:
            failures.append("explicit .aiqt/assurance.toml expected gate_roster PASS (root off-by-one), "
                            "got {}".format(r["status"]))

        # 14. DISCRIMINATING (erroring adapter -> UNVERIFIABLE): an adapter that RAISES becomes
        #     UNVERIFIABLE with an adapter-error kind, never a crash or a pass. Removing the wrap lets the
        #     exception escape and crash this self-test.
        def _boom(spec, root):
            raise RuntimeError("boom")
        ADAPTERS["_selftest-raise"] = _boom
        try:
            rr = discover("raiser", {"surfaces": {"raiser": {"adapter": "_selftest-raise",
                          "required": True, "enabled": True}}}, tmp)
            if rr["status"] != UNVERIFIABLE or rr["kind"] != KIND_ADAPTER_ERROR:
                failures.append("raising adapter expected UNVERIFIABLE/adapter-error, got {}/{}".format(
                    rr["status"], rr["kind"]))
        finally:
            del ADAPTERS["_selftest-raise"]

        # 15. DISCRIMINATING (--config with no operand): config_arg raises on a trailing --config, never a
        #     silent fall-through. Removing the guard makes it return None, failing this case.
        try:
            config_arg(["--config"])
            failures.append("config_arg accepted --config with no operand (silent fall-through)")
        except ValueError:
            pass

        # 16. DISCRIMINATING (discover_all omitted-required): a config that omits a REQUIRED surface
        #     entirely still enumerates it as UNVERIFIABLE, never a silent skip. If discover_all iterated
        #     only the config's declared surfaces, 'backlog' would be absent from the results, failing this.
        omit_cfg = {"surfaces": {"gate_roster": {"adapter": "glob-roster", "required": True,
                    "enabled": True, "dir": "tools", "pattern": "check_*.py"}}}
        all_surf = discover_all(omit_cfg, tmp)
        backlog = next((s for s in all_surf if s["name"] == "backlog"), None)
        if backlog is None:
            failures.append("discover_all omitted the required 'backlog' surface entirely (silent skip)")
        elif backlog["status"] != UNVERIFIABLE:
            failures.append("omitted required surface expected UNVERIFIABLE, got {}".format(backlog["status"]))

        # 17. DISCRIMINATING (FAIL/available is legitimate): a FAIL on an AVAILABLE surface is a real verdict
        #     (the surface was present and probed, the audit's check observed the property violated), so
        #     validate_result ACCEPTS it; an UNVERIFIABLE carrying the available kind is still REJECTED. A
        #     guard that rejects EVERY non-PASS available result (the round-1 regression) fails the accept half.
        try:
            validate_result({"schema": RESULT_SCHEMA, "status": FAIL, "kind": KIND_AVAILABLE,
                             "evidence": ["tools/x.py:1"], "audit": "x", "summary": "s",
                             "surface": None, "required": True})
        except ValueError as exc:
            failures.append("validate_result wrongly rejected a legitimate FAIL/available result: {}".format(exc))
        try:
            validate_result({"schema": RESULT_SCHEMA, "status": UNVERIFIABLE, "kind": KIND_AVAILABLE,
                             "evidence": [], "audit": "x", "summary": "s", "surface": None,
                             "required": True})
            failures.append("validate_result accepted an UNVERIFIABLE carrying the available kind")
        except ValueError:
            pass

        # 18. DISCRIMINATING (evidence typing): a PASS whose evidence is [""] (a non-empty list of an EMPTY
        #     string) or a bare string (not a list) is rejected. Dropping the non-empty-strings or list-type
        #     checks lets a hollow PASS through, failing these.
        try:
            validate_result({"schema": RESULT_SCHEMA, "status": PASS, "kind": KIND_AVAILABLE, "evidence": [""],
                             "audit": "x", "summary": "s", "surface": None, "required": True})
            failures.append("validate_result accepted a PASS with an empty-string evidence entry")
        except ValueError:
            pass
        try:
            validate_result({"schema": RESULT_SCHEMA, "status": PASS, "kind": KIND_AVAILABLE,
                             "evidence": "tools/x.py:1", "audit": "x", "summary": "s", "surface": None,
                             "required": True})
            failures.append("validate_result accepted a PASS with a non-list (string) evidence")
        except ValueError:
            pass

        # 19. DISCRIMINATING (malformed adapter return): an adapter returning a truthy STRING for
        #     `available` ("false") must not reach PASS; discover type-validates the return and resolves it
        #     UNVERIFIABLE/adapter-error. Removing the return validation lets the truthy string read PASS.
        def _liar(spec, root):
            return ("false", "detail", ["ev"], "t")  # a truthy string, not a real bool
        ADAPTERS["_selftest-liar"] = _liar
        try:
            r = discover("liar", {"surfaces": {"liar": {"adapter": "_selftest-liar",
                         "required": True, "enabled": True}}}, tmp)
            if r["status"] != UNVERIFIABLE or r["kind"] != KIND_ADAPTER_ERROR:
                failures.append("truthy-string available expected UNVERIFIABLE/adapter-error, got {}/{}".format(
                    r["status"], r["kind"]))
        finally:
            del ADAPTERS["_selftest-liar"]

        # 20. DISCRIMINATING (discover-boundary fail-safe): an adapter that returns a well-typed
        #     available=True but EMPTY provenance would build a PASS with no evidence; _surface downgrades
        #     it to UNVERIFIABLE rather than hand back a false PASS (and never raises mid-discovery). Removing
        #     the _surface fail-safe lets discover() return a PASS with empty evidence, failing this.
        def _evidenceless(spec, root):
            return (True, "available but no located evidence", [], "t")
        ADAPTERS["_selftest-evidenceless"] = _evidenceless
        try:
            r = discover("ev0", {"surfaces": {"ev0": {"adapter": "_selftest-evidenceless",
                         "required": True, "enabled": True}}}, tmp)
            if r["status"] == PASS:
                failures.append("an available=True adapter with empty evidence produced a false PASS")
        finally:
            del ADAPTERS["_selftest-evidenceless"]

        # 21. DISCRIMINATING (required-key schema): a config that OMITS schema-version, or gives it a string
        #     value, is a loud ConfigError, never a silent accept. Removing the required-key check lets the
        #     omission or the wrong type load, failing these.
        miss_cfg = tmp / "missing-version.toml"
        miss_cfg.write_text("[surfaces.git]\nadapter='git'\nrequired=true\nenabled=true\n", encoding="utf-8")
        try:
            load_config(miss_cfg)
            failures.append("load_config accepted a config missing schema-version")
        except ConfigError:
            pass
        str_cfg = tmp / "string-version.toml"
        str_cfg.write_text("schema-version = '1'\n[surfaces.git]\nadapter='git'\n", encoding="utf-8")
        try:
            load_config(str_cfg)
            failures.append("load_config accepted a string-valued schema-version")
        except ConfigError:
            pass

        # 22. DISCRIMINATING (--config operand parsing): an empty operand, the =-joined empty form, a
        #     next-flag operand, and a duplicate --config are each loud errors, never a silent accept or a
        #     first-wins. (The trailing-operand case is covered separately below.)
        for bad in (["--config", ""], ["--config="], ["--config", "--self-test"],
                    ["--config", "a.toml", "--config", "b.toml"], ["--config=a.toml", "--config=b.toml"]):
            try:
                config_arg(bad)
                failures.append("config_arg accepted a malformed --config argv {!r}".format(bad))
            except ValueError:
                pass
        if config_arg(["--config=x.toml"]) != "x.toml":
            failures.append("config_arg did not parse the =-joined --config=x.toml form")
        if config_arg(["--config", "x.toml"]) != "x.toml":
            failures.append("config_arg did not parse the separate --config x.toml form")

        # 23. DISCRIMINATING (arg validation precedes --self-test dispatch in main): a subprocess given
        #     `--config --self-test` exits 2 (a loud argument error), never 0. A recursion sentinel keeps a
        #     regressed build (which would re-enter the self-test) from spawning nested children.
        if os.environ.get("AIQT_QA_SELFTEST_CHILD") != "1":
            proc = subprocess.run([sys.executable, "-I", "-B", str(Path(__file__).resolve()),
                                   "--config", "--self-test"], capture_output=True, text=True,
                                  env=dict(os.environ, AIQT_QA_SELFTEST_CHILD="1"))
            if proc.returncode != 2:
                failures.append("main did not validate --config before dispatching --self-test "
                                "(expected loud exit 2, got {})".format(proc.returncode))

        # 24. DISCRIMINATING (roster readability): an UNREADABLE gate file in the roster dir is not counted;
        #     when it is the only match the roster is UNVERIFIABLE, never a PASS on an unreadable gate. chmod
        #     000 does not restrict root, so the case is asserted only off-root (CI and local run non-root).
        if hasattr(os, "geteuid") and os.geteuid() != 0:
            rr = tmp / "roster-unreadable"
            (rr / "tools").mkdir(parents=True)
            gate = rr / "tools" / "check_only.py"
            gate.write_text("# gate\n", encoding="utf-8")
            os.chmod(gate, 0o000)
            try:
                r = discover("gate_roster", {"surfaces": {"gate_roster": {"adapter": "glob-roster",
                             "required": True, "enabled": True, "dir": "tools", "pattern": "check_*.py"}}}, rr)
                if r["status"] != UNVERIFIABLE:
                    failures.append("an unreadable-only gate roster expected UNVERIFIABLE, got {} "
                                    "(an unreadable gate must not count as present)".format(r["status"]))
            finally:
                os.chmod(gate, 0o600)

        # 25. DISCRIMINATING (readable, not merely regular): a present, REGULAR, but UNREADABLE file
        #     (chmod 000) at a file-backed surface resolves available=False -> UNVERIFIABLE. Removing the
        #     open-and-read half of _probe_readable_file (keeping only the S_ISREG check) makes this read
        #     PASS, so it fails. Asserted only off-root, since chmod 000 does not restrict root.
        if hasattr(os, "geteuid") and os.geteuid() != 0:
            unreadable = tmp / "unreadable.txt"
            unreadable.write_text("secret\n", encoding="utf-8")
            os.chmod(unreadable, 0o000)
            try:
                r = discover("unread", {"surfaces": {"unread": {"adapter": "record-file", "required": True,
                             "enabled": True, "path": "unreadable.txt"}}}, tmp)
                if r["status"] != UNVERIFIABLE:
                    failures.append("an unreadable regular file at a file-backed surface expected "
                                    "UNVERIFIABLE, got {} (regularity is not readability)".format(r["status"]))
            finally:
                os.chmod(unreadable, 0o600)

        # 26. DISCRIMINATING (make_result rejects non-list evidence): make_result with a bare STRING
        #     evidence is refused, never coerced with list() (which would split 'x:1' into ['x', ':', '1']
        #     and let a hollow PASS through). Reverting the non-list guard to list(evidence or []) lets the
        #     string char-split and the result construct, failing this case (the round-4 BLOCKER). A VALID
        #     `required` is passed so the construction reaches and ISOLATES the string-evidence guard rather
        #     than failing first on a required=None field (the round-14 finding-4 masking).
        try:
            make_result("x", PASS, "s", required=True, kind=KIND_AVAILABLE, evidence="x:1")
            failures.append("make_result accepted a bare-string evidence (char-split into a hollow PASS)")
        except ValueError:
            pass

        # 27. DISCRIMINATING (_surface non-list provenance downgrade, exact status AND kind): a PASS handed
        #     to _surface with a NON-LIST provenance never char-splits into hollow single-character evidence;
        #     it becomes empty evidence and downgrades to EXACTLY UNVERIFIABLE with the adapter-error kind.
        #     Reverting `evidence` to list(provenance or []) splits the string into characters that satisfy
        #     the non-empty-string-list check, returning a false PASS; changing the downgrade STATUS (to FAIL)
        #     or KIND (to required-unavailable) is caught by asserting both exactly, not merely non-PASS.
        r = _surface("np", "record-file", True, True, True, PASS, KIND_AVAILABLE, "d", "x:1", "t")
        if r["status"] != UNVERIFIABLE or r["kind"] != KIND_ADAPTER_ERROR:
            failures.append("_surface malformed-PASS downgrade expected UNVERIFIABLE/adapter-error, got "
                            "{}/{}".format(r["status"], r["kind"]))
        if any(not (isinstance(e, str) and e) for e in r["evidence"]):
            failures.append("_surface downgrade carried malformed evidence: {!r}".format(r["evidence"]))

        # 28. DISCRIMINATING (canonically-required surface not downgradable): a config presenting the
        #     canonically-REQUIRED backlog as enabled=false, or present-but-empty, must resolve
        #     UNVERIFIABLE/required-disabled, NEVER an optional-disabled SKIP. Removing the canonical-required
        #     OR in discover() lets the config downgrade it to a SKIP, failing these cases.
        r = discover("backlog", {"surfaces": {"backlog": {"adapter": "gfm-task-list", "enabled": False,
                     "path": "TODO.md"}}}, tmp)
        if r["status"] != UNVERIFIABLE or r["kind"] != KIND_REQUIRED_DISABLED:
            failures.append("a canonically-required surface disabled in config expected UNVERIFIABLE/"
                            "required-disabled, got {}/{}".format(r["status"], r["kind"]))
        r = discover("backlog", {"surfaces": {"backlog": {}}}, tmp)
        if r["status"] != UNVERIFIABLE or r["kind"] != KIND_REQUIRED_DISABLED:
            failures.append("a present-but-empty canonically-required surface expected UNVERIFIABLE/"
                            "required-disabled, got {}/{}".format(r["status"], r["kind"]))

        # 29. DISCRIMINATING (unknown schema-version): a schema-version that parses as an int but is not a
        #     KNOWN version (999) is a loud ConfigError, never loaded on the strength of being an integer.
        #     Removing the supported-versions check lets 999 load, failing this case.
        v999 = tmp / "version-999.toml"
        v999.write_text("schema-version = 999\n[surfaces.git]\nadapter='git'\n", encoding="utf-8")
        try:
            load_config(v999)
            failures.append("load_config accepted an unknown schema-version 999")
        except ConfigError:
            pass

        # 30. DISCRIMINATING (adapter-specific field typing): a list-valued 'protected-branch' and a
        #     table-valued 'remote-landing-provider' on the git surface are each a loud ConfigError, never a
        #     wrong-typed value that loads and lets the git surface read PASS. Removing the adapter-field
        #     type check lets them load, failing these cases.
        for i, body in enumerate((
                "schema-version = 1\n[surfaces.git]\nadapter='git'\nprotected-branch=['a','b']\n",
                "schema-version = 1\n[surfaces.git]\nadapter='git'\nremote-landing-provider={name='gh'}\n")):
            fp = tmp / "adapter-field-{}.toml".format(i)
            fp.write_text(body, encoding="utf-8")
            try:
                load_config(fp)
                failures.append("load_config accepted a wrong-typed adapter field: {!r}".format(body))
            except ConfigError:
                pass

        # 31. DISCRIMINATING (git protected branch must exist): the git surface is UNVERIFIABLE when the
        #     configured protected branch does not resolve to a commit, and PASS only when it does (real
        #     located evidence, not merely the repo root). Removing the branch verification lets a
        #     nonexistent branch read PASS on repository-root evidence alone, failing the UNVERIFIABLE case.
        #     Needs a git binary; skipped (not asserted) where git is unavailable.
        if shutil.which("git"):
            gitrepo = tmp / "gitrepo"
            gitrepo.mkdir()
            genv = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@e",
                        GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@e")

            def _git(*a):
                return subprocess.run(["git", *a], cwd=str(gitrepo), capture_output=True, text=True, env=genv)

            _git("init", "-q")
            (gitrepo / "f.txt").write_text("x\n", encoding="utf-8")
            _git("add", "-A")
            _git("commit", "-q", "-m", "init")
            cur = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=str(gitrepo),
                                 capture_output=True, text=True, env=genv).stdout.strip()
            head_sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(gitrepo),
                                      capture_output=True, text=True, env=genv).stdout.strip()
            git_top = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=str(gitrepo),
                                     capture_output=True, text=True, env=genv).stdout.strip()
            _git("tag", "v-selftest")
            r = discover("git", {"surfaces": {"git": {"adapter": "git", "required": True, "enabled": True,
                         "protected-branch": cur}}}, gitrepo)
            # 31a. DISCRIMINATING (finding-6a + finding-2-ii + finding-2c): a git PASS carries EXACTLY the
            #      two-element evidence vector [<repo-top>, refs/heads/<branch>@<sha>], asserted by EXACT
            #      VECTOR EQUALITY (==), not membership, against the REAL head_sha git reported. Removing the
            #      branch ref, self-constructing the sha (which then diverges from the real head_sha), OR
            #      appending an arbitrary decoy element each breaks the exact equality; a membership test would
            #      let an appended decoy through, which exact equality catches. SCOPE of this case, stated so
            #      as not to over-claim: it pins the SHA half of the evidence to git's own output (the sha is
            #      compared to the real head_sha, so a self-constructed sha is caught here), while the ref half
            #      is proven equal to the requested refs/heads/<branch> by the returned-ref equality gate
            #      upstream (test 31a-5), so whether the code interpolates the requested `ref` or git's
            #      `returned_ref` into the evidence is IMMATERIAL once that gate holds (they are equal) and is
            #      not independently discriminable. The two-field count of show-ref's line and the strict
            #      40/64-hex LOWERCASE sha format (both directions) are discriminated separately by tests 48
            #      and 47, not by this exact-vector case.
            pass_ref = "refs/heads/{}@{}".format(cur, head_sha)
            if r["status"] != PASS or r["kind"] != KIND_AVAILABLE or r["evidence"] != [git_top, pass_ref]:
                failures.append("git surface on an existing protected branch expected PASS/available with "
                                "evidence exactly {!r}, got {}/{} evidence={!r} ({})".format(
                                    [git_top, pass_ref], r["status"], r["kind"], r["evidence"], r["detail"]))
            # 31a-2. DISCRIMINATING (finding-2-iii): a nonexistent protected branch is UNVERIFIABLE/
            #      required-unavailable carrying EXACTLY the repo-root evidence [<repo-top>]. Removing that
            #      [repo-root] evidence (returning []) fails the exact evidence assertion; a PASS fails the
            #      status. The evidence is compared by realpath so a symlinked tmp root still matches.
            r = discover("git", {"surfaces": {"git": {"adapter": "git", "required": True, "enabled": True,
                         "protected-branch": "no-such-branch-xyz"}}}, gitrepo)
            if (r["status"] != UNVERIFIABLE or r["kind"] != KIND_REQUIRED_UNAVAILABLE
                    or len(r["evidence"]) != 1
                    or os.path.realpath(r["evidence"][0]) != os.path.realpath(git_top)):
                failures.append("git surface on a nonexistent protected branch expected UNVERIFIABLE/"
                                "required-unavailable carrying exactly the repo-root evidence, got {}/{} "
                                "evidence={!r}".format(r["status"], r["kind"], r["evidence"]))
            # 31a-3. DISCRIMINATING (finding-1: a protected-branch value carrying a REVISION SUFFIX is not a
            #      branch name): `<branch>~0`, `<branch>^{commit}`, and `<branch>@{0}` each resolve THROUGH a
            #      revision operator under `rev-parse --verify refs/heads/<value>` and would read PASS with the
            #      expression carried as evidence. Each must be UNVERIFIABLE/required-unavailable, rejected at
            #      the grammar step (detail names an invalid branch name) with exactly [<repo-top>] evidence.
            #      Removing the check-ref-format grammar guard drops the "not a valid branch name" detail
            #      (the value then falls to a does-not-exist UNVERIFIABLE); reverting resolution to the
            #      `rev-parse --verify refs/heads/<value>` form makes every suffix read PASS. Both flips fail.
            for suffix in ("~0", "^{commit}", "@{0}"):
                value = cur + suffix
                r = discover("git", {"surfaces": {"git": {"adapter": "git", "required": True,
                             "enabled": True, "protected-branch": value}}}, gitrepo)
                if (r["status"] != UNVERIFIABLE or r["kind"] != KIND_REQUIRED_UNAVAILABLE
                        or "not a valid branch name" not in r["detail"]
                        or len(r["evidence"]) != 1
                        or os.path.realpath(r["evidence"][0]) != os.path.realpath(git_top)):
                    failures.append("git protected-branch given a revision expression {!r} expected "
                                    "UNVERIFIABLE/required-unavailable rejected as an invalid branch name "
                                    "with repo-root evidence, got {}/{} detail={!r} evidence={!r}".format(
                                        value, r["status"], r["kind"], r["detail"], r["evidence"]))
            # The decoy-git cases below bind a decoy `git` through the module _GIT global (not PATH), because
            # _adapter_git resolves git through the captured absolute _GIT: overriding it SIMULATES a decoy
            # that was already first on PATH at process launch, which is exactly the residual the captured
            # absolute path cannot itself close. Each needs a POSIX shell; asserted where /bin/sh is present.
            this_mod = sys.modules[__name__]
            if os.path.exists("/bin/sh"):
                decoy_bin = tmp / "decoybin"
                decoy_bin.mkdir()
                # 31a-4. DISCRIMINATING (finding-3: the resolved commit id must be a strict sha): a decoy
                #      `git` that emits a NON-SHA line for show-ref (while echoing the surface root as the
                #      toplevel so the root-binding checks pass) must yield UNVERIFIABLE/required-unavailable,
                #      the detail naming a non-sha commit id, never a PASS carrying garbage as the branch sha.
                #      Removing the _is_commit_id sha-format check lets the decoy's line read PASS, failing it.
                decoy_root = tmp / "decoyroot"
                decoy_root.mkdir()
                decoy_git = decoy_bin / "git"
                decoy_git.write_text(
                    "#!/bin/sh\n"
                    "case \"$*\" in\n"
                    "  *--show-toplevel*) printf '%s\\n' \"{root}\" ;;\n"
                    "  *show-ref*) printf 'not-a-real-sha refs/heads/main\\n' ;;\n"
                    "esac\n"
                    "exit 0\n".format(root=decoy_root), encoding="utf-8")
                decoy_git.chmod(0o755)
                saved_git = this_mod._GIT
                this_mod._GIT = str(decoy_git)
                try:
                    r = discover("git", {"surfaces": {"git": {"adapter": "git", "required": True,
                                 "enabled": True, "protected-branch": "main"}}}, decoy_root)
                    if (r["status"] != UNVERIFIABLE or r["kind"] != KIND_REQUIRED_UNAVAILABLE
                            or "non-sha commit id" not in r["detail"]):
                        failures.append("git surface with a decoy git emitting a non-sha commit id expected "
                                        "UNVERIFIABLE/required-unavailable naming a non-sha commit id, got "
                                        "{}/{} detail={!r}".format(r["status"], r["kind"], r["detail"]))
                finally:
                    this_mod._GIT = saved_git

                # 31a-5. DISCRIMINATING (finding-1: the RETURNED ref field must equal the requested ref): a
                #      PRECISE-looking decoy `git` that emits a VALID-format sha but a MISMATCHED ref line
                #      (refs/heads/main for a request of refs/heads/fabricated) must be UNVERIFIABLE, the
                #      detail naming a mismatched ref, never a PASS on a self-constructed
                #      refs/heads/fabricated@<sha> evidence string. Its sha is a valid 40-hex, so ONLY the
                #      returned-ref-equality check can catch it: removing that check lets the mismatched line
                #      read PASS with the branch name the caller asked for fabricated into evidence.
                mm_root = tmp / "mismatchroot"
                mm_root.mkdir()
                mm_git = decoy_bin / "git-mismatch"
                mm_git.write_text(
                    "#!/bin/sh\n"
                    "case \"$*\" in\n"
                    "  *--show-toplevel*) printf '%s\\n' \"{root}\" ;;\n"
                    "  *show-ref*) printf '%s refs/heads/main\\n' {sha} ;;\n"
                    "esac\n"
                    "exit 0\n".format(root=mm_root, sha="a" * 40), encoding="utf-8")
                mm_git.chmod(0o755)
                saved_git = this_mod._GIT
                this_mod._GIT = str(mm_git)
                try:
                    r = discover("git", {"surfaces": {"git": {"adapter": "git", "required": True,
                                 "enabled": True, "protected-branch": "fabricated"}}}, mm_root)
                    if (r["status"] != UNVERIFIABLE or r["kind"] != KIND_REQUIRED_UNAVAILABLE
                            or "mismatched ref" not in r["detail"]):
                        failures.append("git surface with a decoy emitting a mismatched ref expected "
                                        "UNVERIFIABLE/required-unavailable naming a mismatched ref, got "
                                        "{}/{} detail={!r}".format(r["status"], r["kind"], r["detail"]))
                finally:
                    this_mod._GIT = saved_git

                # 31a-6. DISCRIMINATING (finding-2b: show-ref matches a LITERAL ref and does NOT apply
                #      revision operators, unlike rev-parse): a decoy `git` that ACCEPTS the grammar (so the
                #      value reaches resolution) and whose show-ref rejects a revision-suffix ref as a
                #      non-existent literal (exit 1) while its rev-parse WOULD resolve it models the exact
                #      divergence. Under the current `show-ref --verify --` resolution a main~0 value is
                #      UNVERIFIABLE; a revert to `rev-parse --verify --quiet refs/heads/main~0` resolves
                #      THROUGH the ~0 operator to main's commit and reads PASS. Asserting UNVERIFIABLE here
                #      catches that revert (with real git the check-ref-format grammar guard, test 31a-3,
                #      short-circuits main~0 first, so this decoy is what isolates the resolution layer).
                sv_root = tmp / "showrefroot"
                sv_root.mkdir()
                sv_git = decoy_bin / "git-showref"
                sv_git.write_text(
                    "#!/bin/sh\n"
                    "case \"$*\" in\n"
                    "  *--show-toplevel*) printf '%s\\n' \"{root}\" ;;\n"
                    "  *check-ref-format*) exit 0 ;;\n"
                    "  *show-ref*)\n"
                    "    case \"$*\" in *'~'*|*'^'*|*'@{{'*) exit 1 ;; esac\n"
                    "    printf '%s refs/heads/main\\n' {sha} ; exit 0 ;;\n"
                    "  *rev-parse*) printf '%s\\n' {sha} ; exit 0 ;;\n"
                    "esac\n"
                    "exit 0\n".format(root=sv_root, sha="a" * 40), encoding="utf-8")
                sv_git.chmod(0o755)
                saved_git = this_mod._GIT
                this_mod._GIT = str(sv_git)
                try:
                    r = discover("git", {"surfaces": {"git": {"adapter": "git", "required": True,
                                 "enabled": True, "protected-branch": cur + "~0"}}}, sv_root)
                    if r["status"] != UNVERIFIABLE or r["kind"] != KIND_REQUIRED_UNAVAILABLE:
                        failures.append("git surface with a decoy modelling show-ref literal matching expected "
                                        "UNVERIFIABLE/required-unavailable on a revision-suffix value (show-ref "
                                        "does not apply revision operators, unlike a rev-parse revert), got "
                                        "{}/{} detail={!r}".format(r["status"], r["kind"], r["detail"]))
                finally:
                    this_mod._GIT = saved_git

                # 31a-7. DISCRIMINATING (finding-2a: a LEADING-DASH branch value is rejected UP FRONT, before
                #      it can be handed to git as an option): a decoy `git` that ACCEPTS the grammar
                #      (check-ref-format exit 0) and would emit a self-consistent line for refs/heads/-x is
                #      used so that ONLY the leading-dash guard stands between the value and a PASS. With the
                #      `branch.startswith("-")` guard the value '-x' is UNVERIFIABLE (detail names an invalid
                #      branch name) before any resolution; removing that guard lets '-x' flow to the decoy's
                #      accepting grammar and self-consistent show-ref line and read PASS, failing this case.
                ld_root = tmp / "leadingdashroot"
                ld_root.mkdir()
                ld_git = decoy_bin / "git-leadingdash"
                ld_git.write_text(
                    "#!/bin/sh\n"
                    "case \"$*\" in\n"
                    "  *--show-toplevel*) printf '%s\\n' \"{root}\" ;;\n"
                    "  *check-ref-format*) exit 0 ;;\n"
                    "  *show-ref*) printf '%s refs/heads/-x\\n' {sha} ; exit 0 ;;\n"
                    "esac\n"
                    "exit 0\n".format(root=ld_root, sha="a" * 40), encoding="utf-8")
                ld_git.chmod(0o755)
                saved_git = this_mod._GIT
                this_mod._GIT = str(ld_git)
                try:
                    r = discover("git", {"surfaces": {"git": {"adapter": "git", "required": True,
                                 "enabled": True, "protected-branch": "-x"}}}, ld_root)
                    if (r["status"] != UNVERIFIABLE or r["kind"] != KIND_REQUIRED_UNAVAILABLE
                            or "not a valid branch name" not in r["detail"]):
                        failures.append("git surface with a leading-dash protected-branch '-x' expected "
                                        "UNVERIFIABLE/required-unavailable rejected as an invalid branch name "
                                        "before resolution, got {}/{} detail={!r}".format(
                                            r["status"], r["kind"], r["detail"]))
                finally:
                    this_mod._GIT = saved_git
            # 31b. DISCRIMINATING (finding-3: protected-branch must be a BRANCH ref, not any commit-ish): a
            #      raw SHA, a tag name, and HEAD each resolve to a commit but are NOT branch refs, so each is
            #      UNVERIFIABLE. Reverting the check to `rev-parse <value>^{commit}` accepts every one of
            #      them and reads PASS, failing these cases.
            for label, value in (("raw sha", head_sha), ("tag", "v-selftest"), ("HEAD", "HEAD")):
                r = discover("git", {"surfaces": {"git": {"adapter": "git", "required": True,
                             "enabled": True, "protected-branch": value}}}, gitrepo)
                if r["status"] != UNVERIFIABLE:
                    failures.append("git protected-branch given a {} ({!r}) that is not a branch expected "
                                    "UNVERIFIABLE, got {}".format(label, value, r["status"]))
            # 31c. DISCRIMINATING (finding-4: git evidence bound to root, ambient GIT_* scrubbed): with root a
            #      NON-repo directory and ambient GIT_DIR/GIT_WORK_TREE redirected at the real repo, the scrub
            #      binds the probe to root (a non-repo) -> UNVERIFIABLE. Reverting the env scrub lets git
            #      honour the redirect and read PASS for the unrelated repo, failing this case.
            nonrepo = tmp / "gitnonrepo"
            nonrepo.mkdir()
            saved_env = {k: os.environ.get(k) for k in ("GIT_DIR", "GIT_WORK_TREE")}
            os.environ["GIT_DIR"] = str(gitrepo / ".git")
            os.environ["GIT_WORK_TREE"] = str(nonrepo)
            try:
                r = discover("git", {"surfaces": {"git": {"adapter": "git", "required": True,
                             "enabled": True, "protected-branch": cur}}}, nonrepo)
                if r["status"] != UNVERIFIABLE:
                    failures.append("git surface on a non-repo root with an ambient GIT_* redirect expected "
                                    "UNVERIFIABLE, got {} (evidence must be bound to root, not an ambient "
                                    "GIT_DIR)".format(r["status"]))
            finally:
                for k, v in saved_env.items():
                    if v is None:
                        os.environ.pop(k, None)
                    else:
                        os.environ[k] = v
            # 31d. DISCRIMINATING (finding-4: toplevel realpath-equal to root): root a SUBDIRECTORY of the
            #      repo resolves a toplevel that differs from root -> UNVERIFIABLE. Removing the realpath
            #      equality check lets the subdirectory read PASS on the ancestor repo, failing this case.
            subdir = gitrepo / "sub"
            subdir.mkdir()
            r = discover("git", {"surfaces": {"git": {"adapter": "git", "required": True, "enabled": True,
                         "protected-branch": cur}}}, subdir)
            if r["status"] != UNVERIFIABLE:
                failures.append("git surface on a subdirectory root (toplevel != root) expected "
                                "UNVERIFIABLE, got {}".format(r["status"]))

        # 32. DISCRIMINATING (present-but-empty pinned env var): AIQT_ASSURANCE_CONFIG set to "" is a loud
        #     ConfigError (a pinned source that names nothing), never a silent fall-through to the nearest
        #     config, exactly like --config "". Reverting the env handling to a truthiness test lets the
        #     empty value read as absent and fall through, failing this case.
        try:
            resolve_config(environ={ENV_VAR: ""}, start=tmp)
            failures.append("resolve_config silently accepted a present-but-empty {}".format(ENV_VAR))
        except ConfigError:
            pass

        # 33. DISCRIMINATING (SKIP/available rejection): a SKIP result carrying the available kind is
        #     rejected by validate_result, the same as an UNVERIFIABLE/available (test 17 covers
        #     UNVERIFIABLE). Removing SKIP from the disallowed-status set lets a SKIP wear the available
        #     kind, failing this case.
        try:
            validate_result({"schema": RESULT_SCHEMA, "status": SKIP, "kind": KIND_AVAILABLE, "evidence": [],
                             "audit": "x", "summary": "s", "surface": None, "required": False})
            failures.append("validate_result accepted a SKIP carrying the available kind")
        except ValueError:
            pass

        # 33b. DISCRIMINATING (finding-3: the result SCHEMA is validated): a result whose schema is
        #      SUBSTITUTED, and one with NO schema field, are each rejected by validate_result/emit, for
        #      EVERY status (a PASS and a FAIL shown). Removing the schema-equality check lets a substituted
        #      or missing schema serialize as a clean line, failing these. The base object is otherwise
        #      well-formed (available kind, located evidence) so only the schema check can reject it.
        base = {"audit": "x", "status": PASS, "summary": "s", "surface": None, "required": True,
                "kind": KIND_AVAILABLE, "evidence": ["tools/x.py:1"]}
        try:
            emit(dict(base, schema="aiqt-qa-result/999"))
            failures.append("emit accepted a substituted schema on a PASS")
        except ValueError:
            pass
        try:
            emit(dict(base))  # no schema field at all
            failures.append("emit accepted a result with no schema field")
        except ValueError:
            pass
        try:
            validate_result({"status": FAIL, "kind": KIND_AVAILABLE, "evidence": ["tools/x.py:1"],
                             "schema": "aiqt-qa-result/999", "audit": "x", "summary": "s",
                             "surface": None, "required": True})
            failures.append("validate_result accepted a substituted schema on a FAIL")
        except ValueError:
            pass

        # 34. DISCRIMINATING (non-list adapter provenance): an adapter returning available=True with a
        #     NON-LIST provenance is a malformed probe, resolved UNVERIFIABLE/adapter-error at the
        #     return-type gate (detail names the malformed probe). Removing the provenance-type check in
        #     _validate_probe_return lets it reach _surface, which still downgrades but with a different
        #     (evidence-downgrade) detail, so asserting the malformed-probe detail discriminates this gate.
        def _badprov(spec, root):
            return (True, "detail", "not-a-list", "t")
        ADAPTERS["_selftest-badprov"] = _badprov
        try:
            r = discover("bp", {"surfaces": {"bp": {"adapter": "_selftest-badprov",
                         "required": True, "enabled": True}}}, tmp)
            if r["status"] != UNVERIFIABLE or "malformed probe" not in r["detail"]:
                failures.append("non-list provenance expected UNVERIFIABLE via the malformed-probe gate, "
                                "got {}/{}".format(r["status"], r["detail"]))
        finally:
            del ADAPTERS["_selftest-badprov"]

        # 35. DISCRIMINATING (adapter wrong arity): an adapter returning a 3-tuple (not the required
        #     4-tuple) is a malformed probe, resolved UNVERIFIABLE/adapter-error, never an unpack crash.
        #     Removing the arity check lets the 4-way unpack in _validate_probe_return raise and crash this
        #     self-test.
        def _arity(spec, root):
            return (True, "detail", ["ev"])
        ADAPTERS["_selftest-arity"] = _arity
        try:
            r = discover("ar", {"surfaces": {"ar": {"adapter": "_selftest-arity",
                         "required": True, "enabled": True}}}, tmp)
            if r["status"] != UNVERIFIABLE or "4-tuple" not in r["detail"]:
                failures.append("wrong-arity probe expected UNVERIFIABLE via the arity gate, got {}/{}".format(
                    r["status"], r["detail"]))
        finally:
            del ADAPTERS["_selftest-arity"]

        # 36. DISCRIMINATING (_surface wrong-kind PASS downgrade): a PASS handed to _surface with a kind
        #     OTHER than available (but otherwise valid evidence) is downgraded to UNVERIFIABLE, never
        #     returned as a PASS wearing the wrong kind. Removing the `kind == KIND_AVAILABLE` clause of the
        #     downgrade condition lets the mislabeled PASS through, failing this case.
        r = _surface("wk", "record-file", True, True, True, PASS, KIND_REQUIRED_UNAVAILABLE, "d", ["ev"], "t")
        if r["status"] == PASS:
            failures.append("_surface returned a PASS carrying a non-available kind (wrong-kind not downgraded)")

        # 37. DISCRIMINATING (boolean schema-version): a TOML boolean schema-version (true) is a loud
        #     ConfigError, never accepted as the integer 1 it equals (bool is an int subclass, and
        #     True in (1,) is True). Removing the isinstance(version, bool) exclusion lets true load,
        #     failing this case.
        bool_cfg = tmp / "bool-version.toml"
        bool_cfg.write_text("schema-version = true\n[surfaces.git]\nadapter='git'\n", encoding="utf-8")
        try:
            load_config(bool_cfg)
            failures.append("load_config accepted a boolean schema-version")
        except ConfigError:
            pass

        # 38. DISCRIMINATING (non-string adapter): a surface whose `adapter` is a non-string (an integer)
        #     is a loud ConfigError. Removing the adapter string-type check lets it load, failing this case.
        na = tmp / "nonstr-adapter.toml"
        na.write_text("schema-version = 1\n[surfaces.x]\nadapter = 123\n", encoding="utf-8")
        try:
            load_config(na)
            failures.append("load_config accepted a non-string adapter")
        except ConfigError:
            pass

        # 39. DISCRIMINATING (non-boolean required/enabled): a surface whose `required` is a non-boolean
        #     (a string) is a loud ConfigError. Removing the flag boolean-type check lets it load, failing
        #     this case.
        nb = tmp / "nonbool-flag.toml"
        nb.write_text("schema-version = 1\n[surfaces.x]\nadapter = 'git'\nrequired = 'yes'\n", encoding="utf-8")
        try:
            load_config(nb)
            failures.append("load_config accepted a non-boolean 'required' flag")
        except ConfigError:
            pass

        # 40. DISCRIMINATING (finding-1 + finding-2-i: canonical adapter-type substitution rejected): a config
        #     that binds a canonically-recognized surface to a DIFFERENT adapter than its canonical one
        #     resolves UNVERIFIABLE/adapter-mismatch carrying EXACTLY empty evidence ([]), never PASS and
        #     never a decoy evidence element, so a required surface cannot be rebound to the record-file
        #     adapter aimed at any present file and read PASS. Removing the adapter-type guard in discover()
        #     lets the substituted adapter probe and PASS; changing the mismatch return's evidence to a decoy
        #     (["decoy"]) fails the exact []-evidence assertion. Applies to every canonical surface
        #     (backlog/gate_roster/git), not just backlog.
        (tmp / "TODO.md").write_text("- [ ] x1 task\n", encoding="utf-8")  # a present file the decoy points at
        subst = (("backlog", "record-file"), ("gate_roster", "record-file"), ("git", "record-file"))
        for name, decoy in subst:
            r = discover(name, {"surfaces": {name: {"adapter": decoy, "required": True, "enabled": True,
                         "path": "TODO.md", "dir": "tools", "pattern": "check_*.py"}}}, tmp)
            if r["status"] != UNVERIFIABLE or r["kind"] != KIND_ADAPTER_MISMATCH or r["evidence"] != []:
                failures.append("canonical surface '{}' rebound to adapter {!r} expected UNVERIFIABLE/"
                                "adapter-mismatch with empty evidence, got {}/{} evidence={!r}".format(
                                    name, decoy, r["status"], r["kind"], r["evidence"]))
        # The canonical adapter for a canonical surface is accepted (no false mismatch): backlog on
        # gfm-task-list with the present TODO.md is a real PASS.
        r = discover("backlog", {"surfaces": {"backlog": {"adapter": "gfm-task-list", "required": True,
                     "enabled": True, "path": "TODO.md"}}}, tmp)
        if r["status"] != PASS:
            failures.append("backlog on its canonical adapter expected PASS, got {}/{}".format(
                r["status"], r["kind"]))

        # 41. DISCRIMINATING (finding-2: non-PASS malformed evidence rejected uniformly): evidence elements
        #     must be NON-EMPTY strings for EVERY status, not only PASS. make_result(FAIL, evidence=[7]) and
        #     emit of a FAIL with evidence=[""] are each refused, and _surface handed [7] for an UNVERIFIABLE
        #     yields clean (no malformed-element) evidence. Reverting the uniform element check (keeping the
        #     non-empty-string test only under `if status == PASS`) lets a FAIL/UNVERIFIABLE carry [7] or [""].
        try:
            make_result("x", FAIL, "s", surface=None, required=True, kind=KIND_AVAILABLE, evidence=[7])
            failures.append("make_result accepted a FAIL with a non-string evidence element [7]")
        except ValueError:
            pass
        try:
            emit({"schema": RESULT_SCHEMA, "audit": "x", "status": FAIL, "summary": "s", "surface": None,
                  "required": True, "kind": KIND_AVAILABLE, "evidence": [""]})
            failures.append("emit accepted a FAIL with an empty-string evidence element")
        except ValueError:
            pass
        r = _surface("me", "record-file", True, True, False, UNVERIFIABLE, KIND_REQUIRED_UNAVAILABLE,
                     "d", [7], "t")
        if any(not (isinstance(e, str) and e) for e in r["evidence"]):
            failures.append("_surface returned an UNVERIFIABLE with malformed evidence: {!r}".format(r["evidence"]))

        # 41b. DISCRIMINATING (finding-2-iv: the non-empty-string provenance clause of _validate_probe_return):
        #      an available probe returning an EMPTY-STRING provenance element (True, "detail", [""], "t") is
        #      a MALFORMED probe, caught at the return-type gate, so it resolves UNVERIFIABLE/adapter-error
        #      with the malformed-probe detail (naming the provenance fault) rather than reaching the PASS
        #      branch. Removing the `and p` non-empty-string clause from _validate_probe_return lets [""] pass
        #      validation, reach the PASS branch, and be downgraded by _surface with a DIFFERENT detail
        #      ("inconsistent PASS downgraded"), so asserting the malformed-probe detail discriminates the
        #      clause. (Distinct from test 34's non-LIST provenance: this is a list carrying an empty string.)
        def _emptyprov(spec, root):
            return (True, "detail", [""], "t")
        ADAPTERS["_selftest-emptyprov"] = _emptyprov
        try:
            r = discover("ep", {"surfaces": {"ep": {"adapter": "_selftest-emptyprov",
                         "required": True, "enabled": True}}}, tmp)
            if r["status"] != UNVERIFIABLE or r["kind"] != KIND_ADAPTER_ERROR or "malformed probe" not in r["detail"]:
                failures.append("empty-string provenance element expected UNVERIFIABLE/adapter-error via the "
                                "malformed-probe gate, got {}/{} detail={!r}".format(
                                    r["status"], r["kind"], r["detail"]))
        finally:
            del ADAPTERS["_selftest-emptyprov"]

        # 42. DISCRIMINATING (finding-6d: record-file 'path' is type-checked): a record-file surface with a
        #     LIST-valued 'path' is a loud ConfigError, so the record-file adapter's path field cannot load
        #     wrong-typed and then read PASS. Removing "record-file": ("path",) from ADAPTER_STR_FIELDS lets
        #     the list-valued path load, failing this case.
        rf = tmp / "recordfile-path.toml"
        rf.write_text("schema-version = 1\n[surfaces.register]\nadapter='record-file'\npath=['a','b']\n",
                      encoding="utf-8")
        try:
            load_config(rf)
            failures.append("load_config accepted a list-valued record-file 'path' field")
        except ConfigError:
            pass

        # 43. DISCRIMINATING (finding-5: pinned config resolves BEFORE --self-test dispatch): a subprocess
        #     given `--config <absent> --self-test`, or `AIQT_ASSURANCE_CONFIG="" --self-test`, exits 2 (the
        #     loud pinned-source resolution), never 0. Moving the resolve_config call back AFTER the
        #     --self-test dispatch lets the self-test run and exit 0 on a config the caller pinned, failing
        #     these. Guarded by the recursion sentinel so a regressed build cannot spawn nested children.
        if os.environ.get("AIQT_QA_SELFTEST_CHILD") != "1":
            selfpath = str(Path(__file__).resolve())
            childenv = dict(os.environ, AIQT_QA_SELFTEST_CHILD="1")
            proc = subprocess.run([sys.executable, "-I", "-B", selfpath,
                                   "--config", str(tmp / "no-such-config.toml"), "--self-test"],
                                  capture_output=True, text=True, env=childenv)
            if proc.returncode != 2:
                failures.append("pinned absent --config did not exit 2 under --self-test (pinned resolution "
                                "must precede the self-test dispatch), got {}".format(proc.returncode))
            proc = subprocess.run([sys.executable, "-I", "-B", selfpath, "--self-test"],
                                  capture_output=True, text=True,
                                  env=dict(childenv, AIQT_ASSURANCE_CONFIG=""))
            if proc.returncode != 2:
                failures.append("present-but-empty AIQT_ASSURANCE_CONFIG did not exit 2 under --self-test, "
                                "got {}".format(proc.returncode))

        # 44. DISCRIMINATING (finding-1: broken-symlink fail-closed applied CLASS-WIDE): a BROKEN SYMLINK (a
        #     present dentry whose target is missing) that gates a result must FAIL CLOSED, never read as
        #     absent, at EVERY presence-gating site. os.symlink is needed; asserted only where it is
        #     available and creating a dangling symlink succeeds.
        sroot = tmp / "symlinkclass"
        (sroot / ".aiqt").mkdir(parents=True)
        made_sym = False
        try:
            os.symlink(str(sroot / "no-such-target.toml"), str(sroot / ".aiqt" / "assurance.toml"))
            made_sym = True
        except (OSError, NotImplementedError, AttributeError):
            made_sym = False
        if made_sym:
            # 44a. the NEAREST-config walk (the round-12 BLOCKER): a dangling nearest .aiqt/assurance.toml is
            #      a ConfigError, never a silent fall-through to portable defaults. Reverting find_nearest_
            #      config's broken-symlink fail-closed (letting the FileNotFoundError `continue`) makes
            #      resolution fall through to portable defaults, failing this.
            try:
                resolve_config(start=sroot, environ={})
                failures.append("a dangling nearest .aiqt/assurance.toml fell through to portable defaults "
                                "(the round-12 BLOCKER) instead of raising ConfigError")
            except ConfigError:
                pass
            # 44b. the PINNED --config/env load: a --config pointing at a dangling symlink is a ConfigError
            #      (present-but-unreadable), never a silent fall-through. Reverting load_config's OSError ->
            #      ConfigError guard lets the FileNotFoundError escape or read as absent, failing this.
            try:
                resolve_config(explicit=str(sroot / ".aiqt" / "assurance.toml"), environ={})
                failures.append("a pinned --config at a dangling symlink was not a loud ConfigError")
            except ConfigError:
                pass
            # 44c. the GLOB gate ROSTER: a roster with one READABLE and one DANGLING candidate is
            #      UNVERIFIABLE (fail closed on the present-but-unreadable member), NOT a PASS that silently
            #      drops the dangling one. Reverting the roster's present-but-unreadable fail-closed (back to
            #      silently filtering unreadable candidates) reads PASS on the readable member, failing this.
            rtools = sroot / "tools"
            rtools.mkdir()
            (rtools / "check_ok.py").write_text("# gate\n", encoding="utf-8")
            os.symlink(str(rtools / "no-such-gate.py"), str(rtools / "check_broken.py"))
            r = discover("gate_roster", {"surfaces": {"gate_roster": {"adapter": "glob-roster",
                         "required": True, "enabled": True, "dir": "tools", "pattern": "check_*.py"}}}, sroot)
            if r["status"] != UNVERIFIABLE:
                failures.append("a gate roster with a readable AND a dangling candidate expected "
                                "UNVERIFIABLE (fail closed), got {} (a present-but-unreadable candidate must "
                                "not be silently dropped with the roster still PASS)".format(r["status"]))

        # 45. DISCRIMINATING (finding-2: result-contract structural completeness): validate_result enforces
        #     the mandatory identity fields present and typed, and the SKIP/required cross-field consistency,
        #     for every status, and make_result/emit route through it. A structurally-complete result is
        #     accepted; a SKIP marked required, a result missing a mandatory field, a wrong-typed identity
        #     field, and a make_result with required=None are each rejected. Removing the completeness or
        #     consistency checks lets one of these through.
        base_ok = {"schema": RESULT_SCHEMA, "status": FAIL, "summary": "s", "surface": None,
                   "required": True, "kind": KIND_AVAILABLE, "evidence": ["tools/x.py:1"], "audit": "ref"}
        try:
            validate_result(dict(base_ok))
        except ValueError as exc:
            failures.append("validate_result wrongly rejected a structurally-complete result: {}".format(exc))
        try:  # (a) a SKIP marked required is inconsistent (a required surface is never a SKIP)
            validate_result(dict(base_ok, status=SKIP, kind=KIND_OPTIONAL_DISABLED, evidence=[], required=True))
            failures.append("validate_result accepted a SKIP marked required")
        except ValueError:
            pass
        try:  # an optional (required=false) SKIP is accepted, so the reject isolates the required-true clause
            validate_result(dict(base_ok, status=SKIP, kind=KIND_OPTIONAL_DISABLED, evidence=[], required=False))
        except ValueError as exc:
            failures.append("validate_result wrongly rejected an optional SKIP: {}".format(exc))
        missing = dict(base_ok)  # (b) missing a mandatory identity field
        del missing["audit"]
        try:
            validate_result(missing)
            failures.append("validate_result accepted a result missing the mandatory 'audit' field")
        except ValueError:
            pass
        for label, bad in (("non-string audit", dict(base_ok, audit=123)),
                           ("non-bool required", dict(base_ok, required="yes")),
                           ("non-string surface", dict(base_ok, surface=7))):
            try:  # (c) wrong-typed identity fields
                validate_result(bad)
                failures.append("validate_result accepted a {}".format(label))
            except ValueError:
                pass
        try:  # make_result routes through the contract, so a required=None construction is refused
            make_result("ref", FAIL, "s", surface=None, kind=KIND_AVAILABLE, evidence=["tools/x.py:1"])
            failures.append("make_result accepted a construction with required=None")
        except ValueError:
            pass

        # 46. DISCRIMINATING (finding-3: an unknown option is a LOUD exit 2, never a silent default path): a
        #     subprocess given a misspelled --self-testx or a stray --bogus exits 2, never 0 by silently
        #     running the default digest path (which would let --self-testx masquerade as --self-test in CI
        #     parity). Removing reject_unknown_options lets the unknown option fall through, failing this.
        if os.environ.get("AIQT_QA_SELFTEST_CHILD") != "1":
            selfpath = str(Path(__file__).resolve())
            childenv = dict(os.environ, AIQT_QA_SELFTEST_CHILD="1")
            for badarg in ("--self-testx", "--bogus"):
                proc = subprocess.run([sys.executable, "-I", "-B", selfpath, badarg],
                                      capture_output=True, text=True, env=childenv)
                if proc.returncode != 2:
                    failures.append("unknown option {!r} expected a loud exit 2, got {}".format(
                        badarg, proc.returncode))

        # 47. DISCRIMINATING (finding-4 + finding-5: strict commit-id and absolute git resolution):
        #     _is_commit_id accepts a strict 40-hex and 64-hex sha and rejects a hex-but-wrong-length value,
        #     a right-length-but-non-hex value, an uppercased hex, an empty string, and a non-string;
        #     _resolve_git absolutizes a relative which result and falls back to the bare name; and _GIT is an
        #     absolute path when git is discoverable. Removing the length check accepts "a"*3, removing the
        #     hex check accepts "z"*40, narrowing to len==40 rejects a valid 64-hex, and reverting _resolve_
        #     git (or _GIT) to a relative/bare value fails the isabs assertions.
        if not _is_commit_id("a" * 40):
            failures.append("_is_commit_id rejected a valid 40-hex sha1")
        if not _is_commit_id("a" * 64):
            failures.append("_is_commit_id rejected a valid 64-hex sha256")
        for bad in ("a" * 3, "a" * 39, "a" * 41, "a" * 63, "z" * 40, "A" * 40, "", 40 * "a" + "g"):
            if _is_commit_id(bad):
                failures.append("_is_commit_id accepted a non-sha value len={} {!r}".format(len(bad), bad[:8]))
        if _is_commit_id(1234567890):  # a non-string (int) is rejected by the isinstance guard
            failures.append("_is_commit_id accepted a non-string value")
        if not os.path.isabs(_resolve_git("bin/git")):
            failures.append("_resolve_git did not absolutize a RELATIVE which result")
        if not os.path.isabs(_resolve_git("/usr/bin/git")):
            failures.append("_resolve_git did not preserve an absolute which result")
        if _resolve_git(None) != "git":
            failures.append("_resolve_git did not fall back to the bare 'git' when git is not found")
        if shutil.which("git") and not os.path.isabs(_GIT):
            failures.append("_GIT expected an absolute path when git is discoverable, got {!r}".format(_GIT))

        # 48. DISCRIMINATING (finding-4: the show-ref line must have EXACTLY two fields): a decoy git emitting
        #     a THREE-field show-ref line (a valid sha, the requested ref, and an extra token) with an
        #     accepting grammar is UNVERIFIABLE (a malformed line), never a PASS that reads only the first two
        #     fields. Removing the `len(fields) != 2` check lets fields[0]/fields[1] read the valid sha and
        #     matching ref and PASS. Needs a POSIX /bin/sh; asserted where it is present.
        if os.path.exists("/bin/sh"):
            this_mod = sys.modules[__name__]
            mf_bin = tmp / "malformed-decoybin"
            mf_bin.mkdir()
            mf_root = tmp / "malformedfieldroot"
            mf_root.mkdir()
            mf_git = mf_bin / "git"
            mf_git.write_text(
                "#!/bin/sh\n"
                "case \"$*\" in\n"
                "  *--show-toplevel*) printf '%s\\n' \"{root}\" ;;\n"
                "  *check-ref-format*) exit 0 ;;\n"
                "  *show-ref*) printf '%s refs/heads/main extra\\n' {sha} ; exit 0 ;;\n"
                "esac\n"
                "exit 0\n".format(root=mf_root, sha="a" * 40), encoding="utf-8")
            mf_git.chmod(0o755)
            saved_git = this_mod._GIT
            this_mod._GIT = str(mf_git)
            try:
                r = discover("git", {"surfaces": {"git": {"adapter": "git", "required": True,
                             "enabled": True, "protected-branch": "main"}}}, mf_root)
                if r["status"] != UNVERIFIABLE or "malformed line" not in r["detail"]:
                    failures.append("git surface with a decoy emitting a 3-field show-ref line expected "
                                    "UNVERIFIABLE naming a malformed line, got {}/{} detail={!r}".format(
                                        r["status"], r["kind"], r["detail"]))
            finally:
                this_mod._GIT = saved_git

        # 49. DISCRIMINATING (finding-2: every MANDATORY result key must be PRESENT): starting from a
        #     structurally-complete result, DELETING each mandatory key in turn is rejected. `surface` is the
        #     key this closes: it is legitimately None-valued, so a MISSING 'surface' key would otherwise read
        #     as surface=None and slip; `kind` on a FAIL is likewise otherwise-unconstrained. Removing the
        #     MANDATORY_RESULT_KEYS presence loop lets the surface and kind omissions through, failing this.
        wf_result = {"schema": RESULT_SCHEMA, "audit": "x", "status": FAIL, "summary": "s",
                     "surface": None, "required": True, "kind": KIND_AVAILABLE, "evidence": ["tools/x.py:1"]}
        try:
            validate_result(dict(wf_result))  # the structurally-complete result is accepted
        except ValueError as exc:
            failures.append("validate_result wrongly rejected a structurally-complete result: {}".format(exc))
        for key in MANDATORY_RESULT_KEYS:
            missing = dict(wf_result)
            del missing[key]
            try:
                validate_result(missing)
                failures.append("validate_result accepted a result missing the mandatory key {!r}".format(key))
            except ValueError:
                pass

        # 50. DISCRIMINATING (finding-3: a SKIP carries EXACTLY the optional-disabled kind): a legit
        #     optional SKIP (optional-disabled kind) is accepted; a SKIP wearing a required-unavailable,
        #     available, arbitrary, or NON-STRING kind is refused, and make_result routes through the same
        #     check. Removing the SKIP-kind clause lets an inconsistent SKIP kind through, failing this.
        skip_ok = {"schema": RESULT_SCHEMA, "audit": "x", "status": SKIP, "summary": "s", "surface": None,
                   "required": False, "kind": KIND_OPTIONAL_DISABLED, "evidence": []}
        try:
            validate_result(dict(skip_ok))  # a consistent optional SKIP is accepted
        except ValueError as exc:
            failures.append("validate_result wrongly rejected a consistent optional SKIP: {}".format(exc))
        for badkind in (KIND_REQUIRED_UNAVAILABLE, KIND_AVAILABLE, "arbitrary-kind", 123, None):
            try:
                validate_result(dict(skip_ok, kind=badkind))
                failures.append("validate_result accepted a SKIP with an inconsistent kind {!r}".format(badkind))
            except ValueError:
                pass
        try:
            make_result("x", SKIP, "s", surface=None, required=False, kind=KIND_REQUIRED_UNAVAILABLE)
            failures.append("make_result accepted a SKIP with a required-unavailable kind")
        except ValueError:
            pass

        # 51. DISCRIMINATING (finding-4: 'summary' is validated and the non-empty clause holds): a
        #     present-but-EMPTY or present-but-NON-STRING summary is rejected (so removing 'summary' from the
        #     ("audit", "summary") identity tuple fails a case), and a present-but-EMPTY audit and summary are
        #     each rejected (so removing the `or not value` non-empty clause, which lets an empty string pass
        #     the isinstance test, fails a case).
        for badsummary in ("", 123):
            try:
                validate_result(dict(wf_result, summary=badsummary))
                failures.append("validate_result accepted a bad summary {!r} (summary must be validated "
                                "in the identity tuple)".format(badsummary))
            except ValueError:
                pass
        for field in ("audit", "summary"):
            try:
                validate_result(dict(wf_result, **{field: ""}))
                failures.append("validate_result accepted an empty {!r} (the non-empty clause must "
                                "reject it)".format(field))
            except ValueError:
                pass

        # 52. DISCRIMINATING (finding-1: a NON-REGULAR pinned --config FAILS CLOSED, never blocks on open): a
        #     FIFO passed via --config is classified non-regular BEFORE any open, so a subprocess exits 2
        #     promptly rather than BLOCKING on open(FIFO) (which would time out). Reverting load_config to a
        #     bare read_bytes() (no _classify_presence guard) blocks on the FIFO and the subprocess times out,
        #     failing this. Needs os.mkfifo (POSIX); asserted where it is available and the sentinel is unset.
        if hasattr(os, "mkfifo") and os.environ.get("AIQT_QA_SELFTEST_CHILD") != "1":
            fifo = tmp / "config.fifo"
            made_fifo = False
            try:
                os.mkfifo(str(fifo))
                made_fifo = True
            except OSError:
                made_fifo = False
            if made_fifo:
                selfpath = str(Path(__file__).resolve())
                childenv = dict(os.environ, AIQT_QA_SELFTEST_CHILD="1")
                try:
                    proc = subprocess.run([sys.executable, "-I", "-B", selfpath, "--config", str(fifo)],
                                          capture_output=True, text=True, env=childenv, timeout=20)
                    if proc.returncode != 2:
                        failures.append("a FIFO passed via --config expected a loud exit 2 (fail closed), "
                                        "got {}".format(proc.returncode))
                except subprocess.TimeoutExpired:
                    failures.append("a FIFO passed via --config BLOCKED on open instead of failing closed "
                                    "(the non-regular-file hang; _classify_presence must precede any open)")
                finally:
                    fifo.unlink()

        # 53. DISCRIMINATING (finding-5: the module-level _GIT routes through _resolve_git): reverting the
        #     global `_GIT = _resolve_git(shutil.which("git"))` to `shutil.which("git") or "git"` is invisible
        #     when git resolves to an ABSOLUTE path (os.path.abspath is idempotent), so this exercises the one
        #     case that separates them: a `git` reachable ONLY through a RELATIVE PATH component. In a child
        #     with PATH set to a relative dir holding a `git`, the module-level _GIT is absolute ONLY if the
        #     assignment routed through _resolve_git; the reverted form leaves it relative. Needs a writable,
        #     executable git-like stub; asserted where one can be created and the sentinel is unset.
        if os.environ.get("AIQT_QA_SELFTEST_CHILD") != "1":
            relroot = tmp / "relgitroot"
            (relroot / "relbin").mkdir(parents=True)
            gitstub = relroot / "relbin" / "git"
            can_exec = False
            try:
                gitstub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
                gitstub.chmod(0o755)
                can_exec = os.access(str(gitstub), os.X_OK)
            except OSError:
                can_exec = False
            if can_exec:
                toolsdir = str(Path(__file__).resolve().parent)
                probe = ("import sys; sys.path.append({tools!r}); import _qa_adapter as qa; "
                         "print(qa._GIT)").format(tools=toolsdir)
                childenv = dict(os.environ, AIQT_QA_SELFTEST_CHILD="1", PATH="relbin")
                proc = subprocess.run([sys.executable, "-I", "-B", "-c", probe], cwd=str(relroot),
                                      capture_output=True, text=True, env=childenv)
                out = proc.stdout.strip()
                if proc.returncode != 0:
                    failures.append("finding-5 _GIT probe subprocess failed: rc={} err={!r}".format(
                        proc.returncode, proc.stderr))
                elif out != "git" and not os.path.isabs(out):
                    failures.append("module-level _GIT did not route through _resolve_git: a git reachable "
                                    "via a RELATIVE PATH left _GIT relative ({!r}); the global must "
                                    "absolutize".format(out))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if failures:
        print("SELF-TEST FAIL:")
        for f in failures:
            print("  - " + f)
        return 1
    print("SELF-TEST PASS: a present required surface is PASS; a MISSING required surface is UNVERIFIABLE "
          "(never PASS); a DIRECTORY, a non-regular file, or a present-but-UNREADABLE (chmod 000, off-root) "
          "file at a file-backed surface is UNVERIFIABLE (regularity and existence are not readability), and "
          "the gate roster counts only readable regular files (an unreadable-only roster is UNVERIFIABLE, and "
          "a roster with a readable AND a present-but-unreadable candidate fails CLOSED to UNVERIFIABLE, "
          "never silently dropping the unreadable one); a BROKEN SYMLINK (a present dentry whose target is "
          "missing) is present-but-unreadable, not absent, at EVERY presence-gating site (the nearest-config "
          "walk, the pinned --config/env load, and the gate roster each fail closed, so a dangling nearest "
          "config raises ConfigError rather than falling through to portable defaults); "
          "optional-disabled is SKIP (never malformed-required); a disabled required surface is a distinct "
          "malformed-UNVERIFIABLE, and a config can NEVER downgrade a canonically-required surface (an "
          "enabled=false or present-but-empty backlog) to an optional SKIP, nor REBIND a canonically-"
          "recognized surface (backlog/gate_roster/git) to a different adapter type (UNVERIFIABLE/adapter-"
          "mismatch, never a PASS on the substituted adapter); the git surface returns PASS only when the "
          "configured protected branch exists as an actual BRANCH ref (refs/heads/<branch>), with the "
          "returned show-ref ref field required to equal the requested ref (the show-ref line required to "
          "carry EXACTLY two fields, a three-field line rejected as malformed) and the located evidence "
          "derived from git's own output, UNVERIFIABLE for a raw sha, tag, HEAD, other non-branch value, a "
          "leading-dash value, or a decoy git returning a mismatched ref or a non-sha commit id, where a "
          "commit id is a strict 40-hex or 64-hex LOWERCASE sha (a valid 64-hex sha256 accepted; a "
          "hex-but-wrong-length, a right-length-but-non-hex, an uppercased-hex, an empty, or a non-string "
          "value rejected), and the git executable resolved ONCE to an ABSOLUTE path when discoverable; "
          "and the git probe is bound to root (ambient GIT_* scrubbed, toplevel realpath-equal to root) so "
          "an ambient GIT_DIR cannot bind a non-repo root to another repository, with the MODULE-LEVEL "
          "_GIT routing through _resolve_git (proven via a git reachable only on a relative PATH, where "
          "only routing absolutizes it); an unknown adapter, an "
          "adapter that RAISES, and an adapter whose "
          "RETURN is malformed (a truthy-string available, a non-list provenance, or a wrong-arity tuple) "
          "are each UNVERIFIABLE (never a crash or a pass), and an available return with empty located "
          "evidence is fail-safe-downgraded at the discover boundary rather than handed back as a false "
          "PASS; make_result rejects a non-list (bare-string) evidence at construction rather than "
          "char-splitting it, and _surface downgrades a char-split non-list provenance and a wrong-kind "
          "PASS instead of returning a false green; the result contract rejects a substituted or missing "
          "schema tag (for every status), an out-of-set status, a structurally-incomplete or wrong-typed "
          "result (a missing or wrong-typed mandatory identity field: audit/summary non-empty strings, "
          "surface a string or None, required a bool) and the SKIP/required cross-field inconsistency (a SKIP "
          "can never be marked required), with make_result and emit routed through the same choke point, a "
          "non-list evidence, evidence elements that are not non-empty strings for EVERY status uniformly "
          "(a FAIL or UNVERIFIABLE carrying [7] or [\"\"] is refused, not only a PASS), and an inconsistent "
          "result (a PASS with an unavailable kind or without a non-empty list of non-empty evidence "
          "strings, or an UNVERIFIABLE/SKIP wearing the available kind) while ACCEPTING a legitimate FAIL "
          "on an available surface; load_config rejects a "
          "wrong-typed surface schema (a non-table surface entry, a non-string adapter, a non-boolean "
          "required/enabled flag, or a wrong-typed adapter-specific field such as a list-valued "
          "protected-branch or a table-valued remote-landing-provider) and a missing, wrong-typed, boolean, "
          "or unknown schema-version (a present integer drawn from the supported set); config resolution is "
          "fail-loud on a pinned config, including a NON-REGULAR pinned --config (a FIFO, classified "
          "non-regular BEFORE any open so it fails closed rather than BLOCKING on open), and on a malformed "
          "--config operand (absent, empty, =-joined-empty, "
          "a next-flag, or a duplicate), with a present-but-empty AIQT_ASSURANCE_CONFIG treated as a loud "
          "ConfigError on the SAME pinned-source path as --config, and both argument validation and the "
          "loud pinned-source resolution preceding the --self-test dispatch (a pinned-but-absent/empty "
          "config exits 2 even under --self-test), and an UNRECOGNIZED option (a misspelled --self-testx or a "
          "stray flag) is a loud exit 2, never a silent default path that would let it masquerade as "
          "--self-test; the config root is derived consistently so an explicit "
          ".aiqt config "
          "resolves surfaces at the tree root; and discover_all enumerates the full expected set so a "
          "required surface omitted from config resolves UNVERIFIABLE.")
    return 0


def main():
    argv = sys.argv[1:]
    # ARGUMENT VALIDATION precedes the --self-test dispatch, so a malformed operand (e.g. `--config
    # --self-test`, which must NOT swallow the flag as a path) is a loud error rather than a silent
    # self-test run that exits 0.
    try:
        explicit = config_arg(argv)
    except ValueError as exc:
        print("error: {}".format(exc), file=sys.stderr)
        return 2
    # UNKNOWN-OPTION REJECTION also precedes the --self-test dispatch: an unrecognized option (a misspelled
    # --self-testx, a stray --bogus) is a LOUD exit 2, never a silent fall-through to the default digest path
    # that would exit 0 without running the self-test the caller asked for.
    unknown = reject_unknown_options(argv, {"--self-test"})
    if unknown is not None:
        print("error: unrecognized option {!r}".format(unknown), file=sys.stderr)
        return 2
    # The loud PINNED-source resolution runs BEFORE the --self-test dispatch, so a pinned-but-absent or
    # pinned-but-empty config (an explicit --config PATH that does not exist, or AIQT_ASSURANCE_CONFIG set
    # to "") is a loud exit 2 even under --self-test, rather than the self-test silently running and exiting
    # 0 on a config the caller pinned. resolve_config raises ConfigError on such a pinned source; with no
    # pinned source it walks to the nearest/portable config (never an error).
    try:
        cfg, root, prov = resolve_config(explicit=explicit)
    except ConfigError as exc:
        print("error: {}".format(exc), file=sys.stderr)
        return 2
    if "--self-test" in argv:
        return _self_test()
    # Default: print the surface digest for the resolved config (a read-only view).
    surfaces = discover_all(cfg, root)
    print("config: {}".format(prov))
    print(render_digest(surfaces))
    return 0


if __name__ == "__main__":
    sys.exit(main())
