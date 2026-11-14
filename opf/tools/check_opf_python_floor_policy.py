#!/usr/bin/env python3
"""OPF Python-floor policy gate (OPF-PYTHON-FLOOR-POLICY): the OPF tree owns its Python floor and its
authoring host patch. Two policy files beside this gate are the only authorities, and both are required:
tools/python-floor.toml (the floor and every OPF surface that states or carries it) and
tools/python-host.toml (the exact CPython patch release the OPF authoring host-patch jobs run). The
tomllib parse-site table tools/tomllib_parse_sites.toml is OPF's own too. Every path is relative to the
OPF root (the directory above this file's tools/). The gate opens no path outside that root: there is no
parent mode and no fallback to a parent repository's policy, so a missing OPF policy file is
cannot-evaluate even where a parent repository has one.

This gate holds the policy, not the surfaces' content beyond the statements listed below: the guard,
dynamic, fallback, tomllib-handling and claims checks of each named file are the floor gate's, and the
host-patch job binding (the jobs' steps and the interpreter they run) is the host-pin gate's.

Rules (RULES), each a finding (exit 1) unless noted:
  schema        both policies parse and carry exactly their keys (POLICY_KEYS, HOST_KEYS) with their
                types, format-version = 1, python-floor "MAJOR.MINOR" and python-host-patch
                "MAJOR.MINOR.PATCH"; a parse failure, a missing file, a missing or unknown key or a wrong
                type is cannot-evaluate (exit 2). Each path list is sorted and unique,
                nonblocking-surfaces is a subset of guarded-surfaces, and declaration-copies maps a
                declaration-files entry to an integer of at least 2.
  floor         python-floor names the decided floor (FLOOR), and completeness-check and
                documentation-check are true, their decided values.
  host-patch    python-host-patch is a patch release of python-floor; host-patch-jobs names at least one
                job id (the OPF authoring host-patch job is required, never optional);
                host-patch-workflow is one of workflow-files.
  paths         every path is OPF-root-relative (no leading /, no empty, . or .. part, no backslash);
                each listed file is a regular file reached through directories only (no symlink),
                guarded entries end in .py, workflow-dir is WORKFLOW_DIR and a directory, each
                workflow-files entry lies directly in it, and an excluded-trees entry ends in /.
  entrypoints   the authoring tools are enumerated exactly: guarded-surfaces is the set of .py files
                under the OPF root, outside excluded-trees and SKIPPED_DIR_NAMES, with a module-level
                main block.
  workflows     workflow-files is exactly the set of files in workflow-dir.
  pins          every file outside excluded-trees with a python-version pin line is a workflow file, an
                adopter template or a doctor template; each template carries one; a workflow pin is the
                floor, or the host patch in host-patch-workflow only.
  templates     adopter-templates and doctor-templates are disjoint from each other and from
                workflow-files, and every pin in them is exactly the floor: an adopter's CI and doctor
                requirement is the floor, never the OPF authoring host patch.
  declarations  the documentation is enumerated exactly: declaration-files is the set of non-.py files
                outside excluded-trees that state the floor phrase ("Python", MAJOR.MINOR, "or newer");
                each states it declaration-copies times (once by default), naming the floor and no
                patch release.
  table         the parse-site table parses with its schema (format-version = 1 and [[site]] rows of
                class CALLER or EXEMPT; a schema break is cannot-evaluate), each row's path is a
                regular file under the OPF root, and no (path, function, form) row repeats.

Modes:
  check_opf_python_floor_policy.py               the live OPF tree.
  check_opf_python_floor_policy.py --self-test   fixture trees for every rule, red-on-revert vectors (for
                                                 each rule, the gate with that rule removed from RULES
                                                 passes a fixture the intact gate refuses) and a check
                                                 that a live run opens nothing outside the OPF root.

Exit: 0 pass; 1 a finding; 2 usage or cannot-evaluate.

DISCLOSED RESIDUAL. The scans read a file's text with fixed patterns: a pin or floor statement spelled
another way, or in a file that is not UTF-8 text, is not seen, and a symlink or other non-regular file in
the tree is not read. A pin is attributed to a file, not to a job: which job of the host-patch workflow
may pin the host patch is the host-pin gate's to hold. Table rows are checked as data, not rescanned.

Run this gate isolated: python3 -I -B opf/tools/check_opf_python_floor_policy.py
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: check_opf_python_floor_policy.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import ast
import os
import re
import shutil
import stat
import tempfile
import tomllib
from pathlib import Path

OPF_ROOT = Path(__file__).resolve().parents[1]
POLICY_REL = "tools/python-floor.toml"
HOST_POLICY_REL = "tools/python-host.toml"
FLOOR = (3, 14)
WORKFLOW_DIR = ".github/workflows"
POLICY_KEYS = {
    "format-version": int, "python-floor": str, "guarded-surfaces": list, "nonblocking-surfaces": list,
    "completeness-check": bool, "documentation-check": bool, "workflow-dir": str, "workflow-files": list,
    "host-patch-workflow": str, "host-patch-jobs": list, "adopter-templates": list,
    "doctor-templates": list, "declaration-files": list, "declaration-copies": dict,
    "excluded-trees": list, "tomllib-table": str,
}
HOST_KEYS = {"format-version": int, "python-host-patch": str}
FILE_LISTS = ("guarded-surfaces", "nonblocking-surfaces", "workflow-files", "adopter-templates",
              "doctor-templates", "declaration-files")
SORTED_LISTS = FILE_LISTS + ("excluded-trees", "host-patch-jobs")
SKIPPED_DIR_NAMES = frozenset((".git", "__pycache__", ".venv", "venv", "node_modules"))
FLOOR_RE = re.compile(r"([1-9][0-9]*)\.(0|[1-9][0-9]*)")
PATCH_RE = re.compile(r"([1-9][0-9]{0,3})\.(0|[1-9][0-9]{0,3})\.(0|[1-9][0-9]{0,3})")
JOB_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*")
PART_RE = re.compile(r"[A-Za-z0-9_.-]+")
PIN_KEY = "python-version"
# A pin line: optional indentation, an optional "- " list marker, the key in any case, a colon, then the
# value up to an optional comment. Read per "\n"-split line.
PIN_LINE_RE = re.compile(r"[ \t]*(?:-[ \t]+)?" + PIN_KEY + r"[ \t]*:(?P<rest>.*)", re.IGNORECASE)
# The floor phrase of a declaration: "Python", MAJOR.MINOR with an optional .PATCH, "or newer", each
# separated by whitespace or a no-break space in its HTML forms.
SEP = r"(?:\s|&nbsp;|&#160;)+"
PHRASE_RE = re.compile(r"Python" + SEP + r"(?P<major>[0-9]+)\.(?P<minor>[0-9]+)(?P<patch>\.[0-9]+)?" + SEP
                       + r"or" + SEP + r"newer", re.IGNORECASE)
TABLE_ROW_KEYS = {"CALLER": frozenset(("path", "function", "form", "count", "class", "caller")),
                  "EXEMPT": frozenset(("path", "function", "form", "count", "class", "reason"))}
TABLE_EXEMPT_REASONS = frozenset(("test-code", "self-emitted-bytes", "already-validated",
                                  "valueerror-passthrough"))
RULES = frozenset(("schema", "floor", "host-patch", "paths", "entrypoints", "workflows", "pins", "templates",
                   "declarations", "table"))


class CannotEvaluate(Exception):
    """An input is missing, unreadable or malformed: exit 2."""


def _read_bytes(path):
    """A regular, non-symlink file's bytes, or CannotEvaluate."""
    try:
        mode = os.lstat(path).st_mode
    except OSError as exc:
        raise CannotEvaluate("{}: cannot stat: {}".format(path, exc))
    if not stat.S_ISREG(mode):
        raise CannotEvaluate("{}: not a regular file".format(path))
    try:
        return Path(path).read_bytes()
    except OSError as exc:
        raise CannotEvaluate("{}: cannot read: {}".format(path, exc))


def _read_text(path):
    """A regular, non-symlink UTF-8 file's text, or CannotEvaluate."""
    try:
        return _read_bytes(path).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CannotEvaluate("{}: cannot read as UTF-8: {}".format(path, exc))


def _parse_toml(path, label):
    """The parsed TOML at path. tomllib raises ValueError (TOMLDecodeError among them, and a bare
    ValueError on an over-long integer), RecursionError on deep nesting or a many-part key and MemoryError
    under a memory limit: each is cannot-evaluate."""
    try:
        return tomllib.loads(_read_text(path))
    except (ValueError, RecursionError, MemoryError) as exc:
        raise CannotEvaluate("{}: does not parse: {}".format(label, exc))


def _keys(data, keys, label):
    """CannotEvaluate unless data carries exactly keys, each of its type, and format-version = 1."""
    if set(data) != set(keys):
        raise CannotEvaluate("{}: keys {} (want exactly {})".format(label, sorted(data), sorted(keys)))
    for key, kind in keys.items():
        if type(data[key]) is not kind:
            raise CannotEvaluate("{}: {} must be a {}".format(label, key, kind.__name__))
    if data["format-version"] != 1:
        raise CannotEvaluate("{}: format-version must be the integer 1".format(label))


def load_policy(root):
    """(policy, host, findings): both OPF policies under root, schema-checked. Either file missing or
    what the schema rule refuses as unreadable raises CannotEvaluate."""
    data = _parse_toml(Path(root) / POLICY_REL, POLICY_REL)
    host = _parse_toml(Path(root) / HOST_POLICY_REL, HOST_POLICY_REL)
    findings = []
    if "schema" in RULES:
        _keys(data, POLICY_KEYS, POLICY_REL)
        _keys(host, HOST_KEYS, HOST_POLICY_REL)
        if FLOOR_RE.fullmatch(data["python-floor"]) is None:
            raise CannotEvaluate("{}: python-floor must be a \"MAJOR.MINOR\" string, got {!r}".format(
                POLICY_REL, data["python-floor"]))
        if PATCH_RE.fullmatch(host["python-host-patch"]) is None:
            raise CannotEvaluate("{}: python-host-patch must be a \"MAJOR.MINOR.PATCH\" string, got "
                                 "{!r}".format(HOST_POLICY_REL, host["python-host-patch"]))
        for key in SORTED_LISTS:
            if not all(isinstance(item, str) for item in data[key]):
                raise CannotEvaluate("{}: {} must be a list of strings".format(POLICY_REL, key))
            if data[key] != sorted(set(data[key])):
                findings.append("{}: {} must be sorted and unique".format(POLICY_REL, key))
        if not set(data["nonblocking-surfaces"]) <= set(data["guarded-surfaces"]):
            findings.append("{}: nonblocking-surfaces must each be listed in guarded-surfaces".format(POLICY_REL))
        for name, count in data["declaration-copies"].items():
            if name not in data["declaration-files"] or type(count) is not int or count < 2:
                findings.append("{}: declaration-copies entry {!r} must name a declaration-files entry with "
                                "an integer of at least 2".format(POLICY_REL, name))
    return data, host, findings


def _path_fault(rel):
    """Why rel is not an OPF-root-relative path, or None."""
    if not isinstance(rel, str) or not rel or rel.startswith("/") or "\\" in rel:
        return "is not a relative path"
    parts = rel.rstrip("/").split("/")
    if any(part in ("", ".", "..") or not PART_RE.fullmatch(part) for part in parts):
        return "has an empty, . or .. part or a character outside [A-Za-z0-9_.-]"
    return None


def _regular(root, rel):
    """Why root/rel is not a regular file reached through directories only, or None. Each part is read
    with lstat, so a symlink, which is neither a regular file nor a directory there, is refused at
    any part."""
    current = Path(root)
    parts = rel.split("/")
    for index, part in enumerate(parts):
        current = current / part
        try:
            mode = os.lstat(current).st_mode
        except OSError:
            return "is missing"
        if index < len(parts) - 1 and not stat.S_ISDIR(mode):
            return "has a parent that is a symlink or not a directory"
    return None if stat.S_ISREG(mode) else "is a symlink or not a regular file"


def _usable(root, rel):
    """True when rel is a well-formed path to a regular file under root."""
    return _path_fault(rel) is None and _regular(root, rel) is None


def _patch_of(patch, floor):
    """True when the MAJOR.MINOR.PATCH string patch is a patch release of the MAJOR.MINOR string floor."""
    match, base = PATCH_RE.fullmatch(patch), FLOOR_RE.fullmatch(floor)
    return bool(match and base) and (int(match.group(1)), int(match.group(2))) == (
        int(base.group(1)), int(base.group(2)))


def floor_findings(policy):
    if "floor" not in RULES:
        return []
    findings = []
    match = FLOOR_RE.fullmatch(policy["python-floor"])
    floor = (int(match.group(1)), int(match.group(2))) if match else None
    if floor != FLOOR:
        findings.append("{}: python-floor is {!r}, but the decided floor is {}.{}".format(
            POLICY_REL, policy["python-floor"], *FLOOR))
    for key in ("completeness-check", "documentation-check"):
        if policy[key] is not True:
            findings.append("{}: {} must be true, its decided value".format(POLICY_REL, key))
    return findings


def host_patch_findings(policy, host):
    if "host-patch" not in RULES:
        return []
    findings = []
    if not _patch_of(host["python-host-patch"], policy["python-floor"]):
        findings.append("{}: python-host-patch {!r} is not a patch release of the floor {!r} in {}".format(
            HOST_POLICY_REL, host["python-host-patch"], policy["python-floor"], POLICY_REL))
    if not policy["host-patch-jobs"]:
        findings.append("{}: host-patch-jobs is empty; the OPF authoring host-patch job is required".format(
            POLICY_REL))
    findings += ["{}: host-patch-jobs entry {!r} is not a job id".format(POLICY_REL, job)
                 for job in policy["host-patch-jobs"] if not JOB_RE.fullmatch(job)]
    if policy["host-patch-workflow"] not in policy["workflow-files"]:
        findings.append("{}: host-patch-workflow {!r} is not one of workflow-files".format(
            POLICY_REL, policy["host-patch-workflow"]))
    return findings


def path_findings(root, policy):
    if "paths" not in RULES:
        return []
    findings = []
    for key in FILE_LISTS:
        for rel in policy[key]:
            fault = _path_fault(rel)
            if fault is None and key == "guarded-surfaces" and not rel.endswith(".py"):
                fault = "is not a .py file"
            if fault is None and key == "workflow-files" and rel.rpartition("/")[0] != WORKFLOW_DIR:
                fault = "does not lie directly in {}".format(WORKFLOW_DIR)
            if fault is None:
                fault = _regular(root, rel)
            if fault is not None:
                findings.append("{}: {} entry {!r} {}".format(POLICY_REL, key, rel, fault))
    for rel in policy["excluded-trees"]:
        fault = _path_fault(rel)
        if fault is None and not rel.endswith("/"):
            fault = "does not end in /"
        if fault is not None:
            findings.append("{}: excluded-trees entry {!r} {}".format(POLICY_REL, rel, fault))
    if policy["workflow-dir"] != WORKFLOW_DIR:
        findings.append("{}: workflow-dir is {!r}, not {!r}, the directory GitHub reads workflows from".format(
            POLICY_REL, policy["workflow-dir"], WORKFLOW_DIR))
    else:
        try:
            mode = os.lstat(Path(root) / WORKFLOW_DIR).st_mode
        except OSError:
            mode = 0
        if not stat.S_ISDIR(mode):
            findings.append("{}: workflow-dir {} is not a directory".format(POLICY_REL, WORKFLOW_DIR))
    for key in ("host-patch-workflow", "tomllib-table"):
        fault = _path_fault(policy[key]) or _regular(root, policy[key])
        if fault is not None:
            findings.append("{}: {} {!r} {}".format(POLICY_REL, key, policy[key], fault))
    return findings


def _excluded(rel, trees):
    return any(rel == tree.rstrip("/") or rel.startswith(tree) for tree in trees)


def _tree_files(root, trees):
    """Every OPF-root-relative regular file outside trees and SKIPPED_DIR_NAMES, in sorted walk order.
    An unreadable directory is cannot-evaluate; a symlink or other non-regular entry is not read."""
    def _fail(exc):
        raise CannotEvaluate("cannot walk the OPF tree: {}".format(exc))

    files = []
    for dirpath, dirnames, filenames in os.walk(root, onerror=_fail):
        rel_dir = Path(dirpath).relative_to(root).as_posix()
        prefix = "" if rel_dir == "." else rel_dir + "/"
        dirnames[:] = sorted(name for name in dirnames
                             if name not in SKIPPED_DIR_NAMES and not _excluded(prefix + name + "/", trees))
        for name in sorted(filenames):
            rel = prefix + name
            if _excluded(rel, trees):
                continue
            try:
                mode = os.lstat(Path(dirpath) / name).st_mode
            except OSError as exc:
                raise CannotEvaluate("{}: cannot stat: {}".format(rel, exc))
            if stat.S_ISREG(mode):
                files.append(rel)
    return files


def _has_main_block(tree):
    for node in tree.body:
        test = node.test if isinstance(node, ast.If) else None
        if isinstance(test, ast.Compare) and isinstance(test.left, ast.Name) \
                and test.left.id == "__name__" and len(test.ops) == 1 \
                and isinstance(test.ops[0], ast.Eq) and len(test.comparators) == 1 \
                and isinstance(test.comparators[0], ast.Constant) \
                and test.comparators[0].value == "__main__":
            return True
    return False


def _pin_values(text):
    """The value of each python-version pin line in text, quotes and a trailing comment removed."""
    values = []
    for line in text.split("\n"):
        match = PIN_LINE_RE.fullmatch(line.rstrip("\r"))
        if match is not None:
            value = re.sub(r"[ \t]+#.*$", "", match.group("rest")).strip()
            values.append(value[1:-1] if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\""
                          else value)
    return values


def scan(root, policy):
    """(entrypoints, pins, phrases) of the OPF tree outside excluded-trees: the .py files with a
    module-level main block, {path: pin values} for each file with a pin line, and {path: [(major,
    minor, patch)]} for each non-.py text file stating the floor phrase."""
    entrypoints, pins, phrases = [], {}, {}
    for rel in _tree_files(root, policy["excluded-trees"]):
        data = _read_bytes(Path(root) / rel)
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            if rel.endswith(".py"):
                raise CannotEvaluate("{}: cannot read as UTF-8".format(rel))
            continue
        if rel.endswith(".py"):
            try:
                tree = ast.parse(text)
            except (SyntaxError, ValueError, RecursionError, MemoryError) as exc:
                raise CannotEvaluate("{}: does not parse: {}".format(rel, exc))
            if _has_main_block(tree):
                entrypoints.append(rel)
        values = _pin_values(text)
        if values:
            pins[rel] = values
        if not rel.endswith(".py"):
            found = [(match.group("major"), match.group("minor"), match.group("patch"))
                     for match in PHRASE_RE.finditer(text)]
            if found:
                phrases[rel] = found
    return entrypoints, pins, phrases


def entrypoint_findings(root, policy, entrypoints):
    if "entrypoints" not in RULES:
        return []
    listed = set(policy["guarded-surfaces"])
    findings = ["{}: an OPF entrypoint missing from guarded-surfaces in {}".format(rel, POLICY_REL)
                for rel in entrypoints if rel not in listed]
    findings += ["{}: guarded-surfaces entry {!r} has no module-level main block".format(POLICY_REL, rel)
                 for rel in sorted(listed - set(entrypoints)) if _usable(root, rel)
                 and not _excluded(rel, policy["excluded-trees"])]
    return findings


def workflow_findings(root, policy):
    if "workflows" not in RULES or policy["workflow-dir"] != WORKFLOW_DIR:
        return []
    try:
        names = sorted(os.listdir(Path(root) / WORKFLOW_DIR))
    except OSError:
        return []
    present = [WORKFLOW_DIR + "/" + name for name in names]
    listed = set(policy["workflow-files"])
    return (["{}: a workflow file missing from workflow-files in {}".format(rel, POLICY_REL)
             for rel in present if rel not in listed]
            + ["{}: workflow-files entry {!r} is not in {}".format(POLICY_REL, rel, WORKFLOW_DIR)
               for rel in sorted(listed - set(present))])


def pin_findings(policy, host, pins):
    if "pins" not in RULES:
        return []
    findings = []
    templates = set(policy["adopter-templates"]) | set(policy["doctor-templates"])
    carriers = set(policy["workflow-files"]) | templates
    for rel in sorted(pins):
        if rel not in carriers:
            findings.append("{}: pins {} but is not a workflow file, adopter template or doctor template in "
                            "{}".format(rel, PIN_KEY, POLICY_REL))
    for rel in sorted(templates):
        if rel not in pins:
            findings.append("{}: a template in {} that carries no {} pin".format(rel, POLICY_REL, PIN_KEY))
    for rel in policy["workflow-files"]:
        for value in pins.get(rel, ()):
            if value == policy["python-floor"]:
                continue
            if value == host["python-host-patch"] and rel == policy["host-patch-workflow"]:
                continue
            findings.append("{}: pins {} {!r}; a workflow pins the floor {!r}, or the host patch {!r} in "
                            "{} only".format(rel, PIN_KEY, value, policy["python-floor"],
                                             host["python-host-patch"], policy["host-patch-workflow"]))
    return findings


def template_findings(policy, host, pins):
    if "templates" not in RULES:
        return []
    findings = []
    adopter, doctor = set(policy["adopter-templates"]), set(policy["doctor-templates"])
    for rel in sorted(adopter & doctor):
        findings.append("{}: listed as both an adopter template and a doctor template".format(rel))
    for rel in sorted((adopter | doctor) & set(policy["workflow-files"])):
        findings.append("{}: an authoring workflow file listed as a template".format(rel))
    for rel in sorted(adopter | doctor):
        for value in pins.get(rel, ()):
            if value == policy["python-floor"]:
                continue
            what = " (the OPF authoring host patch)" if value == host["python-host-patch"] else ""
            findings.append("{}: a template pins {} {!r}{}; an adopter or doctor template pins the floor {!r}, "
                            "never the authoring host patch".format(rel, PIN_KEY, value, what, policy["python-floor"]))
    return findings


def declaration_findings(root, policy, phrases):
    if "declarations" not in RULES:
        return []
    findings = []
    listed = set(policy["declaration-files"])
    for rel in sorted(phrases):
        if rel not in listed:
            findings.append("{}: states the floor phrase but is not in declaration-files in {}".format(
                rel, POLICY_REL))
    floor = FLOOR_RE.fullmatch(policy["python-floor"])
    for rel in policy["declaration-files"]:
        if not _usable(root, rel) or _excluded(rel, policy["excluded-trees"]):
            continue
        found = phrases.get(rel, [])
        want = policy["declaration-copies"].get(rel, 1)
        if len(found) != want:
            findings.append("{}: states the floor phrase {} time(s), want {}".format(rel, len(found), want))
        for major, minor, patch in found:
            if floor is None or (int(major), int(minor)) != (int(floor.group(1)), int(floor.group(2))) \
                    or patch is not None:
                findings.append("{}: states Python {}.{}{} or newer; the floor is {}".format(
                    rel, major, minor, patch or "", policy["python-floor"]))
    return findings


def load_table(path, label):
    """The rows of a parse-site table, as a list of dicts, schema-checked; a schema break is
    cannot-evaluate."""
    data = _parse_toml(path, label)
    if set(data) - {"site"} != {"format-version"} or type(data["format-version"]) is not int \
            or data["format-version"] != 1 or not isinstance(data.get("site", []), list):
        raise CannotEvaluate("{}: want format-version = 1 and [[site]] rows only".format(label))
    rows = data.get("site", [])
    for index, row in enumerate(rows, 1):
        where = "{}: site {}".format(label, index)
        kind = row.get("class") if isinstance(row, dict) else None
        if not isinstance(kind, str) or kind not in TABLE_ROW_KEYS or set(row) != TABLE_ROW_KEYS[kind]:
            raise CannotEvaluate("{}: want class CALLER or EXEMPT with that class's keys".format(where))
        if type(row["count"]) is not int or row["count"] < 1 or not all(
                isinstance(row[key], str) and row[key] for key in row if key != "count"):
            raise CannotEvaluate("{}: count must be a positive integer and every other field a non-empty "
                                 "string".format(where))
        if row["form"] not in ("call", "alias"):
            raise CannotEvaluate("{}: form must be call or alias".format(where))
        if kind == "EXEMPT" and row["reason"] not in TABLE_EXEMPT_REASONS:
            raise CannotEvaluate("{}: reason {!r} is not in the closed vocabulary".format(where, row["reason"]))
    return rows


def table_findings(root, policy):
    if "table" not in RULES:
        return []
    rel = policy["tomllib-table"]
    if not _usable(root, rel):
        return []
    rows = load_table(Path(root) / rel, rel)
    findings = []
    seen = set()
    for row in rows:
        key = (row["path"], row["function"], row["form"])
        if key in seen:
            findings.append("{}: repeats the row for {} {} {}".format(rel, *key))
        seen.add(key)
        fault = _path_fault(row["path"]) or _regular(root, row["path"])
        if fault is not None:
            findings.append("{}: row path {!r} {}".format(rel, row["path"], fault))
    return findings


def evaluate(root):
    """Every finding for the OPF tree at root; CannotEvaluate propagates. Nothing outside root is read."""
    policy, host, findings = load_policy(root)
    findings += floor_findings(policy)
    findings += host_patch_findings(policy, host)
    findings += path_findings(root, policy)
    entrypoints, pins, phrases = scan(root, policy)
    findings += entrypoint_findings(root, policy, entrypoints)
    findings += workflow_findings(root, policy)
    findings += pin_findings(policy, host, pins)
    findings += template_findings(policy, host, pins)
    findings += declaration_findings(root, policy, phrases)
    findings += table_findings(root, policy)
    return findings


# ---------------------------------------------------------------------------------------------- self-test

FAILURES = []
EXECUTED = []
# The paths the audit hook saw opened or listed while AUDIT_LOG is a list (the no-outside-read check).
AUDIT_LOG = None
AUDIT_EVENTS = frozenset(("open", "os.listdir", "os.scandir"))


def check(name, got, want):
    EXECUTED.append(name)
    if got != want:
        FAILURES.append("{}: got {!r}, want {!r}".format(name, got, want))


def _write(root, rel, text):
    path = Path(root) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _toml_list(items):
    return "[" + ", ".join('"{}"'.format(item) for item in items) + "]"


def _pin(value):
    """One workflow-shaped pin line; built here so no line of this file is itself a pin line."""
    return "          " + PIN_KEY + ": '" + value + "'\n"


MAIN = 'if __name__ == "__main__":\n    pass\n'
GUARDED = ["tools/a.py", "tools/b.py", "tools/doctor.py"]


def _doctor_text(value):
    """A doctor template: an entrypoint whose triple-quoted workflow text carries a pin line."""
    return 'TEMPLATE = """\n' + _pin(value) + '"""\n' + MAIN


def _policy_text(**over):
    fields = {"format-version": "1", "python-floor": '"3.14"',
              "guarded-surfaces": _toml_list(GUARDED), "nonblocking-surfaces": "[]",
              "completeness-check": "true", "documentation-check": "true",
              "workflow-dir": '".github/workflows"', "workflow-files": _toml_list([".github/workflows/q.yml"]),
              "host-patch-workflow": '".github/workflows/q.yml"', "host-patch-jobs": _toml_list(["opf-host-patch"]),
              "adopter-templates": _toml_list(["enforcement/ci/ci.yml"]),
              "doctor-templates": _toml_list(["tools/doctor.py"]),
              "declaration-files": _toml_list(["spec/QUICK.md"]), "declaration-copies": "{}",
              "excluded-trees": _toml_list(["tools/_vendor/"]),
              "tomllib-table": '"tools/tomllib_parse_sites.toml"'}
    fields.update(over)
    return "".join("{} = {}\n".format(key, value) for key, value in fields.items() if value is not None)


HOST_TEXT = 'format-version = 1\npython-host-patch = "3.14.4"\n'
ROW_TEXT = ('[[site]]\npath = "{path}"\nfunction = "{function}"\nform = "call"\ncount = 1\nclass = "EXEMPT"\n'
            'reason = "test-code"\n')


def _table_text(*rows):
    return "format-version = 1\n\n" + "\n".join(ROW_TEXT.format(path=path, function=function)
                                                 for path, function in rows)


def _opf_fixture(base, policy=None, host=HOST_TEXT, table=None):
    """A minimal OPF tree: two entrypoints and a library module, a doctor template (an entrypoint that
    carries a pin), an adopter template, one workflow pinning the floor and the host patch, one
    declaration, and an excluded vendored tree that would break every scan rule were it read."""
    root = Path(tempfile.mkdtemp(dir=base))
    _write(root, "tools/a.py", MAIN)
    _write(root, "tools/b.py", MAIN)
    _write(root, "tools/lib.py", "VALUE = 1\n")
    _write(root, "tools/doctor.py", _doctor_text("3.14"))
    _write(root, "enforcement/ci/ci.yml", "steps:\n" + _pin("3.14"))
    _write(root, ".github/workflows/q.yml", "jobs:\n" + _pin("3.14") + _pin("3.14.4"))
    _write(root, "spec/QUICK.md", "The tooling requires Python 3.14 or newer.\n")
    _write(root, "tools/_vendor/v.py", MAIN + "# Python 3.9 or newer\n")
    _write(root, "tools/_vendor/v.yml", _pin("3.9"))
    _write(root, "assets/blob.bin", "")
    (root / "assets" / "blob.bin").write_bytes(b"\xff\xfe Python 3.9 or newer\n")
    _write(root, POLICY_REL, policy if policy is not None else _policy_text())
    if host is not None:
        _write(root, HOST_POLICY_REL, host)
    _write(root, "tools/tomllib_parse_sites.toml",
           table if table is not None else _table_text(("tools/a.py", "f")))
    return root


def _outcome(root):
    """"pass", "finding" or "cannot-evaluate" for evaluate()."""
    try:
        findings = evaluate(root)
    except CannotEvaluate:
        return "cannot-evaluate"
    return "finding" if findings else "pass"


def _without(rule, root):
    """The outcome with rule removed from RULES (the reverted gate), RULES restored after."""
    global RULES
    saved = RULES
    RULES = saved - {rule}
    try:
        return _outcome(root)
    finally:
        RULES = saved


def _schema_cases(base):
    check("schema/fixture-passes", _outcome(_opf_fixture(base)), "pass")
    empty = Path(tempfile.mkdtemp(dir=base))
    check("schema/missing-policy-cannot-evaluate", _outcome(empty), "cannot-evaluate")
    check("schema/malformed-policy-cannot-evaluate", _outcome(_opf_fixture(base, policy="x = [\n")),
          "cannot-evaluate")
    check("schema/deep-policy-cannot-evaluate",
          _outcome(_opf_fixture(base, policy="deep = " + "[" * 5000 + "]" * 5000 + "\n")), "cannot-evaluate")
    check("schema/long-integer-cannot-evaluate",
          _outcome(_opf_fixture(base, policy="long = 1" + "0" * 5000 + "\n")), "cannot-evaluate")
    check("schema/missing-key-cannot-evaluate",
          _outcome(_opf_fixture(base, policy=_policy_text(**{"host-patch-jobs": None}))), "cannot-evaluate")
    check("schema/unknown-key-cannot-evaluate",
          _outcome(_opf_fixture(base, policy=_policy_text(extra="1"))), "cannot-evaluate")
    check("schema/combined-pin-files-key-cannot-evaluate",
          _outcome(_opf_fixture(base, policy=_policy_text(**{"pin-files": _toml_list(["enforcement/ci/ci.yml"])}))),
          "cannot-evaluate")
    check("schema/wrong-type-cannot-evaluate",
          _outcome(_opf_fixture(base, policy=_policy_text(**{"completeness-check": '"true"'}))),
          "cannot-evaluate")
    check("schema/floor-format-cannot-evaluate",
          _outcome(_opf_fixture(base, policy=_policy_text(**{"python-floor": '"3.14.1"'}))), "cannot-evaluate")
    unsorted = _opf_fixture(base, policy=_policy_text(**{"guarded-surfaces": _toml_list(
        ["tools/b.py", "tools/a.py", "tools/doctor.py"])}))
    check("schema/unsorted-surfaces-finding", _outcome(unsorted), "finding")
    check("schema/unsorted-surfaces-red-without-schema", _without("schema", unsorted), "pass")
    stray = _opf_fixture(base, policy=_policy_text(**{"nonblocking-surfaces": _toml_list(["tools/lib.py"])}))
    check("schema/nonblocking-not-guarded-finding", _outcome(stray), "finding")
    once = _opf_fixture(base, policy=_policy_text(**{"declaration-copies": '{"spec/QUICK.md" = 1}'}))
    check("schema/declaration-copies-below-two-finding", _outcome(once), "finding")
    unlisted = _opf_fixture(base, policy=_policy_text(**{"declaration-copies": '{"spec/OTHER.md" = 2}'}))
    check("schema/declaration-copies-unlisted-finding", _outcome(unlisted), "finding")


def _host_cases(base):
    check("host/missing-host-policy-cannot-evaluate", _outcome(_opf_fixture(base, host=None)), "cannot-evaluate")
    parent = Path(tempfile.mkdtemp(dir=base))
    _write(parent, ".aiqt/python-host.toml", HOST_TEXT)
    _write(parent, ".aiqt/core/python-floor.toml", 'python-floor = "3.14"\n')
    shutil.copytree(_opf_fixture(base, host=None), parent / "opf")
    check("host/no-fallback-to-a-parent-host-policy", _outcome(parent / "opf"), "cannot-evaluate")
    check("host/host-policy-unknown-key-cannot-evaluate",
          _outcome(_opf_fixture(base, host=HOST_TEXT + 'host-jobs = ["x"]\n')), "cannot-evaluate")
    check("host/host-policy-missing-key-cannot-evaluate",
          _outcome(_opf_fixture(base, host="format-version = 1\n")), "cannot-evaluate")
    for name, value in (("minor-only", "3.14"), ("leading-zero", "3.14.04"), ("suffix", "3.14.4rc1")):
        check("host/patch-format-" + name + "-cannot-evaluate",
              _outcome(_opf_fixture(base, host='format-version = 1\npython-host-patch = "{}"\n'.format(value))),
              "cannot-evaluate")
    # The host patch's floor boundary: just below, at each end inside, and just above. The host rule alone
    # is read, since the fixture's workflow pins 3.14.4 and another valid host patch fails the pins rule.
    for name, value, faults in (("just-below-the-floor", "3.13.9999", True), ("floor-first-patch", "3.14.0", False),
                                ("floor-last-patch", "3.14.9999", False), ("just-above-the-floor", "3.15.0", True)):
        policy, host, _findings = load_policy(_opf_fixture(
            base, host='format-version = 1\npython-host-patch = "{}"\n'.format(value)))
        check("host/patch-" + name + ("-finding" if faults else "-accepted"),
              host_patch_findings(policy, host) != [], faults)
    nojob = _opf_fixture(base, policy=_policy_text(**{"host-patch-jobs": "[]"}))
    check("host/empty-host-patch-jobs-finding", _outcome(nojob), "finding")
    check("host/empty-host-patch-jobs-red-without-host-patch", _without("host-patch", nojob), "pass")
    badjob = _opf_fixture(base, policy=_policy_text(**{"host-patch-jobs": _toml_list(["opf host"])}))
    check("host/host-patch-job-not-an-id-finding", _outcome(badjob), "finding")
    check("host/host-patch-non-string-cannot-evaluate",
          _outcome(_opf_fixture(base, policy=_policy_text(**{"host-patch-jobs": "[1]"}))), "cannot-evaluate")
    other = _opf_fixture(base, policy=_policy_text(**{"host-patch-workflow": '"enforcement/ci/ci.yml"'}))
    check("host/host-patch-workflow-not-a-workflow-finding", _outcome(other), "finding")
    policy, host, _findings = load_policy(other)
    check("host/host-patch-workflow-not-a-workflow-found-by-host-rule",
          [finding for finding in host_patch_findings(policy, host) if "host-patch-workflow" in finding] != [], True)


def _floor_and_path_cases(base):
    older = _opf_fixture(base, policy=_policy_text(**{"python-floor": '"3.13"'}),
                         host='format-version = 1\npython-host-patch = "3.13.4"\n')
    check("floor/older-floor-finding", _outcome(older), "finding")
    newer = _opf_fixture(base, policy=_policy_text(**{"python-floor": '"3.15"'}))
    check("floor/newer-floor-finding", _outcome(newer), "finding")
    # The decided floor's boundary, read from the floor rule alone: just below, at, and just above.
    for name, root, faults in (("just-below", older, True), ("at", _opf_fixture(base), False),
                               ("just-above", newer, True)):
        check("floor/floor-" + name + ("-found-by-floor-rule" if faults else "-accepted-by-floor-rule"),
              floor_findings(load_policy(root)[0]) != [], faults)
    switch = _opf_fixture(base, policy=_policy_text(**{"documentation-check": "false"}))
    check("floor/documentation-switch-off-finding", _outcome(switch), "finding")
    check("floor/documentation-switch-off-red-without-floor", _without("floor", switch), "pass")
    missing = _opf_fixture(base, policy=_policy_text(**{"declaration-files": _toml_list(["spec/GONE.md",
                                                                                        "spec/QUICK.md"])}))
    check("paths/missing-declaration-finding", _outcome(missing), "finding")
    check("paths/missing-declaration-red-without-paths", _without("paths", missing), "pass")
    gone = _opf_fixture(base, policy=_policy_text(**{"adopter-templates": _toml_list(["enforcement/ci/ci.yml",
                                                                                      "enforcement/ci/gone.yml"])}))
    check("paths/missing-template-finding", _outcome(gone), "finding")
    climb = _opf_fixture(base)
    _write(climb, POLICY_REL, _policy_text(**{"declaration-files": _toml_list(
        ["../{}/spec/QUICK.md".format(climb.name), "spec/QUICK.md"])}))
    check("paths/parent-path-finding", _outcome(climb), "finding")
    absolute = _opf_fixture(base, policy=_policy_text(**{"declaration-files": _toml_list(["/spec/QUICK.md",
                                                                                         "spec/QUICK.md"])}))
    check("paths/absolute-path-finding", _outcome(absolute), "finding")
    linked = _opf_fixture(base)
    (linked / "spec" / "QUICK.md").unlink()
    _write(linked, "real/QUICK.md", "The tooling requires Python 3.14 or newer.\n")
    (linked / "spec" / "QUICK.md").symlink_to(linked / "real" / "QUICK.md")
    _write(linked, POLICY_REL, _policy_text(**{"declaration-files": _toml_list(["real/QUICK.md",
                                                                                 "spec/QUICK.md"])}))
    check("paths/symlinked-declaration-finding", _outcome(linked), "finding")
    noflow = _opf_fixture(base)
    shutil.rmtree(noflow / ".github")
    check("paths/missing-workflow-dir-finding", _outcome(noflow), "finding")
    otherflow = _opf_fixture(base, policy=_policy_text(**{"workflow-dir": '"ci/workflows"'}))
    check("paths/other-workflow-dir-finding", _outcome(otherflow), "finding")
    tree = _opf_fixture(base, policy=_policy_text(**{"excluded-trees": _toml_list(["tools/_vendor"])}))
    check("paths/excluded-tree-without-slash-finding", _outcome(tree), "finding")


def _surface_cases(base):
    extra = _opf_fixture(base)
    _write(extra, "tools/c.py", MAIN)
    check("entrypoints/unlisted-entrypoint-finding", _outcome(extra), "finding")
    check("entrypoints/unlisted-entrypoint-red-without-entrypoints", _without("entrypoints", extra), "pass")
    lib = _opf_fixture(base, policy=_policy_text(**{"guarded-surfaces": _toml_list(GUARDED + ["tools/lib.py"])}))
    check("entrypoints/listed-library-module-finding", _outcome(lib), "finding")
    vendored = _opf_fixture(base, policy=_policy_text(**{"excluded-trees": "[]"}))
    check("entrypoints/vendored-tree-read-once-not-excluded", _outcome(vendored), "finding")
    broken = _opf_fixture(base)
    _write(broken, "tools/broken.py", "def (\n")
    check("entrypoints/unparsable-python-cannot-evaluate", _outcome(broken), "cannot-evaluate")
    flow = _opf_fixture(base)
    _write(flow, ".github/workflows/other.yml", "jobs: {}\n")
    check("workflows/unlisted-workflow-finding", _outcome(flow), "finding")
    check("workflows/unlisted-workflow-red-without-workflows", _without("workflows", flow), "pass")
    pinned = _opf_fixture(base)
    _write(pinned, "enforcement/precommit/setup.yml", _pin("3.14"))
    check("pins/stray-pin-carrier-finding", _outcome(pinned), "finding")
    check("pins/stray-pin-carrier-red-without-pins", _without("pins", pinned), "pass")
    unpinned = _opf_fixture(base)
    _write(unpinned, "enforcement/ci/ci.yml", "steps: []\n")
    check("pins/template-without-pin-finding", _outcome(unpinned), "finding")
    stale = _opf_fixture(base)
    _write(stale, ".github/workflows/q.yml", "jobs:\n" + _pin("3.13"))
    check("pins/workflow-older-pin-finding", _outcome(stale), "finding")
    moved = _opf_fixture(base, policy=_policy_text(**{
        "workflow-files": _toml_list([".github/workflows/p.yml", ".github/workflows/q.yml"]),
        "host-patch-workflow": '".github/workflows/p.yml"'}))
    _write(moved, ".github/workflows/p.yml", "jobs:\n" + _pin("3.14"))
    check("pins/host-patch-outside-its-workflow-finding", _outcome(moved), "finding")
    spelled = _opf_fixture(base)
    _write(spelled, ".github/workflows/q.yml", "jobs:\n      - Python-Version: \"3.14.4\"  # host\n")
    check("pins/host-patch-in-its-workflow-passes", _outcome(spelled), "pass")
    for name, value in (("host-patch", "3.14.4"), ("other-patch", "3.14.1"), ("older-floor", "3.13")):
        adopt = _opf_fixture(base)
        _write(adopt, "enforcement/ci/ci.yml", "steps:\n" + _pin(value))
        check("templates/adopter-pins-" + name + "-finding", _outcome(adopt), "finding")
        if name == "host-patch":
            check("templates/adopter-pins-host-patch-red-without-templates", _without("templates", adopt), "pass")
    doctor = _opf_fixture(base)
    _write(doctor, "tools/doctor.py", _doctor_text("3.14.4"))
    check("templates/doctor-pins-host-patch-finding", _outcome(doctor), "finding")
    both = _opf_fixture(base, policy=_policy_text(**{"doctor-templates": _toml_list(["enforcement/ci/ci.yml",
                                                                                     "tools/doctor.py"])}))
    check("templates/adopter-and-doctor-overlap-finding", _outcome(both), "finding")
    check("templates/adopter-and-doctor-overlap-red-without-templates", _without("templates", both), "pass")
    flowtpl = _opf_fixture(base, policy=_policy_text(**{"adopter-templates": _toml_list(
        [".github/workflows/q.yml", "enforcement/ci/ci.yml"])}))
    check("templates/workflow-listed-as-template-finding", _outcome(flowtpl), "finding")
    doc = _opf_fixture(base)
    _write(doc, "README.md", "Python 3.14 or newer, as python3.\n")
    check("declarations/unlisted-statement-finding", _outcome(doc), "finding")
    check("declarations/unlisted-statement-red-without-declarations", _without("declarations", doc), "pass")
    html = _opf_fixture(base)
    _write(html, "site/index.html", "<p>Python&nbsp;3.14 or&#160;newer</p>\n")
    check("declarations/html-spaced-statement-seen", _outcome(html), "finding")
    silent = _opf_fixture(base)
    _write(silent, "spec/QUICK.md", "No floor stated here.\n")
    check("declarations/listed-file-states-nothing-finding", _outcome(silent), "finding")
    for name, text in (("older", "Python 3.13 or newer"), ("patch", "Python 3.14.4 or newer"),
                       ("twice", "Python 3.14 or newer; again Python 3.14 or newer")):
        wrong = _opf_fixture(base)
        _write(wrong, "spec/QUICK.md", text + ".\n")
        check("declarations/statement-" + name + "-finding", _outcome(wrong), "finding")
    twice = _opf_fixture(base, policy=_policy_text(**{"declaration-copies": '{"spec/QUICK.md" = 2}'}))
    _write(twice, "spec/QUICK.md", "Python 3.14 or newer; again Python 3.14 or newer.\n")
    check("declarations/declared-copies-pass", _outcome(twice), "pass")
    bad_table = _opf_fixture(base, table="format-version = 2\n")
    check("table/schema-cannot-evaluate", _outcome(bad_table), "cannot-evaluate")
    deep_table = _opf_fixture(base, table="x = " + "[" * 5000 + "]" * 5000 + "\n")
    check("table/deep-cannot-evaluate", _outcome(deep_table), "cannot-evaluate")
    gone = _opf_fixture(base, table=_table_text(("tools/gone.py", "f")))
    check("table/row-path-missing-finding", _outcome(gone), "finding")
    check("table/row-path-missing-red-without-table", _without("table", gone), "pass")
    repeated = _opf_fixture(base, table=_table_text(("tools/a.py", "f"), ("tools/a.py", "f")))
    check("table/row-repeated-finding", _outcome(repeated), "finding")


def _audit(event, args):
    if AUDIT_LOG is not None and event in AUDIT_EVENTS and args and isinstance(args[0], (str, bytes, os.PathLike)):
        AUDIT_LOG.append(os.fsdecode(args[0]))


def _live_copy_cases(base):
    """The live OPF tree copied alone into a directory with no parent repository files: the gate passes
    there, and every path it opens or lists while doing so lies under the copy."""
    global AUDIT_LOG
    copy = Path(tempfile.mkdtemp(dir=base)) / "opf"
    shutil.copytree(OPF_ROOT, copy, symlinks=True, ignore=shutil.ignore_patterns(*SKIPPED_DIR_NAMES))
    check("live-copy/no-parent-policy-beside-the-copy",
          [(copy.parent / rel).exists() for rel in (".aiqt/core/python-floor.toml", ".aiqt/python-host.toml")],
          [False, False])
    sys.addaudithook(_audit)
    AUDIT_LOG = []
    try:
        findings = evaluate(copy)
    except CannotEvaluate as exc:
        findings = ["cannot evaluate: {}".format(exc)]
    finally:
        opened, AUDIT_LOG = AUDIT_LOG, None
    check("live-copy/passes-without-a-parent", findings, [])
    inside = os.fspath(copy) + os.sep
    check("live-copy/reads-nothing-outside-the-opf-root",
          sorted({path for path in opened if path != os.fspath(copy) and not path.startswith(inside)}), [])
    check("live-copy/read-both-policies",
          [any(path.endswith(rel) for path in opened) for rel in (POLICY_REL, HOST_POLICY_REL)], [True, True])
    (copy / HOST_POLICY_REL).unlink()
    check("live-copy/host-policy-removed-cannot-evaluate", _outcome(copy), "cannot-evaluate")


def self_test():
    with tempfile.TemporaryDirectory() as base:
        _schema_cases(base)
        _host_cases(base)
        _floor_and_path_cases(base)
        _surface_cases(base)
        _live_copy_cases(base)
    check("usage/parent-mode-refused", main(["--parent-integration"]), 2)
    for failure in FAILURES:
        print("FAIL", failure)
    print("{} checks, {} failed".format(len(EXECUTED), len(FAILURES)))
    return 1 if FAILURES else 0


def main(argv):
    if argv == ["--self-test"]:
        return self_test()
    if argv != []:
        sys.stderr.write("usage: check_opf_python_floor_policy.py [--self-test]\n")
        return 2
    try:
        findings = evaluate(OPF_ROOT)
    except CannotEvaluate as exc:
        print("CANNOT EVALUATE: {}".format(exc))
        return 2
    for finding in findings:
        print("FINDING:", finding)
    if findings:
        print("OPF python-floor policy: {} finding(s)".format(len(findings)))
        return 1
    print("OPF python-floor policy: OK (floor and host-patch policies, read from the OPF tree only)")
    return 0


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-test"]:
        sys.exit(self_test())
    sys.exit(main(sys.argv[1:]))
