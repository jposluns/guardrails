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


def emit(result):
    """Serialize a result object to a single deterministic JSON line (sorted keys, no wall clock in the
    object itself), so an audit's output is a stable, parseable evidence record."""
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
    if not isinstance(cfg.get("surfaces"), dict):
        raise ConfigError("assurance config {} has no [surfaces] table".format(path))
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


def resolve_config(explicit=None, environ=None, start=None):
    """Resolve (cfg, root, provenance) by the fail-loud order above. `root` is the directory the config
    lives in (or `start` for portable defaults); relative surface paths resolve under it."""
    environ = os.environ if environ is None else environ
    start = Path(start or Path.cwd()).resolve()
    if explicit:
        cfg = load_config(explicit)                      # step 1: loud on failure, no fall-through
        return cfg, Path(explicit).resolve().parent, "explicit --config {}".format(explicit)
    env_val = environ.get(ENV_VAR)
    if env_val:
        cfg = load_config(env_val)                       # step 2: loud on failure, no fall-through
        return cfg, Path(env_val).resolve().parent, "{}={}".format(ENV_VAR, env_val)
    nearest = find_nearest_config(start)                 # step 3
    if nearest is not None:
        cfg = load_config(nearest)
        return cfg, nearest.parent.parent, "nearest {}".format(nearest)
    return PORTABLE_DEFAULTS, start, "portable defaults"  # step 4


# --- shipped adapter types (selected by config; each returns an availability probe) ------------------
# An adapter returns (available: bool, detail: str, provenance: list[str], target: str|None). It probes
# ONLY whether the surface resolves to real, readable evidence; it never decides an audit's verdict.

def _adapter_gfm_task_list(spec, root):
    rel = spec.get("path", "")
    if not rel:
        return False, "no backlog path configured", [], None
    target = (root / rel)
    try:
        os.stat(target)
    except OSError as exc:
        return False, "backlog not readable ({})".format(exc), [], str(target)
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
    try:
        os.stat(target)
    except OSError as exc:
        return False, "record not readable ({})".format(exc), [], str(target)
    return True, "record present", [str(target)], str(target)


def _adapter_transcript_inbox(spec, root):
    rel = spec.get("path", "")
    if not rel:
        return False, "no session surface configured", [], None
    target = root / rel
    try:
        os.stat(target)
    except OSError as exc:
        return False, "session surface not readable ({})".format(exc), [], str(target)
    return True, "session surface present", [str(target)], str(target)


def _adapter_held_source(spec, root):
    rel = spec.get("path", "")
    if not rel:
        return False, "no reference base configured", [], None
    target = root / rel
    try:
        os.stat(target)
    except OSError as exc:
        return False, "reference base not readable ({})".format(exc), [], str(target)
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
        return _surface(name, None, None, None, False, UNVERIFIABLE, KIND_UNKNOWN_ADAPTER,
                        "surface '{}' not declared in config".format(name), [], None)
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

    available, detail, provenance, target = probe(spec, root)
    if not available:
        # DISCRIMINATING GUARD: missing/unreadable evidence is UNVERIFIABLE, never PASS. The kind
        # distinguishes required-unavailable (loud) from optional-unavailable (visible).
        kind = KIND_REQUIRED_UNAVAILABLE if required else KIND_OPTIONAL_UNAVAILABLE
        return _surface(name, adapter, required, enabled, False, UNVERIFIABLE, kind, detail, provenance, target)
    return _surface(name, adapter, required, enabled, True, PASS, KIND_AVAILABLE, detail, provenance, target)


def discover_all(cfg, root):
    """Discover every declared surface, in the config's declared order (a stable dict iteration)."""
    return [discover(name, cfg, root) for name in cfg.get("surfaces", {})]


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
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if failures:
        print("SELF-TEST FAIL:")
        for f in failures:
            print("  - " + f)
        return 1
    print("SELF-TEST PASS: a present required surface is PASS; a MISSING required surface is UNVERIFIABLE "
          "(never PASS); optional-disabled is SKIP (never malformed-required); a disabled required "
          "surface is a distinct malformed-UNVERIFIABLE; an unknown adapter is UNVERIFIABLE; the result "
          "contract rejects an out-of-set status; and config resolution is fail-loud on a pinned config.")
    return 0


def main():
    argv = sys.argv[1:]
    if "--self-test" in argv:
        return _self_test()
    # Default: resolve the config for the current tree and print the surface digest (a read-only view).
    explicit = None
    if "--config" in argv:
        i = argv.index("--config")
        if i + 1 < len(argv):
            explicit = argv[i + 1]
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
