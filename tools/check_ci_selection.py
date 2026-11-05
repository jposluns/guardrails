#!/usr/bin/env python3
"""Declaration gate for tools/ci_checks.toml, the registry of every run: step in .github/workflows/quality.yml.

  check_ci_selection.py              exit 0 clean, 1 finding, 2 cannot evaluate
  check_ci_selection.py --self-test  assert every rule over in-memory fixtures, then the live tree
  check_ci_selection.py [--self-test] --execution-report ABS_PATH
  any other argument list is refused with exit 2

The registry declares, for each run: step (named by its job id and its exact step name), the step's
categories, its scope, the trigger globs whose change would select it, whether a person has reviewed the
declaration, and an optional weekly-only flag with the reference to the maintainer authorization that
allows it. This gate only checks the declarations; nothing selects or skips a step from them yet.

A finding (exit 1), each printed with its rule id:
  missing-entry          a run: step in quality.yml has no entry
  unknown-step           an entry names no run: step in quality.yml
  duplicate-entry        two entries name one step
  bad-field              a field is missing, of the wrong type, unknown, or outside its enum
  empty-triggers         triggers are empty for a scope that needs them (all but SCOPES_WITHOUT_TRIGGERS)
  dead-glob              a trigger glob matches no tracked file
  import-not-triggered   a script the step runs, or a repository module in its static import closure,
                         matches none of the entry's triggers
  weekly-only-unauthorized  weekly-only = true with no weekly-only-authorization reference
  undetermined-over-ceiling more entries have scope "undetermined" than UNDETERMINED_CEILING

Globs match tracked paths segment by segment: `**` spans any number of segments, `*` and `?` stay inside
one segment. The import closure is static: from each repository .py path the step's run: text names, every
`import X` or `from X import ...` (absolute, first name component) that resolves to a tracked X.py in the
importing file's own directory (sys.path[0] under `python3 -I`), followed transitively. A module loaded
through importlib, a sys.path entry added at run time, or a script launched as a subprocess is outside it.

Cannot evaluate (exit 2), never a pass: a registry or workflow that is missing, not a regular file,
unreadable, over MAX_BYTES or not UTF-8; a registry that is not TOML (TOMLDecodeError or a bare
ValueError), nests too deeply (RecursionError) or exhausts memory (MemoryError); a format-version other
than the integer 1; a workflow shape the line reader does not follow, a run: step without a name, or one
job holding two run: steps of one name; a relative .py path in run: text that names no tracked file; a
tracked-file listing that fails or is empty; a source in an import closure that cannot be read or parsed.

The workflow is read by a line reader, not a YAML parser, so the gate stays standard-library only: it
follows the layout quality.yml uses (jobs at two spaces, job keys at four, steps under `steps:` at six,
step keys at eight, a name or run: value on one line or a run: block under |, |-, > or >-) and refuses
any other, including a flow or compact steps list, a job body at another indent, a repeated job key or
jobs: block, a one-line name or run: value continued on a deeper line, and a value that starts with a
YAML indicator (anchor, alias, tag, other block header).
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: check_ci_selection.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import ast
import os
import posixpath
import re
import stat
import subprocess
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # not a version problem: every Python 3.14 ships tomllib
    import os
    try:
        sys.stderr.write(
            "error: check_ci_selection.py cannot import tomllib, part of the Python standard library; "
            "this installation is incomplete. Nothing was run (cannot evaluate).\n")
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = "tools/ci_checks.toml"
WORKFLOW = ".github/workflows/quality.yml"
MAX_BYTES = 4 * 1024 * 1024

CATEGORIES = ("ci", "secrets", "site", "opf", "opf-closure", "corpus", "generated", "release", "records",
              "hooks", "git-env", "python-host", "platform", "probe")
SCOPES = ("per-file", "sub-tree", "whole-repo", "history", "self-test", "probe", "setup", "aggregator",
          "undetermined")
# A setup step and the aggregator check no repository input, and an undetermined step runs on every
# change until its declaration is written by hand, so these three may carry no triggers.
SCOPES_WITHOUT_TRIGGERS = ("setup", "aggregator", "undetermined")
# The most entries that may stay undetermined; raising it is a reviewed edit to this gate.
UNDETERMINED_CEILING = 1

REQUIRED_FIELDS = {"job": str, "step": str, "categories": list, "scope": str, "triggers": list, "reviewed": bool}
OPTIONAL_FIELDS = {"weekly-only": bool, "weekly-only-authorization": str}

JOB_RE = re.compile(r"  ([A-Za-z0-9_-]+):")
KEY_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_-]*):(?: +(.*))?")
PY_PATH_RE = re.compile(r"(?<![A-Za-z0-9_./-])[A-Za-z0-9_.][A-Za-z0-9_./-]*\.py(?![A-Za-z0-9_./-])")
# The first characters that make a plain YAML scalar something other than text (or reserved).
PLAIN_INDICATORS = "|>&*!%@`{}[],#?:'\""
BLOCK_MARKERS = ("|", "|-", ">", ">-")


class CannotEvaluate(Exception):
    """An input that cannot be judged: unreadable, oversized, not UTF-8, not TOML, or not in a shape read."""


def read_text(path, where):
    """The UTF-8 text of the regular file at path, at most MAX_BYTES; CannotEvaluate otherwise."""
    try:
        mode = os.lstat(path).st_mode
    except OSError as exc:
        raise CannotEvaluate("{}: cannot stat ({})".format(where, exc)) from None
    if not stat.S_ISREG(mode):
        raise CannotEvaluate("{}: not a regular file".format(where))
    try:
        with open(path, "rb") as handle:
            data = handle.read(MAX_BYTES + 1)
    except OSError as exc:
        raise CannotEvaluate("{}: unreadable ({})".format(where, exc)) from None
    if len(data) > MAX_BYTES:
        raise CannotEvaluate("{}: larger than {} bytes".format(where, MAX_BYTES))
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CannotEvaluate("{}: not UTF-8 ({})".format(where, exc)) from None


def load_registry(text, where):
    """The parsed registry table; every parse failure is CannotEvaluate, never an empty registry."""
    # ValueError and RecursionError too: tomllib raises a BARE ValueError (not TOMLDecodeError) on an
    # integer literal past CPython's 4300-digit int-string limit, and a RecursionError on a deeply nested
    # array or inline table; neither is a TOMLDecodeError.
    try:
        data = tomllib.loads(text)
    except (tomllib.TOMLDecodeError, ValueError, RecursionError, MemoryError) as exc:
        raise CannotEvaluate("{}: is not valid TOML: {}: {}".format(where, type(exc).__name__, exc)) from None
    # type() first: True == 1 and 1.0 == 1 in Python, and neither is the integer 1 TOML must carry.
    if type(data.get("format-version")) is not int or data["format-version"] != 1:
        raise CannotEvaluate("{}: format-version is not 1".format(where))
    unknown = sorted(set(data) - {"format-version", "check"})
    if unknown:
        raise CannotEvaluate("{}: unknown top-level keys {}".format(where, unknown))
    checks = data.get("check", [])
    if not isinstance(checks, list) or not all(isinstance(entry, dict) for entry in checks):
        raise CannotEvaluate("{}: check is not an array of tables".format(where))
    return checks


def _scalar(value, where):
    """A one-line YAML scalar: plain (cut at a ' #' comment), single-quoted or double-quoted without escapes.
    An empty value, a tab, a plain value that starts with a YAML indicator (block scalar, anchor, alias,
    tag, flow collection) or holds a mapping indicator, and a quote left inside a quoted value are refused,
    since YAML reads each of them as something other than the literal text on the line."""
    if "\t" in value:
        raise CannotEvaluate("{}: tab in a scalar".format(where))
    value = value.strip()
    if value.startswith("'"):
        if len(value) < 2 or not value.endswith("'") or "'" in value[1:-1].replace("''", ""):
            raise CannotEvaluate("{}: single-quoted scalar not read by this gate".format(where))
        return value[1:-1].replace("''", "'")
    if value.startswith('"'):
        if len(value) < 2 or not value.endswith('"') or "\\" in value or '"' in value[1:-1]:
            raise CannotEvaluate("{}: double-quoted scalar not read by this gate".format(where))
        return value[1:-1]
    value = value.split(" #", 1)[0].rstrip()
    if (not value or value[0] in PLAIN_INDICATORS or value == "-" or value.startswith("- ")
            or ": " in value or value.endswith(":")):
        raise CannotEvaluate("{}: scalar {!r} not read by this gate".format(where, value))
    return value


def workflow_run_steps(text, where=WORKFLOW):
    """The run: steps of a workflow as (job, step name, run text, line), in file order."""
    lines = text.split("\n")
    steps, current = [], None
    in_jobs, job, in_steps = False, None, False
    # seen_jobs refuses a second jobs: block; job_keys refuses a repeated job key (YAML keeps the last
    # of either, which this reader would not); last_key is the step key a deeper line would continue.
    seen_jobs, job_keys, last_key = False, None, None

    def flush():
        # The step's own bookkeeping keys start with a space, so no YAML key can collide with them.
        if current is not None and "run" in current:
            if not current.get("name"):
                raise CannotEvaluate("{}:{}: run: step without a name".format(where, current[" line"]))
            steps.append((current[" job"], current["name"], current["run"], current[" line"]))

    index = 0
    while index < len(lines):
        line = lines[index]
        number = index + 1
        index += 1
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "\t" in line[:len(line) - len(line.lstrip())]:
            raise CannotEvaluate("{}:{}: tab in indentation".format(where, number))
        indent = len(line) - len(line.lstrip(" "))
        if indent == 0:
            flush()
            current, job, in_steps = None, None, False
            head = line.split(" #", 1)[0].rstrip()
            match = KEY_RE.fullmatch(head)
            if match is None or (match.group(1) == "jobs" and (head != "jobs:" or seen_jobs)):
                raise CannotEvaluate("{}:{}: top-level line not read by this gate".format(where, number))
            in_jobs = head == "jobs:"
            seen_jobs = seen_jobs or in_jobs
            continue
        if not in_jobs:
            continue
        if indent in (1, 3):
            raise CannotEvaluate("{}:{}: indentation not read by this gate".format(where, number))
        if indent == 2:
            flush()
            current, in_steps, job_keys = None, False, set()
            match = JOB_RE.fullmatch(line.split(" #", 1)[0].rstrip())
            if match is None:
                raise CannotEvaluate("{}:{}: job key not read by this gate".format(where, number))
            job = match.group(1)
            continue
        if job is None:
            raise CannotEvaluate("{}:{}: content under jobs: before any job".format(where, number))
        if not job_keys and indent != 4:
            raise CannotEvaluate("{}:{}: job {} body not at indent 4".format(where, number, job))
        if indent == 4:
            flush()
            current = None
            # A job key is a plain key, with at most a comment after a bare steps: (YAML reads
            # `steps: # note` as `steps:`); a flow list, a compact list at indent 4 or a quoted key is refused.
            match = KEY_RE.fullmatch(stripped.split(" #", 1)[0].rstrip())
            if match is None or (match.group(1) == "steps" and match.group(2) is not None):
                raise CannotEvaluate("{}:{}: job key not read by this gate".format(where, number))
            if match.group(1) in job_keys:
                raise CannotEvaluate("{}:{}: job {} gives {} twice".format(where, number, job, match.group(1)))
            job_keys.add(match.group(1))
            in_steps = match.group(1) == "steps"
            continue
        if not in_steps:
            continue
        if indent > 8:
            # Deeper lines belong to a nested key (with:, env:) and are skipped; under a one-line name or
            # run value they would continue that scalar, so the value read would be wrong.
            if current is None or last_key in ("name", "run"):
                raise CannotEvaluate("{}:{}: continued step {} not read by this gate".format(where, number, last_key))
            continue
        if indent == 6 and stripped.startswith("- "):
            flush()
            current = {" job": job, " line": number}
            body = stripped[2:]
        elif indent == 8 and current is not None:
            body = stripped
        else:
            raise CannotEvaluate("{}:{}: step layout not read by this gate".format(where, number))
        match = KEY_RE.fullmatch(body)
        if match is None:
            raise CannotEvaluate("{}:{}: step key not read by this gate".format(where, number))
        key, value = match.group(1), match.group(2) or ""
        if key in current:
            raise CannotEvaluate("{}:{}: step key {} given twice".format(where, number, key))
        last_key = key
        if key == "name":
            current["name"] = _scalar(value, "{}:{}".format(where, number))
        elif key == "run":
            if value.strip() in BLOCK_MARKERS:
                block = []
                while index < len(lines) and (not lines[index].strip()
                                              or len(lines[index]) - len(lines[index].lstrip(" ")) > 8):
                    block.append(lines[index].strip())
                    index += 1
                current["run"] = "\n".join(block).strip()
            else:
                current["run"] = _scalar(value, "{}:{}".format(where, number))
        else:
            current[key] = value
    flush()
    if not steps:
        raise CannotEvaluate("{}: no run: steps found".format(where))
    seen = set()
    for step_job, name, _run, number in steps:
        if (step_job, name) in seen:
            raise CannotEvaluate("{}:{}: job {} holds two run: steps named {!r}".format(where, number, step_job, name))
        seen.add((step_job, name))
    return steps


def glob_match(glob, path):
    """Whether path fully matches a segment glob (`**` spans segments; `*` and `?` stay in one).
    The match tracks the set of reachable path segments and matches one segment without backtracking,
    so its time stays polynomial in the glob and path lengths; a backtracking regex is exponential on a
    glob with many wildcards."""
    names = path.split("/")
    parts = glob.split("/")
    reach = {0}
    for position, part in enumerate(parts):
        if part == "**":
            if position == len(parts) - 1:
                # A trailing ** takes every remaining segment, at least one.
                return any(start < len(names) for start in reach)
            # ** before another segment takes zero or more whole nonempty segments.
            nxt = set()
            for start in reach:
                end = start
                while True:
                    nxt.add(end)
                    if end >= len(names) or not names[end]:
                        break
                    end += 1
            reach = nxt
        else:
            reach = {start + 1 for start in reach if start < len(names) and _segment_match(part, names[start])}
        if not reach:
            return False
    return len(names) in reach


def _segment_match(part, name):
    """Whether one path segment matches one glob segment of `*` and `?`: the greedy match that returns
    to the last `*` on a mismatch, O(len(part) * len(name))."""
    p = n = 0
    star, mark = -1, 0
    while n < len(name):
        if p < len(part) and part[p] == "*":
            star, mark = p, n
            p += 1
        elif p < len(part) and (part[p] == "?" or part[p] == name[n]):
            p += 1
            n += 1
        elif star >= 0:
            p, mark = star + 1, mark + 1
            n = mark
        else:
            return False
    while p < len(part) and part[p] == "*":
        p += 1
    return p == len(part)


def glob_shape_error(glob):
    """Why a trigger string is not a repository-relative glob, or None."""
    if not glob or glob.startswith("/") or "\\" in glob:
        return "not a repository-relative glob"
    if any(segment in ("", ".", "..") for segment in glob.split("/")):
        return "has an empty, '.' or '..' segment"
    if any(ord(char) < 0x21 or ord(char) == 0x7F for char in glob):
        return "carries a space or control character"
    return None


def run_scripts(run, tracked, where):
    """The tracked .py paths a step's run text names, with `./` and `..` collapsed. A relative .py path
    that names no tracked file is CannotEvaluate: its import closure could not be followed."""
    found = set()
    for token in PY_PATH_RE.findall(run):
        path = posixpath.normpath(token)
        if path not in tracked:
            raise CannotEvaluate("{}: run text names {}, which is not a tracked file".format(where, token))
        found.add(path)
    return sorted(found)


def import_closure(primary, tracked, read_source, cache=None):
    """primary plus every tracked module its static imports reach in the importing file's own directory.
    cache maps a path to its direct tracked imports, so a module shared by many steps is parsed once."""
    cache = {} if cache is None else cache
    seen, pending = set(), [primary]
    while pending:
        rel = pending.pop()
        if rel in seen:
            continue
        seen.add(rel)
        if rel not in cache:
            cache[rel] = _direct_imports(rel, tracked, read_source)
        pending.extend(path for path in cache[rel] if path not in seen)
    return seen


def _direct_imports(rel, tracked, read_source):
    """The tracked modules rel imports directly from its own directory."""
    found = set()
    source = read_source(rel)
    try:
        tree = ast.parse(source, filename=rel)
    except (SyntaxError, ValueError, RecursionError, MemoryError) as exc:
        raise CannotEvaluate("{}: does not parse ({}: {})".format(rel, type(exc).__name__, exc)) from None
    base = rel.rpartition("/")[0]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names = [node.module]
        else:
            continue
        for name in names:
            candidate = (base + "/" if base else "") + name.split(".")[0] + ".py"
            if candidate in tracked:
                found.add(candidate)
    return found


def _entry_findings(index, entry):
    """(bad-field findings, usable) for one registry entry; usable entries take part in the other rules."""
    findings = []
    label = "check #{}".format(index + 1)
    if isinstance(entry.get("job"), str) and isinstance(entry.get("step"), str):
        label = "check #{} ({} / {})".format(index + 1, entry["job"], entry["step"])
    for key, kind in REQUIRED_FIELDS.items():
        if key not in entry:
            findings.append(("bad-field", "{}: missing field {}".format(label, key)))
        elif not isinstance(entry[key], kind):
            findings.append(("bad-field", "{}: field {} is not a {}".format(label, key, kind.__name__)))
    for key, kind in OPTIONAL_FIELDS.items():
        if key in entry and not isinstance(entry[key], kind):
            findings.append(("bad-field", "{}: field {} is not a {}".format(label, key, kind.__name__)))
    for key in sorted(set(entry) - set(REQUIRED_FIELDS) - set(OPTIONAL_FIELDS)):
        findings.append(("bad-field", "{}: unknown field {}".format(label, key)))
    for key in ("job", "step"):
        if isinstance(entry.get(key), str) and not entry[key].strip():
            findings.append(("bad-field", "{}: field {} is empty".format(label, key)))
    if isinstance(entry.get("scope"), str) and entry["scope"] not in SCOPES:
        findings.append(("bad-field", "{}: scope {!r} is outside {}".format(label, entry["scope"], SCOPES)))
    categories = entry.get("categories")
    if isinstance(categories, list):
        if not categories:
            findings.append(("bad-field", "{}: categories is empty".format(label)))
        for category in categories:
            if not isinstance(category, str) or category not in CATEGORIES:
                findings.append(("bad-field", "{}: category {!r} is outside {}".format(label, category, CATEGORIES)))
        if len(set(map(repr, categories))) != len(categories):
            findings.append(("bad-field", "{}: categories repeat".format(label)))
    triggers = entry.get("triggers")
    if isinstance(triggers, list):
        for glob in triggers:
            reason = glob_shape_error(glob) if isinstance(glob, str) else "is not a string"
            if reason is not None:
                findings.append(("bad-field", "{}: trigger {!r} {}".format(label, glob, reason)))
        if len(set(map(repr, triggers))) != len(triggers):
            findings.append(("bad-field", "{}: triggers repeat".format(label)))
    authorization = entry.get("weekly-only-authorization")
    if isinstance(authorization, str) and not authorization.strip():
        findings.append(("bad-field", "{}: weekly-only-authorization is empty".format(label)))
    if "weekly-only-authorization" in entry and entry.get("weekly-only") is not True:
        findings.append(("bad-field", "{}: weekly-only-authorization without weekly-only = true".format(label)))
    return findings, label, not findings


def evaluate(workflow_text, registry_text, tracked, read_source):
    """Every (rule id, message) finding of the registry against the workflow and the tracked paths."""
    steps = workflow_run_steps(workflow_text)
    checks = load_registry(registry_text, REGISTRY)
    tracked = frozenset(tracked)
    if not tracked:
        raise CannotEvaluate("the tracked-file set is empty; an empty tree is never assumed")
    findings = []
    by_step = {}
    for index, entry in enumerate(checks):
        entry_findings, label, usable = _entry_findings(index, entry)
        findings.extend(entry_findings)
        if isinstance(entry.get("job"), str) and isinstance(entry.get("step"), str):
            by_step.setdefault((entry["job"], entry["step"]), []).append((index, entry, label, usable))
    runs = {(job, name): (run, number) for job, name, run, number in steps}
    for job, name, _run, number in steps:
        if (job, name) not in by_step:
            findings.append(("missing-entry", "{}:{}: run: step {} / {} has no entry".format(
                WORKFLOW, number, job, name)))
    for key, rows in sorted(by_step.items()):
        if key not in runs:
            for _index, _entry, label, _usable in rows:
                findings.append(("unknown-step", "{}: names no run: step in {}".format(label, WORKFLOW)))
        if len(rows) > 1:
            findings.append(("duplicate-entry", "{} / {}: named by {} entries ({})".format(
                key[0], key[1], len(rows), ", ".join("#{}".format(row[0] + 1) for row in rows))))
    undetermined = 0
    live, closures, imports = {}, {}, {}
    for key, rows in sorted(by_step.items()):
        for _index, entry, label, usable in rows:
            if not usable:
                continue
            triggers = entry["triggers"]
            scope = entry["scope"]
            if scope == "undetermined":
                undetermined += 1
            if not triggers and scope not in SCOPES_WITHOUT_TRIGGERS:
                findings.append(("empty-triggers", "{}: scope {} needs triggers".format(label, scope)))
            for glob in triggers:
                if glob not in live:
                    # A glob with no wildcard names one path, so set membership decides it.
                    live[glob] = (glob in tracked if "*" not in glob and "?" not in glob
                                  else any(glob_match(glob, path) for path in tracked))
                if not live[glob]:
                    findings.append(("dead-glob", "{}: trigger {!r} matches no tracked file".format(label, glob)))
            if entry.get("weekly-only") is True and not entry.get("weekly-only-authorization"):
                findings.append(("weekly-only-unauthorized",
                                 "{}: weekly-only = true has no weekly-only-authorization reference".format(label)))
            if key not in runs:
                continue
            primaries = run_scripts(runs[key][0], tracked, "{}:{}".format(WORKFLOW, runs[key][1]))
            closure = set()
            for primary in primaries:
                if primary not in closures:
                    closures[primary] = import_closure(primary, tracked, read_source, imports)
                closure |= closures[primary]
            for path in sorted(closure):
                if not any(glob_match(glob, path) for glob in triggers):
                    findings.append(("import-not-triggered",
                                     "{}: {} is in the check's import closure but matches no trigger".format(
                                         label, path)))
    if undetermined > UNDETERMINED_CEILING:
        findings.append(("undetermined-over-ceiling", "{} entries are undetermined; the ceiling is {}".format(
            undetermined, UNDETERMINED_CEILING)))
    return findings


def tracked_files(root):
    """The tracked relpaths under root from `git ls-files -z --cached`, pinned like check_internal_names'
    read: the GIT_* environment is dropped, core.fsmonitor=false, protocol.allow=never and
    GIT_NO_LAZY_FETCH=1. No git, a nonzero exit, a timeout, an empty listing or an undecodable path is
    CannotEvaluate, never an empty tree."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["GIT_NO_LAZY_FETCH"] = "1"
    try:
        proc = subprocess.run(["git", "-C", str(root), "-c", "core.fsmonitor=false", "-c", "protocol.allow=never",
                               "ls-files", "-z", "--cached"], capture_output=True, timeout=120, env=env)
    except (OSError, subprocess.SubprocessError) as exc:
        raise CannotEvaluate("cannot list the tracked files ({})".format(exc)) from None
    if proc.returncode != 0:
        raise CannotEvaluate("git ls-files failed (exit {}): {}".format(
            proc.returncode, proc.stderr.decode("utf-8", "replace").strip()))
    try:
        rels = {record.decode("utf-8") for record in proc.stdout.split(b"\x00") if record}
    except UnicodeDecodeError as exc:
        raise CannotEvaluate("git ls-files listed a path that is not UTF-8 ({})".format(exc)) from None
    if not rels:
        raise CannotEvaluate("git ls-files listed no tracked files; an empty tree is never assumed")
    return rels


def run_gate(root):
    root = Path(root)
    try:
        findings = evaluate(read_text(root / WORKFLOW, WORKFLOW), read_text(root / REGISTRY, REGISTRY),
                            tracked_files(root), lambda rel: read_text(root / rel, rel))
    except CannotEvaluate as exc:
        print("CANNOT EVALUATE: {}".format(exc), file=sys.stderr)
        return 2
    for rule, message in findings:
        print("FAIL [{}]: {}".format(rule, message))
    if findings:
        print("RESULT: FAIL: {} finding(s) in {}".format(len(findings), REGISTRY))
        return 1
    print("PASS: {} declares every run: step of {} exactly once, with valid fields, live triggers and "
          "covered import closures".format(REGISTRY, WORKFLOW))
    return 0


# The registered execution-suite identity (tools/selftest_checks.toml) and the executed-check-id record
# the instrumented check() helper appends to; the execution gate reconciles the report against the
# registry.
SUITE_ID = "ci-selection-selftest"
EXECUTED = []
FAILURES = []


def check(name, ok, detail=""):
    if name in EXECUTED:
        print("SELF-TEST HARNESS ERROR: duplicate check id {!r}".format(name), file=sys.stderr)
        sys.exit(2)
    EXECUTED.append(name)
    if not ok:
        FAILURES.append("{}: {!r}".format(name, detail))


FIXTURE_WORKFLOW = (
    "name: Fixture\n"
    "on:\n"
    "  pull_request:\n"
    "jobs:\n"
    "  lint:\n"
    "    name: Lint\n"
    "    needs:\n"
    "      - setup\n"
    "    steps:\n"
    "      - uses: actions/checkout@v4\n"
    "        with:\n"
    "          fetch-depth: 0\n"
    "      - name: Probe\n"
    "        run: python3 -I -B tools/probe.py\n"
    "      - name: 'Gate self-test'\n"
    "        shell: bash\n"
    "        run: python3 -I -B tools/gate.py --self-test\n"
    "  sum:\n"
    "    steps:\n"
    "      - name: Hostile setting\n"
    "        run: |\n"
    "          git config --global core.autocrlf true\n"
    "      - name: Aggregate\n"
    "        run: echo done\n")
FIXTURE_TRACKED = ("tools/probe.py", "tools/gate.py", "tools/_helper.py", "tools/_deep.py", "docs/a.md",
                   "docs/sub/b.md")
FIXTURE_SOURCES = {
    "tools/probe.py": "import sys\n",
    "tools/gate.py": "import os\nfrom _helper import thing\nimport json.decoder\n",
    "tools/_helper.py": "import _deep\nthing = 1\n",
    "tools/_deep.py": "x = 1\n",
}


def _entry(job, step, scope, triggers, categories=("ci",), extra=""):
    return ("[[check]]\njob = {!r}\nstep = {!r}\ncategories = [{}]\nscope = {!r}\ntriggers = [{}]\n"
            "reviewed = false\n{}\n").format(
        job, step, ", ".join(repr(c) for c in categories), scope, ", ".join(repr(t) for t in triggers),
        extra).replace("'", '"')


FIXTURE_ENTRIES = {
    "probe": ("lint", "Probe", "probe", ["tools/probe.py"]),
    "gate": ("lint", "Gate self-test", "self-test", ["tools/gate.py", "tools/_helper.py", "tools/_deep.py"]),
    "setup": ("sum", "Hostile setting", "setup", []),
    "aggregate": ("sum", "Aggregate", "aggregator", []),
}


def _registry(changes=None, extra=""):
    """A fixture registry: FIXTURE_ENTRIES with changes applied (a key mapped to None drops that entry)."""
    entries = dict(FIXTURE_ENTRIES)
    entries.update(changes or {})
    body = "".join(_entry(*row) for row in entries.values() if row is not None)
    return "format-version = 1\n\n" + body + extra


def _rules(registry, workflow=FIXTURE_WORKFLOW, tracked=FIXTURE_TRACKED, sources=None):
    """The sorted rule ids evaluate() reports, or ('cannot-evaluate', message)."""
    sources = FIXTURE_SOURCES if sources is None else sources

    def read_source(rel):
        if rel not in sources:
            raise CannotEvaluate("{}: unreadable (fixture)".format(rel))
        return sources[rel]

    try:
        return sorted(rule for rule, _message in evaluate(workflow, registry, tracked, read_source))
    except CannotEvaluate as exc:
        return ("cannot-evaluate", str(exc))


def _self_test():
    check("pass/fixture-clean", _rules(_registry()) == [], _rules(_registry()))

    steps = workflow_run_steps(FIXTURE_WORKFLOW)
    check("workflow/reads-run-steps-only", [(job, name) for job, name, _run, _line in steps] == [
        ("lint", "Probe"), ("lint", "Gate self-test"), ("sum", "Hostile setting"), ("sum", "Aggregate")], steps)
    check("workflow/block-run-text", steps[2][2] == "git config --global core.autocrlf true", steps[2])

    got = _rules(_registry({"probe": None}))
    check("refuse/missing-entry", got == ["missing-entry"], got)
    got = _rules(_registry(extra=_entry("lint", "Gone", "probe", ["tools/probe.py"])))
    check("refuse/unknown-step", got == ["unknown-step"], got)
    got = _rules(_registry(extra=_entry(*FIXTURE_ENTRIES["probe"])))
    check("refuse/duplicate-entry", got == ["duplicate-entry"], got)

    got = _rules(_registry({"probe": ("lint", "Probe", "everything", ["tools/probe.py"])}))
    check("refuse/scope-outside-enum", got == ["bad-field"], got)
    got = _rules(_registry({"probe": ("lint", "Probe", "probe", ["tools/probe.py"], ("ci", "misc"))}))
    check("refuse/category-outside-enum", got == ["bad-field"], got)
    got = _rules(_registry({"probe": ("lint", "Probe", "probe", ["tools/probe.py"], ())}))
    check("refuse/categories-empty", got == ["bad-field"], got)
    got = _rules(_registry().replace('scope = "setup"', 'scope = "setup"\nowner = "x"'))
    check("refuse/unknown-field", got == ["bad-field"], got)
    got = _rules(_registry().replace("reviewed = false", 'reviewed = "no"', 1))
    check("refuse/reviewed-not-boolean", got == ["bad-field"], got)
    got = _rules(_registry().replace("reviewed = false\n", "", 1))
    check("refuse/missing-field", got == ["bad-field"], got)
    got = _rules(_registry({"probe": ("lint", "Probe", "probe", ["tools/probe.py", "/tools/probe.py"])}))
    check("refuse/trigger-not-relative", got == ["bad-field"], got)

    got = _rules(_registry({"probe": ("lint", "Probe", "probe", [])}))
    check("refuse/empty-triggers", sorted(set(got)) == ["empty-triggers", "import-not-triggered"], got)
    got = _rules(_registry({"setup": ("sum", "Hostile setting", "undetermined", [])}))
    check("pass/empty-triggers-allowed-scope", got == [], got)

    got = _rules(_registry({"probe": ("lint", "Probe", "probe", ["tools/probe.py", "tools/gone/**"])}))
    check("refuse/dead-glob", got == ["dead-glob"], got)
    got = _rules(_registry({"probe": ("lint", "Probe", "probe", ["tools/probe.py", "docs/*.md"])}))
    check("pass/live-glob", got == [], got)

    got = _rules(_registry({"gate": ("lint", "Gate self-test", "self-test", ["tools/gate.py", "tools/_deep.py"])}))
    check("refuse/import-not-triggered", got == ["import-not-triggered"], got)
    got = _rules(_registry({"gate": ("lint", "Gate self-test", "self-test", ["tools/gate.py", "tools/_helper.py"])}))
    check("refuse/transitive-import-not-triggered", got == ["import-not-triggered"], got)
    got = _rules(_registry({"gate": ("lint", "Gate self-test", "self-test", ["tools/_helper.py", "tools/_deep.py"])}))
    check("refuse/run-script-not-triggered", got == ["import-not-triggered"], got)
    got = _rules(_registry({"gate": ("lint", "Gate self-test", "self-test", ["tools/*.py"])}))
    check("pass/import-covered-by-glob", got == [], got)
    got = _rules(_registry({"gate": ("lint", "Gate self-test", "whole-repo", ["**"])}))
    check("pass/import-covered-by-double-star", got == [], got)

    weekly = ("lint", "Probe", "probe", ["tools/probe.py"], ("ci",))
    got = _rules(_registry({"probe": weekly + ("weekly-only = true",)}))
    check("refuse/weekly-only-unauthorized", got == ["weekly-only-unauthorized"], got)
    got = _rules(_registry({"probe": weekly + ('weekly-only = true\nweekly-only-authorization = ""',)}))
    check("refuse/weekly-only-empty-authorization", sorted(set(got)) == ["bad-field"], got)
    got = _rules(_registry({"probe": weekly + ('weekly-only = true\nweekly-only-authorization = "record 7"',)}))
    check("pass/weekly-only-authorized", got == [], got)

    # The ceiling at its ends: one under, exactly at, and one over UNDETERMINED_CEILING.
    rows = [("lint", "Probe"), ("sum", "Hostile setting"), ("sum", "Aggregate")]
    keys = ["probe", "setup", "aggregate"]
    for name, count, refused in (("bound/undetermined-under", UNDETERMINED_CEILING - 1, False),
                                 ("bound/undetermined-at", UNDETERMINED_CEILING, False),
                                 ("bound/undetermined-over", UNDETERMINED_CEILING + 1, True)):
        changes = {key: row + ("undetermined", ["tools/probe.py"])
                   for key, row in zip(keys[:count], rows[:count])}
        got = _rules(_registry(changes))
        check(name, (got == ["undetermined-over-ceiling"]) if refused else (got == []), (count, got))

    check("glob/star-stays-in-segment", not glob_match("docs/*.md", "docs/sub/b.md")
          and glob_match("docs/*.md", "docs/a.md"), "docs/*.md")
    check("glob/double-star-spans", all(glob_match(g, "docs/sub/b.md") for g in (
        "**", "docs/**", "**/b.md", "docs/**/b.md")) and not glob_match("docs/**/b.md", "b.md"), "**")
    check("glob/question-one-char", glob_match("docs/?.md", "docs/a.md")
          and not glob_match("docs/?.md", "docs/ab.md"), "?")
    # Globs a backtracking regex takes exponential time on: many stars in one segment, stars split by a
    # repeated letter, and many ** segments, each against a long path it does not match.
    long_name = "tools/" + "a" * 60 + ".py"
    check("glob/many-wildcards-bounded", not glob_match("tools/" + "*" * 24 + "Z.py", long_name)
          and not glob_match("tools/" + "*a" * 24 + "Z.py", long_name)
          and not glob_match("**/" * 24 + "Z.py", "/".join("a" * 40) + "/b.py")
          and glob_match("tools/" + "*a" * 24 + "*.py", long_name), long_name)
    got = _rules(_registry({"probe": ("lint", "Probe", "probe", ["tools/probe.py", "tools/" + "*" * 24 + "Z.py"])}))
    check("refuse/many-star-dead-glob", got == ["dead-glob"], got)

    got = _rules(_registry() + "[[check\n")
    check("cannot-evaluate/toml-decode-error", got[0] == "cannot-evaluate" and "TOMLDecodeError" in got[1], got)
    got = _rules(_registry() + "big = " + "9" * 5000 + "\n")
    check("cannot-evaluate/toml-bare-valueerror", got[0] == "cannot-evaluate" and "ValueError" in got[1], got)
    real_loads = tomllib.loads
    for name, error in (("cannot-evaluate/toml-recursion", RecursionError),
                        ("cannot-evaluate/toml-memory", MemoryError)):
        def _raise(_text, error=error):
            raise error("raised by the self-test")
        tomllib.loads = _raise
        try:
            got = _rules(_registry())
        finally:
            tomllib.loads = real_loads
        check(name, got[0] == "cannot-evaluate" and error.__name__ in got[1], got)
    got = _rules(_registry().replace("format-version = 1", "format-version = 2"))
    check("cannot-evaluate/format-version", got[0] == "cannot-evaluate", got)
    got = _rules(_registry(), tracked=())
    check("cannot-evaluate/no-tracked-files", got[0] == "cannot-evaluate", got)
    got = _rules(_registry(), sources={k: v for k, v in FIXTURE_SOURCES.items() if k != "tools/_helper.py"})
    check("cannot-evaluate/closure-source-unreadable", got[0] == "cannot-evaluate", got)
    got = _rules(_registry(), sources=dict(FIXTURE_SOURCES, **{"tools/_deep.py": "def (\n"}))
    check("cannot-evaluate/closure-source-unparseable", got[0] == "cannot-evaluate", got)
    got = _rules(_registry(), workflow=FIXTURE_WORKFLOW.replace(
        "      - name: Aggregate\n        run: echo done\n", "      - run: echo done\n"))
    check("cannot-evaluate/unnamed-run-step", got[0] == "cannot-evaluate", got)
    got = _rules(_registry(), workflow=FIXTURE_WORKFLOW.replace("Hostile setting", "Aggregate"))
    check("cannot-evaluate/duplicate-step-name", got[0] == "cannot-evaluate", got)
    got = _rules(_registry(), workflow="jobs:\n  lint:\n    steps:\n      - uses: actions/checkout@v4\n")
    check("cannot-evaluate/no-run-steps", got[0] == "cannot-evaluate", got)

    # Workflow shapes YAML reads as a step, a name or a run text other than the one the line reader
    # would take; each is cannot-evaluate, never a pass on a misread step.
    extra = FIXTURE_WORKFLOW + "  extra:\n    runs-on: ubuntu-latest\n"
    unregistered = "      - name: Unregistered\n        run: echo unregistered\n"
    probe_step = "      - name: Probe\n        run: python3 -I -B tools/probe.py\n"
    for name, workflow in (
        ("cannot-evaluate/shape-compact-steps-list",
         extra + "    steps:\n    - name: Unregistered\n      run: echo unregistered\n"),
        ("cannot-evaluate/shape-flow-steps", extra + "    steps: [{name: Unregistered, run: echo unregistered}]\n"),
        ("cannot-evaluate/shape-job-body-indent-6",
         FIXTURE_WORKFLOW + "  extra:\n      runs-on: ubuntu-latest\n      steps:\n"
         "        - name: Unregistered\n          run: echo unregistered\n"),
        ("cannot-evaluate/shape-second-jobs-block", FIXTURE_WORKFLOW + "jobs: # again\n  extra:\n    steps:\n"
         + unregistered),
        ("cannot-evaluate/shape-repeated-job-key", FIXTURE_WORKFLOW + "    steps:\n" + unregistered),
        ("cannot-evaluate/shape-block-run-comment", FIXTURE_WORKFLOW.replace(
            "        run: python3 -I -B tools/probe.py\n", "        run: | # note\n          python3 -I -B tools/gate.py\n")),
        ("cannot-evaluate/shape-block-run-keep", FIXTURE_WORKFLOW.replace(
            "        run: python3 -I -B tools/probe.py\n", "        run: |+\n          python3 -I -B tools/gate.py\n")),
        ("cannot-evaluate/shape-block-run-indent-indicator", FIXTURE_WORKFLOW.replace(
            "        run: python3 -I -B tools/probe.py\n", "        run: |2\n            python3 -I -B tools/gate.py\n")),
        ("cannot-evaluate/shape-plain-run-continued", FIXTURE_WORKFLOW.replace(
            "        run: python3 -I -B tools/probe.py\n", "        run: python3 -I -B\n          tools/gate.py\n")),
        ("cannot-evaluate/shape-run-untracked-script", FIXTURE_WORKFLOW.replace("tools/probe.py", "tools/absent.py")),
        ("cannot-evaluate/shape-name-continued", FIXTURE_WORKFLOW.replace(
            probe_step, "      - name: Probe\n          (renamed)\n        run: python3 -I -B tools/probe.py\n")),
        ("cannot-evaluate/shape-name-folded", FIXTURE_WORKFLOW.replace(
            "      - name: Probe\n", "      - name: >-\n          Probe\n")),
        ("cannot-evaluate/shape-name-anchor", FIXTURE_WORKFLOW.replace("      - name: Probe\n", "      - name: &n Probe\n")),
        ("cannot-evaluate/shape-name-quote-inside", FIXTURE_WORKFLOW.replace("'Gate self-test'", "'Gate' self-test'")),
    ):
        got = _rules(_registry(), workflow=workflow)
        check(name, tuple(got[:1]) == ("cannot-evaluate",), got)
    got = _rules(_registry(), workflow=extra + "    steps: # note\n" + unregistered)
    check("refuse/missing-entry-after-steps-comment", got == ["missing-entry"], got)
    got = _rules(_registry({"gate": ("lint", "Gate self-test", "self-test", ["tools/_helper.py", "tools/_deep.py"])}),
                 workflow=FIXTURE_WORKFLOW.replace("tools/gate.py --self-test", "./tools/gate.py --self-test"))
    check("refuse/dot-slash-script-not-triggered", got == ["import-not-triggered"], got)

    for name, value in (("cannot-evaluate/format-version-true", "true"),
                        ("cannot-evaluate/format-version-float", "1.0")):
        got = _rules(_registry().replace("format-version = 1", "format-version = " + value))
        check(name, tuple(got[:1]) == ("cannot-evaluate",), got)
    authorization = 'weekly-only-authorization = "record 7"'
    got = [_rules(_registry({"probe": weekly + (flag + authorization,)})) for flag in ("", "weekly-only = false\n")]
    check("refuse/authorization-without-weekly-only", got == [["bad-field"], ["bad-field"]], got)

    import tempfile
    with tempfile.TemporaryDirectory(prefix="ci-selection-selftest-") as tmp:
        missing, directory, wide = Path(tmp) / "absent", Path(tmp) / "dir", Path(tmp) / "wide"
        os.makedirs(directory)
        with open(wide, "wb") as handle:
            handle.write(b"\xff\n")
        results = []
        for path in (missing, directory, wide):
            try:
                read_text(path, path.name)
                results.append("read")
            except CannotEvaluate as exc:
                results.append(str(exc))
    check("cannot-evaluate/unreadable-input", len(results) == 3 and "cannot stat" in results[0]
          and "not a regular file" in results[1] and "not UTF-8" in results[2], results)

    got = [_main_exit(argv) for argv in (["--bogus"], ["--self-test", "--bogus"],
                                         ["--execution-report", "relative/report"])]
    check("refuse/unrecognized-argument", got == [2, 2, 2], got)

    got = _main_exit([])
    check("live/real-tree-passes", got == 0, got)

    if FAILURES:
        print("SELF-TEST FAIL: check_ci_selection {} of {} check(s) failed".format(len(FAILURES), len(EXECUTED)))
        for failure in FAILURES:
            print("  - " + failure)
        return 1
    print("SELF-TEST PASS: check_ci_selection {} checks (a fixture registry passes; a missing, unknown or "
          "duplicate entry, a field outside its enum or schema, empty triggers where the scope needs them, a "
          "dead glob, an untriggered run script (also through ./) or direct or transitive import, an "
          "unauthorized weekly-only and an authorization without weekly-only each fail; the undetermined "
          "ceiling holds one under, at and over; globs with many wildcards finish; TOML decode, bare "
          "ValueError, RecursionError and MemoryError, a format-version of 2, true or 1.0, unreadable inputs, "
          "and the {} workflow shapes tested (an unnamed step, a duplicate step name, no run steps, and "
          "compact, flow, off-indent, repeated or second-block steps, block run headers with a comment, "
          "keep or indent indicator, a continued run or name, an untracked script, a folded, anchored or "
          "badly quoted name) are cannot-evaluate; an unrecognized argument exits 2; the live tree "
          "passes)".format(len(EXECUTED), 3 + sum(1 for name in EXECUTED if name.startswith("cannot-evaluate/shape-"))))
    return 0


def _main_exit(argv):
    """main()'s exit code for the argument list argv, its output captured."""
    import contextlib
    import io
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return main(list(argv))


def main(argv=None):
    if argv is None:
        # The process entry: the command line, ended through the execution-report finalizer when the
        # self-test armed it.
        _code = main(sys.argv[1:])
        _finalizer = sys.modules.get("_selftest_exit_report")
        (sys.exit if _finalizer is None else _finalizer.exit_with)(_code)
    # The execution gate appends `--execution-report ABS_PATH` to the self-test launch; accepted only in
    # that trailing two-token form, with an absolute path, alone or after --self-test.
    report_path = None
    if len(argv) >= 2 and argv[-2] == "--execution-report" and os.path.isabs(argv[-1]) \
            and argv[:-2] in ([], ["--self-test"]):
        report_path, argv = argv[-1], ["--self-test"]
    if argv not in ([], ["--self-test"]):
        print("error: unrecognized arguments {!r}; fail closed".format(argv), file=sys.stderr)
        return 2
    if argv == ["--self-test"]:
        if report_path is not None:
            # Finalized at interpreter exit by tools/_selftest_exit_report.py; nothing writes it in band.
            sys.path.append(str(Path(__file__).resolve().parent))
            import _selftest_exit_report
            _selftest_exit_report.arm(report_path, SUITE_ID, EXECUTED)
        return _self_test()
    return run_gate(ROOT)


# The child-status guard: an inline copy of the canonical guard in opf/tools/_child_status.py, which
# tools/check_child_status_coverage.py pins this copy to by AST. This gate starts git for the tracked-file
# listing and reads its exit status (subprocess), so under an ignored SIGCHLD a failing listing would read
# as a success; the guard runs at the entry line, before main() dispatches.
# The status the probe child exits with; any value other than 0 and the refusal exit would do.
_CHILD_STATUS_PROBE_CODE = 77


def _child_status_refusal():
    """None when this process can read a child's exit status, otherwise the reason it cannot."""
    import os
    import signal
    if not hasattr(signal, "SIGCHLD"):
        return None
    disposition = signal.getsignal(signal.SIGCHLD)
    if disposition is not signal.SIG_DFL:
        return ("SIGCHLD is ignored (SIG_IGN) in this process" if disposition is signal.SIG_IGN
                else "SIGCHLD is not at its default disposition in this process")
    if not hasattr(os, "fork"):
        return None
    try:
        pid = os.fork()
    except OSError as exc:
        return "the child-status probe could not fork ({})".format(type(exc).__name__)
    if pid == 0:
        os._exit(_CHILD_STATUS_PROBE_CODE)
    try:
        waited, status = os.waitpid(pid, 0)
    except ChildProcessError:
        return "the probe child was reaped before its exit status could be read (ECHILD)"
    except OSError as exc:
        return "the probe child's exit status could not be read ({})".format(type(exc).__name__)
    if waited != pid or os.waitstatus_to_exitcode(status) != _CHILD_STATUS_PROBE_CODE:
        return "a probe child that exited {} was read back as wait status {}".format(
            _CHILD_STATUS_PROBE_CODE, status)
    return None


def _child_status_refusal_exit(name):
    """0 when a child's exit status is observable; otherwise write the named refusal and exit 2 at once
    (os._exit, so no exit-time stream flush can replace the status)."""
    import os
    reason = _child_status_refusal()
    if reason is None:
        return 0
    try:
        sys.stderr.write("error: {} refuses to run: {}, so a failed child would read as a success. Nothing "
                         "was run (cannot evaluate). Start it from a parent that leaves SIGCHLD at "
                         "SIG_DFL.\n".format(name, reason))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)


if __name__ == "__main__":
    sys.exit(_child_status_refusal_exit("check_ci_selection.py") or main())
