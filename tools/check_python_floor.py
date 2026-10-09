#!/usr/bin/env python3
"""Python-floor gate (PYTHON-FLOOR): the single source .aiqt/core/python-floor.toml names the Python
floor of the pack's executable tooling, and every other statement of that floor agrees with it.

Legs, in order:
  source         the single source parses, carries exactly its declared keys, and names the decided
                 floor (FLOOR below; a change to either is a reviewed change to both).
  switch         the single source keeps documentation-check at its decided value (DOCUMENTATION_CHECK
                 below, true since the declarations unit switched it on), so turning the documentation
                 check off is a finding (exit 1) and a reviewed change to this gate, never a silent pass.
  pins           every `python-version:` interpreter pin in .github/workflows/*.yml, in PIN_FILES (the
                 shipped adopter CI template and its inline copy) and in every local action file (each
                 action.yml or action.yaml under the tree outside SKIPPED_DIR_NAMES, and the target of
                 each `uses: ./PATH`) equals the floor; each PIN_FILES entry must carry at least one
                 pin. A local `uses: ./PATH` with no action file inside the tree is cannot-evaluate.
                 The leg is a conservative line model, not a YAML parser.
                 Each scanned workflow or action file (not the inline Python copy) is held, line by
                 line, to an enumerated plain grammar; any line outside it is cannot-evaluate (exit 2),
                 naming file and line. The grammar: a blank line; a comment; a `---` that opens the
                 file; a plain key ([A-Za-z0-9_][A-Za-z0-9_.-]*, no quotes, no spaces), optional spaces
                 and a colon, then nothing, a plain scalar (no indicator first, no ': '), a one-line
                 quoted scalar (single-quoted with '' escapes, or double-quoted with no backslash), a
                 one-line flow sequence or mapping of such scalars (no nesting, plain keys, each once),
                 or a block scalar indicator (| or > with optional chomping and indentation
                 indicators), whose body is then skipped only by indentation (each following line that
                 is blank or more indented than the key, or than the `-` of a `- |` entry); and a `- `
                 list item holding one of the same. So an explicit key (`?`) and its value line (`:`),
                 a quoted key or one with a space in it, a double-quoted scalar with a backslash, a
                 quoted scalar that does not close on its line, a plain scalar continued on the next
                 line (a `- ` line more indented than the key or entry of a one-line value included),
                 an anchor, alias, tag or merge key, a nested `- -` sequence, a tab in the
                 indentation and a later document marker are each cannot-evaluate, and so is a key
                 repeated at the same indentation in one block mapping (two with: in one step),
                 compared without regard to case. A file under .github/workflows is scanned when its
                 name ends in .yml or .yaml in any case, and an action file is found by its name in
                 any case.
                 Every line of a scanned file that contains the text python-version (in any
                 case) must be one strict pin line (PIN_LINE_RE): optional space indentation, an
                 optional `- ` list marker, the bare key in lower case, optional spaces, a colon, one
                 or more spaces, exactly one plain, single-quoted or double-quoted scalar of
                 [A-Za-z0-9._+-] characters (matching quotes, nothing inside them but those
                 characters), then optional whitespace and an
                 optional `#` comment. Any other line naming the key (a value on the next line or a
                 block scalar, a quoted key, a flow mapping, an empty value, a concatenated or
                 escaped value, a comment that names the key) is cannot-evaluate (exit 2), never a
                 pass. No line is read as script text: a line inside a block scalar (a run: script,
                 for example) that names the key is judged as written, so it fails closed (exit 2,
                 or a finding when it is itself a strict pin of another version), a disclosed
                 over-rejection. The next non-blank line after a pin (blank lines skipped, comment
                 lines included) more indented than the pin's key, or led by a tab, is
                 cannot-evaluate: it would continue the value (a plain 3.14 continued by `|| 3.12`
                 is a range) or nest under it. In a .yml or .yaml file, a value that opens with a
                 quote or a flow indicator and does not close on its own line is cannot-evaluate. A
                 plain (unquoted) pin of a floor whose text ends in 0 is a finding, since YAML reads
                 a plain 3.20 as the number 3.2.
                 The key is found without regard to case, since the runner reads an input by its
                 upper-cased name: a strict pin whose key is in any other case is cannot-evaluate,
                 and so is any line, script text included, that names INPUT_PYTHON-VERSION
                 (in any case), the step environment variable that can also set the input.
                 In every scanned file, a line that still carries a carriage return once its one
                 trailing CR is removed, or carries U+0085, U+2028 or U+2029, is cannot-evaluate: a
                 YAML reader may read each as a line break, and the leg splits only on newline.
                 A step that uses actions/setup-python (the name in any case, any version) is a
                 finding unless a strict pin line sets its python-version input: a pin whose parent
                 line (the nearest earlier line whose first key, after any `- ` marker, starts left
                 of the pin's key, comment lines skipped) is the step's own with: key, so a pin at
                 the column of that with: key (a sibling, which leaves with: empty) is not one. The
                 step runs from its `-` marker line (a with: before the uses: line counts) to the
                 next non-blank line at the same or lesser indentation than that marker; a comment
                 line, at any column, never ends it. A uses: line (the key in any
                 case) that is not one plain or quoted literal, a line that names setup-python
                 anywhere else (a comment or a flow mapping included), and a setup-python uses: line
                 whose `-` marker the line model cannot find are cannot-evaluate; a python-version-file
                 input is already cannot-evaluate as a line that names the key. A literal uses: value
                 that names setup-python in any case, or whose path, split on / with empty parts
                 dropped, begins actions/setup-python, is cannot-evaluate unless it is exactly
                 actions/setup-python@REF or actions/setup-python/PATH@REF in some case (no empty, .
                 or .. path part, no leading or trailing slash, no .git suffix), so another spelling
                 the runner may resolve to the action is never passed over. A with: whose body is a
                 block sequence (its next line a `- ` entry) is cannot-evaluate: it holds no input.
                 A line of a workflow or action file holding a flow mapping with a key named uses
                 (after its first `{`, a key spelled uses in any case, plain or quoted) is
                 cannot-evaluate, naming the line: the leg does not read a step written as a flow
                 mapping, so the action it runs (a local ./PATH included) is never scanned or
                 refused as missing. The line is judged as written, script text included, a
                 disclosed over-rejection.
  guard          each guarded-surfaces entrypoint opens with the canonical refusal guard, AST-matched
                 against GUARD_TEMPLATE with the file's own basename, the floor and its refusal exit (1
                 for an entry also listed in nonblocking-surfaces, otherwise 2); only a module
                 docstring and `from __future__` imports may precede its `import sys`. A HOOK_SURFACES
                 entry (a hook whose events differ in the direction an error must fail) carries the
                 hook form instead, HOOK_GUARD_TEMPLATE: a FLOOR_FAIL_OPEN_MODES tuple literal of
                 sorted, unique mode names between `import sys` and the version test, and under the
                 floor a mode in that literal warns (a systemMessage on stdout, exit 0) while any other
                 argv (a PreToolUse handler, an unknown mode, no mode) refuses with the canonical
                 refusal and exit 2, fixed: exit 1 does not block a PreToolUse call, so a HOOK_SURFACES
                 entry listed in nonblocking-surfaces is cannot-evaluate at the source leg. The hook's
                 own self-test holds the literal equal to its fail-open handlers. Both forms
                 wrap each diagnostic write and its explicit flush (the stderr refusal, and the
                 hook form's stdout warning) in try/except BaseException and exit through
                 os._exit, which skips the interpreter-exit flush of the std streams, so the
                 refusal exit depends on no stream's availability, write or flush; the two
                 retired shapes (the bare write before `raise SystemExit`, and the wrapped write
                 that still ended in `raise SystemExit`, whose exit a failing sys.stderr flush at
                 interpreter exit replaced with CPython's own exit 120) do not match and are
                 findings.
  dynamic        each guarded-surfaces entrypoint, run in a child (-I -B plus each of no flag, -O and
                 -OO) from a fresh empty working directory with sys.version_info patched to each of two
                 versions below the floor, exits with its refusal exit (as for the guard leg) with
                 empty stdout and the exact refusal on stderr, and
                 leaves the working directory empty; the guard prefix alone, run at the floor's .0
                 release and at the real interpreter version, continues. A HOOK_SURFACES entry is also
                 run (no flag, the first patched version) with each FLOOR_FAIL_OPEN_MODES mode, which
                 must exit 0 with the exact warning on stdout and the refusal on stderr, and with
                 DENY_PROBE_MODE, a mode outside the literal, which must refuse with exit 2.
                 The refusal exit may not depend on the diagnostic write or its flush: each
                 entrypoint is also run (no flag, the first patched version) under each
                 STDERR_VECTORS condition (sys.stderr set to None, a sys.stderr whose write
                 raises, a sys.stderr whose write succeeds but whose flush raises, and file
                 descriptor 2 closed before the interpreter starts, which leaves sys.stderr
                 None), and must still exit with its refusal exit with empty stdout and stderr;
                 a HOOK_SURFACES entry must also still warn (exit 0, the exact warning on
                 stdout) under each condition with its first FLOOR_FAIL_OPEN_MODES mode.
  fallback       every .py file outside EXCLUDED_TREES (a directory in SKIPPED_DIR_NAMES is not
                 walked): a handler of a module-level try whose body holds an import (an import
                 fallback, such as the shared tooling's tomllib fallback) may not carry a retired
                 guard shape: a `raise SystemExit` or a sys.exit call (a sys.stderr whose flush fails
                 at CPython's interpreter-exit flush of the std streams replaces that exit with the
                 interpreter's own exit 120), a sys.stderr or sys.stdout write or flush outside
                 try/except BaseException (with the stream None the bare call raises out of the
                 handler and the refusal exit is lost), or a wrapped write without the same stream's
                 explicit flush (os._exit skips the interpreter-exit flush, so the unflushed
                 diagnostic is lost). Code inside a function, class or lambda body does not run at
                 import time and is not scanned.
  completeness   ON when the source sets completeness-check = true, as it does since the unit that
                 guarded tools/check_entry_guard.py, the last shipped entrypoint, switched it on (off,
                 an unlisted entrypoint is not a finding). The core-hook, preview-hook, adopter-tool
                 and OPF enforcement deny-hook units and tools/check_entry_guard.py are listed. On,
                 every shipped entrypoint, a .py file outside EXCLUDED_TREES with a module-level
                 `if __name__ == "__main__":`, must be listed in guarded-surfaces.
  documentation  ON (documentation-check = true, held by the switch leg): each DECLARATION_FILES entry
                 must contain "Python <floor> or newer" as many times as DECLARATION_COPIES says
                 (once by default), and each "Python <floor> or|and <word>" in it must be that phrase,
                 so changing or removing one of two copies is a finding.
  claims         ON with the documentation leg: no DECLARATION_FILES entry may name a Python version
                 below the floor (OLDER_CLAIM_RE below), so an older claim beside the floor statement
                 is a finding, naming file and line.

  check_python_floor.py              run every leg over this repository
  check_python_floor.py --self-test  fixture trees for every leg, plus red-on-revert: for each leg, a
                                     copy of this gate with that leg's check removed passes the fixture
                                     the intact gate refuses
  check_python_floor.py [--self-test] --execution-report ABS_PATH
                                     the self-test, also writing the executed check ids as JSON for
                                     tools/check_selftest_execution.py

Exit convention: 0 every enabled leg passes; 1 a finding; 2 usage or cannot-evaluate (a missing,
unreadable or malformed source; a listed or scanned file that is missing, not a regular file, not UTF-8
or does not parse; a child launch failure or timeout; unavailable temporary storage).

DISCLOSED RESIDUAL. The dynamic leg patches sys.version_info inside a child of THIS interpreter. It is a
proxy for a real older interpreter, faithful only for a guard that reads sys.version_info, which is why
the guard leg pins the one canonical form. Each file is compiled whole before its guard runs, so an
interpreter too old to parse a later statement stops with a SyntaxError instead of the refusal; the
dynamic leg sees a compile failure only on the interpreter running it. The completeness scan walks the
working tree, not the git index: an untracked stray entrypoint is counted, and a directory named in
SKIPPED_DIR_NAMES is not walked. The pins leg is a conservative line model, not a YAML parser: it reads
a workflow or action file only through the enumerated grammar, so a YAML form outside it (an escaped,
quoted or explicit key, an anchor, alias, tag or merge key, a multi-line quoted or flow value) stops
the leg (exit 2), never passes. A block scalar's body is skipped by the YAML indentation rule; a reader
that accepts what the YAML specification rejects (a body line less indented than that rule, for
example) is not modelled. Every line that names the key is still judged as written, script text
included, so script text that names it fails closed, a disclosed over-rejection. The leg checks only
the python-version input of a setup-python step: an update-environment: false input, or an if:
condition that skips the step, can leave another interpreter in use by later steps, and is not
judged. Further disclosed over-rejections, each failing closed (exit 2): a step whose `-` marker
stands alone on its line, a matrix or expression pin even when the matrix holds only the floor, and
a file that opens with a byte-order mark. The line model does not check that one mapping's
indentation is consistent, so a workflow the YAML specification rejects can pass; it runs no
interpreter. What remains: the inline Python copy is read only by the pin rule, not the grammar, so a
multi-line flow value around a pin there is not refused, and its lines are read as written, so a
Python string escape there (a backslash x73 for s, for example) that hides a uses: or setup-python
spelling is not decoded (the canonical-copy check in opf/tools/check_opf_doctor.py, which ties that
copy to the shipped github-actions.yml the grammar does read, covers the shipped file); an
INPUT_PYTHON-VERSION variable whose name is built at run time, not written on a line; the action
run as a fork or copy under a name that does not name setup-python, which is not seen as a
setup-python step; and a remote action, a remote reusable workflow
(owner/repo/.github/workflows/x.yml@ref) and a docker:// image, whose content is not in the tree
and is not read, so a setup-python step inside one is not seen. The leg scans only the files named
above. The documentation leg matches the exact phrase, not its meaning: a sentence that negates it
("do not require Python 3.14 or newer") passes, and the phrase reflowed across a line break is a
finding. A floor statement worded another way ("Python 3.14+", "at least 3.14") is caught only
through the copy count, so it passes when it is added beside the declared copies. The claims leg
reads only the forms OLDER_CLAIM_RE names: the word Python, CPython or Py (Py not after a dot, slash
or hyphen; each optionally followed by "version" or "versions" and by an operator such as >=, the
sign U+2265 or their HTML forms) or the interpreter name pythonM.N, followed by a version M.N, or
followed by at least one space and a major version alone ("Python 3", read as 3.0); an operator
followed by 3.N; and a bare 3.N followed by a plus sign, by "or" or "and" and then "newer", "later",
"above", "higher", "greater" or "up", or by "onward" or "onwards". It does not read a version spelled
in words, one separated from the word by markup other than whitespace and a no-break space, a bare
3.N with none of those after it ("runs on 3.12"), the name python3 with no minor version, a bare
version with a major other than 3, or the later end of a range ("Python 3.11 to 3.13" is a finding
for 3.11 only). It judges every older version it reads, whatever the sentence
says about it, so a sentence that names an older version only to say it is refused is also a finding,
a disclosed over-rejection: state the floor without naming older versions.

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
SOURCE_REL = ".aiqt/core/python-floor.toml"
CHECKS_MANIFEST = ROOT / "tools" / "selftest_checks.toml"
SUITE_ID = "python-floor-selftest"
# The decided floor. The source leg asserts the single source names it; every other leg reads the
# floor from the single source.
FLOOR = (3, 14)
# The decided value of the documentation-check switch. The switch leg asserts the single source keeps
# it, so turning the documentation check off is a finding and a reviewed change to this gate too.
DOCUMENTATION_CHECK = True
SOURCE_KEYS = {"format-version", "python-floor", "guarded-surfaces", "nonblocking-surfaces",
               "completeness-check", "documentation-check"}
WORKFLOWS_REL = ".github/workflows"
PIN_FILES = ("opf/enforcement/ci/github-actions.yml", "opf/tools/check_opf_doctor.py")
PIN_KEY = "python-version"
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
# Not shipped (repo-only CI) or byte-exact vendored third-party code under a provenance manifest.
EXCLUDED_TREES = (".github/", "opf/tools/_vendor/")
SKIPPED_DIR_NAMES = {".git", "__pycache__", ".venv", "venv", "node_modules"}
DECLARATION_FILES = ("README.md", "docs/development.md", "site/development.html", "site/install.html",
                     "opf/site/adopt.md", "opf/site/adopt.html", "opf/spec/OPF-QUICKSTART.md",
                     ".preview/README.md")
# How many times a declaration file states the floor phrase, where that is more than once: each copy is
# counted, so changing or removing one of them is a finding.
# .preview/README.md states it once and quotes it once in the hooks' refusal line.
DECLARATION_COPIES = {".preview/README.md": 2, "opf/site/adopt.html": 2}
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
# UserPromptSubmit or PostToolUse handler) warns on exit 0, never blocking, and every other argv (a
# PreToolUse handler, an unknown mode, no mode) fails closed with the canonical refusal and exit 2. That
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
HOOK_SURFACES = (".aiqt/core/hooks/scripts/aiqt_hooks.py",
                 "plugin/aiqt-guardrails-hooks/hooks/scripts/aiqt_hooks.py")
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


def load_source(root):
    """Parse and schema-check the single source; return its normalized fields."""
    try:
        data = tomllib.loads(_read_text(root / SOURCE_REL))
    except tomllib.TOMLDecodeError as exc:
        raise CannotEvaluate("{}: does not parse: {}".format(SOURCE_REL, exc))
    if set(data) != SOURCE_KEYS:
        raise CannotEvaluate("{}: keys {} (want exactly {})".format(
            SOURCE_REL, sorted(data), sorted(SOURCE_KEYS)))
    if type(data["format-version"]) is not int or data["format-version"] != 1:
        raise CannotEvaluate("{}: format-version must be the integer 1".format(SOURCE_REL))
    floor_text = data["python-floor"]
    match = FLOOR_RE.fullmatch(floor_text) if isinstance(floor_text, str) else None
    if match is None:
        raise CannotEvaluate("{}: python-floor must be a \"MAJOR.MINOR\" string, got {!r}".format(
            SOURCE_REL, floor_text))
    for key in ("completeness-check", "documentation-check"):
        if type(data[key]) is not bool:
            raise CannotEvaluate("{}: {} must be a boolean".format(SOURCE_REL, key))
    surfaces = data["guarded-surfaces"]
    if not isinstance(surfaces, list) or not all(isinstance(item, str) for item in surfaces):
        raise CannotEvaluate("{}: guarded-surfaces must be a list of strings".format(SOURCE_REL))
    nonblocking = data["nonblocking-surfaces"]
    if not isinstance(nonblocking, list) or not all(isinstance(item, str) for item in nonblocking):
        raise CannotEvaluate("{}: nonblocking-surfaces must be a list of strings".format(SOURCE_REL))
    if nonblocking != sorted(set(nonblocking)) or not set(nonblocking) <= set(surfaces):
        raise CannotEvaluate("{}: nonblocking-surfaces must be sorted, unique and each listed in "
                             "guarded-surfaces".format(SOURCE_REL))
    hooks = sorted(set(nonblocking) & set(HOOK_SURFACES))
    if hooks:
        raise CannotEvaluate("{}: nonblocking-surfaces lists the hook surface(s) {}, whose hook form "
                             "refuses a PreToolUse, unknown or missing mode with exit 2, fixed (exit 1 "
                             "does not block a PreToolUse call)".format(SOURCE_REL, ", ".join(hooks)))
    for item in surfaces:
        if not SURFACE_RE.fullmatch(item) or any(part in (".", "..") for part in item.split("/")):
            raise CannotEvaluate("{}: guarded-surfaces entry {!r} is not a repo-relative .py path"
                                 .format(SOURCE_REL, item))
    if surfaces != sorted(set(surfaces)):
        raise CannotEvaluate("{}: guarded-surfaces must be sorted and unique".format(SOURCE_REL))
    for item in surfaces:
        _read_text(root / item)
    return {"floor": (int(match.group(1)), int(match.group(2))), "surfaces": tuple(surfaces),
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


def pin_findings(root, floor):
    want = "%d.%d" % floor
    try:
        names = sorted(os.listdir(root / WORKFLOWS_REL))
    except OSError as exc:
        raise CannotEvaluate("{}: cannot list: {}".format(WORKFLOWS_REL, exc))
    targets = [(WORKFLOWS_REL + "/" + name, False) for name in names
               if _yaml_name(name)]
    targets += [(rel, True) for rel in PIN_FILES]
    if "action-files" in RULES:
        targets += [(rel, False) for rel in _action_files(root)]
    findings, seen = [], set()
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
            if value != want:
                findings.append("{}:{}: python-version {!r} differs from the floor {!r}".format(
                    rel, number, value, want))
            elif plain is not None and want.endswith("0"):
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


def _python_files(root):
    """Every repo-relative .py path outside EXCLUDED_TREES, directory by directory in sorted order
    (a directory in SKIPPED_DIR_NAMES is not walked)."""
    def _fail(exc):
        raise CannotEvaluate("cannot walk the tree: {}".format(exc))

    for dirpath, dirnames, filenames in os.walk(root, onerror=_fail):
        rel_dir = Path(dirpath).relative_to(root).as_posix()
        prefix = "" if rel_dir == "." else rel_dir + "/"
        dirnames[:] = sorted(name for name in dirnames if name not in SKIPPED_DIR_NAMES
                             and not _excluded(prefix + name + "/"))
        for name in sorted(filenames):
            rel = prefix + name
            if name.endswith(".py") and not _excluded(rel):
                yield rel


def shipped_entrypoints(root):
    return [rel for rel in _python_files(root) if _has_main_block(_parse(root, rel))]


def completeness_findings(root, surfaces):
    listed = set(surfaces)
    return ["{}: a shipped entrypoint missing from guarded-surfaces in {}".format(rel, SOURCE_REL)
            for rel in shipped_entrypoints(root) if rel not in listed]


# The fallback leg's refusal guidance, shared by its findings.
FALLBACK_GUIDANCE = ("wrap the diagnostic write and its explicit flush in try/except BaseException "
                     "and exit through os._exit, as GUARD_TEMPLATE does")


def _import_time_nodes(nodes):
    """Every AST node under nodes that runs at import time: the walk does not descend into a
    function, class or lambda (code there runs only when called; a decorator or default argument
    inside one is not judged, a disclosed residual of the fallback leg)."""
    stack = list(nodes)
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        yield node
        stack.extend(ast.iter_child_nodes(node))


def _stream_call(node, attr):
    """The stream name when node is a call of sys.stderr.<attr> or sys.stdout.<attr>, else None."""
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
            and node.func.attr == attr and isinstance(node.func.value, ast.Attribute) \
            and node.func.value.attr in ("stderr", "stdout") \
            and isinstance(node.func.value.value, ast.Name) and node.func.value.value.id == "sys":
        return node.func.value.attr
    return None


def _catches_base_exception(node):
    """True when node is a try statement one of whose handlers catches BaseException, by that name or
    bare."""
    return isinstance(node, ast.Try) and any(
        handler.type is None
        or (isinstance(handler.type, ast.Name) and handler.type.id == "BaseException")
        for handler in node.handlers)


def _fallback_handler_findings(rel, handler):
    """The retired guard shapes inside one import-fallback handler (fallback_findings)."""
    found = []
    nodes = list(_import_time_nodes(handler.body))
    wrapped = set()
    for node in nodes:
        if _catches_base_exception(node):
            inside = list(_import_time_nodes(node.body))
            wrapped.update(id(sub) for sub in inside)
            for stream in ("stderr", "stdout"):
                writes = sorted(sub.lineno for sub in inside if _stream_call(sub, "write") == stream)
                if writes and not any(_stream_call(sub, "flush") == stream for sub in inside):
                    found.append(
                        "{}:{}: an import fallback wraps a sys.{} write without its explicit "
                        "sys.{}.flush(), a retired guard shape (os._exit skips the interpreter-exit "
                        "flush of the std streams, so the unflushed diagnostic is lost); {}".format(
                            rel, writes[0], stream, stream, FALLBACK_GUIDANCE))
    for node in nodes:
        if isinstance(node, ast.Raise):
            exc = node.exc.func if isinstance(node.exc, ast.Call) else node.exc
            if isinstance(exc, ast.Name) and exc.id == "SystemExit":
                found.append(
                    "{}:{}: an import fallback exits through `raise SystemExit`, a retired guard "
                    "shape (a sys.stderr whose flush fails at CPython's interpreter-exit flush of "
                    "the std streams replaces that exit with the interpreter's own exit 120); {}"
                    .format(rel, node.lineno, FALLBACK_GUIDANCE))
            continue
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr == "exit" and isinstance(node.func.value, ast.Name) \
                and node.func.value.id == "sys":
            found.append(
                "{}:{}: an import fallback exits through sys.exit, a retired guard shape (sys.exit "
                "raises SystemExit, whose exit a failing sys.stderr flush at interpreter exit "
                "replaces with CPython's own exit 120); {}".format(rel, node.lineno, FALLBACK_GUIDANCE))
            continue
        if id(node) in wrapped:
            continue
        for attr in ("write", "flush"):
            stream = _stream_call(node, attr)
            if stream:
                found.append(
                    "{}:{}: an import fallback calls sys.{}.{} outside try/except BaseException, a "
                    "retired guard shape (with the stream None the bare call raises out of the "
                    "handler and the refusal exit is lost); {}".format(
                        rel, node.lineno, stream, attr, FALLBACK_GUIDANCE))
    return found


def fallback_findings(root):
    """The fallback leg: every module-level import fallback in the tree exits through the canonical
    shape, never a retired one (see the module docstring)."""
    findings = []
    for rel in _python_files(root):
        tree = _parse(root, rel)
        try:
            for node in _import_time_nodes(tree.body):
                if isinstance(node, ast.Try) and any(
                        isinstance(item, (ast.Import, ast.ImportFrom)) for item in node.body):
                    for handler in node.handlers:
                        findings.extend(_fallback_handler_findings(rel, handler))
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
        if source["completeness"]:
            findings.extend(completeness_findings(root, source["surfaces"]))
        if source["documentation"]:
            findings.extend(documentation_findings(root, floor))
            findings.extend(documentation_claim_findings(root, floor))
    except CannotEvaluate as exc:
        return 2, ["CANNOT EVALUATE: {}".format(exc)]
    if findings:
        return 1, ["FAIL: " + finding for finding in findings]
    return 0, ["PASS: python floor {}.{} ({}): source, pins, guard, dynamic and fallback legs over {} "
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
# Red-on-revert: each leg's call in evaluate(), removed in a copy of this gate.
REVERT_CALLS = (
    ("source", "source_findings(source)"),
    ("switch", "switch_findings(source)"),
    ("pins", "pin_findings(root, floor)"),
    ("guard", "guard_findings(root, source[\"surfaces\"], floor, source[\"nonblocking\"])"),
    ("dynamic", "dynamic_findings(root, source[\"surfaces\"], floor, source[\"nonblocking\"])"),
    ("fallback", "fallback_findings(root)"),
    ("completeness", "completeness_findings(root, source[\"surfaces\"])"),
    ("documentation", "documentation_findings(root, floor)"),
    ("claims", "documentation_claim_findings(root, floor)"),
)


def check(name, got, want):
    if name in _EXECUTED_SET:
        print("SELF-TEST HARNESS ERROR: duplicate check id {!r}".format(name), file=sys.stderr)
        sys.exit(2)
    _EXECUTED_SET.add(name)
    EXECUTED.append(name)
    if got != want:
        FAILURES.append("{}: got {!r}, want {!r}".format(name, got, want))


def _source_text(floor="3.14", surfaces=(), completeness=False, documentation=True, extra="",
                 nonblocking=()):
    return ("format-version = 1\npython-floor = {}\nguarded-surfaces = {}\nnonblocking-surfaces = {}\n"
            "completeness-check = {}\ndocumentation-check = {}\n{}".format(
                json.dumps(floor), json.dumps(list(surfaces)), json.dumps(list(nonblocking)),
                "true" if completeness else "false", "true" if documentation else "false", extra))


def _write(root, rel, text):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _declared(floor_text="3.14"):
    """Every declaration file, stating the floor as many times as DECLARATION_COPIES says."""
    return {rel: "Requires Python {} or newer.\n".format(floor_text) * DECLARATION_COPIES.get(rel, 1)
            for rel in DECLARATION_FILES}


def _fixture(base, source=None, workflow_pin="3.14", template_pin="3.14", files=None, pin_quote="'",
             declarations=True):
    """A clean tree; workflow_pin is the version on quality.yml line 6, or with pin_quote="" the whole
    text after that line's indentation. Every declaration file states the floor unless declarations
    is false; files are written after them."""
    root = Path(tempfile.mkdtemp(prefix="tree-", dir=base))
    for rel, text in (_declared() if declarations else {}).items():
        _write(root, rel, text)
    _write(root, SOURCE_REL, _source_text() if source is None else source)
    pin = "python-version: {0}{1}{0}".format(pin_quote, workflow_pin) if pin_quote else workflow_pin
    _write(root, WORKFLOWS_REL + "/quality.yml",
           "jobs:\n  q:\n    steps:\n      - uses: actions/setup-python@v5\n        with:\n"
           "          {}\n".format(pin))
    _write(root, PIN_FILES[0], "      - uses: actions/setup-python@v5\n        with:\n"
           "          python-version: '{}'\n".format(template_pin))
    _write(root, PIN_FILES[1], "TEMPLATE = \"\"\"\n          python-version: '{}'\n\"\"\"\n".format(
        template_pin))
    for rel, text in (files or {}).items():
        _write(root, rel, text)
    return root


def _entry(guard, before="", after="open(\"RAN\", \"w\").close()\n"):
    return before + guard + "\n" + after + "\nif __name__ == \"__main__\":\n    pass\n"


def _has(lines, marker):
    return any(marker in line for line in lines)


def _noflush_guard_text(name, floor, modes=None, code=REFUSAL_EXIT):
    """The retired wrapped-write guard shape, rebuilt from the canonical text by dropping each
    explicit flush and the `import os`, and ending with `raise SystemExit` instead of os._exit:
    the wrapped write held the refusal exit against a failing write, but `raise SystemExit`
    unwinds into CPython's interpreter-exit flush of the std streams, and a sys.stderr whose
    flush fails there replaces the code with the interpreter's own exit 120. Kept only so the
    self-test proves the gate refuses the shape and the flushraises vector catches its exit 120."""
    out = []
    for line in guard_text(name, floor, modes, code).split("\n"):
        stripped = line.strip(" ")
        if stripped in ("import os", "sys.stderr.flush()", "sys.stdout.flush()"):
            continue
        if stripped.startswith("os._exit("):
            line = line.replace("os._exit(", "raise SystemExit(", 1)
        out.append(line)
    return "\n".join(out)


def _old_guard_text(name, floor, modes=None, code=REFUSAL_EXIT):
    """The retired pre-fix guard shape, rebuilt from the wrapped-write text by unwrapping each
    try/except around a diagnostic write: there the write ran bare before `raise SystemExit`, so
    with stderr unavailable the write raised out of the guard and the process exited 1, a
    non-blocking error on PreToolUse (fail open). Kept only so the self-test proves the gate
    refuses the shape and the stderr vectors catch its fail-open."""
    out, dedent, in_handler = [], None, False
    for line in _noflush_guard_text(name, floor, modes, code).split("\n"):
        stripped = line.strip(" ")
        indent = len(line) - len(line.lstrip(" "))
        if stripped == "try:":
            dedent, in_handler = indent, False
            continue
        if dedent is not None and stripped == "except BaseException:" and indent == dedent:
            in_handler = True
            continue
        if in_handler and stripped == "pass" and indent == dedent + 4:
            dedent, in_handler = None, False
            continue
        if dedent is not None and not in_handler and indent > dedent:
            line = line[4:]
        out.append(line)
    return "\n".join(out)


def _self_test_cases(base):
    floor = FLOOR
    good = guard_text("demo.py", floor)
    demo = {"tools/demo.py": _entry(
        good, before="\"\"\"Fixture.\"\"\"\nfrom __future__ import annotations\n")}
    listed = _source_text(surfaces=["tools/demo.py"])

    check("fixture/clean-tree-passes", evaluate(_fixture(base))[0], 0)

    root = _fixture(base)
    (root / SOURCE_REL).unlink()
    check("source/missing-cannot-evaluate", evaluate(root)[0], 2)
    check("source/unparseable-cannot-evaluate",
          evaluate(_fixture(base, source="python-floor = \n"))[0], 2)
    check("source/unknown-key-cannot-evaluate",
          evaluate(_fixture(base, source=_source_text(extra="floor-note = \"x\"\n")))[0], 2)
    check("source/bool-format-version-cannot-evaluate", evaluate(_fixture(
        base, source=_source_text().replace("format-version = 1", "format-version = true")))[0], 2)
    check("source/malformed-floor-cannot-evaluate",
          evaluate(_fixture(base, source=_source_text(floor="3.14.0")))[0], 2)
    check("source/non-bool-switch-cannot-evaluate", evaluate(_fixture(
        base, source=_source_text().replace("completeness-check = false", "completeness-check = 0")))[0],
          2)
    check("source/escaping-surface-cannot-evaluate",
          evaluate(_fixture(base, source=_source_text(surfaces=["../demo.py"])))[0], 2)
    check("source/hidden-dir-surface-passes", evaluate(_fixture(
        base, source=_source_text(surfaces=[".preview/demo.py"]),
        files={".preview/demo.py": demo["tools/demo.py"]}))[0], 0)
    # One leading dot per segment, in the first segment and a later one: each tree holds the file, so a
    # pattern that allowed two dots would pass it (exit 0) instead of refusing the entry (exit 2).
    check("source/two-leading-dots-cannot-evaluate", [evaluate(_fixture(
        base, source=_source_text(surfaces=[rel]), files={rel: demo["tools/demo.py"]}))[0]
        for rel in ("..preview/demo.py", "tools/..hidden/demo.py")], [2, 2])
    check("source/nonblocking-not-guarded-cannot-evaluate", evaluate(_fixture(
        base, source=_source_text(nonblocking=["tools/demo.py"]), files=demo))[0], 2)
    check("source/nonblocking-not-list-cannot-evaluate", evaluate(_fixture(
        base, source=_source_text(surfaces=["tools/demo.py"]).replace(
            "nonblocking-surfaces = []", "nonblocking-surfaces = \"tools/demo.py\""), files=demo))[0], 2)
    check("source/unsorted-surfaces-cannot-evaluate", evaluate(_fixture(
        base, source=_source_text(surfaces=["tools/demo.py", "tools/a.py"]),
        files={"tools/a.py": _entry(guard_text("a.py", floor)), **demo}))[0], 2)
    check("source/missing-surface-cannot-evaluate",
          evaluate(_fixture(base, source=_source_text(surfaces=["tools/absent.py"])))[0], 2)
    code, lines = evaluate(_fixture(base, source=_source_text(floor="3.13"), workflow_pin="3.13",
                                    template_pin="3.13"))
    check("source/wrong-floor-finding", (code, _has(lines, "decided floor")), (1, True))

    code, lines = evaluate(_fixture(base, workflow_pin="3.12"))
    check("pins/workflow-mismatch-finding", (code, _has(lines, "quality.yml:6")), (1, True))
    root = _fixture(base)
    _write(root, PIN_FILES[0], "jobs: none\n")
    code, lines = evaluate(root)
    check("pins/template-without-pin-finding", (code, _has(lines, "carries no python-version pin")),
          (1, True))
    unrecognized = "quality.yml:6: unrecognized python-version spelling"
    for check_id, pin in (
            ("pins/block-scalar-value-cannot-evaluate", "python-version:\n            '3.12'"),
            ("pins/block-indicator-cannot-evaluate", "python-version: |\n            3.12"),
            ("pins/json-quoted-key-cannot-evaluate", '"python-version": "3.12"'),
            ("pins/concatenated-quotes-cannot-evaluate", "python-version: '3.14''3.12'"),
            ("pins/empty-value-cannot-evaluate", "python-version:   "),
            ("pins/empty-quoted-value-cannot-evaluate", "python-version: ''"),
            ("pins/flow-mapping-cannot-evaluate", "{python-version: 3.12}"),
            ("pins/mismatched-quotes-cannot-evaluate", "python-version: '3.14\"")):
        code, lines = evaluate(_fixture(base, workflow_pin=pin, pin_quote=""))
        check(check_id, (code, _has(lines, unrecognized)), (2, True))
    for check_id, pin, label in (
            ("pins/line-separator-cannot-evaluate", "python-version: 3.14\u2028.12", "U+2028"),
            ("pins/comment-line-separator-cannot-evaluate",
             "python-version: '3.14' # x\u2028python-version: '3.12'", "U+2028"),
            ("pins/bare-carriage-return-cannot-evaluate",
             "python-version: '3.14'\r          python-version: '3.12'", "a carriage return")):
        code, lines = evaluate(_fixture(base, workflow_pin=pin, pin_quote=""))
        check(check_id, (code, _has(lines, "quality.yml:6: carries " + label)), (2, True))
    # A line break character on a line that does not name the key, after a floor pin.
    for check_id, char in (
            ("pins/mid-line-carriage-return-cannot-evaluate", "\r"),
            ("pins/mid-line-next-line-cannot-evaluate", "\x85"),
            ("pins/mid-line-line-separator-cannot-evaluate", "\u2028"),
            ("pins/mid-line-paragraph-separator-cannot-evaluate", "\u2029")):
        code, lines = evaluate(_fixture(base, workflow_pin="python-version: '3.14'\n          cache: pip"
                                        + char + "x: 1", pin_quote=""))
        check(check_id, (code, _has(lines, "quality.yml:7: carries " + LINE_BREAKS[char])), (2, True))
    # A bare CR inside run: script text carries a whole step that YAML reads as structure.
    code, lines = evaluate(_fixture(base, files={WORKFLOWS_REL + "/attack.yml": (
        "name: attack\non: push\njobs:\n  q:\n    runs-on: ubuntu-latest\n    steps:\n      - run: |\n"
        "          echo hi\r      - uses: actions/setup-python@v5\r        with:\r"
        "          python-version: '3.12'\n")}))
    check("pins/bare-carriage-return-in-script-cannot-evaluate",
          (code, _has(lines, "attack.yml:8: carries a carriage return")), (2, True))
    root = _fixture(base)
    for rel in (WORKFLOWS_REL + "/quality.yml", PIN_FILES[0]):
        _write(root, rel, _read_text(root / rel).replace("\n", "\r\n"))
    check("pins/crlf-file-passes", evaluate(root)[0], 0)
    outside = "quality.yml:6: the text python-version appears outside a pin"
    lower = "spell the key in lower case"
    matrix = "quality.yml:6: this gate cannot evaluate a matrix or expression pin"
    for check_id, pin, marker in (
            ("pins/comment-mention-cannot-evaluate", "# python-version: '3.12'", outside),
            ("pins/version-file-input-cannot-evaluate", "python-version-file: .python-version", outside),
            ("pins/prose-mention-cannot-evaluate", "- name: Show python-version", outside),
            ("pins/title-case-key-cannot-evaluate", "Python-Version: '3.12'", lower),
            ("pins/upper-case-key-cannot-evaluate", "PYTHON-VERSION: '3.12'", lower),
            ("pins/upper-case-floor-key-cannot-evaluate", "- PYTHON-VERSION: '3.14'", lower),
            ("pins/dotless-i-key-cannot-evaluate", "python-vers\u0131on: '3.12'", lower),
            ("pins/long-s-key-cannot-evaluate", "python-ver\u017fion: '3.12'", lower),
            ("pins/case-variant-block-header-cannot-evaluate", "Python-Version: |\n            3.12",
             unrecognized),
            ("pins/case-variant-continuation-cannot-evaluate",
             "PYTHON-VERSION:\n            '3.12'", unrecognized),
            ("pins/matrix-expression-cannot-evaluate", "python-version: ${{ matrix.python-version }}",
             matrix),
            ("pins/matrix-row-cannot-evaluate", "python-version: ['3.14']", matrix),
            ("pins/input-env-cannot-evaluate", "INPUT_PYTHON-VERSION: '3.12'", INPUT_ENV),
            ("pins/input-env-any-case-cannot-evaluate", "- Input_Python-Version: '3.12'", INPUT_ENV)):
        code, lines = evaluate(_fixture(base, workflow_pin=pin, pin_quote=""))
        check(check_id, (code, _has(lines, marker)), (2, True))
    code, lines = evaluate(_fixture(base, workflow_pin=(
        "python-version: '3.14'\n      - run: |\n          echo \"INPUT_PYTHON-VERSION=3.12\" >> "
        "\"$GITHUB_ENV\""), pin_quote=""))
    check("pins/input-env-in-script-cannot-evaluate",
          (code, _has(lines, "quality.yml:8: names " + INPUT_ENV)), (2, True))
    for check_id, pin in (
            ("pins/trailing-comment-passes", "python-version: '3.14'  # the floor"),
            ("pins/double-quoted-passes", 'python-version: "3.14"'),
            ("pins/crlf-line-passes", "python-version: '3.14'\r"),
            ("pins/space-before-colon-passes", "python-version : '3.14'"),
            ("pins/sibling-key-passes", "python-version: 3.14\n          cache: pip")):
        check(check_id, evaluate(_fixture(base, workflow_pin=pin, pin_quote=""))[0], 0)
    # A list-marker pin passes where a block sequence is legitimate (a matrix include row); under a
    # with: it is cannot-evaluate (revert/with-list-pin).
    check("pins/list-marker-passes", evaluate(_fixture(base, files={WORKFLOWS_REL + "/matrix.yml": (
        "jobs:\n  q:\n    strategy:\n      matrix:\n        include:\n"
        "          - python-version: '3.14'\n")}))[0], 0)
    for check_id, pin, marker in (
            ("pins/plain-continuation-cannot-evaluate", "python-version: 3.14\n            || 3.12",
             "quality.yml:7: more indented than the python-version pin on line 6"),
            ("pins/range-continuation-cannot-evaluate", "python-version: 3.14\n            - 3.15",
             "quality.yml:7: more indented than the python-version pin on line 6"),
            ("pins/continuation-after-blank-cannot-evaluate",
             "python-version: 3.14\n\n            || 3.12",
             "quality.yml:8: more indented than the python-version pin on line 6"),
            ("pins/deeper-comment-cannot-evaluate", "python-version: '3.14'\n            # 3.12",
             "quality.yml:7: more indented than the python-version pin on line 6"),
            ("pins/multi-line-quoted-cannot-evaluate",
             "python-version: '3.14'\n          note: \"a\n  b: |\n            \"",
             "quality.yml:7: a quoted or flow value continues past its line"),
            ("pins/multi-line-flow-cannot-evaluate",
             "python-version: '3.14'\n          note: {a: 1,\n            b: 2}",
             "quality.yml:7: a quoted or flow value continues past its line"),
            ("pins/block-scalar-text-cannot-evaluate",
             "python-version: '3.14'\n      - run: |\n          echo python-version\n"
             "      - run: echo done", "quality.yml:8: unrecognized python-version spelling")):
        code, lines = evaluate(_fixture(base, workflow_pin=pin, pin_quote=""))
        check(check_id, (code, _has(lines, marker)), (2, True))
    code, lines = evaluate(_fixture(base, workflow_pin="python-version: 3.12", pin_quote=""))
    check("pins/wrong-single-line-finding",
          (code, _has(lines, "quality.yml:6: python-version '3.12' differs")), (1, True))
    code, lines = evaluate(_fixture(base, workflow_pin=(
        "python-version: '3.14'\n      - run: |\n          python-version: '3.12'\n"
        "      - run: echo done"), pin_quote=""))
    check("pins/block-scalar-pin-finding",
          (code, _has(lines, "quality.yml:8: python-version '3.12' differs")), (1, True))
    # An explicit-key entry whose value is a quoted scalar over several lines is outside the grammar.
    code, lines = evaluate(_fixture(base, files={WORKFLOWS_REL + "/attack.yml": (
        "name: attack\non: push\njobs:\n  q:\n    runs-on: ubuntu-latest\n    steps:\n"
        "      - ? name\n        : \"start\nfake: |\n          end\"\n"
        "        uses: actions/setup-other@v5\n        with:\n          python-version: '3.12'\n")}))
    check("pins/explicit-key-quoted-value-cannot-evaluate",
          (code, _has(lines, "attack.yml:7: an explicit key")), (2, True))
    def steps(body):
        return {WORKFLOWS_REL + "/steps.yml": "jobs:\n  q:\n    steps:\n" + body}

    no_input = "steps.yml:4: a step that uses actions/setup-python sets no python-version input"
    for check_id, body in (
            ("pins/setup-python-without-with-finding",
             "      - uses: actions/setup-python@v5\n      - run: echo done\n"),
            ("pins/setup-python-without-pin-finding",
             "      - uses: actions/setup-python@v5\n        with:\n          cache: pip\n"),
            ("pins/setup-python-case-variant-finding", "      - uses: Actions/Setup-Python@V5\n"),
            ("pins/setup-python-env-pin-finding",
             "      - uses: actions/setup-python@v5\n        env:\n          python-version: '3.14'\n"),
            ("pins/setup-python-next-step-pin-finding",
             "      - uses: actions/setup-python@v5\n      - uses: actions/checkout@v4\n        with:\n"
             "          python-version: '3.14'\n")):
        code, lines = evaluate(_fixture(base, files=steps(body)))
        check(check_id, (code, _has(lines, no_input)), (1, True))
    # A pin at the column of the with: key is its sibling: the with: is empty and sets no input.
    for check_id, body, number in (
            ("pins/setup-python-with-first-sibling-pin-finding",
             "      - with:\n        python-version: '3.14'\n        uses: actions/setup-python@v5\n", 6),
            ("pins/setup-python-empty-with-sibling-pin-finding",
             "      - uses: actions/setup-python@v5\n        with:\n        python-version: '3.14'\n", 4)):
        code, lines = evaluate(_fixture(base, files=steps(body)))
        check(check_id, (code, _has(lines, no_input.replace(":4:", ":{}:".format(number)))),
              (1, True))
    for check_id, body in (
            ("pins/setup-python-pinned-passes",
             "      - uses: actions/setup-python@v5\n        with:\n          python-version: '3.14'\n"),
            ("pins/setup-python-name-first-passes",
             "      - name: Python\n        # the floor\n        uses: \"actions/setup-python@v5\"  # v5\n"
             "        with:\n          cache: pip\n          python-version: '3.14'\n"
             "      - run: echo done\n"),
            ("pins/setup-python-with-first-passes",
             "      - with:\n          python-version: '3.14'\n        uses: actions/setup-python@v5\n")):
        check(check_id, evaluate(_fixture(base, files=steps(body)))[0], 0)
    literal = "steps.yml:4: a uses: line that is not one plain or quoted literal"
    for check_id, body, marker in (
            ("pins/setup-python-version-file-cannot-evaluate",
             "      - uses: actions/setup-python@v5\n        with:\n"
             "          python-version-file: .python-version\n",
             "steps.yml:6: the text python-version appears outside a pin"),
            ("pins/setup-python-escaped-uses-cannot-evaluate",
             "      - uses: \"actions/setup-\\x70ython@v5\"\n", literal),
            ("pins/setup-python-anchored-uses-cannot-evaluate",
             "      - uses: &a actions/setup-python@v5\n", literal),
            ("pins/setup-python-flow-step-cannot-evaluate",
             "      - {uses: actions/setup-python@v5}\n",
             "steps.yml:4: names setup-python outside a literal uses: line"),
            ("pins/setup-python-unfound-marker-cannot-evaluate", "      uses: actions/setup-python@v5\n",
             "steps.yml:4: a setup-python uses: line whose step `-` marker")):
        code, lines = evaluate(_fixture(base, files=steps(body)))
        check(check_id, (code, _has(lines, marker)), (2, True))
    head = "name: attack\non: push\njobs:\n  q:\n    runs-on: ubuntu-latest\n    steps:\n"
    outside_grammar = "a line outside the enumerated plain grammar"
    for check_id, body, marker in (
            ("grammar/anchor-cannot-evaluate", "      - name: &n x\n", "attack.yml:7: an anchor"),
            ("grammar/alias-cannot-evaluate", "      - *step\n", "attack.yml:7: an anchor"),
            ("grammar/tag-cannot-evaluate", "      - name: !!str x\n", "attack.yml:7: an anchor"),
            ("grammar/merge-key-cannot-evaluate", "      - <<: x\n", "attack.yml:7: a merge key"),
            ("grammar/mid-file-document-marker-cannot-evaluate", "---\n",
             "attack.yml:7: a document marker"),
            ("grammar/unclosed-single-quote-cannot-evaluate", "      - name: 'x\n        y'\n",
             "attack.yml:7: a quoted or flow value continues past its line"),
            ("grammar/plain-continuation-cannot-evaluate", "      - name: x\n          y\n",
             "attack.yml:8: " + outside_grammar),
            ("grammar/nested-sequence-cannot-evaluate", "      - - run: x\n",
             "attack.yml:7: a nested block sequence"),
            ("grammar/dash-continuation-cannot-evaluate",
             "      - uses: actions/checkout@v4\n          - x\n",
             "attack.yml:8: a plain scalar continued on the next line"),
            ("grammar/tab-indentation-cannot-evaluate", "      - run: x\n\t  shell: bash\n",
             "attack.yml:8: a tab in the indentation"),
            ("grammar/nested-flow-cannot-evaluate", "      - run: x\n        env: {A: [1]}\n",
             "attack.yml:8: " + outside_grammar),
            ("grammar/escaped-value-cannot-evaluate", "      - run: \"echo \\x41\"\n",
             "attack.yml:7: a double-quoted scalar with a backslash escape")):
        code, lines = evaluate(_fixture(base, files={WORKFLOWS_REL + "/attack.yml": head + body}))
        check(check_id, (code, _has(lines, marker)), (2, True))
    for check_id, text in (
            ("grammar/block-scalar-body-skipped-passes", head + "      - run: |\n          ? not a key\n"
             "          : \"not closed\n        shell: bash\n"),
            ("grammar/flow-and-quoted-values-pass", head + "      - run: echo \"a\" # c\n"
             "        env: {A: '1', B: \"x\"}\n        with:\n          args: [a, 'b c']\n"
             "          note: 'it''s'\n"),
            ("grammar/leading-document-marker-passes", "---\n" + head + "      - run: x\n")):
        check(check_id, evaluate(_fixture(base, files={WORKFLOWS_REL + "/attack.yml": text}))[0], 0)
    # A step written as a flow mapping with a uses key, whatever its target (a skipped directory, a
    # repository checked out at run time, a missing path), in any case or quoting, is cannot-evaluate.
    attack = WORKFLOWS_REL + "/attack.yml"
    evil = ("runs:\n  using: composite\n  steps:\n    - uses: actions/setup-python@v5\n"
            "      with:\n        python-version: '3.12'\n")
    checkout = ("      - uses: actions/checkout@v4\n        with:\n"
                "          repository: someorg/py312-action\n          path: gen\n")
    flow = "attack.yml:7: a flow mapping with the key "
    gen = "attack.yml:11: a flow mapping with the key uses"
    for check_id, files, marker in (
            ("pins/flow-uses-node-modules-cannot-evaluate", {
                attack: head + "      - {uses: ./node_modules/evil}\n",
                "node_modules/evil/action.yml": evil}, flow + "uses"),
            ("pins/flow-uses-dot-venv-cannot-evaluate", {
                attack: head + "      - {uses: ./.venv/evil}\n", ".venv/evil/action.yml": evil},
             flow + "uses"),
            ("pins/flow-uses-venv-quoted-cannot-evaluate", {
                attack: head + "      - {uses: './venv/evil'}\n", "venv/evil/action.yml": evil},
             flow + "uses"),
            ("pins/flow-uses-pycache-cannot-evaluate", {
                attack: head + "      - {uses: ./__pycache__/evil}\n",
                "__pycache__/evil/action.yml": evil}, flow + "uses"),
            ("pins/flow-uses-checked-out-cannot-evaluate",
             {attack: head + checkout + "      - {uses: ./gen}\n"}, gen),
            ("pins/flow-uses-checked-out-double-quoted-cannot-evaluate",
             {attack: head + checkout + "      - {uses: \"./gen\"}\n"}, gen),
            ("pins/flow-uses-checked-out-named-cannot-evaluate",
             {attack: head + checkout + "      - {name: py, uses: ./gen}\n"}, gen),
            ("pins/flow-uses-absent-cannot-evaluate",
             {attack: head + "      - {uses: ./absent}\n"}, flow + "uses"),
            ("pins/flow-uses-upper-case-key-cannot-evaluate",
             {attack: head + "      - {USES: ./absent}\n"}, flow + "USES"),
            ("pins/flow-uses-quoted-key-cannot-evaluate",
             {attack: head + "      - {'Uses': ./absent}\n"}, flow + "Uses"),
            ("pins/flow-uses-in-flow-sequence-cannot-evaluate",
             {attack: head.replace("steps:\n", "steps: [{uses: ./absent}]\n")},
             "attack.yml:6: a flow mapping with the key uses"),
            ("pins/flow-uses-action-file-cannot-evaluate", {
                "tools/a/action.yml": "runs:\n  using: composite\n  steps:\n"
                "    - {uses: ./node_modules/e}\n",
                "node_modules/e/action.yml": evil.replace("python-version: '3.12'", "cache: pip")},
             "tools/a/action.yml:4: a flow mapping with the key uses")):
        code, lines = evaluate(_fixture(base, files=files))
        check(check_id, (code, _has(lines, marker)), (2, True))
    code, lines = evaluate(_fixture(base, files={"tools/x/action.yml": (
        "runs:\n  using: composite\n  steps:\n    - uses: actions/setup-python@v5\n")}))
    check("pins/unreferenced-action-file-finding",
          (code, _has(lines, "tools/x/action.yml:4: a step that uses actions/setup-python")), (1, True))
    root = _fixture(base, workflow_pin="python-version: 3.10", template_pin="3.10", pin_quote="")
    check("pins/unquoted-trailing-zero-finding",
          [line for line in pin_findings(root, (3, 10)) if "unquoted" in line],
          ["{}/quality.yml:6: python-version 3.10 is unquoted; YAML reads it as a number, so quote it "
           "as '3.10'".format(WORKFLOWS_REL)])

    check("guard/canonical-passes", evaluate(_fixture(base, source=listed, files=demo)), (0, [
        "PASS: python floor 3.14 ({}): source, pins, guard, dynamic and fallback legs over 1 guarded "
        "surface(s); completeness check OFF (completeness-check = false), documentation and claims "
        "checks ON".format(SOURCE_REL)]))
    guard_marker = "canonical floor guard"
    for check_id, text in (
            ("guard/absent-finding", _entry("import sys\n")),
            ("guard/wrong-floor-literal-finding",
             _entry(guard_text("demo.py", (floor[0], floor[1] - 1)))),
            ("guard/wrong-basename-finding", _entry(guard_text("other.py", floor))),
            ("guard/late-placement-finding", _entry(good, before="import os\n")),
            ("guard/assert-form-finding",
             _entry("import sys\n\nassert tuple(sys.version_info[:2]) >= (%d, %d)\n" % floor))):
        code, lines = evaluate(_fixture(base, source=listed, files={"tools/demo.py": text}))
        check(check_id, (code, _has(lines, guard_marker)), (1, True))
    # A nonblocking-surfaces entry refuses with exit 1 (guard and dynamic legs); exit 2 there, or exit 1
    # on an entry not listed, is a guard finding.
    nonblocking = _source_text(surfaces=["tools/demo.py"], nonblocking=["tools/demo.py"])
    good_nonblocking = _entry(guard_text("demo.py", floor, code=NONBLOCKING_EXIT))
    check("guard/nonblocking-exit-1-passes",
          evaluate(_fixture(base, source=nonblocking, files={"tools/demo.py": good_nonblocking}))[0], 0)
    for check_id, source, text in (
            ("guard/nonblocking-exit-2-finding", nonblocking, _entry(good)),
            ("guard/unlisted-exit-1-finding", listed, good_nonblocking)):
        code, lines = evaluate(_fixture(base, source=source, files={"tools/demo.py": text}))
        check(check_id, (code, _has(lines, guard_marker)), (1, True))
    # A parser or traversal overflow is INJECTED (ast.parse raising, ast.dump raising on a marked node)
    # rather than provoked by a deeply nested body: the depth at which CPython's parser, its AST
    # construction or ast.dump overflows, and the class it raises, are interpreter implementation limits
    # that move between patch releases, so a real nesting body can parse cleanly (or raise another class)
    # on a newer interpreter and the case would flip. Injected, each case is identical on every
    # interpreter and turns red if its MemoryError/RecursionError mapping is removed.
    real_parse, real_dump = ast.parse, ast.dump
    deep_marker = "injected_deep_tree_marker"

    def parse_overflow(exc_class):
        def parse(source, *args, **kwargs):
            if kwargs.get("filename") == "tools/demo.py":
                raise exc_class("injected parser overflow")
            return real_parse(source, *args, **kwargs)
        return parse

    def dump_overflow(exc_class):
        def dump(node, *args, **kwargs):
            text = real_dump(node, *args, **kwargs)
            if deep_marker in text:
                raise exc_class("injected ast.dump overflow")
            return text
        return dump

    shallow = {"tools/demo.py": "VALUE = 1\n"}
    got = []
    for exc_class in (MemoryError, RecursionError):
        ast.parse = parse_overflow(exc_class)
        try:
            code, lines = evaluate(_fixture(base, source=listed, files=shallow))
            got.append((code, _has(lines, "tools/demo.py: too complex to parse: " + exc_class.__name__)))
        except (MemoryError, RecursionError) as exc:
            got.append("raised " + type(exc).__name__)
        finally:
            ast.parse = real_parse
    check("guard/parser-overflow-cannot-evaluate", got, [(2, True), (2, True)])
    deep = {"tools/demo.py": deep_marker + " = 1\n"}
    got = []
    for exc_class in (MemoryError, RecursionError):
        ast.dump = dump_overflow(exc_class)
        try:
            code, lines = evaluate(_fixture(base, source=listed, files=deep))
            got.append((code, _has(lines, "tools/demo.py: too complex to evaluate: " + exc_class.__name__)))
        except (MemoryError, RecursionError) as exc:
            got.append("raised " + type(exc).__name__)
        finally:
            ast.dump = real_dump
    check("guard/deep-tree-cannot-evaluate", got, [(2, True), (2, True)])

    path = _fixture(base, files=demo) / "tools" / "demo.py"
    version = below_floor(floor)[0]
    check("dynamic/refusal-exact-under-OO", refusal_observed(path, version, ("-OO",)),
          (2, "", expected_refusal("demo.py", floor, version, sys.executable), []))
    check("dynamic/below-floor-vectors", below_floor((3, 14)), ((3, 13, 9), (3, 12, 0)))
    path = _fixture(base, files={"tools/demo.py": good_nonblocking}) / "tools" / "demo.py"
    check("dynamic/nonblocking-refusal-exact", refusal_observed(path, version, ()),
          (1, "", expected_refusal("demo.py", floor, version, sys.executable), []))
    # The refusal exit may not depend on the diagnostic write or its flush: with sys.stderr None
    # (as Python leaves it when file descriptor 2 is closed at interpreter startup), with a
    # sys.stderr whose write raises, with one whose write succeeds but whose flush raises, and
    # with file descriptor 2 closed in the child, the guard still exits with its refusal exit
    # and nothing on stdout or stderr, and a nonblocking-surfaces entry still exits 1.
    vector_path = _fixture(base, files=demo) / "tools" / "demo.py"
    check("dynamic/stderr-none-refusal-exact",
          stderr_vector_observed(vector_path, version, "none"), (2, "", "", []))
    check("dynamic/stderr-raises-refusal-exact",
          stderr_vector_observed(vector_path, version, "raises"), (2, "", "", []))
    check("dynamic/stderr-flush-raises-refusal-exact",
          stderr_vector_observed(vector_path, version, "flushraises"), (2, "", "", []))
    check("dynamic/closed-fd2-refusal-exact",
          stderr_vector_observed(vector_path, version, "closed"), (2, "", "", []))
    vector_path = _fixture(base, files={"tools/demo.py": good_nonblocking}) / "tools" / "demo.py"
    check("dynamic/nonblocking-stderr-vectors-exit-1",
          [stderr_vector_observed(vector_path, version, vector) for vector in STDERR_VECTORS],
          [(1, "", "", [])] * 4)
    # Both retired shapes fail a vector and the guard leg refuses each. The oldest wrote the
    # refusal bare before `raise SystemExit`: with stderr unavailable the write raised and the
    # process exited 1, so a PreToolUse refusal failed open (and the flushraises vector turns its
    # `raise SystemExit` into the interpreter's own exit 120). The wrapped-write shape held the
    # exit against a failing write but still ended in `raise SystemExit`, so the flushraises
    # vector alone turns its refusal exit into exit 120.
    old_shape = {"tools/demo.py": _entry(_old_guard_text("demo.py", floor))}
    check("dynamic/old-shape-stderr-fails-open",
          [stderr_vector_observed(_fixture(base, files=old_shape) / "tools" / "demo.py",
                                  version, vector)[0] for vector in STDERR_VECTORS], [1, 1, 120, 1])
    code, lines = evaluate(_fixture(base, source=listed, files=old_shape))
    check("guard/old-shape-unwrapped-write-finding",
          (code, _has(lines, guard_marker),
           _has(lines, "the refusal exit may not depend on the diagnostic write")),
          (1, True, True))
    noflush_shape = {"tools/demo.py": _entry(_noflush_guard_text("demo.py", floor))}
    check("dynamic/noflush-shape-flush-raises-exit-120",
          [stderr_vector_observed(_fixture(base, files=noflush_shape) / "tools" / "demo.py",
                                  version, vector)[0] for vector in STDERR_VECTORS], [2, 2, 120, 2])
    code, lines = evaluate(_fixture(base, source=listed, files=noflush_shape))
    check("guard/noflush-shape-finding",
          (code, _has(lines, guard_marker),
           _has(lines, "the refusal exit may not depend on the diagnostic write")),
          (1, True, True))
    code, lines = evaluate(_fixture(base, source=listed, files={
        "tools/demo.py": _entry(good, after="return\n")}))
    check("dynamic/compile-failure-finding",
          (code, _has(lines, "at patched"), _has(lines, guard_marker)), (1, True, False))
    prefix = guard_prefix(ast.parse(demo["tools/demo.py"]), "demo.py", floor)
    check("dynamic/boundary-continues-at-floor", boundary_observed(prefix, floor + (0,), ()),
          (0, CONTINUED + "\n", ""))
    check("dynamic/boundary-refuses-below-floor", boundary_observed(prefix, version, ())[:2], (2, ""))
    got = []
    for exc_class in (MemoryError, RecursionError):
        ast.dump = dump_overflow(exc_class)
        try:
            dynamic_findings(_fixture(base, files=deep), ["tools/demo.py"], floor)
            got.append("no exception")
        except CannotEvaluate as exc:
            got.append("too complex to evaluate: " + exc_class.__name__ in str(exc))
        except (MemoryError, RecursionError) as exc:
            got.append("raised " + type(exc).__name__)
        finally:
            ast.dump = real_dump
    check("dynamic/deep-tree-cannot-evaluate", got, [True, True])

    # The hook form (HOOK_SURFACES): a fixture hook at the core hook's path with two demo modes.
    hook_rel, demo_modes = HOOK_SURFACES[0], ("mode_a", "mode_b")
    hook_good = guard_text("aiqt_hooks.py", floor, demo_modes)
    hook = dict([(hook_rel, _entry(hook_good, before='"""Fixture hook."""\n'))])
    hook_listed = _source_text(surfaces=[hook_rel])
    check("guard/hook-form-passes", evaluate(_fixture(base, source=hook_listed, files=hook))[0], 0)
    for check_id, text in (
            ("guard/hook-cli-form-finding", _entry(guard_text("aiqt_hooks.py", floor))),
            ("guard/hook-unsorted-modes-finding",
             _entry(guard_text("aiqt_hooks.py", floor, ("mode_b", "mode_a")))),
            ("guard/hook-duplicate-modes-finding",
             _entry(guard_text("aiqt_hooks.py", floor, ("mode_a", "mode_a")))),
            ("guard/hook-computed-modes-finding", _entry(hook_good.replace(
                "FLOOR_FAIL_OPEN_MODES = ('mode_a', 'mode_b')", "FLOOR_FAIL_OPEN_MODES = tuple(['mode_a'])"))),
            ("guard/hook-blocking-fail-open-finding",
             _entry(hook_good.replace("os._exit(0)", "os._exit(2)")))):
        code, lines = evaluate(_fixture(base, source=hook_listed, files=dict([(hook_rel, text)])))
        check(check_id, (code, _has(lines, "canonical floor guard in its hook form")), (1, True))
    code, lines = evaluate(_fixture(base, source=listed, files=dict(
        [("tools/demo.py", _entry(guard_text("demo.py", floor, demo_modes)))])))
    check("guard/hook-form-on-cli-surface-finding", (code, _has(lines, guard_marker)), (1, True))
    hook_path = _fixture(base, files=hook) / hook_rel
    hook_refusal = expected_refusal("aiqt_hooks.py", floor, version, sys.executable)
    check("dynamic/hook-fail-open-mode-warns", refusal_observed(hook_path, version, (), ["mode_b"]),
          (0, expected_warning("aiqt_hooks.py", floor, version, sys.executable, "mode_b"), hook_refusal, []))
    check("dynamic/hook-other-mode-refuses",
          refusal_observed(hook_path, version, (), [DENY_PROBE_MODE]), (2, "", hook_refusal, []))
    check("dynamic/hook-no-mode-refuses", refusal_observed(hook_path, version, ()),
          (2, "", hook_refusal, []))
    check("dynamic/hook-stderr-vectors-deny-exact",
          [stderr_vector_observed(hook_path, version, vector) for vector in STDERR_VECTORS],
          [(2, "", "", [])] * 4)
    check("dynamic/hook-stderr-vectors-fail-open-warn",
          [stderr_vector_observed(hook_path, version, vector, ["mode_b"])
           for vector in STDERR_VECTORS],
          [(0, expected_warning("aiqt_hooks.py", floor, version, sys.executable, "mode_b"),
            "", [])] * 4)
    old_hook = dict([(hook_rel, _entry(_old_guard_text("aiqt_hooks.py", floor, demo_modes),
                                       before='"""Fixture hook."""\n'))])
    check("dynamic/hook-old-shape-stderr-fails-open",
          [stderr_vector_observed(_fixture(base, files=old_hook) / hook_rel, version, vector,
                                  [mode])[0] for vector in STDERR_VECTORS
           for mode in ("mode_b", DENY_PROBE_MODE)], [1, 1, 1, 1, 120, 120, 1, 1])
    code, lines = evaluate(_fixture(base, source=hook_listed, files=old_hook))
    check("guard/hook-old-shape-unwrapped-write-finding",
          (code, _has(lines, "canonical floor guard in its hook form")), (1, True))
    noflush_hook = dict([(hook_rel, _entry(_noflush_guard_text("aiqt_hooks.py", floor, demo_modes),
                                           before='"""Fixture hook."""\n'))])
    check("dynamic/hook-noflush-shape-flush-raises-exit-120",
          [stderr_vector_observed(_fixture(base, files=noflush_hook) / hook_rel, version, vector,
                                  [mode])[0] for vector in STDERR_VECTORS
           for mode in ("mode_b", DENY_PROBE_MODE)], [0, 2, 0, 2, 120, 120, 0, 2])
    code, lines = evaluate(_fixture(base, source=hook_listed, files=noflush_hook))
    check("guard/hook-noflush-shape-finding",
          (code, _has(lines, "canonical floor guard in its hook form")), (1, True))
    blocking = dict([(hook_rel, _entry(hook_good.replace("os._exit(0)", "os._exit(2)")))])
    blocked = dynamic_findings(_fixture(base, files=blocking), [hook_rel], floor)
    check("dynamic/hook-blocking-fail-open-finding",
          sorted(set(line.split(": got")[0] for line in blocked if "fail-open mode" in line)),
          sorted(["{} at patched {}.{}.{} with the fail-open mode {}".format(
                      hook_rel, *version, mode) for mode in demo_modes]
                 + ["{} at patched {}.{}.{} with the fail-open mode {} and {}".format(
                        hook_rel, *version, demo_modes[0], STDERR_VECTOR_LABELS[vector])
                    for vector in STDERR_VECTORS]))
    hook_prefix = guard_prefix(ast.parse(hook[hook_rel]), "aiqt_hooks.py", floor, demo_modes)
    check("dynamic/hook-boundary-continues-at-floor", boundary_observed(hook_prefix, floor + (0,), ()),
          (0, CONTINUED + "\n", ""))
    # The hook form's deny path (a PreToolUse, unknown or missing mode) exits 2, fixed: exit 1 does not
    # block a PreToolUse call. A hook surface listed in nonblocking-surfaces is cannot-evaluate, the guard
    # carrying exit 1 there (the fail-open the listing would otherwise demand) included; called directly
    # with such a listing, the guard and dynamic legs still require exit 2.
    hook_exit_1 = _entry(hook_good.replace("    os._exit(2)\n", "    os._exit(1)\n"),
                         before='"""Fixture hook."""\n')
    check("guard/hook-form-deny-exit-fixed-at-2",
          (guard_text("aiqt_hooks.py", floor, demo_modes, code=NONBLOCKING_EXIT) == hook_good,
           hook_good.endswith("    os._exit(2)\n"), hook_exit_1 != hook[hook_rel]),
          (True, True, True))
    got = []
    for rel, text in ((hook_rel, hook_exit_1), (hook_rel, hook[hook_rel]), (HOOK_SURFACES[1], hook_exit_1)):
        code, lines = evaluate(_fixture(base, source=_source_text(surfaces=[rel], nonblocking=[rel]),
                                        files=dict([(rel, text)])))
        got.append((code, _has(lines, "nonblocking-surfaces lists the hook surface(s) " + rel)))
    check("source/nonblocking-hook-surface-cannot-evaluate", got, [(2, True)] * 3)
    code, lines = evaluate(_fixture(base, source=hook_listed, files=dict([(hook_rel, hook_exit_1)])))
    direct = guard_findings(_fixture(base, files=dict([(hook_rel, hook_exit_1)])), [hook_rel], floor,
                            frozenset([hook_rel]))
    check("guard/hook-deny-exit-1-finding",
          (code, _has(lines, "canonical floor guard in its hook form"),
           _has(direct, "canonical floor guard in its hook form")), (1, True, True))
    denied = dynamic_findings(_fixture(base, files=dict([(hook_rel, hook_exit_1)])), [hook_rel], floor,
                              frozenset([hook_rel]))
    check("dynamic/hook-deny-exit-1-finding",
          [_has(denied, "with the mode {}, outside {}: got exit 1".format(DENY_PROBE_MODE, MODES_NAME)),
           _has(denied, "under no flag: got exit 1"), _has(denied, "fail-open mode")], [True, True, False])

    unguarded = {"tools/demo.py": _entry("import sys\n")}
    check("completeness/off-ignores-unguarded", evaluate(_fixture(base, files=unguarded))[0], 0)
    code, lines = evaluate(_fixture(base, source=_source_text(completeness=True), files=unguarded))
    check("completeness/on-unguarded-finding",
          (code, _has(lines, "tools/demo.py: a shipped entrypoint")), (1, True))
    check("completeness/on-all-listed-passes", evaluate(_fixture(
        base, source=_source_text(surfaces=["tools/demo.py"], completeness=True), files=demo))[0], 0)
    check("completeness/excluded-trees-ignored", evaluate(_fixture(
        base, source=_source_text(completeness=True), files={
            ".github/repo_only.py": _entry("import sys\n"),
            "opf/tools/_vendor/lib/__main__.py": _entry("import sys\n"),
            ".venv/lib/tool.py": _entry("import sys\n"),
            "tools/helper.py": "VALUE = 1\n"}))[0], 0)

    # The fallback leg: a module-level import fallback must not carry a retired guard shape; the
    # fixture file is no entrypoint and is not listed, so only this leg judges it.
    fallback_head = "import sys\n\ntry:\n    import tomllib\nexcept ModuleNotFoundError:\n"
    fallback_wrapped = ("    import os\n    try:\n"
                        "        sys.stderr.write(\"error: no tomllib\\n\")\n"
                        "        sys.stderr.flush()\n    except BaseException:\n        pass\n")
    code, lines = evaluate(_fixture(base, files=dict([("tools/helper.py", fallback_head
        + "    sys.stderr.write(\"error: no tomllib\\n\")\n    raise SystemExit(2)\n")])))
    check("fallback/raise-systemexit-and-bare-write-finding",
          (code, _has(lines, "exits through `raise SystemExit`"),
           _has(lines, "calls sys.stderr.write outside try/except BaseException")), (1, True, True))
    code, lines = evaluate(_fixture(base, files=dict([("tools/helper.py",
        fallback_head + fallback_wrapped + "    sys.exit(2)\n")])))
    check("fallback/sys-exit-finding", (code, _has(lines, "exits through sys.exit")), (1, True))
    code, lines = evaluate(_fixture(base, files=dict([("tools/helper.py", fallback_head
        + "    import os\n    try:\n        sys.stderr.write(\"error: no tomllib\\n\")\n"
        "    except BaseException:\n        pass\n    os._exit(2)\n")])))
    check("fallback/wrapped-write-no-flush-finding",
          (code, _has(lines, "without its explicit sys.stderr.flush()")), (1, True))
    check("fallback/current-template-passes", evaluate(_fixture(base, files=dict([(
        "tools/helper.py", fallback_head + fallback_wrapped + "    os._exit(2)\n")])))[0], 0)
    check("fallback/function-body-not-scanned-passes", evaluate(_fixture(base, files=dict([(
        "tools/helper.py", "import sys\n\n\ndef load():\n    try:\n        import tomllib\n"
        "    except ModuleNotFoundError:\n        sys.stderr.write(\"x\\n\")\n"
        "        raise SystemExit(2)\n    return tomllib\n")])))[0], 0)

    code, lines = evaluate(_fixture(base, source=_source_text(documentation=False), declarations=False))
    check("switch/off-finding", (code, [line for line in lines if "documentation-check is" in line]),
          (1, ["FAIL: {}: documentation-check is false, but its decided value is true "
               "(DOCUMENTATION_CHECK in tools/check_python_floor.py); changing it is a reviewed change "
               "to this gate".format(SOURCE_REL)]))
    code, lines = evaluate(_fixture(base, files={DECLARATION_FILES[0]: "Requires Python.\n"}))
    check("documentation/on-missing-phrase-finding",
          (code, [line for line in lines if "does not state" in line]),
          (1, ["FAIL: README.md: does not state 'Python 3.14 or newer'"]))
    check("documentation/on-present-passes", evaluate(_fixture(base, files=_declared()))[0], 0)
    check("documentation/on-absent-file-cannot-evaluate",
          evaluate(_fixture(base, declarations=False))[0], 2)
    # An older claim beside the floor statement: the visible sentence names 3.11, a comment the floor.
    code, lines = evaluate(_fixture(base, files={DECLARATION_FILES[3]: (
        "<!-- Python 3.14 or newer -->\n<p>The hooks require Python 3.11 or newer.</p>\n")}))
    check("claims/older-beside-phrase-finding",
          (code, [line for line in lines if "names Python" in line]),
          (1, ["FAIL: site/install.html:2: names Python 3.11, below the floor 3.14; a declaration may "
               "not state an older version"]))
    forms = ("Requires Python 3.14 or newer.\nCPython 3.13\npython3.12\nPython\n3.10\nPython&nbsp;3.9\n"
             "Python versions 3.12\nruns on 3.11+\nor 3.13 or later\nPython 2.7\nPython\u00a03.8\n")
    code, lines = evaluate(_fixture(base, files={DECLARATION_FILES[0]: forms}))
    check("claims/older-forms-findings",
          (code, [line.split(": names ")[0] + " " + line.split(" ")[4] for line in lines
                  if "names Python" in line]),
          (1, ["FAIL: README.md:2 3.13,", "FAIL: README.md:3 3.12,", "FAIL: README.md:4 3.10,",
               "FAIL: README.md:6 3.9,", "FAIL: README.md:7 3.12,", "FAIL: README.md:8 3.11,",
               "FAIL: README.md:9 3.13,", "FAIL: README.md:10 2.7,", "FAIL: README.md:11 3.8,"]))
    check("claims/floor-newer-and-other-versions-pass", evaluate(_fixture(base, files={
        DECLARATION_FILES[0]: "Requires Python 3.14 or newer.\nPython 3.14.4, Python 3.15, python3.14, "
        "3.14+ and 3.20 or later.\nRun python3 tools/x.py; spec 1.2.0 or later; OPF 1.3.0 and up; "
        "AIQT 2.7+; 13.1+.\n"}))[0], 0)
    check("claims/oversized-version-cannot-evaluate", evaluate(_fixture(base, files={
        DECLARATION_FILES[0]: "Requires Python 3.14 or newer.\nPython 3." + "1" * 5000 + "\n"}))[0], 2)
    # A file holding two copies of the phrase: one copy changed, or removed, is a finding.
    adopt = "opf/site/adopt.html"
    code, lines = evaluate(_fixture(base, files={
        adopt: "<p>Requires Python 3.14 or newer.</p>\n<p>Requires Python 3.14 or later.</p>\n"}))
    check("documentation/copies-one-changed-finding", (code, lines),
          (1, ["FAIL: {}: states 'Python 3.14 or newer' 1 time(s), not the 2 in DECLARATION_COPIES"
               .format(adopt), "FAIL: {}:2: states the floor as 'Python 3.14 or later', not as "
               "'Python 3.14 or newer'".format(adopt)]))
    code, lines = evaluate(_fixture(base, files={adopt: "<p>Requires Python 3.14 or newer.</p>\n"}))
    check("documentation/copies-one-removed-finding", (code, lines),
          (1, ["FAIL: {}: states 'Python 3.14 or newer' 1 time(s), not the 2 in DECLARATION_COPIES"
               .format(adopt)]))
    code, lines = evaluate(_fixture(base, files={
        DECLARATION_FILES[0]: "Requires Python 3.14 or newer.\nThe hooks need python 3.14 and later.\n"}))
    check("documentation/variant-wording-finding", (code, lines),
          (1, ["FAIL: README.md:2: states the floor as 'python 3.14 and later', not as "
               "'Python 3.14 or newer'"]))
    forms = ("Requires Python 3.14 or newer.\nSupports Python >= 3.10.\nNeeds Python \u2265 3.10.\n"
             "Python &gt;= 3.9\nCPython &ge; 3.12\nRuns on 3.12 and above.\nRuns on 3.12 or greater.\n"
             "Tested on Py 3.12.\nPython 3 or newer\nRequires >=3.11.\n3.11 onwards.\nPython 2\n")
    code, lines = evaluate(_fixture(base, files={DECLARATION_FILES[0]: forms}))
    check("claims/operator-major-and-phrase-findings",
          (code, [line.split(": names ")[0] + " " + line.split(" ")[4] for line in lines
                  if "names Python" in line]),
          (1, ["FAIL: README.md:2 3.10,", "FAIL: README.md:3 3.10,", "FAIL: README.md:4 3.9,",
               "FAIL: README.md:5 3.12,", "FAIL: README.md:6 3.12,", "FAIL: README.md:7 3.12,",
               "FAIL: README.md:8 3.12,", "FAIL: README.md:9 3,", "FAIL: README.md:10 3.11,",
               "FAIL: README.md:11 3.11,", "FAIL: README.md:12 2,"]))
    check("claims/operator-major-near-misses-pass", evaluate(_fixture(base, files={
        DECLARATION_FILES[0]: "Requires Python 3.14 or newer.\nPython 4 or newer; Python >= 3.14; "
        "run check.py 3 times; py3 wheels; python3 tools/x.py 2; Python 3.14.4; spec >= 1.2.0.\n"}))[0], 0)

    _red_on_revert(base, good)
    _rule_reverts(base)


def _gate_run(root, gate_source):
    _write(root, "tools/check_python_floor.py", gate_source)
    try:
        proc = subprocess.run(
            [sys.executable, "-I", "-B", str(root / "tools" / "check_python_floor.py")], cwd=root,
            env=CHILD_ENV, stdin=subprocess.DEVNULL, capture_output=True, timeout=CHILD_TIMEOUT * 20)
    except (OSError, subprocess.SubprocessError) as exc:
        raise CannotEvaluate("gate copy launch failed: {}".format(exc))
    return proc.returncode, proc.stdout.decode("utf-8", "backslashreplace").splitlines()


def _red_on_revert(base, good):
    """Each leg red on its fixture with the intact gate, which names that leg's own finding, and green
    on the same fixture with that leg's check removed from a copy of this gate."""
    gate_source = _read_text(Path(__file__).resolve())
    mutants = {}
    for leg, call in REVERT_CALLS:
        line = "findings.extend(" + call + ")"
        if gate_source.count(line) != 1:
            raise CannotEvaluate("red-on-revert anchor for the {} leg occurs {} times".format(
                leg, gate_source.count(line)))
        mutants[leg] = gate_source.replace(line, "findings.extend(())")
    listed = _source_text(surfaces=["tools/demo.py"])
    # Each case: the leg, its fixture, and the text of that leg's own finding.
    cases = (
        ("source", dict(source=_source_text(floor="3.13"), workflow_pin="3.13", template_pin="3.13",
                        files=_declared("3.13")),
         "decided floor"),
        ("switch", dict(source=_source_text(documentation=False)), "documentation-check is false"),
        ("pins", dict(workflow_pin="3.12"), "quality.yml:6: python-version '3.12' differs"),
        ("guard", dict(source=listed, files={"tools/demo.py": _entry(good, before="import os\n")}),
         "canonical floor guard"),
        ("dynamic", dict(source=listed, files={"tools/demo.py": _entry(good, after="return\n")}),
         "at patched"),
        ("fallback", dict(files=dict([("tools/helper.py", (
            "import sys\n\ntry:\n    import tomllib\nexcept ModuleNotFoundError:\n"
            "    sys.stderr.write(\"error: no tomllib\\n\")\n    raise SystemExit(2)\n"))])),
         "exits through `raise SystemExit`"),
        ("completeness", dict(source=_source_text(completeness=True),
                              files={"tools/demo.py": _entry("import sys\n")}),
         "tools/demo.py: a shipped entrypoint"),
        ("documentation", dict(files={DECLARATION_FILES[-1]: "Requires Python.\n"}),
         DECLARATION_FILES[-1] + ": does not state"),
        ("claims", dict(files={DECLARATION_FILES[-1]: "Requires Python 3.14 or newer; 3.12+ works.\n" + (
            "Requires Python 3.14 or newer.\n" * (DECLARATION_COPIES.get(DECLARATION_FILES[-1], 1) - 1))}),
         DECLARATION_FILES[-1] + ":1: names Python 3.12"),
    )
    results = {}
    for leg, kwargs, marker in cases:
        intact_rc, intact_lines = _gate_run(_fixture(base, **kwargs), gate_source)
        named = any(line.startswith("FAIL: ") and marker in line for line in intact_lines)
        results[leg] = (intact_rc, named, _gate_run(_fixture(base, **kwargs), mutants[leg])[0])
    check("revert/source-leg", results["source"], (1, True, 0))
    check("revert/switch-leg", results["switch"], (1, True, 0))
    check("revert/pins-leg", results["pins"], (1, True, 0))
    check("revert/guard-leg", results["guard"], (1, True, 0))
    check("revert/dynamic-leg", results["dynamic"], (1, True, 0))
    check("revert/fallback-leg", results["fallback"], (1, True, 0))
    check("revert/completeness-leg", results["completeness"], (1, True, 0))
    check("revert/documentation-leg", results["documentation"], (1, True, 0))
    check("revert/claims-leg", results["claims"], (1, True, 0))


def _rule_reverts(base):
    """Red-on-revert for each rule of the line model: a reviewer reproduction judged by evaluate() of this
    gate and of a copy loaded through importlib with that one rule removed from RULES."""
    copy = base / "python_floor_copy.py"
    _write(base, copy.name, _read_text(Path(__file__).resolve()))
    spec = importlib.util.spec_from_file_location("python_floor_copy", copy)
    attack = WORKFLOWS_REL + "/attack.yml"
    head = "name: Attack\non: push\njobs:\n  q:\n    runs-on: ubuntu-latest\n    steps:\n"
    step = "      - uses: actions/setup-python@v5\n"
    pin = "          python-version: '3.14'\n"
    escaped_uses = "      - ? uses\n        : \"actions/setup-\\x70ython@v5\"\n"
    explicit = "attack.yml:7: an explicit key"
    run = "      - run: python --version\n"
    shape = "attack.yml:7: a uses: value"
    anchored = "    - uses: &a actions/setup-python@v5\n"
    exercised = set()
    # (check id, rule, files, exit of the intact gate, its message or None)
    for check_id, rule, files, want, marker in (
        ("revert/grammar-codex-explicit-key-name", "grammar", {attack: head + step + (
            "        ? name\n        : \"start\n        with:\n" + pin + "        end\"\n")},
         2, "attack.yml:8: an explicit key"),
        ("revert/grammar-a1-explicit-key-quoted-value", "grammar", {attack: head + step + (
            "        ? name\n        : \"x\n        with:\n" + pin + "        y\"\n")},
         2, "attack.yml:8: an explicit key"),
        ("revert/grammar-a2-spaced-key", "grammar", {attack: head + step + (
            "        env:\n          MY VAR: \"x\n        with:\n" + pin + "        y\"\n")},
         2, "attack.yml:9: a key with a space in it"),
        ("revert/grammar-b1-explicit-uses-key", "grammar",
         {attack: head + escaped_uses + "      - run: python --version\n"}, 2, explicit),
        ("revert/grammar-b2-escaped-uses-key", "grammar",
         {attack: head + "      - \"u\\x73es\": \"actions/setup-\\x70ython@v5\"\n"},
         2, "attack.yml:7: a quoted key"),
        ("revert/grammar-b4-escaped-line-break", "grammar", {attack: head + (
            "      - ? uses\n        : \"actions/setup-\\\n        python@v5\"\n")}, 2, explicit),
        ("revert/grammar-b5-escaped-pin-key", "grammar", {attack: head + escaped_uses + (
            "        with:\n          \"python-\\x76ersion\": \"3.12\"\n")}, 2, explicit),
        ("revert/action-files-c3-composite", "action-files", {
            attack: head + "      - uses: ./.github/actions/py\n",
            ".github/actions/py/action.yml": "name: py\nruns:\n  using: composite\n  steps:\n"
            "    - uses: actions/setup-python@v5\n      with:\n        python-version: '3.12'\n"},
         1, ".github/actions/py/action.yml:7: python-version '3.12' differs"),
        ("revert/local-uses-target-missing-action", "local-uses-target",
         {attack: head + "      - uses: ./.github/actions/absent\n"},
         2, "attack.yml:7: uses: ./.github/actions/absent names a local path with no action file"),
        ("revert/comment-span-c22-column-zero-comment", "comment-span",
         {attack: head + step + "# note\n        with:\n" + pin}, 0, None),
        ("revert/duplicate-key-c28-two-with", "duplicate-key", {attack: head + step + "        with:\n"
            + pin + "        with:\n          cache: pip\n"},
         2, "attack.yml:10: the key with repeats line 8"),
        ("revert/grammar-dash-continuation", "grammar",
         {attack: head + "      - uses: actions/checkout@v4\n          - x\n"},
         2, "attack.yml:8: a plain scalar continued on the next line"),
        ("revert/uses-shape-leading-slash", "uses-shape",
         {attack: head + "      - uses: /actions/setup-python@v5\n" + run}, 2, shape),
        ("revert/uses-shape-double-slash", "uses-shape",
         {attack: head + "      - uses: actions//setup-python@v5\n" + run}, 2, shape),
        ("revert/uses-shape-dot-git", "uses-shape",
         {attack: head + "      - uses: actions/setup-python.git@v5\n" + run}, 2, shape),
        ("revert/with-list-pin", "with-list", {attack: head + step + "        with:\n"
            "          - python-version: '3.14'\n"},
         2, "attack.yml:8: a with: whose body is a block sequence"),
        ("revert/flow-uses-checked-out", "flow-uses", {attack: head + (
            "      - uses: actions/checkout@v4\n        with:\n"
            "          repository: someorg/py312-action\n          path: gen\n"
            "      - {uses: ./gen}\n")},
         2, "attack.yml:11: a flow mapping with the key uses"),
        ("revert/flow-uses-missing-target", "flow-uses",
         {attack: head + "      - {uses: ./absent}\n"},
         2, "attack.yml:7: a flow mapping with the key uses"),
        ("revert/with-child-sibling-pin", "with-child", {attack: head + (
            "      - with:\n        python-version: '3.14'\n        uses: actions/setup-python@v5\n")},
         1, "attack.yml:9: a step that uses actions/setup-python sets no python-version input"),
        ("revert/extension-case-upper-yml", "extension-case",
         {WORKFLOWS_REL + "/x.YML": head + anchored},
         2, "x.YML:7: a uses: line that is not one plain or quoted literal"),
        ("revert/extension-case-mixed-yaml", "extension-case",
         {WORKFLOWS_REL + "/x.Yaml": head + anchored},
         2, "x.Yaml:7: a uses: line that is not one plain or quoted literal"),
        ("revert/extension-case-action-file", "extension-case",
         {"tools/x/Action.YML": "runs:\n  using: composite\n  steps:\n" + anchored},
         2, "tools/x/Action.YML:4: a uses: line that is not one plain or quoted literal")):
        exercised.add(rule)
        root = _fixture(base, files=files)
        code, lines = evaluate(root)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.RULES = RULES - {rule}
        check(check_id, (code, marker is None or _has(lines, marker), module.evaluate(root)[0]),
              (want, True, 1 if want == 0 else 0))
    check("revert/every-rule-exercised", exercised == RULES, True)


def _expected_check_ids():
    try:
        manifest = tomllib.loads(_read_text(CHECKS_MANIFEST))
    except (CannotEvaluate, tomllib.TOMLDecodeError) as exc:
        print("SELF-TEST HARNESS ERROR: cannot read {}: {}".format(CHECKS_MANIFEST, exc),
              file=sys.stderr)
        return None
    for row in manifest.get("suite", []):
        if isinstance(row, dict) and row.get("id") == SUITE_ID:
            ids = row.get("expected-check-ids")
            if isinstance(ids, list) and all(isinstance(item, str) for item in ids) \
                    and len(set(ids)) == len(ids):
                return set(ids)
            break
    print("SELF-TEST HARNESS ERROR: missing or malformed suite {!r} in {}".format(
        SUITE_ID, CHECKS_MANIFEST), file=sys.stderr)
    return None


def self_test(report_path=None):
    if report_path is not None:
        # The execution report is finalized at interpreter exit, after this run's cleanup
        # (tools/_selftest_exit_report.py); nothing writes it in band. Imported only here: the
        # red-on-revert legs run a copy of this gate in a fixture root that does not carry it.
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import _selftest_exit_report
        _selftest_exit_report.arm(report_path, SUITE_ID, EXECUTED)
    try:
        with tempfile.TemporaryDirectory(prefix="python-floor-selftest-") as raw:
            _self_test_cases(Path(raw))
    except (CannotEvaluate, OSError) as exc:
        print("SELF-TEST HARNESS ERROR: {}".format(exc), file=sys.stderr)
        return 2
    expected = _expected_check_ids()
    if expected is None:
        return 2
    for check_id in sorted(expected - _EXECUTED_SET):
        FAILURES.append("execution-set/missing: {}".format(check_id))
    for check_id in sorted(_EXECUTED_SET - expected):
        FAILURES.append("execution-set/extra: {}".format(check_id))
    if FAILURES:
        print("SELF-TEST FAIL:")
        for failure in FAILURES:
            print("  - " + failure)
        return 1
    print("SELF-TEST PASS: {} unique checks executed (each leg red on its fixture and green with its "
          "check removed); execution set reconciled against tools/selftest_checks.toml".format(
              len(EXECUTED)))
    return 0


def main(argv):
    if not argv:
        code, lines = evaluate(ROOT)
        for line in lines:
            print(line, file=sys.stderr if code == 2 else sys.stdout)
        return code
    rest = argv[1:] if argv[0] == "--self-test" else list(argv)
    report_path = None
    if len(rest) == 2 and rest[0] == "--execution-report" and os.path.isabs(rest[1]):
        report_path, rest = rest[1], []
    if rest or (argv[0] != "--self-test" and report_path is None):
        print("usage: check_python_floor.py [--self-test] [--execution-report ABS_PATH] "
              "(the report path must be absolute)", file=sys.stderr)
        return 2
    return self_test(report_path)


if __name__ == "__main__":
    _code = main(sys.argv[1:])
    _finalizer = sys.modules.get("_selftest_exit_report")
    (sys.exit if _finalizer is None else _finalizer.exit_with)(_code)
