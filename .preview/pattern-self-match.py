#!/usr/bin/env python3
"""PreToolUse Bash hook (pattern-self-match): deny a kill or wait whose pgrep pattern selects the shell running it.

WHAT IT DOES
    `pgrep -f PATTERN` and `pkill -f PATTERN` match PATTERN against each process's whole command line. A shell tool
    that runs each command through a shell (`bash -c "... && eval '<command>' < /dev/null && pwd -P ..."`) carries
    the command's own text in that shell's command line, so a pattern written as plain text in the command also
    selects the shell running it: `pkill -f worker/` signals its own shell, and
    `while pgrep -f worker/ >/dev/null; do sleep 2; done` waits on its own shell and never finishes by itself. The
    process such a command acts on is bound by ambient context, whatever the process table holds when it runs; the
    rule that binds an action to its explicit target asks that "where only an ambient binding exists, the execution
    target is confirmed by observation before the action runs"
    (.claude/rules/aiqt/10-INTEG-explicit-binding-over-ambient-context.md). This hook denies the closed set of shapes
    below, where the pattern is proved to select the running shell, and adds a note where a pattern is text the
    command holds but no such proof is possible.

    Event: PreToolUse, matcher Bash. Register the launch line REGISTRATION (below the imports), filled with python3
    and this file's absolute path. Output: nothing (allow), ONE line holding the standard PreToolUse deny object, or
    ONE line holding a note (a systemMessage with no permissionDecision, so the permission flow is unchanged). Exit
    status: always 0; the decision travels in the JSON. This hook never asks.

    LITERAL. A bare word, a '...' string, or a "..." string whose value is non-empty, does not start with `-`, and
    uses only the characters A-Z, a-z, 0-9 and _ . / : @ % = , -. Such a value holds no blank, newline, quote or
    backslash, so it reaches the running shell's command line unchanged (the wrapper rewrites only `'`), procps shows
    it unchanged (procps changes NUL and newline to blanks and escapes only non-printable bytes), and, read as a
    regular expression, it matches itself (its one metacharacter, `.`, matches any character, itself included). `+`
    is left out: as a regular expression, `a+b` does not match the text `a+b`.

    DENY when the whole command parses in the grammar below and one of its elements is one of these shapes. SIG is
    an optional signal: -9, -15, -1, -2 or -3, or -KILL, -TERM, -HUP, -INT or -QUIT with or without a SIG prefix,
    or one of those values (without the dash) after `-s` (kill only; pkill reads -s as a session) or as
    `--signal X` or `--signal=X` (pkill and the kill that xargs runs only; bash's builtin kill rejects that spelling
    and sends nothing). Signal 0 and every other option take the note. Options are separate words.
      (a) pkill OPTS LITERAL: OPTS hold -f or --full, and optionally -e or --echo, one SIG, and `--` just before
          LITERAL; exactly one operand;
      (b) for V in $(pgrep F LITERAL); do BODY; done, where F is -f or --full, optionally then `--`, and BODY is
          simple commands separated by `;` or newlines: exactly one `kill [SIG] "$V"` or `kill [SIG] $V` naming the
          loop's own V, and otherwise only echo or printf (an argument may be "$V"), sleep N, `:` or true;
      (c) kill [SIG] $(pgrep F LITERAL), the substitution unquoted and the only operand, or the two-stage pipeline
          pgrep F LITERAL | xargs [-r | --no-run-if-empty] kill [SIG];
      (d) while pgrep G LITERAL [R]; do BODY; done, or until ! pgrep G LITERAL [R]; do BODY; done, where G is F
          plus optional separate -a, -l or -c, R is one or more of >/dev/null, 2>/dev/null, 2>&1 and &>/dev/null,
          and BODY holds only sleep N, echo, printf, `:`, true and date.
    N is a plain number with an optional s, m, h or d suffix (`sleep infinity` would keep a later shape from
    running). A shape is a whole element of the top-level list, whose elements are split by `;` or newlines. Every
    other element is a benign simple command: echo, printf, sleep N, cd, pwd, true, `:`, date or ls, with LITERAL
    or quoted arguments holding no expansion, and redirections from R. No element holds `&&`, `||`, `&`, an
    assignment, a function definition or a prefix word (exec, sudo, env, nice, timeout, command, nohup and the
    rest), and every element after the shape parses too, since an unparsable tail could stop the shape running.
    The words are read by a small tokenizer that refuses everything else: a backtick, a backslash, `(`, `{`, `<`,
    a here-document, a glob, `~`, a comment, `if`, `case`, `[[` and every other construct send the command to the
    note scan. The grammar is meant as a strict subset of bash's, so a command it accepts is valid bash; the
    self-test checks that with bash -n over each deny example.

    NOTE (allow, with a systemMessage) when the command is not denied, names a process matcher (pgrep, pkill,
    killall or pidof, or grep in a command that also names ps), and that matcher's pattern (the first later word
    that does not start with `-`, or the word just after `--`) is text the command itself holds. The words are read
    with Python's shlex; a word that holds a blank and names a matcher is read again, up to four layers deep
    (`bash -c '...'`). A pattern holding `$` or a backtick is built when the command runs and is passed over
    silently. A command that names a matcher and that shlex cannot read, or that is over 64 KiB, gets a note that
    it could not be read. An internal error gets a note that the command was not checked. Everything else is
    silent.

OPT-OUT
    When selecting the running shell is intended, end the command with a real shell comment whose text starts
    `# self-match-ok`, optionally followed by a colon or blank and a reason, for example:
        pkill -f run-42/ # self-match-ok: the shell is meant to stop too
    The comment must be unquoted, begin a word, and be the last non-blank content of the command; a `#` inside a
    word or a quoted string is not a comment and does not opt out. It silences both the deny and the note.

THREAT MODEL
    This is an accidental-habit guard, not a security boundary. The actor is a well-meaning assistant that stops or
    waits on work by a name pattern and forgets that the name is also in its own shell's command line; nothing here
    resists a caller that sets out to hide a pattern. The deny set is closed, and each shape in it selects the
    running shell under the premises in RESIDUAL COVERAGE; everything uncertain gets a note at most. An internal
    error while reading a command allows it WITH a note that it was not checked; a malformed payload fails open
    with no output. The one exception is an interpreter older than Python 3.14 that can start the hook: the guard
    at the top of this file reads no input, writes one line beginning
    `error: pattern-self-match.py requires Python 3.14 or newer` to stderr and exits 2, which PreToolUse treats as
    a deny, so every Bash call is denied until Python is upgraded or the hook's entry is removed. An older
    interpreter that cannot start the hook never reaches the guard and fails with Python's own error first. For
    this hook that is only one that predates the -I option, and it exits 2, which still denies every Bash call:
    this file is written without f-strings or other syntax newer than Python 3.4 (the self-test checks for
    f-strings and parses the file with the parser's 3.4 grammar setting), so any interpreter that accepts -I
    reaches the guard. .preview/README.md (Installing a hook, step 4) describes those cases.
    The hook is silent for a verification worker process (a worker kill-switch variable; legacy spellings are also
    honoured), where it writes one line to stderr saying it skipped; for a tool_name other than Bash; for an event
    other than PreToolUse; for any argv other than the plain hook call or exactly `--self-test` (answered before
    stdin is read); and for a stdin that is absent, unreadable, over 16 MiB, incomplete after 2 seconds, not JSON,
    or not an object. A payload carrying agent_id (a subagent's call) is checked like any other. Work is bounded: a
    command over 64 KiB of UTF-8 is not read; the tokenizer and the grammar read each token a bounded number of
    times, the note scan reads each word at most four times (once per layer), and each distinct pattern it checks
    is searched for once in the command. The hook never runs the command and starts no process.

RESIDUAL COVERAGE.
    Each deny rests on premises observed on one host (procps-ng 4.0.4 and one harness's Bash tool), not proved for
    every host:
      - the wrapper form: the tool runs each command inside `bash -c "... && eval '<command>' < /dev/null &&
        pwd -P ..."`, so the running shell holds the command's text in its own command line, and because a command
        follows the eval, bash cannot replace the shell with the last command. A host whose tool runs a command so
        that its last command replaces the shell (`bash -c 'pkill -f x'`) makes a deny false. To re-check a host,
        run `pgrep -fc 'qa-probe/'` through its Bash tool: it prints at least 1 while the premise holds;
      - procps: pgrep and pkill search a whole argument (a literal 131,000 bytes into one argument still matched,
        and Linux caps one argument at 128 KiB), and change only NUL and newline to blanks and escape only
        non-printable bytes;
      - a `kill`, `pkill` or `pgrep` shell function or alias (one defined in a shell snapshot, say) can change what
        a shape runs;
      - a trap on the shell: the deny says the command selects or signals its own shell, not that the shell dies.
    What is not denied: everything outside the closed set gets a note at most, including compound commands (`if`,
    groups, subshells), `&&` and `||` lists, `ps | grep` pipelines, prefix words such as `sudo` or `exec`, name
    mode (pkill without -f), killall and pidof, other loops, options outside the lists above (an option cluster
    such as -ef too), redirections outside R, and nested shell strings. A pattern built when the command runs
    (holding `$` or a backtick) is passed over silently: that is the safe form the deny reason names, but a runtime
    pattern that does select the running shell is missed. The note scan reads words with Python's shlex, which
    differs from bash in places (here-documents, `$'...'` strings, a `#` inside a word): a matcher read differently
    is missed or noted wrongly. A matcher word anywhere counts, also as an argument (`echo pkill x` gets a note).
    The opt-out of a command the tokenizer cannot read is confirmed with shlex too, so the same differences apply
    to it. Other tools (PowerShell, a script file) are not read. A worker process that sets the worker kill-switch
    variable is skipped. Whether the host shows a PreToolUse systemMessage to the model, or only to the user, was
    not observed.

Self-test: python3 -I -S -B pattern-self-match.py --self-test
    The reference hook the vendored block was copied from is looked up in the directory named by the G_REF_DIR
    environment variable, else in this file's own directory; when it is absent its byte-identity check reports
    SKIPPED, never a pass. The check that bash accepts every deny vector reports SKIPPED when no trusted, root-owned
    /usr/bin/bash or /bin/bash is found.
"""

import sys

if tuple(sys.version_info[:2]) < (3, 14):
    sys.stderr.write(
        "error: pattern-self-match.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
        "Nothing was run (cannot evaluate).\n"
        % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
    raise SystemExit(2)

import json
import os
import re
import select
import shlex
import time

HOOK_ID = "pattern-self-match"

# The launch line to register ({python} and {hook} filled in: python3 and this file's absolute path). CPython stops
# before running any byte of this file when its stdin is a directory (exit 1), so only a guard in front of the
# interpreter can answer that one input: it exits 0 silently and otherwise execs the hook, stdin untouched.
REGISTRATION = '[ -d /dev/stdin ] && exit 0; exec "{python}" -I -S -B "{hook}"'

# Each vendored block: name -> (reference file, first line, last line, sha256 of the block's text). The block is
# those lines of the reference with their comments and docstrings removed (_strip_source), fenced below; the
# self-test checks the hash, that the block is already stripped, and, when the reference file is found, that
# stripping the reference's same lines gives the block byte for byte.
_VENDOR = {
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

# The hook's own reading.
_SCAN_LIMIT = 64 * 1024
# A LITERAL's value, and a bare word: only these characters (a LITERAL also must not start with `-`).
_LITERAL_RE = re.compile(r"[A-Za-z0-9_./:@%=,-]+\Z")
_BARE_RE = re.compile(r"[A-Za-z0-9_./:@%=,-]+")
_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_DQ_VAR_RE = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)\Z")
# The redirections R, at a token's start: >, 2> or &> onto /dev/null (a blank may stand before the target), or 2>&1.
_REDIR_RE = re.compile(r"(2>|&>|>)[ \t]*/dev/null|2>&1")
# What may follow a word, a variable, a redirection or `)`: a blank, a newline, `;`, `|`, `&`, `)`, or the end.
_AFTER_WORD = frozenset(" \t\n;|&)")
_SLEEP_RE = re.compile(r"[0-9]+(?:\.[0-9]+)?[smhd]?\Z")
_SIG_VALUES = frozenset(("9", "15", "1", "2", "3", "KILL", "TERM", "HUP", "INT", "QUIT",
                         "SIGKILL", "SIGTERM", "SIGHUP", "SIGINT", "SIGQUIT"))
# The signal spellings each signaller reads: pkill reads `-s` as a session, and bash's builtin kill rejects
# `--signal` (it reports an invalid signal specification and sends nothing); the kill that xargs runs takes all.
_SIG_FORMS = {"pkill": ("dash", "long"), "kill": ("dash", "s"), "xargs-kill": ("dash", "s", "long")}
_BENIGN = frozenset(("echo", "printf", "sleep", "cd", "pwd", "true", ":", "date", "ls"))
_FOR_BODY = frozenset(("echo", "printf", "sleep", ":", "true"))
_WAIT_BODY = frozenset(("echo", "printf", "sleep", ":", "true", "date"))
_ECHOES = frozenset(("echo", "printf"))
_FULL = frozenset(("-f", "--full"))
_LISTING = frozenset(("-a", "-l", "-c"))
_RESERVED = frozenset(("for", "in", "do", "done", "while", "until", "if", "then", "else", "elif", "fi", "case",
                       "esac", "select", "function", "time", "coproc"))
_SEPS = (("op", ";", None), ("op", "\n", None))
# The note scan: the matcher words, the operator characters that end a simple command, and the layers read.
_MATCHERS = frozenset(("pgrep", "pkill", "killall", "pidof"))
_PUNCT = frozenset("();<>|&")
_NOTE_DEPTH = 4
_MATCHER_RE = re.compile(r"\b(?:pgrep|pkill|killall|pidof)\b")
_PS_RE = re.compile(r"\bps\b")
_GREP_RE = re.compile(r"\bgrep\b")
_SHOWN = 200  # characters of a LITERAL quoted in the deny reason
# The opt-out: a comment starting `# self-match-ok` (then a colon, a blank, or nothing) that ends the command. The
# lookbehind keeps a mid-word `#` out; _opted_out confirms the `#` stands outside quotes.
_OPT_OUT_RE = re.compile(r"(?<![^ \t\n;&|()])#[ \t]*self-match-ok(?:[ \t:][^\n]*)?\Z")

_REASON = (
    "Blocked (pattern-self-match): `{lit}` is plain text in this command, and the shell running the command "
    "carries that text in its own command line, so `pgrep -f`/`pkill -f` selects that shell; this command would "
    "{effect}. Stop work you launched by its handle, or by the process id or group you recorded at launch "
    "(`kill PID`, `kill -- -PGID`, the harness's task stop). Or keep the pattern off the command line "
    "(`pkill -f -- \"$(cat PATTERN_FILE)\"`). If self-selection is intended, end the command with "
    "`# self-match-ok: <reason>`.")
_EFFECTS = {"signal": "signal its own shell", "wait": "wait on its own shell and never finish by itself"}
_NOTE = ("pattern-self-match: this command selects processes by a pattern that also appears in its own text; it "
         "could not be proved whether that includes the shell running it.")
_NOTE_UNREAD = ("pattern-self-match: this command names a process matcher (pgrep, pkill, killall or pidof) but "
                "could not be read; it could not be proved whether its pattern selects the shell running it.")
_NOTE_ERROR = ("pattern-self-match: an internal error stopped this check, so the command was not checked for a "
               "pattern that selects the shell running it.")
_WORKER_LINE = "pattern-self-match: skipped, worker marker present (AIQT_HOOKS_WORKER=1 or a legacy spelling)"


class _Refused(Exception):
    """A command outside the deny grammar: it goes to the note scan."""


def _is_worker(env):
    """True for a verification worker process: the worker kill-switch variable set to "1", or one of its legacy
    spellings (one set to "1", another present with any value, even empty)."""
    if env.get("AIQT_HOOKS_WORKER") == "1":
        return True
    return env.get("ORCH_WORKER") == "1" or "ORCH_VERIFY_OWNER" in env


def _end(text, j):
    """j, when text[j] may follow a word (_AFTER_WORD, or the end of the text); else raise _Refused."""
    if j < len(text) and text[j] not in _AFTER_WORD:
        raise _Refused
    return j


def _tokens(text):
    """The command's tokens, or raise _Refused at the first character or construct outside the tokenizer's set.
    A token is (kind, value, quoting): ("w", text, "bare" | "sq" | "dq") a word; ("v", name, quoted) a `$V` or
    "$V"; ("op", op, None) for `;`, newline, `|`, `&&`, `||`, `&`, `!`, `$(` and its `)`; ("r", target, None) one
    of R, spelled without blanks. A `$(` may not nest, and every word stands alone (no word is joined to a quote,
    a variable or a substitution)."""
    out, i, n, depth = [], 0, len(text), 0
    while i < n:
        c = text[i]
        if c in " \t":
            i += 1
            continue
        if c in "\n;":
            if c == ";" and text[i + 1:i + 2] in (";", "&"):
                raise _Refused  # a case arm's terminator
            out.append(("op", c, None))
            i += 1
            continue
        m = _REDIR_RE.match(text, i)
        if m:
            tok, i = ("r", m.group(1) + "/dev/null" if m.group(1) else "2>&1", None), _end(text, m.end())
        elif c in "&|":
            two = text[i:i + 2]
            if two == "|&":
                raise _Refused
            if two in ("&&", "||"):
                tok, i = ("op", two, None), i + 2
            else:
                tok, i = ("op", c, None), i + 1
        elif c == ")":
            if not depth:
                raise _Refused
            depth = 0
            tok, i = ("op", ")", None), _end(text, i + 1)
        elif c == "$":
            if text.startswith("$(", i):
                if depth or text.startswith("$((", i):
                    raise _Refused
                depth = 1
                tok, i = ("op", "$(", None), i + 2
            else:
                m = _NAME_RE.match(text, i + 1)
                if not m:
                    raise _Refused
                tok, i = ("v", m.group(), False), _end(text, m.end())
        elif c == "'":
            j = text.find("'", i + 1)
            if j < 0:
                raise _Refused
            tok, i = ("w", text[i + 1:j], "sq"), _end(text, j + 1)
        elif c == '"':
            j = text.find('"', i + 1)
            if j < 0:
                raise _Refused
            body = text[i + 1:j]
            m = _DQ_VAR_RE.match(body)
            if m:
                tok = ("v", m.group(1), True)
            elif "$" in body or "`" in body or "\\" in body:
                raise _Refused
            else:
                tok = ("w", body, "dq")
            i = _end(text, j + 1)
        elif c == "!":
            if text[i + 1:i + 2] not in ("", " ", "\t"):
                raise _Refused
            tok, i = ("op", "!", None), i + 1
        else:
            m = _BARE_RE.match(text, i)
            if not m:
                raise _Refused
            tok, i = ("w", m.group(), "bare"), _end(text, m.end())
        out.append(tok)
    if depth:
        raise _Refused
    return out


def _w(tok, value=None):
    """True for a bare word token (equal to value, when one is given)."""
    return tok is not None and tok[0] == "w" and tok[2] == "bare" and (value is None or tok[1] == value)


def _literal(tok):
    """True for a word token whose value is a LITERAL."""
    return tok[0] == "w" and bool(_LITERAL_RE.match(tok[1])) and not tok[1].startswith("-")


def _plain(tok):
    """True for an argument holding no expansion: a LITERAL bare word, or any quoted word."""
    return tok[0] == "w" and (tok[2] != "bare" or _literal(tok))


def _signal(run, k, who):
    """How many tokens at run[k] spell one signal from the set for the signaller `who` (_SIG_FORMS); 0 when they
    do not."""
    forms = _SIG_FORMS[who]
    t = run[k] if k < len(run) else None
    if not _w(t):
        return 0
    v = t[1]
    if "dash" in forms and v.startswith("-") and v[1:] in _SIG_VALUES:
        return 1
    if "long" in forms and v.startswith("--signal=") and v[9:] in _SIG_VALUES:
        return 1
    nxt = run[k + 1] if k + 1 < len(run) else None
    if ((v == "-s" and "s" in forms) or (v == "--signal" and "long" in forms)) and _w(nxt) and nxt[1] in _SIG_VALUES:
        return 2
    return 0


def _pgrep(run, extra):
    """The LITERAL of the stage `pgrep OPTS LITERAL`, whose OPTS are separate words: -f or --full (required), the
    words in extra, and `--` just before LITERAL; else None."""
    if len(run) < 3 or not _w(run[0], "pgrep") or not _literal(run[-1]):
        return None
    full = False
    for k in range(1, len(run) - 1):
        t = run[k]
        if _w(t) and t[1] in _FULL:
            full = True
        elif not (_w(t) and t[1] in extra) and not (_w(t, "--") and k == len(run) - 2):
            return None
    return run[-1][1] if full else None


def _pkill(run):
    """Shape (a): the LITERAL of `pkill OPTS LITERAL`, else None."""
    last = len(run) - 1
    if last < 2 or not _w(run[0], "pkill") or not _literal(run[last]):
        return None
    full, sig, k = False, False, 1
    while k < last:
        t = run[k]
        if _w(t) and t[1] in _FULL:
            full, k = True, k + 1
        elif _w(t) and t[1] in ("-e", "--echo"):
            k += 1
        elif _w(t, "--") and k == last - 1:
            k += 1
        else:
            m = 0 if sig else _signal(run[:last], k, "pkill")
            if not m:
                return None
            sig, k = True, k + m
    return run[last][1] if full else None


def _kill_sub(run):
    """Shape (c), first form: the LITERAL of `kill [SIG] $(pgrep F LITERAL)`, else None."""
    if len(run) < 2 or not _w(run[0], "kill") or run[-1] != ("op", ")", None):
        return None
    k = 1 + _signal(run, 1, "kill")
    if run[k] != ("op", "$(", None):
        return None
    return _pgrep(run[k + 1:-1], ())


def _xargs_kill(run):
    """Shape (c), second form: the LITERAL of `pgrep F LITERAL | xargs [-r] kill [SIG]`, else None."""
    bars = [k for k, t in enumerate(run) if t == ("op", "|", None)]
    if len(bars) != 1:
        return None
    lit, tail = _pgrep(run[:bars[0]], ()), run[bars[0] + 1:]
    if lit is None or not tail or not _w(tail[0], "xargs"):
        return None
    k = 1
    if k < len(tail) and _w(tail[k]) and tail[k][1] in ("-r", "--no-run-if-empty"):
        k += 1
    if not (k < len(tail) and _w(tail[k], "kill")):
        return None
    k += 1
    k += _signal(tail, k, "xargs-kill")
    return lit if k == len(tail) else None


def _simple(run, names, redirs=False, var=None):
    """True for a simple command named in names whose arguments hold no expansion (_plain; with var, echo and
    printf may also take "$var"), with `sleep` given exactly one N; redirections from R only when redirs."""
    if not run or not _w(run[0]) or run[0][1] not in names:
        return False
    args = []
    for t in run[1:]:
        if t[0] == "r" and redirs:
            continue
        if not (_plain(t) or (var is not None and t == ("v", var, True) and run[0][1] in _ECHOES)):
            return False
        args.append(t)
    if run[0][1] == "sleep":
        return len(args) == 1 and args[0][0] == "w" and bool(_SLEEP_RE.match(args[0][1]))
    return True


def _kill_var(run, var):
    """True for `kill [SIG] "$var"` or `kill [SIG] $var` (bash's builtin kill)."""
    if not run or not _w(run[0], "kill"):
        return False
    k = 1 + _signal(run, 1, "kill")
    return k == len(run) - 1 and run[k][0] == "v" and run[k][1] == var


def _upto_sep(toks, i):
    """The index of the first `;` or newline at or after toks[i], or len(toks)."""
    while i < len(toks) and toks[i] not in _SEPS:
        i += 1
    return i


def _seps(toks, i):
    """The index past the separator at toks[i] before `do` or `done`: one optional `;`, then any newlines; raise
    _Refused when there is none."""
    j = i + 1 if i < len(toks) and toks[i] == _SEPS[0] else i
    while j < len(toks) and toks[j] == _SEPS[1]:
        j += 1
    if j == i:
        raise _Refused
    return j


def _body(toks, i, check):
    """(the index past `done`, the body's commands) for a loop body that starts at toks[i], just after `do`;
    check(command) must hold for each command, and the body must hold at least one."""
    runs = []
    while i < len(toks) and toks[i] == _SEPS[1]:
        i += 1
    while True:
        if i >= len(toks):
            raise _Refused
        if _w(toks[i], "done"):
            if not runs:
                raise _Refused
            return i + 1, runs
        j = _upto_sep(toks, i)
        run = toks[i:j]
        if not run or j >= len(toks) or not check(run):
            raise _Refused
        runs.append(run)
        i = _seps(toks, j)


def _for(toks, i):
    """Shape (b) at toks[i]: (("signal", LITERAL), the index past `done`), or raise _Refused."""
    n = len(toks)
    if (i + 4 >= n or not _w(toks[i + 1]) or not _NAME_RE.fullmatch(toks[i + 1][1])
            or toks[i + 1][1] in _RESERVED or not _w(toks[i + 2], "in") or toks[i + 3] != ("op", "$(", None)):
        raise _Refused
    var, j = toks[i + 1][1], i + 4
    while j < n and toks[j] != ("op", ")", None):
        j += 1
    lit = _pgrep(toks[i + 4:j], ())
    if lit is None:
        raise _Refused
    j = _seps(toks, j + 1)
    if not (j < n and _w(toks[j], "do")):
        raise _Refused
    end, runs = _body(toks, j + 1, lambda run: _kill_var(run, var) or _simple(run, _FOR_BODY, var=var))
    if sum(1 for run in runs if _kill_var(run, var)) != 1:
        raise _Refused
    return ("signal", lit), end


def _wait(toks, i):
    """Shape (d) at toks[i]: (("wait", LITERAL), the index past `done`), or raise _Refused."""
    n, j = len(toks), i + 1
    if toks[i][1] == "until":
        if not (j < n and toks[j] == ("op", "!", None)):
            raise _Refused
        j += 1
    e = _upto_sep(toks, j)
    k = e
    while k > j and toks[k - 1][0] == "r":
        k -= 1
    lit = _pgrep(toks[j:k], _LISTING)
    if lit is None:
        raise _Refused
    j = _seps(toks, e)
    if not (j < n and _w(toks[j], "do")):
        raise _Refused
    end, _ = _body(toks, j + 1, lambda run: _simple(run, _WAIT_BODY))
    return ("wait", lit), end


def _element(toks, i):
    """(the shape found, or None for a benign command; the index past the element) for the element at toks[i];
    raise _Refused for anything else."""
    t = toks[i]
    if _w(t, "for"):
        return _for(toks, i)
    if _w(t, "while") or _w(t, "until"):
        return _wait(toks, i)
    j = _upto_sep(toks, i)
    run = toks[i:j]
    for shape in (_pkill, _kill_sub, _xargs_kill):
        lit = shape(run)
        if lit is not None:
            return ("signal", lit), j
    if _simple(run, _BENIGN, redirs=True):
        return None, j
    raise _Refused


def _shape(toks):
    """The first shape, (kind, LITERAL), of a command whose every element is a shape or a benign command; None
    when it holds no shape. Raise _Refused when any element is neither."""
    first, i, n = None, 0, len(toks)
    while True:
        while i < n and toks[i] == _SEPS[1]:
            i += 1
        if i >= n:
            return first
        found, i = _element(toks, i)
        if first is None:
            first = found
        if i < n:
            if toks[i] not in _SEPS:
                raise _Refused
            i += 1


def _words(text):
    """The words of text as Python's shlex reads them (POSIX rules, operators split off, `#` comments dropped);
    raises ValueError where shlex cannot read it (an unterminated quote, notably)."""
    lexer = shlex.shlex(text, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    return list(lexer)


def _pattern(words, k):
    """The pattern operand after the matcher words[k]: the first later word that does not start with `-`, or the
    word just after `--`; None at an operator word or the end."""
    for j in range(k + 1, len(words)):
        w = words[j]
        if w and all(ch in _PUNCT for ch in w):
            return None
        if w == "--":
            nxt = words[j + 1] if j + 1 < len(words) else ""
            return nxt if nxt and not all(ch in _PUNCT for ch in nxt) else None
        if not w.startswith("-"):
            return w
    return None


def _self_text(text, raw, depth, seen):
    """True when a matcher in text has a pattern that is text raw holds (the NOTE paragraph); a word that holds a
    blank and names a matcher is read again while depth lasts; seen holds patterns already found absent. Raises
    ValueError where shlex cannot read."""
    words = _words(text)
    ps = any(w[w.rfind("/") + 1:] == "ps" for w in words)
    for k, w in enumerate(words):
        name = w[w.rfind("/") + 1:]
        if name in _MATCHERS or (ps and name == "grep"):
            pat = _pattern(words, k)
            if pat is not None and "$" not in pat and "`" not in pat and pat not in seen:
                if pat in raw:
                    return True
                seen.add(pat)
        if depth > 1 and (" " in w or "\t" in w or "\n" in w) and _MATCHER_RE.search(w):
            if _self_text(w, raw, depth - 1, seen):
                return True
    return False


def _names_matcher(text):
    """True when the raw text names a process matcher (a matcher word, or ps and grep, each by word boundary)."""
    return bool(_MATCHER_RE.search(text) or (_PS_RE.search(text) and _GREP_RE.search(text)))


def _note_scan(text):
    """("note", message) or None, for a command that is not denied."""
    try:
        return ("note", _NOTE) if _self_text(text, text, _NOTE_DEPTH, set()) else None
    except ValueError:
        return ("note", _NOTE_UNREAD) if _names_matcher(text) else None


def _opted_out(text):
    """True when the command ends with the `# self-match-ok` comment: the marker regex matches on the last line,
    and the text before the marker is read whole by the tokenizer (so the `#` stands outside quotes, at a word's
    start) or, where the tokenizer refuses it, shlex reads the command alike with and without the marker."""
    s = text.rstrip(" \t\n")
    m = _OPT_OUT_RE.search(s, s.rfind("\n") + 1)
    if not m:
        return False
    head = s[:m.start()]
    try:
        _tokens(head)
        return True
    except _Refused:
        pass
    try:
        return _words(head) == _words(s)
    except ValueError:
        return False


def _shown(lit):
    """The LITERAL as quoted in the deny reason: cut after _SHOWN characters."""
    return lit if len(lit) <= _SHOWN else lit[:_SHOWN] + "..."


def _verdict(cmd):
    """("deny", reason), ("note", message), or None (silent) for one command."""
    if len(cmd) > _SCAN_LIMIT or len(cmd.encode("utf-8", "surrogatepass")) > _SCAN_LIMIT:
        return ("note", _NOTE_UNREAD) if _names_matcher(cmd) else None
    if _opted_out(cmd):
        return None
    try:
        found = _shape(_tokens(cmd))
    except _Refused:
        found = None
    if found is not None:
        return ("deny", _REASON.format(lit=_shown(found[1]), effect=_EFFECTS[found[0]]))
    return _note_scan(cmd)


def _decide(payload, env):
    """The output object for a parsed payload (a deny or a note), or None to stay silent."""
    if _is_worker(env) or not isinstance(payload, dict):
        return None
    if payload.get("tool_name") != "Bash" or payload.get("hook_event_name", "PreToolUse") != "PreToolUse":
        return None
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return None
    cmd = tool_input.get("command")
    if not isinstance(cmd, str) or not cmd:
        return None
    try:
        found = _verdict(cmd)
    except Exception:
        found = ("note", _NOTE_ERROR)  # an internal error allows the call, with a note
    if found is None:
        return None
    if found[0] == "deny":
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                       "permissionDecisionReason": found[1]}}
    return {"systemMessage": found[1]}


def _emit_line(text, *stream):
    """Write one line to `stream` (default stdout) and flush it. A line meant for stderr is written there or
    dropped (sys.stderr is None when descriptor 2 was closed at startup), never sent to stdout. On any output
    failure point that descriptor at /dev/null, so the interpreter's shutdown flush cannot fail either; if even
    that rescue fails, end the process at once with status 0: the hook always exits 0."""
    s = stream[0] if stream else sys.stdout
    if s is None:
        return
    try:
        s.write(text + "\n")
        s.flush()
    except Exception:
        try:
            fd = os.open(os.devnull, os.O_WRONLY)
            try:
                os.dup2(fd, s.fileno())
            finally:
                os.close(fd)
        except Exception:
            os._exit(0)


def main(argv):
    """The hook: always 0, output only a deny or note line. `--self-test` alone runs the self-test instead; any
    other argv returns 0 silently before stdin is read."""
    if not isinstance(argv, (list, tuple)) or not all(isinstance(a, str) for a in argv) or not argv:
        return 0  # a bad argv: fail open, reading nothing
    if list(argv[1:]) == ["--self-test"]:
        return _self_test()
    if len(argv) != 1:
        return 0  # an unsupported argument: fail open, reading nothing
    try:
        if _is_worker(os.environ):
            _emit_line(_WORKER_LINE, sys.stderr)
            return 0
        out = _decide(_read_payload(), os.environ)
    except Exception:
        return 0  # malformed or unreadable input: fail open
    if out is not None:
        _emit_line(json.dumps(out))
    return 0


def _self_test():
    import ast
    import hashlib
    import io
    import shutil
    import stat
    import subprocess
    import tempfile
    import tokenize
    import unittest

    here = os.path.abspath(__file__)
    module = globals()

    def decide(command, env=None, **extra):
        payload = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": command}}
        payload.update(extra)
        return _decide(payload, {} if env is None else env)

    def run_hook(data, env=None, argv=(), timeout=30):
        """The hook run as a process: (status, stdout, stderr); env is the whole environment."""
        p = subprocess.run([sys.executable, "-I", "-S", "-B", here] + list(argv), input=data, capture_output=True,
                           env={"LC_ALL": "C"} if env is None else env, timeout=timeout)
        return p.returncode, p.stdout, p.stderr

    def payload_bytes(command, **extra):
        payload = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": command}}
        payload.update(extra)
        return json.dumps(payload).encode()

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

    def trusted_bash():
        """A root-owned bash that no one else can write, in /usr/bin or /bin, or None."""
        for path in ("/usr/bin/bash", "/bin/bash"):
            try:
                real = os.path.realpath(path)
                parent = os.path.dirname(real)
                if parent not in ("/usr/bin", "/bin"):
                    continue
                fst, pst = os.lstat(real), os.stat(parent)
                if (stat.S_ISREG(fst.st_mode) and fst.st_uid == 0 and not fst.st_mode & 0o022
                        and fst.st_mode & 0o111 and pst.st_uid == 0 and not pst.st_mode & 0o022):
                    return real
            except (OSError, ValueError):
                continue
        return None

    class patched(object):
        """Replace module globals for one block (a mutant); the real ones come back after it."""
        def __init__(self, **values):
            self.values, self.saved = values, {}

        def __enter__(self):
            for key, value in self.values.items():
                self.saved[key] = module[key]
                module[key] = value

        def __exit__(self, *exc):
            module.update(self.saved)
            return False

    def outcome(command):
        """"deny", "note" or "silent" for one command, by _decide."""
        out = decide(command)
        if out is None:
            return "silent"
        return "deny" if "hookSpecificOutput" in out else "note"

    # The deny vectors: (command, kind, LITERAL).
    deny_vectors = [
        ("pkill -f qa-x/", "signal", "qa-x/"),
        ('pkill -9 -f "qa-x/"', "signal", "qa-x/"),
        ("pkill --signal=KILL -e -f -- 'qa-x/'", "signal", "qa-x/"),
        ('for p in $(pgrep -f "qa-x/"); do kill "$p"; done', "signal", "qa-x/"),
        ('for p in $(pgrep -f qa-x/); do echo "$p"; kill -9 $p; done', "signal", "qa-x/"),
        ("kill $(pgrep -f qa-x/)", "signal", "qa-x/"),
        ("pgrep -f qa-x/ | xargs -r kill -9", "signal", "qa-x/"),
        ("pgrep -f qa-x/ | xargs kill", "signal", "qa-x/"),
        ("while pgrep -f qa-x/ >/dev/null; do sleep 2; done", "wait", "qa-x/"),
        ("until ! pgrep -f qa-x/ >/dev/null 2>&1; do sleep 1; done", "wait", "qa-x/"),
        ("cd /tmp; pkill -f qa-x.d", "signal", "qa-x.d"),
        ("echo '# self-match-ok: x'; pkill -f qa-x/", "signal", "qa-x/"),
    ]
    # More deny forms, from the signal, grammar and spelling tests below; bash -n reads them too.
    deny_more = [
        "pkill -SIGKILL -f qa-x/", "pkill --signal KILL --full qa-x/", "kill -s KILL $(pgrep -f qa-x/)",
        "kill -SIGTERM $(pgrep --full -- qa-x/)", "pgrep -f qa-x/ | xargs --no-run-if-empty kill --signal TERM",
        "pgrep -f qa-x/ | xargs kill -s HUP", 'for p in $(pgrep -f qa-x/); do kill -s 9 "$p"; done',
        "for p in $(pgrep -f qa-x/)\ndo\n  printf '%s\\n' \"$p\"\n  kill $p\n  sleep 0.5\ndone",
        "while pgrep -a -f qa-x/ &>/dev/null; do date; echo waiting; sleep 1s; done",
        "sleep 1; pkill -f qa-x/;", "pkill -f qa-x/\n\necho done; ls /tmp >/dev/null 2>&1",
        "echo a; pkill -f qa-x/; pkill -f qa-y/",
    ]
    # The counterexamples: each must not be denied; each gets a note.
    counter_vectors = [
        "pkill -f 'qa2451newline\nend'",
        "until pgrep -f qa2451until/ >/dev/null; do sleep 1; done",
        "while ! pgrep -f qa2451while/ >/dev/null; do sleep 1; done",
        "while pgrep -f x/; do sleep 1; break; done",
        "ps -p 1 -o pid,args | grep qa2451/ | awk '{print $1}' | xargs -r kill",
        "pgrep -f qa2451/ | tr -d '0-9' | xargs -r kill",
        "pgrep -f x/ | awk '{print $2}' | xargs kill",
        'for p in $(pgrep -f x/); do kill "$recorded_pid"; break; done',
        "exec pkill -f qa2451exec/",
        "pkill -x -f 'qa2451/\\|.*'",
        "pkill -f 'w[t]-x/'",
        "pkill -f 'qq7\\.dot'",
    ]

    class T(unittest.TestCase):
        def assertDeny(self, command, kind=None, lit=None, **kw):
            out = decide(command, **kw)
            self.assertIsNotNone(out, command)
            self.assertNotIn("systemMessage", out)
            h = out["hookSpecificOutput"]
            self.assertEqual((h["hookEventName"], h["permissionDecision"]), ("PreToolUse", "deny"), command)
            reason = h["permissionDecisionReason"]
            self.assertIn("Blocked (pattern-self-match)", reason)
            if kind is not None:
                self.assertIn(_EFFECTS[kind], reason)
            if lit is not None:
                self.assertIn("`" + lit + "` is plain text", reason)
            return reason

        def assertNote(self, command, message=_NOTE, **kw):
            out = decide(command, **kw)
            self.assertEqual(out, {"systemMessage": message}, command)

        def assertSilent(self, command, **kw):
            self.assertIsNone(decide(command, **kw), command)

        def test_01_deny_vectors(self):
            for command, kind, lit in deny_vectors:
                self.assertDeny(command, kind, lit)
            for command in deny_more:
                self.assertDeny(command)

        def test_02_subagent_payload_checked(self):
            self.assertDeny("pkill -f qa-x/", "signal", "qa-x/", agent_id="a1")
            self.assertDeny("pkill -f qa-x/", agent_id=None)

        def test_03_counterexamples_get_a_note(self):
            for command in counter_vectors:
                self.assertNote(command)

        def test_04_note_scan(self):
            for command in ("pkill sleep", "pidof sshd", "ps aux | grep x | grep -v grep", "killall -9 node",
                            "/usr/bin/pkill -f qa-x/", "echo pkill x", "pgrep -f x/ >/dev/null && echo up",
                            "pkill -f qa-x/ 2>/dev/null", "pkill -f qa-x/ &", "X=1 pkill -f qa-x/",
                            "sleep infinity; pkill -f qa-x/", "; pkill -f qa-x/", "pkill -f qa-x/ | cat",
                            "pkill -f -- -qa", "ssh host 'pkill -f qa-x/'", "pkill -f 'qa-x/'#c"):
                self.assertNote(command)
            for command in ('pkill -f "$T"', 'pkill -f -- "$(cat /tmp/pat)"', "kill 12345; kill -- -4242", "echo hi",
                            "pkill -f `cat /tmp/pat`", "pgrep", "ls; pwd", "grep x f", "echo 'a'b"):
                self.assertSilent(command)

        def test_05_opt_out(self):
            d1 = "pkill -f qa-x/"
            for command in (d1 + "  # self-match-ok: intended", d1 + " #self-match-ok", d1 + " # self-match-ok\n",
                            d1 + ";# self-match-ok: x", "pkill sleep # self-match-ok",
                            "ps aux | grep x | awk '{print $2}' # self-match-ok: listed only"):
                self.assertSilent(command)
            for command in ("echo '# self-match-ok: x'; " + d1, "echo ' # self-match-ok: x'; " + d1,
                            "echo \"# self-match-ok\"; " + d1):
                self.assertDeny(command)
            for command in (d1 + " # self-match-okay", d1 + "#self-match-ok", "pkill sleep '# self-match-ok'",
                            "# self-match-ok\n" + d1 + " # later"):
                self.assertNote(command)

        def test_06_signal_spellings(self):
            for command in ("pkill -15 -f qa-x/", "pkill -HUP -f qa-x/", "pkill -f --signal=SIGINT qa-x/",
                            "kill -QUIT $(pgrep -f qa-x/)", "kill -s SIGKILL $(pgrep -f qa-x/)",
                            "pgrep -f qa-x/ | xargs -r kill --signal=KILL"):
                self.assertDeny(command, "signal")
            for command in ("kill --signal KILL $(pgrep -f qa-x/)", "kill --signal=KILL $(pgrep -f qa-x/)",
                            'for p in $(pgrep -f qa-x/); do kill --signal=9 "$p"; done', "pkill -s 9 -f qa-x/",
                            "pkill -9 -15 -f qa-x/", "pkill -USR1 -f qa-x/", "kill -n 9 $(pgrep -f qa-x/)",
                            "pkill -f -s0 qa-x/", "kill -9 -- $(pgrep -f qa-x/)", "pkill --signal 0 -f qa-x/"):
                self.assertNote(command)

        def test_07_grammar_edges(self):
            for command in ("for p in $(pgrep -f qa-x/); do kill $p; kill $p; done",
                            "for p in $(pgrep -f qa-x/); do echo $p; kill $p; done",
                            "for p in $(pgrep -f qa-x/); do kill $p done",
                            "for p in $(pgrep -f qa-x/); do ; kill $p; done",
                            "for in in $(pgrep -f qa-x/); do kill $in; done",
                            "for p in $(pgrep -f qa-x/) x; do kill $p; done",
                            "while pgrep -f qa-x/; do sleep 1; done > log",
                            "while pgrep -f qa-x/ >log; do sleep 1; done",
                            "while pgrep -f qa-x/; do done", "while pgrep -f qa-x/ do sleep 1; done",
                            "kill $(pgrep -f qa-x/) 1", "kill \"$(pgrep -f qa-x/)\"", "kill $(pgrep -f qa-x/)x",
                            "kill $( pgrep -f qa-x/ ; )", "pgrep -f qa-x/ | xargs -r kill 1",
                            "pgrep -f qa-x/ | xargs -r -r kill", "pkill -f qa-x/ qa-y/", "pkill -f qa-x/ --",
                            "pkill -f ''", "pkill -f \"qa x\"", "pkill -f qa~x",
                            "echo -n a; pkill -f qa-x/", "cd ~; pkill -f qa-x/", "true\n;pkill -f qa-x/",
                            "pkill -f qa-x/;; echo", "pkill -f qa-x/ || true", "! pkill -f qa-x/"):
                self.assertNote(command)
            for command in ("for p in $(pgrep -f qa-x/); do kill $p; done; echo after",
                            " \n pkill -f qa-x/ \n", "pkill\t-f\tqa-x/", "true; : ; pwd; date; ls; pkill -f qa-x/",
                            "while pgrep -c -l -f -- qa-x/ 2>/dev/null >/dev/null; do :; done"):
                self.assertDeny(command)
            self.assertSilent("pkill -f '-qa'")  # pkill reads -qa as options: no pattern
            self.assertIn("`" + "q" * _SHOWN + "...` is plain text", self.assertDeny("pkill -f " + "q" * 300))

        def test_08_robustness(self):
            self.assertNote("echo 'pkill -f qa-x/", _NOTE_UNREAD)
            self.assertNote("pkill -f qa-x/ " + "x" * 70000, _NOTE_UNREAD)
            self.assertNote("echo " + chr(233) * 40000 + "; pgrep -f qa-x/", _NOTE_UNREAD)  # over 64 KiB of UTF-8
            self.assertSilent("echo " + "x" * 70000)
            self.assertSilent("echo 'unterminated")

            def boom(text):
                raise RuntimeError("seeded internal error")
            with patched(_tokens=boom):
                self.assertEqual(decide("pkill -f qa-x/"), {"systemMessage": _NOTE_ERROR})
            with patched(_note_scan=boom):
                self.assertEqual(decide("pkill sleep"), {"systemMessage": _NOTE_ERROR})
            self.assertDeny("pkill -f qa-x/")  # the real code is back
            for p in (None, 7, "x", [], {}, {"tool_name": "Bash"}, {"tool_name": "Bash", "tool_input": "x"},
                      {"tool_name": "Bash", "tool_input": {"command": 7}},
                      {"tool_name": "Bash", "tool_input": {"command": ""}},
                      {"tool_name": "Read", "tool_input": {"command": "pkill -f qa-x/"}},
                      {"tool_name": None, "tool_input": {"command": "pkill -f qa-x/"}}):
                self.assertIsNone(_decide(p, {}), p)
            self.assertIsNone(decide("pkill -f qa-x/", hook_event_name="PostToolUse"))
            for env in ({"AIQT_HOOKS_WORKER": "1"}, {"ORCH_WORKER": "1"}, {"ORCH_VERIFY_OWNER": ""},
                        {"AIQT_HOOKS_WORKER": "0", "ORCH_WORKER": "1"}):
                self.assertIsNone(decide("pkill -f qa-x/", env=env), env)
            for env in ({"AIQT_HOOKS_WORKER": "0"}, {"AIQT_HOOKS_WORKER": "yes"}, {"ORCH_WORKER": "0"}):
                self.assertIsNotNone(decide("pkill -f qa-x/", env=env), env)

        def test_09_process_outputs(self):
            rc, out, err = run_hook(payload_bytes("pkill -f qa-x/"))
            self.assertEqual((rc, err), (0, b""))
            self.assertTrue(out.endswith(b"\n") and out.count(b"\n") == 1, out)
            h = json.loads(out)["hookSpecificOutput"]
            self.assertEqual(h["permissionDecision"], "deny")
            rc, out, err = run_hook(payload_bytes("pkill sleep"))
            self.assertEqual((rc, err, out.count(b"\n")), (0, b"", 1))
            self.assertEqual(json.loads(out), {"systemMessage": _NOTE})
            self.assertEqual(run_hook(payload_bytes("echo hi")), (0, b"", b""))
            self.assertEqual(run_hook(payload_bytes("pkill -f qa-x/", agent_id="a1"))[1].count(b"deny"), 1)
            out = run_hook(payload_bytes("pkill -f " + chr(233) + "x"))[1]
            out.decode("ascii")  # json.dumps writes ASCII escapes

        def test_09_process_fail_open(self):
            bad = (b"", b"not json", b"[1, 2]", b"7", b"\xff\xfe\x00garbage", b'{"tool_name": "Bash"',
                   payload_bytes("pkill -f qa-x/")[:-1],
                   payload_bytes("pkill -f qa-x/", tool_name="Read"),
                   payload_bytes("pkill -f qa-x/", hook_event_name="PostToolUse"))
            for data in bad:
                self.assertEqual(run_hook(data), (0, b"", b""), data[:30])
            for extra in ({"AIQT_HOOKS_WORKER": "1"}, {"ORCH_WORKER": "1"}, {"ORCH_VERIFY_OWNER": ""}):
                env = dict({"LC_ALL": "C"}, **extra)
                rc, out, err = run_hook(payload_bytes("pkill -f qa-x/"), env)
                self.assertEqual((rc, out, err), (0, b"", _WORKER_LINE.encode() + b"\n"), extra)

        def test_10_argv(self):
            old = sys.stdout
            sys.stdout = io.StringIO()
            try:
                for argv in (None, 7, "--self-test", ["x", 3], {"a": 1}, []):
                    self.assertEqual(main(argv), 0)
                self.assertEqual(sys.stdout.getvalue(), "")
            finally:
                sys.stdout = old
            for argv in (["--self-test", "x"], ["-x"], ["--selftest"], [""]):
                self.assertEqual(run_hook(payload_bytes("pkill -f qa-x/"), argv=argv), (0, b"", b""), argv)

        def test_10_directory_stdin_guard(self):
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

        def test_11_vendor_hashes(self):
            blocks = vendor_blocks()
            self.assertEqual(set(blocks), set(_VENDOR))
            for name, (head, body) in blocks.items():
                ref, first, last, digest = _VENDOR[name]
                self.assertEqual(head[4:], ["from", "%s:%d-%d" % (ref, first, last)], name)
                self.assertEqual(hashlib.sha256(body).hexdigest(), digest, name)
                self.assertEqual(_strip_source(body.decode("ascii")), body.decode("ascii"), name)  # stripped
                compile(body, name, "exec")  # the stripped block is still whole Python

        def test_11_vendor_byte_identity(self):
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

        def test_11_strip_source(self):
            src = ("X = 1  # trailing\n# only a comment\nS = 'a#b'  # cut\n"
                   "def f():\n    \"\"\"Doc # kept out.\"\"\"\n    return '#'\n")
            want = "X = 1\nS = 'a#b'\ndef f():\n    return '#'\n"
            self.assertEqual(_strip_source(src), want)
            self.assertEqual(_strip_source(want), want)

        def test_12_source_house_rules(self):
            src = open(here, "rb").read()
            text = src.decode("ascii")
            for n, line in enumerate(src.split(b"\n"), 1):
                self.assertLessEqual(len(line), 120, n)
            for bad in (chr(8211), chr(8212)):
                self.assertNotIn(bad, text)
            kinds = set(t.type for t in tokenize.generate_tokens(io.StringIO(text).readline))
            self.assertNotIn(getattr(tokenize, "FSTRING_START", -1), kinds)  # no f-string
            ast.parse(text, feature_version=(3, 4))

        def test_12_no_internal_names(self):
            src = open(here).read().replace("." + "cla" + "ude/rules/", "")
            pattern = re.compile(r"\bG" + r"\d+\b|" + "|".join(
                ("cla" + "ude", "gem" + "ini", "cod" + "ex", "fab" + "le", "g" + "pt", "orches" + "trator",
                 "fl" + "eet", "orch" + "-verify", "la" + "b_in" + "fra", "OR" + "CH_[A-Z_]+")), re.I)
            found = set(m.group() for m in pattern.finditer(src))
            self.assertEqual(found - set(("OR" + "CH_WORKER", "OR" + "CH_VERIFY_OWNER")), set())

        def test_13_cost_growth(self):
            # host-independent: the work is the count of Python lines this module executes (sys.settrace), which
            # no machine speed or load can change; 8x the input gives near 8x the lines when linear, 64x quadratic
            def lines_run(cmd):
                count = [0]

                def local(frame, event, arg):
                    if event == "line":
                        count[0] += 1
                    return local
                old = sys.gettrace()
                sys.settrace(lambda frame, event, arg: local if frame.f_globals is module else None)
                try:
                    verdict = _verdict(cmd)
                finally:
                    sys.settrace(old)
                self.assertIsNotNone(verdict, cmd[:40])  # reached a deny or a note
                return count[0]
            for make in (lambda k: "echo a; " * k + "pkill -f qa-x/",
                         lambda k: "for p in $(pgrep -f qa-x/); do " + "echo a; " * k + "kill $p; done",
                         lambda k: "while pgrep -f qa-x/; do " + "sleep 1; " * k + "done",
                         lambda k: "echo pkill; " * k + "pgrep -f x/",
                         lambda k: "bash -c 'echo " + "a " * k + "pgrep -f x/'",
                         lambda k: "echo a; " * k + "pkill -f x/ # c",
                         lambda k: "pgrep 'a'b; " * k + "pkill x"):
                small, large = lines_run(make(30)), lines_run(make(240))
                self.assertLess(large / small, 16, (small, large, make(2)))

        def test_13_hang_ceiling(self):
            shapes = ("$(" * 30000, "'" * 60001, "pkill " * 10000, "pgrep 'a'b; " * 5000,
                      "pkill -f x/ # self-match-ok " * 2000, "for p in $(pgrep -f x/); do " * 6000,
                      "bash -c '" * 7000 + "pkill x", "a " * 30000 + "| grep ps")
            for c in shapes:
                rc, out, err = run_hook(payload_bytes(c))
                self.assertEqual((rc, err), (0, b""), c[:40])

        def test_14_bash_accepts_deny_vectors(self):
            exe = trusted_bash()
            if exe is None:
                self.skipTest("SKIPPED, no trusted bash")
            for command in [c for c, _, _ in deny_vectors] + deny_more:
                self.assertEqual(outcome(command), "deny", command)
                # -n: bash parses the command and runs nothing
                p = subprocess.run([exe, "--norc", "--noprofile", "-n"], input=command.encode(), capture_output=True,
                                   env={"LC_ALL": "C"}, timeout=10, cwd="/")
                self.assertEqual(p.returncode, 0, (command, p.stderr))

        # One mutant per clause: each flips its killing vectors from the real outcome to the mutant's.
        def kill_mutant(self, values, vectors, real, mutated):
            for command in vectors:
                self.assertEqual(outcome(command), real, command)
            with patched(**values):
                for command in vectors:
                    self.assertEqual(outcome(command), mutated, command)
            for command in vectors:
                self.assertEqual(outcome(command), real, command)

        def test_m01_charset_plus(self):
            self.kill_mutant(dict(_LITERAL_RE=re.compile(r"[A-Za-z0-9_./:@%=,+-]+\Z")), ["pkill -f 'qa+x'"],
                             "note", "deny")

        def test_m02_charset_bar(self):
            self.kill_mutant(dict(_LITERAL_RE=re.compile(r"[A-Za-z0-9_./:@%=,|-]+\Z")), ["pkill -f 'a|b'"],
                             "note", "deny")

        def test_m03_charset_newline(self):
            self.kill_mutant(dict(_LITERAL_RE=re.compile(r"[A-Za-z0-9_./:@%=,\n-]+\Z")), [counter_vectors[0]],
                             "note", "deny")

        def test_m04_leading_dash(self):
            self.kill_mutant(dict(_literal=lambda tok: tok[0] == "w" and bool(_LITERAL_RE.match(tok[1]))),
                             ["pkill -f -- -qa"], "note", "deny")

        def test_m05_full_required(self):
            real = _pkill
            self.kill_mutant(dict(_pkill=lambda run: real(run[:1] + [("w", "-f", "bare")] + run[1:])),
                             ["pkill qa-x"], "note", "deny")

        def drop_option(self, option, values):
            real = _pkill

            def mutant(run):
                out, k = [], 0
                while k < len(run):
                    if run[k] == ("w", option, "bare"):
                        k += 1 + values
                        continue
                    out.append(run[k])
                    k += 1
                return real(out)
            return dict(_pkill=mutant)

        def test_m06_option_x(self):
            self.kill_mutant(self.drop_option("-x", 0), ["pkill -x -f qa-x/"], "note", "deny")

        def test_m07_option_ancestors(self):
            self.kill_mutant(self.drop_option("-A", 0), ["pkill -A -f qa-x/"], "note", "deny")

        def test_m08_option_selector(self):
            self.kill_mutant(self.drop_option("-u", 1), ["pkill -u me -f qa-x/"], "note", "deny")

        def test_m09_option_cluster(self):
            real = _pkill

            def mutant(run):
                out = []
                for t in run:
                    if _w(t) and re.match(r"-[a-z]{2,}\Z", t[1]):
                        out.extend(("w", "-" + ch, "bare") for ch in t[1][1:])
                    else:
                        out.append(t)
                return real(out)
            self.kill_mutant(dict(_pkill=mutant), ["pkill -ef qa-x/"], "note", "deny")

        def test_m10_signal_zero(self):
            self.kill_mutant(dict(_SIG_VALUES=_SIG_VALUES | frozenset(("0",))),
                             ["pkill -0 -f qa-x/", "kill -0 $(pgrep -f qa-x/)"], "note", "deny")

        def test_m11_prefix_words(self):
            real = _element

            def mutant(toks, i):
                if _w(toks[i]) and toks[i][1] in ("sudo", "exec", "env", "nice", "timeout", "command", "nohup"):
                    return real(toks, i + 1)
                return real(toks, i)
            self.kill_mutant(dict(_element=mutant), ["sudo pkill -f qa-x/"], "note", "deny")

        def test_m12_and_list(self):
            real = _element

            def mutant(toks, i):
                ands = [k for k in range(i, _upto_sep(toks, i)) if toks[k] == ("op", "&&", None)]
                return real(toks, ands[-1] + 1 if ands else i)
            self.kill_mutant(dict(_element=mutant), ["false && pkill -f qa-x/"], "note", "deny")

        def test_m13_any_prior_command(self):
            real = _simple
            self.kill_mutant(dict(_simple=lambda run, names, redirs=False, var=None:
                                  names is _BENIGN or real(run, names, redirs, var)),
                             ["exit 0; pkill -f qa-x/"], "note", "deny")

        def test_m14_compound_forms(self):
            real = _tokens
            self.kill_mutant(dict(_tokens=lambda text: [t for t in real(text) if not (_w(t) and t[1] in ("if", "then",
                                                                                                         "fi"))],
                                  _BENIGN=_BENIGN | frozenset(("false",))),
                             ["if false; then pkill -f qa-x/; fi"], "note", "deny")

        def test_m15_tail_unparsed(self):
            def first_only(toks):
                i, n = 0, len(toks)
                while True:
                    while i < n and toks[i] == _SEPS[1]:
                        i += 1
                    if i >= n:
                        return None
                    found, i = _element(toks, i)
                    if found is not None:
                        return found
                    i += 1
            self.kill_mutant(dict(_shape=first_only), ["pkill -f qa-x/; if"], "note", "deny")

        def test_m16_loop_variable(self):
            real = _kill_var
            self.kill_mutant(dict(_kill_var=lambda run, var: real(run, run[-1][1] if run and run[-1][0] == "v"
                                                                  else var)),
                             ['for p in $(pgrep -f qa-x/); do kill "$q"; done'], "note", "deny")

        def test_m17_loop_body(self):
            real = _simple
            self.kill_mutant(dict(_simple=lambda run, names, redirs=False, var=None:
                                  names is _FOR_BODY or real(run, names, redirs, var)),
                             ['for p in $(pgrep -f qa-x/); do break; kill "$p"; done'], "note", "deny")
            # the plan's vector stops at the tokenizer (`[`), before the body clause: a note either way
            self.kill_mutant(dict(_simple=lambda run, names, redirs=False, var=None:
                                  names is _FOR_BODY or real(run, names, redirs, var)),
                             ['for p in $(pgrep -f qa-x/); do [ "$p" = "$$" ] || kill "$p"; done'], "note", "note")

        def test_m18_substitution_stages(self):
            real = _pgrep

            def mutant(run, extra):
                bars = [k for k, t in enumerate(run) if t == ("op", "|", None)]
                return real(run[:bars[0]] if bars else run, extra)
            self.kill_mutant(dict(_pgrep=mutant), ["kill $(pgrep -f qa-x/ | head -1)"], "note", "deny")

        def test_m19_substitution_options(self):
            real = _pgrep
            self.kill_mutant(dict(_pgrep=lambda run, extra: real(run, _LISTING)), ["kill $(pgrep -a -f qa-x/)"],
                             "note", "deny")

        def test_m20_any_xargs(self):
            real = _pgrep

            def mutant(run):
                bars = [k for k, t in enumerate(run) if t == ("op", "|", None)]
                if len(bars) != 1 or not _w(run[bars[0] + 1] if bars[0] + 1 < len(run) else None, "xargs"):
                    return None
                return real(run[:bars[0]], ())
            self.kill_mutant(dict(_xargs_kill=mutant), ["pgrep -f qa-x/ | xargs -n1 echo kill"], "note", "deny")

        def test_m21_loop_polarity(self):
            real = _tokens
            self.kill_mutant(dict(_tokens=lambda text: [("w", "while", "bare") if t == ("w", "until", "bare") else t
                                                        for t in real(text) if t != ("op", "!", None)]),
                             counter_vectors[1:3], "note", "deny")

        def test_m22_wait_body(self):
            self.kill_mutant(dict(_WAIT_BODY=_WAIT_BODY | frozenset(("break",))),
                             ["while pgrep -f x/; do sleep 1; break; done"], "note", "deny")
            # the plan's deadline vector stops at the tokenizer (`[`), before the body clause: a note either way
            self.kill_mutant(dict(_WAIT_BODY=_WAIT_BODY | frozenset(("break",))),
                             ["while pgrep -f x/; do sleep 1; [ $SECONDS -gt 9 ] && break; done"], "note", "note")

        def test_m23_nested_string(self):
            real = _tokens

            def mutant(text):
                if text.startswith("bash -c '") and text.endswith("'"):
                    return real(text[len("bash -c '"):-1])
                return real(text)
            self.kill_mutant(dict(_tokens=mutant), ["bash -c 'while pgrep -f x/; do :; done'"], "note", "deny")

        def test_m24_opt_out(self):
            # ignoring the opt-out leaves the comment, which the tokenizer refuses: the note comes back
            self.kill_mutant(dict(_opted_out=lambda text: False), ["pkill -f qa-x/  # self-match-ok: intended"],
                             "silent", "note")

    suite = unittest.defaultTestLoader.loadTestsFromTestCase(T)
    result = unittest.TextTestRunner(stream=sys.stderr, verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
