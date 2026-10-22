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
  E1  pipefail is provably on. The credit is an ALLOWLIST over the WHOLE FILE: every line must be one
      of a small closed set of forms, read from the shared lexer's dequoted words and the raw text
      together: a blank line; a comment line; the standalone column-1 enable (`set` first, then only
      '-' option clusters whose letters come from bash's own set-flag vocabulary and whose 'o'
      letters consume names from bash's own `set -o` vocabulary, pipefail among them, ending at the
      newline or one trailing ';' with nothing after it; an unknown letter or name makes bash reject
      the set command and run on with pipefail OFF, so it earns nothing, and a 'posix' name also
      earns nothing: posix mode turns alias expansion on, state this grammar does not model); a
      plain assignment, never to a variable that is a view of the shell's own bindings or startup
      hooks, or that turns posix mode on (BASH_ALIASES, BASH_CMDS, BASH_ENV, SHELLOPTS, BASHOPTS,
      POSIXLY_CORRECT); or a command line of plain
      simple commands joined by |, |&, &&, ||, ';' or '&' whose every command word is a literal name
      spelled PLAIN (its own token unquoted and unescaped) that is neither a refused word (set,
      eval, source, the '.' dot command, builtin, command, exec, alias, unalias, enable, trap,
      function, sh/bash/dash/zsh/ksh, time, coproc and every reserved word) nor ANY bash builtin
      outside a small modelled allowlist (true, false, echo, pwd, test, times, type; every other
      builtin, printf, read, declare, mapfile and readarray among them, can write options, aliases,
      bindings or variables, or run callback text this reader never sees; shopt is admitted only as
      `shopt -s NAME...` with every NAME from a closed roster of glob-steering option names) and
      that carries no '$', backquote, escape, parenthesis, brace, redirection or trailing comment.
      Every mention of `pipefail` anywhere in the file must additionally sit on a line that IS the
      standalone enable. ANY line outside the set withdraws the credit for the whole file (refusing
      toward a finding; this over-refuses files that keep control flow, redirections, expansions or
      trailing comments beside an enable, disclosed). The enable must additionally be a top-level
      statement in the lexed reading (not inside a block or subshell, not a pipeline stage, not
      backgrounded, not the right arm of && or ||) and sit before the pipeline. `set -- -o pipefail`
      sets positional parameters, never the option. RESIDUAL, stated exactly: the gate reads only the
      file, so inherited environment the file does not show (BASH_ENV, an exported shell function
      shadowing a command word, a parent shell's options such as SHELLOPTS or BASHOPTS) is not
      modelled.
  E2  the script reads the command's own exit: the pipeline is a top-level statement ending at a
      newline or one ';' (never '&', '&&' or '||'), nothing but whitespace and at most that ';'
      follows the final stage on its own line (text BEFORE the pipeline on that line is allowed), no
      heredoc opens on any of the pipeline's lines, and the IMMEDIATELY NEXT LINE, which must also be
      the immediately next LEXED record beginning exactly at that line, is one of a closed set of
      read forms, matched on the lexically intact line (never on text with quotes removed):
      NAME=${PIPESTATUS[N]} or NAME="${PIPESTATUS[N]}" with N exactly the flagged stage's index in
      CANONICAL decimal (bash reads a leading-zero index as octal, so 010 earns nothing), or
      NAME=("${PIPESTATUS[@]}"). Both credited forms are plain assignments with NO command word, so
      no alias, function or re-enabled binding changes what the assignment itself runs. The
      assignment can still be SKIPPED: with the extdebug shell option on, a DEBUG trap whose text
      returns nonzero makes bash skip the next command, the save included (observed in bash 5.3,
      which left the saving variable unset, for the lowercase condition spelling too), and either
      half can arrive invisibly (extdebug through a parent's BASHOPTS, the trap through BASH_ENV).
      The credit is therefore withdrawn for the WHOLE FILE where the raw text or any dequoted word
      spells extdebug, where any dequoted word on its own names DEBUG or SIGDEBUG
      (case-insensitively), where a record's resolved command word is source or '.' (a sourced
      file runs in THIS shell, where it can turn extdebug on or install the trap unseen), where a
      record's resolved command word is any other member of E1's refused set, read from the ONE
      shared roster so the two grammars cannot drift apart (eval and builtin can reach source,
      shopt or trap under a name no source or trap scan resolves: builtin source lib.sh resolves
      to builtin, never to source; alias, unalias and enable re-bind what a later word runs;
      set, exec and function defer or hide state the same roster refuses for E1; the sh family
      runs a child shell that cannot reach this shell's save but withdraws with the rest of the
      set, an over-refusal, disclosed; trap is judged by its own stricter branch below, and the
      set's reserved words stay with the command-word resolver: a plain one is consumed as syntax
      before any command word resolves, a block closer such as fi runs nothing, and a quoted one
      names an ordinary command), where a record's command
      word holds an expansion character ('$', a backquote, '{', '*', '?' or '[': a parameter,
      brace or pathname expansion such as ${t}p, tr{ap,} or tra[p] can resolve to trap or shopt
      at run time; a literal command word merely containing such a character withdraws too, an
      over-refusal, disclosed) or stands unresolvable behind a wrapper's option words (a
      built or hidden command word can resolve to trap at run time; substitution bodies are
      scanned as their own contexts), where a record's resolved command word is shopt and any
      later word holds an expansion character (an option-name operand built at run time, such as
      extdeb{ug,}, spells extdebug in text the extdebug scans never decode; shopt with only
      literal operands is left to those scans, so shopt -s nullglob keeps the credit, and an
      operand that merely contains such a character withdraws, an over-refusal, disclosed), or
      where the word trap stands in any record's dequoted words
      except as a trap command whose every condition is its own plain literal name other than
      DEBUG (one optional SIG prefix stripped, compared case-insensitively; a condition spelled
      quoted, escaped, variable-driven or in any other form this reader cannot resolve to a
      literal name counts as naming DEBUG, and a trap spelled again inside a trap's own arguments
      withdraws too: an ERR or signal action can install a DEBUG trap when it fires). A file
      whose only trap is a plainly spelled EXIT, ERR or signal trap keeps the credit, and a
      comment mentioning trap, source or DEBUG does too (comments lex to no words); a file that
      merely mentions DEBUG as a word, sources a helper, builds a command word, or runs any
      refused-set word (an eval, builtin or alias whose use is entirely harmless included) keeps
      no credit,
      and a comment or string spelling extdebug, a prose argument spelled trap, a quoted condition
      name, a printing trap -p DEBUG, and a trap inside a plain ( ) subshell or a command
      substitution, which runs where it cannot reach the save, all withdraw it (over-refusals,
      disclosed; the plain assignment save is still the credited fix, in a file that shows none
      of these routes). RESIDUAL, stated
      exactly, the same stance as E1's: a DEBUG trap or extdebug the file does not show (installed
      by BASH_ENV, inherited through a parent's BASHOPTS, which was observed in bash 5.3 to
      enable debugging only where a debugger start file is installed and to warn and disable it
      otherwise, so treating it as reachable everywhere errs toward withdrawal, or an exported
      shell function; SHELLOPTS
      cannot carry extdebug, because bash rejects set -o extdebug, observed in bash 5.3) or
      spelled only in text this reader never decodes (an ANSI-C $'...' spelling of the word, or
      trap action text assembled at run time from fragments the file never spells plainly)
      is not modelled. The
      `declare -a NAME=("${PIPESTATUS[@]}")` spelling earns NO credit, in any file (a disclosed
      over-refusal): `declare` is a COMMAND word, and a redefinition can reach it through spellings
      no closed word scan provably bounds (a variable-held or keyword-shadowed command word, an
      indirect BASH_ALIASES write, callback text, inherited environment such as BASH_ENV or an
      exported function), so the whole form sits outside the closed read set instead of behind a
      redefinition scan; the fix is the plain assignment save, which reads the same statuses.
      Only a space or tab may surround the read: CR, FF or NBSP is not shell whitespace. A
      blank or comment line before the read, an extra word on the read line, or any other spelling
      (an unquoted array save, arithmetic, a different index) is not credited.
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
                                      a wrapper given ANY option word, and a declared NAME inside a
                                      subshell, brace group, loop, function body (one-line
                                      definitions included), coproc, or among the words of
                                      xargs/flock/find/eval/sh -c and kin, refuses with exit 2, even
                                      where that name is only another command's argument data)
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
     __pycache__ entry (directory, symlink or regular file), or non-regular file met in a directory
     walk, a non-regular or non-UTF-8 declared surface, a declared command inside or after shell the
     gate cannot parse (an unquoted heredoc, a here-string, a process substitution, an unterminated
     construct; the refusal covers the file's remainder from that point), a declared command inside
     the raw text of an `sh -c`, `bash -c`, `dash -c`, `zsh -c`, `ksh -c` or `eval` argument
     (clustered options such as `-ec` included), among the argument words of `xargs`, `flock` or
     `find`, beside a `coproc`, possibly hidden behind a wrapper's option words (any option word
     after a wrapper: separate, attached with '=', or '--'), a declared command whose subcommand sits
     behind option words, a declared NAME inside a subshell, brace group, loop body or function body
     (one-line definitions and non-brace bodies included: constructs whose execution time or shell
     this gate does not model; the name counts even where it is only another command's argument data,
     a disclosed over-refusal), a function definition whose body is not a compound form this gate
     models in a file that mentions a declared command (a disclosed over-refusal: such a body's
     execution cannot be attributed), or a grouped or compound pipeline producer (a subshell, brace
     group, or loop piped onward) in a file that mentions a declared command. Declared identity in
     raw text is matched QUOTE-TOLERANTLY (to""ol, 'to''ol', to\\ol still count): failing to prove
     absence never reads as clean.
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
WRAPPERS = frozenset(("env", "nice", "timeout", "time", "command", "exec", "nohup", "stdbuf", "sudo",
                      "ionice"))
# Shell reserved words that keep the command-word position open (the real command follows). A reserved
# word counts ONLY when spelled plain: a quoted or escaped spelling ("select") is a command name.
RESERVED_SKIP = frozenset(("if", "then", "else", "elif", "do", "while", "until", "for", "case",
                           "select", "{", "}", "!", "coproc"))
BLOCK_OPEN = frozenset(("if", "while", "until", "for", "case", "select", "{"))
BLOCK_CLOSE = frozenset(("fi", "done", "esac", "}"))
PIPE_SEPS = ("|", "|&")
MAX_SUBST_DEPTH = 20
# Commands that hand their argument words to another execution environment: a declared command among
# them cannot be attributed to a pipeline stage, so it refuses (exit 2). find belongs here for its
# -exec/-execdir/-ok arguments; a declared NAME anywhere among find's words refuses, even when it is
# only a -name operand (an over-refusal, disclosed).
ARG_EXECUTORS = frozenset(("xargs", "flock", "find"))
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


def _skip_blank_and_continuations(raw, i, n):
    """The first index at or after i that holds neither shell whitespace nor a backslash-newline:
    bash removes a backslash-newline wherever it stands outside quotes, so between tokens it is only
    spacing (an indented continuation line still begins the SAME command), never a token character.
    Single-quoted text never reaches this skip: the token walk consumes a quoted span whole."""
    while i < n and (raw[i] in " \t\n" or (raw[i] == "\\" and i + 1 < n and raw[i + 1] == "\n")):
        i += 2 if raw[i] == "\\" else 1
    return i


def _leading_plain(raw, argv):
    """Per-TOKEN plainness for the leading words of one segment: flags[j] is True when argv[j] is
    spelled in raw as one contiguous plain token (no quote or escape touches that token). A whole-text
    search cannot tell WHICH token is quoted ("if" x if holds a quoted command word and a plain
    argument of the same spelling), so this walks raw and argv together from the start, dequoting each
    raw token the way the lexer does, and stops at the first token that no longer aligns (a
    redirection, an operator, an ANSI-C quoting this walk does not decode); callers treat positions
    past the returned list conservatively through _unresolved_token_plain. A backslash-newline is
    REMOVED before reading, exactly as bash removes it before word splitting, WHEREVER it stands
    outside single quotes: inside a token (the joined token is still one plain word), between
    tokens, where it is only spacing, so a continuation followed by indentation still begins the
    same command and a continued keyword is still that keyword, and inside DOUBLE quotes, where the
    backslash keeps its special meaning before a newline, so the continued quoted token decodes to
    the lexer's own word and stays non-plain. Any other escape keeps the token non-plain."""
    flags = []
    n = len(raw or "")
    raw = raw or ""
    i = 0
    for word in argv:
        i = _skip_blank_and_continuations(raw, i, n)
        if i >= n or raw[i] in "<>()&|;":
            break
        token = []
        plain = True
        while i < n and raw[i] not in " \t\n<>()&|;":
            c = raw[i]
            if c == "\\":
                if i + 1 < n and raw[i + 1] == "\n":
                    # bash removes a backslash-newline before word splitting: the joined token is
                    # still one plain word, so a continued keyword stays the keyword.
                    i += 2
                    continue
                if i + 1 < n:
                    token.append(raw[i + 1])
                i += 2
                plain = False
                continue
            if c == "'":
                j = raw.find("'", i + 1)
                if j == -1:
                    return flags
                token.append(raw[i + 1:j])
                i = j + 1
                plain = False
                continue
            if c == '"':
                j = i + 1
                while j < n and raw[j] != '"':
                    if raw[j] == "\\" and j + 1 < n and raw[j + 1] == "\n":
                        # bash removes a backslash-newline INSIDE double quotes too (the backslash
                        # keeps its special meaning before a newline), so the continued span is
                        # still one token, decoded as the lexer decodes it; it stays non-plain.
                        j += 2
                    elif raw[j] == "\\" and j + 1 < n and raw[j + 1] in '"\\$`':
                        token.append(raw[j + 1])
                        j += 2
                    else:
                        token.append(raw[j])
                        j += 1
                if j >= n:
                    return flags
                i = j + 1
                plain = False
                continue
            if c == "$" and i + 1 < n and raw[i + 1] == "'":
                return flags
            token.append(c)
            i += 1
        if "".join(token) != word:
            break
        flags.append(plain)
    return flags


def _unresolved_token_plain(raw, word):
    """Plainness for a token the leading walk could NOT align (it sits past a redirection, an
    operator kept in-segment, or a quoting form the walk does not decode): always False. An unknown
    token's identity is NEVER resolved by finding another plain word of the same spelling elsewhere
    in the segment. False reads the token as a command name, not syntax, which matches bash: a
    reserved word is syntax only at a command start, and every position past such a stop is already
    mid-command (after a redirection or an assignment prefix, `if` names the command if)."""
    return False


def _command_start_unresolved(flags, argv, raw):
    """True when the leading walk aligned NO token even though this segment's raw text begins with
    token text rather than a redirection or operator stop. The segment's first word then stands at a
    COMMAND START, where the token's SPELLING decides what it is: a plain spelling can be a reserved
    word, while a quoted or escaped spelling always names a command in bash. The walk could not
    decode this spelling (an ANSI-C quoting it does not decode, a walk/lexer divergence, an
    unterminated span), so it cannot certify which case holds: were the undecoded spelling actually
    plain (a divergence, not a quoting), reading it as a command name could silently pass a loop or
    group whose body this gate refuses to model. The caller therefore fails closed, and does so only
    for a segment that EXECUTES something and has a declared command in reach (a blank, comment-only
    or keyword-only record is never judged by this guard). This over-refuses the genuinely quoted
    spellings, which bash would run as ordinary commands (disclosed). A walk stopped by a leading
    redirection or operator is NOT this case: past such a stop bash itself no longer reads reserved
    words as syntax, so the first word is a command name there (_unresolved_token_plain)."""
    if flags or not argv:
        return False
    raw = raw or ""
    n = len(raw)
    i = _skip_blank_and_continuations(raw, 0, n)
    return i < n and raw[i] not in "<>()&|;"


def _command_word(argv, raw=None):
    """The command word of one segment: (index, basename, opens_before, opaque). Skips shell reserved
    words that keep the command position open (counting the block openers among them, so a command
    inside this segment's own `if` or `while` head reads as one level deeper), NAME=value assignments,
    the common wrappers used bare, and a duration-like operand after `timeout`. ANY option word after a
    wrapper (separate, value-attached with '=', or '--') makes the result OPAQUE (index and basename
    None, opaque True): whether the following words are option values or the command cannot be told
    from the text (nice -n 10 CMD versus nice -x CMD), so the caller fails closed when a declared name
    could hide behind it. When raw is given, a reserved word is skipped only when its OWN token is
    spelled plain (per-token through _leading_plain; where that walk stopped, the token's identity is
    unknown and _unresolved_token_plain reads it as a command name, never as syntax): a quoted or
    escaped spelling is a command name in bash, so it is returned as the command word instead
    (raw=None, used by the pure-argv unit layer, treats every word as plain)."""
    flags = _leading_plain(raw, argv) if raw is not None else None

    def _plain_at(j, w):
        if raw is None:
            return True
        if j < len(flags):
            return flags[j]
        return _unresolved_token_plain(raw, w)

    opens = 0
    i = 0
    after_wrapper = False
    after_timeout = False
    while i < len(argv):
        w = argv[i]
        if w in RESERVED_SKIP and _plain_at(i, w):
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


# One enabling-set option token: a '-' sign followed only by letters from bash's own set-flag
# vocabulary (help set). '--', a lone '-', any '+' sign, an UNKNOWN letter and any longer or quoted
# spelling fall outside the closed set and earn no credit: bash rejects an unknown option letter and
# keeps running with pipefail OFF, so such a line never proves the option on.
_E1_OPTION_RE = re.compile(r"^-[abefhkmnoptuvxBCEHPT]+$")
# The option-name operands `set -o` accepts (bash 5's closed vocabulary). An unknown name makes bash
# reject the whole set command and keep running with pipefail off (set -o nonexistent -o pipefail
# enables nothing), so only a line spelled entirely from this vocabulary earns credit.
_E1_SET_O_NAMES = frozenset((
    "allexport", "braceexpand", "emacs", "errexit", "errtrace", "functrace", "hashall", "histexpand",
    "history", "ignoreeof", "interactive-comments", "keyword", "monitor", "noclobber", "noexec",
    "noglob", "nolog", "notify", "nounset", "onecmd", "physical", "pipefail", "posix", "privileged",
    "verbose", "vi", "xtrace"))

# The closed LINE GRAMMAR for the whole-file pipefail credit. A credited file may contain ONLY: blank
# lines, comment lines, the standalone column-1 enable, plain assignments (never to a binding-view
# variable, below), and command lines built from plain simple commands. On a command line, every
# command word must be a literal name whose OWN token is spelled plain (a quoted or escaped command
# word earns nothing), read from the lexer's DEQUOTED words (so sour''ce, "set" and e\\val are still
# their refused commands) and from the raw text together, and must be outside this refused set AND
# outside the builtin roster below (except the few modelled-safe builtins). The refused names are the
# words that can change, defer or hide the shell's option state: set reaches the options directly
# (shopt does too and is admitted only through its own narrower rule in _e1_command_line); eval,
# source, '.', and the sh family run text this reader never sees; builtin, command and exec reach set
# indirectly; alias, unalias, enable and trap re-bind what a later word runs; function (and every
# reserved word) opens control flow whose execution time or count this reader does not model.
# Everything here refuses toward a finding, never toward a silent pass.
_E1_REFUSED_WORDS = frozenset((
    "set", "eval", "source", ".", "builtin", "command", "exec", "alias", "unalias",
    "enable", "trap", "sh", "bash", "dash", "zsh", "ksh", "function", "time", "coproc",
    "if", "then", "else", "elif", "fi", "while", "until", "for", "select", "do", "done",
    "case", "esac", "in"))
# Every builtin bash 5 ships (its own closed roster). A builtin runs INSIDE the script's shell, where
# it can reach state this grammar does not model: options, aliases and bindings (printf -v and read
# can write BASH_ALIASES; declare, let, local, export and their kin assign variables; mapfile and
# readarray run -C callback text this reader never parses), so ANY builtin outside the small modelled
# allowlist below takes its line out of the closed grammar and withdraws the credit. An external
# command cannot change the running shell's own state, so a name outside this roster stays inside the
# grammar; a builtin a FUTURE bash adds would read as external, the disclosed bound of this roster.
_E1_BUILTINS = frozenset((
    ".", ":", "[", "alias", "bg", "bind", "break", "builtin", "caller", "cd", "command", "compgen",
    "complete", "compopt", "continue", "declare", "dirs", "disown", "echo", "enable", "eval", "exec",
    "exit", "export", "false", "fc", "fg", "getopts", "hash", "help", "history", "jobs", "kill",
    "let", "local", "logout", "mapfile", "popd", "printf", "pushd", "pwd", "read", "readarray",
    "readonly", "return", "set", "shift", "shopt", "source", "suspend", "test", "times", "trap",
    "true", "type", "typeset", "ulimit", "umask", "unalias", "unset", "wait"))
# The builtins the grammar DOES model: none of these can change the shell's options, its alias or
# binding tables, or a variable, or run text this reader never sees. (':', '[' and '.' never reach
# this allowlist: they fail the command-word spelling rule first, and '.' is refused by name.)
_E1_SAFE_BUILTINS = frozenset(("true", "false", "echo", "pwd", "test", "times", "type"))
# The shopt option names the grammar models as INERT: they steer pathname expansion and pattern
# matching only, never aliases, bindings, options or where text runs. A shopt line is admitted only
# as `shopt -s NAME...` with every NAME from this roster; every other shopt spelling (-u, -o, -q, -p,
# a clustered flag, lastpipe, expand_aliases, or any name outside the roster) withdraws the credit.
_E1_SHOPT_INERT = frozenset(("nullglob", "dotglob", "globstar", "extglob", "failglob", "nocaseglob",
                             "nocasematch"))
# Assignment names that are views of the shell's own binding state or its startup hooks: a write to
# one of these re-binds what a later word runs (or what a child shell reads at startup), so an
# assignment to any of them is outside the grammar. POSIXLY_CORRECT belongs here too: assigning it
# (any value) turns posix mode on in the running shell, and posix mode turns alias expansion ON,
# state this grammar does not model (the same reason the enable line refuses the 'posix' name).
_E1_BINDING_VARS = frozenset(("BASH_ALIASES", "BASH_CMDS", "BASH_ENV", "SHELLOPTS", "BASHOPTS",
                              "POSIXLY_CORRECT"))
# A command word in the closed grammar: a literal name, never a path, an option or an expansion.
_E1_WORD_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.+-]*$")
# Separators a closed-grammar command line may use. '(' and ')' (subshells, function definitions,
# case patterns) are outside the grammar; their raw characters are refused below anyway.
_E1_LINE_SEPS = frozenset(("", ";", "&&", "||", "|", "|&", "&"))
# Raw characters that take a line outside the closed grammar whatever the lexer reads: '$' and '`'
# bring expansion or substitution, '\\' brings escapes and continuations, parentheses and braces bring
# grouping and definitions, '<' and '>' bring redirections and heredocs, and '#' here is a TRAILING
# comment (whole-line comments were already accepted). Refusing them over-refuses ordinary lines (a
# redirection, "$var" as data); that is deliberate and disclosed: the grammar stays small and closed.
_E1_RAW_FORBIDDEN = "$`\\(){}<>#"


def _e1_enable_line(line):
    """True when `line` (one line of the file, without its newline) is exactly a standalone
    pipefail-enabling set command: `set` first, then only plain short-option tokens from the validated
    option vocabulary whose 'o' letters consume option names from the validated name vocabulary,
    turning pipefail ON, with nothing else before the end of the line or one trailing ';'. Anything
    outside this closed set (a wrapper before set, '--', a '+' sign, an unknown letter or name, an
    operand, a second command after ';', a trailing comment, a '$', a non-ASCII or control character
    that bash would read as part of a word) earns no credit. A 'posix' name also earns no credit even
    though bash accepts it: posix mode turns alias expansion ON, state this grammar does not model."""
    if re.fullmatch(r"[ -~\t]*", line) is None:
        return False
    s = line.strip(" \t")
    if s.endswith(";"):
        s = s[:-1].rstrip(" \t")
    if ";" in s:
        return False
    toks = [t for t in re.split(r"[ \t]+", s) if t]
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
                if i >= len(toks) or toks[i] not in _E1_SET_O_NAMES:
                    return False
                if toks[i] == "posix":
                    return False
                if toks[i] == "pipefail":
                    enabled = True
        i += 1
    return enabled


def _e1_command_line(line):
    """True when `line` is one closed-grammar command line: only ASCII printable text, none of the
    refused raw characters, and, in the lexed reading of the line on its own, only plain simple
    commands (optionally after NAME=value assignments, or assignments alone, never assigning a
    binding-view variable) joined by the permitted separators. Each command word must be a literal
    name whose own token is spelled PLAIN, outside the refused set, and not a bash builtin outside
    the modelled-safe allowlist: a builtin runs in this shell and can write options, aliases,
    bindings or variables (printf -v, read, declare, let) or run callback text this reader never
    parses (mapfile/readarray -C), so the grammar admits only externals and the few safe builtins.
    The refused-set and builtin checks read the DEQUOTED words so a quoted spelling cannot slip,
    and the plainness rule refuses the quoted spelling itself. A shopt command is admitted only as
    `shopt -s NAME...` with every NAME from the inert roster. A line the lexer cannot finish on its
    own (an open quote, a continuation) is outside the grammar."""
    if any(ch in _E1_RAW_FORBIDDEN for ch in line):
        return False
    if re.fullmatch(r"[ -~\t]*", line) is None:
        return False
    hooks = _hooks()
    try:
        segments, complete, in_progress = hooks._lex_command(line, partial=True)
    except (ValueError, RecursionError):
        return False
    if not complete or in_progress:
        return False
    for seg in segments:
        if seg.sep_after not in _E1_LINE_SEPS:
            return False
        argv = seg.argv
        i = 0
        while i < len(argv) and ENV_ASSIGN_RE.match(argv[i]):
            name = argv[i].split("=", 1)[0]
            if name.endswith("+"):
                name = name[:-1]
            if name in _E1_BINDING_VARS:
                return False
            i += 1
        if i >= len(argv):
            continue  # a blank record, or a plain assignment line
        word = argv[i]
        if not _E1_WORD_RE.match(word):
            return False
        flags = _leading_plain(seg.raw or "", argv)
        if i >= len(flags) or not flags[i]:
            return False
        if word in _E1_REFUSED_WORDS:
            return False
        if word == "shopt":
            rest = argv[i + 1:]
            if rest[:1] != ["-s"] or len(rest) < 2:
                # Only the exact documented shape earns admission: `shopt` first, one `-s`, then one
                # or more names. A bare shopt, a bare NAME, a lone -s or a repeated flag is a query
                # or an unmodelled spelling, outside the closed grammar (refused, disclosed).
                return False
            for w in rest[1:]:
                if w not in _E1_SHOPT_INERT:
                    return False
        elif word in _E1_BUILTINS and word not in _E1_SAFE_BUILTINS:
            return False
    return True


def _e1_file_credit(text):
    """True when this file may earn the pipefail exemption at all. The credit is an ALLOWLIST: every
    mention of 'pipefail' must sit on a line that is itself a standalone column-1 enabling set command
    (_e1_enable_line), and EVERY line of the file must be one of a small closed set of forms: blank, a
    comment, the standalone enable, a plain assignment, or a closed-grammar command line
    (_e1_command_line). Any line outside the set withdraws the credit for the whole file, refusing
    toward a finding: the gate credits only what it can read with certainty. RESIDUAL, stated exactly:
    the gate reads only the file, so inherited environment the file does not show (BASH_ENV, an
    exported shell function shadowing a command word, a parent shell's options such as SHELLOPTS or
    BASHOPTS, which can arrive with alias expansion already on) is not modelled; of these only an
    inherited ENABLE could wrongly extend the credit's reach, and an exported function or BASH_ENV
    file can change what any credited line runs at all."""
    for m in re.finditer(r"pipefail", text):
        start = text.rfind("\n", 0, m.start()) + 1
        end = text.find("\n", m.start())
        line = text[start:] if end == -1 else text[start:end]
        if not (line.startswith("set ") or line.startswith("set\t")) or not _e1_enable_line(line):
            return False
    for line in text.split("\n"):
        stripped = line.strip(" \t")
        if stripped == "" or stripped.startswith("#"):
            continue
        if (line.startswith("set ") or line.startswith("set\t")) and _e1_enable_line(line):
            continue
        if not _e1_command_line(line):
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
# quotes paired) or a plain-assignment whole-array save. The whole line must be exactly one of these,
# matched lexically intact: quotes are never removed before matching, so a spelling that only LOOKS
# like an expansion once quotes are dropped ("$"{PIPESTATUS[0]}) never exempts. The index must be
# spelled in CANONICAL decimal (no leading zero): bash reads 010 as octal 8, so a non-canonical
# spelling would credit a different stage's status. A `declare -a` head is NOT in the set: declare is
# a COMMAND word a redefinition could re-bind through spellings no closed word scan provably bounds
# (see the module docstring), while an assignment has no command word, so only the plain assignment
# spellings are credited.
_E2_SCALAR_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*=(")?\$\{PIPESTATUS\[(0|[1-9][0-9]*)\]\}(")?$')
_E2_ARRAY_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*=\("\$\{PIPESTATUS\[@\]\}"\)$')


def _e2_stage_text_heredoc_free(raws):
    """True when no stage of the just-closed pipeline carries '<<' in its raw text. Any '<<' on a
    stage (a heredoc or here-string opener) withdraws the E2 credit outright: a heredoc's body lines
    displace the raw "next line", so the read the shell would run is not the line this reader sees.
    This over-refuses a QUOTED '<<' that is only a stage's data (disclosed): the credit stays
    withheld rather than modelling which '<<' spellings open a body."""
    return not any("<<" in (raw or "") for raw in raws)


def _e2_next_record_adjacent(next_record_off, read_start, read_line):
    """True when the next LEXED record's first non-blank character sits exactly at the raw next
    line's first non-blank character: only then are the raw reading and the lexed reading the same
    statement. A heredoc opening in an EARLIER segment of the pipeline's own line swallows the raw
    next line as data the shell never runs as the read, so the next lexed record starts elsewhere
    and the credit is refused."""
    indent = len(read_line) - len(read_line.lstrip(" \t"))
    return next_record_off == read_start + indent


def _e2_read_line(text, end, sep):
    """The lexically intact line immediately after the pipeline's final line and the absolute offset of
    its first character, or (None, None) when the read cannot be adjacent: anything but whitespace and
    at most the one ';' separator follows the final stage on its own line (a further command, a comment
    after ';', or end of file). `end` is the absolute offset just past the final stage's raw text;
    `sep` is the pipeline's own separator."""
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
        return None, None
    if i >= n:
        return None, None
    j = text.find("\n", i + 1)
    return text[i + 1:j if j != -1 else n], i + 1


def _e2_exempt(top_level, read_line, stage_index):
    """True when the script reads the flagged stage's own exit: the pipeline is a top-level statement
    and the immediately next line (which the caller has already required to be the immediately next
    LEXED record, with no heredoc on the pipeline's lines) is exactly one of the closed read forms,
    NAME=${PIPESTATUS[N]} or NAME="${PIPESTATUS[N]}" with N this stage's index in canonical decimal,
    or NAME=("${PIPESTATUS[@]}"). Only a space or tab may surround
    the read: a carriage return, form feed or no-break space is not shell whitespace, so a line
    carrying one is a different command and earns nothing. Lexical only: the gate does not prove the
    script acts on the value, and every other spelling (a bare unquoted array save, the declare -a
    head, arithmetic, an
    extra word on the line) is not recognized, which refuses toward a finding, never toward a silent
    pass."""
    if not top_level or read_line is None:
        return False
    s = read_line.strip(" \t")
    m = _E2_SCALAR_RE.match(s)
    if m is not None and (m.group(1) or "") == (m.group(3) or ""):
        return m.group(2) == str(stage_index)
    return _E2_ARRAY_RE.match(s) is not None


# The word trap, matched on a DEQUOTED word with the same boundary tolerance as the declared-command
# regex: a quote-split or escaped spelling (tr""ap, t\rap) still resolves to the word, and a longer
# name (bootstrap, trapdoor) does not match.
_E2_TRAP_WORD_RE = re.compile(r"(?<![A-Za-z0-9_.-])trap(?![A-Za-z0-9_./-])")
# A trap condition this reader can resolve: one plain literal name. Anything else (a '$', a quote,
# an escape, punctuation such as RTMIN+1) is not provably a name other than DEBUG.
_E2_TRAP_CONDITION_RE = re.compile(r"[A-Za-z0-9_]+\Z")


def _e2_names_debug(word):
    """True when a trap condition word names bash's DEBUG condition. bash accepts the lowercase
    spelling (observed in bash 5.3: `trap '...' debug` skipped the save exactly as DEBUG did), so
    the comparison is case-insensitive, and one optional SIG prefix is stripped first: stripping
    only ever withdraws more, never less."""
    upper = word.upper()
    if upper.startswith("SIG"):
        upper = upper[3:]
    return upper == "DEBUG"


# Command words that run other text in THIS shell from a file this reader never sees: a sourced
# file executes in the current shell, where it can turn extdebug on or install a DEBUG trap. The
# sh family is NOT here: it runs a child shell, which cannot reach this shell's save.
_E2_SOURCE_WORDS = frozenset(("source", "."))


def _e2_runs_unseen_text(argv, raw):
    """True when this record's resolved command word is source or '.', read from the DEQUOTED word
    (so sour''ce still counts) and by basename (so a path-qualified spelling counts too): the
    sourced file runs in the current shell, where it can turn extdebug on or install a DEBUG trap
    this reader never sees, the same stance as the pipefail grammar's refusal of source and '.'.
    Running a script (./helper.sh) is not sourcing it: a child shell cannot reach this save."""
    _cmd_i, base, _opens, _opaque = _command_word(argv, raw)
    return base in _E2_SOURCE_WORDS


# The members of the pipefail grammar's refused set that the shared-set rule below does NOT judge,
# each covered elsewhere: source and '.' withdraw through _e2_runs_unseen_text above; trap is
# admitted only where _e2_record_trap_safe below PROVES it leaves the DEBUG condition alone (so a
# plainly spelled EXIT or ERR trap keeps the credit); and the set's reserved words stay with the
# command-word resolver, because a PLAIN reserved word is consumed as syntax before any command
# word resolves, a block closer (fi, done, esac) or the list word in runs nothing where it stands,
# and a QUOTED spelling names an ordinary command, which cannot reach this shell's option, alias
# or trap state. Everything else in the shared set withdraws through _e2_refused_command_word.
_E2_SHARED_SET_EXEMPT = frozenset((
    "source", ".", "trap",
    "if", "then", "else", "elif", "fi", "while", "until", "for", "select", "do", "done",
    "case", "esac", "in"))


def _e2_refused_command_word(argv, raw):
    """True when this record's resolved command word is in the pipefail grammar's own refused set
    (_E1_REFUSED_WORDS, the ONE shared roster, so the two grammars cannot drift apart), minus the
    members _E2_SHARED_SET_EXEMPT routes to their own branches. The withdrawal closes the wrapped
    spellings of sourcing a source or trap scan never resolves: builtin source lib.sh and
    builtin . lib.sh resolve to builtin, never to source (bash still sources), eval runs text this
    reader never sees, and alias (with unalias and enable) re-binds what a later word runs; the
    resolution already skips the bare wrappers, so command builtin source resolves to builtin and
    withdraws too. set and function resolve and withdraw with the rest of the set; command, exec
    and time are themselves on the wrapper roster, so the resolution skips them and judges the
    word behind them, and a plain coproc is consumed as a reserved word (a quoted coproc
    spelling resolves here and withdraws).
    Over-refusals, disclosed: the sh family runs a CHILD shell, which cannot reach this shell's
    save, and a plain set line (set -x) reaches no trap, but both are in the shared set and
    withdraw, erring toward withdrawal, never toward keeping the credit."""
    _cmd_i, base, _opens, _opaque = _command_word(argv, raw)
    return base in _E1_REFUSED_WORDS and base not in _E2_SHARED_SET_EXEMPT


# Characters through which a word's run-time spelling can differ from its text: parameter and
# command substitution ('$', '`'), brace expansion ('{') and pathname expansion ('*', '?', '[').
_E2_EXPANSION_CHARS = "$`{*?["


def _e2_command_word_unbounded(argv, raw):
    """True when this record's command word cannot be bounded to a literal name: the resolved
    command word holds an expansion character (a parameter expansion such as ${t}p or $t, a brace
    expansion such as tr{ap,}, or a pathname expansion such as tra[p] can resolve to trap or shopt
    at run time), or no command word resolves at all behind a wrapper's option words (opaque). An
    expansion character in an ARGUMENT word does not withdraw here: an argument never becomes the
    command bash runs, and the credited save line itself holds a '$' (shopt operands are the one
    exception, judged by _e2_shopt_args_unbounded below). A literal command word that merely
    contains such a character withdraws too, an over-refusal, disclosed. Fails toward
    withdrawal, never toward keeping the credit."""
    cmd_i, _base, _opens, opaque = _command_word(argv, raw)
    if opaque:
        return True
    if cmd_i is None:
        return False
    word = argv[cmd_i]
    return any(c in word for c in _E2_EXPANSION_CHARS)


def _e2_shopt_args_unbounded(argv, raw):
    """True when this record's resolved command word is shopt and any later dequoted word holds an
    expansion character: shopt reaches the option that makes bash skip the save, and an option
    operand built at run time (extdeb{ug,}, extdeb[u]g, $o, a substitution, an ANSI-C spelling)
    spells its name in text the extdebug scans never decode, so the record withdraws. A shopt
    whose every word is literal is left to those scans (shopt -s nullglob keeps the credit, and a
    literal extdebug operand already withdraws there); an operand that merely contains such a
    character withdraws even where it expands to an inert name, an over-refusal, disclosed."""
    cmd_i, base, _opens, _opaque = _command_word(argv, raw)
    if base != "shopt":
        return False
    return any(any(c in w for c in _E2_EXPANSION_CHARS) for w in argv[cmd_i + 1:])


def _e2_debug_word(argv):
    """True when any dequoted word of this record names DEBUG on its own (case-insensitive, one
    optional SIG prefix): a visible DEBUG name can become the condition of a trap this reader
    cannot bound (a built command word, a sourced wrapper), so the name withdraws wherever it
    stands, command, argument or operand alike. A file that merely mentions the word keeps no
    credit (an over-refusal, disclosed); an option word such as --debug, or a longer name, is not
    the condition name and keeps it."""
    return any(_e2_names_debug(w) for w in argv)


def _e2_extdebug_word(argv):
    """True when any dequoted word of this record spells extdebug: a quote-split or escaped
    spelling (extdeb\\ug) is invisible to the raw-text scan, but the lexer has already removed
    the quoting here, so the dequoted word withdraws on its own."""
    return any("extdebug" in w for w in argv)


def _e2_record_trap_safe(argv, raw):
    """True when this record, in whose dequoted words the word trap appears, PROVABLY leaves the
    DEBUG condition alone: the record's resolved command word is trap itself, trap is spelled in
    none of its arguments (an ERR or signal action could otherwise install a DEBUG trap when it
    fires), and every condition word is its own plain literal token, one name of
    _E2_TRAP_CONDITION_RE's shape, that does not name DEBUG. The conditions are the words after the
    option words (leading '-' clusters and one '--'), skipping one action word; a SOLE remaining
    word is judged as a condition itself (bash reads single-argument `trap DEBUG` as that
    condition's reset, and this reader cannot tell a lone action from a lone condition, so the
    printing trap -p DEBUG withdraws too, an over-refusal, disclosed), and with
    no word left the command only prints. Everything else fails toward withdrawal, never toward
    keeping the credit: trap reached as another command's argument (command, builtin, eval, plain
    data), a trap behind an opaque wrapper, a condition this reader cannot resolve."""
    cmd_i, base, _opens, opaque = _command_word(argv, raw)
    if opaque or base != "trap":
        return False
    if any(_E2_TRAP_WORD_RE.search(w) for j, w in enumerate(argv) if j != cmd_i):
        return False
    flags = _leading_plain(raw, argv)
    j = cmd_i + 1
    while j < len(argv):
        w = argv[j]
        if w == "--":
            j += 1
            break
        if len(w) >= 2 and w[0] == "-" and w[1] != "-":
            j += 1
            continue
        break
    if j >= len(argv):
        return True
    start = j if len(argv) - j == 1 else j + 1
    for k in range(start, len(argv)):
        w = argv[k]
        plain = flags[k] if k < len(flags) else False
        if not plain or _E2_TRAP_CONDITION_RE.fullmatch(w) is None or _e2_names_debug(w):
            return False
    return True


def _e2_file_credit(text):
    """Whether the E2 PIPESTATUS-read credit can hold anywhere in this file. The credited save is a
    plain assignment, but bash can still SKIP it: with the extdebug shell option on, a DEBUG trap
    whose text returns nonzero makes bash skip the next command (observed in bash 5.3: the save
    line left its variable unset), and either half can arrive invisibly (extdebug through a
    parent's BASHOPTS, the trap through BASH_ENV), so each visible half, and every visible route
    that could carry one unseen, withdraws on its own. No credit in a file whose raw text or
    dequoted words spell extdebug (a comment or string mention withdraws too, a disclosed
    over-refusal), in which any dequoted word on its own names DEBUG or SIGDEBUG case-insensitively
    (_e2_debug_word: a file that merely mentions the word keeps no credit, disclosed), in which a
    record's command word is source or '.' (_e2_runs_unseen_text: the sourced text runs in this
    shell and is never seen here), in which a record's resolved command word is any other
    non-exempt member of the pipefail grammar's refused set (_e2_refused_command_word, reading the
    ONE shared roster: builtin, eval and alias can reach source, shopt or trap under a name no
    other scan resolves; the sh family and a plain set withdraw with the set, over-refusals,
    disclosed), in which a record's command word holds an expansion character ('$', a backquote,
    '{', '*', '?' or '[') or
    stands unresolvable behind a wrapper's option words (_e2_command_word_unbounded: a built
    command word can resolve to trap or shopt; the value words of an array assignment NAME=( ... )
    are data to bash, never a command word, the credited array save's own "${PIPESTATUS[@]}"
    included, so only the word-level checks judge them), in which a shopt record carries an
    expansion character in any later word (_e2_shopt_args_unbounded: a built operand can spell
    extdebug in text the extdebug scans never decode; shopt with only literal words is left to
    those scans), or in which the word trap stands in any
    record's dequoted words except as a trap command _e2_record_trap_safe proves leaves DEBUG
    alone (a printing trap -p DEBUG, and a trap inside a plain ( ) subshell or a command
    substitution, which runs where it cannot reach the save, withdraw here too, disclosed). A
    word standing where this reader expects a command is judged as the command word (a case
    subject after the case keyword reads as one, an over-refusal, disclosed). A comment mentioning
    trap, source or DEBUG lexes to no words and keeps the credit, and the plain assignment save is
    still the credited fix in a file that shows none of these routes. Substitution bodies are
    scanned as their own contexts, so a withdrawing spelling inside $( ) or backquotes withdraws
    too. RESIDUAL, stated exactly: a DEBUG trap or extdebug the file does not show (installed by
    BASH_ENV, inherited through a parent's BASHOPTS, which was observed in bash 5.3 to enable
    debugging only where a debugger start file is installed and to warn and disable it otherwise,
    so treating the route as reachable everywhere errs toward withdrawal, or an exported shell
    function; SHELLOPTS cannot
    carry extdebug, because bash rejects set -o extdebug, observed in bash 5.3) or spelled only in
    text this reader never decodes (an ANSI-C $'...' spelling of the word, or trap action text
    assembled at run time from fragments the file never spells plainly) is not modelled."""
    if "extdebug" in text:
        return False
    hooks = _hooks()
    for ctx in _contexts(text):
        try:
            segments = hooks._lex_command(ctx.text)
        except ValueError:
            segments, _complete, _in_progress = hooks._lex_command(ctx.text, partial=True)
        prev_opens_array = False
        for seg in segments:
            argv = seg.argv
            # The record after a NAME=( or NAME+=( opener holds the VALUE WORDS of an array
            # assignment: data to bash, never a command (the credited array save itself carries
            # "${PIPESTATUS[@]}" there), so only the word-level checks judge it. A subshell's '('
            # (no assignment opener before it) keeps every check: its content is commands.
            is_array_value = prev_opens_array
            prev_opens_array = (seg.sep_after == "(" and bool(argv)
                                and ENV_ASSIGN_RE.match(argv[-1]) is not None
                                and argv[-1].endswith("="))
            if not argv:
                continue
            raw = seg.raw or ""
            if _e2_extdebug_word(argv):
                return False
            if _e2_debug_word(argv):
                return False
            if not is_array_value:
                if _e2_runs_unseen_text(argv, raw):
                    return False
                if _e2_refused_command_word(argv, raw):
                    return False
                if _e2_command_word_unbounded(argv, raw):
                    return False
                if _e2_shopt_args_unbounded(argv, raw):
                    return False
            if any(_E2_TRAP_WORD_RE.search(w) for w in argv) \
                    and not _e2_record_trap_safe(argv, raw):
                return False
    return True


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
    """A QUOTE-TOLERANT word-boundary regex matching any declared command name in raw text, for the
    fail-closed checks over text the gate cannot read as parsed shell (sh -c and eval strings, unparsed
    tails, masked substitution spans, the grouped-producer backstop). Between the name's characters it
    tolerates quote and escape characters, so a quote-split or escaped spelling (to""ol, 'to''ol',
    to\\ol) still establishes the declared identity: failing to prove absence must never become a clean
    result just because the contiguous raw spelling is absent. A preceding '/' is allowed on purpose:
    /usr/local/bin/tool and ./tool are path-qualified spellings of the declared basename, so they must
    match; a preceding letter, digit, '_', '.' or '-' still means a longer, different name and does
    not. The tolerance can also match a longer quoted word ('tool'x), which only ever refuses more,
    never less."""
    alts = "|".join("['\"\\\\]*".join(re.escape(ch) for ch in c) for c in sorted(commands))
    return re.compile(r"(?<![A-Za-z0-9_.-])(?:{})(?![A-Za-z0-9_./-])".format(alts))


def _scan_context(ctx, original_text, rel, binding, telemetry, events, pending, declared_re,
                  e2_credit):
    """Scan one context's segments: record set-pipefail events (crediting an enable only when it
    provably reaches this shell AND its line is the standalone column-1 closed form) and
    command/subcommand telemetry, raise GateError (exit 2) on a declared command inside unparseable
    shell, inside an sh -c / bash -c / dash -c / zsh -c / ksh -c / eval argument (clustered options
    included), in the argument list of xargs, flock or find, beside a coproc, possibly hidden behind
    wrapper option words or an ambiguous subcommand position, inside a subshell, brace group, loop
    body or function body (a one-line `function NAME { ... }` definition included), beside a function
    definition whose body is not a compound form this walk models (in a file that mentions a declared
    command), or beside a grouped pipeline producer this gate cannot attribute, and append a pending
    finding for every
    declared relied-on command in a non-final pipeline stage that E2 does not clear. Declared-command
    identity is read from the shell-DEQUOTED words and from the UNMASKED original text through the
    quote-tolerant declared regex (substitution bodies included), so a quoted, split or escaped
    spelling, or a mention living only inside $( ), cannot slip a refused construct; a declared NAME
    counts even where it is only another command's argument data, a disclosed over-refusal."""
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
        # Whether a function definition was already pending when this segment began: the deferred
        # body must be consumed by THIS segment (a modelled block opener, or a subshell body judged
        # by the subshell rules), or the definition's body is a form this walk does not model.
        fp_entry = func_pending
        lead = len(seg.raw) - len(seg.raw.lstrip()) if seg.raw else 0
        abs_off = ctx.base + offs[idx] + lead
        argv = seg.argv
        raw = seg.raw or ""
        cmd_i, base, opens_before, opaque = _command_word(argv, raw)
        plain_flags = _leading_plain(raw, argv)

        def plain_at(j, w, _flags=plain_flags, _raw=raw):
            # Per-token plainness: a reserved word is syntax only when ITS token is spelled plain,
            # never because the same spelling appears plain elsewhere in the segment ("if" x if).
            # Where the leading walk stopped, the token's identity is unknown and it reads as a
            # command name, never as syntax (_unresolved_token_plain): past a redirection or an
            # undecoded construct, bash itself no longer reads reserved words as syntax.
            if j < len(_flags):
                return _flags[j]
            return _unresolved_token_plain(_raw, w)

        eff_depth = len(blocks) + paren_depth + opens_before
        relied = False
        seg_declared = declared_in_words(argv) or bool(declared_re.search(unmasked_span(idx, seg)))
        if opaque and seg_declared:
            raise GateError("{}:{}: binding {!r}: a declared command may sit behind wrapper option "
                            "words this gate cannot parse (the option may consume the following word); "
                            "cannot evaluate".format(rel, line_of(abs_off), binding["id"]))
        if seg_declared and _executes(seg):
            if _command_start_unresolved(plain_flags, argv, raw):
                raise GateError("{}:{}: binding {!r}: the first token of this command is spelled in "
                                "a form this gate cannot resolve, so a reserved word cannot be told "
                                "from a command name; cannot evaluate".format(
                                    rel, line_of(abs_off), binding["id"]))
            # A declared command is judged only as a top-level simple command at the head of a
            # pipeline stage (if/then/else and case arms are still walked). Inside a subshell, brace
            # group, loop or function body its execution time or shell is not modelled: refuse.
            construct = None
            if argv[:1] == ["function"] and plain_at(0, "function"):
                # A one-line `function NAME { ... }` keeps the body's words in THIS segment, before
                # the block stack can see the opener: a declared command here already sits inside the
                # function body.
                construct = REFUSED_BODIES["func"]
            elif paren_depth > 0:
                construct = "a subshell"
            else:
                for kind in reversed(blocks):
                    if kind in REFUSED_BODIES:
                        construct = REFUSED_BODIES[kind]
                        break
            if construct is None:
                prefix = argv[:cmd_i] if cmd_i is not None else argv
                for j, w in enumerate(prefix):
                    if w in REFUSED_BODIES and plain_at(j, w):
                        construct = REFUSED_BODIES[w]
                        break
            if construct is not None:
                raise GateError("{}:{}: binding {!r}: a declared command inside {} is not a top-level "
                                "simple command this gate can attribute; cannot evaluate".format(
                                    rel, line_of(abs_off), binding["id"], construct))
            if cmd_i is not None and any(w == "coproc" and plain_at(j, w)
                                         for j, w in enumerate(argv[:cmd_i])):
                raise GateError("{}:{}: binding {!r}: a declared command beside a coproc runs in a "
                                "coprocess shell this gate does not model; cannot evaluate".format(
                                    rel, line_of(abs_off), binding["id"]))
        if base is not None:
            if base == "eval" or (base in ("sh", "bash", "dash", "zsh", "ksh") and any(
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
                if top_level and e2_credit and seg.sep_after in ("", ";"):
                    # E2 needs the pipeline to end at a newline or one ';' (never '&', '&&' or '||'):
                    # only then can the immediately next line be the adjacent PIPESTATUS read. The
                    # whole-file credit must also hold: a visible extdebug spelling or a trap not
                    # provably leaving DEBUG alone means the save itself can be skipped.
                    read_line, read_start = _e2_read_line(original_text,
                                                          ctx.base + offs[idx] + len(raw),
                                                          seg.sep_after)
                if read_line is not None:
                    # Two independent layers guard the read's identity (each pinned by its own
                    # fixture and mutant): no '<<' on any stage's own text, and the raw next line
                    # must ALSO be the next LEXED record, starting exactly that line.
                    if not _e2_stage_text_heredoc_free(
                            [kept[si].raw for si, _o2, _r2, _b2, _d2, _q2 in run_stages]):
                        read_line = None
                    else:
                        nidx = idx + 1
                        while (nidx < len(kept) and not kept[nidx].argv
                                and not kept[nidx].redirects and not (kept[nidx].raw or "").strip()):
                            # a truly empty record (the lexer's artifact after ';'): nothing in the
                            # source stands at its offset, so it is not the next lexed record.
                            nidx += 1
                        if nidx < len(kept):
                            nraw = kept[nidx].raw or ""
                            nlead = len(nraw) - len(nraw.lstrip())
                            if not _e2_next_record_adjacent(ctx.base + offs[nidx] + nlead,
                                                            read_start, read_line):
                                read_line = None
                        else:
                            read_line = None
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
        # pending body: a BLOCK OPENER in the next executing segment is the deferred FUNCTION body
        # (f() { ... }, f() if ...; fi, function f { ... }), a '(' is a subshell body consumed and
        # judged by the subshell rules, and EVERY other form is refused or consumed at the boundary
        # (the fp_entry check below), so the pending mark never reaches a later unrelated block. A
        # reserved word spelled quoted or escaped is a command name in bash (judged per token), so it
        # neither opens nor closes a block here.
        words = argv
        word_base = 0
        if argv[:1] == ["function"] and plain_at(0, "function"):
            func_pending = True
            words = argv[2:]
            word_base = 2
        # The NAME '( )' form is recognized AFTER this segment's own walk: its pending mark belongs to
        # the body that FOLLOWS the definition, never to an opener inside this same record (an `if (`
        # head is a subshell after a walked `if`, not a function named if; a reserved word cannot name
        # a function).
        name_paren_def = (len(argv) == 1 and seg.sep_after == "(" and not seg.redirects
                          and argv[0] not in RESERVED_SKIP
                          and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", argv[0] or "") is not None
                          and plain_at(0, argv[0]))
        for j, w in enumerate(words, start=word_base):
            if w in BLOCK_OPEN and plain_at(j, w):
                blocks.append("func" if func_pending else w)
                func_pending = False
                if w == "case":
                    case_depth += 1
            elif w in BLOCK_CLOSE and plain_at(j, w):
                if blocks:
                    blocks.pop()
                if w == "esac":
                    case_depth = max(0, case_depth - 1)
            elif (w in RESERVED_SKIP and plain_at(j, w)) or ENV_ASSIGN_RE.match(w):
                continue
            else:
                break
        if name_paren_def:
            func_pending = True
        if fp_entry and func_pending:
            # The deferred body this segment was expected to open is not one of the modelled forms.
            # A '(' here opens a SUBSHELL body: consume the mark (the subshell rules above judge its
            # contents, and the mark must never leak onto a later unrelated block). Any EXECUTING
            # segment that did not open a modelled block means the body is a compound form this walk
            # does not model (a conditional command, an arithmetic command, a malformed body): in a
            # file that mentions a declared command, refuse rather than judge around it (a disclosed
            # over-refusal; with no declared mention there is nothing to attribute, so the mark is
            # consumed and the walk continues).
            if _executes(seg):
                if ctx_declared:
                    raise GateError("{}:{}: binding {!r}: a function definition's body here is not a "
                                    "compound form this gate models, so a declared command's "
                                    "execution cannot be attributed; cannot evaluate".format(
                                        rel, line_of(abs_off), binding["id"]))
                func_pending = False
            elif seg.sep_after == "(":
                func_pending = False
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
        e2_credit = _e2_file_credit(text)
        for ctx in _contexts(text):
            _scan_context(ctx, text, rel, binding, telemetry, events, pending, declared_re,
                          e2_credit)
    except RecursionError:
        raise GateError("configuration {} binding {!r}: surface {} exceeds the scanner's nesting bounds; "
                        "cannot evaluate".format(CONFIG_REL, binding["id"], rel))
    if events and not _e1_file_credit(text):
        # The file holds a pipefail mention outside the standalone column-1 enable form, or a line
        # outside the closed credit grammar (anything but a blank, a comment, the standalone enable, a
        # plain assignment, or a plain simple-command line): the whole file loses the pipefail credit.
        # Disables stay recorded; they only ever refuse, and they hold this gate's verdict even under
        # a regression of the whole-file credit (two independent layers).
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
# Every refuse case (R1..R125 with R24 retired, plus Rb1) runs against the real gate (exit 1) AND
# against a mutant that never reports (exit 0), so each case is shown to discriminate; targeted
# mutants additionally pin the mid-pipeline stage index, the closed-form PIPESTATUS reader (its
# canonical-decimal index, its refusal of the declare -a head, the stage-text heredoc layer and the
# next-record adjacency
# layer, each alone), the whole-file pipefail credit and its line grammar, the whole-file
# PIPESTATUS-read credit (re-admitted whole, its trap reader trusted and distrusted alone, with
# the extdebug route shown independent of the trap route, and its source, shared-refused-set,
# unbounded-command-word, shopt-operand,
# DEBUG-word and dequoted-extdebug branches each stubbed alone; the two-layer fixtures R106,
# R110/R111, R92/R93/R101/R102 and R75/R84 stand under either of their layers' mutants alone and
# fall only
# under both together), the enable line's option
# vocabulary, the unresolved-token rule, the command-start guard, the pre-token continuation skip
# (layered with that guard), the ionice wrapper entry, and one roster member each for the builtin
# (readarray) and binding-variable (BASH_CMDS, BASH_ENV, POSIXLY_CORRECT) rosters, whose every
# member is also pinned by a literal-name unit loop; the
# quoted-disable fixture R78 is held by TWO layers and is re-run under each layer's mutant alone
# (still refusing) and under both (then passing), while R79's off-event layer is shown under the
# forced-credit mutant alone (the dropped-off-events mutant wraps only set's option reader, which
# R79's shopt path never consults). Every control (C1..C25 with C12/C14/C15/C17/C19/C21 retired and
# C26/C27 converted to the
# refusals R66/R67, plus C3a-C3c, C23b and C28..C38) runs against the real gate (exit 0) and names
# the over-broad implementation it catches; where that implementation is a patchable predicate, the
# over-broad mutant is also run and must reach the named wrong exit. Every cannot-evaluate case
# X1..X15 must exit 2 AND print the diagnostic of its OWN check (asserted by a substring no other
# check prints: the grouped-producer backstop's wording shares no needle with the construct
# refusals), so a removed guard cannot hide behind another refusal reaching the same exit code (such
# as unmatched-selector reconciliation or that backstop); the for/until loop entries and the find
# executor entry are additionally pinned by roster mutants, and scan_file's pre-open layer is called
# directly. A case that cannot run (no tempdir, no symlink or FIFO support, permission checks
# bypassed) is a NAMED failure, never a skip.

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
# A declared command NAMED like a reserved word: bash runs the quoted spelling as that command, so the
# gate must read the quoted token as the command word, never as syntax.
CFG_CMD_IF = ('format-version = 1\n\n[[binding]]\nid = "row-writer"\npaths = ["cmd/job.sh"]\n'
              'commands = ["if"]\n')


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
                                         (["ionice", "tool", "write"], "tool", False),
                                         (["timeout", "30", "tool", "write"], "tool", False),
                                         (["if", "tool", "write"], "tool", False),
                                         (["A=1"], None, False),
                                         # ANY option word after a wrapper (separate, '='-attached, or
                                         # '--') makes the command word opaque: whether the following
                                         # words are values or the command cannot be read from text.
                                         (["nice", "-n", "10", "tool", "write"], None, True),
                                         (["ionice", "-c", "2", "tool", "write"], None, True),
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
    # Plainness is judged PER TOKEN: a later plain argument of the same spelling must not make a
    # quoted command word read as syntax ("if" x if runs the command named if), and a plain later
    # argument must not make a quoted first token refuse as a loop opener.
    _i, base, _o, _q = _command_word(["if", "x", "if"], '"if" x if')
    if base != "if":
        failures.append("_command_word on a quoted leading 'if' with a plain 'if' argument expected "
                        "the command word 'if', got {!r}".format(base))
    # A leading redirection stops the per-token walk; the tokens past it have UNKNOWN identity and
    # read as command words, never resolved through another plain same-spelled word in the segment.
    _i, base, _o, _q = _command_word(["if", "x", "if"], '>/dev/null "if" x if')
    if base != "if":
        failures.append("_command_word behind a leading redirection must read the quoted 'if' as the "
                        "command word, never as syntax through the later plain argument, got "
                        "{!r}".format(base))
    for raw, argv, want in (("tool write", ["tool", "write"], [True, True]),
                            ('"if" x if', ["if", "x", "if"], [False, True, True]),
                            ('"select" tool write select', ["select", "tool", "write", "select"],
                             [False, True, True, True]),
                            ("to\\ol write", ["tool", "write"], [False, True]),
                            ("'to''ol' write", ["tool", "write"], [False, True]),
                            ("{ x=1", ["{", "x=1"], [True, True]),
                            ("tool > f write", ["tool", "write"], [True]),
                            ("echo 'a b' x", ["echo", "a b", "x"], [True, False, True]),
                            # bash removes a backslash-newline BEFORE word splitting: the continued
                            # token is still one plain word (a continued keyword stays the keyword),
                            # while any other escape keeps its token non-plain.
                            (" \\\nuntil tool write", ["until", "tool", "write"],
                             [True, True, True]),
                            # the removal holds BETWEEN tokens too: indentation or a further
                            # continuation after a backslash-newline is only spacing, so the
                            # indented continued keyword keeps its identity.
                            (" \\\n  while :", ["while", ":"], [True, True]),
                            ("\t\\\n\tuntil tool write", ["until", "tool", "write"],
                             [True, True, True]),
                            (" \\\n \\\n  until tool", ["until", "tool"], [True, True]),
                            ("tru\\\ne x", ["true", "x"], [True, True]),
                            # bash removes a backslash-newline inside DOUBLE quotes too: the
                            # continued quoted token decodes to the lexer's one word ('tool'),
                            # aligning the walk, and the token stays non-plain.
                            ('"to\\\nol" write', ["tool", "write"], [False, True])):
        got = _leading_plain(raw, argv)
        if got != want:
            failures.append("_leading_plain({!r}, {!r}) expected {!r}, got {!r}".format(
                raw, argv, want, got))
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
    # of exactly the flagged index in CANONICAL decimal (bare or double-quoted, quotes paired), or a
    # quoted whole-array save spelled as a plain assignment. Everything else (the declare -a head,
    # which puts a redefinable COMMAND word before the save, another index, a
    # leading-zero index bash reads as octal, a bare array save, extra words, a literal or commented
    # mention, a quote-mangled spelling, non-shell whitespace such as CR, FF or NBSP around the read)
    # earns nothing. The index-10/010 pair tests the boundary from both sides.
    for line, k, want in (("rc=${PIPESTATUS[0]}", 0, True),
                          ('rc="${PIPESTATUS[0]}"', 0, True),
                          ('st=("${PIPESTATUS[@]}")', 0, True),
                          ('declare -a st=("${PIPESTATUS[@]}")', 0, False),
                          ("\trc=${PIPESTATUS[0]}", 0, True),
                          ("rc=${PIPESTATUS[1]}", 0, False),
                          ("rc=${PIPESTATUS[0]}", 1, False),
                          ("rc=${PIPESTATUS[10]}", 10, True),
                          ("rc=${PIPESTATUS[010]}", 8, False),
                          ("rc=${PIPESTATUS[010]}", 10, False),
                          ("rc=${PIPESTATUS[00]}", 0, False),
                          ("all=(${PIPESTATUS[@]})", 0, False),
                          ('rc=("${PIPESTATUS[0]}")', 0, False),
                          ('rc="${PIPESTATUS[0]}', 0, False),
                          ("rc=${PIPESTATUS[0]} || exit 1", 0, False),
                          ("\rrc=${PIPESTATUS[0]}", 0, False),
                          ("rc=${PIPESTATUS[0]}\r", 0, False),
                          ("\x0crc=${PIPESTATUS[0]}", 0, False),
                          (' st=("${PIPESTATUS[@]}")', 0, False),
                          ("echo '${PIPESTATUS[0]}'", 0, False),
                          ("echo ok # ${PIPESTATUS[0]}", 0, False),
                          ("echo \\${PIPESTATUS[0]}", 0, False),
                          ("printf '%s' 'PIPESTATUS[0]'", 0, False)):
        if _e2_exempt(True, line, k) != want:
            failures.append("_e2_exempt(True, {!r}, {}) expected {!r}".format(line, k, want))
    if _e2_exempt(False, "rc=${PIPESTATUS[0]}", 0):
        failures.append("_e2_exempt must never exempt a non-top-level pipeline")
    # The whole-file read-credit withdrawal's trap reader: a record holding the word trap keeps the
    # credit only as a trap command whose every condition is a plain literal name other than DEBUG.
    # Each boundary is tested from both sides: the DEBUG name against its case, SIG-prefixed and
    # listed-beside-EXIT spellings, a plain condition against its quoted and variable spellings, a
    # sole word as condition against the no-word print forms, '--' against option clusters, and the
    # trap command itself against trap as another command's argument or inside a trap's own action.
    for argv, raw, want in ((["trap", "rm -f lock", "EXIT"], "trap 'rm -f lock' EXIT", True),
                            (["trap", "echo err", "ERR"], "trap 'echo err' ERR", True),
                            (["trap", "cleanup", "INT", "TERM"], "trap cleanup INT TERM", True),
                            (["trap", "x", "DEBUG"], "trap 'x' DEBUG", False),
                            (["trap", "x", "debug"], "trap 'x' debug", False),
                            (["trap", "x", "SIGDEBUG"], "trap 'x' SIGDEBUG", False),
                            (["trap", "x", "EXIT", "DEBUG"], "trap 'x' EXIT DEBUG", False),
                            (["trap", "x", "$sig"], "trap 'x' $sig", False),
                            (["trap", "x", "EXIT"], "trap 'x' \"EXIT\"", False),
                            (["trap", "-", "DEBUG"], "trap - DEBUG", False),
                            (["trap", "DEBUG"], "trap DEBUG", False),
                            (["trap", "EXIT"], "trap EXIT", True),
                            (["trap"], "trap", True),
                            (["trap", "-p"], "trap -p", True),
                            (["trap", "--", "x", "DEBUG"], "trap -- 'x' DEBUG", False),
                            (["trap", "--", "x", "EXIT"], "trap -- 'x' EXIT", True),
                            (["trap", "trap x DEBUG", "ERR"], "trap 'trap x DEBUG' ERR", False),
                            (["echo", "trap"], "echo trap", False),
                            (["eval", "trap x DEBUG"], "eval 'trap x DEBUG'", False)):
        if _e2_record_trap_safe(argv, raw) != want:
            failures.append("_e2_record_trap_safe({!r}, {!r}) expected {!r}".format(argv, raw, want))
    # The word regex matches the dequoted spellings and leaves longer names alone.
    for word, want in (("trap", True), ("/usr/bin/trap", True), ("trap x DEBUG", True),
                       ("T=trap x DEBUG", True), ("bootstrap", False), ("trapdoor", False)):
        if bool(_E2_TRAP_WORD_RE.search(word)) != want:
            failures.append("_E2_TRAP_WORD_RE on {!r} expected {!r}".format(word, want))
    # The whole-file withdrawal's unseen-text branch, each boundary from both sides: the source and
    # dot command words (quote-split, path-qualified and wrapper-fronted spellings included) against
    # a dotted path, source as plain argument data, and an assignment.
    for argv, raw, want in ((["source", "lib.sh"], "source lib.sh", True),
                            (["source", "lib.sh"], "sour''ce lib.sh", True),
                            ([".", "./lib.sh"], ". ./lib.sh", True),
                            (["/usr/bin/source", "lib.sh"], "/usr/bin/source lib.sh", True),
                            (["command", "source", "lib.sh"], "command source lib.sh", True),
                            (["./helper.sh"], "./helper.sh", False),
                            (["echo", "source"], "echo source", False),
                            (["src=."], "src=.", False)):
        if _e2_runs_unseen_text(argv, raw) != want:
            failures.append("_e2_runs_unseen_text({!r}, {!r}) expected {!r}".format(argv, raw,
                                                                                    want))
    # The unbounded-command-word branch: an expansion character ('$', backquote, brace or glob) in
    # the COMMAND word, or a wrapper's option words hiding it, withdraws; the same characters in an
    # argument, or an assignment-only record (the credited save itself), keep the credit, and a
    # command word of ordinary punctuation (./helper.sh) is still a literal name.
    for argv, raw, want in ((["${t}p", "x", "ERR"], "${t}p 'x' ERR", True),
                            (["$t", "x", "ERR"], '"$t" \'x\' ERR', True),
                            (["`get`", "x"], "`get` x", True),
                            (["tr{ap,}", "x", "ERR"], "tr{ap,} 'x' ERR", True),
                            (["tra[p]", "x", "ERR"], "tra[p] 'x' ERR", True),
                            (["tra?", "x"], "tra? x", True),
                            (["tr*", "x"], "tr* x", True),
                            (["command", "-p", "ls"], "command -p ls", True),
                            (["tool", "write", "--row", "7"], "tool write --row 7", False),
                            (["./helper.sh"], "./helper.sh", False),
                            (["rc=${PIPESTATUS[0]}"], "rc=${PIPESTATUS[0]}", False),
                            (["echo", "x{y,z}"], "echo x{y,z}", False),
                            (["echo", "$x"], "echo $x", False)):
        if _e2_command_word_unbounded(argv, raw) != want:
            failures.append("_e2_command_word_unbounded({!r}, {!r}) expected {!r}".format(
                argv, raw, want))
    # The shared refused-set branch: a resolved command word from the pipefail grammar's roster
    # withdraws, behind the bare wrappers too; the exempt members (source, '.', trap, the reserved
    # words), an ordinary command, an assignment-only record and the refused name as mere argument
    # data keep this branch quiet (their own branches judge them).
    for argv, raw, want in ((["builtin", "source", "lib.sh"], "builtin source lib.sh", True),
                            (["builtin", ".", "lib.sh"], "builtin . lib.sh", True),
                            (["command", "builtin", "source", "l"], "command builtin source l",
                             True),
                            (["eval", "source lib.sh"], "eval 'source lib.sh'", True),
                            (["alias", "s=source"], "alias s=source", True),
                            (["eval", "x"], "ev\"\"al x", True),
                            (["unalias", "-a"], "unalias -a", True),
                            (["enable", "-n", "trap"], "enable -n trap", True),
                            (["set", "-x"], "set -x", True),
                            (["sh", "lib.sh"], "sh lib.sh", True),
                            (["source", "lib.sh"], "source lib.sh", False),
                            ([".", "lib.sh"], ". lib.sh", False),
                            (["trap", "x", "EXIT"], "trap 'x' EXIT", False),
                            (["fi"], "fi", False),
                            (["tool", "write"], "tool write", False),
                            (["echo", "eval"], "echo eval", False),
                            (["e=eval"], "e=eval", False)):
        if _e2_refused_command_word(argv, raw) != want:
            failures.append("_e2_refused_command_word({!r}, {!r}) expected {!r}".format(
                argv, raw, want))
    # The shopt-operand branch: an expansion character in any word after a resolved shopt command
    # word withdraws; literal operands, and the same characters under another command word, keep
    # this branch quiet.
    for argv, raw, want in ((["shopt", "-s", "extdeb{ug,}"], "shopt -s extdeb{ug,}", True),
                            (["shopt", "-s", "extdeb[u]g"], "shopt -s extdeb[u]g", True),
                            (["shopt", "-s", "$o"], "shopt -s $o", True),
                            (["shopt", "-s", "nullglob"], "shopt -s nullglob", False),
                            (["shopt", "-s", "expand_aliases"], "shopt -s expand_aliases", False),
                            (["echo", "x{y,z}"], "echo x{y,z}", False),
                            (["rc=${PIPESTATUS[0]}"], "rc=${PIPESTATUS[0]}", False)):
        if _e2_shopt_args_unbounded(argv, raw) != want:
            failures.append("_e2_shopt_args_unbounded({!r}, {!r}) expected {!r}".format(
                argv, raw, want))
    # The standing-DEBUG-word branch: the name on its own, in any case and with one SIG prefix,
    # withdraws; an option word, a longer name and an assignment spelling do not.
    for argv, want in ((["echo", "DEBUG"], True),
                       (["echo", "Debug"], True),
                       (["run", "sigdebug"], True),
                       (["tool", "write", "--debug"], False),
                       (["echo", "DEBUGGER"], False),
                       (["mode=DEBUG"], False),
                       ([], False)):
        if _e2_debug_word(argv) != want:
            failures.append("_e2_debug_word({!r}) expected {!r}".format(argv, want))
    # The dequoted extdebug branch: the lexer has removed the quoting, so the dequoted word is
    # judged on its own (a longer word containing the spelling withdraws too, fail-closed).
    for argv, want in ((["shopt", "-s", "extdebug"], True),
                       (["echo", "extdebugger"], True),
                       (["shopt", "-s", "extglob"], False),
                       ([], False)):
        if _e2_extdebug_word(argv) != want:
            failures.append("_e2_extdebug_word({!r}) expected {!r}".format(argv, want))
    # E1's closed form: the line is exactly a standalone enabling set command, spelled entirely from
    # the validated option-letter and option-name vocabularies, ending at the newline or one ';'. Each
    # boundary is tested just inside, at, and just outside it; an unknown letter or name makes bash
    # reject the set command and run on with pipefail OFF, so it must earn nothing.
    for line, want in (("set -o pipefail", True),
                       ("set -euo pipefail", True),
                       ("set -Eeo pipefail", True),
                       ("set -e -o pipefail", True),
                       ("set -o errexit -o pipefail", True),
                       ("set -o pipefail;", True),
                       ("set -o pipefail ;", True),
                       ("set\t-o pipefail", True),
                       # posix is in bash's -o vocabulary but turns ALIAS EXPANSION on, state the
                       # closed grammar does not model: an enable that also enables it earns nothing,
                       # whichever side of pipefail it sits on.
                       ("set -o posix -o pipefail", False),
                       ("set -o pipefail -o posix", False),
                       ("set -e", False),
                       ("set +o pipefail", False),
                       ("set -Zo pipefail", False),
                       ("set -o nonexistent -o pipefail", False),
                       ("set -o pipefai", False),
                       ("set -o pipefail\r", False),
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
    # The whole-file pipefail credit is an ALLOWLIST over every line: blank, comment, the standalone
    # enable, a plain assignment, or a plain simple-command line (no refused command word read from the
    # DEQUOTED spelling, no '$', backquote, escape, parenthesis, brace, redirection or trailing
    # comment), plus the rule that every raw pipefail mention sits on an enable line. Any line outside
    # the set withdraws the credit for the whole file.
    for text, want in (("set -o pipefail\ntool write | tail -1\n", True),
                       ("set -euo pipefail\n", True),
                       ("echo ok\n", True),
                       ("A=1 tool write | tail -1\nset -o pipefail\n", True),
                       ("opt=x\n# a comment\n\nset -o pipefail\ntool write |& tee x.log\n", True),
                       ("set -o pipefail\nshopt -s nullglob\n", True),
                       # a '.' that is an ARGUMENT is data, not the dot command: only the dequoted
                       # COMMAND word withdraws, so these ordinary lines keep the credit.
                       ("set -o pipefail\nfind . -name x\n", True),
                       ("set -o pipefail\nls . | wc -l\n", True),
                       ("  set -o pipefail\n", False),
                       ("set -o pipefail && true\n", False),
                       ("set -Zo pipefail\n", False),
                       ("set -o nonexistent -o pipefail\n", False),
                       ("set -o pipefail\nset +o pipefail\n", False),
                       ("set -o pipefail\nset -- +o pipefail\n", False),
                       ("set -o pipefail\n# pipefail notes\n", False),
                       ("opt=pipefail\n", False),
                       ("set -o pipefail\nf() { :; }\n", False),
                       ("set -o pipefail\nfunction f { :; }\n", False),
                       ("set -o pipefail\neval ':'\n", False),
                       ("set -o pipefail\nev\"\"al ':'\n", False),
                       ("set -o pipefail\ne\\val ':'\n", False),
                       ("set -o pipefail\nsource lib.sh\n", False),
                       ("set -o pipefail\nsour''ce lib.sh\n", False),
                       ("set -o pipefail\n. lib.sh\n", False),
                       ('set -o pipefail\n"." lib.sh\n', False),
                       ("set -o pipefail\nbash -c ':'\n", False),
                       ("set -o pipefail\ndash -ec ':'\n", False),
                       ("set -o pipefail\nksh ok.sh\n", False),
                       ("set -o pipefail\nbuiltin :\n", False),
                       ('set -o pipefail\n"set" +o pipe""fail\n', False),
                       ("set -o pipefail\nset +x\n", False),
                       ("set -o pipefail\nshopt -u extglob\n", False),
                       ('set -o pipefail\nshopt "-u" -o pipe""fail\n', False),
                       # shopt is admitted only as -s with names from the inert roster: expand_aliases
                       # and lastpipe reach alias expansion and where a stage runs, so they withdraw.
                       ("set -o pipefail\nshopt -s nullglob dotglob\n", True),
                       ("set -o pipefail\nshopt -s expand_aliases\nnp\n", False),
                       ("set -o pipefail\nshopt -s lastpipe\n", False),
                       ("set -o pipefail\nshopt -s nullglob lastpipe\n", False),
                       ("set -o pipefail\nshopt +s nullglob\n", False),
                       ("set -o pipefail\nshopt -su nullglob\n", False),
                       # only the exact documented shape `shopt -s NAME...` is admitted: a bare
                       # shopt, a bare NAME, a lone -s and a repeated -s are query or unmodelled
                       # spellings, outside the closed grammar.
                       ("set -o pipefail\nshopt\n", False),
                       ("set -o pipefail\nshopt nullglob\n", False),
                       ("set -o pipefail\nshopt -s\n", False),
                       ("set -o pipefail\nshopt -s -s nullglob\n", False),
                       # a builtin outside the modelled allowlist can write options, aliases,
                       # bindings or variables, or run callback text this reader never parses.
                       ("set -o pipefail\nprintf -v x 1\n", False),
                       ("set -o pipefail\nmapfile -t a\n", False),
                       ("set -o pipefail\nread -r x\n", False),
                       ("set -o pipefail\ndeclare -i n=1\n", False),
                       ("set -o pipefail\necho ok\ntool write | tail -1\n", True),
                       # an assignment to a binding-view variable, and a QUOTED command word, are
                       # both outside the grammar (every command word must be spelled plain).
                       ("set -o pipefail\nBASH_ALIASES=x\n", False),
                       ("set -o pipefail\nPOSIXLY_CORRECT=1\n", False),
                       ('set -o pipefail\n"tool" write | tail -1\n', False),
                       ("set -o posix -o pipefail\ntool write | tail -1\n", False),
                       ("set -o pipefail\nalias np=x\n", False),
                       ("set -o pipefail\nunalias -a\n", False),
                       ("set -o pipefail\nenable -n set\n", False),
                       ("set -o pipefail\ntrap 'x' DEBUG\n", False),
                       ("set -o pipefail\nexec 3>&1\n", False),
                       ("set -o pipefail\ncommand -p x\n", False),
                       ("set -o pipefail\ntime tool write\n", False),
                       ("set -o pipefail\nif true; then :; fi\n", False),
                       ("set -o pipefail\ncase x in\nesac\n", False),
                       ("set -o pipefail\ntool write > log\n", False),
                       ('set -o pipefail\ntool write "$f" | tail -1\n', False),
                       ("set -o pipefail\nrow=`tool write`\n", False),
                       ("set -o pipefail\ntool write | tail -1 # note\n", False),
                       ("set -o pipefail\ntool write \\\n | tail -1\n", False),
                       ("set -o pipefail\nset -o $opt\n", False)):
        if _e1_file_credit(text) != want:
            failures.append("_e1_file_credit({!r}) expected {!r}".format(text, want))
    # Every deny-side name is checked by LITERAL spelling, so these loops test the gate's effective
    # refusal of each name rather than a roster's own (possibly mutated) contents. They assert
    # REFUSAL COVERAGE, not roster membership: a member another rule also holds (a refused word such
    # as set or eval, or a shape the command-word rule rejects, such as '.', ':' or '[') is still
    # refused with its roster entry removed, so for those names the loop holds whichever layer
    # remains and does not isolate the entry; only the names no sibling rule covers are individually
    # pinned here (the end-to-end roster mutants above isolate one member per roster).
    for name in (".", ":", "[", "alias", "bg", "bind", "break", "builtin", "caller", "cd",
                 "command", "compgen", "complete", "compopt", "continue", "declare", "dirs",
                 "disown", "enable", "eval", "exec", "exit", "export", "fc", "fg", "getopts",
                 "hash", "help", "history", "jobs", "kill", "let", "local", "logout", "mapfile",
                 "popd", "printf", "pushd", "read", "readarray", "readonly", "return", "set",
                 "shift", "shopt", "source", "suspend", "trap", "typeset", "ulimit", "umask",
                 "unalias", "unset", "wait"):
        if _e1_command_line(name + " x"):
            failures.append("_e1_command_line must refuse the unmodelled builtin {!r}".format(name))
    for name in ("BASH_ALIASES", "BASH_CMDS", "BASH_ENV", "SHELLOPTS", "BASHOPTS",
                 "POSIXLY_CORRECT"):
        if _e1_command_line(name + "=x"):
            failures.append("_e1_command_line must refuse an assignment to {!r}".format(name))
        if _e1_command_line(name + "=x true"):
            failures.append("_e1_command_line must refuse the {!r} assignment prefix".format(name))
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
            # --- refuse cases R1..R125 (exit 1; R24 retired), each re-run against never-report ---
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
                # --- the closed-grammar pipefail credit: invalid enable vocabulary ---
                ("R54", CFG, "set -Zo pipefail\ntool write | tail -1\n",
                 "an unknown set option letter makes bash reject the line and run with pipefail OFF; "
                 "the validated vocabulary earns it no credit"),
                ("R55", CFG, "set -o nonexistent -o pipefail\ntool write | tail -1\n",
                 "an unknown set -o option name makes bash reject the whole command; no credit"),
                # --- the closed-grammar pipefail credit: quoted and indirect option changers ---
                ("R56", CFG, "set -o pipefail\nsour''ce ./lib.sh\ntool write | tail -1\n",
                 "a quote-split source is still source by its dequoted word; the line is outside the "
                 "closed grammar and withdraws the whole-file credit"),
                ("R57", CFG, 'set -o pipefail\n"." ./lib.sh\ntool write | tail -1\n',
                 "a quoted dot command is still the dot command; outside the closed grammar"),
                ("R58", CFG, "set -o pipefail\nopt=pi''pefail\n\"set\" +o \"$opt\"\n"
                             "tool write | tail -1\n",
                 "a quoted set with a quote-split variable-driven operand withdraws the credit (the "
                 "dequoted command word is set, and the '$' is outside the grammar)"),
                ("R59", CFG, "enable -n set\nset -o pipefail\ntool write | tail -1\n",
                 "enable can unbind the set builtin so the later enable line never enables; enable is "
                 "outside the closed grammar"),
                ("R60", CFG, "shopt -s expand_aliases\nalias set=true\nset -o pipefail\n"
                             "tool write | tail -1\n",
                 "an alias can make the enable line run something else; alias is outside the closed "
                 "grammar"),
                ("R61", CFG, "set -o pipefail\ntrap 'x' DEBUG\ntool write | tail -1\n",
                 "a trap can run option-changing text between any two lines; trap is outside the "
                 "closed grammar"),
                ("R62", CFG, "set -o pipefail\nset `echo x` pipe\"\"fail\ntool write | tail -1\n",
                 "a set line outside the standalone enable form (a command-substitution operand, a "
                 "quote-split pipefail) withdraws the credit"),
                ("R63", CFG, "set -o pipefail\nev\"\"al ':'\ntool write | tail -1\n",
                 "a quote-split eval is still eval by its dequoted word; outside the closed grammar"),
                ("R64", CFG, "set -o pipefail\ne\\val ':'\ntool write | tail -1\n",
                 "an escaped eval is still eval by its dequoted word; outside the closed grammar"),
                ("R65", CFG, "set -o pipefail\nbuil''tin se''t +o pipe''fail\ntool write | tail -1\n",
                 "a quote-split builtin reaching a quote-split disable withdraws the credit by its "
                 "dequoted command word"),
                ("R66", CFG, "case x in\n  a) : ;;\nesac\nset -o pipefail\ntool write | tail -1\n",
                 "control flow (case) is outside the closed credit grammar, so the file earns no "
                 "credit (a disclosed over-refusal; formerly the control for case-depth tracking)"),
                ("R67", CFG, "select v in a b; do : ; done\nset -o pipefail\n"
                             "tool write | tail -1\n",
                 "control flow (select) is outside the closed credit grammar (a disclosed "
                 "over-refusal; formerly the control for select-depth tracking)"),
                ("R68", CFG, "set -o pipefail\necho hi > log.txt\ntool write | tail -1\n",
                 "a redirection is outside the closed credit grammar (a disclosed over-refusal)"),
                # --- the PIPESTATUS read: heredocs, canonical index, odd whitespace, reader identity ---
                ("R69", CFG, "tool write | cat <<'EOF'\nrc=${PIPESTATUS[0]}\nEOF\necho ok\n",
                 "a heredoc opening on the pipeline's line makes the raw next line heredoc DATA the "
                 "shell never runs; the lexed next record disagrees and the credit is refused"),
                ("R70", CFG, "tool write <<'EOF' | head\nrc=${PIPESTATUS[0]}\nEOF\necho ok\n",
                 "a quoted heredoc on the producer's own stage swallows the would-be read line"),
                ("R71", CFG, "tool write | cat <<'EOF'\nst=(\"${PIPESTATUS[@]}\")\nEOF\necho ok\n",
                 "a whole-array save inside a heredoc body is data, never a read"),
                ("R72", CFG, "true | true | true | true | true | true | true | true | true | true | "
                             "tool write | cat\nrc=${PIPESTATUS[010]}\n",
                 "bash reads 010 as OCTAL index 8; only the canonical decimal spelling of the flagged "
                 "stage's index (10) is credited"),
                ("R73", CFG, "tool write | tail -1\n st=(\"${PIPESTATUS[@]}\")\n",
                 "a no-break space is not shell whitespace: the read line is a different command"),
                ("R74", CFG, "tool write | tail -1\n\rst=(\"${PIPESTATUS[@]}\")\n",
                 "a carriage return is not shell whitespace the read may hide behind"),
                ("R75", CFG, "shopt -s expand_aliases\nalias declare=\"true; declare\"\n"
                             "tool write | tail -1\ndeclare -a st=(\"${PIPESTATUS[@]}\")\n",
                 "an aliased declare runs an intervening command that overwrites PIPESTATUS before "
                 "the save; the declare -a read form is outside the closed read set, so this file "
                 "is flagged"),
                # --- wrappers and quoted command words ---
                ("R76", CFG, "ionice tool write | tail -1\ntool write --row 9\n",
                 "ionice is a bare-transparent wrapper: the declared producer behind it still loses "
                 "its exit to the pipe"),
                ("R77", CFG_CMD_IF, '"if"\n"if" x if | tail -1\n',
                 "a QUOTED leading 'if' is the declared command, judged per token: the plain 'if' "
                 "argument later in the segment must not turn it back into syntax"),
                # --- quoted disables: held by TWO independent layers (see the layered mutants below) ---
                ("R78", CFG, 'set -o pipefail\n"set" +o pipe""fail\ntool write | tail -1\n',
                 "a quoted set with a quote-split pipefail disables the option: the credit grammar "
                 "withdraws on the dequoted command word AND the dequoted off-event cancels E1"),
                ("R79", CFG, 'set -o pipefail\nshopt "-u" -o pipe""fail\ntool write | tail -1\n',
                 "a quoted-flag shopt disable: the credit grammar withdraws on the dequoted 'o'/'u' "
                 "flags AND the dequoted shopt off-event cancels E1"),
                # --- the E2 read-identity layers, each pinned on its own (see the targeted mutants) ---
                ("R80", CFG, "tool write --row 9\ncat <<'EOF'; tool write | tail -1\n"
                             "rc=${PIPESTATUS[0]}\nEOF\necho ok\n",
                 "a heredoc opening in an EARLIER segment of the pipeline's line swallows the raw "
                 "next line as data; only the next-record adjacency check closes this shape, so it "
                 "is pinned on its own"),
                ("R81", CFG, 'tool write "<<" | tail -1\nrc=${PIPESTATUS[0]}\ntool write --row 9\n',
                 "a quoted '<<' that is only a stage's data still withdraws the E2 credit (the "
                 "disclosed over-refusal), pinning the stage-text heredoc layer on its own"),
                # --- backslash-newline removal and per-token identity ---
                ("R82", CFG, "tool write --row 9\nmake build && \\\nif tool write | grep -q ok; "
                             "then echo ok; fi\n",
                 "a backslash-newline before 'if' is removed before reading, so the keyword still "
                 "opens the condition and the pipeline inside it is flagged"),
                ("R87", CFG_CMD_IF, '"if"\n>/dev/null "if" x if | true\n',
                 "a leading redirection stops the per-token walk; the quoted 'if' behind it is still "
                 "the command word, never resolved through the later plain same-spelled argument"),
                # --- credit withheld wherever a line can change shell state the grammar does not model ---
                ("R83", CFG, "shopt -s expand_aliases\nprintf -v 'BASH_ALIASES[set]' true\n"
                             "set -o pipefail\ntool write | tail -1\n",
                 "a visible alias-table write can make the enable line run something else: printf "
                 "and the expand_aliases shopt are outside the closed grammar and withdraw the "
                 "whole-file credit"),
                ("R84", CFG, "set -o posix\nprintf -v 'BASH_ALIASES[declare]' 'true; declare'\n"
                             "tool write | tail -1\ndeclare -a st=(\"${PIPESTATUS[@]}\")\n",
                 "posix mode plus a BASH_ALIASES write can redefine what a declare word runs; the "
                 "declare -a read form earns no credit, so this file is flagged"),
                ("R85", CFG, "set -o pipefail\nshopt -s lastpipe\n"
                             "echo a | mapfile -C 'set +o pipe''fail' -c 1 arr\n"
                             "tool write | tail -1\n",
                 "mapfile -C runs its callback text in a shell this reader never parses: mapfile and "
                 "the lastpipe shopt are outside the closed grammar and withdraw the credit"),
                ("R86", CFG, "shopt -s lastpipe\necho a | "
                             "mapfile -C 'shopt -s expand_aliases; alias declare=\"true; declare\"'"
                             " -c 1 arr\ntool write | tail -1\ndeclare -a st=(\"${PIPESTATUS[@]}\")\n",
                 "a redefinition living only inside a callback string can re-bind a declare word; "
                 "the declare -a read form earns no credit, so this file is flagged"),
                ("R88", CFG, 'set -o pipefail\n"tool" write | tail -1\ntool write --row 9\n',
                 "a QUOTED command word is outside the closed grammar (every command word must be "
                 "spelled plain), so the file earns no pipefail credit"),
                # --- protected roster members pinned end to end (see the roster mutants) ---
                ("R89", CFG, "set -o pipefail\nreadarray -t a\ntool write | tail -1\n",
                 "readarray can run -C callback text and write variables this reader never sees: "
                 "its roster entry alone withdraws the whole-file credit"),
                ("R90", CFG, "set -o pipefail\nBASH_CMDS=x\ntool write | tail -1\n",
                 "an assignment to BASH_CMDS re-binds what a later hashed word runs: its roster "
                 "entry alone withdraws the credit"),
                ("R91", CFG, "set -o pipefail\nBASH_ENV=x\ntool write | tail -1\n",
                 "an assignment to BASH_ENV plants startup text for child shells: its roster entry "
                 "alone withdraws the credit"),
                ("R99", CFG, "set -o pipefail\nPOSIXLY_CORRECT=1\ntool write | tail -1\n",
                 "assigning POSIXLY_CORRECT turns posix mode, and with it alias expansion, on: the "
                 "enable line's refusal of the posix name must not be reachable through the "
                 "assignment spelling"),
                # --- declare -a reads beside redefinitions no literal word scan provably bounds ---
                ("R92", CFG, "e=eval\n$e 'shopt -s expand_aliases; alias declare=\"true; "
                             "declare\"'\ntool write | tail -1\n"
                             'declare -a st=("${PIPESTATUS[@]}")\n',
                 "a command word held in a variable can run an alias-table redefinition of declare "
                 "without spelling a refused word; the declare -a read form earns no credit, so "
                 "this file is flagged"),
                ("R93", CFG, "s=shopt\no=expand_aliases\n$s -s $o\np=printf\nn=BASH_ALIASES\n"
                             "$p -v \"$n[declare]\" 'true; declare'\ntool write | tail -1\n"
                             'declare -a st=("${PIPESTATUS[@]}")\n',
                 "a printf -v alias-table rebinding reached entirely through variables spells no "
                 "refused word; the declare -a read form earns no credit, so this file is flagged"),
                ("R94", CFG, "n=BASH_ALIASES\ntool write | tail -1\n"
                             'declare -a st=("${PIPESTATUS[@]}")\n',
                 "a binding-table name carried mid-word (n=BASH_ALIASES) can feed a later indirect "
                 "redefinition; the declare -a read form earns no credit, so this file is flagged"),
                # --- continued keywords behind indentation (the pre-token continuation removal) ---
                ("R95", CFG, "tool write --row 9\nmake build && \\\n    if tool write | grep -q "
                             "ok; then echo ok; fi\n",
                 "a continuation followed by SPACES still begins the same command, so the indented "
                 "'if' keeps opening its condition and the pipeline inside it is flagged"),
                ("R96", CFG, "tool write --row 9\nmake build && \\\n\tif tool write | grep -q ok; "
                             "then echo ok; fi\n",
                 "a continuation followed by a TAB still begins the same command"),
                ("R97", CFG, "tool write --row 9\nmake build && \\\n \\\n if tool write | grep -q "
                             "ok; then echo ok; fi\n",
                 "repeated continuations with indentation still begin the same command"),
                ("R98", CFG, "tool write --row 9\nmake build && \\\n    ! tool write | grep -q ok\n",
                 "an indented continued '!' still keeps the command position open, so the pipeline "
                 "behind it is read and flagged"),
                # --- the declare -a head is outside the closed read set in EVERY file ---
                ("R100", CFG, 'tool write | tail -1\ndeclare -a st=("${PIPESTATUS[@]}")\n',
                 "the declare -a whole-array save earns no credit even with nothing suspicious in "
                 "the file: declare is a command word a redefinition this gate cannot bound could "
                 "re-bind (a disclosed over-refusal; the plain assignment save, C20, keeps the "
                 "credit)"),
                ("R101", CFG, "e=eval\nif true; then $e 'shopt -s expand_aliases; alias declare="
                              "\"true; declare\"'; fi\ntool write | tail -1\n"
                              'declare -a st=("${PIPESTATUS[@]}")\n',
                 "a variable command word standing behind a plain reserved word can run a "
                 "redefinition of declare that a leading-word scan never reaches; the declare -a "
                 "read form earns no credit, so this file is flagged"),
                ("R102", CFG, "e=eval\ntime $e 'shopt -s expand_aliases; alias declare=\"true; "
                              "declare\"'\ntool write | tail -1\n"
                              'declare -a st=("${PIPESTATUS[@]}")\n',
                 "a variable command word standing behind the time keyword can run a redefinition "
                 "of declare that a leading-word scan never reaches; the declare -a read form "
                 "earns no credit, so this file is flagged"),
                # --- the whole-file read-credit withdrawal: extdebug and DEBUG traps skip the save ---
                ("R103", CFG, "shopt -s extdebug\ntrap '[[ $BASH_COMMAND != rc=* ]]' DEBUG\n"
                              "tool write | tail -1\nrc=${PIPESTATUS[0]}\n",
                 "with extdebug on, a DEBUG trap returning nonzero makes bash skip the scalar save "
                 "(observed: the variable stays unset), so the read earns no credit"),
                ("R104", CFG, "shopt -s extdebug\ntrap '[[ $BASH_COMMAND != st=* ]]' DEBUG\n"
                              "tool write | tail -1\nst=(\"${PIPESTATUS[@]}\")\n",
                 "with extdebug on, a DEBUG trap returning nonzero makes bash skip the whole-array "
                 "save too, so the read earns no credit"),
                ("R105", CFG, "shopt -s extdebug\ntool write | tail -1\nrc=${PIPESTATUS[0]}\n",
                 "the extdebug spelling alone withdraws the read credit: the matching DEBUG trap "
                 "can arrive invisibly (BASH_ENV), so the visible half fails closed"),
                ("R106", CFG, "trap ':' DEBUG\ntool write | tail -1\nrc=${PIPESTATUS[0]}\n",
                 "a DEBUG trap alone withdraws the read credit: extdebug can arrive invisibly (a "
                 "parent's BASHOPTS), so the visible half fails closed"),
                # --- routes into the read credit that carry either half without spelling it ---
                ("R107", CFG, "source ./lib.sh\ntool write | tail -1\nrc=${PIPESTATUS[0]}\n",
                 "a sourced file runs in this shell, where it can turn extdebug on or install a "
                 "DEBUG trap this reader never sees, so a source command word withdraws the read "
                 "credit"),
                ("R108", CFG, ". ./lib.sh\ntool write | tail -1\nrc=${PIPESTATUS[0]}\n",
                 "the dot command is source under another name and withdraws the read credit the "
                 "same way"),
                ("R109", CFG, "source /dev/stdin <<'EOF'\ntrap 'false' DEBUG\nEOF\n"
                              "tool write | tail -1\nrc=${PIPESTATUS[0]}\n",
                 "sourcing /dev/stdin runs the heredoc body in this shell; the body is heredoc "
                 "DATA to the lexer, so only the source command word itself can withdraw here"),
                ("R110", CFG, "t=tra\n${t}p 'false' DEBUG\ntool write | tail -1\n"
                              "rc=${PIPESTATUS[0]}\n",
                 "a trap command word built from a parameter expansion installs a DEBUG trap no "
                 "trap-word scan sees; the '$'-holding command word and the standing DEBUG word "
                 "each withdraw the read credit"),
                ("R111", CFG, "printf -v t %s%s tr ap\n\"$t\" 'false' DEBUG\n"
                              "tool write | tail -1\nrc=${PIPESTATUS[0]}\n",
                 "a trap command word assembled by printf -v and run through a variable installs "
                 "a DEBUG trap no trap-word scan sees; the variable command word and the standing "
                 "DEBUG word each withdraw the read credit"),
                ("R112", CFG, "t=tra\n${t}p ':' ERR\ntool write | tail -1\nrc=${PIPESTATUS[0]}\n",
                 "a variable-built trap command word with no DEBUG word in sight still withdraws: "
                 "the command word alone is unbounded, and an ERR action can install a DEBUG trap "
                 "when it fires"),
                ("R113", CFG, "echo DEBUG\ntool write | tail -1\nrc=${PIPESTATUS[0]}\n",
                 "a word naming DEBUG withdraws wherever it stands (the disclosed over-refusal "
                 "for a file that merely mentions the word): this reader cannot bound where a "
                 "visible DEBUG name ends up"),
                ("R114", CFG, "shopt -s extdeb\\ug\ntool write | tail -1\nrc=${PIPESTATUS[0]}\n",
                 "an escaped extdebug spelling is invisible to the raw-text scan but dequotes to "
                 "the option name, so the dequoted-word check alone withdraws the credit"),
                # --- the shared refused set: wrapped sourcing no source scan resolves ---
                ("R115", CFG, "builtin source ./lib.sh\ntool write | tail -1\n"
                              "rc=${PIPESTATUS[0]}\n",
                 "builtin source still sources in this shell but resolves to the command word "
                 "builtin, never to source; the shared refused set withdraws the read credit"),
                ("R116", CFG, "builtin . ./lib.sh\ntool write | tail -1\nrc=${PIPESTATUS[0]}\n",
                 "builtin with the dot command sources the same way under the builtin command "
                 "word; the shared refused set withdraws the read credit"),
                ("R117", CFG, "command builtin source ./lib.sh\ntool write | tail -1\n"
                              "rc=${PIPESTATUS[0]}\n",
                 "a command wrapper before builtin source is skipped as a bare wrapper, so the "
                 "record still resolves to builtin and the shared refused set withdraws"),
                ("R118", CFG, "eval 'source ./lib.sh'\ntool write | tail -1\nrc=${PIPESTATUS[0]}\n",
                 "an eval can run a source this reader never sees as a command word; eval is in "
                 "the shared refused set and withdraws the read credit"),
                ("R119", CFG, "shopt -s expand_aliases\nalias s=source\ns ./lib.sh\n"
                              "tool write | tail -1\nrc=${PIPESTATUS[0]}\n",
                 "an alias of source lets a later unremarkable word source text this reader "
                 "never sees; alias is in the shared refused set and withdraws the read credit"),
                ("R120", CFG, "s() { builtin source ./lib.sh; }\ns\ntool write | tail -1\n"
                              "rc=${PIPESTATUS[0]}\n",
                 "a function body wrapping builtin source is scanned record by record, so the "
                 "body's builtin command word withdraws the read credit through the shared "
                 "refused set"),
                # --- command and shopt words built by brace or pathname expansion ---
                ("R121", CFG, "tr{ap,} false DEB{UG,}\ntool write | tail -1\n"
                              "rc=${PIPESTATUS[0]}\n",
                 "a brace-built command word (tr{ap,} runs trap) installs a DEBUG trap no trap "
                 "scan sees, and the brace-built condition spells no DEBUG word; the expansion "
                 "character in the command word alone withdraws the read credit"),
                ("R122", CFG, "tra[p] false DEB[U]G\ntool write | tail -1\nrc=${PIPESTATUS[0]}\n",
                 "a glob-built command word (tra[p] can resolve to trap through a matching file "
                 "name) is not bounded to a literal name; the expansion character in the command "
                 "word alone withdraws the read credit"),
                ("R123", CFG, "sho{pt,} -s extdeb{ug,}\ntool write | tail -1\n"
                              "rc=${PIPESTATUS[0]}\n",
                 "a brace-built shopt command word turns extdebug on while spelling neither "
                 "shopt nor extdebug; the expansion character in the command word alone "
                 "withdraws the read credit"),
                ("R124", CFG, "shop[t] -s extdeb[u]g\ntool write | tail -1\nrc=${PIPESTATUS[0]}\n",
                 "a glob-built shopt command word is not bounded to a literal name; the "
                 "expansion character in the command word alone withdraws the read credit"),
                ("R125", CFG, "shopt -s extdeb{ug,}\ntool write | tail -1\nrc=${PIPESTATUS[0]}\n",
                 "a plainly spelled shopt with a brace-built operand turns extdebug on while "
                 "spelling extdebug in no raw text or dequoted word; the expansion character in "
                 "the shopt operand alone withdraws the read credit"),
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
            def _octal_blind(top, line, k):
                if not top or line is None:
                    return False
                m = re.search(r"PIPESTATUS\[(\d+)\]", line.strip(" \t"))
                return m is not None and int(m.group(1)) == k

            # The read set that re-admits the declare -a head (the withdrawn spelling): under it,
            # every declare-form fixture must flip CLEAN, proving each is held by the head's
            # exclusion from _E2_ARRAY_RE and by nothing else.
            _declare_admitting_re = re.compile(
                r'^(?:declare -a )?[A-Za-z_][A-Za-z0-9_]*=\("\$\{PIPESTATUS\[@\]\}"\)$')

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
                ("R54", "_e1_enable_line",
                 lambda line: line.strip(" \t").split()[:1] == ["set"],
                 "an enable reader blind to the option vocabulary must wrongly credit the unknown "
                 "letter"),
                ("R55", "_e1_enable_line",
                 lambda line: line.strip(" \t").split()[:1] == ["set"],
                 "an enable reader blind to the -o name vocabulary must wrongly credit the unknown "
                 "name"),
                ("R72", "_e2_exempt", _octal_blind,
                 "an index reader that converts 010 as decimal must wrongly credit the octal "
                 "spelling"),
                ("R76", "WRAPPERS", frozenset(w for w in WRAPPERS if w != "ionice"),
                 "without the wrapper entry the producer behind ionice is never resolved"),
                ("R80", "_e2_next_record_adjacent",
                 lambda next_record_off, read_start, read_line: True,
                 "an adjacency-blind reader must wrongly credit the heredoc-displaced read"),
                ("R81", "_e2_stage_text_heredoc_free", lambda raws: True,
                 "a reader blind to '<<' on the pipeline's own stages must wrongly credit the read"),
                ("R83", "_e1_command_line", lambda line: True,
                 "a grammar admitting every command line must wrongly credit the file holding the "
                 "alias-table write"),
                ("R85", "_e1_command_line", lambda line: True,
                 "a grammar admitting every command line must wrongly credit the mapfile-callback "
                 "file"),
                ("R88", "_e1_command_line", lambda line: True,
                 "a grammar blind to command-token plainness must wrongly credit the quoted command "
                 "word"),
                ("R86", "_E2_ARRAY_RE", _declare_admitting_re,
                 "a read set re-admitting the declare -a head must wrongly credit the read beside "
                 "callback text"),
                ("R87", "_unresolved_token_plain", _plain_word,
                 "resolving an unknown token's plainness through another same-spelled plain word "
                 "must wrongly read the quoted 'if' as syntax"),
                ("R94", "_E2_ARRAY_RE", _declare_admitting_re,
                 "a read set re-admitting the declare -a head must wrongly credit the read beside "
                 "the mid-word table name"),
                ("R100", "_E2_ARRAY_RE", _declare_admitting_re,
                 "a read set re-admitting the declare -a head must wrongly credit the bare declare "
                 "save"),
                ("R103", "_e2_file_credit", lambda text: True,
                 "re-admitting the whole-file read credit must wrongly credit the scalar save "
                 "beside extdebug and the DEBUG trap"),
                ("R104", "_e2_file_credit", lambda text: True,
                 "re-admitting the whole-file read credit must wrongly credit the array save "
                 "beside extdebug and the DEBUG trap"),
                ("R105", "_e2_file_credit", lambda text: True,
                 "re-admitting the whole-file read credit must wrongly credit the read beside the "
                 "bare extdebug spelling"),
                ("R106", "_e2_file_credit", lambda text: True,
                 "re-admitting the whole-file read credit must wrongly credit the read beside the "
                 "DEBUG trap"),
                ("R107", "_e2_runs_unseen_text", lambda argv, raw: False,
                 "a credit blind to the source command word must wrongly credit the read beside "
                 "the sourced helper"),
                ("R108", "_e2_runs_unseen_text", lambda argv, raw: False,
                 "a credit blind to the dot command word must wrongly credit the read beside the "
                 "dot-sourced helper"),
                ("R109", "_e2_runs_unseen_text", lambda argv, raw: False,
                 "a credit blind to source must wrongly credit the read beside the heredoc-fed "
                 "source, whose DEBUG trap text is heredoc data no other branch reads"),
                ("R110", "_e2_file_credit", lambda text: True,
                 "re-admitting the whole-file read credit must wrongly credit the read beside the "
                 "variable-built trap command word"),
                ("R111", "_e2_file_credit", lambda text: True,
                 "re-admitting the whole-file read credit must wrongly credit the read beside the "
                 "printf-assembled trap command word"),
                ("R112", "_e2_command_word_unbounded", lambda argv, raw: False,
                 "a credit blind to unbounded command words must wrongly credit the read beside "
                 "the variable-built ERR trap"),
                ("R113", "_e2_debug_word", lambda argv: False,
                 "a credit blind to a standing DEBUG word must wrongly credit the read beside the "
                 "mere mention"),
                ("R114", "_e2_extdebug_word", lambda argv: False,
                 "a credit blind to the dequoted extdebug spelling must wrongly credit the read "
                 "beside the escaped spelling"),
                ("R115", "_e2_refused_command_word", lambda argv, raw: False,
                 "a credit blind to the shared refused set must wrongly credit the read beside "
                 "builtin source"),
                ("R116", "_e2_refused_command_word", lambda argv, raw: False,
                 "a credit blind to the shared refused set must wrongly credit the read beside "
                 "builtin with the dot command"),
                ("R117", "_e2_refused_command_word", lambda argv, raw: False,
                 "a credit blind to the shared refused set must wrongly credit the read beside "
                 "the command-wrapped builtin source"),
                ("R118", "_e2_refused_command_word", lambda argv, raw: False,
                 "a credit blind to the shared refused set must wrongly credit the read beside "
                 "the eval that sources"),
                ("R119", "_e2_refused_command_word", lambda argv, raw: False,
                 "a credit blind to the shared refused set must wrongly credit the read beside "
                 "the alias of source"),
                ("R120", "_e2_refused_command_word", lambda argv, raw: False,
                 "a credit blind to the shared refused set must wrongly credit the read beside "
                 "the function body wrapping builtin source"),
                ("R121", "_e2_command_word_unbounded", lambda argv, raw: False,
                 "a credit blind to unbounded command words must wrongly credit the read beside "
                 "the brace-built trap word"),
                ("R122", "_e2_command_word_unbounded", lambda argv, raw: False,
                 "a credit blind to unbounded command words must wrongly credit the read beside "
                 "the glob-built trap word"),
                ("R123", "_e2_command_word_unbounded", lambda argv, raw: False,
                 "a credit blind to unbounded command words must wrongly credit the read beside "
                 "the brace-built shopt word"),
                ("R124", "_e2_command_word_unbounded", lambda argv, raw: False,
                 "a credit blind to unbounded command words must wrongly credit the read beside "
                 "the glob-built shopt word"),
                ("R125", "_e2_shopt_args_unbounded", lambda argv, raw: False,
                 "a credit blind to built shopt operands must wrongly credit the read beside the "
                 "brace-built extdebug operand"),
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

            # The two withdrawal routes are independent layers: a trap reader trusting every trap
            # must NOT re-admit the bare-extdebug file, whose refusal rests on the raw spelling
            # alone (the trap-only file R106 stands under that same trust through its standing
            # DEBUG word, shown by the two-layer checks just below).
            real_trap_safe = this._e2_record_trap_safe
            this._e2_record_trap_safe = lambda argv, raw: True
            try:
                rc, _text = _run_quiet(roots["R105"])
            finally:
                this._e2_record_trap_safe = real_trap_safe
            if rc != 1:
                failures.append("R105 under the trap-trusting mutant: expected exit 1 (the extdebug "
                                "spelling withdraws on its own), got {}".format(rc))

            # R106's plainly spelled DEBUG trap is held by TWO independent layers: the trap reader
            # and the standing DEBUG word. Each layer's mutant alone must leave the refusal
            # standing; only stubbing both together flips the file clean.
            real_unbounded = this._e2_command_word_unbounded
            real_debug_word = this._e2_debug_word
            this._e2_record_trap_safe = lambda argv, raw: True
            try:
                rc, _text = _run_quiet(roots["R106"])
            finally:
                this._e2_record_trap_safe = real_trap_safe
            if rc != 1:
                failures.append("R106 under the trap-trusting mutant alone: expected exit 1 (the "
                                "DEBUG word alone must still withdraw), got {}".format(rc))
            this._e2_debug_word = lambda argv: False
            try:
                rc, _text = _run_quiet(roots["R106"])
            finally:
                this._e2_debug_word = real_debug_word
            if rc != 1:
                failures.append("R106 under the DEBUG-word mutant alone: expected exit 1 (the "
                                "trap reader alone must still withdraw), got {}".format(rc))
            this._e2_record_trap_safe = lambda argv, raw: True
            this._e2_debug_word = lambda argv: False
            try:
                rc, _text = _run_quiet(roots["R106"])
            finally:
                this._e2_record_trap_safe = real_trap_safe
                this._e2_debug_word = real_debug_word
            if rc != 0:
                failures.append("R106 under BOTH the trap-trusting and DEBUG-word mutants: "
                                "expected exit 0 (the fixture is held by exactly those two "
                                "layers), got {}".format(rc))

            # R110 and R111 are each held by TWO independent withdrawal layers: the unbounded
            # command word and the standing DEBUG word. Each layer's mutant alone must leave the
            # refusal standing; only stubbing both together flips the file clean.
            for name in ("R110", "R111"):
                this._e2_command_word_unbounded = lambda argv, raw: False
                try:
                    rc, _text = _run_quiet(roots[name])
                finally:
                    this._e2_command_word_unbounded = real_unbounded
                if rc != 1:
                    failures.append("{} under the unbounded-command mutant alone: expected exit 1 "
                                    "(the DEBUG word alone must still withdraw), got {}".format(
                                        name, rc))
                this._e2_debug_word = lambda argv: False
                try:
                    rc, _text = _run_quiet(roots[name])
                finally:
                    this._e2_debug_word = real_debug_word
                if rc != 1:
                    failures.append("{} under the DEBUG-word mutant alone: expected exit 1 (the "
                                    "unbounded command word alone must still withdraw), got "
                                    "{}".format(name, rc))
                this._e2_command_word_unbounded = lambda argv, raw: False
                this._e2_debug_word = lambda argv: False
                try:
                    rc, _text = _run_quiet(roots[name])
                finally:
                    this._e2_command_word_unbounded = real_unbounded
                    this._e2_debug_word = real_debug_word
                if rc != 0:
                    failures.append("{} under BOTH withdrawal mutants together: expected exit 0 "
                                    "(the fixture is held by exactly those two layers), got "
                                    "{}".format(name, rc))

            # The variable-command-word declare fixtures are likewise held by TWO independent
            # layers: the declare -a head's exclusion from the closed read set, and the whole-file
            # withdrawal for a command word held in a variable. Each mutant alone must leave the
            # refusal standing; only weakening both together flips the file clean.
            real_array_re = this._E2_ARRAY_RE
            for name in ("R92", "R93", "R101", "R102"):
                this._E2_ARRAY_RE = _declare_admitting_re
                try:
                    rc, _text = _run_quiet(roots[name])
                finally:
                    this._E2_ARRAY_RE = real_array_re
                if rc != 1:
                    failures.append("{} under the declare-admitting read set alone: expected exit "
                                    "1 (the unbounded command word alone must still withdraw), got "
                                    "{}".format(name, rc))
                this._e2_command_word_unbounded = lambda argv, raw: False
                try:
                    rc, _text = _run_quiet(roots[name])
                finally:
                    this._e2_command_word_unbounded = real_unbounded
                if rc != 1:
                    failures.append("{} under the unbounded-command mutant alone: expected exit 1 "
                                    "(the declare -a head must still earn no credit), got "
                                    "{}".format(name, rc))
                this._E2_ARRAY_RE = _declare_admitting_re
                this._e2_command_word_unbounded = lambda argv, raw: False
                try:
                    rc, _text = _run_quiet(roots[name])
                finally:
                    this._E2_ARRAY_RE = real_array_re
                    this._e2_command_word_unbounded = real_unbounded
                if rc != 0:
                    failures.append("{} under BOTH the declare-admitting and unbounded-command "
                                    "mutants: expected exit 0 (the fixture is held by exactly "
                                    "those two layers), got {}".format(name, rc))

            # R75 and R84 are each held by TWO independent layers: the declare -a head's exclusion
            # from the closed read set, and the shared refused set (R75's alias command word,
            # R84's set command word). Each mutant alone must leave the refusal standing; only
            # weakening both together flips the file clean.
            real_refused = this._e2_refused_command_word
            for name in ("R75", "R84"):
                this._E2_ARRAY_RE = _declare_admitting_re
                try:
                    rc, _text = _run_quiet(roots[name])
                finally:
                    this._E2_ARRAY_RE = real_array_re
                if rc != 1:
                    failures.append("{} under the declare-admitting read set alone: expected exit "
                                    "1 (the refused command word alone must still withdraw), got "
                                    "{}".format(name, rc))
                this._e2_refused_command_word = lambda argv, raw: False
                try:
                    rc, _text = _run_quiet(roots[name])
                finally:
                    this._e2_refused_command_word = real_refused
                if rc != 1:
                    failures.append("{} under the refused-set mutant alone: expected exit 1 (the "
                                    "declare -a head must still earn no credit), got {}".format(
                                        name, rc))
                this._E2_ARRAY_RE = _declare_admitting_re
                this._e2_refused_command_word = lambda argv, raw: False
                try:
                    rc, _text = _run_quiet(roots[name])
                finally:
                    this._E2_ARRAY_RE = real_array_re
                    this._e2_refused_command_word = real_refused
                if rc != 0:
                    failures.append("{} under BOTH the declare-admitting and refused-set mutants: "
                                    "expected exit 0 (the fixture is held by exactly those two "
                                    "layers), got {}".format(name, rc))

            # Roster-member mutants: dropping one protected name must flip its OWN fixture clean,
            # proving each fixture pins exactly that member (a sibling rule cannot mask its removal).
            real_builtins = this._E1_BUILTINS
            this._E1_BUILTINS = frozenset(w for w in real_builtins if w != "readarray")
            try:
                rc, _text = _run_quiet(roots["R89"])
            finally:
                this._E1_BUILTINS = real_builtins
            if rc != 0:
                failures.append("R89 under the dropped-'readarray' _E1_BUILTINS mutant: expected "
                                "exit 0, got {} (the case does not pin that member)".format(rc))
            real_binding_vars = this._E1_BINDING_VARS
            for mut_name, dropped in (("R90", "BASH_CMDS"), ("R91", "BASH_ENV"),
                                      ("R99", "POSIXLY_CORRECT")):
                this._E1_BINDING_VARS = frozenset(w for w in real_binding_vars if w != dropped)
                try:
                    rc, _text = _run_quiet(roots[mut_name])
                finally:
                    this._E1_BINDING_VARS = real_binding_vars
                if rc != 0:
                    failures.append("{} under the dropped-'{}' _E1_BINDING_VARS mutant: expected "
                                    "exit 0, got {} (the case does not pin that member)".format(
                                        mut_name, dropped, rc))

            # The indented-continuation fixtures are held by TWO independent layers: the pre-token
            # continuation removal (the keyword stays syntax, a finding) and the command-start
            # guard (an unresolvable first token refuses). The skip mutant alone must leave the
            # guard's refusal standing, and only disabling both lets the file pass.
            def _blank_only_skip(raw, i, n):
                while i < n and raw[i] in " \t\n":
                    i += 1
                return i

            real_skip = this._skip_blank_and_continuations
            real_start_guard = this._command_start_unresolved
            this._skip_blank_and_continuations = _blank_only_skip
            try:
                rc, text = _run_quiet(roots["R95"])
                if rc != 2 or "cannot be told from a command name" not in text:
                    failures.append("R95 under the continuation-blind skip mutant: expected the "
                                    "command-start guard's exit 2, got {} (the fixture does not "
                                    "exercise the skip)".format(rc))
                this._command_start_unresolved = lambda flags, argv, raw: False
                rc, _text = _run_quiet(roots["R95"])
                if rc != 0:
                    failures.append("R95 under BOTH the skip and command-start mutants: expected "
                                    "exit 0, got {} (the fixture is held by exactly those two "
                                    "layers)".format(rc))
            finally:
                this._skip_blank_and_continuations = real_skip
                this._command_start_unresolved = real_start_guard

            # Layered discrimination for the quoted disables: R78 is held by TWO independent guards,
            # the closed-grammar credit withdrawal and the dequoted off-event. Each mutant alone must
            # leave the refusal standing, and only disabling both flips it: a silent regression of
            # either layer is caught here by the other layer's assertion. The one-at-a-time and
            # both-together experiments are run for R78 ALONE: the dropped-off-events mutant wraps
            # only set's option reader, which R79's shopt path never consults, so R79 is shown by the
            # forced-credit run above (its off-event layer alone still refuses); R79's grammar layer
            # is pinned by the _e1_file_credit unit over the same quoted-flag shopt spelling (at the
            # real gate both layers are active, so R79's own refusal there isolates neither).
            real_credit = this._e1_file_credit
            real_effect = this._set_pipefail_effect

            def _on_only(argv, _real=real_effect):
                return "on" if _real(argv) == "on" else None

            this._e1_file_credit = lambda text: True
            try:
                for name, why in (("R78", "the dequoted set off-event alone must still cancel E1"),
                                  ("R79", "the dequoted shopt off-event alone must still cancel E1")):
                    rc, _text = _run_quiet(roots[name])
                    if rc != 1:
                        failures.append("{} under the forced-credit mutant: expected exit 1 ({}), "
                                        "got {}".format(name, why, rc))
            finally:
                this._e1_file_credit = real_credit
            this._set_pipefail_effect = _on_only
            try:
                rc, _text = _run_quiet(roots["R78"])
                if rc != 1:
                    failures.append("R78 under the dropped-off-events mutant: expected exit 1 (the "
                                    "credit withdrawal alone must still refuse), got {}".format(rc))
            finally:
                this._set_pipefail_effect = real_effect
            this._e1_file_credit = lambda text: True
            this._set_pipefail_effect = _on_only
            try:
                rc, _text = _run_quiet(roots["R78"])
                if rc != 0:
                    failures.append("R78 under BOTH mutants together: expected exit 0 (the fixture is "
                                    "held by exactly those two layers), got {}".format(rc))
            finally:
                this._e1_file_credit = real_credit
                this._set_pipefail_effect = real_effect

            # --- controls C1..C32 (exit 0), each naming the over-broad implementation it catches ---
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
            # C20: the closed whole-array save (a plain assignment), end to end through the lexer
            # (the NAME=( record is joined with its closing paren by reading the intact source line,
            # never dequoted text). The declare -a spelling is the refusal R100: its head is a
            # command word outside the closed read set.
            c20 = _case("C20", CFG, 'tool write | tail -1\nst=("${PIPESTATUS[@]}")\n', 0,
                        "catches 'refuse the canonical whole-array save'")
            # C22: a pipeline may end at one ';' with the read on the next line.
            _case("C22", CFG, "tool write | tail -1;\nrc=${PIPESTATUS[0]}\n", 0,
                  "catches 'refuse a semicolon-terminated pipeline'")
            # C23: a QUOTED "select" is a command name, not the reserved word: the declared name after
            # it is that command's argument, never this pipeline's producer. A plain select LATER in
            # the segment (an ordinary argument) must not turn the quoted token back into syntax:
            # plainness is judged per token.
            c23 = _case("C23", CFG, '"select" tool write | tail -1\ntool write --row 9\n', 0,
                        "catches 'read a quoted word as a reserved word'")
            _case("C23b", CFG, '"select" tool write select | tail -1\ntool write --row 9\n', 0,
                  "catches 'judge a token's plainness by any same-spelled word in the segment'")
            # C24: a wrapper used BARE (no option words) stays transparent.
            c24 = _case("C24", CFG, "env tool write --row 9\n", 0,
                        "catches 'fail closed on every wrapper'")
            # C25: the double-quoted scalar read is inside the closed set.
            _case("C25", CFG, 'tool write | tail -1\nrc="${PIPESTATUS[0]}"\n', 0,
                  "catches 'refuse the quoted scalar read'")
            # (The former case/select depth controls are now R66/R67: a case or select statement is
            # outside the closed credit grammar, so those files earn no credit at all.)
            # C28: the canonical decimal spelling of a two-digit stage index IS credited (R72 pins the
            # octal spelling of the same stage as refused: the boundary is tested from both sides).
            _case("C28", CFG, "true | true | true | true | true | true | true | true | true | true | "
                              "tool write | cat\nrc=${PIPESTATUS[10]}\n", 0,
                  "catches 'refuse every multi-digit PIPESTATUS index'")
            # C29: shell whitespace (a tab) may surround the read; only non-shell blanks are refused.
            _case("C29", CFG, "tool write | tail -1\n\trc=${PIPESTATUS[0]}\n", 0,
                  "catches 'refuse an indented read line'")
            # C30: text BEFORE the pipeline on its line does not break E2; only text after the final
            # stage does (the docstring states exactly this bound).
            _case("C30", CFG, "echo a; tool write | tail -1\nrc=${PIPESTATUS[0]}\n", 0,
                  "catches 'require the pipeline to own its whole line'")
            # C31: find without any declared mention stays clean (the find refusal is scoped).
            _case("C31", CFG, "find /x -name y | head -1\ntool write --row 9\n", 0,
                  "catches 'refuse every find'")
            # C32: ionice used bare on a non-piped command stays transparent and clean.
            _case("C32", CFG, "ionice tool write --row 9\n", 0,
                  "catches 'fail closed on a bare ionice'")
            # C33: a double-quoted line continuation inside a command word is removed by bash before
            # word splitting, so the token decodes to the lexer's own word ('tool' here, invoked
            # with no pipe) and the walk stays aligned; the later pipeline keeps its adjacent scalar
            # read. A decoder that cannot remove it leaves no aligned token and the command-start
            # guard would wrongly refuse this ordinary script.
            c33 = _case("C33", CFG, '"to\\\nol" write\ntool write | tail -1\nrc=${PIPESTATUS[0]}\n',
                        0, "catches a double-quote decoder blind to the backslash-newline removal")
            # C34/C35: a file whose only trap is a plainly spelled EXIT or ERR trap, with no
            # extdebug, keeps the read credit: the whole-file withdrawal is scoped to what can make
            # bash skip the save, never to every trap.
            c34 = _case("C34", CFG, "trap 'rm -f lock' EXIT\ntool write | tail -1\n"
                                    "rc=${PIPESTATUS[0]}\n", 0,
                        "catches 'withdraw the read credit from every trap' (EXIT, scalar save)")
            c35 = _case("C35", CFG, "trap 'echo err' ERR\ntool write | tail -1\n"
                                    'st=("${PIPESTATUS[@]}")\n', 0,
                        "catches 'withdraw the read credit from every trap' (ERR, array save)")
            # C36/C37: the whole-file withdrawal is scoped to routes that can reach this shell's
            # option or trap state, never to look-alike spellings: running a child script is not
            # sourcing it (the dot rule reads the resolved BASENAME, never a leading './'), and an
            # option word such as --debug is not the DEBUG condition name (the word must stand on
            # its own). Each boundary is pinned by its over-broad mutant below.
            c36 = _case("C36", CFG, "./helper.sh\ntool write | tail -1\nrc=${PIPESTATUS[0]}\n", 0,
                        "catches 'read a dotted path as the dot command' (a child script cannot "
                        "reach this shell's save)")
            c37 = _case("C37", CFG, "tool write --debug | tail -1\nrc=${PIPESTATUS[0]}\n", 0,
                        "catches 'read any word containing debug as the DEBUG name'")
            # C38: a shopt whose every word is literal is left to the extdebug scans, so an inert
            # option beside the credited read keeps it: the shopt-operand withdrawal is scoped to
            # operands an expansion character makes unreadable, never to every shopt.
            c38 = _case("C38", CFG, "shopt -s nullglob\ntool write | tail -1\n"
                                    "rc=${PIPESTATUS[0]}\n", 0,
                        "catches 'withdraw the read credit from every shopt'")

            # The C33 mutant is SPECIFIC to the double-quote continuation branch: it behaves as the
            # real decoder everywhere, and only a segment whose double-quoted span holds a
            # backslash-newline loses its alignment, exactly where a decoder that cannot remove the
            # continuation (it would keep the backslash and newline in the token) stops the walk.
            real_leading_plain = this._leading_plain

            def _dq_continuation_blind(raw, argv, _real=real_leading_plain):
                if raw and re.search(r'"[^"]*\\\n', raw):
                    return []
                return _real(raw, argv)

            # Over-broad mutants: each patched predicate makes its control fixture reach the named
            # WRONG exit (a finding, or a refusal), so the control is shown to discriminate the
            # over-broad implementation it names.
            overbroad = [
                ("C2", c2, "_declared_command", lambda base, commands: bool(base),
                 "flag every pipe", 1),
                ("C3a", c3a, "_e1_exempt", lambda events, offset: False, "ignore pipefail", 1),
                ("C4", c4, "_e2_exempt", lambda top, line, k: False,
                 "ignore PIPESTATUS", 1),
                ("C5", c5, "_nonfinal", lambda stages: stages, "flag any stage", 1),
                ("C9", c9, "_subcommand_ok", lambda subs, sub: True, "ignore subcommands", 1),
                ("C13", c13, "_executes", lambda seg: True,
                 "treat a blank continuation record as a command", 1),
                ("C20", c20, "_e2_exempt", lambda top, line, k: False,
                 "ignore the whole-array save", 1),
                ("C23", c23, "_leading_plain", lambda raw, argv: [True] * len(argv),
                 "read a quoted word as a reserved word", 2),
                ("C24", c24, "_command_word", lambda argv, raw=None: (None, None, 0, True),
                 "treat every wrapper as opaque", 2),
                ("C33", c33, "_leading_plain", _dq_continuation_blind,
                 "keep the double-quoted backslash-newline, so exactly the continued quoted "
                 "command start becomes unresolvable", 2),
                ("C4", c4, "_e2_file_credit", lambda text: False,
                 "withdraw the whole-file read credit from a file with no trap at all", 1),
                ("C34", c34, "_e2_record_trap_safe", lambda argv, raw: False,
                 "withdraw the read credit from every trap (EXIT)", 1),
                ("C35", c35, "_e2_record_trap_safe", lambda argv, raw: False,
                 "withdraw the read credit from every trap (ERR)", 1),
                ("C36", c36, "_e2_runs_unseen_text", lambda argv, raw: True,
                 "withdraw the read credit as if every command sourced text", 1),
                ("C37", c37, "_e2_debug_word",
                 lambda argv: any("debug" in w.lower() for w in argv),
                 "read any word containing the letters debug as the DEBUG name", 1),
                ("C34", c34, "_e2_refused_command_word",
                 lambda argv, raw: _command_word(argv, raw)[1] in _E1_REFUSED_WORDS,
                 "read the shared refused set without its trap carve-out (EXIT trap)", 1),
                ("C35", c35, "_e2_refused_command_word",
                 lambda argv, raw: _command_word(argv, raw)[1] in _E1_REFUSED_WORDS,
                 "read the shared refused set without its trap carve-out (ERR trap)", 1),
                ("C4", c4, "_e2_refused_command_word", lambda argv, raw: True,
                 "withdraw the read credit from every command word", 1),
                ("C38", c38, "_e2_shopt_args_unbounded",
                 lambda argv, raw: _command_word(argv, raw)[1] == "shopt",
                 "withdraw the read credit from every shopt", 1),
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

            # --- cannot-evaluate cases X1..X15 (exit 2, each asserting its OWN diagnostic) ---
            # (X1a's needle must not be a substring of the SUBCOMMAND selector diagnostic, so it
            # asserts the command-side wording "matched no command word".)
            _case("X1a", CFG_NOSUB, "echo hello\n", 2,
                  "a declared command matching nothing is cannot-evaluate",
                  needle="matched no command word")
            _case("X1b", CFG, "tool list\n", 2,
                  "a declared subcommand matching nothing is cannot-evaluate",
                  needle="subcommand selector")
            # (The X2 needle is the unparseable-shell guard's OWN wording: the wrapper-option refusal
            # also prints the words "cannot parse", so the shorter needle could not tell them apart.)
            _case("X2a", CFG, "tool write <<EOF\nbody\nEOF\n", 2,
                  "an unquoted heredoc beside a declared command is cannot-evaluate",
                  needle="sits in shell this gate cannot parse")
            _case("X2b", CFG, "tool write <(echo x)\n", 2,
                  "a process substitution beside a declared command is cannot-evaluate",
                  needle="sits in shell this gate cannot parse")
            _case("X2c", CFG, "tool write --row 9\n/usr/local/bin/tool write <(echo x) | tail -1\n", 2,
                  "a PATH-QUALIFIED declared command in unparseable shell is still seen",
                  needle="sits in shell this gate cannot parse")
            _case("X2d", CFG, 'tool write --row 9\nto""ol write <(echo x) | tail -1\n', 2,
                  "a QUOTE-SPLIT declared name in unparseable shell is still seen: the quote-tolerant "
                  "regex establishes its identity in the raw tail",
                  needle="sits in shell this gate cannot parse")
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
            _case("X3g", CFG, "ksh -c 'tool write | tail -1'\ntool write --row 9\n", 2,
                  "a declared command inside a ksh -c string is cannot-evaluate",
                  needle="ksh -c")
            _case("X3h", CFG, "dash -c 'to\"\"ol write | tail -1'\ntool write --row 9\n", 2,
                  "a QUOTE-SPLIT declared name inside a shell -c string is still seen: absence in the "
                  "contiguous raw spelling proves nothing", needle="dash -c")
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
            # piped onward, all fail closed: their execution shell or time is not modelled. Every
            # needle here is a substring the grouped-producer backstop does NOT print ("inside a ...",
            # or the quoted loop keyword), so a removed construct guard cannot hide behind the
            # backstop's exit 2.
            _case("X6a", CFG, "(tool write) | tail -1\n", 2,
                  "a declared command inside a subshell is cannot-evaluate", needle="inside a subshell")
            _case("X6b", CFG, "{ tool write; } | tail -1\n", 2,
                  "a declared command inside a brace group is cannot-evaluate",
                  needle="inside a brace group")
            _case("X6c", CFG, "while :; do tool write; done | tail -1\n", 2,
                  "a declared command inside a loop is cannot-evaluate", needle="'while' loop")
            _case("X6d", CFG, '(to""ol write) | tail -1\n', 2,
                  "a QUOTED declared name inside a subshell is read from the dequoted words",
                  needle="inside a subshell")
            _case("X6e", CFG, "(true) | tail -1\ntool write --row 9\n", 2,
                  "a grouped producer in a file that mentions a declared command is cannot-evaluate "
                  "even when the group itself holds none (disclosed over-refusal)",
                  needle="grouped or compound")
            _case("X6f", CFG, "coproc tool write\ntool write --row 9\n", 2,
                  "a declared command beside a coproc is cannot-evaluate", needle="coproc")
            _case("X6g", CFG, "(x=$(tool write)) | tail -1\n", 2,
                  "a declared command living only inside a piped group's substitution is read from "
                  "the UNMASKED text", needle="inside a subshell")
            _case("X6h", CFG, "{ x=$(tool write); } | tail -1\n", 2,
                  "a declared command living only inside a piped brace group's substitution is read "
                  "from the UNMASKED text", needle="inside a brace group")
            x6i = _case("X6i", CFG, "until false; do tool write; done | tail -1\n", 2,
                        "a declared command inside an until loop is cannot-evaluate",
                        needle="'until' loop")
            x6j = _case("X6j", CFG, "for i in 1 2; do tool write; done | tail -1\n", 2,
                        "a declared command inside a for loop is cannot-evaluate",
                        needle="'for' loop")
            _case("X6k", CFG, '{ x=$(to""ol write); } | tail -1\n', 2,
                  "a QUOTE-SPLIT declared name living only inside a piped group's substitution is "
                  "still seen through the quote-tolerant regex over the unmasked text",
                  needle="inside a brace group")
            _case("X6l", CFG, "{ echo tool; }\ntool write --row 9\n", 2,
                  "a declared NAME that is only another command's argument data inside a refused "
                  "construct still refuses (a disclosed over-refusal: the gate does not separate "
                  "argument mentions from command positions inside constructs it cannot attribute)",
                  needle="inside a brace group")
            # X6m/X6n: a backslash-newline is removed BEFORE reading, so a continued loop keyword is
            # still the keyword and the declared command inside the loop is refused, never passed.
            x6m = _case("X6m", CFG, "tool write --row 9\ntrue; \\\nuntil tool write | tail -1; "
                                    "do sleep 1; done\n", 2,
                        "a continued 'until' is still the loop keyword; the declared command inside "
                        "the loop head is refused", needle="'until' loop")
            x6n = _case("X6n", CFG, "tool write --row 9\ntrue; \\\nwhile :; do\ntool write | tail "
                                    "-1\nrc=${PIPESTATUS[0]}\nbreak\ndone\n", 2,
                        "a continued 'while' is still the loop keyword; the declared command and its "
                        "read inside the body are refused", needle="'while' loop")
            # X6o..X6v: the continuation may be followed by spaces, a tab, or a further continuation;
            # the keyword (or the '{' opener) still begins the same command, so the loop or group
            # body is refused exactly as the unindented spelling is. The while/for/brace bodies
            # carry a PIPESTATUS read on purpose: were the keyword misread as a command name, that
            # read would wrongly earn E2 and the file would PASS outright.
            x6o = _case("X6o", CFG, "tool write --row 9\ntrue; \\\n until tool write | tail -1; "
                                    "do sleep 1; done\n", 2,
                        "a space-indented continued 'until' is still the loop keyword",
                        needle="'until' loop")
            x6p = _case("X6p", CFG, "tool write --row 9\ntrue; \\\n\tuntil tool write | tail -1; "
                                    "do sleep 1; done\n", 2,
                        "a tab-indented continued 'until' is still the loop keyword",
                        needle="'until' loop")
            x6q = _case("X6q", CFG, "tool write --row 9\ntrue; \\\n \\\n until tool write | "
                                    "tail -1; do sleep 1; done\n", 2,
                        "a repeated continuation before 'until' is still the loop keyword",
                        needle="'until' loop")
            x6r = _case("X6r", CFG, "tool write --row 9\ntrue; \\\n  while :; do\ntool write | "
                                    "tail -1\nrc=${PIPESTATUS[0]}\nbreak\ndone\n", 2,
                        "a space-indented continued 'while' is still the loop keyword; the read "
                        "inside the body earns nothing", needle="'while' loop")
            x6s = _case("X6s", CFG, "tool write --row 9\ntrue; \\\n\twhile :; do\ntool write | "
                                    "tail -1\nrc=${PIPESTATUS[0]}\nbreak\ndone\n", 2,
                        "a tab-indented continued 'while' is still the loop keyword",
                        needle="'while' loop")
            x6t = _case("X6t", CFG, "tool write --row 9\ntrue; \\\n \\\n while :; do\ntool write "
                                    "| tail -1\nrc=${PIPESTATUS[0]}\nbreak\ndone\n", 2,
                        "a repeated continuation before 'while' is still the loop keyword",
                        needle="'while' loop")
            x6u = _case("X6u", CFG, "tool write --row 9\ntrue; \\\n  for i in 1 2; do\ntool write "
                                    "| tail -1\nrc=${PIPESTATUS[0]}\ndone\n", 2,
                        "a space-indented continued 'for' is still the loop keyword",
                        needle="'for' loop")
            x6v = _case("X6v", CFG, "tool write --row 9\ntrue; \\\n  {\ntool write | tail -1\n"
                                    "rc=${PIPESTATUS[0]}\n}\n", 2,
                        "a space-indented continued '{' still opens its brace group",
                        needle="inside a brace group")
            # X6w: a first token whose spelling the walk cannot decode ($'...') stands at a command
            # START. In bash this spelling always names a COMMAND, never the reserved word, so the
            # refusal is a disclosed over-refusal: the walk has not decoded the spelling, and
            # crediting an undecoded first token as a command name could mask a walk/lexer
            # divergence whose token really is plain syntax, so it refuses (its own guard, pinned
            # below).
            x6w = _case("X6w", CFG, "$'until' tool write | tail -1\ntool write --row 9\n", 2,
                        "an undecodable command-start token is never read as a plain command name",
                        needle="cannot be told from a command name")
            real_start_x6w = this._command_start_unresolved
            this._command_start_unresolved = lambda flags, argv, raw: False
            try:
                rc, _text = _run_quiet(x6w)
            finally:
                this._command_start_unresolved = real_start_x6w
            if rc != 0:
                failures.append("X6w under the disabled command-start guard: expected exit 0, got "
                                "{} (the case does not pin the guard)".format(rc))
            # The for/until entries of REFUSED_BODIES are pinned by their own mutants: with the entry
            # removed, the loop's diagnostic must disappear (the run then refuses through the grouped
            # backstop, whose text does not contain the quoted keyword).
            real_bodies = this.REFUSED_BODIES
            for mut_name, dropped, root_dir, needle in (("X6i", "until", x6i, "'until' loop"),
                                                        ("X6j", "for", x6j, "'for' loop"),
                                                        ("X6m", "until", x6m, "'until' loop"),
                                                        ("X6n", "while", x6n, "'while' loop"),
                                                        ("X6o", "until", x6o, "'until' loop"),
                                                        ("X6p", "until", x6p, "'until' loop"),
                                                        ("X6q", "until", x6q, "'until' loop"),
                                                        ("X6r", "while", x6r, "'while' loop"),
                                                        ("X6s", "while", x6s, "'while' loop"),
                                                        ("X6t", "while", x6t, "'while' loop"),
                                                        ("X6u", "for", x6u, "'for' loop"),
                                                        ("X6v", "{", x6v, "a brace group")):
                this.REFUSED_BODIES = {k: v for k, v in real_bodies.items() if k != dropped}
                try:
                    rc, text = _run_quiet(root_dir)
                finally:
                    this.REFUSED_BODIES = real_bodies
                if needle in text:
                    failures.append("{} under the dropped-'{}' REFUSED_BODIES mutant: the loop "
                                    "diagnostic must disappear, proving the case pins that entry".format(
                                        mut_name, dropped))
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
            _case("X12c", CFG, "tool write\nfunction f { tool write | tail -1; }\nf\n", 2,
                  "a ONE-LINE function definition keeps the body's commands in the opener's own "
                  "record, before the block stack sees it; the declared command there is already "
                  "inside the function body", needle="function body")
            _case("X12d", CFG, 'f() {\nx=$(to""ol write)\n}\nf | tail -1\n', 2,
                  "a QUOTE-SPLIT declared name living only inside a function-body substitution is "
                  "still seen through the quote-tolerant regex over the unmasked text",
                  needle="function body")
            _case("X12e", CFG, "f() if true; then tool write | tail -1; fi\nf\n", 2,
                  "a function body that is an if compound (not a brace group) is still a function "
                  "body: the opener after the definition is the deferred body",
                  needle="function body")
            _case("X12f", CFG, "f() if :; then :; fi\n{\ntool write | head\n}\n", 2,
                  "the pending function mark is consumed by the if body and never leaks onto the "
                  "LATER unrelated brace group, which is refused as the brace group it is",
                  needle="inside a brace group")
            _case("X12g", CFG, "tool write --row 9\nf() [[ $(tool write) ]]\nf | true\n", 2,
                  "a conditional-command function body is not a modelled compound form: the declared "
                  "command inside its substitution cannot be attributed, so the definition refuses "
                  "with its own diagnostic",
                  needle="not a compound form this gate models")
            _case("X12h", CFG, "f() ( : )\n{\ntool write | head\n}\n", 2,
                  "a subshell function body consumes the pending definition mark, so the LATER brace "
                  "group is refused as the brace group it is, never misattributed to the function",
                  needle="inside a brace group")
            # X13/X14: argument-executing commands may run the declared command themselves.
            _case("X13", CFG, "flock /abs/l tool write | tail -1\ntool write --row 9\n", 2,
                  "a declared command in flock's argument list is cannot-evaluate",
                  needle="argument list of 'flock'")
            _case("X14", CFG, "printf x | xargs tool write | tail -1\ntool write --row 9\n", 2,
                  "a declared command in xargs's argument list is cannot-evaluate",
                  needle="argument list of 'xargs'")
            x13b = _case("X13b", CFG, "find /x -name y -exec tool write \\; | head\n"
                                      "tool write --row 9\n", 2,
                         "a declared command among find's arguments (-exec and kin can run it) is "
                         "cannot-evaluate", needle="argument list of 'find'")
            # The find entry is pinned by its own mutant: without it the fixture must pass clean.
            real_exec = this.ARG_EXECUTORS
            this.ARG_EXECUTORS = frozenset(w for w in real_exec if w != "find")
            try:
                rc, _text = _run_quiet(x13b)
            finally:
                this.ARG_EXECUTORS = real_exec
            if rc != 0:
                failures.append("X13b under the dropped-'find' ARG_EXECUTORS mutant: expected exit 0, "
                                "got {} (the case does not discriminate that entry)".format(rc))

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
                if rc != 2 or "crosses a symlink at cmd/job.sh" not in text:
                    failures.append("X9a: a symlinked declared surface expected exit 2 naming the "
                                    "symlinked final component itself, got {}".format(rc))
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
                # The needle names the RESOLVER's own diagnostic: scan_file's second layer prints a
                # different "not a regular file" sentence, so a removed resolver check cannot hide
                # behind the fallback layer reaching the same exit code.
                if rc != 2 or "path selector 'cmd/job.sh' is not a regular file" not in text:
                    failures.append("X9d: a FIFO declared surface expected exit 2 with the resolver's "
                                    "own non-regular-file diagnostic, got {} (another layer may have "
                                    "produced the exit code)".format(rc))
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
            # X9j/X9k: a regular FILE named .git or __pycache__ under dir/** refuses through the
            # filenames branch of the walk (the directory fixtures above exercise only the dirnames
            # branch; with this branch removed the file would be SCANNED and exit 1, failing these).
            r = _fresh("X9j", CFG_PREFIX, {"cmd/a.sh": "tool write --row 9\n",
                                           "cmd/.git": "tool write | tail -1\n"})
            rc, text = _run_quiet(r)
            if rc != 2 or "never silently pruned" not in text:
                failures.append("X9j: a regular file named .git under dir/** expected exit 2 naming "
                                "the metadata entry, got {}".format(rc))
            r = _fresh("X9k", CFG_PREFIX, {"cmd/a.sh": "tool write --row 9\n",
                                           "cmd/__pycache__": "tool write | tail -1\n"})
            rc, text = _run_quiet(r)
            if rc != 2 or "never silently pruned" not in text:
                failures.append("X9k: a regular file named __pycache__ under dir/** expected exit 2 "
                                "naming the metadata entry, got {}".format(rc))

            # X15: scan_file's own second-layer regular-file check, exercised DIRECTLY (through run()
            # the resolver refuses first, so this layer would otherwise ship untested). A directory
            # target raises before any open; with the check removed the open would raise a different
            # OSError sentence, failing the needle, and a FIFO there could block forever.
            r = _fresh("X15", CFG, {"cmd/job.sh": "tool write --row 9\n"})
            x15_binding = {"id": "row-writer", "paths": ["cmd/job.sh"], "commands": ["tool"],
                           "subcommands": ["write"]}
            try:
                scan_file(r, r / "cmd", x15_binding, {"commands": set(), "subcommands": set()})
            except GateError as exc:
                if "is not a regular file; refusing to open it" not in str(exc):
                    failures.append("X15: scan_file on a non-regular path expected its own "
                                    "refusing-to-open diagnostic, got {!r}".format(str(exc)[:160]))
            else:
                failures.append("X15: scan_file on a non-regular path must raise before opening it")

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
                if rc != 2 or "a symlinked configuration is not followed" not in text:
                    failures.append("X10-dangling-config: a dangling-symlink configuration expected "
                                    "fail-closed exit 2 with the configuration-symlink check's own "
                                    "diagnostic, got {}".format(rc))
        finally:
            shutil.rmtree(base_tmp, ignore_errors=True)

    if failures:
        print("SELF-TEST FAIL:")
        for f in failures:
            print("  - " + f)
        return 1
    print("SELF-TEST PASS: the refuse cases R1-R125 (R24 retired) and Rb1 (the seed, substitution "
          "bodies, AND lists, if conditions, |&, bare wrappers including ionice, absolute paths, "
          "every enable outside the standalone column-1 closed form (piped, backgrounded, "
          "conditional, wrapped, coproc'd, continued, a final stage, sharing its line, spelled "
          "outside the validated option and -o name vocabularies, or carrying posix), every "
          "whole-file credit withdrawal (a disable at any level, quoted and escaped "
          "eval/source/dot/builtin/set, alias, enable, trap, control flow, redirections, "
          "substitutions, a variable-driven set, positional parameters mentioning pipefail, a "
          "quoted command word, an unmodelled builtin such as printf, mapfile or readarray, an "
          "alias-table write, a non-inert or non-documented-shape shopt, a binding-variable or "
          "POSIXLY_CORRECT assignment, every deny-roster member pinned by literal name), the "
          "refused declare -a read form (bare, and beside posix, callback, indirect, variable-held "
          "and mid-word redefinition spellings, behind a plain reserved word and behind the time "
          "keyword), the withdrawn whole-file read credit beside extdebug and DEBUG traps "
          "(combined beside the scalar and the array save, and each visible half alone), the "
          "withdrawn read credit beside sourced text (source, dot and a heredoc-fed source), "
          "the shared refused set's wrapped sourcing routes (builtin source, builtin dot, "
          "command builtin source, an eval that sources, an alias of source and a function body "
          "wrapping builtin source), brace- and glob-built trap and shopt command words, a "
          "brace-built shopt operand, "
          "variable-built and printf-assembled trap command words, a variable-built ERR trap, a "
          "standing DEBUG word and the escaped extdebug spelling, every "
          "read outside the closed E2 "
          "set (a mismatched or non-canonical "
          "octal index, a late, quoted, commented, quote-mangled, bare-array, extra-word or "
          "non-adjacent read, non-shell whitespace around the read, a heredoc on the pipeline's "
          "lines with its stage-text and next-record-adjacency layers each pinned alone, a "
          "pipeline ending in '&' or '||' or followed on its line), a "
          "line continuation (joined pipelines AND continued keywords, bare, space-indented, "
          "tab-indented and repeated), a first and a MIDDLE "
          "pipeline stage asserted by index, a substitution-body read, a nested dir/** finding "
          "asserted by file, quoted declared and reserved-word command names (behind a leading "
          "redirection too), and the enable-after-pipeline boundary) each fire and each catches the "
          "never-report mutant; the targeted mutants (stage truncation, raw-text PIPESTATUS, forced "
          "file credit, a line-grammar-blind credit, a vocabulary-blind enable reader, an "
          "octal-blind index reader, a read set re-admitting the declare -a head, a re-admitted "
          "whole-file read credit, a trap reader trusting every trap (with the extdebug route "
          "shown to withdraw on its own under that trust), the source, shared-refused-set, "
          "unbounded-command-word, shopt-operand, "
          "DEBUG-word and dequoted-extdebug branches each stubbed alone, "
          "an adjacency-blind and a heredoc-blind read-identity "
          "reader, a same-spelling plainness resolver, a continuation-blind pre-token skip layered "
          "with a disabled command-start guard, a wrapper roster without ionice, and the builtin "
          "and binding-variable rosters without one member each) are each caught, and the layered "
          "quoted-disable fixture R78 stands under "
          "the forced-credit and dropped-off-events mutants one at a time and falls only under both "
          "together (R79 is run under the forced-credit mutant alone: the dropped-off-events mutant "
          "wraps only set's option reader, which R79's shopt path never consults), and the "
          "two-layer fixtures R106 (trap reader plus standing DEBUG word), R110/R111 (unbounded "
          "command word plus standing DEBUG word), "
          "R92/R93/R101/R102 (declare -a head plus unbounded command word) and R75/R84 (declare "
          "-a head plus refused command word) each stand under either "
          "layer's mutant alone and fall only under both together; the controls "
          "C1-C25 (C12, C14, C15, C17 and C19 retired; C21's declare -a save became the refusal "
          "R100 when that head left the closed read set; C26/C27 became "
          "the refusals R66/R67 when control flow left the closed credit grammar) plus C23b and "
          "C28-C38 (no config, undeclared producers, the accepted pipefail spellings, the closed "
          "PIPESTATUS reads including the plain whole-array save, the canonical two-digit index, "
          "the "
          "quoted scalar, tab-indented and semicolon-terminated reads, a double-quoted line "
          "continuation inside a command word, text before the pipeline on "
          "its line, a line-ending pipe, a final-stage command, quoted and commented text, an OR "
          "list, a quoted program string, an undeclared subcommand, a quoted heredoc body, a "
          "redirect, an unrelated shopt, a clean dir/** walk, bare env and ionice wrappers, a clean "
          "find, quoted 'select' command names with and without a plain same-spelled argument, "
          "plainly spelled EXIT and ERR traps beside the credited reads, a child script run by its "
          "dotted path, a --debug option word beside the credited read, and a literal inert shopt "
          "beside the credited read) "
          "each stay clean and the patchable ones catch their over-broad mutants; and the "
          "cannot-evaluate cases X1-X15 (unmatched selectors, unparseable shell beside a plain, "
          "path-qualified or quote-split declared command, sh -c/bash -c/dash -c/zsh -c/ksh -c/eval "
          "strings with plain, clustered and quote-split contents, wrapper-option and subcommand "
          "opacity, quoted and escaped declared names behind wrappers and groups, declared commands "
          "in subshells, brace groups (a space-indented continued '{' included), while/until/for "
          "loops (continued keywords included, bare, space-indented, tab-indented and repeated), an "
          "undecodable command-start token, coprocs, "
          "one-line and non-brace function bodies, function-definition bodies outside the modelled "
          "compound forms (with a subshell body consuming the mark, never leaking it onto a later "
          "block), quote-split substitution identities, xargs/flock/find argument lists, "
          "grouped producers, a declared name as argument data in a refused construct, malformed "
          "configurations and empty, dot and dot-dot selector components, an inaccessible "
          "configuration parent, symlinked files, subdirectories, ancestors and configurations, "
          "FIFO surfaces and configurations, .git/__pycache__ directories, symlinks AND regular "
          "files refused rather than pruned, and scan_file's own pre-open regular-file layer) each "
          "fail closed printing the diagnostic of their OWN check, asserted by a substring no other "
          "check prints")
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
