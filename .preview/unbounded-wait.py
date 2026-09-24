#!/usr/bin/env python3
"""PreToolUse Bash hook (unbounded-wait): deny a background polling loop that has no bound.

WHAT IT DOES
    A `while` or `until` loop that sleeps between polls and carries no bound (no timeout, counter, or deadline)
    waits forever when its condition never becomes true: a wrong path, a producer that died, a pattern that is
    never written. In the foreground such a loop is cut short by the harness's call timeout. In the background
    it can run for hours, and because it never ends it never sends its completion notification, so the stall
    is invisible. This hook denies that shape at launch, when the fix is one line, and its reason names the
    bounded rewrite.

    Event: PreToolUse, matcher Bash. Register the launch line REGISTRATION (below the imports), filled with
    python3 and this file's absolute path. Output: nothing (allow), or ONE line holding the standard PreToolUse
    deny object. Exit status: always 0; the decision travels in the JSON. The verdict is deny or silence: this
    hook never asks.

    DENY when all of these hold:
      - the command runs in the background: tool_input.run_in_background is exactly true (the tracked
        background path), or the loop's own and-or list, or that of a compound command enclosing it, ends
        with the `&` control operator (a detached launch, including one another hook lets pass on its own
        opt-out);
      - it holds a command-position `while` or `until` loop whose text (condition, body, nested commands, and
        their command substitutions) runs `sleep` as a command, found past leading assignments, redirections,
        and these command-running wrappers with their options, option values, and operands (a redirection
        anywhere among them too): builtin, chrt, command, env, exec, ionice, nice, nohup, setsid, stdbuf,
        sudo, taskset, time, and timeout (a timeout bounds only the command it wraps, never the loop around
        it);
      - the loop is not a read loop: one whose condition runs `read`, `mapfile`, or `readarray` on the loop's own
        input, that is, as a command (not a word inside `[[ ... ]]`, nor an argument after a word spelled like a
        reserved word), with no redirection on it that opens something (a `<`, `<>`, here-document, or
        here-string) on the descriptor it reads (0, or its `-u N`), and not fed by a pipe opened within the
        condition when it reads descriptor 0: neither directly (`until : | read l`) nor through a receiver around
        it standing after that pipe (a `{ ...; }` group, `( ... )` subshell, `if`, or loop), unless a `-u N`, or a
        redirection on the reader or on that receiver, gives descriptor 0 other input: one that opens something,
        or a copy or move of another descriptor (`<&3`, `0>&3`), descriptor numbers compared as numbers; a copy or
        move of descriptor 0 onto itself (`<&0`, `<&00`, `<&0-`) or a close (`<&-`) gives none. A pipe feeding the
        whole loop (`producer | while read l`) is the loop's own input. A read loop is allowed without judging
        whether it ends at end of input, except two shapes that can spin there: a `while` whose whole condition is
        one negated `read` (`while ! read ...`), and an `until` whose whole condition is one unnegated `read`
        (`until read ...`), each also recognized after a `time` prefix with an optional `-p` and an optional `--`
        (`time`, `time -p`, `time --`, `time -p --`) and through one `{ ...; }` or `( ... )` layer with any
        redirections on that layer. A read whose own redirection reopens its input each time
        (`read s < status.txt`) polls the same file, so it does not make its loop a read loop;
      - the loop's text carries none of the bound markers: the word SECONDS (EPOCHSECONDS too), the word
        EPOCHREALTIME, an arithmetic `((` (so a `$((` update too), a numeric test operator word (-lt, -le, -gt, -ge,
        -eq, -ne), a `let` or `expr` command, a printf `%(...)T` time format of any length, or a `date` command,
        with any options, whose format argument is an epoch `+...%s` (`+%s.%N` too). Any one of them counts as a
        bound, failing toward allow: an arithmetic update is taken as a counter whatever test (`-lt`, `!=`, `=`)
        ends the loop, and any `%(...)T` format as a clock. A `break` alone is not a bound: it waits on the same
        condition;
      - the command does not end with the opt-out comment (below);
      - bash's own parser accepts the command (the syntax oracle, `bash -n`): a command bash rejects runs
        nothing, so the deny is withdrawn; when the oracle is unavailable the lexical verdict stands.
    A `for` or `select` loop is never flagged, and a plain foreground call is never flagged.

    When a denied loop's condition polls a literal path (`grep -q PAT FILE`, `test -e|-f|-s FILE`,
    `[ -f FILE ]`, `ls FILE`) whose parent directory does not exist (a relative path is resolved against the
    payload's cwd), the reason adds a note naming that directory, since the polled path can never appear
    there. At most 8 stat calls are made for it.

OPT-OUT
    For a deliberate unbounded watcher, end the command with a real shell comment whose text starts
    `# wait-ok`, optionally followed by a colon or blank and a reason, for example:
        until grep -q ready status.log; do sleep 5; done  # wait-ok: a watcher stopped by hand
    The comment must be unquoted, begin a word, and be the last non-blank content of the command; a `#` inside
    a word or a quoted string is not a comment and does not opt out.

THREAT MODEL
    This is an accidental-habit guard, not a security boundary. The actor is a well-meaning assistant that writes an
    ordinary wait loop and forgets its bound; nothing here resists a caller that sets out to hide a loop. So every
    internal error, every input the scan cannot follow, and every malformed payload fails OPEN: no output, exit 0.
    The hook also stays silent for a verification worker process (a worker kill-switch variable; legacy spellings
    are also honoured), for a payload carrying agent_id (a subagent's call), for a tool_name other than Bash, for an
    event other than PreToolUse, for any argv other than the plain hook call or exactly `--self-test` (answered
    before stdin is read), and for a stdin that is absent, unreadable, over 16 MiB, incomplete after 2 seconds, not
    JSON, or not an object. Work is bounded: a command over 64 KiB of UTF-8 or 4000 tokens is not scanned at all
    (the whole command fails open, never a truncated prefix of it), the scan is linear in the tokens (each command
    word and its argument span is read once, however loops nest or commands repeat), and the syntax oracle runs at
    most once, on the deny path only, under a 2 second limit. The hook never executes the command: bash is run only
    with -n (parse, never execute), from a fixed trusted path, with an empty environment, reading the command on its
    stdin.

RESIDUAL COVERAGE.
    This guard reads one command's own top-level loops. It does not catch a loop inside a nested shell string (`bash
    -c '...'`), eval, script file, function body, command or process substitution, or a here-document fed to a
    shell; a command whose top-level stream holds a `case` statement or a `coproc` is not followed at all and is
    allowed. It does not catch waiting done by a watcher utility (`tail -f`, `inotifywait`), an interpreter
    one-liner, a busy loop with no sleep, or a single very long sleep; a loop whose bound marker exists but is never
    reached or bounds nothing, since any counter test, deadline word, date call, arithmetic opener, or `let` or
    `expr` command anywhere in the loop's text (the raw markers in a comment too) counts as a bound, and an
    arithmetic update of a variable the condition never reads still counts; a `sleep` reached through a command
    runner not in the wrapper list above (`doas`, `flock`, `xargs`, `watch`, `unbuffer`, `parallel`, `ssh`, a
    shell's `-c` string), through `env -S`'s split string, through a wrapper option this hook does not know takes a
    value, or through `chrt` or `taskset` given a `-p` process or no operand (a word is then misread as the
    command); `command -v` or `-V` read as running its operand; a `sleep` whose name is spelled with quoting the
    prefilter does not undo (`sl$''eep`; plain quotes and backslashes, as in `sl""eep`, are undone); a `let`,
    `expr`, or optioned `date` call inside a backtick substitution, which stays opaque (only its raw `date +%s` or
    `` `expr `` form is seen); a background launch the command text does not show; or a foreground loop, which the
    harness call timeout bounds, though whether the underlying process ends on that timeout is not verified here. A
    `timeout` that wraps only the loop's probe, or only its sleep, bounds that one command, not the loop, and is not
    counted as a bound. A leading redirection before `while` or `until` makes the word an ordinary command name, as
    in bash (which then rejects the command), so no loop is seen there. A read-driven loop is allowed without
    judging whether it ends at end of input: a condition that runs such a read anywhere is exempt whatever else it
    runs (`while read l || ! test -f F`, `read -t 0`, an assignment after the read, a reopening redirection on a
    group around it, a `mapfile` that succeeds at end of input), and a read inside a command substitution in the
    condition does not count. The two denied read shapes are denied whatever input the loop is given, although
    whether they spin depends on the read's exit status: a first read that succeeds (a complete first line, as a
    here-string always supplies) ends the loop at once, and a read that fails (no input left, or only a last line
    without its newline, as an empty here-document or a partial file gives) makes it spin. A `time` word after a
    pipe is taken as the reserved word even where bash runs it as the external `time` command, so a finite loop
    whose reader stands on the line after `: | time` is denied. They are not recognized through more than one
    grouping layer, and a group redirection around a reader that reopens its input is not examined. The
    missing-directory note checks only a polled literal path's parent directory, only in the forms named above, and
    never changes the verdict. An opt-out comment at the end of an unterminated here-document body is taken as the
    comment. The vendored lexer's own disclosed limits carry over: quotes, escapes, comments, and here-document
    bodies are honoured, and a construct it cannot follow yields no verdict. All unparseable or unfollowable input
    fails open with no output.

Self-test: python3 -I -S -B unbounded-wait.py --self-test
    The reference hooks the vendored blocks were copied from are looked up in the directory named by the
    G_REF_DIR environment variable, else in this file's own directory; when a reference is absent its
    byte-identity check reports SKIPPED, never a pass.
"""

import bisect
import json
import os
import re
import select
import sys
import time

HOOK_ID = "unbounded-wait"

# The launch line to register ({python} and {hook} filled in: python3 and this file's absolute path). CPython stops
# before running any byte of this file when its stdin is a directory (exit 1), so only a guard in front of the
# interpreter can answer that one input: it exits 0 silently and otherwise execs the hook, stdin untouched.
REGISTRATION = '[ -d /dev/stdin ] && exit 0; exec "{python}" -I -S -B "{hook}"'

# Each vendored block: name -> (reference file, first line, last line, sha256 of the block's text). The block is
# those lines of the reference with their comments and docstrings removed (_strip_source), fenced below; the
# self-test checks the hash, that the block is already stripped, and, when the reference file is found, that
# stripping the reference's same lines gives the block byte for byte.
_VENDOR = {
    "lexer-constants": ("inplace-edit-verify.py", 311, 400,
        "c57738d7da963cab9fab7900b37d47536e3a3b0c4f0bd46b0221ee3d3eadda24"),
    "lexer-name-patterns": ("inplace-edit-verify.py", 420, 427,
        "ab67d6760d05dcbc39a7209f3018e7b25a4fd4be0af1f8848a66d9e0333725a3"),
    "lexer-expansions": ("inplace-edit-verify.py", 492, 531,
        "4277f6ff13bbb0ef9aa0a825e16111d3990d7d2c918a07fee017f3840cbb34a8"),
    "lexer": ("inplace-edit-verify.py", 547, 849,
        "dcc75a5a9873a9c71f5970028ea4410e9a236ef487b0c193d35f0650d44b44a1"),
    "lexer-helpers": ("inplace-edit-verify.py", 852, 984,
        "5f65c3b2e3a7515e7dfb5999e1192729a6d17d1599b60a96e994253566a4a78f"),
    "syntax-oracle": ("inplace-edit-verify.py", 1398, 1447,
        "d9a85678a9af1fa8455e897c414001564b227bc0627f5d29a3bc8f09eb1e01c3"),
    "payload-reader": ("block-bare-detach.py", 1625, 1664,
        "22f3d07bd73e5093ee337d87a0eee9d6fda5dd26d7f3083f7492b4a9a634f488"),
}


def _strip_source(text):
    """text, a run of whole lines of Python source, with its comments and its def and class docstrings removed.

    A line that holds only a comment, or that belongs to a docstring (a string that is the whole first statement
    of a def or class body), is dropped, except that a comment-only line after a backslash continuation is kept
    as an empty line (dropping it would join the next line onto the continued one); a trailing comment is cut
    together with the blanks before it; every other line is kept byte for byte. Vendored code is stored and
    checked in this form, so no remark about how the reference was written is published with it. Raises
    tokenize.TokenError or SyntaxError on text that does not tokenize."""
    import io
    import tokenize
    lines = text.split("\n")
    cut, drop = {}, set()  # cut: row -> the column its trailing comment starts; drop: rows removed whole
    logical, header, after_header = [], False, False
    for tok in tokenize.generate_tokens(io.StringIO(text).readline):
        if tok.type == tokenize.COMMENT:
            row, col = tok.start
            if lines[row - 1][:col].strip():
                cut[row] = col
            elif row > 1 and lines[row - 2].rstrip("\r").endswith("\\"):
                cut[row] = 0  # after a continuation: kept empty, so the joined line still ends
            else:
                drop.add(row)
        elif tok.type == tokenize.NEWLINE:
            if after_header and len(logical) == 1 and logical[0].type == tokenize.STRING:
                drop.update(range(logical[0].start[0], logical[0].end[0] + 1))
            after_header, logical = header, []
        elif tok.type not in (tokenize.NL, tokenize.INDENT, tokenize.DEDENT, tokenize.ENDMARKER):
            if not logical:
                header = tok.type == tokenize.NAME and tok.string in ("def", "class", "async")
            logical.append(tok)
    return "\n".join(line[:cut[row]].rstrip() + line[len(line.rstrip("\r")):] if row in cut else line
                     for row, line in enumerate(lines, 1) if row not in drop)


# BEGIN VENDOR lexer-constants from inplace-edit-verify.py:311-400
_POST_EVENTS = ("PostToolUse", "PostToolUseFailure")
_SCAN_LIMIT = 64 * 1024
_BLANKS = " \t"
_META = ";&|<>()"
_OPS = (";;&", "<<<", "<<-", "&>>", "&&", "||", ";;", ";&", "|&", ">>", "<<", ">&", "<&", "&>", ">|", "<>",
        ";", "&", "|", "<", ">", "(", ")")
_OP_RE = re.compile("|".join(re.escape(o) for o in _OPS))
_WORD_RUN_RE = re.compile(r"(?:[^ \t\n'\"\\;&|<>()$`]|\$\$|\$(?![('\"]))+")
_SQ_RE = re.compile(r"'[^']*'")
_ANSI_RE = re.compile(r"\$'(?:[^'\\]|\\.)*'", re.S)
_BODY_TOKEN_RE = re.compile(r"\\.|\$\$|\$\(|`|\$\{|\}", re.S)
_BRACE_RE = re.compile(r"(\$+)(\{)?|\}")
_COND_PARAM_RE = re.compile(r"[#!]?(?:[A-Za-z_][A-Za-z0-9_]*|[0-9]+|[-@*#?$!])(?:\[[^\]]*\])?:?[-=?+]")
_COND_PLAIN_RE = re.compile(r"[#!]?(?:[A-Za-z_][A-Za-z0-9_]*|[0-9]+|[-@*#?$!]):?[-=?+]")
_COND_HEAD_RE = re.compile(r"[#!]?(?:[A-Za-z_][A-Za-z0-9_]*|[0-9]+|[-@*#?$!])\[")
_COND_OP_RE = re.compile(r":?[-=?+]")
_PLAIN_WORD_RE = re.compile(r"(?:[^ \t\n'\"\\;&|<>()$`#]|\$(?!\())(?:[^ \t\n'\"\\;&|<>()$`]|\$(?!\())*"
                            r"(?=[ \t\n;&|()]|[<>](?!\()|\Z)")
_DQ_RUN_RE = re.compile(r'(?:[^"\\$`]|\$(?!\())*')
_BQ_RE = re.compile(r"`(?:[^`\\]|\\.)*`", re.S)
_SUB = "$\x00"
_MARK = "_="
_MAX_DEPTH = 32
_MAX_TOKENS = 4000
_MAX_TIME_PREFIXES = 32
_BREAKS = frozenset(_BLANKS + "\n" + _META)
_BLANK_RUN_RE = re.compile("[ \t]+")
_HEREDOC_OPS = {"<<": False, "<<-": True}
_FD_VAR_RE = re.compile(r"^\{[A-Za-z_][A-Za-z0-9_]*\}$")
_REDIRECTS = frozenset((">", ">>", "<", "<<", "<<-", "<<<", ">&", "<&", "&>", "&>>", ">|", "<>"))
_RESERVED = frozenset(("!", "{", "}", "if", "then", "else", "elif", "do", "while", "until"))
_PREFIXES = frozenset(("env", "sudo", "command", "nohup", "time", "exec", "builtin")) | _RESERVED
_OPENERS = frozenset(("{", "while", "until", "if"))
_LOOPS = frozenset(("while", "until", "for", "select"))
_CLOSERS = {"}": ("{",), "done": tuple(_LOOPS), "fi": ("if",), "esac": ("case",)}
_ARM_ENDS = frozenset((";;", ";&", ";;&"))
_COPROC_BODY = frozenset(("{", "while", "until", "for", "select", "if", "case"))
_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_CONTINUE_OPS = frozenset(("|", "||", "&&", "|&"))
_JOIN = frozenset(("&&", "||", "|", "|&"))
_PIPES = frozenset(("|", "|&"))
_SENT = object()
_UNK = 2
_BASH_PATHS = ("/usr/bin/bash", "/bin/bash")
_BASH_DIRS = ("/usr/bin", "/bin")
_ORACLE_TIMEOUT = 2.0
_ORACLE_MAX_OPENERS = 256
_ORACLE_MAX_BYTES = 16 * 1024
# END VENDOR

# BEGIN VENDOR lexer-name-patterns from inplace-edit-verify.py:420-427
_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\[[^\]]*\])?\+?=")
_INDEXED_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\[")
_ARRAY_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\[[^\]]*\])?\+?=$")
_FUNC_NAME_RE = re.compile(r"^[^$=`]+$")
_VERIFY_WORDS = frozenset(("grep", "rg", "cmp", "diff"))
_GIT_VERIFY_SUBS = frozenset(("diff", "diff-index", "diff-files"))
_EVENT_NAMES = _VERIFY_WORDS | {"sed", "perl", "git"}
_EVENT_WORD_RE = re.compile(r"\b(?:%s)\b" % "|".join(sorted(_EVENT_NAMES)))
# END VENDOR

# BEGIN VENDOR lexer-expansions from inplace-edit-verify.py:492-531
class _Cond(list):
    __slots__ = ()


def _braces(text, s, e, stack, meter):
    for m in _BRACE_RE.finditer(text, s, e):
        if m.group(1) is None:
            if stack:
                stack.pop()
        elif m.group(2) and len(m.group(1)) % 2:
            stack.append(_cond_at(text, m.end(), meter))


def _cond_at(text, pos, meter):
    if _COND_PLAIN_RE.match(text, pos):
        return True
    m = _COND_HEAD_RE.match(text, pos)
    if not m:
        return False
    k, memo = m.end(), meter[1].get(id(text))
    if memo and memo[0] is text and memo[1] <= k and (memo[2] < 0 or memo[2] >= k):
        hit = memo[2]
    else:
        hit = text.find("]", k)
        meter[1][id(text)] = (text, k, hit)
    return hit >= 0 and bool(_COND_OP_RE.match(text, hit + 1))


def _kept(inner, stack):
    return _Cond(inner) if True in stack else inner
# END VENDOR

# BEGIN VENDOR lexer from inplace-edit-verify.py:547-849
def _double_quoted(text, i, depth, meter, stack=None):
    out, subs, j, n = [], [], i + 1, len(text)
    stack = [] if stack is None else stack
    while True:
        m = _DQ_RUN_RE.match(text, j)
        chunk = m.group()
        out.append(chunk)
        if stack or "${" in chunk:
            _braces(text, j, m.end(), stack, meter)
        j = m.end()
        if j >= n:
            raise ValueError("unterminated quote")
        c = text[j]
        if c == '"':
            return "".join(out), j + 1, subs
        if c == "\\":
            nxt = text[j + 1:j + 2]
            if not nxt:
                raise ValueError("unterminated quote")
            if nxt in '$`"\\':
                out.append(nxt)
            elif nxt != "\n":
                out.append("\\" + nxt)
            j += 2
        elif c == "`":
            m = _BQ_RE.match(text, j)
            if not m:
                raise ValueError("unterminated backtick")
            out.append(_SUB)
            j = m.end()
        elif text.startswith("((", j + 1):
            j = _lex(text, j + 2, depth + 1, True, meter)[1]
            out.append(_SUB)
        else:
            inner, j = _lex(text, j + 2, depth + 1, True, meter)
            subs.append(_kept(inner, stack))
            out.append(_SUB)


def _skip_heredocs(text, i, pending, depth, meter):
    n, bodies = len(text), []
    for delim, strip, quoted, _ in pending:
        body_start = body_end = i
        while i < n:
            j = text.find("\n", i)
            line = text[i:] if j < 0 else text[i:j]
            body_end, i = i, n if j < 0 else j + 1
            if (line.lstrip("\t") if strip else line) == delim:
                break
            if not quoted and (len(line) - len(line.rstrip("\\"))) % 2:
                raise ValueError("backslash-newline in an unquoted heredoc body")
        else:
            body_end = n
        subs, stack = [], []
        body = text[body_start:body_end]
        if not quoted and ("$(" in body or "`" in body):
            named = "`" in body and _EVENT_WORD_RE.search(body) is not None
            pos = 0
            while True:
                m = _BODY_TOKEN_RE.search(body, pos)
                if not m:
                    break
                if m.group() == "${":
                    stack.append(_cond_at(body, m.end(), meter))
                    pos = m.end()
                elif m.group() == "}":
                    if stack:
                        stack.pop()
                    pos = m.end()
                elif m.group() == "`":
                    if named:
                        raise ValueError("a backtick substitution in an unquoted heredoc body")
                    bq = _BQ_RE.match(body, m.start())
                    if not bq:
                        raise ValueError("unterminated backtick")
                    pos = bq.end()
                elif m.group() == "$(":
                    if body.startswith("(", m.end()):
                        pos = _lex(body, m.end(), depth + 1, True, meter)[1]
                    else:
                        inner, pos = _lex(body, m.end(), depth + 1, True, meter)
                        subs.append(_kept(inner, stack))
                else:
                    pos = m.end()
        bodies.append(subs)
    return i, bodies


def _lex(text, i, depth, closing, meter):
    if depth > _MAX_DEPTH:
        raise ValueError("substitution nesting too deep")
    toks, parts, subs, quoted, start = [], None, [], False, 0
    heredocs, want_delim, parens, n = [], None, 0, len(text)
    pstack = []

    def flush(end):
        nonlocal parts, subs, want_delim
        value = "".join(parts)
        toks.append(("w", value, quoted, start, end, subs))
        meter[0] -= 1
        if want_delim is not None:
            if _SUB in value:
                raise ValueError("a substitution in a heredoc delimiter")
            heredocs.append((value, want_delim, quoted, len(toks) - 1))
            want_delim = None
        parts, subs = None, []

    while i < n:
        c = text[i]
        if c in _BREAKS and not (c in "<>" and text.startswith("(", i + 1)):
            if parts is not None:
                flush(i)
            if c in _BLANKS:
                i = _BLANK_RUN_RE.match(text, i).end()
                continue
            meter[0] -= 1
            if meter[0] < 0:
                raise ValueError("past the work bound")
            if c == "\n":
                toks.append(("nl", "\n", False, i, i + 1, ()))
                want_delim, i = None, i + 1
                if heredocs:
                    i, bodies = _skip_heredocs(text, i, heredocs, depth, meter)
                    for (_, _, _, at), body in zip(heredocs, bodies):
                        if body:
                            toks[at] = toks[at][:5] + (list(toks[at][5]) + body,)
                    heredocs = []
            else:
                if c == "(" or c == ")":
                    op, end = c, i + 1
                    if closing:
                        if c == "(":
                            parens += 1
                        elif parens == 0:
                            return toks, end
                        else:
                            parens -= 1
                else:
                    m = _OP_RE.match(text, i)
                    op, end = m.group(), m.end()
                toks.append(("op", op, False, i, end, ()))
                want_delim = _HEREDOC_OPS.get(op)
                i = end
            continue
        if parts is None and want_delim is None:
            m = _PLAIN_WORD_RE.match(text, i)
            if m:
                toks.append(("w", m.group(), False, i, m.end(), ()))
                if pstack or "${" in toks[-1][1]:
                    _braces(text, i, m.end(), pstack, meter)
                i = m.end()
                meter[0] -= 1
                continue
        if c == "\\":
            if text.startswith("\n", i + 1):
                i += 2
                continue
            if parts is None:
                parts, quoted, start = [], False, i
            parts.append(text[i + 1:i + 2])
            quoted, i = True, i + 2
            continue
        if c == "'":
            m = _SQ_RE.match(text, i)
            if not m:
                raise ValueError("unterminated quote")
            if parts is None:
                parts, start = [], i
            parts.append(m.group()[1:-1])
            quoted, i = True, m.end()
            continue
        if c == '"':
            if parts is None:
                parts, start = [], i
            value, i, dsubs = _double_quoted(text, i, depth, meter, pstack)
            parts.append(value)
            subs.extend(dsubs)
            quoted = True
            continue
        if c == "$" and (text.startswith('"', i + 1) or (want_delim is not None and text.startswith("'", i + 1))):
            if parts is None:
                parts, start = [], i
            if text[i + 1] == "'":
                m = _SQ_RE.match(text, i + 1)
                if not m:
                    raise ValueError("unterminated quote")
                if "\\" in m.group():
                    raise ValueError("ANSI-C escape in a heredoc delimiter")
                parts.append(m.group()[1:-1])
                i = m.end()
            else:
                value, i, dsubs = _double_quoted(text, i + 1, depth, meter, pstack)
                parts.append(value)
                subs.extend(dsubs)
            quoted = True
            continue
        if c == "$" and text.startswith("'", i + 1):
            m = _ANSI_RE.match(text, i)
            if not m:
                raise ValueError("unterminated quote")
            if parts is None:
                parts, start = [], i
            parts.append("$" + m.group()[2:-1])
            quoted, i = True, m.end()
            continue
        if c == "`" or (c in "$<>" and text.startswith("(", i + 1)):
            if parts is None:
                parts, quoted, start = [], False, i
            if c == "`":
                m = _BQ_RE.match(text, i)
                if not m:
                    raise ValueError("unterminated backtick")
                i = m.end()
            elif text.startswith("((", i + 1) if c == "$" else text.startswith("(((", i + 1):
                i = _lex(text, i + 2, depth + 1, True, meter)[1]
            else:
                inner, i = _lex(text, i + 2, depth + 1, True, meter)
                subs.append(_kept(inner, pstack))
            parts.append(_SUB)
            continue
        if c == "#" and parts is None:
            j = text.find("\n", i)
            i = n if j < 0 else j
            continue
        m = _WORD_RUN_RE.match(text, i)
        if m.group().endswith("$") and text.startswith("(", m.end()):
            raise ValueError("a `(` after an even run of `$`")
        if parts is None:
            parts, quoted, start = [], False, i
        parts.append(m.group())
        if pstack or "${" in parts[-1]:
            _braces(text, i, m.end(), pstack, meter)
        i = m.end()
    if closing:
        raise ValueError("unterminated substitution")
    if parts is not None:
        flush(n)
    return toks, n


def _tokenize(text):
    return _lex(text, 0, 0, False, [_MAX_TOKENS, {}])[0]
# END VENDOR

# BEGIN VENDOR lexer-helpers from inplace-edit-verify.py:852-984
def _is_op(tok, value):
    return tok[0] == "op" and tok[1] == value


def _is_bare_word(tok, value):
    return tok[0] == "w" and not tok[2] and tok[1] == value


def _at_command_start(out):
    k = len(out)
    for _ in range(_MAX_TIME_PREFIXES + 1):
        if not k:
            return True
        last = out[k - 1]
        if last[0] == "nl" or (last[0] == "op" and last[1] not in _REDIRECTS):
            return True
        j = k - 1 if _is_bare_word(last, "--") else k
        if j and _is_bare_word(out[j - 1], "-p"):
            j -= 1
        if j and _is_bare_word(out[j - 1], "time"):
            k = j - 1
            continue
        if last[0] != "w" or last[2]:
            return False
        return last[1] in _RESERVED
    return True


def _skip_function_body(toks, k):
    n = len(toks)
    while k < n and toks[k][0] == "nl":
        k += 1
    if k >= n:
        return None
    if _is_bare_word(toks[k], "{"):
        opener, closer, is_open, is_close = "{", "}", _is_bare_word, _is_bare_word
    elif _is_op(toks[k], "("):
        opener, closer, is_open, is_close = "(", ")", _is_op, _is_op
    else:
        return k
    depth = 0
    while k < n:
        if is_open(toks[k], opener):
            depth += 1
        elif is_close(toks[k], closer):
            depth -= 1
            if depth == 0:
                return k + 1
        k += 1
    return None


def _drop_declarations(toks, st):
    if not any(t[1] == "(" or t[1] == "function" for t in toks):
        return toks
    out, i, n = [], 0, len(toks)
    while i < n:
        t = toks[i]
        if (t[0] == "w" and not t[2] and t[1].endswith("=") and _ARRAY_ASSIGN_RE.match(t[1]) and i + 1 < n
                and _is_op(toks[i + 1], "(") and toks[i + 1][3] == t[4]):
            out.append(("w", _MARK, True, t[3], t[4], ()))
            opener, depth, i = toks[i + 1], 1, i + 2
            while i < n and depth:
                u = toks[i]
                if _is_op(u, "("):
                    depth += 1
                elif _is_op(u, ")"):
                    depth -= 1
                elif u[0] == "w" and u[5]:
                    out.append(("w", "_=" + _SUB, True, u[3], u[4], u[5]))
                i += 1
            if depth:
                out.append(opener)
            continue
        if (t[0] == "w" and not t[2] and (t[1] == "function" or (i + 1 < n and _is_op(toks[i + 1], "(")))
                and _at_command_start(out)):
            if (_FUNC_NAME_RE.match(t[1]) and t[1] not in _RESERVED and i + 2 < n and _is_op(toks[i + 1], "(")
                    and _is_op(toks[i + 2], ")")):
                st["func"] = True
                out.append(("w", _MARK, True, t[3], t[4], ()))
                i = _skip_function_body(toks, i + 3)
                if i is None:
                    out.append(("op", "(", False, t[4], t[4], ()))
                    i = n
                continue
            if t[1] == "function" and i + 1 < n and toks[i + 1][0] == "w":
                k = i + 2
                if k + 1 < n and _is_op(toks[k], "(") and _is_op(toks[k + 1], ")"):
                    k += 2
                st["func"] = True
                out.append(("w", _MARK, True, t[3], t[4], ()))
                i = _skip_function_body(toks, k)
                if i is None:
                    out.append(("op", "(", False, t[4], t[4], ()))
                    i = n
                continue
        out.append(t)
        i += 1
    return out


def _bounded(cmd):
    return cmd[:_SCAN_LIMIT].encode("utf-8", "surrogatepass")[:_SCAN_LIMIT].decode("utf-8", "ignore")
# END VENDOR

# BEGIN VENDOR syntax-oracle from inplace-edit-verify.py:1398-1447
def _trusted_bash():
    import stat
    for path in _BASH_PATHS:
        try:
            real = os.path.realpath(path)
            parent = os.path.dirname(real)
            if parent not in _BASH_DIRS:
                continue
            fst, pst = os.lstat(real), os.stat(parent)
            if (stat.S_ISREG(fst.st_mode) and fst.st_uid == 0 and not fst.st_mode & 0o022
                    and fst.st_mode & 0o111 and pst.st_uid == 0 and not pst.st_mode & 0o022):
                return real
        except (OSError, ValueError):
            continue
    return None


def _bash_syntax_ok(command):
    if "\0" in command or command.count("(") + command.count("{") > _ORACLE_MAX_OPENERS:
        return None
    try:
        script = command.encode("utf-8", "surrogateescape")
    except ValueError:
        return None
    if len(script) > _ORACLE_MAX_BYTES:
        return None
    exe = _trusted_bash()
    if exe is None:
        return None
    import subprocess
    try:
        proc = subprocess.run([exe, "--norc", "--noprofile", "-n"], input=script, env={"LC_ALL": "C"},
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                              timeout=_ORACLE_TIMEOUT, close_fds=True, cwd="/")
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    if proc.returncode < 0:
        return None
    return proc.returncode == 0
# END VENDOR

# BEGIN VENDOR payload-reader from block-bare-detach.py:1625-1664
_MAX_INPUT = 16 * 1024 * 1024


_READ_DEADLINE = 2.0
_READ_IDLE = 0.05


def _read_payload(fd=0, deadline=_READ_DEADLINE):
    end = time.monotonic() + deadline
    data, tried = bytearray(), 0
    while True:
        left = end - time.monotonic()
        if left <= 0:
            return json.loads(bytes(data))
        ready = select.select([fd], [], [], min(left, _READ_IDLE))[0]
        if not ready:
            if data and len(data) >= 2 * tried:
                tried = len(data)
                try:
                    return json.loads(bytes(data))
                except ValueError:
                    pass
            continue
        try:
            chunk = os.read(fd, 65536)
        except BlockingIOError:
            continue
        if not chunk:
            return json.loads(bytes(data))
        data += chunk
        if len(data) > _MAX_INPUT:
            raise ValueError("hook payload over the read bound")
# END VENDOR

# The hook's own scan.
_NUMERIC_TESTS = frozenset(("-lt", "-le", "-gt", "-ge", "-eq", "-ne"))
# Raw-text bound markers, anywhere in a loop's text: a SECONDS deadline (EPOCHSECONDS holds it too), the
# EPOCHREALTIME clock, an arithmetic opener, a `date +%s` call, a backtick `expr` (a backtick substitution is
# opaque to the lexer, so its commands are seen only here). A printf `%(...)T` time format is found apart, by
# _bound_marks, in one pass whatever its length.
_RAW_BOUND_RE = re.compile(r"SECONDS|EPOCHREALTIME|\(\(|\bdate[ \t]+['\"]?\+%s|`[ \t]*expr\b")
# The pieces of a printf time format: an opener, a closer followed by T, a plain closer.
_TIME_FORMAT_RE = re.compile(r"%\(|\)T|\)")
# Command words that update a counter arithmetically: a bound marker wherever they run in the loop.
_COUNTERS = frozenset(("let", "expr"))
# The quoting characters the `sleep` prefilter removes first (`sl""eep` runs sleep); a newline too, for `\<newline>`.
_PREFILTER_DROP = {ord(c): None for c in "'\"\\\n"}
# The command-running wrappers stepped over to the command they run: name -> (the options that take a separate
# value word, the operands before the command). Options are the `-` words up to `--` or the first other word
# (an attached `-n5` or `--opt=v` is one word); redirections anywhere among them are stepped over too.
_WRAPPERS = {
    "builtin": ((), 0),
    "chrt": (("-T", "-P", "-D", "--sched-runtime", "--sched-period", "--sched-deadline"), 1),  # the priority
    "command": ((), 0),
    "env": (("-u", "-C", "-S", "--unset", "--chdir", "--split-string"), 0),
    "exec": (("-a",), 0),
    "ionice": (("-c", "-n", "-p", "-P", "-u", "--class", "--classdata", "--pid", "--pgid", "--uid"), 0),
    "nice": (("-n", "--adjustment"), 0),
    "nohup": ((), 0),
    "setsid": ((), 0),
    "stdbuf": (("-i", "-o", "-e", "--input", "--output", "--error"), 0),
    "sudo": (("-u", "-g", "-C", "-D", "-h", "-p", "-r", "-t", "-U", "-T", "--user", "--group", "--close-from",
              "--chdir", "--host", "--prompt", "--role", "--type", "--other-user", "--command-timeout"), 0),
    "taskset": ((), 1),  # the mask or cpu list
    "time": (("-o", "-f", "--output", "--format"), 0),
    "timeout": (("-s", "-k", "--signal", "--kill-after"), 1),  # the duration
}
# The wrappers that read a lone `-` as an option: `env -` is `env -i`.
_DASH_OPTION = frozenset(("env",))
_LOOP_WORD_RE = re.compile(r"\b(?:while|until)\b")
# The opt-out: a comment starting `# wait-ok` (then a colon, a blank, or nothing) that ends the command. The
# lookbehind keeps a mid-word `#` out; _opted_out confirms the lexer reads the tail as a comment.
_OPT_OUT_RE = re.compile(r"(?<![^ \t\n;&|()])#[ \t]*wait-ok(?:[ \t:][^\n]*)?\Z")
_TERMINATORS = frozenset((";", "&", ";;", ";&", ";;&"))
_COMPOUND_OPENERS = frozenset(("while", "until", "for", "select", "if", "{"))
_PROBE_FLAGS = frozenset(("-e", "-f", "-s"))
_GREP_NAMES = frozenset(("grep", "egrep", "fgrep"))
_GREP_PATTERN_OPTS = frozenset(("-e", "-f", "--regexp", "--file"))
_GREP_VALUE_OPTS = frozenset(("-m", "-A", "-B", "-C", "-d", "-D"))
_MAX_STATS = 8  # stat calls for the missing-directory note
# The read-loop exemption (_flagged): each command that reads the loop's input, with its option letters that
# take a value; and the redirections that open something (a file, a here-document, a here-string), so a reader
# carrying one on the descriptor it reads reopens its input each time instead of reading the loop's input.
_READERS = {"read": "adinNptu", "mapfile": "dnOsuCc", "readarray": "dnOsuCc"}
_REOPENERS = frozenset(("<", "<>", "<<", "<<-", "<<<"))
_MAX_RESERVED_RUN = 64  # reserved words walked back from a reader (_own_input)
_NOTE_MAX = 200  # characters of a directory name quoted in the note

_REASON = (
    "Blocked (unbounded-wait): this background command polls in a while/until loop with sleep and no bound (no "
    "timeout, counter, or deadline). If the condition never becomes true (a wrong path, a producer that died), it "
    "runs for hours and never sends its completion notification. Bound it: timeout 900 bash -c 'until grep -q PAT "
    "FILE; do sleep 5; done' (exits 124 on expiry), or use a deadline: end=$((SECONDS+900)); until grep -q PAT "
    "FILE; do [ $SECONDS -ge $end ] && exit 124; sleep 5; done.{note} For a deliberate unbounded watcher, end the "
    "command with '# wait-ok: <reason>'.")
_NOTE = " Note: {dir} does not exist, so the polled path can never appear there."


class _Unfollowed(Exception):
    """A structure the scan does not follow (a case or coproc, an unmatched opener or closer): fail open."""


def _is_worker(env):
    """True for a verification worker process: the worker kill-switch variable set to "1", or one of its legacy
    spellings (one set to "1", another present with any value, even empty)."""
    if env.get("AIQT_HOOKS_WORKER") == "1":
        return True
    return env.get("ORCH_WORKER") == "1" or "ORCH_VERIFY_OWNER" in env


def _cs_flags(toks):
    """Per token of one stream: True when a word there is in command position (the vendored _at_command_start,
    fed the stream's tokens so far)."""
    flags, out = [], []
    for t in toks:
        flags.append(_at_command_start(out))
        out.append(t)
    return flags


def _is_fd_word(toks, k):
    """True when toks[k] is a descriptor number or {name} written directly against a redirection operator."""
    u = toks[k]
    if k + 1 >= len(toks):
        return False
    nxt = toks[k + 1]
    return (nxt[0] == "op" and nxt[1] in _REDIRECTS and nxt[3] == u[4]
            and (u[1].isdigit() or bool(_FD_VAR_RE.match(u[1]))))


def _command_words(toks, cs, heads=None):
    """[(index, name)] of each simple command's command word in one stream: from each command-position word
    that is not a bare reserved word, or command-position redirection operator, past assignment words,
    redirections, and the _WRAPPERS with their options, option values, and operands (each wraps the command
    after it); name is the word's basename. heads, when given, maps each command word's index to the index of
    its simple command's first token."""
    found, n, j = [], len(toks), 0
    while j < n:
        t = toks[j]
        if not cs[j] or not ((t[0] == "w" and (t[2] or t[1] not in _RESERVED))
                             or (t[0] == "op" and t[1] in _REDIRECTS)):
            j += 1
            continue
        k, wrap = j, None  # wrap: [value options, operands left, options open, a value pending, `-` an option]
        while k < n:
            u = toks[k]
            if u[0] == "op" and u[1] in _REDIRECTS:
                k += 2  # the operator and its target word
                continue
            if u[0] != "w":
                break
            if _is_fd_word(toks, k):
                k += 1
                continue
            v = u[1]
            if wrap is not None:
                k += 1
                if wrap[3]:
                    wrap[3] = False  # an option's value
                elif wrap[2] and v.startswith("-") and (v != "-" or wrap[4]):
                    wrap[2] = v != "--"
                    wrap[3] = v in wrap[0]
                elif wrap[1]:
                    wrap[1], wrap[2] = wrap[1] - 1, False  # an operand
                else:
                    wrap, k = None, k - 1  # the wrapped command's own word
                continue
            name = v[v.rfind("/") + 1:]
            if _ASSIGN_RE.match(v):
                k += 1
                continue
            if name in _WRAPPERS:
                wrap = [_WRAPPERS[name][0], _WRAPPERS[name][1], True, False, name in _DASH_OPTION]
                k += 1
                continue
            found.append((k, name))
            if heads is not None:
                heads[k] = j
            break
        j = k + 1
    return found


def _args(toks, c, stop):
    """The argument words of the simple command whose command word is toks[c], redirections dropped, read no
    further than toks[stop - 1]: the caller passes the stream's next command word as stop, so the spans read
    for a stream's command words never overlap (a word read as a command inside another's arguments, as after
    an argument spelled like a reserved word, would otherwise make every later command reread the tail)."""
    out, k, n = [], c + 1, min(stop, len(toks))
    while k < n:
        u = toks[k]
        if u[0] == "op" and u[1] in _REDIRECTS:
            k += 2
            continue
        if u[0] != "w":
            break
        if not _is_fd_word(toks, k):
            out.append(u[1])
        k += 1
    return out


def _next_start(words, i):
    """The token index of the command word after words[i], or past any stream (the stop for _args)."""
    return words[i + 1][0] if i + 1 < len(words) else 1 << 62


def _word_marks(toks, words):
    """(sleeps, bounds): the indices of one stream's sleep commands, and of its counter commands (_COUNTERS) and
    epoch `date` calls (any options, a `+...%s` format argument)."""
    sleeps, bounds = set(), set()
    for i, (k, name) in enumerate(words):
        if name == "sleep":
            sleeps.add(k)
        elif name in _COUNTERS or (name == "date" and any(a.startswith("+") and "%s" in a
                                                             for a in _args(toks, k, _next_start(words, i)))):
            bounds.add(k)
    return sleeps, bounds


def _stream_marks(toks):
    """(runs sleep, has a bound marker: a numeric test word, a counter command, or an epoch date call) for one
    substitution stream, its own substitutions included."""
    sleep_at, bound_at = _word_marks(toks, _command_words(toks, _cs_flags(toks)))
    sleeps, nums = bool(sleep_at), bool(bound_at)
    for t in toks:
        if t[0] == "w":
            nums = nums or t[1] in _NUMERIC_TESTS
            for sub in t[5]:
                s, m = _stream_marks(sub)
                sleeps, nums = sleeps or s, nums or m
    return sleeps, nums


def _loops(toks, cs):
    """(loops, background) for the top-level stream: loops lists (open, do, done, outer) for each while or until
    loop, outer being the and-or list the loop is an element of; background[i] is True when list i, or a list
    enclosing it, ends with `&`. Raises _Unfollowed on a case or coproc, or an unmatched opener or closer."""
    parent, amp = [None], [False]
    frames = [[None, 0, None, None, None]]  # kind, current list, outer list, open index, do index
    loops, prev = [], None

    def close(ends_amp):
        f = frames[-1]
        if ends_amp:
            amp[f[1]] = True
        parent.append(f[2])
        amp.append(False)
        f[1] = len(amp) - 1

    def push(kind, k):
        outer = frames[-1][1]
        parent.append(outer)
        amp.append(False)
        frames.append([kind, len(amp) - 1, outer, k, None])

    def pop(kinds):
        if len(frames) < 2 or frames[-1][0] not in kinds:
            raise _Unfollowed("an unmatched closer")
        return frames.pop()

    for k, t in enumerate(toks):
        if t[0] == "nl":
            if not (prev is not None and prev[0] == "op" and prev[1] in _CONTINUE_OPS):
                close(False)
        elif t[0] == "op":
            if t[1] in _TERMINATORS:
                close(t[1] == "&")
            elif t[1] == "(":
                push("(", k)
            elif t[1] == ")":
                pop(("(",))
        elif cs[k] and not t[2]:
            v = t[1]
            if v in _COMPOUND_OPENERS:
                push(v, k)
            elif v in ("case", "coproc"):
                raise _Unfollowed(v)
            elif v == "do":
                if frames[-1][0] not in _LOOPS or frames[-1][4] is not None:
                    raise _Unfollowed("a do outside a loop header")
                frames[-1][4] = k
            elif v == "done":
                f = pop(_LOOPS)
                if f[4] is None:
                    raise _Unfollowed("a loop with no do")
                if f[0] in ("while", "until"):
                    loops.append((f[3], f[4], k, f[2]))
            elif v == "fi":
                pop(("if",))
            elif v == "}":
                pop(("{",))
        prev = t
    if len(frames) != 1:
        raise _Unfollowed("an unclosed compound command")
    background = []
    for i, p in enumerate(parent):  # a list's parent is always created before it
        background.append(amp[i] or (p is not None and background[p]))
    return loops, background


def _flagged(toks, text, run_in_background):
    """(flagged, words): flagged lists (open, do, done) for each unbounded background polling loop of the
    top-level stream; words are the stream's command words (_command_words)."""
    cs, heads = _cs_flags(toks), {}
    words = _command_words(toks, cs, heads)
    loops, background = _loops(toks, cs)
    if not loops:
        return [], words
    sleep_at, bound_at = _word_marks(toks, words)
    contexts, readers = _contexts(toks, cs), []  # readers: (index, start of the pipe feeding it, or -1)
    for i, (k, name) in enumerate(words):
        if name in _READERS:
            fd, reopens, diverted = _reader_input(toks, heads[k], k, _next_start(words, i), name)
            fed = None if reopens else _feeding_pipe(toks, cs, heads[k], contexts, fd, diverted)
            if fed is not None:
                readers.append((k, fed))
    fed_min = _MinTable([fed for _, fed in readers])
    reader_at = [k for k, _ in readers]
    sleeps, nums = [0], [0]  # prefix sums over the top-level tokens, substitutions included
    joins, bangs = [0], [0]  # prefix sums: newlines and operators; `!` negations
    for k, t in enumerate(toks):
        joins.append(joins[-1] + (t[0] == "nl" or (t[0] == "op" and t[1] not in _REDIRECTS)))
        bangs.append(bangs[-1] + (_is_bare_word(t, "!") and cs[k]))  # a negation, not a redirection target
        s, m = k in sleep_at, k in bound_at or (t[0] == "w" and t[1] in _NUMERIC_TESTS)
        for sub in t[5]:
            ss, sm = _stream_marks(sub)
            s, m = s or ss, m or sm
        sleeps.append(sleeps[-1] + s)
        nums.append(nums[-1] + m)
    marks = _bound_marks(text)
    starts = [k for k, _ in words]
    flagged = []
    for open_, do, done, outer in loops:
        if not (run_in_background or background[outer]):
            continue
        if sleeps[done + 1] == sleeps[open_] or nums[done + 1] != nums[open_]:
            continue  # no sleep in the loop, or a counter test, counter update, or epoch date call in it
        i = bisect.bisect_left(marks, toks[open_][3])
        if i < len(marks) and marks[i] < toks[done][4]:
            continue  # a raw bound marker in the loop's text
        lo, hi = bisect.bisect_right(reader_at, open_), bisect.bisect_left(reader_at, do)
        own = lo < hi and fed_min.min(lo, hi) <= open_  # a reader in the condition fed from outside the loop
        if own and not _waits_for_input(toks, open_, do, words, starts, joins, bangs):
            continue  # a read loop: allowed without judging whether it ends at end of input
        flagged.append((open_, do, done))
    return flagged, words


def _reader_input(toks, head, k, stop, name):
    """(fd, reopens, diverted) for the reader toks[k] (name: read, mapfile, or readarray), its simple command
    starting at toks[head]: fd is the descriptor it reads (0, or its `-u N`; None when that value is not a
    literal number); reopens is True when a `<`, `<>`, here-document, or here-string opens something on fd,
    so each run rereads the same file rather than the loop's input; diverted is True when an input
    redirection of any kind (a descriptor copy `<&3` too) replaces descriptor 0 on the command, so a pipe
    before it does not feed the reader. stop is the stream's next command word, bounding the scan."""
    args, i, fd = _args(toks, k, stop), 0, 0
    while i < len(args):  # the reader's options, up to its first operand
        a = args[i]
        i += 1
        if a == "--" or not a.startswith("-") or a == "-":
            break
        for n, ch in enumerate(a[1:], 1):
            if ch in _READERS[name]:
                value = a[n + 1:]
                if not value:
                    value = args[i] if i < len(args) else ""
                    i += 1
                if ch == "u":
                    fd = int(value) if value.isdigit() else None
                break
    reopens, diverted, j = False, False, head
    while j < min(stop, len(toks)):
        u = toks[j]
        if u[0] == "op" and u[1] in _REDIRECTS:
            on = 0 if u[1].startswith("<") else 1
            if j > head and _is_fd_word(toks, j - 1):
                on = int(toks[j - 1][1]) if toks[j - 1][1].isdigit() else None
            if _feeds_zero(toks, j):
                diverted = True
            if u[1] in _REOPENERS and fd is not None and on == fd:
                reopens = True
            j += 2
        elif u[0] == "w":
            j += 1
        else:
            break
    return fd, reopens, diverted


class _MinTable:
    """Range minimum over a fixed list in constant time per query (a sparse table, built in n log n)."""

    def __init__(self, values):
        self.rows = [list(values)]
        width = 1
        while 2 * width <= len(values):
            last = self.rows[-1]
            self.rows.append([min(last[i], last[i + width]) for i in range(len(last) - width)])
            width *= 2

    def min(self, lo, hi):
        """The minimum of values[lo:hi], hi > lo."""
        level = (hi - lo).bit_length() - 1
        row = self.rows[level]
        return min(row[lo], row[hi - (1 << level)])


def _feeds_zero(toks, j):
    """True when the redirection operator toks[j] gives descriptor 0 other input: on descriptor 0 (implicit for
    a `<` form, written for any form, compared as a number, so `00` is 0), an input redirection that opens
    something (`<`, `<>`, here-document, here-string), or a copy or move of another descriptor (`<&3`, `0>&3`,
    `<&3-`, or a target that is not a literal number). A copy or move of descriptor 0 onto itself (`<&0`,
    `<&00`, `<&0-`), a close (`<&-`), and an output redirection that is not a copy give it no other input."""
    u = toks[j]
    on = 0 if u[1].startswith("<") else None
    if j > 0 and _is_fd_word(toks, j - 1):
        on = int(toks[j - 1][1]) if toks[j - 1][1].isdigit() else None
    if on != 0:
        return False
    if u[1] not in ("<&", ">&"):
        return u[1].startswith("<")
    target = toks[j + 1][1] if j + 1 < len(toks) and toks[j + 1][0] == "w" else ""
    base = target[:-1] if target.endswith("-") else target
    if not base:
        return False  # `<&-`: a close, no input at all
    return not (base.isdigit() and int(base) == 0)


def _transparent(toks, cs, p):
    """True when toks[p] is skipped when looking back from a command for a pipe feeding it: a newline, a `!`
    or `time` word in command position, or a `-p` or `--` word right after such a `time` (as `time -p --`),
    and nowhere else. A `!` after a pipe is a syntax error the oracle withdraws. A `time` word after a pipe is
    not always a syntax error: when a simple command follows it on the same line, bash runs it as the external
    `time` command, and when the reader stands on the next line (`: | time`, then `read l`), the pipe feeds
    only that `time` command, yet the skip carries it on to the reader, so such a finite loop is denied (a
    disclosed residual)."""
    t = toks[p]
    if t[0] == "nl":
        return True
    if not (cs[p] and t[0] == "w" and not t[2]):
        return False
    if t[1] in ("!", "time"):
        return True
    if t[1] == "--" and p > 0 and _is_bare_word(toks[p - 1], "-p"):
        p -= 1
    return t[1] in ("-p", "--") and p > 0 and _is_bare_word(toks[p - 1], "time")


def _contexts(toks, cs):
    """(fed, in_test), one value per token: fed[k] is the index where the innermost pipe-fed receiver around
    toks[k] opens (a `{ ...; }` group, `( ... )` subshell, `if`, or loop standing after a `|` or `|&`, with
    newlines, `!`, and `time` allowed between), or -1 when no pipe feeds toks[k]'s descriptor 0; a receiver
    whose own redirection replaces descriptor 0 (`{ read l; } <&3`) shields what it holds. in_test[k] is True
    when toks[k] lies inside a `[[ ... ]]` conditional expression, whose words are data, never commands."""
    n, in_test, test = len(toks), [], False
    closers = {"{": ("}",), "(": (")",), "if": ("fi",), "while": ("done",), "until": ("done",),
               "for": ("done",), "select": ("done",)}
    opener_at, stack, shielded = [], [], set()
    for k, t in enumerate(toks):  # pass 1: the `[[ ]]` spans, and which receivers replace descriptor 0
        if test:
            test = not _is_bare_word(t, "]]")
            in_test.append(True)
            opener_at.append(None)
            continue
        word = t[1] if (t[0] == "w" and not t[2] and cs[k]) or _is_op(t, "(") or _is_op(t, ")") else None
        if cs[k] and _is_bare_word(t, "[["):
            test = True
        elif word in closers:
            stack.append((k, closers[word]))
        elif stack and word in stack[-1][1]:
            opener = stack.pop()[0]
            j = k + 1
            while j < n and (toks[j][0] == "op" and toks[j][1] in _REDIRECTS or _is_fd_word(toks, j)):
                if toks[j][0] == "op":
                    if _feeds_zero(toks, j):
                        shielded.add(opener)
                    j += 2
                else:
                    j += 1
        in_test.append(test)
        opener_at.append(word if word in closers else None)
    fed, stack, last = [], [-1], None
    for k, t in enumerate(toks):  # pass 2: the innermost pipe-fed receiver around each token
        if opener_at[k] is not None:
            pipe = last is not None and toks[last][0] == "op" and toks[last][1] in _PIPES
            stack.append(-1 if k in shielded else (k if pipe else stack[-1]))
        elif not in_test[k] and len(stack) > 1 and ((cs[k] and t[0] == "w" and not t[2]
                                                     and t[1] in ("}", "fi", "done")) or _is_op(t, ")")):
            stack.pop()
        fed.append(stack[-1])
        if not _transparent(toks, cs, k):
            last = k
    return fed, in_test


def _feeding_pipe(toks, cs, head, contexts, fd, diverted):
    """For the reader whose simple command starts at toks[head], the index where the pipe feeding it starts,
    -1 when none does, or None when it is not a reader at all. It must truly start a command: not a word
    inside `[[ ... ]]`, and not a word after an argument spelled like a reserved word (`grep -q while read`
    runs grep, not read), so the run of bare reserved words before it must itself start in command position
    (past _MAX_RESERVED_RUN such words it is taken as started, toward allow). A pipe feeds it only when it
    reads descriptor 0 undiverted (fd 0, no input redirection replacing it): the pipe directly before it
    (newlines, `!`, and `time` allowed between), else the innermost pipe-fed receiver around it (contexts,
    from _contexts). The caller compares the index with the loop: a pipe opening at or before the loop's
    own keyword is the loop's input arriving from outside (`producer | while read l`), not a poll."""
    fed, in_test = contexts
    if in_test[head]:
        return None
    p, steps = head - 1, 0
    while p >= 0 and toks[p][0] == "w" and not toks[p][2] and toks[p][1] in _RESERVED:
        if not cs[p]:
            return None
        p, steps = p - 1, steps + 1
        if steps > _MAX_RESERVED_RUN:
            break
    if fd != 0 or diverted:
        return -1
    p = head - 1
    while p >= 0 and _transparent(toks, cs, p):
        p -= 1
    if p >= 0 and toks[p][0] == "op" and toks[p][1] in _PIPES:
        return p
    return fed[head]


def _waits_for_input(toks, open_, do, words, starts, joins, bangs):
    """True when the loop's whole condition is one `read` that spins at end of input: negated under `while`
    (`while ! read ...`), or not negated under `until` (`until read ...`). The condition is one simple
    command, `!` negations before it, blank lines around it, and no other command, through at most one
    `{ ...; }` or `( ... )` layer (`while { ! read l; }`, `until (read l)`); joins and bangs are prefix sums of
    the newline and operator tokens and of the `!` negations."""
    start, end = open_ + 1, do
    while start < end and toks[start][0] == "nl":
        start += 1
    while end > start and (toks[end - 1][0] == "nl" or _is_op(toks[end - 1], ";")):
        end -= 1
    lo = start
    while lo < end and (_is_bare_word(toks[lo], "!") or _is_bare_word(toks[lo], "time")):
        lo += 1  # `!` negations, and a `time` keyword with its `-p` and `--`, before the read or its one group
        if _is_bare_word(toks[lo - 1], "time"):
            for option in ("-p", "--"):
                if lo < end and _is_bare_word(toks[lo], option):
                    lo += 1
    hi = end
    while hi - 2 > lo and toks[hi - 2][0] == "op" and toks[hi - 2][1] in _REDIRECTS and toks[hi - 1][0] == "w":
        hi -= 2  # a redirection on the grouping layer (`{ read l; } 2>/dev/null`)
        if hi - 1 > lo and _is_fd_word(toks, hi - 1):
            hi -= 1
    if lo + 1 < hi and ((_is_bare_word(toks[lo], "{") and _is_bare_word(toks[hi - 1], "}"))
                        or (_is_op(toks[lo], "(") and _is_op(toks[hi - 1], ")"))):
        lo, hi = lo + 1, hi - 1  # one grouping layer: the condition is what it holds
        while lo < hi and toks[lo][0] == "nl":
            lo += 1
        while hi > lo and (toks[hi - 1][0] == "nl" or _is_op(toks[hi - 1], ";")):
            hi -= 1
    else:
        lo, hi = start, end
    if joins[hi] != joins[lo]:
        return False
    i = bisect.bisect_left(starts, lo)
    if not (i < len(starts) and starts[i] < hi and words[i][1] == "read"
            and (i + 1 >= len(starts) or starts[i + 1] >= hi)):
        return False
    negated = (bangs[starts[i]] - bangs[start]) % 2 == 1
    return negated == (toks[open_][1] == "while")


def _bound_marks(text):
    """The sorted positions of the raw bound markers in text: _RAW_BOUND_RE's matches, and each printf
    `%(...)T` time format (any format of any length: conservative), found in one left-to-right pass over the
    openers and closers, since a format ends at its first `)`: an opener is pending until a closer, and a
    closer followed by T completes the pending opener's format, marked at that opener."""
    marks = [m.start() for m in _RAW_BOUND_RE.finditer(text)]
    pending = None
    for m in _TIME_FORMAT_RE.finditer(text):
        piece = m.group()
        if piece == "%(":
            pending = m.start() if pending is None else pending
        elif pending is not None:
            if piece == ")T":
                marks.append(pending)
            pending = None
    return sorted(marks)


def _literal(value):
    """True when a word's value is a literal path: no substitution, parameter, glob, or leading tilde."""
    return bool(value) and _SUB not in value and not any(c in value for c in "$*?[") and not value.startswith("~")


def _probe_paths(name, args):
    """The literal paths a condition probe polls: test/[ with -e, -f, or -s; ls; the grep family's files."""
    if name in ("test", "["):
        return [args[i + 1] for i in range(len(args) - 1) if args[i] in _PROBE_FLAGS]
    if name == "ls":
        return [a for a in args if not a.startswith("-")]
    if name not in _GREP_NAMES:
        return []
    plain, pattern_given, skip = [], False, False
    for a in args:
        if skip:
            skip = False
        elif a in _GREP_PATTERN_OPTS or a in _GREP_VALUE_OPTS:
            pattern_given, skip = pattern_given or a in _GREP_PATTERN_OPTS, True
        elif a.startswith(("--regexp=", "--file=")):
            pattern_given = True
        elif not a.startswith("-"):
            plain.append(a)
    return plain if pattern_given else plain[1:]


def _note(toks, flagged, words, cwd):
    """The missing-directory note for the first polled literal path whose parent directory does not exist, or
    an empty string. A relative path needs an absolute cwd; at most _MAX_STATS stat calls. Nested conditions
    overlap, so the loops are taken in text order and each command word is examined once: seen is the index
    of the first word not yet examined, and every word before it inside a later condition was examined."""
    stats, seen = 0, 0
    starts = [k for k, _ in words]
    for open_, do, _ in sorted(flagged):
        i = max(bisect.bisect_right(starts, open_), seen)
        while i < len(starts) and starts[i] < do:
            k, name = words[i]
            stop = _next_start(words, i)
            i += 1
            for path in _probe_paths(name, _args(toks, k, stop)):
                if not _literal(path):
                    continue
                if not os.path.isabs(path):
                    if not (isinstance(cwd, str) and os.path.isabs(cwd)):
                        continue
                    path = os.path.join(cwd, path)
                parent = os.path.dirname(path)
                if not parent or parent == path:
                    continue
                if stats >= _MAX_STATS:
                    return ""
                stats += 1
                try:
                    os.stat(parent)
                except (FileNotFoundError, NotADirectoryError):
                    shown = parent if len(parent) <= _NOTE_MAX else parent[:_NOTE_MAX] + "..."
                    return _NOTE.format(dir=shown)
                except (OSError, ValueError):
                    continue
        seen = max(seen, i)
    return ""


def _opted_out(text):
    """True when the command ends with the `# wait-ok` comment: the marker regex matches on the last line, and
    the lexer reads the text from the marker on as a comment (the tokens with and without it are the same)."""
    s = text.rstrip(" \t\n")
    m = _OPT_OUT_RE.search(s, s.rfind("\n") + 1)
    if not m:
        return False
    try:
        return _tokenize(s[:m.start()]) == _tokenize(s)
    except ValueError:
        return False


def _verdict(cmd, run_in_background, cwd, oracle=None):
    """None to allow, or the deny's note (an empty string, or the missing-directory sentence). A command over
    _SCAN_LIMIT bytes of UTF-8 is not scanned: a truncated prefix could drop its trailing opt-out."""
    if len(cmd) > _SCAN_LIMIT or len(cmd.encode("utf-8", "surrogatepass")) > _SCAN_LIMIT:
        return None
    text = _bounded(cmd)
    if ("sleep" not in text.translate(_PREFILTER_DROP) or not _LOOP_WORD_RE.search(text)
            or _opted_out(text)):
        return None
    try:
        toks = _drop_declarations(_tokenize(text), {})
        flagged, words = _flagged(toks, text, run_in_background)
    except (ValueError, _Unfollowed):
        return None  # unparseable or unfollowable: fail open
    if not flagged:
        return None
    if (oracle or _bash_syntax_ok)(text) is False:
        return None  # bash rejects the command: it runs nothing, fail open
    return _note(toks, flagged, words, cwd)


def _decide(payload, env, oracle=None):
    """The deny object for a parsed payload, or None to allow."""
    if _is_worker(env) or not isinstance(payload, dict) or "agent_id" in payload:
        return None
    if payload.get("tool_name") != "Bash" or payload.get("hook_event_name", "PreToolUse") != "PreToolUse":
        return None
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return None
    cmd = tool_input.get("command")
    if not isinstance(cmd, str) or not cmd:
        return None
    note = _verdict(cmd, tool_input.get("run_in_background") is True, payload.get("cwd"), oracle)
    if note is None:
        return None
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                   "permissionDecisionReason": _REASON.format(note=note)}}


def _emit_line(text):
    """Write one line to stdout and flush it. On any output failure (a closed pipe, a full device, no stdout at
    all) point descriptor 1 at /dev/null, so the interpreter's shutdown flush cannot fail either; if even that
    rescue fails, end the process at once with status 0 (no retry flush): the hook always exits 0."""
    try:
        sys.stdout.write(text + "\n")
        sys.stdout.flush()
    except Exception:
        try:
            fd = os.open(os.devnull, os.O_WRONLY)
            try:
                os.dup2(fd, 1)
            finally:
                os.close(fd)
        except Exception:
            os._exit(0)


def main(argv):
    """The hook: always 0, output only a deny line. `--self-test` alone runs the self-test instead; any other
    argv returns 0 silently before stdin is read."""
    if not isinstance(argv, (list, tuple)) or not all(isinstance(a, str) for a in argv) or not argv:
        return 0  # a bad argv: fail open, reading nothing
    if list(argv[1:]) == ["--self-test"]:
        return _self_test()
    if len(argv) != 1:
        return 0  # an unsupported argument: fail open, reading nothing
    try:
        if _is_worker(os.environ):
            return 0
        out = _decide(_read_payload(), os.environ)
    except Exception:
        return 0  # malformed or unreadable input, or an internal error: fail open
    if out is not None:
        _emit_line(json.dumps(out))
    return 0


def _self_test():
    import hashlib
    import io
    import shutil
    import subprocess
    import tempfile
    import tokenize
    import unittest

    here = os.path.abspath(__file__)
    no_oracle = lambda command: None  # noqa: E731 (the oracle unavailable: the lexical verdict stands)
    reject = lambda command: False  # noqa: E731 (bash rejects the command)

    def decide(command, bg=True, cwd=None, oracle=no_oracle, env=None, **extra):
        tool_input = {"command": command}
        if bg is not None:
            tool_input["run_in_background"] = bg
        payload = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": tool_input}
        if cwd is not None:
            payload["cwd"] = cwd
        payload.update(extra)
        return _decide(payload, {} if env is None else env, oracle)

    def run_hook(data, env=None, timeout=30):
        """The hook run as a process: (status, stdout, stderr); env is the whole environment."""
        p = subprocess.run([sys.executable, "-I", "-S", "-B", here], input=data, capture_output=True,
                           env={"LC_ALL": "C"} if env is None else env, timeout=timeout)
        return p.returncode, p.stdout, p.stderr

    def payload_bytes(command, bg=True):
        return json.dumps({"hook_event_name": "PreToolUse", "tool_name": "Bash",
                           "tool_input": {"command": command, "run_in_background": bg}}).encode()

    def vendor_blocks():
        """name -> (fence header fields, block bytes) read from this file's own fences."""
        lines = open(here, "rb").read().split(b"\n")
        blocks, name, body, head = {}, None, [], None
        for line in lines:
            if name is None and line.startswith(b"# BEGIN VENDOR "):
                head = line.decode().split()
                name, body = head[3], []
            elif name is not None and line == b"# END VENDOR":
                blocks[name] = (head, b"".join(x + b"\n" for x in body))
                name = None
            elif name is not None:
                body.append(line)
        return blocks

    loop1 = "until grep -q X f; do sleep 5; done"
    loop2 = "while ! test -f x; do sleep 1; done"
    loop3 = "while true; do grep -q X f && break; sleep 5; done"

    class T(unittest.TestCase):
        def assertDeny(self, command, **kw):
            out = decide(command, **kw)
            self.assertIsNotNone(out, command)
            h = out["hookSpecificOutput"]
            self.assertEqual((h["hookEventName"], h["permissionDecision"]), ("PreToolUse", "deny"))
            self.assertIn("Blocked (unbounded-wait)", h["permissionDecisionReason"])
            return h["permissionDecisionReason"]

        def assertAllow(self, command, **kw):
            self.assertIsNone(decide(command, **kw), command)

        # plan 4.8 case 1 to 4: the flip cases (they pass only with detection present)
        def test_01_flip_until_probe(self):
            self.assertDeny(loop1)

        def test_02_flip_while_not_probe(self):
            self.assertDeny(loop2)

        def test_03_flip_while_true_break(self):
            self.assertDeny(loop3)

        def test_04_flip_trailing_amp_with_detach_opt_out(self):
            self.assertDeny(loop3 + " &  # detach-ok: watcher", bg=False)

        def test_04b_flip_trailing_amp_forms(self):
            for c in (loop1 + " &", loop1 + " > log 2>&1 &", "{ " + loop1 + "; } &", "( " + loop2 + " ) &",
                      loop1 + " && echo ok &", "if true; then " + loop1 + "; fi &", "echo a; " + loop1 + " &",
                      "for i in 1 2; do " + loop1 + "; done &", loop1 + " &&\necho ok &",
                      "until grep -q X f; do x=$(sleep 5); done &"):
                self.assertDeny(c, bg=False)

        # case 5: the foreground allows
        def test_05_foreground_allowed(self):
            for c in (loop1, loop2, loop3, loop1 + "; echo x &", "sleep 1 & " + loop1):
                self.assertAllow(c, bg=False)
                self.assertAllow(c, bg="true")  # only boolean true is the background path
                self.assertAllow(c, bg=None)

        # case 6 to 10: bounded or out-of-grammar loops allowed
        def test_06_for_loop_allowed(self):
            self.assertAllow("for i in $(seq 60); do grep -q X f && break; sleep 5; done")
            self.assertAllow("select x in a b; do sleep 1; done")

        def test_07_read_loop_allowed(self):
            self.assertAllow("while read l; do sleep 0.1; done < f")
            self.assertAllow("while IFS= read -r l; do sleep 0.1; done < f")

        def test_08_seconds_deadline_allowed(self):
            self.assertAllow("end=$((SECONDS+900)); until grep -q X f; do [ $SECONDS -ge $end ] && exit 124; "
                             "sleep 5; done")
            self.assertAllow("until grep -q X f || test $SECONDS = 900; do sleep 5; done")

        def test_09_arithmetic_counter_allowed(self):
            self.assertAllow("n=0; while (( n++ < 60 )); do grep -q X f && break; sleep 5; done")

        def test_10_timeout_wrapped_string_allowed(self):
            self.assertAllow("timeout 900 bash -c 'until grep -q X f; do sleep 5; done'")

        def test_markers_each_allowed(self):
            for c in ("n=0; until grep -q X f; do [ $n -lt 60 ] || exit 1; n=$((n+1)); sleep 5; done",
                      "until grep -q X f; do [ \"$(date +%s)\" -gt 9 ] && exit; sleep 5; done",
                      "d=9; until grep -q X f || [ `date +%s` = $d ]; do sleep 5; done",
                      "while [ \"$n\" -ne 0 ]; do sleep 1; done"):
                self.assertAllow(c)

        def test_probe_timeout_is_not_a_bound(self):
            self.assertDeny("until timeout 5 grep -q X f; do sleep 5; done")

        def test_no_sleep_command_allowed(self):
            self.assertAllow("until pgrep sleep; do :; done")
            self.assertAllow("echo while; echo sleep")
            self.assertAllow("echo 'until x; do sleep 1; done'")

        def test_sleep_found_past_prefixes(self):
            self.assertDeny("until grep -q X f; do X=1 command sleep 5; done")
            self.assertDeny("until grep -q X f; do 2>/dev/null /bin/sleep 5; done")
            self.assertDeny("while sleep 5; do grep -q X f && break; done")

        def test_nested_loops(self):
            self.assertDeny("for i in 1 2; do until grep -q X f; do sleep 1; done; done")
            self.assertDeny("while true; do for i in 1 2 3; do sleep 1; done; done")

        # case 11: the opt-out
        def test_11_wait_ok_opt_out(self):
            self.assertAllow(loop1 + "  # wait-ok: deliberate watcher")
            self.assertAllow(loop1 + " #wait-ok")
            self.assertAllow(loop1 + " &  # wait-ok: deliberate watcher\n", bg=False)
            self.assertDeny(loop1 + "; echo a#wait-ok: mid-word")
            self.assertDeny(loop1 + " # wait-okay")
            self.assertDeny("echo '# wait-ok: x'\n" + loop1)

        # case 12: the missing-directory note
        def test_12_missing_directory_note(self):
            root = tempfile.mkdtemp()
            try:
                c = "until grep -q X missingdir/out.log; do sleep 5; done"
                reason = self.assertDeny(c, cwd=root)
                self.assertIn(" Note: " + os.path.join(root, "missingdir") + " does not exist", reason)
                reason = self.assertDeny("until [ -f " + os.path.join(root, "nope", "x") + " ]; do sleep 1; done")
                self.assertIn("Note: " + os.path.join(root, "nope"), reason)
                os.mkdir(os.path.join(root, "missingdir"))
                self.assertNotIn("Note:", self.assertDeny(c, cwd=root))
                self.assertNotIn("Note:", self.assertDeny(c))  # a relative path with no cwd: no note
                self.assertNotIn("Note:", self.assertDeny("until grep -q X $D/out; do sleep 5; done", cwd=root))
            finally:
                shutil.rmtree(root)

        # case 13: the syntax oracle
        def test_13_oracle(self):
            bad = loop1 + " ;;"
            self.assertAllow(bad, oracle=reject)
            self.assertDeny(bad, oracle=no_oracle)
            if _trusted_bash() is None:
                self.skipTest("no trusted bash: the real oracle is unavailable")
            self.assertIs(_bash_syntax_ok(bad), False)
            self.assertAllow(bad, oracle=None)
            self.assertDeny(loop1, oracle=None)

        # case 14: fail-open inputs
        def test_14_malformed_payloads(self):
            for p in (None, 7, "x", [], {}, {"tool_name": "Bash"}, {"tool_name": "Bash", "tool_input": "x"},
                      {"tool_name": "Bash", "tool_input": {"command": 7, "run_in_background": True}},
                      {"tool_name": "Bash", "tool_input": {"command": "", "run_in_background": True}},
                      {"tool_name": "Read", "tool_input": {"command": loop1, "run_in_background": True}},
                      {"tool_name": None, "tool_input": {"command": loop1, "run_in_background": True}}):
                self.assertIsNone(_decide(p, {}, no_oracle), p)
            self.assertIsNone(decide(loop1, hook_event_name="PostToolUse"))

        def test_14_subagent_payload(self):
            self.assertIsNone(decide(loop1, agent_id="a1"))
            self.assertIsNone(decide(loop1, agent_id=None))

        def test_14_worker_envs(self):
            for env in ({"AIQT_HOOKS_WORKER": "1"}, {"ORCH_WORKER": "1"}, {"ORCH_VERIFY_OWNER": ""},
                        {"ORCH_VERIFY_OWNER": "x"}, {"AIQT_HOOKS_WORKER": "0", "ORCH_WORKER": "1"}):
                self.assertIsNone(decide(loop1, env=env), env)
            for env in ({"AIQT_HOOKS_WORKER": "0"}, {"AIQT_HOOKS_WORKER": "yes"}, {"ORCH_WORKER": "0"}):
                self.assertIsNotNone(decide(loop1, env=env), env)

        def test_14_bad_argv(self):
            old = sys.stdout
            sys.stdout = io.StringIO()
            try:
                for argv in (None, 7, "--self-test", ["x", 3], {"a": 1}):
                    self.assertEqual(main(argv), 0)
                self.assertEqual(sys.stdout.getvalue(), "")
            finally:
                sys.stdout = old

        def test_14_process_fail_open(self):
            bad = (b"", b"not json", b"[1, 2]", b"7", b"\xff\xfe\x00garbage", b'{"tool_name": "Bash"',
                   payload_bytes(loop1)[:-1])
            for data in bad:
                self.assertEqual(run_hook(data), (0, b"", b""), data[:30])
            for extra in ({"AIQT_HOOKS_WORKER": "1"}, {"ORCH_WORKER": "1"}, {"ORCH_VERIFY_OWNER": ""}):
                env = dict({"LC_ALL": "C"}, **extra)
                self.assertEqual(run_hook(payload_bytes(loop1), env), (0, b"", b""), extra)

        def test_14_process_deny_is_one_json_line(self):
            rc, out, err = run_hook(payload_bytes(loop1))
            self.assertEqual((rc, err), (0, b""))
            self.assertTrue(out.endswith(b"\n") and out.count(b"\n") == 1, out)
            h = json.loads(out)["hookSpecificOutput"]
            self.assertEqual(h["permissionDecision"], "deny")
            self.assertEqual(run_hook(payload_bytes(loop1, bg=False)), (0, b"", b""))

        def test_14_directory_stdin_guard(self):
            if not os.path.exists("/bin/sh"):
                self.skipTest("no /bin/sh")
            root = tempfile.mkdtemp()
            fd = os.open(root, os.O_RDONLY)
            try:
                line = REGISTRATION.format(python=sys.executable, hook=here)
                p = subprocess.run(["/bin/sh", "-c", line], stdin=fd, capture_output=True, env={"LC_ALL": "C"},
                                   timeout=30)
                self.assertEqual((p.returncode, p.stdout, p.stderr), (0, b"", b""))
            finally:
                os.close(fd)
                shutil.rmtree(root)

        def test_unfollowed_structures_fail_open(self):
            for c in ("case a in a) " + loop1 + ";; esac", "coproc " + loop1, loop1 + "; done",
                      "until grep -q X f; sleep 5; done", "{ " + loop1, "f() { " + loop1 + "; }; f",
                      "until grep -q \"X f; do sleep 5; done", "x" * 70000 + "; " + loop1):
                self.assertAllow(c)

        # case 15: vendoring, cost, hangs, and non-ASCII input
        def test_15_vendor_hashes(self):
            blocks = vendor_blocks()
            self.assertEqual(set(blocks), set(_VENDOR))
            for name, (head, body) in blocks.items():
                ref, first, last, digest = _VENDOR[name]
                self.assertEqual(head[4:], ["from", "%s:%d-%d" % (ref, first, last)], name)
                self.assertEqual(hashlib.sha256(body).hexdigest(), digest, name)
                self.assertEqual(_strip_source(body.decode("ascii")), body.decode("ascii"), name)  # stripped
                compile(body, name, "exec")  # the stripped block is still whole Python

        def test_15_vendor_byte_identity(self):
            ref_dir = os.environ.get("G_REF_DIR") or os.path.dirname(here)
            blocks, missing = vendor_blocks(), []
            for name, (_, body) in sorted(blocks.items()):
                ref, first, last, _ = _VENDOR[name]
                path = os.path.join(ref_dir, ref)
                if not os.path.isfile(path):
                    missing.append(ref)
                    continue
                lines = open(path, "rb").read().split(b"\n")
                raw = b"".join(x + b"\n" for x in lines[first - 1:last]).decode("utf-8")
                self.assertEqual(_strip_source(raw).encode("utf-8"), body, name)
            if missing:
                self.skipTest("SKIPPED, reference not found: " + ", ".join(sorted(set(missing))))

        def test_15_source_house_rules(self):
            src = open(here, "rb").read()
            src.decode("ascii")
            for n, line in enumerate(src.split(b"\n"), 1):
                self.assertLessEqual(len(line), 120, n)
            for bad in (chr(8211), chr(8212)):
                self.assertNotIn(bad.encode(), src)

        def test_15_cost_growth(self):
            # host-independent: the work is the count of Python lines this module executes (sys.settrace), which
            # no machine speed or load can change; 8x the input gives near 8x the lines when linear, 64x quadratic
            module = globals()

            def lines_run(cmd):
                count = [0]

                def local(frame, event, arg):
                    if event == "line":
                        count[0] += 1
                    return local
                old = sys.gettrace()
                sys.settrace(lambda frame, event, arg: local if frame.f_globals is module else None)
                try:
                    verdict = _verdict(cmd, True, "/", no_oracle)
                finally:
                    sys.settrace(old)
                self.assertIsNotNone(verdict, cmd[:40])  # reached a verdict, not failed open
                return count[0]
            for make in (lambda k: "until grep -q X f; do sleep 1; done &\n" * k,
                         lambda k: "while a; do " * k + "sleep 1; " + "done; " * k,
                         lambda k: "while " * k + "sleep 1; " + "do :; done; " * k,
                         lambda k: "until test -f r; do echo " + "while date " * k + "; sleep 1; done",
                         lambda k: "until test -f r; do " + "nice timeout 1 " * k + "sleep 1; done"):
                small, large = lines_run(make(30)), lines_run(make(240))  # 240 stays under 4000 tokens
                self.assertLess(large / small, 16, (small, large, make(2)))

        def test_15_hang_ceiling(self):
            shapes = ("until a; do sleep 1; done &\n" * 3000, "while sleep 1; do " + "x" * 65536 + "; done",
                      "while sleep 1; do " + "$(" * 5000, "while sleep 1; do :; done " + "#wait-ok " * 7000,
                      "until sleep 1; do " + "((" * 30000 + "; done", "while sleep 1; do " + "{ " * 20000)
            for c in shapes:
                self.assertEqual(run_hook(payload_bytes(c)), (0, b"", b""), c[:40])

        def test_15_non_ascii_command(self):
            word = chr(233) + chr(8364) + chr(9989)
            reason = self.assertDeny("until grep -q " + word + " f; do sleep 5; done")
            self.assertNotIn(chr(9989), reason)
            rc, out, err = run_hook(payload_bytes("until grep -q " + word + " f; do sleep 5; done"))
            self.assertEqual((rc, err), (0, b""))
            out.decode("ascii")

        # round 1 QA regressions, one per finding
        def test_r1_01_leading_redirection(self):
            for c in ("until test -f ready; do >/dev/null sleep .01; done", "until test -f r; do >log sleep 1; done",
                      "until test -f r; do 2>/dev/null >x </dev/null sleep 1; done"):
                self.assertDeny(c)
            self.assertAllow("while <&3 read -r line; do printf '%s' \"$line\"; sleep .01; done 3< input")
            # a redirection before `while` makes it an ordinary command name (bash rejects the rest): no loop
            self.assertAllow("2>/dev/null until test -f ready; do sleep 1; done")

        def test_r1_02_timeout_wrapped_sleep(self):
            for c in ("until test -f ready; do timeout 1 sleep .01; done",
                      "until test -f r; do timeout -s KILL -k 2 1 sleep 1; done",
                      "until test -f r; do timeout --preserve-status --kill-after=2 1 sleep 1; done",
                      "until test -f r; do timeout -- 1 /bin/sleep 1; done", "while timeout 9 sleep 1; do :; done"):
                self.assertDeny(c)
            self.assertAllow("timeout 900 bash -c 'until grep -q X f; do sleep 5; done'")
            self.assertAllow("until test -f r; do timeout 1 true; done")
            root = tempfile.mkdtemp()
            try:  # the note sees the probe a timeout wraps
                reason = self.assertDeny("until timeout 5 grep -q X gone/out; do sleep 5; done", cwd=root)
                self.assertIn("Note: " + os.path.join(root, "gone"), reason)
            finally:
                shutil.rmtree(root)

        def test_r1_03_counter_updates(self):
            for c in ("n=1; while [ \"$n\" != 3 ]; do let n+=1; sleep .01; done; printf '%s' \"$n\"",
                      "n=0; while [[ $n < 10 ]]; do sleep 1; n=$(expr $n + 1); done",
                      "n=0; until [ $n = 9 ]; do sleep 1; n=`expr $n + 1`; done",
                      "n=0; while [ $n != 9 ]; do sleep 1; n=$((n+1)); done"):
                self.assertAllow(c)
            self.assertDeny("while [ \"$n\" != 3 ]; do sleep 1; done")

        def test_r1_04_date_options(self):
            for c in ("deadline=$(date -d \"+1 second\" +%s); while [[ $(date -u +%s) < $deadline ]]; do sleep .01; "
                      "done", "until grep -q X f || [ \"$(date --utc '+%s')\" -gt 9 ]; do sleep 5; done",
                      "until grep -q X f || [ \"$(date -u +%s%N)\" = 9 ]; do sleep 5; done"):
                self.assertAllow(c)
            self.assertDeny("until [ \"$(date -u +%H)\" = 09 ]; do sleep 60; done")  # not an epoch call

        def test_r1_05_unsupported_argv(self):
            for extra in (["--bad"], ["--self-test", "extra"], ["-x"], [""]):
                p = subprocess.run([sys.executable, "-I", "-S", "-B", here] + extra, input=payload_bytes(loop1),
                                   capture_output=True, env={"LC_ALL": "C"}, timeout=30)
                self.assertEqual((p.returncode, p.stdout, p.stderr), (0, b"", b""), extra)
            g, calls, old_out = globals(), [], sys.stdout
            old_read, old_worker = g["_read_payload"], g["_is_worker"]
            g["_read_payload"] = lambda *a, **k: calls.append(1) or json.loads(payload_bytes(loop1))
            g["_is_worker"] = lambda env: False  # hermetic: an ambient worker variable must not answer for main
            sys.stdout = io.StringIO()
            try:
                for argv in (["h", "--bad"], ["h", "--self-test", "x"], [], ["h", ""]):
                    self.assertEqual(main(argv), 0)
                self.assertEqual((calls, sys.stdout.getvalue()), ([], ""))  # stdin never read
                self.assertEqual(main(["h"]), 0)
                self.assertEqual(len(calls), 1)
                self.assertIn('"deny"', sys.stdout.getvalue())
            finally:
                g["_read_payload"], g["_is_worker"], sys.stdout = old_read, old_worker, old_out

        def test_r1_06_public_name(self):
            src = open(here).read()
            self.assertNotIn("G" + str(26), src)
            self.assertTrue(self.assertDeny(loop1).startswith("Blocked (unbounded-wait): "))
            doc = globals()["__doc__"]
            self.assertIn("unbounded-wait", doc.split("\n")[0])
            for name in ("ORCH_" + "WORKER", "ORCH_" + "VERIFY_OWNER", "AIQT_" + "HOOKS_WORKER"):
                self.assertNotIn(name, doc)

        def test_r1_07_nested_conditions_examined_once(self):
            g, calls = globals(), [0]
            real = g["_args"]

            def counting(toks, c, stop):
                calls[0] += 1
                return real(toks, c, stop)
            g["_args"] = counting
            try:
                for k in (10, 650):
                    calls[0] = 0
                    # the finding's shape (650 deep stays under 4000 tokens): no literal probe path, so no stat
                    # call ends the scan early. The outermost condition holds 2k - 1 command words (the sleep, and
                    # each inner body's `:` and `done`); each nested condition repeats a suffix of them, so a
                    # rescan examines about k * k words, examining each once exactly 2k - 1.
                    cmd = "while " * k + "sleep 1; " + "do :; done; " * k
                    self.assertIsNotNone(_verdict(cmd, True, "/", no_oracle))
                    self.assertEqual(calls[0], 2 * k - 1, k)
            finally:
                g["_args"] = real

        def test_r1_08_split_word_sleep(self):
            for c in ('until test -f r; do sl""eep 1; done', "until test -f r; do sl''eep 1; done",
                      "until test -f r; do sl\\\neep 1; done", "until test -f r; do s\\leep 1; done"):
                self.assertDeny(c)

        def test_r1_09_oversized_fails_open_whole(self):
            self.assertAllow("until test -f ready; do sleep 1; done; " + " " * 65536 + "# wait-ok: deliberate watcher")
            self.assertAllow(loop1 + "; echo " + chr(233) * 33000)  # under 64 Ki characters, over 64 KiB of UTF-8
            self.assertDeny(loop1 + "; echo " + chr(233) * 100)

        def test_r1_10_case_in_substitution_followed(self):
            self.assertDeny('until grep -q X "$(case a in (a) echo f;; esac)"; do sleep 5; done')
            self.assertAllow("case a in a) " + loop1 + ";; esac")

        def test_r1_11_read_loop_sense(self):
            for c in ("while ! read l; do sleep 1; done < f", "until read l; do sleep 1; done < f",
                      "while ! IFS= read -r l; do sleep 1; done < f"):
                self.assertDeny(c)
            for c in ("while read l; do sleep 1; done < f", "until ! read l; do sleep 1; done < f"):
                self.assertAllow(c)

        # round 2 QA regressions, one per finding
        def test_r2_01_argument_spans_read_once(self):
            g, read = globals(), [0]
            real = g["_args"]

            def counting(toks, c, stop):
                out = real(toks, c, stop)
                read[0] += len(out)
                return out
            g["_args"] = counting
            try:  # each `date` after an argument spelled `while` reads as a command word; the second shape puts
                # them all in the loop's condition, so the missing-directory note reads them too
                for cmd, cwd in (("until test -f ready; do echo " + "while date " * 1900 + "; sleep 1; done", None),
                                 ("until echo " + "while date " * 1900 + "; do sleep 1; done", "/")):
                    read[0] = 0
                    self.assertIsNotNone(_verdict(cmd, True, cwd, no_oracle))
                    self.assertLessEqual(read[0], len(_tokenize(cmd)), cmd[:30])  # linear, not about n * n / 2
            finally:
                g["_args"] = real

        def test_r2_02_redirections_among_wrapper_words(self):
            self.assertDeny("until test -f ready; do timeout >/dev/null 1 sleep .01; done")
            for name, (values, operands) in sorted(_WRAPPERS.items()):
                parts = [name, ">/dev/null"]
                if values:
                    parts += [values[0], "2>/dev/null", "v"]
                parts += ["</dev/null", "1"] * operands + ["2>&1", "sleep", "1"]
                self.assertDeny("until test -f r; do " + " ".join(parts) + "; done")

        def test_r2_03_wrappers(self):
            for name, (_, operands) in sorted(_WRAPPERS.items()):
                self.assertDeny("until test -f r; do " + " ".join([name] + ["1"] * operands) + " sleep 1; done")
            for w in ("nice", "nice -n 5", "nice -5", "nice --adjustment=5", "ionice -c2 -n 7", "stdbuf -o L",
                      "stdbuf -oL", "chrt -f 10", "taskset -c 0-3", "setsid -w", "env -u X -i Y=1", "nohup",
                      "sudo -u x -g y", "/usr/bin/time -f %e", "time -p", "exec -a n", "command -p",
                      "timeout -k 5 10", "nice timeout 5 nohup"):
                self.assertDeny("until test -f r; do " + w + " sleep 1; done")
            self.assertAllow("until test -f r; do nicely sleep 1; done")  # not a wrapper: its own command

        def test_r2_04_read_whole_condition(self):
            # a condition that runs a read of the loop's own input is exempt whatever else it runs (the round 4
            # rule: read loops are not this hook's target, so it fails toward allow for them)
            for c in ("while read -r line || ! test -f ready; do sleep .01; done < /dev/null",
                      "while read l; true; do sleep 1; done < f", "while read l | cat; do sleep 1; done < f",
                      "while read l && a || b; do sleep 1; done < f", "while true && read l; do sleep 1; done < f",
                      "while ! read l && true; do sleep 1; done < f", "until read l && true; do sleep 1; done < f"):
                self.assertAllow(c)
            for c in ("while read l < f; do sleep 1; done", "while ! test -f r; do sleep 1; done"):
                self.assertDeny(c)

        def test_r2_05_epoch_clocks(self):
            for c in ("deadline=$(date -d \"+0.15 second\" +%s.%N); while [[ $EPOCHREALTIME < $deadline ]]; do "
                      "sleep .01; done; echo finished",
                      "d=9; while [ \"$(printf '%(%s)T' -1)\" -lt \"$d\" ]; do sleep 1; done",
                      "until grep -q X f || [ \"$(printf '%(%s)T')\" = 9 ]; do sleep 1; done",
                      "until grep -q X f; do printf -v now '%(%s)T' -1; [ $now = 9 ] && break; sleep 1; done",
                      "while [[ $EPOCHSECONDS < 9 ]]; do sleep 1; done",
                      "while [[ \"$(date -u +%s.%N)\" < 9 ]]; do sleep 1; done"):
                self.assertAllow(c)
            self.assertDeny("until test -f r; do printf '%s' x; sleep 1; done")  # not a time format

        # rounds 3 and 4: read loops are exempt, except three shapes that spin, confirmed in real bash
        def test_r3_01_read_loop_differential(self):
            # D is the loop body: `sleep .01` for the hook; in bash an iteration counter that exits 99 on the 25th
            # pass, so a spin is decided by the count (exit 99), an end by exit 0, and time only guards a hang
            hook_body, bash_body = "do sleep .01; done", 'do n=$((n+1)); [ "$n" -ge 25 ] && exit 99; sleep .001; done'
            finite = [c.replace("B", "; D") for c in (
                "while read -r lineB < input", "while IFS= read -r line || [ -n \"$line\" ]B < input",
                "while read -r line ||\n  [ -n \"$line\" ]B < input", "while\n  read -r lineB < input",
                "while { read -r line; }B < input", "while (read -r line)B < input",
                "while read -r line <&3B 3< input", "while <&3 read -r lineB 3< input",
                "until ! read -r lineB < input", "while true && read -r lineB < input",
                "while command -- read -r lineB < input", "while read -u 3 line < /dev/nullB 3< input",
                "while 2>! read lB < input", "while read -ra fields || [[ -n ${fields[0]} ]]B < input",
                "while mapfile -n 1 fields && [ -n \"${fields[0]}\" ]B < input",
                "set -o pipefail\nwhile read line | catB < input", "while : | read -u 3 lB 3< input",
                "while : | read l <&3B 3< input", "printf 'a\\nb\\n' | while read -r lB",
                "printf 'a\\nb\\n' | { while read -r lB; }", "printf 'a\\nb\\n' | ( while read -r lB )",
                "cat input | while IFS= read -r line || [ -n \"$line\" ]B", "while : | { read l; } <&3B 3< input",
                "while : | { read l; } 0>&3B 3< input", "while : | if read l; then :; else false; fi 0>&3B 3< input",
                "while : | -p\nread lB < input", "while : | --\nread lB < input")]
            exempt = finite + [c.replace("B", "; D") for c in (  # allowed by design, finite or not
                "while read -r line || [ -n $line ]B < input", "while read -r line || ! test -f readyB < input",
                "while read -r line; trueB < input", "while { read -r line; } < inputB",
                "while read -t 0B < input", "while read line; line=pending; [ -n \"$line\" ]B < input",
                "while ! read -r line && trueB < input", "while readarray -t -n 1 fB < input")]
            spins = [c.replace("B", "; D") for c in (
                "while ! read -r lineB < input", "until read -r lineB < input", "while ! IFS= read -r lineB < input",
                "while\n  ! read -r line 2>/dev/nullB < input", "until IFS= read -rB < input",
                "while { ! read -r line; }B < input", "until (read -r line)B < input",
                "while ! { read -r line; }B < input", "while ! 2>! read lB < input",
                "until : | read lB", "while read -r s < status.txtB", "while read -r s <<< pendingB",
                "while read -r s <<EOF\npending\nEOF\nD", "while mapfile -t a < status.txtB",
                "while read -u 3 s 3< status.txtB", "while read -r line < /dev/stdinB < input",
                "until : | { read l; }B", "until : | ( read l )B", "until : | { { read -r l; }; }B",
                "while printf '%s\\n' pending | { read s; [ \"$s\" != ready ]; }B",
                "while ! { read l; } 2>/dev/nullB < input", "until (read l) 2>/dev/nullB < input",
                "until read -r lB <<EOF\nEOF\n", "while [[ ! -f ready || read == read ]]B",
                "until time { read l; }B", "until time -p ( read l )B", "until time : | { read l; }B",
                "until : | if read l; then true; else false; fiB", "until : | { read l <&0; }B",
                "until read -r lB < partial", "until time -- { read l; }B", "until time -p -- ( read l )B",
                "until : | { read l <&00; }B", "until : | { read l <&0-; }B", "until : | { read l <&-; }B",
                "until : | read l 0<&0-B")]
            self.assertEqual((len(finite), len(exempt), len(spins)), (27, 35, 36))
            for c in exempt:
                self.assertIsNone(_verdict(c.replace("D", hook_body), True, None, no_oracle), ("exempt", c))
            for c in spins:
                self.assertIsNotNone(_verdict(c.replace("D", hook_body), True, None, no_oracle), ("spins", c))
            bash = next((b for b in _BASH_PATHS if os.path.isfile(b) and os.access(b, os.X_OK)), None)
            if bash is None:
                self.skipTest("no bash: the hook verdicts are checked, the bash confirmation cannot run")
            env = {"PATH": "/usr/bin:/bin", "LC_ALL": "C", "line": "x", "s": "x", "REPLY": "x"}
            roots, runs = [], []
            try:
                for content in ("", "alpha\nbeta\n"):
                    root = tempfile.mkdtemp()
                    roots.append(root)
                    with open(os.path.join(root, "input"), "w") as f:
                        f.write(content)
                    with open(os.path.join(root, "status.txt"), "w") as f:
                        f.write("pending\n")
                    with open(os.path.join(root, "partial"), "w") as f:
                        f.write("pending")  # a last line without its newline: read fails on it
                    runs.append([subprocess.Popen([bash, "--norc", "--noprofile", "-c", c.replace("D", bash_body)],
                                                  cwd=root, env=env, stdin=subprocess.DEVNULL,
                                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                                  start_new_session=True) for c in finite + spins])
                codes = [[q.wait(timeout=60) for q in run] for run in runs]  # a hang guard, never a verdict
            finally:
                for run in runs:
                    for q in run:
                        if q.poll() is None:
                            try:
                                os.killpg(q.pid, 9)
                            except OSError:
                                pass
                            q.wait(timeout=10)
                for root in roots:
                    shutil.rmtree(root)
            for n, c in enumerate(finite):
                self.assertEqual([codes[0][n], codes[1][n]], [0, 0], ("bash did not end", c))
            for n, c in enumerate(spins, len(finite)):
                self.assertIn(99, (codes[0][n], codes[1][n]), ("bash did not spin", c))

        def test_r3_02_read_loop_structure(self):
            for c in ("while read -r line ||\n  [ -n \"$line\" ]; do sleep 1; done < f",
                      "while { read -r l; }; do sleep 1; done < f", "while (read -r l); do sleep 1; done < f",
                      "while if read l; then true; fi; do sleep 1; done < f",
                      "while read -u 3 line < /dev/null; do sleep 1; done 3< f",
                      "while read -u \"$fd\" line < x; do sleep 1; done", "while read -r s <&3; do sleep 1; done 3< f"):
                self.assertAllow(c)
            for c in ("while read -r s < status.txt && [ \"$s\" != ready ]; do sleep 1; done",
                      "while read -r s <<< x; do sleep 1; done", "while read -u 3 s 3< x; do sleep 1; done",
                      "while read -u3 s 3<x; do sleep 1; done", "while <x read s; do sleep 1; done",
                      "while ! read -r l 2>/dev/null; do sleep 1; done < f", "until read; do sleep 1; done < f"):
                self.assertDeny(c)

        def test_r3_03_env_dash(self):
            self.assertDeny("until test -f ready; do env - sleep .01; done")
            self.assertDeny("until test -f ready; do env - X=1 sleep .01; done")

        def test_r3_04_printf_format_any_length(self):
            for n in (1, 65, 300):
                self.assertAllow("d=$(printf '%(" + "x" * n + "%s)T' -1); until [ \"$(printf '%(" + "x" * n
                                 + "%s)T' -1)\" != \"$d\" ]; do sleep .01; done")
            self.assertDeny("until test -f r; do printf '%(x) T'; sleep 1; done")  # the closer is not `)T`
            self.assertEqual(_bound_marks("%(" * 30000 + ")T"), [0])  # one pass: every opener pends on the first
            self.assertEqual(_bound_marks("%(a) %(b)T"), [5])

        def test_r3_05_strip_source(self):
            src = ("X = 1  # trailing\n# only a comment\nS = 'a#b' + \"c # d\"  # cut\n"
                   "def f():\n    \"\"\"Doc # kept out.\n    more\"\"\"\n    return '#'\n\n"
                   "T = (1,  # inside\n     2)\nU = f'{X}#'\n")
            want = ("X = 1\nS = 'a#b' + \"c # d\"\ndef f():\n    return '#'\n\nT = (1,\n     2)\nU = f'{X}#'\n")
            self.assertEqual(_strip_source(src), want)
            self.assertEqual(_strip_source(want), want)  # idempotent
            compile(_strip_source(src), "stripped", "exec")
            q = chr(39) * 3  # a triple single quote
            keep = ("X = " + q + "not a docstring # kept" + q + "\nclass C:\n    x = 1\n    " + q + "later, kept" + q
                    + "\n")
            self.assertEqual(_strip_source(keep), keep)  # only a def's or class's first statement is a docstring
            with self.assertRaises(tokenize.TokenError):
                _strip_source("X = " + q + "unterminated\n")

        def test_r3_06_no_internal_names(self):
            src = open(here).read()
            pattern = re.compile(r"\bG" + r"\d+\b|" + "|".join(
                ("cla" + "ude", "gem" + "ini", "cod" + "ex", "fab" + "le", "g" + "pt", "orches" + "trator",
                 "fl" + "eet", "orch" + "-verify", "la" + "b_in" + "fra", "OR" + "CH_[A-Z_]+")), re.I)
            found = {m.group() for m in pattern.finditer(src)}
            self.assertEqual(found - {"OR" + "CH_WORKER", "OR" + "CH_VERIFY_OWNER"}, set())

        # round 4 regressions
        def test_r4_01_read_rule_work_is_linear(self):
            g, read = globals(), [0]
            real = g["_args"]

            def counting(toks, c, stop):
                out = real(toks, c, stop)
                read[0] += len(out)
                return out
            g["_args"] = counting
            try:
                cmd = ("while " + "".join("test -f r%d && read a%d; " % (n, n) for n in range(6))
                       + "".join("read v%d; " % n for n in range(1200)) + "true; do sleep 1; done < /dev/null")
                self.assertIsNone(_verdict(cmd, True, None, no_oracle))  # a read loop: exempt
                self.assertLessEqual(read[0], len(_tokenize(cmd)))
            finally:
                g["_args"] = real

        def test_r4_02_strip_keeps_meaning(self):
            import ast

            def code(src):  # the executable AST: every def's and class's docstring removed
                tree = ast.parse(src)
                for node in ast.walk(tree):
                    body = getattr(node, "body", None)
                    if (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and body
                            and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant)
                            and isinstance(body[0].value.value, str)):
                        node.body = body[1:]
                return ast.dump(tree)
            samples = ["X = 1 \\\n# comment\n+ 2\n", "X = 1 \\\r\n# comment\r\n+ 2\r\n",
                       "X = (1 +  # a\n     # b\n     2)\nY = 'a # b'  # c\n",
                       "def f(a):\n    \"\"\"Doc.\"\"\"\n    # note\n    return a  # x\n",
                       "class C:\n    \"\"\"Doc.\"\"\"\n    x = 1 \\\n        # c\n\n"]
            ref_dir = os.environ.get("G_REF_DIR") or os.path.dirname(here)
            for name, (ref, first, last, _) in sorted(_VENDOR.items()):
                path = os.path.join(ref_dir, ref)
                if os.path.isfile(path):
                    lines = open(path, "rb").read().decode("utf-8").split("\n")
                    samples.append("".join(x + "\n" for x in lines[first - 1:last]))
            for src in samples:
                out = _strip_source(src)
                compile(out, "stripped", "exec")
                self.assertEqual(code(out), code(src), src[:40])
                self.assertEqual(_strip_source(out), out)
            for sample in samples[:2]:  # LF and CRLF: the continuation still ends before `+ 2`
                env = {}
                exec(_strip_source(sample), env)
                self.assertEqual(env["X"], 1, repr(sample))
            self.assertEqual(_strip_source("X = 1  # c\r\nY = 2\r\n"), "X = 1\r\nY = 2\r\n")  # CRLF kept

        # round 5 regressions
        def test_r5_01_read_rule_edges(self):
            for c in ("until : | read l; do sleep 1; done", "while x |\n  read l; do sleep 1; done < f",
                      "while { ! read -r l; }; do sleep 1; done < f", "until (read -r l); do sleep 1; done < f",
                      "while ! { read -r l; }; do sleep 1; done < f", "until { IFS= read -r l\n}; do sleep 1; done < f",
                      "until grep -q while read; do sleep .01; done", "while ! 2>! read l; do sleep 1; done < f"):
                self.assertDeny(c)
            for c in ("while 2>! read l; do sleep 1; done < f", "while ! { ! read -r l; }; do sleep 1; done < f",
                      "while { { ! read l; }; }; do sleep 1; done < f", "while read l | cat; do sleep 1; done < f"):
                self.assertAllow(c)

        # round 6 regressions
        def test_r6_01_pipe_descriptor_and_test_data(self):
            for c in ("until curl -s u | { read x; [ \"$x\" = ok ]; }; do sleep 5; done",
                      "until : | ( read l ); do sleep 1; done", "until : |\n  { ! { read l; }; }; do sleep 1; done",
                      "while ! { read l; } 2>/dev/null; do sleep 1; done < f",
                      "until (read l) 2>/dev/null </dev/null; do sleep 1; done < f",
                      "while [[ ! -f ready || read == read ]]; do sleep 1; done",
                      "while [[ -f a && ( read ) ]]; do sleep 1; done"):
                self.assertDeny(c)
            for c in ("while : | read -u 3 l; do sleep 1; done 3< f", "while : | read l <&3; do sleep 1; done 3< f",
                      "while : | { read -u 3 l; }; do sleep 1; done 3< f",
                      "while { read l; } | cat; do sleep 1; done < f",
                      "while [[ -f a ]] && read l; do sleep 1; done < f"):
                self.assertAllow(c)

        # round 7 regressions
        def test_r7_01_pipe_relative_to_loop(self):
            for c in ("producer | while read l; do echo \"$l\"; sleep 1; done",
                      "producer | { while read -r l; do sleep 1; done; }",
                      "producer | ( while read l; do sleep 1; done )",
                      "producer |\n  while IFS= read -r l || [ -n \"$l\" ]; do sleep 1; done",
                      "while : | { read l; } <&3; do sleep 1; done 3< f",
                      "a | while read x; do b | while read y; do sleep 1; done; done"):
                self.assertAllow(c)
            for c in ("until time { read l; }; do sleep 1; done", "until time -p ( read l ); do sleep 1; done",
                      "while ! time { read l; }; do sleep 1; done", "until time : | { read l; }; do sleep 1; done",
                      "until : | if read l; then :; fi; do sleep 1; done",
                      "until : | { read l <&0; }; do sleep 1; done",
                      "until : | { read l 0<&0; }; do sleep 1; done",
                      "producer | { until : | { read l; }; do sleep 1; done; }"):
                self.assertDeny(c)

        # round 8 regressions
        def test_r8_01_descriptors_and_time_options(self):
            for c in ("until time -- { read l; }; do sleep 1; done", "until time -p -- ( read l ); do sleep 1; done",
                      "until : | { read l <&00; }; do sleep 1; done", "until : | { read l 00<&000; }; do sleep 1; done",
                      "until : | { read l <&0-; }; do sleep 1; done", "until : | { read l <&-; }; do sleep 1; done",
                      "until : | read l 0<&0-; do sleep 1; done", "until : | { read l 0>&0; }; do sleep 1; done"):
                self.assertDeny(c)
            for c in ("while : | { read l; } 0>&3; do sleep 1; done 3< f",
                      "while : | { read l <&03; }; do sleep 1; done 3< f",
                      "while : | if read l; then :; fi 0>&3; do sleep 1; done 3< f",
                      "while : | { read l <&3-; }; do sleep 1; done 3< f",
                      "while : | -p\nread l; do sleep 1; done < f",
                      "while : | --\nread l; do sleep 1; done < f",
                      "while : | { read l <&\"$fd\"; }; do sleep 1; done"):
                self.assertAllow(c)

    suite = unittest.defaultTestLoader.loadTestsFromTestCase(T)
    result = unittest.TextTestRunner(stream=sys.stderr, verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
