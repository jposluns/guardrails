#!/usr/bin/env python3
"""The orchestrator-guard activation doctor.

The heavy no-manufactured-winddown enforcement already ships (the Stop and TeammateIdle guards, the
adopter enumeration interface, the orchestration registry). Shipping it does not prove it will FIRE:
a missing registry, an unreadable mode record, a broken enumerator argv, or a hook that never made it
into the live configuration each leave the guard shipped-but-inert. This doctor turns that inertness
into an observed fact by exercising the same contracts the guard exercises, on the same repository,
and reporting GREEN or RED per check with a one-line reason.

It runs four checks and exits 0 only when every one passes; any RED, or any check that cannot be
evaluated, exits non-zero (fail closed: ignorance is never reported as health).

  1. The orchestration registry is present and schema-valid (the .aiqt/orchestration.local.json or
     .aiqt/orchestration.json that the guard reads). Read from the LIVE root.
  2. The mode record the registry points at is readable and its Operating-mode value parses, so the
     backlog guard is in scope on this repository. Read from the LIVE root.
  3. The bound AEI enumerator argv actually runs and returns a shape-valid actionable-item set per
     the AEI v1 contract. Run at the LIVE root.
  4. The Stop guard (orch_stop_guard) AND the TeammateIdle guard (orch_teammate_idle) are registered
     in the live hook configuration: a live settings.json hooks block, and/or an INSTALLED plugin's
     hooks.json under a .claude/plugins tree. The pack's own source plugin definition
     (plugin/*/hooks/hooks.json) does NOT count, since it ships in every checkout whether or not the
     plugin was ever installed. Read from the LIVE root.

Checks 1 to 3 import the shipped hook module and call its own functions, so the doctor observes the
exact code path the guard runs rather than a re-implementation of it. Check 4 reads the active hook
configuration directly, because that wiring lives outside the hook module.

Two roots, so a home-and-repo split works. The LIVE root (--root, or the positional argument,
CLAUDE_PROJECT_DIR, the git toplevel, or the current directory) is where the live orchestration
registry, mode record, settings.json, and installed plugins are found; checks 1, 2, and 4 read it,
and check 3 runs the enumerator there. The PACK root (--pack-root) is where the shipped pack module
(.aiqt/core/hooks/scripts/aiqt_hooks.py) lives; the module the doctor imports for checks 1 to 3 comes
from the PACK root, never from the LIVE root. --pack-root defaults to --root, so an adopter whose home
and repo are the same directory is unaffected. Some orchestrators live-root at a home directory that
holds the registry and settings while the pack module sits in a repo subdirectory (for example a live
root of /opt/guardrails with the pack module under /opt/guardrails/guardrails); such a setup passes
--root and --pack-root separately.

  orch_guard_doctor.py [ROOT] [--root ROOT] [--pack-root PACK_ROOT]
                       [--stop-handler NAME] [--idle-handler NAME] [--json]
  orch_guard_doctor.py --self-test [--json]

--stop-handler NAME and --idle-handler NAME override the handler tokens check 4 looks for in the live
hook wiring; both default to the pack handler names (orch_stop_guard and orch_teammate_idle). An adopter
running a compatible non-pack guard (for example a worker-harness stop-guard that reads the registry and
honours AEI v1) passes its own handler names to verify that guard's live wiring. Check 4 verifies the
WIRING of the named handler, not its behavioral contract, which the guard's own self-tests and a live
observation cover.

ROOT is the repository or store root; when omitted it is discovered from CLAUDE_PROJECT_DIR, then
from `git rev-parse --show-toplevel` at the current directory, then the current directory itself.
--self-test builds synthetic fixtures in a temp directory, asserts the doctor's own verdicts against
them (all-GREEN, each isolated RED, and the home-vs-repo split), prints a PASS/FAIL summary, and
returns 0 only when every case holds.
"""
import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile

GREEN = "GREEN"
RED = "RED"

# The handler tokens the guard registers, matched inside a hook entry's argv (or its command string).
STOP_HANDLER = "orch_stop_guard"
IDLE_HANDLER = "orch_teammate_idle"
STOP_EVENT = "Stop"
IDLE_EVENT = "TeammateIdle"

# The shipped hook module, relative to the repository root.
HOOK_MODULE_REL = os.path.join(".aiqt", "core", "hooks", "scripts")
HOOK_MODULE_NAME = "aiqt_hooks"


def discover_root(explicit):
    """Resolve the repository root: an explicit argument, then CLAUDE_PROJECT_DIR, then the git
    toplevel of the current directory, then the current directory."""
    if explicit:
        return os.path.abspath(explicit)
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    if env and os.path.isdir(env):
        return os.path.abspath(env)
    try:
        result = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                                capture_output=True, text=True, timeout=5)
        if result.returncode == 0 and result.stdout.strip():
            return os.path.abspath(result.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        pass
    return os.path.abspath(os.getcwd())


def discover_pack_root(explicit_pack, root):
    """Resolve the pack root (where the shipped hook module lives): an explicit --pack-root argument,
    else the live root. Defaulting to root keeps an adopter whose home and repo are the same directory
    unaffected."""
    if explicit_pack:
        return os.path.abspath(explicit_pack)
    return root


def load_hook_module(pack_root):
    """Import the shipped hook module from under the PACK root: (module, None) or (None, reason).
    Reusing the guard's own functions is the point: the doctor then tests what the guard actually does.
    The module is imported from pack_root, not the live root, so a home-and-repo split resolves the
    module in the repo subdir while the live registry, mode record, and wiring stay at the live root."""
    script_dir = os.path.join(pack_root, HOOK_MODULE_REL)
    script_path = os.path.join(script_dir, HOOK_MODULE_NAME + ".py")
    rel = os.path.join(HOOK_MODULE_REL, HOOK_MODULE_NAME + ".py")
    if not os.path.isfile(script_path):
        reason = "the shipped hook module is not at {} under the pack root {}".format(rel, pack_root)
        hint = _pack_module_hint(pack_root)
        if hint:
            reason += "; " + hint
        return (None, reason)
    sys.path.insert(0, script_dir)
    try:
        import aiqt_hooks  # noqa: E402
        return (aiqt_hooks, None)
    except Exception as exc:  # a broken or partial module is a cannot-evaluate, not a pass
        return (None, "the hook module under the pack root {} failed to import ({})".format(
            pack_root, exc))


def _pack_module_hint(pack_root):
    """A one-line hint, or "", when the module is absent at pack_root but present one level down under
    an immediate subdirectory (the guardrails home-vs-repo shape). This never auto-guesses the pack
    root: it only names a candidate the operator can pass explicitly via --pack-root."""
    try:
        entries = sorted(os.listdir(pack_root))
    except OSError:
        return ""
    for name in entries:
        sub = os.path.join(pack_root, name)
        if os.path.isfile(os.path.join(sub, HOOK_MODULE_REL, HOOK_MODULE_NAME + ".py")):
            return "a pack module was found under {}; pass --pack-root {}".format(sub, sub)
    return ""


def check_registry(hooks, root):
    """Check 1: the orchestration registry is present and schema-valid, via the guard's own loader."""
    status, reg = hooks._orch_registry(root)
    if status == "ok":
        which = ".aiqt/orchestration.local.json or .aiqt/orchestration.json"
        return (GREEN, "registry present and schema-valid ({})".format(which), reg)
    if status == "absent":
        return (RED, "no orchestration registry at the repo root; the suite is inert here, so the "
                     "guard cannot fire", None)
    return (RED, "registry present but not schema-valid: {}".format(reg), None)


def check_mode(hooks, root, reg):
    """Check 2: the mode record is readable and its Operating-mode value parses, so the backlog guard
    is in scope. A declared mode record that carries no readable Operating-mode line is RED. Where no
    mode record is declared, scope must instead come from a live lease; that is confirmed via the
    guard's own scope predicate, and its absence is RED."""
    if reg is None:
        return (RED, "no readable registry, so the mode record cannot be resolved")
    mode_decl = reg.get("mode") if isinstance(reg.get("mode"), dict) else None
    mode_value = hooks._orch_mode(reg, root)
    if mode_decl and mode_decl.get("path"):
        if mode_value:
            return (GREEN, "mode record readable; Operating-mode parses to {!r}".format(mode_value))
        return (RED, "declared mode record is unreadable or carries no Operating-mode line")
    # No mode record declared: the guard engages only if scope is live another way (a live lease).
    if hooks._orch_scope_live(reg, root):
        return (GREEN, "no mode record declared, but scope is live via the declared lease")
    return (RED, "no mode record declared and no live lease; the backlog guard is out of scope, so "
                 "it will not engage on this repository")


def check_enumerator(hooks, root, reg):
    """Check 3: the bound AEI enumerator argv runs and returns a shape-valid item set, via the guard's
    own runner (which invokes the argv and validates the response per AEI v1)."""
    if reg is None:
        return (RED, "no readable registry, so no enumerator is bound")
    if not reg.get("enumerator"):
        return (RED, "the registry declares no enumerator; the stop guard has no actionable-item "
                     "source to read")
    status, payload = hooks._orch_enumerate(reg, root)
    if status == "ok":
        return (GREEN, "enumerator ran and returned a valid AEI v1 set ({} item(s))".format(
            len(payload)))
    return (RED, "enumerator did not satisfy the AEI v1 contract: {}: {}".format(status, payload))


def _config_candidates(root):
    """Candidate live-configuration files: settings.json blocks and INSTALLED plugin hooks.json files
    (a .claude/plugins tree under root, $CLAUDE_PROJECT_DIR, or $HOME). The pack's own source plugin
    definition (root/plugin/*/hooks/hooks.json) is deliberately excluded: it ships in every checkout
    and describes the plugin, it does not register it in the live session. Deduplicated by real path;
    only existing files are returned."""
    home = os.path.expanduser("~")
    proj = os.environ.get("CLAUDE_PROJECT_DIR")
    literal = []
    for base in (root, proj, home):
        if not base:
            continue
        literal.append(os.path.join(base, ".claude", "settings.json"))
        literal.append(os.path.join(base, ".claude", "settings.local.json"))
    literal.append(os.path.join(home, ".claude", "settings.json"))
    # glob.escape the literal base directory so a bracket in an adopter path (e.g. project[1]) is
    # matched literally, not read as a character class; the "**" wildcard is joined unescaped.
    patterns = [
        os.path.join(glob.escape(root), ".claude", "plugins", "**", "hooks", "hooks.json"),
        os.path.join(glob.escape(proj), ".claude", "plugins", "**", "hooks", "hooks.json")
        if proj else None,
        os.path.join(glob.escape(home), ".claude", "plugins", "**", "hooks", "hooks.json"),
    ]
    patterns = [pat for pat in patterns if pat]
    found = list(literal)
    for pat in patterns:
        found.extend(glob.glob(pat, recursive=True))
    seen, out = set(), []
    for path in found:
        if not os.path.isfile(path):
            continue
        real = os.path.realpath(path)
        if real in seen:
            continue
        seen.add(real)
        out.append(path)
    return out


def _handler_registered(cfg, event, handler):
    """True when handler appears in cfg's hooks[event] block (in an entry's argv, or its command
    string as a fallback). Tolerant of both the settings.json and plugin hooks.json shapes, since
    both nest the event array under a top-level 'hooks' object."""
    hooks_obj = cfg.get("hooks") if isinstance(cfg, dict) else None
    if not isinstance(hooks_obj, dict):
        return False
    entries = hooks_obj.get(event)
    if not isinstance(entries, list):
        return False
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        hks = entry.get("hooks")
        if not isinstance(hks, list):
            continue
        for hk in hks:
            if not isinstance(hk, dict):
                continue
            args = hk.get("args")
            if isinstance(args, list) and any(
                    isinstance(a, str) and a == handler for a in args):
                return True
            cmd = hk.get("command")
            if isinstance(cmd, str) and handler in cmd:
                return True
    return False


def check_hooks_registered(root, stop_handler=STOP_HANDLER, idle_handler=IDLE_HANDLER):
    """Check 4: the Stop and TeammateIdle guards are registered in the live hook configuration. Fail
    closed: if no candidate configuration file can be read at all, that is a cannot-evaluate (RED),
    never a silent pass. stop_handler/idle_handler default to the pack handler names but can be
    overridden (via --stop-handler / --idle-handler) so an adopter running a compatible non-pack guard
    can verify ITS handler's live wiring. This check verifies WIRING of the named handler, not the
    handler's behavioral contract (which the guard's own self-tests and a live observation cover)."""
    candidates = _config_candidates(root)
    if not candidates:
        return (RED, "no live hook configuration found (no settings.json block and no installed "
                     "plugin hooks.json); cannot confirm the guard is wired")
    stop_src = idle_src = None
    unreadable = []
    read_any = False
    for path in candidates:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                cfg = json.load(fh)
        except (OSError, ValueError) as exc:
            unreadable.append("{} ({})".format(path, exc))
            continue
        read_any = True
        if stop_src is None and _handler_registered(cfg, STOP_EVENT, stop_handler):
            stop_src = path
        if idle_src is None and _handler_registered(cfg, IDLE_EVENT, idle_handler):
            idle_src = path
    if not read_any:
        return (RED, "every candidate hook configuration was unreadable ({}); cannot confirm the "
                     "guard is wired".format("; ".join(unreadable)))
    if stop_src and idle_src:
        return (GREEN, "Stop guard registered in {} and TeammateIdle guard in {}".format(
            _short(root, stop_src), _short(root, idle_src)))
    missing = []
    if not stop_src:
        missing.append("Stop -> " + stop_handler)
    if not idle_src:
        missing.append("TeammateIdle -> " + idle_handler)
    return (RED, "not wired in any live settings.json hook block or installed plugin hooks.json "
                 "(the pack's own source plugin definition does not count): {}".format(
                     ", ".join(missing)))


def _short(root, path):
    """A repo-relative path where possible, for readable output."""
    try:
        rel = os.path.relpath(path, root)
    except ValueError:
        return path
    return rel if not rel.startswith("..") else path


def main(argv):
    explicit = None
    explicit_pack = None
    stop_handler = STOP_HANDLER
    idle_handler = IDLE_HANDLER
    want_json = False
    self_test = False
    rest = []
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--json":
            want_json = True
        elif arg == "--self-test":
            self_test = True
        elif arg == "--root":
            i += 1
            explicit = argv[i] if i < len(argv) else None
        elif arg == "--pack-root":
            i += 1
            explicit_pack = argv[i] if i < len(argv) else None
        elif arg == "--stop-handler":
            i += 1
            if i < len(argv):
                stop_handler = argv[i]
        elif arg == "--idle-handler":
            i += 1
            if i < len(argv):
                idle_handler = argv[i]
        elif arg in ("-h", "--help"):
            print(__doc__)
            return 0
        else:
            rest.append(arg)
        i += 1
    if rest and explicit is None:
        explicit = rest[0]

    if self_test:
        return run_self_test(want_json)

    root = discover_root(explicit)
    pack_root = discover_pack_root(explicit_pack, root)
    results = []  # (name, verdict, reason)

    hooks, reason = load_hook_module(pack_root)
    reg = None
    if hooks is None:
        # Checks 1 to 3 all depend on the hook module imported from the pack root; report each as
        # cannot-evaluate, naming the pack root the module was sought under.
        for name in ("registry", "mode", "enumerator"):
            results.append((name, RED, "hook module unavailable: {}".format(reason)))
    else:
        verdict, why, reg = check_registry(hooks, root)
        results.append(("registry", verdict, why))
        verdict, why = check_mode(hooks, root, reg)
        results.append(("mode", verdict, why))
        verdict, why = check_enumerator(hooks, root, reg)
        results.append(("enumerator", verdict, why))

    verdict, why = check_hooks_registered(root, stop_handler, idle_handler)
    results.append(("hooks_registered", verdict, why))

    passed = sum(1 for _, v, _ in results if v == GREEN)
    failed = len(results) - passed
    overall = "PASS" if failed == 0 else "FAIL"

    if want_json:
        print(json.dumps({"result": overall, "root": root, "pack_root": pack_root,
                          "pass": passed, "fail": failed,
                          "checks": [{"name": n, "verdict": v, "reason": r}
                                     for n, v, r in results]}, sort_keys=True))
    else:
        print("Orchestrator-guard activation doctor")
        print("Live root: {}".format(root))
        if pack_root != root:
            print("Pack root: {}".format(pack_root))
        print("")
        for name, verdict, why in results:
            print("[{}] {}: {}".format(verdict, name, why))
        print("")
        # The machine-readable summary line (stable prefix, key=value fields).
        fields = " ".join("{}={}".format(n, v.lower()) for n, v, _ in results)
        print("ORCH_GUARD_DOCTOR result={} pass={} fail={} {}".format(
            overall, passed, failed, fields))

    return 0 if failed == 0 else 1


# --------------------------------------------------------------------------------------------------
# Self-test: build synthetic fixtures and assert the doctor's own verdicts against them.
# --------------------------------------------------------------------------------------------------

# A working AEI v1 enumerator: emits an empty-but-valid actionable-item set with a fresh timestamp.
_ENUM_OK = (
    "import json, datetime\n"
    "now = datetime.datetime.now(datetime.timezone.utc).isoformat()\n"
    "print(json.dumps({'version': 1, 'generated_at_utc': now,\n"
    "                  'source': {'locator': 'orch-guard-doctor self-test'}, 'items': []}))\n"
)
# An enumerator that exits non-zero (the ENUMERATOR_ERROR path).
_ENUM_EXIT = "import sys\nsys.stderr.write('synthetic enumerator failure')\nsys.exit(3)\n"
# An enumerator whose output is not a valid AEI v1 envelope (version missing).
_ENUM_INVALID = "print('{\"not\": \"an aei envelope\"}')\n"

# A settings.json that wires BOTH guards; and one that wires NEITHER.
_SETTINGS_WIRED = {"hooks": {
    "Stop": [{"hooks": [{"type": "command",
                         "command": "python3 .aiqt/core/hooks/scripts/aiqt_hooks.py orch_stop_guard"}]}],
    "TeammateIdle": [{"hooks": [{"type": "command",
                                 "command": "python3 .aiqt/core/hooks/scripts/aiqt_hooks.py "
                                            "orch_teammate_idle"}]}],
}}
_SETTINGS_UNWIRED = {"hooks": {"PreToolUse": [{"hooks": [{"type": "command",
                                                          "command": "echo unrelated"}]}]}}
# A settings.json wiring a CUSTOM (non-pack) handler on each event, for the --stop-handler /
# --idle-handler override case. It wires NEITHER pack handler, so it reads check-4 RED by default.
_SETTINGS_CUSTOM = {"hooks": {
    "Stop": [{"hooks": [{"type": "command",
                         "command": "python3 my_guard.py my_stop_guard"}]}],
    "TeammateIdle": [{"hooks": [{"type": "command",
                                 "command": "python3 my_guard.py my_idle_guard"}]}],
}}


def _st_write(path, text):
    """Write text to path, creating parent directories."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def _st_locate_real_module():
    """The path to the real shipped aiqt_hooks module relative to this doctor (repo/.aiqt/core/hooks/
    scripts/aiqt_hooks.py, the doctor living in repo/tools/), or None when it cannot be found."""
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cand = os.path.join(repo, HOOK_MODULE_REL, HOOK_MODULE_NAME + ".py")
    return cand if os.path.isfile(cand) else None


def _st_stub_module_text():
    """A minimal stub exposing the loader names the doctor calls, faithful enough for the self-test
    (used only when the real module cannot be located). It mirrors the real contracts: a version-1
    registry, an Operating-mode line, a live-lease-or-mode scope predicate, and an AEI v1 enumerator."""
    return (
        "import json, os, re, subprocess, datetime\n"
        "_FILES = ('.aiqt/orchestration.local.json', '.aiqt/orchestration.json')\n"
        "_MODE_RE = re.compile(r'^Operating-mode:\\s*(.+?)\\s*$', re.MULTILINE)\n"
        "_STATES = frozenset(('open', 'closed', 'proposed'))\n"
        "_KINDS = frozenset(('tracked-task', 'human-decision', 'external', 'foreign-lease'))\n"
        "def _now():\n"
        "    return datetime.datetime.now(datetime.timezone.utc)\n"
        "def _parse(t):\n"
        "    if not isinstance(t, str) or not t:\n"
        "        return None\n"
        "    try:\n"
        "        v = datetime.datetime.fromisoformat(t.replace('Z', '+00:00'))\n"
        "    except ValueError:\n"
        "        return None\n"
        "    return v if v.tzinfo else v.replace(tzinfo=datetime.timezone.utc)\n"
        "def _path(root, value):\n"
        "    if not isinstance(value, str) or not value:\n"
        "        return None\n"
        "    return value if os.path.isabs(value) else os.path.join(root, value)\n"
        "def _orch_registry(root):\n"
        "    for rel in _FILES:\n"
        "        p = os.path.join(root, *rel.split('/'))\n"
        "        try:\n"
        "            os.lstat(p)\n"
        "        except FileNotFoundError:\n"
        "            continue\n"
        "        except OSError as e:\n"
        "            return ('bad', '{}: cannot stat ({})'.format(rel, e))\n"
        "        try:\n"
        "            with open(p, encoding='utf-8') as fh:\n"
        "                data = json.load(fh)\n"
        "        except (OSError, ValueError) as e:\n"
        "            return ('bad', '{}: {}'.format(rel, e))\n"
        "        if not isinstance(data, dict) or type(data.get('version')) is not int "
        "or data.get('version') != 1:\n"
        "            return ('bad', '{}: not a version-1 registry object'.format(rel))\n"
        "        return ('ok', data)\n"
        "    return ('absent', None)\n"
        "def _orch_mode(reg, root):\n"
        "    m = reg.get('mode') if isinstance(reg.get('mode'), dict) else None\n"
        "    p = _path(root, m.get('path')) if m else None\n"
        "    if not p:\n"
        "        return None\n"
        "    try:\n"
        "        with open(p, encoding='utf-8', errors='replace') as fh:\n"
        "            text = fh.read()\n"
        "    except OSError:\n"
        "        return None\n"
        "    mm = _MODE_RE.search(text)\n"
        "    return mm.group(1).lower() if mm else None\n"
        "def _orch_scope_live(reg, root, session_id=None):\n"
        "    return _orch_mode(reg, root) is not None\n"
        "def validate_enumeration(obj):\n"
        "    if not isinstance(obj, dict):\n"
        "        return (None, 'payload is not a JSON object')\n"
        "    if type(obj.get('version')) is not int or obj.get('version') != 1:\n"
        "        return (None, 'unknown AEI version')\n"
        "    g = _parse(obj.get('generated_at_utc'))\n"
        "    if g is None:\n"
        "        return (None, 'missing or unparseable generated_at_utc')\n"
        "    s = obj.get('source')\n"
        "    if not isinstance(s, dict) or not isinstance(s.get('locator'), str) "
        "or not s.get('locator').strip():\n"
        "        return (None, 'missing or malformed source')\n"
        "    items = obj.get('items')\n"
        "    if not isinstance(items, list):\n"
        "        return (None, 'items is not a list')\n"
        "    seen, out = set(), []\n"
        "    for raw in items:\n"
        "        if not isinstance(raw, dict):\n"
        "            return (None, 'an item is not an object')\n"
        "        iid = raw.get('id')\n"
        "        if not isinstance(iid, str) or not iid or iid in seen:\n"
        "            return (None, 'bad item id')\n"
        "        seen.add(iid)\n"
        "        if raw.get('state') not in _STATES:\n"
        "            return (None, 'invalid state')\n"
        "        if not isinstance(raw.get('granted'), bool):\n"
        "            return (None, 'non-boolean granted')\n"
        "        out.append({'id': iid, 'title': raw.get('title') or iid,\n"
        "                    'state': raw.get('state'), 'granted': raw.get('granted'),\n"
        "                    'blocker': raw.get('blocker')})\n"
        "    return (out, None)\n"
        "def _orch_enumerate(reg, root):\n"
        "    spec = reg.get('enumerator')\n"
        "    if not isinstance(spec, dict):\n"
        "        return ('NO_ENUMERATOR', 'the registry declares no enumerator')\n"
        "    argv = spec.get('argv')\n"
        "    if not isinstance(argv, list) or not argv or not all("
        "isinstance(a, str) and a for a in argv):\n"
        "        return ('ENUMERATOR_ERROR', 'enumerator argv is not a list of non-empty strings')\n"
        "    try:\n"
        "        r = subprocess.run(argv, capture_output=True, text=True, timeout=60, cwd=root)\n"
        "    except (OSError, subprocess.SubprocessError) as e:\n"
        "        return ('ENUMERATOR_ERROR', 'enumerator failed to run: {}'.format(e))\n"
        "    if r.returncode != 0:\n"
        "        return ('ENUMERATOR_ERROR', 'enumerator exit {}: {}'.format(r.returncode, "
        "(r.stderr or '')[:200]))\n"
        "    try:\n"
        "        payload = json.loads(r.stdout)\n"
        "    except ValueError as e:\n"
        "        return ('ENUMERATOR_ERROR', 'enumerator output is not JSON: {}'.format(e))\n"
        "    items, err = validate_enumeration(payload)\n"
        "    if err:\n"
        "        return ('ENUMERATOR_ERROR', err)\n"
        "    return ('ok', items)\n"
    )


def _st_place_module(pack_root, real_module):
    """Place the hook module under pack_root/.aiqt/core/hooks/scripts/. Copies the real module when
    real_module is a path, else writes the stub."""
    dest = os.path.join(pack_root, HOOK_MODULE_REL, HOOK_MODULE_NAME + ".py")
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    if real_module:
        shutil.copyfile(real_module, dest)
    else:
        _st_write(dest, _st_stub_module_text())


def _st_registry(root, mode_path=None, enum_script=None):
    """Write a version-1 registry at root, optionally declaring a mode record path and an enumerator
    argv that runs the given script with the current interpreter."""
    reg = {"version": 1}
    if mode_path is not None:
        reg["mode"] = {"path": mode_path}
    if enum_script is not None:
        reg["enumerator"] = {"argv": [sys.executable, enum_script]}
    _st_write(os.path.join(root, ".aiqt", "orchestration.local.json"), json.dumps(reg))


def _st_enum(root, name, body):
    """Write an enumerator script under root and return its absolute path."""
    path = os.path.join(root, "tools", name)
    _st_write(path, body)
    return path


def _st_run(doctor, root, pack_root, env, extra_args=None):
    """Run the doctor as an isolated subprocess against root/pack_root with --json; return the parsed
    result dict. Reading the verdict from the doctor's own JSON output is the observation the self-test
    rests on, not a re-implementation of the checks. extra_args, when given, are appended to the argv
    so a case can exercise options such as --stop-handler / --idle-handler."""
    cmd = [sys.executable, "-I", "-B", doctor, "--json", "--root", root]
    if pack_root is not None:
        cmd += ["--pack-root", pack_root]
    if extra_args:
        cmd += list(extra_args)
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=120)
    try:
        return json.loads(proc.stdout)
    except ValueError:
        return {"result": "PARSE-ERROR", "stdout": proc.stdout, "stderr": proc.stderr,
                "checks": []}


def _st_verdict(data, name):
    """The verdict of a named check from a doctor result dict, or None when absent."""
    for c in data.get("checks", []):
        if c.get("name") == name:
            return c.get("verdict")
    return None


def run_self_test(want_json):
    """Build synthetic fixtures and assert the doctor's verdicts. Returns 0 only when every case holds.
    Each case runs the doctor as an isolated subprocess under a hermetic environment (a private HOME and
    XDG_STATE_HOME, with CLAUDE_PROJECT_DIR removed) so the host's own live wiring can never leak into a
    candidate-configuration read and turn a RED case GREEN."""
    doctor = os.path.abspath(__file__)
    real_module = _st_locate_real_module()
    module_kind = "real aiqt_hooks module" if real_module else "minimal stub module"
    tmp = tempfile.mkdtemp(prefix="orch-guard-doctor-selftest-")
    cases = []  # (name, ok, detail)

    def record(name, ok, detail):
        cases.append((name, ok, detail))

    try:
        home = os.path.join(tmp, "home")
        os.makedirs(os.path.join(home, ".claude"), exist_ok=True)  # a real but wiring-free HOME
        env = dict(os.environ)
        env["HOME"] = home
        env["XDG_STATE_HOME"] = os.path.join(tmp, "xdg")
        env.pop("CLAUDE_PROJECT_DIR", None)

        # A shared valid pack root (module present) and an empty pack root (module absent).
        pack_ok = os.path.join(tmp, "pack-ok")
        _st_place_module(pack_ok, real_module)
        pack_empty = os.path.join(tmp, "pack-empty")
        os.makedirs(pack_empty, exist_ok=True)

        def live_root(name, wired=True, registry=True, malformed_registry=False,
                      mode="good", enum="ok", with_module=True, source_plugin=False):
            """Build a live root fixture and return its path. mode: good|empty|none; enum: ok|exit|
            invalid|none."""
            root = os.path.join(tmp, name)
            os.makedirs(root, exist_ok=True)
            if with_module:
                _st_place_module(root, real_module)
            # mode record
            mode_path = None
            if mode == "good":
                mp = os.path.join(root, "state", "mode.md")
                _st_write(mp, "Operating-mode: overnight\n")
                mode_path = mp
            elif mode == "empty":
                mp = os.path.join(root, "state", "mode.md")
                _st_write(mp, "")  # exists but carries no Operating-mode line
                mode_path = mp
            # enumerator
            enum_script = None
            if enum == "ok":
                enum_script = _st_enum(root, "enum_ok.py", _ENUM_OK)
            elif enum == "exit":
                enum_script = _st_enum(root, "enum_exit.py", _ENUM_EXIT)
            elif enum == "invalid":
                enum_script = _st_enum(root, "enum_invalid.py", _ENUM_INVALID)
            if malformed_registry:
                _st_write(os.path.join(root, ".aiqt", "orchestration.local.json"),
                          json.dumps({"version": 2}))  # present but not version-1
            elif registry:
                _st_registry(root, mode_path=mode_path, enum_script=enum_script)
            if wired:
                _st_write(os.path.join(root, ".claude", "settings.json"),
                          json.dumps(_SETTINGS_WIRED))
            else:
                _st_write(os.path.join(root, ".claude", "settings.json"),
                          json.dumps(_SETTINGS_UNWIRED))
            if source_plugin:
                # The pack's own SOURCE plugin definition: present in every checkout, must NOT count.
                _st_write(os.path.join(root, "plugin", "aiqt", "hooks", "hooks.json"),
                          json.dumps(_SETTINGS_WIRED))
            return root

        # 1. All GREEN (home==repo; module at the live root, no --pack-root).
        r = _st_run(doctor, live_root("green"), None, env)
        record("all-GREEN -> PASS", r.get("result") == "PASS" and r.get("fail") == 0,
               "result={} checks={}".format(r.get("result"), r.get("checks")))

        # 2. Missing registry -> registry RED (mode+enumerator cannot resolve either).
        r = _st_run(doctor, live_root("no-registry", registry=False), None, env)
        record("missing registry -> registry RED",
               r.get("result") == "FAIL" and _st_verdict(r, "registry") == RED,
               "registry={} result={}".format(_st_verdict(r, "registry"), r.get("result")))

        # 3. Malformed (non-version-1) registry -> registry RED.
        r = _st_run(doctor, live_root("bad-registry", malformed_registry=True), None, env)
        record("malformed registry -> registry RED",
               r.get("result") == "FAIL" and _st_verdict(r, "registry") == RED,
               "registry={} result={}".format(_st_verdict(r, "registry"), r.get("result")))

        # 4. Unreadable/empty mode record -> mode RED, registry+enumerator GREEN.
        r = _st_run(doctor, live_root("empty-mode", mode="empty"), None, env)
        record("empty mode record -> mode RED",
               _st_verdict(r, "registry") == GREEN and _st_verdict(r, "mode") == RED
               and _st_verdict(r, "enumerator") == GREEN,
               "registry={} mode={} enumerator={}".format(
                   _st_verdict(r, "registry"), _st_verdict(r, "mode"),
                   _st_verdict(r, "enumerator")))

        # 5. Enumerator exits non-zero -> enumerator RED.
        r = _st_run(doctor, live_root("enum-exit", enum="exit"), None, env)
        record("enumerator non-zero exit -> enumerator RED",
               _st_verdict(r, "registry") == GREEN and _st_verdict(r, "enumerator") == RED,
               "enumerator={}".format(_st_verdict(r, "enumerator")))

        # 6. Enumerator emits invalid AEI -> enumerator RED.
        r = _st_run(doctor, live_root("enum-invalid", enum="invalid"), None, env)
        record("enumerator invalid AEI -> enumerator RED",
               _st_verdict(r, "registry") == GREEN and _st_verdict(r, "enumerator") == RED,
               "enumerator={}".format(_st_verdict(r, "enumerator")))

        # 7. settings.json missing the Stop/idle wiring -> hooks_registered RED.
        r = _st_run(doctor, live_root("no-wiring", wired=False), None, env)
        record("settings without wiring -> hooks_registered RED",
               _st_verdict(r, "hooks_registered") == RED,
               "hooks_registered={}".format(_st_verdict(r, "hooks_registered")))

        # 8. Source plugin dir present but NO live wiring -> hooks_registered RED (guards the earlier
        #    false-GREEN: the pack's own plugin/*/hooks/hooks.json must not count as live wiring).
        r = _st_run(doctor, live_root("source-plugin-only", wired=False, source_plugin=True),
                    None, env)
        record("source plugin only (no live wiring) -> hooks_registered RED",
               _st_verdict(r, "hooks_registered") == RED,
               "hooks_registered={}".format(_st_verdict(r, "hooks_registered")))

        # 9. Home-vs-repo split GREEN: live root holds registry/mode/enum/settings (NO module); the
        #    pack module lives at a separate pack root.
        split_live = live_root("split-live", with_module=False)
        r = _st_run(doctor, split_live, pack_ok, env)
        record("home-vs-repo split -> PASS",
               r.get("result") == "PASS" and r.get("fail") == 0,
               "result={} checks={}".format(r.get("result"), r.get("checks")))

        # 10. Same split live root, but --pack-root points at a dir WITHOUT the module -> checks 1-3
        #     fail closed (cannot-evaluate RED), proving the split imports from the pack root.
        r = _st_run(doctor, split_live, pack_empty, env)
        record("split with module-less pack root -> checks 1-3 RED",
               _st_verdict(r, "registry") == RED and _st_verdict(r, "mode") == RED
               and _st_verdict(r, "enumerator") == RED,
               "registry={} mode={} enumerator={}".format(
                   _st_verdict(r, "registry"), _st_verdict(r, "mode"),
                   _st_verdict(r, "enumerator")))

        # 11. Custom handler override: a settings.json wiring CUSTOM (non-pack) handler names. WITH the
        #     override check-4 is GREEN; WITHOUT it (default pack names) the same config reads RED,
        #     proving --stop-handler / --idle-handler retarget the wiring check.
        custom_live = live_root("custom-wired", wired=True)
        _st_write(os.path.join(custom_live, ".claude", "settings.json"),
                  json.dumps(_SETTINGS_CUSTOM))
        r = _st_run(doctor, custom_live, None, env,
                    extra_args=["--stop-handler", "my_stop_guard",
                                "--idle-handler", "my_idle_guard"])
        override_green = _st_verdict(r, "hooks_registered") == GREEN
        r2 = _st_run(doctor, custom_live, None, env)
        default_red = _st_verdict(r2, "hooks_registered") == RED
        record("custom handler override -> check-4 GREEN with override, RED without",
               override_green and default_red,
               "override={} default={}".format(
                   _st_verdict(r, "hooks_registered"), _st_verdict(r2, "hooks_registered")))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    passed = sum(1 for _, ok, _ in cases if ok)
    failed = len(cases) - passed
    overall = "PASS" if failed == 0 else "FAIL"

    if want_json:
        print(json.dumps({"self_test": overall, "module": module_kind, "pass": passed,
                          "fail": failed, "cases": [{"name": n, "ok": ok, "detail": d}
                                                    for n, ok, d in cases]}, sort_keys=True))
    else:
        print("Orchestrator-guard activation doctor: self-test")
        print("Module under test: {}".format(module_kind))
        print("")
        for name, ok, detail in cases:
            print("[{}] {}".format("PASS" if ok else "FAIL", name))
            if not ok:
                print("      {}".format(detail))
        print("")
        print("ORCH_GUARD_DOCTOR_SELFTEST result={} pass={} fail={} module={}".format(
            overall, passed, failed, module_kind.split()[0]))

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
