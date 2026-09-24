#!/usr/bin/env python3
"""PreToolUse hook (parallel-write-read): deny a command reading a file whose write, issued moments ago, never landed.

WHAT IT DOES
    An assistant that issues a file write and, in the same batch of parallel tool calls, a shell command that
    consumes the file (`gh pr create --body-file body.md`, a dispatch reading a prompt file) assumes the write
    lands first. When the write is blocked, fails, or is still pending, the consumer reads a missing file or, worse,
    a stale one, and its effect (a published body, a spent dispatch) cannot be recalled. This hook notices the
    write attempt and, when a later shell command names that file while it is still exactly as it was before the
    attempt, denies the command once, before it runs.

    One file, two roles, both on the PreToolUse event; the role follows the payload's tool_name:
      - RECORD (matcher Write|Edit|MultiEdit|NotebookEdit, the harness's file tools): the target path
        (tool_input.file_path, or notebook_path), made absolute against the payload's cwd and resolved through
        symbolic links, is recorded with its state just before the attempt: missing, or its device, inode, size,
        modification time, and change time (lstat, nanoseconds). The recorder never prints anything.
      - CHECK (matcher Bash): DENY when the command reads a recorded path whose current state is identical to
        the recorded one (still missing, or the same device, inode, size, modification time, and change time):
        the write never landed. Deny once per exact command: the same command text re-issued while the file is
        still unchanged is allowed, the deliberate "the current content is what I intend" case.
    Register the launch line REGISTRATION (below the imports), filled with python3 and this file's absolute path,
    once for each matcher above. Output: nothing (allow), or ONE line holding the standard PreToolUse deny object.
    Exit status: always 0; the decision travels in the JSON. The verdict is deny or silence: this hook never asks.

    A command READS a recorded path when a word it reads as a file, after tilde expansion (a leading, unquoted `~`
    or `~user`) and brace expansion (comma lists, nested, at most 64 names, honouring the word's quoting), made
    absolute against the payload's cwd, resolves through symbolic links to exactly that path; only a name whose file
    name is the recorded path's own, or the one the write named, is resolved. The words read as files are a MINIMAL,
    EXACT set, and no other word is ever read as one: (1) the target of an input redirection `<`, on any command;
    (2) the value of one of these options, as the next word, attached with `=`, or (for a short option) attached
    directly, on any command but echo, printf, `:`, and true: `--body-file`, `--notes-file`, `--env-file`, and
    `--arg-file`; `-F` on gh only (also `-F=FILE`); `--rawfile` and `--slurpfile` on jq only (the second word after
    the option, the file); and `-a` on xargs only, and only as a word of its own followed by the file; (3) the FIRST
    operand of cat, bash, sh, zsh, python, python3, node, ruby, perl, source, or `.`, and only when it is the first
    word after the command, with no option word (one starting with `-` or `+`) before it; for an interpreter that is
    its script, and none of the script's arguments count.

    A command is found past its assignments, reserved words, and the prefix commands env, sudo, doas, nohup,
    command, builtin, exec, time, nice, timeout, stdbuf, ionice, and xargs. Each prefix's own options are read by
    its EXACT grammar: a short cluster letter by letter (a flag continues; a required value is the rest of the word
    or the next word; an optional value, as xargs -e, -i, and -l take, only the rest of the word), and a long option
    with an optional value (xargs --eof, --replace, --max-lines; env's signal options; sudo --preserve-env) takes it
    only after `=`, never the next word. `--` ends a prefix's options, and env's assignments may still follow it. An
    unjudged option, either a query under which nothing runs (command -v or -V in any cluster, --help, --version,
    sudo -l) or one whose effect is not modelled (env -S and --split-string, which split their string into a command
    and run it; sudo -h, which is help in some versions and a host in others), and ANY option a prefix's grammar
    does not define leave the whole command unjudged (allowed), rather than guessing its command word, and a file
    that prefix's own options would have read is dropped with it (`xargs -a f --help` is allowed); the grammars
    follow the documented options of GNU coreutils and of the uutils coreutils this was checked against (env -a and
    -f take a value; timeout -f and -p are flags, and timeout reads its options on both sides of the duration, so
    its command starts at the second word that is no option); so the listed read options are read on a prefix only
    where they are that prefix's own (xargs -a and --arg-file), and `sudo --env-file f` or `time --body-file=f` is
    unjudged. A command starts after reserved words, and after `time` with its `-p` or `--`, whether a simple
    command, a compound command, or a `!` follows. The words inside `[[ ]]` and an
    arithmetic `(( ))` are operands, never redirection targets or commands (`[[ a < f ]]` compares strings); only
    their substitutions are read. Simple commands are split at `;`, `&`, `&&`, `||`, pipes, parentheses, and
    newlines, and the commands inside a `$(...)`, `<(...)`, `>(...)`, or backtick substitution, inside an array
    assignment's substitutions, and inside the `$(...)` and backtick substitutions of an arithmetic expansion
    `$((...))`, are read the same way. Once a cd, pushd, or popd, an env -C or --chdir, or a sudo -D or --chdir is
    reached, no later word and no later command is read, except that an input redirection `<` of that same simple
    command is still read, since the shell opens it in the current directory before the directory changes (`env
    -Csub cat < f` reads the current directory's f); the letter counts only as an option letter of its own cluster
    (`env -iCsub`), never inside another option's attached value (`env -uCLICOLOR`). A word holding an unexpanded
    `$`, a substitution, or an unquoted glob character is not judged. Only exact path equality counts: a file of the
    same name in another directory is a different file. The deny names the path as the write named it.

STATE
    One JSON file per session, named for the payload's session_id (1 to 128 characters from A-Z, a-z, 0-9, `_`, and
    `-`; any other session_id is not recorded or checked), in a per-user directory named for the uid,
    `parallel-write-read-<uid>`, made mode 0700. The directory sits in XDG_RUNTIME_DIR when that names an absolute
    path to a real directory (not a symbolic link) owned by this user with mode 0700, else in the system temporary
    directory (the standard library's choice, which honours TMPDIR). The directory is opened without following a
    symbolic link and must be a directory owned by this user with no group or other permission bits; the file,
    opened through that directory's descriptor without following a symbolic link, must be a regular file owned by
    this user with one link and no group or other permission bits; anything else is used for nothing. Updates hold
    an exclusive advisory lock, waited on for at most 200 milliseconds (past that: fail open). The file holds at
    most 64 entries and 64 KiB, dropping the oldest entries to fit. An entry lives 900 seconds either side of the
    wall clock, which separate processes share: a backward clock step leaves a recent entry live rather than
    dropping it. A new attempt on a path replaces that path's entry. Each entry keeps the digest (128 bits of
    sha256) of every command denied on it until it expires or is replaced, at most 32; an entry holding 32 denies no
    further command. A malformed entry is dropped and counted in the file's `dropped` field; the rest are kept. A
    missing state file lets every command run; one that is not a state document at all (unparseable, over 64 KiB)
    lets every command run, and the next recorder rewrites it with only its own entry.

THREAT MODEL
    This is an accidental-ordering guard, not a security boundary. The actor is a well-meaning assistant that
    batches a producer with its consumer; nothing here resists a caller that sets out to evade it. So every internal
    error, every state problem (a foreign or symbolic-link directory, a lock not won in time, a corrupt file), and
    every malformed payload fails OPEN: no output, exit 0. The hook also stays silent for a verification worker
    process (AIQT_HOOKS_WORKER set to "1"; or the legacy names, ORCH_WORKER set to "1" or ORCH_VERIFY_OWNER present
    at all, even empty), for a payload carrying agent_id (a subagent's call), for a tool_name outside the roles
    above, for an event other than PreToolUse, for a bad argv, and for a stdin that is absent, unreadable, over 16
    MiB, incomplete after 2 seconds, not JSON, or not an object. Work is bounded: a command over 64 KiB of UTF-8 is
    not checked at all, the lexer counts every token but fails the check open only when it reaches an operator, a
    parenthesis, or a newline with more than 4000 tokens counted, so a command made mostly of words, with few of
    those, is read in full (its work still bounded by the 64 KiB limit), at most 256 words are resolved against the
    file system, and at most 64 recorded paths are examined. The hook never runs the command.

RESIDUAL COVERAGE.
    This guard sees only writes attempted through the harness's file tools in this session; a file produced by a
    shell command (a redirection, tee, git) leaves no record, so a consumer of it is never checked. It relies on the
    producer's PreToolUse hook running before the consumer's: a consumer ordered before its producer inside one tool
    batch finds no record and is missed, and that ordering has not been measured here, so every ordering this guard
    does not see fails toward allow, never toward a deny. READS THROUGH ANY OTHER COMMAND ARE NOT CHECKED: grep,
    sed, awk, jq's operands, diff, sort, uniq, head, tail, wc, xxd, make, docker, git, a compiler, an editor, and
    every command not named above read their files unchecked, and so does any option not named above (a -f, a
    --file, a configuration option). READS WITH AN OPTION BEFORE THE FILE ARE NOT CHECKED: `cat -e f`, `bash -s f`,
    `python3 -B f`, and `node --require x f` are allowed, as is a later operand (`cat a f`), and an option clustered
    with others (`xargs -ra f`). A prefix command given an option outside its grammar, or an unjudged option (env -S
    runs a command this guard does not read; sudo -h differs between versions), is unjudged, and so is `[[` or `((`
    outside a command's start (after reserved words, or after `time` and its `-p`), which bash reads differently.
    timeout's options are read after its duration too, as the uutils timeout this was checked against accepts
    (`timeout 2 -v cat f` runs cat and is judged); a GNU timeout would run `-v` itself there and fail, so on such
    a host that deny names a command that could not have read the file. A `#` inside `$((...))` is not judged
    the way bash reads it: bash takes it as a comment only after a blank or newline, while this guard also does so
    after an operator character such as `(`, `)`, `|`, or `<`. A substitution behind such a `#` can therefore be
    missed (the command reads as unparseable here and is allowed, while bash runs the arithmetic's substitutions
    before refusing the `#`, as in `$(( (1)#$(cat f) ))`), or denied where bash never runs it.
    Every later word and later command after a cd, pushd, popd, env -C, or sudo -D is allowed, even where the
    directory change cannot affect it (inside a subshell that has ended); an input redirection `<` of the same
    simple command is still read, since the shell opens it in the current directory before the change. A path built
    by an unexpanded variable, a substitution, a glob, or a brace range (`{1..3}`), a quoted tilde, a partly quoted
    word in a here-document's substitutions holding a brace, tilde, or glob character, a symbolic link named neither
    as the file nor as the write named it, and a consumer inside a nested shell string (`bash -c '...'`), eval, a
    script file, a here-document body, or a backtick substitution inside a here-document body are not resolved and
    are allowed; a name with more than 256 path components, or all names past 4096 components in one command, is not
    resolved (the resolution work is bounded); a `$((` is always read as an arithmetic expansion, although bash
    re-reads it as a command substitution holding a subshell, `$( (...) )`, when no matching `))` closes it, so in
    that case a command inside the subshell (`echo $((cat f);:)`) is missed and allowed, and a substitution the
    subshell single-quotes as literal text (`echo $((echo '$(cat f)');:)`, or the backtick form) is read as though
    it ran, a false deny once; at most 16 backtick and arithmetic-expansion substitution bodies, nested at most 8
    deep, are read, a `)` inside a `case` pattern in a `$(...)` can end its span early for the backtick search, and
    brace alternatives nested more than 32 deep are left unjudged. A file that changes in any way after the recorded
    attempt (its content, its times, or a new inode) is treated as landed, even when the change came from something
    else. A write that completes without touching the file at all (for instance one whose content was already in
    place, if the tool skips such a write) reads as not landed: one deny, cleared by re-issuing the command. A
    change that lands within one tick of the file system's coarse clock while keeping the inode and the size can
    read as unchanged, the same one deny. Entries expire 900 seconds from their recording by the wall clock, so a
    slower workflow is not checked, a forward clock step past that expires them early, and a backward step keeps
    them live up to that long again. A recorder that cannot win the lock in 200 milliseconds records nothing. A
    denied command re-issued unchanged is allowed by design, and once an entry holds 32 denied commands it denies no
    more. The vendored lexer's own limits carry over: quotes, escapes, comments, and here-document bodies are
    honoured, and a construct it cannot follow yields no verdict. All state errors, lock contention past its budget,
    and unparseable input fail open with no output.

Self-test: python3 -I -S -B parallel-write-read.py --self-test
    The reference hooks the vendored blocks were copied from are looked up in the directory named by the
    G_REF_DIR environment variable, else in this file's own directory; when a reference is absent its
    byte-identity check reports SKIPPED, never a pass. The self-test's own state lives under temporary
    directories it makes and removes.
"""

import fcntl
import hashlib
import json
import os
import re
import select
import stat
import sys
import tempfile
import time

HOOK_ID = "parallel-write-read"

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
    "lexer-name-patterns": ("inplace-edit-verify.py", 420, 427,
        "ab67d6760d05dcbc39a7209f3018e7b25a4fd4be0af1f8848a66d9e0333725a3"),
    "lexer-expansions": ("inplace-edit-verify.py", 492, 531,
        "4277f6ff13bbb0ef9aa0a825e16111d3990d7d2c918a07fee017f3840cbb34a8"),
    "not-keyword": ("inplace-edit-verify.py", 534, 537,
        "2ab0ba09c28ee51710a22a1604c5fd796f7b55c33c1a216bcc065695b11a8c6c"),
    "lexer": ("inplace-edit-verify.py", 547, 849,
        "dcc75a5a9873a9c71f5970028ea4410e9a236ef487b0c193d35f0650d44b44a1"),
    "bounded": ("inplace-edit-verify.py", 980, 984,
        "ccc2a9c8d6dcaec87d96a5b01b8ed06209c962c850ec3b1984e4f9b879aa1035"),
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

# BEGIN VENDOR bounded from inplace-edit-verify.py:980-984
def _bounded(cmd):
    return cmd[:_SCAN_LIMIT].encode("utf-8", "surrogatepass")[:_SCAN_LIMIT].decode("utf-8", "ignore")
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

# This hook's own state and check.
_WRITE_TOOLS = {"Write": "file_path", "Edit": "file_path", "MultiEdit": "file_path", "NotebookEdit": "notebook_path"}
_SESSION_RE = re.compile(r"\A[A-Za-z0-9_-]{1,128}\Z")
_DIR_NAME = "parallel-write-read-%d"  # filled with the uid
_TTL = 900.0  # seconds an entry lives, either side of the wall clock (a backward step keeps it live)
_MAX_ENTRIES = 64
_MAX_STATE = 64 * 1024  # bytes of the state file; past it the oldest entries are dropped
_MAX_DENIED = 32  # command digests one entry holds; a full entry denies no new command (fails open)
_DIGEST_LEN = 32  # hex characters of a command digest kept (the first 128 bits of its sha256)
_MAX_DROPPED = 10 ** 9  # the ceiling of the dropped-entry count kept in the state file
_MAX_PATH = 4096  # characters of a recorded path
_MAX_RESOLVE = 256  # words resolved against the file system per check
_MAX_EXPAND = 64  # names one brace expansion may yield (past it the word is left unjudged)
_MAX_BACKTICKS = 16  # backtick and arithmetic-expansion substitution bodies re-read per check
_MAX_NEST = 8  # nesting of backtick bodies followed
_LOCK_WAIT = 0.2  # seconds a hook waits for the state lock (past it: fail open)
_LOCK_STEP = 0.005
_SHOW_MAX = 200  # characters of a path quoted in the reason
_DIGEST_RE = re.compile(r"\A[0-9a-f]{%d}\Z" % _DIGEST_LEN)
# The prefilter also reads the text with quotes, backslashes, and line continuations removed.
_DEQUOTE = str.maketrans("", "", "'\"\\")
_MISSING = "still missing"
_UNCHANGED = "unchanged since that call was issued (same inode, size, and modification and change times)"

# Which words are read as files: a minimal, exact set (a word not named here is never read as a file).
_DIR_CHANGE = frozenset(("cd", "pushd", "popd"))
_LEADING = frozenset(("!", "{", "}", "if", "then", "else", "elif", "do", "done", "fi", "esac", "while", "until",
                      "coproc"))
# Prefix commands that run the command after them, each with its exact option grammar (_prefix). A short cluster is
# read letter by letter: a flag letter continues, a letter with a required value takes the rest of the word or the
# next word, a letter with an optional value takes only the rest of the word. A long option with an optional value
# takes it only after `=`. A directory-change option stops the reading; an unjudged option (a query, under which
# nothing runs, or one whose effect is not modelled, such as env -S running its string as a command) or any option
# outside the grammar leaves the whole command unjudged.


def _prefix(flags="", req="", opt="", stop="", unjudged="", long_flags=(), long_req=(), long_opt=(), long_stop=(),
            long_unjudged=()):
    return {"flags": flags, "req": req, "opt": opt, "stop": stop, "unjudged": unjudged,
            "long_flags": frozenset(long_flags), "long_req": frozenset(long_req), "long_opt": frozenset(long_opt),
            "long_stop": frozenset(long_stop),
            "long_unjudged": frozenset(long_unjudged) | {"--help", "--version"}}


_PREFIXES = {
    "env": _prefix(flags="i0v", req="uaf", stop="C", unjudged="ShV",
                   long_flags=("--ignore-environment", "--null", "--debug", "--list-signal-handling"),
                   long_req=("--unset", "--argv0", "--file"), long_opt=("--ignore-signal", "--default-signal",
                                                                       "--block-signal"),
                   long_stop=("--chdir",), long_unjudged=("--split-string",)),
    "sudo": _prefix(flags="ABbEHikNnPSs", req="CgpRrTtUu", stop="D", unjudged="KlVveh",
                    long_flags=("--askpass", "--bell", "--background", "--set-home", "--login", "--reset-timestamp",
                                "--no-update", "--non-interactive", "--preserve-groups", "--stdin", "--shell"),
                    long_req=("--close-from", "--group", "--prompt", "--chroot", "--role", "--command-timeout",
                              "--type", "--other-user", "--user", "--host"),
                    long_opt=("--preserve-env",), long_stop=("--chdir",),
                    long_unjudged=("--edit", "--remove-timestamp", "--list", "--validate")),
    "xargs": _prefix(flags="0prtxo", req="adEILnPs", opt="eil",
                     long_flags=("--null", "--interactive", "--no-run-if-empty", "--verbose", "--exit", "--open-tty",
                                 "--show-limits"),
                     long_req=("--arg-file", "--delimiter", "--max-args", "--max-procs", "--max-chars",
                               "--process-slot-var"),
                     long_opt=("--eof", "--replace", "--max-lines")),
    "command": _prefix(flags="p", unjudged="vV"),
    "time": _prefix(flags="apqv", req="fo", unjudged="V", long_flags=("--append", "--portability", "--quiet",
                                                                    "--verbose"),
                    long_req=("--format", "--output")),
    "nohup": _prefix(), "builtin": _prefix(), "exec": _prefix(flags="cl", req="a"),
    "doas": _prefix(flags="ns", req="u", unjudged="CL"),
    "nice": _prefix(flags="0123456789", req="n", long_req=("--adjustment",)),
    "timeout": _prefix(flags="vfp", req="sk", unjudged="hV",
                       long_flags=("--foreground", "--preserve-status", "--verbose"),
                       long_req=("--signal", "--kill-after")),
    "stdbuf": _prefix(req="ioe", long_req=("--input", "--output", "--error")),
    "ionice": _prefix(flags="t", req="cn", unjudged="pPu", long_flags=("--ignore",),
                      long_req=("--class", "--classdata"), long_unjudged=("--pid", "--pgid", "--uid")),
}
# Options whose value is read, on any command but the printing ones: (option, the only command it applies to).
_READ_OPTS = {"--body-file": None, "--notes-file": None, "--env-file": None, "--arg-file": None, "-F": "gh"}
_JQ_PAIRS = frozenset(("--rawfile", "--slurpfile"))  # jq: NAME then FILE, the file read
_PRINTERS = frozenset(("echo", "printf", ":", "true"))  # their words are printed, never read
# Commands whose first operand is read, only when no option word comes before it.
_FIRST_READERS = frozenset(("cat", "bash", "sh", "zsh", "python", "python3", "node", "ruby", "perl", "source", "."))
_FD_WORD_RE = re.compile(r"\A(?:[0-9]+|\{[A-Za-z_][A-Za-z0-9_]*\})\Z")
_ENV_ASSIGN_RE = re.compile(r"\A[A-Za-z_][A-Za-z0-9_]*=")
_SEPARATORS = frozenset((";", "&", "&&", "||", "|", "|&", ";;", ";&", ";;&", "(", ")"))
_MAX_BRACE_NEST = 32  # nesting of brace alternatives followed (past it the word is left unjudged)
_MAX_SEGMENTS = 256  # path components one name may have to be resolved (past it the name is left unjudged)
_SEGMENT_BUDGET = 4096  # path components resolved per check, all names together (past it the rest is unjudged)
_STOP = object()  # a directory change: nothing after it is read

_REASON = (
    "Blocked (parallel-write-read): this command reads {path}, but the file-tool call ({tool}) targeting that path, "
    "issued {age}s ago, has not landed: the file is {state}. The write was probably blocked, failed, or is still "
    "pending in the same tool batch. Check the write's result, re-issue it if needed, and run this command in a "
    "later turn. If the current file content is what you intend, re-issue this exact command unchanged and it will "
    "be allowed.")


class _Unknown(Exception):
    """A path whose state cannot be read (a permission error, a loop): neither recorded nor compared."""


def _is_worker(env):
    """True for a verification worker process: AIQT_HOOKS_WORKER == "1" first, then the legacy names,
    ORCH_WORKER == "1" or ORCH_VERIFY_OWNER present (any value, even empty)."""
    if env.get("AIQT_HOOKS_WORKER") == "1":
        return True
    return env.get("ORCH_WORKER") == "1" or "ORCH_VERIFY_OWNER" in env


def _uid():
    return os.getuid()


def _private(st, want):
    """True when st, a stat result, is of type want (a stat.S_IS* test), owned by this user, with no group or
    other permission bits."""
    return want(st.st_mode) and st.st_uid == _uid() and not stat.S_IMODE(st.st_mode) & 0o077


def _state_base(env):
    """The directory the per-user state directory sits in: XDG_RUNTIME_DIR when it is an absolute path to a real
    directory owned by this user with mode 0700, else the system temporary directory."""
    xdg = env.get("XDG_RUNTIME_DIR")
    if isinstance(xdg, str) and os.path.isabs(xdg) and "\x00" not in xdg:
        try:
            st = os.lstat(xdg)
        except OSError:
            st = None
        if st is not None and _private(st, stat.S_ISDIR) and stat.S_IMODE(st.st_mode) == 0o700:
            return xdg
    return tempfile.gettempdir()


def _open_dir(base, create):
    """A descriptor of the verified per-user state directory under base, or None (absent and not created, or not a
    directory of this user's with no group or other bits: used for nothing)."""
    path = os.path.join(base, _DIR_NAME % _uid())
    if create:
        try:
            os.mkdir(path, 0o700)
        except FileExistsError:
            pass
    try:
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError:
        return None  # absent, or a symbolic link (ELOOP), or not a directory
    if _private(os.fstat(fd), stat.S_ISDIR):
        return fd
    os.close(fd)
    return None


def _open_state(dfd, session, create):
    """A descriptor of the session's verified state file inside the directory dfd, or None."""
    flags = os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK | (os.O_CREAT if create else 0)
    try:
        fd = os.open(session + ".json", flags, 0o600, dir_fd=dfd)
    except OSError:
        return None
    st = os.fstat(fd)
    if _private(st, stat.S_ISREG) and st.st_nlink == 1:
        return fd
    os.close(fd)
    return None


def _lock(fd):
    """Take the exclusive lock on fd within _LOCK_WAIT seconds: True when held, False when the wait ran out."""
    end = time.monotonic() + _LOCK_WAIT
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            left = end - time.monotonic()
            if left <= 0:
                return False
            time.sleep(min(_LOCK_STEP, left))


def _valid_entry(e):
    """True for a well-formed entry. Every field's type is checked before it is used, so no malformed value can
    raise here."""
    if not isinstance(e, dict) or set(e) != {"path", "named", "state", "tool", "t", "denied"}:
        return False
    path, named, s, tool, t, d = e["path"], e["named"], e["state"], e["tool"], e["t"], e["denied"]
    return (isinstance(path, str) and os.path.isabs(path) and "\x00" not in path and len(path) <= _MAX_PATH
            and isinstance(named, str) and os.path.isabs(named) and "\x00" not in named
            and len(named) <= _MAX_PATH and isinstance(tool, str) and tool in _WRITE_TOOLS
            and (s is None or isinstance(s, list) and len(s) == 5 and all(type(x) is int for x in s))
            and type(t) in (int, float) and t == t and abs(t) < 1e12 and isinstance(d, list)
            and len(d) <= _MAX_DENIED and all(isinstance(x, str) and _DIGEST_RE.match(x) for x in d))


def _read_entries(fd):
    """(entries, dropped) of the locked state file fd: ([], 0) when it is empty; None when it is over its cap or is
    not a state document at all. An individual malformed entry is dropped and counted in dropped (kept in the file
    as a running count), so one bad entry never leaves the state stuck."""
    data = os.pread(fd, _MAX_STATE + 1, 0)
    if not data:
        return [], 0
    if len(data) > _MAX_STATE:
        return None
    try:
        doc = json.loads(data)
    except (ValueError, RecursionError):
        return None
    if not isinstance(doc, dict) or doc.get("v") != 1 or not isinstance(doc.get("entries"), list):
        return None
    dropped = doc.get("dropped", 0)
    if type(dropped) is not int or not 0 <= dropped <= _MAX_DROPPED:
        dropped = 0
    entries = [e for e in doc["entries"] if _valid_entry(e)]
    dropped = min(_MAX_DROPPED, dropped + len(doc["entries"]) - len(entries))
    return entries[-_MAX_ENTRIES:], dropped


def _write_entries(fd, entries, dropped):
    """Rewrite the locked state file fd in place with entries, the oldest dropped until it fits _MAX_STATE."""
    entries = entries[-_MAX_ENTRIES:]
    while True:
        doc = {"v": 1, "entries": entries, "dropped": dropped}
        data = json.dumps(doc, separators=(",", ":")).encode("ascii")
        if len(data) <= _MAX_STATE or not entries:
            break
        entries = entries[1:]
    view, off = memoryview(data), 0
    while off < len(data):
        off += os.pwrite(fd, view[off:], off)
    os.ftruncate(fd, len(data))


def _observe(path):
    """The state of path now: None when it is missing, else [device, inode, size, mtime_ns, ctime_ns] from lstat.
    Raises _Unknown when that cannot be read."""
    try:
        st = os.lstat(path)
    except (FileNotFoundError, NotADirectoryError):
        return None
    except (OSError, ValueError):
        raise _Unknown(path)
    return [st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns]


def _live(entries, now):
    """The entries within _TTL of now on either side: the wall clock is read by separate processes, so a backward
    step leaves a recent entry live rather than dropping it."""
    return [e for e in entries if abs(now - e["t"]) <= _TTL]


def _with_state(base, session, create, action):
    """Run action(fd, read) on the session's state file, locked, where read is _read_entries' result; its result,
    or None when the file cannot be used (absent and not created, unverified, the lock not won in time)."""
    dfd = _open_dir(base, create)
    if dfd is None:
        return None
    try:
        fd = _open_state(dfd, session, create)
        if fd is None:
            return None
        try:
            if not _lock(fd):
                return None
            return action(fd, _read_entries(fd))
        finally:
            os.close(fd)  # closing the descriptor releases the lock
    finally:
        os.close(dfd)


def _absolute(value, cwd):
    """value made absolute against cwd (None when it is relative and cwd is not a usable absolute path)."""
    if os.path.isabs(value):
        return value
    if not isinstance(cwd, str) or not os.path.isabs(cwd) or "\x00" in cwd:
        return None
    return os.path.join(cwd, value)


def _record(payload, tool, base, now):
    """The recorder: note the target path's state before the write attempt. Prints nothing."""
    session, tool_input = payload.get("session_id"), payload.get("tool_input")
    if not isinstance(session, str) or not _SESSION_RE.match(session) or not isinstance(tool_input, dict):
        return
    raw = tool_input.get(_WRITE_TOOLS[tool])
    if not isinstance(raw, str) or not raw or "\x00" in raw or len(raw) > _MAX_PATH:
        return
    named = _absolute(raw, payload.get("cwd"))
    if named is None:
        return
    if named.count("/") > _MAX_SEGMENTS:
        return  # an oversized path: never resolved
    path = os.path.realpath(named)
    if len(path) > _MAX_PATH or len(named) > _MAX_PATH:
        return
    try:
        state = _observe(path)
    except _Unknown:
        return
    entry = {"path": path, "named": named, "state": state, "tool": tool, "t": now, "denied": []}

    def action(fd, read):
        entries, dropped = read or ([], 0)  # not a state document: rewritten from scratch
        kept = [e for e in _live(entries, now) if e["path"] != path]
        _write_entries(fd, kept[-(_MAX_ENTRIES - 1):] + [entry], dropped)

    _with_state(base, session, True, action)


def _span_end(raw, i):
    """The index just past the `)` that matches the `(` at raw[i], quotes and escapes inside honoured, or None when
    it is unterminated. An unquoted `#` at the start of a word (after a blank, a newline, or an operator
    character other than the `)` closing a substitution, which continues its word: `$(...)#x` is one word) opens
    a comment to the end of the line, as bash reads one, so nothing inside it counts. A backslash-newline is
    deleted before words are split, as the lexer does, so it neither starts nor ends a word."""
    n, quote, word = len(raw), None, False  # word: the character before raw[i] continues a word
    opened, lead = [], False  # opened: per open `(`, a substitution or not; lead: raw[i-1] is an unquoted `$<>`
    while i < n:
        c = raw[i]
        if c == "\\" and quote != "'":
            word = word or not raw.startswith("\n", i + 1)
            i, lead = i + 2, False
            continue
        if quote:
            quote = None if c == quote else quote
        elif c in "'\"":
            quote = c
        elif c == "#" and not word:
            j = raw.find("\n", i + 1)
            if j < 0:
                return None
            i, word, lead = j + 1, False, False
            continue
        elif c == "(":
            opened.append(lead)
        elif c == ")":
            if len(opened) == 1:
                return i + 1
            if opened.pop():  # a substitution's `)` continues its word
                i, word, lead = i + 1, True, False
                continue
        word = quote is not None or c not in " \t\n;&|<>()"
        lead = quote is None and c in "$<>"
        i += 1
    return None


def _backticks(raw, arith=False):
    """The command bodies in raw, a word's source text, that the lexer keeps opaque: its backtick substitutions (bash's
    backslash escapes inside them undone), and the `$(...)` and backtick substitutions inside an arithmetic
    expansion `$((...))`. Single-quoted text (outside an arithmetic expansion, where bash does not treat a single
    quote as quoting), escaped characters, and other `$(...)` spans (read as their own streams) are skipped; arith is
    set while reading an arithmetic expansion's own text."""
    bodies, i, n, dq = [], 0, len(raw), False
    while i < n:
        c = raw[i]
        if c == "\\":
            i += 2
        elif c == "'" and not dq and not arith:  # inside $((...)) a single quote does not hide a substitution
            j = raw.find("'", i + 1)
            i = n if j < 0 else j + 1
        elif c == '"':
            dq, i = not dq, i + 1
        elif raw.startswith("$(", i):
            if arith and raw.startswith("$((", i):
                i += 3  # a nested arithmetic expansion reads by these same rules, so it is read in place;
                continue  # matching its span here would rescan the text after it once per nesting level
            end = _span_end(raw, i + 1)
            if end is None:
                return bodies  # unterminated: the lexer's own reading governs
            if raw.startswith("$((", i):
                bodies.extend(_backticks(raw[i + 3:end - 2 if raw[end - 2] == ")" else end - 1], True))
            elif arith:
                bodies.append(raw[i + 2:end - 1])
            i = end
        elif c == "`":
            j, body = i + 1, []
            while j < n and raw[j] != "`":
                if raw[j] == "\\" and j + 1 < n and raw[j + 1] in "`$\\":
                    j += 1
                body.append(raw[j])
                j += 1
            if j >= n:
                return bodies  # unterminated: the lexer's own reading governs
            bodies.append("".join(body))
            i = j + 1
        else:
            i += 1
    return bodies


def _refs(toks, text, out, budget, nest=0):
    """Append to out the character lists (see _pairs) of the words that the simple commands of one token stream read
    as files, in the order the shell reaches them; return _STOP once a directory change is reached (nothing after
    it counts). text is the source the tokens' offsets index, or None when they index some other text (a
    here-document body), in which case quoting is read from the lexer's flag and backtick bodies are not re-read.
    budget is a one-item list counting backtick bodies left."""
    cmd, i, n, cond, arith = [], 0, len(toks), False, 0
    at_start, timed = True, False  # every item so far a bare reserved word, or `time` and its options
    while i <= n:
        t = toks[i] if i < n else None
        if t is not None and (cond or arith):  # inside [[ ]] or (( )): operands only, their substitutions run
            if t[0] == "w" and _subs(t, text, out, budget, nest) is _STOP:
                return _STOP
            if cond and t[0] == "w" and not t[2] and t[1] == "]]":
                cond = False
            elif arith and t[0] == "op" and t[1] in "()":
                arith += 1 if t[1] == "(" else -1
            i += 1
            continue
        if t is not None and t[0] == "w" and not t[2] and t[1] == "[[" and at_start:
            cond, i = True, i + 1
            continue
        if (t is not None and _is_arith_open(toks, i)
                and (at_start or len(cmd) == 1 and cmd[0][0] == "w" and cmd[0][1][1] == "for")):
            arith, i = 2, i + 2
            continue
        if t is None or t[0] == "nl" or t[0] == "op" and t[1] in _SEPARATORS:
            if cmd and _command(cmd, text, out, budget, nest) is _STOP:
                return _STOP
            cmd, at_start, timed = [], True, False
        elif t[0] == "op" and t[1] in _REDIRECTS:
            target = toks[i + 1] if i + 1 < n and toks[i + 1][0] == "w" else None
            cmd.append(("redir", t[1], target))
            at_start = timed = False
            i += 1 if target is not None else 0
        elif t[0] == "w":
            bare = not t[2]
            timed = bare and (t[1] == "time" or timed and t[1] in ("-p", "--"))
            at_start = at_start and bare and (t[1] in _LEADING or timed)
            nxt = toks[i + 1] if i + 1 < n else None
            if (nxt is not None and nxt[0] == "op" and nxt[1] == "(" and nxt[3] == t[4] and not t[2]
                    and _ARRAY_ASSIGN_RE.match(t[1])):
                cmd.append(("w", t))  # an array assignment: its elements are data, only their substitutions run
                depth, i = 1, i + 2
                while i < n and depth:
                    u = toks[i]
                    depth += {"(": 1, ")": -1}.get(u[1], 0) if u[0] == "op" else 0
                    if u[0] == "w" and _subs(u, text, out, budget, nest) is _STOP:
                        return _STOP
                    i += 1
                continue
            fd = (nxt is not None and nxt[0] == "op" and nxt[1] in _REDIRECTS and nxt[3] == t[4] and not t[2]
                  and _FD_WORD_RE.match(t[1]))
            cmd.append(("fd" if fd else "w", t))
        i += 1
    return None


def _is_arith_open(toks, i):
    """True when toks[i] starts `((` (two adjacent `(` operators): an arithmetic command, not two subshells."""
    return (i + 1 < len(toks) and toks[i][0] == "op" and toks[i][1] == "(" and toks[i + 1][0] == "op"
            and toks[i + 1][1] == "(" and toks[i + 1][3] == toks[i][4])


def _subs(tok, text, out, budget, nest, absolute=True):
    """Walk the substitution streams inside one word (its `$(...)`, `<(...)`, `>(...)` streams, then its backtick
    bodies); _STOP when one reaches a directory change."""
    for stream in tok[5]:
        if _refs(stream, text if absolute else None, out, budget, nest) is _STOP:
            return _STOP
    if text is None or not absolute or _SUB not in tok[1] or nest >= _MAX_NEST:
        return None
    raw = text[tok[3]:tok[4]]
    if "`" not in raw and "$((" not in raw:
        return None
    for body in _backticks(raw):
        if budget[0] <= 0:
            return None
        budget[0] -= 1
        try:
            inner = _tokenize(body)
        except ValueError:
            continue
        if _refs(inner, body, out, budget, nest + 1) is _STOP:
            return _STOP
    return None


def _pairs(tok, text):
    """The characters of a word as (character, unquoted) pairs, or None when its text cannot be known (an
    expansion, a substitution) or its quoting cannot be read. The quoting is read from the word's source text
    when text indexes it (the pairs must spell the lexer's own value, or None); otherwise from the lexer's flag,
    where a partly quoted word holding a brace, tilde, or glob character is left unjudged."""
    value = tok[1]
    if "$" in value or _SUB in value or "`" in value:
        return None
    if text is None:
        if not tok[2]:
            return [(c, True) for c in value]
        return None if any(c in value for c in "{~*?[") else [(c, False) for c in value]
    raw, out, i = text[tok[3]:tok[4]], [], 0
    n = len(raw)
    while i < n:
        c = raw[i]
        if c == "\\":
            if i + 1 >= n:
                return None
            if raw[i + 1] != "\n":
                out.append((raw[i + 1], False))
            i += 2
        elif c == "'":
            j = raw.find("'", i + 1)
            if j < 0:
                return None
            out.extend((x, False) for x in raw[i + 1:j])
            i = j + 1
        elif c == '"':
            i += 1
            while i < n and raw[i] != '"':
                if raw[i] == "\\" and i + 1 < n and raw[i + 1] in '"\\$`\n':
                    if raw[i + 1] != "\n":
                        out.append((raw[i + 1], False))
                    i += 2
                else:
                    out.append((raw[i], False))
                    i += 1
            if i >= n:
                return None
            i += 1
        else:
            out.append((c, True))
            i += 1
    return out if "".join(c for c, _ in out) == value else None


def _value_of(item, text, k):
    """The pairs of a word's characters from index k on (an attached option value), or None."""
    p = _pairs(item, text)
    return None if p is None else p[k:]


def _read(out, pairs):
    """Record pairs as a word read as a file."""
    if pairs:
        out.append(pairs)


def _prefix_options(name, words, k, text, out, arity=0):
    """Step past the options of the prefix command name, starting at words[k], by its exact grammar (_PREFIXES):
    the index of the word after them, _STOP at a directory change, or None when the command is left unjudged (an
    unjudged option, or any option outside the grammar). `--` ends the options; env's assignments may follow its
    options or a `--`. arity counts the operands standing before the command (timeout's duration): the uutils
    timeout this was checked against reads options on both sides of the duration, so its command starts at the
    second word that is no option. xargs's own `-a FILE` (standing alone) and `--arg-file` values are appended to
    out, which the caller holds until the prefix's options are all accepted."""
    g, ended = _PREFIXES[name], False
    while k < len(words):
        w = words[k][1]
        if name == "env" and _ENV_ASSIGN_RE.match(w):
            k, ended = k + 1, True  # an environment assignment: no option follows it
            continue
        if ended or not w.startswith("-") or w == "-":
            if name == "env" and w == "-" and not ended:
                k += 1  # the same as -i
                continue
            if not arity:
                break
            arity, k = arity - 1, k + 1  # an operand before the command (timeout's duration): options go on
            continue
        if w == "--":
            k, ended = k + 1, True
            continue
        if w.startswith("--"):
            opt, eq, _ = w.partition("=")
            if opt in g["long_stop"]:
                return _STOP
            if opt in g["long_unjudged"]:
                return None
            if opt in g["long_flags"] and not eq or opt in g["long_opt"]:
                k += 1
            elif opt in g["long_req"]:
                if name == "xargs" and opt == "--arg-file":
                    _read(out, _value_of(words[k], text, len(opt) + 1) if eq else
                          _pairs(words[k + 1], text) if k + 1 < len(words) else None)
                k += 1 if eq else 2
            else:
                return None  # not in the grammar: unjudged
            continue
        k += 1
        for pos in range(1, len(w)):
            ch = w[pos]
            if ch in g["stop"]:
                return _STOP  # a directory change, separate or attached (-C DIR, -CDIR, -iCDIR)
            if ch in g["unjudged"]:
                return None
            if ch in g["flags"]:
                continue
            if ch in g["opt"]:
                break  # an optional value: only the rest of this word
            if ch in g["req"]:
                if name == "xargs" and w == "-a" and k < len(words):
                    _read(out, _pairs(words[k], text))
                if pos + 1 >= len(w):
                    k += 1  # the value is the next word
                break
            return None  # not in the grammar: unjudged
    return k


def _command(cmd, text, out, budget, nest):
    """Read one simple command's items (see _refs): the substitutions anywhere in it, an input redirection's
    target, and the few words the minimal set reads as files."""
    words = []
    for item in cmd:
        if item[0] == "redir":
            op, target = item[1], item[2]
            if target is None:
                continue
            here = op in _HEREDOC_OPS  # a here-document's delimiter word carries its body's substitutions
            if _subs(target, text, out, budget, nest, absolute=not here) is _STOP:
                return _STOP
            if op == "<":
                _read(out, _pairs(target, text))
        else:
            if _subs(item[1], text, out, budget, nest) is _STOP:
                return _STOP
            if item[0] == "w":
                words.append(item[1])
    k, assigned = 0, False  # assigned: an assignment came first, so a later `time` is no reserved word
    while k < len(words):
        if words[k][1] in _LEADING and not words[k][2] or _ASSIGN_RE.match(words[k][1]):
            assigned = assigned or bool(_ASSIGN_RE.match(words[k][1]))
            k += 1
            continue
        if words[k][1] == "time" and not words[k][2] and not assigned:  # bash's time keyword: before a reserved
            j = k + 1  # word or `!`, it (with `-p`, `--`, or `-p --`) times that compound or negated pipeline
            for opt in ("-p", "--"):
                if j < len(words) and not words[j][2] and words[j][1] == opt:
                    j += 1
            if j < len(words) and not words[j][2] and words[j][1] in _LEADING:
                k = j
                continue
        break
    held = []  # the files the prefixes' own options read, kept only once every prefix option is accepted
    for _ in range(8):  # prefix commands, each with its options and values, before the real command word
        if k >= len(words):
            out.extend(held)
            return None
        name = words[k][1].rsplit("/", 1)[-1]
        if name not in _PREFIXES:
            break
        k = _prefix_options(name, words, k + 1, text, held, 1 if name == "timeout" else 0)
        if k is None:
            return None  # unjudged: what its options would have read is dropped with it
        if k is _STOP:
            out.extend(held)
            return _STOP
    else:
        return None
    out.extend(held)
    if k >= len(words):
        return None
    name = words[k][1].rsplit("/", 1)[-1]
    if name in _DIR_CHANGE:
        return _STOP
    if name in _PRINTERS:
        return None
    args = words[k + 1:]
    _read_options(name, args, text, out)
    if name in _FIRST_READERS and args and not args[0][1].startswith(("-", "+")):
        _read(out, _pairs(args[0], text))  # the first operand, with no option word before it
    return None


def _read_options(name, args, text, out):
    """The values of the file-reading options (_READ_OPTS, and jq's --rawfile and --slurpfile), separate, attached
    with `=`, or (for a short option) attached directly; `--` ends the options."""
    j = 0
    while j < len(args):
        w = args[j][1]
        j += 1
        if w == "--":
            return
        opt, eq, _ = w.partition("=") if w.startswith("--") else (w[:2], "", "")
        if name == "jq" and opt in _JQ_PAIRS:
            k = j if eq else j + 1  # --rawfile NAME FILE (or --rawfile=NAME FILE)
            if k < len(args):
                _read(out, _pairs(args[k], text))
            j = k + 1
            continue
        if opt not in _READ_OPTS or _READ_OPTS[opt] not in (None, name):
            continue
        if eq:
            _read(out, _value_of(args[j - 1], text, len(opt) + 1))
        elif w == opt:
            if j < len(args):
                _read(out, _pairs(args[j], text))
                j += 1
        elif not w.startswith("--"):
            _read(out, _value_of(args[j - 1], text, 3 if w[2] == "=" else 2))  # attached: -Fbody.md, -F=body.md


def _expand(pairs):
    """The names a word's pairs make after tilde and brace expansion (comma lists, nested), or None when the word
    is left unjudged: an unquoted glob character, a quoted tilde prefix, more than _MAX_EXPAND names, or brace
    alternatives nested deeper than _MAX_BRACE_NEST. Linear in the word: one pass matches the braces, one builds
    the alternatives, and the expansion stops at the name bound."""
    if any(s and c in "*?[" for c, s in pairs):
        return None
    if pairs and pairs[0] == ("~", True):
        k = next((i for i, (c, _) in enumerate(pairs) if c == "/"), len(pairs))
        if not all(s for _, s in pairs[:k]):
            return None
        home = os.path.expanduser("".join(c for c, _ in pairs[:k]))
        if home.startswith("~"):
            return None
        pairs = [(c, False) for c in home] + pairs[k:]
    stack, close, owner, has_comma = [], {}, {}, set()
    for i, (c, s) in enumerate(pairs):
        if not s:
            continue
        if c == "{":
            stack.append(i)
        elif c == "}" and stack:
            close[stack.pop()] = i
        elif c == "," and stack:
            owner[i] = stack[-1]
            has_comma.add(stack[-1])
    opens = {o for o in close if o in has_comma}
    if not opens:
        return ["".join(c for c, _ in pairs)]
    closes = {close[o] for o in opens}
    commas = {i for i, o in owner.items() if o in opens}
    frames, buf = [[[]]], []  # frames: a stack of alternative lists; each alternative: a list of parts

    def flush():
        if buf:
            frames[-1][-1].append("".join(buf))
            del buf[:]

    for i, (c, _) in enumerate(pairs):
        if i in opens:
            flush()
            frames.append([[]])
            if len(frames) > _MAX_BRACE_NEST + 1:
                return None
        elif i in closes:
            flush()
            alts = frames.pop()
            frames[-1][-1].append(alts)
        elif i in commas:
            flush()
            frames[-1].append([])
        else:
            buf.append(c)
    flush()

    def names(parts):
        found = [""]
        for part in parts:
            if isinstance(part, str):
                found = [f + part for f in found]
                continue
            options = []
            for alt in part:
                more = names(alt)
                if more is None or len(options) + len(more) > _MAX_EXPAND:
                    return None
                options.extend(more)
            if len(found) * len(options) > _MAX_EXPAND:
                return None
            found = [f + o for f in found for o in options]
        return found

    return names(frames[0][0])


def _named(refs, cwd, pending, names):
    """The pending paths (realpath -> entry) that the read words refs name, in the order first named. A name is
    resolved only when its file name is one an entry was recorded under (its resolved or its written name)."""
    found, resolved, segments = [], 0, _SEGMENT_BUDGET
    for pairs in refs:
        for v in _expand(pairs) or ():
            if not v or "\x00" in v or os.path.basename(v) not in names:
                continue
            v = _absolute(v, cwd)
            if v is None or len(v) > _MAX_PATH or v.count("/") > _MAX_SEGMENTS:
                continue  # an oversized path: left unjudged, never resolved
            if resolved >= _MAX_RESOLVE or segments < v.count("/"):
                return found
            resolved, segments = resolved + 1, segments - v.count("/")
            rp = os.path.realpath(v)
            if rp in pending and rp not in found:
                found.append(rp)
    return found


def _check(payload, base, now):
    """The checker: (path as the write named it, tool, age in seconds, state wording) of the unlanded write to
    deny on, or None."""
    session, tool_input = payload.get("session_id"), payload.get("tool_input")
    if not isinstance(session, str) or not _SESSION_RE.match(session) or not isinstance(tool_input, dict):
        return None
    cmd = tool_input.get("command")
    if not isinstance(cmd, str) or not cmd:
        return None
    if len(cmd) > _SCAN_LIMIT or len(cmd.encode("utf-8", "surrogatepass")) > _SCAN_LIMIT:
        return None  # not judged at all
    cwd = payload.get("cwd")

    def action(fd, read):
        if not read or not read[0]:
            return None  # no entries, or not a state document: allow
        entries, dropped = read
        live = _live(entries, now)
        pending = {e["path"]: e for e in live}
        names = {n for p, e in pending.items() for n in (os.path.basename(p), os.path.basename(e["named"]))} - {""}
        joined = cmd.replace("\\\n", "")
        probes = (cmd, joined, joined.translate(_DEQUOTE))  # a name split by a continuation or by quoting
        if not any(nm in t for nm in names for t in probes) and not ("{" in cmd and "," in cmd):
            return None  # no recorded name in the text (a brace form is read in full: it can build one)
        try:
            toks = _tokenize(_bounded(cmd))
        except ValueError:
            return None  # unparseable, or past the work bound: fail open
        text, refs = _bounded(cmd), []
        _refs(toks, text, refs, [_MAX_BACKTICKS])
        digest = hashlib.sha256(cmd.encode("utf-8", "surrogatepass")).hexdigest()[:_DIGEST_LEN]
        fresh = []
        for path in _named(refs, cwd, pending, names):
            e = pending[path]
            try:
                if _observe(path) != e["state"]:
                    continue  # the write landed, or something else changed the file
            except _Unknown:
                continue
            if digest not in e["denied"] and len(e["denied"]) < _MAX_DENIED:
                fresh.append(e)  # a full entry denies nothing new: its earlier denials are never forgotten
        if not fresh:
            return None
        for e in fresh:
            e["denied"] = e["denied"] + [digest]
        _write_entries(fd, live, dropped)  # deny-once is recorded before the deny is given; a failed write fails open
        e = fresh[0]
        return e["named"], e["tool"], max(0, int(now - e["t"])), _MISSING if e["state"] is None else _UNCHANGED

    return _with_state(base, session, False, action)


def _show(value):
    return value if len(value) <= _SHOW_MAX else value[:_SHOW_MAX] + "..."


def _decide(payload, env, base=None, clock=time.time):
    """The deny object for a parsed payload, or None to allow. A file-tool payload is recorded (always None). base
    is the directory the state directory sits in (default: _state_base(env)); clock gives the wall time."""
    if _is_worker(env) or not isinstance(payload, dict) or "agent_id" in payload:
        return None
    if payload.get("hook_event_name", "PreToolUse") != "PreToolUse":
        return None
    tool = payload.get("tool_name")
    if not isinstance(tool, str) or tool != "Bash" and tool not in _WRITE_TOOLS:
        return None
    if base is None:
        base = _state_base(env)
    if tool != "Bash":
        _record(payload, tool, base, clock())
        return None
    found = _check(payload, base, clock())
    if found is None:
        return None
    path, tool, age, state = found
    reason = _REASON.format(path=_show(path), tool=tool, age=age, state=state)
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                   "permissionDecisionReason": reason}}


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
        return 0  # malformed or unreadable input, a state error, or an internal error: fail open
    if out is not None:
        _emit_line(json.dumps(out))
    return 0


def _self_test():
    import io
    import shutil
    import subprocess
    import threading
    import unittest

    here = os.path.abspath(__file__)
    g = globals()
    real_uid, own_uid = os.getuid(), g["_uid"]
    old_umask = os.umask(0o022)  # pinned: the modes the state checks read must not follow the caller's umask
    root = tempfile.mkdtemp(prefix="parallel-write-read-test-")
    counter = [0]
    hang_cap = 120  # seconds: a hang ceiling far above any run's cost, never a speed assertion
    real_time, real_sleep = time, time.sleep  # captured before any test swaps the module global

    class FakeTime:
        """A stand-in for the time module in _lock: a clock that moves only by the sleeps asked of it, so a wait
        is measured in steps, not in host seconds. on_sleep(n) runs after the n-th sleep."""
        def __init__(self, on_sleep=None, advance=True):
            self.t, self.sleeps, self.on_sleep, self.advance = 0.0, 0, on_sleep, advance

        def monotonic(self):
            return self.t

        def sleep(self, seconds):
            self.sleeps += 1
            if self.sleeps > 100000:
                raise AssertionError("the lock wait did not end")
            if self.advance:
                self.t += seconds
            else:
                real_sleep(0.001)
            if self.on_sleep:
                self.on_sleep(self.sleeps)

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

    class T(unittest.TestCase):
        def setUp(self):
            counter[0] += 1
            self.dir = tempfile.mkdtemp(dir=root)
            self.base = os.path.join(self.dir, "base")
            self.work = os.path.join(self.dir, "work")
            os.mkdir(self.base, 0o700)
            os.mkdir(self.work, 0o755)
            self.sid = "sess-%d" % counter[0]
            self.now = [time.time()]  # the hook processes read the real clock
            self.body = os.path.join(self.work, "body.md")

        def tearDown(self):
            g["_uid"] = own_uid
            shutil.rmtree(self.dir)

        def clock(self):
            return self.now[0]

        def state_path(self):
            return os.path.join(self.base, _DIR_NAME % real_uid, self.sid + ".json")

        def entries(self):
            with open(self.state_path(), "rb") as f:
                return json.loads(f.read())["entries"]

        def attempt(self, path, tool="Write", env=None, **extra):
            payload = {"hook_event_name": "PreToolUse", "tool_name": tool, "session_id": self.sid, "cwd": self.work,
                       "tool_input": {_WRITE_TOOLS.get(tool, "file_path"): path}}
            payload.update(extra)
            self.assertIsNone(_decide(payload, {} if env is None else env, self.base, self.clock))

        def decide(self, command, env=None, **extra):
            payload = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "session_id": self.sid,
                       "cwd": self.work, "tool_input": {"command": command}}
            payload.update(extra)
            return _decide(payload, {} if env is None else env, self.base, self.clock)

        def assertDeny(self, command, **kw):
            out = self.decide(command, **kw)
            self.assertIsNotNone(out, command)
            h = out["hookSpecificOutput"]
            self.assertEqual((h["hookEventName"], h["permissionDecision"]), ("PreToolUse", "deny"))
            self.assertIn("Blocked (parallel-write-read)", h["permissionDecisionReason"])
            return h["permissionDecisionReason"]

        def assertAllow(self, command, **kw):
            self.assertIsNone(self.decide(command, **kw), command)

        def make(self, path, data=b"old content\n"):
            with open(path, "wb") as f:
                f.write(data)

        def hook_env(self, **extra):
            env = {"LC_ALL": "C", "TMPDIR": self.base}
            env.update(extra)
            return env

        def run_hook(self, payload, env=None, timeout=hang_cap):
            data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
            p = subprocess.run([sys.executable, "-I", "-S", "-B", here], input=data, capture_output=True,
                               env=self.hook_env() if env is None else env, timeout=timeout)
            return p.returncode, p.stdout, p.stderr

        def bash_payload(self, command):
            return {"hook_event_name": "PreToolUse", "tool_name": "Bash", "session_id": self.sid, "cwd": self.work,
                    "tool_input": {"command": command}}

        def write_payload(self, path):
            return {"hook_event_name": "PreToolUse", "tool_name": "Write", "session_id": self.sid, "cwd": self.work,
                    "tool_input": {"file_path": path, "content": "x"}}

        # plan cases 1, 2, 7, 8, and 18: the flip cases (they pass only with detection present)
        def test_01_flip_missing(self):
            self.attempt(self.body)
            self.now[0] += 3
            reason = self.assertDeny("gh pr create --title t --body-file body.md")
            for part in (self.body, "still missing", "(Write)", "issued 3s ago"):
                self.assertIn(part, reason)

        def test_02_flip_unchanged(self):
            self.make(self.body)
            self.attempt(self.body, tool="Edit")
            reason = self.assertDeny("cat " + self.body)
            self.assertIn("unchanged since that call was issued", reason)
            self.assertIn("(Edit)", reason)

        def test_07_flip_option_forms(self):
            self.attempt(self.body)
            for c in ("gh pr create --body-file=body.md", "gh pr create -F body.md", "wc -l < body.md",
                      "cat ./body.md", "cat ../work/body.md", "cat " + self.body, 'cat "body.md"',
                      "cat 'bo'dy.md", "cat \\\nbody.md", "cmd --body-file=" + self.body):
                self.assertDeny(c)
            old = os.environ.get("HOME")
            os.environ["HOME"] = self.work
            try:
                self.assertDeny("cat ~/body.md")
            finally:
                if old is None:
                    del os.environ["HOME"]
                else:
                    os.environ["HOME"] = old

        def test_08_flip_substitution(self):
            self.attempt(self.body)
            for c in ("x=$(cat body.md); echo done", "diff <(cat body.md) other", "echo $(echo $(cat body.md))",
                      'echo "$(cat body.md)"'):
                self.assertDeny(c)

        def test_18_flip_end_to_end_processes(self):
            # One batch as the harness runs it: the producer's recorder, the producer blocked (never runs), then
            # the consumer's check, each a separate hook process.
            self.assertEqual(self.run_hook(self.write_payload(self.body)), (0, b"", b""))
            rc, out, err = self.run_hook(self.bash_payload("gh pr create --body-file body.md"))
            self.assertEqual((rc, err), (0, b""))
            self.assertTrue(out.endswith(b"\n") and out.count(b"\n") == 1, out)
            out.decode("ascii")
            h = json.loads(out)["hookSpecificOutput"]
            self.assertEqual(h["permissionDecision"], "deny")
            self.assertIn("still missing", h["permissionDecisionReason"])
            self.assertEqual(self.run_hook(self.bash_payload("gh pr create --body-file body.md")), (0, b"", b""))
            self.make(self.body)  # the write lands
            self.assertEqual(self.run_hook(self.bash_payload("cat body.md")), (0, b"", b""))

        # plan cases 3 to 6 and 9 to 11: must not fire
        def test_03_landed(self):
            self.attempt(self.body)
            self.make(self.body)
            self.assertAllow("cat body.md")
            self.attempt(self.body)
            self.make(self.body, b"new, longer content\n")
            self.assertAllow("cat body.md")

        def test_03_landed_same_size_same_mtime(self):
            self.make(self.body)
            st = os.stat(self.body)
            self.attempt(self.body)
            tmp = os.path.join(self.work, "body.tmp")  # a new inode renamed over the file, times restored
            self.make(tmp, b"new content\n")
            os.utime(tmp, ns=(st.st_atime_ns, st.st_mtime_ns))
            os.rename(tmp, self.body)
            self.assertAllow("cat body.md")

        def test_03_landed_in_place_times_restored(self):
            self.make(self.body)
            st = os.stat(self.body)
            self.attempt(self.body)
            for _ in range(500):  # rewrite until the change time moves (a coarse clock may need a tick or a second)
                real_sleep(0.01)
                self.make(self.body, b"NEW content\n")
                os.utime(self.body, ns=(st.st_atime_ns, st.st_mtime_ns))
                if os.stat(self.body).st_ctime_ns != st.st_ctime_ns:
                    break
            else:
                self.skipTest("the file system's change time did not move")
            self.assertAllow("cat body.md")

        def test_04_no_attempt(self):
            self.assertAllow("cat body.md")
            self.attempt(os.path.join(self.work, "other.md"))
            self.assertAllow("cat body.md")

        def test_05_ttl(self):
            self.attempt(self.body)
            self.now[0] += _TTL - 1
            self.assertDeny("cat body.md")
            self.now[0] += 2
            self.assertAllow("cat body.md --x")
            self.attempt(self.body)
            self.now[0] -= 10  # a clock stepped back within the TTL: the entry stays live
            self.assertDeny("cat body.md --y")
            self.now[0] -= _TTL  # stepped back past the TTL: not live
            self.assertAllow("cat body.md --z")

        def test_06_deny_once(self):
            self.attempt(self.body)
            self.assertDeny("cat body.md")
            self.assertAllow("cat body.md")
            self.assertDeny("cat body.md ")  # a different command text is denied once too
            self.attempt(self.body)  # the write re-issued, blocked again: a new entry
            self.assertDeny("cat body.md")

        def test_09_unknowable_words(self):
            self.attempt(self.body)
            for c in ('cat "$D/body.md"', "cat $D/body.md", 'cat "$(pwd)/body.md"', "cat ${D}body.md",
                      "cat `pwd`/body.md", "cat $'body.md'"):
                self.assertAllow(c)
            old = os.environ.get("HOME")
            os.environ["HOME"] = self.work
            try:
                self.assertAllow('cat "~/body.md"')
            finally:
                if old is None:
                    del os.environ["HOME"]
                else:
                    os.environ["HOME"] = old

        def test_10_same_name_elsewhere(self):
            self.attempt(self.body)
            for c in ("cat /nonexistent-dir/body.md", "cat sub/body.md", "cat body.md.bak", "cat body.md/",
                      "echo body.md-x", "# cat body.md"):
                self.assertAllow(c)
            self.assertAllow("cat body.md", cwd=self.dir)
            self.assertAllow("cat body.md", cwd="relative/dir")

        def test_11_subagent(self):
            self.attempt(self.body, agent_id="a1")
            self.assertFalse(os.path.exists(self.state_path()))
            self.attempt(self.body)
            self.assertAllow("cat body.md", agent_id="a1")
            self.assertAllow("cat body.md", agent_id=None)
            self.assertDeny("cat body.md")

        # plan case 12: an unsafe state directory or file fails open
        def test_12_symlinked_state_dir(self):
            real = os.path.join(self.dir, "real")
            os.mkdir(real, 0o700)
            os.symlink(real, os.path.join(self.base, _DIR_NAME % real_uid))
            self.attempt(self.body)
            self.assertEqual(os.listdir(real), [])  # nothing written through the link
            with open(os.path.join(real, self.sid + ".json"), "w") as f:
                json.dump({"v": 1, "entries": [{"path": self.body, "named": self.body, "state": None,
                                                "tool": "Write", "t": self.now[0], "denied": []}]}, f)
            os.chmod(os.path.join(real, self.sid + ".json"), 0o600)
            self.assertAllow("cat body.md")

        def test_12_open_or_foreign_state_dir(self):
            self.attempt(self.body)
            self.assertDeny("cat body.md")
            d = os.path.join(self.base, _DIR_NAME % real_uid)
            os.chmod(d, 0o755)
            self.assertAllow("cat body.md --a")
            self.attempt(os.path.join(self.work, "x.md"))
            self.assertEqual([e["path"] for e in self.entries()], [self.body])
            os.chmod(d, 0o700)
            self.assertDeny("cat body.md --a")
            g["_uid"] = lambda: real_uid + 1  # the same directory seen by another user: not theirs
            try:
                self.assertAllow("cat body.md --b")
            finally:
                g["_uid"] = own_uid

        def test_12_unsafe_state_file(self):
            self.attempt(self.body)
            path = self.state_path()
            os.chmod(path, 0o644)
            self.assertAllow("cat body.md")
            os.chmod(path, 0o600)
            link = path + ".link"
            os.link(path, link)
            self.assertAllow("cat body.md")
            os.unlink(link)
            self.assertDeny("cat body.md")
            os.rename(path, path + ".real")
            os.symlink(path + ".real", path)
            self.assertAllow("cat body.md --b")
            self.attempt(os.path.join(self.work, "y.md"))
            self.assertTrue(os.path.islink(path))

        def test_12_state_base_choice(self):
            xdg = os.path.join(self.dir, "xdg")
            os.mkdir(xdg, 0o700)
            self.assertEqual(_state_base({"XDG_RUNTIME_DIR": xdg}), xdg)
            fallback = tempfile.gettempdir()
            for env in ({}, {"XDG_RUNTIME_DIR": ""}, {"XDG_RUNTIME_DIR": "relative"},
                        {"XDG_RUNTIME_DIR": os.path.join(self.dir, "absent")}, {"XDG_RUNTIME_DIR": xdg + "\x00"}):
                self.assertEqual(_state_base(env), fallback, env)
            os.chmod(xdg, 0o750)
            self.assertEqual(_state_base({"XDG_RUNTIME_DIR": xdg}), fallback)
            os.chmod(xdg, 0o700)
            link = os.path.join(self.dir, "xdg-link")
            os.symlink(xdg, link)
            self.assertEqual(_state_base({"XDG_RUNTIME_DIR": link}), fallback)
            g["_uid"] = lambda: real_uid + 1
            try:
                self.assertEqual(_state_base({"XDG_RUNTIME_DIR": xdg}), fallback)
            finally:
                g["_uid"] = own_uid

        def test_12_process_uses_xdg_runtime_dir(self):
            xdg = os.path.join(self.dir, "xdg")
            os.mkdir(xdg, 0o700)
            env = self.hook_env(XDG_RUNTIME_DIR=xdg)
            self.assertEqual(self.run_hook(self.write_payload(self.body), env), (0, b"", b""))
            self.assertTrue(os.path.isfile(os.path.join(xdg, _DIR_NAME % real_uid, self.sid + ".json")))
            self.assertFalse(os.path.exists(self.state_path()))
            rc, out, err = self.run_hook(self.bash_payload("cat body.md"), env)
            self.assertEqual((rc, err, out.count(b"\n")), (0, b"", 1))

        # plan case 13: concurrent recorders under the lock
        def test_13_concurrent_recorders_processes(self):
            # Real processes racing: each exits 0 silently and the file stays a valid, duplicate-free record of
            # them. Whether every one wins the lock inside its bounded wait depends on host load (a loser fails
            # open by design), so no-lost-update is proven deterministically in the next test, not here.
            paths = [os.path.join(self.work, "f%02d.md" % i) for i in range(16)]
            procs = [subprocess.Popen([sys.executable, "-I", "-S", "-B", here], stdin=subprocess.PIPE,
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=self.hook_env())
                     for _ in paths]
            try:
                results = [p.communicate(json.dumps(self.write_payload(q)).encode(), timeout=hang_cap)
                           for p, q in zip(procs, paths)]
            finally:
                for p in procs:
                    if p.poll() is None:
                        p.kill()
                        p.wait(timeout=hang_cap)
            self.assertEqual([(p.returncode,) + r for p, r in zip(procs, results)], [(0, b"", b"")] * len(paths))
            got = [e["path"] for e in self.entries()]
            self.assertTrue(got and set(got) <= set(paths) and len(got) == len(set(got)), got)
            self.assertTrue(all(_valid_entry(e) for e in self.entries()))

        def test_13_no_lost_update(self):
            # Recorder A holds the lock mid-update until recorder B is seen waiting on it; B's wait is made
            # unbounded in host time (its clock does not move), so the order is forced, not raced.
            a_in, b_waiting, waited = threading.Event(), threading.Event(), []
            fake = FakeTime(on_sleep=lambda n: b_waiting.set(), advance=False)
            own_write = g["_write_entries"]

            def write(*args):
                if threading.current_thread() is ta:
                    a_in.set()
                    waited.append(b_waiting.wait(10))
                own_write(*args)

            def record(name):
                payload = self.write_payload(os.path.join(self.work, name))
                payload["session_id"] = self.sid
                _record(payload, "Write", self.base, self.now[0])

            ta = threading.Thread(target=record, args=("a.md",))
            tb = threading.Thread(target=record, args=("b.md",))
            g["_write_entries"], g["time"] = write, fake
            try:
                ta.start()
                self.assertTrue(a_in.wait(hang_cap))
                tb.start()
                ta.join(hang_cap)
                tb.join(hang_cap)
            finally:
                g["_write_entries"], g["time"] = own_write, real_time
            self.assertFalse(ta.is_alive() or tb.is_alive())
            self.assertEqual(waited, [True])
            self.assertGreaterEqual(fake.sleeps, 1)
            self.assertEqual(sorted(os.path.basename(e["path"]) for e in self.entries()), ["a.md", "b.md"])

        def test_13_lock_wait_counted(self):
            # The bounded wait measured in steps of the lock loop on a clock that moves only by its own sleeps:
            # held throughout, it gives up after _LOCK_WAIT / _LOCK_STEP steps; released on the third, it wins.
            self.attempt(self.body)
            held, fd = os.open(self.state_path(), os.O_RDWR), os.open(self.state_path(), os.O_RDWR)
            try:
                fcntl.flock(held, fcntl.LOCK_EX)
                steps = int(round(_LOCK_WAIT / _LOCK_STEP))
                fake = FakeTime()
                g["time"] = fake
                try:
                    self.assertFalse(_lock(fd))
                    self.assertIn(fake.sleeps, (steps, steps + 1))
                    fake = FakeTime(on_sleep=lambda n: n == 3 and fcntl.flock(held, fcntl.LOCK_UN))
                    g["time"] = fake
                    self.assertTrue(_lock(fd))
                    self.assertEqual(fake.sleeps, 3)
                finally:
                    g["time"] = real_time
            finally:
                os.close(fd)
                os.close(held)

        def test_13_lock_held_process_outcome(self):
            # A hook process facing a held lock: it records nothing, denies nothing, exits 0 silently, and returns
            # within the hang cap; once the lock is free, the same recorder records.
            self.attempt(self.body)
            other = os.path.join(self.work, "other.md")
            fd = os.open(self.state_path(), os.O_RDWR)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)
                self.assertEqual(self.run_hook(self.write_payload(other)), (0, b"", b""))
                self.assertEqual(self.run_hook(self.bash_payload("cat body.md")), (0, b"", b""))
                self.assertEqual([e["path"] for e in self.entries()], [self.body])
            finally:
                os.close(fd)
            self.assertEqual(self.run_hook(self.write_payload(other)), (0, b"", b""))
            self.assertEqual([e["path"] for e in self.entries()], [self.body, other])

        # plan case 14: a corrupt state file
        def test_14_corrupt_state(self):
            self.attempt(self.body)
            bad_entry = {"path": self.body, "named": self.body, "state": None, "tool": "Write", "t": True,
                         "denied": []}
            for data in (b"{not json", b"[]", b'{"v": 2, "entries": []}', b'{"v": 1}',
                         json.dumps({"v": 1, "entries": [bad_entry]}).encode(), b"\xff\xfe",
                         b" " * (_MAX_STATE + 1), b"[" * 60000):
                with open(self.state_path(), "wb") as f:
                    f.write(data)
                self.assertAllow("cat body.md")
            self.attempt(os.path.join(self.work, "new.md"))
            self.assertEqual([e["path"] for e in self.entries()], [os.path.join(self.work, "new.md")])

        # plan cases 15 and 16: fail-open inputs
        def test_15_malformed_payloads(self):
            self.attempt(self.body)
            good = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "session_id": self.sid, "cwd": self.work,
                    "tool_input": {"command": "cat body.md"}}
            for change in ({"tool_input": "x"}, {"tool_input": {"command": 7}}, {"tool_input": {"command": ""}},
                           {"tool_name": "Read"}, {"tool_name": None}, {"hook_event_name": "PostToolUse"},
                           {"session_id": "../x"}, {"session_id": ""}, {"session_id": "a" * 129},
                           {"session_id": 7}, {"session_id": "a\nb"}):
                p = dict(good, **change)
                self.assertIsNone(_decide(p, {}, self.base, self.clock), change)
            for p in (None, 7, "x", [], {}):
                self.assertIsNone(_decide(p, {}, self.base, self.clock), p)
            self.assertIsNotNone(_decide(good, {}, self.base, self.clock))

        def test_15_recorder_bad_inputs(self):
            for tool, tin in (("Write", {"file_path": 7}), ("Write", {"file_path": ""}),
                              ("Write", {"file_path": "a\x00b"}), ("Write", {"file_path": "/" + "a" * 5000}),
                              ("Write", "x"), ("NotebookEdit", {"file_path": self.body}), ("Read", {"file_path": "x"})):
                payload = {"hook_event_name": "PreToolUse", "tool_name": tool, "session_id": self.sid,
                           "cwd": self.work, "tool_input": tin}
                self.assertIsNone(_decide(payload, {}, self.base, self.clock))
            self.attempt("body.md", cwd=None)  # relative, with no usable cwd
            self.assertFalse(os.path.exists(self.state_path()))

        def test_15_process_fail_open(self):
            self.attempt(self.body)
            good = json.dumps(self.bash_payload("cat body.md")).encode()
            for data in (b"", b"not json", b"[1, 2]", b"7", b"\xff\xfe\x00garbage", good[:-1]):
                self.assertEqual(self.run_hook(data), (0, b"", b""), data[:30])

        def test_16_worker_envs(self):
            for env in ({"AIQT_HOOKS_WORKER": "1"}, {"ORCH_WORKER": "1"}, {"ORCH_VERIFY_OWNER": ""},
                        {"ORCH_VERIFY_OWNER": "x"}, {"AIQT_HOOKS_WORKER": "0", "ORCH_WORKER": "1"}):
                self.attempt(self.body, env=env)
                self.assertFalse(os.path.exists(self.state_path()), env)
            self.attempt(self.body)
            for env in ({"AIQT_HOOKS_WORKER": "1"}, {"ORCH_WORKER": "1"}, {"ORCH_VERIFY_OWNER": ""},
                        {"ORCH_VERIFY_OWNER": "x"}):
                self.assertAllow("cat body.md", env=env)
            for n, env in enumerate(({"AIQT_HOOKS_WORKER": "0"}, {"AIQT_HOOKS_WORKER": "yes"},
                                     {"ORCH_WORKER": "0"})):
                self.assertDeny("cat body.md" + " " * n, env=env)
            for extra in ({"AIQT_HOOKS_WORKER": "1"}, {"ORCH_WORKER": "1"}, {"ORCH_VERIFY_OWNER": ""}):
                self.assertEqual(self.run_hook(self.bash_payload("cat body.md -z"), self.hook_env(**extra)),
                                 (0, b"", b""), extra)

        def test_16_bad_argv(self):
            old = sys.stdout
            sys.stdout = io.StringIO()
            try:
                for argv in (None, 7, "--self-test", ["x", 3], {"a": 1}, []):
                    self.assertEqual(main(argv), 0)
                self.assertEqual(sys.stdout.getvalue(), "")
            finally:
                sys.stdout = old
            self.attempt(self.body)
            for extra in (["--bogus"], ["extra"], ["--self-test", "extra"], ["-", "--self-test"]):
                p = subprocess.run([sys.executable, "-I", "-S", "-B", here] + extra,
                                   input=json.dumps(self.bash_payload("cat body.md")).encode(),
                                   capture_output=True, env=self.hook_env(), timeout=hang_cap)
                self.assertEqual((p.returncode, p.stdout, p.stderr), (0, b"", b""), extra)

        def test_16_directory_stdin_guard(self):
            if not os.path.exists("/bin/sh"):
                self.skipTest("no /bin/sh")
            fd = os.open(self.dir, os.O_RDONLY)
            try:
                line = REGISTRATION.format(python=sys.executable, hook=here)
                p = subprocess.run(["/bin/sh", "-c", line], stdin=fd, capture_output=True, env=self.hook_env(),
                                   timeout=hang_cap)
                self.assertEqual((p.returncode, p.stdout, p.stderr), (0, b"", b""))
            finally:
                os.close(fd)

        # plan case 17: work bounds, hangs, and cost growth
        def test_17_hang_ceiling(self):
            self.attempt(self.body)
            shapes = ("cat " + "body.md " * 7000, "cat " + "x/body.md " * 3000, "cat body.md $(" * 5000,
                      "cat body.md " + " " * 70000, "cat body.md " + chr(233) * 33000,
                      "cat body.md \"" + "a" * 60000, "cat body.md <<E\n" + "body.md\n" * 5000,
                      "cat " + "./" * 30000 + "body.md")
            for c in shapes:
                rc, out, err = self.run_hook(self.bash_payload(c))
                self.assertEqual((rc, err), (0, b""), c[:40])
                self.assertLessEqual(out.count(b"\n"), 1, c[:40])

        def test_17_bounds(self):
            self.attempt(self.body)
            self.assertAllow("cat body.md " + " " * 65536)  # over 64 KiB: not judged
            self.assertAllow("cat body.md " + chr(233) * 33000)  # under 64 KiB of characters, over it in bytes
            self.assertAllow("cat x/body.md; " * 300 + "cat body.md")  # past the resolve bound
            self.assertDeny("cat body.md " + " " * 60000)

        def test_17_cost_growth(self):
            # Host-independent: file-system work is counted (one resolve for the one matching word, whatever the
            # length), and the checking work is the count of Python lines this module executes (sys.settrace),
            # which no machine speed or load can change; 8x the input gives near 8x the lines when linear, 64x
            # quadratic.
            self.attempt(self.body)
            module, seq = globals(), [0]

            def lines_run(cmd, expect_deny=True):
                seq[0] += 1  # a fresh command text each run, so deny-once never allows a repeat
                payload = {"session_id": self.sid, "cwd": self.work,
                           "tool_input": {"command": cmd + " " * seq[0]}}
                count = [0]

                def local(frame, event, arg):
                    if event == "line":
                        count[0] += 1
                    return local
                old = sys.gettrace()
                sys.settrace(lambda frame, event, arg: local if frame.f_globals is module else None)
                try:
                    verdict = _check(payload, self.base, self.now[0])
                finally:
                    sys.settrace(old)
                self.assertEqual(verdict is not None, expect_deny, cmd[:40])
                return count[0]

            calls, own_realpath = [], os.path.realpath

            def counted(path, *a, **kw):
                calls.append(path)
                return own_realpath(path, *a, **kw)
            os.path.realpath = counted
            try:
                for k in (480, 3840):
                    del calls[:]
                    lines_run("cat body.md " + "x " * k)
                    self.assertEqual(len(calls), 1, k)
            finally:
                os.path.realpath = own_realpath
            for make, deny in ((lambda k: "cat body.md " + "x " * k, True),
                               (lambda k: "cat x; " * (k // 4) + "cat body.md", True),
                               (lambda k: "cat " + "{" * k + "body.md" + "}" * k + ",x", False),
                               (lambda k: "cat " + "{a," * k + "body.md" + "}" * k, False),
                               (lambda k: "cat {" + "a," * k + "body.md}", False),
                               (lambda k: "echo " + "`echo x`" * k + "; cat body.md", True),
                               (lambda k: "x=(" + "a " * k + "); cat body.md", True)):
                small, large = lines_run(make(400), deny), lines_run(make(3200), deny)
                self.assertLess(large / small, 16, (small, large, make(2)))

        def test_17_path_resolution_counted(self):
            # The review's shape: one name of 32,760 components, and many long names; lookups are counted, not
            # timed, and stay within the per-name and per-check component bounds.
            self.attempt(self.body)
            own_lstat, calls = os.lstat, [0]

            def counted(*a, **kw):
                calls[0] += 1
                return own_lstat(*a, **kw)
            os.lstat = counted
            try:
                self.assertAllow(("cat " + "a/" * 32760 + "body.md").ljust(65536))
                self.assertLess(calls[0], 16, calls[0])
                calls[0] = 0
                self.assertAllow("cat " + "a/" * 1000 + "body.md")  # past the per-name bound, within the budget
                self.assertLess(calls[0], 16, calls[0])
                calls[0] = 0
                self.assertAllow(("cat " + "b/" * 200 + "body.md; ") * 40)  # 40 names of about 210 components
                self.assertGreater(calls[0], 2 * _MAX_SEGMENTS)  # the budget is reached, not the per-name bound ...
                self.assertLessEqual(calls[0], _SEGMENT_BUDGET + 64, calls[0])  # ... and it holds
            finally:
                os.lstat = own_lstat
            self.assertDeny("cat body.md")  # an ordinary name is still resolved

        def test_17_leading_words_counted(self):
            # The review's shape: 32,762 leading `!` words before the command, padded to 64 KiB. Counted, not timed:
            # the command-start state is kept per token, so the lines grow with the input, not with its square.
            module = globals()

            def lines_for(cmd):
                count = [0]

                def local(frame, event, arg):
                    if event == "line":
                        count[0] += 1
                    return local
                old = sys.gettrace()
                sys.settrace(lambda frame, event, arg: local if frame.f_globals is module else None)
                try:
                    refs = []
                    _refs(_tokenize(cmd), cmd, refs, [_MAX_BACKTICKS])
                finally:
                    sys.settrace(old)
                self.assertEqual(len(refs), 1, cmd[:40])  # reached the command and read its operand
                return count[0]
            small, large = lines_for("! " * 500 + "cat body.md"), lines_for("! " * 4000 + "cat body.md")
            self.assertLess(large / small, 16, (small, large))
            shape = ("! " * 32762 + "cat body.md").ljust(65536)
            self.assertLess(lines_for(shape), 40 * len(shape))

        def test_17_brace_worst_case(self):
            # The review's shape: 10,000 nested braces with no comma inside, padded to 64 KiB. Counted, not timed.
            self.attempt(self.body)
            cmd = ("cat " + "{" * 10000 + "body.md" + "}" * 10000 + ",x").ljust(65536)
            module, count = globals(), [0]

            def local(frame, event, arg):
                if event == "line":
                    count[0] += 1
                return local
            old = sys.gettrace()
            sys.settrace(lambda frame, event, arg: local if frame.f_globals is module else None)
            try:
                self.assertAllow(cmd)
            finally:
                sys.settrace(old)
            self.assertLess(count[0], 40 * len(cmd), count[0])  # linear: a few lines per character

        # state bookkeeping
        def test_caps(self):
            paths = [os.path.join(self.work, "f%03d.md" % i) for i in range(_MAX_ENTRIES + 6)]
            for p in paths:
                self.attempt(p)
                self.now[0] += 1
            self.assertEqual([e["path"] for e in self.entries()], paths[-_MAX_ENTRIES:])
            self.assertLessEqual(os.path.getsize(self.state_path()), _MAX_STATE)
            last = "cat f%03d.md" % (len(paths) - 1)
            for i in range(_MAX_DENIED):
                self.assertDeny(last + " " * i)
            self.assertAllow(last + " " * _MAX_DENIED)  # a full entry denies nothing new (fails open) ...
            self.assertAllow(last)  # ... and forgets none of its earlier denials
            self.assertEqual(len(self.entries()[-1]["denied"]), _MAX_DENIED)

        def test_resolution(self):
            self.make(self.body)
            link = os.path.join(self.work, "link.md")
            os.symlink(self.body, link)
            self.attempt(link)
            self.assertEqual(self.entries()[0]["path"], self.body)
            self.assertDeny("cat link.md")
            self.assertDeny("cat body.md")
            other = os.path.join(self.work, "third.md")
            os.symlink(self.body, other)
            self.assertAllow("cat third.md")  # a link of a third name: not resolved (disclosed)
            nb = os.path.join(self.work, "n.ipynb")
            self.attempt(nb, tool="NotebookEdit")
            self.assertIn("(NotebookEdit)", self.assertDeny("cat n.ipynb"))
            self.attempt("rel.md", tool="MultiEdit")
            self.assertIn("(MultiEdit)", self.assertDeny("cat " + os.path.join(self.work, "rel.md")))
            self.assertEqual(stat.S_IMODE(os.stat(os.path.dirname(self.state_path())).st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(os.stat(self.state_path()).st_mode), 0o600)

        def test_quick_exit_leaves_state(self):
            self.attempt(self.body)
            before = open(self.state_path(), "rb").read()
            self.assertAllow("ls -la")
            self.assertAllow("cat other.md")
            self.assertEqual(open(self.state_path(), "rb").read(), before)

        def test_non_ascii_path(self):
            name = chr(233) + chr(8364) + ".md"
            p = os.path.join(self.work, name)
            self.attempt(p)
            self.assertIn(name, self.assertDeny("cat " + name))
            rc, out, err = self.run_hook(self.bash_payload("cat ./" + name))
            self.assertEqual((rc, err), (0, b""))
            out.decode("ascii")
            self.assertIn(name, json.loads(out)["hookSpecificOutput"]["permissionDecisionReason"])

        # round-1 review items: each passes only with its fix present
        def test_qa_01_reads_only_consumed_words(self):
            self.make(os.path.join(self.work, "other.txt"))
            self.attempt(self.body)
            for c in ("echo body.md", "printf '%s\\n' body.md", "grep -F body.md other.txt",
                      "files=(body.md); echo ok",
                      "printf new > body.md", "echo x >> body.md", "cat <<body.md\nplain text\nbody.md",
                      "cat other.txt <<< body.md", "cd other && cat body.md", "pushd other; cat body.md",
                      "popd; cat body.md", "env -C other cat body.md", "sudo -D other cat body.md",
                      "git -C other show body.md", "test -f body.md", "[ -s body.md ] && echo y", "[[ -e body.md ]]",
                      "rm -f body.md", "command -v body.md", "grep -e body.md other.txt", "sed s/body.md/x/ other.txt",
                      "for f in body.md; do :; done", "case body.md in *) :;; esac", "cat 3<>body.md", "ls >body.md",
                      "grep x body.md", "sed -n 1p body.md", "jq . body.md", "make -f body.md",
                      "cat other.txt body.md"):
                self.assertAllow(c)
            for c in ("cat body.md; cd other", "sudo -u bob cat body.md", "env A=1 cat body.md",
                      "timeout 5 cat body.md",
                      "x=1 cat body.md", "! cat body.md", "if cat body.md; then :; fi", "{ cat body.md; }",
                      "(cat body.md)", "cat 2>/dev/null body.md", "while read l; do :; done < body.md",
                      "gh pr create -Fbody.md", "command cat body.md", "wc -l < body.md", "jq -n --rawfile v body.md .",
                      "xargs -a body.md echo", "nice -n 5 cat body.md", "env -i cat body.md"):
                self.assertDeny(c)

        def test_qa_02_backticks(self):
            self.attempt(self.body)
            for c in ("printf '%s' \"`cat body.md`\"", "x=`cat body.md`", "echo `echo \\`cat body.md\\``",
                      "echo \"$(echo `cat body.md`)\""):
                self.assertDeny(c)
            for c in ("echo '`cat body.md`'", "echo \\`cat body.md\\`", "cat `echo body.md`"):
                self.assertAllow(c)

        def test_qa_03_brace_and_glob(self):
            self.attempt(self.body)
            for c in ("cat body.{md,txt}", "cat {a,body}.md", "cat bo{d,x}y.md", "cat {x,{y,body.md}}"):
                self.assertDeny(c)
            for c in ("cat *.md", "cat body.m?", "cat body.[m]d", "cat \"body.{md,txt}\"", "cat {1..3}.md",
                      "cat {a,b}{a,b}{a,b}{a,b}{a,b}{a,b}{a,b}body.md"):
                self.assertAllow(c)

        def test_qa_04_deny_once_survives_other_denials(self):
            self.attempt(self.body)
            self.assertDeny("cat body.md")
            for c in ("cat body.md > o1", "cat body.md | wc", "python3 body.md", "bash body.md", "sh body.md",
                      "source body.md", ". body.md", "cat body.md; true", "gh pr create -F body.md",
                      "gh pr create --body-file body.md"):
                self.assertDeny(c)
            self.assertAllow("cat body.md")
            self.assertEqual(len(self.entries()[0]["denied"]), 11)

        def test_qa_05_malformed_entry_dropped(self):
            self.attempt(self.body)
            good = self.entries()[0]
            bads = [dict(good, tool=[]), dict(good, t="x"), dict(good, path="rel"), dict(good, denied=[7]),
                    dict(good, named=None), {k: v for k, v in good.items() if k != "named"}, [], None,
                    dict(good, state=[1, 2]), dict(good, t=float("inf"))]
            with open(self.state_path(), "w") as f:
                f.write(json.dumps({"v": 1, "entries": bads + [good]}).replace("Infinity", "1e999"))
            self.assertDeny("cat body.md")  # the valid entry still counts
            self.attempt(os.path.join(self.work, "x.md"))
            doc = json.loads(open(self.state_path()).read())
            self.assertEqual([e["path"] for e in doc["entries"]], [self.body, os.path.join(self.work, "x.md")])
            self.assertEqual(doc["dropped"], len(bads))
            self.assertDeny("cat x.md")

        def test_qa_06_backward_clock_step(self):
            self.attempt(self.body)
            self.now[0] -= 1
            self.assertDeny("cat body.md")
            self.now[0] += 2 + _TTL
            self.assertAllow("cat body.md --late")

        def test_qa_07_reason_names_the_written_path(self):
            real = os.path.join(self.work, "real")
            os.mkdir(real)
            os.symlink(real, os.path.join(self.work, "ln"))
            named = os.path.join(self.work, "ln", "body.md")
            self.attempt(named)
            reason = self.assertDeny("cat ln/body.md")
            self.assertIn(named, reason)
            self.assertNotIn(os.path.join(real, "body.md"), reason)

        def test_qa_09_pre_epoch_times(self):
            self.attempt(os.path.join(self.work, "other.md"))
            self.make(self.body)
            os.utime(self.body, ns=(-5, -5))
            self.attempt(self.body)
            self.assertEqual([e["path"] for e in self.entries()], [os.path.join(self.work, "other.md"), self.body])
            self.assertIn("unchanged", self.assertDeny("cat body.md"))
            self.assertDeny("cat other.md")

        def test_qa_08_line_continuation_in_name(self):
            self.attempt(self.body)
            self.assertDeny("cat bo\\\ndy.md")

        # round-2 review: the allowlist of words read as files
        def test_verdict_table(self):
            # Every review counterexample (rounds 1 to 3): denied when in the minimal set, allowed when outside it.
            spaced = os.path.join(self.work, "dir space")
            os.mkdir(spaced)
            os.mkdir(os.path.join(self.work, "sub"))
            self.make(os.path.join(self.work, "sub", "body.md"))
            self.make(os.path.join(self.work, "other.txt"))
            table = (
                # in the set: denied
                ("cat body.md", 1), ("wc -l < body.md", 1), ("python3 body.md", 1), ("python body.md", 1),
                ("bash body.md", 1), ("sh body.md", 1), ("zsh body.md", 1), ("node body.md", 1), ("ruby body.md", 1),
                ("perl body.md", 1), ("source body.md", 1), (". body.md", 1), ("xargs -a body.md echo", 1),
                ("xargs --arg-file body.md echo", 1), ("xargs --arg-file=body.md echo", 1),
                ("gh pr create --body-file body.md", 1), ("gh issue create -F body.md", 1),
                ("gh pr create --body-file=body.md", 1), ("gh release create v1 --notes-file body.md", 1),
                ("docker run --env-file=body.md img", 1), ("docker run --env-file body.md img", 1),
                ("jq -n --rawfile v body.md .", 1), ("jq --slurpfile v body.md -n .", 1),
                ("jq --args --rawfile v body.md -n '$v'", 1), ("printf '%s' \"`cat body.md`\"", 1),
                ("x=$(cat body.md)", 1), ("cat body.{md,txt}", 1), ('cat "dir space"/body.{md,txt}', 1),
                ("cat bo\\\ndy.md", 1), ("sudo -u bob cat body.md", 1), ("cat body.md; cd other", 1),
                ("x=(a $(cat body.md))", 1), ("env -uCLICOLOR cat body.md", 1), ("env -u CLICOLOR cat body.md", 1),
                ("python3 body.md --flag x", 1), ("tee x < body.md", 1),
                # round 4, in the set: denied
                ("gh issue create -R example/repo --title t -F=body.md", 1), ("xargs --eof cat body.md", 1),
                ("xargs --replace cat body.md", 1), ("xargs --max-lines cat body.md", 1), ("xargs -e cat body.md", 1),
                ("xargs --eof=x cat body.md", 1), ("env -- X=1 cat body.md", 1), ("env -i -- X=1 Y=2 cat body.md", 1),
                ("echo \"$(printf '(')`cat body.md`\"", 1), ("sudo -- cat body.md", 1), ("time -p cat body.md", 1),
                ("command -p cat body.md", 1), ("xargs -0 -a body.md echo", 1), ("nice -10 cat body.md", 1),
                ("timeout -k 5 10 cat body.md", 1), ("[[ -n x ]] && cat body.md", 1), ("(( x )) && cat body.md", 1),
                ("for ((i=0; i<1; i++)); do cat body.md; done", 1), ("[[ $(cat body.md) ]]", 1),
                ("env -Csub cat < body.md", 1),  # the redirection opens in the current directory, before the change
                # round 5, in the set: denied
                ("echo $(( $(cat body.md) + 1 ))", 1), ('echo "$(( $(cat body.md) + 1 ))"', 1),
                ("x=$(( `cat body.md` ))", 1), ("echo $(( 1 + $(( $(cat body.md) )) ))", 1),
                ("env -a label cat body.md", 1), ("env -f vars.env cat body.md", 1), ("env --file=v cat body.md", 1),
                ("timeout -f 2 cat body.md", 1), ("timeout -p 2 cat body.md", 1), ("time -p cat body.md", 1),
                # round 6: bash does not treat a single quote as quoting inside $((...)): the substitution runs
                ("echo $(( '$(cat body.md)' + 1 ))", 1), ("echo $(( '`cat body.md`' + 1 ))", 1),
                # round 8, in the set: denied (bash's time keyword before a compound command or a negation; a
                # comment inside a substitution span; uutils timeout options after the duration)
                ("time { cat body.md; }", 1), ("time if cat body.md; then :; fi", 1), ("time ! cat body.md", 1),
                ("time -p { cat body.md; }", 1), ("time -- ! cat body.md", 1),
                ("echo $(( $(# don't recompute\ncat body.md\n) + 1 ))", 1),
                ("echo $(( 16#ff + $(cat body.md) ))", 1),
                ("timeout 2 -v cat body.md", 1), ("timeout 2 --foreground cat body.md", 1),
                ("timeout 2 -k 1 -v cat body.md", 1), ("timeout -v 2 --kill-after=1 cat body.md", 1),
                ("timeout 2 -- cat body.md", 1),
                # round 9, in the set: denied (a substitution's `)` continues its word, so a `#` after it opens no
                # comment; a backslash-newline is deleted before words are split; time takes `-p --` in that order)
                ("echo $(( $(echo 16)#ff + $(cat body.md) ))", 1), ("echo $(( $((16))#ff + $(cat body.md) ))", 1),
                ("echo $(echo $(echo 1)#x)`cat body.md`", 1), ("echo $(( $(printf 10)#$(cat body.md) ))", 1),
                ("echo $(( $(echo hi \\\n# don't recompute\ncat body.md\n) + 1 ))", 1),
                ("time -p -- ! cat body.md", 1),
                # outside the set, or text only: allowed
                ("echo body.md", 0), ("printf '%s\\n' body.md", 0), ("printf '%s\\n' --body-file body.md", 0),
                ("echo --env-file body.md", 0), (": --body-file body.md", 0), ("true -F body.md", 0),
                ("grep -F body.md other.txt", 0), ("files=(foo body.md); printf '%s\\n' \"${files[@]}\"", 0),
                ("files=(cat body.md)", 0), ("printf new > body.md", 0), ("cat <<body.md\nx\nbody.md", 0),
                ("cd other && cat body.md", 0), ("env -Csub cat body.md", 0), ("env --chdir=sub cat body.md", 0),
                ("sudo -Dsub cat body.md", 0), ("env -iCsub cat body.md", 0), ("cp other.txt body.md", 0),
                ("mv other.txt body.md", 0), ("tee body.md < other.txt", 0), ("sort -o body.md other.txt", 0),
                ("uniq other.txt body.md", 0), ("touch body.md", 0), ("ls body.md", 0), ("stat body.md", 0),
                ("cat --unknown=body.md other.txt", 0), ("cat *.md", 0), ("cat body.m?", 0), ("cat body.[m]d", 0),
                ("python3 -c 'x' body.md", 0), ("python3 -m mod body.md", 0), ("bash -c 'cat body.md'", 0),
                ("tar -cf body.md x", 0), ('cat "body.{md,txt}"', 0), ("chmod 600 body.md", 0),
                ("cat -e body.md", 0), ("cat -- body.md", 0), ("cat other.txt body.md", 0), ("head -n 5 body.md", 0),
                ("grep x body.md", 0), ("grep -e y --exclude body.md other.txt", 0), ("make -Csub -f body.md", 0),
                ("make -f body.md", 0), ("make -sf body.md", 0), ("printf 'printf ok' | bash -s body.md", 0),
                ("sh -s body.md", 0), ("uniq --group body.md", 0), ("uniq --all-repeated body.md", 0),
                ("diff --unified body.md other.txt", 0), ("diff --color body.md other.txt", 0),
                ("xxd -i -C body.md", 0), ("node --require ./bootstrap.js body.md", 0),
                ("python3 -B --check-hash-based-pycs always body.md", 0), ("jq --args -f body.md", 0),
                ("xargs -ra body.md echo", 0), ("bash --rcfile init.sh body.md", 0), ("diff -D body.md f1 f2", 0),
                ("grep --exclude body.md pattern", 0), ("jq . body.md", 0), ("sed -f body.md x", 0),
                ("python3 x.py body.md", 0), ("git commit -F body.md", 0), ("kubectl apply -f body.md", 0),
                ("tar -xf x -F body.md", 0), ("xargs -aFILE echo body.md", 0), ("tool --rawfile v body.md", 0),
                # round 4, outside the set or unjudged: allowed
                ("[[ a < body.md ]]", 0), ("[[ -n x && a < body.md ]]", 0), ("(( a < body.md ))", 0),
                ("for ((i=0; i < body.md; i++)); do :; done", 0), ("command -pv cat body.md", 0),
                ("command -pV cat body.md", 0), ("command -vp cat body.md", 0), ("sudo --env-file body.md ls", 0),
                ("time --body-file=body.md ls", 0), ("sudo -l cat body.md", 0), ("env -S 'cat body.md'", 0),
                ("xargs --bogus cat body.md", 0), ("env --help cat body.md", 0), ("nohup -x cat body.md", 0),
                ("env --split-string=x cat body.md", 0), ("sudo -h cat body.md", 0), ("ionice -p 1 cat body.md", 0),
                ("env -Csub true; cat body.md", 0), ("sudo -Dsub true; cat body.md", 0),
                ("env --chdir sub true; cat body.md", 0),
                # round 5, unjudged or outside the set: allowed
                ("xargs -a body.md --help", 0), ("xargs --arg-file=body.md --bogus", 0),
                ("xargs -a body.md sudo -l cat", 0), ("env -S x cat body.md", 0), ("env -h cat body.md", 0),
                ("timeout -V 2 cat body.md", 0), ("sudo -h localhost cat body.md", 0), ("time [[ a < body.md ]]", 0),
                ("time -p [[ a < body.md ]]", 0), ("time -- (( a < body.md ))", 0),
                ("echo $(( $(echo body.md) ))", 0), ("echo $(( 1 + 2 )) body.md", 0),
                # round 6, disclosed: bash re-reads a `$((` whose text is not arithmetic as `$( (subshell) )`; this
                # guard reads every `$((` as arithmetic, so the subshell's read is missed (pinned as allowed)
                ("echo $((cat body.md);:)", 0), ("echo $((cat body.md) )", 0),
                # round 8, unjudged or outside the set: allowed (an unknown or query option leaves timeout
                # unjudged; after a lone `--` every word is an operand; time -o names a file time writes)
                ("timeout 2 -x cat body.md", 0), ("timeout 2 -h cat body.md", 0),
                ("timeout 2 -- -v cat body.md", 0), ("timeout -- 2 -v cat body.md", 0),
                ("time -o body.md ls", 0),
                # round 9: bash's time takes at most one `-p`, then at most one `--`, and is no reserved word after
                # an assignment, so each of these runs `-p`, `--`, or /usr/bin/time on `!`, never cat
                ("time -p -p ! cat body.md", 0), ("time -- -p ! cat body.md", 0), ("time -- -- ! cat body.md", 0),
                ("FOO=1 time ! cat body.md", 0),
            )
            for command, deny in table:
                self.attempt(self.body)  # a fresh entry per row: each verdict stands alone (deny-once, the cap)
                self.attempt(os.path.join(spaced, "body.md"))
                (self.assertDeny if deny else self.assertAllow)(command)

        def test_r2_brace_expansion_unit(self):
            u = lambda word: [(c, True) for c in word]  # noqa: E731 (all unquoted)
            self.assertEqual(_expand(u("a{b,c}d")), ["abd", "acd"])
            self.assertEqual(_expand(u("{x,{y,z}}")), ["x", "y", "z"])
            self.assertEqual(_expand(u("a{b}c")), ["a{b}c"])  # no comma: literal
            self.assertEqual(_expand(u("a{b{c,d}}")), ["a{bc}", "a{bd}"])
            self.assertEqual(_expand(u("{1..3}")), ["{1..3}"])  # a range: left literal
            self.assertEqual(_expand(u("a{,b}")), ["a", "ab"])
            self.assertEqual(_expand([("{", False), ("a", True), (",", True), ("b", True), ("}", False)]),
                             ["{a,b}"])  # quoted braces are literal
            self.assertEqual(_expand([("d", False), (" ", False), ("/", True)] + u("{a,b}")), ["d /a", "d /b"])
            self.assertIsNone(_expand(u("{a,b}" * 7)))  # 128 names: over the bound
            self.assertIsNone(_expand(u("{a," * 40 + "x" + "}" * 40)))  # nested past the bound
            self.assertIsNone(_expand(u("*.md")))
            self.assertEqual(_expand([("*", False)] + u(".md")), ["*.md"])  # a quoted glob character is literal

        def test_r8_arith_nesting_linear(self):
            # The exact 64 KiB shape from review round 6: 30 nested `$(( ` around 65337 blanks, then a checked
            # read. Matching each nested span through _span_end once rescanned the text after it per level, about
            # 16.2 million executed lines; read in place the whole decision stays linear. The bar is an executed-
            # line count under settrace, the same on every host, never a wall-clock assertion.
            cmd = "echo " + "$(( " * 30 + "1" + " " * 65337 + "))" * 30 + "; cat body.md"
            self.assertEqual(len(cmd), _SCAN_LIMIT)
            self.attempt(self.body)
            counted = [0]

            def tracer(frame, event, arg):
                if event == "line":
                    counted[0] += 1
                return tracer

            old_trace = sys.gettrace()
            sys.settrace(tracer)
            try:
                out = self.decide(cmd)
            finally:
                sys.settrace(old_trace)
            self.assertIsNotNone(out, "the trailing read is still denied")
            self.assertLess(counted[0], 3000000)

        # vendoring and source rules
        def test_21_vendor_hashes(self):
            import hashlib as h
            blocks = vendor_blocks()
            self.assertEqual(set(blocks), set(_VENDOR))
            for name, (head, body) in blocks.items():
                ref, first, last, digest = _VENDOR[name]
                self.assertEqual(head[4:], ["from", "%s:%d-%d" % (ref, first, last)], name)
                self.assertEqual(h.sha256(body).hexdigest(), digest, name)

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
            self.assertIsNone(re.search(rb"\bG[0-9]+\b", src))  # no internal guard numbers
            low = src.lower()
            for word in ("cla" + "ude", "anthr" + "opic", "co" + "dex", "gem" + "ini", "fa" + "ble", "fl" + "eet",
                         "orchestr" + "at", "op" + "us", "son" + "net"):
                self.assertNotIn(word.encode(), low, word)

        def test_strip_source(self):
            sample = ('X = 1  # note\n# whole line\n\ndef f(a):\n    """doc\n    more"""\n'
                      '    s = "a # not a comment"\n    return s  # trailing\n')
            want = 'X = 1\n\ndef f(a):\n    s = "a # not a comment"\n    return s\n'
            self.assertEqual(_strip_source(sample), want)
            self.assertEqual(_strip_source(want), want)

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

    try:
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(T)
        result = unittest.TextTestRunner(stream=sys.stderr, verbosity=2).run(suite)
    finally:
        os.umask(old_umask)
        shutil.rmtree(root, ignore_errors=True)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
