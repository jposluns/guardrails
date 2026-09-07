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

LAUNCHER PREDICATE. In a scanned command, a leading ``env`` and any ``VAR=val`` assignments are skipped;
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

EXIT CONVENTION: 0 every recognized launcher is isolated; 1 at least one recognized launcher is not
isolated; 2 cannot-evaluate (a required input missing, unreadable, non-regular, or malformed; a JSON
parse error; a shell line carrying a python token whose command-word position cannot be established; a
hook entry with a missing type/command or a non-list args; or the gate's own interpreter not isolated).
Diagnostics are deterministic, sorted by relative path then line then location.

DISCLOSED RESIDUAL (this gate does not catch): wrapper or indirect launchers (``bash -c "python3 ..."``,
a ``.sh`` re-launcher), ``$PYTHON`` and shell aliases or functions, a dynamically assembled argv, the
programmatic ``subprocess`` children in the tools and the ``regenerate`` strings in .aiqt/gensrc.json
(a separate follow-up), a runtime ``sys.path`` mutation OTHER than a literal index-0 insertion in the
three enumerated QA-suite sources (a ``sys.path[0:0]`` slice, a computed index, a runtime-assembled
insertion, or one in another source is not caught, and even for those three ``-I`` cannot prevent a
runtime mutation the source performs), an unrecognized interpreter name, launcher configuration outside the enumerated surfaces,
YAML or shell constructs beyond the supported line grammar, and the PATH provenance of ``python3``
itself. A ``python3 tools/*.py`` token embedded in a quoted argument or a heredoc may be miscounted,
mirroring the roster-scan limit the enforceability ledger discloses.

  check_python_launcher_isolation.py             scan the declared surfaces
  check_python_launcher_isolation.py --self-test build synthetic trees and assert the gate's invariants

Run this gate isolated: python3 -I -B tools/check_python_launcher_isolation.py
"""
import sys


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
QUOTE_OR_ESCAPE_RE = re.compile(r"""['"\\]""")       # a quote or backslash that could obfuscate a launcher name
ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")  # a shell VAR=val inline assignment
# YAML `run:` key, optionally under a `- ` sequence dash; captures the leading indent and the scalar value.
RUN_RE = re.compile(r"^(?P<indent>\s*)(?:-\s+)?run:(?:[ \t]+(?P<val>.*))?$")
BLOCK_INDICATORS = {"|", ">", "|-", ">-", "|+", ">+"}
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
    """Classify one already-split command word list. Returns None if it is not a python launcher (out of
    scope), True if isolated, False if a python launcher that is not isolated."""
    rest = _strip_launcher_prefix(argv)
    if not rest or not _is_py_word(rest[0]):
        return None
    return _flags_isolated(rest[1:])


def _segments(tokens):
    """Split a token list into simple-command segments at shell separator tokens."""
    segments, cur = [], []
    for tok in tokens:
        if tok in SEPARATORS:
            if cur:
                segments.append(cur)
                cur = []
        else:
            cur.append(tok)
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


def _shell_split(s):
    """shlex.split after separating unquoted shell operators from adjacent words, so an operator
    glued to a word (`prep;python3`, `(python3`) tokenizes as a real shell would. Raises ValueError
    on an unbalanced quote exactly as shlex.split does, so callers surface it as a parse failure."""
    return shlex.split(_space_shell_operators(s), comments=False, posix=True)


def _scan_command_tokens(rel, lineno, loc, tokens, source_repr, errors, failures):
    """Scan an already-split token list for python launchers across its simple-command segments. Every
    python word must resolve to a command-word position; if the launcher count does not match the python
    words present, a launcher is hidden in an unexpected construct and this is a cannot-evaluate, so it is
    never silently passed. Each recognized launcher that is not isolated is a failure."""
    py_word_count = sum(1 for t in tokens if _is_py_word(t))
    launchers = []
    for seg in _segments(tokens):
        rest = _strip_launcher_prefix(seg)
        if rest and _is_py_word(rest[0]):
            launchers.append(rest)
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
    interpreter (`pyt\\hon3`, `pyt"hon"3`, `py'thon'3`) is caught. Fail-closed: a line that may carry a
    python launcher but cannot be parsed, or one whose python token cannot be resolved to a command
    word, is a cannot-evaluate, so a launcher hidden in an unexpected construct is never silently passed.

    The raw prefilter remains ONLY as a cheap early-accept that can never cause a miss: bash decoding
    strips quote/backslash characters and never inserts letters, so a decoded `python` launcher must
    leave in the raw line either its contiguous substring (matched by PY_TOKEN_RE) or a quote/backslash
    that broke that substring. A line carrying neither provably contains no launcher after decoding and
    is skipped; anything else is tokenized and decided on the decoded words."""
    code = _strip_comment(line)
    if not PY_TOKEN_RE.search(code) and not QUOTE_OR_ESCAPE_RE.search(code):
        return
    try:
        tokens = _shell_split(code)
    except ValueError as exc:
        errors.append((rel, lineno, "", "shell line may carry a python launcher but does not parse; "
                       "fail-closed: {}".format(exc)))
        return
    _scan_command_tokens(rel, lineno, "", tokens, code.strip(), errors, failures)


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
                    if verdict is False:
                        failures.append((rel, 0, hloc, "non-isolated launcher {!r}; requires {}"
                                         .format(" ".join(argv), REQUIRED_FORM)))
                else:
                    # The settings form: a shell string that may chain commands (e.g. `prep && python3
                    # x.py`), so segment-split and check every launcher segment, not just the first.
                    try:
                        tokens = _shell_split(command)
                    except ValueError as exc:
                        errors.append((rel, 0, hloc, "command string does not parse: {}".format(exc)))
                        continue
                    _scan_command_tokens(rel, 0, hloc, tokens, command, errors, failures)


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
          "fails closed (exit 2); and "
          "the gate refuses to run non-isolated (exit 2)" + note)
    return 0


if __name__ == "__main__":
    sys.exit(main())
