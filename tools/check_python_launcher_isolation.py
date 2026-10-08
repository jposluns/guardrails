#!/usr/bin/env python3
"""Enforce isolated Python execution for hook and gate launchers (a deterministic gate).

A Python launcher puts its own script directory first on the interpreter's module path, so a file
written beside the launched script (a tool-authored ``json.py`` next to a hook dispatcher, an ``os.py``
next to a gate) can shadow a standard-library import and silently neuter the control. Python's isolated
mode (``-I``, or the complete ``-P -E -s`` equivalent) removes the script directory from that path and
ignores the ``PYTHON*`` environment variables, so a sibling can no longer shadow. This gate scans the launcher
configuration this repo ships and runs, and fails any direct ``python3`` launcher that is not isolated.

BOOTSTRAP SELF-GUARD. The gate's own first executable statements import only ``sys`` and refuse to run
(exit 2) unless the interpreter is itself isolated, so a sibling planted beside this gate cannot neuter
the gate before it can check anything. Only after that guard passes does it import the rest of the
stdlib and the sibling generator whose plugin path it tracks.

SCANNED SURFACES (the declared set, resolved from the repo root):
  - the generated plugin hook config plugin/aiqt-guardrails-hooks/hooks/hooks.json (required; the scan
    path is taken from gen_hooks so it tracks the generator),
  - tools/run_all_checks.sh (required),
  - every regular *.yml/*.yaml under .github/workflows/ (required directory),
  - .claude/settings.json and .claude/settings.local.json (optional; a truly-absent file is a clean
    skip, but a present-but-unusable one, including a dangling symlink, is a cannot-evaluate),
  - the QA-suite Python sources tools/_qa_adapter.py, tools/audit_reference.py, and
    tools/check_internal_names.py (required), scanned for a sys.path insertion at index 0 that would
    re-add the script directory AHEAD of the stdlib and re-enable the sibling-shadow class under -I; their
    sanctioned sibling-import form is sys.path.append.

SETTINGS SCOPE. A settings.json hook command is in scope only when it is a candidate python launcher:
it holds a python word or the core hook launcher's name once its quote characters, backslashes and
line continuations are removed (the CI-line prefilter, extended to that name), or one of its command
words can hide a python word, which is a cannot-evaluate (exit 2) naming the construct. A command word
can hide one when its file name (the part after its last literal ``/``) holds an expansion, a glob, a
brace or a ``~``, or its directory holds an expansion that word splitting can break apart (an unquoted
or non-simple one); a command or process substitution, a backtick and an unbalanced quote anywhere in
the command count too, because the scan that finds command words does not read inside them. That scan
over-reads command-word positions (the first word after ``;``, ``&``, ``|``, a newline, a parenthesis, a
shell keyword, ``{``, ``!``, ``VAR=val`` and ``env`` with its options). Any other settings hook command
is out of scope (neither pass nor fail), its redirections, ``~`` paths, groups and globs included, and
``"$CLAUDE_PROJECT_DIR"/.claude/hooks/x.sh`` with them.

SHELL GRAMMAR (an allow-list). A candidate settings.json hook command, and a CI shell line the gate
parses, is PARSED only if it lies entirely inside this grammar: words of the characters ``A-Z a-z 0-9 _ . / - + = ,
: @ %``, single-quoted strings, and double-quoted strings that contain no ``$``, backtick, backslash or
``!``, glued as a shell glues them; spaces and tabs between words; and the unquoted operators ``;``,
``;;``, ``|``, ``||``, ``&&`` and ``&`` (SEPARATORS), each of which ends a simple command. Inside it a shell
removes quotes and nothing else, so the words and the operator provenance the gate reads are the ones
the shell uses. ANY other construct makes that command a cannot-evaluate (exit 2) whose message names
it, never a parse by guesswork: a ``$`` anywhere outside single quotes (a parameter expansion, a command
or arithmetic substitution, ANSI-C ``$'`` or locale ``$"`` quoting), a backtick, a backslash (an escape or
a line continuation), an unquoted ``#``, a newline inside a command, a redirection, a heredoc or here-
string, a process substitution ``<(`` or ``>(``, a parenthesis or brace, an unquoted glob character
(``*``, ``?``, ``[``, ``]``), ``!``, ``~``, any other operator such as ``|&``, an unbalanced quote, and any
other character. A settings hook command also admits a double-quoted ``$NAME`` or ``${NAME}`` (a plain
name, no operator) as the leading path segment of a word that is neither the command word, nor before
it, nor an interpreter option or an option's value: the quote opens the word, the expansion follows it
directly, and a ``/`` follows the expansion (``python3 -I -S -B "$CLAUDE_PROJECT_DIR"/.claude/hooks/
aiqt_hooks_launch.py``, the form Claude Code's hook documentation shows), so the basename the rules read
stays literal; one per word, and any other expansion stays a cannot-evaluate. A CI shell line differs in
three ways: a trailing or whole-line comment is cut first
(exact inside the grammar: a ``#`` inside quotes leaves an open quote, which is refused); a double-quoted
``$NAME`` or ``${NAME}`` is also admitted, but only where no rule reads its value, after the script
operand of a python launcher or after the command word of another command (anywhere else it is a
cannot-evaluate); and a line is parsed only when it has a backslash or a ``python`` word once its quote
characters are removed (a line with neither holds no python launcher inside the grammar).

LAUNCHER PREDICATE. In a scanned command, a leading ``env`` and any ``VAR=val`` assignments are skipped;
the command word's basename must then match ``^python(3(\\.\\d+)?)?$`` to be a launcher (a non-python
command word is out of scope, neither pass nor fail, except that a hooks.json args list carrying a
python word fails closed). Interpreter options are the tokens after the command word up to the first
non-option, ``-m``, ``-c``, ``--``, or long ``--option``; single-dash clusters expand letter by letter,
and a ``-`` inside a cluster starts a long option there, as CPython reads it (``-I-check-hash-based-pycs
default`` is ``-I`` then ``--check-hash-based-pycs default``; a bare trailing ``-``, as in ``-IS- x.py``,
names no long option, and CPython prints "Expected long option" and ends the options there, so the next
token is the script operand, as after ``--``). ``--`` ends the options the way a
non-option does: the token after it is the script operand (``python3 -I -B -- x.py`` runs ``x.py``), so
a rule that reads the script operand still sees it. ``-c`` and ``-m`` terminate the option scan in every
form, separate (``-c CMD``, ``-m MOD``), attached (``-cCMD``, ``-mMOD``), or mid-cluster (``-Ic...``):
everything from that point is the command/module operand and is never letter-scanned, so an isolation
letter inside the operand (``-cIbar``) is never credited. The value-taking interpreter options ``-W``
and ``-X`` are recognized: their value is never letter-scanned, whether attached (``-Wxxx``, ``-Xxxx``,
the remainder is the value) or separate (``-W xxx``, ``-X xxx``, the next token is the value and is
skipped, not read as the script); so is the long ``--check-hash-based-pycs``, whose next token is its
value, standalone or at the end of a cluster. CPython takes no ``=VALUE`` form of a long option
(``--check-hash-based-pycs=always`` and ``-I-check-hash-based-pycs=always`` are unknown options, and it
exits 2 before running anything), so such a token, like any other long option, ends the scan with no
script operand. Only genuine valueless single-letter flags (``I``, ``P``, ``E``, ``s``, ``B`` and the
like) cluster and are letter-scanned. A launcher is isolated iff ``I`` is among those option letters, or
all of ``P``, ``E``, and ``s`` are. Options after the script are never credited, and environment
variables (PYTHONSAFEPATH and the like) are never credited.

NO-SITE RULE. Its scope is the two surfaces whose job is to launch hooks: the plugin hooks.json (the args
form) and a settings.json hook command (a shell-string). There, a python launcher any of whose tokens
after the interpreter word names the core hook launcher (gen_hooks.LAUNCHER_NAME, aiqt_hooks_launch.py)
as its basename or as a path component (a ``-c`` or ``-m`` operand included) is held to this rule, and it
PASSES only when the option scan reached the script operand cleanly (the first token after the
interpreter options, the token after ``--``, or the token after a cluster's bare trailing ``-``), that
operand names the launcher, and ``S`` is among the option letters before it (``-S``, alone or in a
cluster). A further mention after that operand is the launcher's own argument and cannot turn the site
module back on, so it is not a failure. Every other spelling FAILS, never falls out of scope: a scan that
stops early (``-m`` with a runner such as ``-m cProfile <launcher>``, ``-c``, an unrecognized or
``=VALUE`` long option, a long option inside a cluster other than ``-check-hash-based-pycs``) with the
launcher later in the argv, and the launcher named as an argument of another script, a benign tool that
only reads the launcher's path included (a deliberate restriction; lifting it needs a reviewed
exemption). On the CI surfaces (tools/run_all_checks.sh and a workflow ``run:`` line), whose lines
compile, hash or lint the launcher rather than launch hooks, the rule reads only the script operand: a
launcher whose script operand has the core hook launcher's basename must carry ``S`` before it, and a
mention anywhere else (``python3 -I -B -m py_compile <launcher>``, a hashing or lint script given the
launcher as its argument) is not held to it. Without ``-S`` the site module runs before the launcher's
first line, and with it every .pth file in the interpreter's site-packages (a PATH-selected project
virtual environment's included), sitecustomize and usercustomize, any of which can install a line trace
that ends a blocking hook with exit 0. ``-I`` does not exclude them. No option excludes the files
CPython reads to compute its startup configuration and module search path before the launcher's first
line (the ``PYTHON*`` environment variables, which ``-I`` ignores, aside): the interpreter binary and
the directory it sits in, a ._pth file there (``python3._pth`` beside the ``python3`` PATH selects
replaces the module search path and, with an ``import site`` line, turns the site module back on), a
pyvenv.cfg beside the binary or in the directory above it and the home it names, and the standard
library and zip locations those resolve to (examples, not an exhaustive list). That stays with whoever
can write those locations, not with this rule.

NAME, NOT IDENTITY. The rule matches the core hook launcher's NAME in the configured argv; it does not
establish which file the interpreter executes. A renamed copy or a symlink of the launcher under another
name (a glob such as ``aiqt_hooks_launc?.py`` or an ANSI-C quoted name in a shell string is outside the
shell grammar, so a cannot-evaluate), an alternate-case name on a case-insensitive filesystem, a
compiled ``.pyc`` of it, and its source fed on stdin by another command (``cat <launcher> | python3 -I
-B - x``) each pass without ``-S``, and a launcher that names no core hook launcher is not held to this
rule. A ``-h``, ``-?`` or ``-V`` letter, alone or in a cluster (``-ISh <launcher>``), makes CPython
print help or its version and exit 0 without running the hook, so a blocking hook registered that way is
silently allowed; the option scan credits such a cluster and this rule does not catch it.

EXIT CONVENTION: 0 every recognized launcher is isolated (and each core hook launcher also runs without
the site module); 1 at least one recognized launcher is not isolated, or a core hook launcher runs with
the site module; 2 cannot-evaluate (a required input missing, unreadable, non-regular, or malformed; a JSON
parse error; a candidate settings hook command, or a parsed CI shell line, outside the allow-listed
shell grammar; a settings hook command with a command word that can hide a python word;
a shell line carrying a python token whose command-word position cannot be established; a
hook entry with a missing type/command or a non-list args; an args-form hook entry whose argv carries a
python word that is not its command word, such as ``/usr/bin/env`` with ``python3`` in args, exactly as
a settings shell-string with such a word is; or the gate's own interpreter not isolated).
Diagnostics are deterministic, sorted by relative path then line then location.

DISCLOSED RESIDUAL (this gate does not catch): wrapper or indirect launchers (``bash -c "python3 ..."``,
a ``.sh`` re-launcher), ``$PYTHON`` and shell aliases or functions, a dynamically assembled argv, the
programmatic ``subprocess`` children in the tools and the ``regenerate`` strings in .aiqt/gensrc.json
(a separate follow-up), a python launched through a script's shebang (a settings hook command
``"$CLAUDE_PROJECT_DIR"/.claude/hooks/x.py``, or the core hook launcher's own path run directly), an
expansion outside every command word that a wrapper or a callee later runs (``exec "$PY"``, ``xargs
"$PY"``), the value of an admitted leading-path expansion (a value that begins with ``-`` is read by the
interpreter as an option; the gate credits only the literal options before it), a runtime ``sys.path`` mutation OTHER than a literal index-0 insertion in the
three enumerated QA-suite sources (a ``sys.path[0:0]`` slice, a computed index, a runtime-assembled
insertion, or one in another source is not caught, and even for those three ``-I`` cannot prevent a
runtime mutation the source performs), an unrecognized interpreter name, launcher configuration outside the enumerated surfaces,
YAML constructs beyond the supported line grammar (a multi-line quoted scalar, for example), a CI shell line
the prefilter skips (no backslash and no ``python`` word once its quotes are removed) that assembles a
python word through a construct outside the shell grammar (``pyt${E}hon3``, a command substitution, a glob
or a brace expansion), and the PATH provenance of ``python3``
itself, and every file CPython reads to compute its startup configuration and module search path
before the launcher's first line (the interpreter binary and its directory, a ._pth file there, a
pyvenv.cfg beside the binary or in the directory above it and the home it names, and the standard library
and zip locations those resolve to; examples, not an exhaustive list), which stays with whoever can write
those locations. The no-site rule matches the launcher's name, not executable identity (a renamed copy or
symlink, an alternate-case name on a case-insensitive filesystem, a ``.pyc`` or stdin-fed source passes
without ``-S``), and a ``-h``, ``-?`` or ``-V`` letter in the
registered cluster (``-ISh``) prints help or the version and exits 0 without running the hook. A
``python3`` token embedded in a quoted argument may be miscounted (a cannot-evaluate, never a pass), and
the lines of a heredoc body in run_all_checks.sh or a ``run:`` block are scanned as shell lines,
mirroring the roster-scan limit the enforceability ledger discloses.

  check_python_launcher_isolation.py             scan the declared surfaces
  check_python_launcher_isolation.py --self-test build synthetic trees and assert the gate's invariants

Run this gate isolated: python3 -I -B tools/check_python_launcher_isolation.py
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    sys.stderr.write(
        "error: check_python_launcher_isolation.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
        "Nothing was run (cannot evaluate).\n"
        % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
    raise SystemExit(2)

def _interpreter_isolated(flags):
    """True iff a sys.flags-like object reports isolated mode: the -I flag, or the full -P (safe_path,
    Python 3.11+) plus -E (ignore_environment) plus -s (no_user_site) equivalent. Anything short of that
    leaves the script directory able to shadow a stdlib import."""
    return bool(flags.isolated) or bool(
        getattr(flags, "safe_path", 0) and flags.ignore_environment and flags.no_user_site)


# The bootstrap self-guard: refuse to run non-isolated, BEFORE importing anything a sibling could shadow.
if not _interpreter_isolated(sys.flags):
    sys.stderr.write("check_python_launcher_isolation: refusing to run non-isolated; launch it as "
                     "`python3 -I -B tools/check_python_launcher_isolation.py` (a sibling file could "
                     "otherwise shadow a stdlib import and neuter this gate)\n")
    raise SystemExit(2)

import json  # noqa: E402  imported only after the isolation guard above
import os  # noqa: E402
import re  # noqa: E402
import shlex  # noqa: E402
import stat  # noqa: E402
from pathlib import Path  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    import gen_hooks  # noqa: E402  the generator whose plugin hooks.json path this gate tracks
except Exception as exc:  # noqa: BLE001  an import failure is a cannot-evaluate, not a traceback
    sys.stderr.write("check_python_launcher_isolation: cannot import gen_hooks ({}); fail-closed\n"
                     .format(exc))
    raise SystemExit(2)

HOOKS_JSON_REL = gen_hooks.HOOKS_JSON_REL
RUN_ALL_REL = "tools/run_all_checks.sh"
WORKFLOWS_REL = ".github/workflows"
OPTIONAL_SETTINGS = (".claude/settings.json", ".claude/settings.local.json")
REQUIRED_FORM = "-I (or the full -P -E -s) before the script"
# The NO-SITE RULE's scope and required form: a launcher of the core hook launcher must also run without
# the site module.
NO_SITE_SCRIPTS = frozenset((gen_hooks.LAUNCHER_NAME,))
NO_SITE_FORM = ("-S (no site module, so no site-packages .pth file, sitecustomize or usercustomize runs "
                "first) before the launcher")
NO_SITE_FAILURE = ("core hook launcher run with the site module, or named where the option scan does not "
                   "reach it as the script operand {!r}; requires {}, with the launcher as the script")

# The QA-suite Python sources whose sibling-import posture this gate keeps isolated. Each imports a sibling
# module (the QA adapter, the shared tree walk, the leak gate) and MUST do so with sys.path.append, never a
# sys.path insertion at index 0: under `python3 -I` an index-0 insertion re-adds the script's own directory
# AHEAD of the stdlib, so a sibling json.py/hashlib.py (raise SystemExit(0)) can shadow a stdlib import and
# silently neuter the self-test. These sources are REQUIRED (a missing one is a cannot-evaluate).
PYSOURCE_ISOLATION_REL = ("tools/_qa_adapter.py", "tools/audit_reference.py", "tools/check_internal_names.py")
# A sys.path insertion at index 0 (`sys.path.insert(0, ...)`, also spelled with whitespace); the sanctioned
# form for the sources above is sys.path.append, which preserves stdlib precedence.
SYS_PATH_FRONT_INSERT_RE = re.compile(r"sys\.path\.insert\(\s*0\b")

PY_WORD_RE = re.compile(r"^python(3(\.\d+)?)?$")     # a whole token that is a python interpreter name
PY_TOKEN_RE = re.compile(r"\bpython3?\b")            # a python word anywhere in a line (a fast pre-filter)
QUOTES = str.maketrans("", "", "'\"")               # removes the quote characters a shell strips from a word
ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")  # a shell VAR=val inline assignment
# YAML `run:` key, optionally under a `- ` sequence dash; captures the leading indent and the scalar value.
RUN_RE = re.compile(r"^(?P<indent>\s*)(?:-\s+)?run:(?:[ \t]+(?P<val>.*))?$")
BLOCK_INDICATORS = {"|", ">", "|-", ">-", "|+", ">+"}
# The only shell operators the allow-listed grammar (_shell_split) admits: each ends one simple command
# and begins the next when it was unquoted in the source (a _ShellOperator); the same text quoted is an
# argument. A maximal unquoted run of `;`, `|` and `&` that is not one of these is outside the grammar.
SEPARATORS = {";", ";;", "|", "||", "&&", "&"}
# Shell control keywords that may lead a simple command; skipped so the command word after them is found.
LEADING_KEYWORDS = {"if", "then", "else", "elif", "fi", "do", "done", "while", "until", "for", "time"}


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


def _strip_launcher_prefix(tokens):
    """Return the tokens from the command word onward: skip leading shell keywords, a `run_gate <label>`
    wrapper (the run_all_checks.sh idiom), and a leading `env` or any `VAR=val` assignments."""
    i = 0
    while i < len(tokens) and tokens[i] in LEADING_KEYWORDS:
        i += 1
    if i < len(tokens) and tokens[i] == "run_gate":
        i += 1
        if i < len(tokens):  # skip the gate label argument
            i += 1
    while i < len(tokens) and (tokens[i] == "env" or ASSIGN_RE.match(tokens[i])):
        i += 1
    return tokens[i:]


VALUE_SHORT_OPTS = frozenset("WX")               # short options that take a value (-Wxxx or -W xxx)
VALUE_LONG_OPTS = frozenset({"--check-hash-based-pycs"})  # long options that take a separate value
# A token that names a NO_SITE_SCRIPTS launcher as a path component: split on every character that is
# not part of a file name, so a -c/-m operand (`-c "runpy.run_path('/p/aiqt_hooks_launch.py')"`) names
# it as well as a plain path does. `-` stays inside a component (it is a file-name character), so a name
# glued to option letters (`-caiqt_hooks_launch.py`) is one component that does not match; an attached
# -cCMD names the launcher only where a non-name character (a quote, a slash, a parenthesis) sets it apart.
NAME_COMPONENT_SPLIT_RE = re.compile(r"[^A-Za-z0-9_.\-]+")


def _option_scan(after_interpreter):
    """Collect the interpreter option letters from the tokens after the command word, stopping at the
    first non-option, `-m`, `-c`, `--`, or an unrecognized long `--option`, and expanding a single-dash
    cluster letter by letter. A value-taking option (`-W`/`-X`, attached or separate, and the long
    `--check-hash-based-pycs`) has its value skipped rather than letter-scanned, so an isolation letter is
    never forged from a value and a real flag after a separate value is never missed. Returns (the set
    of option letters, the index of the script token in after_interpreter, or None when the scan stops
    at `-m`, `-c`, an unrecognized long option or the end of the tokens). `--` ends the options but,
    unlike `-m` and `-c`, is followed by the script operand itself, so its index is the token after
    `--` (None only when `--` is the last token). A `-` inside a cluster starts a long option, as
    CPython 3.14 reads it: `-I-check-hash-based-pycs` is `-I` plus the long option, whose value is the
    next token. A bare trailing `-` (`-I-`, `-IS-`) names no long option: CPython prints "Expected long
    option" and ends the options there, so the next token is the script operand, exactly as after `--`
    (the letters before the `-` still count). Any other long option inside a cluster stops the scan
    with no script operand: it is an unknown option (exit 2; an `=VALUE` form included, which CPython
    does not accept, and `-help` and `-version`) or one of `-help-all`, `-help-env` and
    `-help-xoptions` (help printed, exit 0). A third value is the index of the first token that is
    neither an interpreter option nor an option's value: the script operand, the token after a separate
    `-c`/`-m` or after one that carries `-c`/`-m` (its operand or first argument), or the token count
    when the scan stops at an unrecognized long option or runs out of tokens (every token is then read
    as an option)."""
    flags = set()
    tokens = list(after_interpreter)
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok == "--":
            return flags, (i + 1 if i + 1 < len(tokens) else None), i + 1
        if tok in ("-m", "-c"):
            return flags, None, i + 1
        if tok in VALUE_LONG_OPTS:
            i += 2                       # skip the long option and its separate value token
            continue
        if tok.startswith("--"):
            return flags, None, len(tokens)  # an unrecognized long interpreter option: not credited
        if tok.startswith("-") and len(tok) > 1:
            j = 1
            skip_next = False
            terminate = False
            while j < len(tok):
                letter = tok[j]
                if letter == "-":
                    # A long option inside a cluster (`-I-check-hash-based-pycs default`): CPython
                    # reads the rest of the token as the long option's name. A bare trailing `-` names
                    # none: CPython prints "Expected long option" and ends the options, so the next
                    # token is the script operand, as after `--`. The only long option that runs a
                    # script takes the next token as its value; anything else is an unknown option
                    # (exit 2) or a help option (`-help-all`, exit 0), so no script operand is reached.
                    if j == len(tok) - 1:
                        return flags, (i + 1 if i + 1 < len(tokens) else None), i + 1
                    if tok[j + 1:] in (opt[2:] for opt in VALUE_LONG_OPTS):
                        skip_next = True
                        break
                    return flags, None, len(tokens)
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
                return flags, None, i + 1
            i += 2 if skip_next else 1
            continue
        return flags, i, i               # the script/program token: options end here
    return flags, None, len(tokens)


def _option_letters(after_interpreter):
    """(the option letters, the script operand index) of _option_scan."""
    return _option_scan(after_interpreter)[:2]


def _flags_isolated(after_interpreter):
    """Isolated iff the option letters (_option_letters) hold `I`, or all of `P`, `E`, `s`."""
    flags = _option_letters(after_interpreter)[0]
    return ("I" in flags) or {"P", "E", "s"}.issubset(flags)


def _names_no_site_script(token):
    """True iff the token names a NO_SITE_SCRIPTS launcher: as its basename, or as a path component
    anywhere in it (a -c/-m operand, an attached -cCMD, a quoted runpy call)."""
    return (_basename(token) in NO_SITE_SCRIPTS
            or any(part in NO_SITE_SCRIPTS for part in NAME_COMPONENT_SPLIT_RE.split(token)))


def _no_site_verdict(rest, launch_surface):
    """The NO-SITE RULE for one launcher (rest: the command word onward). On a hook-launching surface
    (launch_surface: the plugin hooks.json, a settings.json hook command), None when no token after the
    command word names a NO_SITE_SCRIPTS launcher (out of scope); otherwise True only when the option
    scan reached the script operand cleanly, that operand names the launcher, and `S` is among the option
    letters before it. Every other case there is False (a FAIL, never out of scope): a scan that stops
    early (-m, -c, an unrecognized or =VALUE long option, a non-value long option inside a cluster) with
    the launcher later in the argv, or the launcher named only where the script operand is not. A
    further mention after a compliant operand is the launcher's own argument: the site module is decided
    by the options before the operand, so it is not a failure. On a CI surface (run_all_checks.sh, a
    workflow run: line), which compiles, hashes or lints the launcher rather than launching hooks, only
    the script operand is read: None unless it has a NO_SITE_SCRIPTS basename, else True iff `S` is
    among the option letters before it."""
    tokens = rest[1:]
    flags, at = _option_letters(tokens)
    if not launch_surface:
        if at is None or _basename(tokens[at]) not in NO_SITE_SCRIPTS:
            return None
        return "S" in flags
    if not any(_names_no_site_script(tok) for tok in tokens):
        return None
    return at is not None and _names_no_site_script(tokens[at]) and "S" in flags


def check_argv(argv):
    """Classify one already-split command word list. Returns None if it is not a python launcher (out of
    scope), True if isolated, False if a python launcher that is not isolated."""
    rest = _strip_launcher_prefix(argv)
    if not rest or not _is_py_word(rest[0]):
        return None
    return _flags_isolated(rest[1:])


def _segments(tokens):
    """Split a token list into simple-command segments at shell separator tokens. Only a token that was
    an UNQUOTED operator in the source (a _ShellOperator from _shell_split) separates: a quoted `;`,
    `;;`, `|`, `||`, `&&` or `&` is an ordinary argument of the command it sits in, as a real shell reads
    it, so it can never cut a launcher off from the arguments after it."""
    segments, cur = [], []
    for tok in tokens:
        if isinstance(tok, _ShellOperator) and tok in SEPARATORS:
            if cur:
                segments.append(cur)
                cur = []
        else:
            cur.append(tok)
    if cur:
        segments.append(cur)
    return segments


def _strip_comment(line):
    """Drop a shell trailing comment (a space- or tab-preceded `#`) and a whole-line comment from a CI
    shell line, the lexical cut the enforceability roster scan uses. For a line inside the allow-listed
    grammar (_shell_split, which has no escape) the cut is exact: a `#` after a space or tab outside
    quotes starts a comment, and one inside quotes leaves the kept text ending inside an open quote,
    which _shell_split refuses as an unbalanced quote (cannot-evaluate), never parses. A settings hook
    command is not cut: an unquoted `#` there is outside the grammar."""
    code = re.split(r"[ \t]#", line, maxsplit=1)[0]
    if code.lstrip().startswith("#"):
        return ""
    return code


class _ShellOperator(str):
    """A token that was an UNQUOTED shell operator in the source line. Only _shell_split makes one, and
    only one of these ends a simple command (_segments): after the quoting is removed, a quoted `';'`
    and a real `;` are the same text, so the provenance travels with the token instead."""
    __slots__ = ()


class _ExpandedWord(str):
    """A word holding a double-quoted parameter expansion (`"$NAME"`, `"${NAME}"`), admitted on a CI
    shell line only (_shell_split with expansions=True). Its value is unknown to the gate, so the word
    keeps its source spelling and _scan_command_tokens accepts it only where no rule reads it: after
    the script operand of a python launcher, or after the command word of any other command."""
    __slots__ = ()


class _OutsideGrammar(ValueError):
    """A shell string that does not lie entirely inside the allow-listed grammar (_shell_split). The
    message names the construct; the caller reports it as a cannot-evaluate, never a parse by guess."""


# The allow-listed shell grammar (_shell_split). A word is a run of WORD_CHARS, single-quoted strings
# and double-quoted strings, glued together; nothing else is a word character.
WORD_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_./-+=,:@%")
OPERATOR_CHARS = frozenset(";|&")
# A double-quoted parameter expansion admitted on a CI shell line: `$NAME` or `${NAME}`, nothing more.
DQ_EXPANSION_RE = re.compile(r"\$(?:[A-Za-z_][A-Za-z0-9_]*|\{[A-Za-z_][A-Za-z0-9_]*\})")
# The _shell_split expansions mode of a settings hook command: a double-quoted `$NAME` or `${NAME}` is
# admitted only as the leading path segment of a word (`"$CLAUDE_PROJECT_DIR"/.claude/hooks/x.py`).
LEADING_PATH = "leading-path"
# The construct an unquoted character outside the grammar starts, named in the cannot-evaluate message.
OUTSIDE_GRAMMAR_NAMES = (
    ("$'", "ANSI-C quoting $'...'"), ('$"', 'locale quoting $"..."'),
    ("$((", "an arithmetic expansion $((...))"), ("$(", "a command substitution $(...)"),
    ("${", "a parameter expansion ${...}"), ("$", "a $ expansion"),
    ("`", "a backtick command substitution"), ("\\\n", "a backslash-newline line continuation"),
    ("\\", "a backslash escape"), ("\n", "a newline inside a command"), ("#", "an unquoted # (a comment)"),
    ("<(", "a process substitution <(...)"), (">(", "a process substitution >(...)"),
    ("<<<", "a here-string <<<"), ("<<", "a heredoc <<"), ("<", "a redirection"), (">", "a redirection"),
    ("(", "a parenthesis (a subshell or group)"), (")", "a parenthesis (a subshell or group)"),
    ("{", "a brace (a group or brace expansion)"), ("}", "a brace (a group or brace expansion)"),
    ("*", "an unquoted glob character *"), ("?", "an unquoted glob character ?"),
    ("[", "an unquoted glob character ["), ("]", "an unquoted glob character ]"),
    ("!", "an unquoted ! (negation or history expansion)"), ("~", "a tilde expansion"))


def _outside(s, i, what=None):
    """Raise _OutsideGrammar naming the construct that starts at s[i]."""
    if what is None:
        what = next((name for lead, name in OUTSIDE_GRAMMAR_NAMES if s.startswith(lead, i)),
                    "the character {!r}".format(s[i]))
    raise _OutsideGrammar("outside the allow-listed shell grammar: {} at offset {}".format(what, i))


def _leads_path(s, quote, at, end, in_word):
    """True iff the double-quoted expansion s[at:end] opens its word (the word starts at the opening
    quote s[quote] and the expansion follows that quote directly) and a `/` follows it, inside the same
    quotes or straight after the closing quote: a leading path segment, so the rest of the word, its
    basename included, is literal."""
    return (not in_word and at == quote + 1
            and (s.startswith("/", end) or s.startswith('"/', end)))


def _shell_split(s, expansions=False):
    """Split a shell string into words and operators ONLY when it lies entirely inside an ALLOW-LISTED
    grammar; raise _OutsideGrammar (a ValueError) naming the first construct outside it. The grammar:
    words made of WORD_CHARS (letters, digits and `_ . / - + = , : @ %`), single-quoted strings, and
    double-quoted strings that contain no `$`, backtick, backslash or `!`, glued into one word as a
    shell glues them; spaces and tabs between words; and the unquoted operators in SEPARATORS (a
    maximal run of `;`, `|` and `&` must be one of them). Each operator is returned as a
    _ShellOperator, each word with its quoting removed. ANY other construct is refused, never parsed by
    guesswork: a `$` outside single quotes (an expansion, a substitution, ANSI-C `$'` or locale `$"`
    quoting), a backtick, a backslash (an escape or a line continuation), an unquoted `#`, a newline,
    a redirection, a heredoc or here-string, a process substitution, a parenthesis or brace, an
    unquoted glob character (`*`, `?`, `[`, `]`), `!`, `~`, an unbalanced quote, or any other
    character. Inside the grammar a shell removes quotes and nothing else, so the words and the
    operator provenance returned are the ones the shell uses. With expansions=True (a CI shell line) a
    double-quoted `$NAME` or `${NAME}` is also admitted, and with expansions=LEADING_PATH (a settings
    hook command) one is admitted only as the leading path segment of a word (_leads_path, at most one
    per word); either way the word holding it is returned as an _ExpandedWord for the caller to place."""
    tokens = []
    word, in_word, expanded = [], False, False
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if c in (" ", "\t"):
            if in_word:
                tokens.append((_ExpandedWord if expanded else str)("".join(word)))
                word, in_word, expanded = [], False, False
            i += 1
            continue
        if c in OPERATOR_CHARS:
            if in_word:
                tokens.append((_ExpandedWord if expanded else str)("".join(word)))
                word, in_word, expanded = [], False, False
            j = i
            while j < n and s[j] in OPERATOR_CHARS:
                j += 1
            if s[i:j] not in SEPARATORS:
                _outside(s, i, "the operator {!r}, which is not a listed separator".format(s[i:j]))
            tokens.append(_ShellOperator(s[i:j]))
            i = j
            continue
        if c == "'":
            close = s.find("'", i + 1)
            if close < 0:
                _outside(s, i, "an unbalanced single quote")
            if "\n" in s[i + 1:close]:
                _outside(s, s.index("\n", i + 1))
            word.append(s[i + 1:close])
            in_word = True
            i = close + 1
            continue
        if c == '"':
            j = i + 1
            while j < n and s[j] != '"':
                if s[j] == "$" and expansions:
                    m = DQ_EXPANSION_RE.match(s, j)
                    if m and (expansions != LEADING_PATH or _leads_path(s, i, j, m.end(), in_word)):
                        word.append(m.group(0))
                        expanded = True
                        j = m.end()
                        continue
                if s[j] in "$`\\!\n":
                    leads = ("$((", "$(", "${", "`", "\\\n", "\\", "\n") if s[j] != "!" else ()
                    what = next((name for lead, name in OUTSIDE_GRAMMAR_NAMES
                                 if lead in leads and s.startswith(lead, j)),
                                "a $ expansion" if s[j] == "$" else "a ! (history expansion)")
                    if s[j] == "$" and expansions == LEADING_PATH:
                        what += (" inside double quotes (a settings hook command admits a double-quoted "
                                 "$NAME or ${NAME} only as the leading path segment of a word)")
                    else:
                        what += " inside double quotes"
                    _outside(s, j, what)
                word.append(s[j])
                j += 1
            if j >= n:
                _outside(s, i, "an unbalanced double quote")
            in_word = True
            i = j + 1
            continue
        if c not in WORD_CHARS:
            _outside(s, i)
        word.append(c)
        in_word = True
        i += 1
    if in_word:
        tokens.append((_ExpandedWord if expanded else str)("".join(word)))
    return tokens


# The SCOPE of a settings hook command. Only a candidate python launcher is parsed with the allow-listed
# grammar: one holding a python word or a NO_SITE_SCRIPTS name once its quoting is removed
# (_unquoted_text), or one whose command word can hide a python word (_command_word_hazard, a
# cannot-evaluate). Any other settings hook command is out of scope, its redirections, groups, globs and
# `~` paths included.
SCAN_LITERAL, SCAN_SIMPLE, SCAN_EXPANSION, SCAN_PATTERN = "lit", "dq-name", "expansion", "pattern"
# The words after which the next word is still in a command-word position for _command_word_hazard: the
# shell keywords the launcher predicate skips, the group and negation words, and `env`.
COMMAND_POSITION_WORDS = LEADING_KEYWORDS | {"{", "}", "!", "env"}
# A command word that is the test builtin: a lone `[` is no bracket expression, so it is literal.
TEST_WORDS = {"[", "[["}
NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _unquoted_text(s):
    """s with every line continuation, backslash and quote character removed: a python word or a
    NO_SITE_SCRIPTS name spelled with quotes or escapes (`pyt"hon"3`, `pyt\\hon3`, `pyt\\<newline>hon3`)
    reads here as the shell reads it once its quoting is removed."""
    return s.replace("\\\n", "").replace("\\", "").translate(QUOTES)


def _is_candidate_text(s):
    """True iff the settings hook command s holds a python word or a NO_SITE_SCRIPTS name once its
    quoting is removed (the CI-line prefilter, extended to the core hook launcher's name)."""
    text = _unquoted_text(s)
    return bool(PY_TOKEN_RE.search(text)) or any(name in text for name in NO_SITE_SCRIPTS)


def _dollar_construct(s, j, quoted):
    """Read the `$` construct at s[j] for _command_word_hazard. Return (kind, end): a `$NAME` or
    `${NAME}` (SCAN_SIMPLE inside double quotes, else SCAN_EXPANSION, which word splitting can break
    apart), a special parameter, an operator-free `${...}`, ANSI-C `$'...'` or a locale `$"` prefix
    (SCAN_EXPANSION), or a lone `$` (SCAN_LITERAL). Return a str naming a construct the scan does not
    read: a command substitution or arithmetic expansion, or a `${...}` holding a quote, a backslash,
    a brace or another expansion."""
    m = DQ_EXPANSION_RE.match(s, j)
    if m:
        return (SCAN_SIMPLE if quoted else SCAN_EXPANSION), m.end()
    nxt = s[j + 1:j + 2]
    if nxt == "(":
        return "a command substitution or arithmetic expansion $(...)"
    if nxt == "{":
        close = s.find("}", j + 2)
        if close < 0 or any(ch in s[j + 2:close] for ch in "\"'$`\\{"):
            return "a ${...} expansion the command-word scan does not read"
        return SCAN_EXPANSION, close + 1
    if nxt and nxt in "@*#?-$!0123456789":
        return SCAN_EXPANSION, j + 2
    if not quoted and nxt == "'":
        k = j + 2
        while k < len(s) and s[k] != "'":
            k += 2 if s[k] == "\\" else 1
        if k >= len(s):
            return "an unbalanced ANSI-C quote $'"
        return SCAN_EXPANSION, k + 1
    if not quoted and nxt == '"':
        return SCAN_EXPANSION, j + 1        # a locale $"...": the string after it is read as usual
    return SCAN_LITERAL, j + 1


def _file_name_hazard(atoms):
    """For one command word (its atoms, each (text, kind)): None when its file name, the part after its
    last literal `/`, is wholly literal and its directory holds no expansion that word splitting can
    break apart; otherwise a str naming the hazard. A double-quoted `$NAME`, a glob, a brace or a leading
    `~` in the directory cannot change the literal file name, so a python word cannot hide there."""
    slash = max((k for k, (t, kind) in enumerate(atoms) if t == "/" and kind == SCAN_LITERAL), default=-1)
    if any(kind != SCAN_LITERAL for _, kind in atoms[slash + 1:]):
        return "an expansion, glob, brace or ~ in a command word's file name, where a python word could hide"
    if any(kind == SCAN_EXPANSION for _, kind in atoms[:slash + 1]):
        return ("an unquoted or non-simple expansion in a command word's directory, which word splitting "
                "can break into a python command word")
    return None


def _command_word_hazard(s):
    """For a settings hook command that is not a candidate by its text (_is_candidate_text), return None
    when none of its command words can hide a python word, else a str naming the construct that can (the
    command is then a candidate, and a cannot-evaluate). A lenient lexer, NOT the allow-listed grammar: it
    reads redirections, groups, globs, `~` and expansions only far enough to find each command word, and
    it over-reads command-word positions rather than under-reads them (the first word after `;`, `&`,
    `|`, a newline, a parenthesis, a shell keyword, `{`, `!`, an assignment, or `env` and its options;
    a redirection's target is never one). A command word is a hazard per _file_name_hazard. A construct
    the lexer does not read is a hazard wherever it is: a command or process substitution, a backtick, a
    `${...}` holding a quote, a backslash, a brace or a `$`, and an unbalanced quote."""
    tokens = []                             # a word as (atoms, is_redirect_target); None ends a command
    atoms, started, target = [], False, False
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if c in " \t\n;&|()<>":
            if started:
                if not (c in "<>" and all(k == SCAN_LITERAL and t.isdigit() for t, k in atoms)):
                    tokens.append((atoms, target))   # (a digit word before `<`/`>` is its descriptor)
                    target = False
                atoms, started = [], False
            if c in "<>":
                if s.startswith("(", i + 1):
                    return "a process substitution"
                j = i
                while j < n and s[j] in "<>&|":
                    j += 1
                if s.startswith("-", j) and s[i:j] == "<<":
                    j += 1
                target, i = True, j
                continue
            if c not in " \t":
                tokens.append(None)
                target = False
            i += 1
            continue
        if c == "#" and not started:
            end = s.find("\n", i)
            i = n if end < 0 else end
            continue
        if c == "\\":
            if not s.startswith("\n", i + 1):
                atoms.append((s[i + 1:i + 2] or c, SCAN_LITERAL))
                started = True
            i += 2
            continue
        if c == "'":
            close = s.find("'", i + 1)
            if close < 0:
                return "an unbalanced single quote"
            atoms.extend((ch, SCAN_LITERAL) for ch in s[i + 1:close])
            started, i = True, close + 1
            continue
        if c == '"':
            j = i + 1
            while j < n and s[j] != '"':
                if s[j] == "\\" and j + 1 < n and s[j + 1] in '$`"\\\n':
                    if s[j + 1] != "\n":
                        atoms.append((s[j + 1], SCAN_LITERAL))
                    j += 2
                    continue
                if s[j] == "`":
                    return "a backtick command substitution"
                if s[j] == "$":
                    read = _dollar_construct(s, j, True)
                    if isinstance(read, str):
                        return read
                    atoms.append((s[j:read[1]], read[0]))
                    j = read[1]
                    continue
                atoms.append((s[j], SCAN_LITERAL))
                j += 1
            if j >= n:
                return "an unbalanced double quote"
            started, i = True, j + 1
            continue
        if c == "`":
            return "a backtick command substitution"
        if c == "$":
            read = _dollar_construct(s, i, False)
            if isinstance(read, str):
                return read
            atoms.append((s[i:read[1]], read[0]))
            started, i = True, read[1]
            continue
        if c in "*?[{}" or (c == "~" and not started):
            atoms.append((c, SCAN_PATTERN))
        else:
            atoms.append((c, SCAN_LITERAL))
        started = True
        i += 1
    if started:
        tokens.append((atoms, target))
    expect, after_env = True, False         # the next word is in a command-word position
    for tok in tokens:
        if tok is None:
            expect, after_env = True, False
            continue
        word, is_target = tok
        if is_target or not expect:
            continue
        text = "".join(t for t, _ in word)
        kinds = {kind for _, kind in word}
        if text in COMMAND_POSITION_WORDS and kinds <= {SCAN_LITERAL, SCAN_PATTERN}:
            after_env = text == "env"
            continue
        if after_env and kinds == {SCAN_LITERAL} and text.startswith("-"):
            continue
        eq = next((k for k, (t, kind) in enumerate(word) if t == "=" and kind == SCAN_LITERAL), 0)
        if (eq and all(kind == SCAN_LITERAL for _, kind in word[:eq])
                and NAME_RE.match("".join(t for t, _ in word[:eq]))):
            continue                         # a VAR=val assignment: the command word comes after it
        expect, after_env = False, False
        if text in TEST_WORDS and kinds <= {SCAN_LITERAL, SCAN_PATTERN}:
            continue
        hazard = _file_name_hazard(word)
        if hazard:
            return "{} ({!r})".format(hazard, text)
    return None


def _scan_command_tokens(rel, lineno, loc, tokens, source_repr, errors, failures, launch_surface):
    """Scan an already-split token list for python launchers across its simple-command segments. Every
    python word must resolve to a command-word position; if the launcher count does not match the python
    words present, a launcher is hidden in an unexpected construct and this is a cannot-evaluate, so it is
    never silently passed. Each recognized launcher that is not isolated is a failure, and so is one the
    NO-SITE RULE refuses (launch_surface: True for a settings.json hook command, False for a CI shell
    line, where only the script operand is read). An _ExpandedWord (a double-quoted `$NAME` admitted by
    _shell_split) is accepted only after the command word of a non-python command, or in a python
    launcher after its interpreter options: on a CI line only after the script operand (where no rule
    reads it), in a settings hook command from the script operand on (where it is a leading path
    segment, so the basename the rules read is literal). At or before the command word, or among the
    interpreter options and their values, it is a cannot-evaluate."""
    py_word_count = sum(1 for t in tokens if _is_py_word(t))
    launchers = []
    for seg in _segments(tokens):
        rest = _strip_launcher_prefix(seg)
        expanded = [k for k, t in enumerate(seg) if isinstance(t, _ExpandedWord)]
        if expanded and expanded[0] <= len(seg) - len(rest):
            errors.append((rel, lineno, loc, "a parameter expansion at or before the command word, whose "
                           "value the gate cannot know: {!r}".format(source_repr)))
            return
        if rest and _is_py_word(rest[0]):
            _, at, end = _option_scan(rest[1:])
            first = len(seg) - len(rest) + 1   # the index in seg of the first token after the command word
            if expanded and (expanded[0] < first + end if launch_surface
                             else at is None or expanded[0] <= first + at):
                errors.append((rel, lineno, loc, "a parameter expansion among the interpreter options{}, "
                               "whose value the gate cannot know: {!r}".format(
                                   "" if launch_surface else " or in the script operand", source_repr)))
                return
            launchers.append(rest)
    if len(launchers) != py_word_count:
        errors.append((rel, lineno, loc, "a python token is not in a resolvable command-word position: "
                       "{!r}".format(source_repr)))
        return
    for rest in launchers:
        if not _flags_isolated(rest[1:]):
            failures.append((rel, lineno, loc, "non-isolated launcher {!r}; requires {}"
                             .format(" ".join(rest), REQUIRED_FORM)))
        if _no_site_verdict(rest, launch_surface) is False:
            failures.append((rel, lineno, loc, NO_SITE_FAILURE.format(" ".join(rest), NO_SITE_FORM)))


def _check_shell_line(rel, lineno, line, errors, failures):
    """Scan one shell line (from run_all_checks.sh or a workflow `run:` scalar) for python launchers.
    The line is PARSED only when it lies entirely inside the allow-listed grammar (_shell_split, with a
    double-quoted `$NAME` or `${NAME}` also admitted where no rule reads it); any other construct is a
    cannot-evaluate naming it. The decision is made on the DECODED command words, exactly as the
    settings path does via the shared `_scan_command_tokens`, so a quote-obfuscated launcher NAME that
    bash resolves to a real interpreter (`pyt"hon"3`, `py'thon'3`) is caught, and a backslash-spelled one
    (`pyt\\hon3`) is refused. Fail-closed: a line whose python token cannot be resolved to a command word
    is a cannot-evaluate too.

    The raw prefilter decides which lines are parsed: a line is skipped only when it has no backslash
    and no `python` word once its quote characters are removed. Inside the grammar a shell removes
    quotes and nothing else, so a skipped line holds no python launcher there; a backslash (which can
    split a name across a line continuation) always sends the line to the parser, which refuses it. A
    skipped line that would assemble a python word through a construct outside the grammar (a parameter
    expansion such as `pyt${E}hon3`, a command substitution, a glob, a brace expansion) is not refused:
    that is the disclosed CI-line residual."""
    code = _strip_comment(line)
    if "\\" not in code and not PY_TOKEN_RE.search(code.translate(QUOTES)):
        return
    try:
        tokens = _shell_split(code, expansions=True)
    except ValueError as exc:
        errors.append((rel, lineno, "", "shell line may carry a python launcher; fail-closed: {}"
                       .format(exc)))
        return
    _scan_command_tokens(rel, lineno, "", tokens, code.strip(), errors, failures, False)


def _yaml_run_lines(text):
    """Yield (lineno, shell_line) for every YAML `run:` scalar and every line inside a `run: |`/`run: >`
    block. A line-lexical extraction (no YAML library): a block's content is the run of lines indented
    deeper than the `run:` key; it ends at the first non-blank line indented no deeper. Only `run:`
    payloads are yielded, so `uses:`/`with:` lines (setup-python and the like) are never scanned."""
    lines = text.splitlines()
    out = []
    i, n = 0, len(lines)
    while i < n:
        m = RUN_RE.match(lines[i])
        if not m:
            i += 1
            continue
        val = (m.group("val") or "").strip()
        key_indent = len(m.group("indent"))
        if val in BLOCK_INDICATORS:
            j = i + 1
            while j < n:
                content = lines[j]
                if content.strip() == "":
                    j += 1
                    continue
                if len(content) - len(content.lstrip(" ")) <= key_indent:
                    break
                out.append((j + 1, content))
                j += 1
            i = j
            continue
        if val:
            if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
                val = val[1:-1]
            out.append((i + 1, val))
        i += 1
    return out


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
                    if verdict is None and any(_is_py_word(a) for a in argv):
                        # A python word that is not the command word (`/usr/bin/env` with `python3` in
                        # args): fail closed exactly as a settings shell-string with such a word does.
                        errors.append((rel, 0, hloc, "a python token is not in a resolvable command-word "
                                       "position: {!r}".format(" ".join(argv))))
                        continue
                    if verdict is False:
                        failures.append((rel, 0, hloc, "non-isolated launcher {!r}; requires {}"
                                         .format(" ".join(argv), REQUIRED_FORM)))
                    if (verdict is not None
                            and _no_site_verdict(_strip_launcher_prefix(argv), True) is False):
                        failures.append((rel, 0, hloc, NO_SITE_FAILURE.format(" ".join(argv),
                                                                             NO_SITE_FORM)))
                else:
                    # The settings form: a shell string that may chain commands (e.g. `prep && python3
                    # x.py`), so segment-split and check every launcher segment, not just the first.
                    # Only a candidate python launcher is in scope (_is_candidate_text, or a command
                    # word that can hide a python word, which is a cannot-evaluate). A candidate is
                    # parsed only inside the allow-listed grammar (_shell_split, with a double-quoted
                    # `$NAME` admitted as a leading path segment): anything else, a `$` expansion
                    # elsewhere, a backtick, a backslash, a `#`, a newline, a redirection included, is
                    # a cannot-evaluate naming the construct.
                    if not _is_candidate_text(command):
                        hazard = _command_word_hazard(command)
                        if hazard is not None:
                            errors.append((rel, 0, hloc, "command string: a command word that may hide a "
                                           "python launcher; fail-closed: {}".format(hazard)))
                        continue             # no python word and no hidden one: out of scope
                    try:
                        tokens = _shell_split(command, expansions=LEADING_PATH)
                    except ValueError as exc:
                        errors.append((rel, 0, hloc, "command string: {}".format(exc)))
                        continue
                    _scan_command_tokens(rel, 0, hloc, tokens, command, errors, failures, True)


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
        return path.read_text(encoding="utf-8")
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
    """Scan every regular *.yml/*.yaml under .github/workflows/. The directory is required: an
    unreadable or absent directory is a cannot-evaluate, never an empty clean scan."""
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
            if not path.is_file():
                continue  # a directory or special file named *.yml is not a workflow document
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            errors.append((rel, 0, "", "cannot read workflow: {}".format(exc)))
            continue
        for lineno, line in _yaml_run_lines(text):
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
        print("RESULT: {} launcher finding(s): non-isolated, or a core hook launcher run with the site "
              "module".format(len(failures)))
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
#   7. malformed JSON, a non-list args, and an unreadable required input each fail closed (exit 2),
#   8. the run_gate shell grammar, an inline `run:` scalar, and a `run: |` block are all recognized
#      (a bare launcher in each fails, its isolated form passes),
#   9. an optional settings.json shell-string command is recognized (bare fails, -I passes),
#  10. the gate REFUSES (exit 2) when its own interpreter is not isolated (a real subprocess),
#  11-13. a value-taking option's value is never letter-scanned (-Wignore::ImportWarning is not
#      isolated; -W ignore -I -B and -Xfoo -I are), so isolation is neither forged nor missed,
#  14. a settings.json shell-string that chains commands catches a launcher in a later segment,
#  15-18. a required surface that is non-regular (/dev/null) or non-UTF-8, and a PRESENT optional
#      settings file that is non-regular or non-UTF-8, each fail closed (exit 2),
#  19-21. a launcher glued to a `;` (`echo prep;python3`), unspaced, is still segmented and caught in
#      both a settings shell-string and a run_all_checks.sh shell line (exit 1), and one glued to a
#      subshell `(` is outside the allow-listed grammar (exit 2),
#  22-23. a PRESENT-but-dangling optional settings symlink fails closed (exit 2) while a truly-absent
#      optional settings file stays a clean skip (exit 0),
#  24. an attached -m/-c operand (-mIfoo, -cIbar) terminates option scanning so its letters never
#      forge isolation (exit 1), confirmed against a real interpreter for the -c case,
#  25-27. an `&`-carrying REDIRECT (`>&python3`, `2>&1`, `>&2`, `&>/dev/null`) is outside the allow-listed
#      grammar: a launcher line carrying one is a cannot-evaluate naming the redirection (exit 2),
#  28. the genuine separators are not loosened: a non-isolated launcher after a real `&&` or a real
#      background `&` is still segmented and caught (exit 1),
#  29. a runtime witness, not a gate regression: real bash reads `>&python3` as a redirect to a file
#      named python3 (why a redirection is refused rather than parsed).
#  30. a quote-obfuscated launcher NAME in a run_all_checks.sh shell line (`pyt"hon"3`, `py'thon'3`),
#      which bash resolves to a real non-isolated python, is caught (exit 1), the backslash-spelled
#      `pyt\hon3` is outside the grammar (exit 2), and the isolated `pyt"hon"3 -I` form passes (exit 0).
#  31. the same obfuscated NAMES in a settings.json shell-string give the same exits.
#  32. a runtime witness, not a gate regression: real bash resolves `pyt\hon3`, `pyt"hon"3` and
#      `py'thon'3` to the token `python3`.
#  34. the NO-SITE RULE: the core hook launcher registered with -I alone, or with -S after the script,
#      fails (exit 1) in the plugin hooks.json and in a settings.json shell-string; -I -S -B, the -IS
#      cluster and the shell-string -I -S -B form pass (exit 0); another script with -I alone passes;
#      and a real interpreter confirms -S is what keeps the site module (so every site-packages .pth
#      file) from running.
#  35. the `--` delimiter does not hide the script operand: `-I -B -- <launcher>` fails the no-site rule
#      (exit 1) in the plugin hooks.json args form and in a settings.json shell-string, and the same with
#      -S before `--` passes (exit 0).
#  36. the no-site rule is categorical on the hook-launching surfaces: any token naming the core hook
#      launcher FAILS (exit 1) unless the option scan reached the script operand cleanly, that operand
#      names the launcher, and -S is before it. Each
#      spelling that stops the scan early with the launcher later in the argv (`=VALUE` long option,
#      `-I-check-hash-based-pycs default`, `-IB-check-hash-based-pycs default`, `-m cProfile`, a `-c`
#      runpy operand, an `=VALUE` long option inside a cluster), with or without -S, and the launcher as
#      an argument of another script each fail in the hooks.json args form and in a settings.json shell-
#      string; the S-carrying spellings that reach the launcher cleanly (`-IS-check-hash-based-pycs
#      default`, `-ISB-check-hash-based-pycs default`, `-I -S -B --check-hash-based-pycs always`) pass
#      (exit 0); an args-form `/usr/bin/env` command word with `python3` in args fails closed (exit 2),
#      as the settings shell-string does; and a real interpreter confirms CPython's reading of those
#      spellings (a `-` in a cluster starts the long option, the `=VALUE` form is refused with exit 2).
#  37. only an UNQUOTED shell operator separates: for every member of SEPARATORS, the reproduction
#      `python3 -I -B -v -c <runpy of sys.argv[2]> '<op>' <launcher>` fails (exit 1) in a settings.json
#      hook command, single-quoted and double-quoted, and is outside the grammar backslash-escaped
#      (exit 2); the six hooks.json args-form cases are CONTROLS (the args form never reaches the
#      splitter, and they failed before it changed); the unquoted controls still split (`echo a <op>
#      python3 -I -S -B <launcher>` passes, `echo a <op> python3 /p/x.py` fails); 37b, a runtime witness,
#      not a gate regression: real bash confirms the quoted operator is an argument and the runpy
#      operand runs a launcher-named probe with the site module.
#  38. the categorical rule is scoped to the hook-launching surfaces: a CI line that compiles or hashes
#      the launcher passes (exit 0) in run_all_checks.sh and in a workflow run: line, the same line as a
#      settings.json hook command fails (exit 1), and a CI line running the launcher still needs -S.
#  39. a bare trailing `-` in a cluster ends the options and the next token is the script operand
#      (`-I -S- <launcher>` and `-IS- <launcher>` pass, `-I- <launcher>` and `-I- -S <launcher>` fail),
#      and (39b, a runtime witness, not a gate regression) a real interpreter confirms that reading, the
#      cluster help and unknown long options, and that `-ISh <script>` prints help and exits 0 without
#      running the script.
#  40. a second mention of the launcher after a compliant script operand is its own argument: it passes
#      with -S (exit 0) and fails without it (exit 1), in both forms.
#  41. the ALLOW-LISTED SHELL GRAMMAR: every reviewer reproduction (a nested $( ) holding a quoted `;`,
#      a backslash-newline inside the launcher name and inside the interpreter name, ANSI-C quoting, a
#      `#` comment before a newline, backticks) and every other construct outside the grammar is a
#      settings-command cannot-evaluate (exit 2) naming it; plain compliant commands pass (exit 0) and
#      plain non-compliant ones fail (exit 1); on a CI line a double-quoted `$NAME` after the script
#      operand passes and one at the command word, in the options or in the script operand is exit 2.

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


def self_test_main():
    import io
    import shutil
    import subprocess
    import tempfile
    from contextlib import redirect_stdout

    def run_quiet(root):
        with redirect_stdout(io.StringIO()):
            return run(root)

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
        if os.access(target, os.R_OK):
            skipped.append("7 unreadable-run-all (chmod-0 still readable)")
        elif run_quiet(unread) != 2:
            failures.append("an unreadable required input expected exit 2 (fail-closed)")
        os.chmod(target, 0o644)

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
        #     (python3 ...)`) is outside the allow-listed grammar, so it is a cannot-evaluate (exit 2).
        glue_paren = _build(tmp / "settings-glue-paren")
        sp = glue_paren / ".claude" / "settings.json"
        sp.parent.mkdir(parents=True)
        sp.write_text('{"hooks": {"PreToolUse": [{"hooks": [{"type": "command", '
                      '"command": "echo a && (python3 tools/x.py)"}]}]}}\n', encoding="utf-8")
        if run_quiet(glue_paren) != 2:
            failures.append("a subshell-glued settings launcher (&& (python3 ...)) expected exit 2")

        # 21. The same punctuation-adjacent forms in a run_all_checks.sh shell line (which shares the
        #     segment logic): a `;`-glued bare launcher is segmented and caught (exit 1), and a
        #     `(`-glued one is outside the grammar (exit 2).
        glue_sh = _build(tmp / "runall-glue-semi",
                         run_all='#!/usr/bin/env bash\necho prep;python3 tools/alpha.py\n')
        if run_quiet(glue_sh) != 1:
            failures.append("a semicolon-glued launcher in run_all_checks.sh expected exit 1")
        glue_sh_paren = _build(tmp / "runall-glue-paren",
                               run_all='#!/usr/bin/env bash\necho a && (python3 tools/alpha.py)\n')
        if run_quiet(glue_sh_paren) != 2:
            failures.append("a subshell-glued launcher in run_all_checks.sh expected exit 2")

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

        # 25-27. A redirection is outside the allow-listed grammar, so a launcher line carrying one is a
        #     cannot-evaluate (exit 2) naming it, never a parse by guess: `>&python3` (a redirect to a FILE
        #     named python3, which a hand-written splitter once read as a second launcher), `2>&1`,
        #     `>&2` and `&>/dev/null`, each around an isolated and a non-isolated launcher.
        for k, redirect in enumerate((">&python3", "2>&1", ">&2", "&>/dev/null")):
            for opts in ("-I -B ", ""):
                run_all = "#!/usr/bin/env bash\npython3 {}tools/alpha.py {}\n".format(opts, redirect)
                tree = _build(tmp / "redir-{}-{}".format(k, len(opts)), run_all=run_all)
                errs, fails = scan(tree)
                if not any("a redirection" in e[3] for e in errs):
                    failures.append("a {!r} redirect on a launcher line expected a cannot-evaluate naming "
                                    "the redirection (errs={!r}, fails={!r})".format(redirect, errs, fails))

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

        # 29. A RUNTIME WITNESS (it exercises bash, not the gate): REAL bash reads `>&python3` as a
        #     redirect to a file named `python3`, not a command; the launcher runs ISOLATED
        #     (sys.flags.isolated == 1) and the file holds its output. It records why a redirection is
        #     refused (rows 25-27) rather than parsed. Skipped where bash is unavailable.
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

        # 30. A quote-obfuscated launcher NAME in a run_all_checks.sh shell line is resolved by bash to a
        #     real interpreter, so the gate must decide on the DECODED command word, not the raw text.
        #     Each quote-obfuscated bare launcher fails (exit 1) and the backslash-spelled one is outside
        #     the grammar (exit 2); the isolated `pyt"hon"3 -I` form, alongside a plain `echo hello`,
        #     passes (exit 0).
        for label, sh_line, want in (("backslash", r"pyt\hon3 tools/alpha.py", 2),
                                     ("dquote", 'pyt"hon"3 tools/alpha.py', 1),
                                     ("squote", "py'thon'3 tools/alpha.py", 1)):
            run_all = "#!/usr/bin/env bash\n{}\n".format(sh_line)
            if run_quiet(_build(tmp / ("obf-" + label), run_all=run_all)) != want:
                failures.append("an obfuscated launcher name ({}) in run_all_checks.sh expected exit {}"
                                .format(label, want))
        iso_obf = '#!/usr/bin/env bash\npyt"hon"3 -I tools/alpha.py\necho hello\n'
        if run_quiet(_build(tmp / "obf-iso", run_all=iso_obf)) != 0:
            failures.append('an isolated obfuscated launcher (pyt"hon"3 -I) plus a plain line expected '
                            "exit 0")

        # 31. The same obfuscated NAMES in a settings.json shell-string agree with the shell-line path:
        #     the quoted ones are caught (exit 1) and the backslash one is outside the grammar (exit 2).
        #     json.dumps builds the command so the backslash and quotes survive into the JSON string.
        for label, cmd, want in (("backslash", r"pyt\hon3 tools/x.py", 2),
                                 ("dquote", 'pyt"hon"3 tools/x.py', 1),
                                 ("squote", "py'thon'3 tools/x.py", 1)):
            obf = _build(tmp / ("settings-obf-" + label))
            sp = obf / ".claude" / "settings.json"
            sp.parent.mkdir(parents=True)
            sp.write_text(json.dumps({"hooks": {"PreToolUse": [{"hooks": [
                {"type": "command", "command": cmd}]}]}}) + "\n", encoding="utf-8")
            if run_quiet(obf) != want:
                failures.append("an obfuscated launcher name ({}) in settings.json expected exit {}"
                                .format(label, want))

        # 32. A RUNTIME WITNESS (it exercises bash, not the gate): real bash resolves each obfuscated
        #     NAME to the token `python3`. Skipped where bash is unavailable.
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

        # 34. The NO-SITE RULE (the core hook launcher only). Each case: label, hooks.json args (or a
        #     settings.json shell-string), expected exit.
        launcher = SCRIPT.rsplit("/", 1)[0] + "/" + gen_hooks.LAUNCHER_NAME
        for label, args, want in (("dash-I-only", ("-I", launcher, "h_one"), 1),
                                  ("S-after-script", ("-I", launcher, "-S", "h_one"), 1),
                                  ("registered", ("-I", "-S", "-B", launcher, "h_one"), 0),
                                  ("cluster-IS", ("-IS", launcher, "h_one"), 0),
                                  ("other-script", ("-I", SCRIPT, "h_one"), 0)):
            if run_quiet(_build(tmp / ("nosite-" + label), hooks_args=args)) != want:
                failures.append("no-site rule: the {} hooks.json form expected exit {}".format(label, want))
        for label, cmd, want in (("settings-dash-I-only", "python3 -I /p/" + gen_hooks.LAUNCHER_NAME + " x", 1),
                                 ("settings-registered",
                                  "python3 -I -S -B /p/" + gen_hooks.LAUNCHER_NAME + " x", 0)):
            tree = _build(tmp / ("nosite-" + label))
            sp = tree / ".claude" / "settings.json"
            sp.parent.mkdir(parents=True)
            sp.write_text(json.dumps(dict(hooks=dict(PreToolUse=[dict(hooks=[
                dict(type="command", command=cmd)])]))) + "\n", encoding="utf-8")
            if run_quiet(tree) != want:
                failures.append("no-site rule: the {} form expected exit {}".format(label, want))
        # 35. The `--` delimiter ends the options, and the token after it is still the script operand:
        #     `-I -B -- <launcher>` runs the core hook launcher with the site module, so it fails the
        #     no-site rule (exit 1) in the args form and in a settings shell-string, and -S before `--`
        #     passes (exit 0). Treating `--` like -m/-c (no script operand) passed both failing forms.
        for label, args, want in (("delim-no-S", ("-I", "-B", "--", launcher, "h_one"), 1),
                                  ("delim-S", ("-I", "-S", "-B", "--", launcher, "h_one"), 0)):
            if run_quiet(_build(tmp / ("nosite-" + label), hooks_args=args)) != want:
                failures.append("no-site rule: the {} hooks.json form expected exit {}".format(label, want))
        for label, cmd, want in (("settings-delim-no-S",
                                  "python3 -I -B -- /p/" + gen_hooks.LAUNCHER_NAME + " x", 1),
                                 ("settings-delim-S",
                                  "python3 -I -S -B -- /p/" + gen_hooks.LAUNCHER_NAME + " x", 0)):
            tree = _build(tmp / ("nosite-" + label))
            sp = tree / ".claude" / "settings.json"
            sp.parent.mkdir(parents=True)
            sp.write_text(json.dumps(dict(hooks=dict(PreToolUse=[dict(hooks=[
                dict(type="command", command=cmd)])]))) + "\n", encoding="utf-8")
            if run_quiet(tree) != want:
                failures.append("no-site rule: the {} form expected exit {}".format(label, want))
        # 36. The categorical no-site rule: (label, args after python3, expected exit), each run in the
        #     hooks.json args form and as a settings.json shell-string.
        name = gen_hooks.LAUNCHER_NAME
        launch_arg = "/p/" + name
        categorical = (
            ("eq-long", ["-I", "-B", "--check-hash-based-pycs=always", launch_arg, "x"], 1),
            ("eq-long-S", ["-I", "-S", "-B", "--check-hash-based-pycs=always", launch_arg, "x"], 1),
            ("cluster-long", ["-I-check-hash-based-pycs", "default", launch_arg, "x"], 1),
            ("cluster-B-long", ["-IB-check-hash-based-pycs", "default", launch_arg, "x"], 1),
            ("cluster-eq-long-S", ["-IS-check-hash-based-pycs=always", launch_arg, "x"], 1),
            ("m-runner", ["-I", "-B", "-m", "cProfile", launch_arg, "x"], 1),
            ("m-runner-S", ["-I", "-S", "-B", "-m", "cProfile", launch_arg, "x"], 1),
            ("c-runpy-S", ["-I", "-S", "-B", "-c",
                           "import runpy; runpy.run_path('{}')".format(launch_arg)], 1),
            ("argument-of-other-script", ["-I", "-S", "-B", "/p/other.py", launch_arg], 1),
            ("cluster-long-S", ["-IS-check-hash-based-pycs", "default", launch_arg, "x"], 0),
            ("cluster-B-long-S", ["-ISB-check-hash-based-pycs", "default", launch_arg, "x"], 0),
            ("long-separate-S", ["-I", "-S", "-B", "--check-hash-based-pycs", "always", launch_arg, "x"],
             0))
        for label, args, want in categorical:
            if run_quiet(_build(tmp / ("categorical-args-" + label), hooks_args=args)) != want:
                failures.append("no-site rule: the {} hooks.json form expected exit {}".format(label, want))
            tree = _build(tmp / ("categorical-settings-" + label))
            sp = tree / ".claude" / "settings.json"
            sp.parent.mkdir(parents=True)
            sp.write_text(json.dumps(dict(hooks=dict(PreToolUse=[dict(hooks=[dict(
                type="command", command=" ".join(["python3"] + [shlex.quote(a) for a in args]))])]))) + "\n",
                encoding="utf-8")
            if run_quiet(tree) != want:
                failures.append("no-site rule: the settings {} form expected exit {}".format(label, want))
        env_args = _build(tmp / "categorical-env-args")
        (env_args / HOOKS_JSON_REL).write_text(json.dumps(dict(hooks=dict(PreToolUse=[dict(hooks=[dict(
            type="command", command="/usr/bin/env", args=["python3", "-I", "-B", launch_arg, "x"])])])))
            + "\n", encoding="utf-8")
        if run_quiet(env_args) != 2:
            failures.append("an args-form /usr/bin/env command word with python3 in args expected exit 2 "
                            "(fail-closed, as the settings shell-string)")
        site_probe = "import sys; sys.stdout.write(str(int('site' in sys.modules)))"
        spelled = [subprocess.run([sys.executable] + flags + ["-c", site_probe], capture_output=True,
                                  text=True, env=env, cwd=str(tmp), timeout=30)
                   for flags in (["-I-check-hash-based-pycs", "default"],
                                 ["-IS-check-hash-based-pycs", "default"],
                                 ["-I", "-S", "--check-hash-based-pycs=always"])]
        if ([(p.returncode, p.stdout) for p in spelled] != [(0, "1"), (0, "0"), (2, "")]
                or "Unknown option" not in spelled[2].stderr):
            failures.append("a real interpreter should read `-I-check-hash-based-pycs default` with the "
                            "site module, `-IS-...` without it, and refuse the =VALUE form with exit 2 "
                            "(got {!r})".format([(p.returncode, p.stdout, p.stderr[-80:]) for p in spelled]))
        site_seen = [subprocess.run([sys.executable] + flags + ["-c", site_probe], capture_output=True,
                                    text=True, env=env, cwd=str(tmp), timeout=30).stdout
                     for flags in (["-I"], ["-I", "-S", "-B"])]
        if site_seen != ["1", "0"]:
            failures.append("no-site rule: a real interpreter should import site under -I and not under "
                            "-I -S -B (got {!r})".format(site_seen))

        # 37. Only an UNQUOTED shell operator separates. The reproduction is a settings.json hook command
        #     whose -c runpy operand runs sys.argv[2], with a quoted separator as sys.argv[1] and the core
        #     hook launcher as sys.argv[2]: a real shell passes both as arguments, so the launcher runs
        #     with the site module. For every member of SEPARATORS, single-quoted and double-quoted, it
        #     FAILs (exit 1); backslash-escaped, it is outside the allow-listed grammar (exit 2).
        #     Splitting at the quoted operator cut the launcher off into a non-python segment and passed
        #     (exit 0). CONTROLS, which never reach the splitter: the same argv in the hooks.json args
        #     form fails (exit 1), as it did before the splitter changed (-c stops the option scan). The
        #     unquoted controls still split: `echo a <op> python3 -I -S -B <launcher> x` passes (exit 0,
        #     one compliant launcher after the operator; no split reads it as exit 2) and `echo a <op>
        #     python3 /p/x.py` fails (exit 1).
        def settings_tree(label, cmd):
            tree = _build(tmp / label)
            sp = tree / ".claude" / "settings.json"
            sp.parent.mkdir(parents=True)
            sp.write_text(json.dumps(dict(hooks=dict(PreToolUse=[dict(hooks=[
                dict(type="command", command=cmd)])]))) + "\n", encoding="utf-8")
            return tree

        runpy_code = 'import runpy,sys;runpy.run_path(sys.argv[2],run_name="__main__")'
        for k, op in enumerate(sorted(SEPARATORS)):
            spellings = (("squote", shlex.quote(op), 1), ("dquote", '"' + op + '"', 1),
                         ("escaped", "".join("\\" + ch for ch in op), 2))
            for style, spelled_op, want in spellings:
                cmd = "python3 -I -B -v -c {} {} {}".format(shlex.quote(runpy_code), spelled_op, launch_arg)
                if run_quiet(settings_tree("quoted-op-{}-{}".format(k, style), cmd)) != want:
                    failures.append("no-site rule: a {!r} argument ({}) must not split a settings hook "
                                    "command (expected exit {}): {!r}".format(op, style, want, cmd))
            args = ("-I", "-B", "-v", "-c", runpy_code, op, launch_arg)
            if run_quiet(_build(tmp / "quoted-op-args-{}".format(k), hooks_args=args)) != 1:
                failures.append("control: the {!r} argument in the hooks.json args form expected "
                                "exit 1".format(op))
            for want, after in ((0, "python3 -I -S -B {} x".format(launch_arg)), (1, "python3 /p/x.py")):
                cmd = "echo a {} {}".format(op, after)
                if run_quiet(settings_tree("unquoted-op-{}-{}".format(k, want), cmd)) != want:
                    failures.append("an unquoted {!r} must still split a settings hook command (expected "
                                    "exit {}): {!r}".format(op, want, cmd))
        # 37b. A RUNTIME WITNESS (it exercises bash and CPython, not the gate): real bash runs the
        #      reproduction: for each quoted separator, the -c runpy operand reaches
        #      a scratch file named like the core hook launcher, which reports that the site module is
        #      loaded (stdout "1"). Skipped where bash is unavailable.
        if bash_bin is None:
            skipped.append("37b real-bash-quoted-operator (bash unavailable)")
        else:
            probe_dir = tmp / "quoted-op-probe"
            probe_dir.mkdir()
            probe = probe_dir / gen_hooks.LAUNCHER_NAME
            probe.write_text("import sys\nsys.stdout.write(str(int('site' in sys.modules)))\n",
                             encoding="utf-8")
            for op in sorted(SEPARATORS):
                bash_cmd = "{} -I -B -c {} {} {}".format(shlex.quote(sys.executable), shlex.quote(runpy_code),
                                                        shlex.quote(op), shlex.quote(str(probe)))
                proc = subprocess.run([bash_bin, "-c", bash_cmd], capture_output=True, text=True,
                                      env=env, cwd=str(tmp), timeout=30)
                if proc.returncode != 0 or proc.stdout != "1":
                    failures.append("real bash: a quoted {!r} should be an argument, so the runpy operand "
                                    "runs the launcher-named probe with site (got rc={}, out={!r}, err={!r})"
                                    .format(op, proc.returncode, proc.stdout, proc.stderr[-120:]))

        # 38. The categorical rule is scoped to the surfaces that launch hooks. A CI line that compiles or
        #     hashes the launcher (run_all_checks.sh, a workflow run: line) passes (exit 0); the same line
        #     as a settings.json hook command fails (exit 1). In CI the script-operand form still holds: a
        #     line that runs the launcher without -S fails (exit 1) and with -S passes (exit 0).
        core_launcher = ".aiqt/core/hooks/scripts/" + gen_hooks.LAUNCHER_NAME
        ci_lines = ("python3 -I -B -m py_compile " + core_launcher,
                    "python3 -I -B tools/hash_file.py " + core_launcher)
        for k, line in enumerate(ci_lines):
            run_all = "#!/usr/bin/env bash\nrun_gate \"launcher-ci\" {}\n".format(line)
            if run_quiet(_build(tmp / "ci-scope-run-all-{}".format(k), run_all=run_all)) != 0:
                failures.append("no-site rule: a run_all_checks.sh line {!r} expected exit 0".format(line))
            workflow = "name: Q\njobs:\n  q:\n    steps:\n      - name: c\n        run: {}\n".format(line)
            if run_quiet(_build(tmp / "ci-scope-workflow-{}".format(k), workflow=workflow)) != 0:
                failures.append("no-site rule: a workflow run: line {!r} expected exit 0".format(line))
            if run_quiet(settings_tree("ci-scope-settings-{}".format(k), line)) != 1:
                failures.append("no-site rule: the settings hook command {!r} expected exit 1".format(line))
        for want, opts in ((1, "-I -B"), (0, "-I -S -B")):
            run_all = "#!/usr/bin/env bash\npython3 {} {} x\n".format(opts, core_launcher)
            if run_quiet(_build(tmp / "ci-scope-operand-{}".format(want), run_all=run_all)) != want:
                failures.append("no-site rule: a run_all_checks.sh line running the launcher with {} "
                                "expected exit {}".format(opts, want))
        # 39. A bare trailing `-` in a cluster ends the options and the next token is the script
        #     operand (CPython prints "Expected long option" and runs it), and 40. a further mention of
        #     the launcher after a compliant operand is its own argument, not a failure. Each row runs in
        #     the hooks.json args form and as a settings.json hook command.
        for label, args, want in (
                ("trailing-dash-S", ["-I", "-S-", launch_arg, "x"], 0),
                ("cluster-trailing-dash-S", ["-IS-", launch_arg, "x"], 0),
                ("trailing-dash-no-S", ["-I-", launch_arg, "x"], 1),
                ("trailing-dash-then-S", ["-I-", "-S", launch_arg, "x"], 1),
                ("named-twice-S", ["-I", "-S", "-B", launch_arg, launch_arg], 0),
                ("named-twice-no-S", ["-I", "-B", launch_arg, launch_arg], 1)):
            if run_quiet(_build(tmp / ("dash-args-" + label), hooks_args=args)) != want:
                failures.append("no-site rule: the {} hooks.json form expected exit {}".format(label, want))
            cmd = " ".join(["python3"] + [shlex.quote(a) for a in args])
            if run_quiet(settings_tree("dash-settings-" + label, cmd)) != want:
                failures.append("no-site rule: the settings {} form expected exit {}".format(label, want))
        # 39b. A RUNTIME WITNESS (it exercises CPython, not the gate): a real interpreter confirms that
        #      reading: `-I- <probe>` runs the probe with site, `-IS-
        #      <probe>` without it, both after "Expected long option"; `-I- -S <probe>` takes `-S` as the
        #      script (exit 2); in a cluster `-help-all` prints help (exit 0) and `-version` is an unknown
        #      option (exit 2); and `-ISh <probe>` prints help and exits 0 without running the probe (the
        #      disclosed help-cluster residual).
        site_file = tmp / "site-probe.py"
        site_file.write_text("import sys\nsys.stdout.write('RAN' + str(int('site' in sys.modules)))\n",
                             encoding="utf-8")
        dash_runs = [subprocess.run([sys.executable] + flags, capture_output=True, text=True, env=env,
                                    cwd=str(tmp), timeout=30, stdin=subprocess.DEVNULL)
                     for flags in (["-I-", str(site_file)], ["-IS-", str(site_file)],
                                   ["-I-", "-S", str(site_file)], ["-I-help-all"], ["-I-version"],
                                   ["-ISh", str(site_file)])]
        got = [(p.returncode, p.stdout[:5]) for p in dash_runs]
        if (got[:3] != [(0, "RAN1"), (0, "RAN0"), (2, "")]
                or not all("Expected long option" in p.stderr for p in dash_runs[:3])
                or got[3] != (0, "usage") or got[4][0] != 2 or "Unknown option" not in dash_runs[4].stderr
                or got[5] != (0, "usage") or "RAN" in dash_runs[5].stdout):
            failures.append("a real interpreter should read a bare trailing `-` as the end of the options, "
                            "-help-all as help, -version as unknown, and -ISh as help without running the "
                            "script (got {!r})".format([(p.returncode, p.stdout[:40], p.stderr[-80:])
                                                       for p in dash_runs]))

        # 41. The ALLOW-LISTED GRAMMAR. Every reviewer reproduction, as a settings.json hook command, is a
        #     cannot-evaluate (exit 2) whose message names the construct: a nested $( ) holding a quoted
        #     `;`, a backslash-newline inside the launcher name and inside the interpreter name, ANSI-C
        #     quoting, a `#` comment before a newline, and backticks; so is every other construct outside
        #     the grammar. Plain compliant commands still pass (exit 0) and plain non-compliant ones
        #     still fail (exit 1). On a CI line a double-quoted `$NAME` or `${NAME}` after the script
        #     operand passes (exit 0), and one at the command word, among the interpreter options or in
        #     the script operand is a cannot-evaluate (exit 2); a backslash-newline on a CI line that
        #     joins `pyth` and `on3` is a cannot-evaluate (exit 2).
        grammar_rows = (
            ("nested-subst", 'python3 -I -B -c {} "$(printf "%s;" x)" {}'.format(shlex.quote(runpy_code),
                                                                             launch_arg),
             2, "a command substitution"),
            ("bsnl-launcher", "python3 -I -B /p/aiqt_hooks_\\\nlaunch.py", 2, "a backslash-newline"),
            ("bsnl-interpreter", "pyth\\\non3 -I -B " + launch_arg, 2, "a backslash-newline"),
            ("ansi-c", "echo $'\\'' ; python3 -I -B {} # '".format(launch_arg), 2, "ANSI-C quoting"),
            ("comment", "echo hi # don't\npython3 -I -B {}\n#'".format(launch_arg), 2, "an unquoted #"),
            ("backtick", "out=`python3 -I -B {}`; rc=$?; printf %s \"$out\"; exit $rc".format(launch_arg), 2,
             "a backtick"),
            ("newline", "echo a\npython3 -I -B " + launch_arg, 2, "a newline"),
            ("dollar-var", "python3 -I -B $L", 2, "a $ expansion"),
            ("dq-dollar", 'python3 -I -S -B "$CLAUDE_PROJECT_DIR"', 2, "a $ expansion inside double"),
            ("locale", 'python3 -I -B $"x" ' + launch_arg, 2, "locale quoting"),
            ("process-subst", "python3 -I -S -B {} <(echo x)".format(launch_arg), 2, "a process substitution"),
            ("heredoc", "python3 -I -B - <<EOF", 2, "a heredoc"),
            ("here-string", "python3 -I -B - <<< x", 2, "a here-string"),
            ("redirect", "python3 -I -S -B {} 2>/dev/null".format(launch_arg), 2, "a redirection"),
            ("subshell", "(python3 -I -B {})".format(launch_arg), 2, "a parenthesis"),
            ("brace", "{ python3 -I -B " + launch_arg + "; }", 2, "a brace"),
            ("glob-launcher", "python3 -I -B /p/aiqt_hooks_launc?.py", 2, "glob character ?"),
            ("glob-python", "pytho[n]3 -I -B " + launch_arg, 2, "glob character ["),
            ("bang", "! python3 -I -B " + launch_arg, 2, "an unquoted !"),
            ("tilde", "python3 -I -B ~/" + gen_hooks.LAUNCHER_NAME, 2, "a tilde"),
            ("dq-backslash", 'python3 -I -B "/p/aiqt_hooks_\\launch.py"', 2, "a backslash escape inside"),
            ("dq-bang", 'echo "a!b" && python3 -I -B ' + launch_arg, 2, "a ! (history expansion) inside"),
            ("pipe-amp", "echo a |& python3 -I -B " + launch_arg, 2, "not a listed separator"),
            ("unbalanced-squote", "python3 -I -B 'x " + launch_arg, 2, "an unbalanced single quote"),
            ("unbalanced-dquote", 'python3 -I -B "x ' + launch_arg, 2, "an unbalanced double quote"),
            ("control-char", "python3 -I -B\r" + launch_arg, 2, "the character"),
            ("compliant", "python3 -I -S -B {} h_one".format(launch_arg), 0, ""),
            ("compliant-chain", "echo a && python3 -I -S -B '{}' \"h one\"; true".format(launch_arg), 0, ""),
            ("compliant-or", "python3 -I -S -B {} h_one || true".format(launch_arg), 0, ""),
            ("plain-no-S", "python3 -I -B {} h_one".format(launch_arg), 1, ""),
            ("plain-bare", "python3 tools/x.py", 1, ""),
            ("plain-chained", "echo a;python3 -B tools/x.py", 1, ""))
        for label, cmd, want, construct in grammar_rows:
            tree = settings_tree("grammar-" + label, cmd)
            errs, fails = scan(tree)
            got = 2 if errs else (1 if fails else 0)
            if got != want or (construct and not any(construct in e[3] for e in errs)):
                failures.append("allow-listed grammar: the settings command {} {!r} expected exit {} naming "
                                "{!r} (got exit {}, errs={!r})".format(label, cmd, want, construct, got, errs))
        ci_rows = (
            ("after-script", 'python3 -I -B tools/a.py --base "origin/${GITHUB_BASE_REF}" --head "$SHA"', 0),
            ("non-python-arg", 'echo "$X" && python3 -I -B tools/a.py', 0),
            ("command-word", '"$PY" -I -B tools/a.py python3', 2),
            ("assignment", 'X="$Y" python3 -I -B tools/a.py', 2),
            ("option", 'python3 "$OPT" tools/a.py', 2),
            ("script-operand", 'python3 -I -S -B "$L"', 2),
            ("after-c", 'python3 -I -B -c "$CODE"', 2),
            ("unquoted", "python3 -I -B tools/a.py $X", 2),
            ("braced-default", 'python3 -I -B tools/a.py "${X:-y}"', 2),
            ("bsnl-start", "pyth\\", 2))
        for label, line, want in ci_rows:
            run_all = "#!/usr/bin/env bash\n{}\non3 tools/a.py\n".format(line)
            if run_quiet(_build(tmp / ("grammar-ci-" + label), run_all=run_all)) != want:
                failures.append("allow-listed grammar: the run_all_checks.sh line {} {!r} expected exit {}"
                                .format(label, line, want))

        # 42. SCOPE, and the leading path segment. A settings hook command with no python word and no
        #     core hook launcher name once its quoting is removed is out of scope (exit 0), its
        #     redirections, `~` paths, groups and globs included, unless a command word can hide a python
        #     word (an expansion, glob, brace or `~` in its file name, an unquoted expansion in its
        #     directory, a command or process substitution or a backtick anywhere): that is a
        #     cannot-evaluate (exit 2). Inside a candidate the form Claude Code's hook documentation shows,
        #     a double-quoted `$NAME` or `${NAME}` as the leading path segment of the script operand,
        #     is admitted (compliant 0, non-compliant 1, the basename read literally), and an expansion at
        #     or before the command word, among the interpreter options and their values, or anywhere but
        #     a leading path segment is a cannot-evaluate (exit 2). A backslash- or line-continuation-
        #     spelled python word makes a command a candidate, which the grammar then refuses (exit 2).
        scope_rows = (
            ('python3 -I -S -B "$CLAUDE_PROJECT_DIR"/.claude/hooks/aiqt_hooks_launch.py h_one', 0),
            ('python3 -I -S -B "$CLAUDE_PROJECT_DIR/.claude/hooks/aiqt_hooks_launch.py" h_one', 0),
            ('python3 -I -S -B "${CLAUDE_PROJECT_DIR}"/.claude/hooks/aiqt_hooks_launch.py h_one', 0),
            ('python3 -I -S -B -- "$CLAUDE_PROJECT_DIR"/.claude/hooks/aiqt_hooks_launch.py h_one', 0),
            ('cd "$CLAUDE_PROJECT_DIR"/x && python3 -I -S -B /p/aiqt_hooks_launch.py h_one', 0),
            ('python3 -I -B "$CLAUDE_PROJECT_DIR"/.claude/hooks/other.py', 0),
            ('python3 -I -B "$CLAUDE_PROJECT_DIR"/.claude/hooks/aiqt_hooks_launch.py h_one', 1),
            ('python3 "$CLAUDE_PROJECT_DIR"/.claude/hooks/x.py', 1),
            ('python3 -S "$CLAUDE_PROJECT_DIR"/.claude/hooks/aiqt_hooks_launch.py h_one', 1),
            ('"$PY" -I -S -B /p/aiqt_hooks_launch.py h_one', 2),
            ('"$PY" -I -S -B "$CLAUDE_PROJECT_DIR"/.claude/hooks/aiqt_hooks_launch.py h_one', 2),
            ('"${PY}" -I -S -B /p/x.py', 2),
            ('"$PY" -I -S -B /p/x.py', 2),
            ('"$CLAUDE_PROJECT_DIR"/bin/python3 -I -S -B /p/aiqt_hooks_launch.py', 2),
            ('python3 -I -W "$W"/x -S -B /p/aiqt_hooks_launch.py h_one', 2),
            ('python3 -I -X "$D"/importtime -S -B /p/aiqt_hooks_launch.py', 2),
            ('python3 --check-hash-based-pycs "$D"/x -I -S -B /p/aiqt_hooks_launch.py', 2),
            ('python3 -I -S -B --frob "$D"/aiqt_hooks_launch.py', 2),
            ('X="$D"/y python3 -I -S -B /p/aiqt_hooks_launch.py', 2),
            ('python3 -I -S -B $CLAUDE_PROJECT_DIR/.claude/hooks/aiqt_hooks_launch.py', 2),
            ('python3 -I -S -B "$L"', 2),
            ('python3 -I -S -B "$D"x/aiqt_hooks_launch.py', 2),
            ('python3 -I -S -B "$A"/"$B"/aiqt_hooks_launch.py', 2),
            ('python3 -I -S -B "$A/$B/aiqt_hooks_launch.py"', 2),
            ('python3 -I -S -B "${D:-/x}"/aiqt_hooks_launch.py', 2),
            ('python3 -I -S -B "$1"/aiqt_hooks_launch.py', 2),
            ('python3 -I -S -B "x$D"/aiqt_hooks_launch.py', 2),
            ('python3 -I -S -B /p/aiqt_hooks_launch.py "$X"', 2),
            ('python3 -I -S -B "$D"/aiqt_hooks_launch.py 2>/dev/null', 2),
            ('pyt\\hon3 -B x.py', 2),
            ('pyth\\\non3 -B x.py', 2),
            ('pyt"hon"3 -B x.py', 1),
            ('"$D"/aiqt_hooks_launch.py h_one', 2),
            ('"$CLAUDE_PROJECT_DIR"/.claude/hooks/check-style.sh', 0),
            ('"${CLAUDE_PROJECT_DIR}/.claude/hooks/check-style.sh" 2>/dev/null', 0),
            ('~/bin/notify.sh >> ~/.claude/notify.log 2>&1', 0),
            ('{ jq -r .tool_input.command; echo; } >> /tmp/claude-commands.log', 0),
            ('(cd "$CLAUDE_PROJECT_DIR" && npx prettier --write src/*.ts) || true', 0),
            ('rm -f /tmp/*.lock; echo done > /dev/null', 0),
            ("jq -r '.tool_input.file_path' | xargs -r npx eslint --fix", 0),
            ('if [ -f .env ]; then echo "found"; fi', 0),
            ('[ -n "$CI" ] || ./scripts/hook.sh', 0),
            ('[[ -n "$CI" ]] && echo ci', 0),
            ('for f in *.md; do echo "$f"; done', 0),
            ('npm run lint -- --fix 2>&1 | tee /tmp/lint.log', 0),
            ('"$HOME"/.claude/hooks/x.sh', 0),
            ('bash "$CLAUDE_PROJECT_DIR"/.claude/hooks/x.sh', 0),
            ('X=1 Y="$Z" ./hook.sh < /dev/null', 0),
            ('env -i FOO=1 ./hook.sh', 0),
            ('echo "${X:-default}" >> log', 0),
            ('cat <<EOF\nhello\nEOF', 0),
            ('"$CLAUDE_PROJECT_DIR"/.claude/hooks/x.py', 0),
            ('echo "$(date)" >> ~/log', 2),
            ('$HOME/.claude/hooks/x.sh', 2),
            ('"$HOOK"', 2),
            ('"$D"/pyth?n3 x.py', 2),
            ('/usr/bin/pyth{o,}n3 x.py', 2),
            ('X=1 "$PY" x.py', 2),
            ('env -i "$PY" x.py', 2),
            ('! "$PY" x.py', 2),
            ('echo a; "$PY" x.py', 2),
            ("$'pyth\\x6fn3' x.py", 2),
            ('~x', 2),
            ('echo `id`', 2),
            ('cat <(echo x)', 2),
            ("echo 'unbalanced", 2),
            ('"$PY"x x.py', 2),
            ('${PY} x.py', 2),
            ('"$D"$E/x.sh', 2),
            ('"$D"/~x/y.sh', 0),
            ('if true; then "$PY" x.py; fi', 2),
            ('{ "$PY" x.py; }', 2),
            ('( "$PY" x.py )', 2),
        )
        for k, (cmd, want) in enumerate(scope_rows):
            errs, fails = scan(settings_tree("scope-{}".format(k), cmd))
            got = 2 if errs else (1 if fails else 0)
            if got != want:
                failures.append("scope: the settings command {!r} expected exit {} (got exit {}, "
                                "errs={!r}, fails={!r})".format(cmd, want, got, errs, fails))

        # 10. The gate REFUSES (exit 2) when its own interpreter is not isolated (a real subprocess: no
        #     -I, so the bootstrap self-guard fires before any scan or sibling import).
        refuse = subprocess.run([sys.executable, str(Path(__file__).resolve())], capture_output=True,
                                text=True, env=env, cwd=str(tmp), timeout=30)
        if refuse.returncode != 2:
            failures.append("the gate should refuse to run non-isolated (expected exit 2, got {})"
                            .format(refuse.returncode))
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
          "glued to a `;` (`prep;python3`) is still segmented and caught in a settings shell-string and a "
          "run_all_checks.sh line (exit 1); a present-but-"
          "dangling optional settings symlink fails closed (exit 2) while a truly-absent one is a clean "
          "skip (exit 0); an attached -m/-c operand (-mIfoo, -cIbar) never forges isolation (exit 1); a "
          "redirect (`>&python3`, `2>&1`, `>&2`, `&>/dev/null`) is a cannot-evaluate (exit 2) while a "
          "genuine `&&`/background `&` still splits; a quote-obfuscated launcher NAME (`pyt\"hon\"3`, "
          "`py'thon'3`) that bash resolves to a real interpreter is caught on a run_all_checks.sh line and "
          "in a settings.json shell-string (exit 1) with its isolated form passing (exit 0), and the "
          "backslash-spelled `pyt\\hon3` is a cannot-evaluate (exit 2); a "
          "QA-suite Python source that reintroduces a sys.path index-0 insertion is a finding (exit 1) "
          "while the sanctioned sys.path.append form is clean (exit 0) and a missing required QA source "
          "fails closed (exit 2); the core hook launcher registered without -S before it fails (exit 1) "
          "while -I -S -B and -IS pass, and a real interpreter imports site under -I alone but not under "
          "-I -S -B; `-I -B -- <launcher>` fails the no-site rule in hooks.json args and in a settings "
          "shell-string (exit 1) while -S before `--` passes; in hooks.json and settings any token naming "
          "the core hook launcher fails unless the option scan reaches it cleanly as the script with -S "
          "before it (=VALUE long options, a long option inside a cluster, -m cProfile, a -c runpy "
          "operand and the launcher as another script's argument each fail in both forms, the S-carrying "
          "clean spellings pass, an args-form /usr/bin/env fails closed, and a real interpreter confirms "
          "those readings); a quoted shell operator is an argument, never a separator, so the quoted-"
          "operator runpy reproduction fails for every separator (escaped, it is a cannot-evaluate) while "
          "unquoted operators still split; a CI line that compiles or hashes the launcher passes while "
          "the same line as a settings hook command fails; a bare trailing `-` in a cluster hands the next "
          "token to the script operand, confirmed against a real interpreter; a second mention of the "
          "launcher after a compliant operand passes; every reviewer reproduction and every other construct "
          "outside the allow-listed shell grammar is a cannot-evaluate (exit 2) naming it while plain "
          "compliant commands pass and plain non-compliant ones fail; a settings hook command with no "
          "python word and no launcher name is out of scope (exit 0) with its redirections, `~` paths, "
          "groups and globs, unless a command word can hide a python word (exit 2), and the documented "
          "`\"$CLAUDE_PROJECT_DIR\"/...` script operand passes compliant (exit 0) and fails non-compliant "
          "(exit 1) while an expansion at the command word or among the options is exit 2; and the gate "
          "refuses to run non-"
          "isolated (exit 2)"
          + note)
    return 0


if __name__ == "__main__":
    sys.exit(main())
