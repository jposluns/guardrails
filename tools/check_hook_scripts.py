#!/usr/bin/env python3
"""Hook-scripts gate: the pack's standalone hook scripts stay warn-only, environment-inert,
unduplicated, and in parity with the preview Stop hook. Offline, stdlib only, no git, fail-closed.

A standalone hook script is a [[hook]] row with a `script` key in .aiqt/core/hooks/manifest.toml. Its
source lives in .aiqt/core/hooks/scripts/ and tools/gen_hooks.py copies it byte-identical into
plugin/aiqt-guardrails-hooks/hooks/scripts/ (byte identity is that generator's --check, not this gate).
Every script the manifest names must have a fixture set in build_specs below, and every fixture set must
be in the manifest; a mismatch either way is a cannot-evaluate (exit 2), so a new script forces a
reviewed edit here. Five legs:

  (a) SELF-TEST. Each source script runs as [sys.executable, "-I", "-S", "-B", <path>, "--self-test"]
      under the run contract below. A nonzero exit is a finding.
  (w) WARN ONLY, through the rendered hooks.json. Each script's hooks.json entry must be exactly
      ["-I", "-S", "-B", "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/<file>"] under its manifest event and
      matcher, and the plugin copy is run that way on each fixture. Every run must exit 0, and stdout must
      be empty or one JSON object: for future-stamp-write.py only `systemMessage`, one line of at most 100
      characters; for clock-inject.py only `hookSpecificOutput` with exactly hookEventName and
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
  (d) DUPLICATES. A file name present both in .preview/ and in the manifest's script set is a finding:
      a reader could install both copies and run the check twice.
  (p) REPOSITORY PARITY. Code the pack copies share with .preview/stamp-truth-stop.py, and the `_cfg`
      the two pack copies share with each other, must be identical: `inspect.getsource` for functions,
      (pattern, flags) for compiled regexes, equality for constants. Each pair is compared in one child
      [sys.executable, "-I", "-S", "-B", <this file>, "--parity-child", ...] that loads both files with
      importlib. `_is_worker` and `_sibling_or_skip` must be absent from both pack copies. `_cfg` is
      excluded from the pairs with the Stop hook, because the preview Stop hook still reads the legacy
      spelling. A missing or unloadable pair file is a cannot-evaluate (exit 2), never a skip, so
      promoting or retiring the Stop hook forces a deliberate edit to PAIRS.

RUN CONTRACT for legs (a), (w) and (k): a fresh temporary working directory per run, stdin closed or
fed the fixture, every AIQT_, ORCH_ and CLAUDE_ variable and CDPATH removed, TZ=UTC, and per-run
temporary store and lease paths where a fixture needs them. A run that outlives its deadline is a
cannot-evaluate (exit 2).

DISCLOSED RESIDUALS (what this gate does not catch):
  - The fixtures are a fixed sample; a deny or block reached only by an input outside them is not seen.
    Leg (w) judges output shape, not whether a warning is correct.
  - Leg (a) judges a self-test by its exit status only.
  - Leg (k) tries the named variables only; another variable that changes behaviour is not found.
  - Leg (p) compares the listed names only; shared code outside the lists can drift.
  - The scripts run under this gate's interpreter (sys.executable), not the host's `python3` lookup.

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
    """Run one script under the contract; returns (rc, stdout bytes, stderr bytes). Raises GateError on
    a timeout or a launch failure."""
    work = tempfile.mkdtemp(prefix="aiqt-hook-scripts-")
    try:
        argv = [sys.executable, "-I", "-S", "-B", str(path)] + list(args)
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
    """[(script, event, matcher)] for every manifest row with a `script` key."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        import gen_hooks  # noqa: E402  the manifest loader and its validation
    finally:
        sys.path.pop(0)
    try:
        _plugin, hooks = gen_hooks.load_manifest(root / MANIFEST_REL)
    except (ValueError, OSError) as exc:
        raise GateError("%s: %s" % (MANIFEST_REL, _bounded(exc)))
    return [(h["script"], h["event"], h.get("matcher")) for h in hooks if "script" in h]


def check_rendered(root, rows, findings):
    """The hooks.json entry for each script row must be exactly the isolated launcher."""
    try:
        with open(root / HOOKS_JSON_REL, encoding="utf-8") as fh:
            rendered = json.load(fh)
        events = rendered["hooks"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise GateError("%s: %s" % (HOOKS_JSON_REL, _bounded(exc)))
    for script, event, matcher in rows:
        want = ["-I", "-S", "-B", "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/" + script]
        found = []
        for ev, groups in events.items():
            for group in groups:
                for hook in group.get("hooks", []):
                    args = hook.get("args") or []
                    if args and str(args[-1]).endswith("/hooks/scripts/" + script):
                        found.append((ev, group.get("matcher"), args))
        if (event, matcher, want) not in found:
            findings.append("(w) %s: no hooks.json entry %s %r with args %s (found %s)"
                            % (script, event, matcher, want, found))


def leg_selftest(root, scripts, findings):
    for script in scripts:
        rc, out, err = run_script(root / SRC_REL / script, ["--self-test"], None, dict(), SELFTEST_TIMEOUT)
        if rc != 0:
            tail = (out + err).decode("utf-8", "replace").rstrip().splitlines()[-10:]
            findings.append("(a) %s: --self-test exited %s:\n      %s" % (script, rc, "\n      ".join(tail)))
        else:
            print("  (a) %s: --self-test exit 0" % script)


def leg_warn_and_inert(root, specs, scripts, findings):
    for script in scripts:
        spec = specs[script]
        path = root / PLUGIN_REL / "hooks" / "scripts" / script
        if not path.is_file():
            raise GateError("%s: the plugin copy is absent (run tools/gen_hooks.py)" % path)
        nonsilent = False
        for fixture in spec["fixtures"]:
            label = "%s %s" % (script, fixture["name"])
            rc, out, err = run_script(path, [], fixture["payload"], fixture["env"], RUN_TIMEOUT)
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
                vrc, vout, verr = run_script(path, [], fixture["payload"], env, RUN_TIMEOUT)
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
        try:
            diffs = json.loads(res.stdout.decode("utf-8"))
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
    """Child mode: load both files, print the JSON list of names that differ. Exit 2 if a file cannot be
    loaded."""
    path_a, path_b, funcs, regexes, consts = json.loads(spec)
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
    print(json.dumps(diffs))
    return 0


def run_gate(root, legs=("a", "w", "d", "p"), only=None):
    """(findings, unverifiable) for the tree at root. `only` restricts legs (a) and (w)(k) to scripts."""
    findings = []
    try:
        rows = load_script_rows(root)
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
                check_rendered(root, rows, findings)
                leg_warn_and_inert(root, specs, chosen, findings)
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
    print("PASS: hook scripts are warn-only, inert to the worker and legacy variables tried, "
          "unduplicated, and in parity")
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
# Seeded faults against a scratch copy of the real files. Each must be caught: a deny key, a block key,
# an honoured worker variable, an over-length line, a failing self-test, a basename present in .preview/
# too, and for leg (p): a one-character change to a shared constant, a changed shared function on either
# side, a changed regex, a drifted _cfg, _is_worker reintroduced into a pack copy, and a missing pair
# file (exit 2). The unmodified copy must pass legs (w), (k), (d) and (p).

_FAULT_HEAD = "def main(argv):\n"


def _fault(body):
    """Source that replaces main() with `body` (an indented block that may call _orig_main)."""
    return "def main(argv):\n" + body + "\n\n\ndef _orig_main(argv):\n"


_FAULTS = (
    ("deny", RECORD, "w", "permissionDecision",
     _fault("    print(json.dumps(dict(hookSpecificOutput=dict(hookEventName='PreToolUse', "
            "permissionDecision='deny'))))\n    return 0")),
    ("block", CLOCK, "w", "'decision'",
     _fault("    print(json.dumps(dict(decision='block', reason='x')))\n    return 0")),
    ("worker", RECORD, "w", "AIQT_HOOKS_WORKER=1 changes",
     _fault("    if os.environ.get('AIQT_HOOKS_WORKER') == '1':\n        return 0\n    return _orig_main(argv)")),
    ("overlong", RECORD, "w", "characters (at most",
     _fault("    print(json.dumps(dict(systemMessage='x' * 150)))\n    return 0")),
    ("selftest", CLOCK, "a", "--self-test exited 1",
     _fault("    if argv[1:] == ['--self-test']:\n        return 1\n    return _orig_main(argv)")),
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

        findings, unver = run_gate(fresh("clean"), legs=("w", "d", "p"))
        if findings or unver:
            failures.append("clean copy: expected no finding, got %s %s" % (findings, unver))

        for name, script, leg, needle, src in _FAULTS:
            root = fresh(name)
            target = root / (SRC_REL if leg == "a" else PLUGIN_REL + "/hooks/scripts") / script
            _patch(target, _FAULT_HEAD, src)
            findings, unver = run_gate(root, legs=(leg,), only=(script,))
            if unver or not any(needle in f for f in findings):
                failures.append("fault %s: expected a finding containing %r, got %s %s"
                                % (name, needle, findings[:3], unver))

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
    except (GateError, OSError) as exc:
        failures.append("self-test setup failed: %s" % _bounded(exc))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if failures:
        print("SELF-TEST FAIL:")
        for failure in failures:
            print("  - " + failure)
        return 1
    print("SELF-TEST PASS: a clean copy passes legs (w), (k), (d) and (p); a deny key, a block key, an "
          "honoured worker variable, an over-length line, a failing self-test, a dual-present basename, "
          "a changed shared constant, function (either side), regex or _cfg, and a reintroduced _is_worker "
          "are each caught, and a missing pair file is a cannot-evaluate")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
