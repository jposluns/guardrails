#!/usr/bin/env python3
"""PreToolUse Bash hook (ungated-record): deny a completion record written on a path where its test gate has failed.

WHAT IT DOES
    A command that runs a test gate and, in the same command, writes a completion claim to a file (`pytest;
    echo PASS >> report`) writes that claim whether or not the gate passed: `;`, a newline, `||`, or a pipe
    that hides the gate's status lets the record run after a failure. The record then claims a result nothing
    checked, and a later reader trusts it. This hook denies that shape before the command runs, when the fix
    is one operator, and its reason names the gated rewrite.

    Event: PreToolUse, matcher Bash. Register the launch line REGISTRATION (below the imports), filled with
    python3 and this file's absolute path. Output: nothing (allow), or ONE line holding the standard PreToolUse
    deny object. Exit status: always 0; the decision travels in the JSON. The verdict is deny or silence: this
    hook never asks.

    A GATE is a simple command whose command word (found past assignments and the env, sudo, time, nohup,
    command, exec, and timeout prefixes) is one of pytest, py.test, tox, nox, bats, ctest, prove,
    jest, vitest, mocha, rspec, or phpunit; python (python3, python3.N) with `-m pytest` or `-m unittest`;
    any command carrying a `--self-test` argument; npm, pnpm, yarn, or bun with `test` or `run test` or
    `run check`; go or cargo with `test`; make, gmake, or just with a `test`, `tests`, or `check` target; or a
    script whose file name looks like a test runner (`run_tests.sh`, `test.sh`, `unit-tests.py`), run
    directly or through sh, bash, or python. Each runner's and interpreter's value-taking options are stepped
    over with their values, written apart, attached, or at the end of a short-option cluster (`make -sC dir`,
    `python3 -Wd -m pytest`), as are make's numeric -j and -l values and a cargo `+toolchain`. The shell
    conditionals `test`, `[`, and `[[` are never gates.

    A CLAIM RECORD is a simple command, echo, printf, cat (reading a here-document or here-string), or tee
    (echo and printf also after a `builtin` prefix, with its `--`), whose written text holds one of the
    uppercase words FINAL, DONE, PASS, PASSED, COMPLETE, COMPLETED, VERIFIED, SUCCESS, SUCCESSFUL, or GREEN
    (case-sensitive, whole words) or the check-mark character, and whose destination is a literal file path
    outside /dev: standard output, after its redirections are made left to right with each descriptor tracked
    (`>`, `>>`, `>|`, `&>`, `&>>`, `>& FILE`, duplications such as `3>>r 1>&3`, and moves such as `1>&3-`), or
    a tee file argument. The text cat or tee reads is its standard input, tracked the same way (`3<<<x 0<&3`
    reads x): a here-document or here-string, or, for tee whose input is not redirected, the echo, printf, or
    cat stage that feeds it in a pipeline. printf's text is its rendered output: the format parsed once and
    replayed while arguments remain, a string conversion (%s, %b, %q) writing its argument cut to the
    precision, %c a first character, a numeric conversion a digit, a `*` width or precision read from an
    argument (a negative width left-justifies), so `printf '%10.10s' PASS`, `printf '%s%s' PA SS`, and
    `printf '%*s%s' -8 PASS unit` write PASS as a word while `printf '%.2s' PASS` and `printf '%d' PASS` do not.
    A backslash escape, in the format or in a %b argument, writes a blank (a word boundary, as the newline or
    tab it usually stands for is), and a `\\c` in a %b argument ends all output there. Only literal
    text counts: a claim word in the destination path, or inside a parameter expansion (`$PASS`, `${X:-PASS}`,
    whose end is found as bash finds it: only `${` nests, and a single-quoted brace does not close it), does
    not; text after the expansion's end does (`"${X:-{} PASS}"` writes PASS).

    DENY when, in a straight-line walk of the command, a claim record runs on a path where a gate has failed.
    The walk forks each gate into its success and its failure and follows both through bash's `&&` and `||`
    short-circuits; a pipeline's status is its last stage's, so a gate in an earlier stage leaves its failure
    unseen; `exit` and `exec` end a path; a command substitution's gate counts where its status is used. It
    stays silent when:
      - the command is over 64 KiB of UTF-8 (it is not judged at all, rather than judged by a prefix);
      - the command text holds `$?`, `${?}`, or PIPESTATUS anywhere (the status is being examined);
      - a `set` command in it turns on errexit or pipefail (`set -e`, `set -euo pipefail`, `set -o errexit`);
      - it defines a function, or holds structure the walk cannot follow;
      - it ends with the opt-out comment (below);
      - bash's own parser rejects the command (the syntax oracle, `bash -n`): a command bash rejects runs
        nothing, so the deny is withdrawn; when the oracle is unavailable the lexical verdict stands.
    A compound command (if, a loop, case, a `{ }` group, a `( )` subshell) standing alone is a barrier: what
    it holds is not read, and no gate before it pairs with a record after it. As a stage of a longer pipeline
    it runs in a subshell, so it is opaque instead: its contents are not read, its status is taken as 0, and
    the pipeline's other stages are walked (`( true ) | pytest; echo PASS >> report` is denied). So
    `pytest || { echo failed; exit 1; }; echo PASS >> report` and `if pytest; then echo PASS >> report; fi` are
    allowed, as is `pytest && echo PASS >> report`.

OPT-OUT
    For a record that is deliberately not a claim, end the command with a real shell comment whose text starts
    `# record-ok`, optionally followed by a colon or blank and a reason, for example:
        make test; echo DONE >> timing.log  # record-ok: timing log, not a result
    The comment must be unquoted, begin a word, and be the last non-blank content of the command; a `#` inside
    a word or a quoted string is not a comment and does not opt out.

THREAT MODEL
    This is an accidental-habit guard, not a security boundary. The actor is a well-meaning assistant that
    joins a gate and its record with `;` and forgets the record then runs on failure; nothing here resists a
    caller that sets out to hide a record. So every internal error, every input the walk cannot follow, and
    every malformed payload fails OPEN: no output, exit 0. The hook also stays silent for a verification worker
    process (AIQT_HOOKS_WORKER set to "1"; or the legacy names, ORCH_WORKER set to "1" or ORCH_VERIFY_OWNER
    present at all, even empty), for a payload carrying agent_id (a subagent's call), for a tool_name other
    than Bash, for an event other than PreToolUse, for a bad argv, and for a stdin that is absent, unreadable,
    over 16 MiB, incomplete after 2 seconds, not JSON, or not an object. Work is bounded: the command text is
    read to 64 KiB of UTF-8 and at most 4000 tokens (past either bound the walk fails open), each token stream
    is walked once with at most a few path states, and the syntax oracle runs at most once, on the deny path
    only, under a 2 second limit. The hook never executes the command: bash is run only with -n (parse, never
    execute), from a fixed trusted path, with an empty environment, reading the command on its stdin.

RESIDUAL COVERAGE.
    This guard reads one straight-line command. The raw prefilters read the text with quotes and backslashes
    removed, so a word assembled by quoting (`pyt"est"`, `"PA"SS`) still reaches the walk. It does not catch a
    gate or record inside a loop, group,
    conditional, case, function body, nested shell string (`bash -c '...'`), eval, or script file (a compound
    pipeline stage included, whose status is taken as 0), nor a gate before a standalone compound paired with a
    record after it; a gate run in the background (`gate & ...`), whose
    status the walk does not follow; a record written by an interpreter one-liner, an editor tool, or a
    separate later call; a claim word outside the built-in uppercase vocabulary, or text built at run time (a
    variable, a substitution, an ANSI-C `$'...'` escape, an echo -e or printf escape); a claim word written
    after a `$` that bash prints literally (`'$PASS'`, `\\$PASS`), which is set aside as an expansion; a claim
    word inside an expansion's default or alternative that the lexer has already unquoted or split (a nested
    double-quoted `"}"`, or a blank in an unquoted `${X:-a PASS}`), or that follows a backslash-escaped brace
    (`"${X:-\\} PASS}"`, where bash does not close the expansion but this guard does), which is then read as
    outside it and denied, a conservative false deny; and, the other way, a claim word written after an
    expansion whose end is hidden by an escape the lexer has already removed, which is then read as inside it
    and missed: an escaped `\\${` in a double-quoted default counts as a nested `${` (`"${X:-\\${} PASS}"`
    writes PASS), and a quote the lexer has unquoted opens a span (`${X:-"'"}" PASS"\\'` writes PASS); a gate
    outside the built-in list, one run through a launcher other than the prefixes named above (`uv run`,
    `poetry run`, `nice`, `xvfb-run`), or one whose runner takes a value-taking option this guard does not
    list; a `builtin` prefix before anything but echo, printf, set, true, false, `:`, or exit; a record
    destination reached through a variable, a substitution, or a descriptor opened by an earlier command; a
    here-document record inside a command substitution; a claim in one pipeline stage concurrent with a gate
    in another stage of the same pipeline; and shell options set by a parent shell or environment. printf is
    rendered approximately: since an escape writes a blank, a claim spelled by escapes (`\\x50ASS`, an octal
    code) is missed; an invalid conversion character (`%P`), at which bash stops all output, is written as literal text
    and rendering goes on, so the text read is a superset of what bash writes (a conservative false deny, never
    a miss); and once the printf commands of one command have used the shared work budget (twice the 64 KiB
    text bound, charged per format character, replayed item, and character written), the rest are read
    unrendered, their formats and arguments each counting whole. A `*` width or precision argument is read as
    bash reads the common forms of a number; two spellings may be read differently, a very long zero-padded
    number and a `0b` binary prefix, so the padding or cut applied to the text beside it can differ from what
    bash writes. A command that examines the gate status anywhere
    in its text (a comment included), or turns on errexit or pipefail in any command of it (a subshell's or a
    compound's included), is skipped
    rather than modelled. A command that only
    dispatches work and records "dispatched" is outside the gate list and not flagged. The vendored lexer's
    own disclosed limits carry over: quotes, escapes, comments, and here-document bodies are honoured, and a
    construct it cannot follow yields no verdict. All unparseable or unfollowable input fails open with no
    output.

Self-test: python3 -I -S -B ungated-record.py --self-test
    The reference hooks the vendored blocks were copied from are looked up in the directory named by the
    G_REF_DIR environment variable, else in this file's own directory; when a reference is absent its
    byte-identity check reports SKIPPED, never a pass.
"""

import json
import os
import re
import select
import sys
import time

HOOK_ID = "ungated-record"

# The launch line to register ({python} and {hook} filled in: python3 and this file's absolute path). CPython stops
# before running any byte of this file when its stdin is a directory (exit 1), so only a guard in front of the
# interpreter can answer that one input: it exits 0 silently and otherwise execs the hook, stdin untouched.
REGISTRATION = '[ -d /dev/stdin ] && exit 0; exec "{python}" -I -S -B "{hook}"'

# Each vendored block: name -> (reference file, first line, last line, sha256 of the block's stripped text). A block
# is those reference lines with their comments and docstrings removed by _strip_source, fenced below; the
# self-test checks the hash and, when the reference file is found, that stripping the same lines of it gives the
# same bytes.
_VENDOR = {
    "lexer-constants": ("inplace-edit-verify.py", 311, 400,
        "c57738d7da963cab9fab7900b37d47536e3a3b0c4f0bd46b0221ee3d3eadda24"),
    "prefix-options": ("inplace-edit-verify.py", 401, 417,
        "f6217147196c05947f087ac3268dad32482aa6891aaf97027a42e5b5925115bd"),
    "lexer-name-patterns": ("inplace-edit-verify.py", 420, 427,
        "ab67d6760d05dcbc39a7209f3018e7b25a4fd4be0af1f8848a66d9e0333725a3"),
    "lexer-expansions": ("inplace-edit-verify.py", 492, 531,
        "4277f6ff13bbb0ef9aa0a825e16111d3990d7d2c918a07fee017f3840cbb34a8"),
    "not-keyword": ("inplace-edit-verify.py", 534, 537,
        "2ab0ba09c28ee51710a22a1604c5fd796f7b55c33c1a216bcc065695b11a8c6c"),
    "lexer": ("inplace-edit-verify.py", 547, 849,
        "dcc75a5a9873a9c71f5970028ea4410e9a236ef487b0c193d35f0650d44b44a1"),
    "lexer-helpers": ("inplace-edit-verify.py", 852, 984,
        "5f65c3b2e3a7515e7dfb5999e1192729a6d17d1599b60a96e994253566a4a78f"),
    "in-order": ("inplace-edit-verify.py", 1037, 1048,
        "fb71cfbff5999188112ca86c8c9a0cff332668d6288ce07eb9a26b53382d931b"),
    "units": ("inplace-edit-verify.py", 1051, 1170,
        "222f68a6aaf367f872d81d859d20a5fccdc184131e12847936402c12329a6f0e"),
    "syntax-oracle": ("inplace-edit-verify.py", 1398, 1447,
        "d9a85678a9af1fa8455e897c414001564b227bc0627f5d29a3bc8f09eb1e01c3"),
    "coproc-end": ("inplace-edit-verify.py", 1450, 1467,
        "72f751cc5f60bfceaa83728e4479e2df707d392a9fcef222d15f6f3b8e1fd1df"),
    "command-target": ("inplace-edit-verify.py", 1478, 1672,
        "a550c251d33197d28da7ffb6d3dab8ad38f904f1dd56fe3f47cfba29ad7cd57a"),
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

# BEGIN VENDOR prefix-options from inplace-edit-verify.py:401-417
_SHELL_PREFIXES = frozenset(("command", "builtin", "exec"))
_ENV_LONG = {"ignore-environment": "flag", "chdir": "arg", "null": "exit", "file": "arg", "unset": "arg",
             "debug": "flag", "split-string": "arg", "argv0": "arg", "ignore-signal": "opt",
             "default-signal": "opt", "block-signal": "opt", "list-signal-handling": "flag", "help": "exit",
             "version": "exit"}
_SUDO_SHORT_ARG = "ugpCDrtUTR"
_SUDO_ARG_LONG = frozenset(("user", "group", "prompt", "close-from", "chdir", "role", "type", "other-user",
                            "command-timeout", "chroot", "host"))
_SUDO_NO_RUN_LONG = frozenset(("help", "version", "remove-timestamp", "validate", "list", "edit"))
_TIME_LONG = {"append": "flag", "format": "arg", "output": "arg", "portability": "flag", "quiet": "flag",
              "verbose": "flag", "help": "exit", "version": "exit"}
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

# BEGIN VENDOR not-keyword from inplace-edit-verify.py:534-537
class _NotKeyword(str):
    __slots__ = ()
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

# BEGIN VENDOR in-order from inplace-edit-verify.py:1037-1048
def _in_order(words, wsubs, rsubs):
    a = 0
    while a < len(words) and _ASSIGN_RE.match(words[a]):
        a += 1
    cmd = a < len(words)
    return ([s for k, subs in wsubs if k >= a for s in subs]
            + [s for k, subs in wsubs if k < a and not (cmd and _INDEXED_RE.match(words[k])) for s in subs]
            + rsubs)
# END VENDOR

# BEGIN VENDOR units from inplace-edit-verify.py:1051-1170
def _units(recs):
    units, pats, stack, start, closed = [], set(), [], None, False
    for r, (words, _, op, _) in enumerate(recs):
        was_empty, touched, j = not stack, False, 0
        if closed:
            if words or op == "(":
                return None
            closed, touched = False, True
        elif stack and stack[-1][0] == "case" and stack[-1][1] == "p":
            top = stack[-1]
            if words == ["esac"] and not isinstance(words[0], _NotKeyword) and not top[2]:
                stack.pop()
            elif op == ")" and words:
                top[1], top[2], op = "b", False, None
                pats.add(r)
            elif op in ("(", "|", "\n") and not (op == "(" and words):
                top[2], op = top[2] or bool(words) or op != "\n", None
                pats.add(r)
            else:
                return None
            touched = True
        else:
            while j < len(words):
                w, top = words[j], stack[-1] if stack else None
                if isinstance(w, _NotKeyword):
                    break
                if w == "!":
                    j += 1
                elif w == "time":
                    j = _time_options(words, j + 1, True)
                elif w == "coproc":
                    j, touched = _coproc_end(words, j, op), True
                elif w in _OPENERS:
                    stack.append([w, None, False])
                    j, touched = j + 1, True
                elif w in ("for", "select"):
                    stack.append([w, None, False])
                    touched = True
                    break
                elif w == "case":
                    touched = True
                    if words[j + 2:j + 3] != ["in"] or isinstance(words[j + 2], _NotKeyword):
                        return None
                    rest = words[j + 3:]
                    if rest == ["esac"] and not isinstance(rest[0], _NotKeyword):
                        break
                    stack.append(["case", "p", bool(rest) or op in ("(", "|")])
                    if op == ")" and rest:
                        stack[-1][1], stack[-1][2] = "b", False
                    elif op not in ("(", "|", "\n") or (op == "(" and rest):
                        return None
                    op = None
                    break
                elif w in _CLOSERS:
                    if not top or top[0] not in _CLOSERS[w]:
                        return None
                    stack.pop()
                    j, touched = j + 1, True
                elif w in ("then", "else", "elif"):
                    if not top or top[0] != "if":
                        return None
                    j, touched = j + 1, True
                elif w == "do":
                    if not top or top[0] not in _LOOPS:
                        return None
                    j, touched = j + 1, True
                else:
                    break
        if op == "(":
            if j < len(words):
                return None
            stack.append(["(", None, False])
            touched = True
        elif op == ")":
            if not stack or stack[-1][0] != "(":
                return None
            stack.pop()
            touched = closed = True
        if op in _ARM_ENDS:
            if not stack or stack[-1][0] != "case" or stack[-1][1] != "b":
                return None
            stack[-1][1], stack[-1][2] = "p", False
        if touched or not was_empty:
            if start is None:
                start = r
            if not stack and not closed:
                units.append((True, start, r))
                start = None
        else:
            units.append((False, r, r))
    if stack or closed or start is not None:
        return None
    return units, pats


def _negated(words):
    neg, j = False, 0
    while j < len(words):
        w = words[j]
        if isinstance(w, _NotKeyword):
            break
        if w == "!":
            neg, j = not neg, j + 1
        elif w == "time":
            j = _time_options(words, j + 1, True)
        else:
            break
    return neg
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

# BEGIN VENDOR coproc-end from inplace-edit-verify.py:1450-1467
def _coproc_end(words, j, op):
    if j >= len(words) or words[j] != "coproc" or isinstance(words[j], _NotKeyword):
        return j
    name = words[j + 1] if j + 1 < len(words) else None
    if (name is None or isinstance(name, _NotKeyword) or not _NAME_RE.match(name) or name in _COPROC_BODY
            or name in _RESERVED):
        return j + 1
    body = words[j + 2] if j + 2 < len(words) else None
    if (body is None and op == "(") or (body in _COPROC_BODY and not isinstance(body, _NotKeyword)):
        return j + 2
    return j + 1
# END VENDOR

# BEGIN VENDOR command-target from inplace-edit-verify.py:1478-1672
def _command_target(words, op=None):
    i, lead = 0, True
    shell = assign = True
    execd = only_builtin = copro = False
    while i < len(words):
        w = words[i]
        if only_builtin and w not in _SHELL_PREFIXES:
            return None
        only_builtin = False
        if "=" in w and _ASSIGN_RE.match(w):
            if not assign:
                return i, execd
            i, lead = i + 1, False
            continue
        w = w[w.rfind("/") + 1:]
        if w == "time":
            lead = lead and words[i] == "time" and not isinstance(words[i], _NotKeyword)
            if not lead:
                shell = assign = False
            i = _time_options(words, i + 1, lead)
            if i is None:
                return None
            continue
        if w == "coproc" and lead and words[i] == w and not isinstance(words[i], _NotKeyword):
            i, copro = _coproc_end(words, i, op), True
            continue
        if w in _RESERVED and (not lead or words[i] != w or isinstance(words[i], _NotKeyword)):
            return i, execd
        if w in _PREFIXES:
            if w in _SHELL_PREFIXES:
                if not shell or words[i] != w:
                    return None
                if w == "exec":
                    shell, execd = False, not copro
                only_builtin = w == "builtin"
                assign = False
            elif w not in _RESERVED:
                shell, assign = False, w != "nohup"
            elif w in _COPROC_BODY:
                copro = False
            lead = lead and words[i] in _RESERVED
            i = _prefix_options(w, words, i + 1)
            if i is None:
                return None
            continue
        return i, execd
    return None


def _prefix_options(w, words, i):
    n = len(words)
    if w in ("command", "builtin", "exec", "nohup"):
        while i < n and words[i].startswith("-") and words[i] != "-":
            a = words[i]
            i += 1
            if a == "--":
                return i
            if a.startswith("--") or w in ("builtin", "nohup"):
                return None
            for j, ch in enumerate(a[1:], 2):
                if w == "command" and ch in "vV":
                    return None
                if w == "exec" and ch == "a":
                    if j == len(a):
                        if i >= n:
                            return None
                        i += 1
                    break
                if ch not in ("p" if w == "command" else "cl"):
                    return None
        return i
    if w == "env":
        while i < n and words[i].startswith("-"):
            a = words[i]
            i += 1
            if a == "--":
                return i
            if a == "-":
                continue
            if a.startswith("--"):
                name, eq, _ = a[2:].partition("=")
                hits = [k for k in _ENV_LONG if k.startswith(name)] if name else []
                kind = _ENV_LONG.get(name) or (_ENV_LONG[hits[0]] if len(hits) == 1 else None)
                if kind is None or kind == "exit" or (eq and kind == "flag"):
                    return None
                if kind == "arg" and not eq:
                    if i >= n:
                        return None
                    i += 1
                continue
            for j, ch in enumerate(a[1:], 2):
                if ch in "CfuaS":
                    if j == len(a):
                        if i >= n:
                            return None
                        i += 1
                    break
                if ch not in "iv":
                    return None
        return i
    if w == "sudo":
        while i < n and words[i].startswith("-") and words[i] != "-":
            a = words[i]
            i += 1
            if a == "--":
                return i
            if a.startswith("--"):
                name, eq, _ = a[2:].partition("=")
                if name in _SUDO_NO_RUN_LONG:
                    return None
                if name in _SUDO_ARG_LONG and not eq:
                    i += 1
                continue
            for j, ch in enumerate(a[1:], 2):
                if ch in "hVKvle":
                    return None
                if ch in _SUDO_SHORT_ARG:
                    if j == len(a):
                        i += 1
                    break
        return i
    return i


def _time_options(words, i, keyword):
    n = len(words)
    if keyword:
        if i < n and words[i] == "-p" and not isinstance(words[i], _NotKeyword):
            i += 1
        return i + 1 if i < n and words[i] == "--" and not isinstance(words[i], _NotKeyword) else i
    while i < n and words[i].startswith("-") and words[i] != "-":
        a = words[i]
        i += 1
        if a == "--":
            return i
        if a.startswith("--"):
            name, eq, _ = a[2:].partition("=")
            hits = [k for k in _TIME_LONG if k.startswith(name)] if name else []
            kind = _TIME_LONG.get(name) or (_TIME_LONG[hits[0]] if len(hits) == 1 else None)
            if kind is None or kind == "exit" or (eq and kind == "flag"):
                return None
            if kind == "arg" and not eq:
                i += 1
            continue
        for j, ch in enumerate(a[1:], 2):
            if ch in "fo":
                if j == len(a):
                    i += 1
                break
            if ch not in "apqv":
                return None
    return i if i <= n else None
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

# This hook's own walk.
_GATE_NAMES = frozenset(("pytest", "py.test", "tox", "nox", "bats", "ctest", "prove", "jest", "vitest", "mocha",
                         "rspec", "phpunit"))
_NOT_GATES = frozenset(("test", "[", "[["))  # the shell conditionals
_PKG_RUNNERS = frozenset(("npm", "pnpm", "yarn", "bun"))
_MAKERS = frozenset(("make", "gmake", "just"))
_MAKE_TARGETS = frozenset(("test", "tests", "check"))
# Each runner's options that take the next word as their value (an attached `--opt=value` is one word), so the
# value is not read as a subcommand or target.
_RUNNER_ARG_OPTS = {
    "npm": frozenset(("--prefix", "-w", "--workspace", "--loglevel", "--registry", "--cache", "--userconfig",
                      "--globalconfig", "--tag", "--scope", "--otp", "--script-shell")),
    "pnpm": frozenset(("-C", "--dir", "--filter", "-F", "--reporter", "--loglevel", "--workspace-concurrency")),
    "yarn": frozenset(("--cwd", "--modules-folder", "--cache-folder", "--mutex", "--network-timeout",
                       "--registry")),
    "bun": frozenset(("--cwd", "-c", "--config", "--preload", "-r", "--env-file")),
    "go": frozenset(("-C",)),
    "cargo": frozenset(("-C", "--color", "--config", "-Z", "--manifest-path")),
    "make": frozenset(("-C", "-f", "-I", "-o", "-W", "--directory", "--file", "--makefile", "--include-dir",
                       "--old-file", "--assume-old", "--new-file", "--assume-new", "--what-if")),
    "just": frozenset(("-f", "--justfile", "-d", "--working-directory", "--shell", "--shell-arg",
                       "--dotenv-filename", "--dotenv-path", "--color", "--list-heading", "--list-prefix",
                       "--chooser", "--dump-format")),
}
_RUNNER_ARG_OPTS["gmake"] = _RUNNER_ARG_OPTS["make"]
_JUST_PAIR_OPTS = frozenset(("--set",))  # just --set NAME VALUE: two value words
# make's -j and -l (--jobs, --load-average) take an optional value: the next word, only when it is a number.
_MAKE_OPTIONAL_NUMBER = frozenset(("-j", "-l", "--jobs", "--load-average"))
_NUMBER_RE = re.compile(r"^[0-9]+(?:\.[0-9]+)?$")
_PYTHON_RE = re.compile(r"^python(?:3(?:\.[0-9]+)?)?$")
_PYTHON_ARG_OPTS = frozenset(("-W", "-X", "-Q"))
_SHELLS = frozenset(("sh", "bash", "dash", "zsh", "ksh"))
_SHELL_ARG_OPTS = frozenset(("-o", "-O", "+o", "+O"))
_SCRIPT_RE = re.compile(r"^(?:run[-_])?tests?(?:[-_.].*)?$|[-_]tests?\.(?:sh|py)$")
_CLAIM_RE = re.compile(r"\b(?:FINAL|DONE|PASS(?:ED)?|COMPLETE(?:D)?|VERIFIED|SUCCESS(?:FUL)?|GREEN)\b")
_CHECK_MARK = chr(9989)
# A name after `$` in a written text: a parameter expansion, whose value is not literal (see _strip_params).
_NAME_START = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz_")
_NAME_CHARS = _NAME_START | frozenset("0123456789")
# A descriptor duplication's target: a descriptor number, and a trailing `-` when the source is moved (closed).
_DUP_RE = re.compile(r"^([0-9]+)(-?)$")
_PRINTF_FLAGS = "-+ #0'"
_PRINTF_NUMERIC = "diouxXeEfFgGaA"
_PRINTF_BUDGET = 2 * _SCAN_LIMIT  # printf rendering work per command (past it: the texts read unrendered)
_PRINTF_METER = [_PRINTF_BUDGET]  # what is left of it for the command being judged (reset by _verdict)
_DIGITS = "0123456789"
_HEX_DIGITS = _DIGITS + "abcdefABCDEF"
_STOP = ("stop",)  # a parsed format's end at an incomplete specification: all output stops there
_BIG = 10 ** 9  # a width or precision written with more than 9 digits (int() is never run on a long digit run)
# Raw-text prefilters, read with quote characters and backslashes removed (so `"PA"SS` reads PASS): every gate
# name holds one of these words (a superset); the status examined.
_GATE_HINT_RE = re.compile(r"test|check|tox|nox|bats|prove|jest|mocha|rspec|phpunit")
_DEQUOTE = str.maketrans("", "", "'\"\\")
_STATUS_RE = re.compile(r"\$\{?\?|PIPESTATUS")
# The opt-out: a comment starting `# record-ok` (then a colon, a blank, or nothing) that ends the command. The
# lookbehind keeps a mid-word `#` out; _opted_out confirms the lexer reads the tail as a comment.
_OPT_OUT_RE = re.compile(r"(?<![^ \t\n;&|()])#[ \t]*record-ok(?:[ \t:][^\n]*)?\Z")
_STDOUT_OPS = frozenset((">", ">>", ">|"))
_BOTH_OPS = frozenset(("&>", "&>>"))
_STDIN_OPS = frozenset(("<", "<<", "<<-", "<<<"))
_TIMEOUT_ARG_OPTS = frozenset(("-s", "-k", "--signal", "--kill-after"))
# The builtins a `builtin` prefix may run that this walk reads (the vendored finder admits only its own three).
_BUILTIN_TARGETS = frozenset(("echo", "printf", "set", "true", "false", ":", "exit"))
_MAX_WRAPS = 8  # nested timeout prefixes followed to the command word (past it: no command word)
_SHOW_MAX = 200  # characters of a gate, word, or file quoted in the reason

_REASON = (
    "Blocked (ungated-record): this command runs a test gate ({gate}) and also writes a completion claim ({word} "
    "to {file}) on a path where the gate has failed (joined by ';', a newline, '||', or a pipe that hides the "
    "gate's status). The record would claim a result nothing checked. Make the record depend on the gate: "
    "{gate} && echo '{word}' >> {file}; or record the real status: {gate}; rc=$?; echo \"rc=$rc\" >> {file}. "
    "For a record that is deliberately not a claim, end the command with '# record-ok: <reason>'.")


def _is_worker(env):
    """True for a verification worker process: AIQT_HOOKS_WORKER == "1" first, then the legacy names,
    ORCH_WORKER == "1" or ORCH_VERIFY_OWNER present (any value, even empty)."""
    if env.get("AIQT_HOOKS_WORKER") == "1":
        return True
    return env.get("ORCH_WORKER") == "1" or "ORCH_VERIFY_OWNER" in env


def _base(word):
    return word[word.rfind("/") + 1:]


def _real(mark):
    """True for a failed gate's label (not None, the cleared state, nor _SENT, a stage's stand-in)."""
    return mark is not None and mark is not _SENT


def _canon(states):
    """The path states with one label kept per (status, kind of mark): a set of at most six states, so the
    walk's work stays linear. Any failed gate's label serves the reason; the smallest is kept."""
    best = {}
    for s, p in states:
        key = (s, 2 if _real(p) else 1 if p is _SENT else 0)
        if key not in best or (key[1] == 2 and p < best[key]):
            best[key] = p
    return {(k[0], p) for k, p in best.items()}


def _show(value):
    """A value quoted in the reason: substitutions shown as $(...), long values cut."""
    value = value.replace(_SUB, "$(...)")
    return value if len(value) <= _SHOW_MAX else value[:_SHOW_MAX] + "..."


def _option_end(args, k, longs, shorts, optional=""):
    """Index past the option word args[k] and its value. A long option (`--name`) in longs takes the next word,
    unless its value is attached with `=`. In a short cluster (`-sC`), the first letter in shorts takes the rest
    of the cluster as its value or, when it ends the cluster, the next word; a letter in optional takes a value
    the same way, but the next word only when that word is a number."""
    a = args[k]
    if a.startswith("--"):
        return k + 2 if a in longs else k + 1
    for j in range(1, len(a)):
        if a[j] in shorts or a[j] in optional:
            if j + 1 < len(a):
                return k + 1
            if a[j] in shorts or (k + 1 < len(args) and _NUMBER_RE.match(args[k + 1])):
                return k + 2
            return k + 1
    return k + 1


def _positional(args, name):
    """The arguments of runner name that are not options: each option skipped with its value (_option_end, with
    the runner's _RUNNER_ARG_OPTS, their short letters in clusters, and make's numeric -j and -l), and a cargo
    `+toolchain` selector; after `--`, every word."""
    opts = _RUNNER_ARG_OPTS.get(name, frozenset())
    shorts = "".join(o[1] for o in opts if len(o) == 2 and o[0] == "-" and o[1] != "-")
    maker = name in ("make", "gmake")
    out, k, n = [], 0, len(args)
    while k < n:
        a = args[k]
        if a == "--":
            out.extend(args[k + 1:])
            break
        if name == "just" and a in _JUST_PAIR_OPTS:
            k += 3
        elif name == "cargo" and a.startswith("+"):
            k += 1
        elif maker and a in _MAKE_OPTIONAL_NUMBER and a.startswith("--"):
            k += 2 if k + 1 < n and _NUMBER_RE.match(args[k + 1]) else 1
        elif a.startswith("-") and a != "-":
            k = _option_end(args, k, opts, shorts, "jl" if maker else "")
        else:
            out.append(a)
            k += 1
    return out


def _script_arg(args, shorts, stops):
    """(stop letter, value) or ("", script) for an interpreter's arguments: the first short-option letter in stops
    (python's -m and -c, a shell's -c) with its value (the rest of the cluster, or the next word), else the first
    non-option argument; each letter in shorts takes a value the same way and is skipped with it. None when
    neither is found."""
    k, n = 0, len(args)
    while k < n:
        a = args[k]
        if a == "--":
            return ("", args[k + 1]) if k + 1 < n else None
        if a.startswith("--"):
            k += 1
            continue
        if a[:1] in ("-", "+") and a not in ("-", "+"):
            for j in range(1, len(a)):
                if a[j] in stops:
                    value = a[j + 1:] or (args[k + 1] if k + 1 < n else None)
                    return (a[j], value) if value is not None else None
                if a[j] in shorts:
                    k += 1 if j + 1 < len(a) else 2
                    break
            else:
                k += 1
            continue
        return "", a
    return None


def _gate(words, t):
    """A label for the gate the simple command whose command word is words[t] runs, or None."""
    name, args = _base(words[t]), [str(a) for a in words[t + 1:]]
    label = _show(" ".join(str(w) for w in words[t:]))
    if name in _NOT_GATES:
        return None
    if "--self-test" in args or name in _GATE_NAMES or _SCRIPT_RE.search(name):
        return label
    if _PYTHON_RE.match(name) or name in _SHELLS:
        python = name not in _SHELLS
        found = _script_arg(args, "WXQ" if python else "oO", "mc" if python else "c")
        if found is None:
            return None
        if found[0] == "m":
            return label if found[1] in ("pytest", "unittest") else None
        return label if not found[0] and _SCRIPT_RE.search(_base(found[1])) else None
    pos = _positional(args, name)
    if name in _PKG_RUNNERS:
        if pos[:1] in (["test"], ["t"]) or (pos[:1] in (["run"], ["run-script"]) and pos[1:2] in (["test"],
                                                                                                 ["check"])):
            return label
        return None
    if name in ("go", "cargo"):
        return label if pos[:1] == ["test"] else None
    if name in _MAKERS:
        return label if any(a in _MAKE_TARGETS for a in pos if "=" not in a) else None
    return None


def _timeout_command(words, k):
    """Index of the command timeout runs, its options starting at words[k] (-s and -k take a value, also in a
    cluster), or None when it runs none."""
    n = len(words)
    while k < n and words[k].startswith("-") and words[k] != "-":
        if words[k] == "--":
            k += 1
            break
        k = _option_end(words, k, _TIMEOUT_ARG_OPTS, "sk")
    k += 1  # the duration
    return k if k < n else None


def _builtin_target(words, op):
    """(index, execd) of the builtin a `builtin` prefix runs when it is one of _BUILTIN_TARGETS, or None: the
    prefix (a regular builtin, so quoting it or a redirection before it changes nothing) and its `--` are read
    as `command`, which runs the same builtin, and the name after them must be the command word."""
    for i, w in enumerate(words):
        if w == "builtin":
            j = i + 2 if words[i + 1:i + 2] == ["--"] else i + 1
            if j >= len(words) or words[j] not in _BUILTIN_TARGETS:
                return None
            found = _command_target(words[:i] + ["command"] + words[i + 1:], op)
            return found if found is not None and found[0] == j else None
    return None


def _target(words, op):
    """(index of the command word, execd) as _command_target finds it, stepping past timeout prefixes and reading
    a `builtin` prefix before one of _BUILTIN_TARGETS, or None."""
    found, base = _command_target(words, op), 0
    if found is None:
        found = _builtin_target(words, op)
    for _ in range(_MAX_WRAPS):
        if found is None:
            return None
        t = base + found[0]
        if _base(words[t]) != "timeout":
            return t, found[1] if not base else outer
        if not base:
            outer = found[1]
        k = _timeout_command(words, t + 1)
        if k is None:
            return None
        found, base = _command_target(words[k:], None), k
    return None


def _errexit(rec):
    """True when the record is a `set` command that turns on errexit or pipefail: an option cluster holding e, or
    an o (each o in a cluster takes the next word as an option name) naming errexit or pipefail."""
    words = rec[0]
    found = _target(words, rec[2]) if words else None
    if found is None or words[found[0]] != "set":
        return False
    args, k = words[found[0] + 1:], 0
    while k < len(args):
        a = args[k]
        k += 1
        if a in ("--", "-", "+") or a[:1] not in ("-", "+"):
            return False  # the positional parameters follow
        on = a[0] == "-"
        if on and "e" in a[1:]:
            return True
        for _ in range(a.count("o")):
            if k < len(args):
                if on and args[k] in ("errexit", "pipefail"):
                    return True
                k += 1
    return False


def _descriptors(redirs):
    """{descriptor: what it holds} once the redirections are made, left to right as bash makes them: ("file",
    word) for a file opened for writing, ("text", text) for a here-document or here-string, ("other",) for
    anything else (a read-only file, a closed descriptor, or a duplicate of one not redirected here). A
    duplication copies its source's entry, and a move (`1>&3-`) closes the source too. A descriptor that is not
    redirected is absent."""
    fds = {}
    for fd, op, target, body in redirs:
        if op in _BOTH_OPS or (op == ">&" and fd is None and target and not _DUP_RE.match(target) and target != "-"):
            fds["1"] = fds["2"] = ("file", target) if target else ("other",)
        elif op in _STDOUT_OPS or op == "<>":
            fds[fd or ("1" if op in _STDOUT_OPS else "0")] = ("file", target) if target else ("other",)
        elif op in _HEREDOC_OPS or op == "<<<":
            text = target if op == "<<<" else body
            fds[fd or "0"] = ("other",) if text is None else ("text", text)
        elif op == "<":
            fds[fd or "0"] = ("other",)
        elif op in (">&", "<&"):
            m = _DUP_RE.match(target or "")
            fds[fd or ("1" if op == ">&" else "0")] = fds.get(m.group(1), ("other",)) if m else ("other",)
            if m and m.group(2):
                fds[m.group(1)] = ("other",)
    return fds


def _stdout_file(redirs):
    """Where standard output goes once the redirections are made (_descriptors): None when it is not
    redirected, "" when it goes anywhere but a file opened for writing, else the file word."""
    entry = _descriptors(redirs).get("1")
    if entry is None:
        return None
    return entry[1] if entry[0] == "file" else ""


def _file(value):
    """value when it names a literal file outside /dev (no parameter or substitution), else None."""
    if not value or "$" in value or value.startswith("/dev/"):
        return None
    return value


def _stdin(redirs):
    """(redirected, texts): whether standard input is redirected, and the text it then reads when it holds a
    here-document or here-string once the redirections are made (_descriptors, so `3<<<x 0<&3` reads x)."""
    entry = _descriptors(redirs).get("0")
    if entry is None:
        return False, []
    return True, [entry[1]] if entry[0] == "text" else []


def _blank_escapes(text, stop):
    """(text, stopped): text with each backslash escape (the backslash and the character after it) written as one
    blank, a word boundary as the escaped newline or tab is; with stop (as %b reads its argument), a `\\c` ends
    all output there, and stopped is True. Linear."""
    out, i = [], 0
    while True:
        j = text.find("\\", i)
        if j < 0:
            out.append(text[i:])
            return "".join(out), False
        out.append(text[i:j])
        if stop and text[j + 1:j + 2] == "c":
            return "".join(out), True
        out.append(" ")
        i = j + 2


def _int_arg(value):
    """The integer printf reads from an argument for a `*` width or precision, as bash reads the common forms:
    a leading quote gives the code of the character after it (`'A` is 65, a lone quote 0); otherwise leading
    whitespace, an optional sign, then `0x` or `0X` and hexadecimal digits, a leading `0` and octal digits, or
    decimal digits, the value being the longest such prefix (`8x` is 8, `08` and `0x` are 0) and 0 when there
    is none (`n`: bash reports the argument and reads 0). More than 9 digits read as _BIG. Linear."""
    if value[:1] in ("'", '"'):
        return ord(value[1]) if len(value) > 1 else 0
    text = value.lstrip(" \t\n\v\f\r")
    sign = -1 if text[:1] == "-" else 1
    text = text[1:] if text[:1] in ("-", "+") else text
    if text[:2] in ("0x", "0X") and text[2:3] and text[2] in _HEX_DIGITS:
        base, digits, text = 16, _HEX_DIGITS, text[2:]
    elif text[:1] == "0":
        base, digits = 8, _DIGITS[:8]
    else:
        base, digits = 10, _DIGITS
    run = 0
    while run < len(text) and text[run] in digits:
        run += 1
    return sign * (_BIG if run > 9 else int(text[:run] or "0", base))


def _spec_number(fmt, i):
    """(value, index past it) of a width or precision starting at fmt[i]: "*" (from an argument), or its decimal
    digits as an int (0 when there are none; more than 9 digits read as _BIG, never passed to int())."""
    if fmt[i:i + 1] == "*":
        return "*", i + 1
    j, n = i, len(fmt)
    while i < n and fmt[i] in _DIGITS:
        i += 1
    return (_BIG if i - j > 9 else int(fmt[j:i] or "0")), i


def _printf_parse(fmt):
    """The format as a list of items, read once, left to right, every character charged to _PRINTF_METER: ("",
    text) for literal text (escapes blanked, `%%` a percent sign), or (conv, left, width, prec) for a conversion,
    left the `-` flag, width an int or "*" (from an argument), prec None (none given), an int, or "*". An
    incomplete specification ends the list with _STOP: bash writes the text before it and then stops all
    output, with no replay. None past the meter."""
    _PRINTF_METER[0] -= len(fmt)
    if _PRINTF_METER[0] < 0:
        return None
    items, lit, i, n = [], [], 0, len(fmt)
    while i < n:
        j = fmt.find("%", i)
        if j < 0:
            lit.append(fmt[i:])
            break
        lit.append(fmt[i:j])
        i = j + 1
        if fmt[i:i + 1] == "%":
            lit.append("%")
            i += 1
            continue
        left = False
        while i < n and fmt[i] in _PRINTF_FLAGS:
            left, i = left or fmt[i] == "-", i + 1
        width, i = _spec_number(fmt, i)
        prec = None
        if fmt[i:i + 1] == ".":
            prec, i = _spec_number(fmt, i + 1)
        if i >= n:  # an incomplete specification: bash writes the text before it, then stops all output
            if lit:
                items.append(("", _blank_escapes("".join(lit), False)[0]))
            items.append(_STOP)
            return items
        if lit:
            items.append(("", _blank_escapes("".join(lit), False)[0]))
            lit = []
        items.append((fmt[i], left, width, prec))
        i += 1
    if lit:
        items.append(("", _blank_escapes("".join(lit), False)[0]))
    return items


def _printf_text(fmt, args):
    """The text printf writes for a literal format and arguments, as far as a claim word needs it, or None when
    the work would pass _PRINTF_METER (shared by every printf of one command, so the total stays linear). The
    format is parsed once (_printf_parse) and its items replayed while arguments remain, as printf reuses its
    format; each replayed item and each character written is charged. A string conversion (%s, %b, %q) writes
    its argument cut to the precision, %b with its escapes blanked and a `\\c` ending all output, %c its first
    character, a numeric conversion a digit. A `*` width or precision reads an argument as bash reads the common
    forms of a number (_int_arg): a negative width left-justifies at its magnitude, and a negative precision is
    none. A width wider than the text writes one blank on the padded side."""
    items = _printf_parse(fmt)
    if items is None:
        return None
    out, k = [], 0
    while True:
        start = k
        for item in items:
            _PRINTF_METER[0] -= 1 + (len(item[1]) if item is not _STOP and not item[0] else 0)
            if _PRINTF_METER[0] < 0:
                return None
            if item is _STOP:
                return "".join(out)
            if not item[0]:
                out.append(item[1])
                continue
            conv, left, width, prec = item
            if width == "*":
                width, k = _int_arg(args[k]) if k < len(args) else 0, k + 1
                if width < 0:
                    left, width = True, -width
            if prec == "*":
                prec, k = _int_arg(args[k]) if k < len(args) else 0, k + 1
                prec = None if prec < 0 else prec
            stopped = False
            if conv in "sbqc" or conv in _PRINTF_NUMERIC:
                arg, k = args[k] if k < len(args) else "", k + 1
                _PRINTF_METER[0] -= len(arg)
                if _PRINTF_METER[0] < 0:
                    return None
                if conv == "b":
                    arg, stopped = _blank_escapes(arg, True)
                text = "0" if conv in _PRINTF_NUMERIC else arg[:1] if conv == "c" else (
                    arg if prec is None else arg[:prec])
            else:
                text = "%" + conv
            pad = [" "] if width > len(text) else []
            out.extend([text] + pad if left else pad + [text])
            if stopped:
                return "".join(out)
        if k == start or k >= len(args):
            return "".join(out)


def _written(words, t, redirs):
    """The literal texts an echo, printf, or cat (reading a here-document or here-string) command writes, or
    None. printf's text is its rendered output (_printf_text), or, past the rendering budget, its format and
    arguments as written."""
    name, args = _base(words[t]), words[t + 1:]
    if name == "echo":
        return list(args)
    if name == "printf":
        if args[:1] and args[0].startswith("-v"):
            return None
        if args[:1] == ["--"]:
            args = args[1:]
        if not args:
            return None
        text = _printf_text(args[0], [str(a) for a in args[1:]])
        return list(args) if text is None else [text]
    if name == "cat" and all(a in ("-", "--") or a.startswith("-") for a in args):
        return _stdin(redirs)[1] or None
    return None


def _strip_params(text):
    """text with each parameter expansion replaced by a blank: `$NAME`, and `${...}` to its matching brace, read as
    bash reads it: a nested `${` opens a level (`${A:-${B}: PASS}` is one expansion), a plain `{` is text (bash
    ends `${A:-{} PASS}` at the first `}`), and a brace inside a single-quoted span does not close it
    (`"${A:-'}' PASS}"` is one expansion; a quote with no closing quote is text, and bash rejects that command
    anyway). A backslash escapes the character after it (so `\\'` opens no span), except a closing brace: once
    the lexer has read the word, an escaped brace (`\\}`) and an escaped backslash before a brace (`\\\\` then
    `}`) look the same, so that brace closes the expansion (a false deny when it was escaped). The lexer's
    reading hides two other escapes, which this function then misreads the other way (see RESIDUAL COVERAGE):
    an escaped `\\${` counts as a nested `${`, and a quote the lexer has unquoted opens a span. An unclosed
    expansion runs to the end. Linear: once a quote search finds no closing quote, no later one searches
    again."""
    out, i, n, quotes = [], 0, len(text), True  # quotes: a closing quote may still lie ahead
    while i < n:
        j = text.find("$", i)
        if j < 0:
            out.append(text[i:])
            break
        out.append(text[i:j])
        if text.startswith("{", j + 1):
            depth, k = 1, j + 2
            while k < n:
                c = text[k]
                if c == "\\":
                    if text[k + 1:k + 2] != "}":
                        k += 1  # an escaped character, never a quote or `${`; an escaped `}` still closes
                elif c == "'" and quotes:
                    q = text.find("'", k + 1)
                    quotes = q >= 0
                    k = q if quotes else k
                elif text.startswith("${", k):
                    depth += 1
                    k += 1
                elif c == "}":
                    depth -= 1
                    if not depth:
                        break
                k += 1
            out.append(" ")
            i = k + 1
            continue
        k = j + 1
        if k < n and text[k] in _NAME_START:
            while k < n and text[k] in _NAME_CHARS:
                k += 1
            out.append(" ")
        else:
            out.append("$")
        i = k
    return "".join(out)


def _claim_word(texts):
    """The first claim word (or the check mark) in the texts, parameter expansions set aside (_strip_params)."""
    for text in texts:
        text = _strip_params(text)
        m = _CLAIM_RE.search(text)
        if m:
            return m.group()
        if _CHECK_MARK in text:
            return _CHECK_MARK
    return None


def _claim(words, t, redirs, feed):
    """(word, file) when the simple command at words[t] is a claim record, else None. feed holds the texts the
    previous stage of its pipeline writes to it (see _feed), or None; a redirection of tee's standard input
    replaces it."""
    if _base(words[t]) == "tee":
        redirected, texts = _stdin(redirs)
        if not redirected:
            texts = feed or []
        files, ended = [], False
        for a in words[t + 1:]:
            if ended or not a.startswith("-") or a == "-":
                files.append(a)
            elif a == "--":
                ended = True
        files.append(_stdout_file(redirs))  # tee copies its input to standard output too
        dest = next((f for f in files if _file(f)), None)
    else:
        texts, dest = _written(words, t, redirs), _file(_stdout_file(redirs))
    word = _claim_word(texts) if texts and dest else None
    return (word, dest) if word else None


def _feed(rec):
    """The literal texts a pipeline stage writes to the next stage (an echo, printf, or cat whose standard
    output is not redirected), or None."""
    words, _, op, redirs = rec
    found = _target(words, op) if words else None
    if found is None or _stdout_file(redirs) is not None:
        return None
    return _written(words, found[0], redirs)


def _claim_meets(st, pendings, claim):
    """A claim record runs where these marks may be pending: record the deny (a failed gate), or pass the claim
    out to the enclosing pipeline stage (_SENT stands for the marks pending before its pipeline)."""
    real = sorted(p for p in pendings if _real(p))
    if real and st["deny"] is None:
        st["deny"] = (real[0],) + claim
    if _SENT in pendings and st["sent"] is None:
        st["sent"] = claim


def _records(toks, st, bodies=None):
    """ADAPTED from the vendored reference's _records: the simple-command records of one token stream, each
    (words, subs, op, redirs), where redirs lists each redirection as (fd, op, target, body) in order instead of
    dropping it. fd is the descriptor word written against the operator (None when there is none), target the
    next word's value (None when there is none), and body a here-document's text, looked up in bodies by the
    operator's position (top-level stream only; None elsewhere). redirs is truthy exactly when the reference's
    redir flag is, so the vendored _units reads these records unchanged."""
    toks = _drop_declarations(toks, st)
    recs, words, wsubs, rsubs, i, n, cont, redirs, fd = [], [], [], [], 0, len(toks), False, [], None
    while True:
        if i < n:
            kind, value, quoted, start, end, tsubs = toks[i]
            if kind == "nl" and cont:
                i += 1  # after |, ||, &&, or |& a newline (or a comment's line end) continues the command
                continue
            cont = False
            if kind == "w":
                nxt = toks[i + 1] if i + 1 < n else None
                # An fd number (2>/dev/null) or fd variable ({fd}>out) attached to a redirect belongs to it.
                if (nxt and nxt[0] == "op" and nxt[3] == end and nxt[1] in _REDIRECTS and not quoted
                        and (value.isdigit() or _FD_VAR_RE.match(value))):
                    fd = value
                    i += 1
                    continue
                if tsubs:
                    wsubs.append((len(words), tsubs))
                # Past a redirection bash reads the words as a simple command's: no keyword, no time option.
                words.append(_NotKeyword(value) if quoted or redirs else value)
                i += 1
                continue
            if kind == "op" and value in _REDIRECTS:
                target = None
                if i + 1 < n and toks[i + 1][0] == "w":  # the operator and its target
                    rsubs.extend(toks[i + 1][5])
                    target = toks[i + 1][1]
                    i += 2
                else:
                    i += 1
                body = bodies.get(start) if bodies is not None and value in _HEREDOC_OPS else None
                redirs.append((fd, value, target, body))
                fd = None
                continue
        recs.append((words, _in_order(words, wsubs, rsubs) if wsubs or rsubs else (),
                     toks[i][1] if i < n else None, redirs))
        if i >= n:
            break
        cont = toks[i][1] in _CONTINUE_OPS
        words, wsubs, rsubs, redirs, fd = [], [], [], [], None
        i += 1
    return recs


def _heredoc_bodies(text, toks):
    """{position of a top-level `<<` or `<<-` operator: its here-document's text}. The bodies of a line's
    here-documents follow that line's newline in order, each running to its delimiter line (leading tabs
    stripped for `<<-`), as the vendored lexer skips them; an unterminated body runs to the end of the text."""
    bodies, pending, n = {}, [], len(text)
    for k, t in enumerate(toks):
        if t[0] == "op" and t[1] in _HEREDOC_OPS and k + 1 < len(toks) and toks[k + 1][0] == "w":
            pending.append((t[3], toks[k + 1][1], _HEREDOC_OPS[t[1]]))
        elif t[0] == "nl" and pending:
            i = t[4]
            for pos, delim, strip in pending:
                lines = []
                while i < n:
                    j = text.find("\n", i)
                    line = text[i:] if j < 0 else text[i:j]
                    i = n if j < 0 else j + 1
                    if (line.lstrip("\t") if strip else line) == delim:
                        break
                    lines.append(line)
                bodies[pos] = "\n".join(lines)
            pending = []
    return bodies


def _walk(toks, states, st, bodies=None):
    """ADAPTED from the vendored reference's _walk (its deny mode only): the path states (live, ended) after one
    token stream runs from states. A state is (status, mark): the last exit status, 0 or 1, and the label of a
    gate that failed on that path (None when none did; _SENT, inside a pipeline stage, for whatever was pending
    before the pipeline). A gate forks every path into its success (the mark kept) and its failure (marked);
    every other command takes its ordinary status (true and `:` 0, false 1, the rest 0). A compound command
    standing alone as a pipeline is a barrier on every path, run or skipped, that clears the marks (a compound
    stage of a longer pipeline is opaque instead, see _pipeline); an asynchronous list is not followed past;
    a stream whose structure cannot be followed, or that turns on errexit or pipefail, sets st["lost"]."""
    recs = _records(toks, st, bodies)
    if any(_errexit(rec) for rec in recs):
        st["lost"] = True
    shape = None if st["lost"] else _units(recs)
    if shape is None:
        st["lost"] = True
        return states, set()
    units = shape[0]
    live, ended, k, n = states, set(), 0, len(units)
    while k < n:
        e = k  # one and-or list: units k..e, joined by &&, ||, and pipes
        while e + 1 < n and recs[units[e][2]][2] in _JOIN:
            e += 1
        cur, gone, prev, p = live, set(), None, k
        while p <= e:
            q = p  # one pipeline: units p..q
            while q < e and recs[units[q][2]][2] in _PIPES:
                q += 1
            if prev is None:
                run, skip = cur, set()
            else:  # bash runs it after && only on status 0, after || only on a non-zero status
                run, skip = set(), set()
                for x in cur:
                    (run if (x[0] == 0) == (prev == "&&") else skip).add(x)
            if q == p and units[p][0]:
                out, end, skip = {(0, None)} if run else set(), set(), {(x[0], None) for x in skip}
            else:
                out, end = _pipeline(recs, units[p:q + 1], run, st) if run else (set(), set())
            cur, gone, prev, p = _canon(skip | out), gone | end, recs[units[q][2]][2], q + 1
        if recs[units[e][2]][2] == "&":
            # An asynchronous list runs concurrently with what follows: the shell goes on from the state before
            # it, with status 0. A claim in it still meets the marks pending before it (checked as walked).
            live = {(0, x[1]) for x in live}
        else:
            live, ended = cur, ended | gone
        k = e + 1
    return live, ended


def _merge(a, b):
    """The mark after two concurrent pipeline stages whose own marks are a and b (as the vendored reference
    merges its pending edits): a gate failed in either stays, else a cleared one clears, else _SENT stands."""
    if _real(b):
        return b
    if _real(a):
        return a
    return None if a is None or b is None else _SENT


def _pipeline(recs, units, states, st):
    """ADAPTED from the vendored reference's _pipeline: (live, ended) after one pipeline runs from states. The
    stages run concurrently: each is walked once from _SENT, a claim in any stage meets the marks pending
    before the pipeline, and a gate failed in any stage stays marked. The status is the last stage's (so an
    earlier stage's failure is hidden), inverted by a leading `!`. A compound stage runs in a subshell of its
    own, so it can neither end the path nor clear a mark: it is opaque, its contents unread and its status taken
    as 0, while the other stages are walked."""
    if len(units) == 1:
        out, gone = _simple(recs[units[0][1]], states, st, True, None)
    else:
        outs, feed = [], None
        for u in units:
            if u[0]:  # a compound stage: opaque (its status 0, no mark of its own, nothing fed on)
                outs.append({(0, _SENT)})
                feed = None
                continue
            saved, st["sent"] = st["sent"], None
            outs.append(_simple(recs[u[1]], {(0, _SENT)}, st, False, feed)[0])
            hit, st["sent"] = st["sent"], saved
            if hit is not None:
                _claim_meets(st, {p for _, p in states}, hit)
            feed = _feed(recs[u[1]])
        acc = {_SENT}
        for o in outs[:-1]:  # the marks kept canonical (at most three), so a long pipeline stays linear
            acc = {m for _, m in _canon({(0, _merge(a, b)) for a in acc for _, b in o})}
        out, gone = set(), set()
        for _, p in states:
            for a in acc:
                for s, b in outs[-1]:
                    m = _merge(a, b)
                    out.add((s, p if m is _SENT or (m is not None and _real(p)) else m))
    if _negated(recs[units[0][1]][0]):
        out = {(s ^ 1, p) for s, p in out}
    return _canon(out), gone


def _simple(rec, states, st, alone, feed):
    """(live, ended) after one simple command runs from states: its substitution streams in order, each a shell
    of its own whose paths all come back, then the command. A claim record meets the marks on the states it
    runs from; a gate forks each state into (0, its mark) and (1, its mark or the gate's label). alone: the
    command is its shell's own (not a pipeline stage), so an exec'd command or an exit ends the path."""
    words, subs, op, redirs = rec
    if not words and not subs and not redirs:
        return set(states), set()
    for sub in subs:
        live, gone = _walk(sub, {(0, p) for _, p in states}, st)
        states = live | gone
    found = _target(words, op) if words else None
    if found is None:
        if subs and all(_ASSIGN_RE.match(w) for w in words):
            return set(states), set()  # assignments or redirections: the last substitution's status
        return {(0, p) for _, p in states}, set()
    t, execd = found
    claim = _claim(words, t, redirs, feed)
    if claim:
        _claim_meets(st, {p for _, p in states}, claim)
    gate = _gate(words, t)
    if gate:
        out = {(0, p) for _, p in states} | {(1, p if _real(p) else gate) for _, p in states}
    else:
        out = {(1 if _base(words[t]) == "false" else 0, p) for _, p in states}
    out = _canon(out)
    if alone and (execd or words[t] == "exit"):
        return set(), out
    return out, set()


def _state():
    return {"func": False, "lost": False, "deny": None, "sent": None}


def _opted_out(text):
    """True when the command ends with the `# record-ok` comment: the marker regex matches on the last line, and
    the lexer reads the text from the marker on as a comment (the tokens with and without it are the same)."""
    s = text.rstrip(" \t\n")
    m = _OPT_OUT_RE.search(s, s.rfind("\n") + 1)
    if not m:
        return False
    try:
        return _tokenize(s[:m.start()]) == _tokenize(s)
    except ValueError:
        return False


def _verdict(cmd, oracle=None):
    """None to allow, or (gate label, claim word, file) for the deny. A command over _SCAN_LIMIT bytes of UTF-8
    is not judged at all (a prefix could drop its end, the opt-out included)."""
    if len(cmd) > _SCAN_LIMIT or len(cmd.encode("utf-8", "surrogatepass")) > _SCAN_LIMIT:
        return None
    text = _bounded(cmd)
    probes = (text, text.translate(_DEQUOTE))  # the dequoted form joins an escape's letter: read both
    # printf can assemble a claim from pieces (`printf '%s%s' PA SS`), so a command naming it skips the claim test.
    if (not any(_GATE_HINT_RE.search(t) for t in probes) or _CHECK_MARK not in text and "printf" not in probes[1]
            and not any(_CLAIM_RE.search(t) for t in probes)):
        return None
    if _STATUS_RE.search(text) or _opted_out(text):
        return None
    try:
        toks = _tokenize(text)
        st = _state()
        _PRINTF_METER[0] = _PRINTF_BUDGET
        _walk(toks, {(0, None)}, st, _heredoc_bodies(text, toks))
    except ValueError:
        return None  # unparseable, or past the work bound: fail open
    if st["func"] or st["lost"] or st["deny"] is None:
        return None
    if (oracle or _bash_syntax_ok)(text) is False:
        return None  # bash rejects the command: it runs nothing, fail open
    return st["deny"]


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
    found = _verdict(cmd, oracle)
    if found is None:
        return None
    gate, word, dest = (_show(v) for v in found)
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                   "permissionDecisionReason": _REASON.format(gate=gate, word=word, file=dest)}}


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
    """The hook: always 0, output only a deny line. `--self-test` alone runs the self-test instead."""
    if not isinstance(argv, (list, tuple)) or not argv or not all(isinstance(a, str) for a in argv):
        return 0  # a bad argv: fail open, reading nothing
    if len(argv) > 1:  # only the plain call and an exact --self-test; any other argv fails open, reading nothing
        return _self_test() if list(argv[1:]) == ["--self-test"] else 0
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
    import unittest

    here = os.path.abspath(__file__)
    no_oracle = lambda command: None  # noqa: E731 (the oracle unavailable: the lexical verdict stands)
    reject = lambda command: False  # noqa: E731 (bash rejects the command)

    def decide(command, oracle=no_oracle, env=None, **extra):
        payload = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": command}}
        payload.update(extra)
        return _decide(payload, {} if env is None else env, oracle)

    def run_hook(data, env=None, timeout=30):
        """The hook run as a process: (status, stdout, stderr); env is the whole environment."""
        p = subprocess.run([sys.executable, "-I", "-S", "-B", here], input=data, capture_output=True,
                           env={"LC_ALL": "C"} if env is None else env, timeout=timeout)
        return p.returncode, p.stdout, p.stderr

    def work(command):
        """The Python line events one verdict of command executes (the vendored lexer's included), counted with a
        trace function: a deterministic operation count, so a scaling check reads the same on any host and under
        any load. The command must reach a verdict (a deny), so the whole walk is counted."""
        count, old = [0], sys.gettrace()

        def local(frame, event, arg):
            if event == "line":
                count[0] += 1
            return local
        sys.settrace(lambda frame, event, arg: local)
        try:
            found = _verdict(command, no_oracle)
        finally:
            sys.settrace(old)
        if found is None:
            raise AssertionError("no verdict, so the walk was not counted whole: " + command[:60])
        return count[0]

    def payload_bytes(command):
        return json.dumps({"hook_event_name": "PreToolUse", "tool_name": "Bash",
                           "tool_input": {"command": command}}).encode()

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

    base = "pytest; echo PASS >> r"

    class T(unittest.TestCase):
        def assertDeny(self, command, **kw):
            out = decide(command, **kw)
            self.assertIsNotNone(out, command)
            h = out["hookSpecificOutput"]
            self.assertEqual((h["hookEventName"], h["permissionDecision"]), ("PreToolUse", "deny"))
            self.assertIn("Blocked (ungated-record)", h["permissionDecisionReason"])
            return h["permissionDecisionReason"]

        def assertAllow(self, command, **kw):
            self.assertIsNone(decide(command, **kw), command)

        # cases 1 to 8 and 17: the flip cases (they pass only with detection present)
        def test_01_flip_semicolon(self):
            reason = self.assertDeny("pytest; echo FINAL >> r")
            self.assertIn("(pytest)", reason)
            self.assertIn("(FINAL to r)", reason)

        def test_02_flip_newline(self):
            self.assertDeny("pytest\necho PASS >> r")

        def test_03_flip_or_true(self):
            self.assertDeny("pytest || true; echo DONE >> r")

        def test_04_flip_or_record(self):
            self.assertDeny("pytest || echo PASS >> r")

        def test_05_flip_pipe_hides_status(self):
            self.assertDeny("pytest | tee log && echo PASS >> r")
            self.assertDeny("pytest | tail -5 && echo PASS >> r")

        def test_06_flip_partial_gating(self):
            self.assertDeny("pytest; pytest2 && echo PASS >> r")  # pytest2 is no gate: this is case 1 again

        def test_06b_flip_partial_gating_fork(self):
            # Both commands are gates: denied only through the path where the first failed, the second passed.
            self.assertDeny("pytest; tox && echo PASS >> r")
            self.assertDeny("make test; go test ./... && echo PASS >> r")

        def test_07_flip_heredoc(self):
            self.assertDeny("pytest\ncat >> r <<EOF\nFINAL: PASS\nEOF")
            self.assertDeny("pytest; cat >> r <<'EOF'\nsummary\nFINAL: PASS\nEOF\necho next")
            self.assertDeny("pytest; cat <<-EOF >> r\n\tDONE\n\tEOF")

        def test_08_flip_substitution(self):
            self.assertDeny("x=$(pytest -q); echo PASS >> r")

        def test_17_flip_check_mark(self):
            reason = self.assertDeny("make test; echo " + chr(9989) + " >> r")
            self.assertIn(chr(9989), reason)

        def test_flip_more_denies(self):
            for c in ("timeout 600 pytest; echo PASS >> r", "sudo -u x env A=1 pytest -q; echo PASS >> r",
                      "! pytest && echo PASS >> r", "python3 -m pytest -q; echo PASSED >> r",
                      "python3 -mpytest; echo PASS >> r", "python3 -W error -m unittest; echo DONE >> r",
                      "npm test; echo DONE >> r", "yarn run check; echo DONE >> r", "cargo test; echo PASS >> r",
                      "make -C sub check; echo COMPLETE >> r", "./run_tests.sh; echo PASS >> r",
                      "bash run_tests.sh; echo PASS >> r", "python3 hook.py --self-test; echo PASS >> r",
                      "pytest; printf 'VERIFIED\\n' >> r", "pytest; echo PASS | tee -a r",
                      "pytest; tee r <<< SUCCESS", "pytest; echo PASS &> r", "pytest; echo PASS 1> r",
                      "pytest; echo PASS >& r", "pytest && echo PASS >> r; echo DONE >> r2",
                      "pytest; echo \"tests: GREEN\" > r", "pytest; x=$(echo PASS >> r)",
                      "pytest; echo PASS >> r & wait", "set -x; pytest; echo PASS >> r",
                      "set +e; pytest; echo PASS >> r", "vitest run; echo PASS >> r",
                      "case a in a) :;; esac; pytest; echo PASS >> r"):
                self.assertDeny(c)

        # cases 9 to 16: must not fire
        def test_09_gated(self):
            self.assertAllow("pytest && echo PASS >> r")
            self.assertAllow("pytest && tox && echo PASS >> r")

        def test_10_or_exit(self):
            self.assertAllow("pytest || exit 1; echo PASS >> r")
            self.assertAllow("pytest || { echo failed; exit 1; }; echo PASS >> r")

        def test_11_compound_barrier(self):
            self.assertAllow("if pytest; then echo PASS >> r; fi")
            self.assertAllow("pytest; if true; then :; fi; echo PASS >> r")

        def test_12_status_examined(self):
            self.assertAllow("pytest; rc=$?; echo \"rc=$rc\" >> r")
            self.assertAllow("pytest; echo \"PASS ${?}\" >> r")
            self.assertAllow("pytest | tee log; echo \"PASS ${PIPESTATUS[0]}\" >> r")

        def test_13_errexit(self):
            for c in ("set -euo pipefail; pytest | tee log; echo PASS >> r", "set -e; pytest; echo PASS >> r",
                      "set -o errexit; pytest; echo PASS >> r", "set -o pipefail; pytest; echo PASS >> r",
                      "pytest; echo PASS >> r; ( set -e )"):
                self.assertAllow(c)

        def test_14_no_redirect(self):
            self.assertAllow("pytest; echo PASS")
            self.assertAllow("pytest; echo PASS 2>> r")

        def test_15_dev_targets(self):
            for c in ("pytest; echo PASS > /dev/stderr", "pytest; echo PASS >> /dev/null", "pytest; echo PASS >&2",
                      "pytest; echo PASS > r 1>&2", "pytest; echo PASS | tee /dev/stderr"):
                self.assertAllow(c)

        def test_16_not_gates(self):
            for c in ("test -f x; echo DONE >> r", "[ -f x ]; echo DONE >> r", "make build; echo DONE >> r",
                      "npm install test; echo DONE >> r", "go vet; echo DONE >> r", "latest; echo DONE >> r",
                      "python3 -c 'import pytest'; echo PASS >> r"):
                self.assertAllow(c)

        def test_negative_list_extras(self):
            for c in ("pytest; echo done >> timing.log", "pytest; echo x >> PASS.txt", "pytest; echo PASS >> \"$R\"",
                      "pytest; echo PASS >> $(date +%s).log", "pytest & echo PASS >> r",
                      "exec pytest; echo PASS >> r", "pytest; cat >> r <<PASS\nx\nPASS",
                      "pytest | echo PASS >> r", "pytest; cat notes <<EOF >> r\nPASS\nEOF",
                      "pytest; printf -v x PASS >> r", "pytest; echo PASS; echo x >> r",
                      "echo PASS >> r; pytest", "pytest && echo PASS >> r || true"):
                self.assertAllow(c)

        # case 18: the opt-out
        def test_18_record_ok_opt_out(self):
            self.assertAllow(base + "  # record-ok: dispatch log")
            self.assertAllow(base + " #record-ok")
            self.assertAllow(base + " # record-ok: x\n")
            self.assertDeny(base + "; echo a#record-ok: mid-word")
            self.assertDeny(base + " # record-okay")
            self.assertDeny("echo '# record-ok: x'\n" + base)

        # case 19: the syntax oracle
        def test_19_oracle(self):
            self.assertAllow(base, oracle=reject)
            self.assertDeny(base, oracle=no_oracle)
            if _trusted_bash() is None:
                self.skipTest("no trusted bash: the real oracle is unavailable")
            bad = base + "; &&"
            self.assertIs(_bash_syntax_ok(bad), False)
            self.assertDeny(bad, oracle=no_oracle)
            self.assertAllow(bad, oracle=None)
            self.assertDeny(base, oracle=None)

        # case 20: fail-open inputs
        def test_20_malformed_payloads(self):
            for p in (None, 7, "x", [], {}, {"tool_name": "Bash"}, {"tool_name": "Bash", "tool_input": "x"},
                      {"tool_name": "Bash", "tool_input": {"command": 7}},
                      {"tool_name": "Bash", "tool_input": {"command": ""}},
                      {"tool_name": "Read", "tool_input": {"command": base}},
                      {"tool_name": None, "tool_input": {"command": base}}):
                self.assertIsNone(_decide(p, {}, no_oracle), p)
            self.assertIsNone(decide(base, hook_event_name="PostToolUse"))

        def test_20_subagent_payload(self):
            self.assertIsNone(decide(base, agent_id="a1"))
            self.assertIsNone(decide(base, agent_id=None))

        def test_20_worker_envs(self):
            for env in ({"AIQT_HOOKS_WORKER": "1"}, {"ORCH_WORKER": "1"}, {"ORCH_VERIFY_OWNER": ""},
                        {"ORCH_VERIFY_OWNER": "x"}, {"AIQT_HOOKS_WORKER": "0", "ORCH_WORKER": "1"}):
                self.assertIsNone(decide(base, env=env), env)
            for env in ({"AIQT_HOOKS_WORKER": "0"}, {"AIQT_HOOKS_WORKER": "yes"}, {"ORCH_WORKER": "0"}):
                self.assertIsNotNone(decide(base, env=env), env)

        def test_20_bad_argv(self):
            old = sys.stdout
            sys.stdout = io.StringIO()
            try:
                for argv in (None, 7, "--self-test", ["x", 3], {"a": 1}):
                    self.assertEqual(main(argv), 0)
                self.assertEqual(sys.stdout.getvalue(), "")
            finally:
                sys.stdout = old

        def test_20_process_fail_open(self):
            bad = (b"", b"not json", b"[1, 2]", b"7", b"\xff\xfe\x00garbage", b'{"tool_name": "Bash"',
                   payload_bytes(base)[:-1])
            for data in bad:
                self.assertEqual(run_hook(data), (0, b"", b""), data[:30])
            for extra in ({"AIQT_HOOKS_WORKER": "1"}, {"ORCH_WORKER": "1"}, {"ORCH_VERIFY_OWNER": ""}):
                env = dict({"LC_ALL": "C"}, **extra)
                self.assertEqual(run_hook(payload_bytes(base), env), (0, b"", b""), extra)

        def test_20_process_deny_is_one_json_line(self):
            rc, out, err = run_hook(payload_bytes(base))
            self.assertEqual((rc, err), (0, b""))
            self.assertTrue(out.endswith(b"\n") and out.count(b"\n") == 1, out)
            h = json.loads(out)["hookSpecificOutput"]
            self.assertEqual(h["permissionDecision"], "deny")
            self.assertEqual(run_hook(payload_bytes("pytest && echo PASS >> r")), (0, b"", b""))

        def test_20_directory_stdin_guard(self):
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
            for c in ("f() { :; }; " + base, "function f { :; }; " + base, base + "; done", base + "; fi",
                      "pytest; echo \"PASS >> r", "x" * 70000 + "; " + base,
                      "pytest; echo PASS >> r; ;;"):
                self.assertAllow(c)

        # the round-1 review items: each passes only with its fix present
        def test_qa_runner_options(self):
            for c in ("make -j test; echo PASS >> r", "make -j 4 -C sub test; echo PASS >> r",
                      "make --jobs check; echo PASS >> r", "npm --prefix web test; echo PASS >> r",
                      "npm -w pkg run test; echo PASS >> r", "pnpm -C web test; echo PASS >> r",
                      "yarn --cwd web test; echo PASS >> r", "go -C . test ./...; echo PASS >> r",
                      "cargo +stable test; echo PASS >> r", "just --set a b test; echo PASS >> r"):
                self.assertDeny(c)
            for c in ("make -C test build; echo PASS >> r", "npm --prefix test install; echo PASS >> r",
                      "go -C test build; echo PASS >> r"):
                self.assertAllow(c)

        def test_qa_errexit_option_values(self):
            for c in ("set -o nounset -o errexit; pytest; echo PASS >> r", "set -o posix -e; pytest; echo PASS >> r",
                      "set -uo pipefail; pytest; echo PASS >> r", "builtin set -e; pytest; echo PASS >> r"):
                self.assertAllow(c)
            for c in ("set -o nounset; pytest; echo PASS >> r", "set +o errexit; pytest; echo PASS >> r",
                      "set -ou nounset pipefail; pytest; echo PASS >> r",
                      "set -- -e; pytest; echo PASS >> r"):
                self.assertDeny(c)

        def test_qa_tee_stdout(self):
            self.assertDeny("pytest; echo PASS | tee > r")
            self.assertDeny("pytest; echo PASS | tee -a log >> r")
            self.assertAllow("pytest; echo PASS | tee > /dev/null")

        def test_qa_builtin_echo(self):
            self.assertDeny("pytest; builtin echo PASS >> r")
            self.assertDeny("pytest; builtin printf PASS >> r")
            self.assertAllow("pytest; builtin grep PASS >> r")

        def test_qa_parameter_expansion(self):
            for c in ('pytest; echo "$PASS" >> r', "pytest; echo $PASS >> r", 'pytest; echo "${PASS}" >> r',
                      'pytest; echo "${X:-PASS}" >> r', "pytest; cat >> r <<EOF\n$DONE\nEOF"):
                self.assertAllow(c)
            self.assertDeny('pytest; echo "$X PASS" >> r')
            self.assertDeny('pytest; echo "${X}PASS" >> r')

        def test_qa_extra_argv_fails_open(self):
            for extra in (["--bogus"], ["extra"], ["--self-test", "extra"], ["-", "--self-test"]):
                p = subprocess.run([sys.executable, "-I", "-S", "-B", here] + extra, input=payload_bytes(base),
                                   capture_output=True, env={"LC_ALL": "C"}, timeout=30)
                self.assertEqual((p.returncode, p.stdout, p.stderr), (0, b"", b""), extra)
            self.assertEqual(main([]), 0)

        # the round-2 review items: each passes only with its fix present
        def test_r2_printf_precision_and_assembly(self):
            for c in ("pytest; printf '%10.10s\\n' PASS >> r", "pytest; printf '%.4s\\n' PASSED >> r",
                      "pytest; printf '%s%s\\n' PA SS >> r", "pytest; printf '%c%c%c%c' P A S S >> r",
                      "pytest; printf '%3s%s' PA SS >> r", "pytest; printf '%s\\n' x PASS >> r",
                      "pytest; printf '%.*s' 4 PASS >> r", "pytest; printf '%.*s' 4x PASS >> r",
                      "pytest; printf '%%s PASS' >> r", "pytest; printf '%.2s%.2s' PASS SS >> r"):
                self.assertDeny(c)
            for c in ("pytest; printf '%.3s\\n' PASS >> r", "pytest; printf '%.2s-%.2s' PASS SS >> r",
                      "pytest; printf '%-3s%s' PA SS >> r", "pytest; printf '%d%s' PASS x >> r",
                      "pytest; printf '%s' >> r PASS2", "pytest; printf '%' PASS >> r",
                      "pytest; printf -vx PASS >> r"):
                self.assertAllow(c)

        def test_r2_printf_cost(self):
            for make in (lambda k: "pytest; printf '%" + "0" * k + "s' PASS >> r",
                         lambda k: "pytest; printf '%" + "0" * k + "d %s' 1 PASS >> r",
                         lambda k: "pytest; printf '" + "%s" * k + "' PASS >> r",
                         lambda k: "pytest; printf '%s ' " + "x " * k + "PASS >> r",
                         lambda k: 'pytest; echo "' + "${a:-" * k + "}" * k + ' PASS" >> r'):
                small, large = work(make(400)), work(make(3200))
                self.assertLess(large / small, 10, (small, large))  # 8x the input: linear near 8, quadratic 64
            # Line counts cannot see work inside a regex engine (backtracking), so the scanners that read a whole
            # format or text in one pass use no regex at all (checked on their code objects, not their timing).
            for fn in (_printf_text, _strip_params):
                self.assertNotIn("re", fn.__code__.co_names, fn.__name__)
                self.assertFalse([n for n in fn.__code__.co_names if n.endswith("_RE")], fn.__name__)

        def test_r2_clustered_options(self):
            for c in ("make -sC test build; echo PASS >> r", "make -Ctest build; echo PASS >> r",
                      "make -kf test build; echo PASS >> r", "pnpm -sC test install; echo PASS >> r",
                      "cargo -Ztest build; echo PASS >> r", "go -C test build; echo PASS >> r",
                      "python3 -Wtest -c 'import x'; echo PASS >> r", "bash -xo pipefail build.sh; echo PASS >> r",
                      "just -sf test build; echo PASS >> r", "timeout -vk 5 60 build.sh; echo PASS >> r"):
                self.assertAllow(c)
            for c in ("make -sC sub test; echo PASS >> r", "make -j4 check; echo PASS >> r",
                      "make -sj 4 test; echo PASS >> r", "python3 -Wd -m pytest; echo PASS >> r",
                      "python3 -Bm pytest; echo PASS >> r", "bash -xo pipefail run_tests.sh; echo PASS >> r",
                      "timeout -vk 5 60 pytest; echo PASS >> r", "timeout -s9 60 pytest; echo PASS >> r"):
                self.assertDeny(c)

        def test_r2_nested_parameter_expansion(self):
            for c in ('RESULT=failed; pytest; echo "${RESULT:-${LABEL:-test}: PASS}" >> r',
                      'pytest; echo "${A:-{PASS}}" >> r', 'pytest; echo "${A:-x PASS" >> r'):
                self.assertAllow(c)
            self.assertDeny('pytest; echo "${A:-${B}} PASS" >> r')

        def test_r2_descriptor_moves_and_input_duplication(self):
            for c in ("pytest; echo PASS 3>>r 1>&3-", "pytest; cat 3<<<PASS 0<&3 >> r",
                      "pytest; cat 3<<<PASS 0<&3- >> r", "pytest; echo PASS | tee 3<&0 r"):
                self.assertDeny(c)
            for c in ("pytest; echo PASS 3>>r 1>&3- >&3", "pytest; cat 3<<<PASS 0<&4 >> r",
                      "pytest; cat 3<<<PASS 0<&3 < /dev/null >> r"):
                self.assertAllow(c)

        def test_r2_builtin_forms(self):
            for c in ("pytest; builtin -- echo PASS >> r", "pytest; >r builtin echo PASS",
                      "pytest; 2>/dev/null builtin printf PASS >> r", "pytest; \"builtin\" echo PASS >> r"):
                self.assertDeny(c)
            for c in ("pytest; builtin -- grep PASS >> r", "pytest; timeout 1 builtin echo PASS >> r"):
                self.assertAllow(c)  # timeout runs a program, and builtin is none: nothing is written

        # the round-3 review items: each passes only with its fix present
        def test_r3_printf_reuse_cost(self):
            for make in (lambda k: "pytest; printf '%" + "0" * k + "s ' " + "x " * (k // 8) + "PASS >> r",
                         lambda k: "pytest; printf '" + "y" * k + "%s' " + "x " * (k // 8) + "PASS >> r",
                         lambda k: "pytest; printf '%*s %.*s ' " + "-3 x 2 y " * (k // 16) + "8 PASS 9 z >> r",
                         lambda k: ("printf '" + "z" * 64 + "%s' " + "x " * 8 + ";\n") * (k // 64)
                         + "pytest; echo PASS >> r",
                         lambda k: 'pytest; echo "' + "${a:-'}' x}" * (k // 16) + ' PASS" >> r',
                         lambda k: 'pytest; echo "' + "${a:-\\}}" * (k // 16) + "'" * (k // 16) + ' PASS" >> r'):
                small, large = work(make(1600)), work(make(12800))
                self.assertLess(large / small, 10, (small, large))  # 8x the input: linear near 8, quadratic 64
            # A reused literal is charged on every replay, so a long literal with many arguments stops at the budget
            # instead of building a huge text that line counts would not see (the copying happens inside join).
            _PRINTF_METER[0] = _PRINTF_BUDGET
            out = _printf_text("y" * 20000 + "%s", ["x"] * 1000)
            self.assertTrue(out is None or len(out) <= _PRINTF_BUDGET, None if out is None else len(out))
            for fn in (_printf_parse, _printf_text, _blank_escapes, _int_arg, _spec_number):
                self.assertFalse([n for n in fn.__code__.co_names if n == "re" or n.endswith("_RE")], fn.__name__)

        def test_r3_signed_dynamic_width_and_precision(self):
            for c in ("pytest; printf '%*s%s\\n' -8 PASS unit >> r", "pytest; printf '%-*s%s\\n' 8 PASS unit >> r",
                      "pytest; printf '%s%*s' PASS +8 unit >> r", "pytest; printf '%.*s\\n' -1 PASS >> r",
                      "pytest; printf '%.*s' +4 PASS >> r"):
                self.assertDeny(c)
            for c in ("pytest; printf '%*s%s\\n' 2 PASS unit >> r", "pytest; printf '%.*s\\n' 2 PASS >> r",
                      "pytest; printf '%*s%s' x PA SS2 >> r"):
                self.assertAllow(c)

        def test_r3_literal_brace_in_expansion(self):
            for c in ('X=x; pytest; echo "${X:-{} PASS}" >> r', 'pytest; echo "${A:-{a} PASS}" >> r',
                      'pytest; echo "${A:-${B:-{}} PASS}x" >> r', 'pytest; echo "${A:-${B:-{x}}: PASS}" >> r',
                      'pytest; echo "${A#{}: PASS}" >> r', 'pytest; echo "result ${A:-{ } PASS}" >> r'):
                self.assertDeny(c)  # bash writes PASS outside the expansion in each, whatever A holds
            # PASS inside the expansion (a default bash may or may not write): set aside, as every expansion is
            for c in ('pytest; echo "${A:-{PASS}}" >> r', 'pytest; echo "${A:-${B:-x}: PASS}" >> r',
                      "pytest; echo \"${A:-'}' PASS}\" >> r"):
                self.assertAllow(c)

        def test_r3_percent_b_escapes(self):
            for c in ("pytest; printf '%b' '\\nPASS' >> r", "pytest; printf '%b\\n' 'x\\tPASS' >> r",
                      "pytest; printf 'x\\nPASS' >> r"):
                self.assertDeny(c)
            for c in ("pytest; printf '%b' 'x\\cPASS' >> r", "pytest; printf '%b%s' '\\c' PASS >> r",
                      "pytest; printf '%b' 'x\\x50ASS' >> r"):
                self.assertAllow(c)

        # the round-4 review items: each passes only with its fix present
        def test_r4_backslash_before_brace_closes(self):
            # bash writes `x PASS` (an escaped backslash, then the closing brace): the brace must close here, so
            # an escaped brace (`\\}`), which looks the same once read, closes too, a disclosed false deny
            for c in ('X=x; pytest; echo "${X:-\\\\} PASS" >> r', 'pytest; echo "${A:-\\} PASS}" >> r'):
                self.assertDeny(c)

        def test_r4_star_number_forms(self):
            for c in ("pytest; printf '%s%*s' PASS \"'A\" unit >> r", "pytest; printf '%s%*s' PASS ' 8' unit >> r",
                      "pytest; printf '%s%.*s' PASS \" 0\" x >> r"):
                self.assertDeny(c)
            for c in ("pytest; printf '%s%*s' PASS ' -8' unit >> r", "pytest; printf '%s%*s' PASS \"'\" unit >> r"):
                self.assertAllow(c)  # a negative width pads after the text; a lone quote reads 0

        def test_r4_incomplete_spec_stops_output(self):
            for c in ("pytest; printf '%s%-5' PA SS >> r", "pytest; printf 'x%s%.' PA SS >> r"):
                self.assertAllow(c)  # bash writes PA (or xPA) and stops: no replay pass writes SS
            for c in ("pytest; printf 'PASS %-5' >> r", "pytest; printf '%s %-5' PASS >> r",
                      "pytest; printf '%s%s x' PA SS >> r"):
                self.assertDeny(c)

        # the round-5 review items: each passes only with its fix present
        def test_r5_escaped_quote_opens_no_span(self):
            self.assertDeny("X=x; pytest; echo \"${X:-\\'} PASS '\" >> r")  # bash writes x PASS '
            self.assertDeny('X=x; pytest; echo "${X:-\\\\} PASS" >> r')  # an escaped backslash, then the brace

        def test_r5_star_numbers_as_bash_reads_them(self):
            for c in ("pytest; printf '%.*s' ' 010' '123 PASSxy PASS' >> r",
                      "pytest; printf '%s%*s' PASS 0x8 unit >> r", "pytest; printf '%s%*s' PASS 8x unit >> r",
                      "pytest; printf '%s%*s' PASS ' +010' unit >> r"):
                self.assertDeny(c)  # octal 010 is 8, hex 0x8 is 8, 8x reads its prefix 8
            for c in ("pytest; printf '%s%*s' PASS ' 010' abcdefgh >> r", "pytest; printf '%.*s%s' 0x0 PA SS >> r",
                      "pytest; printf '%s%*s' PASS 08 unit >> r", "pytest; printf '%s%*s' PASS 0x unit >> r",
                      "pytest; printf '%s%*s' PASS -0x8 unit >> r", "pytest; printf '%.*s' n PASS >> r"):
                self.assertAllow(c)  # bash writes PASSabcdefgh, SS, PASSunit (0), PASSunit (0), PASSunit, nothing
            self.assertEqual([_int_arg(v) for v in ("010", " -0x1F", "'A", "", "+", "9" * 12, "0x", "08", "7z")],
                             [8, -31, 65, 0, 0, _BIG, 0, 0, 7])

        def test_r5_disclosed_misses_after_hidden_escapes(self):
            # RESIDUAL COVERAGE names these: bash writes PASS in each, but the escape that ends the expansion is gone
            # once the lexer has read the word, so the claim reads as inside it. Pinned so a change is noticed.
            self.assertAllow('X=x; pytest; echo "${X:-\\${} PASS}" >> r')
            self.assertAllow("unset X; pytest; echo ${X:-\"'\"}\" PASS\"\\' >> r")

        def test_qa_compound_pipeline_stage(self):
            for c in ("( true ) | pytest; echo PASS >> r", "pytest | (cat > log); echo PASS >> r",
                      "{ true; } | pytest; echo PASS >> r", "pytest | { cat; } && echo PASS >> r",
                      "pytest || { exit 1; } | cat; echo PASS >> r"):
                self.assertDeny(c)
            for c in ("( pytest ) | cat; echo PASS >> r",
                      "{ true; } | pytest && echo PASS >> r", "{ pytest; } && echo PASS >> r"):
                self.assertAllow(c)

        def test_qa_pipeline_growth(self):
            def cost(k):
                return work(" | ".join("pytest t%d" % i for i in range(k)) + "; echo PASS >> r")
            small, large = cost(150), cost(1200)
            self.assertLess(large / small, 10, (small, large))  # 8x the stages: linear is 7.9 here, quadratic 64

        def test_qa_oversized_command_not_judged(self):
            self.assertAllow(base + " " * 65536 + " # record-ok: intentional log")
            self.assertAllow(base + " " * 65536)
            self.assertAllow(base + " " + chr(233) * 33000)  # under 64 KiB of characters, over it in bytes
            self.assertDeny(base + " " * 60000)

        def test_qa_stdin_order(self):
            for c in ("pytest; cat <<<PASS < /dev/null >> r", "pytest; echo PASS | tee r < /dev/null",
                      "pytest; cat <<EOF < /dev/null >> r\nPASS\nEOF"):
                self.assertAllow(c)
            for c in ("pytest; cat < /dev/null <<<PASS >> r", "pytest; cat 3<<<x <<<PASS >> r",
                      "pytest; tee r <<< PASS"):
                self.assertDeny(c)

        def test_qa_printf_format(self):
            for c in ("pytest; printf '%.0s\\n' PASS >> r", "pytest; printf '%d\\n' PASS >> r",
                      "pytest; printf 'x\\n' PASS >> r"):
                self.assertAllow(c)
            for c in ("pytest; printf '%s\\n' PASS >> r", "pytest; printf 'PASS %d\\n' 3 >> r",
                      "pytest; printf -- '%b' PASS >> r", "pytest; printf '%-10s' PASS >> r"):
                self.assertDeny(c)

        def test_qa_quote_assembled_words(self):
            self.assertDeny('pytest; echo "PA"SS >> r')
            self.assertDeny('pyt"est"; echo PASS >> r')
            self.assertDeny("py\\test; echo P\\ASS >> r")

        def test_qa_descriptor_duplication(self):
            for c in ("pytest; echo PASS 3>>r 1>&3", "pytest; echo PASS 2>r >&2", "pytest; echo PASS 3>r >&3"):
                self.assertDeny(c)
            for c in ("pytest; echo PASS 3>>r 1>&4", "pytest; echo PASS 3>>r 1>&3 >&-", "pytest; echo PASS 1<r"):
                self.assertAllow(c)

        def test_strip_source(self):
            sample = ('X = 1  # note\n# whole line\n\ndef f(a):\n    """doc\n    more"""\n'
                      '    s = "a # not a comment"\n    return s  # trailing\n\n\nclass C(str):\n    """d"""\n'
                      '    __slots__ = ()\n\n\nif X:\n    "kept: not a def or class body"\n    y = """kept"""\n')
            want = ('X = 1\n\ndef f(a):\n    s = "a # not a comment"\n    return s\n\n\nclass C(str):\n'
                    '    __slots__ = ()\n\n\nif X:\n    "kept: not a def or class body"\n    y = """kept"""\n')
            self.assertEqual(_strip_source(sample), want)
            self.assertEqual(_strip_source(want), want)  # idempotent
            compile(_strip_source(sample), "sample", "exec")

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


        # case 21: vendoring, cost, hangs, and non-ASCII input
        def test_21_vendor_hashes(self):
            blocks = vendor_blocks()
            self.assertEqual(set(blocks), set(_VENDOR))
            for name, (head, body) in blocks.items():
                ref, first, last, digest = _VENDOR[name]
                self.assertEqual(head[4:], ["from", "%s:%d-%d" % (ref, first, last)], name)
                self.assertEqual(hashlib.sha256(body).hexdigest(), digest, name)

        def test_21_vendor_byte_identity(self):
            ref_dir = os.environ.get("G_REF_DIR") or os.path.dirname(here)
            blocks, missing = vendor_blocks(), []
            for name, (_, body) in sorted(blocks.items()):
                ref, first, last, _ = _VENDOR[name]
                path = os.path.join(ref_dir, ref)
                if not os.path.isfile(path):
                    missing.append(ref)
                    continue
                lines = open(path, "rb").read().decode("ascii").split("\n")[first - 1:last]
                self.assertEqual(_strip_source("".join(x + "\n" for x in lines)).encode("ascii"), body, name)
            if missing:
                self.skipTest("SKIPPED, reference not found: " + ", ".join(sorted(set(missing))))

        def test_21_source_house_rules(self):
            src = open(here, "rb").read()
            src.decode("ascii")
            for n, line in enumerate(src.split(b"\n"), 1):
                self.assertLessEqual(len(line), 120, n)
            for bad in (chr(8211), chr(8212)):
                self.assertNotIn(bad.encode(), src)

        def test_21_no_internal_names(self):
            # Guard numbers and model, vendor, and orchestration names, the words split so this test does not
            # match itself; only the two legacy worker env names are allowed.
            src = open(here).read()
            pattern = re.compile(r"\bG" + r"\d+\b|" + "|".join(
                ("cla" + "ude", "gem" + "ini", "cod" + "ex", "fab" + "le", "g" + "pt", "orches" + "trator",
                 "fl" + "eet", "orch" + "-verify", "la" + "b_in" + "fra", "OR" + "CH_[A-Z_]+")), re.I)
            found = {m.group() for m in pattern.finditer(src)}
            self.assertEqual(found - {"OR" + "CH_WORKER", "OR" + "CH_VERIFY_OWNER"}, set())

        def test_21_cost_growth(self):
            for make in (lambda k: "pytest || true; " * k + "echo PASS >> r",
                         lambda k: "pytest | " * k + "tee log; echo PASS >> r",
                         lambda k: "( true ) | pytest; " * k + "echo PASS >> r",
                         lambda k: "pytest; tox && echo x >> r\n" * k + "echo PASS >> r",
                         lambda k: "cat >> r <<EOF\nPASS\nEOF\n" * k + "pytest; echo PASS >> r",
                         lambda k: "pytest; builtin echo x 3>>r 1>&3 < /dev/null; " * k + "echo PASS >> r"):
                small, large = work(make(25)), work(make(200))  # the larger within the 4000-token bound
                self.assertLess(large / small, 10, (small, large))  # 8x the input: linear 7.3 to 7.9, quadratic 64

        def test_21_hang_ceiling(self):
            # A hang guard, not a speed test: each run's only time bound is run_hook's 30 second kill, far above
            # any plausible run (tens of milliseconds); what is asserted is the exit status and the output shape.
            shapes = (base + "\n" * 3000 + base * 3000, "pytest; echo PASS >> r; " * 3000,
                      "pytest; echo PASS >> " + "$(" * 5000, base + " " + "#record-ok " * 7000,
                      "pytest; " + "((" * 30000 + " PASS", "pytest; " + "{ " * 20000 + "echo PASS >> r",
                      "pytest; cat >> r " + "<<E " * 3000 + "\nPASS\n" * 3000,
                      "pytest | " * 2000 + "echo PASS >> r")
            for c in shapes:
                rc, out, err = run_hook(payload_bytes(c))
                self.assertEqual((rc, err), (0, b""), c[:40])
                self.assertLessEqual(out.count(b"\n"), 1, c[:40])

        def test_21_non_ascii_command(self):
            word = chr(233) + chr(8364)
            reason = self.assertDeny("pytest " + word + "; echo PASS >> " + word)
            self.assertIn(word, reason)
            rc, out, err = run_hook(payload_bytes("pytest; echo " + chr(9989) + " >> " + word))
            self.assertEqual((rc, err), (0, b""))
            out.decode("ascii")
            self.assertIn(chr(9989), json.loads(out)["hookSpecificOutput"]["permissionDecisionReason"])

    suite = unittest.defaultTestLoader.loadTestsFromTestCase(T)
    result = unittest.TextTestRunner(stream=sys.stderr, verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
