#!/usr/bin/env python3
"""Python host-patch pin checker, OPF tree (PYTHON-HOST-PIN): the OPF tree's own host-patch policy,
tools/python-host.toml, names the exact CPython patch release the OPF authoring host runs; this checker
binds that patch to the running interpreter, binds each host-patch job the OPF floor policy names
(host-patch-jobs in tools/python-floor.toml) to the guarded OPF tool it exists to run, and holds that
job in the OPF workflow (host-patch-workflow there) to the patch, this gate and those run lines. Every
path is relative to the OPF root (the directory that holds this tools/ directory), and the checker opens
no file of a parent repository: a missing OPF policy is cannot-evaluate, never a read of a parent's.
That bound is lexical, on the path text: a path is joined to the OPF root without resolving it, so a
symlinked directory inside the root can lead a read outside it.

Legs, in order:
  source       tools/python-host.toml is a regular UTF-8 file that parses and carries exactly the keys
               format-version (the integer 1) and python-host-patch (a "MAJOR.MINOR.PATCH" string with no
               leading zero and at most four digits in each component, PATCH_RE); tools/python-floor.toml
               is a regular UTF-8 file that parses and carries python-floor (a "MAJOR.MINOR" string),
               host-patch-jobs (a nonempty list of unique job ids, each a plain [A-Za-z_][A-Za-z0-9_-]*
               key), guarded-surfaces (a list of strings) and host-patch-workflow (a relative path with no
               empty, "." or ".." part and no NUL character, refused before any filesystem call reads
               it). Anything else is cannot-evaluate, and so is a source that tomllib refuses with a
               RecursionError (deep nesting), a bare ValueError (an over-long integer) or a MemoryError.
               The floor policy's other keys, and the full schema of both files, are held by
               tools/check_opf_python_floor_policy.py; this checker reads only the four keys it binds.
  patch        the host patch is a patch release of the floor: its MAJOR.MINOR equals python-floor. A
               mismatched patch is a finding.
  binding      each host-patch-jobs entry has a row in HOST_TOOLS, the run lines that job exists to run,
               and each HOST_TOOLS job is named by host-patch-jobs: a policy job with no row (an unknown
               job) and a row whose job the policy does not name are each a finding. Each run line is
               tokenized with shlex (a line shlex cannot tokenize is cannot-evaluate) and must be
               python3 -I -B followed by an OPF-root-relative tool path that guarded-surfaces lists (so
               the floor gate holds that tool to the canonical floor guard) and that is a regular file;
               any other run line is an unguarded run line, a finding.
  jobs         host-patch-workflow, read as a line model (job_findings, below), holds each host-patch
               job that HOST_TOOLS binds: a job missing from it is a finding, and so is one whose lines,
               blank and comment lines aside, are not JOB_TEMPLATE filled in: a job name, runs-on
               HOST_RUNNER, the checkout step, one Python setup step pinning exactly the host patch, then
               this gate in full mode (GATE_RUN, so the interpreter leg judges the job's own interpreter)
               and the job's HOST_TOOLS run lines, in order, each a step with a name, shell: bash and that
               run line, and nothing else (no env:, if:, defaults: or other key on the job or a step). A
               job host-patch-jobs does not name that pins the host patch, a job defined twice, and a
               workflow top-level key other than name, on, permissions and jobs (a workflow env: or
               defaults: reaches every step) are each a finding, and so is a HOST_TOOLS run line that is
               not plain YAML text, which no workflow line can carry as written. A policy job with no
               HOST_TOOLS row is the binding leg's finding and this leg skips it. Every line of the
               workflow but a blank line, a comment line and the lines of a run: block scalar is read by
               _line_refusal, and a line it does not read is cannot-evaluate: it reads list markers
               ("- "), then an optional block mapping key (plain or quoted) and its colon, then a value
               that is empty, a comment, a plain scalar, a quoted scalar or flow collection that closes
               on that line with only blanks or a comment after it, or, on a run: key alone, a block
               scalar header (| or >, an optional chomping indicator, no indentation indicator). So these
               are cannot-evaluate: a node that opens with & (an anchor), ! (a tag), * (an alias) or ?
               (an explicit key), in or out of a flow collection; a block mapping key holding a colon;
               a double-quoted scalar holding a backslash escape; a quoted scalar or flow collection that
               does not close on its line or is followed by other text (in a flow collection a # after a
               blank, or where a node starts, begins a comment, so a closer after it does not count); and
               a block scalar on any key but run:. A quote opens a quoted scalar only where a node
               starts, so an apostrophe inside a plain scalar is text. The lines after a run: block
               scalar header that are blank or more indented than its key are script text, skipped by
               every rule here. Residual: the leg reads lines, not YAML, so it accepts no host-patch job
               but JOB_TEMPLATE's text, but outside the host-patch jobs it does not validate YAML: a line
               nested under a top-level key other than jobs, or in another job, is held only to the line
               model and _line_refusal, so other malformed YAML there (a plain scalar holding ": ", say)
               is not detected. Over-rejection, disclosed: valid YAML in those shapes is refused too, and
               a continuation line of a plain scalar spread over lines is read as a line of its own, so
               one that opens with a bracket and is not a flow collection closed on its line (a shell
               test such as [ -f x ] && echo ok written as a continued plain scalar) is cannot-evaluate;
               write such a script as a run: block scalar. Another job is read for a host-patch pin
               written as a literal (PIN_VALUE_RE, every match on a line: the python-version key in any
               case, the value plain, single-quoted or double-quoted, with or without leading blanks); a
               python-version key whose value does not start on its line (PIN_LATER_RE) is
               cannot-evaluate, and so, by the rules above, is a pin with an anchor, tag or alias, under
               an explicit key, in a block scalar or in a double-quoted scalar with an escape. A pin
               through an expression, a matrix value, a version range opening with an operator (>=, say)
               or a version file (python-version-file) is not seen there.
  interpreter  the running interpreter (sys.version_info) is the host patch, a final release. A difference
               is a finding: run locally, this interpreter is not the committed host patch, so a passing
               local run does not predict the host-patch job; run in a host-patch job, that job did not
               run the pinned release.

  check_opf_host_pin.py                 run every leg over the OPF tree
  check_opf_host_pin.py --binding-only  run the source, patch, binding and jobs legs and skip the
                                        interpreter leg, for a run on any interpreter: the OPF runner
                                        and the parent repository's runner (tools/run_all_checks.sh) and
                                        quality workflow job opf-tooling-a, which need not run on the host
                                        patch and so judge the host-patch job from outside it
  check_opf_host_pin.py --self-test     fixture trees for every leg, each acceptance case at its
                                        boundaries, and red-on-revert: a scratch copy of this checker,
                                        loaded through importlib, with one rule removed passes the fixture
                                        the intact checker refuses

Adapted from the parent repository's tools/check_python_host_pin.py, and importing nothing from it.
Copied: the exit convention, CannotEvaluate, _read_text, the host policy's key set and PATCH_RE, the
parse mapping of TOMLDecodeError, ValueError, RecursionError and MemoryError to cannot-evaluate, the
interpreter leg and its final-release rule, and the job-to-suite table (the parent's HOST_SUITES, here
HOST_TOOLS, bound to the OPF floor policy's host-patch-jobs instead of a constant job list). The jobs
leg is not the parent's: in place of the parent's line model of its workflow, job rules and parser
emulation, it compares each host-patch job, line for line, with a template filled in from the policies
and HOST_TOOLS, so any departure is a finding without a rule of its own. Not carried: --jobs-only
(--binding-only skips the interpreter leg and runs the jobs leg) and the parser-capping and key-depth
emulation vectors, which exist for the parent's workflow parser.

Exit convention: 0 every leg run passes; 1 a finding; 2 usage or cannot-evaluate (a missing, unreadable,
non-regular, symlinked or non-UTF-8 policy or workflow, a malformed policy, a host-patch-workflow path
with a NUL character, a bound tool that is not a regular file, a run line shlex cannot tokenize, a
workflow line outside the jobs leg's line model or one _line_refusal does not read, a python-version key
in another job whose value does not start on its line, unavailable temporary storage). A workflow is
malformed for this checker only in those ways; see the jobs leg's residual.

Run this checker isolated: python3 -I -B tools/check_opf_host_pin.py
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: check_opf_host_pin.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import ast
import importlib.util
import os
import re
import shlex
import stat
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOST_SOURCE_REL = "tools/python-host.toml"
HOST_SOURCE_KEYS = {"format-version", "python-host-patch"}
FLOOR_SOURCE_REL = "tools/python-floor.toml"
# The floor policy's keys this checker binds; tools/check_opf_python_floor_policy.py holds the rest.
FLOOR_BOUND_KEYS = {"python-floor": str, "host-patch-jobs": list, "guarded-surfaces": list,
                    "host-patch-workflow": str}
PATCH_RE = re.compile(r"([1-9][0-9]{0,3})\.(0|[1-9][0-9]{0,3})\.(0|[1-9][0-9]{0,3})")
FLOOR_RE = re.compile(r"([1-9][0-9]{0,3})\.(0|[1-9][0-9]{0,3})")
JOB_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*")
# Each OPF host-patch job, with the ordered run lines it exists to run, each OPF-root-relative. The OPF
# floor policy's host-patch-jobs must name exactly these jobs. opf-host-patch runs the OPF tooling
# self-test on the host patch, as the parent's job of that name does.
HOST_TOOLS = {
    "opf-host-patch": ("python3 -I -B tools/opf.py --self-test",),
}
RUN_PREFIX = ("python3", "-I", "-B")
BINDING_ONLY_FLAG = "--binding-only"

# The jobs leg. Each host-patch job runs this gate in full mode first, so its interpreter leg judges the
# job's own interpreter, then the job's HOST_TOOLS run lines.
GATE_RUN = "python3 -I -B tools/check_opf_host_pin.py"
# On 2026-10-12 the Python versions manifest listed Linux x64 builds of the host patch for Ubuntu 22.04 and
# 24.04 only (as the parent workflow's host-patch jobs record), so ubuntu-latest could fail the job on an
# image without one.
HOST_RUNNER = "ubuntu-24.04"
WORKFLOW_TOP_KEYS = ("name", "on", "permissions", "jobs")
# A job's lines, blank and comment lines aside: JOB_NAME and STEP_NAME stand for a name line with any
# NAME_RE text, the PIN placeholder for the host patch, and each STEP_RUNS for one step per run line.
JOB_NAME = ("name", "    name: ")
STEP_NAME = ("name", "      - name: ")
JOB_TEMPLATE = (
    JOB_NAME,
    "    runs-on: " + HOST_RUNNER,
    "    steps:",
    "      - uses: actions/checkout@v4",
    "        with:",
    "          persist-credentials: false",
    "      - uses: actions/setup-python@v5",
    "        with:",
    "          python-version: '{patch}'",
)
STEP_RUNS = (STEP_NAME, "        shell: bash", "        run: {run}")
# Name text that YAML reads as itself: no ':' or '#', so neither a mapping nor a comment can start inside.
NAME_RE = re.compile(r"[A-Za-z0-9(][A-Za-z0-9 ,.()/_+-]*[A-Za-z0-9)]")
# A run line YAML reads as itself, plain words separated by single spaces.
PLAIN_RUN_RE = re.compile(r"[A-Za-z0-9_./=+-]+(?: [A-Za-z0-9_./=+-]+)*")
TOP_KEY_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_-]*):(?: .*)?")
JOB_HEADER_RE = re.compile(r"  ([A-Za-z_][A-Za-z0-9_-]*):")
# A literal pin in another job: the key in any case, an optional quote around the key and the value, and
# blanks around the colon and before the value, so a pin spelled differently only in those ways is still
# seen; whether a runner would honour such a spelling is not judged here. Every match on a line is read.
PIN_VALUE_RE = re.compile(r"python-version['\"]?[ ]*:[ ]*['\"]?[ ]*([0-9][0-9.]*)", re.IGNORECASE)
# A python-version key whose value does not start on its line (blanks or a comment follow the colon), so
# no match of PIN_VALUE_RE can read it.
PIN_LATER_RE = re.compile(r"python-version['\"]?[ ]*:[ ]*(?:#.*)?$", re.IGNORECASE)
# A character YAML reads, where a node starts, as a node property (an anchor or a tag), an alias or an
# explicit key; this line model reads none of them.
NODE_MARKS = {"&": "an anchor (&)", "!": "a tag (!)", "*": "an alias (*)", "?": "an explicit key (?)"}
# The one key whose value may be a block scalar: | or >, an optional chomping indicator, then blanks or a
# comment. Its more indented lines below are script text, not read as YAML.
BLOCK_KEY = "run"
BLOCK_HEADER_RE = re.compile(r"[|>][+-]?(?: +#.*| *)")
# What may follow a flow collection or quoted scalar closed on its line: blanks, or blanks and a comment.
LINE_END_RE = re.compile(r"(?: +#.*| *)")
# Characters a YAML reader may read as a line break or indentation this line model does not.
REFUSED_CHARS = {"\r": "a carriage return", "\t": "a tab", "\f": "a form feed", "\v": "a vertical tab",
                 "\x85": "U+0085 (next line)", "\u2028": "U+2028 (line separator)",
                 "\u2029": "U+2029 (paragraph separator)", "\ufeff": "U+FEFF (byte order mark)"}


class CannotEvaluate(Exception):
    """An input this checker cannot read or interpret; reported as exit 2, never as a pass."""


def _read_text(path):
    """Read a regular, non-symlink UTF-8 file, or raise CannotEvaluate."""
    try:
        mode = os.lstat(path).st_mode
    except OSError as exc:
        raise CannotEvaluate("{}: cannot stat: {}".format(path, exc))
    if not stat.S_ISREG(mode):
        raise CannotEvaluate("{}: not a regular file".format(path))
    try:
        return Path(path).read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise CannotEvaluate("{}: cannot read as UTF-8: {}".format(path, exc))


def _parse(root, rel):
    """One OPF policy file, parsed; any parse failure is cannot-evaluate, never a traceback."""
    try:
        return tomllib.loads(_read_text(root / rel))
    except tomllib.TOMLDecodeError as exc:
        raise CannotEvaluate("{}: does not parse: {}".format(rel, exc))
    # tomllib also raises a BARE ValueError on an integer literal past CPython's int-string limit, a
    # RecursionError on a deeply nested array or inline table or, in a release that bounds key depth, on a
    # key of more than 1000 dotted parts, and a MemoryError on a source too large for the memory left.
    except (ValueError, RecursionError, MemoryError) as exc:
        raise CannotEvaluate("{}: does not parse ({}): {}".format(rel, type(exc).__name__, exc))


def load_host_patch(root):
    """The OPF host policy's patch as (major, minor, micro), schema-checked."""
    data = _parse(root, HOST_SOURCE_REL)
    if set(data) != HOST_SOURCE_KEYS:
        raise CannotEvaluate("{}: keys {} (want exactly {})".format(
            HOST_SOURCE_REL, sorted(data), sorted(HOST_SOURCE_KEYS)))
    if type(data["format-version"]) is not int or data["format-version"] != 1:
        raise CannotEvaluate("{}: format-version must be the integer 1".format(HOST_SOURCE_REL))
    text = data["python-host-patch"]
    match = PATCH_RE.fullmatch(text) if isinstance(text, str) else None
    if match is None:
        raise CannotEvaluate("{}: python-host-patch must be a \"MAJOR.MINOR.PATCH\" string with at most four "
                             "digits in each component, got {!r:.60}".format(HOST_SOURCE_REL, text))
    return tuple(int(part) for part in match.groups())


def load_floor_binding(root):
    """The floor policy's python-floor as (major, minor), its host-patch-jobs, its guarded-surfaces and its
    host-patch-workflow."""
    data = _parse(root, FLOOR_SOURCE_REL)
    for key, kind in FLOOR_BOUND_KEYS.items():
        if key not in data or type(data[key]) is not kind:
            raise CannotEvaluate("{}: {} must be present and a {}".format(FLOOR_SOURCE_REL, key, kind.__name__))
    match = FLOOR_RE.fullmatch(data["python-floor"])
    if match is None:
        raise CannotEvaluate("{}: python-floor must be a \"MAJOR.MINOR\" string, got {!r:.60}".format(
            FLOOR_SOURCE_REL, data["python-floor"]))
    jobs = data["host-patch-jobs"]
    if not jobs or not all(isinstance(job, str) and JOB_RE.fullmatch(job) for job in jobs) \
            or len(set(jobs)) != len(jobs):
        raise CannotEvaluate("{}: host-patch-jobs must name at least one job id, each a plain "
                             "[A-Za-z_][A-Za-z0-9_-]* key, none twice".format(FLOOR_SOURCE_REL))
    surfaces = data["guarded-surfaces"]
    if not all(isinstance(item, str) for item in surfaces):
        raise CannotEvaluate("{}: guarded-surfaces must be a list of strings".format(FLOOR_SOURCE_REL))
    workflow = data["host-patch-workflow"]
    # Refused here, before any filesystem call: os.lstat raises ValueError, not OSError, on a NUL.
    if "\x00" in workflow:
        raise CannotEvaluate("{}: host-patch-workflow carries a NUL character, which no path can hold, got "
                             "{!r:.60}".format(FLOOR_SOURCE_REL, workflow))
    outside = workflow.startswith("/") or "\\" in workflow or any(part in ("", ".", "..")
                                                                  for part in workflow.split("/"))
    if outside:
        raise CannotEvaluate("{}: host-patch-workflow must be a relative path inside the OPF root with no "
                             "empty, \".\" or \"..\" part, got {!r:.60}".format(FLOOR_SOURCE_REL, workflow))
    return (int(match.group(1)), int(match.group(2))), tuple(jobs), frozenset(surfaces), workflow


def _patch_text(patch):
    return "%d.%d.%d" % tuple(patch[:3])


def patch_findings(patch, floor):
    """The host patch is a patch release of the floor."""
    if tuple(patch[:2]) == tuple(floor):
        return []
    return ["{}: python-host-patch {} is not a patch release of python-floor {}.{} in {}".format(
        HOST_SOURCE_REL, _patch_text(patch), floor[0], floor[1], FLOOR_SOURCE_REL)]


def _run_findings(root, job, run, surfaces):
    """One bound run line: python3 -I -B, then a guarded OPF tool that is a regular file."""
    try:
        tokens = shlex.split(run)
    except ValueError as exc:
        raise CannotEvaluate("HOST_TOOLS {}: run line {!r} does not tokenize: {}".format(job, run, exc))
    if tuple(tokens[:len(RUN_PREFIX)]) != RUN_PREFIX or len(tokens) <= len(RUN_PREFIX):
        return ["HOST_TOOLS {}: unguarded run line {!r}: not {} followed by a guarded OPF tool".format(
            job, run, " ".join(RUN_PREFIX))]
    tool = tokens[len(RUN_PREFIX)]
    if tool not in surfaces:
        return ["HOST_TOOLS {}: unguarded run line {!r}: {} is not a guarded-surfaces entry of {}".format(
            job, run, tool, FLOOR_SOURCE_REL)]
    _read_text(root / tool)
    return []


def binding_findings(root, jobs, surfaces):
    """Each policy host-patch job is bound to its run lines, each a guarded OPF tool, and no bound job is
    left out of the policy."""
    findings = []
    for job in jobs:
        if job not in HOST_TOOLS:
            findings.append("{}: host-patch-jobs names {}, an unknown job: HOST_TOOLS binds it to no "
                            "tool".format(FLOOR_SOURCE_REL, job))
            continue
        for run in HOST_TOOLS[job]:
            findings.extend(_run_findings(root, job, run, surfaces))
    for job in sorted(set(HOST_TOOLS) - set(jobs)):
        findings.append("{}: host-patch-jobs does not name {}, which HOST_TOOLS binds".format(
            FLOOR_SOURCE_REL, job))
    return findings


def _quoted_end(text, start):
    """The index just past the quoted scalar that opens at text[start], or None when it does not close on
    the line; a backslash in a double-quoted scalar escapes the character after it."""
    quote, index = text[start], start + 1
    while index < len(text):
        if quote == "\"" and text[index] == "\\":
            index += 2
        elif quote == "'" and text[index:index + 2] == "''":
            index += 2
        elif text[index] == quote:
            return index + 1
        else:
            index += 1
    return None


def _flow_refusal(text, start):
    """(None, end) when the flow collection that opens at text[start] closes on the line, its brackets
    matched, end the index just past it; otherwise (reason, None). A node starts at an opening bracket,
    after a comma and after a separating colon: only there does a quote open a quoted scalar and is &, !,
    * or ? refused, and a # there or after a blank starts a comment, so a closer after it does not count."""
    closers, index, node = [], start, True
    while index < len(text):
        char = text[index]
        if char == "#" and (node or text[index - 1] == " "):
            break
        if char in "[{":
            closers.append("]" if char == "[" else "}")
            node = True
        elif char in "]}":
            if closers.pop() != char:
                break
            if not closers:
                return None, index + 1
            node = False
        elif char == "," or (char == ":" and (text[index + 1:index + 2] in ("", " ", ",", "[", "]", "{", "}")
                                              or text[index - 1] in "'\"")):
            node = True
        elif node and char in NODE_MARKS:
            return "carries {} in a flow collection".format(NODE_MARKS[char]), None
        elif node and char in "'\"":
            end = _quoted_end(text, index)
            if end is None:
                break
            if char == "\"" and "\\" in text[index:end]:
                return "carries a double-quoted scalar with a backslash escape", None
            index, node = end, False
            continue
        elif char != " ":
            node = False
        index += 1
    return "opens a flow collection that does not close on its line", None


def _line_refusal(body):
    """How the line model reads body, a line's text after its indentation, neither blank nor a comment:
    (reason, None) when it does not read the line, otherwise (None, column), column the offset in body of
    a run: key whose value is a block scalar, else None. Read: list markers ("- "), then an optional
    block mapping key (plain or quoted, holding no colon) and its colon, then a value: nothing, a comment,
    a plain scalar, a quoted scalar or flow collection closed on the line and followed by blanks or a
    comment only, or, for a run: key alone, a block scalar header. A node opening with &, !, * or ?, a
    double-quoted scalar with a backslash escape, and any other block scalar are refused."""
    index, key, column = 0, None, None
    while body[index:index + 2] == "- " or body[index:] == "-":
        index += 1
        while body[index:index + 1] == " ":
            index += 1
    while True:
        char = body[index:index + 1]
        if char in ("", "#"):
            return None, None
        if char in NODE_MARKS:
            return "carries " + NODE_MARKS[char], None
        if char in "[{":
            reason, end = _flow_refusal(body, index)
            if reason is None and not LINE_END_RE.fullmatch(body[end:]):
                reason = "opens a flow collection that does not close on its line"
            return reason, None
        if char in "|>":
            if key != BLOCK_KEY:
                return "opens a block scalar on a key other than run:", None
            if not BLOCK_HEADER_RE.fullmatch(body[index:]):
                return "opens a block scalar with an indentation indicator or text after its header", None
            return None, column
        if char in "'\"":
            end = _quoted_end(body, index)
            if end is None:
                return "opens a quoted scalar that does not close on its line", None
            if char == "\"" and "\\" in body[index:end]:
                return "carries a double-quoted scalar with a backslash escape", None
            colon = re.match(r" *:(?= |$)", body[end:])
            if key is not None or colon is None:
                return (None if LINE_END_RE.fullmatch(body[end:]) else "carries text after a quoted scalar"), None
            found, after = body[index + 1:end - 1], end + colon.end()
        else:
            separator = re.search(r":(?= |$)| #", body[index:])
            if key is not None or separator is None or separator.group() == " #":
                return None, None
            found, after = body[index:index + separator.start()].rstrip(" "), index + separator.end()
        if ":" in found:
            return "has a key holding a colon", None
        key, column, index = found, index, after
        while body[index:index + 1] == " ":
            index += 1


def _workflow_lines(rel, text):
    """The workflow's top-level keys in order, each job's lines as (line number, line) with blank and
    comment lines dropped, and the ids of jobs defined more than once. The line model: a column-0 line is
    a top-level key line, the jobs region is what follows a line that is exactly jobs:, in that region a
    two-space line is exactly a job header, two spaces, a plain id and a colon, and every deeper line
    belongs to the job above it. Any other line is cannot-evaluate, never skipped, and so is a line
    _line_refusal does not read; the lines of a run: block scalar, each blank or more indented than its
    key, are script text and are skipped."""
    top, jobs, twice, current = [], {}, [], None
    in_jobs, block = False, None
    for number, line in enumerate(text.split("\n"), 1):
        for char, label in REFUSED_CHARS.items():
            if char in line:
                raise CannotEvaluate("{}:{}: carries {}, which a YAML reader may read as a line break or "
                                     "indentation this line model does not".format(rel, number, label))
        body = line.lstrip(" ")
        indent = len(line) - len(body)
        if block is not None and (not body or indent > block):
            continue
        block = None
        if not body or body.startswith("#"):
            continue
        reason, column = _line_refusal(body)
        if reason is not None:
            raise CannotEvaluate("{}:{}: {!r:.60} {}, which this line model does not read".format(
                rel, number, line, reason))
        if column is not None:
            block = indent + column
        if indent == 0:
            match = TOP_KEY_RE.fullmatch(line)
            if match is None:
                raise CannotEvaluate("{}:{}: {!r:.60} is not a top-level key line".format(rel, number, line))
            top.append(match.group(1))
            if match.group(1) == "jobs" and line != "jobs:":
                raise CannotEvaluate("{}:{}: jobs: must stand alone on its line".format(rel, number))
            in_jobs, current = line == "jobs:", None
        elif not in_jobs:
            continue
        elif indent == 2:
            match = JOB_HEADER_RE.fullmatch(line)
            if match is None:
                raise CannotEvaluate("{}:{}: {!r:.60} is not a job header (two spaces, a plain id and a "
                                     "colon, nothing after it)".format(rel, number, line))
            if match.group(1) in jobs:
                twice.append(match.group(1))
                current = []
            else:
                current = jobs.setdefault(match.group(1), [])
        elif indent == 1 or current is None:
            raise CannotEvaluate("{}:{}: {!r:.60} belongs to no job".format(rel, number, line))
        else:
            current.append((number, line))
    return top, jobs, twice


def expected_job(patch, runs):
    """A host-patch job's lines, blank and comment lines aside: JOB_TEMPLATE with the host patch, then one
    STEP_RUNS step for GATE_RUN and one for each run line. A name entry is a (\"name\", prefix) pair."""
    lines = [item if isinstance(item, tuple) else item.format(patch=_patch_text(patch)) for item in JOB_TEMPLATE]
    for run in (GATE_RUN,) + tuple(runs):
        lines += [item if isinstance(item, tuple) else item.format(run=run) for item in STEP_RUNS]
    return lines


def _job_line_findings(rel, job, got, want):
    """The first line where the job's lines, got, depart from want; a name entry accepts any NAME_RE text."""
    for index in range(max(len(got), len(want))):
        if index >= len(want):
            number, line = got[index]
            return ["{}:{}: host-patch job {} carries {!r} past its last expected line".format(
                rel, number, job, line)]
        expected = want[index] if isinstance(want[index], str) else want[index][1] + "<name>"
        if index >= len(got):
            return ["{}: host-patch job {} ends before its expected line {!r}".format(rel, job, expected)]
        number, line = got[index]
        if isinstance(want[index], str):
            if line == want[index]:
                continue
        elif line.startswith(want[index][1]) and NAME_RE.fullmatch(line[len(want[index][1]):]):
            continue
        return ["{}:{}: host-patch job {} departs from the host-patch policy and HOST_TOOLS: {!r}, want {!r}"
                .format(rel, number, job, line, expected)]
    return []


def job_findings(root, rel, patch, jobs):
    """Each host-patch job HOST_TOOLS binds is in the workflow rel as expected_job; no other job pins the
    host patch; no job is defined twice; the workflow's top-level keys are WORKFLOW_TOP_KEYS only."""
    top, bodies, twice = _workflow_lines(rel, _read_text(root / rel))
    if top.count("jobs") != 1:
        raise CannotEvaluate("{}: carries {} top-level jobs: keys (want exactly one)".format(rel, top.count("jobs")))
    findings = []
    for key in top:
        if key not in WORKFLOW_TOP_KEYS:
            findings.append("{}: top-level key {} is not one of {}: a workflow-level key reaches every step of "
                            "the host-patch jobs".format(rel, key, ", ".join(WORKFLOW_TOP_KEYS)))
    for key in sorted({key for key in top if top.count(key) > 1}):
        findings.append("{}: top-level key {} is written more than once".format(rel, key))
    for job in sorted(set(twice)):
        findings.append("{}: job {} is defined more than once".format(rel, job))
    for job in jobs:
        if HOST_TOOLS.get(job) is None:
            continue
        unplain = [run for run in HOST_TOOLS[job] if not PLAIN_RUN_RE.fullmatch(run)]
        if unplain:
            findings.append("HOST_TOOLS {}: run line {!r} is not plain YAML text, so no workflow line can "
                            "carry it as written".format(job, unplain[0]))
        elif job not in bodies:
            findings.append("{}: host-patch job {} is missing: {} names it and HOST_TOOLS binds it".format(
                rel, job, FLOOR_SOURCE_REL))
        else:
            findings.extend(_job_line_findings(rel, job, bodies[job], expected_job(patch, HOST_TOOLS[job])))
    for job, lines in bodies.items():
        if job in jobs:
            continue
        for number, line in lines:
            if PIN_LATER_RE.search(line):
                raise CannotEvaluate("{}:{}: {!r:.60} has a python-version key whose value does not start on "
                                     "its line, which this line model does not read".format(rel, number, line))
            for match in PIN_VALUE_RE.finditer(line):
                if match.group(1) == _patch_text(patch):
                    findings.append("{}:{}: job {} pins the host patch {}, but it is not a host-patch-jobs entry "
                                    "of {}".format(rel, number, job, _patch_text(patch), FLOOR_SOURCE_REL))
    return findings


def interpreter_findings(patch, version):
    """The running interpreter, version = (major, minor, micro, releaselevel), is the host patch, final."""
    if tuple(version[:3]) == tuple(patch) and version[3] == "final":
        return []
    return ["the running interpreter is Python {}.{}.{} ({}), but {} names the host patch {}: a passing "
            "local run does not predict the host-patch job; run on Python {}, or, when the host "
            "interpreter has changed, update {} and the host-patch job's pin together".format(
                *version[:4], HOST_SOURCE_REL, _patch_text(patch), _patch_text(patch), HOST_SOURCE_REL)]


def evaluate(root, version, binding_only=False):
    """Run every leg over root, judging the interpreter version; return (exit code, report lines).
    binding_only runs the source, patch, binding and jobs legs and skips the interpreter leg."""
    try:
        patch = load_host_patch(root)
        floor, jobs, surfaces, workflow = load_floor_binding(root)
        findings = []
        findings.extend(patch_findings(patch, floor))
        findings.extend(binding_findings(root, jobs, surfaces))
        findings.extend(job_findings(root, workflow, patch, jobs))
        if not binding_only:
            findings.extend(interpreter_findings(patch, version))
    except CannotEvaluate as exc:
        return 2, ["CANNOT EVALUATE: {}".format(exc)]
    if findings:
        return 1, ["FAIL: " + finding for finding in findings]
    report = ("PASS: OPF host patch {} ({}) is a patch release of python-floor {}.{}, each host-patch job "
              "of {} ({}) runs a guarded OPF tool, and {} sets that job up on the host patch and runs this "
              "gate, then those tools".format(
                  _patch_text(patch), HOST_SOURCE_REL, floor[0], floor[1], FLOOR_SOURCE_REL, ", ".join(jobs),
                  workflow))
    if binding_only:
        return 0, [report + " ({}: the interpreter leg did not run)".format(BINDING_ONLY_FLAG)]
    return 0, [report + ", and this interpreter is Python {}".format(_patch_text(version))]


# ---------------------------------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------------------------------

FAILURES = []
EXECUTED = []
_EXECUTED_SET = set()
HOST = (3, 14, 4, "final")
HOST_TEXT = "format-version = 1\npython-host-patch = \"3.14.4\"\n"
FIXTURE_TOOL = "tools/opf.py"
FIXTURE_WORKFLOW = ".github/workflows/quality.yml"
# Each rule, reverted in a scratch copy of this checker by replacing its one anchor.
REVERTS = {
    "patch-leg": ("        findings.extend(patch_findings(patch, floor))\n", ""),
    "binding-leg": ("        findings.extend(binding_findings(root, jobs, surfaces))\n", ""),
    "interpreter-leg": ("            findings.extend(interpreter_findings(patch, version))\n",
                        "            pass\n"),
    "unknown-job": ("        if job not in HOST_TOOLS:\n", "        if job not in HOST_TOOLS:\n            continue\n"
                                                              "        if False:\n"),
    "unbound-job": ("    for job in sorted(set(HOST_TOOLS) - set(jobs)):\n", "    for job in ():\n"),
    "run-prefix": ("    if tuple(tokens[:len(RUN_PREFIX)]) " "!= RUN_PREFIX or",
                   "    if False and tuple(tokens[:len(RUN_PREFIX)]) != RUN_PREFIX or"),
    "guarded-tool": ("    if tool not in surfaces:\n", "    if False:\n"),
    "final-release": ("tuple(patch) and version[3] == \"final\":", "tuple(patch):"),
    "host-keys": ("    if set(data) != HOST_SOURCE_KEYS:\n", "    if not HOST_SOURCE_KEYS <= set(data):\n"),
    "host-parent": ("HOST_SOURCE_REL = \"tools/" "python-host.toml\"",
                    "HOST_SOURCE_REL = \"../.aiqt/python-host.toml\""),
    "jobs-empty": ("    if not jobs " "or not all(", "    if not all("),
    "parse-mapping": ("memory left.\n    except (ValueError, RecursionError, MemoryError) as exc:\n",
                      "memory left.\n    except () as exc:\n"),
    "binding-only-forward": ("binding_only=argv " "== [BINDING_ONLY_FLAG])", "binding_only=False)"),
    "jobs-leg": ("        findings.extend(job_findings(root, workflow, patch, jobs))\n", ""),
    "job-missing": ("        elif job not in bodies:\n",
                    "        elif job not in bodies:\n            continue\n        elif False:\n"),
    "job-lines": ("            findings.extend(_job_line_findings(rel, job, bodies[job], expected_job(patch, "
                  "HOST_TOOLS[job])))\n", "            pass\n"),
    "top-keys": ("        if key not in WORKFLOW_TOP_KEYS:\n", "        if False:\n"),
    "top-twice": ("    for key in sorted({key for key in top if top.count(key) > 1}):\n", "    for key in ():\n"),
    "job-twice": ("    for job in sorted(set(twice)):\n", "    for job in ():\n"),
    "outside-pin": ("                if match.group(1) == _patch_text(patch):\n", "                if False:\n"),
    "workflow-path": ("    if outside:\n", "    if False:\n"),
    "line-breaks": ("        for char, label in REFUSED_CHARS.items():\n", "        for char, label in ():\n"),
    "name-text": ("        elif line.startswith(want[index][1]) and NAME_RE.fullmatch(line[len(want[index][1]):]):\n",
                  "        elif line.startswith(want[index][1]):\n"),
    "pin-from-policy": ("item.format(patch=" "_patch_text(patch))", "item.format(patch=\"3.14.4\")"),
    "unplaced-line": ("        elif indent == 1 or current is None:\n",
                      "        elif indent == 1 or current is None:\n            continue\n        elif False:\n"),
    "plain-run": ("        unplain = [run for run in HOST_TOOLS[job] if not PLAIN_RUN_RE.fullmatch(run)]\n",
                  "        unplain = []\n"),
    "flow-close": ("            reason, end = _flow_refusal(body, index)\n", "            return None, None\n"),
    "pin-any-case": ("[0-9][0-9.]*)\", re.IGNORECASE)", "[0-9][0-9.]*)\")"),
    "pin-blanks": ("['\\\"]?[ ]*([0-9][0-9.]*)\", re", "['\\\"]?([0-9][0-9.]*)\", re"),
    "flow-comment": ("        if char == \"#\" and (node or text[index - 1] == \" \"):\n            break\n", ""),
    "flow-token-quote": ("        elif node and char in \"'\\\"\":\n", "        elif char in \"'\\\"\":\n"),
    "flow-node-marks": ("        elif node and char in NODE_MARKS:\n", "        elif False:\n"),
    "flow-escape": ("            if char == \"\\\"\" and \"\\\\\" in text[index:end]:\n", "            if False:\n"),
    "node-marks": ("        if char in NODE_MARKS:\n", "        if False:\n"),
    "key-colon": ("        if \":\" in found:\n", "        if False:\n"),
    "quoted-close": ("                return \"opens a quoted scalar that does not close on its line\", None\n",
                     "                return None, None\n"),
    "quoted-escape": ("            if char == \"\\\"\" and \"\\\\\" in body[index:end]:\n", "            if False:\n"),
    "block-key": ("            if key != BLOCK_KEY:\n", "            if False:\n"),
    "block-header": ("            if not BLOCK_HEADER_RE.fullmatch(body[index:]):\n", "            if False:\n"),
    "block-content": ("            continue\n        block = None\n", "            pass\n        block = None\n"),
    "block-end": ("        block = None\n        if not body", "        if not body"),
    "pin-every-match": ("            for match in PIN_VALUE_RE.finditer(line):\n",
                        "            for match in filter(None, [PIN_VALUE_RE.search(line)]):\n"),
    "pin-later": ("            if PIN_LATER_RE.search(line):\n", "            if False:\n"),
    "flow-text-after": ("            if reason is None and not LINE_END_RE.fullmatch(body[end:]):\n",
                        "            if False:\n"),
    "quoted-text-after": ("(None if LINE_END_RE.fullmatch(body[end:]) else \"carries text after a quoted scalar\")",
                          "None"),
}


def check(name, got, want):
    if name in _EXECUTED_SET:
        print("SELF-TEST HARNESS ERROR: duplicate check id {!r}".format(name), file=sys.stderr)
        sys.exit(2)
    _EXECUTED_SET.add(name)
    EXECUTED.append(name)
    if got != want:
        FAILURES.append("{}: got {!r}, want {!r}".format(name, got, want))


def _write(root, rel, text):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))


def _floor_text(floor="3.14", jobs=("opf-host-patch",), surfaces=(FIXTURE_TOOL,), workflow=FIXTURE_WORKFLOW):
    return ("format-version = 1\npython-floor = \"{}\"\nhost-patch-jobs = [{}]\nguarded-surfaces = [{}]\n"
            "host-patch-workflow = \"{}\"\ncompleteness-check = true\n").format(
        floor, ", ".join('"{}"'.format(job) for job in jobs), ", ".join('"{}"'.format(item) for item in surfaces),
        workflow)


def _host_text(patch="3.14.4"):
    return "format-version = 1\npython-host-patch = \"{}\"\n".format(patch)


# The fixture workflow: a floor job that runs the OPF runner, then the host-patch job as the OPF workflow
# writes it, its pin and run lines filled in when the fixture is written.
_WORKFLOW_HEAD = (
    "name: OPF Quality",
    "",
    "on:",
    "  push:",
    "    branches: [main]",
    "",
    "permissions:",
    "  contents: read",
    "",
    "jobs:",
    "  opf-checks:",
    "    name: OPF checks",
    "    runs-on: ubuntu-latest",
    "    steps:",
    "      - uses: actions/checkout@v4",
    "        with:",
    "          persist-credentials: false",
    "",
    "      - uses: actions/setup-python@v5",
    "        with:",
    "          python-version: '3.14'",
    "",
    "      - name: OPF runner",
    "        shell: bash",
    "        run: bash tools/run_all_checks.sh",
    "",
    "  # The host-patch job.",
    "  opf-host-patch:",
    "    name: OPF tooling (host patch)",
    "    runs-on: ubuntu-24.04",
    "    steps:",
    "      - uses: actions/checkout@v4",
    "        with:",
    "          persist-credentials: false",
    "",
    "      - uses: actions/setup-python@v5",
    "        with:",
    "          python-version: '{patch}'",
    "",
    "      - name: OPF host pin",
    "        shell: bash",
    "        run: python3 -I -B tools/check_opf_host_pin.py",
)


def _workflow_text(patch="3.14.4", edits=()):
    """The fixture workflow on patch with the current HOST_TOOLS run lines, then each (old, new) edit,
    whose old text must occur exactly once."""
    lines = [line.format(patch=patch) if "{patch}" in line else line for line in _WORKFLOW_HEAD]
    for number, run in enumerate(HOST_TOOLS["opf-host-patch"], 1):
        lines += ["", "      - name: Host-patch run {}".format(number), "        shell: bash", "        run: " + run]
    text = "\n".join(lines) + "\n"
    for old, new in edits:
        if text.count(old) != 1:
            raise CannotEvaluate("fixture workflow edit {!r} matches {} times".format(old, text.count(old)))
        text = text.replace(old, new)
    return text


_FIXTURES = []


def _fixture(base, host=None, floor=None, files=None):
    """An OPF tree under base/tN/opf holding both policies, the bound tool and the fixture workflow on the
    host policy's patch (3.14.4 when it names none), then files over it; a files value of None removes
    that file."""
    _FIXTURES.append(None)
    root = base / "t{}".format(len(_FIXTURES)) / "opf"
    host = HOST_TEXT if host is None else host
    named = re.search(r"python-host-patch = \"([0-9.]+)\"", host)
    _write(root, HOST_SOURCE_REL, host)
    _write(root, FLOOR_SOURCE_REL, _floor_text() if floor is None else floor)
    _write(root, FIXTURE_TOOL, "import sys\n")
    _write(root, FIXTURE_WORKFLOW, _workflow_text("3.14.4" if named is None else named.group(1)))
    for rel, text in (files or {}).items():
        if text is None:
            (root / rel).unlink()
        else:
            _write(root, rel, text)
    return root


def _has(lines, marker):
    return any(marker in line for line in lines)


def _outcome(module, root, version, marker, binding_only=False):
    code, lines = module.evaluate(root, version, binding_only)
    return code, _has(lines, marker)


def _mutant(base, name, anchor, replacement):
    """A scratch copy of this checker with anchor replaced once, loaded through importlib."""
    text = _read_text(Path(__file__).resolve())
    if text.count(anchor) != 1:
        raise CannotEvaluate("revert anchor for {} occurs {} times".format(name, text.count(anchor)))
    path = base / "mutants" / (name.replace("-", "_") + ".py")
    _write(base / "mutants", path.name, text.replace(anchor, replacement))
    spec = importlib.util.spec_from_file_location("opf_host_pin_" + name.replace("-", "_"), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _injected(kind):
    """A tomllib.loads stand-in that raises kind, as tomllib does on the inputs _parse names."""
    def loads(text):
        raise kind("injected: tomllib refused the source")
    return loads


def _parse_refusal(module, root, kind):
    """module.evaluate over root with tomllib.loads raising kind: (exit, cannot-evaluate line) or the name
    of the class that escaped."""
    from unittest import mock
    with mock.patch("tomllib.loads", _injected(kind)):
        try:
            code, lines = module.evaluate(root, HOST)
        except (ValueError, RecursionError, MemoryError) as exc:
            return "raised " + type(exc).__name__
    return code, _has(lines, "does not parse (" + kind.__name__ + ")")


def _source_cases(base, me):
    check("control/pass", _outcome(me, _fixture(base), HOST, "and this interpreter is Python 3.14.4"), (0, True))
    check("source/missing-host-policy",
          _outcome(me, _fixture(base, files={HOST_SOURCE_REL: None}), HOST, "cannot stat"), (2, True))
    check("source/missing-floor-policy",
          _outcome(me, _fixture(base, files={FLOOR_SOURCE_REL: None}), HOST, "cannot stat"), (2, True))
    # A parent repository's host policy one level above the OPF root never stands in for the OPF one.
    nested = _fixture(base, files={HOST_SOURCE_REL: None})
    _write(nested.parent, ".aiqt/python-host.toml", HOST_TEXT)
    check("source/host-never-parent", _outcome(me, nested, HOST, HOST_SOURCE_REL), (2, True))
    symlinked = _fixture(base, files={HOST_SOURCE_REL: None, "tools/real-host.toml": HOST_TEXT})
    os.symlink("real-host.toml", symlinked / HOST_SOURCE_REL)
    check("source/host-symlink", _outcome(me, symlinked, HOST, "not a regular file"), (2, True))
    for check_id, host, marker in (
            ("source/host-malformed-toml", "format-version = 1\npython-host-patch = \n", "does not parse"),
            ("source/host-extra-key", HOST_TEXT + "python-floor = \"3.14\"\n", "want exactly"),
            ("source/host-missing-key", "format-version = 1\n", "want exactly"),
            ("source/host-format-version-2", "format-version = 2\npython-host-patch = \"3.14.4\"\n",
             "format-version must be"),
            ("source/host-format-version-bool", "format-version = true\npython-host-patch = \"3.14.4\"\n",
             "format-version must be"),
            ("source/host-patch-minor-only", _host_text("3.14"), "MAJOR.MINOR.PATCH"),
            ("source/host-patch-leading-zero", _host_text("3.14.04"), "MAJOR.MINOR.PATCH"),
            ("source/host-patch-five-digits", _host_text("3.14.10000"), "MAJOR.MINOR.PATCH"),
            ("source/host-patch-not-string", "format-version = 1\npython-host-patch = 3.14\n",
             "MAJOR.MINOR.PATCH")):
        check(check_id, _outcome(me, _fixture(base, host=host), HOST, marker), (2, True))
    root = _fixture(base)
    (root / HOST_SOURCE_REL).write_bytes(b"format-version = 1\n# \xff\n")
    check("source/host-not-utf8", _outcome(me, root, HOST, "cannot read as UTF-8"), (2, True))
    for check_id, floor, marker in (
            ("source/floor-malformed-toml", "python-floor = \n", "does not parse"),
            ("source/floor-jobs-missing", "python-floor = \"3.14\"\nguarded-surfaces = []\n",
             "host-patch-jobs must be present"),
            ("source/floor-jobs-not-list", "python-floor = \"3.14\"\nhost-patch-jobs = \"opf-host-patch\"\n"
             "guarded-surfaces = []\n", "host-patch-jobs must be present"),
            ("source/floor-jobs-empty", _floor_text(jobs=()), "at least one job id"),
            ("source/floor-jobs-bad-id", _floor_text(jobs=("opf host",)), "at least one job id"),
            ("source/floor-jobs-twice", _floor_text(jobs=("opf-host-patch", "opf-host-patch")),
             "none twice"),
            ("source/floor-shape", _floor_text(floor="3"), "\"MAJOR.MINOR\""),
            ("source/floor-surfaces-missing", "python-floor = \"3.14\"\nhost-patch-jobs = [\"opf-host-patch\"]\n",
             "guarded-surfaces must be present")):
        check(check_id, _outcome(me, _fixture(base, floor=floor), HOST, marker), (2, True))
    # Each class _parse maps, injected at tomllib.loads (real memory exhaustion cannot be provoked safely).
    root = _fixture(base)
    for check_id, kind in (("source/injected-recursion-error-cannot-evaluate", RecursionError),
                           ("source/injected-memory-error-cannot-evaluate", MemoryError),
                           ("source/injected-value-error-cannot-evaluate", ValueError)):
        check(check_id, _parse_refusal(me, root, kind), (2, True))


def _patch_cases(base, me):
    # The patch leg's two ends: the floor's minor is the only minor accepted, from .0 to the largest
    # four-digit patch; one minor below and one above are each a finding. Each runs on its own patch, so
    # only the patch leg can judge it.
    for check_id, patch, version, want in (
            ("patch/3.13.9999-below-floor", "3.13.9999", (3, 13, 9999, "final"), (1, True)),
            ("patch/3.14.0-lowest", "3.14.0", (3, 14, 0, "final"), (0, False)),
            ("patch/3.14.9999-highest", "3.14.9999", (3, 14, 9999, "final"), (0, False)),
            ("patch/3.15.0-above-floor", "3.15.0", (3, 15, 0, "final"), (1, True)),
            ("patch/4.14.4-other-major", "4.14.4", (4, 14, 4, "final"), (1, True))):
        check(check_id, _outcome(me, _fixture(base, host=_host_text(patch)), version,
                                 "is not a patch release of python-floor"), want)


def _binding_cases(base, me):
    check("binding/unknown-job",
          _outcome(me, _fixture(base, floor=_floor_text(jobs=("opf-host-patch", "opf-other"))), HOST,
                   "host-patch-jobs names opf-other, an unknown job"), (1, True))
    check("binding/bound-job-not-named",
          _outcome(me, _fixture(base, floor=_floor_text(jobs=("opf-other",))), HOST,
                   "host-patch-jobs does not name opf-host-patch"), (1, True))
    check("binding/unguarded-tool",
          _outcome(me, _fixture(base, floor=_floor_text(surfaces=("tools/other.py",))), HOST,
                   "tools/opf.py is not a guarded-surfaces entry"), (1, True))
    check("binding/guarded-tool-missing",
          _outcome(me, _fixture(base, files={FIXTURE_TOOL: None}), HOST, "cannot stat"), (2, True))
    root = _fixture(base)
    for check_id, run, want in (
            ("binding/run-without-isolation", "python3 tools/opf.py --self-test", (1, True)),
            ("binding/run-other-interpreter", "python -I -B tools/opf.py --self-test", (1, True)),
            ("binding/run-flags-reordered", "python3 -B -I tools/opf.py --self-test", (1, True)),
            ("binding/run-shell-script", "bash tools/run_all_checks.sh", (1, True)),
            ("binding/run-prefix-only", "python3 -I -B", (1, True)),
            ("binding/run-dotted-path", "python3 -I -B ./tools/opf.py --self-test", (1, True))):
        saved = dict(me.HOST_TOOLS)
        me.HOST_TOOLS["opf-host-patch"] = (run,)
        try:
            check(check_id, _outcome(me, root, HOST, "unguarded run line"), want)
        finally:
            me.HOST_TOOLS.clear()
            me.HOST_TOOLS.update(saved)
    saved = dict(me.HOST_TOOLS)
    me.HOST_TOOLS["opf-host-patch"] = ("python3 -I -B \"tools/opf.py",)
    try:
        check("binding/run-untokenizable", _outcome(me, root, HOST, "does not tokenize"), (2, True))
    finally:
        me.HOST_TOOLS.clear()
        me.HOST_TOOLS.update(saved)


def _interpreter_cases(base, me):
    root = _fixture(base)
    for check_id, version, want in (
            ("interpreter/older-patch", (3, 14, 3, "final"), (1, True)),
            ("interpreter/newer-patch", (3, 14, 5, "final"), (1, True)),
            ("interpreter/other-minor", (3, 15, 4, "final"), (1, True)),
            ("interpreter/candidate-of-host-patch", (3, 14, 4, "candidate"), (1, True)),
            ("interpreter/host-patch-final", (3, 14, 4, "final"), (0, False))):
        check(check_id, _outcome(me, root, version, "the running interpreter is Python"), want)
    check("binding-only/skips-interpreter",
          _outcome(me, root, (3, 14, 5, "final"), "the interpreter leg did not run", binding_only=True),
          (0, True))
    check("binding-only/judges-binding",
          _outcome(me, _fixture(base, floor=_floor_text(jobs=("opf-host-patch", "opf-other"))),
                   (3, 14, 5, "final"), "an unknown job", binding_only=True), (1, True))


_GATE_LINE = "        run: python3 -I -B tools/check_opf_host_pin.py\n"
_RUN_LINE = "        run: python3 -I -B tools/opf.py --self-test\n"
_HOST_HEADER = "  opf-host-patch:\n"
_HOST_PIN = "          python-version: '3.14.4'\n"
_HOST_NAME = "    name: OPF tooling (host patch)\n"
_OTHER_JOB = ("  other:\n    runs-on: ubuntu-24.04\n    steps:\n      - uses: actions/setup-python@v5\n"
              "        with:\n          python-version: '{}'\n\n  # The host-patch job.\n")
_BRANCHES = "    branches: [main]\n"


def _other_job(steps):
    """The edit that places a job other, its steps the given lines, before the host-patch job."""
    return ("  # The host-patch job.\n",
            "  other:\n    runs-on: ubuntu-24.04\n    steps:\n" + "".join(steps) + "\n  # The host-patch job.\n")


def _setup_with(text):
    """The edit that places a job other whose Python setup step's with: block holds text."""
    return _other_job(("      - uses: actions/setup-python@v5\n", "        with:\n", "          " + text + "\n"))


def _workflow_fixture(base, edits, host=None):
    """A fixture whose workflow is the fixture workflow with edits applied."""
    return _fixture(base, host=host, files={FIXTURE_WORKFLOW: _workflow_text(edits=edits)})


def _without_host_job(base):
    """A fixture whose workflow carries the floor job only."""
    text = _workflow_text()
    return _fixture(base, files={FIXTURE_WORKFLOW: text[:text.index("  # The host-patch job.\n")]})


def _duplicated_job(base):
    """A fixture whose workflow defines the host-patch job twice, the second an exact copy of the first."""
    text = _workflow_text()
    return _fixture(base, files={FIXTURE_WORKFLOW: text + "\n" + text[text.index(_HOST_HEADER):]})


def _with_host_tools(me, runs, build):
    """build() with me.HOST_TOOLS["opf-host-patch"] set to runs, restored afterwards."""
    saved = dict(me.HOST_TOOLS)
    me.HOST_TOOLS["opf-host-patch"] = runs
    try:
        return build()
    finally:
        me.HOST_TOOLS.clear()
        me.HOST_TOOLS.update(saved)


def _jobs_cases(base, me):
    """The jobs leg: each departure of a host-patch job from the policies and HOST_TOOLS is a finding,
    blank lines, comment lines and name text aside, and a workflow outside the line model is
    cannot-evaluate."""
    for check_id, edits, marker, want in (
            ("jobs/missing-job", ((_HOST_HEADER, "  opf-renamed:\n"),),
             "host-patch job opf-host-patch is missing", (1, True)),
            ("jobs/setup-patch-older", ((_HOST_PIN, "          python-version: '3.14.3'\n"),), "departs from",
             (1, True)),
            ("jobs/setup-patch-newer", ((_HOST_PIN, "          python-version: '3.14.5'\n"),), "departs from",
             (1, True)),
            ("jobs/setup-patch-floor", ((_HOST_PIN, "          python-version: '3.14'\n"),), "departs from",
             (1, True)),
            ("jobs/setup-patch-double-quoted", ((_HOST_PIN, "          python-version: \"3.14.4\"\n"),),
             "departs from", (1, True)),
            ("jobs/gate-binding-only", ((_GATE_LINE, _GATE_LINE[:-1] + " --binding-only\n"),), "departs from",
             (1, True)),
            ("jobs/gate-self-test", ((_GATE_LINE, _GATE_LINE[:-1] + " --self-test\n"),), "departs from", (1, True)),
            ("jobs/gate-step-missing", (("      - name: OPF host pin\n        shell: bash\n" + _GATE_LINE, ""),),
             "departs from", (1, True)),
            ("jobs/gate-after-run-line", ((_GATE_LINE, "        run: SWAP\n"), (_RUN_LINE, _GATE_LINE),
                                          ("        run: SWAP\n", _RUN_LINE)), "departs from", (1, True)),
            ("jobs/run-line-other", ((_RUN_LINE, _RUN_LINE[:-1] + "-group core\n"),), "departs from", (1, True)),
            ("jobs/run-line-missing", (("\n      - name: Host-patch run 1\n        shell: bash\n" + _RUN_LINE, ""),),
             "ends before its expected line", (1, True)),
            ("jobs/run-line-extra", ((_RUN_LINE, _RUN_LINE + "\n      - name: Extra\n        shell: bash\n"
                                                             "        run: python3 -I -B tools/opf.py\n"),),
             "past its last expected line", (1, True)),
            ("jobs/job-env", ((_HOST_NAME, _HOST_NAME + "    env:\n      BASH_ENV: /dev/null\n"),), "departs from",
             (1, True)),
            ("jobs/job-if", ((_HOST_HEADER, _HOST_HEADER + "    if: false\n"),), "departs from", (1, True)),
            ("jobs/step-env", ((_GATE_LINE, _GATE_LINE + "        env:\n          BASH_ENV: /dev/null\n"),),
             "departs from", (1, True)),
            ("jobs/runs-on-latest", (("    runs-on: ubuntu-24.04\n", "    runs-on: ubuntu-latest\n"),),
             "departs from", (1, True)),
            ("jobs/trailing-space", ((_GATE_LINE, _GATE_LINE[:-1] + " \n"),), "departs from", (1, True)),
            ("jobs/name-with-colon", ((_HOST_NAME, "    name: OPF: host patch\n"),), "departs from", (1, True)),
            ("jobs/name-with-hash", (("      - name: OPF host pin\n", "      - name: OPF host pin #x\n"),),
             "departs from", (1, True)),
            ("jobs/top-level-env", (("permissions:\n", "env:\n  BASH_ENV: /dev/null\n\npermissions:\n"),),
             "top-level key env is not one of", (1, True)),
            ("jobs/top-level-defaults", (("permissions:\n", "defaults:\n  run:\n    shell: sh\n\npermissions:\n"),),
             "top-level key defaults is not one of", (1, True)),
            ("jobs/top-level-twice", (("permissions:\n", "permissions:\n  actions: read\n\npermissions:\n"),),
             "top-level key permissions is written more than once", (1, True)),
            ("jobs/other-job-pins-host-patch", (("  # The host-patch job.\n", _OTHER_JOB.format("3.14.4")),),
             "job other pins the host patch 3.14.4, but it is not a host-patch-jobs entry", (1, True)),
            ("jobs/other-job-pins-floor", (("  # The host-patch job.\n", _OTHER_JOB.format("3.14")),),
             "not a host-patch-jobs entry", (0, False)),
            ("jobs/blank-and-comment-lines-aside", ((_GATE_LINE, _GATE_LINE + "\n\n        # a comment\n"),),
             "departs from", (0, False)),
            ("jobs/name-text-free", (("      - name: OPF host pin\n", "      - name: Gate, full mode (host)\n"),),
             "departs from", (0, False)),
            ("jobs/carriage-return-cannot-evaluate", ((_GATE_LINE, _GATE_LINE[:-1] + "\r\n"),),
             "carries a carriage return", (2, True)),
            ("jobs/tab-cannot-evaluate", (("    runs-on: ubuntu-24.04\n", "\truns-on: ubuntu-24.04\n"),),
             "carries a tab", (2, True)),
            ("jobs/header-comment-cannot-evaluate", ((_HOST_HEADER, "  opf-host-patch: # host\n"),),
             "is not a job header", (2, True)),
            ("jobs/flow-job-cannot-evaluate", ((_HOST_HEADER, "  opf-host-patch: {}\n"),), "is not a job header",
             (2, True)),
            ("jobs/jobs-key-not-alone-cannot-evaluate", (("jobs:\n", "jobs: # all\n"),), "must stand alone",
             (2, True)),
            ("jobs/no-jobs-key-cannot-evaluate", (("jobs:\n", "work:\n"),), "carries 0 top-level jobs: keys",
             (2, True)),
            ("jobs/document-marker-cannot-evaluate", (("name: OPF Quality\n", "---\nname: OPF Quality\n"),),
             "is not a top-level key line", (2, True)),
            ("jobs/line-before-job-cannot-evaluate", (("jobs:\n", "jobs:\n    stray: 1\n"),), "belongs to no job",
             (2, True)),
            ("jobs/one-space-line-cannot-evaluate", ((_HOST_HEADER, _HOST_HEADER + " x: 1\n"),),
             "belongs to no job", (2, True)),
            # A flow collection must close on its own line, a quote read where a node starts: unclosed on
            # the on: key and nested under it, closed with text after it, or closed by the wrong bracket is
            # cannot-evaluate; one closed on its line, brackets inside quotes and a trailing comment
            # included, passes.
            ("jobs/flow-on-key-unclosed-cannot-evaluate", (("on:\n  push:\n", "on: [\n  push:\n"),),
             "opens a flow collection", (2, True)),
            ("jobs/flow-nested-unclosed-cannot-evaluate", (("    branches: [main]\n", "    branches: [main\n"),),
             "opens a flow collection", (2, True)),
            ("jobs/flow-text-after-close-cannot-evaluate",
             (("    branches: [main]\n", "    branches: [main] x\n"),), "opens a flow collection", (2, True)),
            ("jobs/flow-wrong-closer-cannot-evaluate", (("    branches: [main]\n", "    branches: [main}\n"),),
             "opens a flow collection", (2, True)),
            ("jobs/flow-closed-on-its-line-passes", (("on:\n  push:\n    branches: [main]\n", "on: [push]\n"),),
             "opens a flow collection", (0, False)),
            ("jobs/flow-quoted-brackets-pass",
             (("    branches: [main]\n", "    branches: ['ma]in', \"x[\", {a: b}]  # c\n"),),
             "opens a flow collection", (0, False)),
            # A literal host-patch pin in another job, its key in another case or its value led by blanks.
            ("jobs/other-job-pin-key-any-case",
             (("  # The host-patch job.\n", _OTHER_JOB.replace("python-version", "Python-Version").format("3.14.4")),),
             "job other pins the host patch 3.14.4", (1, True)),
            ("jobs/other-job-pin-leading-blanks", (("  # The host-patch job.\n", _OTHER_JOB.format("  3.14.4")),),
             "job other pins the host patch 3.14.4", (1, True)),
            # A node property, alias or explicit key, a key holding a colon, a quoted scalar left open or
            # with an escape or text after it, and a block scalar on any key but run: are each refused.
            ("jobs/flow-anchor-on-key-cannot-evaluate", (("on:\n  push:\n", "on: &a [\n  push:\n"),),
             "carries an anchor (&)", (2, True)),
            ("jobs/flow-tag-on-key-cannot-evaluate", (("on:\n  push:\n", "on: !!seq [\n  push:\n"),),
             "carries a tag (!)", (2, True)),
            ("jobs/flow-anchor-nested-cannot-evaluate", ((_BRANCHES, "    branches: &b [main\n"),),
             "carries an anchor (&)", (2, True)),
            ("jobs/key-with-colon-unclosed-cannot-evaluate", ((_BRANCHES, "    a:b: [main\n"),),
             "has a key holding a colon", (2, True)),
            ("jobs/key-with-colon-cannot-evaluate", ((_BRANCHES, "    a:b: [main]\n"),), "has a key holding a colon",
             (2, True)),
            ("jobs/quoted-key-with-colon-cannot-evaluate", ((_BRANCHES, "    'a:b': [main]\n"),),
             "has a key holding a colon", (2, True)),
            ("jobs/explicit-key-cannot-evaluate", ((_BRANCHES, "    ? [main\n"),), "carries an explicit key (?)",
             (2, True)),
            ("jobs/alias-cannot-evaluate", ((_BRANCHES, "    branches: *b\n"),), "carries an alias (*)", (2, True)),
            ("jobs/flow-closer-in-comment-cannot-evaluate", ((_BRANCHES, "    branches: [main # ]\n"),),
             "opens a flow collection", (2, True)),
            ("jobs/flow-anchor-inside-cannot-evaluate", ((_BRANCHES, "    branches: [main, &a b]\n"),),
             "carries an anchor (&) in a flow collection", (2, True)),
            ("jobs/flow-apostrophe-in-plain-scalar-passes", ((_BRANCHES, "    branches: [it's, main]\n"),),
             "which this line model does not read", (0, False)),
            ("jobs/quoted-unclosed-cannot-evaluate", ((_BRANCHES, "    branches: 'main\n"),),
             "opens a quoted scalar that does not close", (2, True)),
            ("jobs/quoted-text-after-cannot-evaluate", ((_BRANCHES, "    branches: 'main' x\n"),),
             "carries text after a quoted scalar", (2, True)),
            ("jobs/quoted-escape-cannot-evaluate", ((_BRANCHES, "    branches: \"m\\x61in\"\n"),),
             "carries a double-quoted scalar with a backslash escape", (2, True)),
            ("jobs/block-scalar-not-run-cannot-evaluate",
             (_other_job(("      - name: |\n", "          x\n")),), "opens a block scalar on a key other than run:",
             (2, True)),
            ("jobs/block-scalar-indentation-indicator-cannot-evaluate",
             (_other_job(("      - run: |2\n", "          echo a\n")),), "an indentation indicator", (2, True)),
            # The lines of a run: block scalar are script text, not read: brackets, braces, quotes and pin
            # text in them pass, in a list item step and in a step's later key; a line at or left of the
            # key's column ends the block, so a pin after it is read.
            ("jobs/run-block-script-text-passes",
             (_other_job(("      - name: x\n", "        shell: bash\n", "        run: |-  # c\n",
                          "          [ -f x ] && echo ok\n", "          [[ -n x ]] && echo ok\n", "\n",
                          "          { echo a; } > /dev/null\n", "          [ x = it's ]\n",
                          "          echo python-version: 3.14.4\n", "      - run: >\n",
                          "          [ -f x ] && b\n")),),
             "which this line model does not read", (0, False)),
            ("jobs/run-block-ends-pin-after-read",
             (_other_job(("      - run: |\n", "          [ -f x ] && echo ok\n",
                          "      - uses: actions/setup-python@v5\n", "        with:\n",
                          "          python-version: '3.14.4'\n")),),
             "job other pins the host patch 3.14.4", (1, True)),
            # Pin shapes in another job: each spelling this line model does not read is refused; every
            # literal pin on a line is read.
            ("jobs/other-job-pin-anchor-cannot-evaluate", (_setup_with("python-version: &p '3.14.4'"),),
             "carries an anchor (&)", (2, True)),
            ("jobs/other-job-pin-tag-cannot-evaluate", (_setup_with("python-version: !!str 3.14.4"),),
             "carries a tag (!)", (2, True)),
            ("jobs/other-job-pin-block-scalar-cannot-evaluate",
             (_setup_with("python-version: |-\n            3.14.4"),), "opens a block scalar on a key other than run:",
             (2, True)),
            ("jobs/other-job-pin-escape-cannot-evaluate", (_setup_with("python-version: \"\\x33.14.4\""),),
             "carries a double-quoted scalar with a backslash escape", (2, True)),
            ("jobs/other-job-pin-escaped-key-cannot-evaluate", (_setup_with("\"python-v\\x65rsion\": 3.14.4"),),
             "carries a double-quoted scalar with a backslash escape", (2, True)),
            ("jobs/other-job-pin-flow-escape-cannot-evaluate", (_setup_with("{python-version: \"\\x33.14.4\"}"),),
             "carries a double-quoted scalar with a backslash escape", (2, True)),
            ("jobs/other-job-pin-line-continuation-cannot-evaluate",
             (_setup_with("python-version: \"3.14.\\\n            4\""),), "opens a quoted scalar that does not close",
             (2, True)),
            ("jobs/other-job-pin-explicit-key-cannot-evaluate",
             (_setup_with("? python-version\n          : '3.14.4'"),), "carries an explicit key (?)", (2, True)),
            ("jobs/other-job-pin-value-on-later-line-cannot-evaluate",
             (_setup_with("python-version:\n            3.14.4"),), "whose value does not start on its line",
             (2, True)),
            ("jobs/other-job-two-pins-on-one-line",
             (_other_job(("      - [{uses: a, with: {python-version: '3.14'}}, {uses: a, with: {python-version: "
                          "'3.14.4'}}]\n",)),), "job other pins the host patch 3.14.4", (1, True))):
        check(check_id, _outcome(me, _workflow_fixture(base, edits), HOST, marker), want)
    check("jobs/host-job-absent", _outcome(me, _without_host_job(base), HOST,
                                           "host-patch job opf-host-patch is missing"), (1, True))
    check("jobs/duplicate-job", _outcome(me, _duplicated_job(base), HOST,
                                         "job opf-host-patch is defined more than once"), (1, True))
    check("jobs/missing-workflow-cannot-evaluate",
          _outcome(me, _fixture(base, files={FIXTURE_WORKFLOW: None}), HOST, "cannot stat"), (2, True))
    for check_id, workflow in (("jobs/workflow-absolute-cannot-evaluate", "/etc/quality.yml"),
                               ("jobs/workflow-dotdot-cannot-evaluate", "../.github/workflows/quality.yml"),
                               ("jobs/workflow-dot-part-cannot-evaluate", ".github/./workflows/quality.yml"),
                               ("jobs/workflow-empty-part-cannot-evaluate", ".github//workflows/quality.yml")):
        check(check_id, _outcome(me, _fixture(base, floor=_floor_text(workflow=workflow)), HOST,
                                 "host-patch-workflow must be a relative path"), (2, True))
    # A NUL character, written as a TOML escape, is refused before any filesystem call reads the path.
    check("jobs/workflow-nul-cannot-evaluate",
          _outcome(me, _fixture(base, floor=_floor_text(workflow=".github/workflows/q\\u0000.yml")), HOST,
                   "carries a NUL character"), (2, True))
    check("jobs/workflow-key-missing-cannot-evaluate",
          _outcome(me, _fixture(base, floor="python-floor = \"3.14\"\nhost-patch-jobs = [\"opf-host-patch\"]\n"
                                            "guarded-surfaces = [\"tools/opf.py\"]\n"), HOST,
                   "host-patch-workflow must be present"), (2, True))
    # Mismatched policy and job values: the host policy moved to 3.14.5 while the job still pins 3.14.4,
    # and the reverse; each is red on the jobs leg alone (the interpreter is the policy's patch).
    moved = _fixture(base, host=_host_text("3.14.5"), files={FIXTURE_WORKFLOW: _workflow_text("3.14.4")})
    check("jobs/policy-patch-moved-job-not", _outcome(me, moved, (3, 14, 5, "final"), "departs from"), (1, True))
    stale = _fixture(base, files={FIXTURE_WORKFLOW: _workflow_text("3.14.5")})
    check("jobs/job-patch-moved-policy-not", _outcome(me, stale, HOST, "departs from"), (1, True))
    check("jobs/policy-and-job-moved-together",
          _outcome(me, _fixture(base, host=_host_text("3.14.5")), (3, 14, 5, "final"), "departs from"), (0, False))
    # HOST_TOOLS moved while the job did not, and a HOST_TOOLS line no workflow line can carry.
    plain = _fixture(base)
    check("jobs/host-tools-moved-job-not",
          _with_host_tools(me, ("python3 -I -B tools/opf.py --self-test-group core",),
                           lambda: _outcome(me, plain, HOST, "departs from")), (1, True))
    check("jobs/host-tools-and-job-moved-together",
          _with_host_tools(me, ("python3 -I -B tools/opf.py --self-test-group core",),
                           lambda: _outcome(me, _fixture(base), HOST, "departs from")), (0, False))
    check("jobs/host-tools-not-plain",
          _with_host_tools(me, ("python3 -I -B tools/opf.py --name 'a b'",),
                           lambda: _outcome(me, _fixture(base), HOST, "is not plain YAML text")), (1, True))
    # A second bound host-patch job, named by the policy and missing from the workflow.
    me.HOST_TOOLS["opf-host-patch-b"] = ("python3 -I -B tools/opf.py --self-test",)
    try:
        check("jobs/second-host-job-missing",
              _outcome(me, _fixture(base, floor=_floor_text(jobs=("opf-host-patch", "opf-host-patch-b"))), HOST,
                       "host-patch job opf-host-patch-b is missing"), (1, True))
    finally:
        del me.HOST_TOOLS["opf-host-patch-b"]
    # Failure propagation: the binding-only run judges the jobs leg, and main() exits 1 on its finding
    # in both modes.
    missing = _workflow_fixture(base, ((_HOST_HEADER, "  opf-renamed:\n"),))
    check("jobs/binding-only-runs-jobs-leg",
          _outcome(me, missing, (3, 14, 5, "final"), "is missing", binding_only=True), (1, True))
    check("jobs/cli-full-run-exits-1", _cli(me, missing, []), 1)
    check("jobs/cli-binding-only-exits-1", _cli(me, missing, [BINDING_ONLY_FLAG]), 1)
    check("jobs/cli-cannot-evaluate-exits-2",
          _cli(me, _workflow_fixture(base, ((_HOST_HEADER, "  opf-host-patch: {}\n"),)), [BINDING_ONLY_FLAG]), 2)


def _jobs_revert_cases(base, me, mutants):
    """Each jobs-leg rule red on its fixture with the intact checker and judged otherwise in a scratch copy
    with that rule reverted."""
    missing = _without_host_job(base)
    older = _workflow_fixture(base, ((_HOST_PIN, "          python-version: '3.14.3'\n"),))
    top_env = _workflow_fixture(base, (("permissions:\n", "env:\n  BASH_ENV: /dev/null\n\npermissions:\n"),))
    twice = _workflow_fixture(base, (("permissions:\n", "permissions:\n  actions: read\n\npermissions:\n"),))
    other = _workflow_fixture(base, (("  # The host-patch job.\n", _OTHER_JOB.format("3.14.4")),))
    carriage = _workflow_fixture(base, ((_GATE_LINE, _GATE_LINE[:-1] + "\r\n"),))
    colon = _workflow_fixture(base, ((_HOST_NAME, "    name: OPF: host patch\n"),))
    stray = _workflow_fixture(base, (("jobs:\n", "jobs:\n    stray: 1\n"),))
    moved = _fixture(base, host=_host_text("3.14.5"))
    unclosed = _workflow_fixture(base, (("on:\n  push:\n", "on: [\n  push:\n"),))
    any_case = _workflow_fixture(base, (("  # The host-patch job.\n", _OTHER_JOB.replace(
        "python-version", "Python-Version").format("3.14.4")),))
    blanks = _workflow_fixture(base, (("  # The host-patch job.\n", _OTHER_JOB.format("  3.14.4")),))
    shapes = {name:_workflow_fixture(base, (edit,)) for name, edit in (
        ("comment", (_BRANCHES, "    branches: [main # ]\n")),
        ("apostrophe", (_BRANCHES, "    branches: [it's]\n")),
        ("flow-anchor", (_BRANCHES, "    branches: [main, &a b]\n")),
        ("flow-escape", _setup_with("{python-version: \"\\x33.14.4\"}")),
        ("anchor", ("on:\n  push:\n", "on: &a [\n  push:\n")),
        ("key-colon", (_BRANCHES, "    a:b: [main]\n")),
        ("continued", _setup_with("python-version: \"3.14.\\\n            4\"")),
        ("escape", _setup_with("python-version: \"\\x33.14.4\"")),
        ("block-pin", _setup_with("python-version: |-\n            3.14.4")),
        ("indicator", _other_job(("      - run: |2\n", "          echo a\n"))),
        ("script", _other_job(("      - run: |\n", "          [ -f x ] && echo ok\n"))),
        ("after-block", _other_job(("      - run: |\n", "          [ -f x ] && echo ok\n",
                                    "      - uses: actions/setup-python@v5\n", "        with:\n",
                                    "          python-version: '3.14.4'\n"))),
        ("two-pins", _other_job(("      - [{uses: a, with: {python-version: '3.14'}}, {uses: a, with: "
                                 "{python-version: '3.14.4'}}]\n",))),
        ("later", _setup_with("python-version:\n            3.14.4")),
        ("flow-text", (_BRANCHES, "    branches: [main] x\n")),
        ("quoted-text", (_BRANCHES, "    branches: 'main' x\n")))}
    # A parent's workflow one level above the OPF root never stands in for the OPF one.
    dotdot = _fixture(base, floor=_floor_text(workflow="../.github/workflows/quality.yml"))
    _write(dotdot.parent, FIXTURE_WORKFLOW, _workflow_text())
    # (check id, reverted rule, fixture, interpreter, (intact exit, reverted exit))
    for check_id, name, root, version, want in (
            ("revert/jobs-leg", "jobs-leg", missing, HOST, (1, 0)),
            ("revert/job-missing", "job-missing", missing, HOST, (1, 0)),
            ("revert/job-lines", "job-lines", older, HOST, (1, 0)),
            ("revert/top-keys", "top-keys", top_env, HOST, (1, 0)),
            ("revert/top-twice", "top-twice", twice, HOST, (1, 0)),
            ("revert/job-twice", "job-twice", _duplicated_job(base), HOST, (1, 0)),
            ("revert/outside-pin", "outside-pin", other, HOST, (1, 0)),
            ("revert/workflow-path", "workflow-path", dotdot, HOST, (2, 0)),
            ("revert/line-breaks", "line-breaks", carriage, HOST, (2, 1)),
            ("revert/name-text", "name-text", colon, HOST, (1, 0)),
            ("revert/pin-from-policy", "pin-from-policy", moved, (3, 14, 5, "final"), (0, 1)),
            ("revert/unplaced-line", "unplaced-line", stray, HOST, (2, 0)),
            ("revert/flow-close", "flow-close", unclosed, HOST, (2, 0)),
            ("revert/pin-any-case", "pin-any-case", any_case, HOST, (1, 0)),
            ("revert/pin-blanks", "pin-blanks", blanks, HOST, (1, 0)),
            ("revert/flow-comment", "flow-comment", shapes["comment"], HOST, (2, 0)),
            ("revert/flow-token-quote", "flow-token-quote", shapes["apostrophe"], HOST, (0, 2)),
            ("revert/flow-node-marks", "flow-node-marks", shapes["flow-anchor"], HOST, (2, 0)),
            ("revert/flow-escape", "flow-escape", shapes["flow-escape"], HOST, (2, 0)),
            ("revert/node-marks", "node-marks", shapes["anchor"], HOST, (2, 0)),
            ("revert/key-colon", "key-colon", shapes["key-colon"], HOST, (2, 0)),
            ("revert/quoted-close", "quoted-close", shapes["continued"], HOST, (2, 0)),
            ("revert/quoted-escape", "quoted-escape", shapes["escape"], HOST, (2, 0)),
            ("revert/block-key", "block-key", shapes["block-pin"], HOST, (2, 0)),
            ("revert/block-header", "block-header", shapes["indicator"], HOST, (2, 0)),
            ("revert/block-content", "block-content", shapes["script"], HOST, (0, 2)),
            ("revert/pin-every-match", "pin-every-match", shapes["two-pins"], HOST, (1, 0)),
            ("revert/pin-later", "pin-later", shapes["later"], HOST, (2, 0)),
            ("revert/flow-text-after", "flow-text-after", shapes["flow-text"], HOST, (2, 0)),
            ("revert/quoted-text-after", "quoted-text-after", shapes["quoted-text"], HOST, (2, 0))):
        check(check_id, (me.evaluate(root, version)[0], mutants[name].evaluate(root, version)[0]), want)
    # A block scalar that never ends: the copy that keeps skipping deeper lines misses the pin after the
    # block (and reports the host-patch job's skipped lines instead, so the pin finding is compared).
    check("revert/block-end", tuple(_has(module.evaluate(shapes["after-block"], HOST)[1],
                                         "job other pins the host patch 3.14.4")
                                    for module in (me, mutants["block-end"])), (True, False))
    # A HOST_TOOLS line that is not plain YAML text, its fixture written after the override so the job
    # carries that line: the intact checker refuses it; the copy without the rule compares and passes.
    runs = ("python3 -I -B tools/opf.py --name 'a b'",)
    mutants["plain-run"].HOST_TOOLS["opf-host-patch"] = runs

    def plain_run_exits():
        root = _fixture(base)
        return me.evaluate(root, HOST)[0], mutants["plain-run"].evaluate(root, HOST)[0]
    check("revert/plain-run", _with_host_tools(me, runs, plain_run_exits), (1, 0))


def _cli(module, root, argv):
    """module.main(argv) over root in place of ROOT, its report lines discarded."""
    import contextlib
    import io
    saved = module.ROOT
    module.ROOT = root
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return module.main(argv)
    finally:
        module.ROOT = saved


def _cli_cases(base, me):
    # A host patch no interpreter runs: the full run is red on the interpreter leg, the binding-only run
    # passes, so the flag's forwarding is observed through main().
    root = _fixture(base, host=_host_text("3.14.9999"))
    check("cli/full-run-judges-interpreter", _cli(me, root, []), 1)
    check("cli/binding-only-skips-interpreter", _cli(me, root, [BINDING_ONLY_FLAG]), 0)
    check("cli/usage-unknown-flag", _cli(me, root, ["--jobs-only"]), 2)
    check("cli/usage-extra-argument", _cli(me, root, [BINDING_ONLY_FLAG, "x"]), 2)


def _revert_cases(base, me):
    """Each rule red on its fixture with the intact checker and judged otherwise in a scratch copy with
    that rule reverted."""
    mutants = {name: _mutant(base, name, anchor, replacement) for name, (anchor, replacement) in REVERTS.items()}
    other = (3, 14, 5, "final")
    mismatch = _fixture(base, host=_host_text("3.15.0"))
    unknown = _fixture(base, floor=_floor_text(jobs=("opf-host-patch", "opf-other")))
    unnamed = _fixture(base, floor=_floor_text(jobs=("opf-other",)))
    unguarded = _fixture(base, floor=_floor_text(surfaces=("tools/other.py",)))
    extra = _fixture(base, host=HOST_TEXT + "python-floor = \"3.14\"\n")
    empty = _fixture(base, floor=_floor_text(jobs=()))
    parent = _fixture(base, files={HOST_SOURCE_REL: None})
    _write(parent.parent, ".aiqt/python-host.toml", HOST_TEXT)
    plain = _fixture(base)
    # (check id, reverted rule, fixture, interpreter, (intact exit, reverted exit))
    for check_id, name, root, version, want in (
            ("revert/patch-leg", "patch-leg", mismatch, (3, 15, 0, "final"), (1, 0)),
            ("revert/binding-leg", "binding-leg", unknown, HOST, (1, 0)),
            ("revert/interpreter-leg", "interpreter-leg", plain, other, (1, 0)),
            ("revert/unknown-job", "unknown-job", unknown, HOST, (1, 0)),
            ("revert/unguarded-tool", "guarded-tool", unguarded, HOST, (1, 0)),
            ("revert/final-release", "final-release", plain, (3, 14, 4, "candidate"), (1, 0)),
            ("revert/host-keys", "host-keys", extra, HOST, (2, 0)),
            ("revert/host-parent", "host-parent", parent, HOST, (2, 0)),
            ("revert/jobs-empty", "jobs-empty", empty, HOST, (2, 1))):
        check(check_id, (me.evaluate(root, version)[0], mutants[name].evaluate(root, version)[0]), want)
    # A bound job the policy leaves out: the intact checker names it; the reverted copy still reports the
    # unknown job beside it, but no longer names the bound one.
    check("revert/unbound-job", (_has(me.evaluate(unnamed, HOST)[1], "does not name opf-host-patch"),
                                 _has(mutants["unbound-job"].evaluate(unnamed, HOST)[1], "does not name")),
          (True, False))
    # A run line on another interpreter name: the intact checker refuses it; the copy without the
    # python3 -I -B prefix rule reads the tool at the same position and passes. The fixture is written
    # after the override, so its workflow job runs that line and the jobs leg agrees with both.
    saved = dict(me.HOST_TOOLS)
    me.HOST_TOOLS["opf-host-patch"] = ("python -I -B tools/opf.py --self-test",)
    mutants["run-prefix"].HOST_TOOLS["opf-host-patch"] = ("python -I -B tools/opf.py --self-test",)
    try:
        other_interpreter = _fixture(base)
        check("revert/run-prefix", (me.evaluate(other_interpreter, HOST)[0],
                                    mutants["run-prefix"].evaluate(other_interpreter, HOST)[0]), (1, 0))
    finally:
        me.HOST_TOOLS.clear()
        me.HOST_TOOLS.update(saved)
    check("revert/parse-mapping", (_parse_refusal(me, plain, RecursionError),
                                   _parse_refusal(mutants["parse-mapping"], plain, RecursionError)),
          ((2, True), "raised RecursionError"))
    root = _fixture(base, host=_host_text("3.14.9999"))
    check("revert/binding-only-forward", (_cli(me, root, [BINDING_ONLY_FLAG]),
                                          _cli(mutants["binding-only-forward"], root, [BINDING_ONLY_FLAG])), (0, 1))
    _jobs_revert_cases(base, me, mutants)


def _self_test_cases(base):
    me = sys.modules[__name__]
    _source_cases(base, me)
    _patch_cases(base, me)
    _binding_cases(base, me)
    _jobs_cases(base, me)
    _interpreter_cases(base, me)
    _cli_cases(base, me)
    _revert_cases(base, me)


def _expected_check_ids():
    """The check ids written in this file: the first argument of each check() call, a string literal, and
    each string literal a for loop over a literal tuple binds to check_id (the first name of its target)."""
    tree = ast.parse(_read_text(Path(__file__).resolve()))
    ids = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "check" \
                and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
            ids.add(node.args[0].value)
        if isinstance(node, ast.For) and isinstance(node.target, ast.Tuple) and node.target.elts \
                and isinstance(node.target.elts[0], ast.Name) and node.target.elts[0].id == "check_id" \
                and isinstance(node.iter, ast.Tuple):
            for item in node.iter.elts:
                if isinstance(item, ast.Tuple) and item.elts and isinstance(item.elts[0], ast.Constant) \
                        and isinstance(item.elts[0].value, str):
                    ids.add(item.elts[0].value)
    return ids


def self_test():
    """Fixture trees for every leg and red-on-revert, then the executed check ids reconciled against the
    check ids written in this file."""
    _child_status_refusal_exit("check_opf_host_pin.py")
    try:
        with tempfile.TemporaryDirectory(prefix="opf-host-pin-selftest-") as raw:
            _self_test_cases(Path(raw))
    except (CannotEvaluate, OSError) as exc:
        print("SELF-TEST HARNESS ERROR: {}".format(exc), file=sys.stderr)
        return 2
    expected = _expected_check_ids()
    for check_id in sorted(expected - _EXECUTED_SET):
        FAILURES.append("execution-set/missing: {}".format(check_id))
    for check_id in sorted(_EXECUTED_SET - expected):
        FAILURES.append("execution-set/extra: {}".format(check_id))
    if FAILURES:
        print("SELF-TEST FAIL:")
        for failure in FAILURES:
            print("  - " + failure)
        return 1
    print("SELF-TEST PASS: {} unique checks executed (each leg and rule red on its fixture and judged "
          "otherwise with its change reverted); execution set reconciled against this file's check ids".format(
              len(EXECUTED)))
    return 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv == [BINDING_ONLY_FLAG]:
        code, lines = evaluate(ROOT, tuple(sys.version_info[:4]), binding_only=argv == [BINDING_ONLY_FLAG])
        for line in lines:
            print(line, file=sys.stderr if code == 2 else sys.stdout)
        return code
    if argv == ["--self-test"]:
        return self_test()
    print("usage: check_opf_host_pin.py [{} | --self-test]".format(BINDING_ONLY_FLAG), file=sys.stderr)
    return 2


# The child-status guard: an inline copy of the canonical guard in opf/tools/_child_status.py, which
# tools/check_child_status_coverage.py pins this copy to by AST. The checker starts no child of its own;
# it carries the guard so a run under an ignored SIGCHLD, where the runner and the host-patch job that
# launch it could not read any child's exit status, refuses here as well. The guard runs as the first
# statement of self_test(), so the --self-test path refuses too, and at the entry line before main();
# the entry line comes after the --self-test statement that opf.py requires first in the __main__
# block. An import of this module runs nothing.
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
    if sys.argv[1:] == ["--self-test"]:
        sys.exit(self_test())
    sys.exit(_child_status_refusal_exit("check_opf_host_pin.py") or main())
