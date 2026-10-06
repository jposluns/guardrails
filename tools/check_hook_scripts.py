#!/usr/bin/env python3
"""Hook-scripts gate: the pack's standalone hook scripts stay warn-only, environment-inert,
unduplicated, and in parity with the preview Stop hook. Offline, stdlib only, no git, fail-closed.

A standalone hook script is a [[hook]] row with a `script` key in .aiqt/core/hooks/manifest.toml. Its
source lives in .aiqt/core/hooks/scripts/ and tools/gen_hooks.py copies it byte-identical into
plugin/aiqt-guardrails-hooks/hooks/scripts/ (byte identity is that generator's --check, not this gate).
Every script the manifest names must have a fixture set in build_specs below, and every fixture set must
be in the manifest; a mismatch either way is a cannot-evaluate (exit 2), so a new script forces a
reviewed edit here. Six legs:

  (a) SELF-TEST. Each source script runs as [sys.executable, "-I", "-S", "-B", <path>, "--self-test"]
      under the run contract below. A nonzero exit is a finding.
  (w) WARN ONLY, through the rendered hooks.json. The hooks.json entries that name a script (in any arg
      or the command, in any spelling of its path: see NAMING below) must be, counted with their
      multiplicity, exactly one per manifest row of that
      script: under its event and matcher, the whole entry {type "command", command "python3", args
      ["-I", "-S", "-B", "-c", <gen_hooks.SCRIPT_LAUNCHER>, "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/<file>"],
      timeout <the row's, default gen_hooks.TIMEOUT>}, so an extra or duplicated entry is a finding. The
      plugin copy is run through those args (under sys.executable) on each fixture. Every run must exit 0, and
      stdout must be empty or one JSON object: for future-stamp-write.py only `systemMessage`, one line
      of at most 100 characters; for clock-inject.py only `hookSpecificOutput` with exactly hookEventName and
      additionalContext. A `decision`, `permissionDecision`, `continue`, or `stopReason` key anywhere is
      a finding, as is a fixture whose class (silent, warn, context) differs from the one expected.
      Fixtures are relative to the real clock (future literals at now plus two days).
  (k) ENVIRONMENT INERTNESS. Each inert fixture is rerun with a worker marker set (AIQT_HOOKS_WORKER=1,
      ORCH_WORKER=1, ORCH_VERIFY_OWNER empty and set, and all of them together). These are names the
      preview hooks honoured; this leg names them on purpose, to prove the shipped scripts ignore them.
      The legacy configuration spellings ORCH_STORE_ROOT and ORCH_LEASE_FILE are fixtures of their own
      (expected to act as unset). The reduced outcome (exit code, sorted key set, class, whether stderr
      is empty, and the output with every digit run masked) must equal the unmodified run's. Two
      fixtures record a disclosed dependency instead of inertness: CDPATH on a relative cd (expected
      silent) and TZ on an unzoned literal (its class is printed, not asserted). Each script must have
      an inert fixture whose class is not silent, so inertness is tested on real output.
  (l) LAUNCH FAILURE. Every hooks.json entry that names a script, accepted by (w) or not, is run (its
      command must be python3, run as sys.executable) with the script file replaced, in a scratch plugin
      root, by: no file, an unreadable file (mode 0; one that exits 2 if read, so a privileged user who
      can still read it gets the exit-2 case, printed as such), a directory, a syntax error, a module that
      raises, and a module that exits 2. Each of these launches must exit exactly 1 (never 2, which
      blocks a PreToolUse call, and never 0, which would hide the failure), print nothing on stdout, and
      say why on stderr. A module that exits 0 when run as __main__ with sys.argv equal to [its path]
      must give exit 0, both as rendered and with an extra argument appended after the path, so the
      launcher is shown to run the file the way a direct launch would and to take the file from its own
      fixed position.
  (d) DUPLICATES. A file name present both in .preview/ and in the manifest's script set is a finding:
      a reader could install both copies and run the check twice.
  (p) REPOSITORY PARITY. Code the pack copies share with .preview/stamp-truth-stop.py, and the `_cfg`
      the two pack copies share with each other, must be identical: `inspect.getsource` for functions,
      (pattern, flags) for compiled regexes, equality for constants. Each pair is compared in one child
      [sys.executable, "-I", "-S", "-B", <this file>, "--parity-child", ...] that loads both files with
      importlib. The child writes its structured result at interpreter shutdown, AFTER the loaded
      files' own cleanup (non-daemon threads joined, their atexit hooks run, pending cyclic-garbage
      finalizers collected REPEATEDLY until a pass CHANGES NOTHING: frees no object and leaves
      the identity set of tracked objects unchanged, within _child_contract.GC_PASS_BOUND
      passes, past which the child refuses by name -- merge train 2 QA r4, codex MEDIUM: a
      collected finalizer may create NEW cyclic cleanup work, which one collection left for the
      ending os._exit to skip; QA r5, codex MEDIUM: a finalizer that resurrects its object
      while creating that work makes a pass free nothing; QA r6, codex MEDIUM: one that also
      RELEASES other tracked objects kept the tracked count equal or lower, so the count rules
      settled over its pending work), and then ends ITSELF with os._exit, so no interpreter
      finalization runs after the result (merge train 2 QA r3; the child half of this discipline
      is the shared fail-closed child contract, opf/tools/_child_contract.py, implemented once
      for this child and the OPF unit child); a fault the loaded code reports into a replaced
      or silenced reporting machinery is seen by an audit hook and makes the child exit 2, and
      reporting machinery left replaced is put back and refused the same way, also when the
      fault, the replacement or a thread start happens inside the result write itself (the
      contract re-checks after its record callback returns; merge train 2 QA r8, codex MAJOR:
      a stdout wrapper's flush that raised an unraisable fault under a hook it put back left
      the exit 0), and the re-check's own flushes run BEFORE its checks, the terminator line
      written after them by os.write on a saved descriptor (merge train 2 QA r9, codex MAJOR: a
      wrapper's second flush, in the re-check, replaced a hook or dropped faulting cyclic
      garbage after the checks covering them, and the exit stayed 0). The parent accepts
      a verdict only with the zero exit, the complete result ending in its terminator line, and
      NOTHING on the child's error stream: the loaded files write nothing to stderr at load time,
      so any bytes there -- an uncaught-exception traceback, a frameless `Exception ignored` line
      from a builtin cleanup hook, a shutdown finalizer's write -- are a cleanup or thread fault
      (the fail-closed child-process rule,
      secfcl; merge train 2 QA rounds 1 and 2): loaded code that faults in a thread or cleanup hook,
      or ends the child from its cleanup, is a cannot-evaluate (exit 2), never a pass.
      `_is_worker` and `_sibling_or_skip` must be absent from both pack copies. `_cfg` is
      excluded from the pairs with the Stop hook, because the preview Stop hook still reads the legacy
      spelling. A missing or unloadable pair file is a cannot-evaluate (exit 2), never a skip, so
      promoting or retiring the Stop hook forces a deliberate edit to PAIRS.

NAMING, for legs (w) and (l): an entry names a script when a word of its command or of any arg (split at
whitespace, quotes, `=`, `;`, `|`, `&`, `<`, `>` and parentheses) either, with ${CLAUDE_PLUGIN_ROOT} or
$CLAUDE_PLUGIN_ROOT replaced by the plugin root, resolves to the plugin copy of the script (symlinks
followed, or the same file by device and inode), or has the script's name as its last path component
once ./, // and .. are normalised. So `scripts/./<file>`, `scripts//<file>`, `scripts/../scripts/<file>`,
a symlinked plugin root, a symlink to the file under another name and a relative path all name it.

RUN CONTRACT for legs (a), (w), (k) and (l): a fresh temporary working directory per run, stdin closed or
fed the fixture, every AIQT_, ORCH_ and CLAUDE_ variable and CDPATH removed, TZ=UTC, and per-run
temporary store and lease paths where a fixture needs them. A run that outlives its deadline is a
cannot-evaluate (exit 2).

DISCLOSED RESIDUALS (what this gate does not catch):
  - The fixtures are a fixed sample; a deny or block reached only by an input outside them is not seen.
    Leg (w) judges output shape, not whether a warning is correct.
  - Leg (a) judges a self-test by its exit status only.
  - Leg (k) tries the named variables only; another variable that changes behaviour is not found.
  - Leg (p) compares the listed names only; shared code outside the lists can drift.
  - Leg (p): loaded code can REPLACE the reporting machinery: code that itself prints a forged
    result line and the terminator to the child's stdout, keeps its error stream empty and exits 0
    passes; secfcl exempts exactly this channel for reviewed, pinned loaded code, disclosed here as
    a residual (merge train 2 QA r2, claude MINOR).
  - Leg (p): the r2 finalization residual is closed (merge train 2 QA r3: pending finalizers run
    before the result and the child ends with os._exit at it, so a finalizer that ends the child
    silently leaves no result). The remaining channels are the shared child contract's, disclosed
    ONCE in opf/tools/_child_contract.py (its DISCLOSED RESIDUALS section): a thread's uncaught
    exception raising no audit event; the window while loaded code has DELETED sys.unraisablehook
    with sys.stderr replaced, both restored before the result (merge train 2 QA r5, codex MEDIUM,
    reproduced), in the result write as in cleanup; an object still reachable at the result,
    never finalized (os._exit), and a thread cleanup code started before the result handler ran,
    still running at the result, ended by the os._exit unobserved (it also runs concurrently with
    the handler, outside its ordering); and the address-reuse edge of the identity-set settle
    comparison (merge train 2 QA r6).
  - The scripts run under this gate's interpreter (sys.executable), not the host's `python3` lookup.
    Not exercised: a python3 too old to accept -I (before 3.4), which exits 2 with a usage error before
    the launcher runs; a python3 that cannot be found or started (the outcome is the host's); and a
    script that ends the process itself with os._exit(2), which bypasses the launcher (leg (w) sees it
    only on a fixture that reaches it); a directory as a standard stream, which makes Python exit 1 at
    startup; and a host that runs command and args through a shell (the launcher assumes it does not).
  - Leg (w) reconciles only entries that name a manifest script; an entry naming none (a dispatcher
    row, or any other) and group-level keys are tools/gen_hooks.py --check's drift comparison. An entry
    that reaches a script without a word NAMING accepts (a path built at run time, a relative path to a
    copy under another name, or the program read from stdin) is not matched.

Exit: 0 pass; 1 a finding; 2 cannot-evaluate (a missing input, an unloadable pair file, a timeout, a
fixture set and manifest mismatch).
  check_hook_scripts.py             run the gate
  check_hook_scripts.py --self-test seeded faults against copies of the real files
"""
import datetime
import importlib.util
import inspect
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST_REL = ".aiqt/core/hooks/manifest.toml"
SRC_REL = ".aiqt/core/hooks/scripts"
PLUGIN_REL = "plugin/aiqt-guardrails-hooks"
HOOKS_JSON_REL = PLUGIN_REL + "/hooks/hooks.json"
PREVIEW_REL = ".preview"
STOP_REL = ".preview/stamp-truth-stop.py"
CLOCK = "clock-inject.py"
RECORD = "future-stamp-write.py"
SCRUB_PREFIXES = ("AIQT_", "ORCH_", "CLAUDE_")
SCRUB_NAMES = ("CDPATH",)
SELFTEST_TIMEOUT = 600
RUN_TIMEOUT = 60
WARN_MAX = 100
FORBIDDEN_KEYS = ("decision", "permissionDecision", "continue", "stopReason")
DIAG_LIMIT = 300

# Leg (k): each variant is added to a fixture's own environment; the outcome must not change.
INERT_VARIANTS = (
    ("AIQT_HOOKS_WORKER=1", (("AIQT_HOOKS_WORKER", "1"),)),
    ("ORCH_WORKER=1", (("ORCH_WORKER", "1"),)),
    ("ORCH_VERIFY_OWNER empty", (("ORCH_VERIFY_OWNER", ""),)),
    ("ORCH_VERIFY_OWNER=x", (("ORCH_VERIFY_OWNER", "x"),)),
    ("all markers", (("AIQT_HOOKS_WORKER", "1"), ("ORCH_WORKER", "1"), ("ORCH_VERIFY_OWNER", "x"))),
)

# Leg (p): (label, file a, file b, functions, regexes, constants). Names are compared across both files.
PAIRS = (
    ("A", SRC_REL + "/" + CLOCK, STOP_REL,
     ("read_regular", "lease_file", "_utc_field", "transcript_start", "lease_start",
      "_wall_clock_asserts", "_wall_clock_alias_fixtures"),
     ("_SESS_RE", "_LEASE_FIELD_RE", "_START_VALUE_RE", "_HEADING_RE"),
     ("LEASE_MAX_BYTES", "TRANSCRIPT_PREFIX_BYTES")),
    ("B", SRC_REL + "/" + RECORD, STOP_REL,
     ("sched_exempter", "_code_lines", "_code_spans", "_in_spans", "_wall_clock_asserts",
      "_wall_clock_alias_fixtures"),
     ("_SCHED_RE", "_TOKEN_RE", "_WORDCH_RE", "_BTICK_RE", "_FENCE_RE"),
     ("SCHED_KEYWORDS", "SCHED_GAP_TOKENS", "TIME_GRAMMAR", "ZONE_GRAMMAR", "SCHED_STEMS")),
    ("C", SRC_REL + "/" + CLOCK, SRC_REL + "/" + RECORD, ("_cfg",), (), ()),
)
# Declared divergence: these names must be ABSENT from both pack copies.
ABSENT_IN_PACK = ("_is_worker", "_sibling_or_skip")
# Leg (p): the parity child's completeness terminator, written after the JSON result line at
# the child's shutdown; a result without it was cut short by the loaded code's cleanup (secfcl).
PARITY_COMPLETE = "AIQT-PARITY-RESULT-COMPLETE"
# The parity child's record handler, settling collection, guarded reporting machinery and ending
# os._exit are the shared fail-closed child contract's, implemented once in
# opf/tools/_child_contract.py (merge train 2 QA r6; parity_child wires it to this gate's result
# and fault line).


class GateError(Exception):
    """A cannot-evaluate condition (exit 2)."""


def _bounded(data, limit=DIAG_LIMIT):
    if isinstance(data, bytes):
        data = data.decode("utf-8", "replace")
    text = " ".join(str(data).split())
    return text if len(text) <= limit else text[:limit] + "..."


def _stamp(dt, zoned=True):
    return dt.strftime("%Y-%m-%dT%H:%M:%S") + ("Z" if zoned else "")


def build_specs(now, base):
    """The fixture sets, keyed by script file name. Each fixture is a dict with: name, payload (str, or
    None for closed stdin), env (extra variables), expect (silent, warn, context, or None to record
    only), inert (rerun under INERT_VARIANTS), and not_in (a substring the output must lack, or None).
    `base` is a per-gate temporary directory holding the store and the lease."""
    store = os.path.join(base, "store")
    lease = os.path.join(base, "lease.txt")
    fut = _stamp(now + datetime.timedelta(days=2))
    fut_unzoned = _stamp(now + datetime.timedelta(days=2), zoned=False)
    past = _stamp(now - datetime.timedelta(days=30))
    long_lit = (now + datetime.timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%S") + ".123456789+05:30"
    rec = os.path.join(store, "record.md")
    st = dict(AIQT_STORE_ROOT=store)

    def write(path, content):
        return json.dumps(dict(hook_event_name="PreToolUse", tool_name="Write", cwd=base,
                               tool_input=dict(file_path=path, content=content)))

    def bash(command, cwd):
        return json.dumps(dict(hook_event_name="PreToolUse", tool_name="Bash", cwd=cwd,
                               tool_input=dict(command=command)))

    def fx(name, payload, env, expect, inert=True, not_in=None):
        return dict(name=name, payload=payload, env=env, expect=expect, inert=inert, not_in=not_in)

    record = (
        fx("write-future", write(rec, "Heartbeat: %s\n" % fut), st, "warn"),
        fx("write-past", write(rec, "Heartbeat: %s\n" % past), st, "silent"),
        fx("bash-append-future", bash("printf 'seen: %s\\n' >> %s/log.md" % (fut, store), base), st, "warn"),
        fx("write-outside-store", write(os.path.join(base, "other.md"), "seen: %s\n" % fut), st, "silent"),
        fx("long-zoned-literal", write(rec, "".join("at %s\n" % long_lit for _ in range(5))), st, "warn"),
        fx("no-store", write(rec, "Heartbeat: %s\n" % fut), dict(), "silent"),
        fx("legacy-store-only", write(rec, "Heartbeat: %s\n" % fut), dict(ORCH_STORE_ROOT=store), "silent"),
        fx("malformed", "not json", st, "silent"),
        fx("closed-stdin", None, st, "silent"),
        fx("cdpath-relative-cd", bash("cd child; printf 'seen: %s\\n' > record.md" % fut, store),
           dict(AIQT_STORE_ROOT=store, CDPATH="/elsewhere"), "silent", inert=False),
        fx("tz-unzoned", write(rec, "Heartbeat: %s\n" % fut_unzoned),
           dict(AIQT_STORE_ROOT=store, TZ="EST5EDT"), None, inert=False),
    )
    post = json.dumps(dict(hook_event_name="PostToolUse", tool_name="Bash", tool_input=dict(command="true")))
    clock = (
        fx("post-tool", post, dict(), "context"),
        fx("lease", post, dict(AIQT_LEASE_FILE=lease), "context"),
        fx("legacy-lease-only", post, dict(ORCH_LEASE_FILE=lease), "context", not_in="elapsed"),
        fx("malformed", "not json", dict(), "context"),
        fx("closed-stdin", None, dict(), "context"),
        fx("tz", post, dict(TZ="EST5EDT"), None, inert=False),
    )
    specs = dict()
    specs[RECORD] = dict(event="PreToolUse", fixtures=record)
    specs[CLOCK] = dict(event="PostToolUse", fixtures=clock)
    return specs


def prepare_base(base, now):
    """Create the store and a valid lease (session started one hour ago) under base."""
    os.makedirs(os.path.join(base, "store"), exist_ok=True)
    start = (now - datetime.timedelta(hours=1)).strftime("%Y%m%dT%H%M%SZ")
    with open(os.path.join(base, "lease.txt"), "w", encoding="utf-8") as fh:
        fh.write("Active-session: sess-%s\n" % start)


def contract_env(extra):
    env = dict((k, v) for k, v in os.environ.items()
               if not k.startswith(SCRUB_PREFIXES) and k not in SCRUB_NAMES)
    env["TZ"] = "UTC"
    env.update(extra)
    return env


def run_script(path, args, payload, extra, timeout):
    """Run one script directly under the contract; returns (rc, stdout bytes, stderr bytes)."""
    return run_argv(["-I", "-S", "-B", str(path)] + list(args), payload, extra, timeout)


def launch_argv(args, plugin_root):
    """The rendered args with the plugin root substituted (run_argv puts sys.executable first)."""
    return [str(a).replace("${CLAUDE_PLUGIN_ROOT}", str(plugin_root)) for a in args]


def run_argv(pyargs, payload, extra, timeout):
    """Run sys.executable with pyargs under the contract; returns (rc, stdout bytes, stderr bytes).
    Raises GateError on a timeout or a launch failure."""
    path = pyargs[-1]
    work = tempfile.mkdtemp(prefix="aiqt-hook-scripts-")
    try:
        argv = [sys.executable] + list(pyargs)
        try:
            if payload is None:
                res = subprocess.run(argv, cwd=work, capture_output=True, timeout=timeout,
                                     env=contract_env(extra), stdin=subprocess.DEVNULL)
            else:
                res = subprocess.run(argv, cwd=work, capture_output=True, timeout=timeout,
                                     env=contract_env(extra), input=payload.encode("utf-8"))
        except subprocess.TimeoutExpired:
            raise GateError("%s: did not finish within %ss" % (path, timeout))
        except (OSError, subprocess.SubprocessError) as exc:
            raise GateError("%s: could not be launched: %s" % (path, _bounded(exc)))
        return res.returncode, res.stdout, res.stderr
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _walk_keys(obj):
    if isinstance(obj, dict):
        for key, value in obj.items():
            yield key
            yield from _walk_keys(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from _walk_keys(value)


def classify(script, rc, out, event):
    """(class, problems) for one run. Class is silent, warn, context, or other."""
    problems = []
    if rc != 0:
        problems.append("exit %s (only 0 is allowed)" % rc)
    text = out.decode("utf-8", "replace")
    if not text.strip():
        return "silent", problems
    try:
        obj = json.loads(text)
    except ValueError:
        return "other", problems + ["stdout is not one JSON object: %s" % _bounded(text)]
    if not isinstance(obj, dict):
        return "other", problems + ["stdout is not a JSON object"]
    for key in _walk_keys(obj):
        if key in FORBIDDEN_KEYS:
            problems.append("forbidden decision key %r" % key)
    keys = sorted(obj)
    if script == RECORD:
        if keys != ["systemMessage"]:
            return "other", problems + ["keys %s, expected only systemMessage" % keys]
        msg = obj["systemMessage"]
        if not isinstance(msg, str):
            return "other", problems + ["systemMessage is not a string"]
        if len(msg) > WARN_MAX:
            problems.append("systemMessage is %s characters (at most %s)" % (len(msg), WARN_MAX))
        if "\n" in msg or "\r" in msg:
            problems.append("systemMessage holds a line break")
        return "warn", problems
    if keys != ["hookSpecificOutput"] or not isinstance(obj["hookSpecificOutput"], dict):
        return "other", problems + ["keys %s, expected only hookSpecificOutput" % keys]
    inner = obj["hookSpecificOutput"]
    if sorted(inner) != ["additionalContext", "hookEventName"]:
        return "other", problems + ["hookSpecificOutput keys %s" % sorted(inner)]
    if inner["hookEventName"] != event:
        problems.append("hookEventName %r, expected %r" % (inner["hookEventName"], event))
    ctx = inner["additionalContext"]
    if not isinstance(ctx, str) or "\n" in ctx or not ctx.startswith("CLOCK (read by hook, authoritative): "):
        problems.append("additionalContext is not one CLOCK line: %s" % _bounded(ctx))
    return "context", problems


def reduce_outcome(script, rc, out, err, event):
    cls, _problems = classify(script, rc, out, event)
    try:
        keys = tuple(sorted(json.loads(out.decode("utf-8", "replace")))) if out.strip() else ()
    except (ValueError, TypeError):
        keys = ("<unparsed>",)
    return (rc, keys, cls, not err.strip(), re.sub(r"\d+", "#", out.decode("utf-8", "replace")))


def load_script_rows(root):
    """([(script, event, matcher, timeout)] for every manifest row with a `script` key, the generator's
    SCRIPT_LAUNCHER program)."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        import gen_hooks  # noqa: E402  the manifest loader and its validation
    finally:
        sys.path.pop(0)
    try:
        _plugin, hooks = gen_hooks.load_manifest(root / MANIFEST_REL)
    except (ValueError, OSError) as exc:
        raise GateError("%s: %s" % (MANIFEST_REL, _bounded(exc)))
    return ([(h["script"], h["event"], h.get("matcher"), h.get("timeout", gen_hooks.TIMEOUT))
             for h in hooks if "script" in h], gen_hooks.SCRIPT_LAUNCHER)


def want_args(script, launcher):
    """The exact args of a script's rendered entry (its command is "python3")."""
    return ["-I", "-S", "-B", "-c", launcher, "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/" + script]


_WORD_RE = re.compile(r"[^\s'\"`=;|&<>()]+")
_ROOT_VARS = ("${CLAUDE_PLUGIN_ROOT}", "$CLAUDE_PLUGIN_ROOT")


def _names_script(text, plugin_root, script, target):
    """Whether a word of text names script by its path (see NAMING in the module docstring)."""
    for word in _WORD_RE.findall(text):
        if os.path.basename(os.path.normpath(word)) == script:
            return True
        for var in _ROOT_VARS:
            word = word.replace(var, str(plugin_root))
        if not os.path.isabs(word):
            continue
        resolved = os.path.realpath(word)
        if resolved == target:
            return True
        try:
            if os.path.samefile(resolved, target):
                return True
        except OSError:
            pass
    return False


def rendered_entries(root, script):
    """[(event, matcher, entry dict)] for every hooks.json entry that names script in its command or in any
    of its args, in any spelling of its path (NAMING in the module docstring)."""
    plugin_root = root / PLUGIN_REL
    target = os.path.realpath(plugin_root / "hooks" / "scripts" / script)
    try:
        with open(root / HOOKS_JSON_REL, encoding="utf-8") as fh:
            rendered = json.load(fh)
        found = []
        for ev, groups in rendered["hooks"].items():
            for group in groups:
                for hook in group.get("hooks", []):
                    args = hook.get("args")
                    texts = [str(a) for a in args] if isinstance(args, list) else [str(args)]
                    if any(_names_script(t, plugin_root, script, target)
                           for t in texts + [str(hook.get("command"))]):
                        found.append((ev, group.get("matcher"), hook))
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise GateError("%s: %s" % (HOOKS_JSON_REL, _bounded(exc)))
    return found


def _entry_key(event, matcher, hook):
    return (event, matcher, json.dumps(hook, sort_keys=True))


def _describe(entry_json):
    """An entry for a finding: every key but args first, then the args, bounded."""
    hook = json.loads(entry_json)
    args = hook.pop("args", None)
    return "%s args %s" % (json.dumps(hook, sort_keys=True), _bounded(json.dumps(args), 600))


def check_rendered(root, rows, launcher, findings):
    """Reconcile, per script, the complete set of hooks.json entries naming it against its manifest rows,
    multiplicity included: each row wants exactly one entry, the isolated launcher with type, command,
    args and timeout all compared and no other key; any entry left over on either side is a finding."""
    for script in sorted(set(r[0] for r in rows)):
        want = dict()
        for _s, event, matcher, timeout in (r for r in rows if r[0] == script):
            hook = {"type": "command", "command": "python3", "args": want_args(script, launcher),
                    "timeout": timeout}
            key = _entry_key(event, matcher, hook)
            want[key] = want.get(key, 0) + 1
        got = dict()
        for event, matcher, hook in rendered_entries(root, script):
            key = _entry_key(event, matcher, hook)
            got[key] = got.get(key, 0) + 1
        for key in sorted(set(want) | set(got), key=repr):
            short, extra = want.get(key, 0) - got.get(key, 0), got.get(key, 0) - want.get(key, 0)
            if short > 0:
                findings.append("(w) %s: %s missing hooks.json entry %s %r of type 'command' with command "
                                "'python3' and the launcher args: %s"
                                % (script, short, key[0], key[1], _describe(key[2])))
            if extra > 0:
                findings.append("(w) %s: %s hooks.json entry not accounted for by the manifest (each "
                                "script row renders exactly one): %s %r %s"
                                % (script, extra, key[0], key[1], _describe(key[2])))


# Leg (l): (case, file content or None for no file, or "<dir>" for a directory, file mode or None,
# args appended after the rendered ones, expected exit).
_RUNS = "import sys\nsys.exit(0 if __name__ == '__main__' and sys.argv == [__file__] else 1)\n"
LAUNCH_CASES = (
    ("missing", None, None, (), 1),
    ("unreadable", "import sys\nsys.exit(2)\n", 0o000, (), 1),
    ("directory", "<dir>", None, (), 1),
    ("syntax-error", "def (\n", None, (), 1),
    ("raises", "raise RuntimeError('seeded launch fault')\n", None, (), 1),
    ("exit-2", "import sys\nsys.exit(2)\n", None, (), 1),
    ("runs", _RUNS, None, (), 0),
    ("runs-appended-arg", _RUNS, None, ("--host-appended",), 0),
)


def _still_readable(path):
    try:
        with open(path, "rb"):
            return True
    except OSError:
        return False


def leg_launch(root, rows, findings):
    """Leg (l): run every hooks.json entry naming a script against a missing, broken or exiting script
    file. The rows only name the scripts; the entries run are all those in hooks.json."""
    for script in sorted(set(r[0] for r in rows)):
        found = rendered_entries(root, script)
        if not found:
            findings.append("(l) %s: no hooks.json entry to launch" % script)
            continue
        for number, (event, matcher, hook) in enumerate(found, 1):
            args = hook.get("args")
            where = "%s (%s %r entry %s of %s)" % (script, event, matcher, number, len(found))
            if (hook.get("type") != "command" or hook.get("command") != "python3"
                    or not isinstance(args, list) or not all(isinstance(a, str) for a in args)):
                findings.append("(l) %s: type %r command %r is not the python3 launcher; the launch cannot "
                                "be judged" % (where, hook.get("type"), hook.get("command")))
                continue
            for case, content, mode, appended, expect in LAUNCH_CASES:
                label = "(l) %s %s" % (where, case)
                plugin = Path(tempfile.mkdtemp(prefix="aiqt-hook-launch-"))
                try:
                    target = plugin / "hooks" / "scripts" / script
                    os.makedirs(target.parent)
                    if content == "<dir>":
                        os.makedirs(target)
                    elif content is not None:
                        target.write_text(content, encoding="utf-8")
                    if mode is not None:
                        os.chmod(target, mode)
                        if _still_readable(target):
                            print("  %s: the file stays readable to this user, so it ran as an exit 2" % label)
                    rc, out, err = run_argv(launch_argv(args, plugin) + list(appended), "{}", dict(),
                                            RUN_TIMEOUT)
                finally:
                    shutil.rmtree(plugin, ignore_errors=True)
                if rc != expect:
                    findings.append("%s: the launcher exited %s, expected %s (a failure exits exactly 1: "
                                    "never 2, which blocks, nor 0, which hides it)" % (label, rc, expect))
                if out.strip():
                    findings.append("%s: the launcher printed on stdout: %s" % (label, _bounded(out)))
                if expect and not err.strip():
                    findings.append("%s: the failure is not reported on stderr" % label)
        print("  (l) %s: %s launch cases run on %s entr%s" % (script, len(LAUNCH_CASES), len(found),
                                                          "y" if len(found) == 1 else "ies"))


def leg_selftest(root, scripts, findings):
    for script in scripts:
        rc, out, err = run_script(root / SRC_REL / script, ["--self-test"], None, dict(), SELFTEST_TIMEOUT)
        if rc != 0:
            tail = (out + err).decode("utf-8", "replace").rstrip().splitlines()[-10:]
            findings.append("(a) %s: --self-test exited %s:\n      %s" % (script, rc, "\n      ".join(tail)))
        else:
            print("  (a) %s: --self-test exit 0" % script)


def leg_warn_and_inert(root, specs, scripts, launcher, findings):
    for script in scripts:
        spec = specs[script]
        path = root / PLUGIN_REL / "hooks" / "scripts" / script
        if not path.is_file():
            raise GateError("%s: the plugin copy is absent (run tools/gen_hooks.py)" % path)
        argv = launch_argv(want_args(script, launcher), root / PLUGIN_REL)
        nonsilent = False
        for fixture in spec["fixtures"]:
            label = "%s %s" % (script, fixture["name"])
            rc, out, err = run_argv(argv, fixture["payload"], fixture["env"], RUN_TIMEOUT)
            cls, problems = classify(script, rc, out, spec["event"])
            for problem in problems:
                findings.append("(w) %s: %s" % (label, problem))
            if fixture["not_in"] and fixture["not_in"] in out.decode("utf-8", "replace"):
                findings.append("(w) %s: output contains %r" % (label, fixture["not_in"]))
            if fixture["expect"] is None:
                print("  (k) %s: recorded dependency, class %s" % (label, cls))
            elif cls != fixture["expect"]:
                findings.append("(w) %s: class %s, expected %s" % (label, cls, fixture["expect"]))
            if not fixture["inert"]:
                continue
            if cls != "silent":
                nonsilent = True
            base = reduce_outcome(script, rc, out, err, spec["event"])
            for vname, pairs in INERT_VARIANTS:
                env = dict(fixture["env"])
                env.update(dict(pairs))
                vrc, vout, verr = run_argv(argv, fixture["payload"], env, RUN_TIMEOUT)
                _cls, vproblems = classify(script, vrc, vout, spec["event"])
                for problem in vproblems:
                    findings.append("(w) %s under %s: %s" % (label, vname, problem))
                got = reduce_outcome(script, vrc, vout, verr, spec["event"])
                if got != base:
                    findings.append("(k) %s: %s changes the outcome (%s -> %s)"
                                    % (label, vname, _bounded(repr(base[:4])), _bounded(repr(got[:4]))))
        if not nonsilent:
            findings.append("(k) %s: no inert fixture produced output, so inertness is untested" % script)
        print("  (w)(k) %s: %s fixtures run" % (script, len(spec["fixtures"])))


def leg_duplicates(root, scripts, findings):
    pdir = root / PREVIEW_REL
    if not pdir.is_dir():
        return
    try:
        names = set(os.listdir(pdir))
    except OSError as exc:
        raise GateError("%s: %s" % (PREVIEW_REL, _bounded(exc)))
    for script in sorted(set(scripts) & names):
        findings.append("(d) %s is both in %s/ and a pack script; remove the preview copy" % (script, PREVIEW_REL))


def leg_parity(root, findings):
    me = str(Path(__file__).resolve())
    for label, rel_a, rel_b, funcs, regexes, consts in PAIRS:
        for rel in (rel_a, rel_b):
            if not (root / rel).is_file():
                raise GateError("(p) pair %s: %s is absent" % (label, rel))
        spec = json.dumps([str(root / rel_a), str(root / rel_b), list(funcs), list(regexes), list(consts)])
        work = tempfile.mkdtemp(prefix="aiqt-hook-parity-")
        try:
            try:
                res = subprocess.run([sys.executable, "-I", "-S", "-B", me, "--parity-child", spec], cwd=work,
                                     capture_output=True, timeout=RUN_TIMEOUT, env=contract_env(dict()),
                                     stdin=subprocess.DEVNULL)
            except subprocess.TimeoutExpired:
                raise GateError("(p) pair %s: the comparison did not finish within %ss" % (label, RUN_TIMEOUT))
            except (OSError, subprocess.SubprocessError) as exc:
                raise GateError("(p) pair %s: could not be launched: %s" % (label, _bounded(exc)))
        finally:
            shutil.rmtree(work, ignore_errors=True)
        if res.returncode != 0:
            raise GateError("(p) pair %s: cannot evaluate: %s" % (label, _bounded(res.stdout + res.stderr)))
        if res.stderr:
            raise GateError("(p) pair %s: a fault on the comparison child's error stream over its "
                            "zero exit; any bytes there -- a traceback, a frameless 'Exception "
                            "ignored' line, a finalizer's write -- are a cleanup or thread fault "
                            "in loaded code, never a pass (secfcl): %s"
                            % (label, _bounded(res.stderr)))
        lines = res.stdout.decode("utf-8", "replace").splitlines()
        if len(lines) < 2 or lines[-1] != PARITY_COMPLETE:
            raise GateError("(p) pair %s: no complete structured result after the child's cleanup; "
                            "loaded code that ends or cuts the child during cleanup leaves no "
                            "verdict to accept (secfcl): %s" % (label, _bounded(res.stdout + res.stderr)))
        try:
            diffs = json.loads(lines[-2])
        except ValueError:
            raise GateError("(p) pair %s: unreadable comparison output" % label)
        for name in diffs:
            findings.append("(p) pair %s (%s vs %s): %s differs" % (label, rel_a, rel_b, name))
    for rel in (SRC_REL + "/" + CLOCK, SRC_REL + "/" + RECORD):
        try:
            with open(root / rel, encoding="utf-8") as fh:
                text = fh.read()
        except OSError as exc:
            raise GateError("(p) %s: %s" % (rel, _bounded(exc)))
        for name in ABSENT_IN_PACK:
            if re.search(r"^\s*(?:def\s+%s\b|%s\s*=)" % (name, name), text, re.M):
                findings.append("(p) %s: %s must be absent from a pack copy" % (rel, name))


def parity_child(spec):
    """Child mode: load both files and compare the named objects. The verdict discipline is the
    shared fail-closed child contract, implemented ONCE in opf/tools/_child_contract.py (the
    _semver single-source idiom: the one implementation lives in the dependency-closed opf/
    subtree, this gate imports it from there, and its module docstring states the contract and
    discloses, once, the channels it cannot close). The structured result (one JSON line holding
    the list of names that differ, then the PARITY_COMPLETE line) is written by the contract's
    record handler, registered FIRST, before either file is loaded: exit handlers run
    last-registered-first, after non-daemon threads are joined, so the write comes after every
    cleanup handler the loaded files registered, and loaded code that ends the child from its
    cleanup suppresses the result instead of leaving an already-reported pass standing (the
    fail-closed child-process rule, secfcl; merge train 2 QA rounds 1 to 6). Before the result
    the handler collects pending cyclic-garbage finalizers REPEATEDLY until a pass CHANGES
    NOTHING (frees no object and leaves the identity set of tracked objects unchanged), within
    _child_contract.GC_PASS_BOUND passes, past which it refuses by name (r4: a collected
    finalizer may create new cyclic cleanup work; r5: a resurrecting finalizer frees nothing in
    its pass; r6: one that also releases other tracked objects keeps the tracked count equal or
    lower, so both count rules settled over its pending faulting work, where the identity
    comparison does not), restores the guarded reporting machinery (sys.stderr, sys.excepthook,
    sys.unraisablehook and threading.excepthook, checked against the objects this child started
    with, before, between and after the passes; an audit hook armed before either file loads
    sees every `sys.unraisablehook` and `sys.excepthook` event, which CPython raises before it
    calls whatever hook is installed), writes the result only on a fault-free settle, and ends
    the child ITSELF with os._exit, so no interpreter finalization runs after the result. Any
    such event, any machinery replacement, a closed original stderr, an unsettled collection or
    a flush failure writes the named fault line to fd 2 and exits 2 with no result; a result
    write or flush that RAISES (after the result reached stdout) exits 2 with the contract's
    one-line diagnostic, and the nonzero exit is refused (merge train 2 QA r7); a result write
    that RETURNS but faulted without raising (an unraisable fault the audit hook saw, the
    machinery left replaced, a thread started, a flush failure on the re-check) also exits 2,
    with the contract's diagnostic, because the contract re-runs its checks after the record
    callback and a new fault voids the result (merge train 2 QA r8). That re-check is the
    FINAL check, and no code the check calls runs after its own judgments (contract step 6, merge
    train 2 QA r9, codex MAJOR: its own stdout flush, which runs a loaded wrapper, used to come
    after its machinery check and collection); after it the contract disarms every interpreter
    callback loaded code left armed (a profile or trace function, a sys.monitoring tool, a gc
    callback), refuses one it finds and judges again before its decision (QA r10), so what loaded
    code can still run after the decision is only the contract module's disclosed residuals (c),
    (e) and (f): the result line is written and flushed through sys.stdout
    before it, and the PARITY_COMPLETE terminator only after it, by os.write on the stdout
    descriptor the contract saved at register(), so a fault the final check finds leaves the
    result without its terminator and the exit 2. Exit 2 also
    if a file or the contract module cannot be loaded, or if another exit handler was registered
    before the record handler (its os._exit would skip that one)."""
    sys.path.insert(0, str(ROOT / "opf" / "tools"))
    try:
        import _child_contract  # the shared fail-closed child contract (one source, no fork)
    except ImportError as exc:
        print("cannot load the shared child contract (opf/tools/_child_contract.py): %s"
              % _bounded(exc))
        return 2
    finally:
        sys.path.pop(0)
    path_a, path_b, funcs, regexes, consts = json.loads(spec)
    outcome = []

    def record(code):
        # The result line goes through sys.stdout BEFORE the contract's final check; the
        # terminator is the seal, written by os.write on the descriptor saved at register() only
        # AFTER that check passes (contract step 6, merge train 2 QA r9).
        if code == 0:
            sys.stdout.write(json.dumps(outcome[0]) + "\n")
            sys.stdout.flush()
            return [(contract.saved_stdout, (PARITY_COMPLETE + "\n").encode("ascii"))]
        return ()

    def fault_line(faults):
        return ("parity child: a fault in loaded code's cleanup or its reporting "
                "machinery: %s; no result (secfcl)\n"
                % "; ".join(sorted(set(faults)))[:1000]).encode("utf-8", "replace")

    contract = _child_contract.FailClosedChild(record=record, fault_line=fault_line)
    if not contract.register():
        print("another exit handler was registered before the result's; refusing")
        return 2
    contract.arm_audit()
    mods = []
    for i, path in enumerate((path_a, path_b)):
        try:
            mspec = importlib.util.spec_from_file_location("_aiqt_parity_%s" % i, path)
            mod = importlib.util.module_from_spec(mspec)
            mspec.loader.exec_module(mod)
        except Exception as exc:  # any load failure is a cannot-evaluate
            print("cannot load %s: %s" % (path, _bounded(exc)))
            return 2
        mods.append(mod)
    diffs = []
    missing = object()
    for kind, names in (("func", funcs), ("regex", regexes), ("const", consts)):
        for name in names:
            a, b = (getattr(m, name, missing) for m in mods)
            if a is missing or b is missing:
                diffs.append("missing:" + name)
            elif kind == "func" and inspect.getsource(a) != inspect.getsource(b):
                diffs.append(name)
            elif kind == "regex" and (a.pattern, a.flags) != (b.pattern, b.flags):
                diffs.append(name)
            elif kind == "const" and a != b:
                diffs.append(name)
    outcome.append(diffs)
    contract.settle(0)
    return 0


def run_gate(root, legs=("a", "w", "l", "d", "p"), only=None):
    """(findings, unverifiable) for the tree at root. `only` restricts legs (a) and (w)(k) to scripts."""
    findings = []
    try:
        rows, launcher = load_script_rows(root)
        scripts = sorted(set(r[0] for r in rows))
        now = datetime.datetime.now(datetime.timezone.utc)
        base = tempfile.mkdtemp(prefix="aiqt-hook-scripts-base-")
        try:
            prepare_base(base, now)
            specs = build_specs(now, base)
            if set(scripts) != set(specs):
                raise GateError("manifest scripts %s but the fixture sets cover %s; update build_specs"
                                % (scripts, sorted(specs)))
            chosen = [s for s in scripts if only is None or s in only]
            if "a" in legs:
                leg_selftest(root, chosen, findings)
            if "w" in legs:
                check_rendered(root, rows, launcher, findings)
                leg_warn_and_inert(root, specs, chosen, launcher, findings)
            if "l" in legs:
                leg_launch(root, [r for r in rows if r[0] in chosen], findings)
            if "d" in legs:
                leg_duplicates(root, scripts, findings)
            if "p" in legs:
                leg_parity(root, findings)
        finally:
            shutil.rmtree(base, ignore_errors=True)
    except GateError as exc:
        return findings, [str(exc)]
    except OSError as exc:
        return findings, ["cannot evaluate: %s" % _bounded(exc)]
    return findings, []


def report(findings, unverifiable):
    for item in unverifiable:
        print("CANNOT EVALUATE: " + item)
    if findings:
        print("FAIL: %s hook-scripts finding(s)" % len(findings))
        for item in findings:
            print("  " + item)
    if unverifiable:
        return 2
    if findings:
        return 1
    print("PASS: hook scripts are warn-only, inert to the worker and legacy variables tried, launched "
          "so a missing or failing script exits 1 (never 2), unduplicated, and in parity")
    return 0


def main(argv):
    if argv[1:2] == ["--parity-child"] and len(argv) == 3:
        return parity_child(argv[2])
    if argv[1:] == ["--self-test"]:
        return self_test_main()
    if argv[1:]:
        print("usage: check_hook_scripts.py [--self-test]")
        return 2
    print("hook-scripts: %s" % MANIFEST_REL)
    findings, unverifiable = run_gate(ROOT)
    return report(findings, unverifiable)


# --- self-test -------------------------------------------------------------------------------------
# Seeded faults against a scratch copy of the real files. Each must be caught, by the finding text of the
# check it pins (every needle of a row must appear): a deny key; a block key (both the forbidden-key walk
# and the clock shape check); an honoured worker variable; an over-length line holding a line break (the
# length and line-break checks); a script returning 2 (the exit check: the launcher reports it as exit
# 1, needled separately on the scrubbed run and on an inertness rerun, so each launch site is pinned on
# its own) and one ending the process with os._exit(2) (the exit check sees the 2 itself); a silent clock
# script (the class check and the no-inert-output check); a wrong hookEventName, a non-CLOCK context
# and an `elapsed` segment where a fixture forbids it; a failing self-test; in hooks.json, a substituted
# command, timeout, matcher, or an extra key in an entry (leg (w)'s whole-entry comparison), the launcher
# reverted to a direct `python3 <file>` launch (leg (l): a missing script exits 2),
# a launcher that never runs the file, one that reports a failure on stdout, not stderr, one that takes
# the last argument as the script (leg (l)'s appended argument), one that swallows a failure as exit 0
# (leg (l)'s exact 1), and one that exits 2 on an unreadable file (leg (l)'s unreadable case, skipped
# with a printed note where this user can read a mode 0 file); an extra entry beside the right one that
# exits 2 (caught by leg (w)'s reconciliation and, separately, by leg (l) running every entry), an extra
# direct `python3 <file>` entry (leg (l)), and a duplicate of the right entry (leg (w)'s counts); the
# extra exit-2 entry once per other spelling of the script path (./, //, .., a symlinked plugin root, a
# symlink under another name, a relative path), each caught by legs (w) and (l) through NAMING;
# a basename present in .preview/ too; a manifest
# script with no fixture set (cannot-evaluate); and for leg (p): a one-character change to a shared
# constant, a changed shared function on either side, a changed regex, a drifted _cfg, _is_worker
# reintroduced into a pack copy, a missing pair file (exit 2), and the secfcl child-process pair
# (merge train 2 QA rounds 1 and 2; each a cannot-evaluate, and each passed before the parity child
# met the rule): a seeded background-thread fault in a loaded file (zero exit, empty diffs, the
# traceback only on the child's error stream), a seeded cleanup os._exit(0) (the child ends
# before its shutdown-written result), a seeded BUILTIN cleanup hook that raises (a frameless
# 'Exception ignored' line, no 'Traceback' header, over a zero exit and a complete result: only
# the any-bytes error-stream check refuses it, r2 claude MEDIUM), a seeded cyclic shutdown
# finalizer that writes to stderr and os._exit(0)s AFTER the emitted result (r2 codex MEDIUM),
# and a seeded import-time forged two-line stdout ('[]' then 'done') cut off by a cleanup
# os._exit(0), which only the terminator comparison refuses (r2 claude MINOR). The unmodified
# copy must pass legs
# (w), (k), (l), (d) and (p).

_FAULT_HEAD = "def main(argv):\n"


def _fault(body):
    """Source that replaces main() with `body` (an indented block that may call _orig_main)."""
    return "def main(argv):\n" + body + "\n\n\ndef _orig_main(argv):\n"


# (name, script, leg, needles, replacement main). Every needle must appear in some finding.
_FAULTS = (
    ("deny", RECORD, "w", ("permissionDecision",),
     _fault("    print(json.dumps(dict(hookSpecificOutput=dict(hookEventName='PreToolUse', "
            "permissionDecision='deny'))))\n    return 0")),
    ("block", CLOCK, "w", ("forbidden decision key 'decision'", "expected only hookSpecificOutput"),
     _fault("    print(json.dumps(dict(decision='block', reason='x')))\n    return 0")),
    ("worker", RECORD, "w", ("AIQT_HOOKS_WORKER=1 changes",),
     _fault("    if os.environ.get('AIQT_HOOKS_WORKER') == '1':\n        return 0\n    return _orig_main(argv)")),
    ("overlong", RECORD, "w", ("characters (at most", "holds a line break"),
     _fault("    print(json.dumps(dict(systemMessage='x' * 150 + '\\n')))\n    return 0")),
    ("return-2", CLOCK, "w", ("post-tool: exit 1 (only 0 is allowed)",
                              "post-tool under AIQT_HOOKS_WORKER=1: exit 1 (only 0 is allowed)"),
     _fault("    return 2")),
    ("os-exit-2", CLOCK, "w", ("exit 2 (only 0 is allowed)",), _fault("    os._exit(2)")),
    ("silent", CLOCK, "w", ("class silent, expected context", "no inert fixture produced output"),
     _fault("    return 0")),
    ("clock-shape", CLOCK, "w", ("hookEventName 'PreToolUse', expected", "is not one CLOCK line",
                                 "output contains 'elapsed'"),
     _fault("    print(json.dumps(dict(hookSpecificOutput=dict(hookEventName='PreToolUse', "
            "additionalContext='elapsed'))))\n    return 0")),
    ("selftest", CLOCK, "a", ("--self-test exited 1",),
     _fault("    if argv[1:] == ['--self-test']:\n        return 1\n    return _orig_main(argv)")),
)

# Faults in the rendered hooks.json: (name, script, leg, needles, function(entry) that edits the entry).
_DIRECT_ARGS = ["-I", "-S", "-B"]
# A launcher that exits 1 on a failure but reports it on stdout (the hook protocol channel), not stderr.
_STDOUT_LAUNCHER = ("import sys\nimport runpy\ntry:\n    sys.argv = sys.argv[-1:]\n"
                    "    runpy.run_path(sys.argv[0], run_name='__main__')\nexcept SystemExit as e:\n"
                    "    sys.exit(0 if e.code in (None, 0) else 1)\nexcept BaseException as e:\n"
                    "    print(type(e).__name__)\n    sys.exit(1)\n")
# The real launcher with its fixed script position moved back to the last argument.
_ARGV_LAST = ("import sys\nsys.argv = ['-c', sys.argv[-1]]\n", "")
# A launcher that runs the file but swallows every failure as exit 0.
_SWALLOW_LAUNCHER = ("import sys\nimport runpy\ntry:\n    p = sys.argv[1]\n    sys.argv = [p]\n"
                     "    runpy.run_path(p, run_name='__main__')\nexcept BaseException:\n    pass\n")
# The real launcher, prefixed with an exit 2 for an unreadable regular file.
_UNREADABLE_2 = "import os, sys\nif os.path.isfile(sys.argv[1]) and not os.access(sys.argv[1], os.R_OK):\n    sys.exit(2)\n"
_RENDER_FAULTS = (
    ("command", CLOCK, "w", ("missing hooks.json entry", '"command": "/bin/false"'),
     lambda h: h.update(command="/bin/false")),
    ("timeout", CLOCK, "w", ("missing hooks.json entry", '"timeout": 11'),
     lambda h: h.update(timeout=h["timeout"] + 1)),
    ("extra-key", RECORD, "w", ("missing hooks.json entry", '"statusMessage": "seeded"'),
     lambda h: h.update(statusMessage="seeded")),
    ("direct-launch", RECORD, "l", ("missing: the launcher exited 2",),
     lambda h: h.update(args=_DIRECT_ARGS + h["args"][-1:])),
    ("launcher-skips-file", RECORD, "l", ("runs: the launcher exited 1, expected 0",),
     lambda h: h.update(args=h["args"][:4] + ["import sys; sys.exit(1)"] + h["args"][-1:])),
    ("launcher-stdout", CLOCK, "l", ("missing: the launcher printed on stdout",
                                     "missing: the failure is not reported on stderr"),
     lambda h: h.update(args=h["args"][:4] + [_STDOUT_LAUNCHER] + h["args"][-1:])),
    ("launcher-argv-last", RECORD, "l", ("runs-appended-arg: the launcher exited 1, expected 0",),
     lambda h: h.update(args=h["args"][:4] + [_ARGV_LAST[0] + h["args"][4]] + h["args"][-1:])),
    ("launcher-swallows", CLOCK, "l", ("missing: the launcher exited 0, expected 1",
                                       "exit-2: the launcher exited 0, expected 1"),
     lambda h: h.update(args=h["args"][:4] + [_SWALLOW_LAUNCHER] + h["args"][-1:])),
    ("launcher-unreadable-2", RECORD, "l", ("unreadable: the launcher exited 2, expected 1",),
     lambda h: h.update(args=h["args"][:4] + [_UNREADABLE_2 + h["args"][4]] + h["args"][-1:])),
)

# Faults in the group holding the entry: (name, script, leg, needles, function(group) that edits it).
_GROUP_FAULTS = (
    ("matcher", CLOCK, "w", ("missing hooks.json entry PostToolUse '.*'", "exactly one): PostToolUse 'Bash'"),
     lambda g: g.update(matcher="Bash")),
)

# Entries added beside the right one: (name, script, leg, needles, function(entry) returning the new entry).
_EXTRA_BLOCKING = lambda h: dict(h, args=h["args"][:4] + ["import sys; sys.exit(2)"] + h["args"][-1:])
_ADD_FAULTS = (
    ("extra-blocking-entry", RECORD, "w", ("hooks.json entry not accounted for by the manifest",
                                           "sys.exit(2)"), _EXTRA_BLOCKING),
    ("extra-blocking-entry", RECORD, "l", ("entry 2 of 2) missing: the launcher exited 2, expected 1",),
     _EXTRA_BLOCKING),
    ("extra-direct-entry", CLOCK, "l", ("entry 2 of 2) missing: the launcher exited 2, expected 1",),
     lambda h: dict(h, args=_DIRECT_ARGS + h["args"][-1:])),
    ("duplicate-entry", CLOCK, "w", ("1 hooks.json entry not accounted for by the manifest",),
     lambda h: dict(h)),
)

# Other spellings of the script path, each in an extra exit-2 entry beside the right one: (name, setup(root,
# script) run first or None, function(script, rendered path) giving the spelling).
_SCRIPTS_REL = "/hooks/scripts/"
_SPELLINGS = (
    ("dot", None, lambda s, p: p.replace(_SCRIPTS_REL, _SCRIPTS_REL + "./")),
    ("double-slash", None, lambda s, p: p.replace(_SCRIPTS_REL, _SCRIPTS_REL + "/")),
    ("dot-dot", None, lambda s, p: p.replace(_SCRIPTS_REL, _SCRIPTS_REL + "../scripts/")),
    ("symlinked-root",
     lambda r, s: os.symlink(Path(PLUGIN_REL).name, (r / PLUGIN_REL).parent / "aiqt-root-link"),
     lambda s, p: p.replace("${CLAUDE_PLUGIN_ROOT}", "${CLAUDE_PLUGIN_ROOT}/../aiqt-root-link")),
    ("symlink-alias", lambda r, s: os.symlink(s, r / PLUGIN_REL / "hooks" / "scripts" / "seeded-alias.py"),
     lambda s, p: p[:-len(s)] + "seeded-alias.py"),
    ("relative", None, lambda s, p: p.replace("${CLAUDE_PLUGIN_ROOT}/", "")),
)

_PARITY_FAULTS = (
    ("const", STOP_REL, "TRANSCRIPT_PREFIX_BYTES = 256 << 10", "TRANSCRIPT_PREFIX_BYTES = 257 << 10", "",
     "TRANSCRIPT_PREFIX_BYTES differs"),
    ("func-preview", STOP_REL, "", "", "\n\ndef read_regular(path, limit):\n    return None\n",
     "read_regular differs"),
    ("func-pack", SRC_REL + "/" + RECORD, "", "", "\n\ndef _in_spans(pos, spans):\n    return False\n",
     "_in_spans differs"),
    ("regex", STOP_REL, "", "", "\n\n_FENCE_RE = re.compile(_FENCE_RE.pattern + '(?:)', _FENCE_RE.flags)\n",
     "_FENCE_RE differs"),
    ("cfg", SRC_REL + "/" + CLOCK, "", "", "\n\ndef _cfg(name, env=None):\n    return None\n",
     "pair C"),
    ("is-worker", SRC_REL + "/" + CLOCK, "", "", "\n\ndef _is_worker(env=None):\n    return False\n",
     "_is_worker must be absent"),
)


def _copy_tree(src_root, dst):
    for rel in (MANIFEST_REL, SRC_REL + "/" + CLOCK, SRC_REL + "/" + RECORD, HOOKS_JSON_REL,
                PLUGIN_REL + "/hooks/scripts/" + CLOCK, PLUGIN_REL + "/hooks/scripts/" + RECORD, STOP_REL):
        os.makedirs((dst / rel).parent, exist_ok=True)
        shutil.copyfile(src_root / rel, dst / rel)


def _patch_rendered(root, script, edit, group_level=False):
    """Apply edit() to every hooks.json entry whose last arg names script (to its group if group_level)."""
    path = root / HOOKS_JSON_REL
    with open(path, encoding="utf-8") as fh:
        rendered = json.load(fh)
    hits = 0
    for groups in rendered["hooks"].values():
        for group in groups:
            for hook in group["hooks"]:
                if str(hook["args"][-1]).endswith("/hooks/scripts/" + script):
                    edit(group if group_level else hook)
                    hits += 1
    if not hits:
        raise GateError("self-test setup: no hooks.json entry for %s" % script)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(rendered, indent=2) + "\n")


def _add_rendered(root, script, make):
    """Append make(entry) to the group of every hooks.json entry whose last arg names script."""
    path = root / HOOKS_JSON_REL
    with open(path, encoding="utf-8") as fh:
        rendered = json.load(fh)
    hits = 0
    for groups in rendered["hooks"].values():
        for group in groups:
            for hook in list(group["hooks"]):
                if str(hook["args"][-1]).endswith("/hooks/scripts/" + script):
                    group["hooks"].append(make(hook))
                    hits += 1
    if not hits:
        raise GateError("self-test setup: no hooks.json entry for %s" % script)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(rendered, indent=2) + "\n")


def _mode0_unreadable(base):
    """Whether this user is refused a mode 0 file (a privileged user is not)."""
    probe = base / "mode0-probe"
    with open(probe, "w", encoding="utf-8") as fh:
        fh.write("x")
    os.chmod(probe, 0)
    try:
        return not _still_readable(probe)
    finally:
        os.unlink(probe)


def _patch(path, old, new, append=""):
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    if old and text.count(old) != 1:
        raise GateError("self-test setup: %r not found once in %s" % (old, path))
    if old:
        text = text.replace(old, new)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text + append)


def self_test_main():
    failures = []
    tmp = Path(tempfile.mkdtemp(prefix="aiqt-hook-scripts-selftest-"))
    try:
        def fresh(name):
            dst = tmp / name
            _copy_tree(ROOT, dst)
            return dst

        findings, unver = run_gate(fresh("clean"), legs=("w", "l", "d", "p"))
        if findings or unver:
            failures.append("clean copy: expected no finding, got %s %s" % (findings, unver))

        def expect(name, needles, findings, unver):
            missing = [n for n in needles if not any(n in f for f in findings)]
            if unver or missing:
                failures.append("fault %s: expected findings containing %r, got %s %s"
                                % (name, missing, findings[:4], unver))

        for name, script, leg, needles, src in _FAULTS:
            root = fresh(name)
            target = root / (SRC_REL if leg == "a" else PLUGIN_REL + "/hooks/scripts") / script
            _patch(target, _FAULT_HEAD, src)
            findings, unver = run_gate(root, legs=(leg,), only=(script,))
            expect(name, needles, findings, unver)

        mode0 = _mode0_unreadable(tmp)
        for name, script, leg, needles, edit in _RENDER_FAULTS:
            if name == "launcher-unreadable-2" and not mode0:
                print("  self-test: fault %s not run, this user can read a mode 0 file" % name)
                continue
            root = fresh("r-" + name)
            _patch_rendered(root, script, edit)
            findings, unver = run_gate(root, legs=(leg,), only=(script,))
            expect(name, needles, findings, unver)

        for name, script, leg, needles, edit in _GROUP_FAULTS:
            root = fresh("g-" + name)
            _patch_rendered(root, script, edit, group_level=True)
            findings, unver = run_gate(root, legs=(leg,), only=(script,))
            expect(name, needles, findings, unver)

        for name, script, leg, needles, make in _ADD_FAULTS:
            root = fresh("e-%s-%s" % (name, leg))
            _add_rendered(root, script, make)
            findings, unver = run_gate(root, legs=(leg,), only=(script,))
            expect("%s (leg %s)" % (name, leg), needles, findings, unver)

        for name, setup, spell in _SPELLINGS:
            root = fresh("s-" + name)
            if setup is not None:
                setup(root, RECORD)
            def respelled(h, spell=spell):
                extra = _EXTRA_BLOCKING(h)
                extra["args"] = extra["args"][:-1] + [spell(RECORD, extra["args"][-1])]
                return extra
            _add_rendered(root, RECORD, respelled)
            findings, unver = run_gate(root, legs=("w", "l"), only=(RECORD,))
            expect("spelling %s" % name, ("hooks.json entry not accounted for by the manifest",
                                          "entry 2 of 2) missing: the launcher exited 2, expected 1"),
                   findings, unver)

        extra = fresh("unfixtured")
        _patch(extra / MANIFEST_REL, "", "", '\n[[hook]]\nid = "unfixtured"\nrules = ["tstamp"]\n'
               'platform = "claude-code"\nevent = "PostToolUse"\nmatcher = ".*"\nscript = "unfixtured.py"\n'
               'default = "warn"\nclass = "c"\nresidue = "A self-test row with no fixture set."\n')
        findings, unver = run_gate(extra, legs=("d",))
        if not any("update build_specs" in u for u in unver):
            failures.append("a manifest script with no fixture set: expected cannot-evaluate, got %s %s"
                            % (findings, unver))

        dup = fresh("dup")
        shutil.copyfile(dup / SRC_REL / CLOCK, dup / PREVIEW_REL / CLOCK)
        findings, unver = run_gate(dup, legs=("d",))
        if unver or not any(f.startswith("(d) " + CLOCK) for f in findings):
            failures.append("dual-present basename: expected a (d) finding, got %s %s" % (findings, unver))

        for name, rel, old, new, append, needle in _PARITY_FAULTS:
            root = fresh("p-" + name)
            _patch(root / rel, old, new, append)
            findings, unver = run_gate(root, legs=("p",))
            if unver or not any(needle in f for f in findings):
                failures.append("parity fault %s: expected a finding containing %r, got %s %s"
                                % (name, needle, findings, unver))

        gone = fresh("p-missing")
        os.unlink(gone / STOP_REL)
        findings, unver = run_gate(gone, legs=("p",))
        if not unver:
            failures.append("missing pair file: expected cannot-evaluate (exit 2), got %s" % findings)

        # Merge train 2 QA, claude MEDIUM (secfcl): the parity verdict must cover the loaded
        # code's cleanup. The seeded thread fault leaves the child's exit 0 and its diffs empty,
        # so only the error-stream scan refuses it; the seeded cleanup os._exit(0) ends the child
        # before the shutdown-written result, so only the completeness check refuses it. Both
        # were a clean pass before the parity child met the fail-closed child-process rule.
        for name, append, needle in (
                ("cleanup-fault", "\n\nimport threading as _seeded_threading\n"
                 "_seeded_threading.Thread(target=lambda: 1 // 0).start()\n",
                 "fault on the comparison child's error stream"),
                ("cleanup-exit", "\n\nimport atexit as _seeded_atexit\nimport os as _seeded_os\n"
                 "_seeded_atexit.register(_seeded_os._exit, 0)\n",
                 "no complete structured result after the child's cleanup"),
                ("cleanup-builtin-fault", "\n\nimport atexit as _seeded_atexit\n"
                 "import os as _seeded_os\n"
                 "_seeded_atexit.register(_seeded_os.remove, '/nonexistent-aiqt-parity-selftest')\n",
                 "parity child: a fault in loaded code's cleanup"),
                ("finalizer-exit", "\n\nimport atexit as _seeded_atexit\nimport os as _seeded_os\n"
                 "\n\nclass _SeededFinalizer:\n"
                 "    def __init__(self):\n"
                 "        self.cycle = self\n"
                 "        self.write = _seeded_os.write\n"
                 "        self.exit = _seeded_os._exit\n"
                 "\n    def __del__(self):\n"
                 "        self.write(2, b'FINALIZER INTERRUPTED CLEANUP\\n')\n"
                 "        self.exit(0)\n"
                 "\n\n_seeded_atexit.register(_SeededFinalizer)\n",
                 "fault on the comparison child's error stream"),
                ("forged-terminator", "\n\nimport atexit as _seeded_atexit\nimport os as _seeded_os\n"
                 "print('[]')\nprint('done')\n"
                 "_seeded_atexit.register(_seeded_os._exit, 0)\n",
                 "no complete structured result after the child's cleanup"),
                # Merge train 2 QA r3: a finalizer that ends the child SILENTLY now runs before
                # the result (codex MEDIUM), and a cleanup fault under a replaced sys.stderr, a
                # silenced sys.unraisablehook or a replaced sys.excepthook is refused (claude
                # MINOR 2); each was a clean pass before.
                ("finalizer-silent-exit", "\n\nimport atexit as _seeded_atexit\nimport os as _seeded_os\n"
                 "\n\nclass _SeededSilentFinalizer:\n"
                 "    def __init__(self):\n"
                 "        self.cycle = self\n"
                 "        self.exit = _seeded_os._exit\n"
                 "\n    def __del__(self):\n"
                 "        self.exit(0)\n"
                 "\n\n_seeded_atexit.register(_SeededSilentFinalizer)\n",
                 "no complete structured result after the child's cleanup"),
                ("stderr-replaced-fault", "\n\nimport atexit as _seeded_atexit\nimport io as _seeded_io\n"
                 "import os as _seeded_os\nimport sys as _seeded_sys\n"
                 "_seeded_sys.stderr = _seeded_io.StringIO()\n"
                 "_seeded_atexit.register(_seeded_os.remove, '/nonexistent-aiqt-parity-selftest')\n",
                 "parity child: a fault in loaded code's cleanup"),
                ("unraisablehook-silenced-fault", "\n\nimport atexit as _seeded_atexit\n"
                 "import os as _seeded_os\nimport sys as _seeded_sys\n"
                 "_seeded_sys.unraisablehook = lambda unraisable: None\n"
                 "_seeded_atexit.register(_seeded_os.remove, '/nonexistent-aiqt-parity-selftest')\n",
                 "parity child: a fault in loaded code's cleanup"),
                ("excepthook-replaced", "\n\nimport sys as _seeded_sys\n"
                 "_seeded_sys.excepthook = lambda *args: None\n",
                 "parity child: a fault in loaded code's cleanup"),
                # Merge train 2 QA r4 (codex MEDIUM): a collected finalizer that creates NEW
                # cyclic cleanup work whose own finalizer faults -- one collection missed it and
                # the child passed -- and a finalizer chain no collection pass inside
                # _child_contract.GC_PASS_BOUND ends, which must refuse by name, never emit a result.
                ("finalizer-chained-fault", "\n\nimport atexit as _seeded_atexit\n"
                 "import os as _seeded_os\n"
                 "\n\nclass _SeededInner:\n"
                 "    def __init__(self):\n"
                 "        self.cycle = self\n"
                 "        self.remove = _seeded_os.remove\n"
                 "\n    def __del__(self):\n"
                 "        self.remove('/nonexistent-aiqt-parity-selftest-chain')\n"
                 "\n\nclass _SeededOuter:\n"
                 "    def __init__(self):\n"
                 "        self.cycle = self\n"
                 "        self.inner = _SeededInner\n"
                 "\n    def __del__(self):\n"
                 "        self.inner()\n"
                 "\n\n_seeded_atexit.register(_SeededOuter)\n",
                 # The bounded diagnostic shows the stream's head: the unraisable report of the
                 # Inner fault raised during the repeated collection (the child's own named
                 # "parity child: a fault ..." line follows past the bound).
                 "Exception ignored"),
                ("finalizer-unsettled-gc", "\n\nimport atexit as _seeded_atexit\n"
                 "\n\ndef _seeded_make_link(depth):\n"
                 "    class _SeededLink:\n"
                 "        def __init__(self, depth):\n"
                 "            self.cycle = self\n"
                 "            self.depth = depth\n"
                 "            self.make = _seeded_make_link\n"
                 "\n        def __del__(self):\n"
                 "            if self.depth > 0:\n"
                 "                self.make(self.depth - 1)\n"
                 "\n    _SeededLink(depth)\n"
                 "\n\n_seeded_atexit.register(_seeded_make_link, 12)\n",
                 "still freed or created objects after"),
                # Merge train 2 QA r5 (codex MEDIUM): an Outer finalizer that RESURRECTS its
                # object while building a cyclic Inner whose finalizer faults made the first pass
                # free nothing, and the r4 loop emitted a result over the Inner's pending cleanup;
                # automatic collection is disabled so the Outer is still pending at the record.
                ("finalizer-resurrecting-fault", "\n\nimport atexit as _seeded_atexit\n"
                 "import gc as _seeded_gc\nimport os as _seeded_os\n"
                 "_seeded_held = []\n"
                 "\n\nclass _SeededResurrectInner:\n"
                 "    def __init__(self):\n"
                 "        self.cycle = self\n"
                 "        self.remove = _seeded_os.remove\n"
                 "\n    def __del__(self):\n"
                 "        self.remove('/nonexistent-aiqt-parity-selftest-resurrect')\n"
                 "\n\nclass _SeededResurrectOuter:\n"
                 "    def __init__(self):\n"
                 "        self.cycle = self\n"
                 "        self.held = _seeded_held\n"
                 "        self.inner = _SeededResurrectInner\n"
                 "\n    def __del__(self):\n"
                 "        self.held.append(self)\n"
                 "        self.inner()\n"
                 "\n\ndef _seeded_setup():\n"
                 "    _seeded_gc.collect()\n"
                 "    _seeded_gc.disable()\n"
                 "    _SeededResurrectOuter()\n"
                 "\n\n_seeded_atexit.register(_seeded_setup)\n",
                 "Exception ignored"),
                # Merge train 2 QA r6 (codex MEDIUM): the r5 settle rule (frees nothing AND no
                # more objects tracked than before) still passed a resurrecting finalizer that
                # also RELEASES other tracked objects while creating the new faulting work; the
                # shared contract settles only on a pass whose tracked identity SET is unchanged.
                ("finalizer-net-release-fault", "\n\nimport atexit as _seeded_atexit\n"
                 "import gc as _seeded_gc\n"
                 "_seeded_net_held = []\n"
                 "\n\nclass _SeededNetInner:\n"
                 "    def __init__(self):\n"
                 "        self.cycle = self\n"
                 "\n    def __del__(self):\n"
                 "        raise RuntimeError('new cyclic cleanup failed')\n"
                 "\n\nclass _SeededNetOuter:\n"
                 "    def __init__(self):\n"
                 "        self.cycle = self\n"
                 "        self.held = _seeded_net_held\n"
                 "        self.spare = [[] for _ in range(100)]\n"
                 "        self.inner = _SeededNetInner\n"
                 "\n    def __del__(self):\n"
                 "        self.held.append(self)\n"
                 "        self.spare.clear()\n"
                 "        self.inner()\n"
                 "\n\ndef _seeded_net_setup():\n"
                 "    _seeded_gc.collect()\n"
                 "    _seeded_gc.disable()\n"
                 "    _SeededNetOuter()\n"
                 "\n\n_seeded_atexit.register(_seeded_net_setup)\n",
                 "Exception ignored"),
                # Merge train 2 QA r7 (codex MAJOR), the reproduction: a stdout wrapper whose
                # flush raises once written. The child writes the genuine result and terminator,
                # then the result callback's flush raises; the pinned contract's
                # `finally: os._exit(code)` swallowed it and the leg accepted exit 0.
                ("result-flush-fault", "\n\nimport sys as _seeded_sys\n"
                 "\n\nclass _SeededOutput:\n"
                 "    def __init__(self, stream):\n"
                 "        self.stream, self.written = stream, False\n"
                 "\n    def write(self, text):\n"
                 "        result = self.stream.write(text)\n"
                 "        self.written = True\n"
                 "        return result\n"
                 "\n    def flush(self):\n"
                 "        self.stream.flush()\n"
                 "        if self.written:\n"
                 "            raise RuntimeError('result flush failed')\n"
                 "\n\n_seeded_sys.stdout = _SeededOutput(_seeded_sys.stdout)\n",
                 "the record handler raised RuntimeError in the record callback"),
                # Merge train 2 QA r8 (codex MAJOR), the reproduction: the wrapper's flush,
                # once written, absorbs an unraisable fault (a finalizer raising during the
                # flush) under a hook it installs and puts back, so the callback returns
                # normally; the pinned contract kept its decided exit 0 over a complete result.
                ("result-flush-unraisable", "\n\nimport sys as _seeded_sys\n"
                 "\n\nclass _SeededRaiser:\n"
                 "    def __del__(self):\n"
                 "        raise RuntimeError('fault during result flush')\n"
                 "\n\nclass _SeededAbsorbingOutput:\n"
                 "    def __init__(self, stream):\n"
                 "        self.stream, self.written, self.log = stream, False, []\n"
                 "\n    def write(self, text):\n"
                 "        result = self.stream.write(text)\n"
                 "        self.written = True\n"
                 "        return result\n"
                 "\n    def flush(self):\n"
                 "        self.stream.flush()\n"
                 "        if self.written:\n"
                 "            saved = _seeded_sys.unraisablehook\n"
                 "            _seeded_sys.unraisablehook = self.log.append\n"
                 "            _SeededRaiser()\n"
                 "            _seeded_sys.unraisablehook = saved\n"
                 "\n\n_seeded_sys.stdout = _SeededAbsorbingOutput(_seeded_sys.stdout)\n",
                 "a fault was observed during the record callback"),
                # QA r8, the error-stream channel: a result write that writes to stderr is no
                # fault the child sees (it exits 0 with its result); the leg refuses the bytes.
                ("result-write-stderr", "\n\nimport sys as _seeded_sys\n"
                 "\n\nclass _SeededNoisyOutput:\n"
                 "    def __init__(self, stream):\n"
                 "        self.stream = stream\n"
                 "\n    def write(self, text):\n"
                 "        _seeded_sys.stderr.write('result write noise\\n')\n"
                 "        return self.stream.write(text)\n"
                 "\n    def flush(self):\n"
                 "        self.stream.flush()\n"
                 "\n\n_seeded_sys.stdout = _SeededNoisyOutput(_seeded_sys.stdout)\n",
                 "fault on the comparison child's error stream"),
                # Merge train 2 QA r9 (codex MAJOR), the reproductions: the wrapper's SECOND
                # flush once written (the contract re-check's own) replaces
                # threading.excepthook, or disables automatic collection and drops a cyclic
                # object whose finalizer raises; the pinned re-check flushed after its checks,
                # and the leg accepted exit 0 with a complete result.
                ("final-flush-replaces-hook", "\n\nimport sys as _seeded_sys\n"
                 "import threading as _seeded_threading\n"
                 "\n\nclass _SeededCountingOutput:\n"
                 "    def __init__(self, stream):\n"
                 "        self.stream, self.written, self.flushes = stream, False, 0\n"
                 "\n    def write(self, text):\n"
                 "        self.written = True\n"
                 "        return self.stream.write(text)\n"
                 "\n    def flush(self):\n"
                 "        self.stream.flush()\n"
                 "        if self.written:\n"
                 "            self.flushes += 1\n"
                 "            if self.flushes == 2:\n"
                 "                _seeded_threading.excepthook = lambda args: None\n"
                 "\n\n_seeded_sys.stdout = _SeededCountingOutput(_seeded_sys.stdout)\n",
                 "a fault was observed during the record callback"),
                ("final-flush-drops-cyclic-fault", "\n\nimport gc as _seeded_gc\n"
                 "import sys as _seeded_sys\n"
                 "\n\nclass _SeededCyclicRaiser:\n"
                 "    def __init__(self):\n"
                 "        self.cycle = self\n"
                 "\n    def __del__(self):\n"
                 "        raise RuntimeError('cyclic cleanup dropped by the final flush')\n"
                 "\n\nclass _SeededDroppingOutput:\n"
                 "    def __init__(self, stream):\n"
                 "        self.stream, self.written, self.flushes = stream, False, 0\n"
                 "\n    def write(self, text):\n"
                 "        self.written = True\n"
                 "        return self.stream.write(text)\n"
                 "\n    def flush(self):\n"
                 "        self.stream.flush()\n"
                 "        if self.written:\n"
                 "            self.flushes += 1\n"
                 "            if self.flushes == 2:\n"
                 "                _seeded_gc.disable()\n"
                 "                _SeededCyclicRaiser()\n"
                 "\n\n_seeded_sys.stdout = _SeededDroppingOutput(_seeded_sys.stdout)\n",
                 # The bounded diagnostic shows the stream's head: the finalizer's unraisable
                 # report, raised by the final check's collection (the contract's diagnostic
                 # follows).
                 "Exception ignored"),
        ):
            root = fresh("p-" + name)
            _patch(root / STOP_REL, "", "", append)
            findings, unver = run_gate(root, legs=("p",))
            if not any(needle in u for u in unver):
                failures.append("parity %s: expected a cannot-evaluate containing %r, got %s %s"
                                % (name, needle, findings, unver))
    except (GateError, OSError) as exc:
        failures.append("self-test setup failed: %s" % _bounded(exc))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if failures:
        print("SELF-TEST FAIL:")
        for failure in failures:
            print("  - " + failure)
        return 1
    print("SELF-TEST PASS: a clean copy passes legs (w), (k), (l), (d) and (p); a deny key, a block key, "
          "an honoured worker variable, an over-length line with a line break, a script returning 2 or "
          "calling os._exit(2), a silent clock script, a wrong clock shape, an elapsed segment where "
          "forbidden, a failing self-test, a substituted command, a direct python3 <file> launch, a "
          "launcher that skips the file, reports on stdout, takes the last argument, swallows a failure "
          "or exits 2 on an unreadable file, a changed timeout, matcher or extra key, an extra exit-2 entry "
          "(legs (w) and (l)) in each of seven spellings of the script path, an extra direct entry, a "
          "duplicated entry, a dual-present basename, a changed shared "
          "constant, function (either side), regex or _cfg, and a reintroduced _is_worker are each "
          "caught by their own check, and a missing pair file, a script with no fixture set, a seeded "
          "thread fault on the parity child's error stream, a seeded builtin cleanup fault (a "
          "frameless 'Exception ignored' over a zero exit), a seeded shutdown finalizer that "
          "writes and exits after the emitted result, a seeded forged two-line stdout cut off by "
          "a cleanup exit, a seeded finalizer that ends the child silently, a seeded cleanup "
          "fault under a replaced sys.stderr or a silenced sys.unraisablehook, a seeded replaced "
          "sys.excepthook, a seeded finalizer whose newly created cyclic cleanup work faults "
          "under the repeated collection, a seeded finalizer chain outlasting the bounded "
          "collection, a seeded resurrecting finalizer whose newly created cyclic cleanup work "
          "faults, the same with other tracked objects released alongside (the identity-set "
          "settle rule, merge train 2 QA r6), a seeded stdout whose flush raises after the "
          "result was written (QA r7), a seeded stdout whose flush absorbs an unraisable fault "
          "under a hook it puts back and one whose write writes to stderr (QA r8), a seeded "
          "stdout whose second flush (the final check's own) replaces threading.excepthook or "
          "drops a cyclic raising finalizer (QA r9), and a "
          "seeded cleanup exit that cuts "
          "off its shutdown-written result are cannot-evaluate (secfcl)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
