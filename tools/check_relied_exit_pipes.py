#!/usr/bin/env python3
"""Relied-exit-pipes gate for adopter-declared commands whose exit a pipe discards. Offline, stdlib only,
fail-closed.

A script that pipes a RELIED-ON command (a producer whose own termination status is the evidence later
steps rest on) into another command discards that producer's exit: the pipeline reports the LAST stage's
status, so under `set -e` a failed producer still reads as success and the script keeps going (gatdis:
when a command's own termination status is the evidence a verdict rests on, that status is taken only
from a construct that faithfully propagates it). This gate flags an adopter-declared relied-on command
sitting in a NON-FINAL stage of a pipeline (`|` or `|&`), at top level or inside a `$( )` or backquote
command-substitution body, in an adopter-declared shell surface.

The gate is ADOPTER-PARAMETERIZED: it ships NO command, subcommand, or path of its own. The adopter
authors `.aiqt/relied-exit-pipes.toml`, declaring which script surfaces to scan and which command words
(optionally narrowed to subcommands) are relied on. With NO configuration the gate prints NOT APPLICABLE
and exits clean; a present but unusable configuration never does (fail-closed). A pipe whose left side
is not a declared command never fires, which keeps false alarms low: `git log | head` is not a finding.

TWO EXEMPTIONS clear a flagged pipeline. Each is credited only for a closed set of spellings the gate
can read with certainty; every other spelling refuses toward a finding, never toward a silent pass:
  E1  pipefail is provably on. The credit needs BOTH: (1) every mention of `pipefail` anywhere in the
      file (comments and strings included) sits on a line that IS a standalone top-level enable: the
      line starts with `set` in column 1 (no wrapper, coproc, builtin, env, exec, nohup or timeout
      before it), carries only enabling short options (`set -o pipefail`, or an option cluster
      containing -o such as `set -euo pipefail`), and ends at the newline or one trailing ';' with
      nothing after it; and (2) the file contains NO function definition, eval, source, `.` (dot
      command), bash -c / sh -c / dash -c / zsh -c, builtin, `set +...`, `shopt -u...`, or `set` with a
      variable-driven operand anywhere: each of those can move, disable or re-scope the option in ways
      this gate does not model, so its mere presence withdraws the credit for the whole file. The
      enable must additionally be a top-level statement in the lexed reading (not inside a block or
      subshell, not a pipeline stage, not backgrounded, not the right arm of && or ||) and sit before
      the pipeline. `set -- -o pipefail` sets positional parameters, never the option.
  E2  the script reads the command's own exit: the pipeline is a top-level statement ending at a
      newline or one ';' (never '&', '&&' or '||'), nothing but whitespace and at most that ';'
      follows the final stage on its own line, and the IMMEDIATELY NEXT LINE is exactly one of a
      closed set of read forms, matched on the lexically intact line (never on text with quotes
      removed): NAME=${PIPESTATUS[N]} or NAME="${PIPESTATUS[N]}" with N exactly the flagged stage's
      index, NAME=("${PIPESTATUS[@]}"), or declare -a NAME=("${PIPESTATUS[@]}"). A blank or comment
      line before the read, an extra word on the read line, or any other spelling (an unquoted array
      save, arithmetic, a different index) is not credited.
Any final stage counts as a sink, not only a truncating one: the exit is lost whatever the last stage is.

Configuration schema (`.aiqt/relied-exit-pipes.toml`), adopter-authored:

  format-version = 1                 the exact integer 1 (true and 1.0 do not pass)

  [[binding]]
  id = "row-writer"                  a stable, unique kebab-case identifier
  paths = ["path/to/file", "dir/**"]  repo-relative surfaces: an exact file, or a directory prefix
                                      ending in /**; each selector must match at least one readable
                                      regular file, with no symlinks and no escaping the repository;
                                      a dir/** walk covers EVERY regular file beneath it, and refuses
                                      (exit 2) any .git or __pycache__ entry, symlink, or non-regular
                                      file it meets, never pruning or skipping them silently
  commands = ["record-tool"]          relied-on command basenames (matched at command-word position,
                                      after NAME=value assignments and the common wrappers used BARE;
                                      a wrapper given ANY option word, and a declared command inside a
                                      subshell, brace group, loop, function body, coproc, or behind
                                      xargs/flock/eval/sh -c and kin, refuses with exit 2)
  subcommands = ["write"]             OPTIONAL: when present, only these first operands count as relied

  check_relied_exit_pipes.py               scan the surfaces the configuration declares
  check_relied_exit_pipes.py --self-test   deterministic self-test over synthetic trees

Exit convention (matches the repo's gates):
  0  clean, or a printed NOT APPLICABLE (the configuration path is CONFIRMED absent; an inspection
     failure such as an unreadable parent directory is exit 2, never a silent clean)
  1  a real finding (a declared relied-on command in a non-final pipeline stage, neither exemption held)
  2  cannot evaluate, fail-closed: a malformed, unreadable, uninspectable or non-regular (e.g. FIFO)
     configuration, an unmatched path, command or subcommand selector, a selector with an empty, '.'
     or '..' component or a symlinked component, a symlinked directory, symlinked file, .git or
     __pycache__ entry, or non-regular file met in a directory walk, a non-regular or non-UTF-8
     declared surface, a declared command inside or after shell the gate cannot parse (an unquoted
     heredoc, a here-string, a process substitution, an unterminated construct; the refusal covers the
     file's remainder from that point), a declared command inside the raw text of an `sh -c`,
     `bash -c`, `dash -c`, `zsh -c` or `eval` argument (clustered options such as `-ec` included), in
     the argument list of `xargs` or `flock`, beside a `coproc`, possibly hidden behind a wrapper's
     option words (any option word after a wrapper: separate, attached with '=', or '--'), a declared
     command whose subcommand sits behind option words, a declared command inside a subshell, brace
     group, loop body or function body (constructs whose execution time or shell this gate does not
     model), or a grouped or compound pipeline producer (a subshell, brace group, or loop piped
     onward) in a file that mentions a declared command.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: check_relied_exit_pipes.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import collections
import os
import re
import stat
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # not a version problem: every Python 3.14 ships tomllib
    import os
    try:
        sys.stderr.write(
            "error: check_relied_exit_pipes.py cannot import tomllib, part of the Python standard library; "
            "this installation is incomplete. Nothing was run (cannot evaluate).\n")
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

CONFIG_REL = ".aiqt/relied-exit-pipes.toml"
ID_RE = re.compile(r"^[a-z][a-z0-9-]*$")                       # a kebab-case binding identifier
ENV_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\+?=")     # a NAME=value assignment before a command
BINDING_REQUIRED = frozenset(("id", "paths", "commands"))
BINDING_OPTIONAL = frozenset(("subcommands",))
# Wrapper words skipped at command-word position when used BARE: the real command follows them. Any
# option word after one (separate, attached with '=', or '--') makes the segment opaque (fail-closed).
WRAPPERS = frozenset(("env", "nice", "timeout", "time", "command", "exec", "nohup", "stdbuf", "sudo"))
# Shell reserved words that keep the command-word position open (the real command follows). A reserved
# word counts ONLY when spelled plain: a quoted or escaped spelling ("select") is a command name.
RESERVED_SKIP = frozenset(("if", "then", "else", "elif", "do", "while", "until", "for", "case",
                           "select", "{", "}", "!", "coproc"))
BLOCK_OPEN = frozenset(("if", "while", "until", "for", "case", "select", "{"))
BLOCK_CLOSE = frozenset(("fi", "done", "esac", "}"))
PIPE_SEPS = ("|", "|&")
MAX_SUBST_DEPTH = 20
# Commands that hand their argument words to another execution environment: a declared command among
# them cannot be attributed to a pipeline stage, so it refuses (exit 2).
ARG_EXECUTORS = frozenset(("xargs", "flock"))
# Block-opener kinds a declared command may not sit under: the construct's execution time (a loop, a
# function called later) or count is not modelled, so the gate refuses rather than judging around it.
# if/then/else and case arms stay walked: they run at most once, in this shell, where they sit.
REFUSED_BODIES = {"while": "a 'while' loop", "until": "an 'until' loop",
                  "for": "a 'for' loop", "select": "a 'select' loop",
                  "{": "a brace group", "func": "a function body"}
# Directory-walk entry names that are tool metadata: met inside a declared surface they refuse,
# never silently pruned (a declared subtree must be scanned whole or not at all).
METADATA_NAMES = frozenset((".git", "__pycache__"))

# A scanning context: text is the (possibly masked) shell text, base the absolute offset of its first
# character in the original file, is_top True only for the file's own top level (a substitution body is
# not top level: E2 never applies there and a `set -o pipefail` inside one is not a top-level enable),
# and truncated the local offset where extraction stopped on an unterminated construct (None when whole).
_Ctx = collections.namedtuple("_Ctx", ("text", "base", "is_top", "truncated"))


class GateError(Exception):
    """An input the gate cannot read, parse, or resolve. Caught at run() and reported as exit 2
    (fail-closed): an unreadable configuration or declared surface is never treated as clean."""


_AIQT_HOOKS = None


def _hooks():
    """The repository's shared shell lexer module (aiqt_hooks), imported lazily so the NOT APPLICABLE
    path needs no lexer, and fail-closed when it cannot be imported."""
    global _AIQT_HOOKS
    if _AIQT_HOOKS is None:
        hooks_dir = Path(__file__).resolve().parents[1] / ".aiqt" / "core" / "hooks" / "scripts"
        if str(hooks_dir) not in sys.path:
            sys.path.insert(0, str(hooks_dir))
        try:
            import aiqt_hooks
        except BaseException as exc:
            raise GateError("cannot import the shared shell lexer aiqt_hooks ({})".format(exc))
        _AIQT_HOOKS = aiqt_hooks
    return _AIQT_HOOKS


# --- substitution extraction -------------------------------------------------------------------------
# The shared lexer reads an unquoted `$( )` as a word boundary plus grouping and a backquote as an
# ordinary opaque character, so a pipeline INSIDE a substitution body would leak into, or hide from, the
# top-level reading. This extractor pulls each `$( )` and backquote body out as its own context and masks
# the span in the parent with a LENGTH- AND NEWLINE-PRESERVING quoted filler, so every offset and line
# number keeps indexing the original file. Single-quoted and ANSI-C-quoted text, comments, and heredoc
# bodies are never extracted from (their content is data, or is handled by the lexer's own refusal).

def _match_subst_close(text, i, n):
    """The index of the `)` closing the `$(` whose body starts at i, or -1 when unterminated. Honors
    nested parentheses, single and double quotes, backslash escapes, backquotes, and nested `$(`."""
    depth = 1
    word_break = True
    while i < n:
        c = text[i]
        if c == "\\":
            i += 2
            word_break = False
            continue
        if c == "'":
            j = text.find("'", i + 1)
            if j == -1:
                return -1
            i = j + 1
            word_break = False
            continue
        if c == '"':
            i += 1
            while i < n:
                d = text[i]
                if d == "\\":
                    i += 2
                    continue
                if d == '"':
                    break
                if d == "`":
                    j = i + 1
                    while j < n and text[j] != "`":
                        j += 2 if text[j] == "\\" else 1
                    if j >= n:
                        return -1
                    i = j + 1
                    continue
                if d == "$" and i + 1 < n and text[i + 1] == "(":
                    j = _match_subst_close(text, i + 2, n)
                    if j == -1:
                        return -1
                    i = j + 1
                    continue
                i += 1
            if i >= n:
                return -1
            i += 1
            word_break = False
            continue
        if c == "`":
            j = i + 1
            while j < n and text[j] != "`":
                j += 2 if text[j] == "\\" else 1
            if j >= n:
                return -1
            i = j + 1
            word_break = False
            continue
        if c == "#" and word_break:
            nl = text.find("\n", i)
            i = n if nl == -1 else nl
            continue
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return i
        word_break = c in " \t\n;&|("
        i += 1
    return -1


def _extract(text):
    """One extraction level over one context's text. Returns (masked_text, subs, truncated): masked_text
    is text with every top-level-of-this-context `$( )` and backquote substitution replaced by a quoted
    filler of IDENTICAL length and newline positions; subs is a list of (body_text, local_offset) pairs,
    each to be scanned as its own context; truncated is the local offset where the walk stopped on an
    unterminated quote or substitution (None when the whole text was walked). Heredoc bodies (quoted or
    not) are skipped, never extracted from: a quoted body is literal data, and an unquoted heredoc makes
    the lexer refuse the opener, so the declared-word check over the unparsed tail still sees the
    ORIGINAL body text."""
    out = list(text)
    subs = []
    n = len(text)
    i = 0
    in_dq = False
    word_break = True
    pending = []  # queued (delimiter, strip_tabs) heredoc bodies to skip at the next newline

    def mask(a, b):
        out[a] = "'"
        for k in range(a + 1, b - 1):
            if text[k] != "\n":
                out[k] = "x"
        out[b - 1] = "'"

    while i < n:
        c = text[i]
        if c == "\n" and not in_dq and pending:
            i += 1
            for delim, strip in pending:
                while i < n:
                    nl = text.find("\n", i)
                    line_end = n if nl == -1 else nl
                    line = text[i:line_end]
                    if strip:
                        line = line.lstrip("\t")
                    i = line_end + 1 if nl != -1 else n
                    if line == delim:
                        break
            pending = []
            word_break = True
            continue
        if c == "\\":
            i += 2
            word_break = False
            continue
        if c == "'" and not in_dq:
            j = text.find("'", i + 1)
            if j == -1:
                return "".join(out), subs, i
            i = j + 1
            word_break = False
            continue
        if c == "$" and not in_dq and i + 1 < n and text[i + 1] == "'":
            j = i + 2
            while j < n and text[j] != "'":
                j += 2 if text[j] == "\\" else 1
            if j >= n:
                return "".join(out), subs, i
            i = j + 1
            word_break = False
            continue
        if c == '"':
            in_dq = not in_dq
            i += 1
            word_break = False
            continue
        if c == "`":
            j = i + 1
            body = []
            while j < n and text[j] != "`":
                if text[j] == "\\" and j + 1 < n and text[j + 1] in "`\\$":
                    body.append(text[j + 1])
                    j += 2
                else:
                    body.append(text[j])
                    j += 1
            if j >= n:
                return "".join(out), subs, i
            subs.append(("".join(body), i + 1))
            mask(i, j + 1)
            i = j + 1
            word_break = False
            continue
        if c == "$" and i + 1 < n and text[i + 1] == "(":
            j = _match_subst_close(text, i + 2, n)
            if j == -1:
                return "".join(out), subs, i
            subs.append((text[i + 2:j], i + 2))
            mask(i, j + 1)
            i = j + 1
            word_break = False
            continue
        if in_dq:
            i += 1
            continue
        if c == "#" and word_break:
            nl = text.find("\n", i)
            i = n if nl == -1 else nl
            continue
        if c == "<" and text[i:i + 3] == "<<<":
            i += 3
            word_break = True
            continue
        if c == "<" and text[i:i + 2] == "<<":
            k = i + 2
            strip = False
            if k < n and text[k] == "-":
                strip = True
                k += 1
            while k < n and text[k] in " \t":
                k += 1
            delim = []
            while k < n and text[k] not in " \t\n;&|()<>":
                if text[k] in "'\"":
                    q = text[k]
                    e = text.find(q, k + 1)
                    if e == -1:
                        return "".join(out), subs, i
                    delim.append(text[k + 1:e])
                    k = e + 1
                elif text[k] == "\\" and k + 1 < n:
                    delim.append(text[k + 1])
                    k += 2
                else:
                    delim.append(text[k])
                    k += 1
            if delim:
                pending.append(("".join(delim), strip))
            i = k
            word_break = True
            continue
        word_break = c in " \t\n;&|("
        i += 1
    return "".join(out), subs, None


def _contexts(text):
    """Every scanning context of one file: the top level, then each substitution body, recursively.
    Fail-closed on pathological nesting."""
    result = []

    def descend(t, base, is_top, depth):
        if depth > MAX_SUBST_DEPTH:
            raise GateError("command substitutions nested deeper than {} levels; cannot evaluate"
                            .format(MAX_SUBST_DEPTH))
        masked, subs, truncated = _extract(t)
        result.append(_Ctx(masked, base, is_top, truncated))
        for body, off in subs:
            descend(body, base + off, False, depth + 1)

    descend(text, 0, True, 0)
    return result


# --- command-word and pipeline reading ---------------------------------------------------------------

def _plain_word(raw, word):
    """True when `word` occurs in raw as one contiguous token spelled plain: delimited by whitespace,
    separators or the text's ends, with no quote, escape or any other character touching it. A reserved
    word counts as reserved only when it passes this test; a quoted or escaped spelling ("select",
    to\\ol) is an ordinary command word in bash, never syntax."""
    pattern = r"(?<![^ \t\n;&|(){}<>])" + re.escape(word) + r"(?![^ \t\n;&|(){}<>])"
    return re.search(pattern, raw or "") is not None


def _command_word(argv, raw=None):
    """The command word of one segment: (index, basename, opens_before, opaque). Skips shell reserved
    words that keep the command position open (counting the block openers among them, so a command
    inside this segment's own `if` or `while` head reads as one level deeper), NAME=value assignments,
    the common wrappers used bare, and a duration-like operand after `timeout`. ANY option word after a
    wrapper (separate, value-attached with '=', or '--') makes the result OPAQUE (index and basename
    None, opaque True): whether the following words are option values or the command cannot be told
    from the text (nice -n 10 CMD versus nice -x CMD), so the caller fails closed when a declared name
    could hide behind it. When raw is given, a reserved word is skipped only when `_plain_word` shows
    it spelled plain: a quoted or escaped spelling is a command name in bash, so it is returned as the
    command word instead (raw=None, used by the pure-argv unit layer, treats every word as plain)."""
    opens = 0
    i = 0
    after_wrapper = False
    after_timeout = False
    while i < len(argv):
        w = argv[i]
        if w in RESERVED_SKIP and (raw is None or _plain_word(raw, w)):
            if w in BLOCK_OPEN:
                opens += 1
            i += 1
            continue
        if ENV_ASSIGN_RE.match(w):
            i += 1
            continue
        if after_wrapper and w.startswith("-"):
            return None, None, opens, True
        if after_timeout and w[:1].isdigit():
            after_timeout = False
            i += 1
            continue
        base = w.rsplit("/", 1)[-1] if w else ""
        if base in WRAPPERS:
            after_wrapper = True
            after_timeout = base == "timeout"
            i += 1
            continue
        return i, base, opens, False
    return None, None, opens, False


# Sentinel for a first operand that cannot be told from the text: an option word before it may consume
# the following word as its value, so the subcommand position is unprovable and the caller fails closed.
_AMBIGUOUS = object()


def _first_operand(argv, cmd_i):
    """The first non-option operand after the command word (the subcommand position), None when there is
    none, or _AMBIGUOUS when a separate-word option sits before the first operand (tool --config x.toml
    write: is 'x.toml' the option's value or the subcommand?). An option carrying its value in the same
    word (it contains '=') is skipped, and after '--' every word is an operand."""
    j = cmd_i + 1
    past_options = False
    while j < len(argv):
        w = argv[j]
        if not past_options and w == "--":
            past_options = True
            j += 1
            continue
        if not past_options and w.startswith("-"):
            if "=" in w:
                j += 1
                continue
            return _AMBIGUOUS
        return w
    return None


def _declared_command(base, commands):
    """True when the resolved command basename is one the adopter declared relied-on."""
    return base in commands


def _subcommand_ok(subcommands, sub):
    """True when the binding's subcommand narrowing admits this invocation."""
    return sub is not None and sub in subcommands


def _nonfinal(stages):
    """The pipeline stages whose exit the pipe discards: every stage but the last."""
    return stages[:-1]


def _set_pipefail_effect(argv):
    """The pipefail effect of one `set` command's argv (argv[0] == 'set'): 'on', 'off', or None.
    Reads `-o pipefail`, `+o pipefail`, and clusters such as `-euo pipefail` or `-Eeo pipefail`
    (each 'o' in a cluster consumes the next operand as an option name). Option parsing TERMINATES at
    `--`, at a lone `-`, or at the first word that is not an option: everything from there on sets the
    positional parameters, so `set -- -o pipefail` and `set foo +o pipefail` have no pipefail effect."""
    effect = None
    i = 1
    n = len(argv)
    while i < n:
        w = argv[i]
        if w in ("--", "-") or not (len(w) >= 2 and w[0] in "-+" and w[1] != "-"):
            break
        sign = w[0]
        consumed = i
        for ch in w[1:]:
            if ch == "o" and consumed + 1 < n and argv[consumed + 1][:1] not in ("-", "+", ""):
                consumed += 1
                if argv[consumed] == "pipefail":
                    effect = "on" if sign == "-" else "off"
        i = consumed + 1
    return effect


# One enabling-set option token: a '-' sign followed by short option letters only. '--', a lone '-',
# any '+' sign and any longer or quoted spelling fall outside the closed set and earn no credit.
_E1_OPTION_RE = re.compile(r"^-[A-Za-z]+$")
_E1_NAME_RE = re.compile(r"^[a-z]+$")  # an option-name operand after 'o' (pipefail, errexit, ...)

# Constructs whose PRESENCE anywhere in a file withdraws the pipefail credit for that whole file: each
# can define, defer, disable or re-scope the option in a way this bounded reader does not model (a
# function body runs at its call sites, eval/source/'.'/sh -c run text the lexer never sees, builtin
# reaches set indirectly, 'set +' and 'shopt -u' spell disables, and a '$' in a set line makes the
# option variable-driven). Matching is textual and deliberately over-broad: a comment or string that
# merely LOOKS like one of these also withdraws the credit, which refuses toward a finding, never
# toward a silent pass.
_E1_FILE_DISQUALIFIERS = (
    re.compile(r"(?<![\w.$-])function(?![\w.-])"),               # function NAME { ... }
    re.compile(r"[A-Za-z_][A-Za-z0-9_]*[ \t]*\([ \t]*\)"),       # NAME() { ... }
    re.compile(r"(?<![\w.$-])eval(?![\w.-])"),
    re.compile(r"(?<![\w.$-])source(?![\w.-])"),
    re.compile(r"(?m)(?:^|(?<=[;&|( \t]))\.[ \t]"),              # the '.' (dot) command
    re.compile(r"(?m)(?<![\w.$-])(?:ba|da|z)?sh[ \t][^\n]*?(?<=[ \t])-[A-Za-z]*c(?![\w-])"),  # sh ... -c
    re.compile(r"(?<![\w.$-])builtin(?![\w.-])"),
    re.compile(r"(?m)(?<![\w.$-])set[ \t]+[^\n]*\+"),            # set +o pipefail and every other '+' form
    re.compile(r"(?m)(?<![\w.$-])shopt[ \t][^\n]*?(?<=[ \t])-[A-Za-z]*u(?![\w-])"),  # shopt -u / -uo
    re.compile(r"(?m)(?<![\w.$-])set[ \t][^\n]*\$"),             # set with a variable-driven operand
)


def _e1_enable_line(line):
    """True when `line` (one line of the file, without its newline) is exactly a standalone
    pipefail-enabling set command: `set` first, then only plain short-option tokens whose 'o' letters
    consume lowercase option-name operands, turning pipefail ON, with nothing else before the end of
    the line or one trailing ';'. Anything outside this closed set (a wrapper before set, '--', a '+'
    sign, an operand, a second command after ';', a trailing comment, a '$') earns no credit."""
    s = line.strip()
    if s.endswith(";"):
        s = s[:-1].rstrip()
    if ";" in s:
        return False
    toks = s.split()
    if len(toks) < 2 or toks[0] != "set":
        return False
    enabled = False
    i = 1
    while i < len(toks):
        w = toks[i]
        if not _E1_OPTION_RE.match(w):
            return False
        for ch in w[1:]:
            if ch == "o":
                i += 1
                if i >= len(toks) or not _E1_NAME_RE.match(toks[i]):
                    return False
                if toks[i] == "pipefail":
                    enabled = True
        i += 1
    return enabled


def _e1_file_credit(text):
    """True when this file may earn the pipefail exemption at all: no credit-withdrawing construct
    appears anywhere (see _E1_FILE_DISQUALIFIERS), and every mention of 'pipefail' sits on a line that
    is itself a standalone column-1 enabling set command (_e1_enable_line). A mention on any other line
    (a disable, a conditional or wrapped enable, a comment, a string) withdraws the credit for the
    whole file: the gate credits only what it can read with certainty."""
    for rx in _E1_FILE_DISQUALIFIERS:
        if rx.search(text):
            return False
    for m in re.finditer(r"pipefail", text):
        start = text.rfind("\n", 0, m.start()) + 1
        end = text.find("\n", m.start())
        line = text[start:] if end == -1 else text[start:end]
        if not (line.startswith("set ") or line.startswith("set\t")) or not _e1_enable_line(line):
            return False
    return True


def _e1_exempt(events_sorted, offset):
    """True when pipefail is provably on at `offset`: an earlier top-level enable with no later disable
    (at any level) in between. events_sorted is the file's (offset, 'on'|'off') list, sorted."""
    state = False
    for off, kind in events_sorted:
        if off >= offset:
            break
        state = kind == "on"
    return state


def _executes(seg):
    """True when a lexed segment runs anything: it has words or redirects, or its raw text carries
    something besides whitespace and comments. A blank line or comment-only record executes nothing, so
    it neither ends a pipeline whose pipe sits at the end of the previous line nor counts as the 'very
    next' command for the PIPESTATUS exemption. A redirection-only command still executes (it can
    change the status), so it is never skipped."""
    if seg.argv or seg.redirects:
        return True
    for line in (seg.raw or "").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            return True
    return False


# The closed set of PIPESTATUS read forms: a scalar save of one index (bare or double-quoted, the
# quotes paired), a whole-array save, or a declare -a whole-array save. The whole line must be exactly
# one of these, matched lexically intact: quotes are never removed before matching, so a spelling that
# only LOOKS like an expansion once quotes are dropped ("$"{PIPESTATUS[0]}) never exempts.
_E2_SCALAR_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*=(")?\$\{PIPESTATUS\[(\d+)\]\}(")?$')
_E2_ARRAY_RE = re.compile(r'^(?:declare -a )?[A-Za-z_][A-Za-z0-9_]*=\("\$\{PIPESTATUS\[@\]\}"\)$')


def _e2_read_line(text, end, sep):
    """The lexically intact line immediately after the pipeline's final line, or None when the read
    cannot be adjacent: anything but whitespace and at most the one ';' separator follows the final
    stage on its own line (a further command, a comment after ';', or end of file). `end` is the
    absolute offset just past the final stage's raw text; `sep` is the pipeline's own separator."""
    i = end
    n = len(text)
    seen_semi = False
    while i < n and text[i] != "\n":
        c = text[i]
        if c in " \t":
            i += 1
            continue
        if c == ";" and sep == ";" and not seen_semi:
            seen_semi = True
            i += 1
            continue
        return None
    if i >= n:
        return None
    j = text.find("\n", i + 1)
    return text[i + 1:j if j != -1 else n]


def _e2_exempt(top_level, read_line, stage_index):
    """True when the script reads the flagged stage's own exit: the pipeline is a top-level statement
    and the immediately next line is exactly one of the closed read forms, NAME=${PIPESTATUS[N]} or
    NAME="${PIPESTATUS[N]}" with N this stage's index, NAME=("${PIPESTATUS[@]}"), or
    declare -a NAME=("${PIPESTATUS[@]}"). Lexical only: the gate does not prove the script acts on the
    value, and every other spelling (a bare unquoted array save, arithmetic, an extra word on the line)
    is not recognized, which refuses toward a finding, never toward a silent pass."""
    if not top_level or read_line is None:
        return False
    s = read_line.strip()
    m = _E2_SCALAR_RE.match(s)
    if m is not None and (m.group(1) or "") == (m.group(3) or ""):
        return int(m.group(2)) == stage_index
    return _E2_ARRAY_RE.match(s) is not None


def _judge(pending, events):
    """The final findings: every pending non-final relied stage whose pipeline the E1 pipefail
    exemption does not clear (E2 was already applied where it could hold). A stage inside a function
    body or any other nested block (depth > 0) may run later than it sits in the text, so for it E1
    holds only when no disable follows anywhere in the file: the enclosing body could otherwise run
    after the disable."""
    ordered = sorted(events)
    kept = []
    for rec in pending:
        if _e1_exempt(ordered, rec["offset"]):
            if rec["depth"] == 0:
                continue
            if not any(kind == "off" and off >= rec["offset"] for off, kind in ordered):
                continue
        kept.append(rec)
    return kept


def _declared_regex(commands):
    """A word-boundary regex matching any declared command name, for the fail-closed checks over text
    the gate cannot read as parsed shell. A preceding '/' is allowed on purpose: /usr/local/bin/tool
    and ./tool are path-qualified spellings of the declared basename, so they must match; a preceding
    letter, digit, '_', '.' or '-' still means a longer, different name and does not."""
    alts = "|".join(re.escape(c) for c in sorted(commands))
    return re.compile(r"(?<![A-Za-z0-9_.-])(?:{})(?![A-Za-z0-9_./-])".format(alts))


def _scan_context(ctx, original_text, rel, binding, telemetry, events, pending, declared_re):
    """Scan one context's segments: record set-pipefail events (crediting an enable only when it
    provably reaches this shell AND its line is the standalone column-1 closed form) and
    command/subcommand telemetry, raise GateError (exit 2) on a declared command inside unparseable
    shell, inside an sh -c / bash -c / dash -c / zsh -c / eval argument (clustered options included),
    in the argument list of xargs or flock, beside a coproc, possibly hidden behind wrapper option
    words or an ambiguous subcommand position, inside a subshell, brace group, loop body or function
    body, or beside a grouped pipeline producer this gate cannot attribute, and append a pending
    finding for every declared relied-on command in a non-final pipeline stage that E2 does not clear.
    Declared-command identity is read from the shell-DEQUOTED words and from the UNMASKED original
    text (substitution bodies included), so a quoted spelling or a mention living only inside $( )
    cannot slip a refused construct."""
    hooks = _hooks()
    commands = set(binding["commands"])
    subcommands = set(binding.get("subcommands") or ())
    try:
        segments = hooks._lex_command(ctx.text)
        complete, in_progress = True, False
    except ValueError:
        segments, complete, in_progress = hooks._lex_command(ctx.text, partial=True)

    offs = []
    cursor = 0
    for seg in segments:
        if seg.raw:
            pos = ctx.text.find(seg.raw, cursor)
            if pos < 0:
                pos = cursor
            offs.append(pos)
            cursor = pos + len(seg.raw)
        else:
            offs.append(cursor)

    kept = list(segments)
    unparsed_at = None
    if in_progress:
        kept = kept[:-1]
        unparsed_at = offs[-1]
    elif not complete:
        unparsed_at = cursor
    if ctx.truncated is not None and (unparsed_at is None or ctx.truncated < unparsed_at):
        unparsed_at = ctx.truncated
    if unparsed_at is not None:
        tail = ctx.text[unparsed_at:]
        if declared_re.search(tail):
            line = original_text.count("\n", 0, min(ctx.base + unparsed_at, len(original_text))) + 1
            raise GateError("{}:{}: binding {!r}: a declared command sits in shell this gate cannot "
                            "parse (an unquoted heredoc, a here-string, a process substitution, or an "
                            "unterminated construct); cannot evaluate".format(rel, line, binding["id"]))

    def line_of(abs_offset):
        return original_text.count("\n", 0, min(abs_offset, len(original_text))) + 1

    def declared_in_words(words):
        return any(w and w.rsplit("/", 1)[-1] in commands for w in words)

    def unmasked_span(idx, seg):
        start = ctx.base + offs[idx]
        return original_text[start:start + len(seg.raw or "")]

    # Declared-command presence for the whole context, from the dequoted words and the unmasked text
    # (substitution bodies included): the grouped-producer backstop below reads this, never the masked
    # filler, so a declared command living only inside a piped group's $( ) still refuses.
    ctx_declared = bool(declared_re.search(original_text[ctx.base:ctx.base + len(ctx.text)])) \
        or any(declared_in_words(s.argv) for s in kept)

    blocks = []        # the stack of open block kinds ('if', 'while', ..., '{', 'func')
    paren_depth = 0
    case_depth = 0
    func_pending = False
    prev_sep = ""
    run_stages = []  # (segment index, absolute offset, relied, basename, effective depth, opaque)
    for idx, seg in enumerate(kept):
        if run_stages and not _executes(seg) and seg.sep_after in ("",) + PIPE_SEPS:
            # A blank or comment-only record directly after a pipe: the pipeline continues on the next
            # executing line, so this record is neither a stage nor the end of the statement.
            continue
        lead = len(seg.raw) - len(seg.raw.lstrip()) if seg.raw else 0
        abs_off = ctx.base + offs[idx] + lead
        argv = seg.argv
        raw = seg.raw or ""
        cmd_i, base, opens_before, opaque = _command_word(argv, raw)
        eff_depth = len(blocks) + paren_depth + opens_before
        relied = False
        seg_declared = declared_in_words(argv) or bool(declared_re.search(unmasked_span(idx, seg)))
        if opaque and seg_declared:
            raise GateError("{}:{}: binding {!r}: a declared command may sit behind wrapper option "
                            "words this gate cannot parse (the option may consume the following word); "
                            "cannot evaluate".format(rel, line_of(abs_off), binding["id"]))
        if seg_declared and _executes(seg):
            # A declared command is judged only as a top-level simple command at the head of a
            # pipeline stage (if/then/else and case arms are still walked). Inside a subshell, brace
            # group, loop or function body its execution time or shell is not modelled: refuse.
            construct = None
            if paren_depth > 0:
                construct = "a subshell"
            else:
                for kind in reversed(blocks):
                    if kind in REFUSED_BODIES:
                        construct = REFUSED_BODIES[kind]
                        break
            if construct is None:
                for w in argv[:cmd_i] if cmd_i is not None else argv:
                    if w in REFUSED_BODIES and _plain_word(raw, w):
                        construct = REFUSED_BODIES[w]
                        break
            if construct is not None:
                raise GateError("{}:{}: binding {!r}: a declared command inside {} is not a top-level "
                                "simple command this gate can attribute; cannot evaluate".format(
                                    rel, line_of(abs_off), binding["id"], construct))
            if cmd_i is not None and any(w == "coproc" and _plain_word(raw, w)
                                         for w in argv[:cmd_i]):
                raise GateError("{}:{}: binding {!r}: a declared command beside a coproc runs in a "
                                "coprocess shell this gate does not model; cannot evaluate".format(
                                    rel, line_of(abs_off), binding["id"]))
        if base is not None:
            if base == "eval" or (base in ("sh", "bash", "dash", "zsh") and any(
                    len(w) >= 2 and w[0] == "-" and w[1] != "-" and "c" in w[1:]
                    for w in argv[cmd_i + 1:])):
                joined = " ".join(argv[cmd_i + 1:])
                if declared_re.search(joined):
                    label = "eval" if base == "eval" else base + " -c"
                    article = "an" if label[:1] == "e" else "a"
                    raise GateError("{}:{}: binding {!r}: a declared command appears inside the raw "
                                    "text of {} {} argument, which this gate cannot read as parsed "
                                    "shell; cannot evaluate".format(rel, line_of(abs_off),
                                                                    binding["id"], article, label))
            if base in ARG_EXECUTORS and (declared_in_words(argv[cmd_i + 1:])
                                          or declared_re.search(" ".join(argv[cmd_i + 1:]))):
                raise GateError("{}:{}: binding {!r}: a declared command appears in the argument list "
                                "of {!r}, which may execute it outside this pipeline reading; cannot "
                                "evaluate".format(rel, line_of(abs_off), binding["id"], base))
            if _declared_command(base, commands):
                telemetry["commands"].add(base)
                sub = _first_operand(argv, cmd_i)
                if subcommands:
                    if sub is _AMBIGUOUS:
                        raise GateError("{}:{}: binding {!r}: option words before the first operand of "
                                        "declared command {!r} hide whether a declared subcommand is "
                                        "invoked; cannot evaluate".format(rel, line_of(abs_off),
                                                                          binding["id"], base))
                    if _subcommand_ok(subcommands, sub):
                        telemetry["subcommands"].add(sub)
                        relied = True
                else:
                    relied = True
            if base == "set":
                effect = _set_pipefail_effect(argv[cmd_i:])
                if effect == "off":
                    events.append((abs_off, "off"))
                elif (effect == "on" and ctx.is_top and eff_depth == 0 and not run_stages
                        and seg.sep_after in ("", ";") and prev_sep not in ("&&", "||")):
                    # Credit an enable only when it provably reaches this shell: not a pipeline stage
                    # (its own separator a pipe, or a prior stage piping into it), not backgrounded,
                    # not the right-hand arm of && or || (it may never run), ending at a newline or
                    # one ';', AND spelled as the standalone column-1 closed form on its own line.
                    line_start = original_text.rfind("\n", 0, abs_off) + 1
                    line_end = original_text.find("\n", abs_off)
                    line = original_text[line_start:] if line_end == -1 \
                        else original_text[line_start:line_end]
                    if abs_off == line_start and _e1_enable_line(line):
                        events.append((abs_off, "on"))
            elif base == "shopt":
                flags = set()
                names = []
                for w in argv[cmd_i + 1:]:
                    if len(w) >= 2 and w[0] == "-" and w[1] != "-":
                        flags.update(w[1:])
                    else:
                        names.append(w)
                if "o" in flags and "u" in flags and "pipefail" in names:
                    # shopt -uo pipefail turns the option off; recorded like set +o pipefail, at any
                    # depth. The enabling spelling stays uncredited (same stance as shopt -so).
                    events.append((abs_off, "off"))
        run_stages.append((idx, abs_off, relied, base, eff_depth, opaque))
        if seg.sep_after not in PIPE_SEPS:
            if len(run_stages) > 1:
                for _si, soff, _sr, sbase, _sd, sopq in _nonfinal(run_stages):
                    # No simple command (None covers '}' and an empty record; fi/done/esac resolve as
                    # the word itself) means this stage closes a group whose status feeds the pipe.
                    if (sbase is None or sbase in BLOCK_CLOSE) and not sopq and ctx_declared:
                        # A stage with no attributable simple command closes a subshell, brace group or
                        # loop: the group's status (which may carry a declared command's failure) feeds
                        # the pipe, and this reader cannot connect the two. Fail closed rather than
                        # credit the pipeline, whenever the context mentions a declared command.
                        raise GateError("{}:{}: binding {!r}: a grouped or compound pipeline stage (a "
                                        "subshell, brace group, or loop piped onward) hides which "
                                        "command's exit the pipe discards; cannot evaluate".format(
                                            rel, line_of(soff), binding["id"]))
                nstages = len(run_stages)
                last_idx, _o, _r, last_base, _d, _q = run_stages[-1]
                final_word = last_base or (kept[last_idx].argv[0] if kept[last_idx].argv else "?")
                top_level = ctx.is_top and run_stages[0][4] == 0
                read_line = None
                if top_level and seg.sep_after in ("", ";"):
                    # E2 needs the pipeline to end at a newline or one ';' (never '&', '&&' or '||'):
                    # only then can the immediately next line be the adjacent PIPESTATUS read.
                    read_line = _e2_read_line(original_text, ctx.base + offs[idx] + len(raw),
                                              seg.sep_after)
                for k, (sidx, soff, srelied, sbase, sdepth, _sq) in enumerate(_nonfinal(run_stages)):
                    if not srelied:
                        continue
                    if _e2_exempt(top_level, read_line, k):
                        continue
                    pending.append({"offset": soff, "rel": rel, "line": line_of(soff),
                                    "binding": binding["id"], "command": sbase,
                                    "stage": k, "nstages": nstages, "final": final_word,
                                    "depth": sdepth})
            run_stages = []
        # In `function NAME {` the NAME is a definition, not a command, and would stop the reserved-word
        # walk before the '{' is counted; skip the keyword and the name so the body depth is tracked.
        # Either definition form (function NAME, or NAME followed by an empty '( )' pair) marks the
        # next '{' as opening a FUNCTION body. A reserved word spelled quoted or escaped is a command
        # name in bash, so it neither opens nor closes a block here.
        words = argv
        if argv[:1] == ["function"] and _plain_word(raw, "function"):
            func_pending = True
            words = argv[2:]
        elif (len(argv) == 1 and seg.sep_after == "(" and not seg.redirects
                and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", argv[0] or "") is not None
                and _plain_word(raw, argv[0])):
            func_pending = True
        for w in words:
            if w in BLOCK_OPEN and _plain_word(raw, w):
                blocks.append("func" if (w == "{" and func_pending) else w)
                if w == "{":
                    func_pending = False
                if w == "case":
                    case_depth += 1
            elif w in BLOCK_CLOSE and _plain_word(raw, w):
                if blocks:
                    blocks.pop()
                if w == "esac":
                    case_depth = max(0, case_depth - 1)
            elif (w in RESERVED_SKIP and _plain_word(raw, w)) or ENV_ASSIGN_RE.match(w):
                continue
            else:
                break
        if case_depth == 0:
            # Inside a case statement a ')' ends a pattern, not a subshell, so the paren separators are
            # not depth boundaries there; case itself already holds the block depth above zero.
            if seg.sep_after == "(":
                paren_depth += 1
            elif seg.sep_after == ")":
                paren_depth = max(0, paren_depth - 1)
        if seg.argv or seg.sep_after:
            prev_sep = seg.sep_after


# --- configuration and surface loading (pattern copied from check_derived_command_parameters.py) -----

def _req_str_list(table, key, where):
    value = table.get(key)
    if not isinstance(value, list) or not value:
        raise GateError("{}: {!r} must be a non-empty array of strings".format(where, key))
    seen = set()
    for element in value:
        if not isinstance(element, str) or not element.strip():
            raise GateError("{}: {!r} contains a non-string or empty element".format(where, key))
        # A duplicate selector is a malformed control (grdinp): it double-counts a match and cannot be
        # reconciled soundly against the source, so fail closed rather than silently dedup and pass.
        if element in seen:
            raise GateError("{}: {!r} contains a duplicate element {!r}".format(where, key, element))
        seen.add(element)
    return value


def load_config(path):
    """Parse and fully validate the adopter configuration into a list of binding dicts. Fail-closed
    (GateError -> exit 2) on any malformed input: an unreadable or unparseable file, an unknown
    top-level key, a version that is not the exact integer 1, no bindings, a binding that is not a
    table, an unknown or missing binding field, an empty roster, a malformed id, or a duplicate id.
    An absent file is the caller's NOT APPLICABLE case, handled before this loader."""
    try:
        with open(path, "rb") as handle:
            data = tomllib.load(handle)
    except OSError as exc:
        raise GateError("cannot read the configuration {} ({})".format(CONFIG_REL, exc))
    # ValueError and RecursionError too: tomllib raises a BARE ValueError (not TOMLDecodeError) on an
    # integer literal past CPython's int-string limit, and a RecursionError on a deeply nested array or
    # inline table.
    except (tomllib.TOMLDecodeError, ValueError, RecursionError, MemoryError) as exc:
        raise GateError("configuration {} does not parse ({})".format(CONFIG_REL, exc))
    extra = set(data) - {"format-version", "binding"}
    if extra:
        raise GateError("configuration {}: unknown top-level key(s): {}".format(
            CONFIG_REL, ", ".join(sorted(extra))))
    version = data.get("format-version")
    if version is not True and version == 1 and isinstance(version, int):
        pass  # the exact integer 1
    else:
        raise GateError("configuration {}: format-version must be the exact integer 1".format(CONFIG_REL))
    bindings = data.get("binding")
    if not isinstance(bindings, list) or not bindings:
        raise GateError("configuration {}: at least one [[binding]] is required".format(CONFIG_REL))
    seen_ids = set()
    out = []
    for entry in bindings:
        if not isinstance(entry, dict):
            raise GateError("configuration {}: every [[binding]] must be a table".format(CONFIG_REL))
        missing = BINDING_REQUIRED - set(entry)
        if missing:
            raise GateError("configuration {}: a [[binding]] is missing key(s): {}".format(
                CONFIG_REL, ", ".join(sorted(missing))))
        unknown = set(entry) - BINDING_REQUIRED - BINDING_OPTIONAL
        if unknown:
            raise GateError("configuration {}: a [[binding]] has unknown key(s): {}".format(
                CONFIG_REL, ", ".join(sorted(unknown))))
        bid = entry.get("id")
        if not isinstance(bid, str) or not ID_RE.match(bid):
            raise GateError("configuration {}: binding id {!r} is not a kebab-case identifier".format(
                CONFIG_REL, bid))
        if bid in seen_ids:
            raise GateError("configuration {}: duplicate binding id {!r}".format(CONFIG_REL, bid))
        seen_ids.add(bid)
        where = "configuration {} binding {!r}".format(CONFIG_REL, bid)
        binding = {
            "id": bid,
            "paths": _req_str_list(entry, "paths", where),
            "commands": _req_str_list(entry, "commands", where),
        }
        if "subcommands" in entry:
            binding["subcommands"] = _req_str_list(entry, "subcommands", where)
        out.append(binding)
    return out


def _selector_lineage(root, selector, parts):
    """lstat every component of the selector path from root downward, fail-closed: a symlinked component
    (an ancestor directory or the final entry) would let a declared surface alias or escape the
    repository, and an uninspectable component is never read as clean. Returns the final component's
    stat record, or None when a component is confirmed absent (the caller reports the unmatched
    selector)."""
    st = None
    probe = root
    for part in parts:
        probe = probe / part
        try:
            st = os.lstat(probe)
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise GateError("configuration {}: cannot inspect path selector {!r} ({})".format(
                CONFIG_REL, selector, exc))
        if stat.S_ISLNK(st.st_mode):
            raise GateError("configuration {}: path selector {!r} crosses a symlink at {}".format(
                CONFIG_REL, selector, probe.relative_to(root).as_posix()))
    return st


def resolve_surface(root, selector):
    """Resolve one path selector to the list of readable regular files it names, fail-closed. A selector
    is an exact repo-relative file path, or a directory prefix ending in /** (EVERY regular file beneath
    it: nothing is pruned or skipped). Every resolved path must stay inside root (an absolute or
    escaping selector fails closed), no component of it may be a symlink (a symlinked directory or file
    met during the walk fails closed too, never a silent skip), a .git or __pycache__ entry met during
    the walk fails closed (tool metadata inside a declared surface is refused, never silently pruned),
    every match must be a regular file, and it must decode as UTF-8 when read by the caller; a selector
    matching zero files fails closed (an unmatched declared surface is never 'nothing to scan').
    Returns a sorted list."""
    root_resolved = root.resolve()
    if Path(selector).is_absolute():
        raise GateError("configuration {}: path selector {!r} is absolute; a repo-relative selector is "
                        "required".format(CONFIG_REL, selector))
    is_prefix = selector.endswith("/**")
    rel = selector[:-3] if is_prefix else selector
    parts = rel.split("/")
    if "" in parts or "." in parts or ".." in parts:
        raise GateError("configuration {}: path selector {!r} contains an empty, '.' or '..' component; "
                        "a plain repo-relative path is required".format(CONFIG_REL, selector))
    final_st = _selector_lineage(root, selector, parts)
    if is_prefix:
        base = root / rel
        if not base.resolve().is_relative_to(root_resolved):
            raise GateError("configuration {}: path selector {!r} escapes the repository".format(
                CONFIG_REL, selector))
        if final_st is None or not stat.S_ISDIR(final_st.st_mode):
            raise GateError("configuration {}: directory-prefix selector {!r} is not a directory".format(
                CONFIG_REL, selector))
        # Walk the subtree with os.walk (onerror raises, so an unlistable directory is exit 2, never a
        # silent clean) and refuse, in deterministic order, every entry the gate must not judge around:
        # a .git or __pycache__ name (tool metadata is never silently pruned from a DECLARED surface)
        # and a symlinked directory (never followed, never skipped). Files are vetted below.
        def _raise(exc):
            raise exc

        found = []
        try:
            for dirpath, dirnames, filenames in os.walk(base, onerror=_raise):
                dirnames.sort()
                for name in dirnames:
                    entry = Path(dirpath) / name
                    if name in METADATA_NAMES:
                        raise GateError("configuration {}: surface {!r} contains the metadata entry "
                                        "{}; a declared surface is scanned whole, never silently "
                                        "pruned".format(CONFIG_REL, selector,
                                                        entry.relative_to(root).as_posix()))
                    if entry.is_symlink():
                        raise GateError("configuration {}: surface {!r} contains a symlinked directory "
                                        "{}; a symlinked subtree is never followed and never silently "
                                        "skipped".format(CONFIG_REL, selector,
                                                         entry.relative_to(root).as_posix()))
                for name in filenames:
                    if name in METADATA_NAMES:
                        raise GateError("configuration {}: surface {!r} contains the metadata entry "
                                        "{}; a declared surface is scanned whole, never silently "
                                        "pruned".format(CONFIG_REL, selector,
                                                        (Path(dirpath) / name)
                                                        .relative_to(root).as_posix()))
                    found.append(Path(dirpath) / name)
        except OSError as exc:
            raise GateError("configuration {}: cannot list surface {!r} ({})".format(
                CONFIG_REL, selector, exc))
        found.sort()
        files = []
        for path in found:
            try:
                st = os.lstat(path)
            except OSError as exc:
                raise GateError("configuration {}: cannot inspect surface {!r} entry {} ({})".format(
                    CONFIG_REL, selector, path.relative_to(root).as_posix(), exc))
            if stat.S_ISLNK(st.st_mode):
                raise GateError("configuration {}: surface {!r} contains a symlink {}".format(
                    CONFIG_REL, selector, path.relative_to(root)))
            if not stat.S_ISREG(st.st_mode):
                raise GateError("configuration {}: surface {!r} contains {}, which is not a regular "
                                "file".format(CONFIG_REL, selector,
                                              path.relative_to(root).as_posix()))
            files.append(path)
        if not files:
            raise GateError("configuration {}: path selector {!r} matched no readable regular file".format(
                CONFIG_REL, selector))
        return files
    target = root / rel
    if not target.resolve().is_relative_to(root_resolved):
        raise GateError("configuration {}: path selector {!r} escapes the repository".format(
            CONFIG_REL, selector))
    if final_st is None:
        raise GateError("configuration {}: path selector {!r} matched no readable regular file".format(
            CONFIG_REL, selector))
    if not stat.S_ISREG(final_st.st_mode):
        raise GateError("configuration {}: path selector {!r} is not a regular file".format(
            CONFIG_REL, selector))
    return [target]


def scan_file(root, path, binding, telemetry):
    """Scan one declared surface file for a binding's relied-on commands in non-final pipeline stages.
    Read fail-closed: an OSError or a non-UTF-8 file is exit 2 (an unreadable declared surface is never
    a clean skip), and a non-regular file is refused BEFORE open (opening a FIFO would block; the
    resolver already refuses these, so this second layer only ever catches a resolver regression).
    Returns a list of finding strings."""
    try:
        st = os.lstat(path)
    except OSError as exc:
        raise GateError("configuration {} binding {!r}: cannot inspect declared surface {} ({})".format(
            CONFIG_REL, binding["id"], path.relative_to(root), exc))
    if not stat.S_ISREG(st.st_mode):
        raise GateError("configuration {} binding {!r}: declared surface {} is not a regular file; "
                        "refusing to open it".format(CONFIG_REL, binding["id"], path.relative_to(root)))
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise GateError("configuration {} binding {!r}: cannot read declared surface {} ({})".format(
            CONFIG_REL, binding["id"], path.relative_to(root), exc))
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise GateError("configuration {} binding {!r}: declared surface {} is not valid UTF-8".format(
            CONFIG_REL, binding["id"], path.relative_to(root)))
    rel = path.relative_to(root).as_posix()
    declared_re = _declared_regex(binding["commands"])
    events = []
    pending = []
    try:
        for ctx in _contexts(text):
            _scan_context(ctx, text, rel, binding, telemetry, events, pending, declared_re)
    except RecursionError:
        raise GateError("configuration {} binding {!r}: surface {} exceeds the scanner's nesting bounds; "
                        "cannot evaluate".format(CONFIG_REL, binding["id"], rel))
    if events and not _e1_file_credit(text):
        # The file holds a pipefail mention outside the standalone column-1 enable form, or a construct
        # that can move or disable the option behind this reader's back (a function definition, eval,
        # source, '.', sh -c and kin, builtin, a 'set +'/'shopt -u' spelling, a variable-driven set):
        # the whole file loses the pipefail credit. Disables stay recorded; they only ever refuse.
        events = [event for event in events if event[1] == "off"]
    findings = []
    for rec in _judge(pending, events):
        findings.append("{}:{}: binding {!r}: relied-on command {!r} (stage {} of {}) pipes into {!r}; "
                        "the pipe discards its exit status, so read it through pipefail or PIPESTATUS, "
                        "or drop the pipe".format(rec["rel"], rec["line"], rec["binding"], rec["command"],
                                                  rec["stage"] + 1, rec["nstages"], rec["final"]))
    return findings


def run(root):
    """Scan the declared surfaces under root. Returns the exit code 0/1/2. Only a CONFIRMED absent
    configuration is NOT APPLICABLE (exit 0): an inspection failure (an unreadable parent directory, a
    permission error) is never read as absence, because Path.exists() answers False for both and a
    present-but-unusable configuration must fail closed. A present configuration is loaded, its
    surfaces resolved and scanned."""
    config_path = root / CONFIG_REL
    try:
        try:
            cfg_st = os.lstat(config_path)
        except FileNotFoundError:
            print("NOT APPLICABLE: no {} configuration; the relied-exit-pipes gate is adopter-declared "
                  "and inert without one".format(CONFIG_REL))
            return 0
        except OSError as exc:
            raise GateError("cannot inspect the configuration {} ({}); only a confirmed absence is "
                            "NOT APPLICABLE".format(CONFIG_REL, exc))
        # A symlinked configuration is fail-closed, never followed (same stance as the declared
        # surfaces); only a genuinely absent path is the NOT APPLICABLE case.
        if stat.S_ISLNK(cfg_st.st_mode):
            raise GateError("configuration {} is a symlink; a symlinked configuration is not "
                            "followed".format(CONFIG_REL))
        # A FIFO (or any other non-regular file) is refused BEFORE open: opening a FIFO with no writer
        # blocks forever, and a device or socket is never a configuration. Fail closed, never hang.
        if not stat.S_ISREG(cfg_st.st_mode):
            raise GateError("configuration {} is not a regular file; refusing to open it".format(
                CONFIG_REL))
        bindings = load_config(config_path)
        findings = []
        scanned = 0
        for binding in bindings:
            files = []
            seen_files = set()
            for selector in binding["paths"]:
                for path in resolve_surface(root, selector):
                    if path in seen_files:
                        raise GateError("configuration {} binding {!r}: path selector {!r} overlaps "
                                        "another selector at {}".format(CONFIG_REL, binding["id"],
                                                                        selector, path.relative_to(root)))
                    seen_files.add(path)
                    files.append(path)
            telemetry = {"commands": set(), "subcommands": set()}
            for path in sorted(files):
                scanned += 1
                findings.extend(scan_file(root, path, binding, telemetry))
            # Reconcile the config's selectors against the real scanned source (grdinp): a command or
            # subcommand selector that matches NOTHING (a typo, or a stale surface) cannot be evaluated,
            # so it is a cannot-evaluate that fails closed (exit 2), never a silent clean pass.
            unmatched_commands = [c for c in binding["commands"] if c not in telemetry["commands"]]
            if unmatched_commands:
                raise GateError("configuration {} binding {!r}: command selector(s) {} matched no "
                                "command word in the scanned surface (cannot-evaluate)".format(
                                    CONFIG_REL, binding["id"], ", ".join(map(repr, unmatched_commands))))
            unmatched_subs = [s for s in binding.get("subcommands", ())
                              if s not in telemetry["subcommands"]]
            if unmatched_subs:
                raise GateError("configuration {} binding {!r}: subcommand selector(s) {} matched no "
                                "invocation in the scanned surface (cannot-evaluate)".format(
                                    CONFIG_REL, binding["id"], ", ".join(map(repr, unmatched_subs))))
    except GateError as exc:
        print("error: {}; fail-closed".format(exc), file=sys.stderr)
        return 2
    print("scanned {} declared surface file(s) across {} binding(s)".format(scanned, len(bindings)))
    if findings:
        print("FAIL: {} relied-exit-pipes finding(s)".format(len(findings)))
        for finding in sorted(set(findings)):
            print("  " + finding)
        return 1
    print("PASS: no declared relied-on command loses its exit status to a pipe")
    return 0


# --- self-test ---------------------------------------------------------------------------------------
# Every refuse case R1..R53 runs against the real gate (exit 1) AND against a mutant that never reports
# (exit 0), so each case is shown to discriminate; targeted mutants additionally pin the mid-pipeline
# stage index, the closed-form PIPESTATUS reader and the whole-file pipefail credit. Every control
# C1..C27 runs against the real gate (exit 0) and names the over-broad implementation it catches; where
# that implementation is a patchable predicate, the over-broad mutant is also run and must reach the
# named wrong exit. Every cannot-evaluate case X1..X14 must exit 2 AND print the diagnostic of its OWN
# check (asserted by a substring no other diagnostic contains), so a removed guard cannot hide behind
# another refusal reaching the same exit code (such as unmatched-selector reconciliation). A case that
# cannot run (no tempdir, no symlink or FIFO support, permission checks bypassed) is a NAMED failure,
# never a skip.

def _run_quiet(root):
    import io
    from contextlib import redirect_stderr, redirect_stdout
    out = io.StringIO()
    err = io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = run(root)
    return rc, out.getvalue() + err.getvalue()


def _not_run(failures, case, cause):
    failures.append("NOT RUN: {}: {}; coverage that did not run FAILS the self-test, never a silent "
                    "PASS".format(case, cause))


CFG = ('format-version = 1\n\n[[binding]]\nid = "row-writer"\npaths = ["cmd/job.sh"]\n'
       'commands = ["tool"]\nsubcommands = ["write"]\n')
CFG_NOSUB = ('format-version = 1\n\n[[binding]]\nid = "row-writer"\npaths = ["cmd/job.sh"]\n'
             'commands = ["tool"]\n')
CFG_PREFIX = ('format-version = 1\n\n[[binding]]\nid = "row-writer"\npaths = ["cmd/**"]\n'
              'commands = ["tool"]\nsubcommands = ["write"]\n')


def self_test_main(_mkdtemp=None, _symlink=None):  # noqa: C901  a flat sequence of cases
    failures = []
    this = sys.modules[__name__]

    # --- pure-logic boundary checks (always run) ---
    for argv, want in ((["set", "-o", "pipefail"], "on"),
                       (["set", "-euo", "pipefail"], "on"),
                       (["set", "-Eeo", "pipefail"], "on"),
                       (["set", "+o", "pipefail"], "off"),
                       (["set", "-e"], None),
                       (["set", "-o"], None),
                       (["set", "-o", "errexit", "-o", "pipefail"], "on"),
                       # Option termination: after -- or the first operand everything is a positional
                       # parameter, so neither a false enable nor a false disable may register; options
                       # BEFORE the terminator still count.
                       (["set", "--", "-o", "pipefail"], None),
                       (["set", "--", "+o", "pipefail"], None),
                       (["set", "foo", "-o", "pipefail"], None),
                       (["set", "-eo", "pipefail", "--", "+o", "pipefail"], "on")):
        if _set_pipefail_effect(argv) != want:
            failures.append("_set_pipefail_effect({!r}) expected {!r}".format(argv, want))
    for argv, want_base, want_opaque in ((["tool", "write"], "tool", False),
                                         (["/usr/local/bin/tool", "write"], "tool", False),
                                         (["env", "A=1", "tool", "write"], "tool", False),
                                         (["nice", "tool", "write"], "tool", False),
                                         (["timeout", "30", "tool", "write"], "tool", False),
                                         (["if", "tool", "write"], "tool", False),
                                         (["A=1"], None, False),
                                         # ANY option word after a wrapper (separate, '='-attached, or
                                         # '--') makes the command word opaque: whether the following
                                         # words are values or the command cannot be read from text.
                                         (["nice", "-n", "10", "tool", "write"], None, True),
                                         (["sudo", "-u", "deploy", "tool", "write"], None, True),
                                         (["env", "--chdir=/x", "tool", "write"], None, True),
                                         (["env", "--", "tool", "write"], None, True),
                                         (["command", "-v", "tool"], None, True),
                                         (["time", "-p", "tool", "write"], None, True)):
        _i, base, _o, opq = _command_word(argv)
        if base != want_base or opq != want_opaque:
            failures.append("_command_word({!r}) expected base {!r}/opaque {!r}, got {!r}/{!r}".format(
                argv, want_base, want_opaque, base, opq))
    # A reserved word spelled quoted or escaped is a command name, never syntax: with the raw text
    # supplied, the quoted "select" is returned as the command word instead of being skipped.
    _i, base, _o, _q = _command_word(["select", "tool", "write"], '"select" tool write')
    if base != "select":
        failures.append("_command_word on a quoted 'select' expected the command word 'select', "
                        "got {!r}".format(base))
    for raw, word, want in (("tool write", "tool", True),
                            ('"select" tool write', "select", False),
                            ("to\\ol write", "tool", False),
                            ("'to''ol' write", "tool", False),
                            ("{ x; }", "{", True),
                            ("while :", "while", True)):
        if _plain_word(raw, word) != want:
            failures.append("_plain_word({!r}, {!r}) expected {!r}".format(raw, word, want))
    for argv, want_sub in (((["tool", "write", "--row", "9"]), "write"),
                           ((["tool", "--", "write"]), "write"),
                           ((["tool", "--config=x.toml", "write"]), "write"),
                           ((["tool", "--config", "x.toml", "write"]), _AMBIGUOUS),
                           ((["tool"]), None)):
        got = _first_operand(argv, 0)
        if got is not want_sub and got != want_sub:
            failures.append("_first_operand({!r}) expected {!r}, got {!r}".format(argv, want_sub, got))
    # E2 admits only the closed set of read forms, matched on the lexically intact line: a scalar save
    # of exactly the flagged index (bare or double-quoted, quotes paired), a quoted whole-array save,
    # or its declare -a spelling. Everything else (another index, a bare array save, extra words, a
    # literal or commented mention, a quote-mangled spelling) earns nothing.
    for line, k, want in (("rc=${PIPESTATUS[0]}", 0, True),
                          ('rc="${PIPESTATUS[0]}"', 0, True),
                          ('st=("${PIPESTATUS[@]}")', 0, True),
                          ('declare -a st=("${PIPESTATUS[@]}")', 5, True),
                          ("rc=${PIPESTATUS[1]}", 0, False),
                          ("rc=${PIPESTATUS[0]}", 1, False),
                          ("all=(${PIPESTATUS[@]})", 0, False),
                          ('rc=("${PIPESTATUS[0]}")', 0, False),
                          ('rc="${PIPESTATUS[0]}', 0, False),
                          ("rc=${PIPESTATUS[0]} || exit 1", 0, False),
                          ("echo '${PIPESTATUS[0]}'", 0, False),
                          ("echo ok # ${PIPESTATUS[0]}", 0, False),
                          ("echo \\${PIPESTATUS[0]}", 0, False),
                          ("printf '%s' 'PIPESTATUS[0]'", 0, False)):
        if _e2_exempt(True, line, k) != want:
            failures.append("_e2_exempt(True, {!r}, {}) expected {!r}".format(line, k, want))
    if _e2_exempt(False, "rc=${PIPESTATUS[0]}", 0):
        failures.append("_e2_exempt must never exempt a non-top-level pipeline")
    # E1's closed form: the line is exactly a standalone enabling set command ending at the newline or
    # one ';'. Each boundary is tested just inside, at, and just outside it.
    for line, want in (("set -o pipefail", True),
                       ("set -euo pipefail", True),
                       ("set -Eeo pipefail", True),
                       ("set -e -o pipefail", True),
                       ("set -o errexit -o pipefail", True),
                       ("set -o pipefail;", True),
                       ("set -o pipefail ;", True),
                       ("set -e", False),
                       ("set +o pipefail", False),
                       ("set -- -o pipefail", False),
                       ("set -o pipefail extra", False),
                       ("set -o pipefail; true", False),
                       ("set -o pipefail && true", False),
                       ("set -o pipefail | cat", False),
                       ("set -o pipefail &", False),
                       ("set -o pipefail # on", False),
                       ('set -o "$opt"', False),
                       ("env set -o pipefail", False),
                       ("set", False)):
        if _e1_enable_line(line) != want:
            failures.append("_e1_enable_line({!r}) expected {!r}".format(line, want))
    # The whole-file pipefail credit: withdrawn by any pipefail mention outside the closed form and by
    # any construct that can move or disable the option behind this reader's back.
    for text, want in (("set -o pipefail\ntool write | tail -1\n", True),
                       ("set -euo pipefail\n", True),
                       ("echo ok\n", True),
                       ("  set -o pipefail\n", False),
                       ("set -o pipefail && true\n", False),
                       ("set -o pipefail\nset +o pipefail\n", False),
                       ("set -o pipefail\nset -- +o pipefail\n", False),
                       ("set -o pipefail\n# pipefail notes\n", False),
                       ("set -o pipefail\nf() { :; }\n", False),
                       ("set -o pipefail\nfunction f { :; }\n", False),
                       ("set -o pipefail\neval ':'\n", False),
                       ("set -o pipefail\nsource lib.sh\n", False),
                       ("set -o pipefail\n. lib.sh\n", False),
                       ("set -o pipefail\nbash -c ':'\n", False),
                       ("set -o pipefail\ndash -ec ':'\n", False),
                       ("set -o pipefail\nbuiltin :\n", False),
                       ("set -o pipefail\nset +x\n", False),
                       ("set -o pipefail\nshopt -u extglob\n", False),
                       ("set -o pipefail\nshopt -s nullglob\n", True),
                       ("set -o pipefail\nset -o $opt\n", False)):
        if _e1_file_credit(text) != want:
            failures.append("_e1_file_credit({!r}) expected {!r}".format(text, want))
    _SegStub = collections.namedtuple("_SegStub", ("argv", "redirects", "raw"))
    for stub, want in ((_SegStub([], [], ""), False),
                       (_SegStub([], [], "  \n"), False),
                       (_SegStub([], [], "# only a comment"), False),
                       (_SegStub(["x"], [], "x"), True),
                       (_SegStub([], ["redir"], "> f"), True)):
        if _executes(stub) != want:
            failures.append("_executes({!r}) expected {!r}".format(stub, want))
    masked, subs, trunc = _extract("row=$(tool write | tail -1)\n")
    if len(masked) != len("row=$(tool write | tail -1)\n") or trunc is not None:
        failures.append("_extract did not preserve length on a $( ) substitution")
    if subs != [("tool write | tail -1", 6)]:
        failures.append("_extract did not pull the $( ) body at its offset, got {!r}".format(subs))
    _m2, subs2, _t2 = _extract("row=`tool write` done\n")
    if subs2 != [("tool write", 5)]:
        failures.append("_extract did not pull the backquote body, got {!r}".format(subs2))
    # E1 boundary: an enable strictly before the pipeline exempts; at or after it does not.
    if not _e1_exempt([(5, "on")], 6) or _e1_exempt([(6, "on")], 6) or _e1_exempt([(7, "on")], 6):
        failures.append("_e1_exempt boundary: only a strictly earlier enable may exempt")
    if _e1_exempt([(1, "on"), (3, "off")], 6):
        failures.append("_e1_exempt: a later disable between enable and pipeline must cancel")

    # --- end-to-end layer over real synthetic trees -------------------------------------------------
    import shutil
    import tempfile

    symlink = _symlink or Path.symlink_to
    try:
        base_tmp = Path((_mkdtemp or tempfile.mkdtemp)(prefix="aiqt-relied-pipes-selftest-"))
    except OSError as exc:
        base_tmp = None
        _not_run(failures, "every end-to-end case over a real synthetic tree",
                 "no writable temp directory ({})".format(exc))

    if base_tmp is not None:
        def _fresh(tag, config, surface_files):
            r = base_tmp / tag
            (r / ".aiqt").mkdir(parents=True, exist_ok=True)
            if config is not None:
                (r / ".aiqt" / "relied-exit-pipes.toml").write_text(config, encoding="utf-8")
            for rel, body in surface_files.items():
                p = r / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                if isinstance(body, bytes):
                    p.write_bytes(body)
                else:
                    p.write_text(body, encoding="utf-8")
            return r

        def _case(name, config, body, expected, note, needle=None, files=None):
            surface = dict(files) if files else {}
            if body is not None:
                surface["cmd/job.sh"] = body
            r = _fresh(name, config, surface)
            rc, text = _run_quiet(r)
            if rc != expected:
                failures.append("{}: expected exit {}, got {} ({})".format(name, expected, rc, note))
            elif needle is not None and needle not in text:
                # The exit code alone can come from ANOTHER refusal (unmatched-selector reconciliation
                # also exits 2), so a case for a specific guard must see that guard's own diagnostic.
                failures.append("{}: exit {} came without the expected diagnostic {!r}; another check "
                                "may have produced the exit code ({})".format(name, expected, needle,
                                                                              note))
            return r

        try:
            # --- refuse cases R1..R53 (exit 1), each later re-run against the never-report mutant ---
            refuse = [
                ("R1", CFG, "set -e\ntool write --row 7 | tail -1\ntool write --row 8\n",
                 "the seed: a row writer piped into tail under set -e"),
                ("R2", CFG, "set -e\nrow=$(tool write --row 7 | tail -1)\n",
                 "a $( ) substitution body pipeline"),
                ("R3", CFG, "set -e\nrow=`tool write --row 7 | tail -1`\n",
                 "a backquote substitution body pipeline"),
                ("R4", CFG, "tool write | grep -q ok && next-step\n",
                 "a grep -q sink inside an AND list"),
                ("R5", CFG, "if tool write | grep -q ok; then\n  echo ok\nfi\n",
                 "a pipeline in an if condition"),
                ("R6", CFG, "tool write |& tee /abs/log\n",
                 "a |& into tee: any final stage counts, not only a truncating sink"),
                ("R7", CFG, "/usr/local/bin/tool write | head -1\nenv A=1 tool write | tail -1\n"
                            "nice tool write | tail -1\n",
                 "an absolute path and the env/nice wrappers still resolve the command"),
                ("R8", CFG, "set -o pipefail\nset +o pipefail\ntool write | tail -1\n",
                 "a set +o pipefail between the enable and the pipeline cancels E1"),
                ("R9a", CFG, "f() {\n  set -o pipefail\n}\ntool write | tail -1\n",
                 "pipefail set only inside a function body is not a top-level enable"),
                ("R9b", CFG, "if true; then\n  set -o pipefail\nfi\ntool write | tail -1\n",
                 "pipefail set only inside an if block is not a top-level enable"),
                ("R10a", CFG, "tool write | tail -1\nrc=${PIPESTATUS[1]}\n",
                 "a PIPESTATUS[1] read does not cover the declared stage 0"),
                ("R10b", CFG, "tool write | tail -1\necho between\nrc=${PIPESTATUS[0]}\n",
                 "a PIPESTATUS[0] read after an intervening command is too late"),
                ("R11", CFG, "tool write \\\n  | tail -1\n",
                 "a line continuation still joins into one pipeline"),
                ("R12", CFG, "tool write | sort | tail -1\n",
                 "a three-stage pipeline: any non-final stage counts"),
                ("Rb1", CFG, "tool write | tail -1\nset -o pipefail\n",
                 "boundary: an enable AFTER the pipeline does not exempt it"),
                ("R13", CFG, "printf x | tool write | tail -1\n",
                 "a relied-on MIDDLE stage (index 1 of 3) is flagged, not only stage 0"),
                ("R14", CFG, "set -o pipefail | cat\ntool write | tail -1\n",
                 "a set that is itself a pipeline stage runs in another shell; no E1 credit"),
                ("R15", CFG, "set -o pipefail &\nwait\ntool write | tail -1\n",
                 "a backgrounded set runs in another shell; no E1 credit"),
                ("R16", CFG, "false && set -o pipefail\ntool write | tail -1\n",
                 "a set on the right arm of && may never run; no E1 credit"),
                ("R17", CFG, "set -- -o pipefail\ntool write | tail -1\n",
                 "set -- makes -o pipefail a positional parameter, never an enable"),
                ("R18", CFG, "set -o pipefail\nshopt -uo pipefail\ntool write | tail -1\n",
                 "shopt -uo pipefail disables the option and cancels E1"),
                ("R19", CFG, "function f {\n  set -o pipefail\n}\ntool write | tail -1\n",
                 "pipefail set inside a function NAME { body is not a top-level enable"),
                ("R20", CFG, "(\n  case x in\n    a) : ;;\n  esac\n  set -o pipefail\n)\n"
                             "tool write | tail -1\n",
                 "a case pattern's ) does not close the enclosing subshell; the set stays nested"),
                ("R21", CFG, "select x in a b; do set -o pipefail; done\ntool write | tail -1\n",
                 "pipefail set inside a select body is not a top-level enable"),
                ("R22", CFG, "tool write | tail -1\nprintf '%s\\n' 'PIPESTATUS[0]'\n",
                 "a single-quoted literal PIPESTATUS[0] is not a read of the status"),
                ("R23", CFG, "tool write | tail -1\necho done # PIPESTATUS[0]\n",
                 "PIPESTATUS[0] inside a trailing comment is not a read of the status"),
                ("R25", CFG, "row=$(tool write | tail -1\nrc=${PIPESTATUS[0]})\n",
                 "E2 never applies inside a substitution body: it is not a top-level statement"),
                ("R26", CFG, "set -o pipefail && true &\nwait\ntool write | tail -1\n",
                 "a backgrounded AND list runs its enable in another shell; the line is not the "
                 "standalone closed form"),
                ("R27", CFG, "coproc set -o pipefail\nwait\ntool write | tail -1\n",
                 "a coproc runs its set in a coprocess shell; no E1 credit"),
                ("R28", CFG, "env set -o pipefail\ntool write | tail -1\n",
                 "an external set run through env never reaches this shell; no E1 credit"),
                ("R29", CFG, "exec set -o pipefail\ntool write | tail -1\n",
                 "exec set replaces the shell; the line is not the standalone closed form"),
                ("R30", CFG, "set -o pipefail\nbuiltin set +o pipefail\ntool write | tail -1\n",
                 "a disable reached through builtin withdraws the whole-file credit"),
                ("R31", CFG, "set -o pipefail\neval 'set +o pipefail'\ntool write | tail -1\n",
                 "an eval anywhere withdraws the whole-file credit"),
                ("R32", CFG, 'set -o pipefail\nset +o "$opt"\ntool write | tail -1\n',
                 "a variable-driven set option withdraws the whole-file credit"),
                ("R33", CFG, "set -o pipefail\nfunction d { set +o pipefail; }\nd\n"
                             "tool write | tail -1\n",
                 "a function definition anywhere withdraws the whole-file credit (the body can "
                 "disable at any call site)"),
                ("R34", CFG, "d() { set +o pipefail; }\nset -o pipefail\nd\ntool write | tail -1\n",
                 "a disabling function defined before the enable still withdraws the credit"),
                ("R35", CFG, "set -o pipefail\nsource ./lib.sh\ntool write | tail -1\n",
                 "a source anywhere withdraws the whole-file credit"),
                ("R36", CFG, 'tool write | tail -1\nprintf "%s\\n" "$"{PIPESTATUS[0]}\n',
                 "a quote-mangled spelling is not a PIPESTATUS expansion; quotes are never removed "
                 "before matching"),
                ("R37", CFG, "tool write | tail -1 &\nrc=${PIPESTATUS[0]}\n",
                 "a backgrounded pipeline's statuses are not in PIPESTATUS; '&' earns no E2 credit"),
                ("R38", CFG, "tool write | tail -1 & rc=${PIPESTATUS[0]}\n",
                 "a same-line read after '&' still reads an earlier pipeline; no E2 credit"),
                ("R39", CFG, "tool write | grep -q ok || rc=${PIPESTATUS[0]}\n",
                 "a read on the right arm of || may never run; no E2 credit"),
                ("R40", CFG, "tool write | tail -1\n\n# save the status\nrc=${PIPESTATUS[0]}\n",
                 "the read must be the immediately next line; a blank or comment line before it is "
                 "refused toward a finding"),
                ("R41", CFG, "set -o pipefail\nset -- +o pipefail\ntool write | tail -1\n",
                 "a pipefail mention outside the closed enable form (here positional parameters) "
                 "withdraws the whole-file credit"),
                ("R42", CFG, "set -o pipefail && tool write | tail -1\n",
                 "an enable sharing its line with an AND list is not the standalone closed form"),
                ("R43", CFG, "true &&\nset -o pipefail\ntool write | tail -1\n",
                 "a standalone-looking line that is the right arm of a continued && earns no credit"),
                ("R53", CFG, "true ||\nset -o pipefail\ntool write | tail -1\n",
                 "a standalone-looking line that is the right arm of a continued || earns no credit"),
                ("R44", CFG, "true |\nset -o pipefail\ntool write | tail -1\n",
                 "a standalone-looking line that is a continued pipeline's stage earns no credit"),
                ("R45", CFG, "true | set -o pipefail\ntool write | tail -1\n",
                 "a set that is the final pipeline stage runs in another shell; no E1 credit"),
                ("R46", CFG, "if true; then\n  tool write | tail -1\n  rc=${PIPESTATUS[0]}\nfi\n"
                             "tool write --row 9\n",
                 "E2 applies only to a top-level statement, never inside a block"),
                ("R48", CFG, "tool write | tail -1\nst=(${PIPESTATUS[@]})\n",
                 "a bare unquoted array save is outside the closed read set"),
                ("R49", CFG, 'tool write | tail -1\nrc=("${PIPESTATUS[0]}")\n',
                 "a scalar read wrapped in array parens is outside the closed read set"),
                ("R50", CFG, "tool write | tail -1; echo x\nrc=${PIPESTATUS[0]}\n",
                 "another command after the pipeline's ';' overwrites PIPESTATUS before the read"),
                ("R51", CFG, "'to''ol' write | tail -1\ntool write --row 9\n",
                 "a quoted spelling of a declared command still resolves by its dequoted identity"),
                ("R52", CFG, "tool write | tail -1\nrc=${PIPESTATUS[0]} || exit 1\n",
                 "an extra word on the read line is outside the closed read set"),
            ]
            roots = {}
            for name, config, body, note in refuse:
                roots[name] = _case(name, config, body, 1, note)
            # R47: a dir/** selector reaches a NESTED unsafe file; the finding must name it, so a
            # walker that scans only the top directory cannot pass.
            refuse.append(("R47", CFG_PREFIX, None,
                           "a nested file under dir/** is scanned and its finding names it"))
            roots["R47"] = _case("R47", CFG_PREFIX, None, 1,
                                 "a nested file under dir/** is scanned and its finding names it",
                                 needle="cmd/b/c.sh",
                                 files={"cmd/a.sh": "tool write --row 9\n",
                                        "cmd/b/c.sh": "tool write | tail -1\n"})
            # R13 must flag the MIDDLE stage by index, or a stage-0-only reader would pass it.
            rc, text = _run_quiet(roots["R13"])
            if rc != 1 or "(stage 2 of 3)" not in text:
                failures.append("R13: expected the finding to name stage 2 of 3, got exit {} with "
                                "{!r}".format(rc, text.strip()[:200]))

            # Each refuse case must FAIL (exit 0) against the mutant that removes the reporting it
            # covers: a gate that never reports anything.
            real_judge = this._judge
            this._judge = lambda pending, events: []
            try:
                for name, _config, _body, _note in refuse:
                    rc, _text = _run_quiet(roots[name])
                    if rc != 0:
                        failures.append("{} under the never-report mutant: expected exit 0, got {} "
                                        "(the case does not discriminate the mutant)".format(name, rc))
            finally:
                this._judge = real_judge

            # Targeted mutants: each weakened predicate must flip its fixture to exit 0, proving the
            # fixture exercises THAT predicate and not a neighbouring check.
            targeted = [
                ("R13", "_nonfinal", lambda stages: stages[:1],
                 "a reader that judges only stage 0 must miss the mid-pipeline stage"),
                ("R22", "_e2_exempt",
                 lambda top, line, k: bool(line) and "PIPESTATUS" in line,
                 "a raw-text PIPESTATUS match must wrongly exempt the quoted literal"),
                ("R41", "_e1_file_credit", lambda text: True,
                 "a credit blind to non-enabling pipefail mentions must wrongly exempt"),
                ("R33", "_e1_file_credit", lambda text: True,
                 "a credit blind to function definitions must wrongly exempt"),
            ]
            for name, attr, weak, why in targeted:
                real = getattr(this, attr)
                setattr(this, attr, weak)
                try:
                    rc, _text = _run_quiet(roots[name])
                finally:
                    setattr(this, attr, real)
                if rc != 0:
                    failures.append("{} under the weakened '{}' mutant: expected exit 0, got {} "
                                    "({})".format(name, attr, rc, why))

            # --- controls C1..C19 (exit 0), each naming the over-broad implementation it catches ---
            # C1: no configuration is NOT APPLICABLE. Catches a gate that requires a config.
            r = base_tmp / "C1"
            (r / "cmd").mkdir(parents=True, exist_ok=True)
            rc, text = _run_quiet(r)
            if rc != 0 or "NOT APPLICABLE" not in text:
                failures.append("C1: an absent configuration expected NOT APPLICABLE exit 0 "
                                "(catches a gate that requires a config)")

            # C2: an undeclared producer piped into head. Catches 'flag every pipe'.
            c2 = _case("C2", CFG_NOSUB, "git log --oneline | head -1\ntool write --row 9\n", 0,
                       "catches 'flag every pipe'")
            # C3: a provably-on pipefail exempts, in all three set spellings. Catches 'ignore pipefail'.
            c3a = _case("C3a", CFG, "set -euo pipefail\ntool write | tail -1\n", 0,
                        "catches 'ignore pipefail' (cluster form)")
            _case("C3b", CFG, "set -o pipefail\ntool write | tail -1\n", 0,
                  "catches 'ignore pipefail' (spelled-out form)")
            _case("C3c", CFG, "set -Eeo pipefail\ntool write | tail -1\n", 0,
                  "catches 'ignore pipefail' (-Eeo cluster form)")
            # C4: the very next command reads PIPESTATUS[0]. Catches 'ignore PIPESTATUS'.
            c4 = _case("C4", CFG, "tool write | tail -1\nrc=${PIPESTATUS[0]}\n", 0,
                       "catches 'ignore PIPESTATUS'")
            # C5: the declared command in the FINAL stage. Catches 'flag any stage'.
            c5 = _case("C5", CFG, "printf '%s\\n' hi | tool write\n", 0,
                       "catches 'flag any stage'")
            # C6: the declared text inside a quoted echo argument and inside a comment. Catches a
            # raw-regex scan.
            _case("C6", CFG, "echo \"tool write | tail -1\"\n# tool write | tail -1\n"
                             "tool write --row 9\n", 0, "catches a raw-regex scan")
            # C7: an OR list is not a pipe. Catches 'read || as a pipe'.
            _case("C7", CFG, "tool write || exit 1\n", 0, "catches 'read || as a pipe'")
            # C8: pipes inside a single-quoted program string. Catches a raw-regex scan.
            _case("C8", CFG, "CI_STATUS_JQ='map(.x) | length | tostring'\ntool write --row 9\n", 0,
                  "catches a raw-regex scan over quoted program text")
            # C9: an undeclared subcommand is not relied on. Catches 'ignore subcommands'.
            c9 = _case("C9", CFG, "tool list | head -1\ntool write --row 9\n", 0,
                       "catches 'ignore subcommands'")
            # C10: a quoted heredoc body is literal data. Catches heredoc-blind lexing.
            _case("C10", CFG, "cat <<'EOF'\ntool write | tail -1\nEOF\ntool write --row 9\n", 0,
                  "catches heredoc-blind lexing")
            # C11: a redirect is not a pipe. Catches 'treat a redirect as a pipe'.
            _case("C11", CFG, "tool write > /abs/out.log\ntail -1 /abs/out.log\n", 0,
                  "catches 'treat a redirect as a pipe'")
            # C13: a pipe at end of line continues the pipeline on the next line; the stage list and the
            # following PIPESTATUS read both survive the line break.
            c13 = _case("C13", CFG, "tool write |\n  tail -1\nrc=${PIPESTATUS[0]}\n", 0,
                        "catches 'end the pipeline at a newline after the pipe'")
            # C16: a shopt that touches neither -o nor pipefail is not a disable and keeps the credit.
            _case("C16", CFG, "set -o pipefail\nshopt -s nullglob\ntool write | tail -1\n", 0,
                  "catches 'read any shopt as a pipefail disable'")
            # C18: a clean dir/** selector resolves and scans every regular file beneath it (R47 proves
            # the nested file is really scanned: its finding must name it).
            _case("C18", CFG_PREFIX, None, 0, "catches a walker that cannot resolve dir/**",
                  files={"cmd/a.sh": "tool write --row 9\n", "cmd/b/c.sh": "tool write --row 8\n"})
            # C20/C21: the closed whole-array saves, end to end through the lexer (the NAME=( record is
            # joined with its closing paren by reading the intact source line, never dequoted text).
            c20 = _case("C20", CFG, 'tool write | tail -1\nst=("${PIPESTATUS[@]}")\n', 0,
                        "catches 'refuse the canonical whole-array save'")
            _case("C21", CFG, 'tool write | tail -1\ndeclare -a st=("${PIPESTATUS[@]}")\n', 0,
                  "catches 'refuse the declare -a whole-array save'")
            # C22: a pipeline may end at one ';' with the read on the next line.
            _case("C22", CFG, "tool write | tail -1;\nrc=${PIPESTATUS[0]}\n", 0,
                  "catches 'refuse a semicolon-terminated pipeline'")
            # C23: a QUOTED "select" is a command name, not the reserved word: the declared name after
            # it is that command's argument, never this pipeline's producer.
            c23 = _case("C23", CFG, '"select" tool write | tail -1\ntool write --row 9\n', 0,
                        "catches 'read a quoted word as a reserved word'")
            # C24: a wrapper used BARE (no option words) stays transparent.
            c24 = _case("C24", CFG, "env tool write --row 9\n", 0,
                        "catches 'fail closed on every wrapper'")
            # C25: the double-quoted scalar read is inside the closed set.
            _case("C25", CFG, 'tool write | tail -1\nrc="${PIPESTATUS[0]}"\n', 0,
                  "catches 'refuse the quoted scalar read'")
            # C26/C27: a top-level enable AFTER a properly closed case or select body still credits
            # (the case-pattern ')' and the loop's done must restore the depth, not leak it).
            _case("C26", CFG, "case x in\n  a) : ;;\nesac\nset -o pipefail\ntool write | tail -1\n",
                  0, "catches 'leak case depth past esac'")
            _case("C27", CFG, "select v in a b; do : ; done\nset -o pipefail\n"
                              "tool write | tail -1\n",
                  0, "catches 'leak select depth past done'")

            # Over-broad mutants: each patched predicate makes its control fixture reach the named
            # WRONG exit (a finding, or a refusal), so the control is shown to discriminate the
            # over-broad implementation it names.
            overbroad = [
                ("C2", c2, "_declared_command", lambda base, commands: bool(base),
                 "flag every pipe", 1),
                ("C3a", c3a, "_e1_exempt", lambda events, offset: False, "ignore pipefail", 1),
                ("C4", c4, "_e2_exempt", lambda top, line, k: False, "ignore PIPESTATUS", 1),
                ("C5", c5, "_nonfinal", lambda stages: stages, "flag any stage", 1),
                ("C9", c9, "_subcommand_ok", lambda subs, sub: True, "ignore subcommands", 1),
                ("C13", c13, "_executes", lambda seg: True,
                 "treat a blank continuation record as a command", 1),
                ("C20", c20, "_e2_exempt", lambda top, line, k: False,
                 "ignore the whole-array save", 1),
                ("C23", c23, "_plain_word", lambda raw, word: True,
                 "read a quoted word as a reserved word", 2),
                ("C24", c24, "_command_word", lambda argv, raw=None: (None, None, 0, True),
                 "treat every wrapper as opaque", 2),
            ]
            for name, root_dir, attr, broad, label, wrong_rc in overbroad:
                real = getattr(this, attr)
                setattr(this, attr, broad)
                try:
                    rc, _text = _run_quiet(root_dir)
                finally:
                    setattr(this, attr, real)
                if rc != wrong_rc:
                    failures.append("{} under the over-broad '{}' mutant: expected exit {}, got {} "
                                    "(the control does not discriminate it)".format(name, label,
                                                                                    wrong_rc, rc))

            # --- cannot-evaluate cases X1..X10 (exit 2, each asserting its OWN diagnostic) ---
            _case("X1a", CFG_NOSUB, "echo hello\n", 2,
                  "a declared command matching nothing is cannot-evaluate",
                  needle="command selector")
            _case("X1b", CFG, "tool list\n", 2,
                  "a declared subcommand matching nothing is cannot-evaluate",
                  needle="subcommand selector")
            _case("X2a", CFG, "tool write <<EOF\nbody\nEOF\n", 2,
                  "an unquoted heredoc beside a declared command is cannot-evaluate",
                  needle="cannot parse")
            _case("X2b", CFG, "tool write <(echo x)\n", 2,
                  "a process substitution beside a declared command is cannot-evaluate",
                  needle="cannot parse")
            _case("X2c", CFG, "tool write --row 9\n/usr/local/bin/tool write <(echo x) | tail -1\n", 2,
                  "a PATH-QUALIFIED declared command in unparseable shell is still seen",
                  needle="cannot parse")
            _case("X3a", CFG, "bash -c 'tool write | tail -1'\n", 2,
                  "a declared command inside a bash -c string is cannot-evaluate",
                  needle="bash -c")
            _case("X3b", CFG, "eval \"tool write | tail -1\"\ntool write --row 9\n", 2,
                  "a declared command inside an eval argument is cannot-evaluate (the clean second "
                  "line satisfies selector reconciliation, so only the eval guard can refuse)",
                  needle="eval argument")
            _case("X3c", CFG, "bash -ec 'tool write | tail -1'\ntool write --row 9\n", 2,
                  "a clustered -ec still carries -c; the string is cannot-evaluate",
                  needle="bash -c")
            _case("X3d", CFG, "eval '/usr/local/bin/tool write | tail -1'\ntool write --row 9\n", 2,
                  "a path-qualified declared command inside an eval argument is seen",
                  needle="eval argument")
            _case("X3e", CFG, "dash -c 'tool write | tail -1'\ntool write --row 9\n", 2,
                  "a declared command inside a dash -c string is cannot-evaluate",
                  needle="dash -c")
            _case("X3f", CFG, "zsh -c 'tool write | tail -1'\ntool write --row 9\n", 2,
                  "a declared command inside a zsh -c string is cannot-evaluate",
                  needle="zsh -c")
            _case("X4a", CFG, b"\xff\xfe not utf-8", 2,
                  "a non-UTF-8 declared surface is cannot-evaluate",
                  needle="not valid UTF-8")
            _case("X4b", "not toml [ at all\n", "tool write --row 9\n", 2,
                  "a malformed configuration is cannot-evaluate", needle="does not parse")
            _case("X4c", CFG.replace("format-version = 1", "format-version = 1.0"),
                  "tool write --row 9\n", 2, "a 1.0 format-version is cannot-evaluate",
                  needle="format-version")
            _case("X4d", CFG.replace("format-version = 1", "format-version = true"),
                  "tool write --row 9\n", 2, "a boolean format-version is cannot-evaluate",
                  needle="format-version")
            _case("X4e", CFG.replace('paths = ["cmd/job.sh"]', 'paths = ["cmd/none.sh"]'),
                  "tool write --row 9\n", 2, "an unmatched path selector is cannot-evaluate",
                  needle="matched no readable regular file")
            # X5: option words that can hide the command or the subcommand fail closed, whatever the
            # option's spelling and whatever the declared command's spelling (quoted, escaped).
            _case("X5a", CFG, "nice -n 10 tool write | tail -1\ntool write --row 9\n", 2,
                  "a wrapper option may consume the next word; the command is unprovable",
                  needle="wrapper option")
            _case("X5b", CFG, "tool --config x.toml write | tail -1\ntool write --row 9\n", 2,
                  "an option before the first operand hides the subcommand",
                  needle="option words before the first operand")
            _case("X5c", CFG, "nice --adjustment=10 tool write --row 9\n", 2,
                  "a value-attached wrapper option is an option word too; fail closed",
                  needle="wrapper option")
            _case("X5d", CFG, "nice -n 10 'to''ol' write | tail -1\ntool write --row 9\n", 2,
                  "a QUOTED declared name behind a wrapper option is read from the dequoted words",
                  needle="wrapper option")
            _case("X5e", CFG, "nice -n 10 to\\ol write | tail -1\ntool write --row 9\n", 2,
                  "an ESCAPED declared name behind a wrapper option is read from the dequoted words",
                  needle="wrapper option")
            # X6: a declared command inside a group, subshell, loop or coproc, and a grouped producer
            # piped onward, all fail closed: their execution shell or time is not modelled.
            _case("X6a", CFG, "(tool write) | tail -1\n", 2,
                  "a declared command inside a subshell is cannot-evaluate", needle="subshell")
            _case("X6b", CFG, "{ tool write; } | tail -1\n", 2,
                  "a declared command inside a brace group is cannot-evaluate", needle="brace group")
            _case("X6c", CFG, "while :; do tool write; done | tail -1\n", 2,
                  "a declared command inside a loop is cannot-evaluate", needle="loop")
            _case("X6d", CFG, '(to""ol write) | tail -1\n', 2,
                  "a QUOTED declared name inside a subshell is read from the dequoted words",
                  needle="subshell")
            _case("X6e", CFG, "(true) | tail -1\ntool write --row 9\n", 2,
                  "a grouped producer in a file that mentions a declared command is cannot-evaluate "
                  "even when the group itself holds none (disclosed over-refusal)",
                  needle="grouped or compound")
            _case("X6f", CFG, "coproc tool write\ntool write --row 9\n", 2,
                  "a declared command beside a coproc is cannot-evaluate", needle="coproc")
            _case("X6g", CFG, "(x=$(tool write)) | tail -1\n", 2,
                  "a declared command living only inside a piped group's substitution is read from "
                  "the UNMASKED text", needle="subshell")
            _case("X6h", CFG, "{ x=$(tool write); } | tail -1\n", 2,
                  "a declared command living only inside a piped brace group's substitution is read "
                  "from the UNMASKED text", needle="brace group")
            # X7: configuration validation diagnostics (each discriminates its own check: with the
            # check removed the run would exit 0 or print another message, failing the needle).
            _case("X7a", CFG.replace('paths = ["cmd/job.sh"]',
                                     'paths = ["cmd/job.sh", "cmd/**"]'),
                  "tool write --row 9\n", 2, "overlapping selectors are cannot-evaluate",
                  needle="overlaps")
            _case("X7b", "stray = 1\n" + CFG, "tool write --row 9\n", 2,
                  "an unknown top-level key is cannot-evaluate", needle="unknown top-level key")
            _case("X7c", CFG + '\n[[binding]]\nid = "row-writer"\npaths = ["cmd/job.sh"]\n'
                                'commands = ["tool"]\n',
                  "tool write --row 9\n", 2, "a duplicate binding id is cannot-evaluate",
                  needle="duplicate binding id")
            _case("X7d", CFG + 'extra = "x"\n', "tool write --row 9\n", 2,
                  "an unknown binding key is cannot-evaluate", needle="unknown key")
            _case("X7e", CFG.replace('id = "row-writer"', 'id = "Bad_Id"'),
                  "tool write --row 9\n", 2, "a non-kebab-case id is cannot-evaluate",
                  needle="kebab-case")
            _case("X7f", CFG.replace('commands = ["tool"]', 'commands = ["tool", "tool"]'),
                  "tool write --row 9\n", 2, "a duplicate roster element is cannot-evaluate",
                  needle="duplicate element")
            _case("X7g", CFG.replace('paths = ["cmd/job.sh"]', 'paths = ["cmd/../cmd/job.sh"]'),
                  "tool write --row 9\n", 2, "a '..' selector component is cannot-evaluate",
                  needle="component")
            _case("X7h", CFG.replace('paths = ["cmd/job.sh"]', 'paths = ["/abs/job.sh"]'),
                  "tool write --row 9\n", 2, "an absolute selector is cannot-evaluate",
                  needle="is absolute")
            _case("X7i", CFG,
                  "tool write --row 9\nrow=" + "$(" * 25 + "true" + ")" * 25 + "\n", 2,
                  "substitutions nested past the bound are cannot-evaluate",
                  needle="nested deeper")
            _case("X7j", CFG.replace('paths = ["cmd/job.sh"]', 'paths = ["cmd//job.sh"]'),
                  "tool write --row 9\n", 2, "an EMPTY selector component is cannot-evaluate",
                  needle="component")
            _case("X7k", CFG.replace('paths = ["cmd/job.sh"]', 'paths = ["./cmd/job.sh"]'),
                  "tool write --row 9\n", 2, "a '.' selector component is cannot-evaluate",
                  needle="component")
            # X12: a declared command inside a function body (directly, or only inside a substitution
            # there) is refused: the body runs at its call sites, which this gate does not model.
            _case("X12a", CFG, "set -o pipefail\nf() {\n  tool write | tail -1\n}\nf\n", 2,
                  "a declared command inside a function body is cannot-evaluate",
                  needle="function body")
            _case("X12b", CFG, "set -o pipefail\nf() {\n  row=$(tool write | tail -1)\n}\n"
                               "set +o pipefail\nf\n", 2,
                  "a declared command inside a function-body SUBSTITUTION is read from the unmasked "
                  "text and refused; the later disable can precede the call",
                  needle="function body")
            # X13/X14: argument-executing commands may run the declared command themselves.
            _case("X13", CFG, "flock /abs/l tool write | tail -1\ntool write --row 9\n", 2,
                  "a declared command in flock's argument list is cannot-evaluate",
                  needle="argument list of 'flock'")
            _case("X14", CFG, "printf x | xargs tool write | tail -1\ntool write --row 9\n", 2,
                  "a declared command in xargs's argument list is cannot-evaluate",
                  needle="argument list of 'xargs'")

            # X8: an INACCESSIBLE configuration parent is never NOT APPLICABLE.
            r = _fresh("X8", CFG, {"cmd/job.sh": "tool write --row 9\n"})
            aiqt_dir = r / ".aiqt"
            os.chmod(aiqt_dir, 0)
            try:
                blocked = False
                try:
                    os.lstat(aiqt_dir / "relied-exit-pipes.toml")
                except OSError:
                    blocked = True
                if blocked:
                    rc, text = _run_quiet(r)
                    if rc != 2 or "cannot inspect the configuration" not in text:
                        failures.append("X8: an inaccessible configuration parent expected fail-closed "
                                        "exit 2 naming the inspection failure, got {}".format(rc))
                else:
                    _not_run(failures, "X8 the inaccessible-configuration-parent case",
                             "this account bypasses directory permissions")
            finally:
                os.chmod(aiqt_dir, 0o755)

            # X9: symlinks anywhere in a selector's resolution fail closed, never a silent skip.
            # X9a: a symlinked declared surface file.
            r = _fresh("X9a", CFG, {"cmd/real.sh": "tool write --row 9\n"})
            link = r / "cmd" / "job.sh"
            try:
                symlink(link, r / "cmd" / "real.sh")
            except (OSError, NotImplementedError) as exc:
                _not_run(failures, "X9a the symlinked declared-surface case",
                         "no symlink support ({})".format(exc))
            else:
                rc, text = _run_quiet(r)
                if rc != 2 or "symlink" not in text:
                    failures.append("X9a: a symlinked declared surface expected exit 2 naming the "
                                    "symlink, got {}".format(rc))
            # X9b: a symlinked SUBDIRECTORY under a dir/** selector is refused, never skipped.
            r = _fresh("X9b", CFG_PREFIX, {"cmd/good.sh": "tool write --row 9\n",
                                           "other/bad.sh": "tool write | tail -1\n"})
            try:
                symlink(r / "cmd" / "link", r / "other")
            except (OSError, NotImplementedError) as exc:
                _not_run(failures, "X9b the symlinked-subdirectory case",
                         "no symlink support ({})".format(exc))
            else:
                rc, text = _run_quiet(r)
                if rc != 2 or "symlinked directory" not in text:
                    failures.append("X9b: a symlinked subdirectory under dir/** expected exit 2 naming "
                                    "it, got {} (a silent skip would hide the subtree)".format(rc))
            # X9c: a symlinked ANCESTOR of an exact selector is refused, never followed.
            r = _fresh("X9c", CFG, {"real/job.sh": "tool write | tail -1\n"})
            try:
                symlink(r / "cmd", r / "real")
            except (OSError, NotImplementedError) as exc:
                _not_run(failures, "X9c the symlinked-ancestor case",
                         "no symlink support ({})".format(exc))
            else:
                rc, text = _run_quiet(r)
                if rc != 2 or "crosses a symlink" not in text:
                    failures.append("X9c: an exact selector behind a symlinked directory expected "
                                    "exit 2 naming the crossing, got {}".format(rc))
            # X9d: a declared surface that is not a regular file (a FIFO) is refused, never read.
            r = _fresh("X9d", CFG, {"cmd/keep": ""})
            try:
                os.mkfifo(r / "cmd" / "job.sh")
            except (OSError, NotImplementedError, AttributeError) as exc:
                _not_run(failures, "X9d the non-regular-file case",
                         "no FIFO support ({})".format(exc))
            else:
                rc, text = _run_quiet(r)
                if rc != 2 or "not a regular file" not in text:
                    failures.append("X9d: a FIFO declared surface expected exit 2 naming the "
                                    "non-regular file, got {}".format(rc))
            # X9e: a FIFO met during a dir/** walk is refused BEFORE any read (a read would block).
            r = _fresh("X9e", CFG_PREFIX, {"cmd/a.sh": "tool write --row 9\n"})
            try:
                os.mkfifo(r / "cmd" / "pipe.fifo")
            except (OSError, NotImplementedError, AttributeError) as exc:
                _not_run(failures, "X9e the FIFO-under-prefix case",
                         "no FIFO support ({})".format(exc))
            else:
                rc, text = _run_quiet(r)
                if rc != 2 or "which is not a regular file" not in text:
                    failures.append("X9e: a FIFO under dir/** expected exit 2 naming the non-regular "
                                    "entry, got {}".format(rc))
            # X9f: a symlinked FILE met during a dir/** walk is refused with the SYMLINK diagnostic
            # (its own branch, not the regular-file fallback).
            r = _fresh("X9f", CFG_PREFIX, {"cmd/a.sh": "tool write --row 9\n",
                                           "cmd/real.sh": "tool write --row 8\n"})
            try:
                symlink(r / "cmd" / "link.sh", r / "cmd" / "real.sh")
            except (OSError, NotImplementedError) as exc:
                _not_run(failures, "X9f the symlinked-file-under-prefix case",
                         "no symlink support ({})".format(exc))
            else:
                rc, text = _run_quiet(r)
                if rc != 2 or "contains a symlink " not in text:
                    failures.append("X9f: a symlinked file under dir/** expected exit 2 with the "
                                    "symlink branch's own diagnostic, got exit {} (another refusal "
                                    "may have produced the exit code)".format(rc))
            # X9g/X9h: a __pycache__ or .git entry inside a declared subtree refuses, never a prune.
            r = _fresh("X9g", CFG_PREFIX, {"cmd/a.sh": "tool write --row 9\n",
                                           "cmd/__pycache__/bad.sh": "tool write | tail -1\n"})
            rc, text = _run_quiet(r)
            if rc != 2 or "never silently pruned" not in text:
                failures.append("X9g: a __pycache__ directory under dir/** expected exit 2 naming the "
                                "metadata entry, got {} (a silent prune would hide its files)".format(
                                    rc))
            r = _fresh("X9h", CFG_PREFIX, {"cmd/a.sh": "tool write --row 9\n",
                                           "cmd/.git/hook.sh": "tool write | tail -1\n"})
            rc, text = _run_quiet(r)
            if rc != 2 or "never silently pruned" not in text:
                failures.append("X9h: a .git directory under dir/** expected exit 2 naming the "
                                "metadata entry, got {}".format(rc))
            # X9i: a SYMLINK named __pycache__ is refused by name, before any symlink handling could
            # prune it (the pruned name used to hide the symlink entirely).
            r = _fresh("X9i", CFG_PREFIX, {"cmd/a.sh": "tool write --row 9\n",
                                           "other/bad.sh": "tool write | tail -1\n"})
            try:
                symlink(r / "cmd" / "__pycache__", r / "other")
            except (OSError, NotImplementedError) as exc:
                _not_run(failures, "X9i the symlink-named-__pycache__ case",
                         "no symlink support ({})".format(exc))
            else:
                rc, text = _run_quiet(r)
                if rc != 2 or "never silently pruned" not in text:
                    failures.append("X9i: a symlink named __pycache__ under dir/** expected exit 2 "
                                    "naming the metadata entry, got {}".format(rc))

            # X11: a FIFO at the CONFIGURATION path is refused before open (an open would block).
            r = base_tmp / "X11-fifo-config"
            (r / ".aiqt").mkdir(parents=True, exist_ok=True)
            (r / "cmd").mkdir(parents=True, exist_ok=True)
            (r / "cmd" / "job.sh").write_text("tool write --row 9\n", encoding="utf-8")
            try:
                os.mkfifo(r / ".aiqt" / "relied-exit-pipes.toml")
            except (OSError, NotImplementedError, AttributeError) as exc:
                _not_run(failures, "X11 the FIFO-configuration case",
                         "no FIFO support ({})".format(exc))
            else:
                rc, text = _run_quiet(r)
                if rc != 2 or "refusing to open" not in text:
                    failures.append("X11: a FIFO configuration expected fail-closed exit 2 before any "
                                    "open, got {}".format(rc))

            # X10: a present-but-dangling symlink CONFIGURATION fails closed, never NOT APPLICABLE.
            r = base_tmp / "X10-dangling-config"
            (r / ".aiqt").mkdir(parents=True, exist_ok=True)
            cfg_link = r / ".aiqt" / "relied-exit-pipes.toml"
            try:
                symlink(cfg_link, r / ".aiqt" / "does-not-exist.toml")
            except (OSError, NotImplementedError) as exc:
                _not_run(failures, "X10 the dangling-symlink configuration case",
                         "no symlink support ({})".format(exc))
            else:
                rc, text = _run_quiet(r)
                if rc != 2 or "symlink" not in text:
                    failures.append("X10-dangling-config: a dangling-symlink configuration expected "
                                    "fail-closed exit 2 naming the symlink, got {}".format(rc))
        finally:
            shutil.rmtree(base_tmp, ignore_errors=True)

    if failures:
        print("SELF-TEST FAIL:")
        for f in failures:
            print("  - " + f)
        return 1
    print("SELF-TEST PASS: the refuse cases R1-R53 and Rb1 (the seed, substitution bodies, AND lists, "
          "if conditions, |&, bare wrappers and absolute paths, every enable outside the standalone "
          "column-1 closed form (piped, backgrounded, conditional, wrapped, coproc'd, continued, a "
          "final stage, or sharing its line), every whole-file credit withdrawal (a disable at any "
          "level, builtin, eval, source, a function definition on either side of the enable, a "
          "variable-driven set, positional parameters mentioning pipefail), every read outside the "
          "closed E2 set (a mismatched index, a late, quoted, commented, quote-mangled, bare-array, "
          "extra-word or non-adjacent read, a pipeline ending in '&' or '||' or followed on its line), "
          "a line continuation, a first and a MIDDLE pipeline stage asserted by index, a "
          "substitution-body read, a nested dir/** finding asserted by file, a quoted declared name, "
          "and the enable-after-pipeline boundary) each fire and each catches the never-report mutant; "
          "the targeted stage-truncation, raw-text-PIPESTATUS and forced-file-credit mutants are each "
          "caught; the controls C1-C27 (no config, undeclared producers, the accepted pipefail "
          "spellings, the closed PIPESTATUS reads including both whole-array saves, the quoted scalar "
          "and a semicolon-terminated pipeline, a line-ending pipe, a final-stage command, quoted and "
          "commented text, an OR list, a quoted program string, an undeclared subcommand, a quoted "
          "heredoc body, a redirect, an unrelated shopt, a clean dir/** walk, a bare wrapper, a "
          "top-level enable after a closed case or select body, and a quoted 'select' command name) "
          "each stay clean and the patchable ones catch their over-broad "
          "mutants; and the cannot-evaluate cases X1-X14 (unmatched selectors, unparseable shell "
          "beside a plain or path-qualified declared command, sh -c/bash -c/dash -c/zsh -c/eval "
          "strings with plain and clustered options, wrapper-option and subcommand opacity, quoted and "
          "escaped declared names behind wrappers and groups, declared commands in subshells, brace "
          "groups, loops, coprocs, function bodies and xargs/flock argument lists, grouped producers, "
          "malformed configurations and empty, dot and dot-dot selector components, an inaccessible "
          "configuration parent, symlinked files, subdirectories, ancestors and configurations, FIFO "
          "surfaces and configurations, and .git/__pycache__ entries refused rather than pruned) each "
          "fail closed printing the diagnostic of their OWN check")
    return 0


def _parse_args(argv):
    self_test = False
    for arg in argv:
        if arg == "--self-test":
            self_test = True
        else:
            print("usage: check_relied_exit_pipes.py [--self-test]; fail-closed", file=sys.stderr)
            return None
    return (self_test,)


def main():
    parsed = _parse_args(sys.argv[1:])
    if parsed is None:
        return 2
    (self_test,) = parsed
    if self_test:
        return self_test_main()
    root = Path(__file__).resolve().parents[1]
    return run(root)


if __name__ == "__main__":
    sys.exit(main())
