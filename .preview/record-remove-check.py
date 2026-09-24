#!/usr/bin/env python3
"""PreToolUse Bash hook (record-remove-check): deny destroying an existing working-record file.

WHAT IT DOES
    A command that removes, truncates, or replaces a file in the working-record store (the folders that hold
    the project's durable records) destroys that file's content with no copy when the file already existed and
    held something. `rm -f` exits 0 either way, so the loss is silent, and by the time any later step could
    notice it the bytes are gone. The mistake this guards is destroying a record in the belief that the same
    command just created it (`cat >> DIR/X.md <<'EOF' ... EOF`, then `rm -f DIR/X.md`, when X.md already held
    content: an append creates a file only when it was absent). This hook reads the command before it runs and
    checks the filesystem at that moment: when a file the command would destroy already exists in the store
    and is not empty, the command is denied, and the reason names the file and its size and says how to go on.

    Event: PreToolUse, matcher Bash. Register the launch line REGISTRATION (below the imports), filled with
    python3 and this file's absolute path. Output: nothing (allow), or ONE line holding the standard PreToolUse
    deny object plus a short systemMessage. Exit status: always 0; the decision travels in the JSON. The
    verdict is deny or silence: this hook never asks.

CONFIGURATION
    The store is named by the environment variable AIQT_STORE_ROOT or, only when that name is not set at all,
    by the legacy name ORCH_STORE_ROOT: absolute paths joined by ':'. A set but empty AIQT_STORE_ROOT means no
    store, even when the legacy name is set. Empty and relative entries, and an entry that resolves to /, are
    dropped. With no store configured the hook does nothing at all. Each store path is resolved (symbolic
    links followed), and a path is inside a store only by whole path components, so /records-old is not
    inside /records.

IN SCOPE (judged exactly)
    A small set of forms is judged; everything else is out of scope (below) and allowed. The command word is
    found past assignments, the env, sudo, time, nohup, command, exec, and timeout prefixes, and a function
    keyword's name, and is matched by its last path component (so /bin/rm counts).
    - rm, in all of GNU rm's option forms (permuted, long options by unambiguous prefix; an option rm rejects,
      or --help or --version, runs nothing). Each operand's entry counts: a file; a directory only under -r,
      -R, or --recursive, when a non-empty file lies anywhere in it (a directory that holds a store counts that
      store's content; --one-file-system does not cross into another device; --preserve-root=all skips an
      operand on another device than its parent); not `.`, `..`, or `/` (rm refuses them; `/` counts with
      --no-preserve-root). The entry's own last component is not followed (removing a symbolic link removes
      only the link), except with a trailing slash, which follows it and removes only a directory.
    - A redirection that truncates its file: `>`, `>|`, `&>`, and `>& FILE`, with or without a descriptor
      number or {name} before it, on any command, on a compound command, or on none (`: > f`, `> f`),
      including inside a substitution or a pipeline. Appending and read-write redirections (`>>`, `&>>`, `<>`)
      and reads do not count. The write is followed through links.
    - cp SOURCE DEST and mv SOURCE DEST, with exactly two operands, no option, and no glob, when DEST is not a
      directory: cp writes over DEST through a link, mv replaces the DEST entry. Not when SOURCE is a directory
      or the same file as DEST (both commands refuse).
    - truncate -s 0 (or -s0, --size=0, --size 0, or a unique prefix of --size, the zero written as any number
      of 0 digits) with file operands and no other option. The write is followed through links.
    - tee with file operands and no option (and no `-` operand): each file is truncated.
    The command is read in order and flat: a command inside an if, a loop, a case arm, a `{ }` group, a `( )`
    subshell, a function body, a pipeline, a `$( )` command substitution, a process substitution, or an
    unquoted here-document's `$( )` substitution counts as if it ran, whether or not it would. Text bash does
    not run as a command does not count: an echo's argument, a quoted string, a comment, a here-document's
    body, an array assignment's list (`a=(rm x)`, whose substitutions are still read), and the operators of a
    `[[ ... ]]` conditional or a `(( ... ))` arithmetic command (`>` there compares).

    DENY when a judged target, at the moment the hook runs, is inside a store, not inside a `.git` directory
    there, and is an existing regular file holding at least one byte. Silent when the target does not exist
    (so a file the command itself creates before destroying it never fires), is empty, is not a regular file,
    is outside every store, or lies inside a `.git` directory within a store (git's own files, such as a stale
    index.lock); a `.git` directory removed whole is judged.

OPERANDS AND THE DIRECTORY
    An operand is read from the command text: a word with its quoting removed; a leading `~` or `~/`; a parameter
    expansion ($NAME or ${NAME}) of a variable given a readable value earlier in the command by a plain assignment
    (NAME=..., or export, local, declare, typeset, or readonly NAME=... with no option), else of AIQT_STORE_ROOT,
    ORCH_STORE_ROOT, HOME, or CLAUDE_PROJECT_DIR from the hook's own environment, or of PWD; inside a `for NAME in
    LIST` loop's body, and after a plain one, NAME stands for each item of LIST (a loop that is not plain leaves
    NAME unknown after it); and an unquoted glob (`*`, `?`, `[...]`), matched completely against the disk as bash
    would when the hook runs. A relative operand is resolved against the working directory: the payload's cwd,
    followed only through a plain `cd DIR` with a literal DIR (no variable, `~`, or glob in it), as bash's logical
    cd moves (`..` removed textually, every directory on the way existing; a DIR that does not exist, is empty (`cd
    ""` fails from bash 5.2 on), or is not a directory leaves the directory where it was, and what follows it with
    `&&` does not run). An assignment or a cd is plain when it is in the command's top-level text, outside any if,
    loop, case, `{ }` group, `( )` subshell, or function body, first in its and-or list (after `;`, a newline, `&`,
    or the start), and not a pipeline stage or an asynchronous command. Any other directory change (cd anywhere
    else, cd with an option, a non-literal or `-` target, pushd, popd, env -C, sudo -D, sudo -i) makes the directory
    unknown from then on, and any other assignment (or read, unset, printf -v, and the like) its variable; eval,
    source, and `.` make both unknown. A relative operand in an unknown directory, and an unknown variable, are not
    read.

OUT OF SCOPE (allowed, by design)
    Each of these can destroy a record and is not judged at all: find (any form, -delete and -exec rm
    included), rsync, git's clean, rm, checkout, and restore (guarded elsewhere in the pack), unlink, shred,
    install, dd, sed -i, perl -i, ln -sf over a file, an interpreter one-liner, an editor; cp and mv with any
    option (-n, -u, -b, --backup, -t, -T, -f, --exchange, and the like), with a glob, with more or fewer than
    two operands, or onto a directory; truncate with any size but zero or with any other option; tee with any
    option; env -S strings; eval, `bash -c` and `sh -c` strings, xargs, a script file, and aliases and
    functions defined in an earlier call; and a command run through a launcher other than the prefixes named
    above (nice, stdbuf, busybox, a shell). Also out of scope, and allowed: an rm, cp, mv, truncate, or tee
    whose argument words include one made by an expansion (a variable, `~`, or a glob) that expands to a word
    beginning with `-` (`flag=-r; rm "$flag" DIR`, or a glob matching a file named `-rf`), since that word is
    an option; an option value given through a variable (`truncate -s "$n"`); a word that uses one `for` list
    variable more than once (`for n in a b; do rm "$n-$n.md"; done`), whose references are not correlated;
    a `>&` redirection whose target holds an expansion (`>& "$fd"`), which may name a descriptor; and array
    elements: an assignment to one (`f[0]=...`, `read f[0]`, `declare f[0]=...`) is not followed, though bash
    applies index 0 to `$f` itself, so `f` keeps its earlier value (a stale value may be over-denied, and a new
    one missed), and an array element read as an operand (`"${f[0]}"`) is not read. Moving
    a record out of the store is not a loss this hook judges.

OPT-OUT
    For a destruction that is meant, end the command with a real shell comment whose text is
    `# record-rm-ok: <reason>`, the reason not empty, for example:
        rm DIR/old.md  # record-rm-ok: superseded by new.md
    It must follow the command's last token on the same line after a blank, be unquoted, and be the last
    non-blank content of the command; a marker inside quotes, on a here-document's body line, or followed by
    another command does not count. The marker is an explicit, recorded attestation: it does not prove the
    file was read, and it does not verify a restore path.

THREAT MODEL
    This is an accidental-habit guard, not a security boundary. The actor is a well-meaning assistant that
    destroys or recreates a record without first looking at what it holds; nothing here resists a caller that
    sets out to destroy a file. So every internal error, every input the reading cannot follow, and every
    malformed payload fails OPEN: no output, exit 0. The hook also stays silent for a verification worker
    process (AIQT_HOOKS_WORKER set to "1"; or the legacy names, ORCH_WORKER set to "1" or ORCH_VERIFY_OWNER
    present at all, even empty), for a tool_name other than Bash, for an event other than PreToolUse, for a
    bad argv, and for a stdin that is absent, unreadable, over 16 MiB, incomplete after 2 seconds, not JSON,
    or not an object. A subagent's call (a payload carrying agent_id) is judged like any other: a subagent
    that destroys a record loses the same data. Work is bounded: a command over 64 KiB of UTF-8 is not judged
    at all, the reading stops at 4000 tokens and 32 levels of nesting (past either it fails open), at most 128
    target words are read, a word is read in time linear in its length, walks, glob expansion, and the
    values a for list multiplies together share one budget of 2000 entries, symbolic links not followed, and
    the characters expansions produce (a variable's value, however it grew, and each copy of it) are held
    to 262144 per command.
    The hook never runs the command or any part of it, and it starts no process: it only inspects the
    filesystem (lstat, stat, realpath, and directory listings).

RESIDUAL COVERAGE.
    Beyond the out-of-scope forms above, not caught, silently allowed: an operand the hook cannot read (a
    variable that is unknown or never assigned readably, a function's arguments, an array element, an
    arithmetic or substitution-built value, an unquoted variable whose value holds a blank or a glob
    character, brace expansion (`{a,b}`), a glob bracket opening with `^` or holding a `[:class:]`, `[=c=]`,
    or `[.c.]` form, an extended glob, and a removal inside a backtick substitution, which the vendored lexer
    does not read); any relative operand after a directory change the hook does not follow (see above, and
    CDPATH, which is not read); more than 128 target words in one command; and what lies past the
    2000-entry budget (a store tree larger than that is not fully examined, a glob past it is matched only
    so far, a word whose expansion would pass it is not read at all, and what was not reached is allowed),
    and past the 262144-character expansion budget (a word or assignment whose expansion would pass it is not
    read; a word that expands nothing is still judged).
    An argument word whose value cannot be read is taken as an operand, not an option, so a hidden --help
    there may be over-denied. The hook's environment may differ from the Bash tool's
    shell environment (for example a CLAUDE_ENV_FILE), so a variable or cwd the hook reads may not be what
    the shell expands. Between the check and the run another process may create or fill the file, and a
    file that is absent or empty when the hook runs, filled by one line of the command and destroyed by a
    later line, is allowed by design. No restore path is looked for: a file with a committed, unchanged copy
    is denied like any other, as are an unreachable command (`false && rm`), a truncation that noclobber
    would refuse, and an interactive rm (-i, -I), judged as if every prompt were answered yes; the opt-out
    marker answers each. A command over 64 KiB, or one the lexer cannot read (an unbalanced quote), is
    allowed although bash runs its complete earlier lines. A verification worker process (the skip above)
    is not judged at all, so a worker that destroys a record is not caught. Option parsing follows GNU rm's
    permuted form; POSIXLY_CORRECT and other implementations are not modelled. A target the hook cannot
    lstat or list is not judged. The vendored lexer's own disclosed limits carry over: quotes, escapes,
    comments, and here-document bodies are honoured, and a construct it cannot follow yields no verdict.

Self-test: python3 -I -S -B record-remove-check.py --self-test
    The reference hooks the vendored blocks were copied from are looked up in the directory named by the
    G_REF_DIR environment variable, else in this file's own directory; when a reference is absent its
    byte-identity check reports SKIPPED, never a pass. The differential check runs real bash (a root-owned
    /usr/bin/bash or /bin/bash) on throwaway fixture trees only, and is skipped when no such bash exists.
"""

import json
import os
import re
import select
import stat
import sys
import time

HOOK_ID = "record-remove-check"

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
    "bounded": ("inplace-edit-verify.py", 980, 984,
        "ccc2a9c8d6dcaec87d96a5b01b8ed06209c962c850ec3b1984e4f9b879aa1035"),
    "trusted-bash": ("inplace-edit-verify.py", 1398, 1415,
        "f1826690bf62a7817d7a53414e3d3752a51b9977ac4356d72fcf9e8fb6f411de"),
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

# BEGIN VENDOR bounded from inplace-edit-verify.py:980-984
def _bounded(cmd):
    return cmd[:_SCAN_LIMIT].encode("utf-8", "surrogatepass")[:_SCAN_LIMIT].decode("utf-8", "ignore")
# END VENDOR

# BEGIN VENDOR trusted-bash from inplace-edit-verify.py:1398-1415
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

# This hook's own reading.
_MOVERS = frozenset(("cd", "pushd", "popd"))
_DECLARERS = frozenset(("export", "local", "declare", "typeset", "readonly"))
_READERS = frozenset(("read", "mapfile", "readarray", "getopts", "unset", "let"))  # may set a NAME
_OPAQUE = frozenset(("eval", "source", "."))  # may change anything: directory and variables become unknown
_LEAD_WORDS = frozenset(("!", "then", "else", "elif", "do"))
_TRUNCATING = frozenset((">", ">|", "&>"))  # and `>& FILE` with no descriptor number (see _truncates)
_EXPAND_NAMES = frozenset(("AIQT_STORE_ROOT", "ORCH_STORE_ROOT", "HOME", "CLAUDE_PROJECT_DIR"))
_NAME_AT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_ASSIGN_HEAD_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)(\+?)=")
_SPLIT_OR_GLOB = frozenset(" \t\n*?[")  # in an unquoted expansion's value: split or globbed, so not read
_GLOB_CHARS = frozenset("*?[")
_ESCAPED_GLOB_RE = re.compile(r"\[([*?\[])\]")
_ZERO_RE = re.compile(r"^0+$")
_HINT_RE = re.compile(r"rm|cp|mv|tee|trunc")
_DEQUOTE = str.maketrans("", "", "'\"\\")
_MARKER_RE = re.compile(r"\A[ \t]+#[ \t]*record-rm-ok:[ \t]*\S[^\n]*\Z")
_MAX_OPERANDS = 128  # target words read in one command (past it: the reading stops, what was found so far stands)
_MAX_WALK = 2000  # directory entries examined, and expansion values made, in one command
_MAX_EXPANDED = 1 << 18  # characters expansions may make in one command (past it an expanding word is not read)
_MAX_WRAPS = 8  # nested timeout prefixes and function keywords followed to the command word
_TIMEOUT_ARG_OPTS = frozenset(("-s", "-k", "--signal", "--kill-after"))
_SHOW_MAX = 200  # characters of a path quoted in the reason
# rm's options as GNU rm parses them: short letters, and long names (an unambiguous prefix of one counts as it)
# with their kind: flag (no value), opt (an optional `=value`), exit (rm runs nothing).
_RM_SHORT = "fiIrRdv"
_RM_LONG = {"force": "flag", "interactive": "opt", "one-file-system": "flag", "no-preserve-root": "flag",
            "preserve-root": "opt", "recursive": "flag", "dir": "flag", "verbose": "flag", "help": "exit",
            "version": "exit"}

_LEAD = ("record-remove-check (a destructive operation requires a verified restore path; preserve uncommitted "
         "work): ")
_REASON = (_LEAD + "this command {action}, an existing file under the working-record store {root}. It holds "
           "{size} (modified {mtime}), and nothing here confirms a copy it could be restored from, so its content "
           "could be lost for good. First read it in its own call (for example: cat -- {example}) and decide "
           "whether it is a record to keep. To add to it, append (>>) instead of recreating it, or use the Edit "
           "or Write tools. To remove or replace it deliberately, end the command with the comment "
           "'# record-rm-ok: <reason>'.")


def _is_worker(env):
    """True for a verification worker process: AIQT_HOOKS_WORKER == "1" first, then the legacy names,
    ORCH_WORKER == "1" or ORCH_VERIFY_OWNER present (any value, even empty)."""
    if env.get("AIQT_HOOKS_WORKER") == "1":
        return True
    return env.get("ORCH_WORKER") == "1" or "ORCH_VERIFY_OWNER" in env


def _base(word):
    return word[word.rfind("/") + 1:]


def _show(value):
    """A path quoted in the reason, long values cut."""
    return value if len(value) <= _SHOW_MAX else value[:_SHOW_MAX] + "..."


def _bare(value):
    return not isinstance(value, _NotKeyword)


def _store_roots(env):
    """The configured store roots, each resolved: AIQT_STORE_ROOT when set at all (set but empty: none), else
    ORCH_STORE_ROOT; entries split on ':', empty and relative ones and any that resolve to / dropped."""
    raw = env.get("AIQT_STORE_ROOT") if "AIQT_STORE_ROOT" in env else env.get("ORCH_STORE_ROOT", "")
    if not isinstance(raw, str) or "\0" in raw:
        return []
    roots = []
    for part in raw.split(":"):
        if not part.startswith("/"):
            continue
        real = os.path.realpath(part)
        if real.strip("/") and real not in roots:
            roots.append(real)
    return roots


def _within(path, root):
    """True when path is root or lies under it, by whole path components."""
    return path == root or path.startswith(root.rstrip("/") + "/")


def _in_git(path, root):
    """True when path lies inside (not at) a `.git` directory within root: git's own files, not records."""
    parts = path[len(root):].strip("/").split("/")
    return ".git" in parts[:-1]


# The command text as bash reads it: records, and the parts that are not commands.

def _command_start(out):
    """True when the next token of a stream (after the tokens in out) is in command position."""
    if not out:
        return True
    last = out[-1]
    if last[0] == "nl" or (last[0] == "op" and last[1] not in _REDIRECTS):
        return True
    return last[0] == "w" and not last[2] and last[1] in (_LEAD_WORDS | {"{", "}", "if", "while", "until", "time"})


def _inert(toks):
    """toks with what bash does not run as a command or redirection made inert: a `[[ ... ]]` conditional's and a
    `(( ... ))` arithmetic command's operators dropped (`>` there compares, it does not redirect), and an array
    assignment's `( ... )` list reduced to one word that keeps the list's substitutions (`a=(rm x)` is data)."""
    out, i, n = [], 0, len(toks)
    while i < n:
        t = toks[i]
        if t[0] == "w" and not t[2] and t[1] == "[[" and _command_start(out):
            j = i + 1
            while j < n and not (toks[j][0] == "w" and not toks[j][2] and toks[j][1] == "]]"):
                j += 1
            out.extend(u for u in toks[i:j + 1] if u[0] == "w")
            i = j + 1
            continue
        arith = (t[0] == "op" and t[1] == "(" and i + 1 < n and toks[i + 1][0] == "op" and toks[i + 1][1] == "("
                 and toks[i + 1][3] == t[4] and (_command_start(out) or (out[-1][0] == "w" and out[-1][1] == "for")))
        array = (t[0] == "w" and not t[2] and t[1].endswith("=") and _ARRAY_ASSIGN_RE.match(t[1]) and i + 1 < n
                 and toks[i + 1][0] == "op" and toks[i + 1][1] == "(" and toks[i + 1][3] == t[4])
        if arith or array:
            j, depth, subs = i + 1, 0, list(t[5])
            while j < n:
                u = toks[j]
                if u[0] == "op" and u[1] == "(":
                    depth += 1
                elif u[0] == "op" and u[1] == ")":
                    depth -= 1
                    if depth == 0:
                        break
                elif u[0] == "w":
                    if arith:
                        out.append(u)
                    else:
                        subs.extend(u[5])
                j += 1
            if array:
                out.append(("w", t[1] + _SUB, True, t[3], toks[min(j, n - 1)][4], subs))
            i = j + 1
            continue
        out.append(t)
        i += 1
    return out


def _body_offsets(toks, text, off):
    """{index in toks of a here-document delimiter word: where its body starts in text}. The stream's own
    positions are offset by off in text. The bodies of a line's here-documents follow that line's newline in
    order, each running to its delimiter line (leading tabs stripped for `<<-`), as the vendored lexer reads them;
    the lexer lexes an unquoted body's substitutions from the body's own text, so their positions count from
    the body's start."""
    out, pending, n = {}, [], len(text)
    for k, t in enumerate(toks):
        if t[0] == "op" and t[1] in _HEREDOC_OPS and k + 1 < len(toks) and toks[k + 1][0] == "w":
            pending.append((k + 1, toks[k + 1][1], _HEREDOC_OPS[t[1]]))
        elif t[0] == "nl" and pending:
            i = off + t[4]
            for at, delim, strip in pending:
                out[at] = i
                while i < n:
                    j = text.find("\n", i)
                    line = text[i:] if j < 0 else text[i:j]
                    i = n if j < 0 else j + 1
                    if (line.lstrip("\t") if strip else line) == delim:
                        break
            pending = []
    return out


def _records(toks, text, off):
    """The simple-command records of one token stream (its positions offset by off in text), each (words, subs,
    op, redirs): words as (value, token) pairs, the value a _NotKeyword when the word was quoted or followed a
    redirection (so bash reads no keyword in it); subs every substitution stream the record runs, as (stream,
    offset) pairs (its words', its redirection targets', and an unquoted here-document body's); op the operator
    that ends it (None at the end); and redirs each redirection as (fd, op, target token or None), fd the
    descriptor word written against the operator (a number or {name})."""
    toks = _inert(toks)
    bodies = _body_offsets(toks, text, off) if any(t[1] in _HEREDOC_OPS for t in toks if t[0] == "op") else {}
    recs, words, subs, redirs, fd, i, n = [], [], [], [], None, 0, len(toks)
    while True:
        if i < n:
            tok = toks[i]
            kind, value, quoted, start, end, tsubs = tok
            if kind == "w":
                nxt = toks[i + 1] if i + 1 < n else None
                if (nxt and nxt[0] == "op" and nxt[3] == end and nxt[1] in _REDIRECTS and not quoted
                        and (_digits(value) or _FD_VAR_RE.match(value))):
                    fd, i = value, i + 1
                    continue
                subs.extend((sub, off) for sub in tsubs)
                words.append((_NotKeyword(value) if quoted or redirs else value, tok))
                i += 1
                continue
            if kind == "op" and value in _REDIRECTS:
                target = None
                if i + 1 < n and toks[i + 1][0] == "w":
                    target = toks[i + 1]
                    subs.extend((sub, bodies.get(i + 1, off)) for sub in target[5])
                    i += 2
                else:
                    i += 1
                redirs.append((fd, value, target))
                fd = None
                continue
        recs.append((words, subs, toks[i][1] if i < n else None, redirs))
        if i >= n:
            break
        words, subs, redirs, fd = [], [], [], None
        i += 1
    return recs


def _timeout_command(values, k):
    """Index of the command timeout runs, its options starting at values[k], or None when it runs none."""
    n = len(values)
    while k < n and values[k].startswith("-") and values[k] != "-":
        a = values[k]
        k += 1
        if a == "--":
            break
        if a in _TIMEOUT_ARG_OPTS:
            k += 1
    k += 1  # the duration
    return k if k < n else None


def _target(values, op):
    """Index of the command word as _command_target finds it, stepping past timeout prefixes and a function
    keyword with its name (`function f { rm x; }` runs rm when called), or None."""
    found, base = _command_target(values, op), 0
    for _ in range(_MAX_WRAPS):
        if found is None:
            return None
        t = base + found[0]
        w = values[t]
        if _base(w) == "timeout":
            k = _timeout_command(values, t + 1)
        elif w == "function" and _bare(w):
            k = t + 2
        else:
            return t
        if k is None or k >= len(values):
            return None
        found, base = _command_target(values[k:], None), k
    return None


def _long(arg, table):
    """(kind, name, attached value or None) for a long option word as getopt_long reads it (an exact name, or an
    unambiguous prefix of one), or (None, None, None) for an unknown or ambiguous one."""
    name, eq, value = arg[2:].partition("=")
    hits = [name] if name in table else [k for k in table if k.startswith(name)] if name else []
    if len(hits) != 1:
        return None, None, None
    return table[hits[0]], hits[0], value if eq else None


_RM_VALUES = {"interactive": {"never": "never", "no": "never", "none": "never", "once": "once", "always": "always",
                               "yes": "always"}}


def _rm_value_ok(name, value):
    """True when rm accepts value for the long option name: --preserve-root takes exactly `all`; --interactive
    takes a word from its list, or a prefix of words that all mean the same (as GNU argmatch reads it)."""
    if name == "preserve-root":
        return value == "all"
    table = _RM_VALUES.get(name, {})
    if value in table:
        return True
    meanings = {m for w, m in table.items() if value and w.startswith(value)}
    return len(meanings) == 1


def _rm_options(values):
    """({option name or letter: [values]}, operand indexes) for rm's permuted argument words, or None when rm
    would reject them or run nothing (--help, --version)."""
    opts, ops, k, n, ended = {}, [], 0, len(values), False
    while k < n:
        a = values[k]
        k += 1
        if ended or not a.startswith("-") or a == "-":
            ops.append(k - 1)
        elif a == "--":
            ended = True
        elif a.startswith("--"):
            kind, name, value = _long(a, _RM_LONG)
            if kind is None or kind == "exit" or (value is not None and kind == "flag"):
                return None
            if value is not None and not _rm_value_ok(name, value):
                return None  # rm rejects the value
            opts.setdefault(name, []).append(value)
        else:
            for ch in a[1:]:
                if ch not in _RM_SHORT:
                    return None
                opts.setdefault(ch, []).append(None)
    return opts, ops


# Reading a word: its source text parsed into pieces, expanded against the state, globbed against the disk.

def _pieces(raw, value, assign):
    """[(kind, text)] for a word's source text, adjacent literal text joined: ("lit", s) literal text, ("magic",
    c) an unquoted glob character, ("var", name) and ("qvar", name) a parameter expansion, unquoted or
    double-quoted (`~` reads as HOME); None when the word holds anything else that expands (a substitution, a
    backtick, another ${...} form, an unquoted brace, an ANSI-C or locale string), or when the pieces do not
    rebuild the lexer's own value for the word (its text is not at its offsets). assign: an assignment word,
    whose value is neither split nor globbed and may start with `~`. Linear in the word's length."""
    out, lit, run, i, n = [], [], [], 0, len(raw)

    def flush():
        if run:
            out.append(("lit", "".join(run)))
            del run[:]
    tilde = {0}
    if assign:
        m = _ASSIGN_HEAD_RE.match(raw)
        if not m or m.group(2):
            return None
        tilde.add(m.end())
    while i < n:
        c = raw[i]
        if c == "~" and i in tilde and raw[i + 1:i + 2] in ("", "/"):
            flush()
            out.append(("var", "HOME"))
            lit.append("~")
            i += 1
        elif c == "'":
            j = raw.find("'", i + 1)
            if j < 0:
                return None
            run.append(raw[i + 1:j])
            lit.append(raw[i + 1:j])
            i = j + 1
        elif c == '"':
            i += 1
            while True:
                if i >= n:
                    return None
                c = raw[i]
                if c == '"':
                    i += 1
                    break
                if c == "\\":
                    nxt = raw[i + 1:i + 2]
                    if not nxt:
                        return None
                    if nxt in '$`"\\':
                        run.append(nxt)
                        lit.append(nxt)
                    elif nxt != "\n":
                        run.append("\\" + nxt)
                        lit.append("\\" + nxt)
                    i += 2
                elif c == "$":
                    got = _param_name(raw, i)
                    if got is None:
                        return None
                    flush()
                    out.append(("qvar", got[0]))
                    lit.append(raw[i:got[1]])
                    i = got[1]
                elif c == "`":
                    return None
                else:
                    run.append(c)
                    lit.append(c)
                    i += 1
        elif c == "\\":
            nxt = raw[i + 1:i + 2]
            if not nxt:
                return None
            if nxt != "\n":
                run.append(nxt)
                lit.append(nxt)
            i += 2
        elif c == "$":
            got = _param_name(raw, i)
            if got is None:
                return None
            flush()
            out.append(("qvar" if assign else "var", got[0]))
            lit.append(raw[i:got[1]])
            i = got[1]
        elif c == "`" or (c == "{" and not assign):
            return None
        elif c in _GLOB_CHARS and not assign:
            flush()
            out.append(("magic", c))
            lit.append(c)
            i += 1
        else:
            run.append(c)
            lit.append(c)
            i += 1
    flush()
    return out if "".join(lit) == value else None


def _param_name(raw, i):
    """(name, end) for the parameter expansion at raw[i] (a `$`): $NAME or ${NAME}; None for any other form."""
    if raw.startswith("{", i + 1):
        j = raw.find("}", i + 2)
        if j < 0 or not _NAME_RE.match(raw[i + 2:j]):
            return None
        return raw[i + 2:j], j + 1
    m = _NAME_AT_RE.match(raw, i + 1)
    return (m.group(), m.end()) if m else None


def _lookup(name, ctx, state):
    """The values a parameter may hold here ([None] when it cannot be read): a literal assignment made earlier in
    the command, or a `for` loop's list inside its body; PWD from the logical working directory; or one of
    _EXPAND_NAMES from the hook's environment."""
    if name in state["vars"]:
        return state["vars"][name]
    if state.get("forgot"):
        return [None]  # every variable was made unknown, and this one not set since
    if name == "PWD":
        return [state["cwd"]]
    value = ctx["env"].get(name) if name in _EXPAND_NAMES else None
    return [value if isinstance(value, str) else None]


def _gesc(s):
    """s with its glob characters bracketed, so fnmatch reads them literally (as glob.escape does)."""
    return re.sub(r"([*?\[])", r"[\1]", s)


def _glob_unread(component):
    """True when a glob component (literal parts bracketed by _gesc) holds a bracket expression fnmatch reads
    differently from bash: one opening with `^`, or holding a `[:class:]`, `[=c=]`, or `[.c.]` form."""
    s = _ESCAPED_GLOB_RE.sub("", component)
    i, n = 0, len(s)
    while i < n:
        if s[i] != "[":
            i += 1
            continue
        j = i + 1
        if j < n and s[j] in "!^":
            if s[j] == "^":
                return True
            j += 1
        if j < n and s[j] == "]":
            j += 1  # a leading `]` is a member, not the end
        while j < n and s[j] != "]":
            if s[j] == "[" and j + 1 < n and s[j + 1] in ":=.":
                return True
            j += 1
        i = j + 1
    return False


def _glob(pattern, ctx):
    """The existing paths an absolute glob pattern (literal parts bracketed by _gesc) matches, as bash globs it: a
    component's `*`, `?`, and `[...]` match the names in its directory, a leading `.` only when the component
    starts with one, and directories on the way are followed; a trailing slash matches directories only (through
    links) and each match keeps it. Complete, bounded only by the shared entry budget:
    past it the matches found so far are returned. None when a bracket form is not read (see _glob_unread)."""
    import fnmatch
    parts = [p for p in pattern.split("/") if p]
    if any(_glob_unread(p) for p in parts):
        return None
    dirs_only = pattern.endswith("/")  # `DIR*/` matches directories (through links) and keeps its slash
    current = ["/"]
    for k, part in enumerate(parts):
        last, plain = k == len(parts) - 1, _ESCAPED_GLOB_RE.sub("", part)
        nxt = []
        if not any(ch in plain for ch in _GLOB_CHARS):
            name = _ESCAPED_GLOB_RE.sub(r"\1", part)
            nxt = [os.path.join(d, name) for d in current]
            nxt = [p for p in nxt if (os.path.isdir(p) if dirs_only else os.path.lexists(p))] if last else nxt
        else:
            for d in current:
                try:
                    it = os.scandir(d)
                except (OSError, ValueError):
                    continue
                with it:
                    for e in it:
                        ctx["budget"] -= 1
                        if ctx["budget"] < 0:
                            return sorted(p + "/" if dirs_only else p for p in nxt) if last else []
                        if e.name.startswith(".") and not plain.startswith("."):
                            continue
                        if fnmatch.fnmatchcase(e.name, part) and ((last and not dirs_only) or os.path.isdir(e.path)):
                            nxt.append(e.path)
        current = nxt
        if not current:
            return []
    return sorted(p + "/" if dirs_only else p for p in current)


def _expand(tok, ctx, text, off, state, phys, assign=False):
    """The values a word token may expand to (None members for what cannot be read, [] when the word cannot be read
    at all): its parameters from the state (a `for` list gives one value per item), each unquoted glob matched
    against the disk, a relative pattern in the directory phys (its matches relative names; no match leaves the
    word's text, as bash does). For an assignment word, the assigned value. Each value is built as a list of parts
    joined once, so the work is linear in the word's length; every piece's work beyond a single value is counted
    against the shared budget, and when the budget runs out the word is not read (out of work, it is allowed, never
    judged on part of its values); so too when the characters its expansions would make (each value's text, and each
    copy of the text before it) exceed what is left of the per-command expansion budget, charged before they are
    made. A word whose pieces make no characters is never refused by it. A word that uses one variable holding
    several values (a `for` list) more than once is not read either: its references are not correlated."""
    _, value, _, start, end, subs = tok
    if subs or _SUB in value:
        return []
    pieces = _pieces(text[off + start:off + end], value, assign)
    if pieces is None:
        return []
    uses = {}  # references per variable, counted in one pass
    for kind, x in pieces:
        if kind in ("var", "qvar"):
            uses[x] = uses.get(x, 0) + 1
    if any(n > 1 and len(_lookup(x, ctx, state)) > 1 for x, n in uses.items()):
        return []  # one multi-valued variable used twice: out of scope
    cands = [[[], [], False, 0]]  # each: text parts, pattern parts, holds a glob, length so far
    for kind, s in pieces:
        vals = _lookup(s, ctx, state) if kind in ("var", "qvar") else ()
        work = len(cands) * max(1, len(vals))
        if work > 1:
            ctx["budget"] -= work
            if ctx["budget"] < 0:
                return []  # out of work: the word is not read
        live = [c for c in cands if c is not None]
        if vals:  # each value's text, and for each value past the first a copy of the text so far
            added = sum(len(v) for v in vals if v)
            made = sum(added + (len(vals) - 1) * c[3] for c in live)
        else:  # a literal costs nothing in a single value: it is the command's own text
            made = len(s) * len(live) if len(live) > 1 else 0
        if made:  # charged before the characters are made; a piece that makes none is never refused
            ctx["bytes"] -= made
            if ctx["bytes"] < 0:
                return []  # out of expansion budget: the word is not read
        if kind in ("lit", "magic"):
            esc = _gesc(s) if kind == "lit" else s
            for c in live:
                c[0].append(s)
                c[1].append(esc)
                c[2] = c[2] or kind == "magic"
                c[3] += len(s)
            continue
        escs = [None if v is None else _gesc(v) for v in vals]
        nxt = []
        for c in cands:
            for idx, v in enumerate(vals):
                if c is None or v is None or (kind == "var" and any(ch in _SPLIT_OR_GLOB for ch in v)):
                    nxt.append(None)
                    continue
                d = c if idx == len(vals) - 1 else _fork(c)  # the last value extends c
                d[0].append(v)
                d[1].append(escs[idx])
                d[3] += len(v)
                nxt.append(d)
        cands = nxt
    out = []
    for c in cands:
        if c is None or not c[2]:
            out.append(None if c is None else "".join(c[0]))
            continue
        pattern, prefix = "".join(c[1]), ""
        if not pattern.startswith("/"):
            if phys is None:
                continue
            prefix = phys.rstrip("/") + "/"
            pattern = _gesc(prefix) + pattern
        got = _glob(pattern, ctx)
        if got is None:
            out.append(None)
        else:  # a relative pattern's matches are relative names, as bash gives them (so `-f` reads as an option)
            out.extend([m[len(prefix):] for m in got] or ["".join(c[0])])
    if assign:
        head = _ASSIGN_HEAD_RE.match(value).end()
        out = [None if v is None else v[head:] for v in out]
    return out


def _fork(c):
    """A copy of an expansion candidate, for one more value of a variable (its cost is charged by the caller)."""
    return [list(c[0]), list(c[1]), c[2], c[3]]


def _has_glob(tok, text, off):
    """True when a word token holds an unquoted glob character."""
    pieces = _pieces(text[off + tok[3]:off + tok[4]], tok[1], False) if not tok[5] else None
    return bool(pieces) and any(kind == "magic" for kind, _ in pieces)


def _paths(tok, ctx, text, off, state, phys):
    """The absolute paths a target word names (a relative one joined to the physical directory phys, when it is
    known); [] when it cannot be read or the target bound is passed."""
    ctx["count"] += 1
    if ctx["count"] > _MAX_OPERANDS:
        ctx["stop"] = True
        return []
    out = []
    for v in _expand(tok, ctx, text, off, state, phys):
        if not v or "\0" in v:
            continue
        if v.startswith("/"):
            out.append(v)
        elif phys is not None:
            out.append(phys.rstrip("/") + "/" + v)
    return out


# What a target holds, and the hits.

def _hit(kind, path, root, st, **extra):
    hit = {"kind": kind, "path": path, "root": root, "size": st.st_size, "mtime": st.st_mtime}
    hit.update(extra)
    return hit


def _files_under(top, ctx, dev=None):
    """(path, stat) of each regular file under directory top, symbolic links not followed (and, with dev, no
    directory on another device entered), while the shared entry budget lasts (past it the walk ends quietly:
    what it did not reach is not judged)."""
    stack = [top]
    while stack:
        d = stack.pop()
        try:
            it = os.scandir(d)
        except (OSError, ValueError):
            continue
        with it:
            for e in it:
                ctx["budget"] -= 1
                if ctx["budget"] < 0:
                    return
                try:
                    if e.is_dir(follow_symlinks=False):
                        if dev is None or e.stat(follow_symlinks=False).st_dev == dev:
                            stack.append(e.path)
                    elif e.is_file(follow_symlinks=False):
                        yield e.path, e.stat(follow_symlinks=False)
                except (OSError, ValueError):
                    continue


def _same_device_path(top, below, dev):
    """True when every directory from top down to below (both resolved, below under top) is on device dev."""
    path = top.rstrip("/")
    for part in below[len(path):].strip("/").split("/"):
        path = path + "/" + part
        try:
            if os.lstat(path).st_dev != dev:
                return False
        except (OSError, ValueError):
            return False
    return True


def _nonempty_under(top, ctx, dev=None):
    """The first non-empty store file under the resolved directory top, as a recursive-removal hit: top itself
    when it lies in a store (not inside `.git`), else each store root under top, walked within the budget."""
    for root in ctx["roots"]:
        if _within(top, root):
            if _in_git(top, root):
                continue
            walk = top
        elif _within(root, top) and (dev is None or _same_device_path(top, root, dev)):
            walk = root
        else:
            continue
        for path, st in _files_under(walk, ctx, dev):
            if st.st_size > 0:
                return _hit("recursive", path, root, st, entry=top)
    return None


def _store_file(path, follow, ctx):
    """(resolved path, root, stat) when path (resolved: followed through links when follow, else its parent only)
    is a non-empty regular file in a store and not inside `.git` there, else None."""
    try:
        real = os.path.realpath(path) if follow else os.path.join(os.path.realpath(os.path.dirname(path) or "/"),
                                                                  os.path.basename(path))
        st = os.stat(real) if follow else os.lstat(real)
    except (OSError, ValueError):
        return None
    if not stat.S_ISREG(st.st_mode) or st.st_size == 0:
        return None
    return next(((real, root, st) for root in ctx["roots"] if _within(real, root) and not _in_git(real, root)),
                None)


def _removal_entry(path, noroot):
    """(entry, directory only) for what rm removes for path: the entry has its parent resolved and its last
    component kept (a link is removed, not followed); with a trailing slash it is resolved whole and only a
    directory is removed (`rm -r link/` empties the linked directory; a link to a file fails). None for a last
    component `.` or `..`, and for `/` unless noroot (rm refuses them)."""
    stripped = path.rstrip("/")
    if not stripped:
        return ("/", True) if noroot else None
    cut = stripped.rfind("/")
    base = stripped[cut + 1:]
    if base in (".", ".."):
        return None
    if stripped != path:
        return os.path.realpath(stripped), True
    return os.path.join(os.path.realpath(stripped[:cut] or "/"), base), False


def _check_write(path, kind, ctx, **extra):
    """A write that replaces the content of the file path reaches (through links)."""
    got = _store_file(path, True, ctx)
    if got:
        ctx["hit"] = _hit(kind, got[0], got[1], got[2], **extra)


# The commands in scope.

def _rm(args, toks, ctx, text, off, state, phys):
    """rm, in all of GNU rm's option forms: each operand's entry; a directory only when recursive."""
    parsed = _rm_options(args)
    if parsed is None:
        return
    opts, ops = parsed
    rec = any(k in opts for k in ("r", "R", "recursive"))
    noroot = "no-preserve-root" in opts
    one_fs = "one-file-system" in opts
    all_root = "all" in (opts.get("preserve-root") or [])
    for k in ops:
        for path in _paths(toks[k], ctx, text, off, state, phys):
            found = _removal_entry(path, noroot)
            if found is None:
                continue
            entry, dir_only = found
            try:
                st = os.lstat(entry)
            except (OSError, ValueError):
                continue
            if stat.S_ISREG(st.st_mode) and not dir_only:
                got = _store_file(entry, False, ctx)
                if got:
                    ctx["hit"] = _hit("remove", got[0], got[1], got[2])
            elif stat.S_ISDIR(st.st_mode) and rec:
                if all_root:
                    try:
                        if os.lstat(os.path.dirname(entry) or "/").st_dev != st.st_dev:
                            continue  # --preserve-root=all: an operand on another device than its parent
                    except (OSError, ValueError):
                        continue
                ctx["hit"] = _nonempty_under(entry, ctx, st.st_dev if one_fs else None)
            if ctx["hit"] is not None or ctx["stop"]:
                return


def _truncate(args, toks, ctx, text, off, state, phys):
    """truncate -s 0 (or -s0, --size=0, --size 0, or a unique prefix of --size) and file operands, nothing else."""
    size, ops, k, n = None, [], 0, len(args)
    while k < n:
        a = args[k]
        k += 1
        eq = a.find("=")
        if a == "-s" or (a.startswith("--") and eq < 0 and len(a) > 2 and "size".startswith(a[2:])):
            if k >= n:
                return
            value, k = args[k], k + 1
        elif a.startswith("-s") and not a.startswith("--"):
            value = a[2:]
        elif a.startswith("--") and eq > 2 and "size".startswith(a[2:eq]):
            value = a[eq + 1:]
        elif a.startswith("-") and a != "-":
            return  # any other option: out of scope
        else:
            ops.append(k - 1)
            continue
        if size is not None or not _ZERO_RE.match(value):
            return  # a size other than zero, or given twice: out of scope
        size = value
    if size is None:
        return
    for k in ops:
        for path in _paths(toks[k], ctx, text, off, state, phys):
            _check_write(path, "zero", ctx)
            if ctx["hit"] is not None:
                return


def _tee(args, toks, ctx, text, off, state, phys):
    """tee with file operands and no option: each file is truncated and rewritten."""
    if not args or any(a.startswith("-") for a in args):
        return  # an option (or `-`): out of scope
    for k in range(len(args)):
        for path in _paths(toks[k], ctx, text, off, state, phys):
            _check_write(path, "overwrite", ctx, tool="tee")
            if ctx["hit"] is not None:
                return


def _copy_or_move(name, args, toks, ctx, text, off, state, phys):
    """cp or mv SOURCE DEST, no option and no glob, DEST not a directory: cp writes through a link at DEST, mv
    replaces the DEST entry. A directory SOURCE is refused by both (cp needs -r; mv cannot put a directory over a
    file), and SOURCE and DEST the same file is refused."""
    if len(args) != 2 or any(a.startswith("-") and a != "-" for a in args):
        return  # an option, or other than two operands: out of scope
    if _has_glob(toks[0], text, off) or _has_glob(toks[1], text, off):
        return
    srcs = _paths(toks[0], ctx, text, off, state, phys)
    for dest in _paths(toks[1], ctx, text, off, state, phys):
        if dest.endswith("/") or os.path.isdir(dest):
            continue  # a directory destination: out of scope
        for src in srcs:
            if os.path.isdir(src) or _same(src, dest):
                continue
            if name == "cp":
                _check_write(dest, "overwrite", ctx, tool="cp")
            else:
                got = _store_file(dest, False, ctx)
                if got:
                    ctx["hit"] = _hit("replace", got[0], got[1], got[2])
            if ctx["hit"] is not None:
                return


def _same(a, b):
    try:
        return os.path.samefile(a, b)
    except (OSError, ValueError):
        return False


# The directory and the variables, read in order.

def _launch(values, t):
    """(directory changed, string split) for the env and sudo prefixes before the command word at t: env
    -C/--chdir and sudo -D/--chdir or -i/--login run the command elsewhere; env -S/--split-string makes its own
    command from a string (not read)."""
    moved = split = False
    k = 0
    while k < t:
        name = _base(values[k])
        k += 1
        if name not in ("env", "sudo"):
            continue
        arg_letters = "CSufa" if name == "env" else "ugpCDrtUTR"
        while k < t and values[k].startswith("-") and values[k] != "-":
            a = values[k]
            k += 1
            if a == "--":
                break
            if a.startswith("--"):
                long_name, eq, _ = a[2:].partition("=")
                full = [w for w in ("chdir", "split-string", "login", "user", "unset", "file", "argv0", "group",
                                    "prompt", "role", "type", "other-user", "host", "close-from", "command-timeout",
                                    "chroot") if long_name and w.startswith(long_name)]
                full = full[0] if len(full) == 1 else None
                if full not in (None, "login") and not eq:
                    k += 1
                moved = moved or full == "chdir" or (full == "login" and name == "sudo")
                split = split or (full == "split-string" and name == "env")
                continue
            for j, ch in enumerate(a[1:], 2):
                if name == "sudo" and ch == "i":
                    moved = True
                if ch in arg_letters:
                    if not a[j:]:
                        k += 1
                    moved = moved or ch == ("C" if name == "env" else "D")
                    split = split or (name == "env" and ch == "S")
                    break
    return moved, split


def _plain_cd(tok, op, ctx, text, off, state):
    """A plain top-level `cd DIR`, DIR literal: bash's logical cd. The new logical directory is DIR joined to the
    logical one with `..` removed textually; when every directory on that way exists, cd lands there (a relative
    operand after it is resolved through links as the kernel resolves it). When DIR does not exist, cd fails: the
    directory is unchanged, and the rest of an and-or list it heads with `&&` does not run (unknown until that list
    ends); so too when DIR is not a directory or is empty (`cd ""` fails from bash 5.2 on). Anything else bash's cd
    might do (its physical fallback, CDPATH) makes the directory unknown."""
    cwd = state["cwd"]
    pieces = None if tok[5] else _pieces(text[off + tok[3]:off + tok[4]], tok[1], False)
    if tok[1] == "" and pieces is not None and all(kind == "lit" for kind, _ in pieces):
        if op == "&&":  # cd "" fails (bash 5.2 and later: "null directory"): what follows with && does not run
            state["held"], state["cwd"] = (cwd,), None
        return
    if not pieces or any(kind != "lit" for kind, _ in pieces):
        state["cwd"] = None  # not a literal target (a variable, `~`, a glob): not followed
        return
    vals = _expand(tok, ctx, text, off, state, cwd)
    if len(vals) != 1 or not vals[0] or (cwd is None and not vals[0].startswith("/")):
        state["cwd"] = None
        return
    v = vals[0]
    joined = v if v.startswith("/") else cwd.rstrip("/") + "/" + v
    path, ok = "", True
    for part in joined.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            ok = ok and os.path.isdir(path or "/")
            path = path[:path.rfind("/")]
        else:
            path = path + "/" + part
    logical = path or "/"
    if ok and os.path.isdir(logical):
        state["cwd"] = logical
    elif not os.path.isdir(joined):  # not there, or not a directory: cd fails
        if op == "&&":  # cd fails: what follows it with && does not run
            state["held"], state["cwd"] = (cwd,), None
    else:
        state["cwd"] = None


def _printf_dest(a, k, args, toks, ctx, text, off, state):
    """printf -v DEST: the variable DEST names becomes unknown (NAME[i] changes NAME, since $NAME is NAME[0]). A
    DEST written with an expansion is resolved when it reads to one name; otherwise every variable becomes
    unknown, never the text of the expansion itself."""
    if a != "-v":
        dest = a[2:] if not any(ch in a for ch in "$`") and _SUB not in a else None
    elif k + 1 >= len(args):
        return
    else:
        tok = toks[k + 1]
        pieces = None if tok[5] else _pieces(text[off + tok[3]:off + tok[4]], tok[1], False)
        if pieces is not None and all(kind == "lit" for kind, _ in pieces):
            dest = args[k + 1]
        else:
            vals = _expand(tok, ctx, text, off, state, state["cwd"])
            dest = vals[0] if len(vals) == 1 and vals[0] else None
    base = None if dest is None else dest.split("[", 1)[0]
    if base is not None and _NAME_RE.match(base):
        state["vars"][base] = [None]
    else:
        _forget_all(state)


def _forget_all(state):
    """Make every variable unknown, in constant time: the values set so far are dropped, and a flag makes each
    name not set after this read as unknown (the environment's names and PWD included)."""
    state["vars"], state["forgot"] = {}, True


def _assigned(tok, ctx, text, off, state):
    """The values a literal assignment word gives its variable ([None] when it cannot be read)."""
    vals = _expand(tok, ctx, text, off, state, state["cwd"], assign=True)
    return vals if len(vals) == 1 and vals[0] is not None else [None]


def _command(words, op, ctx, text, off, state, plain):
    """Judge one simple command, and carry its directory change and assignments into the state. plain: a plain
    top-level command (see _scan), the only place a cd or an assignment is followed."""
    values = [w[0] for w in words]
    toks = [w[1] for w in words]
    lead = 0
    while lead < len(values) and _bare(values[lead]) and values[lead] in (_LEAD_WORDS | {"{", "}"}):
        lead += 1
    if lead < len(values) and all(_ASSIGN_RE.match(v) for v in values[lead:]):
        for tok in toks[lead:]:
            name = _ASSIGN_HEAD_RE.match(tok[1])
            if name:
                state["vars"][name.group(1)] = _assigned(tok, ctx, text, off, state) if plain else [None]
        return
    t = _target(values, op)
    moved, split = _launch(values, len(values) if t is None else t)
    if t is None or split:
        if moved:
            state["cwd"] = None
        return
    name, args, arg_toks = _base(values[t]), values[t + 1:], toks[t + 1:]
    if values[t] in ("for", "select") and _bare(values[t]):
        if args and _NAME_RE.match(args[0]):
            vals = [None]
            if values[t] == "for" and args[1:2] == ["in"]:
                vals = [v for tok in arg_toks[2:] for v in (_expand(tok, ctx, text, off, state, state["cwd"])
                                                             or [None])]
            state["vars"][args[0]] = vals or [None]
        return
    if name in _MOVERS:
        if plain and not moved and name == "cd" and t == lead and len(args) == 1 and not args[0].startswith("-"):
            _plain_cd(arg_toks[0], op, ctx, text, off, state)
        else:
            state["cwd"], state["held"] = None, None
        return
    if name == "printf":
        for k, a in enumerate(args):  # only printf -v NAME (or -vNAME) assigns; the rest is output
            if a == "--" or not a.startswith("-"):
                break
            if a.startswith("-v"):
                _printf_dest(a, k, args, arg_toks, ctx, text, off, state)
                break
        return
    if name in _DECLARERS or name in _READERS or name in _OPAQUE:
        opts = any(a.startswith("-") for a in args)
        for tok in arg_toks:
            m = _ASSIGN_HEAD_RE.match(tok[1]) or _NAME_AT_RE.fullmatch(tok[1])
            if m:
                key = m.group(1) if m.re is _ASSIGN_HEAD_RE else m.group()
                keep = plain and name in _DECLARERS and not opts and m.re is _ASSIGN_HEAD_RE and t == lead
                state["vars"][key] = _assigned(tok, ctx, text, off, state) if keep else [None]
        if name in _OPAQUE:
            state["cwd"], state["held"] = None, None
            _forget_all(state)
        return
    phys = None if moved else state["cwd"]
    if name in ("rm", "truncate", "tee", "cp", "mv") and _expanded_option(arg_toks, ctx, text, off, state, phys):
        return  # an option word made by an expansion: out of scope
    if name == "rm":
        _rm(args, arg_toks, ctx, text, off, state, phys)
    elif name == "truncate":
        _truncate(args, arg_toks, ctx, text, off, state, phys)
    elif name == "tee":
        _tee(args, arg_toks, ctx, text, off, state, phys)
    elif name in ("cp", "mv"):
        _copy_or_move(name, args, arg_toks, ctx, text, off, state, phys)
    if moved:
        state["cwd"] = None


def _expanded_option(toks, ctx, text, off, state, phys):
    """True when an argument word with an expansion in it (a variable, `~`, or a glob) may expand to a word
    beginning with `-`, which the command would read as an option."""
    for tok in toks:
        pieces = None if tok[5] else _pieces(text[off + tok[3]:off + tok[4]], tok[1], False)
        if pieces and any(kind != "lit" for kind, _ in pieces):
            if any(v is not None and v.startswith("-") for v in _expand(tok, ctx, text, off, state, phys)):
                return True
    return False


def _digits(value):
    """True when value is a descriptor number as bash reads one: ASCII digits only (a superscript or other
    non-ASCII digit names a file)."""
    return bool(value) and all("0" <= ch <= "9" for ch in value)


def _truncates(fd, op, target, text, off):
    """True when a redirection truncates its target file. A `>&` target that holds an expansion (a variable, a
    glob, `~`) may name a descriptor, so it is not read; a quoted literal target is a file like any other."""
    if target is None:
        return False
    if op in _TRUNCATING:
        return True
    if op != ">&" or fd is not None or _digits(target[1]) or target[1] == "-":
        return False
    pieces = None if target[5] else _pieces(text[off + target[3]:off + target[4]], target[1], False)
    return bool(pieces) and all(kind == "lit" for kind, _ in pieces)


def _scan(toks, ctx, text, off, state, top):
    """Read one token stream (its positions offset by off in text) in order and flat: each record's substitution
    streams, its truncating redirections, then its command, all as if they ran. top: the command's own top-level
    stream. A cd or an assignment is followed only when it is plain: in the top-level stream, outside any if,
    loop, case, `{ }` group, `( )` subshell, or function body, first in its and-or list (after `;`, a newline,
    `&`, or the start), and not a pipeline stage or an asynchronous command. Any other directory change makes the
    directory unknown from then on, and any other assignment its variable. Inside a `for NAME in LIST` loop's
    body NAME stands for each item of LIST; after a plain loop it still may be any of them (bash leaves the last
    item, or the one a break stopped at), after any other it is unknown. Stops at the first hit or at the
    target bound."""
    stack, prev = [], None
    for words, subs, op, redirs in _records(toks, text, off):
        if ctx["hit"] is not None or ctx["stop"]:
            return
        if prev not in ("&&", "|", "|&") and state["held"] is not None:  # the failed cd's and-or chain is over
            state["cwd"], state["held"] = state["held"][0], None
        values = [w[0] for w in words]
        j = 0
        while j < len(values) and _bare(values[j]):
            w = values[j]
            if w in ("if", "while", "until", "case", "select", "{"):
                stack.append((w, None))
            elif w == "for":
                name = values[j + 1] if j + 1 < len(values) and _NAME_RE.match(values[j + 1]) else None
                outer = top and not stack and prev in (None, ";", "\n", "&")
                stack.append(("for", (name, outer)))
                break
            elif w in ("fi", "done", "esac", "}"):
                if stack and stack[-1][0] not in ("sub", "fn"):
                    kind, info = stack.pop()
                    if kind == "for" and info[0] and not info[1]:
                        state["vars"][info[0]] = [None]  # a loop that may not run: its variable is unknown after
            elif w not in _LEAD_WORDS:
                break
            if w == "case":
                break
            j += 1
        plain = top and not stack and prev in (None, ";", "\n", "&") and op not in ("|", "|&", "&")
        for sub, sub_off in subs:
            _scan(sub, ctx, text, sub_off, state, False)
            if ctx["hit"] is not None or ctx["stop"]:
                return
        phys = state["cwd"]
        for fd, rop, target in redirs:
            if _truncates(fd, rop, target, text, off):
                for path in _paths(target, ctx, text, off, state, phys):
                    _check_write(path, "truncate", ctx, op=rop)
                if ctx["hit"] is not None or ctx["stop"]:
                    return
        if words:
            _command(words, op, ctx, text, off, state, plain)
            if ctx["hit"] is not None or ctx["stop"]:
                return
        if op == "(":
            stack.append(("sub", None) if not words else ("fn", None))
        elif op == ")" and stack and stack[-1][0] in ("sub", "fn"):
            stack.pop()
        prev = op


def _marked(text):
    """True when the command ends with the `# record-rm-ok: <reason>` comment: after the last token of the text
    (trailing blanks and newlines set aside), on that token's own line, a blank, then the marker, then nothing."""
    s = text.rstrip(" \t\n")
    if "record-rm-ok:" not in s:
        return False
    try:
        toks = _tokenize(s)
    except ValueError:
        return False
    if not toks or toks[-1][0] == "nl":
        return False
    return _MARKER_RE.match(s[toks[-1][4]:]) is not None


def _verdict(cmd, roots, env, cwd):
    """None to allow, or the hit (a dict) that the deny reports. A command over _SCAN_LIMIT bytes of UTF-8 is
    not judged at all (a prefix could drop its end, the opt-out included)."""
    if not roots or len(cmd) > _SCAN_LIMIT or len(cmd.encode("utf-8", "surrogatepass")) > _SCAN_LIMIT:
        return None
    text = _bounded(cmd)
    if ">" not in text and not _HINT_RE.search(text) and not _HINT_RE.search(text.translate(_DEQUOTE)):
        return None
    try:
        toks = _tokenize(text)
    except ValueError:
        return None  # unparseable, or past the work bound: fail open
    if _marked(text):
        return None
    ctx = {"roots": roots, "env": env, "count": 0, "stop": False, "budget": _MAX_WALK, "bytes": _MAX_EXPANDED,
           "hit": None}
    start = None if cwd is None else os.path.normpath(cwd)
    _scan(toks, ctx, text, 0, {"cwd": start, "vars": {}, "held": None, "forgot": False}, True)
    return ctx["hit"]


def _deny_object(hit):
    import shlex
    path, root, kind = _show(hit["path"]), _show(hit["root"]), hit["kind"]
    if kind == "remove":
        action, noun = "would remove " + path, "removal"
    elif kind == "recursive":
        action, noun = "would remove %s recursively, and with it %s" % (_show(hit["entry"]), path), "removal"
    elif kind == "truncate":
        action, noun = "would truncate %s with a '%s' redirection" % (path, hit["op"]), "truncation"
    elif kind == "zero":
        action, noun = "would truncate %s to zero bytes with truncate" % path, "truncation"
    elif kind == "overwrite":
        action, noun = "would overwrite %s with %s" % (path, hit["tool"]), "overwrite"
    else:
        action, noun = "would replace %s with mv" % path, "replacement"
    size = "%d byte%s" % (hit["size"], "" if hit["size"] == 1 else "s")
    mtime = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(hit["mtime"]))
    reason = _REASON.format(action=action, root=root, size=size, mtime=mtime, example=shlex.quote(path))
    note = "record-remove-check: blocked %s of existing store record %s (%s)." % (noun, path, size)
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                   "permissionDecisionReason": reason},
            "systemMessage": note}


def _decide(payload, env):
    """The deny object for a parsed payload, or None to allow."""
    if _is_worker(env) or not isinstance(payload, dict):
        return None
    if payload.get("tool_name") != "Bash" or payload.get("hook_event_name", "PreToolUse") != "PreToolUse":
        return None
    roots = _store_roots(env)
    if not roots:
        return None
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return None
    cmd = tool_input.get("command")
    if not isinstance(cmd, str) or not cmd:
        return None
    cwd = payload.get("cwd")
    if not isinstance(cwd, str) or not cwd.startswith("/") or "\0" in cwd:
        cwd = None
    hit = _verdict(cmd, roots, env, cwd)
    return None if hit is None else _deny_object(hit)


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
    import shlex
    import shutil
    import subprocess
    import tempfile
    import tokenize
    import unittest

    here = os.path.abspath(__file__)

    def run_hook(data, env=None, timeout=60):
        """The hook run as a process: (status, stdout, stderr); env is the whole environment. The timeout is a
        hang guard far above the real runtime, not a latency bound."""
        p = subprocess.run([sys.executable, "-I", "-S", "-B", here], input=data, capture_output=True,
                           env={"LC_ALL": "C"} if env is None else env, timeout=timeout)
        return p.returncode, p.stdout, p.stderr

    def payload_bytes(command, cwd=None):
        p = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": command}}
        if cwd is not None:
            p["cwd"] = cwd
        return json.dumps(p).encode()

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

    class Fixture:
        """A throwaway tree: F/records (the store), F/other, F/records-old, each file small and synthetic."""
        FILES = {"records/X.md": b"x\n", "records/E.md": b"", "records/sub/a.md": b"a\n", "records/sub2/e.md": b"",
                 "records/.git/index.lock": b"L\n", "other/X.md": b"o\n", "records-old/X.md": b"x\n"}

        def __init__(self):
            self.f = os.path.realpath(tempfile.mkdtemp(prefix="rrc."))
            self.s = os.path.join(self.f, "records")
            self.o = os.path.join(self.f, "other")
            for rel, data in self.FILES.items():
                self.put(rel, data)
            os.symlink(os.path.join(self.o, "X.md"), os.path.join(self.s, "link"))
            os.symlink(os.path.join(self.s, "X.md"), os.path.join(self.o, "l"))
            os.symlink(os.path.join(self.s, "sub"), os.path.join(self.o, "dl"))
            os.symlink(os.path.join(self.s, "sub"), os.path.join(self.o, "L"))
            os.makedirs(os.path.join(self.o, "empty"))
            for k in range(16):
                self.put("records/g/a%02d.md" % k, b"")
            self.put("records/g/z.md", b"z\n")

        def put(self, rel, data=b"x\n"):
            path = os.path.join(self.f, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as fh:
                fh.write(data)
            return path

        def sub(self, cmd):
            return (cmd.replace("@S", shlex.quote(self.s)).replace("@O", shlex.quote(self.o))
                    .replace("@F", shlex.quote(self.f)))

        def snapshot(self):
            """{relative path: bytes} of every regular file under the store (links not followed)."""
            out = {}
            for d, dirs, files in os.walk(self.s):
                for name in files:
                    p = os.path.join(d, name)
                    if os.path.isfile(p) and not os.path.islink(p):
                        with open(p, "rb") as fh:
                            out[os.path.relpath(p, self.s)] = fh.read()
            return out

        def close(self):
            shutil.rmtree(self.f, ignore_errors=True)

    def destroyed(before, after):
        """The pre-existing non-empty store files whose content did not survive: not kept as a prefix at the same
        path, nor whole in any store file (a move or a backup within the store keeps it)."""
        kept = set(after.values())
        return sorted(k for k, v in before.items() if v and not after.get(k, b"").startswith(v) and v not in kept)

    # The differential cases: (command, cwd key, the hook's verdict, whether real bash destroys a pre-existing
    # non-empty store file, tag). A tag pins a known divergence: miss (a disclosed residual: bash destroys, the
    # hook allows), marker (allowed by the opt-out), exempt (allowed by design: git's own files), out (out of
    # scope by design: allowed, whatever bash does), over (the hook denies what this run does not destroy: flat
    # reading, noclobber, an interactive prompt).
    CASES = (
        # rm, and the observed incident
        ("rm -f @S/X.md", "O", "deny", True, ""),
        ("rm -f @S/Y.md", "O", "allow", False, ""),
        ("cat >> @S/Y.md <<'EOF' || true\nnew line\nEOF\nrm -f @S/Y.md", "O", "allow", False, ""),
        ("cat >> @S/X.md <<'EOF' || true\nnew line\nEOF\nrm -f @S/X.md", "O", "deny", True, ""),
        ("echo new > @S/Y.md; rm @S/Y.md", "O", "allow", False, ""),
        ("rm @S/E.md", "O", "allow", False, ""),
        ("rm @O/X.md", "O", "allow", False, ""),
        ("rm -r @S/sub", "O", "deny", True, ""),
        ("rm -r @S/sub2", "O", "allow", False, ""),
        ("rm @S/sub", "O", "allow", False, ""),
        ("rm -d @S/sub", "O", "allow", False, ""),
        ("rm -rf @F", "O", "deny", True, ""),
        ("rm @S/link", "O", "allow", False, ""),
        ("rm -r @O/dl/", "O", "deny", True, ""),
        ("rm -r @O/l/", "O", "allow", False, ""),
        ("rm -rf @S/.", "O", "allow", False, ""),
        ("rm -rf @S/sub/..", "O", "allow", False, ""),
        ("rm -f @S/.git/index.lock", "O", "allow", True, "exempt"),
        ("rm -rf @S/.git", "O", "deny", True, ""),
        ("rm -i @S/X.md", "O", "deny", False, "over"),
        ("time rm @S/X.md", "O", "deny", True, ""),
        ("command rm @S/X.md", "O", "deny", True, ""),
        ("command -v rm @S/X.md", "O", "allow", False, ""),
        ("env rm @S/X.md", "O", "deny", True, ""),
        ("\\rm @S/X.md", "O", "deny", True, ""),
        ("r\"m\" @S/X.md", "O", "deny", True, ""),
        ("/usr/bin/rm @S/X.md", "O", "deny", True, ""),
        ("timeout 20 rm @S/X.md", "O", "deny", True, ""),
        ("rm --help @S/X.md > /dev/null", "O", "allow", False, ""),
        ("rm -Q @S/X.md", "O", "allow", False, ""),
        ("rm @S/X.md -f", "O", "deny", True, ""),
        ("rm -- @S/X.md", "O", "deny", True, ""),
        ("rm --rec @S/sub", "O", "deny", True, ""),
        ("rm @S/X.md # record-rm-ok: superseded by Y.md", "O", "allow", True, "marker"),
        ("rm @S/X.md \"# record-rm-ok: quoted\"", "O", "deny", True, ""),
        # structure read flat
        ("if :; then rm @S/X.md; fi", "O", "deny", True, ""),
        ("(rm @S/X.md)", "O", "deny", True, ""),
        ("x=$(rm @S/X.md)", "O", "deny", True, ""),
        ("f() { rm @S/X.md; }; f", "O", "deny", True, ""),
        ("f() { rm @S/X.md; }", "O", "deny", False, "over"),
        ("function f { rm @S/X.md; }; f", "O", "deny", True, ""),
        ("false && rm @S/X.md", "O", "deny", False, "over"),
        ("false || rm @S/X.md", "O", "deny", True, ""),
        ("echo 'rm -f @S/X.md'", "O", "allow", False, ""),
        ("cat <<EOF\n$(rm @S/X.md)\nEOF", "O", "deny", True, ""),
        ("cat <<'EOF'\n$(rm @S/X.md)\nEOF", "O", "allow", False, ""),
        ("x=$(cat <<EOF\n$(rm @S/X.md)\nEOF\n)", "O", "deny", True, ""),
        ("cat <(rm @S/X.md)", "O", "deny", True, ""),
        ("echo `rm @S/X.md`", "O", "allow", True, "miss"),
        ("args=(rm -f @S/X.md)", "O", "allow", False, ""),
        ("args=($(rm @S/X.md))", "O", "deny", True, ""),
        ("[[ z > @S/X.md ]]", "O", "allow", False, ""),
        ("[[ z > X.md ]] && echo gt", "S", "allow", False, ""),
        ("(( 3 > 2 )) && echo gt", "S", "allow", False, ""),
        # redirections
        (": > @S/X.md", "O", "deny", True, ""),
        ("> @S/X.md", "O", "deny", True, ""),
        ("echo hi >> @S/X.md", "O", "allow", False, ""),
        ("echo hi 2> @S/X.md", "O", "deny", True, ""),
        ("echo hi >| @S/X.md", "O", "deny", True, ""),
        ("echo hi &> @S/X.md", "O", "deny", True, ""),
        ("echo hi >& @S/X.md", "O", "deny", True, ""),
        ("echo hi 1>&2", "O", "allow", False, ""),
        ("true <> @S/X.md", "O", "allow", False, ""),
        ("cat < @S/X.md > /dev/null", "O", "allow", False, ""),
        ("{ echo hi; } > @S/X.md", "O", "deny", True, ""),
        ("exec 3> @S/X.md", "O", "deny", True, ""),
        ("echo hi | cat > @S/X.md", "O", "deny", True, ""),
        ("x=$(echo hi > @S/X.md)", "O", "deny", True, ""),
        ("set -C; echo hi > @S/X.md", "O", "deny", False, "over"),
        ("echo hi > @O/l", "O", "deny", True, ""),
        ("echo hi > @S/link", "O", "allow", False, ""),
        # operands: variables, for lists, globs, CLAUDE_PROJECT_DIR
        ("rm \"$AIQT_STORE_ROOT/X.md\"", "O", "deny", True, ""),
        ("rm ${AIQT_STORE_ROOT}/X.md", "O", "deny", True, ""),
        ("rm '$AIQT_STORE_ROOT/X.md'", "O", "allow", False, ""),
        ("rm \"$CLAUDE_PROJECT_DIR/records/X.md\"", "O", "deny", True, ""),
        ("rm ~/X.md", "O", "deny", True, ""),
        ("rm '~/X.md'", "S", "allow", False, ""),
        ("rm \"$OTHER/X.md\"", "O", "allow", True, "miss"),
        ("f=@S/X.md; rm \"$f\"", "O", "deny", True, ""),
        ("f=@O/X.md; rm \"$f\"", "O", "allow", False, ""),
        ("export f=@S/X.md; rm $f", "O", "deny", True, ""),
        ("f=@S/X.md rm \"$f\"", "O", "allow", False, ""),
        ("(f=@S/X.md); rm \"$f\"", "O", "allow", False, ""),
        ("for f in @S/X.md; do rm \"$f\"; done", "O", "deny", True, ""),
        ("for f in @O/X.md @S/X.md; do rm \"$f\"; done", "O", "deny", True, ""),
        ("for f in @S/*.md; do rm \"$f\"; done", "O", "deny", True, ""),
        ("for f in @S/X.md; do :; done; rm \"$f\"", "O", "deny", True, ""),
        ("rm @S/*.md", "O", "deny", True, ""),
        ("rm @S/g/*.md", "O", "deny", True, ""),
        ("rm @S/X[.]md", "O", "deny", True, ""),
        ("rm @S/*.none", "O", "allow", False, ""),
        ("rm @S/E*", "O", "allow", False, ""),
        ("rm @S/s*/*", "O", "deny", True, ""),
        ("rm @S/{X,Y}.md", "O", "allow", True, "miss"),
        # the working directory
        ("rm X.md", "S", "deny", True, ""),
        ("rm ./sub/../X.md", "S", "deny", True, ""),
        ("rm \"$PWD/X.md\"", "S", "deny", True, ""),
        ("cd @S && rm X.md", "O", "deny", True, ""),
        ("cd @S; rm X.md", "O", "deny", True, ""),
        ("cd @S\nrm X.md", "O", "deny", True, ""),
        ("cd @O; rm X.md", "S", "allow", False, ""),
        ("cd @F/missing; rm X.md", "S", "deny", True, ""),
        ("cd @F/missing && rm X.md", "S", "allow", False, ""),
        ("cd @F/missing && rm E.md; rm X.md", "S", "deny", True, ""),
        ("cd @F/missing && rm E.md || rm X.md", "S", "deny", True, ""),
        ("cd @O/L && rm ../X.md", "O", "deny", True, ""),
        ("cd @O/L; cd ..; rm X.md", "O", "allow", False, ""),
        ("cd @O; cd ../records; rm X.md", "S", "deny", True, ""),
        ("cd @O/nope/../../records; rm X.md", "O", "allow", False, ""),
        ("env -i rm X.md", "S", "deny", True, ""),
        ("env --ignore-environment rm X.md", "S", "deny", True, ""),
        # directory changes not followed: out of scope, allowed
        ("(cd @O); rm X.md", "S", "allow", True, "out"),
        ("echo $(cd @O) > X.md", "S", "allow", True, "out"),
        ("{ cd @O; :; } | cat; rm X.md", "S", "allow", True, "out"),
        ("{ cd @S; :; } | cat; rm X.md", "O", "allow", False, "out"),
        ("pushd @O; popd; rm X.md", "S", "allow", True, "out"),
        ("cd -P @O/L; cd ..; rm X.md", "O", "allow", True, "out"),
        ("cd - ; rm X.md", "S", "allow", True, "out"),
        ("env -C @S rm X.md", "O", "allow", True, "out"),
        ("if :; then cd @O; fi; rm X.md", "S", "allow", False, "out"),
        ("if false; then cd @O; fi; rm X.md", "S", "allow", True, "out"),
        # cp, mv, truncate, tee in scope
        ("cp @O/X.md @S/X.md", "O", "deny", True, ""),
        ("cp @O/X.md @O/l", "O", "deny", True, ""),
        ("cp @O/X.md @S/Y.md", "O", "allow", False, ""),
        ("cp @S/X.md @S/X.md", "O", "allow", False, ""),
        ("mv @O/X.md @S/X.md", "O", "deny", True, ""),
        ("mv @S/E.md @S/X.md", "O", "deny", True, ""),
        ("mv @S/X.md @S/Z.md", "O", "allow", False, ""),
        ("mv @O/X.md @S/link", "O", "allow", False, ""),
        ("cp @O/X.md @S/X.md/", "O", "allow", False, ""),
        ("cp -f @S/X.md", "O", "allow", False, ""),
        # round 3
        ("rm -r @O/d*/", "O", "deny", True, ""),
        ("rm -r @S/X*/", "O", "allow", False, ""),
        ("record=@S/X.md; printf record; rm \"$record\"", "O", "deny", True, ""),
        ("f=@S/X.md; printf -v f x; rm \"$f\"", "O", "allow", False, ""),
        ("rm --interactive=never @S/X.md", "O", "deny", True, ""),
        ("rm --interactive=bogus @S/X.md", "O", "allow", False, ""),
        ("rm --preserve-root=bogus @S/X.md", "O", "allow", False, ""),
        ("cd @O/X.md; rm X.md", "S", "deny", True, ""),
        ("cd @O/X.md && rm X.md", "S", "allow", False, ""),
        ("cd @O/X.md || rm X.md", "S", "deny", True, ""),
        ("fd=2; echo hi >& \"$fd\"", "S", "allow", False, ""),
        ("flag=-r; rm \"$flag\" @S/sub", "O", "allow", True, "out"),
        ("flag=--help; rm \"$flag\" @S/X.md", "O", "allow", False, "out"),
        ("n=0; truncate -s \"$n\" @S/X.md", "O", "allow", True, "out"),
        ("flag=-a; tee \"$flag\" @S/X.md < /dev/null", "O", "allow", False, "out"),
        ("d=@S; cd \"$d\"; rm X.md", "O", "allow", True, "out"),
        ("cd ~; rm X.md", "O", "allow", True, "out"),
        # round 4
        (": >& \"@S/X.md\"", "O", "deny", True, ""),
        (": >& '@S/X.md'", "O", "deny", True, ""),
        (": >& @S/X*", "O", "allow", True, "out"),
        (": >& ~/X.md", "O", "allow", True, "out"),
        ("f=@S/X.md; printf -v 'f[0]' %s @O/X.md; rm \"$f\"", "O", "allow", False, ""),
        ("cd \"\"; rm X.md", "S", "deny", True, ""),
        # round 5
        ("cd \"\" && rm X.md", "S", "allow", False, ""),
        ("cd \"\" || rm X.md", "S", "deny", True, ""),
        ("v=aaaa; v=$v$v; rm @S/X.md", "O", "deny", True, ""),
        ("f=@S/X.md; n=f; printf -v \"$n\" %s @O/X.md; rm \"$f\"", "O", "allow", False, ""),
        ("f=@S/X.md; n=g; printf -v \"$n\" %s x; rm \"$f\"", "O", "deny", True, ""),
        ("f=@S/X.md; f[0]=@O/X.md; rm \"$f\"", "O", "deny", False, "over"),
        ("f=safe; read f[0] <<< @S/X.md; rm \"$f\"", "O", "allow", True, "out"),
        ("truncate -s 0 @S/X.md", "O", "deny", True, ""),
        ("truncate --size=0 @S/X.md", "O", "deny", True, ""),
        ("truncate -s0 @O/l", "O", "deny", True, ""),
        ("tee @S/X.md < /dev/null", "O", "deny", True, ""),
        ("echo hi | tee @O/a @S/X.md", "O", "deny", True, ""),
        # out of scope by design: allowed
        ("cp @O/X.md @S/", "O", "allow", True, "out"),
        ("cp -f @O/X.md @S/X.md", "O", "allow", True, "out"),
        ("cp -n @O/X.md @S/X.md", "O", "allow", False, "out"),
        ("cp -t \"$AIQT_STORE_ROOT\" @O/X.md", "O", "allow", True, "out"),
        ("cp --backup=none @O/X.md @S/X.md", "O", "allow", True, "out"),
        ("mv -n -f @O/X.md @S/X.md", "O", "allow", True, "out"),
        ("mv @O/X.md @S/", "O", "allow", True, "out"),
        ("truncate -s 1 @S/X.md", "O", "allow", True, "out"),
        ("truncate -s +10 @S/X.md", "O", "allow", False, "out"),
        ("tee -a @S/X.md < /dev/null", "O", "allow", False, "out"),
        ("tee -i @S/X.md < /dev/null", "O", "allow", True, "out"),
        ("find @S -name X.md -delete", "O", "allow", True, "out"),
        ("find -name X.md -delete", "S", "allow", True, "out"),
        ("find @S -type f -exec rm -f {} +", "O", "allow", True, "out"),
        ("rsync -a --delete @O/empty/ @S/sub/", "O", "allow", True, "out"),
        ("unlink @S/X.md", "O", "allow", True, "out"),
        ("shred -u @S/X.md", "O", "allow", True, "out"),
        ("sed -i d @S/X.md", "O", "allow", True, "out"),
        ("dd if=/dev/null of=@S/X.md 2> /dev/null", "O", "allow", True, "out"),
        ("ln -sf @O/X.md @S/X.md", "O", "allow", True, "out"),
        ("bash -c 'rm @S/X.md'", "O", "allow", True, "out"),
        ("eval 'rm @S/X.md'", "O", "allow", True, "out"),
        ("echo @S/X.md | xargs rm", "O", "allow", True, "out"),
    )

    class T(unittest.TestCase):
        def setUp(self):
            self.x = Fixture()
            self.env = {"AIQT_STORE_ROOT": self.x.s, "HOME": self.x.s}

        def tearDown(self):
            self.x.close()

        def decide(self, command, env=None, cwd=None, **extra):
            payload = {"hook_event_name": "PreToolUse", "tool_name": "Bash",
                       "tool_input": {"command": self.x.sub(command)}, "cwd": self.x.o if cwd is None else cwd}
            payload.update(extra)
            return _decide(payload, self.env if env is None else env)

        def assertDeny(self, command, **kw):
            out = self.decide(command, **kw)
            self.assertIsNotNone(out, command)
            h = out["hookSpecificOutput"]
            self.assertEqual((h["hookEventName"], h["permissionDecision"]), ("PreToolUse", "deny"))
            self.assertTrue(h["permissionDecisionReason"].startswith("record-remove-check ("), command)
            self.assertIn("# record-rm-ok: <reason>", h["permissionDecisionReason"])
            self.assertTrue(out["systemMessage"].startswith("record-remove-check: blocked "), command)
            return h["permissionDecisionReason"]

        def assertAllow(self, command, **kw):
            self.assertIsNone(self.decide(command, **kw), command)

        # row 1: the observed incident
        def test_01_flip_observed_incident(self):
            cmd = "cat >> @S/X.md <<'EOF' || true\nappended\nEOF\nrm -f @S/X.md"
            reason = self.assertDeny(cmd)
            self.assertIn("remove " + os.path.join(self.x.s, "X.md") + ",", reason)
            self.assertIn("It holds 2 bytes (modified ", reason)
            os.unlink(os.path.join(self.x.s, "X.md"))
            self.assertAllow(cmd)  # absent when the hook runs: the command creates it, then removes its own file
            self.assertAllow("echo new > @S/X.md; rm @S/X.md")

        # row 2: empty files
        def test_02_flip_empty_file(self):
            self.assertAllow("rm @S/E.md")
            self.x.put("records/E.md", b"1")
            self.assertIn("It holds 1 byte (", self.assertDeny("rm @S/E.md"))

        # row 3: outside the store
        def test_03_flip_outside_store(self):
            self.assertAllow("rm @O/X.md")
            self.assertAllow("rm @F/records-old/X.md")
            self.assertAllow("rm " + os.path.join(self.x.s + "-old", "X.md"))
            self.assertDeny("rm @S/X.md")

        # row 4: configuration
        def test_04_flip_configuration(self):
            s, cmd = self.x.s, "rm @S/X.md"
            for env in ({}, {"AIQT_STORE_ROOT": ""}, {"AIQT_STORE_ROOT": "records"}, {"AIQT_STORE_ROOT": "/"},
                        {"AIQT_STORE_ROOT": "/.."}, {"AIQT_STORE_ROOT": ":"},
                        {"AIQT_STORE_ROOT": "", "ORCH_STORE_ROOT": s},
                        {"ORCH_STORE_ROOT": ""}, {"AIQT_STORE_ROOT": s + "-old"}, {"AIQT_STORE_ROOT": 7}):
                self.assertAllow(cmd, env=env)
            for env in ({"AIQT_STORE_ROOT": s}, {"ORCH_STORE_ROOT": s}, {"AIQT_STORE_ROOT": self.x.o + ":" + s},
                        {"AIQT_STORE_ROOT": "rel:" + s + ":"}, {"AIQT_STORE_ROOT": s + "/"},
                        {"AIQT_STORE_ROOT": s, "ORCH_STORE_ROOT": ""}):
                self.assertDeny(cmd, env=env)
            alias = os.path.join(self.x.f, "alias")
            os.symlink(s, alias)
            self.assertDeny(cmd, env={"AIQT_STORE_ROOT": alias})
            self.assertDeny("rm " + os.path.join(alias, "X.md"))

        # row 7: text that is not a command
        def test_07_flip_text_not_command(self):
            for c in ("echo 'rm -f @S/X.md'", "echo rm -f @S/X.md", "# rm -f @S/X.md", "cat <<EOF\nrm -f @S/X.md\nEOF",
                      "grep -r 'rm @S/X.md' @O", "printf '%s\\n' \"rm @S/X.md\"", "cat <<'EOF'\n$(rm @S/X.md)\nEOF",
                      "echo 'x > @S/X.md'", "cat <<EOF\n> @S/X.md\nEOF"):
                self.assertAllow(c)
            self.assertDeny("rm -f @S/X.md")

        # row 8: redirections
        def test_08_flip_redirections(self):
            for c in ("echo x >> @S/X.md", "true <> @S/X.md", "cat < @S/X.md", "echo x > /dev/null",
                      "echo x &>> @S/X.md",
                      "echo x 2>&1", "echo x >&2", "echo x >&-", "echo x 2>> @S/X.md", "echo x > @S/Y.md",
                      "echo x > @S/E.md", "echo x > @S/sub", "cat <<< @S/X.md", "echo x >& 2"):
                self.assertAllow(c)
            for c in ("echo x > @S/X.md", "echo x >| @S/X.md", "echo x &> @S/X.md", "echo x 2> @S/X.md",
                      "exec {fd}> @S/X.md", "echo x >& @S/X.md", ": > @S/X.md", "> @S/X.md", "{ echo; } > @S/X.md",
                      "(echo) > @S/X.md", "while false; do :; done > @S/X.md", "echo x 3> @S/X.md 1>&3",
                      "cat > @S/X.md <<'EOF'\nnew\nEOF", "echo x > \"$AIQT_STORE_ROOT/X.md\""):
                self.assertIn("truncate", self.assertDeny(c))
            self.assertIn("with a '>|' redirection", self.assertDeny("echo x >| @S/X.md"))

        # row 10: recursive removal
        def test_08_duplication_target_expansion(self):
            self.x.put("records/2", b"two")
            for c in ("fd=2; echo hi >& \"$fd\"", "f=@S/X.md; echo hi >& \"$f\"", "echo hi >& $HOME/X.md",
                      ": >& @S/X*", ": >& ~/X.md", ": >& X.m?"):
                self.assertAllow(c, cwd=self.x.s)  # an expansion in the target may name a descriptor: not read
            for c in (": >& \"@S/X.md\"", ": >& '@S/X.md'", ": >& @S/\"X\".md", ": >& \"X.md\""):
                self.assertDeny(c, cwd=self.x.s)  # quoting alone keeps a literal file target
            for digit in (chr(178), chr(1634)):  # a superscript two and an Arabic-Indic two name files, not descriptors
                self.x.put("records/" + digit, b"d")
                self.assertDeny(": >& " + digit, cwd=self.x.s)
                self.assertDeny(": >& \"" + digit + "\"", cwd=self.x.s)
                self.assertDeny("echo x " + digit + "> @S/X.md", cwd=self.x.s)  # a word before `>`, not a descriptor
            self.assertAllow(": >& 2", cwd=self.x.s)
            self.assertAllow(": >& \"2\"", cwd=self.x.s)
            self.assertDeny("echo hi >& @S/X.md")
            self.assertDeny("echo hi >& X.md", cwd=self.x.s)

        def test_10_flip_recursive(self):
            self.assertAllow("rm -r @S/sub2")  # empty files only
            self.assertAllow("rm @S/sub")  # a directory without -r: rm refuses it
            self.assertAllow("rm -d @S/sub")
            reason = self.assertDeny("rm -r @S/sub")
            self.assertIn("recursively, and with it " + os.path.join(self.x.s, "sub", "a.md"), reason)
            self.x.put("records/sub2/deep/f.md", b"z")
            self.assertDeny("rm -R @S/sub2")
            self.assertDeny("rm --recursive @S/sub2")
            self.assertDeny("rm --rec @S/sub2")  # an unambiguous prefix of --recursive
            self.assertDeny("rm -rf @F")  # an ancestor of the store
            self.assertDeny("rm -rf @S")
            self.assertAllow("rm -rf @O")  # its link into the store is removed, not followed
            self.assertAllow("rm -rf /")  # rm refuses /
            self.assertDeny("rm -rf --no-preserve-root /")

        def test_10_walk_bound(self):
            for k in range(_MAX_WALK + 5):
                self.x.put("records/big/%04d" % k, b"")
            self.assertAllow("rm -r @S/big")  # past the budget with no content found: allowed (disclosed)
            self.x.put("records/big2/keep.md", b"k")
            self.assertDeny("rm -r @S/big2")

        def test_10_symlinks_not_followed_in_walk(self):
            d = self.x.put("records/sub3/keep", b"")
            os.symlink(os.path.join(self.x.s, "X.md"), os.path.join(os.path.dirname(d), "to-x"))
            os.symlink(self.x.o, os.path.join(os.path.dirname(d), "to-other"))
            self.assertAllow("rm -r @S/sub3")

        # row 11: the opt-out marker
        def test_11_flip_marker(self):
            base = "rm @S/X.md"
            for tail in (" # record-rm-ok: superseded by Y.md", "  #record-rm-ok:x", " # record-rm-ok: x\n\n",
                         "\t# record-rm-ok:\tdone"):
                self.assertAllow(base + tail)
            self.assertAllow("rm @S/X.md; rm @S/sub -r # record-rm-ok: both retired")
            for tail in (" # record-rm-ok:", " # record-rm-ok:   ", " # record-rm-ok", " # record-rm-okay: x",
                         " '# record-rm-ok: quoted'", " \"# record-rm-ok: quoted\"",
                         " # record-rm-ok: x\necho next", ";# record-rm-ok: x", "\n # record-rm-ok: own line",
                         " x#record-rm-ok: mid-word",
                         " # RECORD-RM-OK: x"):
                self.assertDeny(base + tail)
            self.assertDeny("cat > @S/X.md <<EOF\n # record-rm-ok: body line")
            self.assertDeny("cat > @S/X.md <<EOF\nx\nEOF # record-rm-ok: not the delimiter line")
            self.assertAllow("cat > @S/X.md <<EOF # record-rm-ok: regenerated\nx\nEOF\nrm @S/E.md # record-rm-ok: y")

        # row 12: prefixes and the command word
        def test_12_flip_prefixes(self):
            for c in ("command -v rm @S/X.md", "type rm @S/X.md", "rm --help @S/X.md", "rm --version", "rm -Z @S/X.md",
                      "rm --force=yes @S/X.md", "rm --ver @S/X.md", "builtin rm @S/X.md", "echo rm @S/X.md",
                      "rmdir @S/sub", "env -S 'rm @S/X.md'", "env -S x rm @S/X.md"):
                self.assertAllow(c)
            for c in ("sudo rm @S/X.md", "sudo -u root rm @S/X.md", "/bin/rm @S/X.md", "env rm @S/X.md",
                      "env A=1 rm @S/X.md", "env -i rm @S/X.md", "time rm @S/X.md", "time -p rm @S/X.md",
                      "command rm @S/X.md", "nohup rm @S/X.md", "exec rm @S/X.md", "x=1 rm @S/X.md", "\\rm @S/X.md",
                      "'rm' @S/X.md", "timeout 5 rm @S/X.md", "timeout -s KILL 5 rm @S/X.md", "! rm @S/X.md",
                      "coproc rm @S/X.md", "rm @S/Y.md @S/X.md", "rm -fv @S/X.md", "rm --interactive=never @S/X.md",
                      "rm -- @S/X.md", "rm @S/X.md --force", "rm -I @S/X.md", "rm --preserve-root @S/X.md",
                      "rm --interactive=n @S/X.md", "rm --interactive=a @S/X.md", "rm --interactive=o @S/X.md",
                      "rm --inter=yes @S/X.md", "rm --preserve-root=all @S/X.md"):
                self.assertDeny(c)
            for c in ("rm --interactive=bogus @S/X.md", "rm --preserve-root=bogus @S/X.md", "rm --interactive= @S/X.md",
                      "rm --preserve-root=al @S/X.md", "rm --interactive=nev3r @S/X.md"):
                self.assertAllow(c)

        # row 13: flat reading of structure
        def test_13_flip_structures(self):
            self.assertAllow("echo `rm @S/X.md`")  # inside backticks: a disclosed residual, pinned
            for c in ("for f in @S/X.md; do rm \"$f\"; done", "if :; then rm @S/X.md; fi",
                      "(rm @S/X.md)", "x=$(rm @S/X.md)", "f() { rm @S/X.md; }",
                      "function f { rm @S/X.md; }", "function f () { rm @S/X.md; }", "probe || rm @S/X.md",
                      "false && rm @S/X.md", "rm @S/X.md &", "echo $(rm @S/X.md)", "cat <<EOF\n$(rm @S/X.md)\nEOF",
                      "cat <(rm @S/X.md)", "while :; do rm @S/X.md; done", "case a in a) rm @S/X.md;; esac",
                      "{ rm @S/X.md; }", "echo \"$(rm @S/X.md)\"", "a | rm @S/X.md",
                      "x=$(cat <<EOF\n$(rm @S/X.md)\nEOF\n)", "cat <<A <<B\na\nA\n$(rm @S/X.md)\nB",
                      "cat <<-EOF\n\t$(rm @S/X.md)\n\tEOF",
                      "for f in a; do rm @S/X.md; done", "x=$(y=$(rm @S/X.md))", "echo > \"$(rm @S/X.md)\""):
                self.assertDeny(c)

        # row 14: reading operands
        def test_14_flip_operands(self):
            s, o = self.x.s, self.x.o
            for c in ("rm \"$AIQT_STORE_ROOT/X.md\"", "rm ${AIQT_STORE_ROOT}/X.md", "rm $AIQT_STORE_ROOT/X.md",
                      "rm ~/X.md", "rm \"$HOME\"/X.md", "rm @S/./X.md", "rm @S/sub/../X.md", "rm @S//X.md",
                      "rm @S\"/X.md\"", "rm @S/X\\.md", "rm @S/'X'.md"):
                self.assertDeny(c)
            self.assertDeny("rm \"$ORCH_STORE_ROOT/X.md\"", env={"AIQT_STORE_ROOT": s, "ORCH_STORE_ROOT": s})
            self.assertDeny("rm \"$CLAUDE_PROJECT_DIR/records/X.md\"", env=dict(self.env, CLAUDE_PROJECT_DIR=self.x.f))
            for c in ("rm '$AIQT_STORE_ROOT/X.md'", "rm \"$OTHER/X.md\"", "rm \"${AIQT_STORE_ROOT:-x}/X.md\"",
                      "rm '~/X.md'", "rm \"~\"/X.md", "rm @S/{X,Y}.md", "rm $(echo @S/X.md)", "rm $'@S/X.md'",
                      "rm \"$1\"", "rm @S/X.md\\\\", "rm \"$CLAUDE_PROJECT_DIR/records/X.md\"", "rm @S/[^E].md",
                      "rm @S/'*'.md", "rm @S/\\*.md"):
                self.assertAllow(c)
            self.assertAllow("rm $HOME/X.md", env={"AIQT_STORE_ROOT": s, "HOME": s + " x"})  # split: not read
            self.x.put("records/a b", b"ab")  # bash splits $V into two words: the file `a b` is not what it removes
            self.assertAllow("rm $HOME", env={"AIQT_STORE_ROOT": s, "HOME": os.path.join(s, "a b")})
            self.assertDeny("rm \"$HOME\"", env={"AIQT_STORE_ROOT": s, "HOME": os.path.join(s, "a b")})
            self.assertAllow("rm ~/X.md", env={"AIQT_STORE_ROOT": s})  # no HOME to read
            self.assertDeny("rm X.md", cwd=s)
            self.assertDeny("rm sub/../X.md", cwd=s)
            self.assertDeny("rm \"$PWD\"/X.md", cwd=s)
            self.assertDeny("rm ../records/X.md", cwd=o)
            self.assertAllow("rm X.md", cwd=o)
            self.assertAllow("rm X.md", cwd="records")  # a relative cwd is not read

        def test_14_flip_globs(self):
            for c in ("rm @S/*.md", "rm @S/X.m?", "rm @S/[X].md", "rm @S/[!E].md", "rm @S/*/a.md", "rm *.md",
                      "rm ./*", "rm @S/s*/*", "rm @S/X[.]md", "rm @S/X[.:]md", "rm @S/[]X].md", "rm @S/g/*.md",
                      "rm @S/g/*"):
                self.assertDeny(c, cwd=self.x.s)
            for c in ("rm @S/*.none", "rm @S/E*", "rm @S/sub2/*", "rm @O/*", "rm @F/records-old/*.md", "rm .*",
                      "rm @S/[^E].md", "rm @S/[[:upper:]].md", "rm @S/g/a*"):
                self.assertAllow(c, cwd=self.x.s)
            self.assertIn(os.path.join(self.x.s, "g", "z.md"), self.assertDeny("rm @S/g/*.md"))  # past 16 matches
            self.x.put("records/.hidden", b"h")
            self.assertAllow("rm @S/*hidden")  # a leading dot is matched only by a leading dot
            self.assertDeny("rm @S/.h*")
            self.x.put("records/*.lit", b"l")
            self.assertDeny("rm @S/'*'.lit")  # a quoted star is literal, and that file exists
            self.assertIn(os.path.join(self.x.s, "X.md"), self.assertDeny("rm @S/X*"))
            self.assertDeny("rm -r @O/d*/")  # a trailing slash selects directories, through links
            self.assertDeny("rm -r @S/s*/")
            for c in ("rm -r @S/X*/", "rm @S/X*/", "rm -r @O/l*/", "rm @S/s*/"):
                self.assertAllow(c)

        def test_14_glob_bracket_reading(self):
            for comp, unread in (("X[.]md", False), ("[.]", False), ("[:]", False), ("[=]x", False), ("[!.]", False),
                                 ("[]a]", False), ("[[:alpha:]]", True), ("[[=a=]]", True), ("[[.a.]]", True),
                                 ("[^a]", True), ("a[b", False), ("[*]x", False)):
                self.assertEqual(_glob_unread(comp), unread, comp)

        def test_14_flip_variables(self):
            for c in ("f=@S/X.md; rm \"$f\"", "f=@S/X.md\nrm $f", "d=@S; rm \"$d/X.md\"", "d=@S; f=$d/X.md; rm \"$f\"",
                      "export f=@S/X.md; rm \"$f\"", "local f=@S/X.md; rm \"${f}\"", "readonly f=@S/X.md; rm $f",
                      "for f in @S/X.md; do rm \"$f\"; done", "for f in @O/a @S/X.md; do rm \"$f\"; done",
                      "for f in @S/*.md; do rm \"$f\"; done", "for f in @S/X.md; do :; done; rm \"$f\"",
                      "HOME=@F/records; rm ~/X.md", "n=X; rm @S/$n.md", "f=\"@S/X.md\"; rm \"$f\"",
                      "for d in @O @S; do rm \"$d/X.md\"; done",
                      "for a in @O @S; do for b in X Y; do rm $a/$b.md; done; done",
                      "f=@S/X.md && rm \"$f\"", "f=@S/X.md; if c; then rm \"$f\"; fi"):
                self.assertDeny(c)
            for c in ("record=@S/X.md; printf record; rm \"$record\"", "f=@S/X.md; printf '%s' \"$f\"; rm \"$f\"",
                      "f=@S/X.md; printf -- -v; rm \"$f\""):
                self.assertDeny(c)
            for c in ("f=@S/X.md; n=g; printf -v \"$n\" x; rm \"$f\"", "f=@S/X.md; printf -v \"g\" x; rm \"$f\"",
                      "f=@S/X.md; g=@O/a; printf -v 'g[0]' x; rm \"$f\""):
                self.assertDeny(c)  # a destination read to another name leaves f known
            for c in ("f=@S/X.md; n=f; printf -v \"$n\" %s x; rm \"$f\"", "f=@S/X.md; printf -v \"$u\" x; rm \"$f\"",
                      "f=@S/X.md; printf -v$u x; rm \"$f\"", "HOME=@F/records; printf -v \"$u\" x; rm ~/X.md"):
                self.assertAllow(c)  # read to f, or not readable: every variable unknown, never the text `$n`
            # array elements are out of scope (disclosed): an assignment to one is not followed, so f keeps its
            # earlier value, stale either way
            self.assertDeny("f=@S/X.md; f[0]=@O/X.md; rm \"$f\"")
            self.assertAllow("f=safe; read f[0] <<< @S/X.md; rm \"$f\"")
            self.assertAllow("f=(@S/X.md); rm \"${f[0]}\"")
            for c in ("f=@S/X.md; printf -v f x; rm \"$f\"", "f=@S/X.md; printf -vf x; rm \"$f\"",
                      "f=@S/X.md; printf -v 'f[0]' %s x; rm \"$f\"", "f=@S/X.md; printf -v f[1] x; rm \"$f\"",
                      "flag=-r; rm \"$flag\" @S", "flag=--help; rm \"$flag\" @S/X.md",
                      "n=0; truncate -s \"$n\" @S/X.md", "flag=-a; tee \"$flag\" @S/X.md",
                      "f=-f; cp \"$f\" @O/X.md @S/X.md", "o=-n; mv $o @O/X.md @S/X.md"):
                self.assertAllow(c)
            self.x.put("records/old-new.md", b"on")
            self.x.put("records/old-old.md", b"")
            self.x.put("records/new-new.md", b"")
            self.assertAllow("for n in old new; do rm \"$n-$n.md\"; done", cwd=self.x.s)  # one variable twice: out
            self.assertDeny("for a in old new; do for b in new old; do rm \"$a-$b.md\"; done; done", cwd=self.x.s)
            self.x.put("records/dash/-f", b"")
            self.x.put("records/dash/k.md", b"k")
            self.assertAllow("rm *", cwd=os.path.join(self.x.s, "dash"))  # a glob made the option -f: out of scope
            self.assertDeny("rm ./*", cwd=os.path.join(self.x.s, "dash"))
            for c in ("f=@O/X.md; rm \"$f\"", "f=@S/X.md; f=@O/X.md; rm \"$f\"", "f=$(x); rm \"$f\"",
                      "f=@S/X.md rm \"$f\"", "f+=@S/X.md; rm \"$f\"", "for f; do rm \"$f\"; done",
                      "(f=@S/X.md); rm \"$f\"", "echo $(f=@S/X.md); rm \"$f\"", "f=@S/X.md | true; rm \"$f\"",
                      "f=@S/X.md; read f; rm \"$f\"", "arr=(@S/X.md); rm \"${arr[0]}\"", "declare -r f=@S/X.md; rm $f",
                      "f=@O/a; if c; then f=@S/X.md; fi; rm \"$f\"", "c && f=@S/X.md; rm \"$f\"",
                      "if c; then for f in @S/X.md; do :; done; fi; rm \"$f\"", "f=@S/X.md; unset f; rm \"$f\"",
                      "f=@S/X.md; eval x; rm \"$f\"", "HOME=@O; rm ~/X.md", "f=@S/X.md; . ./x; rm \"$f\""):
                self.assertAllow(c)

        def test_14_flip_directories(self):
            s, o = self.x.s, self.x.o
            for c, cwd in (("cd @S && rm X.md", o), ("cd @S; rm X.md", o), ("cd @S\nrm X.md", o),
                           ("cd @F/missing; rm X.md", s), ("cd @O; cd ../records; rm X.md", s),
                           ("env -i rm X.md", s), ("env --ignore-environment rm X.md", s),
                           ("cd /nowhere && rm E.md; rm X.md", s), ("cd /nowhere && rm E.md || rm X.md", s),
                           ("cd @O/L && rm ../X.md", o), ("cd @O/X.md; rm X.md", s), ("cd @O/X.md || rm X.md", s),
                           ("cd \"\"; rm X.md", s), ("cd ''; rm X.md", s), ("cd \"\" || rm X.md", s),
                           ("cd @S; x=$(pwd); rm X.md", o),
                           ("cd @S & rm @S/X.md", o), ("cd @S; rm \"$PWD/X.md\"", o),
                           ("cd @O/L/..; cd ../records; rm X.md", o)):
                self.assertDeny(c, cwd=cwd)
            for c, cwd in (("cd @O && rm X.md", s), ("cd @O; rm X.md", s), ("cd /nowhere && rm X.md", s),
                           ("pushd @S; rm X.md", o), ("pushd /tmp; rm X.md", s), ("cd /tmp; rm \"$PWD/X.md\"", s),
                           ("cd - ; rm X.md", s), ("cd \"$(x)\"; rm X.md", s), ("popd; rm X.md", s), ("cd; rm X.md", o),
                           ("env -C / rm X.md", s), ("env -C @S rm X.md", o), ("sudo -D / rm X.md", s),
                           ("sudo -i rm X.md", s), ("(cd @S); rm X.md", o), ("cd @O; (cd @S); rm X.md", s),
                           ("(cd @O); rm X.md", s), ("echo $(cd @O) > X.md", s), ("{ cd @O; :; } | cat; rm X.md", s),
                           ("{ cd @S; :; } | cat; rm X.md", o), ("pushd @O; popd; rm X.md", s),
                           ("cd -P @O/L; cd ..; rm X.md", o), ("cd @O && cd @S && rm X.md", o),
                           ("if c; then cd @O; fi; rm X.md", s), ("c || cd @O; rm X.md", s),
                           ("cd @O | true; rm X.md", s),
                           ("cd @O & rm X.md", s), ("cd @O/L; cd ..; rm X.md", o), ("cd @S/s*; rm a.md", o),
                           ("cd -- @S; rm X.md", o), ("command cd @S; rm X.md", o), ("{ cd @S; }; rm X.md", o),
                           ("f() { cd @S; }; rm X.md", o), ("cd @O; eval x; rm X.md", s),
                           ("cd @O/nope/..; rm X.md", o), ("cd @O/nope/../../records; rm X.md", o)):
                self.assertAllow(c, cwd=cwd)
            for c, cwd in (("cd ~ && rm X.md", o), ("d=@S; cd \"$d\" && rm X.md", o), ("cd @O/X.md && rm X.md", s),
                           ("cd \"\" && rm X.md", s), ("cd '' && rm X.md", s),
                           ("cd $HOME && rm X.md", o)):
                self.assertAllow(c, cwd=cwd)  # a target with an expansion is not followed; a failed cd && stops
            self.assertDeny("cd \"@S\" && rm X.md", cwd=o)  # quoting alone keeps a target literal
            self.assertDeny("cd /tmp; rm @S/X.md", cwd=s)  # an absolute path is read after a cd

        def test_14_misplaced_token_not_read(self):
            # a token whose text is not at its offsets reads as nothing, never as the text found there
            text = "rm " + os.path.join(self.x.s, "X.md")
            ctx = {"env": self.env, "budget": _MAX_WALK, "bytes": _MAX_EXPANDED, "roots": [self.x.s]}
            state = {"cwds": [self.x.o], "vars": {}, "join": None}
            good = _tokenize(text)[1]
            self.assertEqual(_expand(good, ctx, text, 0, state, state["cwds"]), [os.path.join(self.x.s, "X.md")])
            bad = ("w", "elsewhere", False, good[3], good[4], ())
            self.assertEqual(_expand(bad, ctx, text, 0, state, state["cwds"]), [])
            self.assertEqual(_expand(good, ctx, text, 1, state, state["cwds"]), [])

        def test_14_payload_without_cwd(self):
            payload = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "rm X.md"}}
            self.assertIsNone(_decide(payload, self.env))
            payload["tool_input"]["command"] = self.x.sub("rm @S/X.md")
            self.assertIsNotNone(_decide(payload, self.env))

        # row 15: symbolic links
        def test_15_flip_symlinks(self):
            self.assertAllow("rm @S/link")  # a link in the store: only the link is removed
            self.assertAllow("rm @O/l")  # a link outside pointing in: only the link is removed
            self.assertAllow("echo x > @S/link")  # the write lands outside the store
            self.assertIn("truncate " + os.path.join(self.x.s, "X.md"), self.assertDeny("echo x > @O/l"))
            self.assertAllow("rm -r @O/l/")  # a trailing slash on a link to a file: rm fails
            self.assertDeny("rm -r @O/dl/")  # a trailing slash on a link to a directory: its content goes
            self.assertAllow("rm -r @O/dl")  # without it only the link goes

        # row 16: fail-open inputs
        def test_16_malformed_payloads(self):
            base = self.x.sub("rm @S/X.md")
            for p in (None, 7, "x", [], {}, {"tool_name": "Bash"}, {"tool_name": "Bash", "tool_input": "x"},
                      {"tool_name": "Bash", "tool_input": {"command": 7}},
                      {"tool_name": "Bash", "tool_input": {"command": ""}},
                      {"tool_name": "Read", "tool_input": {"command": base}},
                      {"tool_name": None, "tool_input": {"command": base}}):
                self.assertIsNone(_decide(p, self.env), p)
            self.assertIsNone(self.decide("rm @S/X.md", hook_event_name="PostToolUse"))
            self.assertIsNone(self.decide("rm \"@S/X.md"))  # an unbalanced quote
            self.assertIsNone(self.decide("rm @S/X.md; echo " + "$(" * 40 + "x" + ")" * 40))  # past the nesting
            self.assertDeny("rm @S/X.md")

        def test_16_flip_subagent_payload_judged(self):
            # a subagent's call is judged like any other: removing a store record from one loses the same data
            for agent in ("a1", None, ""):
                self.assertDeny("rm @S/X.md", agent_id=agent)
                self.assertAllow("rm @S/E.md", agent_id=agent)
            env = dict(self.env, LC_ALL="C")
            data = json.dumps({"hook_event_name": "PreToolUse", "tool_name": "Bash", "agent_id": "a1",
                               "tool_input": {"command": self.x.sub("rm -f @S/X.md")}}).encode()
            rc, out, err = run_hook(data, env)
            self.assertEqual((rc, err), (0, b""))
            self.assertEqual(json.loads(out)["hookSpecificOutput"]["permissionDecision"], "deny")
            self.assertEqual(run_hook(data, dict(env, AIQT_HOOKS_WORKER="1")), (0, b"", b""))  # the kill-switch

        def test_16_worker_envs(self):
            for extra in ({"AIQT_HOOKS_WORKER": "1"}, {"ORCH_WORKER": "1"}, {"ORCH_VERIFY_OWNER": ""},
                          {"ORCH_VERIFY_OWNER": "x"}, {"AIQT_HOOKS_WORKER": "0", "ORCH_WORKER": "1"}):
                self.assertIsNone(self.decide("rm @S/X.md", env=dict(self.env, **extra)), extra)
            for extra in ({"AIQT_HOOKS_WORKER": "0"}, {"AIQT_HOOKS_WORKER": "yes"}, {"ORCH_WORKER": "0"}):
                self.assertIsNotNone(self.decide("rm @S/X.md", env=dict(self.env, **extra)), extra)

        def test_16_bad_argv(self):
            old = sys.stdout
            sys.stdout = io.StringIO()
            try:
                for argv in (None, 7, "--self-test", ["x", 3], {"a": 1}, []):
                    self.assertEqual(main(argv), 0)
                self.assertEqual(sys.stdout.getvalue(), "")
            finally:
                sys.stdout = old

        def test_16_extra_argv_fails_open(self):
            env = dict(self.env, LC_ALL="C")
            for extra in (["--bogus"], ["extra"], ["--self-test", "extra"], ["-", "--self-test"]):
                p = subprocess.run([sys.executable, "-I", "-S", "-B", here] + extra,
                                   input=payload_bytes(self.x.sub("rm @S/X.md")), capture_output=True, env=env,
                                   timeout=60)
                self.assertEqual((p.returncode, p.stdout, p.stderr), (0, b"", b""), extra)

        def test_16_process_fail_open(self):
            env = dict(self.env, LC_ALL="C")
            good = payload_bytes(self.x.sub("rm @S/X.md"))
            for data in (b"", b"not json", b"[1, 2]", b"7", b"\xff\xfe\x00garbage", b'{"tool_name": "Bash"',
                         good[:-1]):
                self.assertEqual(run_hook(data, env), (0, b"", b""), data[:30])
            for extra in ({"AIQT_HOOKS_WORKER": "1"}, {"ORCH_WORKER": "1"}, {"ORCH_VERIFY_OWNER": ""}):
                self.assertEqual(run_hook(good, dict(env, **extra)), (0, b"", b""), extra)
            self.assertEqual(run_hook(good, {"LC_ALL": "C"}), (0, b"", b""))  # no store configured

        def test_16_oversized_not_judged(self):
            base = "rm @S/X.md"
            self.assertAllow(base + " " * 65536)
            self.assertAllow(base + " " + chr(233) * 33000)  # under 64 KiB of characters, over it in bytes
            self.assertDeny(base + " " * 60000)
            env = dict(self.env, LC_ALL="C")
            big = json.dumps({"tool_name": "Bash", "tool_input": {"command": "x" * (17 * 1024 * 1024)}}).encode()
            self.assertEqual(run_hook(big, env), (0, b"", b""))

        def test_16_process_deny_is_one_json_line(self):
            env = dict(self.env, LC_ALL="C")
            rc, out, err = run_hook(payload_bytes(self.x.sub("rm @S/X.md")), env)
            self.assertEqual((rc, err), (0, b""))
            self.assertTrue(out.endswith(b"\n") and out.count(b"\n") == 1, out)
            doc = json.loads(out)
            self.assertEqual(set(doc), {"hookSpecificOutput", "systemMessage"})
            h = doc["hookSpecificOutput"]
            self.assertEqual(set(h), {"hookEventName", "permissionDecision", "permissionDecisionReason"})
            self.assertEqual(h["permissionDecision"], "deny")
            self.assertEqual(run_hook(payload_bytes(self.x.sub("rm @S/E.md")), env), (0, b"", b""))

        def test_16_closed_stdout(self):
            env = dict(self.env, LC_ALL="C")
            p = subprocess.run([sys.executable, "-I", "-S", "-B", here], input=payload_bytes(self.x.sub("rm @S/X.md")),
                               stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, env=env, timeout=60)
            self.assertEqual((p.returncode, p.stderr), (0, b""))
            r, w = os.pipe()
            os.close(r)
            try:
                p = subprocess.run([sys.executable, "-I", "-S", "-B", here],
                                   input=payload_bytes(self.x.sub("rm @S/X.md")), stdout=w, stderr=subprocess.PIPE,
                                   env=env, timeout=60)
            finally:
                os.close(w)
            self.assertEqual((p.returncode, p.stderr), (0, b""))

        def test_16_directory_stdin_guard(self):
            if not os.path.exists("/bin/sh"):
                self.skipTest("no /bin/sh")
            fd = os.open(self.x.f, os.O_RDONLY)
            try:
                line = REGISTRATION.format(python=sys.executable, hook=here)
                p = subprocess.run(["/bin/sh", "-c", line], stdin=fd, capture_output=True, env={"LC_ALL": "C"},
                                   timeout=60)
                self.assertEqual((p.returncode, p.stdout, p.stderr), (0, b"", b""))
            finally:
                os.close(fd)

        # row 18: git's own files
        def test_18_flip_git_internals(self):
            self.assertAllow("rm -f @S/.git/index.lock")
            self.assertAllow("echo x > @S/.git/index.lock")
            self.x.put("records/sub/.git/objects/ab", b"obj")
            self.assertAllow("rm -f @S/sub/.git/objects/ab")
            self.assertDeny("rm -rf @S/.git")
            self.x.put("records/.gitkeep-notes", b"n")
            self.assertDeny("rm @S/.gitkeep-notes")  # a record whose name starts .git is not git's own file

        def test_19_operand_bound(self):
            many = " ".join("@O/n%d" % k for k in range(_MAX_OPERANDS))
            self.assertAllow("rm " + many + " @S/X.md")  # past the bound: a disclosed residual, pinned
            self.assertDeny("rm @S/X.md " + many)
            self.assertDeny("rm " + " ".join("@O/n%d" % k for k in range(_MAX_OPERANDS - 1)) + " @S/X.md")

        def test_20_reason_text(self):
            self.x.put("records/it's.md", b"q")
            reason = self.assertDeny("rm @S/\"it's.md\"")
            self.assertIn("cat -- " + shlex.quote(os.path.join(self.x.s, "it's.md")), reason)
            self.assertIn("under the working-record store " + self.x.s + ".", reason)
            os.utime(os.path.join(self.x.s, "X.md"), (0, 86400 * 365))
            self.assertIn("(modified 1971-01-01 00:00:00 UTC)", self.assertDeny("rm @S/X.md"))
            long_name = "n" * 240
            self.x.put("records/" + long_name, b"z")
            self.assertIn("...", self.assertDeny("rm @S/" + long_name))

        def test_21_non_ascii(self):
            word = chr(233) + chr(8364) + chr(9989)
            self.x.put("records/" + word + ".md", b"u")
            reason = self.assertDeny("rm @S/" + word + ".md")
            self.assertIn(word, reason)
            rc, out, err = run_hook(payload_bytes(self.x.sub("rm @S/" + word + ".md")), dict(self.env, LC_ALL="C"))
            self.assertEqual((rc, err), (0, b""))
            out.decode("ascii")
            self.assertIn(word, json.loads(out)["hookSpecificOutput"]["permissionDecisionReason"])

        # cp, mv, truncate, and tee in their in-scope forms
        def test_25_flip_cp_mv(self):
            for c in ("cp @O/X.md @S/X.md", "cp @O/X.md @O/l", "cp @O/new @S/X.md", "f=@S/X.md; cp @O/X.md \"$f\"",
                      "cp @O/X.md \"$AIQT_STORE_ROOT/X.md\"", "cp - @S/X.md"):
                self.assertIn("with cp", self.assertDeny(c))
            for c in ("mv @O/X.md @S/X.md", "mv @S/E.md @S/X.md", "mv @O/new @S/X.md"):
                self.assertIn("replace", self.assertDeny(c))
            for c in ("cp -n @O/X.md @S/X.md", "cp -f @O/X.md @S/X.md", "cp -u @O/X.md @S/X.md",
                      "cp -b @O/X.md @S/X.md",
                      "cp --backup=none @O/X.md @S/X.md", "cp -t @S @O/X.md", "cp -T @O/X.md @S/X.md",
                      "cp -- @O/X.md @S/X.md", "mv -n -f @O/X.md @S/X.md", "mv --exchange @O/X.md @S/X.md",
                      "cp @O/X.md @S/", "cp @O/X.md @S", "mv @O/X.md @S/", "cp @O/X.md @S/Y.md", "cp @S/X.md @S/X.md",
                      "cp @O/X.md", "cp @O/*.md @S/X.md", "cp @O/X.md @S/X.m?", "cp @O/a @O/b @S/X.md",
                      "mv @O/X.md @S/link", "cp @O @S/X.md", "mv @O @S/X.md", "mv @S/X.md @S/Z.md", "cp @S/X.md @O/c",
                      "cp -f @S/X.md", "mv -f @S/X.md", "cp @O/X.md @S/X.md/", "mv @O/X.md @S/X.md/"):
                self.assertAllow(c)

        def test_25_flip_truncate(self):
            for c in ("truncate -s 0 @S/X.md", "truncate -s0 @S/X.md", "truncate --size=0 @S/X.md",
                      "truncate --size 0 @S/X.md", "truncate --s=0 @S/X.md", "truncate --si 0 @S/X.md",
                      "truncate -s 00 @S/X.md", "truncate -s 0 @O/l", "truncate @S/X.md -s 0",
                      "truncate -s 0 @O/a @S/X.md"):
                self.assertIn("to zero bytes with truncate", self.assertDeny(c))
            for c in ("truncate -s 1 @S/X.md", "truncate -s +0 @S/X.md", "truncate -s -0 @S/X.md",
                      "truncate -s 0K @S/X.md",
                      "truncate -c -s 0 @S/X.md", "truncate -r @O/X.md @S/X.md", "truncate -s 0 -s 0 @S/X.md",
                      "truncate @S/X.md", "truncate -s 0 @S/E.md", "truncate -s 0 @O/X.md", "truncate -s 0 -- @S/X.md",
                      "truncate -s", "truncate --size=1 @S/X.md", "truncate -o -s 0 @S/X.md"):
                self.assertAllow(c)

        def test_25_flip_tee(self):
            for c in ("echo x | tee @S/X.md", "tee @S/X.md < /dev/null", "tee @O/a @S/X.md",
                      "echo x | sudo tee @S/X.md > /dev/null", "echo x | tee @O/l"):
                self.assertIn("with tee", self.assertDeny(c))
            for c in ("echo x | tee -a @S/X.md", "tee --append @S/X.md", "tee -i @S/X.md", "tee -p @S/X.md",
                      "tee - @S/X.md", "tee @S/Y.md", "tee @S/E.md", "tee", "tee --help @S/X.md"):
                self.assertAllow(c)

        def test_25_out_of_scope_allowed(self):
            for c in ("find @S -name X.md -delete", "find -name X.md -delete", "find @S -exec rm {} +",
                      "find @S -maxdepth 0 -type d -exec rm -r {} +", "rsync -a --delete @O/empty/ @S/",
                      "rsync -a @O/ @S/", "git -C @S clean -fdx", "git rm @S/X.md", "git checkout -- @S/X.md",
                      "unlink @S/X.md", "shred -u @S/X.md", "shred @S/X.md", "install @O/X.md @S/X.md",
                      "dd if=/dev/zero of=@S/X.md", "sed -i d @S/X.md", "perl -i -ne 1 @S/X.md",
                      "ln -sf @O/X.md @S/X.md",
                      "python3 -c 'import os; os.remove(\"@S/X.md\")'", "env -S 'rm @S/X.md'", "eval 'rm @S/X.md'",
                      "bash -c 'rm @S/X.md'", "sh -c 'rm @S/X.md'", "echo @S/X.md | xargs rm", "nice rm @S/X.md",
                      "stdbuf -o0 rm @S/X.md", "busybox rm @S/X.md", "cp -f @O/X.md @S/X.md", "truncate -s 1 @S/X.md",
                      "tee -i @S/X.md", "mv -f @O/X.md @S/X.md"):
                self.assertAllow(c)
            doc = " ".join((__doc__ or "").split())
            for name in ("find (any form", "rsync", "git's clean", "unlink", "shred", "install", "dd", "sed -i",
                         "perl -i", "env -S", "xargs", "cp and mv with any option", "truncate with any size but zero",
                         "tee with any option", "nice, stdbuf, busybox"):
                self.assertIn(name, doc, name)

        # text bash does not run as a command
        def test_26_flip_inert_text(self):
            for c in ("args=(rm -f @S/X.md)", "declare -a args=(rm -f @S/X.md)", "a=(x > @S/X.md)",
                      "[[ z > @S/X.md ]]", "[[ a < b && z > @S/X.md ]]", "if [[ z > @S/X.md ]]; then :; fi",
                      "(( 3 > 2 ))", "for ((i=0; i>@S; i++)); do :; done", "x=$(( 3 > 2 ))"):
                self.assertAllow(c, cwd=self.x.s)
            for c in ("args=($(rm @S/X.md))", "[[ $(rm @S/X.md) ]]", "[[ -n x ]] && rm @S/X.md",
                      "[[ z > @S/Y.md ]] || rm @S/X.md", "(( 1 )) && rm @S/X.md", "echo [[ > @S/X.md",
                      "args=(a); rm @S/X.md"):
                self.assertDeny(c, cwd=self.x.s)
            self.assertDeny("(( 3 > 2 )) > X.md", cwd=self.x.s)  # a redirection after the arithmetic command
            self.x.put("records/b", b"b")  # `>` inside (( )) compares with the variable b, never truncates the file b
            for c in ("(( a > b ))", "for ((i=0; i>b; i++)); do :; done", "while (( a > b )); do :; done"):
                self.assertAllow(c, cwd=self.x.s)
            self.assertDeny("echo > b", cwd=self.x.s)

        # the differential check against real bash
        def test_22_differential_bash(self):
            bash = _trusted_bash()
            if bash is None:
                self.skipTest("SKIPPED, no trusted bash: the differential check did not run")
            ran = 0
            for cmd, where, verdict, destroys, tag in CASES:
                with self.subTest(cmd=cmd):
                    tool = cmd.split()[0]
                    if tool in ("shred", "rsync", "find", "truncate", "tee", "cp", "mv", "sed", "dd", "ln",
                                "unlink") and not any(os.path.isfile(os.path.join(d, tool)) for d in ("/usr/bin",
                                                                                                    "/bin")):
                        continue
                    if tool.startswith("/") and not os.path.isfile(tool):
                        continue
                    fx = Fixture()
                    try:
                        cwd = fx.s if where == "S" else fx.o
                        text = fx.sub(cmd)
                        payload = {"hook_event_name": "PreToolUse", "tool_name": "Bash",
                                   "tool_input": {"command": text}, "cwd": cwd}
                        got = "deny" if _decide(payload, {"AIQT_STORE_ROOT": fx.s, "HOME": fx.s,
                                                          "CLAUDE_PROJECT_DIR": fx.f}) else "allow"
                        before = fx.snapshot()
                        p = subprocess.run([bash, "--norc", "--noprofile", "-c", text], cwd=cwd,
                                           stdin=subprocess.DEVNULL, capture_output=True, timeout=60,
                                           env={"LC_ALL": "C", "PATH": "/usr/bin:/bin", "HOME": fx.s,
                                                "AIQT_STORE_ROOT": fx.s, "OTHER": fx.s, "CLAUDE_PROJECT_DIR": fx.f})
                        self.assertGreaterEqual(p.returncode, 0)
                        lost = destroyed(before, fx.snapshot() if os.path.isdir(fx.s) else {})
                        ran += 1
                    finally:
                        fx.close()
                    self.assertEqual(got, verdict, (cmd, lost))
                    self.assertEqual(bool(lost), destroys, (cmd, lost, p.stderr[-200:]))
                    if lost and got == "allow":
                        self.assertIn(tag, ("miss", "marker", "exempt", "out"), cmd)
                    if not lost and got == "deny":
                        self.assertEqual(tag, "over", cmd)
                    if tag in ("miss", "marker", "exempt"):
                        self.assertTrue(lost and got == "allow", cmd)
                    if tag == "out":
                        self.assertEqual(got, "allow", cmd)
            self.assertGreater(ran, len(CASES) * 3 // 4)

        # house rules, vendoring, and cost
        def test_23_vendor_hashes(self):
            blocks = vendor_blocks()
            self.assertEqual(set(blocks), set(_VENDOR))
            for name, (head, body) in blocks.items():
                ref, first, last, digest = _VENDOR[name]
                self.assertEqual(head[4:], ["from", "%s:%d-%d" % (ref, first, last)], name)
                self.assertEqual(hashlib.sha256(body).hexdigest(), digest, name)
                self.assertEqual(_strip_source(body.decode("ascii")), body.decode("ascii"), name)  # stripped
                compile(body, name, "exec")  # the stripped block is still whole Python

        def test_23_vendor_byte_identity(self):
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

        def test_23_source_house_rules(self):
            src = open(here, "rb").read()
            src.decode("ascii")
            for n, line in enumerate(src.split(b"\n"), 1):
                self.assertLessEqual(len(line), 120, n)
            for bad in (chr(8211), chr(8212)):
                self.assertNotIn(bad.encode(), src)

        def test_23_no_internal_names(self):
            # the product surface (Claude Code, and documented variables such as CLAUDE_PROJECT_DIR) is allowed
            src = re.sub(r"\bCLA" + r"UDE_[A-Z_]+\b|\bCla" + r"ude Code\b", "", open(here).read())
            pattern = re.compile(r"\bG" + r"\d+\b|" + "|".join(
                ("cla" + "ude", "gem" + "ini", "cod" + "ex", "fab" + "le", "g" + "pt", "orches" + "trator",
                 "fl" + "eet", "orch" + "-verify", "la" + "b_in" + "fra", "OR" + "CH_[A-Z_]+")), re.I)
            found = {m.group() for m in pattern.finditer(src)}
            self.assertEqual(found - {"OR" + "CH_WORKER", "OR" + "CH_VERIFY_OWNER", "OR" + "CH_STORE_ROOT"}, set())

        def test_23_docstring_pins_residuals(self):
            doc = __doc__ or ""
            self.assertIn("RESIDUAL COVERAGE.", doc)
            self.assertIn("OUT OF SCOPE (allowed, by design)", doc)
            for item in ("a function's arguments", "brace expansion", "backtick", "CDPATH", "extended glob",
                         "what lies past the 2000-entry budget", "CLAUDE_ENV_FILE", "more than 128 target words",
                         "Between the check and the run", "No restore path is looked for", "over 64 KiB",
                         "interactive rm (-i, -I), judged as if every prompt were answered yes", "POSIXLY_CORRECT",
                         "does not verify a restore path", "a worker that destroys a record",
                         "A subagent's call (a payload carrying agent_id) is judged", "pushd, popd, env -C, sudo -D",
                         "eval, source, and `.` make both unknown", "(`flag=-r; rm \"$flag\" DIR`",
                         "(`truncate -s \"$n\"`)", "whose references are not correlated", "(`>& \"$fd\"`)",
                         "a word whose expansion would pass it is not read at all", "a hidden --help",
                         "(no variable, `~`, or glob in it)", "past the 262144-character expansion budget",
                         "a word that expands nothing is still judged", "(`cd \"\"` fails from bash 5.2 on)",
                         "an assignment to one (`f[0]=...`, `read f[0]`, `declare f[0]=...`) is not followed"):
                self.assertIn(item, " ".join(doc.split()), item)

        def test_23_strip_source(self):
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

        def test_23_strip_keeps_meaning(self):
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

        def test_24_cost_growth(self):
            # host-independent: the work is the count of Python lines this module executes (sys.settrace), which
            # no machine speed or load can change; 8x the input gives near 8x the lines when linear, 64x quadratic
            module = globals()
            roots, env = [self.x.s], self.env

            def lines_run(cmd):
                count = [0]

                def local(frame, event, arg):
                    if event == "line":
                        count[0] += 1
                    return local
                old = sys.gettrace()
                sys.settrace(lambda frame, event, arg: local if frame.f_globals is module else None)
                try:
                    hit = _verdict(self.x.sub(cmd), roots, env, self.x.o)
                finally:
                    sys.settrace(old)
                self.assertIsNotNone(hit, cmd[:40])  # reached a verdict, not failed open
                return count[0]
            for make in (lambda k: "echo x >> @S/log; " * k + "rm @S/X.md",
                         lambda k: "if :; then " * k + "rm @S/X.md; " + "fi; " * k,
                         lambda k: "cat <<EOF\n" + "line $(true)\n" * k + "EOF\nrm @S/X.md",
                         lambda k: "rm " + "@O/a " * (k // 2) + "@S/X.md",
                         lambda k: "rm -r @S/sub " + "x=$(y) " * k,
                         lambda k: "rm @S/X.md " + "\"a\"'b'c " * k):
                small, large = lines_run(make(30)), lines_run(make(240))
                self.assertLess(large / small, 16, (small, large, make(2)[:60]))

        def test_24_long_operand_is_linear(self):
            # host-independent: a word is read as a few literal runs joined once, never rebuilt per character, so
            # the pieces stay few however long it is; and the lines executed grow linearly with its length
            for n in (8000, 64000):
                raw = "a" * n
                self.assertEqual(_pieces('"' + raw + '"', raw, False), [("lit", raw)])
                self.assertEqual(_pieces(raw, raw, False), [("lit", raw)])
                mixed = "'ab'cd\"ef\"" * (n // 12)
                self.assertEqual(len(_pieces(mixed, "abcdef" * (n // 12), False)), 1)
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
                    _verdict(cmd, [self.x.s], self.env, self.x.o)
                finally:
                    sys.settrace(old)
                return count[0]
            small, large = lines_run('rm "' + "a" * 8000 + '"'), lines_run('rm "' + "a" * 64000 + '"')
            self.assertLess(large / small, 16, (small, large))  # 8x the length: linear is near 8, quadratic 64

        def test_24_expansion_budget(self):
            # host-independent: the escapes made while expanding are counted, never timed
            g, calls = globals(), [0]
            real = g["_gesc"]

            def counting(value):
                calls[0] += 1
                return real(value)
            g["_gesc"] = counting
            try:
                repeat = 'for f in a b; do rm "' + "$f" * 500 + '"; done'
                self.assertIsNone(_verdict(repeat + " " * (65536 - len(repeat)), [self.x.s], self.env, self.x.o))
                self.assertLess(calls[0], 10)
                calls[0] = 0
                names = ["v%d" % k for k in range(14)]  # 2**14 combinations: past the budget, so not read
                nested = "".join("for %s in a b; do " % n for n in names)
                nested += 'rm "' + "".join("$" + n for n in names) + '"' + "; done" * len(names)
                self.assertIsNone(_verdict(nested, [self.x.s], self.env, self.x.o))
                self.assertLessEqual(calls[0], 4 * _MAX_WALK)
            finally:
                g["_gesc"] = real
            chars = [0]

            def counting_chars(value):
                chars[0] += len(value)
                return real(value)
            g["_gesc"] = counting_chars
            try:  # a scalar doubled ten times: the characters made are charged before they are made
                doubling = ("v=" + "a" * 16000 + "; " + "v=$v$v; " * 10 + 'rm "$v"').ljust(65536)
                self.assertIsNone(_verdict(doubling, [self.x.s], self.env, self.x.o))
                self.assertLessEqual(chars[0], 2 * _MAX_EXPANDED)
            finally:
                g["_gesc"] = real
            copied = [0]  # the exact 64 KiB prefix-copy shape: no copy is made that the budget did not allow
            real_fork = g["_fork"]

            def counting_fork(c):
                copied[0] += c[3]
                return real_fork(c)
            g["_fork"] = counting_fork
            try:
                prefix = ("for v in " + "'' " * 1000 + '; do rm "' + "a" * 61000 + '$v"; done').ljust(65536)
                self.assertIsNone(_verdict(prefix, [self.x.s], self.env, self.x.o))
                self.assertLessEqual(copied[0], 2 * _MAX_EXPANDED)
            finally:
                g["_fork"] = real_fork
            storm = "v=" + "a" * 16000 + "; " + "v=$v$v; " * 5  # the budget spent: expanding words are not read,
            for tail in ("rm @S/X.md", ": > @S/X.md", "rm X.md"):  # but a literal word after it is still judged
                cmd = (storm + tail).replace("@S", self.x.s)
                self.assertIsNotNone(_verdict(cmd, [self.x.s], self.env, self.x.s), tail)
            self.assertIsNone(_verdict((storm + 'rm "$v" @S/Y.md').replace("@S", self.x.s), [self.x.s], self.env,
                                       self.x.o))
            module, forgets = globals(), {}  # 400 invalidations of 1990 variables: each costs the same few lines

            def lines_for(cmd):
                count = [0]

                def local(frame, event, arg):
                    if event == "line":
                        count[0] += 1
                    return local
                sys.settrace(lambda frame, event, arg: local if frame.f_globals is module else None)
                try:
                    _verdict(cmd, [self.x.s], self.env, self.x.o)
                finally:
                    sys.settrace(None)
                return count[0]
            for verb in ('printf -v "$u" x; ', "eval x; "):
                one, many = (lines_for((" ".join("v%d=x" % i for i in range(1990)) + "; " + verb * n
                                        + "rm missing").ljust(65536)) for n in (1, 400))
                forgets[verb] = many - one
                self.assertLess(many - one, 399 * 1200, (verb, one, many))  # a statement: about 450 lines; a
                # rebuild of the 1990 variables adds some 2000 more to each
            counted = [0]  # 7000 distinct variables: references are counted in one pass, no list.count scans

            def profile(frame, event, arg):
                if event == "c_call" and getattr(arg, "__name__", "") == "count":
                    counted[0] += 1
            many = ('rm "' + "".join("${v%d}" % i for i in range(7000)) + '"').ljust(65536)
            sys.setprofile(profile)
            try:
                self.assertIsNone(_verdict(many, [self.x.s], self.env, self.x.o))
            finally:
                sys.setprofile(None)
            self.assertEqual(counted[0], 0)
            module, lines = globals(), [0]  # empty values make no characters: the entry budget alone must stop them

            def local(frame, event, arg):
                if event == "line":
                    lines[0] += 1
                return local
            empties = "".join("for %s in '' ''; do " % n for n in names)
            empties += 'rm "' + "".join("$" + n for n in names) + '"' + "; done" * len(names)
            sys.settrace(lambda frame, event, arg: local if frame.f_globals is module else None)
            try:
                self.assertIsNone(_verdict(empties, [self.x.s], self.env, self.x.o))
            finally:
                sys.settrace(None)
            self.assertLess(lines[0], 100000, lines[0])  # unbounded, 2**14 values: well over a million lines
            self.assertIsNotNone(_verdict("for a in @O @S; do for b in X Y; do rm $a/$b.md; done; done".replace(
                "@O", self.x.o).replace("@S", self.x.s), [self.x.s], self.env, self.x.o))

        def test_24_walk_is_bounded(self):
            # host-independent: the entries examined are counted, never timed
            for k in range(3 * _MAX_WALK):
                self.x.put("records/wide/%05d" % k, b"")
            seen, real = [0], os.scandir

            class Counting:
                def __init__(self, d):
                    self.it = real(d)

                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    self.it.close()

                def __iter__(self):
                    for e in self.it:
                        seen[0] += 1
                        yield e
            os.scandir = Counting
            try:
                self.assertIsNone(self.decide("rm -r @S/wide"))
            finally:
                os.scandir = real
            self.assertGreaterEqual(seen[0], _MAX_WALK)  # the walk ran (a count of zero proves nothing)
            self.assertLessEqual(seen[0], _MAX_WALK + 1)

        def test_24_hang_ceiling(self):
            # each run is bounded by run_hook's hang guard (far above the real runtime), never a latency bound
            env = dict(self.env, LC_ALL="C")
            shapes = ("rm @S/Y.md; " * 3000, "rm " + "a " * 30000, "echo " + "$(" * 5000, "rm x " + "#r " * 7000,
                      "echo " + "((" * 30000, "{ " * 20000 + "rm @S/X.md",
                      "cat > @O/f " + "<<E " * 3000 + "\nx\n" * 3000,
                      "echo x " + "> @O/f " * 5000, "rm @S/X.md" + " # record-rm-ok: x" * 3000)
            for c in shapes:
                rc, out, err = run_hook(payload_bytes(self.x.sub(c), self.x.o), env)
                self.assertEqual((rc, err), (0, b""), c[:40])
                self.assertLessEqual(out.count(b"\n"), 1, c[:40])

    suite = unittest.defaultTestLoader.loadTestsFromTestCase(T)
    result = unittest.TextTestRunner(stream=sys.stderr, verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
