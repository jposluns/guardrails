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
    status: 0, or the blocking exit 2 when a DENY line cannot be written to stdout and flushed (a lost deny
    blocks, never allows; a lost note keeps exit 0); the decision travels in the JSON. This hook never asks.

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
      (a) pkill OPTS LITERAL [R]: OPTS hold -f or --full, and optionally -e or --echo, one SIG, and `--` just
          before LITERAL; exactly one operand;
      (b) for V in $(pgrep F LITERAL); do BODY; done, where F is -f or --full, optionally then `--`, and BODY is
          simple commands separated by `;` or newlines: exactly one `kill [SIG] "$V" [R]` or `kill [SIG] $V [R]`
          naming the loop's own V, and otherwise only echo (an argument may be "$V"), sleep N, `:` or true;
      (c) kill [SIG] $(pgrep F LITERAL) [R], the substitution unquoted and the only operand, or the two-stage
          pipeline pgrep F LITERAL | xargs [-r | --no-run-if-empty] kill [SIG] [R];
      (d) while pgrep G LITERAL [R]; do BODY; done, or until ! pgrep G LITERAL [R]; do BODY; done, where G is F
          plus optional separate -a, -l or -c, and BODY holds only sleep N, echo, `:`, true and date.
    R is one or more of >/dev/null, 2>/dev/null, 2>&1 and &>/dev/null after the command's last word, with or
    without a blank before it: as in bash, `>` ends a word, so `qa-x/>/dev/null` is the word qa-x/ and then
    >/dev/null, while `qa-x/2>&1` is the word qa-x/2 and then >&1, which is not in R. A bare word of digits alone
    whose value is at most 2147483647 (leading zeros allowed) needs the blank: bash reads `15>/dev/null` as a
    redirection of descriptor 15, not as the word 15 and then >/dev/null, so `pkill -f 15>/dev/null` (pkill gets
    no pattern), `pkill -f 2147483647>/dev/null` (bash reports a bad file descriptor and runs no pkill) and
    `while pgrep -f 7>/dev/null; ...` (the loop ends at once) take the note, while `pkill -f 15 >/dev/null`,
    `pkill -f "15">/dev/null` and `pkill -f 2fa>/dev/null` are denied. bash 5.3.9 reads a run of digits whose
    value is larger, such as 2147483648, as a word before `>` (`pgrep -f 2147483648>/dev/null` ran and matched),
    so `pkill -f 2147483648>/dev/null` is denied too. On
    the signalling command R changes only where messages go, and on the pgrep of (d) only what is shown. A shape
    may be followed by one `|| CMD` or `&& CMD`, CMD a benign simple command (below), and the kill in a (b) body
    by one `|| CMD` or `&& CMD`, CMD an echo, sleep N, `:` or true as that body allows: CMD runs, if at all, only
    after the shape or the kill has run. V is an identifier that holds a lowercase letter (so no all-caps name,
    and not `_`), does
    not start with BASH and is not a reserved word, so it is none of bash's all-caps special names: assigning a
    readonly one (EUID, UID, PPID) stops the loop, and others (RANDOM, SRANDOM, SECONDS, LINENO, IFS, PATH) change
    the value kill gets or how the shell runs it. auto_resume and histchars, special names that hold a lowercase
    letter, are allowed: neither is readonly, and neither changes the value assigned to it (bash 5.3.9 printed
    both loop values back unchanged). N is a plain number with an optional s, m, h or d suffix. The sleeps that
    can run before the first shape add up to at most 60 seconds, so no sleep keeps the shape from running
    (`sleep infinity` anywhere, or `sleep 99999d` before the shape, takes the note): the sleeps in the elements
    before the shape and, when the shape is (b), the sleeps in its body, counted once (one in a `|| CMD` or
    `&& CMD` there too). A (d) body is not counted: its pgrep runs before any body sleep, and the loop waits on
    its own shell whatever the body sleeps. A shape is a whole element of the top-level list, whose elements are
    split by `;` or newlines. Every
    other element is a benign simple command: echo, sleep N, pwd, true, `:`, date or ls, with LITERAL or quoted
    arguments holding no expansion, no argument starting with `-` (an option, quoted or not), and redirections from
    R. printf and cd are not benign: `printf -v` assigns a variable (PATH, or the loop variable), a printf field
    width can write gigabytes before the shape runs, and cd can change what a relative PATH entry finds. No
    element holds `&&`, `||` (other than the trailing forms above), `&`, an assignment, a function definition or a
    prefix word (exec, sudo, env, nice, timeout, command, nohup and the rest), and every element after the shape
    parses too, since an unparsable tail could stop the shape running.
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
    The comment must be unquoted, begin a word (after a blank, a newline, `;`, `&` or `|`), and be the last
    non-blank content of the command; a `#` inside a word or in a quoted string is not a comment and does not opt
    out. A `#` directly after `)` does not opt out either. After a command substitution, `$(...)#`, bash reads no
    comment: the `#` continues the last word the substitution gives (`$(echo 11 22)#` gives the words 11 and 22#).
    After a subshell, `(...)#`, bash does read a comment, but the hook deliberately does not accept it as an
    opt-out (hook policy, not bash's reading), and such a command gets the note. The opt-out silences both the
    deny and the note.

THREAT MODEL
    This is an accidental-habit guard, not a security boundary. The actor is a well-meaning assistant that stops or
    waits on work by a name pattern and forgets that the name is also in its own shell's command line; nothing here
    resists a caller that sets out to hide a pattern. The deny set is closed, and each shape in it selects the
    running shell under the premises in RESIDUAL COVERAGE; everything uncertain gets a note at most. An internal
    error while reading a command allows it WITH a note that it was not checked, and so does a payload the hook
    cannot evaluate: a stdin that is closed or fails to read, one over 16 MiB in all (every byte read through the
    end of input counts, blanks after the JSON too), one whose end of input (EOF) the hook does not read within 2
    seconds by time.monotonic (the clock is read again after every wait and every read, before an end of input
    is accepted, so input that ends after the deadline is refused however late the OS runs the hook; input that
    ends in time but is read late is refused too), one with bytes other than JSON blanks (space, tab, CR, LF)
    after its JSON, one that is not JSON in strict UTF-8 (bytes that are not valid UTF-8, encoded surrogates, a
    byte order mark, UTF-16 and UTF-32 included, and the constants NaN, Infinity and -Infinity, which JSON does
    not have), a JSON value that is not an object, and a Bash call whose
    tool_input is not an object or whose command is not a string. An argv other than the plain hook call or
    exactly `--self-test` gets a note too (a stdin that is a directory excepted, since the registration line
    exits before Python starts), that the hook checked nothing, written before stdin is read (stdin is left
    unread); a verification worker process gets its stderr line instead. A JSON prefix followed by an idle interval is
    not taken as the whole payload: the hook reads on until the input ends, so a host that keeps stdin open after
    the payload gets the cannot-evaluate note on every Bash call. Each wait is for the time left
    at most, so that note comes about 2 seconds late, but the OS can return from a wait late, and nothing here
    bounds how much later. The one exception is an interpreter older than Python 3.14 that can start the hook: the guard
    at the top of this file reads no input, writes one line beginning
    `error: pattern-self-match.py requires Python 3.14 or newer` to stderr (a best-effort write: the exit
    does not depend on it) and exits 2, which PreToolUse treats as
    a deny, so every Bash call is denied until Python is upgraded or the hook's entry is removed. An older
    interpreter that cannot start the hook never reaches the guard and fails with Python's own error first. For
    this hook that is only one that predates the -I option, and it exits 2, which still denies every Bash call:
    this file is meant to hold no f-string or other syntax newer than Python 3.4, so that any interpreter that
    accepts -I reaches the guard. The self-test checks for f-strings, parses the file with the parser's 3.4
    grammar setting, and scans the syntax tree and tokens for these newer forms that grammar setting accepts: a
    starred item in a display or subscript ([*a], x[*a], return *a, b), {**a}, f(*a, b), f(**a, **b), a trailing
    comma after a starred parameter or argument (lambda *a,: 0 too), a decorator that is not a dotted name or a
    call of one (@a[0].b, @(a)), a parenthesized with (with (a as b, c as d):), and continue inside finally. A
    newer form outside these checks would go unnoticed, and an older interpreter would then fail to compile the
    file and exit 1, which PreToolUse treats as non-blocking. .preview/README.md (Installing a hook, step 4)
    describes those cases.
    The hook writes nothing to stdout in exactly these cases: a verification worker process (a worker kill-switch
    variable; legacy spellings are also honoured), where it writes one line to stderr saying it skipped, whatever
    its argv other than a lone `--self-test`, and reads nothing; a JSON
    object whose tool_name is not Bash or whose event is not PreToolUse; a Bash call whose command is empty; a
    stdin that is a directory, which the REGISTRATION launch line answers before Python starts; a command the
    NOTE paragraph leaves silent or that opts out; and an output failure, when the line cannot be written. A
    payload carrying agent_id (a subagent's call) is checked like any other. Work is bounded: a
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
      - a shell function or alias (one defined in a shell snapshot, say) that shadows a command word the grammar
        accepts (kill, pkill, pgrep, xargs, echo, sleep, ls, date, pwd, true or `:`) can change what a shape runs,
        or keep it from running, and the deny assumes no alias named for a reserved word (for, while, until, do,
        done, if, then, fi, case, esac or `!`): with expand_aliases on, one named for, while, until, do or done
        changes the loop that word belongs to (bash 5.3.9 then reported a syntax error and ran no loop), and one
        named `!` changes what `until ! pgrep ...` tests (with `alias '!'='true;'`, bash 5.3.9 reported no error
        and ended such a loop at once);
      - shell state set before the command (by a shell snapshot, say): an IFS without a newline changes how
        $(pgrep ...) splits, and a loop variable made readonly, a nameref or otherwise given an attribute changes
        the loop;
      - /dev/null opens for writing (a redirection that fails stops its command), and a benign command finishes
        (an ls on a hung network mount, or an echo into a full pipe, would keep a later shape from running);
      - a trap on the shell: the deny says the command selects or signals its own shell, not that the shell dies.
    What is not denied: everything outside the closed set gets a note at most, including compound commands (`if`,
    groups, subshells), `&&` and `||` lists other than one trailing `|| CMD` or `&& CMD` as above, `ps | grep`
    pipelines, prefix words such as `sudo` or `exec`, name mode (pkill without -f), killall and pidof, other loops,
    a pattern outside LITERAL (one holding a blank, as in `pkill -f 'python worker.py'`),
    options outside the lists above (an option cluster such as -ef too), printf, cd, an echo or other benign
    command with an argument starting with `-`, a loop variable outside V's rule, sleeps over the 60-second bound,
    redirections outside R, an R redirection in a shape anywhere but after the last word of its signalling command
    or of the pgrep of (d) (so `kill $(pgrep -f x/ 2>/dev/null)` takes the note), and nested shell strings. A
    pattern built when the command runs
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
    import os
    try:
        sys.stderr.write(
            "error: pattern-self-match.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

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

# The hook's own reading. It does not call the vendored _read_payload, which returns a complete JSON prefix that an
# idle interval follows (that may not be the whole payload) and does not say how many bytes it read, so the bytes
# after that prefix could not be counted toward _MAX_INPUT. Only the self-test (test_m45) calls _read_payload.
_LATE = "hook payload did not end within the read deadline"


def _no_constant(name):
    """parse_constant for json.loads: NaN, Infinity and -Infinity are not JSON (RFC 8259, section 6)."""
    raise ValueError("hook payload has " + name + ", which is not JSON")


def _read_complete(fd=0, deadline=_READ_DEADLINE):
    """The payload, parsed once the input has ended (EOF) and that end was read before the deadline. Every byte
    read through the end counts toward one _MAX_INPUT budget. The clock is read again after each wait and after
    each read, before an end of input is accepted: a reading taken after a read returns is never earlier than
    the read, however late the OS runs this process, so an end of input that comes after the deadline is refused.
    Each wait is for the time left at most, but the OS can return from a wait late, so refusing can take longer
    than the deadline. The whole input must be one JSON value in strict UTF-8 with no byte order mark (BOM), so
    only JSON blanks (space, tab, CR and LF) may follow it; UTF-16, UTF-32 and encoded surrogates are not
    UTF-8. NaN, Infinity and -Infinity, which json.loads takes by default, are not JSON and are refused. Raises
    ValueError for more than _MAX_INPUT bytes, for no end read before the deadline, and for input that is not one
    JSON value in strict UTF-8 (UnicodeDecodeError is a ValueError)."""
    end = time.monotonic() + deadline
    data = bytearray()
    while True:
        left = end - time.monotonic()
        if left <= 0:
            raise ValueError(_LATE)
        ready = select.select([fd], [], [], left)[0]
        if time.monotonic() >= end:  # after the wait: a wait the OS ended late is not followed by a read
            raise ValueError(_LATE)
        if not ready:
            continue
        try:
            chunk = os.read(fd, 65536)
        except BlockingIOError:
            continue
        if time.monotonic() >= end:  # after the read, before its end of input is accepted
            raise ValueError(_LATE)
        if not chunk:  # strict UTF-8 first: json.loads on bytes would also take UTF-16, UTF-32 and surrogates
            return json.loads(bytes(data).decode("utf-8", "strict"), parse_constant=_no_constant)
        data += chunk
        if len(data) > _MAX_INPUT:
            raise ValueError("hook payload over the read bound")


_SCAN_LIMIT = 64 * 1024
# A LITERAL's value, and a bare word: only these characters (a LITERAL also must not start with `-`).
_LITERAL_RE = re.compile(r"[A-Za-z0-9_./:@%=,-]+\Z")
_BARE_RE = re.compile(r"[A-Za-z0-9_./:@%=,-]+")
# A bare word of digits alone, and the largest value bash reads as a file descriptor number directly before `>`
# (_descriptor); bash 5.3.9 reads a larger value as a word.
_DIGITS_RE = re.compile(r"[0-9]+\Z")
_FD_MAX = 2147483647
_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_DQ_VAR_RE = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)\Z")
# The redirections R, at a token's start: >, 2> or &> onto /dev/null (a blank may stand before the target), or 2>&1.
_REDIR_RE = re.compile(r"(2>|&>|>)[ \t]*/dev/null|2>&1")
# What may follow a word, a variable, a redirection or `)`: a blank, a newline, `;`, `|`, `&`, `)`, `>` (as in bash,
# it ends the word and starts a redirection, which must then be one of R; a bare word bash reads as a descriptor
# number before it is refused, _descriptor), or the end.
_AFTER_WORD = frozenset(" \t\n;|&)>")
_SLEEP_RE = re.compile(r"([0-9]+(?:\.[0-9]+)?)([smhd]?)\Z")
_SLEEP_UNITS = {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}
# Seconds: the most the sleeps before the first shape, and a (b) shape's body counted once, may add up to.
_SLEEP_TOTAL = 60
_SIG_VALUES = frozenset(("9", "15", "1", "2", "3", "KILL", "TERM", "HUP", "INT", "QUIT",
                         "SIGKILL", "SIGTERM", "SIGHUP", "SIGINT", "SIGQUIT"))
# The signal spellings each signaller reads: pkill reads `-s` as a session, and bash's builtin kill rejects
# `--signal` (it reports an invalid signal specification and sends nothing); the kill that xargs runs takes all.
_SIG_FORMS = {"pkill": ("dash", "long"), "kill": ("dash", "s"), "xargs-kill": ("dash", "s", "long")}
# printf and cd are left out: `printf -v` assigns a variable, a printf field width can write gigabytes, and cd can
# change what a relative PATH entry finds.
_BENIGN = frozenset(("echo", "sleep", "pwd", "true", ":", "date", "ls"))
_FOR_BODY = frozenset(("echo", "sleep", ":", "true"))
_WAIT_BODY = frozenset(("echo", "sleep", ":", "true", "date"))
_FULL = frozenset(("-f", "--full"))
_LISTING = frozenset(("-a", "-l", "-c"))
_RESERVED = frozenset(("for", "in", "do", "done", "while", "until", "if", "then", "else", "elif", "fi", "case",
                       "esac", "select", "function", "time", "coproc"))
# The operators of a trailing `|| CMD` or `&& CMD`.
_ANDOR = (("op", "||", None), ("op", "&&", None))
# The characters that make a "..." string an expansion.
_DQ_SPECIAL = "$`\\"
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
_OPT_OUT_RE = re.compile(r"(?<![^ \t\n;&|])#[ \t]*self-match-ok(?:[ \t:][^\n]*)?\Z")

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
_NOTE_PAYLOAD = ("pattern-self-match: the hook could not read this call's payload (cannot evaluate), so the call "
                 "was not checked for a pattern that selects the shell running it.")
_NOTE_ARGV = ("pattern-self-match: the hook was launched with {} (only a lone --self-test is accepted), so it checked "
              "nothing: the call was not checked for a pattern that selects the shell running it.")
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
    of R, spelled without blanks. A `$(` may not nest, every word stands alone (no word is joined to a quote, a
    variable or a substitution), and no bare word that bash reads as a descriptor number (_descriptor) stands
    directly before `>`."""
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
            elif any(ch in body for ch in _DQ_SPECIAL):
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
            if not m or (_descriptor(m.group()) and text.startswith(">", m.end())):
                raise _Refused  # bash reads `15>` as descriptor 15, so the digits are no word
            tok, i = ("w", m.group(), "bare"), _end(text, m.end())
        out.append(tok)
    if depth:
        raise _Refused
    return out


def _descriptor(word):
    """True for a bare word that bash reads as a file descriptor number directly before `>`: digits alone, leading
    zeros allowed, with a value of at most _FD_MAX (the length test keeps int() off a long run)."""
    if not _DIGITS_RE.match(word):
        return False
    value = word.lstrip("0")
    return len(value) <= len(str(_FD_MAX)) and int(value or "0") <= _FD_MAX


def _w(tok, value=None):
    """True for a bare word token (equal to value, when one is given)."""
    return tok is not None and tok[0] == "w" and tok[2] == "bare" and (value is None or tok[1] == value)


def _literal(tok):
    """True for a word token whose value is a LITERAL."""
    return tok[0] == "w" and bool(_LITERAL_RE.match(tok[1])) and not tok[1].startswith("-")


def _plain(tok):
    """True for an argument holding no expansion and no option: a LITERAL bare word, or a quoted word that does not
    start with `-`."""
    return tok[0] == "w" and (_literal(tok) if tok[2] == "bare" else not tok[1].startswith("-"))


def _seconds(word):
    """The seconds the `sleep` operand word stands for, or None when it is not an N."""
    m = _SLEEP_RE.match(word)
    return float(m.group(1)) * _SLEEP_UNITS[m.group(2)] if m else None


def _slept(run):
    """The seconds the simple command run sleeps: its N for `sleep N`, else 0; for `X || CMD` or `X && CMD`, the
    sleeps of X and CMD."""
    k = _andor(run)
    if k < len(run):
        return _slept(run[:k]) + _slept(run[k + 1:])
    if not run or not _w(run[0], "sleep"):
        return 0
    return sum(_seconds(t[1]) or 0 for t in run[1:] if t[0] == "w")


def _unredirected(run):
    """run without the redirections from R after its last word."""
    k = len(run)
    while k > 0 and run[k - 1][0] == "r":
        k -= 1
    return run[:k]


def _andor(run):
    """The index of the first `||` or `&&` in run, or len(run)."""
    for k, t in enumerate(run):
        if t in _ANDOR:
            return k
    return len(run)


def _trailer(toks, i, names, var=None):
    """The index past a `|| CMD` or `&& CMD` at toks[i] that reaches the next `;`, newline or the end, CMD a simple
    command named in names (_simple; redirections from R only for _BENIGN); i when there is none."""
    if i < len(toks) and toks[i] in _ANDOR:
        e = _upto_sep(toks, i + 1)
        if _simple(toks[i + 1:e], names, redirs=names is _BENIGN, var=var):
            return e
    return i


def _loop_var(name):
    """True for a loop variable V: an identifier holding a lowercase letter, not starting with BASH, and not a
    reserved word."""
    return (bool(_NAME_RE.fullmatch(name)) and any("a" <= ch <= "z" for ch in name) and not name.startswith("BASH")
            and name not in _RESERVED)


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
    """True for a simple command named in names whose arguments hold no expansion and no option (_plain; with var,
    echo may also take "$var"), with `sleep` given exactly one N; redirections from R only when redirs."""
    if not run or not _w(run[0]) or run[0][1] not in names:
        return False
    args = []
    for t in run[1:]:
        if t[0] == "r" and redirs:
            continue
        if not (_plain(t) or (var is not None and t == ("v", var, True) and run[0][1] == "echo")):
            return False
        args.append(t)
    if run[0][1] == "sleep":
        return len(args) == 1 and args[0][0] == "w" and _seconds(args[0][1]) is not None
    return True


def _kill_var(run, var):
    """True for `kill [SIG] "$var" [R]` or `kill [SIG] $var [R]` (bash's builtin kill)."""
    run = _unredirected(run)
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
    """Shape (b) at toks[i]: (("signal", LITERAL), the index past `done`, the body's commands), or raise
    _Refused."""
    n = len(toks)
    if (i + 4 >= n or not _w(toks[i + 1]) or not _loop_var(toks[i + 1][1])
            or not _w(toks[i + 2], "in") or toks[i + 3] != ("op", "$(", None)):
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

    def check(run):
        k = _andor(run)
        if k < len(run):
            return _kill_var(run[:k], var) and _trailer(run, k, _FOR_BODY, var) == len(run)
        return _kill_var(run, var) or _simple(run, _FOR_BODY, var=var)
    end, runs = _body(toks, j + 1, check)
    if sum(1 for run in runs if _kill_var(run[:_andor(run)], var)) != 1:
        raise _Refused
    return ("signal", lit), end, runs


def _wait(toks, i):
    """Shape (d) at toks[i]: (("wait", LITERAL), the index past `done`, the body's commands), or raise
    _Refused."""
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
    end, runs = _body(toks, j + 1, lambda run: _simple(run, _WAIT_BODY))
    return ("wait", lit), end, runs


def _element(toks, i):
    """(the shape found, or None for a benign command; the index past the element; its simple commands whose sleeps
    can run before the shape: a benign command itself, a (b) body, and nothing for (d), whose pgrep runs before
    its body) for the element at toks[i]; raise _Refused for anything else. A shape may end with one `|| CMD` or
    `&& CMD`, CMD benign, and its signalling command with redirections from R."""
    t = toks[i]
    if _w(t, "for") or _w(t, "while") or _w(t, "until"):
        found, end, runs = (_for if _w(t, "for") else _wait)(toks, i)
        return found, _trailer(toks, end, _BENIGN), runs if _w(t, "for") else []
    j = _upto_sep(toks, i)
    run = toks[i:j]
    k = _andor(run)
    if k < len(run) and _trailer(toks, i + k, _BENIGN) != j:
        raise _Refused
    core = run[:k]
    for shape in (_pkill, _kill_sub, _xargs_kill):
        lit = shape(_unredirected(core))
        if lit is not None:
            return ("signal", lit), j, []
    if k == len(run) and _simple(run, _BENIGN, redirs=True):
        return None, j, [run]
    raise _Refused


def _shape(toks):
    """The first shape, (kind, LITERAL), of a command whose every element is a shape or a benign command; None
    when it holds no shape. Raise _Refused when any element is neither, or when the sleeps up to and through the
    first shape add up to more than _SLEEP_TOTAL seconds."""
    first, slept, i, n = None, 0, 0, len(toks)
    while True:
        while i < n and toks[i] == _SEPS[1]:
            i += 1
        if i >= n:
            return first
        found, i, runs = _element(toks, i)
        if first is None:
            slept += sum(_slept(run) for run in runs)
            if slept > _SLEEP_TOTAL:
                raise _Refused
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
    if _is_worker(env):
        return None
    if not isinstance(payload, dict):
        return {"systemMessage": _NOTE_PAYLOAD}
    if payload.get("tool_name") != "Bash" or payload.get("hook_event_name", "PreToolUse") != "PreToolUse":
        return None
    tool_input = payload.get("tool_input")
    cmd = tool_input.get("command") if isinstance(tool_input, dict) else None
    if not isinstance(cmd, str):
        return {"systemMessage": _NOTE_PAYLOAD}
    if not cmd:
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
    that rescue fails, end the process at once with status 0: every line this path carries is advisory (a
    note, or the worker line on stderr), so losing one keeps exit 0 (the deny line goes through
    _emit_deny_line, which fails closed when the deny cannot be written and flushed)."""
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


def _emit_deny_line(text):
    """Write the DENY line to stdout and flush it. A deny that cannot be both written and flushed never
    provably reached the platform, and the silent exit 0 reads as an allow, so the failure is noted on
    stderr (best-effort: write, then flush, each failure swallowed) and the process ends at once with the
    blocking exit 2 through os._exit, which skips the interpreter's exit flush (a stream that buffered a
    failed write raises again there, and the interpreter's own status, 120, is non-blocking). A lost deny
    blocks, never allows; a lost NOTE still travels _emit_line's fail-open path and keeps exit 0."""
    try:
        sys.stdout.write(text + "\n")
        sys.stdout.flush()
    except BaseException:
        try:
            sys.stderr.write("pattern-self-match: the deny decision could not be written to stdout; failing "
                             "closed with exit 2 (a lost deny blocks, never allows).\n")
            sys.stderr.flush()
        except BaseException:
            pass
        os._exit(2)


def main(argv):
    """The hook: 0, or, through _emit_deny_line, the blocking exit 2 when a deny line cannot be written and
    flushed (a lost deny blocks, never allows); output only a deny or note line (a payload it cannot read
    gets the cannot-evaluate note).
    `--self-test` alone runs the self-test instead. Otherwise a verification worker process writes only
    the worker line, to stderr, whatever its argv; any other argv gets the argv note (_NOTE_ARGV). Both return 0
    before stdin is read."""
    readable = isinstance(argv, (list, tuple)) and bool(argv) and all(isinstance(a, str) for a in argv)
    if readable and list(argv[1:]) == ["--self-test"]:
        return _self_test()
    if _is_worker(os.environ):  # before the argv notes: a worker process writes nothing to stdout (QA round 15)
        _emit_line(_WORKER_LINE, sys.stderr)
        return 0
    if not readable:  # a bad argv: it checks nothing, and says so, reading nothing
        _emit_line(json.dumps({"systemMessage": _NOTE_ARGV.format("an argument list it cannot read")}))
        return 0
    if len(argv) != 1:  # an argument list it does not accept: it checks nothing, and says so, reading nothing
        _emit_line(json.dumps({"systemMessage": _NOTE_ARGV.format("an argument list it does not accept")}))
        return 0
    try:
        payload = _read_complete()
    except Exception:
        payload = None  # a closed or failing stdin, over 16 MiB, incomplete, or not JSON: _decide notes it
    try:
        out = _decide(payload, os.environ)
    except Exception:
        out = {"systemMessage": _NOTE_ERROR}
    if out is not None:
        if "hookSpecificOutput" in out:  # a deny decision: lost means blocked, never allowed
            _emit_deny_line(json.dumps(out))
        else:
            _emit_line(json.dumps(out))
    return 0


def _self_test():
    import ast
    import hashlib
    import inspect
    import io
    import shutil
    import signal
    import stat
    import subprocess
    import tempfile
    import tokenize
    import unittest

    here = os.path.abspath(__file__)
    module = globals()
    # the real clock, sleep and select for the test's own writes to a child (T.child), taken before any test
    # replaces the module's time or select
    wall_clock, wall_sleep, wall_select = time.monotonic, time.sleep, select.select
    # The child that T.child_read runs: this file loaded as a module (its main does not run), _MAX_INPUT set when
    # argv[4] is not "-", then the reader named argv[2] called on stdin with deadline argv[3] ("-": the reader's
    # default); it writes one line, ["payload", value] or ["refused", the ValueError's message].
    DRIVER = "\n".join((
        "import importlib.util, json, sys",
        "spec = importlib.util.spec_from_file_location('hook', sys.argv[1])",
        "hook = importlib.util.module_from_spec(spec)",
        "spec.loader.exec_module(hook)",
        "if sys.argv[4] != '-':",
        "    hook._MAX_INPUT = int(sys.argv[4])",
        "args = () if sys.argv[3] == '-' else (0, float(sys.argv[3]))",
        "try:",
        "    out = ['payload', getattr(hook, sys.argv[2])(*args)]",
        "except ValueError as e:",
        "    out = ['refused', str(e)]",
        "sys.stdout.write(json.dumps(out) + '\\n')"))

    # The child that T.run_hook runs: this file loaded as a module, its main called with this file's path and the
    # arguments after it, its _read_complete given 20 seconds whatever arguments main passes, 10 seconds below the
    # child's default parent-observed bound of 30 (T.child). That 10 seconds is a NOMINAL margin: the child's
    # start-up, the host's scheduling and the time to report the result all come out of it, so a reader that waits
    # its whole deadline exits before the kill only while those delays total under 10 seconds, and a verdict
    # there depends on how fast the host runs the child only when the host stalls it (or this test) for about
    # that long (QA rounds 11 to 14). The arguments main passes, stdin at the
    # production deadline, are pinned in process (T.scheduled with via_main); the script's own entry point runs
    # on the actual command line in T.hook and T.child with [here].
    MAIN = "\n".join((
        "import importlib.util, sys",
        "spec = importlib.util.spec_from_file_location('hook', sys.argv[1])",
        "hook = importlib.util.module_from_spec(spec)",
        "spec.loader.exec_module(hook)",
        "read = hook._read_complete",
        "hook._read_complete = lambda *a, **k: read(0, 20.0)",
        "sys.exit(hook.main(sys.argv[1:]))"))

    def decide(command, env=None, **extra):
        payload = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": command}}
        payload.update(extra)
        return _decide(payload, {} if env is None else env)

    def payload_bytes(command, **extra):
        payload = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": command}}
        payload.update(extra)
        return json.dumps(payload).encode()

    def not_strict_utf8():
        """The thirteen payloads that json.loads parses from bytes but that are not strict UTF-8 without a BOM:
        four with encoded surrogates (a lone high one, a lone low one and a CESU-8 pair in echo hi, a lone high
        one in a deny), one in UTF-8 after a BOM, and eight in UTF-16 and UTF-32, each of them little-endian and
        big-endian, each of those without and with a BOM. Parsed, each Bash call would get the deny, silence or
        another note, not the cannot-evaluate note."""
        deny = payload_bytes("pkill -f qa-x/")
        out = [payload_bytes("echo hi").replace(b"hi", b"h" + s + b"i")
               for s in (b"\xed\xa0\x80", b"\xed\xbf\xbf", b"\xed\xa0\xbd\xed\xb8\x80")]
        out.append(deny.replace(b"qa-x/", b"qa-x/\xed\xa0\x80"))
        out.append(b"\xef\xbb\xbf" + deny)
        for codec in ("utf-16-le", "utf-16-be", "utf-32-le", "utf-32-be"):  # each byte order built explicitly
            body = deny.decode("ascii").encode(codec)
            out += [body, "\ufeff".encode(codec) + body]
        return out

    def non_json_constants():
        """Payloads that json.loads parses by default but that are not JSON: NaN, Infinity and -Infinity, each as
        an extra member of a deny and of echo hi (which would be silent)."""
        return [payload_bytes(command)[:-1] + b', "extra": ' + name.encode() + b"}"
                for name in ("NaN", "Infinity", "-Infinity") for command in ("pkill -f qa-x/", "echo hi")]

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
        ("pwd; pkill -f qa-x.d", "signal", "qa-x.d"),
        ("echo '# self-match-ok: x'; pkill -f qa-x/", "signal", "qa-x/"),
    ]
    # More deny forms, from the signal, grammar and spelling tests below; bash -n reads them too.
    deny_more = [
        "pkill -SIGKILL -f qa-x/", "pkill --signal KILL --full qa-x/", "kill -s KILL $(pgrep -f qa-x/)",
        "kill -SIGTERM $(pgrep --full -- qa-x/)", "pgrep -f qa-x/ | xargs --no-run-if-empty kill --signal TERM",
        "pgrep -f qa-x/ | xargs kill -s HUP", 'for p in $(pgrep -f qa-x/); do kill -s 9 "$p"; done',
        "for p in $(pgrep -f qa-x/)\ndo\n  echo \"$p\"\n  kill $p\n  sleep 0.5\ndone",
        "while pgrep -a -f qa-x/ &>/dev/null; do date; echo waiting; sleep 1s; done",
        "sleep 1; pkill -f qa-x/;", "pkill -f qa-x/\n\necho done; ls /tmp >/dev/null 2>&1",
        "echo a; pkill -f qa-x/; pkill -f qa-y/",
        # a trailing `|| true` or `|| :`, and R after the signalling command's last word, cannot stop the shape
        "pkill -f qa-x/ || true", "pkill -f qa-x/ 2>/dev/null", "kill $(pgrep -f qa-x/) 2>/dev/null",
        "for pid in $(pgrep -f qa-x/); do kill -9 $pid 2>/dev/null; done", "pgrep -f qa-x/ | xargs -r kill 2>/dev/null",
        "for p in $(pgrep -f qa-x/); do kill $p; done || :", "while pgrep -f qa-x/; do sleep 1; done || true",
        "pkill -f qa-x/ >/dev/null 2>&1 || true; echo x", "kill -9 $(pgrep -f qa-x/) &>/dev/null || :",
        # sleeps within the 60-second bound, a mixed-case loop variable, and a sleep after the shape
        "sleep 30; sleep 30; pkill -f qa-x/", "sleep 1m; pkill -f qa-x/", "pkill -f qa-x/; sleep 99999d",
        "for Pid in $(pgrep -f qa-x/); do kill $Pid; done",
        # R right after the last word, as bash reads it: `>` ends the word (the QA round 2 table)
        "pkill -f qa-r2-adj/>/dev/null", "kill $(pgrep -f qa-r2-adj/)>/dev/null", "pkill -f qa-r2-adj/ >/dev/null",
        "while pgrep -f qa-x/>/dev/null; do sleep 1; done", "pgrep -f qa-x/ | xargs kill>/dev/null",
        "pkill -f qa-x/&>/dev/null", 'pkill -f "qa-x/">/dev/null 2>&1',
        # a (d) body's sleeps are not counted: its pgrep runs before any of them
        "while pgrep -f qa-x/ >/dev/null; do sleep 61; done", "while pgrep -f qa-x/ >/dev/null; do sleep 300; done",
        "until ! pgrep -f qa-x/ >/dev/null; do sleep 5m; done", "while pgrep -f qa-x/; do sleep 300; done",
        # one trailing `|| CMD` or `&& CMD`, CMD benign, runs after the shape (or the (b) kill) has run
        "pkill -f qa-x/ || echo none", "pkill -f qa-x/ && echo stopped",
        "pkill -f qa-x/ 2>/dev/null || echo 'not running'", "pkill -f qa-x/ || true x",
        "for p in $(pgrep -f qa-x/); do kill $p 2>/dev/null || true; done",
        'for p in $(pgrep -f qa-x/); do kill $p || echo "$p"; done', "while pgrep -f qa-x/; do sleep 1; done && date",
        # auto_resume and histchars are neither readonly nor change the value assigned
        "for histchars in $(pgrep -f qa-x/); do kill $histchars; done",
        "for auto_resume in $(pgrep -f qa-x/); do kill $auto_resume; done",
        # a word of digits with a blank, quoted, or not digits alone before `>` is a pattern (QA round 3)
        "pkill -f 15 >/dev/null", 'pkill -f "15">/dev/null', "pkill -f '8080'>/dev/null", "pkill -f qa-x/15>/dev/null",
        "pkill -f 15&>/dev/null", "pkill -f -- 15 >/dev/null", "while pgrep -f 7 >/dev/null; do sleep 1; done",
        # the trailing CMD of a shape may carry R
        "pkill -f qa-x/ || echo none >/dev/null", "pkill -f qa-x/ && date 2>/dev/null",
        "while pgrep -f qa-x/; do sleep 1; done || echo gone >/dev/null 2>&1",
    ]
    # Elements that would keep a shape from running or from reaching the shell: each must take the note.
    false_deny_witnesses = [
        "while pgrep -c -f qa-selfmatch472/; do printf '-v' PATH /nonexistent; done",
        "for p in $(pgrep -f qa-x/); do printf '-v' p not_a_pid; kill \"$p\"; done",
        "for UID in $(pgrep -f qa472readonly/); do kill \"$UID\"; done",
        "printf '-v' PATH x; pkill -f qa-x/", "while pgrep -f qa-x/; do printf '-v' PATH x; done",
        "for p in $(pgrep -f qa-x/); do printf '-v' p x; kill $p; done",
        "printf '%999999999d' 1; pkill -f qa-x/", "printf x; pkill -f qa-x/",
        "echo '-e' x; pkill -f qa-x/", "echo \"-n\"; pkill -f qa-x/", "cd /tmp; pkill -f qa-x.d",
        "for p in $(pgrep -f qa-x/); do echo \"-n\"; kill $p; done", "while pgrep -f qa-x/; do echo '-e'; done",
        "sleep 99999d; pkill -f qa-x/", "sleep 30; sleep 31; pkill -f qa-x/",
        "for p in $(pgrep -f qa-x/); do sleep 61; kill $p; done",
        "for p in $(pgrep -f qa-x/); do kill $p && sleep 61; done", "sleep 61; while pgrep -f qa-x/; do :; done",
        # a bare word of digits alone directly before `>` is a descriptor number in bash: no pattern (QA round 3)
        "pkill -f 15>/dev/null", "pkill -f 8080>/dev/null", "pkill -f -- 15>/dev/null", "pkill -f 15>/dev/null 2>&1",
        "while pgrep -f 7>/dev/null; do sleep 1; done", "until ! pgrep -f 7>/dev/null; do sleep 1; done",
    ] + ["for V in $(pgrep -f qa-x/); do kill $V; done".replace("V", name) for name in (
        "RANDOM", "SRANDOM", "IFS", "LINENO", "EPOCHSECONDS", "EPOCHREALTIME", "SECONDS", "PPID", "UID", "EUID",
        "PATH", "HOME", "SHELLOPTS", "BASHOPTS", "BASHPID", "BASH_pid", "MYPID", "_")]
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

    def newer_syntax(text):
        """The (form, line) of each syntax newer than Python 3.4 that parsing with the 3.4 grammar setting lets
        through and this scan knows: a starred item in a display, `**` in a dict display, a call with a positional
        or a second `*` after `*`, or with anything after `**`, a trailing comma after a starred parameter or
        argument (in a lambda too), a decorator that is not a dotted name or a call of one, a parenthesized with,
        and continue inside finally."""
        found = []
        skip = (tokenize.NL, tokenize.NEWLINE, tokenize.COMMENT, tokenize.INDENT, tokenize.DEDENT)
        toks = [t for t in tokenize.generate_tokens(io.StringIO(text).readline) if t.type not in skip]
        at = dict((t.start, k) for k, t in enumerate(toks))  # the file is ASCII, so ast and tokenize columns agree

        def closing(k):
            """The index of the token that closes the bracket at toks[k]."""
            depth = 0
            for m in range(k, len(toks)):
                if toks[m].string in ("(", "[", chr(123)):
                    depth += 1
                elif toks[m].string in (")", "]", chr(125)):
                    depth -= 1
                    if not depth:
                        return m
            return len(toks) - 1
        tries = tuple(getattr(ast, name) for name in ("Try", "TryStar") if hasattr(ast, name))
        defs = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, (ast.List, ast.Tuple, ast.Set)) and not isinstance(getattr(node, "ctx", None),
                                                                                   ast.Store):
                if any(isinstance(e, ast.Starred) for e in node.elts):
                    found.append(("display", node.lineno))
            elif isinstance(node, ast.Dict) and None in node.keys:
                found.append(("dict", node.lineno))
            elif isinstance(node, ast.Call):
                stars = [k for k, a in enumerate(node.args) if isinstance(a, ast.Starred)]
                double = [k for k, kw in enumerate(node.keywords) if kw.arg is None]
                if stars and stars != [len(node.args) - 1] or double and double != [len(node.keywords) - 1]:
                    found.append(("call", node.lineno))
            if isinstance(node, defs):
                for d in node.decorator_list:
                    e = d.func if isinstance(d, ast.Call) else d
                    while isinstance(e, ast.Attribute):
                        e = e.value
                    k = at.get((e.lineno, e.col_offset), 0)
                    if not isinstance(e, ast.Name) or not k or toks[k - 1].string != "@":
                        found.append(("decorator", d.lineno))
            elif isinstance(node, (ast.With, ast.AsyncWith)):
                k = at[(node.lineno, node.col_offset)]
                k += toks[k].string == "async"
                if toks[k + 1].string == "(" and toks[closing(k + 1) + 1].string == ":":
                    if len(node.items) > 1 or node.items[0].optional_vars is not None:
                        found.append(("with", node.lineno))
            elif isinstance(node, tries):
                stack = list(node.finalbody)
                while stack:
                    st = stack.pop()
                    if isinstance(st, ast.Continue):
                        found.append(("finally", st.lineno))
                    elif isinstance(st, (ast.For, ast.AsyncFor, ast.While)):
                        stack.extend(st.orelse)  # a continue in the loop's body is that loop's
                    elif not isinstance(st, defs):
                        stack.extend(c for c in ast.iter_child_nodes(st)
                                     if isinstance(c, (ast.stmt, ast.excepthandler)))
        starred = []  # one [flag, lambda] per open bracket or lambda parameter list: a `*` or `**` began an item
        for k, t in enumerate(toks):
            if t.type == tokenize.NAME and t.string == "lambda":
                starred.append([False, True])
            if t.type != tokenize.OP:
                continue
            if t.string in ("(", "[", chr(123)):
                starred.append([False, False])
            elif t.string in (")", "]", chr(125)):
                starred.pop()
            elif t.string == ":" and starred and starred[-1][1]:
                starred.pop()  # the end of a lambda's parameters
            elif t.string in ("*", "**") and starred and toks[k - 1].string in ("(", ",", "lambda"):
                starred[-1][0] = True
            elif t.string == "," and starred and starred[-1][0]:
                nxt = toks[k + 1].string
                if nxt == ")" or nxt == ":" and starred[-1][1]:
                    found.append(("comma", t.start[0]))
        return found

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

        def test_03_false_deny_witnesses_get_a_note(self):
            for command in false_deny_witnesses:
                self.assertNote(command)

        def test_04_note_scan(self):
            for command in ("pkill sleep", "pidof sshd", "ps aux | grep x | grep -v grep", "killall -9 node",
                            "/usr/bin/pkill -f qa-x/", "echo pkill x", "pgrep -f x/ >/dev/null && echo up",
                            "pkill -f qa-x/ &", "X=1 pkill -f qa-x/", "kill $(pgrep -f qa-x/ 2>/dev/null)",
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
            # in `$(...)#` the `#` continues the substitution's last word: kill gets `PID#` for the last id pgrep
            # prints, so when that is the shell's it is not signalled, and no deny is proved either; it takes the
            # note. After a subshell's `)` bash reads a comment, but the hook does not accept it (hook policy).
            for command in (d1 + " # self-match-okay", d1 + "#self-match-ok", "pkill sleep '# self-match-ok'",
                            "# self-match-ok\n" + d1 + " # later", "kill $(pgrep -f qa-x/)# self-match-ok",
                            "(pkill -f qa-x/)# self-match-ok"):
                self.assertNote(command)
            self.assertSilent("pkill -f qa-x/ 2>/dev/null # self-match-ok")

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
                            "pkill -f qa-x/;; echo", "pkill -f qa-x/ || exit", "! pkill -f qa-x/",
                            "pkill -f qa-x/ || true || true", "pkill -f qa-x/ || echo a && echo b",
                            "echo a || true; pkill -f qa-x/", "pkill -f qa-x/ || pkill -f qa-y/",
                            "for p in $(pgrep -f qa-x/); do kill $p || break; done",
                            "for p in $(pgrep -f qa-x/); do echo a || true; kill $p; done",
                            "for p in $(pgrep -f qa-x/); do kill $p || kill $p; done",
                            "pkill -f qa-x/ && sleep infinity", "pkill -f qa-x/ || echo $p",
                            "pkill -f qa-x/2>&1", "pkill -f qa-x/>>/dev/null", "pkill -f qa-x/>log",
                            "pkill -f qa-x/<x", "pkill -f qa-x/(", "pkill -f 'python worker.py'",
                            "pkill 2>/dev/null -f qa-x/", "pgrep -f qa-x/ >/dev/null | xargs kill",
                            "for p in $(pgrep -f qa-x/ >/dev/null); do kill $p; done",
                            "for p in $(pgrep -f qa-x/); do kill $p || date; done",
                            "for p in $(pgrep -f qa-x/); do kill $p || pwd; done",
                            "for p in $(pgrep -f qa-x/); do kill $p || echo x >/dev/null; done"):
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
            for p in (None, 7, "x", [], dict(tool_name="Bash"), dict(tool_name="Bash", tool_input="x"),
                      dict(tool_name="Bash", tool_input=dict(command=7))):
                self.assertEqual(_decide(p, dict()), dict(systemMessage=_NOTE_PAYLOAD), p)  # cannot evaluate
            self.assertIsNone(_decide(None, dict(AIQT_HOOKS_WORKER="1")))
            for p in (dict(), dict(tool_name="Bash", tool_input=dict(command="")),
                      dict(tool_name="Read", tool_input=dict(command="pkill -f qa-x/")),
                      {"tool_name": None, "tool_input": {"command": "pkill -f qa-x/"}}):
                self.assertIsNone(_decide(p, {}), p)
            self.assertIsNone(decide("pkill -f qa-x/", hook_event_name="PostToolUse"))
            for env in ({"AIQT_HOOKS_WORKER": "1"}, {"ORCH_WORKER": "1"}, {"ORCH_VERIFY_OWNER": ""},
                        {"AIQT_HOOKS_WORKER": "0", "ORCH_WORKER": "1"}):
                self.assertIsNone(decide("pkill -f qa-x/", env=env), env)
            for env in ({"AIQT_HOOKS_WORKER": "0"}, {"AIQT_HOOKS_WORKER": "yes"}, {"ORCH_WORKER": "0"}):
                self.assertIsNotNone(decide("pkill -f qa-x/", env=env), env)

        def test_09_process_outputs(self):
            # the script on the actual command line, through its own entry point (hook): a deny, the note and
            # silence for a well-formed payload (QA round 12)
            rc, out, err = self.hook(payload_bytes("pkill -f qa-x/"))
            self.assertEqual((rc, err), (0, b""))
            self.assertTrue(out.endswith(b"\n") and out.count(b"\n") == 1, out)
            h = json.loads(out)["hookSpecificOutput"]
            self.assertEqual(h["permissionDecision"], "deny")
            rc, out, err = self.hook(payload_bytes("pkill sleep"))
            self.assertEqual((rc, err, out.count(b"\n")), (0, b"", 1))
            self.assertEqual(json.loads(out), {"systemMessage": _NOTE})
            self.assertEqual(self.hook(payload_bytes("echo hi")), (0, b"", b""))
            self.assertEqual(self.hook(payload_bytes("pkill -f qa-x/", agent_id="a1"))[1].count(b"deny"), 1)
            out = self.hook(payload_bytes("pkill -f " + chr(233) + "x"))[1]
            out.decode("ascii")  # json.dumps writes ASCII escapes
            # and through main in a child (run_hook), whatever arguments main passes its reader
            out = self.run_hook(payload_bytes("pkill -f qa-x/"))[1]
            self.assertEqual(json.loads(out)["hookSpecificOutput"]["permissionDecision"], "deny")

        def test_09b_stream_failure_deny_exits_2(self):
            """A deny whose stdout cannot be written or flushed exits 2 with the failure noted on stderr
            (a lost deny blocks, never allows; before this hardening these runs exited 0 with the deny
            lost, which allowed the call), while a lost NOTE keeps exit 0. One child per state: stdout on
            /dev/full (the write or flush raises ENOSPC) and on the write end of a pipe whose read end is
            already closed (EPIPE). Skipped where /dev/full is absent."""
            if not os.path.exists("/dev/full"):
                self.skipTest("/dev/full is absent on this host")

            def run_streams(data, stdout_to):
                handles = []
                if stdout_to == "full":
                    handle = open("/dev/full", "wb")
                    handles.append(handle)
                    stdout_target = handle
                else:
                    read_end, write_end = os.pipe()
                    os.close(read_end)
                    handles.append(write_end)
                    stdout_target = write_end
                try:
                    p = subprocess.run([sys.executable, "-I", "-S", "-B", here], input=data,
                                       stdout=stdout_target, stderr=subprocess.PIPE,
                                       env=dict(LC_ALL="C"), timeout=60)
                finally:
                    for handle in handles:
                        try:
                            if isinstance(handle, int):
                                os.close(handle)
                            else:
                                handle.close()
                        except OSError:
                            pass
                return p.returncode, p.stderr

            for stdout_to in ("full", "broken"):
                rc, err = run_streams(payload_bytes("pkill -f qa-x/"), stdout_to)
                self.assertEqual(rc, 2, (stdout_to, err))
                self.assertIn(b"could not be written to stdout", err)
                rc, err = run_streams(payload_bytes("pkill sleep"), stdout_to)  # a NOTE line, lost, exit 0
                self.assertEqual((rc, err), (0, b""), stdout_to)

        def test_09_process_fail_open(self):
            # a payload the hook cannot read is allowed WITH the cannot-evaluate note, never silently; each one on
            # the actual command line, through the script's own entry point (QA round 13): those that fit in the
            # pipe's buffer written before the child starts (hook), the one over the 16 MiB read bound written
            # while it reads (child with [here]), where the note is the verdict whether the hook stops at the bound
            # or at its production deadline. That one also runs through main (run_hook), whose reader's 20-second
            # deadline leaves the read bound the only way to the note on any host that writes it in time.
            huge = payload_bytes("pkill -f qa-x/ #" + "x" * (17 * 1024 * 1024))  # over the 16 MiB read bound
            bad = (b"", b"not json", b"[1, 2]", b"7", b"\xff\xfe\x00garbage", b'{"tool_name": "Bash"',
                   payload_bytes("pkill -f qa-x/")[:-1], payload_bytes("echo hi").replace(b"hi", b"h\xffi"), huge)
            bad += tuple(not_strict_utf8())  # not strict UTF-8 without a BOM (QA round 5)
            bad += tuple(non_json_constants())  # NaN, Infinity and -Infinity are not JSON (QA round 6)
            for data in bad:
                for rc, out, err in ((self.hook(data),) if data is not huge else
                                     (self.child([here], [(0, data)]), self.run_hook(data))):
                    self.assertEqual((rc, err, out.count(b"\n")), (0, b"", 1), data[:30])
                    self.assertEqual(json.loads(out), dict(systemMessage=_NOTE_PAYLOAD), data[:30])
            for data in (payload_bytes("pkill -f qa-x/", tool_name="Read"),
                         payload_bytes("pkill -f qa-x/", hook_event_name="PostToolUse")):
                self.assertEqual(self.hook(data), (0, b"", b""), data[:30])
            if os.path.exists("/bin/sh"):  # a closed stdin
                p = subprocess.run(["/bin/sh", "-c", 'exec "$0" -I -S -B "$1" <&-', sys.executable, here],
                                   capture_output=True, env=dict(LC_ALL="C"), timeout=30)
                self.assertEqual((p.returncode, p.stderr), (0, b""))
                self.assertEqual(json.loads(p.stdout), dict(systemMessage=_NOTE_PAYLOAD))
            for extra in ({"AIQT_HOOKS_WORKER": "1"}, {"ORCH_WORKER": "1"}, {"ORCH_VERIFY_OWNER": ""}):
                env = dict({"LC_ALL": "C"}, **extra)
                rc, out, err = self.hook(payload_bytes("pkill -f qa-x/"), env)  # the actual command line
                self.assertEqual((rc, out, err), (0, b"", _WORKER_LINE.encode() + b"\n"), extra)

        def test_09_read_error_note(self):
            def fail(*args, **kw):
                raise OSError("injected read failure")
            old = sys.stdout
            sys.stdout = io.StringIO()
            try:
                with patched(_read_complete=fail, _is_worker=lambda env: False):
                    self.assertEqual(main(["hook"]), 0)
                text = sys.stdout.getvalue()
            finally:
                sys.stdout = old
            self.assertEqual(json.loads(text), dict(systemMessage=_NOTE_PAYLOAD))

        def test_09_fragmented_payload(self):
            """A whole JSON payload, an idle interval, then more bytes: the input as a whole is not one JSON object,
            so it gets the cannot-evaluate note, never silence; the same interval before the end of input (or
            before blanks) leaves the payload read as sent. Through main on injected schedules (scheduled with
            via_main, at the production deadline), the idle wait taking 0.2 seconds, so no verdict depends on how
            fast the host runs the test (QA round 11). The cases that get the note are also run as the hook's own
            process (child), where the note is the verdict whenever the bytes arrive."""
            whole, note = payload_bytes("echo hi"), json.dumps(dict(systemMessage=_NOTE_PAYLOAD)) + "\n"
            for chunks, want in (((whole, b"garbage"), note), ((payload_bytes("pkill -f qa-x/"), b"x"), note),
                                 ((whole, b""), ""), ((whole, b"\n "), ""), ((whole[:20], whole[20:]), ""),
                                 ((whole, b" \t\r\n"), ""), ((whole, b"\x0b"), note), ((whole, b"\x0c"), note),
                                 ((whole, b"\xc2\xa0"), note)):
                steps = [(0.0, chunks[0]), (0.2, None)] + ([(0.0, chunks[1])] if chunks[1] else []) + [(0.0, b"")]
                self.assertEqual(self.scheduled(steps, via_main=True)[0], want, chunks)
                if want:
                    self.assertEqual(self.child([here], [(0, chunks[0]), (0.2, chunks[1])]),
                                     (0, want.encode(), b""), chunks)

        def test_10_argv(self):
            # a bad argv gets the argv note on stdout, nothing on stderr, and _read_complete is never called (QA
            # round 14); under a worker marker it gets the worker line on stderr instead, nothing on stdout (QA
            # round 15)
            def read(*args, **kw):
                reads.append(args)
                raise OSError("stdin read")
            reads = []
            bad = json.dumps(dict(systemMessage=_NOTE_ARGV.format("an argument list it cannot read"))) + "\n"
            argvs = (None, 7, "--self-test", ["x", 3], dict(a=1), [], [3, "--self-test"])
            old = sys.stdout, sys.stderr
            saved = os.environ.get("AIQT_HOOKS_WORKER")
            sys.stdout, sys.stderr = io.StringIO(), io.StringIO()
            try:
                with patched(_read_complete=read, _is_worker=lambda env: False):
                    for argv in argvs:
                        sys.stdout.seek(0)
                        sys.stdout.truncate()
                        self.assertEqual((main(argv), sys.stdout.getvalue()), (0, bad), argv)
                text = sys.stderr.getvalue()
                os.environ["AIQT_HOOKS_WORKER"] = "1"  # the real _is_worker, on a worker marker
                with patched(_read_complete=read):
                    for argv in argvs:
                        for stream in (sys.stdout, sys.stderr):
                            stream.seek(0)
                            stream.truncate()
                        self.assertEqual((main(argv), sys.stdout.getvalue(), sys.stderr.getvalue()),
                                         (0, "", _WORKER_LINE + "\n"), argv)
            finally:
                sys.stdout, sys.stderr = old
                if saved is None:
                    os.environ.pop("AIQT_HOOKS_WORKER", None)
                else:
                    os.environ["AIQT_HOOKS_WORKER"] = saved
            self.assertEqual((reads, text), ([], ""))
            # on the actual command line: an argument list it does not accept (only a lone --self-test is
            # accepted, so --self-test twice is refused too) gets the argv note and returns 0 before stdin is
            # read; under each worker marker the hook honours, an argv it does not accept gets the worker line on
            # stderr and nothing on stdout. The payload and its end of input are in the pipe before the child
            # starts, and the pipe's read end, kept open here, still holds the whole payload after the child
            # exits (QA rounds 12 to 15)
            data = payload_bytes("pkill -f qa-x/")
            note = _NOTE_ARGV.format("an argument list it does not accept")
            note = (json.dumps(dict(systemMessage=note)) + "\n").encode()
            worker = _WORKER_LINE.encode() + b"\n"
            cases = [(argv, dict(), (0, note, b"", data)) for argv in (["--self-test", "x"], ["-x"], ["--selftest"],
                                                                        [""], ["-x", "-y"], ["--self-test"] * 2)]
            cases += [(["-x"], extra, (0, b"", worker, data))
                      for extra in (dict(AIQT_HOOKS_WORKER="1"), dict(ORCH_WORKER="1"), dict(ORCH_VERIFY_OWNER="x"))]
            for argv, extra, want in cases:
                r, w = os.pipe()
                try:
                    self.assertEqual(os.write(w, data), len(data))
                    os.close(w)
                    w = None
                    p = subprocess.run([sys.executable, "-I", "-S", "-B", here] + argv, stdin=r,
                                       capture_output=True, env=dict(extra, LC_ALL="C"), timeout=30)
                    os.set_blocking(r, False)
                    self.assertEqual((p.returncode, p.stdout, p.stderr, os.read(r, len(data) + 1)), want,
                                     (argv, extra))
                finally:
                    for fd in (r, w):
                        if fd is not None:
                            os.close(fd)

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
            self.assertEqual(newer_syntax(text), [])

        def test_12_newer_syntax_scan(self):
            # each form the 3.4 grammar setting accepts though Python 3.4 does not
            for snippet in ("[*a]\n", "(*a, b)\n", "{*a}\n", "{**a}\n", "f(*a, b)\n", "f(*a, *b)\n",
                            "f(**a, **b)\n", "f(**a, b=1)\n", "def f(*, a,):\n    pass\n", "f(*a,)\n",
                            "x[*a]\n", "def f():\n    return *a, b\n", "f = lambda *a,: 0\n",
                            "f = lambda *, a,: 0\n", "f = lambda **a,: 0\n", "@a[0].b\ndef f():\n    pass\n",
                            "@(a)\ndef f():\n    pass\n", "@(a)(b)\nclass C:\n    pass\n",
                            "with (open(a) as b, open(c) as d):\n    pass\n", "with (a as b):\n    pass\n",
                            "for x in y:\n    try:\n        pass\n    finally:\n        continue\n",
                            "for x in y:\n    try:\n        pass\n    finally:\n        if x:\n            continue\n",
                            "for x in y:\n    try:\n        pass\n    finally:\n        for z in x:\n"
                            "            pass\n        else:\n            continue\n"):
                ast.parse(snippet, feature_version=(3, 4))
                self.assertNotEqual(newer_syntax(snippet), [], snippet)
            for snippet in ("a, *b = c\n", "f(a, *b)\n", "f(a, *b, c=1, **d)\n", "def f(a, *b, **c):\n    pass\n",
                            "x = (1, 2 * 3,)\n", "f(a,)\n", "f = lambda a,: 0\n", "f = lambda *a: 0\n",
                            "d = dict(k=lambda *a: 0)\n", "f = lambda a=x[1:2], *b: a\n",
                            "@a.b(c)\ndef f():\n    pass\n", "@a\nclass C:\n    pass\n",
                            "with (a) as b, c:\n    pass\n", "with (a):\n    pass\n", "with a as b:\n    pass\n",
                            "try:\n    pass\nfinally:\n    for x in y:\n        continue\n",
                            "for x in y:\n    try:\n        continue\n    finally:\n        pass\n"):
                self.assertEqual(newer_syntax(snippet), [], snippet)

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
                         lambda k: "while pgrep -f qa-x/; do " + "sleep 0; " * k + "done",
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
                rc, out, err = self.run_hook(payload_bytes(c))
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
                    found, i, _ = _element(toks, i)
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
            self.kill_mutant(dict(_simple=lambda run, names, redirs=False, var=None:
                                  names is _FOR_BODY or real(run, names, redirs, var)),
                             ['for p in $(pgrep -f qa-x/); do continue; kill "$p"; done'], "note", "deny")

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
            self.kill_mutant(dict(_WAIT_BODY=_WAIT_BODY | frozenset(("break",))),
                             ["while pgrep -f x/\ndo\n  break\ndone"], "note", "deny")

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

        def test_m25_word_end(self):
            # pkill gets one word, -eqa-x/, an invalid option, and sends nothing
            self.kill_mutant(dict(_end=lambda text, j: j), ['pkill -f -e"qa-x/"'], "silent", "deny")

        def test_m26_quoted_expansion(self):
            # the substitution blocks, so pkill never runs
            self.kill_mutant(dict(_DQ_SPECIAL=""), ['echo "$(sleep infinity)"; pkill -f qa-x/'], "note", "deny")

        def test_m27_separator_before_do(self):
            def unchecked(toks, i):
                j = i + 1 if i < len(toks) and toks[i] == _SEPS[0] else i
                while j < len(toks) and toks[j] == _SEPS[1]:
                    j += 1
                return j
            # bash -n: a syntax error near `done`
            self.kill_mutant(dict(_seps=unchecked), ["for p in $(pgrep -f qa-x/) do kill $p; done"], "note", "deny")

        def test_m28_pgrep_full_required(self):
            # in name mode pgrep matches the process name, and the shell's name is bash
            real = _pgrep
            self.kill_mutant(dict(_pgrep=lambda run, extra: real(run[:1] + [("w", "-f", "bare")] + run[1:], extra)),
                             ["kill $(pgrep -- myworker)", "pgrep -- myworker | xargs kill",
                              "while pgrep -c qa-x/; do sleep 1; done", "for p in $(pgrep qa-x/); do kill $p; done",
                              "kill $(pgrep qa-x/)", "pgrep qa-x/ | xargs kill",
                              "while pgrep qa-x/ >/dev/null; do sleep 1; done"], "note", "deny")

        def drop_dashdash(self, name):
            real = module[name]

            def strip(run):
                return [t for t in run if t != ("w", "--", "bare")]
            if name == "_pkill":
                return dict(_pkill=lambda run: real(strip(run)))
            return dict(_pgrep=lambda run, extra: real(strip(run), extra))

        def test_m29_pgrep_dashdash_position(self):
            # after `--`, -f is a pattern, and pgrep rejects a second pattern
            self.kill_mutant(self.drop_dashdash("_pgrep"), ["kill $(pgrep -- -f qa-x/)"], "note", "deny")

        def test_m30_pkill_dashdash_position(self):
            self.kill_mutant(self.drop_dashdash("_pkill"), ["pkill -- -f qa-x/"], "note", "deny")

        def test_m31_separator_after_done(self):
            def unchecked(toks):
                first, slept, i, n = None, 0, 0, len(toks)
                while True:
                    while i < n and toks[i] == _SEPS[1]:
                        i += 1
                    if i >= n:
                        return first
                    found, i, runs = _element(toks, i)
                    if first is None:
                        slept += sum(_slept(run) for run in runs)
                        if slept > _SLEEP_TOTAL:
                            raise _Refused
                        first = found
                    i += 1
            self.kill_mutant(dict(_shape=unchecked), ["while pgrep -f qa-x/; do sleep 1; done & echo"], "note", "deny")

        def test_m32_option_words(self):
            # a quoted word starting with `-` is an option to echo
            self.kill_mutant(dict(_plain=lambda tok: tok[0] == "w" and (tok[2] != "bare" or _literal(tok))),
                             ["echo '-e' x; pkill -f qa-x/", 'for p in $(pgrep -f qa-x/); do echo "-n"; kill $p; done',
                              "while pgrep -f qa-x/; do echo '-e'; done"], "note", "deny")

        def test_m33_no_printf(self):
            # a field width writes about a gigabyte before pkill runs
            add = frozenset(("printf",))
            self.kill_mutant(dict(_BENIGN=_BENIGN | add, _FOR_BODY=_FOR_BODY | add),
                             ["printf '%999999999d' 1; pkill -f qa-x/",
                              "for p in $(pgrep -f qa-x/); do printf '%999999999d' 1; kill $p; done"], "note", "deny")

        def test_m34_no_cd(self):
            self.kill_mutant(dict(_BENIGN=_BENIGN | frozenset(("cd",))), ["cd /tmp; pkill -f qa-x.d"], "note", "deny")

        def loop_vars(self, *names):
            return ["for V in $(pgrep -f qa-x/); do kill $V; done".replace("V", name) for name in names]

        def test_m35_loop_variable_lowercase(self):
            # the lowercase rule is what leaves out bash's all-caps special names
            self.kill_mutant(dict(_loop_var=lambda name: bool(_NAME_RE.fullmatch(name)) and not name.startswith("BASH")
                                  and name not in _RESERVED),
                             self.loop_vars("MYPID", "_", "PIDS", "RANDOM", "UID", "IFS", "SECONDS"), "note", "deny")

        def test_m36_loop_variable_special(self):
            # auto_resume and histchars are neither readonly nor change the value assigned: excluding them, as the
            # previous revision did, turns two true denies into notes
            real = _loop_var
            self.kill_mutant(dict(_loop_var=lambda name: real(name) and name not in ("auto_resume", "histchars")),
                             self.loop_vars("auto_resume", "histchars"), "deny", "note")

        def test_m37_loop_variable_bash_prefix(self):
            self.kill_mutant(dict(_loop_var=lambda name: bool(_NAME_RE.fullmatch(name))
                                  and any("a" <= ch <= "z" for ch in name)
                                  and name not in _RESERVED),
                             self.loop_vars("BASH_pid"), "note", "deny")

        def test_m38_sleep_total(self):
            self.kill_mutant(dict(_SLEEP_TOTAL=float("inf")),
                             ["sleep 99999d; pkill -f qa-x/", "sleep 30; sleep 31; pkill -f qa-x/",
                              "for p in $(pgrep -f qa-x/); do sleep 61; kill $p; done"], "note", "deny")

        def test_m39_trailer(self):
            self.kill_mutant(dict(_trailer=lambda toks, i, names, var=None: i),
                             ["pkill -f qa-x/ || true", "for p in $(pgrep -f qa-x/); do kill $p; done || :",
                              "while pgrep -f qa-x/; do sleep 1; done || true", "pkill -f qa-x/ || echo none",
                              "pkill -f qa-x/ && echo stopped", "pkill -f qa-x/ 2>/dev/null || echo 'not running'",
                              "for p in $(pgrep -f qa-x/); do kill $p 2>/dev/null || true; done"], "deny", "note")

        def test_m40_signaller_redirections(self):
            self.kill_mutant(dict(_unredirected=lambda run: run),
                             ["pkill -f qa-x/ 2>/dev/null", "kill $(pgrep -f qa-x/) 2>/dev/null",
                              "for pid in $(pgrep -f qa-x/); do kill -9 $pid 2>/dev/null; done",
                              "pgrep -f qa-x/ | xargs -r kill 2>/dev/null"], "deny", "note")

        def test_m41_redirections_only_on_signaller(self):
            # >/dev/null on the pgrep that feeds kill leaves kill no process id
            real = _pgrep
            self.kill_mutant(dict(_pgrep=lambda run, extra: real([t for t in run if t[0] != "r"], extra)),
                             ["kill $(pgrep -f qa-x/ >/dev/null)", "pgrep -f qa-x/ >/dev/null | xargs kill",
                              "for p in $(pgrep -f qa-x/ >/dev/null); do kill $p; done"], "note", "deny")

        def test_m42_opt_out_after_paren(self):
            old = re.compile(r"(?<![^ \t\n;&|()])#[ \t]*self-match-ok(?:[ \t:][^\n]*)?\Z")
            self.kill_mutant(dict(_OPT_OUT_RE=old), ["kill $(pgrep -f qa-x/)# self-match-ok"], "note", "silent")

        def test_m43_adjacent_redirection(self):
            # as in bash, `>` ends a word: R needs no blank before it
            self.kill_mutant(dict(_AFTER_WORD=_AFTER_WORD - frozenset(">")),
                             ["pkill -f qa-r2-adj/>/dev/null", "kill $(pgrep -f qa-r2-adj/)>/dev/null",
                              "while pgrep -f qa-x/>/dev/null; do sleep 1; done"], "deny", "note")

        def test_m44_wait_body_sleeps_not_counted(self):
            # a (d) loop's pgrep runs before any body sleep, so the body's sleeps cannot keep the shape from running
            real = _element

            def mutant(toks, i):
                found, end, runs = real(toks, i)
                if _w(toks[i], "while") or _w(toks[i], "until"):
                    runs = _wait(toks, i)[2]
                return found, end, runs
            self.kill_mutant(dict(_element=mutant),
                             ["while pgrep -f qa-x/ >/dev/null; do sleep 61; done",
                              "while pgrep -f qa-x/ >/dev/null; do sleep 300; done",
                              "until ! pgrep -f qa-x/ >/dev/null; do sleep 5m; done"], "deny", "note")

        def test_m45_end_of_input_required(self):
            """In process, on injected schedules (scheduled), with no thread and nothing timed: the vendored reader
            alone (the hook does not call it) returns a whole JSON value that an idle wait follows, and the hook's
            reader reads on to the end of input and refuses the bytes after it unless they are JSON blanks. The
            payload is decoded as strict UTF-8 before it is parsed (QA rounds 4 and 5), and NaN, Infinity and
            -Infinity are refused (QA round 6)."""
            whole = payload_bytes("echo hi")

            def read(reader, tail):
                """reader on the whole payload, an idle wait of 0.1 seconds, tail and the end of input."""
                steps = [(0.0, whole), (0.1, None)] + ([(0.0, tail)] if tail else []) + [(0.0, b"")]
                return self.scheduled(steps, deadline=30.0, reader=reader)[0]

            def refused(out):
                return isinstance(out, str) and out.startswith("refused: ")
            self.assertTrue(refused(read(_read_complete, b"garbage")))
            self.assertEqual(read(_read_complete, b"\n"), json.loads(whole))
            self.assertEqual(read(_read_complete, b""), json.loads(whole))
            self.assertEqual(read(_read_payload, b"garbage"), json.loads(whole))  # the mutant: no end required
            for blank in (b" ", b"\t", b"\r", b"\n", b"\t\r\n "):  # every JSON blank may follow the payload
                self.assertEqual(read(_read_complete, blank), json.loads(whole), blank)
            for other in (b"\x0b", b"\x0c", b"\x00", b"\xc2\xa0"):  # no other byte may
                self.assertTrue(refused(read(_read_complete, other)), other)

            def whole_input(data):
                """_read_complete on data and then the end of input."""
                return self.scheduled([(0.0, data), (0.0, b"")], deadline=30.0)[0]
            # invalid UTF-8 inside the JSON is refused, not decoded with replacement characters (QA round 4: the
            # parse is no longer inside the vendored block, whose hash pinned it)
            for bad in (b'{"a": "\xff"}', whole.replace(b"hi", b"h\xffi")):
                out = whole_input(bad)
                self.assertTrue(refused(out), (bad, out))
            # the payload is decoded as strict UTF-8 before it is parsed: json.loads on the bytes takes each of these
            # (encoded surrogates, a BOM, and the other encodings it detects), the reader refuses them (QA round 5)
            for bad in not_strict_utf8():
                json.loads(bad)  # parsed when the bytes go to json.loads undecoded
                out = whole_input(bad)
                self.assertTrue(refused(out), (bad[:12], out))
            # the matrix is the thirteen its docstring names: the UTF-16 and UTF-32 ones in both byte orders, each
            # without and with its BOM, built explicitly rather than in the interpreter's own byte order (QA round 6)
            self.assertEqual(len(not_strict_utf8()), 13)
            self.assertEqual([bad[:4].hex() for bad in not_strict_utf8()[5:]],
                             ["7b002200", "fffe7b00", "007b0022", "feff007b",
                              "7b000000", "fffe0000", "0000007b", "0000feff"])
            # NaN, Infinity and -Infinity are not JSON: json.loads takes them by default, the reader refuses them
            # (QA round 6)
            for bad in non_json_constants():
                json.loads(bad)  # parsed without a parse_constant that refuses them
                name = bad.rsplit(b" ", 1)[1][:-1].decode()
                out = whole_input(bad)
                self.assertEqual(out, "refused: hook payload has " + name + ", which is not JSON", bad)

        def child(self, argv, parts, end=True, bound=30, env=None, before=False):
            """python3 -I -S -B argv run as a child process (env its whole environment, LC_ALL=C alone when None),
            its stdin a pipe that this test writes with no thread:
            parts are (pause, bytes) each, each written after sleeping pause seconds through a non-blocking write
            end, so a child that stops reading cannot block the test. The write end is then closed when end is
            true, and kept open until the child exits when it is false (an input that never ends). A child that
            stops reading (EPIPE) gets no more parts. before: the parts (each with no pause) are all written and
            the write end closed before the child is started, so the child finds its whole input and its end
            already there whenever it starts reading; a part that does not fit in the pipe's buffer fails the
            test. bound is parent-observed: it runs on this test's clock from just after the child is started, so
            it includes the child's start-up and any stall of this test process (its pauses and writes too), and
            it is an outer bound, not a measure of how long the child took. A child that has not finished within
            it, or whose input could not all be written by then, is killed and fails the test with its output,
            reported as not finished within the parent-observed bound (not as an overrun the child was shown to
            make); it never hangs the test. On every path the child is killed if it still runs and is reaped,
            and each descriptor opened here is closed. Returns (status, stdout, stderr)."""
            r, w = os.pipe()
            p, late = None, False
            try:
                os.set_blocking(w, False)
                if before:
                    for pause, data in parts:
                        self.assertEqual((pause, os.write(w, data)), (0, len(data)), "a part written before the start")
                    os.close(w)
                    w, parts = None, ()
                p = subprocess.Popen([sys.executable, "-I", "-S", "-B"] + argv, stdin=r, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, env=dict(LC_ALL="C") if env is None else env)
                stop = wall_clock() + bound
                os.close(r)
                r = None
                try:
                    for pause, data in parts:
                        wall_sleep(pause)
                        view = memoryview(data)
                        while view and not late:
                            late = not wall_select([], [w], [], max(0.0, stop - wall_clock()))[1]
                            if not late:
                                view = view[os.write(w, view):]
                except BrokenPipeError:
                    pass  # the child stopped reading
                if end and w is not None:
                    os.close(w)
                    w = None
                if not late:
                    try:
                        out, err = p.communicate(timeout=max(0.0, stop - wall_clock()))
                    except subprocess.TimeoutExpired:
                        late = True
                if late:
                    p.kill()
                    out, err = p.communicate()
                    self.fail("the child did not finish within the parent-observed %d-second bound (it includes the "
                              "child's start-up and any stall of this test process): status %r, stdout %r, stderr "
                              "%r" % (bound, p.returncode, out[-2000:], err[-2000:]))
                return p.returncode, out, err
            finally:
                for fd in (r, w):
                    if fd is not None:
                        os.close(fd)
                if p is not None:
                    if p.poll() is None:
                        p.kill()
                    p.communicate()  # reaps it and closes its stdout and stderr

        def run_hook(self, data, env=None, argv=()):
            """The hook's main run in a child process (child, MAIN) on data and then the end of input, with argv
            after this file's path and env the whole environment: (status, stdout, stderr). Its reader is given
            20 seconds whatever arguments main passes, 10 seconds below the child's parent-observed 30-second
            bound. That is a NOMINAL margin, reduced by the child's start-up, the host's scheduling and the time
            to report the result, so a reader that waits its whole deadline exits before the kill only while those
            delays total under 10 seconds, and these verdicts depend on how fast the host runs the child only when
            the host stalls it (or this test) for about that long (QA rounds 11 to 14); scheduled with via_main
            pins that main reads stdin at the production deadline."""
            return self.child(["-c", MAIN, here] + list(argv), [(0, data)], env=env)

        def hook(self, data, env=None, argv=()):
            """This file on the actual command line, python3 -I -S -B <this file> argv, so through its own entry
            point (raise SystemExit(main(sys.argv))), with env the whole environment (child): data and its end of
            input are in the pipe before the child starts (child with before), so the reader, at the production
            deadline, finds them whenever it starts, and no pause or write of this test can make it late; a
            correct hook fails here only if the host stalls the child itself for about that deadline while it
            reads them, or stalls the child or this test for about the child's parent-observed 30-second bound
            (QA round 12). data must fit in the pipe's buffer. Returns (status, stdout, stderr)."""
            return self.child([here] + list(argv), [(0, data)], env=env, before=True)

        def child_read(self, parts, deadline=20.0, end=True, reader="_read_complete", max_input=None,
                       bound=30):
            """reader (this file's _read_complete, or the vendored _read_payload) called in a child process (child,
            DRIVER) on its stdin, given deadline seconds (None: the reader's default, the production
            _READ_DEADLINE of 2.0) and, when max_input is given, a _MAX_INPUT of max_input: the payload, or
            "refused: <message>" for the ValueError it raised. The child must exit 0 with one line on stdout and
            nothing on stderr within bound seconds (child). The default deadline of 20 seconds is 10 seconds below
            the default parent-observed bound of 30, a NOMINAL margin reduced by the child's start-up, the host's
            scheduling and the time to report the result, so a reader that waits its whole deadline exits, and is
            reported, before the kill only while those delays total under 10 seconds (QA rounds 13 and 14); every
            caller that passes a deadline of None (the production 2.0 seconds) passes a bound of 12, the same
            nominal 10-second margin."""
            rc, out, err = self.child(["-c", DRIVER, here, reader, "-" if deadline is None else repr(deadline),
                                       "-" if max_input is None else str(max_input)], parts, end, bound)
            self.assertEqual((rc, err, out.count(b"\n")), (0, b"", 1), out)
            kind, value = json.loads(out.decode("ascii"))
            return value if kind == "payload" else "refused: " + value

        def scheduled(self, steps, read_takes=0.0, deadline=1.0, reader=None, mutant=None, held=None,
                      via_main=False):
            """reader(r, deadline) (_read_complete when None) under an injected clock, select and os.read, with no
            thread and nothing timed: the clock stands still but for the waits, the reads, a held reading and a
            sleep. Wait number i is given steps[i], (seconds, part): the wait records its timeout, moves the clock
            on that many seconds, then writes part (which fits the pipe's buffer) and reports the pipe ready
            (closes the pipe for b"" and reports it ready; reports it not ready for None); each read moves the
            clock on read_takes seconds (a list: each read in turn its own entry). held, when given, is (k,
            seconds): clock reading number k is taken and the clock then moves on that many seconds before the
            reader uses it, as when the OS runs the reader late (QA round 4). mutant, when given, wraps the
            injected select (the reader as a mutant changes it). via_main: the reader is main's own call of
            _read_complete (the worker check off, stdout captured), which must be one call on stdin (descriptor
            0) at the production _READ_DEADLINE, its arguments recorded and the defaults filled in (QA round 11);
            the result is then main's output. The injected time has monotonic and sleep, the injected select has
            select, the injected os has read, and any other name the reader reaches through time, select or os
            fails the test, so a blocking call there cannot go unseen; sleep moves the clock on and is recorded.
            The deadline assertions cover the total injected time (QA round 11): once the reader has read a time
            at or past the deadline, it must return without another wait, read or sleep; and when it returns, the
            clock must have moved by the waits, the reads and a held reading alone. So a reader that sleeps,
            before a refusal or anywhere else, fails the test by assertion. Not seen here: a delay through a name
            the harness does not patch (__import__("time").sleep, threading, another module's clock, a busy
            loop). More waits than steps fail the test, and so do more reads than steps (a read follows a wait)
            and over 100 clock readings, so a reader that loops without waiting fails the test rather than
            hanging it (QA round 9). Both pipe ends are closed on every path from the moment os.pipe returns them
            (QA round 11). Returns (the payload, "refused: <message>" or main's output; the timeouts in order; the
            number of reads)."""
            case, real_os, real_read = self, os, _read_complete
            r, w = real_os.pipe()
            ends = [r, w]
            try:
                real_os.set_blocking(r, False)  # a read with nothing to read raises BlockingIOError, it never blocks
                timeouts, reads, slept, failed, moved = [], [], [], [], [100.0]

                def prompt(what):
                    case.assertFalse(clock.late, "the reader %s after it read a time at or past the deadline" % what)

                def move(seconds):
                    """The clock moved on by a wait, a read or a held reading (moved: those alone)."""
                    clock.now += seconds
                    moved[0] += seconds

                class Clock(object):
                    now, readings, end, late = 100.0, 0, None, False

                    def monotonic(self):
                        self.readings += 1
                        case.assertLessEqual(self.readings, 100, "the clock read over 100 times")
                        value = self.now
                        if held and self.readings == held[0]:
                            move(held[1])
                        self.late = self.late or value >= self.end
                        return value

                    def sleep(self, seconds):
                        prompt("slept")
                        slept.append(seconds)
                        self.now += seconds

                    def __getattr__(self, name):
                        case.fail("the reader used time.%s, which the injected time does not give" % name)
                clock = Clock()

                class Select(object):
                    def select(self, rlist, wlist, xlist, timeout):
                        prompt("waited")
                        case.assertLess(len(timeouts), len(steps), "more waits than the schedule")
                        seconds, part = steps[len(timeouts)]
                        timeouts.append(timeout)
                        move(seconds)
                        if part is None:
                            return [], [], []
                        if part:
                            real_os.write(w, part)
                        else:
                            case.assertIn(w, ends, "the schedule ends the input twice")
                            ends.remove(w)
                            real_os.close(w)
                        return list(rlist), [], []

                    def __getattr__(self, name):
                        case.fail("the reader used select.%s, which the injected select does not give" % name)

                class Os(object):
                    def read(self, fd, size):
                        prompt("read")
                        case.assertLess(len(reads), len(steps), "more reads than the schedule")
                        move(read_takes[len(reads)] if isinstance(read_takes, list) else read_takes)
                        reads.append(fd)
                        return real_os.read(fd, size)

                    def __getattr__(self, name):
                        case.fail("the reader used os.%s, which the injected os does not give" % name)

                def run(seconds):
                    clock.end = clock.now + seconds  # the reader's first clock reading is clock.now
                    with patched(time=clock, select=mutant(Select()) if mutant else Select(), os=Os()):
                        return (reader or real_read)(r, seconds)
                if via_main:
                    given, signature = [], inspect.signature(real_read)

                    def stand_in(*args, **kw):
                        call = signature.bind(*args, **kw)
                        given.append(tuple(call.arguments.get(name, value.default)
                                           for name, value in signature.parameters.items()))
                        try:
                            return run(given[-1][1])
                        except case.failureException as e:  # main takes any exception as an unreadable payload
                            failed.append(e)
                            raise
                    old = sys.stdout
                    sys.stdout = io.StringIO()
                    try:
                        with patched(_read_complete=stand_in, _is_worker=lambda env: False):
                            status = main(["hook"])
                        out = sys.stdout.getvalue()
                    finally:
                        sys.stdout = old
                    if failed:
                        raise failed[0]
                    self.assertEqual((status, given), (0, [(0, _READ_DEADLINE)]),
                                     "main reads stdin once, at the production deadline")
                else:
                    try:
                        out = run(deadline)
                    except ValueError as e:
                        out = "refused: " + str(e)
                self.assertEqual(clock.now, moved[0], "the clock moved on outside the waits and reads: slept %r" % (
                    slept,))
                return out, timeouts, len(reads)
            finally:
                for fd in ends:
                    real_os.close(fd)

        def wait_for_time_left(self, mutant=None, reader=None):
            """The deterministic check, and the only verdict on the timeouts, the time each wait is given (QA rounds
            6 to 11): the reader on fixed injected schedules (scheduled), where the timeout each wait is given is
            known. mutant, when given, wraps the injected select (the reader as a mutant changes it); reader, when
            given, replaces _read_complete. Fails the test
            when, on one of the schedules below, a wait is given a timeout other than the time left by the clock,
            an end of input read at or after the deadline is accepted, or the reader waits, reads or reads the
            clock more often than the schedule allows. Every timeout is compared exactly, with no tolerance: on
            the two schedules with steps of 0.1, 0.3, 0.2, 0.05 and 0.01 seconds the clock's float sums are not
            the decimal values, so the values expected there are those sums (0.9000000000000057 seconds where
            the decimal value is 0.9, and so on). A change to the timeouts is therefore caught exactly when it
            changes one of the timeouts below. The schedules: deadlines of 1.0 seconds and of the production
            _READ_DEADLINE, 2.0 seconds; steps from 2 ** -10 to 0.5 seconds, some of them off the 0.125-second
            grid; up to six waits; reads with up to 2 - 2 ** -10 seconds left; timeouts from 2.0 seconds down to
            0.0799999999999983 seconds; and an end of input read at the deadline and 2 ** -10 seconds either side
            of it. Not caught here (test_m46_timeout_mutants passes the first two): a floor at or below
            0.0799999999999983 seconds, since no timeout here is lower; a wait for the whole deadline given only
            while more than 2 - 2 ** -10 seconds are left, since no wait here after the first has more left (at
            the 2-second deadline such a wait is given less than 2 ** -10 seconds more than the time left); and
            any timeout that is wrong only on other schedules (QA round 8)."""
            one = json.dumps(dict(a=1)).encode()

            def waits(steps, read_takes=0.0, deadline=1.0):
                return self.scheduled(steps, read_takes, deadline, reader, mutant)[:2]
            # the end of input does not come: the waits are for 1.0, 0.75 and 0.25 seconds, and the clock reaches
            # the deadline at the third (a wait for the whole deadline would be given 1.0 each time, and a time
            # left not taken again after a wait that found nothing would give the third 0.75)
            self.assertEqual(waits([(0.25, one[:3]), (0.5, None), (0.25, None)]),
                             ("refused: " + _LATE, [1.0, 0.75, 0.25]))
            # the end of input comes in time: each wait is for what is left after the one before it (a deadline
            # started again at each read would give the second 1.0)
            self.assertEqual(waits([(0.25, one[:3]), (0.25, one[3:]), (0.125, b"")]),
                             (dict(a=1), [1.0, 0.75, 0.5]))
            # each read takes 0.125 seconds: the time left is taken after the read, not from the reading before it
            # (that would give 0.75 and 0.375) (QA round 7)
            self.assertEqual(waits([(0.25, one[:3]), (0.25, one[3:]), (0.0625, b"")], read_takes=0.125),
                             (dict(a=1), [1.0, 0.625, 0.25]))
            # steps off the 0.125-second grid (0.1, 0.3 and 0.2 seconds) and five waits (QA round 8), compared
            # exactly with the clock's own float sums (QA round 10): the last time left is 0.08 seconds in decimal
            # and 0.0799999999999983 by the clock, so a floor above that (0.08 itself included) is caught here
            self.assertEqual(waits([(0.1, one[:3]), (0.3, None), (0.2, one[3:]), (0.3, None), (0.25, None)]),
                             ("refused: " + _LATE, [1.0, 0.9000000000000057, 0.6000000000000085,
                                                    0.4000000000000057, 0.10000000000000853]))
            self.assertEqual(waits([(0.1, one[:3]), (0.3, None), (0.3, one[3:]), (0.2, None), (0.05, b"")],
                                   read_takes=0.01),
                             (dict(a=1), [1.0, 0.8900000000000006, 0.5900000000000034, 0.28000000000000114,
                                          0.0799999999999983]))
            # the read of the end of input takes the clock exactly to the deadline, 2 ** -10 seconds past it, and
            # 2 ** -10 seconds short of it (each value exact in binary): the end is refused at and past the
            # deadline, by the check after the read (without that check, or with > for >=, the end read at the
            # deadline is accepted), and taken short of it (QA round 8)
            for last, out in ((0.125, "refused: " + _LATE), (0.125 + 2 ** -10, "refused: " + _LATE),
                              (0.125 - 2 ** -10, dict(a=1))):
                self.assertEqual(waits([(0.25, one[:3]), (0.25, one[3:]), (last, b"")], read_takes=0.125),
                                 (out, [1.0, 0.625, 0.25]), last)
            # the production deadline of 2.0 seconds, so waits with more than 1.0 second left (QA round 10). Reads
            # with 2 - 2 ** -10 and 1.25 seconds left, and the input never ends: a wait for the whole deadline
            # whenever more than 1.0 second is left would give the second, third and fourth 2.0
            self.assertEqual(waits([(2 ** -10, one[:3]), (0.5 - 2 ** -10, None), (0.25, one[3:]), (0.5, None),
                                    (0.25, None), (0.5, None)], deadline=_READ_DEADLINE),
                             ("refused: " + _LATE, [2.0, 2 - 2 ** -10, 1.5, 1.25, 0.75, 0.5]))
            # and the end comes in time, each read taking 0.125 seconds (a time left taken from the reading before
            # the read would give the second 1.75)
            self.assertEqual(waits([(0.25, one[:3]), (0.5, one[3:]), (0.25, None), (0.5, b"")], read_takes=0.125,
                                   deadline=_READ_DEADLINE), (dict(a=1), [2.0, 1.625, 1.0, 0.75]))

        def test_m46_wait_for_time_left(self):
            """The deterministic check of the remaining-time logic, and the only verdict on the timeouts
            (wait_for_time_left, QA rounds 6 to 11): on its schedules each wait is for the time left by the clock,
            compared exactly, and an end of input read at or after the deadline is refused."""
            self.wait_for_time_left()

        def test_m46_timeout_mutants(self):
            """Readers whose waits are given a wrong timeout fail the deterministic check: the whole deadline at
            every wait, from the fourth wait on, or whenever more than 1.0 or 1.99 seconds are left; rounded up to
            eighths of a second or to the nearest 0.01 seconds; held at a floor of 0.24, 0.08 or 0.0800000001
            seconds. The limits wait_for_time_left states pass it: a floor of 0.0799999999999983 seconds, and the
            whole deadline whenever more than 2 - 2 ** -10 seconds are left (QA round 10)."""
            class Changed(object):
                """A mutant's select: wait number n (from 1) is given change(n, timeout, the first wait's timeout,
                which is the whole deadline)."""
                def __init__(self, inner, change):
                    self.inner, self.change, self.n, self.first = inner, change, 0, None

                def select(self, rlist, wlist, xlist, timeout):
                    self.n += 1
                    if self.n == 1:
                        self.first = timeout
                    return self.inner.select(rlist, wlist, xlist, self.change(self.n, timeout, self.first))
            for name, change in (("the whole deadline at every wait", lambda n, t, first: first),
                                 ("the whole deadline from the fourth wait on",
                                  lambda n, t, first: first if n >= 4 else t),
                                 ("the whole deadline whenever more than 1.0 seconds are left",
                                  lambda n, t, first: first if t > 1.0 else t),
                                 ("the whole deadline whenever more than 1.99 seconds are left",
                                  lambda n, t, first: first if t > 1.99 else t),
                                 ("rounded up to eighths of a second", lambda n, t, first: -(-t * 8 // 1) / 8),
                                 ("rounded to the nearest 0.01 seconds", lambda n, t, first: round(t, 2)),
                                 ("at least 0.24 seconds", lambda n, t, first: max(t, 0.24)),
                                 ("at least 0.08 seconds", lambda n, t, first: max(t, 0.08)),
                                 ("at least 0.0800000001 seconds", lambda n, t, first: max(t, 0.0800000001))):
                with self.assertRaises(self.failureException, msg=name):
                    self.wait_for_time_left(lambda inner: Changed(inner, change))
            for change in (lambda n, t, first: max(t, 0.0799999999999983),
                           lambda n, t, first: first if t > 2 - 2 ** -10 else t):
                self.wait_for_time_left(lambda inner: Changed(inner, change))

        def test_m46_delay_mutants(self):
            """Readers that delay through the names the injected harness patches fail the deterministic check by
            assertion, not by an error (QA round 11): one that sleeps 1 second before each refusal for lateness,
            one that sleeps 2 ** -10 seconds before it first reads the clock, and one that waits through
            select.poll. A delay through a name the harness does not patch is not seen there (scheduled); the
            parent-observed bound on the production-deadline runs in test_m46_input_still_open catches one of
            just under 10 seconds or more."""
            def late_sleep(fd, deadline):
                try:
                    return _read_complete(fd, deadline)
                except ValueError as e:
                    if str(e) == _LATE:
                        time.sleep(1)  # the module's time: the injected one while the reader runs
                    raise

            def early_sleep(fd, deadline):
                time.sleep(2 ** -10)
                return _read_complete(fd, deadline)

            def polled(fd, deadline):
                select.poll()
                return _read_complete(fd, deadline)
            for reader in (late_sleep, early_sleep, polled):
                with self.assertRaises(self.failureException, msg=reader.__name__):
                    self.wait_for_time_left(reader=reader)

        def test_m46_input_still_open(self):
            """End to end, in child processes (child): the reader on a real pipe, clock and select, and the hook's
            own main. Input that ends is taken (the reader given a 20-second deadline). Input that never ends (its
            write end kept open until the child exits) is refused with the deadline's message by the reader at its
            production deadline, although a whole JSON value has been written; through main it gets the
            cannot-evaluate note, not the deny that payload gets once read. A reader with no deadline never
            returns on that input, and one that takes a whole JSON value without its end of input (QA round 3
            mutant B) returns it: each fails here. Each child has a parent-observed bound (child) of 30 seconds,
            and the two at the production deadline one of 12 seconds, with their input open throughout: the
            refusal must come by then, from the child's exit (QA round 11). That bound starts just after the
            child is started, and the reader's deadline once the child has started up (about 0.05 seconds here;
            the 0.3-second pause runs within the deadline), so the margin after the deadline is just under 10
            seconds, less any stall of this test process. So a reader that delays its refusal by just under 10
            seconds or more fails here, and a correct one fails only if the host stalls the child, or this test,
            for about that long; no other assertion here depends on when anything happened, and
            test_m46_wait_for_time_left is the only verdict on the timeouts."""
            one, prompt = json.dumps(dict(a=1)).encode(), int(_READ_DEADLINE) + 10
            self.assertEqual(self.child_read([(0, one)]), dict(a=1))
            self.assertEqual(self.child_read([(0, one[:5]), (0.3, one[5:])]), dict(a=1))
            self.assertEqual(self.child_read([(0, one[:3]), (0.3, one[3:])], deadline=None, end=False,
                                             bound=prompt), "refused: " + _LATE)
            deny = payload_bytes("pkill -f qa-x/")
            self.assertEqual(self.child([here], [(0, deny[:20]), (0.3, deny[20:])], end=False, bound=prompt),
                             (0, json.dumps(dict(systemMessage=_NOTE_PAYLOAD)).encode() + b"\n", b""))

        def test_m47_one_input_budget(self):
            """Every byte read through the end of input counts toward the one 16 MiB budget, whether the blanks
            after the payload are written with it or 0.15 seconds after it (QA round 3). The reader runs in a
            child process (child_read) and the hook as one (child, with its non-blocking writes, QA round 11);
            neither verdict depends on when the reader saw the bytes, and nothing is asserted on the time taken
            beyond each child's 30-second bound."""
            whole, blanks = payload_bytes("echo hi"), b" " * 65536
            over = (_MAX_INPUT - len(whole)) // len(blanks) + 1
            bound = "refused: hook payload over the read bound"
            self.assertEqual(self.child_read([(0, whole + blanks * over)]), bound)  # continuous
            self.assertEqual(self.child_read([(0, whole)] + [(0.15, blanks)] + [(0, blanks)] * over),
                             bound)  # fragmented
            pad = 4096 - len(whole)  # the boundary, with a _MAX_INPUT of 4096: 4096 bytes in all is read, 4097 not
            for pause in (0, 0.15):
                self.assertEqual(self.child_read([(0, whole), (pause, b" " * pad)], max_input=4096),
                                 json.loads(whole))
                self.assertEqual(self.child_read([(0, whole), (pause, b" " * (pad + 1))], max_input=4096), bound)
            note = json.dumps(dict(systemMessage=_NOTE_PAYLOAD)).encode() + b"\n"
            self.assertEqual(self.child([here], [(0, whole), (0.15, blanks)] + [(0, blanks)] * (over - 1)),
                             (0, note, b""))  # the hook stops reading at the bound: its note

        def test_m48_descriptor_number(self):
            # bash reads a bare word of digits alone directly before `>` as a descriptor number: `pkill -f
            # 15>/dev/null` gets no pattern and signals nothing, and `while pgrep -f 7>/dev/null` ends at once
            self.kill_mutant(dict(_DIGITS_RE=re.compile(r"(?!)")),
                             ["pkill -f 15>/dev/null", "pkill -f 8080>/dev/null", "pkill -f -- 15>/dev/null",
                              "pkill -f 15>/dev/null 2>&1", "while pgrep -f 7>/dev/null; do sleep 1; done",
                              "until ! pgrep -f 7>/dev/null; do sleep 1; done"], "note", "deny")
            for command in ("pkill -f 15 >/dev/null", 'pkill -f "15">/dev/null', "pkill -f qa-x/15>/dev/null",
                            "pkill -f 15&>/dev/null"):
                self.assertEqual(outcome(command), "deny", command)
            # digits must be the whole word: bash reads 2fa and 15.0 as words (QA round 4)
            self.kill_mutant(dict(_DIGITS_RE=re.compile(r"[0-9]+")),
                             ["pkill -f 2fa>/dev/null", "pkill -f 15.0>/dev/null"], "deny", "note")
            # bash 5.3.9 reads digits up to 2147483647 as a descriptor (that one is a bad descriptor, and pgrep
            # never ran) and a larger run as a word (pgrep -f 2147483648>/dev/null ran and matched)
            for command in ("pkill -f 2147483647>/dev/null", "pkill -f 02147483647>/dev/null",
                            "pkill -f 0000000000000000000015>/dev/null"):
                self.assertEqual(outcome(command), "note", command)
            longer = ["pkill -f 2147483648>/dev/null", "pkill -f 00000000002147483648>/dev/null",
                      "pkill -f 99999999999999999999>/dev/null", "pkill -f " + "9" * 5000 + ">/dev/null"]
            self.kill_mutant(dict(_FD_MAX=10 ** 30), longer[:2], "deny", "note")
            self.kill_mutant(dict(_descriptor=lambda word: bool(_DIGITS_RE.match(word))), longer, "deny", "note")

        def test_m49_body_trailer_names(self):
            # the CMD after the kill in a (b) body is one of that body's commands, not any benign command
            real = _trailer
            self.kill_mutant(dict(_trailer=lambda toks, i, names, var=None:
                                  real(toks, i, _BENIGN if names is _FOR_BODY else names, var)),
                             ["for p in $(pgrep -f qa-x/); do kill $p || date; done",
                              "for p in $(pgrep -f qa-x/); do kill $p || pwd; done"], "note", "deny")

        def test_m50_trailer_redirections(self):
            # a shape's trailing CMD may carry R, as any benign command may
            def unredirected(toks, i, names, var=None):
                if i < len(toks) and toks[i] in _ANDOR:
                    e = _upto_sep(toks, i + 1)
                    if _simple(toks[i + 1:e], names, redirs=False, var=var):
                        return e
                return i
            self.kill_mutant(dict(_trailer=unredirected),
                             ["pkill -f qa-x/ || echo none >/dev/null", "pkill -f qa-x/ && date 2>/dev/null",
                              "while pgrep -f qa-x/; do sleep 1; done || echo gone >/dev/null 2>&1"], "deny", "note")
            self.assertEqual(outcome("for p in $(pgrep -f qa-x/); do kill $p || echo x >/dev/null; done"), "note")

        def test_m51_late_end_of_input(self):
            """An end of input that comes after the deadline is refused even when the OS runs the hook late (QA
            round 4): the clock is read again after each wait and after each read, before an end of input is
            accepted. On injected schedules (scheduled), with no thread and nothing timed (QA round 11), at a
            deadline of 0.05 seconds: no reading held, then each of the six clock readings in turn held off 0.12
            seconds between taking it and using it, on a schedule whose second wait ends the input and takes 0.08
            seconds; a wait reported at once whose read then takes the clock past the deadline; and a wait that
            takes 1.0 second, so it ends about 0.95 seconds past the deadline, which is not followed by a read
            (asserted on the count of reads, QA round 10). Through main, at the production
            deadline, waits that each take 1.1 seconds: the end of input is seen at 2.2 seconds and gets the
            cannot-evaluate note."""
            late, one = "refused: " + _LATE, b"{}"
            # the reported regression. Unheld, the second wait alone takes the clock to 100.08, past the deadline
            # at 100.05: refused after one read. Holding reading 3, 4, 5 or 6 gives that same result (with 1 to 4
            # held the reader refuses before the second wait, so the input never ends in the run; with 5 held the
            # end comes 0.20 seconds after the first reading). Holding reading 1 or 2 gives a refusal with no read:
            # reading 2 is the time left before the first wait, so without the check after a wait, that wait,
            # held off, is followed by a read
            for held, reads in ((None, 1), (1, 0), (2, 0), (3, 1), (4, 1), (5, 1), (6, 1)):
                out, _, n = self.scheduled([(0.0, one), (0.08, b"")], deadline=0.05,
                                           held=held and (held, 0.12))
                self.assertEqual((out, n), (late, reads), held)
            # a wait reported at once: the read of the end of input itself takes the clock past the deadline
            out, _, n = self.scheduled([(0.0, one), (0.0, b"")], read_takes=[0.0, 0.08], deadline=0.05)
            self.assertEqual((out, n), (late, 2))
            # a wait that takes 1.0 second ends about 0.95 seconds past the deadline: it is not followed by a read
            out, _, n = self.scheduled([(1.0, b"")], deadline=0.05)
            self.assertEqual((out, n), (late, 0))
            # through main: each wait moves the clock on 1.1 seconds, so the end of input is seen at 2.2 seconds
            out = self.scheduled([(1.1, payload_bytes("echo hi")), (1.1, b"")], via_main=True)[0]
            self.assertEqual(json.loads(out), dict(systemMessage=_NOTE_PAYLOAD))

        def test_m52_schedule_pipe_closed(self):
            """scheduled closes both pipe ends on every path from the moment os.pipe returns them: an injected
            failure of os.set_blocking, its first call after os.pipe, leaves neither end open (QA round 11)."""
            real_os, made = os, []

            class Failing(object):
                def __getattr__(self, name):
                    return getattr(real_os, name)

                def pipe(self):
                    made.extend(real_os.pipe())
                    return made[0], made[1]

                def set_blocking(self, fd, flag):
                    raise OSError("injected set_blocking failure")
            with patched(os=Failing()):
                with self.assertRaises(OSError):
                    self.scheduled([(0.0, b"{}"), (0.0, b"")])
            self.assertEqual(len(made), 2)
            for fd in made:
                with self.assertRaises(OSError, msg=fd):  # EBADF: that end was closed
                    os.fstat(fd)

        def test_15_adjacent_redirection_probe(self):
            # non-signalling: pgrep -c in the wrapper form (a command after the eval), in its own process group,
            # under a timeout, the group killed if it still runs; the pattern selects the shell running it
            exe = trusted_bash()
            if exe is None or not os.path.isfile("/usr/bin/pgrep") or not os.path.isdir("/proc/self"):
                self.skipTest("SKIPPED, no trusted bash, /usr/bin/pgrep or /proc")
            # the digit runs: bash reads 2147483648 as a word (pgrep runs and selects the shell) and 2147483647 as
            # a descriptor (a bad one: pgrep never runs, status 1)
            for pattern, decision, status in (("qa-r3-adj-%d/" % os.getpid(), "deny", b"rc=0\n"),
                                              ("2147483648", "deny", b"rc=0\n"), ("2147483647", "note", b"rc=1\n")):
                self.assertEqual(outcome("pkill -f " + pattern + ">/dev/null"), decision, pattern)
                wrapper = "eval 'pgrep -c -f " + pattern + ">/dev/null' < /dev/null; echo rc=$?; pwd -P >/dev/null"
                p = subprocess.Popen([exe, "--norc", "--noprofile", "-c", wrapper], stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, env=dict(LC_ALL="C", PATH="/usr/bin:/bin"), cwd="/",
                                     start_new_session=True)
                try:
                    out, err = p.communicate(timeout=10)
                finally:
                    if p.poll() is None:
                        os.killpg(p.pid, signal.SIGKILL)
                        p.communicate()
                self.assertEqual(out, status, (pattern, err))

    suite = unittest.defaultTestLoader.loadTestsFromTestCase(T)
    result = unittest.TextTestRunner(stream=sys.stderr, verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
