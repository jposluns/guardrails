#!/usr/bin/env python3
"""Python-floor gate, OPF copy (PYTHON-FLOOR): the OPF tree's own policies, tools/python-floor.toml and
tools/python-host.toml, name the Python floor of the OPF tree's executable tooling and the exact patch
release its authoring host-patch jobs run, and every OPF statement and pin of either agrees with them.
Every path is relative to the OPF root (the directory that holds this tools/ directory), and the gate
reads no file of a parent repository, so it runs the same in a nested checkout and in an OPF-only tree.

The legs are those of the parent repository's tools/check_python_floor.py, whose docstring states each
leg's rules in full: source, switch, pins, guard, dynamic, fallback, tomllib, completeness, documentation
and claims. Every function from _read_text to evaluate() below is that gate's, copied unchanged except
for the adaptations listed here, and judges the OPF surfaces the policies name.

Adapted to the OPF tree:
  policy        load_source reads tools/python-floor.toml, whose keys are exactly POLICY_KEYS (the
                parent's six source keys plus workflow-dir, workflow-files, host-patch-workflow,
                host-patch-jobs, adopter-templates, doctor-templates, declaration-files,
                declaration-copies, excluded-trees and tomllib-table; the earlier pin-files key is
                refused), and binds the pins leg's WORKFLOWS_REL, WORKFLOW_FILES, HOST_WORKFLOW_REL,
                HOST_JOBS and PIN_FILES (the adopter templates, then the doctor templates),
                EXCLUDED_TREES, DECLARATION_FILES, DECLARATION_COPIES and TOMLLIB_TABLE_REL from it
                (_bind), so the legs read the OPF policy and no constant of the parent. A missing or
                extra key, a value of the wrong type, a list that is not sorted and unique, a path that
                is not OPF-root-relative, an excluded tree that does not end in /, a workflow file
                outside workflow-dir, a host-patch workflow that is not a listed workflow file, an empty
                or malformed host-patch job list, or a template listed twice (as both kinds, or as a
                workflow file) is cannot-evaluate (exit 2).
  host patch    load_source also reads tools/python-host.toml, required beside the floor policy and
                never replaced by a parent's host policy: exactly format-version = 1 and
                python-host-patch = "MAJOR.MINOR.PATCH", a patch release of the floor (each component at
                most four digits). A missing, unreadable or malformed host policy, or a patch of
                another minor version, is cannot-evaluate. Inside a host-patch job of the host-patch
                workflow, and nowhere else, a pin of a patch release of the floor passes only when it
                equals that patch (_host_patch_pin, the parent's rule bound to the OPF policies); the
                adopter and doctor templates pin the floor, so a template pinning the host patch is a
                finding. That each named host-patch job exists in the workflow is not checked here.
  hooks         HOOK_SURFACES is empty: no OPF entrypoint carries the hook form, so every guarded
                surface is held to the canonical guard. The hook-form code is carried unchanged.
  completeness  the git listing runs at the OPF root with the root's grandparent as the ceiling, so
                the repository read is the OPF root's own (an OPF-only repository) or its parent's
                (the nested checkout, where git ls-files lists the paths under the OPF root relative to
                it); a repository further up is never read, and no repository within those two levels
                is cannot-evaluate.
  self-test     --self-test runs this copy's own fixture trees and reconciles the executed check ids
                against the check ids written in this file, with no parent registry.

Left to the parent gate, which while OPF is nested still runs every leg over the whole repository, the
OPF surfaces included: the parent's single source, workflows, declaration files and guarded surfaces;
the hook form of the parent's hook surfaces; the host-patch jobs of the parent workflow and their
source; and the execution report its self-test writes for tools/check_selftest_execution.py. The full
line-model and grammar vectors stay in the parent self-test, which exercises the same function bodies;
this copy's self-test covers each leg and each adaptation, and the parent's self-test holds the rules
both gates share to the same verdicts while this copy exists.

  check_python_floor.py              run every leg over the OPF tree
  check_python_floor.py --self-test  fixture trees for every leg and adaptation, each red with its
                                     change reverted in a scratch copy loaded through importlib

Exit convention: 0 every enabled leg passes; 1 a finding; 2 usage or cannot-evaluate (a missing,
unreadable or malformed floor or host policy; a listed or scanned file that is missing, not a regular file, not UTF-8
or does not parse; a child launch failure or timeout; unavailable temporary storage).

Run this gate isolated: python3 -I -B tools/check_python_floor.py
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: check_python_floor.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import ast
import builtins
import importlib.util
import json
import os
import re
import stat
import subprocess
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_REL = "tools/python-floor.toml"
# The decided floor. The source leg asserts the policy names it; every other leg reads the floor from the
# policy.
FLOOR = (3, 14)
# The decided value of the documentation-check switch. The switch leg asserts the policy keeps it, so
# turning the documentation check off is a finding and a reviewed change to this gate too.
DOCUMENTATION_CHECK = True
# The keys of the OPF policy and the type of each: the parent source's six, plus the surfaces the parent
# gate holds as constants, here stated by the policy.
POLICY_KEYS = {
    "format-version": int, "python-floor": str, "guarded-surfaces": list, "nonblocking-surfaces": list,
    "completeness-check": bool, "documentation-check": bool, "workflow-dir": str, "workflow-files": list,
    "host-patch-workflow": str, "host-patch-jobs": list, "adopter-templates": list,
    "doctor-templates": list, "declaration-files": list, "declaration-copies": dict, "excluded-trees": list,
    "tomllib-table": str,
}
# The policy's sorted, unique string lists, and the keys whose values are OPF-root-relative paths.
POLICY_LISTS = ("guarded-surfaces", "nonblocking-surfaces", "workflow-files", "host-patch-jobs",
                "adopter-templates", "doctor-templates", "declaration-files", "excluded-trees")
POLICY_PATHS = ("workflow-dir", "workflow-files", "host-patch-workflow", "adopter-templates", "doctor-templates",
                "declaration-files", "excluded-trees", "tomllib-table")
# One OPF-root-relative path part: no empty, . or .. part, and nothing outside these characters.
PART_RE = re.compile(r"[A-Za-z0-9_.-]+")
# Bound from the policy by _bind before any leg runs; empty until then, so a leg never reads a default.
WORKFLOWS_REL = ""
WORKFLOW_FILES = ()
PIN_FILES = ()
PIN_KEY = "python-version"
# The host-patch jobs: inside a HOST_JOBS job of HOST_WORKFLOW_REL, and nowhere else, a pin that is a patch
# release of the floor passes only when it equals HOST_PATCH, the python-host-patch the OPF host policy
# names (_host_patch_pin). All three are bound from the OPF policies by _bind.
HOST_WORKFLOW_REL = ""
HOST_JOBS = ()
HOST_PATCH = ""
HOST_SOURCE_REL = "tools/python-host.toml"
HOST_SOURCE_KEYS = {"format-version", "python-host-patch"}
HOST_PATCH_RE = re.compile(r"([1-9][0-9]{0,3})\.(0|[1-9][0-9]{0,3})\.(0|[1-9][0-9]{0,3})")
# A job id under the top-level jobs: key, a plain key at indent 2, then nothing but an optional comment.
HOST_JOB_RE = re.compile(r"  (?P<id>[A-Za-z_][A-Za-z0-9_-]*) *:(?:[ \t]+#.*|[ \t]*)")
# The step environment variable the Actions toolkit reads the input from ("INPUT_" plus the upper-cased
# name). A line naming it can set the input outside a pin.
INPUT_ENV = "INPUT_" + PIN_KEY.upper()
# The one accepted spelling of a line naming PIN_KEY (fullmatch on a "\n"-split line, one trailing "\r"
# removed). The key matches without regard to case, so a strict pin whose key is not spelled in lower
# case is seen and refused (pin_findings), never passed over. A comment may not carry a character a
# YAML 1.1 reader treats as a line break.
PIN_LINE_RE = re.compile(
    r" *(?:- +)?(?P<key>(?i:python-version)) *: +"
    r"(?:(?P<plain>[A-Za-z0-9._+-]+)|'(?P<single>[A-Za-z0-9._+-]+)'|\"(?P<double>[A-Za-z0-9._+-]+)\")"
    r"(?:[ \t]+#[^\r\x85\u2028\u2029]*|[ \t]*)")
# The key of a line that is not a strict pin, read only to word the refusal: after the indentation and
# any list markers, an optionally quoted key and its colon.
KEY_RE = re.compile(r" *(?:- +)*([\"']?)([^\s\"':#{}\[\],][^\"':#{}\[\],]*?)\1 *:(?=[ \t]|$)")
# A node that opens with a quote or a flow indicator: after the indentation, any list markers, an
# optional plain key with its colon, and optional anchor or tag properties.
NODE_START_RE = re.compile(
    r" *(?:- +)*(?:[A-Za-z0-9_][A-Za-z0-9_.-]* *:[ \t]+)?(?:[&!][^ \t]*[ \t]+)*([\"'\[{].*)")
# Characters a YAML reader may read as a line break that the "\n" split does not: a carriage return
# left inside a line, and the next-line, line-separator and paragraph-separator characters a YAML 1.1
# reader also breaks on. A scanned line that carries one is cannot-evaluate (pin_findings).
LINE_BREAKS = {"\r": "a carriage return", "\x85": "U+0085 (next line)",
               "\u2028": "U+2028 (line separator)", "\u2029": "U+2029 (paragraph separator)"}
SETUP_PYTHON = "actions/setup-python"
# The one accepted spelling of a line whose key is uses: optional space indentation, an optional `- `
# list marker, the bare key in lower case, optional spaces, a colon, one or more spaces, exactly one
# plain, single-quoted or double-quoted literal of [A-Za-z0-9._/@:+~-] characters, then optional
# whitespace and an optional `#` comment.
USES_LINE_RE = re.compile(
    r" *(?:- +)?(?P<key>uses) *: +"
    r"(?:(?P<plain>[A-Za-z0-9./][A-Za-z0-9._/@:+~-]*)|'(?P<single>[A-Za-z0-9._/@:+~-]+)'"
    r"|\"(?P<double>[A-Za-z0-9._/@:+~-]+)\")(?:[ \t]+#.*|[ \t]*)")
# A step's input key: with: alone on its line, after an optional `- ` list marker.
WITH_LINE_RE = re.compile(r" *(?:- +)?(?P<key>with) *:(?:[ \t]+#.*|[ \t]*)")
# A block sequence entry's `-` marker and the spaces after it, up to the entry's first key.
STEP_MARKER_RE = re.compile(r" *- +(?=[^\s#])")
# A line's indentation and any `- ` list markers: its end is the column of the line's first key.
LEAD_RE = re.compile(r" *(?:- +)*")
# A key inside a flow mapping, after a `{` or `,`: an optional explicit-key `?`, a double-quoted,
# single-quoted or plain key, then optional spaces and a colon. Read only to refuse a uses key.
FLOW_KEY_RE = re.compile(
    r"[{,][ \t]*(?:\?[ \t]+)?(?:\"(?P<double>[^\"]*)\"|'(?P<single>(?:[^']|'')*)'"
    r"|(?P<plain>[^\s,\[\]{}:'\"#][^,\[\]{}:#]*?))[ \t]*:")
# The rules this line model adds to the strict pin rule. The self-test's red-on-revert loads a copy of
# this gate through importlib, removes one entry, and shows the reproduction that rule refuses passing.
RULES = frozenset({"grammar", "duplicate-key", "comment-span", "action-files", "local-uses-target",
                   "uses-shape", "with-list", "extension-case", "flow-uses", "with-child"})
# The enumerated plain grammar of a workflow or action file (_grammar_check). A key line: a plain key,
# optional spaces, a colon, then nothing or whitespace and the value.
GRAMMAR_KEY_RE = re.compile(r"(?P<key>[A-Za-z0-9_][A-Za-z0-9_.-]*) *:(?:[ \t]+(?P<value>.*))?")
# A block scalar indicator with optional chomping and indentation indicators, then an optional comment.
BLOCK_INDICATOR_RE = re.compile(r"[|>](?:[1-9][+-]?|[+-][1-9]?)?(?:[ \t]+#.*|[ \t]*)")
# One-line quoted scalars: single-quoted with '' escapes, double-quoted with no backslash at all.
QUOTED_RE = re.compile(r"'(?:[^']|'')*'|\"[^\"\\]*\"")
# What may follow a quoted or flow node on its line: whitespace and an optional comment.
TRAILER_RE = re.compile(r"(?:[ \t]+#.*|[ \t]*)")
# One entry of a one-line flow node: an optional plain key with its colon, an optional quoted or plain
# scalar (no nesting), then the separator or the closing bracket.
FLOW_ENTRY_RE = re.compile(
    r"[ \t]*(?:(?P<key>[A-Za-z0-9_][A-Za-z0-9_.-]*) *:[ \t]+)?"
    r"(?P<node>'(?:[^']|'')*'|\"[^\"\\]*\"|[^\s,\[\]{}#'\"][^,\[\]{}#]*?)?[ \t]*(?P<sep>[,\]}])")
# Characters that may not open a plain scalar (a -, ? or : may, before a non-space).
PLAIN_INDICATORS = "-?:,[]{}#&*!|>'\"%@`"
# The action file names of a local action, in the order the runner looks for them.
ACTION_FILES = ("action.yml", "action.yaml")
# Trees no floor check walks, bound from the policy's excluded-trees (the vendored third-party tree).
EXCLUDED_TREES = ()
SKIPPED_DIR_NAMES = {".git", "__pycache__", ".venv", "venv", "node_modules"}
# The git index mode of a gitlink (a submodule's commit), as git ls-files -s prints it.
GITLINK_MODE = b"160000"
# The OPF documentation that states the floor, and how many times each states it where that is more than
# once, bound from the policy's declaration-files and declaration-copies.
DECLARATION_FILES = ()
DECLARATION_COPIES = {}
# A separator the claims leg reads between words: whitespace, or a no-break space and its HTML forms.
CLAIM_SEP = r"(?:\s|&nbsp;|&#160;)"
# A version operator: >=, =>, ~=, ==, >, the sign U+2265, and their HTML forms.
CLAIM_OP = r"(?:>=|=>|~=|==|>|\u2265|&gt;=|&gt;|&ge;|&#8805;|&#x2265;)"
# The word before a version: Python or CPython, or Py when no dot, slash or hyphen precedes it (so a
# file name such as x.py is not read), then optionally "version" or "versions" and an operator.
CLAIM_WORD = (r"(?:(?<![A-Za-z0-9_])c?python|(?<![A-Za-z0-9_./-])py)(?:{0}*versions?)?{0}*(?:{1}{0}*)?v?"
              .format(CLAIM_SEP, CLAIM_OP))
# A Python version a declaration file names (the claims leg): the word, then a version M.N (the
# interpreter name pythonM.N included); the word, at least one separator, then a major version alone
# ("Python 3"); an operator then 3.N; or a bare 3.N followed by a plus sign, an "or newer" style
# phrase or "onward".
OLDER_CLAIM_RE = re.compile(
    r"(?i)" + CLAIM_WORD + r"(?P<major>[0-9]+)\.(?P<minor>[0-9]+)"
    r"|(?:(?<![A-Za-z0-9_])c?python|(?<![A-Za-z0-9_./-])py)(?:" + CLAIM_SEP + r"+versions?)?"
    + CLAIM_SEP + r"+(?:" + CLAIM_OP + CLAIM_SEP + r"*)?v?(?P<only>[0-9]+)(?![0-9]|\.[0-9])"
    r"|" + CLAIM_OP + CLAIM_SEP + r"*v?(?P<op>3)\.(?P<op_minor>[0-9]+)"
    r"|(?<![0-9.])(?P<bare>3)\.(?P<bare_minor>[0-9]+)(?=\+|" + CLAIM_SEP + r"+(?:(?:or|and)" + CLAIM_SEP
    + r"+(?:newer|later|above|higher|greater|up)|onwards?)\b)")
# A repo-relative .py path. Each segment opens with at most one dot (a hidden directory such as .preview
# or the .aiqt/ source tree) and then a letter, digit or underscore, so the pattern itself refuses an
# empty, . or .. segment and a leading /. load_source also refuses a . or .. segment by splitting on /,
# a second layer.
SURFACE_RE = re.compile(r"\.?[A-Za-z0-9_][A-Za-z0-9_.-]*(?:/\.?[A-Za-z0-9_][A-Za-z0-9_.-]*)*\.py")
FLOOR_RE = re.compile(r"([1-9][0-9]*)\.(0|[1-9][0-9]*)")
FLAG_SETS = ((), ("-O",), ("-OO",))
CHILD_TIMEOUT = 60
CONTINUED = "CONTINUED"
# The canonical guard. The CLI form: stdout stays empty and the process exits 2 (cannot evaluate). A
# nonblocking-surfaces entry exits 1 instead: it is a hook whose event reads exit 2 as a block (a Stop
# hook's exit 2 blocks the stop, and the guard runs before the hook's own block cap).
REFUSAL_EXIT = 2
NONBLOCKING_EXIT = 1
# The refusal exit survives a failing diagnostic stream end to end. The stderr write and its
# explicit flush are wrapped in try/except BaseException: sys.stderr can be None (Python leaves it
# None when file descriptor 2 is invalid at interpreter startup), its write can raise, and its
# flush can raise; an unwrapped write error escaping the guard once turned the refusal exit into
# exit 1, which a PreToolUse hook event reads as non-blocking (fail open). The exit is os._exit,
# no longer `raise SystemExit`: SystemExit unwinds into CPython's interpreter-exit flush of the std
# streams, and a sys.stderr whose flush fails there replaced the refusal exit with the
# interpreter's own exit 120, so wrapping the write alone still lost the code to a failing flush.
# os._exit skips that exit-time flush, and it loses no buffered output the explicit flush did not
# already hand over: the guard leg pins this guard to the top of the file (only a docstring and
# `from __future__` imports may precede its `import sys`), so when it exits, nothing else has run
# and nothing else has buffered output, and `import sys` and `import os` write nothing. The
# observed exit code is the same under runpy (the dynamic leg's child) and in a plain subprocess
# (how Claude Code runs a hook): neither intercepts os._exit, and neither caught the top-level
# SystemExit either, so the change removes no interception point.
GUARD_TEMPLATE = '''import sys

if tuple(sys.version_info[:2]) < ({major}, {minor}):
    import os
    try:
        sys.stderr.write(
            "error: {name} requires Python {major}.{minor} or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit({code})
'''
# The hook form, for HOOK_SURFACES only: the hook's events differ in the direction an error must fail, so
# a mode named in the FLOOR_FAIL_OPEN_MODES literal (a Stop-type, SessionStart, TeammateIdle,
# UserPromptSubmit or PostToolUse handler, or a note-only PreToolUse handler) warns on exit 0, never
# blocking, and every other argv (any other PreToolUse handler, an unknown mode, no mode) fails closed with
# the canonical refusal and exit 2. That
# exit is fixed, never {code}: Claude Code reads exit 1 as a non-blocking error and lets a PreToolUse call
# proceed, so load_source refuses a HOOK_SURFACES entry listed in nonblocking-surfaces. Both the
# stderr refusal and the stdout warning are best-effort writes, each flushed inside its own
# try/except BaseException, and both paths exit through os._exit, which skips the interpreter-exit
# flush of the std streams (see the GUARD_TEMPLATE comment; here too nothing runs before the
# guard, so os._exit loses no other buffered output, and the stdout warning is already flushed):
# the exits above hold even when a stream is closed, missing, failing on write or failing on
# flush, and the warning reaches stdout whenever stdout accepts the write and the flush.
HOOK_GUARD_TEMPLATE = '''import sys

FLOOR_FAIL_OPEN_MODES = {modes}

if tuple(sys.version_info[:2]) < ({major}, {minor}):
    import os
    _floor_refusal = (
        "error: {name} requires Python {major}.{minor} or newer; this is Python %d.%d.%d (%s). "
        "Nothing was run (cannot evaluate).\\n"
        % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
    try:
        sys.stderr.write(_floor_refusal)
        sys.stderr.flush()
    except BaseException:
        pass
    if len(sys.argv) > 1 and sys.argv[1] in FLOOR_FAIL_OPEN_MODES:
        try:
            import json
            sys.stdout.write(json.dumps(dict(systemMessage=(
                "AIQT guardrail: the %s check could not run (%s); surfacing a warning rather than blocking "
                "(non-blocking by design on this event)." % (sys.argv[1], _floor_refusal.strip())))) + "\\n")
            sys.stdout.flush()
        except BaseException:
            pass
        os._exit(0)
    os._exit(2)
'''
# No OPF entrypoint carries the hook form; the parent's hook surfaces stay with the parent gate.
HOOK_SURFACES = ()
MODES_NAME = "FLOOR_FAIL_OPEN_MODES"
MODE_RE = re.compile(r"[a-z][a-z0-9_]*")
DENY_PROBE_MODE = "floor_deny_probe"
# Child programs. sys.version_info is replaced by a plain tuple before the entrypoint (or the guard
# prefix) runs; the guard reads only sys.version_info, so the tuple stands in for an older interpreter.
REFUSAL_CHILD = (
    "import runpy, sys\n"
    "path = sys.argv[1]\n"
    "version = tuple(int(part) for part in sys.argv[2].split('.'))\n"
    "sys.argv = [path] + sys.argv[3:]\n"
    "sys.version_info = version + ('final', 0)\n"
    "runpy.run_path(path, run_name='__main__')\n")
BOUNDARY_CHILD = (
    "import sys\n"
    "prefix, version = sys.argv[1], sys.argv[2]\n"
    "if version != 'real':\n"
    "    sys.version_info = tuple(int(part) for part in version.split('.')) + ('final', 0)\n"
    "exec(compile(prefix, '<guard prefix>', 'exec'), {'__name__': '__main__'})\n"
    "print('" + CONTINUED + "')\n")
# The stderr-unavailable vectors: the refusal exit may not depend on the diagnostic write or on
# the stream's later flush. none runs the entrypoint with sys.stderr set to None (what Python
# leaves when file descriptor 2 is invalid at interpreter startup); raises with a sys.stderr
# whose write raises; flushraises with a sys.stderr whose write succeeds but whose flush raises,
# the stream shape that turned `raise SystemExit` into the interpreter's own exit 120 when CPython
# flushed the std streams at interpreter exit (the guard now flushes inside its try and exits
# through os._exit, which skips that exit-time flush); and closed in a fresh interpreter started
# with file descriptor 2 closed, so Python itself sets sys.stderr to None.
STDERR_VECTORS = ("none", "raises", "flushraises", "closed")
STDERR_VECTOR_LABELS = {"none": "sys.stderr set to None",
                        "raises": "a sys.stderr whose write raises",
                        "flushraises": "a sys.stderr whose write succeeds but whose flush raises",
                        "closed": "file descriptor 2 closed at interpreter startup"}
BROKEN_STDERR_CHILD = (
    "import runpy, sys\n"
    "path, vector = sys.argv[1], sys.argv[3]\n"
    "version = tuple(int(part) for part in sys.argv[2].split('.'))\n"
    "sys.argv = [path] + sys.argv[4:]\n"
    "sys.version_info = version + ('final', 0)\n"
    "class _BrokenStderr:\n"
    "    def write(self, text):\n"
    "        raise OSError('stderr unavailable')\n"
    "    def flush(self):\n"
    "        pass\n"
    "class _FlushRaisesStderr:\n"
    "    def write(self, text):\n"
    "        return len(text)\n"
    "    def flush(self):\n"
    "        raise OSError('stderr flush failed')\n"
    "sys.stderr = None if vector == 'none' else (\n"
    "    _FlushRaisesStderr() if vector == 'flushraises' else _BrokenStderr())\n"
    "runpy.run_path(path, run_name='__main__')\n")
# Closes file descriptor 2, then replaces the process with a fresh interpreter running the -c
# program in argv[1] (REFUSAL_CHILD) with the remaining arguments: that interpreter starts with
# no usable file descriptor 2, so it initializes sys.stderr to None itself.
CLOSED_FD2_CHILD = (
    "import os, sys\n"
    "os.close(2)\n"
    "os.execv(sys.executable, [sys.executable, '-I', '-B', '-c'] + sys.argv[1:])\n")
CHILD_ENV = {"PATH": "/usr/bin:/bin", "LC_ALL": "C.UTF-8"}


class CannotEvaluate(Exception):
    """An input this gate cannot read or interpret; reported as exit 2, never as a pass."""


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


def _policy_path_fault(item, directory=False):
    """Why item is not an OPF-root-relative path (ending in / when directory is true), or None."""
    if not isinstance(item, str) or not item or item.startswith("/") or (item.endswith("/") != directory):
        return "is not an OPF-root-relative {}".format("tree ending in /" if directory else "path")
    if any(part in ("", ".", "..") or not PART_RE.fullmatch(part)
           for part in (item[:-1] if directory else item).split("/")):
        return "has an empty, . or .. part or a character outside [A-Za-z0-9_.-]"
    return None


def _bind(data, host_patch):
    """Bind the legs' surfaces from the schema-checked policies."""
    global WORKFLOWS_REL, WORKFLOW_FILES, HOST_WORKFLOW_REL, HOST_JOBS, HOST_PATCH, PIN_FILES
    global EXCLUDED_TREES, DECLARATION_FILES, DECLARATION_COPIES, TOMLLIB_TABLE_REL
    WORKFLOWS_REL = data["workflow-dir"]
    WORKFLOW_FILES = tuple(data["workflow-files"])
    HOST_WORKFLOW_REL = data["host-patch-workflow"]
    HOST_JOBS = tuple(data["host-patch-jobs"])
    HOST_PATCH = host_patch
    PIN_FILES = tuple(data["adopter-templates"]) + tuple(data["doctor-templates"])
    EXCLUDED_TREES = tuple(data["excluded-trees"])
    DECLARATION_FILES = tuple(data["declaration-files"])
    DECLARATION_COPIES = dict(data["declaration-copies"])
    TOMLLIB_TABLE_REL = data["tomllib-table"]


def load_host_patch(root, floor):
    """The python-host-patch the OPF host policy names, schema-checked and a patch release of floor: a
    missing, unreadable or malformed host policy is cannot-evaluate, never a pass and never a read of a
    parent's host policy."""
    try:
        data = tomllib.loads(_read_text(root / HOST_SOURCE_REL))
    except tomllib.TOMLDecodeError as exc:
        raise CannotEvaluate("{}: does not parse: {}".format(HOST_SOURCE_REL, exc))
    # tomllib also raises a BARE ValueError on an integer literal past CPython's int-string limit, a
    # RecursionError on a deeply nested array or inline table or, in a release that bounds key depth, on a
    # key of more than 1000 dotted parts, and a MemoryError on a source too large for the memory left:
    # each is cannot-evaluate, never a traceback.
    except (ValueError, RecursionError, MemoryError) as exc:
        raise CannotEvaluate("{}: does not parse ({}): {}".format(HOST_SOURCE_REL, type(exc).__name__,
                                                                     exc))
    patch = data.get("python-host-patch")
    if set(data) != HOST_SOURCE_KEYS or type(data["format-version"]) is not int \
            or data["format-version"] != 1 or not isinstance(patch, str) \
            or HOST_PATCH_RE.fullmatch(patch) is None:
        raise CannotEvaluate("{}: want exactly format-version = 1 and python-host-patch = "
                             "\"MAJOR.MINOR.PATCH\"".format(HOST_SOURCE_REL))
    match = HOST_PATCH_RE.fullmatch(patch)
    if (int(match.group(1)), int(match.group(2))) != floor:
        raise CannotEvaluate("{}: python-host-patch {!r} is not a patch release of the floor {}.{} in {}"
                             .format(HOST_SOURCE_REL, patch, floor[0], floor[1], SOURCE_REL))
    return patch


def load_source(root):
    """Parse and schema-check both OPF policies, bind the legs' surfaces from them, and return the floor
    policy's normalized fields."""
    try:
        data = tomllib.loads(_read_text(root / SOURCE_REL))
    except (tomllib.TOMLDecodeError, ValueError, RecursionError, MemoryError) as exc:
        # tomllib raises a bare ValueError on an over-long integer literal, RecursionError on deep nesting or
        # a key of more than 1000 dotted parts, and MemoryError on a many-part key under a memory limit: each
        # is exit 2.
        raise CannotEvaluate("{}: does not parse: {}".format(SOURCE_REL, exc))
    if set(data) != set(POLICY_KEYS):
        raise CannotEvaluate("{}: keys {} (want exactly {})".format(
            SOURCE_REL, sorted(data), sorted(POLICY_KEYS)))
    for key, kind in POLICY_KEYS.items():
        if type(data[key]) is not kind:
            raise CannotEvaluate("{}: {} must be a {}".format(SOURCE_REL, key, kind.__name__))
    if data["format-version"] != 1:
        raise CannotEvaluate("{}: format-version must be the integer 1".format(SOURCE_REL))
    floor_text = data["python-floor"]
    match = FLOOR_RE.fullmatch(floor_text)
    if match is None:
        raise CannotEvaluate("{}: python-floor must be a \"MAJOR.MINOR\" string, got {!r}".format(
            SOURCE_REL, floor_text))
    for key in POLICY_LISTS:
        if not all(isinstance(item, str) for item in data[key]) or data[key] != sorted(set(data[key])):
            raise CannotEvaluate("{}: {} must be a sorted, unique list of strings".format(SOURCE_REL, key))
    surfaces, nonblocking = data["guarded-surfaces"], data["nonblocking-surfaces"]
    if not set(nonblocking) <= set(surfaces):
        raise CannotEvaluate("{}: nonblocking-surfaces must each be listed in guarded-surfaces".format(
            SOURCE_REL))
    for key in POLICY_PATHS:
        for item in data[key] if isinstance(data[key], list) else [data[key]]:
            fault = _policy_path_fault(item, directory=key == "excluded-trees")
            if fault is not None:
                raise CannotEvaluate("{}: {} entry {!r} {}".format(SOURCE_REL, key, item, fault))
    for item in data["workflow-files"]:
        if item.rsplit("/", 1)[0] != data["workflow-dir"]:
            raise CannotEvaluate("{}: workflow-files entry {!r} is not a file in workflow-dir {!r}".format(
                SOURCE_REL, item, data["workflow-dir"]))
    if data["host-patch-workflow"] not in data["workflow-files"]:
        raise CannotEvaluate("{}: host-patch-workflow {!r} is not a workflow-files entry".format(
            SOURCE_REL, data["host-patch-workflow"]))
    if not data["host-patch-jobs"] or not all(HOST_JOB_RE.fullmatch("  " + job + ":")
                                              for job in data["host-patch-jobs"]):
        raise CannotEvaluate("{}: host-patch-jobs must name at least one job id, each a plain "
                             "[A-Za-z_][A-Za-z0-9_-]* key".format(SOURCE_REL))
    adopter, doctor = set(data["adopter-templates"]), set(data["doctor-templates"])
    if not adopter or not doctor or adopter & doctor or (adopter | doctor) & set(data["workflow-files"]):
        raise CannotEvaluate("{}: adopter-templates and doctor-templates must each be nonempty and name "
                             "no file the other or workflow-files names".format(SOURCE_REL))
    for name, count in data["declaration-copies"].items():
        if name not in data["declaration-files"] or type(count) is not int or count < 2:
            raise CannotEvaluate("{}: declaration-copies entry {!r} must name a declaration-files entry "
                                 "with an integer of at least 2".format(SOURCE_REL, name))
    for item in surfaces:
        if not SURFACE_RE.fullmatch(item) or any(part in (".", "..") for part in item.split("/")):
            raise CannotEvaluate("{}: guarded-surfaces entry {!r} is not an OPF-root-relative .py path"
                                 .format(SOURCE_REL, item))
    for item in surfaces:
        _read_text(root / item)
    floor = (int(match.group(1)), int(match.group(2)))
    _bind(data, load_host_patch(root, floor))
    return {"floor": floor, "surfaces": tuple(surfaces),
            "nonblocking": frozenset(nonblocking), "completeness": data["completeness-check"],
            "documentation": data["documentation-check"]}


def source_findings(source):
    if source["floor"] != FLOOR:
        return ["{}: python-floor is {}.{}, but the decided floor is {}.{}".format(
            SOURCE_REL, *source["floor"], *FLOOR)]
    return []


def switch_findings(source):
    if source["documentation"] != DOCUMENTATION_CHECK:
        return ["{}: documentation-check is {}, but its decided value is {} (DOCUMENTATION_CHECK in "
                "tools/check_python_floor.py); changing it is a reviewed change to this gate".format(
                    SOURCE_REL, *("true" if value else "false"
                                  for value in (source["documentation"], DOCUMENTATION_CHECK)))]
    return []


def _workflow_job(lines):
    """The id of the job the last of lines sits in: the nearest earlier job id at indent 2 (HOST_JOB_RE)
    under a top-level jobs: line, or None when the line sits elsewhere or an indent-2 line above it is not a
    plain job id."""
    job = None
    for text in reversed(lines[:-1]):
        stripped = text.strip(" \t")
        if not stripped or stripped.startswith("#"):
            continue
        indent = _indent(text)
        if indent == 0:
            return job if text.split("#", 1)[0].rstrip(" \t") == "jobs:" else None
        if indent == 2 and job is None:
            header = HOST_JOB_RE.fullmatch(text)
            if header is None:
                return None
            job = header.group("id")
    return None


def _host_patch_pin(root, rel, lines, value, want, host):
    """Whether a pin that differs from the floor is the host patch: rel is HOST_WORKFLOW_REL, the pin, the
    last of lines, sits in a HOST_JOBS job, value is a patch release of the floor, and it equals the
    python-host-patch the OPF host policy names (HOST_PATCH, read and checked by load_source)."""
    if rel != HOST_WORKFLOW_REL or _workflow_job(lines) not in HOST_JOBS \
            or not value.startswith(want + ".") or HOST_PATCH_RE.fullmatch(value) is None:
        return False
    return value == HOST_PATCH


def pin_findings(root, floor):
    want = "%d.%d" % floor
    try:
        names = sorted(os.listdir(root / WORKFLOWS_REL))
    except OSError as exc:
        raise CannotEvaluate("{}: cannot list: {}".format(WORKFLOWS_REL, exc))
    # Every YAML file the workflow directory holds, and every workflow file the policy lists (a listed
    # file that is missing is cannot-evaluate when it is read).
    targets = [(rel, False) for rel in sorted(set(WORKFLOWS_REL + "/" + name for name in names
                                                  if _yaml_name(name)) | set(WORKFLOW_FILES))]
    targets += [(rel, True) for rel in PIN_FILES]
    if "action-files" in RULES:
        targets += [(rel, False) for rel in _action_files(root)]
    findings, seen, host = [], set(), {}
    while targets:
        rel, required = targets.pop(0)
        if rel in seen:
            continue
        seen.add(rel)
        pins = 0
        yaml_file = _yaml_name(rel)
        # pin: (key column, line number) of the last strict pin until the next non-blank line is
        # judged. No line is skipped as block scalar text: every line naming the key is judged.
        pin = None
        lines, pin_lines = [], set()
        for number, line in enumerate(_read_text(root / rel).split("\n"), 1):
            line = line[:-1] if line.endswith("\r") else line
            lines.append(line)
            for char, label in LINE_BREAKS.items():
                if char in line:
                    raise CannotEvaluate(
                        "{}:{}: carries {}, which a YAML reader may read as a line break; this line "
                        "model splits only on newline and cannot evaluate the file, so remove it"
                        .format(rel, number, label))
            if not line.strip(" \t"):
                continue
            indent = len(line) - len(line.lstrip(" "))
            deeper = line[indent] == "\t"
            if pin is not None:
                if indent > pin[0] or deeper:
                    raise CannotEvaluate(
                        "{}:{}: more indented than the python-version pin on line {} (a continuation "
                        "or nested value this gate cannot judge); write the pin on one line as "
                        "python-version: 'X.Y'".format(rel, number, pin[1]))
                pin = None
            if not _names(line, PIN_KEY):
                node = NODE_START_RE.fullmatch(line) if yaml_file else None
                if node is not None and not _closes_on_line(node.group(1)):
                    raise CannotEvaluate("{}:{}: a quoted or flow value continues past its line; "
                                         "this line model cannot follow it".format(rel, number))
                continue
            match = PIN_LINE_RE.fullmatch(line)
            if match is None or match.group("key") != PIN_KEY or _names(line, INPUT_ENV):
                raise CannotEvaluate(_refusal(rel, number, line))
            pin = (match.start("key"), number)
            pins += 1
            pin_lines.add(number)
            plain = match.group("plain")
            value = next(group for group in match.group("plain", "single", "double")
                         if group is not None)
            if value != want and not _host_patch_pin(root, rel, lines, value, want, host):
                findings.append("{}:{}: python-version {!r} differs from the floor {!r}".format(
                    rel, number, value, want))
            elif value == want and plain is not None and want.endswith("0"):
                findings.append("{}:{}: python-version {} is unquoted; YAML reads it as a number, so "
                                "quote it as '{}'".format(rel, number, value, want))
        if required and not pins:
            findings.append("{}: carries no python-version pin (want {!r})".format(rel, want))
        findings.extend(_setup_python_findings(rel, lines, pin_lines))
        if yaml_file:
            _flow_uses_check(rel, lines)
            _grammar_check(rel, lines)
            targets += [(target, False) for target in _local_targets(root, rel, lines)]
    return findings


def _yaml_name(name):
    """Whether a file name ends in .yml or .yaml, without regard to case: a file the runner may read as
    a workflow is scanned, never skipped."""
    return (name.lower() if "extension-case" in RULES else name).endswith((".yml", ".yaml"))


def _indent(line):
    return len(line) - len(line.lstrip(" "))


def _parent(lines, index):
    """Index of the nearest earlier non-blank, non-comment line less indented than lines[index], or
    None."""
    for back in range(index - 1, -1, -1):
        text = lines[back]
        if text.strip(" \t") and not text.lstrip(" \t").startswith("#") \
                and _indent(text) < _indent(lines[index]):
            return back
    return None


def _key_parent(lines, index):
    """Index of the nearest earlier non-blank, non-comment line whose first key (after any `- `
    marker) starts left of the first key of lines[index], or None: the key lines[index] nests under. A
    key at the same column, on a `- ` line or not, is its sibling in one mapping, never its parent."""
    column = LEAD_RE.match(lines[index]).end()
    for back in range(index - 1, -1, -1):
        text = lines[back]
        if text.strip(" \t") and not text.lstrip(" \t").startswith("#") \
                and LEAD_RE.match(text).end() < column:
            return back
    return None


def _flow_uses_check(rel, lines):
    """Refuse a line that holds a flow mapping with a key named uses (any case, plain or quoted): this
    line model does not read a step written as a flow mapping, so it can neither judge nor scan the
    action that step runs, a local ./PATH included. Every line is judged as written, script text too."""
    if "flow-uses" not in RULES:
        return
    for index, line in enumerate(lines):
        start = line.find("{")
        for key in (FLOW_KEY_RE.finditer(line, start) if start >= 0 else ()):
            name = next(group for group in key.group("double", "single", "plain")
                        if group is not None).strip(" \t")
            if name.lower() == "uses" or name.upper() == "USES":
                raise CannotEvaluate(
                    "{}:{}: a flow mapping with the key {}, a step written as a flow mapping; this "
                    "line model does not read one, so it cannot judge or scan the action it runs (a "
                    "local ./PATH included); write the step as a block mapping, - uses: owner/action@ref"
                    .format(rel, index + 1, name))


def _setup_python_findings(rel, lines, pin_lines):
    """Each step that uses actions/setup-python must set its python-version input on a strict pin line
    (pin_lines, 1-based) whose parent line (_key_parent) is the step's own with: key; a step without one
    is a finding,
    since the action then falls back to another interpreter. What the line model cannot judge is
    cannot-evaluate: a uses: line that is not one plain or quoted literal, a line that names
    setup-python outside such a line, a setup-python uses: line whose `-` marker it cannot find, a uses:
    value that names setup-python in another shape (_setup_python_action), and a with: whose body is a
    block sequence."""
    findings = []
    for index, line in enumerate(lines):
        number = index + 1
        with_key = WITH_LINE_RE.fullmatch(line) if "with-list" in RULES else None
        child = with_key and next((text for text in lines[index + 1:] if text.strip(" \t")
                                   and not text.lstrip(" \t").startswith("#")), None)
        if child and _indent(child) >= with_key.start("key") \
                and (child.lstrip(" ") == "-" or child.lstrip(" ").startswith("- ")):
            raise CannotEvaluate(
                "{}:{}: a with: whose body is a block sequence (a `- ` entry), not a mapping of inputs; "
                "a pin inside it sets no input, so this line model cannot evaluate the step; write "
                "with: then python-version: 'X.Y' with no `- ` marker".format(rel, number))
        key = KEY_RE.match(line)
        uses = key is not None and (key.group(2).lower() == "uses" or key.group(2).upper() == "USES")
        match = USES_LINE_RE.fullmatch(line) if uses else None
        if uses and match is None:
            raise CannotEvaluate(
                "{}:{}: a uses: line that is not one plain or quoted literal (an anchor, alias, tag, "
                "escape, block scalar, empty value or a key in another case); this line model cannot "
                "judge which action it runs, so write it as uses: owner/action@ref".format(rel, number))
        if match is None:
            if _names(line, "setup-python"):
                raise CannotEvaluate(
                    "{}:{}: names setup-python outside a literal uses: line (a comment, a flow mapping, "
                    "a continued value or another key); this line model cannot judge whether a step "
                    "runs it, so name it only in uses: actions/setup-python@REF".format(rel, number))
            continue
        value = next(group for group in match.group("plain", "single", "double") if group is not None)
        if not _setup_python_action(rel, number, value):
            continue
        column = match.start("key")
        if column > _indent(line):
            dash = index
        else:
            dash = _parent(lines, index)
            marker = STEP_MARKER_RE.match(lines[dash]) if dash is not None else None
            if marker is None or marker.end() != column:
                raise CannotEvaluate(
                    "{}:{}: a setup-python uses: line whose step `-` marker this line model cannot "
                    "find; write the step as a block sequence entry".format(rel, number))
        end = next((fwd for fwd in range(index + 1, len(lines)) if lines[fwd].strip(" \t")
                    and not ("comment-span" in RULES and lines[fwd].lstrip(" \t").startswith("#"))
                    and _indent(lines[fwd]) <= _indent(lines[dash])), len(lines))
        pinned = False
        for pin_index in range(dash, end):
            if pin_index + 1 not in pin_lines:
                continue
            parent = _key_parent(lines, pin_index) if "with-child" in RULES \
                else _parent(lines, pin_index)
            with_key = WITH_LINE_RE.fullmatch(lines[parent]) if parent is not None else None
            if parent is not None and parent >= dash and with_key is not None \
                    and with_key.start("key") == column:
                pinned = True
        if not pinned:
            findings.append(
                "{}:{}: a step that uses actions/setup-python sets no python-version input (no strict "
                "pin line directly under the step's with:), so the action falls back to another "
                "interpreter; add python-version: 'X.Y' under its with:".format(rel, number))
    return findings


def _setup_python_action(rel, number, value):
    """Whether a literal uses: value runs actions/setup-python. A value that names setup-python in any
    case, or whose path split on / with empty parts dropped begins actions/setup-python, must be exactly
    actions/setup-python@REF or actions/setup-python/PATH@REF (any case; no empty, . or .. path part, no
    .git suffix) or it is cannot-evaluate: the runner may resolve another spelling to the action."""
    if "uses-shape" not in RULES:
        action = value.split("@", 1)[0].lower()
        return action == SETUP_PYTHON or action.startswith(SETUP_PYTHON + "/")
    path, at, ref = value.partition("@")
    parts, wanted = path.lower().split("/"), SETUP_PYTHON.split("/")
    if not _names(value, "setup-python") and [part for part in parts if part][:2] != wanted:
        return False
    if at and ref and "@" not in ref and parts[:2] == wanted and not path.lower().endswith(".git") \
            and all(part and part not in (".", "..") for part in parts):
        return True
    raise CannotEvaluate(
        "{}:{}: a uses: value {!r} that names setup-python but is not exactly actions/setup-python@REF "
        "or actions/setup-python/PATH@REF (a leading, doubled or trailing slash, a .git suffix, another "
        "owner or name); the runner may resolve it to the action, so this line model cannot evaluate "
        "the step; write uses: actions/setup-python@REF".format(rel, number, value))


def _plain_scalar(text, flow):
    """Whether text (its comment removed) is one plain scalar: it does not open with an indicator, and
    holds no ': ', no trailing colon and, inside a flow node, none of ,[]{}."""
    if not text or text != text.strip(" \t"):
        return False
    if text[0] in PLAIN_INDICATORS and not (text[0] in "-?:" and len(text) > 1 and text[1] not in " \t"):
        return False
    if ": " in text or ":\t" in text or text.endswith(":"):
        return False
    return not (flow and any(char in text for char in ",[]{}"))


def _flow_node(text):
    """Whether text is one single-line flow sequence or mapping of plain or quoted scalars (no nesting,
    no escapes; a mapping entry is keyed by a plain key, each key once), then an optional comment."""
    close, index, keys = "]" if text[0] == "[" else "}", 1, set()
    while True:
        entry = FLOW_ENTRY_RE.match(text, index)
        if entry is None:
            return False
        key, node = entry.group("key"), entry.group("node")
        if close == "]" and key is not None or close != "]" and node is not None and key is None:
            return False
        if key is not None:
            if key.lower() in keys:
                return False
            keys.add(key.lower())
        if node is not None and node[0] not in "'\"" and not _plain_scalar(node.strip(" \t"), True):
            return False
        index = entry.end()
        if entry.group("sep") != ",":
            return entry.group("sep") == close and TRAILER_RE.fullmatch(text, index) is not None


def _value_shape(text):
    """The shape of the value after `key:` or `- ` (no leading whitespace): "empty", "scalar",
    "block" (a block scalar indicator) or None, outside the enumerated grammar."""
    if not text or text.startswith("#"):
        return "empty"
    if text[0] in "|>":
        return "block" if BLOCK_INDICATOR_RE.fullmatch(text) else None
    if text[0] in "'\"":
        quoted = QUOTED_RE.match(text)
        return "scalar" if quoted and TRAILER_RE.fullmatch(text, quoted.end()) else None
    if text[0] in "[{":
        return "scalar" if _flow_node(text) else None
    return "scalar" if _plain_scalar(re.split(r"[ \t]#", text, maxsplit=1)[0].rstrip(" \t"), False) \
        else None


def _grammar_refusal(rel, number, text):
    """The cannot-evaluate error for a line outside the enumerated grammar; text follows any `- `."""
    if text.startswith("?"):
        what = "an explicit key (a line led by ?)"
    elif text.startswith(":"):
        what = "the value line of an explicit key (a line led by :)"
    elif text.startswith(("---", "...")):
        what = "a document marker after the start of the file"
    elif text == "-" or text.startswith(("- ", "-\t")):
        what = "a nested block sequence on one line"
    elif text.startswith("<<"):
        what = "a merge key"
    elif text[:1] in "'\"" and re.match(r"('[^']*'|\"[^\"]*\") *:(?:[ \t]|$)", text):
        what = "a quoted key"
    elif "\\" in text and "\"" in text:
        what = "a double-quoted scalar with a backslash escape"
    elif re.match(r"(?:[&*!]|[^:]*:[ \t]+[&*!])", text):
        what = "an anchor, an alias or a tag"
    elif re.match(r"[^:'\"#]* [^:'\"#]*:(?:[ \t]|$)", text):
        what = "a key with a space in it"
    elif re.search(r"(?:^|[ \t\[{,:])['\"]", text):
        what = "a quoted scalar that does not close on its line"
    else:
        what = "a line outside the enumerated plain grammar"
    return CannotEvaluate(
        "{}:{}: {}; this line model reads only plain keys, one-line plain, quoted or flow values, "
        "and block scalars skipped by indentation, so it cannot evaluate the file".format(
            rel, number, what))


def _grammar_check(rel, lines):
    """Hold every line of a workflow or action file to the enumerated plain grammar (module docstring):
    a block scalar's body is skipped only by indentation, a document marker may only open the file, and
    a key repeated in one block mapping is refused. Any other line is cannot-evaluate."""
    body, stack, started, scalar = None, [], False, None
    for index, line in enumerate(lines):
        number, content = index + 1, line.lstrip(" ")
        indent = len(line) - len(content)
        if body is not None:
            if not content.strip(" \t") or indent > body:
                continue
            body = None
        if not content.strip(" \t") or content.startswith("#"):
            continue
        if "grammar" in RULES and content.startswith("\t"):
            raise CannotEvaluate("{}:{}: a tab in the indentation; this line model cannot evaluate "
                                 "the file".format(rel, number))
        if content.startswith("---") and TRAILER_RE.fullmatch(content, 3) and not started:
            started = True
            continue
        started = True
        column, text, item = indent, content, False
        if "grammar" in RULES and scalar is not None and indent > scalar[0] \
                and (content == "-" or content.startswith("- ")):
            raise CannotEvaluate(
                "{}:{}: a plain scalar continued on the next line (a `- ` line more indented than the "
                "one-line value on line {}); this line model cannot evaluate the file".format(
                    rel, number, scalar[1]))
        scalar = None
        if text == "-" or text.startswith("- "):
            item, text = True, text[1:].lstrip(" ")
            column = len(line) - len(text)
            while "duplicate-key" in RULES and stack and stack[-1][0] > indent:
                stack.pop()
            if not text or text.startswith("#"):
                continue
            if "grammar" in RULES and (text == "-" or text.startswith(("- ", "-\t"))):
                raise _grammar_refusal(rel, number, text)
        key = GRAMMAR_KEY_RE.fullmatch(text)
        if key is not None:
            name = key.group("key").lower()
            while "duplicate-key" in RULES and stack and (stack[-1][0] > column
                                                          or item and stack[-1][0] == column):
                stack.pop()
            if "duplicate-key" in RULES and stack and stack[-1][0] == column:
                if name in stack[-1][1]:
                    raise CannotEvaluate(
                        "{}:{}: the key {} repeats line {} in the same mapping; YAML readers "
                        "keep one of the two or reject the file, so this line model cannot evaluate "
                        "it".format(rel, number, key.group("key"), stack[-1][1][name]))
                stack[-1][1][name] = number
            elif "duplicate-key" in RULES:
                stack.append((column, {name: number}))
            shape = _value_shape(key.group("value") or "")
        else:
            shape = _value_shape(text) if item else None
        if shape is None and "grammar" in RULES:
            raise _grammar_refusal(rel, number, text)
        if shape == "scalar":
            scalar = (column if key is not None else indent, number)
        if shape == "block":
            body = column if key is not None else indent


def _action_files(root):
    """Every local action file (ACTION_FILES) under root, outside SKIPPED_DIR_NAMES, repo-relative."""
    def _fail(exc):
        raise CannotEvaluate("cannot walk the tree for action files: {}".format(exc))

    found = []
    for dirpath, dirnames, filenames in os.walk(root, onerror=_fail):
        dirnames[:] = sorted(name for name in dirnames if name not in SKIPPED_DIR_NAMES)
        rel_dir = Path(dirpath).relative_to(root).as_posix()
        found += [(name if rel_dir == "." else rel_dir + "/" + name) for name in sorted(filenames)
                  if (name.lower() if "extension-case" in RULES else name) in ACTION_FILES]
    return found


def _local_targets(root, rel, lines):
    """The files that the local `uses: ./PATH` lines of a scanned file run: PATH itself when it names a
    .yml or .yaml file (a local reusable workflow), else PATH/action.yml or PATH/action.yaml. A local
    path outside the tree or with no such file is cannot-evaluate."""
    base, found = root.resolve(), []
    for index, line in enumerate(lines):
        match = USES_LINE_RE.fullmatch(line)
        value = None if match is None else next(
            group for group in match.group("plain", "single", "double") if group is not None)
        if value is None or not value.startswith("./"):
            continue
        path = (base / value).resolve()
        inside = path == base or base in path.parents
        if inside and _yaml_name(value):
            candidates = [path] if path.is_file() else []
        else:
            candidates = [path / name for name in ACTION_FILES if inside and (path / name).is_file()]
        if not candidates:
            if "local-uses-target" in RULES:
                raise CannotEvaluate(
                    "{}:{}: uses: {} names a local path with no action file inside this tree; "
                    "this gate cannot evaluate the step it runs".format(rel, index + 1, value))
            continue
        if "action-files" in RULES:
            found += [candidate.relative_to(base).as_posix() for candidate in candidates]
    return found


def _names(line, token):
    """Whether a line names token without regard to case: under Python's lower-casing, or under its
    upper-casing, the fold the Actions runner applies to an input name (which also maps the dotless i
    and the long s onto ASCII letters)."""
    return token.lower() in line.lower() or token.upper() in line.upper()


def _refusal(rel, number, line):
    """The cannot-evaluate message for a line that names the key (or INPUT_ENV) but is not a strict
    lower-case pin. Every such line is exit 2; the message says what the gate cannot judge."""
    where = "{}:{}: ".format(rel, number)
    if _names(line, INPUT_ENV):
        return (where + "names {}, an environment variable that can set the python-version input "
                "outside a pin; this gate cannot evaluate it, so remove it and set the interpreter "
                "only with a literal pin python-version: 'X.Y'".format(INPUT_ENV))
    strict = PIN_LINE_RE.fullmatch(line)
    if strict is not None:
        return (where + "python-version key spelled {!r}; the runner reads an input by its upper-cased "
                "name, so this gate cannot evaluate a key in any other case; spell the key in lower "
                "case as python-version: 'X.Y'".format(strict.group("key")))
    key = KEY_RE.match(line)
    if key is not None and _names(key.group(2), PIN_KEY) and len(key.group(2)) == len(PIN_KEY):
        value = line[key.end():].strip(" \t")
        if value.startswith(("[", "{")) or "${{" in value:
            return (where + "this gate cannot evaluate a matrix or expression pin; write each pinned "
                    "interpreter as a literal single-line pin python-version: 'X.Y'")
    elif key is not None or line.lstrip(" \t").startswith("#"):
        return (where + "the text python-version appears outside a pin (in a comment, a value or "
                "another key) and must not; name it only as the key of a literal single-line pin "
                "python-version: 'X.Y'")
    return where + "unrecognized python-version spelling; write the pin on one line as " \
        "python-version: 'X.Y'"


def _closes_on_line(text):
    """Whether a node that opens with a quote or a flow indicator closes on its own line: a quote
    (double with backslash escapes, single with '' escapes) opens only at a token start, and a `#`
    after whitespace outside quotes ends the line."""
    quote, depth, index = None, 0, 0
    while index < len(text):
        char = text[index]
        if quote == "\"":
            if char == "\\":
                index += 1
            elif char == "\"":
                quote = None
        elif quote == "'":
            if char == "'" and text[index + 1:index + 2] == "'":
                index += 1
            elif char == "'":
                quote = None
        elif char in "\"'" and (index == 0 or text[index - 1] in " \t[{,:"):
            quote = char
        elif char in "[{":
            depth += 1
        elif char in "]}":
            depth -= 1
        elif char == "#" and index and text[index - 1] in " \t":
            break
        index += 1
    return quote is None and depth <= 0


def _parse(root, rel):
    try:
        return ast.parse(_read_text(root / rel), filename=rel)
    except (SyntaxError, ValueError) as exc:
        raise CannotEvaluate("{}: does not parse: {}".format(rel, exc))
    except (MemoryError, RecursionError) as exc:
        # The parser's own limits (too deep a nesting or too long a chain): cannot-evaluate, never a
        # crash that reads as a finding.
        raise CannotEvaluate("{}: too complex to parse: {}: {}".format(rel, type(exc).__name__, exc))


def _dump(node):
    return ast.dump(node, include_attributes=False)


def guard_text(name, floor, modes=None, code=REFUSAL_EXIT):
    """The canonical guard with code as its refusal exit, or with modes (a tuple of mode names) its hook
    form, whose deny exit is fixed at 2 (code is ignored there)."""
    if modes is None:
        return GUARD_TEMPLATE.format(name=name, major=floor[0], minor=floor[1], code=code)
    return HOOK_GUARD_TEMPLATE.format(name=name, major=floor[0], minor=floor[1],
                                      modes=repr(tuple(modes)))


def refusal_exit(rel, nonblocking):
    """The exit the guard and dynamic legs require of rel's refusal: 2, or 1 for a nonblocking-surfaces
    entry; a HOOK_SURFACES entry is always 2 (load_source refuses one listed as nonblocking)."""
    if rel in HOOK_SURFACES:
        return REFUSAL_EXIT
    return NONBLOCKING_EXIT if rel in nonblocking else REFUSAL_EXIT


def _canonical(name, floor, modes=None, code=REFUSAL_EXIT):
    return [_dump(node) for node in ast.parse(guard_text(name, floor, modes, code)).body]


def hook_modes(tree):
    """The FLOOR_FAIL_OPEN_MODES literal of a hook-form guard: the tuple of mode names the second
    statement after the preamble assigns, or None when that statement is not one plain assignment of a
    non-empty, sorted, unique tuple of lower-case identifier strings to that name."""
    start = _preamble_end(tree)
    node = tree.body[start + 1] if len(tree.body) > start + 1 else None
    if not (isinstance(node, ast.Assign) and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name) and node.targets[0].id == MODES_NAME
            and isinstance(node.value, ast.Tuple) and node.value.elts):
        return None
    modes = []
    for elt in node.value.elts:
        if not (isinstance(elt, ast.Constant) and type(elt.value) is str and MODE_RE.fullmatch(elt.value)):
            return None
        modes.append(elt.value)
    return tuple(modes) if modes == sorted(set(modes)) else None


def _preamble_end(tree):
    """Index of the first statement after the module docstring and any `from __future__` imports."""
    body, index = tree.body, 0
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
            and isinstance(body[0].value.value, str):
        index = 1
    while index < len(body) and isinstance(body[index], ast.ImportFrom) \
            and body[index].module == "__future__" and body[index].level == 0:
        index += 1
    return index


def _too_complex(rel, exc):
    """A traversal limit (too deep a tree for ast.dump or ast.unparse, or memory): cannot-evaluate,
    never a crash that reads as a finding."""
    return CannotEvaluate("{}: too complex to evaluate: {}: {}".format(rel, type(exc).__name__, exc))


def guard_findings(root, surfaces, floor, nonblocking=()):
    findings = []
    for rel in surfaces:
        tree = _parse(root, rel)
        code = refusal_exit(rel, nonblocking)
        start = _preamble_end(tree)
        try:
            modes = hook_modes(tree) if rel in HOOK_SURFACES else None
            want = None if rel in HOOK_SURFACES and modes is None \
                else _canonical(Path(rel).name, floor, modes, code)
            opens = want is not None \
                and [_dump(node) for node in tree.body[start:start + len(want)]] == want
        except (MemoryError, RecursionError) as exc:
            raise _too_complex(rel, exc)
        if not opens and rel in HOOK_SURFACES:
            findings.append(
                "{}: does not open with the canonical floor guard in its hook form (only a docstring "
                "and `from __future__` imports may precede `import sys`, then a {} tuple of sorted, "
                "unique mode names, then `if tuple(sys.version_info[:2]) < ({}, {}):` warning on exit 0 "
                "for a listed mode and refusing as {} with exit {} otherwise; see HOOK_GUARD_TEMPLATE)"
                .format(rel, MODES_NAME, floor[0], floor[1], Path(rel).name, code))
        elif not opens:
            findings.append(
                "{}: does not open with the canonical floor guard (only a docstring and `from "
                "__future__` imports may precede `import sys` and `if tuple(sys.version_info[:2]) < "
                "({}, {}):`, refusing as {} with exit {}; see GUARD_TEMPLATE)".format(
                    rel, floor[0], floor[1], Path(rel).name, code))
    return findings


def below_floor(floor):
    """The two patched versions the dynamic leg refuses: a late micro release of the previous minor,
    and the .0 release two minors back."""
    return ((floor[0], floor[1] - 1, 9), (floor[0], max(floor[1] - 2, 0), 0))


def expected_refusal(name, floor, version, executable):
    return ("error: %s requires Python %d.%d or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % ((name,) + tuple(floor) + tuple(version) + (executable or "unknown interpreter",)))


def expected_warning(name, floor, version, executable, mode):
    """The hook form's stdout for a fail-open mode under the floor: one systemMessage JSON line."""
    return json.dumps(dict(systemMessage=(
        "AIQT guardrail: the %s check could not run (%s); surfacing a warning rather than blocking "
        "(non-blocking by design on this event)."
        % (mode, expected_refusal(name, floor, version, executable).strip())))) + "\n"


def _child(code, args, flags, cwd):
    try:
        proc = subprocess.run([sys.executable, "-I", "-B", *flags, "-c", code, *args], cwd=cwd,
                              env=CHILD_ENV, stdin=subprocess.DEVNULL, capture_output=True,
                              timeout=CHILD_TIMEOUT)
    except (OSError, subprocess.SubprocessError) as exc:
        raise CannotEvaluate("child launch failed: {}".format(exc))
    return (proc.returncode, proc.stdout.decode("utf-8", "backslashreplace"),
            proc.stderr.decode("utf-8", "backslashreplace"))


def refusal_observed(path, version, flags, args=()):
    """Run one entrypoint at a patched version with the argv args; return (exit, stdout, stderr,
    working-dir entries)."""
    try:
        with tempfile.TemporaryDirectory(prefix="python-floor-cwd-") as cwd:
            rc, out, err = _child(REFUSAL_CHILD, [str(path), "%d.%d.%d" % version, *args], flags, cwd)
            return rc, out, err, sorted(os.listdir(cwd))
    except OSError as exc:
        raise CannotEvaluate("temporary working directory: {}".format(exc))


def boundary_observed(prefix, version, flags):
    """Run a guard prefix at a patched version (or "real"); return (exit, stdout, stderr)."""
    arg = version if version == "real" else "%d.%d.%d" % version
    try:
        with tempfile.TemporaryDirectory(prefix="python-floor-cwd-") as cwd:
            return _child(BOUNDARY_CHILD, [prefix, arg], flags, cwd)
    except OSError as exc:
        raise CannotEvaluate("temporary working directory: {}".format(exc))


def stderr_vector_observed(path, version, vector, args=()):
    """Run one entrypoint at a patched version with stderr unavailable (one of STDERR_VECTORS);
    return (exit, stdout, stderr, working-dir entries). The guard may be unable to write its
    refusal, so stderr must stay empty and the exit must still be the refusal exit."""
    try:
        with tempfile.TemporaryDirectory(prefix="python-floor-cwd-") as cwd:
            if vector == "closed":
                rc, out, err = _child(CLOSED_FD2_CHILD,
                                      [REFUSAL_CHILD, str(path), "%d.%d.%d" % version, *args],
                                      (), cwd)
            else:
                rc, out, err = _child(BROKEN_STDERR_CHILD,
                                      [str(path), "%d.%d.%d" % version, vector, *args], (), cwd)
            return rc, out, err, sorted(os.listdir(cwd))
    except OSError as exc:
        raise CannotEvaluate("temporary working directory: {}".format(exc))


def guard_prefix(tree, name, floor, modes=None, code=REFUSAL_EXIT):
    """Source of the top-level statements up to and including the canonical guard (with modes, its hook
    form), or None."""
    want = _canonical(name, floor, modes, code)[-1]
    for index, node in enumerate(tree.body):
        if _dump(node) == want:
            return ast.unparse(ast.Module(body=tree.body[:index + 1], type_ignores=[]))
    return None


def dynamic_findings(root, surfaces, floor, nonblocking=()):
    findings = []
    for rel in surfaces:
        name = Path(rel).name
        code = refusal_exit(rel, nonblocking)
        for version in below_floor(floor):
            want = (code, "", expected_refusal(name, floor, version, sys.executable), [])
            for flags in FLAG_SETS:
                got = refusal_observed(root / rel, version, flags)
                if got != want:
                    findings.append(
                        "{} at patched {}.{}.{} under {}: got exit {}, stdout {!r}, stderr "
                        "{!r}, working-dir entries {!r}; want exit {}, empty stdout, the exact refusal "
                        "and an untouched working directory".format(
                            rel, *version, " ".join(flags) or "no flag", *got, code))
        try:
            tree = _parse(root, rel)
            modes = hook_modes(tree) if rel in HOOK_SURFACES else None
            prefix = None if rel in HOOK_SURFACES and modes is None \
                else guard_prefix(tree, name, floor, modes, code)
        except (MemoryError, RecursionError) as exc:
            raise _too_complex(rel, exc)
        version = below_floor(floor)[0]
        refusal = expected_refusal(name, floor, version, sys.executable)
        for mode in modes or ():
            got = refusal_observed(root / rel, version, (), [mode])
            if got != (0, expected_warning(name, floor, version, sys.executable, mode), refusal, []):
                findings.append(
                    "{} at patched {}.{}.{} with the fail-open mode {}: got exit {}, stdout {!r}, stderr "
                    "{!r}, working-dir entries {!r}; want exit 0, the exact warning, the refusal on "
                    "stderr and an untouched working directory".format(rel, *version, mode, *got))
        if modes is not None:
            got = refusal_observed(root / rel, version, (), [DENY_PROBE_MODE])
            if got != (REFUSAL_EXIT, "", refusal, []):
                findings.append(
                    "{} at patched {}.{}.{} with the mode {}, outside {}: got exit {}, stdout {!r}, "
                    "stderr {!r}, working-dir entries {!r}; want exit {}, empty stdout, the exact refusal "
                    "and an untouched working directory".format(
                        rel, *version, DENY_PROBE_MODE, MODES_NAME, *got, REFUSAL_EXIT))
        for vector in STDERR_VECTORS:
            got = stderr_vector_observed(root / rel, version, vector)
            if got != (code, "", "", []):
                findings.append(
                    "{} at patched {}.{}.{} with {}: got exit {}, stdout {!r}, stderr {!r}, "
                    "working-dir entries {!r}; want exit {}, empty stdout and stderr and an "
                    "untouched working directory (the refusal exit may not depend on the "
                    "diagnostic write or its flush)".format(
                        rel, *version, STDERR_VECTOR_LABELS[vector], *got, code))
        for mode in modes[:1] if modes else ():
            warning = expected_warning(name, floor, version, sys.executable, mode)
            for vector in STDERR_VECTORS:
                got = stderr_vector_observed(root / rel, version, vector, [mode])
                if got != (0, warning, "", []):
                    findings.append(
                        "{} at patched {}.{}.{} with the fail-open mode {} and {}: got exit {}, "
                        "stdout {!r}, stderr {!r}, working-dir entries {!r}; want exit 0, the "
                        "exact warning on stdout, empty stderr and an untouched working "
                        "directory".format(
                            rel, *version, mode, STDERR_VECTOR_LABELS[vector], *got))
        if prefix is None:
            findings.append("{}: no canonical guard statement to run at the floor boundary".format(rel))
            continue
        for version in (floor + (0,), "real"):
            label = version if version == "real" else "%d.%d.%d" % version
            for flags in FLAG_SETS:
                rc, out, err = boundary_observed(prefix, version, flags)
                if (rc, out) != (0, CONTINUED + "\n"):
                    findings.append(
                        "{}: the guard prefix at {} under {} did not continue (exit {}, stdout "
                        "{!r}, stderr {!r})".format(rel, label, " ".join(flags) or "no flag", rc, out,
                                                      err))
    return findings


def _excluded(rel):
    return any(rel == tree.rstrip("/") or rel.startswith(tree) for tree in EXCLUDED_TREES)


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


def _python_files(root, every=False):
    """Every repo-relative .py path outside EXCLUDED_TREES (inside them too when every is true),
    directory by directory in sorted order (a directory in SKIPPED_DIR_NAMES is not walked)."""
    def _fail(exc):
        raise CannotEvaluate("cannot walk the tree: {}".format(exc))

    for dirpath, dirnames, filenames in os.walk(root, onerror=_fail):
        rel_dir = Path(dirpath).relative_to(root).as_posix()
        prefix = "" if rel_dir == "." else rel_dir + "/"
        dirnames[:] = sorted(name for name in dirnames if name not in SKIPPED_DIR_NAMES
                             and (every or not _excluded(prefix + name + "/")))
        for name in sorted(filenames):
            rel = prefix + name
            if name.endswith(".py") and (every or not _excluded(rel)):
                yield rel


def _counted(rel, directory=False):
    """Whether a tracked path is in the completeness leg's scope: outside EXCLUDED_TREES and under no
    directory named in SKIPPED_DIR_NAMES (a directory path is judged by its own name too)."""
    parts = rel.split("/")
    return not _excluded(rel) and not SKIPPED_DIR_NAMES.intersection(parts if directory else parts[:-1])


def _listing_env(root):
    """The environment of the git ls-files child: the caller's, without its GIT_* variables (an ambient
    GIT_DIR or GIT_INDEX_FILE would list another index), with CHILD_ENV's pinned PATH (a git earlier on
    the caller's PATH is never the one run) and the grandparent of root as a ceiling, so the repository
    read is root's own or, in a nested checkout, its parent's, and never one further up."""
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(CHILD_ENV, GIT_CEILING_DIRECTORIES=str(Path(root).parent.parent), GIT_NO_LAZY_FETCH="1")
    return env


def _refuse_gitlinks(modes):
    """A gitlink (mode 160000, a submodule) in scope is cannot-evaluate: the listing holds only its
    path, never the files inside it, so an entrypoint there would go uncounted."""
    links = sorted(rel for rel, mode in modes.items() if mode == GITLINK_MODE and _counted(rel, True))
    if links:
        raise CannotEvaluate("cannot list the tracked files: {} is a gitlink (submodule, mode 160000); "
                             "the files inside it are not in the listing".format(", ".join(links)))


def _tracked_files(root):
    """The sorted repo-relative paths git tracks under root (git ls-files -s -z), the completeness
    leg's file set: an ignored or untracked file is never shipped. The child runs under _listing_env, so
    the read is of root's own repository by the git on the pinned PATH; core.fsmonitor=false keeps a
    configured hook from running. No git, a nonzero exit, a timeout, an empty listing, a record that is
    not mode, object, stage and path, an undecodable path or an in-scope gitlink is cannot-evaluate,
    never an empty set that would read as clean. A gitlink outside scope is left out."""
    try:
        proc = subprocess.run(["git", "-C", str(root), "-c", "core.fsmonitor=false", "-c",
                               "protocol.allow=never", "--no-optional-locks", "ls-files", "-s", "-z"],
                              env=_listing_env(root), stdin=subprocess.DEVNULL, capture_output=True,
                              timeout=CHILD_TIMEOUT)
    except (OSError, subprocess.SubprocessError) as exc:
        raise CannotEvaluate("cannot list the tracked files: {}".format(exc))
    if proc.returncode != 0:
        raise CannotEvaluate("cannot list the tracked files: git ls-files exit {}: {}".format(
            proc.returncode, proc.stderr.decode("utf-8", "backslashreplace").strip()))
    modes = {}
    for record in proc.stdout.split(b"\0"):
        if not record:
            continue
        meta, tab, path = record.partition(b"\t")
        if not tab or not path or len(meta.split(b" ")) != 3:
            raise CannotEvaluate("cannot list the tracked files: a record is not mode, object, stage "
                                 "and path: {!r}".format(record))
        try:
            modes[path.decode("utf-8")] = meta.split(b" ")[0]
        except UnicodeDecodeError as exc:
            raise CannotEvaluate("cannot list the tracked files: a path is not UTF-8: {}".format(exc))
    if not modes:
        raise CannotEvaluate("cannot list the tracked files: git ls-files listed none")
    _refuse_gitlinks(modes)
    return sorted(rel for rel, mode in modes.items() if mode != GITLINK_MODE)


def shipped_entrypoints(root):
    """Every tracked .py path outside EXCLUDED_TREES and SKIPPED_DIR_NAMES with a module-level main
    block."""
    return [rel for rel in _tracked_files(root) if rel.endswith(".py") and _counted(rel)
            and _has_main_block(_parse(root, rel))]


def completeness_findings(root, surfaces):
    listed = set(surfaces)
    return ["{}: a shipped entrypoint missing from guarded-surfaces in {}".format(rel, SOURCE_REL)
            for rel in shipped_entrypoints(root) if rel not in listed]


# The tomllib leg (see the module docstring): every tomllib.load and tomllib.loads call is HANDLED in its own
# function or classified by one row of TOMLLIB_TABLE_REL, the one recorded table.
TOMLLIB_TABLE_REL = "tools/tomllib_parse_sites.toml"
TOMLLIB_PARSERS = frozenset(("load", "loads"))
TOMLLIB_COVERED = (RecursionError, MemoryError, ValueError)
TOMLLIB_EXEMPT_REASONS = frozenset(("test-code", "self-emitted-bytes", "already-validated",
                                    "valueerror-passthrough"))
TOMLLIB_ROW_KEYS = {"CALLER": frozenset(("path", "function", "form", "count", "class", "caller")),
                    "EXEMPT": frozenset(("path", "function", "form", "count", "class", "reason"))}
TOMLLIB_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.GeneratorExp)


def _tomllib_module_name(name):
    """True for the module name tomllib or a dotted submodule name under it (tomllib._parser)."""
    return re.fullmatch(r"tomllib(?:\.[A-Za-z_][A-Za-z0-9_]*)*", name) is not None


def _tomllib_bindings(tree):
    """(module names, parser names, unresolved forms) a file binds anywhere in it: the bare name tomllib
    always, `import tomllib [as X]` (a submodule such as tomllib._parser included), `from tomllib import
    load, loads [as Y]`, `from tomllib import _parser [as Z]` and `from M import tomllib [as X]` (a module
    re-export). `from tomllib import *` is an unresolved form."""
    modules, parsers, unresolved = {"tomllib"}, set(), []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.asname or "tomllib" for alias in node.names
                           if _tomllib_module_name(alias.name))
        elif isinstance(node, ast.ImportFrom):
            from_tomllib = node.level == 0 and _tomllib_module_name(node.module or "")
            for alias in node.names:
                if from_tomllib and alias.name == "*":
                    unresolved.append((node, "from {} import *".format(node.module)))
                elif from_tomllib and alias.name in TOMLLIB_PARSERS:
                    parsers.add(alias.asname or alias.name)
                elif alias.name == "tomllib" or (from_tomllib and alias.name == "_parser"):
                    modules.add(alias.asname or alias.name)
    return modules, parsers, unresolved


def _tomllib_module_expr(expr, modules):
    """True when expr names the tomllib module: a bound module name, any `<expr>.tomllib` (a module
    re-export such as _opf_store.tomllib), or the _parser submodule of either."""
    if isinstance(expr, ast.Name):
        return expr.id in modules
    if isinstance(expr, ast.Attribute):
        return expr.attr == "tomllib" or (expr.attr == "_parser" and _tomllib_module_expr(expr.value, modules))
    return False


def _tomllib_namespace(expr):
    """True when expr is a module or name table: X.modules (sys.modules) or a globals(), vars() or locals()
    call."""
    return (isinstance(expr, ast.Attribute) and expr.attr == "modules") or (
        isinstance(expr, ast.Call) and isinstance(expr.func, ast.Name) and expr.func.id in ("globals", "vars", "locals"))


def _tomllib_by_name(node, parent):
    """True when the string node names the module to a loader or a module or name table: an argument of
    import_module, __import__ or find_spec, a key passed to a method of such a table (sys.modules.get), or
    a subscript of one (sys.modules["tomllib"], globals()["tomllib"])."""
    if isinstance(parent, ast.Subscript):
        return parent.slice is node and _tomllib_namespace(parent.value)
    if not isinstance(parent, ast.Call) or node not in parent.args:
        return False
    func = parent.func
    name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else None
    return name in ("import_module", "__import__", "find_spec") or (isinstance(func, ast.Attribute)
                                                                   and _tomllib_namespace(func.value))


def _tomllib_rebound(tree):
    """Every name the file binds anywhere: an assignment or deletion target, an import alias, a def,
    class, handler or match-capture name, or a function parameter. A handler clause spelling one of
    these is not readable as the builtin it names, so it never establishes coverage and never resolves
    a first catcher."""
    bound = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            bound.add(node.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(node.name)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            bound.add(node.name)
        elif isinstance(node, ast.alias):
            bound.add(node.asname or node.name.split(".")[0])
        elif isinstance(node, ast.arg):
            bound.add(node.arg)
        elif isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name:
            bound.add(node.name)
        elif isinstance(node, ast.MatchMapping) and node.rest:
            bound.add(node.rest)
    return frozenset(bound)


def _tomllib_catches(handler, kind, modules, rebound):
    """Whether handler would catch kind at runtime: True (a bare except, or a readable class that kind
    is a subclass of, alone or in a tuple literal), False (every class is readable and none catches),
    or None (a class the scan cannot read leaves it unresolved). Readable: a builtin exception name the
    file never rebinds, or tomllib.TOMLDecodeError spelled through a recognized tomllib module, which
    catches none of the three covered classes, so a narrower handler before the covering one is
    honoured, never refused."""
    if handler.type is None:
        return True
    unknown = False
    for name in handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]:
        named = None
        if isinstance(name, ast.Name) and name.id not in rebound:
            named = getattr(builtins, name.id, None)
        elif isinstance(name, ast.Attribute) and name.attr == "TOMLDecodeError" \
                and _tomllib_module_expr(name.value, modules):
            named = tomllib.TOMLDecodeError
        if not (isinstance(named, type) and issubclass(named, BaseException)):
            unknown = True
        elif issubclass(kind, named):
            return True
    return None if unknown else False


def _tomllib_reraises(handler):
    """Every bare `raise` and every raise of the handler's bound name anywhere in its body, a
    conditional one included."""
    return [node for stmt in handler.body for node in ast.walk(stmt)
            if isinstance(node, ast.Raise) and (node.exc is None or (
                isinstance(node.exc, ast.Name) and node.exc.id == handler.name))]


def _tomllib_covers(handler):
    """True when the scan can establish that handler disposes of what it caught: its body is not one
    pass statement alone (an effect this scan does not read as disposal) and holds no bare `raise` and
    no raise of its bound name anywhere, a conditional one included."""
    if len(handler.body) == 1 and isinstance(handler.body[0], ast.Pass):
        return False
    return not _tomllib_reraises(handler)


def _tomllib_guarded_passthrough(handler, rebound):
    """True when every re-raise in handler is exactly the single statement of an
    `if isinstance(<bound name>, ValueError):` with no else (isinstance and ValueError never rebound in
    the file), so only the ValueError family can leave the handler and every other caught class is
    disposed in its body: the shape a valueerror-passthrough row records."""
    if handler.name is None or "isinstance" in rebound or "ValueError" in rebound:
        return False
    if len(handler.body) == 1 and isinstance(handler.body[0], ast.Pass):
        return False
    guarded = set()
    for stmt in handler.body:
        for node in ast.walk(stmt):
            if (isinstance(node, ast.If) and not node.orelse and len(node.body) == 1
                    and isinstance(node.body[0], ast.Raise) and node.body[0].exc is None
                    and isinstance(node.test, ast.Call) and isinstance(node.test.func, ast.Name)
                    and node.test.func.id == "isinstance" and len(node.test.args) == 2
                    and not node.test.keywords
                    and isinstance(node.test.args[0], ast.Name) and node.test.args[0].id == handler.name
                    and isinstance(node.test.args[1], ast.Name) and node.test.args[1].id == "ValueError"):
                guarded.add(node.body[0])
    return all(node in guarded for node in _tomllib_reraises(handler))


def _tomllib_handled(tries, kind, modules, rebound, passthrough=False):
    """Whether the enclosing handlers dispose of kind: the FIRST handler that would catch it, scanning
    the innermost try's clauses in source order and then outward, decides, and it must cover
    (_tomllib_covers). A handler the scan cannot read is never skipped and never covers. With
    passthrough, a deciding handler whose only re-raises are guarded by `if isinstance(<bound name>,
    ValueError): raise` also counts: the valueerror-passthrough row shape."""
    for handlers in tries:
        for handler in handlers:
            catches = _tomllib_catches(handler, kind, modules, rebound)
            if catches is None:
                return False
            if catches:
                return _tomllib_covers(handler) or (passthrough
                                                    and _tomllib_guarded_passthrough(handler, rebound))
    return False


def _tomllib_site(node, parents, modules, rebound):
    """(function, handled, passthrough) for a node: the enclosing def, class, lambda and
    generator-expression names joined by dots (<module> at module level); whether the handlers of the
    try statements that hold the node in their body (never a handler, else or finally), inside the same
    function, lambda or generator expression, dispose of RecursionError, MemoryError and ValueError
    (_tomllib_handled, first-catcher order); and whether RecursionError and MemoryError are disposed
    when ValueError-guarded re-raises are honoured (the shape a valueerror-passthrough row records)."""
    names, tries, scoped, child, cur = [], [], False, node, parents.get(node)
    while cur is not None:
        if not scoped and isinstance(cur, (ast.Try, ast.TryStar)) and child in cur.body:
            tries.append(cur.handlers)
        scoped = scoped or isinstance(cur, TOMLLIB_SCOPES)
        if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.append(cur.name)
        elif isinstance(cur, ast.Lambda):
            names.append("<lambda>")
        elif isinstance(cur, ast.GeneratorExp):
            names.append("<genexpr>")
        child, cur = cur, parents.get(cur)
    return (".".join(reversed(names)) or "<module>",
            all(_tomllib_handled(tries, kind, modules, rebound) for kind in TOMLLIB_COVERED),
            all(_tomllib_handled(tries, kind, modules, rebound, passthrough=True)
                for kind in (RecursionError, MemoryError)))


def tomllib_sites(tree):
    """(sites, parents) for a parsed file. Each site is (line, function, form, text, handled,
    passthrough): form "call" is a direct call of tomllib.load or tomllib.loads under any spelling
    _tomllib_bindings and _tomllib_module_expr resolve; form "alias" is a form the scan cannot follow,
    never handled: a parser or the module used other than as a direct call (saved, passed, returned,
    read through getattr), any attribute of a recognized module other than load, loads and
    TOMLDecodeError (tomllib.__dict__, tomllib.__getattribute__, tomllib._parser.parse_key),
    `from tomllib import *`, or a string naming the module to a loader or a module or name table
    (_tomllib_by_name: importlib.import_module("tomllib"), sys.modules["tomllib"],
    globals()["tomllib"])."""
    modules, parsers, unresolved = _tomllib_bindings(tree)
    rebound = _tomllib_rebound(tree)
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    found = [(node, "alias", text) for node, text in unresolved]
    for node in ast.walk(tree):
        parent = parents.get(node)
        load = isinstance(getattr(node, "ctx", None), ast.Load)
        if (isinstance(node, ast.Attribute) and node.attr in TOMLLIB_PARSERS
                and _tomllib_module_expr(node.value, modules)) or (isinstance(node, ast.Name)
                                                                    and node.id in parsers):
            if isinstance(parent, ast.Call) and parent.func is node:
                found.append((parent, "call", ast.unparse(node)))
            elif load:
                found.append((node, "alias", ast.unparse(node) + " used other than as a direct call"))
        elif load and _tomllib_module_expr(node, modules):
            if isinstance(parent, ast.Attribute) and parent.value is node:
                if not (parent.attr in TOMLLIB_PARSERS or parent.attr == "TOMLDecodeError"
                        or _tomllib_module_expr(parent, modules)):
                    found.append((node, "alias", "the attribute {} of the module {} (only load, loads and "
                                  "TOMLDecodeError are followed)".format(parent.attr, ast.unparse(node))))
            else:
                found.append((node, "alias", "the module " + ast.unparse(node) + " used as a value"))
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) \
                and _tomllib_module_name(node.value) and _tomllib_by_name(node, parent):
            found.append((node, "alias", "the module named by the string {!r}".format(node.value)))
    sites = []
    for node, form, text in found:
        function, handled, passthrough = _tomllib_site(node, parents, modules, rebound)
        sites.append((node.lineno, function, form, text, handled and form == "call",
                      passthrough and form == "call"))
    return sorted(sites), parents


def _tomllib_caller_handles(tree, parents, caller, function):
    """True when the function named caller holds a HANDLED call of function, matched by its full
    qualified name as spelled at the call: a plain name call matches an undotted (module-level)
    function, and an attribute call matches only when its dotted spelling equals the qualified name.
    Residual (disclosed in the module docstring): a swap to another call with the same spelling inside
    the same caller is not detected."""
    modules, _parsers, _unresolved = _tomllib_bindings(tree)
    rebound = _tomllib_rebound(tree)
    for node in ast.walk(tree):
        func = node.func if isinstance(node, ast.Call) else None
        if func is None:
            continue
        spelled = func.id if isinstance(func, ast.Name) else (
            ast.unparse(func) if isinstance(func, ast.Attribute) else None)
        if spelled == function and _tomllib_site(node, parents, modules, rebound)[:2] == (caller, True):
            return True
    return False


def _tomllib_table(root):
    """The rows of TOMLLIB_TABLE_REL under root, keyed by (path, function, form). An absent table is an
    empty one, so every unhandled call then fails; a table that does not parse or breaks the schema is
    cannot-evaluate."""
    path = root / TOMLLIB_TABLE_REL
    if not os.path.lexists(path):
        return {}
    try:
        data = tomllib.loads(_read_text(path))
    except (ValueError, RecursionError, MemoryError) as exc:
        raise CannotEvaluate("{}: does not parse: {}".format(TOMLLIB_TABLE_REL, exc))
    if set(data) - {"site"} != {"format-version"} or type(data["format-version"]) is not int \
            or data["format-version"] != 1 or not isinstance(data.get("site", []), list):
        raise CannotEvaluate("{}: want format-version = 1 and [[site]] rows only".format(TOMLLIB_TABLE_REL))
    table = {}
    for index, row in enumerate(data.get("site", []), 1):
        where = "{}: site {}".format(TOMLLIB_TABLE_REL, index)
        kind = row.get("class") if isinstance(row, dict) else None
        if not isinstance(kind, str) or kind not in TOMLLIB_ROW_KEYS or set(row) != TOMLLIB_ROW_KEYS[kind]:
            raise CannotEvaluate("{}: want class CALLER with keys {} or class EXEMPT with keys {}".format(
                where, sorted(TOMLLIB_ROW_KEYS["CALLER"]), sorted(TOMLLIB_ROW_KEYS["EXEMPT"])))
        if type(row["count"]) is not int or row["count"] < 1 or not all(
                isinstance(row[key], str) and row[key] for key in row if key != "count"):
            raise CannotEvaluate("{}: count must be a positive integer and every other field a non-empty "
                                 "string".format(where))
        if row["form"] not in ("call", "alias"):
            raise CannotEvaluate("{}: form must be call or alias".format(where))
        if kind == "EXEMPT" and row["reason"] not in TOMLLIB_EXEMPT_REASONS:
            raise CannotEvaluate("{}: reason {!r} is not one of {}".format(
                where, row["reason"], ", ".join(sorted(TOMLLIB_EXEMPT_REASONS))))
        if kind == "CALLER" and row["form"] != "call":
            raise CannotEvaluate("{}: an alias form cannot be CALLER (the scan cannot follow it to a caller); "
                                 "record it EXEMPT or remove it".format(where))
        if kind == "EXEMPT" and row["reason"] == "valueerror-passthrough" and row["form"] != "call":
            raise CannotEvaluate("{}: an alias form cannot be valueerror-passthrough (the scan verifies the "
                                 "handler shape of a call); give another reason or remove it".format(where))
        key = (row["path"], row["function"], row["form"])
        if key in table:
            raise CannotEvaluate("{}: repeats the row for {} {} {}".format(where, *key))
        table[key] = row
    return table


def tomllib_findings(root):
    """The tomllib leg: each unhandled parse call has a CALLER or EXEMPT row, each unresolved form an
    EXEMPT row, each row the count the scan finds (and, for CALLER, a caller that still handles the
    call), and no row names a function in which the scan finds nothing unhandled."""
    table, findings, seen = _tomllib_table(root), [], set()
    for rel in _python_files(root, every=True):
        tree = _parse(root, rel)
        try:
            sites, parents = tomllib_sites(tree)
            groups = {}
            for line, function, form, text, handled, passthrough in sites:
                if not handled:
                    groups.setdefault((rel, function, form), []).append((line, text, passthrough))
            for key, found in sorted(groups.items()):
                seen.add(key)
                row, (line, text, _passthrough) = table.get(key), found[0]
                if row is None and key[2] == "call":
                    findings.append("{}:{}: {} in {} is not HANDLED (for each of RecursionError, MemoryError "
                                    "and ValueError, the first enclosing handler that would catch it must be "
                                    "readable and unable to re-raise it) and no row of {} classifies it "
                                    "CALLER or EXEMPT".format(rel, line, text, key[1], TOMLLIB_TABLE_REL))
                elif row is None:
                    findings.append("{}:{}: {} in {}: a tomllib form the scan cannot resolve, and no EXEMPT "
                                    "row of {} records it".format(rel, line, text, key[1], TOMLLIB_TABLE_REL))
                elif row["count"] != len(found):
                    findings.append("{}: the row for {} {} {} records {}, but the scan finds {} (lines {})".format(
                        TOMLLIB_TABLE_REL, rel, key[1], key[2], row["count"], len(found),
                        ", ".join(str(item[0]) for item in found)))
                elif row["class"] == "CALLER" and not _tomllib_caller_handles(tree, parents, row["caller"],
                                                                               key[1]):
                    findings.append("{}: the CALLER row for {} {} names {}, which holds no HANDLED call of "
                                    "it".format(TOMLLIB_TABLE_REL, rel, key[1], row["caller"]))
                elif row["class"] == "EXEMPT" and row["reason"] == "valueerror-passthrough" \
                        and not all(item[2] for item in found):
                    findings.append("{}: the valueerror-passthrough row for {} {}: a handler shape there "
                                    "could let RecursionError or MemoryError escape (only an `if "
                                    "isinstance(<name>, ValueError): raise` re-raise is honoured)".format(
                                        TOMLLIB_TABLE_REL, rel, key[1]))
        except (MemoryError, RecursionError) as exc:
            raise _too_complex(rel, exc)
    findings.extend("{}: the row for {} {} {} names a call that no longer exists or is now HANDLED; remove "
                    "it".format(TOMLLIB_TABLE_REL, *key) for key in sorted(set(table) - seen))
    return findings


# The fallback leg's refusal guidance, shared by its findings.
FALLBACK_GUIDANCE = ("refuse only through the canonical grammar: `import os`, then any "
                     "`NAME = \"<string literal>\"` bindings, then try: sys.stderr.write(<message>); "
                     "sys.stderr.flush() except BaseException: pass with nothing else in it (or one "
                     "`if tuple(sys.version_info[:2]) < (<int literals>):` whose if and else branches are "
                     "each that try), then os._exit(2) or os._exit(1), as GUARD_TEMPLATE does; the "
                     "message is a string literal, an f-string or a + concatenation whose parts are "
                     "string literals and names bound to string literals in the handler; sys is "
                     "bound by a module-level `import sys` before the try, and the handler's `as` "
                     "name is not os, sys, BaseException or tuple")
# The names the canonical grammar reads (the exit's os, the stream's sys, the diagnostic handler's
# BaseException and the version test's tuple): a message binding may not rebind one of them.
FALLBACK_SHAPE_NAMES = frozenset(("os", "sys", "BaseException", "tuple"))
# The fixed parts of the canonical grammar, compared as AST dumps: the diagnostic's two callees and the
# left side of the version split's one comparison (the guard's own).
FALLBACK_WRITE = _dump(ast.parse("sys.stderr.write", mode="eval").body)
FALLBACK_FLUSH = _dump(ast.parse("sys.stderr.flush", mode="eval").body)
FALLBACK_VERSION_LEFT = _dump(ast.parse("tuple(sys.version_info[:2])", mode="eval").body)


def _import_time_nodes(nodes):
    """Every AST node under nodes that runs at import time. A class body runs when the class
    statement runs, so the walk descends into it and into its decorators, bases and keywords. A
    function or lambda body runs only when called, so the walk yields a def but does not descend
    into its body (a method's included); it does walk a function's decorators and a function's or
    lambda's default values, which run when the def or lambda is evaluated. Annotations are not
    walked: Python 3.14 evaluates them lazily, on first access."""
    stack = list(nodes)
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            yield node
            stack.extend(getattr(node, "decorator_list", ()))
            stack.extend(node.args.defaults)
            stack.extend(default for default in node.args.kw_defaults if default is not None)
            continue
        yield node
        stack.extend(ast.iter_child_nodes(node))


def _expr_call(stmt):
    """The call when stmt is an expression statement holding a call, else None."""
    return stmt.value if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call) else None


def _guarded_module(try_node):
    """The module an import fallback guards: X when the try's body is exactly `import X` (one
    undotted name, an alias allowed), else None. A dotted import can fail on its parent package, whose
    import error names the parent, so no re-raise can name the module it guards."""
    body = try_node.body
    if len(body) == 1 and isinstance(body[0], ast.Import) and len(body[0].names) == 1 \
            and "." not in body[0].names[0].name:
        return body[0].names[0].name
    return None


def _is_import_os(stmt):
    """True when stmt is exactly `import os`, which binds the name the exit's os._exit reads."""
    return isinstance(stmt, ast.Import) and [(alias.name, alias.asname)
                                             for alias in stmt.names] == [("os", None)]


def _is_import_sys(stmt):
    """True when stmt is exactly `import sys`, which binds the name the diagnostic and the version
    test read."""
    return isinstance(stmt, ast.Import) and [(alias.name, alias.asname)
                                             for alias in stmt.names] == [("sys", None)]


def _sys_bound_before(tree, try_node):
    """True when a module-level statement of tree that ends before try_node begins is exactly
    `import sys` (_is_import_sys). Module-level statements run in order, and a try nested in one (a
    class body's) runs while that statement runs, so such an import has bound sys by then. An import
    in a function, a class, a branch or a try, an aliased one and one after the try do not count."""
    return any(_is_import_sys(stmt) and stmt.end_lineno < try_node.lineno for stmt in tree.body)


def _literal_binding(stmt):
    """The bound name when stmt is exactly NAME = "<string literal>" with NAME none of
    FALLBACK_SHAPE_NAMES, else None."""
    if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name) \
            and stmt.targets[0].id not in FALLBACK_SHAPE_NAMES and isinstance(stmt.value, ast.Constant) \
            and type(stmt.value.value) is str:
        return stmt.targets[0].id
    return None


def _is_message(node, bound):
    """True when node is the canonical message: a string literal, an f-string whose every replacement
    field is a plain {NAME} with no format spec, a NAME, or a + concatenation of those, each NAME one
    of bound (bound to a string literal earlier in the handler). Evaluating it only joins and formats
    str values: it calls no user code, so it cannot end the process or close a stream, and the write
    receives a str."""
    stack = [node]
    while stack:
        part = stack.pop()
        if isinstance(part, ast.BinOp) and isinstance(part.op, ast.Add):
            stack.extend((part.left, part.right))
        elif isinstance(part, ast.JoinedStr):
            stack.extend(part.values)
        elif isinstance(part, ast.FormattedValue):
            if part.format_spec is not None or not isinstance(part.value, ast.Name) \
                    or part.value.id not in bound:
                return False
        elif isinstance(part, ast.Name):
            if part.id not in bound:
                return False
        elif not (isinstance(part, ast.Constant) and type(part.value) is str):
            return False
    return True


def _stderr_args(stmt, callee):
    """The positional arguments when stmt is exactly a call of callee (FALLBACK_WRITE or
    FALLBACK_FLUSH) with no keyword argument, else None."""
    call = _expr_call(stmt)
    return call.args if call is not None and _dump(call.func) == callee and not call.keywords else None


def _is_wrapped_diagnostic(stmt, bound):
    """The canonical diagnostic, with nothing else in any part of the try statement:
        try:
            sys.stderr.write(<message>)
            sys.stderr.flush()
        except BaseException:
            pass
    with <message> a canonical message (_is_message) over the names in bound."""
    if not (isinstance(stmt, ast.Try) and len(stmt.body) == 2 and len(stmt.handlers) == 1
            and not stmt.orelse and not stmt.finalbody):
        return False
    write = _stderr_args(stmt.body[0], FALLBACK_WRITE)
    handler = stmt.handlers[0]
    return write is not None and len(write) == 1 and _is_message(write[0], bound) \
        and _stderr_args(stmt.body[1], FALLBACK_FLUSH) == [] \
        and isinstance(handler.type, ast.Name) and handler.type.id == "BaseException" \
        and handler.name is None and len(handler.body) == 1 and isinstance(handler.body[0], ast.Pass)


def _is_version_test(node):
    """True when node is exactly tuple(sys.version_info[:2]) < (<int literal>, ...), the guard's own
    comparison: it reads the version, compares two tuples of ints and calls nothing else."""
    return isinstance(node, ast.Compare) and _dump(node.left) == FALLBACK_VERSION_LEFT \
        and len(node.ops) == 1 and isinstance(node.ops[0], ast.Lt) \
        and isinstance(node.comparators[0], ast.Tuple) and all(
            isinstance(elt, ast.Constant) and type(elt.value) is int for elt in node.comparators[0].elts)


def _is_diagnostic(stmt, bound):
    """The canonical diagnostic, or the version split the shared tooling uses to choose its message:
    an if/else on the canonical version test (_is_version_test) whose two branches are each exactly
    the canonical diagnostic."""
    if isinstance(stmt, ast.If):
        return _is_version_test(stmt.test) and len(stmt.body) == 1 and len(stmt.orelse) == 1 \
            and _is_wrapped_diagnostic(stmt.body[0], bound) and _is_wrapped_diagnostic(stmt.orelse[0], bound)
    return _is_wrapped_diagnostic(stmt, bound)


def _is_os_exit(stmt):
    """True when stmt is exactly os._exit(<the refusal or nonblocking exit, a literal>)."""
    call = _expr_call(stmt)
    return call is not None and isinstance(call.func, ast.Attribute) and call.func.attr == "_exit" \
        and isinstance(call.func.value, ast.Name) and call.func.value.id == "os" and not call.keywords \
        and len(call.args) == 1 and isinstance(call.args[0], ast.Constant) \
        and type(call.args[0].value) is int and call.args[0].value in (REFUSAL_EXIT, NONBLOCKING_EXIT)


def _is_other_module_reraise(stmt, handler, guarded):
    """True when stmt is exactly `if <exc>.name != "<guarded>": raise` in an
    `except ModuleNotFoundError as <exc>` or `except ImportError as <exc>` handler, with guarded the
    module the fallback guards (_guarded_module): it hands an import error for any other module back
    unchanged, so only the guarded module's absence refuses. An unnamed handler and a None guarded
    (no single undotted import, or a try/except*, whose handler receives an ExceptionGroup with no
    .name) never match: the comparison would read None.name, or compare <exc>.name with None."""
    return guarded is not None and handler.name is not None \
        and isinstance(handler.type, ast.Name) and handler.type.id in ("ModuleNotFoundError", "ImportError") \
        and isinstance(stmt, ast.If) and _dump(stmt.test) == _dump(ast.parse(
            "{}.name != {!r}".format(handler.name, guarded), mode="eval").body) \
        and len(stmt.body) == 1 and isinstance(stmt.body[0], ast.Raise) and stmt.body[0].exc is None \
        and not stmt.orelse


def _canonical_departure(handler, guarded, sys_bound):
    """None when an import-fallback handler's body is exactly the canonical refusal: an optional
    re-raise naming the guarded module (_is_other_module_reraise), `import os`, any literal bindings
    (_literal_binding), the canonical diagnostic over the names they bind (_is_diagnostic), then
    os._exit. Otherwise the line of the first statement that departs from it (the handler's last line
    when a required statement is missing), or the handler's own line when its `as` name is one of
    FALLBACK_SHAPE_NAMES or sys_bound (_sys_bound_before) is false. Every statement is matched whole,
    so a re-raise naming another module, a missing `import os`, a message or version test outside the
    grammar, a re-raise in the diagnostic's handler, a flush before the write, a missing flush, a
    write to another stream, an extra statement anywhere or a missing os._exit departs."""
    if handler.name in FALLBACK_SHAPE_NAMES or not sys_bound:
        return handler.lineno
    body = list(handler.body)
    if body and _is_other_module_reraise(body[0], handler, guarded):
        body = body[1:]
    bound = set()
    index = 0
    for matches in (_is_import_os, None, lambda stmt: _is_diagnostic(stmt, bound), _is_os_exit):
        if matches is None:
            while index < len(body) and _literal_binding(body[index]) is not None:
                bound.add(_literal_binding(body[index]))
                index += 1
            continue
        if index == len(body):
            return handler.body[-1].end_lineno
        if not matches(body[index]):
            return body[index].lineno
        index += 1
    return body[index].lineno if index < len(body) else None


def is_canonical_fallback(handler, guarded, sys_bound):
    """True when an import-fallback handler's body is exactly the canonical refusal
    (_canonical_departure); guarded is the module its plain try imports (_guarded_module, None for a
    try/except*) and sys_bound whether a module-level `import sys` precedes the try
    (_sys_bound_before)."""
    return _canonical_departure(handler, guarded, sys_bound) is None


def _fallback_action(handler):
    """The first import-time construct in a non-canonical handler that can end the process, raise
    or write (a call, a raise, a try, a with, an await or yield, an assert, a decorated definition
    or a class with bases or keywords), as (line, description); None for a plain fallback such as
    `NAME = None`, `pass`, an alternative import or an undecorated def."""
    kinds = ((ast.Call, "a call"), (ast.Raise, "a raise"), ((ast.Try, ast.TryStar), "a try statement"),
             ((ast.With, ast.AsyncWith), "a with statement"),
             ((ast.Await, ast.Yield, ast.YieldFrom), "an await or yield"), (ast.Assert, "an assert"))
    found = []
    for node in _import_time_nodes(handler.body):
        for kind, description in kinds:
            if isinstance(node, kind):
                found.append((node.lineno, node.col_offset, description))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.decorator_list:
            found.append((node.lineno, node.col_offset, "a decorated definition"))
        if isinstance(node, ast.ClassDef) and (node.bases or node.keywords):
            found.append((node.lineno, node.col_offset, "a class with bases or keywords"))
    return min(found)[::2] if found else None


def _fallback_handler_findings(rel, handler, guarded, sys_bound):
    """A finding when one import-fallback handler is neither the canonical refusal
    (is_canonical_fallback) nor a plain fallback that cannot end the process (_fallback_action)."""
    departure = _canonical_departure(handler, guarded, sys_bound)
    action = None if departure is None else _fallback_action(handler)
    if action is None:
        return []
    return ["{}:{}: an import fallback departs here from the canonical write-flush-exit refusal, and "
            "it is no plain fallback (it holds {} at line {}); a retired or altered shape can lose "
            "the refusal exit or its diagnostic (`raise SystemExit` or sys.exit becomes exit 120 when "
            "the sys.stderr flush at interpreter exit fails, an unwrapped write raises when "
            "sys.stderr is None, a re-raise turns the refusal into exit 1, an unflushed or "
            "late-flushed write is lost to os._exit, and a call in the message or version test can "
            "end the process, close the stream or raise); {}".format(
                rel, departure, action[1], action[0], FALLBACK_GUIDANCE)]


def fallback_findings(root):
    """The fallback leg: every module-level import fallback in the tree is the canonical refusal or a
    plain fallback, never a retired or altered shape (see the module docstring)."""
    findings = []
    for rel in _python_files(root):
        tree = _parse(root, rel)
        try:
            for node in _import_time_nodes(tree.body):
                if isinstance(node, (ast.Try, ast.TryStar)) and any(
                        isinstance(item, (ast.Import, ast.ImportFrom)) for item in node.body):
                    guarded = _guarded_module(node) if isinstance(node, ast.Try) else None
                    sys_bound = _sys_bound_before(tree, node)
                    for handler in node.handlers:
                        findings.extend(_fallback_handler_findings(rel, handler, guarded, sys_bound))
        except (MemoryError, RecursionError) as exc:
            raise _too_complex(rel, exc)
    return findings


def documentation_findings(root, floor):
    """Each declaration file must state the floor phrase as many times as DECLARATION_COPIES says (once
    by default), and every statement of the floor in the form "Python <floor> or|and <word>" must be
    exactly the phrase."""
    phrase = "Python %d.%d or newer" % floor
    variant_re = re.compile(r"(?i)(?<![A-Za-z0-9_])c?python{0}+{1}(?![0-9]|\.[0-9]){0}+(?:or|and){0}+"
                            r"[A-Za-z]+".format(CLAIM_SEP, re.escape("%d.%d" % floor)))
    found = []
    for rel in DECLARATION_FILES:
        text = _read_text(root / rel)
        count, want = text.count(phrase), DECLARATION_COPIES.get(rel, 1)
        if not count:
            found.append("{}: does not state {!r}".format(rel, phrase))
        elif count != want:
            found.append("{}: states {!r} {} time(s), not the {} in DECLARATION_COPIES".format(
                rel, phrase, count, want))
        found.extend("{}:{}: states the floor as {!r}, not as {!r}".format(
            rel, text.count("\n", 0, match.start()) + 1, match.group(0), phrase)
            for match in variant_re.finditer(text) if match.group(0) != phrase)
    return found


def documentation_claim_findings(root, floor):
    """Each Python version below the floor that a declaration file names, with its line."""
    found = []
    for rel in DECLARATION_FILES:
        text = _read_text(root / rel)
        for match in OLDER_CLAIM_RE.finditer(text):
            parts = next(match.group(*pair) for pair in (("major", "minor"), ("only", "only"),
                                                         ("op", "op_minor"), ("bare", "bare_minor"))
                         if match.group(pair[0]) is not None)
            try:
                version = (int(parts[0]), 0 if match.group("only") is not None else int(parts[1]))
            except ValueError as exc:
                raise CannotEvaluate("{}: a version too long to read: {}".format(rel, exc))
            if version < floor:
                named = parts[0] if match.group("only") is not None else "{}.{}".format(*parts)
                found.append("{}:{}: names Python {}, below the floor {}.{}; a declaration may "
                             "not state an older version".format(
                                 rel, text.count("\n", 0, match.start()) + 1, named, *floor))
    return found


def evaluate(root):
    """Run every enabled leg over root; return (exit code, report lines)."""
    try:
        source = load_source(root)
        floor = source["floor"]
        findings = []
        findings.extend(source_findings(source))
        findings.extend(switch_findings(source))
        findings.extend(pin_findings(root, floor))
        findings.extend(guard_findings(root, source["surfaces"], floor, source["nonblocking"]))
        findings.extend(dynamic_findings(root, source["surfaces"], floor, source["nonblocking"]))
        findings.extend(fallback_findings(root))
        findings.extend(tomllib_findings(root))
        if source["completeness"]:
            findings.extend(completeness_findings(root, source["surfaces"]))
        if source["documentation"]:
            findings.extend(documentation_findings(root, floor))
            findings.extend(documentation_claim_findings(root, floor))
    except CannotEvaluate as exc:
        return 2, ["CANNOT EVALUATE: {}".format(exc)]
    if findings:
        return 1, ["FAIL: " + finding for finding in findings]
    return 0, ["PASS: python floor {}.{} ({}): source, pins, guard, dynamic, fallback and tomllib legs over {} "
               "guarded surface(s); completeness check {}, documentation and claims checks {}".format(
                   floor[0], floor[1], SOURCE_REL, len(source["surfaces"]),
                   "ON" if source["completeness"] else "OFF (completeness-check = false)",
                   "ON" if source["documentation"] else "OFF (documentation-check = false)")]



# ---------------------------------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------------------------------

FAILURES = []
EXECUTED = []
_EXECUTED_SET = set()
# Red-on-revert: each leg's call in evaluate(), removed in a scratch copy of this gate.
REVERT_CALLS = (
    ("source", "source_findings(source)"),
    ("switch", "switch_findings(source)"),
    ("pins", "pin_findings(root, floor)"),
    ("guard", "guard_findings(root, source[\"surfaces\"], floor, source[\"nonblocking\"])"),
    ("dynamic", "dynamic_findings(root, source[\"surfaces\"], floor, source[\"nonblocking\"])"),
    ("fallback", "fallback_findings(root)"),
    ("tomllib", "tomllib_findings(root)"),
    ("completeness", "completeness_findings(root, source[\"surfaces\"])"),
    ("documentation", "documentation_findings(root, floor)"),
    ("claims", "documentation_claim_findings(root, floor)"),
)
# The OPF adaptations, each reverted in a scratch copy by replacing its one anchor.
ADAPTATION_REVERTS = {
    "bind": ("    _bind(data, load_host_patch(root, floor))\n", ""),
    # The host policy made optional, and read from the parent repository's place instead of the OPF tree's.
    "host-optional": ("    _bind(data, load_host_patch(root, floor))\n", "    _bind(data, \"\")\n"),
    "host-parent": ("HOST_SOURCE_REL = \"tools/" "python-host.toml\"", "HOST_SOURCE_REL = \"../.aiqt/python-host.toml\""),
    "floor-parent": ("SOURCE_REL = \"tools/" "python-floor.toml\"", "SOURCE_REL = \"../.aiqt/core/python-floor.toml\""),
    # The earlier schema's key set: an extra key (pin-files) accepted.
    "extra-key": ("    if set(data) != set(POLICY_KEYS):\n", "    if not set(POLICY_KEYS) <= set(data):\n"),
    "host-minor": ("    if (int(match.group(1)), int(match.group(2))) != floor:\n", "    if False:\n"),
    "host-jobs-empty": ("    if not data[\"host-patch-jobs\"] or not all(", "    if not all("),
    "host-workflow-listed": ("    if data[\"host-patch-workflow\"] not in data[\"workflow-files\"]:\n",
                             "    if False:\n"),
    "workflow-dir": ("        if item.rsplit(\"/\", 1)[0] != data[\"workflow-dir\"]:\n", "        if False:\n"),
    "templates-apart": ("    if not adopter or not doctor or adopter & doctor " "or (adopter | doctor) & set(",
                        "    if False and set("),
    # The host-patch pin rule: never accepted (the earlier copy), accepted in any file, in any job.
    "host-pin-never": ("    return value == HOST_PATCH\n", "    return False\n"),
    "host-pin-any-file": ("    if rel != HOST_WORKFLOW_REL or _workflow_job(lines) not in HOST_JOBS \\\n",
                          "    if _workflow_job(lines) not in HOST_JOBS \\\n"),
    "host-pin-any-job": ("    if rel != HOST_WORKFLOW_REL or _workflow_job(lines) not in HOST_JOBS \\\n",
                         "    if rel != HOST_WORKFLOW_REL \\\n"),
    "host-pin-templates": ("    if rel != HOST_WORKFLOW_REL or _workflow_job(lines) not in HOST_JOBS \\\n",
                           "    if rel not in PIN_FILES and (rel != HOST_WORKFLOW_REL or _workflow_job(lines) "
                           "not in HOST_JOBS) \\\n"),
    "listed-workflows": ("if _yaml_name(name)) " "| set(WORKFLOW_FILES))]", "if _yaml_name(name)))]"),
    "ceiling-parent": ("GIT_CEILING_DIRECTORIES=str(Path(root)" ".parent.parent)",
                       "GIT_CEILING_DIRECTORIES=str(Path(root).parent)"),
    "ceiling-far": ("GIT_CEILING_DIRECTORIES=str(Path(root)" ".parent.parent)",
                    "GIT_CEILING_DIRECTORIES=str(Path(root).parent.parent.parent)"),
}
FIXTURE_ADOPTER = ("enforcement/ci/github-actions.yml",)
FIXTURE_DOCTOR = ("tools/check_opf_doctor.py",)
FIXTURE_PIN_FILES = FIXTURE_ADOPTER + FIXTURE_DOCTOR
FIXTURE_WORKFLOW = ".github/workflows/quality.yml"
FIXTURE_HOST_PATCH = "3.14.4"
FIXTURE_DECLARATIONS = ("site/adopt.md", "site/adopt/index.html", "spec/OPF-QUICKSTART.md")


def check(name, got, want):
    if name in _EXECUTED_SET:
        print("SELF-TEST HARNESS ERROR: duplicate check id {!r}".format(name), file=sys.stderr)
        sys.exit(2)
    _EXECUTED_SET.add(name)
    EXECUTED.append(name)
    if got != want:
        FAILURES.append("{}: got {!r}, want {!r}".format(name, got, want))


def _toml_list(items):
    return "[" + ", ".join('"{}"'.format(item) for item in items) + "]"


def _policy_text(floor="3.14", surfaces=(), completeness=False, documentation=True, adopter=FIXTURE_ADOPTER,
                 doctor=FIXTURE_DOCTOR, workflows=(FIXTURE_WORKFLOW,), host_workflow=FIXTURE_WORKFLOW,
                 declarations=FIXTURE_DECLARATIONS, copies="{}", host_jobs=("opf-host-patch",), extra=""):
    return ("format-version = 1\npython-floor = \"{}\"\nguarded-surfaces = {}\nnonblocking-surfaces = []\n"
            "completeness-check = {}\ndocumentation-check = {}\nworkflow-dir = \".github/workflows\"\n"
            "workflow-files = {}\nhost-patch-workflow = \"{}\"\nhost-patch-jobs = {}\n"
            "adopter-templates = {}\ndoctor-templates = {}\ndeclaration-files = {}\ndeclaration-copies = {}\n"
            "excluded-trees = [\"tools/_vendor/\"]\n"
            "tomllib-table = \"tools/tomllib_parse_sites.toml\"\n{}").format(
                floor, _toml_list(surfaces), "true" if completeness else "false",
                "true" if documentation else "false", _toml_list(workflows), host_workflow,
                _toml_list(host_jobs), _toml_list(adopter), _toml_list(doctor), _toml_list(declarations),
                copies, extra)


def _host_text(patch=FIXTURE_HOST_PATCH):
    return "format-version = 1\npython-host-patch = \"{}\"\n".format(patch)


def _workflow_text(pin="3.14", job="q", host_pin=None, host_job="opf-host-patch"):
    """A workflow whose job pins pin and, when host_pin is given, a second job host_job pinning it."""
    text = ("name: Q\non: push\njobs:\n  {}:\n    runs-on: ubuntu-latest\n    steps:\n"
            "      - uses: actions/setup-python@v5\n        with:\n          python-version: '{}'\n").format(job, pin)
    if host_pin is not None:
        text += ("  {}:\n    runs-on: ubuntu-latest\n    steps:\n      - uses: actions/setup-python@v5\n"
                 "        with:\n          python-version: '{}'\n").format(host_job, host_pin)
    return text


def _write(root, rel, text):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))


_FIXTURES = []


def _fixture(base, policy=None, workflow_pin="3.14", template_pin="3.14", doctor_pin="3.14", files=None,
             at=None, host=None):
    """An OPF-only tree under base (or at base/at, for a nested layout) holding both policies, one
    workflow, both templates, the declaration files and an empty parse-site table, then files over it.
    A files value of None removes that file."""
    _FIXTURES.append(None)
    root = base / "t{}".format(len(_FIXTURES)) / (at or "opf")
    _write(root, SOURCE_REL, _policy_text() if policy is None else policy)
    _write(root, HOST_SOURCE_REL, _host_text() if host is None else host)
    _write(root, FIXTURE_WORKFLOW, _workflow_text(workflow_pin))
    _write(root, FIXTURE_PIN_FILES[0], "steps:\n  - uses: actions/setup-python@v5\n    with:\n"
           "      python-version: '{}'\n".format(template_pin))
    _write(root, FIXTURE_PIN_FILES[1], "TEMPLATE = \"\"\"\n          python-version: '{}'\n\"\"\"\n".format(
        doctor_pin))
    for rel in FIXTURE_DECLARATIONS:
        _write(root, rel, "Requires Python 3.14 or newer.\n")
    _write(root, "tools/tomllib_parse_sites.toml", "format-version = 1\n")
    for rel, text in (files or {}).items():
        if text is None:
            (root / rel).unlink()
        else:
            _write(root, rel, text)
    return root


def _fixture_git_env():
    """The fixture git environment: no inherited GIT_ variable, the pinned PATH, and no system or
    global configuration, so a caller's repository or configuration never reaches a fixture."""
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(CHILD_ENV, GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
    return env


def _fixture_git_done(proc, what):
    if proc.returncode != 0:
        raise CannotEvaluate("fixture git {} exit {}: {}".format(
            what, proc.returncode, proc.stderr.decode("utf-8", "backslashreplace").strip()))


def _track(top):
    """Make top a repository and stage every file under it. Each launch is a literal argv carrying
    the automatic-maintenance pins, so neither starts a background maintenance child."""
    try:
        _fixture_git_done(subprocess.run(
            ["git", "-C", str(top), "-c", "gc.auto=0", "-c", "gc.autoDetach=false", "-c",
             "maintenance.auto=false", "-c", "core.fsmonitor=false", "init", "-q"],
            env=_fixture_git_env(), stdin=subprocess.DEVNULL, capture_output=True,
            timeout=CHILD_TIMEOUT), "init")
        _fixture_git_done(subprocess.run(
            ["git", "-C", str(top), "-c", "gc.auto=0", "-c", "gc.autoDetach=false", "-c",
             "maintenance.auto=false", "-c", "core.fsmonitor=false", "add", "-A"],
            env=_fixture_git_env(), stdin=subprocess.DEVNULL, capture_output=True,
            timeout=CHILD_TIMEOUT), "add")
    except (OSError, subprocess.SubprocessError) as exc:
        raise CannotEvaluate("fixture git failed: {}".format(exc))


def _entry(guard, after="open(\"RAN\", \"w\").close()\n"):
    return guard + "\n\ndef main():\n    " + after.replace("\n", "\n    ").rstrip(" ") \
        + "\nif __name__ == \"__main__\":\n    main()\n"


def _has(lines, marker):
    return any(marker in line for line in lines)


def _mutant(base, name, anchor, replacement):
    """A scratch copy of this gate with anchor replaced once, loaded through importlib."""
    text = _read_text(Path(__file__).resolve())
    if text.count(anchor) != 1:
        raise CannotEvaluate("revert anchor for {} occurs {} times".format(name, text.count(anchor)))
    path = base / "mutants" / (name.replace("-", "_") + ".py")
    _write(base / "mutants", path.name, text.replace(anchor, replacement))
    spec = importlib.util.spec_from_file_location("opf_python_floor_" + name.replace("-", "_"), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _leg_reverts(base, guard):
    """Each leg red on its fixture, naming its own finding, and green on the same fixture judged by a
    scratch copy of this gate with that leg's call removed from evaluate()."""
    listed = _policy_text(surfaces=["tools/demo.py"])
    cases = {
        "source": (dict(policy=_policy_text(floor="3.13"), host=_host_text("3.13.4"), workflow_pin="3.13",
                        template_pin="3.13", doctor_pin="3.13", files={rel: "Requires Python 3.13 or newer.\n"
                                                  for rel in FIXTURE_DECLARATIONS}), "decided floor"),
        "switch": (dict(policy=_policy_text(documentation=False)), "documentation-check is false"),
        "pins": (dict(workflow_pin="3.12"), "quality.yml:9: python-version '3.12' differs"),
        "guard": (dict(policy=listed, files={"tools/demo.py": _entry("import os\n" + guard)}),
                  "canonical floor guard"),
        # A module-level return parses, so the guard leg matches, but compiling the file fails, so the
        # patched child never reaches its refusal.
        "dynamic": (dict(policy=listed, files={"tools/demo.py": guard + "return\n"}), "tools/demo.py"),
        "fallback": (dict(files={"tools/helper.py": (
            "import sys\n\ntry:\n    import tomllib\nexcept ModuleNotFoundError:\n"
            "    sys.stderr.write(\"error: no tomllib\\n\")\n    raise SystemExit(2)\n")}),
            "departs here from the canonical write-flush-exit refusal"),
        "tomllib": (dict(files={"tools/site.py": "import tomllib\n\ndef read(text):\n    return tomllib.loads(text)\n"}),
                    "tools/site.py:4: tomllib.loads in read is not HANDLED"),
        "completeness": (dict(policy=_policy_text(completeness=True),
                              files={"tools/demo.py": _entry("import sys\n")}),
                         "tools/demo.py: a shipped entrypoint"),
        "documentation": (dict(files={FIXTURE_DECLARATIONS[-1]: "Requires Python.\n"}),
                          FIXTURE_DECLARATIONS[-1] + ": does not state"),
        "claims": (dict(files={FIXTURE_DECLARATIONS[-1]: "Requires Python 3.14 or newer; 3.12+ works.\n"}),
                   FIXTURE_DECLARATIONS[-1] + ":1: names Python 3.12"),
    }
    results = {}
    for leg, call in REVERT_CALLS:
        kwargs, marker = cases[leg]
        root = _fixture(base, **kwargs)
        if leg == "completeness":
            _track(root)
        code, lines = evaluate(root)
        mutant = _mutant(base, "leg-" + leg, "findings.extend(" + call + ")", "findings.extend(())")
        results[leg] = (code, any(line.startswith("FAIL: ") and marker in line for line in lines),
                        mutant.evaluate(root)[0])
    check("revert/source-leg", results["source"], (1, True, 0))
    check("revert/switch-leg", results["switch"], (1, True, 0))
    check("revert/pins-leg", results["pins"], (1, True, 0))
    check("revert/guard-leg", results["guard"], (1, True, 0))
    check("revert/dynamic-leg", results["dynamic"], (1, True, 0))
    check("revert/fallback-leg", results["fallback"], (1, True, 0))
    check("revert/tomllib-leg", results["tomllib"], (1, True, 0))
    check("revert/completeness-leg", results["completeness"], (1, True, 0))
    check("revert/documentation-leg", results["documentation"], (1, True, 0))
    check("revert/claims-leg", results["claims"], (1, True, 0))


def _adaptation_reverts(base):
    """Each OPF adaptation red on its fixture and judged otherwise by a scratch copy with it reverted."""
    def mutant(name):
        anchor, replacement = ADAPTATION_REVERTS[name]
        return _mutant(base, "adapt-" + name, anchor, replacement)

    # Binding: a declaration file the policy names, outside the fixture's default set, is read; without
    # the binding the legs keep their empty surfaces and see nothing.
    root = _fixture(base, policy=_policy_text(declarations=["docs/floor.md"]),
                    files={"docs/floor.md": "Requires Python.\n"})
    code, lines = evaluate(root)
    check("revert/policy-binding", (code, _has(lines, "docs/floor.md: does not state"),
                                    mutant("bind").evaluate(root)[0]), (1, True, 0))
    # Only the OPF policies are read. Each fixture is nested under a parent directory holding the parent
    # repository's policy files; the OPF tree's own policy is removed, so the run is cannot-evaluate, and a
    # copy that reads the parent's place instead passes.
    parent_files = {"../.aiqt/core/python-floor.toml": _policy_text(), "../.aiqt/python-host.toml": _host_text()}
    root = _fixture(base, files={**parent_files, HOST_SOURCE_REL: None})
    code, lines = evaluate(root)
    check("revert/host-policy-required", (code, _has(lines, HOST_SOURCE_REL + ": cannot stat"),
                                          mutant("host-optional").evaluate(root)[0]), (2, True, 0))
    check("revert/host-policy-never-parent", (code, mutant("host-parent").evaluate(root)[0]), (2, 0))
    root = _fixture(base, files={**parent_files, SOURCE_REL: None})
    code, lines = evaluate(root)
    check("revert/floor-policy-never-parent", (code, _has(lines, SOURCE_REL + ": cannot stat"),
                                               mutant("floor-parent").evaluate(root)[0]), (2, True, 0))
    # The host patch is the OPF host policy's, whatever a parent's says: the OPF policy's 3.14.5 passes
    # in the host-patch job while the parent's place says 3.14.4, and a copy reading the parent's fails.
    root = _fixture(base, host=_host_text("3.14.5"), files={**parent_files, FIXTURE_WORKFLOW: _workflow_text(
        host_pin="3.14.5")})
    check("revert/host-patch-from-opf-policy", (evaluate(root)[0], mutant("host-parent").evaluate(root)[0]),
          (0, 1))
    # The earlier schema's pin-files key is refused: alone (the template keys missing) and beside the
    # template keys, where a copy that accepts an extra key passes.
    old = _policy_text().replace("adopter-templates = [\"enforcement/ci/github-actions.yml\"]\n"
                                 "doctor-templates = [\"tools/check_opf_doctor.py\"]\n",
                                 "pin-files = " + _toml_list(FIXTURE_PIN_FILES) + "\n")
    code, lines = evaluate(_fixture(base, policy=old))
    check("source/pin-files-schema-cannot-evaluate", (code, _has(lines, "want exactly")), (2, True))
    root = _fixture(base, policy=_policy_text(extra="pin-files = " + _toml_list(FIXTURE_PIN_FILES) + "\n"))
    code, lines = evaluate(root)
    check("revert/pin-files-key-refused", (code, _has(lines, "'pin-files'"),
                                           mutant("extra-key").evaluate(root)[0]), (2, True, 0))
    # Host-patch pin: in a host-patch job of the host-patch workflow, the OPF host patch passes; a copy that
    # never accepts a host patch (the earlier copy) fails it.
    root = _fixture(base, files={FIXTURE_WORKFLOW: _workflow_text(host_pin=FIXTURE_HOST_PATCH)})
    check("revert/host-patch-job-pin-passes", (evaluate(root)[0], mutant("host-pin-never").evaluate(root)[0]),
          (0, 1))
    code, lines = evaluate(_fixture(base, files={FIXTURE_WORKFLOW: _workflow_text(host_pin="3.14.5")}))
    check("pins/host-job-other-patch-differs", (code, _has(lines, "python-version '3.14.5' differs")), (1, True))
    # The same pin in a job the policy does not name, or in another workflow file, is a finding.
    root = _fixture(base, files={FIXTURE_WORKFLOW: _workflow_text(host_pin=FIXTURE_HOST_PATCH, host_job="other")})
    code, lines = evaluate(root)
    check("revert/host-patch-other-job-differs", (code, _has(lines, "python-version '3.14.4' differs"),
                                                  mutant("host-pin-any-job").evaluate(root)[0]), (1, True, 0))
    root = _fixture(base, policy=_policy_text(workflows=(".github/workflows/other.yml", FIXTURE_WORKFLOW)),
                    files={".github/workflows/other.yml": _workflow_text(host_pin=FIXTURE_HOST_PATCH)})
    code, lines = evaluate(root)
    check("revert/host-patch-other-workflow-differs", (code, _has(lines, "other.yml:15: python-version '3.14.4'"),
                                                       mutant("host-pin-any-file").evaluate(root)[0]), (1, True, 0))
    # The templates pin the floor, never the authoring host patch: each template pinning the host patch is a
    # finding, and a copy that treats a template as a host-patch carrier passes both.
    for check_id, kwargs, rel in (("revert/adopter-template-host-patch-differs", {"template_pin": "3.14.4"},
                                   FIXTURE_ADOPTER[0]),
                                  ("revert/doctor-template-host-patch-differs", {"doctor_pin": "3.14.4"},
                                   FIXTURE_DOCTOR[0])):
        root = _fixture(base, files={FIXTURE_WORKFLOW: _workflow_text(host_pin=FIXTURE_HOST_PATCH)}, **kwargs)
        code, lines = evaluate(root)
        check(check_id, (code, _has(lines, rel + ":"), _has(lines, "'3.14.4' differs"),
                         mutant("host-pin-templates").evaluate(root)[0]), (1, True, True, 0))
    # The host patch is a patch release of the floor, at each end of the floor's minor version: just
    # outside below (3.13.9999), at the lowest (3.14.0) and highest (3.14.9999) patch, just outside above
    # (3.15.0); and a component of five digits is malformed.
    for check_id, patch, want in (("host/patch-3.13.9999-cannot-evaluate", "3.13.9999", 2),
                                  ("host/patch-3.14.0-passes", "3.14.0", 0),
                                  ("host/patch-3.14.9999-passes", "3.14.9999", 0),
                                  ("host/patch-3.15.0-cannot-evaluate", "3.15.0", 2),
                                  ("host/patch-five-digit-cannot-evaluate", "3.14.10000", 2)):
        check(check_id, evaluate(_fixture(base, host=_host_text(patch)))[0], want)
    root = _fixture(base, host=_host_text("3.15.0"))
    code, lines = evaluate(root)
    check("revert/host-patch-of-floor", (code, _has(lines, "not a patch release of the floor"),
                                         mutant("host-minor").evaluate(root)[0]), (2, True, 0))
    for check_id, host, marker in (("host/missing-key-cannot-evaluate", "format-version = 1\n", "want exactly"),
                                   ("host/extra-key-cannot-evaluate", _host_text() + "x = 1\n", "want exactly"),
                                   ("host/unparseable-cannot-evaluate", "format-version = \n", "does not parse"),
                                   ("host/deep-nesting-cannot-evaluate",
                                    "deep = " + "[" * 5000 + "]" * 5000 + "\n", "does not parse"),
                                   ("host/overlong-integer-cannot-evaluate", "long = 1" + "0" * 5000 + "\n",
                                    "does not parse")):
        code, lines = evaluate(_fixture(base, host=host))
        check(check_id, (code, _has(lines, HOST_SOURCE_REL + ": " + marker)), (2, True))
    # The policy's job and workflow binding: at least one host-patch job, a host-patch workflow that is a
    # listed workflow file, workflow files inside workflow-dir, a listed workflow file that exists, and
    # templates kept apart from each other and from the workflows.
    root = _fixture(base, policy=_policy_text(host_jobs=()))
    code, lines = evaluate(root)
    check("revert/host-patch-jobs-required", (code, _has(lines, "host-patch-jobs must name"),
                                              mutant("host-jobs-empty").evaluate(root)[0]), (2, True, 0))
    code, lines = evaluate(_fixture(base, policy=_policy_text(host_jobs=("9job",))))
    check("source/host-patch-job-id-cannot-evaluate", (code, _has(lines, "host-patch-jobs must name")), (2, True))
    root = _fixture(base, policy=_policy_text(host_workflow=".github/workflows/other.yml"))
    code, lines = evaluate(root)
    check("revert/host-patch-workflow-listed", (code, _has(lines, "is not a workflow-files entry"),
                                                mutant("host-workflow-listed").evaluate(root)[0]), (2, True, 0))
    root = _fixture(base, policy=_policy_text(workflows=(FIXTURE_WORKFLOW, "ci/other.yml")),
                    files={"ci/other.yml": _workflow_text()})
    code, lines = evaluate(root)
    check("revert/workflow-files-in-workflow-dir", (code, _has(lines, "is not a file in workflow-dir"),
                                                    mutant("workflow-dir").evaluate(root)[0]), (2, True, 0))
    root = _fixture(base, policy=_policy_text(workflows=(".github/workflows/gone.yml", FIXTURE_WORKFLOW)))
    code, lines = evaluate(root)
    check("revert/listed-workflow-read", (code, _has(lines, "gone.yml: cannot stat"),
                                          mutant("listed-workflows").evaluate(root)[0]), (2, True, 0))
    for check_id, kwargs in (("revert/template-both-kinds", {"doctor": FIXTURE_ADOPTER}),
                             ("revert/template-is-workflow", {"adopter": (FIXTURE_WORKFLOW,)}),
                             ("revert/template-list-empty", {"doctor": ()})):
        root = _fixture(base, policy=_policy_text(**kwargs))
        code, lines = evaluate(root)
        check(check_id, (code, _has(lines, "adopter-templates and doctor-templates"),
                         mutant("templates-apart").evaluate(root)[0]), (2, True, 0))
    # Completeness ceiling, at each end: the OPF root's own repository (inside), the parent's (at the
    # bound) and the grandparent's (just outside). An unlisted tracked entrypoint is the marker.
    entry = {"tools/demo.py": _entry("import sys\n")}
    policy = _policy_text(completeness=True)
    own = _fixture(base, policy=policy, files=entry)
    _track(own)
    code, lines = evaluate(own)
    check("completeness/own-repository-listed", (code, _has(lines, "tools/demo.py: a shipped entrypoint")),
          (1, True))
    nested = _fixture(base, policy=policy, files=entry)
    _track(nested.parent)
    code, lines = evaluate(nested)
    check("revert/completeness-nested-parent", (code, _has(lines, "tools/demo.py: a shipped entrypoint"),
                                                mutant("ceiling-parent").evaluate(nested)[0]), (1, True, 2))
    far = _fixture(base, policy=policy, files=entry, at="mid/opf")
    _track(far.parent.parent)
    code, lines = evaluate(far)
    check("revert/completeness-grandparent-refused", (code, _has(lines, "cannot list the tracked files"),
                                                      mutant("ceiling-far").evaluate(far)[0]), (2, True, 1))


def _self_test_cases(base):
    guard = guard_text("demo.py", (3, 14))
    check("fixture/clean-tree-passes", evaluate(_fixture(base))[0], 0)
    listed = _policy_text(surfaces=["tools/demo.py"])
    check("guard/canonical-guard-passes", evaluate(_fixture(base, policy=listed,
                                                            files={"tools/demo.py": _entry(guard)}))[0], 0)
    # TOML handling of the policy: each malformed form is cannot-evaluate, never a traceback.
    root = _fixture(base)
    (root / SOURCE_REL).unlink()
    code, lines = evaluate(root)
    check("source/missing-cannot-evaluate", (code, _has(lines, SOURCE_REL)), (2, True))
    code, lines = evaluate(_fixture(base, policy="format-version = \n"))
    check("source/unparseable-cannot-evaluate", (code, _has(lines, SOURCE_REL + ": does not parse")), (2, True))
    code, lines = evaluate(_fixture(base, policy="deep = " + "[" * 5000 + "]" * 5000 + "\n"))
    check("source/deep-nesting-cannot-evaluate", (code, _has(lines, SOURCE_REL + ": does not parse")), (2, True))
    code, lines = evaluate(_fixture(base, policy="long = 1" + "0" * 5000 + "\n"))
    check("source/overlong-integer-cannot-evaluate", (code, _has(lines, SOURCE_REL + ": does not parse")),
          (2, True))
    code, lines = evaluate(_fixture(base, policy=_policy_text(extra="extra = 1\n")))
    check("source/extra-key-cannot-evaluate", (code, _has(lines, "want exactly")), (2, True))
    code, lines = evaluate(_fixture(base, policy=_policy_text().replace("format-version = 1", "format-version = 2")))
    check("source/format-version-cannot-evaluate", (code, _has(lines, "format-version")), (2, True))
    code, lines = evaluate(_fixture(base, policy=_policy_text(adopter=["b.yml", "a.yml"])))
    check("source/unsorted-list-cannot-evaluate", (code, _has(lines, "adopter-templates must be a sorted")),
          (2, True))
    code, lines = evaluate(_fixture(base, policy=_policy_text(doctor=["../x.yml"])))
    check("source/escaping-path-cannot-evaluate", (code, _has(lines, "doctor-templates entry '../x.yml'")),
          (2, True))
    code, lines = evaluate(_fixture(base, policy=_policy_text().replace(
        "excluded-trees = [\"tools/_vendor/\"]", "excluded-trees = [\"tools/_vendor\"]")))
    check("source/excluded-tree-without-slash-cannot-evaluate", (code, _has(lines, "excluded-trees entry")),
          (2, True))
    code, lines = evaluate(_fixture(base, policy=_policy_text(copies="{\"site/adopt.md\" = 1}")))
    check("source/declaration-copies-below-two-cannot-evaluate", (code, _has(lines, "declaration-copies")),
          (2, True))
    # No parent file: a fixture holds only the OPF tree, so each pass above read no parent path; neither
    # live policy names a parent path as a value either.
    check("source/no-parent-path-in-live-policy",
          [rel for name in (SOURCE_REL, HOST_SOURCE_REL) for rel in _read_text(ROOT / name).split("\"")
           if rel.startswith((".aiqt", "../", "opf/"))],
          [])
    # Workflow pin, adopter template and doctor template: each pin must equal the floor.
    code, lines = evaluate(_fixture(base, template_pin="3.12"))
    check("pins/adopter-template-differs", (code, _has(lines, FIXTURE_PIN_FILES[0] + ":4: python-version '3.12'")),
          (1, True))
    code, lines = evaluate(_fixture(base, doctor_pin="3.13"))
    check("pins/doctor-template-differs", (code, _has(lines, FIXTURE_PIN_FILES[1] + ":2: python-version '3.13'")),
          (1, True))
    code, lines = evaluate(_fixture(base, files={FIXTURE_PIN_FILES[0]: "steps: []\n"}))
    check("pins/adopter-template-without-pin", (code, _has(lines, FIXTURE_PIN_FILES[0])), (1, True))
    code, lines = evaluate(_fixture(base, files={".github/workflows/x.yml": "a: &anchor b\n"}))
    check("pins/workflow-grammar-cannot-evaluate", (code, _has(lines, "x.yml:1")), (2, True))
    code, lines = evaluate(_fixture(base, files={"tools/x/action.yml": (
        "runs:\n  using: composite\n  steps:\n    - uses: actions/setup-python@v5\n      with:\n"
        "        python-version: '3.12'\n")}))
    check("pins/local-action-file-differs", (code, _has(lines, "tools/x/action.yml:6")), (1, True))
    # Fallback: the vendored tree the policy excludes is not judged; elsewhere the same file is a finding.
    bad = ("import sys\n\ntry:\n    import tomllib\nexcept ModuleNotFoundError:\n"
           "    sys.stderr.write(\"x\\n\")\n    raise SystemExit(2)\n")
    check("fallback/excluded-tree-not-judged", evaluate(_fixture(base, files={"tools/_vendor/v.py": bad}))[0], 0)
    # TOML handling of the parse-site table: a table that does not parse is cannot-evaluate; a recorded
    # EXEMPT row classifies an unhandled call.
    site = "import tomllib\n\ndef read(text):\n    return tomllib.loads(text)\n"
    code, lines = evaluate(_fixture(base, files={"tools/site.py": site,
                                                 "tools/tomllib_parse_sites.toml": "format-version = \n"}))
    check("tomllib/table-unparseable-cannot-evaluate", (code, _has(lines, "tools/tomllib_parse_sites.toml")),
          (2, True))
    row = ("format-version = 1\n[[site]]\npath = \"tools/site.py\"\nfunction = \"read\"\nform = \"call\"\n"
           "count = 1\nclass = \"EXEMPT\"\nreason = \"test-code\"\n")
    check("tomllib/exempt-row-classifies", evaluate(_fixture(base, files={
        "tools/site.py": site, "tools/tomllib_parse_sites.toml": row}))[0], 0)
    handled = ("import tomllib\n\ndef read(text):\n    try:\n        return tomllib.loads(text)\n"
               "    except (ValueError, RecursionError, MemoryError):\n        return None\n")
    check("tomllib/handled-site-passes", evaluate(_fixture(base, files={"tools/site.py": handled}))[0], 0)
    # Documentation: the copy count from declaration-copies.
    code, lines = evaluate(_fixture(base, policy=_policy_text(copies="{\"site/adopt.md\" = 2}")))
    check("documentation/declared-copies-counted", (code, _has(lines, "1 time(s), not the 2")), (1, True))
    # Completeness with no repository at either level is cannot-evaluate, never a pass.
    code, lines = evaluate(_fixture(base, policy=_policy_text(completeness=True)))
    check("completeness/no-repository-cannot-evaluate", (code, _has(lines, "cannot list the tracked files")),
          (2, True))
    _leg_reverts(base, guard)
    _adaptation_reverts(base)


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
    """The self-test in a private fixture home: HOME and XDG_CONFIG_HOME name an empty scratch
    directory and the system git configuration is off, so no caller configuration reaches a fixture
    repository; the caller's environment is restored on every exit."""
    _child_status_refusal_exit("check_python_floor.py")
    from unittest.mock import patch
    with tempfile.TemporaryDirectory(prefix="opf-python-floor-home-") as home:
        with patch.dict(os.environ, HOME=home, XDG_CONFIG_HOME=home, GIT_CONFIG_NOSYSTEM="1"):
            return _self_test_isolated()


def _self_test_isolated():
    try:
        with tempfile.TemporaryDirectory(prefix="opf-python-floor-selftest-") as raw:
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
    print("SELF-TEST PASS: {} unique checks executed (each leg and adaptation red on its fixture and judged "
          "otherwise with its change reverted); execution set reconciled against this file's check ids".format(
              len(EXECUTED)))
    return 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        code, lines = evaluate(ROOT)
        for line in lines:
            print(line, file=sys.stderr if code == 2 else sys.stdout)
        return code
    if argv != ["--self-test"]:
        print("usage: check_python_floor.py [--self-test]", file=sys.stderr)
        return 2
    return self_test()


# The child-status guard: an inline copy of the canonical guard in opf/tools/_child_status.py, which
# tools/check_child_status_coverage.py pins this copy to by AST. This gate reads the exit status of the
# git and interpreter children it starts, so under an ignored SIGCHLD a failing child would read as a
# pass. The guard runs as the first statement of self_test(), so the --self-test path refuses too, and
# at the entry line before main(); the entry line comes after the --self-test statement that opf.py
# requires first in the __main__ block. An import of this module runs nothing.
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
    sys.exit(_child_status_refusal_exit("check_python_floor.py") or main())
