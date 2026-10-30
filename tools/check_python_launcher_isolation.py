#!/usr/bin/env python3
"""Enforce isolated Python execution for hook and gate launchers (a deterministic gate).

A Python launcher puts its own script directory first on the interpreter's module path, so a file
written beside the launched script (a tool-authored ``json.py`` next to a hook dispatcher, an ``os.py``
next to a gate) can shadow a standard-library import and silently neuter the control. Python's isolated
mode (``-I``, or the complete ``-P -E -s`` equivalent) removes the script directory from that path and
ignores the ambient environment, so a sibling can no longer shadow. This gate scans the launcher
configuration this repo ships and runs, and fails any direct ``python3`` launcher that is not isolated.

BOOTSTRAP SELF-GUARD. The gate's own first executable statements import only ``sys`` and refuse to run
(exit 2) unless the interpreter is itself isolated, so a sibling planted beside this gate cannot neuter
the gate before it can check anything. The refusal itself imports ``os`` for ``os._exit`` (the canonical
floor-guard shape; the comment above the guard says why that import loads no sibling). Only after that
guard passes does it import the rest of the stdlib and the sibling generator whose plugin path it tracks.

SCANNED SURFACES (the declared set, resolved from the repo root):
  - the generated plugin hook config plugin/aiqt-guardrails-hooks/hooks/hooks.json (required; the scan
    path is taken from gen_hooks so it tracks the generator),
  - tools/run_all_checks.sh (required),
  - every *.yml/*.yaml candidate under .github/workflows/ (required directory); only a regular file is
    scanned, and a candidate that is anything else (a symlink of any kind, never followed; a directory;
    an unreadable or otherwise non-regular entry) is a named cannot-evaluate (exit 2),
  - .claude/settings.json and .claude/settings.local.json (optional; a truly-absent file is a clean
    skip, but a present-but-unusable one, including a dangling symlink, is a cannot-evaluate),
  - the QA-suite Python sources tools/_qa_adapter.py, tools/audit_reference.py, and
    tools/check_internal_names.py (required), scanned for a sys.path insertion at index 0 that would
    re-add the script directory AHEAD of the stdlib and re-enable the sibling-shadow class under -I; their
    sanctioned sibling-import form is sys.path.append.

LAUNCHER PREDICATE. In a scanned command, a leading shell control keyword is skipped ONLY in its
literal (unquoted, unescaped) spelling: a quoted or backslash-escaped spelling (``i'f'``,
``"then"``, ``d\\o``, ``t'ime'``, ``'!'``) is a literal command name, so a python word after it
is not in a command-word position and is a cannot-evaluate, never a finding from a misread
position. A leading ``env`` and any ``VAR=val`` assignments are then skipped; a shell-level
assignment (before any ``env``) only when its name and ``=`` are unquoted and unescaped, so a
quoted ``'X=y'`` is a literal command name as well (an argument after ``env`` is matched decoded,
as env reads it). Commands split at a shell separator (``;``, ``&&``, ``|`` and the like) only in
its literal spelling: a quoted or escaped ``';'`` or ``\\;`` is an argument word, never a split;
the command word's basename must then match ``^python(3(\\.\\d+)?)?$`` to be a launcher (a non-python
command word is out of scope, neither pass nor fail). Interpreter options are the tokens after the
command word up to the first non-option, ``-m``, ``-c``, ``--``, or long ``--option``; single-dash
clusters expand letter by letter. ``-c`` and ``-m`` terminate the option scan in every form, separate
(``-c CMD``, ``-m MOD``), attached (``-cCMD``, ``-mMOD``), or mid-cluster (``-Ic...``): everything from
that point is the command/module operand and is never letter-scanned, so an isolation letter inside the
operand (``-cIbar``) is never credited. The value-taking interpreter options ``-W`` and ``-X`` are recognized:
their value is never letter-scanned, whether attached (``-Wxxx``, ``-Xxxx``, the remainder is the value)
or separate (``-W xxx``, ``-X xxx``, the next token is the value and is skipped, not read as the script);
so is the long ``--check-hash-based-pycs``, whose next token is its value. Only genuine valueless
single-letter flags (``I``, ``P``, ``E``, ``s``, ``B`` and the like) cluster and are letter-scanned. A
launcher is isolated iff ``I`` is among those option letters, or all of ``P``, ``E``, and ``s`` are.
Options after the script are never credited, and environment variables (PYTHONSAFEPATH and the like) are
never credited.

WORKFLOW run: GRAMMAR. A workflow `run:` value is read line-lexically, under a CLOSED grammar: the
only key spelling read is the bare word ``run`` (optionally under a ``- `` sequence dash) with no
whitespace before its colon, and the only VALUE forms accepted are a plain unquoted scalar, a
LITERAL (``|``) block scalar under the full header grammar below, and a single-quoted scalar with no
``''`` escape (decoded to its body, exactly PyYAML's reading). Every other spelling that could carry
or hide a run key or value is cannot-evaluate (exit 2, naming the file and line), never a misread: a
double-quoted scalar (its backslash escapes are not decoded here, so its shell text cannot be
proven), a single-quoted scalar with a ``''`` escape or no closing quote, a quoted scalar followed
by anything (a trailing comment included), a flow mapping or flow sequence step, value or line, a
run key inside a flow collection, ANY quoted key (an escaped spelling YAML decodes to ``run``
included), an explicit-key marker (``? run``), a key with whitespace before its colon (``run :``),
and any line opening with a YAML node property, tag, anchor or alias (``&k run:``, ``!!str run:``,
``*k :``). A FOLDED (``>``) block scalar is cannot-evaluate until folding is implemented (YAML folds
its content into one command, so a line-by-line reading can disagree with the decoded value); a
LITERAL block-scalar header is read under the full YAML block-header grammar: the ``|`` indicator,
an optional indentation digit (1-9) and an optional chomping indicator (``+``/``-``) in either
order, and an optional trailing comment (``run: | # x``, ``run: |2``); every content line of that
block is scanned. STEP GRAMMAR: inside a ``steps:`` block (a plain bare ``steps:`` key; one with a
same-line value is cannot-evaluate), the first entry must be a ``- `` item, and every line at the
item or step-key indentation must be a plain unquoted identifier key from the closed step-key set
(name, run, uses, with, env, if, id, shell, working-directory, continue-on-error, timeout-minutes),
optionally after the dash; a plain key outside that set, a non-key line, and a line at an
indentation the step model cannot place are each cannot-evaluate, and deeper lines (``with:``/
``env:`` values) keep the line-form handling above. A LITERAL block's content is bounded by the
KEY column, so a sibling key after a dashed ``- run: |`` block re-enters these rules instead of
being consumed as block content. CLOSED LINE GRAMMAR: outside block-scalar content, EVERY other
line must take a shape this reader models: a blank line, a comment line, a plain ``KEY:`` or
``KEY: VALUE`` mapping line whose key matches [A-Za-z_][A-Za-z0-9_-]* with the colon directly
after the key (optionally under ONE leading ``- `` sequence dash), or a plain ``- SCALAR``
sequence item (no quote, flow, node-property or block-header lead, no ``: `` and no trailing
colon that could spell a key). A flow collection is read ONLY as a whole ``KEY: [...]`` value
that is a flow SEQUENCE of plain or whole-quoted scalars containing no colon, brace, bracket or
``#``; a flow MAPPING value (``defaults: {...}``, an escaped, tagged or explicit-key spelling
inside it included), any other flow spelling, a nested ``- - `` entry (at most one leading dash
is modelled), a key with whitespace before its colon (a ``shell : bash`` under ``defaults:
run:`` included, which the plain-scalar path used to misread as command text), a value opening
with a node property, tag, anchor or alias, and a malformed block header are each
cannot-evaluate (exit 2, naming the file and line), never skipped. This closed grammar
OVER-REFUSES legal YAML outside the plain shapes it models (a quoted block-sequence item
``- "main"``, a flow sequence followed by a trailing comment, a ``---`` document marker, a
dotted or ``<<`` merge key, a plain-scalar continuation line): each is cannot-evaluate, a
disclosed conservative refusal, never a misread. A generic key's block-scalar
value (literal or folded) is consumed as content bounded by the KEY column and never scanned: it
is not a run: or shell: payload, so only its extent matters here. SHELL TEMPLATES: every
``shell:`` key line (a step's,
defaults.run's at the workflow or job level, and any other spelling of the key, an action input
named shell included, a disclosed over-read) is read under a CLOSED classification: a value
whose comment-stripped scalar is ITSELF one of the known non-python shell keywords (bash, sh,
pwsh, powershell, cmd), compared literally with NO shell decoding first, on one line with no
continuation, is clean (a spelling that only shell-decodes to a keyword, ``ba'sh'``,
``ba"sh"``, ``ba\\sh``, ``b''ash``, is cannot-evaluate; a following non-blank,
non-comment line indented deeper than the key is a plain-scalar continuation YAML folds into
the value: cannot-evaluate); a template whose
command word is a python interpreter with NO isolation options is handed to the launcher
predicate (a non-isolated finding, unchanged); every other value, a single- or double-quoted
keyword and a python template claiming isolation options included
(this reader does not model the runner's template invocation closely enough to credit them), is
cannot-evaluate. A bare ``run:`` whose deeper next line is a plain ``key:`` mapping line is read
as a MAPPING (the defaults.run shape): only ``shell`` (classified as above) and
``working-directory`` (a plain or single-quoted scalar) are read there; any other key, depth or
spelling is cannot-evaluate. An EMPTY same-line value is
read ONLY in the two proven shapes: the next non-blank, NON-COMMENT line, when deeper than the key, is
either the plain-scalar command itself or a LITERAL block header whose content is deeper than both the
header and the key (its content lines, bounded by the key column, are scanned); the CLOSED line
grammar governs that next line BEFORE it is read as command text: a spelling YAML reads as a
MAPPING or SEQUENCE value there -- a mapping key of ANY spelling (an identifier key, a spaced
``shell : bash``, a ``foo.bar :`` or ``123:`` key, an UNQUOTED plain value whose operative text
before its `` #`` trailing comment carries ``: ``/``:<TAB>`` or ends with a colon; a WHOLE quoted
scalar is never mapping-tested, it is decoded or refused by the value grammar, so a single-quoted
``'echo foo: bar'`` and a ``: `` inside a trailing comment stay clean scalars), an explicit-key
indicator (``? shell``), or a ``- `` sequence item (``- shell: bash``)
-- is cannot-evaluate (only the plain identifier-key form is read, as the defaults.run mapping
above), never misread as command text; a sequence item at the KEY'S OWN COLUMN (comment lines
between included) is YAML's reading of the run: value as a SEQUENCE and is cannot-evaluate, never
read as null; the value is null (no
command text) only when that next non-blank, non-comment line is not deeper than the key and does
not begin a sequence item at the key's own column, or the file
ends. Any other arrangement after an empty ``run:`` (a comment line before a deeper value, a header
with no content deeper than the key, block content at or above the header's column, a comment line
inside the block before its content), any other ``run:`` value that starts with ``|`` or ``>`` (on
the key line or on the deeper line), a value that opens with a node property or alias (``&``, ``!``,
``*``), and a plain scalar continued onto a further deeper-indented line are cannot-evaluate (exit
2), never skipped.

EXIT CONVENTION: 0 every recognized launcher is isolated; 1 at least one recognized launcher is not
isolated; 2 cannot-evaluate (a required input missing, unreadable, non-regular, or malformed; a JSON
parse error; a shell line carrying a python token whose command-word position cannot be established; a
workflow `run:` value outside the run: grammar above; a hook entry with a missing type/command or a
non-list args; or the gate's own interpreter not isolated).
Diagnostics are deterministic, sorted by relative path then line then location.

DISCLOSED RESIDUAL (this gate does not catch): wrapper or indirect launchers (``bash -c "python3 ..."``,
a ``.sh`` re-launcher), ``$PYTHON`` and shell aliases or functions, a dynamically assembled argv, bash
brace expansion (``pyth{o,}n3``, which the shell-line prefilter skips because no quote or backslash
breaks a ``python`` substring, expands to ``python3 pythn3``), the
programmatic ``subprocess`` children in the tools and the ``regenerate`` strings in .aiqt/gensrc.json
(a separate follow-up), a runtime ``sys.path`` mutation OTHER than a literal index-0 insertion in the
three enumerated QA-suite sources (a ``sys.path[0:0]`` slice, a computed index, a runtime-assembled
insertion, or one in another source is not caught, and even for those three ``-I`` cannot prevent a
runtime mutation the source performs), an unrecognized interpreter name, launcher configuration outside the enumerated surfaces,
YAML or shell constructs beyond the supported line grammar (a run key or value spelling outside it,
every non-plain key spelling, a folded block scalar and an off-allowlist step key are refused, exit
2; a plain key other than run: and shell: outside a steps: block has its VALUE unscanned (its
line shape is still checked against the closed line grammar), and the
steps tracker is single-level, so a nested plain steps: key ends the outer block's allowlist
check early),
and the PATH provenance of ``python3``
itself. A ``python3 tools/*.py`` token embedded in a quoted argument or a heredoc may be miscounted,
mirroring the roster-scan limit the enforceability ledger discloses.

  check_python_launcher_isolation.py             scan the declared surfaces
  check_python_launcher_isolation.py --self-test build synthetic trees and assert the gate's invariants

Run this gate isolated: python3 -I -B tools/check_python_launcher_isolation.py
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: check_python_launcher_isolation.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

def _interpreter_isolated(flags):
    """True iff a sys.flags-like object reports isolated mode: the -I flag, or the full -P (safe_path,
    Python 3.11+) plus -E (ignore_environment) plus -s (no_user_site) equivalent. Anything short of that
    leaves the script directory able to shadow a stdlib import."""
    return bool(flags.isolated) or bool(
        getattr(flags, "safe_path", 0) and flags.ignore_environment and flags.no_user_site)


# The bootstrap self-guard: refuse to run non-isolated, BEFORE importing anything a sibling could shadow.
# It refuses through the canonical floor-guard shape (tools/check_python_floor.py GUARD_TEMPLATE): the
# write and its flush are best-effort, and os._exit skips the interpreter-exit flush of the std
# streams, so the exit stays 2 when sys.stderr is None, fails on write or fails on flush (`raise
# SystemExit` became exit 120 under a failing flush, and an unwrapped write to a None stream, exit 1).
# `import os` here loads no sibling: a normal start has already imported os, and under -S the frozen
# os module is found before the script directory is searched; only a launch that also passes
# -X frozen_modules=off could reach a sibling os.py, and such a launch already chooses its own flags.
if not _interpreter_isolated(sys.flags):
    import os
    try:
        sys.stderr.write("check_python_launcher_isolation: refusing to run non-isolated; launch it as "
                         "`python3 -I -B tools/check_python_launcher_isolation.py` (a sibling file could "
                         "otherwise shadow a stdlib import and neuter this gate)\n")
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import json  # noqa: E402  imported only after the isolation guard above
import os  # noqa: E402
import re  # noqa: E402
import shlex  # noqa: E402
import stat  # noqa: E402
from pathlib import Path  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    import gen_hooks  # noqa: E402  the generator whose plugin hooks.json path this gate tracks
except Exception:  # noqa: BLE001  an import failure is a cannot-evaluate, not a traceback
    # The message is a literal, as the fallback grammar in tools/check_python_floor.py requires (it
    # admits no call, and formatting the exception runs its __str__), so it does not quote the error;
    # running the generator itself (python3 -I -B tools/gen_hooks.py --check) shows it.
    import os
    try:
        sys.stderr.write("check_python_launcher_isolation: cannot import gen_hooks; fail-closed\n")
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

HOOKS_JSON_REL = gen_hooks.HOOKS_JSON_REL
RUN_ALL_REL = "tools/run_all_checks.sh"
WORKFLOWS_REL = ".github/workflows"
OPTIONAL_SETTINGS = (".claude/settings.json", ".claude/settings.local.json")
REQUIRED_FORM = "-I (or the full -P -E -s) before the script"

# The QA-suite Python sources whose sibling-import posture this gate keeps isolated. Each imports a sibling
# module (the QA adapter, the shared tree walk, the leak gate) and MUST do so with sys.path.append, never a
# sys.path insertion at index 0: under `python3 -I` an index-0 insertion re-adds the script's own directory
# AHEAD of the stdlib, so a sibling json.py/hashlib.py (raise SystemExit(0)) can shadow a stdlib import and
# silently neuter the self-test. These sources are REQUIRED (a missing one is a cannot-evaluate).
PYSOURCE_ISOLATION_REL = ("tools/_qa_adapter.py", "tools/audit_reference.py", "tools/check_internal_names.py")
# A sys.path insertion at index 0 (`sys.path.insert(0, ...)`, also spelled with whitespace); the sanctioned
# form for the sources above is sys.path.append, which preserves stdlib precedence.
SYS_PATH_FRONT_INSERT_RE = re.compile(r"sys\.path\.insert\(\s*0\b")

PY_WORD_RE = re.compile(r"^python(3(\.\d+)?)?$", re.ASCII)     # a whole token that is a python interpreter name
PY_TOKEN_RE = re.compile(r"\bpython3?\b")            # a python word anywhere in a line (a fast pre-filter)
QUOTE_OR_ESCAPE_RE = re.compile(r"""['"\\]""")       # a quote or backslash that could obfuscate a launcher name
ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")  # a shell VAR=val inline assignment
# YAML `run:` key, optionally under a `- ` sequence dash; captures the leading indent, the key itself (its
# column is the parent mapping indentation), and the scalar value.
RUN_RE = re.compile(r"^(?P<indent>\s*)(?:-\s+)?(?P<key>run):(?:[ \t]+(?P<val>.*))?$")
# A YAML block-scalar header, read under the full block-header grammar: the `|` or `>` indicator, then an
# optional indentation digit (1-9) and an optional chomping indicator (`+` or `-`) in either order, then an
# optional comment (whitespace, then `#`). Any other `run:` value that starts with `|` or `>` is outside the
# supported grammar and is a cannot-evaluate, never an inline scalar whose block content goes unread. A
# header carrying the FOLDED `>` indicator matches this grammar but is refused before any content is read
# (folding is not implemented); only the literal `|` form is read as a block.
BLOCK_HEADER_RE = re.compile(r"^[|>](?:[1-9][+-]?|[+-][1-9]?)?(?:[ \t]+#.*)?$")
# YAML `shell:` key, optionally under a `- ` sequence dash: a step's shell key, defaults.run's at
# either level, or any other spelling of the key (an action input named shell is read the same
# way, a disclosed over-read). The value selects the interpreter the run: script is handed to, so
# it is READ under the closed classification of _classify_shell_value, never skipped.
SHELL_RE = re.compile(r"^(?P<indent>\s*)(?:-\s+)?(?P<key>shell):(?:[ \t]+(?P<val>.*))?$")
# A plain `key:` mapping line (no dash), the shape that makes a bare `run:` a MAPPING rather
# than a scalar (the defaults.run form: shell / working-directory keys under `run:`).
RUN_MAPPING_KEY_RE = re.compile(r"^(?P<key>[A-Za-z][A-Za-z0-9_-]*):(?:[ \t]+(?P<val>.*))?$")
# The closed set of bare shell keywords this reader accepts as provably NOT a python
# interpreter; any other non-python template is refused rather than classified.
KNOWN_SHELLS = frozenset({"bash", "sh", "pwsh", "powershell", "cmd"})
# A `run:` value that opens with a YAML node property or alias (an `&anchor`, a `!tag`, a `*alias`) does not
# carry its shell text where this line grammar reads it, so it is a cannot-evaluate too.
NODE_PROPERTY_LEADS = ("&", "!", "*")
# Line spellings OUTSIDE the plain-key grammar under which YAML could still read a key (a run key
# included) or value where this line reader sees none: each is REFUSED (cannot-evaluate, exit 2,
# naming the file and line), never silently skipped, at EVERY depth. A flow mapping or flow sequence
# -- as a sequence step, as a line of its own, or carrying a `run:` key inline -- an explicit-key
# marker, ANY quoted key (so an escaped spelling that YAML decodes to a run key is refused rather
# than missed), ANY key with whitespace before its colon, and any line opening with a YAML node
# property, tag, anchor or alias are all spellings this line reader does not decode, so none may
# pass unread. Each form is matched after ANY number of leading `- ` sequence dashes (a nested
# `- - ` entry included), so a deeper sequence nesting cannot carry a spelling past the refusal;
# a nested `- - ` entry is itself ALSO refused (at most one leading dash is modelled, so a plain
# run: or shell: key nested under `- - `, which used to be skipped unread, can no longer pass),
# and the whitespace-before-colon refusal covers EVERY plain key, not only run (a `shell : bash`
# under `defaults: run:` used to be misread downstream as scalar command text and passed clean).
UNSUPPORTED_LINE_FORMS = (
    (re.compile(r"^\s*(?:-\s+)*-\s*[\[{]"),
     "a flow mapping or flow sequence step, outside the supported line grammar"),
    (re.compile(r"^\s*[\[{]"),
     "a line opening a flow collection, outside the supported line grammar"),
    (re.compile(r"""^\s*(?:-\s+)*(['"]).*?\1\s*:"""),
     "a quoted key (escaped spellings included), outside the supported line grammar"),
    (re.compile(r"^\s*(?:-\s+)*\?(?:[ \t]|$)"),
     "an explicit-key marker ('? '), outside the supported line grammar"),
    (re.compile(r"^\s*(?:-\s+)*[A-Za-z_][A-Za-z0-9_-]*[ \t]+:(?:[ \t]|$)"),
     "a key with whitespace before its colon, outside the supported line grammar"),
    (re.compile(r"""[\[{,][ \t]*(['"]?)run\1[ \t]*:"""),
     "a run key inside a flow collection, outside the supported line grammar"),
    (re.compile(r"^\s*(?:-\s+)*[&!*]"),
     "a line opening with a YAML node property, tag, anchor or alias, outside the supported "
     "line grammar"),
    (re.compile(r"^\s*(?:-\s+){2,}"),
     "a nested '- - ' sequence entry (at most one leading '- ' sequence dash is read), outside "
     "the supported line grammar"),
)
# GitHub-workflow STEP grammar: inside a `steps:` block (a plain, bare `steps:` key with no
# same-line value), every line at the step-item or step-key indentation must be a PLAIN, unquoted
# identifier key from this closed set (optionally after a `- ` item dash). A plain key outside the
# set, a key spelling that is not a plain identifier, a step entry that is not a `- key:` mapping
# line, and a line at an indentation the step model cannot place are each cannot-evaluate (exit 2,
# naming the file and line): the reader accepts only the step shapes it can prove it reads, instead
# of skipping what it does not recognize (an escaped or tagged run key used to be skipped exactly
# there).
STEP_KEYS = frozenset({"name", "run", "uses", "with", "env", "if", "id", "shell",
                       "working-directory", "continue-on-error", "timeout-minutes"})
STEPS_KEY_RE = re.compile(r"^(?P<indent> *)steps:[ \t]*(?:#.*)?$")
STEPS_VALUE_RE = re.compile(r"^ *steps:[ \t]+\S")
PLAIN_STEP_KEY_RE = re.compile(r"^(?P<indent> *)(?P<dash>- +)?(?P<key>[A-Za-z][A-Za-z0-9-]*):(?:[ \t]|$)")
# The CLOSED generic line grammar (_check_line_grammar): every plain line outside block-scalar
# content that no earlier rule places must be a plain `KEY:` or `KEY: VALUE` mapping line, the
# colon directly after the key, optionally under ONE leading `- ` sequence dash, or a plain
# `- SCALAR` sequence item; everything else is cannot-evaluate, never skipped.
GENERIC_KEY_LINE_RE = re.compile(
    r"^(?P<indent> *)(?P<dash>- +)?(?P<key>[A-Za-z_][A-Za-z0-9_-]*):(?:$|[ \t]+(?P<val>.*)$)")
PLAIN_ITEM_RE = re.compile(r"^(?P<indent> *)- +(?P<item>\S.*)$")
# A plain key with whitespace before its colon on the line AFTER an empty `run:`, where YAML
# reads the run: value as a MAPPING (the defaults.run shape), never as the scalar command text
# the plain-scalar branch would otherwise read (a `shell : bash` there used to pass clean).
SPACED_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*[ \t]+:(?:[ \t]|$)")
# Shell separators (as whole tokens after shlex) that end one simple command and begin the next.
SEPARATORS = {";", ";;", "|", "||", "&&", "&"}
# Unquoted shell operators separated from adjacent words before tokenizing, longest first so `;;`,
# `&&`, and `||` win over their single-character prefixes. shlex.split leaves an operator glued to a
# word as one token (`prep;python3`, `(python3`), which would hide a launcher after a punctuation-
# adjacent operator; separating them first makes tokenization segment as a real shell does.
SHELL_OPERATORS = (";;", "&&", "||", ";", "|", "&", "(", ")")
# Shell control keywords that may lead a simple command; skipped so the command word after them is found.
LEADING_KEYWORDS = {"if", "then", "else", "elif", "fi", "do", "done", "while", "until", "for", "time",
                    "!", "{", "}", "(", ")"}


def _exists(path):
    """Fail-closed existence probe (the gen_hooks idiom): Path.stat() raises on EACCES so an unreadable
    parent surfaces as OSError (mapped to a cannot-evaluate), rather than Path.exists() masking a
    present-but-unreadable target as absent."""
    try:
        path.stat()
    except FileNotFoundError:
        return False
    return True


def _present(path):
    """Presence probe for an OPTIONAL surface, via lstat (which does NOT follow a symlink). Unlike
    _exists (which stat()s THROUGH a symlink and so reads a present-but-dangling symlink as absent),
    this distinguishes a truly-absent path (no filesystem entry at all: lstat raises FileNotFoundError
    -> a clean skip) from anything PRESENT (a regular file, or a dangling symlink whose target is
    gone, or an entry lstat cannot read). A present-but-unusable entry returns True so it is routed to
    _read_required_text and fails closed there, rather than being silently skipped as absent."""
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    except OSError:
        return True                      # a present entry lstat cannot read: route to cannot-evaluate
    return True


def _basename(token):
    return token.rsplit("/", 1)[-1]


def _is_py_word(token):
    return bool(PY_WORD_RE.match(_basename(token)))


def _strip_launcher_prefix(pairs):
    """Return the (decoded, raw) token pairs from the command word onward: skip leading shell
    keywords, a `run_gate <label>` wrapper (the run_all_checks.sh idiom), and a leading `env` or
    any `VAR=val` assignments. A shell control keyword is skipped ONLY in its literal spelling
    (the raw token equals the decoded one): bash recognizes a reserved word, `!` or `time` only
    unquoted and unescaped, so a quoted or backslash-escaped spelling (`i'f'`, `"then"`, `d\\o`,
    `t'ime'`, `'!'`) is a literal command NAME, never a skipped keyword, and the python word
    after it is then not in a command-word position (_scan_command_tokens fails closed:
    cannot-evaluate, never a finding from a misread position). `run_gate` and `env` are matched on
    the decoded form (quoting does not stop the shell from resolving a command or function name).
    A shell-level `VAR=val` assignment (before any `env`) is matched on the RAW lexeme: bash reads
    a word as an assignment only when its name and `=` are unquoted and unescaped (`X="a b"` is
    one), so a quoted or escaped spelling (`'X=y'`, `"X"=y`, `X\\=y`) is a literal command NAME,
    never a skipped assignment, and the python word after it is a cannot-evaluate (the decoded
    match used to skip it and read `'X=y' python3 -I x.py` clean). After `env` an assignment is
    matched on the decoded form, because env reads its arguments after the shell decoded them
    (`env 'X=y' python3` does run python3 with X set)."""
    i = 0
    while i < len(pairs) and pairs[i][0] in LEADING_KEYWORDS and pairs[i][1] == pairs[i][0]:
        i += 1
    if i < len(pairs) and pairs[i][0] == "run_gate":
        i += 1
        if i < len(pairs):  # skip the gate label argument
            i += 1
    after_env = False
    while i < len(pairs) and (pairs[i][0] == "env"
                              or ASSIGN_RE.match(pairs[i][0] if after_env else pairs[i][1])):
        after_env = after_env or pairs[i][0] == "env"
        i += 1
    return pairs[i:]


VALUE_SHORT_OPTS = frozenset("WX")               # short options that take a value (-Wxxx or -W xxx)
VALUE_LONG_OPTS = frozenset({"--check-hash-based-pycs"})  # long options that take a separate value


def _flags_isolated(after_interpreter):
    """Collect the interpreter option letters from the tokens after the command word, stopping at the
    first non-option, `-m`, `-c`, `--`, or an unrecognized long `--option`, and expanding a single-dash
    cluster letter by letter. A value-taking option (`-W`/`-X`, attached or separate, and the long
    `--check-hash-based-pycs`) has its value skipped rather than letter-scanned, so an isolation letter is
    never forged from a value and a real flag after a separate value is never missed. Isolated iff `I`, or
    all of `P`, `E`, `s`."""
    flags = set()
    tokens = list(after_interpreter)
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok in ("--", "-m", "-c"):
            break
        if tok in VALUE_LONG_OPTS:
            i += 2                       # skip the long option and its separate value token
            continue
        if tok.startswith("--"):
            break                        # an unrecognized long interpreter option: not a flag we credit
        if tok.startswith("-") and len(tok) > 1:
            j = 1
            skip_next = False
            terminate = False
            while j < len(tok):
                letter = tok[j]
                if letter in ("c", "m"):
                    # -c/-m end interpreter-option scanning even mid-cluster or attached (-cCMD,
                    # -mMOD, -Ic...): the rest of this token, and every following token, is the
                    # command/module operand, never letter-scanned, so it cannot forge an isolation
                    # letter. The separate forms (-c CMD, -m MOD) are handled by the break above.
                    terminate = True
                    break
                if letter in VALUE_SHORT_OPTS:
                    # The rest of this token is the value (attached, -Wxxx); if the letter ends the
                    # token, the value is the next token (separate, -W xxx). Neither is letter-scanned.
                    skip_next = (j == len(tok) - 1)
                    break
                flags.add(letter)        # a valueless flag: credit it (e.g. -PEs -> P, E, s)
                j += 1
            if terminate:
                break
            i += 2 if skip_next else 1
            continue
        break                            # the script/program token: options end here
    return ("I" in flags) or {"P", "E", "s"}.issubset(flags)


def check_argv(argv):
    """Classify one already-split command word list (a JSON argv: no shell decoding happened, so
    each token is its own raw/literal spelling). Returns None if it is not a python launcher (out
    of scope), True if isolated, False if a python launcher that is not isolated."""
    rest = _strip_launcher_prefix([(tok, tok) for tok in argv])
    if not rest or not _is_py_word(rest[0][0]):
        return None
    return _flags_isolated([decoded for decoded, _ in rest[1:]])


def _segments(pairs):
    """Split a (decoded, raw) token pair list into simple-command segments at shell separator
    tokens, ONLY in their literal spelling (the raw lexeme equals the decoded one): bash reads a
    quoted or escaped `';'`, `"&&"` or `\\;` as an ordinary argument word, never a separator, so
    splitting there would put the next word in a command-word position the shell never gives it
    (`i'f' ';' python3 x.py` used to be a finding, and its `-I` form clean; both are a
    cannot-evaluate)."""
    segments, cur = [], []
    for pair in pairs:
        if pair[0] in SEPARATORS and pair[1] == pair[0]:
            if cur:
                segments.append(cur)
                cur = []
        else:
            cur.append(pair)
    if cur:
        segments.append(cur)
    return segments


def _strip_comment(line):
    """Drop a shell trailing comment (a space- or tab-preceded `#`) and a whole-line comment, the
    conservative lexical cut the enforceability roster scan uses. This does not parse the shell, so a `#`
    inside a quoted string may be over-trimmed; the residual is disclosed."""
    code = re.split(r"[ \t]#", line, maxsplit=1)[0]
    if code.lstrip().startswith("#"):
        return ""
    return code


def _space_shell_operators(s):
    """Insert a space on each side of every UNQUOTED, UNESCAPED shell operator, so a later
    shlex.split yields the operator as its own token even when it was glued to an adjacent word
    (`prep;python3` -> `prep ; python3`, `(python3` -> `( python3`), matching how a real shell
    segments. An operator inside single or double quotes, or preceded by a backslash escape, is left
    untouched. Quote and escape state are tracked exactly so a `;` or `&` inside a string is never
    mistaken for a separator; shlex then removes the quoting as usual.

    The `&` split is REDIRECTION-AWARE: an `&` that is part of a redirect operator, not a control
    operator, does NOT separate commands and is left glued to its redirect. That is an `&` that
    immediately follows an unquoted, unescaped `>` or `<` (the `>&`, `<&`, `N>&`, `N<&`, `>&WORD`,
    `>&-` fd-dup / merge / close forms; a leading fd number sits before the `>`/`<`, so the `&` still
    immediately follows it), or immediately precedes an unquoted `>` (the `&>`, `&>>` forms). Only a
    standalone background `&` and logical `&&` remain separators, so a redirect whose target is a
    filename like `python3` (`... >&python3`) is not mis-split into a fabricated bare launcher. A
    redirect target given as a SEPARATE, space-separated token that is itself a launcher name
    (`>& python3`) is not glued and is left to the caller, where it surfaces as a cannot-evaluate
    rather than a fabricated failure or a silent pass."""
    out = []
    i, n = 0, len(s)
    quote = None  # None, "'", or '"'
    prev_redirect = False  # the char just emitted was an unquoted, unescaped `>` or `<`
    while i < n:
        c = s[i]
        if quote == "'":
            out.append(c)                # single quotes: everything literal until the next '
            if c == "'":
                quote = None
            i += 1
            prev_redirect = False
            continue
        if quote == '"':
            out.append(c)                # double quotes: a backslash escapes the next char
            if c == "\\" and i + 1 < n:
                out.append(s[i + 1])
                i += 2
                prev_redirect = False
                continue
            if c == '"':
                quote = None
            i += 1
            prev_redirect = False
            continue
        if c == "\\":                    # unquoted backslash: the next char is literal, not an operator
            out.append(c)
            if i + 1 < n:
                out.append(s[i + 1])
                i += 2
            else:
                i += 1
            prev_redirect = False        # an escaped `>`/`<` is a literal char, not a redirect operator
            continue
        if c == "'" or c == '"':
            quote = c
            out.append(c)
            i += 1
            prev_redirect = False
            continue
        matched = next((op for op in SHELL_OPERATORS if s.startswith(op, i)), None)
        if matched:
            if matched == "&" and (prev_redirect or (i + 1 < n and s[i + 1] == ">")):
                # A single `&` that belongs to a redirect operator (it follows an unquoted `>`/`<`,
                # or precedes an unquoted `>`), not a control operator: leave it glued so it is not
                # spaced into a standalone separator token. `&&` matches longer above and is unaffected.
                out.append("&")
                i += 1
                prev_redirect = False
                continue
            out.append(" " + matched + " ")
            i += len(matched)
            prev_redirect = False
            continue
        out.append(c)
        prev_redirect = c in "<>"
        i += 1
    return "".join(out)


def _decode_tokens(spaced):
    """The decoded tokenization (shlex.split) of operator-spaced shell text. A module-level seam
    the self-test patches to inject a raw/decoded disagreement at the tokenization boundary, so
    both consistency guards in _shell_split_pairs are exercised deterministically."""
    return shlex.split(spaced, comments=False, posix=True)


def _shell_split_pairs(s):
    """Tokenize a shell line into (decoded, raw) pairs: the decoded tokens are shlex.split after
    separating unquoted shell operators from adjacent words (so an operator glued to a word,
    `prep;python3`, `(python3`, tokenizes as a real shell would); the RAW lexemes (quotes and
    backslashes preserved) come from splitting the SAME operator-spaced text on unquoted,
    unescaped whitespace under the exact quote/escape tracking _space_shell_operators uses. The
    raw form is what decides leading-keyword literalness in _strip_launcher_prefix. FAIL-CLOSED:
    raises ValueError on an unbalanced quote exactly as shlex.split does, and ALSO when the raw
    and decoded tokenizations disagree (a different token count, or a raw lexeme that does not
    shlex-decode to exactly its decoded token), so a token whose raw spelling cannot be recovered
    is a parse failure the callers surface as a cannot-evaluate, never a guessed spelling."""
    spaced = _space_shell_operators(s)
    decoded = _decode_tokens(spaced)
    raws, cur = [], []
    quote = None
    i, n = 0, len(spaced)
    while i < n:
        c = spaced[i]
        if quote is not None:
            cur.append(c)
            if quote == '"' and c == "\\" and i + 1 < n:
                cur.append(spaced[i + 1])
                i += 2
                continue
            if c == quote:
                quote = None
            i += 1
            continue
        if c == "\\":
            cur.append(c)
            if i + 1 < n:
                cur.append(spaced[i + 1])
                i += 2
            else:
                i += 1
            continue
        if c in ("'", '"'):
            quote = c
            cur.append(c)
            i += 1
            continue
        if c in " \t\r\n":
            if cur:
                raws.append("".join(cur))
                cur = []
            i += 1
            continue
        cur.append(c)
        i += 1
    if cur:
        raws.append("".join(cur))
    if len(raws) != len(decoded):
        raise ValueError("raw and decoded tokenizations disagree ({} raw token(s), {} decoded)"
                         .format(len(raws), len(decoded)))
    for dec, raw in zip(decoded, raws):
        if shlex.split(raw, comments=False, posix=True) != [dec]:
            raise ValueError("a raw token does not decode to its decoded form: {!r}".format(raw))
    return list(zip(decoded, raws))


def _shell_split(s):
    """The decoded token list of _shell_split_pairs (shlex.split semantics, operators pre-spaced);
    raises ValueError exactly as _shell_split_pairs does."""
    return [decoded for decoded, _ in _shell_split_pairs(s)]


def _scan_command_tokens(rel, lineno, loc, pairs, source_repr, errors, failures):
    """Scan an already-split (decoded, raw) token pair list for python launchers across its
    simple-command segments. Every python word must resolve to a command-word position; if the
    launcher count does not match the python words present, a launcher is hidden in an unexpected
    construct (one after a quoted or escaped leading keyword or assignment-like word, which is a
    literal command name, or after a quoted separator, which is an argument, included) and this
    is a cannot-evaluate, so it is never silently passed. Each recognized
    launcher that is not isolated is a failure."""
    py_word_count = sum(1 for dec, _ in pairs if _is_py_word(dec))
    launchers = []
    for seg in _segments(pairs):
        rest = _strip_launcher_prefix(seg)
        if rest and _is_py_word(rest[0][0]):
            launchers.append([dec for dec, _ in rest])
    if len(launchers) != py_word_count:
        errors.append((rel, lineno, loc, "a python token is not in a resolvable command-word position: "
                       "{!r}".format(source_repr)))
        return
    for rest in launchers:
        if not _flags_isolated(rest[1:]):
            failures.append((rel, lineno, loc, "non-isolated launcher {!r}; requires {}"
                             .format(" ".join(rest), REQUIRED_FORM)))


def _check_shell_line(rel, lineno, line, errors, failures):
    """Scan one shell line (from run_all_checks.sh or a workflow `run:` scalar) for python launchers.
    The decision is made on the DECODED command words, exactly as the settings path does via the shared
    `_scan_command_tokens`, so a quote/escape-obfuscated launcher NAME that bash resolves to a real
    interpreter (`pyt\\hon3`, `pyt"hon"3`, `py'thon'3`) is caught, while a LEADING KEYWORD is skipped
    only in its literal spelling (a quoted or escaped `i'f'`, `"then"`, `d\\o`, `t'ime'`, `'!'` is
    a literal command name bash would run, so the python word after it is not in a command-word
    position: a cannot-evaluate, never a finding from a misread position). Fail-closed: a line that
    may carry a python launcher but cannot be parsed, or one whose python token cannot be resolved
    to a command word, is a cannot-evaluate, so a launcher hidden in an unexpected construct is
    never silently passed.

    The raw prefilter remains ONLY as a cheap early-accept, and it is sound for quote removal alone:
    quote removal strips quote/backslash characters and never inserts letters, so a launcher that
    decoding produces must leave in the raw line either its contiguous `python` substring (matched by
    PY_TOKEN_RE) or a quote/backslash that broke that substring. A line carrying neither holds no
    launcher that quote removal could produce, and is skipped; anything else is tokenized and decided
    on the decoded words. The skip is not a proof that bash runs no launcher from that line: other
    expansions can build one from text the prefilter does not match, and two are disclosed residuals
    (see DISCLOSED RESIDUAL above): bash brace expansion (`pyth{o,}n3` expands to the words `python3
    pythn3`, so bash runs python3) and a dynamically assembled argv."""
    code = _strip_comment(line)
    if not PY_TOKEN_RE.search(code) and not QUOTE_OR_ESCAPE_RE.search(code):
        return
    try:
        pairs = _shell_split_pairs(code)
    except ValueError as exc:
        errors.append((rel, lineno, "", "shell line may carry a python launcher but does not parse; "
                       "fail-closed: {}".format(exc)))
        return
    _scan_command_tokens(rel, lineno, "", pairs, code.strip(), errors, failures)


def _decode_scalar(val):
    """Decode a same-line (or next-line) `run:` scalar under the CLOSED accepted value grammar.
    Returns (decoded, None) for an accepted form, (None, message) for a refused one. Accepted: a
    plain unquoted scalar, returned as-is (YAML strips its ` #` trailing comment exactly where the
    downstream shell comment strip does, so the two readings agree), and a single-quoted scalar
    whose body holds no quote (no `''` escape), decoded to its body, exactly what PyYAML yields for
    it. REFUSED (cannot-evaluate, never a misread): a double-quoted scalar (its backslash escapes,
    `\\n` or `\\u0070` and the like, are not decoded by this line reader, so its shell text cannot
    be proven), a single-quoted scalar carrying a `''` escape, a quoted scalar followed by anything
    (a trailing comment included: naive quote-stripping would hand the shell a still-quoted or
    rejoined command), and an unterminated quoted scalar."""
    lead = val[:1]
    if lead == "'":
        body = val[1:-1]
        if len(val) >= 2 and val.endswith("'") and "'" not in body:
            return body, None
        return None, ("a single-quoted run: value with a quote escape, trailing text or no closing "
                      "quote, outside the supported scalar grammar")
    if lead == '"':
        return None, ("a double-quoted run: value (its escapes are not decoded by this line "
                      "reader), outside the supported scalar grammar")
    return val, None


def _classify_shell_value(val):
    """Classify a `shell:` value under a CLOSED grammar. Returns (shell_line, message):
    `shell_line` is a command string for the launcher scan when the template's command word is a
    python interpreter WITHOUT isolation options (the standard launcher predicate then reports
    the non-isolated finding; the python-template classification INSIDE this function is
    unchanged, though the caller's deeper-continuation refusal can refuse a multi-line
    template before it reaches this classification); both None when the comment-stripped
    scalar is ITSELF one of the known non-python shell keywords (bash, sh, pwsh, powershell,
    cmd), compared LITERALLY with no shell decoding first, on one line (clean; the caller
    refuses a deeper continuation line; a single- or double-quoted keyword is refused here,
    and a plain spelling that only shell-decodes to a keyword, `ba'sh'`, `ba"sh"`,
    `ba\\sh`, `b''ash`, is refused too: YAML hands the runner the literal value,
    which is not a keyword); `message` is a cannot-evaluate reason for EVERYTHING else, so no
    shell: value ever passes unread. A python template that CLAIMS isolation options is
    cannot-evaluate rather than
    credited: this line reader does not model the hosted runner's template invocation (the
    temp-script path substituted for the {0} placeholder, the splitting of the template into
    argv) closely enough to prove the options reach the interpreter, so it refuses instead of
    crediting them."""
    val = (val or "").strip()
    if not val or val.startswith("#"):
        return None, ("a shell: key without a same-line value, outside the supported shell "
                      "grammar")
    if val[:1] in ("|", ">"):
        return None, "a shell: block-scalar value, outside the supported shell grammar"
    if val[:1] in NODE_PROPERTY_LEADS:
        return None, ("a shell: value opening with a YAML node property or alias, outside the "
                      "supported shell grammar")
    if val[:1] in ("{", "["):
        return None, "a shell: flow-collection value, outside the supported shell grammar"
    if val[:1] == '"':
        return None, ("a double-quoted shell: value (its escapes are not decoded by this line "
                      "reader), outside the supported shell grammar")
    if val[:1] == "'":
        return None, ("a single-quoted shell: value (only an UNQUOTED value is read; a quoted "
                      "known-shell keyword is refused too, a conservative over-refusal), "
                      "outside the supported shell grammar")
    # A plain scalar: YAML strips its ` #` trailing comment exactly where _strip_comment does, so
    # the two readings agree.
    code = _strip_comment(val).strip()
    if code in KNOWN_SHELLS:
        return None, None
    try:
        tokens = _shell_split(code)
    except ValueError:
        return None, ("a shell: template that does not parse as a command line, outside the "
                      "supported shell grammar")
    if not tokens:
        return None, "an empty shell: template, outside the supported shell grammar"
    if _is_py_word(tokens[0]):
        if not _flags_isolated(tokens[1:]):
            return code, None
        return None, ("a shell: template naming a python interpreter with isolation options; "
                      "this reader does not model the runner's template invocation closely "
                      "enough to credit them, outside the supported shell grammar")
    return None, ("a shell: value that is neither a bare known shell keyword (bash, sh, pwsh, "
                  "powershell, cmd) nor a python interpreter template, outside the supported "
                  "shell grammar")


def _flow_sequence_value_ok(val):
    """True iff the whole value is a flow SEQUENCE of plain or whole-quoted scalars: `[` through
    `]` with no colon, brace, nested bracket or `#` inside, every comma-separated item nonempty
    and either plain (no quote character, no node-property or explicit-key lead) or wholly
    single- or double-quoted with no inner quote. Everything else (a flow mapping, a nested
    collection, a flow entry that could spell a key, a trailing comment after the `]`) is
    outside the modelled value grammar, so the caller refuses it."""
    if not (val.startswith("[") and val.endswith("]")):
        return False
    body = val[1:-1]
    if any(ch in body for ch in ":{}[]#"):
        return False
    if not body.strip():
        return True
    for item in body.split(","):
        item = item.strip()
        if not item:
            return False
        if item[:1] in ("'", '"'):
            if not (len(item) >= 2 and item.endswith(item[:1]) and item[:1] not in item[1:-1]):
                return False
        elif "'" in item or '"' in item or item[:1] in ("&", "!", "*", "?"):
            return False
    return True


def _check_line_grammar(raw, lines, i, n, unsupported):
    """The CLOSED line grammar for a plain workflow line no earlier rule placed (outside
    block-scalar content; not a run:, shell: or steps: key; not an UNSUPPORTED_LINE_FORMS or
    step-grammar refusal): the line must be a plain `KEY:`/`KEY: VALUE` mapping line (the colon
    directly after the key, optionally under ONE leading `- ` sequence dash) or a plain
    `- SCALAR` sequence item; EVERY other line is cannot-evaluate, never skipped. A flow value is
    read only as a flow sequence of plain or quoted scalars (_flow_sequence_value_ok); a generic
    key's block-scalar value (literal or folded, BLOCK_HEADER_RE) is consumed as content bounded
    by the KEY column and never scanned: it is not a run: or shell: payload, so only its extent
    matters (its lines must never be misread as free-standing keys). Returns the next line index
    when it consumed such a block, else None (the caller advances one line)."""
    gm = GENERIC_KEY_LINE_RE.match(raw)
    if gm:
        gval = (gm.group("val") or "").strip()
        if not gval or gval.startswith("#"):
            return None
        if gval[:1] == "{":
            unsupported.append((i + 1, "a flow mapping value, outside the supported line "
                                      "grammar"))
            return None
        if gval[:1] == "[":
            if not _flow_sequence_value_ok(gval):
                unsupported.append((i + 1, "a flow collection value that is not a flow sequence "
                                          "of plain or quoted scalars (no colon, brace, bracket "
                                          "or '#'), outside the supported line grammar"))
            return None
        if gval[:1] in NODE_PROPERTY_LEADS:
            unsupported.append((i + 1, "a value opening with a YAML node property, tag, anchor "
                                      "or alias, outside the supported line grammar"))
            return None
        if BLOCK_HEADER_RE.match(gval):
            j = i + 1
            while j < n:
                content = lines[j]
                if content.strip() == "":
                    j += 1
                    continue
                if len(content) - len(content.lstrip(" ")) <= gm.start("key"):
                    break
                j += 1
            return j
        if gval[:1] in ("|", ">"):
            unsupported.append((i + 1, "a value that starts like a YAML block scalar but whose "
                                      "header is outside the block-header grammar, outside the "
                                      "supported line grammar"))
            return None
        return None
    pm = PLAIN_ITEM_RE.match(raw)
    if pm:
        item = pm.group("item")
        if (item[:1] not in ("'", '"', "{", "[", "&", "!", "*", "|", ">", "?", ":")
                and item != "-" and ": " not in item and not item.rstrip().endswith(":")):
            return None
        unsupported.append((i + 1, "a sequence item that is neither a plain scalar nor a plain "
                                  "key line, outside the supported line grammar"))
        return None
    unsupported.append((i + 1, "a line that is neither a plain 'KEY:' mapping line nor a '- ' "
                              "sequence item, outside the supported line grammar"))
    return None


def _yaml_run_lines(text):
    """Return (lines, unsupported): lines is a (lineno, shell_line) for every YAML `run:` scalar, every
    line inside a `run:` LITERAL block scalar, and every `shell:` template whose command word is a
    python interpreter without isolation options; unsupported is a (lineno, message) for every line this
    grammar cannot read, which the caller reports as a cannot-evaluate. A line-lexical extraction (no
    YAML library), with three layers.

    STEP LAYER (the positive step grammar). A plain, bare `steps:` key (no same-line value; a `steps:`
    carrying one is cannot-evaluate) opens a step block that runs until the first non-blank,
    non-comment line at or above its indentation (a `- ` item at exactly the `steps:` indentation
    stays inside, the YAML same-indent sequence form). The block's first entry must be a `- ` item;
    its dash fixes the item indentation and its key column fixes the step-key column. Every line AT
    those columns is accepted ONLY as a plain unquoted identifier key from STEP_KEYS (optionally
    after the item dash): a plain key outside that closed set, a non-key line, a nested `- -` entry,
    and a line at an indentation the step model cannot place are each cannot-evaluate, never
    skipped. Deeper lines (`with:`/`env:` values) keep the line-form handling below. DISCLOSED
    RESIDUAL: the tracker is single-level, so a deeper plain `steps:` key (an action input named
    steps) opens a new block and the outer block's remaining non-run keys are no longer
    allowlist-checked after it closes; a run key is still read or refused at every depth and every
    non-plain key spelling is refused by the line forms, so no launcher hides behind that residual.
    A LITERAL block's content is bounded by the KEY column (never the item dash's indent), so a
    sibling step key after a dashed `- run: |` block re-enters this layer and the line forms. A
    `shell:` key line at ANY position is read through _classify_shell_value: a comment-stripped
    scalar that is ITSELF a known non-python shell keyword (compared literally, with no shell
    decoding first) on one line is clean (a following non-blank, non-comment line indented
    deeper than the key is a continuation, cannot-evaluate), a python template without
    isolation options is yielded to the launcher scan, and every other value, a quoted keyword
    and a quote- or backslash-embedded spelling (`ba'sh'`, `ba\\sh`) included, is
    cannot-evaluate; a bare `run:` whose deeper next
    line is a plain `key:` mapping line is read as the defaults.run MAPPING (shell and
    working-directory keys only, anything else cannot-evaluate).

    LINE-FORM LAYER (every depth, UNSUPPORTED_LINE_FORMS and _check_line_grammar). Every line
    spelling a key outside the plain grammar is cannot-evaluate: a flow mapping or flow sequence
    step or line, a run key inside a flow collection, ANY quoted key (an escaped spelling YAML
    decodes to `run` included), an explicit-key marker, ANY key with whitespace before its colon,
    a nested `- - ` sequence entry (at most one leading dash is modelled), and any line opening
    with a YAML node property, tag, anchor or alias. The remaining grammar is POSITIVE (closed):
    a plain `KEY:`/`KEY: VALUE` line (the colon directly after the key, at most one leading `- `
    dash) or a plain `- SCALAR` item is accepted; a flow value only as a flow sequence of plain
    or quoted scalars (no colon, brace, bracket or '#'); a generic key's block-scalar value is
    consumed as content bounded by the key column, never scanned; EVERY other line is
    cannot-evaluate, never skipped.

    VALUE LAYER. A block header is read under the full block-header grammar (BLOCK_HEADER_RE:
    indicator, optional indentation digit and chomping indicator in either order, optional comment),
    but ONLY the literal `|` indicator is read as a block (its content is the run of lines indented
    deeper than the `run:` key, ending at the first non-blank line indented no deeper); a FOLDED
    (`>`) header is cannot-evaluate until folding is implemented, because YAML folds the content
    lines into ONE command and so CHANGES SHELL COMMENT BOUNDARIES (a first content line opening
    with `#` folds onto one line with the content after it, so the comment consumes the folded
    launcher lines that follow; a `#` line after other content folds into mid-command text), so a
    line-by-line reading can disagree with the decoded value the shell sees; the folded block's content is skipped as
    consumed, never scanned as free-standing lines. An EMPTY same-line value -- a bare `run:`, or a
    `run:` whose only same-line text is a YAML comment (`run: # note`), which YAML strips to the
    SAME empty value (proven against PyYAML) -- is read ONLY in the two shapes proven against
    PyYAML: the next non-blank, NON-COMMENT line, when deeper than the key, is either the
    plain-scalar command itself or a LITERAL block header whose content is deeper than both the
    header and the key (its content lines, bounded by the key column, are yielded). The CLOSED
    line grammar governs that next line BEFORE it is read as command text: a mapping key of ANY
    spelling (the plain identifier form is the defaults.run mapping; a spaced key, a `foo.bar :`
    or `123:` key, and an UNQUOTED plain value whose OPERATIVE text -- the part before its ` #`
    trailing comment -- carries `: `/`:<TAB>` or ends with a colon are cannot-evaluate; a WHOLE
    quoted scalar is never mapping-tested, it is decoded or refused by _decode_scalar, so
    `'echo foo: bar'` and a `: ` inside a trailing comment are clean scalars), an
    explicit-key indicator (`? shell`) and a `- ` sequence item (`- shell: bash`) are each
    refused, never misread as a command; a sequence item AT THE KEY'S OWN COLUMN (comment
    lines between included) is YAML's reading of the run: VALUE as a sequence and is refused
    BEFORE the null decision, never read as null. The value is
    null (nothing is yielded) only when that next non-blank, non-comment line is not deeper than
    the key and does not begin a sequence item at the key's own column, or the text ends.
    Unsupported (cannot-evaluate, never a silent null): a comment line
    between an empty `run:` and a deeper value line, a next-line block header with no content deeper
    than the key, block content at or above that header's column, a line opening with `#` directly
    after that next-line header (YAML reads it as block CONTENT, not a comment; this line reader
    refuses rather than reads it, a disclosed over-refusal), any other value starting with `|` or
    `>` (on the key line or the deeper line), a value opening with a node property or alias, and a
    plain scalar continued onto a further deeper-indented line (YAML folds it into the command, so a
    launcher there would otherwise go unread). The VALUE grammar is CLOSED: a same-line or next-line
    scalar is accepted only as a plain unquoted scalar or a single-quoted scalar with no `''` escape
    (decoded by _decode_scalar); a double-quoted scalar, a single-quoted scalar with an escape or no
    closing quote, a quoted scalar followed by anything (a trailing comment included), and a flow
    mapping or flow sequence value are unsupported (cannot-evaluate). Only `run:` payloads and
    non-isolated python `shell:` templates are yielded, so `uses:`/`with:` input VALUES
    (setup-python and the like) are never scanned (a `shell:`-spelled input KEY is read as a
    shell template, a disclosed over-read)."""
    lines = text.splitlines()
    out, unsupported = [], []
    in_steps, steps_indent, item_indent, key_col = False, -1, None, None
    i, n = 0, len(lines)
    while i < n:
        raw = lines[i]
        bare = raw.strip()
        is_plain_line = bool(bare) and not bare.startswith("#")
        d = len(raw) - len(raw.lstrip(" "))
        if in_steps and is_plain_line:
            # The step tracker: leave the block at its parent indentation (a `- ` item at exactly
            # the steps: indentation stays inside, the YAML same-indent sequence form), and fix the
            # item and key columns from the block's first entry.
            if d < steps_indent or (d == steps_indent and not raw.lstrip(" ").startswith("- ")):
                in_steps, item_indent, key_col = False, None, None
            elif item_indent is None:
                dm = re.match(r"^( *)(- +)\S", raw)
                if dm is None:
                    unsupported.append((i + 1, "a steps: block whose first entry is not a '- ' "
                                               "item, outside the supported step grammar"))
                    i += 1
                    continue
                item_indent, key_col = d, len(dm.group(1)) + len(dm.group(2))
        m = RUN_RE.match(raw)
        if not m:
            matched = False
            for pattern, message in UNSUPPORTED_LINE_FORMS:
                if pattern.search(raw):
                    unsupported.append((i + 1, message))
                    matched = True
                    break
            if not matched and is_plain_line:
                sm = STEPS_KEY_RE.match(raw)
                if sm:
                    in_steps, steps_indent = True, len(sm.group("indent"))
                    item_indent, key_col = None, None
                elif STEPS_VALUE_RE.match(raw):
                    unsupported.append((i + 1, "a steps: key with a same-line value, outside the "
                                               "supported step grammar"))
                else:
                    refused = False
                    if in_steps and item_indent is not None and d <= key_col:
                        km = PLAIN_STEP_KEY_RE.match(raw)
                        accepted = False
                        if km and km.group("key") in STEP_KEYS:
                            if km.group("dash"):
                                accepted = (d == item_indent and
                                            len(km.group("indent")) + len(km.group("dash"))
                                            == key_col)
                            else:
                                accepted = (d == key_col)
                        if not accepted:
                            unsupported.append((i + 1, "a step line outside the supported step "
                                                       "grammar (only a plain step key from the "
                                                       "supported set, optionally after a '- ' "
                                                       "item dash, is read at the step "
                                                       "indentation)"))
                            refused = True
                    if not refused:
                        shm = SHELL_RE.match(raw)
                        if shm:
                            # EVERY shell: key line outside consumed block-scalar content is
                            # read (a block's content lines are command text or consumed
                            # extent, never key lines): clean only when the comment-stripped
                            # scalar is ITSELF a known non-python shell keyword on ONE line
                            # (compared literally, with no shell decoding first); a python
                            # template with no isolation options is yielded to the launcher scan
                            # (a non-isolated finding); everything else is cannot-evaluate. A
                            # following non-blank, non-comment line indented deeper than the key
                            # is a plain-scalar CONTINUATION YAML folds into the value, so the
                            # one-line value this reader classified is not the decoded value:
                            # cannot-evaluate, never a partial read.
                            j = i + 1
                            while j < n and lines[j].strip() == "":
                                j += 1
                            if (j < n and not lines[j].lstrip().startswith("#")
                                    and len(lines[j]) - len(lines[j].lstrip(" "))
                                    > shm.start("key")):
                                unsupported.append((j + 1, "a shell: value continued on a "
                                                          "deeper-indented line, outside the "
                                                          "supported shell grammar"))
                                i = j + 1
                                continue
                            shell_line, message = _classify_shell_value(shm.group("val"))
                            if message is not None:
                                unsupported.append((i + 1, message))
                            elif shell_line is not None:
                                out.append((i + 1, shell_line))
                        else:
                            skip_to = _check_line_grammar(raw, lines, i, n, unsupported)
                            if skip_to is not None:
                                i = skip_to
                                continue
            i += 1
            continue
        val = (m.group("val") or "").strip()
        if val.startswith("#"):
            # A same-line value that is ONLY a YAML comment carries no value: YAML strips the comment
            # (proven against PyYAML), so the key has the SAME empty value as a bare `run:` and is
            # read through the bounded empty-value path below, never as an inline scalar (whose
            # continuation lookahead would step past the real deeper value).
            val = ""
        if not val:
            # An empty same-line value (a bare `run:`, or one whose only same-line text was a YAML
            # comment): YAML reads the run: value from a following deeper-indented
            # line. FAIL-CLOSED BOUNDS: the value is read ONLY in the two shapes proven against
            # PyYAML (a next-line plain scalar; a next-line LITERAL block header whose content is
            # deeper than both the header and the key, the content run bounded by the key column).
            # The null decision skips comment lines (a YAML comment carries no value): the value is
            # null ONLY when the next non-blank, NON-COMMENT line is not deeper than the key AND
            # does not begin a sequence item at the key's own column (YAML reads that as the run:
            # SEQUENCE value; the key-column check below refuses it BEFORE the null decision), or
            # the text ends. Any other arrangement (a comment line before a deeper value, a header
            # with no content deeper than the key, block content at or above the header's column,
            # a line opening with '#' directly after the header, which YAML reads as block content
            # but this reader refuses, and every folded header) is a cannot-evaluate, never a
            # silent null and never a partial read.
            j = i + 1
            saw_comment = False
            while j < n and (lines[j].strip() == "" or lines[j].lstrip().startswith("#")):
                if lines[j].strip():
                    saw_comment = True
                j += 1
            if j >= n:
                i += 1
                continue
            nxt = lines[j]
            nxt_indent = len(nxt) - len(nxt.lstrip(" "))
            nbare = nxt.strip()
            if (nxt_indent == m.start("key")
                    and (nbare == "-" or (nbare[:1] == "-" and nbare[1:2] in (" ", "\t")))):
                # A sequence item at the KEY'S OWN COLUMN: YAML reads a block sequence that sits
                # at its key's indentation as that key's VALUE, so this run: is NOT null. The
                # workflow-level and job-level defaults.run shapes (`run:` with `- python3 x.py`
                # at the key column, comment lines between included: the loop above already
                # skipped them) used to take the null decision below, and the item line then
                # passed the generic grammar as a plain sequence item, a false clean. The step
                # shape took the same null decision but was refused (exit 2) by the step-grammar
                # diagnostic instead, so it was never clean; it now gets this diagnostic too.
                # Refused BEFORE the null decision: cannot-evaluate, never read as null, never
                # scanned as command text.
                unsupported.append((j + 1, "a sequence item at the run: key's own column after "
                                          "an empty run: (YAML reads the run: value as a "
                                          "sequence, never as null), outside the supported "
                                          "line grammar"))
                i = j + 1
                continue
            if nxt_indent <= m.start("key"):
                i += 1
                continue
            if saw_comment:
                unsupported.append((j + 1, "run: value on the line after an empty run: follows a "
                                          "comment line, outside the supported line grammar"))
                i = j + 1
                continue
            nval = nxt.strip()
            mm = RUN_MAPPING_KEY_RE.match(nval)
            if mm:
                # The run: value is a MAPPING, not a scalar (the defaults.run shape: YAML reads a
                # deeper plain `key:` line under a bare `run:` as a mapping, which the scalar path
                # below would misread as command text). Only the two keys that shape carries are
                # read: `shell` through the closed classification, `working-directory` as a plain
                # or single-quoted scalar with a plain lead; any other key, any deeper or non-key
                # line, and any other spelling is cannot-evaluate, never skipped.
                k = j
                while k < n:
                    content = lines[k]
                    if content.strip() == "" or content.lstrip().startswith("#"):
                        k += 1
                        continue
                    cind = len(content) - len(content.lstrip(" "))
                    if cind <= m.start("key"):
                        break
                    cm = RUN_MAPPING_KEY_RE.match(content.strip())
                    if cind != nxt_indent or cm is None:
                        unsupported.append((k + 1, "a line inside a run: mapping that is not a "
                                                   "plain key at the mapping's own column, "
                                                   "outside the supported line grammar"))
                    elif cm.group("key") == "shell":
                        shell_line, message = _classify_shell_value(cm.group("val"))
                        if message is not None:
                            unsupported.append((k + 1, message))
                        elif shell_line is not None:
                            out.append((k + 1, shell_line))
                    elif cm.group("key") == "working-directory":
                        wd = (cm.group("val") or "").strip()
                        accepted_wd = bool(wd) and not wd.startswith("#") and (
                            wd[:1].isalnum() or wd[:1] in ("/", ".", "_", "'"))
                        if accepted_wd:
                            accepted_wd = _decode_scalar(wd)[1] is None
                        if not accepted_wd:
                            unsupported.append((k + 1, "a working-directory value outside the "
                                                       "supported scalar grammar"))
                    else:
                        unsupported.append((k + 1, "a run: mapping key outside the supported set "
                                                   "(shell, working-directory), outside the "
                                                   "supported line grammar"))
                    k += 1
                i = k
                continue
            if SPACED_KEY_RE.match(nval):
                # YAML reads a `key :` line under an empty run: as a MAPPING (a spaced key is
                # legal YAML), never as the plain-scalar command text the branch below would
                # read: a `shell : bash` under defaults: run: used to be read as a command line
                # and passed clean. The spaced-key spelling is outside the mapping grammar
                # above, so it is cannot-evaluate, never a misread.
                unsupported.append((j + 1, "a key with whitespace before its colon on the line "
                                          "after an empty run: (YAML reads the run: value as a "
                                          "mapping), outside the supported line grammar"))
                i = j + 1
                continue
            if BLOCK_HEADER_RE.match(nval):
                if nval[:1] == ">":
                    # A folded header: consume its would-be content (bounded by the key column) so
                    # the lines are never misread as free-standing keys, and refuse the header.
                    k = j + 1
                    while k < n:
                        content = lines[k]
                        if content.strip() == "":
                            k += 1
                            continue
                        if len(content) - len(content.lstrip(" ")) <= m.start("key"):
                            break
                        k += 1
                    unsupported.append((j + 1, "a folded (>) run: block scalar (YAML folds its "
                                              "content into one command; folding is not implemented "
                                              "here), outside the supported line grammar"))
                    i = k
                    continue
                k = j + 1
                while k < n and lines[k].strip() == "":
                    k += 1
                if k >= n or len(lines[k]) - len(lines[k].lstrip(" ")) <= m.start("key"):
                    unsupported.append((j + 1, "block header on the line after an empty run: has no "
                                              "content deeper than the key, outside the supported "
                                              "line grammar"))
                    i = j + 1
                    continue
                if lines[k].lstrip().startswith("#"):
                    unsupported.append((k + 1, "line opening with '#' directly after the block header "
                                              "after an empty run: (YAML reads it as block content; "
                                              "this line reader refuses rather than reads it), "
                                              "outside the supported line grammar"))
                    i = j + 1
                    continue
                if len(lines[k]) - len(lines[k].lstrip(" ")) <= nxt_indent:
                    unsupported.append((k + 1, "block content after an empty run: is not deeper than "
                                              "its header, outside the supported line grammar"))
                    i = j + 1
                    continue
                while k < n:
                    content = lines[k]
                    if content.strip() == "":
                        k += 1
                        continue
                    if len(content) - len(content.lstrip(" ")) <= m.start("key"):
                        break
                    out.append((k + 1, content))
                    k += 1
                i = k
                continue
            if nval[:1] in ("|", ">"):
                unsupported.append((j + 1, "run: value {!r} on the line after an empty run: starts like "
                                          "a YAML block scalar but its header is outside the "
                                          "block-header grammar".format(nval)))
                i = j + 1
                continue
            if nval[:1] in NODE_PROPERTY_LEADS:
                unsupported.append((j + 1, "run: value {!r} on the line after an empty run: opens with a "
                                          "YAML node property or alias, outside the supported line "
                                          "grammar".format(nval)))
                i = j + 1
                continue
            if nval[:1] in ("{", "["):
                unsupported.append((j + 1, "run: value on the line after an empty run: is a flow "
                                          "collection, outside the supported scalar grammar"))
                i = j + 1
                continue
            # The CLOSED line grammar also governs this next-line value BEFORE it is read
            # as command text: YAML reads a mapping key of ANY spelling ('foo.bar : bash',
            # '123: bash'; the plain identifier spelling is the defaults.run mapping
            # above), an explicit-key indicator ('? shell') and a '- ' sequence item
            # ('- shell: bash') there as a MAPPING or SEQUENCE value, never as the
            # plain-scalar command text the branch below would read: each is cannot-evaluate,
            # never a misread.
            if nval == "-" or (nval[:1] == "-" and nval[1:2] in (" ", "\t")):
                unsupported.append((j + 1, "a sequence item on the line after an empty run: "
                                          "(YAML reads the run: value as a sequence), outside "
                                          "the supported line grammar"))
                i = j + 1
                continue
            if nval == "?" or (nval[:1] == "?" and nval[1:2] in (" ", "\t")):
                unsupported.append((j + 1, "an explicit-key indicator on the line after an "
                                          "empty run: (YAML reads the run: value as a "
                                          "mapping), outside the supported line grammar"))
                i = j + 1
                continue
            if nval[:1] not in ("'", '"'):
                # The mapping-indicator test reads ONLY an UNQUOTED plain value's OPERATIVE text,
                # the part before its ` #` trailing comment (YAML strips that comment from a
                # plain scalar exactly where _strip_comment does, so `echo ok # note: harmless`
                # is the plain scalar `echo ok`, never a mapping). A WHOLE quoted scalar is a
                # SCALAR whatever its body carries (`'echo foo: bar'`), so it is never
                # mapping-tested: it goes to _decode_scalar below, which decodes the whole
                # single-quoted form and refuses every other quoted spelling (a double-quoted
                # value, a quote escape, trailing text), exactly as on the key line. KEPT
                # over-refusal: a plain operative text carrying `: `/`:<TAB>` or ending with a
                # colon is refused with the mapping diagnostic even where YAML would reject the
                # text as malformed rather than read a mapping from it.
                operative = re.split(r"[ \t]#", nval, maxsplit=1)[0].rstrip()
                if ": " in operative or ":\t" in operative or operative.endswith(":"):
                    unsupported.append((j + 1, "a mapping key (of any spelling) on the line after "
                                              "an empty run: (YAML reads the run: value as a "
                                              "mapping), outside the supported line grammar"))
                    i = j + 1
                    continue
            k = j + 1
            while k < n and lines[k].strip() == "":
                k += 1
            if k < n:
                follow = lines[k]
                if (len(follow) - len(follow.lstrip(" ")) > m.start("key")
                        and not follow.lstrip().startswith("#")):
                    unsupported.append((k + 1, "run: plain scalar continues on a deeper-indented line, "
                                              "outside the supported line grammar"))
            decoded, refused = _decode_scalar(nval)
            if refused is not None:
                unsupported.append((j + 1, refused))
            else:
                out.append((j + 1, decoded))
            i = j + 1
            continue
        if BLOCK_HEADER_RE.match(val):
            if val[:1] == ">":
                # A folded header: consume its would-be content (bounded by the key column) so the
                # lines are never misread as free-standing keys, and refuse the header.
                j = i + 1
                while j < n:
                    content = lines[j]
                    if content.strip() == "":
                        j += 1
                        continue
                    if len(content) - len(content.lstrip(" ")) <= m.start("key"):
                        break
                    j += 1
                unsupported.append((i + 1, "a folded (>) run: block scalar (YAML folds its content "
                                          "into one command; folding is not implemented here), "
                                          "outside the supported line grammar"))
                i = j
                continue
            # The block is bounded by the KEY column (m.start("key")), exactly as the empty-value
            # path's block below is, never by the item dash's indent: a sibling step key after a
            # dashed `- run: |` block sits at the key column and must re-enter the step-key and
            # line-form rules, not be consumed as block content.
            j = i + 1
            while j < n:
                content = lines[j]
                if content.strip() == "":
                    j += 1
                    continue
                if len(content) - len(content.lstrip(" ")) <= m.start("key"):
                    break
                out.append((j + 1, content))
                j += 1
            i = j
            continue
        if val[:1] in ("|", ">"):
            unsupported.append((i + 1, "run: value {!r} starts like a YAML block scalar but its header is "
                                       "outside the block-header grammar".format(val)))
            i += 1
            continue
        if val[:1] in NODE_PROPERTY_LEADS:
            unsupported.append((i + 1, "run: value {!r} opens with a YAML node property or alias, outside "
                                       "the supported line grammar".format(val)))
            i += 1
            continue
        if val[:1] in ("{", "["):
            unsupported.append((i + 1, "run: value is a flow collection, outside the supported "
                                       "scalar grammar"))
            i += 1
            continue
        if val:
            j = i + 1
            while j < n and lines[j].strip() == "":
                j += 1
            if j < n:
                nxt = lines[j]
                if (len(nxt) - len(nxt.lstrip(" ")) > m.start("key")
                        and not nxt.lstrip().startswith("#")):
                    unsupported.append((j + 1, "run: plain scalar continues on a deeper-indented line, "
                                               "outside the supported line grammar"))
            decoded, refused = _decode_scalar(val)
            if refused is not None:
                unsupported.append((i + 1, refused))
            else:
                out.append((i + 1, decoded))
        i += 1
    return out, unsupported


def _check_hooks_json(rel, text, errors, failures):
    """Scan a plugin hooks.json or a Claude settings.json hooks block. Each command-type hook carries
    either an `args` list (the plugin form: argv is command + args, one command) or a shell-string
    `command` (the settings form: a full shell string that may chain several commands, so it is
    segment-split and every python launcher segment is checked). Fail-closed on malformed JSON or a
    malformed hook entry."""
    try:
        obj = json.loads(text)
    except ValueError as exc:
        errors.append((rel, 0, "", "malformed JSON: {}".format(exc)))
        return
    hooks = obj.get("hooks") if isinstance(obj, dict) else None
    if not isinstance(hooks, dict):
        errors.append((rel, 0, "", "missing or malformed top-level 'hooks' object"))
        return
    for event in sorted(hooks):
        entries = hooks[event]
        if not isinstance(entries, list):
            errors.append((rel, 0, event, "event value is not a list"))
            continue
        for idx, entry in enumerate(entries):
            loc = "{}[{}]".format(event, idx)
            if not isinstance(entry, dict):
                errors.append((rel, 0, loc, "hook group is not an object"))
                continue
            items = entry.get("hooks")
            if not isinstance(items, list):
                errors.append((rel, 0, loc, "hook group has no 'hooks' list"))
                continue
            for hidx, item in enumerate(items):
                hloc = "{}.hooks[{}]".format(loc, hidx)
                if not isinstance(item, dict):
                    errors.append((rel, 0, hloc, "hook entry is not an object"))
                    continue
                htype = item.get("type")
                if htype is None:
                    errors.append((rel, 0, hloc, "hook entry has no 'type'"))
                    continue
                if htype != "command":
                    continue  # a non-command hook has no python launcher; out of scope
                command = item.get("command")
                if not isinstance(command, str) or not command:
                    errors.append((rel, 0, hloc, "command must be a non-empty string"))
                    continue
                if "args" in item:
                    # The plugin form: command + args is exactly one argv, so classify it directly.
                    args = item.get("args")
                    if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
                        errors.append((rel, 0, hloc, "args must be a list of strings"))
                        continue
                    argv = [command] + args
                    verdict = check_argv(argv)
                    if verdict is False:
                        failures.append((rel, 0, hloc, "non-isolated launcher {!r}; requires {}"
                                         .format(" ".join(argv), REQUIRED_FORM)))
                else:
                    # The settings form: a shell string that may chain commands (e.g. `prep && python3
                    # x.py`), so segment-split and check every launcher segment, not just the first.
                    try:
                        pairs = _shell_split_pairs(command)
                    except ValueError as exc:
                        errors.append((rel, 0, hloc, "command string does not parse: {}".format(exc)))
                        continue
                    _scan_command_tokens(rel, 0, hloc, pairs, command, errors, failures)


def _candidate_mode(path):
    """No-follow (lstat) mode probe for a workflow candidate. A module-level seam the self-test patches
    to exercise the non-regular-candidate branch deterministically."""
    return path.lstat().st_mode


def _read_file_text(path):
    """UTF-8 file read. A module-level seam the self-test patches to exercise the unreadable-input
    branch (exit 2) deterministically where chmod 0 cannot bite (a DAC-bypassing runner)."""
    return path.read_text(encoding="utf-8")


def _read_required_text(root, rel, errors):
    """Read a required-surface file fail-closed as UTF-8 text; return the text, or None after recording a
    cannot-evaluate. A missing, unreadable, non-regular (S_ISREG must hold; /dev/null and the like are
    cannot-evaluate, never a clean empty read), or non-UTF-8 input is a failure, per the check-fails-closed
    rule. This governs both the required surfaces and a present optional settings file, whose present-but-
    unusable state is likewise a failure rather than a silent skip."""
    path = root / rel
    try:
        mode = path.stat().st_mode
    except OSError as exc:
        errors.append((rel, 0, "", "cannot read required input: {}".format(exc)))
        return None
    if not stat.S_ISREG(mode):
        errors.append((rel, 0, "", "required input is not a regular file"))
        return None
    try:
        return _read_file_text(path)
    except (OSError, UnicodeError) as exc:
        errors.append((rel, 0, "", "cannot read required input: {}".format(exc)))
        return None


def _scan_json_file(root, rel, errors, failures):
    """Read a required JSON launcher file and scan it. Missing/unreadable/non-regular/non-UTF-8 ->
    cannot-evaluate."""
    text = _read_required_text(root, rel, errors)
    if text is None:
        return
    _check_hooks_json(rel, text, errors, failures)


def _scan_shell_file(root, rel, errors, failures):
    """Read a required shell file and scan each line. Missing/unreadable/non-regular/non-UTF-8 ->
    cannot-evaluate."""
    text = _read_required_text(root, rel, errors)
    if text is None:
        return
    for lineno, line in enumerate(text.splitlines(), start=1):
        _check_shell_line(rel, lineno, line, errors, failures)


def _scan_workflows(root, errors, failures):
    """Scan every *.yml/*.yaml candidate under .github/workflows/. The directory is required: an
    unreadable or absent directory is a cannot-evaluate, never an empty clean scan. Every candidate is
    classified with an error-preserving, NO-FOLLOW probe (_candidate_mode, lstat): a regular file is read
    and scanned; anything else -- a symlink whether dangling, looping or resolvable (none is followed), a
    directory, an unreadable entry, or any other non-regular entry -- is a named cannot-evaluate
    (exit 2), never a silent skip."""
    wf_dir = root / ".github" / "workflows"
    try:
        names = sorted(os.listdir(wf_dir))
    except OSError as exc:
        errors.append((WORKFLOWS_REL, 0, "", "cannot list required workflows directory: {}".format(exc)))
        return
    for name in names:
        if not name.endswith((".yml", ".yaml")):
            continue
        rel = WORKFLOWS_REL + "/" + name
        path = wf_dir / name
        try:
            mode = _candidate_mode(path)
        except OSError as exc:
            errors.append((rel, 0, "", "cannot probe workflow candidate: {}".format(exc)))
            continue
        if stat.S_ISLNK(mode):
            errors.append((rel, 0, "", "workflow candidate is a symlink (not followed); replace it with "
                           "a regular file"))
            continue
        if not stat.S_ISREG(mode):
            errors.append((rel, 0, "", "workflow candidate is not a regular file"))
            continue
        try:
            text = _read_file_text(path)
        except (OSError, UnicodeError) as exc:
            errors.append((rel, 0, "", "cannot read workflow: {}".format(exc)))
            continue
        run_lines, unsupported = _yaml_run_lines(text)
        for lineno, message in unsupported:
            errors.append((rel, lineno, "", message))
        for lineno, line in run_lines:
            _check_shell_line(rel, lineno, line, errors, failures)


def _scan_pysource_isolation(root, errors, failures):
    """Scan the QA-suite Python sources for a sys.path insertion at index 0 that would place the script's
    own directory AHEAD of the stdlib on sys.path. Under `python3 -I` such a reinsertion re-enables the
    sibling-shadow class this gate exists to prevent: a sibling json.py/hashlib.py beside the script can
    then shadow a stdlib import and silently neuter the control. The sanctioned form for these sources is
    sys.path.append (stdlib precedence preserved). Each source is REQUIRED and read fail-closed; a
    reintroduced index-0 insertion is a non-isolated finding. RESIDUAL: this is a line-lexical scan
    (mirroring the roster-scan limit) that strips a trailing/whole-line shell-style `#` note is NOT used
    here (Python comments are stripped by cutting at the first `#`), so an index-0 insertion spelled
    differently (a sys.path[0:0] slice or a computed index), one assembled at runtime, or one hidden inside
    a string literal or a heredoc is outside it."""
    for rel in PYSOURCE_ISOLATION_REL:
        text = _read_required_text(root, rel, errors)
        if text is None:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            code = line.split("#", 1)[0]  # drop a Python comment so a phrase in a comment is not flagged
            if SYS_PATH_FRONT_INSERT_RE.search(code):
                failures.append((rel, lineno, "", "a sys.path insertion at index 0 reintroduces the script "
                                 "directory ahead of the stdlib (a sibling-shadow risk under -I); use "
                                 "sys.path.append"))


def scan(root):
    """Scan every declared surface under root; return (errors, failures) as sorted diagnostic tuples."""
    errors, failures = [], []
    _scan_json_file(root, HOOKS_JSON_REL, errors, failures)
    _scan_shell_file(root, RUN_ALL_REL, errors, failures)
    _scan_workflows(root, errors, failures)
    _scan_pysource_isolation(root, errors, failures)
    for rel in OPTIONAL_SETTINGS:
        # Optional: a TRULY-ABSENT settings file (no filesystem entry at all) is the only clean
        # absence; a PRESENT-but-unusable one (a dangling symlink, or an unreadable, non-regular,
        # non-UTF-8, or malformed file) is a cannot-evaluate, per the check-fails-closed rule. The
        # presence test uses lstat (via _present) so a present-but-dangling symlink counts as present
        # and is routed to fail-closed, not read through the link as absent.
        if _present(root / rel):
            _scan_json_file(root, rel, errors, failures)
    errors.sort()
    failures.sort()
    return errors, failures


def run(root):
    """Scan and report. Exit 2 on any cannot-evaluate, else 1 on any non-isolated launcher, else 0."""
    errors, failures = scan(root)
    for rel, lineno, loc, msg in errors:
        where = "{}:{}".format(rel, lineno) if lineno else rel
        if loc:
            where = "{} {}".format(where, loc)
        print("cannot-evaluate: {}: {}".format(where, msg))
    for rel, lineno, loc, msg in failures:
        where = "{}:{}".format(rel, lineno) if lineno else rel
        if loc:
            where = "{} {}".format(where, loc)
        print("FAIL: {}: {}".format(where, msg))
    if errors:
        print("RESULT: cannot-evaluate ({} issue(s)); fail-closed".format(len(errors)))
        return 2
    if failures:
        print("RESULT: {} non-isolated launcher(s)".format(len(failures)))
        return 1
    print("PASS: every recognized python launcher in the scanned surfaces runs isolated")
    return 0


def _repo_root():
    p = Path(__file__).resolve()
    for anc in [p, *p.parents]:
        if (anc / ".git").exists():
            return anc
    return Path.cwd()


def main():
    if "--self-test" in sys.argv[1:]:
        return self_test_main()
    return run(_repo_root())


# --- self-test ----------------------------------------------------------------------------------------
# Proves the gate's behaviour against synthetic trees and one real subprocess:
#   1. the hostile-sibling MECHANISM: a probe importing json is neutered by a sibling json.py under a
#      bare interpreter and clean under `-I` (the witnessed fail-to-pass transition for the class),
#   2. a fully-isolated tree passes (exit 0),
#   3. a bare python3 launcher in hooks.json fails (exit 1),
#   4. the -I, -PEs, and -P -E -s forms each pass,
#   5. a partial -P -E triple fails (exit 1),
#   6. options placed AFTER the script are not credited (exit 1),
#   7. malformed JSON, a non-list args, and an unreadable required input each fail closed (exit 2;
#      the unreadable case injects the read error through a module seam where chmod 0 cannot bite, so
#      it runs on every runner),
#   8. the run_gate shell grammar, an inline `run:` scalar, and a `run: |` block are all recognized
#      (a bare launcher in each fails, its isolated form passes),
#   9. an optional settings.json shell-string command is recognized (bare fails, -I passes),
#  10. the gate REFUSES (exit 2) when its own interpreter is not isolated (a real subprocess),
#  11-13. a value-taking option's value is never letter-scanned (-Wignore::ImportWarning is not
#      isolated; -W ignore -I -B and -Xfoo -I are), so isolation is neither forged nor missed,
#  14. a settings.json shell-string that chains commands catches a launcher in a later segment,
#  15-18. a required surface that is non-regular (/dev/null) or non-UTF-8, and a PRESENT optional
#      settings file that is non-regular or non-UTF-8, each fail closed (exit 2),
#  19-21. a launcher glued to a punctuation-adjacent operator (`echo prep;python3`, `&& (python3`),
#      unspaced, is still segmented and caught in both a settings shell-string and a run_all_checks.sh
#      shell line (exit 1), matching real bash where shlex alone would miss it,
#  22-23. a PRESENT-but-dangling optional settings symlink fails closed (exit 2) while a truly-absent
#      optional settings file stays a clean skip (exit 0),
#  24. an attached -m/-c operand (-mIfoo, -cIbar) terminates option scanning so its letters never
#      forge isolation (exit 1), confirmed against a real interpreter for the -c case,
#  25-27. an `&`-carrying REDIRECT (`>&python3`, `2>&1`, `>&2`, `&>/dev/null`) is not mis-split into a
#      fabricated bare launcher: an isolated launcher with such a redirect passes (exit 0), and a
#      non-isolated one fails once on the REAL launcher, never on the redirect target,
#  28. the genuine separators are not loosened: a non-isolated launcher after a real `&&` or a real
#      background `&` is still segmented and caught (exit 1),
#  29. real bash confirms `>&python3` is a redirect to a file named python3, run isolated (the
#      fail-to-pass witness for the redirect-aware `&` split).
#  30. a quote/escape-obfuscated launcher NAME in a run_all_checks.sh shell line (`pyt\hon3`,
#      `pyt"hon"3`, `py'thon'3`), which bash resolves to a real non-isolated python, is caught
#      (exit 1), its isolated `pyt\hon3 -I` form passes (exit 0), and a plain `echo hello` line stays
#      clean; this is the shell-line-path bug the fix closes (the raw prefilter used to miss it).
#  31. the same obfuscated NAMES in a settings.json shell-string are caught (exit 1): a regression
#      guard proving the settings path (which already decoded first) still agrees with the shell path.
#  32. real bash confirms the obfuscated names `pyt\hon3`, `pyt"hon"3`, `py'thon'3` all resolve to the
#      token `python3` (the fail-to-pass witness for the decode-then-decide shell-line fix).
#  33. a QA-suite Python source reintroducing a sys.path index-0 insertion is a finding (exit 1).
#  34. a `run:` LITERAL block header in the full block-header grammar (`| # comment`, `|2`, `|2-`),
#      each with the content indentation its indicator requires, is read as a block: a bare launcher
#      in its content fails (exit 1) and the isolated form under `| # comment` passes (exit 0);
#      34b. EVERY folded (`>`) header (`>`, `>+9`, `>-2 # note`, and one on the line after an empty
#      run:) is cannot-evaluate (exit 2, naming the header line): YAML folds the content into one
#      command and so changes shell comment boundaries (a leading `# note` line folds onto one
#      line with the content after it and comments out the launcher that follows; a later `#`
#      content line folds into mid-command text), so a line-by-line read can disagree with the
#      decoded value; the `>+9` and `>-2 # note` forms were read line-by-line before this rule,
#  35. any other `run:` value starting with `|` or `>` (`|0`, `|#x`, `|2x`, `|-+`, `>>`), a value opening
#      with a node property or alias (`&a |`, `!!str |`, `*a`), and a plain scalar continued onto a
#      deeper-indented line are cannot-evaluate (exit 2), while a sibling key at the run: key column after
#      an inline scalar stays clean (exit 0),
#  36. every *.yml workflow candidate is classified with a no-follow, error-preserving probe: a
#      dangling, looping or resolvable symlink (none followed; a readable, CLEAN target included, the
#      vector that discriminates no-follow on a non-root runner), a directory, and an injected
#      non-regular mode are each a named cannot-evaluate (exit 2),
#  37. an empty run: value -- a bare `run:`, or a `run: # note` whose only same-line text is a YAML
#      comment (YAML strips it to the SAME empty value; proven against PyYAML) -- is read ONLY in the
#      two proven shapes (a next-line plain scalar; a next-line block header whose content is deeper
#      than both the header and the key): each carries a bare launcher (exit 1) or an isolated one
#      (exit 0); an anchored value, a malformed header, a further continuation, a comment line before
#      a deeper value (after a bare `run:` or a comment-only value alike), a header with no content
#      deeper than the key (a header at the end of the text included) or with content at or above its
#      own column, and a line opening with '#' directly after the header (YAML block content this
#      reader refuses, at the header's column or deeper) are cannot-evaluate (exit 2), each naming
#      .github/workflows/quality.yml and the line; a null run: (next non-blank, non-comment line not
#      deeper than the key and not a sequence item at the key's own column) carries no command text
#      (exit 0), a comment line alone never making it one,
#  38. the run: VALUE grammar is closed and the key/step grammar is POSITIVE: a single-quoted scalar
#      with no escape decodes to its body (a bare launcher there fails, an isolated one passes); a
#      quoted scalar followed by a trailing comment, a double-quoted scalar (escapes undecoded:
#      `\n`, `\x70`, `\u0074`, `\t`), a single-quoted scalar with a `''` escape or no closing
#      quote, a flow mapping or flow sequence step or value, a run key inside a flow collection, a
#      quoted run key (escaped spellings that YAML decodes to `run` included), a tagged, anchored
#      or aliased run key, an explicit-key marker, a run key with whitespace before its colon, a
#      plain step key outside the closed step-key set, and a flow line under `with:` are each
#      cannot-evaluate (exit 2), naming .github/workflows/quality.yml and the line; the
#      single-quoted-escape, unterminated-quote and flow-value rows also assert the YAML value
#      reader's own diagnostic (the parent reader exited 2 there through the downstream shell
#      parse, with a different message), the flow-line and next-line-flow-value rows pin
#      UNSUPPORTED_LINE_FORMS[1] and the empty-run flow branch, and a step whose keys are all on
#      the allowlist stays clean (exit 0); before this rule the quoted/escaped forms passed with
#      their launcher unread and the off-grammar key spellings were silently skipped.
#  39. a `- run: |` block ends at the KEY COLUMN, and every `shell:` value is READ: a quoted
#      "shell" key and an unknown timeout key after a dashed literal block are cannot-evaluate
#      (exit 2; they were consumed as block content and the tree passed clean), a second run: key
#      there is a finding (exit 1), and an env: folded VALUE after the block is clean (exit 0; it
#      was a false launcher finding); `shell: python {0}` on a step and under defaults.run at the
#      job and workflow level is a non-isolated launcher finding (exit 1; a bare `run:` whose
#      deeper next line is a plain `key:` line is read as the defaults.run MAPPING, shell and
#      working-directory keys only), `shell: bash` and a two-key defaults.run mapping stay clean
#      (exit 0), a python template claiming isolation options and every other shell: value the
#      closed classification cannot place (a non-shell template, a double-quoted value, an
#      unknown mapping key) are cannot-evaluate (exit 2), and the quoted-key and node-property
#      line forms refuse a key under ANY number of leading sequence dashes (exit 2; one level of
#      `- - ` nesting used to pass both unread).
#  40. the CLOSED line grammar: a flow MAPPING value under any key (an escaped, tagged or
#      explicit-key run spelling inside it included) is cannot-evaluate, a `shell : bash` spaced
#      key under `defaults: run:` at the workflow and job level is cannot-evaluate (the
#      plain-scalar path used to read it as command text and pass it clean), a nested `- - `
#      entry (a plain run: or shell: key there used to be skipped unread) and its flow-step form
#      are cannot-evaluate, `branches: [main]` (a flow sequence of plain scalars, the live
#      workflows' only flow shape) stays clean, a single-quoted `shell: 'bash'` and a
#      `shell: bash` with a deeper continuation line are cannot-evaluate, and the `- run: >`
#      folded consumer's KEY-column bound is pinned by a sibling quoted-key diagnostic that
#      disappears when the bound reverts to the dash indent; the value on the line after an
#      empty run: is governed by the SAME closed grammar (a dotted `foo.bar : bash` and a
#      numeric `123: bash` mapping key, a `? shell` explicit-key indicator and a
#      `- shell: bash` sequence item each used to be read as command text and pass clean;
#      the mapping test reads ONLY an unquoted plain value's text before its ` #` comment,
#      so a whole single-quoted `'echo foo: bar'` and a `: ` inside a trailing comment are
#      clean scalars; a sequence item AT THE KEY'S OWN COLUMN, comment-separated included,
#      used to be read as NULL and pass clean and is now cannot-evaluate, a lone `-` and a
#      `-`+TAB item there each asserting that same-column diagnostic; a quoted or
#      escaped leading keyword, `i'f'`/`"then"`/`d\o`/`t'ime'`/`'!'`, is a literal
#      command name, so a bare launcher after it is a cannot-evaluate, never a finding; a
#      quoted `';'` after `i'f'` is an argument, never a separator, so the bare launcher
#      after it (a finding when it split) and its `-I` form (clean then) are each a
#      cannot-evaluate; and a quoted `'X=y'` is a command name, never an assignment, so the
#      bare launcher after it and its `-I` form (clean when matched decoded) are each a
#      cannot-evaluate, while `X="a b"` and an `env 'X=y'` argument still skip, pinned by
#      their exit-1 findings; each of those cannot-evaluate rows also asserts, on the
#      structured scan() result, that no finding is emitted), a
#      shell: keyword is the literal comment-stripped scalar (`ba'sh'`, `ba"sh"`, `ba\sh` and
#      `b''ash`, at a step and under defaults.run, each used to shell-decode to bash and pass
#      clean), and the remaining generic checks are each pinned by a row of their own: the
#      colon-carrying flow sequence (_flow_sequence_value_ok), the generic node-property value,
#      the generic malformed block header, the generic spaced key's own diagnostic, and the
#      generic block-scalar KEY-column bound (a sibling run: after `- name: |` inside steps and
#      after `- y: |` outside steps is a line-named finding, exit 1, never consumed content).
#  41. the settings shell-string caller agrees with the workflow rows: `i'f' ';' python3 x.py`,
#      its `-I` form and `'X=y' python3 -I x.py` in a settings.json command are each a
#      cannot-evaluate with no finding; and both raw/decoded consistency guards in
#      _shell_split_pairs refuse a disagreement injected through the _decode_tokens seam (an
#      extra decoded token, and a same-count altered token), each through a run_all_checks.sh
#      line and a settings.json command, with its own guard's message and no finding.

SCRIPT = "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/aiqt_hooks.py"

_ISO_RUN_ALL = """#!/usr/bin/env bash
set -uo pipefail
run_gate "alpha" python3 -I -B tools/alpha.py
run_gate "beta" env PYTHONDONTWRITEBYTECODE=1 python3 -I -B tools/beta.py --check
"""

_ISO_WORKFLOW = """name: Quality
jobs:
  quality:
    steps:
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      - name: inline
        run: python3 -I -B tools/alpha.py
      - name: block
        run: |
          set -euo pipefail
          python3 -I -B tools/beta.py --check
"""


def _hooks_json(args):
    obj = {"description": "self-test",
           "hooks": {"PreToolUse": [{"matcher": "Bash",
                                     "hooks": [{"type": "command", "command": "python3",
                                                "args": args, "timeout": 10}]}]}}
    return json.dumps(obj, indent=2) + "\n"


def _build(base, hooks_args=("-I", SCRIPT, "h_one"),
           run_all=_ISO_RUN_ALL, workflow=_ISO_WORKFLOW):
    """A fully-isolated synthetic tree: the plugin hooks.json, run_all_checks.sh, one workflow, and clean
    stubs for the three REQUIRED QA-suite Python sources so the pysource-isolation scan finds them present
    and clean (a case that needs an index-0 insertion overwrites a stub)."""
    hooks_path = base / HOOKS_JSON_REL
    hooks_path.parent.mkdir(parents=True)
    hooks_path.write_text(_hooks_json(list(hooks_args)), encoding="utf-8")
    (base / "tools").mkdir(parents=True)
    (base / RUN_ALL_REL).write_text(run_all, encoding="utf-8")
    for rel in PYSOURCE_ISOLATION_REL:
        (base / rel).write_text("# qa-suite source stub (launcher-isolation coverage)\n", encoding="utf-8")
    wf = base / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "quality.yml").write_text(workflow, encoding="utf-8")
    (base / ".git").mkdir()
    return base


def _close_fd2():
    """preexec_fn for the self-test's closed-stderr refusal case: descriptor 2 is closed in the child
    between fork and exec, so the gate starts with sys.stderr None."""
    os.close(2)


def self_test_main():
    import io
    import shutil
    import subprocess
    import tempfile
    from contextlib import redirect_stdout

    def run_quiet(root):
        with redirect_stdout(io.StringIO()):
            return run(root)

    def run_capture(root):
        out = io.StringIO()
        with redirect_stdout(out):
            rc = run(root)
        return rc, out.getvalue()

    try:
        tmp = Path(tempfile.mkdtemp(prefix="aiqt-launcher-isolation-selftest-"))
    except OSError as exc:
        print("SELF-TEST ERROR: no writable temporary directory: {}".format(exc), file=sys.stderr)
        return 2
    failures = []
    skipped = []
    try:
        # 1. The hostile-sibling MECHANISM: a probe importing json is neutered by a sibling json.py under
        #    a bare interpreter and clean under -I. This is the class this gate exists to prevent.
        mech = tmp / "mech"
        mech.mkdir()
        (mech / "probe.py").write_text("import json\nprint(json.dumps({'ok': 1}))\n", encoding="utf-8")
        (mech / "json.py").write_text("# a hostile sibling: no dumps, so a bare import neuters the probe\n",
                                      encoding="utf-8")
        env = dict(os.environ)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        bare = subprocess.run([sys.executable, str(mech / "probe.py")], capture_output=True,
                              text=True, env=env, timeout=30)
        iso = subprocess.run([sys.executable, "-I", "-B", str(mech / "probe.py")], capture_output=True,
                             text=True, env=env, timeout=30)
        if bare.returncode == 0:
            failures.append("mechanism: a bare interpreter should have been neutered by the sibling json.py")
        if iso.returncode != 0 or '{"ok": 1}' not in iso.stdout:
            failures.append("mechanism: -I should have used the real json (got rc={}, out={!r})"
                            .format(iso.returncode, iso.stdout))

        # A python interpreter name is ASCII: a version written in Arabic-Indic or fullwidth digits
        # names no interpreter.
        for word, want in (("python3.12", True), ("/usr/bin/python3", True),
                           ("python3.١٢", False), ("python3.１２", False)):
            if _is_py_word(word) != want:
                failures.append("the token {} expected python word {}".format(ascii(word), want))

        # 2. A fully-isolated tree passes.
        if run_quiet(_build(tmp / "iso")) != 0:
            failures.append("a fully-isolated tree expected exit 0")

        # 3. A bare python3 launcher in hooks.json fails (exit 1).
        if run_quiet(_build(tmp / "bare", hooks_args=(SCRIPT, "h_one"))) != 1:
            failures.append("a bare python3 launcher in hooks.json expected exit 1")

        # 4. The -I, -PEs, and -P -E -s forms each pass.
        for label, args in (("dash-I", ("-I", SCRIPT, "h_one")),
                            ("cluster-PEs", ("-PEs", SCRIPT, "h_one")),
                            ("separate-P-E-s", ("-P", "-E", "-s", SCRIPT, "h_one"))):
            if run_quiet(_build(tmp / ("ok-" + label), hooks_args=args)) != 0:
                failures.append("the {} isolated form expected exit 0".format(label))

        # 5. A partial -P -E triple (missing -s) fails.
        if run_quiet(_build(tmp / "partial", hooks_args=("-P", "-E", SCRIPT, "h_one"))) != 1:
            failures.append("a partial -P -E launcher expected exit 1 (the full -P -E -s is required)")

        # 6. Options AFTER the script are not credited.
        if run_quiet(_build(tmp / "after", hooks_args=(SCRIPT, "-I", "h_one"))) != 1:
            failures.append("a -I placed after the script expected exit 1 (flags after the script "
                            "are not credited)")

        # 7. Malformed JSON, a non-list args, and an unreadable required input each fail closed (exit 2).
        badjson = _build(tmp / "badjson")
        (badjson / HOOKS_JSON_REL).write_text("{not valid json", encoding="utf-8")
        if run_quiet(badjson) != 2:
            failures.append("malformed hooks.json expected exit 2 (fail-closed)")
        badargs = _build(tmp / "badargs")
        (badargs / HOOKS_JSON_REL).write_text(
            '{"hooks": {"PreToolUse": [{"hooks": [{"type": "command", "command": "python3", '
            '"args": "-I"}]}]}}\n', encoding="utf-8")
        if run_quiet(badargs) != 2:
            failures.append("a non-list args expected exit 2 (fail-closed)")
        unread = _build(tmp / "unread")
        target = unread / RUN_ALL_REL
        os.chmod(target, 0)
        module = sys.modules[__name__]
        try:
            if os.access(target, os.R_OK):
                # chmod 0 cannot bite (root or CAP_DAC_OVERRIDE): inject the read error through the
                # module seam instead, so the guarded exit-2 observation is made on every runner.
                saved_read = module._read_file_text
                def _deny_read(path, _real=saved_read, _t=target):
                    if Path(path) == _t:
                        raise PermissionError(13, "injected permission denied", str(path))
                    return _real(path)
                module._read_file_text = _deny_read
                try:
                    rc = run_quiet(unread)
                finally:
                    module._read_file_text = saved_read
            else:
                rc = run_quiet(unread)
        finally:
            os.chmod(target, 0o644)
        if rc != 2:
            failures.append("an unreadable required input expected exit 2 (fail-closed)")

        # 8. The run_gate grammar, an inline run: scalar, and a run: | block are all recognized: a bare
        #    launcher in each surface fails, and the isolated baseline passes (case 2 already proved pass).
        bare_sh = _build(tmp / "bare-sh",
                         run_all='#!/usr/bin/env bash\nrun_gate "alpha" python3 tools/alpha.py\n')
        if run_quiet(bare_sh) != 1:
            failures.append("a bare run_gate launcher in run_all_checks.sh expected exit 1")
        bare_inline = _build(tmp / "bare-inline",
                             workflow="name: Q\njobs:\n  q:\n    steps:\n"
                                      "      - run: python3 tools/alpha.py\n")
        if run_quiet(bare_inline) != 1:
            failures.append("a bare inline run: launcher expected exit 1")
        bare_block = _build(tmp / "bare-block",
                            workflow="name: Q\njobs:\n  q:\n    steps:\n"
                                     "      - run: |\n          set -e\n          python3 tools/alpha.py\n")
        if run_quiet(bare_block) != 1:
            failures.append("a bare run: | block launcher expected exit 1")

        # 9. An optional settings.json shell-string command is recognized (bare fails, -I passes).
        settings_bare = _build(tmp / "settings-bare")
        sp = settings_bare / ".claude" / "settings.json"
        sp.parent.mkdir(parents=True)
        sp.write_text('{"hooks": {"PreToolUse": [{"hooks": [{"type": "command", '
                      '"command": "python3 tools/x.py"}]}]}}\n', encoding="utf-8")
        if run_quiet(settings_bare) != 1:
            failures.append("a bare shell-string command in settings.json expected exit 1")
        settings_ok = _build(tmp / "settings-ok")
        sp = settings_ok / ".claude" / "settings.json"
        sp.parent.mkdir(parents=True)
        sp.write_text('{"hooks": {"PreToolUse": [{"hooks": [{"type": "command", '
                      '"command": "python3 -I tools/x.py"}]}]}}\n', encoding="utf-8")
        if run_quiet(settings_ok) != 0:
            failures.append("an isolated shell-string command in settings.json expected exit 0")

        # 11. A value-taking option's VALUE is never letter-scanned: -Wignore::ImportWarning is NOT
        #     isolated, so the 'I' inside "ImportWarning" must not forge isolation (exit 1).
        if run_quiet(_build(tmp / "wvalue",
                            hooks_args=("-Wignore::ImportWarning", SCRIPT, "h_one"))) != 1:
            failures.append("-Wignore::ImportWarning must not be read as isolated (expected exit 1)")

        # 12. A separate value (-W ignore) is skipped, not treated as the script, so a real -I after it is
        #     still credited: `-W ignore -I -B` IS isolated (exit 0).
        if run_quiet(_build(tmp / "wsep",
                            hooks_args=("-W", "ignore", "-I", "-B", SCRIPT, "h_one"))) != 0:
            failures.append("-W ignore -I -B must be read as isolated (expected exit 0)")

        # 13. An attached -X value is not letter-scanned but a following -I is credited: `-Xfoo -I` IS
        #     isolated (exit 0).
        if run_quiet(_build(tmp / "xvalue", hooks_args=("-Xfoo", "-I", SCRIPT, "h_one"))) != 0:
            failures.append("-Xfoo -I must be read as isolated (expected exit 0)")

        # 14. A settings.json shell-string that chains commands is segment-split: a launcher in a LATER
        #     segment (`echo a && python3 tools/x.py`) is caught, not just the first command (exit 1).
        chain = _build(tmp / "settings-chain")
        sp = chain / ".claude" / "settings.json"
        sp.parent.mkdir(parents=True)
        sp.write_text('{"hooks": {"PreToolUse": [{"hooks": [{"type": "command", '
                      '"command": "echo a && python3 tools/x.py"}]}]}}\n', encoding="utf-8")
        if run_quiet(chain) != 1:
            failures.append("a chained shell-string command must catch the second-segment launcher "
                            "(expected exit 1)")

        # 15. A required surface that is a non-regular file is cannot-evaluate: read_text on /dev/null
        #     yields an empty string, so without the regular-file guard it would read as a clean empty
        #     scan; the guard makes it exit 2. Skipped where /dev/null is unavailable.
        devnull = Path("/dev/null")
        if not _exists(devnull) or devnull.is_file():
            skipped.append("15 non-regular-required (/dev/null unavailable)")
        else:
            nonreg = _build(tmp / "nonreg-required")
            target = nonreg / RUN_ALL_REL
            target.unlink()
            os.symlink(str(devnull), str(target))
            if run_quiet(nonreg) != 2:
                failures.append("a non-regular required input (/dev/null) expected exit 2 (fail-closed)")

        # 16. A required surface that is not valid UTF-8 is cannot-evaluate (a decode error must map to
        #     exit 2, never an uncaught traceback).
        nonutf = _build(tmp / "nonutf-required")
        (nonutf / RUN_ALL_REL).write_bytes(b"#!/usr/bin/env bash\n\xff\xfe python3 -I tools/a.py\n")
        if run_quiet(nonutf) != 2:
            failures.append("a non-UTF-8 required input expected exit 2 (fail-closed)")

        # 17. A PRESENT optional settings file that is not valid UTF-8 is cannot-evaluate: present-but-
        #     unusable is a failure, not a clean skip (exit 2).
        opt_nonutf = _build(tmp / "opt-nonutf")
        sp = opt_nonutf / ".claude" / "settings.json"
        sp.parent.mkdir(parents=True)
        sp.write_bytes(b'{"hooks": \xff\xfe}')
        if run_quiet(opt_nonutf) != 2:
            failures.append("a present-but-non-UTF-8 optional settings file expected exit 2")

        # 18. A PRESENT optional settings file that is non-regular is likewise cannot-evaluate (exit 2).
        if not _exists(devnull) or devnull.is_file():
            skipped.append("18 non-regular-optional (/dev/null unavailable)")
        else:
            opt_nonreg = _build(tmp / "opt-nonreg")
            sp = opt_nonreg / ".claude" / "settings.local.json"
            sp.parent.mkdir(parents=True)
            os.symlink(str(devnull), str(sp))
            if run_quiet(opt_nonreg) != 2:
                failures.append("a present-but-non-regular optional settings file expected exit 2")

        # 19. A settings shell-string with a launcher GLUED to a punctuation-adjacent operator (no
        #     space around `;`) is still segmented and the launcher caught, matching real bash: shlex
        #     alone leaves `prep;python3` one token and would miss it (exit 1).
        glue_semi = _build(tmp / "settings-glue-semi")
        sp = glue_semi / ".claude" / "settings.json"
        sp.parent.mkdir(parents=True)
        sp.write_text('{"hooks": {"PreToolUse": [{"hooks": [{"type": "command", '
                      '"command": "echo prep;python3 tools/x.py"}]}]}}\n', encoding="utf-8")
        if run_quiet(glue_semi) != 1:
            failures.append("a semicolon-glued settings launcher (echo prep;python3) expected exit 1")

        # 20. A settings shell-string with a launcher inside a subshell glued to `(` (`echo a &&
        #     (python3 ...)`, no space after `(`) is segmented and caught (exit 1).
        glue_paren = _build(tmp / "settings-glue-paren")
        sp = glue_paren / ".claude" / "settings.json"
        sp.parent.mkdir(parents=True)
        sp.write_text('{"hooks": {"PreToolUse": [{"hooks": [{"type": "command", '
                      '"command": "echo a && (python3 tools/x.py)"}]}]}}\n', encoding="utf-8")
        if run_quiet(glue_paren) != 1:
            failures.append("a subshell-glued settings launcher (&& (python3 ...)) expected exit 1")

        # 21. The same punctuation-adjacent forms in a run_all_checks.sh shell line (which shares the
        #     segment logic) are segmented and caught: a `;`-glued and a `(`-glued bare launcher
        #     (exit 1).
        glue_sh = _build(tmp / "runall-glue-semi",
                         run_all='#!/usr/bin/env bash\necho prep;python3 tools/alpha.py\n')
        if run_quiet(glue_sh) != 1:
            failures.append("a semicolon-glued launcher in run_all_checks.sh expected exit 1")
        glue_sh_paren = _build(tmp / "runall-glue-paren",
                               run_all='#!/usr/bin/env bash\necho a && (python3 tools/alpha.py)\n')
        if run_quiet(glue_sh_paren) != 1:
            failures.append("a subshell-glued launcher in run_all_checks.sh expected exit 1")

        # 22. A PRESENT-but-dangling optional settings symlink (a filesystem entry whose target is
        #     gone) is present-but-unusable -> cannot-evaluate (exit 2), NOT the clean skip a truly-
        #     absent path gets. _exists stat()s THROUGH the link and would read it as absent; the
        #     lstat-based _present probe routes it to fail-closed instead.
        dangling = _build(tmp / "opt-dangling")
        sp = dangling / ".claude" / "settings.json"
        sp.parent.mkdir(parents=True)
        os.symlink(str(dangling / ".claude" / "no-such-target.json"), str(sp))
        if run_quiet(dangling) != 2:
            failures.append("a present-but-dangling optional settings symlink expected exit 2")

        # 23. A TRULY-ABSENT optional settings file (no filesystem entry at all) is a clean skip: the
        #     baseline tree ships neither optional settings file, so it must still pass (exit 0). This
        #     pins the absent-vs-present boundary opposite case 22.
        if run_quiet(_build(tmp / "opt-absent")) != 0:
            failures.append("a tree with truly-absent optional settings expected exit 0 (clean skip)")

        # 24. An ATTACHED -m/-c operand terminates interpreter-option scanning: the letters in -mIfoo
        #     / -cIbar are the module/command operand, not isolation flags, so neither forges
        #     isolation and the (non-isolated) launcher fails (exit 1).
        if run_quiet(_build(tmp / "attached-m", hooks_args=("-mIfoo", "x.py"))) != 1:
            failures.append("-mIfoo must not forge isolation (expected exit 1)")
        if run_quiet(_build(tmp / "attached-c", hooks_args=("-cIbar", "x.py"))) != 1:
            failures.append("-cIbar must not forge isolation (expected exit 1)")
        # 24b. Confirm against a REAL interpreter that the char attached after -c is the command
        #      operand, not the -I flag: `python3 -c<command starting with I>` runs NON-isolated
        #      (sys.flags.isolated == 0), matching the gate's model above.
        attached = subprocess.run(
            [sys.executable, "-cImp=1\nimport sys\nprint(sys.flags.isolated)"],
            capture_output=True, text=True, env=env, timeout=30)
        if attached.returncode != 0 or attached.stdout.strip() != "0":
            failures.append("real python3 -c with an attached I-command should run non-isolated "
                            "(got rc={}, out={!r})".format(attached.returncode, attached.stdout))

        # 25. A redirect whose TARGET is a filename like `python3` (`... >&python3`) is NOT mis-split
        #     into a fabricated bare launcher: the `&` belongs to the `>&` redirect, not a control
        #     operator. An ISOLATED launcher with that redirect passes (exit 0). Without the redirect-
        #     aware `&` split the target `python3` becomes a fabricated non-isolated launcher (exit 1).
        redir_iso = _build(tmp / "redir-iso",
                           run_all="#!/usr/bin/env bash\npython3 -I -B tools/alpha.py >&python3\n")
        if run_quiet(redir_iso) != 0:
            failures.append("an isolated launcher with a `>&python3` redirect expected exit 0 "
                            "(the redirect target must not be read as a launcher)")

        # 26. A NON-isolated launcher with a `>&python3` redirect fails on the REAL launcher, not on a
        #     fabricated redirect-target launcher: exactly one failure, it names the real launcher, and
        #     none of them is a bare `python3`. Without the fix this reports two failures (the real
        #     launcher and a fabricated bare `python3`).
        redir_noniso = _build(tmp / "redir-noniso",
                              run_all="#!/usr/bin/env bash\npython3 tools/alpha.py >&python3\n")
        errs, fails = scan(redir_noniso)
        if (errs or len(fails) != 1 or "tools/alpha.py" not in fails[0][3]
                or any("launcher 'python3';" in f[3] for f in fails)):
            failures.append("a non-isolated `>&python3` launcher must fail once on the real launcher, "
                            "never fabricate a redirect-target launcher (errs={!r}, fails={!r})"
                            .format(errs, fails))

        # 27. The other `&`-carrying redirect forms around an ISOLATED launcher (`2>&1`, `>&2`,
        #     `&>/dev/null`) do not fabricate a launcher: all pass (exit 0).
        redir_forms = _build(tmp / "redir-forms",
                             run_all="#!/usr/bin/env bash\n"
                                     "python3 -I -B tools/a.py 2>&1\n"
                                     "python3 -I -B tools/b.py >&2\n"
                                     "python3 -I -B tools/c.py &>/dev/null\n")
        if run_quiet(redir_forms) != 0:
            failures.append("isolated launchers with 2>&1 / >&2 / &>/dev/null redirects expected exit 0")

        # 28. The genuine separators are NOT loosened: a non-isolated launcher after a real logical
        #     `&&` and after a real background `&` is still segmented and caught (exit 1 in each case).
        and_caught = _build(tmp / "and-caught",
                            run_all="#!/usr/bin/env bash\necho a && python3 tools/a.py\n")
        if run_quiet(and_caught) != 1:
            failures.append("a non-isolated launcher after a genuine `&&` must still be caught (exit 1)")
        bg_caught = _build(tmp / "bg-caught",
                           run_all="#!/usr/bin/env bash\necho a & python3 tools/a.py\n")
        if run_quiet(bg_caught) != 1:
            failures.append("a non-isolated launcher after a genuine background `&` must still be "
                            "caught (exit 1)")

        # 29. Confirm against REAL bash that `>&python3` is a redirect to a file named `python3` (not a
        #     command): the launcher runs ISOLATED (sys.flags.isolated == 1) and a file `python3` holds
        #     its output. This is the fail-to-pass witness for the class the gate's model above now
        #     matches. Skipped where bash is unavailable.
        bash_bin = shutil.which("bash")
        if bash_bin is None:
            skipped.append("29 real-bash-redirect (bash unavailable)")
        else:
            rb = tmp / "realbash"
            rb.mkdir()
            rb_cmd = ("{} -I -c 'import sys; sys.stdout.write(str(sys.flags.isolated))' >&python3"
                      .format(shlex.quote(sys.executable)))
            rb_proc = subprocess.run([bash_bin, "-c", rb_cmd], cwd=str(rb), capture_output=True,
                                     text=True, env=env, timeout=30)
            rb_out = rb / "python3"
            if not _exists(rb_out):
                failures.append("real bash: `>&python3` should create a file named python3 "
                                "(rc={}, stderr={!r})".format(rb_proc.returncode, rb_proc.stderr))
            elif rb_out.read_text(encoding="utf-8").strip() != "1":
                failures.append("real bash: `python3 -I ... >&python3` should run isolated (file held "
                                "{!r})".format(rb_out.read_text(encoding="utf-8")))

        # 30. A quote/escape-obfuscated launcher NAME in a run_all_checks.sh shell line is resolved by
        #     bash to a real interpreter, so the gate must decide on the DECODED command word, not the
        #     raw text. Each obfuscated bare launcher fails (exit 1); the isolated `pyt\hon3 -I` form,
        #     alongside a plain `echo hello`, passes (exit 0). Before the fix the raw prefilter never saw
        #     `python3` in these lines and reported them CLEAN (the shell-line-path miss this fix closes).
        for label, sh_line in (("backslash", r"pyt\hon3 tools/alpha.py"),
                               ("dquote", 'pyt"hon"3 tools/alpha.py'),
                               ("squote", "py'thon'3 tools/alpha.py")):
            run_all = "#!/usr/bin/env bash\n{}\n".format(sh_line)
            if run_quiet(_build(tmp / ("obf-" + label), run_all=run_all)) != 1:
                failures.append("an obfuscated launcher name ({}) in run_all_checks.sh must be caught "
                                "(expected exit 1)".format(label))
        iso_obf = "#!/usr/bin/env bash\npyt\\hon3 -I tools/alpha.py\necho hello\n"
        if run_quiet(_build(tmp / "obf-iso", run_all=iso_obf)) != 0:
            failures.append("an isolated obfuscated launcher (pyt\\hon3 -I) plus a plain line expected "
                            "exit 0")

        # 31. The same obfuscated NAMES in a settings.json shell-string are caught (exit 1): a
        #     regression guard proving the settings path (which already decoded first) still agrees
        #     with the now-fixed shell-line path. json.dumps builds the command so the backslash and
        #     quotes survive into the JSON string exactly.
        for label, cmd in (("backslash", r"pyt\hon3 tools/x.py"),
                           ("dquote", 'pyt"hon"3 tools/x.py'),
                           ("squote", "py'thon'3 tools/x.py")):
            obf = _build(tmp / ("settings-obf-" + label))
            sp = obf / ".claude" / "settings.json"
            sp.parent.mkdir(parents=True)
            sp.write_text(json.dumps({"hooks": {"PreToolUse": [{"hooks": [
                {"type": "command", "command": cmd}]}]}}) + "\n", encoding="utf-8")
            if run_quiet(obf) != 1:
                failures.append("an obfuscated launcher name ({}) in settings.json must be caught "
                                "(expected exit 1)".format(label))

        # 32. Real bash resolves each obfuscated NAME to the token `python3` (the fail-to-pass witness
        #     for the decode-then-decide shell-line fix). Skipped where bash is unavailable.
        if bash_bin is None:
            skipped.append("32 real-bash-obfuscated-name (bash unavailable)")
        else:
            for label, expr in (("backslash", r"pyt\hon3"), ("dquote", 'pyt"hon"3'),
                                ("squote", "py'thon'3")):
                proc = subprocess.run([bash_bin, "-c", "printf '%s' {}".format(expr)],
                                      capture_output=True, text=True, env=env, timeout=30)
                if proc.returncode != 0 or proc.stdout != "python3":
                    failures.append("real bash should resolve the obfuscated name {} to 'python3' "
                                    "(got rc={}, out={!r})".format(label, proc.returncode, proc.stdout))

        # 33. DISCRIMINATING (finding-6: the QA-suite Python sources are scanned for a reintroduced sys.path
        #     insertion at index 0). A covered source that reinserts the script dir ahead of the stdlib is a
        #     non-isolated FINDING (exit 1); the sanctioned sys.path.append form is clean (exit 0); a MISSING
        #     covered source fails closed (exit 2, a required input). Removing _scan_pysource_isolation lets
        #     the reintroduction pass (exit 0), failing the first case.
        insert_tree = _build(tmp / "pysource-insert")
        (insert_tree / "tools" / "audit_reference.py").write_text(
            "import sys\nfrom pathlib import Path\n"
            "sys.path.insert(0, str(Path(__file__).resolve().parent))\nimport _qa_adapter\n",
            encoding="utf-8")
        if run_quiet(insert_tree) != 1:
            failures.append("a QA source reintroducing a sys.path index-0 insertion expected exit 1 "
                            "(finding-6)")
        append_tree = _build(tmp / "pysource-append")
        (append_tree / "tools" / "audit_reference.py").write_text(
            "import sys\nfrom pathlib import Path\n"
            "sys.path.append(str(Path(__file__).resolve().parent))\nimport _qa_adapter\n", encoding="utf-8")
        if run_quiet(append_tree) != 0:
            failures.append("a QA source using the sanctioned sys.path.append form expected exit 0 "
                            "(finding-6)")
        missing_tree = _build(tmp / "pysource-missing")
        (missing_tree / "tools" / "check_internal_names.py").unlink()
        if run_quiet(missing_tree) != 2:
            failures.append("a missing required QA-suite Python source expected exit 2 (fail-closed, "
                            "finding-6)")

        # 34. A LITERAL block header in the full block-header grammar is read as a block: a bare
        #     launcher in its content fails (exit 1). The `| # comment` and `|2` forms passed unread
        #     before this case.
        for label, header, pad in (("comment", "| # comment", 10), ("indent", "|2", 10),
                                   ("indent-chomp", "|2-", 10)):
            wf_text = ("name: Q\njobs:\n  q:\n    steps:\n      - run: " + header +
                       "\n" + " " * pad + "set -e\n" + " " * pad + "python3 tools/alpha.py\n")
            if run_quiet(_build(tmp / ("header-" + label), workflow=wf_text)) != 1:
                failures.append("a bare launcher under the block header {!r} expected exit 1".format(header))
        iso_comment = ("name: Q\njobs:\n  q:\n    steps:\n      - run: | # comment\n"
                       "          python3 -I -B tools/alpha.py\n")
        if run_quiet(_build(tmp / "header-comment-iso", workflow=iso_comment)) != 0:
            failures.append("an isolated launcher under the block header '| # comment' expected exit 0")

        # 34b. A FOLDED (>) header, inside the block-header grammar but never read: YAML folds the
        #     content lines into ONE command and so changes SHELL COMMENT BOUNDARIES (a leading
        #     '#' content line folds onto one line with the content after it, so the comment
        #     consumes the folded launcher lines that follow; a '#' line after other content
        #     folds into mid-command text), so a line-by-line scan can disagree with the decoded
        #     value. Until folding is implemented
        #     every folded run: header is cannot-evaluate (exit 2, naming the header line), on the
        #     key line and on the line after an empty run: alike. Before this rule a bare launcher
        #     under `>+9` was read line-by-line (exit 1 here), and the `# note` form handed the
        #     comment line to the shell scan, where it is a comment only to the shell, not to YAML.
        for label, wf_text, named_line in (
                ("note",
                 "name: Q\njobs:\n  q:\n    steps:\n      - run: >\n          # note\n"
                 "          python3 tools/alpha.py\n", 5),
                ("indent9",
                 "name: Q\njobs:\n  q:\n    steps:\n      - run: >+9\n" + " " * 17 +
                 "python3 tools/alpha.py\n", 5),
                ("strip-comment",
                 "name: Q\njobs:\n  q:\n    steps:\n      - run: >-2 # note\n"
                 "          python3 tools/alpha.py\n", 5),
                ("next-line",
                 "name: Q\njobs:\n  q:\n    steps:\n      - run:\n          >\n"
                 "            python3 tools/alpha.py\n", 6)):
            rc, out = run_capture(_build(tmp / ("folded-" + label), workflow=wf_text))
            if rc != 2 or ("cannot-evaluate: .github/workflows/quality.yml:{}: a folded (>) run: "
                           "block scalar".format(named_line) not in out):
                failures.append("a folded run: header ({}) expected exit 2 naming quality.yml:{} as "
                                "a folded block scalar (got {}: {!r})".format(
                                    label, named_line, rc, out))

        # 35. A run: value outside the supported grammar is cannot-evaluate (exit 2), never skipped: a
        #     malformed block header, a node property or alias, and a plain scalar continued onto a deeper
        #     line. A sibling key at the run: key column after an inline scalar is not a continuation.
        # Each malformed header is tried with block content (a deeper line) and with a sibling key next, so
        # the header rule itself, not only the continuation rule, must refuse it.
        for label, value in (("zero", "|0"), ("glued-comment", "|#x"), ("trailing", "|2x"),
                             ("double-chomp", "|-+"), ("double", ">>")):
            for form, after in (("content", "\n          python3 tools/alpha.py\n"),
                                ("sibling", "\n        shell: bash\n")):
                wf_text = "name: Q\njobs:\n  q:\n    steps:\n      - run: " + value + after
                if run_quiet(_build(tmp / ("unsupported-" + label + "-" + form), workflow=wf_text)) != 2:
                    failures.append("a run: value {!r} outside the grammar ({}) expected exit 2".format(
                        value, form))
        # A node property or alias, followed by a sibling key (no deeper line), so only the node-property
        # rule can refuse it; read as an inline scalar it would pass clean.
        for label, value in (("anchor", "&a |"), ("tag", "!!str |"), ("alias", "*a")):
            wf_text = ("name: Q\njobs:\n  q:\n    steps:\n      - run: " + value +
                       "\n        shell: bash\n")
            if run_quiet(_build(tmp / ("unsupported-" + label), workflow=wf_text)) != 2:
                failures.append("a run: value {!r} outside the grammar expected exit 2".format(value))
        continued = ("name: Q\njobs:\n  q:\n    steps:\n      - run: echo a &&\n"
                     "          python3 tools/alpha.py\n")
        if run_quiet(_build(tmp / "unsupported-continued", workflow=continued)) != 2:
            failures.append("a plain run: scalar continued onto a deeper line expected exit 2")
        sibling = ("name: Q\njobs:\n  q:\n    steps:\n      - run: python3 -I -B tools/alpha.py\n"
                   "        shell: bash\n")
        if run_quiet(_build(tmp / "inline-sibling-key", workflow=sibling)) != 0:
            failures.append("an inline run: scalar followed by a sibling key expected exit 0")

        # 36. Every *.yml workflow candidate is classified with a no-follow, error-preserving probe: a
        #     dangling symlink, a looping symlink, a symlink to a regular (even unreadable) target, a
        #     symlink to a READABLE and CLEAN target (the vector that discriminates the no-follow rule
        #     on a non-root runner: a probe that follows it scans the clean target and passes), a
        #     directory, and an injected non-regular mode are each a named cannot-evaluate (exit 2);
        #     case 2's regular file is the positive control. Before this rule each was a silent pass.
        wf_extra = Path(".github") / "workflows" / "extra.yml"
        dangling_wf = _build(tmp / "wf-dangling")
        os.symlink("no-such-target.yml", str(dangling_wf / wf_extra))
        if run_quiet(dangling_wf) != 2:
            failures.append("a dangling workflow symlink expected exit 2 (fail-closed)")
        loop_wf = _build(tmp / "wf-loop")
        os.symlink("extra.yml", str(loop_wf / wf_extra))
        if run_quiet(loop_wf) != 2:
            failures.append("a looping workflow symlink expected exit 2 (fail-closed)")
        linked_wf = _build(tmp / "wf-linked")
        hidden_target = linked_wf / "hidden-target.yml"
        hidden_target.write_text("name: Q\njobs:\n  q:\n    steps:\n"
                                 "      - run: python3 tools/alpha.py\n", encoding="utf-8")
        os.symlink(str(hidden_target), str(linked_wf / wf_extra))
        os.chmod(hidden_target, 0)
        try:
            rc = run_quiet(linked_wf)
        finally:
            os.chmod(hidden_target, 0o644)
        if rc != 2:
            failures.append("a workflow symlink to a regular target expected exit 2 (no-follow), even "
                            "when the target is unreadable and holds a bare launcher")
        linked_clean_wf = _build(tmp / "wf-linked-clean")
        clean_target = linked_clean_wf / "clean-target.yml"
        clean_target.write_text("name: Q\njobs:\n  q:\n    steps:\n"
                                "      - run: python3 -I -B tools/alpha.py\n", encoding="utf-8")
        os.symlink(str(clean_target), str(linked_clean_wf / wf_extra))
        if run_quiet(linked_clean_wf) != 2:
            failures.append("a workflow symlink to a READABLE, clean regular target expected exit 2 "
                            "(no-follow): a probe that follows the link scans the clean target and "
                            "passes, so this vector discriminates the rule on a non-root runner")
        dir_wf = _build(tmp / "wf-dir")
        (dir_wf / ".github" / "workflows" / "sub.yml").mkdir()
        if run_quiet(dir_wf) != 2:
            failures.append("a directory named *.yml under workflows expected exit 2 (fail-closed)")
        nonreg_wf = _build(tmp / "wf-nonreg")
        (nonreg_wf / ".github" / "workflows" / "pipe.yml").write_text("name: Q\n", encoding="utf-8")
        saved_mode = module._candidate_mode
        def _fifo_mode(path, _real=saved_mode):
            if path.name == "pipe.yml":
                return stat.S_IFIFO | 0o644
            return _real(path)
        module._candidate_mode = _fifo_mode
        try:
            rc = run_quiet(nonreg_wf)
        finally:
            module._candidate_mode = saved_mode
        if rc != 2:
            failures.append("a non-regular workflow candidate (injected FIFO mode) expected exit 2")

        # 37. An empty run: value is read ONLY in the two proven shapes, else cannot-evaluate: a plain
        #     scalar on the next line (P3) and a block header on the next line with deeper content (P4)
        #     carry a bare launcher (exit 1) with their isolated forms passing (exit 0); an anchored
        #     next-line value, a malformed next-line header, a further-continued next-line scalar, a
        #     comment line before a deeper value, a header whose content sits at or above the header's
        #     own column or not deeper than the key, and a comment inside the block before its content
        #     are each cannot-evaluate (exit 2); an empty run: whose next non-blank, NON-COMMENT line
        #     is not deeper than the key and is not a sequence item at the key's own column (case 40
        #     refuses that one) is a null value with no command text (exit 0), a comment line alone
        #     never making it one. The bare forms passed unread, and the exit-2 arrangements
        #     passed as silent nulls or partial reads, before this rule.
        head = "name: Q\njobs:\n  q:\n    steps:\n      - run:\n"
        # chead's same-line value is ONLY a YAML comment: PyYAML strips it to the SAME empty value as
        # head's bare `run:`, so every head arrangement must behave identically under chead.
        chead = "name: Q\njobs:\n  q:\n    steps:\n      - run: # c\n"
        for label, wf_text, want, named_line in (
                ("p3-bare", head + "          python3 tools/alpha.py\n", 1, None),
                ("p3-iso", head + "          python3 -I -B tools/alpha.py\n", 0, None),
                ("p4-bare", head + "          |\n            python3 tools/alpha.py\n", 1, None),
                ("p4-iso", head + "          | # note\n            python3 -I -B tools/alpha.py\n",
                 0, None),
                ("comment-val-p3-bare", chead + "          python3 tools/alpha.py\n", 1, None),
                ("comment-val-p3-iso", chead + "          python3 -I -B tools/alpha.py\n", 0, None),
                ("comment-val-p4-bare", chead + "          |\n            python3 tools/alpha.py\n",
                 1, None),
                ("anchored", head + "          &a python3 tools/alpha.py\n", 2, 6),
                ("bad-header", head + "          |0\n            python3 tools/alpha.py\n", 2, 6),
                ("continued", head + "          echo a &&\n          python3 tools/alpha.py\n", 2, 7),
                ("comment-first", head + "          # setup\n          python3 tools/alpha.py\n",
                 2, 7),
                ("comment-val-then-comment-plain",
                 chead + "          # d\n          python3 tools/alpha.py\n", 2, 7),
                ("comment-val-then-comment-block",
                 chead + "          # d\n          |\n            python3 tools/alpha.py\n", 2, 7),
                ("comment-in-block",
                 head + "          |\n          # x\n            python3 tools/alpha.py\n", 2, 7),
                ("comment-in-block-deeper",
                 head + "          |\n            # x\n            python3 tools/alpha.py\n", 2, 7),
                ("header-equal-content", head + "          |\n          python3 tools/alpha.py\n",
                 2, 7),
                ("header-deeper-than-content",
                 head + "            |\n          python3 tools/alpha.py\n", 2, 7),
                ("header-no-content", head + "          |\n        shell: bash\n", 2, 6),
                ("header-at-eof", head + "          |\n", 2, 6),
                ("comment-then-null", head + "          # note\n        shell: bash\n", 0, None),
                ("comment-val-null", chead + "        shell: bash\n", 0, None),
                ("comment-val-at-eof", chead, 0, None),
                ("null-value", head + "        shell: bash\n", 0, None)):
            rc, out = run_capture(_build(tmp / ("empty-run-" + label), workflow=wf_text))
            if rc != want:
                failures.append("an empty run: {} form expected exit {} (got {})".format(
                    label, want, rc))
            elif named_line is not None and (
                    "cannot-evaluate: .github/workflows/quality.yml:{}:".format(named_line)
                    not in out):
                failures.append("the {} cannot-evaluate expected to name quality.yml:{} "
                                "(got {!r})".format(label, named_line, out))

        # 38. The run: VALUE grammar is CLOSED and the key/step grammar is POSITIVE (the roster
        #     comment above lists every row): a refused row asserts exit 2 naming quality.yml and
        #     the line, and a row carrying an expected MESSAGE also asserts that named diagnostic,
        #     so the sq-escape, sq-unterminated and flow-value rows are red on a reader whose
        #     refusal came from the downstream shell parse instead of the YAML value grammar, and
        #     the flow-line-under-with / flow-value-next-line rows pin UNSUPPORTED_LINE_FORMS[1]
        #     and the empty-run flow branch.
        sbase = "name: Q\njobs:\n  q:\n    steps:\n"
        for label, wf_text, want, named_line, message in (
                ("sq-plain-iso", sbase + "      - run: 'python3 -I -B tools/alpha.py'\n", 0, None,
                 None),
                ("sq-plain-bare", sbase + "      - run: 'python3 tools/alpha.py'\n", 1, None,
                 None),
                ("sq-then-comment",
                 sbase + "      - run: 'python3 tools/alpha.py' # note\n", 2, 5, None),
                ("dq-then-comment",
                 sbase + '      - run: "python3 tools/alpha.py" # note\n', 2, 5, None),
                ("dq-backslash-n",
                 sbase + '      - run: "echo a\\npython3 tools/alpha.py"\n', 2, 5, None),
                ("dq-escape-x",
                 sbase + '      - run: "\\x70ython3 tools/alpha.py"\n', 2, 5, None),
                ("dq-escape-u",
                 sbase + '      - run: "py\\u0074hon3 tools/alpha.py"\n', 2, 5, None),
                ("dq-escape-tab",
                 sbase + '      - run: "echo a;\\tpython3 tools/alpha.py"\n', 2, 5, None),
                ("dq-plain", sbase + '      - run: "python3 -I -B tools/alpha.py"\n', 2, 5, None),
                ("sq-escape",
                 sbase + "      - run: 'it''s python3 tools/alpha.py'\n", 2, 5,
                 "a single-quoted run: value with a quote escape"),
                ("sq-unterminated", sbase + "      - run: 'python3 tools/alpha.py\n", 2, 5,
                 "a single-quoted run: value with a quote escape"),
                ("flow-map-step", sbase + "      - {run: python3 tools/alpha.py}\n", 2, 5, None),
                ("flow-seq-step", sbase + "      - [run, python3 tools/alpha.py]\n", 2, 5, None),
                ("flow-steps-value",
                 "name: Q\njobs:\n  q:\n    steps: [{run: python3 tools/alpha.py}]\n", 2, 4, None),
                ("quoted-key", sbase + '      - "run": python3 tools/alpha.py\n', 2, 5, None),
                ("quoted-key-sq", sbase + "      - 'run': python3 tools/alpha.py\n", 2, 5, None),
                ("escaped-key-x", sbase + '      - "r\\x75n": python3 tools/alpha.py\n', 2, 5,
                 "a quoted key"),
                ("escaped-key-u", sbase + '      - "\\u0072un": python3 tools/alpha.py\n', 2, 5,
                 "a quoted key"),
                ("tagged-key", sbase + "      - !!str run: python3 tools/alpha.py\n", 2, 5,
                 "a YAML node property, tag, anchor or alias"),
                ("anchored-key", sbase + "      - &k run: python3 tools/alpha.py\n", 2, 5,
                 "a YAML node property, tag, anchor or alias"),
                ("alias-key",
                 "name: Q\nenv:\n  K: &k run\njobs:\n  q:\n    steps:\n"
                 "      - *k : python3 tools/alpha.py\n", 2, 7,
                 "a YAML node property, tag, anchor or alias"),
                ("plain-key-outside-set",
                 sbase + "      - timeout: 5\n        run: python3 -I -B tools/alpha.py\n", 2, 5,
                 "a step line outside the supported step grammar"),
                ("step-keys-allowlisted",
                 sbase + "      - name: a\n        id: x\n        if: always()\n"
                         "        continue-on-error: false\n        timeout-minutes: 5\n"
                         "        working-directory: docs\n        shell: bash\n        env:\n"
                         "          A: b\n        run: python3 -I -B tools/alpha.py\n", 0, None,
                 None),
                ("flow-line-under-with",
                 sbase + "      - uses: x\n        with:\n          [python3 tools/alpha.py]\n",
                 2, 7, "a line opening a flow collection"),
                ("flow-value-next-line",
                 sbase + "      - run:\n          {a: python3 tools/alpha.py}\n", 2, 6,
                 "on the line after an empty run: is a flow collection"),
                ("explicit-key",
                 sbase + "      - ? run\n        : python3 tools/alpha.py\n", 2, 5, None),
                ("spaced-key", sbase + "      - run : python3 tools/alpha.py\n", 2, 5, None),
                ("flow-value", sbase + "      - run: {a: python3 tools/alpha.py}\n", 2, 5,
                 "run: value is a flow collection"),
                ("comment-val-dq-next",
                 sbase + '      - run: # c\n          "echo a\\npython3 tools/alpha.py"\n', 2, 6,
                 None)):
            rc, out = run_capture(_build(tmp / ("value-grammar-" + label), workflow=wf_text))
            if rc != want:
                failures.append("a run: value-grammar {} form expected exit {} (got {})".format(
                    label, want, rc))
            elif named_line is not None and (
                    "cannot-evaluate: .github/workflows/quality.yml:{}:".format(named_line)
                    not in out):
                failures.append("the {} cannot-evaluate expected to name quality.yml:{} "
                                "(got {!r})".format(label, named_line, out))
            elif message is not None and message not in out:
                failures.append("the {} cannot-evaluate expected the diagnostic {!r} "
                                "(got {!r})".format(label, message, out))

        # 39. The literal block is bounded by the KEY column and every shell: value is read (the
        #     roster comment above lists the rows): a sibling step key after a dashed `- run: |`
        #     block re-enters the step-key and line-form rules instead of being consumed as block
        #     content; a shell: python template without isolation options is a non-isolated
        #     launcher finding on a step and under defaults.run at both levels; shell: bash and a
        #     two-key defaults.run mapping stay clean; a python template claiming isolation
        #     options and every unclassifiable shell: value are cannot-evaluate; and the
        #     quoted-key and node-property line forms refuse a key under any number of leading
        #     sequence dashes.
        for label, wf_text, want, named_line, message in (
                ("block-then-quoted-shell",
                 sbase + "      - run: |\n          echo ok\n        \"shell\": bash\n", 2, 7,
                 "a quoted key"),
                ("block-then-unknown-key",
                 sbase + "      - run: |\n          echo ok\n        timeout: 5\n", 2, 7,
                 "a step line outside the supported step grammar"),
                ("block-then-second-run",
                 sbase + "      - run: |\n          echo a\n        run: python3 tools/alpha.py\n",
                 1, None, "non-isolated launcher 'python3 tools/alpha.py'"),
                ("block-then-env-folded-value",
                 sbase + "      - run: |\n          echo ok\n        env:\n          X: >\n"
                         "            python3 tools/alpha.py\n", 0, None, None),
                ("shell-python-step",
                 sbase + "      - shell: python {0}\n        run: |\n          import json\n",
                 1, None, "non-isolated launcher 'python {0}'"),
                ("shell-python-job-defaults",
                 "name: Q\njobs:\n  q:\n    defaults:\n      run:\n        shell: python {0}\n"
                 "    steps:\n      - run: python3 -I -B tools/alpha.py\n", 1, None,
                 "non-isolated launcher 'python {0}'"),
                ("shell-python-workflow-defaults",
                 "name: Q\ndefaults:\n  run:\n    shell: python {0}\njobs:\n  q:\n    steps:\n"
                 "      - run: python3 -I -B tools/alpha.py\n", 1, None,
                 "non-isolated launcher 'python {0}'"),
                ("shell-bash-defaults-clean",
                 "name: Q\ndefaults:\n  run:\n    shell: bash\n    working-directory: docs\n"
                 "jobs:\n  q:\n    defaults:\n      run:\n        shell: bash\n    steps:\n"
                 "      - shell: bash\n        run: python3 -I -B tools/alpha.py\n", 0, None,
                 None),
                ("shell-python-isolated-template",
                 sbase + "      - shell: python3 -I {0}\n        run: |\n          import json\n",
                 2, 5, "isolation options"),
                ("shell-unclassifiable-template",
                 sbase + "      - shell: bash -e {0}\n        run: |\n          echo hi\n", 2, 5,
                 "outside the supported shell grammar"),
                ("shell-double-quoted",
                 sbase + "      - shell: \"bash\"\n        run: |\n          echo hi\n", 2, 5,
                 "a double-quoted shell: value"),
                ("defaults-run-unknown-key",
                 "name: Q\ndefaults:\n  run:\n    fetch-depth: 1\njobs:\n  q:\n    steps:\n"
                 "      - run: python3 -I -B tools/alpha.py\n", 2, 4,
                 "a run: mapping key outside the supported set"),
                ("nested-dash-tagged-key",
                 "name: Q\nx:\n  - - !!str run: python3 tools/alpha.py\n", 2, 3,
                 "a YAML node property, tag, anchor or alias"),
                ("nested-dash-quoted-key",
                 "name: Q\nx:\n  - - \"r\\x75n\": python3 tools/alpha.py\n", 2, 3,
                 "a quoted key")):
            rc, out = run_capture(_build(tmp / ("key-column-shell-" + label), workflow=wf_text))
            if rc != want:
                failures.append("a key-column/shell {} form expected exit {} (got {}: {!r})"
                                .format(label, want, rc, out))
            elif named_line is not None and (
                    "cannot-evaluate: .github/workflows/quality.yml:{}:".format(named_line)
                    not in out):
                failures.append("the {} cannot-evaluate expected to name quality.yml:{} "
                                "(got {!r})".format(label, named_line, out))
            elif message is not None and message not in out:
                failures.append("the {} row expected the diagnostic {!r} (got {!r})".format(
                    label, message, out))

        # 40. the CLOSED line grammar (the roster comment above lists the rows): outside
        #     block-scalar content a line is read only as a blank or comment line, a plain
        #     `KEY:`/`KEY: VALUE` line (the colon directly after the key, at most ONE leading
        #     `- ` dash), or a plain `- SCALAR` item; a flow value only as a flow sequence of
        #     plain or quoted scalars; a shell: value only as the literal comment-stripped
        #     keyword on one line; a value on the line after an empty run: only under the same
        #     closed grammar. Each refused row asserts exit 2 naming quality.yml and the line
        #     plus the named diagnostic; each exit-1 row asserts the finding's file, line and
        #     command. What the rows pin, one check each: the flow-mapping refusal (escaped,
        #     tagged and explicit key spellings); the empty-run spaced-key refusal (both
        #     defaults levels); the nested-dash refusal (run and shell keys; the flow-step form
        #     is a pre-existing refusal, a control, not a discriminator); the single-quoted and
        #     continued shell refusals; the `- run: >` folded consumer's KEY-column bound (its
        #     line-7 quoted-key diagnostic disappears when the bound reverts to the dash
        #     indent); the empty-run next-line closed grammar (a dotted and a numeric mapping
        #     key, an explicit-key indicator, a sequence item, one row per sub-condition --
        #     a lone '-', a '-'+TAB item, a lone '?', a ':'+TAB dotted key, a trailing
        #     colon -- the three clean next-line scalars the mapping test used to over-refuse,
        #     and the four key-column sequence shapes, comment-separated included, that used
        #     to be read as null, plus a same-column lone '-' and '-'+TAB item, each asserting
        #     the same-column diagnostic, not the deeper-value one); the literal LEADING-KEYWORD
        #     spelling in a run: block (a quoted or escaped if/then/do/time/'!' before a bare
        #     launcher is a cannot-evaluate, never a finding; bare coproc is the pre-existing
        #     control; an unquoted if still skips, pinned by its exit-1 finding); the literal
        #     SEPARATOR spelling (a quoted ';' after i'f' is an argument, so the bare and the
        #     -I launcher after it are each a cannot-evaluate, not a finding and not clean); the
        #     literal shell-level ASSIGNMENT spelling (a quoted 'X=y' is a command name: the bare
        #     and -I launcher after it are each a cannot-evaluate; an X="a b" assignment and an
        #     env 'X=y' argument still skip, pinned by their exit-1 findings); the rows in
        #     no_finding_rows also check the structured scan() result, the named error present
        #     and the failures list empty, because exit 2 hides any extra finding; the literal shell-keyword
        #     comparison (embedded single-quote, double-quote, backslash and doubled-quote
        #     spellings, at a step and under defaults.run); _flow_sequence_value_ok (the
        #     colon-carrying flow sequence); the generic node-property refusal; the generic
        #     malformed-block-header refusal; the generic spaced-key form's own diagnostic
        #     (red when the form narrows to run-only); and the generic block-scalar KEY-column
        #     bound (a sibling run: after `- name: |` inside steps and after `- y: |` outside
        #     steps is a line-named finding, exit 1, never consumed content).
        #     flow-sequence-clean is the live workflows' clean control.
        no_finding_rows = frozenset({
            "keyword-quoted-if", "keyword-quoted-then", "keyword-escaped-do",
            "keyword-quoted-time", "keyword-quoted-bang", "separator-quoted-bare",
            "separator-quoted-isolated", "assign-quoted-name-bare", "assign-quoted-name-isolated"})
        for label, wf_text, want, named_line, message in (
                ("flow-mapping-escaped-key",
                 'name: Q\ndefaults: {"r\\x75n": {shell: bash}}\njobs:\n  q:\n    steps:\n'
                 "      - run: python3 -I -B tools/alpha.py\n", 2, 2,
                 "a flow mapping value"),
                ("flow-mapping-tagged-key",
                 "name: Q\ndefaults: {!!str run: {shell: bash}}\njobs:\n  q:\n    steps:\n"
                 "      - run: python3 -I -B tools/alpha.py\n", 2, 2,
                 "a flow mapping value"),
                ("flow-mapping-explicit-key",
                 "name: Q\ndefaults: {? run : {shell: bash}}\njobs:\n  q:\n    steps:\n"
                 "      - run: python3 -I -B tools/alpha.py\n", 2, 2,
                 "a flow mapping value"),
                ("spaced-shell-key-workflow-defaults",
                 "name: Q\ndefaults:\n  run:\n    shell : bash\njobs:\n  q:\n    steps:\n"
                 "      - run: python3 -I -B tools/alpha.py\n", 2, 4,
                 "a key with whitespace before its colon on the line after an empty run:"),
                ("spaced-shell-key-job-defaults",
                 "name: Q\njobs:\n  q:\n    defaults:\n      run:\n        shell : bash\n"
                 "    steps:\n      - run: python3 -I -B tools/alpha.py\n", 2, 6,
                 "a key with whitespace before its colon on the line after an empty run:"),
                ("nested-dash-run",
                 "name: Q\nx:\n  - - run: echo ok\n", 2, 3,
                 "a nested '- - ' sequence entry"),
                ("nested-dash-shell",
                 "name: Q\nx:\n  - - shell: bash\n", 2, 3,
                 "a nested '- - ' sequence entry"),
                ("nested-dash-flow-step",
                 "name: Q\nx:\n  - - {shell: bash}\n", 2, 3,
                 "a flow mapping or flow sequence step"),
                ("flow-sequence-clean",
                 "name: Q\non:\n  push:\n    branches: [main]\njobs:\n  q:\n    steps:\n"
                 "      - run: python3 -I -B tools/alpha.py\n", 0, None, None),
                ("single-quoted-shell",
                 sbase + "      - shell: 'bash'\n        run: |\n          echo hi\n", 2, 5,
                 "a single-quoted shell: value"),
                ("shell-continuation",
                 sbase + "      - shell: bash\n          -e {0}\n        run: |\n"
                         "          echo hi\n", 2, 6,
                 "a shell: value continued on a deeper-indented line"),
                ("folded-bound-sibling",
                 sbase + '      - run: >\n          echo hi\n        "shell": bash\n', 2, 7,
                 "a quoted key"),
                ("next-line-dotted-key",
                 "name: Q\ndefaults:\n  run:\n    foo.bar : bash\njobs:\n  q:\n    steps:\n"
                 "      - run: python3 -I -B tools/alpha.py\n", 2, 4,
                 "a mapping key (of any spelling) on the line after an empty run:"),
                ("next-line-numeric-key",
                 "name: Q\ndefaults:\n  run:\n    123: bash\njobs:\n  q:\n    steps:\n"
                 "      - run: python3 -I -B tools/alpha.py\n", 2, 4,
                 "a mapping key (of any spelling) on the line after an empty run:"),
                ("next-line-explicit-key",
                 "name: Q\ndefaults:\n  run:\n    ? shell\njobs:\n  q:\n    steps:\n"
                 "      - run: python3 -I -B tools/alpha.py\n", 2, 4,
                 "an explicit-key indicator on the line after an empty run:"),
                ("next-line-sequence-item",
                 "name: Q\ndefaults:\n  run:\n    - shell: bash\njobs:\n  q:\n    steps:\n"
                 "      - run: python3 -I -B tools/alpha.py\n", 2, 4,
                 "a sequence item on the line after an empty run:"),
                ("next-line-seq-key-col-defaults",
                 "name: Q\ndefaults:\n  run:\n  - python3 tools/alpha.py\njobs:\n  q:\n    steps:\n"
                 "      - run: python3 -I -B tools/alpha.py\n", 2, 4,
                 "a sequence item at the run: key's own column after an empty run:"),
                ("next-line-seq-key-col-comment",
                 "name: Q\ndefaults:\n  run:\n  # c\n  - python3 tools/alpha.py\njobs:\n  q:\n"
                 "    steps:\n      - run: python3 -I -B tools/alpha.py\n", 2, 5,
                 "a sequence item at the run: key's own column after an empty run:"),
                ("next-line-seq-key-col-job-defaults",
                 "name: Q\njobs:\n  q:\n    defaults:\n      run:\n      - python3 tools/alpha.py\n"
                 "    steps:\n      - run: python3 -I -B tools/alpha.py\n", 2, 6,
                 "a sequence item at the run: key's own column after an empty run:"),
                ("next-line-seq-key-col-step",
                 sbase + "      - run:\n        - python3 tools/alpha.py\n", 2, 6,
                 "a sequence item at the run: key's own column after an empty run:"),
                ("next-line-seq-key-col-lone-dash",
                 "name: Q\ndefaults:\n  run:\n  -\njobs:\n  q:\n    steps:\n"
                 "      - run: python3 -I -B tools/alpha.py\n", 2, 4,
                 "a sequence item at the run: key's own column after an empty run:"),
                ("next-line-seq-key-col-dash-tab",
                 "name: Q\ndefaults:\n  run:\n  -\tpython3 tools/alpha.py\njobs:\n  q:\n"
                 "    steps:\n      - run: python3 -I -B tools/alpha.py\n", 2, 4,
                 "a sequence item at the run: key's own column after an empty run:"),
                ("next-line-lone-dash",
                 "name: Q\ndefaults:\n  run:\n    -\njobs:\n  q:\n    steps:\n"
                 "      - run: python3 -I -B tools/alpha.py\n", 2, 4,
                 "a sequence item on the line after an empty run:"),
                ("next-line-dash-tab",
                 "name: Q\ndefaults:\n  run:\n    -\techo hi\njobs:\n  q:\n    steps:\n"
                 "      - run: python3 -I -B tools/alpha.py\n", 2, 4,
                 "a sequence item on the line after an empty run:"),
                ("next-line-lone-question",
                 "name: Q\ndefaults:\n  run:\n    ?\njobs:\n  q:\n    steps:\n"
                 "      - run: python3 -I -B tools/alpha.py\n", 2, 4,
                 "an explicit-key indicator on the line after an empty run:"),
                ("next-line-colon-tab",
                 "name: Q\ndefaults:\n  run:\n    a.b:\tbash\njobs:\n  q:\n    steps:\n"
                 "      - run: python3 -I -B tools/alpha.py\n", 2, 4,
                 "a mapping key (of any spelling) on the line after an empty run:"),
                ("next-line-trailing-colon",
                 "name: Q\ndefaults:\n  run:\n    a.b:\njobs:\n  q:\n    steps:\n"
                 "      - run: python3 -I -B tools/alpha.py\n", 2, 4,
                 "a mapping key (of any spelling) on the line after an empty run:"),
                ("next-line-sq-colon-clean",
                 sbase + "      - run:\n          'echo foo: bar'\n", 0, None, None),
                ("next-line-comment-colon-clean",
                 sbase + "      - run:\n          echo ok # note: harmless\n", 0, None, None),
                ("next-line-isolated-comment-colon-clean",
                 sbase + "      - run:\n          python3 -I x.py  # :\n", 0, None, None),
                ("keyword-quoted-if",
                 sbase + "      - run: |\n          i'f' python3 x.py\n", 2, 6,
                 "a python token is not in a resolvable command-word position"),
                ("keyword-quoted-then",
                 sbase + "      - run: |\n          \"then\" python3 x.py\n", 2, 6,
                 "a python token is not in a resolvable command-word position"),
                ("keyword-escaped-do",
                 sbase + "      - run: |\n          d\\o python3 x.py\n", 2, 6,
                 "a python token is not in a resolvable command-word position"),
                ("keyword-quoted-time",
                 sbase + "      - run: |\n          t'ime' python3 x.py\n", 2, 6,
                 "a python token is not in a resolvable command-word position"),
                ("keyword-quoted-bang",
                 sbase + "      - run: |\n          '!' python3 x.py\n", 2, 6,
                 "a python token is not in a resolvable command-word position"),
                ("keyword-bare-coproc",
                 sbase + "      - run: |\n          coproc python3 x.py\n", 2, 6,
                 "a python token is not in a resolvable command-word position"),
                ("keyword-literal-if-control",
                 sbase + "      - run: |\n          if python3 x.py; then true; fi\n", 1, None,
                 "non-isolated launcher 'python3 x.py'"),
                ("separator-quoted-bare",
                 sbase + "      - run: |\n          i'f' ';' python3 x.py\n", 2, 6,
                 "a python token is not in a resolvable command-word position"),
                ("separator-quoted-isolated",
                 sbase + "      - run: |\n          i'f' ';' python3 -I x.py\n", 2, 6,
                 "a python token is not in a resolvable command-word position"),
                ("assign-quoted-name-bare",
                 sbase + "      - run: |\n          'X=y' python3 x.py\n", 2, 6,
                 "a python token is not in a resolvable command-word position"),
                ("assign-quoted-name-isolated",
                 sbase + "      - run: |\n          'X=y' python3 -I x.py\n", 2, 6,
                 "a python token is not in a resolvable command-word position"),
                ("assign-quoted-value-control",
                 sbase + "      - run: |\n          X=\"a b\" python3 x.py\n", 1, None,
                 ".github/workflows/quality.yml:6: non-isolated launcher 'python3 x.py'"),
                ("assign-after-env-control",
                 sbase + "      - run: |\n          env 'X=y' python3 x.py\n", 1, None,
                 ".github/workflows/quality.yml:6: non-isolated launcher 'python3 x.py'"),
                ("shell-embedded-sq-step",
                 sbase + "      - shell: ba'sh'\n        run: |\n          echo hi\n", 2, 5,
                 "neither a bare known shell keyword"),
                ("shell-embedded-dq-step",
                 sbase + '      - shell: ba"sh"\n        run: |\n          echo hi\n', 2, 5,
                 "neither a bare known shell keyword"),
                ("shell-backslash-step",
                 sbase + "      - shell: ba\\sh\n        run: |\n          echo hi\n", 2, 5,
                 "neither a bare known shell keyword"),
                ("shell-doubled-sq-step",
                 sbase + "      - shell: b''ash\n        run: |\n          echo hi\n", 2, 5,
                 "neither a bare known shell keyword"),
                ("shell-embedded-sq-defaults",
                 "name: Q\ndefaults:\n  run:\n    shell: ba'sh'\njobs:\n  q:\n    steps:\n"
                 "      - run: python3 -I -B tools/alpha.py\n", 2, 4,
                 "neither a bare known shell keyword"),
                ("shell-embedded-dq-defaults",
                 'name: Q\ndefaults:\n  run:\n    shell: ba"sh"\njobs:\n  q:\n    steps:\n'
                 "      - run: python3 -I -B tools/alpha.py\n", 2, 4,
                 "neither a bare known shell keyword"),
                ("shell-backslash-defaults",
                 "name: Q\ndefaults:\n  run:\n    shell: ba\\sh\njobs:\n  q:\n    steps:\n"
                 "      - run: python3 -I -B tools/alpha.py\n", 2, 4,
                 "neither a bare known shell keyword"),
                ("shell-doubled-sq-defaults",
                 "name: Q\ndefaults:\n  run:\n    shell: b''ash\njobs:\n  q:\n    steps:\n"
                 "      - run: python3 -I -B tools/alpha.py\n", 2, 4,
                 "neither a bare known shell keyword"),
                ("flow-sequence-colon",
                 "name: Q\non:\n  push:\n    branches: [a: b]\njobs:\n  q:\n    steps:\n"
                 "      - run: python3 -I -B tools/alpha.py\n", 2, 4,
                 "not a flow sequence of plain or quoted scalars"),
                ("generic-anchored-value",
                 "name: Q\nx: &a b\njobs:\n  q:\n    steps:\n"
                 "      - run: python3 -I -B tools/alpha.py\n", 2, 2,
                 "a value opening with a YAML node property, tag, anchor or alias"),
                ("generic-malformed-header",
                 "name: Q\nx: |x\njobs:\n  q:\n    steps:\n"
                 "      - run: python3 -I -B tools/alpha.py\n", 2, 2,
                 "header is outside the block-header grammar"),
                ("generic-spaced-key",
                 "name: Q\nfoo : bar\njobs:\n  q:\n    steps:\n"
                 "      - run: python3 -I -B tools/alpha.py\n", 2, 2,
                 "a key with whitespace before its colon, outside the supported line grammar"),
                ("generic-block-bound-step",
                 sbase + "      - name: |\n          a name\n        run: python3 tools/alpha.py\n",
                 1, None,
                 ".github/workflows/quality.yml:7: non-isolated launcher 'python3 tools/alpha.py'"),
                ("generic-block-bound-nonsteps",
                 "name: Q\nx:\n  - y: |\n      a\n    run: python3 tools/alpha.py\n", 1, None,
                 ".github/workflows/quality.yml:5: "
                 "non-isolated launcher 'python3 tools/alpha.py'")):
            lg_root = _build(tmp / ("line-grammar-" + label), workflow=wf_text)
            rc, out = run_capture(lg_root)
            if rc != want:
                failures.append("a line-grammar {} form expected exit {} (got {}: {!r})".format(
                    label, want, rc, out))
            elif named_line is not None and (
                    "cannot-evaluate: .github/workflows/quality.yml:{}:".format(named_line)
                    not in out):
                failures.append("the line-grammar {} row expected to name quality.yml:{} "
                                "(got {!r})".format(label, named_line, out))
            elif message is not None and message not in out:
                failures.append("the line-grammar {} row expected the diagnostic {!r} "
                                "(got {!r})".format(label, message, out))
            if label in no_finding_rows:
                # Exit 2 already wins over any finding, so the exit code and output text above
                # cannot see an extra finding: the structured errors and failures are checked
                # separately, the named error required and the failures list required empty.
                lg_errors, lg_failures = scan(lg_root)
                if lg_failures:
                    failures.append("the line-grammar {} row expected no finding (got {!r})"
                                    .format(label, lg_failures))
                if not any(err[0] == ".github/workflows/quality.yml" and err[1] == named_line
                           and message in err[3] for err in lg_errors):
                    failures.append("the line-grammar {} row expected the structured error {!r} "
                                    "at quality.yml:{} (got {!r})".format(
                                        label, message, named_line, lg_errors))

        # 41. The settings shell-string caller and the tokenization boundary. A quoted ';' is an
        #     argument, never a separator, and a quoted 'X=y' is a command name, never an
        #     assignment, so in a settings.json command `i'f' ';' python3 x.py` (a finding when
        #     the quoted ';' split), its -I form (clean then) and `'X=y' python3 -I x.py` (clean
        #     when the assignment was matched decoded) are each a cannot-evaluate with no finding.
        #     Then a raw/decoded disagreement is injected through the _decode_tokens seam: an extra
        #     decoded token (the count guard) and a same-count altered last token (the per-token
        #     decoding guard) are each refused through a run_all_checks.sh line and a settings.json
        #     command, each refusal carrying its own guard's message, with no finding.
        def _settings_tree(base, cmd, run_all=_ISO_RUN_ALL):
            root = _build(base, run_all=run_all)
            sp = root / ".claude" / "settings.json"
            sp.parent.mkdir(parents=True)
            sp.write_text(json.dumps({"hooks": {"PreToolUse": [{"hooks": [
                {"type": "command", "command": cmd}]}]}}) + "\n", encoding="utf-8")
            return root

        for label, cmd in (("separator-bare", "i'f' ';' python3 x.py"),
                           ("separator-isolated", "i'f' ';' python3 -I x.py"),
                           ("assign-isolated", "'X=y' python3 -I x.py")):
            st_root = _settings_tree(tmp / ("settings-literal-" + label), cmd)
            rc = run_quiet(st_root)
            st_errors, st_failures = scan(st_root)
            if rc != 2:
                failures.append("the settings {} command expected exit 2 (got {})".format(label, rc))
            if st_failures:
                failures.append("the settings {} command expected no finding (got {!r})"
                                .format(label, st_failures))
            if not any(err[0] == ".claude/settings.json" and "a python token is not in a "
                       "resolvable command-word position" in err[3] for err in st_errors):
                failures.append("the settings {} command expected the command-word-position "
                                "error (got {!r})".format(label, st_errors))

        def _extra_token(tokens):
            return tokens + ["injected"]

        def _altered_token(tokens):
            return tokens[:-1] + [tokens[-1] + "injected"] if tokens else tokens

        module = sys.modules[__name__]
        saved_decode = module._decode_tokens
        for label, inject, guard_message in (
                ("count", _extra_token, "raw and decoded tokenizations disagree"),
                ("content", _altered_token, "a raw token does not decode to its decoded form")):
            inj_root = _settings_tree(tmp / ("tokenize-inject-" + label), "python3 -I tools/x.py",
                                      run_all="#!/usr/bin/env bash\npython3 -I tools/alpha.py\n")
            module._decode_tokens = (lambda spaced, _real=saved_decode, _inject=inject:
                                     _inject(_real(spaced)))
            try:
                inj_errors, inj_failures = scan(inj_root)
            finally:
                module._decode_tokens = saved_decode
            if inj_failures:
                failures.append("the injected {} disagreement expected no finding (got {!r})"
                                .format(label, inj_failures))
            if not any(err[0] == RUN_ALL_REL and err[1] == 2
                       and "shell line may carry a python launcher but does not parse" in err[3]
                       and guard_message in err[3] for err in inj_errors):
                failures.append("the injected {} disagreement expected a run_all_checks.sh:2 "
                                "refusal carrying {!r} (got {!r})".format(
                                    label, guard_message, inj_errors))
            if not any(err[0] == ".claude/settings.json" and "command string does not parse" in err[3]
                       and guard_message in err[3] for err in inj_errors):
                failures.append("the injected {} disagreement expected a settings.json refusal "
                                "carrying {!r} (got {!r})".format(label, guard_message, inj_errors))

        # 10. The gate REFUSES (exit 2) when its own interpreter is not isolated (a real subprocess: no
        #     -I, so the bootstrap self-guard fires before any scan or sibling import).
        refuse = subprocess.run([sys.executable, str(Path(__file__).resolve())], capture_output=True,
                                text=True, env=env, cwd=str(tmp), timeout=30)
        if refuse.returncode != 2:
            failures.append("the gate should refuse to run non-isolated (expected exit 2, got {})"
                            .format(refuse.returncode))
        # 10b. The refusal exit survives a failing stderr: on /dev/full (the write succeeds into the
        #      buffer, the flush raises ENOSPC) and with descriptor 2 closed before exec (sys.stderr is
        #      None). The retired `sys.stderr.write(...); raise SystemExit(2)` shape exited 120 and 1 here.
        if os.path.exists("/dev/full"):
            with open("/dev/full", "wb") as full:
                refuse = subprocess.run([sys.executable, str(Path(__file__).resolve())],
                                        stdout=subprocess.PIPE, stderr=full, env=env, cwd=str(tmp),
                                        timeout=30)
            if refuse.returncode != 2 or refuse.stdout:
                failures.append("with stderr on /dev/full the gate should still refuse non-isolated with "
                                "exit 2 and empty stdout (got exit {}, stdout {!r})".format(
                                    refuse.returncode, refuse.stdout))
        else:
            skipped.append("10b stderr-full self-guard (/dev/full unavailable)")
        refuse = subprocess.run([sys.executable, str(Path(__file__).resolve())], stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, preexec_fn=_close_fd2, env=env, cwd=str(tmp),
                                timeout=30)
        if refuse.returncode != 2 or refuse.stdout:
            failures.append("with descriptor 2 closed the gate should still refuse non-isolated with "
                            "exit 2 and empty stdout (got exit {}, stdout {!r})".format(
                                refuse.returncode, refuse.stdout))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if failures:
        print("SELF-TEST FAIL:")
        for failure in failures:
            print("  - " + failure)
        return 1
    note = ("" if not skipped else
            " NOTE: skipped {} case(s) the runner cannot exercise: {}"
            .format(len(skipped), ", ".join(skipped)))
    print("SELF-TEST PASS: a sibling json.py neuters a bare interpreter and -I defeats it; a fully-"
          "isolated tree passes; a bare launcher, a partial -P -E, and flags after the script each fail "
          "(exit 1); malformed JSON, a non-list args, and an unreadable required input fail closed "
          "(exit 2); the run_gate grammar, an inline run: scalar, a run: | block, and an optional "
          "settings.json shell-string command are all recognized; a value-taking option's value is "
          "never letter-scanned (-W/-X isolation is neither forged nor missed); a chained settings "
          "shell-string catches a later-segment launcher; a non-regular or non-UTF-8 required surface "
          "and a present-but-unusable optional settings file each fail closed (exit 2); a launcher "
          "glued to a punctuation-adjacent operator (`prep;python3`, `&& (python3`) is still segmented "
          "and caught in a settings shell-string and a run_all_checks.sh line (exit 1); a present-but-"
          "dangling optional settings symlink fails closed (exit 2) while a truly-absent one is a clean "
          "skip (exit 0); an attached -m/-c operand (-mIfoo, -cIbar) never forges isolation (exit 1); an "
          "`&`-carrying redirect (`>&python3`, `2>&1`, `>&2`, `&>/dev/null`) is not mis-split into a "
          "fabricated launcher (an isolated one passes, a non-isolated one fails once on the real "
          "launcher) while a genuine `&&`/background `&` still splits, matching real bash; a quote/"
          "escape-obfuscated launcher NAME (`pyt\\hon3`, `pyt\"hon\"3`, `py'thon'3`) that bash resolves "
          "to a real interpreter is caught on a run_all_checks.sh line and in a settings.json shell-"
          "string (exit 1) with its isolated form passing (exit 0), confirmed against real bash; a "
          "QA-suite Python source that reintroduces a sys.path index-0 insertion is a finding (exit 1) "
          "while the sanctioned sys.path.append form is clean (exit 0) and a missing required QA source "
          "fails closed (exit 2); a run: LITERAL block header in the full block-header grammar (a "
          "trailing comment, an indentation digit, a chomping indicator in either order) is read as a "
          "block and EVERY folded (>) header, on the key line or after an empty run:, is "
          "cannot-evaluate (exit 2) naming the header line; an "
          "empty run: (a bare `run:` or a comment-only same-line value, the same empty value) is read only "
          "in the two proven shapes (a next-line plain scalar, or a next-line "
          "LITERAL block header with content deeper than both the header and the key; a bare launcher "
          "there fails, an isolated one passes) while a null run: (next non-blank, non-comment line not "
          "deeper than the key and not a sequence item at the key's own column) stays clean; a "
          "comment line before a deeper value, a header without "
          "content deeper than the key (a header at the end of the text included) or with content at "
          "or above its own column, and a '#' line directly after the header (YAML block content this "
          "reader refuses) are cannot-evaluate (exit 2), each naming the file and line; any other run: value "
          "starting with | or > on either line, a node property or alias, and a further-continued plain "
          "scalar are cannot-evaluate (exit 2); the run: value grammar is closed (a plain scalar, a "
          "LITERAL block scalar, a single-quoted scalar with no escape decoded to its body): a "
          "double-quoted scalar, a quoted scalar followed by a trailing comment or text, a "
          "single-quoted escape or unterminated quote (each asserting the value reader's own "
          "diagnostic), a flow mapping or sequence step, value or line, a run key in a flow "
          "collection, ANY quoted key (escaped spellings included), a tagged, anchored or aliased key "
          "line, an explicit key, a run key with whitespace before its colon, a plain step key "
          "outside the closed step-key set, a steps: key with a same-line value, and a step line the "
          "step model cannot place are each cannot-evaluate (exit 2) naming the file and line, while "
          "a step whose keys are all on the allowlist stays clean; "
          "a `- run: |` block ends at the KEY column, so a quoted or off-allowlist sibling key "
          "after it is cannot-evaluate (exit 2), a second run: key there is a finding (exit 1) "
          "and an env: folded value after it is clean (exit 0); every shell: value is read (a "
          "bare known non-python shell keyword is clean; a python template without isolation "
          "options is a non-isolated finding, on a step and under defaults.run at the workflow "
          "and job level through the run: mapping; every other shell: value, an isolation-"
          "claiming python template included, is cannot-evaluate); the quoted-key and "
          "node-property line forms refuse a key under any number of leading sequence dashes; "
          "every other line outside the closed line grammar (a flow mapping value, a flow "
          "sequence carrying a colon, a value opening with a node property, a malformed "
          "block-scalar header, a nested '- - ' entry, a spaced key, a non-key non-item line) "
          "is cannot-evaluate while a flow sequence of plain or quoted scalars stays clean; a "
          "generic key's block-scalar value is bounded by the KEY column (a sibling run: after "
          "a '- name: |' block, inside and outside a steps: block, is a line-named finding, "
          "never consumed content); the value on the line after an empty run: is governed by "
          "the same closed grammar (a mapping key of any spelling, an explicit-key indicator "
          "and a '- ' sequence item are cannot-evaluate; the mapping test reads ONLY an "
          "unquoted plain value's operative text before its ' #' comment, so a whole "
          "single-quoted 'echo foo: bar', a ': ' inside a trailing comment and an isolated "
          "launcher with a ': ' comment stay clean, and each sub-condition, a lone '-', a "
          "'-'+TAB item, a lone '?', a ':'+TAB and a trailing colon, is pinned by its own "
          "fixture); a sequence item at the run: key's own column after an empty run: "
          "(workflow and job defaults, a step, a comment-separated spelling, a lone '-' and a "
          "'-'+TAB item) is cannot-evaluate, never read as null; a leading shell keyword is "
          "skipped only in its literal spelling (a quoted or escaped if/then/do/time/'!' is a "
          "literal command name: the bare launcher after it is a cannot-evaluate with no "
          "finding emitted, bare coproc stays cannot-evaluate, and the unquoted spelling still "
          "skips); a shell separator splits only in its literal spelling (a quoted ';' is an "
          "argument: the bare and the -I launcher after it are each a cannot-evaluate with no "
          "finding, in a run: block and in a settings.json shell-string); a shell-level "
          "assignment is skipped only with its name and '=' unquoted (a quoted 'X=y' is a "
          "command name: the launcher after it is a cannot-evaluate with no finding, while "
          "X=\"a b\" and an env 'X=y' argument still skip); a raw/decoded tokenization "
          "disagreement injected at the tokenization boundary (a token-count and a same-count "
          "token-content disagreement) is refused through a run_all_checks.sh line and a "
          "settings.json shell-string, each with its own guard's message and no finding; "
          "and a shell: value is clean only "
          "when its comment-stripped scalar is itself a known shell keyword on one line (a "
          "quoted keyword, an embedded quote or backslash spelling, and a deeper continuation "
          "line are cannot-evaluate); "
          "every *.yml workflow candidate that is not a regular "
          "file (a symlink of any kind, a directory, or another non-regular entry) is a named "
          "cannot-evaluate (exit 2); and "
          "the gate refuses to run non-isolated (exit 2), with stderr on /dev/full and with descriptor "
          "2 closed too" + note)
    return 0


if __name__ == "__main__":
    sys.exit(main())
