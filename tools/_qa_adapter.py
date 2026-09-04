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
    green (for example status PASS with a required-unavailable kind and no evidence). A PASS must carry the
    available kind AND non-empty evidence; conversely the available kind cannot pair with any non-PASS
    status, so an UNVERIFIABLE, SKIP, or FAIL never wears an 'available' label. Raises ValueError on an
    inconsistent result; returns it unchanged when consistent. is_pass alone checks only the status field,
    so this is the guard that keeps status, kind, and evidence from disagreeing."""
    status = result.get("status")
    if status not in STATUSES:
        raise ValueError("status {!r} is not one of {}".format(status, ", ".join(STATUSES)))
    kind = result.get("kind")
    evidence = result.get("evidence") or []
    if status == PASS:
        if kind != KIND_AVAILABLE:
            raise ValueError("a PASS result must carry the {!r} kind, got {!r}".format(KIND_AVAILABLE, kind))
        if not evidence:
            raise ValueError("a PASS result must carry non-empty evidence")
    elif kind == KIND_AVAILABLE:
        raise ValueError("kind {!r} cannot pair with a non-PASS status {!r}".format(kind, status))
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
    """Extract the value of a `--config` option from an argv list. `--config` with NO operand (it is the
    last token) is a LOUD error (ValueError), never a silent fall-through to a lower-precedence resolution
    step: a caller that typed --config meant to pin a config. Returns None when --config is absent."""
    if "--config" not in argv:
        return None
    i = argv.index("--config")
    if i + 1 >= len(argv):
        raise ValueError("--config requires a path operand")
    return argv[i + 1]


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
        matches = sorted(p.name for p in d.glob(pattern) if p.is_file())
    except OSError as exc:
        return False, "gate roster dir not readable ({})".format(exc), [], str(d)
    if not matches:
        return False, "no gates match {}/{}".format(spec.get("dir", "tools"), pattern), [], str(d)
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


def _surface(name, adapter, required, enabled, available, status, kind, detail, provenance, target):
    return {"name": name, "adapter": adapter, "required": required, "enabled": enabled,
            "available": available, "status": status, "kind": kind, "detail": detail,
            "evidence": list(provenance or []), "target": target}


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
        available, detail, provenance, target = probe(spec, root)
    except Exception as exc:  # noqa: BLE001  an adapter that RAISES is a cannot-evaluate, never a crash
        # An adapter fault is UNVERIFIABLE with an adapter-error kind, so a broken probe surfaces loudly
        # instead of crashing discovery or (worse) reading as a pass. Deleting this wrap lets the exception
        # escape and crash the caller, which is exactly what the self-test's raising-adapter case catches.
        return _surface(name, adapter, required, enabled, False, UNVERIFIABLE, KIND_ADAPTER_ERROR,
                        "adapter {!r} raised: {}".format(adapter, exc), [], None)
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
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if failures:
        print("SELF-TEST FAIL:")
        for f in failures:
            print("  - " + f)
        return 1
    print("SELF-TEST PASS: a present required surface is PASS; a MISSING required surface is UNVERIFIABLE "
          "(never PASS); a DIRECTORY or unreadable file at a file-backed surface is UNVERIFIABLE (existence "
          "is not readability); optional-disabled is SKIP (never malformed-required); a disabled required "
          "surface is a distinct malformed-UNVERIFIABLE; an unknown adapter and an adapter that RAISES are "
          "each UNVERIFIABLE (never a crash or a pass); the result contract rejects an out-of-set status "
          "and an inconsistent result (a PASS with an unavailable kind or no evidence); load_config rejects "
          "a wrong-typed surface schema; config resolution is fail-loud on a pinned config and on a "
          "--config with no operand; the config root is derived consistently so an explicit .aiqt config "
          "resolves surfaces at the tree root; and discover_all enumerates the full expected set so a "
          "required surface omitted from config resolves UNVERIFIABLE.")
    return 0


def main():
    argv = sys.argv[1:]
    if "--self-test" in argv:
        return _self_test()
    # Default: resolve the config for the current tree and print the surface digest (a read-only view).
    try:
        explicit = config_arg(argv)
    except ValueError as exc:
        print("error: {}".format(exc), file=sys.stderr)
        return 2
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
