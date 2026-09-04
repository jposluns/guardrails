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

RESULT_SCHEMA = "aiqt-qa-result/1"


class ConfigError(Exception):
    """A pinned config (explicit --config or AIQT_ASSURANCE_CONFIG) is absent, unreadable, or malformed.
    Raised loudly so resolution never silently falls through to a lower-precedence source."""


def make_result(audit, status, summary, surface=None, required=None, kind=None, evidence=None):
    """Build a normalized result object. `evidence` is a list of provenance strings (path:line, a git
    ref, a run id): the located basis for the verdict, never the verdict itself. Rejects a status outside
    the fixed set so a typo can never smuggle a fifth, unhandled state past the contract."""
    if status not in STATUSES:
        raise ValueError("status {!r} is not one of {}".format(status, ", ".join(STATUSES)))
    return {
        "schema": RESULT_SCHEMA,
        "audit": audit,
        "status": status,
        "summary": summary,
        "surface": surface,
        "required": required,
        "kind": kind,
        "evidence": list(evidence or []),
    }


def validate_result(result):
    """Enforce the mutual consistency of a result-contract object so a caller cannot hand-build a false
    green (for example status PASS with a required-unavailable kind and no evidence). `evidence` must be a
    LIST; a PASS additionally requires it to be a NON-EMPTY list of NON-EMPTY strings, so an empty string or
    a non-list can never stand in for located evidence. A PASS must carry the available kind. The available
    kind is legitimate on a PASS and on a FAIL: a FAIL on an AVAILABLE surface is a real verdict (the
    surface was present and probed, and the audit's own check observed the property violated), so only
    UNVERIFIABLE and SKIP, the missing-evidence and declared-no-op states, may not wear an 'available'
    label. Raises ValueError on an inconsistent result; returns it unchanged when consistent. is_pass alone
    checks only the status field, so this is the guard that keeps status, kind, and evidence from disagreeing."""
    status = result.get("status")
    if status not in STATUSES:
        raise ValueError("status {!r} is not one of {}".format(status, ", ".join(STATUSES)))
    kind = result.get("kind")
    evidence = result.get("evidence")
    if not isinstance(evidence, list):
        raise ValueError("a result's evidence must be a list, got {}".format(type(evidence).__name__))
    if status == PASS:
        if kind != KIND_AVAILABLE:
            raise ValueError("a PASS result must carry the {!r} kind, got {!r}".format(KIND_AVAILABLE, kind))
        if not evidence or not all(isinstance(e, str) and e for e in evidence):
            raise ValueError("a PASS result must carry a non-empty list of non-empty evidence strings")
    elif status in (UNVERIFIABLE, SKIP) and kind == KIND_AVAILABLE:
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


def load_config(path):
    """Parse an assurance.toml. Raises ConfigError on an absent, unreadable, or malformed file, so a
    pinned config that cannot be honoured is loud, never a silent empty pass."""
    try:
        raw = Path(path).read_bytes()
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
    return cfg


def find_nearest_config(start):
    """Walk up from `start` returning the nearest existing .aiqt/assurance.toml, or None. os.stat (not
    exists()) so a present-but-unreadable config surfaces as ConfigError via load_config rather than
    reading as absent."""
    start = Path(start).resolve()
    for anc in [start, *start.parents]:
        cand = anc.joinpath(*CONFIG_RELPARTS)
        try:
            os.stat(cand)
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise ConfigError("cannot stat candidate config {}: {}".format(cand, exc))
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
    if explicit:
        cfg = load_config(explicit)                      # step 1: loud on failure, no fall-through
        return cfg, _config_root(explicit), "explicit --config {}".format(explicit)
    env_val = environ.get(ENV_VAR)
    if env_val:
        cfg = load_config(env_val)                       # step 2: loud on failure, no fall-through
        return cfg, _config_root(env_val), "{}={}".format(ENV_VAR, env_val)
    nearest = find_nearest_config(start)                 # step 3
    if nearest is not None:
        cfg = load_config(nearest)
        return cfg, _config_root(nearest), "nearest {}".format(nearest)
    return PORTABLE_DEFAULTS, start, "portable defaults"  # step 4


# --- shipped adapter types (selected by config; each returns an availability probe) ------------------
# An adapter returns (available: bool, detail: str, provenance: list[str], target: str|None). It probes
# ONLY whether the surface resolves to real, readable evidence; it never decides an audit's verdict.

def _probe_readable_file(target):
    """A file-backed surface is available only when `target` is a REGULAR file that actually opens and
    reads: a bare os.stat would let a DIRECTORY or an UNREADABLE file at the path read as available, which
    the caller would then turn into a false PASS. Returns (ok, why): ok is False for an absent path (stat
    raises), a directory or other non-regular file, and a file that cannot be opened and read, so every
    such case resolves to available=False (which discover() turns into UNVERIFIABLE). `why` names the
    reason for the detail string."""
    try:
        st = os.stat(target)
    except OSError as exc:
        return False, "not readable ({})".format(exc)
    if not stat.S_ISREG(st.st_mode):
        return False, "not a regular file"
    try:
        with open(target, "rb") as fh:
            fh.read(1)
    except OSError as exc:
        return False, "not readable ({})".format(exc)
    return True, ""


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
    # hold to: an unreadable or non-regular match is not a present gate and is not counted, consistent with
    # the readable-file probe. If that empties the roster the surface is UNVERIFIABLE (the caller's not-
    # available branch), never a PASS on a directory of unreadable files.
    matches = [p.name for p in candidates if _probe_readable_file(p)[0]]
    if not matches:
        return False, "no readable gates match {}/{}".format(spec.get("dir", "tools"), pattern), [], str(d)
    sample = ", ".join(matches[:3]) + (" ..." if len(matches) > 3 else "")
    return True, "{} gate(s) in roster ({})".format(len(matches), sample), [str(d / m) for m in matches[:3]], str(d)


def _adapter_git(spec, root):
    try:
        proc = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=str(root),
                              capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, "git not runnable ({})".format(exc), [], None
    if proc.returncode != 0:
        return False, "not a git repository", [], None
    top = proc.stdout.strip()
    branch = spec.get("protected-branch", "main")
    provider = spec.get("remote-landing-provider", "")
    detail = "git root at repo toplevel, protected branch '{}'".format(branch)
    if provider:
        detail += ", remote-landing provider '{}'".format(provider)
    return True, detail, [top], top


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
    if not isinstance(provenance, list) or not all(isinstance(p, str) for p in provenance):
        return False, "provenance must be a list of strings"
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
    evidence = list(provenance or [])
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
    required = bool(spec.get("required", False))
    enabled = bool(spec.get("enabled", False))

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
            validate_result({"status": FAIL, "kind": KIND_AVAILABLE, "evidence": ["tools/x.py:1"]})
        except ValueError as exc:
            failures.append("validate_result wrongly rejected a legitimate FAIL/available result: {}".format(exc))
        try:
            validate_result({"status": UNVERIFIABLE, "kind": KIND_AVAILABLE, "evidence": []})
            failures.append("validate_result accepted an UNVERIFIABLE carrying the available kind")
        except ValueError:
            pass

        # 18. DISCRIMINATING (evidence typing): a PASS whose evidence is [""] (a non-empty list of an EMPTY
        #     string) or a bare string (not a list) is rejected. Dropping the non-empty-strings or list-type
        #     checks lets a hollow PASS through, failing these.
        try:
            validate_result({"status": PASS, "kind": KIND_AVAILABLE, "evidence": [""]})
            failures.append("validate_result accepted a PASS with an empty-string evidence entry")
        except ValueError:
            pass
        try:
            validate_result({"status": PASS, "kind": KIND_AVAILABLE, "evidence": "tools/x.py:1"})
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
          "the gate roster counts only readable regular files (an unreadable-only roster is UNVERIFIABLE); "
          "optional-disabled is SKIP (never malformed-required); a disabled required surface is a distinct "
          "malformed-UNVERIFIABLE; an unknown adapter, an adapter that RAISES, and an adapter whose RETURN "
          "is malformed (a truthy-string available, a non-list provenance) are each UNVERIFIABLE (never a "
          "crash or a pass), and an available return with empty located evidence is fail-safe-downgraded at "
          "the discover boundary rather than handed back as a false PASS; the result contract rejects an "
          "out-of-set status, a non-list evidence, and an inconsistent result (a PASS with an unavailable "
          "kind or without a non-empty list of non-empty evidence strings, or an UNVERIFIABLE/SKIP wearing "
          "the available kind) while ACCEPTING a legitimate FAIL on an available surface; load_config "
          "rejects a wrong-typed surface schema and a missing or wrong-typed required key (schema-version); "
          "config resolution is fail-loud on a pinned config and on a malformed --config operand (absent, "
          "empty, =-joined-empty, a next-flag, or a duplicate), with argument validation preceding the "
          "--self-test dispatch; the config root is derived consistently so an explicit .aiqt config "
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
    if "--self-test" in argv:
        return _self_test()
    # Default: resolve the config for the current tree and print the surface digest (a read-only view).
    try:
        cfg, root, prov = resolve_config(explicit=explicit)
    except ConfigError as exc:
        print("error: {}".format(exc), file=sys.stderr)
        return 2
    surfaces = discover_all(cfg, root)
    print("config: {}".format(prov))
    print(render_digest(surfaces))
    return 0


if __name__ == "__main__":
    sys.exit(main())
